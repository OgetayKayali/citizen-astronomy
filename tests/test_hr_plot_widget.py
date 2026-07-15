from __future__ import annotations



import os

from pathlib import Path

import tempfile

import unittest



import numpy as np
from PySide6.QtCore import Qt

from PySide6.QtWidgets import QApplication



from photometry_app.core.hr_diagram import HrMeasurementRow, HrWorkingTable

from photometry_app.ui.hr_plot_widget import HrDiagramPlotWidget





os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")





class HrDiagramPlotWidgetTest(unittest.TestCase):

    @classmethod

    def setUpClass(cls) -> None:

        cls._app = QApplication.instance() or QApplication([])



    def setUp(self) -> None:

        self.widget = HrDiagramPlotWidget()



    def tearDown(self) -> None:

        self.widget.close()



    def test_plot_working_table_uses_gaia_absolute_magnitude_by_default(self) -> None:

        table = HrWorkingTable(

            rows=[

                HrMeasurementRow(

                    source_id="1",

                    source_name="1",

                    catalog="gaia-dr3",

                    ra_deg=0.0,

                    dec_deg=0.0,

                    gaia_g_mag=12.0,

                    gaia_bp_rp=0.8,

                    parallax_mas=10.0,

                    parallax_error_mas=0.2,

                    x=10.0,

                    y=10.0,

                    aperture_radius=5.0,

                    annulus_inner_radius=8.0,

                    annulus_outer_radius=12.0,

                    gaia_absolute_magnitude=5.0,

                    absolute_magnitude_proxy=5.0,

                    plot_color_index=0.8,

                    display_color_hex="#ffffff",

                )

            ],

            plane_labels=["red", "green", "blue", "luminance"],

            measured_count=1,

            usable_count=1,

        )



        self.widget.plot_working_table(table)



        self.assertIn("Showing 1 of 1", self.widget._status_label.text())

        self.assertEqual(self.widget._x_axis_mode, "gaia_bp_rp")

        self.assertEqual(self.widget._y_axis_mode, "gaia_absolute_magnitude")



    def test_default_hr_view_moves_temperature_and_luminosity_to_bottom_and_left(self) -> None:

        table = HrWorkingTable(

            rows=[

                HrMeasurementRow(

                    source_id="1",

                    source_name="1",

                    catalog="gaia-dr3",

                    ra_deg=0.0,

                    dec_deg=0.0,

                    gaia_g_mag=12.0,

                    gaia_bp_rp=0.8,

                    parallax_mas=10.0,

                    parallax_error_mas=0.2,

                    x=10.0,

                    y=10.0,

                    aperture_radius=5.0,

                    annulus_inner_radius=8.0,

                    annulus_outer_radius=12.0,

                    gaia_absolute_magnitude=5.0,

                    plot_color_index=0.8,

                )

            ],

            plane_labels=["red", "green", "blue", "luminance"],

            measured_count=1,

            usable_count=1,

        )



        self.widget.plot_working_table(table)



        self.assertEqual(self.widget._plot_item.getAxis("bottom").labelText, "Color Temperature (K)")

        self.assertEqual(self.widget._plot_item.getAxis("left").labelText, "Luminosity (L_sun)")

        self.assertEqual(self.widget._plot_item.getAxis("top").labelText, "Gaia BP-RP")

        self.assertEqual(self.widget._plot_item.getAxis("right").labelText, "Gaia Absolute G Magnitude")



    def test_unsupported_secondary_scales_fall_back_to_primary_bottom_and_left_axes(self) -> None:

        table = HrWorkingTable(

            rows=[

                HrMeasurementRow(

                    source_id="1",

                    source_name="1",

                    catalog="gaia-dr3",

                    ra_deg=0.0,

                    dec_deg=0.0,

                    gaia_g_mag=12.0,

                    gaia_bp_rp=0.8,

                    parallax_mas=None,

                    parallax_error_mas=None,

                    x=10.0,

                    y=10.0,

                    aperture_radius=5.0,

                    annulus_inner_radius=8.0,

                    annulus_outer_radius=12.0,

                    calibrated_mag_luminance=11.5,

                    instrumental_blue_minus_red=0.3,

                    plot_color_index=0.3,

                )

            ],

            plane_labels=["red", "green", "blue", "luminance"],

            measured_count=1,

            usable_count=1,

        )



        self.widget.set_axes("instrumental_blue_minus_red", "calibrated_mag_luminance")

        self.widget.set_filters(hide_flagged=False, hide_saturated=False, require_parallax=False)

        self.widget.plot_working_table(table)



        self.assertEqual(self.widget._plot_item.getAxis("bottom").labelText, "Instrumental Blue - Red")

        self.assertEqual(self.widget._plot_item.getAxis("left").labelText, "Calibrated Luminance Magnitude")

        self.assertEqual(self.widget._plot_item.getAxis("top").labelText, "")

        self.assertEqual(self.widget._plot_item.getAxis("right").labelText, "")



    def test_absolute_magnitude_luminosity_conversion_uses_solar_reference(self) -> None:

        self.assertAlmostEqual(self.widget.absolute_magnitude_to_luminosity_ratio(4.67), 1.0, places=6)



    def test_color_index_temperature_conversion_is_monotonic(self) -> None:

        self.assertGreater(self.widget.color_index_to_temperature_kelvin(0.0), self.widget.color_index_to_temperature_kelvin(1.5))



    def test_plot_title_can_be_updated(self) -> None:

        self.widget.set_plot_title("M67 HR Diagram")



        self.assertEqual(self.widget.plot_title(), "M67 HR Diagram")



    def test_scientific_export_writes_output_file(self) -> None:

        table = HrWorkingTable(

            rows=[

                HrMeasurementRow(

                    source_id="1",

                    source_name="1",

                    catalog="gaia-dr3",

                    ra_deg=0.0,

                    dec_deg=0.0,

                    gaia_g_mag=12.0,

                    gaia_bp_rp=0.8,

                    parallax_mas=10.0,

                    parallax_error_mas=0.2,

                    x=10.0,

                    y=10.0,

                    aperture_radius=5.0,

                    annulus_inner_radius=8.0,

                    annulus_outer_radius=12.0,

                    gaia_absolute_magnitude=5.0,

                    plot_color_index=0.8,

                    display_color_hex="#ffffff",

                )

            ],

            plane_labels=["red", "green", "blue", "luminance"],

            measured_count=1,

            usable_count=1,

        )



        self.widget.plot_working_table(table)



        with tempfile.TemporaryDirectory() as temp_dir:

            output_path = Path(temp_dir) / "hr_scientific.png"

            self.widget.export_scientific_view(str(output_path))

            self.assertTrue(output_path.exists())



    def test_plot_filters_can_hide_flagged_rows(self) -> None:

        table = HrWorkingTable(

            rows=[

                HrMeasurementRow(

                    source_id="1",

                    source_name="1",

                    catalog="gaia-dr3",

                    ra_deg=0.0,

                    dec_deg=0.0,

                    gaia_g_mag=12.0,

                    gaia_bp_rp=0.8,

                    parallax_mas=10.0,

                    parallax_error_mas=0.2,

                    x=10.0,

                    y=10.0,

                    aperture_radius=5.0,

                    annulus_inner_radius=8.0,

                    annulus_outer_radius=12.0,

                    gaia_absolute_magnitude=5.0,

                    absolute_magnitude_proxy=5.0,

                    plot_color_index=0.8,

                    flags=["flagged"],

                )

            ],

            plane_labels=["red", "green", "blue", "luminance"],

            measured_count=1,

            usable_count=1,

        )



        self.widget.set_filters(hide_flagged=True, hide_saturated=False, require_parallax=True)

        self.widget.plot_working_table(table)



        self.assertIn("No plottable HR rows", self.widget._status_label.text())



    def test_apply_view_updates_selected_circle_settings(self) -> None:

        row = HrMeasurementRow(

            source_id="1",

            source_name="Selected Star",

            catalog="gaia-dr3",

            ra_deg=0.0,

            dec_deg=0.0,

            gaia_g_mag=12.0,

            gaia_bp_rp=0.8,

            parallax_mas=10.0,

            parallax_error_mas=0.2,

            x=10.0,

            y=10.0,

            aperture_radius=5.0,

            annulus_inner_radius=8.0,

            annulus_outer_radius=12.0,

            gaia_absolute_magnitude=5.0,

            absolute_magnitude_proxy=5.0,

            plot_color_index=0.8,

        )

        table = HrWorkingTable(

            rows=[row],

            plane_labels=["red", "green", "blue", "luminance"],

            measured_count=1,

            usable_count=1,

        )



        self.widget.set_selected_row(row)

        self.widget.apply_view(

            x_axis_mode="gaia_bp_rp",

            y_axis_mode="gaia_absolute_magnitude",

            hide_flagged=False,

            hide_saturated=False,

            require_parallax=True,

            apparent_magnitude_min=-5.0,

            apparent_magnitude_max=30.0,

            show_age_guide=False,

            age_guide_gyr=12.0,

            point_color_saturation=1.0,

            point_opacity=0.8,

            selection_circle_color="#44aaee",

            selection_circle_opacity=0.45,

            selection_circle_size_factor=1.8,

            marker_size_mode="scaled",

            fixed_marker_size=8.0,

            working_table=table,

            show_class_guides=False,

        )



        self.assertEqual(self.widget._selection_circle_color, "#44aaee")

        self.assertAlmostEqual(self.widget._selection_circle_opacity, 0.45)

        self.assertAlmostEqual(self.widget._selection_circle_size_factor, 1.8)



    def test_scatter_click_accepts_event_and_blocks_background_activation(self) -> None:

        row = HrMeasurementRow(

            source_id="1",

            source_name="Alpha",

            catalog="gaia-dr3",

            ra_deg=0.0,

            dec_deg=0.0,

            gaia_g_mag=12.0,

            gaia_bp_rp=0.8,

            parallax_mas=10.0,

            parallax_error_mas=0.2,

            x=10.0,

            y=10.0,

            aperture_radius=5.0,

            annulus_inner_radius=8.0,

            annulus_outer_radius=12.0,

            gaia_absolute_magnitude=5.0,

            absolute_magnitude_proxy=5.0,

            plot_color_index=0.8,

        )

        table = HrWorkingTable(

            rows=[row],

            plane_labels=["red", "green", "blue", "luminance"],

            measured_count=1,

            usable_count=1,

        )

        self.widget.plot_working_table(table)

        activated_rows: list[HrMeasurementRow] = []

        background_hits: list[str] = []

        self.widget.pointActivated.connect(lambda activated_row: activated_rows.append(activated_row))

        self.widget.backgroundActivated.connect(lambda: background_hits.append("background"))



        class _FakePoint:

            def data(self) -> int:

                return 0



        class _FakeClickEvent:

            def __init__(self, scene_pos: object) -> None:

                self._accepted = False

                self._scene_pos = scene_pos



            def accept(self) -> None:

                self._accepted = True



            def isAccepted(self) -> bool:

                return self._accepted



            def button(self) -> Qt.MouseButton:

                return Qt.MouseButton.LeftButton



            def scenePos(self) -> object:

                return self._scene_pos



        scene_pos = self.widget._plot_item.getViewBox().sceneBoundingRect().center()

        event = _FakeClickEvent(scene_pos)



        self.widget._handle_scatter_clicked(object(), [_FakePoint()], event)

        self.widget._handle_scene_mouse_clicked(event)



        self.assertEqual(activated_rows, [row])

        self.assertEqual(background_hits, [])



    def test_point_popup_is_scene_overlay_anchored_to_plotted_row_and_persists_until_hidden(self) -> None:

        row = HrMeasurementRow(

            source_id="1",

            source_name="Alpha",

            catalog="gaia-dr3",

            ra_deg=0.0,

            dec_deg=0.0,

            gaia_g_mag=12.0,

            gaia_bp_rp=0.8,

            parallax_mas=10.0,

            parallax_error_mas=0.2,

            x=10.0,

            y=10.0,

            aperture_radius=5.0,

            annulus_inner_radius=8.0,

            annulus_outer_radius=12.0,

            gaia_absolute_magnitude=5.0,

            absolute_magnitude_proxy=5.0,

            plot_color_index=0.8,

        )

        table = HrWorkingTable(

            rows=[row],

            plane_labels=["red", "green", "blue", "luminance"],

            measured_count=1,

            usable_count=1,

        )

        self.widget.resize(640, 480)

        self.widget.show()

        self._app.processEvents()

        self.widget.apply_view(

            x_axis_mode="plot_color_index",

            y_axis_mode="absolute_magnitude_proxy",

            hide_flagged=False,

            hide_saturated=False,

            require_parallax=False,

            apparent_magnitude_min=-5.0,

            apparent_magnitude_max=30.0,

            show_age_guide=False,

            age_guide_gyr=12.0,

            point_color_saturation=1.0,

            point_opacity=0.8,

            selection_circle_color="#ffd166",

            selection_circle_opacity=0.85,

            selection_circle_size_factor=1.35,

            marker_size_mode="scaled",

            fixed_marker_size=8.0,

            working_table=table,

            show_class_guides=False,

        )

        self.widget.show_point_popup(row, "Alpha\nTemperature: 5400 K")

        popup_item = self.widget._point_popup_item

        self.assertIsNotNone(popup_item)

        self.assertEqual(self.widget.point_popup_text(), "Alpha\nTemperature: 5400 K")

        self.assertAlmostEqual(popup_item.pos().x(), 0.8)

        self.assertAlmostEqual(popup_item.pos().y(), 5.0)



        self.widget.set_selected_row(row)

        self._app.processEvents()

        refreshed_popup_item = self.widget._point_popup_item

        self.assertIsNotNone(refreshed_popup_item)

        self.assertEqual(self.widget.point_popup_text(), "Alpha\nTemperature: 5400 K")

        self.assertAlmostEqual(refreshed_popup_item.pos().x(), 0.8)

        self.assertAlmostEqual(refreshed_popup_item.pos().y(), 5.0)



        self.widget.hide_point_popup()

        self.assertIsNone(self.widget._point_popup_item)

        self.assertIsNone(self.widget.point_popup_text())



    def test_apply_view_uses_x_log_scale_only_for_positive_x_values(self) -> None:

        row = HrMeasurementRow(

            source_id="1",

            source_name="Positive X Star",

            catalog="gaia-dr3",

            ra_deg=0.0,

            dec_deg=0.0,

            gaia_g_mag=12.0,

            gaia_bp_rp=0.8,

            parallax_mas=10.0,

            parallax_error_mas=0.2,

            x=10.0,

            y=10.0,

            aperture_radius=5.0,

            annulus_inner_radius=8.0,

            annulus_outer_radius=12.0,

            gaia_absolute_magnitude=5.0,

            absolute_magnitude_proxy=5.0,

            plot_color_index=0.8,

        )

        table = HrWorkingTable(

            rows=[row],

            plane_labels=["red", "green", "blue", "luminance"],

            measured_count=1,

            usable_count=1,

        )



        self.widget.apply_view(

            x_axis_mode="plot_color_index",

            y_axis_mode="absolute_magnitude_proxy",

            hide_flagged=False,

            hide_saturated=False,

            require_parallax=False,

            apparent_magnitude_min=-5.0,

            apparent_magnitude_max=30.0,

            show_age_guide=False,

            age_guide_gyr=12.0,

            point_color_saturation=1.0,

            point_opacity=0.8,

            x_log_scale=True,

            selection_circle_color="#ffd166",

            selection_circle_opacity=0.85,

            selection_circle_size_factor=1.35,

            marker_size_mode="scaled",

            fixed_marker_size=8.0,

            working_table=table,

            show_class_guides=False,

        )



        self.assertTrue(self.widget._should_use_x_log_scale())



    def test_gaia_bp_rp_log_scale_uses_temperature_coordinates_in_default_hr_view(self) -> None:

        row = HrMeasurementRow(

            source_id="1",

            source_name="Default HR Star",

            catalog="gaia-dr3",

            ra_deg=0.0,

            dec_deg=0.0,

            gaia_g_mag=12.0,

            gaia_bp_rp=0.8,

            parallax_mas=10.0,

            parallax_error_mas=0.2,

            x=10.0,

            y=10.0,

            aperture_radius=5.0,

            annulus_inner_radius=8.0,

            annulus_outer_radius=12.0,

            gaia_absolute_magnitude=5.0,

            absolute_magnitude_proxy=5.0,

            plot_color_index=0.8,

        )

        table = HrWorkingTable(

            rows=[row],

            plane_labels=["red", "green", "blue", "luminance"],

            measured_count=1,

            usable_count=1,

        )



        self.widget.apply_view(

            x_axis_mode="gaia_bp_rp",

            y_axis_mode="gaia_absolute_magnitude",

            hide_flagged=False,

            hide_saturated=False,

            require_parallax=True,

            apparent_magnitude_min=-5.0,

            apparent_magnitude_max=30.0,

            show_age_guide=False,

            age_guide_gyr=12.0,

            point_color_saturation=1.0,

            point_opacity=0.8,

            x_log_scale=True,

            selection_circle_color="#ffd166",

            selection_circle_opacity=0.85,

            selection_circle_size_factor=1.35,

            marker_size_mode="scaled",

            fixed_marker_size=8.0,

            working_table=table,

            show_class_guides=False,

        )



        self.assertTrue(self.widget._should_use_plot_temperature_log_x_coordinates())

        expected_temperature = float(self.widget.color_index_to_temperature_kelvin(0.8))

        self.assertAlmostEqual(self.widget._export_x_values_cache[0], expected_temperature)

        scatter_items = [item for item in self.widget._plot_item.items if type(item).__name__ == "ScatterPlotItem"]

        plotted_x = float(scatter_items[0].points()[0].pos().x())

        self.assertAlmostEqual(plotted_x, float(np.log10(expected_temperature)), places=6)



    def test_visible_temperature_tick_values_are_unique_and_ordered(self) -> None:

        table = HrWorkingTable(

            rows=[

                HrMeasurementRow(

                    source_id="1",

                    source_name="Blue Star",

                    catalog="gaia-dr3",

                    ra_deg=0.0,

                    dec_deg=0.0,

                    gaia_g_mag=12.0,

                    gaia_bp_rp=-0.2,

                    parallax_mas=10.0,

                    parallax_error_mas=0.2,

                    x=10.0,

                    y=10.0,

                    aperture_radius=5.0,

                    annulus_inner_radius=8.0,

                    annulus_outer_radius=12.0,

                    gaia_absolute_magnitude=2.0,

                    absolute_magnitude_proxy=2.0,

                    plot_color_index=-0.2,

                ),

                HrMeasurementRow(

                    source_id="2",

                    source_name="Red Star",

                    catalog="gaia-dr3",

                    ra_deg=1.0,

                    dec_deg=1.0,

                    gaia_g_mag=13.0,

                    gaia_bp_rp=2.8,

                    parallax_mas=8.0,

                    parallax_error_mas=0.2,

                    x=20.0,

                    y=20.0,

                    aperture_radius=5.0,

                    annulus_inner_radius=8.0,

                    annulus_outer_radius=12.0,

                    gaia_absolute_magnitude=8.0,

                    absolute_magnitude_proxy=8.0,

                    plot_color_index=2.8,

                ),

            ],

            plane_labels=["red", "green", "blue", "luminance"],

            measured_count=2,

            usable_count=2,

        )



        self.widget.plot_working_table(table)

        tick_values = self.widget._visible_temperature_tick_values()



        self.assertEqual(tick_values, sorted(set(tick_values), reverse=True))

        self.assertEqual(tick_values[0], 30000.0)

        self.assertEqual(tick_values[-1], 2600.0)



    def test_common_motion_highlight_uses_selection_circle_settings(self) -> None:

        row = HrMeasurementRow(

            source_id="1",

            source_name="Cluster Star",

            catalog="gaia-dr3",

            ra_deg=0.0,

            dec_deg=0.0,

            gaia_g_mag=12.0,

            gaia_bp_rp=0.8,

            parallax_mas=10.0,

            parallax_error_mas=0.2,

            x=10.0,

            y=10.0,

            aperture_radius=5.0,

            annulus_inner_radius=8.0,

            annulus_outer_radius=12.0,

            gaia_absolute_magnitude=5.0,

            absolute_magnitude_proxy=5.0,

            plot_color_index=0.8,

        )

        table = HrWorkingTable(

            rows=[row],

            plane_labels=["red", "green", "blue", "luminance"],

            measured_count=1,

            usable_count=1,

        )



        self.widget.apply_view(

            x_axis_mode="gaia_bp_rp",

            y_axis_mode="gaia_absolute_magnitude",

            hide_flagged=False,

            hide_saturated=False,

            require_parallax=True,

            apparent_magnitude_min=-5.0,

            apparent_magnitude_max=30.0,

            show_age_guide=False,

            age_guide_gyr=12.0,

            point_color_saturation=1.0,

            point_opacity=0.8,

            selection_circle_color="#44aaee",

            selection_circle_opacity=0.45,

            selection_circle_size_factor=1.8,

            marker_size_mode="scaled",

            fixed_marker_size=8.0,

            working_table=table,

            highlighted_row_keys={("gaia-dr3", "1")},

            show_class_guides=False,

        )



        scatter_items = [item for item in self.widget._plot_item.items if type(item).__name__ == "ScatterPlotItem"]

        self.assertEqual(len(scatter_items), 2)

        highlight_item = scatter_items[-1]

        pen = highlight_item.opts["pen"]

        brush = highlight_item.opts["brush"]

        highlight_spot = highlight_item.points()[0]



        self.assertEqual(pen.color().name(), "#44aaee")

        self.assertAlmostEqual(pen.color().alphaF(), 0.45, places=2)

        self.assertAlmostEqual(brush.color().alphaF(), 0.0, places=2)

        self.assertGreater(float(highlight_spot.size()), 9.0)

    def test_highlight_overlays_ignore_mouse_clicks(self) -> None:

        alpha = HrMeasurementRow(

            source_id="1",

            source_name="Alpha",

            catalog="gaia-dr3",

            ra_deg=0.0,

            dec_deg=0.0,

            gaia_g_mag=12.0,

            gaia_bp_rp=0.8,

            parallax_mas=10.0,

            parallax_error_mas=0.2,

            x=10.0,

            y=10.0,

            aperture_radius=5.0,

            annulus_inner_radius=8.0,

            annulus_outer_radius=12.0,

            gaia_absolute_magnitude=5.0,

            absolute_magnitude_proxy=5.0,

            plot_color_index=0.8,

        )

        beta = HrMeasurementRow(

            source_id="2",

            source_name="Beta",

            catalog="gaia-dr3",

            ra_deg=1.0,

            dec_deg=1.0,

            gaia_g_mag=13.0,

            gaia_bp_rp=1.1,

            parallax_mas=9.0,

            parallax_error_mas=0.2,

            x=20.0,

            y=20.0,

            aperture_radius=5.0,

            annulus_inner_radius=8.0,

            annulus_outer_radius=12.0,

            gaia_absolute_magnitude=6.0,

            absolute_magnitude_proxy=6.0,

            plot_color_index=1.1,

        )

        table = HrWorkingTable(

            rows=[alpha, beta],

            plane_labels=["red", "green", "blue", "luminance"],

            measured_count=2,

            usable_count=2,

        )

        self.widget.set_selected_row(alpha)

        self.widget.apply_view(

            x_axis_mode="gaia_bp_rp",

            y_axis_mode="gaia_absolute_magnitude",

            hide_flagged=False,

            hide_saturated=False,

            require_parallax=True,

            apparent_magnitude_min=-5.0,

            apparent_magnitude_max=30.0,

            show_age_guide=False,

            age_guide_gyr=12.0,

            point_color_saturation=1.0,

            point_opacity=0.8,

            selection_circle_color="#44aaee",

            selection_circle_opacity=0.45,

            selection_circle_size_factor=1.8,

            marker_size_mode="scaled",

            fixed_marker_size=8.0,

            working_table=table,

            highlighted_row_keys={("gaia-dr3", "1"), ("gaia-dr3", "2")},

            show_class_guides=False,

        )

        scatter_items = [item for item in self.widget._plot_item.items if type(item).__name__ == "ScatterPlotItem"]

        self.assertEqual(len(scatter_items), 3)

        self.assertNotEqual(scatter_items[0].acceptedMouseButtons(), Qt.MouseButton.NoButton)

        self.assertEqual(scatter_items[1].acceptedMouseButtons(), Qt.MouseButton.NoButton)

        self.assertEqual(scatter_items[2].acceptedMouseButtons(), Qt.MouseButton.NoButton)



    def test_plot_axes_can_switch_to_calibrated_magnitude(self) -> None:

        table = HrWorkingTable(

            rows=[

                HrMeasurementRow(

                    source_id="1",

                    source_name="1",

                    catalog="gaia-dr3",

                    ra_deg=0.0,

                    dec_deg=0.0,

                    gaia_g_mag=12.0,

                    gaia_bp_rp=0.8,

                    parallax_mas=None,

                    parallax_error_mas=None,

                    x=10.0,

                    y=10.0,

                    aperture_radius=5.0,

                    annulus_inner_radius=8.0,

                    annulus_outer_radius=12.0,

                    calibrated_mag_luminance=11.5,

                    instrumental_blue_minus_red=0.3,

                    plot_color_index=0.3,

                )

            ],

            plane_labels=["red", "green", "blue", "luminance"],

            measured_count=1,

            usable_count=1,

        )



        self.widget.set_axes("instrumental_blue_minus_red", "calibrated_mag_luminance")

        self.widget.set_filters(hide_flagged=False, hide_saturated=False, require_parallax=False)

        self.widget.plot_working_table(table)



        self.assertIn("Showing 1 of 1", self.widget._status_label.text())



    def test_plot_axes_can_switch_to_gaia_absolute_magnitude(self) -> None:

        table = HrWorkingTable(

            rows=[

                HrMeasurementRow(

                    source_id="1",

                    source_name="1",

                    catalog="gaia-dr3",

                    ra_deg=0.0,

                    dec_deg=0.0,

                    gaia_g_mag=12.0,

                    gaia_bp_rp=0.8,

                    parallax_mas=10.0,

                    parallax_error_mas=0.2,

                    x=10.0,

                    y=10.0,

                    aperture_radius=5.0,

                    annulus_inner_radius=8.0,

                    annulus_outer_radius=12.0,

                    gaia_absolute_magnitude=7.0,

                    plot_color_index=0.8,

                )

            ],

            plane_labels=["red", "green", "blue", "luminance"],

            measured_count=1,

            usable_count=1,

        )



        self.widget.set_axes("gaia_bp_rp", "gaia_absolute_magnitude")

        self.widget.set_filters(hide_flagged=False, hide_saturated=False, require_parallax=True)

        self.widget.plot_working_table(table)



        self.assertIn("Showing 1 of 1", self.widget._status_label.text())



    def test_gaia_absolute_magnitude_axis_respects_require_parallax(self) -> None:

        table = HrWorkingTable(

            rows=[

                HrMeasurementRow(

                    source_id="1",

                    source_name="WithParallax",

                    catalog="gaia-dr3",

                    ra_deg=0.0,

                    dec_deg=0.0,

                    gaia_g_mag=12.0,

                    gaia_bp_rp=0.8,

                    parallax_mas=10.0,

                    parallax_error_mas=0.2,

                    x=10.0,

                    y=10.0,

                    aperture_radius=5.0,

                    annulus_inner_radius=8.0,

                    annulus_outer_radius=12.0,

                    gaia_absolute_magnitude=7.0,

                    plot_color_index=0.8,

                ),

                HrMeasurementRow(

                    source_id="2",

                    source_name="NoParallax",

                    catalog="gaia-dr3",

                    ra_deg=0.0,

                    dec_deg=0.0,

                    gaia_g_mag=13.0,

                    gaia_bp_rp=1.1,

                    parallax_mas=None,

                    parallax_error_mas=None,

                    x=11.0,

                    y=11.0,

                    aperture_radius=5.0,

                    annulus_inner_radius=8.0,

                    annulus_outer_radius=12.0,

                    gaia_absolute_magnitude=None,

                    plot_color_index=1.1,

                ),

            ],

            plane_labels=["red", "green", "blue", "luminance"],

            measured_count=2,

            usable_count=2,

        )



        self.widget.set_axes("gaia_bp_rp", "gaia_absolute_magnitude")

        self.widget.set_filters(hide_flagged=False, hide_saturated=False, require_parallax=True)

        self.widget.plot_working_table(table)



        self.assertEqual([row.source_name for row in self.widget.visible_rows()], ["WithParallax"])



    def test_visible_rows_follow_current_axes_and_filters(self) -> None:

        table = HrWorkingTable(

            rows=[

                HrMeasurementRow(

                    source_id="1",

                    source_name="Alpha",

                    catalog="gaia-dr3",

                    ra_deg=0.0,

                    dec_deg=0.0,

                    gaia_g_mag=12.0,

                    gaia_bp_rp=0.8,

                    parallax_mas=10.0,

                    parallax_error_mas=0.2,

                    x=10.0,

                    y=10.0,

                    aperture_radius=5.0,

                    annulus_inner_radius=8.0,

                    annulus_outer_radius=12.0,

                    gaia_absolute_magnitude=5.0,

                    absolute_magnitude_proxy=5.0,

                    plot_color_index=0.8,

                ),

                HrMeasurementRow(

                    source_id="2",

                    source_name="Beta",

                    catalog="gaia-dr3",

                    ra_deg=0.0,

                    dec_deg=0.0,

                    gaia_g_mag=13.0,

                    gaia_bp_rp=1.1,

                    parallax_mas=10.0,

                    parallax_error_mas=0.2,

                    x=11.0,

                    y=11.0,

                    aperture_radius=5.0,

                    annulus_inner_radius=8.0,

                    annulus_outer_radius=12.0,

                    gaia_absolute_magnitude=6.0,

                    absolute_magnitude_proxy=6.0,

                    plot_color_index=1.1,

                    flags=["flagged"],

                ),

            ],

            plane_labels=["red", "green", "blue", "luminance"],

            measured_count=2,

            usable_count=2,

        )



        self.widget.set_filters(hide_flagged=True, hide_saturated=False, require_parallax=True)

        self.widget.plot_working_table(table)



        self.assertEqual([row.source_name for row in self.widget.visible_rows()], ["Alpha"])



    def test_apply_view_can_keep_table_rows_broader_than_plot_row_mask(self) -> None:

        table = HrWorkingTable(

            rows=[

                HrMeasurementRow(

                    source_id="1",

                    source_name="Alpha",

                    catalog="gaia-dr3",

                    ra_deg=0.0,

                    dec_deg=0.0,

                    gaia_g_mag=12.0,

                    gaia_bp_rp=0.8,

                    parallax_mas=10.0,

                    parallax_error_mas=0.2,

                    x=10.0,

                    y=10.0,

                    aperture_radius=5.0,

                    annulus_inner_radius=8.0,

                    annulus_outer_radius=12.0,

                    gaia_absolute_magnitude=5.0,

                    absolute_magnitude_proxy=5.0,

                    plot_color_index=0.8,

                ),

                HrMeasurementRow(

                    source_id="2",

                    source_name="Beta",

                    catalog="gaia-dr3",

                    ra_deg=0.0,

                    dec_deg=0.0,

                    gaia_g_mag=13.0,

                    gaia_bp_rp=1.1,

                    parallax_mas=9.0,

                    parallax_error_mas=0.2,

                    x=11.0,

                    y=11.0,

                    aperture_radius=5.0,

                    annulus_inner_radius=8.0,

                    annulus_outer_radius=12.0,

                    gaia_absolute_magnitude=6.0,

                    absolute_magnitude_proxy=6.0,

                    plot_color_index=1.1,

                ),

            ],

            plane_labels=["red", "green", "blue", "luminance"],

            measured_count=2,

            usable_count=2,

        )



        self.widget.apply_view(

            x_axis_mode="gaia_bp_rp",

            y_axis_mode="absolute_magnitude_proxy",

            hide_flagged=False,

            hide_saturated=False,

            require_parallax=True,

            apparent_magnitude_min=-5.0,

            apparent_magnitude_max=30.0,

            show_age_guide=False,

            age_guide_gyr=12.0,

            point_color_saturation=1.0,

            point_opacity=0.8,

            marker_size_mode="scaled",

            fixed_marker_size=8.0,

            working_table=table,

            row_mask=np.asarray([True, False], dtype=bool),

        )



        self.assertEqual([row.source_name for row in self.widget.visible_rows()], ["Alpha", "Beta"])

        self.assertEqual(self.widget.visible_row_count(), 2)

        self.assertEqual(self.widget.plotted_row_count(), 1)

        self.assertEqual(self.widget.plotted_row_keys(), {("gaia-dr3", "1")})



    def test_display_sample_stays_stable_when_non_displayed_row_is_removed(self) -> None:

        rows = [

            HrMeasurementRow(

                source_id=str(index),

                source_name=f"Star {index}",

                catalog="gaia-dr3",

                ra_deg=0.0,

                dec_deg=0.0,

                gaia_g_mag=10.0 + (index % 20) * 0.1,

                gaia_bp_rp=0.4 + (index % 30) * 0.03,

                parallax_mas=10.0,

                parallax_error_mas=0.2,

                x=float(index),

                y=float(index),

                aperture_radius=5.0,

                annulus_inner_radius=8.0,

                annulus_outer_radius=12.0,

                gaia_absolute_magnitude=5.0 + (index % 15) * 0.1,

                absolute_magnitude_proxy=5.0 + (index % 15) * 0.1,

                plot_color_index=0.4 + (index % 30) * 0.03,

            )

            for index in range(2105)

        ]

        table = HrWorkingTable(

            rows=rows,

            plane_labels=["red", "green", "blue", "luminance"],

            measured_count=len(rows),

            usable_count=len(rows),

        )



        self.widget.apply_view(

            x_axis_mode="gaia_bp_rp",

            y_axis_mode="absolute_magnitude_proxy",

            hide_flagged=False,

            hide_saturated=False,

            require_parallax=True,

            apparent_magnitude_min=-5.0,

            apparent_magnitude_max=30.0,

            show_age_guide=False,

            age_guide_gyr=12.0,

            point_color_saturation=1.0,

            point_opacity=0.8,

            marker_size_mode="scaled",

            fixed_marker_size=8.0,

            working_table=table,

        )



        original_displayed_ids = {row.source_id for row in self.widget._plotted_rows_cache}

        removed_row = next(row for row in rows if row.source_id not in original_displayed_ids)

        reduced_table = HrWorkingTable(

            rows=[row for row in rows if row.source_id != removed_row.source_id],

            plane_labels=["red", "green", "blue", "luminance"],

            measured_count=len(rows) - 1,

            usable_count=len(rows) - 1,

        )



        self.widget.apply_view(

            x_axis_mode="gaia_bp_rp",

            y_axis_mode="absolute_magnitude_proxy",

            hide_flagged=False,

            hide_saturated=False,

            require_parallax=True,

            apparent_magnitude_min=-5.0,

            apparent_magnitude_max=30.0,

            show_age_guide=False,

            age_guide_gyr=12.0,

            point_color_saturation=1.0,

            point_opacity=0.8,

            marker_size_mode="scaled",

            fixed_marker_size=8.0,

            working_table=reduced_table,

        )



        self.assertEqual(original_displayed_ids, {row.source_id for row in self.widget._plotted_rows_cache})



    def test_brighter_stars_use_larger_marker_sizes(self) -> None:

        table = HrWorkingTable(

            rows=[

                HrMeasurementRow(

                    source_id="1",

                    source_name="Bright",

                    catalog="gaia-dr3",

                    ra_deg=0.0,

                    dec_deg=0.0,

                    gaia_g_mag=10.0,

                    gaia_bp_rp=0.8,

                    parallax_mas=10.0,

                    parallax_error_mas=0.2,

                    x=10.0,

                    y=10.0,

                    aperture_radius=5.0,

                    annulus_inner_radius=8.0,

                    annulus_outer_radius=12.0,

                    gaia_absolute_magnitude=5.0,

                    absolute_magnitude_proxy=5.0,

                    plot_color_index=0.8,

                ),

                HrMeasurementRow(

                    source_id="2",

                    source_name="Faint",

                    catalog="gaia-dr3",

                    ra_deg=0.0,

                    dec_deg=0.0,

                    gaia_g_mag=15.0,

                    gaia_bp_rp=1.1,

                    parallax_mas=9.0,

                    parallax_error_mas=0.2,

                    x=11.0,

                    y=11.0,

                    aperture_radius=5.0,

                    annulus_inner_radius=8.0,

                    annulus_outer_radius=12.0,

                    gaia_absolute_magnitude=6.0,

                    absolute_magnitude_proxy=6.0,

                    plot_color_index=1.1,

                ),

            ],

            plane_labels=["red", "green", "blue", "luminance"],

            measured_count=2,

            usable_count=2,

        )



        self.widget.plot_working_table(table)



        self.assertEqual(len(self.widget._plotted_point_sizes_cache), 2)

        self.assertGreater(self.widget._plotted_point_sizes_cache[0], self.widget._plotted_point_sizes_cache[1])



    def test_fixed_marker_size_mode_uses_same_size_for_all_points(self) -> None:

        self.widget._marker_size_mode = "fixed"

        self.widget._fixed_marker_size = 9.5



        sizes = self.widget._marker_sizes_for_rows(

            [

                HrMeasurementRow(

                    source_id="1",

                    source_name="Bright",

                    catalog="gaia-dr3",

                    ra_deg=0.0,

                    dec_deg=0.0,

                    gaia_g_mag=10.0,

                    gaia_bp_rp=0.8,

                    parallax_mas=10.0,

                    parallax_error_mas=0.2,

                    x=10.0,

                    y=10.0,

                    aperture_radius=5.0,

                    annulus_inner_radius=8.0,

                    annulus_outer_radius=12.0,

                ),

                HrMeasurementRow(

                    source_id="2",

                    source_name="Faint",

                    catalog="gaia-dr3",

                    ra_deg=0.0,

                    dec_deg=0.0,

                    gaia_g_mag=15.0,

                    gaia_bp_rp=1.1,

                    parallax_mas=9.0,

                    parallax_error_mas=0.2,

                    x=11.0,

                    y=11.0,

                    aperture_radius=5.0,

                    annulus_inner_radius=8.0,

                    annulus_outer_radius=12.0,

                ),

            ]

        )



        self.assertEqual(sizes, [9.5, 9.5])



    def test_plot_color_helper_applies_saturation_and_opacity(self) -> None:

        self.widget._point_color_saturation = 0.0

        self.widget._point_opacity = 0.35



        color = self.widget._plot_color("#ff0000", opacity=self.widget._point_opacity)

        _hue, saturation, _value, alpha = color.getHsvF()



        self.assertAlmostEqual(saturation, 0.0, places=3)

        self.assertAlmostEqual(alpha, 0.35, places=3)



    def test_apparent_magnitude_filter_limits_visible_rows(self) -> None:

        table = HrWorkingTable(

            rows=[

                HrMeasurementRow(

                    source_id="1",

                    source_name="Too Bright",

                    catalog="gaia-dr3",

                    ra_deg=0.0,

                    dec_deg=0.0,

                    gaia_g_mag=10.0,

                    gaia_bp_rp=0.8,

                    parallax_mas=10.0,

                    parallax_error_mas=0.2,

                    x=10.0,

                    y=10.0,

                    aperture_radius=5.0,

                    annulus_inner_radius=8.0,

                    annulus_outer_radius=12.0,

                    gaia_absolute_magnitude=5.0,

                    absolute_magnitude_proxy=5.0,

                    plot_color_index=0.8,

                ),

                HrMeasurementRow(

                    source_id="2",

                    source_name="In Range",

                    catalog="gaia-dr3",

                    ra_deg=0.0,

                    dec_deg=0.0,

                    gaia_g_mag=12.4,

                    gaia_bp_rp=1.0,

                    parallax_mas=9.0,

                    parallax_error_mas=0.2,

                    x=11.0,

                    y=11.0,

                    aperture_radius=5.0,

                    annulus_inner_radius=8.0,

                    annulus_outer_radius=12.0,

                    gaia_absolute_magnitude=6.0,

                    absolute_magnitude_proxy=6.0,

                    plot_color_index=1.0,

                ),

                HrMeasurementRow(

                    source_id="3",

                    source_name="Too Faint",

                    catalog="gaia-dr3",

                    ra_deg=0.0,

                    dec_deg=0.0,

                    gaia_g_mag=15.0,

                    gaia_bp_rp=1.2,

                    parallax_mas=8.0,

                    parallax_error_mas=0.2,

                    x=12.0,

                    y=12.0,

                    aperture_radius=5.0,

                    annulus_inner_radius=8.0,

                    annulus_outer_radius=12.0,

                    gaia_absolute_magnitude=7.0,

                    absolute_magnitude_proxy=7.0,

                    plot_color_index=1.2,

                ),

            ],

            plane_labels=["red", "green", "blue", "luminance"],

            measured_count=3,

            usable_count=3,

        )



        self.widget.set_apparent_magnitude_filter(11.0, 13.0)

        self.widget.plot_working_table(table)



        self.assertEqual([row.source_name for row in self.widget.visible_rows()], ["In Range"])



    def test_age_guide_overlay_is_cached_for_supported_axes(self) -> None:

        table = HrWorkingTable(

            rows=[

                HrMeasurementRow(

                    source_id="1",

                    source_name="Cluster Star",

                    catalog="gaia-dr3",

                    ra_deg=0.0,

                    dec_deg=0.0,

                    gaia_g_mag=12.0,

                    gaia_bp_rp=0.8,

                    parallax_mas=10.0,

                    parallax_error_mas=0.2,

                    x=10.0,

                    y=10.0,

                    aperture_radius=5.0,

                    annulus_inner_radius=8.0,

                    annulus_outer_radius=12.0,

                    absolute_magnitude_proxy=4.5,

                    gaia_absolute_magnitude=4.4,

                    plot_color_index=0.8,

                )

            ],

            plane_labels=["red", "green", "blue", "luminance"],

            measured_count=1,

            usable_count=1,

        )



        self.widget.apply_view(

            x_axis_mode="gaia_bp_rp",

            y_axis_mode="gaia_absolute_magnitude",

            hide_flagged=False,

            hide_saturated=False,

            require_parallax=True,

            apparent_magnitude_min=-5.0,

            apparent_magnitude_max=30.0,

            show_age_guide=True,

            age_guide_gyr=12.0,

            point_color_saturation=1.0,

            point_opacity=0.8,

            marker_size_mode="scaled",

            fixed_marker_size=8.0,

            working_table=table,

        )



        self.assertIsNotNone(self.widget._age_guide_curve_cache)

        self.assertIn("Age guide: 12.0 Gyr", self.widget._status_label.text())



    def test_class_guides_overlay_is_cached_for_supported_axes(self) -> None:

        table = HrWorkingTable(

            rows=[

                HrMeasurementRow(

                    source_id="1",

                    source_name="Guide Star",

                    catalog="gaia-dr3",

                    ra_deg=0.0,

                    dec_deg=0.0,

                    gaia_g_mag=12.0,

                    gaia_bp_rp=0.8,

                    parallax_mas=10.0,

                    parallax_error_mas=0.2,

                    x=10.0,

                    y=10.0,

                    aperture_radius=5.0,

                    annulus_inner_radius=8.0,

                    annulus_outer_radius=12.0,

                    absolute_magnitude_proxy=4.5,

                    gaia_absolute_magnitude=4.4,

                    plot_color_index=0.8,

                )

            ],

            plane_labels=["red", "green", "blue", "luminance"],

            measured_count=1,

            usable_count=1,

        )



        self.widget.apply_view(

            x_axis_mode="gaia_bp_rp",

            y_axis_mode="gaia_absolute_magnitude",

            hide_flagged=False,

            hide_saturated=False,

            require_parallax=True,

            apparent_magnitude_min=-5.0,

            apparent_magnitude_max=30.0,

            show_age_guide=False,

            age_guide_gyr=12.0,

            point_color_saturation=1.0,

            point_opacity=0.8,

            marker_size_mode="scaled",

            fixed_marker_size=8.0,

            working_table=table,

        )



        self.assertEqual(

            self.widget._class_guide_names_cache,

            ("Supergiants", "Giants", "Subgiants", "Main Sequence", "White Dwarfs"),

        )



    def test_class_guides_overlay_is_suppressed_when_disabled(self) -> None:

        table = HrWorkingTable(

            rows=[

                HrMeasurementRow(

                    source_id="1",

                    source_name="Guide Star",

                    catalog="gaia-dr3",

                    ra_deg=0.0,

                    dec_deg=0.0,

                    gaia_g_mag=12.0,

                    gaia_bp_rp=0.8,

                    parallax_mas=10.0,

                    parallax_error_mas=0.2,

                    x=10.0,

                    y=10.0,

                    aperture_radius=5.0,

                    annulus_inner_radius=8.0,

                    annulus_outer_radius=12.0,

                    absolute_magnitude_proxy=4.5,

                    gaia_absolute_magnitude=4.4,

                    plot_color_index=0.8,

                )

            ],

            plane_labels=["red", "green", "blue", "luminance"],

            measured_count=1,

            usable_count=1,

        )



        self.widget.apply_view(

            x_axis_mode="gaia_bp_rp",

            y_axis_mode="gaia_absolute_magnitude",

            hide_flagged=False,

            hide_saturated=False,

            require_parallax=True,

            apparent_magnitude_min=-5.0,

            apparent_magnitude_max=30.0,

            show_age_guide=False,

            age_guide_gyr=12.0,

            point_color_saturation=1.0,

            point_opacity=0.8,

            marker_size_mode="scaled",

            fixed_marker_size=8.0,

            working_table=table,

            show_class_guides=False,

        )



        self.assertEqual(self.widget._class_guide_names_cache, ())



    def test_age_guide_overlay_is_suppressed_for_unsupported_axes(self) -> None:

        table = HrWorkingTable(

            rows=[

                HrMeasurementRow(

                    source_id="1",

                    source_name="Cluster Star",

                    catalog="gaia-dr3",

                    ra_deg=0.0,

                    dec_deg=0.0,

                    gaia_g_mag=12.0,

                    gaia_bp_rp=0.8,

                    parallax_mas=10.0,

                    parallax_error_mas=0.2,

                    x=10.0,

                    y=10.0,

                    aperture_radius=5.0,

                    annulus_inner_radius=8.0,

                    annulus_outer_radius=12.0,

                    calibrated_mag_luminance=11.2,

                    instrumental_blue_minus_red=0.3,

                    plot_color_index=0.3,

                )

            ],

            plane_labels=["red", "green", "blue", "luminance"],

            measured_count=1,

            usable_count=1,

        )



        self.widget.apply_view(

            x_axis_mode="instrumental_blue_minus_red",

            y_axis_mode="calibrated_mag_luminance",

            hide_flagged=False,

            hide_saturated=False,

            require_parallax=False,

            apparent_magnitude_min=-5.0,

            apparent_magnitude_max=30.0,

            show_age_guide=True,

            age_guide_gyr=12.0,

            point_color_saturation=1.0,

            point_opacity=0.8,

            marker_size_mode="scaled",

            fixed_marker_size=8.0,

            working_table=table,

            show_class_guides=True,

        )



        self.assertIsNone(self.widget._age_guide_curve_cache)

        self.assertIn("Age guide is available", self.widget._status_label.text())

        self.assertIn("Class guides are available", self.widget._status_label.text())





if __name__ == "__main__":

    unittest.main()

