from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from astropy.io import fits
from xisf import XISF

from photometry_app.core.scanner import scan_fits_tree


class ScannerTest(unittest.TestCase):
    def test_scan_fits_tree_classifies_solved_and_unsolved_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            object_dir = root / "Files" / "M42"
            object_dir.mkdir(parents=True)

            self._write_fits(object_dir / "solved.fits", solved=True)
            self._write_fits(object_dir / "unsolved.fits", solved=False)

            report = scan_fits_tree(root)

            self.assertEqual(len(report.object_summaries), 1)
            summary = report.object_summaries[0]
            self.assertEqual(summary.object_name, "M42")
            self.assertEqual(summary.solved_count, 1)
            self.assertEqual(summary.unsolved_count, 1)
            self.assertEqual(len(summary.files), 2)

    def test_scan_fits_tree_discovers_fit_extension(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            object_dir = root / "Files" / "R_Cas"
            object_dir.mkdir(parents=True)

            self._write_fits(object_dir / "frame_001.fit", solved=True)

            report = scan_fits_tree(root)

            self.assertEqual(len(report.object_summaries), 1)
            summary = report.object_summaries[0]
            self.assertEqual(summary.object_name, "R_Cas")
            self.assertEqual(len(summary.files), 1)
            self.assertEqual(summary.files[0].path.suffix.lower(), ".fit")
            self.assertEqual(summary.solved_count, 1)

    def test_scan_fits_tree_accepts_files_folder_as_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            files_root = Path(temp_dir) / "Files"
            object_dir = files_root / "M42"
            object_dir.mkdir(parents=True)

            self._write_fits(object_dir / "frame_001.fit", solved=True)

            report = scan_fits_tree(files_root)

            self.assertEqual(len(report.object_summaries), 1)
            summary = report.object_summaries[0]
            self.assertEqual(summary.object_name, "M42")
            self.assertEqual(len(summary.files), 1)

    def test_scan_fits_tree_accepts_object_folder_as_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            object_dir = Path(temp_dir) / "M42"
            object_dir.mkdir(parents=True)

            self._write_fits(object_dir / "frame_001.fit", solved=True)

            report = scan_fits_tree(object_dir)

            self.assertEqual(len(report.object_summaries), 1)
            summary = report.object_summaries[0]
            self.assertEqual(summary.object_name, "M42")
            self.assertEqual(len(summary.files), 1)

    def test_scan_fits_tree_keeps_filename_metadata_except_timestamp_when_headers_are_sparse(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            object_dir = root / "Files" / "M42"
            object_dir.mkdir(parents=True)

            file_path = object_dir / "Light_M42_30.0s_Bin1_B_20250205-201036_0001.fit"
            self._write_fits(file_path, solved=False, include_metadata=False)

            report = scan_fits_tree(root)

            summary = report.object_summaries[0]
            result = summary.files[0]
            self.assertEqual(result.metadata.object_name, "M42")
            self.assertEqual(result.metadata.frame_type, "LIGHT")
            self.assertEqual(result.metadata.binning, "Bin1")
            self.assertEqual(result.metadata.filter_name, "B")
            self.assertEqual(result.metadata.exposure_seconds, 30.0)
            self.assertIsNone(result.metadata.date_obs)

    def test_scan_fits_tree_does_not_use_filename_timestamp_even_with_configured_timezone(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            object_dir = root / "Files" / "M42"
            object_dir.mkdir(parents=True)

            file_path = object_dir / "Light_M42_30.0s_Bin1_B_20250205-201036_0001.fit"
            self._write_fits(file_path, solved=False, include_metadata=False)

            report = scan_fits_tree(root, observation_timezone="UTC-05:00")

            summary = report.object_summaries[0]
            result = summary.files[0]
            self.assertIsNone(result.metadata.date_obs)

    def test_scan_fits_tree_ignores_filename_timestamp_with_zoneinfo_standard_time(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            object_dir = root / "Files" / "M42"
            object_dir.mkdir(parents=True)

            file_path = object_dir / "Light_M42_30.0s_Bin1_B_20251108-065357_0001.fit"
            self._write_fits(file_path, solved=False, include_metadata=False)

            report = scan_fits_tree(root, observation_timezone="America/New_York")

            summary = report.object_summaries[0]
            result = summary.files[0]
            self.assertIsNone(result.metadata.date_obs)

    def test_scan_fits_tree_parses_prefixed_and_suffixed_pixinsight_style_filename_metadata_without_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            object_dir = root / "Files" / "M42"
            object_dir.mkdir(parents=True)

            file_path = object_dir / "10. Light_3I_120.0s_Bin1_L_20251207-042330_0010_c.xisf"
            self._write_xisf(file_path, solved=False, include_metadata=False)

            report = scan_fits_tree(root, observation_timezone="America/New_York")

            summary = report.object_summaries[0]
            result = summary.files[0]
            self.assertEqual(result.metadata.object_name, "3I")
            self.assertEqual(result.metadata.filter_name, "L")
            self.assertEqual(result.metadata.exposure_seconds, 120.0)
            self.assertIsNone(result.metadata.date_obs)

    def test_scan_fits_tree_ignores_filename_timestamp_with_zoneinfo_daylight_time(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            object_dir = root / "Files" / "M42"
            object_dir.mkdir(parents=True)

            file_path = object_dir / "Light_M42_30.0s_Bin1_B_20250708-065357_0001.fit"
            self._write_fits(file_path, solved=False, include_metadata=False)

            report = scan_fits_tree(root, observation_timezone="America/New_York")

            summary = report.object_summaries[0]
            result = summary.files[0]
            self.assertIsNone(result.metadata.date_obs)

    def test_scan_fits_tree_treats_naive_header_timestamp_as_utc(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            object_dir = root / "Files" / "M42"
            object_dir.mkdir(parents=True)

            self._write_fits(object_dir / "frame_001.fit", solved=True)

            report = scan_fits_tree(root, observation_timezone="UTC-04:00")

            summary = report.object_summaries[0]
            self.assertEqual(summary.files[0].metadata.date_obs, datetime(2026, 3, 16, 1, 2, 3, tzinfo=UTC))

    def test_scan_fits_tree_honors_explicit_header_timezone_offset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            object_dir = root / "Files" / "M42"
            object_dir.mkdir(parents=True)

            self._write_fits(object_dir / "frame_001.fit", solved=True, date_obs_text="2026-03-16T01:02:03-04:00")

            report = scan_fits_tree(root, observation_timezone="UTC")

            summary = report.object_summaries[0]
            self.assertEqual(summary.files[0].metadata.date_obs, datetime(2026, 3, 16, 5, 2, 3, tzinfo=UTC))

    def test_scan_fits_tree_prefers_header_timestamp_over_conflicting_filename_time(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            object_dir = root / "Files" / "M42"
            object_dir.mkdir(parents=True)

            file_path = object_dir / "10. Light_3I_120.0s_Bin1_L_20251207-042330_0010_c.fit"
            self._write_fits(file_path, solved=False, include_metadata=True, date_obs_text="2025-12-07T10:21:30")

            report = scan_fits_tree(root, observation_timezone="America/New_York")

            summary = report.object_summaries[0]
            result = summary.files[0]
            self.assertEqual(result.metadata.date_obs, datetime(2025, 12, 7, 10, 21, 30, tzinfo=UTC))
            self.assertNotIn("Using filename timestamp", " ".join(result.reasons))

    def test_scan_fits_tree_keeps_filename_metadata_but_not_timestamp_when_fits_read_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            object_dir = root / "Files" / "M42"
            object_dir.mkdir(parents=True)

            file_path = object_dir / "Light_M42_30.0s_Bin1_B_20250205-201036_0001.fit"
            file_path.write_bytes(b"not-a-fits-file")

            report = scan_fits_tree(root)

            summary = report.object_summaries[0]
            result = summary.files[0]
            self.assertEqual(result.wcs_status.value, "invalid")
            self.assertEqual(result.metadata.object_name, "M42")
            self.assertEqual(result.metadata.filter_name, "B")
            self.assertEqual(result.metadata.exposure_seconds, 30.0)
            self.assertEqual(result.metadata.frame_type, "LIGHT")
            self.assertEqual(result.metadata.binning, "Bin1")
            self.assertIsNone(result.metadata.date_obs)
            self.assertTrue(result.reasons)

    def test_scan_fits_tree_discovers_xisf_extension_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            object_dir = root / "Files" / "M42"
            object_dir.mkdir(parents=True)

            self._write_xisf(object_dir / "frame_001.xisf", solved=True)

            report = scan_fits_tree(root)

            self.assertEqual(len(report.object_summaries), 1)
            summary = report.object_summaries[0]
            self.assertEqual(summary.object_name, "M42")
            self.assertEqual(len(summary.files), 1)
            self.assertEqual(summary.files[0].path.suffix.lower(), ".xisf")
            self.assertEqual(summary.files[0].metadata.filter_name, "R")
            self.assertEqual(summary.files[0].metadata.width, 32)
            self.assertEqual(summary.files[0].metadata.height, 32)
            self.assertEqual(summary.solved_count, 1)

    def test_scan_fits_tree_uses_timezone_aware_xisf_observation_time_over_naive_date_obs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            object_dir = root / "Files" / "M42"
            object_dir.mkdir(parents=True)

            data = np.ones((32, 32, 1), dtype=np.float32)
            XISF.write(
                str(object_dir / "frame_001.xisf"),
                data,
                image_metadata={
                    "FITSKeywords": {
                        "DATE-OBS": [{"value": "2025-12-07T09:00:30.775588", "comment": "Observation timestamp"}],
                        "FILTER": [{"value": "R", "comment": "Filter name"}],
                        "EXPTIME": [{"value": "60.0", "comment": "Exposure seconds"}],
                        "OBJECT": [{"value": "M42", "comment": "Target name"}],
                    },
                    "XISFProperties": {
                        "Observation:Time:Start": {"id": "Observation:Time:Start", "type": "TimePoint", "value": "2025-12-07T09:00:30.776Z"},
                    },
                },
            )

            report = scan_fits_tree(root, observation_timezone="UTC-06:00")

            summary = report.object_summaries[0]
            self.assertEqual(summary.files[0].metadata.date_obs, datetime(2025, 12, 7, 9, 0, 30, 776000, tzinfo=UTC))

    def test_scan_fits_tree_distrusts_xisf_wcs_when_pixinsight_xdrz_sidecar_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            object_dir = root / "Files" / "M42"
            object_dir.mkdir(parents=True)

            image_path = object_dir / "frame_001.xisf"
            self._write_xisf(image_path, solved=True)
            image_path.with_suffix(".xdrz").write_text("pixinsight drizzle data", encoding="utf-8")

            report = scan_fits_tree(root)

            summary = report.object_summaries[0]
            self.assertEqual(summary.solved_count, 0)
            self.assertEqual(summary.unsolved_count, 1)
            self.assertIn("StarAlignment output", " ".join(summary.files[0].reasons))

    def test_scan_fits_tree_accepts_plate_solved_header_missing_ctype_keywords(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            object_dir = root / "Files" / "M42"
            object_dir.mkdir(parents=True)

            self._write_fits(object_dir / "missing_ctype.fits", solved=True, include_ctype=False)

            report = scan_fits_tree(root)

            summary = report.object_summaries[0]
            self.assertEqual(summary.solved_count, 1)
            self.assertEqual(summary.unsolved_count, 0)

    def _write_fits(
        self,
        path: Path,
        solved: bool,
        include_metadata: bool = True,
        include_ctype: bool = True,
        date_obs_text: str = "2026-03-16T01:02:03",
    ) -> None:
        data = np.ones((32, 32), dtype=np.float32)
        header = fits.Header()
        if include_metadata:
            header["DATE-OBS"] = date_obs_text
            header["FILTER"] = "R"
            header["EXPTIME"] = 60.0
            header["OBJECT"] = "M42"
        if solved:
            if include_ctype:
                header["CTYPE1"] = "RA---TAN"
                header["CTYPE2"] = "DEC--TAN"
            header["CRVAL1"] = 83.822
            header["CRVAL2"] = -5.391
            header["CRPIX1"] = 16.0
            header["CRPIX2"] = 16.0
            header["CD1_1"] = -0.00028
            header["CD1_2"] = 0.0
            header["CD2_1"] = 0.0
            header["CD2_2"] = 0.00028
        fits.PrimaryHDU(data=data, header=header).writeto(path)

    def _write_xisf(self, path: Path, solved: bool, include_metadata: bool = True) -> None:
        data = np.ones((32, 32, 1), dtype=np.float32)
        fits_keywords = {}
        if include_metadata:
            fits_keywords.update(
                {
                    "DATE-OBS": [{"value": "2026-03-16T01:02:03", "comment": "Observation timestamp"}],
                    "FILTER": [{"value": "R", "comment": "Filter name"}],
                    "EXPTIME": [{"value": "60.0", "comment": "Exposure seconds"}],
                    "OBJECT": [{"value": "M42", "comment": "Target name"}],
                }
            )
        if solved:
            fits_keywords.update(
                {
                    "CTYPE1": [{"value": "RA---TAN", "comment": "WCS axis"}],
                    "CTYPE2": [{"value": "DEC--TAN", "comment": "WCS axis"}],
                    "CRVAL1": [{"value": "83.822", "comment": "Reference RA"}],
                    "CRVAL2": [{"value": "-5.391", "comment": "Reference Dec"}],
                    "CRPIX1": [{"value": "16.0", "comment": "Reference pixel"}],
                    "CRPIX2": [{"value": "16.0", "comment": "Reference pixel"}],
                    "CD1_1": [{"value": "-0.00028", "comment": "WCS matrix"}],
                    "CD1_2": [{"value": "0.0", "comment": "WCS matrix"}],
                    "CD2_1": [{"value": "0.0", "comment": "WCS matrix"}],
                    "CD2_2": [{"value": "0.00028", "comment": "WCS matrix"}],
                }
            )
        XISF.write(str(path), data, image_metadata={"FITSKeywords": fits_keywords})
