from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from astropy.wcs import WCS

from photometry_app.core.hr_diagram import (
    HR_GAIA_MAX_MAGNITUDE,
    HR_GAIA_ROW_CAP,
    HrMeasurementRow,
    _DetectedSource,
    _absolute_magnitude_from_parallax,
    _apply_photometric_calibration,
    _centroid_plane_from_named_planes,
    _display_color_hex,
    _extract_named_planes,
    _match_detected_sources_to_gaia,
    measure_hr_sources,
    resolve_hr_gaia_query_limits,
    resolve_hr_gaia_tile_options,
)
from photometry_app.core.models import CatalogStar
from photometry_app.core.settings import AppSettings


class HrDiagramHelperTest(unittest.TestCase):
    def test_resolve_hr_gaia_query_limits_uses_hr_sized_caps(self) -> None:
        magnitude, row_cap = resolve_hr_gaia_query_limits(SimpleNamespace(hr_max_sources=5000))

        self.assertEqual(magnitude, HR_GAIA_MAX_MAGNITUDE)
        self.assertEqual(row_cap, HR_GAIA_ROW_CAP)
        self.assertGreaterEqual(row_cap, 25000)
        self.assertEqual(magnitude, 18.0)

    def test_resolve_hr_gaia_query_limits_grows_with_larger_max_sources(self) -> None:
        _magnitude, row_cap = resolve_hr_gaia_query_limits(SimpleNamespace(hr_max_sources=50000))

        self.assertEqual(row_cap, 50000)

    def test_resolve_hr_gaia_query_limits_uses_settings_overrides(self) -> None:
        magnitude, row_cap = resolve_hr_gaia_query_limits(
            SimpleNamespace(hr_max_sources=5000, hr_gaia_max_magnitude=16.5, hr_gaia_row_cap=12000)
        )

        self.assertEqual(magnitude, 16.5)
        self.assertEqual(row_cap, 12000)

    def test_resolve_hr_gaia_tile_options_uses_settings_overrides(self) -> None:
        options = resolve_hr_gaia_tile_options(
            SimpleNamespace(
                hr_gaia_tile_max_radius_deg=0.25,
                hr_gaia_tile_radius_margin=1.20,
                hr_gaia_tile_max_count=16,
                hr_gaia_tile_max_split_depth=1,
            )
        )

        self.assertAlmostEqual(options.max_radius_deg, 0.25)
        self.assertAlmostEqual(options.radius_margin, 1.20)
        self.assertEqual(options.max_count, 16)
        self.assertEqual(options.max_split_depth, 1)

    def test_extract_named_planes_handles_rgb_last_axis(self) -> None:
        image = np.zeros((4, 5, 3), dtype=float)
        image[:, :, 0] = 10.0
        image[:, :, 1] = 20.0
        image[:, :, 2] = 30.0

        planes = _extract_named_planes(image)

        self.assertEqual(list(planes.keys()), ["red", "green", "blue", "luminance"])
        self.assertEqual(planes["red"].shape, (4, 5))
        self.assertTrue(np.allclose(planes["red"], 10.0))
        self.assertTrue(np.allclose(planes["green"], 20.0))
        self.assertTrue(np.allclose(planes["blue"], 30.0))
        self.assertTrue(np.allclose(planes["luminance"], 20.0))

    def test_extract_named_planes_handles_rgb_first_axis(self) -> None:
        image = np.zeros((3, 4, 5), dtype=float)
        image[0, :, :] = 5.0
        image[1, :, :] = 15.0
        image[2, :, :] = 25.0

        planes = _extract_named_planes(image)

        self.assertEqual(list(planes.keys()), ["red", "green", "blue", "luminance"])
        self.assertTrue(np.allclose(planes["red"], 5.0))
        self.assertTrue(np.allclose(planes["green"], 15.0))
        self.assertTrue(np.allclose(planes["blue"], 25.0))
        self.assertTrue(np.allclose(planes["luminance"], 15.0))

    def test_extract_named_planes_handles_monochrome(self) -> None:
        image = np.full((4, 5), 42.0, dtype=float)

        planes = _extract_named_planes(image)

        self.assertEqual(list(planes.keys()), ["luminance"])
        self.assertTrue(np.allclose(planes["luminance"], 42.0))

    def test_centroid_plane_prefers_luminance_without_numpy_truthiness(self) -> None:
        image = np.zeros((4, 5, 3), dtype=float)
        image[:, :, 0] = 10.0
        image[:, :, 1] = 20.0
        image[:, :, 2] = 30.0

        planes = _extract_named_planes(image)
        centroid_plane = _centroid_plane_from_named_planes(planes)

        self.assertIs(centroid_plane, planes["luminance"])

    def test_display_color_hex_scales_channels_by_brightest_flux(self) -> None:
        self.assertEqual(_display_color_hex(None, 100.0, 50.0, 25.0), "#f2824a")
        self.assertIsNone(_display_color_hex(None, None, 50.0, 25.0))
        self.assertIsNone(_display_color_hex(None, 0.0, 50.0, 25.0))

    def test_display_color_hex_prefers_gaia_bp_rp_stellar_palette(self) -> None:
        blue_star = _display_color_hex(0.0, 100.0, 100.0, 100.0)
        solar_like_star = _display_color_hex(0.8, 100.0, 100.0, 100.0)
        red_star = _display_color_hex(2.5, 100.0, 100.0, 100.0)

        self.assertEqual(blue_star, "#5397ff")
        self.assertEqual(solar_like_star, "#fff6d2")
        self.assertEqual(red_star, "#ff4500")

    def test_absolute_magnitude_from_parallax_uses_mas_formula(self) -> None:
        absolute_mag = _absolute_magnitude_from_parallax(10.0, 10.0)

        self.assertIsNotNone(absolute_mag)
        if absolute_mag is None:
            self.fail("Expected an absolute magnitude for a positive parallax.")
        self.assertAlmostEqual(absolute_mag, 5.0, places=6)
        self.assertIsNone(_absolute_magnitude_from_parallax(10.0, None))
        self.assertIsNone(_absolute_magnitude_from_parallax(10.0, -1.0))

    def test_apply_photometric_calibration_estimates_zero_point_and_absolute_proxy(self) -> None:
        rows = [
            HrMeasurementRow(
                source_id="1",
                source_name="1",
                catalog="gaia-dr3",
                ra_deg=0.0,
                dec_deg=0.0,
                gaia_g_mag=12.0,
                gaia_bp_rp=0.8,
                parallax_mas=10.0,
                parallax_error_mas=0.2,
                x=10.0,
                y=10.0,
                aperture_radius=5.0,
                annulus_inner_radius=8.0,
                annulus_outer_radius=12.0,
                instrumental_mag_luminance=10.0,
                instrumental_blue_minus_red=0.5,
            ),
            HrMeasurementRow(
                source_id="2",
                source_name="2",
                catalog="gaia-dr3",
                ra_deg=1.0,
                dec_deg=1.0,
                gaia_g_mag=13.0,
                gaia_bp_rp=1.2,
                parallax_mas=5.0,
                parallax_error_mas=0.3,
                x=20.0,
                y=20.0,
                aperture_radius=5.0,
                annulus_inner_radius=8.0,
                annulus_outer_radius=12.0,
                instrumental_mag_luminance=11.0,
                instrumental_blue_minus_red=0.7,
            ),
        ]

        zero_point_offset_mag, zero_point_source_count = _apply_photometric_calibration(rows)

        self.assertAlmostEqual(zero_point_offset_mag or 0.0, 2.0, places=6)
        self.assertEqual(zero_point_source_count, 2)
        self.assertAlmostEqual(rows[0].calibrated_mag_luminance or 0.0, 12.0, places=6)
        self.assertAlmostEqual(rows[1].calibrated_mag_luminance or 0.0, 13.0, places=6)
        self.assertAlmostEqual(rows[0].absolute_magnitude_proxy or 0.0, 7.0, places=6)
        self.assertAlmostEqual(rows[1].absolute_magnitude_proxy or 0.0, 6.49485002168, places=6)
        self.assertAlmostEqual(rows[0].gaia_absolute_magnitude or 0.0, 7.0, places=6)
        self.assertAlmostEqual(rows[1].gaia_absolute_magnitude or 0.0, 6.49485002168, places=6)
        self.assertTrue(rows[0].used_for_zero_point)
        self.assertEqual(rows[0].plot_color_index, 0.8)

    def test_apply_photometric_calibration_ignores_nan_offsets(self) -> None:
        rows = [
            HrMeasurementRow(
                source_id="1",
                source_name="1",
                catalog="gaia-dr3",
                ra_deg=0.0,
                dec_deg=0.0,
                gaia_g_mag=float("nan"),
                gaia_bp_rp=0.8,
                parallax_mas=10.0,
                parallax_error_mas=0.2,
                x=10.0,
                y=10.0,
                aperture_radius=5.0,
                annulus_inner_radius=8.0,
                annulus_outer_radius=12.0,
                instrumental_mag_luminance=10.0,
            ),
            HrMeasurementRow(
                source_id="2",
                source_name="2",
                catalog="gaia-dr3",
                ra_deg=1.0,
                dec_deg=1.0,
                gaia_g_mag=13.0,
                gaia_bp_rp=1.2,
                parallax_mas=5.0,
                parallax_error_mas=0.3,
                x=20.0,
                y=20.0,
                aperture_radius=5.0,
                annulus_inner_radius=8.0,
                annulus_outer_radius=12.0,
                instrumental_mag_luminance=11.0,
            ),
        ]

        zero_point_offset_mag, zero_point_source_count = _apply_photometric_calibration(rows)

        self.assertAlmostEqual(zero_point_offset_mag or 0.0, 2.0, places=6)
        self.assertEqual(zero_point_source_count, 1)
        self.assertFalse(rows[0].used_for_zero_point)
        self.assertTrue(rows[1].used_for_zero_point)

    def test_match_detected_sources_to_gaia_returns_only_close_unique_matches(self) -> None:
        wcs = WCS(naxis=2)
        wcs.wcs.crpix = [50.0, 50.0]
        wcs.wcs.cdelt = np.array([-0.0002777778, 0.0002777778])
        wcs.wcs.crval = [120.0, -15.0]
        wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]

        detected_sources = [
            _DetectedSource(x=50.0, y=50.0, peak=1000.0),
            _DetectedSource(x=30.0, y=55.0, peak=900.0),
            _DetectedSource(x=30.2, y=55.2, peak=850.0),
            _DetectedSource(x=5.0, y=5.0, peak=100.0),
        ]
        ra0, dec0 = wcs.pixel_to_world_values(50.0, 50.0)
        ra1, dec1 = wcs.pixel_to_world_values(30.0, 55.0)
        gaia_stars = [
            CatalogStar(
                catalog="gaia-dr3",
                source_id="A",
                name="A",
                ra_deg=float(ra0),
                dec_deg=float(dec0),
                magnitude=12.0,
                is_variable=False,
            ),
            CatalogStar(
                catalog="gaia-dr3",
                source_id="B",
                name="B",
                ra_deg=float(ra1),
                dec_deg=float(dec1),
                magnitude=13.0,
                is_variable=False,
            ),
        ]

        matches = _match_detected_sources_to_gaia(detected_sources, gaia_stars, wcs, aperture_radius=5.0)

        self.assertEqual([match.star.source_id for match in matches], ["A", "B"])
        self.assertAlmostEqual(matches[0].x, 50.0, places=2)
        self.assertAlmostEqual(matches[1].x, 30.0, places=2)

    def test_measure_hr_sources_reports_table_finalization_before_completion(self) -> None:
        settings = AppSettings.defaults(Path.cwd())
        row = HrMeasurementRow(
            source_id="1",
            source_name="Star 1",
            catalog="gaia-dr3",
            ra_deg=10.0,
            dec_deg=20.0,
            gaia_g_mag=12.3,
            gaia_bp_rp=0.7,
            parallax_mas=4.5,
            parallax_error_mas=0.2,
            x=10.0,
            y=12.0,
            aperture_radius=5.0,
            annulus_inner_radius=8.0,
            annulus_outer_radius=12.0,
            flux_luminance=1000.0,
        )
        progress_messages: list[str] = []

        with (
            patch("photometry_app.core.hr_diagram.read_header", return_value={}),
            patch("photometry_app.core.hr_diagram.read_photometry_image_data", return_value=np.zeros((8, 8), dtype=float)),
            patch("photometry_app.core.hr_diagram.WCS", return_value=object()),
            patch("photometry_app.core.hr_diagram._resolve_saturation_threshold", return_value=None),
            patch("photometry_app.core.hr_diagram._detect_hr_image_sources", return_value=[object()]),
            patch("photometry_app.core.hr_diagram._match_detected_sources_to_gaia", return_value=[object()]),
            patch("photometry_app.core.hr_diagram._measure_hr_rows", return_value=[row]),
            patch("photometry_app.core.hr_diagram._apply_photometric_calibration", return_value=(1.2, 1)),
        ):
            table = measure_hr_sources(
                Path("demo.xisf"),
                [],
                settings,
                progress_callback=progress_messages.append,
            )

        self.assertEqual(table.measured_count, 1)
        self.assertIn("[H-R 1/1] Finalizing measurements and building the H-R working table.", progress_messages)
        finalizing_index = progress_messages.index("[H-R 1/1] Finalizing measurements and building the H-R working table.")
        completion_index = progress_messages.index(
            "H-R source measurement complete: 1 row(s) measured, 1 usable, 1 zero-point source(s)."
        )
        self.assertLess(finalizing_index, completion_index)


if __name__ == "__main__":
    unittest.main()
