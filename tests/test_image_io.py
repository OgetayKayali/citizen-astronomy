from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from astropy.io import fits
from PIL import Image

from photometry_app.core.image_io import (
    is_supported_image_path,
    photometry_xisf_scale_factor,
    read_header,
    read_header_and_shape,
    read_photometry_image_data,
    write_fits_copy,
)


class ImageIoTest(unittest.TestCase):
    def test_photometry_xisf_scale_factor_detects_normalized_float_images(self) -> None:
        metadata = {"sampleFormat": "Float32", "bounds": "0:1"}
        self.assertEqual(photometry_xisf_scale_factor(metadata), 65535.0)

    def test_photometry_xisf_scale_factor_ignores_non_normalized_data(self) -> None:
        metadata = {"sampleFormat": "UInt16", "bounds": "0:65535"}
        self.assertIsNone(photometry_xisf_scale_factor(metadata))

    def test_read_photometry_image_data_rescales_normalized_xisf_samples(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "frame.xisf"
            path.write_text("placeholder", encoding="utf-8")
            with patch("photometry_app.core.image_io._read_xisf_metadata", return_value={"sampleFormat": "Float32", "bounds": "0:1"}), patch(
                "photometry_app.core.image_io._read_xisf_image",
                return_value=np.asarray([[0.0, 0.5], [1.0, 0.25]], dtype=np.float32),
            ):
                data = read_photometry_image_data(path)

        expected = np.asarray([[0.0, 32767.5], [65535.0, 16383.75]], dtype=float)
        np.testing.assert_allclose(data, expected)

    def test_write_fits_copy_collapses_rgb_image_to_monochrome(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "frame.xisf"
            destination_path = Path(temp_dir) / "frame_plate_solve.fits"
            source_path.write_text("placeholder", encoding="utf-8")

            with patch("photometry_app.core.image_io.read_header", return_value=fits.Header()), patch(
                "photometry_app.core.image_io.read_image_data",
                return_value=np.asarray(
                    [
                        [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]],
                        [[7.0, 8.0, 9.0], [10.0, 11.0, 12.0]],
                    ],
                    dtype=np.float32,
                ),
            ):
                write_fits_copy(source_path, destination_path)

            with fits.open(destination_path) as hdul:
                written = np.asarray(hdul[0].data, dtype=float)

        expected = np.asarray([[2.0, 5.0], [8.0, 11.0]], dtype=float)
        np.testing.assert_allclose(written, expected)

    def test_read_header_and_shape_synthesizes_wcs_from_pixinsight_astrometric_properties(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "frame.xisf"
            path.write_text("placeholder", encoding="utf-8")
            metadata = {
                "geometry": (2742, 2460, 3),
                "FITSKeywords": {
                    "OBJECT": [{"value": "M46", "comment": "Target"}],
                },
                "XISFProperties": {
                    "PCL:AstrometricSolution:ProjectionSystem": {"value": "Gnomonic"},
                    "PCL:AstrometricSolution:ReferenceCelestialCoordinates": {"value": np.asarray([115.43955634, -14.83711658], dtype=np.float64)},
                    "PCL:AstrometricSolution:ReferenceImageCoordinates": {"value": np.asarray([1371.10375286, 1230.00944601], dtype=np.float64)},
                    "PCL:AstrometricSolution:LinearTransformationMatrix": {
                        "value": np.asarray(
                            [[3.58971783e-05, 4.31456382e-04], [-4.31455335e-04, 3.64919218e-05]],
                            dtype=np.float64,
                        )
                    },
                },
            }
            with patch("photometry_app.core.image_io._read_xisf_metadata", return_value=metadata):
                header, width, height = read_header_and_shape(path)

        self.assertEqual((width, height), (2742, 2460))
        self.assertEqual(header["CTYPE1"], "RA---TAN")
        self.assertEqual(header["CTYPE2"], "DEC--TAN")
        self.assertAlmostEqual(float(header["CRVAL1"]), 115.43955634)
        self.assertAlmostEqual(float(header["CRVAL2"]), -14.83711658)
        self.assertAlmostEqual(float(header["CRPIX1"]), 1371.10375286)
        self.assertAlmostEqual(float(header["CRPIX2"]), 1230.00944601)

    def test_read_header_and_shape_promotes_timezone_aware_xisf_observation_time(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "frame.xisf"
            path.write_text("placeholder", encoding="utf-8")
            metadata = {
                "geometry": (32, 32, 1),
                "FITSKeywords": {
                    "DATE-OBS": [{"value": "2025-12-07T09:00:30.775588", "comment": "Image exposure start time"}],
                },
                "XISFProperties": {
                    "Observation:Time:Start": {"value": "2025-12-07T09:00:30.776Z"},
                    "Observation:Time:End": {"value": "2025-12-07T09:02:30.776Z"},
                },
            }
            with patch("photometry_app.core.image_io._read_xisf_metadata", return_value=metadata):
                header, width, height = read_header_and_shape(path)

        self.assertEqual((width, height), (32, 32))
        self.assertEqual(header["DATE-OBS"], "2025-12-07T09:00:30.776Z")
        self.assertEqual(header["DATE-END"], "2025-12-07T09:02:30.776Z")

    def test_supported_image_paths_include_common_raster_formats(self) -> None:
        self.assertTrue(is_supported_image_path(Path("frame.tif")))
        self.assertTrue(is_supported_image_path(Path("frame.tiff")))
        self.assertTrue(is_supported_image_path(Path("frame.png")))
        self.assertTrue(is_supported_image_path(Path("frame.jpg")))
        self.assertTrue(is_supported_image_path(Path("frame.jpeg")))

    def test_read_header_and_shape_reads_standard_raster_image_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "frame.png"
            Image.fromarray(
                np.asarray(
                    [
                        [[0, 10, 20], [30, 40, 50], [60, 70, 80]],
                        [[90, 100, 110], [120, 130, 140], [150, 160, 170]],
                    ],
                    dtype=np.uint8,
                ),
                mode="RGB",
            ).save(path)

            header = read_header(path)
            header_with_shape, width, height = read_header_and_shape(path)

        self.assertEqual((width, height), (3, 2))
        self.assertEqual(header["NAXIS1"], 3)
        self.assertEqual(header["NAXIS2"], 2)
        self.assertEqual(header_with_shape["IMGMODE"], "RGB")

    def test_read_photometry_image_data_reads_standard_raster_pixels(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "frame.tiff"
            expected = np.asarray([[1000, 2000], [3000, 4000]], dtype=np.uint16)
            Image.fromarray(expected).save(path)

            data = read_photometry_image_data(path, dtype=np.float32)

        np.testing.assert_allclose(data, expected.astype(np.float32))


if __name__ == "__main__":
    unittest.main()