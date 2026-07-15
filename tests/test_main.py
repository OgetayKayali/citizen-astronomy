from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt

from photometry_app import main as main_module


class MainStartupTest(unittest.TestCase):

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