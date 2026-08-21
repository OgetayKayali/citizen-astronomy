from __future__ import annotations

from datetime import date
from math import atan2, cos, floor, hypot, pi, sin
from typing import Callable

from PySide6.QtCore import QEvent, QLineF, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QConicalGradient,
    QFont,
    QImage,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPalette,
    QPen,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from photometry_app.core.observation_map import ObservationMapResult, format_duration
from photometry_app.core.observation_orbit import (
    INTEGRATION_LEGEND_LABELS,
    ObservationOrbit,
    OrbitDay,
    archive_duration_headline,
    build_observation_orbit,
    elapsed_years,
    month_activity,
    month_labels,
    year_fraction,
)

# Activity / intensity colors belong to the orbit visualization, not the app theme.
_ACCENT = QColor("#38bdf8")
_LEVELS = (
    QColor("#152033"),
    QColor("#1e3a5f"),
    QColor("#1d4ed8"),
    QColor("#0ea5e9"),
    QColor("#7dd3fc"),
    QColor("#e0f2fe"),
)


def _with_alpha(color: QColor, alpha: int) -> QColor:
    tinted = QColor(color)
    tinted.setAlpha(max(0, min(255, alpha)))
    return tinted


_TRACK_FILL = 1.15
_DAY_OCCUPANCY = 0.91
_WEEK_OCCUPANCY = 0.86


