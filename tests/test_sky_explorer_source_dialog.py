from __future__ import annotations

import unittest

from PySide6.QtWidgets import QApplication

from photometry_app.ui.sky_explorer_source_dialog import SkyExplorerSourceDialog


class SkyExplorerSourceDialogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_dialog_defaults_to_upload_choice(self) -> None:
        dialog = SkyExplorerSourceDialog(
            title="Open Sky Explorer Source",
            intro="Choose a source.",
            survey_options=(("dss2_blue", "DSS2 Blue"), ("panstarrs", "PanSTARRS")),
        )

        choice = dialog.choice()

        self.assertEqual(choice.kind, "upload")
        self.assertTrue(dialog._upload_radio.isChecked())
        self.assertFalse(dialog._survey_box.isVisible())

    def test_dialog_returns_selected_survey_choice(self) -> None:
        dialog = SkyExplorerSourceDialog(
            title="Comparison",
            intro="Choose a comparison source.",
            survey_options=(("dss2_blue", "DSS2 Blue"), ("panstarrs", "PanSTARRS")),
            initial_survey_key="panstarrs",
            field_hint="Survey tiles follow the image WCS.",
        )
        dialog._survey_radio.setChecked(True)

        choice = dialog.choice()

        self.assertEqual(choice.kind, "survey")
        self.assertEqual(choice.survey_key, "panstarrs")
        self.assertTrue(dialog._survey_combo.isEnabled())
        self.assertTrue(dialog._field_hint.isVisibleTo(dialog))
        self.assertIn("WCS", dialog._field_hint.text())


if __name__ == "__main__":
    unittest.main()
