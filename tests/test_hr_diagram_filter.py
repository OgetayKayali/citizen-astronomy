from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest

from PySide6.QtWidgets import QApplication

from photometry_app.core.hr_diagram import HrMeasurementRow, HrWorkingTable
from photometry_app.core.hr_diagram_filter import (
    HR_LUMINOSITY_CLASS_GUIDES,
    HrDiagramFilter,
    color_index_to_temperature_kelvin,
    hr_diagram_filter_work_log_lines,
    luminosity_class_for_position,
    spectral_type_for_temperature,
    summarize_hr_diagram_filter,
)
from photometry_app.ui.hr_diagram_filter_dialog import HrDiagramFilterDialog
from photometry_app.ui.hr_plot_widget import HrDiagramPlotWidget


def _star(**overrides: object) -> HrMeasurementRow:
    values: dict[str, object] = {
        "source_id": "1",
        "source_name": "1",
        "catalog": "gaia-dr3",
        "ra_deg": 0.0,
        "dec_deg": 0.0,
        "gaia_g_mag": 12.0,
        "gaia_bp_rp": 0.9,
        "parallax_mas": 10.0,
        "parallax_error_mas": 0.1,
        "x": 10.0,
        "y": 10.0,
        "aperture_radius": 5.0,
        "annulus_inner_radius": 8.0,
        "annulus_outer_radius": 12.0,
        "gaia_absolute_magnitude": 4.67,
        "instrumental_blue_minus_red": 0.2,
        "calibrated_mag_luminance": 12.0,
    }
    values.update(overrides)
    return HrMeasurementRow(**values)  # type: ignore[arg-type]


