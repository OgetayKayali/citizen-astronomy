from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from astropy.io import fits
import numpy as np

from photometry_app.core.observation_deck import (
    ImageKind,
    build_deck_stats,
    classify_image_kind,
    counting_frames,
    header_keyword_rows,
    inspect_deck_image,
    is_observation_deck_image_path,
    load_observation_deck_library,
    merge_observation_deck_libraries,
    path_is_inside,
    relative_path_parts,
    remove_observation_deck_root,
    save_observation_deck_library,
    scan_observation_deck,
    target_name_for_path,
)


def _write_fits(
    path: Path,
    *,
    exptime: float = 60.0,
    date_obs: str | None = "2024-06-01T01:00:00",
    imagetyp: str | None = "Light",
    masttype: str | None = None,
    filter_name: str | None = None,
    object_name: str | None = None,
    extra: dict[str, object] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.zeros((8, 8), dtype=np.float32)
    header = fits.Header()
    header["EXPTIME"] = exptime
    if date_obs is not None:
        header["DATE-OBS"] = date_obs
    header["CRVAL1"] = 270.0
    header["CRVAL2"] = -24.0
    header["CTYPE1"] = "RA---TAN"
    header["CTYPE2"] = "DEC--TAN"
    header["CRPIX1"] = 4.0
    header["CRPIX2"] = 4.0
    header["CD1_1"] = -0.001
    header["CD1_2"] = 0.0
    header["CD2_1"] = 0.0
    header["CD2_2"] = 0.001
    if imagetyp is not None:
        header["IMAGETYP"] = imagetyp
    if masttype is not None:
        header["MASTTYPE"] = masttype
    if filter_name is not None:
        header["FILTER"] = filter_name
    if object_name is not None:
        header["OBJECT"] = object_name
    if extra:
        for key, value in extra.items():
            header[key] = value
    fits.PrimaryHDU(data=data, header=header).writeto(path, overwrite=True)


class ObservationDeckClassifyTest(unittest.TestCase):
    def test_classifies_processing_stages_from_folders_and_headers(self) -> None:
        root = Path("C:/Astrophotography")
        self.assertEqual(
            classify_image_kind(root / "Messier 8" / "Lights" / "frame.fits", None, root=root),
            ImageKind.LIGHT_SUBFRAME,
        )
        self.assertEqual(
            classify_image_kind(root / "Messier 8" / "calibrated" / "frame.fits", None, root=root),
            ImageKind.CALIBRATED_LIGHT,
        )
        self.assertEqual(
            classify_image_kind(root / "Messier 8" / "registered" / "frame.fits", None, root=root),
            ImageKind.REGISTERED_LIGHT,
        )
        self.assertEqual(
            classify_image_kind(root / "Messier 8" / "normalized" / "frame.fits", None, root=root),
            ImageKind.NORMALIZED_LIGHT,
        )
        self.assertEqual(
            classify_image_kind(root / "Messier 8" / "Integration" / "M8_Ha.fits", None, root=root),
            ImageKind.INTEGRATION,
        )
        self.assertEqual(
            classify_image_kind(root / "Messier 8" / "darks" / "dark.fits", None, root=root),
            ImageKind.CALIBRATION_FRAME,
        )
        self.assertEqual(
            classify_image_kind(root / "Messier 8" / "masters" / "master_dark.fits", None, root=root),
            ImageKind.MASTER_CALIBRATION,
        )
        self.assertEqual(
            classify_image_kind(root / "Messier 8" / "processed" / "final.tif", None, root=root),
            ImageKind.OTHER,
        )

    def test_master_light_in_masters_folder_is_integration(self) -> None:
        root = Path("C:/Astrophotography")
        header = fits.Header()
        header["IMAGETYP"] = "Light"
        header["MASTTYPE"] = "LIGHT"
        self.assertEqual(
            classify_image_kind(root / "M31" / "masters" / "masterLight.fits", header, root=root),
            ImageKind.INTEGRATION,
        )


class ObservationDeckScanTest(unittest.TestCase):
    def test_scan_groups_targets_and_avoids_double_counting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            m8 = root / "Messier 8"
            ngc = root / "NGC 555"
            _write_fits(m8 / "Lights" / "a.fits", exptime=120.0, date_obs="2024-06-01T01:00:00", filter_name="Ha")
            _write_fits(m8 / "Lights" / "b.fits", exptime=180.0, date_obs="2024-06-01T02:00:00", filter_name="Ha")
            _write_fits(m8 / "calibrated" / "a.fits", exptime=120.0, date_obs="2024-06-01T01:00:00", filter_name="Ha")
            _write_fits(m8 / "Integration" / "M8_Ha.fits", exptime=300.0, date_obs="2024-06-01T03:00:00", filter_name="Ha")
            _write_fits(m8 / "darks" / "dark.fits", imagetyp="Dark", exptime=60.0, filter_name=None)
            _write_fits(m8 / "masters" / "master_dark.fits", imagetyp="Dark", masttype="DARK", exptime=60.0)
            _write_fits(
                ngc / "LIGHT" / "c.fits",
                exptime=300.0,
                date_obs="2023-08-15T04:00:00",
                filter_name="L",
            )
            notes = root / "Messier 8" / "notes.txt"
            notes.parent.mkdir(parents=True, exist_ok=True)
            notes.write_text("not an image", encoding="utf-8")

            library = scan_observation_deck(root)
            self.assertGreaterEqual(library.scanned_files, 7)
            self.assertFalse(any(image.path.suffix == ".txt" for image in library.images))
            kinds = {(image.target_name, image.kind) for image in library.images}
            self.assertIn(("Messier 8", ImageKind.LIGHT_SUBFRAME), kinds)
            self.assertIn(("Messier 8", ImageKind.CALIBRATED_LIGHT), kinds)
            self.assertIn(("Messier 8", ImageKind.INTEGRATION), kinds)
            self.assertIn(("Messier 8", ImageKind.CALIBRATION_FRAME), kinds)
            self.assertIn(("Messier 8", ImageKind.MASTER_CALIBRATION), kinds)
            self.assertEqual(library.target_names(), ("Messier 8", "NGC 555"))

            counted = counting_frames(library.images)
            self.assertEqual(len(counted), 3)
            self.assertTrue(all(image.kind == ImageKind.LIGHT_SUBFRAME for image in counted))

            stats = build_deck_stats(library.images, scope_label="All targets")
            self.assertEqual(stats.target_count, 2)
            self.assertEqual(stats.subframe_count, 3)
            self.assertEqual(stats.integration_count, 1)
            self.assertAlmostEqual(stats.total_exposure_seconds, 600.0)
            self.assertEqual(stats.night_count, 2)
            self.assertEqual(stats.first_date, date(2023, 8, 15))
            self.assertEqual(stats.last_date, date(2024, 6, 1))
            ha = next(item for item in stats.filters if item.filter_name == "Ha")
            self.assertAlmostEqual(ha.exposure_seconds, 300.0)
            lum = next(item for item in stats.filters if item.filter_name == "L")
            self.assertAlmostEqual(lum.exposure_seconds, 300.0)

            m8_stats = build_deck_stats(library.images_for_path(m8), scope_label="Messier 8")
            self.assertEqual(m8_stats.target_count, 1)
            self.assertAlmostEqual(m8_stats.total_exposure_seconds, 300.0)
            self.assertEqual(m8_stats.subframe_count, 2)

    def test_target_name_uses_first_level_folder(self) -> None:
        root = Path("C:/Astrophotography")
        self.assertEqual(target_name_for_path(root / "Messier 8" / "Lights" / "a.fits", root), "Messier 8")
        self.assertEqual(target_name_for_path(root / "orphan.fits", root), "Astrophotography")

    def test_accepts_science_and_raster_image_suffixes(self) -> None:
        self.assertTrue(is_observation_deck_image_path(Path("frame.fits")))
        self.assertTrue(is_observation_deck_image_path(Path("frame.xisf")))
        self.assertTrue(is_observation_deck_image_path(Path("frame.tif")))
        self.assertFalse(is_observation_deck_image_path(Path("notes.txt")))
        self.assertFalse(is_observation_deck_image_path(Path("catalog.csv")))

    def test_header_keyword_rows_include_wcs_cards(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "wcs.fits"
            _write_fits(path, extra={"FILTER": "OIII", "INSTRUME": "ASI2600"})
            image = inspect_deck_image(path, root=Path(temp_dir))
            self.assertEqual(image.filter_name, "OIII")
            with fits.open(path) as hdul:
                rows = header_keyword_rows(hdul[0].header)
            keywords = [item[0] for item in rows]
            self.assertIn("CRVAL1", keywords)
            self.assertIn("CTYPE1", keywords)
            self.assertIn("FILTER", keywords)
            self.assertIn("INSTRUME", keywords)

    def test_multiple_master_folders_are_additive_and_persist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "Astrophotography"
            second = root / "OtherImaging"
            _write_fits(first / "Messier 8" / "Lights" / "a.fits", exptime=120.0, date_obs="2023-08-15T01:00:00", filter_name="Ha")
            _write_fits(second / "NGC 555" / "Lights" / "b.fits", exptime=180.0, date_obs="2024-06-01T01:00:00", filter_name="L")
            first_library = scan_observation_deck(first)
            second_library = scan_observation_deck(second)
            merged = merge_observation_deck_libraries(first_library, second_library)
            self.assertEqual(len(merged.root_paths), 2)
            self.assertEqual(merged.target_names(), ("Messier 8", "NGC 555"))
            stats = build_deck_stats(merged.images, scope_label="All targets")
            self.assertEqual(stats.target_count, 2)
            self.assertAlmostEqual(stats.total_exposure_seconds, 300.0)

            db_path = root / "observation_deck.json"
            save_observation_deck_library(merged, db_path)
            loaded = load_observation_deck_library(db_path)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(len(loaded.root_paths), 2)
            self.assertEqual(len(loaded.images), len(merged.images))
            removed = remove_observation_deck_root(loaded, first)
            self.assertEqual(len(removed.root_paths), 1)
            self.assertEqual(removed.target_names(), ("NGC 555",))

    def test_filename_date_token_counts_when_header_lacks_date_obs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_fits(
                root / "Target" / "Lights" / "frame_20240615.fits",
                date_obs=None,
                exptime=120.0,
                filter_name="Ha",
            )
            image = inspect_deck_image(root / "Target" / "Lights" / "frame_20240615.fits", root=root)
            self.assertEqual(image.observation_date, date(2024, 6, 15))
            stats = build_deck_stats((image,), scope_label="Target")
            self.assertEqual(stats.night_count, 1)
            self.assertEqual(stats.first_date, date(2024, 6, 15))

    def test_path_helpers_do_not_need_the_filesystem(self) -> None:
        root = Path("/offline-nas/Astrophotography")
        image = root / "Messier 8" / "Lights" / "a.fits"
        self.assertTrue(path_is_inside(image, root))
        self.assertEqual(relative_path_parts(image, root), ("Messier 8", "Lights", "a.fits"))
        self.assertEqual(target_name_for_path(image, root), "Messier 8")

    def test_loading_saved_library_does_not_resolve_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "Astrophotography"
            _write_fits(
                root / "Messier 8" / "Lights" / "a.fits",
                exptime=120.0,
                date_obs="2023-08-15T01:00:00",
                filter_name="Ha",
            )
            library = scan_observation_deck(root)
            db_path = Path(temp_dir) / "observation_deck.json"
            save_observation_deck_library(library, db_path)
            offline_root = Path("/offline-nas/Astrophotography")
            offline_file = offline_root / "Messier 8" / "Lights" / "a.fits"
            payload = json.loads(db_path.read_text(encoding="utf-8"))
            payload["roots"] = [str(offline_root)]
            for item in payload["images"]:
                item["path"] = str(offline_file)
                item["root_path"] = str(offline_root)
            db_path.write_text(json.dumps(payload), encoding="utf-8")

            def forbidden_resolve(self: Path) -> Path:
                raise AssertionError(f"resolve() must not run on saved library paths: {self}")

            def forbidden_is_dir(self: Path) -> bool:
                raise AssertionError(f"is_dir() must not run on saved library paths: {self}")

            with patch.object(Path, "resolve", forbidden_resolve), patch.object(Path, "is_dir", forbidden_is_dir):
                loaded = load_observation_deck_library(db_path)
                self.assertIsNotNone(loaded)
                assert loaded is not None
                stats = build_deck_stats(loaded.images, scope_label="All targets")
                self.assertEqual(stats.target_count, 1)
                self.assertEqual(len(loaded.images_for_path(offline_root / "Messier 8")), 1)
                removed = remove_observation_deck_root(loaded, offline_root)
                self.assertEqual(removed.root_paths, ())


class ObservationDeckWorkspaceLoadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls._app = QApplication.instance() or QApplication([])

    def test_workspace_init_does_not_load_or_scan(self) -> None:
        from photometry_app.ui.astro_tools_panel import ObservationDeckWorkspace

        with (
            patch("photometry_app.ui.astro_tools_panel.load_observation_deck_library") as load_cached,
            patch("photometry_app.ui.astro_tools_panel.scan_observation_deck") as scan,
        ):
            workspace = ObservationDeckWorkspace()
            load_cached.assert_not_called()
            scan.assert_not_called()
            workspace.load_cached_library()
            from PySide6.QtWidgets import QApplication

            QApplication.processEvents()
            worker = workspace._load_worker
            if worker is not None:
                worker.wait(3000)
                QApplication.processEvents()
            load_cached.assert_called_once()
            scan.assert_not_called()
            workspace.deleteLater()

    def test_kpi_strip_is_in_the_header_band(self) -> None:
        from photometry_app.ui.astro_tools_panel import AstroToolsPanel

        panel = AstroToolsPanel()
        self.assertIs(panel._workspace._stat_strip, panel._stat_strip)
        self.assertIsNot(panel._stat_strip.parent(), panel._workspace)
        panel.deleteLater()


class FilterUsageCardLayoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls._app = QApplication.instance() or QApplication([])

    def test_filter_usage_flips_at_panel_width_and_has_no_details_link(self) -> None:
        from PySide6.QtWidgets import QBoxLayout, QLabel, QPushButton, QToolButton

        from photometry_app.core.observation_deck import FilterTimeStats
        from photometry_app.ui.astro_tools_panel import _FilterUsageCard

        card = _FilterUsageCard()
        card.set_filters(
            (
                FilterTimeStats("Ha", 1_037 * 3600.0, 10),
                FilterTimeStats("L", 716 * 3600.0, 8),
                FilterTimeStats("OIII", 486 * 3600.0, 6),
            ),
            (1_037 + 716 + 486) * 3600.0,
        )
        texts = [widget.text() for widget in card.findChildren(QLabel)]
        texts.extend(widget.text() for widget in card.findChildren(QPushButton))
        texts.extend(widget.text() for widget in card.findChildren(QToolButton))
        self.assertFalse(any("view details" in text.casefold() for text in texts))

        card.resize(300, 320)
        card._update_orientation()
        self.assertEqual(card._body_layout.direction(), QBoxLayout.Direction.TopToBottom)
        self.assertGreaterEqual(card.pie.width(), 188)
        self.assertLessEqual(card.pie.width(), 224)

        card.resize(440, 320)
        card._update_orientation()
        self.assertEqual(card._body_layout.direction(), QBoxLayout.Direction.LeftToRight)
        self.assertGreaterEqual(card.pie.width(), 216)
        self.assertLessEqual(card.pie.width(), 280)

        card.resize(360, 320)
        card._update_orientation()
        self.assertEqual(card._body_layout.direction(), QBoxLayout.Direction.LeftToRight)

        card.resize(320, 320)
        card._update_orientation()
        self.assertEqual(card._body_layout.direction(), QBoxLayout.Direction.TopToBottom)
        card.deleteLater()


if __name__ == "__main__":
    unittest.main()
