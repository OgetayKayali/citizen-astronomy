from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

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
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

SkyExplorerMagLimitMode = Literal["ladder", "auto"]
SkyExplorerMagLimitTextScaleMode = Literal["off", "fainter", "brighter"]


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


def _normalized_magnitude_range(start_magnitude: float, max_magnitude: float) -> tuple[float, float]:
    start_value = max(-5.0, min(30.0, float(start_magnitude)))
    final_value = max(-5.0, min(30.0, float(max_magnitude)))
    if start_value > final_value:
        return final_value, start_value
    return start_value, final_value


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
        self.setMinimumWidth(460)

        options = initial_options or SkyExplorerMagLimitOptions()
        self._clear_requested = False
        start_magnitude, max_magnitude = _normalized_magnitude_range(
            getattr(options, "start_magnitude", options.auto_start_magnitude),
            options.max_magnitude,
        )
        increment = float(options.bin_size_mag or options.auto_step_magnitude or 0.5)

        self._ladder_mode_radio = QRadioButton("Mark magnitude ladder", self)
        self._ladder_mode_radio.setToolTip(
            "Select representative Gaia stars in magnitude bins and mark them whether or not they are detected in the image."
        )
        self._auto_mode_radio = QRadioButton("Find actual magnitude limit", self)
        self._auto_mode_radio.setToolTip(
            "Probe Gaia stars at each magnitude step and stop when too few pass the SNR detection test."
        )
        self._mode_group = QButtonGroup(self)
        self._mode_group.addButton(self._ladder_mode_radio)
        self._mode_group.addButton(self._auto_mode_radio)
        if options.mode == "auto":
            self._auto_mode_radio.setChecked(True)
        else:
            self._ladder_mode_radio.setChecked(True)

        self._start_magnitude_input = QDoubleSpinBox(self)
        self._start_magnitude_input.setDecimals(1)
        self._start_magnitude_input.setRange(-5.0, 30.0)
        self._start_magnitude_input.setSingleStep(0.5)
        self._start_magnitude_input.setSuffix(" mag")
        self._start_magnitude_input.setValue(start_magnitude)
        self._start_magnitude_input.setToolTip("Brightest magnitude included for both the ladder and the automatic limit.")

        self._increment_input = QDoubleSpinBox(self)
        self._increment_input.setDecimals(2)
        self._increment_input.setRange(0.1, 5.0)
        self._increment_input.setSingleStep(0.1)
        self._increment_input.setSuffix(" mag")
        self._increment_input.setValue(increment)
        self._increment_input.setToolTip("Shared bin width for the ladder and magnitude step for the automatic limit.")

        self._max_magnitude_input = QDoubleSpinBox(self)
        self._max_magnitude_input.setDecimals(1)
        self._max_magnitude_input.setRange(-5.0, 30.0)
        self._max_magnitude_input.setSingleStep(0.5)
        self._max_magnitude_input.setSuffix(" mag")
        self._max_magnitude_input.setValue(max_magnitude)
        self._max_magnitude_input.setToolTip("Faintest magnitude included for both the ladder and the automatic limit.")

        self._examples_per_bin_input = QSpinBox(self)
        self._examples_per_bin_input.setRange(1, 20)
        self._examples_per_bin_input.setValue(int(options.examples_per_bin))
        self._examples_per_bin_input.setToolTip("How many representative stars to mark in each magnitude bin.")

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

        self._mark_only_faintest_checkbox = QCheckBox("Mark only the faintest N", self)
        self._mark_only_faintest_checkbox.setChecked(bool(options.mark_only_faintest))
        self._mark_only_faintest_checkbox.setToolTip(
            "Keep only the faintest N marked stars. Use N greater than 1 when the single faintest detection may be an artifact."
        )
        self._faintest_count_input = QSpinBox(self)
        self._faintest_count_input.setRange(1, 50)
        self._faintest_count_input.setValue(max(1, int(getattr(options, "faintest_count", 1) or 1)))
        self._faintest_count_input.setToolTip("How many of the faintest stars to keep when the option is enabled.")
        self._mark_only_faintest_checkbox.toggled.connect(self._sync_faintest_count_enabled)

        self._auto_stars_per_bin_input = QSpinBox(self)
        self._auto_stars_per_bin_input.setRange(2, 50)
        self._auto_stars_per_bin_input.setValue(int(options.auto_stars_per_bin))
        self._auto_stars_per_bin_input.setToolTip("How many Gaia stars to test in each magnitude step.")

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

        mode_group = QGroupBox("Mode", self)
        mode_layout = QVBoxLayout()
        mode_layout.addWidget(self._ladder_mode_radio)
        mode_layout.addWidget(self._auto_mode_radio)
        mode_group.setLayout(mode_layout)

        range_group = QGroupBox("Magnitude range", self)
        range_layout = QFormLayout()
        range_layout.addRow("Starting magnitude", self._start_magnitude_input)
        range_layout.addRow("Increment", self._increment_input)
        range_layout.addRow("Final magnitude", self._max_magnitude_input)
        range_group.setLayout(range_layout)

        mark_group = QGroupBox("Marking", self)
        mark_layout = QFormLayout()
        mark_layout.addRow("Stars / bin", self._examples_per_bin_input)
        mark_layout.addRow("Scale text by magnitude", self._text_scale_combo)
        faintest_row = QWidget(self)
        faintest_layout = QHBoxLayout(faintest_row)
        faintest_layout.setContentsMargins(0, 0, 0, 0)
        faintest_layout.addWidget(self._mark_only_faintest_checkbox)
        faintest_layout.addWidget(self._faintest_count_input)
        faintest_layout.addStretch(1)
        mark_layout.addRow("", faintest_row)
        mark_group.setLayout(mark_layout)

        self._auto_group = QGroupBox("Automatic limit", self)
        auto_layout = QFormLayout()
        auto_layout.addRow("Stars to check", self._auto_stars_per_bin_input)
        auto_layout.addRow("Stars to pass", self._auto_required_visible_input)
        auto_layout.addRow("Detection SNR", self._auto_snr_threshold_input)
        self._auto_group.setLayout(auto_layout)

        self._help_label = QLabel(self)
        self._help_label.setWordWrap(True)

        self._buttons = QDialogButtonBox(self)
        self._run_button = self._buttons.addButton("Mark", QDialogButtonBox.ButtonRole.AcceptRole)
        self._clear_button = self._buttons.addButton("Clear markers", QDialogButtonBox.ButtonRole.ActionRole)
        self._clear_button.setEnabled(bool(markers_active))
        self._buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        self._clear_button.clicked.connect(self._request_clear_markers)

        self._ladder_mode_radio.toggled.connect(self._sync_mode_controls)
        self._auto_mode_radio.toggled.connect(self._sync_mode_controls)
        self._auto_stars_per_bin_input.valueChanged.connect(self._sync_required_visible_range)
        self._sync_required_visible_range()
        self._sync_faintest_count_enabled()
        self._sync_mode_controls()

        root = QVBoxLayout(self)
        root.addWidget(mode_group)
        root.addWidget(range_group)
        root.addWidget(mark_group)
        root.addWidget(self._auto_group)
        root.addWidget(self._help_label)
        root.addLayout(self._button_row())

    def _button_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(self._buttons)
        return row

    def _sync_required_visible_range(self) -> None:
        checked_stars = max(2, int(self._auto_stars_per_bin_input.value()))
        self._auto_required_visible_input.setRange(1, checked_stars - 1)
        if self._auto_required_visible_input.value() >= checked_stars:
            self._auto_required_visible_input.setValue(checked_stars - 1)

    def _sync_faintest_count_enabled(self) -> None:
        self._faintest_count_input.setEnabled(self._mark_only_faintest_checkbox.isChecked())

    def _sync_mode_controls(self) -> None:
        auto_mode = self._auto_mode_radio.isChecked()
        self._auto_group.setEnabled(auto_mode)
        if auto_mode:
            self._run_button.setText("Estimate")
            self._help_label.setText(
                "Checks Gaia stars from the starting magnitude to the final magnitude in the shared increment. "
                "A step fails when too few detections reach the SNR threshold; the previous successful step is the estimated limit."
            )
        else:
            self._run_button.setText("Mark")
            self._help_label.setText(
                "Queries Gaia from the starting magnitude to the final magnitude and marks representative stars "
                "in each increment so you can visually check whether they appear in the image."
            )

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
            mode="auto" if self._auto_mode_radio.isChecked() else "ladder",
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
        )
