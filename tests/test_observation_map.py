from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from astropy.io import fits
import numpy as np

from photometry_app.core.observation_map import (
    build_observation_map,
    contribution_level,
    format_duration,
    frame_type_is_calibration,
    frame_type_is_light,
    inspect_observation_map_frame,
    path_looks_like_master_or_processed,
)


def _write_fits(
    path: Path,
    *,
    exptime: float = 60.0,
    date_obs: str = "2024-06-01T01:00:00",
    imagetyp: str | None = "Light",
    masttype: str | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.zeros((8, 8), dtype=np.float32)
    header = fits.Header()
    header["EXPTIME"] = exptime
    header["DATE-OBS"] = date_obs
    if imagetyp is not None:
        header["IMAGETYP"] = imagetyp
    if masttype is not None:
        header["MASTTYPE"] = masttype
    fits.PrimaryHDU(data=data, header=header).writeto(path, overwrite=True)


class ObservationMapFilterTest(unittest.TestCase):
    def test_frame_type_helpers(self) -> None:
        self.assertTrue(frame_type_is_light("Light"))
        self.assertTrue(frame_type_is_light("LIGHT FRAME"))
        self.assertFalse(frame_type_is_light("Dark"))
        self.assertTrue(frame_type_is_calibration("Bias"))
        self.assertTrue(frame_type_is_calibration("flat field"))
        self.assertIsNone(frame_type_is_light(None))

    def test_path_excludes_master_and_processed_folders(self) -> None:
        root = Path("C:/imaging")
        self.assertTrue(
            path_looks_like_master_or_processed(root / "M31" / "Masters" / "light.fits", root=root)
        )
        self.assertTrue(
            path_looks_like_master_or_processed(root / "M31" / "processed" / "tile.fits", root=root)
        )
        self.assertTrue(path_looks_like_master_or_processed(root / "M31" / "master_dark.fits", root=root))
        self.assertFalse(path_looks_like_master_or_processed(root / "M31" / "Light" / "frame_001.fits", root=root))


class ObservationMapBuildTest(unittest.TestCase):
    def test_build_observation_map_aggregates_light_subframes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "M31"
            _write_fits(target / "Light_M31_60.0s_Bin1_L_20240601-010000_0001.fits", exptime=120.0, date_obs="2024-06-01T02:00:00")
            _write_fits(target / "frame_b.fits", exptime=180.0, date_obs="2024-06-01T03:00:00")
            _write_fits(target / "frame_c.fits", exptime=300.0, date_obs="2024-06-02T01:00:00")
            _write_fits(target / "Masters" / "master_light.fits", exptime=999.0, date_obs="2024-06-01T04:00:00")
            _write_fits(target / "darks" / "dark.fits", imagetyp="Dark", exptime=60.0, date_obs="2024-06-01T05:00:00")
            _write_fits(target / "processed" / "stack.fits", imagetyp="Light", exptime=600.0, date_obs="2024-06-01T06:00:00")
            _write_fits(target / "master_flat.fits", masttype="FLAT", exptime=1.0, date_obs="2024-06-01T07:00:00")

            result = build_observation_map(root)
            self.assertEqual(result.included_frames, 3)
            self.assertEqual(result.skipped_files, 4)
            self.assertEqual(len(result.days), 2)
            day_map = result.day_map()
            self.assertEqual(day_map[date(2024, 6, 1)].exposure_seconds, 300.0)
            self.assertEqual(day_map[date(2024, 6, 1)].frame_count, 2)
            self.assertEqual(day_map[date(2024, 6, 2)].exposure_seconds, 300.0)
            self.assertAlmostEqual(result.total_exposure_hours, 600.0 / 3600.0)

    def test_inspect_rejects_calibration_imagetyp(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "flat.fits"
            _write_fits(path, imagetyp="Flat", exptime=1.0)
            self.assertIsNone(inspect_observation_map_frame(path, root=Path(temp_dir)))

    def test_format_duration_and_levels(self) -> None:
        self.assertEqual(format_duration(3661), "1h 01m")
        self.assertEqual(contribution_level(0.0, max_seconds=100.0), 0)
        self.assertEqual(contribution_level(10.0, max_seconds=100.0), 1)
        self.assertEqual(contribution_level(90.0, max_seconds=100.0), 4)


if __name__ == "__main__":
    unittest.main()