class HrDiagramFilterTest(unittest.TestCase):
    def test_temperature_scale_matches_the_hr_plot(self) -> None:
        widget = HrDiagramPlotWidget()
        for color in (-0.4, 0.0, 0.3, 0.9, 1.5, 3.0):
            self.assertAlmostEqual(
                color_index_to_temperature_kelvin(color),
                float(widget.color_index_to_temperature_kelvin(color)),
            )
        guides = widget._build_class_guide_specs()
        self.assertEqual(
            [(guide.name, tuple(guide.x_values), tuple(guide.y_values)) for guide in guides],
            list(HR_LUMINOSITY_CLASS_GUIDES),
        )

    def test_spectral_type_bins_follow_temperature(self) -> None:
        self.assertEqual(spectral_type_for_temperature(30000), "O")
        self.assertEqual(spectral_type_for_temperature(12000), "B")
        self.assertEqual(spectral_type_for_temperature(8000), "A")
        self.assertEqual(spectral_type_for_temperature(6200), "F")
        self.assertEqual(spectral_type_for_temperature(5300), "G")
        self.assertEqual(spectral_type_for_temperature(4600), "K")
        self.assertEqual(spectral_type_for_temperature(3200), "M")
        self.assertEqual(spectral_type_for_temperature(3700), "K")

    def test_luminosity_class_uses_the_class_guides(self) -> None:
        self.assertEqual(luminosity_class_for_position(0.6, 2.2), "Main Sequence")
        self.assertEqual(luminosity_class_for_position(1.45, -1.8), "Giants")
        self.assertEqual(luminosity_class_for_position(0.15, 9.8), "White Dwarfs")
        self.assertIsNone(luminosity_class_for_position(0.6, 6.0))

    def test_missing_selected_values_are_removed_before_out_of_range_stars(self) -> None:
        diagram_filter = HrDiagramFilter(
            color_enabled=True,
            temperature_min_k=4000,
            temperature_max_k=7000,
            distance_enabled=True,
            distance_min_pc=50,
            distance_max_pc=500,
            luminosity_enabled=True,
            luminosity_min_solar=0.1,
            luminosity_max_solar=10,
        )
        rows = [
            _star(source_id="missing-color", gaia_bp_rp=None),
            _star(source_id="too-cool", gaia_bp_rp=2.2),
            _star(source_id="missing-distance", parallax_mas=None),
            _star(source_id="negative-parallax", parallax_mas=-1.0),
            _star(source_id="too-far", parallax_mas=1.0),
            _star(source_id="missing-luminosity", gaia_absolute_magnitude=None),
            _star(source_id="too-bright", gaia_absolute_magnitude=0.0),
            _star(source_id="kept", gaia_bp_rp=0.9, parallax_mas=10.0, gaia_absolute_magnitude=4.67),
        ]

        summary = summarize_hr_diagram_filter(rows, diagram_filter)

        self.assertEqual(summary.considered, 8)
        self.assertEqual(summary.kept, 1)
        self.assertEqual(summary.removed["missing_color"], 1)
        self.assertEqual(summary.removed["outside_color"], 1)
        self.assertEqual(summary.removed["missing_distance"], 2)
        self.assertEqual(summary.removed["outside_distance"], 1)
        self.assertEqual(summary.removed["missing_luminosity"], 1)
        self.assertEqual(summary.removed["outside_luminosity"], 1)
        self.assertEqual(sum(summary.removed.values()) + summary.kept, summary.considered)

        notes = "\n".join(
            hr_diagram_filter_work_log_lines(summary, plotted_count=1, matching_count=1, roi_active=False)
        )
        self.assertIn("color and temperature were unavailable", notes)
        self.assertIn("no usable Gaia parallax, so distance was unavailable", notes)
        self.assertIn("no Gaia absolute G magnitude, so luminosity was unavailable", notes)
        self.assertIn("Not used: stellar class.", notes)
        self.assertIn("This filter keeps 1 of 8 stars.", notes)
        self.assertIn("1 plotted star and 1 matching table row", notes)

    def test_stellar_class_removes_unclassified_and_unselected_stars(self) -> None:
        diagram_filter = HrDiagramFilter(
            stellar_class_enabled=True,
            spectral_types=frozenset({"G"}),
            luminosity_classes=frozenset({"Main Sequence"}),
        )
        rows = [
            _star(source_id="g-dwarf", gaia_bp_rp=0.9, gaia_absolute_magnitude=5.0),
            _star(source_id="k-dwarf", gaia_bp_rp=1.2, gaia_absolute_magnitude=7.0),
            _star(source_id="unclassified", gaia_bp_rp=0.9, gaia_absolute_magnitude=9.0),
            _star(source_id="no-color", gaia_bp_rp=None, gaia_absolute_magnitude=5.0),
        ]

        summary = summarize_hr_diagram_filter(rows, diagram_filter)

        self.assertEqual(summary.kept, 1)
        self.assertEqual(summary.removed["outside_stellar_class"], 1)
        self.assertEqual(summary.removed["missing_stellar_class"], 2)
        notes = "\n".join(hr_diagram_filter_work_log_lines(summary))
        self.assertIn("spectral types G", notes)
        self.assertIn("luminosity classes Main Sequence", notes)
        self.assertIn("no stellar class", notes)
        self.assertIn("whose class was not selected", notes)
        self.assertEqual(summary.kept_spectral_types, {"G": 1})
        self.assertEqual(summary.kept_luminosity_classes, {"Main Sequence": 1})

    def test_inactive_filter_keeps_every_star(self) -> None:
        summary = summarize_hr_diagram_filter([_star(), _star(source_id="2", gaia_bp_rp=None)], HrDiagramFilter())
        self.assertEqual(summary.kept, 2)
        self.assertEqual(
            hr_diagram_filter_work_log_lines(summary),
            [
                "HR diagram filter is off. Color/temperature, distance, luminosity, and stellar class are not removing stars."
            ],
        )

    def test_dialog_round_trip_links_temperature_and_color(self) -> None:
        dialog = HrDiagramFilterDialog(HrDiagramFilter())
        dialog._color_group.setChecked(True)
        dialog._hot_temperature_spin.setValue(10000)
        self.assertAlmostEqual(dialog._hot_color_spin.value(), 0.0, places=2)
        dialog._cool_temperature_spin.setValue(4600)
        self.assertAlmostEqual(dialog._cool_color_spin.value(), 1.2, places=2)
        dialog._distance_group.setChecked(True)
        dialog._distance_min_spin.setValue(80)
        dialog._distance_max_spin.setValue(40)
        settings = dialog.filter_settings()
        self.assertTrue(settings.color_enabled)
        self.assertTrue(settings.distance_enabled)
        self.assertAlmostEqual(settings.temperature_max_k, 10000)
        self.assertAlmostEqual(settings.temperature_min_k, 4600)
        self.assertAlmostEqual(settings.distance_min_pc, 40)
        self.assertAlmostEqual(settings.distance_max_pc, 80)
        self.assertFalse(settings.luminosity_enabled)
        self.assertFalse(settings.stellar_class_enabled)

    def test_plot_hides_stars_missing_the_selected_color(self) -> None:
        widget = HrDiagramPlotWidget()
        table = HrWorkingTable(
            rows=[
                _star(source_id="kept", gaia_bp_rp=0.9, instrumental_blue_minus_red=0.2, calibrated_mag_luminance=12.0),
                _star(
                    source_id="missing",
                    gaia_bp_rp=None,
                    instrumental_blue_minus_red=0.4,
                    calibrated_mag_luminance=13.0,
                ),
            ],
            plane_labels=["red", "green", "blue", "luminance"],
            measured_count=2,
            usable_count=2,
        )
        widget.set_axes("instrumental_blue_minus_red", "calibrated_mag_luminance")
        widget.set_filters(hide_flagged=False, hide_saturated=False, require_parallax=False)
        widget.set_diagram_filter(HrDiagramFilter(color_enabled=True, temperature_min_k=2600, temperature_max_k=30000))
        widget.plot_working_table(table)

        self.assertEqual({row.source_id for row in widget.visible_rows()}, {"kept"})
        self.assertEqual(widget.plotted_row_count(), 1)


# Widgets in these tests need an application before the first plot or dialog is built.
_APP = QApplication.instance() or QApplication([])
