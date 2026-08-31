from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from photometry_app.core.matching import build_overview_light_curve_layers
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
        default_comps_per_set: int | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("scanCompsDialog")
        self.setWindowTitle(f"Scan Comps — {target_source_name} [{filter_name}]")
        self.resize(1180, 760)
        self.setMinimumSize(960, 600)
        self._combinations_window_size = (1480, 860)
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
        if default_comps_per_set is not None:
            _SESSION_DEFAULTS.comps_per_set = max(1, min(10, int(default_comps_per_set)))
        self._candidates: list[ScanCompCandidate] = []
        self._color_used = False
        self._favourite_ids: set[str] = set()
        self._starred_set_keys: set[tuple[str, ...]] = set()
        self._evaluations: dict[tuple[str, ...], ScanCompSetEvaluation] = {}
        self._combo_order: list[tuple[str, ...]] = []
        self._combo_source_key: tuple[frozenset[str], int] | None = None
        self._current_combo_index = 0
        self._preview_mode = "filter"
        self._worker: ScanCompsWorker | None = None
        self._selected_result: ScanCompsDialogResult | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        self._header = self._build_header()
        root.addWidget(self._header)

        self._step_row = self._build_step_row()
        root.addWidget(self._step_row)

        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._build_params_page())
        self._stack.addWidget(self._build_pool_page())
        self._stack.addWidget(self._build_browse_page())
        self._stack.currentChanged.connect(self._on_stack_page_changed)
        root.addWidget(self._stack, 1)

        self._status_label = QLabel("")
        self._status_label.setObjectName("scanCompsStatusLabel")
        self._status_label.setWordWrap(True)
        root.addWidget(self._status_label)

        self._apply_theme_to_plot()
        self._apply_visual_style()
        self._sync_step_indicators(0)
        self._refresh_favourite_summary()

    def selected_result(self) -> ScanCompsDialogResult | None:
        return self._selected_result

    def closeEvent(self, event) -> None:  # noqa: N802
        self._stop_worker()
        super().closeEvent(event)

    def _build_header(self) -> QWidget:
        frame = QFrame(self)
        frame.setObjectName("scanCompsHeader")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(4)

        title = QLabel(f"{self._target_source_name}  ·  [{self._filter_name}]")
        title.setObjectName("scanCompsTitleLabel")
        title_font = QFont(title.font())
        title_font.setPointSize(max(11, title_font.pointSize() + 2))
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        mag_text = "—" if self._target_magnitude is None else f"{self._target_magnitude:.2f}"
        color_text = "—" if self._target_bp_rp is None else f"{self._target_bp_rp:.2f}"
        meta = QLabel(
            f"Catalog mag {mag_text}   ·   BP−RP {color_text}   ·   "
            f"{len(self._reference_inputs)} measured reference star(s)"
        )
        meta.setObjectName("scanCompsMetaLabel")
        meta.setWordWrap(True)
        layout.addWidget(meta)

        intro = QLabel(
            "Pick search limits, favourite magnitude-matched candidates, then browse combination light curves. "
            "Ranking matches automatic Generate (closest Gaia mag, then distance)."
        )
        intro.setObjectName("scanCompsIntroLabel")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        return frame

    def _build_step_row(self) -> QWidget:
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self._step_badges: list[QPushButton] = []
        for index, label in enumerate(("1. Limits", "2. Favourites", "3. Combinations")):
            badge = QPushButton(label)
            badge.setObjectName("scanCompsStepBadge")
            badge.setCursor(Qt.CursorShape.PointingHandCursor)
            badge.setProperty("stepState", "waiting")
            badge.setMinimumHeight(34)
            badge.clicked.connect(lambda _checked=False, page=index: self._request_step(page))
            self._step_badges.append(badge)
            layout.addWidget(badge, 1)
            if index < 2:
                spacer = QLabel("›")
                spacer.setObjectName("scanCompsStepArrow")
                spacer.setAlignment(Qt.AlignmentFlag.AlignCenter)
                layout.addWidget(spacer)
        return row

    def _on_stack_page_changed(self, page_index: int) -> None:
        self._sync_step_indicators(page_index)
        if page_index == 2:
            self._ensure_combinations_window_size()

    def _sync_step_indicators(self, page_index: int) -> None:
        for index, badge in enumerate(self._step_badges):
            if index < page_index:
                state = "done"
            elif index == page_index:
                state = "active"
            else:
                state = "waiting"
            badge.setProperty("stepState", state)
            badge.style().unpolish(badge)
            badge.style().polish(badge)

    def _request_step(self, page_index: int) -> None:
        current = self._stack.currentIndex()
        if page_index == current:
            return
        if page_index == 0:
            if current == 2:
                self._stop_worker()
            self._stack.setCurrentIndex(0)
            return
        if page_index == 1:
            if not self._candidates:
                QMessageBox.information(
                    self,
                    "Scan Comps",
                    "Build candidates on the Limits step before opening Favourites.",
                )
                return
            if current == 2:
                self._stop_worker()
            self._stack.setCurrentIndex(1)
            return
        if page_index == 2:
            self._continue_to_combinations()

    def _ensure_combinations_window_size(self) -> None:
        target_width, target_height = self._combinations_window_size
        current = self.size()
        width = max(current.width(), target_width)
        height = max(current.height(), target_height)
        if width != current.width() or height != current.height():
            self.resize(width, height)

    def _build_params_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        limits_group = QGroupBox("Search Limits")
        limits_layout = QVBoxLayout(limits_group)
        limits_layout.setContentsMargins(14, 18, 14, 14)
        limits_layout.setSpacing(8)

        self._max_delta_mag_input = QDoubleSpinBox()
        self._max_delta_mag_input.setRange(0.0, 10.0)
        self._max_delta_mag_input.setDecimals(2)
        self._max_delta_mag_input.setSingleStep(0.1)
        self._max_delta_mag_input.setSpecialValueText("No limit")
        self._max_delta_mag_input.setSuffix(" mag")
        self._max_delta_mag_input.setValue(_SESSION_DEFAULTS.max_delta_mag)
        self._max_delta_mag_input.setToolTip("Hard cut on |catalog mag − target mag|. 0 = no limit.")

        self._max_delta_color_input = QDoubleSpinBox()
        self._max_delta_color_input.setRange(0.0, 5.0)
        self._max_delta_color_input.setDecimals(2)
        self._max_delta_color_input.setSingleStep(0.05)
        self._max_delta_color_input.setSpecialValueText("No limit")
        self._max_delta_color_input.setSuffix(" BP−RP")
        self._max_delta_color_input.setValue(_SESSION_DEFAULTS.max_delta_color)
        self._max_delta_color_input.setToolTip("Optional Gaia BP−RP color cut. 0 = no limit.")

        self._max_sep_input = QDoubleSpinBox()
        self._max_sep_input.setRange(0.0, 180.0)
        self._max_sep_input.setDecimals(1)
        self._max_sep_input.setSingleStep(1.0)
        self._max_sep_input.setSpecialValueText("No limit")
        self._max_sep_input.setSuffix(" arcmin")
        self._max_sep_input.setValue(_SESSION_DEFAULTS.max_separation_arcmin)
        self._max_sep_input.setToolTip("Maximum sky separation from the target. 0 = no limit.")

        self._pool_size_input = QSpinBox()
        self._pool_size_input.setRange(1, 200)
        self._pool_size_input.setValue(_SESSION_DEFAULTS.pool_size)
        self._pool_size_input.setToolTip("How many ranked candidates to show in the favourites table.")

        self._comps_per_set_input = QSpinBox()
        self._comps_per_set_input.setRange(1, 10)
        self._comps_per_set_input.setValue(_SESSION_DEFAULTS.comps_per_set)
        self._comps_per_set_input.setToolTip("How many favourites go into each unordered comparison set.")

        control_rows = (
            ("Max |Δmag|", "Keep candidates near the target's brightness.", self._max_delta_mag_input),
            ("Max |Δ(BP−RP)|", "Optional color similarity (needs target BP−RP).", self._max_delta_color_input),
            ("Max separation", "Optional distance cut around the target.", self._max_sep_input),
            ("Candidate pool size", "Number of ranked stars listed next.", self._pool_size_input),
            ("Comparisons per set (N)", "Favourites needed per combination.", self._comps_per_set_input),
        )
        for index, (label, helper, widget) in enumerate(control_rows):
            limits_layout.addLayout(self._labeled_control_row(label, helper, widget))
            if index < len(control_rows) - 1:
                limits_layout.addStretch(1)

        color_note = (
            "Candidates rank by closest magnitude, then distance."
            if self._target_bp_rp is not None
            else "Target BP−RP is missing — color ranking stays off unless colors appear later."
        )
        self._params_note = QLabel(color_note)
        self._params_note.setObjectName("scanCompsTipLabel")
        self._params_note.setWordWrap(True)
        limits_layout.addWidget(self._params_note)
        layout.addWidget(limits_group, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        cancel_button = QPushButton("Cancel")
        cancel_button.setObjectName("scanCompsSecondaryButton")
        cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_button.clicked.connect(self.reject)
        build_button = QPushButton("Build Candidates")
        build_button.setObjectName("scanCompsPrimaryButton")
        build_button.setCursor(Qt.CursorShape.PointingHandCursor)
        build_button.setFixedHeight(38)
        build_button.clicked.connect(self._build_candidates)
        buttons.addStretch(1)
        buttons.addWidget(cancel_button)
        buttons.addWidget(build_button)
        layout.addLayout(buttons)
        return page

    def _labeled_control_row(self, title: str, helper: str, widget: QWidget) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(16)
        text_col = QVBoxLayout()
        text_col.setSpacing(4)
        title_label = QLabel(title)
        title_label.setObjectName("scanCompsFieldLabel")
        helper_label = QLabel(helper)
        helper_label.setObjectName("scanCompsFieldHelper")
        helper_label.setWordWrap(True)
        text_col.addWidget(title_label)
        text_col.addWidget(helper_label)
        row.addLayout(text_col, 1)
        widget.setMinimumWidth(160)
        widget.setMinimumHeight(34)
        row.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
        return row

    def _build_pool_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        pool_group = QGroupBox("Favourite Candidates")
        pool_layout = QVBoxLayout(pool_group)
        pool_layout.setSpacing(10)

        summary_row = QHBoxLayout()
        self._pool_note = QLabel("Favourite at least N candidates to form the combination pool.")
        self._pool_note.setObjectName("scanCompsTipLabel")
        self._pool_note.setWordWrap(True)
        summary_row.addWidget(self._pool_note, 1)
        self._favourite_count_label = QLabel("0 favourited")
        self._favourite_count_label.setObjectName("scanCompsCountBadge")
        self._favourite_count_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._favourite_count_label.setMinimumWidth(110)
        summary_row.addWidget(self._favourite_count_label)
        pool_layout.addLayout(summary_row)

        quick_row = QHBoxLayout()
        quick_row.setSpacing(8)
        select_all_button = QPushButton("Select All")
        select_all_button.setObjectName("scanCompsSecondaryButton")
        select_all_button.setCursor(Qt.CursorShape.PointingHandCursor)
        select_all_button.setToolTip("Favourite every candidate in the current pool.")
        select_all_button.clicked.connect(self._favourite_all)
        favourite_top_button = QPushButton("Favourite Best N")
        favourite_top_button.setObjectName("scanCompsSecondaryButton")
        favourite_top_button.setCursor(Qt.CursorShape.PointingHandCursor)
        favourite_top_button.setToolTip("Select the top N magnitude-matched candidates automatically.")
        favourite_top_button.clicked.connect(self._favourite_best_n)
        clear_fav_button = QPushButton("Clear Favourites")
        clear_fav_button.setObjectName("scanCompsSecondaryButton")
        clear_fav_button.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_fav_button.clicked.connect(self._clear_favourites)
        quick_row.addWidget(select_all_button)
        quick_row.addWidget(favourite_top_button)
        quick_row.addWidget(clear_fav_button)
        quick_row.addStretch(1)
        pool_layout.addLayout(quick_row)

        self._pool_table = QTableWidget(0, 6, page)
        self._pool_table.setObjectName("scanCompsTable")
        self._pool_table.setHorizontalHeaderLabels(
            ["Favourite", "Name", "Mag", "BP−RP", "Sep (′)", "Δmag"]
        )
        self._pool_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._pool_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._pool_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._pool_table.setAlternatingRowColors(True)
        self._pool_table.verticalHeader().setVisible(False)
        self._pool_table.horizontalHeader().setStretchLastSection(False)
        self._pool_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._pool_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in (2, 3, 4, 5):
            self._pool_table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self._pool_table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        pool_layout.addWidget(self._pool_table, 1)
        layout.addWidget(pool_group, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        cancel_button = QPushButton("Cancel")
        cancel_button.setObjectName("scanCompsSecondaryButton")
        cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_button.clicked.connect(self.reject)
        continue_button = QPushButton("Continue to Combinations")
        continue_button.setObjectName("scanCompsPrimaryButton")
        continue_button.setCursor(Qt.CursorShape.PointingHandCursor)
        continue_button.setFixedHeight(38)
        continue_button.clicked.connect(self._continue_to_combinations)
        buttons.addStretch(1)
        buttons.addWidget(cancel_button)
        buttons.addWidget(continue_button)
        layout.addLayout(buttons)
        return page

    def _build_browse_page(self) -> QWidget:
        page = QWidget(self)
        layout = QHBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        left_group = QGroupBox("Combination Sets")
        left = QVBoxLayout(left_group)
        left.setSpacing(8)
        tip = QLabel("Click ☆ to star promising sets. Starred sets stay easy to find while you browse.")
        tip.setObjectName("scanCompsTipLabel")
        tip.setWordWrap(True)
        left.addWidget(tip)
        self._combo_list = QListWidget(page)
        self._combo_list.setObjectName("scanCompsComboList")
        self._combo_list.setMinimumWidth(280)
        self._combo_list.currentRowChanged.connect(self._on_combo_row_changed)
        left.addWidget(self._combo_list, 1)
        layout.addWidget(left_group, 3)

        right_group = QGroupBox("Light Curve Preview")
        right = QVBoxLayout(right_group)
        right.setSpacing(8)
        preview_header = QHBoxLayout()
        preview_header.setSpacing(8)
        self._combo_title = QLabel("Select a combination")
        self._combo_title.setObjectName("scanCompsFieldLabel")
        self._combo_title.setWordWrap(True)
        preview_header.addWidget(self._combo_title, 1)
        self._preview_mode_combo = QComboBox()
        self._preview_mode_combo.setObjectName("scanCompsPreviewMode")
        self._preview_mode_combo.addItem(f"Filter [{self._filter_name}]", "filter")
        self._preview_mode_combo.addItem("Overview (with comps)", "overview")
        self._preview_mode_combo.setToolTip(
            "Filter shows the differential target curve for this band. Overview overlays the same set’s comparison stars."
        )
        self._preview_mode_combo.currentIndexChanged.connect(self._on_preview_mode_changed)
        preview_header.addWidget(self._preview_mode_combo, 0, Qt.AlignmentFlag.AlignTop)
        right.addLayout(preview_header)
        self._plot = LightCurvePlotWidget(page)
        self._plot.setMinimumHeight(280)
        right.addWidget(self._plot, 1)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self._prev_button = QPushButton("Previous")
        self._next_button = QPushButton("Next")
        for button in (self._prev_button, self._next_button):
            button.setObjectName("scanCompsSecondaryButton")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._prev_button.clicked.connect(self._go_previous)
        self._next_button.clicked.connect(self._go_next)
        cancel_button = QPushButton("Cancel")
        cancel_button.setObjectName("scanCompsSecondaryButton")
        cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_button.clicked.connect(self.reject)
        use_button = QPushButton("Use Selected")
        use_button.setObjectName("scanCompsPrimaryButton")
        use_button.setCursor(Qt.CursorShape.PointingHandCursor)
        use_button.setFixedHeight(38)
        use_button.clicked.connect(self._use_selected)
        actions.addWidget(self._prev_button)
        actions.addWidget(self._next_button)
        actions.addStretch(1)
        actions.addWidget(cancel_button)
        actions.addWidget(use_button)
        right.addLayout(actions)
        layout.addWidget(right_group, 7)
        return page

    def _apply_theme_to_plot(self) -> None:
        parent_window = self.parentWidget()
        if parent_window is None:
            return
        theme_name = "normal"
        custom_colors = None
        if hasattr(parent_window, "_current_theme_name"):
            theme_name = str(parent_window._current_theme_name())
        if hasattr(parent_window, "_current_custom_theme_colors"):
            custom_colors = cast(dict[str, str], parent_window._current_custom_theme_colors())
        if hasattr(self, "_plot"):
            self._plot.set_theme(theme_name, custom_colors)

    def _contrast_text_for_dialog(self, color_value: str) -> str:
        color = QColor(color_value)
        return "#0f1720" if color.lightness() > 160 else "#f7fbff"

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
        contrast = self._contrast_text_for_dialog(accent)
        alt_row = QColor(card_bg).lighter(108).name().lower()
        ready_green = "#2f9e64"
        ready_contrast = self._contrast_text_for_dialog(ready_green)

        self.setStyleSheet(
            "QDialog#scanCompsDialog {"
            f"background-color: {window_bg};"
            f"color: {body_text};"
            "}"
            "QFrame#scanCompsHeader {"
            f"background-color: {header_bg};"
            f"border: 1px solid {border_color};"
            "border-radius: 12px;"
            "}"
            f"QLabel#scanCompsTitleLabel {{ color: {body_text}; }}"
            f"QLabel#scanCompsMetaLabel {{ color: {accent_soft}; font-weight: 600; }}"
            f"QLabel#scanCompsIntroLabel {{ color: {muted_text}; }}"
            f"QLabel#scanCompsTipLabel {{ color: {muted_text}; font-size: 12px; }}"
            f"QLabel#scanCompsStatusLabel {{ color: {accent_soft}; font-style: italic; }}"
            f"QLabel#scanCompsFieldLabel {{ color: {body_text}; font-weight: 700; font-size: 14px; }}"
            f"QLabel#scanCompsFieldHelper {{ color: {muted_text}; font-size: 12px; }}"
            f"QLabel#scanCompsStepArrow {{ color: {muted_text}; font-size: 16px; padding: 0 2px; }}"
            "QPushButton#scanCompsStepBadge {"
            f"background-color: {QColor(border_color).darker(102).name().lower()};"
            f"color: {muted_text};"
            "border: none;"
            "border-radius: 8px;"
            "padding: 6px 10px;"
            "font-weight: 600;"
            "text-align: center;"
            "}"
            f"QPushButton#scanCompsStepBadge:hover {{ border: 1px solid {accent_soft}; }}"
            "QPushButton#scanCompsStepBadge[stepState='active'] {"
            f"background-color: {accent};"
            f"color: {contrast};"
            "}"
            "QPushButton#scanCompsStepBadge[stepState='done'] {"
            f"background-color: {QColor(accent).darker(115).name().lower()};"
            f"color: {contrast};"
            "}"
            "QLabel#scanCompsCountBadge {"
            f"background-color: {accent};"
            f"color: {contrast};"
            "border-radius: 10px;"
            "padding: 6px 10px;"
            "font-weight: 700;"
            "}"
            "QLabel#scanCompsCountBadge[readyState='ready'] {"
            f"background-color: {ready_green};"
            f"color: {ready_contrast};"
            "}"
            "QGroupBox {"
            f"background-color: {card_bg};"
            f"border: 1px solid {border_color};"
            "border-radius: 12px;"
            "margin-top: 18px;"
            "padding: 16px 12px 12px 12px;"
            "font-weight: 600;"
            f"color: {body_text};"
            "}"
            "QGroupBox::title {"
            "subcontrol-origin: margin;"
            "left: 12px;"
            "padding: 0 6px;"
            f"color: {accent};"
            "}"
            "QPushButton {"
            f"background-color: {card_bg};"
            f"color: {body_text};"
            f"border: 1px solid {border_color};"
            "border-radius: 8px;"
            "padding: 6px 14px;"
            "font-weight: 600;"
            "}"
            f"QPushButton:hover {{ border-color: {accent_soft}; background-color: {QColor(card_bg).lighter(112).name().lower()}; }}"
            f"QPushButton:pressed {{ background-color: {QColor(card_bg).darker(108).name().lower()}; }}"
            f"QPushButton#scanCompsPrimaryButton {{ background-color: {accent}; color: {contrast}; border-color: {accent_deep}; }}"
            f"QPushButton#scanCompsPrimaryButton:hover {{ background-color: {QColor(accent_soft).lighter(105).name().lower()}; color: {self._contrast_text_for_dialog(accent_soft)}; border: 2px solid {accent_soft}; }}"
            f"QPushButton#scanCompsPrimaryButton:pressed {{ background-color: {accent_deep}; color: {contrast}; border-color: {accent_deep}; }}"
            f"QPushButton#scanCompsSecondaryButton {{ border-color: {border_color}; }}"
            "QToolButton#scanCompsStarButton {"
            "border: none;"
            "background: transparent;"
            f"color: {accent_soft};"
            "font-size: 16px;"
            "padding: 2px 4px;"
            "}"
            f"QToolButton#scanCompsStarButton:hover {{ color: {accent}; }}"
            "QDoubleSpinBox, QSpinBox, QComboBox#scanCompsPreviewMode {"
            f"background-color: {window_bg};"
            f"color: {body_text};"
            f"border: 1px solid {border_color};"
            "border-radius: 8px;"
            "padding: 4px 8px;"
            "min-height: 30px;"
            "}"
            f"QDoubleSpinBox:focus, QSpinBox:focus, QComboBox#scanCompsPreviewMode:focus {{ border: 2px solid {accent_soft}; }}"
            "QTableWidget#scanCompsTable {"
            f"background-color: {window_bg};"
            f"alternate-background-color: {alt_row};"
            f"color: {body_text};"
            f"gridline-color: {border_color};"
            f"border: 1px solid {border_color};"
            "border-radius: 8px;"
            "}"
            "QHeaderView::section {"
            f"background-color: {card_bg};"
            f"color: {body_text};"
            f"border: 0px solid {border_color};"
            f"border-bottom: 1px solid {border_color};"
            "padding: 6px 8px;"
            "font-weight: 600;"
            "}"
            "QListWidget#scanCompsComboList {"
            f"background-color: {window_bg};"
            f"color: {body_text};"
            f"border: 1px solid {border_color};"
            "border-radius: 8px;"
            "padding: 4px;"
            "}"
            f"QListWidget#scanCompsComboList::item {{ padding: 2px; border-radius: 6px; }}"
            f"QListWidget#scanCompsComboList::item:selected {{ background-color: {accent}; color: {contrast}; }}"
            "QCheckBox { spacing: 8px; }"
            "QCheckBox::indicator { width: 18px; height: 18px; }"
        )

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
        self._combo_order = []
        self._combo_source_key = None
        self._evaluations = {}
        self._starred_set_keys = set()
        if not self._candidates:
            QMessageBox.warning(
                self,
                "Scan Comps",
                "No reference stars matched the current limits. Widen mag/color/distance or check the measured reference pool.",
            )
            return
        note = (
            f"Built {len(self._candidates)} candidate(s), ranked by closest magnitude then distance. "
            f"Favourite at least {_SESSION_DEFAULTS.comps_per_set}."
        )
        if self._target_bp_rp is not None and not self._color_used:
            note += " Color filter was off or unused for ranked candidates."
        elif self._color_used:
            note += " Color (Gaia BP−RP) hard-filter was applied."
        self._pool_note.setText(note)
        self._populate_pool_table()
        self._refresh_favourite_summary()
        self._stack.setCurrentIndex(1)
        self._status_label.setText("Tip: Favourite Best N selects the top magnitude matches instantly.")

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
                if column_index > 1:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._pool_table.setItem(row_index, column_index, item)
        self._pool_table.resizeRowsToContents()

    def _on_favourite_toggled(self, source_id: str, checked: bool) -> None:
        if checked:
            self._favourite_ids.add(source_id)
        else:
            self._favourite_ids.discard(source_id)
        self._refresh_favourite_summary()

    def _refresh_favourite_summary(self) -> None:
        needed = int(_SESSION_DEFAULTS.comps_per_set)
        count = len(self._favourite_ids)
        if hasattr(self, "_favourite_count_label"):
            self._favourite_count_label.setText(f"{count} / {needed} favourited")
            ready = count >= needed and needed > 0
            self._favourite_count_label.setProperty("readyState", "ready" if ready else "pending")
            self._favourite_count_label.style().unpolish(self._favourite_count_label)
            self._favourite_count_label.style().polish(self._favourite_count_label)

    def _favourite_all(self) -> None:
        self._favourite_ids = {candidate.source_id for candidate in self._candidates}
        self._populate_pool_table()
        self._refresh_favourite_summary()

    def _favourite_best_n(self) -> None:
        needed = int(_SESSION_DEFAULTS.comps_per_set)
        self._favourite_ids = {candidate.source_id for candidate in self._candidates[:needed]}
        self._populate_pool_table()
        self._refresh_favourite_summary()

    def _clear_favourites(self) -> None:
        self._favourite_ids.clear()
        self._populate_pool_table()
        self._refresh_favourite_summary()

    def _current_combo_source_key(self) -> tuple[frozenset[str], int]:
        return (frozenset(self._favourite_ids), int(_SESSION_DEFAULTS.comps_per_set))

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
        source_key = self._current_combo_source_key()
        if self._combo_order and self._combo_source_key == source_key:
            self._stack.setCurrentIndex(2)
            if self._worker is None and len(self._evaluations) < len(self._combo_order):
                self._start_worker()
            self._show_current_combo()
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
        self._stop_worker()
        self._combo_order = iter_unordered_comp_sets(favourites, n)
        self._combo_source_key = source_key
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
        for group in self._combo_order:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, group)
            row_widget = self._make_combo_row_widget(group)
            item.setSizeHint(row_widget.sizeHint())
            self._combo_list.addItem(item)
            self._combo_list.setItemWidget(item, row_widget)
        if self._combo_order:
            self._combo_list.setCurrentRow(self._current_combo_index)
        self._combo_list.blockSignals(False)

    def _combo_status_text(self, group: tuple[str, ...]) -> str:
        name_lookup = {item.source_id: item.source_name for item in self._candidates}
        names = ", ".join(name_lookup.get(source_id, source_id) for source_id in group)
        ready = group in self._evaluations
        status = "ready" if ready else "…"
        return f"{names}  ({status})"

    def _make_combo_row_widget(self, group: tuple[str, ...]) -> QWidget:
        row = QWidget(self._combo_list)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(4, 4, 8, 4)
        layout.setSpacing(6)
        star_button = QToolButton(row)
        star_button.setObjectName("scanCompsStarButton")
        star_button.setAutoRaise(True)
        star_button.setCursor(Qt.CursorShape.PointingHandCursor)
        star_button.setToolTip("Star this combination set")
        star_button.clicked.connect(lambda _checked=False, key=group: self._toggle_star_group(key))
        status_label = QLabel(self._combo_status_text(group), row)
        status_label.setObjectName("scanCompsComboRowLabel")
        status_label.setWordWrap(True)
        layout.addWidget(star_button, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(status_label, 1)
        row.setMinimumHeight(36)
        row.setProperty("comboGroup", group)
        self._sync_combo_row_widget(row, group)
        return row

    def _sync_combo_row_widget(self, row: QWidget, group: tuple[str, ...]) -> None:
        star_button = row.findChild(QToolButton, "scanCompsStarButton")
        status_label = row.findChild(QLabel, "scanCompsComboRowLabel")
        if star_button is not None:
            starred = group in self._starred_set_keys
            star_button.setText("★" if starred else "☆")
            star_button.setToolTip("Unstar this combination set" if starred else "Star this combination set")
        if status_label is not None:
            status_label.setText(self._combo_status_text(group))

    def _refresh_combo_list_labels(self) -> None:
        for row_index in range(self._combo_list.count()):
            item = self._combo_list.item(row_index)
            group = item.data(Qt.ItemDataRole.UserRole)
            if not isinstance(group, tuple):
                continue
            row_widget = self._combo_list.itemWidget(item)
            if row_widget is not None:
                self._sync_combo_row_widget(row_widget, group)

    def _update_combo_list_item(self, group: tuple[str, ...]) -> None:
        for row_index in range(self._combo_list.count()):
            item = self._combo_list.item(row_index)
            if item.data(Qt.ItemDataRole.UserRole) == group:
                row_widget = self._combo_list.itemWidget(item)
                if row_widget is not None:
                    self._sync_combo_row_widget(row_widget, group)
                return

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
        self._worker = None
        if worker is None:
            return
        for signal, slot in (
            (worker.set_ready, self._on_set_ready),
            (worker.progress_updated, self._status_label.setText),
            (worker.batch_completed, self._on_batch_completed),
            (worker.batch_failed, self._on_batch_failed),
        ):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        if worker.isRunning():
            worker.request_cancel()
            # Never block the UI waiting for combination workers to finish.
            worker.finished.connect(worker.deleteLater)
        else:
            worker.deleteLater()

    def _on_set_ready(self, evaluation: object) -> None:
        if not isinstance(evaluation, ScanCompSetEvaluation):
            return
        self._evaluations[evaluation.comparison_source_ids] = evaluation
        self._update_combo_list_item(evaluation.comparison_source_ids)
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

    def _on_preview_mode_changed(self) -> None:
        mode = self._preview_mode_combo.currentData()
        self._preview_mode = str(mode or "filter")
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
        if evaluation is None or evaluation.series is None:
            self._combo_title.setText(
                f"Set {self._current_combo_index + 1}/{len(self._combo_order)}: {names}\nBuilding light curve…"
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
            f"Set {self._current_combo_index + 1}/{len(self._combo_order)}: {names}\n"
            f"{evaluation.valid_point_count} usable point(s), {evaluation.excluded_point_count} excluded."
        )
        if self._preview_mode == "overview":
            self._plot_combo_overview(evaluation, group)
            return
        self._plot.plot_series(
            evaluation.series,
            empty_message="No differential points for this combination.",
            y_axis_mode=self._y_axis_mode,
        )

    def _plot_combo_overview(self, evaluation: ScanCompSetEvaluation, group: tuple[str, ...]) -> None:
        comparison_ids = set(group)
        measurements = list(evaluation.target_measurements) + [
            measurement
            for measurement in self._reference_measurements
            if measurement.source_id in comparison_ids
        ]
        layers, status_note = build_overview_light_curve_layers(
            measurements,
            self._target_source_id,
            max_comparison_stars=max(1, len(group)),
            preferred_comparison_source_ids=list(group),
        )
        empty_message = "No Overview series for this combination."
        if status_note:
            empty_message = status_note
        self._plot.plot_overview(
            layers,
            empty_message=empty_message,
            y_axis_mode=self._y_axis_mode,
            status_note=status_note,
            title=f"{self._target_source_name} Overview",
        )

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

    def _toggle_star_group(self, group: tuple[str, ...]) -> None:
        if group in self._starred_set_keys:
            self._starred_set_keys.discard(group)
        else:
            self._starred_set_keys.add(group)
        self._update_combo_list_item(group)

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
