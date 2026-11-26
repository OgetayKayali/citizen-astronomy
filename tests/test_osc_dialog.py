import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from photometry_app.ui.osc_dialog import OscPreparationDialog


def test_osc_dialog_builds_request_and_camera_timing_profile(tmp_path):
    app = QApplication.instance() or QApplication([])
    dialog = OscPreparationDialog(tmp_path)
    dialog.show()
    app.processEvents()
    dialog._dwarf()
    assert dialog.reference.currentText() == "end"
    assert dialog.timezone.text() == ""
    dialog.timezone.setText("America/New_York")
    dialog.numbers["gain"].setText(".5")
    dialog.numbers["read_noise"].setText("1.3")
    dialog.inputs["flat"].setText(str(tmp_path / "flats"))
    request = dialog.build_request()
    assert request.profile.timestamp_reference == "end"
    assert request.profile.gain == .5
    assert request.flat_path == tmp_path / "flats"
    assert request.channels == ("TG", "TB")
    assert not dialog.use.isEnabled()
    dialog.close()


def test_profile_save_load_roundtrip(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    dialog = OscPreparationDialog(tmp_path)
    target = tmp_path / "profile.json"
    monkeypatch.setattr("photometry_app.ui.osc_dialog.QFileDialog.getSaveFileName", lambda *a: (str(target), ""))
    monkeypatch.setattr("photometry_app.ui.osc_dialog.QFileDialog.getOpenFileName", lambda *a: (str(target), ""))
    dialog.profile_name.setText("IMX662 measured gain")
    dialog.pattern.setCurrentText("BGGR")
    dialog.numbers["saturation"].setText("4095")
    dialog._save_profile()
    dialog.pattern.setCurrentText("RGGB")
    dialog._load_profile()
    assert dialog._profile().pattern == "BGGR"
    assert dialog._profile().saturation == 4095
    dialog.close()


def test_use_prepared_folder_starts_a_fresh_scan(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from PySide6.QtWidgets import QDialog, QLineEdit
    from photometry_app.ui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    output = tmp_path / "channels"
    dialog = SimpleNamespace(output_root=output, exec=lambda: QDialog.DialogCode.Accepted)
    monkeypatch.setattr("photometry_app.ui.osc_dialog.OscPreparationDialog", lambda *args: dialog)
    window = SimpleNamespace(_root_path_input=QLineEdit(str(tmp_path)), _differential_workflow_dialog=None,
                             _load_settings_for_root=Mock(), _append_work_log=Mock(), _start_scan=Mock())
    MainWindow._open_osc_dialog(window)
    assert window._root_path_input.text() == str(output)
    window._load_settings_for_root.assert_called_once_with(output, preserve_app_mode=True)
    window._start_scan.assert_called_once()
