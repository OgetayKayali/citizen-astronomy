from __future__ import annotations

from datetime import date, timedelta
from collections import defaultdict
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QMouseEvent, QPainter, QPaintEvent, QPalette, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QBoxLayout,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QToolTip,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from photometry_app.core.image_io import read_header
from photometry_app.core.observation_deck import (
    DeckImage,
    DeckStats,
    FilterTimeStats,
    ImageKind,
    KIND_LABELS,
    ObservationDeckLibrary,
    build_deck_stats,
    header_keyword_rows,
    load_observation_deck_library,
    merge_observation_deck_libraries,
    observation_map_from_stats,
    path_key,
    relative_path_parts,
    remove_observation_deck_root,
    save_observation_deck_library,
    scan_observation_deck,
)
from photometry_app.core.observation_map import (
    ObservationMapDay,
    ObservationMapResult,
    contribution_level,
    contribution_span_bounds,
    format_duration,
)
from photometry_app.ui.observation_orbit_widget import ObservationHistoryView


_LEVEL_COLORS = (
    QColor("#161b22"),
    QColor("#0e4429"),
    QColor("#006d32"),
    QColor("#26a641"),
    QColor("#39d353"),
)
_YEAR_EMPTY_COLORS = (
    QColor("#161b22"),
    QColor("#1b2030"),
    QColor("#1b241c"),
    QColor("#241c1b"),
)
_YEAR_BORDER_COLORS = (
    QColor("#30363d"),
    QColor("#4c6aa8"),
    QColor("#3d8f62"),
    QColor("#a86a4c"),
)
_YEAR_LABEL_COLORS = (
    QColor("#8b949e"),
    QColor("#93c5fd"),
    QColor("#86efac"),
    QColor("#fdba74"),
)
_EMPTY_BORDER = QColor("#30363d")
_DAY_LABELS = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")
_MONTH_LABELS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)
_WCS_PREFIXES = (
    "CTYPE",
    "CRVAL",
    "CRPIX",
    "CDELT",
    "CROTA",
    "CUNIT",
    "CD1",
    "CD2",
    "PC1",
    "PC2",
    "PV1",
    "PV2",
    "LONPOLE",
    "LATPOLE",
    "RADESYS",
    "RADECSYS",
    "EQUINOX",
    "WCSAXES",
    "WCSNAME",
    "CVAL",
)
_ROLE_PATH = int(Qt.ItemDataRole.UserRole)
_ROLE_IS_FOLDER = int(Qt.ItemDataRole.UserRole) + 1
_ROLE_KIND = int(Qt.ItemDataRole.UserRole) + 2
_ROLE_IS_LIBRARY = int(Qt.ItemDataRole.UserRole) + 3
_ROLE_IS_MASTER = int(Qt.ItemDataRole.UserRole) + 4
_ROLE_LAZY_IMAGES = int(Qt.ItemDataRole.UserRole) + 5
_ROLE_LAZY_PLACEHOLDER = int(Qt.ItemDataRole.UserRole) + 6


def _section_title(text: str, parent: QWidget | None = None) -> QLabel:
    label = QLabel(text, parent)
    font = QFont(label.font())
    font.setBold(True)
    font.setPointSize(font.pointSize() + 1)
    label.setFont(font)
    return label


def _muted(label: QLabel) -> QLabel:
    label.setForegroundRole(QPalette.ColorRole.PlaceholderText)
    return label


def _format_hours(seconds: float) -> str:
    hours = float(seconds) / 3600.0
    if hours >= 100:
        return f"{hours:,.0f} h"
    if hours >= 10:
        return f"{hours:,.1f} h"
    return f"{hours:,.2f} h"


_HUE_SHIFTS_DEG = (0.0, 16.0, -14.0, 30.0, -26.0, 44.0, -38.0, 8.0)
_LIGHT_DELTAS = (0.08, -0.07, 0.14, -0.12, 0.02, -0.16, 0.10, -0.04)
_SAT_DELTAS = (0.0, -0.08, 0.05, -0.12, 0.02, -0.06, 0.04, -0.14)


def theme_filter_colors(accent: QColor, surface: QColor, count: int) -> tuple[QColor, ...]:
    if count <= 0:
        return ()
    hue, saturation, _lightness, _alpha = accent.getHslF()
    surface_light = surface.lightnessF()
    base_saturation = min(0.48, max(0.26, (saturation if saturation >= 0.18 else 0.34) * 0.82))
    base_light = 0.42 if surface_light < 0.45 else 0.48
    colors: list[QColor] = []
    for index in range(count):
        slot = index % len(_HUE_SHIFTS_DEG)
        next_hue = (hue + _HUE_SHIFTS_DEG[slot] / 360.0) % 1.0
        next_sat = min(0.50, max(0.20, base_saturation + _SAT_DELTAS[slot]))
        next_light = min(0.64, max(0.28, base_light + _LIGHT_DELTAS[slot]))
        if surface_light < 0.4:
            next_light = max(next_light, 0.34)
        else:
            next_light = min(next_light, 0.56)
        if abs(next_light - surface_light) < 0.12:
            next_light = min(0.68, next_light + 0.16) if surface_light < 0.5 else max(0.24, next_light - 0.16)
        color = QColor.fromHslF(next_hue, next_sat, next_light)
        hue_out, sat_out, light_out, _alpha_out = color.getHslF()
        if sat_out > 0.50:
            color = QColor.fromHslF(hue_out, 0.50, light_out)
        colors.append(color)
    return tuple(colors)


def _subdued_filter_color(accent: QColor, surface: QColor) -> QColor:
    hue, saturation, _lightness, _alpha = accent.getHslF()
    surface_light = surface.lightnessF()
    light = 0.38 if surface_light < 0.45 else 0.52
    return QColor.fromHslF(hue, min(0.22, max(0.10, saturation * 0.35)), light)


