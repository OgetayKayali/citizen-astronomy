from __future__ import annotations

import unittest

from PySide6.QtWidgets import QApplication

from photometry_app.ui.sky_explorer_source_dialog import SkyExplorerSourceDialog


class SkyExplorerSourceDialogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_dialog_defaults_to_first_survey_choice(self) -> None:
        dialog = SkyExplorerSourceDialog(
            title="Open Sky Explorer Survey",
            intro="Choose a survey.",
            survey_options=(("dss2_blue", "DSS2 Blue"), ("panstarrs", "PanSTARRS")),
        )

        choice = dialog.choice()

        self.assertEqual(choice.kind, "survey")
        self.assertEqual(choice.survey_key, "dss2_blue")
        self.assertTrue(dialog._survey_combo.isEnabled())

    def test_dialog_returns_selected_survey_choice(self) -> None:
        dialog = SkyExplorerSourceDialog(
            title="Choose Sky Explorer Survey",
            intro="Choose a comparison survey.",
            survey_options=(("dss2_blue", "DSS2 Blue"), ("panstarrs", "PanSTARRS")),
            initial_survey_key="panstarrs",
            field_hint="Survey tiles follow the image WCS.",
        )

        choice = dialog.choice()

        self.assertEqual(choice.kind, "survey")
        self.assertEqual(choice.survey_key, "panstarrs")
        self.assertTrue(dialog._field_hint.isVisibleTo(dialog))
        self.assertIn("WCS", dialog._field_hint.text())


if __name__ == "__main__":
    unittest.main()
