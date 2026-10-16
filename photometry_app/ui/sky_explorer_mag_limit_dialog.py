from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

SkyExplorerMagLimitMode = Literal["ladder", "auto"]
SkyExplorerMagLimitTextScaleMode = Literal["off", "fainter", "brighter"]

_CATALOG_CHAIN = "Gaia → Pan-STARRS → DES DR1 / SkyMapper"
_CATALOG_INFO_TOOLTIP = (
    "Gaia is queried to about magnitude 21. Fainter sources come from Pan-STARRS (Dec ≥ −30°) "
    "or DES DR1 farther south, with SkyMapper as a fallback outside the DES footprint. "
    "Magnitude ladder marks representative catalog sources in each step, including sources that "
    "fall on the image but may be too faint to see. Automatic limit tests sources at each step "
    "and stops when too few detections reach the SNR threshold; the previous successful step is the estimated limit."
)


@dataclass(frozen=True, slots=True)
class SkyExplorerMagLimitOptions:
    mode: SkyExplorerMagLimitMode = "ladder"
    bin_size_mag: float = 0.5
    examples_per_bin: int = 1
    mark_only_faintest: bool = False
    faintest_count: int = 1
    start_magnitude: float = 12.0
    max_magnitude: float = 17.0
    auto_stars_per_bin: int = 10
    auto_required_visible_stars: int = 7
    auto_start_magnitude: float = 12.0
    auto_step_magnitude: float = 0.5
    auto_snr_threshold: float = 5.0
    text_scale_mode: SkyExplorerMagLimitTextScaleMode = "off"
    only_detected_sources: bool = False


def _normalized_magnitude_range(start_magnitude: float, max_magnitude: float) -> tuple[float, float]:
    start_value = max(-5.0, min(30.0, float(start_magnitude)))
    final_value = max(-5.0, min(30.0, float(max_magnitude)))
    if start_value > final_value:
        return final_value, start_value
    return start_value, final_value


def mag_limit_step_count(bright_limit: float, faint_limit: float, step: float) -> int:
    """Inclusive magnitude steps from the bright limit through the faint limit."""
    bright, faint = _normalized_magnitude_range(bright_limit, faint_limit)
    increment = max(0.1, float(step))
    if not math.isfinite(bright) or not math.isfinite(faint) or not math.isfinite(increment):
        return 0
    return int(math.floor(((faint - bright) / increment) + 1e-9)) + 1


def mag_limit_text_scale_factor(
    magnitude: float,
    *,
    min_magnitude: float,
    max_magnitude: float,
    mode: str,
    min_scale: float = 0.75,
    max_scale: float = 1.30,
) -> float:
    if str(mode).strip().lower() not in {"fainter", "brighter"}:
        return 1.0
    span = float(max_magnitude) - float(min_magnitude)
    if not math.isfinite(span) or span <= 1e-9:
        return 1.0
    t = (float(magnitude) - float(min_magnitude)) / span
    t = min(1.0, max(0.0, t))
    if str(mode).strip().lower() == "brighter":
        t = 1.0 - t
    return min_scale + ((max_scale - min_scale) * t)


