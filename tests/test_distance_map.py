from __future__ import annotations

import math
import unittest
from pathlib import Path

import numpy as np
from PySide6.QtWidgets import QApplication

from photometry_app.core.distance_map import (
    DISTANCE_MAP_GAIA_QUERY_ROW_FACTOR,
    DISTANCE_MAP_MAX_STAR_COUNT,
    DistanceMapStar,
    _distance_map_star_from_catalog,
    catalog_star_meets_parallax_snr,
    distance_map_depth_ruler_geometry,
    distance_map_depth_ruler_tick_values,
    distance_map_gaia_query_row_limit,
    distance_map_imaging_axes,
    distance_map_pixel_position,
    distance_map_tomography_default_depth,
    distance_map_tomography_depth_range,
    distance_map_tomography_plane_transform,
    parallax_snr,
    parallax_to_distance_pc,
    star_cartesian_pc,
)
from photometry_app.core.models import CatalogStar
from photometry_app.ui.distance_map_view import DistanceMapPanel


class DistanceMapCoreTest(unittest.TestCase):
    def test_parallax_to_distance_pc(self) -> None:
        self.assertAlmostEqual(parallax_to_distance_pc(10.0), 100.0)
        self.assertIsNone(parallax_to_distance_pc(0.0))
        self.assertIsNone(parallax_to_distance_pc(None))

    def test_distance_map_gaia_query_row_limit_scales_with_max_stars(self) -> None:
        self.assertEqual(distance_map_gaia_query_row_limit(500), 500 * DISTANCE_MAP_GAIA_QUERY_ROW_FACTOR)
        self.assertEqual(distance_map_gaia_query_row_limit(5000), 20000)
        self.assertEqual(distance_map_gaia_query_row_limit(DISTANCE_MAP_MAX_STAR_COUNT), DISTANCE_MAP_MAX_STAR_COUNT * DISTANCE_MAP_GAIA_QUERY_ROW_FACTOR)
        self.assertEqual(distance_map_gaia_query_row_limit(10), 500)

    def test_star_cartesian_pc_places_star_on_positive_x_axis(self) -> None:
        x_pc, y_pc, z_pc = star_cartesian_pc(0.0, 0.0, 100.0)
        self.assertAlmostEqual(x_pc, 100.0, places=3)
        self.assertAlmostEqual(y_pc, 0.0, places=3)
        self.assertAlmostEqual(z_pc, 0.0, places=3)

    def test_distance_map_star_filters_by_distance_limit(self) -> None:
        star = CatalogStar(
            catalog="gaia-dr3",
            source_id="1",
            name="Nearby",
            ra_deg=10.0,
            dec_deg=20.0,
            magnitude=12.0,
            is_variable=False,
            metadata={"parallax_mas": 10.0},
        )
        mapped = _distance_map_star_from_catalog(star, max_distance_pc=200.0)
        self.assertIsNotNone(mapped)
        assert mapped is not None
        self.assertAlmostEqual(mapped.distance_pc, 100.0)
        self.assertTrue(math.isfinite(mapped.x_pc))

        rejected = _distance_map_star_from_catalog(star, max_distance_pc=50.0)
        self.assertIsNone(rejected)

    def test_parallax_snr(self) -> None:
        self.assertAlmostEqual(parallax_snr(0.10, 0.02), 5.0)
        self.assertIsNone(parallax_snr(None, 0.02))
        self.assertIsNone(parallax_snr(0.10, 0.0))

    def test_catalog_star_meets_parallax_snr(self) -> None:
        good = CatalogStar(
            catalog="gaia-dr3",
            source_id="good",
            name="Good",
            ra_deg=10.0,
            dec_deg=20.0,
            magnitude=12.0,
            is_variable=False,
            metadata={"parallax_mas": 0.5, "parallax_error_mas": 0.05},
        )
        bad = CatalogStar(
            catalog="gaia-dr3",
            source_id="bad",
            name="Bad",
            ra_deg=10.0,
            dec_deg=20.0,
            magnitude=12.0,
            is_variable=False,
            metadata={"parallax_mas": 0.5, "parallax_error_mas": 0.2},
        )
        self.assertTrue(catalog_star_meets_parallax_snr(good, 5.0))
        self.assertFalse(catalog_star_meets_parallax_snr(bad, 5.0))
        self.assertTrue(catalog_star_meets_parallax_snr(bad, 0.0))

    def test_distance_map_star_rejects_low_parallax_snr(self) -> None:
        star = CatalogStar(
            catalog="gaia-dr3",
            source_id="low-snr",
            name="Low SNR",
            ra_deg=10.0,
            dec_deg=20.0,
            magnitude=12.0,
            is_variable=False,
            metadata={"parallax_mas": 0.5, "parallax_error_mas": 0.2},
        )
        self.assertIsNone(_distance_map_star_from_catalog(star, max_distance_pc=5000.0, min_parallax_snr=5.0))

    def test_distance_map_pixel_position_rejects_off_image_star(self) -> None:
        from astropy.wcs import WCS as AstropyWCS

        from photometry_app.core.models import SolvedField

        width = 100
        height = 80
        wcs = AstropyWCS(naxis=2)
        wcs.wcs.crpix = [50.5, 40.5]
        wcs.wcs.crval = [180.0, 30.0]
        wcs.wcs.cd = [[-0.0001, 0.0], [0.0, 0.0001]]
        wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
        solved_field = SolvedField(
            center_ra_deg=180.0,
            center_dec_deg=30.0,
            radius_deg=0.5,
            width=width,
            height=height,
            wcs_path=Path("unused.fits"),
        )
        inside = DistanceMapStar(
            source_id="inside",
            name="Inside",
            ra_deg=180.0,
            dec_deg=30.0,
            magnitude=10.0,
            bp_rp=None,
            parallax_mas=2.0,
            parallax_error_mas=0.05,
            pm_ra_mas_per_year=0.0,
            pm_dec_mas_per_year=0.0,
            distance_pc=500.0,
            x_pc=1.0,
            y_pc=0.0,
            z_pc=0.0,
        )
        outside = DistanceMapStar(
            source_id="outside",
            name="Outside",
            ra_deg=181.0,
            dec_deg=31.0,
            magnitude=10.0,
            bp_rp=None,
            parallax_mas=2.0,
            parallax_error_mas=0.05,
            pm_ra_mas_per_year=0.0,
            pm_dec_mas_per_year=0.0,
            distance_pc=500.0,
            x_pc=2.0,
            y_pc=0.0,
            z_pc=0.0,
        )
        self.assertIsNotNone(distance_map_pixel_position(inside, solved_field=solved_field, wcs=wcs))
        self.assertIsNone(distance_map_pixel_position(outside, solved_field=solved_field, wcs=wcs))

    def test_distance_map_star_color_uses_bp_rp_and_falls_back_to_green(self) -> None:
        from photometry_app.core.distance_map import DistanceMapStar, distance_map_star_color_rgba

        blue_star = DistanceMapStar(
            source_id="blue",
            name="Blue",
            ra_deg=0.0,
            dec_deg=0.0,
            magnitude=10.0,
            bp_rp=-0.2,
            parallax_mas=5.0,
            parallax_error_mas=0.1,
            pm_ra_mas_per_year=1.0,
            pm_dec_mas_per_year=1.0,
            distance_pc=200.0,
            x_pc=1.0,
            y_pc=0.0,
            z_pc=0.0,
        )
        red_star = DistanceMapStar(
            source_id="red",
            name="Red",
            ra_deg=0.0,
            dec_deg=0.0,
            magnitude=10.0,
            bp_rp=2.4,
            parallax_mas=5.0,
            parallax_error_mas=0.1,
            pm_ra_mas_per_year=1.0,
            pm_dec_mas_per_year=1.0,
            distance_pc=200.0,
            x_pc=2.0,
            y_pc=0.0,
            z_pc=0.0,
        )
        unknown_star = DistanceMapStar(
            source_id="unknown",
            name="Unknown",
            ra_deg=0.0,
            dec_deg=0.0,
            magnitude=10.0,
            bp_rp=None,
            parallax_mas=5.0,
            parallax_error_mas=0.1,
            pm_ra_mas_per_year=1.0,
            pm_dec_mas_per_year=1.0,
            distance_pc=200.0,
            x_pc=3.0,
            y_pc=0.0,
            z_pc=0.0,
        )

        blue_color = distance_map_star_color_rgba(blue_star)
        red_color = distance_map_star_color_rgba(red_star)
        unknown_color = distance_map_star_color_rgba(unknown_star)

        self.assertGreater(blue_color[2], blue_color[0])
        self.assertGreater(red_color[0], red_color[2])
        self.assertGreater(unknown_color[1], unknown_color[0])
        self.assertGreater(unknown_color[1], unknown_color[2])

    def test_distance_map_star_point_size_scales_with_magnitude_and_defaults(self) -> None:
        from photometry_app.core.distance_map import DistanceMapStar, distance_map_star_point_size

        bright = DistanceMapStar(
            source_id="bright",
            name="Bright",
            ra_deg=0.0,
            dec_deg=0.0,
            magnitude=8.0,
            bp_rp=0.8,
            parallax_mas=5.0,
            parallax_error_mas=0.1,
            pm_ra_mas_per_year=1.0,
            pm_dec_mas_per_year=1.0,
            distance_pc=200.0,
            x_pc=0.0,
            y_pc=0.0,
            z_pc=0.0,
        )
        faint = DistanceMapStar(
            source_id="faint",
            name="Faint",
            ra_deg=0.0,
            dec_deg=0.0,
            magnitude=16.0,
            bp_rp=0.8,
            parallax_mas=5.0,
            parallax_error_mas=0.1,
            pm_ra_mas_per_year=1.0,
            pm_dec_mas_per_year=1.0,
            distance_pc=200.0,
            x_pc=0.0,
            y_pc=0.0,
            z_pc=0.0,
        )
        unknown = DistanceMapStar(
            source_id="unknown",
            name="Unknown",
            ra_deg=0.0,
            dec_deg=0.0,
            magnitude=None,
            bp_rp=0.8,
            parallax_mas=5.0,
            parallax_error_mas=0.1,
            pm_ra_mas_per_year=1.0,
            pm_dec_mas_per_year=1.0,
            distance_pc=200.0,
            x_pc=0.0,
            y_pc=0.0,
            z_pc=0.0,
        )

        bright_size = distance_map_star_point_size(bright)
        faint_size = distance_map_star_point_size(faint)
        default_size = distance_map_star_point_size(unknown, reference_magnitude=13.0)

        self.assertGreater(bright_size, faint_size)
        self.assertAlmostEqual(default_size, 2.0)

    def test_distance_map_star_rejects_missing_parallax(self) -> None:
        star = CatalogStar(
            catalog="gaia-dr3",
            source_id="2",
            name="NoPlx",
            ra_deg=10.0,
            dec_deg=20.0,
            magnitude=12.0,
            is_variable=False,
            metadata={},
        )
        self.assertIsNone(_distance_map_star_from_catalog(star, max_distance_pc=500.0))

    def test_distance_map_imaging_axes_are_orthonormal(self) -> None:
        axes = distance_map_imaging_axes(180.0, 45.0)
        los = np.array(axes.line_of_sight)
        east = np.array(axes.east)
        north = np.array(axes.north)
        self.assertAlmostEqual(float(np.linalg.norm(los)), 1.0, places=6)
        self.assertAlmostEqual(float(np.linalg.norm(east)), 1.0, places=6)
        self.assertAlmostEqual(float(np.linalg.norm(north)), 1.0, places=6)
        self.assertAlmostEqual(float(np.dot(los, east)), 0.0, places=6)
        self.assertAlmostEqual(float(np.dot(los, north)), 0.0, places=6)
        self.assertAlmostEqual(float(np.dot(east, north)), 0.0, places=6)

    def test_distance_map_tomography_depth_range_spans_star_projections(self) -> None:
        from photometry_app.core.distance_map import DistanceMapStar

        stars = (
            DistanceMapStar(
                source_id="1",
                name="Near",
                ra_deg=0.0,
                dec_deg=0.0,
                magnitude=10.0,
                bp_rp=None,
                parallax_mas=10.0,
                parallax_error_mas=0.1,
                pm_ra_mas_per_year=0.0,
                pm_dec_mas_per_year=0.0,
                distance_pc=100.0,
                x_pc=100.0,
                y_pc=0.0,
                z_pc=0.0,
            ),
            DistanceMapStar(
                source_id="2",
                name="Far",
                ra_deg=0.0,
                dec_deg=0.0,
                magnitude=11.0,
                bp_rp=None,
                parallax_mas=5.0,
                parallax_error_mas=0.1,
                pm_ra_mas_per_year=0.0,
                pm_dec_mas_per_year=0.0,
                distance_pc=200.0,
                x_pc=200.0,
                y_pc=0.0,
                z_pc=0.0,
            ),
        )
        axes = distance_map_imaging_axes(0.0, 0.0)
        depth_min, depth_max = distance_map_tomography_depth_range(stars, axes.line_of_sight)
        default_depth = distance_map_tomography_default_depth(stars, axes.line_of_sight)
        self.assertLess(depth_min, 100.0)
        self.assertGreater(depth_max, 200.0)
        self.assertAlmostEqual(default_depth, 150.0, delta=20.0)

    def test_distance_map_tomography_plane_transform_maps_image_center_to_depth(self) -> None:
        axes = distance_map_imaging_axes(0.0, 0.0)
        matrix = distance_map_tomography_plane_transform(
            line_of_sight=axes.line_of_sight,
            east=axes.east,
            north=axes.north,
            depth_pc=150.0,
            width_pc=20.0,
            height_pc=10.0,
            texture_width=100,
            texture_height=50,
        )
        center = matrix @ np.array([50.0, 25.0, 0.0, 1.0], dtype=float)
        expected = np.array(axes.line_of_sight) * 150.0
        self.assertAlmostEqual(float(center[0]), float(expected[0]), places=3)
        self.assertAlmostEqual(float(center[1]), float(expected[1]), places=3)
        self.assertAlmostEqual(float(center[2]), float(expected[2]), places=3)

    def test_distance_map_ray_plane_point_places_center_on_depth(self) -> None:
        from photometry_app.core.distance_map import distance_map_ray_plane_point

        axes = distance_map_imaging_axes(0.0, 0.0)
        point = distance_map_ray_plane_point(0.0, 0.0, depth_pc=150.0, line_of_sight=axes.line_of_sight)
        expected = np.array(axes.line_of_sight) * 150.0
        np.testing.assert_allclose(point, expected, rtol=0.0, atol=1e-3)

    def test_distance_map_tomography_plane_maps_star_pixel_to_position(self) -> None:
        from astropy.wcs import WCS as AstropyWCS

        from photometry_app.core.distance_map import distance_map_tomography_plane_transform_from_field
        from photometry_app.core.models import SolvedField

        width = 100
        height = 80
        wcs = AstropyWCS(naxis=2)
        wcs.wcs.crpix = [50.5, 40.5]
        wcs.wcs.crval = [180.0, 30.0]
        wcs.wcs.cd = [[-0.0001, 0.0], [0.0, 0.0001]]
        wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]

        solved_field = SolvedField(
            center_ra_deg=180.0,
            center_dec_deg=30.0,
            radius_deg=0.5,
            width=width,
            height=height,
            wcs_path=Path("unused.fits"),
        )
        ra_deg = 180.05
        dec_deg = 30.02
        distance_pc = 150.0
        star_position = np.asarray(star_cartesian_pc(ra_deg, dec_deg, distance_pc), dtype=float)
        _, axes = distance_map_tomography_plane_transform_from_field(
            solved_field,
            wcs,
            depth_pc=100.0,
            texture_shape=(height, width),
        )
        depth_pc = float(np.dot(star_position, np.asarray(axes.line_of_sight, dtype=float)))

        matrix, _axes = distance_map_tomography_plane_transform_from_field(
            solved_field,
            wcs,
            depth_pc=depth_pc,
            texture_shape=(height, width),
        )
        pixel_x, pixel_y = wcs.world_to_pixel_values(ra_deg, dec_deg)
        plane_point = matrix @ np.array([float(pixel_y) + 0.5, float(pixel_x) + 0.5, 0.0, 1.0], dtype=float)
        np.testing.assert_allclose(plane_point[:3], star_position, rtol=0.0, atol=1.0)

    def test_distance_map_depth_ruler_tick_values_span_range(self) -> None:
        ticks = distance_map_depth_ruler_tick_values(120.0, 280.0, max_ticks=6)
        self.assertGreaterEqual(len(ticks), 3)
        self.assertLessEqual(ticks[0], 120.0)
        self.assertGreaterEqual(ticks[-1], 280.0)

    def test_distance_map_depth_ruler_geometry_follows_line_of_sight(self) -> None:
        axes = distance_map_imaging_axes(180.0, 30.0)
        los = np.asarray(axes.line_of_sight, dtype=float)
        near_position = los * 100.0
        far_position = los * 200.0
        stars = (
            DistanceMapStar(
                source_id="near",
                name="Near",
                ra_deg=180.0,
                dec_deg=30.0,
                magnitude=10.0,
                bp_rp=None,
                parallax_mas=10.0,
                parallax_error_mas=0.1,
                pm_ra_mas_per_year=0.0,
                pm_dec_mas_per_year=0.0,
                distance_pc=100.0,
                x_pc=float(near_position[0]),
                y_pc=float(near_position[1]),
                z_pc=float(near_position[2]),
            ),
            DistanceMapStar(
                source_id="far",
                name="Far",
                ra_deg=180.0,
                dec_deg=30.0,
                magnitude=11.0,
                bp_rp=None,
                parallax_mas=5.0,
                parallax_error_mas=0.1,
                pm_ra_mas_per_year=0.0,
                pm_dec_mas_per_year=0.0,
                distance_pc=200.0,
                x_pc=float(far_position[0]),
                y_pc=float(far_position[1]),
                z_pc=float(far_position[2]),
            ),
        )
        geometry = distance_map_depth_ruler_geometry(stars, axes)
        self.assertIsNotNone(geometry)
        assert geometry is not None
        axis_vector = geometry.axis_points[1] - geometry.axis_points[0]
        axis_norm = float(np.linalg.norm(axis_vector))
        self.assertGreater(axis_norm, 1.0)
        axis_direction = axis_vector / axis_norm
        np.testing.assert_allclose(axis_direction, los, atol=0.05)
        self.assertGreaterEqual(len(geometry.tick_labels), 2)


class DistanceMapPanelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_source_image_path_keeps_full_path(self) -> None:
        panel = DistanceMapPanel()
        source_path = Path("C:/data/night1/field.fits")
        panel.set_source_image_path(source_path)
        self.assertEqual(panel.source_image_path(), source_path)

    def test_save_button_is_next_to_reset_view(self) -> None:
        panel = DistanceMapPanel()
        self.assertEqual(panel._save_button.text(), "Save")  # noqa: SLF001
        self.assertEqual(panel._reset_view_button.text(), "Reset View")  # noqa: SLF001
        self.assertEqual(panel._default_save_path().name, "distance_map.png")  # noqa: SLF001
        panel.set_source_image_path(Path("C:/data/night1/field.fits"))
        self.assertEqual(panel._default_save_path().name, "field_distance_map.png")  # noqa: SLF001


if __name__ == "__main__":
    unittest.main()
