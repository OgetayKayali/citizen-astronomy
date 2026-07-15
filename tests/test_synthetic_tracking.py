from __future__ import annotations

import unittest
from concurrent.futures import Future
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import numpy as np

from photometry_app.core.array_backend import NUMPY_ARRAY_BACKEND, create_array_backend, resolve_full_frame_backend
from photometry_app.core.solar_system import SolarSystemDetection, SolarSystemFrameMeasurement
from photometry_app.core.synthetic_tracking import SyntheticTrackingFrameTarget, build_synthetic_tracked_cumulative_stacks, build_synthetic_tracked_full_frame_stack, build_synthetic_tracked_stack, measure_synthetic_tracking_peak


class SyntheticTrackingTest(unittest.TestCase):
    def test_build_synthetic_tracked_stack_aligns_selected_object(self) -> None:
        frame_paths = [Path("frame_1.fits"), Path("frame_2.fits"), Path("frame_3.fits")]
        observation_start = datetime(2025, 1, 14, 21, 12, tzinfo=UTC)
        positions = {
            str(frame_paths[0].resolve()): (15.2, 15.1),
            str(frame_paths[1].resolve()): (16.1, 15.1),
            str(frame_paths[2].resolve()): (17.0, 15.1),
        }
        images = {
            key: self._synthetic_frame(x_pos, y_pos, seed=index)
            for index, (key, (x_pos, y_pos)) in enumerate(positions.items(), start=1)
        }
        frame_times = {
            str(path.resolve()): observation_start + timedelta(seconds=index * 60.0)
            for index, path in enumerate(frame_paths)
        }
        detection = self._detection_stub()

        def measurement_side_effect(source_path: Path, *_args, **_kwargs):
            x_pos, y_pos = positions[str(source_path.resolve())]
            return self._frame_measurement(source_path, frame_times[str(source_path.resolve())], x_pos, y_pos)

        def image_side_effect(source_path: Path):
            return images[str(source_path.resolve())]

        with (
            patch("photometry_app.core.synthetic_tracking.measure_detection_in_frame", side_effect=measurement_side_effect),
            patch("photometry_app.core.synthetic_tracking.read_photometry_image_data", side_effect=image_side_effect),
        ):
            result = build_synthetic_tracked_stack(
                frame_paths,
                detection,
                reference_path=frame_paths[0],
                frame_observation_times=frame_times,
                frame_exposure_seconds={str(path.resolve()): 120.0 for path in frame_paths},
                reference_observation_time=observation_start,
                crop_radius=10,
            )

        self.assertEqual(result.used_frame_count, 3)
        self.assertEqual(result.skipped_frame_count, 0)
        self.assertIsNotNone(result.local_snr)
        self.assertGreater(result.local_snr or 0.0, 5.0)
        self.assertIsNotNone(result.match_offset_px)
        assert result.match_offset_px is not None
        self.assertLess(result.match_offset_px, 1.5)
        self.assertIsNotNone(result.motion_px_per_hour)
        self.assertIsNotNone(result.motion_angle_deg)
        self.assertAlmostEqual(result.motion_px_per_hour or 0.0, 54.0, places=1)
        self.assertAlmostEqual(result.motion_angle_deg or 0.0, 0.0, places=1)
        self.assertIn("Stacking motion", result.summary_text)
        center_value = float(result.stacked_data[10, 10])
        self.assertGreater(center_value, float(np.nanstd(result.stacked_data)) * 3.0)

    def test_build_synthetic_tracked_stack_skips_unusable_frames(self) -> None:
        frame_paths = [Path("good.fits"), Path("missing.fits"), Path("edge.fits")]
        observation_start = datetime(2025, 1, 14, 21, 12, tzinfo=UTC)
        frame_times = {
            str(path.resolve()): observation_start + timedelta(seconds=index * 60.0)
            for index, path in enumerate(frame_paths)
        }
        detection = self._detection_stub()
        images = {
            str(frame_paths[0].resolve()): self._synthetic_frame(14.5, 14.5, seed=1),
            str(frame_paths[2].resolve()): self._synthetic_frame(1.2, 1.1, seed=2),
        }

        def measurement_side_effect(source_path: Path, *_args, **_kwargs):
            resolved = str(source_path.resolve())
            if resolved == str(frame_paths[1].resolve()):
                return None
            if resolved == str(frame_paths[2].resolve()):
                return self._frame_measurement(source_path, frame_times[resolved], 1.2, 1.1)
            return self._frame_measurement(source_path, frame_times[resolved], 14.5, 14.5)

        def image_side_effect(source_path: Path):
            return images[str(source_path.resolve())]

        with (
            patch("photometry_app.core.synthetic_tracking.measure_detection_in_frame", side_effect=measurement_side_effect),
            patch("photometry_app.core.synthetic_tracking.read_photometry_image_data", side_effect=image_side_effect),
        ):
            result = build_synthetic_tracked_stack(
                frame_paths,
                detection,
                reference_path=frame_paths[0],
                frame_observation_times=frame_times,
                frame_exposure_seconds={str(path.resolve()): 120.0 for path in frame_paths},
                reference_observation_time=observation_start,
                crop_radius=10,
            )

        self.assertEqual(result.used_frame_count, 1)
        self.assertEqual(result.skipped_frame_count, 2)
        self.assertIn("1 of 3", result.summary_text)
        self.assertEqual(sum(1 for item in result.frame_contributions if item.used), 1)
        self.assertEqual(sum(1 for item in result.frame_contributions if not item.used), 2)

    def test_build_synthetic_tracked_cumulative_stacks_streams_average_prefixes(self) -> None:
        frame_paths = [Path("trail_1.fits"), Path("trail_2.fits"), Path("trail_3.fits")]
        observation_start = datetime(2025, 1, 14, 21, 12, tzinfo=UTC)
        positions = {
            str(frame_paths[0].resolve()): (15.0, 15.0),
            str(frame_paths[1].resolve()): (16.0, 15.0),
            str(frame_paths[2].resolve()): (17.0, 15.0),
        }
        images = {
            key: self._synthetic_frame(x_pos, y_pos, seed=index)
            for index, (key, (x_pos, y_pos)) in enumerate(positions.items(), start=1)
        }
        frame_times = {
            str(path.resolve()): observation_start + timedelta(seconds=index * 60.0)
            for index, path in enumerate(frame_paths)
        }
        detection = self._detection_stub()

        def measurement_side_effect(source_path: Path, *_args, **_kwargs):
            x_pos, y_pos = positions[str(source_path.resolve())]
            return self._frame_measurement(source_path, frame_times[str(source_path.resolve())], x_pos, y_pos)

        def image_side_effect(source_path: Path):
            return images[str(source_path.resolve())]

        with (
            patch("photometry_app.core.synthetic_tracking.measure_detection_in_frame", side_effect=measurement_side_effect),
            patch("photometry_app.core.synthetic_tracking.read_photometry_image_data", side_effect=image_side_effect),
            patch(
                "photometry_app.core.synthetic_tracking._combine_stack_patches",
                side_effect=AssertionError("default cumulative average should not recombine every prefix"),
            ),
        ):
            results = build_synthetic_tracked_cumulative_stacks(
                frame_paths,
                detection,
                reference_path=frame_paths[0],
                frame_observation_times=frame_times,
                frame_exposure_seconds={str(path.resolve()): 120.0 for path in frame_paths},
                reference_observation_time=observation_start,
                crop_radius=10,
                integration_mode="average",
                rejection_mode="no_rejection",
            )

        self.assertEqual(len(results), 3)
        self.assertEqual([result.used_frame_count for result in results], [1, 2, 3])
        self.assertEqual([len(result.frame_contributions) for result in results], [1, 2, 3])
        self.assertTrue(all(result.linear_stacked_data is not None for result in results))
        self.assertGreater(float(results[-1].stacked_data[10, 10]), float(np.nanstd(results[-1].stacked_data)) * 3.0)

    def test_build_synthetic_tracked_stack_preserves_linear_export_data(self) -> None:
        frame_paths = [Path("linear_1.fits"), Path("linear_2.fits")]
        observation_start = datetime(2025, 1, 14, 21, 12, tzinfo=UTC)
        frame_times = {
            str(path.resolve()): observation_start + timedelta(seconds=index * 60.0)
            for index, path in enumerate(frame_paths)
        }
        detection = self._detection_stub()
        positions = {str(path.resolve()): (15.0, 15.0) for path in frame_paths}
        images = {
            str(frame_paths[0].resolve()): self._synthetic_frame(15.0, 15.0, seed=1) + 120.0,
            str(frame_paths[1].resolve()): self._synthetic_frame(15.0, 15.0, seed=2) + 180.0,
        }

        def measurement_side_effect(source_path: Path, *_args, **_kwargs):
            x_pos, y_pos = positions[str(source_path.resolve())]
            return self._frame_measurement(source_path, frame_times[str(source_path.resolve())], x_pos, y_pos)

        def image_side_effect(source_path: Path):
            return images[str(source_path.resolve())]

        with (
            patch("photometry_app.core.synthetic_tracking.measure_detection_in_frame", side_effect=measurement_side_effect),
            patch("photometry_app.core.synthetic_tracking.read_photometry_image_data", side_effect=image_side_effect),
        ):
            result = build_synthetic_tracked_stack(
                frame_paths,
                detection,
                reference_path=frame_paths[0],
                frame_observation_times=frame_times,
                frame_exposure_seconds={str(path.resolve()): 120.0 for path in frame_paths},
                reference_observation_time=observation_start,
                crop_radius=10,
                integration_mode="average",
                rejection_mode="no_rejection",
            )

        self.assertIsNotNone(result.linear_stacked_data)
        assert result.linear_stacked_data is not None
        self.assertLess(abs(float(np.nanmedian(result.stacked_data))), 5.0)
        self.assertGreater(float(np.nanmedian(result.linear_stacked_data)), 100.0)

    def test_build_synthetic_tracked_stack_sigma_clipped_mean_rejects_outlier_frame(self) -> None:
        frame_paths = [Path(f"frame_{index}.fits") for index in range(1, 6)]
        observation_start = datetime(2025, 1, 14, 21, 12, tzinfo=UTC)
        frame_times = {
            str(path.resolve()): observation_start + timedelta(seconds=index * 60.0)
            for index, path in enumerate(frame_paths)
        }
        detection = self._detection_stub()
        positions = {str(path.resolve()): (15.0, 15.0) for path in frame_paths}
        images = {
            str(path.resolve()): self._synthetic_frame(15.0, 15.0, seed=index)
            for index, path in enumerate(frame_paths, start=1)
        }
        images[str(frame_paths[-1].resolve())][15, 15] += 1000.0

        def measurement_side_effect(source_path: Path, *_args, **_kwargs):
            x_pos, y_pos = positions[str(source_path.resolve())]
            return self._frame_measurement(source_path, frame_times[str(source_path.resolve())], x_pos, y_pos)

        def image_side_effect(source_path: Path):
            return images[str(source_path.resolve())]

        with (
            patch("photometry_app.core.synthetic_tracking.measure_detection_in_frame", side_effect=measurement_side_effect),
            patch("photometry_app.core.synthetic_tracking.read_photometry_image_data", side_effect=image_side_effect),
        ):
            mean_result = build_synthetic_tracked_stack(
                frame_paths,
                detection,
                reference_path=frame_paths[0],
                frame_observation_times=frame_times,
                frame_exposure_seconds={str(path.resolve()): 120.0 for path in frame_paths},
                reference_observation_time=observation_start,
                crop_radius=10,
                combine_mode="mean",
            )
            clipped_result = build_synthetic_tracked_stack(
                frame_paths,
                detection,
                reference_path=frame_paths[0],
                frame_observation_times=frame_times,
                frame_exposure_seconds={str(path.resolve()): 120.0 for path in frame_paths},
                reference_observation_time=observation_start,
                crop_radius=10,
                combine_mode="sigma_clipped_mean",
            )

        self.assertGreater(float(mean_result.stacked_data[10, 10]), float(clipped_result.stacked_data[10, 10]))

    def test_build_synthetic_tracked_stack_weighted_mean_prefers_stronger_frames(self) -> None:
        frame_paths = [Path("weight_a.fits"), Path("weight_b.fits")]
        observation_start = datetime(2025, 1, 14, 21, 12, tzinfo=UTC)
        frame_times = {
            str(path.resolve()): observation_start + timedelta(seconds=index * 60.0)
            for index, path in enumerate(frame_paths)
        }
        detection = self._detection_stub()
        positions = {str(path.resolve()): (15.0, 15.0) for path in frame_paths}
        images = {
            str(frame_paths[0].resolve()): self._synthetic_frame(15.0, 15.0, seed=1),
            str(frame_paths[1].resolve()): self._synthetic_frame(15.0, 15.0, seed=2),
        }
        images[str(frame_paths[1].resolve())][15, 15] += 40.0

        def measurement_side_effect(source_path: Path, *_args, **_kwargs):
            x_pos, y_pos = positions[str(source_path.resolve())]
            return self._frame_measurement(source_path, frame_times[str(source_path.resolve())], x_pos, y_pos)

        def image_side_effect(source_path: Path):
            return images[str(source_path.resolve())]

        with (
            patch("photometry_app.core.synthetic_tracking.measure_detection_in_frame", side_effect=measurement_side_effect),
            patch("photometry_app.core.synthetic_tracking.read_photometry_image_data", side_effect=image_side_effect),
        ):
            average_result = build_synthetic_tracked_stack(
                frame_paths,
                detection,
                reference_path=frame_paths[0],
                frame_observation_times=frame_times,
                frame_exposure_seconds={str(path.resolve()): 120.0 for path in frame_paths},
                reference_observation_time=observation_start,
                crop_radius=10,
                integration_mode="average",
                rejection_mode="no_rejection",
            )
            weighted_result = build_synthetic_tracked_stack(
                frame_paths,
                detection,
                reference_path=frame_paths[0],
                frame_observation_times=frame_times,
                frame_exposure_seconds={str(path.resolve()): 120.0 for path in frame_paths},
                reference_observation_time=observation_start,
                crop_radius=10,
                integration_mode="mean",
                weight_mode="psf_signal_weight",
                rejection_mode="no_rejection",
            )

        self.assertGreater(float(weighted_result.stacked_data[10, 10]), float(average_result.stacked_data[10, 10]))

    def test_build_synthetic_tracked_stack_min_max_rejection_reduces_single_hot_pixel(self) -> None:
        frame_paths = [Path(f"reject_{index}.fits") for index in range(1, 6)]
        observation_start = datetime(2025, 1, 14, 21, 12, tzinfo=UTC)
        frame_times = {
            str(path.resolve()): observation_start + timedelta(seconds=index * 60.0)
            for index, path in enumerate(frame_paths)
        }
        detection = self._detection_stub()
        positions = {str(path.resolve()): (15.0, 15.0) for path in frame_paths}
        images = {
            str(path.resolve()): self._synthetic_frame(15.0, 15.0, seed=index)
            for index, path in enumerate(frame_paths, start=1)
        }
        images[str(frame_paths[-1].resolve())][15, 15] += 500.0

        def measurement_side_effect(source_path: Path, *_args, **_kwargs):
            x_pos, y_pos = positions[str(source_path.resolve())]
            return self._frame_measurement(source_path, frame_times[str(source_path.resolve())], x_pos, y_pos)

        def image_side_effect(source_path: Path):
            return images[str(source_path.resolve())]

        with (
            patch("photometry_app.core.synthetic_tracking.measure_detection_in_frame", side_effect=measurement_side_effect),
            patch("photometry_app.core.synthetic_tracking.read_photometry_image_data", side_effect=image_side_effect),
        ):
            average_result = build_synthetic_tracked_stack(
                frame_paths,
                detection,
                reference_path=frame_paths[0],
                frame_observation_times=frame_times,
                frame_exposure_seconds={str(path.resolve()): 120.0 for path in frame_paths},
                reference_observation_time=observation_start,
                crop_radius=10,
                integration_mode="average",
                rejection_mode="no_rejection",
            )
            rejected_result = build_synthetic_tracked_stack(
                frame_paths,
                detection,
                reference_path=frame_paths[0],
                frame_observation_times=frame_times,
                frame_exposure_seconds={str(path.resolve()): 120.0 for path in frame_paths},
                reference_observation_time=observation_start,
                crop_radius=10,
                integration_mode="average",
                rejection_mode="min_max",
            )

        self.assertGreater(float(average_result.stacked_data[10, 10]), float(rejected_result.stacked_data[10, 10]))

    def test_build_synthetic_tracked_stack_uses_explicit_frame_targets_without_prediction_model(self) -> None:
        frame_paths = [Path("candidate_1.fits"), Path("candidate_2.fits"), Path("candidate_3.fits")]
        observation_start = datetime(2025, 1, 14, 21, 12, tzinfo=UTC)
        positions = {
            str(frame_paths[0].resolve()): (15.0, 15.0),
            str(frame_paths[1].resolve()): (16.0, 15.0),
            str(frame_paths[2].resolve()): (17.0, 15.0),
        }
        images = {
            key: self._synthetic_frame(x_pos, y_pos, seed=index)
            for index, (key, (x_pos, y_pos)) in enumerate(positions.items(), start=1)
        }
        frame_targets = {
            str(path.resolve()): SyntheticTrackingFrameTarget(
                source_path=path,
                observation_time=observation_start + timedelta(seconds=index * 60.0),
                predicted_x=positions[str(path.resolve())][0],
                predicted_y=positions[str(path.resolve())][1],
            )
            for index, path in enumerate(frame_paths)
        }
        detection = self._detection_stub()

        def image_side_effect(source_path: Path):
            return images[str(source_path.resolve())]

        with (
            patch("photometry_app.core.synthetic_tracking.measure_detection_in_frame") as measure_detection_in_frame,
            patch("photometry_app.core.synthetic_tracking.read_photometry_image_data", side_effect=image_side_effect),
        ):
            result = build_synthetic_tracked_stack(
                frame_paths,
                detection,
                reference_path=frame_paths[0],
                frame_observation_times={},
                frame_exposure_seconds={str(path.resolve()): 120.0 for path in frame_paths},
                frame_targets=frame_targets,
                reference_observation_time=observation_start,
                crop_radius=10,
            )

        measure_detection_in_frame.assert_not_called()
        self.assertEqual(result.used_frame_count, 3)
        self.assertEqual(result.skipped_frame_count, 0)
        self.assertGreater(float(result.stacked_data[10, 10]), float(np.nanstd(result.stacked_data)) * 3.0)

    def test_build_synthetic_tracked_stack_supports_parallel_frame_preparation(self) -> None:
        frame_paths = [Path(f"frame_{index}.fits") for index in range(1, 5)]
        observation_start = datetime(2025, 1, 14, 21, 12, tzinfo=UTC)
        frame_times = {
            str(path.resolve()): observation_start + timedelta(seconds=index * 45.0)
            for index, path in enumerate(frame_paths)
        }
        positions = {
            str(path.resolve()): (15.0 + index, 15.0)
            for index, path in enumerate(frame_paths)
        }
        images = {
            str(path.resolve()): self._synthetic_frame(positions[str(path.resolve())][0], 15.0, seed=index)
            for index, path in enumerate(frame_paths, start=1)
        }
        detection = self._detection_stub()

        def measurement_side_effect(source_path: Path, *_args, **_kwargs):
            x_pos, y_pos = positions[str(source_path.resolve())]
            return self._frame_measurement(source_path, frame_times[str(source_path.resolve())], x_pos, y_pos)

        def image_side_effect(source_path: Path):
            return images[str(source_path.resolve())]

        with (
            patch("photometry_app.core.synthetic_tracking.measure_detection_in_frame", side_effect=measurement_side_effect),
            patch("photometry_app.core.synthetic_tracking.read_photometry_image_data", side_effect=image_side_effect),
        ):
            result = build_synthetic_tracked_stack(
                frame_paths,
                detection,
                reference_path=frame_paths[0],
                frame_observation_times=frame_times,
                frame_exposure_seconds={str(path.resolve()): 120.0 for path in frame_paths},
                reference_observation_time=observation_start,
                crop_radius=10,
                max_parallel_workers=4,
            )

        self.assertEqual([item.source_path for item in result.frame_contributions], frame_paths)
        self.assertTrue(all(item.used for item in result.frame_contributions))
        self.assertEqual(result.used_frame_count, 4)

    def test_build_synthetic_tracked_full_frame_stack_aligns_motion_across_entire_image(self) -> None:
        frame_paths = [Path("full_frame_1.fits"), Path("full_frame_2.fits"), Path("full_frame_3.fits")]
        observation_start = datetime(2025, 1, 14, 21, 12, tzinfo=UTC)
        frame_times = {
            str(path.resolve()): observation_start + timedelta(minutes=index)
            for index, path in enumerate(frame_paths)
        }
        images = {
            str(frame_paths[0].resolve()): self._synthetic_frame(12.0, 12.0, seed=1),
            str(frame_paths[1].resolve()): self._synthetic_frame(12.5, 12.0, seed=2),
            str(frame_paths[2].resolve()): self._synthetic_frame(13.0, 12.0, seed=3),
        }

        def image_side_effect(source_path: Path):
            return images[str(source_path.resolve())]

        with patch("photometry_app.core.synthetic_tracking.read_photometry_image_data", side_effect=image_side_effect):
            result = build_synthetic_tracked_full_frame_stack(
                frame_paths,
                reference_path=frame_paths[0],
                frame_observation_times=frame_times,
                motion_px_per_hour=30.0,
                motion_angle_deg=0.0,
                combine_mode="mean",
                motion_arcsec_per_hour=45.0,
            )

        self.assertTrue(result.full_frame_mode)
        self.assertEqual(result.used_frame_count, 3)
        self.assertEqual(result.skipped_frame_count, 0)
        self.assertAlmostEqual(result.motion_px_per_hour or 0.0, 30.0, places=3)
        self.assertAlmostEqual(result.motion_arcsec_per_hour or 0.0, 45.0, places=3)
        self.assertGreater(float(result.local_snr or 0.0), 3.0)
        self.assertGreater(float(result.stacked_data[int(result.center_y), int(result.center_x)]), float(np.nanstd(result.stacked_data)) * 3.0)

    def test_build_synthetic_tracked_full_frame_stack_supports_parallel_frame_preparation(self) -> None:
        frame_paths = [Path(f"full_frame_parallel_{index}.fits") for index in range(1, 5)]
        observation_start = datetime(2025, 1, 14, 21, 12, tzinfo=UTC)
        frame_times = {
            str(path.resolve()): observation_start + timedelta(seconds=index * 45.0)
            for index, path in enumerate(frame_paths)
        }
        images = {
            str(path.resolve()): self._synthetic_frame(12.0 + (0.4 * index), 12.0, seed=index)
            for index, path in enumerate(frame_paths, start=1)
        }
        recorded_max_workers: list[int] = []

        class _FakeExecutor:
            def __init__(self, *, max_workers: int) -> None:
                recorded_max_workers.append(max_workers)

            def __enter__(self) -> "_FakeExecutor":
                return self

            def __exit__(self, exc_type, exc, tb) -> bool:
                return False

            def submit(self, func, **kwargs):
                future: Future = Future()
                future.set_result(func(**kwargs))
                return future

        def image_side_effect(source_path: Path):
            return images[str(source_path.resolve())]

        with (
            patch("photometry_app.core.synthetic_tracking.ThreadPoolExecutor", _FakeExecutor),
            patch("photometry_app.core.synthetic_tracking.read_photometry_image_data", side_effect=image_side_effect),
        ):
            result = build_synthetic_tracked_full_frame_stack(
                frame_paths,
                reference_path=frame_paths[0],
                frame_observation_times=frame_times,
                motion_px_per_hour=32.0,
                motion_angle_deg=0.0,
                combine_mode="mean",
                max_parallel_workers=8,
                array_backend_preference="cpu",
            )

        self.assertEqual(recorded_max_workers, [2])
        self.assertEqual(result.used_frame_count, 4)

    def test_build_synthetic_tracked_full_frame_stack_reports_parallel_progress_incrementally(self) -> None:
        frame_paths = [Path(f"full_frame_progress_{index}.fits") for index in range(1, 5)]
        observation_start = datetime(2025, 1, 14, 21, 12, tzinfo=UTC)
        frame_times = {
            str(path.resolve()): observation_start + timedelta(seconds=index * 45.0)
            for index, path in enumerate(frame_paths)
        }
        images = {
            str(path.resolve()): self._synthetic_frame(12.0 + (0.4 * index), 12.0, seed=index)
            for index, path in enumerate(frame_paths, start=1)
        }
        progress_updates: list[tuple[int, int, str]] = []

        class _FakeExecutor:
            def __init__(self, *, max_workers: int) -> None:
                self._max_workers = max_workers

            def __enter__(self) -> "_FakeExecutor":
                return self

            def __exit__(self, exc_type, exc, tb) -> bool:
                return False

            def submit(self, func, **kwargs):
                future: Future = Future()
                future.set_result(func(**kwargs))
                return future

        def image_side_effect(source_path: Path):
            return images[str(source_path.resolve())]

        with (
            patch("photometry_app.core.synthetic_tracking.ThreadPoolExecutor", _FakeExecutor),
            patch("photometry_app.core.synthetic_tracking.read_photometry_image_data", side_effect=image_side_effect),
        ):
            result = build_synthetic_tracked_full_frame_stack(
                frame_paths,
                reference_path=frame_paths[0],
                frame_observation_times=frame_times,
                motion_px_per_hour=32.0,
                motion_angle_deg=0.0,
                combine_mode="mean",
                max_parallel_workers=4,
                array_backend_preference="cpu",
                progress_callback=lambda completed, total, message: progress_updates.append((completed, total, message)),
            )

        self.assertEqual(result.used_frame_count, 4)
        self.assertEqual([completed for completed, _total, _message in progress_updates], [1, 2, 3, 4])
        self.assertTrue(all(total == 4 for _completed, total, _message in progress_updates))

    def test_build_synthetic_tracked_full_frame_stack_mean_does_not_materialize_stack_cube(self) -> None:
        frame_paths = [Path(f"full_frame_mean_{index}.fits") for index in range(1, 4)]
        observation_start = datetime(2025, 1, 14, 21, 12, tzinfo=UTC)
        frame_times = {
            str(path.resolve()): observation_start + timedelta(minutes=index)
            for index, path in enumerate(frame_paths)
        }
        images = {
            str(path.resolve()): self._synthetic_frame(12.0 + (0.5 * index), 12.0, seed=index)
            for index, path in enumerate(frame_paths, start=1)
        }

        def image_side_effect(source_path: Path):
            return images[str(source_path.resolve())]

        with (
            patch("photometry_app.core.synthetic_tracking.read_photometry_image_data", side_effect=image_side_effect),
            patch(
                "photometry_app.core.synthetic_tracking._combine_stack_patches",
                side_effect=AssertionError("full-frame mean should not call _combine_stack_patches"),
            ),
        ):
            result = build_synthetic_tracked_full_frame_stack(
                frame_paths,
                reference_path=frame_paths[0],
                frame_observation_times=frame_times,
                motion_px_per_hour=30.0,
                motion_angle_deg=0.0,
                combine_mode="mean",
            )

        self.assertEqual(result.used_frame_count, 3)
        self.assertTrue(result.full_frame_mode)

    def test_build_synthetic_tracked_full_frame_stack_sigma_clipped_mean_rejects_large_stack(self) -> None:
        frame_paths = [Path(f"full_frame_sigma_{index}.fits") for index in range(1, 4)]
        observation_start = datetime(2025, 1, 14, 21, 12, tzinfo=UTC)
        frame_times = {
            str(path.resolve()): observation_start + timedelta(minutes=index)
            for index, path in enumerate(frame_paths)
        }
        images = {
            str(path.resolve()): self._synthetic_frame(12.0, 12.0, seed=index)
            for index, path in enumerate(frame_paths, start=1)
        }

        def image_side_effect(source_path: Path):
            return images[str(source_path.resolve())]

        with (
            patch("photometry_app.core.synthetic_tracking.read_photometry_image_data", side_effect=image_side_effect),
            patch("photometry_app.core.synthetic_tracking._MAX_SIGMA_CLIPPED_STACK_BYTES", 1),
        ):
            with self.assertRaisesRegex(ValueError, "Use Mean or a smaller crop radius"):
                build_synthetic_tracked_full_frame_stack(
                    frame_paths,
                    reference_path=frame_paths[0],
                    frame_observation_times=frame_times,
                    motion_px_per_hour=30.0,
                    motion_angle_deg=0.0,
                    combine_mode="sigma_clipped_mean",
                )

    def test_build_synthetic_tracked_full_frame_stack_gpu_backend_handles_streaming_mean(self) -> None:
        frame_paths = [Path(f"full_frame_gpu_mean_{index}.fits") for index in range(1, 4)]
        observation_start = datetime(2025, 1, 14, 21, 12, tzinfo=UTC)
        frame_times = {
            str(path.resolve()): observation_start + timedelta(minutes=index)
            for index, path in enumerate(frame_paths)
        }
        images = {
            str(path.resolve()): self._synthetic_frame(12.0 + (0.5 * index), 12.0, seed=index)
            for index, path in enumerate(frame_paths, start=1)
        }
        fake_gpu_backend = create_array_backend(name="gpu", xp=np, is_gpu=True)

        def image_side_effect(source_path: Path):
            return images[str(source_path.resolve())]

        with (
            patch("photometry_app.core.synthetic_tracking.read_photometry_image_data", side_effect=image_side_effect),
            patch("photometry_app.core.synthetic_tracking.resolve_full_frame_backend", return_value=(fake_gpu_backend, None)),
            patch("photometry_app.core.synthetic_tracking._FULL_FRAME_GPU_BACKEND_WARMED", False),
        ):
            result = build_synthetic_tracked_full_frame_stack(
                frame_paths,
                reference_path=frame_paths[0],
                frame_observation_times=frame_times,
                motion_px_per_hour=30.0,
                motion_angle_deg=0.0,
                integration_mode="mean",
                weight_mode="psf_signal_weight",
                rejection_mode="no_rejection",
            )

        self.assertEqual(result.compute_backend_summary, "Compute backend: GPU (CuPy full-frame no-rejection stack)")
        self.assertEqual(result.gpu_warmup_summary, "GPU warm-up cache: Prepared during this run.")
        self.assertEqual(result.used_frame_count, 3)

    def test_build_synthetic_tracked_full_frame_stack_gpu_average_skips_weight_metrics(self) -> None:
        frame_paths = [Path(f"full_frame_gpu_average_{index}.fits") for index in range(1, 4)]
        observation_start = datetime(2025, 1, 14, 21, 12, tzinfo=UTC)
        frame_times = {
            str(path.resolve()): observation_start + timedelta(minutes=index)
            for index, path in enumerate(frame_paths)
        }
        images = {
            str(path.resolve()): self._synthetic_frame(12.0 + (0.5 * index), 12.0, seed=index)
            for index, path in enumerate(frame_paths, start=1)
        }
        fake_gpu_backend = create_array_backend(name="gpu", xp=np, is_gpu=True)

        def image_side_effect(source_path: Path):
            return images[str(source_path.resolve())]

        with (
            patch("photometry_app.core.synthetic_tracking.read_photometry_image_data", side_effect=image_side_effect),
            patch("photometry_app.core.synthetic_tracking.resolve_full_frame_backend", return_value=(fake_gpu_backend, None)),
            patch("photometry_app.core.synthetic_tracking._FULL_FRAME_GPU_BACKEND_WARMED", True),
            patch(
                "photometry_app.core.synthetic_tracking._estimate_full_frame_weight_metrics_array_backend",
                side_effect=AssertionError("average full-frame streaming should not compute frame weight metrics"),
            ),
        ):
            result = build_synthetic_tracked_full_frame_stack(
                frame_paths,
                reference_path=frame_paths[0],
                frame_observation_times=frame_times,
                motion_px_per_hour=30.0,
                motion_angle_deg=0.0,
                integration_mode="average",
                rejection_mode="no_rejection",
            )

        self.assertEqual(result.compute_backend_summary, "Compute backend: GPU (CuPy full-frame no-rejection stack)")
        self.assertEqual(result.used_frame_count, 3)

    def test_build_synthetic_tracked_full_frame_stack_warms_gpu_backend_once(self) -> None:
        frame_paths = [Path(f"full_frame_gpu_warm_{index}.fits") for index in range(1, 4)]
        observation_start = datetime(2025, 1, 14, 21, 12, tzinfo=UTC)
        frame_times = {
            str(path.resolve()): observation_start + timedelta(minutes=index)
            for index, path in enumerate(frame_paths)
        }
        images = {
            str(path.resolve()): self._synthetic_frame(12.0 + (0.5 * index), 12.0, seed=index)
            for index, path in enumerate(frame_paths, start=1)
        }
        fake_gpu_backend = create_array_backend(name="gpu", xp=np, is_gpu=True)

        def image_side_effect(source_path: Path):
            return images[str(source_path.resolve())]

        with (
            patch("photometry_app.core.synthetic_tracking.read_photometry_image_data", side_effect=image_side_effect),
            patch("photometry_app.core.synthetic_tracking.resolve_full_frame_backend", return_value=(fake_gpu_backend, None)),
            patch("photometry_app.core.synthetic_tracking._warm_up_full_frame_gpu_backend") as warmup_mock,
        ):
            build_synthetic_tracked_full_frame_stack(
                frame_paths,
                reference_path=frame_paths[0],
                frame_observation_times=frame_times,
                motion_px_per_hour=30.0,
                motion_angle_deg=0.0,
                integration_mode="mean",
                weight_mode="psf_signal_weight",
                rejection_mode="no_rejection",
            )

        warmup_mock.assert_called_once()

    def test_build_synthetic_tracked_full_frame_stack_reports_cached_gpu_warmup_status(self) -> None:
        frame_paths = [Path(f"full_frame_gpu_cached_{index}.fits") for index in range(1, 4)]
        observation_start = datetime(2025, 1, 14, 21, 12, tzinfo=UTC)
        frame_times = {
            str(path.resolve()): observation_start + timedelta(minutes=index)
            for index, path in enumerate(frame_paths)
        }
        images = {
            str(path.resolve()): self._synthetic_frame(12.0 + (0.5 * index), 12.0, seed=index)
            for index, path in enumerate(frame_paths, start=1)
        }
        fake_gpu_backend = create_array_backend(name="gpu", xp=np, is_gpu=True)

        def image_side_effect(source_path: Path):
            return images[str(source_path.resolve())]

        with (
            patch("photometry_app.core.synthetic_tracking.read_photometry_image_data", side_effect=image_side_effect),
            patch("photometry_app.core.synthetic_tracking.resolve_full_frame_backend", return_value=(fake_gpu_backend, None)),
            patch("photometry_app.core.synthetic_tracking._FULL_FRAME_GPU_BACKEND_WARMED", True),
        ):
            result = build_synthetic_tracked_full_frame_stack(
                frame_paths,
                reference_path=frame_paths[0],
                frame_observation_times=frame_times,
                motion_px_per_hour=30.0,
                motion_angle_deg=0.0,
                integration_mode="mean",
                weight_mode="psf_signal_weight",
                rejection_mode="no_rejection",
            )

        self.assertEqual(result.gpu_warmup_summary, "GPU warm-up cache: Ready for this session.")

    def test_build_synthetic_tracked_full_frame_stack_gpu_backend_handles_streaming_max(self) -> None:
        frame_paths = [Path(f"full_frame_gpu_max_{index}.fits") for index in range(1, 4)]
        observation_start = datetime(2025, 1, 14, 21, 12, tzinfo=UTC)
        frame_times = {
            str(path.resolve()): observation_start + timedelta(minutes=index)
            for index, path in enumerate(frame_paths)
        }
        images = {
            str(path.resolve()): self._synthetic_frame(12.0 + (0.5 * index), 12.0, seed=index)
            for index, path in enumerate(frame_paths, start=1)
        }
        fake_gpu_backend = create_array_backend(name="gpu", xp=np, is_gpu=True)

        def image_side_effect(source_path: Path):
            return images[str(source_path.resolve())]

        with (
            patch("photometry_app.core.synthetic_tracking.read_photometry_image_data", side_effect=image_side_effect),
            patch("photometry_app.core.synthetic_tracking.resolve_full_frame_backend", return_value=(fake_gpu_backend, None)),
        ):
            result = build_synthetic_tracked_full_frame_stack(
                frame_paths,
                reference_path=frame_paths[0],
                frame_observation_times=frame_times,
                motion_px_per_hour=30.0,
                motion_angle_deg=0.0,
                integration_mode="max",
                rejection_mode="no_rejection",
            )

        self.assertEqual(result.compute_backend_summary, "Compute backend: GPU (CuPy full-frame no-rejection stack)")
        self.assertEqual(result.used_frame_count, 3)

    def test_build_synthetic_tracked_full_frame_stack_reports_cpu_fallback_note(self) -> None:
        frame_paths = [Path(f"full_frame_fallback_{index}.fits") for index in range(1, 4)]
        observation_start = datetime(2025, 1, 14, 21, 12, tzinfo=UTC)
        frame_times = {
            str(path.resolve()): observation_start + timedelta(minutes=index)
            for index, path in enumerate(frame_paths)
        }
        images = {
            str(path.resolve()): self._synthetic_frame(12.0, 12.0, seed=index)
            for index, path in enumerate(frame_paths, start=1)
        }

        def image_side_effect(source_path: Path):
            return images[str(source_path.resolve())]

        with (
            patch("photometry_app.core.synthetic_tracking.read_photometry_image_data", side_effect=image_side_effect),
            patch(
                "photometry_app.core.synthetic_tracking.resolve_full_frame_backend",
                return_value=(NUMPY_ARRAY_BACKEND, "Using CPU fallback: GPU full-frame acceleration currently supports only No rejection stacks."),
            ),
        ):
            result = build_synthetic_tracked_full_frame_stack(
                frame_paths,
                reference_path=frame_paths[0],
                frame_observation_times=frame_times,
                motion_px_per_hour=30.0,
                motion_angle_deg=0.0,
                integration_mode="average",
                rejection_mode="sigma_clipping",
            )

        self.assertIsNotNone(result.compute_backend_summary)
        assert result.compute_backend_summary is not None
        self.assertIn("Using CPU fallback", result.compute_backend_summary)

    def test_build_synthetic_tracked_full_frame_stack_warmup_failure_falls_back_to_cpu(self) -> None:
        frame_paths = [Path(f"full_frame_warmup_fallback_{index}.fits") for index in range(1, 4)]
        observation_start = datetime(2025, 1, 14, 21, 12, tzinfo=UTC)
        frame_times = {
            str(path.resolve()): observation_start + timedelta(minutes=index)
            for index, path in enumerate(frame_paths)
        }
        images = {
            str(path.resolve()): self._synthetic_frame(12.0, 12.0, seed=index)
            for index, path in enumerate(frame_paths, start=1)
        }
        fake_gpu_backend = create_array_backend(name="gpu", xp=np, is_gpu=True)

        def image_side_effect(source_path: Path):
            return images[str(source_path.resolve())]

        with (
            patch("photometry_app.core.synthetic_tracking.read_photometry_image_data", side_effect=image_side_effect),
            patch("photometry_app.core.synthetic_tracking.resolve_full_frame_backend", return_value=(fake_gpu_backend, None)),
            patch("photometry_app.core.synthetic_tracking._warm_up_full_frame_gpu_backend", side_effect=RuntimeError("forced warmup failure")),
        ):
            result = build_synthetic_tracked_full_frame_stack(
                frame_paths,
                reference_path=frame_paths[0],
                frame_observation_times=frame_times,
                motion_px_per_hour=30.0,
                motion_angle_deg=0.0,
                integration_mode="average",
                rejection_mode="no_rejection",
            )

        self.assertIsNotNone(result.compute_backend_summary)
        assert result.compute_backend_summary is not None
        self.assertIn("GPU warm-up failed", result.compute_backend_summary)
        self.assertIsNone(result.gpu_warmup_summary)

    def test_resolve_full_frame_backend_falls_back_when_cupy_runtime_is_unusable(self) -> None:
        fake_gpu_backend = create_array_backend(name="gpu", xp=np, is_gpu=True)

        with (
            patch("photometry_app.core.array_backend._CUPY_ARRAY_BACKEND", fake_gpu_backend),
            patch(
                "photometry_app.core.array_backend._cupy_runtime_status",
                return_value=(False, "CuPy could not initialize a compatible CUDA runtime."),
            ),
        ):
            backend, note = resolve_full_frame_backend(
                preference="gpu",
                integration_mode="average",
                rejection_mode="no_rejection",
            )

        self.assertEqual(backend.name, "cpu")
        self.assertIsNotNone(note)
        assert note is not None
        self.assertIn("compatible CUDA runtime", note)

    def test_manual_anchor_peak_measurement_can_recover_faint_center_target(self) -> None:
        image = np.zeros((21, 21), dtype=np.float32)
        image[10, 10] = 12.0
        image[10, 13] = 30.0

        auto_measurement = measure_synthetic_tracking_peak(image, 10.0, 10.0)
        manual_measurement = measure_synthetic_tracking_peak(image, 10.0, 10.0, anchor_x=10.0, anchor_y=10.0, search_radius=2)

        self.assertEqual(auto_measurement[0], 13.0)
        self.assertEqual(auto_measurement[1], 10.0)
        self.assertEqual(manual_measurement[0], 10.0)
        self.assertEqual(manual_measurement[1], 10.0)

    def _detection_stub(self) -> SolarSystemDetection:
        return SolarSystemDetection(
            name="(1) Ceres",
            designation="1",
            object_type="Asteroid",
            orbit_class="MBA",
            predicted_ra_deg=10.0,
            predicted_dec_deg=20.0,
            predicted_x=0.0,
            predicted_y=0.0,
            predicted_magnitude=17.0,
            ra_rate_arcsec_per_hour=10.0,
            dec_rate_arcsec_per_hour=0.0,
            motion_rate_arcsec_per_hour=10.0,
            expected_trail_length_px=0.1,
            positional_uncertainty_arcsec=0.5,
            altitude_deg=45.0,
            likely_visible=False,
            confidence_score=0.5,
            status="Predicted in field",
        )

    def _frame_measurement(
        self,
        source_path: Path,
        observation_time: datetime,
        predicted_x: float,
        predicted_y: float,
    ) -> SolarSystemFrameMeasurement:
        return SolarSystemFrameMeasurement(
            source_path=source_path,
            observation_time=observation_time,
            predicted_ra_deg=10.0,
            predicted_dec_deg=20.0,
            predicted_x=predicted_x,
            predicted_y=predicted_y,
            expected_trail_length_px=0.1,
        )

    def _synthetic_frame(self, center_x: float, center_y: float, *, seed: int) -> np.ndarray:
        rng = np.random.default_rng(seed)
        image = rng.normal(0.0, 1.0, size=(40, 40)).astype(np.float32)
        yy, xx = np.indices(image.shape, dtype=float)
        signal = 10.0 * np.exp(-(((xx - center_x) ** 2) + ((yy - center_y) ** 2)) / (2.0 * (1.1 ** 2)))
        return image + signal.astype(np.float32)


if __name__ == "__main__":
    unittest.main()