from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget


_CURVE_MARGIN = 12.0
_CURVE_BOTTOM_SPACE = 18.0
_CURVE_POINT_RADIUS = 5.0
_CURVE_HIT_RADIUS = 10.0
_MIN_CURVE_POINT_GAP = 0.01
_MAX_CURVE_POINTS = 12


class HistogramCurvesWidget(QWidget):
    curveChanged = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(150)
        self.setMinimumWidth(260)
        self.setMouseTracking(True)
        self.setToolTip("Drag the curve points. Click the graph to add a point, or right-click a point to remove it.")
        self._histogram = np.zeros(128, dtype=float)
        self._points: list[tuple[float, float]] = [(0.0, 0.0), (1.0, 1.0)]
        self._active_index: int | None = None

    def clear_histogram(self) -> None:
        self._histogram = np.zeros_like(self._histogram)
        self.update()

    def set_histogram_data(self, normalized_data: np.ndarray | None) -> None:
        if normalized_data is None:
            self.clear_histogram()
            return
        flat = np.asarray(normalized_data, dtype=float).ravel()
        finite = flat[np.isfinite(flat)]
        if finite.size == 0:
            self.clear_histogram()
            return
        if finite.size > 65536:
            stride = max(1, finite.size // 65536)
            finite = finite[::stride]
        clipped = np.clip(finite, 0.0, 1.0)
        histogram, _edges = np.histogram(clipped, bins=self._histogram.size, range=(0.0, 1.0))
        self._histogram = histogram.astype(float)
        self.update()

    def set_curve_points(self, points: tuple[tuple[float, float], ...] | list[tuple[float, float]], *, emit_signal: bool = False) -> None:
        sanitized = self._sanitize_points(points)
        changed = sanitized != self._points
        self._points = sanitized
        self.update()
        if changed and emit_signal:
            self.curveChanged.emit(self.curve_points())

    def curve_points(self) -> tuple[tuple[float, float], ...]:
        return tuple(self._points)

    def reset_curve(self) -> None:
        self.set_curve_points(((0.0, 0.0), (1.0, 1.0)), emit_signal=True)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#1f2125"))

        plot_rect = self._plot_rect()
        painter.fillRect(plot_rect, QColor("#262a30"))
        painter.setPen(QPen(QColor("#4c5158"), 1.0))
        painter.drawRect(plot_rect)

        grid_pen = QPen(QColor("#3a3f46"), 1.0)
        painter.setPen(grid_pen)
        for index in range(1, 4):
            x_position = plot_rect.left() + (plot_rect.width() * index / 4.0)
            y_position = plot_rect.top() + (plot_rect.height() * index / 4.0)
            painter.drawLine(QPointF(x_position, plot_rect.top()), QPointF(x_position, plot_rect.bottom()))
            painter.drawLine(QPointF(plot_rect.left(), y_position), QPointF(plot_rect.right(), y_position))

        self._draw_histogram(painter, plot_rect)

        painter.setPen(QPen(QColor("#6b7280"), 1.0, Qt.PenStyle.DashLine))
        painter.drawLine(plot_rect.bottomLeft(), plot_rect.topRight())

        curve_path = QPainterPath()
        for index, point in enumerate(self._points):
            widget_point = self._point_to_widget(point, plot_rect)
            if index == 0:
                curve_path.moveTo(widget_point)
            else:
                curve_path.lineTo(widget_point)
        painter.setPen(QPen(QColor("#ffcf66"), 2.0))
        painter.drawPath(curve_path)

        for index, point in enumerate(self._points):
            widget_point = self._point_to_widget(point, plot_rect)
            is_active = index == self._active_index
            fill = QColor("#ffcf66") if is_active else QColor("#f8fafc")
            stroke = QColor("#111827") if is_active else QColor("#0f172a")
            painter.setPen(QPen(stroke, 1.2))
            painter.setBrush(fill)
            painter.drawEllipse(widget_point, _CURVE_POINT_RADIUS, _CURVE_POINT_RADIUS)
        painter.end()

    def mousePressEvent(self, event) -> None:
        plot_rect = self._plot_rect()
        if event.button() == Qt.MouseButton.RightButton:
            index = self._nearest_point_index(event.position(), plot_rect)
            if index is not None and 0 < index < len(self._points) - 1:
                self._points.pop(index)
                self._active_index = None
                self.update()
                self.curveChanged.emit(self.curve_points())
                event.accept()
                return
        if event.button() != Qt.MouseButton.LeftButton or not plot_rect.contains(event.position()):
            super().mousePressEvent(event)
            return
        index = self._nearest_point_index(event.position(), plot_rect)
        if index is None and len(self._points) < _MAX_CURVE_POINTS:
            curve_point = self._curve_from_widget(event.position(), plot_rect)
            self._points.append(curve_point)
            self._points = self._sanitize_points(self._points)
            index = self._nearest_point_index(event.position(), plot_rect)
            self.curveChanged.emit(self.curve_points())
        self._active_index = index
        if self._active_index is not None:
            self._move_active_point(event.position(), plot_rect)
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._active_index is None:
            super().mouseMoveEvent(event)
            return
        self._move_active_point(event.position(), self._plot_rect())
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._active_index = None
            self.update()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _plot_rect(self) -> QRectF:
        return QRectF(
            _CURVE_MARGIN,
            8.0,
            max(1.0, self.width() - (2.0 * _CURVE_MARGIN)),
            max(1.0, self.height() - (_CURVE_BOTTOM_SPACE + 12.0)),
        )

    def _draw_histogram(self, painter: QPainter, plot_rect: QRectF) -> None:
        histogram_max = float(np.max(self._histogram)) if self._histogram.size else 0.0
        if histogram_max <= 0.0:
            return
        bar_width = plot_rect.width() / float(self._histogram.size)
        for index, value in enumerate(self._histogram):
            if value <= 0:
                continue
            bar_height = (value / histogram_max) * plot_rect.height()
            bar_rect = QRectF(
                plot_rect.left() + (index * bar_width),
                plot_rect.bottom() - bar_height,
                max(1.0, bar_width - 1.0),
                bar_height,
            )
            painter.fillRect(bar_rect, QColor("#65748a"))

    def _point_to_widget(self, point: tuple[float, float], plot_rect: QRectF) -> QPointF:
        x_value, y_value = point
        return QPointF(
            plot_rect.left() + (min(1.0, max(0.0, x_value)) * plot_rect.width()),
            plot_rect.bottom() - (min(1.0, max(0.0, y_value)) * plot_rect.height()),
        )

    def _curve_from_widget(self, point: QPointF, plot_rect: QRectF) -> tuple[float, float]:
        x_value = 0.0 if plot_rect.width() <= 0 else (point.x() - plot_rect.left()) / plot_rect.width()
        y_value = 0.0 if plot_rect.height() <= 0 else (plot_rect.bottom() - point.y()) / plot_rect.height()
        return (min(1.0, max(0.0, x_value)), min(1.0, max(0.0, y_value)))

    def _nearest_point_index(self, position: QPointF, plot_rect: QRectF) -> int | None:
        best_index: int | None = None
        best_distance = _CURVE_HIT_RADIUS
        for index, point in enumerate(self._points):
            widget_point = self._point_to_widget(point, plot_rect)
            distance = float(((position.x() - widget_point.x()) ** 2 + (position.y() - widget_point.y()) ** 2) ** 0.5)
            if distance <= best_distance:
                best_index = index
                best_distance = distance
        return best_index

    def _move_active_point(self, position: QPointF, plot_rect: QRectF) -> None:
        if self._active_index is None:
            return
        x_value, y_value = self._curve_from_widget(position, plot_rect)
        index = self._active_index
        if index > 0:
            x_value = max(x_value, self._points[index - 1][0] + _MIN_CURVE_POINT_GAP)
        else:
            x_value = max(0.0, x_value)
        if index < len(self._points) - 1:
            x_value = min(x_value, self._points[index + 1][0] - _MIN_CURVE_POINT_GAP)
        else:
            x_value = min(1.0, x_value)
        x_value = min(1.0, max(0.0, x_value))
        new_point = (x_value, y_value)
        if new_point == self._points[index]:
            return
        self._points[index] = new_point
        self.update()
        self.curveChanged.emit(self.curve_points())

    def _sanitize_points(self, points: tuple[tuple[float, float], ...] | list[tuple[float, float]]) -> list[tuple[float, float]]:
        sanitized: list[tuple[float, float]] = []
        for raw_point in points:
            try:
                raw_x, raw_y = raw_point
                x_value = min(1.0, max(0.0, float(raw_x)))
                y_value = min(1.0, max(0.0, float(raw_y)))
            except (TypeError, ValueError):
                continue
            if not np.isfinite(x_value) or not np.isfinite(y_value):
                continue
            sanitized.append((x_value, y_value))
        if len(sanitized) < 2:
            return [(0.0, 0.0), (1.0, 1.0)]
        sanitized.sort(key=lambda point: point[0])
        deduplicated: list[tuple[float, float]] = []
        for x_value, y_value in sanitized[:_MAX_CURVE_POINTS]:
            if deduplicated and x_value - deduplicated[-1][0] < _MIN_CURVE_POINT_GAP:
                deduplicated[-1] = (deduplicated[-1][0], y_value)
            else:
                deduplicated.append((x_value, y_value))
        if len(deduplicated) < 2:
            return [(0.0, 0.0), (1.0, 1.0)]
        return deduplicated