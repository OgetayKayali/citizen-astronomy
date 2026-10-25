"""Render the embedded lab and verify its live controls; run with CAst's Python."""
from pathlib import Path
import json
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QTabWidget
from PySide6.QtTest import QTest
from photometry_app.ui.binary_light_view import BinaryLightPanel


def verify_bloom(panel, output):
    """GPU regressions: smooth falloff, occultation, and unchanged measurements."""
    model = panel.model
    saved = (model.values.copy(), model.cycles, dict(panel.look), [list(c) for c in panel.tints], panel.custom_tint.isChecked())
    measured_before = model.curve(101)
    recorded_before = list(model.samples)

    def capture(glow):
        panel.look["glow"] = glow
        frame = panel.scene.grabFramebuffer().convertToFormat(QImage.Format.Format_RGB888)
        pixels = np.frombuffer(frame.constBits(), dtype=np.uint8).reshape(frame.height(), frame.bytesPerLine())
        return pixels[:, :frame.width()*3].reshape(frame.height(), frame.width(), 3).copy()

    from PySide6.QtGui import QImage
    try:
        panel.look.update(sky=0., detail=0., zoom=1.)
        panel.custom_tint.setChecked(True)
        panel.tints = [[1., 1., 1.], [0., 0., 0.]]
        model.set("inclination", 90)
        model.set("radius_b", 2.5)
        model.cycles = 0.
        off, on = capture(0), capture(3)
        np.testing.assert_array_equal(off, on, err_msg="A completely hidden star still emits bloom")
        energy = []
        for phase in (0., .005, .015, .03, .08):
            model.cycles = phase
            off, on = capture(0), capture(1)
            energy.append(float(np.maximum(on.astype(float)-off, 0).sum()))
        assert energy[0] == 0 and np.all(np.diff(energy) >= 0), energy
        model.cycles = .25
        off, on = capture(0), capture(1)
        height, width = on.shape[:2]
        g = model.geometry(model.cycles)
        extent = model.get("separation")*.68
        scale = height/(2*extent)
        cx = width/2+g[0]*scale
        cy = height/2-g[1]*scale
        edge = int(np.ceil(cx+model.get("radius_a")*scale))
        profile = (on.astype(float)-off)[round(cy)-2:round(cy)+3, edge+3:edge+150, :].mean(axis=(0, 2))
        assert profile[0] > 5 and profile[-1] < 1, (profile[0], profile[-1])
        assert np.max(np.diff(profile)) <= 1, "Halo has a secondary bright ring or wave"
        (output / "bloom-checks.json").write_text(json.dumps({
            "eclipse_bloom_energy": energy,
            "halo_profile_max_outward_rise_8bit": float(np.max(np.diff(profile))),
            "halo_inner_outer_8bit": [float(profile[0]), float(profile[-1])],
        }, indent=2))
    finally:
        model.values, model.cycles, panel.look, panel.tints, custom = saved
        panel.custom_tint.setChecked(custom)
    np.testing.assert_array_equal(model.curve(101), measured_before)
    assert model.samples == recorded_before


def main():
    output = ROOT / "build/binary_light_smoke"
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    panel = BinaryLightPanel()
    panel.resize(1500, 1000)
    panel.show()
    QTest.qWait(600)
    assert panel.scene.isValid(), "No OpenGL context"
    assert panel.scene.program, panel.scene.error
    panel.prediction.setChecked(True)
    panel._edit("phase", .18)
    panel.scene.update()
    QTest.qWait(150)
    frame = panel.scene.grabFramebuffer()
    assert not frame.isNull()
    frame.save(str(output / "scene.png"))
    panel.grab().save(str(output / "panel.png"))
    rgb = frame.convertToFormat(frame.Format.Format_RGB888)
    pixels = np.frombuffer(rgb.constBits(), dtype=np.uint8).reshape(rgb.height(), rgb.bytesPerLine())[:, :rgb.width()*3].reshape(rgb.height(), rgb.width(), 3)
    middle = pixels[pixels.shape[0]//4:3*pixels.shape[0]//4]
    assert np.count_nonzero((middle[:, :, 0] > 130) & (middle[:, :, 1] > 130)) > 1000
    if "--preview" in sys.argv:
        # Regenerate the shipped preview only when explicitly requested.
        crop = frame.copy(0, round(frame.height()*.2), frame.width(), round(frame.height()*.6))
        crop.save(str(ROOT / "assets/mode_launcher/binary_light_lab.jpg"), quality=92)
    verify_bloom(panel, output)
    panel.scene.setFocus()
    QTest.keyClick(panel.scene, Qt.Key.Key_Space)
    QTest.qWait(400)
    assert panel.playing and panel.model.samples
    QTest.keyClick(panel.scene, Qt.Key.Key_Space)
    state = panel.model.cycles, list(panel.model.samples)
    QTest.qWait(100)
    assert not panel.playing
    assert state == (panel.model.cycles, panel.model.samples)
    panel.hide()
    assert not panel.timer.isActive()
    panel.show()
    QTest.qWait(80)
    assert panel.timer.isActive()
    panel.resize(1100, 760)
    tabs = panel.findChild(QTabWidget)
    for i in range(tabs.count()):
        tabs.setCurrentIndex(i)
        QTest.qWait(80)
        panel.grab().save(str(output / f"tab-{i}.png"))
    assert len(panel.scene.bloom.targets) == 10
    assert panel.scene.bloom.size == (round(panel.scene.width()*panel.scene.devicePixelRatioF()),
                                     round(panel.scene.height()*panel.scene.devicePixelRatioF()))
    panel.close()
    if "--main" in sys.argv:
        from photometry_app.core.models import AppMode
        from photometry_app.ui.main_window import MainWindow
        config = output / "smoke-settings.json"
        config.write_text(json.dumps({"default_settings": {"show_mode_launcher_on_startup": True}}))
        os.environ["CITIZEN_PHOTOMETRY_CONFIG_PATH"] = str(config)
        os.environ["CITIZEN_PHOTOMETRY_STATE_PATH"] = str(output / "smoke-state.json")
        os.environ["CITIZEN_PHOTOMETRY_TRAINING_DB"] = str(output / "smoke-training.sqlite3")
        window = MainWindow()
        window.resize(1500, 1000)
        window.show()
        QTest.qWait(400)
        window._handle_mode_launcher_selection(AppMode.BINARY_LIGHT_LAB)
        QTest.qWait(600)
        embedded = window._binary_light_panel
        assert embedded.scene.isValid() and embedded.scene.program, embedded.scene.error
        embedded.prediction.setChecked(True)
        embedded._edit("phase", .18)
        QTest.qWait(100)
        window.grab().save(str(output / "cast-window.png"))
        window._handle_app_mode_changed(AppMode.DIFFERENTIAL_PHOTOMETRY)
        assert not embedded.timer.isActive()
        window._handle_app_mode_changed(AppMode.BINARY_LIGHT_LAB)
        QTest.qWait(150)
        assert embedded.timer.isActive() and embedded.scene.program
        window.close()
        print("Full CAst window, launcher entry, GPU embedding, and mode switching verified.")
    print("OpenGL shaders, smooth bloom, eclipse occlusion, unchanged photometry, resized targets, Space playback/pause, hide/resume, and five tabs verified.")


if __name__ == "__main__":
    main()
