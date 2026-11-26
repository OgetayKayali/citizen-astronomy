import math
import os
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from astropy.wcs import Sip, WCS
from PySide6.QtWidgets import QApplication, QFileDialog

from photometry_app.core.comet_aperture_geometry import aperture_scale
from photometry_app.core.comet_photometry import save_comet_session


def distorted_wcs(scale):
    wcs = WCS(naxis=2)
    wcs.wcs.crpix = [40, 63]
    wcs.wcs.crval = [0., 68.]
    wcs.wcs.ctype = ["RA---TAN-SIP", "DEC--TAN-SIP"]
    wcs.wcs.cd = np.array([[-1.4, 0.3], [0.12, 0.9]]) * scale / 3600
    a = np.zeros((3, 3))
    b = np.zeros((3, 3))
    a[2, 0], b[0, 2] = 0.0008, -0.0004
    wcs.sip = Sip(a, b, None, None, wcs.wcs.crpix)
    return wcs


def test_local_jacobian_with_skew_sip_and_ra_wrap():
    wcs = distorted_wcs(3)
    header = wcs.to_header(relax=True).tostring()
    x, y = 120, 90
    scale = aperture_scale(header, x, y)
    # Independent solid angle of the full pixel from its four sky corners.
    sky = wcs.pixel_to_world(x + np.array([-.5, .5, .5, -.5]), y + np.array([-.5, -.5, .5, .5]))
    vec = sky.cartesian.xyz.value.T
    def triangle(a, b, c):
        return 2 * math.atan2(abs(np.dot(a, np.cross(b-a, c-a))), 1 + np.dot(a,b) + np.dot(b,c) + np.dot(c,a))
    solid_angle = triangle(*vec[:3]) + triangle(vec[0], vec[2], vec[3])
    assert scale.equivalent_arcsec == pytest.approx(math.degrees(math.sqrt(solid_angle)) * 3600, rel=1e-8)
    assert scale.x_arcsec == pytest.approx(wcs.pixel_to_world(x-.5, y).separation(wcs.pixel_to_world(x+.5, y)).arcsec)
    assert scale.y_arcsec == pytest.approx(wcs.pixel_to_world(x, y-.5).separation(wcs.pixel_to_world(x, y+.5)).arcsec)
    assert abs(scale.axis_angle_deg - 90) > 5
    assert scale.major_arcsec / scale.minor_arcsec > 1.5
    assert scale.equivalent_arcsec / aperture_scale(header, 39, 62).equivalent_arcsec > 1.03


def test_arcsecond_units_preserve_radius_and_replay_legacy_detector_aperture(tmp_path):
    from test_comet_photometry_dialog import make_dialog

    app = QApplication.instance() or QApplication([])
    dialog, pair = make_dialog(tmp_path)
    wcs = distorted_wcs(100)
    pair.header_text = wcs.to_header(relax=True).tostring()
    pair.pixel_scale = aperture_scale(pair.header_text, pair.center_x, pair.center_y).equivalent_arcsec
    dialog._prepared(pair, [], "")
    dialog._center = (84., 76.)
    local = aperture_scale(pair.header_text, *dialog._center)
    dialog.radius.setValue(21.255)
    dialog.units.setCurrentIndex(1)
    assert dialog.radius.value() == pytest.approx(21.255 * local.equivalent_arcsec, abs=1e-10)
    assert dialog.radius.value() > 2000
    dialog._measure()
    selected = next(row for row in dialog.rows if row["selected_aperture"])
    assert selected["radius_px"] == pytest.approx(21.255, abs=1e-10)
    assert selected["aperture_semimajor_arcsec"] == pytest.approx(21.255 * local.major_arcsec)
    for _ in range(5):
        dialog.units.setCurrentIndex(0)
        dialog.units.setCurrentIndex(1)
    saved = tmp_path / "large-arcsec.castcomet"
    save_comet_session(saved, pair, dialog._session_settings(), [])
    dialog.units.setCurrentIndex(0)
    with patch.object(QFileDialog, "getOpenFileName", return_value=(str(saved), "")):
        dialog._open_session()
    assert dialog.units.currentIndex() == 1
    assert dialog.radius.value() == pytest.approx(selected["radius_arcsec"], abs=1e-10)
    # A pre-local-scale archive used one projection-plane scale everywhere.
    pair.provenance.pop("aperture_scale_model")
    pair.pixel_scale = 100.
    settings = dialog._session_settings()
    settings["radius"] = 1200.
    old = tmp_path / "legacy.castcomet"
    save_comet_session(old, pair, settings, [])
    with patch.object(QFileDialog, "getOpenFileName", return_value=(str(old), "")):
        dialog._open_session()
    restored = next(row for row in dialog.rows if row["selected_aperture"])
    assert restored["radius_px"] == pytest.approx(12., abs=1e-10)
    assert restored["radius_arcsec"] == pytest.approx(12 * local.equivalent_arcsec, abs=1e-9)
    dialog.close()
