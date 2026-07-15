from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QImage, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from photometry_app.core.observation_map import (
    ObservationMapDay,
    ObservationMapResult,
    build_observation_map,
    contribution_level,
    contribution_year_bounds,
    format_duration,
)


_LEVEL_COLORS = (
    QColor("#161b22"),
    QColor("#0e4429"),
    QColor("#006d32"),
    QColor("#26a641"),
    QColor("#39d353"),
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


class ObservationMapScanWorker(QThread):
    progress_updated = Signal(int, int, str)
    scan_completed = Signal(object)
    scan_failed = Signal(str)

    def __init__(self, root_path: Path, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._root_path = root_path

    def run(self) -> None:
        try:
            def progress(index: int, total: int, path: Path) -> None:
                self.progress_updated.emit(index, total, str(path.name))

            result = build_observation_map(self._root_path, progress_callback=progress)
        except Exception as exc:
            self.scan_failed.emit(str(exc).strip() or exc.__class__.__name__)
            return
        self.scan_completed.emit(result)


class ContributionCalendarWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._result: ObservationMapResult | None = None
        self._year: int | None = None
        self._cell = 12
        self._gap = 3
        self._left_label_width = 34
        self._top_label_height = 22
        self._cells: list[tuple[QRectF, date, ObservationMapDay | None]] = []
        self.setMouseTracking(True)
        self.setMinimumHeight(140)

    def set_result(self, result: ObservationMapResult | None, *, year: int | None = None) -> None:
        self._result = result
        self._year = year
        self._rebuild_geometry()
        self.update()
        self.updateGeometry()

    def sizeHint(self) -> QSize:
        weeks = self._week_count()
        width = self._left_label_width + weeks * (self._cell + self._gap) + 16
        height = self._top_label_height + 7 * (self._cell + self._gap) + 16
        return QSize(max(420, width), max(140, height))

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def _week_count(self) -> int:
        if self._result is None:
            return 53
        start, end = contribution_year_bounds(self._result, self._year)
        start_pad = start - timedelta(days=(start.weekday() + 1) % 7)
        end_pad = end + timedelta(days=(6 - ((end.weekday() + 1) % 7)))
        return max(1, ((end_pad - start_pad).days // 7) + 1)

    def _rebuild_geometry(self) -> None:
        self._cells.clear()
        if self._result is None:
            return
        day_lookup = self._result.day_map()
        start, end = contribution_year_bounds(self._result, self._year)
        # Align weeks to Sunday (GitHub-style).
        cursor = start - timedelta(days=(start.weekday() + 1) % 7)
        week_index = 0
        while cursor <= end + timedelta(days=(6 - ((end.weekday() + 1) % 7))):
            day_index = (cursor.weekday() + 1) % 7
            rect = QRectF(
                self._left_label_width + week_index * (self._cell + self._gap),
                self._top_label_height + day_index * (self._cell + self._gap),
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
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Open an imaging folder to build the observation map.")
            painter.end()
            return

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

        month_positions: dict[int, float] = {}
        for rect, day, _info in self._cells:
            if day.day == 1 or day not in month_positions:
                month_positions.setdefault(day.month, rect.left())
        for month, x in month_positions.items():
            painter.drawText(
                QRectF(x, 0, 48, self._top_label_height - 2),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                _MONTH_LABELS[month - 1],
            )

        for rect, _day, info in self._cells:
            level = 0 if info is None else contribution_level(info.exposure_seconds, max_seconds=max_seconds)
            painter.fillRect(rect, _LEVEL_COLORS[level])
            painter.setPen(QPen(_EMPTY_BORDER, 1))
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


class ObservationMapToolWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._result: ObservationMapResult | None = None
        self._root_path: Path | None = None
        self._worker: ObservationMapScanWorker | None = None
        self._progress_dialog: QProgressDialog | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        header = QLabel("Observation Map")
        header.setObjectName("astroToolsToolTitle")
        header_font = QFont(header.font())
        header_font.setPointSize(header_font.pointSize() + 4)
        header_font.setBold(True)
        header.setFont(header_font)
        root.addWidget(header)

        description = QLabel(
            "Choose a major imaging folder. The map walks each subfolder, counts only light "
            "subframes (.fit / .fits), skips masters and processed files, and fills each day "
            "by total exposure time."
        )
        description.setWordWrap(True)
        description.setStyleSheet("color: rgba(226, 232, 240, 0.78);")
        root.addWidget(description)

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(8)
        self._open_button = QPushButton("Open Imaging Folder...")
        self._open_button.clicked.connect(self._browse_for_folder)
        controls.addWidget(self._open_button)
        self._year_combo = QComboBox()
        self._year_combo.setMinimumWidth(110)
        self._year_combo.currentIndexChanged.connect(self._handle_year_changed)
        controls.addWidget(QLabel("Year"))
        controls.addWidget(self._year_combo)
        controls.addStretch(1)
        self._save_button = QPushButton("Save Map...")
        self._save_button.setEnabled(False)
        self._save_button.clicked.connect(self._save_map)
        controls.addWidget(self._save_button)
        root.addLayout(controls)

        self._path_label = QLabel("No folder selected")
        self._path_label.setWordWrap(True)
        self._path_label.setStyleSheet("color: rgba(148, 163, 184, 0.92);")
        root.addWidget(self._path_label)

        self._summary_label = QLabel("")
        self._summary_label.setWordWrap(True)
        root.addWidget(self._summary_label)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._calendar = ContributionCalendarWidget(scroll)
        scroll.setWidget(self._calendar)
        scroll.setMinimumHeight(180)
        root.addWidget(scroll, 1)

        legend = QHBoxLayout()
        legend.setContentsMargins(0, 0, 0, 0)
        legend.setSpacing(6)
        legend.addWidget(QLabel("Less"))
        for color in _LEVEL_COLORS:
            swatch = QLabel()
            swatch.setFixedSize(12, 12)
            swatch.setStyleSheet(
                f"background-color: {color.name()}; border: 1px solid {_EMPTY_BORDER.name()};"
            )
            legend.addWidget(swatch)
        legend.addWidget(QLabel("More"))
        legend.addStretch(1)
        root.addLayout(legend)

    def browse_for_folder(self) -> None:
        self._browse_for_folder()

    def _browse_for_folder(self) -> None:
        start = str(self._root_path) if self._root_path is not None else str(Path.home())
        selected = QFileDialog.getExistingDirectory(self, "Select major imaging folder", start)
        if not selected:
            return
        self.scan_folder(Path(selected).expanduser())

    def scan_folder(self, root_path: Path) -> None:
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(self, "Scan in progress", "An observation map scan is already running.")
            return
        resolved = root_path.expanduser()
        if not resolved.is_dir():
            QMessageBox.warning(self, "Folder not found", f"Could not find:\n{resolved}")
            return
        self._root_path = resolved
        self._path_label.setText(str(resolved))
        progress = QProgressDialog("Scanning imaging folder...", "", 0, 0, self)
        progress.setWindowTitle("Observation Map")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setCancelButton(None)
        progress.setMinimumDuration(0)
        progress.setRange(0, 0)
        progress.show()
        self._progress_dialog = progress
        self._worker = ObservationMapScanWorker(resolved, parent=self)
        self._worker.progress_updated.connect(self._handle_progress)
        self._worker.scan_completed.connect(self._handle_scan_completed)
        self._worker.scan_failed.connect(self._handle_scan_failed)
        self._worker.start()

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
        if not isinstance(result, ObservationMapResult):
            QMessageBox.warning(self, "Observation Map", "Scan returned an unexpected result.")
            return
        self._result = result
        self._populate_year_combo(result)
        self._refresh_calendar()
        self._save_button.setEnabled(True)
        self._summary_label.setText(
            f"{result.included_frames} light subframe{'s' if result.included_frames != 1 else ''} · "
            f"{format_duration(result.total_exposure_seconds)} total · "
            f"{len(result.days)} night{'s' if len(result.days) != 1 else ''} · "
            f"{result.skipped_files} skipped"
        )

    def _handle_scan_failed(self, message: str) -> None:
        self._worker = None
        self._close_progress()
        QMessageBox.warning(self, "Observation Map failed", message)

    def _populate_year_combo(self, result: ObservationMapResult) -> None:
        self._year_combo.blockSignals(True)
        self._year_combo.clear()
        if result.first_date is None or result.last_date is None:
            self._year_combo.addItem("No data", None)
            self._year_combo.setEnabled(False)
            self._year_combo.blockSignals(False)
            return
        self._year_combo.setEnabled(True)
        years = list(range(result.first_date.year, result.last_date.year + 1))
        for year in years:
            self._year_combo.addItem(str(year), year)
        self._year_combo.setCurrentIndex(len(years) - 1)
        self._year_combo.blockSignals(False)

    def _selected_year(self) -> int | None:
        data = self._year_combo.currentData()
        return int(data) if data is not None else None

    def _handle_year_changed(self, _index: int) -> None:
        self._refresh_calendar()

    def _refresh_calendar(self) -> None:
        self._calendar.set_result(self._result, year=self._selected_year())

    def _save_map(self) -> None:
        if self._result is None:
            return
        default_name = f"observation-map-{self._selected_year() or 'all'}.png"
        selected, _filter = QFileDialog.getSaveFileName(
            self,
            "Save observation map",
            str(Path.home() / default_name),
            "PNG Image (*.png)",
        )
        if not selected:
            return
        image = self._calendar.render_to_image()
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
        chrome_layout.setContentsMargins(16, 12, 16, 12)
        chrome_layout.setSpacing(10)

        title = QLabel("Observation Deck")
        title_font = QFont(title.font())
        title_font.setPointSize(title_font.pointSize() + 2)
        title_font.setBold(True)
        title.setFont(title_font)
        chrome_layout.addWidget(title)

        self._tool_combo = QComboBox(chrome)
        self._tool_combo.addItem("Observation Map", "observation_map")
        self._tool_combo.currentIndexChanged.connect(self._handle_tool_changed)
        chrome_layout.addWidget(self._tool_combo)
        chrome_layout.addStretch(1)
        layout.addWidget(chrome)

        self._stack = QStackedWidget(self)
        self._observation_map_tool = ObservationMapToolWidget(self._stack)
        self._stack.addWidget(self._observation_map_tool)
        layout.addWidget(self._stack, 1)

        self.setStyleSheet(
            """
            #observationDeckPanel {
                background-color: #0b1220;
            }
            #observationDeckChrome {
                background-color: rgba(15, 23, 42, 0.92);
                border-bottom: 1px solid rgba(148, 163, 184, 0.18);
            }
            """
        )

    @property
    def observation_map_tool(self) -> ObservationMapToolWidget:
        return self._observation_map_tool

    def _handle_tool_changed(self, index: int) -> None:
        self._stack.setCurrentIndex(max(0, index))
