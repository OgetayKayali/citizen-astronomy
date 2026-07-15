from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from astropy import units as u
from astropy.coordinates import SkyCoord
import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from photometry_app.core.image_io import read_header, read_photometry_image_data
from photometry_app.core.transient import TransientCandidate, TransientFrameResult
from photometry_app.core.wcs import celestial_wcs


TRANSIENT_QUICK_LABEL_OPTIONS: tuple[tuple[str, str], ...] = (
    ("Real", "real"),
    ("Artifact", "artifact"),
    ("Known Object", "known_object"),
    ("Moving Object", "moving_object"),
    ("Noise", "noise"),
    ("Unsure", "unsure"),
)

_BLINK_SPEED_OPTIONS_MS: tuple[tuple[str, int], ...] = (
    ("Slow", 700),
    ("Normal", 350),
    ("Fast", 180),
    ("Very Fast", 100),
)


class TransientQuickLabelDialog(QDialog):
    def __init__(
        self,
        *,
        candidates: Sequence[TransientCandidate],
        frame_results: Sequence[TransientFrameResult],
        label_lookup: Callable[[TransientCandidate], str | None],
        save_label: Callable[[TransientCandidate, str], None],
        cutout_size: int = 50,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._candidates = list(candidates)
        self._label_lookup = label_lookup
        self._save_label = save_label
        self._cutout_size = max(16, int(cutout_size))
        self._current_candidate_index = 0
        self._current_frame_index = 0
        self._saved_count = 0
        self._cutout_cache: dict[tuple[str, str], list[QPixmap]] = {}
        self._frame_wcs_paths = {
            str(frame_result.source_path.resolve()): frame_result.wcs_path
            for frame_result in frame_results
            if frame_result.wcs_path is not None and frame_result.wcs_path.exists()
        }

        self.setWindowTitle("Label Transient Candidates")
        self.resize(560, 640)

        self._candidate_label = QLabel()
        self._candidate_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._candidate_label.setWordWrap(True)
        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setMinimumSize(420, 420)
        self._image_label.setStyleSheet("background: #0b0f14; border: 1px solid #30363d;")
        self._frame_label = QLabel()
        self._frame_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._frame_label.setWordWrap(True)

        self._blink_button = QPushButton("Auto Blink")
        self._blink_button.setCheckable(True)
        self._blink_button.setChecked(True)
        self._blink_button.toggled.connect(self._handle_blink_toggled)
        self._blink_speed_combo = QComboBox()
        self._blink_speed_combo.setToolTip("Choose how quickly candidate cutouts blink while labeling.")
        for speed_label, interval_ms in _BLINK_SPEED_OPTIONS_MS:
            self._blink_speed_combo.addItem(speed_label, interval_ms)
        self._blink_speed_combo.setCurrentIndex(1)
        self._blink_speed_combo.currentIndexChanged.connect(self._handle_blink_speed_changed)
        self._previous_button = QPushButton("Previous")
        self._previous_button.clicked.connect(self._show_previous_candidate)
        self._skip_button = QPushButton("Skip")
        self._skip_button.clicked.connect(self._show_next_candidate)
        self._close_button = QPushButton("Close")
        self._close_button.clicked.connect(self.reject)

        label_row = QHBoxLayout()
        label_row.setSpacing(6)
        for button_text, label_value in TRANSIENT_QUICK_LABEL_OPTIONS:
            button = QPushButton(button_text)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(lambda _checked=False, value=label_value: self._save_current_label(value))
            label_row.addWidget(button)

        controls_row = QHBoxLayout()
        controls_row.setSpacing(6)
        controls_row.addWidget(self._previous_button)
        controls_row.addWidget(self._skip_button)
        controls_row.addStretch(1)
        controls_row.addWidget(self._blink_button)
        controls_row.addWidget(self._blink_speed_combo)
        controls_row.addWidget(self._close_button)

        layout = QVBoxLayout()
        layout.addWidget(self._candidate_label)
        layout.addWidget(self._image_label, stretch=1)
        layout.addWidget(self._frame_label)
        layout.addLayout(label_row)
        layout.addLayout(controls_row)
        self.setLayout(layout)

        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(int(self._blink_speed_combo.currentData() or 350))
        self._blink_timer.timeout.connect(self._advance_frame)

        self._refresh_candidate()

    @property
    def saved_count(self) -> int:
        return self._saved_count

    def accept(self) -> None:  # type: ignore[override]
        self._blink_timer.stop()
        super().accept()

    def reject(self) -> None:  # type: ignore[override]
        self._blink_timer.stop()
        super().reject()

    def _current_candidate(self) -> TransientCandidate | None:
        if not self._candidates:
            return None
        if self._current_candidate_index < 0 or self._current_candidate_index >= len(self._candidates):
            return None
        return self._candidates[self._current_candidate_index]

    def _refresh_candidate(self) -> None:
        candidate = self._current_candidate()
        if candidate is None:
            self._blink_timer.stop()
            self._candidate_label.setText("No transient candidates available for labeling.")
            self._image_label.setText("")
            self._frame_label.setText("")
            return
        self._current_frame_index = 0
        current_label = self._label_lookup(candidate)
        label_text = f"Current label: {self._format_label(current_label)}" if current_label else "Unlabeled"
        self._candidate_label.setText(
            f"{candidate.candidate_id} ({self._current_candidate_index + 1}/{len(self._candidates)})  "
            f"RA {candidate.ra_deg:.6f}, Dec {candidate.dec_deg:.6f}  {label_text}"
        )
        self._refresh_frame()
        self._handle_blink_toggled(self._blink_button.isChecked())

    def _refresh_frame(self) -> None:
        candidate = self._current_candidate()
        if candidate is None:
            return
        cutouts = self._candidate_cutouts(candidate)
        if not cutouts:
            self._image_label.setText("Could not load cutout frames for this candidate.")
            self._frame_label.setText("")
            self._blink_timer.stop()
            return
        self._current_frame_index %= len(cutouts)
        self._image_label.setPixmap(cutouts[self._current_frame_index])
        self._frame_label.setText(f"Frame {self._current_frame_index + 1}/{len(cutouts)}")

    def _candidate_cutouts(self, candidate: TransientCandidate) -> list[QPixmap]:
        cache_key = (candidate.candidate_id, ";".join(str(path.resolve()) for path in self._candidate_frame_paths(candidate)))
        cached = self._cutout_cache.get(cache_key)
        if cached is not None:
            return cached
        pixmaps: list[QPixmap] = []
        for frame_path in self._candidate_frame_paths(candidate):
            try:
                center = self._candidate_center_for_frame(candidate, frame_path)
                if center is None:
                    continue
                pixmaps.append(self._render_cutout_pixmap(frame_path, center))
            except Exception:
                continue
        self._cutout_cache[cache_key] = pixmaps
        return pixmaps

    def _candidate_frame_paths(self, candidate: TransientCandidate) -> list[Path]:
        paths: list[Path] = []
        seen: set[Path] = set()
        source_paths = candidate.blink_paths or tuple(detection.source_path for detection in candidate.detections)
        for path in source_paths:
            if not path.exists():
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            paths.append(path)
        return paths

    def _candidate_center_for_frame(self, candidate: TransientCandidate, frame_path: Path) -> tuple[float, float] | None:
        frame_key = str(frame_path.resolve())
        for detection in candidate.detections:
            if str(detection.source_path.resolve()) == frame_key:
                return detection.x, detection.y
        wcs_path = self._frame_wcs_paths.get(frame_key)
        if wcs_path is None:
            return None
        header = read_header(wcs_path)
        wcs = celestial_wcs(header)
        coordinate = SkyCoord(candidate.ra_deg * u.deg, candidate.dec_deg * u.deg)
        x_value, y_value = wcs.world_to_pixel(coordinate)
        return float(np.asarray(x_value).reshape(-1)[0]), float(np.asarray(y_value).reshape(-1)[0])

    def _render_cutout_pixmap(self, frame_path: Path, center: tuple[float, float]) -> QPixmap:
        data = np.asarray(read_photometry_image_data(frame_path, dtype=float), dtype=float)
        if data.ndim == 3:
            if data.shape[-1] in {3, 4}:
                data = np.nanmean(data[:, :, :3], axis=2)
            else:
                data = np.squeeze(data)
        if data.ndim != 2:
            raise ValueError("Transient quick-label cutouts require a 2D image.")
        cutout = _centered_cutout(data, center[0], center[1], self._cutout_size)
        display = _normalize_cutout(cutout)
        display = np.ascontiguousarray(display)
        height, width = display.shape
        image = QImage(display.data, width, height, display.strides[0], QImage.Format.Format_Grayscale8).copy()
        pixmap = QPixmap.fromImage(image).scaled(
            self._image_label.minimumWidth(),
            self._image_label.minimumHeight(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        _draw_center_marker(pixmap)
        return pixmap

    def _advance_frame(self) -> None:
        candidate = self._current_candidate()
        if candidate is None:
            return
        cutouts = self._candidate_cutouts(candidate)
        if len(cutouts) <= 1:
            return
        self._current_frame_index = (self._current_frame_index + 1) % len(cutouts)
        self._refresh_frame()

    def _handle_blink_toggled(self, checked: bool) -> None:
        candidate = self._current_candidate()
        if candidate is None:
            self._blink_timer.stop()
            return
        if checked and len(self._candidate_cutouts(candidate)) > 1:
            self._blink_timer.start()
        else:
            self._blink_timer.stop()

    def _handle_blink_speed_changed(self, _index: int) -> None:
        interval_ms = self._blink_speed_combo.currentData()
        if not isinstance(interval_ms, int):
            interval_ms = 350
        was_active = self._blink_timer.isActive()
        self._blink_timer.setInterval(max(50, int(interval_ms)))
        if was_active:
            self._blink_timer.start()

    def _save_current_label(self, label: str) -> None:
        self.clearFocus()
        candidate = self._current_candidate()
        if candidate is None:
            return
        self._save_label(candidate, label)
        self._saved_count += 1
        self._show_next_candidate()

    def _show_next_candidate(self) -> None:
        if not self._candidates:
            self.accept()
            return
        if self._current_candidate_index >= len(self._candidates) - 1:
            self.accept()
            return
        self._current_candidate_index += 1
        self._refresh_candidate()

    def _show_previous_candidate(self) -> None:
        if not self._candidates:
            return
        self._current_candidate_index = max(0, self._current_candidate_index - 1)
        self._refresh_candidate()

    def _format_label(self, label: str | None) -> str:
        if not label:
            return ""
        return str(label).replace("_", " ").title()


def _centered_cutout(data: np.ndarray, x: float, y: float, size: int) -> np.ndarray:
    finite = data[np.isfinite(data)]
    fill_value = float(np.nanmedian(finite)) if finite.size else 0.0
    cutout = np.full((size, size), fill_value, dtype=float)
    center_x = int(round(float(x)))
    center_y = int(round(float(y)))
    half = size // 2
    raw_x0 = center_x - half
    raw_y0 = center_y - half
    raw_x1 = raw_x0 + size
    raw_y1 = raw_y0 + size
    source_x0 = max(0, raw_x0)
    source_y0 = max(0, raw_y0)
    source_x1 = min(data.shape[1], raw_x1)
    source_y1 = min(data.shape[0], raw_y1)
    target_x0 = source_x0 - raw_x0
    target_y0 = source_y0 - raw_y0
    target_x1 = target_x0 + max(0, source_x1 - source_x0)
    target_y1 = target_y0 + max(0, source_y1 - source_y0)
    if source_x1 > source_x0 and source_y1 > source_y0:
        cutout[target_y0:target_y1, target_x0:target_x1] = data[source_y0:source_y1, source_x0:source_x1]
    return cutout


def _normalize_cutout(cutout: np.ndarray) -> np.ndarray:
    finite = cutout[np.isfinite(cutout)]
    if not finite.size:
        return np.zeros(cutout.shape, dtype=np.uint8)
    low, high = np.nanpercentile(finite, [1.0, 99.5])
    if not np.isfinite(low) or not np.isfinite(high) or high <= low:
        low = float(np.nanmin(finite))
        high = float(np.nanmax(finite))
    if not np.isfinite(low) or not np.isfinite(high) or high <= low:
        return np.zeros(cutout.shape, dtype=np.uint8)
    normalized = np.clip((cutout - low) / (high - low), 0.0, 1.0)
    return np.rint(normalized * 255.0).astype(np.uint8)


def _draw_center_marker(pixmap: QPixmap) -> None:
    painter = QPainter(pixmap)
    try:
        pen = QPen(QColor("#ffd166"))
        pen.setWidth(2)
        painter.setPen(pen)
        center_x = pixmap.width() // 2
        center_y = pixmap.height() // 2
        painter.drawEllipse(center_x - 22, center_y - 22, 44, 44)
        painter.drawLine(center_x - 38, center_y, center_x - 26, center_y)
        painter.drawLine(center_x + 26, center_y, center_x + 38, center_y)
        painter.drawLine(center_x, center_y - 38, center_x, center_y - 26)
        painter.drawLine(center_x, center_y + 26, center_x, center_y + 38)
    finally:
        painter.end()