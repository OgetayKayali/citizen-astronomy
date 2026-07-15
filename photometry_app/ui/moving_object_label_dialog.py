from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from photometry_app.core.discovery import MovingObjectCandidate, MovingObjectCandidateDetection
from photometry_app.core.image_io import read_photometry_image_data


_BLINK_SPEED_OPTIONS_MS: tuple[tuple[str, int], ...] = (
    ("Slow", 700),
    ("Normal", 350),
    ("Fast", 180),
    ("Very Fast", 100),
)


class MovingObjectQuickLabelDialog(QDialog):
    def __init__(
        self,
        *,
        candidates: Sequence[MovingObjectCandidate],
        label_options: Sequence[tuple[str, str]],
        label_lookup: Callable[[MovingObjectCandidate], str | None],
        save_label: Callable[[MovingObjectCandidate, str], None],
        select_candidate: Callable[[MovingObjectCandidate], None] | None = None,
        start_candidate_id: str | None = None,
        cutout_size: int = 64,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._candidates = list(candidates)
        self._label_options = tuple(label_options)
        self._label_lookup = label_lookup
        self._save_label = save_label
        self._select_candidate = select_candidate
        self._cutout_size = max(24, int(cutout_size))
        self._current_candidate_index = self._initial_candidate_index(start_candidate_id)
        self._current_frame_index = 0
        self._saved_count = 0
        self._cutout_cache: dict[str, list[QPixmap]] = {}

        self.setWindowTitle("Label Discover Candidates")
        self.resize(580, 680)

        self._candidate_label = QLabel()
        self._candidate_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._candidate_label.setWordWrap(True)
        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setMinimumSize(440, 440)
        self._image_label.setStyleSheet("background: #0b0f14; border: 1px solid #30363d;")
        self._frame_label = QLabel()
        self._frame_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._frame_label.setWordWrap(True)

        self._blink_button = QPushButton("Auto Blink")
        self._blink_button.setCheckable(True)
        self._blink_button.setChecked(True)
        self._blink_button.toggled.connect(self._handle_blink_toggled)
        self._blink_speed_combo = QComboBox()
        self._blink_speed_combo.setToolTip("Choose how quickly moving-candidate cutouts blink while labeling.")
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
        for button_text, label_value in self._label_options:
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

    def _initial_candidate_index(self, start_candidate_id: str | None) -> int:
        if not self._candidates or not start_candidate_id:
            return 0
        for index, candidate in enumerate(self._candidates):
            if candidate.candidate_id == start_candidate_id:
                return index
        return 0

    def _current_candidate(self) -> MovingObjectCandidate | None:
        if not self._candidates:
            return None
        if self._current_candidate_index < 0 or self._current_candidate_index >= len(self._candidates):
            return None
        return self._candidates[self._current_candidate_index]

    def _refresh_candidate(self) -> None:
        candidate = self._current_candidate()
        if candidate is None:
            self._blink_timer.stop()
            self._candidate_label.setText("No Discover candidates available for labeling.")
            self._image_label.setText("")
            self._frame_label.setText("")
            return
        self._current_frame_index = 0
        if self._select_candidate is not None:
            self._select_candidate(candidate)
        current_label = self._label_lookup(candidate)
        label_text = f"Current label: {self._format_label(current_label)}" if current_label else "Unlabeled"
        self._candidate_label.setText(
            f"{candidate.candidate_id} ({self._current_candidate_index + 1}/{len(self._candidates)})  "
            f"Frames {len(candidate.frame_detections)}  Motion {candidate.motion_px_per_hour:.2f} px/h  {label_text}"
        )
        self._refresh_frame()
        self._handle_blink_toggled(self._blink_button.isChecked())

    def _refresh_frame(self) -> None:
        candidate = self._current_candidate()
        if candidate is None:
            return
        cutouts = self._candidate_cutouts(candidate)
        detections = self._candidate_detections(candidate)
        if not cutouts or not detections:
            self._image_label.setText("Could not load cutout frames for this candidate.")
            self._frame_label.setText("")
            self._blink_timer.stop()
            return
        self._current_frame_index %= len(cutouts)
        detection = detections[self._current_frame_index]
        self._image_label.setPixmap(cutouts[self._current_frame_index])
        self._frame_label.setText(
            f"Frame {self._current_frame_index + 1}/{len(cutouts)}  "
            f"{detection.source_path.name}  SNR {detection.local_snr:.2f}"
        )

    def _candidate_detections(self, candidate: MovingObjectCandidate) -> list[MovingObjectCandidateDetection]:
        return sorted(candidate.frame_detections, key=lambda detection: (int(detection.frame_index), detection.source_path.name))

    def _candidate_cutouts(self, candidate: MovingObjectCandidate) -> list[QPixmap]:
        cached = self._cutout_cache.get(candidate.candidate_id)
        if cached is not None:
            return cached
        pixmaps: list[QPixmap] = []
        for detection in self._candidate_detections(candidate):
            try:
                if not detection.source_path.exists():
                    continue
                pixmaps.append(self._render_cutout_pixmap(detection.source_path, (detection.x, detection.y)))
            except Exception:
                continue
        self._cutout_cache[candidate.candidate_id] = pixmaps
        return pixmaps

    def _render_cutout_pixmap(self, frame_path: Path, center: tuple[float, float]) -> QPixmap:
        data = np.asarray(read_photometry_image_data(frame_path, dtype=float), dtype=float)
        if data.ndim == 3:
            if data.shape[-1] in {3, 4}:
                data = np.nanmean(data[:, :, :3], axis=2)
            else:
                data = np.squeeze(data)
        if data.ndim != 2:
            raise ValueError("Moving-candidate quick-label cutouts require a 2D image.")
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
        _draw_target_marker(pixmap)
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


def _draw_target_marker(pixmap: QPixmap) -> None:
    painter = QPainter(pixmap)
    try:
        outline_pen = QPen(QColor("#ffffff"))
        outline_pen.setWidth(4)
        painter.setPen(outline_pen)
        _draw_target_shape(painter, pixmap)

        pen = QPen(QColor("#ef4444"))
        pen.setWidth(2)
        painter.setPen(pen)
        _draw_target_shape(painter, pixmap)
    finally:
        painter.end()


def _draw_target_shape(painter: QPainter, pixmap: QPixmap) -> None:
    center_x = pixmap.width() // 2
    center_y = pixmap.height() // 2
    half_box = max(18, min(pixmap.width(), pixmap.height()) // 10)
    left = center_x - half_box
    top = center_y - half_box
    size = half_box * 2
    margin = 8
    painter.drawRect(left, top, size, size)
    painter.drawLine(margin, center_y, left - 6, center_y)
    painter.drawLine(left + size + 6, center_y, pixmap.width() - margin, center_y)
    painter.drawLine(center_x, margin, center_x, top - 6)
    painter.drawLine(center_x, top + size + 6, center_x, pixmap.height() - margin)
