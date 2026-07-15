from __future__ import annotations

import importlib.util
import unittest

from photometry_app.core.hr_diagram import HrMeasurementRow
from photometry_app.core.hr_motion_groups import HrMotionGroupSettings, find_common_motion_group


class HrMotionGroupTest(unittest.TestCase):
    def test_motion_group_settings_default_preset_normalizes_to_recommended_profile(self) -> None:
        settings = HrMotionGroupSettings().normalized()

        self.assertEqual(settings.preset, "default")
        self.assertEqual(settings.method, "auto")
        self.assertEqual(settings.strictness, 1.0)
        self.assertEqual(settings.parallax_mode, "auto")
        self.assertTrue(settings.refine_hr_consistency)
        self.assertFalse(settings.auto_filter)

    def test_find_common_motion_group_prefers_dense_cluster_with_parallax(self) -> None:
        rows = [
            HrMeasurementRow(
                source_id=str(index),
                source_name=f"Cluster {index}",
                catalog="gaia-dr3",
                ra_deg=0.0,
                dec_deg=0.0,
                gaia_g_mag=12.0,
                gaia_bp_rp=0.8,
                parallax_mas=2.0 + (index * 0.03),
                parallax_error_mas=0.1,
                pm_ra_mas_per_year=10.0 + (index * 0.05),
                pm_dec_mas_per_year=-4.5 + (index * 0.04),
                x=float(index),
                y=float(index),
                aperture_radius=5.0,
                annulus_inner_radius=8.0,
                annulus_outer_radius=12.0,
                gaia_absolute_magnitude=5.0,
            )
            for index in range(6)
        ]
        rows.extend(
            [
                HrMeasurementRow(
                    source_id="field-a",
                    source_name="Field A",
                    catalog="gaia-dr3",
                    ra_deg=0.0,
                    dec_deg=0.0,
                    gaia_g_mag=13.0,
                    gaia_bp_rp=1.1,
                    parallax_mas=8.0,
                    parallax_error_mas=0.2,
                    pm_ra_mas_per_year=-14.0,
                    pm_dec_mas_per_year=6.5,
                    x=20.0,
                    y=20.0,
                    aperture_radius=5.0,
                    annulus_inner_radius=8.0,
                    annulus_outer_radius=12.0,
                    gaia_absolute_magnitude=6.5,
                ),
                HrMeasurementRow(
                    source_id="field-b",
                    source_name="Field B",
                    catalog="gaia-dr3",
                    ra_deg=0.0,
                    dec_deg=0.0,
                    gaia_g_mag=13.5,
                    gaia_bp_rp=1.3,
                    parallax_mas=0.4,
                    parallax_error_mas=0.2,
                    pm_ra_mas_per_year=24.0,
                    pm_dec_mas_per_year=15.0,
                    x=21.0,
                    y=21.0,
                    aperture_radius=5.0,
                    annulus_inner_radius=8.0,
                    annulus_outer_radius=12.0,
                    gaia_absolute_magnitude=7.0,
                ),
            ]
        )

        result = find_common_motion_group(rows)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.used_parallax)
        self.assertEqual(result.strictness, 1.0)
        self.assertEqual(result.member_count, 6)
        self.assertEqual(result.astrometric_member_count, 6)
        self.assertEqual(result.hr_refined_member_count, 6)
        self.assertFalse(result.used_hr_refinement)
        self.assertEqual(result.clustering_method, "lightweight")
        self.assertEqual(result.member_indices, [0, 1, 2, 3, 4, 5])

    def test_find_common_motion_group_can_fall_back_to_proper_motion_only(self) -> None:
        rows = [
            HrMeasurementRow(
                source_id=str(index),
                source_name=f"Cluster {index}",
                catalog="gaia-dr3",
                ra_deg=0.0,
                dec_deg=0.0,
                gaia_g_mag=12.0,
                gaia_bp_rp=0.8,
                parallax_mas=None,
                parallax_error_mas=None,
                pm_ra_mas_per_year=5.0 + (index * 0.04),
                pm_dec_mas_per_year=2.0 + (index * 0.03),
                x=float(index),
                y=float(index),
                aperture_radius=5.0,
                annulus_inner_radius=8.0,
                annulus_outer_radius=12.0,
                gaia_absolute_magnitude=5.0,
            )
            for index in range(5)
        ]
        rows.append(
            HrMeasurementRow(
                source_id="field",
                source_name="Field",
                catalog="gaia-dr3",
                ra_deg=0.0,
                dec_deg=0.0,
                gaia_g_mag=13.0,
                gaia_bp_rp=1.0,
                parallax_mas=None,
                parallax_error_mas=None,
                pm_ra_mas_per_year=-10.0,
                pm_dec_mas_per_year=12.0,
                x=30.0,
                y=30.0,
                aperture_radius=5.0,
                annulus_inner_radius=8.0,
                annulus_outer_radius=12.0,
                gaia_absolute_magnitude=6.0,
            )
        )

        result = find_common_motion_group(rows)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result.used_parallax)
        self.assertEqual(result.member_count, 5)

    def test_find_common_motion_group_can_force_proper_motion_only_even_when_parallax_exists(self) -> None:
        rows = [
            HrMeasurementRow(
                source_id=str(index),
                source_name=f"Cluster {index}",
                catalog="gaia-dr3",
                ra_deg=0.0,
                dec_deg=0.0,
                gaia_g_mag=12.0,
                gaia_bp_rp=0.8,
                parallax_mas=2.0 + (index * 0.03),
                parallax_error_mas=0.1,
                pm_ra_mas_per_year=5.0 + (index * 0.04),
                pm_dec_mas_per_year=2.0 + (index * 0.03),
                x=float(index),
                y=float(index),
                aperture_radius=5.0,
                annulus_inner_radius=8.0,
                annulus_outer_radius=12.0,
                gaia_absolute_magnitude=5.0,
            )
            for index in range(5)
        ]
        rows.append(
            HrMeasurementRow(
                source_id="field",
                source_name="Field",
                catalog="gaia-dr3",
                ra_deg=0.0,
                dec_deg=0.0,
                gaia_g_mag=13.0,
                gaia_bp_rp=1.0,
                parallax_mas=9.0,
                parallax_error_mas=0.2,
                pm_ra_mas_per_year=-10.0,
                pm_dec_mas_per_year=12.0,
                x=30.0,
                y=30.0,
                aperture_radius=5.0,
                annulus_inner_radius=8.0,
                annulus_outer_radius=12.0,
                gaia_absolute_magnitude=6.0,
            )
        )

        result = find_common_motion_group(rows, parallax_mode="never")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result.used_parallax)
        self.assertEqual(result.member_count, 5)

    def test_find_common_motion_group_can_refine_out_hr_inconsistent_member(self) -> None:
        rows = [
            HrMeasurementRow(
                source_id=str(index),
                source_name=f"Cluster {index}",
                catalog="gaia-dr3",
                ra_deg=0.0,
                dec_deg=0.0,
                gaia_g_mag=12.0 + (index * 0.05),
                gaia_bp_rp=0.82 + (index * 0.015),
                parallax_mas=2.0 + (index * 0.02),
                parallax_error_mas=0.1,
                pm_ra_mas_per_year=9.8 + (index * 0.04),
                pm_dec_mas_per_year=-4.7 + (index * 0.03),
                x=float(index),
                y=float(index),
                aperture_radius=5.0,
                annulus_inner_radius=8.0,
                annulus_outer_radius=12.0,
                gaia_absolute_magnitude=5.0 + (index * 0.04),
            )
            for index in range(6)
        ]
        rows.append(
            HrMeasurementRow(
                source_id="astrometric-outlier",
                source_name="Astrometric Outlier",
                catalog="gaia-dr3",
                ra_deg=0.0,
                dec_deg=0.0,
                gaia_g_mag=14.5,
                gaia_bp_rp=2.6,
                parallax_mas=2.08,
                parallax_error_mas=0.1,
                pm_ra_mas_per_year=9.95,
                pm_dec_mas_per_year=-4.58,
                x=25.0,
                y=25.0,
                aperture_radius=5.0,
                annulus_inner_radius=8.0,
                annulus_outer_radius=12.0,
                gaia_absolute_magnitude=9.7,
            )
        )

        result = find_common_motion_group(rows, strictness=0.9)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.used_parallax)
        self.assertTrue(result.used_hr_refinement)
        self.assertEqual(result.strictness, 0.9)
        self.assertEqual(result.astrometric_member_count, 7)
        self.assertEqual(result.hr_refined_member_count, 6)
        self.assertEqual(result.member_count, 6)
        self.assertEqual(result.member_indices, [0, 1, 2, 3, 4, 5])

    @unittest.skipUnless(importlib.util.find_spec("sklearn") is not None, "scikit-learn not installed")
    def test_find_common_motion_group_supports_sklearn_dbscan(self) -> None:
        rows = [
            HrMeasurementRow(
                source_id=str(index),
                source_name=f"Cluster {index}",
                catalog="gaia-dr3",
                ra_deg=0.0,
                dec_deg=0.0,
                gaia_g_mag=12.0,
                gaia_bp_rp=0.8,
                parallax_mas=2.0 + (index * 0.03),
                parallax_error_mas=0.1,
                pm_ra_mas_per_year=10.0 + (index * 0.05),
                pm_dec_mas_per_year=-4.5 + (index * 0.04),
                x=float(index),
                y=float(index),
                aperture_radius=5.0,
                annulus_inner_radius=8.0,
                annulus_outer_radius=12.0,
                gaia_absolute_magnitude=5.0,
            )
            for index in range(6)
        ]
        rows.extend(
            [
                HrMeasurementRow(
                    source_id="field-a",
                    source_name="Field A",
                    catalog="gaia-dr3",
                    ra_deg=0.0,
                    dec_deg=0.0,
                    gaia_g_mag=13.0,
                    gaia_bp_rp=1.1,
                    parallax_mas=8.0,
                    parallax_error_mas=0.2,
                    pm_ra_mas_per_year=-14.0,
                    pm_dec_mas_per_year=6.5,
                    x=20.0,
                    y=20.0,
                    aperture_radius=5.0,
                    annulus_inner_radius=8.0,
                    annulus_outer_radius=12.0,
                    gaia_absolute_magnitude=6.5,
                ),
                HrMeasurementRow(
                    source_id="field-b",
                    source_name="Field B",
                    catalog="gaia-dr3",
                    ra_deg=0.0,
                    dec_deg=0.0,
                    gaia_g_mag=13.5,
                    gaia_bp_rp=1.3,
                    parallax_mas=0.4,
                    parallax_error_mas=0.2,
                    pm_ra_mas_per_year=24.0,
                    pm_dec_mas_per_year=15.0,
                    x=21.0,
                    y=21.0,
                    aperture_radius=5.0,
                    annulus_inner_radius=8.0,
                    annulus_outer_radius=12.0,
                    gaia_absolute_magnitude=7.0,
                ),
            ]
        )

        result = find_common_motion_group(rows, method="sklearn")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.clustering_method, "sklearn")
        self.assertEqual(result.member_indices, [0, 1, 2, 3, 4, 5])