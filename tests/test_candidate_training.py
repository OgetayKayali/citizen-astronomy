from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from photometry_app.core.candidate_training import (
    CandidateTrainingStore,
    TRAINING_MODE_TRANSIENT,
    moving_object_candidate_training_features,
    moving_object_candidate_training_key,
    transient_candidate_training_features,
    transient_candidate_training_key,
)
from photometry_app.core.discovery import MovingObjectCandidate, MovingObjectCandidateDetection
from photometry_app.core.transient import TransientCandidate, TransientSourceDetection


class CandidateTrainingStoreTest(unittest.TestCase):
    def test_records_labels_trains_model_and_predicts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = CandidateTrainingStore(Path(temp_dir) / "training.sqlite3")

            for index in range(8):
                is_real = index < 4
                label = "real" if is_real else "artifact"
                features = {
                    "median_snr": 30.0 + index if is_real else 3.0 + index,
                    "max_snr": 35.0 + index if is_real else 4.0 + index,
                    "nearest_catalog_separation_arcsec": 9999.0 if is_real else 0.8,
                    "has_catalog_neighbor": 0.0 if is_real else 1.0,
                }
                store.record_label(
                    mode=TRAINING_MODE_TRANSIENT,
                    candidate_key=f"candidate-{index}",
                    features=features,
                    payload={"candidate_id": f"T{index:03d}"},
                    label=label,
                )

            record = store.label_for_key(TRAINING_MODE_TRANSIENT, "candidate-0")
            self.assertIsNotNone(record)
            assert record is not None
            self.assertEqual(record.label, "real")

            result = store.train_model(TRAINING_MODE_TRANSIENT)

            self.assertTrue(result.trained)
            self.assertEqual(result.example_count, 8)
            self.assertEqual(result.class_counts, {"artifact": 4, "real": 4})
            prediction = store.predict(
                TRAINING_MODE_TRANSIENT,
                {
                    "median_snr": 32.0,
                    "max_snr": 38.0,
                    "nearest_catalog_separation_arcsec": 9999.0,
                    "has_catalog_neighbor": 0.0,
                },
            )
            self.assertIsNotNone(prediction)
            assert prediction is not None
            self.assertIn(prediction.label, {"artifact", "real"})
            self.assertGreaterEqual(prediction.confidence, 0.0)
            self.assertLessEqual(prediction.confidence, 1.0)

    def test_transient_candidate_features_are_numeric_and_key_is_stable(self) -> None:
        root_path = Path("example")
        candidate = self._candidate()

        features = transient_candidate_training_features(candidate)
        first_key = transient_candidate_training_key(root_path, candidate)
        second_key = transient_candidate_training_key(root_path, candidate)

        self.assertEqual(first_key, second_key)
        self.assertEqual(features["frame_count"], 2.0)
        self.assertEqual(features["detection_count"], 2.0)
        self.assertAlmostEqual(features["detection_fraction"], 1.0)
        self.assertGreater(features["observation_span_seconds"], 0.0)
        self.assertTrue(all(isinstance(value, float) for value in features.values()))

    def test_moving_object_candidate_features_are_numeric_and_key_is_stable(self) -> None:
        root_path = Path("example")
        candidate = self._moving_object_candidate("C001", snr=9.0, motion=4.5)

        features = moving_object_candidate_training_features(candidate)
        first_key = moving_object_candidate_training_key(root_path, candidate)
        second_key = moving_object_candidate_training_key(root_path, candidate)

        self.assertEqual(first_key, second_key)
        self.assertEqual(features["detection_count"], 3.0)
        self.assertEqual(features["frame_index_span"], 3.0)
        self.assertAlmostEqual(features["linked_frame_fraction"], 1.0)
        self.assertAlmostEqual(features["motion_px_per_hour"], 4.5)
        self.assertGreater(features["observation_span_seconds"], 0.0)
        self.assertTrue(all(isinstance(value, float) for value in features.values()))

    def _candidate(self) -> TransientCandidate:
        start = datetime(2026, 5, 1, 3, 0, tzinfo=UTC)
        detections = tuple(
            TransientSourceDetection(
                source_path=Path(f"frame_{index}.fit"),
                observation_time=start + timedelta(minutes=index),
                x=30.0 + index,
                y=32.0,
                ra_deg=120.0,
                dec_deg=22.0,
                snr=12.0 + index,
                flux=500.0 + index * 20.0,
                peak_value=180.0 + index,
            )
            for index in range(2)
        )
        return TransientCandidate(
            candidate_id="T001",
            ra_deg=120.0,
            dec_deg=22.0,
            frame_count=2,
            detection_count=2,
            first_observation=start,
            last_observation=start + timedelta(minutes=1),
            median_snr=12.5,
            max_snr=13.0,
            nearest_catalog_name=None,
            nearest_catalog_separation_arcsec=None,
            detections=detections,
            summary_text="Candidate T001",
            variability_snr=8.0,
            flux_ratio=2.5,
        )

    def _moving_object_candidate(self, candidate_id: str, *, snr: float, motion: float) -> MovingObjectCandidate:
        start = datetime(2026, 5, 1, 3, 0, tzinfo=UTC)
        detections = tuple(
            MovingObjectCandidateDetection(
                source_path=Path(f"frame_{index}.fit"),
                observation_time=start + timedelta(minutes=index),
                frame_index=index,
                x=20.0 + index * motion / 60.0,
                y=30.0 + index * 0.2,
                peak_value=100.0 + index,
                local_snr=snr + index,
                ra_deg=120.0 + index * 0.001,
                dec_deg=22.0,
            )
            for index in range(3)
        )
        return MovingObjectCandidate(
            candidate_id=candidate_id,
            frame_detections=detections,
            average_snr=snr,
            peak_value=120.0,
            fit_rms_px=0.25,
            motion_px_per_hour=motion,
            motion_arcsec_per_hour=motion * 1.5,
            displacement_px=motion / 20.0,
            start_x=detections[0].x,
            start_y=detections[0].y,
            end_x=detections[-1].x,
            end_y=detections[-1].y,
            summary_text=f"Candidate {candidate_id}",
        )


if __name__ == "__main__":
    unittest.main()