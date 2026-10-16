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
        self.assertEqual(dialog._mark_only_faintest_checkbox.text(), "Show only faintest")
        self.assertTrue(dialog._clear_button.isEnabled())
        self.assertEqual(dialog._clear_button.text(), "Clear")
        self.assertEqual(dialog._run_button.text(), "Estimate limit")
        self.assertEqual(dialog._help_label.text(), "Gaia → Pan-STARRS → DES DR1 / SkyMapper")
        self.assertIn("Pan-STARRS", dialog._catalog_info_button.toolTip())
        self.assertTrue(dialog._sampling_group.isHidden())
        self.assertFalse(dialog._auto_group.isHidden())
        self.assertIn("13.0–18.0 mag", dialog._summary_label.text())
        self.assertIn("10 sources/step", dialog._summary_label.text())
        self.assertIn("≥7 detections", dialog._summary_label.text())
        self.assertIn("SNR ≥5.0", dialog._summary_label.text())

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

    def test_mode_selector_shows_only_the_active_controls(self) -> None:
        dialog = SkyExplorerMagLimitDialog()
        self.assertTrue(dialog._ladder_mode_button.isChecked())
        self.assertEqual(dialog._run_button.text(), "Mark")
        self.assertFalse(dialog._sampling_group.isHidden())
        self.assertTrue(dialog._auto_group.isHidden())
        self.assertEqual(dialog._start_magnitude_input.value(), 12.0)
        self.assertEqual(dialog._max_magnitude_input.value(), 17.0)
        self.assertEqual(dialog._increment_input.value(), 0.5)
        self.assertEqual(dialog._summary_label.text(), "11 magnitude steps · up to 11 reference sources")

        dialog._examples_per_bin_input.setValue(5)
        dialog._start_magnitude_input.setValue(10.0)
        dialog._max_magnitude_input.setValue(30.0)
        self.assertEqual(dialog._summary_label.text(), "41 magnitude steps · up to 205 reference sources")

        dialog._auto_mode_button.setChecked(True)
        self.assertEqual(dialog._run_button.text(), "Estimate limit")
        self.assertTrue(dialog._sampling_group.isHidden())
        self.assertFalse(dialog._auto_group.isHidden())
        self.assertEqual(dialog.selected_options().mode, "auto")
        self.assertIn("10.0–30.0 mag", dialog._summary_label.text())

        dialog._ladder_mode_button.setChecked(True)
        self.assertFalse(dialog._sampling_group.isHidden())
        self.assertTrue(dialog._auto_group.isHidden())
        self.assertEqual(dialog.selected_options().mode, "ladder")
        self.assertEqual(dialog.selected_options().examples_per_bin, 5)
        self.assertEqual(dialog.selected_options().max_magnitude, 30.0)
        self.assertFalse(dialog.selected_options().only_detected_sources)
        self.assertTrue(dialog._snr_host.isHidden())

        dialog._only_detected_checkbox.setChecked(True)
        self.assertFalse(dialog._snr_host.isHidden())
        self.assertEqual(dialog._summary_label.text(), "41 magnitude steps · up to 205 detected sources")
        self.assertTrue(dialog.selected_options().only_detected_sources)

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

    def test_detected_source_check_keeps_a_real_peak_and_drops_empty_sky(self) -> None:
        import numpy as np

        from photometry_app.core.solar_system import catalog_position_is_detected

        image = np.full((48, 48), 100.0)
        image[24, 24] = 5000.0
        self.assertTrue(catalog_position_is_detected(image, 24.0, 24.0, snr_threshold=5.0))
        self.assertFalse(catalog_position_is_detected(np.full((48, 48), 100.0), 24.0, 24.0, snr_threshold=5.0))


if __name__ == "__main__":
    unittest.main()
