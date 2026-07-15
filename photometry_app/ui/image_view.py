from __future__ import annotations



from collections.abc import Callable

from dataclasses import dataclass

import math



import numpy as np

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal

from PySide6.QtGui import QColor, QFont, QFontMetrics, QFontMetricsF, QImage, QPainter, QPainterPath, QPen

from PySide6.QtWidgets import QWidget



from photometry_app.core.plotting import AnnotatedImageDisplay, AnnotatedImageRenderSettings, render_annotated_image


_OVERLAY_LABEL_CANDIDATE_ANGLES: tuple[float, ...] = (
    45.0,
    30.0,
    60.0,
    15.0,
    75.0,
    0.0,
    90.0,
    330.0,
    120.0,
    300.0,
    150.0,
    270.0,
    180.0,
    240.0,
    210.0,
)





@dataclass(frozen=True)

class ImageOverlay:

    source_id: str

    name: str

    x: float

    y: float

    aperture_radius: float

    annulus_inner_radius: float

    annulus_outer_radius: float

    color: str

    show_annulus: bool = True

    show_handles: bool = False

    marker_style: str = "circle"

    show_marker: bool = True

    show_label: bool = True

    pen_width: float = 0.0

    text_color: str | None = None

    fill_color: str | None = None

    fill_opacity: float = 64.0 / 255.0

    stroke_opacity: float = 1.0

    text_font: QFont | None = None

    text_size: float | None = None

    text_opacity: float = 1.0

    text_outline_color: str | None = None

    text_outline_width: float = 0.0

    accent_color: str | None = None

    show_center_dot: bool = True

    outline_color: str | None = None

    outline_width: float = 0.0

    ellipse_minor_radius: float | None = None

    rotation_degrees: float = 0.0

    fixed_label_position: bool = False

    line_chart: ImageInfoLineChart | None = None

    plot_title: str | None = None

    chart_overlay_panel: ImageChartOverlayPanel | None = None

    plot_include_stack_status: bool = False

    plot_style: ImagePlotStyle | None = None

    dynamic_metric_kind: str | None = None


@dataclass(frozen=True)
class ImagePlotStyle:
    corner_radius: float = 0.0
    stroke_color: str = "#3a3a3a"
    stroke_width: float = 0.0
    stroke_opacity: float = 1.0
    fill_color: str = "#121212"
    fill_opacity: float = 228.0 / 255.0
    title_align_h: str = "left"
    title_align_v: str = "top"
    title_offset_x: float = 0.0
    title_offset_y: float = 0.0
    x_label_offset_x: float = 0.0
    x_label_offset_y: float = 0.0
    y_label_offset_x: float = 0.0
    y_label_offset_y: float = 0.0
    curve_color: str = "#3d8bfd"
    curve_opacity: float = 1.0
    curve_width: float = 0.0
    highlight_color: str = "#ffd166"
    highlight_opacity: float = 1.0
    highlight_radius: float = 0.0
    chart_margin_left: float = 0.0
    chart_margin_right: float = 0.0
    chart_margin_top: float = 0.0
    chart_margin_bottom: float = 0.0
    title_text_color: str = "#f2f2f2"
    title_text_opacity: float = 1.0
    title_font_family: str = ""
    title_font_style: str = "regular"
    title_font_size: float = 0.0
    label_text_color: str = "#f2f2f2"
    label_text_opacity: float = 1.0
    label_font_family: str = ""
    label_font_style: str = "regular"
    label_font_size: float = 0.0
    accent_text_color: str = "#ffd166"



@dataclass(frozen=True)

class EquatorialGridOverlay:

    label: str

    points: tuple[tuple[float, float], ...]

    color: str

    axis_kind: str = ""

    pen_style: Qt.PenStyle = Qt.PenStyle.SolidLine





@dataclass(frozen=True)

class SelectionOverlay:

    shape: str

    x0: float

    y0: float

    x1: float

    y1: float

    color: str = "#ff9f1c"





@dataclass(frozen=True)

class MotionVectorOverlay:

    x: float

    y: float

    dx: float

    dy: float

    color: str

    width: float = 1.5

    show_anchor: bool = True





@dataclass(frozen=True)

class ImageInfoItem:

    label: str

    value: str





@dataclass(frozen=True)

class ImageInfoSection:

    title: str

    items: tuple[ImageInfoItem, ...] = ()

    note: str | None = None





@dataclass(frozen=True)

class ImageInfoLineChart:

    x_label: str

    y_label: str

    x_values: tuple[float, ...]

    y_values: tuple[float, ...]

    highlight_index: int | None = None





@dataclass(frozen=True)

class ImageInfoPanel:

    title: str

    subtitle: str | None = None

    sections: tuple[ImageInfoSection, ...] = ()

    footer: str | None = None

    line_chart: ImageInfoLineChart | None = None





@dataclass(frozen=True)

class ImageChartOverlayPanel:

    title: str

    line_chart: ImageInfoLineChart

    integration_text: str

    frame_text: str





@dataclass(frozen=True)

class ImageTextDecoration:

    text: str

    location: str

    font_family: str

    font_size_pt: float

    color: str





@dataclass(frozen=True)

class ImageBandDecoration:

    location: str

    width_fraction: float

    height_fraction: float

    color: str

    opacity: float





@dataclass(frozen=True)

class ImageDecorationOverlays:

    title: ImageTextDecoration | None = None

    location_label: ImageTextDecoration | None = None

    band: ImageBandDecoration | None = None





_CHART_OVERLAY_WIDTH_FRACTION = 0.20

_CHART_OVERLAY_HEIGHT_FRACTION = 0.15

_CHART_OVERLAY_EDGE_MARGIN_FRACTION = 0.015

_CHART_OVERLAY_MIN_WIDTH = 96.0

_CHART_OVERLAY_MIN_HEIGHT = 72.0





