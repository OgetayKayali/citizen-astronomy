"""Guided image discovery and automatic fitting, without network access."""
import json
from dataclasses import replace
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.io import fits

from photometry_app.core import transformation_calibration as calibration
from photometry_app.core.models import CatalogStar, PlateSolveResult, SolvedField, WcsStatus
from photometry_app.core.scanner import inspect_fits_file
from photometry_app.core.settings import _settings_from_payload
from photometry_app.core.transformation import fit_profile, load_profile
from photometry_app.core.wcs import celestial_wcs, extract_solved_field
from photometry_app.core.image_io import read_header_and_shape
from test_transformation import standards


def images(tmp_path, tokens):
    paths = []
    for index, token in enumerate(tokens):
        path = tmp_path / f"frame-{index}.fits"
        header = fits.Header({"FILTER": token, "INSTRUME": "Camera", "TELESCOP": "Scope"})
        fits.writeto(path, np.zeros((4, 4)), header)
        paths.append(path)
    return paths


def test_rgb_header_discovery_and_deduplication(tmp_path):
    paths = images(tmp_path, ["ZWO Blue", "ZWO Green", "ZWO Red", "ZWO L"])
    result = calibration.inspect_calibration_images(paths + paths, "rgb")
    assert result["blue"] == "ZWO Blue"
    assert result["visual"] == "ZWO Green"
    assert len(result["groups"]) == 4
    assert sum(map(len, result["groups"].values())) == 4
    assert result["cameras"] == ["Camera"]
    assert result["telescopes"] == ["Scope"]


@pytest.mark.parametrize("tokens,system,blue,visual", [
    (["B", "G", "V"], "rgb", "B", "G"),
    (["B", "G", "V"], "bv", "B", "V"),
    (["B", "Blue", "Green"], "rgb", "", "Green"),
    (["TB", "TG", "TR"], "rgb", "TB", "TG"),
    (["B", "Sloan g"], "rgb", "B", ""),
    (["B", "V"], "custom", "", ""),
])
def test_discovery_respects_system_and_ambiguity(tmp_path, tokens, system, blue, visual):
    result = calibration.inspect_calibration_images(images(tmp_path, tokens), system)
    assert (result["blue"], result["visual"]) == (blue, visual)


def test_missing_metadata_and_color_cubes_are_actionable(tmp_path):
    path = tmp_path / "color.fits"
    fits.writeto(path, np.zeros((3, 4, 4)))
    with pytest.raises(ValueError, match="separate 2D"):
        calibration.inspect_calibration_images([path], "rgb")
    path = tmp_path / "no-filter.fits"
    fits.writeto(path, np.zeros((4, 4)))
    with pytest.raises(ValueError, match="missing FILTER"):
        calibration.inspect_calibration_images([path], "rgb")


def options():
    return dict(name="RGB calibration", telescope="Scope", camera="Camera", optical_setup="RGB imaging set",
                blue_filter="B", visual_filter="G", calibration_date="2026-10-17", provenance="",
                equipment={"filter_system": "rgb", "brand": "Custom brand", "model": "RGB"})


def test_automatic_failure_retains_measurements_and_source(monkeypatch):
    rows = standards()[:6]
    monkeypatch.setattr(calibration, "measure_standard_images", lambda *args: (rows, "Chart 123"))
    result = calibration.calibrate_standard_images([], None, options())
    assert result["profile"] is None
    assert "12 distinct standards" in result["error"]
    assert result["rows"] == rows
    assert result["provenance"] == "Chart 123"
    assert result["calibration_date"] == "2026-10-01"


def test_automatic_snr_lowers_cutoff_for_paired_references_without_remeasuring(monkeypatch):
    from photometry_app.core.transformation import MAG_FACTOR
    rows = [replace(r, instrumental_error=MAG_FACTOR / (150 if int(r.star_id) % 3 == 0 else 60 if int(r.star_id) % 3 == 1 else 35))
            for r in standards()]
    calls = []
    def measure(*args):
        calls.append(args[-1])
        return rows, "Chart 123"
    monkeypatch.setattr(calibration, "measure_standard_images", measure)
    result = calibration.calibrate_standard_images([], None, options())
    assert calls == [20.]
    assert result["min_snr"] == 30.
    assert result["profile"]["validated"]
    assert len({r.star_id for r in result["rows"] if r.enabled}) == 20


