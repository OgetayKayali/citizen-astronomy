from __future__ import annotations

import uuid
from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from photometry_app.core.settings import (
    AppSettings,
    SkyAtlasCustomOverlayRecord,
    SkyAtlasCustomOverlaySurvey,
    sky_atlas_custom_overlay_surveys_for_settings,
)
from photometry_app.core.sky_atlas_custom_overlay import (
    SKY_ATLAS_OVERLAY_FILE_FILTER,
    is_sky_atlas_overlay_path,
)
from photometry_app.core.sky_atlas_survey_storage import persist_survey_to_disk
from photometry_app.ui.workers import SkyAtlasCustomOverlayImportWorker

_SURVEY_FILTER_PRESETS = (
    "",
    "Luminance",
    "Clear",
    "R",
    "G",
    "B",
    "Ha",
    "OIII",
    "SII",
)


def _survey_list_label(survey: SkyAtlasCustomOverlaySurvey) -> str:
    filter_name = str(survey.filter_name or "").strip()
    if filter_name:
        return f"{survey.name} ({filter_name})"
    return survey.name


class SkyAtlasCustomOverlaySurveysDialog(QDialog):
    def __init__(
        self,
        settings: AppSettings,
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Custom Sky Map Surveys")
        self.setModal(True)
        self.resize(760, 480)

        self._settings = settings
        self._surveys = deepcopy(sky_atlas_custom_overlay_surveys_for_settings(settings))
        self._import_worker: SkyAtlasCustomOverlayImportWorker | None = None
        self._progress_dialog: QProgressDialog | None = None
        self._pending_import_survey_id: str | None = None

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(12, 12, 12, 12)
        root_layout.setSpacing(10)

        intro = QLabel(
            "Group sky map overlays into surveys, for example by filter band. "
            "Surveys are saved under the survey folder next to the program so you can share them by copying a survey subfolder. "
            "Select a survey in the Custom Sky Map menu to display its images."
        )
        intro.setWordWrap(True)
        root_layout.addWidget(intro)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root_layout.addWidget(splitter, 1)

        survey_panel = QWidget(splitter)
        survey_layout = QVBoxLayout(survey_panel)
        survey_layout.setContentsMargins(0, 0, 0, 0)
        survey_layout.setSpacing(8)
        survey_layout.addWidget(QLabel("Surveys"))
        self._survey_list = QListWidget(survey_panel)
        self._survey_list.currentItemChanged.connect(self._handle_survey_selection_changed)
        survey_layout.addWidget(self._survey_list, 1)
        survey_button_row = QHBoxLayout()
        survey_button_row.setContentsMargins(0, 0, 0, 0)
        survey_button_row.setSpacing(8)
        self._add_survey_button = QPushButton("New Survey")
        self._add_survey_button.clicked.connect(self._add_survey)
        self._remove_survey_button = QPushButton("Delete Survey")
        self._remove_survey_button.clicked.connect(self._remove_selected_survey)
        survey_button_row.addWidget(self._add_survey_button)
        survey_button_row.addWidget(self._remove_survey_button)
        survey_layout.addLayout(survey_button_row)
        splitter.addWidget(survey_panel)

        detail_panel = QWidget(splitter)
        detail_layout = QVBoxLayout(detail_panel)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(8)

        name_row = QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(8)
        name_row.addWidget(QLabel("Name"))
        self._survey_name_edit = QLineEdit(detail_panel)
        self._survey_name_edit.editingFinished.connect(self._apply_survey_detail_edits)
        name_row.addWidget(self._survey_name_edit, 1)
        detail_layout.addLayout(name_row)

        filter_row = QHBoxLayout()
        filter_row.setContentsMargins(0, 0, 0, 0)
        filter_row.setSpacing(8)
        filter_row.addWidget(QLabel("Filter"))
        self._survey_filter_combo = QComboBox(detail_panel)
        self._survey_filter_combo.setEditable(True)
        for preset in _SURVEY_FILTER_PRESETS:
            self._survey_filter_combo.addItem(preset)
        self._survey_filter_combo.currentTextChanged.connect(self._apply_survey_detail_edits)
        self._survey_filter_combo.lineEdit().editingFinished.connect(self._apply_survey_detail_edits)
        filter_row.addWidget(self._survey_filter_combo, 1)
        detail_layout.addLayout(filter_row)

        detail_layout.addWidget(QLabel("Images"))
        self._overlay_list = QListWidget(detail_panel)
        self._overlay_list.setMinimumHeight(180)
        detail_layout.addWidget(self._overlay_list, 1)

        image_button_row = QHBoxLayout()
        image_button_row.setContentsMargins(0, 0, 0, 0)
        image_button_row.setSpacing(8)
        self._add_image_button = QPushButton("Add Image...")
        self._add_image_button.clicked.connect(self._browse_for_overlay_image)
        self._remove_image_button = QPushButton("Remove Selected")
        self._remove_image_button.clicked.connect(self._remove_selected_overlay)
        image_button_row.addWidget(self._add_image_button)
        image_button_row.addWidget(self._remove_image_button)
        detail_layout.addLayout(image_button_row)
        splitter.addWidget(detail_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self._handle_accept)
        button_box.rejected.connect(self.reject)
        root_layout.addWidget(button_box)

        self._refresh_survey_list()
        if self._survey_list.count() > 0:
            self._survey_list.setCurrentRow(0)
        else:
            self._set_detail_panel_enabled(False)

    @property
    def surveys(self) -> list[SkyAtlasCustomOverlaySurvey]:
        self._apply_survey_detail_edits()
        return deepcopy(self._surveys)

    def _set_detail_panel_enabled(self, enabled: bool) -> None:
        self._survey_name_edit.setEnabled(enabled)
        self._survey_filter_combo.setEnabled(enabled)
        self._overlay_list.setEnabled(enabled)
        self._add_image_button.setEnabled(enabled)
        self._remove_image_button.setEnabled(enabled)

    def _selected_survey_id(self) -> str | None:
        current_item = self._survey_list.currentItem()
        if current_item is None:
            return None
        survey_id = current_item.data(Qt.ItemDataRole.UserRole)
        return str(survey_id) if survey_id else None

    def _survey_index(self, survey_id: str | None) -> int | None:
        if not survey_id:
            return None
        for index, survey in enumerate(self._surveys):
            if survey.survey_id == survey_id:
                return index
        return None

    def _selected_survey(self) -> SkyAtlasCustomOverlaySurvey | None:
        index = self._survey_index(self._selected_survey_id())
        if index is None:
            return None
        return self._surveys[index]

    def _refresh_survey_list(self, *, select_survey_id: str | None = None) -> None:
        selected_survey_id = select_survey_id or self._selected_survey_id()
        self._survey_list.blockSignals(True)
        self._survey_list.clear()
        for survey in self._surveys:
            item = QListWidgetItem(_survey_list_label(survey))
            item.setData(Qt.ItemDataRole.UserRole, survey.survey_id)
            image_count = len(survey.overlays)
            item.setToolTip(
                f"{survey.name}\nFilter: {survey.filter_name or '—'}\nImages: {image_count}"
            )
            self._survey_list.addItem(item)
            if selected_survey_id == survey.survey_id:
                self._survey_list.setCurrentItem(item)
        self._survey_list.blockSignals(False)
        self._remove_survey_button.setEnabled(bool(self._surveys))

    def _refresh_overlay_list(self) -> None:
        survey = self._selected_survey()
        self._overlay_list.clear()
        if survey is None:
            return
        for record in survey.overlays:
            item = QListWidgetItem(record.display_name)
            item.setData(Qt.ItemDataRole.UserRole, record.overlay_id)
            item.setToolTip(record.source_image_path or record.cached_image_path)
            self._overlay_list.addItem(item)

    def _handle_survey_selection_changed(
        self,
        _current: QListWidgetItem | None,
        _previous: QListWidgetItem | None,
    ) -> None:
        survey = self._selected_survey()
        if survey is None:
            self._survey_name_edit.clear()
            self._survey_filter_combo.setCurrentText("")
            self._overlay_list.clear()
            self._set_detail_panel_enabled(False)
            return
        self._set_detail_panel_enabled(True)
        self._survey_name_edit.blockSignals(True)
        self._survey_filter_combo.blockSignals(True)
        self._survey_name_edit.setText(survey.name)
        self._survey_filter_combo.setCurrentText(survey.filter_name)
        self._survey_name_edit.blockSignals(False)
        self._survey_filter_combo.blockSignals(False)
        self._refresh_overlay_list()

    def _apply_survey_detail_edits(self) -> None:
        survey = self._selected_survey()
        if survey is None:
            return
        index = self._survey_index(survey.survey_id)
        if index is None:
            return
        name = self._survey_name_edit.text().strip() or survey.name
        filter_name = self._survey_filter_combo.currentText().strip()
        self._surveys[index] = SkyAtlasCustomOverlaySurvey(
            survey_id=survey.survey_id,
            name=name,
            filter_name=filter_name,
            overlays=list(survey.overlays),
        )
        current_item = self._survey_list.currentItem()
        if current_item is not None:
            current_item.setText(_survey_list_label(self._surveys[index]))

    def _add_survey(self) -> None:
        self._apply_survey_detail_edits()
        survey_id = uuid.uuid4().hex
        survey_number = len(self._surveys) + 1
        survey = SkyAtlasCustomOverlaySurvey(
            survey_id=survey_id,
            name=f"Survey {survey_number}",
            filter_name="",
            overlays=[],
        )
        self._surveys.append(survey)
        self._refresh_survey_list(select_survey_id=survey_id)
        self._handle_survey_selection_changed(self._survey_list.currentItem(), None)

    def _remove_selected_survey(self) -> None:
        survey = self._selected_survey()
        if survey is None:
            return
        if survey.overlays:
            answer = QMessageBox.question(
                self,
                "Delete survey",
                f"Delete survey “{survey.name}” and its {len(survey.overlays)} image(s)?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        index = self._survey_index(survey.survey_id)
        if index is None:
            return
        del self._surveys[index]
        self._refresh_survey_list()
        if self._survey_list.count() > 0:
            self._survey_list.setCurrentRow(min(index, self._survey_list.count() - 1))
        else:
            self._handle_survey_selection_changed(None, None)

    def _browse_for_overlay_image(self) -> None:
        if self._import_worker is not None and self._import_worker.isRunning():
            QMessageBox.information(
                self,
                "Import in progress",
                "A custom sky overlay import is already running.",
            )
            return
        survey = self._selected_survey()
        if survey is None:
            QMessageBox.information(self, "No survey selected", "Create or select a survey first.")
            return
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "Select sky map overlay image",
            str(Path.home()),
            SKY_ATLAS_OVERLAY_FILE_FILTER,
        )
        if not selected:
            return
        source_path = Path(selected).expanduser()
        if not is_sky_atlas_overlay_path(source_path):
            QMessageBox.warning(
                self,
                "Unsupported image",
                "Sky Atlas custom overlays support PNG, XISF, TIFF, and FITS images only.",
            )
            return
        self._pending_import_survey_id = survey.survey_id
        progress_dialog = QProgressDialog("Preparing custom sky overlay...", "", 0, 0, self)
        progress_dialog.setWindowTitle("Sky Atlas")
        progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        progress_dialog.setMinimumDuration(0)
        progress_dialog.setCancelButton(None)
        progress_dialog.setRange(0, 0)
        progress_dialog.show()
        self._progress_dialog = progress_dialog
        self._import_worker = SkyAtlasCustomOverlayImportWorker(
            source_path=source_path,
            settings=self._settings,
            survey_id=survey.survey_id,
            parent=self,
        )
        self._import_worker.progress_updated.connect(self._handle_import_progress)
        self._import_worker.import_completed.connect(self._handle_import_completed)
        self._import_worker.import_failed.connect(self._handle_import_failed)
        self._import_worker.start()

    def _close_progress_dialog(self) -> None:
        if self._progress_dialog is None:
            return
        self._progress_dialog.close()
        self._progress_dialog.deleteLater()
        self._progress_dialog = None

    def _handle_import_progress(self, message: str) -> None:
        if self._progress_dialog is not None and message:
            self._progress_dialog.setLabelText(message)

    def _handle_import_completed(self, record: object) -> None:
        self._import_worker = None
        self._close_progress_dialog()
        if not isinstance(record, SkyAtlasCustomOverlayRecord):
            QMessageBox.warning(self, "Custom overlay failed", "Import returned an unexpected result.")
            self._pending_import_survey_id = None
            return
        survey_id = self._pending_import_survey_id
        self._pending_import_survey_id = None
        index = self._survey_index(survey_id)
        if index is None:
            return
        survey = self._surveys[index]
        self._surveys[index] = SkyAtlasCustomOverlaySurvey(
            survey_id=survey.survey_id,
            name=survey.name,
            filter_name=survey.filter_name,
            overlays=[*survey.overlays, record],
        )
        persist_survey_to_disk(self._surveys[index])
        if self._selected_survey_id() == survey.survey_id:
            self._refresh_overlay_list()

    def _handle_import_failed(self, message: str) -> None:
        self._import_worker = None
        self._pending_import_survey_id = None
        self._close_progress_dialog()
        QMessageBox.warning(self, "Custom overlay failed", message)

    def _remove_selected_overlay(self) -> None:
        survey = self._selected_survey()
        if survey is None:
            return
        current_item = self._overlay_list.currentItem()
        if current_item is None:
            return
        overlay_id = current_item.data(Qt.ItemDataRole.UserRole)
        index = self._survey_index(survey.survey_id)
        if index is None:
            return
        remaining = [record for record in survey.overlays if record.overlay_id != overlay_id]
        self._surveys[index] = SkyAtlasCustomOverlaySurvey(
            survey_id=survey.survey_id,
            name=survey.name,
            filter_name=survey.filter_name,
            overlays=remaining,
        )
        self._refresh_overlay_list()

    def _handle_accept(self) -> None:
        self._apply_survey_detail_edits()
        empty_names = [survey for survey in self._surveys if not str(survey.name or "").strip()]
        if empty_names:
            QMessageBox.warning(self, "Survey name required", "Every survey needs a name.")
            return
        self.accept()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._import_worker is not None and self._import_worker.isRunning():
            QMessageBox.information(
                self,
                "Import in progress",
                "Wait for the overlay import to finish before closing.",
            )
            event.ignore()
            return
        super().closeEvent(event)
