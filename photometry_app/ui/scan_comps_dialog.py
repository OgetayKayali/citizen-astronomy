from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from photometry_app.core.models import LightCurveSeries, PhotometryMeasurement
from photometry_app.core.scan_comps import (
    DEFAULT_COMPS_PER_SET,
    DEFAULT_MAX_DELTA_COLOR,
    DEFAULT_MAX_DELTA_MAG,
    DEFAULT_MAX_SEPARATION_ARCMIN,
    DEFAULT_POOL_SIZE,
    MAX_COMBINATIONS_SOFT_CAP,
    ScanCompCandidate,
    ScanCompReferenceInput,
    build_scan_comp_candidates,
    combination_count,
    iter_unordered_comp_sets,
)
from photometry_app.ui.light_curve_widget import LightCurvePlotWidget
from photometry_app.ui.workers import ScanCompSetEvaluation, ScanCompsWorker


@dataclass(slots=True)
class ScanCompsDialogResult:
    comparison_source_ids: tuple[str, ...]
    comparison_source_names: tuple[str, ...]
    target_measurements: list[PhotometryMeasurement]
    series: LightCurveSeries


@dataclass
class _ScanCompsSessionDefaults:
    max_delta_mag: float = DEFAULT_MAX_DELTA_MAG
    max_delta_color: float = DEFAULT_MAX_DELTA_COLOR
    max_separation_arcmin: float = DEFAULT_MAX_SEPARATION_ARCMIN
    pool_size: int = DEFAULT_POOL_SIZE
    comps_per_set: int = DEFAULT_COMPS_PER_SET


_SESSION_DEFAULTS = _ScanCompsSessionDefaults()


