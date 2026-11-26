from datetime import UTC, datetime
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from photometry_app.core.comet_reporting import (
    aperture_dust_proxy, build_icq_record, icq_target, magnitude_system, preparation_band,
)


def aperture(**changes):
    fields = {"target": "3I/ATLAS", "filter": "V", "zero_point": 25., "magnitude": 13.2,
              "magnitude_system": "APASS V zero point (untransformed)", "radius_arcsec": 10.,
              "magnitude_error_estimate": 0.03, "quality": ""}
    return {**fields, **changes}


def test_afrho_against_published_angular_diameter_form():
    # Independent angular-diameter form, doi:10.1051/0004-6361/201424223.
    # Their independent angular form uses aperture DIAMETER (arcseconds).
    geom = {"heliocentric_au": 2.3, "observer_au": 1.4, "source": "test geometry"}
    output = aperture_dust_proxy(aperture(), geom)
    expected = 2.4686e19 * 2.3**2 * 1.4 * 10**(0.4 * (-26.75 - 13.2)) / 20
    assert output["afrho_cm"] == pytest.approx(expected, rel=5e-5)
    assert output["projected_aperture_radius_km"] == pytest.approx(725.27 * 14, rel=1e-5)
    assert output["afrho_mag_error_cm"] / expected == pytest.approx(0.921 * 0.03, rel=2e-4)
    assert aperture_dust_proxy(aperture(filter="L"), geom)["afrho_cm"] is None
    assert aperture_dust_proxy(aperture(zero_point=None), geom)["afrho_cm"] is None
    assert aperture_dust_proxy(aperture(), {})["afrho_cm"] is None
    assert aperture_dust_proxy(aperture(magnitude=None), geom)["afrho_cm"] is None
    assert aperture_dust_proxy(aperture(filter="PS1g"), geom)["solar_magnitude_1au"] == -26.54
    assert preparation_band("L", False) is None
    assert preparation_band("L", True) == "V"
    assert preparation_band("Ha", True) is None
    assert magnitude_system("L", "V").startswith("L (APASS V-referenced")


def record(*, row=None, frames=None, **overrides):
    config = {"designation": icq_target("3I/ATLAS"), "observer": "ABCaa", "method": "V", "catalog": "AV",
              "instrument": "R", "aperture_cm": 20, "f_ratio": 4.5, "total": True, "calibration": "5"}
    if frames is None:
        frames = [{"midpoint": "2025-12-31T23:59:00+00:00", "exposure": 120}] * 76
    return build_icq_record(row or aperture(tail_length_arcmin=2.5, tail_pa_deg=359.8), frames,
                            {**config, **overrides}, closest_comparison=13.13, pixel_scales=(2.9, 3.2))


def test_extended_icq_columns_rollover_and_arithmetic():
    line = record()
    assert line[:11] == "  3I       "
    assert line[11:24] == "2026 01 01.00"
    assert line[26:35] == "V 13.2 AV"
    assert line[35:47] == " 20.0R 4I120"
    assert line[48:54] == "      "
    assert line[59:67] == " 2.5m  0"
    assert line[68:81] == "ICQ XX ABCaaI"
    assert line[86:93] == "C20.00s"
    assert line[93:105] == " " * 12
    assert line[105:114] == "5 76*0.03"
    assert line[115:129] == "13.1  2.9s 3.2"
    assert "CAst" in line[130:]
    one = record(frames=[{"midpoint": datetime(2025, 12, 7, 12, tzinfo=UTC).isoformat(), "exposure": 50}])
    assert one[11:24] == "2025 12 07.50"
    assert one[43:47] == "a 50"
    assert one[107:110] == " 1 "
    assert icq_target("73P-B/Schwassmann–Wachmann") == " 73       b"
    annotated = record(row=aperture(extinction_model="Green winter", target_stack_extinction_mag=0.19,
                                    extinction_coefficient_mag_airmass=0.15),
                       coma_arcmin="3.1", condensation="5/")
    assert annotated[25] == "w"
    assert annotated[49:57] == " 3.1  5/"


