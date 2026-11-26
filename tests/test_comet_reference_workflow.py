import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from astropy.io import fits
from astropy.table import Table
import numpy as np
import pytest
from PySide6.QtWidgets import QApplication, QFileDialog

from photometry_app.core.comet_extinction import comparison_extinction, green_coefficient, stack_extinction
from photometry_app.core.comet_photometry import (
    ComparisonStar, build_comet_stack_pair, comet_catalog_band, comet_image_units,
    mask_cast_alignment_padding, site_from_comet_headers,
)
from photometry_app.core.comet_references import _refcat_field, read_comparison_sequence, write_comparison_sequence
from photometry_app.core.models import SolvedField
from test_comet_photometry import make_sequence
from test_comet_photometry_dialog import make_dialog


def test_rate_images_skip_exposure_division_and_reprojection(tmp_path):
    frames, _ = make_sequence(tmp_path)
    original = build_comet_stack_pair(frames, reference_path=frames[0].path)
    for f in frames:
        with fits.open(f.path, mode="update") as hdus:
            hdus[0].data /= f.exposure
            hdus[0].header["BUNIT"] = "ADU/sec"
            hdus[0].header["DATE-OBS"] = f.midpoint.isoformat()
    rate = build_comet_stack_pair(frames, reference_path=frames[0].path)
    np.testing.assert_allclose(rate.comet, original.comet, equal_nan=True)
    assert rate.provenance["units"] == "adu / s"
    assert all(not row["star_reprojected"] and not row["divided_by_exposure"] for row in rate.provenance["frames"])
    with pytest.raises(ValueError, match="BUNIT"):
        comet_image_units({"BUNIT": "mag"})
    with fits.open(frames[0].path, mode="update") as hdus:
        hdus[0].header["BAYERPAT"] = "RGGB"
    with pytest.raises(ValueError, match="Bayer"):
        build_comet_stack_pair(frames, reference_path=frames[0].path)


def test_only_cast_exterior_zero_padding_masked():
    image = np.ones((10, 12))
    image[:2] = 0
    image[5, 5] = 0
    h = fits.Header()
    assert mask_cast_alignment_padding(image, h)[1] is False
    h.add_history("Citizen Photometry aligned this frame onto the reference grid of test.fits.")
    masked, changed = mask_cast_alignment_padding(image, h)
    assert changed and np.isnan(masked[:2]).all() and masked[5, 5] == 0


def test_propagate_precalibration_saturation_extension(tmp_path):
    frames, _ = make_sequence(tmp_path)
    with fits.open(frames[0].path, mode="update", memmap=False) as hdus:
        frac = np.zeros(hdus[0].data.shape, dtype=np.float32)
        frac[80, 80] = .25
        hdus.append(fits.ImageHDU(frac, name="SATFRAC"))
    pair = build_comet_stack_pair(frames, reference_path=frames[0].path)
    assert pair.star_coverage[80, 80] == 2 and pair.comet_coverage[80, 80] == 2
    assert pair.provenance["frames"][0]["saturation_masked"]
    with pytest.raises(ValueError, match="more than once"):
        build_comet_stack_pair([frames[0], frames[0]], reference_path=frames[0].path)


def test_consistent_site_from_fits_and_decline_conflicting_sites(tmp_path):
    frames, _ = make_sequence(tmp_path)
    paths = [f.path for f in frames]
    for p in paths:
        fits.setval(p, "SITELONG", value=-99.3825)
        fits.setval(p, "SITELAT", value=31.5472)
    assert site_from_comet_headers(paths) == pytest.approx(dict(lon=-99.3825, lat=31.5472, elevation=0))
    fits.setval(paths[-1], "SITELAT", value=40.)
    assert site_from_comet_headers(paths) is None


def test_green_and_differential_transmission(tmp_path):
    # MBK Green calculator: average sea-level extinction at 30 degrees rounds to 0.56 mag.
    k = green_coefficient("Green average", 0)
    assert round(stack_extinction(np.array([30.]), k), 2) == 0.56
    assert green_coefficient("Green winter", 1) < green_coefficient("Green summer", 1)
    frames, wcs = make_sequence(tmp_path)
    frames = [replace(f, ra_deg=frames[0].ra_deg, dec_deg=frames[0].dec_deg) for f in frames]
    pair = build_comet_stack_pair(frames, reference_path=frames[0].path)
    pair.provenance["observer"] = {"lon": 62, "lat": 20, "elevation": 0.25}
    x, y = pair.center_x, pair.center_y
    east, north = wcs.world_to_pixel_values(frames[0].ra_deg+1, frames[0].dec_deg)
    stars = [ComparisonStar("same", "same", x, y, 12, .02),
             ComparisonStar("east", "east", east, north, 12, .02)]
    k, attenuation, corrections = comparison_extinction(pair, stars, "Measured coefficient", 0.2)
    assert k == 0.2 and attenuation > k
    assert corrections["same"] == pytest.approx(0, abs=1e-12)
    assert corrections["east"] < 0  # Closer to transit than the target.
    pair.provenance["filter"] = "L"
    with pytest.raises(ValueError, match="require V"):
        comparison_extinction(pair, stars, "Green winter", 0)


def test_refcat_uses_documented_duplicate_and_variability_flags():
    table = Table(dict(RA_ICRS=[1., 2., 3., 4.], DE_ICRS=[0.]*4, rmag=[13.]*4,
                       e_rmag=[.02]*4, dupvar=[0, 1, 2, 6]))
    with patch("photometry_app.core.comet_references.Vizier") as query:
        query.return_value.query_region.return_value = [table]
        refs = _refcat_field(SolvedField(0., 0., .05, 10, 10, Path(".")), "PS1r", "test")
    assert [s.ra_deg for s in refs] == [1., 3.]
    assert comet_catalog_band("Pan-STARRS r") == "PS1r"
    assert comet_catalog_band("Sloan r") == "SR"


def test_local_sequence_round_trip_through_dialog(tmp_path):
    app = QApplication.instance() or QApplication([])
    dlg, pair = make_dialog(tmp_path)
    pair.provenance["comparison_catalog"] = "My measured V sequence"
    star = ComparisonStar("A", "Ref A", 35, 40, 12.3, .02, .64, False)
    csv_path = tmp_path / "field.csv"
    write_comparison_sequence(csv_path, pair, [star], "V")
    with pytest.raises(ValueError, match="band"):
        read_comparison_sequence(csv_path, pair, "B")
    dlg._prepared(pair, [], "Instrumental")
    with patch.object(QFileDialog, "getOpenFileName", return_value=(str(csv_path), "")):
        dlg.import_refs.click()
    assert dlg.comparisons[0].x == pytest.approx(35)
    assert not dlg.comparisons[0].selected and dlg.export_refs.isEnabled()
    assert dlg.rows[0]["comparison_catalog"] == "My measured V sequence"
    dlg.close()
    app.processEvents()
