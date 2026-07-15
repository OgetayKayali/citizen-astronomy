from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from astropy.io import fits

from photometry_app.core.alignment import SequenceAlignmentResult
from photometry_app.core.calibration import CalibrationPipelineRequest, calibrate_image_sequence
from photometry_app.core.settings import AppSettings


class CalibrationPipelineTest(unittest.TestCase):
    def test_calibrates_fits_sequence_with_bias_dark_and_flat(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            science_dir = root / "science"
            bias_dir = root / "bias"
            dark_dir = root / "dark"
            flat_dir = root / "flat"
            for folder in (science_dir, bias_dir, dark_dir, flat_dir):
                folder.mkdir()

            self._write_frame(science_dir / "light_1.fits", np.full((8, 8), 100.0, dtype=np.float32))
            self._write_frame(science_dir / "light_2.fits", np.full((8, 8), 120.0, dtype=np.float32))
            self._write_frame(bias_dir / "bias_1.fits", np.full((8, 8), 10.0, dtype=np.float32))
            self._write_frame(dark_dir / "dark_1.fits", np.full((8, 8), 15.0, dtype=np.float32))
            self._write_frame(flat_dir / "flat_1.fits", np.full((8, 8), 210.0, dtype=np.float32))

            result = calibrate_image_sequence(
                CalibrationPipelineRequest(
                    science_path=science_dir,
                    output_directory=science_dir / "calibration_output",
                    bias_path=bias_dir,
                    dark_path=dark_dir,
                    flat_path=flat_dir,
                    align_output=False,
                )
            )

            self.assertEqual(len(result.calibrated_frames), 2)
            self.assertTrue(result.master_bias_path and result.master_bias_path.exists())
            self.assertTrue(result.master_dark_path and result.master_dark_path.exists())
            self.assertTrue(result.master_flat_path and result.master_flat_path.exists())
            self.assertEqual(result.master_bias_path.parent, science_dir / "calibration_output")
            self.assertEqual(result.master_dark_path.parent, science_dir / "calibration_output")
            self.assertEqual(result.master_flat_path.parent, science_dir / "calibration_output")
            with fits.open(result.calibrated_frames[0], memmap=False) as hdul:
                calibrated_data = np.array(hdul[0].data, dtype=np.float32)
                calibrated_header = hdul[0].header.copy()
            self.assertTrue(np.allclose(calibrated_data, 85.0))
            self.assertTrue(bool(calibrated_header["CALIBRAT"]))
            self.assertTrue(bool(calibrated_header["CALBIAS"]))
            self.assertTrue(bool(calibrated_header["CALDARK"]))
            self.assertTrue(bool(calibrated_header["CALFLAT"]))

    def test_scales_bias_corrected_dark_by_science_exposure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            science_dir = root / "science"
            bias_dir = root / "bias"
            dark_dir = root / "dark"
            for folder in (science_dir, bias_dir, dark_dir):
                folder.mkdir()

            self._write_frame(science_dir / "light_1.fits", np.full((8, 8), 100.0, dtype=np.float32), exposure_seconds=20.0)
            self._write_frame(bias_dir / "bias_1.fits", np.full((8, 8), 10.0, dtype=np.float32))
            self._write_frame(dark_dir / "dark_1.fits", np.full((8, 8), 15.0, dtype=np.float32), exposure_seconds=10.0)

            result = calibrate_image_sequence(
                CalibrationPipelineRequest(
                    science_path=science_dir,
                    output_directory=science_dir / "calibration_output",
                    bias_path=bias_dir,
                    dark_path=dark_dir,
                    align_output=False,
                )
            )

            with fits.open(result.calibrated_frames[0], memmap=False) as hdul:
                calibrated_data = np.array(hdul[0].data, dtype=np.float32)
                calibrated_header = hdul[0].header.copy()

            self.assertTrue(np.allclose(calibrated_data, 80.0))
            self.assertAlmostEqual(float(calibrated_header["CALDSCL"]), 2.0)

    def test_mismatched_master_exposures_warn_and_continue(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            science_dir = root / "science"
            bias_dir = root / "bias"
            dark_dir = root / "dark"
            for folder in (science_dir, bias_dir, dark_dir):
                folder.mkdir()

            self._write_frame(science_dir / "light_1.fits", np.full((8, 8), 100.0, dtype=np.float32), exposure_seconds=30.0)
            self._write_frame(bias_dir / "bias_1.fits", np.full((8, 8), 5.0, dtype=np.float32))
            self._write_frame(dark_dir / "dark_1.fits", np.full((8, 8), 15.0, dtype=np.float32), exposure_seconds=10.0)
            self._write_frame(dark_dir / "dark_2.fits", np.full((8, 8), 25.0, dtype=np.float32), exposure_seconds=20.0)
            progress_messages: list[str] = []

            result = calibrate_image_sequence(
                CalibrationPipelineRequest(
                    science_path=science_dir,
                    output_directory=science_dir / "calibration_output",
                    bias_path=bias_dir,
                    dark_path=dark_dir,
                    align_output=False,
                ),
                progress_callback=progress_messages.append,
            )

            with fits.open(result.calibrated_frames[0], memmap=False) as hdul:
                calibrated_data = np.array(hdul[0].data, dtype=np.float32)
                calibrated_header = hdul[0].header.copy()

            self.assertTrue(np.allclose(calibrated_data, 65.0))
            self.assertAlmostEqual(float(calibrated_header["CALDSCL"]), 2.0)
            self.assertTrue(bool(calibrated_header["CALDBIAS"]))
            self.assertTrue(any("Warning: Master dark frames have different exposure times" in message for message in progress_messages))
            self.assertIn("Warnings: Master dark frames have different exposure times", result.summary_text)

    def test_raw_dark_without_bias_is_not_exposure_scaled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            science_dir = root / "science"
            dark_dir = root / "dark"
            for folder in (science_dir, dark_dir):
                folder.mkdir()

            self._write_frame(science_dir / "light_1.fits", np.full((8, 8), 100.0, dtype=np.float32), exposure_seconds=20.0)
            self._write_frame(dark_dir / "dark_1.fits", np.full((8, 8), 15.0, dtype=np.float32), exposure_seconds=10.0)
            progress_messages: list[str] = []

            result = calibrate_image_sequence(
                CalibrationPipelineRequest(
                    science_path=science_dir,
                    output_directory=science_dir / "calibration_output",
                    dark_path=dark_dir,
                    align_output=False,
                ),
                progress_callback=progress_messages.append,
            )

            with fits.open(result.calibrated_frames[0], memmap=False) as hdul:
                calibrated_data = np.array(hdul[0].data, dtype=np.float32)
                calibrated_header = hdul[0].header.copy()
            with fits.open(result.master_dark_path, memmap=False) as hdul:
                master_dark_header = hdul[0].header.copy()

            self.assertTrue(np.allclose(calibrated_data, 85.0))
            self.assertAlmostEqual(float(calibrated_header["CALDSCL"]), 1.0)
            self.assertFalse(bool(calibrated_header["CALDBIAS"]))
            self.assertFalse(bool(master_dark_header["BIASCOR"]))
            self.assertTrue(any("Dark exposure scaling was disabled because the dark master is not bias-corrected" in message for message in progress_messages))

    def test_selected_master_sources_rebuild_existing_cached_masters(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            science_dir = root / "science"
            bias_dir = root / "bias"
            dark_dir = root / "dark"
            output_dir = root / "calibration_output"
            for folder in (science_dir, bias_dir, dark_dir):
                folder.mkdir()

            self._write_frame(science_dir / "light_1.fits", np.full((8, 8), 100.0, dtype=np.float32))
            self._write_frame(bias_dir / "bias_1.fits", np.full((8, 8), 10.0, dtype=np.float32))
            self._write_frame(dark_dir / "dark_1.fits", np.full((8, 8), 15.0, dtype=np.float32))

            calibrate_image_sequence(
                CalibrationPipelineRequest(
                    science_path=science_dir,
                    output_directory=output_dir,
                    bias_path=bias_dir,
                    dark_path=dark_dir,
                    align_output=False,
                )
            )

            self._write_frame(dark_dir / "dark_1.fits", np.full((8, 8), 25.0, dtype=np.float32))
            result = calibrate_image_sequence(
                CalibrationPipelineRequest(
                    science_path=science_dir,
                    output_directory=output_dir,
                    bias_path=bias_dir,
                    dark_path=dark_dir,
                    align_output=False,
                )
            )

            with fits.open(result.calibrated_frames[0], memmap=False) as hdul:
                calibrated_data = np.array(hdul[0].data, dtype=np.float32)

            self.assertTrue(np.allclose(calibrated_data, 75.0))

    def test_reuses_cached_output_masters_when_sources_are_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            science_dir = root / "science"
            bias_dir = root / "bias"
            dark_dir = root / "dark"
            flat_dir = root / "flat"
            output_dir = root / "calibration_output"
            for folder in (science_dir, bias_dir, dark_dir, flat_dir):
                folder.mkdir()

            self._write_frame(science_dir / "light_1.fits", np.full((8, 8), 100.0, dtype=np.float32))
            self._write_frame(science_dir / "light_2.fits", np.full((8, 8), 120.0, dtype=np.float32))
            self._write_frame(bias_dir / "bias_1.fits", np.full((8, 8), 10.0, dtype=np.float32))
            self._write_frame(dark_dir / "dark_1.fits", np.full((8, 8), 15.0, dtype=np.float32))
            self._write_frame(flat_dir / "flat_1.fits", np.full((8, 8), 210.0, dtype=np.float32))

            calibrate_image_sequence(
                CalibrationPipelineRequest(
                    science_path=science_dir,
                    output_directory=output_dir,
                    bias_path=bias_dir,
                    dark_path=dark_dir,
                    flat_path=flat_dir,
                    align_output=False,
                )
            )

            result = calibrate_image_sequence(
                CalibrationPipelineRequest(
                    science_path=science_dir,
                    output_directory=output_dir,
                    align_output=False,
                )
            )

            self.assertEqual(len(result.calibrated_frames), 2)
            self.assertEqual(result.master_bias_path, output_dir / "master_bias.fits")
            self.assertEqual(result.master_dark_path, output_dir / "master_dark.fits")
            self.assertEqual(result.master_flat_path, output_dir / "master_flat_normalized.fits")
            with fits.open(result.calibrated_frames[0], memmap=False) as hdul:
                calibrated_data = np.array(hdul[0].data, dtype=np.float32)
            self.assertTrue(np.allclose(calibrated_data, 85.0))

    def test_settings_round_trip_calibration_master_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "settings.json"
            with patch.dict(
                os.environ,
                {
                    "CITIZEN_PHOTOMETRY_CONFIG_PATH": str(config_path),
                },
                clear=False,
            ):
                settings = AppSettings.from_root(root)
                settings.calibration_bias_path = str((root / "bias").resolve())
                settings.calibration_dark_path = str((root / "dark").resolve())
                settings.calibration_flat_path = str((root / "flat").resolve())
                settings.save(root)

                reloaded = AppSettings.from_root(root)

            self.assertEqual(reloaded.calibration_bias_path, str((root / "bias").resolve()))
            self.assertEqual(reloaded.calibration_dark_path, str((root / "dark").resolve()))
            self.assertEqual(reloaded.calibration_flat_path, str((root / "flat").resolve()))

    def test_optional_alignment_receives_parallel_worker_setting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            science_dir = root / "science"
            science_dir.mkdir()
            self._write_frame(science_dir / "light_1.fits", np.full((8, 8), 100.0, dtype=np.float32))
            self._write_frame(science_dir / "light_2.fits", np.full((8, 8), 120.0, dtype=np.float32))

            with patch("photometry_app.core.calibration.align_wcs_image_sequence") as align_mock:
                align_mock.return_value = SequenceAlignmentResult(
                    output_directory=root / "calibration_output" / "aligned",
                    reference_path=Path("light_1_calibrated.fits"),
                    aligned_frames=(),
                    summary_text="aligned",
                )

                calibrate_image_sequence(
                    CalibrationPipelineRequest(
                        science_path=science_dir,
                        output_directory=root / "calibration_output",
                        align_output=True,
                        max_parallel_workers=7,
                    )
                )

            self.assertEqual(align_mock.call_count, 1)
            self.assertEqual(align_mock.call_args.kwargs["max_parallel_workers"], 7)

    def _write_frame(self, path: Path, data: np.ndarray, *, exposure_seconds: float | None = None) -> None:
        header = fits.Header()
        if exposure_seconds is not None:
            header["EXPTIME"] = float(exposure_seconds)
        fits.PrimaryHDU(data=data, header=header).writeto(path, overwrite=True)


if __name__ == "__main__":
    unittest.main()