def test_automatic_snr_preserves_hard_exclusions_and_floor(monkeypatch):
    from photometry_app.core.transformation import MAG_FACTOR
    rows = [replace(r, instrumental_error=MAG_FACTOR/10) if int(r.star_id) < 4 else r for r in standards()]
    rows = [replace(r, enabled=False, reason="Saturated or near saturation") if r.star_id == "19" else r for r in rows]
    monkeypatch.setattr(calibration, "measure_standard_images", lambda *args: (rows, "Chart 123"))
    result = calibration.calibrate_standard_images([], None, options())
    assert result["profile"]["validated"]
    assert result["min_snr"] == 100.  # Lowering it cannot add usable references.
    assert all(not r.enabled for r in result["rows"] if int(r.star_id) < 4 or r.star_id == "19")


def test_manual_snr_and_outlier_setting_are_honored(monkeypatch):
    from photometry_app.core.transformation import MAG_FACTOR
    rows = [replace(r, instrumental_error=MAG_FACTOR/40) for r in standards()]
    monkeypatch.setattr(calibration, "measure_standard_images", lambda *args: (rows, "Chart 123"))
    result = calibration.calibrate_standard_images([], None, {**options(), "reject_outliers": False}, min_snr=100)
    assert result["profile"] is None
    assert result["min_snr"] == 100
    assert all(not r.enabled for r in result["rows"])


def test_automatic_snr_does_not_search_for_a_passing_validation_score(monkeypatch):
    rows = [replace(r, instrumental_magnitude=r.instrumental_magnitude+3) if r.star_id == "1" else r for r in standards()]
    monkeypatch.setattr(calibration, "measure_standard_images", lambda *args: (rows, "Chart 123"))
    messages = []
    result = calibration.calibrate_standard_images([], None, options(), progress=messages.append)
    assert not result["profile"]["validated"]
    assert result["min_snr"] == 100
    assert len([m for m in messages if "Selecting reliable" in m]) == 1


def test_failed_screening_keeps_exclusion_reasons_for_review(monkeypatch):
    rows = [r for r in standards() if int(r.star_id) < 12]
    rows = [replace(r, instrumental_magnitude=r.instrumental_magnitude+4) if r.star_id == "0" else r for r in rows]
    monkeypatch.setattr(calibration, "measure_standard_images", lambda *args: (rows, "Chart 123"))
    result = calibration.calibrate_standard_images([], None, options())
    assert result["profile"] is None
    assert "After quality screening" in result["error"]
    assert all(not r.enabled and "Automatic outlier" in r.reason for r in result["rows"] if r.star_id == "0")
    assert "8 fitting and 3 independent validation stars remain" in result["error"]


def test_standard_positions_reject_bright_neighbor_but_accept_common_wcs_shift(monkeypatch):
    # Six sources agree on the WCS displacement; the seventh has latched onto
    # a neighbor. Its high SNR must not make it a reliable standard.
    class PixelWcs:
        def world_to_pixel_values(self, ra, dec):
            return ra, dec
    monkeypatch.setattr(calibration, "read_header_and_shape", lambda _: ({}, 100, 100))
    monkeypatch.setattr(calibration, "celestial_wcs", lambda _: PixelWcs())
    stars = {str(i): SimpleNamespace(ra_deg=10*i, dec_deg=10) for i in range(7)}
    measured = [SimpleNamespace(source_id=str(i), x=10*i+3+.02*i, y=9,
                 flux=10000, flux_error=10, flags=[], is_saturated=False, is_near_saturated=False) for i in range(7)]
    measured[-1].y = 13
    rejected = calibration._standard_position_rejections(measured, stars, Path('solved.fits'))
    assert set(rejected) == {'6'}
    assert 'Catalog-position mismatch' in rejected['6']
    measured[-1].y = 9
    assert calibration._standard_position_rejections(measured, stars, Path('solved.fits')) == {}


def test_standard_position_consensus_ignores_noisy_and_saturated_sources(monkeypatch):
    measured = [SimpleNamespace(source_id=str(i), x=i, y=0, flux=10000, flux_error=10,
                 flags=[], is_saturated=False, is_near_saturated=False) for i in range(7)]
    stars = {str(i): object() for i in range(7)}
    measured[0].is_saturated = True
    measured[1].flux_error = 1000
    # Only five eligible stars remain, so do not infer a frame displacement.
    assert calibration._standard_position_rejections(measured, stars, Path('missing.fits')) == {}


