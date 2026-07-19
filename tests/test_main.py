from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtCore import Qt

from photometry_app import main as main_module


class MainStartupTest(unittest.TestCase):

    def test_velopack_startup_applies_user_downloaded_update_on_next_launch(self) -> None:

        app = MagicMock()
        app.set_auto_apply_on_startup.return_value = app
        with patch("velopack.App", return_value=app):
            main_module._run_velopack_startup()

        app.set_auto_apply_on_startup.assert_called_once_with(True)
        app.run.assert_called_once_with()

    def test_packaging_smoke_invocations_skip_unmanaged_velopack_startup(self) -> None:

        for flag in (
            "--qt-image-format-smoke",
            "--packaged-format-smoke",
            "--about-dialog-smoke",
        ):
            self.assertTrue(
                main_module._is_packaging_smoke_invocation(["cast.exe", flag]),
                flag,
            )
        self.assertFalse(
            main_module._is_packaging_smoke_invocation(["cast.exe"])
        )

    def test_configure_qt_application_attributes_enables_shared_opengl_contexts_before_app_exists(self) -> None:

        with (
            patch("PySide6.QtCore.QCoreApplication.instance", return_value=None),
            patch("PySide6.QtCore.QCoreApplication.setAttribute") as set_attribute,
        ):
            main_module._configure_qt_application_attributes()

        set_attribute.assert_called_once_with(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

    def test_configure_qt_application_attributes_does_not_change_attributes_after_app_exists(self) -> None:

        with (
            patch("PySide6.QtCore.QCoreApplication.instance", return_value=object()),
            patch("PySide6.QtCore.QCoreApplication.setAttribute") as set_attribute,
        ):
            main_module._configure_qt_application_attributes()

        set_attribute.assert_not_called()


if __name__ == "__main__":

    unittest.main()