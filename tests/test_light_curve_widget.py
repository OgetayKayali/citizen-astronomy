from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtWidgets import QApplication, QLabel

from photometry_app.core.models import LightCurvePoint, LightCurveSeries
from photometry_app.core.plotting import build_light_curve_plot_payload, light_curve_y_limits
from photometry_app.ui.light_curve_widget import LightCurvePlotWidget


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class LightCurvePlotWidgetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.widget = LightCurvePlotWidget()

    def tearDown(self) -> None:
        self.widget.close()

    def test_plot_layout_does_not_reserve_space_for_instruction_label(self) -> None:
        layout = self.widget.layout()
        self.assertIsNotNone(layout)
        label_widgets = [
            layout.itemAt(index).widget()
            for index in range(layout.count())
            if isinstance(layout.itemAt(index).widget(), QLabel)
        ]
        self.assertEqual(label_widgets, [])
        self.assertFalse(self.widget._status_label.isVisible())

    def test_segment_reset_button_emits_clear_selection_signal(self) -> None:
        selections: list[object] = []
        self.widget.segmentSelected.connect(selections.append)

        self.widget._handle_segment_reset_button_clicked()

        self.assertEqual(selections, [None])

    def test_segment_reset_button_matches_auto_button_visibility_and_position(self) -> None:
        start = datetime(2025, 1, 1, 0, 0, 0)
        series = LightCurveSeries(
            object_name="Demo",
            source_id="target-1",
            source_name="Target",
            filter_name="V",
            points=[
                LightCurvePoint(
                    observation_time=start,
                    file_path=Path("frame_01.fits"),
                    differential_magnitude=12.10,
                    instrumental_magnitude=None,
                    flux=None,
                    flux_error=None,
                    differential_magnitude_error=0.05,
                ),
                LightCurvePoint(
                    observation_time=start + timedelta(minutes=15),
                    file_path=Path("frame_02.fits"),
                    differential_magnitude=12.35,
                    instrumental_magnitude=None,
                    flux=None,
                    flux_error=None,
                    differential_magnitude_error=0.04,
                ),
            ],
        )

        self.widget.plot_series(series, empty_message="No data available.")
        self.widget._plot_item.getViewBox().disableAutoRange()
        self.widget._segment_selection_active = True
        self.widget._plot_item.mouseHovering = True
        self.widget._sync_plot_corner_buttons()

        self.assertTrue(self.widget._auto_range_button.isVisible())
        self.assertTrue(self.widget._segment_reset_button_item.isVisible())
        self.assertGreater(self.widget._segment_reset_button_item.pos().x(), self.widget._auto_range_button.pos().x())
        self.assertAlmostEqual(self.widget._segment_reset_button_item.pos().y(), self.widget._auto_range_button.pos().y(), places=2)
        self.assertAlmostEqual(self.widget._segment_reset_button_item.opacity(), self.widget._auto_range_button.opacity(), places=2)

    def test_plot_series_y_axis_ignores_error_bar_overshoot(self) -> None:
        start = datetime(2025, 1, 1, 0, 0, 0)
        series = LightCurveSeries(
            object_name="Demo",
            source_id="target-1",
            source_name="Target",
            filter_name="V",
            points=[
                LightCurvePoint(
                    observation_time=start,
                    file_path=Path("frame_01.fits"),
                    differential_magnitude=12.10,
                    instrumental_magnitude=None,
                    flux=None,
                    flux_error=None,
                    differential_magnitude_error=0.05,
                ),
                LightCurvePoint(
                    observation_time=start + timedelta(minutes=15),
                    file_path=Path("frame_02.fits"),
                    differential_magnitude=12.35,
                    instrumental_magnitude=None,
                    flux=None,
                    flux_error=None,
                    differential_magnitude_error=2.5,
                ),
                LightCurvePoint(
                    observation_time=start + timedelta(minutes=30),
                    file_path=Path("frame_03.fits"),
                    differential_magnitude=12.25,
                    instrumental_magnitude=None,
                    flux=None,
                    flux_error=None,
                    differential_magnitude_error=0.04,
                ),
            ],
        )

        self.widget.plot_series(series, empty_message="No data available.")
        QApplication.processEvents()

        view_range = self.widget._plot_item.getViewBox().viewRange()
        actual_y_range = tuple(float(value) for value in view_range[1])
        expected_y_range = light_curve_y_limits([12.10, 12.35, 12.25])

        self.assertIsNotNone(expected_y_range)
        assert expected_y_range is not None
        self.assertAlmostEqual(actual_y_range[0], expected_y_range[0], places=3)
        self.assertAlmostEqual(actual_y_range[1], expected_y_range[1], places=3)

    def test_phase_error_bar_beam_width_is_fixed(self) -> None:
        start = datetime(2025, 1, 1, 0, 0, 0)
        series = LightCurveSeries(
            object_name="Demo",
            source_id="target-1",
            source_name="Target",
            filter_name="V",
            points=[
                LightCurvePoint(
                    observation_time=start,
                    file_path=Path("frame_01.fits"),
                    differential_magnitude=12.10,
                    instrumental_magnitude=None,
                    flux=None,
                    flux_error=None,
                    differential_magnitude_error=0.05,
                ),
                LightCurvePoint(
                    observation_time=start + timedelta(hours=6),
                    file_path=Path("frame_02.fits"),
                    differential_magnitude=12.35,
                    instrumental_magnitude=None,
                    flux=None,
                    flux_error=None,
                    differential_magnitude_error=0.08,
                ),
            ],
        )

        payload = build_light_curve_plot_payload(
            series,
            empty_message="No data available.",
            x_axis_mode="phase",
            phase_period_hours=24.0,
            phase_anchor_mode="primary_minimum",
        )

        self.assertEqual(self.widget._error_bar_beam_width(payload), 0.01)

    def test_phase_plot_makes_recent_points_more_opaque(self) -> None:
        start = datetime(2025, 1, 1, 0, 0, 0)
        series = LightCurveSeries(
            object_name="Demo",
            source_id="target-1",
            source_name="Target",
            filter_name="V",
            points=[
                LightCurvePoint(
                    observation_time=start,
                    file_path=Path("frame_01.fits"),
                    differential_magnitude=12.10,
                    instrumental_magnitude=None,
                    flux=None,
                    flux_error=None,
                    differential_magnitude_error=0.05,
                ),
                LightCurvePoint(
                    observation_time=start + timedelta(hours=6),
                    file_path=Path("frame_02.fits"),
                    differential_magnitude=12.35,
                    instrumental_magnitude=None,
                    flux=None,
                    flux_error=None,
                    differential_magnitude_error=0.08,
                ),
                LightCurvePoint(
                    observation_time=start + timedelta(hours=12),
                    file_path=Path("frame_03.fits"),
                    differential_magnitude=12.20,
                    instrumental_magnitude=None,
                    flux=None,
                    flux_error=None,
                    differential_magnitude_error=0.04,
                ),
            ],
        )

        self.widget.plot_series(
            series,
            empty_message="No data available.",
            x_axis_mode="phase",
            phase_period_hours=24.0,
            phase_anchor_mode="primary_minimum",
        )
        QApplication.processEvents()

        assert self.widget._scatter_item is not None
        point_items = self.widget._scatter_item.points()
        self.assertEqual(len(point_items), 3)
        self.assertLess(point_items[0].brush().color().alpha(), point_items[-1].brush().color().alpha())

    def test_phase_opacity_floor_is_configurable(self) -> None:
        start = datetime(2025, 1, 1, 0, 0, 0)
        series = LightCurveSeries(
            object_name="Demo",
            source_id="target-1",
            source_name="Target",
            filter_name="V",
            points=[
                LightCurvePoint(
                    observation_time=start,
                    file_path=Path("frame_01.fits"),
                    differential_magnitude=12.10,
                    instrumental_magnitude=None,
                    flux=None,
                    flux_error=None,
                    differential_magnitude_error=0.05,
                ),
                LightCurvePoint(
                    observation_time=start + timedelta(hours=6),
                    file_path=Path("frame_02.fits"),
                    differential_magnitude=12.35,
                    instrumental_magnitude=None,
                    flux=None,
                    flux_error=None,
                    differential_magnitude_error=0.08,
                ),
                LightCurvePoint(
                    observation_time=start + timedelta(hours=12),
                    file_path=Path("frame_03.fits"),
                    differential_magnitude=12.20,
                    instrumental_magnitude=None,
                    flux=None,
                    flux_error=None,
                    differential_magnitude_error=0.04,
                ),
            ],
        )

        self.widget.plot_series(
            series,
            empty_message="No data available.",
            x_axis_mode="phase",
            phase_period_hours=24.0,
            phase_anchor_mode="primary_minimum",
            phase_opacity_floor=0.5,
        )
        QApplication.processEvents()

        assert self.widget._scatter_item is not None
        point_items = self.widget._scatter_item.points()
        self.assertGreaterEqual(point_items[0].brush().color().alpha(), 127)
        self.assertEqual(point_items[-1].brush().color().alpha(), 255)

    def test_point_tooltip_includes_two_decimal_y_value_and_error(self) -> None:
        start = datetime(2025, 1, 1, 0, 0, 0)
        series = LightCurveSeries(
            object_name="Demo",
            source_id="target-1",
            source_name="Target",
            filter_name="V",
            points=[
                LightCurvePoint(
                    observation_time=start,
                    file_path=Path("frame_01.fits"),
                    differential_magnitude=10.4057,
                    instrumental_magnitude=None,
                    flux=None,
                    flux_error=None,
                    differential_magnitude_error=0.094,
                )
            ],
        )

        self.widget.plot_series(series, empty_message="No data available.", x_axis_mode="jd")
        QApplication.processEvents()

        assert self.widget._payload is not None
        tooltip = self.widget._format_point_tooltip(self.widget._payload.points[0])

        self.assertEqual(tooltip.splitlines()[1], "y:10.41±0.09")
        self.assertTrue(tooltip.splitlines()[0].startswith("JD:"))

    def test_fit_period_badge_shows_only_when_text_and_points_exist(self) -> None:
        self.widget.show()

        start = datetime(2025, 1, 1, 0, 0, 0)
        series = LightCurveSeries(
            object_name="Demo",
            source_id="target-1",
            source_name="Target",
            filter_name="V",
            points=[
                LightCurvePoint(
                    observation_time=start,
                    file_path=Path("frame_01.fits"),
                    differential_magnitude=10.4057,
                    instrumental_magnitude=None,
                    flux=None,
                    flux_error=None,
                    differential_magnitude_error=0.094,
                )
            ],
        )

        self.widget.set_fit_period_badge_text("Fit Period  240.00 min")
        self.widget.plot_series(series, empty_message="No data available.")
        QApplication.processEvents()

        self.assertTrue(self.widget._fit_period_badge.isVisible())
        self.assertEqual(self.widget._fit_period_badge.text(), "Fit Period  240.00 min")

        self.widget.set_fit_period_badge_text(None)
        QApplication.processEvents()

        self.assertFalse(self.widget._fit_period_badge.isVisible())

    def test_fit_period_badge_hides_for_empty_payload(self) -> None:
        self.widget.set_fit_period_badge_text("Fit Period  240.00 min")
        self.widget.show_message("Variable Star Light Curve", "No data available.")
        QApplication.processEvents()

        self.assertFalse(self.widget._fit_period_badge.isVisible())

    def test_export_current_view_writes_high_resolution_png(self) -> None:
        start = datetime(2025, 1, 1, 0, 0, 0)
        series = LightCurveSeries(
            object_name="Demo",
            source_id="target-1",
            source_name="Target",
            filter_name="V",
            points=[
                LightCurvePoint(
                    observation_time=start + timedelta(minutes=index * 15),
                    file_path=Path(f"frame_{index:02d}.fits"),
                    differential_magnitude=12.10 + (0.05 * index),
                    instrumental_magnitude=None,
                    flux=None,
                    flux_error=None,
                    differential_magnitude_error=0.05,
                )
                for index in range(4)
            ],
        )

        self.widget.plot_series(series, empty_message="No data available.")
        QApplication.processEvents()

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "light_curve_view.png"
            self.widget.export_current_view(str(output_path), scale_factor=2.0)

            self.assertTrue(output_path.exists())
            self.assertGreater(output_path.stat().st_size, 0)

    def test_export_current_view_writes_pdf_and_svg(self) -> None:
        start = datetime(2025, 1, 1, 0, 0, 0)
        series = LightCurveSeries(
            object_name="Demo",
            source_id="target-1",
            source_name="Target",
            filter_name="V",
            points=[
                LightCurvePoint(
                    observation_time=start + timedelta(minutes=index * 15),
                    file_path=Path(f"frame_{index:02d}.fits"),
                    differential_magnitude=12.10 + (0.05 * index),
                    instrumental_magnitude=None,
                    flux=None,
                    flux_error=None,
                    differential_magnitude_error=0.05,
                )
                for index in range(4)
            ],
        )

        self.widget.plot_series(series, empty_message="No data available.")
        QApplication.processEvents()

        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "light_curve_view.pdf"
            svg_path = Path(temp_dir) / "light_curve_view.svg"
            self.widget.export_current_view(str(pdf_path), scale_factor=2.0)
            self.widget.export_current_view(str(svg_path), scale_factor=2.0)

            self.assertTrue(pdf_path.exists())
            self.assertGreater(pdf_path.stat().st_size, 0)
            self.assertTrue(svg_path.exists())
            self.assertGreater(svg_path.stat().st_size, 0)

    def test_phase_error_bars_follow_point_opacity(self) -> None:
        start = datetime(2025, 1, 1, 0, 0, 0)
        series = LightCurveSeries(
            object_name="Demo",
            source_id="target-1",
            source_name="Target",
            filter_name="V",
            points=[
                LightCurvePoint(
                    observation_time=start,
                    file_path=Path("frame_01.fits"),
                    differential_magnitude=12.10,
                    instrumental_magnitude=None,
                    flux=None,
                    flux_error=None,
                    differential_magnitude_error=0.05,
                ),
                LightCurvePoint(
                    observation_time=start + timedelta(hours=6),
                    file_path=Path("frame_02.fits"),
                    differential_magnitude=12.35,
                    instrumental_magnitude=None,
                    flux=None,
                    flux_error=None,
                    differential_magnitude_error=0.08,
                ),
                LightCurvePoint(
                    observation_time=start + timedelta(hours=12),
                    file_path=Path("frame_03.fits"),
                    differential_magnitude=12.20,
                    instrumental_magnitude=None,
                    flux=None,
                    flux_error=None,
                    differential_magnitude_error=0.04,
                ),
            ],
        )

        self.widget.plot_series(
            series,
            empty_message="No data available.",
            x_axis_mode="phase",
            phase_period_hours=24.0,
            phase_anchor_mode="primary_minimum",
        )
        QApplication.processEvents()

        self.assertEqual(len(self.widget._error_bar_items), 3)
        oldest_alpha = self.widget._error_bar_items[0].opts["pen"].color().alpha()
        newest_alpha = self.widget._error_bar_items[-1].opts["pen"].color().alpha()
        self.assertLess(oldest_alpha, newest_alpha)

    def test_recent_period_error_points_can_filter_to_latest_period(self) -> None:
        start = datetime(2025, 1, 1, 0, 0, 0)
        points = [
            LightCurvePoint(
                observation_time=start,
                file_path=Path("frame_01.fits"),
                differential_magnitude=12.10,
                instrumental_magnitude=None,
                flux=None,
                flux_error=None,
                differential_magnitude_error=0.05,
            ),
            LightCurvePoint(
                observation_time=start + timedelta(hours=30),
                file_path=Path("frame_02.fits"),
                differential_magnitude=12.35,
                instrumental_magnitude=None,
                flux=None,
                flux_error=None,
                differential_magnitude_error=0.08,
            ),
            LightCurvePoint(
                observation_time=start + timedelta(hours=42),
                file_path=Path("frame_03.fits"),
                differential_magnitude=12.20,
                instrumental_magnitude=None,
                flux=None,
                flux_error=None,
                differential_magnitude_error=0.04,
            ),
        ]
        series = LightCurveSeries(
            object_name="Demo",
            source_id="target-1",
            source_name="Target",
            filter_name="V",
            points=points,
        )
        payload = build_light_curve_plot_payload(
            series,
            empty_message="No data available.",
            x_axis_mode="phase",
            phase_period_hours=24.0,
            phase_anchor_mode="primary_minimum",
        )
        self.widget._phase_period_days = 1.0

        filtered_points = self.widget._recent_period_error_points(list(payload.points))

        self.assertEqual(len(filtered_points), 2)
        self.assertTrue(all(point.source_point.file_path.name != "frame_01.fits" for point in filtered_points))

    def test_reset_view_keeps_point_based_y_limits(self) -> None:
        start = datetime(2025, 1, 1, 0, 0, 0)
        series = LightCurveSeries(
            object_name="Demo",
            source_id="target-1",
            source_name="Target",
            filter_name="V",
            points=[
                LightCurvePoint(
                    observation_time=start,
                    file_path=Path("frame_01.fits"),
                    differential_magnitude=12.10,
                    instrumental_magnitude=None,
                    flux=None,
                    flux_error=None,
                    differential_magnitude_error=0.05,
                ),
                LightCurvePoint(
                    observation_time=start + timedelta(minutes=15),
                    file_path=Path("frame_02.fits"),
                    differential_magnitude=12.35,
                    instrumental_magnitude=None,
                    flux=None,
                    flux_error=None,
                    differential_magnitude_error=2.5,
                ),
                LightCurvePoint(
                    observation_time=start + timedelta(minutes=30),
                    file_path=Path("frame_03.fits"),
                    differential_magnitude=12.25,
                    instrumental_magnitude=None,
                    flux=None,
                    flux_error=None,
                    differential_magnitude_error=0.04,
                ),
            ],
        )

        self.widget.plot_series(series, empty_message="No data available.")
        self.widget._plot_item.getViewBox().setYRange(9.0, 15.0, padding=0.0)
        self.widget.reset_view()
        QApplication.processEvents()

        actual_y_range = tuple(float(value) for value in self.widget._plot_item.getViewBox().viewRange()[1])
        expected_y_range = light_curve_y_limits([12.10, 12.35, 12.25])

        self.assertIsNotNone(expected_y_range)
        assert expected_y_range is not None
        self.assertAlmostEqual(actual_y_range[0], expected_y_range[0], places=3)
        self.assertAlmostEqual(actual_y_range[1], expected_y_range[1], places=3)


if __name__ == "__main__":
    unittest.main()