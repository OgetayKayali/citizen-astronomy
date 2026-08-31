from __future__ import annotations

from pathlib import Path
from threading import Event
from typing import cast

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from photometry_app.core.aavso_aid import (
    AID_BAND_CHOICES,
    AID_MTYPE_CHOICES,
    AID_OBSTYPE_CHOICES,
    DEFAULT_MAX_AID_OBSERVATIONS,
    MAX_AID_OBSERVATIONS,
    AidFilterRejectedAllError,
    AidQuery,
    download_aid_photometry,
    format_aid_query_notes,
)

_AID_DIALOG_SESSION: dict[str, object] | None = None


def remember_aid_dialog_session(state: dict[str, object] | None) -> None:
    global _AID_DIALOG_SESSION
    _AID_DIALOG_SESSION = None if state is None else dict(state)


def aid_dialog_session() -> dict[str, object] | None:
    return None if _AID_DIALOG_SESSION is None else dict(_AID_DIALOG_SESSION)
from photometry_app.core.oc_extrema import (
    EXTREMUM_MAXIMUM,
    EXTREMUM_MINIMUM,
    OcSession,
    OcStarLog,
    PhotometryImport,
    apply_star_name,
    compute_oc_residuals,
    export_oc_log_csv,
    import_photometry_table,
    mark_extremum_near_jd,
    mark_series_extrema,
    observation_jd,
    remove_records,
    upsert_records,
)
from photometry_app.core.plotting import LightCurveFitConfig, resolve_light_curve_theme_colors
from photometry_app.ui.light_curve_widget import LightCurvePlotWidget


