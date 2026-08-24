from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

SkyExplorerSourceKind = Literal["survey"]


@dataclass(frozen=True, slots=True)
class SkyExplorerSourceChoice:
    kind: SkyExplorerSourceKind = "survey"
    survey_key: str | None = None


class SkyExplorerSourceDialog(QDialog):
    """Choose one of the built-in sky surveys."""

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

        self._survey_combo = QComboBox(self)
        self._survey_combo.setMaxVisibleItems(18)
        self._survey_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        for survey_key, survey_title in self._survey_options:
            self._survey_combo.addItem(survey_title, survey_key)
        if initial_survey_key:
            survey_index = self._survey_combo.findData(str(initial_survey_key))
            if survey_index >= 0:
                self._survey_combo.setCurrentIndex(survey_index)

        layout.addWidget(self._survey_combo)

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

    def choice(self) -> SkyExplorerSourceChoice:
        survey_key = str(self._survey_combo.currentData() or "").strip() or None
        return SkyExplorerSourceChoice(kind="survey", survey_key=survey_key)