def write_field(path, *, shape=(90, 180), pixel_scale=0.02, object_pixel=(20, 30), rotation_deg=0.0,
                object_name="M67", ra=132.838375, dec=11.800667):
    height, width = shape
    row, column = object_pixel
    header = fits.Header()
    header["FILTER"] = "B"
    header["EXPTIME"] = 30.0
    header["DATE-OBS"] = "2026-10-01T00:00:00"
    header["AIRMASS"] = 1.2
    header["INSTRUME"] = "Camera"
    header["TELESCOP"] = "Scope"
    if object_name:
        header["OBJECT"] = object_name
    header["CTYPE1"] = "RA---TAN"
    header["CTYPE2"] = "DEC--TAN"
    header["CRVAL1"] = ra
    header["CRVAL2"] = dec
    header["CRPIX1"] = column + 1
    header["CRPIX2"] = row + 1
    header["EQUINOX"] = 2000.0
    header["RADESYS"] = "ICRS"
    theta = math.radians(rotation_deg)
    cosine, sine = math.cos(theta), math.sin(theta)
    header["CD1_1"] = -pixel_scale * cosine
    header["CD1_2"] = pixel_scale * sine
    header["CD2_1"] = pixel_scale * sine
    header["CD2_2"] = pixel_scale * cosine
    data = np.arange(height * width, dtype=np.int32).reshape(height, width)
    fits.PrimaryHDU(data=data, header=header).writeto(path, overwrite=True)
    return path


def solved_frame(path):
    scan = inspect_fits_file(path, path.parent.name)
    header, width, height = read_header_and_shape(path)
    field = extract_solved_field(header, width, height, path)
    return scan, PlateSolveResult(source_path=path, status=WcsStatus.SOLVED, solved_field=field)


def test_wide_field_is_cropped_around_m67_without_resampling(tmp_path):
    path = write_field(tmp_path / "m67-b.fits", object_pixel=(20, 30))
    settings = _settings_from_payload({}, tmp_path / "settings.json", False)
    prepared, note, sources = calibration.limit_wide_calibration_fields([solved_frame(path)], settings)
    scan, solve = prepared[0]
    assert solve.solved_field.radius_deg <= 0.5
    center = SkyCoord(solve.solved_field.center_ra_deg * u.deg, solve.solved_field.center_dec_deg * u.deg)
    m67 = SkyCoord(132.838375 * u.deg, 11.800667 * u.deg)
    assert center.separation(m67).arcmin < 2
    original = np.asarray(fits.getdata(path))
    cropped = np.asarray(fits.getdata(scan.path))
    assert cropped.size < original.size
    assert set(cropped.ravel().tolist()) <= set(original.ravel().tolist())
    assert sources[scan.path.resolve()] == path.resolve()
    assert "M67" in note
    assert scan.metadata.filter_name == "B"
    assert scan.metadata.exposure_seconds == 30


def test_dithered_frames_stay_unresampled(tmp_path, monkeypatch):
    settings = _settings_from_payload({}, tmp_path / "settings.json", False)
    first = write_field(tmp_path / "m67-a.fits", object_pixel=(20, 30))
    second = write_field(tmp_path / "m67-b.fits", object_pixel=(20, 45))

    def fail_reproject(*_args, **_kwargs):
        raise AssertionError("dithered frames should not be resampled")

    monkeypatch.setattr("photometry_app.core.alignment._reproject_image_data", fail_reproject)
    prepared, note, _sources = calibration.limit_wide_calibration_fields(
        [solved_frame(first), solved_frame(second)], settings,
    )
    assert all(solve.solved_field.radius_deg <= 0.5 for _scan, solve in prepared)
    assert "aligned" not in note.casefold()


def test_rotated_frames_are_aligned_onto_one_solution(tmp_path):
    settings = _settings_from_payload({}, tmp_path / "settings.json", False)
    first = write_field(tmp_path / "m67-a.fits", object_pixel=(20, 30))
    rotated = write_field(tmp_path / "m67-rotated.fits", object_pixel=(40, 30), rotation_deg=90)
    prepared, note, _sources = calibration.limit_wide_calibration_fields(
        [solved_frame(first), solved_frame(rotated)], settings,
    )
    assert "aligned" in note.casefold()
    assert any(scan.path.parent.name == "aligned" for scan, _solve in prepared)
    pixels = []
    for scan, _solve in prepared:
        header, _width, _height = read_header_and_shape(scan.path)
        x, y = celestial_wcs(header).world_to_pixel_values(132.838375, 11.800667)
        pixels.append((float(np.asarray(x).reshape(-1)[0]), float(np.asarray(y).reshape(-1)[0])))
    assert math.hypot(pixels[0][0] - pixels[1][0], pixels[0][1] - pixels[1][1]) < 1.5


