from __future__ import annotations

import json
from pathlib import Path
import tempfile
import threading
import unittest

from astropy.io import fits
import numpy as np

from photometry_app.assistant.file_guard import GuardedFileWriter
from photometry_app.assistant.image_tools import image_tools
from photometry_app.assistant.messages import ToolCall
from photometry_app.assistant.sky_objects import sky_object_catalog
from photometry_app.assistant.tools import ToolContext, ToolRegistry, execute_tool_call


M13 = (250.4235, 36.4613)
EMPTY_SKY = (100.0, -70.0)


def _write_fits(path: Path, *, center: tuple[float, float] | None, obj: str = "", filt: str = "L", exposure: float = 60.0, date: str = "2025-05-01T03:00:00") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = fits.Header()
    if obj:
        header["OBJECT"] = obj
    header["FILTER"] = filt
    header["EXPTIME"] = exposure
    header["DATE-OBS"] = date
    header["IMAGETYP"] = "Light Frame"
    if center is not None:
        header["CTYPE1"] = "RA---TAN"
        header["CTYPE2"] = "DEC--TAN"
        header["CRVAL1"] = center[0]
        header["CRVAL2"] = center[1]
        header["CRPIX1"] = 200.5
        header["CRPIX2"] = 150.5
        header["CDELT1"] = -0.0005
        header["CDELT2"] = 0.0005
    fits.PrimaryHDU(np.zeros((300, 400), dtype=np.int16), header=header).writeto(path)


class ImageToolsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._temp.name).resolve() / "Lights"
        _write_fits(cls.root / "M13" / "m13_L_001.fits", center=M13, obj="M 13", filt="L")
        _write_fits(cls.root / "M13" / "m13_R_001.fits", center=M13, obj="M 13", filt="R", date="2025-05-02T03:00:00")
        _write_fits(cls.root / "Blank" / "blank_001.fits", center=EMPTY_SKY, obj="Test field")
        _write_fits(cls.root / "Unsolved" / "raw_001.fits", center=None, obj="NGC 7000")
        (cls.root / "Unsolved" / "broken.fits").write_bytes(b"not a fits file")
        (cls.root / "Unsolved" / "notes.txt").write_text("ignore me", encoding="utf-8")
        _write_fits(cls.root / ".cache" / "hidden.fits", center=M13)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temp.cleanup()

    def setUp(self) -> None:
        self.registry = ToolRegistry(image_tools())
        self.context = ToolContext(writer=GuardedFileWriter())

    def _run(self, name: str, arguments: dict[str, object]) -> tuple[str, dict[str, object]]:
        result = execute_tool_call(self.registry, self.context, ToolCall("c1", name, arguments), approver=None)
        return result.status, json.loads(result.content)

    def test_list_folder_images_counts_supported_files_and_skips_hidden_folders(self) -> None:
        status, payload = self._run("list_folder_images", {"folder": str(self.root)})
        self.assertEqual(status, "ok")
        self.assertEqual(payload["images"], 5)
        self.assertEqual(payload["by_extension"], {".fits": 5})
        self.assertIn({"folder": "M13", "images": 2}, payload["folders"])
        self.assertNotIn("hidden.fits", json.dumps(payload))
        self.assertTrue(self.context.writer.is_protected(self.root / "M13" / "new.png"))

    def test_list_can_stay_in_one_folder_and_limit_names(self) -> None:
        status, payload = self._run("list_folder_images", {"folder": str(self.root), "recursive": False})
        self.assertEqual(payload["images"], 0)
        status, payload = self._run("list_folder_images", {"folder": str(self.root), "max_files": 1})
        self.assertEqual(len(payload["files"]), 1)
        self.assertIn("first 1 of 5", payload["note"])

    def test_paths_must_be_full_and_exist(self) -> None:
        status, payload = self._run("list_folder_images", {"folder": "Lights"})
        self.assertEqual(status, "error")
        self.assertIn("not a full path", payload["message"])
        status, payload = self._run("list_folder_images", {"folder": str(self.root / "missing")})
        self.assertEqual(status, "error")
        status, payload = self._run("read_image_header", {"path": str(self.root / "Unsolved" / "notes.txt")})
        self.assertIn("not an image type", payload["message"])

    def test_read_image_header_reports_fields_wcs_and_objects_in_frame(self) -> None:
        status, payload = self._run("read_image_header", {"path": str(self.root / "M13" / "m13_L_001.fits")})
        self.assertEqual(status, "ok")
        self.assertEqual(payload["object"], "M 13")
        self.assertEqual(payload["filter"], "L")
        self.assertEqual(payload["exposure_s"], 60.0)
        self.assertEqual((payload["width"], payload["height"]), (400, 300))
        wcs = payload["wcs"]
        self.assertTrue(wcs["valid"])
        self.assertAlmostEqual(wcs["center_ra_deg"], M13[0], places=2)
        self.assertEqual(wcs["pixel_scale_arcsec"], 1.8)
        self.assertEqual(wcs["field_deg"], [0.2, 0.15])
        self.assertIn("Globular Cluster", [item["type"] for item in wcs["objects_in_field"]])
        self.assertNotIn("keywords", payload)

    def test_read_image_header_can_return_every_keyword(self) -> None:
        status, payload = self._run("read_image_header", {"path": str(self.root / "Unsolved" / "raw_001.fits"), "all_keywords": True})
        self.assertIn("OBJECT = NGC 7000", payload["keywords"])
        self.assertFalse(payload["wcs"]["valid"])
        self.assertIn("CTYPE", payload["wcs"]["reason"])

    def test_unreadable_image_is_a_readable_error(self) -> None:
        status, payload = self._run("read_image_header", {"path": str(self.root / "Unsolved" / "broken.fits")})
        self.assertEqual(status, "error")
        self.assertIn("could not read the header of broken.fits", payload["message"])

    def test_summary_finds_globular_clusters_per_folder(self) -> None:
        status, payload = self._run("summarize_image_headers", {"folder": str(self.root), "object_type": "Globular Cluster"})
        self.assertEqual(status, "ok")
        self.assertEqual(payload["images_read"], 5)
        self.assertEqual(payload["with_wcs"], 3)
        found = payload["catalog_objects_found"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["type"], "Globular Cluster")
        self.assertEqual(found[0]["images"], 2)
        self.assertEqual(found[0]["folders"], ["M13"])
        folders = {row["folder"]: row for row in payload["folders"]}
        self.assertEqual(folders["M13"]["filters"], {"L": 1, "R": 1})
        self.assertEqual(folders["M13"]["total_exposure_s"], 120.0)
        self.assertEqual(folders["M13"]["dates"], "2025-05-01 to 2025-05-02")
        self.assertNotIn("objects_in_field", folders["Blank"])
        self.assertEqual(folders["Unsolved"]["unreadable"], 1)
        self.assertEqual(payload["unreadable_files"][0]["file"], str(Path("Unsolved") / "broken.fits"))

    def test_summary_per_file_rows_and_limits(self) -> None:
        status, payload = self._run("summarize_image_headers", {"folder": str(self.root / "M13"), "detail": "files", "max_files": 1})
        self.assertEqual(len(payload["files"]), 1)
        row = payload["files"][0]
        self.assertEqual(row["size"], "400x300")
        self.assertTrue(row["objects_in_field"])
        self.assertIn("first 1 images", payload["note"])

    def test_empty_folder_says_so(self) -> None:
        with tempfile.TemporaryDirectory() as empty:
            status, payload = self._run("summarize_image_headers", {"folder": empty})
        self.assertEqual(payload["images"], 0)

    def test_stop_interrupts_a_long_read(self) -> None:
        self.context.cancel_event.set()
        result = execute_tool_call(self.registry, self.context, ToolCall("c1", "summarize_image_headers", {"folder": str(self.root)}), None)
        self.assertEqual(result.status, "cancelled")

    def test_look_up_sky_object_by_any_name(self) -> None:
        for name in ("M13", "m 13", "Messier 13", "NGC 6205"):
            with self.subTest(name=name):
                status, payload = self._run("look_up_sky_object", {"name": name})
                self.assertEqual(payload["match"]["type"], "Globular Cluster")
                self.assertAlmostEqual(payload["match"]["ra_deg"], M13[0], places=1)
        status, payload = self._run("look_up_sky_object", {"name": "Planet Nine"})
        self.assertIsNone(payload["match"])

    def test_tools_never_write_into_the_folder(self) -> None:
        before = sorted(path.relative_to(self.root) for path in self.root.rglob("*"))
        for name, arguments in (
            ("list_folder_images", {"folder": str(self.root)}),
            ("summarize_image_headers", {"folder": str(self.root)}),
            ("read_image_header", {"path": str(self.root / "M13" / "m13_L_001.fits"), "all_keywords": True}),
        ):
            self._run(name, arguments)
        self.assertEqual(sorted(path.relative_to(self.root) for path in self.root.rglob("*")), before)


class SkyObjectCatalogTest(unittest.TestCase):
    def test_packaged_catalog_has_messier_and_globulars_without_stars(self) -> None:
        catalog = sky_object_catalog()
        types = {item.object_type for item in catalog.objects}
        self.assertIn("Globular Cluster", types)
        self.assertNotIn("Star", types)
        self.assertGreaterEqual(len(catalog.objects), 110)

    def test_near_filters_by_type_and_sorts_by_distance(self) -> None:
        hits = sky_object_catalog().near(*M13, 2.0, object_type="globular")
        self.assertTrue(hits)
        self.assertLess(hits[0][1], 0.05)
        self.assertTrue(all("Globular" in item.object_type for item, _distance in hits))


if __name__ == "__main__":
    unittest.main()