class ObservationOrbitWidget(QWidget):
    day_clicked = Signal(object)
    year_clicked = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._orbit: ObservationOrbit | None = None
        self._hovered: OrbitDay | None = None
        self._hovered_year: int | None = None
        self._hovered_month: tuple[int, int] | None = None
        self._hovered_pattern_month: int | None = None
        self._selected_day: date | None = None
        self._selected_year: int | None = None
        self._night_inspector: Callable[[date], tuple[str, ...]] | None = None
        self._cache: QImage | None = None
        self._cx = 0.0
        self._cy = 0.0
        self._r0 = 72.0
        self._pitch = 28.0
        self._track = 10.0
        self._r_max = 160.0
        self._r_season_inner = 170.0
        self._r_season_outer = 182.0
        self._logical_height = 640.0
        self._year_anchors: list[tuple[int, QRectF]] = []
        self._month_anchors: list[tuple[int, QRectF]] = []
        self.setMouseTracking(True)
        self.setMinimumSize(420, 420)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_result(self, result: ObservationMapResult | None) -> None:
        self.set_orbit(build_observation_orbit(result))

    def set_orbit(self, orbit: ObservationOrbit | None) -> None:
        self._orbit = orbit
        self._hovered = None
        self._hovered_year = None
        self._hovered_month = None
        self._hovered_pattern_month = None
        self._selected_day = None
        self._selected_year = None
        self._cache = None
        self.update()
        self.updateGeometry()

    def set_night_inspector(self, inspector: Callable[[date], tuple[str, ...]] | None) -> None:
        self._night_inspector = inspector

    def sizeHint(self) -> QSize:
        return QSize(640, 640)

    def minimumSizeHint(self) -> QSize:
        return QSize(440, 440)

    def _window_color(self) -> QColor:
        return self.palette().color(QPalette.ColorRole.Window)

    def _text_color(self) -> QColor:
        return self.palette().color(QPalette.ColorRole.WindowText)

    def _muted_color(self, alpha: int = 160) -> QColor:
        color = self.palette().color(QPalette.ColorRole.PlaceholderText)
        if not color.isValid() or color == self._window_color():
            color = self._text_color()
        return _with_alpha(color, alpha)

    def _highlight_color(self) -> QColor:
        return self.palette().color(QPalette.ColorRole.Highlight)

    def render_to_image(self) -> QImage:
        logical = max(self.width(), self.height(), 580)
        dpr = max(self._device_pixel_ratio(), 2.0)
        pixel = max(1, int(round(logical * dpr)))
        image = QImage(pixel, pixel, QImage.Format.Format_ARGB32_Premultiplied)
        self._render(image, logical_width=logical, logical_height=logical, dpr=dpr)
        image.setDevicePixelRatio(dpr)
        return image

    def _device_pixel_ratio(self) -> float:
        return max(1.0, float(self.devicePixelRatioF()))

    def _configure_painter(self, painter: QPainter) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    def changeEvent(self, event: QEvent) -> None:  # type: ignore[override]
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange:
            self._cache = None
            self.update()

    def event(self, event: QEvent) -> bool:  # type: ignore[override]
        dpr_type = getattr(QEvent.Type, "DevicePixelRatioChange", None)
        if dpr_type is not None and event.type() == dpr_type:
            self._cache = None
            self.update()
        return super().event(event)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        del event
        self._cache = None

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        del event
        self._clear_hover()
        self.update()

    def _clear_hover(self) -> None:
        self._hovered = None
        self._hovered_year = None
        self._hovered_month = None
        self._hovered_pattern_month = None

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        point = event.position().toPoint() if hasattr(event, "position") else event.pos()
        self._layout_metrics(self.width(), self.height())
        day, year, month, pattern = self._hit_focus(point.x(), point.y())
        changed = (
            day is not self._hovered
            or year != self._hovered_year
            or month != self._hovered_month
            or pattern != self._hovered_pattern_month
        )
        self._hovered = day
        self._hovered_year = year
        self._hovered_month = month
        self._hovered_pattern_month = pattern
        if changed:
            self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        point = event.position().toPoint() if hasattr(event, "position") else event.pos()
        self._layout_metrics(self.width(), self.height())
        year = self._hit_year_label(point)
        if year is not None:
            self._selected_year = year
            self.year_clicked.emit(year)
            self.update()
            return
        day = self._hit_day(point.x(), point.y())
        if day is None:
            return
        self._selected_day = day.observation_date
        self._selected_year = day.observation_date.year
        self.day_clicked.emit(day.observation_date)
        self.update()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        point = event.position().toPoint() if hasattr(event, "position") else event.pos()
        self._layout_metrics(self.width(), self.height())
        day = self._hit_day(point.x(), point.y())
        if day is None:
            return
        self._selected_year = day.observation_date.year
        self.year_clicked.emit(day.observation_date.year)

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        logical_w = max(1, self.width())
        logical_h = max(1, self.height())
        dpr = self._device_pixel_ratio()
        pixel = QSize(
            max(1, int(round(logical_w * dpr))),
            max(1, int(round(logical_h * dpr))),
        )
        if self._cache is None or self._cache.size() != pixel:
            image = QImage(pixel, QImage.Format.Format_ARGB32_Premultiplied)
            self._render(image, logical_width=logical_w, logical_height=logical_h, dpr=dpr)
            self._cache = image
        self._layout_metrics(logical_w, logical_h)
        painter = QPainter(self)
        self._configure_painter(painter)
        painter.drawImage(QRectF(0.0, 0.0, float(logical_w), float(logical_h)), self._cache)
        self._paint_overlay(painter)
        painter.end()

    def _layout_metrics(self, width: int, height: int) -> None:
        self._logical_height = float(height)
        available = min(float(width), float(height))
        side = min(available, 900.0) * 0.97
        margin = 28.0
        legend_space = 36.0
        self._cx = width / 2.0
        centered = height / 2.0
        if height > width + 12:
            centered = margin + side / 2.0
        self._cy = min(centered, height - margin - side / 2.0 - legend_space)
        outer = side / 2.0 - margin
        self._r_season_outer = outer
        self._r_season_inner = outer - 3.6
        self._r_max = self._r_season_inner - 32.0
        years = 1.0 if self._orbit is None else max(self._orbit.display_years, 1.0)
        if years < 3.0:
            self._r0 = max(98.0, min(side * 0.20, 124.0))
        elif years < 12.0:
            self._r0 = max(84.0, min(side * 0.17, 108.0))
        else:
            self._r0 = max(70.0, min(side * 0.15, 92.0))
        usable = max(56.0, self._r_max - self._r0)
        self._pitch = usable / years
        cap = 17.2 if years < 3 else 11.5 if years < 12 else 6.9
        self._track = min(self._pitch * 0.78, cap)

    def _render(
        self,
        image: QImage,
        *,
        logical_width: int,
        logical_height: int,
        dpr: float,
    ) -> None:
        image.fill(self._window_color())
        painter = QPainter(image)
        self._configure_painter(painter)
        if dpr > 1.01:
            painter.scale(dpr, dpr)
        self._layout_metrics(logical_width, logical_height)
        if self._orbit is None:
            painter.setPen(self._muted_color(180))
            painter.drawText(
                QRectF(0.0, 0.0, float(logical_width), float(logical_height)),
                Qt.AlignmentFlag.AlignCenter,
                "Add a master folder to build the observation orbit.",
            )
            painter.end()
            return
        self._paint_guides(painter)
        self._paint_tracks(painter)
        self._paint_days(painter)
        self._paint_season_ring(painter)
        self._month_anchors = self._paint_month_labels(painter)
        self._year_anchors = self._paint_year_labels(painter)
        self._paint_intensity_legend(painter)
        painter.end()

    def _paint_overlay(self, painter: QPainter) -> None:
        if self._orbit is None:
            return
        self._configure_painter(painter)
        if self._hovered_year is not None and self._hovered is None:
            painter.fillRect(QRectF(self.rect()), _with_alpha(self._window_color(), 140))
            self._paint_days(painter, highlight_year=self._hovered_year)
        elif self._hovered_month is not None:
            year, month = self._hovered_month
            frac0 = (month - 1) / 12.0
            painter.fillPath(
                self._annular_path(self._r0 - 4.0, self._r_max + 6.0, frac0, frac0 + 1.0 / 12.0),
                _with_alpha(self._highlight_color(), 28),
            )
        if self._hovered is not None:
            self._paint_mark(painter, self._hovered, selected=True)
        elif self._selected_day is not None:
            for item in self._orbit.days:
                if item.observation_date == self._selected_day:
                    self._paint_mark(painter, item, selected=True)
                    break
        self._paint_center(painter)

    def _paint_guides(self, painter: QPainter) -> None:
        for month in range(12):
            frac = month / 12.0
            january = month == 0
            painter.setPen(QPen(self._muted_color(78 if january else 16), 1.25 if january else 1.0))
            inner = self._r0 - (10.0 if january else 4.0)
            outer = self._r_season_outer + (2.0 if january else -6.0)
            x0, y0 = self._polar(inner, frac)
            x1, y1 = self._polar(outer, frac)
            painter.drawLine(QLineF(QPointF(x0, y0), QPointF(x1, y1)))

    def _paint_tracks(self, painter: QPainter) -> None:
        if self._orbit is None or self._pitch <= 0:
            return
        start_frac = year_fraction(self._orbit.start_date)
        steps = max(420, int(self._orbit.display_years * 360))
        path = QPainterPath()
        for index in range(steps + 1):
            elapsed = self._orbit.display_years * index / steps
            frac = (start_frac + elapsed) % 1.0
            radius = self._r0 + elapsed * self._pitch
            x, y = self._polar(radius, frac)
            if index == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        painter.setPen(
            QPen(
                self._muted_color(20),
                max(0.8, self._track * 0.22),
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

    def _paint_days(self, painter: QPainter, *, highlight_year: int | None = None) -> None:
        if self._orbit is None:
            return
        for item in self._orbit.days:
            if highlight_year is not None and item.observation_date.year != highlight_year:
                continue
            if item.is_empty:
                continue
            self._fill_day(painter, item, _LEVELS[item.level])

    def _paint_mark(self, painter: QPainter, item: OrbitDay, *, selected: bool) -> None:
        color = _ACCENT if item.is_empty else _LEVELS[min(5, max(1, item.level))]
        self._fill_day(painter, item, color)
        painter.setPen(QPen(self._text_color() if selected else _ACCENT, 1.6))
        path = self._day_path(item, inflate=1.8)
        painter.drawPath(path)

    def _fill_day(self, painter: QPainter, item: OrbitDay, color: QColor) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawPath(self._day_path(item))

    def _day_mid_fraction(self, item: OrbitDay) -> float:
        return item.year_fraction + max(1, item.aggregated_days) / 730.5

    def _day_path(self, item: OrbitDay, *, inflate: float = 0.0) -> QPainterPath:
        radius = self._r0 + item.elapsed_years * self._pitch
        half = max(1.35, self._track * 0.5 * _TRACK_FILL) + inflate
        days = max(1, item.aggregated_days)
        slot = days / 365.25
        occupancy = _WEEK_OCCUPANCY if days > 1 else _DAY_OCCUPANCY
        span = max(0.0016, slot * occupancy)
        mid = self._day_mid_fraction(item)
        return self._annular_path(radius - half, radius + half, mid - span * 0.5, mid + span * 0.5)

    def _annular_path(self, r_in: float, r_out: float, frac0: float, frac1: float) -> QPainterPath:
        r_in = max(0.5, r_in)
        r_out = max(r_in + 0.6, r_out)
        span = max(0.0005, frac1 - frac0)
        start = 90.0 - frac0 * 360.0
        sweep = -max(0.18, span * 360.0)
        thickness = r_out - r_in
        mid_r = 0.5 * (r_in + r_out)
        chord = span * 2.0 * pi * mid_r
        fillet = min(1.25, thickness * 0.22, chord * 0.28)
        da = fillet / max(mid_r, 8.0) / (2.0 * pi)
        if fillet < 0.45 or span <= da * 2.6:
            return self._plain_annular_path(r_in, r_out, start, sweep)
        return self._rounded_annular_path(r_in, r_out, frac0, frac1, da)

    def _plain_annular_path(self, r_in: float, r_out: float, start: float, sweep: float) -> QPainterPath:
        outer = QRectF(self._cx - r_out, self._cy - r_out, r_out * 2.0, r_out * 2.0)
        inner = QRectF(self._cx - r_in, self._cy - r_in, r_in * 2.0, r_in * 2.0)
        path = QPainterPath()
        path.arcMoveTo(outer, start)
        path.arcTo(outer, start, sweep)
        path.arcTo(inner, start + sweep, -sweep)
        path.closeSubpath()
        return path

    def _rounded_annular_path(
        self,
        r_in: float,
        r_out: float,
        frac0: float,
        frac1: float,
        da: float,
    ) -> QPainterPath:
        outer = QRectF(self._cx - r_out, self._cy - r_out, r_out * 2.0, r_out * 2.0)
        inner = QRectF(self._cx - r_in, self._cy - r_in, r_in * 2.0, r_in * 2.0)
        thickness = r_out - r_in
        path = QPainterPath()
        start_x, start_y = self._polar(r_out, frac0 + da)
        path.moveTo(start_x, start_y)
        path.arcTo(outer, 90.0 - (frac0 + da) * 360.0, -(frac1 - frac0 - 2.0 * da) * 360.0)
        c1x, c1y = self._polar(r_out, frac1)
        e1x, e1y = self._polar(r_out - min(thickness * 0.45, 1.25), frac1)
        path.quadTo(c1x, c1y, e1x, e1y)
        path.lineTo(*self._polar(r_in + min(thickness * 0.45, 1.25), frac1))
        c2x, c2y = self._polar(r_in, frac1)
        path.quadTo(c2x, c2y, *self._polar(r_in, frac1 - da))
        path.arcTo(inner, 90.0 - (frac1 - da) * 360.0, (frac1 - frac0 - 2.0 * da) * 360.0)
        c3x, c3y = self._polar(r_in, frac0)
        path.quadTo(c3x, c3y, *self._polar(r_in + min(thickness * 0.45, 1.25), frac0))
        path.lineTo(*self._polar(r_out - min(thickness * 0.45, 1.25), frac0))
        c0x, c0y = self._polar(r_out, frac0)
        path.quadTo(c0x, c0y, start_x, start_y)
        path.closeSubpath()
        return path

    def _paint_season_ring(self, painter: QPainter) -> None:
        if self._orbit is None:
            return
        ring = self._plain_annular_path(self._r_season_inner, self._r_season_outer, 90.0, -360.0)
        gradient = QConicalGradient(QPointF(self._cx, self._cy), 90.0)
        stops: list[tuple[float, QColor]] = []
        for item in self._orbit.season_bins:
            stop = (1.0 - item.year_fraction) % 1.0
            if item.level <= 0:
                color = self._muted_color(12)
            else:
                color = _with_alpha(_LEVELS[min(4, item.level)], 120)
            stops.append((stop, color))
        stops.sort(key=lambda item: item[0])
        if stops:
            gradient.setColorAt(0.0, stops[-1][1])
            gradient.setColorAt(1.0, stops[0][1])
            for stop, color in stops:
                gradient.setColorAt(stop, color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(gradient))
        painter.drawPath(ring)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(self._muted_color(36), 1))
        painter.drawEllipse(QRectF(
            self._cx - self._r_season_outer,
            self._cy - self._r_season_outer,
            self._r_season_outer * 2.0,
            self._r_season_outer * 2.0,
        ))

    def _paint_month_labels(self, painter: QPainter) -> list[tuple[int, QRectF]]:
        anchors: list[tuple[int, QRectF]] = []
        font = QFont(self.font())
        font.setPointSize(max(9, self.font().pointSize()))
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(self._text_color())
        radius = self._r_season_outer + 18.0
        for index, label in enumerate(month_labels()):
            frac = (index + 0.5) / 12.0
            x, y = self._polar(radius, frac)
            rect = QRectF(x - 18, y - 9, 36, 18)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
            anchors.append((index + 1, rect.adjusted(-6, -6, 6, 6)))
        return anchors

    def _paint_intensity_legend(self, painter: QPainter) -> None:
        font = QFont(self.font())
        font.setPointSize(max(7, self.font().pointSize() - 1))
        painter.setFont(font)
        x = self._cx - self._r_season_outer
        y = min(self._cy + self._r_season_outer + 28.0, self._logical_height - 28.0)
        painter.setPen(self._muted_color(200))
        painter.drawText(
            QRectF(x, y - 14.0, 160.0, 14.0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "Integration / night",
        )
        swatch_x = x
        for color, label in zip(_LEVELS[:5], INTEGRATION_LEGEND_LABELS, strict=True):
            painter.fillRect(QRectF(swatch_x, y + 2.0, 12.0, 10.0), color)
            painter.setPen(self._muted_color(210))
            painter.drawText(
                QRectF(swatch_x + 14.0, y, 40.0, 14.0),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                label,
            )
            swatch_x += 54.0

    def _paint_year_labels(self, painter: QPainter) -> list[tuple[int, QRectF]]:
        anchors: list[tuple[int, QRectF]] = []
        if self._orbit is None:
            return anchors
        font = QFont(self.font())
        font.setPointSize(max(9, self.font().pointSize()))
        font.setBold(True)
        painter.setFont(font)
        years = list(range(self._orbit.start_date.year, self._orbit.end_date.year + 1))
        step = 1
        if len(years) > 14:
            step = 2
        if len(years) > 24:
            step = 5
        for offset, year in enumerate(years):
            if offset not in (0, len(years) - 1) and offset % step != 0:
                continue
            sample = date(year, 1, 1)
            if sample < self._orbit.start_date:
                sample = self._orbit.start_date
            if sample > self._orbit.end_date:
                continue
            elapsed = elapsed_years(sample, self._orbit.start_date)
            radius = self._r0 + elapsed * self._pitch + self._track * 1.05
            x, y = self._polar(radius, 0.0 if sample.month == 1 and sample.day == 1 else year_fraction(sample))
            rect = QRectF(x - 22, y - 9, 44, 18)
            painter.setPen(self._highlight_color() if year == self._hovered_year else self._text_color())
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(year))
            anchors.append((year, rect.adjusted(-6, -6, 6, 6)))
        return anchors

    def _paint_center(self, painter: QPainter) -> None:
        hole = max(64.0, self._r0 - self._track * 0.85)
        painter.setPen(QPen(_with_alpha(self._highlight_color(), 70), 1))
        painter.setBrush(self._window_color())
        painter.drawEllipse(QRectF(self._cx - hole, self._cy - hole, hole * 2.0, hole * 2.0))
        title, lines = self._center_copy()
        title_font = QFont(self.font())
        title_font.setBold(True)
        title_font.setPointSize(max(12, self.font().pointSize() + 5))
        body = QFont(self.font())
        body.setPointSize(max(8, self.font().pointSize()))
        muted = QFont(self.font())
        muted.setPointSize(max(7, self.font().pointSize() - 1))
        inner = QRectF(self._cx - hole + 10, self._cy - hole + 12, hole * 2.0 - 20, hole * 2.0 - 24)
        painter.setFont(title_font)
        painter.setPen(self._text_color())
        title_rect = QRectF(inner.x(), inner.y() + inner.height() * 0.12, inner.width(), 28)
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, title)
        y = title_rect.bottom() + 4.0
        for index, line in enumerate(lines):
            painter.setFont(body if index == 0 else muted)
            painter.setPen(self._text_color() if index == 0 else self._muted_color(190))
            painter.drawText(
                QRectF(inner.x(), y, inner.width(), 16),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                line,
            )
            y += 16.0

    def _center_copy(self) -> tuple[str, tuple[str, ...]]:
        if self._orbit is None:
            return "ORBIT", ("Add a master folder to begin.",)
        if self._hovered is not None and not self._hovered.is_empty:
            day = self._hovered.observation_date
            title = f"{day.strftime('%b')} {day.day}, {day.year}"
            extra: tuple[str, ...] = ()
            if self._night_inspector is not None:
                extra = self._night_inspector(day)
            if extra:
                return title, extra
            return title, (
                f"{format_duration(self._hovered.exposure_seconds)} integration",
                f"{self._hovered.frame_count} frames",
            )
        if self._hovered_month is not None:
            year, month = self._hovered_month
            nights, seconds = month_activity(self._orbit, year=year, month=month)
            heading = date(year, month, 1).strftime("%B %Y")
            night_word = "observing night" if nights == 1 else "observing nights"
            return heading, (f"{nights:,} {night_word}", f"{seconds / 3600.0:,.1f} h integration")
        if self._hovered_pattern_month is not None:
            month = self._hovered_pattern_month
            nights, seconds = month_activity(self._orbit, month=month)
            heading = date(2000, month, 1).strftime("%B")
            night_word = "observing night" if nights == 1 else "observing nights"
            return heading, ("Observing pattern", f"{nights:,} {night_word}", f"{seconds / 3600.0:,.1f} h")
        if self._hovered_year is not None:
            for item in self._orbit.years:
                if item.year == self._hovered_year:
                    night_word = "observing night" if item.night_count == 1 else "observing nights"
                    return (
                        str(item.year),
                        (f"{item.night_count:,} {night_word}", f"{item.exposure_seconds / 3600.0:,.0f} h integration"),
                    )
            return str(self._hovered_year), ("No dated lights",)
        headline = archive_duration_headline(self._orbit.span_years, night_count=self._orbit.night_count)
        span = f"{self._orbit.first_activity.year}—{self._orbit.last_activity.year}"
        stats = f"{self._orbit.night_count:,} nights · {self._orbit.total_exposure_hours:,.0f} h"
        return headline, (span, stats)

    def _polar(self, radius: float, fraction: float) -> tuple[float, float]:
        angle = -0.5 * pi + fraction * 2.0 * pi
        return self._cx + radius * cos(angle), self._cy + radius * sin(angle)

    def _hit_year_label(self, point) -> int | None:
        for year, rect in self._year_anchors:
            if rect.contains(point):
                return year
        return None

    def _hit_month_label(self, point) -> int | None:
        for month, rect in self._month_anchors:
            if rect.contains(point):
                return month
        return None

    def _hit_focus(
        self, x: float, y: float
    ) -> tuple[OrbitDay | None, int | None, tuple[int, int] | None, int | None]:
        point = QPointF(x, y)
        day = self._hit_day(x, y)
        if day is not None:
            return day, None, None, None
        year = self._hit_year_label(point)
        if year is not None:
            return None, year, None, None
        pattern = self._hit_month_label(point)
        if pattern is not None:
            return None, None, None, pattern
        if self._orbit is None or self._pitch <= 0:
            return None, None, None, None
        dx = x - self._cx
        dy = y - self._cy
        radius = hypot(dx, dy)
        if radius < self._r0 - self._track or radius > self._r_max + self._track:
            return None, None, None, None
        elapsed = (radius - self._r0) / self._pitch
        fraction = ((atan2(dx, -dy) + 2.0 * pi) % (2.0 * pi)) / (2.0 * pi)
        month = min(12, max(1, int(fraction * 12.0) + 1))
        start_frac = year_fraction(self._orbit.start_date)
        year_value = int(self._orbit.start_date.year + floor(elapsed - fraction + start_frac + 1e-6))
        year_value = min(self._orbit.end_date.year, max(self._orbit.start_date.year, year_value))
        if fraction < 0.04 or fraction > 0.96:
            return None, year_value, None, None
        return None, None, (year_value, month), None

    def _hit_day(self, x: float, y: float) -> OrbitDay | None:
        if self._orbit is None or self._pitch <= 0:
            return None
        dx = x - self._cx
        dy = y - self._cy
        radius = hypot(dx, dy)
        pad = self._track * _TRACK_FILL
        if radius < self._r0 - pad or radius > self._r_max + pad:
            return None
        elapsed = (radius - self._r0) / self._pitch
        fraction = ((atan2(dx, -dy) + 2.0 * pi) % (2.0 * pi)) / (2.0 * pi)
        best: OrbitDay | None = None
        best_score = 10.0
        for item in self._orbit.days:
            if item.is_empty:
                continue
            mid = self._day_mid_fraction(item)
            delta_frac = abs(mid - fraction)
            delta_frac = min(delta_frac, 1.0 - delta_frac)
            delta_elapsed = abs(item.elapsed_years - elapsed)
            score = delta_elapsed / max(self._pitch * 0.02, 0.08) + delta_frac * 48.0
            if score < best_score:
                best = item
                best_score = score
        if best is None:
            return None
        mid = self._day_mid_fraction(best)
        delta_frac = abs(mid - fraction)
        delta_frac = min(delta_frac, 1.0 - delta_frac)
        if abs(best.elapsed_years - elapsed) <= 0.5 and delta_frac < 0.012:
            return best
        return None

    def _tooltip(self, item: OrbitDay) -> str:
        if item.aggregated_days > 1:
            heading = f"Week of {item.observation_date.isoformat()}"
        else:
            heading = item.observation_date.isoformat()
        if item.is_empty:
            return f"{heading}: no observations"
        return (
            f"{heading}: {format_duration(item.exposure_seconds)} "
            f"({item.frame_count} subframe{'s' if item.frame_count != 1 else ''})"
        )


