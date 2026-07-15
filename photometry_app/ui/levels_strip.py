from __future__ import annotations

from typing import Literal

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QWidget


_HANDLE_HALF_WIDTH = 6.0
_HANDLE_HEIGHT = 10.0
_LEVEL_MARGIN = 8.0
_LEVEL_BOTTOM_SPACE = 16.0
_MIN_LEVEL_SPAN = 0.04


class HistogramLevelsStrip(QWidget):
    levelsChanged = Signal(float, float, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(46)
        self.setMinimumWidth(180)
        self.setMouseTracking(True)
        self.setToolTip("Drag the black, midtone, and white handles to adjust the display stretch for the current image preview.")
        self._histogram = np.zeros(96, dtype=float)
        self._black_point = 0.0
        self._midtone_point = 0.5
        self._white_point = 1.0
        self._active_handle: Literal["black", "mid", "white"] | None = None

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

    def set_levels(self, black_point: float, midtone_point: float, white_point: float, *, emit_signal: bool = False) -> None:
        black = max(0.0, min(1.0 - _MIN_LEVEL_SPAN, float(black_point)))
        white = min(1.0, max(black + _MIN_LEVEL_SPAN, float(white_point)))
        mid = min(white - (_MIN_LEVEL_SPAN / 2.0), max(black + (_MIN_LEVEL_SPAN / 2.0), float(midtone_point)))
        changed = (
            abs(black - self._black_point) > 1e-6
            or abs(mid - self._midtone_point) > 1e-6
            or abs(white - self._white_point) > 1e-6
        )
        self._black_point = black
        self._midtone_point = mid
        self._white_point = white
        self.update()
        if changed and emit_signal:
            self.levelsChanged.emit(self._black_point, self._midtone_point, self._white_point)

    def levels(self) -> tuple[float, float, float]:
        return (self._black_point, self._midtone_point, self._white_point)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#1f2125"))

        plot_rect = QRectF(
            _LEVEL_MARGIN,
            4.0,
            max(1.0, self.width() - (2.0 * _LEVEL_MARGIN)),
            max(1.0, self.height() - (_LEVEL_BOTTOM_SPACE + 8.0)),
        )
        painter.fillRect(plot_rect, QColor("#2b2d31"))
        painter.setPen(QPen(QColor("#4c5158"), 1.0))
        painter.drawRect(plot_rect)

        histogram_max = float(np.max(self._histogram)) if self._histogram.size else 0.0
        if histogram_max > 0.0:
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
                painter.fillRect(bar_rect, QColor("#7f8ea3"))

        painter.setPen(QPen(QColor("#ffcf66"), 1.2, Qt.PenStyle.DashLine))
        painter.drawLine(self._handle_x(self._midtone_point, plot_rect), plot_rect.top(), self._handle_x(self._midtone_point, plot_rect), plot_rect.bottom())

        self._draw_handle(painter, plot_rect, self._black_point, QColor("#0f1115"), QColor("#f5f5f5"))
        self._draw_handle(painter, plot_rect, self._midtone_point, QColor("#ffcf66"), QColor("#3a2c00"))
        self._draw_handle(painter, plot_rect, self._white_point, QColor("#f5f5f5"), QColor("#101214"))
        painter.end()

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        plot_rect = self._plot_rect()
        if not plot_rect.contains(event.position()):
            super().mousePressEvent(event)
            return
        handle = self._nearest_handle(event.position().x(), plot_rect)
        self._active_handle = handle
        self._move_active_handle(event.position().x(), plot_rect)
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._active_handle is None:
            super().mouseMoveEvent(event)
            return
        self._move_active_handle(event.position().x(), self._plot_rect())
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._active_handle = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _plot_rect(self) -> QRectF:
        return QRectF(
            _LEVEL_MARGIN,
            4.0,
            max(1.0, self.width() - (2.0 * _LEVEL_MARGIN)),
            max(1.0, self.height() - (_LEVEL_BOTTOM_SPACE + 8.0)),
        )

    def _normalized_from_x(self, x_position: float, plot_rect: QRectF) -> float:
        if plot_rect.width() <= 0:
            return 0.0
        return min(1.0, max(0.0, (x_position - plot_rect.left()) / plot_rect.width()))

    def _handle_x(self, normalized_value: float, plot_rect: QRectF) -> float:
        return plot_rect.left() + (min(1.0, max(0.0, normalized_value)) * plot_rect.width())

    def _draw_handle(self, painter: QPainter, plot_rect: QRectF, normalized_value: float, fill: QColor, stroke: QColor) -> None:
        x_position = self._handle_x(normalized_value, plot_rect)
        painter.setPen(QPen(stroke, 1.0))
        painter.drawLine(x_position, plot_rect.top(), x_position, plot_rect.bottom())
        base_y = plot_rect.bottom() + 2.0
        triangle = QPolygonF(
            [
                QPointF(x_position, base_y),
                QPointF(x_position - _HANDLE_HALF_WIDTH, base_y + _HANDLE_HEIGHT),
                QPointF(x_position + _HANDLE_HALF_WIDTH, base_y + _HANDLE_HEIGHT),
            ]
        )
        painter.setBrush(fill)
        painter.drawPolygon(triangle)

    def _nearest_handle(self, x_position: float, plot_rect: QRectF) -> Literal["black", "mid", "white"]:
        distances = {
            "black": abs(x_position - self._handle_x(self._black_point, plot_rect)),
            "mid": abs(x_position - self._handle_x(self._midtone_point, plot_rect)),
            "white": abs(x_position - self._handle_x(self._white_point, plot_rect)),
        }
        return min(distances, key=distances.get)

    def _move_active_handle(self, x_position: float, plot_rect: QRectF) -> None:
        normalized = self._normalized_from_x(x_position, plot_rect)
        black, mid, white = self.levels()
        if self._active_handle == "black":
            black = min(normalized, white - _MIN_LEVEL_SPAN)
            mid = max(mid, black + (_MIN_LEVEL_SPAN / 2.0))
        elif self._active_handle == "white":
            white = max(normalized, black + _MIN_LEVEL_SPAN)
            mid = min(mid, white - (_MIN_LEVEL_SPAN / 2.0))
        else:
            mid = min(white - (_MIN_LEVEL_SPAN / 2.0), max(black + (_MIN_LEVEL_SPAN / 2.0), normalized))
        self.set_levels(black, mid, white, emit_signal=True)