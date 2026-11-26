"""Regressions discovered in the 300-exposure AE UMa field."""

import math
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import pytest
from astropy.io import fits
from astropy.wcs import WCS

from photometry_app.core.catalogs import CatalogService
from photometry_app.core.exporters import (
    ScienceExportMetadata, _aavso_extended_observation, _aavso_observation_timestamp,
    _build_export_measurement_index,
)
from photometry_app.core.matching import (
    apply_differential_photometry, apply_measurement_quality_analysis, build_light_curve_series,
    select_reference_stars,
)
from photometry_app.core.models import (
    CatalogStar, ManualPhotometryConfig, ManualSourceConfig, ManualSourceRole, PhotometryMeasurement,
    SolvedField,
)
from photometry_app.core.photometry import _estimate_flux_error, measure_manual_sources, measure_targets
from photometry_app.core.pipeline import _stars_on_detector
from photometry_app.core.scanner import inspect_fits_file
from photometry_app.core.settings import AppSettings
from photometry_app.core.snr_binning import SnrBinningSettings, _aggregate_measurement_bin
from photometry_app.core.standard_magnitude import (
    merge_band_magnitudes_by_sky_match, prefer_catalog_display_magnitude, query_vsp_field,
    seed_gaia_g_band_magnitude, set_band_magnitude,
)


def row(index, source="target", flux=1000, **changes):
    is_ref = source != "target"
    base = PhotometryMeasurement(
        source, source, "gaia-dr3" if is_ref else "vsx", "field", Path(f"frame{index}.fit"),
        datetime(2026, 3, 27, 2, tzinfo=timezone.utc) + timedelta(minutes=index), "L",
        144, 44, 30, 30, flux, 5.0, -2.5 * math.log10(flux), None, not is_ref, is_ref,
        exposure_seconds=30, catalog_magnitude=12, snr=30,
    )
    return replace(base, **changes)


def test_unequal_ensemble_tracks_transparency_and_detects_bad_reference():
    rows = []
    for index, transparency in enumerate([1, .8, 1.1, .7, 1, .95, 1.2]):
        rows.extend([row(index, flux=1000 * transparency),
                     row(index, "bright", 5000 * transparency),
                     row(index, "faint", (500 if index != 4 else 1100) * transparency)])
    targets = [r for r in apply_measurement_quality_analysis(apply_differential_photometry(rows))
               if r.is_variable]
    stable = [r for i, r in enumerate(targets) if i != 4]
    assert max(r.comparison_scatter for r in stable) < 1e-12
    assert np.ptp([r.differential_magnitude for r in stable]) < 1e-12
    assert not any(r.excluded_from_analysis for r in stable)
    assert targets[4].comparison_scatter > .18 and targets[4].excluded_from_analysis


def test_automatic_ensemble_favors_repeatably_precise_reserve():
    inputs = []
    for i in range(5):
        target = row(i, snr=30)
        near = row(i, "near", 500, snr=14, band_magnitudes={"V":{"mag":12, "source":"vsp", "error":.02}})
        reserve = replace(near, source_id="reserve", ra_deg=144.1, snr=45 if i != 2 else 25)
        inputs.extend([target, near, reserve])
    targets = [r for r in apply_differential_photometry(inputs, nearby_reference_count=1) if r.is_variable]
    assert all(r.comparison_source_ids == ["reserve"] for r in targets)


def test_partial_ensemble_remains_on_same_differential_scale():
    rows = []
    for i in range(8):
        duration = 10 if i % 2 else 30
        rows.extend([row(i, flux=100*duration, exposure_seconds=duration),
                     row(i, "bright", 500*duration, exposure_seconds=duration)])
        if i != 7:
            rows.append(row(i, "faint", 50*duration, exposure_seconds=duration))
    targets = [r for r in apply_differential_photometry(rows, nearby_reference_count=2) if r.is_variable]
    assert targets[-1].comparison_source_ids == ["bright"]
    assert np.ptp([r.differential_magnitude for r in targets]) < 1e-12
    red = apply_differential_photometry([replace(r, filter_name="R") for r in rows], nearby_reference_count=2)
    last = next(r for r in red if r.source_id == "target" and r.file_path == targets[-1].file_path)
    obs, reason = _aavso_extended_observation(last, _build_export_measurement_index(red), {}, ScienceExportMetadata())
    assert reason is None and obs.magnitude == pytest.approx(2.5 * math.log10(5))
    assert obs.export_line.split(",")[6:8] == ["DIF", "bright"]


