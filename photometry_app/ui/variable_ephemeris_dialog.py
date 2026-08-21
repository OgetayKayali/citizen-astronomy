from __future__ import annotations

from datetime import datetime
import math

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.colors import to_rgb
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter
from matplotlib import dates as mdates
import numpy as np
from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from photometry_app.core.variable_ephemeris import (
    TonightSchedule,
    VariableEphemerisEvent,
    VariableEphemerisForecast,
    VariableEphemerisLookupError,
    build_variable_ephemeris_forecast,
    compute_site_tonight_schedule,
    daylight_sky_factor,
    ephemeris_sky_rgb,
    ephemeris_sky_text_rgb,
    event_sky_tooltip,
    format_eclipse_window,
    format_site_coordinate_lines,
    _is_eclipsing_type,
)
from photometry_app.core.settings import DEFAULT_EPHEMERIS_MIN_ALTITUDE_DEG, normalize_ephemeris_min_altitude_deg


class VariableEphemerisWorker(QThread):
    lookup_completed = Signal(object)
    lookup_failed = Signal(str)

    def __init__(
        self,
        star_name: str,
        *,
        timezone_name: str,
        latitude_deg: float | None,
        longitude_deg: float | None,
        elevation_m: float | None,
        min_altitude_deg: float = DEFAULT_EPHEMERIS_MIN_ALTITUDE_DEG,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._star_name = star_name
        self._timezone_name = timezone_name
        self._latitude_deg = latitude_deg
        self._longitude_deg = longitude_deg
        self._elevation_m = elevation_m
        self._min_altitude_deg = normalize_ephemeris_min_altitude_deg(min_altitude_deg)

    def run(self) -> None:
        try:
            forecast = build_variable_ephemeris_forecast(
                self._star_name,
                timezone_name=self._timezone_name,
                latitude_deg=self._latitude_deg,
                longitude_deg=self._longitude_deg,
                elevation_m=self._elevation_m,
                min_altitude_deg=self._min_altitude_deg,
            )
        except VariableEphemerisLookupError as exc:
            self.lookup_failed.emit(str(exc))
            return
        except Exception as exc:
            self.lookup_failed.emit(f"Ephemeris lookup failed: {exc}")
            return
        self.lookup_completed.emit(forecast)


class TonightScheduleWorker(QThread):
    schedule_completed = Signal(object)
    schedule_failed = Signal(str)

    def __init__(
        self,
        *,
        timezone_name: str,
        latitude_deg: float | None,
        longitude_deg: float | None,
        elevation_m: float | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._timezone_name = timezone_name
        self._latitude_deg = latitude_deg
        self._longitude_deg = longitude_deg
        self._elevation_m = elevation_m

    def run(self) -> None:
        try:
            schedule = compute_site_tonight_schedule(
                timezone_name=self._timezone_name,
                latitude_deg=self._latitude_deg,
                longitude_deg=self._longitude_deg,
                elevation_m=self._elevation_m,
            )
        except Exception as exc:
            self.schedule_failed.emit(str(exc))
            return
        self.schedule_completed.emit(schedule)


class VariableEphemerisDialog(QDialog):
    def __init__(
        self,
        *,
        timezone_name: str = "UTC",
        latitude_deg: float | None = None,
        longitude_deg: float | None = None,
        elevation_m: float | None = None,
        min_altitude_deg: float = DEFAULT_EPHEMERIS_MIN_ALTITUDE_DEG,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Variable Ephemeris")
        self.setMinimumWidth(1240)
        self.setMinimumHeight(580)
        self.resize(1420, 680)
        self._timezone_name = timezone_name or "UTC"
        self._latitude_deg = latitude_deg
        self._longitude_deg = longitude_deg
        self._elevation_m = elevation_m
        self._min_altitude_deg = normalize_ephemeris_min_altitude_deg(min_altitude_deg)
        self._worker: VariableEphemerisWorker | None = None
        self._schedule_worker: TonightScheduleWorker | None = None
        self._forecast: VariableEphemerisForecast | None = None
        self._site_schedule: TonightSchedule | None = None

        self._name_input = QLineEdit(self)
        self._name_input.setPlaceholderText("e.g. RR Lyr, W UMa, del Cep")
        self._name_input.setClearButtonEnabled(True)
        self._name_input.returnPressed.connect(self._handle_search_clicked)
        self._search_button = QPushButton("Search", self)
        self._search_button.setDefault(True)
        self._search_button.clicked.connect(self._handle_search_clicked)

        search_row = QHBoxLayout()
        search_row.setContentsMargins(0, 0, 0, 0)
        search_row.setSpacing(8)
        search_row.addWidget(self._name_input, 1)
        search_row.addWidget(self._search_button)

        timezone_note = QLabel(
            f"Times use Settings timezone {self._timezone_name}."
            + (
                f" Altitude and darkness use the saved observing site (minimum altitude {self._min_altitude_deg:g}°)."
                if latitude_deg is not None and longitude_deg is not None
                else " Set Observing Latitude/Longitude in Settings to check whether the star is up after dark."
            ),
            self,
        )
        timezone_note.setWordWrap(True)
        timezone_note.setObjectName("variableEphemerisTimezoneNote")

        self._status_label = QLabel("Enter a variable-star name. No image is required.", self)
        self._status_label.setWordWrap(True)

        self._star_name_label = QLabel("—", self)
        self._star_type_label = QLabel("—", self)
        self._period_label = QLabel("—", self)
        self._epoch_label = QLabel("—", self)
        self._magnitude_label = QLabel("—", self)
        self._phase_label = QLabel("—", self)
        self._verdict_label = QLabel("—", self)
        self._verdict_label.setWordWrap(True)
        self._verdict_label.setObjectName("variableEphemerisVerdict")

        details = QWidget(self)
        details_layout = QFormLayout(details)
        details_layout.setContentsMargins(0, 0, 0, 0)
        details_layout.setSpacing(6)
        details_layout.addRow("Star", self._star_name_label)
        details_layout.addRow("Type", self._star_type_label)
        details_layout.addRow("Period", self._period_label)
        details_layout.addRow("Epoch (HJD)", self._epoch_label)
        details_layout.addRow("Mag range", self._magnitude_label)
        details_layout.addRow("Phase now", self._phase_label)
        details_layout.addRow("Tonight", self._verdict_label)

        self._events_table = QTableWidget(0, 5, self)
        self._events_table.setObjectName("variableEphemerisEvents")
        self._events_table.setHorizontalHeaderLabels(("Event", "Local time", "Window", "Alt", "Observable"))
        self._events_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._events_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._events_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._events_table.setAlternatingRowColors(False)
        self._events_table.verticalHeader().setVisible(False)
        header = self._events_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self._events_table.setToolTip(
            "Upcoming maxima or minima. Window is the VSX eclipse duration around mid-eclipse. "
            "Row color follows daylight: gold is sun, navy is dark."
        )
        self._events_table.setStyleSheet(
            "QTableWidget#variableEphemerisEvents {"
            "background-color: transparent;"
            "gridline-color: rgba(255, 255, 255, 36);"
            "}"
            "QTableWidget#variableEphemerisEvents::item:selected {"
            "border: 1px solid rgba(255, 255, 255, 170);"
            "}"
        )

        legend = QWidget(self)
        legend_layout = QHBoxLayout(legend)
        legend_layout.setContentsMargins(0, 0, 0, 0)
        legend_layout.setSpacing(8)
        day_label = QLabel("Day", self)
        night_label = QLabel("Night", self)
        day_label.setToolTip("Bright gold rows are in sunlight.")
        night_label.setToolTip("Navy rows are in darkness.")
        gradient_bar = QLabel(self)
        gradient_bar.setFixedHeight(10)
        gradient_bar.setMinimumWidth(120)
        gradient_bar.setToolTip("Row color follows sun altitude, or local time if no observing site is set.")
        gradient_bar.setStyleSheet(
            "border-radius: 4px;"
            "background: qlineargradient(x1:0, y1:0, x2:1, y2:0,"
            " stop:0 #f8dc76, stop:0.35 #ec9c44, stop:0.55 #ba4c38,"
            " stop:0.75 #583270, stop:1 #101630);"
        )
        legend_layout.addWidget(day_label)
        legend_layout.addWidget(gradient_bar, 1)
        legend_layout.addWidget(night_label)

        left = QWidget(self)
        left.setMinimumWidth(360)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)
        left_layout.addLayout(search_row)
        left_layout.addWidget(timezone_note)
        left_layout.addWidget(self._status_label)
        left_layout.addWidget(details)
        left_layout.addWidget(self._events_table, 1)
        left_layout.addWidget(legend)

        self._schedule_panel = TonightSchedulePanel(self)
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(left)
        splitter.addWidget(self._schedule_panel)
        splitter.setChildrenCollapsible(False)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 7)
        splitter.setSizes((420, 860))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        layout.addWidget(splitter, 1)
        layout.addWidget(buttons)

        self._name_input.setFocus()
        self._start_site_schedule_worker()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._stop_worker()
        self._stop_schedule_worker()
        super().closeEvent(event)

    def _handle_search_clicked(self) -> None:
        name = self._name_input.text().strip()
        if not name:
            self._status_label.setText("Enter a variable-star name to search VSX.")
            return
        self._stop_worker()
        self._search_button.setEnabled(False)
        self._name_input.setEnabled(False)
        self._status_label.setText(f"Looking up {name} in VSX…")
        worker = VariableEphemerisWorker(
            name,
            timezone_name=self._timezone_name,
            latitude_deg=self._latitude_deg,
            longitude_deg=self._longitude_deg,
            elevation_m=self._elevation_m,
            min_altitude_deg=self._min_altitude_deg,
            parent=self,
        )
        worker.lookup_completed.connect(self._handle_lookup_completed)
        worker.lookup_failed.connect(self._handle_lookup_failed)
        worker.finished.connect(self._handle_worker_finished)
        self._worker = worker
        worker.start()

    def _handle_lookup_completed(self, forecast: object) -> None:
        if not isinstance(forecast, VariableEphemerisForecast):
            self._handle_lookup_failed("Ephemeris lookup returned an unexpected result.")
            return
        self._forecast = forecast
        self._populate_forecast(forecast)

    def _handle_lookup_failed(self, message: str) -> None:
        self._forecast = None
        self._status_label.setText(message)
        self._star_name_label.setText("—")
        self._star_type_label.setText("—")
        self._period_label.setText("—")
        self._epoch_label.setText("—")
        self._magnitude_label.setText("—")
        self._phase_label.setText("—")
        self._verdict_label.setText("—")
        self._events_table.setRowCount(0)
        self._restore_site_schedule()

    def _handle_worker_finished(self) -> None:
        self._worker = None
        self._search_button.setEnabled(True)
        self._name_input.setEnabled(True)
        self._name_input.setFocus()
        self._name_input.selectAll()

    def _stop_worker(self) -> None:
        worker = self._worker
        if worker is None:
            return
        self._worker = None
        worker.lookup_completed.disconnect(self._handle_lookup_completed)
        worker.lookup_failed.disconnect(self._handle_lookup_failed)
        worker.finished.disconnect(self._handle_worker_finished)
        worker.requestInterruption()
        worker.wait(200)

    def _start_site_schedule_worker(self) -> None:
        if self._latitude_deg is None or self._longitude_deg is None:
            self._schedule_panel.show_placeholder(
                "Set Observing Latitude/Longitude in Settings to plot tonight's altitude and twilight."
            )
            return
        self._schedule_panel.show_placeholder("Loading tonight's schedule…")
        worker = TonightScheduleWorker(
            timezone_name=self._timezone_name,
            latitude_deg=self._latitude_deg,
            longitude_deg=self._longitude_deg,
            elevation_m=self._elevation_m,
            parent=self,
        )
        worker.schedule_completed.connect(self._handle_site_schedule_completed)
        worker.schedule_failed.connect(self._handle_site_schedule_failed)
        worker.finished.connect(self._handle_schedule_worker_finished)
        self._schedule_worker = worker
        worker.start()

    def _handle_site_schedule_completed(self, schedule: object) -> None:
        if not isinstance(schedule, TonightSchedule):
            self._handle_site_schedule_failed("Tonight's altitude chart is not available.")
            return
        self._site_schedule = schedule
        if self._forecast is not None and self._forecast.tonight_schedule is not None:
            return
        self._schedule_panel.show_schedule(schedule)

    def _handle_site_schedule_failed(self, message: str) -> None:
        if self._forecast is not None and self._forecast.tonight_schedule is not None:
            return
        self._schedule_panel.show_placeholder(message or "Tonight's altitude chart is not available.")

    def _handle_schedule_worker_finished(self) -> None:
        self._schedule_worker = None

    def _stop_schedule_worker(self) -> None:
        worker = self._schedule_worker
        if worker is None:
            return
        self._schedule_worker = None
        worker.schedule_completed.disconnect(self._handle_site_schedule_completed)
        worker.schedule_failed.disconnect(self._handle_site_schedule_failed)
        worker.finished.disconnect(self._handle_schedule_worker_finished)
        worker.requestInterruption()
        worker.wait(200)

    def _restore_site_schedule(self) -> None:
        if self._site_schedule is not None:
            self._schedule_panel.show_schedule(self._site_schedule)
            return
        if self._latitude_deg is None or self._longitude_deg is None:
            self._schedule_panel.show_placeholder(
                "Set Observing Latitude/Longitude in Settings to plot tonight's altitude and twilight."
            )
            return
        self._schedule_panel.show_placeholder("Loading tonight's schedule…")

    def _populate_forecast(self, forecast: VariableEphemerisForecast) -> None:
        star = forecast.star
        self._status_label.setText(f"VSX match from {star.source}.")
        self._star_name_label.setText(star.name)
        self._star_type_label.setText(star.variability_type or "—")
        if star.period_days is None:
            self._period_label.setText("—")
        else:
            period_text = f"{star.period_days:.6g} d"
            if star.eclipse_duration_hours is not None and _is_eclipsing_type(star.variability_type):
                period_text += f"  (eclipse {star.eclipse_duration_hours:.2f} h)"
            self._period_label.setText(period_text)
        self._epoch_label.setText("—" if star.epoch_hjd is None else f"{star.epoch_hjd:.5f}")
        self._magnitude_label.setText(_format_magnitude_range(star.max_mag, star.min_mag))
        self._phase_label.setText("—" if forecast.current_phase is None else f"{forecast.current_phase:.3f}")
        self._verdict_label.setText(forecast.summary)
        self._events_table.setRowCount(len(forecast.events))
        for row, event in enumerate(forecast.events):
            background, foreground = _row_colors(event)
            tooltip = event_sky_tooltip(event)
            self._set_event_cell(row, 0, event.kind, background, foreground, tooltip)
            self._set_event_cell(row, 1, _format_event_local(event.local), background, foreground, tooltip)
            self._set_event_cell(row, 2, format_eclipse_window(event), background, foreground, tooltip)
            altitude_text = "—" if event.altitude_deg is None else f"{event.altitude_deg:.0f}°"
            self._set_event_cell(row, 3, altitude_text, background, foreground, tooltip)
            observable = event.window_observable if event.window_observable is not None else event.observable
            self._set_event_cell(
                row,
                4,
                "Yes" if observable else "No",
                background,
                foreground,
                tooltip,
            )
        self._schedule_panel.show_forecast(forecast, fallback_schedule=self._site_schedule)

    def _set_event_cell(
        self,
        row: int,
        column: int,
        text: str,
        background: QColor,
        foreground: QColor,
        tooltip: str,
    ) -> None:
        item = QTableWidgetItem(text)
        item.setTextAlignment(
            Qt.AlignmentFlag.AlignCenter
            if column not in (1, 2)
            else Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        item.setBackground(QBrush(background))
        item.setForeground(QBrush(foreground))
        item.setToolTip(tooltip)
        self._events_table.setItem(row, column, item)


def _format_magnitude_range(max_mag: float | None, min_mag: float | None) -> str:
    if max_mag is None and min_mag is None:
        return "—"
    if max_mag is None:
        return f"— – {min_mag:.2f}"
    if min_mag is None:
        return f"{max_mag:.2f} – —"
    return f"{max_mag:.2f} – {min_mag:.2f}"


def _format_event_local(value: datetime) -> str:
    return value.strftime("%a %d %b %Y %H:%M")


def _row_colors(event: VariableEphemerisEvent) -> tuple[QColor, QColor]:
    factor = daylight_sky_factor(sun_altitude_deg=event.sun_altitude_deg, local=event.local)
    background = ephemeris_sky_rgb(factor)
    foreground = ephemeris_sky_text_rgb(background)
    return QColor(*background), QColor(*foreground)


_SCHEDULE_FACE = "#0b1220"
_SCHEDULE_AXIS = "#f4f7ff"
_MOON_CURVE_COLOR = "#f4f7ff"
_STAR_CURVE_COLOR = "#ffbf47"
_SCHEDULE_ALTITUDE_MAX_DEG = 90.0
_ECLIPSE_WINDOW_PEAK_ALPHA = 0.42
_ECLIPSE_WINDOW_MIN_SAMPLES = 48
_ECLIPSE_WINDOW_MAX_SAMPLES = 160
_TWILIGHT_BAND_COLORS = (
    (-18.0, "#070b18"),
    (-12.0, "#141a30"),
    (-6.0, "#222a44"),
    (0.0, "#323a54"),
    (90.0, "#3e4860"),
)


class TonightSchedulePanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(620)
        self.setObjectName("tonightSchedulePanel")
        self.setStyleSheet(
            "QWidget#tonightSchedulePanel { background-color: transparent; }"
            "QLabel { color: #e8eef8; }"
        )

        self._title = QLabel("Tonight's Schedule", self)
        title_font = self._title.font()
        title_font.setPointSize(title_font.pointSize() + 4)
        title_font.setBold(True)
        self._title.setFont(title_font)

        self._timezone_label = QLabel("All times are shown in observatory timezone.", self)
        self._timezone_label.setWordWrap(True)
        self._timezone_label.setStyleSheet("color: #b8c4d8;")

        self._coords_dms_label = QLabel("", self)
        self._coords_decimal_label = QLabel("", self)
        self._coords_dms_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._coords_decimal_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._moon_label = QLabel("", self)

        header_right = QWidget(self)
        header_right_layout = QVBoxLayout(header_right)
        header_right_layout.setContentsMargins(0, 0, 0, 0)
        header_right_layout.setSpacing(2)
        header_right_layout.addWidget(self._coords_dms_label, 0, Qt.AlignmentFlag.AlignRight)
        header_right_layout.addWidget(self._coords_decimal_label, 0, Qt.AlignmentFlag.AlignRight)
        header_right_layout.addWidget(self._moon_label, 0, Qt.AlignmentFlag.AlignRight)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header_left = QVBoxLayout()
        header_left.setContentsMargins(0, 0, 0, 0)
        header_left.setSpacing(4)
        header_left.addWidget(self._title)
        header_left.addWidget(self._timezone_label)
        header.addLayout(header_left, 1)
        header.addWidget(header_right, 0, Qt.AlignmentFlag.AlignTop)

        self._figure = Figure(figsize=(7.4, 4.2), facecolor=_SCHEDULE_FACE)
        self._axes = self._figure.add_subplot(111)
        self._canvas = FigureCanvasQTAgg(self._figure)
        self._canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._canvas.setStyleSheet(f"background-color: {_SCHEDULE_FACE};")
        self._canvas.setCursor(Qt.CursorShape.CrossCursor)
        self._canvas.mpl_connect("motion_notify_event", self._handle_hover_move)
        self._canvas.mpl_connect("axes_leave_event", self._handle_hover_leave)
        self._canvas.mpl_connect("figure_leave_event", self._handle_hover_leave)

        self._hover_times: list[float] = []
        self._hover_moon: list[float | None] = []
        self._hover_star: list[float | None] = []
        self._hover_star_name: str | None = None
        self._hover_vline = None
        self._hover_moon_marker = None
        self._hover_star_marker = None
        self._hover_annot = None

        self._placeholder = QLabel(
            "Set Observing Latitude/Longitude in Settings to plot tonight's altitude and twilight.",
            self,
        )
        self._placeholder.setWordWrap(True)
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet("color: #9aabc4;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 0, 0, 0)
        layout.setSpacing(8)
        layout.addLayout(header)
        layout.addWidget(self._canvas, 1)
        layout.addWidget(self._placeholder)
        self.show_placeholder(
            "Set Observing Latitude/Longitude in Settings to plot tonight's altitude and twilight."
        )

    def show_placeholder(self, message: str) -> None:
        self._timezone_label.setText("All times are shown in observatory timezone.")
        self._coords_dms_label.setText("")
        self._coords_decimal_label.setText("")
        self._moon_label.setText("")
        self._placeholder.setText(message)
        self._placeholder.show()
        self._canvas.hide()
        self._reset_hover()
        self._clear_axes()
        self._canvas.draw_idle()

    def show_forecast(
        self,
        forecast: VariableEphemerisForecast,
        fallback_schedule: TonightSchedule | None = None,
    ) -> None:
        schedule = forecast.tonight_schedule or fallback_schedule
        if schedule is None:
            if forecast.site_configured:
                self.show_placeholder("Tonight's altitude chart is not available for this search.")
            else:
                self.show_placeholder(
                    "Set Observing Latitude/Longitude in Settings to plot tonight's altitude and twilight."
                )
            return
        star_name = forecast.star.name if forecast.tonight_schedule is not None else None
        if star_name is None and any(sample.star_altitude_deg is not None for sample in schedule.samples):
            star_name = forecast.star.name
        self.show_schedule(schedule, events=forecast.events, star_name=star_name)

    def show_schedule(
        self,
        schedule: TonightSchedule,
        *,
        events: list[VariableEphemerisEvent] | tuple[VariableEphemerisEvent, ...] = (),
        star_name: str | None = None,
    ) -> None:
        self._placeholder.hide()
        self._canvas.show()
        self._timezone_label.setText(
            f"All times are shown in observatory timezone ({schedule.timezone_name})."
        )
        dms, decimal = format_site_coordinate_lines(schedule.latitude_deg, schedule.longitude_deg)
        self._coords_dms_label.setText(dms)
        self._coords_decimal_label.setText(decimal)
        if schedule.moon_illumination_percent is None:
            self._moon_label.setText("Moon Illumination: —")
        else:
            self._moon_label.setText(f"Moon Illumination: {schedule.moon_illumination_percent:.0f}%")
        self._draw_schedule(schedule, list(events), star_name)
        self._canvas.draw_idle()

    def _clear_axes(self) -> None:
        self._axes.clear()
        self._hover_vline = None
        self._hover_moon_marker = None
        self._hover_star_marker = None
        self._hover_annot = None
        self._style_axes()

    def _draw_schedule(
        self,
        schedule: TonightSchedule,
        events: list[VariableEphemerisEvent],
        star_name: str | None,
    ) -> None:
        self._axes.clear()
        self._style_axes()
        samples = schedule.samples
        if len(samples) < 2:
            return
        times = [_mpl_local_time(sample.local) for sample in samples]
        self._draw_twilight_bands(samples, times)
        moon_alts = [sample.moon_altitude_deg for sample in samples]
        if any(altitude is not None for altitude in moon_alts):
            self._axes.plot(
                times,
                [float("nan") if altitude is None else altitude for altitude in moon_alts],
                color=_MOON_CURVE_COLOR,
                linewidth=1.8,
                zorder=3,
                label="Moon",
            )
        star_alts = [sample.star_altitude_deg for sample in samples]
        if star_name and any(altitude is not None for altitude in star_alts):
            self._axes.plot(
                times,
                [float("nan") if altitude is None else altitude for altitude in star_alts],
                color=_STAR_CURVE_COLOR,
                linewidth=1.8,
                zorder=4,
                label=star_name,
            )
        self._axes.set_ylim(0.0, _SCHEDULE_ALTITUDE_MAX_DEG)
        self._axes.set_yticks((0, 15, 30, 45, 60, 75, 90))
        self._axes.set_xlim(times[0], times[-1])
        self._axes.set_ylabel("Altitude (degrees)")
        self._axes.xaxis.set_major_locator(mdates.HourLocator(interval=1))
        self._axes.xaxis.set_major_formatter(FuncFormatter(_format_hour_tick))
        self._axes.tick_params(axis="x", labelrotation=0, labelsize=8)
        self._axes.tick_params(axis="y", labelsize=8)
        self._mark_twilight(schedule.marks, _SCHEDULE_ALTITUDE_MAX_DEG)
        self._mark_eclipse_windows(schedule, events)
        self._mark_events(schedule, events, _SCHEDULE_ALTITUDE_MAX_DEG)
        handles, labels = self._axes.get_legend_handles_labels()
        if labels:
            legend = self._axes.legend(
                handles,
                labels,
                loc="upper right",
                fontsize=8,
                framealpha=0.45,
                facecolor="#121a2c",
                edgecolor="#3d4d6c",
                labelcolor=_SCHEDULE_AXIS,
            )
            legend.set_zorder(6)
        self._prepare_hover(times, moon_alts, star_alts, star_name)
        self._figure.subplots_adjust(left=0.08, right=0.98, top=0.86, bottom=0.12)

    def _draw_twilight_bands(self, samples, times) -> None:
        start_index = 0
        color = _twilight_band_color(samples[0].sun_altitude_deg)
        for index in range(1, len(samples)):
            next_color = _twilight_band_color(samples[index].sun_altitude_deg)
            if next_color == color:
                continue
            self._axes.axvspan(times[start_index], times[index], color=color, lw=0, zorder=0)
            start_index = index
            color = next_color
        self._axes.axvspan(times[start_index], times[-1], color=color, lw=0, zorder=0)

    def _mark_twilight(self, marks, y_max: float) -> None:
        for mark in marks:
            x = _mpl_local_time(mark.local)
            if mark.name == "Astronomical Dark":
                self._axes.text(
                    x,
                    y_max * 0.92,
                    mark.name,
                    color="#dce6f8",
                    fontsize=8,
                    ha="center",
                    va="top",
                    zorder=4,
                )
                continue
            self._axes.axvline(x, color="#c5d2ea", linewidth=0.8, linestyle="--", zorder=2)
            self._axes.text(
                x,
                y_max * 0.98,
                mark.name,
                color="#e8eef8",
                fontsize=8,
                rotation=90,
                ha="right",
                va="top",
                zorder=4,
            )

    def _mark_eclipse_windows(self, schedule: TonightSchedule, events: list[VariableEphemerisEvent]) -> None:
        xlim = self._axes.get_xlim()
        ylim = self._axes.get_ylim()
        red, green, blue = to_rgb(_STAR_CURVE_COLOR)
        for event in events:
            if event.window_start_local is None or event.window_end_local is None:
                continue
            start = max(event.window_start_local, schedule.start_local)
            end = min(event.window_end_local, schedule.end_local)
            if end <= start:
                continue
            x0 = _mpl_local_time(start)
            x1 = _mpl_local_time(end)
            if x1 <= x0:
                continue
            minutes = max(1.0, (end - start).total_seconds() / 60.0)
            samples = int(min(_ECLIPSE_WINDOW_MAX_SAMPLES, max(_ECLIPSE_WINDOW_MIN_SAMPLES, minutes)))
            full_start = _mpl_local_time(event.window_start_local)
            full_end = _mpl_local_time(event.window_end_local)
            mid = _mpl_local_time(event.local)
            step = (x1 - x0) / samples
            alphas = [
                _eclipse_window_fade_weight(x0 + step * (index + 0.5), full_start, mid, full_end)
                * _ECLIPSE_WINDOW_PEAK_ALPHA
                for index in range(samples)
            ]
            gradient = np.zeros((2, samples, 4))
            gradient[..., 0] = red
            gradient[..., 1] = green
            gradient[..., 2] = blue
            gradient[..., 3] = alphas
            self._axes.imshow(
                gradient,
                extent=(x0, x1, 0.0, _SCHEDULE_ALTITUDE_MAX_DEG),
                aspect="auto",
                interpolation="bilinear",
                interpolation_stage="rgba",
                origin="lower",
                zorder=1,
                clip_on=True,
            )
        self._axes.set_xlim(xlim)
        self._axes.set_ylim(ylim)

    def _mark_events(self, schedule: TonightSchedule, events: list[VariableEphemerisEvent], y_max: float) -> None:
        visible: list[tuple[float, float, VariableEphemerisEvent]] = []
        for event in events:
            if event.local < schedule.start_local or event.local > schedule.end_local:
                continue
            altitude = _event_altitude_on_curve(schedule, event)
            if altitude is None or altitude < 0:
                continue
            visible.append((_mpl_local_time(event.local), altitude, event))
        for index, (x_value, altitude, event) in enumerate(visible):
            self._axes.scatter(
                [x_value],
                [altitude],
                s=190,
                facecolors="none",
                edgecolors=_STAR_CURVE_COLOR,
                linewidths=2.4,
                zorder=5.4,
                label="_nolegend_",
            )
            self._axes.scatter(
                [x_value],
                [altitude],
                s=78,
                marker="D",
                color=_STAR_CURVE_COLOR,
                edgecolors="#0b1220",
                linewidths=1.1,
                zorder=5.6,
                label="_nolegend_",
            )
            place_above = altitude < (y_max * 0.78)
            side = 1 if index % 2 == 0 else -1
            self._axes.annotate(
                f"{event.kind}\n{event.local.strftime('%H:%M')}",
                xy=(x_value, altitude),
                xytext=(10 * side, 12 if place_above else -12),
                textcoords="offset points",
                ha="left" if side > 0 else "right",
                va="bottom" if place_above else "top",
                color=_STAR_CURVE_COLOR,
                fontsize=8,
                fontweight="bold",
                arrowprops={
                    "arrowstyle": "-",
                    "color": _STAR_CURVE_COLOR,
                    "lw": 0.9,
                },
                bbox={
                    "boxstyle": "round,pad=0.28",
                    "facecolor": "#121a2c",
                    "edgecolor": _STAR_CURVE_COLOR,
                    "alpha": 0.94,
                },
                zorder=6,
            )

    def _prepare_hover(
        self,
        times: list[float],
        moon_alts: list[float | None],
        star_alts: list[float | None],
        star_name: str | None,
    ) -> None:
        self._hover_times = list(times)
        self._hover_moon = list(moon_alts)
        self._hover_star = list(star_alts)
        self._hover_star_name = star_name if star_name and any(alt is not None for alt in star_alts) else None
        self._hover_vline = self._axes.axvline(
            times[0],
            color="#e8eef8",
            linewidth=0.9,
            linestyle="-",
            zorder=7,
            label="_nolegend_",
        )
        (self._hover_moon_marker,) = self._axes.plot(
            [],
            [],
            "o",
            color=_MOON_CURVE_COLOR,
            markersize=7,
            markeredgecolor="#0b1220",
            markeredgewidth=0.8,
            zorder=8,
            label="_nolegend_",
        )
        (self._hover_star_marker,) = self._axes.plot(
            [],
            [],
            "o",
            color=_STAR_CURVE_COLOR,
            markersize=7,
            markeredgecolor="#0b1220",
            markeredgewidth=0.8,
            zorder=8,
            label="_nolegend_",
        )
        self._hover_annot = self._axes.annotate(
            "",
            xy=(times[0], 0.0),
            xytext=(10, 8),
            textcoords="offset points",
            color=_SCHEDULE_AXIS,
            fontsize=8,
            ha="left",
            va="bottom",
            bbox={
                "boxstyle": "round,pad=0.35",
                "facecolor": "#121a2c",
                "edgecolor": "#3d4d6c",
                "alpha": 0.94,
            },
            zorder=9,
        )
        self._hide_hover()

    def _handle_hover_move(self, event) -> None:
        if event.inaxes is not self._axes or event.xdata is None or len(self._hover_times) < 2:
            if self._hide_hover():
                self._canvas.draw_idle()
            return
        self._update_hover(float(event.xdata))

    def _handle_hover_leave(self, _event) -> None:
        if self._hide_hover():
            self._canvas.draw_idle()

    def _update_hover(self, x_value: float) -> None:
        times = self._hover_times
        if len(times) < 2 or x_value < times[0] or x_value > times[-1]:
            self._hide_hover()
            self._canvas.draw_idle()
            return
        moon_alt = _interpolate_altitude(times, self._hover_moon, x_value)
        star_alt = None
        if self._hover_star_name is not None:
            star_alt = _interpolate_altitude(times, self._hover_star, x_value)
        y_min, y_max = self._axes.get_ylim()
        x_min, x_max = self._axes.get_xlim()
        if self._hover_vline is not None:
            self._hover_vline.set_xdata([x_value, x_value])
            self._hover_vline.set_visible(True)
        self._set_hover_marker(self._hover_moon_marker, x_value, moon_alt, y_min, y_max)
        self._set_hover_marker(self._hover_star_marker, x_value, star_alt, y_min, y_max)
        lines = [_format_hover_time(x_value)]
        if moon_alt is not None:
            lines.append(f"Moon  {_format_hover_altitude(moon_alt)}")
        if self._hover_star_name and star_alt is not None:
            lines.append(f"{self._hover_star_name}  {_format_hover_altitude(star_alt)}")
        visible_alts = [alt for alt in (moon_alt, star_alt) if alt is not None]
        annot_y = y_max * 0.08
        if visible_alts:
            annot_y = min(y_max, max(y_min, max(visible_alts)))
        if self._hover_annot is not None:
            on_left = x_value <= (x_min + x_max) * 0.55
            self._hover_annot.xy = (x_value, annot_y)
            self._hover_annot.set_text("\n".join(lines))
            self._hover_annot.set_horizontalalignment("left" if on_left else "right")
            self._hover_annot.set_position((10 if on_left else -10, 8))
            self._hover_annot.set_visible(True)
        self._canvas.draw_idle()

    def _set_hover_marker(self, marker, x_value: float, altitude: float | None, y_min: float, y_max: float) -> None:
        if marker is None:
            return
        if altitude is None:
            marker.set_data([], [])
            marker.set_visible(False)
            return
        marker.set_data([x_value], [min(y_max, max(y_min, altitude))])
        marker.set_visible(True)

    def _hide_hover(self) -> bool:
        changed = False
        if self._hover_vline is not None and self._hover_vline.get_visible():
            self._hover_vline.set_visible(False)
            changed = True
        for marker in (self._hover_moon_marker, self._hover_star_marker):
            if marker is not None and marker.get_visible():
                marker.set_data([], [])
                marker.set_visible(False)
                changed = True
        if self._hover_annot is not None and self._hover_annot.get_visible():
            self._hover_annot.set_visible(False)
            changed = True
        return changed

    def _reset_hover(self) -> None:
        self._hover_times = []
        self._hover_moon = []
        self._hover_star = []
        self._hover_star_name = None
        self._hide_hover()

    def _style_axes(self) -> None:
        self._axes.set_facecolor(_SCHEDULE_FACE)
        for spine in self._axes.spines.values():
            spine.set_color(_SCHEDULE_AXIS)
        self._axes.tick_params(colors=_SCHEDULE_AXIS, which="both")
        self._axes.yaxis.label.set_color(_SCHEDULE_AXIS)
        self._axes.xaxis.label.set_color(_SCHEDULE_AXIS)
        self._axes.grid(False)


def _twilight_band_color(sun_altitude_deg: float) -> str:
    for limit, color in _TWILIGHT_BAND_COLORS:
        if sun_altitude_deg <= limit:
            return color
    return _TWILIGHT_BAND_COLORS[-1][1]


def _event_altitude_on_curve(schedule: TonightSchedule, event: VariableEphemerisEvent) -> float | None:
    if event.altitude_deg is not None:
        return event.altitude_deg
    times = [_mpl_local_time(sample.local) for sample in schedule.samples]
    altitudes = [sample.star_altitude_deg for sample in schedule.samples]
    return _interpolate_altitude(times, altitudes, _mpl_local_time(event.local))


def _mpl_local_time(value: datetime) -> float:
    return mdates.date2num(value.replace(tzinfo=None))


def _eclipse_window_fade_weight(x_value: float, start: float, mid: float, end: float) -> float:
    if end <= start or x_value <= start or x_value >= end:
        return 0.0
    if x_value <= mid:
        span = mid - start
        if span <= 0:
            return 1.0
        progress = (x_value - start) / span
    else:
        span = end - mid
        if span <= 0:
            return 1.0
        progress = (end - x_value) / span
    progress = max(0.0, min(1.0, progress))
    return 0.5 - 0.5 * math.cos(math.pi * progress)


def _format_hour_tick(value: float, _pos: int | None = None) -> str:
    moment = mdates.num2date(value)
    hour = moment.hour
    suffix = "am" if hour < 12 else "pm"
    display = hour % 12
    if display == 0:
        display = 12
    return f"{display} {suffix}"


def _format_hover_time(value: float) -> str:
    return mdates.num2date(value).strftime("%a %d %b  %H:%M")


def _format_hover_altitude(altitude_deg: float) -> str:
    return f"{altitude_deg:.0f}°"


def _interpolate_altitude(times: list[float], altitudes: list[float | None], x_value: float) -> float | None:
    if len(times) < 2 or len(times) != len(altitudes):
        return None
    if x_value < times[0] or x_value > times[-1]:
        return None
    for index in range(len(times) - 1):
        left = times[index]
        right = times[index + 1]
        if x_value > right:
            continue
        start = altitudes[index]
        end = altitudes[index + 1]
        if start is None or end is None:
            return start if end is None else end
        span = right - left
        if span <= 0:
            return float(start)
        fraction = (x_value - left) / span
        return float(start) + fraction * (float(end) - float(start))
    return altitudes[-1]
