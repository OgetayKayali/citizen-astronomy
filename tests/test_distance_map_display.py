from __future__ import annotations

import math
import unittest

import numpy as np

from photometry_app.core.distance_map import DistanceMapStar, star_cartesian_pc
from photometry_app.core.distance_map_display import (
    DistanceMapDisplayOptions,
    build_parallax_uncertainty_segments,
    catalog_angular_radius_deg,
    catalog_visible_radius_pc,
    cluster_mean_distance_pc,
    find_globular_cluster_catalog_match,
    find_globular_cluster_catalog_match_for_field,
    globular_cluster_sphere_wireframe,
    load_globular_cluster_catalog,
    parallax_distance_interval_pc,
    prepare_distance_map_display,
    resolve_display_member_indices,
    toy_globular_cluster_member_distance_pc,
)


def _star(
    index: int,
    *,
    ra_deg: float = 0.0,
    dec_deg: float = 0.0,
    parallax_mas: float = 2.0,
    parallax_error_mas: float = 0.1,
    distance_pc: float | None = None,
    pm_ra: float = 1.0,
    pm_dec: float = -1.0,
) -> DistanceMapStar:
    if distance_pc is None:
        distance_pc = 1000.0 / parallax_mas
    x_pc, y_pc, z_pc = star_cartesian_pc(ra_deg, dec_deg, distance_pc)
    return DistanceMapStar(
        source_id=str(index),
        name=f"Star {index}",
        ra_deg=ra_deg,
        dec_deg=dec_deg,
        magnitude=12.0,
        bp_rp=0.8,
        parallax_mas=parallax_mas,
        parallax_error_mas=parallax_error_mas,
        pm_ra_mas_per_year=pm_ra,
        pm_dec_mas_per_year=pm_dec,
        distance_pc=distance_pc,
        x_pc=x_pc,
        y_pc=y_pc,
        z_pc=z_pc,
    )


def _moving_group_stars(count: int = 8) -> tuple[DistanceMapStar, ...]:
    return tuple(
        _star(
            index,
            ra_deg=245.90 + (index * 0.002),
            dec_deg=-26.52 + (index * 0.001),
            parallax_mas=0.45 + (index * 0.01),
            parallax_error_mas=0.08,
            distance_pc=2200.0 + (index * 400.0),
            pm_ra=10.0 + (index * 0.05),
            pm_dec=-4.5 + (index * 0.04),
        )
        for index in range(count)
    )


