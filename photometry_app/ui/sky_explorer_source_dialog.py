from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QLabel,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

SkyExplorerSourceKind = Literal["upload", "survey"]


@dataclass(frozen=True, slots=True)
class SkyExplorerSourceChoice:
    kind: SkyExplorerSourceKind
    survey_key: str | None = None


class SkyExplorerSourceDialog(QDialog):
    """Choose either a local upload or one of the built-in sky surveys."""

    def __init__(
        self,
        *,
        title: str,
        intro: str,
        survey_options: tuple[tuple[str, str], ...],
        initial_survey_key: str | None = None,
        field_hint: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(440)
        self._survey_options = tuple(survey_options)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        intro_label = QLabel(intro, self)
        intro_label.setWordWrap(True)
        layout.addWidget(intro_label)

        self._upload_radio = QRadioButton("Upload image", self)
        self._upload_radio.setToolTip("Choose a local FITS, XISF, TIFF, PNG, or JPEG image.")
        self._survey_radio = QRadioButton("Sky survey", self)
        self._survey_radio.setToolTip("Load a WCS-aligned public survey cutout.")
        self._mode_group = QButtonGroup(self)
        self._mode_group.addButton(self._upload_radio)
        self._mode_group.addButton(self._survey_radio)
        self._upload_radio.setChecked(True)

        mode_box = QGroupBox("Source", self)
        mode_layout = QVBoxLayout(mode_box)
        mode_layout.setContentsMargins(10, 10, 10, 10)
        mode_layout.setSpacing(6)
        mode_layout.addWidget(self._upload_radio)
        mode_layout.addWidget(self._survey_radio)
        layout.addWidget(mode_box)

        self._survey_combo = QComboBox(self)
        for survey_key, survey_title in self._survey_options:
            self._survey_combo.addItem(survey_title, survey_key)
        if initial_survey_key:
            survey_index = self._survey_combo.findData(str(initial_survey_key))
            if survey_index >= 0:
                self._survey_combo.setCurrentIndex(survey_index)

        self._survey_box = QGroupBox("Survey", self)
        survey_layout = QVBoxLayout(self._survey_box)
        survey_layout.setContentsMargins(10, 10, 10, 10)
        survey_layout.setSpacing(8)
        survey_layout.addWidget(self._survey_combo)
        layout.addWidget(self._survey_box)

        self._field_hint = QLabel(
            field_hint
            or "Survey fields start from Settings → Sky Explorer (default: Trifid Nebula) and keep loading as you pan.",
            self,
        )
        self._field_hint.setWordWrap(True)
        layout.addWidget(self._field_hint)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._upload_radio.toggled.connect(self._sync_mode_controls)
        self._survey_radio.toggled.connect(self._sync_mode_controls)
        self._sync_mode_controls()

    def _sync_mode_controls(self) -> None:
        survey_selected = self._survey_radio.isChecked()
        self._survey_combo.setEnabled(survey_selected)
        self._survey_box.setVisible(survey_selected)
        self._field_hint.setVisible(survey_selected)

    def choice(self) -> SkyExplorerSourceChoice:
        if self._upload_radio.isChecked():
            return SkyExplorerSourceChoice(kind="upload")
        survey_key = str(self._survey_combo.currentData() or "").strip() or None
        return SkyExplorerSourceChoice(kind="survey", survey_key=survey_key)
