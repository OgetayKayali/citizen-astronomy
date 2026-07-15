from __future__ import annotations

import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from astropy.io import fits

from photometry_app.core.discovery import (
    MovingObjectCandidate,
    MovingObjectCandidateDetection,
    MovingObjectDiscoveryResult,
    _DiscoveryMotionRangeEstimate,
    _ResidualDetection,
    _detect_residual_sources,
    _estimate_discovery_motion_range,
    _link_moving_candidates,
    _shift_image_with_zero_fill,
    cleanup_stale_discovery_temp_cache,
    discover_unmatched_moving_candidates,
    export_discovery_residual_debug_outputs,
    recover_known_moving_objects,
)
from photometry_app.core.models import ObservationMetadata, SolvedField
from photometry_app.core.solar_system import SolarSystemDetection, SolarSystemDetectionResult, SolarSystemVisibilityEstimateResult


class DiscoverMovingCandidatesTest(unittest.TestCase):
    def test_estimate_discovery_motion_range_uses_pixel_scale_and_exposure(self) -> None:
        frame_paths = [Path(f"frame_{index:02d}.fit") for index in range(3)]
        start_time = datetime(2025, 1, 14, 21, 12, tzinfo=UTC)
        frame_metadata = {
            str(path.resolve()): ObservationMetadata(
                date_obs=start_time + timedelta(minutes=index),
                filter_name="L",
                exposure_seconds=180.0,
                width=64,
                height=64,
                object_name="Demo",
                frame_type="Light",
            )
            for index, path in enumerate(frame_paths)
        }

        estimate = _estimate_discovery_motion_range(
            frame_paths,
            frame_metadata=frame_metadata,
            pixel_scale_arcsec_per_pixel=1.5,
        )

        self.assertIsInstance(estimate, _DiscoveryMotionRangeEstimate)
        assert estimate is not None
        self.assertAlmostEqual(estimate.min_motion_px_per_hour, 8.0)
        self.assertAlmostEqual(estimate.max_motion_px_per_hour, 80.0)
        self.assertAlmostEqual(estimate.median_exposure_seconds, 180.0)

    def test_estimate_discovery_motion_range_respects_motion_prior_bias(self) -> None:
        frame_paths = [Path(f"frame_{index:02d}.fit") for index in range(3)]
        start_time = datetime(2025, 1, 14, 21, 12, tzinfo=UTC)
        frame_metadata = {
            str(path.resolve()): ObservationMetadata(
                date_obs=start_time + timedelta(minutes=index),
                filter_name="L",
                exposure_seconds=180.0,
                width=64,
                height=64,
                object_name="Demo",
                frame_type="Light",
            )
            for index, path in enumerate(frame_paths)
        }

        main_belt_estimate = _estimate_discovery_motion_range(
            frame_paths,
            frame_metadata=frame_metadata,
            pixel_scale_arcsec_per_pixel=1.5,
            motion_prior_bias="main_belt",
        )
        near_earth_estimate = _estimate_discovery_motion_range(
            frame_paths,
            frame_metadata=frame_metadata,
            pixel_scale_arcsec_per_pixel=1.5,
            motion_prior_bias="near_earth",
        )

        assert main_belt_estimate is not None
        assert near_earth_estimate is not None
        self.assertAlmostEqual(main_belt_estimate.min_motion_px_per_hour, 8.0 / 1.5)
        self.assertAlmostEqual(main_belt_estimate.max_motion_px_per_hour, 60.0)
        self.assertAlmostEqual(near_earth_estimate.min_motion_px_per_hour, 20.0 / 1.5)
        self.assertAlmostEqual(near_earth_estimate.max_motion_px_per_hour, 80.0)

    def test_export_discovery_residual_debug_outputs_writes_background_residuals_and_stack(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frame_paths = [root / f"frame_{index:02d}.fit" for index in range(3)]
            for path in frame_paths:
                path.write_text("placeholder", encoding="utf-8")

            start_time = datetime(2025, 1, 14, 21, 12, tzinfo=UTC)
            frame_metadata = {
                str(path.resolve()): ObservationMetadata(
                    date_obs=start_time + timedelta(minutes=index),
                    filter_name="L",
                    exposure_seconds=120.0,
                    width=4,
                    height=4,
                    object_name="Demo",
                    frame_type="Light",
                )
                for index, path in enumerate(frame_paths)
            }
            background_model = np.ones((2, 2), dtype=np.float32)
            aligned_frames = [
                np.asarray([[2.0, 3.0], [4.0, 5.0]], dtype=np.float32),
                np.asarray([[1.5, 2.5], [3.5, 4.5]], dtype=np.float32),
                np.asarray([[3.0, 4.0], [5.0, 6.0]], dtype=np.float32),
            ]
            context = SimpleNamespace(
                resolved_reference_path=frame_paths[0],
                reference_header=fits.Header(),
                frame_times=[start_time + timedelta(minutes=index) for index in range(3)],
                aligned_frames=aligned_frames,
                frame_pixel_wcs=[object(), object(), object()],
                preview_data=np.zeros((2, 2), dtype=np.float32),
                background_model=background_model,
                pixel_scale_arcsec_per_pixel=1.5,
                known_positions={},
                motion_range_estimate=None,
                effective_synthetic_sweep_settings=None,
                prepare_seconds=0.01,
            )
            output_directory = root / "discover_debug"

            with (
                patch("photometry_app.core.discovery._prepare_residual_search_context", return_value=context),
                patch("photometry_app.core.discovery._scan_frames_for_residual_detections", return_value=[[], [], []]),
            ):
                result = export_discovery_residual_debug_outputs(
                    frame_paths,
                    frame_metadata=frame_metadata,
                    known_detection_result=None,
                    output_directory=output_directory,
                    reference_path=frame_paths[0],
                )

            self.assertEqual(result, output_directory)
            self.assertTrue((output_directory / "background_model.fits").exists())
            self.assertTrue((output_directory / "residual_stack_mean.fits").exists())
            self.assertTrue((output_directory / "residual_001_frame_00.fits").exists())
            self.assertTrue((output_directory / "residual_002_frame_01.fits").exists())
            self.assertTrue((output_directory / "residual_003_frame_02.fits").exists())
            self.assertTrue((output_directory / "manifest.txt").exists())

            with fits.open(output_directory / "background_model.fits") as handle:
                np.testing.assert_allclose(handle[0].data, background_model)
            expected_stack = np.mean(np.stack([frame - background_model for frame in aligned_frames], axis=0), axis=0)
            with fits.open(output_directory / "residual_stack_mean.fits") as handle:
                np.testing.assert_allclose(handle[0].data, expected_stack)

    def test_synthetic_sweep_shift_uses_subpixel_interpolation(self) -> None:
        image = np.zeros((5, 5), dtype=np.float32)
        image[2, 2] = 1.0

        shifted = _shift_image_with_zero_fill(image, 0.5, 0.0)

        self.assertAlmostEqual(float(shifted[2, 1]), 0.5, places=6)
        self.assertAlmostEqual(float(shifted[2, 2]), 0.5, places=6)

    def test_detect_residual_sources_applies_residual_snr_bounds(self) -> None:
        residual_image = np.zeros((32, 32), dtype=np.float32)

        def finder_side_effect(_image: np.ndarray):
            return [
                {"xcentroid": 10.0, "ycentroid": 10.0, "peak": 10.0},
                {"xcentroid": 14.0, "ycentroid": 14.0, "peak": 40.0},
                {"xcentroid": 18.0, "ycentroid": 18.0, "peak": 80.0},
            ]

        with (
            patch("photometry_app.core.discovery.sigma_clipped_stats", return_value=(0.0, 0.0, 5.0)),
            patch("photometry_app.core.discovery.DAOStarFinder", return_value=finder_side_effect),
        ):
            result = _detect_residual_sources(
                residual_image,
                source_path=Path("frame.fit"),
                observation_time=datetime(2025, 1, 14, 21, 12, tzinfo=UTC),
                frame_index=0,
                next_detection_id=1,
                excluded_positions=(),
                residual_min_snr=5.0,
                residual_max_snr=10.0,
            )

        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0].local_snr, 8.0)

    def test_link_moving_candidates_refits_membership_when_intermediate_clutter_is_present(self) -> None:
        frame_times = [datetime(2025, 1, 14, 21, 12, tzinfo=UTC) + timedelta(minutes=index) for index in range(4)]
        residual_detections_by_frame = [
            [
                _ResidualDetection(
                    detection_id=1,
                    frame_index=0,
                    source_path=Path("frame_00.fit"),
                    observation_time=frame_times[0],
                    x=10.0,
                    y=12.0,
                    peak_value=20.0,
                    local_snr=8.0,
                )
            ],
            [
                _ResidualDetection(
                    detection_id=2,
                    frame_index=1,
                    source_path=Path("frame_01.fit"),
                    observation_time=frame_times[1],
                    x=11.0,
                    y=12.0,
                    peak_value=20.0,
                    local_snr=8.0,
                )
            ],
            [
                _ResidualDetection(
                    detection_id=3,
                    frame_index=2,
                    source_path=Path("frame_02.fit"),
                    observation_time=frame_times[2],
                    x=11.6,
                    y=12.0,
                    peak_value=24.0,
                    local_snr=9.0,
                ),
                _ResidualDetection(
                    detection_id=4,
                    frame_index=2,
                    source_path=Path("frame_02.fit"),
                    observation_time=frame_times[2],
                    x=12.0,
                    y=12.0,
                    peak_value=20.0,
                    local_snr=8.0,
                ),
            ],
            [
                _ResidualDetection(
                    detection_id=5,
                    frame_index=3,
                    source_path=Path("frame_03.fit"),
                    observation_time=frame_times[3],
                    x=13.0,
                    y=12.0,
                    peak_value=20.0,
                    local_snr=8.0,
                )
            ],
        ]

        candidates = _link_moving_candidates(
            residual_detections_by_frame,
            frame_times=frame_times,
            pixel_scale_arcsec_per_pixel=1.5,
            minimum_candidate_frames=4,
        )

        self.assertGreaterEqual(len(candidates), 1)
        self.assertEqual([detection.frame_index for detection in candidates[0].frame_detections], [0, 1, 2, 3])
        self.assertEqual([round(detection.x, 3) for detection in candidates[0].frame_detections], [10.0, 11.0, 12.0, 13.0])
        self.assertLess(candidates[0].fit_rms_px, 0.05)

    def test_link_moving_candidates_keeps_tracklet_with_one_missing_intermediate_frame(self) -> None:
        frame_times = [datetime(2025, 1, 14, 21, 12, tzinfo=UTC) + timedelta(minutes=index) for index in range(5)]
        residual_detections_by_frame = [
            [
                _ResidualDetection(
                    detection_id=1,
                    frame_index=0,
                    source_path=Path("frame_00.fit"),
                    observation_time=frame_times[0],
                    x=10.0,
                    y=12.0,
                    peak_value=20.0,
                    local_snr=8.0,
                )
            ],
            [
                _ResidualDetection(
                    detection_id=2,
                    frame_index=1,
                    source_path=Path("frame_01.fit"),
                    observation_time=frame_times[1],
                    x=11.0,
                    y=12.0,
                    peak_value=20.0,
                    local_snr=8.0,
                )
            ],
            [],
            [
                _ResidualDetection(
                    detection_id=3,
                    frame_index=3,
                    source_path=Path("frame_03.fit"),
                    observation_time=frame_times[3],
                    x=13.0,
                    y=12.0,
                    peak_value=20.0,
                    local_snr=8.0,
                )
            ],
            [
                _ResidualDetection(
                    detection_id=4,
                    frame_index=4,
                    source_path=Path("frame_04.fit"),
                    observation_time=frame_times[4],
                    x=14.0,
                    y=12.0,
                    peak_value=20.0,
                    local_snr=8.0,
                )
            ],
        ]

        candidates = _link_moving_candidates(
            residual_detections_by_frame,
            frame_times=frame_times,
            pixel_scale_arcsec_per_pixel=1.5,
            minimum_candidate_frames=4,
        )

        self.assertGreaterEqual(len(candidates), 1)
        self.assertEqual([detection.frame_index for detection in candidates[0].frame_detections], [0, 1, 3, 4])
        self.assertLess(candidates[0].fit_rms_px, 0.05)

    def test_link_moving_candidates_allows_lower_runtime_seed_displacement(self) -> None:
        frame_times = [datetime(2025, 1, 14, 21, 12, tzinfo=UTC) + timedelta(minutes=index) for index in range(3)]
        residual_detections_by_frame = [
            [
                _ResidualDetection(
                    detection_id=1,
                    frame_index=0,
                    source_path=Path("frame_00.fit"),
                    observation_time=frame_times[0],
                    x=10.0,
                    y=12.0,
                    peak_value=20.0,
                    local_snr=8.0,
                )
            ],
            [
                _ResidualDetection(
                    detection_id=2,
                    frame_index=1,
                    source_path=Path("frame_01.fit"),
                    observation_time=frame_times[1],
                    x=10.6,
                    y=12.0,
                    peak_value=20.0,
                    local_snr=8.0,
                )
            ],
            [
                _ResidualDetection(
                    detection_id=3,
                    frame_index=2,
                    source_path=Path("frame_02.fit"),
                    observation_time=frame_times[2],
                    x=11.2,
                    y=12.0,
                    peak_value=20.0,
                    local_snr=8.0,
                )
            ],
        ]

        default_candidates = _link_moving_candidates(
            residual_detections_by_frame,
            frame_times=frame_times,
            pixel_scale_arcsec_per_pixel=1.5,
            minimum_candidate_frames=3,
        )

        lowered_threshold_candidates = _link_moving_candidates(
            residual_detections_by_frame,
            frame_times=frame_times,
            pixel_scale_arcsec_per_pixel=1.5,
            minimum_candidate_frames=3,
            min_seed_displacement_px=0.5,
        )

        self.assertEqual(default_candidates, [])
        self.assertGreaterEqual(len(lowered_threshold_candidates), 1)
        self.assertEqual([detection.frame_index for detection in lowered_threshold_candidates[0].frame_detections], [0, 1, 2])

    def test_link_moving_candidates_keeps_tracklet_when_first_followup_frame_is_missing(self) -> None:
        frame_times = [datetime(2025, 1, 14, 21, 12, tzinfo=UTC) + timedelta(minutes=index) for index in range(5)]
        residual_detections_by_frame = [
            [
                _ResidualDetection(
                    detection_id=1,
                    frame_index=0,
                    source_path=Path("frame_00.fit"),
                    observation_time=frame_times[0],
                    x=10.0,
                    y=12.0,
                    peak_value=20.0,
                    local_snr=8.0,
                )
            ],
            [],
            [
                _ResidualDetection(
                    detection_id=2,
                    frame_index=2,
                    source_path=Path("frame_02.fit"),
                    observation_time=frame_times[2],
                    x=12.0,
                    y=12.0,
                    peak_value=20.0,
                    local_snr=8.0,
                )
            ],
            [
                _ResidualDetection(
                    detection_id=3,
                    frame_index=3,
                    source_path=Path("frame_03.fit"),
                    observation_time=frame_times[3],
                    x=13.0,
                    y=12.0,
                    peak_value=20.0,
                    local_snr=8.0,
                )
            ],
            [
                _ResidualDetection(
                    detection_id=4,
                    frame_index=4,
                    source_path=Path("frame_04.fit"),
                    observation_time=frame_times[4],
                    x=14.0,
                    y=12.0,
                    peak_value=20.0,
                    local_snr=8.0,
                )
            ],
        ]

        candidates = _link_moving_candidates(
            residual_detections_by_frame,
            frame_times=frame_times,
            pixel_scale_arcsec_per_pixel=1.5,
            minimum_candidate_frames=4,
        )

        self.assertGreaterEqual(len(candidates), 1)
        self.assertEqual([detection.frame_index for detection in candidates[0].frame_detections], [0, 2, 3, 4])
        self.assertLess(candidates[0].fit_rms_px, 0.05)

    def test_discover_unmatched_moving_candidates_links_three_frame_tracklet(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frame_paths = [root / f"frame_{index:02d}.fit" for index in range(3)]
            for path in frame_paths:
                path.write_text("placeholder", encoding="utf-8")

            class _FakeWcs:
                def pixel_to_world_values(self, x_value: float, y_value: float) -> tuple[float, float]:
                    return 100.0 + (x_value / 100.0), 20.0 + (y_value / 100.0)

            start_time = datetime(2025, 1, 14, 21, 12, tzinfo=UTC)
            frame_metadata = {
                str(path.resolve()): ObservationMetadata(
                    date_obs=start_time + timedelta(minutes=index),
                    filter_name="L",
                    exposure_seconds=60.0,
                    width=32,
                    height=32,
                    object_name="Demo",
                    frame_type="Light",
                )
                for index, path in enumerate(frame_paths)
            }
            image_data = np.zeros((32, 32), dtype=np.float32)

            def residual_side_effect(
                _residual_image: np.ndarray,
                *,
                source_path: Path,
                observation_time: datetime,
                frame_index: int,
                next_detection_id: int,
                excluded_positions,
                residual_min_snr: float = 0.0,
                residual_max_snr: float = 0.0,
                **_kwargs,
            ) -> list[_ResidualDetection]:
                del excluded_positions, residual_min_snr, residual_max_snr
                moving_x = 10.0 + frame_index
                return [
                    _ResidualDetection(
                        detection_id=next_detection_id,
                        frame_index=frame_index,
                        source_path=source_path,
                        observation_time=observation_time,
                        x=moving_x,
                        y=12.0,
                        peak_value=25.0,
                        local_snr=8.0,
                    )
                ]

            with (
                patch("photometry_app.core.discovery.read_header_and_shape", return_value=({}, 32, 32)),
                patch("photometry_app.core.discovery._validated_celestial_wcs", return_value=(_FakeWcs(), [])),
                patch("photometry_app.core.discovery.read_image_data", return_value=image_data),
                patch("photometry_app.core.discovery._reproject_image_data", side_effect=lambda data, *_args, **_kwargs: data),
                patch("photometry_app.core.discovery._known_positions_by_frame", return_value={}),
                patch("photometry_app.core.discovery._reference_pixel_scale_arcsec", return_value=1.5),
                patch("photometry_app.core.discovery._detect_residual_sources", side_effect=residual_side_effect),
            ):
                result = discover_unmatched_moving_candidates(
                    frame_paths,
                    frame_metadata=frame_metadata,
                    known_detection_result=None,
                    reference_path=frame_paths[0],
                )

        self.assertEqual(result.candidate_count, 1)
        self.assertEqual(result.aligned_frame_count, 3)
        self.assertEqual(result.candidates[0].candidate_id, "C1")

    def test_discover_unmatched_moving_candidates_can_bin_working_frames(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frame_paths = [root / f"frame_{index:02d}.fit" for index in range(3)]
            for path in frame_paths:
                path.write_text("placeholder", encoding="utf-8")

            class _FakeWcs:
                def pixel_to_world_values(self, x_value: float, y_value: float) -> tuple[float, float]:
                    return 100.0 + (x_value / 100.0), 20.0 + (y_value / 100.0)

                def world_to_pixel_values(self, ra_deg: float, dec_deg: float) -> tuple[float, float]:
                    return (ra_deg - 100.0) * 100.0, (dec_deg - 20.0) * 100.0

            start_time = datetime(2025, 1, 14, 21, 12, tzinfo=UTC)
            frame_metadata = {
                str(path.resolve()): ObservationMetadata(
                    date_obs=start_time + timedelta(minutes=index),
                    filter_name="L",
                    exposure_seconds=60.0,
                    width=32,
                    height=32,
                    object_name="Demo",
                    frame_type="Light",
                )
                for index, path in enumerate(frame_paths)
            }
            image_data = np.ones((32, 32), dtype=np.float32)

            def residual_side_effect(
                residual_image: np.ndarray,
                *,
                source_path: Path,
                observation_time: datetime,
                frame_index: int,
                next_detection_id: int,
                excluded_positions,
                residual_min_snr: float = 0.0,
                residual_max_snr: float = 0.0,
                **_kwargs,
            ) -> list[_ResidualDetection]:
                del source_path, observation_time, excluded_positions, residual_min_snr, residual_max_snr
                self.assertEqual(residual_image.shape, (16, 16))
                return [
                    _ResidualDetection(
                        detection_id=next_detection_id,
                        frame_index=frame_index,
                        source_path=frame_paths[frame_index],
                        observation_time=start_time + timedelta(minutes=frame_index),
                        x=5.0 + frame_index,
                        y=6.0,
                        peak_value=25.0,
                        local_snr=8.0,
                    )
                ]

            with (
                patch("photometry_app.core.discovery.read_header_and_shape", return_value=({}, 32, 32)),
                patch("photometry_app.core.discovery._validated_celestial_wcs", return_value=(_FakeWcs(), [])),
                patch("photometry_app.core.discovery.read_image_data", return_value=image_data),
                patch("photometry_app.core.discovery._reference_pixel_scale_arcsec", return_value=1.5),
                patch("photometry_app.core.discovery._detect_residual_sources", side_effect=residual_side_effect),
            ):
                result = discover_unmatched_moving_candidates(
                    frame_paths,
                    frame_metadata=frame_metadata,
                    known_detection_result=None,
                    reference_path=frame_paths[0],
                    assume_aligned=True,
                    binning_factor=2,
                )

        self.assertEqual(result.candidate_count, 1)

    def test_discover_unmatched_moving_candidates_can_use_final_synthetic_sweep_after_empty_residual_linking(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frame_paths = [root / f"frame_{index:02d}.fit" for index in range(4)]
            for path in frame_paths:
                path.write_text("placeholder", encoding="utf-8")

            class _FakeWcs:
                def pixel_to_world_values(self, x_value: float, y_value: float) -> tuple[float, float]:
                    return 100.0 + (x_value / 100.0), 20.0 + (y_value / 100.0)

            start_time = datetime(2025, 1, 14, 21, 12, tzinfo=UTC)
            frame_metadata = {
                str(path.resolve()): ObservationMetadata(
                    date_obs=start_time + timedelta(minutes=index * 5),
                    filter_name="L",
                    exposure_seconds=60.0,
                    width=32,
                    height=32,
                    object_name="Demo",
                    frame_type="Light",
                )
                for index, path in enumerate(frame_paths)
            }

            def _synthetic_frame(x_pos: int, y_pos: int) -> np.ndarray:
                image = np.zeros((32, 32), dtype=np.float32)
                image[y_pos, x_pos] = 20.0
                image[y_pos, x_pos - 1] = 8.0
                image[y_pos, x_pos + 1] = 8.0
                image[y_pos - 1, x_pos] = 8.0
                image[y_pos + 1, x_pos] = 8.0
                image[y_pos - 1, x_pos - 1] = 4.0
                image[y_pos - 1, x_pos + 1] = 4.0
                image[y_pos + 1, x_pos - 1] = 4.0
                image[y_pos + 1, x_pos + 1] = 4.0
                return image

            images = {
                str(path.resolve()): _synthetic_frame(10 + index, 15)
                for index, path in enumerate(frame_paths)
            }

            with (
                patch("photometry_app.core.discovery.read_header_and_shape", return_value=({}, 32, 32)),
                patch("photometry_app.core.discovery._validated_celestial_wcs", return_value=(_FakeWcs(), [])),
                patch("photometry_app.core.discovery.read_image_data", side_effect=lambda path: images[str(path.resolve())]),
                patch("photometry_app.core.discovery._reference_pixel_scale_arcsec", return_value=1.5),
                patch(
                    "photometry_app.core.discovery._scan_frames_for_residual_detections",
                    side_effect=lambda batch_paths, **_: [[] for _ in batch_paths],
                ),
            ):
                result = discover_unmatched_moving_candidates(
                    frame_paths,
                    frame_metadata=frame_metadata,
                    known_detection_result=None,
                    reference_path=frame_paths[0],
                    assume_aligned=True,
                    enable_synthetic_sweep=True,
                    synthetic_sweep_max_motion_px_per_hour=12.0,
                    synthetic_sweep_motion_step_px_per_hour=1.0,
                    synthetic_sweep_angle_step_deg=45.0,
                    synthetic_sweep_min_stacked_snr=5.0,
                )

        self.assertGreaterEqual(result.candidate_count, 1)
        self.assertTrue(
            any(
                len(candidate.frame_detections) == 4 and abs(candidate.motion_px_per_hour - 12.0) < 1e-6
                for candidate in result.candidates
            )
        )
        self.assertIn("synthetic sweep", result.report_text.lower())

    def test_discover_synthetic_sweep_can_focus_on_main_belt_direction_and_save_stacks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frame_paths = [root / f"frame_{index:02d}.fit" for index in range(4)]
            for path in frame_paths:
                path.write_text("placeholder", encoding="utf-8")

            class _FakeWcs:
                def pixel_to_world_values(self, x_value: float, y_value: float) -> tuple[float, float]:
                    return 100.0 + ((x_value - 10.0) / 60.0), 20.0 + ((y_value - 15.0) / 60.0)

                def world_to_pixel_values(self, ra_deg: float, dec_deg: float) -> tuple[float, float]:
                    return 10.0 + ((ra_deg - 100.0) * 60.0), 15.0 + ((dec_deg - 20.0) * 60.0)

            start_time = datetime(2025, 1, 14, 21, 12, tzinfo=UTC)
            frame_metadata = {
                str(path.resolve()): ObservationMetadata(
                    date_obs=start_time + timedelta(minutes=index * 5),
                    filter_name="L",
                    exposure_seconds=60.0,
                    width=32,
                    height=32,
                    object_name="Demo",
                    frame_type="Light",
                )
                for index, path in enumerate(frame_paths)
            }

            def _synthetic_frame(x_pos: int, y_pos: int) -> np.ndarray:
                image = np.zeros((32, 32), dtype=np.float32)
                image[y_pos, x_pos] = 20.0
                image[y_pos, x_pos - 1] = 8.0
                image[y_pos, x_pos + 1] = 8.0
                image[y_pos - 1, x_pos] = 8.0
                image[y_pos + 1, x_pos] = 8.0
                return image

            images = {
                str(path.resolve()): _synthetic_frame(10 + index, 15)
                for index, path in enumerate(frame_paths)
            }
            known_detection_result = SolarSystemDetectionResult(
                source_path=frame_paths[0],
                solved_field=SolvedField(center_ra_deg=100.0, center_dec_deg=20.0, radius_deg=1.0, width=32, height=32, wcs_path=frame_paths[0]),
                observation_time=start_time,
                prediction_time=start_time,
                exposure_seconds=60.0,
                filter_name="L",
                pixel_scale_arcsec_per_pixel=1.5,
                field_width_deg=0.02,
                field_height_deg=0.02,
                magnitude_limit=18.0,
                used_astrometry_fallback=False,
                detections=[
                    SolarSystemDetection(
                        name="(1) Ceres",
                        designation="1",
                        object_type="Asteroid",
                        orbit_class="main-belt",
                        predicted_ra_deg=100.0,
                        predicted_dec_deg=20.0,
                        predicted_x=10.0,
                        predicted_y=15.0,
                        predicted_magnitude=12.0,
                        ra_rate_arcsec_per_hour=720.0,
                        dec_rate_arcsec_per_hour=0.0,
                        motion_rate_arcsec_per_hour=720.0,
                        expected_trail_length_px=1.0,
                        positional_uncertainty_arcsec=0.2,
                        altitude_deg=50.0,
                        likely_visible=True,
                        confidence_score=0.9,
                        status="Likely visible",
                    )
                ],
                summary_text="1 predicted object",
            )

            with (
                patch("photometry_app.core.discovery.read_header_and_shape", return_value=({}, 32, 32)),
                patch("photometry_app.core.discovery._validated_celestial_wcs", return_value=(_FakeWcs(), [])),
                patch("photometry_app.core.discovery.read_image_data", side_effect=lambda path: images[str(path.resolve())]),
                patch("photometry_app.core.discovery._reference_pixel_scale_arcsec", return_value=1.5),
                patch(
                    "photometry_app.core.discovery._scan_frames_for_residual_detections",
                    side_effect=lambda batch_paths, **_: [[] for _ in batch_paths],
                ),
            ):
                result = discover_unmatched_moving_candidates(
                    frame_paths,
                    frame_metadata=frame_metadata,
                    known_detection_result=known_detection_result,
                    reference_path=frame_paths[0],
                    assume_aligned=True,
                    enable_synthetic_sweep=True,
                    synthetic_sweep_max_motion_px_per_hour=12.0,
                    synthetic_sweep_motion_step_px_per_hour=12.0,
                    synthetic_sweep_angle_step_deg=45.0,
                    synthetic_sweep_direction_focus="main_belt",
                    synthetic_sweep_direction_focus_half_width_deg=10.0,
                    synthetic_sweep_min_stacked_snr=5.0,
                    synthetic_sweep_save_stacks=True,
                )

            output_files = sorted((root / "synthetic_track").glob("*.fits"))

        self.assertGreaterEqual(result.recovered_known_count, 1)
        self.assertEqual(len(output_files), 1)
        self.assertTrue(all("speed_12p0pxph" in path.name for path in output_files))
        self.assertTrue(all("width_10p0deg" in path.name for path in output_files))

    def test_discover_batched_synthetic_sweep_runs_in_each_batch_and_saves_batch_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frame_paths = [root / f"frame_{index:02d}.fit" for index in range(6)]
            for path in frame_paths:
                path.write_text("placeholder", encoding="utf-8")

            class _FakeWcs:
                def pixel_to_world_values(self, x_value: float, y_value: float) -> tuple[float, float]:
                    return 100.0 + (x_value / 100.0), 20.0 + (y_value / 100.0)

            start_time = datetime(2025, 1, 14, 21, 12, tzinfo=UTC)
            frame_metadata = {
                str(path.resolve()): ObservationMetadata(
                    date_obs=start_time + timedelta(minutes=index * 5),
                    filter_name="L",
                    exposure_seconds=60.0,
                    width=32,
                    height=32,
                    object_name="Demo",
                    frame_type="Light",
                )
                for index, path in enumerate(frame_paths)
            }

            def _synthetic_frame(x_pos: int, y_pos: int) -> np.ndarray:
                image = np.zeros((32, 32), dtype=np.float32)
                image[y_pos, x_pos] = 20.0
                image[y_pos, x_pos - 1] = 8.0
                image[y_pos, x_pos + 1] = 8.0
                image[y_pos - 1, x_pos] = 8.0
                image[y_pos + 1, x_pos] = 8.0
                return image

            images = {
                str(path.resolve()): _synthetic_frame(10 + index, 15)
                for index, path in enumerate(frame_paths)
            }

            with (
                patch("photometry_app.core.discovery.read_header_and_shape", return_value=({}, 32, 32)),
                patch("photometry_app.core.discovery._validated_celestial_wcs", return_value=(_FakeWcs(), [])),
                patch("photometry_app.core.discovery.read_image_data", side_effect=lambda path: images[str(path.resolve())]),
                patch("photometry_app.core.discovery._reference_pixel_scale_arcsec", return_value=1.5),
                patch(
                    "photometry_app.core.discovery._scan_frames_for_residual_detections",
                    side_effect=lambda batch_paths, **_: [[] for _ in batch_paths],
                ),
            ):
                result = discover_unmatched_moving_candidates(
                    frame_paths,
                    frame_metadata=frame_metadata,
                    known_detection_result=None,
                    reference_path=frame_paths[0],
                    assume_aligned=True,
                    frames_per_batch=4,
                    enable_synthetic_sweep=True,
                    synthetic_sweep_max_motion_px_per_hour=12.0,
                    synthetic_sweep_motion_step_px_per_hour=12.0,
                    synthetic_sweep_angle_step_deg=45.0,
                    synthetic_sweep_min_stacked_snr=5.0,
                    synthetic_sweep_save_stacks=True,
                )

            batch_output_directories = sorted(path for path in (root / "synthetic_track").glob("batch_*") if path.is_dir())
            batch_output_files = sorted((root / "synthetic_track").glob("batch_*/*.fits"))

        self.assertGreaterEqual(result.candidate_count, 1)
        self.assertEqual([path.name for path in batch_output_directories], ["batch_01", "batch_02"])
        self.assertTrue(any(path.parent.name == "batch_01" for path in batch_output_files))
        self.assertTrue(any(path.parent.name == "batch_02" for path in batch_output_files))
        self.assertTrue(all("speed_12p0pxph" in path.name for path in batch_output_files))
        self.assertIn("optional final synthetic sweep ran", result.methods_summary_text.lower())

    def test_cleanup_stale_discovery_temp_cache_removes_old_run_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_root = Path(temp_dir)
            stale_run = cache_root / "discover-old"
            stale_run.mkdir()
            fresh_run = cache_root / "discover-new"
            fresh_run.mkdir()
            old_timestamp = datetime.now(tz=UTC).timestamp() - 3600.0
            os.utime(stale_run, (old_timestamp, old_timestamp))

            cleanup_stale_discovery_temp_cache(cache_root, max_age_seconds=60)

            self.assertFalse(stale_run.exists())
            self.assertTrue(fresh_run.exists())

    def test_discover_unmatched_moving_candidates_skips_reprojection_when_frames_are_already_aligned(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frame_paths = [root / f"frame_{index:02d}.fit" for index in range(3)]
            for path in frame_paths:
                path.write_text("placeholder", encoding="utf-8")

            start_time = datetime(2025, 1, 14, 21, 12, tzinfo=UTC)
            frame_metadata = {
                str(path.resolve()): ObservationMetadata(
                    date_obs=start_time + timedelta(minutes=index),
                    filter_name="L",
                    exposure_seconds=60.0,
                    width=32,
                    height=32,
                    object_name="Demo",
                    frame_type="Light",
                )
                for index, path in enumerate(frame_paths)
            }
            image_data = np.zeros((32, 32), dtype=np.float32)

            def residual_side_effect(
                _residual_image: np.ndarray,
                *,
                source_path: Path,
                observation_time: datetime,
                frame_index: int,
                next_detection_id: int,
                excluded_positions,
                residual_min_snr: float = 0.0,
                residual_max_snr: float = 0.0,
                **_kwargs,
            ) -> list[_ResidualDetection]:
                del source_path, observation_time, excluded_positions, residual_min_snr, residual_max_snr
                return [
                    _ResidualDetection(
                        detection_id=next_detection_id,
                        frame_index=frame_index,
                        source_path=frame_paths[frame_index],
                        observation_time=start_time + timedelta(minutes=frame_index),
                        x=10.0 + frame_index,
                        y=12.0,
                        peak_value=25.0,
                        local_snr=8.0,
                    )
                ]

            with (
                patch("photometry_app.core.discovery.read_header_and_shape", return_value=({}, 32, 32)),
                patch("photometry_app.core.discovery._validated_celestial_wcs", return_value=(object(), [])),
                patch("photometry_app.core.discovery.read_image_data", return_value=image_data),
                patch("photometry_app.core.discovery._reproject_image_data") as reproject_image_data,
                patch("photometry_app.core.discovery._reference_pixel_scale_arcsec", return_value=1.5),
                patch("photometry_app.core.discovery._detect_residual_sources", side_effect=residual_side_effect),
            ):
                result = discover_unmatched_moving_candidates(
                    frame_paths,
                    frame_metadata=frame_metadata,
                    known_detection_result=None,
                    reference_path=frame_paths[0],
                    assume_aligned=True,
                )

        reproject_image_data.assert_not_called()
        self.assertEqual(result.candidate_count, 1)
        self.assertIn("from 3 frame(s)", result.summary_text)

    def test_recover_known_moving_objects_matches_likely_visible_known_detection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frame_paths = [root / f"frame_{index:02d}.fit" for index in range(3)]
            for path in frame_paths:
                path.write_text("placeholder", encoding="utf-8")

            start_time = datetime(2025, 1, 14, 21, 12, tzinfo=UTC)
            frame_metadata = {
                str(path.resolve()): ObservationMetadata(
                    date_obs=start_time + timedelta(minutes=index),
                    filter_name="L",
                    exposure_seconds=60.0,
                    width=32,
                    height=32,
                    object_name="Demo",
                    frame_type="Light",
                )
                for index, path in enumerate(frame_paths)
            }
            image_data = np.zeros((32, 32), dtype=np.float32)
            known_detection_result = SolarSystemDetectionResult(
                source_path=frame_paths[0],
                solved_field=SolvedField(center_ra_deg=10.0, center_dec_deg=20.0, radius_deg=1.0, width=32, height=32, wcs_path=frame_paths[0]),
                observation_time=start_time,
                prediction_time=start_time,
                exposure_seconds=60.0,
                filter_name="L",
                pixel_scale_arcsec_per_pixel=1.5,
                field_width_deg=0.02,
                field_height_deg=0.02,
                magnitude_limit=18.0,
                used_astrometry_fallback=False,
                detections=[
                    SolarSystemDetection(
                        name="(1) Ceres",
                        designation="1",
                        object_type="Asteroid",
                        orbit_class="main-belt",
                        predicted_ra_deg=10.0,
                        predicted_dec_deg=20.0,
                        predicted_x=10.0,
                        predicted_y=12.0,
                        predicted_magnitude=12.3,
                        ra_rate_arcsec_per_hour=3600.0,
                        dec_rate_arcsec_per_hour=0.0,
                        motion_rate_arcsec_per_hour=3600.0,
                        expected_trail_length_px=1.0,
                        positional_uncertainty_arcsec=0.2,
                        altitude_deg=50.0,
                        likely_visible=True,
                        confidence_score=0.92,
                        status="Likely visible",
                    )
                ],
                summary_text="1 predicted object",
            )

            class _FakeWcs:
                def __init__(self, frame_index: int) -> None:
                    self._frame_index = frame_index

                def world_to_pixel_values(self, ra_deg: float, dec_deg: float) -> tuple[float, float]:
                    del dec_deg
                    return ra_deg + self._frame_index, 12.0

            def residual_side_effect(
                _residual_image: np.ndarray,
                *,
                source_path: Path,
                observation_time: datetime,
                frame_index: int,
                next_detection_id: int,
                excluded_positions,
                residual_min_snr: float = 0.0,
                residual_max_snr: float = 0.0,
                **_kwargs,
            ) -> list[_ResidualDetection]:
                del source_path, observation_time, excluded_positions, residual_min_snr, residual_max_snr
                return [
                    _ResidualDetection(
                        detection_id=next_detection_id,
                        frame_index=frame_index,
                        source_path=frame_paths[frame_index],
                        observation_time=start_time + timedelta(minutes=frame_index),
                        x=10.0 + frame_index,
                        y=12.0,
                        peak_value=25.0,
                        local_snr=8.0,
                    )
                ]

            with (
                patch("photometry_app.core.discovery.read_header_and_shape", return_value=({}, 32, 32)),
                patch("photometry_app.core.discovery._validated_celestial_wcs", side_effect=lambda path, _header: (_FakeWcs(frame_paths.index(path)), [])),
                patch("photometry_app.core.discovery.read_image_data", return_value=image_data),
                patch("photometry_app.core.discovery._reference_pixel_scale_arcsec", return_value=1.5),
                patch("photometry_app.core.discovery._detect_residual_sources", side_effect=residual_side_effect),
            ):
                result = recover_known_moving_objects(
                    frame_paths,
                    frame_metadata=frame_metadata,
                    known_detection_result=known_detection_result,
                    reference_path=frame_paths[0],
                    assume_aligned=True,
                )

        self.assertEqual(result.recovered_known_count, 1)
        self.assertEqual(result.benchmark_known_count, 1)
        self.assertEqual(len(result.unmatched_candidates), 0)
        self.assertEqual(result.recovered_known_objects[0].detection.name, "(1) Ceres")
        self.assertIn("Recovered 1 of 1 likely visible known object", result.summary_text)

    def test_discover_unmatched_moving_candidates_separates_known_recoveries_from_potential_discoveries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frame_paths = [root / f"frame_{index:02d}.fit" for index in range(3)]
            for path in frame_paths:
                path.write_text("placeholder", encoding="utf-8")

            start_time = datetime(2025, 1, 14, 21, 12, tzinfo=UTC)
            frame_metadata = {
                str(path.resolve()): ObservationMetadata(
                    date_obs=start_time + timedelta(minutes=index),
                    filter_name="L",
                    exposure_seconds=60.0,
                    width=64,
                    height=64,
                    object_name="Demo",
                    frame_type="Light",
                )
                for index, path in enumerate(frame_paths)
            }

            known_detection_result = SolarSystemDetectionResult(
                source_path=frame_paths[0],
                solved_field=SolvedField(center_ra_deg=10.0, center_dec_deg=20.0, radius_deg=1.0, width=64, height=64, wcs_path=frame_paths[0]),
                observation_time=start_time,
                prediction_time=start_time,
                exposure_seconds=60.0,
                filter_name="L",
                pixel_scale_arcsec_per_pixel=1.5,
                field_width_deg=0.04,
                field_height_deg=0.04,
                magnitude_limit=18.0,
                used_astrometry_fallback=False,
                detections=[
                    SolarSystemDetection(
                        name="(1) Ceres",
                        designation="1",
                        object_type="Asteroid",
                        orbit_class="main-belt",
                        predicted_ra_deg=10.0,
                        predicted_dec_deg=20.0,
                        predicted_x=10.0,
                        predicted_y=12.0,
                        predicted_magnitude=10.0,
                        ra_rate_arcsec_per_hour=216000.0,
                        dec_rate_arcsec_per_hour=0.0,
                        motion_rate_arcsec_per_hour=216000.0,
                        expected_trail_length_px=1.0,
                        positional_uncertainty_arcsec=0.2,
                        altitude_deg=50.0,
                        likely_visible=True,
                        confidence_score=0.92,
                        status="Likely visible",
                    ),
                    SolarSystemDetection(
                        name="(2) Pallas",
                        designation="2",
                        object_type="Asteroid",
                        orbit_class="main-belt",
                        predicted_ra_deg=30.0,
                        predicted_dec_deg=30.0,
                        predicted_x=30.0,
                        predicted_y=22.0,
                        predicted_magnitude=14.0,
                        ra_rate_arcsec_per_hour=216000.0,
                        dec_rate_arcsec_per_hour=0.0,
                        motion_rate_arcsec_per_hour=216000.0,
                        expected_trail_length_px=1.0,
                        positional_uncertainty_arcsec=0.2,
                        altitude_deg=48.0,
                        likely_visible=True,
                        confidence_score=0.89,
                        status="Likely visible",
                    ),
                ],
                summary_text="2 predicted objects",
            )
            estimate_result = SolarSystemVisibilityEstimateResult(
                source_path=frame_paths[0],
                solved_field=known_detection_result.solved_field,
                used_astrometry_fallback=False,
                tested_star_count=6,
                visible_star_count=3,
                dimmest_visible_magnitude=12.5,
                summary_text="Estimated visible limit: Gaia G 12.5.",
            )

            class _FakeWcs:
                def world_to_pixel_values(self, ra_deg: float, dec_deg: float) -> tuple[float, float]:
                    return ra_deg, dec_deg - 8.0

            candidates = [
                MovingObjectCandidate(
                    candidate_id="",
                    frame_detections=(
                        MovingObjectCandidateDetection(frame_paths[0], start_time, 0, 10.0, 12.0, 25.0, 8.5),
                        MovingObjectCandidateDetection(frame_paths[1], start_time + timedelta(minutes=1), 1, 11.0, 12.0, 24.0, 8.1),
                        MovingObjectCandidateDetection(frame_paths[2], start_time + timedelta(minutes=2), 2, 12.0, 12.0, 23.0, 7.9),
                    ),
                    average_snr=8.17,
                    peak_value=25.0,
                    fit_rms_px=0.2,
                    motion_px_per_hour=60.0,
                    motion_arcsec_per_hour=90.0,
                    displacement_px=2.0,
                    start_x=10.0,
                    start_y=12.0,
                    end_x=12.0,
                    end_y=12.0,
                    summary_text="",
                ),
                MovingObjectCandidate(
                    candidate_id="",
                    frame_detections=(
                        MovingObjectCandidateDetection(frame_paths[0], start_time, 0, 30.0, 22.0, 21.0, 6.8),
                        MovingObjectCandidateDetection(frame_paths[1], start_time + timedelta(minutes=1), 1, 31.0, 22.0, 20.0, 6.6),
                        MovingObjectCandidateDetection(frame_paths[2], start_time + timedelta(minutes=2), 2, 32.0, 22.0, 19.0, 6.4),
                    ),
                    average_snr=6.6,
                    peak_value=21.0,
                    fit_rms_px=0.3,
                    motion_px_per_hour=60.0,
                    motion_arcsec_per_hour=90.0,
                    displacement_px=2.0,
                    start_x=30.0,
                    start_y=22.0,
                    end_x=32.0,
                    end_y=22.0,
                    summary_text="",
                ),
                MovingObjectCandidate(
                    candidate_id="",
                    frame_detections=(
                        MovingObjectCandidateDetection(frame_paths[0], start_time, 0, 50.0, 32.0, 18.0, 6.0),
                        MovingObjectCandidateDetection(frame_paths[1], start_time + timedelta(minutes=1), 1, 51.0, 32.0, 17.0, 5.8),
                        MovingObjectCandidateDetection(frame_paths[2], start_time + timedelta(minutes=2), 2, 52.0, 32.0, 16.0, 5.7),
                    ),
                    average_snr=5.83,
                    peak_value=18.0,
                    fit_rms_px=0.4,
                    motion_px_per_hour=60.0,
                    motion_arcsec_per_hour=90.0,
                    displacement_px=2.0,
                    start_x=50.0,
                    start_y=32.0,
                    end_x=52.0,
                    end_y=32.0,
                    summary_text="",
                ),
            ]

            def search_side_effect(*_args, **_kwargs):
                return (
                    frame_paths[0],
                    np.ones((8, 8), dtype=np.float32),
                    [start_time + timedelta(minutes=index) for index in range(3)],
                    [_FakeWcs(), _FakeWcs(), _FakeWcs()],
                    1.5,
                    candidates,
                )

            with patch("photometry_app.core.discovery._search_moving_candidates", side_effect=search_side_effect):
                result = discover_unmatched_moving_candidates(
                    frame_paths,
                    frame_metadata=frame_metadata,
                    known_detection_result=known_detection_result,
                    reference_path=frame_paths[0],
                    estimate_result=estimate_result,
                )

        self.assertIsInstance(result, MovingObjectDiscoveryResult)
        self.assertEqual(result.recovered_known_count, 2)
        self.assertEqual(result.benchmark_known_count, 1)
        self.assertEqual(result.benchmark_recovered_count, 1)
        self.assertEqual(result.candidate_count, 1)
        self.assertEqual(result.candidates[0].candidate_id, "C1")
        self.assertEqual(result.recovered_known_objects[0].detection.name, "(1) Ceres")
        self.assertIn("Generate predicted 2 known object(s) in the current field.", result.report_text)
        self.assertIn("within the estimated visible limit", result.report_text)

    def test_discover_unmatched_moving_candidates_associates_known_objects_by_sky_coordinates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frame_paths = [root / f"frame_{index:02d}.fit" for index in range(3)]
            for path in frame_paths:
                path.write_text("placeholder", encoding="utf-8")

            start_time = datetime(2025, 1, 14, 21, 12, tzinfo=UTC)
            frame_metadata = {
                str(path.resolve()): ObservationMetadata(
                    date_obs=start_time + timedelta(minutes=index),
                    filter_name="L",
                    exposure_seconds=60.0,
                    width=64,
                    height=64,
                    object_name="Demo",
                    frame_type="Light",
                )
                for index, path in enumerate(frame_paths)
            }

            known_detection_result = SolarSystemDetectionResult(
                source_path=frame_paths[0],
                solved_field=SolvedField(center_ra_deg=10.0, center_dec_deg=20.0, radius_deg=1.0, width=64, height=64, wcs_path=frame_paths[0]),
                observation_time=start_time,
                prediction_time=start_time,
                exposure_seconds=60.0,
                filter_name="L",
                pixel_scale_arcsec_per_pixel=1.5,
                field_width_deg=0.04,
                field_height_deg=0.04,
                magnitude_limit=18.0,
                used_astrometry_fallback=False,
                detections=[
                    SolarSystemDetection(
                        name="3I/ATLAS",
                        designation="3I",
                        object_type="Comet",
                        orbit_class="interstellar",
                        predicted_ra_deg=10.0,
                        predicted_dec_deg=20.0,
                        predicted_x=10.0,
                        predicted_y=12.0,
                        predicted_magnitude=15.1,
                        ra_rate_arcsec_per_hour=216000.0,
                        dec_rate_arcsec_per_hour=0.0,
                        motion_rate_arcsec_per_hour=216000.0,
                        expected_trail_length_px=1.5,
                        positional_uncertainty_arcsec=12.0,
                        altitude_deg=45.0,
                        likely_visible=True,
                        confidence_score=0.95,
                        status="Likely visible",
                    ),
                ],
                summary_text="1 predicted object",
            )

            class _FakeWcs:
                def world_to_pixel_values(self, ra_deg: float, dec_deg: float) -> tuple[float, float]:
                    del dec_deg
                    return ra_deg, 12.0

            candidate = MovingObjectCandidate(
                candidate_id="",
                frame_detections=(
                    MovingObjectCandidateDetection(frame_paths[0], start_time, 0, 40.0, 30.0, 25.0, 18.0, 10.0, 20.0),
                    MovingObjectCandidateDetection(frame_paths[1], start_time + timedelta(minutes=1), 1, 41.0, 30.0, 24.0, 17.0, 11.0, 20.0),
                    MovingObjectCandidateDetection(frame_paths[2], start_time + timedelta(minutes=2), 2, 42.0, 30.0, 23.0, 16.0, 12.0, 20.0),
                ),
                average_snr=17.0,
                peak_value=25.0,
                fit_rms_px=0.2,
                motion_px_per_hour=60.0,
                motion_arcsec_per_hour=90.0,
                displacement_px=2.0,
                start_x=40.0,
                start_y=30.0,
                end_x=42.0,
                end_y=30.0,
                summary_text="",
            )

            def search_side_effect(*_args, **_kwargs):
                return (
                    frame_paths[0],
                    np.ones((8, 8), dtype=np.float32),
                    [start_time + timedelta(minutes=index) for index in range(3)],
                    [_FakeWcs(), _FakeWcs(), _FakeWcs()],
                    1.5,
                    [candidate],
                )

            with patch("photometry_app.core.discovery._search_moving_candidates", side_effect=search_side_effect):
                result = discover_unmatched_moving_candidates(
                    frame_paths,
                    frame_metadata=frame_metadata,
                    known_detection_result=known_detection_result,
                    reference_path=frame_paths[0],
                )

        self.assertEqual(result.recovered_known_count, 1)
        self.assertEqual(result.candidate_count, 0)
        self.assertEqual(result.recovered_known_objects[0].detection.name, "3I/ATLAS")

    def test_discover_unmatched_moving_candidates_suppresses_batched_duplicates_of_recovered_known_object(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frame_paths = [root / f"frame_{index:02d}.fit" for index in range(6)]
            for path in frame_paths:
                path.write_text("placeholder", encoding="utf-8")

            start_time = datetime(2025, 1, 14, 21, 12, tzinfo=UTC)
            frame_metadata = {
                str(path.resolve()): ObservationMetadata(
                    date_obs=start_time + timedelta(minutes=index),
                    filter_name="L",
                    exposure_seconds=60.0,
                    width=64,
                    height=64,
                    object_name="Demo",
                    frame_type="Light",
                )
                for index, path in enumerate(frame_paths)
            }

            known_detection_result = SolarSystemDetectionResult(
                source_path=frame_paths[0],
                solved_field=SolvedField(center_ra_deg=10.0, center_dec_deg=20.0, radius_deg=1.0, width=64, height=64, wcs_path=frame_paths[0]),
                observation_time=start_time,
                prediction_time=start_time,
                exposure_seconds=60.0,
                filter_name="L",
                pixel_scale_arcsec_per_pixel=1.5,
                field_width_deg=0.04,
                field_height_deg=0.04,
                magnitude_limit=18.0,
                used_astrometry_fallback=False,
                detections=[
                    SolarSystemDetection(
                        name="3I/ATLAS",
                        designation="3I",
                        object_type="Comet",
                        orbit_class="interstellar",
                        predicted_ra_deg=10.0,
                        predicted_dec_deg=12.0,
                        predicted_x=10.0,
                        predicted_y=12.0,
                        predicted_magnitude=15.1,
                        ra_rate_arcsec_per_hour=0.0,
                        dec_rate_arcsec_per_hour=0.0,
                        motion_rate_arcsec_per_hour=90.0,
                        expected_trail_length_px=1.5,
                        positional_uncertainty_arcsec=0.5,
                        altitude_deg=45.0,
                        likely_visible=True,
                        confidence_score=0.95,
                        status="Likely visible",
                    ),
                ],
                summary_text="1 predicted object",
            )

            class _FakeWcs:
                def world_to_pixel_values(self, ra_deg: float, dec_deg: float) -> tuple[float, float]:
                    return ra_deg, dec_deg

            def _candidate_for_batch(batch_paths: list[Path]) -> MovingObjectCandidate:
                batch_indices = [int(path.stem.split("_")[-1]) for path in batch_paths]
                x_positions = [10.0 + float(index) for index in batch_indices[:3]]
                frame_detections = tuple(
                    MovingObjectCandidateDetection(
                        batch_paths[offset],
                        start_time + timedelta(minutes=batch_indices[offset]),
                        offset,
                        x_positions[offset],
                        12.0,
                        25.0 - float(offset),
                        17.0 - float(offset) * 0.5,
                    )
                    for offset in range(3)
                )
                return MovingObjectCandidate(
                    candidate_id="C1",
                    frame_detections=frame_detections,
                    average_snr=17.0 if batch_indices[1] == 1 else 16.0,
                    peak_value=25.0,
                    fit_rms_px=0.2,
                    motion_px_per_hour=60.0,
                    motion_arcsec_per_hour=90.0,
                    displacement_px=2.0,
                    start_x=x_positions[0],
                    start_y=12.0,
                    end_x=x_positions[2],
                    end_y=12.0,
                    summary_text="",
                )

            def search_side_effect(batch_paths: list[Path], **_kwargs):
                batch_indices = [int(path.stem.split("_")[-1]) for path in batch_paths]
                return (
                    batch_paths[0],
                    np.ones((8, 8), dtype=np.float32),
                    [start_time + timedelta(minutes=index) for index in batch_indices],
                    [_FakeWcs() for _ in batch_paths],
                    1.5,
                    [_candidate_for_batch(batch_paths)],
                )

            def predict_side_effect(
                _detection: SolarSystemDetection,
                *,
                reference_observation_time: datetime,
                observation_time: datetime,
            ) -> tuple[float, float]:
                del reference_observation_time
                minute_offset = int(round((observation_time - start_time).total_seconds() / 60.0))
                return 10.0 + float(minute_offset), 12.0

            with (
                patch("photometry_app.core.discovery._search_moving_candidates", side_effect=search_side_effect),
                patch("photometry_app.core.discovery.predict_detection_coordinates", side_effect=predict_side_effect),
            ):
                result = discover_unmatched_moving_candidates(
                    frame_paths,
                    frame_metadata=frame_metadata,
                    known_detection_result=known_detection_result,
                    reference_path=frame_paths[0],
                    frames_per_batch=4,
                )

        self.assertEqual(result.recovered_known_count, 1)
        self.assertEqual(result.candidate_count, 0)
        self.assertEqual(result.recovered_known_objects[0].detection.name, "3I/ATLAS")
        self.assertAlmostEqual(result.recovered_known_objects[0].candidate.average_snr, 17.0)

    def test_discover_unmatched_moving_candidates_batches_search_windows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frame_paths = [root / f"frame_{index:02d}.fit" for index in range(6)]
            for path in frame_paths:
                path.write_text("placeholder", encoding="utf-8")

            start_time = datetime(2025, 1, 14, 21, 12, tzinfo=UTC)
            frame_metadata = {
                str(path.resolve()): ObservationMetadata(
                    date_obs=start_time + timedelta(minutes=index),
                    filter_name="L",
                    exposure_seconds=60.0,
                    width=32,
                    height=32,
                    object_name="Demo",
                    frame_type="Light",
                )
                for index, path in enumerate(frame_paths)
            }

            def search_side_effect(
                batch_paths: list[Path],
                **_kwargs,
            ):
                if len(batch_paths) == 4 and batch_paths[1].name == "frame_01.fit":
                    candidates = [
                        MovingObjectCandidate(
                            candidate_id="C1",
                            frame_detections=(
                                MovingObjectCandidateDetection(batch_paths[0], start_time, 0, 10.0, 12.0, 25.0, 8.0),
                                MovingObjectCandidateDetection(batch_paths[1], start_time + timedelta(minutes=1), 1, 11.0, 12.0, 24.0, 7.5),
                                MovingObjectCandidateDetection(batch_paths[2], start_time + timedelta(minutes=2), 2, 12.0, 12.0, 23.0, 7.0),
                            ),
                            average_snr=7.5,
                            peak_value=25.0,
                            fit_rms_px=0.2,
                            motion_px_per_hour=60.0,
                            motion_arcsec_per_hour=90.0,
                            displacement_px=2.0,
                            start_x=10.0,
                            start_y=12.0,
                            end_x=12.0,
                            end_y=12.0,
                            summary_text="",
                        )
                    ]
                else:
                    candidates = [
                        MovingObjectCandidate(
                            candidate_id="C1",
                            frame_detections=(
                                MovingObjectCandidateDetection(batch_paths[0], start_time, 0, 30.0, 22.0, 18.0, 6.5),
                                MovingObjectCandidateDetection(batch_paths[1], start_time + timedelta(minutes=3), 1, 31.0, 22.0, 18.0, 6.0),
                                MovingObjectCandidateDetection(batch_paths[2], start_time + timedelta(minutes=4), 2, 32.0, 22.0, 18.0, 6.2),
                            ),
                            average_snr=6.23,
                            peak_value=18.0,
                            fit_rms_px=0.3,
                            motion_px_per_hour=40.0,
                            motion_arcsec_per_hour=60.0,
                            displacement_px=2.0,
                            start_x=30.0,
                            start_y=22.0,
                            end_x=32.0,
                            end_y=22.0,
                            summary_text="",
                        )
                    ]
                return batch_paths[0], np.ones((4, 4), dtype=np.float32), [], [], 1.5, candidates

            with patch("photometry_app.core.discovery._search_moving_candidates", side_effect=search_side_effect) as search_mock:
                result = discover_unmatched_moving_candidates(
                    frame_paths,
                    frame_metadata=frame_metadata,
                    known_detection_result=None,
                    reference_path=frame_paths[0],
                    frames_per_batch=4,
                )

        self.assertEqual(search_mock.call_count, 2)
        self.assertEqual(result.candidate_count, 2)
        self.assertEqual([d.frame_index for d in result.candidates[0].frame_detections], [0, 1, 2])
        self.assertEqual([d.frame_index for d in result.candidates[1].frame_detections], [0, 3, 4])

    def test_discover_unmatched_moving_candidates_can_limit_search_to_single_batch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frame_paths = [root / f"frame_{index:02d}.fit" for index in range(6)]
            for path in frame_paths:
                path.write_text("placeholder", encoding="utf-8")

            start_time = datetime(2025, 1, 14, 21, 12, tzinfo=UTC)
            frame_metadata = {
                str(path.resolve()): ObservationMetadata(
                    date_obs=start_time + timedelta(minutes=index),
                    filter_name="L",
                    exposure_seconds=60.0,
                    width=32,
                    height=32,
                    object_name="Demo",
                    frame_type="Light",
                )
                for index, path in enumerate(frame_paths)
            }
            known_detection_result = SolarSystemDetectionResult(
                source_path=frame_paths[0],
                solved_field=SolvedField(center_ra_deg=10.0, center_dec_deg=20.0, radius_deg=1.0, width=32, height=32, wcs_path=frame_paths[0]),
                observation_time=start_time,
                prediction_time=start_time,
                exposure_seconds=60.0,
                filter_name="L",
                pixel_scale_arcsec_per_pixel=1.5,
                field_width_deg=0.04,
                field_height_deg=0.04,
                magnitude_limit=18.0,
                used_astrometry_fallback=False,
                detections=[
                    SolarSystemDetection(
                        name="(1) Ceres",
                        designation="1",
                        object_type="Asteroid",
                        orbit_class="main-belt",
                        predicted_ra_deg=10.0,
                        predicted_dec_deg=12.0,
                        predicted_x=10.0,
                        predicted_y=12.0,
                        predicted_magnitude=12.3,
                        ra_rate_arcsec_per_hour=0.0,
                        dec_rate_arcsec_per_hour=0.0,
                        motion_rate_arcsec_per_hour=90.0,
                        expected_trail_length_px=1.5,
                        positional_uncertainty_arcsec=0.5,
                        altitude_deg=45.0,
                        likely_visible=True,
                        confidence_score=0.95,
                        status="Likely visible",
                    ),
                ],
                summary_text="1 predicted object",
            )
            searched_batches: list[list[str]] = []

            class _FakeWcs:
                def world_to_pixel_values(self, ra_deg: float, dec_deg: float) -> tuple[float, float]:
                    return ra_deg, dec_deg

            def search_side_effect(batch_paths: list[Path], **_kwargs):
                searched_batches.append([path.name for path in batch_paths])
                candidates = [
                    MovingObjectCandidate(
                        candidate_id="C1",
                        frame_detections=(
                            MovingObjectCandidateDetection(batch_paths[0], start_time, 0, 10.0, 12.0, 25.0, 8.0),
                            MovingObjectCandidateDetection(batch_paths[1], start_time + timedelta(minutes=1), 1, 11.0, 12.0, 24.0, 7.5),
                            MovingObjectCandidateDetection(batch_paths[2], start_time + timedelta(minutes=2), 2, 12.0, 12.0, 23.0, 7.0),
                        ),
                        average_snr=7.5,
                        peak_value=25.0,
                        fit_rms_px=0.2,
                        motion_px_per_hour=60.0,
                        motion_arcsec_per_hour=90.0,
                        displacement_px=2.0,
                        start_x=10.0,
                        start_y=12.0,
                        end_x=12.0,
                        end_y=12.0,
                        summary_text="",
                    )
                ]
                batch_indices = [int(path.stem.split("_")[-1]) for path in batch_paths]
                return (
                    batch_paths[0],
                    np.ones((4, 4), dtype=np.float32),
                    [start_time + timedelta(minutes=index) for index in batch_indices],
                    [_FakeWcs() for _ in batch_paths],
                    1.5,
                    candidates,
                )

            with patch("photometry_app.core.discovery._search_moving_candidates", side_effect=search_side_effect) as search_mock:
                result = discover_unmatched_moving_candidates(
                    frame_paths,
                    frame_metadata=frame_metadata,
                    known_detection_result=known_detection_result,
                    estimate_result=SolarSystemVisibilityEstimateResult(
                        source_path=frame_paths[0],
                        solved_field=known_detection_result.solved_field,
                        used_astrometry_fallback=False,
                        tested_star_count=1,
                        visible_star_count=1,
                        dimmest_visible_magnitude=13.0,
                        summary_text="Estimated visible limit Gaia G 13.0.",
                    ),
                    reference_path=frame_paths[0],
                    frames_per_batch=4,
                    single_batch_only=True,
                )

        self.assertEqual(search_mock.call_count, 1)
        self.assertEqual(searched_batches, [["frame_00.fit", "frame_01.fit", "frame_02.fit", "frame_03.fit"]])
        self.assertEqual(result.candidate_count, 0)
        self.assertEqual(result.benchmark_known_count, 1)
        self.assertEqual(result.recovered_known_count, 1)
        self.assertEqual(result.recovered_known_objects[0].detection.name, "(1) Ceres")

    def test_discover_unmatched_moving_candidates_batched_partial_results_keep_benchmark_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frame_paths = [root / f"frame_{index:02d}.fit" for index in range(5)]
            for path in frame_paths:
                path.write_text("placeholder", encoding="utf-8")

            start_time = datetime(2025, 1, 14, 21, 12, tzinfo=UTC)
            frame_metadata = {
                str(path.resolve()): ObservationMetadata(
                    date_obs=start_time + timedelta(minutes=index),
                    filter_name="L",
                    exposure_seconds=60.0,
                    width=64,
                    height=64,
                    object_name="Demo",
                    frame_type="Light",
                )
                for index, path in enumerate(frame_paths)
            }
            known_detection_result = SolarSystemDetectionResult(
                source_path=frame_paths[0],
                solved_field=SolvedField(center_ra_deg=10.0, center_dec_deg=12.0, radius_deg=1.0, width=64, height=64, wcs_path=frame_paths[0]),
                observation_time=start_time,
                prediction_time=start_time,
                exposure_seconds=60.0,
                filter_name="L",
                pixel_scale_arcsec_per_pixel=1.5,
                field_width_deg=0.04,
                field_height_deg=0.04,
                magnitude_limit=18.0,
                used_astrometry_fallback=False,
                detections=[
                    SolarSystemDetection(
                        name="(1) Ceres",
                        designation="1",
                        object_type="Asteroid",
                        orbit_class="main-belt",
                        predicted_ra_deg=10.0,
                        predicted_dec_deg=12.0,
                        predicted_x=10.0,
                        predicted_y=12.0,
                        predicted_magnitude=12.2,
                        ra_rate_arcsec_per_hour=0.0,
                        dec_rate_arcsec_per_hour=0.0,
                        motion_rate_arcsec_per_hour=0.0,
                        expected_trail_length_px=0.0,
                        positional_uncertainty_arcsec=0.2,
                        altitude_deg=48.0,
                        likely_visible=True,
                        confidence_score=0.92,
                        status="Likely visible",
                    ),
                ],
                summary_text="1 predicted object",
            )
            estimate_result = SolarSystemVisibilityEstimateResult(
                source_path=frame_paths[0],
                solved_field=known_detection_result.solved_field,
                used_astrometry_fallback=False,
                tested_star_count=5,
                visible_star_count=3,
                dimmest_visible_magnitude=13.0,
                summary_text="Estimated visible limit Gaia G 13.0.",
            )
            partial_results: list[MovingObjectDiscoveryResult] = []

            class _FakeWcs:
                def world_to_pixel_values(self, ra_deg: float, dec_deg: float) -> tuple[float, float]:
                    return ra_deg, dec_deg

            def search_side_effect(batch_paths: list[Path], **_kwargs):
                batch_names = [path.name for path in batch_paths]
                if batch_names == ["frame_00.fit", "frame_01.fit", "frame_02.fit", "frame_03.fit"]:
                    candidates = [
                        MovingObjectCandidate(
                            candidate_id="",
                            frame_detections=(
                                MovingObjectCandidateDetection(batch_paths[0], start_time, 0, 10.0, 12.0, 24.0, 8.5),
                                MovingObjectCandidateDetection(batch_paths[1], start_time + timedelta(minutes=1), 1, 10.0, 12.0, 23.5, 8.0),
                                MovingObjectCandidateDetection(batch_paths[2], start_time + timedelta(minutes=2), 2, 10.0, 12.0, 23.0, 7.8),
                            ),
                            average_snr=8.1,
                            peak_value=24.0,
                            fit_rms_px=0.2,
                            motion_px_per_hour=0.0,
                            motion_arcsec_per_hour=0.0,
                            displacement_px=0.0,
                            start_x=10.0,
                            start_y=12.0,
                            end_x=10.0,
                            end_y=12.0,
                            summary_text="",
                        )
                    ]
                else:
                    candidates = [
                        MovingObjectCandidate(
                            candidate_id="",
                            frame_detections=(
                                MovingObjectCandidateDetection(batch_paths[0], start_time, 0, 20.0, 25.0, 18.0, 6.2),
                                MovingObjectCandidateDetection(batch_paths[1], start_time + timedelta(minutes=3), 1, 21.0, 25.2, 17.5, 6.0),
                                MovingObjectCandidateDetection(batch_paths[2], start_time + timedelta(minutes=4), 2, 22.0, 25.4, 17.0, 5.9),
                            ),
                            average_snr=6.03,
                            peak_value=18.0,
                            fit_rms_px=0.3,
                            motion_px_per_hour=30.0,
                            motion_arcsec_per_hour=45.0,
                            displacement_px=2.04,
                            start_x=20.0,
                            start_y=25.0,
                            end_x=22.0,
                            end_y=25.4,
                            summary_text="",
                            discovery_method="synthetic_sweep",
                        )
                    ]
                batch_indices = [int(path.stem.split("_")[-1]) for path in batch_paths]
                return (
                    batch_paths[0],
                    np.ones((4, 4), dtype=np.float32),
                    [start_time + timedelta(minutes=index) for index in batch_indices],
                    [_FakeWcs() for _ in batch_paths],
                    1.5,
                    candidates,
                )

            with patch("photometry_app.core.discovery._search_moving_candidates", side_effect=search_side_effect):
                result = discover_unmatched_moving_candidates(
                    frame_paths,
                    frame_metadata=frame_metadata,
                    known_detection_result=known_detection_result,
                    estimate_result=estimate_result,
                    reference_path=frame_paths[0],
                    frames_per_batch=4,
                    intermediate_result_callback=partial_results.append,
                )

        self.assertEqual(len(partial_results), 2)
        self.assertEqual(partial_results[0].benchmark_known_count, 1)
        self.assertEqual(partial_results[0].benchmark_recovered_count, 1)
        self.assertEqual(partial_results[1].benchmark_known_count, 1)
        self.assertEqual(partial_results[1].benchmark_recovered_count, 1)
        self.assertEqual(partial_results[1].candidate_count, 1)
        self.assertEqual(partial_results[1].candidates[0].discovery_method, "synthetic_sweep")
        self.assertEqual(result.benchmark_known_count, 1)
        self.assertEqual(result.benchmark_recovered_count, 1)
        self.assertEqual(result.candidate_count, 1)
        self.assertEqual(result.candidates[0].discovery_method, "synthetic_sweep")

    def test_discover_unmatched_moving_candidates_splits_borderline_tracklets_into_review_bucket(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frame_paths = [root / f"frame_{index:02d}.fit" for index in range(3)]
            for path in frame_paths:
                path.write_text("placeholder", encoding="utf-8")

            start_time = datetime(2025, 1, 14, 21, 12, tzinfo=UTC)
            frame_metadata = {
                str(path.resolve()): ObservationMetadata(
                    date_obs=start_time + timedelta(minutes=index),
                    filter_name="L",
                    exposure_seconds=60.0,
                    width=32,
                    height=32,
                    object_name="Demo",
                    frame_type="Light",
                )
                for index, path in enumerate(frame_paths)
            }

            def _candidate(y_value: float, fit_rms_px: float) -> MovingObjectCandidate:
                return MovingObjectCandidate(
                    candidate_id="",
                    frame_detections=(
                        MovingObjectCandidateDetection(frame_paths[0], start_time, 0, 10.0, y_value, 25.0, 8.0),
                        MovingObjectCandidateDetection(frame_paths[1], start_time + timedelta(minutes=1), 1, 11.0, y_value + 0.1, 24.0, 7.5),
                        MovingObjectCandidateDetection(frame_paths[2], start_time + timedelta(minutes=2), 2, 12.0, y_value + 0.2, 23.0, 7.0),
                    ),
                    average_snr=7.5,
                    peak_value=25.0,
                    fit_rms_px=fit_rms_px,
                    motion_px_per_hour=60.0,
                    motion_arcsec_per_hour=90.0,
                    displacement_px=2.0,
                    start_x=10.0,
                    start_y=y_value,
                    end_x=12.0,
                    end_y=y_value + 0.2,
                    summary_text="",
                    max_deflection_px=fit_rms_px * 1.4,
                )

            candidates = [
                _candidate(12.0, 0.45),
                _candidate(18.0, 1.20),
                _candidate(24.0, 2.35),
            ]

            def search_side_effect(*_args, **_kwargs):
                return frame_paths[0], np.ones((4, 4), dtype=np.float32), [], [], 1.5, candidates

            with patch("photometry_app.core.discovery._search_moving_candidates", side_effect=search_side_effect):
                result = discover_unmatched_moving_candidates(
                    frame_paths,
                    frame_metadata=frame_metadata,
                    known_detection_result=None,
                    reference_path=frame_paths[0],
                )

        self.assertEqual(result.candidate_count, 1)
        self.assertEqual(len(result.review_candidates), 1)
        self.assertEqual(result.candidates[0].candidate_id, "C1")
        self.assertEqual(result.review_candidates[0].candidate_id, "R1")
        self.assertIn("borderline review", result.summary_text.lower())

    def test_detect_residual_sources_streak_mode_finds_elongated_component(self) -> None:
        residual_image = np.zeros((48, 48), dtype=np.float32)
        residual_image[20, 12:20] = np.array([35.0, 50.0, 65.0, 72.0, 72.0, 65.0, 50.0, 35.0], dtype=np.float32)
        residual_image[21, 13:19] = np.array([28.0, 40.0, 52.0, 52.0, 40.0, 28.0], dtype=np.float32)

        with (
            patch("photometry_app.core.discovery.sigma_clipped_stats", return_value=(0.0, 0.0, 5.0)),
            patch("photometry_app.core.discovery.DAOStarFinder", return_value=lambda _image: None),
        ):
            result = _detect_residual_sources(
                residual_image,
                source_path=Path("frame.fit"),
                observation_time=datetime(2025, 1, 14, 21, 12, tzinfo=UTC),
                frame_index=0,
                next_detection_id=1,
                excluded_positions=(),
                detector_mode="streak",
                streak_min_area_px=6,
                streak_min_elongation=1.5,
            )

        self.assertEqual(len(result), 1)
        self.assertGreater(result[0].local_snr, 5.0)
        self.assertAlmostEqual(result[0].y, 20.35, places=2)

    def test_discover_unmatched_moving_candidates_can_link_two_frame_tracklet_when_configured(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frame_paths = [root / f"frame_{index:02d}.fit" for index in range(2)]
            for path in frame_paths:
                path.write_text("placeholder", encoding="utf-8")

            class _FakeWcs:
                def pixel_to_world_values(self, x_value: float, y_value: float) -> tuple[float, float]:
                    return 100.0 + (x_value / 100.0), 20.0 + (y_value / 100.0)

            start_time = datetime(2025, 1, 14, 21, 12, tzinfo=UTC)
            frame_metadata = {
                str(path.resolve()): ObservationMetadata(
                    date_obs=start_time + timedelta(minutes=index),
                    filter_name="L",
                    exposure_seconds=60.0,
                    width=32,
                    height=32,
                    object_name="Demo",
                    frame_type="Light",
                )
                for index, path in enumerate(frame_paths)
            }
            image_data = np.zeros((32, 32), dtype=np.float32)

            def residual_side_effect(
                _residual_image: np.ndarray,
                *,
                source_path: Path,
                observation_time: datetime,
                frame_index: int,
                next_detection_id: int,
                excluded_positions,
                residual_min_snr: float = 0.0,
                residual_max_snr: float = 0.0,
                **_kwargs,
            ) -> list[_ResidualDetection]:
                del excluded_positions, residual_min_snr, residual_max_snr
                return [
                    _ResidualDetection(
                        detection_id=next_detection_id,
                        frame_index=frame_index,
                        source_path=source_path,
                        observation_time=observation_time,
                        x=10.0 + (frame_index * 2.0),
                        y=12.0,
                        peak_value=25.0,
                        local_snr=8.0,
                    )
                ]

            with (
                patch("photometry_app.core.discovery.read_header_and_shape", return_value=({}, 32, 32)),
                patch("photometry_app.core.discovery._validated_celestial_wcs", return_value=(_FakeWcs(), [])),
                patch("photometry_app.core.discovery.read_image_data", return_value=image_data),
                patch("photometry_app.core.discovery._reproject_image_data", side_effect=lambda data, *_args, **_kwargs: data),
                patch("photometry_app.core.discovery._known_positions_by_frame", return_value={}),
                patch("photometry_app.core.discovery._reference_pixel_scale_arcsec", return_value=1.5),
                patch("photometry_app.core.discovery._detect_residual_sources", side_effect=residual_side_effect),
            ):
                result = discover_unmatched_moving_candidates(
                    frame_paths,
                    frame_metadata=frame_metadata,
                    known_detection_result=None,
                    reference_path=frame_paths[0],
                    minimum_candidate_frames=2,
                )

        self.assertEqual(result.candidate_count, 1)


if __name__ == "__main__":
    unittest.main()