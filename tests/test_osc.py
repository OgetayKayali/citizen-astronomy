from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from astropy.io import fits
from astropy.wcs import WCS, Sip

from photometry_app.core.osc import (OscCameraProfile, OscPreparationRequest, cfa_pattern,
    extract_channels, normalized_start, prepare_osc_sequence, read_mosaic)
from photometry_app.core.image_io import read_differential_photometry_image_data
from photometry_app.core.scanner import inspect_fits_file
from photometry_app.core.models import CatalogStar, WcsStatus
from photometry_app.core.photometry import measure_targets


def header():
    wcs = WCS(naxis=2)
    wcs.wcs.crpix = [64.5, 64.5]
    wcs.wcs.crval = [150, 30]
    wcs.wcs.cd = np.array([[-.001, .0001], [.0001, .001]])
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    h = wcs.to_header()
    h.update(BAYERPAT="RGGB", EXPTIME=10., **{"DATE-OBS": "2026-10-27T03:00:10"})
    h.update(EGAIN=2., RDNOISE=4., SATURATE=50000., OBJECT="test", INSTRUME="test osc")
    return h


def write(path, data, h, variance=None, saturation=None):
    hdus = [fits.PrimaryHDU(np.asarray(data, dtype=np.float32), h)]
    if variance is not None:
        hdus.append(fits.ImageHDU(np.asarray(variance, dtype=np.float32), name="VARIANCE"))
    if saturation is not None:
        hdus.append(fits.ImageHDU(np.asarray(saturation, dtype=np.float32), name="SATFRAC"))
    fits.HDUList(hdus).writeto(path)
    return path