class OcDialog(QDialog):
    log_changed = Signal(object)
    work_log_message = Signal(str)

    def __init__(
        self,
        *,
        log: OcStarLog,
        sessions: list[OcSession],
        y_axis_mode: str = "standard_magnitude",
        spline_smoothing: float = 0.35,
        theme: str = "normal",
        custom_theme_colors: dict[str, str] | None = None,
        aavso_api_token: str = "",
        observer_code: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ocDialog")
        self.setWindowTitle(f"O–C — {log.star_name or log.star_key}")
        self.setMinimumSize(1180, 720)
        self.resize(1380, 860)
        self._log = OcStarLog(
            star_key=log.star_key,
            star_name=log.star_name,
            source_id=log.source_id,
            t0_hjd=log.t0_hjd,
            period_days=log.period_days,
            oc_kind=log.oc_kind or EXTREMUM_MAXIMUM,
            records=list(log.records),
        )
        self._sessions = list(sessions)
        self._y_axis_mode = y_axis_mode
        self._spline_smoothing = float(spline_smoothing)
        self._imported_sessions: list[OcSession] = []
        self._aavso_api_token = str(aavso_api_token or "").strip()
        self._observer_code = str(observer_code or "").strip()
        self._aid_worker: AidDownloadWorker | None = None
        self._theme = theme
        self._custom_theme_colors = custom_theme_colors

        self._star_name_edit = QLineEdit(self)
        self._star_name_edit.setText(log.star_name or log.star_key)
        self._star_name_edit.setPlaceholderText("AAVSO / VSX name")
        self._star_name_edit.setClearButtonEnabled(True)
        self._star_name_edit.setToolTip(
            "Filled automatically from a catalog scan when the star is identified. "
            "Edit this for manual photometry so AID download and the log use the AAVSO name."
        )
        self._star_name_edit.editingFinished.connect(self._handle_star_name_changed)

        self._t0_spin = QDoubleSpinBox(self)
        self._t0_spin.setDecimals(6)
        self._t0_spin.setRange(0.0, 4000000.0)
        self._t0_spin.setSingleStep(0.0001)
        self._t0_spin.setSpecialValueText("Not set")
        if log.t0_hjd:
            self._t0_spin.setValue(float(log.t0_hjd))
        self._t0_spin.valueChanged.connect(self._handle_ephemeris_changed)

        self._period_spin = QDoubleSpinBox(self)
        self._period_spin.setDecimals(8)
        self._period_spin.setRange(0.0, 10000.0)
        self._period_spin.setSingleStep(0.000001)
        self._period_spin.setSpecialValueText("Not set")
        if log.period_days:
            self._period_spin.setValue(float(log.period_days))
        self._period_spin.valueChanged.connect(self._handle_ephemeris_changed)

        self._kind_selector = QComboBox(self)
        self._kind_selector.addItem("Maximum", EXTREMUM_MAXIMUM)
        self._kind_selector.addItem("Minimum", EXTREMUM_MINIMUM)
        kind_index = self._kind_selector.findData(self._log.oc_kind)
        if kind_index >= 0:
            self._kind_selector.setCurrentIndex(kind_index)
        self._kind_selector.currentIndexChanged.connect(self._handle_ephemeris_changed)

        header = QFrame(self)
        header.setObjectName("ocHeader")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(14, 12, 14, 12)
        header_layout.setSpacing(8)
        title = QLabel(log.star_name or log.star_key, self)
        title.setObjectName("ocTitleLabel")
        title_font = QFont(title.font())
        title_font.setPointSize(max(11, title_font.pointSize() + 2))
        title_font.setBold(True)
        title.setFont(title_font)
        self._title_label = title
        header_layout.addWidget(title)
        ephemeris = QHBoxLayout()
        ephemeris.setSpacing(12)
        for label_text, widget in (
            ("Star", self._star_name_edit),
            ("T₀ (HJD)", self._t0_spin),
            ("Period (d)", self._period_spin),
            ("Uses", self._kind_selector),
        ):
            field = QWidget(self)
            field_layout = QVBoxLayout(field)
            field_layout.setContentsMargins(0, 0, 0, 0)
            field_layout.setSpacing(2)
            caption = QLabel(label_text, self)
            caption.setObjectName("ocFieldLabel")
            field_layout.addWidget(caption)
            field_layout.addWidget(widget)
            ephemeris.addWidget(field, 1 if widget is self._star_name_edit else 0)
        header_layout.addLayout(ephemeris)

        self._session_list = QListWidget(self)
        self._session_list.setObjectName("ocSessionList")
        self._session_list.currentRowChanged.connect(self._handle_session_changed)
        self._import_button = QPushButton("Import…", self)
        self._import_button.setObjectName("ocSecondaryButton")
        self._import_button.clicked.connect(self._handle_import_clicked)
        self._pull_aavso_button = QPushButton("Pull AAVSO…", self)
        self._pull_aavso_button.setObjectName("ocSecondaryButton")
        self._pull_aavso_button.setToolTip(
            "Download historic AID photometry for this star. "
            "An AAVSO API token in Settings is optional; without one CAst uses VSX."
        )
        self._pull_aavso_button.clicked.connect(self._handle_pull_aavso_clicked)

        session_panel = QFrame(self)
        session_panel.setObjectName("ocCard")
        session_layout = QVBoxLayout(session_panel)
        session_layout.setContentsMargins(12, 10, 12, 10)
        session_title = QLabel("Sessions", self)
        session_title.setObjectName("ocCardTitle")
        session_layout.addWidget(session_title)
        session_layout.addWidget(self._session_list, 1)
        session_buttons = QHBoxLayout()
        session_buttons.addWidget(self._import_button)
        session_buttons.addWidget(self._pull_aavso_button)
        session_layout.addLayout(session_buttons)

        self._curve_widget = LightCurvePlotWidget(self)
        self._curve_widget.set_theme(theme, custom_theme_colors)
        self._curve_widget.set_plot_minimum_height(200)
        self._mark_button = QPushButton("Mark Extrema", self)
        self._mark_button.setObjectName("ocPrimaryButton")
        self._mark_button.setToolTip(
            "Fit a spline and add every local maximum and minimum in this session. "
            "Click a peak or trough on the curve to add just that one."
        )
        self._mark_button.clicked.connect(self._handle_mark_extrema)
        self._curve_widget.pointSelected.connect(self._handle_curve_point_selected)

        curve_panel = QFrame(self)
        curve_panel.setObjectName("ocCard")
        curve_layout = QVBoxLayout(curve_panel)
        curve_layout.setContentsMargins(12, 10, 12, 10)
        curve_header = QHBoxLayout()
        curve_title = QLabel("Session light curve", self)
        curve_title.setObjectName("ocCardTitle")
        curve_header.addWidget(curve_title)
        curve_header.addStretch(1)
        curve_header.addWidget(self._mark_button)
        curve_layout.addLayout(curve_header)
        curve_layout.addWidget(self._curve_widget, 1)

        upper = QSplitter(Qt.Orientation.Horizontal, self)
        upper.addWidget(session_panel)
        upper.addWidget(curve_panel)
        upper.setStretchFactor(0, 1)
        upper.setStretchFactor(1, 4)
        upper.setSizes([260, 980])

        self._log_table = QTableWidget(0, 7, self)
        self._log_table.setObjectName("ocLogTable")
        self._log_table.setHorizontalHeaderLabels(["Session", "Kind", "JD", "±JD", "Mag", "Filter", "Origin"])
        self._log_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._log_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._log_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._log_table.setAlternatingRowColors(True)
        header_view = self._log_table.horizontalHeader()
        header_view.setMinimumSectionSize(72)
        header_view.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header_view.setStretchLastSection(True)
        self._log_table.verticalHeader().setVisible(False)
        self._remove_button = QPushButton("Remove", self)
        self._remove_button.setObjectName("ocSecondaryButton")
        self._remove_button.clicked.connect(self._handle_remove_selected)
        self._export_button = QPushButton("Export…", self)
        self._export_button.setObjectName("ocSecondaryButton")
        self._export_button.clicked.connect(self._handle_export_clicked)

        log_panel = QFrame(self)
        log_panel.setObjectName("ocCard")
        log_layout = QVBoxLayout(log_panel)
        log_layout.setContentsMargins(12, 10, 12, 10)
        log_header = QHBoxLayout()
        log_title = QLabel("Extrema log", self)
        log_title.setObjectName("ocCardTitle")
        log_header.addWidget(log_title)
        log_header.addStretch(1)
        log_header.addWidget(self._remove_button)
        log_header.addWidget(self._export_button)
        log_layout.addLayout(log_header)
        log_layout.addWidget(self._log_table, 1)

        self._oc_plot = _OcInteractivePlot(self)
        self._oc_plot.set_theme(theme, custom_theme_colors)
        oc_panel = QFrame(self)
        oc_panel.setObjectName("ocCard")
        oc_layout = QVBoxLayout(oc_panel)
        oc_layout.setContentsMargins(12, 10, 12, 10)
        oc_title = QLabel("O–C", self)
        oc_title.setObjectName("ocCardTitle")
        oc_layout.addWidget(oc_title)
        oc_layout.addWidget(self._oc_plot, 1)

        body = QSplitter(Qt.Orientation.Vertical, self)
        body.addWidget(upper)
        body.addWidget(log_panel)
        body.addWidget(oc_panel)
        body.setStretchFactor(0, 5)
        body.setStretchFactor(1, 3)
        body.setStretchFactor(2, 3)
        body.setSizes([360, 220, 220])

        self._status_label = QLabel("Select a session, mark extrema, or pull historic photometry.", self)
        self._status_label.setObjectName("ocStatusLabel")
        self._status_label.setWordWrap(True)
        close_button = QPushButton("Close", self)
        close_button.setObjectName("ocSecondaryButton")
        close_button.clicked.connect(self.reject)
        footer = QHBoxLayout()
        footer.addWidget(self._status_label, 1)
        footer.addWidget(close_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addWidget(header)
        layout.addWidget(body, 1)
        layout.addLayout(footer)

        self._apply_visual_style()
        self._populate_sessions()
        self._refresh_log_table()
        self._refresh_oc_plot()
        if self._session_list.count():
            self._session_list.setCurrentRow(0)

    def current_log(self) -> OcStarLog:
        self._handle_star_name_changed()
        return self._log

    def closeEvent(self, event) -> None:
        self._cancel_aid_download()
        super().closeEvent(event)

    def _all_sessions(self) -> list[OcSession]:
        return [*self._sessions, *self._imported_sessions]

    def _selected_session(self) -> OcSession | None:
        item = self._session_list.currentItem()
        if item is None:
            return None
        session = item.data(Qt.ItemDataRole.UserRole)
        return session if isinstance(session, OcSession) else None

    def _populate_sessions(self) -> None:
        self._session_list.clear()
        for session in self._all_sessions():
            item = QListWidgetItem(f"{session.session_name}  [{session.series.filter_name}]")
            item.setData(Qt.ItemDataRole.UserRole, session)
            self._session_list.addItem(item)

    def _handle_session_changed(self) -> None:
        session = self._selected_session()
        self._mark_button.setEnabled(session is not None and len(session.series.points) >= 4)
        if session is None:
            self._curve_widget.show_message("O–C", "Select a session to inspect its light curve.")
            self._curve_widget.set_extrema_markers([])
            return
        self._curve_widget.plot_series(
            session.series,
            "Selected session has no valid values for the selected light-curve axis.",
            fit_config=LightCurveFitConfig(mode="spline", spline_smoothing=self._spline_smoothing),
            y_axis_mode=self._y_axis_mode,
            x_axis_mode="jd",
        )
        self._refresh_session_extrema_markers()

    def _min_extremum_separation_days(self) -> float | None:
        period = float(self._period_spin.value()) if hasattr(self, "_period_spin") else 0.0
        if period > 0:
            return 0.35 * period
        if self._log.period_days is not None and float(self._log.period_days) > 0:
            return 0.35 * float(self._log.period_days)
        return None

    def _handle_mark_extrema(self) -> None:
        session = self._selected_session()
        if session is None:
            return
        try:
            records = mark_series_extrema(
                session.series,
                y_axis_mode=self._y_axis_mode,
                spline_smoothing=self._spline_smoothing,
                session_name=session.session_name,
                origin=session.origin,
                min_separation_days=self._min_extremum_separation_days(),
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Mark Extrema", str(exc))
            return
        self._log = upsert_records(self._log, records)
        maxima = sum(1 for record in records if record.kind == EXTREMUM_MAXIMUM)
        minima = sum(1 for record in records if record.kind == EXTREMUM_MINIMUM)
        self._status_label.setText(
            f"Marked {session.session_name}: {maxima} maximum(s) and {minima} minimum(s)."
        )
        self._refresh_log_table()
        self._refresh_oc_plot()
        self._refresh_session_extrema_markers()
        self.log_changed.emit(self._log)

    def _handle_curve_point_selected(self, key: object) -> None:
        session = self._selected_session()
        if session is None:
            return
        point = self._series_point_for_key(session.series, key)
        jd = observation_jd(None if point is None else point.observation_time)
        if jd is None:
            return
        try:
            record = mark_extremum_near_jd(
                session.series,
                jd,
                y_axis_mode=self._y_axis_mode,
                spline_smoothing=self._spline_smoothing,
                session_name=session.session_name,
                origin=session.origin,
                min_separation_days=self._min_extremum_separation_days(),
            )
        except ValueError as exc:
            self._status_label.setText(str(exc))
            return
        self._log = upsert_records(self._log, [record])
        self._status_label.setText(
            f"Added {record.kind} at JD {record.jd:.6f} from the selected point."
        )
        self._refresh_log_table()
        self._refresh_oc_plot()
        self._refresh_session_extrema_markers()
        self.log_changed.emit(self._log)

    def _series_point_for_key(self, series: LightCurveSeries, key: object) -> object | None:
        if not isinstance(key, tuple) or len(key) < 4:
            return None
        wanted_time = str(key[3])
        for point in series.points:
            stamp = point.observation_time.isoformat(sep=" ") if point.observation_time else "-"
            if stamp == wanted_time:
                return point
        return None

    def _refresh_session_extrema_markers(self) -> None:
        session = self._selected_session()
        if session is None or not hasattr(self._curve_widget, "set_extrema_markers"):
            return
        markers = [
            (record.jd, float(record.magnitude), record.kind)
            for record in self._log.records
            if record.session_name == session.session_name and record.magnitude is not None
        ]
        self._curve_widget.set_extrema_markers(markers)

    def _handle_remove_selected(self) -> None:
        rows = sorted({index.row() for index in self._log_table.selectedIndexes()}, reverse=False)
        record_ids = []
        for row in rows:
            item = self._log_table.item(row, 0)
            if item is None:
                continue
            record_id = item.data(Qt.ItemDataRole.UserRole)
            if record_id:
                record_ids.append(str(record_id))
        if not record_ids:
            return
        self._log = remove_records(self._log, record_ids)
        self._refresh_log_table()
        self._refresh_oc_plot()
        self._refresh_session_extrema_markers()
        self.log_changed.emit(self._log)

    def _handle_ephemeris_changed(self) -> None:
        t0 = float(self._t0_spin.value())
        period = float(self._period_spin.value())
        self._log.t0_hjd = t0 if t0 > 0 else None
        self._log.period_days = period if period > 0 else None
        self._log.oc_kind = str(self._kind_selector.currentData() or EXTREMUM_MAXIMUM)
        self._refresh_oc_plot()
        self.log_changed.emit(self._log)

    def _handle_import_clicked(self) -> None:
        selected, _filter = QFileDialog.getOpenFileName(
            self,
            "Import photometry or extrema",
            "",
            "Photometry files (*.csv *.txt *.dat);;All files (*)",
        )
        if not selected:
            return
        try:
            imported = import_photometry_table(
                Path(selected),
                star_name=self._log.star_name,
                source_id=self._log.source_id,
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Import failed", str(exc))
            return
        self._apply_import(imported)

    def _handle_star_name_changed(self) -> None:
        name = self._star_name_edit.text().strip()
        if not name or name == self._log.star_name:
            return
        self._log = apply_star_name(self._log, name)
        self.setWindowTitle(f"O–C — {self._log.star_name or self._log.star_key}")
        if hasattr(self, "_title_label"):
            self._title_label.setText(self._log.star_name or self._log.star_key)
        self.log_changed.emit(self._log)

    def _handle_pull_aavso_clicked(self) -> None:
        self._handle_star_name_changed()
        restore_star = False
        while True:
            dialog = AidDownloadDialog(
                star_name=self._log.star_name or self._star_name_edit.text().strip(),
                observer_code="",
                has_api_token=bool(self._aavso_api_token),
                session=aid_dialog_session(),
                include_star_from_session=restore_star,
                parent=self,
            )
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            remember_aid_dialog_session(dialog.session_state())
            query = dialog.current_query(source_id=self._log.source_id, api_token=self._aavso_api_token)
            if query.star_name != self._star_name_edit.text().strip():
                self._star_name_edit.setText(query.star_name)
                self._handle_star_name_changed()
            if query.start_jd is None and query.end_jd is None:
                answer = QMessageBox.question(
                    self,
                    "Pull AAVSO",
                    "No JD range is set. Downloading the full AID history can be large. Continue?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    restore_star = True
                    continue
            self._start_aid_download(query)
            return

    def _start_aid_download(self, query: AidQuery) -> None:
        self._cancel_aid_download()
        progress = QProgressDialog("Downloading AAVSO AID photometry…", "Cancel", 0, 0, self)
        progress.setWindowTitle("Pull AAVSO")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        worker = AidDownloadWorker(query, parent=self)
        self._aid_worker = worker
        self._aid_progress = progress
        self._pull_aavso_button.setEnabled(False)
        worker.progress_updated.connect(progress.setLabelText)
        worker.work_log_lines.connect(self._append_aid_filter_work_log_lines)
        worker.completed.connect(self._handle_aid_download_completed)
        worker.failed.connect(self._handle_aid_download_failed)
        self._append_aid_work_log_lines(["Starting AAVSO AID download."] + format_aid_query_notes(query))
        progress.canceled.connect(self._cancel_aid_download)
        worker.start()

    def _handle_aid_download_completed(self, result: object) -> None:
        self._finish_aid_download()
        notes = getattr(result, "notes", ())
        if notes:
            self._append_aid_work_log_lines(notes, skip_query=True)
        imported = getattr(result, "imported", None)
        if not isinstance(imported, PhotometryImport):
            return
        self._apply_import(imported)

    def _handle_aid_download_failed(self, message: str) -> None:
        self._finish_aid_download()
        if "cancelled" in str(message or "").casefold():
            self._status_label.setText("AAVSO AID download cancelled.")
            self.work_log_message.emit("AAVSO AID download cancelled.")
            return
        QMessageBox.warning(self, "Pull AAVSO", str(message))

    def _append_aid_filter_work_log_lines(self, lines: object) -> None:
        self._append_aid_work_log_lines(lines, skip_query=True)

    def _append_aid_work_log_lines(self, lines: object, *, skip_query: bool = False) -> None:
        if isinstance(lines, str):
            values = [lines]
        else:
            values = [str(item) for item in list(lines or ()) if str(item).strip()]
        for line in values:
            if skip_query and line.startswith("AID query "):
                continue
            self.work_log_message.emit(line)

    def _finish_aid_download(self) -> None:
        progress = getattr(self, "_aid_progress", None)
        if progress is not None:
            try:
                progress.canceled.disconnect(self._cancel_aid_download)
            except (RuntimeError, TypeError):
                pass
            progress.reset()
            progress.deleteLater()
            self._aid_progress = None
        worker = self._aid_worker
        self._aid_worker = None
        if worker is not None:
            for signal in (worker.progress_updated, worker.work_log_lines, worker.completed, worker.failed):
                try:
                    signal.disconnect()
                except (RuntimeError, TypeError):
                    pass
            worker.deleteLater()
        self._pull_aavso_button.setEnabled(True)

    def _cancel_aid_download(self) -> None:
        worker = self._aid_worker
        if worker is not None and worker.isRunning():
            worker.request_cancel()

    def _apply_import(self, imported: PhotometryImport) -> None:
        if imported.sessions:
            self._imported_sessions.extend(imported.sessions)
            self._populate_sessions()
            self._session_list.setCurrentRow(self._session_list.count() - 1)
        if imported.records:
            self._log = upsert_records(self._log, list(imported.records))
            self._refresh_log_table()
            self._refresh_oc_plot()
            self.log_changed.emit(self._log)
        if imported.notes:
            self._status_label.setText(" ".join(imported.notes))

    def _handle_export_clicked(self) -> None:
        if not self._log.records:
            QMessageBox.information(self, "Export log", "The extrema log is empty.")
            return
        selected, _filter = QFileDialog.getSaveFileName(
            self,
            "Export O–C log",
            f"{self._log.star_name or 'oc'}_oc_log.csv",
            "CSV files (*.csv)",
        )
        if not selected:
            return
        export_oc_log_csv(self._log, Path(selected))
        self._status_label.setText(f"Exported O–C log to {selected}.")

    def _refresh_log_table(self) -> None:
        self._log_table.setRowCount(len(self._log.records))
        for row, record in enumerate(self._log.records):
            values = [
                record.session_name,
                record.kind,
                f"{record.jd:.6f}",
                "" if record.jd_error is None else f"{record.jd_error:.6f}",
                "" if record.magnitude is None else f"{record.magnitude:.4f}",
                record.filter_name,
                record.origin,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, record.record_id)
                self._log_table.setItem(row, column, item)

    def _refresh_oc_plot(self) -> None:
        t0 = self._log.t0_hjd
        period = self._log.period_days
        if t0 is None or period is None or period <= 0:
            self._oc_plot.show_message("Set T₀ and period to plot O–C.")
            return
        residuals = compute_oc_residuals(self._log.records, t0_hjd=t0, period_days=period, kind=self._log.oc_kind)
        if not residuals:
            self._oc_plot.show_message(f"No {self._log.oc_kind} times in the log yet.")
            return
        self._oc_plot.plot_points(
            np.asarray([residual.epoch for residual in residuals], dtype=float),
            np.asarray([residual.oc_days for residual in residuals], dtype=float),
            np.asarray([residual.record.jd_error or 0.0 for residual in residuals], dtype=float),
            x_label="Epoch E",
            y_label="O–C (days)",
        )

    def _apply_visual_style(self) -> None:
        palette = self.palette()
        parent_window = self.parentWidget()
        colors: dict[str, str] = {}
        if parent_window is not None and hasattr(parent_window, "_resolved_theme_editor_colors"):
            colors = cast(dict[str, str], parent_window._resolved_theme_editor_colors())
        window_bg = colors.get("panel_bg", palette.window().color().name().lower())
        card_bg = QColor(window_bg).lighter(106).name().lower()
        header_bg = QColor(window_bg).lighter(110).name().lower()
        border_color = QColor(colors.get("gridline", window_bg)).lighter(118).name().lower()
        accent_color = QColor(colors.get("accent", "#3d8bfd"))
        accent = accent_color.name().lower()
        accent_soft = accent_color.lighter(130).name().lower()
        accent_deep = accent_color.darker(118).name().lower()
        body_text = colors.get("text", palette.windowText().color().name().lower())
        muted_text = colors.get("placeholder", QColor(body_text).lighter(130).name().lower())
        contrast = "#0f1720" if accent_color.lightness() > 160 else "#f7fbff"
        alt_row = QColor(card_bg).lighter(108).name().lower()
        self.setStyleSheet(
            "QDialog#ocDialog {"
            f"background-color: {window_bg}; color: {body_text};"
            "}"
            "QFrame#ocHeader {"
            f"background-color: {header_bg}; border: 1px solid {border_color}; border-radius: 12px;"
            "}"
            "QFrame#ocCard {"
            f"background-color: {card_bg}; border: 1px solid {border_color}; border-radius: 12px;"
            "}"
            f"QLabel#ocTitleLabel {{ color: {body_text}; }}"
            f"QLabel#ocFieldLabel {{ color: {muted_text}; font-size: 11px; font-weight: 600; }}"
            f"QLabel#ocCardTitle {{ color: {body_text}; font-weight: 700; font-size: 13px; }}"
            f"QLabel#ocStatusLabel {{ color: {accent_soft}; font-style: italic; }}"
            "QPushButton {"
            f"background-color: {card_bg}; color: {body_text}; border: 1px solid {border_color};"
            "border-radius: 8px; padding: 6px 14px; font-weight: 600;"
            "}"
            f"QPushButton:hover {{ border-color: {accent_soft}; background-color: {QColor(card_bg).lighter(112).name().lower()}; }}"
            f"QPushButton#ocPrimaryButton {{ background-color: {accent}; color: {contrast}; border-color: {accent_deep}; }}"
            f"QPushButton#ocPrimaryButton:hover {{ background-color: {accent_soft}; }}"
            "QLineEdit, QDoubleSpinBox, QComboBox {"
            f"background-color: {window_bg}; color: {body_text}; border: 1px solid {border_color};"
            "border-radius: 8px; padding: 4px 8px; min-height: 28px;"
            "}"
            f"QLineEdit:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border: 2px solid {accent_soft}; }}"
            "QTableWidget#ocLogTable {"
            f"background-color: {window_bg}; alternate-background-color: {alt_row}; color: {body_text};"
            f"gridline-color: {border_color}; border: 1px solid {border_color}; border-radius: 8px;"
            "}"
            "QHeaderView::section {"
            f"background-color: {card_bg}; color: {body_text}; border: none;"
            f"border-bottom: 1px solid {border_color}; padding: 6px 8px; font-weight: 600;"
            "}"
            "QListWidget#ocSessionList {"
            f"background-color: {window_bg}; color: {body_text}; border: 1px solid {border_color};"
            "border-radius: 8px; padding: 4px;"
            "}"
            f"QListWidget#ocSessionList::item {{ padding: 6px 8px; border-radius: 6px; }}"
            f"QListWidget#ocSessionList::item:selected {{ background-color: {accent}; color: {contrast}; }}"
        )


class _OcInteractivePlot(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme_colors = {
            "background_color": "#181a1f",
            "axis_color": "#d8dee9",
            "grid_alpha": "0.28",
            "point_brush": "#2f81f7",
            "empty_text_color": "#9aa5b1",
        }
        self._plot_widget = pg.PlotWidget(background=self._theme_colors["background_color"])
        self._plot_item = self._plot_widget.getPlotItem()
        self._plot_item.showGrid(x=True, y=True, alpha=0.28)
        self._plot_item.setMenuEnabled(False)
        self._plot_item.getViewBox().setMouseEnabled(x=True, y=True)
        self._plot_widget.setMinimumHeight(160)
        self._plot_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._plot_widget)

    def set_theme(self, theme: str, custom_colors: dict[str, str] | None = None) -> None:
        self._theme_colors = resolve_light_curve_theme_colors(theme, custom_colors)
        self._plot_widget.setBackground(self._theme_colors["background_color"])
        self._plot_item.showGrid(x=True, y=True, alpha=float(self._theme_colors.get("grid_alpha", 0.28)))
        axis_pen = pg.mkPen(self._theme_colors["axis_color"])
        self._plot_item.getAxis("bottom").setTextPen(axis_pen)
        self._plot_item.getAxis("bottom").setPen(axis_pen)
        self._plot_item.getAxis("left").setTextPen(axis_pen)
        self._plot_item.getAxis("left").setPen(axis_pen)
        self._plot_item.setTitle(color=self._theme_colors["axis_color"])

    def show_message(self, message: str) -> None:
        self._plot_item.clear()
        self._plot_item.setLabel("bottom", "")
        self._plot_item.setLabel("left", "")
        text = pg.TextItem(message, color=self._theme_colors.get("empty_text_color", "#9aa5b1"), anchor=(0.5, 0.5))
        self._plot_item.addItem(text)
        text.setPos(0.5, 0.5)
        self._plot_item.getViewBox().setRange(xRange=(0, 1), yRange=(0, 1), padding=0)

    def plot_points(
        self,
        x_values: np.ndarray,
        y_values: np.ndarray,
        y_errors: np.ndarray,
        *,
        x_label: str,
        y_label: str,
    ) -> None:
        self._plot_item.clear()
        self._plot_item.setLabel("bottom", x_label)
        self._plot_item.setLabel("left", y_label)
        zero = pg.InfiniteLine(pos=0.0, angle=0, pen=pg.mkPen(self._theme_colors["axis_color"], width=1.0, style=Qt.PenStyle.DashLine))
        self._plot_item.addItem(zero)
        color = self._theme_colors.get("point_brush", "#2f81f7")
        if y_errors.size and np.isfinite(y_errors).any():
            self._plot_item.addItem(
                pg.ErrorBarItem(
                    x=x_values,
                    y=y_values,
                    top=y_errors,
                    bottom=y_errors,
                    beam=0.0,
                    pen=pg.mkPen(color, width=1.0),
                )
            )
        scatter = pg.ScatterPlotItem(
            x=x_values,
            y=y_values,
            size=9,
            symbol="o",
            pen=pg.mkPen(color, width=1.0),
            brush=pg.mkBrush(color),
            hoverPen=pg.mkPen(self._theme_colors.get("hover_pen", color), width=1.6),
            hoverBrush=pg.mkBrush(self._theme_colors.get("hover_brush", color)),
        )
        self._plot_item.addItem(scatter)
        self._plot_item.enableAutoRange()


class AidDownloadDialog(QDialog):
    def __init__(
        self,
        *,
        star_name: str,
        observer_code: str = "",
        has_api_token: bool = False,
        session: dict[str, object] | None = None,
        include_star_from_session: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Pull AAVSO AID")
        self.setMinimumWidth(460)

        self._star_name_edit = QLineEdit(star_name, self)
        self._star_name_edit.setPlaceholderText("AAVSO name or AUID")
        self._star_name_edit.setClearButtonEnabled(True)

        self._start_jd_spin = QDoubleSpinBox(self)
        self._configure_jd_spin(self._start_jd_spin)
        self._end_jd_spin = QDoubleSpinBox(self)
        self._configure_jd_spin(self._end_jd_spin)

        self._band_selector = QComboBox(self)
        for choice in AID_BAND_CHOICES:
            self._band_selector.addItem(choice.label, choice.api_id)
        v_index = self._band_selector.findData("2")
        if v_index >= 0:
            self._band_selector.setCurrentIndex(v_index)

        self._obstype_selector = QComboBox(self)
        for choice in AID_OBSTYPE_CHOICES:
            self._obstype_selector.addItem(choice.label, choice.code)
        ccd_index = self._obstype_selector.findData("CCD")
        if ccd_index >= 0:
            self._obstype_selector.setCurrentIndex(ccd_index)

        self._mtype_selector = QComboBox(self)
        for value, label in AID_MTYPE_CHOICES:
            self._mtype_selector.addItem(label, value)

        self._observer_edit = QLineEdit(observer_code, self)
        self._observer_edit.setPlaceholderText("Optional — leave empty for all observers")
        self._observer_edit.setClearButtonEnabled(True)
        self._observer_edit.setToolTip(
            "Filters the download to this observer code only. "
            "Leave empty to keep every observer. The Settings observer code is for science export, not this filter."
        )

        self._campaign_edit = QLineEdit(self)
        self._campaign_edit.setPlaceholderText("Optional campaign name")
        self._campaign_edit.setClearButtonEnabled(True)

        self._exclude_fainterthan = QCheckBox("Exclude fainter-than / upper limits", self)
        self._exclude_fainterthan.setChecked(True)
        self._skip_discrepant = QCheckBox("Skip discrepant AID flags", self)
        self._skip_discrepant.setChecked(True)
        self._group_by_night = QCheckBox("Split into nightly sessions", self)
        self._group_by_night.setChecked(True)

        self._max_spin = QSpinBox(self)
        self._max_spin.setRange(100, MAX_AID_OBSERVATIONS)
        self._max_spin.setSingleStep(1000)
        self._max_spin.setValue(DEFAULT_MAX_AID_OBSERVATIONS)

        source_label = QLabel(
            "Using the official AAVSO API token from Settings."
            if has_api_token
            else "No AAVSO API token in Settings — download uses VSX (no login). "
            "Add a token in Settings → Science Export for the official AID API."
        )
        source_label.setWordWrap(True)

        form = QFormLayout()
        form.addRow("Star", self._star_name_edit)
        form.addRow("Start JD", self._start_jd_spin)
        form.addRow("End JD", self._end_jd_spin)
        form.addRow("Band", self._band_selector)
        form.addRow("Observation type", self._obstype_selector)
        form.addRow("Measurement type", self._mtype_selector)
        form.addRow("Observer", self._observer_edit)
        form.addRow("Campaign", self._campaign_edit)
        form.addRow("Maximum observations", self._max_spin)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Download")

        layout = QVBoxLayout(self)
        layout.addWidget(source_label)
        layout.addLayout(form)
        layout.addWidget(self._exclude_fainterthan)
        layout.addWidget(self._skip_discrepant)
        layout.addWidget(self._group_by_night)
        layout.addWidget(buttons)
        if session:
            self.apply_session(session, include_star=include_star_from_session)

    def current_query(self, *, source_id: str = "", api_token: str = "") -> AidQuery:
        star_name = self._star_name_edit.text().strip()
        if not star_name:
            raise ValueError("Enter the AAVSO / VSX star name.")
        return AidQuery(
            star_name=star_name,
            source_id=source_id,
            api_token=api_token,
            start_jd=self._jd_value(self._start_jd_spin),
            end_jd=self._jd_value(self._end_jd_spin),
            band=str(self._band_selector.currentData() or ""),
            obstype=str(self._obstype_selector.currentData() or ""),
            mtype=str(self._mtype_selector.currentData() or ""),
            observer=self._observer_edit.text().strip(),
            campaign=self._campaign_edit.text().strip(),
            exclude_fainterthan=self._exclude_fainterthan.isChecked(),
            skip_discrepant=self._skip_discrepant.isChecked(),
            group_by_night=self._group_by_night.isChecked(),
            max_observations=int(self._max_spin.value()),
        )

    def session_state(self) -> dict[str, object]:
        return {
            "star_name": self._star_name_edit.text().strip(),
            "start_jd": self._jd_value(self._start_jd_spin),
            "end_jd": self._jd_value(self._end_jd_spin),
            "band": str(self._band_selector.currentData() or ""),
            "obstype": str(self._obstype_selector.currentData() or ""),
            "mtype": str(self._mtype_selector.currentData() or ""),
            "observer": self._observer_edit.text().strip(),
            "campaign": self._campaign_edit.text().strip(),
            "exclude_fainterthan": self._exclude_fainterthan.isChecked(),
            "skip_discrepant": self._skip_discrepant.isChecked(),
            "group_by_night": self._group_by_night.isChecked(),
            "max_observations": int(self._max_spin.value()),
        }

    def apply_session(self, state: dict[str, object] | None, *, include_star: bool = False) -> None:
        if not state:
            return
        if include_star and state.get("star_name"):
            self._star_name_edit.setText(str(state.get("star_name") or ""))
        start_jd = state.get("start_jd")
        self._start_jd_spin.setValue(float(start_jd) if isinstance(start_jd, (int, float)) and float(start_jd) > 0 else 0.0)
        end_jd = state.get("end_jd")
        self._end_jd_spin.setValue(float(end_jd) if isinstance(end_jd, (int, float)) and float(end_jd) > 0 else 0.0)
        band_index = self._band_selector.findData(str(state.get("band") or ""))
        if band_index >= 0:
            self._band_selector.setCurrentIndex(band_index)
        obstype_index = self._obstype_selector.findData(str(state.get("obstype") or ""))
        if obstype_index >= 0:
            self._obstype_selector.setCurrentIndex(obstype_index)
        mtype_index = self._mtype_selector.findData(str(state.get("mtype") or ""))
        if mtype_index >= 0:
            self._mtype_selector.setCurrentIndex(mtype_index)
        self._observer_edit.setText(str(state.get("observer") or ""))
        self._campaign_edit.setText(str(state.get("campaign") or ""))
        self._exclude_fainterthan.setChecked(bool(state.get("exclude_fainterthan")))
        self._skip_discrepant.setChecked(bool(state.get("skip_discrepant")))
        self._group_by_night.setChecked(bool(state.get("group_by_night")))
        max_observations = state.get("max_observations")
        if isinstance(max_observations, (int, float)):
            self._max_spin.setValue(int(max_observations))

    def accept(self) -> None:
        if not self._star_name_edit.text().strip():
            QMessageBox.warning(self, "Pull AAVSO", "Enter the AAVSO / VSX star name.")
            return
        super().accept()

    @staticmethod
    def _configure_jd_spin(spin: QDoubleSpinBox) -> None:
        spin.setDecimals(5)
        spin.setRange(0.0, 4000000.0)
        spin.setSingleStep(1.0)
        spin.setSpecialValueText("Any")
        spin.setValue(0.0)

    @staticmethod
    def _jd_value(spin: QDoubleSpinBox) -> float | None:
        value = float(spin.value())
        return value if value > 0 else None


class AidDownloadWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)
    progress_updated = Signal(str)
    work_log_lines = Signal(object)

    def __init__(self, query: AidQuery, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._query = query
        self._cancel_event = Event()

    def request_cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        try:
            result = download_aid_photometry(
                self._query,
                progress_callback=self.progress_updated.emit,
                cancel_event=self._cancel_event,
            )
        except AidFilterRejectedAllError as exc:
            self.work_log_lines.emit(exc.notes)
            self.failed.emit(str(exc))
            return
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.completed.emit(result)
