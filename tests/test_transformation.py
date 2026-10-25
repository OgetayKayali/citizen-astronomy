from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from photometry_app.core.models import PhotometryMeasurement, ProcessingReport
from photometry_app.core.transformation import (
    StandardObservation, apply_transformations, fit_profile, load_profile, profile_digest,
    read_standard_observations, transformation_applied, write_standard_observations,
)


def standards(repeats=3, curvature=0.0):
    rows = []
    for i, color in enumerate(np.linspace(-0.2, 1.8, 20)):
        v = 11 + i/20
        b = v + color
        for exposure in range(repeats):
            for band, coefficient, magnitude in (("B", 0.12, b), ("V", -0.06, v)):
                rows.append(StandardObservation(str(i), f"{band}-{exposure}", band,
                            magnitude-(20+0.2*exposure)-coefficient*color-curvature*color**2,
                            0.003, b, v, 0.01, 0.01, observation_date="2026-10-01"))
    return rows


def profile(rows=None):
    return fit_profile(rows or standards(), name="Synthetic", telescope="Scope", camera="Camera",
                       optical_setup="IR cut; linear extracted channels", blue_filter="TB", visual_filter="TG",
                       calibration_date="2026-10-01", provenance="Synthetic test standards")


def settings(p):
    return SimpleNamespace(transformation_enabled=True, transformation_profiles=[p], transformation_profile_id=p["id"],
                           telescope=p["telescope"], camera=p["camera"], transformation_optical_setup=p["optical_setup"],
                           transformation_max_pair_seconds=120)


def science(color=0.9, comparisons=3):
    rows = []
    for band, coefficient, zp, token, delay in (("B", .12, 20, "TB", 0), ("V", -.06, 21, "TG", 30)):
        for i in range(comparisons+1):
            c = color if i == 0 else i*.3
            v = 12 if i == 0 else 11+i*.1
            b = v+c
            magnitude = (b if band == "B" else v)-zp-coefficient*c
            flux = 10**(-.4*magnitude)
            row = PhotometryMeasurement(str(i), f"Star {i}", "synthetic", "Field", Path(f"frame-{band}.fits"),
                                        datetime(2026, 10, 2, 1, 0, delay), token, 12, 23, 100+i*30, 150,
                                        flux, flux*.002, magnitude, 0.1, i == 0, i != 0,
                                        standard_magnitude=12.2, standard_magnitude_error=.02,
                                        standard_catalog_source="vsp", standard_zero_point_magnitude=20.,
                                        band_magnitudes={"B": {"mag": b, "error": .01, "source": "vsp"},
                                                         "V": {"mag": v, "error": .01, "source": "vsp"}},
                                        comparison_source_ids=[str(j) for j in range(1, comparisons+1)] if i == 0 else [])
            rows.append(row)
    return rows


def test_fit_recovers_coefficients_and_exposure_zero_points():
    p = profile()
    assert p["validated"], p["failures"]
    assert p["coefficients"]["Tb_bv"] == pytest.approx(.12, abs=1e-10)
    assert p["coefficients"]["Tv_bv"] == pytest.approx(-.06, abs=1e-10)
    assert p["coefficients"]["Tbv"] == pytest.approx(1/.82)
    held = {d["star_id"] for d in p["diagnostics"] if d["validation"]}
    train = {d["star_id"] for d in p["diagnostics"] if not d["validation"]}
    assert not held & train
    assert len(held) == 5


def test_repeated_exposures_do_not_average_away_catalog_errors():
    one, repeated = profile(standards(1)), profile(standards(5))
    assert repeated["covariance"][0][0] > one["covariance"][0][0] * .8


def test_curved_response_fails_validation():
    p = profile(standards(curvature=.4))
    assert not p["validated"]
    assert p["failures"]


