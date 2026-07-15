from __future__ import annotations

import importlib
import unittest


class RuntimeDependencyImportTest(unittest.TestCase):
    def test_visualization_runtime_dependencies_are_importable(self) -> None:
        missing: list[str] = []
        for package_label, module_name in (
            ("pyqtgraph", "pyqtgraph"),
            ("pyqtgraph.opengl", "pyqtgraph.opengl"),
            ("PyOpenGL", "OpenGL"),
        ):
            try:
                importlib.import_module(module_name)
            except Exception as exc:
                missing.append(f"{package_label}: {type(exc).__name__}: {exc}")

        self.assertFalse(
            missing,
            "Active Python environment is missing required visualization dependencies. "
            "Resync it with `python -m pip install -e .` before launching the app.\n"
            + "\n".join(missing),
        )