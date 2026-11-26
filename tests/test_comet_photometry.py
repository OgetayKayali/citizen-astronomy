from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import math

import numpy as np
import pytest
from astropy.io import fits
from astropy.wcs import WCS

from photometry_app.core.comet_photometry import (
    ApertureMeasurement, ComparisonStar, CometFrame, apass_comparison_stars,
    build_comet_stack_pair, comet_catalog_band, export_comet_measurements,
    fit_comet_zero_point, load_comet_session, measure_comet_aperture, save_comet_session,
)


def make_sequence(root: Path, *, size=160, count=3):
    wcs = WCS(naxis=2)
    wcs.wcs.crpix = [size / 2, size / 2]
    wcs.wcs.crval = [120, 20]
    wcs.wcs.cdelt = [-1 / 3600, 1 / 3600]
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    yy, xx = np.indices((size, size))
    frames = []
    for idx in range(count):
        exposure = 30.0 + 30 * idx
        cx, cy = size / 2 + 2 * idx, size / 2
        rate = 40 + 100 * np.exp(-((xx - cx)**2 + (yy - cy)**2) / 18)
        rate += 60 * np.exp(-((xx - 35)**2 + (yy - 40)**2) / 8)
        header = wcs.to_header()
        header["EXPTIME"] = exposure
        header["FILTER"] = "V"
        path = root / f"comet_{idx}.fits"
        fits.PrimaryHDU(rate * exposure, header).writeto(path)
        ra, dec = wcs.pixel_to_world_values(cx, cy)
        frames.append(CometFrame(path, datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=idx),
                                 exposure, float(ra), float(dec), "V"))
    return frames, wcs


def test_pair_preserves_count_rate_flux_and_tracks_motion(tmp_path):
    frames, _ = make_sequence(tmp_path)
    pair = build_comet_stack_pair(frames, reference_path=frames[0].path)
    measured = measure_comet_aperture(pair.comet, x=80, y=80, radius=15,
                                      sky_inner=20, sky_outer=30, valid_mask=pair.comet_coverage == 3)
    assert measured.flux == pytest.approx(100 * 2 * math.pi * 3**2, rel=0.005)
    assert np.unravel_index(np.nanargmax(pair.comet), pair.comet.shape) == (80, 80)
    assert pair.comet[80, 80] == pytest.approx(140, rel=0.001)
    assert pair.star_coverage[80, 80] == 3
    assert pair.comet_coverage[80, -1] < 3
    assert pair.provenance["frames"][1]["exposure"] == 60


def test_aperture_subpixel_flux_and_sky_offset_invariance():
    yy, xx = np.indices((101, 101))
    image = 500 * np.exp(-((xx - 50.3)**2 + (yy - 49.7)**2) / 8)
    first = measure_comet_aperture(image + 100, x=50.3, y=49.7, radius=12, sky_inner=17, sky_outer=25)
    second = measure_comet_aperture(image + 1200, x=50.3, y=49.7, radius=12, sky_inner=17, sky_outer=25)
    assert first.flux == pytest.approx(500 * 8 * math.pi, rel=0.0001)
    assert second.flux == pytest.approx(first.flux, rel=1e-8)
    assert first.coverage == pytest.approx(1)


def test_pair_aligns_dithered_wcs_and_masks_saturated_pixels(tmp_path):
    frames, wcs = make_sequence(tmp_path)
    yy, xx = np.indices((160, 160))
    # Shift the detector pointings by fractional pixels without changing the
    # world coordinates of the comet or reference star.
    for index, frame in enumerate(frames[1:], start=1):
        dx, dy = 3.25 * index, -2.5 * index
        shifted_wcs = wcs.deepcopy()
        shifted_wcs.wcs.crpix += [dx, dy]
        cx, cy = shifted_wcs.world_to_pixel_values(frame.ra_deg, frame.dec_deg)
        rate = 40 + 100 * np.exp(-((xx-cx)**2 + (yy-cy)**2) / 18)
        rate += 60 * np.exp(-((xx-35-dx)**2 + (yy-40-dy)**2) / 8)
        fits.PrimaryHDU(rate * frame.exposure, shifted_wcs.to_header()).writeto(frame.path, overwrite=True)
    pair = build_comet_stack_pair(frames, reference_path=frames[0].path)
    result = measure_comet_aperture(pair.comet, x=80, y=80, radius=15,
        sky_inner=20, sky_outer=30, valid_mask=pair.comet_coverage == 3)
    assert result.flux == pytest.approx(100 * 18 * math.pi, rel=0.005)
    star = measure_comet_aperture(pair.stars, x=35, y=40, radius=12,
        sky_inner=17, sky_outer=22, valid_mask=pair.star_coverage == 3)
    assert star.flux == pytest.approx(60 * 8 * math.pi, rel=0.005)
    with fits.open(frames[0].path, mode="update") as hdus:
        hdus[0].header["SATURATE"] = 130 * frames[0].exposure
    masked = build_comet_stack_pair(frames, reference_path=frames[0].path)
    invalid = measure_comet_aperture(masked.comet, x=80, y=80, radius=15,
        sky_inner=20, sky_outer=30, valid_mask=masked.comet_coverage == 3)
    assert invalid.instrumental_magnitude is None


def test_missing_aperture_data_never_returns_partial_magnitude():
    image = np.full((80, 80), 50.0)
    image[40, 40] = np.nan
    result = measure_comet_aperture(image, x=40, y=40, radius=8, sky_inner=12, sky_outer=20)
    assert result.flux is None
    assert result.instrumental_magnitude is None
    assert result.flags
    edge = measure_comet_aperture(np.ones((80, 80)), x=2, y=40, radius=8, sky_inner=12, sky_outer=20)
    assert edge.flux is None