def test_gross_outliers_are_excluded_and_profile_roundtrip_is_stable(tmp_path):
    rows = [replace(r, instrumental_magnitude=r.instrumental_magnitude+4)
            if r.star_id == "19" else r for r in standards()]
    p = profile(rows)
    assert p["validated"], p["failures"]
    assert p["coefficients"]["Tb_bv"] == pytest.approx(.12, abs=1e-9)
    bad = [r for r in p["observations"] if r["star_id"] == "19"]
    assert all(not r["enabled"] and "Automatic outlier" in r["reason"] for r in bad)
    assert all(r.enabled for r in rows)  # Inputs are retained unchanged.
    assert all(not d["enabled"] for d in p["diagnostics"] if d["star_id"] == "19")
    path = tmp_path / "screened.json"
    path.write_text(json.dumps(p))
    assert load_profile(path)["id"] == p["id"]


def test_single_bad_measurement_keeps_other_observations_of_the_star():
    rows = standards()
    rows[0] = replace(rows[0], instrumental_magnitude=rows[0].instrumental_magnitude+2)
    p = profile(rows)
    assert p["validated"]
    assert sum(not r["enabled"] for r in p["observations"]) == 1
    assert sum(r["enabled"] for r in p["observations"] if r["star_id"] == "0") == 5


def test_bad_exposure_is_excluded_using_training_stars_only():
    rows = [replace(r, instrumental_magnitude=r.instrumental_magnitude+(-1)**int(r.star_id)*.4)
            if r.exposure_id == "B-1" else r for r in standards()]
    p = profile(rows)
    assert p["validated"], p["failures"]
    excluded = [r for r in p["observations"] if not r["enabled"]]
    assert len(excluded) == 20
    assert all(r["exposure_id"] == "B-1" and "Automatic exposure" in r["reason"] for r in excluded)
    # A uniform dimming is handled by the exposure's own zero point.
    uniform = [replace(r, instrumental_magnitude=r.instrumental_magnitude+.7)
               if r.exposure_id == "B-1" else r for r in standards()]
    p = profile(uniform)
    assert p["validated"] and all(r["enabled"] for r in p["observations"])


def test_validation_outlier_is_not_clipped_to_make_fit_pass():
    rows = [replace(r, instrumental_magnitude=r.instrumental_magnitude+4)
            if r.star_id == "1" else r for r in standards()]
    p = profile(rows)
    assert not p["validated"]
    assert all(r["enabled"] for r in p["observations"])
    assert all(d["validation"] for d in p["diagnostics"] if d["star_id"] == "1")


def test_opposite_validation_errors_do_not_cancel_into_a_pass():
    rows = [replace(r, instrumental_magnitude=r.instrumental_magnitude+
                    (.5 if r.exposure_id.endswith("0") else -.5 if r.exposure_id.endswith("1") else 0))
            if r.star_id == "1" else r for r in standards()]
    p = profile(rows)
    assert not p["validated"]
    assert p["validation_rms"]["B"] < 1e-8
    assert p["validation_observation_rms"]["B"] > .03


def test_explicit_user_override_retains_outlier_and_fails_validation():
    rows = [replace(r, instrumental_magnitude=r.instrumental_magnitude+4, reason="User selection")
            if r.star_id == "19" else r for r in standards()]
    p = profile(rows)
    assert not p["validated"]
    assert all(r["enabled"] for r in p["observations"])


def test_insufficient_color_and_duplicate_observations_rejected():
    with pytest.raises(ValueError, match="12 distinct"):
        profile([r for r in standards() if int(r.star_id) < 10])
    with pytest.raises(ValueError, match="Duplicate"):
        profile(standards()+[standards()[0]])
    rows = [replace(r, B=r.V+.5) for r in standards()]
    with pytest.raises(ValueError, match="0.8 mag"):
        profile(rows)


def test_ensemble_solution_preserves_inputs_and_propagates_color_covariance():
    original = science()
    result = apply_transformations(original, settings(profile()))
    target = [r for r in result if r.is_variable]
    assert all(transformation_applied(r) for r in target)
    assert [r.transformation["magnitude"] for r in target] == pytest.approx([12.9, 12])
    assert [r.transformation["color"] for r in target] == pytest.approx([.9, .9])
    assert target[0].transformation["group"] == target[1].transformation["group"]
    assert target[0].transformation["error"] > 0
    assert target[0].standard_magnitude == 12.2
    assert all(not row.transformation for row in original)