class ObservationDeckScanWorker(QThread):
    progress_updated = Signal(int, int, str)
    scan_completed = Signal(object)
    scan_failed = Signal(str)

    def __init__(
        self,
        root_paths: Path | tuple[Path, ...],
        *,
        observation_timezone: str = "UTC",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if isinstance(root_paths, Path):
            self._root_paths: tuple[Path, ...] = (root_paths,)
        else:
            self._root_paths = tuple(root_paths)
        self._observation_timezone = observation_timezone

    def run(self) -> None:
        try:
            def progress(index: int, total: int, path: Path) -> None:
                self.progress_updated.emit(index, total, str(path.name))

            result = scan_observation_deck(
                self._root_paths,
                observation_timezone=self._observation_timezone,
                progress_callback=progress,
            )
        except Exception as exc:
            self.scan_failed.emit(str(exc).strip() or exc.__class__.__name__)
            return
        self.scan_completed.emit(result)


class ObservationDeckCacheWorker(QThread):
    load_completed = Signal(object)
    load_failed = Signal(str)

    def run(self) -> None:
        try:
            self.load_completed.emit(load_observation_deck_library())
        except Exception as exc:
            self.load_failed.emit(str(exc).strip() or exc.__class__.__name__)


def _year_palette_index(year: int) -> int:
    return int(year) % len(_YEAR_EMPTY_COLORS)


class ContributionCalendarWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._result: ObservationMapResult | None = None
        self._max_seconds_override: float | None = None
        self._cell = 12
        self._gap = 3
        self._weeks_per_row = 53
        self._row_gap = 14
        self._left_label_width = 34
        self._top_label_height = 36
        self._cells: list[tuple[QRectF, date, ObservationMapDay | None]] = []
        self.setMouseTracking(True)
        self.setMinimumHeight(160)

    def set_result(
        self,
        result: ObservationMapResult | None,
        *,
        max_seconds: float | None = None,
    ) -> None:
        self._result = result
        self._max_seconds_override = max_seconds
        self._rebuild_geometry()
        hint = self.sizeHint()
        self.setMinimumWidth(hint.width())
        self.setMinimumHeight(hint.height())
        self.resize(hint)
        self.update()
        self.updateGeometry()

    def sizeHint(self) -> QSize:
        weeks = self._week_count()
        rows = max(1, (weeks + self._weeks_per_row - 1) // self._weeks_per_row)
        width = self._left_label_width + self._weeks_per_row * (self._cell + self._gap) + 16
        height = rows * self._row_height() + 8
        return QSize(max(420, width), max(160, height))

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def _row_height(self) -> int:
        return self._top_label_height + 7 * (self._cell + self._gap) + self._row_gap

    def _span(self) -> tuple[date, date] | None:
        if self._result is None or self._result.first_date is None or self._result.last_date is None:
            return None
        return contribution_span_bounds(self._result)

    def _week_count(self) -> int:
        span = self._span()
        if span is None:
            return self._weeks_per_row
        start, end = span
        start_pad = start - timedelta(days=(start.weekday() + 1) % 7)
        end_pad = end + timedelta(days=(6 - ((end.weekday() + 1) % 7)))
        return max(1, ((end_pad - start_pad).days // 7) + 1)

    def _rebuild_geometry(self) -> None:
        self._cells.clear()
        span = self._span()
        if span is None or self._result is None:
            return
        day_lookup = self._result.day_map()
        start, end = span
        cursor = start - timedelta(days=(start.weekday() + 1) % 7)
        end_pad = end + timedelta(days=(6 - ((end.weekday() + 1) % 7)))
        week_index = 0
        loops = 0
        while cursor <= end_pad and loops <= 40 * 366:
            loops += 1
            day_index = (cursor.weekday() + 1) % 7
            row = week_index // self._weeks_per_row
            col = week_index % self._weeks_per_row
            rect = QRectF(
                self._left_label_width + col * (self._cell + self._gap),
                row * self._row_height() + self._top_label_height + day_index * (self._cell + self._gap),
                self._cell,
                self._cell,
            )
            if start <= cursor <= end:
                self._cells.append((rect, cursor, day_lookup.get(cursor)))
            cursor += timedelta(days=1)
            if ((cursor.weekday() + 1) % 7) == 0:
                week_index += 1

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.fillRect(self.rect(), QColor("#0d1117"))
        if self._result is None:
            painter.setPen(QColor("#8b949e"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Add a master folder to build the observation map.")
            painter.end()
            return
        if not self._cells:
            painter.setPen(QColor("#8b949e"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No dated light subframes in this selection.")
            painter.end()
            return

        if self._max_seconds_override is not None:
            max_seconds = float(self._max_seconds_override)
        else:
            max_seconds = max((day.exposure_seconds for day in self._result.days), default=0.0)
        font = QFont(self.font())
        font.setPointSize(8)
        painter.setFont(font)
        painter.setPen(QColor("#8b949e"))
        for index, label in enumerate(_DAY_LABELS):
            if index % 2 == 1:
                continue
            y = self._top_label_height + index * (self._cell + self._gap)
            painter.drawText(
                QRectF(0, y, self._left_label_width - 4, self._cell),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                label,
            )
        rows = max(1, (self._week_count() + self._weeks_per_row - 1) // self._weeks_per_row)
        for row in range(1, rows):
            for index, label in enumerate(_DAY_LABELS):
                if index % 2 == 1:
                    continue
                y = row * self._row_height() + self._top_label_height + index * (self._cell + self._gap)
                painter.drawText(
                    QRectF(0, y, self._left_label_width - 4, self._cell),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    label,
                )

        year_positions: dict[int, tuple[float, float]] = {}
        month_positions: list[tuple[float, float, str]] = []
        seen_months: set[tuple[int, int]] = set()
        for rect, day, _info in self._cells:
            label_y = rect.top() - self._top_label_height
            if day.year not in year_positions:
                year_positions[day.year] = (rect.left(), label_y)
            month_key = (day.year, day.month)
            if day.day == 1 and month_key not in seen_months:
                seen_months.add(month_key)
                month_positions.append((rect.left(), label_y + 16, _MONTH_LABELS[day.month - 1]))
        for year, (x, y) in year_positions.items():
            painter.setPen(_YEAR_LABEL_COLORS[_year_palette_index(year)])
            painter.drawText(
                QRectF(x, y, 72, 16),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                str(year),
            )
        painter.setPen(QColor("#8b949e"))
        for x, y, label in month_positions:
            painter.drawText(
                QRectF(x, y, 36, 16),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                label,
            )

        for rect, day, info in self._cells:
            palette = _year_palette_index(day.year)
            if info is None:
                painter.fillRect(rect, _YEAR_EMPTY_COLORS[palette])
            else:
                level = contribution_level(info.exposure_seconds, max_seconds=max_seconds)
                painter.fillRect(rect, _LEVEL_COLORS[level])
            painter.setPen(QPen(_YEAR_BORDER_COLORS[palette], 1))
            painter.drawRect(rect.adjusted(0, 0, -1, -1))
        painter.end()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        point = event.position().toPoint() if hasattr(event, "position") else event.pos()
        for rect, day, info in self._cells:
            if not rect.contains(point):
                continue
            if info is None:
                tip = f"{day.isoformat()}: no imaging"
            else:
                tip = (
                    f"{day.isoformat()}: {format_duration(info.exposure_seconds)} "
                    f"({info.frame_count} subframe{'s' if info.frame_count != 1 else ''})"
                )
            QToolTip.showText(self.mapToGlobal(point + QPoint(12, 8)), tip, self)
            return
        QToolTip.hideText()

    def render_to_image(self) -> QImage:
        hint = self.sizeHint()
        image = QImage(hint, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor("#0d1117"))
        painter = QPainter(image)
        self.render(painter)
        painter.end()
        return image


class FilterPieChartWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._filters: tuple[FilterTimeStats, ...] = ()
        self._slices: list[tuple[str, float, QColor]] = []
        self.set_diameter(240)

    def set_diameter(self, size: int) -> None:
        side = max(180, min(300, int(size)))
        if self.width() == side and self.height() == side:
            return
        self.setFixedSize(side, side)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.update()

    def set_filters(self, filters: tuple[FilterTimeStats, ...]) -> None:
        self._filters = filters
        self._rebuild_slices()
        self.update()

    def color_for(self, index: int) -> QColor:
        if 0 <= index < len(self._slices):
            return self._slices[index][2]
        return self.palette().color(QPalette.ColorRole.Highlight)

    def sizeHint(self) -> QSize:
        return self.size()

    def changeEvent(self, event: QEvent) -> None:  # type: ignore[override]
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange:
            self._rebuild_slices()
            self.update()

    def _rebuild_slices(self) -> None:
        accent = self.palette().color(QPalette.ColorRole.Highlight)
        surface = self.palette().color(QPalette.ColorRole.Window)
        colors = theme_filter_colors(accent, surface, len(self._filters))
        muted = _subdued_filter_color(accent, surface)
        self._slices = []
        for index, item in enumerate(self._filters):
            name = item.filter_name
            color = muted if name.casefold() in {"unknown", "other"} else colors[index]
            self._slices.append((name, float(item.exposure_seconds), color))

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        window = self.palette().color(QPalette.ColorRole.Window)
        painter.fillRect(self.rect(), window)
        total = sum(seconds for _name, seconds, _color in self._slices)
        if total <= 0:
            painter.setPen(self.palette().color(QPalette.ColorRole.PlaceholderText))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No filter\ntime yet")
            painter.end()
            return
        side = min(self.width(), self.height()) - 4
        pie = QRect((self.width() - side) // 2, (self.height() - side) // 2, side, side)
        start_angle = 90 * 16
        for _name, seconds, color in self._slices:
            span = max(1, int(round((seconds / total) * 360 * 16)))
            painter.setBrush(color)
            painter.setPen(QPen(window, 1))
            painter.drawPie(pie, start_angle, -span)
            start_angle -= span
        hole = side * 0.52
        inner = QRect(
            int(pie.center().x() - hole / 2),
            int(pie.center().y() - hole / 2),
            int(hole),
            int(hole),
        )
        painter.setBrush(window)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(inner)
        painter.end()


class _NumericTableItem(QTableWidgetItem):
    def __init__(self, display: str, value: float) -> None:
        super().__init__(display)
        self.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.setData(Qt.ItemDataRole.UserRole, float(value))

    def __lt__(self, other: QTableWidgetItem) -> bool:  # type: ignore[override]
        left = self.data(Qt.ItemDataRole.UserRole)
        right = other.data(Qt.ItemDataRole.UserRole) if other is not None else None
        if left is not None and right is not None:
            return float(left) < float(right)
        return super().__lt__(other)


class _StatStrip(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Maximum)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(0)
        self._values: list[QLabel] = []
        for index, caption in enumerate(("Integration", "Targets", "Nights", "Frames")):
            if index:
                separator = QFrame(self)
                separator.setFrameShape(QFrame.Shape.VLine)
                separator.setFrameShadow(QFrame.Shadow.Plain)
                separator.setFixedHeight(32)
                layout.addWidget(separator, 0, Qt.AlignmentFlag.AlignVCenter)
            cell = QWidget(self)
            column = QVBoxLayout(cell)
            column.setContentsMargins(14, 0, 14, 0)
            column.setSpacing(1)
            value = QLabel("—")
            font = QFont(value.font())
            font.setBold(True)
            font.setPointSizeF(max(1.0, (font.pointSizeF() + 6.0) * 1.25))
            value.setFont(font)
            value.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label = _muted(QLabel(caption))
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            column.addWidget(value)
            column.addWidget(label)
            layout.addWidget(cell, 0)
            self._values.append(value)

    def set_metrics(self, integration: str, targets: str, nights: str, frames: str) -> None:
        for label, text in zip(self._values, (integration, targets, nights, frames), strict=True):
            label.setText(text)


class _FilterUsageCard(QFrame):
    _WIDE_ENTER = 358
    _WIDE_EXIT = 318
    _DONUT_MIN = 216
    _DONUT_MAX = 280
    _LEGEND_RESERVE = 142
    _BODY_GAP = 14

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumWidth(220)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self._wide = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)
        layout.addWidget(_section_title("Filter Usage"), 0, Qt.AlignmentFlag.AlignTop)
        self.pie = FilterPieChartWidget(self)
        self._legend_host = QWidget(self)
        self._legend_host.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Maximum)
        self._legend = QGridLayout(self._legend_host)
        self._legend.setContentsMargins(0, 0, 0, 0)
        self._legend.setHorizontalSpacing(10)
        self._legend.setVerticalSpacing(5)
        self._legend.setColumnMinimumWidth(1, 36)
        self._legend.setColumnMinimumWidth(2, 58)
        self._legend.setColumnMinimumWidth(3, 28)
        self._legend.setColumnStretch(0, 0)
        self._legend.setColumnStretch(1, 0)
        self._legend.setColumnStretch(2, 0)
        self._legend.setColumnStretch(3, 0)
        self._body = QWidget(self)
        self._body.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self._body_layout = QBoxLayout(QBoxLayout.Direction.TopToBottom, self._body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(10)
        self._body_layout.addWidget(self.pie, 0, Qt.AlignmentFlag.AlignHCenter)
        self._body_layout.addWidget(self._legend_host, 0)
        layout.addWidget(self._body, 0, Qt.AlignmentFlag.AlignTop)
        self._update_orientation()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self._update_orientation()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._update_orientation()

    def _legend_width(self) -> int:
        return max(self._LEGEND_RESERVE, int(self._legend_host.sizeHint().width()))

    def _donut_diameter(self, available: int, wide: bool) -> int:
        if wide:
            room = available - self._legend_width() - self._BODY_GAP
            return max(self._DONUT_MIN, min(self._DONUT_MAX, room))
        return max(188, min(224, available))

    def _update_orientation(self) -> None:
        available = max(0, self.width() - 20)
        if self._wide:
            wide = available >= self._WIDE_EXIT
        else:
            wide = available >= self._WIDE_ENTER
        diameter = self._donut_diameter(available, wide)
        size_changed = diameter != self.pie.width()
        self.pie.set_diameter(diameter)
        if wide == self._wide:
            if size_changed:
                self._cap_height()
            return
        self._wide = wide
        if wide:
            self._body_layout.setDirection(QBoxLayout.Direction.LeftToRight)
            self._body_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            self._body_layout.setAlignment(self.pie, Qt.AlignmentFlag.AlignVCenter)
            self._body_layout.setAlignment(self._legend_host, Qt.AlignmentFlag.AlignVCenter)
            self._body_layout.setStretch(0, 0)
            self._body_layout.setStretch(1, 0)
            self._body_layout.setSpacing(self._BODY_GAP)
        else:
            self._body_layout.setDirection(QBoxLayout.Direction.TopToBottom)
            self._body_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
            self._body_layout.setAlignment(self.pie, Qt.AlignmentFlag.AlignHCenter)
            self._body_layout.setAlignment(self._legend_host, Qt.AlignmentFlag.AlignTop)
            self._body_layout.setStretch(0, 0)
            self._body_layout.setStretch(1, 0)
            self._body_layout.setSpacing(8)
        self._cap_height()

    def _cap_height(self) -> None:
        self.setMaximumHeight(16777215)
        self.updateGeometry()
        self.setMaximumHeight(max(self.sizeHint().height(), self.minimumSizeHint().height()))

    def set_filters(self, filters: tuple[FilterTimeStats, ...], total_seconds: float) -> None:
        self.pie.set_filters(filters)
        while self._legend.count():
            item = self._legend.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        total = total_seconds or 1.0
        shown = filters[:8]
        for index, item in enumerate(shown):
            share = (item.exposure_seconds / total) * 100.0 if total_seconds else 0.0
            swatch = QFrame(self._legend_host)
            swatch.setFixedSize(8, 8)
            swatch.setAutoFillBackground(True)
            palette = swatch.palette()
            palette.setColor(QPalette.ColorRole.Window, self.pie.color_for(index))
            swatch.setPalette(palette)
            name = QLabel(item.filter_name, self._legend_host)
            hours = QLabel(_format_hours(item.exposure_seconds), self._legend_host)
            hours.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            percent = _muted(QLabel(f"{share:.0f}%", self._legend_host))
            percent.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._legend.addWidget(swatch, index, 0, Qt.AlignmentFlag.AlignVCenter)
            self._legend.addWidget(name, index, 1, Qt.AlignmentFlag.AlignVCenter)
            self._legend.addWidget(hours, index, 2, Qt.AlignmentFlag.AlignVCenter)
            self._legend.addWidget(percent, index, 3, Qt.AlignmentFlag.AlignVCenter)
        self._cap_height()

    def clear(self) -> None:
        self.set_filters((), 0.0)


class ObservationDeckWorkspace(QWidget):
    library_changed = Signal()
    headline_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None, *, stat_strip: _StatStrip | None = None) -> None:
        super().__init__(parent)
        self._library: ObservationDeckLibrary | None = None
        self._map_result: ObservationMapResult | None = None
        self._worker: ObservationDeckScanWorker | None = None
        self._load_worker: ObservationDeckCacheWorker | None = None
        self._cache_load_started = False
        self._progress_dialog: QProgressDialog | None = None
        self._scan_merge = False
        self._folder_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        self._file_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)
        self._splitter_settings_sync_enabled = False
        self._stat_strip = stat_strip if stat_strip is not None else _StatStrip(self)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 4, 16, 12)
        root.setSpacing(8)

        left = QSplitter(Qt.Orientation.Vertical, self)
        left.setChildrenCollapsible(False)
        left.setHandleWidth(8)
        left.addWidget(self._build_tree_panel())
        left.addWidget(self._build_metadata_panel())
        left.setStretchFactor(0, 1)
        left.setStretchFactor(1, 1)
        left.setSizes([320, 300])
        left.setMinimumWidth(220)
        self._left_splitter = left

        self._heatmap = ContributionCalendarWidget()
        self._history = ObservationHistoryView(self._heatmap, self)
        self._history.setMinimumWidth(480)
        self._history.setMinimumHeight(480)
        self._history.year_clicked.connect(self._show_orbit_year)
        self._history.day_clicked.connect(self._show_orbit_day)
        self._history.set_night_inspector(self._orbit_night_detail)

        right = QSplitter(Qt.Orientation.Vertical, self)
        right.setChildrenCollapsible(False)
        right.setHandleWidth(8)
        self._filter_card = _FilterUsageCard(self)
        self._pie = self._filter_card.pie
        right.addWidget(self._filter_card)
        right.addWidget(self._build_targets_panel())
        right.setStretchFactor(0, 0)
        right.setStretchFactor(1, 1)
        right.setSizes([200, 580])
        right.setMinimumWidth(230)
        self._right_splitter = right

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(8)
        splitter.addWidget(left)
        splitter.addWidget(self._history)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([260, 720, 280])
        self._main_splitter = splitter
        root.addWidget(splitter, 1)

        left.splitterMoved.connect(self._handle_splitter_moved)
        right.splitterMoved.connect(self._handle_splitter_moved)
        splitter.splitterMoved.connect(self._handle_splitter_moved)
        self._set_headline("Your astrophotography archive at a glance")
        QTimer.singleShot(0, self._restore_splitter_sizes)

    def load_cached_library(self) -> None:
        if self._library is not None:
            return
        if self._load_worker is not None and self._load_worker.isRunning():
            return
        self._path_label.setText("Loading saved library…")
        self._summary_label.setText("Restoring the cached library. Folders are not being scanned.")
        self._set_headline("Restoring your archive…")
        QTimer.singleShot(0, self._start_cache_load)

    def _start_cache_load(self) -> None:
        if self._library is not None:
            return
        if self._load_worker is not None and self._load_worker.isRunning():
            return
        worker = ObservationDeckCacheWorker(self)
        worker.load_completed.connect(self._handle_cache_loaded)
        worker.load_failed.connect(self._handle_cache_failed)
        self._load_worker = worker
        worker.start()

    def _handle_cache_loaded(self, stored: object) -> None:
        self._load_worker = None
        if isinstance(stored, ObservationDeckLibrary) and stored.root_paths:
            try:
                self._apply_library(stored, persist=False)
            except Exception:
                self._library = None
                self._path_label.setText("No master folders")
                self._summary_label.setText("Could not restore the saved library.")
                self._set_headline("Your astrophotography archive at a glance")
            return
        self._path_label.setText("No master folders")
        self._summary_label.setText("Add a master folder to build the Observation Deck.")
        self._set_headline("Your astrophotography archive at a glance")

    def _handle_cache_failed(self, message: str) -> None:
        self._load_worker = None
        self._path_label.setText("No master folders")
        self._summary_label.setText(f"Could not restore the saved library: {message}")
        self._set_headline("Your astrophotography archive at a glance")

    def _restore_saved_library(self) -> None:
        self.load_cached_library()

    def stop_background_work(self) -> None:
        self._close_progress()
        for attr in ("_worker", "_load_worker"):
            worker = getattr(self, attr, None)
            if worker is None:
                continue
            try:
                worker.blockSignals(True)
            except RuntimeError:
                pass
            setattr(self, attr, None)

    def browse_for_folder(self) -> None:
        start = str(Path.home())
        if self._library is not None and self._library.root_paths:
            start = str(self._library.root_paths[-1])
        selected = QFileDialog.getExistingDirectory(self, "Add master imaging folder", start)
        if not selected:
            return
        self.add_master_folder(Path(selected).expanduser())

    def add_master_folder(self, root_path: Path) -> None:
        self._start_scan((root_path,), merge=True, title="Add master folder")

    def scan_folder(self, root_path: Path) -> None:
        self.add_master_folder(root_path)

    def rescan(self) -> None:
        if self._library is None or not self._library.root_paths:
            QMessageBox.information(self, "Observation Deck", "Add a master folder before rescanning.")
            return
        self._start_scan(self._library.root_paths, merge=False, title="Rescan library")

    def remove_selected_master_folder(self) -> None:
        if self._library is None:
            return
        masters = self._selected_master_roots()
        if not masters:
            QMessageBox.information(self, "Observation Deck", "Select a master folder in the library tree to remove it.")
            return
        library = self._library
        for root in masters:
            library = remove_observation_deck_root(library, root)
        self._apply_library(library)

    def _selected_master_roots(self) -> list[Path]:
        if self._library is None:
            return []
        known = {path_key(path) for path in self._library.root_paths}
        selected: list[Path] = []
        seen: set[str] = set()
        for item in self._tree.selectedItems():
            if not bool(item.data(0, _ROLE_IS_MASTER)):
                continue
            raw = item.data(0, _ROLE_PATH)
            if not raw:
                continue
            path = Path(str(raw))
            key = path_key(path)
            if key in known and key not in seen:
                selected.append(path)
                seen.add(key)
        return selected

    def _start_scan(self, root_paths: tuple[Path, ...], *, merge: bool, title: str) -> None:
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(self, "Scan in progress", "An Observation Deck scan is already running.")
            return
        resolved: list[Path] = []
        for root_path in root_paths:
            path = root_path.expanduser().resolve()
            if not path.is_dir():
                QMessageBox.warning(self, "Folder not found", f"Could not find:\n{path}")
                return
            resolved.append(path)
        if not resolved:
            return
        self._scan_merge = merge
        self._path_label.setText(self._roots_label(tuple(resolved) if not merge or self._library is None else (*self._library.root_paths, *resolved)))
        progress = QProgressDialog("Scanning master folders...", "", 0, 0, self)
        progress.setWindowTitle(title)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setCancelButton(None)
        progress.setMinimumDuration(0)
        progress.setRange(0, 0)
        progress.show()
        self._progress_dialog = progress
        self._worker = ObservationDeckScanWorker(
            tuple(resolved),
            observation_timezone=self._observation_timezone(),
            parent=self,
        )
        self._worker.progress_updated.connect(self._handle_progress)
        self._worker.scan_completed.connect(self._handle_scan_completed)
        self._worker.scan_failed.connect(self._handle_scan_failed)
        self._worker.start()

    def clear_loaded_work(self) -> None:
        self.stop_background_work()
        self._library = None
        self._tree.clear()
        self._path_label.setText("No master folders")
        self._summary_label.setText("")
        self._set_stats(None, "All targets")
        self._show_metadata_placeholder("Select an image to inspect its headers.")
        self._sync_library_actions()
        self.library_changed.emit()

    def _roots_label(self, roots: tuple[Path, ...]) -> str:
        if not roots:
            return "No master folders"
        if len(roots) == 1:
            return str(roots[0])
        names = ", ".join(path.name for path in roots)
        return f"{len(roots)} master folders: {names}"

    def can_save_orbit(self) -> bool:
        return self._library is not None and bool(self._library.images)

    def _set_headline(self, text: str) -> None:
        self.headline_changed.emit(text)

    def _folder_count_label(self, roots: tuple[Path, ...]) -> str:
        count = len(roots)
        if count == 0:
            return "No master folders"
        return f"{count} master folder" + ("" if count == 1 else "s")

    def _app_settings(self):
        window = self.window()
        ensure = getattr(window, "_ensure_settings", None)
        if callable(ensure):
            try:
                return ensure()
            except Exception:
                return None
        return None

    def _restore_splitter_sizes(self) -> None:
        settings = self._app_settings()
        self._splitter_settings_sync_enabled = False
        try:
            if settings is not None:
                main = getattr(settings, "observation_deck_main_splitter_sizes", None)
                left = getattr(settings, "observation_deck_left_splitter_sizes", None)
                right = getattr(settings, "observation_deck_right_splitter_sizes", None)
                if isinstance(main, list) and len(main) == 3:
                    self._main_splitter.setSizes(main)
                if isinstance(left, list) and len(left) == 2:
                    self._left_splitter.setSizes(left)
                if isinstance(right, list) and len(right) == 2:
                    self._right_splitter.setSizes(right)
        finally:
            self._splitter_settings_sync_enabled = True
        QTimer.singleShot(0, self._fit_filter_pane)

    def _handle_splitter_moved(self, _position: int, _index: int) -> None:
        if not self._splitter_settings_sync_enabled:
            return
        settings = self._app_settings()
        if settings is None:
            return
        main = [int(size) for size in self._main_splitter.sizes()]
        left = [int(size) for size in self._left_splitter.sizes()]
        right = [int(size) for size in self._right_splitter.sizes()]
        updated = False
        if len(main) == 3 and all(size > 0 for size in main):
            settings.observation_deck_main_splitter_sizes = main
            updated = True
        if len(left) == 2 and all(size > 0 for size in left):
            settings.observation_deck_left_splitter_sizes = left
            updated = True
        if len(right) == 2 and all(size > 0 for size in right):
            settings.observation_deck_right_splitter_sizes = right
            updated = True
        if not updated:
            return
        save = getattr(self.window(), "_save_settings_snapshot", None)
        if callable(save):
            save()

    def _show_library_menu(self, global_pos) -> None:
        menu = QMenu(self)
        add_action = menu.addAction("Add Master Folder…")
        add_action.triggered.connect(self.browse_for_folder)
        remove_action = menu.addAction("Remove Folder")
        remove_action.setEnabled(bool(self._selected_master_roots()))
        remove_action.triggered.connect(self.remove_selected_master_folder)
        menu.addSeparator()
        rescan_action = menu.addAction("Rescan")
        rescan_action.setEnabled(self._library is not None and bool(self._library.root_paths))
        rescan_action.triggered.connect(self.rescan)
        menu.exec(global_pos)

    def _sync_library_actions(self) -> None:
        has_roots = self._library is not None and bool(self._library.root_paths)
        self._library_rescan_action.setEnabled(has_roots)
        self._remove_folder_action.setEnabled(bool(self._selected_master_roots()))

    def _observation_timezone(self) -> str:
        window = self.window()
        ensure = getattr(window, "_ensure_settings", None)
        if callable(ensure):
            try:
                settings = ensure()
            except Exception:
                settings = None
            timezone = getattr(settings, "observation_timezone", None)
            if timezone:
                return str(timezone)
        return "UTC"

    def _build_tree_panel(self) -> QWidget:
        panel = QWidget(self)
        panel.setMinimumWidth(220)
        panel.setMinimumHeight(180)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 10, 12, 0)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(4)
        header.addWidget(_section_title("Library"), 1)
        self._add_folder_button = QToolButton(panel)
        self._add_folder_button.setText("+")
        self._add_folder_button.setAutoRaise(True)
        self._add_folder_button.setToolTip("Add Master Folder")
        self._add_folder_button.clicked.connect(self.browse_for_folder)
        header.addWidget(self._add_folder_button)
        self._library_menu_button = QToolButton(panel)
        self._library_menu_button.setText("•••")
        self._library_menu_button.setAutoRaise(True)
        self._library_menu_button.setToolTip("Manage folders")
        self._library_menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        library_menu = QMenu(self._library_menu_button)
        add_action = library_menu.addAction("Add Master Folder…")
        add_action.triggered.connect(self.browse_for_folder)
        self._remove_folder_action = library_menu.addAction("Remove Folder")
        self._remove_folder_action.triggered.connect(self.remove_selected_master_folder)
        library_menu.addSeparator()
        self._library_rescan_action = library_menu.addAction("Rescan")
        self._library_rescan_action.triggered.connect(self.rescan)
        self._library_menu_button.setMenu(library_menu)
        header.addWidget(self._library_menu_button)
        layout.addLayout(header)

        self._path_label = _muted(QLabel("No master folders"))
        self._path_label.setWordWrap(True)
        layout.addWidget(self._path_label)

        self._search = QLineEdit(panel)
        self._search.setPlaceholderText("Search targets and images…")
        self._search.textChanged.connect(self._apply_tree_filter)
        layout.addWidget(self._search)

        self._kind_combo = QComboBox(panel)
        self._kind_combo.addItem("All types", None)
        for kind in ImageKind:
            self._kind_combo.addItem(KIND_LABELS[kind], kind)
        self._kind_combo.currentIndexChanged.connect(self._apply_tree_filter)
        layout.addWidget(self._kind_combo)

        self._tree = QTreeWidget(panel)
        self._tree.setObjectName("observationDeckTree")
        self._tree.setHeaderLabels(("Name", "Type", "Filter"))
        self._tree.setUniformRowHeights(True)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._tree.setAnimated(False)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(
            lambda pos: self._show_library_menu(self._tree.viewport().mapToGlobal(pos))
        )
        self._tree.itemSelectionChanged.connect(self._handle_tree_selection)
        self._tree.itemExpanded.connect(self._handle_tree_expanded)
        header_view = self._tree.header()
        header_view.setStretchLastSection(False)
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self._tree, 1)
        self._summary_label = _muted(QLabel(""))
        self._summary_label.setWordWrap(True)
        layout.addWidget(self._summary_label)
        return panel

    def _build_targets_panel(self) -> QWidget:
        panel = QFrame(self)
        panel.setFrameShape(QFrame.Shape.StyledPanel)
        panel.setMinimumHeight(180)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 10)
        layout.setSpacing(8)
        targets_header = QHBoxLayout()
        targets_header.setContentsMargins(0, 0, 0, 0)
        targets_header.setSpacing(12)
        targets_header.addWidget(_section_title("Targets"))
        self._target_count_label = _muted(QLabel(""))
        targets_header.addWidget(self._target_count_label, 1)
        layout.addLayout(targets_header)
        self._target_table = QTableWidget(0, 6, panel)
        self._target_table.setHorizontalHeaderLabels(
            ("Target", "Integration", "Nights", "Lights", "Integrations", "Last Observed")
        )
        self._target_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._target_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._target_table.setSortingEnabled(True)
        self._target_table.setAlternatingRowColors(True)
        self._target_table.verticalHeader().setVisible(False)
        self._target_table.verticalHeader().setDefaultSectionSize(26)
        self._target_table.horizontalHeader().setStretchLastSection(False)
        self._target_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3, 4, 5):
            self._target_table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self._target_table, 1)
        return panel

    def _build_metadata_panel(self) -> QWidget:
        panel = QWidget(self)
        panel.setMinimumHeight(160)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(6)
        header_row = QHBoxLayout()
        header_row.addWidget(_section_title("Metadata"), 1)
        self._copy_headers_button = QPushButton("Copy headers")
        self._copy_headers_button.setEnabled(False)
        self._copy_headers_button.clicked.connect(self._copy_headers)
        header_row.addWidget(self._copy_headers_button)
        layout.addLayout(header_row)
        self._metadata_path = _muted(QLabel("Select an image to inspect its headers."))
        self._metadata_path.setWordWrap(True)
        layout.addWidget(self._metadata_path)
        self._metadata_search = QLineEdit(panel)
        self._metadata_search.setPlaceholderText("Filter keywords (CTYPE, CRVAL, FILTER, …)")
        self._metadata_search.textChanged.connect(self._apply_metadata_filter)
        layout.addWidget(self._metadata_search)
        self._metadata_table = QTableWidget(0, 3, panel)
        self._metadata_table.setObjectName("observationDeckMetadata")
        self._metadata_table.setHorizontalHeaderLabels(("Keyword", "Value", "Comment"))
        self._metadata_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._metadata_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._metadata_table.verticalHeader().setVisible(False)
        self._metadata_table.verticalHeader().setDefaultSectionSize(22)
        self._metadata_table.setWordWrap(False)
        self._metadata_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._metadata_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._metadata_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self._metadata_table, 1)
        return panel

    def _handle_progress(self, index: int, total: int, name: str) -> None:
        if self._progress_dialog is None:
            return
        if total > 0:
            self._progress_dialog.setRange(0, total)
            self._progress_dialog.setValue(index)
        self._progress_dialog.setLabelText(f"Scanning {index}/{max(total, 1)}: {name}")

    def _close_progress(self) -> None:
        if self._progress_dialog is None:
            return
        self._progress_dialog.close()
        self._progress_dialog.deleteLater()
        self._progress_dialog = None

    def _handle_scan_completed(self, result: object) -> None:
        self._worker = None
        self._close_progress()
        if not isinstance(result, ObservationDeckLibrary):
            QMessageBox.warning(self, "Observation Deck", "Scan returned an unexpected result.")
            return
        library = result
        if self._scan_merge:
            library = merge_observation_deck_libraries(self._library, result)
        self._apply_library(library)

    def _apply_library(self, library: ObservationDeckLibrary, *, persist: bool = True) -> None:
        self._library = library
        self._path_label.setText(self._folder_count_label(library.root_paths))
        self._path_label.setToolTip(self._roots_label(library.root_paths))
        self._populate_tree(library)
        self._set_stats(build_deck_stats(library.images, scope_label="All targets"), "All targets")
        self._show_metadata_placeholder("Select an image to inspect its headers.")
        extra = f" · {library.unreadable_files} unreadable" if library.unreadable_files else ""
        self._summary_label.setText(
            f"{library.scanned_files:,} image{'s' if library.scanned_files != 1 else ''}{extra}"
        )
        self.library_changed.emit()
        self._sync_library_actions()
        if persist:
            try:
                save_observation_deck_library(library)
            except OSError as exc:
                QMessageBox.warning(self, "Observation Deck", f"Could not save the library database:\n{exc}")

    def _handle_scan_failed(self, message: str) -> None:
        self._worker = None
        self._close_progress()
        QMessageBox.warning(self, "Observation Deck scan failed", message)

    def _populate_tree(self, library: ObservationDeckLibrary) -> None:
        self._tree.itemSelectionChanged.disconnect(self._handle_tree_selection)
        try:
            self._tree.clear()
            library_item = QTreeWidgetItem(("Library", "All masters", ""))
            library_item.setIcon(0, self._folder_icon)
            library_item.setData(0, _ROLE_PATH, "")
            library_item.setData(0, _ROLE_IS_FOLDER, True)
            library_item.setData(0, _ROLE_IS_LIBRARY, True)
            self._tree.addTopLevelItem(library_item)
            for root in library.root_paths:
                root_item = QTreeWidgetItem((root.name, "Master folder", ""))
                root_item.setIcon(0, self._folder_icon)
                root_item.setData(0, _ROLE_PATH, str(root))
                root_item.setData(0, _ROLE_IS_FOLDER, True)
                root_item.setData(0, _ROLE_IS_MASTER, True)
                library_item.addChild(root_item)
                folder_items: dict[str, QTreeWidgetItem] = {path_key(root): root_item}
                files_by_folder: dict[str, list[DeckImage]] = defaultdict(list)
                for image in library.images_for_path(root):
                    parts = relative_path_parts(image.path, root)
                    if parts is None:
                        continue
                    parent = root_item
                    current_parts: list[str] = []
                    for part in parts[:-1]:
                        current_parts.append(part)
                        current = Path(root, *current_parts)
                        key = path_key(current)
                        item = folder_items.get(key)
                        if item is None:
                            item = QTreeWidgetItem((part, "Folder", ""))
                            item.setIcon(0, self._folder_icon)
                            item.setData(0, _ROLE_PATH, str(current))
                            item.setData(0, _ROLE_IS_FOLDER, True)
                            parent.addChild(item)
                            folder_items[key] = item
                        parent = item
                    files_by_folder[path_key(Path(root, *parts[:-1]) if parts[:-1] else root)].append(image)
                for key, folder_item in folder_items.items():
                    pending = files_by_folder.get(key)
                    if not pending:
                        continue
                    folder_item.setData(0, _ROLE_LAZY_IMAGES, pending)
                    placeholder = QTreeWidgetItem((f"{len(pending)} images", "", ""))
                    placeholder.setData(0, _ROLE_LAZY_PLACEHOLDER, True)
                    placeholder.setData(0, _ROLE_IS_FOLDER, False)
                    folder_item.addChild(placeholder)
                root_item.setExpanded(False)
            library_item.setExpanded(True)
            self._tree.setCurrentItem(library_item)
            self._apply_tree_filter()
        finally:
            self._tree.itemSelectionChanged.connect(self._handle_tree_selection)

    def _handle_tree_expanded(self, item: QTreeWidgetItem) -> None:
        pending = item.data(0, _ROLE_LAZY_IMAGES)
        if not pending:
            return
        self._fill_folder_files(item, list(pending))

    def _fill_folder_files(self, item: QTreeWidgetItem, images: list[DeckImage]) -> None:
        item.setData(0, _ROLE_LAZY_IMAGES, None)
        for index in range(item.childCount() - 1, -1, -1):
            child = item.child(index)
            if bool(child.data(0, _ROLE_LAZY_PLACEHOLDER)):
                item.removeChild(child)
        for image in images:
            file_item = QTreeWidgetItem((image.path.name, image.kind_label, image.filter_name or ""))
            file_item.setIcon(0, self._file_icon)
            file_item.setData(0, _ROLE_PATH, str(image.path))
            file_item.setData(0, _ROLE_IS_FOLDER, False)
            file_item.setData(0, _ROLE_KIND, image.kind)
            item.addChild(file_item)

    def _materialize_visible_files(self) -> None:
        query = self._search.text().strip()
        kind_filter = self._kind_combo.currentData()
        if not query and kind_filter is None:
            return

        def visit(item: QTreeWidgetItem) -> None:
            pending = item.data(0, _ROLE_LAZY_IMAGES)
            if pending:
                self._fill_folder_files(item, list(pending))
            for index in range(item.childCount()):
                visit(item.child(index))

        for index in range(self._tree.topLevelItemCount()):
            visit(self._tree.topLevelItem(index))

    def _apply_tree_filter(self) -> None:
        self._materialize_visible_files()
        query = self._search.text().strip().casefold()
        kind_filter = self._kind_combo.currentData()

        def visit(item: QTreeWidgetItem, ancestor_matched: bool) -> bool:
            is_folder = bool(item.data(0, _ROLE_IS_FOLDER))
            name_ok = (not query) or query in item.text(0).casefold()
            kind_ok = True
            if not is_folder and kind_filter is not None:
                kind_ok = item.data(0, _ROLE_KIND) == kind_filter
            child_visible = False
            for index in range(item.childCount()):
                if visit(item.child(index), ancestor_matched or (is_folder and name_ok)):
                    child_visible = True
            visible = child_visible or ((name_ok or ancestor_matched) and kind_ok)
            item.setHidden(not visible)
            return visible

        for index in range(self._tree.topLevelItemCount()):
            visit(self._tree.topLevelItem(index), False)

    def _handle_tree_selection(self) -> None:
        if self._library is None:
            return
        items = self._tree.selectedItems()
        if not items:
            self._set_stats(build_deck_stats(self._library.images, scope_label="All targets"), "All targets")
            self._show_metadata_placeholder("Select an image to inspect its headers.")
            return
        folders: list[Path] = []
        images: list[Path] = []
        library_selected = False
        for item in items:
            if bool(item.data(0, _ROLE_IS_LIBRARY)) or bool(item.data(0, _ROLE_LAZY_PLACEHOLDER)):
                library_selected = library_selected or bool(item.data(0, _ROLE_IS_LIBRARY))
                continue
            raw = item.data(0, _ROLE_PATH)
            if not raw:
                continue
            path = Path(str(raw))
            if bool(item.data(0, _ROLE_IS_FOLDER)):
                folders.append(path)
            else:
                images.append(path)
        if library_selected and not folders and not images:
            self._set_stats(build_deck_stats(self._library.images, scope_label="All targets"), "All targets")
            self._show_metadata_placeholder("Select an image to inspect its headers.")
            return
        if folders:
            selected_images: list[DeckImage] = []
            seen: set[Path] = set()
            labels: list[str] = []
            for folder in folders:
                labels.append(folder.name)
                for image in self._library.images_for_path(folder):
                    if image.path in seen:
                        continue
                    seen.add(image.path)
                    selected_images.append(image)
            scope = labels[0] if len(labels) == 1 else f"{len(labels)} folders"
            master_roots = {path_key(path) for path in self._library.root_paths}
            if len(folders) == 1 and path_key(folders[0]) in master_roots:
                scope = folders[0].name
            self._set_stats(build_deck_stats(tuple(selected_images), scope_label=scope), scope)
        elif images:
            first = self._library.image_by_path(images[0])
            scope = first.target_name if first is not None else images[0].parent.name
            if first is not None:
                target_images = tuple(
                    image
                    for image in self._library.images
                    if image.target_name == first.target_name and image.root_path == first.root_path
                )
            else:
                target_images = tuple(
                    image for image in self._library.images if image.target_name == scope
                )
            self._set_stats(build_deck_stats(target_images, scope_label=scope), scope)
        if len(images) == 1 and not folders:
            self._load_metadata(images[0])
        elif images and not folders:
            self._show_metadata_placeholder(f"{len(images)} images selected. Choose one to inspect headers.")
        else:
            self._show_metadata_placeholder("Select an image to inspect its headers.")
        self.library_changed.emit()
        self._sync_library_actions()

    def _set_stats(self, stats: DeckStats | None, scope: str) -> None:
        if stats is None:
            self._stat_strip.set_metrics("—", "—", "—", "—")
            self._filter_card.clear()
            self._history.set_result(None)
            self._map_result = None
            self._target_table.setSortingEnabled(False)
            self._target_table.setRowCount(0)
            self._target_table.setSortingEnabled(True)
            self._target_count_label.setText("")
            self._set_headline("Your astrophotography archive at a glance")
            QTimer.singleShot(0, self._fit_filter_pane)
            return
        self._stat_strip.set_metrics(
            _format_hours(stats.total_exposure_seconds),
            f"{stats.target_count:,}",
            f"{stats.night_count:,}",
            f"{stats.subframe_count:,}",
        )
        self._filter_card.set_filters(stats.filters, stats.total_exposure_seconds)
        QTimer.singleShot(0, self._fit_filter_pane)
        map_result = None
        if self._library is not None:
            skipped = max(0, self._library.scanned_files - stats.subframe_count)
            map_result = observation_map_from_stats(
                self._library.root_path,
                stats,
                included_frames=stats.subframe_count,
                skipped_files=skipped,
            )
        self._map_result = map_result
        self._history.set_result(map_result)
        self._set_headline(self._headline_from_stats(stats, scope))
        count = len(stats.targets)
        self._target_count_label.setText(f"{count:,} object" + ("" if count == 1 else "s"))
        self._target_table.setSortingEnabled(False)
        self._target_table.setRowCount(count)
        show_root = len({target.root_name for target in stats.targets}) > 1
        for row, target in enumerate(stats.targets):
            name = f"{target.name} ({target.root_name})" if show_root else target.name
            last_observed = target.last_date.isoformat() if target.last_date is not None else "—"
            last_value = float(target.last_date.toordinal()) if target.last_date is not None else 0.0
            hours = target.exposure_seconds / 3600.0
            self._target_table.setItem(row, 0, QTableWidgetItem(name))
            self._target_table.setItem(row, 1, _NumericTableItem(_format_hours(target.exposure_seconds), hours))
            self._target_table.setItem(row, 2, _NumericTableItem(f"{target.night_count:,}", float(target.night_count)))
            self._target_table.setItem(row, 3, _NumericTableItem(f"{target.frame_count:,}", float(target.frame_count)))
            self._target_table.setItem(
                row, 4, _NumericTableItem(f"{target.integration_count:,}", float(target.integration_count))
            )
            last_item = _NumericTableItem(last_observed, last_value)
            last_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self._target_table.setItem(row, 5, last_item)
        self._target_table.setSortingEnabled(True)

    def _fit_filter_pane(self) -> None:
        self._filter_card._cap_height()
        sizes = self._right_splitter.sizes()
        if len(sizes) != 2:
            return
        total = sum(sizes)
        if total <= 0:
            return
        hint = max(self._filter_card.sizeHint().height(), self._filter_card.minimumSizeHint().height())
        hint = min(hint, self._filter_card.maximumHeight())
        targets = max(180, total - hint)
        filter_h = max(hint, total - targets)
        if abs(sizes[0] - filter_h) <= 6:
            return
        sync = self._splitter_settings_sync_enabled
        self._splitter_settings_sync_enabled = False
        try:
            self._right_splitter.setSizes([filter_h, targets])
        finally:
            self._splitter_settings_sync_enabled = sync

    def _headline_from_stats(self, stats: DeckStats, scope: str) -> str:
        span_years = 0.0
        if stats.first_date is not None and stats.last_date is not None:
            span_years = max(0.0, (stats.last_date - stats.first_date).days / 365.25)
        if scope != "All targets":
            return f"{scope} · {stats.night_count:,} nights · {_format_hours(stats.total_exposure_seconds)}"
        if span_years <= 0:
            span_text = "new archive"
        elif span_years < 1.0:
            months = max(1, int(round(span_years * 12.0)))
            span_text = "1 month archive" if months == 1 else f"{months} month archive"
        else:
            span_text = f"{span_years:.1f} yr archive"
        return f"{stats.target_count:,} targets · {stats.night_count:,} nights · {span_text}"

    def _show_metadata_placeholder(self, message: str) -> None:
        self._metadata_path.setText(message)
        self._metadata_table.setRowCount(0)
        self._copy_headers_button.setEnabled(False)

    def _load_metadata(self, path: Path) -> None:
        self._metadata_path.setText(path.name)
        self._metadata_path.setToolTip(str(path))
        try:
            header = read_header(path)
            rows = header_keyword_rows(header)
        except Exception as exc:
            self._metadata_table.setRowCount(1)
            self._metadata_table.setItem(0, 0, QTableWidgetItem("ERROR"))
            self._metadata_table.setItem(0, 1, QTableWidgetItem(str(exc).strip() or exc.__class__.__name__))
            self._metadata_table.setItem(0, 2, QTableWidgetItem(""))
            self._copy_headers_button.setEnabled(False)
            return
        self._metadata_table.setRowCount(len(rows))
        for row, (keyword, value, comment) in enumerate(rows):
            items = (QTableWidgetItem(keyword), QTableWidgetItem(value), QTableWidgetItem(comment))
            if any(keyword.upper().startswith(prefix) for prefix in _WCS_PREFIXES):
                for item in items:
                    item.setForeground(self.palette().color(QPalette.ColorRole.Highlight))
            for column, item in enumerate(items):
                self._metadata_table.setItem(row, column, item)
        self._copy_headers_button.setEnabled(bool(rows))
        self._apply_metadata_filter()

    def _apply_metadata_filter(self) -> None:
        query = self._metadata_search.text().strip().casefold()
        for row in range(self._metadata_table.rowCount()):
            item = self._metadata_table.item(row, 0)
            value = self._metadata_table.item(row, 1)
            comment = self._metadata_table.item(row, 2)
            haystack = " ".join(
                part.text()
                for part in (item, value, comment)
                if part is not None
            ).casefold()
            self._metadata_table.setRowHidden(row, bool(query) and query not in haystack)

    def _copy_headers(self) -> None:
        lines = ["Keyword\tValue\tComment"]
        for row in range(self._metadata_table.rowCount()):
            if self._metadata_table.isRowHidden(row):
                continue
            values = []
            for column in range(3):
                item = self._metadata_table.item(row, column)
                values.append("" if item is None else item.text())
            lines.append("\t".join(values))
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText("\n".join(lines))

    def _show_orbit_year(self, year: int) -> None:
        result = self._map_result
        if result is None:
            return
        days = tuple(item for item in result.days if item.observation_date.year == year)
        year_result = ObservationMapResult(
            root_path=result.root_path,
            days=days,
            included_frames=sum(item.frame_count for item in days),
            skipped_files=result.skipped_files,
            total_exposure_seconds=float(sum(item.exposure_seconds for item in days)),
            first_date=date(year, 1, 1),
            last_date=date(year, 12, 31),
        )
        max_seconds = max((item.exposure_seconds for item in result.days), default=0.0)
        self._heatmap.set_result(year_result, max_seconds=max_seconds)

    def _show_orbit_day(self, day: object) -> None:
        if not isinstance(day, date) or self._library is None:
            return
        images = [image for image in self._library.images if image.observation_date == day]
        names = sorted({image.target_name for image in images}, key=str.casefold)
        detail = f"{day.isoformat()}: {len(images)} image{'s' if len(images) != 1 else ''}"
        if names:
            detail += f" · {', '.join(names[:8])}"
        self._history.set_status(detail)

    def _orbit_night_detail(self, day: date) -> tuple[str, ...]:
        if self._library is None:
            return ()
        images = [
            image
            for image in self._library.images
            if image.observation_date == day and image.exposure_seconds
        ]
        if not images:
            return ()
        targets: list[str] = []
        seen_targets: set[str] = set()
        filters: list[str] = []
        seen_filters: set[str] = set()
        seconds = 0.0
        for image in images:
            seconds += float(image.exposure_seconds or 0.0)
            target_key = image.target_name.casefold()
            if target_key not in seen_targets:
                seen_targets.add(target_key)
                targets.append(image.target_name)
            name = (image.filter_name or "").strip()
            filter_key = name.casefold()
            if name and filter_key not in seen_filters:
                seen_filters.add(filter_key)
                filters.append(name)
        lines: list[str] = []
        if targets:
            lines.append(", ".join(targets[:4]))
        lines.append(f"{format_duration(seconds)} integration")
        lines.append(f"{len(images)} frame{'s' if len(images) != 1 else ''}")
        if filters:
            lines.append(" · ".join(filters[:6]))
        return tuple(lines)

    def _save_map(self) -> None:
        if self._library is None:
            return
        default_name = "observation-orbit.png"
        selected, _filter = QFileDialog.getSaveFileName(
            self,
            "Save observation orbit",
            str(Path.home() / default_name),
            "PNG Image (*.png)",
        )
        if not selected:
            return
        image = self._history.render_to_image()
        if image.isNull() or not image.save(selected, "PNG"):
            QMessageBox.warning(self, "Save failed", "Could not save the observation map image.")
            return
        QMessageBox.information(self, "Saved", f"Observation map saved to:\n{selected}")


class AstroToolsPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("observationDeckPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        chrome = QWidget(self)
        chrome.setObjectName("observationDeckChrome")
        chrome_layout = QHBoxLayout(chrome)
        chrome_layout.setContentsMargins(16, 8, 16, 6)
        chrome_layout.setSpacing(12)

        title_block = QVBoxLayout()
        title_block.setContentsMargins(0, 0, 0, 0)
        title_block.setSpacing(2)
        title = QLabel("Observation Deck")
        title_font = QFont(title.font())
        title_font.setPointSize(title_font.pointSize() + 3)
        title_font.setBold(True)
        title.setFont(title_font)
        title_block.addWidget(title)
        self._headline = _muted(QLabel("Your astrophotography archive at a glance"))
        self._headline.setWordWrap(True)
        title_block.addWidget(self._headline)
        chrome_layout.addLayout(title_block, 0)
        chrome_layout.addStretch(1)

        self._stat_strip = _StatStrip(chrome)
        chrome_layout.addWidget(self._stat_strip, 0, Qt.AlignmentFlag.AlignVCenter)
        chrome_layout.addStretch(1)

        self._rescan_button = QPushButton("Rescan")
        self._rescan_button.setEnabled(False)
        self._rescan_button.setToolTip("Re-read every master folder and update the saved Observation Deck database.")
        self._rescan_button.clicked.connect(self.rescan)
        chrome_layout.addWidget(self._rescan_button, 0, Qt.AlignmentFlag.AlignVCenter)

        self._overflow_button = QToolButton(chrome)
        self._overflow_button.setText("•••")
        self._overflow_button.setAutoRaise(True)
        self._overflow_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._overflow_button.setToolTip("More Observation Deck actions")
        overflow = QMenu(self._overflow_button)
        add_action = overflow.addAction("Add Master Folder…")
        add_action.triggered.connect(self.browse_for_folder)
        self._remove_action = overflow.addAction("Remove Folder")
        self._remove_action.triggered.connect(self.remove_selected_master_folder)
        overflow.addSeparator()
        self._save_action = overflow.addAction("Save Observation History…")
        self._save_action.triggered.connect(self._save_map)
        self._overflow_button.setMenu(overflow)
        chrome_layout.addWidget(self._overflow_button, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(chrome)

        self._workspace = ObservationDeckWorkspace(self, stat_strip=self._stat_strip)
        self._workspace.library_changed.connect(self._sync_chrome_actions)
        self._workspace.headline_changed.connect(self._headline.setText)
        layout.addWidget(self._workspace, 1)
        self._sync_chrome_actions()

    @property
    def observation_map_tool(self) -> ObservationDeckWorkspace:
        return self._workspace

    def browse_for_folder(self) -> None:
        self._workspace.browse_for_folder()

    def load_cached_library(self) -> None:
        self._workspace.load_cached_library()

    def stop_background_work(self) -> None:
        self._workspace.stop_background_work()

    def scan_folder(self, root_path: Path) -> None:
        self._workspace.scan_folder(root_path)

    def rescan(self) -> None:
        self._workspace.rescan()

    def remove_selected_master_folder(self) -> None:
        self._workspace.remove_selected_master_folder()

    def _save_map(self) -> None:
        self._workspace._save_map()

    def _sync_chrome_actions(self) -> None:
        library = self._workspace._library
        has_roots = library is not None and bool(library.root_paths)
        self._rescan_button.setEnabled(has_roots)
        self._remove_action.setEnabled(bool(self._workspace._selected_master_roots()))
        self._save_action.setEnabled(self._workspace.can_save_orbit())
        self._workspace._sync_library_actions()

    def clear_loaded_work(self) -> None:
        self._workspace.clear_loaded_work()
        self._sync_chrome_actions()
