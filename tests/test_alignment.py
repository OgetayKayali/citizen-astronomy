from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import numpy as np
from astropy.io import fits
from astropy.time import Time
from astropy.wcs import WCS

import photometry_app.core.alignment as alignment_module
from photometry_app.core.alignment import _resolve_alignment_worker_count, align_wcs_image_sequence


class AlignmentTest(unittest.TestCase):
    def test_alignment_worker_count_caps_configured_parallelism_for_memory(self) -> None:
        self.assertEqual(_resolve_alignment_worker_count(24, 650), 2)
        self.assertEqual(_resolve_alignment_worker_count(7, 10), 2)
        self.assertEqual(_resolve_alignment_worker_count(24, 650, fast_affine=True), 4)
        self.assertEqual(_resolve_alignment_worker_count(7, 10, fast_affine=True), 4)
        self.assertEqual(_resolve_alignment_worker_count(1, 10), 1)

    @unittest.skipIf(alignment_module.ndimage is None, "scipy.ndimage is unavailable")
    def test_linear_wcs_alignment_uses_fast_affine_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reference_path = root / "reference.fits"
            shifted_path = root / "shifted.fits"
            output_dir = root / "aligned"

            reference_wcs = WCS(naxis=2)
            reference_wcs.wcs.crpix = [10.0, 10.0]
            reference_wcs.wcs.cdelt = np.array([-1.0 / 3600.0, 1.0 / 3600.0])
            reference_wcs.wcs.crval = [10.0, 20.0]
            reference_wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
            reference_data = np.zeros((21, 21), dtype=np.float32)
            reference_data[10, 10] = 1000.0
            fits.PrimaryHDU(data=reference_data, header=reference_wcs.to_header()).writeto(reference_path)

            shifted_wcs = WCS(naxis=2)
            shifted_wcs.wcs.crpix = [12.0, 9.0]
            shifted_wcs.wcs.cdelt = np.array([-1.0 / 3600.0, 1.0 / 3600.0])
            shifted_wcs.wcs.crval = [10.0, 20.0]
            shifted_wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
            shifted_data = np.zeros((21, 21), dtype=np.float32)
            shifted_data[9, 12] = 1000.0
            fits.PrimaryHDU(data=shifted_data, header=shifted_wcs.to_header()).writeto(shifted_path)

            with patch("photometry_app.core.alignment.reproject_interp", side_effect=AssertionError("generic reproject should not be used")):
                result = align_wcs_image_sequence(
                    [reference_path, shifted_path],
                    reference_path=reference_path,
                    output_directory=output_dir,
                    max_parallel_workers=8,
                )

            self.assertEqual(len(result.aligned_frames), 2)
            self.assertTrue((output_dir / "shifted.fits").exists())

    def test_align_wcs_image_sequence_reprojects_frames_onto_reference_grid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reference_path = root / "reference.fits"
            shifted_path = root / "shifted.fits"
            output_dir = root / "aligned"

            reference_wcs = WCS(naxis=2)
            reference_wcs.wcs.crpix = [10.0, 10.0]
            reference_wcs.wcs.cdelt = np.array([-1.0 / 3600.0, 1.0 / 3600.0])
            reference_wcs.wcs.crval = [10.0, 20.0]
            reference_wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
            reference_data = np.zeros((21, 21), dtype=np.float32)
            reference_data[10, 10] = 1000.0
            reference_header = reference_wcs.to_header()
            reference_header["DATE-OBS"] = "2025-01-14T21:12:00Z"
            reference_header["MJD-OBS"] = float(Time(datetime.fromisoformat(reference_header["DATE-OBS"].replace("Z", "+00:00"))).mjd)
            fits.PrimaryHDU(data=reference_data, header=reference_header).writeto(reference_path)

            shifted_wcs = WCS(naxis=2)
            shifted_wcs.wcs.crpix = [12.0, 9.0]
            shifted_wcs.wcs.cdelt = np.array([-1.0 / 3600.0, 1.0 / 3600.0])
            shifted_wcs.wcs.crval = [10.0, 20.0]
            shifted_wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
            shifted_data = np.zeros((21, 21), dtype=np.float32)
            shifted_data[9, 12] = 1000.0
            shifted_header = shifted_wcs.to_header()
            shifted_header["DATE-OBS"] = "2025-01-14T21:14:00Z"
            shifted_header["MJD-OBS"] = float(Time(datetime.fromisoformat(shifted_header["DATE-OBS"].replace("Z", "+00:00"))).mjd)
            fits.PrimaryHDU(data=shifted_data, header=shifted_header).writeto(shifted_path)

            result = align_wcs_image_sequence(
                [reference_path, shifted_path],
                reference_path=reference_path,
                output_directory=output_dir,
                max_parallel_workers=2,
            )

            self.assertEqual(result.output_directory, output_dir)
            self.assertEqual(len(result.aligned_frames), 2)
            self.assertTrue((output_dir / "reference.fits").exists())
            self.assertTrue((output_dir / "shifted.fits").exists())

            with fits.open(output_dir / "shifted.fits") as hdul:
                aligned_data = np.asarray(hdul[0].data, dtype=float)
                aligned_header = hdul[0].header

            max_position = np.unravel_index(int(np.argmax(aligned_data)), aligned_data.shape)
            self.assertEqual(max_position, (10, 10))
            self.assertAlmostEqual(float(aligned_header["CRPIX1"]), 10.0)
            self.assertAlmostEqual(float(aligned_header["CRPIX2"]), 10.0)
            self.assertEqual(aligned_header["DATE-OBS"], "2025-01-14T21:14:00Z")

    def test_align_wcs_image_sequence_can_align_onto_cropped_reference_grid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reference_path = root / "reference.fits"
            shifted_path = root / "shifted.fits"
            output_dir = root / "aligned"

            reference_wcs = WCS(naxis=2)
            reference_wcs.wcs.crpix = [10.0, 10.0]
            reference_wcs.wcs.cdelt = np.array([-1.0 / 3600.0, 1.0 / 3600.0])
            reference_wcs.wcs.crval = [10.0, 20.0]
            reference_wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
            reference_data = np.zeros((21, 21), dtype=np.float32)
            reference_data[10, 10] = 1000.0
            fits.PrimaryHDU(data=reference_data, header=reference_wcs.to_header()).writeto(reference_path)

            shifted_wcs = WCS(naxis=2)
            shifted_wcs.wcs.crpix = [12.0, 9.0]
            shifted_wcs.wcs.cdelt = np.array([-1.0 / 3600.0, 1.0 / 3600.0])
            shifted_wcs.wcs.crval = [10.0, 20.0]
            shifted_wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
            shifted_data = np.zeros((21, 21), dtype=np.float32)
            shifted_data[9, 12] = 1000.0
            fits.PrimaryHDU(data=shifted_data, header=shifted_wcs.to_header()).writeto(shifted_path)

            result = align_wcs_image_sequence(
                [reference_path, shifted_path],
                reference_path=reference_path,
                output_directory=output_dir,
                max_parallel_workers=2,
                reference_crop_bounds=(8, 8, 13, 13),
            )

            self.assertEqual(len(result.aligned_frames), 2)

            with fits.open(output_dir / "shifted.fits") as hdul:
                aligned_data = np.asarray(hdul[0].data, dtype=float)
                aligned_header = hdul[0].header

            self.assertEqual(aligned_data.shape, (5, 5))
            max_position = np.unravel_index(int(np.argmax(aligned_data)), aligned_data.shape)
            self.assertEqual(max_position, (2, 2))
            self.assertAlmostEqual(float(aligned_header["CRPIX1"]), 2.0)
            self.assertAlmostEqual(float(aligned_header["CRPIX2"]), 2.0)

    @unittest.skipIf(alignment_module.ndimage is None, "scipy.ndimage is unavailable")
    def test_align_wcs_image_sequence_refines_residual_star_shift(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reference_path = root / "reference.fits"
            shifted_path = root / "shifted_bad_wcs.fits"
            output_dir = root / "aligned"
            star_positions = [
                (14, 16, 1200.0),
                (22, 42, 850.0),
                (38, 28, 950.0),
                (49, 50, 780.0),
                (52, 18, 700.0),
            ]

            reference_wcs = self._linear_wcs(crpix=(32.0, 32.0))
            reference_data = self._star_field((64, 64), star_positions)
            fits.PrimaryHDU(data=reference_data, header=reference_wcs.to_header()).writeto(reference_path)

            actual_row_shift = 4
            actual_column_shift = -3
            shifted_positions = [
                (row + actual_row_shift, column + actual_column_shift, brightness)
                for row, column, brightness in star_positions
            ]
            shifted_data = self._star_field((64, 64), shifted_positions)
            shifted_wcs = self._linear_wcs(crpix=(31.0, 33.0))
            fits.PrimaryHDU(data=shifted_data, header=shifted_wcs.to_header()).writeto(shifted_path)

            result = align_wcs_image_sequence(
                [reference_path, shifted_path],
                reference_path=reference_path,
                output_directory=output_dir,
                max_parallel_workers=1,
            )

            self.assertEqual(len(result.aligned_frames), 2)
            with fits.open(output_dir / "shifted_bad_wcs.fits") as hdul:
                aligned_data = np.asarray(hdul[0].data, dtype=float)
                aligned_header = hdul[0].header

            max_position = np.unravel_index(int(np.argmax(aligned_data)), aligned_data.shape)
            self.assertLess(float(np.hypot(max_position[0] - 14, max_position[1] - 16)), 1.5)
            self.assertAlmostEqual(float(aligned_header["ALNDY"]), -3.0, delta=0.75)
            self.assertAlmostEqual(float(aligned_header["ALNDX"]), 2.0, delta=0.75)

    def _linear_wcs(self, *, crpix: tuple[float, float]) -> WCS:
        wcs = WCS(naxis=2)
        wcs.wcs.crpix = [float(crpix[0]), float(crpix[1])]
        wcs.wcs.cdelt = np.array([-1.0 / 3600.0, 1.0 / 3600.0])
        wcs.wcs.crval = [10.0, 20.0]
        wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
        return wcs

    def _star_field(self, shape: tuple[int, int], stars: list[tuple[int, int, float]]) -> np.ndarray:
        data = np.zeros(shape, dtype=np.float32)
        for row, column, brightness in stars:
            data[row, column] = np.float32(brightness)
            data[row - 1, column] = np.float32(brightness * 0.35)
            data[row + 1, column] = np.float32(brightness * 0.35)
            data[row, column - 1] = np.float32(brightness * 0.35)
            data[row, column + 1] = np.float32(brightness * 0.35)
        return data


if __name__ == "__main__":
    unittest.main()