@pytest.mark.parametrize("change,reason", [
    (lambda s, rows: setattr(s, "camera", "Different"), "Instrument"),
    (lambda s, rows: setattr(s, "transformation_max_pair_seconds", 1), "paired"),
    (lambda s, rows: [setattr(r, "observation_time", datetime(2028, 1, 1, 1)) for r in rows], "one year"),
    (lambda s, rows: [setattr(r, "filter_name", "G") for r in rows], "not mapped"),
    (lambda s, rows: [setattr(r, "band_magnitudes", {}) for r in rows if r.is_reference], "comparison"),
])
def test_ineligible_science_retains_untransformed_values(change, reason):
    config, rows = settings(profile()), science()
    change(config, rows)
    result = apply_transformations(rows, config)
    targets = [r for r in result if r.is_variable]
    assert all(not transformation_applied(r) and reason in r.transformation["reason"] for r in targets)


def test_extrapolation_and_check_star_self_calibration_blocked():
    result = apply_transformations(science(color=3), settings(profile()))
    assert all("outside" in r.transformation["reason"] for r in result if r.is_variable)
    rows = science(comparisons=1)
    for r in rows:
        if r.is_reference:
            r.is_check = True
    assert not any(transformation_applied(r) for r in apply_transformations(rows, settings(profile())))


def test_profile_csv_roundtrip_and_import_recomputes_validation(tmp_path):
    p = profile()
    path = tmp_path / "profile.json"
    p["coefficients"]["Tb_bv"] = 100  # import derives coefficients from observations
    path.write_text(json.dumps(p))
    loaded = load_profile(path)
    assert loaded["coefficients"]["Tb_bv"] == pytest.approx(.12)
    csv_path = tmp_path / "standards.csv"
    write_standard_observations(csv_path, standards())
    assert read_standard_observations(csv_path) == standards()


def test_modified_saved_profile_is_not_applied():
    p = profile()
    p["coefficients"]["Tb_bv"] = 100
    result = apply_transformations(science(), settings(p))
    assert not any(transformation_applied(r) for r in result)


def test_export_uses_actual_transformation_not_checkbox():
    from photometry_app.core.exporters import ScienceExportMetadata, preview_aavso_extended_export
    rows = apply_transformations(science(), settings(profile()))
    report = ProcessingReport("Field", 2, 2, None, measurements=rows)
    preview = preview_aavso_extended_export(report, science_metadata=ScienceExportMetadata(filter_system="CV"))
    # Public preview returns preflight; inspect the same observation builder used by export.
    from photometry_app.core.exporters import _build_aavso_extended_observations
    observations, preflight = _build_aavso_extended_observations(report, rows, science_metadata=ScienceExportMetadata(filter_system="CV"))
    assert observations
    assert {r.filter_code for r in observations} == {"B", "V"}
    assert all(r.export_line.split(",")[5] == "YES" for r in observations)
    assert [r.magnitude for r in observations] == pytest.approx([12.9, 12])
    assert all("Joint B/V ensemble" in r.export_line for r in observations)
    original = science()
    report.measurements = original
    observations, _ = _build_aavso_extended_observations(report, original, science_metadata=ScienceExportMetadata(filter_system="V", transformed=True))
    assert {r.filter_code for r in observations} == {"TB", "TG"}
    assert all(r.export_line.split(",")[5] == "NO" for r in observations)


def test_cached_measurement_preserves_transformation():
    from photometry_app.core.pipeline import _serialize_processing_report_cache_value, _deserialize_processing_report_cache_value
    rows = apply_transformations(science(), settings(profile()))
    copied = _deserialize_processing_report_cache_value(_serialize_processing_report_cache_value(rows[0]))
    assert copied.transformation == rows[0].transformation


