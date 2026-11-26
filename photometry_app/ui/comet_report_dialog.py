"""Review the selected aperture's dust proxy and prepare an electronic ICQ record."""
from __future__ import annotations

from pathlib import Path
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QScrollArea, QTabWidget, QVBoxLayout, QWidget,
)

from photometry_app.core.comet_photometry import comet_catalog_band
from photometry_app.core.comet_reporting import COBS_APASS_KEYS, COBS_FILTER_KEYS, aperture_dust_proxy, build_icq_record, icq_target


class CometReportDialog(QDialog):
    def __init__(self, workspace, row):
        super().__init__(workspace)
        self.setWindowTitle("Comet report")
        self.resize(680, 620)
        self.row = row
        self.pair = workspace.pair
        self.band = workspace.band
        self.frames = self.pair.provenance["frames"]
        config = self.pair.provenance.get("report_inputs", {})
        self.geometry = self.pair.provenance.get("geometry", {})
        self.closest = None
        if workspace.zero_point and row["magnitude"] is not None:
            zp = workspace.zero_point
            usable = [s.magnitude for s in workspace.comparisons if
                      s.source_id in zp.residuals and s.source_id not in zp.rejected]
            if usable:
                self.closest = min(usable, key=lambda m: abs(m - row["magnitude"]))
        self.pixel_scales = (row["pixel_scale_x_arcsec"], row["pixel_scale_y_arcsec"])
        root = QVBoxLayout(self)
        root.addWidget(QLabel(f"{row['target']}  ·  {row['radius_arcsec']:.2f}″ radius  ·  {row['magnitude_system']}"))
        tabs = QTabWidget()
        root.addWidget(tabs)
        dust = QWidget()
        dust_layout = QVBoxLayout(dust)
        form = QFormLayout()
        dust_layout.addLayout(form)
        self.helio = self._number(form, "Sun–comet distance (au)", self.geometry.get("heliocentric_au"), 9)
        self.delta = self._number(form, "Observer–comet distance (au)", self.geometry.get("observer_au"), 9)
        hint = QLabel("Use distances at the observation epoch. Horizons preparation supplies the mean distances across the selected exposures.")
        hint.setWordWrap(True)
        dust_layout.addWidget(hint)
        self.dust_result = QLabel()
        self.dust_result.setWordWrap(True)
        dust_layout.addWidget(self.dust_result)
        dust_layout.addStretch(1)
        tabs.addTab(dust, "Aperture Afρ")
        report = QWidget()
        layout = QVBoxLayout(report)
        entry = QWidget()
        form = QFormLayout()
        entry.setLayout(form)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(entry)
        layout.addWidget(scroll, 2)
        self.fields = {}
        defaults = {"designation": icq_target(row["target"]), "observer": "", "instrument": "",
                    "method": COBS_FILTER_KEYS.get(comet_catalog_band(row["filter"]), ""),
                    "catalog": COBS_APASS_KEYS.get(self.band, "") if row.get("comparison_catalog", "APASS") == "APASS" else "",
                    "camera": "", "chip": "", "software": ""}
        choices = {
            "method": {"D": "Johnson B", "V": "Johnson V", "g": "Sloan g", "r": "Sloan r", "h": "Sloan i",
                       "C": "Unfiltered total magnitude", "Z": "Visual-equivalent CCD magnitude"},
            "catalog": {"AB": "APASS B", "AV": "APASS V", "AG": "APASS Sloan g", "AR": "APASS Sloan r",
                        "AI": "APASS Sloan i", "AL": "ATLAS Refcat"},
            "instrument": {"R": "Refractor", "L": "Newtonian", "C": "Cassegrain", "T": "Schmidt-Cassegrain",
                           "M": "Maksutov", "D": "Schmidt reflector", "S": "Schmidt-Newtonian", "P": "Prime focus"},
        }
        if config.get("comparison_catalog", row.get("comparison_catalog")) != row.get("comparison_catalog"):
            config = {k: v for k, v in config.items() if k != "catalog"}
        for key, title in (("designation", "ICQ object ID (columns 1–11)"), ("observer", "Observer code"),
                           ("method", "Magnitude method key"), ("catalog", "Comparison catalog key"),
                           ("instrument", "Telescope type key")):
            value = config.get(key, defaults[key])
            if key in choices:
                combo = QComboBox()
                combo.setEditable(True)
                combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
                combo.addItems([f"{code}  ·  {name}" for code, name in choices[key].items()])
                combo.setCurrentText(f"{value}  ·  {choices[key][value]}" if value in choices[key] else value)
                self.fields[key] = combo.lineEdit()
                form.addRow(title, combo)
            else:
                field = QLineEdit(value)
                self.fields[key] = field
                form.addRow(title, field)
        key_help = QLabel('<a href="https://cobs.si/help/icq_list/obs_methods/">COBS method, catalog and instrument keys</a>')
        key_help.setOpenExternalLinks(True)
        form.addRow("", key_help)
        self.aperture_cm = self._number(form, "Telescope aperture (cm)", config.get("aperture_cm"), 1)
        self.f_ratio = self._number(form, "Focal ratio", config.get("f_ratio"), 2)
        self.calibration = QComboBox()
        self.calibration.addItems(["Select image corrections", "0  None", "1  Bias", "2  Flat", "3  Bias and flat",
                                   "4  Bias and dark", "5  Bias, dark and flat"])
        cal = str(config.get("calibration", ""))
        if cal in tuple("012345"):
            self.calibration.setCurrentIndex(int(cal) + 1)
        form.addRow("Detector calibration", self.calibration)
        extra = QHBoxLayout()
        for key in ("camera", "chip", "software"):
            field = QLineEdit(config.get(key, ""))
            field.setPlaceholderText(key.capitalize())
            field.setAccessibleName(f"ICQ {key} key")
            self.fields[key] = field
            extra.addWidget(field)
        self.fields["software"].setToolTip("Use CAst's assigned software code if the receiving archive has issued one.")
        form.addRow("Assigned keys (optional)", extra)
        for key, label, hint in (("coma_arcmin", "Measured coma diameter (arcmin)", "Optional visual extent"),
                                 ("condensation", "Degree of condensation", "Optional, 0–9 or e.g. 5/")):
            field = QLineEdit(config.get(key, ""))
            field.setPlaceholderText(hint)
            self.fields[key] = field
            form.addRow(label, field)
        self.total = QCheckBox("The selected aperture encloses the visible coma")
        layout.addWidget(self.total)
        self.quality_check = QCheckBox("I reviewed the flagged aperture measurement")
        self.quality_check.setVisible(bool(row["quality"]))
        layout.addWidget(self.quality_check)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.preview.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        layout.addWidget(self.preview, 1)
        self.report_status = QLabel()
        self.report_status.setWordWrap(True)
        layout.addWidget(self.report_status)
        actions = QHBoxLayout()
        self.copy = QPushButton("Copy ICQ Line")
        self.save = QPushButton("Save ICQ…")
        self.copy.clicked.connect(lambda: QApplication.clipboard().setText(self.preview.toPlainText()))
        self.save.clicked.connect(self._save)
        actions.addWidget(self.copy)
        actions.addWidget(self.save)
        layout.addLayout(actions)
        tabs.addTab(report, "ICQ observation")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        for edit in self.fields.values():
            edit.textChanged.connect(self._refresh)
        for spin in (self.helio, self.delta, self.aperture_cm, self.f_ratio):
            spin.valueChanged.connect(self._refresh)
        self.calibration.currentIndexChanged.connect(self._refresh)
        self.total.toggled.connect(self._refresh)
        self.quality_check.toggled.connect(self._refresh)
        self._refresh()

    def _number(self, layout, title, value, precision):
        spin = QDoubleSpinBox()
        spin.setDecimals(precision)
        spin.setRange(0, 100000)
        spin.setSpecialValueText("Enter value")
        spin.setValue(value or 0)
        layout.addRow(title, spin)
        return spin

    def distances(self):
        r, delta = self.helio.value() or None, self.delta.value() or None
        if all(round(self.geometry.get(key) or 0, 9) == (value or 0)
               for key, value in (("heliocentric_au", r), ("observer_au", delta))):
            return self.geometry.copy()
        return {"heliocentric_au": r, "observer_au": delta, "source": "Observer-entered"}

    def inputs(self):
        return {**{key: edit.text().partition("  ·  ")[0] for key, edit in self.fields.items()},
                "comparison_catalog": self.row.get("comparison_catalog"), "aperture_cm": self.aperture_cm.value(),
                "f_ratio": self.f_ratio.value(), "calibration": self.calibration.currentText()[0]
                if self.calibration.currentIndex() else ""}

    def _refresh(self, *_):
        values = aperture_dust_proxy(self.row, self.distances())
        lines = []
        if values["projected_aperture_radius_km"] is not None:
            lines.append(f"Projected aperture radius  {values['projected_aperture_radius_km']:,.1f} km")
        if values["afrho_cm"] is not None:
            lines.append(f"Afρ  {values['afrho_cm']:.4g} cm  ·  solar magnitude {values['solar_magnitude_1au']:.2f}")
        if values["geometry_source"]:
            lines.append(f"Distances  {values['geometry_source']}")
        self.dust_result.setText("\n".join([*lines, values["afrho_note"]]))
        inputs = {**self.inputs(), "total": self.total.isChecked(), "quality_reviewed": self.quality_check.isChecked()}
        try:
            record = build_icq_record(self.row, self.frames, inputs, closest_comparison=self.closest,
                                      pixel_scales=self.pixel_scales)
            message = "Review the keys against the receiving archive before submission."
        except ValueError as exc:
            record, message = "", str(exc)
        self.report_status.setText(message)
        self.preview.setPlainText(record)
        self.copy.setEnabled(bool(record))
        self.save.setEnabled(bool(record))

    def _save(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save ICQ observation", "comet_icq.txt", "Text (*.txt)")
        if path:
            try:
                Path(path).write_text(self.preview.toPlainText() + "\n", encoding="utf-8")
                self.report_status.setText("ICQ observation saved.")
            except OSError as exc:
                self.report_status.setText(str(exc))
