from __future__ import annotations



import os

import math

import tempfile

import unittest

from pathlib import Path

from unittest.mock import patch



import numpy as np



from PySide6.QtCore import QPointF, QRect, QRectF, Qt

from PySide6.QtGui import QColor, QImage, QPainter, QPen

from PySide6.QtWidgets import QApplication



from photometry_app.core.plotting import AnnotatedImageRenderSettings, build_annotated_image_display, build_annotated_image_display_from_array

from photometry_app.ui.image_view import AnnotatedImageView, EquatorialGridOverlay, ImageChartOverlayPanel, ImageInfoItem, ImageInfoLineChart, ImageInfoPanel, ImageInfoSection, ImageOverlay, SelectionOverlay





os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")





class ImageViewInfoPanelTest(unittest.TestCase):

    @classmethod

    def setUpClass(cls) -> None:

        cls._app = QApplication.instance() or QApplication([])



    def test_info_panel_overlays_without_shrinking_image_content(self) -> None:

        view = AnnotatedImageView()

        view.resize(1200, 800)

        view._info_panel = ImageInfoPanel(title="Frame", sections=(ImageInfoSection(title="Frame"),))



        panel_rect = view._info_panel_rect()

        content_rect = view._image_content_rect()



        self.assertGreater(panel_rect.width(), 0.0)

        self.assertEqual(content_rect.left(), 0.0)

        self.assertEqual(content_rect.right(), 1200.0)

        self.assertGreater(panel_rect.left(), content_rect.left())

        self.assertAlmostEqual(panel_rect.height(), 800.0)

        view._info_panel = None

        self.assertEqual(view._image_content_rect().right(), 1200.0)


    def test_chart_overlay_composites_onto_export_image(self) -> None:

        view = AnnotatedImageView()

        base_image = QImage(640, 480, QImage.Format.Format_RGB888)

        base_image.fill(QColor("black"))

        overlay = ImageChartOverlayPanel(
            title="Stack SNR",
            line_chart=ImageInfoLineChart(
                x_label="Stacked frames",
                y_label="SNR",
                x_values=(1.0, 2.0, 3.0),
                y_values=(1.0, 1.4, 1.7),
                highlight_index=2,
            ),
            integration_text="02h34m",
            frame_text="188/421",
        )

        composited = view.composite_chart_overlay_onto_image(base_image, overlay)

        self.assertFalse(composited.isNull())

        self.assertEqual(composited.width(), 640)

        self.assertEqual(composited.height(), 480)


    def test_overlay_layers_composite_onto_export_image(self) -> None:

        view = AnnotatedImageView()

        base_image = QImage(640, 480, QImage.Format.Format_RGB888)

        base_image.fill(QColor("black"))

        overlays = [
            ImageOverlay(
                source_id="astrostack-layer:layer-1",
                name="NGC 1234",
                x=24.0,
                y=24.0,
                aperture_radius=120.0,
                annulus_inner_radius=120.0,
                annulus_outer_radius=120.0,
                color="#ffffff",
                show_annulus=False,
                marker_style="text",
                show_marker=True,
                show_label=True,
                text_color="#ffffff",
                text_size=24.0,
            )
        ]

        composited = view.composite_overlays_onto_image(base_image, overlays)

        self.assertFalse(composited.isNull())

        self.assertEqual(composited.width(), 640)

        self.assertEqual(composited.height(), 480)


    def test_plot_overlay_composites_onto_export_image(self) -> None:

        view = AnnotatedImageView()

        base_image = QImage(640, 480, QImage.Format.Format_RGB888)

        base_image.fill(QColor("black"))

        overlays = [
            ImageOverlay(
                source_id="astrostack-layer:plot-1",
                name="Stack SNR",
                x=40.0,
                y=40.0,
                aperture_radius=240.0,
                annulus_inner_radius=240.0,
                annulus_outer_radius=240.0,
                color="#3a3a3a",
                show_annulus=False,
                marker_style="plot",
                show_marker=True,
                show_label=False,
                ellipse_minor_radius=160.0,
                plot_include_stack_status=True,
                chart_overlay_panel=ImageChartOverlayPanel(
                    title="Stack SNR",
                    line_chart=ImageInfoLineChart(
                        x_label="Stacked frames",
                        y_label="SNR",
                        x_values=(1.0, 2.0, 3.0),
                        y_values=(1.0, 1.4, 1.7),
                        highlight_index=1,
                    ),
                    integration_text="00h05m",
                    frame_text="2/3",
                ),
            )
        ]

        composited = view.composite_overlays_onto_image(base_image, overlays)

        self.assertFalse(composited.isNull())

        self.assertEqual(composited.width(), 640)

        self.assertEqual(composited.height(), 480)


    def test_fixed_label_text_overlay_hit_test_uses_image_anchor(self) -> None:

        view = AnnotatedImageView()

        overlay = ImageOverlay(
            source_id="astrostack-layer:test-text",
            name="Hello",
            x=120.0,
            y=80.0,
            aperture_radius=20.0,
            annulus_inner_radius=20.0,
            annulus_outer_radius=20.0,
            color="#ffffff",
            marker_style="text",
            text_size=24.0,
            fixed_label_position=True,
        )

        bounds = AnnotatedImageView._overlay_text_bounds_in_image_space(overlay)

        center = bounds.center()

        self.assertIsNotNone(view._overlay_hit_test_score(overlay, center))

        self.assertIsNone(view._overlay_hit_test_score(overlay, QPointF(center.x() + bounds.width(), center.y() + bounds.height())))


    def test_info_panel_line_chart_composites_onto_export_image(self) -> None:

        view = AnnotatedImageView()

        base_image = QImage(320, 240, QImage.Format.Format_RGB888)

        base_image.fill(QColor("black"))

        panel = ImageInfoPanel(
            title="Stack SNR",
            line_chart=ImageInfoLineChart(
                x_label="Stacked frames",
                y_label="SNR",
                x_values=(1.0, 2.0, 3.0),
                y_values=(1.0, 1.4, 1.7),
                highlight_index=2,
            ),
        )

        composited = view.composite_info_panel_onto_image(base_image, panel)

        self.assertFalse(composited.isNull())

        self.assertEqual(composited.width(), 320)

        self.assertEqual(composited.height(), 240)


    def test_info_panel_scroll_survives_content_refresh(self) -> None:

        view = AnnotatedImageView()

        view.resize(900, 600)

        with tempfile.TemporaryDirectory() as temp_dir:

            image_path = Path(temp_dir) / "demo.fit"

            image_path.write_text("placeholder", encoding="utf-8")

            data = np.arange(400, dtype=float).reshape(20, 20)

            with patch("photometry_app.core.plotting.read_image_data", return_value=data):

                display = build_annotated_image_display(image_path)

            first_panel = ImageInfoPanel(title="Frame", sections=(ImageInfoSection(title="A", items=(ImageInfoItem("A", "1"),)),))

            second_panel = ImageInfoPanel(title="Frame", sections=(ImageInfoSection(title="B", items=(ImageInfoItem("B", "2"),)),))

            view.set_content(display, [], [], False, info_panel=first_panel)

            view._info_panel_scroll_max = 120.0

            view._info_panel_scroll_offset = 60.0

            view.set_content(display, [], [], False, info_panel=second_panel)

            self.assertEqual(view._info_panel_scroll_offset, 60.0)



    def test_scroll_info_panel_clamps_to_available_range(self) -> None:

        view = AnnotatedImageView()

        view._info_panel_scroll_max = 120.0



        view._scroll_info_panel(60.0)

        self.assertEqual(view._info_panel_scroll_offset, 60.0)



        view._scroll_info_panel(1000.0)

        self.assertEqual(view._info_panel_scroll_offset, 120.0)



        view._scroll_info_panel(-1000.0)

        self.assertEqual(view._info_panel_scroll_offset, 0.0)


    def test_hover_text_formatter_tracks_and_clears_image_point(self) -> None:

        view = AnnotatedImageView()

        view.set_hover_text_formatter(lambda image_x, image_y: f"{image_x:.1f}, {image_y:.1f}")

        view._set_hover_image_point(QPointF(12.0, 34.0))

        self.assertIsNotNone(view._hover_image_point)

        assert view._hover_image_point is not None

        self.assertEqual(view._hover_image_point.x(), 12.0)

        self.assertEqual(view._hover_image_point.y(), 34.0)

        view.set_hover_text_formatter(None)

        self.assertIsNone(view._hover_image_point)


    def test_right_click_emits_image_context_request_when_not_editing(self) -> None:

        class FakeMouseEvent:

            def position(self) -> QPointF:

                return QPointF(10.0, 20.0)

            def globalPosition(self) -> QPointF:

                return QPointF(30.0, 40.0)

            def button(self) -> Qt.MouseButton:

                return Qt.MouseButton.RightButton

            def modifiers(self) -> Qt.KeyboardModifier:

                return Qt.KeyboardModifier.NoModifier

        view = AnnotatedImageView()

        view.widget_to_image = lambda _x, _y: QPointF(12.0, 34.0)  # type: ignore[method-assign]

        received: list[tuple[float, float, object, object]] = []

        view.imageContextRequested.connect(lambda image_x, image_y, global_position, modifiers: received.append((image_x, image_y, global_position, modifiers)))

        view.mousePressEvent(FakeMouseEvent())

        self.assertEqual(len(received), 1)

        self.assertEqual(received[0][0], 12.0)

        self.assertEqual(received[0][1], 34.0)



    def test_capture_view_image_uses_current_widget_size(self) -> None:

        view = AnnotatedImageView()

        view.resize(320, 180)

        view.set_message("Blink export preview")



        captured = view.capture_view_image()



        self.assertEqual(captured.width(), view.width())

        self.assertEqual(captured.height(), view.height())


    def test_overlay_label_image_point_clamps_inside_image_bounds(self) -> None:

        view = AnnotatedImageView()

        view._qimage = QImage(100, 80, QImage.Format.Format_ARGB32)

        overlay = ImageOverlay(

            source_id="large-nebula",

            name="M",

            x=50.0,

            y=40.0,

            aperture_radius=1000.0,

            annulus_inner_radius=1000.0,

            annulus_outer_radius=1000.0,

            color="#ff8844",

            text_size=8.0,

        )

        paint_device = QImage(100, 80, QImage.Format.Format_ARGB32)

        painter = QPainter(paint_device)

        try:

            view._apply_overlay_label_style(painter, overlay)

            label_point = view._overlay_label_image_point(overlay, painter)

            bounds = painter.fontMetrics().boundingRect(overlay.name)

        finally:

            painter.end()

        self.assertGreaterEqual(label_point.x() + bounds.left(), 0.0)

        self.assertLessEqual(label_point.x() + bounds.right(), 100.0)

        self.assertGreaterEqual(label_point.y() + bounds.top(), 0.0)

        self.assertLessEqual(label_point.y() + bounds.bottom(), 80.0)


    def test_overlay_label_image_point_uses_circle_edge_not_bounding_corner(self) -> None:

        view = AnnotatedImageView()

        view._qimage = QImage(240, 200, QImage.Format.Format_ARGB32)

        overlay = ImageOverlay(

            source_id="large-nebula",

            name="",

            x=80.0,

            y=60.0,

            aperture_radius=70.0,

            annulus_inner_radius=70.0,

            annulus_outer_radius=70.0,

            color="#ff8844",

            text_size=8.0,

        )

        paint_device = QImage(240, 200, QImage.Format.Format_ARGB32)

        painter = QPainter(paint_device)

        try:

            view._apply_overlay_label_style(painter, overlay)

            label_point = view._overlay_label_image_point(overlay, painter)

        finally:

            painter.end()

        expected_coordinate = 80.0 + (70.0 + 3.0) / math.sqrt(2.0)

        self.assertAlmostEqual(label_point.x(), expected_coordinate, delta=8.0)

        self.assertAlmostEqual(label_point.y(), 60.0 + (70.0 + 3.0) / math.sqrt(2.0), delta=8.0)

        self.assertLess(label_point.x(), 80.0 + 70.0)

        self.assertLess(label_point.y(), 60.0 + 70.0)


    def test_overlay_label_image_point_uses_ellipse_edge_in_label_direction(self) -> None:

        view = AnnotatedImageView()

        view._qimage = QImage(240, 200, QImage.Format.Format_ARGB32)

        overlay = ImageOverlay(

            source_id="flattened-galaxy",

            name="",

            x=80.0,

            y=60.0,

            aperture_radius=70.0,

            annulus_inner_radius=70.0,

            annulus_outer_radius=70.0,

            color="#44ccff",

            text_size=8.0,

            marker_style="ellipse",

            ellipse_minor_radius=20.0,

            rotation_degrees=0.0,

        )

        paint_device = QImage(240, 200, QImage.Format.Format_ARGB32)

        painter = QPainter(paint_device)

        try:

            view._apply_overlay_label_style(painter, overlay)

            label_point = view._overlay_label_image_point(overlay, painter)

        finally:

            painter.end()

        expected_edge_distance = 1.0 / math.sqrt((0.5 / (70.0 * 70.0)) + (0.5 / (20.0 * 20.0)))

        expected_coordinate = 80.0 + (expected_edge_distance + 3.0) / math.sqrt(2.0)

        circular_coordinate = 80.0 + (70.0 + 3.0) / math.sqrt(2.0)

        self.assertAlmostEqual(label_point.x(), expected_coordinate, delta=8.0)

        self.assertAlmostEqual(label_point.y(), 60.0 + (expected_edge_distance + 3.0) / math.sqrt(2.0), delta=8.0)

        self.assertLess(label_point.x(), circular_coordinate - 10.0)


    def test_overlay_label_image_point_uses_fast_path_when_preferred_position_fits(self) -> None:

        view = AnnotatedImageView()

        view._qimage = QImage(240, 200, QImage.Format.Format_ARGB32)

        overlay = ImageOverlay(

            source_id="large-nebula",

            name="",

            x=80.0,

            y=60.0,

            aperture_radius=70.0,

            annulus_inner_radius=70.0,

            annulus_outer_radius=70.0,

            color="#ff8844",

            text_size=8.0,

        )

        paint_device = QImage(240, 200, QImage.Format.Format_ARGB32)

        painter = QPainter(paint_device)

        try:

            view._apply_overlay_label_style(painter, overlay)

            with patch.object(view, "_point_to_rect_distance", wraps=view._point_to_rect_distance) as distance_mock:

                label_point = view._overlay_label_image_point(overlay, painter)

            self.assertEqual(distance_mock.call_count, 0)

        finally:

            painter.end()

        expected_coordinate = 80.0 + (70.0 + 3.0) / math.sqrt(2.0)

        self.assertAlmostEqual(label_point.x(), expected_coordinate, delta=8.0)

        self.assertAlmostEqual(label_point.y(), 60.0 + (70.0 + 3.0) / math.sqrt(2.0), delta=8.0)


    def test_overlay_label_widget_point_clamps_inside_visible_image_frame(self) -> None:

        view = AnnotatedImageView()

        view.resize(300, 180)

        view._qimage = QImage(100, 80, QImage.Format.Format_ARGB32)

        view._view_center = view._default_view_center()

        overlay = ImageOverlay(

            source_id="large-nebula",

            name="M",

            x=50.0,

            y=40.0,

            aperture_radius=1000.0,

            annulus_inner_radius=1000.0,

            annulus_outer_radius=1000.0,

            color="#ff8844",

            text_size=8.0,

        )

        paint_device = QImage(300, 180, QImage.Format.Format_ARGB32)

        painter = QPainter(paint_device)

        try:

            view._apply_overlay_label_style(painter, overlay)

            label_point = view._overlay_label_widget_point(overlay, painter)

            visible_rect = view._visible_image_widget_rect()

            bounds = painter.fontMetrics().boundingRect(overlay.name)

        finally:

            painter.end()

        self.assertGreaterEqual(label_point.x() + bounds.left(), visible_rect.left())

        self.assertLessEqual(label_point.x() + bounds.right(), visible_rect.right())

        self.assertGreaterEqual(label_point.y() + bounds.top(), visible_rect.top())

        self.assertLessEqual(label_point.y() + bounds.bottom(), visible_rect.bottom())


    def test_overlay_hit_test_uses_drawn_ellipse_instead_of_bounding_circle(self) -> None:

        view = AnnotatedImageView()

        overlay = ImageOverlay(

            source_id="flattened-galaxy",

            name="NGC 7331",

            x=80.0,

            y=60.0,

            aperture_radius=70.0,

            annulus_inner_radius=70.0,

            annulus_outer_radius=70.0,

            color="#44ccff",

            marker_style="ellipse",

            ellipse_minor_radius=20.0,

            rotation_degrees=0.0,

        )

        view._overlays = [overlay]

        self.assertIs(view._overlay_at_image_point(QPointF(120.0, 60.0)), overlay)

        self.assertIsNone(view._overlay_at_image_point(QPointF(80.0, 100.0)))


    def test_overlay_click_is_emitted_on_release_when_no_drag_occurs(self) -> None:

        class _FakeMouseEvent:

            def __init__(self, x: float, y: float, button: object, buttons: object, modifiers: object) -> None:

                self._point = QPointF(x, y)

                self._button = button

                self._buttons = buttons

                self._modifiers = modifiers


            def position(self) -> QPointF:

                return self._point


            def button(self) -> object:

                return self._button


            def buttons(self) -> object:

                return self._buttons


            def modifiers(self) -> object:

                return self._modifiers

        view = AnnotatedImageView()

        view.resize(900, 600)

        view._qimage = QImage(400, 300, QImage.Format.Format_ARGB32)

        view._overlays = [
            ImageOverlay(
                source_id="galaxy",
                name="NGC 7331",
                x=200.0,
                y=180.0,
                aperture_radius=20.0,
                annulus_inner_radius=20.0,
                annulus_outer_radius=20.0,
                color="#44ccff",
            )
        ]

        view.widget_to_image = lambda x, y: QPointF(x, y)  # type: ignore[method-assign]

        clicked: list[str] = []

        view.imageOverlayClicked.connect(lambda overlay: clicked.append(overlay.name))

        view.mousePressEvent(_FakeMouseEvent(200.0, 180.0, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))

        self.assertEqual(clicked, [])

        view.mouseReleaseEvent(_FakeMouseEvent(200.0, 180.0, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))

        self.assertEqual(clicked, ["NGC 7331"])


    def test_drag_from_overlay_pans_without_emitting_overlay_click(self) -> None:

        class _FakeMouseEvent:

            def __init__(self, x: float, y: float, button: object, buttons: object, modifiers: object) -> None:

                self._point = QPointF(x, y)

                self._button = button

                self._buttons = buttons

                self._modifiers = modifiers


            def position(self) -> QPointF:

                return self._point


            def button(self) -> object:

                return self._button


            def buttons(self) -> object:

                return self._buttons


            def modifiers(self) -> object:

                return self._modifiers

        view = AnnotatedImageView()

        view.resize(900, 600)

        view._qimage = QImage(400, 300, QImage.Format.Format_ARGB32)

        view._zoom_scale = 2.0

        view._view_center = QPointF(200.0, 150.0)

        view._overlays = [
            ImageOverlay(
                source_id="galaxy",
                name="NGC 7331",
                x=200.0,
                y=180.0,
                aperture_radius=20.0,
                annulus_inner_radius=20.0,
                annulus_outer_radius=20.0,
                color="#44ccff",
            )
        ]

        view.widget_to_image = lambda x, y: QPointF(x, y)  # type: ignore[method-assign]

        clicked: list[str] = []

        view.imageOverlayClicked.connect(lambda overlay: clicked.append(overlay.name))

        before_center = view._clamped_view_center()

        view.mousePressEvent(_FakeMouseEvent(200.0, 180.0, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))

        view.mouseMoveEvent(_FakeMouseEvent(240.0, 220.0, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))

        after_center = view._clamped_view_center()

        view.mouseReleaseEvent(_FakeMouseEvent(240.0, 220.0, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))

        self.assertEqual(clicked, [])

        self.assertNotAlmostEqual(after_center.x(), before_center.x(), places=4)


    def test_overlay_label_is_hidden_when_overlay_is_outside_visible_widget_frame(self) -> None:

        view = AnnotatedImageView()

        view.resize(300, 180)

        view._qimage = QImage(100, 80, QImage.Format.Format_ARGB32)

        view._zoom_scale = 4.0

        view._view_center = QPointF(80.0, 40.0)

        offscreen_overlay = ImageOverlay(

            source_id="offscreen",

            name="Galaxy",

            x=5.0,

            y=40.0,

            aperture_radius=4.0,

            annulus_inner_radius=4.0,

            annulus_outer_radius=4.0,

            color="#ff8844",

            text_size=8.0,

        )

        onscreen_overlay = ImageOverlay(

            source_id="onscreen",

            name="Galaxy",

            x=80.0,

            y=40.0,

            aperture_radius=4.0,

            annulus_inner_radius=4.0,

            annulus_outer_radius=4.0,

            color="#ff8844",

            text_size=8.0,

        )

        self.assertFalse(view._overlay_intersects_visible_widget_frame(offscreen_overlay))

        self.assertTrue(view._overlay_intersects_visible_widget_frame(onscreen_overlay))



    def test_render_setting_changes_keep_zoom_and_view_center(self) -> None:

        view = AnnotatedImageView()

        view.resize(900, 600)



        with tempfile.TemporaryDirectory() as temp_dir:

            image_path = Path(temp_dir) / "demo.fit"

            image_path.write_text("placeholder", encoding="utf-8")

            data = np.arange(400, dtype=float).reshape(20, 20)



            with patch("photometry_app.core.plotting.read_image_data", return_value=data):

                display = build_annotated_image_display(image_path)

            view.set_content(display, [], [], False)

            view.zoom_in()

            view._view_center = view.widget_to_image(250.0, 220.0)

            before_zoom = view._zoom_scale

            before_center = view._clamped_view_center()



            view.set_content(

                display,

                [],

                [],

                False,

                render_settings=AnnotatedImageRenderSettings(brightness=0.15, contrast=1.4),

            )



            after_center = view._clamped_view_center()

            self.assertEqual(view._zoom_scale, before_zoom)

            self.assertAlmostEqual(after_center.x(), before_center.x(), places=4)

            self.assertAlmostEqual(after_center.y(), before_center.y(), places=4)


    def test_rendered_rgba_and_channel_first_arrays_convert_to_qimage(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            image_path = Path(temp_dir) / "demo.fit"

            image_path.write_text("placeholder", encoding="utf-8")

            display = build_annotated_image_display_from_array(

                np.zeros((2, 3), dtype=float),

                image_path=image_path,

            )

            rgba_render = np.zeros((2, 3, 4), dtype=np.uint8)

            rgba_render[..., 3] = 255

            view = AnnotatedImageView()

            with patch("photometry_app.ui.image_view.render_annotated_image", return_value=rgba_render):

                view.set_content(display, [], [], False)

            self.assertIsNotNone(view._qimage)

            assert view._qimage is not None

            self.assertEqual(view._qimage.width(), 3)

            self.assertEqual(view._qimage.height(), 2)


            channel_first_render = np.zeros((4, 2, 5), dtype=np.uint8)

            view = AnnotatedImageView()

            with patch("photometry_app.ui.image_view.render_annotated_image", return_value=channel_first_render):

                view.set_content(display, [], [], False)

            self.assertIsNotNone(view._qimage)

            assert view._qimage is not None

            self.assertEqual(view._qimage.width(), 5)

            self.assertEqual(view._qimage.height(), 2)



    def test_frame_switch_keeps_zoom_and_view_center(self) -> None:

        view = AnnotatedImageView()

        view.resize(900, 600)



        with tempfile.TemporaryDirectory() as temp_dir:

            image_a_path = Path(temp_dir) / "frame_a.fit"

            image_b_path = Path(temp_dir) / "frame_b.fit"

            image_a_path.write_text("placeholder", encoding="utf-8")

            image_b_path.write_text("placeholder", encoding="utf-8")

            data_a = np.arange(400, dtype=float).reshape(20, 20)

            data_b = np.flipud(data_a)



            with patch("photometry_app.core.plotting.read_image_data", side_effect=[data_a, data_b]):

                display_a = build_annotated_image_display(image_a_path)

                display_b = build_annotated_image_display(image_b_path)



            view.set_content(display_a, [], [], False)

            view.zoom_in()

            view._view_center = view.widget_to_image(250.0, 220.0)

            before_zoom = view._zoom_scale

            before_center = view._clamped_view_center()



            view.set_content(display_b, [], [], False)



            after_center = view._clamped_view_center()

            self.assertEqual(view._zoom_scale, before_zoom)

            self.assertAlmostEqual(after_center.x(), before_center.x(), places=4)

            self.assertAlmostEqual(after_center.y(), before_center.y(), places=4)


    def test_rerenders_generated_display_when_array_changes_but_source_path_is_same(self) -> None:

        view = AnnotatedImageView()

        view.resize(120, 120)



        with tempfile.TemporaryDirectory() as temp_dir:

            image_path = Path(temp_dir) / "source.fit"

            image_path.write_text("placeholder", encoding="utf-8")

            data_a = np.zeros((16, 16), dtype=float)

            data_b = np.zeros((16, 16), dtype=float)

            data_a[3, 3] = 100.0

            data_b[12, 12] = 100.0

            display_a = build_annotated_image_display_from_array(data_a, image_path=image_path)

            display_b = build_annotated_image_display_from_array(data_b, image_path=image_path)

            render_settings = AnnotatedImageRenderSettings(stretch_mode="linear")



            view.set_content(display_a, [], [], False, render_settings=render_settings)

            first_qimage = view._qimage.copy()

            view.set_content(display_b, [], [], False, render_settings=render_settings)



            self.assertIsNotNone(view._qimage)

            self.assertGreater(first_qimage.pixelColor(3, 3).value(), first_qimage.pixelColor(12, 12).value())

            self.assertGreater(view._qimage.pixelColor(12, 12).value(), view._qimage.pixelColor(3, 3).value())



    def test_shift_drag_emits_roi_signals_without_disabling_pan_zoom_mode(self) -> None:

        view = AnnotatedImageView()

        view.resize(900, 600)



        with tempfile.TemporaryDirectory() as temp_dir:

            image_path = Path(temp_dir) / "demo.fit"

            image_path.write_text("placeholder", encoding="utf-8")

            data = np.arange(400, dtype=float).reshape(20, 20)



            with patch("photometry_app.core.plotting.read_image_data", return_value=data):

                display = build_annotated_image_display(image_path)



            pressed: list[tuple[float, float]] = []

            moved: list[tuple[float, float]] = []

            released: list[object] = []

            view.imagePressed.connect(lambda x, y, _button, _mods: pressed.append((x, y)))

            view.imageMoved.connect(lambda x, y, _buttons, _mods: moved.append((x, y)))

            view.imageReleased.connect(lambda button, _mods: released.append(button))

            view.set_content(display, [], [], False, gesture_roi_enabled=True)



            class _FakeMouseEvent:

                def __init__(self, x: float, y: float, button: object, buttons: object, modifiers: object) -> None:

                    self._point = QPointF(x, y)

                    self._button = button

                    self._buttons = buttons

                    self._modifiers = modifiers



                def position(self) -> QPointF:

                    return self._point



                def button(self) -> object:

                    return self._button



                def buttons(self) -> object:

                    return self._buttons



                def modifiers(self) -> object:

                    return self._modifiers



            view.mousePressEvent(_FakeMouseEvent(200.0, 180.0, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier))

            view.mouseMoveEvent(_FakeMouseEvent(240.0, 220.0, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier))

            view.mouseReleaseEvent(_FakeMouseEvent(240.0, 220.0, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.ShiftModifier))



            self.assertEqual(len(pressed), 1)

            self.assertEqual(len(moved), 1)

            self.assertEqual(released, [Qt.MouseButton.LeftButton])

    def test_selection_overlay_refresh_invalidates_only_changed_region(self) -> None:

        class TrackingImageView(AnnotatedImageView):

            def __init__(self) -> None:

                self.update_calls: list[tuple[object, ...]] = []

                super().__init__()

            def update(self, *args: object) -> None:  # type: ignore[override]

                self.update_calls.append(args)

        view = TrackingImageView()

        view.resize(900, 600)

        with tempfile.TemporaryDirectory() as temp_dir:

            image_path = Path(temp_dir) / "demo.fit"

            image_path.write_text("placeholder", encoding="utf-8")

            data = np.arange(400, dtype=float).reshape(20, 20)

            with patch("photometry_app.core.plotting.read_image_data", return_value=data):

                display = build_annotated_image_display(image_path)

            view.set_content(display, [], [], False)

            view.update_calls.clear()

            view.set_selection_overlays([SelectionOverlay("circle", 6.0, 6.0, 9.0, 6.0)])

        self.assertEqual(len(view.update_calls), 1)

        self.assertEqual(len(view.update_calls[0]), 1)

        self.assertIsInstance(view.update_calls[0][0], QRect)

        dirty_rect = view.update_calls[0][0]

        self.assertLess(dirty_rect.width(), view.width())

        self.assertLess(dirty_rect.height(), view.height())



    def test_plain_left_drag_without_shift_keeps_pan_behavior(self) -> None:

        view = AnnotatedImageView()

        view.resize(900, 600)



        with tempfile.TemporaryDirectory() as temp_dir:

            image_path = Path(temp_dir) / "demo.fit"

            image_path.write_text("placeholder", encoding="utf-8")

            data = np.arange(400, dtype=float).reshape(20, 20)



            with patch("photometry_app.core.plotting.read_image_data", return_value=data):

                display = build_annotated_image_display(image_path)



            pressed: list[tuple[float, float]] = []

            view.imagePressed.connect(lambda x, y, _button, _mods: pressed.append((x, y)))

            view.set_content(display, [], [], False, gesture_roi_enabled=True)

            view.zoom_in()

            before_center = view._clamped_view_center()



            class _FakeMouseEvent:

                def __init__(self, x: float, y: float, button: object, buttons: object, modifiers: object) -> None:

                    self._point = QPointF(x, y)

                    self._button = button

                    self._buttons = buttons

                    self._modifiers = modifiers



                def position(self) -> QPointF:

                    return self._point



                def button(self) -> object:

                    return self._button



                def buttons(self) -> object:

                    return self._buttons



                def modifiers(self) -> object:

                    return self._modifiers



            view.mousePressEvent(_FakeMouseEvent(200.0, 180.0, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))

            view.mouseMoveEvent(_FakeMouseEvent(240.0, 220.0, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))

            after_center = view._clamped_view_center()



            self.assertEqual(pressed, [])

            self.assertNotAlmostEqual(after_center.x(), before_center.x(), places=4)


    def test_editor_left_drag_pans_without_emitting_aperture_press(self) -> None:

        view = AnnotatedImageView()

        view.resize(900, 600)

        with tempfile.TemporaryDirectory() as temp_dir:

            image_path = Path(temp_dir) / "demo.fit"

            image_path.write_text("placeholder", encoding="utf-8")

            data = np.arange(400, dtype=float).reshape(20, 20)

            with patch("photometry_app.core.plotting.read_image_data", return_value=data):

                display = build_annotated_image_display(image_path)

            pressed: list[tuple[float, float]] = []

            released: list[object] = []

            view.imagePressed.connect(lambda x, y, _button, _mods: pressed.append((x, y)))

            view.imageReleased.connect(lambda button, _mods: released.append(button))

            view.set_content(display, [], [], True)

            view.zoom_in()

            before_center = view._clamped_view_center()


            class _FakeMouseEvent:

                def __init__(self, x: float, y: float, button: object, buttons: object, modifiers: object) -> None:

                    self._point = QPointF(x, y)

                    self._button = button

                    self._buttons = buttons

                    self._modifiers = modifiers

                def position(self) -> QPointF:

                    return self._point

                def button(self) -> object:

                    return self._button

                def buttons(self) -> object:

                    return self._buttons

                def modifiers(self) -> object:

                    return self._modifiers


            view.mousePressEvent(_FakeMouseEvent(200.0, 180.0, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))

            view.mouseMoveEvent(_FakeMouseEvent(245.0, 225.0, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))

            view.mouseReleaseEvent(_FakeMouseEvent(245.0, 225.0, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))

            after_center = view._clamped_view_center()

            self.assertEqual(pressed, [])

            self.assertEqual(released, [Qt.MouseButton.LeftButton])

            self.assertNotAlmostEqual(after_center.x(), before_center.x(), places=4)


    def test_editor_left_click_emits_aperture_press_on_release(self) -> None:

        view = AnnotatedImageView()

        view.resize(900, 600)

        with tempfile.TemporaryDirectory() as temp_dir:

            image_path = Path(temp_dir) / "demo.fit"

            image_path.write_text("placeholder", encoding="utf-8")

            data = np.arange(400, dtype=float).reshape(20, 20)

            with patch("photometry_app.core.plotting.read_image_data", return_value=data):

                display = build_annotated_image_display(image_path)

            pressed: list[tuple[float, float]] = []

            released: list[object] = []

            view.imagePressed.connect(lambda x, y, _button, _mods: pressed.append((x, y)))

            view.imageReleased.connect(lambda button, _mods: released.append(button))

            view.set_content(display, [], [], True)


            class _FakeMouseEvent:

                def __init__(self, x: float, y: float, button: object, buttons: object, modifiers: object) -> None:

                    self._point = QPointF(x, y)

                    self._button = button

                    self._buttons = buttons

                    self._modifiers = modifiers

                def position(self) -> QPointF:

                    return self._point

                def button(self) -> object:

                    return self._button

                def buttons(self) -> object:

                    return self._buttons

                def modifiers(self) -> object:

                    return self._modifiers


            view.mousePressEvent(_FakeMouseEvent(200.0, 180.0, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))

            self.assertEqual(pressed, [])

            view.mouseReleaseEvent(_FakeMouseEvent(200.0, 180.0, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))

            self.assertEqual(len(pressed), 1)

            self.assertEqual(released, [Qt.MouseButton.LeftButton])


    def test_editor_drag_mode_emits_live_aperture_drag_signals(self) -> None:

        view = AnnotatedImageView()

        view.resize(900, 600)

        with tempfile.TemporaryDirectory() as temp_dir:

            image_path = Path(temp_dir) / "demo.fit"

            image_path.write_text("placeholder", encoding="utf-8")

            data = np.arange(400, dtype=float).reshape(20, 20)

            with patch("photometry_app.core.plotting.read_image_data", return_value=data):

                display = build_annotated_image_display(image_path)

            pressed: list[tuple[float, float]] = []

            moved: list[tuple[float, float]] = []

            released: list[object] = []

            view.imagePressed.connect(lambda x, y, _button, _mods: pressed.append((x, y)))

            view.imageMoved.connect(lambda x, y, _buttons, _mods: moved.append((x, y)))

            view.imageReleased.connect(lambda button, _mods: released.append(button))

            view.set_content(display, [], [], True, editor_drag_enabled=True)

            view.zoom_in()

            edit_point = view.widget_to_image(200.0, 180.0)

            self.assertIsNotNone(edit_point)

            assert edit_point is not None

            overlay = ImageOverlay(

                source_id="manual-target-1",

                name="Target",

                x=float(edit_point.x()),

                y=float(edit_point.y()),

                aperture_radius=6.0,

                annulus_inner_radius=9.0,

                annulus_outer_radius=13.0,

                color="#33aaff",

                show_handles=True,

            )

            view.set_content(display, [overlay], [], True, editor_drag_enabled=True)

            before_center = view._clamped_view_center()

            class _FakeMouseEvent:

                def __init__(self, x: float, y: float, button: object, buttons: object, modifiers: object) -> None:

                    self._point = QPointF(x, y)

                    self._button = button

                    self._buttons = buttons

                    self._modifiers = modifiers

                def position(self) -> QPointF:

                    return self._point

                def button(self) -> object:

                    return self._button

                def buttons(self) -> object:

                    return self._buttons

                def modifiers(self) -> object:

                    return self._modifiers

            view.mousePressEvent(_FakeMouseEvent(200.0, 180.0, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))

            view.mouseMoveEvent(_FakeMouseEvent(245.0, 225.0, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))

            view.mouseReleaseEvent(_FakeMouseEvent(245.0, 225.0, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))

            after_center = view._clamped_view_center()

            self.assertEqual(len(pressed), 1)

            self.assertEqual(len(moved), 1)

            self.assertEqual(released, [Qt.MouseButton.LeftButton])

            self.assertAlmostEqual(after_center.x(), before_center.x(), places=4)


    def test_editor_drag_mode_blank_left_drag_keeps_pan_behavior(self) -> None:

        view = AnnotatedImageView()

        view.resize(900, 600)

        with tempfile.TemporaryDirectory() as temp_dir:

            image_path = Path(temp_dir) / "demo.fit"

            image_path.write_text("placeholder", encoding="utf-8")

            data = np.arange(400, dtype=float).reshape(20, 20)

            with patch("photometry_app.core.plotting.read_image_data", return_value=data):

                display = build_annotated_image_display(image_path)

            pressed: list[tuple[float, float]] = []

            released: list[object] = []

            view.imagePressed.connect(lambda x, y, _button, _mods: pressed.append((x, y)))

            view.imageReleased.connect(lambda button, _mods: released.append(button))

            view.set_content(display, [], [], True, editor_drag_enabled=True)

            view.zoom_in()

            before_center = view._clamped_view_center()

            class _FakeMouseEvent:

                def __init__(self, x: float, y: float, button: object, buttons: object, modifiers: object) -> None:

                    self._point = QPointF(x, y)

                    self._button = button

                    self._buttons = buttons

                    self._modifiers = modifiers

                def position(self) -> QPointF:

                    return self._point

                def button(self) -> object:

                    return self._button

                def buttons(self) -> object:

                    return self._buttons

                def modifiers(self) -> object:

                    return self._modifiers

            view.mousePressEvent(_FakeMouseEvent(200.0, 180.0, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))

            view.mouseMoveEvent(_FakeMouseEvent(245.0, 225.0, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))

            view.mouseReleaseEvent(_FakeMouseEvent(245.0, 225.0, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))

            after_center = view._clamped_view_center()

            self.assertEqual(pressed, [])

            self.assertEqual(released, [Qt.MouseButton.LeftButton])

            self.assertNotAlmostEqual(after_center.x(), before_center.x(), places=4)



    def test_ctrl_left_click_emits_image_pressed_without_starting_pan(self) -> None:

        view = AnnotatedImageView()

        view.resize(900, 600)



        with tempfile.TemporaryDirectory() as temp_dir:

            image_path = Path(temp_dir) / "demo.fit"

            image_path.write_text("placeholder", encoding="utf-8")

            data = np.arange(400, dtype=float).reshape(20, 20)



            with patch("photometry_app.core.plotting.read_image_data", return_value=data):

                display = build_annotated_image_display(image_path)



            pressed: list[tuple[float, float]] = []

            view.imagePressed.connect(lambda x, y, _button, _mods: pressed.append((x, y)))

            view.set_content(display, [], [], False, gesture_roi_enabled=True)

            before_center = view._clamped_view_center()



            class _FakeMouseEvent:

                def __init__(self, x: float, y: float, button: object, buttons: object, modifiers: object) -> None:

                    self._point = QPointF(x, y)

                    self._button = button

                    self._buttons = buttons

                    self._modifiers = modifiers



                def position(self) -> QPointF:

                    return self._point



                def button(self) -> object:

                    return self._button



                def buttons(self) -> object:

                    return self._buttons



                def modifiers(self) -> object:

                    return self._modifiers



            view.mousePressEvent(_FakeMouseEvent(200.0, 180.0, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier))

            after_center = view._clamped_view_center()



            self.assertEqual(len(pressed), 1)

            self.assertAlmostEqual(after_center.x(), before_center.x(), places=4)

            self.assertAlmostEqual(after_center.y(), before_center.y(), places=4)



    def test_overlay_center_dot_can_be_hidden(self) -> None:

        view = AnnotatedImageView()



        class _FakePainter:

            def __init__(self) -> None:

                self.ellipse_calls: list[tuple[object, ...]] = []



            def setPen(self, _pen: object) -> None:

                return None



            def setBrush(self, _brush: object) -> None:

                return None



            def drawEllipse(self, *args: object) -> None:

                self.ellipse_calls.append(args)



            def drawLine(self, *_args: object) -> None:

                return None



        no_dot_painter = _FakePainter()

        view._draw_overlay(

            no_dot_painter,

            ImageOverlay(

                source_id="synthetic:peak",

                name="Measured peak",

                x=20.0,

                y=20.0,

                aperture_radius=5.0,

                annulus_inner_radius=5.0,

                annulus_outer_radius=5.0,

                color="#38bdf8",

                show_annulus=False,

                show_center_dot=False,

            ),

        )



        with_dot_painter = _FakePainter()

        view._draw_overlay(

            with_dot_painter,

            ImageOverlay(

                source_id="synthetic:peak",

                name="Measured peak",

                x=20.0,

                y=20.0,

                aperture_radius=5.0,

                annulus_inner_radius=5.0,

                annulus_outer_radius=5.0,

                color="#38bdf8",

                show_annulus=False,

                show_center_dot=True,

            ),

        )



        self.assertEqual(len(no_dot_painter.ellipse_calls), 1)

        self.assertEqual(len(with_dot_painter.ellipse_calls), 2)

    def test_draw_overlay_target_marker_draws_square_and_guides(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            image_path = Path(temp_dir) / "synthetic_target.fit"
            image_path.write_text("placeholder", encoding="utf-8")

            view = AnnotatedImageView()

            view.set_content(
                build_annotated_image_display_from_array(np.ones((80, 80), dtype=np.float32), image_path=image_path),
                overlays=[],
                grid_overlays=[],
                editor_enabled=False,
            )

            class _FakePainter:
                def __init__(self) -> None:
                    self.rect_calls: list[object] = []
                    self.line_calls: list[object] = []
                    self.ellipse_calls: list[object] = []

                def setPen(self, _pen: object) -> None:
                    return None

                def setBrush(self, _brush: object) -> None:
                    return None

                def drawRect(self, *args: object) -> None:
                    self.rect_calls.append(args)

                def drawLine(self, *args: object) -> None:
                    self.line_calls.append(args)

                def drawEllipse(self, *args: object) -> None:
                    self.ellipse_calls.append(args)

            painter = _FakePainter()
            view._draw_overlay(
                painter,
                ImageOverlay(
                    source_id="discover:selected",
                    name="Candidate C2",
                    x=40.0,
                    y=40.0,
                    aperture_radius=8.0,
                    annulus_inner_radius=8.0,
                    annulus_outer_radius=8.0,
                    color="#ef4444",
                    show_annulus=False,
                    marker_style="target",
                    show_center_dot=True,
                    outline_color="#ffffff",
                    outline_width=3.0,
                ),
            )

            self.assertGreaterEqual(len(painter.rect_calls), 2)
            self.assertGreaterEqual(len(painter.line_calls), 8)
            self.assertEqual(len(painter.ellipse_calls), 0)

    def test_grid_overlay_label_point_tracks_visible_line_segment_when_zoomed(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            image_path = Path(temp_dir) / "synthetic_grid.fit"
            image_path.write_text("placeholder", encoding="utf-8")

            view = AnnotatedImageView()
            view.resize(320, 240)
            view.set_content(
                build_annotated_image_display_from_array(np.ones((120, 120), dtype=np.float32), image_path=image_path),
                overlays=[],
                grid_overlays=[],
                editor_enabled=False,
            )
            view._zoom_scale = 4.0
            view._view_center = QPointF(60.0, 60.0)

            overlay = EquatorialGridOverlay(
                label="00h00m00s",
                points=((10.0, 60.0), (20.0, 60.0), (40.0, 60.0), (60.0, 60.0), (80.0, 60.0)),
                color="#60a5fa",
                axis_kind="ra",
            )

            paint_device = QImage(320, 240, QImage.Format.Format_ARGB32)
            painter = QPainter(paint_device)
            try:
                label_point = view._grid_overlay_label_widget_point(overlay, painter)
                label_rect = None if label_point is None else view._label_rect_at(label_point, painter, overlay.label)
            finally:
                painter.end()

            self.assertIsNotNone(label_point)
            assert label_point is not None
            assert label_rect is not None
            self.assertTrue(view._visible_image_widget_rect().intersects(label_rect))



    def test_grid_overlay_label_point_hides_lines_outside_visible_frame(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            image_path = Path(temp_dir) / "synthetic_grid.fit"
            image_path.write_text("placeholder", encoding="utf-8")

            view = AnnotatedImageView()
            view.resize(320, 240)
            view.set_content(
                build_annotated_image_display_from_array(np.ones((120, 120), dtype=np.float32), image_path=image_path),
                overlays=[],
                grid_overlays=[],
                editor_enabled=False,
            )
            view._zoom_scale = 4.0
            view._view_center = QPointF(60.0, 60.0)

            overlay = EquatorialGridOverlay(
                label="+00d00m00s",
                points=((0.0, 5.0), (40.0, 5.0), (80.0, 5.0), (120.0, 5.0)),
                color="#60a5fa",
                axis_kind="dec",
            )

            paint_device = QImage(320, 240, QImage.Format.Format_ARGB32)
            painter = QPainter(paint_device)
            try:
                label_point = view._grid_overlay_label_widget_point(overlay, painter)
            finally:
                painter.end()

            self.assertIsNone(label_point)



    def test_grid_overlay_label_point_prefers_frame_edges_when_zoomed_out(self) -> None:

        with tempfile.TemporaryDirectory() as temp_dir:

            image_path = Path(temp_dir) / "synthetic_grid.fit"
            image_path.write_text("placeholder", encoding="utf-8")

            view = AnnotatedImageView()
            view.resize(320, 240)
            view.set_content(
                build_annotated_image_display_from_array(np.ones((120, 120), dtype=np.float32), image_path=image_path),
                overlays=[],
                grid_overlays=[],
                editor_enabled=False,
            )

            dec_overlay = EquatorialGridOverlay(
                label="+00d00m00s",
                points=((60.0, 0.0), (60.0, 40.0), (60.0, 80.0), (60.0, 119.0)),
                color="#60a5fa",
                axis_kind="dec",
            )
            ra_overlay = EquatorialGridOverlay(
                label="00h00m00s",
                points=((0.0, 60.0), (40.0, 60.0), (80.0, 60.0), (119.0, 60.0)),
                color="#60a5fa",
                axis_kind="ra",
            )

            paint_device = QImage(320, 240, QImage.Format.Format_ARGB32)
            painter = QPainter(paint_device)
            try:
                dec_label_point = view._grid_overlay_label_widget_point(dec_overlay, painter)
                ra_label_point = view._grid_overlay_label_widget_point(ra_overlay, painter)
                dec_label_rect = None if dec_label_point is None else view._label_rect_at(dec_label_point, painter, dec_overlay.label)
                ra_label_rect = None if ra_label_point is None else view._label_rect_at(ra_label_point, painter, ra_overlay.label)
            finally:
                painter.end()

            visible_rect = view._visible_image_widget_rect()
            self.assertIsNotNone(dec_label_point)
            self.assertIsNotNone(ra_label_point)
            assert dec_label_point is not None
            assert ra_label_point is not None
            assert dec_label_rect is not None
            assert ra_label_rect is not None
            self.assertLess(
                abs(dec_label_rect.bottom() - visible_rect.bottom()),
                32.0,
            )
            self.assertLess(
                abs(ra_label_rect.left() - visible_rect.left()),
                32.0,
            )



    def test_draw_grid_overlay_uses_overlay_pen_style(self) -> None:

        view = AnnotatedImageView()

        overlay = EquatorialGridOverlay(
            label="00h00m",
            points=((0.0, 0.0), (10.0, 10.0)),
            color="#9aa3ad",
            pen_style=Qt.PenStyle.SolidLine,
        )

        class _FakePainter:
            def __init__(self) -> None:
                self.pen: QPen | None = None
                self.line_calls: list[object] = []

            def setPen(self, pen: QPen) -> None:
                self.pen = pen

            def drawLine(self, *args: object) -> None:
                self.line_calls.append(args)

        painter = _FakePainter()

        view._draw_grid_overlay(painter, overlay)

        self.assertIsNotNone(painter.pen)
        assert painter.pen is not None
        self.assertEqual(painter.pen.style(), Qt.PenStyle.SolidLine)
        self.assertEqual(len(painter.line_calls), 1)

    def test_comparison_content_is_opt_in_and_clears_without_affecting_primary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            primary_path = Path(temp_dir) / "primary.fit"
            survey_path = Path(temp_dir) / "survey.fit"
            primary_path.write_text("primary", encoding="utf-8")
            survey_path.write_text("survey", encoding="utf-8")
            primary = build_annotated_image_display_from_array(
                np.arange(10000, dtype=np.float32).reshape(100, 100),
                image_path=primary_path,
            )
            survey = build_annotated_image_display_from_array(
                np.arange(10000, dtype=np.float32).reshape(100, 100)[::-1],
                image_path=survey_path,
            )
            view = AnnotatedImageView()
            view.resize(200, 100)
            view.set_content(primary, overlays=[], grid_overlays=[], editor_enabled=False)

            self.assertFalse(view._comparison_is_active())
            view.set_comparison_content(
                survey,
                target_rect=QRectF(10.0, 20.0, 70.0, 60.0),
                render_settings=AnnotatedImageRenderSettings(stretch_mode="linear"),
            )
            self.assertTrue(view._comparison_is_active())
            self.assertEqual(view._comparison_target_rect, QRectF(10.0, 20.0, 70.0, 60.0))

            view.clear_comparison()
            self.assertFalse(view._comparison_is_active())
            self.assertIsNotNone(view._qimage)

    def test_comparison_content_paints_only_right_of_divider(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            primary_path = Path(temp_dir) / "primary.fit"
            survey_path = Path(temp_dir) / "survey.fit"
            primary_path.write_text("primary", encoding="utf-8")
            survey_path.write_text("survey", encoding="utf-8")
            constant_data = np.ones((100, 100), dtype=np.float32)
            view = AnnotatedImageView()
            view.resize(200, 360)
            view.set_content(
                build_annotated_image_display_from_array(constant_data, image_path=primary_path),
                overlays=[],
                grid_overlays=[],
                editor_enabled=False,
            )
            view.set_comparison_content(
                build_annotated_image_display_from_array(constant_data, image_path=survey_path),
                target_rect=QRectF(0.0, 0.0, 100.0, 100.0),
                render_settings=AnnotatedImageRenderSettings(inverted=True),
            )

            captured = view.capture_view_image()
            left_pixel = captured.pixelColor(50, 180)
            right_pixel = captured.pixelColor(150, 180)

            self.assertLess(left_pixel.lightness(), 32)
            self.assertGreater(right_pixel.lightness(), 220)

    def test_comparison_divider_drag_updates_only_split_fraction(self) -> None:
        class FakeMouseEvent:
            def __init__(self, x: float, y: float, *, pressed: bool) -> None:
                self._position = QPointF(x, y)
                self._pressed = pressed

            def position(self) -> QPointF:
                return self._position

            def button(self) -> Qt.MouseButton:
                return Qt.MouseButton.LeftButton

            def buttons(self) -> Qt.MouseButton:
                return Qt.MouseButton.LeftButton if self._pressed else Qt.MouseButton.NoButton

            def modifiers(self) -> Qt.KeyboardModifier:
                return Qt.KeyboardModifier.NoModifier

        with tempfile.TemporaryDirectory() as temp_dir:
            primary_path = Path(temp_dir) / "primary.fit"
            survey_path = Path(temp_dir) / "survey.fit"
            primary_path.write_text("primary", encoding="utf-8")
            survey_path.write_text("survey", encoding="utf-8")
            data = np.arange(10000, dtype=np.float32).reshape(100, 100)
            view = AnnotatedImageView()
            view.resize(200, 100)
            view.set_content(
                build_annotated_image_display_from_array(data, image_path=primary_path),
                overlays=[],
                grid_overlays=[],
                editor_enabled=False,
            )
            view.set_comparison_content(
                build_annotated_image_display_from_array(data[::-1], image_path=survey_path),
                target_rect=QRectF(0.0, 0.0, 100.0, 100.0),
            )

            view.mousePressEvent(FakeMouseEvent(100.0, 180.0, pressed=True))
            self.assertTrue(view._comparison_split_drag_active)
            view.mouseMoveEvent(FakeMouseEvent(160.0, 180.0, pressed=True))
            self.assertAlmostEqual(view.comparison_split_fraction(), 0.8, places=2)
            self.assertIsNone(view._pan_anchor)
            view.mouseReleaseEvent(FakeMouseEvent(160.0, 180.0, pressed=False))
            self.assertFalse(view._comparison_split_drag_active)

    def test_comparison_split_stays_active_without_survey_raster(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            primary_path = Path(temp_dir) / "primary.fit"
            survey_path = Path(temp_dir) / "survey.fit"
            primary_path.write_text("primary", encoding="utf-8")
            survey_path.write_text("survey", encoding="utf-8")
            data = np.arange(10000, dtype=np.float32).reshape(100, 100)
            view = AnnotatedImageView()
            view.resize(200, 100)
            view.set_content(
                build_annotated_image_display_from_array(data, image_path=primary_path),
                overlays=[],
                grid_overlays=[],
                editor_enabled=False,
            )
            view.set_comparison_split_enabled(True)
            self.assertTrue(view._comparison_is_active())
            self.assertFalse(view._comparison_has_survey_raster())

            view.clear_comparison_survey_content()

            self.assertTrue(view._comparison_is_active())
            self.assertFalse(view._comparison_has_survey_raster())
            self.assertIsNotNone(view._comparison_split_widget_x())

    def test_comparison_loading_indicator_is_active_while_loading(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            primary_path = Path(temp_dir) / "primary.fit"
            primary_path.write_text("primary", encoding="utf-8")
            view = AnnotatedImageView()
            view.resize(200, 100)
            view.set_content(
                build_annotated_image_display_from_array(
                    np.ones((100, 100), dtype=np.float32),
                    image_path=primary_path,
                ),
                overlays=[],
                grid_overlays=[],
                editor_enabled=False,
            )
            view.set_comparison_split_enabled(True)
            view.set_comparison_loading(True)
            view.set_comparison_loading_message("Loading DSS2 Blue preview…")

            self.assertTrue(view._comparison_loading)
            self.assertEqual(view.comparison_loading_message(), "Loading DSS2 Blue preview…")
            self.assertTrue(view._comparison_loading_timer.isActive())

            view.set_comparison_loading(False)

            self.assertFalse(view._comparison_loading)
            self.assertEqual(view.comparison_loading_message(), "")
            self.assertFalse(view._comparison_loading_timer.isActive())

    def test_visible_image_rect_tracks_zoomed_viewport(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "primary.fit"
            image_path.write_text("primary", encoding="utf-8")
            view = AnnotatedImageView()
            view.resize(200, 100)
            view.set_content(
                build_annotated_image_display_from_array(
                    np.ones((100, 100), dtype=np.float32),
                    image_path=image_path,
                ),
                overlays=[],
                grid_overlays=[],
                editor_enabled=False,
            )
            view._zoom_scale = 2.0
            view._view_center = QPointF(50.0, 50.0)

            visible = view.visible_image_rect()
            self.assertAlmostEqual(visible.left(), 25.0, places=2)
            self.assertAlmostEqual(visible.top(), 5.0, places=2)
            self.assertAlmostEqual(visible.width(), 50.0, places=2)
            self.assertAlmostEqual(visible.height(), 90.0, places=2)





if __name__ == "__main__":

    unittest.main()