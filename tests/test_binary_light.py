from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pytest

from photometry_app.core.binary_light import BinaryLightModel, load_library

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def model():
    try:
        from photometry_app.core.variable_stars import StellarLabModel
        return StellarLabModel()
    except RuntimeError as exc:
        pytest.skip(str(exc))


def test_vendored_solver_shader_and_original_tests_are_unchanged():
    root = ROOT / "native/binary_light"
    for name, expected in json.loads((root / "source_manifest.json").read_text()).items():
        assert hashlib.sha256((root / "vendor" / name).read_bytes()).hexdigest() == expected


def test_kepler_barycenter_period_and_eccentric_orbit(model):
    model.set("eccentricity", .6)
    model.set_period(8)
    assert model.period == pytest.approx(8)
    for phase in (0, .17, .5, .92):
        g = model.geometry(phase)
        np.testing.assert_allclose(g[:6], model.geometry(phase+1)[:6], atol=1e-12)
        np.testing.assert_allclose(g[:3]*model.get("mass_a") + g[3:6]*model.get("mass_b"), 0, atol=1e-12)
    assert model.geometry(0)[6] == pytest.approx(model.get("separation")*.4)
    assert model.geometry(.5)[6] == pytest.approx(model.get("separation")*1.6)


def test_occultation_analytic_and_convergence(model):
    blocked = model.lib.bl_blocked
    for d in (.01, .4, 1, 1.7, 1.999):
        area = 2*math.acos(d/2)-.5*d*math.sqrt(4-d*d)
        assert blocked(1, 1, d, 0, 512) == pytest.approx(area/math.pi, rel=2e-5)
    r, u = .4, .7
    analytic = ((1-u)*r*r+2*u/3*(1-(1-r*r)**1.5))/(1-u/3)
    assert blocked(1, r, 0, u, 512) == pytest.approx(analytic, rel=1e-5)
    assert blocked(1, r, .85, u, 512) == pytest.approx(blocked(1, r, .85, u, 4096), rel=1e-5)


def test_equal_stars_and_face_on(model):
    model.values[4:8] = model.values[:4]
    model.set("inclination", 90)
    assert model.observe(0)[0] == pytest.approx(.5)
    assert model.observe(.5)[0] == pytest.approx(.5)
    assert model.observe(.25)[0] == 1
    model.set("inclination", 0)
    np.testing.assert_allclose(model.curve()[:, 0], 1)


def test_band_dilution_distance_and_orientation(model):
    model.set("inclination", 90)
    a = model.observe(0)
    assert a[0] < model.observe(.5)[0]
    model.set("distance", 200)
    far = model.observe(0)
    assert far[0] == a[0]
    assert far[2] == pytest.approx(a[2]/4, rel=1e-12, abs=0)
    model.set("third_light", .3)
    assert model.observe(0)[0] == pytest.approx(.3+.7*a[0])
    model.set("third_light", 0)
    model.set("band", 1)
    blue = model.observe(0)[0]
    model.set("band", 3)
    red = model.observe(0)[0]
    assert blue < red
    model.set("node", 63)
    assert model.observe(0)[0] == pytest.approx(red)


def test_exposure_averages_flux(model):
    model.set("exposure", model.period*86400*.04)
    integrated = model.observe(.04)[0]
    model.set("exposure", 0)
    expected = np.mean([model.observe(.04+((k+.5)/1000-.5)*.04)[0] for k in range(1000)])
    assert integrated == pytest.approx(expected, rel=2e-4)


def test_presets_and_invalid_inputs(model, tmp_path):
    for i in range(6):
        model.set_preset(i)
        assert not model.invalid
        flux = model.curve(101)[:, 0]
        assert np.all((flux > 0) & (flux <= 1.0000001))
    model.set("separation", 1)
    assert "intersect" in model.invalid
    with pytest.raises(ValueError):
        model.export(tmp_path / "invalid.csv")
    for value in (float("nan"), float("inf"), .5, 100):
        model.set("band", value)
        assert model.invalid


