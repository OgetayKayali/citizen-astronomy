from __future__ import annotations

import math
import unittest
from datetime import datetime
from pathlib import Path

from photometry_app.core.matching import (
    apply_differential_photometry,
    apply_measurement_quality_analysis,
    build_light_curve_series,
    build_overview_light_curve_layers,
    select_reference_stars,
)
from photometry_app.core.models import CatalogStar, PhotometryMeasurement


class MatchingTest(unittest.TestCase):
    def test_select_reference_stars_prefers_midrange_magnitudes(self) -> None:
        gaia_stars = [
            CatalogStar("gaia-dr3", "ref-bright-a", "Ref Bright A", 10.0, 20.0, 8.1, False),
            CatalogStar("gaia-dr3", "ref-bright-b", "Ref Bright B", 10.1, 20.1, 8.4, False),
            CatalogStar("gaia-dr3", "ref-mid-a", "Ref Mid A", 10.2, 20.2, 11.2, False),
            CatalogStar("gaia-dr3", "ref-mid-b", "Ref Mid B", 10.3, 20.3, 11.8, False),
            CatalogStar("gaia-dr3", "ref-mid-c", "Ref Mid C", 10.4, 20.4, 12.6, False),
        ]

        selected = select_reference_stars(gaia_stars, [], limit=2)

        self.assertEqual([star.source_id for star in selected], ["ref-mid-b", "ref-mid-c"])

    def test_select_reference_stars_respects_magnitude_range_limiter(self) -> None:
        gaia_stars = [
            CatalogStar("gaia-dr3", "ref-a", "Ref A", 10.0, 20.0, 8.1, False),
            CatalogStar("gaia-dr3", "ref-b", "Ref B", 10.1, 20.1, 10.2, False),
            CatalogStar("gaia-dr3", "ref-c", "Ref C", 10.2, 20.2, 11.7, False),
            CatalogStar("gaia-dr3", "ref-d", "Ref D", 10.3, 20.3, 13.8, False),
        ]

        selected = select_reference_stars(gaia_stars, [], limit=5, minimum_magnitude=10.0, maximum_magnitude=12.0)

        self.assertEqual([star.source_id for star in selected], ["ref-c", "ref-b"])

    def test_select_reference_stars_builds_per_variable_magnitude_matched_pools(self) -> None:
        variables = [
            CatalogStar("vsx", "var-bright", "Bright Var", 10.0, 20.0, 5.0, True),
            CatalogStar("vsx", "var-faint", "Faint Var", 10.5, 20.5, 14.0, True),
        ]
        gaia_stars = [
            CatalogStar("gaia-dr3", "ref-near-bright-far-mag", "Near Bright Far Mag", 10.001, 20.001, 12.0, False),
            CatalogStar("gaia-dr3", "ref-match-bright", "Match Bright", 10.05, 20.05, 5.2, False),
            CatalogStar("gaia-dr3", "ref-match-bright-b", "Match Bright B", 10.06, 20.06, 4.8, False),
            CatalogStar("gaia-dr3", "ref-match-faint", "Match Faint", 10.55, 20.55, 13.8, False),
            CatalogStar("gaia-dr3", "ref-match-faint-b", "Match Faint B", 10.56, 20.56, 14.3, False),
            CatalogStar("gaia-dr3", "ref-mid", "Mid", 10.2, 20.2, 11.5, False),
        ]

        selected = select_reference_stars(gaia_stars, variables, per_target_count=2)
        selected_ids = {star.source_id for star in selected}

        self.assertEqual(
            selected_ids,
            {"ref-match-bright", "ref-match-bright-b", "ref-match-faint", "ref-match-faint-b"},
        )
        self.assertNotIn("ref-near-bright-far-mag", selected_ids)
        self.assertNotIn("ref-mid", selected_ids)

    def test_select_reference_stars_uses_explicit_target_stars_for_discover_style_pools(self) -> None:
        exclusions = [
            CatalogStar("vsx", "known-var", "Known Var", 10.0, 20.0, 11.0, True),
        ]
        targets = [
            CatalogStar("gaia-dr3", "cand-bright", "Cand Bright", 10.2, 20.2, 5.0, False),
            CatalogStar("gaia-dr3", "cand-faint", "Cand Faint", 10.8, 20.8, 14.0, False),
        ]
        gaia_stars = [
            *exclusions,
            *targets,
            CatalogStar("gaia-dr3", "ref-match-bright", "Match Bright", 10.25, 20.25, 5.1, False),
            CatalogStar("gaia-dr3", "ref-match-faint", "Match Faint", 10.85, 20.85, 13.9, False),
            CatalogStar("gaia-dr3", "ref-mid", "Mid", 10.5, 20.5, 11.5, False),
        ]

        selected = select_reference_stars(
            gaia_stars,
            exclusions,
            per_target_count=1,
            target_stars=targets,
        )
        selected_ids = {star.source_id for star in selected}

        self.assertEqual(selected_ids, {"ref-match-bright", "ref-match-faint"})
        self.assertNotIn("cand-bright", selected_ids)
        self.assertNotIn("cand-faint", selected_ids)
        self.assertNotIn("ref-mid", selected_ids)

    def test_apply_differential_photometry_uses_nearest_reference_stars(self) -> None:
        root = Path("C:/synthetic")
        file_path = root / "frame.fit"
        observation_time = datetime(2026, 3, 16, 1, 0, 0)
        variable = PhotometryMeasurement(
            source_id="var-1",
            source_name="Var 1",
            catalog="vsx",
            object_name="M42",
            file_path=file_path,
            observation_time=observation_time,
            filter_name="R",
            ra_deg=10.0,
            dec_deg=20.0,
            x=100.0,
            y=100.0,
            flux=1000.0,
            flux_error=10.0,
            instrumental_magnitude=-7.5,
            differential_magnitude=None,
            is_variable=True,
            is_reference=False,
            flags=[],
        )
        nearby_reference_a = PhotometryMeasurement(
            source_id="ref-a",
            source_name="Ref A",
            catalog="gaia-dr3",
            object_name="M42",
            file_path=file_path,
            observation_time=observation_time,
            filter_name="R",
            ra_deg=10.001,
            dec_deg=20.001,
            x=101.0,
            y=101.0,
            flux=2000.0,
            flux_error=8.0,
            instrumental_magnitude=-8.2,
            differential_magnitude=None,
            is_variable=False,
            is_reference=True,
            catalog_magnitude=11.0,
            flags=[],
        )
        nearby_reference_b = PhotometryMeasurement(
            source_id="ref-b",
            source_name="Ref B",
            catalog="gaia-dr3",
            object_name="M42",
            file_path=file_path,
            observation_time=observation_time,
            filter_name="R",
            ra_deg=10.002,
            dec_deg=20.002,
            x=102.0,
            y=102.0,
            flux=2200.0,
            flux_error=8.0,
            instrumental_magnitude=-8.3,
            differential_magnitude=None,
            is_variable=False,
            is_reference=True,
            catalog_magnitude=11.3,
            flags=[],
        )
        distant_reference = PhotometryMeasurement(
            source_id="ref-far",
            source_name="Ref Far",
            catalog="gaia-dr3",
            object_name="M42",
            file_path=file_path,
            observation_time=observation_time,
            filter_name="R",
            ra_deg=15.0,
            dec_deg=25.0,
            x=400.0,
            y=400.0,
            flux=10000.0,
            flux_error=15.0,
            instrumental_magnitude=-10.0,
            differential_magnitude=None,
            is_variable=False,
            is_reference=True,
            catalog_magnitude=12.5,
            flags=[],
        )

        updated = apply_differential_photometry(
            [variable, nearby_reference_a, nearby_reference_b, distant_reference],
            nearby_reference_count=2,
        )

        updated_variable = next(item for item in updated if item.source_id == "var-1")
        expected_reference_flux = 2100.0
        expected_diff_mag = -2.5 * math.log10(1000.0 / expected_reference_flux)
        expected_reference_error = math.sqrt((8.0 * 8.0) + (8.0 * 8.0)) / 2.0
        expected_diff_error = (2.5 / math.log(10.0)) * math.sqrt((10.0 / 1000.0) ** 2 + (expected_reference_error / expected_reference_flux) ** 2)
        target_mag_error = (2.5 / math.log(10.0)) * (10.0 / 1000.0)
        reference_a_mag_error = (2.5 / math.log(10.0)) * (8.0 / 2000.0)
        reference_b_mag_error = (2.5 / math.log(10.0)) * (8.0 / 2200.0)
        expected_zero_point = (
            ((11.0 - (-8.2)) / (reference_a_mag_error ** 2))
            + ((11.3 - (-8.3)) / (reference_b_mag_error ** 2))
        ) / ((1.0 / (reference_a_mag_error ** 2)) + (1.0 / (reference_b_mag_error ** 2)))
        expected_zero_point_error = math.sqrt(1.0 / ((1.0 / (reference_a_mag_error ** 2)) + (1.0 / (reference_b_mag_error ** 2))))
        expected_calibrated_error = math.sqrt((target_mag_error ** 2) + (expected_zero_point_error ** 2))
        self.assertAlmostEqual(updated_variable.differential_magnitude or 0.0, expected_diff_mag, places=6)
        self.assertAlmostEqual(updated_variable.differential_magnitude_error or 0.0, expected_diff_error, places=6)
        self.assertAlmostEqual(updated_variable.zero_point_magnitude or 0.0, expected_zero_point, places=6)
        self.assertAlmostEqual(updated_variable.calibrated_magnitude or 0.0, -7.5 + expected_zero_point, places=6)
        self.assertAlmostEqual(updated_variable.calibrated_magnitude_error or 0.0, expected_calibrated_error, places=6)
        self.assertEqual(updated_variable.zero_point_source_count, 2)
        self.assertEqual(updated_variable.comparison_source_ids, ["ref-a", "ref-b"])
        self.assertEqual(updated_variable.comparison_source_names, ["Ref A", "Ref B"])
        self.assertAlmostEqual(updated_variable.comparison_reference_flux or 0.0, expected_reference_flux, places=6)

    def test_apply_differential_photometry_prefers_magnitude_matched_references(self) -> None:
        root = Path("C:/synthetic")
        file_path = root / "frame.fit"
        observation_time = datetime(2026, 3, 16, 1, 0, 0)
        variable = PhotometryMeasurement(
            source_id="var-1",
            source_name="Var 1",
            catalog="vsx",
            object_name="M42",
            file_path=file_path,
            observation_time=observation_time,
            filter_name="R",
            ra_deg=10.0,
            dec_deg=20.0,
            x=100.0,
            y=100.0,
            flux=1000.0,
            flux_error=10.0,
            instrumental_magnitude=-7.5,
            differential_magnitude=None,
            is_variable=True,
            is_reference=False,
            catalog_magnitude=5.0,
            flags=[],
        )
        nearby_mismatched = PhotometryMeasurement(
            source_id="ref-near-faint",
            source_name="Ref Near Faint",
            catalog="gaia-dr3",
            object_name="M42",
            file_path=file_path,
            observation_time=observation_time,
            filter_name="R",
            ra_deg=10.001,
            dec_deg=20.001,
            x=101.0,
            y=101.0,
            flux=500.0,
            flux_error=8.0,
            instrumental_magnitude=-6.7,
            differential_magnitude=None,
            is_variable=False,
            is_reference=True,
            catalog_magnitude=12.0,
            flags=[],
        )
        farther_matched = PhotometryMeasurement(
            source_id="ref-far-bright",
            source_name="Ref Far Bright",
            catalog="gaia-dr3",
            object_name="M42",
            file_path=file_path,
            observation_time=observation_time,
            filter_name="R",
            ra_deg=10.05,
            dec_deg=20.05,
            x=150.0,
            y=150.0,
            flux=2000.0,
            flux_error=8.0,
            instrumental_magnitude=-8.2,
            differential_magnitude=None,
            is_variable=False,
            is_reference=True,
            catalog_magnitude=5.1,
            flags=[],
        )

        updated = apply_differential_photometry(
            [variable, nearby_mismatched, farther_matched],
            nearby_reference_count=1,
        )

        updated_variable = next(item for item in updated if item.source_id == "var-1")
        self.assertEqual(updated_variable.comparison_source_ids, ["ref-far-bright"])
        self.assertAlmostEqual(
            updated_variable.differential_magnitude or 0.0,
            -2.5 * math.log10(1000.0 / 2000.0),
            places=6,
        )

    def test_apply_differential_photometry_ignores_saturated_reference_measurements(self) -> None:
        root = Path("C:/synthetic")
        file_path = root / "frame.fit"
        observation_time = datetime(2026, 3, 16, 1, 0, 0)
        variable = PhotometryMeasurement(
            source_id="var-1",
            source_name="Var 1",
            catalog="vsx",
            object_name="M42",
            file_path=file_path,
            observation_time=observation_time,
            filter_name="R",
            ra_deg=10.0,
            dec_deg=20.0,
            x=100.0,
            y=100.0,
            flux=1000.0,
            flux_error=10.0,
            instrumental_magnitude=-7.5,
            differential_magnitude=None,
            is_variable=True,
            is_reference=False,
            flags=[],
        )
        safe_reference = PhotometryMeasurement(
            source_id="ref-safe",
            source_name="Ref Safe",
            catalog="gaia-dr3",
            object_name="M42",
            file_path=file_path,
            observation_time=observation_time,
            filter_name="R",
            ra_deg=10.001,
            dec_deg=20.001,
            x=101.0,
            y=101.0,
            flux=2000.0,
            flux_error=8.0,
            instrumental_magnitude=-8.2,
            differential_magnitude=None,
            is_variable=False,
            is_reference=True,
            flags=[],
        )
        saturated_reference = PhotometryMeasurement(
            source_id="ref-sat",
            source_name="Ref Saturated",
            catalog="gaia-dr3",
            object_name="M42",
            file_path=file_path,
            observation_time=observation_time,
            filter_name="R",
            ra_deg=10.002,
            dec_deg=20.002,
            x=102.0,
            y=102.0,
            flux=8000.0,
            flux_error=15.0,
            instrumental_magnitude=-9.8,
            differential_magnitude=None,
            is_variable=False,
            is_reference=True,
            flags=[],
            peak_pixel_value=65535.0,
            saturation_threshold=65535.0,
            saturated_pixel_count=3,
            is_saturated=True,
        )
        near_saturated_reference = PhotometryMeasurement(
            source_id="ref-near",
            source_name="Ref Near Saturated",
            catalog="gaia-dr3",
            object_name="M42",
            file_path=file_path,
            observation_time=observation_time,
            filter_name="R",
            ra_deg=10.003,
            dec_deg=20.003,
            x=103.0,
            y=103.0,
            flux=4000.0,
            flux_error=12.0,
            instrumental_magnitude=-9.0,
            differential_magnitude=None,
            is_variable=False,
            is_reference=True,
            flags=[],
            peak_pixel_value=65000.0,
            saturation_threshold=65535.0,
            saturated_pixel_count=0,
            is_saturated=False,
        )

        updated = apply_differential_photometry(
            [variable, safe_reference, saturated_reference, near_saturated_reference],
            nearby_reference_count=3,
        )

        updated_variable = next(item for item in updated if item.source_id == "var-1")
        expected_diff_mag = -2.5 * math.log10(1000.0 / 2000.0)
        self.assertAlmostEqual(updated_variable.differential_magnitude or 0.0, expected_diff_mag, places=6)
        self.assertEqual(updated_variable.comparison_source_ids, ["ref-safe"])
        self.assertEqual(updated_variable.comparison_source_names, ["Ref Safe"])

    def test_apply_differential_photometry_flags_variables_without_reference_flux(self) -> None:
        root = Path("C:/synthetic")
        file_path = root / "frame.fit"
        observation_time = datetime(2026, 3, 16, 1, 0, 0)
        variable = PhotometryMeasurement(
            source_id="var-1",
            source_name="Var 1",
            catalog="vsx",
            object_name="M42",
            file_path=file_path,
            observation_time=observation_time,
            filter_name="R",
            ra_deg=10.0,
            dec_deg=20.0,
            x=100.0,
            y=100.0,
            flux=1000.0,
            flux_error=10.0,
            instrumental_magnitude=-7.5,
            differential_magnitude=None,
            is_variable=True,
            is_reference=False,
            flags=[],
        )

        updated = apply_differential_photometry([variable])

        updated_variable = updated[0]
        self.assertIsNone(updated_variable.differential_magnitude)
        self.assertIsNone(updated_variable.differential_magnitude_error)
        self.assertIn("No nearby reference stars with positive flux.", updated_variable.flags)

    def test_build_light_curve_series_omits_sources_without_usable_values(self) -> None:
        root = Path("C:/synthetic")
        file_path = root / "frame.fit"
        observation_time = datetime(2026, 3, 16, 1, 0, 0)
        rejected = PhotometryMeasurement(
            source_id="var-rejected",
            source_name="Rejected",
            catalog="vsx",
            object_name="M42",
            file_path=file_path,
            observation_time=observation_time,
            filter_name="R",
            ra_deg=10.0,
            dec_deg=20.0,
            x=100.0,
            y=100.0,
            flux=None,
            flux_error=None,
            instrumental_magnitude=None,
            differential_magnitude=None,
            is_variable=True,
            is_reference=False,
            flags=["Target lies outside the usable image area (configured 15.0% edge margin)."],
        )
        valid = PhotometryMeasurement(
            source_id="var-valid",
            source_name="Valid",
            catalog="vsx",
            object_name="M42",
            file_path=file_path,
            observation_time=observation_time,
            filter_name="R",
            ra_deg=11.0,
            dec_deg=21.0,
            x=120.0,
            y=120.0,
            flux=1000.0,
            flux_error=10.0,
            instrumental_magnitude=-7.5,
            differential_magnitude=0.4,
            is_variable=True,
            is_reference=False,
            flags=[],
        )

        series = build_light_curve_series([rejected, valid])

        self.assertEqual(len(series), 1)
        self.assertEqual(series[0].source_id, "var-valid")
        self.assertEqual(len(series[0].points), 1)
        self.assertIn("mad", series[0].variability_metrics)

    def test_build_light_curve_series_computes_candidate_score(self) -> None:
        root = Path("C:/synthetic")
        rows: list[PhotometryMeasurement] = []
        base_time = datetime(2026, 3, 16, 1, 0, 0)
        for index, value in enumerate([0.02, 0.41, 0.08, 0.38, 0.05, 0.44]):
            rows.append(
                PhotometryMeasurement(
                    source_id="var-1",
                    source_name="Var 1",
                    catalog="vsx",
                    object_name="M42",
                    file_path=root / f"frame_{index}.fit",
                    observation_time=base_time.replace(minute=index * 5),
                    filter_name="R",
                    ra_deg=10.0,
                    dec_deg=20.0,
                    x=100.0,
                    y=100.0,
                    flux=1000.0,
                    flux_error=10.0,
                    instrumental_magnitude=-7.5,
                    differential_magnitude=value,
                    differential_magnitude_error=0.01,
                    is_variable=True,
                    is_reference=False,
                    flags=[],
                )
            )

        series = build_light_curve_series(rows)

        self.assertEqual(len(series), 1)
        self.assertGreater(series[0].candidate_score, 0.0)
        self.assertIn("stetson_j", series[0].variability_metrics)

    def test_apply_differential_photometry_weights_reference_flux_by_uncertainty(self) -> None:
        root = Path("C:/synthetic")
        file_path = root / "frame.fit"
        observation_time = datetime(2026, 3, 16, 1, 0, 0)
        variable = PhotometryMeasurement(
            source_id="var-1",
            source_name="Var 1",
            catalog="vsx",
            object_name="M42",
            file_path=file_path,
            observation_time=observation_time,
            filter_name="R",
            ra_deg=10.0,
            dec_deg=20.0,
            x=100.0,
            y=100.0,
            flux=1000.0,
            flux_error=10.0,
            instrumental_magnitude=-7.5,
            differential_magnitude=None,
            is_variable=True,
            is_reference=False,
            flags=[],
        )
        precise_reference = PhotometryMeasurement(
            source_id="ref-precise",
            source_name="Precise",
            catalog="gaia-dr3",
            object_name="M42",
            file_path=file_path,
            observation_time=observation_time,
            filter_name="R",
            ra_deg=10.001,
            dec_deg=20.001,
            x=101.0,
            y=101.0,
            flux=2000.0,
            flux_error=4.0,
            instrumental_magnitude=-8.2,
            differential_magnitude=None,
            is_variable=False,
            is_reference=True,
            flags=[],
        )
        noisy_reference = PhotometryMeasurement(
            source_id="ref-noisy",
            source_name="Noisy",
            catalog="gaia-dr3",
            object_name="M42",
            file_path=file_path,
            observation_time=observation_time,
            filter_name="R",
            ra_deg=10.002,
            dec_deg=20.002,
            x=102.0,
            y=102.0,
            flux=2600.0,
            flux_error=20.0,
            instrumental_magnitude=-8.5,
            differential_magnitude=None,
            is_variable=False,
            is_reference=True,
            flags=[],
        )

        updated = apply_differential_photometry([variable, precise_reference, noisy_reference], nearby_reference_count=2)

        updated_variable = next(item for item in updated if item.source_id == "var-1")
        expected_weighted_flux = ((2000.0 / (4.0 * 4.0)) + (2600.0 / (20.0 * 20.0))) / ((1.0 / (4.0 * 4.0)) + (1.0 / (20.0 * 20.0)))
        self.assertAlmostEqual(updated_variable.comparison_reference_flux or 0.0, expected_weighted_flux, places=6)

    def test_apply_measurement_quality_analysis_marks_low_quality_outlier(self) -> None:
        root = Path("C:/synthetic")
        rows: list[PhotometryMeasurement] = []
        base_time = datetime(2026, 3, 16, 1, 0, 0)
        baseline_values = [0.10, 0.11, 0.09, 0.10, 0.12]
        for index, value in enumerate(baseline_values):
            rows.append(
                PhotometryMeasurement(
                    source_id="var-1",
                    source_name="Var 1",
                    catalog="vsx",
                    object_name="M42",
                    file_path=root / f"frame_{index}.fit",
                    observation_time=base_time.replace(minute=index),
                    filter_name="R",
                    ra_deg=10.0,
                    dec_deg=20.0,
                    x=100.0,
                    y=100.0,
                    flux=1000.0,
                    flux_error=10.0,
                    instrumental_magnitude=-7.5,
                    differential_magnitude=value,
                    differential_magnitude_error=0.01,
                    is_variable=True,
                    is_reference=False,
                    flags=[],
                    snr=25.0,
                )
            )
        rows.append(
            PhotometryMeasurement(
                source_id="var-1",
                source_name="Var 1",
                catalog="vsx",
                object_name="M42",
                file_path=root / "frame_outlier.fit",
                observation_time=base_time.replace(minute=10),
                filter_name="R",
                ra_deg=10.0,
                dec_deg=20.0,
                x=100.0,
                y=100.0,
                flux=300.0,
                flux_error=120.0,
                instrumental_magnitude=-6.0,
                differential_magnitude=1.3,
                differential_magnitude_error=0.3,
                is_variable=True,
                is_reference=False,
                flags=[],
                snr=2.5,
            )
        )

        updated = apply_measurement_quality_analysis(rows)
        outlier = updated[-1]

        self.assertTrue(outlier.excluded_from_analysis)
        self.assertLess(outlier.quality_score, 0.35)
        self.assertTrue(any("Low SNR" in flag or "outlier" in flag.lower() for flag in outlier.flags))

    def test_build_overview_light_curve_layers_includes_target_comps_and_check(self) -> None:
        root = Path("C:/synthetic")
        t0 = datetime(2026, 3, 16, 1, 0, 0)

        def _row(
            *,
            source_id: str,
            source_name: str,
            filter_name: str,
            minute: int,
            is_reference: bool = False,
            is_check: bool = False,
            comparison_source_ids: list[str] | None = None,
            comparison_source_names: list[str] | None = None,
            flux: float = 1000.0,
            file_name: str | None = None,
            comparison_reference_flux: float | None = None,
            zero_point_magnitude: float | None = None,
            differential_magnitude: float | None = None,
        ) -> PhotometryMeasurement:
            resolved_file = file_name or f"{filter_name}_{minute}.fit"
            return PhotometryMeasurement(
                source_id=source_id,
                source_name=source_name,
                catalog="vsx" if not is_reference else "gaia-dr3",
                object_name="DY Her",
                file_path=root / resolved_file,
                observation_time=t0.replace(minute=minute),
                filter_name=filter_name,
                ra_deg=10.0,
                dec_deg=20.0,
                x=100.0,
                y=100.0,
                flux=flux,
                flux_error=10.0,
                instrumental_magnitude=-7.5 if not is_reference else -8.0,
                differential_magnitude=(0.1 if differential_magnitude is None and not is_reference else differential_magnitude),
                calibrated_magnitude=None if is_reference else 12.0,
                zero_point_magnitude=zero_point_magnitude,
                comparison_reference_flux=comparison_reference_flux,
                is_variable=not is_reference,
                is_reference=is_reference,
                is_check=is_check,
                comparison_source_ids=list(comparison_source_ids or []),
                comparison_source_names=list(comparison_source_names or []),
                flags=[],
            )

        measurements = [
            _row(
                source_id="target-1",
                source_name="DY Her",
                filter_name="V",
                minute=0,
                file_name="frame_v.fit",
                comparison_source_ids=["comp-a", "comp-b", "comp-c"],
                comparison_source_names=["Comp A", "Comp B", "Comp C"],
                comparison_reference_flux=2000.0,
                zero_point_magnitude=19.5,
            ),
            _row(
                source_id="target-1",
                source_name="DY Her",
                filter_name="B",
                minute=1,
                file_name="frame_b.fit",
                comparison_source_ids=["comp-a", "comp-b"],
                comparison_source_names=["Comp A", "Comp B"],
                comparison_reference_flux=2100.0,
                zero_point_magnitude=19.8,
            ),
            _row(source_id="comp-a", source_name="Comp A", filter_name="V", minute=0, is_reference=True, flux=2000.0, file_name="frame_v.fit"),
            _row(source_id="comp-a", source_name="Comp A", filter_name="B", minute=1, is_reference=True, flux=2100.0, file_name="frame_b.fit"),
            _row(source_id="comp-b", source_name="Comp B", filter_name="V", minute=0, is_reference=True, flux=1800.0, file_name="frame_v.fit"),
            _row(source_id="comp-c", source_name="Comp C", filter_name="V", minute=0, is_reference=True, flux=1700.0, file_name="frame_v.fit"),
            _row(
                source_id="check-1",
                source_name="Check Star",
                filter_name="V",
                minute=0,
                is_reference=True,
                is_check=True,
                flux=1900.0,
                file_name="frame_v.fit",
            ),
        ]

        layers, status_note = build_overview_light_curve_layers(measurements, "target-1", max_comparison_stars=2)

        labels = [layer.legend_label for layer in layers]
        roles = [layer.role for layer in layers]
        self.assertIn("DY Her [V]", labels)
        self.assertIn("DY Her [B]", labels)
        self.assertTrue(any(label.startswith("Comp ") and "[V]" in label for label in labels))
        self.assertIn("Check Check Star [V]", labels)
        self.assertEqual(roles.count("target"), 2)
        self.assertEqual(roles.count("check"), 1)
        # Most common exact ensemble is Comp A+B; Comp A appears in V and B filters.
        comparison_source_ids = {layer.series.source_id for layer in layers if layer.role == "comparison"}
        self.assertEqual(comparison_source_ids, {"comp-a", "comp-b"})
        self.assertIsNone(status_note)

        comp_layers = [layer for layer in layers if layer.role == "comparison"]
        self.assertTrue(comp_layers)
        for layer in comp_layers:
            for point in layer.series.points:
                self.assertIsNotNone(point.differential_magnitude)
                self.assertIsNotNone(point.calibrated_magnitude)

    def test_build_overview_light_curve_layers_prefers_sticky_comparison_ids(self) -> None:
        root = Path("C:/synthetic")
        t0 = datetime(2026, 3, 16, 1, 0, 0)

        def _row(
            *,
            source_id: str,
            source_name: str,
            filter_name: str,
            minute: int,
            is_reference: bool = False,
            comparison_source_ids: list[str] | None = None,
            comparison_source_names: list[str] | None = None,
            flux: float = 1000.0,
        ) -> PhotometryMeasurement:
            return PhotometryMeasurement(
                source_id=source_id,
                source_name=source_name,
                catalog="vsx" if not is_reference else "gaia-dr3",
                object_name="CS UMa",
                file_path=root / f"{filter_name}_{minute}.fit",
                observation_time=t0.replace(minute=minute),
                filter_name=filter_name,
                ra_deg=10.0,
                dec_deg=20.0,
                x=100.0,
                y=100.0,
                flux=flux,
                flux_error=10.0,
                instrumental_magnitude=-7.5 if not is_reference else -8.0,
                differential_magnitude=0.1 if not is_reference else None,
                calibrated_magnitude=None if is_reference else 12.0,
                is_variable=not is_reference,
                is_reference=is_reference,
                comparison_source_ids=list(comparison_source_ids or []),
                comparison_source_names=list(comparison_source_names or []),
                flags=[],
            )

        measurements = [
            _row(
                source_id="target-1",
                source_name="CS UMa",
                filter_name="V",
                minute=0,
                comparison_source_ids=["comp-a", "comp-b"],
                comparison_source_names=["Comp A", "Comp B"],
            ),
            _row(
                source_id="target-1",
                source_name="CS UMa",
                filter_name="L",
                minute=1,
                comparison_source_ids=["comp-c", "comp-d"],
                comparison_source_names=["Comp C", "Comp D"],
            ),
            _row(source_id="comp-a", source_name="Comp A", filter_name="V", minute=0, is_reference=True, flux=2000.0),
            _row(source_id="comp-b", source_name="Comp B", filter_name="V", minute=0, is_reference=True, flux=1800.0),
            _row(source_id="comp-c", source_name="Comp C", filter_name="L", minute=1, is_reference=True, flux=1700.0),
            _row(source_id="comp-d", source_name="Comp D", filter_name="L", minute=1, is_reference=True, flux=1600.0),
        ]

        layers, status_note = build_overview_light_curve_layers(
            measurements,
            "target-1",
            max_comparison_stars=8,
            preferred_comparison_source_ids=["comp-a", "comp-b"],
        )

        comparison_source_ids = {layer.series.source_id for layer in layers if layer.role == "comparison"}
        self.assertEqual(comparison_source_ids, {"comp-a", "comp-b"})
        self.assertIsNone(status_note)

    def test_build_overview_light_curve_layers_requires_science_target(self) -> None:
        root = Path("C:/synthetic")
        measurement = PhotometryMeasurement(
            source_id="comp-a",
            source_name="Comp A",
            catalog="gaia-dr3",
            object_name="DY Her",
            file_path=root / "comp.fit",
            observation_time=datetime(2026, 3, 16, 1, 0, 0),
            filter_name="V",
            ra_deg=10.0,
            dec_deg=20.0,
            x=100.0,
            y=100.0,
            flux=2000.0,
            flux_error=10.0,
            instrumental_magnitude=-8.0,
            differential_magnitude=None,
            is_variable=False,
            is_reference=True,
            flags=[],
        )

        layers, status_note = build_overview_light_curve_layers([measurement], "comp-a")

        self.assertEqual(layers, [])
        self.assertIsNotNone(status_note)
        assert status_note is not None
        self.assertIn("science target", status_note.lower())