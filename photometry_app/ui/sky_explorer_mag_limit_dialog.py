from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
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


@dataclass(frozen=True, slots=True)
class SkyExplorerMagLimitOptions:
    mode: SkyExplorerMagLimitMode = "ladder"
    bin_size_mag: float = 0.5
    examples_per_bin: int = 1
    mark_only_faintest: bool = False
    max_magnitude: float = 17.0
    auto_stars_per_bin: int = 10
    auto_required_visible_stars: int = 7
    auto_start_magnitude: float = 12.0
    auto_step_magnitude: float = 0.5
    auto_snr_threshold: float = 5.0


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

        self._bin_size_input = QDoubleSpinBox(self)
        self._bin_size_input.setDecimals(2)
        self._bin_size_input.setRange(0.1, 5.0)
        self._bin_size_input.setSingleStep(0.1)
        self._bin_size_input.setSuffix(" mag")
        self._bin_size_input.setValue(float(options.bin_size_mag))
        self._bin_size_input.setToolTip("Magnitude bin width used when selecting stars to mark.")

        self._examples_per_bin_input = QSpinBox(self)
        self._examples_per_bin_input.setRange(1, 20)
        self._examples_per_bin_input.setValue(int(options.examples_per_bin))
        self._examples_per_bin_input.setToolTip("How many representative stars to mark in each magnitude bin.")

        self._max_magnitude_input = QDoubleSpinBox(self)
        self._max_magnitude_input.setDecimals(1)
        self._max_magnitude_input.setRange(-5.0, 30.0)
        self._max_magnitude_input.setSingleStep(0.5)
        self._max_magnitude_input.setSuffix(" mag")
        self._max_magnitude_input.setValue(float(options.max_magnitude))
        self._max_magnitude_input.setToolTip("Faintest Gaia magnitude included when marking the ladder.")

        self._mark_only_faintest_checkbox = QCheckBox("Mark only the faintest", self)
        self._mark_only_faintest_checkbox.setChecked(bool(options.mark_only_faintest))
        self._mark_only_faintest_checkbox.setToolTip(
            "Avoid filling the image with bright-star markers. Ladder mode keeps only the faintest bin; "
            "auto mode marks only the faintest confirmed detections at the found limit."
        )

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

        self._auto_start_magnitude_input = QDoubleSpinBox(self)
        self._auto_start_magnitude_input.setDecimals(1)
        self._auto_start_magnitude_input.setRange(-5.0, 30.0)
        self._auto_start_magnitude_input.setSingleStep(0.5)
        self._auto_start_magnitude_input.setSuffix(" mag")
        self._auto_start_magnitude_input.setValue(float(options.auto_start_magnitude))

        self._auto_step_magnitude_input = QDoubleSpinBox(self)
        self._auto_step_magnitude_input.setDecimals(2)
        self._auto_step_magnitude_input.setRange(0.1, 5.0)
        self._auto_step_magnitude_input.setSingleStep(0.1)
        self._auto_step_magnitude_input.setSuffix(" mag")
        self._auto_step_magnitude_input.setValue(float(options.auto_step_magnitude))

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

        mark_group = QGroupBox("Marking", self)
        mark_layout = QFormLayout()
        mark_layout.addRow("Bin size", self._bin_size_input)
        mark_layout.addRow("Stars / bin", self._examples_per_bin_input)
        mark_layout.addRow("Max magnitude", self._max_magnitude_input)
        mark_layout.addRow("", self._mark_only_faintest_checkbox)
        mark_group.setLayout(mark_layout)

        self._auto_group = QGroupBox("Automatic limit", self)
        auto_layout = QFormLayout()
        auto_layout.addRow("Stars to check", self._auto_stars_per_bin_input)
        auto_layout.addRow("Stars to pass", self._auto_required_visible_input)
        auto_layout.addRow("Starting magnitude", self._auto_start_magnitude_input)
        auto_layout.addRow("Increment", self._auto_step_magnitude_input)
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
        self._sync_mode_controls()

        root = QVBoxLayout(self)
        root.addWidget(mode_group)
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

    def _sync_mode_controls(self) -> None:
        auto_mode = self._auto_mode_radio.isChecked()
        self._auto_group.setEnabled(auto_mode)
        self._max_magnitude_input.setEnabled(not auto_mode)
        if auto_mode:
            self._run_button.setText("Estimate")
            self._help_label.setText(
                "Checks Gaia stars at each magnitude step. By default, 10 stars are tested and a step fails "
                "when fewer than 7 detections reach SNR 5; the previous successful step is the estimated limit."
            )
        else:
            self._run_button.setText("Mark")
            self._help_label.setText(
                "Queries Gaia up to the max magnitude and marks representative stars in each bin so you can "
                "visually check whether they appear in the image."
            )

    def _request_clear_markers(self) -> None:
        self._clear_requested = True
        self.accept()

    def clear_markers_requested(self) -> bool:
        return bool(self._clear_requested)

    def selected_options(self) -> SkyExplorerMagLimitOptions:
        stars_per_bin = max(2, int(self._auto_stars_per_bin_input.value()))
        required_visible = min(max(1, int(self._auto_required_visible_input.value())), stars_per_bin - 1)
        return SkyExplorerMagLimitOptions(
            mode="auto" if self._auto_mode_radio.isChecked() else "ladder",
            bin_size_mag=float(self._bin_size_input.value()),
            examples_per_bin=max(1, int(self._examples_per_bin_input.value())),
            mark_only_faintest=bool(self._mark_only_faintest_checkbox.isChecked()),
            max_magnitude=float(self._max_magnitude_input.value()),
            auto_stars_per_bin=stars_per_bin,
            auto_required_visible_stars=required_visible,
            auto_start_magnitude=float(self._auto_start_magnitude_input.value()),
            auto_step_magnitude=float(self._auto_step_magnitude_input.value()),
            auto_snr_threshold=float(self._auto_snr_threshold_input.value()),
        )
