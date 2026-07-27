from __future__ import annotations

import unittest

from PySide6.QtWidgets import QApplication

from photometry_app.ui.sky_explorer_mag_limit_dialog import (
    SkyExplorerMagLimitDialog,
    SkyExplorerMagLimitOptions,
)


class SkyExplorerMagLimitDialogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_default_auto_options_match_requested_defaults(self) -> None:
        options = SkyExplorerMagLimitOptions()
        self.assertEqual(options.mode, "ladder")
        self.assertEqual(options.bin_size_mag, 0.5)
        self.assertEqual(options.examples_per_bin, 1)
        self.assertFalse(options.mark_only_faintest)
        self.assertEqual(options.auto_stars_per_bin, 10)
        self.assertEqual(options.auto_required_visible_stars, 7)
        self.assertEqual(options.auto_snr_threshold, 5.0)
        self.assertEqual(options.auto_step_magnitude, 0.5)

    def test_dialog_returns_selected_options_for_auto_mode(self) -> None:
        dialog = SkyExplorerMagLimitDialog(
            initial_options=SkyExplorerMagLimitOptions(
                mode="auto",
                bin_size_mag=0.5,
                examples_per_bin=2,
                mark_only_faintest=True,
                max_magnitude=18.0,
                auto_stars_per_bin=10,
                auto_required_visible_stars=7,
                auto_start_magnitude=12.0,
                auto_step_magnitude=0.5,
                auto_snr_threshold=5.0,
            ),
            markers_active=True,
        )
        selected = dialog.selected_options()
        self.assertEqual(selected.mode, "auto")
        self.assertEqual(selected.examples_per_bin, 2)
        self.assertTrue(selected.mark_only_faintest)
        self.assertEqual(selected.auto_stars_per_bin, 10)
        self.assertEqual(selected.auto_required_visible_stars, 7)
        self.assertEqual(selected.auto_snr_threshold, 5.0)
        self.assertTrue(dialog._clear_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