@pytest.mark.parametrize("kwargs, reason", [
    ({"row": aperture(zero_point=None)}, "catalog-referenced"), ({"total": False}, "visible coma"),
    ({"row": aperture(quality="Low signal-to-noise")}, "Low signal"),
    ({"row": aperture(filter="L")}, "clear or luminance"), ({"observer": "BOGUS01"}, "observer code"),
    ({"calibration": ""}, "corrections"), ({"camera": "LongName"}, "camera key"),
])
def test_invalid_icq_never_truncates_or_fabricates(kwargs, reason):
    with pytest.raises(ValueError, match=reason):
        record(**kwargs)


def test_report_dialog_default_instrumental_and_applied_geometry(tmp_path):
    from unittest.mock import patch
    from test_comet_photometry_dialog import make_dialog
    from photometry_app.ui.comet_report_dialog import CometReportDialog

    app = QApplication.instance() or QApplication([])
    dialog, pair = make_dialog(tmp_path)
    dialog._prepared(pair, [], "Instrumental")
    selected = next(row for row in dialog.rows if row["selected_aperture"])
    report = CometReportDialog(dialog, selected)
    assert not report.save.isEnabled()
    assert not report.copy.isEnabled()
    assert "catalog-referenced" in report.report_status.text()
    report.helio.setValue(2.0)
    report.delta.setValue(1.)
    assert "Projected aperture radius" in report.dust_result.text()
    with patch("photometry_app.ui.comet_photometry_dialog.CometReportDialog", return_value=report), \
            patch.object(report, "exec", return_value=report.DialogCode.Accepted):
        dialog.report_button.click()
    assert pair.provenance["geometry"]["source"] == "Observer-entered"
    assert all(row["projected_aperture_radius_km"] > 0 and row["afrho_cm"] is None for row in dialog.rows)
    report.close()
    dialog.close()


def test_catalog_calibrated_clear_and_native_v_report(tmp_path):
    import math
    from unittest.mock import patch
    import numpy as np
    from PySide6.QtWidgets import QFileDialog
    from test_comet_photometry_dialog import make_dialog
    from photometry_app.core.comet_photometry import ComparisonStar
    from photometry_app.ui.comet_report_dialog import CometReportDialog

    app = QApplication.instance() or QApplication([])
    dialog, pair = make_dialog(tmp_path)
    yy, xx = np.indices(pair.stars.shape)
    pair.stars = np.full_like(pair.stars, 40)
    stars = []
    for number, (x, y, amplitude) in enumerate([(32, 34, 80), (118, 36, 130), (39, 120, 200)]):
        pair.stars += amplitude * np.exp(-((xx-x)**2 + (yy-y)**2) / 2)
        magnitude = 25 - 2.5 * math.log10(2 * math.pi * amplitude)
        stars.append(ComparisonStar(str(number), str(number), x, y, magnitude, 0.01))
    pair.provenance["geometry"] = {"heliocentric_au": 2, "observer_au": 1.3, "source": "test ephemeris"}
    for name, clear in [("V", False), ("L", True)]:
        pair.provenance.update(filter=name, clear_v_reference=clear)
        dialog._prepared(pair, stars, "")
        row = next(row for row in dialog.rows if row["selected_aperture"])
        assert row["zero_point"] == pytest.approx(25, abs=1e-5)
        assert (row["afrho_cm"] is None) == clear
        assert row["magnitude_system"] == magnitude_system(name, "V")
        report = CometReportDialog(dialog, row)
        assert report.inputs()["catalog"] == "AV"
        for key, val in {"designation": icq_target("3I/ATLAS"), "observer": "ABCaa", "instrument": "R",
                         "method": "Z" if clear else "V"}.items():
            report.fields[key].setText(val)
        report.aperture_cm.setValue(10)
        report.f_ratio.setValue(5)
        report.calibration.setCurrentIndex(6)
        report.total.setChecked(True)
        assert report.save.isEnabled(), report.report_status.text()
        path = tmp_path / f"{name}.txt"
        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(path), "")):
            report.save.click()
        assert path.read_text().startswith("  3I       2026 01 01.00")
        assert path.read_text()[26] == ("Z" if clear else "V")
        report.close()
    dialog.close()
