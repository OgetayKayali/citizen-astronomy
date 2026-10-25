"""Instrument profiles and an inspectable standard-field calibration workflow."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import date
import json
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QSplitter, QTabWidget, QGroupBox, QProgressBar,
)

from photometry_app.core.transformation import (
    StandardObservation, apply_calibration_snr_limit, fit_profile, load_profile, read_standard_observations,
    reason_category, write_standard_observations,
)


class _SortableTableItem(QTableWidgetItem):
    def __lt__(self, other):
        if not isinstance(other, QTableWidgetItem):
            return True
        left = self.data(Qt.ItemDataRole.UserRole)
        right = other.data(Qt.ItemDataRole.UserRole)
        if left is not None and right is not None and type(left) is type(right):
            return left < right
        return self.text().casefold() < other.text().casefold()


class _CalibrationWorker(QThread):
    progress = Signal(str)
    result = Signal(object)
    failed = Signal(str)

    def __init__(self, job, parent):
        super().__init__(parent)
        self.job = job

    def run(self):
        try:
            self.result.emit(self.job(self.progress.emit, self.isInterruptionRequested))
        except Exception as exc:
            self.failed.emit(str(exc))


class TransformationDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = deepcopy(settings)
        self.rows: list[StandardObservation] = []
        self.candidate = None
        self.worker = None
        self.image_groups = {}
        self.dirty = False
        self.setWindowTitle("Photometric Transformation")
        self.setObjectName("transformationDialog")
        self.resize(960, 730)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 22)
        layout.setSpacing(16)
        heading = QHBoxLayout()
        title = QLabel("Color calibration")
        title.setObjectName("calibrationTitle")
        heading.addWidget(title)
        heading.addStretch()
        badge = QLabel("B / V")
        badge.setObjectName("calibrationBadge")
        heading.addWidget(badge)
        layout.addLayout(heading)
        intro = QLabel("Optional calibration to put your measurements on the standard B/V system.")
        intro.setProperty("muted", True)
        layout.addWidget(intro)
        self.controls = QTabWidget()
        self.controls.setObjectName("calibrationTabs")
        layout.addWidget(self.controls)
        setup = QWidget()
        self.controls.addTab(setup, "Setup")
        controls = QVBoxLayout(setup)
        controls.setContentsMargins(0, 18, 0, 0)
        controls.setSpacing(18)
        saved = QHBoxLayout()
        saved.addWidget(QLabel("Calibration"))
        self.profiles = QComboBox()
        saved.addWidget(self.profiles, 1)
        import_button = QPushButton("Import…")
        import_button.clicked.connect(self._import)
        saved.addWidget(import_button)
        controls.addLayout(saved)
        equipment_box = QGroupBox("Equipment")
        equipment_box.setProperty("card", True)
        controls.addWidget(equipment_box)
        equipment_form = QFormLayout(equipment_box)
        equipment_form.setContentsMargins(20, 28, 20, 18)
        equipment_form.setSpacing(12)
        self.filter_system = QComboBox()
        self.filter_system.addItem("RGB filters / channels", "rgb")
        self.filter_system.addItem("Photometric B + V", "bv")
        self.filter_system.addItem("Custom broadband pair", "custom")
        equipment_form.addRow("Filter type", self.filter_system)
        filter_info = QHBoxLayout()
        self.brand = QComboBox()
        self.brand.setEditable(True)
        self.brand.addItems(["Other / unknown", "Antlia", "Astronomik", "Astrodon", "Baader", "Chroma", "Optolong", "Player One", "QHY", "SVBONY", "ZWO"])
        self.brand.setToolTip("Choose or type a manufacturer. For a mixed set, enter both manufacturers and describe the channels in Model.")
        self.model = QLineEdit()
        self.model.setPlaceholderText("Model or series · optional")
        filter_info.addWidget(self.brand)
        filter_info.addWidget(self.model, 1)
        equipment_form.addRow("Filters", filter_info)
        instrument = QHBoxLayout()
        self.telescope = QLineEdit(settings.telescope)
        self.telescope.setPlaceholderText("Telescope")
        self.telescope.setToolTip("Filled from image headers when available. Calibration is specific to this equipment.")
        self.camera = QLineEdit(settings.camera)
        self.camera.setPlaceholderText("Camera")
        self.camera.setToolTip("Filled from image headers when available.")
        instrument.addWidget(self.telescope)
        instrument.addWidget(self.camera)
        equipment_form.addRow("Instrument", instrument)
        self.accessories = QLineEdit()
        self.accessories.setPlaceholderText("Reducer, IR-cut, camera mode or channel extraction (if used)")
        images_box = QGroupBox("Calibration images")
        images_box.setProperty("card", True)
        controls.addWidget(images_box)
        images_layout = QVBoxLayout(images_box)
        images_layout.setContentsMargins(20, 28, 20, 18)
        images_layout.setSpacing(12)
        image_intro = QHBoxLayout()
        instructions = QLabel("Blue + green or B + V images of a reference star field.")
        instructions.setProperty("muted", True)
        image_intro.addWidget(instructions, 1)
        self.help_button = QPushButton("What do I need?")
        self.help_button.setProperty("quiet", True)
        self.help_button.clicked.connect(self._show_image_help)
        image_intro.addWidget(self.help_button)
        images_layout.addLayout(image_intro)
        image_actions = QHBoxLayout()
        for label, handler in (("Choose images…", self._choose_images), ("Choose folder…", self._choose_folder)):
            button = QPushButton(label)
            button.clicked.connect(handler)
            image_actions.addWidget(button)
        image_actions.addStretch()
        images_layout.addLayout(image_actions)
        self.image_summary = QLabel("No images selected")
        self.image_summary.setProperty("muted", True)
        self.image_summary.setWordWrap(True)
        images_layout.addWidget(self.image_summary)
        self.mapping = QWidget()
        mapping_layout = QHBoxLayout(self.mapping)
        mapping_layout.setContentsMargins(0, 0, 0, 0)
        self.blue_choice, self.visual_choice = QComboBox(), QComboBox()
        mapping_layout.addWidget(QLabel("Blue → B"))
        mapping_layout.addWidget(self.blue_choice, 1)
        mapping_layout.addWidget(QLabel("Green / visual → V"))
        mapping_layout.addWidget(self.visual_choice, 1)
        images_layout.addWidget(self.mapping)
        self.mapping.hide()
        self.calibrate_button = QPushButton("Calibrate automatically")
        self.calibrate_button.setEnabled(False)
        self.calibrate_button.clicked.connect(self._calibrate)
        self.calibrate_button.setProperty("primary", True)
        self.calibrate_button.setToolTip("Automatically identify and measure standards, fit coefficients, and validate on separate stars.")
        self.enabled = QCheckBox("Automatically transform eligible science observations")
        self.enabled.setChecked(settings.transformation_enabled)
        controls.addStretch()

        advanced = QWidget()
        self.controls.addTab(advanced, "Advanced")
        advanced_layout = QVBoxLayout(advanced)
        advanced_layout.setContentsMargins(0, 18, 0, 0)
        self.advanced_pages = QTabWidget()
        advanced_layout.addWidget(self.advanced_pages)
        settings_page = QWidget()
        settings_layout = QVBoxLayout(settings_page)
        settings_layout.setContentsMargins(0, 16, 0, 0)
        settings_layout.addWidget(self.enabled)
        self.auto_snr = QCheckBox("Choose SNR automatically (target: 20 paired standards)")
        self.auto_snr.setChecked(True)
        self.auto_snr.setToolTip("Try SNR 100 down to 20, keeping the highest cutoff with enough reliable standards and color coverage.")
        settings_layout.addWidget(self.auto_snr)
        self.reject_outliers = QCheckBox("Automatically exclude outliers and unreliable exposures")
        self.reject_outliers.setChecked(True)
        self.reject_outliers.setToolTip("Screen using fitting stars only; validation stars remain an independent check. Exclusions are shown in Measurements & residuals.")
        self.reject_outliers.toggled.connect(self._invalidate)
        settings_layout.addWidget(self.reject_outliers)
        self.advanced_pages.addTab(settings_page, "Fit settings")
        diagnostics_page = QWidget()
        diagnostics_layout = QVBoxLayout(diagnostics_page)
        diagnostics_layout.setContentsMargins(0, 16, 0, 0)
        self.advanced_pages.addTab(diagnostics_page, "Measurements & residuals")
        form = QFormLayout()
        settings_layout.addLayout(form)
        form.addRow("Other optics / camera mode", self.accessories)
        self.name = QLineEdit("My B/V calibration")
        self.optical_setup = QLineEdit(settings.transformation_optical_setup)
        self.blue = QLineEdit("B")
        self.visual = QLineEdit("G")
        self.calibration_date = QLineEdit(date.today().isoformat())
        self.provenance = QLineEdit()
        self.provenance.setPlaceholderText("AAVSO standard-field chart IDs or documented Landolt/secondary standards")
        for label, widget in (("Profile name", self.name), ("Optical setup identity", self.optical_setup)):
            form.addRow(label, widget)
        filters = QHBoxLayout()
        filters.addWidget(QLabel("Blue input FILTER")); filters.addWidget(self.blue)
        filters.addWidget(QLabel("Visual input FILTER")); filters.addWidget(self.visual)
        form.addRow("Input → standard B/V", filters)
        self.blue.setToolTip("Exact FILTER header token. For extracted RGB, use e.g. TB and TG; G is not assumed to mean Sloan g.")
        form.addRow("Calibration date (YYYY-MM-DD)", self.calibration_date)
        form.addRow("Standards provenance", self.provenance)
        self.pair_seconds = QDoubleSpinBox()
        self.pair_seconds.setRange(1, 3600)
        self.pair_seconds.setSuffix(" s maximum B/V separation")
        self.pair_seconds.setValue(settings.transformation_max_pair_seconds)
        self.min_snr = QDoubleSpinBox()
        self.min_snr.setRange(1, 10000)
        self.min_snr.setDecimals(0)
        self.min_snr.setValue(100)
        self.min_snr.setPrefix("Minimum SNR ")
        self.min_snr.setToolTip(
            "Standards below this signal-to-noise ratio are left out of the fit. "
            "Changing it updates the measurement table without measuring the images again."
        )
        form.addRow("B/V pairing", self.pair_seconds)
        actions = QHBoxLayout()
        for label, handler in (("Measure standard images…", self._measure), ("Load measurements CSV…", self._load_csv),
                               ("Export measurements…", self._export_csv), ("Refit and validate", self._fit)):
            button = QPushButton(label)
            button.clicked.connect(handler)
            actions.addWidget(button)
        actions.addWidget(self.min_snr)
        diagnostics_layout.addLayout(actions)
        profile_actions = QHBoxLayout()
        for label, handler in (("Export profile…", self._export),
                               ("Export TG/TA coefficients…", self._export_coefficients), ("Remove profile", self._remove)):
            button = QPushButton(label)
            button.clicked.connect(handler)
            profile_actions.addWidget(button)
        settings_layout.addLayout(profile_actions)
        settings_layout.addStretch()
        splitter = QSplitter(Qt.Orientation.Horizontal)
        diagnostics_layout.addWidget(splitter, 1)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["Use", "Star", "Band", "B-V", "Instrumental", "Residual", "Reason / validation"])
        self.table.horizontalHeader().setSectionsClickable(True)
        self.table.horizontalHeader().setSortIndicatorShown(True)
        self.table.horizontalHeader().sectionClicked.connect(self._header_clicked)
        reason_header = self.table.horizontalHeaderItem(6)
        if reason_header is not None:
            reason_header.setToolTip("Click to group rows by exclusion category, such as SNR or overlapping apertures.")
        self._sort_column = -1
        self._sort_order = Qt.SortOrder.AscendingOrder
        splitter.addWidget(self.table)
        self.figure = Figure(figsize=(5, 3), tight_layout=True)
        self.canvas = FigureCanvasQTAgg(self.figure)
        splitter.addWidget(self.canvas)
        splitter.setSizes([550, 450])
        self.details = QLabel()
        self.details.setWordWrap(True)
        diagnostics_layout.addWidget(self.details)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(3)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)
        self.status = QLabel("No calibration yet. Differential photometry is available.")
        self.status.setProperty("muted", True)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Close)
        self.buttons.button(QDialogButtonBox.StandardButton.Apply).setText("Save setup")
        self.buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self._apply)
        self.buttons.rejected.connect(self.reject)
        footer = QHBoxLayout()
        footer.addStretch()
        footer.addWidget(self.buttons)
        footer.addWidget(self.calibrate_button)
        layout.addLayout(footer)
        self.profiles.currentIndexChanged.connect(self._profile_chosen)
        self.table.itemChanged.connect(self._row_changed)
        for widget in (self.name, self.telescope, self.camera, self.optical_setup, self.blue, self.visual, self.calibration_date, self.provenance):
            widget.textEdited.connect(self._invalidate)
        equipment = getattr(settings, "transformation_equipment", {})
        self.filter_system.setCurrentIndex(max(0, self.filter_system.findData(equipment.get("filter_system", "rgb"))))
        self.brand.setCurrentText(equipment.get("brand", "Other / unknown"))
        self.model.setText(equipment.get("model", ""))
        self.accessories.setText(equipment.get("accessories", ""))
        self.filter_system.currentIndexChanged.connect(self._system_changed)
        self.brand.currentTextChanged.connect(self._equipment_changed)
        self.model.textEdited.connect(self._equipment_changed)
        self.accessories.textEdited.connect(self._equipment_changed)
        self.blue_choice.currentIndexChanged.connect(self._mapping_changed)
        self.visual_choice.currentIndexChanged.connect(self._mapping_changed)
        self.blue.textEdited.connect(self._manual_mapping_changed)
        self.visual.textEdited.connect(self._manual_mapping_changed)
        self.min_snr.valueChanged.connect(self._snr_limit_changed)
        self.enabled.toggled.connect(self._update_apply_button)
        self.controls.currentChanged.connect(self._update_apply_button)
        if not self.optical_setup.text():
            self._update_optical_setup()
        self._refresh_profiles(settings.transformation_profile_id)
        self._style_dialog()

    def _style_dialog(self):
        """Use the application's palette, with flat surfaces scoped to this dialog."""
        palette = self.palette()
        window = palette.color(QPalette.ColorRole.Window)
        dark = window.lightness() < 128
        surface = window.lighter(118) if dark else window.lighter(104)
        border = window.lighter(160) if dark else window.darker(115)
        text = palette.color(QPalette.ColorRole.WindowText).name()
        muted = palette.color(QPalette.ColorRole.WindowText).darker(135).name() if dark else "#5c6675"
        accent = palette.color(QPalette.ColorRole.Highlight).name()
        accent_text = palette.color(QPalette.ColorRole.HighlightedText).name()
        base = palette.color(QPalette.ColorRole.Base).name()
        self.setStyleSheet(f"""
            QDialog#transformationDialog {{ background: {window.name()}; }}
            QDialog#transformationDialog QLabel {{ background: transparent; color: {text}; }}
            QDialog#transformationDialog QLabel[muted="true"] {{ color: {muted}; }}
            QLabel#calibrationTitle {{ font-size: 24px; font-weight: 600; }}
            QLabel#calibrationBadge {{ color: {accent}; background: {surface.name()};
                border: 1px solid {border.name()}; border-radius: 6px; padding: 6px 12px; font-weight: 600; }}
            QTabWidget::pane {{ border: 0; background: transparent; }}
            QTabWidget > QStackedWidget {{ background: transparent; }}
            QTabBar::tab {{ background: transparent; border: 0; border-bottom: 2px solid transparent;
                color: {muted}; padding: 9px 18px; margin-right: 12px; }}
            QTabBar::tab:selected {{ color: {text}; border-bottom-color: {accent}; }}
            QGroupBox[card="true"] {{ background: {surface.name()}; border: 1px solid {border.name()};
                border-radius: 10px; margin-top: 10px; font-weight: 600; }}
            QGroupBox[card="true"]::title {{ subcontrol-origin: margin; left: 18px; padding: 0 5px; color: {text}; }}
            QLineEdit, QComboBox, QDoubleSpinBox {{ background: {base}; color: {text};
                border: 1px solid {border.name()}; border-radius: 5px; padding: 6px 9px; min-height: 18px; }}
            QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus {{ border-color: {accent}; }}
            QPushButton {{ background: {surface.name()}; color: {text}; border: 1px solid {border.name()};
                border-radius: 6px; padding: 8px 14px; min-height: 18px; }}
            QPushButton:hover {{ border-color: {accent}; }}
            QPushButton:pressed {{ background: {base}; }}
            QPushButton[quiet="true"] {{ background: transparent; border-color: transparent; color: {accent}; }}
            QPushButton[primary="true"] {{ background: {accent}; color: {accent_text}; border-color: {accent}; font-weight: 600; }}
            QPushButton:disabled {{ background: {window.name()}; color: {muted}; border-color: {border.name()}; }}
            QCheckBox {{ background: transparent; spacing: 8px; }}
            QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid {border.name()}; border-radius: 3px; }}
            QCheckBox::indicator:checked {{ border-color: {accent}; background: {accent}; }}
            QProgressBar {{ border: 0; background: {surface.name()}; }}
            QProgressBar::chunk {{ background: {accent}; }}
        """)
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
        self._style_plot()

    def _style_plot(self):
        background = self.palette().color(QPalette.ColorRole.Base).name()
        foreground = self.palette().color(QPalette.ColorRole.WindowText).name()
        self.figure.set_facecolor(background)
        for axis in self.figure.axes:
            axis.set_facecolor(background)
            axis.tick_params(colors=foreground)
            axis.xaxis.label.set_color(foreground)
            axis.yaxis.label.set_color(foreground)
            for spine in axis.spines.values():
                spine.set_color(foreground)
            legend = axis.get_legend()
            if legend:
                legend.get_frame().set_facecolor(background)
                for label in legend.get_texts():
                    label.set_color(foreground)
        self.canvas.draw_idle()

    def _show_image_help(self):
        help_dialog = QMessageBox(self)
        help_dialog.setWindowTitle("Calibration images")
        help_dialog.setTextFormat(Qt.TextFormat.RichText)
        help_dialog.setText("<b>A reference field is a patch of sky with precisely measured stars.</b>")
        help_dialog.setInformativeText(
            "Examples include M67 and NGC 7790. Photograph one with your own telescope, camera, and filters; "
            "CAst retrieves the reference magnitudes. A frame wider than one degree is cropped around that field. "
            "Exposures that do not already line up are aligned before the stars are measured.<br><br>"
            "<b>Have images?</b> Add several blue + green (or B + V) exposures. Use separate, linear 2D channels, "
            "bias/dark/flat corrected, with unsaturated stars and a stable sky. Observe high in the sky (low airmass). Red frames are unused.<br><br>"
            "Use linear images without a display stretch. Differential extinction must be negligible; no extinction model is fitted.<br><br>"
            "<b>No images yet?</b> Close this window to keep your current settings, or select an existing calibration for the same equipment. "
            "Without an enabled calibration, differential photometry already works without transformation. "
            "You can take reference-field images later.<br><br>"
            "Filter brands describe your equipment; they do not supply its calibration coefficients. "
            "Record extra optics and camera mode in Advanced. Recalibrate when the equipment changes.")
        help_dialog.setStandardButtons(QMessageBox.StandardButton.Ok)
        help_dialog.exec()

    def _snr_limit_changed(self, _value=None):
        if self.worker is not None:
            return
        self.auto_snr.setChecked(False)
        self.rows = apply_calibration_snr_limit(self.rows, float(self.min_snr.value()))
        self._invalidate()
        self._display()

    def _header_clicked(self, section: int):
        if section == self._sort_column:
            self._sort_order = (
                Qt.SortOrder.DescendingOrder
                if self._sort_order == Qt.SortOrder.AscendingOrder
                else Qt.SortOrder.AscendingOrder
            )
        else:
            self._sort_column = section
            self._sort_order = Qt.SortOrder.AscendingOrder
        self._apply_table_sort()

    def _apply_table_sort(self):
        if self._sort_column < 0 or self.table.rowCount() == 0:
            return
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(self._sort_column, self._sort_order)
        self.table.horizontalHeader().setSortIndicator(self._sort_column, self._sort_order)
        self.table.setSortingEnabled(False)

    def _invalidate(self, *_):
        had_calibration = bool(self.candidate or self._selected() or self.rows)
        self.candidate = None
        self.dirty = True
        self.status.setText("Changes need calibration. Manual refitting is available in Advanced." if had_calibration
                            else "Add reference-field images to calibrate. You can also close this window and keep your current settings.")
        self._update_apply_button()

    def _equipment(self):
        return dict(filter_system=self.filter_system.currentData(), brand=self.brand.currentText().strip(),
                    model=self.model.text().strip(), accessories=self.accessories.text().strip())

    def _update_optical_setup(self):
        equipment = self._equipment()
        self.optical_setup.setText(" | ".join(value for value in equipment.values() if value))

    def _equipment_changed(self, *_):
        self._update_optical_setup()
        self._invalidate()

    def _system_changed(self, *_):
        self._equipment_changed()
        if self.image_groups:
            # Reinspect when the selected system changes; RGB G and photometric V
            # require different suggestions. Header reading is inexpensive.
            from photometry_app.core.transformation_calibration import inspect_calibration_images
            paths = [path for group in self.image_groups.values() for path in group]
            system = self.filter_system.currentData()
            self.image_groups = {}
            self._update_image_summary()
            self._start(lambda progress, cancelled: inspect_calibration_images(paths, system, progress, cancelled), self._images_scanned)

    def _update_apply_button(self, *_):
        ready = self.candidate or (self._selected() if not self.dirty else None)
        button = self.buttons.button(QDialogButtonBox.StandardButton.Apply)
        button.setText("Use calibration" if self.enabled.isChecked() else "Save setup")
        button.setEnabled(self.worker is None and (not self.enabled.isChecked() or bool(ready and ready["validated"])))
        button.setProperty("primary", self.enabled.isChecked())
        button.style().unpolish(button)
        button.style().polish(button)
        has_valid_profile = bool(ready and ready["validated"])
        button.setVisible(has_valid_profile or self.controls.currentIndex() == 1)
        self.calibrate_button.setVisible(not has_valid_profile and self.controls.currentIndex() == 0)

    def _choose_images(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Standard-field images (an entire RGB set is OK)", "", "Scientific images (*.fit *.fits *.fts *.xisf *.tif *.tiff)")
        if paths:
            self._inspect_images([Path(path) for path in paths])

    def _choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Folder containing standard-field images")
        if folder:
            paths = sorted(path for path in Path(folder).rglob("*") if path.is_file() and path.suffix.lower() in {".fit", ".fits", ".fts", ".xisf", ".tif", ".tiff"})
            if paths:
                self._inspect_images(paths)
            else:
                self._error("No supported scientific images found. Use separate 2D FITS, XISF, or TIFF channel images with observation metadata.")

    def _inspect_images(self, paths):
        from photometry_app.core.transformation_calibration import inspect_calibration_images
        self._invalidate()
        self.image_groups = {}
        self.rows = []
        self._update_image_summary()
        self._display()
        system = self.filter_system.currentData()
        self._start(lambda progress, cancelled: inspect_calibration_images(paths, system, progress, cancelled), self._images_scanned)

    def _images_scanned(self, result):
        if len(result["cameras"]) > 1 or len(result["telescopes"]) > 1:
            self.image_groups = {}
            self._update_image_summary()
            self._error("These images contain different cameras or telescopes. Choose images from one equipment setup.")
            return
        self.image_groups = result["groups"]
        header_notes = []
        for widget, key in ((self.camera, "cameras"), (self.telescope, "telescopes")):
            header = result[key][0] if result[key] else ""
            if not widget.text().strip() and header:
                widget.setText(header)
            elif header and widget.text().strip().casefold() != header.casefold():
                header_notes.append(f"Image {key[:-1]} is {header}, but the setup says {widget.text()}.")
        for combo, key in ((self.blue_choice, "blue"), (self.visual_choice, "visual")):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("Select channel…", "")
            for token, paths in self.image_groups.items():
                combo.addItem(f"{token} ({len(paths)} images)", token)
            combo.setCurrentIndex(max(0, combo.findData(result[key])))
            combo.blockSignals(False)
        self.mapping.setVisible(bool(self.image_groups))
        self._mapping_changed()
        if header_notes:
            warning = " ".join(header_notes) + " Image headers can be wrong, so the setup is kept. You can continue."
            self.status.setText(warning)
            QMessageBox.warning(self, "Photometric Transformation", warning)
        else:
            self.status.setText("Channels detected. Ready to calibrate automatically." if result["blue"] and result["visual"] and result["blue"] != result["visual"]
                                else "Choose the blue and green / visual channels to calibrate.")

    def _mapping_changed(self, *_):
        self.blue.setText(self.blue_choice.currentData() or "")
        self.visual.setText(self.visual_choice.currentData() or "")
        self._invalidate()
        self._update_image_summary()

    def _manual_mapping_changed(self, *_):
        for combo, widget in ((self.blue_choice, self.blue), (self.visual_choice, self.visual)):
            combo.blockSignals(True)
            match = next((i for i in range(combo.count()) if str(combo.itemData(i)).casefold() == widget.text().strip().casefold()), 0)
            combo.setCurrentIndex(match)
            combo.blockSignals(False)
        self._update_image_summary()

    def _update_image_summary(self, *_):
        blue, visual = self.blue_choice.currentData(), self.visual_choice.currentData()
        valid = bool(blue and visual and blue != visual and blue in self.image_groups and visual in self.image_groups)
        if not self.image_groups:
            message = "No images selected · channels detected automatically"
            self.mapping.hide()
        elif not valid:
            message = "Select two different channels to continue."
        else:
            message = f"{len(self.image_groups[blue])} blue + {len(self.image_groups[visual])} green / visual images"
            unused = [(token, len(paths)) for token, paths in self.image_groups.items() if token not in {blue, visual}]
            if unused:
                message += " Unused for B/V: " + ", ".join(f"{token} ({count})" for token, count in unused) + "."
        self.image_summary.setText(message)
        self.calibrate_button.setEnabled(self.worker is None and valid)

    def _profile_options(self):
        return dict(name=self.name.text(), telescope=self.telescope.text(), camera=self.camera.text(), optical_setup=self.optical_setup.text(),
                    blue_filter=self.blue.text(), visual_filter=self.visual.text(), calibration_date=self.calibration_date.text(),
                    provenance=self.provenance.text(), equipment=self._equipment(), reject_outliers=self.reject_outliers.isChecked(),
                    minimum_snr=self.min_snr.value())

    def _calibrate(self):
        blue, visual = self.blue_choice.currentData(), self.visual_choice.currentData()
        if not blue or not visual or blue == visual:
            self._error("Select blue and green / visual channels first.")
            return
        if not self.telescope.text().strip() or not self.camera.text().strip():
            self._error("Enter the telescope and camera so this calibration can be matched to your science images.")
            return
        from photometry_app.core.transformation_calibration import calibrate_standard_images
        paths = self.image_groups[blue] + self.image_groups[visual]
        options = self._profile_options()
        settings = deepcopy(self.settings)
        snr = None if self.auto_snr.isChecked() else self.min_snr.value()
        self._invalidate()
        self._start(lambda progress, cancelled: calibrate_standard_images(paths, settings, options, min_snr=snr, progress=progress, cancelled=cancelled), self._calibrated)

    def _calibrated(self, result):
        self.rows = result["rows"]
        if "min_snr" in result:
            self.min_snr.blockSignals(True)
            self.min_snr.setValue(result["min_snr"])
            self.min_snr.blockSignals(False)
        self.provenance.setText(result["provenance"])
        self.calibration_date.setText(result["calibration_date"])
        if result["profile"]:
            self._fitted(result["profile"])
        else:
            self.candidate = None
            self._display()
            self.details.setText(result["error"])
            self.status.setText("Calibration cancelled. Your current settings are unchanged." if "cancelled" in result["error"].lower()
                                else result["error"].split(". ", 1)[0] + ". More usable reference-field images are needed; details are in Advanced."
                                if result["error"].startswith("After quality screening")
                                else "Calibration could not be completed. Check your reference-field images; details are in Advanced.")

    def _refresh_profiles(self, selected=""):
        self.profiles.blockSignals(True)
        self.profiles.clear()
        self.profiles.addItem("New calibration…", "")
        for profile in self.settings.transformation_profiles:
            self.profiles.addItem(f"{profile['name']} — {profile['camera']} — {profile['calibration_date']}", profile["id"])
        self.profiles.setCurrentIndex(max(0, self.profiles.findData(selected)))
        self.profiles.blockSignals(False)
        self._select_profile()

    def _selected(self):
        return next((p for p in self.settings.transformation_profiles if p["id"] == self.profiles.currentData()), None)

    def _profile_chosen(self, *_):
        self._select_profile()
        self.enabled.setChecked(bool(self._selected()))

    def _select_profile(self, *_):
        profile = self._selected()
        self.candidate = None
        self.dirty = False
        self.image_groups = {}
        self._update_image_summary()
        if not profile:
            self.rows = []
            self.enabled.setChecked(False)
            self._display()
            self.status.setText("No calibration yet. Differential photometry is available.")
            self._update_apply_button()
            return
        equipment = profile.get("equipment", {})
        self.filter_system.blockSignals(True)
        self.brand.blockSignals(True)
        self.filter_system.setCurrentIndex(max(0, self.filter_system.findData(equipment.get("filter_system", "custom"))))
        self.brand.setCurrentText(equipment.get("brand", "Other / unknown"))
        self.model.setText(equipment.get("model", ""))
        self.accessories.setText(equipment.get("accessories", ""))
        self.filter_system.blockSignals(False)
        self.brand.blockSignals(False)
        self.telescope.setText(profile["telescope"])
        self.camera.setText(profile["camera"])
        self.optical_setup.setText(profile["optical_setup"])
        self.name.setText(profile["name"])
        self.blue.setText(profile["input_filters"]["B"])
        self.visual.setText(profile["input_filters"]["V"])
        self.calibration_date.setText(profile["calibration_date"])
        self.provenance.setText(profile["provenance"])
        self.reject_outliers.blockSignals(True)
        self.reject_outliers.setChecked(profile.get("reject_outliers", True))
        self.reject_outliers.blockSignals(False)
        self.min_snr.blockSignals(True)
        self.min_snr.setValue(profile.get("minimum_snr") or 100)
        self.min_snr.blockSignals(False)
        self.rows = [StandardObservation(**r) for r in profile["observations"]]
        self._display(profile)
        self._update_apply_button()

    def _start(self, job, result_handler):
        if self.worker is not None:
            return
        self.controls.setEnabled(False)
        self.calibrate_button.setEnabled(False)
        self.progress_bar.show()
        self.table.setEnabled(False)
        self.buttons.button(QDialogButtonBox.StandardButton.Apply).setEnabled(False)
        self.worker = _CalibrationWorker(job, self)
        self.worker.progress.connect(self.status.setText)
        self.worker.result.connect(result_handler)
        self.worker.failed.connect(self._error)
        self.worker.finished.connect(self._finished)
        self.worker.start()

    def _finished(self):
        self.controls.setEnabled(True)
        self.table.setEnabled(True)
        self.worker.deleteLater()
        self.worker = None
        self.progress_bar.hide()
        self._update_image_summary()
        self._update_apply_button()

    def _error(self, message):
        self.status.setText(message)
        QMessageBox.warning(self, "Photometric Transformation", message)

    def _measure(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Calibrated standard-field images (both filters)", "", "Scientific images (*.fit *.fits *.xisf *.tif *.tiff)")
        if not paths:
            return
        from photometry_app.core.transformation_calibration import measure_standard_images
        settings = deepcopy(self.settings)
        blue, visual, snr = self.blue.text(), self.visual.text(), self.min_snr.value()
        self._invalidate()
        self._start(lambda progress, cancelled: measure_standard_images([Path(p) for p in paths], settings, blue, visual, progress, cancelled, snr), self._measured)

    def _measured(self, result):
        self.rows, provenance = result
        self.provenance.setText(provenance)
        dates = [row.observation_date for row in self.rows if row.observation_date]
        if dates:
            self.calibration_date.setText(max(dates))
        self._invalidate()
        self._display()
        self.status.setText(f"Measured {len(self.rows)} star/exposure rows. Review exclusions, then fit and validate.")

    def _load_csv(self):
        path, _ = QFileDialog.getOpenFileName(self, "Standard measurements", "", "CSV (*.csv)")
        if path:
            try:
                self.rows = read_standard_observations(Path(path))
                self._invalidate()
                self._display()
                self.status.setText("CSV loaded. Confirm provenance and calibration date, then fit and validate.")
            except Exception as exc:
                self._error(str(exc))

    def _export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, "Standard measurements", "standards.csv", "CSV (*.csv)")
        if path:
            try:
                write_standard_observations(Path(path), self.rows)
            except OSError as exc:
                self._error(str(exc))

    def _fit(self):
        rows = deepcopy(self.rows)
        kwargs = self._profile_options()
        self._invalidate()
        def job(progress, cancelled):
            from photometry_app.core.transformation import CalibrationSelectionError
            try:
                return fit_profile(rows, **kwargs)
            except CalibrationSelectionError as exc:
                return exc
        def fitted(result):
            if isinstance(result, ValueError):
                self.rows = result.observations
                self._display()
                self.details.setText(str(result))
                self.status.setText(str(result).split(". ", 1)[0] + ". More usable reference-field images are needed; details are in Advanced.")
            else:
                self._fitted(result)
        self._start(job, fitted)

    def _fitted(self, profile):
        self.candidate = profile
        self.rows = [StandardObservation(**r) for r in profile["observations"]]
        self.dirty = not profile["validated"]
        self.enabled.setChecked(bool(profile["validated"]))
        self._display(profile)
        self._update_apply_button()

    def _display(self, profile=None):
        self.details.clear()
        diagnostics = {(d["star_id"], d["exposure_id"], d["band"]): d for d in (profile or {}).get("diagnostics", [])}
        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self.rows))
        for i, row in enumerate(self.rows):
            diagnostic = diagnostics.get((row.star_id, row.exposure_id, row.band), {})
            use = _SortableTableItem()
            use.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            use.setCheckState(Qt.CheckState.Checked if row.enabled else Qt.CheckState.Unchecked)
            use.setData(Qt.ItemDataRole.UserRole, 0 if row.enabled else 1)
            use.setData(Qt.ItemDataRole.UserRole + 1, i)
            self.table.setItem(i, 0, use)
            reason_text = row.reason or ("Validation star" if diagnostic.get("validation") else "")
            category = reason_category(row.reason) if row.reason else ("Validation" if diagnostic.get("validation") else "Included")
            numeric = [
                None,
                None,
                row.B - row.V,
                row.instrumental_magnitude,
                float(diagnostic["residual"]) if diagnostic else None,
                category,
            ]
            values = [row.star_id, row.band, f"{row.B-row.V:.3f}", f"{row.instrumental_magnitude:.4f}",
                      f"{diagnostic['residual']:.4f}" if diagnostic else "", reason_text]
            for column, value in enumerate(values, 1):
                item = _SortableTableItem(value)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                item.setToolTip(row.exposure_id + "\n" + row.reason)
                item.setData(Qt.ItemDataRole.UserRole + 1, i)
                sort_key = numeric[column - 1]
                if sort_key is not None:
                    item.setData(Qt.ItemDataRole.UserRole, sort_key)
                self.table.setItem(i, column, item)
        self.table.blockSignals(False)
        self.table.resizeColumnsToContents()
        self._apply_table_sort()
        self.figure.clear()
        axis = self.figure.add_subplot(111)
        axis.axhline(0, color="gray", lw=0.8)
        axis.set_xlabel("Catalog B-V")
        axis.set_ylabel("Standard − fitted magnitude")
        if profile:
            for band, color in (("B", "royalblue"), ("V", "seagreen")):
                for held_out, marker in ((False, "o"), (True, "^")):
                    data = [d for d in profile["diagnostics"] if d["enabled"] and d["band"] == band and d["validation"] == held_out]
                    axis.scatter([d["color"] for d in data], [d["residual"] for d in data], c=color, marker=marker, s=16,
                                 label=band + (" validation" if held_out else " fit"))
            excluded = [d for d in profile["diagnostics"] if not d["enabled"]]
            if excluded:
                axis.scatter([d["color"] for d in excluded], [d["residual"] for d in excluded],
                             c="gray", marker="x", s=20, alpha=.5, label="Excluded")
            axis.legend(fontsize=8)
            limit = max(.05, max(abs(d["residual"]) for d in profile["diagnostics"] if d["enabled"])*1.2)
            axis.set_ylim(-limit, limit)
            axis.axhline(.03, color="gray", ls=":", lw=.7)
            axis.axhline(-.03, color="gray", ls=":", lw=.7)
            c = profile["coefficients"]
            errors = np.sqrt(np.diag(profile["covariance"]))
            state = "Validated" if profile["validated"] else "Not validated: " + " ".join(profile["failures"])
            age = (date.today()-date.fromisoformat(profile["calibration_date"])).days
            self.details.setText(f"{state}. Tb_bv={c['Tb_bv']:.4f} ± {errors[0]:.4f}; Tv_bv={c['Tv_bv']:.4f} ± {errors[1]:.4f}; Tbv={c['Tbv']:.4f}. "
                                f"B-V range {profile['color_range'][0]:.2f}…{profile['color_range'][1]:.2f}. "
                                f"Validation RMS: B {profile['validation_rms']['B']:.4f}, V {profile['validation_rms']['V']:.4f} mag. "
                                f"Instrument: {profile['telescope']} / {profile['camera']} / {profile['optical_setup']}. "
                                + ("Annual recalibration is due." if age > 366 else ""))
            count = len({d["star_id"] for d in profile["diagnostics"] if d["enabled"]})
            rejected = sum(not r.enabled for r in self.rows)
            self.details.setText(self.details.text() + f" {rejected} measurements excluded; see their reasons in the table. Excluded points outside the plot range remain in the table."
                                 + (f" Selected minimum SNR: {profile['minimum_snr']:g}." if profile.get("minimum_snr") else ""))
            if profile.get("validation_observation_rms"):
                scatter = profile["validation_observation_rms"]
                self.details.setText(self.details.text() + f" Validation observation RMS: B {scatter['B']:.4f}, V {scatter['V']:.4f} mag.")
            if profile["validated"]:
                self.status.setText(f"Validated · {count} reference stars · {rejected} measurements excluded. "
                                    + ("Annual recalibration is due. " if age > 366 else "")
                                    + "Regenerate science results after applying.")
                self.status.setToolTip("Independent validation scatter in B and V. Use calibration saves this profile and enables automatic transformation of eligible observations.")
            else:
                self.status.setText("Calibration did not pass the accuracy checks. Try better reference-field images; details are in Advanced.")
        self._style_plot()

    def _row_changed(self, item):
        if item.column() != 0:
            return
        index = item.data(Qt.ItemDataRole.UserRole + 1)
        if index is None:
            index = item.row()
        index = int(index)
        row = self.rows[index]
        checked = item.checkState() == Qt.CheckState.Checked
        parts = [part.strip() for part in row.reason.split(";") if part.strip() and part.strip() != "User selection"]
        parts.append("User selection")
        self.rows[index] = replace(row, enabled=checked, reason="; ".join(parts))
        item.setData(Qt.ItemDataRole.UserRole, 0 if checked else 1)
        self._invalidate()

    def _save_profile(self):
        if not self.candidate or not self.candidate["validated"]:
            self._error("Fit and validate the current measurements before saving an active profile.")
            return
        profile = deepcopy(self.candidate)
        self.settings.transformation_profiles = [p for p in self.settings.transformation_profiles if p["id"] != profile["id"]] + [profile]
        self._refresh_profiles(profile["id"])

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import a CAst profile (revalidated on import)", "", "JSON (*.json)")
        if path:
            try:
                profile = load_profile(Path(path))
                if not profile["validated"]:
                    raise ValueError("Imported profile did not pass validation: " + " ".join(profile["failures"]))
                self.settings.transformation_profiles = [p for p in self.settings.transformation_profiles if p["id"] != profile["id"]] + [profile]
                self._refresh_profiles(profile["id"])
                self.enabled.setChecked(True)
            except Exception as exc:
                self._error(str(exc))

    def _export(self):
        profile = self.candidate or (self._selected() if not self.dirty else None)
        if profile:
            path, _ = QFileDialog.getSaveFileName(self, "Export instrument profile", "transformation.json", "JSON (*.json)")
            if path:
                try:
                    Path(path).write_text(json.dumps(profile, indent=2, allow_nan=False), encoding="utf-8")
                except OSError as exc:
                    self._error(str(exc))

    def _export_coefficients(self):
        profile = self.candidate or (self._selected() if not self.dirty else None)
        if not profile:
            return
        path, _ = QFileDialog.getSaveFileName(self, "TG/TA coefficient file", "coefficients.ini", "INI (*.ini)")
        if path:
            coefficients = profile["coefficients"]
            covariance = np.asarray(profile["covariance"])
            derivative = np.array([1., -1.])*coefficients["Tbv"]**2
            errors = dict(zip(("Tb_bv", "Tv_bv"), np.sqrt(np.diag(covariance))))
            errors["Tbv"] = float(np.sqrt(derivative @ covariance @ derivative))
            text = "[Setup]\ndescription=CAst validated B/V profile " + profile["id"] + "\n[Coefficients]\n"
            text += "\n".join(f"{key}={value:.8f}" for key, value in coefficients.items())
            text += "\n[Error]\n" + "\n".join(f"{key}={value:.8f}" for key, value in errors.items()) + "\n"
            try:
                Path(path).write_text(text, encoding="utf-8")
            except OSError as exc:
                self._error(str(exc))

    def _remove(self):
        self.settings.transformation_profiles = [p for p in self.settings.transformation_profiles if p["id"] != self.profiles.currentData()]
        self._refresh_profiles()

    def _apply(self):
        profile = self.candidate or (self._selected() if not self.dirty else None)
        if self.enabled.isChecked():
            if not profile or not profile["validated"]:
                self._error("Calibrate this setup first, or turn off automatic transformation to save just the setup.")
                return
            if any(widget.text().strip() != profile[key] for widget, key in ((self.telescope, "telescope"), (self.camera, "camera"), (self.optical_setup, "optical_setup"))):
                self._error("The current telescope, camera, and optical setup must match the profile. Recalibrate after changing the instrument.")
                return
        if self.candidate and self.candidate["validated"]:
            self._save_profile()
        self.settings.transformation_enabled = self.enabled.isChecked()
        self.settings.transformation_profile_id = (self.profiles.currentData() or "") if not self.dirty else ""
        self.settings.transformation_max_pair_seconds = self.pair_seconds.value()
        self.settings.transformation_optical_setup = self.optical_setup.text().strip()
        self.settings.transformation_equipment = self._equipment()
        self.settings.telescope = self.telescope.text().strip()
        self.settings.camera = self.camera.text().strip()
        self.accept()

    def reject(self):
        if self.worker is not None:
            self.worker.requestInterruption()
            self.status.setText("Cancellation requested. Waiting for the current solver/measurement to finish…")
            return
        super().reject()

    def closeEvent(self, event):
        if self.worker is not None:
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)
