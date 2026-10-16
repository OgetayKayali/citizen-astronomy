"""HR Diagram plot filter dialog."""

from __future__ import annotations

import math

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
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


class HrDiagramFilterDialog(QDialog):
    def __init__(self, diagram_filter: HrDiagramFilter, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("HR Diagram Filter")
        self.resize(560, 680)
        self._syncing = False
        active = diagram_filter.normalized()

        description = QLabel(
            "Limit which stars stay on the HR diagram, in Source Results, and on the motion overlays. "
            "A checked section removes stars outside its range and stars that do not have that measurement. "
            "Apply writes the ranges and the removal counts to the Work Log."
        )
        description.setWordWrap(True)

        self._color_group = self._build_color_group(active)
        self._distance_group = self._build_distance_group(active)
        self._luminosity_group = self._build_luminosity_group(active)
        self._stellar_group = self._build_stellar_group(active)

        content = QWidget()
        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(10)
        content_layout.addWidget(description)
        content_layout.addWidget(self._color_group)
        content_layout.addWidget(self._distance_group)
        content_layout.addWidget(self._luminosity_group)
        content_layout.addWidget(self._stellar_group)
        content_layout.addStretch(1)
        content.setLayout(content_layout)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(content)

        clear_button = QPushButton("Clear")
        clear_button.setToolTip("Turn off every filter section. Apply to update the diagram and the Work Log.")
        clear_button.clicked.connect(self._clear_sections)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        apply_button = QPushButton("Apply")
        apply_button.setDefault(True)
        apply_button.clicked.connect(self._apply)
        button_row = QHBoxLayout()
        button_row.addWidget(clear_button)
        button_row.addStretch(1)
        button_row.addWidget(cancel_button)
        button_row.addWidget(apply_button)

        layout = QVBoxLayout()
        layout.addWidget(scroll, stretch=1)
        layout.addLayout(button_row)
        self.setLayout(layout)
        self._connect_color_spins()
        self._update_luminosity_label()

    def filter_settings(self) -> HrDiagramFilter:
        spectral_types = frozenset(
            name for name, box in self._spectral_boxes.items() if box.isChecked()
        )
        luminosity_classes = frozenset(
            name for name, box in self._luminosity_class_boxes.items() if box.isChecked()
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
        self._color_group.setChecked(False)
        self._distance_group.setChecked(False)
        self._luminosity_group.setChecked(False)
        self._stellar_group.setChecked(False)

    def _any_stellar_class_checked(self) -> bool:
        return any(box.isChecked() for box in self._spectral_boxes.values()) or any(
            box.isChecked() for box in self._luminosity_class_boxes.values()
        )

    def _build_color_group(self, active: HrDiagramFilter) -> QGroupBox:
        group = QGroupBox("Color / temperature")
        group.setCheckable(True)
        group.setChecked(active.color_enabled)
        group.setToolTip("Uses Gaia BP-RP. Stars with no Gaia BP-RP are removed.")

        self._hot_temperature_spin = self._temperature_spin(active.temperature_max_k)
        self._cool_temperature_spin = self._temperature_spin(active.temperature_min_k)
        self._hot_color_spin = self._color_spin(temperature_kelvin_to_color_index(active.temperature_max_k))
        self._cool_color_spin = self._color_spin(temperature_kelvin_to_color_index(active.temperature_min_k))

        form = QGridLayout()
        form.addWidget(QLabel("Hot limit"), 0, 0)
        form.addWidget(self._hot_temperature_spin, 0, 1)
        form.addWidget(QLabel("BP-RP"), 0, 2)
        form.addWidget(self._hot_color_spin, 0, 3)
        form.addWidget(QLabel("Cool limit"), 1, 0)
        form.addWidget(self._cool_temperature_spin, 1, 1)
        form.addWidget(QLabel("BP-RP"), 1, 2)
        form.addWidget(self._cool_color_spin, 1, 3)
        note = QLabel("Gaia BP-RP only. Hotter stars have the smaller BP-RP. Stars with no Gaia BP-RP are removed.")
        note.setWordWrap(True)
        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(note)
        group.setLayout(layout)
        return group

    def _build_distance_group(self, active: HrDiagramFilter) -> QGroupBox:
        group = QGroupBox("Distance")
        group.setCheckable(True)
        group.setChecked(active.distance_enabled)
        group.setToolTip("Parsecs from Gaia parallax. Missing or non-positive parallax is removed.")

        self._distance_min_spin = self._distance_spin(active.distance_min_pc)
        self._distance_max_spin = self._distance_spin(active.distance_max_pc)
        row = QHBoxLayout()
        row.addWidget(QLabel("From"))
        row.addWidget(self._distance_min_spin)
        row.addWidget(QLabel("to"))
        row.addWidget(self._distance_max_spin)
        row.addStretch(1)
        note = QLabel(
            "Parsecs = 1000 / Gaia parallax in mas. Missing or non-positive parallax is removed."
        )
        note.setWordWrap(True)
        layout = QVBoxLayout()
        layout.addLayout(row)
        layout.addWidget(note)
        group.setLayout(layout)
        return group

    def _build_luminosity_group(self, active: HrDiagramFilter) -> QGroupBox:
        group = QGroupBox("Luminosity")
        group.setCheckable(True)
        group.setChecked(active.luminosity_enabled)
        group.setToolTip("Solar luminosities from Gaia absolute G. Stars with no absolute G are removed.")

        self._luminosity_min_spin = self._log_luminosity_spin(active.luminosity_min_solar)
        self._luminosity_max_spin = self._log_luminosity_spin(active.luminosity_max_solar)
        self._luminosity_min_spin.valueChanged.connect(self._update_luminosity_label)
        self._luminosity_max_spin.valueChanged.connect(self._update_luminosity_label)
        row = QHBoxLayout()
        row.addWidget(QLabel("log10 L/Lsun"))
        row.addWidget(self._luminosity_min_spin)
        row.addWidget(QLabel("to"))
        row.addWidget(self._luminosity_max_spin)
        row.addStretch(1)
        self._luminosity_label = QLabel()
        self._luminosity_label.setWordWrap(True)
        note = QLabel("From Gaia absolute G. Stars with no Gaia absolute G magnitude are removed.")
        note.setWordWrap(True)
        layout = QVBoxLayout()
        layout.addLayout(row)
        layout.addWidget(self._luminosity_label)
        layout.addWidget(note)
        group.setLayout(layout)
        return group

    def _build_stellar_group(self, active: HrDiagramFilter) -> QGroupBox:
        group = QGroupBox("Stellar class")
        group.setCheckable(True)
        group.setChecked(active.stellar_class_enabled)
        group.setToolTip(
            "Spectral type from Gaia BP-RP temperature, and luminosity class from the class guides. "
            "Stars that cannot be classified are removed."
        )

        self._spectral_boxes: dict[str, QCheckBox] = {}
        spectral_row = QHBoxLayout()
        for name in SPECTRAL_TYPE_ORDER:
            box = QCheckBox(name)
            box.setChecked(name in active.spectral_types)
            self._spectral_boxes[name] = box
            spectral_row.addWidget(box)
        spectral_row.addStretch(1)

        self._luminosity_class_boxes: dict[str, QCheckBox] = {}
        class_grid = QGridLayout()
        for index, name in enumerate(LUMINOSITY_CLASS_ORDER):
            box = QCheckBox(name)
            box.setChecked(name in active.luminosity_classes)
            self._luminosity_class_boxes[name] = box
            class_grid.addWidget(box, index // 2, index % 2)

        note = QLabel(
            "Spectral type uses the Gaia BP-RP temperature. "
            "Luminosity class is the nearest class guide within 1.75 mag. "
            "Stars that cannot be classified are removed. "
            "A star has to match a checked spectral type and a checked luminosity class. "
            "Clear every box in one group to skip that group."
        )
        note.setWordWrap(True)
        layout = QVBoxLayout()
        layout.addWidget(QLabel("Spectral type"))
        layout.addLayout(spectral_row)
        layout.addWidget(QLabel("Luminosity class"))
        layout.addLayout(class_grid)
        layout.addWidget(note)
        group.setLayout(layout)
        return group

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

    def _update_luminosity_label(self) -> None:
        minimum = float(10.0 ** self._luminosity_min_spin.value())
        maximum = float(10.0 ** self._luminosity_max_spin.value())
        if minimum > maximum:
            minimum, maximum = maximum, minimum
        self._luminosity_label.setText(
            f"About {format_solar_luminosity(minimum)} to {format_solar_luminosity(maximum)} Lsun."
        )

    def _temperature_spin(self, value: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(TEMPERATURE_MIN_K, TEMPERATURE_MAX_K)
        spin.setDecimals(0)
        spin.setSingleStep(100.0)
        spin.setSuffix(" K")
        spin.setValue(value)
        spin.setKeyboardTracking(False)
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
        return spin

    def _log_luminosity_spin(self, luminosity: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(math.log10(LUMINOSITY_MIN_SOLAR), math.log10(LUMINOSITY_MAX_SOLAR))
        spin.setDecimals(2)
        spin.setSingleStep(0.1)
        spin.setValue(math.log10(max(LUMINOSITY_MIN_SOLAR, luminosity)))
        return spin