def test_system_roundtrip_rejects_invalid_without_mutating(model, tmp_path):
    model.set_preset(2)
    model.set("exposure", 30)
    path = tmp_path / "星 système.binary"
    model.save(path)
    before = model.values.copy()
    original_flux = model.observe(.133)[0]
    model.set_preset(0)
    model.load(path)
    np.testing.assert_array_equal(model.values, before)
    assert model.observe(.133)[0] == original_flux
    for text in ("BINARY_LIGHT_LAB 1\n", path.read_text().replace("24 ", "nan "), path.read_text().rsplit(" ", 1)[0]+" 1.5\n"):
        path.write_text(text)
        with pytest.raises(ValueError):
            model.load(path)
        np.testing.assert_array_equal(model.values, before)


def test_recording_cadence_noise_seek_cap_and_export(model, tmp_path):
    model.noise_ppm = 1000
    for _ in range(10):
        model.advance(.1, .5)
    samples = np.array(model.samples)
    assert len(samples) == 301
    model.restart()
    for _ in range(20):
        model.advance(.05, .5)
    np.testing.assert_array_equal(np.array(model.samples), samples)
    path = tmp_path / "curve.csv"
    output = model.export(path, observed=True)
    ideal = np.genfromtxt(path, delimiter=",", skip_header=5, names=True)
    observed = np.genfromtxt(output, delimiter=",", skip_header=1, names=True)
    assert len(ideal) == 1001
    assert len(observed) == 301
    np.testing.assert_allclose(observed["measured_flux"], samples[:, 1])
    model.restart(.97)
    for _ in range(4):
        model.advance(.1, .5)
    assert model.samples[0][0] == .97
    assert model.samples[-1][0] > 1
    # Retain the newest 6,000 entries, with their unwrapped timestamps.
    model.samples = [(0., 1., 1.)]*6000
    model.advance(.1, .5)
    assert len(model.samples) == 6000
    assert model.samples[-1][0] > 1


def test_panel_controls_and_lifecycle(model, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from photometry_app.ui.binary_light_view import BinaryLightPanel
    app = QApplication.instance() or QApplication([])
    panel = BinaryLightPanel()
    panel.model = model
    panel._build()
    try:
        assert panel.presets.count() == 23
        panel._rebuild_curve()
        assert len(panel.prediction_curve) == 601
        model.advance(.1, .5)
        original = list(model.samples)
        panel._edit("look_glow", 2)
        assert model.samples == original
        panel._edit("inclination", 0)
        assert model.samples == []
        panel._rebuild_curve()
        np.testing.assert_allclose(panel.prediction_curve, 1)
        panel._edit("phase", .4)
        assert not panel.playing and model.cycles == .4
        panel._toggle_play()
        panel._tick()
        panel._toggle_play()
        paused = (model.cycles, list(model.samples))
        panel._tick()
        assert (model.cycles, model.samples) == paused
        panel.presets.setCurrentIndex(2)
        assert model.get("eccentricity") == .45
        panel._edit("separation", 1)
        assert not panel.play.isEnabled()
        assert not panel.save_system.isEnabled()
        assert not panel.export_model.isEnabled()
    finally:
        panel.close()
        panel.deleteLater()
        app.processEvents()


def test_main_window_mode_integration(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from photometry_app.core.models import AppMode
    from photometry_app.ui.main_window import MainWindow
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"default_settings": {"show_mode_launcher_on_startup": False}}))
    monkeypatch.setenv("CITIZEN_PHOTOMETRY_CONFIG_PATH", str(config))
    monkeypatch.setenv("CITIZEN_PHOTOMETRY_STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setenv("CITIZEN_PHOTOMETRY_TRAINING_DB", str(tmp_path / "training.sqlite3"))
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        window._handle_mode_launcher_selection(AppMode.BINARY_LIGHT_LAB)
        assert window._current_app_mode() == AppMode.BINARY_LIGHT_LAB
        assert window._app_mode_stack.currentWidget() is window._binary_light_panel
        assert window._app_mode_binary_light_action.isChecked()
        assert "Binary Light Lab" in window.windowTitle()
        assert "Binary Light Lab" in window.statusBar().currentMessage()
        window._handle_app_mode_changed(AppMode.CLUSTER_FLOW)
        assert window._current_app_mode() == AppMode.CLUSTER_FLOW
        assert "Cluster Flow" in window.statusBar().currentMessage()
        window._app_mode_binary_light_action.trigger()
        assert window._current_app_mode() == AppMode.BINARY_LIGHT_LAB
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()
