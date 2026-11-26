from dataclasses import replace
from datetime import datetime, timezone

import numpy as np
import pytest
from astropy.io import fits

from photometry_app.core.models import CatalogStar
from photometry_app.core.exporters import (_aavso_extended_observation, _build_export_measurement_index,
    _write_aavso_extended_file, ScienceExportMetadata)
from photometry_app.core.transformation import apply_transformations, transformation_applied
from photometry_app.core.standard_magnitude import compute_standard_magnitude_context
from test_transformation import science, profile, settings


def osc_rows(tmp_path):
    rows = science(comparisons=2)
    for band in ("TB", "TG"):
        path = tmp_path / f"{band}.fits"
        from test_osc import header
        h = header()
        del h["BAYERPAT"]
        h.update({"OSCCHAN": band, "OSCFRAME": "exposure1", "OBSTYPE": "DSLR", "EXPTIME": 10., "FILTER": band, "OSCUNC": "PARTIAL"})
        fits.PrimaryHDU(np.ones((20, 20)), h).writeto(path)
        for row in rows:
            if row.filter_name == band:
                row.file_path = path
                row.observation_time = datetime(2026, 10, 2, 1, 0, tzinfo=timezone.utc)
                row.standard_catalog_band = "V" if band == "TG" else "B"
    return rows


def test_untransformed_osc_export_and_auid_precedence(tmp_path):
    rows = osc_rows(tmp_path)
    target = next(r for r in rows if r.is_variable and r.filter_name == "TG")
    target.comparison_source_ids = ["1"]
    reference = CatalogStar("vsp", "1", "Display name", 12, 23, 11., False, metadata={"auid": "000-AAA-001"})
    check = replace(next(r for r in rows if r.source_id == "2" and r.filter_name == "TG"),
                    source_id="manual-check", auid="000-AAA-002", source_name="Check display", is_reference=False, is_variable=False, is_check=True)
    rows.append(check)
    item, reason = _aavso_extended_observation(target, _build_export_measurement_index(rows), {"1": reference}, ScienceExportMetadata())
    assert reason is None
    fields = item.export_line.split(",")
    assert fields[4:6] == ["TG", "NO"]
    assert fields[7] == "000-AAA-001"
    assert fields[9] == "000-AAA-002"
    assert item.warnings and "uncertainty" in item.warnings[0]
    output = tmp_path / "aavso.txt"
    _write_aavso_extended_file([item], output, science_metadata=None)
    assert "#OBSTYPE=DSLR" in output.read_text()


def test_transformed_osc_export_preserves_camera_type_and_numeric_pair_group(tmp_path):
    rows = apply_transformations(osc_rows(tmp_path), settings(profile()))
    targets = [r for r in rows if r.is_variable]
    assert all(transformation_applied(r) for r in targets)
    index = _build_export_measurement_index(rows)
    items = [_aavso_extended_observation(row, index, {}, ScienceExportMetadata())[0] for row in targets]
    assert {i.filter_code for i in items} == {"B", "V"}
    output = tmp_path / "aavso.txt"
    _write_aavso_extended_file(items, output, science_metadata=None)
    text = output.read_text()
    assert "#OBSTYPE=DSLR" in text
    records = [line.split(",") for line in text.splitlines() if not line.startswith("#")]
    assert {row[5] for row in records} == {"YES"}
    assert {row[12] for row in records} == {"1"}


def test_ensemble_check_magnitude_and_no_mixed_obstype(tmp_path):
    rows = osc_rows(tmp_path)
    target = next(r for r in rows if r.is_variable and r.filter_name == "TG")
    check = replace(target, source_id="000-AAA-002", source_name="Check", is_variable=False,
                    is_check=True, standard_magnitude=10.75, instrumental_magnitude=-9.)
    rows.append(check)
    item, _ = _aavso_extended_observation(target, _build_export_measurement_index(rows), {}, ScienceExportMetadata())
    fields = item.export_line.split(",")
    assert fields[7:9] == ["ENSEMBLE", "na"]
    assert float(fields[10]) == 10.75
    assert "Comparison AUIDs/IDs:" in fields[-1]
    with pytest.raises(ValueError, match="one OBSTYPE"):
        _write_aavso_extended_file([item, replace(item, observation_type="CCD")], tmp_path / "mixed.txt", science_metadata=None)


def test_osc_gaia_g_fallback_cannot_be_submitted_as_tg(tmp_path):
    rows = osc_rows(tmp_path)
    target = next(r for r in rows if r.is_variable and r.filter_name == "TG")
    target.standard_catalog_source = "gaia-g"
    item, reason = _aavso_extended_observation(target, _build_export_measurement_index(rows), {}, ScienceExportMetadata())
    assert item is None and reason == "osc_requires_matching_catalog_passband"
    refs = [replace(r, band_magnitudes={"G": {"mag": 12., "source": "gaia-g"}}) for r in rows if r.is_reference and r.filter_name == "TG"]
    assert compute_standard_magnitude_context(target, refs)[0] is None


def test_osc_pairs_cannot_cross_original_exposures(tmp_path):
    rows = osc_rows(tmp_path)
    with fits.open(tmp_path / "TG.fits", mode="update") as hdus:
        hdus[0].header["OSCFRAME"] = "different-exposure"
    result = apply_transformations(rows, settings(profile()))
    assert not any(transformation_applied(r) for r in result if r.is_variable)