@pytest.mark.parametrize("pattern", ["RGGB", "BGGR", "GBRG", "GRBG"])
def test_extract_exact_native_samples_and_variance(pattern):
    data = np.arange(35, dtype=float).reshape(5, 7)
    variance = np.full(data.shape, 3.)
    sat = data / 100
    result = {channel: (out, var, frac, h) for channel, out, var, frac, h in
              extract_channels(data, variance, sat, header(), pattern, ("TG", "TB", "TR"))}
    for channel, (out, var, frac, h) in result.items():
        pos = [(i // 2, i % 2) for i, color in enumerate(pattern) if color == channel[-1]]
        expected = sum(data[y:4:2, x:6:2] for y, x in pos)
        np.testing.assert_array_equal(out, expected)
        np.testing.assert_array_equal(var, np.full((2, 3), 3 * len(pos)))
        np.testing.assert_array_equal(frac, np.maximum.reduce([sat[y:4:2, x:6:2] for y, x in pos]))
        assert "BAYERPAT" not in h
        assert h["FILTER"] == channel and h["OBSTYPE"] == "DSLR"


def test_bayer_offsets_and_orientation():
    h = fits.Header({"BAYERPAT": "RGGB", "XBAYROFF": 1})
    assert cfa_pattern(h, (6, 6)) == "GRBG"
    h["YBAYROFF"] = 1
    assert cfa_pattern(h, (6, 6)) == "BGGR"
    h["ROWORDER"] = "BOTTOM-UP"
    assert cfa_pattern(h, (6, 6)) == "GRBG"
    assert cfa_pattern(h, (7, 7)) == "BGGR"
    assert cfa_pattern(h, (6, 6), "RGGB") == "RGGB"
    h["XBAYROFF"] = .5
    with pytest.raises(ValueError, match="integer"):
        cfa_pattern(h, (6, 6))


@pytest.mark.parametrize("sip", [False, True])
@pytest.mark.parametrize("cd", [False, True])
def test_wcs_matches_sample_centers(sip, cd):
    h = header()
    original = WCS(h)
    if sip:
        a, b = np.zeros((3, 3)), np.zeros((3, 3))
        a[2, 0], b[0, 2] = 1e-4, -1e-4
        original.sip = Sip(a, b, None, None, original.wcs.crpix)
        original.wcs.ctype = ["RA---TAN-SIP", "DEC--TAN-SIP"]
        h.update(original.to_header(relax=True))
    if cd:
        matrix = original.pixel_scale_matrix
        for key in list(h):
            if key.startswith("PC") or key.startswith("CDELT"):
                del h[key]
        for i in range(2):
            for j in range(2):
                h[f"CD{i+1}_{j+1}"] = matrix[i, j]
    original = WCS(h)
    data = np.ones((128, 128))
    for channel, _, _, _, out_header in extract_channels(data, data, data, h, "RGGB"):
        xy = np.array([[10., 20.], [31., 31.], [50., 50.]])
        offset = np.array([.5, .5]) if channel == "TG" else np.array([1., 1.])
        np.testing.assert_allclose(WCS(out_header).all_pix2world(xy, 0), original.all_pix2world(2 * xy + offset, 0), atol=1e-10)


def test_end_local_timestamp_and_dst():
    p = OscCameraProfile(timestamp_timezone="America/New_York", timestamp_reference="end")
    stamp = normalized_start(header(), p, 10)
    assert stamp == datetime(2026, 10, 27, 7, 0, 0, tzinfo=timezone.utc)
    h = header()
    h["DATE-OBS"] = "2026-10-27T03:00:10+02:00"
    assert normalized_start(h, p, 10).hour == 1
    h["DATE-OBS"] = "2026-11-01T01:30:00"
    with pytest.raises(ValueError, match="Ambiguous"):
        normalized_start(h, p, 10)
    h["DATE-OBS"] = "2026-03-08T02:30:00"
    with pytest.raises(ValueError, match="Ambiguous|Nonexistent"):
        normalized_start(h, p, 10)
    p = replace(p, timestamp_timezone="UTC-04:00")
    assert normalized_start(h, p, 10).utcoffset().total_seconds() == 0


def test_channel_optics_metadata_does_not_double_count_binning():
    from photometry_app.core.local_wcs import _binning_multiplier
    h = header()
    h.update(XPIXSZ=3., YPIXSZ=3., XBINNING=1, YBINNING=1, SCALE=2.)
    data = np.ones((20, 20))
    out = next(extract_channels(data, data, data, h, "RGGB"))[-1]
    assert out["XPIXSZ"] == 6.
    assert out["SCALE"] == 4.
    assert out["XBINNING"] == 2
    assert _binning_multiplier(out, "XPIXSZ", "XBINNING") == 1.


def make_request(tmp_path, shape=(128, 128)):
    h = header()
    bias = np.full(shape, 100.)
    # Different CFA illumination must not become a false G1/G2 imbalance.
    flat = np.full(shape, 1100.)
    flat[0::2, 1::2] = 2100.
    flat[1::2, 0::2] = 3100.
    flat[1::2, 1::2] = 600.
    signal = np.full(shape, 300.)
    y, x = np.indices(shape)
    for cx, cy, amplitude in ((40, 50, 5000), (85, 75, 10000)):
        signal += amplitude * np.exp(-((x - cx)**2 + (y - cy)**2) / (2 * 4.**2))
    science = write(tmp_path / "science.fits", signal + bias, h)
    biasfile = write(tmp_path / "bias.fits", bias, h, np.ones(shape))
    flatfile = write(tmp_path / "flat.fits", flat, h, np.ones(shape) * 4)
    return OscPreparationRequest(science, tmp_path / "output", bias_path=biasfile, flat_path=flatfile), signal


def test_preparation_calibrates_native_mosaic_and_measures_flux(tmp_path):
    request, signal = make_request(tmp_path)
    products, warnings = prepare_osc_sequence(request)
    assert len(products) == 2
    assert warnings  # no dark-flats supplied
    for path in products:
        with fits.open(path) as hdus:
            assert {hdu.name for hdu in hdus} == {"PRIMARY", "VARIANCE", "SATFRAC"}
            data = hdus[0].data
            assert data.shape == (64, 64)
            assert hdus[0].header["DATE-OBS"].endswith("Z")
            if hdus[0].header["FILTER"] == "TG":
                np.testing.assert_allclose(data, signal[0::2, 1::2] + signal[1::2, 0::2], rtol=1e-6)
                # Green saturation stores the maximum original sample, not sum.
                assert np.max(hdus["SATFRAC"].data) < 1
        scan = inspect_fits_file(path, "test")
        assert scan.wcs_status == WcsStatus.SOLVED
        sky = WCS(header()).all_pix2world([[40, 50], [85, 75]], 0)
        stars = [CatalogStar("test", str(i), str(i), *pos, 12., False) for i, pos in enumerate(sky)]
        rows = measure_targets(path, scan, path, stars, [], aperture_radius=7, annulus_inner_radius=9,
                               annulus_outer_radius=13, frame_edge_margin_percent=0)
        assert len(rows) == 2
        assert all(row.flux > 0 and row.flux_error > 0 for row in rows)
        assert abs(rows[0].instrumental_magnitude - rows[1].instrumental_magnitude - 2.5*np.log10(2)) < .01
    manifest = json.loads((request.output_directory / "osc-preparation.json").read_text())
    assert manifest["status"] == "complete"
    with pytest.raises(ValueError, match="already exists"):
        prepare_osc_sequence(request)


def test_saturation_survives_calibration_and_green_sum(tmp_path):
    request, _ = make_request(tmp_path)
    with fits.open(request.science_path, mode="update") as hdus:
        hdus[0].data[50, 41] = 60000
    products, _ = prepare_osc_sequence(request)
    path = next(p for p in products if p.stem.endswith("TG"))
    sky = WCS(header()).all_pix2world([[40, 50]], 0)[0]
    star = CatalogStar("test", "1", "1", *sky, 12., False)
    rows = measure_targets(path, inspect_fits_file(path, "test"), path, [star], [],
                           aperture_radius=7, annulus_inner_radius=9, annulus_outer_radius=13, frame_edge_margin_percent=0)
    assert rows[0].is_saturated
    assert rows[0].peak_pixel_value >= 1


def test_raw_mosaics_rejected_by_science_reader_and_scanner(tmp_path):
    path = write(tmp_path / "raw.fits", np.ones((20, 20)), header())
    with pytest.raises(ValueError, match="Prepare OSC"):
        read_differential_photometry_image_data(path)
    assert inspect_fits_file(path, "test").wcs_status == WcsStatus.INVALID


def test_calibrated_input_requires_uncertainties_and_original_saturation(tmp_path):
    p = write(tmp_path / "raw.fits", np.ones((20, 20)), header())
    request = OscPreparationRequest(p, tmp_path / "output", already_calibrated=True)
    with pytest.raises(ValueError, match="VARIANCE"):
        prepare_osc_sequence(request)
    manifest = json.loads((request.output_directory / "osc-preparation.json").read_text())
    assert manifest["status"] == "incomplete"


def test_missing_metadata_not_guessed(tmp_path):
    request, _ = make_request(tmp_path)
    with fits.open(request.science_path, mode="update") as hdus:
        del hdus[0].header["EGAIN"]
    with pytest.raises(ValueError, match="gain"):
        prepare_osc_sequence(request)


def test_unicode_camera_profile_preserved_in_manifest_and_valid_fits(tmp_path):
    request, _ = make_request(tmp_path)
    name = "DWARF Mini — verify firmware timing"
    products, _ = prepare_osc_sequence(replace(request, profile=OscCameraProfile(name=name)))
    with fits.open(products[0]) as hdus:
        hdus.verify("exception")
        assert "DWARF Mini" in hdus[0].header["OSCPROF"]
    manifest = json.loads((request.output_directory / "osc-preparation.json").read_text())
    assert manifest["profile"]["name"] == name


def test_raw_decoder_never_calls_postprocess(tmp_path):
    class Raw:
        raw_pattern = np.array([[0, 1], [3, 2]])
        raw_colors_visible = np.tile(raw_pattern, (2, 3))
        raw_image_visible = np.arange(24).reshape(4, 6)
        color_desc = b"RGBG"
        white_level = 4095
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def postprocess(self, **kwargs): raise AssertionError("No demosaic allowed")
    with patch("photometry_app.core.osc.read_header", return_value=header()), patch("photometry_app.core.image_io._open_raw_camera_image", return_value=Raw()):
        data, h, pattern = read_mosaic(tmp_path / "test.cr2", OscCameraProfile())
    np.testing.assert_array_equal(data, Raw.raw_image_visible)
    assert pattern == "RGGB"


def test_no_dark_scaling(tmp_path):
    request, _ = make_request(tmp_path)
    h = header()
    h["EXPTIME"] = 20
    dark = write(tmp_path / "dark.fits", np.full((128, 128), 110.), h, np.ones((128, 128)))
    with pytest.raises(ValueError, match="must match"):
        prepare_osc_sequence(replace(request, dark_path=dark))


def test_bad_flat_pixels_not_interpolated_and_negative_values_retained(tmp_path):
    request, _ = make_request(tmp_path)
    with fits.open(request.science_path, mode="update") as hdus:
        hdus[0].data[0, 1] = 50
        hdus[0].data[1, 0] = 50
    with fits.open(request.flat_path, mode="update") as hdus:
        hdus[0].data[2, 3] = 0
    products, _ = prepare_osc_sequence(request)
    with fits.open(next(p for p in products if p.stem.endswith("TG"))) as hdus:
        assert hdus[0].data[0, 0] == -100
        assert np.isnan(hdus[0].data[1, 1])
        assert np.isnan(hdus["VARIANCE"].data[1, 1])


def test_pipeline_never_reuses_green_wcs_for_blue_and_caches_auid(tmp_path):
    from photometry_app.core.pipeline import _aligned_frame_reuse_key, _measurement_from_payload, _measurement_to_payload
    from photometry_app.core.settings import AppSettings
    from test_transformation import science
    request, _ = make_request(tmp_path)
    products, _ = prepare_osc_sequence(request)
    settings = AppSettings.from_root(tmp_path)
    settings.assume_aligned_images = True
    for path in products:
        assert _aligned_frame_reuse_key(inspect_fits_file(path, "test"), None, settings) is None
    row = replace(science()[0], auid="000-AAA-001")
    assert _measurement_from_payload(_measurement_to_payload(row)).auid == row.auid


def test_manual_apertures_use_native_variance_and_saturation(tmp_path):
    from photometry_app.core.models import ManualPhotometryConfig, ManualSourceConfig, ManualSourceRole
    from photometry_app.core.photometry import measure_manual_sources
    request, _ = make_request(tmp_path)
    products, _ = prepare_osc_sequence(request)
    path = next(p for p in products if p.stem.endswith("TG"))
    sky = WCS(header()).all_pix2world([[40, 50]], 0)[0]
    # Use the persisted manual-source schema rather than bypassing measurement.
    source = ManualSourceConfig(source_id="manual", name="target", role=ManualSourceRole.TARGET,
                                ra_deg=float(sky[0]), dec_deg=float(sky[1]), aperture_radius=7.,
                                annulus_inner_radius=9., annulus_outer_radius=13.,
                                reference_frame_name=path.name, reference_x=20., reference_y=25.)
    config = ManualPhotometryConfig(object_name="test", sources=[source])
    rows = measure_manual_sources(path, inspect_fits_file(path, "test"), path, config, frame_edge_margin_percent=0)
    assert rows[0].flux_error > 0
    with fits.open(path, mode="update") as hdus:
        hdus["VARIANCE"].data *= 4
        hdus["SATFRAC"].data[25, 20] = 1.1
    revised = measure_manual_sources(path, inspect_fits_file(path, "test"), path, config, frame_edge_margin_percent=0)
    assert revised[0].flux_error > rows[0].flux_error * 1.9
    assert revised[0].is_saturated


def test_wide_rotated_osc_calibration_crops_all_planes_without_resampling(tmp_path, monkeypatch):
    from test_transformation_calibration import write_field, solved_frame
    from photometry_app.core import transformation_calibration as calibration
    from photometry_app.core.settings import _settings_from_payload
    paths = [write_field(tmp_path / "a.fits", object_pixel=(20, 30)),
             write_field(tmp_path / "b.fits", object_pixel=(40, 30), rotation_deg=90)]
    for path in paths:
        with fits.open(path, mode="update") as hdus:
            hdus[0].header["OSCCHAN"] = "TG"
            hdus[0].header["FILTER"] = "TG"
            hdus.append(fits.ImageHDU(np.ones(hdus[0].data.shape) * 3, name="VARIANCE"))
            hdus.append(fits.ImageHDU(np.ones(hdus[0].data.shape) * .5, name="SATFRAC"))
    def no_alignment(*args, **kwargs):
        raise AssertionError("OSC photometry must not be resampled")
    monkeypatch.setattr(calibration, "_align_frames", no_alignment)
    settings = _settings_from_payload({}, tmp_path / "settings.json", False)
    result, _, _ = calibration.limit_wide_calibration_fields([solved_frame(p) for p in paths], settings)
    for scan, solve in result:
        with fits.open(scan.path) as hdus:
            assert hdus[0].data.shape == hdus["VARIANCE"].data.shape == hdus["SATFRAC"].data.shape
            np.testing.assert_array_equal(hdus["VARIANCE"].data, 3)
            np.testing.assert_array_equal(hdus["SATFRAC"].data, .5)