class SkyExplorerMagLimitDialog(QDialog):
    def __init__(
        self,
        *,
        initial_options: SkyExplorerMagLimitOptions | None = None,
        markers_active: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Estimate Magnitude Limit")
        self.setMinimumWidth(480)

        options = initial_options or SkyExplorerMagLimitOptions()
        self._clear_requested = False
        start_magnitude, max_magnitude = _normalized_magnitude_range(
            getattr(options, "start_magnitude", options.auto_start_magnitude),
            options.max_magnitude,
        )
        increment = float(options.bin_size_mag or options.auto_step_magnitude or 0.5)

        self._ladder_mode_button = QPushButton("Magnitude ladder", self)
        self._ladder_mode_button.setCheckable(True)
        self._ladder_mode_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._ladder_mode_button.setToolTip(
            "Mark representative catalog sources in each magnitude step. Sources that project onto the image are included even when they are not visibly detected."
        )
        self._auto_mode_button = QPushButton("Automatic limit", self)
        self._auto_mode_button.setCheckable(True)
        self._auto_mode_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._auto_mode_button.setToolTip(
            "Test catalog sources at each magnitude step and stop when too few pass the SNR detection test."
        )
        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)
        self._mode_group.addButton(self._ladder_mode_button)
        self._mode_group.addButton(self._auto_mode_button)
        self._style_mode_buttons()

        self._start_magnitude_input = QDoubleSpinBox(self)
        self._start_magnitude_input.setDecimals(1)
        self._start_magnitude_input.setRange(-5.0, 30.0)
        self._start_magnitude_input.setSingleStep(0.5)
        self._start_magnitude_input.setSuffix(" mag")
        self._start_magnitude_input.setValue(start_magnitude)
        self._start_magnitude_input.setToolTip("Brightest magnitude included for both the ladder and the automatic limit.")

        self._max_magnitude_input = QDoubleSpinBox(self)
        self._max_magnitude_input.setDecimals(1)
        self._max_magnitude_input.setRange(-5.0, 30.0)
        self._max_magnitude_input.setSingleStep(0.5)
        self._max_magnitude_input.setSuffix(" mag")
        self._max_magnitude_input.setValue(max_magnitude)
        self._max_magnitude_input.setToolTip("Faintest magnitude included for both the ladder and the automatic limit.")

        self._increment_input = QDoubleSpinBox(self)
        self._increment_input.setDecimals(2)
        self._increment_input.setRange(0.1, 5.0)
        self._increment_input.setSingleStep(0.1)
        self._increment_input.setSuffix(" mag")
        self._increment_input.setValue(increment)
        self._increment_input.setToolTip("Shared step for the ladder bins and the automatic limit.")

        self._examples_per_bin_input = QSpinBox(self)
        self._examples_per_bin_input.setRange(1, 20)
        self._examples_per_bin_input.setValue(int(options.examples_per_bin))
        self._examples_per_bin_input.setToolTip("How many representative sources to mark in each magnitude step.")

        self._text_scale_combo = QComboBox(self)
        self._text_scale_combo.addItem("Off", "off")
        self._text_scale_combo.addItem("Fainter looks bigger", "fainter")
        self._text_scale_combo.addItem("Brighter looks bigger", "brighter")
        scale_mode = str(getattr(options, "text_scale_mode", "off") or "off")
        scale_index = max(0, self._text_scale_combo.findData(scale_mode))
        self._text_scale_combo.setCurrentIndex(scale_index)
        self._text_scale_combo.setToolTip(
            "Scale Mag Limit labels by magnitude. Choose whether the faintest or the brightest labels are larger."
        )

        self._mark_only_faintest_checkbox = QCheckBox("Show only faintest", self)
        self._mark_only_faintest_checkbox.setChecked(bool(options.mark_only_faintest))
        self._mark_only_faintest_checkbox.setToolTip(
            "Keep only the faintest N marked sources. Use N greater than 1 when the single faintest detection may be an artifact."
        )
        self._faintest_count_input = QSpinBox(self)
        self._faintest_count_input.setRange(1, 50)
        self._faintest_count_input.setValue(max(1, int(getattr(options, "faintest_count", 1) or 1)))
        self._faintest_count_input.setToolTip("How many of the faintest sources to keep when the option is enabled.")

        self._only_detected_checkbox = QCheckBox("Only sources that appear", self)
        self._only_detected_checkbox.setChecked(bool(getattr(options, "only_detected_sources", False)))
        self._only_detected_checkbox.setToolTip(
            "On: mark only sources detected in the image at the minimum SNR. Off: mark a catalog source in each step even when it is too faint to see."
        )

        self._auto_stars_per_bin_input = QSpinBox(self)
        self._auto_stars_per_bin_input.setRange(2, 50)
        self._auto_stars_per_bin_input.setValue(int(options.auto_stars_per_bin))
        self._auto_stars_per_bin_input.setToolTip("How many catalog sources to test in each magnitude step.")

        self._auto_required_visible_input = QSpinBox(self)
        self._auto_required_visible_input.setRange(1, 49)
        self._auto_required_visible_input.setValue(int(options.auto_required_visible_stars))
        self._auto_required_visible_input.setToolTip(
            "Minimum detections that must pass the SNR test for a magnitude step to count as successful."
        )

        self._auto_snr_threshold_input = QDoubleSpinBox(self)
        self._auto_snr_threshold_input.setDecimals(1)
        self._auto_snr_threshold_input.setRange(0.1, 100.0)
        self._auto_snr_threshold_input.setSingleStep(0.5)
        self._auto_snr_threshold_input.setValue(float(options.auto_snr_threshold))
        self._auto_snr_threshold_input.setToolTip("A source counts as detected when its signal-to-noise ratio reaches this value.")

        range_group = QGroupBox("Magnitude range", self)
        range_layout = QFormLayout()
        range_layout.addRow("Bright limit", self._start_magnitude_input)
        range_layout.addRow("Faint limit", self._max_magnitude_input)
        range_layout.addRow("Step", self._increment_input)
        range_group.setLayout(range_layout)

        self._sampling_group = QGroupBox("Sampling & labels", self)
        sampling_layout = QFormLayout()
        sampling_layout.addRow("Sources per step", self._examples_per_bin_input)
        sampling_layout.addRow("Label size", self._text_scale_combo)
        faintest_row = QWidget(self)
        faintest_layout = QHBoxLayout(faintest_row)
        faintest_layout.setContentsMargins(0, 0, 0, 0)
        faintest_layout.addWidget(self._mark_only_faintest_checkbox)
        faintest_layout.addWidget(self._faintest_count_input)
        faintest_layout.addStretch(1)
        sampling_layout.addRow("", faintest_row)
        sampling_layout.addRow("", self._only_detected_checkbox)
        self._sampling_layout = sampling_layout
        self._snr_host = QWidget(self)
        snr_host_layout = QFormLayout(self._snr_host)
        snr_host_layout.setContentsMargins(0, 0, 0, 0)
        snr_host_layout.addRow("Minimum SNR", self._auto_snr_threshold_input)
        self._sampling_group.setLayout(sampling_layout)

        self._auto_group = QGroupBox("Detection criteria", self)
        auto_layout = QFormLayout()
        auto_layout.addRow("Sources tested per step", self._auto_stars_per_bin_input)
        auto_layout.addRow("Detections required", self._auto_required_visible_input)
        self._auto_layout = auto_layout
        self._auto_group.setLayout(auto_layout)

        self._help_label = QLabel(_CATALOG_CHAIN, self)
        self._help_label.setWordWrap(True)
        self._catalog_info_button = QToolButton(self)
        self._catalog_info_button.setText("i")
        self._catalog_info_button.setAutoRaise(True)
        self._catalog_info_button.setToolTip(_CATALOG_INFO_TOOLTIP)
        self._catalog_info_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._catalog_info_button.setAccessibleName("Catalog details")
        catalog_row = QWidget(self)
        catalog_row_layout = QHBoxLayout(catalog_row)
        catalog_row_layout.setContentsMargins(0, 0, 0, 0)
        catalog_row_layout.addWidget(self._help_label, 1)
        catalog_row_layout.addWidget(self._catalog_info_button)
        catalog_group = QGroupBox("Catalogs", self)
        catalog_layout = QVBoxLayout()
        catalog_layout.addWidget(catalog_row)
        catalog_group.setLayout(catalog_layout)

        self._summary_label = QLabel(self)
        self._summary_label.setWordWrap(True)

        self._buttons = QDialogButtonBox(self)
        self._run_button = self._buttons.addButton("Mark", QDialogButtonBox.ButtonRole.AcceptRole)
        self._clear_button = self._buttons.addButton("Clear", QDialogButtonBox.ButtonRole.ActionRole)
        self._clear_button.setToolTip("Clear magnitude limit markers.")
        self._clear_button.setEnabled(bool(markers_active))
        self._buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        self._clear_button.clicked.connect(self._request_clear_markers)

        if options.mode == "auto":
            self._auto_mode_button.setChecked(True)
        else:
            self._ladder_mode_button.setChecked(True)

        self._ladder_mode_button.toggled.connect(self._sync_mode_controls)
        self._auto_mode_button.toggled.connect(self._sync_mode_controls)
        self._mark_only_faintest_checkbox.toggled.connect(self._sync_faintest_count_enabled)
        self._mark_only_faintest_checkbox.toggled.connect(self._update_summary)
        self._only_detected_checkbox.toggled.connect(self._sync_detected_source_controls)
        self._only_detected_checkbox.toggled.connect(self._update_summary)
        self._auto_stars_per_bin_input.valueChanged.connect(self._sync_required_visible_range)
        for spin in (
            self._start_magnitude_input,
            self._max_magnitude_input,
            self._increment_input,
            self._auto_snr_threshold_input,
        ):
            spin.valueChanged.connect(self._update_summary)
        for spin in (
            self._examples_per_bin_input,
            self._faintest_count_input,
            self._auto_stars_per_bin_input,
            self._auto_required_visible_input,
        ):
            spin.valueChanged.connect(self._update_summary)

        self._sync_required_visible_range()
        self._sync_faintest_count_enabled()
        self._sync_detected_source_controls()
        self._sync_mode_controls()

        mode_row = QHBoxLayout()
        mode_row.setSpacing(0)
        mode_row.addWidget(self._ladder_mode_button)
        mode_row.addWidget(self._auto_mode_button)

        root = QVBoxLayout(self)
        root.addLayout(mode_row)
        root.addWidget(range_group)
        root.addWidget(self._sampling_group)
        root.addWidget(self._auto_group)
        root.addWidget(catalog_group)
        root.addWidget(self._summary_label)
        root.addLayout(self._button_row())
        self.adjustSize()

    def _style_mode_buttons(self) -> None:
        palette = self.palette()
        button = palette.color(QPalette.ColorRole.Button).name()
        text = palette.color(QPalette.ColorRole.ButtonText).name()
        highlight = palette.color(QPalette.ColorRole.Highlight).name()
        highlight_text = palette.color(QPalette.ColorRole.HighlightedText).name()
        border = palette.color(QPalette.ColorRole.Mid).name()
        hover = QColor(button).lighter(118).name()
        shared = (
            f"background-color: {button}; color: {text}; border: 1px solid {border}; "
            "padding: 7px 14px; font-weight: 600;"
        )
        checked = f"background-color: {highlight}; color: {highlight_text}; border: 1px solid {highlight};"
        self._ladder_mode_button.setStyleSheet(
            "QPushButton {"
            f"{shared} border-top-left-radius: 6px; border-bottom-left-radius: 6px; border-right: none;"
            "}"
            f"QPushButton:checked {{ {checked} }}"
            f"QPushButton:hover:!checked {{ background-color: {hover}; }}"
        )
        self._auto_mode_button.setStyleSheet(
            "QPushButton {"
            f"{shared} border-top-right-radius: 6px; border-bottom-right-radius: 6px;"
            "}"
            f"QPushButton:checked {{ {checked} }}"
            f"QPushButton:hover:!checked {{ background-color: {hover}; }}"
        )

    def _button_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(self._buttons)
        return row

    def _sync_required_visible_range(self) -> None:
        checked_sources = max(2, int(self._auto_stars_per_bin_input.value()))
        self._auto_required_visible_input.setRange(1, checked_sources - 1)
        if self._auto_required_visible_input.value() >= checked_sources:
            self._auto_required_visible_input.setValue(checked_sources - 1)

    def _sync_faintest_count_enabled(self) -> None:
        self._faintest_count_input.setEnabled(self._mark_only_faintest_checkbox.isChecked())

    def _detach_snr_host(self) -> None:
        # removeRow deletes the widgets in the row, so pull the host out first.
        for layout in (self._sampling_layout, self._auto_layout):
            for row in range(layout.rowCount() - 1, -1, -1):
                item = layout.itemAt(row, QFormLayout.ItemRole.SpanningRole)
                if item is None or item.widget() is not self._snr_host:
                    continue
                self._snr_host.setParent(self)
                layout.removeRow(row)
                return

    def _sync_detected_source_controls(self) -> None:
        self._detach_snr_host()
        if self._auto_mode_button.isChecked():
            self._auto_layout.addRow(self._snr_host)
            self._snr_host.setVisible(True)
            self.adjustSize()
            return
        self._sampling_layout.addRow(self._snr_host)
        self._snr_host.setVisible(self._only_detected_checkbox.isChecked())
        self.adjustSize()

    def _sync_mode_controls(self) -> None:
        auto_mode = self._auto_mode_button.isChecked()
        self._sampling_group.setVisible(not auto_mode)
        self._auto_group.setVisible(auto_mode)
        self._run_button.setText("Estimate limit" if auto_mode else "Mark")
        self._sync_detected_source_controls()
        self._update_summary()
        self.adjustSize()

    def _update_summary(self) -> None:
        bright, faint = _normalized_magnitude_range(
            self._start_magnitude_input.value(),
            self._max_magnitude_input.value(),
        )
        if self._auto_mode_button.isChecked():
            self._summary_label.setText(
                f"Search: {bright:.1f}–{faint:.1f} mag · "
                f"{int(self._auto_stars_per_bin_input.value())} sources/step · "
                f"≥{int(self._auto_required_visible_input.value())} detections at "
                f"SNR ≥{float(self._auto_snr_threshold_input.value()):.1f}"
            )
            return
        steps = mag_limit_step_count(bright, faint, self._increment_input.value())
        detected_note = " detected" if self._only_detected_checkbox.isChecked() else ""
        if self._mark_only_faintest_checkbox.isChecked():
            self._summary_label.setText(
                f"{steps} magnitude steps · showing {int(self._faintest_count_input.value())} faintest{detected_note} sources"
            )
            return
        sources = steps * max(1, int(self._examples_per_bin_input.value()))
        reference_note = "detected" if self._only_detected_checkbox.isChecked() else "reference"
        self._summary_label.setText(f"{steps} magnitude steps · up to {sources} {reference_note} sources")

    def _request_clear_markers(self) -> None:
        self._clear_requested = True
        self.accept()

    def clear_markers_requested(self) -> bool:
        return bool(self._clear_requested)

    def selected_options(self) -> SkyExplorerMagLimitOptions:
        stars_per_bin = max(2, int(self._auto_stars_per_bin_input.value()))
        required_visible = min(max(1, int(self._auto_required_visible_input.value())), stars_per_bin - 1)
        start_magnitude, max_magnitude = _normalized_magnitude_range(
            self._start_magnitude_input.value(),
            self._max_magnitude_input.value(),
        )
        increment = max(0.1, float(self._increment_input.value()))
        scale_mode = str(self._text_scale_combo.currentData() or "off")
        if scale_mode not in {"off", "fainter", "brighter"}:
            scale_mode = "off"
        return SkyExplorerMagLimitOptions(
            mode="auto" if self._auto_mode_button.isChecked() else "ladder",
            bin_size_mag=increment,
            examples_per_bin=max(1, int(self._examples_per_bin_input.value())),
            mark_only_faintest=bool(self._mark_only_faintest_checkbox.isChecked()),
            faintest_count=max(1, int(self._faintest_count_input.value())),
            start_magnitude=start_magnitude,
            max_magnitude=max_magnitude,
            auto_stars_per_bin=stars_per_bin,
            auto_required_visible_stars=required_visible,
            auto_start_magnitude=start_magnitude,
            auto_step_magnitude=increment,
            auto_snr_threshold=float(self._auto_snr_threshold_input.value()),
            text_scale_mode=scale_mode,  # type: ignore[arg-type]
            only_detected_sources=bool(self._only_detected_checkbox.isChecked()),
        )
