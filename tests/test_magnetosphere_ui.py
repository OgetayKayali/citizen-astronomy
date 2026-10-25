"""Focused Qt lifecycle, background update and CAst integration tests."""
import json
import time

from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from photometry_app.core.models import AppMode
from photometry_app.core.magnetosphere import dynamic_pressure
from photometry_app.ui.magnetosphere_view import MagnetospherePanel


def pump(app, predicate, timeout=3):
    end = time.perf_counter()+timeout
    while time.perf_counter() < end:
        app.processEvents()
        if predicate(): return
        QTest.qWait(10)
    assert predicate(), "Qt condition did not become true"


def test_controls_coalesce_updates_and_pause_is_exact():
    app = QApplication.instance() or QApplication([])
    panel = MagnetospherePanel()
    try:
        # Poll the same path as a visible widget, without requiring GL in unit tests.
        panel.timer.start()
        panel._begin_scrub()
        panel.seek(45)
        panel.inputs["speed"].setValue(1200)
        panel.inputs["speed"].setValue(700)
        panel.inputs["density"].setValue(12)
        pump(app, lambda: panel.model.parameters.speed == 700 and panel.model.parameters.density == 12)
        assert panel.scene.state.pressure == dynamic_pressure(12, 700)
        state = panel.scene.state
        panel.seek(0); panel.seek(45)
        assert state == panel.scene.state
        panel._tick(); assert panel.sim_time == 45
        assert panel.scene.wind_profile.shape == (256, 4)
        assert "Stored-flux proxy" in panel.budget.text()
        panel.camera.setCurrentText("Topology · schematic")
        panel._camera_changed()
        assert panel.scene.visible_layers["topology"]
        panel._layer("field", False); assert not panel.scene.visible_layers["field"]
        panel.preset.setCurrentIndex(0)
        pump(app, lambda: panel.model.parameters.quiet)
        assert panel.scene.quiet
        assert panel.sim_time == 45
        assert panel.scene.state.speed == 400
        panel.inputs["speed"].setValue(250)
        panel.inputs["density"].setValue(1)
        panel.inputs["bz"].setValue(-25)
        pump(app, lambda: panel.model.parameters.bz == -25)
        assert not panel.scene.state.bow_applicable
        assert "Bow shock hidden" in panel.fit_note.text()
        panel.reset(); assert panel.sim_time == 0 and not panel.playing
    finally:
        panel.shutdown(); panel.close()


def test_cast_launcher_menu_title_and_settings_roundtrip(tmp_path, monkeypatch):
    from photometry_app.ui.main_window import MainWindow
    from photometry_app.core.settings import AppSettings
    config = tmp_path/"settings.json"
    state = tmp_path/"state.json"
    config.write_text(json.dumps({"default_settings": {"show_mode_launcher_on_startup": False}}))
    state.write_text("{}")
    monkeypatch.setenv("CITIZEN_PHOTOMETRY_CONFIG_PATH", str(config))
    monkeypatch.setenv("CITIZEN_PHOTOMETRY_STATE_PATH", str(state))
    monkeypatch.setenv("CITIZEN_PHOTOMETRY_TRAINING_DB", str(tmp_path/"training.sqlite3"))
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        window._handle_mode_launcher_selection(AppMode.AURORA_MAGNETOSPHERE)
        assert window._current_app_mode() == AppMode.AURORA_MAGNETOSPHERE
        assert window._app_mode_stack.currentWidget() is window._magnetosphere_panel
        assert window._app_mode_magnetosphere_action.isChecked()
        assert "Aurora & Magnetosphere Lab" in window.windowTitle()
        window._sync_file_menu_actions(AppMode.AURORA_MAGNETOSPHERE)
        assert window._open_file_action not in window._file_menu.actions()
        assert window._export_report_action not in window._file_menu.actions()
        settings = AppSettings.from_root(tmp_path)
        settings.app_mode = AppMode.AURORA_MAGNETOSPHERE
        settings.save(tmp_path)
        assert AppSettings.from_root(tmp_path).app_mode == AppMode.AURORA_MAGNETOSPHERE
        window._handle_app_mode_changed(AppMode.DIFFERENTIAL_PHOTOMETRY)
        assert window._current_app_mode() == AppMode.DIFFERENTIAL_PHOTOMETRY
    finally:
        window._magnetosphere_panel.shutdown()
        window.close()
        app.processEvents()