def test_reselection_clears_results_and_recovers_missing_or_partial_ensemble():
    target = row(0, comparison_source_ids=["bright", "faint"])
    refs = [row(0, "bright", 5000), row(0, "faint", 500)]
    measured = apply_differential_photometry([target, *refs])[0]
    missing = apply_measurement_quality_analysis(apply_differential_photometry([measured]))[0]
    assert missing.excluded_from_analysis and missing.differential_magnitude is None
    assert missing.comparison_source_ids == [] and missing.comparison_reference_flux is None
    partial = apply_measurement_quality_analysis(apply_differential_photometry([missing, refs[1]]))[0]
    assert partial.comparison_source_ids == ["faint"] and not partial.excluded_from_analysis
    restored = apply_measurement_quality_analysis(apply_differential_photometry([partial, *refs]))[0]
    assert restored.comparison_source_ids == ["bright", "faint"]
    manual = replace(restored, excluded_from_analysis=True, exclusion_reasons=["Rejected by observer"])
    assert apply_measurement_quality_analysis(apply_differential_photometry([manual, *refs]))[0].excluded_from_analysis


def test_coherent_eclipse_survives_global_outlier_cut():
    values = [.01, -.02, 0, .01] * 6 + [.1, .35, .62, .65, .64, .37, .12] + [0, -.01, .01] * 6
    rows = [row(i, differential_magnitude=value, differential_magnitude_error=.02)
            for i, value in enumerate(values)]
    rows.append(replace(row(80), observation_time=None))
    assert not any(r.excluded_from_analysis for r in apply_measurement_quality_analysis(rows))
    points = build_light_curve_series(rows)[0].points
    assert points[0].observation_time == rows[0].observation_time + timedelta(seconds=15)
    assert points[-1].observation_time is None


@pytest.mark.parametrize("clear_filter", ["L", "Lum", "luminance", "Clear", "CV"])
def test_clear_passband_and_export_overrides(clear_filter):
    target = row(0, filter_name=clear_filter)
    good = row(0, "comparison", 2000, filter_name=clear_filter,
               band_magnitudes={"V": {"mag": 11.3, "source": "vsp", "error": .015}})
    other = replace(good, source_id="gaia_only", band_magnitudes={"G": {"mag": 9.1, "source": "gaia-g"}})
    data = apply_differential_photometry([target, good, other])
    assert data[0].standard_zero_point_source_count == 1
    assert data[0].standard_magnitude == pytest.approx(11.3 + 2.5 * math.log10(2))
    for override in ("", "V", "CR", "TB", "HA"):
        obs, reason = _aavso_extended_observation(data[0], _build_export_measurement_index(data), {},
                                                  ScienceExportMetadata(filter_system=override))
        assert reason is None
        assert obs.export_line.split(",")[4:8] == ["CV", "NO", "STD", "comparison"]
    g_only = apply_differential_photometry([target, other])
    assert g_only[0].standard_magnitude is None
    obs, reason = _aavso_extended_observation(g_only[0], _build_export_measurement_index(g_only), {},
                                              ScienceExportMetadata(filter_system="V"))
    assert obs is None and reason == "clear_requires_matching_catalog_passband"


def test_chart_url_fov_cache_and_catalog_provenance(tmp_path):
    field = SolvedField(144, 44, 3.245, 120, 100, tmp_path / "f.fit")
    response = Mock(status_code=200)
    response.json.return_value = {"photometry": [], "chartid": "X123"}
    with patch("photometry_app.core.standard_magnitude.requests.get", return_value=response) as get:
        query_vsp_field(field, aavso_chart_id="X123")
        assert get.call_args.args[0].endswith("/chart/X123/")
        assert "chartid" not in get.call_args.kwargs["params"]
        query_vsp_field(field, mag_limit=16)
        assert float(get.call_args.kwargs["params"]["fov"]) <= 180
    service = CatalogService(tmp_path)
    args = dict(include_gaia=True, include_variable_stars=True, include_exoplanets=True,
                gaia_max_magnitude=None, gaia_row_cap=None, variable_star_max_magnitude=None,
                exoplanet_max_magnitude=None)
    assert service._field_catalog_cache_key(field, **args) != service._field_catalog_cache_key(
        field, **args, aavso_chart_id="X123")
    star = CatalogStar("gaia-dr3", "1", "1", 144, 44, 10.46, False)
    seed_gaia_g_band_magnitude(star)
    donor = replace(star, source_id="AUID", catalog="aavso-vsp", metadata={})
    set_band_magnitude(donor, "V", 10.86, source="vsp")
    neighbor = replace(star, source_id="2", ra_deg=144.0002, metadata={})
    assert merge_band_magnitudes_by_sky_match([neighbor, star], [donor], source="vsp") == 1
    prefer_catalog_display_magnitude(star)
    seed_gaia_g_band_magnitude(star)
    seed_gaia_g_band_magnitude(donor)
    assert star.magnitude == 10.86 and star.metadata["band_magnitudes"]["G"]["mag"] == 10.46
    assert "G" not in donor.metadata["band_magnitudes"]
    assert "V" not in neighbor.metadata.get("band_magnitudes", {})


