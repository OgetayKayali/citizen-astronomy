from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from photometry_app.core.sky_explorer import (
    SkyExplorerStarlessWcsChoice,
    SkyExplorerStarlessWcsPrompt,
    SkyExplorerWcsCandidate,
    sky_explorer_external_wcs_error,
)


_WCS_FILE_FILTER = "FITS and XISF (*.fits *.fit *.fts *.fz *.xisf);;All files (*.*)"


def format_sky_explorer_wcs_candidate(candidate: SkyExplorerWcsCandidate) -> str:
    if (
        candidate.width_arcmin is not None
        and candidate.height_arcmin is not None
        and candidate.width_arcmin > 0.0
        and candidate.height_arcmin > 0.0
    ):
        size = f"{candidate.width_arcmin:.1f} × {candidate.height_arcmin:.1f} arcmin"
    else:
        size = f"{candidate.radius_deg * 60.0:.1f} arcmin across"
    return (
        f"This image: RA {candidate.center_ra_deg:.4f}°, Dec {candidate.center_dec_deg:.4f}°, {size}."
    )


class SkyExplorerStarlessWcsDialog(QDialog):
    """Ask how to place catalogs when a frame has a WCS but almost no stars."""

    def __init__(self, prompt: SkyExplorerStarlessWcsPrompt, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._prompt = prompt
        self._choice = SkyExplorerStarlessWcsChoice(action="cancel")
        self.setWindowTitle("This image looks starless")
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        intro = QLabel(
            "Catalogs can still be drawn from an existing WCS. "
            "This image does not contain stars CAst can plate-solve.",
            self,
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        candidate = prompt.embedded_candidate
        if candidate is not None:
            candidate_label = QLabel(format_sky_explorer_wcs_candidate(candidate), self)
            candidate_label.setWordWrap(True)
            layout.addWidget(candidate_label)
            if candidate.staralignment_sidecar:
                caution = QLabel(
                    "This file has a PixInsight StarAlignment sidecar. The WCS may not match the pixels.",
                    self,
                )
                caution.setWordWrap(True)
                layout.addWidget(caution)

        self._notice = QLabel(prompt.companion_unavailable_note or "", self)
        self._notice.setWordWrap(True)
        self._notice.setVisible(bool(prompt.companion_unavailable_note))
        layout.addWidget(self._notice)

        self._use_embedded_button = QPushButton("Use this image's WCS", self)
        self._use_embedded_button.setEnabled(candidate is not None)
        self._use_embedded_button.clicked.connect(self._accept_embedded)

        self._use_file_button = QPushButton("Use WCS from another file…", self)
        self._use_file_button.clicked.connect(self._pick_wcs_file)

        self._plate_solve_button = QPushButton("Plate-solve anyway", self)
        self._plate_solve_button.clicked.connect(self._accept_plate_solve)

        self._cancel_button = QPushButton("Cancel", self)
        self._cancel_button.clicked.connect(self.reject)

        if candidate is not None:
            self._use_embedded_button.setDefault(True)
            self._use_embedded_button.setFocus()
        else:
            self._use_file_button.setDefault(True)
            self._use_file_button.setFocus()

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addWidget(self._use_embedded_button)
        buttons.addWidget(self._use_file_button)
        buttons.addWidget(self._plate_solve_button)
        buttons.addStretch(1)
        buttons.addWidget(self._cancel_button)
        layout.addLayout(buttons)

    def choice(self) -> SkyExplorerStarlessWcsChoice:
        return self._choice

    def _accept_embedded(self) -> None:
        self._choice = SkyExplorerStarlessWcsChoice(action="use_embedded")
        self.accept()

    def _accept_plate_solve(self) -> None:
        self._choice = SkyExplorerStarlessWcsChoice(action="plate_solve")
        self.accept()

    def _pick_wcs_file(self) -> None:
        start_dir = str(self._prompt.image_path.parent) if self._prompt.image_path.parent else ""
        selected, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Choose a solved image",
            start_dir,
            _WCS_FILE_FILTER,
        )
        if not selected:
            return
        wcs_path = Path(selected)
        error = sky_explorer_external_wcs_error(self._prompt.image_width, self._prompt.image_height, wcs_path)
        if error:
            self._notice.setText(error)
            self._notice.setVisible(True)
            return
        self._choice = SkyExplorerStarlessWcsChoice(action="use_file", wcs_path=wcs_path)
        self.accept()
