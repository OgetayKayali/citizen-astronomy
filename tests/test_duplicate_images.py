from __future__ import annotations

import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from astropy.io import fits
import numpy as np

from photometry_app.assistant.tools import ToolCancelled
from photometry_app.core.duplicate_images import EXACT_COPY, SAME_EXPOSURE, find_duplicate_images


def _write_fits(path: Path, *, date_obs: str | None, seed: int = 0, size: int = 8, filter_name: str = "L", fmt_extra: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = fits.Header()
    header["IMAGETYP"] = "Light"
    header["EXPTIME"] = 300.0
    header["FILTER"] = filter_name
    header["INSTRUME"] = "ZWO ASI2600MM Pro"
    if date_obs is not None:
        header["DATE-OBS"] = date_obs
    if fmt_extra:
        header["HISTORY"] = fmt_extra
    data = np.random.default_rng(seed).random((size, size)).astype(np.float32)
    fits.PrimaryHDU(data=data, header=header).writeto(path)
    return path


def _snapshot(folder: Path) -> dict[str, tuple[int, int, bytes]]:
    return {
        str(path): (path.stat().st_size, path.stat().st_mtime_ns, path.read_bytes())
        for path in sorted(folder.rglob("*"))
        if path.is_file()
    }


class DuplicateImagesTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_exact_copies_keep_the_shallowest_file_and_distinct_subs_stay_apart(self) -> None:
        original = _write_fits(self.root / "M13" / "a.fits", date_obs="2024-06-01T01:00:00", seed=1)
        _write_fits(self.root / "M13" / "b.fits", date_obs="2024-06-01T01:05:00", seed=2)
        backup = self.root / "Backup" / "2024" / "M13" / "a.fits"
        backup.parent.mkdir(parents=True)
        shutil.copyfile(original, backup)

        report = find_duplicate_images(sorted(self.root.rglob("*.fits")))

        self.assertEqual(report.checked_files, 3)
        self.assertEqual(len(report.groups), 1)
        group = report.groups[0]
        self.assertEqual(group.kind, EXACT_COPY)
        self.assertEqual(group.keep, original)
        self.assertEqual(group.copies, (backup,))
        self.assertEqual(report.copy_count, 1)

    def test_files_with_matching_edges_but_different_middles_are_not_exact_copies(self) -> None:
        first = self.root / "big_a.fits"
        second = self.root / "big_b.fits"
        body = bytearray(400_000)
        first.write_bytes(bytes(body))
        body[200_000] = 1
        second.write_bytes(bytes(body))

        report = find_duplicate_images([first, second])

        self.assertEqual(report.groups_of(EXACT_COPY), ())

    def test_same_exposure_saved_twice_is_a_likely_copy_but_processing_stages_are_not(self) -> None:
        raw = _write_fits(self.root / "M13" / "Lights" / "a.fits", date_obs="2024-06-01T01:00:00.123", seed=1)
        resaved = _write_fits(self.root / "M13" / "Lights" / "a_copy.fits", date_obs="2024-06-01T01:00:00.123", seed=1, fmt_extra="re-saved")
        calibrated = _write_fits(self.root / "M13" / "calibrated" / "a_c.fits", date_obs="2024-06-01T01:00:00.123", seed=3)
        stages = {raw: "light", resaved: "light", calibrated: "calibrated"}

        report = find_duplicate_images([raw, resaved, calibrated], stage_of=lambda path, _header: stages[path])

        self.assertEqual(len(report.groups), 1)
        group = report.groups[0]
        self.assertEqual(group.kind, SAME_EXPOSURE)
        self.assertEqual(set(group.paths), {raw, resaved})
        self.assertIn("2024-06-01T01:00:00.123", group.detail)
        self.assertIn("300 s", group.detail)

    def test_frames_without_a_timestamp_or_with_other_filters_are_not_grouped(self) -> None:
        _write_fits(self.root / "a.fits", date_obs="2024-06-01", seed=1)
        _write_fits(self.root / "b.fits", date_obs="2024-06-01", seed=2)
        _write_fits(self.root / "c.fits", date_obs="2024-06-01T01:00:00", seed=3, filter_name="Ha")
        _write_fits(self.root / "d.fits", date_obs="2024-06-01T01:00:00", seed=4, filter_name="OIII")
        _write_fits(self.root / "e.fits", date_obs=None, seed=5)

        report = find_duplicate_images(sorted(self.root.glob("*.fits")))

        self.assertEqual(report.groups, ())

    def test_unreadable_files_are_reported_and_nothing_is_changed(self) -> None:
        _write_fits(self.root / "a.fits", date_obs="2024-06-01T01:00:00", seed=1)
        shutil.copyfile(self.root / "a.fits", self.root / "b.fits")
        (self.root / "broken.fits").write_bytes(b"not a fits file")
        before = _snapshot(self.root)

        report = find_duplicate_images(
            [self.root / "a.fits", self.root / "b.fits", self.root / "broken.fits", self.root / "missing.fits"]
        )

        self.assertEqual(len(report.groups_of(EXACT_COPY)), 1)
        self.assertEqual({path.name for path, _reason in report.unreadable}, {"broken.fits", "missing.fits"})
        self.assertEqual(_snapshot(self.root), before)

    def test_stop_interrupts_the_search(self) -> None:
        _write_fits(self.root / "a.fits", date_obs="2024-06-01T01:00:00", seed=1)
        cancel = threading.Event()
        cancel.set()

        def check() -> None:
            if cancel.is_set():
                raise ToolCancelled("Stopped by the user.")

        with self.assertRaises(ToolCancelled):
            find_duplicate_images([self.root / "a.fits"], check_cancelled=check)


if __name__ == "__main__":
    unittest.main()
