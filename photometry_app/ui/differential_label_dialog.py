from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout

from photometry_app.core.models import CatalogStar, LightCurveSeries
from photometry_app.core.plotting import LightCurveFitConfig
from photometry_app.ui.light_curve_widget import LightCurvePlotWidget


class DifferentialQuickLabelDialog(QDialog):
    def __init__(
        self,
        *,
        entries: Sequence[CatalogStar],
        label_options: Sequence[tuple[str, str]],
        label_lookup: Callable[[CatalogStar], str | None],
        save_label: Callable[[CatalogStar, str], None],
        series_lookup: Callable[[CatalogStar], LightCurveSeries | None],
        details_lookup: Callable[[CatalogStar], str],
        fit_config: LightCurveFitConfig | None = None,
        y_axis_mode: str = "differential_magnitude",
        x_axis_mode: str = "datetime",
        phase_period_hours: float | None = None,
        phase_anchor_mode: str = "first_observation",
        phase_opacity_floor: float = 0.24,
        recent_period_error_bars_only: bool = False,
        theme: str = "normal",
        custom_theme_colors: dict[str, str] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._entries = list(entries)
        self._label_options = tuple((str(text), str(value)) for text, value in label_options)
        self._label_lookup = label_lookup
        self._save_label = save_label
        self._series_lookup = series_lookup
        self._details_lookup = details_lookup
        self._fit_config = fit_config
        self._y_axis_mode = str(y_axis_mode or "differential_magnitude")
        self._x_axis_mode = str(x_axis_mode or "datetime")
        self._phase_period_hours = phase_period_hours
        self._phase_anchor_mode = str(phase_anchor_mode or "first_observation")
        self._phase_opacity_floor = float(phase_opacity_floor)
        self._recent_period_error_bars_only = bool(recent_period_error_bars_only)
        self._current_entry_index = 0
        self._saved_count = 0

        self.setWindowTitle("Label Differential Sources")
        self.resize(920, 760)

        self._entry_label = QLabel()
        self._entry_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._entry_label.setWordWrap(True)

        self._light_curve_widget = LightCurvePlotWidget(self)
        self._light_curve_widget.setMinimumHeight(360)
        self._light_curve_widget.set_theme(theme, custom_theme_colors)

        self._details_output = QPlainTextEdit(self)
        self._details_output.setReadOnly(True)
        self._details_output.setPlaceholderText("Source review details will appear here.")
        self._details_output.setMinimumHeight(180)

        self._previous_button = QPushButton("<")
        self._previous_button.setToolTip("Review the previous source.")
        self._previous_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._previous_button.clicked.connect(self._show_previous_entry)
        self._next_button = QPushButton(">")
        self._next_button.setToolTip("Skip to the next source without changing the current label.")
        self._next_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._next_button.clicked.connect(self._show_next_entry)
        self._train_button = QPushButton("Train")
        self._train_button.setToolTip("Close the review window and train the Differential detectability model.")
        self._train_button.clicked.connect(self.accept)

        label_row = QHBoxLayout()
        label_row.setSpacing(6)
        label_row.addWidget(self._previous_button)
        for button_text, label_value in self._label_options:
            button = QPushButton(button_text)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(lambda _checked=False, value=label_value: self._save_current_label(value))
            label_row.addWidget(button)
        label_row.addWidget(self._next_button)

        controls_row = QHBoxLayout()
        controls_row.setSpacing(6)
        controls_row.addStretch(1)
        controls_row.addWidget(self._train_button)

        layout = QVBoxLayout()
        layout.addWidget(self._entry_label)
        layout.addWidget(self._light_curve_widget, stretch=1)
        layout.addWidget(self._details_output)
        layout.addLayout(label_row)
        layout.addLayout(controls_row)
        self.setLayout(layout)

        self._refresh_entry()

    @property
    def saved_count(self) -> int:
        return self._saved_count

    def _current_entry(self) -> CatalogStar | None:
        if not self._entries:
            return None
        if self._current_entry_index < 0 or self._current_entry_index >= len(self._entries):
            return None
        return self._entries[self._current_entry_index]

    def _refresh_entry(self) -> None:
        entry = self._current_entry()
        if entry is None:
            self._entry_label.setText("No Differential source rows are available for labeling.")
            self._light_curve_widget.show_message("Differential Source Review", "No source rows are available for labeling.")
            self._details_output.clear()
            self._previous_button.setEnabled(False)
            self._next_button.setEnabled(False)
            self._train_button.setEnabled(False)
            return
        current_label = self._label_lookup(entry)
        label_text = f"Current label: {self._format_label(current_label)}" if current_label else "Unlabeled"
        magnitude = "-" if entry.magnitude is None else f"{entry.magnitude:.2f}"
        self._entry_label.setText(
            f"{entry.name} ({self._current_entry_index + 1}/{len(self._entries)})  "
            f"{entry.catalog}:{entry.source_id}  Mag {magnitude}  {label_text}"
        )
        series = self._series_lookup(entry)
        if series is None:
            self._light_curve_widget.show_message(
                f"{entry.name} Light Curve",
                "No light-curve series is available for this source.",
                y_axis_mode=self._y_axis_mode,
            )
        else:
            self._light_curve_widget.plot_series(
                series,
                "Selected series has no valid values for the selected light-curve axis.",
                fit_config=self._fit_config,
                y_axis_mode=self._y_axis_mode,
                x_axis_mode=self._x_axis_mode,
                phase_period_hours=self._phase_period_hours,
                phase_anchor_mode=self._phase_anchor_mode,
                phase_opacity_floor=self._phase_opacity_floor,
                recent_period_error_bars_only=self._recent_period_error_bars_only,
            )
        self._details_output.setPlainText(self._details_lookup(entry))
        self._previous_button.setEnabled(self._current_entry_index > 0)
        self._next_button.setEnabled(self._current_entry_index < len(self._entries) - 1)
        self._train_button.setEnabled(bool(self._entries))

    def _save_current_label(self, label: str) -> None:
        entry = self._current_entry()
        if entry is None:
            return
        normalized_label = str(label or "").strip()
        if not normalized_label:
            return
        self._save_label(entry, normalized_label)
        self._saved_count += 1
        if self._current_entry_index < len(self._entries) - 1:
            self._current_entry_index += 1
        self._refresh_entry()

    def _show_next_entry(self) -> None:
        if self._current_entry_index < len(self._entries) - 1:
            self._current_entry_index += 1
        self._refresh_entry()

    def _show_previous_entry(self) -> None:
        if self._current_entry_index > 0:
            self._current_entry_index -= 1
        self._refresh_entry()

    def _format_label(self, label: str | None) -> str:
        return str(label or "").replace("_", " ").title() or "Unlabeled"