class ObservationHistoryView(QFrame):
    day_clicked = Signal(object)
    year_clicked = Signal(int)

    def __init__(self, year_widget: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._year_widget = year_widget
        self._result: ObservationMapResult | None = None
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        header.addStretch(1)
        self._back_button = QPushButton("Back to orbit")
        self._back_button.setVisible(False)
        self._back_button.clicked.connect(self.show_orbit)
        header.addWidget(self._back_button, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)

        self._stack = QStackedWidget(self)
        self._orbit = ObservationOrbitWidget(self)
        self._orbit.setMinimumSize(440, 440)
        self._orbit.day_clicked.connect(self._handle_day_clicked)
        self._orbit.year_clicked.connect(self._handle_year_clicked)
        self._stack.addWidget(self._orbit)
        year_scroll = QScrollArea(self)
        year_scroll.setWidgetResizable(False)
        year_scroll.setFrameShape(QFrame.Shape.NoFrame)
        year_scroll.setWidget(year_widget)
        self._year_widget = year_widget
        self._year_scroll = year_scroll
        self._stack.addWidget(year_scroll)
        layout.addWidget(self._stack, 1)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(8)
        hint = QToolButton()
        hint.setText("ⓘ")
        hint.setAutoRaise(True)
        hint.setCursor(Qt.CursorShape.WhatsThisCursor)
        hint.setToolTip(
            "Hover a night, month, or year to inspect it in the center.\n"
            "Click a year label or double-click a night for that year’s calendar.\n"
            "Outer ring: observing pattern across all years."
        )
        footer.addWidget(hint)
        footer.addStretch(1)
        self._status = QLabel("")
        self._status.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        self._status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        footer.addWidget(self._status, 0, Qt.AlignmentFlag.AlignRight)
        layout.addLayout(footer)

    def sizeHint(self) -> QSize:
        return QSize(680, 680)

    def minimumSizeHint(self) -> QSize:
        return QSize(480, 500)

    def set_result(self, result: ObservationMapResult | None) -> None:
        self._result = result
        self._orbit.set_result(result)
        self.show_orbit()

    def set_night_inspector(self, inspector: Callable[[date], tuple[str, ...]] | None) -> None:
        self._orbit.set_night_inspector(inspector)

    def set_status(self, text: str) -> None:
        self._status.setText(text)

    def show_orbit(self) -> None:
        self._stack.setCurrentWidget(self._orbit)
        self._back_button.setVisible(False)
        self._status.setText("")

    def render_to_image(self) -> QImage:
        if self._stack.currentWidget() is self._orbit:
            return self._orbit.render_to_image()
        render = getattr(self._year_widget, "render_to_image", None)
        if callable(render):
            return render()
        image = QImage(self.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(self.palette().color(QPalette.ColorRole.Window))
        return image

    def _handle_day_clicked(self, day: object) -> None:
        if not isinstance(day, date):
            return
        record = self._day_record(day)
        self._status.setText(self._orbit._tooltip(record) if record is not None else day.isoformat())
        self.day_clicked.emit(day)

    def _handle_year_clicked(self, year: int) -> None:
        self._status.setText(self._year_status(year))
        self._stack.setCurrentWidget(self._year_scroll)
        self._back_button.setVisible(True)
        self.year_clicked.emit(year)

    def _year_status(self, year: int) -> str:
        orbit = self._orbit._orbit
        if orbit is None:
            return f"{year}"
        for item in orbit.years:
            if item.year == year:
                return f"{year}: {item.night_count:,} nights · {format_duration(item.exposure_seconds)}"
        return f"{year}: no dated lights"

    def _day_record(self, day: date) -> OrbitDay | None:
        orbit = self._orbit._orbit
        if orbit is None:
            return None
        for item in orbit.days:
            if item.observation_date == day:
                return item
        return None
