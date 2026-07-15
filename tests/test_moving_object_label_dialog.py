from __future__ import annotations

import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
from astropy.io import fits
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from photometry_app.core.discovery import MovingObjectCandidate, MovingObjectCandidateDetection
from photometry_app.ui.moving_object_label_dialog import MovingObjectQuickLabelDialog


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class MovingObjectQuickLabelDialogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_dialog_blinks_cutouts_saves_label_and_advances(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root_path = Path(temp_dir)
            first_path = root_path / "frame_1.fit"
            second_path = root_path / "frame_2.fit"
            third_path = root_path / "frame_3.fit"
            fits.PrimaryHDU(data=self._image_with_source((31.0, 30.0, 900.0))).writeto(first_path)
            fits.PrimaryHDU(data=self._image_with_source((32.0, 31.0, 960.0))).writeto(second_path)
            fits.PrimaryHDU(data=self._image_with_source((20.0, 18.0, 700.0))).writeto(third_path)

            first_candidate = self._candidate("C1", ((first_path, 0, 31.0, 30.0, 8.0), (second_path, 1, 32.0, 31.0, 9.0)))
            second_candidate = self._candidate("C2", ((third_path, 0, 20.0, 18.0, 6.5),))
            saved: list[tuple[str, str]] = []
            selected: list[str] = []

            dialog = MovingObjectQuickLabelDialog(
                candidates=(first_candidate, second_candidate),
                label_options=(("Real Mover", "real_mover"), ("Artifact", "artifact")),
                label_lookup=lambda _candidate: None,
                save_label=lambda labeled_candidate, label: saved.append((labeled_candidate.candidate_id, label)),
                select_candidate=lambda candidate: selected.append(candidate.candidate_id),
                start_candidate_id="C1",
            )

            cutouts = dialog._candidate_cutouts(first_candidate)

            self.assertEqual(len(cutouts), 2)
            self.assertFalse(cutouts[0].isNull())
            speed_index = dialog._blink_speed_combo.findData(180)
            self.assertGreaterEqual(speed_index, 0)
            dialog._blink_speed_combo.setCurrentIndex(speed_index)
            self.assertEqual(dialog._blink_timer.interval(), 180)
            label_buttons = dialog.findChildren(type(dialog._skip_button))
            self.assertTrue(any(button.text() == "Real Mover" and button.focusPolicy() == Qt.FocusPolicy.NoFocus for button in label_buttons))

            dialog._save_current_label("real_mover")

            self.assertEqual(saved, [("C1", "real_mover")])
            self.assertEqual(dialog.saved_count, 1)
            self.assertEqual(dialog._current_candidate().candidate_id, "C2")
            self.assertEqual(selected[:2], ["C1", "C2"])

            dialog.close()

    def _image_with_source(self, source: tuple[float, float, float] | None) -> np.ndarray:
        image = np.full((64, 64), 100.0, dtype=np.float32)
        if source is not None:
            x, y, amplitude = source
            yy, xx = np.indices(image.shape)
            image += amplitude * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2.0 * 1.15 ** 2))
        return image

    def _candidate(self, candidate_id: str, detections: tuple[tuple[Path, int, float, float, float], ...]) -> MovingObjectCandidate:
        frame_detections = tuple(
            MovingObjectCandidateDetection(
                source_path=path,
                observation_time=datetime(2026, 5, 1, 3, 0, tzinfo=UTC) + timedelta(minutes=index),
                frame_index=frame_index,
                x=x,
                y=y,
                peak_value=800.0 + local_snr,
                local_snr=local_snr,
            )
            for index, (path, frame_index, x, y, local_snr) in enumerate(detections)
        )
        return MovingObjectCandidate(
            candidate_id=candidate_id,
            frame_detections=frame_detections,
            average_snr=float(np.mean([detection.local_snr for detection in frame_detections])),
            peak_value=max(detection.peak_value for detection in frame_detections),
            fit_rms_px=0.2,
            motion_px_per_hour=1.5,
            motion_arcsec_per_hour=2.4,
            displacement_px=1.7,
            start_x=frame_detections[0].x,
            start_y=frame_detections[0].y,
            end_x=frame_detections[-1].x,
            end_y=frame_detections[-1].y,
            summary_text=f"{candidate_id} synthetic moving candidate",
        )


if __name__ == "__main__":
    unittest.main()