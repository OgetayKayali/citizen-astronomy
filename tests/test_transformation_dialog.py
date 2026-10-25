import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox
from PySide6.QtCore import Qt

from photometry_app.core.settings import _settings_from_payload
from photometry_app.ui.transformation_dialog import TransformationDialog
from test_transformation import profile


def test_profile_review_selection_and_apply(tmp_path):
    app = QApplication.instance() or QApplication([])
    p = profile()
    settings = _settings_from_payload({}, tmp_path / "settings.json", False)
    settings.telescope = p["telescope"]
    settings.camera = p["camera"]
    settings.transformation_optical_setup = p["optical_setup"]
    settings.transformation_profiles = [p]
    settings.transformation_profile_id = p["id"]
    dialog = TransformationDialog(settings)
    dialog.show()
    app.processEvents()
    assert dialog.table.rowCount() == len(p["observations"])
    assert "Validated" in dialog.status.text()
    dialog.enabled.setChecked(True)
    dialog._apply()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.settings.transformation_enabled
    assert not settings.transformation_enabled  # Cancel/apply isolation
    dialog.close()


def test_guided_calibration_skips_red_and_saves_in_one_action(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    from photometry_app.core import transformation_calibration as calibration
    from test_transformation import standards
    settings = _settings_from_payload({}, tmp_path / "settings.json", False)
    dialog = TransformationDialog(settings)
    dialog.show()
    app.processEvents()
    assert dialog.controls.currentIndex() == 0
    assert not dialog.table.isVisible()
    assert not hasattr(dialog, "conditions")
    assert not hasattr(dialog, "skip_button")
    dialog.brand.setCurrentText("ZWO")
    dialog.model.setText("RGB set")
    dialog._equipment_changed()
    groups = {token: [tmp_path / f"{token}.fits"] for token in ("B", "G", "R")}
    dialog._images_scanned(dict(groups=groups, blue="B", visual="G", cameras=["Camera"], telescopes=["Scope"]))
    assert "Unused for B/V: R (1)" in dialog.image_summary.text()
    assert dialog.calibrate_button.isEnabled()
    measured = []

    def measure(paths, *args):
        measured.extend(paths)
        return standards(), "Synthetic AAVSO standard-field fixture"

    monkeypatch.setattr(calibration, "measure_standard_images", measure)
    monkeypatch.setattr(dialog, "_start", lambda job, handler: handler(job(lambda _: None, lambda: False)))
    dialog._calibrate()
    assert measured == groups["B"] + groups["G"]
    assert dialog.candidate["validated"]
    assert dialog.candidate["minimum_snr"] == dialog.min_snr.value()
    assert dialog.candidate["equipment"]["brand"] == "ZWO"
    assert dialog.calibration_date.text() == "2026-10-01"
    assert "Tb_bv" not in dialog.status.text()
    assert "Tb_bv" in dialog.details.text()
    assert not settings.transformation_profiles
    dialog._apply()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.settings.transformation_enabled
    assert len(dialog.settings.transformation_profiles) == 1
    assert dialog.settings.transformation_profile_id == dialog.settings.transformation_profiles[0]["id"]


def test_brand_selection_does_not_supply_coefficients_and_persists(tmp_path):
    app = QApplication.instance() or QApplication([])
    from photometry_app.core.settings import _settings_payload
    settings = _settings_from_payload({}, tmp_path / "settings.json", False)
    dialog = TransformationDialog(settings)
    dialog.brand.setCurrentText("Astronomik")
    dialog.enabled.setChecked(True)
    assert not dialog.buttons.button(QDialogButtonBox.StandardButton.Apply).isEnabled()
    assert dialog.candidate is None
    dialog.enabled.setChecked(False)
    dialog._apply()
    restored = _settings_from_payload(_settings_payload(dialog.settings, tmp_path), tmp_path / "settings.json", False)
    reopened = TransformationDialog(restored)
    assert reopened.brand.currentText() == "Astronomik"
    assert not reopened.enabled.isChecked()
    reopened.close()


def test_saved_profile_changes_cannot_reuse_stale_fit(tmp_path):
    app = QApplication.instance() or QApplication([])
    p = profile()
    settings = _settings_from_payload({}, tmp_path / "settings.json", False)
    settings.transformation_profiles = [p]
    settings.transformation_profile_id = p["id"]
    settings.transformation_enabled = True
    dialog = TransformationDialog(settings)
    dialog.brand.setCurrentText("Baader")
    assert dialog.dirty
    assert not dialog.buttons.button(QDialogButtonBox.StandardButton.Apply).isEnabled()
    dialog.enabled.setChecked(False)
    dialog._apply()
    assert dialog.settings.transformation_profile_id == ""
    assert dialog.settings.transformation_equipment["brand"] == "Baader"


def test_equipment_header_mismatch_warns_and_keeps_images(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    dialog = TransformationDialog(_settings_from_payload({}, tmp_path / "settings.json", False))
    dialog.telescope.setText("William Optics Redcat51 WIFD")
    dialog.camera.setText("ASI2600")
    shown = []
    monkeypatch.setattr(
        "photometry_app.ui.transformation_dialog.QMessageBox.warning",
        lambda *args: shown.append(args[-1]),
    )
    groups = {"B": [tmp_path / "b.fits"], "G": [tmp_path / "g.fits"]}
    dialog._images_scanned(dict(
        groups=groups, blue="B", visual="G", cameras=["ASI2600"], telescopes=["ZWO AM5"],
    ))
    assert dialog.image_groups == groups
    assert dialog.telescope.text() == "William Optics Redcat51 WIFD"
    assert dialog.camera.text() == "ASI2600"
    assert (dialog.blue_choice.currentData(), dialog.visual_choice.currentData()) == ("B", "G")
    assert shown and "ZWO AM5" in shown[0] and "William Optics Redcat51 WIFD" in shown[0]
    assert dialog.calibrate_button.isEnabled()
    dialog.close()


def test_ambiguous_mapping_requires_explicit_channel_selection(tmp_path):
    app = QApplication.instance() or QApplication([])
    dialog = TransformationDialog(_settings_from_payload({}, tmp_path / "settings.json", False))
    dialog._images_scanned(dict(groups={"B1": [Path("b.fits")], "G1": [Path("g.fits")]},
                               blue="", visual="", cameras=[], telescopes=[]))
    assert not dialog.calibrate_button.isEnabled()
    dialog.blue_choice.setCurrentIndex(1)
    dialog.visual_choice.setCurrentIndex(1)
    assert not dialog.calibrate_button.isEnabled()
    dialog.visual_choice.setCurrentIndex(2)
    assert dialog.calibrate_button.isEnabled()
    assert (dialog.blue.text(), dialog.visual.text()) == ("B1", "G1")
    dialog.close()


def test_image_inspection_makes_calibration_ready_without_confirmation(tmp_path, monkeypatch):
    from photometry_app.core import transformation_calibration as calibration
    app = QApplication.instance() or QApplication([])
    dialog = TransformationDialog(_settings_from_payload({}, tmp_path / "settings.json", False))
    groups = {token: [tmp_path / f"{token}.fits"] for token in ("B", "G")}
    monkeypatch.setattr(calibration, "inspect_calibration_images", lambda *args: dict(
        groups=groups, blue="B", visual="G", cameras=["Camera"], telescopes=["Scope"]))
    monkeypatch.setattr(dialog, "_start", lambda job, handler: handler(job(lambda _: None, lambda: False)))
    dialog._inspect_images(groups["B"] + groups["G"])
    assert dialog.calibrate_button.isEnabled()
    assert "Ready to calibrate" in dialog.status.text()
    dialog.close()


def test_close_preserves_existing_transformation_settings(tmp_path):
    app = QApplication.instance() or QApplication([])
    p = profile()
    settings = _settings_from_payload({}, tmp_path / "settings.json", False)
    settings.transformation_profiles = [p]
    settings.transformation_profile_id = p["id"]
    settings.transformation_enabled = True
    dialog = TransformationDialog(settings)
    dialog.reject()
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert dialog.settings.transformation_enabled
    assert dialog.settings.transformation_profile_id == p["id"]
    assert dialog.settings.transformation_profiles == [p]
    assert settings.transformation_enabled


def test_advanced_snr_and_rejection_settings_restore_with_profile(tmp_path):
    from photometry_app.core.transformation import fit_profile
    from test_transformation import standards
    app = QApplication.instance() or QApplication([])
    p = fit_profile(standards(), name="Saved", telescope="Scope", camera="Camera", optical_setup="RGB",
                    blue_filter="B", visual_filter="G", calibration_date="2026-10-01", provenance="Test",
                    minimum_snr=30, reject_outliers=False)
    settings = _settings_from_payload({}, tmp_path / "settings.json", False)
    settings.transformation_profiles = [p]
    settings.transformation_profile_id = p["id"]
    dialog = TransformationDialog(settings)
    assert dialog.min_snr.value() == 30
    assert not dialog.reject_outliers.isChecked()
    assert not dialog.dirty
    dialog.close()


def test_fitted_exclusions_are_unchecked_and_explained(tmp_path):
    from dataclasses import replace
    from test_transformation import standards
    app = QApplication.instance() or QApplication([])
    dialog = TransformationDialog(_settings_from_payload({}, tmp_path / "settings.json", False))
    rows = standards()
    rows[0] = replace(rows[0], instrumental_magnitude=rows[0].instrumental_magnitude+4)
    dialog._fitted(profile(rows))
    assert not dialog.rows[0].enabled
    assert dialog.table.item(0, 0).checkState() == Qt.CheckState.Unchecked
    assert "Automatic outlier" in dialog.table.item(0, 6).text()
    assert "Excluded" in dialog.figure.axes[0].get_legend_handles_labels()[1]
    assert dialog.figure.axes[0].get_ylim()[1] < .1
    dialog.close()


def test_primary_action_changes_after_successful_calibration(tmp_path):
    app = QApplication.instance() or QApplication([])
    dialog = TransformationDialog(_settings_from_payload({}, tmp_path / "settings.json", False))
    dialog.show()
    app.processEvents()
    assert dialog.calibrate_button.isVisible()
    assert not dialog.calibrate_button.isEnabled()
    dialog._fitted(profile())
    assert not dialog.calibrate_button.isVisible()
    apply = dialog.buttons.button(QDialogButtonBox.StandardButton.Apply)
    assert apply.text() == "Use calibration"
    assert apply.property("primary")
    dialog._invalidate()
    assert dialog.calibrate_button.isVisible()
    assert not apply.isEnabled()
    dialog.close()


def test_measurement_edits_invalidate_fitted_candidate(tmp_path):
    app = QApplication.instance() or QApplication([])
    settings = _settings_from_payload({}, tmp_path / "settings.json", False)
    dialog = TransformationDialog(settings)
    p = profile()
    from photometry_app.core.transformation import StandardObservation
    dialog.rows = [StandardObservation(**r) for r in p["observations"]]
    dialog._fitted(p)
    dialog.table.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
    assert dialog.candidate is None
    assert not dialog.rows[0].enabled
    dialog.close()


def test_measurements_page_changes_snr_limit_and_sorts_reason_categories(tmp_path):
    app = QApplication.instance() or QApplication([])
    from photometry_app.core.transformation import MAG_FACTOR, StandardObservation
    dialog = TransformationDialog(_settings_from_payload({}, tmp_path / "settings.json", False))
    dialog.show()
    app.processEvents()
    dialog.controls.setCurrentIndex(1)
    dialog.advanced_pages.setCurrentIndex(1)
    app.processEvents()
    measurements = dialog.advanced_pages.widget(1)
    settings_page = dialog.advanced_pages.widget(0)
    assert measurements.isAncestorOf(dialog.min_snr)
    assert not settings_page.isAncestorOf(dialog.min_snr)
    dialog.rows = [
        StandardObservation("snr", "e1", "B", -4, MAG_FACTOR / 40, 12.5, 12, .01, .01, enabled=False, reason="SNR below 100"),
        StandardObservation("overlap", "e1", "B", -4, MAG_FACTOR / 80, 12.6, 12, .01, .01, enabled=False, reason="Overlapping standard-star apertures"),
        StandardObservation("ok", "e1", "V", -4, MAG_FACTOR / 300, 12.2, 12, .01, .01, enabled=True, reason=""),
    ]
    dialog._display()
    dialog.min_snr.setValue(30)
    by_id = {row.star_id: row for row in dialog.rows}
    assert by_id["snr"].enabled and by_id["snr"].reason == ""
    assert not by_id["overlap"].enabled
    assert by_id["ok"].enabled
    dialog.table.horizontalHeader().sectionClicked.emit(6)
    categories = [dialog.table.item(i, 6).data(Qt.ItemDataRole.UserRole) for i in range(dialog.table.rowCount())]
    assert categories == sorted(categories)
    assert categories[-1] == "Overlapping apertures"
    dialog.close()
