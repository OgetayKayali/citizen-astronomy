from __future__ import annotations

import unittest

from PySide6.QtWidgets import QApplication

from photometry_app.ui.sky_explorer_mag_limit_dialog import (
    SkyExplorerMagLimitDialog,
    SkyExplorerMagLimitOptions,
    mag_limit_text_scale_factor,
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
        self.assertEqual(options.start_magnitude, 12.0)
        self.assertEqual(options.faintest_count, 1)
        self.assertEqual(options.text_scale_mode, "off")

    def test_dialog_returns_selected_options_for_auto_mode(self) -> None:
        dialog = SkyExplorerMagLimitDialog(
            initial_options=SkyExplorerMagLimitOptions(
                mode="auto",
                bin_size_mag=0.5,
                examples_per_bin=2,
                mark_only_faintest=True,
                faintest_count=3,
                start_magnitude=13.0,
                max_magnitude=18.0,
                auto_stars_per_bin=10,
                auto_required_visible_stars=7,
                auto_start_magnitude=12.0,
                auto_step_magnitude=0.5,
                auto_snr_threshold=5.0,
                text_scale_mode="fainter",
            ),
            markers_active=True,
        )
        selected = dialog.selected_options()
        self.assertEqual(selected.mode, "auto")
        self.assertEqual(selected.examples_per_bin, 2)
        self.assertTrue(selected.mark_only_faintest)
        self.assertEqual(selected.faintest_count, 3)
        self.assertEqual(selected.start_magnitude, 13.0)
        self.assertEqual(selected.max_magnitude, 18.0)
        self.assertEqual(selected.bin_size_mag, 0.5)
        self.assertEqual(selected.auto_start_magnitude, 13.0)
        self.assertEqual(selected.auto_step_magnitude, 0.5)
        self.assertEqual(selected.auto_stars_per_bin, 10)
        self.assertEqual(selected.auto_required_visible_stars, 7)
        self.assertEqual(selected.auto_snr_threshold, 5.0)
        self.assertEqual(selected.text_scale_mode, "fainter")
        self.assertEqual(dialog._mark_only_faintest_checkbox.text(), "Mark only the faintest N")
        self.assertTrue(dialog._clear_button.isEnabled())

    def test_dialog_normalizes_inverted_magnitude_range(self) -> None:
        dialog = SkyExplorerMagLimitDialog(
            initial_options=SkyExplorerMagLimitOptions(start_magnitude=18.0, max_magnitude=12.0, bin_size_mag=0.25)
        )
        selected = dialog.selected_options()
        self.assertEqual(selected.start_magnitude, 12.0)
        self.assertEqual(selected.max_magnitude, 18.0)
        self.assertEqual(selected.bin_size_mag, 0.25)
        self.assertEqual(selected.auto_start_magnitude, 12.0)
        self.assertEqual(selected.auto_step_magnitude, 0.25)

    def test_text_scale_factor_grows_in_the_chosen_direction(self) -> None:
        self.assertEqual(mag_limit_text_scale_factor(12.0, min_magnitude=12.0, max_magnitude=16.0, mode="off"), 1.0)
        self.assertAlmostEqual(
            mag_limit_text_scale_factor(16.0, min_magnitude=12.0, max_magnitude=16.0, mode="fainter"),
            1.3,
        )
        self.assertAlmostEqual(
            mag_limit_text_scale_factor(12.0, min_magnitude=12.0, max_magnitude=16.0, mode="fainter"),
            0.75,
        )
        self.assertAlmostEqual(
            mag_limit_text_scale_factor(12.0, min_magnitude=12.0, max_magnitude=16.0, mode="brighter"),
            1.3,
        )


if __name__ == "__main__":
    unittest.main()
