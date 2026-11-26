import numpy as np
import pytest
from astropy.wcs import WCS
from photutils.aperture import CircularAperture

from photometry_app.core.comet_morphology import build_radial_star_filter, comparison_star_fwhm, tail_geometry
from photometry_app.core.comet_photometry import measure_comet_aperture


def test_radial_filter_ring_mean_and_fractional_flux_match_independent_image():
    y, x = np.indices((75, 75))
    center = (37.1, 37.0)
    radius = 9.2
    dist = np.hypot(x-center[0], y-center[1])
    image = np.full(dist.shape, 20.0)
    image[dist < 4] += 70
    image[37, 46] = 1000
    image[37, 37] = 2000
    image[37, 47] = np.nan  # Excluded from local ring mean.
    coverage = np.ones(image.shape, dtype=np.uint16)
    coverage[38, 47] = 0
    image[38, 47] = 20_000  # An incomplete pixel is excluded from replacement statistics.
    expected = image.copy()
    ring = (dist >= 7) & (dist < 9)
    expected[37, 46] = np.mean(image[ring & np.isfinite(image) & (coverage == 1)])
    model = build_radial_star_filter(image, coverage, 1, center, radius, 7, 3)
    assert list(zip(model.y, model.x)) == [(37, 46)]
    assert model.replacement[0] == pytest.approx(expected[37, 46])
    params = dict(x=center[0], y=center[1], radius=radius, sky_inner=18, sky_outer=26)
    # Invalid pixels in this fixture are outside the fractional aperture.
    ref = measure_comet_aperture(expected, **params)
    measured = measure_comet_aperture(image, **params, radial_filter=model)
    raw = measure_comet_aperture(image, **params)
    weight = CircularAperture(center, radius).to_mask(method="exact").to_image(image.shape)[37, 46]
    assert 0 < weight < 1
    assert measured.replaced_pixels == 1
    assert measured.flux == pytest.approx(ref.flux)
    assert measured.unfiltered_flux == raw.flux
    assert raw.flux - measured.flux == pytest.approx(weight * (1000 - model.replacement[0]))
    assert measured.background == raw.background
    assert image[37, 46] == 1000
    median_model = build_radial_star_filter(image, coverage, 1, center, radius, 7, 3, statistic="median")
    assert list(zip(median_model.y, median_model.x)) == [(37, 46)]
    assert median_model.replacement[0] == np.median(image[ring & np.isfinite(image) & (coverage == 1)])
    assert median_model.replacement[0] < model.replacement[0]


def test_filter_skips_nonpositive_ring_means_and_protected_core():
    image = np.full((40, 40), -2.0)
    image[20, 28] = 10
    valid = np.ones(image.shape, dtype=int)
    assert build_radial_star_filter(image, valid, 1, (20, 20), 12).x.size == 0
    assert build_radial_star_filter(image, valid, 1, (20, 20), 5).x.size == 0


def test_rectangular_sky_uses_whole_clean_pixels_and_rejects_overlap():
    image = np.full((80, 80), 1000.)
    image[10:20, 3:11] = 50
    image[10, 5] = 8000
    image[11, 6] = np.nan
    params = dict(x=40., y=40., radius=8, sky_inner=0, sky_outer=0)
    result = measure_comet_aperture(image, **params, sky_rectangle=(2.5, 9.5, 10.5, 19.5))
    assert result.background == 50
    assert result.flux == pytest.approx(950*np.pi*8**2)
    assert result.flags == ()
    assert result.sky_pixels == 78
    assert result.sky_pixels_clipped == 1
    with pytest.raises(ValueError, match="clear"):
        measure_comet_aperture(image, **params, sky_rectangle=(45, 45, 60, 60))
    with pytest.raises(ValueError, match="enlarge"):
        measure_comet_aperture(image, **params, sky_rectangle=(-30, -30, -1, -1))


def test_sky_variance_counts_retained_samples_and_narrow_annulus_coverage():
    image = np.zeros((80, 80))
    image[10:20, 10:20] = np.tile([-2., 2.], (10, 5))
    image[10, 10] = 5000
    result = measure_comet_aperture(image, x=40, y=40, radius=5, sky_inner=4, sky_outer=9,
                                    sky_rectangle=(9.5, 9.5, 19.5, 19.5))
    weights = CircularAperture((40, 40), 5).to_mask(method="exact").data
    expected_var = result.background_rms**2 * ((weights**2).sum() + np.pi/2*weights.sum()**2/99)
    assert result.sky_pixels == 99
    assert result.flux_error**2 == pytest.approx(expected_var)
    image[40, 40] = 100
    narrow = measure_comet_aperture(image, x=40, y=40, radius=3, sky_inner=6, sky_outer=8)
    assert narrow.flags == ()


def test_comparison_star_fwhm_approximates_gaussian_width_and_rejects_bad_core():
    y, x = np.indices((50, 50))
    image = 80 + 1400*np.exp(-((x-25.2)**2 + (y-24.9)**2)/(2*1.7**2))
    coverage = np.ones(image.shape, dtype=np.uint16)
    assert comparison_star_fwhm(image, coverage, 1, 25.2, 24.9) == pytest.approx(2.35482*1.7, abs=.35)
    coverage[25, 24] = 0
    assert comparison_star_fwhm(image, coverage, 1, 25.2, 24.9) is None


@pytest.mark.parametrize("angle", [0.0, 41.0, 91.0, 237.0])
def test_tail_ruler_wcs_direction_even_through_ra_wrap(angle):
    from astropy import units as u
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.crpix = [100, 100]
    wcs.wcs.crval = [359.999, 65]
    wcs.wcs.cd = [[0.00065, 0.00012], [0.00012, -0.00065]]
    center = (99., 99.)
    origin = wcs.pixel_to_world(*center)
    tip = wcs.world_to_pixel(origin.directional_offset_by(angle*u.deg, 2.8*u.arcmin))
    length, pa = tail_geometry(wcs.to_header().tostring(), center, tip)
    assert length == pytest.approx(2.8)
    assert min(abs(pa - angle), abs(pa - angle - 360)) < 1e-7
    with pytest.raises(ValueError, match="distinct"):
        tail_geometry(wcs.to_header().tostring(), center, center)