def test_named_field_outside_the_frame_is_reported(tmp_path):
    path = write_field(tmp_path / "elsewhere.fits", object_name="M67", ra=180.0, dec=-30.0, object_pixel=(45, 90))
    settings = _settings_from_payload({}, tmp_path / "settings.json", False)
    with pytest.raises(ValueError, match="M67 is outside"):
        calibration.limit_wide_calibration_fields([solved_frame(path)], settings)


def test_wide_measurement_uses_the_crop_and_keeps_the_original_exposure(tmp_path, monkeypatch):
    path = write_field(tmp_path / "m67-b.fits")
    settings = _settings_from_payload({}, tmp_path / "settings.json", False)
    captured = {}

    class Pipeline:
        def _resolve_summary_fields(self, scans, _settings, _progress):
            return [solved_frame(scan.path) for scan in scans]

    def measure(image_path, _scan, wcs_path, _variables, standards, **_kwargs):
        header, width, height = read_header_and_shape(wcs_path)
        captured["path"] = image_path
        captured["radius"] = extract_solved_field(header, width, height, wcs_path).radius_deg
        return [SimpleNamespace(
            source_id=standards[0].source_id, instrumental_magnitude=-10.0, flux=10000.0, flux_error=10.0,
            is_saturated=False, is_near_saturated=False, flags=[], x=10.0, y=10.0,
        )]

    monkeypatch.setattr("photometry_app.core.pipeline.PhotometryPipeline", Pipeline)
    monkeypatch.setattr(calibration, "resolve_aperture_profile", lambda *_args, **_kwargs: SimpleNamespace(
        aperture_radius=5, annulus_inner_radius=8, annulus_outer_radius=12, fwhm_pixels=3,
    ))
    monkeypatch.setattr(calibration, "measure_targets", measure)
    monkeypatch.setattr(calibration, "query_vsp_field", lambda *_args, **_kwargs: ([CatalogStar(
        catalog="aavso-vsp", source_id="000-BLG-879", name="1", ra_deg=132.84, dec_deg=11.80,
        magnitude=12.0, is_variable=False,
        metadata={"band_magnitudes": {"B": {"mag": 12.5, "error": 0.01}, "V": {"mag": 12.0, "error": 0.01}}},
    )], "M67"))
    rows, provenance = calibration.measure_standard_images([path], settings, "B", "G")
    assert rows[0].exposure_id == str(path.resolve())
    assert captured["path"] != path
    assert captured["radius"] <= 0.5
    assert "M67" in provenance
    assert "Cropped to one degree" in provenance


def test_vsp_gateway_timeout_is_reported_as_a_service_failure(monkeypatch):
    from photometry_app.core.standard_magnitude import VspServiceError, query_vsp_field

    class Response:
        status_code = 504

        def raise_for_status(self):
            raise AssertionError("a gateway timeout is retried before it is treated as a failed chart")

        def json(self):
            raise AssertionError("a gateway timeout has no chart")

    monkeypatch.setattr("photometry_app.core.standard_magnitude.requests.get", lambda *_args, **_kwargs: Response())
    monkeypatch.setattr("photometry_app.core.standard_magnitude._pause_before_vsp_retry", lambda _attempt: None)
    field = SolvedField(132.838375, 11.800667, 0.49, 10, 10, Path("m67.fits"))
    with pytest.raises(VspServiceError, match="504"):
        query_vsp_field(field, standard_field=True, raise_service_errors=True)
    stars, chart = query_vsp_field(field, standard_field=True)
    assert stars == [] and chart is None
    message = calibration._vsp_unavailable_message("M67", "AAVSO VSP returned HTTP 504.")
    assert "M67" in message and "504" in message and "recognized" in message


def test_equipment_metadata_survives_profile_revalidation(tmp_path):
    kwargs = options()
    kwargs["provenance"] = "Synthetic standards"
    p = fit_profile(standards(), **kwargs)
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(p), encoding="utf-8")
    restored = load_profile(path)
    assert restored["equipment"] == kwargs["equipment"]
    assert restored["id"] == p["id"]