class DistanceMapDisplayTest(unittest.TestCase):
    def test_cluster_mean_distance_pc_uses_inverse_variance_parallax(self) -> None:
        members = [
            _star(0, parallax_mas=2.0, parallax_error_mas=0.1),
            _star(1, parallax_mas=2.2, parallax_error_mas=0.2),
        ]
        mean_distance = cluster_mean_distance_pc(members)
        self.assertIsNotNone(mean_distance)
        assert mean_distance is not None
        self.assertAlmostEqual(mean_distance, 1000.0 / ((2.0 / 0.01 + 2.2 / 0.04) / (1.0 / 0.01 + 1.0 / 0.04)), places=3)

    def test_prepare_distance_map_display_models_cluster_depth(self) -> None:
        stars = (
            *_moving_group_stars(6),
            _star(100, ra_deg=10.0, dec_deg=10.0, parallax_mas=5.0, parallax_error_mas=0.1, distance_pc=200.0, pm_ra=-8.0, pm_dec=6.0),
        )
        result = prepare_distance_map_display(
            stars,
            member_indices=frozenset({0, 1, 2, 3, 4, 5}),
            options=DistanceMapDisplayOptions(model_cluster_depth=True),
            field_center_ra_deg=245.9,
            field_center_dec_deg=-26.5,
            field_radius_deg=1.0,
        )
        self.assertIsNotNone(result.cluster_distance_pc)
        assert result.cluster_distance_pc is not None
        member_distances = [result.display_stars[index].distance_pc for index in range(6)]
        self.assertGreater(max(member_distances) - min(member_distances), 1.0)
        self.assertAlmostEqual(result.display_stars[6].distance_pc, 200.0, places=6)

    def test_resolve_display_member_indices_ignores_catalog_sky_cone(self) -> None:
        m4 = next(entry for entry in load_globular_cluster_catalog() if entry.name == "M4")
        group = _moving_group_stars(6)
        background = tuple(
            _star(
                100 + index,
                ra_deg=m4.ra_deg + (index * 0.15),
                dec_deg=m4.dec_deg,
                parallax_mas=0.2,
                parallax_error_mas=0.05,
                distance_pc=5000.0,
                pm_ra=-20.0,
                pm_dec=12.0,
            )
            for index in range(20)
        )
        stars = group + background
        members, auto_detected = resolve_display_member_indices(
            stars,
            frozenset(),
            DistanceMapDisplayOptions(model_cluster_depth=True, use_external_cluster_catalog=True),
            catalog_entry=m4,
        )
        self.assertTrue(auto_detected)
        self.assertLessEqual(len(members), 8)
        self.assertGreaterEqual(len(members), 5)

    def test_toy_globular_cluster_member_distance_pc_stays_within_cluster_envelope(self) -> None:
        m4 = next(entry for entry in load_globular_cluster_catalog() if entry.name == "M4")
        center_star = _star(0, ra_deg=m4.ra_deg, dec_deg=m4.dec_deg, distance_pc=m4.distance_pc)
        visual_radius_pc = catalog_visible_radius_pc(m4)
        edge_star = _star(1, ra_deg=m4.ra_deg + 0.1, dec_deg=m4.dec_deg, distance_pc=m4.distance_pc + 800.0)
        center_distance = toy_globular_cluster_member_distance_pc(
            center_star,
            cluster_distance_pc=m4.distance_pc,
            cluster_radius_pc=visual_radius_pc,
            cluster_center_ra_deg=m4.ra_deg,
            cluster_center_dec_deg=m4.dec_deg,
        )
        edge_distance = toy_globular_cluster_member_distance_pc(
            edge_star,
            cluster_distance_pc=m4.distance_pc,
            cluster_radius_pc=visual_radius_pc,
            cluster_center_ra_deg=m4.ra_deg,
            cluster_center_dec_deg=m4.dec_deg,
        )
        self.assertLess(abs(center_distance - m4.distance_pc), visual_radius_pc)
        self.assertLess(abs(edge_distance - m4.distance_pc), visual_radius_pc)
        self.assertNotAlmostEqual(center_distance, edge_distance, places=2)

    def test_find_globular_cluster_catalog_match_finds_m4(self) -> None:
        entry = find_globular_cluster_catalog_match(245.897, -26.526)
        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertEqual(entry.name, "M4")

    def test_find_globular_cluster_catalog_match_for_field_uses_footprint(self) -> None:
        m4 = next(entry for entry in load_globular_cluster_catalog() if entry.name == "M4")
        angular_radius = catalog_angular_radius_deg(m4)
        field_center_ra = m4.ra_deg + (angular_radius + 1.0)
        entry = find_globular_cluster_catalog_match_for_field(
            field_center_ra,
            m4.dec_deg,
            field_radius_deg=2.5,
            stars=[],
        )
        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertEqual(entry.name, "M4")

    def test_find_globular_cluster_catalog_match_for_field_uses_star_density(self) -> None:
        stars = _moving_group_stars(8)
        entry = find_globular_cluster_catalog_match_for_field(
            10.0,
            10.0,
            field_radius_deg=0.5,
            stars=stars,
        )
        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertEqual(entry.name, "M4")

    def test_prepare_distance_map_display_auto_detects_moving_group_members(self) -> None:
        stars = _moving_group_stars(8)
        result = prepare_distance_map_display(
            stars,
            member_indices=frozenset(),
            options=DistanceMapDisplayOptions(
                model_cluster_depth=True,
                use_external_cluster_catalog=True,
            ),
            field_center_ra_deg=245.897,
            field_center_dec_deg=-26.526,
            field_radius_deg=1.0,
        )
        self.assertTrue(result.auto_detected_members)
        self.assertGreaterEqual(len(result.effective_member_indices), 5)
        self.assertLessEqual(len(result.effective_member_indices), 8)
        self.assertIsNotNone(result.catalog_entry)
        assert result.catalog_entry is not None
        self.assertEqual(result.catalog_entry.name, "M4")
        visual_radius_pc = catalog_visible_radius_pc(result.catalog_entry)
        for index in result.effective_member_indices:
            self.assertLess(abs(result.display_stars[index].distance_pc - result.catalog_entry.distance_pc), visual_radius_pc + 1.0)
        member_distances = [result.display_stars[index].distance_pc for index in result.effective_member_indices]
        self.assertGreater(max(member_distances) - min(member_distances), 1.0)

    def test_resolve_display_member_indices_prefers_manual_selection(self) -> None:
        stars = (_star(0), _star(1))
        members, auto_detected = resolve_display_member_indices(
            stars,
            frozenset({1}),
            DistanceMapDisplayOptions(model_cluster_depth=True),
            catalog_entry=None,
        )
        self.assertEqual(members, frozenset({1}))
        self.assertFalse(auto_detected)

    def test_prepare_distance_map_display_can_render_catalog_sphere(self) -> None:
        stars = (_star(0, ra_deg=245.90, dec_deg=-26.52),)
        result = prepare_distance_map_display(
            stars,
            member_indices=frozenset(),
            options=DistanceMapDisplayOptions(use_external_cluster_catalog=True),
            field_center_ra_deg=245.897,
            field_center_dec_deg=-26.526,
            field_radius_deg=1.0,
        )
        self.assertIsNotNone(result.catalog_entry)
        self.assertIsNotNone(result.sphere_wireframe)
        assert result.sphere_wireframe is not None
        self.assertEqual(len(result.sphere_wireframe), 3)
        visual_radius_pc = catalog_visible_radius_pc(result.catalog_entry)
        for circle in result.sphere_wireframe:
            center = np.asarray(star_cartesian_pc(result.catalog_entry.ra_deg, result.catalog_entry.dec_deg, result.catalog_entry.distance_pc))
            shell_delta = np.abs(np.linalg.norm(circle - center, axis=1) - visual_radius_pc)
            self.assertLess(float(np.max(shell_delta)), 0.1)

    def test_parallax_distance_interval_pc_orders_near_and_far(self) -> None:
        interval = parallax_distance_interval_pc(0.5, 0.1)
        self.assertIsNotNone(interval)
        assert interval is not None
        near_pc, far_pc = interval
        self.assertLess(near_pc, far_pc)

    def test_build_parallax_uncertainty_segments_returns_line_pairs(self) -> None:
        stars = (_star(0, parallax_mas=0.5, parallax_error_mas=0.1),)
        segments = build_parallax_uncertainty_segments(stars, member_indices=frozenset({0}), visible_indices=[0])
        self.assertIsNotNone(segments)
        assert segments is not None
        self.assertEqual(segments.shape, (2, 3))
        separation = float(np.linalg.norm(segments[0] - segments[1]))
        self.assertGreater(separation, 0.0)

    def test_globular_cluster_sphere_wireframe_points_sit_near_shell_radius(self) -> None:
        circles = globular_cluster_sphere_wireframe(245.897, -26.526, 2200.0, 35.0, segments=16)
        self.assertEqual(len(circles), 3)
        for circle in circles:
            distances = np.linalg.norm(circle, axis=1)
            shell_delta = np.abs(distances - 2200.0)
            self.assertTrue(float(np.max(shell_delta)) < 40.0)


if __name__ == "__main__":
    unittest.main()