def test_coverage_mask_and_nonpositive_flux():
    image = np.ones((80, 80))
    mask = np.ones_like(image, dtype=bool)
    mask[40, 40] = False
    invalid = measure_comet_aperture(image, x=40, y=40, radius=8, sky_inner=12, sky_outer=20, valid_mask=mask)
    assert invalid.instrumental_magnitude is None
    background = measure_comet_aperture(image, x=40, y=40, radius=8, sky_inner=12, sky_outer=20)
    assert background.flux == 0
    assert background.instrumental_magnitude is None
    with pytest.raises(ValueError, match="annulus"):
        measure_comet_aperture(image, x=40, y=40, radius=12, sky_inner=10, sky_outer=20)


def test_separate_sky_patch_measures_flux_and_rejects_overlap():
    yy, xx = np.indices((160, 160))
    image = 80 + 200 * np.exp(-((xx - 80)**2 + (yy - 80)**2) / 18)
    result = measure_comet_aperture(image, x=80, y=80, radius=15,
        sky_inner=10, sky_outer=20, sky_patch=(30, 30, 12))
    assert result.flux == pytest.approx(200 * 18 * math.pi, rel=0.005)
    with pytest.raises(ValueError, match="clear of"):
        measure_comet_aperture(image, x=80, y=80, radius=15,
            sky_inner=20, sky_outer=30, sky_patch=(90, 80, 12))


def test_even_one_missing_pixel_blocks_large_aperture_magnitude():
    image = np.ones((250, 250))
    image[125, 125] = np.nan
    result = measure_comet_aperture(image, x=125, y=125, radius=50, sky_inner=70, sky_outer=90)
    assert result.instrumental_magnitude is None


def test_zero_point_recovers_calibration_rejects_outlier_and_respects_selection():
    stars = [ComparisonStar(str(i), str(i), 0, 0, 12 + i, 0.02) for i in range(5)]
    measurements = {str(i): ApertureMeasurement(8, 100, 1, 12 + i - 25 + (2 if i == 4 else 0),
                                               0.01, 5, 1, 1) for i in range(5)}
    zero = fit_comet_zero_point(stars, measurements)
    assert zero.value == pytest.approx(25)
    assert zero.count == 4
    assert zero.rejected == ("4",)
    for star in stars[2:]:
        star.selected = False
    assert fit_comet_zero_point(stars, measurements) is None


def test_unknown_clear_and_wrong_passbands_do_not_become_v():
    for name in ("L", "Clear", "RGB", "R", "I", "Ha", "Unknown", ""):
        assert comet_catalog_band(name) is None
    assert comet_catalog_band("Johnson V") == "V"
    assert comet_catalog_band("Sloan r") == "SR"


def test_apass_never_falls_back_to_unrelated_catalog_magnitude(tmp_path):
    from photometry_app.core.models import CatalogStar
    frames, wcs = make_sequence(tmp_path)
    pair = build_comet_stack_pair(frames, reference_path=frames[0].path)
    ra, dec = wcs.pixel_to_world_values(35, 40)
    star = CatalogStar("apass9", "star", "Star", float(ra), float(dec), 12, False,
                       metadata={"band_magnitudes": {"V": {"mag": 12, "error": 0.03}}})
    assert len(apass_comparison_stars([star], pair, "V")) == 1
    assert apass_comparison_stars([star], pair, "SR") == []


def test_session_roundtrip_restores_stacks_and_detects_changed_sources(tmp_path):
    frames, _ = make_sequence(tmp_path)
    pair = build_comet_stack_pair(frames, reference_path=frames[0].path)
    target = tmp_path / "session.castcomet"
    settings = {"radius": 12, "target": "Test comet"}
    stars = [ComparisonStar("one", "One", 35, 40, 12, 0.03, 0.6, False)]
    save_comet_session(target, pair, settings, stars)
    restored, config, comps, changes = load_comet_session(target)
    np.testing.assert_allclose(restored.comet, pair.comet, equal_nan=True)
    np.testing.assert_array_equal(restored.comet_coverage, pair.comet_coverage)
    assert config == settings
    assert not comps[0].selected
    assert changes == []
    # Earlier v1 saves compressed both the NPZ and its outer ZIP member.
    import zipfile
    legacy = tmp_path / "legacy.castcomet"
    with zipfile.ZipFile(target) as current, zipfile.ZipFile(legacy, "w", zipfile.ZIP_DEFLATED) as archive:
        assert current.getinfo("stacks.npz").compress_type == zipfile.ZIP_STORED
        for name in current.namelist():
            archive.writestr(name, current.read(name))
    np.testing.assert_array_equal(load_comet_session(legacy)[0].comet, pair.comet)
    with frames[0].path.open("ab") as handle:
        handle.write(b"changed")
    assert "Changed original" in load_comet_session(target)[3][0]


def test_cancel_and_mixed_filters_stop_preparation(tmp_path):
    from dataclasses import replace
    frames, _ = make_sequence(tmp_path)
    with pytest.raises(InterruptedError):
        build_comet_stack_pair(frames, reference_path=frames[0].path, cancel=lambda: True)
    with pytest.raises(ValueError, match="one filter"):
        build_comet_stack_pair([frames[0], replace(frames[1], filter_name="B")], reference_path=frames[0].path)


def test_export_preserves_quality_and_empty_magnitudes(tmp_path):
    import csv
    path = tmp_path / "measurements.csv"
    export_comet_measurements(path, [{"target": "Comet", "magnitude": None, "quality": "Missing aperture pixels"}])
    with path.open(encoding="utf-8-sig", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["magnitude"] == ""
    assert row["quality"] == "Missing aperture pixels"
