from __future__ import annotations

from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from photometry_app.core.sky_explorer_collage import (
    DEFAULT_COLLAGE_MARGIN_FRACTION,
    SkyExplorerCollageLayout,
    SkyExplorerCollageOptions,
    SkyExplorerCollageScope,
    automatic_collage_column_count,
    format_sky_explorer_collage_summary,
)


class SkyExplorerCollageDialog(QDialog):
    def __init__(
        self,
        *,
        selected_count: int,
        selected_eligible_count: int,
        selected_skipped_count: int,
        visible_count: int,
        visible_eligible_count: int,
        visible_skipped_count: int,
        initial_options: SkyExplorerCollageOptions | None = None,
        initial_scope: SkyExplorerCollageScope = "selected",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sky Explorer Collage")
        self.setMinimumWidth(420)

        self._selected_count = max(0, int(selected_count))
        self._selected_eligible_count = max(0, int(selected_eligible_count))
        self._selected_skipped_count = max(0, int(selected_skipped_count))
        self._visible_count = max(0, int(visible_count))
        self._visible_eligible_count = max(0, int(visible_eligible_count))
        self._visible_skipped_count = max(0, int(visible_skipped_count))

        options = initial_options or SkyExplorerCollageOptions()
        preferred_scope: SkyExplorerCollageScope = initial_scope
        if preferred_scope == "selected" and self._selected_eligible_count <= 0 and self._visible_eligible_count > 0:
            preferred_scope = "all_eligible_visible"

        self._selected_scope_radio = QRadioButton("Selected Objects", self)
        self._all_visible_scope_radio = QRadioButton("All Eligible Visible Objects", self)
        self._scope_group = QButtonGroup(self)
        self._scope_group.addButton(self._selected_scope_radio)
        self._scope_group.addButton(self._all_visible_scope_radio)
        if preferred_scope == "all_eligible_visible":
            self._all_visible_scope_radio.setChecked(True)
        else:
            self._selected_scope_radio.setChecked(True)
        self._selected_scope_radio.setEnabled(self._selected_count > 0)
        if self._selected_count <= 0 and self._visible_eligible_count > 0:
            self._all_visible_scope_radio.setChecked(True)

        self._uniform_layout_radio = QRadioButton("Uniform Tiles", self)
        self._natural_layout_radio = QRadioButton("Natural Sizes", self)
        self._mosaic_layout_radio = QRadioButton("Packed Mosaic", self)
        self._mosaic_layout_radio.setToolTip(
            "Arrange largest-to-smallest crops into an interlocking mosaic that fills a "
            "rectangle. Themed gutters keep each sky section visually separate without "
            "black letterboxing."
        )
        self._layout_group = QButtonGroup(self)
        self._layout_group.addButton(self._uniform_layout_radio)
        self._layout_group.addButton(self._natural_layout_radio)
        self._layout_group.addButton(self._mosaic_layout_radio)
        if options.layout == "mosaic":
            self._mosaic_layout_radio.setChecked(True)
        elif options.layout == "natural":
            self._natural_layout_radio.setChecked(True)
        else:
            self._uniform_layout_radio.setChecked(True)

        self._margin_input = QDoubleSpinBox(self)
        self._margin_input.setRange(0.0, 100.0)
        self._margin_input.setDecimals(0)
        self._margin_input.setSingleStep(5.0)
        self._margin_input.setSuffix(" %")
        self._margin_input.setValue(max(0.0, float(options.margin_fraction) * 100.0))
        self._margin_input.setToolTip(
            "Extra sky margin around each catalog size. 15% enlarges each crop by 15% relative to the catalog axes."
        )

        self._labels_checkbox = QCheckBox("Show object labels", self)
        self._labels_checkbox.setChecked(bool(options.show_labels))

        self._summary_label = QLabel(self)
        self._summary_label.setWordWrap(True)

        self._selected_scope_radio.toggled.connect(lambda _checked: self._update_summary())
        self._all_visible_scope_radio.toggled.connect(lambda _checked: self._update_summary())
        self._uniform_layout_radio.toggled.connect(lambda _checked: self._update_summary())
        self._natural_layout_radio.toggled.connect(lambda _checked: self._update_summary())
        self._mosaic_layout_radio.toggled.connect(lambda _checked: self._update_summary())
        self._margin_input.valueChanged.connect(lambda _value: self._update_summary())
        self._labels_checkbox.toggled.connect(lambda _checked: self._update_summary())

        form_layout = QFormLayout()
        form_layout.addRow("Objects", self._selected_scope_radio)
        form_layout.addRow("", self._all_visible_scope_radio)
        form_layout.addRow("Layout", self._uniform_layout_radio)
        form_layout.addRow("", self._natural_layout_radio)
        form_layout.addRow("", self._mosaic_layout_radio)
        form_layout.addRow("Margin", self._margin_input)
        form_layout.addRow("Labels", self._labels_checkbox)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_button is not None:
            ok_button.setText("Export...")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._ok_button = ok_button

        layout = QVBoxLayout()
        layout.addLayout(form_layout)
        layout.addWidget(self._summary_label)
        layout.addWidget(buttons)
        self.setLayout(layout)
        self._update_summary()

    def selected_scope(self) -> SkyExplorerCollageScope:
        if self._all_visible_scope_radio.isChecked():
            return "all_eligible_visible"
        return "selected"

    def selected_layout(self) -> SkyExplorerCollageLayout:
        if self._mosaic_layout_radio.isChecked():
            return "mosaic"
        if self._natural_layout_radio.isChecked():
            return "natural"
        return "uniform"

    def selected_options(self) -> SkyExplorerCollageOptions:
        return SkyExplorerCollageOptions(
            layout=self.selected_layout(),
            margin_fraction=max(0.0, float(self._margin_input.value()) / 100.0),
            show_labels=self._labels_checkbox.isChecked(),
            columns=None,
        )

    def _current_counts(self) -> tuple[int, int, int]:
        if self.selected_scope() == "all_eligible_visible":
            return self._visible_count, self._visible_eligible_count, self._visible_skipped_count
        return self._selected_count, self._selected_eligible_count, self._selected_skipped_count

    def _update_summary(self) -> None:
        candidate_count, eligible_count, skipped_count = self._current_counts()
        columns = automatic_collage_column_count(eligible_count) if eligible_count else 0
        rows = int((eligible_count + columns - 1) / columns) if eligible_count and columns else 0
        summary = format_sky_explorer_collage_summary(
            candidate_count=candidate_count,
            eligible_count=eligible_count,
            skipped_missing_size_count=skipped_count,
            included_count=eligible_count,
            columns=columns or None,
            rows=rows or None,
            layout=self.selected_layout(),
            margin_fraction=max(0.0, float(self._margin_input.value()) / 100.0),
        )
        if eligible_count <= 0:
            summary += "\n\nNo eligible objects with catalog size metadata are available for this scope."
        elif self.selected_layout() == "mosaic":
            summary += (
                "\n\nLargest catalog footprints lead the pack. Crops fill interlocking tiles "
                "with themed gutters and no black letterboxing."
            )
        else:
            summary += "\n\nColumn count is chosen automatically from the number of included objects."
        self._summary_label.setText(summary)
        if self._ok_button is not None:
            self._ok_button.setEnabled(eligible_count > 0)


__all__ = ["SkyExplorerCollageDialog", "DEFAULT_COLLAGE_MARGIN_FRACTION"]
