"""Prepare native OSC science channels in a worker, keeping originals intact."""
from dataclasses import asdict
import json
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
                              QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
                              QScrollArea, QTextEdit, QVBoxLayout, QWidget)

from photometry_app.core.osc import OscCameraProfile, OscPreparationRequest, prepare_osc_sequence


class OscWorker(QThread):
    progress = Signal(str)
    completed = Signal(object, object)
    failed = Signal(str)

    def __init__(self, request, parent=None):
        super().__init__(parent)
        self.request = request

    def run(self):
        try:
            products, warnings = prepare_osc_sequence(self.request, progress=self.progress.emit,
                                                       cancelled=self.isInterruptionRequested)
            self.completed.emit(products, warnings)
        except Exception as exc:
            self.failed.emit(str(exc))


class OscPreparationDialog(QDialog):
    def __init__(self, root: Path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Prepare OSC / Bayer photometry")
        self.resize(720, 760)
        self.worker = None
        self.output_root = None
        layout = QVBoxLayout(self)
        note = QLabel("Calibrate the original Bayer samples, then extract linear TB and TG (G1+G2). "
                      "No demosaicing, white balance, gamma, or alignment is applied. "
                      "Use matching camera gain/ISO, temperature, geometry, and optics for calibration frames.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.form_widget = QWidget()
        form = QFormLayout(self.form_widget)
        self.inputs = {}
        for key, label, initial in (("science", "Science file / folder", str(root)),
                                    ("output", "New output directory", str(root / "osc-prepared")),
                                    ("bias", "Bias master / frames", ""), ("dark", "Matched dark master / frames", ""),
                                    ("flat", "Raw flat master / frames", ""), ("dark_flat", "Matched dark-flat / frames", "")):
            row = QHBoxLayout()
            field = QLineEdit(initial)
            self.inputs[key] = field
            row.addWidget(field)
            if key != "output":
                button = QPushButton("File…")
                button.clicked.connect(lambda _=False, f=field: self._browse(f, False))
                row.addWidget(button)
            button = QPushButton("Parent…" if key == "output" else "Folder…")
            button.clicked.connect(lambda _=False, f=field, output=(key == "output"): self._browse(f, True, output))
            row.addWidget(button)
            form.addRow(label, row)
        self.calibrated = QCheckBox("Already calibrated mosaic (requires VARIANCE and SATFRAC extensions)")
        form.addRow(self.calibrated)
        self.profile_name = QLineEdit("Generic OSC")
        form.addRow("Camera profile name", self.profile_name)
        self.pattern = QComboBox()
        self.pattern.addItems(["Auto", "RGGB", "BGGR", "GRBG", "GBRG"])
        self.pattern.setToolTip("Auto reads Bayer headers and ROI offsets. An explicit choice describes the first 2×2 samples in array row order.")
        form.addRow("Bayer pattern", self.pattern)
        self.timezone = QLineEdit("UTC")
        form.addRow("Naive DATE-OBS timezone", self.timezone)
        self.reference = QComboBox()
        self.reference.addItems(["start", "mid", "end"])
        form.addRow("DATE-OBS exposure reference", self.reference)
        self.numbers = {}
        for key, label in (("gain", "Gain (electrons/ADU)"), ("read_noise", "Read noise (electrons RMS)"),
                           ("saturation", "Sensor saturation (original ADU)"), ("exposure_seconds", "Exposure if missing (seconds)")):
            field = QLineEdit()
            field.setPlaceholderText("Use header if available")
            self.numbers[key] = field
            form.addRow(label, field)
        self.channels = QComboBox()
        self.channels.addItem("Green + blue — TG and TB", ("TG", "TB"))
        self.channels.addItem("Green only — TG", ("TG",))
        self.channels.addItem("Blue only — TB", ("TB",))
        self.channels.addItem("Green, blue, red — TG, TB, TR", ("TG", "TB", "TR"))
        form.addRow("Extract", self.channels)
        buttons = QHBoxLayout()
        for title, callback in (("Load camera profile…", self._load_profile), ("Save camera profile…", self._save_profile),
                                ("DWARF Mini timing", self._dwarf)):
            button = QPushButton(title)
            button.clicked.connect(callback)
            buttons.addWidget(button)
        form.addRow(buttons)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.form_widget)
        layout.addWidget(scroll, 3)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log, 1)
        row = QHBoxLayout()
        self.prepare = QPushButton("Prepare channels")
        self.prepare.clicked.connect(self._start)
        row.addWidget(self.prepare)
        self.use = QPushButton("Use prepared folder")
        self.use.setEnabled(False)
        self.use.clicked.connect(self.accept)
        row.addWidget(self.use)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.reject)
        row.addWidget(self.close_button)
        layout.addLayout(row)

    def _browse(self, field, folder, output=False):
        selected = (QFileDialog.getExistingDirectory(self, "Select folder", field.text()) if folder else
                    QFileDialog.getOpenFileName(self, "Select original mosaic or master", field.text())[0])
        if selected:
            field.setText(str(Path(selected) / "osc-prepared") if output else selected)

    def _profile(self):
        values = {key: float(field.text()) if field.text().strip() else None for key, field in self.numbers.items()}
        return OscCameraProfile(name=self.profile_name.text().strip(), pattern=self.pattern.currentText(),
                                timestamp_timezone=self.timezone.text().strip(),
                                timestamp_reference=self.reference.currentText(), **values)

    def _load_profile(self):
        name, _ = QFileDialog.getOpenFileName(self, "Load OSC camera profile", "", "JSON (*.json)")
        if not name:
            return
        try:
            profile = OscCameraProfile(**json.loads(Path(name).read_text(encoding="utf-8")))
            if profile.pattern not in [self.pattern.itemText(i) for i in range(self.pattern.count())] or profile.timestamp_reference not in {"start", "mid", "end"}:
                raise ValueError("Unsupported Bayer pattern or timestamp reference.")
            self.profile_name.setText(profile.name)
            self.pattern.setCurrentText(profile.pattern)
            self.timezone.setText(profile.timestamp_timezone)
            self.reference.setCurrentText(profile.timestamp_reference)
            for key, field in self.numbers.items():
                value = getattr(profile, key)
                field.setText("" if value is None else str(value))
        except Exception as exc:
            QMessageBox.warning(self, "Invalid profile", str(exc))

    def _save_profile(self):
        try:
            profile = self._profile()
            name, _ = QFileDialog.getSaveFileName(self, "Save OSC camera profile", "osc-camera.json", "JSON (*.json)")
            if name:
                Path(name).write_text(json.dumps(asdict(profile), indent=2), encoding="utf-8")
        except Exception as exc:
            QMessageBox.warning(self, "Profile", str(exc))

    def _dwarf(self):
        self.profile_name.setText("DWARF Mini — verify firmware timing")
        self.reference.setCurrentText("end")
        self.timezone.clear()
        self.timezone.setPlaceholderText("Required: phone timezone at acquisition, e.g. America/New_York")
        self.log.append("DWARF Mini preset: DATE-OBS is exposure end. Enter the phone timezone used at acquisition and verify against a sample exposure.")

    def build_request(self):
        return OscPreparationRequest(
            science_path=Path(self.inputs["science"].text()), output_directory=Path(self.inputs["output"].text()),
            profile=self._profile(), already_calibrated=self.calibrated.isChecked(), channels=self.channels.currentData(),
            **{key + "_path": Path(self.inputs[key].text()) if self.inputs[key].text().strip() else None
               for key in ("bias", "dark", "flat", "dark_flat")})

    def _start(self):
        try:
            request = self.build_request()
        except Exception as exc:
            QMessageBox.warning(self, "OSC preparation", str(exc))
            return
        self.form_widget.setEnabled(False)
        self.prepare.setEnabled(False)
        self.use.setEnabled(False)
        self.close_button.setText("Cancel")
        self.worker = OscWorker(request, self)
        self.worker.progress.connect(self.log.append)
        self.worker.failed.connect(self._failed)
        self.worker.completed.connect(self._completed)
        self.worker.finished.connect(self._finished)
        self.worker.start()

    def _completed(self, products, warnings):
        self.output_root = self.worker.request.output_directory / "channels"
        for warning in warnings:
            self.log.append("Warning: " + warning)
        self.log.append("Channel pixels span 2×2 sensor pixels. Review aperture radii and PSF sampling. "
                        "Use these channels in Photometric Transformation with RGB selected; use the same extraction for standards and science.")

    def _failed(self, message):
        self.output_root = None
        self.log.append("Preparation failed: " + message)

    def _finished(self):
        self.form_widget.setEnabled(True)
        self.prepare.setEnabled(True)
        self.use.setEnabled(self.output_root is not None)
        self.close_button.setText("Close")

    def reject(self):
        if self.worker is not None and self.worker.isRunning():
            self.worker.requestInterruption()
            self.log.append("Cancellation requested; waiting for the current frame.")
            return
        super().reject()

    def closeEvent(self, event):
        if self.worker is not None and self.worker.isRunning():
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)
