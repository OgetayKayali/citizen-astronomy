from __future__ import annotations

from datetime import datetime
import math
import unittest

import numpy as np

from pathlib import Path

from astropy.io.fits import Header
from astropy.stats import SigmaClip
from astropy.wcs import WCS

from photometry_app.core.models import CatalogStar, FileScanResult, ObservationMetadata, PhotometryApertureMode, WcsStatus
from photometry_app.core.photometry import PhotometryFrameContext, _estimate_flux_error, _estimate_star_fwhm, _inside_image, _is_near_saturated, _measure_source_saturation, _resolve_catalog_source_position, _resolve_saturation_threshold, _usable_image_margin, measure_targets
from photometry_app.core.settings import AppSettings


class PhotometryErrorTest(unittest.TestCase):
    @staticmethod
    def _build_test_wcs() -> WCS:
        wcs = WCS(naxis=2)
        wcs.wcs.crpix = [1.0, 1.0]
        wcs.wcs.cdelt = np.array([-1.0 / 3600.0, 1.0 / 3600.0])
        wcs.wcs.crval = [150.0, 2.0]
        wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
        return wcs

    @staticmethod
    def _add_gaussian_star(data: np.ndarray, x_center: float, y_center: float, sigma: float, amplitude: float) -> None:
        y_indices, x_indices = np.indices(data.shape, dtype=float)
        data += amplitude * np.exp(-(((x_indices - x_center) ** 2) + ((y_indices - y_center) ** 2)) / (2.0 * sigma * sigma))

    def test_estimate_flux_error_prefers_local_annulus_noise(self) -> None:
        error_value = _estimate_flux_error(
            background_corrected_flux=10000.0,
            aperture_area=100.0,
            annulus_area=1000.0,
            local_background_std=5.0,
            fallback_background_std=50.0,
        )

        expected = math.sqrt(10000.0 + (100.0 * (5.0 ** 2) * (1.0 + (100.0 / 1000.0))))
        self.assertAlmostEqual(error_value, expected, places=6)

    def test_estimate_flux_error_falls_back_when_local_noise_is_invalid(self) -> None:
        error_value = _estimate_flux_error(
            background_corrected_flux=10000.0,
            aperture_area=100.0,
            annulus_area=1000.0,
            local_background_std=float("nan"),
            fallback_background_std=8.0,
        )

        expected = math.sqrt(10000.0 + (100.0 * (8.0 ** 2) * (1.0 + (100.0 / 1000.0))))
        self.assertAlmostEqual(error_value, expected, places=6)

    def test_resolve_catalog_source_position_recenters_small_wcs_offsets(self) -> None:
        data = np.zeros((40, 40), dtype=float)
        data[20, 20] = 1000.0
        x_value, y_value, flags = _resolve_catalog_source_position(data, predicted_x=17.5, predicted_y=18.0, aperture_radius=5.0)

        self.assertAlmostEqual(x_value, 20.0, places=1)
        self.assertAlmostEqual(y_value, 20.0, places=1)
        self.assertEqual(flags, [])

    def test_resolve_catalog_source_position_rejects_large_shifts(self) -> None:
        data = np.zeros((60, 60), dtype=float)
        data[28, 28] = 1000.0
        x_value, y_value, flags = _resolve_catalog_source_position(data, predicted_x=20.0, predicted_y=20.0, aperture_radius=12.0)

        self.assertEqual((x_value, y_value), (20.0, 20.0))
        self.assertTrue(any("exceeded the max recenter radius" in flag for flag in flags))

    def test_inside_image_respects_configured_fractional_edge_margin(self) -> None:
        margin = _usable_image_margin((100, 200), annulus_outer_radius=12.0, frame_edge_margin_percent=15.0)

        self.assertEqual(margin, (30.0, 15.0))
        self.assertFalse(_inside_image(20.0, 50.0, (100, 200), margin))
        self.assertFalse(_inside_image(100.0, 10.0, (100, 200), margin))
        self.assertTrue(_inside_image(100.0, 50.0, (100, 200), margin))

    def test_resolve_saturation_threshold_prefers_header_keyword(self) -> None:
        header = Header()
        header["SATURATE"] = 54321.0

        threshold = _resolve_saturation_threshold(header, np.zeros((8, 8), dtype=float), Path("frame.fits"))

        self.assertEqual(threshold, 54321.0)

    def test_measure_source_saturation_detects_saturated_core_pixels(self) -> None:
        data = np.zeros((25, 25), dtype=float)
        data[12, 12] = 65535.0
        data[12, 13] = 65535.0

        peak_pixel_value, saturated_pixel_count, is_saturated = _measure_source_saturation(
            data,
            x=12.0,
            y=12.0,
            aperture_radius=3.5,
            saturation_threshold=65535.0,
        )

        self.assertEqual(peak_pixel_value, 65535.0)
        self.assertEqual(saturated_pixel_count, 2)
        self.assertTrue(is_saturated)

    def test_is_near_saturated_warns_below_hard_limit(self) -> None:
        self.assertTrue(_is_near_saturated(65000.0, 65535.0, False))
        self.assertFalse(_is_near_saturated(60000.0, 65535.0, False))
        self.assertFalse(_is_near_saturated(65535.0, 65535.0, True))

    def test_measure_targets_uses_local_fwhm_per_source_in_adaptive_mode(self) -> None:
        data = np.zeros((120, 120), dtype=float)
        self._add_gaussian_star(data, x_center=35.0, y_center=40.0, sigma=1.1, amplitude=4000.0)
        self._add_gaussian_star(data, x_center=80.0, y_center=78.0, sigma=2.7, amplitude=4000.0)
        wcs = self._build_test_wcs()
        star_a_ra, star_a_dec = wcs.pixel_to_world_values(35.0, 40.0)
        star_b_ra, star_b_dec = wcs.pixel_to_world_values(80.0, 78.0)
        scan_result = FileScanResult(
            path=Path("frame.fits"),
            object_folder="Alpha",
            metadata=ObservationMetadata(
                date_obs=datetime(2025, 1, 1),
                filter_name="V",
                exposure_seconds=60.0,
                width=120,
                height=120,
                object_name="Alpha",
            ),
            wcs_status=WcsStatus.SOLVED,
        )
        settings = AppSettings.from_root(Path("."))
        settings.photometry_aperture_mode = PhotometryApertureMode.FWHM_SCALED
        settings.aperture_radius_pixels = 5.0
        settings.annulus_inner_radius_pixels = 8.0
        settings.annulus_outer_radius_pixels = 12.0
        settings.aperture_radius_fwhm_scale = 1.6
        settings.annulus_inner_radius_fwhm_scale = 3.0
        settings.annulus_outer_radius_fwhm_scale = 4.5
        frame_context = PhotometryFrameContext(
            source_path=scan_result.path,
            wcs_path=Path("frame.wcs"),
            source_header=Header(),
            data=data,
            wcs=wcs,
            background_median=0.0,
            background_std=0.0,
            sigma_clip=SigmaClip(sigma=3.0),
            saturation_threshold=None,
            is_2d=True,
        )
        measurements = measure_targets(
            source_path=scan_result.path,
            scan_result=scan_result,
            wcs_path=Path("frame.wcs"),
            variable_stars=[
                CatalogStar("gaia", "star-a", "Star A", star_a_ra, star_a_dec, 11.0, True),
                CatalogStar("gaia", "star-b", "Star B", star_b_ra, star_b_dec, 11.5, True),
            ],
            reference_stars=[],
            aperture_radius=5.0,
            annulus_inner_radius=8.0,
            annulus_outer_radius=12.0,
            settings=settings,
            frame_context=frame_context,
        )

        self.assertEqual(len(measurements), 2)
        measurement_by_id = {measurement.source_id: measurement for measurement in measurements}
        self.assertLess(measurement_by_id["star-a"].aperture_radius or 0.0, measurement_by_id["star-b"].aperture_radius or 0.0)
        self.assertLess(measurement_by_id["star-a"].annulus_outer_radius or 0.0, measurement_by_id["star-b"].annulus_outer_radius or 0.0)
        self.assertGreater(abs((measurement_by_id["star-b"].aperture_radius or 0.0) - (measurement_by_id["star-a"].aperture_radius or 0.0)), 1.0)

    def test_estimate_star_fwhm_does_not_broaden_faint_equal_psf_sources(self) -> None:
        rng = np.random.default_rng(0)
        data = rng.normal(1000.0, 80.0, size=(120, 120))
        self._add_gaussian_star(data, x_center=35.0, y_center=35.0, sigma=1.2, amplitude=12000.0)
        self._add_gaussian_star(data, x_center=80.0, y_center=80.0, sigma=1.2, amplitude=2500.0)

        bright_fwhm = _estimate_star_fwhm(data, 35.0, 35.0)
        faint_fwhm = _estimate_star_fwhm(data, 80.0, 80.0)

        self.assertIsNotNone(bright_fwhm)
        self.assertIsNotNone(faint_fwhm)
        assert bright_fwhm is not None
        assert faint_fwhm is not None
        self.assertLess(abs(bright_fwhm - faint_fwhm), 0.4)


if __name__ == "__main__":
    unittest.main()