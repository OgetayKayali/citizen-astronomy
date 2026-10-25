"""Real OpenGL smoke and reproducible screenshots; run from repository root.

python scripts/smoke_magnetosphere.py --output Exports/magnetosphere-smoke
Exercises GPU rendering and controls, records frame intervals and GL errors.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt, QPoint
from OpenGL import GL

from photometry_app.ui.magnetosphere_view import MagnetospherePanel
from photometry_app.core.magnetosphere import PRESETS


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="Exports/magnetosphere-smoke")
    parser.add_argument("--launcher-preview", action="store_true", help="Update the bundled launcher thumbnail from this real renderer")
    args = parser.parse_args()
    output = Path(args.output); output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    panel = MagnetospherePanel(); panel.resize(1440, 980); panel.show()
    frames = []
    panel.scene.frame_rendered.connect(lambda: frames.append(time.perf_counter()))
    def wait(ms):
        until = time.perf_counter()+ms/1000
        while time.perf_counter() < until:
            app.processEvents(); QTest.qWait(5)
    result = {}
    try:
        wait(1000)
        assert not panel.scene.error, panel.scene.error
        assert panel.scene.isValid(), "No valid OpenGL context"
        panel._begin_scrub()
        panel.camera.setCurrentText("Pressure comparison"); panel._camera_changed()
        for index, name in ((0, "quiet"), (1, "north"), (2, "south")):
            panel.preset.setCurrentIndex(index); wait(350)
            assert panel.model.parameters == list(PRESETS.values())[index]
            panel.seek(45); wait(100)
            assert panel.grab().save(str(output/f"{name}.png"))
            s = panel.scene.state
            result[name] = dict(r0=s.r0, bow_r0=s.bow_r0, pressure=s.pressure, bz=s.bz,
                               newell=s.newell, storage=s.coupling, lag_seconds=s.lag_seconds)
        if args.launcher_preview:
            frame = panel.scene.grabFramebuffer().scaled(960, 540, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            frame.copy((frame.width()-960)//2, (frame.height()-540)//2, 960, 540).save("assets/mode_launcher/aurora_magnetosphere.jpg", "JPG", 92)
        reference = panel.scene.state
        panel.seek(5); panel.seek(45)
        assert panel.scene.state == reference
        for t, stage in ((12.5, "incoming"), (panel.model.arrival+3, "impact"), (100, "recovery")):
            panel.seek(t); wait(80); panel.grab().save(str(output/f"{stage}.png"))
            if stage == "incoming":
                assert panel.scene.state.upstream.pressure > panel.scene.state.pressure
        panel.seek(45)
        panel.camera.setCurrentText("Topology · schematic"); panel._camera_changed()
        wait(100); panel.grab().save(str(output/"topology.png"))
        panel.layer_boxes["topology"].setChecked(False)
        panel.scene.camera_preset("Earth"); wait(150)
        panel.grab().save(str(output/"earth.png"))
        panel.scene.camera_preset("Overview")
        before = panel.scene.yaw
        QTest.mousePress(panel.scene, Qt.MouseButton.LeftButton, pos=QPoint(450, 350))
        QTest.mouseMove(panel.scene, QPoint(480, 360), delay=20)
        QTest.mouseRelease(panel.scene, Qt.MouseButton.LeftButton, pos=QPoint(480, 360))
        assert panel.scene.yaw != before
        panel.scene.camera_preset("Overview")
        for key in panel.scene.visible_layers:
            panel._layer(key, False)
        wait(50)
        for key in panel.scene.visible_layers: panel._layer(key, True)
        panel.reset(); assert panel.sim_time == 0 and not panel.playing
        wait(80); assert panel.sim_time == 0
        panel.seek(28); panel.toggle_play()
        frames.clear(); wait(3500)
        assert panel.sim_time > 30
        assert len(frames) > 15, f"Unresponsive rendering: {len(frames)} frames"
        intervals = np.diff(frames)*1000
        measured_frames = len(frames)
        panel.scene.makeCurrent()
        result["gpu"] = GL.glGetString(GL.GL_RENDERER).decode()
        result["gl_error"] = int(GL.glGetError())
        panel.scene.doneCurrent()
        assert result["gl_error"] == 0
        # Controls remain editable during playback; completed work reaches the view.
        panel.inputs["density"].setValue(20); wait(250)
        assert panel.model.parameters.density == 20
        result["frames"] = measured_frames
        result["fps"] = float(1000/np.mean(intervals))
        result["frame_ms_p95"] = float(np.percentile(intervals, 95))
        panel._begin_scrub()
        panel.preset.setCurrentIndex(0); wait(250)
        panel.inputs["speed"].setValue(250)
        panel.inputs["density"].setValue(1)
        panel.inputs["bz"].setValue(-25); wait(300)
        assert not panel.scene.state.bow_applicable
        assert "Bow shock hidden" in panel.fit_note.text()
        panel.grab().save(str(output/"unsupported-shock.png"))
        panel.hide(); previous = panel.sim_time; wait(100)
        assert not panel.timer.isActive() and panel.sim_time == previous
        (output/"result.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2))
    finally:
        panel.shutdown(); panel.close(); app.processEvents()


if __name__ == "__main__": main()