def test_automatic_pool_uses_known_variables_color_and_sequence_precision():
    target = CatalogStar("vsx", "target", "Target", 144, 44, 12, True, metadata={"bp_rp": .4})
    def star(name, ra, color, error):
        return CatalogStar("gaia-dr3", name, name, ra, 44, 12, False, metadata={
            "bp_rp": color, "band_magnitudes": {"V": {"source": "vsp", "mag": 12, "error": error}}})
    refs = [star("unselected variable", 144.02, .4, .01), star("red", 144.03, 1.6, .01),
            star("uncertain", 144.05, .4, .1), star("preferred", 144.09, .5, .02)]
    known = replace(refs[0], source_id="another VSX target", is_variable=True)
    selected = select_reference_stars(refs, [target, known], per_target_count=1,
                                      target_stars=[target], filter_names=("L",))
    assert [ref.name for ref in selected] == ["preferred"]


def test_crowded_aperture_detector_edges_gain_and_failed_manual_centroid(tmp_path):
    wcs = WCS(naxis=2)
    wcs.wcs.crpix, wcs.wcs.crval = [51, 51], [144, 44]
    wcs.wcs.cdelt = [-.001, .001]
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    source = tmp_path / "science.fit"
    yy, xx = np.indices((100, 100))
    signal = 1500 * np.exp(-((xx-50)**2 + (yy-50)**2) / 2)
    signal += 8000 * np.exp(-((xx-60)**2 + (yy-50)**2) / 2)
    hdr = wcs.to_header()
    hdr.update({"DATE-OBS": "2026-03-27T01:49:14.271", "EXPTIME": 30, "FILTER": "L", "EGAIN": .25})
    fits.writeto(source, signal + 100, hdr)
    scan = inspect_fits_file(source, "field")
    settings = AppSettings.defaults(tmp_path)
    field = SolvedField(144, 44, .1, 100, 100, source)
    center = CatalogStar("gaia", "center", "center", 144, 44, 12, False)
    outside = replace(center, source_id="edge", ra_deg=144.2)
    assert _stars_on_detector([outside, center], field, settings) == [center]
    sidecar = tmp_path / "field.wcs"
    fits.PrimaryHDU(header=wcs.to_header()).writeto(sidecar)
    assert _stars_on_detector([outside, center], replace(field, wcs_path=sidecar), settings) == [center]
    with patch("photometry_app.core.photometry._estimate_star_fwhm", return_value=9.49):
        m = measure_targets(source, scan, sidecar, [center], [], 5, 8, 12, settings=settings,
                            adaptive_frame_fwhm_pixels=1.5)[0]
    assert m.aperture_radius == pytest.approx(settings.aperture_radius_fwhm_scale * 1.5)
    assert m.midpoint_utc == datetime(2026, 3, 27, 1, 49, 29, 271000, tzinfo=timezone.utc)
    legacy = replace(m, exposure_seconds=None)
    assert legacy.midpoint_utc == m.midpoint_utc == _aavso_observation_timestamp(legacy)
    assert _estimate_flux_error(1000, 10, 30, 2, 4, .25)**2 == pytest.approx(4000 + 40 * (1 + 10/30))
    aperture = ManualSourceConfig("target", "target", ManualSourceRole.TARGET, 144, 44, source.name,
                                  50, 50, 5, 8, 12)
    config = ManualPhotometryConfig("field", sources=[aperture], fallback_to_wcs_on_centroid_failure=False)
    with patch("photometry_app.core.photometry._centroid_source", return_value=None):
        failed = measure_manual_sources(source, scan, source, config)[0]
    assert failed.flux is None and apply_measurement_quality_analysis([failed])[0].excluded_from_analysis


def test_mixed_exposure_bin_time_and_stale_standard():
    rows = [row(i, exposure_seconds=duration, differential_magnitude=.1, comparison_reference_flux=2000,
                standard_magnitude=12+i, standard_catalog_band="V", standard_catalog_source="vsp")
            for i, duration in enumerate((10, 50))]
    binned = _aggregate_measurement_bin(rows, bin_index=0, settings=SnrBinningSettings(),
                                        source_name="target", filter_name="L")
    expected = rows[0].observation_time + timedelta(seconds=(10 * 5 + 50 * 85) / 60)
    assert binned.midpoint_utc == expected == _aavso_observation_timestamp(binned)
    assert binned.standard_magnitude is None and binned.standard_catalog_band is None