def test_transformed_curve_values_are_separate():
    from photometry_app.core.matching import build_light_curve_series
    from photometry_app.core.plotting import _light_curve_point_value, _light_curve_point_error
    rows = apply_transformations(science(), settings(profile()))
    curve = build_light_curve_series(rows)[0]
    assert _light_curve_point_value(curve.points[0], "transformed_magnitude") == pytest.approx(12.9)
    assert _light_curve_point_error(curve.points[0], "transformed_magnitude") > 0
    assert _light_curve_point_value(curve.points[0], "standard_magnitude") == 12.2


def test_settings_roundtrip_and_cache_identity(tmp_path):
    from photometry_app.core.settings import _settings_from_payload, _settings_payload
    from photometry_app.core.pipeline import _processing_report_cache_inputs
    p = profile()
    config = _settings_from_payload({}, tmp_path / "settings.json", False)
    config.transformation_profiles = [p]
    config.transformation_profile_id = p["id"]
    config.transformation_enabled = True
    config.transformation_optical_setup = p["optical_setup"]
    result = _settings_from_payload(_settings_payload(config, tmp_path), tmp_path / "settings.json", False)
    assert result.transformation_profiles == [p]
    assert result.transformation_enabled
    before = _processing_report_cache_inputs([], result, "Field", None)
    result.transformation_max_pair_seconds = 25
    assert _processing_report_cache_inputs([], result, "Field", None) != before


def test_standard_image_measurement_uses_standard_catalog_and_count_rates(tmp_path, monkeypatch):
    from astropy.io import fits
    from photometry_app.core.models import CatalogStar, PlateSolveResult, SolvedField, WcsStatus
    from photometry_app.core.settings import _settings_from_payload
    from photometry_app.core.transformation_calibration import measure_standard_images
    from photometry_app.core.pipeline import PhotometryPipeline
    from photometry_app.core import transformation_calibration as module
    paths = []
    for band in ("TB", "TG"):
        path = tmp_path / f"{band}.fits"
        fits.PrimaryHDU(np.zeros((100, 100)), header=fits.Header({"FILTER": band, "EXPTIME": 30., "DATE-OBS": "2026-10-01T00:00:00", "AIRMASS": 1.1})).writeto(path)
        paths.append(path)
    config = _settings_from_payload({}, tmp_path / "settings.json", False)
    fields = [SolvedField(10, 20, .1, 100, 100, p) for p in paths]
    monkeypatch.setattr(PhotometryPipeline, "_resolve_summary_fields", lambda self, scans, settings, progress: [
        (scan, PlateSolveResult(scan.path, WcsStatus.SOLVED, field)) for scan, field in zip(scans, fields)])
    star = CatalogStar("aavso-vsp", "1", "Comp", 10., 20., 11., False,
                       metadata={"band_magnitudes": {"B": {"mag": 11.4, "error": .01, "source": "vsp"}, "V": {"mag": 11., "error": .01, "source": "vsp"}}})
    calls = []
    monkeypatch.setattr(module, "query_vsp_field", lambda field, **kwargs: (calls.append(kwargs) or [star], "X123"))
    monkeypatch.setattr(module, "resolve_aperture_profile", lambda *args: SimpleNamespace(aperture_radius=3., annulus_inner_radius=5., annulus_outer_radius=8., fwhm_pixels=2.))
    monkeypatch.setattr(module, "measure_targets", lambda path, scan, *args, **kwargs: [
        replace(science()[1], source_id="1", file_path=path, filter_name=scan.metadata.filter_name, instrumental_magnitude=-10., flux=10000., flux_error=20.)])
    result, provenance = measure_standard_images(paths, config, "TB", "TG")
    assert len(result) == 2 and all(r.enabled for r in result)
    assert result[0].instrumental_magnitude == pytest.approx(-10+2.5*np.log10(30))
    assert {r.band for r in result} == {"B", "V"}
    assert all(call["standard_field"] for call in calls)
    assert "X123" in provenance

    # A faint catalog entry recentred onto this same comparison must not
    # veto the correctly associated star through the overlap check.
    neighbor = replace(star, source_id="neighbor")
    monkeypatch.setattr(module, "query_vsp_field", lambda *args, **kwargs: ([star, neighbor], "X123"))
    monkeypatch.setattr(module, "measure_targets", lambda path, scan, *args, **kwargs: [
        replace(science()[1], source_id=source, file_path=path, filter_name=scan.metadata.filter_name,
                instrumental_magnitude=-10., flux=10000., flux_error=20.) for source in ("1", "neighbor")])
    monkeypatch.setattr(module, "_standard_position_rejections", lambda *args: {"neighbor": "Catalog-position mismatch"})
    result, _ = measure_standard_images(paths, config, "TB", "TG")
    assert all(r.enabled for r in result if r.star_id == "1")
    assert all(not r.enabled and "Catalog-position mismatch" in r.reason for r in result if r.star_id == "neighbor")