class ScanCompsDialog(QDialog):
    def __init__(
        self,
        *,
        report_token: int,
        target_source_id: str,
        target_source_name: str,
        filter_name: str,
        target_ra_deg: float,
        target_dec_deg: float,
        target_magnitude: float | None,
        target_bp_rp: float | None,
        target_measurements: list[PhotometryMeasurement],
        reference_measurements: list[PhotometryMeasurement],
        reference_inputs: list[ScanCompReferenceInput],
        y_axis_mode: str = "differential_magnitude",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Scan Comps — {target_source_name} [{filter_name}]")
        self.resize(980, 640)
        self._report_token = report_token
        self._target_source_id = target_source_id
        self._target_source_name = target_source_name
        self._filter_name = filter_name
        self._target_ra_deg = target_ra_deg
        self._target_dec_deg = target_dec_deg
        self._target_magnitude = target_magnitude
        self._target_bp_rp = target_bp_rp
        self._target_measurements = list(target_measurements)
        self._reference_measurements = list(reference_measurements)
        self._reference_inputs = list(reference_inputs)
        self._y_axis_mode = y_axis_mode
        self._candidates: list[ScanCompCandidate] = []
        self._color_used = False
        self._favourite_ids: set[str] = set()
        self._starred_set_keys: set[tuple[str, ...]] = set()
        self._evaluations: dict[tuple[str, ...], ScanCompSetEvaluation] = {}
        self._combo_order: list[tuple[str, ...]] = []
        self._current_combo_index = 0
        self._worker: ScanCompsWorker | None = None
        self._selected_result: ScanCompsDialogResult | None = None

        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._build_params_page())
        self._stack.addWidget(self._build_pool_page())
        self._stack.addWidget(self._build_browse_page())

        layout = QVBoxLayout(self)
        layout.addWidget(self._stack)
        self._status_label = QLabel("")
        layout.addWidget(self._status_label)

    def selected_result(self) -> ScanCompsDialogResult | None:
        return self._selected_result

    def closeEvent(self, event) -> None:  # noqa: N802
        self._stop_worker()
        super().closeEvent(event)

    def _build_params_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel(
            "Choose search limits, then build a ranked candidate pool from measured reference stars."
        ))

        self._max_delta_mag_input = QDoubleSpinBox()
        self._max_delta_mag_input.setRange(0.0, 10.0)
        self._max_delta_mag_input.setDecimals(2)
        self._max_delta_mag_input.setSingleStep(0.1)
        self._max_delta_mag_input.setSuffix(" mag")
        self._max_delta_mag_input.setValue(_SESSION_DEFAULTS.max_delta_mag)

        self._max_delta_color_input = QDoubleSpinBox()
        self._max_delta_color_input.setRange(0.0, 5.0)
        self._max_delta_color_input.setDecimals(2)
        self._max_delta_color_input.setSingleStep(0.05)
        self._max_delta_color_input.setSuffix(" BP−RP")
        self._max_delta_color_input.setValue(_SESSION_DEFAULTS.max_delta_color)

        self._max_sep_input = QDoubleSpinBox()
        self._max_sep_input.setRange(0.1, 180.0)
        self._max_sep_input.setDecimals(1)
        self._max_sep_input.setSingleStep(1.0)
        self._max_sep_input.setSuffix(" arcmin")
        self._max_sep_input.setValue(_SESSION_DEFAULTS.max_separation_arcmin)

        self._pool_size_input = QSpinBox()
        self._pool_size_input.setRange(1, 200)
        self._pool_size_input.setValue(_SESSION_DEFAULTS.pool_size)

        self._comps_per_set_input = QSpinBox()
        self._comps_per_set_input.setRange(1, 10)
        self._comps_per_set_input.setValue(_SESSION_DEFAULTS.comps_per_set)

        form = QVBoxLayout()
        for label, widget in (
            ("Max |Δmag| from target", self._max_delta_mag_input),
            ("Max |Δ(BP−RP)| from target", self._max_delta_color_input),
            ("Max separation", self._max_sep_input),
            ("Candidate pool size", self._pool_size_input),
            ("Comparisons per set (N)", self._comps_per_set_input),
        ):
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            row.addStretch(1)
            row.addWidget(widget)
            form.addLayout(row)
        layout.addLayout(form)

        color_note = (
            "Gaia BP−RP available for the target."
            if self._target_bp_rp is not None
            else "Target BP−RP missing — candidates will rank by magnitude and distance only."
        )
        self._params_note = QLabel(color_note)
        self._params_note.setWordWrap(True)
        layout.addWidget(self._params_note)
        layout.addStretch(1)

        buttons = QDialogButtonBox(self)
        build_button = buttons.addButton("Build Candidates", QDialogButtonBox.ButtonRole.AcceptRole)
        cancel_button = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        build_button.clicked.connect(self._build_candidates)
        cancel_button.clicked.connect(self.reject)
        layout.addWidget(buttons)
        return page

    def _build_pool_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        self._pool_note = QLabel("Favourite at least N candidates to form the combination pool.")
        self._pool_note.setWordWrap(True)
        layout.addWidget(self._pool_note)

        self._pool_table = QTableWidget(0, 6, page)
        self._pool_table.setHorizontalHeaderLabels(
            ["Favourite", "Name", "Mag", "BP−RP", "Sep (′)", "Δmag"]
        )
        self._pool_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._pool_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._pool_table.verticalHeader().setVisible(False)
        layout.addWidget(self._pool_table)

        buttons = QHBoxLayout()
        back_button = QPushButton("Back")
        back_button.clicked.connect(lambda: self._stack.setCurrentIndex(0))
        continue_button = QPushButton("Continue to Combinations")
        continue_button.clicked.connect(self._continue_to_combinations)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        buttons.addWidget(back_button)
        buttons.addStretch(1)
        buttons.addWidget(cancel_button)
        buttons.addWidget(continue_button)
        layout.addLayout(buttons)
        return page

    def _build_browse_page(self) -> QWidget:
        page = QWidget(self)
        layout = QHBoxLayout(page)

        left = QVBoxLayout()
        left.addWidget(QLabel("Combination sets"))
        self._combo_list = QListWidget(page)
        self._combo_list.currentRowChanged.connect(self._on_combo_row_changed)
        left.addWidget(self._combo_list)
        layout.addLayout(left, 2)

        right = QVBoxLayout()
        self._combo_title = QLabel("Select a combination")
        self._combo_title.setWordWrap(True)
        right.addWidget(self._combo_title)
        self._plot = LightCurvePlotWidget(page)
        self._plot.setMinimumHeight(280)
        right.addWidget(self._plot, 1)

        nav = QHBoxLayout()
        self._prev_button = QPushButton("Previous")
        self._next_button = QPushButton("Next")
        self._skip_button = QPushButton("Skip")
        self._star_button = QPushButton("Star Set")
        self._prev_button.clicked.connect(self._go_previous)
        self._next_button.clicked.connect(self._go_next)
        self._skip_button.clicked.connect(self._go_skip)
        self._star_button.clicked.connect(self._toggle_star_current)
        nav.addWidget(self._prev_button)
        nav.addWidget(self._next_button)
        nav.addWidget(self._skip_button)
        nav.addWidget(self._star_button)
        nav.addStretch(1)
        right.addLayout(nav)

        actions = QHBoxLayout()
        back_button = QPushButton("Back")
        back_button.clicked.connect(self._back_from_browse)
        use_button = QPushButton("Use Selected")
        use_button.clicked.connect(self._use_selected)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        actions.addWidget(back_button)
        actions.addStretch(1)
        actions.addWidget(cancel_button)
        actions.addWidget(use_button)
        right.addLayout(actions)
        layout.addLayout(right, 5)
        return page

    def _build_candidates(self) -> None:
        _SESSION_DEFAULTS.max_delta_mag = float(self._max_delta_mag_input.value())
        _SESSION_DEFAULTS.max_delta_color = float(self._max_delta_color_input.value())
        _SESSION_DEFAULTS.max_separation_arcmin = float(self._max_sep_input.value())
        _SESSION_DEFAULTS.pool_size = int(self._pool_size_input.value())
        _SESSION_DEFAULTS.comps_per_set = int(self._comps_per_set_input.value())

        self._candidates, self._color_used = build_scan_comp_candidates(
            target_ra_deg=self._target_ra_deg,
            target_dec_deg=self._target_dec_deg,
            target_magnitude=self._target_magnitude,
            target_bp_rp=self._target_bp_rp,
            references=self._reference_inputs,
            max_delta_mag=_SESSION_DEFAULTS.max_delta_mag,
            max_delta_color=_SESSION_DEFAULTS.max_delta_color,
            max_separation_arcmin=_SESSION_DEFAULTS.max_separation_arcmin,
            pool_size=_SESSION_DEFAULTS.pool_size,
        )
        self._favourite_ids = set()
        if not self._candidates:
            QMessageBox.warning(
                self,
                "Scan Comps",
                "No reference stars matched the current limits. Widen mag/color/distance or check the measured reference pool.",
            )
            return
        note = f"Built {len(self._candidates)} candidate(s). Favourite at least {_SESSION_DEFAULTS.comps_per_set}."
        if self._target_bp_rp is not None and not self._color_used:
            note += " Color was unavailable for ranked candidates; ranking used magnitude and distance only."
        elif self._color_used:
            note += " Color (Gaia BP−RP) contributed to ranking."
        self._pool_note.setText(note)
        self._populate_pool_table()
        self._stack.setCurrentIndex(1)

    def _populate_pool_table(self) -> None:
        self._pool_table.setRowCount(len(self._candidates))
        for row_index, candidate in enumerate(self._candidates):
            checkbox = QCheckBox()
            checkbox.setChecked(candidate.source_id in self._favourite_ids)
            # Use toggled(bool): stateChanged can emit Qt.CheckState in newer PySide6,
            # and int(CheckState) raises TypeError so favourites never stick.
            checkbox.toggled.connect(
                lambda checked, source_id=candidate.source_id: self._on_favourite_toggled(source_id, checked)
            )
            self._pool_table.setCellWidget(row_index, 0, checkbox)
            values = [
                candidate.source_name,
                "-" if candidate.magnitude is None else f"{candidate.magnitude:.2f}",
                "-" if candidate.bp_rp is None else f"{candidate.bp_rp:.2f}",
                f"{candidate.separation_arcmin:.2f}",
                "-" if candidate.delta_mag is None else f"{candidate.delta_mag:.2f}",
            ]
            for column_index, value in enumerate(values, start=1):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, candidate.source_id)
                self._pool_table.setItem(row_index, column_index, item)
        self._pool_table.resizeColumnsToContents()

    def _on_favourite_toggled(self, source_id: str, checked: bool) -> None:
        if checked:
            self._favourite_ids.add(source_id)
        else:
            self._favourite_ids.discard(source_id)

    def _continue_to_combinations(self) -> None:
        n = int(_SESSION_DEFAULTS.comps_per_set)
        favourites = sorted(self._favourite_ids)
        if len(favourites) < n:
            QMessageBox.warning(
                self,
                "Scan Comps",
                f"Favourite at least {n} candidate(s) before continuing (currently {len(favourites)}).",
            )
            return
        total = combination_count(len(favourites), n)
        if total > MAX_COMBINATIONS_SOFT_CAP:
            answer = QMessageBox.question(
                self,
                "Scan Comps",
                (
                    f"This will generate {total} unordered combinations "
                    f"(soft warning above {MAX_COMBINATIONS_SOFT_CAP}). "
                    "Reduce favourites or N for a smaller set, or continue anyway?"
                ),
                QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._combo_order = iter_unordered_comp_sets(favourites, n)
        self._evaluations = {}
        self._starred_set_keys = set()
        self._current_combo_index = 0
        self._populate_combo_list()
        self._stack.setCurrentIndex(2)
        self._start_worker()
        self._show_current_combo()

    def _populate_combo_list(self) -> None:
        self._combo_list.blockSignals(True)
        self._combo_list.clear()
        name_lookup = {item.source_id: item.source_name for item in self._candidates}
        for group in self._combo_order:
            names = ", ".join(name_lookup.get(source_id, source_id) for source_id in group)
            ready = group in self._evaluations
            star = "★ " if group in self._starred_set_keys else ""
            status = "ready" if ready else "…"
            item = QListWidgetItem(f"{star}{names}  ({status})")
            item.setData(Qt.ItemDataRole.UserRole, group)
            self._combo_list.addItem(item)
        if self._combo_order:
            self._combo_list.setCurrentRow(self._current_combo_index)
        self._combo_list.blockSignals(False)

    def _refresh_combo_list_labels(self) -> None:
        name_lookup = {item.source_id: item.source_name for item in self._candidates}
        for row_index in range(self._combo_list.count()):
            item = self._combo_list.item(row_index)
            group = item.data(Qt.ItemDataRole.UserRole)
            if not isinstance(group, tuple):
                continue
            names = ", ".join(name_lookup.get(source_id, source_id) for source_id in group)
            ready = group in self._evaluations
            star = "★ " if group in self._starred_set_keys else ""
            status = "ready" if ready else "…"
            item.setText(f"{star}{names}  ({status})")

    def _start_worker(self) -> None:
        self._stop_worker()
        if not self._combo_order:
            return
        self._worker = ScanCompsWorker(
            report_token=self._report_token,
            target_source_id=self._target_source_id,
            target_source_name=self._target_source_name,
            filter_name=self._filter_name,
            target_measurements=self._target_measurements,
            reference_measurements=self._reference_measurements,
            comparison_groups=list(self._combo_order),
            y_axis_mode=self._y_axis_mode,
            parent=self,
        )
        self._worker.set_ready.connect(self._on_set_ready)
        self._worker.progress_updated.connect(self._status_label.setText)
        self._worker.batch_completed.connect(self._on_batch_completed)
        self._worker.batch_failed.connect(self._on_batch_failed)
        self._worker.start()
        self._status_label.setText(f"Building light curves for {len(self._combo_order)} combination(s)…")

    def _stop_worker(self) -> None:
        worker = self._worker
        if worker is None:
            return
        if worker.isRunning():
            worker.request_cancel()
            worker.wait(5000)
        self._worker = None

    def _on_set_ready(self, evaluation: object) -> None:
        if not isinstance(evaluation, ScanCompSetEvaluation):
            return
        self._evaluations[evaluation.comparison_source_ids] = evaluation
        self._refresh_combo_list_labels()
        current = self._current_group()
        if current == evaluation.comparison_source_ids:
            self._show_current_combo()

    def _on_batch_completed(self, _result: object) -> None:
        self._worker = None
        self._refresh_combo_list_labels()
        self._status_label.setText(
            f"Ready: {len(self._evaluations)}/{len(self._combo_order)} combination light curve(s)."
        )
        self._show_current_combo()

    def _on_batch_failed(self, message: str) -> None:
        self._worker = None
        self._status_label.setText(f"Scan Comps failed: {message}")
        QMessageBox.critical(self, "Scan Comps", message)

    def _current_group(self) -> tuple[str, ...] | None:
        if not self._combo_order:
            return None
        index = min(max(0, self._current_combo_index), len(self._combo_order) - 1)
        return self._combo_order[index]

    def _on_combo_row_changed(self, row: int) -> None:
        if row < 0 or row >= len(self._combo_order):
            return
        self._current_combo_index = row
        self._show_current_combo()

    def _show_current_combo(self) -> None:
        group = self._current_group()
        if group is None:
            self._combo_title.setText("No combinations")
            self._plot.plot_series(
                LightCurveSeries(
                    object_name="",
                    source_id="",
                    source_name="",
                    filter_name=self._filter_name,
                    points=[],
                ),
                empty_message="No combinations to display.",
                y_axis_mode=self._y_axis_mode,
            )
            return
        name_lookup = {item.source_id: item.source_name for item in self._candidates}
        names = ", ".join(name_lookup.get(source_id, source_id) for source_id in group)
        evaluation = self._evaluations.get(group)
        starred = "★ " if group in self._starred_set_keys else ""
        if evaluation is None or evaluation.series is None:
            self._combo_title.setText(
                f"{starred}Set {self._current_combo_index + 1}/{len(self._combo_order)}: {names}\nBuilding light curve…"
            )
            self._plot.plot_series(
                LightCurveSeries(
                    object_name=self._target_source_name,
                    source_id=self._target_source_id,
                    source_name=self._target_source_name,
                    filter_name=self._filter_name,
                    points=[],
                ),
                empty_message="Light curve not ready yet for this combination.",
                y_axis_mode=self._y_axis_mode,
            )
            return
        self._combo_title.setText(
            f"{starred}Set {self._current_combo_index + 1}/{len(self._combo_order)}: {names}\n"
            f"{evaluation.valid_point_count} usable point(s), {evaluation.excluded_point_count} excluded."
        )
        self._plot.plot_series(
            evaluation.series,
            empty_message="No differential points for this combination.",
            y_axis_mode=self._y_axis_mode,
        )
        self._star_button.setText("Unstar Set" if group in self._starred_set_keys else "Star Set")

    def _go_previous(self) -> None:
        if not self._combo_order:
            return
        self._current_combo_index = (self._current_combo_index - 1) % len(self._combo_order)
        self._combo_list.setCurrentRow(self._current_combo_index)

    def _go_next(self) -> None:
        if not self._combo_order:
            return
        self._current_combo_index = (self._current_combo_index + 1) % len(self._combo_order)
        self._combo_list.setCurrentRow(self._current_combo_index)

    def _go_skip(self) -> None:
        self._go_next()

    def _toggle_star_current(self) -> None:
        group = self._current_group()
        if group is None:
            return
        if group in self._starred_set_keys:
            self._starred_set_keys.discard(group)
        else:
            self._starred_set_keys.add(group)
        self._refresh_combo_list_labels()
        self._show_current_combo()

    def _back_from_browse(self) -> None:
        self._stop_worker()
        self._stack.setCurrentIndex(1)

    def _use_selected(self) -> None:
        group = self._current_group()
        if group is None:
            QMessageBox.warning(self, "Scan Comps", "Select a combination first.")
            return
        evaluation = self._evaluations.get(group)
        if evaluation is None or evaluation.series is None:
            QMessageBox.warning(
                self,
                "Scan Comps",
                "That combination’s light curve is not ready yet. Wait for evaluation or pick another set.",
            )
            return
        self._selected_result = ScanCompsDialogResult(
            comparison_source_ids=evaluation.comparison_source_ids,
            comparison_source_names=evaluation.comparison_source_names,
            target_measurements=list(evaluation.target_measurements),
            series=evaluation.series,
        )
        self._stop_worker()
        self.accept()
