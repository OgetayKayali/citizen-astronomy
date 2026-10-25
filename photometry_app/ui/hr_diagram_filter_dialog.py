"""HR Diagram plot filter dialog.

Each limit is a switch. The section stays closed, and does not remove stars,
until that switch is On.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from photometry_app.core.hr_diagram_filter import (
    DISTANCE_MAX_PC,
    DISTANCE_MIN_PC,
    LUMINOSITY_CLASS_ORDER,
    LUMINOSITY_MAX_SOLAR,
    LUMINOSITY_MIN_SOLAR,
    SPECTRAL_TYPE_ORDER,
    TEMPERATURE_MAX_K,
    TEMPERATURE_MIN_K,
    HrDiagramFilter,
    format_solar_luminosity,
    temperature_kelvin_to_color_index,
    color_index_to_temperature_kelvin,
)


def _repolish(widget: QWidget) -> None:
    style = widget.style()
    if style is None:
        return
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def _mix(base: QColor, tint: QColor, amount: float) -> QColor:
    keep = 1.0 - amount
    return QColor(
        int(base.red() * keep + tint.red() * amount),
        int(base.green() * keep + tint.green() * amount),
        int(base.blue() * keep + tint.blue() * amount),
    )


def _join_names(names: list[str]) -> str:
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + f", and {names[-1]}"


def _format_temperature_range(minimum: float, maximum: float) -> str:
    if minimum > maximum:
        minimum, maximum = maximum, minimum
    return f"{int(round(minimum)):,}\u2013{int(round(maximum)):,} K"


def _format_distance(value: float) -> str:
    if value >= 100.0:
        return f"{value:,.0f}"
    return f"{value:,.1f}"


def _format_distance_range(minimum: float, maximum: float) -> str:
    if minimum > maximum:
        minimum, maximum = maximum, minimum
    return f"{_format_distance(minimum)}\u2013{_format_distance(maximum)} pc"


def _format_luminosity_range(minimum: float, maximum: float) -> str:
    if minimum > maximum:
        minimum, maximum = maximum, minimum
    return f"{format_solar_luminosity(minimum)}\u2013{format_solar_luminosity(maximum)} Lsun"


class _CardHeader(QWidget):
    """Title block. A click toggles the section, same as the switch."""

    def __init__(self, card: "_FilterCard") -> None:
        super().__init__(card)
        self._card = card
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._card.setChecked(not self._card.isChecked())
            event.accept()
            return
        super().mousePressEvent(event)


class _FilterCard(QFrame):
    toggled = Signal(bool)

    def __init__(self, title: str, checked: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("hrFilterCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._checked = False
        self._title_text = title

        self._title = QLabel(title)
        self._title.setObjectName("hrFilterCardTitle")
        self._title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._summary = QLabel()
        self._summary.setObjectName("hrFilterCardSummary")
        self._summary.setWordWrap(True)
        self._summary.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._summary.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(3)
        text.addWidget(self._title)
        text.addWidget(self._summary)
        self._header = _CardHeader(self)
        self._header.setLayout(text)

        self._switch = QPushButton("Off")
        self._switch.setObjectName("hrFilterSwitch")
        self._switch.setCheckable(True)
        self._switch.setAutoDefault(False)
        self._switch.setCursor(Qt.CursorShape.PointingHandCursor)
        self._switch.setFixedSize(96, 36)
        self._switch.setAccessibleName(f"{title} switch")
        self._switch.toggled.connect(self.setChecked)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(16)
        row.addWidget(self._header, 1)
        row.addWidget(self._switch, 0, Qt.AlignmentFlag.AlignVCenter)

        self._body = QWidget()
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 4, 0, 0)
        self._body_layout.setSpacing(8)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 13, 14, 13)
        layout.setSpacing(10)
        layout.addLayout(row)
        layout.addWidget(self._body)
        self.setChecked(checked)

    def isChecked(self) -> bool:  # noqa: N802
        return self._checked

    def setChecked(self, checked: bool) -> None:  # noqa: N802
        checked = bool(checked)
        changed = checked != self._checked
        self._checked = checked
        self._switch.blockSignals(True)
        self._switch.setChecked(checked)
        self._switch.setText("On" if checked else "Off")
        self._switch.setToolTip(f"Turn off {self._title_text}" if checked else f"Turn on {self._title_text}")
        self._switch.blockSignals(False)
        self._body.setVisible(checked)
        self.setProperty("active", checked)
        _repolish(self)
        _repolish(self._switch)
        if changed:
            self.toggled.emit(checked)

    def set_summary(self, text: str, *, warning: bool = False) -> None:
        self._summary.setText(text)
        self._summary.setProperty("warning", warning)
        _repolish(self._summary)

    def summary_text(self) -> str:
        return self._summary.text()

    def set_details(self, text: str) -> None:
        self._header.setToolTip(text)

    def body_layout(self) -> QVBoxLayout:
        return self._body_layout


class HrDiagramFilterDialog(QDialog):
    def __init__(self, diagram_filter: HrDiagramFilter, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("hrFilterDialog")
        self.setWindowTitle("HR Diagram Filter")
        self.setMinimumWidth(560)
        self._syncing = False
        active = diagram_filter.normalized()

        title = QLabel("Filter stars")
        title.setObjectName("hrFilterTitle")
        subtitle = QLabel("A section removes stars only while its switch says On.")
        subtitle.setObjectName("hrFilterSubtitle")
        subtitle.setWordWrap(True)
        heading = QVBoxLayout()
        heading.setContentsMargins(0, 0, 0, 0)
        heading.setSpacing(4)
        heading.addWidget(title)
        heading.addWidget(subtitle)

        self._badge = QLabel("All off")
        self._badge.setObjectName("hrFilterBadge")
        self._badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header = QHBoxLayout()
        header.setSpacing(16)
        header.addLayout(heading, 1)
        header.addWidget(self._badge, 0, Qt.AlignmentFlag.AlignTop)

        self._banner = QFrame()
        self._banner.setObjectName("hrFilterBanner")
        self._banner.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._banner_label = QLabel()
        self._banner_label.setObjectName("hrFilterBannerText")
        self._banner_label.setWordWrap(True)
        banner_layout = QVBoxLayout(self._banner)
        banner_layout.setContentsMargins(14, 10, 14, 10)
        banner_layout.addWidget(self._banner_label)

        self._color_group = self._build_color_group(active)
        self._distance_group = self._build_distance_group(active)
        self._luminosity_group = self._build_luminosity_group(active)
        self._stellar_group = self._build_stellar_group(active)

        content = QWidget()
        content.setObjectName("hrFilterContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(1, 1, 8, 1)
        content_layout.setSpacing(10)
        content_layout.addWidget(self._color_group)
        content_layout.addWidget(self._distance_group)
        content_layout.addWidget(self._luminosity_group)
        content_layout.addWidget(self._stellar_group)
        content_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setObjectName("hrFilterScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        self._scroll = scroll
        self._content = content

        self._clear_button = QPushButton("Turn all off")
        self._clear_button.setObjectName("hrFilterSecondary")
        self._clear_button.setAutoDefault(False)
        self._clear_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear_button.setToolTip("Turn every section off. Apply to update the diagram and the Work Log.")
        self._clear_button.clicked.connect(self._clear_sections)
        cancel_button = QPushButton("Cancel")
        cancel_button.setObjectName("hrFilterSecondary")
        cancel_button.setAutoDefault(False)
        cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_button.clicked.connect(self.reject)
        apply_button = QPushButton("Apply")
        apply_button.setObjectName("hrFilterPrimary")
        apply_button.setCursor(Qt.CursorShape.PointingHandCursor)
        apply_button.setDefault(True)
        apply_button.setAutoDefault(True)
        apply_button.setToolTip("Update the diagram, Source Results, motion overlays, and the Work Log.")
        apply_button.clicked.connect(self._apply)
        button_row = QHBoxLayout()
        button_row.setSpacing(8)
        button_row.addWidget(self._clear_button)
        button_row.addStretch(1)
        button_row.addWidget(cancel_button)
        button_row.addWidget(apply_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(14)
        layout.addLayout(header)
        layout.addWidget(self._banner)
        layout.addWidget(scroll, stretch=1)
        layout.addLayout(button_row)

        for card in self._cards():
            card.toggled.connect(self._on_card_toggled)
        self._connect_color_spins()
        self._hot_temperature_spin.valueChanged.connect(self._refresh_state)
        self._cool_temperature_spin.valueChanged.connect(self._refresh_state)
        self._distance_min_spin.valueChanged.connect(self._refresh_state)
        self._distance_max_spin.valueChanged.connect(self._refresh_state)
        self._luminosity_min_spin.valueChanged.connect(self._refresh_state)
        self._luminosity_max_spin.valueChanged.connect(self._refresh_state)
        self._apply_style()
        self._refresh_state()
        self._fit_initial_size()

    def _cards(self) -> tuple[_FilterCard, ...]:
        return (self._color_group, self._distance_group, self._luminosity_group, self._stellar_group)

    def _on_card_toggled(self, checked: bool) -> None:
        self._refresh_state()
        if not checked or not self.isVisible():
            return
        card = self.sender()
        if isinstance(card, QWidget):
            QTimer.singleShot(0, lambda card=card: self._reveal(card))

    def _reveal(self, card: QWidget) -> None:
        if not self.isVisible():
            return
        self._grow_to_fit()
        self._scroll.ensureWidgetVisible(card, 0, 28)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        QTimer.singleShot(0, self._grow_to_fit)

    def _fit_initial_size(self) -> None:
        content_height = self._content.sizeHint().height()
        open_sections = sum(card.isChecked() for card in self._cards())
        slack = 248 + (open_sections * 48)
        self.resize(620, min(820, max(540, content_height + slack)))

    def _content_used_height(self) -> int:
        layout = self._content.layout()
        margins = layout.contentsMargins()
        cards = self._cards()
        total = sum(card.height() for card in cards)
        total += layout.spacing() * max(0, len(cards) - 1)
        return total + margins.top() + margins.bottom()

    def _grow_to_fit(self) -> None:
        if not self.isVisible():
            return
        self.layout().activate()
        needed = self._content_used_height() + 16
        viewport = self._scroll.viewport().height()
        if needed <= viewport + 2:
            return
        target = min(820, self.height() + (needed - viewport))
        if target > self.height() + 4:
            self.resize(self.width(), target)

    def filter_settings(self) -> HrDiagramFilter:
        spectral_types = frozenset(
            name for name, box in self._spectral_boxes.items() if box.isChecked() and self._spectral_enabled.isChecked()
        )
        luminosity_classes = frozenset(
            name for name, box in self._luminosity_class_boxes.items() if box.isChecked() and self._luminosity_class_enabled.isChecked()
        )
        return HrDiagramFilter(
            color_enabled=self._color_group.isChecked(),
            temperature_min_k=float(self._cool_temperature_spin.value()),
            temperature_max_k=float(self._hot_temperature_spin.value()),
            distance_enabled=self._distance_group.isChecked(),
            distance_min_pc=float(self._distance_min_spin.value()),
            distance_max_pc=float(self._distance_max_spin.value()),
            luminosity_enabled=self._luminosity_group.isChecked(),
            luminosity_min_solar=float(10.0 ** self._luminosity_min_spin.value()),
            luminosity_max_solar=float(10.0 ** self._luminosity_max_spin.value()),
            stellar_class_enabled=self._stellar_group.isChecked(),
            spectral_types=spectral_types,
            luminosity_classes=luminosity_classes,
            allow_estimated_classes=self._estimated_classes.isChecked(),
        ).normalized()

    def _apply(self) -> None:
        if self._stellar_group.isChecked() and not self._any_stellar_class_checked():
            QMessageBox.information(
                self,
                "Choose a stellar class",
                "Choose at least one spectral type or luminosity class, or turn Stellar class off.",
            )
            return
        self.accept()

    def _clear_sections(self) -> None:
        for card in self._cards():
            card.setChecked(False)
        self._refresh_state()

    def _any_stellar_class_checked(self) -> bool:
        return (
            self._spectral_enabled.isChecked() and any(box.isChecked() for box in self._spectral_boxes.values())
        ) or (
            self._luminosity_class_enabled.isChecked() and any(box.isChecked() for box in self._luminosity_class_boxes.values())
        )

    def _build_color_group(self, active: HrDiagramFilter) -> _FilterCard:
        card = _FilterCard("Color and temperature", active.color_enabled, self)
        card.set_details(
            "Uses Gaia BP\u2212RP and the same temperature scale as the plot. Hotter stars have the smaller BP\u2212RP."
        )
        self._hot_temperature_spin = self._temperature_spin(active.temperature_max_k)
        self._cool_temperature_spin = self._temperature_spin(active.temperature_min_k)
        self._hot_color_spin = self._color_spin(temperature_kelvin_to_color_index(active.temperature_max_k))
        self._cool_color_spin = self._color_spin(temperature_kelvin_to_color_index(active.temperature_min_k))
        self._hot_temperature_spin.setToolTip("Hotter limit, in kelvin.")
        self._cool_temperature_spin.setToolTip("Cooler limit, in kelvin.")
        self._hot_color_spin.setToolTip("Gaia BP\u2212RP at the hotter limit. Smaller numbers are hotter.")
        self._cool_color_spin.setToolTip("Gaia BP\u2212RP at the cooler limit. Smaller numbers are hotter.")

        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
        form.addWidget(self._field_label("Hotter"), 0, 0)
        form.addWidget(self._hot_temperature_spin, 0, 1)
        form.addWidget(self._field_label("BP\u2212RP"), 0, 2)
        form.addWidget(self._hot_color_spin, 0, 3)
        form.addWidget(self._field_label("Cooler"), 1, 0)
        form.addWidget(self._cool_temperature_spin, 1, 1)
        form.addWidget(self._field_label("BP\u2212RP"), 1, 2)
        form.addWidget(self._cool_color_spin, 1, 3)
        form.setColumnStretch(1, 1)
        form.setColumnStretch(3, 1)
        hint = QLabel("Smaller BP\u2212RP is hotter.")
        hint.setProperty("muted", True)
        card.body_layout().addLayout(form)
        card.body_layout().addWidget(hint)
        return card

    def _build_distance_group(self, active: HrDiagramFilter) -> _FilterCard:
        card = _FilterCard("Distance", active.distance_enabled, self)
        card.set_details(
            "Parsecs = 1000 / Gaia parallax in milliarcseconds. Missing or non-positive parallax is removed when this section is On."
        )
        self._distance_min_spin = self._distance_spin(active.distance_min_pc)
        self._distance_max_spin = self._distance_spin(active.distance_max_pc)
        self._distance_min_spin.setToolTip("Nearest distance to keep.")
        self._distance_max_spin.setToolTip("Farthest distance to keep.")
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(self._field_label("From"))
        row.addWidget(self._distance_min_spin, 1)
        row.addWidget(self._field_label("to"))
        row.addWidget(self._distance_max_spin, 1)
        card.body_layout().addLayout(row)
        return card

    def _build_luminosity_group(self, active: HrDiagramFilter) -> _FilterCard:
        card = _FilterCard("Luminosity", active.luminosity_enabled, self)
        card.set_details("Solar luminosity from Gaia absolute G. The Sun is absolute G = 4.67.")
        self._luminosity_min_spin = self._log_luminosity_spin(active.luminosity_min_solar)
        self._luminosity_max_spin = self._log_luminosity_spin(active.luminosity_max_solar)
        self._luminosity_min_spin.setToolTip("Fainter limit, as log10 of solar luminosity.")
        self._luminosity_max_spin.setToolTip("Brighter limit, as log10 of solar luminosity.")
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(self._field_label("log10 L/Lsun"))
        row.addWidget(self._luminosity_min_spin, 1)
        row.addWidget(self._field_label("to"))
        row.addWidget(self._luminosity_max_spin, 1)
        card.body_layout().addLayout(row)
        return card

    def _build_stellar_group(self, active: HrDiagramFilter) -> _FilterCard:
        card = _FilterCard("Stellar class", active.stellar_class_enabled, self)
        card.set_details(
            "Published SIMBAD types are used first. Unknown or unsupported types are excluded only "
            "by enabled criteria. Broad OB classifications require both O and B selected. "
            "Generate the diagram to retrieve literature types."
        )
        self._spectral_enabled = QCheckBox("Filter spectral type")
        self._spectral_enabled.setChecked(bool(active.spectral_types))
        self._luminosity_class_enabled = QCheckBox("Filter luminosity class")
        self._luminosity_class_enabled.setChecked(bool(active.luminosity_classes))
        self._estimated_classes = QCheckBox("Allow estimated fallback for stars without literature types")
        self._estimated_classes.setChecked(active.allow_estimated_classes)
        self._estimated_classes.setToolTip(
            "Estimates use uncorrected Gaia BP-RP and diagram guides. They are not literature classifications. "
            "Ambiguous or unsupported published types are never replaced by estimates."
        )
        self._spectral_boxes = {}
        spectral_row = QHBoxLayout()
        spectral_row.setSpacing(6)
        for name in SPECTRAL_TYPE_ORDER:
            chip = self._chip(name)
            chip.setChecked(not active.spectral_types or name in active.spectral_types)
            chip.setToolTip(f"Spectral type {name}")
            chip.toggled.connect(self._refresh_state)
            self._spectral_boxes[name] = chip
            spectral_row.addWidget(chip, 1)
        self._luminosity_class_boxes = {}
        class_chips: list[QPushButton] = []
        for name in LUMINOSITY_CLASS_ORDER:
            chip = self._chip(name, expanding=False)
            chip.setChecked(not active.luminosity_classes or name in active.luminosity_classes)
            chip.setToolTip(name)
            chip.toggled.connect(self._refresh_state)
            self._luminosity_class_boxes[name] = chip
            class_chips.append(chip)

        layout = card.body_layout()
        layout.addWidget(self._spectral_enabled)
        layout.addLayout(self._chip_heading("Spectral type", self._spectral_boxes))
        layout.addLayout(spectral_row)
        layout.addWidget(self._luminosity_class_enabled)
        layout.addLayout(self._chip_heading("Luminosity class", self._luminosity_class_boxes))
        layout.addLayout(self._chip_line(class_chips[:3]))
        layout.addLayout(self._chip_line(class_chips[3:]))
        layout.addWidget(self._estimated_classes)
        for checkbox in (self._spectral_enabled, self._luminosity_class_enabled, self._estimated_classes):
            checkbox.toggled.connect(self._refresh_state)
        return card

    def _chip_line(self, chips: list[QPushButton]) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        for chip in chips:
            row.addWidget(chip)
        row.addStretch(1)
        return row

    def _chip_heading(self, text: str, boxes: dict[str, QPushButton]) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(4)
        row.addWidget(self._field_label(text))
        row.addStretch(1)
        choose_all = self._link_button("All")
        choose_all.setToolTip(f"Select every {text.lower()}")
        choose_all.clicked.connect(lambda: self._set_chips(boxes, True))
        choose_none = self._link_button("None")
        choose_none.setToolTip(f"Ignore {text.lower()}. Stars are not limited by this row.")
        choose_none.clicked.connect(lambda: self._set_chips(boxes, False))
        row.addWidget(choose_all)
        row.addWidget(choose_none)
        return row

    def _set_chips(self, boxes: dict[str, QPushButton], checked: bool) -> None:
        for box in boxes.values():
            box.blockSignals(True)
            box.setChecked(checked)
            box.blockSignals(False)
        self._refresh_state()

    def _field_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("hrFilterFieldLabel")
        return label

    def _link_button(self, text: str) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("hrFilterLink")
        button.setAutoDefault(False)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    def _chip(self, text: str, *, expanding: bool = True) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("hrFilterChip")
        button.setCheckable(True)
        button.setAutoDefault(False)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setMinimumHeight(34)
        policy = QSizePolicy.Policy.Expanding if expanding else QSizePolicy.Policy.Minimum
        button.setSizePolicy(policy, QSizePolicy.Policy.Fixed)
        return button

    def _refresh_state(self, *_args: object) -> None:
        self._color_group.set_summary(self._color_summary())
        self._distance_group.set_summary(self._distance_summary())
        self._luminosity_group.set_summary(self._luminosity_summary())
        stellar_text, stellar_warning = self._stellar_summary()
        self._stellar_group.set_summary(stellar_text, warning=stellar_warning)

        names = self._active_names()
        count = len(names)
        self._badge.setText("All off" if count == 0 else f"{count} on")
        self._badge.setProperty("active", count > 0)
        self._banner.setProperty("tone", "active" if count else "idle")
        if count == 0:
            banner = "All switches are Off. Apply will not remove stars."
        elif count == 1:
            banner = f"{names[0]} is On. Apply updates the diagram, Source Results, and the Work Log."
        else:
            banner = (
                f"{_join_names(names)} are On. Apply updates the diagram, Source Results, and the Work Log."
            )
        self._banner_label.setText(banner)
        self._clear_button.setEnabled(count > 0)
        _repolish(self._badge)
        _repolish(self._banner)

    def _active_names(self) -> list[str]:
        names: list[str] = []
        if self._color_group.isChecked():
            names.append("Color")
        if self._distance_group.isChecked():
            names.append("Distance")
        if self._luminosity_group.isChecked():
            names.append("Luminosity")
        if self._stellar_group.isChecked():
            names.append("Stellar class")
        return names

    def _color_summary(self) -> str:
        cool = float(self._cool_temperature_spin.value())
        hot = float(self._hot_temperature_spin.value())
        full = min(cool, hot) <= TEMPERATURE_MIN_K + 0.5 and max(cool, hot) >= TEMPERATURE_MAX_K - 0.5
        if not self._color_group.isChecked():
            detail = "every temperature" if full else _format_temperature_range(cool, hot)
            return f"Not applied \u00b7 {detail}"
        if full:
            return "Keeping every temperature. Stars with no Gaia BP\u2212RP are removed."
        return f"Keeping {_format_temperature_range(cool, hot)}. Stars with no Gaia BP\u2212RP are removed."

    def _distance_summary(self) -> str:
        text = _format_distance_range(
            float(self._distance_min_spin.value()),
            float(self._distance_max_spin.value()),
        )
        if not self._distance_group.isChecked():
            return f"Not applied \u00b7 {text}"
        return f"Keeping {text}. Stars with no parallax are removed."

    def _luminosity_summary(self) -> str:
        faint = float(10.0 ** self._luminosity_min_spin.value())
        bright = float(10.0 ** self._luminosity_max_spin.value())
        full = min(faint, bright) <= LUMINOSITY_MIN_SOLAR * 1.05 and max(faint, bright) >= LUMINOSITY_MAX_SOLAR * 0.95
        if not self._luminosity_group.isChecked():
            detail = "every luminosity" if full else _format_luminosity_range(faint, bright)
            return f"Not applied \u00b7 {detail}"
        if full:
            return "Keeping every luminosity. Stars with no absolute G are removed."
        return f"Keeping {_format_luminosity_range(faint, bright)}. Stars with no absolute G are removed."

    def _stellar_summary(self) -> tuple[str, bool]:
        for box in self._spectral_boxes.values():
            box.setEnabled(self._spectral_enabled.isChecked())
        for box in self._luminosity_class_boxes.values():
            box.setEnabled(self._luminosity_class_enabled.isChecked())
        spectral = [name for name in SPECTRAL_TYPE_ORDER if self._spectral_enabled.isChecked() and self._spectral_boxes[name].isChecked()]
        classes = [name for name in LUMINOSITY_CLASS_ORDER if self._luminosity_class_enabled.isChecked() and self._luminosity_class_boxes[name].isChecked()]
        spectral_all = len(spectral) == len(SPECTRAL_TYPE_ORDER)
        class_all = len(classes) == len(LUMINOSITY_CLASS_ORDER)
        spectral_text = "all types" if spectral_all else ("any type" if not spectral else _join_names(spectral))
        class_text = "all classes" if class_all else ("any class" if not classes else _join_names(classes))
        if not self._stellar_group.isChecked():
            return f"Not applied \u00b7 {spectral_text}, {class_text}", False
        if not spectral and not classes:
            return "Choose at least one spectral type or luminosity class.", True
        source = "Literature + estimated fallback. " if self._estimated_classes.isChecked() else "Literature only. "
        if spectral and classes:
            if spectral_all and class_all:
                return source + "Keeping every supported type and class. Unknown stars are removed.", False
            return (
                source + f"Keeping {spectral_text} on {class_text}. A star must match both. Unknown stars are removed.",
                False,
            )
        if spectral:
            return (
                source + f"Keeping {spectral_text}. Luminosity class is not limited. Unknown types are removed.",
                False,
            )
        return (
            source + f"Keeping {class_text}. Spectral type is not limited. Unknown classes are removed.",
            False,
        )

    def _connect_color_spins(self) -> None:
        self._hot_temperature_spin.valueChanged.connect(self._sync_hot_temperature)
        self._cool_temperature_spin.valueChanged.connect(self._sync_cool_temperature)
        self._hot_color_spin.valueChanged.connect(self._sync_hot_color)
        self._cool_color_spin.valueChanged.connect(self._sync_cool_color)

    def _sync_hot_temperature(self) -> None:
        self._set_color_from_temperature(self._hot_temperature_spin, self._hot_color_spin)

    def _sync_cool_temperature(self) -> None:
        self._set_color_from_temperature(self._cool_temperature_spin, self._cool_color_spin)

    def _sync_hot_color(self) -> None:
        self._set_temperature_from_color(self._hot_color_spin, self._hot_temperature_spin)

    def _sync_cool_color(self) -> None:
        self._set_temperature_from_color(self._cool_color_spin, self._cool_temperature_spin)

    def _set_color_from_temperature(self, temperature_spin: QDoubleSpinBox, color_spin: QDoubleSpinBox) -> None:
        if self._syncing:
            return
        self._syncing = True
        color = temperature_kelvin_to_color_index(float(temperature_spin.value()))
        if abs(color_spin.value() - color) > 0.005:
            color_spin.setValue(color)
        self._syncing = False

    def _set_temperature_from_color(self, color_spin: QDoubleSpinBox, temperature_spin: QDoubleSpinBox) -> None:
        if self._syncing:
            return
        self._syncing = True
        temperature = color_index_to_temperature_kelvin(float(color_spin.value()))
        if abs(temperature_spin.value() - temperature) > 0.5:
            temperature_spin.setValue(temperature)
        self._syncing = False

    def _temperature_spin(self, value: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(TEMPERATURE_MIN_K, TEMPERATURE_MAX_K)
        spin.setDecimals(0)
        spin.setSingleStep(100.0)
        spin.setSuffix(" K")
        spin.setValue(value)
        spin.setKeyboardTracking(False)
        spin.setAccelerated(True)
        return spin

    def _color_spin(self, value: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(-0.40, 3.00)
        spin.setDecimals(2)
        spin.setSingleStep(0.05)
        spin.setValue(value)
        spin.setKeyboardTracking(False)
        return spin

    def _distance_spin(self, value: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(DISTANCE_MIN_PC, DISTANCE_MAX_PC)
        spin.setDecimals(1)
        spin.setSingleStep(10.0)
        spin.setSuffix(" pc")
        spin.setValue(value)
        spin.setKeyboardTracking(False)
        spin.setAccelerated(True)
        return spin

    def _log_luminosity_spin(self, luminosity: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(math.log10(LUMINOSITY_MIN_SOLAR), math.log10(LUMINOSITY_MAX_SOLAR))
        spin.setDecimals(2)
        spin.setSingleStep(0.1)
        spin.setValue(math.log10(max(LUMINOSITY_MIN_SOLAR, luminosity)))
        spin.setKeyboardTracking(False)
        return spin

    def _apply_style(self) -> None:
        palette = self.palette()
        window = palette.color(QPalette.ColorRole.Window)
        text_color = palette.color(QPalette.ColorRole.WindowText)
        dark = window.lightness() < 128
        surface = window.lighter(116) if dark else window.darker(103)
        border = window.lighter(168) if dark else window.darker(118)
        base = palette.color(QPalette.ColorRole.Base)
        if not base.isValid():
            base = window
        accent = palette.color(QPalette.ColorRole.Highlight)
        if not accent.isValid() or accent == window:
            accent = QColor("#3d8bfd")
        contrast = palette.color(QPalette.ColorRole.HighlightedText)
        if not contrast.isValid() or contrast == accent:
            contrast = QColor("#1a1c22") if accent.lightness() > 150 else QColor("#f7f8fa")
        muted = text_color.darker(140) if dark else QColor("#5c6675")
        amber = QColor("#e0b15c")
        notice_bg = _mix(window, amber, 0.20 if dark else 0.34)
        notice_border = _mix(window, amber, 0.62 if dark else 0.72)
        warning = QColor("#f0d7a8") if dark else QColor("#8a5a12")
        active_surface = _mix(surface, accent, 0.18)
        active_banner = _mix(window, accent, 0.18 if dark else 0.12)
        accent_soft = accent.lighter(118) if dark else accent.darker(108)
        hover_surface = surface.lighter(112) if dark else surface.darker(104)
        switch_off_bg = surface.lighter(128) if dark else base
        switch_off_border = _mix(border, text_color, 0.50 if dark else 0.28)
        field_border = _mix(border, text_color, 0.28 if dark else 0.12)
        chip_bg = surface.lighter(110) if dark else base

        viewport = self._scroll.viewport()
        viewport_palette = viewport.palette()
        viewport_palette.setColor(QPalette.ColorRole.Window, window)
        viewport_palette.setColor(QPalette.ColorRole.Base, window)
        viewport.setPalette(viewport_palette)
        viewport.setAutoFillBackground(True)

        self.setStyleSheet(
            f"""
            QDialog#hrFilterDialog {{
                background: {window.name()};
                color: {text_color.name()};
            }}
            QDialog#hrFilterDialog QLabel {{
                background: transparent;
                color: {text_color.name()};
            }}
            QDialog#hrFilterDialog QLabel[muted="true"] {{
                color: {muted.name()};
                font-size: 12px;
            }}
            QLabel#hrFilterTitle {{
                font-size: 22px;
                font-weight: 600;
            }}
            QLabel#hrFilterSubtitle {{
                color: {muted.name()};
                font-size: 13px;
            }}
            QLabel#hrFilterBadge {{
                color: {muted.name()};
                background: {surface.name()};
                border: 1px solid {border.name()};
                border-radius: 12px;
                padding: 5px 12px;
                font-size: 12px;
                font-weight: 700;
            }}
            QLabel#hrFilterBadge[active="true"] {{
                color: {contrast.name()};
                background: {accent.name()};
                border-color: {accent.name()};
            }}
            QFrame#hrFilterBanner {{
                background: {notice_bg.name()};
                border: 1px solid {notice_border.name()};
                border-radius: 10px;
            }}
            QFrame#hrFilterBanner[tone="active"] {{
                background: {active_banner.name()};
                border-color: {accent.name()};
            }}
            QLabel#hrFilterBannerText {{
                font-size: 13px;
                color: {text_color.name()};
            }}
            QScrollArea#hrFilterScroll {{
                background: transparent;
                border: none;
            }}
            QWidget#hrFilterContent {{
                background: transparent;
            }}
            QFrame#hrFilterCard {{
                background: {surface.name()};
                border: 1px solid {border.name()};
                border-left: 3px solid {border.name()};
                border-radius: 12px;
            }}
            QFrame#hrFilterCard[active="true"] {{
                background: {active_surface.name()};
                border: 1px solid {accent.name()};
                border-left: 3px solid {accent.name()};
            }}
            QLabel#hrFilterCardTitle {{
                font-size: 15px;
                font-weight: 600;
            }}
            QLabel#hrFilterCardSummary {{
                font-size: 12px;
                color: {text_color.name()};
            }}
            QLabel#hrFilterCardSummary[warning="true"] {{
                color: {warning.name()};
            }}
            QLabel#hrFilterFieldLabel {{
                color: {muted.name()};
                font-size: 12px;
                font-weight: 600;
            }}
            QDialog#hrFilterDialog QDoubleSpinBox {{
                background: {base.name()};
                color: {text_color.name()};
                border: 1px solid {field_border.name()};
                border-radius: 8px;
                padding: 4px 8px;
                min-height: 28px;
                selection-background-color: {accent.name()};
                selection-color: {contrast.name()};
            }}
            QDialog#hrFilterDialog QDoubleSpinBox:focus {{
                border: 1px solid {accent.name()};
            }}
            QDialog#hrFilterDialog QPushButton#hrFilterSwitch {{
                background: {switch_off_bg.name()};
                color: {text_color.name()};
                border: 1px solid {switch_off_border.name()};
                border-radius: 18px;
                font-size: 14px;
                font-weight: 700;
                padding: 0px;
            }}
            QDialog#hrFilterDialog QPushButton#hrFilterSwitch:hover {{
                border-color: {accent.name()};
                color: {text_color.name()};
            }}
            QDialog#hrFilterDialog QPushButton#hrFilterSwitch:checked {{
                background: {accent.name()};
                color: {contrast.name()};
                border-color: {accent.name()};
            }}
            QDialog#hrFilterDialog QPushButton#hrFilterSwitch:checked:hover {{
                background: {accent_soft.name()};
            }}
            QDialog#hrFilterDialog QPushButton#hrFilterChip {{
                background: {chip_bg.name()};
                color: {text_color.name()};
                border: 1px solid {field_border.name()};
                border-radius: 8px;
                padding: 6px 14px;
                font-weight: 600;
                min-height: 32px;
            }}
            QDialog#hrFilterDialog QPushButton#hrFilterChip:hover {{
                border-color: {accent.name()};
            }}
            QDialog#hrFilterDialog QPushButton#hrFilterChip:checked {{
                background: {accent.name()};
                color: {contrast.name()};
                border-color: {accent.name()};
            }}
            QDialog#hrFilterDialog QPushButton#hrFilterChip:disabled {{
                background: {surface.name()};
                color: {muted.name()};
                border-color: {border.name()};
            }}
            QDialog#hrFilterDialog QPushButton#hrFilterLink {{
                background: transparent;
                color: {accent.name()};
                border: none;
                border-radius: 6px;
                padding: 2px 8px;
                font-weight: 600;
                min-height: 22px;
            }}
            QDialog#hrFilterDialog QPushButton#hrFilterLink:hover {{
                background: {hover_surface.name()};
            }}
            QDialog#hrFilterDialog QPushButton#hrFilterSecondary {{
                background: transparent;
                color: {text_color.name()};
                border: 1px solid {border.name()};
                border-radius: 8px;
                padding: 8px 14px;
                min-height: 34px;
                font-weight: 600;
            }}
            QDialog#hrFilterDialog QPushButton#hrFilterSecondary:hover {{
                border-color: {accent.name()};
                background: {hover_surface.name()};
            }}
            QDialog#hrFilterDialog QPushButton#hrFilterSecondary:disabled {{
                color: {muted.name()};
                background: transparent;
                border-color: {border.name()};
            }}
            QDialog#hrFilterDialog QPushButton#hrFilterPrimary {{
                background: {accent.name()};
                color: {contrast.name()};
                border: 1px solid {accent.name()};
                border-radius: 8px;
                padding: 8px 18px;
                min-height: 34px;
                min-width: 88px;
                font-weight: 700;
            }}
            QDialog#hrFilterDialog QPushButton#hrFilterPrimary:hover {{
                background: {accent_soft.name()};
                border-color: {accent_soft.name()};
            }}
            """
        )
        for button in self.findChildren(QPushButton):
            if button.objectName() != "hrFilterPrimary":
                button.setAutoDefault(False)
                button.setDefault(False)