def test_binned_and_raw_measurements_are_not_transformed():
    config = settings(profile())
    rows = [replace(r, flags=["Derived SNR bin from 3 measurement(s)."] if r.is_variable else []) for r in science()]
    assert not any(transformation_applied(r) for r in apply_transformations(rows, config))
    rows = [replace(r, file_path=r.file_path.with_suffix(".cr2")) for r in science()]
    assert not any(transformation_applied(r) for r in apply_transformations(rows, config))


def test_solved_field_size_is_checked_even_without_wcs_in_original_image():
    rows = science()
    result = apply_transformations(rows, settings(profile()), field_radii={r.file_path: .8 for r in rows})
    assert all("Field exceeds" in r.transformation["reason"] for r in result if r.is_variable)


def test_pairing_uses_exposure_midpoints(tmp_path):
    from astropy.io import fits
    rows = science()
    for row in rows:
        path = tmp_path / row.file_path
        if not path.exists():
            fits.PrimaryHDU(np.zeros((4, 4)), header=fits.Header({"EXPTIME": 600. if row.filter_name == "TB" else 10., "AIRMASS": 1.2})).writeto(path)
        row.file_path = path
    # Starts differ by 30s, but midpoints differ by 265s, outside the 120s limit.
    result = apply_transformations(rows, settings(profile()))
    assert not any(transformation_applied(r) for r in result)


def test_snr_limit_updates_exclusions_and_reason_categories():
    from photometry_app.core.transformation import MAG_FACTOR, apply_calibration_snr_limit, reason_category
    faint = StandardObservation("a", "e", "B", 1, MAG_FACTOR / 40, 12, 11, .01, .01, enabled=False, reason="SNR below 100")
    blend = StandardObservation("b", "e", "B", 1, MAG_FACTOR / 40, 12, 11, .01, .01, enabled=False,
                                reason="SNR below 100; Overlapping standard-star apertures")
    chosen = StandardObservation("c", "e", "V", 1, MAG_FACTOR / 40, 12, 11, .01, .01, enabled=True,
                                 reason="SNR below 100; User selection")
    bright = StandardObservation("d", "e", "V", 1, MAG_FACTOR / 200, 12, 11, .01, .01, enabled=True)
    rows = apply_calibration_snr_limit([faint, blend, chosen, bright], 30)
    assert rows[0].enabled and rows[0].reason == ""
    assert not rows[1].enabled and rows[1].reason == "Overlapping standard-star apertures"
    assert rows[2].enabled and rows[2].reason == ""
    assert rows[3].enabled and rows[3].reason == ""
    stricter = apply_calibration_snr_limit([faint, blend, chosen, bright], 250)
    assert not stricter[0].enabled and stricter[0].reason == "SNR below 250"
    assert stricter[2].enabled and "User selection" in stricter[2].reason
    assert not stricter[3].enabled and stricter[3].reason == "SNR below 250"
    assert reason_category("SNR below 100; Overlapping standard-star apertures") == "SNR; Overlapping apertures"
    assert reason_category("") == "Included"
