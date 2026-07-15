from __future__ import annotations

import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from astropy.io import fits
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from photometry_app.core.models import ObservationMetadata, SolvedField, WcsStatus
from photometry_app.core.transient import TransientCandidate, TransientFrameResult, TransientSourceDetection
from photometry_app.ui.transient_label_dialog import TransientQuickLabelDialog


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class TransientQuickLabelDialogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_dialog_blinks_cutouts_and_saves_label_then_advances(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root_path = Path(temp_dir)
            first_path = root_path / "frame_1.fit"
            second_path = root_path / "frame_2.fit"
            header = self._wcs_header()
            fits.PrimaryHDU(data=self._image_with_source(None), header=header).writeto(first_path)
            fits.PrimaryHDU(data=self._image_with_source((31.0, 30.0, 900.0)), header=header).writeto(second_path)
            candidate = self._candidate(first_path, second_path)
            frame_results = tuple(self._frame_result(path) for path in (first_path, second_path))
            saved: list[tuple[str, str]] = []

            dialog = TransientQuickLabelDialog(
                candidates=(candidate,),
                frame_results=frame_results,
                label_lookup=lambda _candidate: None,
                save_label=lambda labeled_candidate, label: saved.append((labeled_candidate.candidate_id, label)),
            )

            cutouts = dialog._candidate_cutouts(candidate)

            self.assertEqual(len(cutouts), 2)
            self.assertFalse(cutouts[0].isNull())
            speed_index = dialog._blink_speed_combo.findData(180)
            self.assertGreaterEqual(speed_index, 0)
            dialog._blink_speed_combo.setCurrentIndex(speed_index)
            self.assertEqual(dialog._blink_timer.interval(), 180)
            label_buttons = dialog.findChildren(type(dialog._skip_button))
            self.assertTrue(any(button.text() == "Real" and button.focusPolicy() == Qt.FocusPolicy.NoFocus for button in label_buttons))
            dialog._save_current_label("real")
            self.assertEqual(saved, [("TF-001", "real")])
            self.assertEqual(dialog.saved_count, 1)

            dialog.close()

    def _wcs_header(self) -> fits.Header:
        header = fits.Header()
        header["DATE-OBS"] = datetime(2026, 5, 1, 3, 0, tzinfo=UTC).isoformat()
        header["EXPTIME"] = 60.0
        header["NAXIS"] = 2
        header["NAXIS1"] = 64
        header["NAXIS2"] = 64
        header["CTYPE1"] = "RA---TAN"
        header["CTYPE2"] = "DEC--TAN"
        header["CRVAL1"] = 100.0
        header["CRVAL2"] = 20.0
        header["CRPIX1"] = 32.0
        header["CRPIX2"] = 32.0
        header["CD1_1"] = -1.0 / 3600.0
        header["CD1_2"] = 0.0
        header["CD2_1"] = 0.0
        header["CD2_2"] = 1.0 / 3600.0
        return header

    def _image_with_source(self, source: tuple[float, float, float] | None) -> np.ndarray:
        image = np.full((64, 64), 100.0, dtype=np.float32)
        if source is not None:
            x, y, amplitude = source
            yy, xx = np.indices(image.shape)
            image += amplitude * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2.0 * 1.15 ** 2))
        return image

    def _candidate(self, first_path: Path, second_path: Path) -> TransientCandidate:
        detection = TransientSourceDetection(
            source_path=second_path,
            observation_time=datetime(2026, 5, 1, 3, 1, tzinfo=UTC),
            x=31.0,
            y=30.0,
            ra_deg=100.0,
            dec_deg=20.0,
            snr=25.0,
            flux=2500.0,
            peak_value=900.0,
        )
        return TransientCandidate(
            candidate_id="TF-001",
            ra_deg=100.0,
            dec_deg=20.0,
            frame_count=1,
            detection_count=1,
            first_observation=detection.observation_time,
            last_observation=detection.observation_time,
            median_snr=25.0,
            max_snr=25.0,
            nearest_catalog_name=None,
            nearest_catalog_separation_arcsec=None,
            detections=(detection,),
            summary_text="TF-001 synthetic candidate",
            variability_snr=12.0,
            flux_ratio=4.0,
            blink_paths=(first_path, second_path),
        )

    def _frame_result(self, path: Path) -> TransientFrameResult:
        return TransientFrameResult(
            source_path=path,
            metadata=ObservationMetadata(
                date_obs=datetime(2026, 5, 1, 3, 0, tzinfo=UTC),
                filter_name="L",
                exposure_seconds=60.0,
                width=64,
                height=64,
                object_name="Transient Field",
            ),
            status=WcsStatus.SOLVED,
            solved_field=SolvedField(100.0, 20.0, 0.02, 64, 64, path),
            wcs_path=path,
        )


if __name__ == "__main__":
    unittest.main()