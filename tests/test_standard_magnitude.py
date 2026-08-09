from __future__ import annotations

import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from photometry_app.core.matching import apply_differential_photometry, build_overview_light_curve_layers
from photometry_app.core.models import CatalogStar, FieldCatalog, PhotometryMeasurement, SolvedField
from photometry_app.core.pipeline import (
    _enrich_manual_catalog_entries_from_field,
    _stamp_catalog_bands_onto_measurements,
)
from photometry_app.core.standard_magnitude import (
    get_band_magnitudes,
    merge_band_magnitudes_by_sky_match,
    normalize_photometric_band,
    resolve_band_catalog_magnitude,
    set_band_magnitude,
)


class StandardMagnitudeTest(unittest.TestCase):
    def test_normalize_photometric_band_maps_common_filters(self) -> None:
        self.assertEqual(normalize_photometric_band("V"), "V")
        self.assertEqual(normalize_photometric_band("Johnson V"), "V")
        self.assertEqual(normalize_photometric_band("TG"), "V")
        self.assertEqual(normalize_photometric_band("Clear"), "V")
        self.assertEqual(normalize_photometric_band("L"), "V")
        self.assertEqual(normalize_photometric_band("CV"), "V")
        self.assertEqual(normalize_photometric_band("TB"), "B")
        self.assertEqual(normalize_photometric_band("TR"), "R")
        self.assertEqual(normalize_photometric_band("SG"), "SG")
        self.assertEqual(normalize_photometric_band("g'"), "SG")
        self.assertIsNone(normalize_photometric_band(None))
        self.assertIsNone(normalize_photometric_band("mystery"))

    def test_resolve_band_catalog_magnitude_prefers_vsp_over_apass_over_gaia(self) -> None:
        star = CatalogStar("gaia-dr3", "g1", "G1", 10.0, 20.0, 12.5, False)
        set_band_magnitude(star, "V", 12.9, source="gaia-g")
        set_band_magnitude(star, "V", 12.4, error=0.03, source="apass")
        set_band_magnitude(star, "V", 12.1, error=0.02, source="vsp")

        mag, error, source = resolve_band_catalog_magnitude(star, "V")
        self.assertAlmostEqual(mag or 0.0, 12.1, places=6)
        self.assertAlmostEqual(error or 0.0, 0.02, places=6)
        self.assertEqual(source, "vsp")

        set_band_magnitude(star, "V", 11.0, source="gaia-g")  # lower priority must not overwrite
        mag, _error, source = resolve_band_catalog_magnitude(star, "V")
        self.assertAlmostEqual(mag or 0.0, 12.1, places=6)
        self.assertEqual(source, "vsp")

        mag, _error, source = resolve_band_catalog_magnitude(star, "R")
        self.assertAlmostEqual(mag or 0.0, 12.5, places=6)
        self.assertEqual(source, "gaia-g")

    def test_merge_band_magnitudes_by_sky_match_attaches_nearby_donors(self) -> None:
        target = CatalogStar("gaia-dr3", "g1", "G1", 146.27, 56.63, 13.0, False)
        donor = CatalogStar(
            "apass9",
            "a1",
            "A1",
            146.2701,
            56.6301,
            12.2,
            False,
            metadata={"band_magnitudes": {"V": {"mag": 12.2, "error": 0.04, "source": "apass"}}},
        )
        matched = merge_band_magnitudes_by_sky_match([target], [donor], source="apass")
        self.assertEqual(matched, 1)
        mag, error, source = resolve_band_catalog_magnitude(target, "V")
        self.assertAlmostEqual(mag or 0.0, 12.2, places=6)
        self.assertAlmostEqual(error or 0.0, 0.04, places=6)
        self.assertEqual(source, "apass")

    def test_enrich_manual_catalog_entries_seeds_gaia_g_from_magnitude_only_donors(self) -> None:
        manual_comp = CatalogStar("manual", "manual-comp-1", "Comp 1", 146.2702, 56.6302, None, False)
        gaia_donor = CatalogStar("gaia-dr3", "g1", "G1", 146.2701, 56.6301, 12.4, False)
        field = FieldCatalog(
            center_ra_deg=146.27,
            center_dec_deg=56.63,
            radius_deg=0.2,
            gaia_stars=[gaia_donor],
            variable_stars=[],
            exoplanets=[],
        )

        matched = _enrich_manual_catalog_entries_from_field([manual_comp], field)

        self.assertEqual(matched, 1)
        self.assertAlmostEqual(manual_comp.magnitude or 0.0, 12.4, places=6)
        self.assertIn("G", get_band_magnitudes(manual_comp))
        mag, _error, source = resolve_band_catalog_magnitude(manual_comp, "V")
        self.assertAlmostEqual(mag or 0.0, 12.4, places=6)
        self.assertEqual(source, "gaia-g")

    def test_enrich_manual_catalog_entries_matches_vsp_and_apass_directly(self) -> None:
        manual_comp = CatalogStar("manual", "manual-comp-1", "Comp 1", 146.2702, 56.6302, None, False)
        field = FieldCatalog(
            center_ra_deg=146.27,
            center_dec_deg=56.63,
            radius_deg=0.2,
            gaia_stars=[],
            variable_stars=[],
            exoplanets=[],
        )
        solved = SolvedField(
            center_ra_deg=146.27,
            center_dec_deg=56.63,
            radius_deg=0.2,
            width=1000,
            height=1000,
            wcs_path=Path("C:/synthetic/field.wcs"),
        )
        apass_donor = CatalogStar(
            "apass9",
            "a1",
            "A1",
            146.2701,
            56.6301,
            12.2,
            False,
            metadata={"band_magnitudes": {"B": {"mag": 12.8, "error": 0.03, "source": "apass"}, "V": {"mag": 12.2, "error": 0.02, "source": "apass"}}},
        )
        vsp_donor = CatalogStar(
            "aavso-vsp",
            "000-BBB-CCC",
            "112",
            146.27015,
            56.63015,
            12.1,
            False,
            metadata={"band_magnitudes": {"V": {"mag": 12.1, "error": 0.01, "source": "vsp"}}},
        )

        with (
            patch("photometry_app.core.standard_magnitude.query_apass_field", return_value=[apass_donor]),
            patch("photometry_app.core.standard_magnitude.query_vsp_field", return_value=([vsp_donor], "X99999")),
        ):
            matched = _enrich_manual_catalog_entries_from_field(
                [manual_comp],
                field,
                solved_field=solved,
                aavso_chart_id="X99999",
            )

        self.assertEqual(matched, 1)
        mag_v, error_v, source_v = resolve_band_catalog_magnitude(manual_comp, "V")
        mag_b, error_b, source_b = resolve_band_catalog_magnitude(manual_comp, "B")
        self.assertAlmostEqual(mag_v or 0.0, 12.1, places=6)
        self.assertEqual(source_v, "vsp")
        self.assertAlmostEqual(error_v or 0.0, 0.01, places=6)
        self.assertAlmostEqual(mag_b or 0.0, 12.8, places=6)
        self.assertEqual(source_b, "apass")
        self.assertAlmostEqual(error_b or 0.0, 0.03, places=6)
        self.assertAlmostEqual(manual_comp.magnitude or 0.0, 12.1, places=6)

    def test_stamp_catalog_bands_onto_measurements_updates_cached_rows(self) -> None:
        measurement = PhotometryMeasurement(
            source_id="manual-comp-1",
            source_name="Comp 1",
            catalog="manual",
            object_name="DY Her",
            file_path=Path("C:/synthetic/frame.fit"),
            observation_time=datetime(2026, 3, 16, 1, 0, 0),
            filter_name="V",
            ra_deg=10.0,
            dec_deg=20.0,
            x=100.0,
            y=100.0,
            flux=2000.0,
            flux_error=8.0,
            instrumental_magnitude=-8.2,
            differential_magnitude=None,
            is_variable=False,
            is_reference=True,
            catalog_magnitude=None,
            band_magnitudes={},
            flags=[],
        )
        catalog_star = CatalogStar(
            "manual",
            "manual-comp-1",
            "Comp 1",
            10.0,
            20.0,
            11.6,
            False,
            metadata={"band_magnitudes": {"V": {"mag": 11.6, "error": 0.02, "source": "vsp"}}},
        )
        rows = [measurement]
        stamped = _stamp_catalog_bands_onto_measurements(rows, {"manual-comp-1": catalog_star})
        self.assertEqual(stamped, 1)
        self.assertAlmostEqual(rows[0].catalog_magnitude or 0.0, 11.6, places=6)
        self.assertEqual(rows[0].band_magnitudes["V"]["source"], "vsp")

    def test_apply_differential_photometry_uses_gaia_g_fallback_for_standard_on_bv_filters(self) -> None:
        root = Path("C:/synthetic")
        file_path = root / "frame_b.fit"
        observation_time = datetime(2026, 3, 16, 1, 0, 0)
        variable = PhotometryMeasurement(
            source_id="manual-target",
            source_name="Target",
            catalog="manual",
            object_name="DY Her",
            file_path=file_path,
            observation_time=observation_time,
            filter_name="B",
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
            comparison_source_ids=["manual-comp-1"],
            flags=[],
        )
        reference = PhotometryMeasurement(
            source_id="manual-comp-1",
            source_name="Comp 1",
            catalog="manual",
            object_name="DY Her",
            file_path=file_path,
            observation_time=observation_time,
            filter_name="B",
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
            band_magnitudes={"G": {"mag": 11.0, "source": "gaia-g"}},
            flags=[],
        )

        updated = apply_differential_photometry([variable, reference], nearby_reference_count=1)
        target = next(item for item in updated if item.source_id == "manual-target")

        self.assertIsNotNone(target.differential_magnitude)
        self.assertIsNotNone(target.standard_magnitude)
        self.assertAlmostEqual(target.standard_magnitude or 0.0, -7.5 + (11.0 - (-8.2)), places=6)
        self.assertEqual(target.standard_catalog_source, "gaia-g")

    def test_apply_differential_photometry_computes_standard_from_band_mags(self) -> None:
        root = Path("C:/synthetic")
        file_path = root / "frame.fit"
        observation_time = datetime(2026, 3, 16, 1, 0, 0)
        variable = PhotometryMeasurement(
            source_id="var-1",
            source_name="Var 1",
            catalog="vsx",
            object_name="DY Her",
            file_path=file_path,
            observation_time=observation_time,
            filter_name="V",
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
        reference = PhotometryMeasurement(
            source_id="ref-a",
            source_name="Ref A",
            catalog="gaia-dr3",
            object_name="DY Her",
            file_path=file_path,
            observation_time=observation_time,
            filter_name="V",
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
            catalog_magnitude=11.0,  # Gaia G used for calibrated
            band_magnitudes={"V": {"mag": 11.6, "error": 0.02, "source": "vsp"}, "G": {"mag": 11.0, "source": "gaia-g"}},
            flags=[],
        )

        updated = apply_differential_photometry([variable, reference], nearby_reference_count=1)
        target = next(item for item in updated if item.source_id == "var-1")

        expected_calibrated_zp = 11.0 - (-8.2)
        expected_standard_zp = 11.6 - (-8.2)
        self.assertAlmostEqual(target.calibrated_magnitude or 0.0, -7.5 + expected_calibrated_zp, places=6)
        self.assertAlmostEqual(target.standard_magnitude or 0.0, -7.5 + expected_standard_zp, places=6)
        self.assertEqual(target.standard_catalog_band, "V")
        self.assertEqual(target.standard_catalog_source, "vsp")
        self.assertEqual(target.standard_zero_point_source_count, 1)
        # Calibrated path remains Gaia-G based and distinct from standard.
        self.assertNotAlmostEqual(target.calibrated_magnitude or 0.0, target.standard_magnitude or 0.0, places=6)

    def test_overview_comps_use_target_standard_zero_point(self) -> None:
        root = Path("C:/synthetic")
        t0 = datetime(2026, 3, 16, 1, 0, 0)
        target = PhotometryMeasurement(
            source_id="target-1",
            source_name="DY Her",
            catalog="vsx",
            object_name="DY Her",
            file_path=root / "frame_v.fit",
            observation_time=t0,
            filter_name="V",
            ra_deg=10.0,
            dec_deg=20.0,
            x=100.0,
            y=100.0,
            flux=1000.0,
            flux_error=10.0,
            instrumental_magnitude=-7.5,
            differential_magnitude=0.1,
            calibrated_magnitude=12.0,
            zero_point_magnitude=19.5,
            standard_magnitude=12.4,
            standard_zero_point_magnitude=19.9,
            standard_zero_point_magnitude_error=0.01,
            comparison_reference_flux=2000.0,
            comparison_source_ids=["comp-a"],
            comparison_source_names=["Comp A"],
            is_variable=True,
            is_reference=False,
            flags=[],
        )
        comp = PhotometryMeasurement(
            source_id="comp-a",
            source_name="Comp A",
            catalog="gaia-dr3",
            object_name="DY Her",
            file_path=root / "frame_v.fit",
            observation_time=t0,
            filter_name="V",
            ra_deg=10.001,
            dec_deg=20.001,
            x=101.0,
            y=101.0,
            flux=2000.0,
            flux_error=10.0,
            instrumental_magnitude=-8.0,
            differential_magnitude=None,
            is_variable=False,
            is_reference=True,
            flags=[],
        )

        layers, _status = build_overview_light_curve_layers([target, comp], "target-1")
        comp_layers = [layer for layer in layers if layer.role == "comparison"]
        self.assertTrue(comp_layers)
        point = comp_layers[0].series.points[0]
        self.assertIsNotNone(point.standard_magnitude)
        self.assertAlmostEqual(point.standard_magnitude or 0.0, -8.0 + 19.9, places=6)

    def test_query_vsp_and_apass_helpers_are_mocked_safe(self) -> None:
        from photometry_app.core.standard_magnitude import enrich_gaia_stars_with_standard_catalogs

        solved = SolvedField(
            center_ra_deg=146.27,
            center_dec_deg=56.63,
            radius_deg=0.2,
            width=1000,
            height=1000,
            wcs_path=Path("C:/synthetic/field.wcs"),
        )
        gaia = [CatalogStar("gaia-dr3", "g1", "G1", 146.27, 56.63, 12.5, False)]
        apass_donor = CatalogStar(
            "apass9",
            "a1",
            "A1",
            146.27005,
            56.63005,
            12.3,
            False,
            metadata={"band_magnitudes": {"V": {"mag": 12.3, "source": "apass"}}},
        )
        vsp_donor = CatalogStar(
            "aavso-vsp",
            "v1",
            "114",
            146.27004,
            56.63004,
            12.1,
            False,
            metadata={"band_magnitudes": {"V": {"mag": 12.1, "source": "vsp"}}},
        )
        with (
            patch("photometry_app.core.standard_magnitude.query_apass_field", return_value=[apass_donor]),
            patch("photometry_app.core.standard_magnitude.query_vsp_field", return_value=([vsp_donor], "X12345")),
        ):
            notes = enrich_gaia_stars_with_standard_catalogs(gaia, solved, aavso_chart_id=None)
        self.assertEqual(notes["vsp_chart_id"], "X12345")
        self.assertEqual(notes["vsp_matches"], 1)
        mag, _error, source = resolve_band_catalog_magnitude(gaia[0], "V")
        self.assertAlmostEqual(mag or 0.0, 12.1, places=6)
        self.assertEqual(source, "vsp")


if __name__ == "__main__":
    unittest.main()
