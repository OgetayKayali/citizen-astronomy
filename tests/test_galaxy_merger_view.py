from __future__ import annotations

import importlib.util
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "native" / "merger" / "vendor"
LAB = ROOT.parent / "PhysicsEngine"


def _vendor_script():
    spec = importlib.util.spec_from_file_location("vendor_galaxy_merger", ROOT / "scripts" / "vendor_galaxy_merger.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GalaxyMergerVendoredSourceTest(unittest.TestCase):
    def test_viewer_core_has_no_front_end(self) -> None:
        core = (VENDOR / "apps" / "viewer" / "viewer_core.hpp").read_text(encoding="utf-8")
        core = core.split("inline std::filesystem::path cast_examples_directory;", 1)[1]
        for token in ("ImGui", "imgui", "glfwSet", "GLFWwindow", "int main("):
            self.assertNotIn(token, core)
        for kept in ("void lab_draw_scene(", "void draw_scene(", "void launch_simulation(", "bool capture_rgb(", "void pump_export("):
            self.assertIn(kept, core)

    def test_builtin_scenes_and_looks_ship(self) -> None:
        examples = ROOT / "photometry_app" / "native" / "galaxy_merger"
        for name in _vendor_script().SCENES:
            self.assertTrue((examples / name).is_file(), name)

    @unittest.skipUnless((LAB / "apps" / "viewer" / "main.cpp").is_file(), "PhysicsEngine checkout not present")
    def test_vendored_code_matches_the_lab(self) -> None:
        script = _vendor_script()
        for relative in script.LIBRARY:
            self.assertEqual((VENDOR / relative).read_bytes(), (LAB / relative).read_bytes(), relative)
        core = script.strip_viewer((LAB / "apps" / "viewer" / "main.cpp").read_text(encoding="utf-8"))
        self.assertEqual((VENDOR / "apps" / "viewer" / "viewer_core.hpp").read_text(encoding="utf-8"), core)


@unittest.skipUnless(sys.platform == "win32", "the merger engine is Windows-only")
class GalaxyMergerEngineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        from photometry_app.ui.galaxy_merger_view import GalaxyMergerPanel, library_error, load_library

        if load_library() is None:
            raise unittest.SkipTest(library_error())
        cls._app = QApplication.instance() or QApplication([])
        if cls._app.platformName() == "offscreen":
            raise unittest.SkipTest("Qt's offscreen platform cannot host OpenGL windows")
        cls.panel = GalaxyMergerPanel()
        cls.panel.resize(1200, 800)
        cls.panel.show()
        cls.pump(lambda: cls.panel.viewport.engine is not None, 10)
        cls.engine = cls.panel.viewport.engine
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

    def test_every_panel_control_has_an_engine_field(self) -> None:
        panel = self.panel
        keys = [key for group in (panel._sliders, panel._checks, panel._combos, panel._numbers, panel._arms) for key, _ in group]  # noqa: SLF001
        keys += [f"{key}.{axis}" for key, _ in panel._vectors for axis in "xyz"]  # noqa: SLF001
        keys += [f"{key}.{channel}" for key, _ in panel._palettes for channel in "rgb"]  # noqa: SLF001
        missing = []
        for key in keys:
            try:
                self.engine.get(key)
            except KeyError:
                missing.append(key)
        self.assertEqual(missing, [])
        self.assertEqual(self.engine.text("name"), "Galaxy merger")
        self.assertRegex(self.engine.text("seed0"), r"^\d+$")

    def test_short_run_renders(self) -> None:
        from PySide6.QtGui import QImage

        engine = self.engine
        engine.action("quality.0")
        engine.set("steps", 40)
        engine.set("sampleEvery", 10)
        engine.action("run")
        self.pump(lambda: not engine.get("busy"), 120)
        self.assertFalse(engine.get("busy"))
        self.assertEqual(engine.text("error"), "")
        self.assertTrue(engine.get("hasSamples"))
        self.assertAlmostEqual(engine.get("timeEnd"), 40 * engine.get("dt"))
        engine.set("playhead", engine.get("timeEnd"))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "frame.png"
            engine.render_png(path, 320, 180)
            image = QImage(str(path))
            self.assertEqual((image.width(), image.height()), (320, 180))
            lit = self.lit_pixels(image, 40)
            self.assertGreater(lit, 10)

            engine.set("sky.milkyWay", 0)
            engine.set("sky.stars", 1)
            engine.set("sky.catalog", 0)
            engine.set("sky.starBrightness", 12)
            try:
                engine.render_png(path, 320, 180)
            finally:
                engine.set("sky.stars", 0)
                engine.set("sky.catalog", 1)
                engine.set("sky.starBrightness", 4)
            self.assertGreater(self.lit_pixels(QImage(str(path)), 1), self.lit_pixels(image, 1))

    @staticmethod
    def lit_pixels(image, threshold: int) -> int:
        return sum(1 for y in range(image.height()) for x in range(image.width()) if image.pixelColor(x, y).lightness() > threshold)


if __name__ == "__main__":
    unittest.main()
