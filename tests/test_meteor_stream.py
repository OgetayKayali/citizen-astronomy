from __future__ import annotations

from datetime import UTC, datetime
import math
import unittest

import numpy as np

from photometry_app.core.meteor_stream import (
    build_meteor_stream_overlay,
    build_meteor_stream_overlay_from_samples,
    format_meteor_stream_label,
    keplerian_elements_from_state,
    match_meteor_shower_parents,
    normalize_body_identity,
    position_from_keplerian,
    primary_meteor_shower_for_identities,
)
from photometry_app.core.solar_system import SolarSystemStateVectorSample


def _circular_equatorial_state(*, radius_au: float = 1.0, mu: float = 0.00029591220828559115):
    speed = math.sqrt(mu / radius_au)
    return {
        "x_au": radius_au,
        "y_au": 0.0,
        "z_au": 0.0,
        "vx_au_per_day": 0.0,
        "vy_au_per_day": speed,
        "vz_au_per_day": 0.0,
    }


class MeteorStreamModelTest(unittest.TestCase):
    def test_normalize_body_identity_strips_punctuation(self) -> None:
        self.assertEqual(normalize_body_identity("109P/Swift-Tuttle"), "109pswifttuttle")

    def test_match_perseids_parent_aliases(self) -> None:
        showers = match_meteor_shower_parents("109P/Swift-Tuttle")
        self.assertEqual(len(showers), 1)
        self.assertEqual(showers[0].shower_name, "Perseids")
        self.assertEqual(primary_meteor_shower_for_identities("Swift-Tuttle").iau_code, "PER")

    def test_halley_matches_both_orionids_and_eta_aquariids(self) -> None:
        showers = match_meteor_shower_parents("1P/Halley")
        names = {shower.shower_name for shower in showers}
        self.assertEqual(names, {"Orionids", "η Aquariids"})
        self.assertEqual(format_meteor_stream_label(showers), "Orionids / η Aquariids stream")

    def test_tuttle_does_not_false_match_swift_tuttle(self) -> None:
        ursids = match_meteor_shower_parents("8P/Tuttle")
        self.assertEqual([shower.iau_code for shower in ursids], ["URS"])
        # Bare "Tuttle" must not claim Perseids via Swift-Tuttle.
        bare = match_meteor_shower_parents("Tuttle")
        self.assertEqual(bare, ())

    def test_one_p_does_not_match_one_oh_nine_p(self) -> None:
        showers = match_meteor_shower_parents("109P")
        self.assertEqual([shower.iau_code for shower in showers], ["PER"])

    def test_circular_orbit_elements_and_positions(self) -> None:
        state = _circular_equatorial_state(radius_au=1.0)
        elements = keplerian_elements_from_state(**state)
        self.assertIsNotNone(elements)
        assert elements is not None
        self.assertAlmostEqual(elements.semi_major_axis_au, 1.0, places=5)
        self.assertLess(elements.eccentricity, 1.0e-6)
        self.assertAlmostEqual(elements.period_days, 365.25, delta=0.2)

        peri = position_from_keplerian(elements, 0.0)
        apo = position_from_keplerian(elements, math.pi)
        self.assertAlmostEqual(float(np.linalg.norm(peri)), 1.0, places=5)
        self.assertAlmostEqual(float(np.linalg.norm(apo)), 1.0, places=5)

    def test_eccentric_orbit_recovers_perihelion_and_aphelion(self) -> None:
        # Place a body at perihelion of a=2 AU, e=0.5 => q=1 AU.
        a_au = 2.0
        eccentricity = 0.5
        q_au = a_au * (1.0 - eccentricity)
        mu = 0.00029591220828559115
        speed = math.sqrt(mu * (2.0 / q_au - 1.0 / a_au))
        elements = keplerian_elements_from_state(
            x_au=q_au,
            y_au=0.0,
            z_au=0.0,
            vx_au_per_day=0.0,
            vy_au_per_day=speed,
            vz_au_per_day=0.0,
        )
        self.assertIsNotNone(elements)
        assert elements is not None
        self.assertAlmostEqual(elements.semi_major_axis_au, a_au, places=4)
        self.assertAlmostEqual(elements.eccentricity, eccentricity, places=4)

        peri = position_from_keplerian(elements, 0.0)
        apo = position_from_keplerian(elements, math.pi)
        self.assertAlmostEqual(float(np.linalg.norm(peri)), q_au, places=4)
        self.assertAlmostEqual(float(np.linalg.norm(apo)), a_au * (1.0 + eccentricity), places=4)

    def test_hyperbolic_state_returns_none(self) -> None:
        elements = keplerian_elements_from_state(
            x_au=1.0,
            y_au=0.0,
            z_au=0.0,
            vx_au_per_day=0.0,
            vy_au_per_day=0.05,  # far above circular escape for 1 AU
            vz_au_per_day=0.0,
        )
        self.assertIsNone(elements)

    def test_stream_overlay_highlights_earth_crossing(self) -> None:
        state = _circular_equatorial_state(radius_au=1.0)
        earth = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [-1.0, 0.0, 0.0], [0.0, -1.0, 0.0]], dtype=float)
        showers = match_meteor_shower_parents("109P")
        overlay = build_meteor_stream_overlay(
            shower=showers[0],
            state_x_au=state["x_au"],
            state_y_au=state["y_au"],
            state_z_au=state["z_au"],
            state_vx_au_per_day=state["vx_au_per_day"],
            state_vy_au_per_day=state["vy_au_per_day"],
            state_vz_au_per_day=state["vz_au_per_day"],
            earth_positions_au=earth,
            particle_count=400,
            tube_radius_au=0.02,
            near_earth_threshold_au=0.08,
        )
        self.assertIsNotNone(overlay)
        assert overlay is not None
        self.assertEqual(overlay.particle_positions_au.shape[1], 3)
        self.assertTrue(bool(np.any(overlay.near_earth_mask)))
        self.assertLess(overlay.closest_approach_au, 0.08)
        self.assertEqual(overlay.orbit_polyline_au.shape[1], 3)
        self.assertTrue(np.allclose(overlay.orbit_polyline_au[0], overlay.orbit_polyline_au[-1]))

    def test_high_eccentricity_stream_resolves_earth_node(self) -> None:
        # Swift-Tuttle-like: descending node near 1 AU must be recovered for Perseids.
        mu = 0.00029591220828559115
        a_au, eccentricity = 26.3, 0.963
        inclination = math.radians(113.45)
        node = math.radians(139.4)
        arg_peri = math.radians(153.0)
        q_au = a_au * (1.0 - eccentricity)
        speed = math.sqrt(mu * (2.0 / q_au - 1.0 / a_au))
        # State at perihelion in the perifocal frame, rotated into ecliptic.
        from photometry_app.core.meteor_stream import KeplerianElements, position_from_true_anomaly

        elements = KeplerianElements(
            semi_major_axis_au=a_au,
            eccentricity=eccentricity,
            inclination_rad=inclination,
            longitude_of_ascending_node_rad=node,
            argument_of_periapsis_rad=arg_peri,
            mean_anomaly_rad=0.0,
            period_days=2.0 * math.pi * math.sqrt((a_au**3) / mu),
        )
        peri = position_from_true_anomaly(elements, 0.0)
        # Velocity at perihelion is perpendicular to radius in the orbital plane.
        # Build via a tiny true-anomaly step.
        nearby = position_from_true_anomaly(elements, math.radians(0.05))
        tangent = nearby - peri
        tangent /= np.linalg.norm(tangent)
        velocity = tangent * speed

        earth = np.array(
            [[math.cos(math.radians(deg)), math.sin(math.radians(deg)), 0.0] for deg in range(0, 360, 2)],
            dtype=float,
        )
        overlay = build_meteor_stream_overlay(
            shower=match_meteor_shower_parents("109P")[0],
            state_x_au=float(peri[0]),
            state_y_au=float(peri[1]),
            state_z_au=float(peri[2]),
            state_vx_au_per_day=float(velocity[0]),
            state_vy_au_per_day=float(velocity[1]),
            state_vz_au_per_day=float(velocity[2]),
            earth_positions_au=earth,
            particle_count=800,
        )
        self.assertIsNotNone(overlay)
        assert overlay is not None
        self.assertLess(overlay.closest_approach_au, 0.08)
        self.assertIsNotNone(overlay.crossing_position_au)
        crossing_radius = float(np.linalg.norm(np.asarray(overlay.crossing_position_au, dtype=float)))
        self.assertGreater(crossing_radius, 0.7)
        self.assertLess(crossing_radius, 1.4)
        self.assertTrue(bool(np.any(overlay.near_earth_mask)))

    def test_true_anomaly_sampling_keeps_perihelion_populated(self) -> None:
        from photometry_app.core.meteor_stream import KeplerianElements, sample_closed_orbit_polyline

        elements = KeplerianElements(
            semi_major_axis_au=26.3,
            eccentricity=0.963,
            inclination_rad=math.radians(113.45),
            longitude_of_ascending_node_rad=math.radians(139.4),
            argument_of_periapsis_rad=math.radians(153.0),
            mean_anomaly_rad=0.0,
            period_days=50000.0,
        )
        points = sample_closed_orbit_polyline(elements, sample_count=720)
        radii = np.linalg.norm(points[:-1], axis=1)
        self.assertLess(float(np.min(radii)), 1.2)
        self.assertGreater(float(np.max(radii)), 40.0)

    def test_even_path_length_avoids_aphelion_clump(self) -> None:
        from photometry_app.core.meteor_stream import (
            KeplerianElements,
            build_orbit_soft_glow_mesh,
            effective_meteor_stream_tube_radius_au,
            resample_closed_polyline_even_arc_length,
            sample_closed_orbit_polyline,
            sample_positions_even_along_orbit,
        )

        elements = KeplerianElements(
            semi_major_axis_au=26.3,
            eccentricity=0.963,
            inclination_rad=math.radians(113.45),
            longitude_of_ascending_node_rad=math.radians(139.4),
            argument_of_periapsis_rad=math.radians(153.0),
            mean_anomaly_rad=0.0,
            period_days=50000.0,
        )
        orbit = sample_closed_orbit_polyline(elements, sample_count=720)
        rng = np.random.default_rng(7)
        positions, fractions, _offsets = sample_positions_even_along_orbit(
            orbit,
            count=400,
            tube_radius_au=0.0,
            rng=rng,
        )
        self.assertEqual(len(positions), 400)
        self.assertTrue(np.all(fractions >= 0.0))
        self.assertTrue(np.all(fractions < 1.0))
        # Neighbor spacing along the path should stay within a small factor (no tip pile-up / gap).
        ordered = np.sort(fractions)
        gaps = np.diff(np.concatenate([ordered, ordered[:1] + 1.0]))
        self.assertLess(float(np.max(gaps) / np.min(gaps)), 3.0)
        self.assertLess(float(np.std(gaps) / np.mean(gaps)), 0.25)

        even = resample_closed_polyline_even_arc_length(orbit, sample_count=240)
        even_body = even[:-1]
        even_segments = np.linalg.norm(np.roll(even_body, -1, axis=0) - even_body, axis=1)
        self.assertLess(float(np.max(even_segments) / np.min(even_segments)), 1.05)

        tube_radius = effective_meteor_stream_tube_radius_au(orbit, thickness_scale=1.0)
        self.assertGreater(tube_radius, 0.1)
        vertexes, faces, colors = build_orbit_soft_glow_mesh(
            orbit,
            radius_au=tube_radius,
            rgb=(0.3, 0.8, 0.9),
            path_samples=120,
        )
        self.assertGreater(len(vertexes), 100)
        self.assertGreater(len(faces), 100)
        self.assertEqual(len(colors), len(vertexes))
        # Brightest near the centerline; outer ribbon edges are transparent.
        self.assertGreater(float(np.max(colors[:, 3])), 0.2)
        self.assertLess(float(np.min(colors[:, 3])), 0.02)

        overlay = build_meteor_stream_overlay(
            shower=match_meteor_shower_parents("109P")[0],
            state_x_au=1.0,
            state_y_au=0.0,
            state_z_au=0.0,
            state_vx_au_per_day=0.0,
            state_vy_au_per_day=math.sqrt(0.00029591220828559115),
            state_vz_au_per_day=0.0,
            earth_positions_au=np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=float),
            particle_count=200,
        )
        self.assertIsNotNone(overlay)
        assert overlay is not None
        self.assertIsNotNone(overlay.particle_path_fractions)
        assert overlay.particle_path_fractions is not None
        self.assertEqual(len(overlay.particle_path_fractions), len(overlay.particle_positions_au))

    def test_density_spin_range_scales_particle_counts(self) -> None:
        from photometry_app.ui.dialogs import (
            _KNOWN_OBJECT_3D_METEOR_STREAM_DENSITY_DEFAULT,
            _KNOWN_OBJECT_3D_METEOR_STREAM_DENSITY_MAX,
            _KNOWN_OBJECT_3D_METEOR_STREAM_DENSITY_MIN,
        )

        self.assertLess(_KNOWN_OBJECT_3D_METEOR_STREAM_DENSITY_MIN, _KNOWN_OBJECT_3D_METEOR_STREAM_DENSITY_DEFAULT)
        self.assertLess(_KNOWN_OBJECT_3D_METEOR_STREAM_DENSITY_DEFAULT, _KNOWN_OBJECT_3D_METEOR_STREAM_DENSITY_MAX)
        sparse = build_meteor_stream_overlay(
            shower=match_meteor_shower_parents("109P")[0],
            earth_positions_au=np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=float),
            particle_count=_KNOWN_OBJECT_3D_METEOR_STREAM_DENSITY_MIN,
            **{f"state_{axis}": value for axis, value in {
                "x_au": 1.0,
                "y_au": 0.0,
                "z_au": 0.0,
                "vx_au_per_day": 0.0,
                "vy_au_per_day": math.sqrt(0.00029591220828559115),
                "vz_au_per_day": 0.0,
            }.items()},
        )
        dense = build_meteor_stream_overlay(
            shower=match_meteor_shower_parents("109P")[0],
            earth_positions_au=np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=float),
            particle_count=min(2000, _KNOWN_OBJECT_3D_METEOR_STREAM_DENSITY_MAX),
            state_x_au=1.0,
            state_y_au=0.0,
            state_z_au=0.0,
            state_vx_au_per_day=0.0,
            state_vy_au_per_day=math.sqrt(0.00029591220828559115),
            state_vz_au_per_day=0.0,
        )
        self.assertIsNotNone(sparse)
        self.assertIsNotNone(dense)
        assert sparse is not None and dense is not None
        self.assertLess(len(sparse.particle_positions_au), len(dense.particle_positions_au))

    def test_build_from_samples_prefers_finite_velocity_sample(self) -> None:
        now = datetime(2024, 8, 12, tzinfo=UTC)
        samples = (
            SolarSystemStateVectorSample(
                observation_time=now,
                x_au=1.0,
                y_au=0.0,
                z_au=0.0,
                vx_au_per_day=0.0,
                vy_au_per_day=math.sqrt(0.00029591220828559115),
                vz_au_per_day=0.0,
            ),
        )
        earth = (
            SolarSystemStateVectorSample(
                observation_time=now,
                x_au=0.0,
                y_au=1.0,
                z_au=0.0,
                vx_au_per_day=-math.sqrt(0.00029591220828559115),
                vy_au_per_day=0.0,
                vz_au_per_day=0.0,
            ),
        )
        overlay = build_meteor_stream_overlay_from_samples(
            shower=match_meteor_shower_parents("109P")[0],
            object_samples=samples,
            earth_samples=earth,
            particle_count=200,
        )
        self.assertIsNotNone(overlay)
        assert overlay is not None
        self.assertGreater(overlay.semi_major_axis_au, 0.5)


if __name__ == "__main__":
    unittest.main()
