from __future__ import annotations



from math import hypot

from pathlib import Path



from matplotlib import dates as mdates

import numpy as np

import pyqtgraph as pg

from PySide6.QtCore import QEvent, QEventLoop, QPoint, QPointF, QRectF, Qt, Signal

from PySide6.QtGui import QAction, QActionGroup, QColor, QFont, QImage, QMouseEvent, QPainter, QPen, QPixmap

from PySide6.QtWidgets import QApplication, QColorDialog, QLabel, QMenu, QVBoxLayout, QWidget



from photometry_app.core.models import LightCurveSeries

from photometry_app.core.plotting import (

    LightCurveFitConfig,

    LightCurvePlotPayload,

    LightCurveRenderPoint,

    LightCurveSeriesLayer,

    _LIGHT_CURVE_SYMBOL_CHOICES,

    _is_magnitude_axis,

    build_light_curve_plot_payload,

    build_overview_light_curve_plot_payload,

    light_curve_series_style_key,

    light_curve_y_limits,

    light_curve_axis_label,

    resolve_light_curve_theme_colors,

)





class _LightCurveAxisItem(pg.AxisItem):

    def __init__(self, orientation: str = "bottom") -> None:

        super().__init__(orientation=orientation)

        self._mode = "datetime"

        self._index_labels: tuple[str, ...] = ()



    def set_mode(self, mode: str, index_labels: tuple[str, ...] = ()) -> None:

        self._mode = mode

        self._index_labels = index_labels

        self.picture = None

        self.update()



    def tickStrings(self, values: list[float], scale: float, spacing: float) -> list[str]:

        if self._mode == "datetime":

            labels: list[str] = []

            for value in values:

                try:

                    timestamp = mdates.num2date(value)

                except Exception:

                    labels.append("")

                    continue

                labels.append(timestamp.strftime("%m-%d\n%H:%M"))

            return labels

        if self._mode == "jd":

            return [f"{value:.3f}" for value in values]

        if self._mode == "phase":

            return [f"{value:.2f}" if -0.55 <= value <= 1.55 else "" for value in values]



        labels = []

        for value in values:

            index = int(round(value))

            if abs(value - index) > 0.2 or index < 0 or index >= len(self._index_labels):

                labels.append("")

                continue

            label = self._index_labels[index]

            labels.append(label if len(label) <= 18 else f"{label[:15]}...")

        return labels