class AnnotatedImageView(QWidget):

    imagePressed = Signal(float, float, object, object)

    imageOverlayClicked = Signal(object)

    imageContextRequested = Signal(float, float, object, object)

    imageMoved = Signal(float, float, object, object)

    imageReleased = Signal(object, object)

    imageWheelAdjusted = Signal(float, float, float, object)

    viewportChanged = Signal()



    def __init__(self, parent: QWidget | None = None) -> None:

        super().__init__(parent)

        self.setMinimumHeight(360)

        self.setMouseTracking(True)

        self._display: AnnotatedImageDisplay | None = None

        self._qimage: QImage | None = None

        self._comparison_qimage: QImage | None = None

        self._comparison_display_key: tuple[object, ...] | None = None

        self._comparison_render_settings: AnnotatedImageRenderSettings | None = None

        self._comparison_target_rect: QRectF | None = None

        self._comparison_split_enabled = False

        self._comparison_loading = False

        self._comparison_loading_message = ""

        self._comparison_loading_angle = 0.0

        self._comparison_split_fraction = 0.5

        self._comparison_split_drag_active = False

        self._comparison_loading_timer = QTimer(self)

        self._comparison_loading_timer.setInterval(50)

        self._comparison_loading_timer.timeout.connect(self._advance_comparison_loading_animation)

        self._message = "Select a processed series point or image row to inspect the stretched frame."

        self._overlays: list[ImageOverlay] = []

        self._static_overlay_count = 0

        self._static_overlay_cache_key: tuple[object, ...] | None = None

        self._static_overlay_shape_cache_image: QImage | None = None

        self._static_overlay_label_cache_image: QImage | None = None

        self._grid_overlays: list[EquatorialGridOverlay] = []

        self._editor_enabled = False

        self._editor_drag_enabled = False

        self._editor_drag_active = False

        self._direct_edit_enabled = False

        self._direct_edit_draw_enabled = False

        self._direct_edit_select_enabled = False

        self._direct_edit_active = False

        self._gesture_roi_enabled = False

        self._gesture_roi_active = False

        self._gesture_roi_shift_required = True

        self._zoom_scale = 1.0

        self._view_center: QPointF | None = None

        self._pan_anchor: QPointF | None = None

        self._pan_center_start: QPointF | None = None

        self._pan_drag_active = False

        self._editor_pending_click_button: Qt.MouseButton | None = None

        self._pending_overlay_click: ImageOverlay | None = None

        self._current_image_key: tuple[str, int, int] | None = None

        self._safe_margin_fraction = 0.0

        self._selection_overlays: list[SelectionOverlay] = []

        self._motion_vector_overlays: list[MotionVectorOverlay] = []

        self._render_settings = AnnotatedImageRenderSettings()

        self._info_panel: ImageInfoPanel | None = None

        self._info_panel_scroll_offset = 0.0

        self._info_panel_scroll_max = 0.0

        self._chart_overlay_panel: ImageChartOverlayPanel | None = None

        self._decoration_overlays: ImageDecorationOverlays | None = None

        self._hover_image_point: QPointF | None = None

        self._hover_text_formatter: Callable[[float, float], str | None] | None = None

        self._overlay_label_formatter: Callable[[ImageOverlay], str] | None = None



    def invalidate_static_overlay_cache(self) -> None:

        self._static_overlay_cache_key = None

        self._static_overlay_shape_cache_image = None

        self._static_overlay_label_cache_image = None



    def set_overlay_label_formatter(self, formatter: Callable[[ImageOverlay], str] | None) -> None:

        self._overlay_label_formatter = formatter

        self.invalidate_static_overlay_cache()



    def _overlay_label_text(self, overlay: ImageOverlay) -> str:

        formatter = self._overlay_label_formatter

        if formatter is not None and (
            overlay.dynamic_metric_kind
            or (
                overlay.marker_style == "text"
                and str(overlay.source_id or "").startswith("astrostack-layer:")
            )
        ):

            try:

                resolved = str(formatter(overlay)).strip()

                if resolved:

                    return resolved

            except Exception:

                pass

        return str(overlay.name or "")



    def set_message(self, message: str) -> None:

        self._display = None

        self._qimage = None

        self.clear_comparison()

        self._message = message

        self._overlays = []

        self._static_overlay_count = 0

        self._static_overlay_cache_key = None

        self._static_overlay_shape_cache_image = None

        self._static_overlay_label_cache_image = None

        self._grid_overlays = []

        self._zoom_scale = 1.0

        self._view_center = None

        self._pan_anchor = None

        self._pan_center_start = None

        self._editor_pending_click_button = None

        self._direct_edit_enabled = False

        self._direct_edit_draw_enabled = False

        self._direct_edit_select_enabled = False

        self._direct_edit_active = False

        self._current_image_key = None

        self._safe_margin_fraction = 0.0

        self._selection_overlays = []

        self._motion_vector_overlays = []

        self._info_panel = None

        self._info_panel_scroll_offset = 0.0

        self._info_panel_scroll_max = 0.0

        self._chart_overlay_panel = None

        self._decoration_overlays = None

        self._hover_image_point = None

        self.update()



    def set_content(

        self,

        display: AnnotatedImageDisplay,

        overlays: list[ImageOverlay],

        grid_overlays: list[EquatorialGridOverlay],

        editor_enabled: bool,

        reset_view: bool = False,

        safe_margin_fraction: float = 0.0,

        selection_overlays: list[SelectionOverlay] | None = None,

        motion_vector_overlays: list[MotionVectorOverlay] | None = None,

        render_settings: AnnotatedImageRenderSettings | None = None,

        pre_rendered_qimage: QImage | None = None,

        info_panel: ImageInfoPanel | None = None,

        chart_overlay_panel: ImageChartOverlayPanel | None = None,

        decoration_overlays: ImageDecorationOverlays | None = None,

        gesture_roi_enabled: bool = False,

        gesture_roi_shift_required: bool = True,

        direct_edit_enabled: bool = False,

        direct_edit_draw_enabled: bool = False,

        direct_edit_select_enabled: bool = False,

        editor_drag_enabled: bool = False,

        static_overlay_count: int = 0,

    ) -> None:

        view_was_uninitialized = self._view_center is None

        image_key = (
            str(display.image_path.resolve()),
            int(display.image_path.stat().st_mtime_ns),
            int(display.image_path.stat().st_size),
            id(display.normalized_data),
        )

        resolved_render_settings = render_settings or AnnotatedImageRenderSettings()

        image_changed = image_key != self._current_image_key

        if pre_rendered_qimage is not None:

            self._qimage = pre_rendered_qimage

            self._current_image_key = image_key

            self._render_settings = resolved_render_settings

        elif image_changed or resolved_render_settings != self._render_settings or self._qimage is None:

            self._qimage = self._display_to_qimage(display, resolved_render_settings)

            self._current_image_key = image_key

            self._render_settings = resolved_render_settings

        self._display = display

        self._hover_image_point = None

        self._overlays = overlays

        self._static_overlay_count = max(0, min(len(self._overlays), int(static_overlay_count)))

        self._grid_overlays = grid_overlays

        self._editor_enabled = editor_enabled

        self._editor_drag_enabled = bool(editor_enabled and editor_drag_enabled)

        if not self._editor_drag_enabled:

            self._editor_drag_active = False

        self._direct_edit_enabled = bool(direct_edit_enabled)

        self._direct_edit_draw_enabled = bool(direct_edit_draw_enabled)

        self._direct_edit_select_enabled = bool(direct_edit_select_enabled)

        if not self._direct_edit_enabled:

            self._direct_edit_active = False

        self._gesture_roi_enabled = bool(gesture_roi_enabled)

        self._gesture_roi_shift_required = bool(gesture_roi_shift_required)

        if not self._gesture_roi_enabled:

            self._gesture_roi_active = False

        self._safe_margin_fraction = max(0.0, min(0.49, float(safe_margin_fraction)))

        self._selection_overlays = list(selection_overlays or [])

        self._motion_vector_overlays = list(motion_vector_overlays or [])

        self._info_panel = info_panel

        self._chart_overlay_panel = chart_overlay_panel

        self._decoration_overlays = decoration_overlays

        self._info_panel_scroll_offset = self._clamp_info_panel_scroll(self._info_panel_scroll_offset)

        if reset_view:

            self._zoom_scale = 1.0

            self._view_center = self._default_view_center()

        elif self._view_center is None:

            self._view_center = self._default_view_center()

        self.update()

        if image_changed or reset_view or view_was_uninitialized:

            self.viewportChanged.emit()

    def set_comparison_content(

        self,

        display: AnnotatedImageDisplay,

        *,

        target_rect: QRectF,

        render_settings: AnnotatedImageRenderSettings | None = None,

    ) -> None:

        resolved_render_settings = render_settings or AnnotatedImageRenderSettings()

        comparison_key = (

            str(display.image_path.resolve()),

            id(display.normalized_data),

            tuple(int(value) for value in display.normalized_data.shape),

        )

        if (

            comparison_key != self._comparison_display_key

            or resolved_render_settings != self._comparison_render_settings

            or self._comparison_qimage is None

        ):

            self._comparison_qimage = self._display_to_qimage(display, resolved_render_settings)

            self._comparison_display_key = comparison_key

            self._comparison_render_settings = resolved_render_settings

        self._comparison_target_rect = QRectF(target_rect).normalized()

        self._comparison_split_enabled = (

            self._comparison_target_rect.width() > 0.0

            and self._comparison_target_rect.height() > 0.0

        )

        self.update()

    def set_comparison_split_enabled(self, enabled: bool) -> None:

        self._comparison_split_enabled = bool(enabled)

        if not self._comparison_split_enabled:

            self.set_comparison_loading(False)

            self.clear_comparison_survey_content()

            self._comparison_split_drag_active = False

        self.update()

    def set_comparison_loading(self, loading: bool) -> None:

        self._comparison_loading = bool(loading) and self._comparison_split_enabled

        if self._comparison_loading:

            if not self._comparison_loading_timer.isActive():

                self._comparison_loading_timer.start()

        else:

            self._comparison_loading_timer.stop()

            self._comparison_loading_angle = 0.0

            self._comparison_loading_message = ""

        self.update()

    def set_comparison_loading_message(self, message: str) -> None:

        self._comparison_loading_message = str(message or "")

        self.update()

    def comparison_loading_message(self) -> str:

        return str(self._comparison_loading_message)

    def clear_comparison_survey_content(self) -> None:

        self._comparison_qimage = None

        self._comparison_display_key = None

        self._comparison_render_settings = None

        self._comparison_target_rect = None

        self.update()

    def clear_comparison(self) -> None:

        self.set_comparison_loading(False)

        self.clear_comparison_survey_content()

        self._comparison_split_enabled = False

        self._comparison_split_drag_active = False

        self.update()

    def _advance_comparison_loading_animation(self) -> None:

        if not self._comparison_loading:

            return

        self._comparison_loading_angle = (self._comparison_loading_angle + 36.0) % 360.0

        self.update()

    def comparison_split_fraction(self) -> float:

        return float(self._comparison_split_fraction)

    def set_comparison_split_fraction(self, fraction: float) -> None:

        resolved_fraction = min(0.98, max(0.02, float(fraction)))

        if math.isclose(resolved_fraction, self._comparison_split_fraction, abs_tol=1.0e-6):

            return

        self._comparison_split_fraction = resolved_fraction

        self.update()

    def comparison_divider_travel_pixels(self) -> float:

        visible_rect = self._visible_image_widget_rect()

        if visible_rect.isEmpty():

            return 0.0

        return float(visible_rect.width()) * 0.96

    def capture_view_image_at_comparison_split(self, fraction: float) -> QImage:

        self._comparison_split_fraction = min(0.98, max(0.02, float(fraction)))

        return self.capture_view_image()

    def visible_image_rect(self, *, margin_fraction: float = 0.0) -> QRectF:

        if self._qimage is None or self._display is None:

            return QRectF()

        visible_widget_rect = self._visible_image_widget_rect()

        if visible_widget_rect.isEmpty():

            return QRectF()

        scale = self._effective_scale()

        if scale <= 0.0:

            return QRectF()

        center = self._clamped_view_center()

        content_center = self._image_content_rect().center()

        def image_coordinate(widget_x: float, widget_y: float) -> QPointF:

            return QPointF(

                center.x() + ((float(widget_x) - content_center.x()) / scale),

                center.y() + ((float(widget_y) - content_center.y()) / scale),

            )

        top_left = image_coordinate(visible_widget_rect.left(), visible_widget_rect.top())

        bottom_right = image_coordinate(visible_widget_rect.right(), visible_widget_rect.bottom())

        left = min(max(0.0, top_left.x()), float(self._qimage.width()))

        top = min(max(0.0, top_left.y()), float(self._qimage.height()))

        right = min(max(0.0, bottom_right.x()), float(self._qimage.width()))

        bottom = min(max(0.0, bottom_right.y()), float(self._qimage.height()))

        rect = QRectF(left, top, max(0.0, right - left), max(0.0, bottom - top))

        if rect.isEmpty():

            return rect

        margin = max(0.0, float(margin_fraction))

        if margin <= 0.0:

            return rect

        expanded = rect.adjusted(

            -rect.width() * margin,

            -rect.height() * margin,

            rect.width() * margin,

            rect.height() * margin,

        )

        return expanded.intersected(self._image_bounds_rect())

    def set_overlays(
        self,
        overlays: list[ImageOverlay],
        *,
        selection_overlays: list[SelectionOverlay] | None = None,
    ) -> None:

        next_overlays = list(overlays)

        selection_changed = False

        if selection_overlays is not None:

            next_selection = list(selection_overlays)

            selection_changed = next_selection != self._selection_overlays

            self._selection_overlays = next_selection

        if next_overlays == self._overlays and not selection_changed:

            return

        self._overlays = next_overlays

        self.invalidate_static_overlay_cache()

        self.update()

    def set_decoration_overlays(self, decoration_overlays: ImageDecorationOverlays | None) -> None:

        next_overlays = decoration_overlays

        if self._decoration_overlays == next_overlays:

            return

        self._decoration_overlays = next_overlays

        self.update()


    def set_hover_text_formatter(self, formatter: Callable[[float, float], str | None] | None) -> None:

        if formatter is not self._hover_text_formatter:

            self._hover_image_point = None

        self._hover_text_formatter = formatter

    def set_selection_overlays(self, selection_overlays: list[SelectionOverlay] | None) -> None:

        previous_overlays = self._selection_overlays

        next_overlays = list(selection_overlays or [])

        if previous_overlays == next_overlays:

            return

        self._selection_overlays = next_overlays

        dirty_rect = self._selection_overlays_update_rect(previous_overlays, next_overlays)

        if dirty_rect is None:

            self.update()

            return

        if not dirty_rect.isEmpty():

            self.update(dirty_rect)

    def set_gesture_roi_mode(self, *, enabled: bool, shift_required: bool = True) -> None:

        self._gesture_roi_enabled = bool(enabled)

        self._gesture_roi_shift_required = bool(shift_required)

        if not self._gesture_roi_enabled:

            self._gesture_roi_active = False

        if enabled:

            self.setCursor(Qt.CursorShape.CrossCursor)

        else:

            self.unsetCursor()

    def set_interaction_mode(
        self,
        *,
        gesture_roi_enabled: bool = False,
        gesture_roi_shift_required: bool = True,
        direct_edit_enabled: bool = False,
        direct_edit_draw_enabled: bool = False,
        direct_edit_select_enabled: bool = False,
    ) -> None:
        self._direct_edit_enabled = bool(direct_edit_enabled)
        self._direct_edit_draw_enabled = bool(direct_edit_draw_enabled)
        self._direct_edit_select_enabled = bool(direct_edit_select_enabled)
        if not self._direct_edit_enabled:
            self._direct_edit_active = False
        self.set_gesture_roi_mode(
            enabled=bool(gesture_roi_enabled),
            shift_required=bool(gesture_roi_shift_required),
        )

    def _selection_overlays_update_rect(

        self,

        previous_overlays: list[SelectionOverlay],

        next_overlays: list[SelectionOverlay],

    ) -> QRect | None:

        if self._display is None or self._qimage is None:

            return None

        dirty_rect = QRectF()

        has_dirty_rect = False

        for overlay in [*previous_overlays, *next_overlays]:

            overlay_rect = self._selection_overlay_widget_rect(overlay)

            if overlay_rect is None or overlay_rect.isEmpty():

                continue

            dirty_rect = overlay_rect if not has_dirty_rect else dirty_rect.united(overlay_rect)

            has_dirty_rect = True

        if not has_dirty_rect:

            return QRect()

        widget_rect = dirty_rect.intersected(QRectF(self.rect()))

        if widget_rect.isEmpty():

            return QRect()

        return widget_rect.toAlignedRect().adjusted(-2, -2, 2, 2).intersected(self.rect())

    def _selection_overlay_widget_rect(self, overlay: SelectionOverlay) -> QRectF | None:

        scale = self._effective_scale()

        padding = 8.0

        if overlay.shape == "circle":

            radius = float(np.hypot(overlay.x1 - overlay.x0, overlay.y1 - overlay.y0))

            if radius <= 0.5:

                return None

            center = self.image_to_widget(float(overlay.x0), float(overlay.y0))

            widget_radius = (radius * scale) + padding

            return QRectF(

                center.x() - widget_radius,

                center.y() - widget_radius,

                widget_radius * 2.0,

                widget_radius * 2.0,

            )

        if overlay.shape != "rectangle":

            return None

        top_left = self.image_to_widget(min(float(overlay.x0), float(overlay.x1)), min(float(overlay.y0), float(overlay.y1)))

        bottom_right = self.image_to_widget(max(float(overlay.x0), float(overlay.x1)), max(float(overlay.y0), float(overlay.y1)))

        rect = QRectF(top_left, bottom_right).normalized()

        if rect.width() <= 1.0 and rect.height() <= 1.0:

            return None

        return rect.adjusted(-padding, -padding, padding, padding)



    def zoom_in(self) -> None:

        self._zoom_scale = min(32.0, self._zoom_scale * 1.8)

        self.update()

        self.viewportChanged.emit()



    def zoom_out(self) -> None:

        self._zoom_scale = max(1.0, self._zoom_scale / 1.8)

        self.update()

        self.viewportChanged.emit()



    def reset_view(self) -> None:

        self._zoom_scale = 1.0

        self._view_center = self._default_view_center()

        self._pan_anchor = None

        self._pan_center_start = None

        self.update()

        self.viewportChanged.emit()


    def cancel_direct_edit_interaction(self) -> None:

        self._direct_edit_active = False

        self._pan_anchor = None

        self._pan_center_start = None

        self._pan_drag_active = False

        self._pending_overlay_click = None



    def focus_on(self, image_x: float, image_y: float, *, minimum_zoom_scale: float | None = None) -> None:

        self._view_center = QPointF(float(image_x), float(image_y))

        if minimum_zoom_scale is not None:

            self._zoom_scale = max(1.0, max(self._zoom_scale, float(minimum_zoom_scale)))

        self.update()

        self.viewportChanged.emit()



    def capture_view_image(self) -> QImage:

        width = max(1, self.width())

        height = max(1, self.height())

        image = QImage(width, height, QImage.Format.Format_ARGB32)

        image.fill(QColor("black"))

        painter = QPainter(image)

        try:

            self.render(painter, QPoint())

        finally:

            painter.end()

        return image


    def capture_full_resolution_image(self) -> QImage | None:

        if self._qimage is None:

            return None

        image = QImage(self._qimage.width(), self._qimage.height(), QImage.Format.Format_ARGB32)

        image.fill(QColor("black"))

        painter = QPainter(image)

        try:

            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)

            painter.drawImage(QPoint(0, 0), self._qimage)

            self._draw_safe_margin(painter)

            self._draw_selection_overlay(painter)

            self._paint_decoration_overlays(
                painter,
                self._decoration_overlays,
                float(self._qimage.width()),
                float(self._qimage.height()),
            )

            for motion_vector_overlay in self._motion_vector_overlays:

                self._draw_motion_vector_overlay(painter, motion_vector_overlay)

            for grid_overlay in self._grid_overlays:

                self._draw_grid_overlay(painter, grid_overlay)

            painter.save()

            painter.setClipRect(self._image_bounds_rect())

            for overlay in self._overlays:

                self._draw_overlay(painter, overlay)

            painter.restore()

            painter.setPen(QColor("white"))

            for overlay in self._overlays:

                if not overlay.show_label:

                    continue

                painter.save()

                self._apply_overlay_label_style(painter, overlay)

                self._draw_overlay_label_text(
                    painter,
                    self._overlay_label_image_point(overlay, painter),
                    self._overlay_label_text(overlay),
                    outline_color=overlay.text_outline_color,
                    outline_width=overlay.text_outline_width,
                )

                painter.restore()

            for grid_overlay in self._grid_overlays:

                if not grid_overlay.points:

                    continue

                label_x, label_y = grid_overlay.points[0]

                painter.setPen(QColor(grid_overlay.color))

                painter.drawText(QPointF(label_x + 3.0, label_y + 3.0), grid_overlay.label)

        finally:

            painter.end()

        return image



    def composite_info_panel_onto_image(self, base_image: QImage, info_panel: ImageInfoPanel) -> QImage:

        composited = base_image.convertToFormat(QImage.Format.Format_ARGB32)

        if composited is None or composited.isNull():

            composited = base_image.copy()

        painter = QPainter(composited)

        try:

            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

            previous_panel = self._info_panel

            self._info_panel = info_panel

            try:

                self._draw_info_panel_for_bounds(painter, float(composited.width()), float(composited.height()))

            finally:

                self._info_panel = previous_panel

        finally:

            painter.end()

        return composited



    def composite_chart_overlay_onto_image(self, base_image: QImage, overlay_panel: ImageChartOverlayPanel) -> QImage:

        composited = base_image.convertToFormat(QImage.Format.Format_ARGB32)

        if composited is None or composited.isNull():

            composited = base_image.copy()

        painter = QPainter(composited)

        try:

            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

            previous_panel = self._chart_overlay_panel

            self._chart_overlay_panel = overlay_panel

            try:

                image_rect = self._chart_overlay_image_bounds(float(composited.width()), float(composited.height()))

                self._draw_chart_overlay(painter, image_rect)

            finally:

                self._chart_overlay_panel = previous_panel

        finally:

            painter.end()

        return composited



    def composite_decorations_onto_image(self, base_image: QImage, decoration_overlays: ImageDecorationOverlays | None) -> QImage:

        if decoration_overlays is None:

            return base_image

        composited = base_image.convertToFormat(QImage.Format.Format_ARGB32)

        if composited is None or composited.isNull():

            composited = base_image.copy()

        painter = QPainter(composited)

        try:

            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

            self._paint_decoration_overlays(
                painter,
                decoration_overlays,
                float(composited.width()),
                float(composited.height()),
            )

        finally:

            painter.end()

        return composited



    def composite_overlays_onto_image(self, base_image: QImage, overlays: list[ImageOverlay]) -> QImage:

        if not overlays:

            return base_image

        composited = base_image.convertToFormat(QImage.Format.Format_ARGB32)

        if composited is None or composited.isNull():

            composited = base_image.copy()

        painter = QPainter(composited)

        try:

            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

            previous_qimage = self._qimage

            previous_overlays = self._overlays

            self._qimage = composited

            self._overlays = list(overlays)

            try:

                painter.save()

                painter.setClipRect(self._image_bounds_rect())

                for overlay in overlays:

                    self._draw_overlay(painter, overlay)

                painter.restore()

                for overlay in overlays:

                    if not overlay.show_label:

                        continue

                    painter.save()

                    self._apply_overlay_label_style(painter, overlay)

                    self._draw_overlay_label_text(
                    painter,
                    self._overlay_label_image_point(overlay, painter),
                    self._overlay_label_text(overlay),
                    outline_color=overlay.text_outline_color,
                    outline_width=overlay.text_outline_width,
                )

                    painter.restore()

            finally:

                self._qimage = previous_qimage

                self._overlays = previous_overlays

        finally:

            painter.end()

        return composited



    def composite_astrostack_export_frame(
        self,
        base_image: QImage,
        *,
        overlays: list[ImageOverlay] | None,
        chart_overlay_panel: ImageChartOverlayPanel | None,
    ) -> QImage:

        composited = self.composite_overlays_onto_image(base_image, list(overlays or []))

        if chart_overlay_panel is None:

            return composited

        return self.composite_chart_overlay_onto_image(composited, chart_overlay_panel)



    def zoom_at(self, image_x: float, image_y: float, step: float) -> None:

        if self._qimage is None:

            return

        self._view_center = QPointF(image_x, image_y)

        zoom_factor = 1.25 ** step

        self._zoom_scale = min(32.0, max(1.0, self._zoom_scale * zoom_factor))

        self.update()

        self.viewportChanged.emit()

    def _comparison_is_active(self) -> bool:

        return self._comparison_split_enabled

    def _comparison_has_survey_raster(self) -> bool:

        return (

            self._comparison_qimage is not None

            and not self._comparison_qimage.isNull()

            and self._comparison_target_rect is not None

            and not self._comparison_target_rect.isEmpty()

        )

    def _comparison_split_widget_x(self) -> float | None:

        if not self._comparison_is_active():

            return None

        visible_rect = self._visible_image_widget_rect()

        if visible_rect.isEmpty():

            return None

        return visible_rect.left() + (visible_rect.width() * self._comparison_split_fraction)

    def _comparison_split_hit_test(self, point: QPointF) -> bool:

        split_x = self._comparison_split_widget_x()

        visible_rect = self._visible_image_widget_rect()

        return (

            split_x is not None

            and not visible_rect.isEmpty()

            and visible_rect.adjusted(-6.0, 0.0, 6.0, 0.0).contains(point)

            and abs(float(point.x()) - split_x) <= 7.0

        )

    def _update_comparison_split_from_widget_x(self, widget_x: float) -> None:

        visible_rect = self._visible_image_widget_rect()

        if visible_rect.isEmpty() or visible_rect.width() <= 0.0:

            return

        fraction = (float(widget_x) - visible_rect.left()) / visible_rect.width()

        self.set_comparison_split_fraction(fraction)

    def _draw_comparison_divider(self, painter: QPainter) -> None:

        split_x = self._comparison_split_widget_x()

        visible_rect = self._visible_image_widget_rect()

        if split_x is None or visible_rect.isEmpty():

            return

        top = visible_rect.top()

        bottom = visible_rect.bottom()

        painter.save()

        painter.setPen(QPen(QColor(0, 0, 0, 210), 4.0))

        painter.drawLine(QPointF(split_x, top), QPointF(split_x, bottom))

        painter.setPen(QPen(QColor(255, 255, 255, 235), 1.5))

        painter.drawLine(QPointF(split_x, top), QPointF(split_x, bottom))

        handle_center = QPointF(split_x, visible_rect.center().y())

        painter.setBrush(QColor(255, 255, 255, 235))

        painter.setPen(QPen(QColor(0, 0, 0, 220), 1.5))

        painter.drawEllipse(handle_center, 5.0, 14.0)

        painter.restore()

    def _draw_comparison_loading_indicator(self, painter: QPainter) -> None:

        if not self._comparison_loading or not self._comparison_is_active():

            return

        split_x = self._comparison_split_widget_x()

        visible_rect = self._visible_image_widget_rect()

        if split_x is None or visible_rect.isEmpty():

            return

        margin = 12.0

        radius = 10.0

        survey_left = split_x + margin

        survey_right = visible_rect.right() - margin

        if survey_right - survey_left < radius * 2.0:

            return

        center_x = survey_right - radius

        center_y = visible_rect.top() + margin + radius

        arc_rect = QRectF(center_x - radius, center_y - radius, radius * 2.0, radius * 2.0)

        start_angle = int(self._comparison_loading_angle * 16.0)

        span_angle = 270 * 16

        loading_message = self._comparison_loading_message.strip()

        painter.save()

        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        if loading_message:

            message_right = center_x - radius - 8.0

            message_left = survey_left

            if message_right > message_left + 48.0:

                message_rect = QRectF(message_left, center_y - radius, message_right - message_left, radius * 2.0)

                painter.setFont(QFont("Segoe UI", 9))

                painter.setPen(QPen(QColor(0, 0, 0, 180)))

                painter.drawText(message_rect.translated(1.0, 1.0), int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), loading_message)

                painter.setPen(QPen(QColor(255, 255, 255, 235)))

                painter.drawText(message_rect, int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), loading_message)

        painter.setPen(QPen(QColor(0, 0, 0, 150), 4.0))

        painter.setBrush(Qt.BrushStyle.NoBrush)

        painter.drawArc(arc_rect, start_angle, span_angle)

        painter.setPen(QPen(QColor(255, 255, 255, 235), 2.5))

        painter.drawArc(arc_rect, start_angle, span_angle)

        painter.restore()

    def paintEvent(self, _event: object) -> None:

        painter = QPainter(self)

        painter.fillRect(self.rect(), QColor("black"))

        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)



        if self._display is None or self._qimage is None:

            painter.setPen(QColor("white"))

            painter.drawText(self.rect(), int(Qt.AlignmentFlag.AlignCenter), self._message)

            return



        scale = self._effective_scale()

        center = self._clamped_view_center()

        self._view_center = center

        content_rect = self._image_content_rect()

        static_overlay_count = max(0, min(len(self._overlays), self._static_overlay_count))

        static_shape_layer: QImage | None = None

        static_label_layer: QImage | None = None

        if static_overlay_count:

            static_shape_layer, static_label_layer = self._static_overlay_layers(scale, center)

        dynamic_overlays = self._overlays[static_overlay_count:]



        painter.save()

        painter.translate(content_rect.center())

        painter.scale(scale, scale)

        painter.translate(-center.x(), -center.y())

        painter.drawImage(QRectF(0.0, 0.0, self._qimage.width(), self._qimage.height()), self._qimage)

        if self._comparison_has_survey_raster():

            split_widget_x = self._comparison_split_widget_x()

            if split_widget_x is not None and self._comparison_target_rect is not None and self._comparison_qimage is not None:

                split_image_x = center.x() + ((split_widget_x - content_rect.center().x()) / scale)

                painter.save()

                painter.setClipRect(

                    QRectF(

                        split_image_x,

                        0.0,

                        max(0.0, float(self._qimage.width()) - split_image_x),

                        float(self._qimage.height()),

                    )

                )

                painter.drawImage(self._comparison_target_rect, self._comparison_qimage)

                painter.restore()

        self._draw_safe_margin(painter)

        image_clip_rect = self._image_bounds_rect()

        self._draw_selection_overlay(painter)

        self._paint_decoration_overlays(
            painter,
            self._decoration_overlays,
            float(self._qimage.width()),
            float(self._qimage.height()),
        )

        motion_vector_clip_rect = image_clip_rect.adjusted(-48.0, -48.0, 48.0, 48.0)

        for motion_vector_overlay in self._motion_vector_overlays:

            if not self._motion_vector_overlay_intersects_image_rect(motion_vector_overlay, motion_vector_clip_rect):

                continue

            self._draw_motion_vector_overlay(painter, motion_vector_overlay)

        for grid_overlay in self._grid_overlays:

            self._draw_grid_overlay(painter, grid_overlay)

        painter.restore()



        if static_shape_layer is not None:

            painter.drawImage(QPoint(0, 0), static_shape_layer)

        self._draw_overlay_shapes(painter, dynamic_overlays, scale, center)

        if static_label_layer is not None:

            painter.drawImage(QPoint(0, 0), static_label_layer)

        self._draw_overlay_labels(painter, dynamic_overlays, scale)

        for grid_overlay in self._grid_overlays:

            if not grid_overlay.points:

                continue

            label_point = self._grid_overlay_label_widget_point(grid_overlay, painter)

            if label_point is None:

                continue

            painter.setPen(QColor(grid_overlay.color))

            painter.drawText(label_point, grid_overlay.label)

        self._draw_comparison_divider(painter)

        self._draw_comparison_loading_indicator(painter)

        self._draw_hover_text_overlay(painter)

        self._draw_info_panel(painter)

        self._draw_chart_overlay(painter, self._chart_overlay_widget_bounds())


    def _static_overlay_layers(self, scale: float, center: QPointF) -> tuple[QImage | None, QImage | None]:

        static_overlay_count = max(0, min(len(self._overlays), self._static_overlay_count))

        if static_overlay_count <= 0:

            return None, None

        static_overlays = self._overlays[:static_overlay_count]

        if any(overlay.dynamic_metric_kind for overlay in static_overlays):

            return None, None

        cache_key = self._static_overlay_layer_cache_key(static_overlays, scale, center)

        if (

            cache_key == self._static_overlay_cache_key

            and self._static_overlay_shape_cache_image is not None

            and self._static_overlay_label_cache_image is not None

        ):

            return self._static_overlay_shape_cache_image, self._static_overlay_label_cache_image

        shape_layer = self._new_transparent_widget_layer()

        label_layer = self._new_transparent_widget_layer()

        shape_painter = QPainter(shape_layer)

        try:

            shape_painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

            shape_painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)

            self._draw_overlay_shapes(shape_painter, static_overlays, scale, center)

        finally:

            shape_painter.end()

        label_painter = QPainter(label_layer)

        try:

            label_painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

            label_painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)

            self._draw_overlay_labels(label_painter, static_overlays, scale)

        finally:

            label_painter.end()

        self._static_overlay_cache_key = cache_key

        self._static_overlay_shape_cache_image = shape_layer

        self._static_overlay_label_cache_image = label_layer

        return shape_layer, label_layer


    def _static_overlay_layer_cache_key(self, overlays: list[ImageOverlay], scale: float, center: QPointF) -> tuple[object, ...]:

        return (

            self._current_image_key,

            self.width(),

            self.height(),

            round(float(self.devicePixelRatioF()), 4),

            round(float(scale), 6),

            round(float(center.x()), 4),

            round(float(center.y()), 4),

            tuple(
                (
                    overlay.source_id,
                    overlay.name,
                    overlay.dynamic_metric_kind,
                )
                for overlay in overlays
            ),

        )


    def _new_transparent_widget_layer(self) -> QImage:

        device_pixel_ratio = max(1.0, float(self.devicePixelRatioF()))

        image = QImage(

            max(1, int(round(self.width() * device_pixel_ratio))),

            max(1, int(round(self.height() * device_pixel_ratio))),

            QImage.Format.Format_ARGB32_Premultiplied,

        )

        image.setDevicePixelRatio(device_pixel_ratio)

        image.fill(QColor(0, 0, 0, 0))

        return image


    def _draw_overlay_shapes(self, painter: QPainter, overlays: list[ImageOverlay], scale: float, center: QPointF) -> None:

        if not overlays:

            return

        content_rect = self._image_content_rect()

        painter.save()

        painter.translate(content_rect.center())

        painter.scale(scale, scale)

        painter.translate(-center.x(), -center.y())

        painter.save()

        painter.setClipRect(self._image_bounds_rect())

        for overlay in overlays:

            self._draw_overlay(painter, overlay)

        painter.restore()

        painter.restore()


    def _draw_overlay_labels(self, painter: QPainter, overlays: list[ImageOverlay], scale: float) -> None:

        painter.setPen(QColor("white"))

        for overlay in overlays:

            if not overlay.show_label:

                continue

            if not self._overlay_intersects_visible_widget_frame(overlay):

                continue

            painter.save()

            self._apply_overlay_label_style(painter, overlay, scale_factor=(scale if overlay.marker_style == "text" else 1.0))

            label_point = self._overlay_label_widget_point(overlay, painter)

            self._draw_overlay_label_text(
                painter,
                label_point,
                self._overlay_label_text(overlay),
                outline_color=overlay.text_outline_color,
                outline_width=overlay.text_outline_width,
            )

            painter.restore()



    def wheelEvent(self, event: object) -> None:

        if self._handle_info_panel_wheel(event):

            return

        image_point = self.widget_to_image(event.position().x(), event.position().y())

        if image_point is None:

            return

        if self._editor_enabled:

            self.imageWheelAdjusted.emit(image_point.x(), image_point.y(), float(event.angleDelta().y() / 120.0), event.modifiers())

            return



        self._view_center = image_point

        if event.angleDelta().y() > 0:

            self._zoom_scale = min(32.0, self._zoom_scale * 1.25)

        elif event.angleDelta().y() < 0:

            self._zoom_scale = max(1.0, self._zoom_scale / 1.25)

        self.update()

        self.viewportChanged.emit()



    def mousePressEvent(self, event: object) -> None:

        if self._info_panel_rect().contains(event.position()):

            return

        if (

            event.button() == Qt.MouseButton.LeftButton

            and self._comparison_split_hit_test(QPointF(event.position()))

        ):

            self._comparison_split_drag_active = True

            self._pan_anchor = None

            self._pan_center_start = None

            self._pan_drag_active = False

            self._pending_overlay_click = None

            self._update_comparison_split_from_widget_x(event.position().x())

            return

        image_point = self.widget_to_image(event.position().x(), event.position().y())

        if image_point is None:

            return

        if self._direct_edit_enabled and event.button() == Qt.MouseButton.LeftButton:

            modifiers = event.modifiers()

            candidate_overlay = self._overlay_at_image_point(image_point)

            starts_draw_gesture = self._direct_edit_draw_enabled

            starts_selected_overlay_gesture = candidate_overlay is not None and bool(candidate_overlay.show_handles)

            starts_select_gesture = (
                self._direct_edit_select_enabled
                and not self._direct_edit_draw_enabled
                and candidate_overlay is not None
            )

            if starts_draw_gesture or starts_selected_overlay_gesture or starts_select_gesture:

                self._direct_edit_active = True

                self._pan_anchor = None

                self._pan_center_start = None

                self._pan_drag_active = False

                self._pending_overlay_click = None

                self.imagePressed.emit(image_point.x(), image_point.y(), event.button(), modifiers)

                return

        if self._editor_enabled:

            self._pending_overlay_click = None

            self._editor_pending_click_button = event.button()

            if (
                self._editor_drag_enabled
                and event.button() == Qt.MouseButton.LeftButton
                and self._editable_overlay_at_image_point(image_point) is not None
            ):

                self._editor_drag_active = True

                self._pan_anchor = None

                self._pan_center_start = None

                self._pan_drag_active = False

                self.imagePressed.emit(image_point.x(), image_point.y(), event.button(), event.modifiers())

                return

            if event.button() == Qt.MouseButton.LeftButton:

                self._pan_anchor = QPointF(event.position())

                self._pan_center_start = self._clamped_view_center()

                self._pan_drag_active = False

            else:

                self._pan_anchor = None

                self._pan_center_start = None

                self._pan_drag_active = False

            return

        if event.button() == Qt.MouseButton.RightButton:

            global_position = event.globalPosition().toPoint() if hasattr(event, "globalPosition") else self.mapToGlobal(event.position().toPoint())

            self._pan_anchor = None

            self._pan_center_start = None

            self._pan_drag_active = False

            self._pending_overlay_click = None

            self.imageContextRequested.emit(image_point.x(), image_point.y(), global_position, event.modifiers())

            return

        if (

            self._gesture_roi_enabled

            and event.button() == Qt.MouseButton.LeftButton

            and (

                not self._gesture_roi_shift_required

                or bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)

            )

        ):

            self._gesture_roi_active = True

            self._pan_anchor = None

            self._pan_center_start = None

            self._pan_drag_active = False

            self._pending_overlay_click = None

            self.imagePressed.emit(image_point.x(), image_point.y(), event.button(), event.modifiers())

            return

        if event.button() == Qt.MouseButton.LeftButton and bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier):

            self._pan_anchor = None

            self._pan_center_start = None

            self._pan_drag_active = False

            self._pending_overlay_click = None

            self.imagePressed.emit(image_point.x(), image_point.y(), event.button(), event.modifiers())

            return

        if event.button() == Qt.MouseButton.LeftButton:

            self._pan_anchor = QPointF(event.position())

            self._pan_center_start = self._clamped_view_center()

            self._pan_drag_active = False

            self._pending_overlay_click = self._overlay_at_image_point(image_point)


    def _overlay_at_image_point(self, image_point: QPointF) -> ImageOverlay | None:

        best_overlay: ImageOverlay | None = None

        best_score: float | None = None

        for overlay in self._overlays:

            if not overlay.show_marker:

                continue

            score = self._overlay_hit_test_score(overlay, image_point)

            if score is None:

                continue

            if best_score is None or score < best_score:

                best_overlay = overlay

                best_score = score

        return best_overlay


    def _editable_overlay_at_image_point(self, image_point: QPointF) -> ImageOverlay | None:

        candidate = self._overlay_at_image_point(image_point)

        if candidate is not None and candidate.show_handles:

            return candidate

        for overlay in self._overlays:

            if not overlay.show_marker or not overlay.show_handles:

                continue

            center_x = float(overlay.x)

            center_y = float(overlay.y)

            if math.hypot(float(image_point.x()) - center_x, float(image_point.y()) - center_y) <= 6.0:

                return overlay

            radii = (overlay.aperture_radius, overlay.annulus_inner_radius, overlay.annulus_outer_radius) if overlay.show_annulus else (overlay.aperture_radius,)

            for radius in radii:

                if math.hypot(float(image_point.x()) - (center_x + float(radius)), float(image_point.y()) - center_y) <= 6.0:

                    return overlay

        return None


    def _overlay_hit_test_score(self, overlay: ImageOverlay, image_point: QPointF) -> float | None:

        padding = max(3.0, float(overlay.pen_width))

        dx = float(image_point.x()) - float(overlay.x)

        dy = float(image_point.y()) - float(overlay.y)

        if overlay.marker_style == "text":

            if overlay.fixed_label_position:

                hit_rect = self._overlay_text_bounds_in_image_space(
                    overlay,
                    text=self._overlay_label_text(overlay),
                ).adjusted(-padding, -padding, padding, padding)

                if not hit_rect.contains(image_point):

                    return None

                half_width = max(1.0, hit_rect.width() * 0.5)

                half_height = max(1.0, hit_rect.height() * 0.5)

                normalized_dx = (float(image_point.x()) - hit_rect.center().x()) / half_width

                normalized_dy = (float(image_point.y()) - hit_rect.center().y()) / half_height

                return normalized_dx * normalized_dx + normalized_dy * normalized_dy

            hit_rect = QRectF(
                float(overlay.x) - padding,
                float(overlay.y) - padding,
                max(10.0, float(overlay.aperture_radius) * 2.0) + padding * 2.0,
                max(10.0, float(overlay.ellipse_minor_radius) * 2.0 if overlay.ellipse_minor_radius is not None else float(overlay.aperture_radius)) + padding * 2.0,
            )

            if not hit_rect.contains(image_point):

                return None

            half_width = max(1.0, hit_rect.width() * 0.5)

            half_height = max(1.0, hit_rect.height() * 0.5)

            normalized_dx = (float(image_point.x()) - hit_rect.center().x()) / half_width

            normalized_dy = (float(image_point.y()) - hit_rect.center().y()) / half_height

            return normalized_dx * normalized_dx + normalized_dy * normalized_dy

        if overlay.marker_style == "rectangle":

            width = max(1.0, float(overlay.aperture_radius))

            height = max(1.0, float(overlay.ellipse_minor_radius or overlay.aperture_radius))

            hit_rect = QRectF(float(overlay.x), float(overlay.y), width, height).adjusted(-padding, -padding, padding, padding)

            if not hit_rect.contains(image_point):

                return None

            half_width = max(1.0, hit_rect.width() * 0.5)

            half_height = max(1.0, hit_rect.height() * 0.5)

            normalized_dx = (float(image_point.x()) - hit_rect.center().x()) / half_width

            normalized_dy = (float(image_point.y()) - hit_rect.center().y()) / half_height

            return normalized_dx * normalized_dx + normalized_dy * normalized_dy

        if overlay.marker_style == "ellipse" and overlay.ellipse_minor_radius is not None:

            major_radius = max(6.0, float(overlay.aperture_radius) + padding)

            minor_radius = max(6.0, float(overlay.ellipse_minor_radius) + padding)

            rotation_radians = math.radians(float(overlay.rotation_degrees))

            cos_rotation = math.cos(rotation_radians)

            sin_rotation = math.sin(rotation_radians)

            ellipse_x = dx * cos_rotation + dy * sin_rotation

            ellipse_y = -dx * sin_rotation + dy * cos_rotation

            score = (ellipse_x * ellipse_x) / (major_radius * major_radius) + (ellipse_y * ellipse_y) / (minor_radius * minor_radius)

            if score > 1.0:

                return None

            return score

        hit_radius = max(6.0, float(overlay.aperture_radius) + padding)

        distance_squared = dx * dx + dy * dy

        if distance_squared > hit_radius * hit_radius:

            return None

        return distance_squared / max(1.0, hit_radius * hit_radius)



    def mouseMoveEvent(self, event: object) -> None:

        if self._comparison_split_drag_active:

            if bool(event.buttons() & Qt.MouseButton.LeftButton):

                self._update_comparison_split_from_widget_x(event.position().x())

            return

        if self._info_panel_rect().contains(event.position()):

            self._set_hover_image_point(None)

            return

        image_point = self.widget_to_image(event.position().x(), event.position().y())

        self._set_hover_image_point(image_point)

        if self._direct_edit_active:

            if image_point is not None and bool(event.buttons() & Qt.MouseButton.LeftButton):

                self.imageMoved.emit(image_point.x(), image_point.y(), event.buttons(), event.modifiers())

            return

        if self._editor_enabled:

            if self._editor_drag_active:

                if image_point is not None and bool(event.buttons() & Qt.MouseButton.LeftButton):

                    self.imageMoved.emit(image_point.x(), image_point.y(), event.buttons(), event.modifiers())

                return

            if bool(event.buttons() & Qt.MouseButton.LeftButton):

                self._update_pan_from_mouse_event(event)

            return

        if self._gesture_roi_active:

            if image_point is not None:

                self.imageMoved.emit(image_point.x(), image_point.y(), event.buttons(), event.modifiers())

            return

        if self._pan_anchor is None or self._pan_center_start is None or image_point is None:

            return

        self._update_pan_from_mouse_event(event)


    def _update_pan_from_mouse_event(self, event: object) -> None:

        if self._pan_anchor is None or self._pan_center_start is None:

            return

        drag_distance = math.hypot(event.position().x() - self._pan_anchor.x(), event.position().y() - self._pan_anchor.y())

        if not self._pan_drag_active and drag_distance < 4.0:

            return

        self._pan_drag_active = True

        scale = self._effective_scale()

        delta_x = (event.position().x() - self._pan_anchor.x()) / scale

        delta_y = (event.position().y() - self._pan_anchor.y()) / scale

        self._view_center = QPointF(self._pan_center_start.x() - delta_x, self._pan_center_start.y() - delta_y)

        self.update()

        self.viewportChanged.emit()


    def leaveEvent(self, _event: object) -> None:

        self._set_hover_image_point(None)

    def resizeEvent(self, event: object) -> None:

        super().resizeEvent(event)

        self.viewportChanged.emit()


    def _set_hover_image_point(self, image_point: QPointF | None) -> None:

        if self._hover_text_formatter is None:

            image_point = None

        previous_point = self._hover_image_point

        if previous_point is None and image_point is None:

            return

        if previous_point is not None and image_point is not None:

            if abs(previous_point.x() - image_point.x()) < 0.25 and abs(previous_point.y() - image_point.y()) < 0.25:

                return

        self._hover_image_point = image_point

        self.update()



    def mouseReleaseEvent(self, event: object) -> None:

        if self._comparison_split_drag_active:

            self._comparison_split_drag_active = False

            return

        if self._direct_edit_active:

            self._direct_edit_active = False

            self._pan_anchor = None

            self._pan_center_start = None

            self._pan_drag_active = False

            self._pending_overlay_click = None

            self.imageReleased.emit(event.button(), event.modifiers())

            return

        if self._editor_enabled:

            image_point = self.widget_to_image(event.position().x(), event.position().y())

            if self._editor_drag_active:

                self._editor_drag_active = False

                self._pan_anchor = None

                self._pan_center_start = None

                self._pan_drag_active = False

                self._editor_pending_click_button = None

                self.imageReleased.emit(event.button(), event.modifiers())

                return

            pending_button = self._editor_pending_click_button

            was_drag = self._pan_drag_active

            self._pan_anchor = None

            self._pan_center_start = None

            self._pan_drag_active = False

            self._editor_pending_click_button = None

            if pending_button == event.button() and not was_drag and image_point is not None:

                self.imagePressed.emit(image_point.x(), image_point.y(), event.button(), event.modifiers())

            self.imageReleased.emit(event.button(), event.modifiers())

            return

        if self._gesture_roi_active:

            self._gesture_roi_active = False

            self.imageReleased.emit(event.button(), event.modifiers())

            return

        if (
            event.button() == Qt.MouseButton.LeftButton
            and not self._pan_drag_active
            and self._pending_overlay_click is not None
        ):

            self.imageOverlayClicked.emit(self._pending_overlay_click)

        self._pan_anchor = None

        self._pan_center_start = None

        self._pan_drag_active = False

        self._pending_overlay_click = None



    def widget_to_image(self, widget_x: float, widget_y: float) -> QPointF | None:

        if self._qimage is None or self._display is None:

            return None

        if not self._image_content_rect().contains(widget_x, widget_y):

            return None

        scale = self._effective_scale()

        center = self._clamped_view_center()

        content_rect = self._image_content_rect()

        image_x = center.x() + ((widget_x - content_rect.center().x()) / scale)

        image_y = center.y() + ((widget_y - content_rect.center().y()) / scale)

        if not (0.0 <= image_x < self._qimage.width() and 0.0 <= image_y < self._qimage.height()):

            return None

        return QPointF(image_x, image_y)



    def image_to_widget(self, image_x: float, image_y: float) -> QPointF:

        scale = self._effective_scale()

        center = self._clamped_view_center()

        content_rect = self._image_content_rect()

        widget_x = content_rect.center().x() + ((image_x - center.x()) * scale)

        widget_y = content_rect.center().y() + ((image_y - center.y()) * scale)

        return QPointF(widget_x, widget_y)



    def _display_to_qimage(self, display: AnnotatedImageDisplay, render_settings: AnnotatedImageRenderSettings) -> QImage:

        rendered = self._coerce_rendered_image_data(render_annotated_image(display, render_settings))

        if rendered.ndim == 2:

            height, width = rendered.shape

            return QImage(rendered.data, width, height, rendered.strides[0], QImage.Format.Format_Grayscale8).copy()

        if rendered.ndim == 3 and rendered.shape[2] == 3:

            height, width, _channels = rendered.shape

            return QImage(rendered.data, width, height, rendered.strides[0], QImage.Format.Format_RGB888).copy()

        raise ValueError("Rendered annotated image must be grayscale or RGB.")


    @staticmethod
    def _coerce_rendered_image_data(rendered: np.ndarray) -> np.ndarray:

        image = np.asarray(rendered)

        if image.ndim == 2:

            return np.ascontiguousarray(image)

        if image.ndim != 3:

            return image

        if image.shape[-1] == 1:

            return np.ascontiguousarray(image[..., 0])

        if image.shape[-1] in {3, 4}:

            return np.ascontiguousarray(image[..., :3])

        if image.shape[0] == 1:

            return np.ascontiguousarray(image[0])

        if image.shape[0] in {3, 4}:

            return np.ascontiguousarray(np.moveaxis(image[:3], 0, -1))

        return image



    def _draw_overlay(self, painter: QPainter, overlay: ImageOverlay) -> None:

        if not overlay.show_marker:

            return

        if overlay.marker_style == "text":

            return

        color = QColor(overlay.color)

        if color.isValid():

            color.setAlphaF(max(0.0, min(1.0, float(overlay.stroke_opacity))))

        pen_width = max(0.0, float(overlay.pen_width))

        pen = QPen(color, pen_width)

        pen.setCosmetic(True)

        painter.setBrush(Qt.BrushStyle.NoBrush)

        outline_color = QColor(overlay.outline_color) if overlay.outline_color else QColor()

        outline_width = max(0.0, float(overlay.outline_width))

        outline_pen: QPen | None = None

        fill_brush_color: QColor | None = None

        if overlay.outline_color and outline_color.isValid() and outline_width > 0.0:

            outline_pen = QPen(outline_color, max(1.0, pen_width + outline_width))

            outline_pen.setCosmetic(True)

        if overlay.fill_color:

            candidate_fill_color = QColor(overlay.fill_color)

            if candidate_fill_color.isValid():

                candidate_fill_color.setAlphaF(max(0.0, min(1.0, float(overlay.fill_opacity))))

                fill_brush_color = candidate_fill_color

        if overlay.marker_style == "cross":

            arm = max(4.0, min(overlay.aperture_radius, 10.0))

            if outline_pen is not None:

                painter.setPen(outline_pen)

                painter.drawLine(QPointF(overlay.x - arm, overlay.y - arm), QPointF(overlay.x + arm, overlay.y + arm))

                painter.drawLine(QPointF(overlay.x - arm, overlay.y + arm), QPointF(overlay.x + arm, overlay.y - arm))

                painter.drawEllipse(QPointF(overlay.x, overlay.y), max(1.4, 0.26 * arm), max(1.4, 0.26 * arm))

            painter.setPen(pen)

            painter.drawLine(QPointF(overlay.x - arm, overlay.y - arm), QPointF(overlay.x + arm, overlay.y + arm))

            painter.drawLine(QPointF(overlay.x - arm, overlay.y + arm), QPointF(overlay.x + arm, overlay.y - arm))

            if overlay.accent_color:

                accent_pen = QPen(QColor(overlay.accent_color), max(0.5, float(overlay.pen_width)))

                accent_pen.setCosmetic(True)

                painter.setPen(accent_pen)

                painter.drawEllipse(QPointF(overlay.x, overlay.y), max(1.3, 0.22 * arm), max(1.3, 0.22 * arm))

            elif fill_brush_color is not None:

                painter.setBrush(fill_brush_color)

                painter.setPen(QPen(color, max(0.5, float(overlay.pen_width))))

                painter.drawEllipse(QPointF(overlay.x, overlay.y), max(1.3, 0.22 * arm), max(1.3, 0.22 * arm))

            else:

                painter.drawEllipse(QPointF(overlay.x, overlay.y), 0.9, 0.9)

            return

        if overlay.marker_style == "target":

            half_box = max(4.0, float(overlay.aperture_radius))
            image_bounds = self._image_bounds_rect()
            left_bound = image_bounds.left() + 2.0
            right_bound = image_bounds.right() - 2.0
            top_bound = image_bounds.top() + 2.0
            bottom_bound = image_bounds.bottom() - 2.0
            left = max(left_bound, overlay.x - half_box)
            right = min(right_bound, overlay.x + half_box)
            top = max(top_bound, overlay.y - half_box)
            bottom = min(bottom_bound, overlay.y + half_box)
            if right <= left:
                right = left + 1.0
            if bottom <= top:
                bottom = top + 1.0
            horizontal_gap = max(2.0, min(10.0, half_box * 0.35))
            vertical_gap = max(2.0, min(10.0, half_box * 0.35))

            def draw_target_shape(active_painter: QPainter) -> None:
                active_painter.drawRect(QRectF(left, top, right - left, bottom - top))
                active_painter.drawLine(QPointF(left_bound, overlay.y), QPointF(max(left_bound, left - horizontal_gap), overlay.y))
                active_painter.drawLine(QPointF(min(right_bound, right + horizontal_gap), overlay.y), QPointF(right_bound, overlay.y))
                active_painter.drawLine(QPointF(overlay.x, top_bound), QPointF(overlay.x, max(top_bound, top - vertical_gap)))
                active_painter.drawLine(QPointF(overlay.x, min(bottom_bound, bottom + vertical_gap)), QPointF(overlay.x, bottom_bound))

            if outline_pen is not None:
                painter.setPen(outline_pen)
                draw_target_shape(painter)
            painter.setPen(pen)
            draw_target_shape(painter)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            return

        if overlay.marker_style == "rectangle":

            width = max(1.0, float(overlay.aperture_radius))

            height = max(1.0, float(overlay.ellipse_minor_radius or overlay.aperture_radius))

            rect = QRectF(float(overlay.x), float(overlay.y), width, height)

            if outline_pen is not None:

                painter.setPen(outline_pen)

                painter.setBrush(Qt.BrushStyle.NoBrush)

                painter.drawRect(rect)

            painter.setPen(pen)

            painter.setBrush(fill_brush_color if fill_brush_color is not None else Qt.BrushStyle.NoBrush)

            painter.drawRect(rect)

            painter.setBrush(Qt.BrushStyle.NoBrush)

            if overlay.show_handles:

                handle_color = QColor(overlay.accent_color or overlay.color)

                handle_pen = QPen(handle_color, max(1.0, float(overlay.pen_width)))

                handle_pen.setCosmetic(True)

                painter.setPen(handle_pen)

                painter.setBrush(handle_color)

                for corner in (
                    QPointF(rect.left(), rect.top()),
                    QPointF(rect.right(), rect.top()),
                    QPointF(rect.left(), rect.bottom()),
                    QPointF(rect.right(), rect.bottom()),
                ):

                    painter.drawEllipse(corner, 1.4, 1.4)

            return

        if overlay.marker_style == "plot":

            width = max(1.0, float(overlay.aperture_radius))

            height = max(1.0, float(overlay.ellipse_minor_radius or overlay.aperture_radius))

            rect = QRectF(float(overlay.x), float(overlay.y), width, height)

            if overlay.chart_overlay_panel is not None:

                self._draw_chart_overlay_panel(
                    painter,
                    overlay.chart_overlay_panel,
                    rect,
                    include_stack_status=bool(overlay.plot_include_stack_status),
                    stroke_color=overlay.color or "#3a3a3a",
                    fill_color=overlay.fill_color or "#121212",
                    fill_opacity=max(0.0, min(1.0, float(overlay.fill_opacity))),
                    stroke_opacity=max(0.0, min(1.0, float(overlay.stroke_opacity))),
                    plot_style=overlay.plot_style,
                )

            elif overlay.line_chart is not None:

                self._draw_chart_overlay_panel(
                    painter,
                    ImageChartOverlayPanel(
                        title=str(overlay.plot_title or overlay.name or "Plot").strip() or "Plot",
                        line_chart=overlay.line_chart,
                        integration_text="",
                        frame_text="",
                    ),
                    rect,
                    include_stack_status=False,
                    stroke_color=overlay.color or "#3a3a3a",
                    fill_color=overlay.fill_color or "#121212",
                    fill_opacity=max(0.0, min(1.0, float(overlay.fill_opacity))),
                    stroke_opacity=max(0.0, min(1.0, float(overlay.stroke_opacity))),
                    plot_style=overlay.plot_style,
                )

            if overlay.show_handles:

                handle_color = QColor(overlay.accent_color or "#3d8bfd")

                handle_pen = QPen(handle_color, 1.0)

                handle_pen.setCosmetic(True)

                painter.setPen(handle_pen)

                painter.setBrush(handle_color)

                for corner in (
                    QPointF(rect.left(), rect.top()),
                    QPointF(rect.right(), rect.top()),
                    QPointF(rect.left(), rect.bottom()),
                    QPointF(rect.right(), rect.bottom()),
                ):

                    painter.drawEllipse(corner, 1.4, 1.4)

            return

        if overlay.marker_style == "ellipse":

            minor_radius = overlay.ellipse_minor_radius if overlay.ellipse_minor_radius is not None else overlay.aperture_radius

            minor_radius = max(1.0, float(minor_radius))

            if outline_pen is not None:

                painter.setPen(outline_pen)

                painter.setBrush(Qt.BrushStyle.NoBrush)

                self._draw_rotated_ellipse(

                    painter,

                    center_x=overlay.x,

                    center_y=overlay.y,

                    major_radius=overlay.aperture_radius,

                    minor_radius=minor_radius,

                    rotation_degrees=overlay.rotation_degrees,

                )

            painter.setPen(pen)

            painter.setBrush(fill_brush_color if fill_brush_color is not None else Qt.BrushStyle.NoBrush)

            self._draw_rotated_ellipse(

                painter,

                center_x=overlay.x,

                center_y=overlay.y,

                major_radius=overlay.aperture_radius,

                minor_radius=minor_radius,

                rotation_degrees=overlay.rotation_degrees,

            )

            painter.setBrush(Qt.BrushStyle.NoBrush)

            if overlay.show_center_dot:

                if outline_pen is not None:

                    painter.setBrush(outline_color)

                    painter.setPen(Qt.PenStyle.NoPen)

                    painter.drawEllipse(QPointF(overlay.x, overlay.y), 1.3, 1.3)

                painter.setBrush(color)

                painter.setPen(QPen(QColor("black"), 0.0))

                painter.drawEllipse(QPointF(overlay.x, overlay.y), 0.9, 0.9)

            if overlay.show_handles:

                handle_color = QColor(overlay.accent_color or overlay.color)

                handle_pen = QPen(handle_color, max(1.0, float(overlay.pen_width)))

                handle_pen.setCosmetic(True)

                painter.setPen(handle_pen)

                painter.setBrush(handle_color)

                rotation_radians = math.radians(float(overlay.rotation_degrees))

                cos_rotation = math.cos(rotation_radians)

                sin_rotation = math.sin(rotation_radians)

                major_radius = max(1.0, float(overlay.aperture_radius))

                minor_radius = max(1.0, float(minor_radius))

                handle_points = (

                    QPointF(overlay.x + major_radius * cos_rotation, overlay.y + major_radius * sin_rotation),

                    QPointF(overlay.x - major_radius * cos_rotation, overlay.y - major_radius * sin_rotation),

                    QPointF(overlay.x - minor_radius * sin_rotation, overlay.y + minor_radius * cos_rotation),

                    QPointF(overlay.x + minor_radius * sin_rotation, overlay.y - minor_radius * cos_rotation),

                )

                for handle_point in handle_points:

                    painter.drawEllipse(handle_point, 1.4, 1.4)

                rotation_handle_distance = major_radius + max(10.0, min(28.0, major_radius * 0.35))

                major_edge_point = QPointF(overlay.x + major_radius * cos_rotation, overlay.y + major_radius * sin_rotation)

                rotation_handle_point = QPointF(

                    overlay.x + rotation_handle_distance * cos_rotation,

                    overlay.y + rotation_handle_distance * sin_rotation,

                )

                painter.setBrush(Qt.BrushStyle.NoBrush)

                painter.drawLine(major_edge_point, rotation_handle_point)

                painter.setBrush(handle_color)

                painter.drawEllipse(rotation_handle_point, 1.7, 1.7)

            return

        if outline_pen is not None:

            painter.setPen(outline_pen)

            painter.drawEllipse(QPointF(overlay.x, overlay.y), overlay.aperture_radius, overlay.aperture_radius)

            if overlay.show_annulus:

                painter.drawEllipse(QPointF(overlay.x, overlay.y), overlay.annulus_inner_radius, overlay.annulus_inner_radius)

                painter.drawEllipse(QPointF(overlay.x, overlay.y), overlay.annulus_outer_radius, overlay.annulus_outer_radius)

        painter.setPen(pen)

        painter.setBrush(fill_brush_color if fill_brush_color is not None else Qt.BrushStyle.NoBrush)

        painter.drawEllipse(QPointF(overlay.x, overlay.y), overlay.aperture_radius, overlay.aperture_radius)

        painter.setBrush(Qt.BrushStyle.NoBrush)

        if overlay.show_annulus:

            annulus_pen = QPen(color, max(0.0, float(overlay.pen_width)))

            annulus_pen.setCosmetic(True)

            painter.setPen(annulus_pen)

            painter.drawEllipse(QPointF(overlay.x, overlay.y), overlay.annulus_inner_radius, overlay.annulus_inner_radius)

            painter.drawEllipse(QPointF(overlay.x, overlay.y), overlay.annulus_outer_radius, overlay.annulus_outer_radius)

        if overlay.show_center_dot:

            if outline_pen is not None:

                painter.setBrush(outline_color)

                painter.setPen(Qt.PenStyle.NoPen)

                painter.drawEllipse(QPointF(overlay.x, overlay.y), 1.3, 1.3)

            painter.setBrush(color)

            painter.setPen(QPen(QColor("black"), 0.0))

            painter.drawEllipse(QPointF(overlay.x, overlay.y), 0.9, 0.9)

        if overlay.show_handles:

            handle_radii = (overlay.aperture_radius, overlay.annulus_inner_radius, overlay.annulus_outer_radius) if overlay.show_annulus else (overlay.aperture_radius,)

            for radius in handle_radii:

                painter.drawEllipse(QPointF(overlay.x + radius, overlay.y), 0.8, 0.8)


    def _apply_overlay_label_style(self, painter: QPainter, overlay: ImageOverlay, *, scale_factor: float = 1.0) -> None:

        text_color = QColor(overlay.text_color or "white")

        if text_color.isValid():

            text_color.setAlphaF(max(0.0, min(1.0, float(overlay.text_opacity))))

        painter.setPen(text_color)

        effective_scale = max(0.05, float(scale_factor)) if overlay.marker_style == "text" else 1.0

        if overlay.text_font is not None:

            font = QFont(overlay.text_font)

            if overlay.marker_style == "text":

                base_size = font.pointSizeF()

                if base_size <= 0.0:

                    base_size = float(font.pointSize()) if font.pointSize() > 0 else 12.0

                font.setPointSizeF(max(1.0, base_size * effective_scale))

            painter.setFont(font)

            return

        if overlay.text_size is not None:

            font = painter.font()

            font.setPointSizeF(max(1.0, float(overlay.text_size) * effective_scale))

            painter.setFont(font)


    def _overlay_label_image_point(self, overlay: ImageOverlay, painter: QPainter) -> QPointF:

        return self._overlay_label_point_near_marker(

            overlay,

            frame_rect=self._image_bounds_rect(),

            painter=painter,

            text=self._overlay_label_text(overlay),

            map_image_point=lambda point: point,

        )


    def _overlay_label_widget_point(self, overlay: ImageOverlay, painter: QPainter) -> QPointF:

        return self._overlay_label_point_near_marker(

            overlay,

            frame_rect=self._visible_image_widget_rect(),

            painter=painter,

            text=self._overlay_label_text(overlay),

            map_image_point=lambda point: self.image_to_widget(point.x(), point.y()),

        )


    def _overlay_label_point_near_marker(

        self,

        overlay: ImageOverlay,

        *,

        frame_rect: QRectF,

        painter: QPainter,

        text: str,

        map_image_point: Callable[[QPointF], QPointF],

    ) -> QPointF:

        if frame_rect.isEmpty():

            return map_image_point(QPointF(float(overlay.x), float(overlay.y)))

        center = QPointF(float(overlay.x), float(overlay.y))

        if overlay.marker_style == "text":

            anchor = map_image_point(center)

            if overlay.fixed_label_position:

                return anchor

            return self._clamped_label_point(

                map_image_point(center),

                frame_rect=frame_rect,

                painter=painter,

                text=text,

                text_bounds=self._overlay_label_text_bounds(painter, text),

            )

        radius = max(0.0, float(overlay.aperture_radius))

        gap = max(3.0, min(18.0, radius * 0.04))

        text_bounds = self._overlay_label_text_bounds(painter, text)

        preferred_radius = self._overlay_label_edge_distance(overlay, 45.0)

        preferred_point = self._overlay_label_candidate_point(center, preferred_radius, gap, 45.0)

        mapped_preferred_point = map_image_point(preferred_point)

        label_point = self._clamped_label_point(

            mapped_preferred_point,

            frame_rect=frame_rect,

            painter=painter,

            text=text,

            text_bounds=text_bounds,

        )

        if math.hypot(label_point.x() - mapped_preferred_point.x(), label_point.y() - mapped_preferred_point.y()) <= 0.5:

            return label_point

        fallback_point = self._clamped_label_point(

            map_image_point(QPointF(center.x() + preferred_radius + gap, center.y() + preferred_radius + gap)),

            frame_rect=frame_rect,

            painter=painter,

            text=text,

            text_bounds=text_bounds,

        )

        best_point = fallback_point

        best_score: float | None = None

        for angle_degrees in _OVERLAY_LABEL_CANDIDATE_ANGLES:

            direction_x, direction_y = self._overlay_label_candidate_direction(angle_degrees)

            edge_distance = self._overlay_label_edge_distance(overlay, angle_degrees)

            edge_point = QPointF(

                center.x() + direction_x * edge_distance,

                center.y() + direction_y * edge_distance,

            )

            preferred_label_point = QPointF(

                center.x() + direction_x * (edge_distance + gap),

                center.y() + direction_y * (edge_distance + gap),

            )

            mapped_edge_point = map_image_point(edge_point)

            mapped_label_point = map_image_point(preferred_label_point)

            label_point = self._clamped_label_point(

                mapped_label_point,

                frame_rect=frame_rect,

                painter=painter,

                text=text,

                text_bounds=text_bounds,

            )

            label_rect = self._label_rect_at(label_point, painter, text, text_bounds=text_bounds)

            angle_penalty = self._angle_distance_degrees(angle_degrees, 45.0) * 0.04

            clamp_distance = math.hypot(

                label_point.x() - mapped_label_point.x(),

                label_point.y() - mapped_label_point.y(),

            )

            score = (

                self._point_to_rect_distance(mapped_edge_point, label_rect)

                + clamp_distance * 0.15

                + angle_penalty

            )

            if best_score is None or score < best_score:

                best_score = score

                best_point = label_point

        return best_point


    def _overlay_label_candidate_point(self, center: QPointF, radius: float, gap: float, angle_degrees: float) -> QPointF:

        direction_x, direction_y = self._overlay_label_candidate_direction(angle_degrees)

        return QPointF(center.x() + direction_x * (radius + gap), center.y() + direction_y * (radius + gap))


    def _overlay_label_edge_distance(self, overlay: ImageOverlay, angle_degrees: float) -> float:

        major_radius = max(0.0, float(overlay.aperture_radius))

        if overlay.marker_style != "ellipse" or overlay.ellipse_minor_radius is None:

            return major_radius

        minor_radius = max(0.0, float(overlay.ellipse_minor_radius))

        if major_radius <= 0.0 or minor_radius <= 0.0:

            return major_radius

        direction_x, direction_y = self._overlay_label_candidate_direction(angle_degrees)

        rotation_radians = math.radians(float(overlay.rotation_degrees))

        cos_rotation = math.cos(rotation_radians)

        sin_rotation = math.sin(rotation_radians)

        ellipse_direction_x = direction_x * cos_rotation + direction_y * sin_rotation

        ellipse_direction_y = -direction_x * sin_rotation + direction_y * cos_rotation

        denominator = (

            (ellipse_direction_x * ellipse_direction_x) / (major_radius * major_radius)

            + (ellipse_direction_y * ellipse_direction_y) / (minor_radius * minor_radius)

        )

        if denominator <= 0.0:

            return major_radius

        return 1.0 / math.sqrt(denominator)


    def _overlay_label_candidate_direction(self, angle_degrees: float) -> tuple[float, float]:

        angle_radians = math.radians(angle_degrees)

        return math.cos(angle_radians), math.sin(angle_radians)


    def _label_rect_at(self, point: QPointF, painter: QPainter, text: str, *, text_bounds: QRectF | None = None) -> QRectF:

        text_bounds = QRectF(text_bounds) if text_bounds is not None else self._overlay_label_text_bounds(painter, text)

        text_bounds.translate(point)

        return text_bounds


    def _overlay_label_text_bounds(self, painter: QPainter, text: str) -> QRectF:

        metrics = painter.fontMetrics()

        lines = str(text or "").splitlines() or [""]

        line_spacing = max(1, metrics.lineSpacing())

        bounds = QRectF()

        for line_index, line in enumerate(lines):

            line_bounds = QRectF(metrics.boundingRect(line))

            line_bounds.translate(0.0, float(line_index * line_spacing))

            bounds = line_bounds if bounds.isNull() else bounds.united(line_bounds)

        return bounds



    @classmethod
    def _overlay_text_bounds_in_image_space(cls, overlay: ImageOverlay, *, text: str | None = None) -> QRectF:

        font = QFont(overlay.text_font) if overlay.text_font is not None else QFont()

        if overlay.text_size is not None:

            font.setPointSizeF(max(6.0, float(overlay.text_size)))

        metrics = QFontMetricsF(font)

        lines = str(text if text is not None else overlay.name or "").splitlines() or [""]

        line_spacing = max(1.0, metrics.lineSpacing())

        bounds = QRectF()

        for line_index, line in enumerate(lines):

            line_bounds = QRectF(metrics.boundingRect(line))

            line_bounds.translate(0.0, float(line_index * line_spacing))

            bounds = line_bounds if bounds.isNull() else bounds.united(line_bounds)

        return bounds.translated(float(overlay.x), float(overlay.y))


    def _draw_overlay_label_text(
        self,
        painter: QPainter,
        point: QPointF,
        text: str,
        *,
        outline_color: str | None = None,
        outline_width: float = 0.0,
    ) -> None:

        lines = str(text or "").splitlines() or [""]

        line_spacing = max(1, painter.fontMetrics().lineSpacing())

        resolved_outline_width = max(0.0, float(outline_width))

        outline_qcolor = QColor(outline_color) if outline_color else QColor()

        use_outline = resolved_outline_width > 0.0 and outline_qcolor.isValid()

        for line_index, line in enumerate(lines):

            line_point = QPointF(point.x(), point.y() + float(line_index * line_spacing))

            if use_outline:

                path = QPainterPath()

                path.addText(line_point, painter.font(), line)

                outline_pen = QPen(outline_qcolor)

                outline_pen.setWidthF(max(0.5, resolved_outline_width))

                outline_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)

                painter.strokePath(path, outline_pen)

            painter.drawText(line_point, line)


    def _point_to_rect_distance(self, point: QPointF, rect: QRectF) -> float:

        distance_x = max(rect.left() - point.x(), 0.0, point.x() - rect.right())

        distance_y = max(rect.top() - point.y(), 0.0, point.y() - rect.bottom())

        return math.hypot(distance_x, distance_y)


    def _angle_distance_degrees(self, first: float, second: float) -> float:

        delta = abs((float(first) - float(second) + 180.0) % 360.0 - 180.0)

        return delta


    def _clamped_label_point(

        self,

        point: QPointF,

        *,

        frame_rect: QRectF,

        painter: QPainter,

        text: str,

        text_bounds: QRectF | None = None,

    ) -> QPointF:

        if frame_rect.isEmpty():

            return point

        margin = 3.0

        text_bounds = QRectF(text_bounds) if text_bounds is not None else self._overlay_label_text_bounds(painter, text)

        min_x = frame_rect.left() + margin - text_bounds.left()

        max_x = frame_rect.right() - margin - text_bounds.right()

        min_y = frame_rect.top() + margin - text_bounds.top()

        max_y = frame_rect.bottom() - margin - text_bounds.bottom()

        if max_x < min_x:

            min_x = max_x = frame_rect.left() + margin

        if max_y < min_y:

            min_y = max_y = frame_rect.bottom() - margin

        return QPointF(

            min(max(float(point.x()), min_x), max_x),

            min(max(float(point.y()), min_y), max_y),

        )


    def _image_bounds_rect(self) -> QRectF:

        if self._qimage is None:

            return QRectF()

        return QRectF(0.0, 0.0, float(self._qimage.width()), float(self._qimage.height()))


    def _visible_image_widget_rect(self) -> QRectF:

        if self._qimage is None:

            return QRectF()

        top_left = self.image_to_widget(0.0, 0.0)

        bottom_right = self.image_to_widget(float(self._qimage.width()), float(self._qimage.height()))

        image_rect = QRectF(top_left, bottom_right).normalized()

        visible_rect = image_rect.intersected(self._image_content_rect())

        return visible_rect if not visible_rect.isEmpty() else image_rect


    def _overlay_intersects_visible_widget_frame(self, overlay: ImageOverlay) -> bool:

        visible_rect = self._visible_image_widget_rect()

        if visible_rect.isEmpty():

            return False

        center_point = self.image_to_widget(float(overlay.x), float(overlay.y))

        edge_point = self.image_to_widget(float(overlay.x) + float(overlay.aperture_radius), float(overlay.y))

        widget_radius = math.hypot(edge_point.x() - center_point.x(), edge_point.y() - center_point.y())

        minor_radius = float(overlay.ellipse_minor_radius) if overlay.ellipse_minor_radius is not None else float(overlay.aperture_radius)

        minor_edge_point = self.image_to_widget(float(overlay.x), float(overlay.y) + minor_radius)

        widget_minor_radius = math.hypot(minor_edge_point.x() - center_point.x(), minor_edge_point.y() - center_point.y())

        bounding_radius = max(widget_radius, widget_minor_radius, 1.5)

        overlay_rect = QRectF(

            center_point.x() - bounding_radius,

            center_point.y() - bounding_radius,

            bounding_radius * 2.0,

            bounding_radius * 2.0,

        )

        return overlay_rect.intersects(visible_rect)


    def _draw_rotated_ellipse(

        self,

        painter: QPainter,

        *,

        center_x: float,

        center_y: float,

        major_radius: float,

        minor_radius: float,

        rotation_degrees: float,

    ) -> None:

        painter.save()

        painter.translate(float(center_x), float(center_y))

        painter.rotate(float(rotation_degrees))

        painter.drawEllipse(QRectF(-float(major_radius), -float(minor_radius), float(major_radius) * 2.0, float(minor_radius) * 2.0))

        painter.restore()



    def _draw_safe_margin(self, painter: QPainter) -> None:

        if self._qimage is None or self._safe_margin_fraction <= 0:

            return

        margin_x = self._qimage.width() * self._safe_margin_fraction

        margin_y = self._qimage.height() * self._safe_margin_fraction

        safe_rect = QRectF(

            margin_x,

            margin_y,

            self._qimage.width() - (2.0 * margin_x),

            self._qimage.height() - (2.0 * margin_y),

        )

        if safe_rect.width() <= 0 or safe_rect.height() <= 0:

            return

        safe_pen = QPen(QColor("deepskyblue"), 0.0)

        safe_pen.setStyle(Qt.PenStyle.DashLine)

        painter.setPen(safe_pen)

        painter.setBrush(Qt.BrushStyle.NoBrush)

        painter.drawRect(safe_rect)



    def _draw_grid_overlay(self, painter: QPainter, overlay: EquatorialGridOverlay) -> None:

        if len(overlay.points) < 2:

            return

        pen = QPen(QColor(overlay.color), 0.0)

        pen.setStyle(overlay.pen_style)

        painter.setPen(pen)

        for start_point, end_point in zip(overlay.points, overlay.points[1:]):

            painter.drawLine(

                QPointF(start_point[0], start_point[1]),

                QPointF(end_point[0], end_point[1]),

            )

    def _grid_overlay_label_widget_point(self, overlay: EquatorialGridOverlay, painter: QPainter) -> QPointF | None:

        if not overlay.points:

            return None

        frame_rect = self._visible_image_widget_rect()

        if frame_rect.isEmpty():

            frame_rect = self._image_content_rect()

        text_bounds = self._overlay_label_text_bounds(painter, overlay.label)

        mapped_points = [self.image_to_widget(point_x, point_y) for point_x, point_y in overlay.points]

        visible_segments: list[tuple[float, QPointF, QPointF]] = []

        visible_points: list[QPointF] = []

        for start_point, end_point in zip(mapped_points, mapped_points[1:]):

            clipped_segment = self._clip_line_segment_to_rect(start_point, end_point, frame_rect)

            if clipped_segment is None:

                continue

            clipped_start, clipped_end = clipped_segment

            segment_length = math.hypot(clipped_end.x() - clipped_start.x(), clipped_end.y() - clipped_start.y())

            if segment_length <= 0.25:

                continue

            visible_segments.append((segment_length, clipped_start, clipped_end))

            visible_points.extend((clipped_start, clipped_end))

        if not visible_segments:

            return None

        anchor_point = self._grid_overlay_preferred_anchor_point(overlay.axis_kind, visible_points, frame_rect)

        candidate_point = self._grid_overlay_label_candidate_point(anchor_point, text_bounds)

        return self._clamped_label_point(
            candidate_point,
            frame_rect=frame_rect,
            painter=painter,
            text=overlay.label,
            text_bounds=text_bounds,
        )



    def _grid_overlay_label_candidate_point(self, anchor_point: QPointF, text_bounds: QRectF) -> QPointF:

        return QPointF(

            float(anchor_point.x()) + 6.0 - float(text_bounds.left()),

            float(anchor_point.y()) - 6.0 - float(text_bounds.bottom()),

        )



    def _grid_overlay_preferred_anchor_point(self, axis_kind: str, points: list[QPointF], rect: QRectF) -> QPointF:

        normalized_axis_kind = str(axis_kind or "").strip().lower()

        if normalized_axis_kind == "dec":

            return min(

                points,

                key=lambda point: (

                    abs(float(point.y()) - float(rect.bottom())),

                    min(abs(float(point.x()) - float(rect.left())), abs(float(point.x()) - float(rect.right()))),

                ),

            )

        if normalized_axis_kind == "ra":

            return min(

                points,

                key=lambda point: (

                    abs(float(point.x()) - float(rect.left())),

                    min(abs(float(point.y()) - float(rect.top())), abs(float(point.y()) - float(rect.bottom()))),

                ),

            )

        return min(points, key=lambda point: self._point_to_nearest_rect_corner_distance(point, rect))



    def _point_to_nearest_rect_corner_distance(self, point: QPointF, rect: QRectF) -> float:

        corners = (

            QPointF(rect.left(), rect.top()),

            QPointF(rect.right(), rect.top()),

            QPointF(rect.left(), rect.bottom()),

            QPointF(rect.right(), rect.bottom()),

        )

        return min(math.hypot(float(point.x()) - corner.x(), float(point.y()) - corner.y()) for corner in corners)



    def _clip_line_segment_to_rect(self, start_point: QPointF, end_point: QPointF, rect: QRectF) -> tuple[QPointF, QPointF] | None:

        if rect.isEmpty():

            return None

        x0 = float(start_point.x())

        y0 = float(start_point.y())

        x1 = float(end_point.x())

        y1 = float(end_point.y())

        dx = x1 - x0

        dy = y1 - y0

        t0 = 0.0

        t1 = 1.0

        for edge_delta, edge_distance in (

            (-dx, x0 - rect.left()),

            (dx, rect.right() - x0),

            (-dy, y0 - rect.top()),

            (dy, rect.bottom() - y0),

        ):

            if abs(edge_delta) < 1.0e-9:

                if edge_distance < 0.0:

                    return None

                continue

            ratio = edge_distance / edge_delta

            if edge_delta < 0.0:

                if ratio > t1:

                    return None

                if ratio > t0:

                    t0 = ratio

            else:

                if ratio < t0:

                    return None

                if ratio < t1:

                    t1 = ratio

        clipped_start = QPointF(x0 + (t0 * dx), y0 + (t0 * dy))

        clipped_end = QPointF(x0 + (t1 * dx), y0 + (t1 * dy))

        return clipped_start, clipped_end



    @staticmethod
    def _scaled_font_point_size(font_size_pt: float, image_width: float, image_height: float) -> float:

        reference = max(1.0, min(float(image_width), float(image_height)))

        return max(8.0, float(font_size_pt) * reference / 900.0)



    @classmethod
    def _text_draw_rect(
        cls,
        location: str,
        image_width: float,
        image_height: float,
        *,
        margin_fraction: float = 0.03,
    ) -> tuple[QRectF, int]:

        margin = max(8.0, min(float(image_width), float(image_height)) * margin_fraction)

        width = max(1.0, float(image_width))

        height = max(1.0, float(image_height))

        location_key = location.strip().lower().replace(" ", "_")

        if location_key == "top_center":

            return QRectF(margin, margin, width - (2.0 * margin), height * 0.25), int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)

        if location_key == "top_right":

            return QRectF(margin, margin, width - (2.0 * margin), height * 0.25), int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)

        if location_key == "center_left":

            return QRectF(margin, margin, width * 0.45, height - (2.0 * margin)), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        if location_key == "center":

            return QRectF(margin, margin, width - (2.0 * margin), height - (2.0 * margin)), int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)

        if location_key == "center_right":

            return QRectF(width * 0.55 - margin, margin, width * 0.45, height - (2.0 * margin)), int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        if location_key == "bottom_left":

            return QRectF(margin, height * 0.75 - margin, width * 0.7, height * 0.25), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)

        if location_key == "bottom_center":

            return QRectF(margin, height * 0.75 - margin, width - (2.0 * margin), height * 0.25), int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom)

        if location_key == "bottom_right":

            return QRectF(width * 0.3, height * 0.75 - margin, width * 0.7 - margin, height * 0.25), int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)

        return QRectF(margin, margin, width * 0.7, height * 0.25), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)



    @classmethod
    def _band_draw_rect(
        cls,
        location: str,
        width_fraction: float,
        height_fraction: float,
        image_width: float,
        image_height: float,
        *,
        margin_fraction: float = 0.02,
    ) -> QRectF:

        width = max(1.0, float(image_width))

        height = max(1.0, float(image_height))

        margin = max(6.0, min(width, height) * margin_fraction)

        band_width = max(8.0, width * max(0.05, min(1.0, float(width_fraction))))

        band_height = max(8.0, height * max(0.02, min(1.0, float(height_fraction))))

        location_key = location.strip().lower().replace(" ", "_")

        if location_key == "top_center":

            return QRectF((width - band_width) * 0.5, margin, band_width, band_height)

        if location_key == "top_right":

            return QRectF(width - margin - band_width, margin, band_width, band_height)

        if location_key == "center_left":

            return QRectF(margin, (height - band_height) * 0.5, band_width, band_height)

        if location_key == "center":

            return QRectF((width - band_width) * 0.5, (height - band_height) * 0.5, band_width, band_height)

        if location_key == "center_right":

            return QRectF(width - margin - band_width, (height - band_height) * 0.5, band_width, band_height)

        if location_key == "bottom_left":

            return QRectF(margin, height - margin - band_height, band_width, band_height)

        if location_key == "bottom_center":

            return QRectF((width - band_width) * 0.5, height - margin - band_height, band_width, band_height)

        if location_key == "bottom_right":

            return QRectF(width - margin - band_width, height - margin - band_height, band_width, band_height)

        return QRectF(margin, margin, band_width, band_height)



    @classmethod
    def _paint_text_decoration(
        cls,
        painter: QPainter,
        decoration: ImageTextDecoration,
        image_width: float,
        image_height: float,
    ) -> None:

        text = decoration.text.strip()

        if not text:

            return

        draw_rect, alignment = cls._text_draw_rect(decoration.location, image_width, image_height)

        font = QFont(decoration.font_family or painter.font().family())

        font.setPointSizeF(cls._scaled_font_point_size(decoration.font_size_pt, image_width, image_height))

        painter.save()

        painter.setFont(font)

        painter.setPen(QColor(decoration.color or "#ffffff"))

        painter.drawText(draw_rect, alignment, text)

        painter.restore()



    @classmethod
    def _paint_band_decoration(
        cls,
        painter: QPainter,
        decoration: ImageBandDecoration,
        image_width: float,
        image_height: float,
    ) -> None:

        band_rect = cls._band_draw_rect(
            decoration.location,
            decoration.width_fraction,
            decoration.height_fraction,
            image_width,
            image_height,
        )

        if band_rect.width() <= 1.0 or band_rect.height() <= 1.0:

            return

        fill_color = QColor(decoration.color or "#000000")

        fill_color.setAlphaF(max(0.0, min(1.0, float(decoration.opacity))))

        painter.save()

        painter.setPen(Qt.PenStyle.NoPen)

        painter.setBrush(fill_color)

        painter.drawRect(band_rect)

        painter.restore()



    @classmethod
    def _paint_decoration_overlays(
        cls,
        painter: QPainter,
        overlays: ImageDecorationOverlays | None,
        image_width: float,
        image_height: float,
    ) -> None:

        if overlays is None:

            return

        if overlays.band is not None:

            cls._paint_band_decoration(painter, overlays.band, image_width, image_height)

        if overlays.title is not None:

            cls._paint_text_decoration(painter, overlays.title, image_width, image_height)

        if overlays.location_label is not None:

            cls._paint_text_decoration(painter, overlays.location_label, image_width, image_height)



    def _draw_selection_overlay(self, painter: QPainter) -> None:

        for overlay in self._selection_overlays:

            pen = QPen(QColor(overlay.color), 0.0)

            pen.setStyle(Qt.PenStyle.DashLine)

            painter.setPen(pen)

            fill_color = QColor(overlay.color)

            fill_color.setAlpha(48)

            painter.setBrush(fill_color)

            if overlay.shape == "circle":

                radius = float(np.hypot(overlay.x1 - overlay.x0, overlay.y1 - overlay.y0))

                if radius <= 0.5:

                    continue

                painter.drawEllipse(QPointF(overlay.x0, overlay.y0), radius, radius)

                continue

            rect = QRectF(QPointF(overlay.x0, overlay.y0), QPointF(overlay.x1, overlay.y1)).normalized()

            if rect.width() <= 1.0 or rect.height() <= 1.0:

                continue

            painter.drawRect(rect)



    def _draw_motion_vector_overlay(self, painter: QPainter, overlay: MotionVectorOverlay) -> None:

        end_x = float(overlay.x + overlay.dx)

        end_y = float(overlay.y + overlay.dy)

        vector_length = float(np.hypot(overlay.dx, overlay.dy))

        if vector_length <= 0.25:

            return



        pen = QPen(QColor(overlay.color), max(0.5, float(overlay.width)))

        pen.setCosmetic(True)

        painter.setPen(pen)

        painter.setBrush(Qt.BrushStyle.NoBrush)

        painter.drawLine(QPointF(overlay.x, overlay.y), QPointF(end_x, end_y))

        arrow_length = max(5.0, min(12.0, 0.28 * vector_length))

        arrow_angle = np.deg2rad(28.0)

        direction_angle = float(np.arctan2(overlay.dy, overlay.dx))

        left_angle = direction_angle + np.pi - arrow_angle

        right_angle = direction_angle + np.pi + arrow_angle

        left_point = QPointF(

            end_x + (arrow_length * float(np.cos(left_angle))),

            end_y + (arrow_length * float(np.sin(left_angle))),

        )

        right_point = QPointF(

            end_x + (arrow_length * float(np.cos(right_angle))),

            end_y + (arrow_length * float(np.sin(right_angle))),

        )

        painter.drawLine(QPointF(end_x, end_y), left_point)

        painter.drawLine(QPointF(end_x, end_y), right_point)

        if overlay.show_anchor:

            painter.setBrush(QColor(overlay.color))

            painter.drawEllipse(QPointF(overlay.x, overlay.y), 1.6, 1.6)

    def _motion_vector_overlay_intersects_image_rect(self, overlay: MotionVectorOverlay, image_rect: QRectF) -> bool:

        padding = max(6.0, float(overlay.width) + 3.0)

        left = min(float(overlay.x), float(overlay.x + overlay.dx)) - padding

        top = min(float(overlay.y), float(overlay.y + overlay.dy)) - padding

        right = max(float(overlay.x), float(overlay.x + overlay.dx)) + padding

        bottom = max(float(overlay.y), float(overlay.y + overlay.dy)) + padding

        return image_rect.intersects(QRectF(left, top, right - left, bottom - top))


    def _draw_hover_text_overlay(self, painter: QPainter) -> None:

        if self._hover_image_point is None or self._hover_text_formatter is None:

            return

        try:

            text = self._hover_text_formatter(float(self._hover_image_point.x()), float(self._hover_image_point.y()))

        except Exception:

            text = None

        if not text:

            return

        content_rect = self._image_content_rect()

        if content_rect.isEmpty():

            return

        painter.save()

        font = QFont(painter.font())

        font.setPointSizeF(max(8.0, min(11.0, font.pointSizeF() if font.pointSizeF() > 0 else 9.0)))

        painter.setFont(font)

        metrics = painter.fontMetrics()

        horizontal_padding = 10.0

        vertical_padding = 5.0

        text_width = float(metrics.horizontalAdvance(text))

        text_height = float(metrics.height())

        card_width = min(max(1.0, content_rect.width() - 16.0), text_width + (2.0 * horizontal_padding))

        card_height = text_height + (2.0 * vertical_padding)

        card_rect = QRectF(

            content_rect.left() + 8.0,

            content_rect.top() + 8.0,

            card_width,

            card_height,

        )

        painter.setPen(QPen(QColor(15, 23, 42, 220), 1.0))

        painter.setBrush(QColor(15, 23, 42, 196))

        painter.drawRoundedRect(card_rect, 5.0, 5.0)

        painter.setPen(QColor("#f8fafc"))

        painter.drawText(card_rect.adjusted(horizontal_padding, vertical_padding, -horizontal_padding, -vertical_padding), int(Qt.AlignmentFlag.AlignCenter), text)

        painter.restore()



    def _default_view_center(self) -> QPointF:

        if self._qimage is None:

            return QPointF(0.0, 0.0)

        return QPointF(self._qimage.width() / 2.0, self._qimage.height() / 2.0)



    def _effective_scale(self) -> float:

        if self._qimage is None:

            return 1.0

        content_rect = self._image_content_rect()

        fit_scale = min(content_rect.width() / max(1, self._qimage.width()), content_rect.height() / max(1, self._qimage.height()))

        return max(0.01, fit_scale * self._zoom_scale)



    def _clamped_view_center(self) -> QPointF:

        if self._qimage is None:

            return QPointF(0.0, 0.0)

        center = self._view_center or self._default_view_center()

        scale = self._effective_scale()

        content_rect = self._image_content_rect()

        half_width = content_rect.width() / (2.0 * scale)

        half_height = content_rect.height() / (2.0 * scale)

        min_x = half_width

        max_x = max(half_width, self._qimage.width() - half_width)

        min_y = half_height

        max_y = max(half_height, self._qimage.height() - half_height)

        if self._qimage.width() <= 2 * half_width:

            x_value = self._qimage.width() / 2.0

        else:

            x_value = min(max(center.x(), min_x), max_x)

        if self._qimage.height() <= 2 * half_height:

            y_value = self._qimage.height() / 2.0

        else:

            y_value = min(max(center.y(), min_y), max_y)

        return QPointF(x_value, y_value)



    def _image_content_rect(self) -> QRectF:

        return QRectF(0.0, 0.0, max(1.0, float(self.width())), float(self.height()))



    def _info_panel_rect(self) -> QRectF:

        return self._info_panel_rect_for_bounds(float(self.width()), float(self.height()))



    def _info_panel_rect_for_bounds(self, width: float, height: float) -> QRectF:

        panel_width = self._info_panel_width_for_bounds(width)

        if panel_width <= 0.0:

            return QRectF()

        return QRectF(width - panel_width, 0.0, panel_width, height)



    def _info_panel_width(self) -> float:

        return self._info_panel_width_for_bounds(float(self.width()))



    def _info_panel_width_for_bounds(self, width: float) -> float:

        if self._info_panel is None:

            return 0.0

        max_panel_width = max(0.0, width - 220.0)

        if max_panel_width < 180.0:

            return 0.0

        desired_width = max(220.0, min(310.0, width * 0.24))

        return min(desired_width, max_panel_width)



    def _chart_overlay_widget_bounds(self) -> QRectF:

        visible_rect = self._visible_image_widget_rect()

        if not visible_rect.isEmpty():

            return visible_rect

        return self._image_content_rect()



    def _chart_overlay_image_bounds(self, width: float, height: float) -> QRectF:

        return QRectF(0.0, 0.0, max(1.0, width), max(1.0, height))



    @staticmethod
    def chart_overlay_panel_size(image_width: float, image_height: float) -> tuple[float, float, float]:

        width = max(_CHART_OVERLAY_MIN_WIDTH, float(image_width) * _CHART_OVERLAY_WIDTH_FRACTION)

        height = max(_CHART_OVERLAY_MIN_HEIGHT, float(image_height) * _CHART_OVERLAY_HEIGHT_FRACTION)

        edge_margin = max(8.0, min(24.0, float(image_width) * _CHART_OVERLAY_EDGE_MARGIN_FRACTION))

        return width, height, edge_margin



    @staticmethod
    def chart_overlay_layout_scale(panel_rect: QRectF) -> float:

        reference_height = 120.0

        return max(0.55, min(8.0, panel_rect.height() / reference_height))



    @staticmethod
    def chart_overlay_metrics(image_rect: QRectF) -> tuple[float, float, float, float]:

        image_width = max(1.0, image_rect.width())

        image_height = max(1.0, image_rect.height())

        panel_width, panel_height, edge_margin = AnnotatedImageView.chart_overlay_panel_size(image_width, image_height)

        chart_height = max(48.0, panel_height * 0.55)

        return panel_width, chart_height, edge_margin, image_height



    @staticmethod
    def measure_chart_overlay_content_height(panel_width: float, chart_height: float, *, title: str = "Stack SNR") -> float:

        title_font = QFont()

        title_font.setBold(True)

        title_font.setPointSizeF(11.0)

        title_metrics = QFontMetrics(title_font)

        body_font = QFont()

        body_font.setPointSizeF(9.0)

        body_metrics = QFontMetrics(body_font)

        value_font = QFont()

        value_font.setBold(True)

        value_font.setPointSizeF(11.0)

        value_metrics = QFontMetrics(value_font)

        _ = panel_width

        _ = title

        return (
            12.0
            + float(title_metrics.height())
            + 8.0
            + chart_height
            + 10.0
            + float(body_metrics.height())
            + 6.0
            + float(value_metrics.height())
            + 12.0
        )



    @classmethod
    def default_chart_overlay_rect(cls, image_width: float, image_height: float, *, title: str = "Stack SNR") -> QRectF:

        _ = title

        image_rect = QRectF(0.0, 0.0, max(1.0, float(image_width)), max(1.0, float(image_height)))

        panel_width, panel_height, edge_margin = cls.chart_overlay_panel_size(image_rect.width(), image_rect.height())

        left = image_rect.right() - panel_width - edge_margin

        top = image_rect.top() + edge_margin

        return QRectF(left, top, panel_width, panel_height)



    def _chart_overlay_metrics(self, image_rect: QRectF) -> tuple[float, float, float, float]:

        return self.chart_overlay_metrics(image_rect)



    def _measure_chart_overlay_content_height(self, panel_width: float, chart_height: float) -> float:

        panel = self._chart_overlay_panel

        title = panel.title if panel is not None else "Stack SNR"

        return self.measure_chart_overlay_content_height(panel_width, chart_height, title=title)



    @staticmethod
    def _plot_horizontal_alignment(align: str) -> Qt.AlignmentFlag:

        normalized = str(align or "left").strip().lower()

        if normalized == "center":

            return Qt.AlignmentFlag.AlignHCenter

        if normalized == "right":

            return Qt.AlignmentFlag.AlignRight

        return Qt.AlignmentFlag.AlignLeft



    @staticmethod
    def _plot_vertical_alignment(align: str) -> Qt.AlignmentFlag:

        normalized = str(align or "top").strip().lower()

        if normalized in {"center", "middle"}:

            return Qt.AlignmentFlag.AlignVCenter

        if normalized == "bottom":

            return Qt.AlignmentFlag.AlignBottom

        return Qt.AlignmentFlag.AlignTop



    @staticmethod
    def _plot_text_color(color: str, opacity: float) -> QColor:
        resolved = QColor(color or "#f2f2f2")
        if resolved.isValid():
            resolved.setAlphaF(max(0.0, min(1.0, float(opacity))))
        return resolved

    @staticmethod
    def _plot_font_from_style(
        *,
        font_family: str = "",
        font_style: str = "regular",
        base_pixel_size: float,
        bold: bool = False,
        explicit_font_size: float = 0.0,
    ) -> QFont:

        font = QFont(font_family) if font_family else QFont()

        normalized_style = str(font_style or "regular").strip().lower()

        font.setBold(bold or normalized_style in {"bold", "bold-italic"})

        font.setItalic(normalized_style in {"italic", "bold-italic"})

        if float(explicit_font_size) > 0.0:

            font.setPixelSize(int(max(1.0, float(explicit_font_size))))

        else:

            font.setPixelSize(int(max(6.0, base_pixel_size)))

        return font



    def _draw_chart_overlay_panel(
        self,
        painter: QPainter,
        panel: ImageChartOverlayPanel,
        panel_rect: QRectF,
        *,
        include_stack_status: bool = True,
        stroke_color: str = "#3a3a3a",
        fill_color: str = "#121212",
        fill_opacity: float = 228.0 / 255.0,
        stroke_opacity: float = 1.0,
        plot_style: ImagePlotStyle | None = None,
    ) -> None:

        if panel_rect.isEmpty():

            return

        style = plot_style or ImagePlotStyle(
            stroke_color=stroke_color,
            fill_color=fill_color,
            fill_opacity=fill_opacity,
            stroke_opacity=stroke_opacity,
        )

        panel_width = max(1.0, panel_rect.width())

        panel_height = max(1.0, panel_rect.height())

        pad = max(4.0, min(panel_width, panel_height) * 0.06)

        inner_rect = panel_rect.adjusted(pad, pad, -pad, -pad)

        inner_width = max(1.0, inner_rect.width())

        inner_height = max(1.0, inner_rect.height())

        gap = max(2.0, inner_height * 0.015)

        title_band = inner_height * 0.11

        footer_band = inner_height * 0.24 if include_stack_status else 0.0

        chart_band = inner_height - title_band - footer_band - gap * (3.0 if include_stack_status else 2.0)

        if chart_band < inner_height * 0.30:

            chart_band = inner_height * 0.30

            remaining = inner_height - chart_band

            if include_stack_status:

                title_band = remaining * 0.28

                footer_band = remaining * 0.72

            else:

                title_band = remaining

                footer_band = 0.0

        if float(style.corner_radius) > 0.0:

            corner_radius = float(style.corner_radius)

        else:

            corner_radius = max(4.0, min(panel_width, panel_height) * 0.03)

        base_font_px = max(8.0, inner_height * 0.075)

        title_font_px = float(style.title_font_size) if float(style.title_font_size) > 0.0 else base_font_px * 1.05

        label_font_px = float(style.label_font_size) if float(style.label_font_size) > 0.0 else base_font_px



        painter.save()

        fill = QColor(style.fill_color or fill_color)

        if fill.isValid():

            fill.setAlphaF(max(0.0, min(1.0, float(style.fill_opacity))))

        stroke = QColor(style.stroke_color or stroke_color)

        if stroke.isValid():

            stroke.setAlphaF(max(0.0, min(1.0, float(style.stroke_opacity))))

        stroke_width = float(style.stroke_width)

        if stroke_width <= 0.0:

            stroke_width = max(1.0, min(panel_width, panel_height) * 0.0025)

        painter.setPen(QPen(stroke, stroke_width))

        painter.setBrush(fill)

        painter.drawRoundedRect(panel_rect, corner_radius, corner_radius)

        painter.setClipRect(panel_rect)



        title_rect = QRectF(
            inner_rect.left() + float(style.title_offset_x),
            inner_rect.top() + float(style.title_offset_y),
            inner_width,
            title_band,
        )

        title_font = self._plot_font_from_style(
            font_family=style.title_font_family,
            font_style=style.title_font_style,
            base_pixel_size=title_font_px,
            bold=True,
            explicit_font_size=float(style.title_font_size),
        )

        painter.setFont(title_font)

        painter.setPen(self._plot_text_color(style.title_text_color, style.title_text_opacity))

        painter.drawText(
            title_rect,
            int(self._plot_horizontal_alignment(style.title_align_h) | self._plot_vertical_alignment(style.title_align_v)),
            panel.title,
        )

        y_position = inner_rect.top() + title_band + gap



        chart_rect = QRectF(inner_rect.left(), y_position, inner_width, chart_band)

        margin_left = max(0.0, min(0.45, float(style.chart_margin_left)))

        margin_right = max(0.0, min(0.45, float(style.chart_margin_right)))

        margin_top = max(0.0, min(0.45, float(style.chart_margin_top)))

        margin_bottom = max(0.0, min(0.45, float(style.chart_margin_bottom)))

        if margin_left > 0.0 or margin_right > 0.0 or margin_top > 0.0 or margin_bottom > 0.0:

            chart_rect = chart_rect.adjusted(
                chart_rect.width() * margin_left,
                chart_rect.height() * margin_top,
                -chart_rect.width() * margin_right,
                -chart_rect.height() * margin_bottom,
            )

        self._draw_info_panel_line_chart(painter, panel.line_chart, chart_rect, plot_style=style)

        y_position += chart_band + gap



        if include_stack_status and footer_band > 0.0:

            integration_row_height = footer_band * 0.42

            frame_row_height = max(0.0, footer_band - integration_row_height - gap)



            label_font = self._plot_font_from_style(
                font_family=style.label_font_family,
                font_style=style.label_font_style,
                base_pixel_size=label_font_px * 0.9,
                bold=False,
                explicit_font_size=float(style.label_font_size),
            )

            painter.setFont(label_font)

            painter.setPen(QColor(style.accent_text_color))

            painter.drawText(

                QRectF(inner_rect.left(), y_position, inner_width * 0.55, integration_row_height),

                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),

                "Integration",

            )

            value_font = self._plot_font_from_style(
                font_family=style.label_font_family,
                font_style=style.label_font_style,
                base_pixel_size=label_font_px,
                bold=True,
                explicit_font_size=float(style.label_font_size),
            )

            painter.setFont(value_font)

            painter.setPen(self._plot_text_color(style.label_text_color, style.label_text_opacity))

            painter.drawText(

                QRectF(inner_rect.left(), y_position, inner_width, integration_row_height),

                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),

                panel.integration_text,

            )

            y_position += integration_row_height + gap



            frame_font = self._plot_font_from_style(
                font_family=style.title_font_family,
                font_style=style.title_font_style,
                base_pixel_size=title_font_px * 1.03,
                bold=True,
                explicit_font_size=float(style.title_font_size),
            )

            painter.setFont(frame_font)

            painter.setPen(self._plot_text_color(style.title_text_color, style.title_text_opacity))

            painter.drawText(

                QRectF(inner_rect.left(), y_position, inner_width, frame_row_height),

                int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),

                panel.frame_text,

            )

        painter.restore()



    def _chart_overlay_rect(self, image_rect: QRectF) -> QRectF:

        panel = self._chart_overlay_panel

        if panel is None:

            return QRectF()

        return self.default_chart_overlay_rect(image_rect.width(), image_rect.height(), title=panel.title)



    def _draw_chart_overlay(self, painter: QPainter, image_rect: QRectF) -> None:

        panel = self._chart_overlay_panel

        if panel is None:

            return

        panel_rect = self._chart_overlay_rect(image_rect)

        if panel_rect.isEmpty():

            return

        self._draw_chart_overlay_panel(painter, panel, panel_rect, include_stack_status=True)



    def _draw_info_panel(self, painter: QPainter) -> None:

        self._draw_info_panel_for_bounds(painter, float(self.width()), float(self.height()))



    def _info_panel_line_chart_height(self) -> float:

        return 150.0



    def _draw_info_panel_for_bounds(self, painter: QPainter, width: float, height: float) -> None:

        panel = self._info_panel

        panel_rect = self._info_panel_rect_for_bounds(width, height)

        if panel is None or panel_rect.isEmpty():

            self._info_panel_scroll_max = 0.0

            self._info_panel_scroll_offset = 0.0

            return

        inner_rect = panel_rect.adjusted(10.0, 10.0, -10.0, -10.0)

        card_rect = inner_rect.adjusted(0.0, 0.0, -2.0, 0.0)

        viewport_rect = card_rect.adjusted(0.0, 0.0, -10.0, 0.0)



        painter.save()

        painter.setPen(QPen(QColor("#3a3a3a"), 1.0))

        painter.setBrush(QColor(18, 18, 18, 228))

        painter.drawRoundedRect(card_rect, 8.0, 8.0)



        content_height = self._measure_info_panel_content_height(painter, panel, viewport_rect)

        self._info_panel_scroll_max = max(0.0, content_height - max(1.0, viewport_rect.height() - 8.0))

        self._info_panel_scroll_offset = self._clamp_info_panel_scroll(self._info_panel_scroll_offset)

        painter.setClipRect(viewport_rect.adjusted(1.0, 1.0, -4.0, -1.0))

        painter.translate(0.0, -self._info_panel_scroll_offset)



        y_position = card_rect.top() + 12.0

        text_left = card_rect.left() + 12.0

        text_right = viewport_rect.right() - 10.0

        text_width = max(40.0, text_right - text_left)



        title_font = painter.font()

        title_font.setBold(True)

        title_font.setPointSizeF(max(9.5, title_font.pointSizeF()))

        painter.setFont(title_font)

        painter.setPen(QColor("#f2f2f2"))

        title_height = self._draw_wrapped_text(

            painter,

            QRectF(text_left, y_position, text_width, card_rect.height()),

            panel.title,

        )

        y_position += title_height + 6.0



        if panel.subtitle:

            subtitle_font = painter.font()

            subtitle_font.setBold(False)

            subtitle_font.setPointSizeF(max(8.5, subtitle_font.pointSizeF() - 0.5))

            painter.setFont(subtitle_font)

            painter.setPen(QColor("#b8b8b8"))

            subtitle_height = self._draw_wrapped_text(

                painter,

                QRectF(text_left, y_position, text_width, card_rect.height()),

                panel.subtitle,

            )

            y_position += subtitle_height + 10.0



        if panel.line_chart is not None:

            chart_height = self._info_panel_line_chart_height()

            chart_rect = QRectF(text_left, y_position, text_width, chart_height)

            self._draw_info_panel_line_chart(painter, panel.line_chart, chart_rect)

            y_position += chart_height + 10.0



        for section in panel.sections:

            painter.setPen(QColor("#4b4b4b"))

            painter.drawLine(QPointF(text_left, y_position), QPointF(text_right, y_position))

            y_position += 8.0



            section_font = painter.font()

            section_font.setBold(True)

            section_font.setPointSizeF(max(8.5, section_font.pointSizeF()))

            painter.setFont(section_font)

            painter.setPen(QColor("#ffd166"))

            section_height = self._draw_wrapped_text(

                painter,

                QRectF(text_left, y_position, text_width, card_rect.height()),

                section.title,

            )

            y_position += section_height + 6.0



            body_font = painter.font()

            body_font.setBold(False)

            body_font.setPointSizeF(max(8.0, body_font.pointSizeF() - 0.25))

            painter.setFont(body_font)

            for item in section.items:

                painter.setPen(QColor("#9aa5b1"))

                label_height = self._draw_wrapped_text(

                    painter,

                    QRectF(text_left, y_position, text_width, card_rect.height()),

                    item.label,

                )

                y_position += label_height + 1.0

                painter.setPen(QColor("#f2f2f2"))

                value_height = self._draw_wrapped_text(

                    painter,

                    QRectF(text_left + 6.0, y_position, text_width - 6.0, card_rect.height()),

                    item.value,

                )

                y_position += value_height + 5.0



            if section.note:

                painter.setPen(QColor("#cfcfcf"))

                note_height = self._draw_wrapped_text(

                    painter,

                    QRectF(text_left, y_position, text_width, card_rect.height()),

                    section.note,

                )

                y_position += note_height + 8.0



        if panel.footer:

            painter.setPen(QColor("#8a8a8a"))

            footer_font = painter.font()

            footer_font.setItalic(True)

            painter.setFont(footer_font)

            self._draw_wrapped_text(

                painter,

                QRectF(text_left, y_position, text_width, card_rect.bottom() - y_position - 8.0),

                panel.footer,

            )

        painter.restore()

        self._draw_info_panel_scrollbar(painter, card_rect, viewport_rect)



    def _draw_info_panel_line_chart(
        self,
        painter: QPainter,
        chart: ImageInfoLineChart,
        plot_rect: QRectF,
        *,
        plot_style: ImagePlotStyle | None = None,
    ) -> None:

        style = plot_style or ImagePlotStyle()

        layout_scale = self.chart_overlay_layout_scale(plot_rect)

        if not chart.x_values or not chart.y_values or len(chart.x_values) != len(chart.y_values):

            empty_font = self._plot_font_from_style(
                font_family=style.label_font_family,
                font_style=style.label_font_style,
                base_pixel_size=max(12.0, 11.0 * layout_scale),
                bold=False,
                explicit_font_size=float(style.label_font_size),
            )

            painter.setFont(empty_font)

            painter.setPen(self._plot_text_color(style.label_text_color, style.label_text_opacity))

            painter.drawText(plot_rect, int(Qt.AlignmentFlag.AlignCenter), "No plot data yet.")

            return



        x_values = [float(value) for value in chart.x_values]

        y_values = [float(value) for value in chart.y_values]

        x_min = min(x_values)

        x_max = max(x_values)

        y_min = min(y_values)

        y_max = max(y_values)

        if x_max <= x_min:

            x_max = x_min + 1.0

        if y_max <= y_min:

            y_max = y_min + max(1.0, abs(y_min) * 0.05)



        margin_left = max(18.0, plot_rect.width() * 0.16)

        margin_bottom = max(12.0, plot_rect.height() * 0.18)

        margin_top = max(4.0, plot_rect.height() * 0.05)

        margin_right = max(4.0, plot_rect.width() * 0.04)

        inner_rect = plot_rect.adjusted(margin_left, margin_top, -margin_right, -margin_bottom)

        if inner_rect.width() <= 1.0 or inner_rect.height() <= 1.0:

            return



        def _map_point(x_value: float, y_value: float) -> QPointF:

            x_ratio = (x_value - x_min) / (x_max - x_min)

            y_ratio = (y_value - y_min) / (y_max - y_min)

            return QPointF(

                inner_rect.left() + x_ratio * inner_rect.width(),

                inner_rect.bottom() - y_ratio * inner_rect.height(),

            )



        axis_line_width = max(1.0, layout_scale)

        painter.setPen(QPen(QColor("#555555"), axis_line_width))

        painter.drawLine(inner_rect.bottomLeft(), inner_rect.topLeft())

        painter.drawLine(inner_rect.bottomLeft(), inner_rect.bottomRight())



        axis_font = self._plot_font_from_style(
            font_family=style.label_font_family,
            font_style=style.label_font_style,
            base_pixel_size=max(10.0, 9.0 * layout_scale),
            bold=False,
            explicit_font_size=float(style.label_font_size),
        )

        painter.setFont(axis_font)

        painter.setPen(self._plot_text_color(style.label_text_color, style.label_text_opacity))

        axis_label_height = max(14.0, 16.0 * layout_scale)

        painter.drawText(
            QRectF(
                inner_rect.left() + float(style.x_label_offset_x),
                inner_rect.bottom() + 2.0 + float(style.x_label_offset_y),
                inner_rect.width(),
                axis_label_height,
            ),
            int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop),
            chart.x_label,
        )

        y_anchor_x = inner_rect.left() - margin_left * 0.5 + float(style.y_label_offset_x)

        y_anchor_y = inner_rect.center().y() + float(style.y_label_offset_y)

        painter.save()

        painter.translate(y_anchor_x, y_anchor_y)

        painter.rotate(-90.0)

        painter.drawText(
            QRectF(-inner_rect.height() * 0.5, -axis_label_height * 0.5, inner_rect.height(), axis_label_height),
            int(Qt.AlignmentFlag.AlignCenter),
            chart.y_label,
        )

        painter.restore()



        tick_font = self._plot_font_from_style(
            font_family=style.label_font_family,
            font_style=style.label_font_style,
            base_pixel_size=max(9.0, 8.0 * layout_scale),
            bold=False,
            explicit_font_size=float(style.label_font_size),
        )

        painter.setFont(tick_font)

        painter.setPen(QColor("#8a8a8a"))

        tick_height = max(10.0, 12.0 * layout_scale)

        tick_width = max(24.0, 30.0 * layout_scale)

        painter.drawText(
            QRectF(inner_rect.left() - tick_width - 4.0 * layout_scale, inner_rect.bottom() - 6.0, tick_width, tick_height),
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            f"{y_min:.1f}",
        )

        painter.drawText(
            QRectF(inner_rect.left() - tick_width - 4.0 * layout_scale, inner_rect.top() - 6.0, tick_width, tick_height),
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            f"{y_max:.1f}",
        )

        painter.drawText(
            QRectF(inner_rect.left(), inner_rect.bottom() + 4.0, tick_width, tick_height),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop),
            f"{int(x_min)}",
        )

        painter.drawText(
            QRectF(inner_rect.right() - tick_width, inner_rect.bottom() + 4.0, tick_width, tick_height),
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop),
            f"{int(x_max)}",
        )



        curve_width = float(style.curve_width)

        if curve_width <= 0.0:

            curve_width = max(1.5, 2.0 * layout_scale)

        curve_color = QColor(style.curve_color)

        if curve_color.isValid():

            curve_color.setAlphaF(max(0.0, min(1.0, float(style.curve_opacity))))

        painter.setPen(QPen(curve_color, curve_width))

        previous_point: QPointF | None = None

        for x_value, y_value in zip(x_values, y_values, strict=True):

            point = _map_point(x_value, y_value)

            if previous_point is not None:

                painter.drawLine(previous_point, point)

            previous_point = point



        highlight_index = chart.highlight_index

        if highlight_index is not None and 0 <= highlight_index < len(x_values):

            highlight_point = _map_point(x_values[highlight_index], y_values[highlight_index])

            highlight_radius = float(style.highlight_radius)

            if highlight_radius <= 0.0:

                highlight_radius = max(3.0, 4.0 * layout_scale)

            highlight_color = QColor(style.highlight_color)

            if highlight_color.isValid():

                highlight_color.setAlphaF(max(0.0, min(1.0, float(style.highlight_opacity))))

            painter.setPen(QPen(highlight_color, max(1.0, highlight_radius * 0.35)))

            painter.setBrush(highlight_color)

            painter.drawEllipse(highlight_point, highlight_radius, highlight_radius)



    def _measure_info_panel_content_height(self, painter: QPainter, panel: ImageInfoPanel, viewport_rect: QRectF) -> float:

        y_position = viewport_rect.top() + 12.0

        text_width = max(40.0, viewport_rect.width() - 32.0)



        title_font = painter.font()

        title_font.setBold(True)

        title_font.setPointSizeF(max(9.5, title_font.pointSizeF()))

        title_metrics = painter.fontMetrics() if painter.font() == title_font else None

        painter.save()

        painter.setFont(title_font)

        title_metrics = painter.fontMetrics()

        y_position += self._wrapped_text_height(title_metrics, text_width, panel.title) + 6.0



        if panel.subtitle:

            subtitle_font = painter.font()

            subtitle_font.setBold(False)

            subtitle_font.setPointSizeF(max(8.5, subtitle_font.pointSizeF() - 0.5))

            painter.setFont(subtitle_font)

            subtitle_metrics = painter.fontMetrics()

            y_position += self._wrapped_text_height(subtitle_metrics, text_width, panel.subtitle) + 10.0



        if panel.line_chart is not None:

            y_position += self._info_panel_line_chart_height() + 10.0



        for section in panel.sections:

            y_position += 8.0

            section_font = painter.font()

            section_font.setBold(True)

            section_font.setPointSizeF(max(8.5, section_font.pointSizeF()))

            painter.setFont(section_font)

            section_metrics = painter.fontMetrics()

            y_position += self._wrapped_text_height(section_metrics, text_width, section.title) + 6.0



            body_font = painter.font()

            body_font.setBold(False)

            body_font.setPointSizeF(max(8.0, body_font.pointSizeF() - 0.25))

            painter.setFont(body_font)

            body_metrics = painter.fontMetrics()

            for item in section.items:

                y_position += self._wrapped_text_height(body_metrics, text_width, item.label) + 1.0

                y_position += self._wrapped_text_height(body_metrics, max(34.0, text_width - 6.0), item.value) + 5.0



            if section.note:

                y_position += self._wrapped_text_height(body_metrics, text_width, section.note) + 8.0



        if panel.footer:

            footer_font = painter.font()

            footer_font.setItalic(True)

            painter.setFont(footer_font)

            footer_metrics = painter.fontMetrics()

            y_position += self._wrapped_text_height(footer_metrics, text_width, panel.footer) + 8.0

        painter.restore()

        return y_position - viewport_rect.top()



    def _draw_info_panel_scrollbar(self, painter: QPainter, card_rect: QRectF, viewport_rect: QRectF) -> None:

        if self._info_panel_scroll_max <= 0.0:

            return

        track_rect = QRectF(card_rect.right() - 7.0, viewport_rect.top() + 8.0, 3.0, max(24.0, viewport_rect.height() - 16.0))

        visible_height = max(1.0, viewport_rect.height() - 8.0)

        total_height = visible_height + self._info_panel_scroll_max

        thumb_height = max(24.0, track_rect.height() * (visible_height / total_height))

        available = max(1.0, track_rect.height() - thumb_height)

        offset_ratio = self._info_panel_scroll_offset / max(1.0, self._info_panel_scroll_max)

        thumb_top = track_rect.top() + (available * offset_ratio)



        painter.save()

        painter.setPen(Qt.PenStyle.NoPen)

        painter.setBrush(QColor(70, 70, 70, 110))

        painter.drawRoundedRect(track_rect, 1.5, 1.5)

        painter.setBrush(QColor("#d0d0d0"))

        painter.drawRoundedRect(QRectF(track_rect.left(), thumb_top, track_rect.width(), thumb_height), 1.5, 1.5)

        painter.restore()



    def _handle_info_panel_wheel(self, event: object) -> bool:

        if self._info_panel is None or not self._info_panel_rect().contains(event.position()):

            return False

        step_delta = float(event.angleDelta().y())

        if step_delta == 0.0:

            return True

        self._scroll_info_panel(-(step_delta / 120.0) * 36.0)

        return True



    def _scroll_info_panel(self, delta_pixels: float) -> None:

        new_offset = self._clamp_info_panel_scroll(self._info_panel_scroll_offset + delta_pixels)

        if abs(new_offset - self._info_panel_scroll_offset) < 0.5:

            return

        self._info_panel_scroll_offset = new_offset

        self.update()



    def _clamp_info_panel_scroll(self, value: float) -> float:

        return max(0.0, min(float(value), max(0.0, self._info_panel_scroll_max)))



    def _wrapped_text_height(self, metrics: object, width: float, text: str) -> float:

        if not text:

            return 0.0

        text_flags = int(Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        bounded_rect = metrics.boundingRect(QRect(0, 0, max(1, int(width)), 10000), text_flags, text)

        return max(float(bounded_rect.height()), float(metrics.lineSpacing()))



    def _draw_wrapped_text(self, painter: QPainter, rect: QRectF, text: str) -> float:

        if not text:

            return 0.0

        text_flags = int(Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        metrics = painter.fontMetrics()

        height = self._wrapped_text_height(metrics, rect.width(), text)

        draw_rect = QRectF(rect.left(), rect.top(), rect.width(), height)

        painter.drawText(draw_rect, text_flags, text)

        return height