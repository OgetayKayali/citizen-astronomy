from __future__ import annotations

import importlib.util
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "native" / "cluster_flow" / "vendor"
LAB = ROOT.parent / "PhysicsEngine"


def _vendor_script():
    spec = importlib.util.spec_from_file_location("vendor_cluster_flow", ROOT / "scripts" / "vendor_cluster_flow.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ClusterFlowVendoredSourceTest(unittest.TestCase):
    def test_core_has_no_front_end(self) -> None:
        core = (VENDOR / "apps" / "cosmic" / "cosmic_core.hpp").read_text(encoding="utf-8")
        core = core.split("#pragma once", 1)[1]
        for token in ("ImGui", "imgui", "glfw", "GLFW", "int main(", "int run(", "Options parse("):
            self.assertNotIn(token, core)
        for kept in ("class Worker", "void bakeFlow(", "int verifyWorker(", "void configure(", "Controls initialControls("):
            self.assertIn(kept, core)
        help_text = (VENDOR / "apps" / "cosmic" / "control_help.hpp").read_text(encoding="utf-8")
        self.assertNotIn("ImGui::", help_text)
        self.assertNotIn("#include <imgui", help_text)
        self.assertIn("inline char const* help(", help_text)

    @unittest.skipUnless((LAB / "apps" / "cosmic" / "main.cpp").is_file(), "PhysicsEngine checkout not present")
    def test_vendored_code_matches_the_lab(self) -> None:
        script = _vendor_script()
        for relative in script.LIBRARY:
            self.assertEqual((VENDOR / relative).read_bytes(), (LAB / relative).read_bytes(), relative)
        cosmic = LAB / "apps" / "cosmic"
        core = script.strip_core((cosmic / "main.cpp").read_text(encoding="utf-8"))
        self.assertEqual((VENDOR / "apps" / "cosmic" / "cosmic_core.hpp").read_text(encoding="utf-8"), core)
        help_text = script.strip_help((cosmic / "control_help.hpp").read_text(encoding="utf-8"))
        self.assertEqual((VENDOR / "apps" / "cosmic" / "control_help.hpp").read_text(encoding="utf-8"), help_text)


@unittest.skipUnless(sys.platform == "win32", "the Cluster Flow engine is Windows-only")
class ClusterFlowEngineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        from photometry_app.ui.cluster_flow_view import ClusterFlowPanel, library_error, load_library

        if load_library() is None:
            raise unittest.SkipTest(library_error())
        cls._app = QApplication.instance() or QApplication([])
        if cls._app.platformName() == "offscreen":
            raise unittest.SkipTest("Qt's offscreen platform cannot host OpenGL windows")
        cls.panel = ClusterFlowPanel()
        cls.panel.resize(1200, 800)
        cls.panel.show()
        cls.pump(lambda: cls.panel.viewport is not None and cls.panel.viewport.engine is not None, 10)
        cls.engine = cls.panel.viewport.engine if cls.panel.viewport is not None else None
        if cls.engine is None:
            cls.panel.close()
            raise unittest.SkipTest("no OpenGL 3.3 context")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.panel.close()
        cls._app.processEvents()

    @classmethod
    def pump(cls, done, seconds: float) -> None:
        from PySide6.QtCore import QEventLoop

        end = time.time() + seconds
        while time.time() < end and not done():
            cls._app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)

    def test_lab_worker_self_test(self) -> None:
        from photometry_app.ui.cluster_flow_view import load_library

        lib = load_library()
        self.assertEqual(lib.cf_verify_worker(), 1)

    def test_every_panel_control_has_an_engine_field_and_lab_tooltip(self) -> None:
        panel = self.panel
        keys = [key for key, _ in panel._sliders] + [key for key, _ in panel._checks]  # noqa: SLF001
        keys += [key for key, _, _ in panel._choices] + ["scene.seed"]  # noqa: SLF001
        missing = []
        for key in keys:
            try:
                self.engine.get(key)
            except KeyError:
                missing.append(key)
        self.assertEqual(missing, [])
        untipped = [slider.label for _, slider in panel._sliders if not slider.toolTip()]  # noqa: SLF001
        self.assertEqual(untipped, [])

    def test_live_run_bake_and_replay(self) -> None:
        from PySide6.QtGui import QImage

        engine = self.engine
        self.pump(lambda: engine.get("uploaded") and engine.get("shown.time") > 0, 20)
        self.assertGreater(engine.get("shown.time"), 0)
        self.assertEqual(engine.text("status"), "")
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "view.png"
            self.panel.viewport.with_context(lambda e: e.render_png(image, 320, 180))
            picture = QImage(str(image))
            self.assertEqual((picture.width(), picture.height()), (320, 180))
            lit = sum(1 for y in range(0, 180, 4) for x in range(0, 320, 4) if picture.pixelColor(x, y).lightness() > 40)
            self.assertGreater(lit, 10)

            engine.set("bake.duration", 0.1)
            engine.set("bake.frames", 3)
            engine.set("bake.grid", 16)
            engine.set("bake.tracers", 500)
            cache = Path(folder) / "run.cflow"
            engine.bake(cache)
            self.pump(lambda: not engine.get("bake.busy") and engine.get("replay"), 120)
            self.assertTrue(engine.get("replay"), engine.text("status"))
            self.assertEqual(engine.get("replay.frames"), 3)
            self.assertIn("Cosmic Flow cache v4", engine.text("description"))
            engine.set("playHead", 2)
            self.pump(lambda: False, 0.3)
            self.assertEqual(engine.get("playHead"), 2)
            engine.action("returnLive")
            self.pump(lambda: False, 0.3)
            self.assertFalse(engine.get("replay"))


if __name__ == "__main__":
    unittest.main()