class LightCurvePlotWidget(QWidget):

    pointSelected = Signal(object)

    segmentSelected = Signal(object)



    @classmethod
    def for_offscreen_export(cls) -> LightCurvePlotWidget:
        widget = cls()
        widget.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        widget.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        widget.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        widget.move(-20000, -20000)
        return widget

    def __init__(self, parent: QWidget | None = None) -> None:

        super().__init__(parent)

        self._axis_item = _LightCurveAxisItem(orientation="bottom")

        self._theme = "normal"

        self._theme_colors: dict[str, str] = {}

        self._plot_widget = pg.PlotWidget(axisItems={"bottom": self._axis_item}, background="w")

        self._plot_item = self._plot_widget.getPlotItem()

        self._plot_item.showGrid(x=True, y=True, alpha=0.25)

        self._plot_item.getViewBox().setMouseEnabled(x=True, y=True)

        self._plot_item.setMenuEnabled(False)

        self._plot_widget.setMinimumHeight(320)

        self._plot_widget.viewport().installEventFilter(self)



        self._status_label = QLabel("")

        self._status_label.hide()



        layout = QVBoxLayout()

        layout.setContentsMargins(0, 0, 0, 0)

        layout.addWidget(self._plot_widget)

        self.setLayout(layout)



        self._payload: LightCurvePlotPayload | None = None

        self._last_render_data_view_geometry: tuple[tuple[float, float], tuple[float, float], QRectF, bool] | None = None

        self._series: LightCurveSeries | None = None

        self._scatter_item: pg.ScatterPlotItem | None = None

        self._scatter_items: list[pg.ScatterPlotItem] = []

        self._legend_item: pg.LegendItem | None = None

        self._series_style_overrides: dict[str, dict[str, str]] = {}

        self._selected_style_key: str | None = None

        self._selected_point_item = pg.ScatterPlotItem(size=12, pen=pg.mkPen("#ff9f1c", width=1.5), brush=pg.mkBrush(255, 255, 255, 0))

        self._hover_radius_pixels = 18.0

        self._segment_selection_active = False

        self._auto_range_button = self._plot_item.autoBtn

        self._auto_range_button.setPixmap(self._plot_corner_button_pixmap("A"))

        self._hover_popup = QLabel(self._plot_widget.viewport())

        self._hover_popup.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self._hover_popup.hide()

        self._segment_reset_button_item = pg.ButtonItem(

            pixmap=self._plot_corner_button_pixmap("R"),

            width=int(self._auto_range_button.boundingRect().width()),

            parentItem=self._plot_item,

        )

        self._segment_reset_button_item.setOpacity(self._auto_range_button.opacity())

        self._segment_reset_button_item.clicked.connect(self._handle_segment_reset_button_clicked)

        self._segment_reset_button_item.hide()

        self._fit_period_badge = QLabel(self._plot_widget.viewport())

        self._fit_period_badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self._fit_period_badge.hide()

        self._segment_region_item: pg.LinearRegionItem | None = None

        self._ctrl_drag_active = False

        self._ctrl_drag_start_x: float | None = None

        self._phase_opacity_floor = 0.24

        self._recent_period_error_bars_only = False

        self._phase_period_days: float | None = None

        self._fit_period_badge_text: str | None = None

        self._error_bar_items: list[pg.ErrorBarItem] = []

        self._plot_item.addItem(self._selected_point_item)

        self._plot_widget.scene().sigMouseMoved.connect(self._handle_mouse_moved)

        self._position_plot_corner_buttons()

        self.set_theme("normal")



    def set_theme(self, theme: str, custom_colors: dict[str, str] | None = None) -> None:

        normalized_theme = str(theme).strip().lower()

        if normalized_theme not in {"normal", "dark", "dracula", "nord", "tokyo-night", "gruvbox", "catppuccin", "solarized-dark", "one-dark", "crimson", "blood-moon", "custom"}:

            normalized_theme = "normal"

        self._theme = normalized_theme

        self._theme_colors = self._resolve_theme_colors(normalized_theme, custom_colors)

        self._selected_point_item.setPen(pg.mkPen(self._theme_colors["selection_color"], width=1.5))



        self._plot_widget.setBackground(self._theme_colors["background_color"])

        self._plot_item.showGrid(x=True, y=True, alpha=float(self._theme_colors["grid_alpha"]))

        self._plot_item.getAxis("bottom").setTextPen(pg.mkPen(self._theme_colors["axis_color"]))

        self._plot_item.getAxis("bottom").setPen(pg.mkPen(self._theme_colors["axis_color"]))

        self._plot_item.getAxis("left").setTextPen(pg.mkPen(self._theme_colors["axis_color"]))

        self._plot_item.getAxis("left").setPen(pg.mkPen(self._theme_colors["axis_color"]))

        self._plot_item.setTitle(color=self._theme_colors["axis_color"])

        self._hover_popup.setStyleSheet(self._theme_colors["hover_style"])

        self._apply_fit_period_badge_theme()

        self._apply_segment_region_theme()



        if self._payload is not None:

            self._render_payload(self._payload)

    def set_fit_period_badge_text(self, text: str | None) -> None:

        normalized = str(text).strip() if text is not None else ""

        self._fit_period_badge_text = normalized or None

        self._update_fit_period_badge()

    def fit_period_badge_text(self) -> str | None:

        return self._fit_period_badge_text

    def current_payload(self):

        return self._payload

    def current_series(self) -> LightCurveSeries | None:

        return self._series

    def resizeEvent(self, event: object) -> None:

        super().resizeEvent(event)

        self._position_plot_corner_buttons()

        self._position_fit_period_badge()

    def _plot_corner_button_pixmap(self, label: str) -> QPixmap:

        pixmap = QPixmap(30, 30)

        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)

        try:

            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

            painter.setBrush(QColor("#252525"))

            painter.setPen(QPen(QColor("#f2dfb0"), 2.0))

            painter.drawEllipse(2, 2, 26, 26)

            font = QFont(self.font())

            font.setBold(True)

            font.setPixelSize(16)

            painter.setFont(font)

            painter.setPen(QColor("#f2dfb0"))

            painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, label)

        finally:

            painter.end()

        return pixmap

    def _position_plot_corner_buttons(self) -> None:

        auto_button_rect = self._auto_range_button.boundingRect()

        y_position = self._plot_item.size().height() - auto_button_rect.height()

        x_position = auto_button_rect.width() + 2.0 if self._auto_range_button.isVisible() else 0.0

        self._segment_reset_button_item.setPos(x_position, y_position)

    def _sync_plot_corner_buttons(self, force_hide: bool = False) -> None:

        try:

            self._plot_item.updateButtons()

        except RuntimeError:

            return

        self._position_plot_corner_buttons()

        if force_hide:

            self._segment_reset_button_item.hide()

            return

        should_show_reset = (

            self._segment_selection_active

            and bool(getattr(self._plot_item, "mouseHovering", False))

            and not bool(getattr(self._plot_item, "buttonsHidden", False))

        )

        if should_show_reset:

            self._segment_reset_button_item.show()

        else:

            self._segment_reset_button_item.hide()

    def _handle_segment_reset_button_clicked(self) -> None:

        self._segment_selection_active = False

        self._sync_plot_corner_buttons()

        self._hide_tooltip()

        self.segmentSelected.emit(None)

    def _apply_fit_period_badge_theme(self) -> None:

        background_color = QColor(self._theme_colors["background_color"])

        background_color.setAlpha(228)

        border_color = QColor(self._theme_colors["fit_curve_color"])

        border_color.setAlpha(235)

        text_color = QColor(self._theme_colors["axis_color"])

        self._fit_period_badge.setStyleSheet(

            "".join(

                [

                    f"background-color: rgba({background_color.red()}, {background_color.green()}, {background_color.blue()}, {background_color.alpha()});",

                    f"color: {text_color.name()};",

                    f"border: 1px solid {border_color.name()};",

                    "border-radius: 10px;",

                    "padding: 6px 10px;",

                    "font-weight: 600;",

                ]

            )

        )

        self._update_fit_period_badge()

    def _position_fit_period_badge(self) -> None:

        if not self._fit_period_badge.isVisible():

            return

        viewport = self._plot_widget.viewport()

        margin = 14

        x_position = max(margin, viewport.width() - self._fit_period_badge.width() - margin)

        self._fit_period_badge.move(x_position, margin)

    def _update_fit_period_badge(self) -> None:

        if self._payload is None or not self._payload.points or not self._fit_period_badge_text:

            self._fit_period_badge.hide()

            return

        self._fit_period_badge.setText(self._fit_period_badge_text)

        self._fit_period_badge.adjustSize()

        self._position_fit_period_badge()

        self._fit_period_badge.show()

        self._fit_period_badge.raise_()



    def _is_dark_theme(self) -> bool:

        return self._theme in {"dark", "dracula", "nord", "tokyo-night", "gruvbox", "catppuccin", "solarized-dark", "one-dark", "crimson", "blood-moon"}



    def _resolve_theme_colors(self, theme: str, custom_colors: dict[str, str] | None) -> dict[str, str]:

        return resolve_light_curve_theme_colors(theme, custom_colors)



    def eventFilter(self, watched: object, event: object) -> bool:

        if watched is self._plot_widget.viewport() and isinstance(event, QEvent):

            if isinstance(event, QMouseEvent):

                if (

                    event.type() == QEvent.Type.MouseButtonPress

                    and event.button() == Qt.MouseButton.RightButton

                    and not self._ctrl_drag_active

                ):

                    if self._show_series_style_menu_at(event.globalPosition().toPoint(), event.position()):

                        return True

                if event.type() == QEvent.Type.MouseButtonPress and self._should_begin_segment_drag(event):

                    start_x = self._view_x_from_mouse_event(event)

                    if start_x is not None:

                        self._begin_segment_drag(start_x)

                        return True

                if event.type() == QEvent.Type.MouseMove and self._ctrl_drag_active:

                    current_x = self._view_x_from_mouse_event(event)

                    if current_x is not None:

                        self._update_segment_drag(current_x)

                    return True

                if event.type() == QEvent.Type.MouseButtonRelease and self._ctrl_drag_active:

                    current_x = self._view_x_from_mouse_event(event)

                    self._finish_segment_drag(current_x)

                    return True

            if event.type() in {QEvent.Type.Leave, QEvent.Type.Hide}:

                if self._ctrl_drag_active:

                    self._cancel_segment_drag()

                self._hide_tooltip()

                self._sync_plot_corner_buttons(force_hide=True)

        return super().eventFilter(watched, event)

    def series_style_overrides(self) -> dict[str, dict[str, str]]:

        return {key: dict(value) for key, value in self._series_style_overrides.items()}

    def set_series_style_overrides(self, overrides: dict[str, dict[str, str]] | None) -> None:

        self._series_style_overrides = {
            str(key): dict(value)
            for key, value in dict(overrides or {}).items()
            if isinstance(value, dict)
        }



    def plot_series(

        self,

        series: LightCurveSeries,

        empty_message: str,

        fit_config: LightCurveFitConfig | None = None,

        y_axis_mode: str = "differential_magnitude",

        x_axis_mode: str = "datetime",

        phase_period_hours: float | None = None,

        phase_anchor_mode: str = "first_observation",

        phase_opacity_floor: float = 0.24,

        recent_period_error_bars_only: bool = False,

    ) -> None:

        self._series = series

        self._phase_opacity_floor = min(1.0, max(0.0, float(phase_opacity_floor)))

        self._recent_period_error_bars_only = bool(recent_period_error_bars_only)

        self._phase_period_days = (phase_period_hours / 24.0) if phase_period_hours is not None and phase_period_hours > 0 else None

        self._render_payload(

            build_light_curve_plot_payload(

                series,

                empty_message,

                fit_config=fit_config,

                y_axis_mode=y_axis_mode,

                x_axis_mode=x_axis_mode,

                phase_period_hours=phase_period_hours,

                phase_anchor_mode=phase_anchor_mode,

            )

        )



    def plot_overview(

        self,

        layers: list[object],

        empty_message: str,

        *,

        y_axis_mode: str = "differential_magnitude",

        x_axis_mode: str = "datetime",

        status_note: str | None = None,

        title: str | None = None,

    ) -> None:

        self._series = None

        self._phase_opacity_floor = 0.24

        self._recent_period_error_bars_only = False

        self._phase_period_days = None

        self._render_payload(

            build_overview_light_curve_plot_payload(

                layers,

                empty_message,

                title=title,

                y_axis_mode=y_axis_mode,

                x_axis_mode=x_axis_mode,

                status_note=status_note,

                style_overrides=self._series_style_overrides,

            )

        )



    def show_message(self, title: str, message: str, y_axis_mode: str = "differential_magnitude") -> None:

        self._series = None

        self._render_payload(

            LightCurvePlotPayload(

                title=title,

                y_axis_label=light_curve_axis_label(y_axis_mode),

                x_axis_label="Observation",

                x_axis_mode="index",

                invert_y=_is_magnitude_axis(y_axis_mode),

                empty_message=message,

            )

        )



    def reset_view(self) -> None:

        if self._payload is None:

            return

        x_limits = self._x_limits(self._payload)

        if x_limits is not None:

            self._plot_item.getViewBox().setXRange(*x_limits, padding=0.0)

        y_limits = light_curve_y_limits([point.y for point in self._payload.points], self._payload.fit_y_values)

        if y_limits is not None:

            self._plot_item.getViewBox().setYRange(*y_limits, padding=0.0)



    def clear_segment_selection(self) -> None:

        self._segment_selection_active = False

        self._cancel_segment_drag()

        self._hide_tooltip()

        self._sync_plot_corner_buttons()

        self._status_label.setText("Drag to pan, wheel to zoom, click a point to sync the matching measurement, and Ctrl+drag to isolate a segment.")



    def current_view_ranges(self) -> tuple[tuple[float, float], tuple[float, float]] | None:

        if self._payload is None:

            return None

        view_range = self._plot_item.getViewBox().viewRange()

        if len(view_range) != 2 or len(view_range[0]) != 2 or len(view_range[1]) != 2:

            return None

        return (

            (float(view_range[0][0]), float(view_range[0][1])),

            (float(view_range[1][0]), float(view_range[1][1])),

        )

    def data_view_geometry(self) -> tuple[tuple[float, float], tuple[float, float], QRectF, bool] | None:

        ranges = self.current_view_ranges()

        if ranges is None:

            return None

        view_box = self._plot_item.getViewBox()

        scene_rect = view_box.sceneBoundingRect()

        if scene_rect.isNull() or scene_rect.width() <= 1 or scene_rect.height() <= 1:

            return None

        top_left = self._plot_widget.mapFromScene(scene_rect.topLeft())

        bottom_right = self._plot_widget.mapFromScene(scene_rect.bottomRight())

        pixel_rect = QRectF(top_left, bottom_right).normalized()

        if pixel_rect.width() <= 1 or pixel_rect.height() <= 1:

            return None

        invert_y = True if self._payload is None else bool(self._payload.invert_y)

        return ranges[0], ranges[1], pixel_rect, invert_y

    def last_render_data_view_geometry(self) -> tuple[tuple[float, float], tuple[float, float], QRectF, bool] | None:

        return self._last_render_data_view_geometry



    def current_plot_aspect_ratio(self) -> float:

        size = self._plot_widget.size()

        width = max(1, int(size.width()))

        height = max(1, int(size.height()))

        return float(width) / float(height)


    def show_payload(

        self,

        payload: LightCurvePlotPayload,

        *,

        phase_opacity_floor: float | None = None,

        recent_period_error_bars_only: bool | None = None,

        series: LightCurveSeries | None = None,

    ) -> None:

        self._series = series

        if phase_opacity_floor is not None:

            self._phase_opacity_floor = min(1.0, max(0.0, float(phase_opacity_floor)))

        if recent_period_error_bars_only is not None:

            self._recent_period_error_bars_only = bool(recent_period_error_bars_only)

        self._render_payload(payload)


    def set_view_ranges(

        self,

        x_limits: tuple[float, float] | None = None,

        y_limits: tuple[float, float] | None = None,

    ) -> None:

        view_box = self._plot_item.getViewBox()

        if x_limits is not None:

            view_box.setXRange(float(x_limits[0]), float(x_limits[1]), padding=0.0)

        if y_limits is not None:

            view_box.setYRange(float(y_limits[0]), float(y_limits[1]), padding=0.0)


    def current_view_image(self, scale_factor: float = 3.0) -> QImage:

        size = self._plot_widget.size()

        source_width = max(1, int(size.width()))

        source_height = max(1, int(size.height()))

        resolved_scale = max(1.0, float(scale_factor))

        export_width = max(1, int(round(source_width * resolved_scale)))

        export_height = max(1, int(round(source_height * resolved_scale)))

        return self._render_current_view_image(source_width, source_height, export_width, export_height)

    def render_at_size(
        self,
        width: int,
        height: int,
        *,
        x_limits: tuple[float, float] | None = None,
        y_limits: tuple[float, float] | None = None,
    ) -> QImage:

        target_width = max(2, int(width))

        target_height = max(2, int(height))

        previous_min_height = self._plot_widget.minimumHeight()
        was_visible = self.isVisible()
        try:
            if not was_visible:
                self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
                self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
                self.setWindowFlags(
                    Qt.WindowType.Tool
                    | Qt.WindowType.FramelessWindowHint
                    | Qt.WindowType.WindowDoesNotAcceptFocus
                )
                self.move(-20000, -20000)
            self._plot_widget.setMinimumSize(1, 1)
            self.setMinimumSize(1, 1)
            self.setFixedSize(target_width, target_height)
            self._plot_widget.setFixedSize(target_width, target_height)
            self.show()
            QApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
            if x_limits is not None or y_limits is not None:
                self.set_view_ranges(x_limits=x_limits, y_limits=y_limits)
                QApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
            image = QImage(target_width, target_height, QImage.Format.Format_RGB888)
            background = QColor(self._theme_colors.get("background_color", "#111111"))
            image.fill(background if background.isValid() else QColor("#111111"))
            painter = QPainter(image)
            try:
                self._plot_widget.render(painter)
            finally:
                painter.end()
            self._last_render_data_view_geometry = self.data_view_geometry()
            if image.isNull():
                raise OSError("Unable to render the light curve at the requested size")
            return image
        finally:
            self.setMinimumSize(0, 0)
            self.setMaximumSize(16777215, 16777215)
            self._plot_widget.setMinimumSize(0, previous_min_height)
            self._plot_widget.setMaximumSize(16777215, 16777215)
            if not was_visible:
                self.hide()



    def export_current_view(self, output_path: str, scale_factor: float = 3.0) -> None:

        target_path = Path(output_path)

        size = self._plot_widget.size()

        source_width = max(1, int(size.width()))

        source_height = max(1, int(size.height()))

        resolved_scale = max(1.0, float(scale_factor))

        export_width = max(1, int(round(source_width * resolved_scale)))

        export_height = max(1, int(round(source_height * resolved_scale)))



        if target_path.suffix.lower() == ".svg":

            self._export_current_view_svg(str(target_path), source_width, source_height, export_width, export_height)

            return



        image = self.current_view_image(scale_factor=scale_factor)

        if target_path.suffix.lower() == ".pdf":

            self._export_current_view_pdf(str(target_path), image, export_width, export_height)

            return



        if not image.save(str(target_path)):

            raise OSError(f"Unable to save light-curve export to {target_path}")



    def _render_current_view_image(

        self,

        source_width: int,

        source_height: int,

        export_width: int,

        export_height: int,

    ) -> QImage:

        snapshot = self._plot_widget.grab().toImage()

        if snapshot.isNull():

            raise OSError("Unable to capture the current light-curve view")

        if export_width == source_width and export_height == source_height:

            return snapshot

        return snapshot.scaled(

            export_width,

            export_height,

            Qt.AspectRatioMode.IgnoreAspectRatio,

            Qt.TransformationMode.SmoothTransformation,

        )



    def _export_current_view_pdf(self, output_path: str, image: QImage, export_width: int, export_height: int) -> None:

        from PySide6.QtCore import QRectF, QSizeF

        from PySide6.QtGui import QPageSize, QPdfWriter



        writer = QPdfWriter(output_path)

        writer.setResolution(72)

        writer.setPageSize(QPageSize(QSizeF(float(export_width), float(export_height)), QPageSize.Unit.Point))

        painter = QPainter(writer)

        try:

            painter.drawImage(QRectF(0.0, 0.0, float(export_width), float(export_height)), image)

        finally:

            painter.end()



    def _export_current_view_svg(

        self,

        output_path: str,

        source_width: int,

        source_height: int,

        export_width: int,

        export_height: int,

    ) -> None:

        from PySide6.QtCore import QRect, QSize

        from PySide6.QtSvg import QSvgGenerator



        generator = QSvgGenerator()

        generator.setFileName(output_path)

        generator.setSize(QSize(export_width, export_height))

        generator.setViewBox(QRect(0, 0, export_width, export_height))

        generator.setTitle("Citizen Photometry Light Curve")



        painter = QPainter(generator)

        try:

            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

            painter.scale(export_width / source_width, export_height / source_height)

            self._plot_widget.render(painter)

        finally:

            painter.end()



    def _render_payload(self, payload: LightCurvePlotPayload) -> None:

        self._payload = payload

        self._plot_item.clear()

        self._plot_item.addItem(self._selected_point_item)

        self._selected_point_item.setData([], [])

        self._scatter_item = None

        self._scatter_items = []

        self._legend_item = None

        self._error_bar_items = []

        self._remove_segment_region_item()

        self._ctrl_drag_active = False

        self._ctrl_drag_start_x = None

        self._sync_plot_corner_buttons()



        self._axis_item.set_mode(payload.x_axis_mode, payload.index_labels)

        self._plot_item.setTitle(payload.title)

        label_color = self._theme_colors["axis_color"]

        self._plot_item.setLabel("left", payload.y_axis_label, color=label_color)

        self._plot_item.setLabel("bottom", payload.x_axis_label, color=label_color)

        self._plot_item.getViewBox().invertY(payload.invert_y)



        if not payload.points:

            text_item = pg.TextItem(

                payload.empty_message or "No data available.",

                anchor=(0.5, 0.5),

                color=self._theme_colors["empty_text_color"],

            )

            self._plot_item.addItem(text_item)

            text_item.setPos(0.5, 0.5)

            self._plot_item.getViewBox().setRange(xRange=(0.0, 1.0), yRange=(0.0, 1.0), padding=0.0)

            self._status_label.setText(payload.empty_message or "No data available.")

            self._update_fit_period_badge()

            self._hide_tooltip()

            self._sync_plot_corner_buttons()

            return



        if payload.layers:

            self._render_overview_layers(payload)

        else:

            if payload.fit_x_values is not None and payload.fit_y_values is not None:

                self._plot_item.addItem(

                    pg.PlotDataItem(payload.fit_x_values, payload.fit_y_values, pen=pg.mkPen(self._theme_colors["fit_curve_color"], width=1.8))

                )



            finite_error_points = [point for point in payload.points if point.y_error is not None]

            if payload.x_axis_mode == "phase" and self._recent_period_error_bars_only:

                finite_error_points = self._recent_period_error_points(finite_error_points)

            if finite_error_points:

                self._add_error_bar_items(payload, finite_error_points)



            style_key = self._current_series_style_key()

            override = self._series_style_overrides.get(style_key or "") or {}

            symbol = str(override.get("symbol") or "o")

            color = str(override.get("color") or self._theme_colors["point_brush"])

            pen_color = str(override.get("color") or self._theme_colors["point_pen"])

            spots = self._scatter_spots(payload)

            self._scatter_item = pg.ScatterPlotItem(

                spots=spots,

                size=8,

                symbol=symbol,

                pen=pg.mkPen(pen_color, width=1.0),

                brush=pg.mkBrush(color),

                hoverPen=pg.mkPen(self._theme_colors["hover_pen"], width=1.6),

                hoverBrush=pg.mkBrush(self._theme_colors["hover_brush"]),

            )

            self._scatter_item.sigClicked.connect(self._handle_scatter_clicked)

            self._plot_item.addItem(self._scatter_item)



        self._plot_item.enableAutoRange()

        x_limits = self._x_limits(payload)

        if x_limits is not None:

            self._plot_item.getViewBox().setXRange(*x_limits, padding=0.0)

        y_limits = light_curve_y_limits([point.y for point in payload.points], payload.fit_y_values)

        if y_limits is not None:

            self._plot_item.getViewBox().setYRange(*y_limits, padding=0.0)

        status_text = (

            "Drag to pan, wheel to zoom, click a point to sync the matching measurement, "

            "right-click a series for Icon/Color, and Ctrl+drag to isolate a segment."

        )

        if payload.status_note:

            status_text = f"{payload.status_note} {status_text}"

        self._status_label.setText(status_text)

        self._update_fit_period_badge()

        self._sync_plot_corner_buttons()



    def _render_overview_layers(self, payload: LightCurvePlotPayload) -> None:

        if payload.show_legend:

            self._legend_item = self._plot_item.addLegend(offset=(10, 10))

        point_offset = 0

        for layer in payload.layers:

            color, symbol = self._resolved_layer_style(layer)

            finite_error_points = [point for point in layer.points if point.y_error is not None]

            if finite_error_points:

                error_item = pg.ErrorBarItem(

                    x=np.asarray([point.x for point in finite_error_points], dtype=float),

                    y=np.asarray([point.y for point in finite_error_points], dtype=float),

                    top=np.asarray([point.y_error or 0.0 for point in finite_error_points], dtype=float),

                    bottom=np.asarray([point.y_error or 0.0 for point in finite_error_points], dtype=float),

                    beam=self._error_bar_beam_width(payload),

                    pen=pg.mkPen(color, width=1.0),

                )

                self._plot_item.addItem(error_item)

                self._error_bar_items.append(error_item)

            spots = [

                {

                    "pos": (point.x, point.y),

                    "data": point_offset + index,

                    "pen": pg.mkPen(color, width=1.2 if layer.role == "target" else 0.8),

                    "brush": pg.mkBrush(QColor(color)),

                }

                for index, point in enumerate(layer.points)

            ]

            scatter = pg.ScatterPlotItem(

                spots=spots,

                size=layer.size,

                symbol=symbol,

                hoverPen=pg.mkPen(self._theme_colors["hover_pen"], width=1.6),

                hoverBrush=pg.mkBrush(self._theme_colors["hover_brush"]),

                name=layer.label,

            )

            scatter.sigClicked.connect(self._handle_scatter_clicked)

            self._plot_item.addItem(scatter)

            self._scatter_items.append(scatter)

            if self._scatter_item is None:

                self._scatter_item = scatter

            point_offset += len(layer.points)



    def _should_begin_segment_drag(self, event: QMouseEvent) -> bool:

        return (

            event.button() == Qt.MouseButton.LeftButton

            and bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)

            and self._payload is not None

            and bool(self._payload.points)

        )



    def _view_x_from_mouse_event(self, event: QMouseEvent) -> float | None:

        if self._payload is None:

            return None

        scene_position = self._plot_widget.mapToScene(event.position().toPoint())

        if not self._plot_widget.sceneBoundingRect().contains(scene_position):

            return None

        view_position = self._plot_item.getViewBox().mapSceneToView(scene_position)

        x_value = float(view_position.x())

        return x_value if np.isfinite(x_value) else None



    def _begin_segment_drag(self, start_x: float) -> None:

        self._ctrl_drag_active = True

        self._ctrl_drag_start_x = start_x

        self._remove_segment_region_item()

        self._segment_region_item = pg.LinearRegionItem(

            values=(start_x, start_x),

            orientation="vertical",

            movable=False,

        )

        self._apply_segment_region_theme()

        self._segment_region_item.setZValue(20)

        self._plot_item.addItem(self._segment_region_item)

        self._status_label.setText("Selecting light-curve segment. Release to exclude points outside the dragged window.")

        self._hide_tooltip()



    def _update_segment_drag(self, current_x: float) -> None:

        if not self._ctrl_drag_active or self._ctrl_drag_start_x is None or self._segment_region_item is None:

            return

        lower_bound = min(self._ctrl_drag_start_x, current_x)

        upper_bound = max(self._ctrl_drag_start_x, current_x)

        self._segment_region_item.setRegion((lower_bound, upper_bound))



    def _finish_segment_drag(self, current_x: float | None) -> None:

        if not self._ctrl_drag_active or self._ctrl_drag_start_x is None or self._payload is None:

            self._cancel_segment_drag()

            return

        if current_x is None:

            self._cancel_segment_drag()

            return

        lower_bound = min(self._ctrl_drag_start_x, current_x)

        upper_bound = max(self._ctrl_drag_start_x, current_x)

        self._ctrl_drag_active = False

        self._ctrl_drag_start_x = None

        self._remove_segment_region_item()



        selected_points = [point for point in self._payload.points if lower_bound <= point.x <= upper_bound]

        if len(selected_points) < 2:

            self._segment_selection_active = False

            self._sync_plot_corner_buttons()

            self._status_label.setText("Ctrl+drag across at least two points to isolate a light-curve segment.")

            self.segmentSelected.emit(None)

            return

        if len(selected_points) >= len(self._payload.points):

            self._segment_selection_active = False

            self._sync_plot_corner_buttons()

            self._status_label.setText("The dragged window covers the full dataset; showing all points.")

            self.segmentSelected.emit(None)

            return

        self._segment_selection_active = True

        self._sync_plot_corner_buttons()

        self._status_label.setText(f"Selected {len(selected_points)} point(s); points outside the dragged window were excluded from the current light-curve analysis.")

        self.segmentSelected.emit(tuple(self._measurement_key(point) for point in selected_points))



    def _cancel_segment_drag(self) -> None:

        self._ctrl_drag_active = False

        self._ctrl_drag_start_x = None

        self._remove_segment_region_item()



    def _remove_segment_region_item(self) -> None:

        if self._segment_region_item is None:

            return

        try:

            self._plot_item.removeItem(self._segment_region_item)

        except Exception:

            pass

        self._segment_region_item = None



    def _apply_segment_region_theme(self) -> None:

        if self._segment_region_item is None:

            return

        selection_color = QColor(self._theme_colors["selection_color"])

        fill_color = QColor(selection_color)

        fill_color.setAlpha(90 if self._is_dark_theme() else 70)

        edge_color = QColor(selection_color)

        edge_color.setAlpha(220)

        hover_fill_color = QColor(selection_color)

        hover_fill_color.setAlpha(120 if self._is_dark_theme() else 95)

        self._segment_region_item.setBrush(pg.mkBrush(fill_color))

        self._segment_region_item.setHoverBrush(pg.mkBrush(hover_fill_color))

        for line in self._segment_region_item.lines:

            line.setPen(pg.mkPen(edge_color, width=2.0))

            line.setHoverPen(pg.mkPen(edge_color.lighter(120), width=2.4))



    def _error_bar_beam_width(self, payload: LightCurvePlotPayload) -> float:

        if payload.x_axis_mode == "phase":

            return 0.01

        if len(payload.points) < 2:

            return 0.2

        x_values = [point.x for point in payload.points]

        span = max(x_values) - min(x_values)

        if payload.x_axis_mode in {"datetime", "jd"}:

            return max(span / 300.0, 1.0 / (24.0 * 60.0))

        return max(span / 50.0, 0.2)



    def _scatter_spots(self, payload: LightCurvePlotPayload) -> list[dict[str, object]]:

        style_key = self._current_series_style_key()

        override = self._series_style_overrides.get(style_key or "") or {}

        default_pen = str(override.get("color") or self._theme_colors["point_pen"])

        default_brush = str(override.get("color") or self._theme_colors["point_brush"])

        if payload.x_axis_mode != "phase":

            return [{"pos": (point.x, point.y), "data": index} for index, point in enumerate(payload.points)]



        alpha_by_index = self._phase_alpha_by_point_index(payload)

        maximum_alpha = 255



        spots: list[dict[str, object]] = []

        for index, point in enumerate(payload.points):

            alpha = alpha_by_index.get(index, maximum_alpha)

            pen_color = QColor(default_pen)

            brush_color = QColor(default_brush)

            pen_color.setAlpha(alpha)

            brush_color.setAlpha(alpha)

            spots.append(

                {

                    "pos": (point.x, point.y),

                    "data": index,

                    "pen": pg.mkPen(pen_color, width=1.0),

                    "brush": pg.mkBrush(brush_color),

                }

            )

        return spots



    def _phase_alpha_by_point_index(self, payload: LightCurvePlotPayload) -> dict[int, int]:

        if payload.x_axis_mode != "phase":

            return {}



        timed_indices = [

            (index, point.source_point.observation_time)

            for index, point in enumerate(payload.points)

            if point.source_point.observation_time is not None

        ]

        if len(timed_indices) < 2:

            return {}



        ordered_indices = [index for index, _observation_time in sorted(timed_indices, key=lambda item: item[1])]

        alpha_by_index: dict[int, int] = {}

        minimum_alpha = int(round(255 * self._phase_opacity_floor))

        maximum_alpha = 255

        denominator = max(1, len(ordered_indices) - 1)

        for rank, index in enumerate(ordered_indices):

            alpha_fraction = rank / denominator

            alpha_by_index[index] = int(round(minimum_alpha + ((maximum_alpha - minimum_alpha) * alpha_fraction)))



        return alpha_by_index



    def _add_error_bar_items(self, payload: LightCurvePlotPayload, finite_error_points: list[LightCurveRenderPoint]) -> None:

        beam_width = self._error_bar_beam_width(payload)

        if payload.x_axis_mode != "phase":

            error_item = pg.ErrorBarItem(

                x=np.asarray([point.x for point in finite_error_points], dtype=float),

                y=np.asarray([point.y for point in finite_error_points], dtype=float),

                top=np.asarray([point.y_error or 0.0 for point in finite_error_points], dtype=float),

                bottom=np.asarray([point.y_error or 0.0 for point in finite_error_points], dtype=float),

                beam=beam_width,

                pen=pg.mkPen(self._theme_colors["error_bar_color"], width=1.0),

            )

            self._error_bar_items.append(error_item)

            self._plot_item.addItem(error_item)

            return



        alpha_by_index = self._phase_alpha_by_point_index(payload)

        point_index_lookup = {id(point): index for index, point in enumerate(payload.points)}

        for point in finite_error_points:

            point_index = point_index_lookup.get(id(point))

            alpha = alpha_by_index.get(point_index, 255)

            error_color = QColor(self._theme_colors["error_bar_color"])

            error_color.setAlpha(alpha)

            error_item = pg.ErrorBarItem(

                x=np.asarray([point.x], dtype=float),

                y=np.asarray([point.y], dtype=float),

                top=np.asarray([point.y_error or 0.0], dtype=float),

                bottom=np.asarray([point.y_error or 0.0], dtype=float),

                beam=beam_width,

                pen=pg.mkPen(error_color, width=1.0),

            )

            self._error_bar_items.append(error_item)

            self._plot_item.addItem(error_item)



    def _recent_period_error_points(self, points: list[LightCurveRenderPoint]) -> list[LightCurveRenderPoint]:

        if self._phase_period_days is None or self._phase_period_days <= 0:

            return points

        observation_times = [point.source_point.observation_time for point in points if point.source_point.observation_time is not None]

        if not observation_times:

            return points

        latest_observation = max(observation_times)

        latest_jd = float(mdates.date2num(latest_observation))

        period_days = float(self._phase_period_days)

        recent_points = [

            point

            for point in points

            if point.source_point.observation_time is not None

            and (latest_jd - float(mdates.date2num(point.source_point.observation_time))) <= period_days

        ]

        return recent_points or points



    def _x_limits(self, payload: LightCurvePlotPayload) -> tuple[float, float] | None:

        if payload.x_limits is not None:

            return payload.x_limits

        x_values = [float(point.x) for point in payload.points if np.isfinite(point.x)]

        if payload.fit_x_values is not None:

            x_values.extend(float(value) for value in np.asarray(payload.fit_x_values, dtype=float) if np.isfinite(value))

        if not x_values:

            return None

        lower_bound = min(x_values)

        upper_bound = max(x_values)

        if upper_bound <= lower_bound:

            padding = 0.5 if payload.x_axis_mode == "index" else 0.02

            return (lower_bound - padding, upper_bound + padding)

        padding = (upper_bound - lower_bound) * 0.03

        if payload.x_axis_mode == "phase":

            padding = max(padding, 0.02)

        elif payload.x_axis_mode == "index":

            padding = max(padding, 0.5)

        else:

            padding = max(padding, 1.0 / (24.0 * 60.0))

        return (lower_bound - padding, upper_bound + padding)



    def _handle_scatter_clicked(self, _item: object, points: list[object], _event: object) -> None:

        if not points or self._payload is None:

            return

        point_index = points[0].data()

        if not isinstance(point_index, int) or point_index < 0 or point_index >= len(self._payload.points):

            return

        payload_point = self._payload.points[point_index]

        self._selected_style_key = self._style_key_for_point_index(point_index)

        self._selected_point_item.setData([payload_point.x], [payload_point.y])

        self._status_label.setText(self._format_point_summary(payload_point, point_index=point_index))

        self.pointSelected.emit(self._measurement_key(payload_point, point_index=point_index))

    def _resolved_layer_style(self, layer: LightCurveSeriesLayer) -> tuple[str, str]:

        override = self._series_style_overrides.get(layer.style_key) or {}

        color = str(override.get("color") or layer.color)

        symbol = str(override.get("symbol") or layer.symbol)

        return color, symbol

    def _current_series_style_key(self) -> str | None:

        if self._series is None:

            return self._selected_style_key

        return light_curve_series_style_key("target", self._series.source_id, self._series.filter_name)

    def _style_key_for_point_index(self, point_index: int) -> str | None:

        if self._payload is None:

            return None

        if self._payload.layers:

            offset = 0

            for layer in self._payload.layers:

                next_offset = offset + len(layer.points)

                if offset <= point_index < next_offset:

                    return layer.style_key or light_curve_series_style_key(layer.role, layer.source_id, layer.filter_name)

                offset = next_offset

            return None

        return self._current_series_style_key()

    def _layer_for_point_index(self, point_index: int) -> LightCurveSeriesLayer | None:

        if self._payload is None or not self._payload.layers:

            return None

        offset = 0

        for layer in self._payload.layers:

            next_offset = offset + len(layer.points)

            if offset <= point_index < next_offset:

                return layer

            offset = next_offset

        return None

    def _point_index_for_render_point(self, target: LightCurveRenderPoint) -> int | None:

        if self._payload is None:

            return None

        for index, point in enumerate(self._payload.points):

            if point is target or (

                point.x == target.x

                and point.y == target.y

                and point.source_point.file_path == target.source_point.file_path

            ):

                return index

        return None

    def _show_series_style_menu_at(self, global_pos: QPoint, local_pos: QPointF) -> bool:

        if self._payload is None or not self._payload.points:

            return False

        scene_position = self._plot_widget.mapToScene(local_pos.toPoint())

        nearest_point = self._nearest_point(scene_position)

        if nearest_point is None:

            return False

        point_index = self._point_index_for_render_point(nearest_point)

        if point_index is None:

            return False

        style_key = self._style_key_for_point_index(point_index)

        if not style_key:

            return False

        layer = self._layer_for_point_index(point_index)

        if layer is not None:

            current_color, current_symbol = self._resolved_layer_style(layer)

            series_label = layer.label

        else:

            override = self._series_style_overrides.get(style_key) or {}

            current_symbol = str(override.get("symbol") or "o")

            current_color = str(override.get("color") or self._theme_colors["point_brush"])

            series_label = (

                f"{self._series.source_name} [{self._series.filter_name}]"

                if self._series is not None

                else "Series"

            )

        self._selected_style_key = style_key

        self._selected_point_item.setData([nearest_point.x], [nearest_point.y])

        self._status_label.setText(self._format_point_summary(nearest_point, point_index=point_index))

        menu = QMenu(self)

        menu.setTitle(series_label)

        header = menu.addAction(series_label)

        header.setEnabled(False)

        menu.addSeparator()

        icon_menu = menu.addMenu("Icon")

        symbol_group = QActionGroup(menu)

        symbol_group.setExclusive(True)

        for label, symbol in _LIGHT_CURVE_SYMBOL_CHOICES:

            action = QAction(label, menu)

            action.setCheckable(True)

            action.setChecked(symbol == current_symbol)

            action.setData(symbol)

            symbol_group.addAction(action)

            icon_menu.addAction(action)

            action.triggered.connect(lambda checked=False, key=style_key, value=symbol: self._set_series_symbol(key, value))

        color_action = menu.addAction("Color…")

        color_action.triggered.connect(lambda: self._choose_series_color(style_key, current_color))

        menu.exec(global_pos)

        return True

    def _set_series_symbol(self, style_key: str, symbol: str) -> None:

        override = dict(self._series_style_overrides.get(style_key) or {})

        override["symbol"] = str(symbol)

        self._series_style_overrides[style_key] = override

        if self._payload is not None:

            self._render_payload(self._payload)

    def _choose_series_color(self, style_key: str, current_color: str) -> None:

        initial = QColor(current_color)

        if not initial.isValid():

            initial = QColor(self._theme_colors["point_brush"])

        selected = QColorDialog.getColor(initial, self, "Series Color")

        if not selected.isValid():

            return

        override = dict(self._series_style_overrides.get(style_key) or {})

        override["color"] = selected.name(QColor.NameFormat.HexRgb)

        self._series_style_overrides[style_key] = override

        if self._payload is not None:

            self._render_payload(self._payload)



    def _handle_mouse_moved(self, scene_position: object) -> None:

        self._sync_plot_corner_buttons()

        if self._payload is None or not self._payload.points or not isinstance(scene_position, QPointF):

            self._hide_tooltip()

            return

        if not self._plot_widget.sceneBoundingRect().contains(scene_position):

            self._hide_tooltip()

            return

        nearest_point = self._nearest_point(scene_position)

        if nearest_point is None:

            self._status_label.setText(

                "Drag to pan, wheel to zoom, click a point to sync the matching measurement, and right-click a series for Icon/Color."

            )

            self._hide_tooltip()

            return

        point_index = self._point_index_for_render_point(nearest_point)

        self._status_label.setText(self._format_point_summary(nearest_point, point_index=point_index))

        self._show_tooltip(self._format_point_tooltip(nearest_point), scene_position)



    def _nearest_point(self, scene_position: QPointF) -> LightCurveRenderPoint | None:

        if self._payload is None:

            return None

        best_point: LightCurveRenderPoint | None = None

        best_distance = self._hover_radius_pixels

        view_box = self._plot_item.getViewBox()

        for point in self._payload.points:

            point_scene = view_box.mapViewToScene(QPointF(point.x, point.y))

            distance = hypot(point_scene.x() - scene_position.x(), point_scene.y() - scene_position.y())

            if distance <= best_distance:

                best_distance = distance

                best_point = point

        return best_point



    def _format_point_summary(self, point: LightCurveRenderPoint, *, point_index: int | None = None) -> str:

        observation = point.source_point.observation_time.isoformat(sep=" ") if point.source_point.observation_time else point.source_point.file_path.name

        error_text = f" +/- {point.y_error:.4f}" if point.y_error is not None else ""

        source_name = self._series.source_name if self._series is not None else "Series"

        filter_name = self._series.filter_name if self._series is not None else "-"

        if point_index is not None:

            layer = self._layer_for_point_index(point_index)

            if layer is not None:

                source_name = layer.label

                filter_name = layer.filter_name or filter_name

        x_value = self._format_x_value(point)

        x_label = "JD" if self._payload is not None and self._payload.x_axis_mode == "jd" else "X"

        return f"{source_name} | {observation} | {x_label}={x_value} | {point.source_point.file_path.name} | {point.y:.4f}{error_text}"



    def _format_x_value(self, point: LightCurveRenderPoint) -> str:

        if self._payload is None:

            return f"{point.x:.3f}"

        if self._payload.x_axis_mode == "datetime":

            try:

                return mdates.num2date(point.x).strftime("%Y-%m-%d %H:%M:%S")

            except Exception:

                return f"{point.x:.3f}"

        if self._payload.x_axis_mode == "jd":

            return f"{point.x:.3f}"

        if self._payload.x_axis_mode == "phase":

            return f"{point.x:.3f}"

        return f"{point.x:.3f}"



    def _format_point_tooltip(self, point: LightCurveRenderPoint) -> str:

        x_value = self._format_x_value(point)

        x_label = "Phase" if self._payload is not None and self._payload.x_axis_mode == "phase" else ("JD" if self._payload is not None and self._payload.x_axis_mode == "jd" else "X")

        y_text = f"{point.y:.2f}"

        if point.y_error is not None:

            y_text = f"{y_text}\N{PLUS-MINUS SIGN}{point.y_error:.2f}"

        return f"{x_label}:{x_value}\ny:{y_text}"



    def _measurement_key(self, point: LightCurveRenderPoint, *, point_index: int | None = None) -> tuple[str, str, str, str]:

        source_id = self._series.source_id if self._series is not None else ""

        filter_name = self._series.filter_name if self._series is not None else ""

        if point_index is not None:

            layer = self._layer_for_point_index(point_index)

            if layer is not None:

                source_id = layer.source_id or source_id

                filter_name = layer.filter_name or filter_name

        observation = point.source_point.observation_time.isoformat(sep=" ") if point.source_point.observation_time else "-"

        return (source_id, filter_name, point.source_point.file_path.name, observation)



    def _show_tooltip(self, text: str, scene_position: QPointF) -> None:

        self._hover_popup.setText(text)

        self._hover_popup.adjustSize()

        viewport_point = self._plot_widget.mapFromScene(scene_position)

        x_position = viewport_point.x() + 14

        y_position = viewport_point.y() + 18

        maximum_x = max(0, self._plot_widget.viewport().width() - self._hover_popup.width() - 4)

        maximum_y = max(0, self._plot_widget.viewport().height() - self._hover_popup.height() - 4)

        self._hover_popup.move(min(x_position, maximum_x), min(y_position, maximum_y))

        self._hover_popup.show()

        self._hover_popup.raise_()



    def _hide_tooltip(self) -> None:

        self._hover_popup.hide()