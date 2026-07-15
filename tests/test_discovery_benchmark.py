from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import numpy as np

from photometry_app.core.discovery import (
    DiscoveryBatchDiagnostics,
    DiscoveryResidualSnapshot,
    DiscoverySearchDiagnostics,
    MovingObjectCandidate,
    MovingObjectCandidateDetection,
    MovingObjectDiscoveryResult,
    RecoveredKnownMovingObject,
)
from photometry_app.core.discovery_benchmark import _build_known_object_diagnostics, group_discovery_scan_results, resolve_group_reference_path, run_discovery_benchmark, select_discovery_group
from photometry_app.core.models import FileScanResult, ObservationMetadata, SolvedField, WcsStatus
from photometry_app.core.settings import AppSettings
from photometry_app.core.solar_system import SolarSystemDetection, SolarSystemDetectionResult, SolarSystemVisibilityEstimateResult


class DiscoveryBenchmarkTest(unittest.TestCase):
    def _build_known_detection_result(self, frame_path: Path, start_time: datetime) -> SolarSystemDetectionResult:
        return SolarSystemDetectionResult(
            source_path=frame_path,
            solved_field=SolvedField(center_ra_deg=10.0, center_dec_deg=20.0, radius_deg=1.0, width=64, height=64, wcs_path=frame_path),
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

    def _build_estimate_result(self, frame_path: Path, solved_field: SolvedField) -> SolarSystemVisibilityEstimateResult:
        return SolarSystemVisibilityEstimateResult(
            source_path=frame_path,
            solved_field=solved_field,
            used_astrometry_fallback=False,
            tested_star_count=6,
            visible_star_count=3,
            dimmest_visible_magnitude=12.5,
            summary_text="Estimated visible limit: Gaia G 12.5.",
        )

    def test_build_known_object_diagnostics_marks_recovered_detection(self) -> None:
        start_time = datetime(2025, 1, 14, 21, 12, tzinfo=UTC)
        frame_path = Path("frame_00.fit")
        known_detection_result = self._build_known_detection_result(frame_path, start_time)
        detection = known_detection_result.detections[0]

        class _FakeWcs:
            def world_to_pixel_values(self, ra_deg: float, dec_deg: float) -> tuple[float, float]:
                return ((ra_deg - 10.0) * 60.0) + 10.0, dec_deg - 8.0

        frame_times = tuple(start_time + timedelta(minutes=index) for index in range(3))
        candidate = MovingObjectCandidate(
            candidate_id="C1",
            frame_detections=tuple(
                MovingObjectCandidateDetection(
                    source_path=Path(f"frame_{index:02d}.fit"),
                    observation_time=frame_times[index],
                    frame_index=index,
                    x=10.0 + index,
                    y=12.0,
                    peak_value=25.0,
                    local_snr=8.0,
                )
                for index in range(3)
            ),
            average_snr=8.0,
            peak_value=25.0,
            fit_rms_px=0.1,
            motion_px_per_hour=60.0,
            motion_arcsec_per_hour=90.0,
            displacement_px=2.0,
            start_x=10.0,
            start_y=12.0,
            end_x=12.0,
            end_y=12.0,
            summary_text="Recovered tracklet",
        )
        search_diagnostics = DiscoverySearchDiagnostics(
            batch_diagnostics=(
                DiscoveryBatchDiagnostics(
                    batch_index=1,
                    frame_indices=(0, 1, 2),
                    frame_times=frame_times,
                    frame_pixel_wcs=(_FakeWcs(), _FakeWcs(), _FakeWcs()),
                    residual_detections_by_frame=(
                        (DiscoveryResidualSnapshot(frame_index=0, observation_time=frame_times[0], x=10.0, y=12.0, local_snr=8.0),),
                        (DiscoveryResidualSnapshot(frame_index=1, observation_time=frame_times[1], x=11.0, y=12.0, local_snr=8.0),),
                        (DiscoveryResidualSnapshot(frame_index=2, observation_time=frame_times[2], x=12.0, y=12.0, local_snr=8.0),),
                    ),
                    linked_candidates=(candidate,),
                    prepare_seconds=1.0,
                    residual_scan_seconds=2.0,
                    link_seconds=3.0,
                    synthetic_sweep_seconds=0.0,
                    residual_detection_count=3,
                    linked_candidate_count=1,
                    synthetic_sweep_candidate_count=0,
                ),
            ),
            aggregated_linked_candidates=(candidate,),
        )
        discovery_result = MovingObjectDiscoveryResult(
            reference_path=frame_path,
            preview_data=np.zeros((4, 4), dtype=np.float32),
            candidate_count=0,
            aligned_frame_count=3,
            candidates=(),
            summary_text="Recovered known object.",
            recovered_known_count=1,
            recovered_known_objects=(
                RecoveredKnownMovingObject(
                    detection=detection,
                    candidate=candidate,
                    matched_frame_count=3,
                    expected_frame_count=3,
                    match_rms_px=0.1,
                    max_match_offset_px=0.1,
                    reference_x=10.0,
                    reference_y=12.0,
                    summary_text="Recovered (1) Ceres.",
                ),
            ),
        )

        diagnostics = _build_known_object_diagnostics(
            search_diagnostics,
            known_detection_result=known_detection_result,
            discovery_result=discovery_result,
            minimum_matched_frames=3,
            estimated_magnitude_limit=12.5,
            frame_count=3,
        )

        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(diagnostics[0].status, "recovered")
        self.assertIsNone(diagnostics[0].failure_stage)
        self.assertEqual(diagnostics[0].linked_candidate_matches, 1)

    def test_run_discovery_benchmark_reports_stage_timings_and_failure_attribution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            folder_path = Path(temp_dir)
            frame_paths = [folder_path / f"frame_{index:02d}.fit" for index in range(3)]
            for path in frame_paths:
                path.write_text("placeholder", encoding="utf-8")
            start_time = datetime(2025, 1, 14, 21, 12, tzinfo=UTC)
            known_detection_result = self._build_known_detection_result(frame_paths[0], start_time)
            estimate_result = self._build_estimate_result(frame_paths[0], known_detection_result.solved_field)
            settings = AppSettings.from_root(folder_path)

            class _FakeWcs:
                def world_to_pixel_values(self, ra_deg: float, dec_deg: float) -> tuple[float, float]:
                    return ((ra_deg - 10.0) * 60.0) + 10.0, dec_deg - 8.0

            def inspect_side_effect(path: Path, _object_folder: str, observation_timezone: str = "UTC") -> FileScanResult:
                del observation_timezone
                frame_index = frame_paths.index(path)
                return FileScanResult(
                    path=path,
                    object_folder="Demo",
                    metadata=ObservationMetadata(
                        date_obs=start_time + timedelta(minutes=frame_index),
                        filter_name="L",
                        exposure_seconds=60.0,
                        width=64,
                        height=64,
                        object_name="Demo",
                        frame_type="Light",
                    ),
                    wcs_status=WcsStatus.SOLVED,
                )

            def discover_side_effect(frame_paths_arg, **kwargs) -> MovingObjectDiscoveryResult:
                del frame_paths_arg
                search_diagnostics = kwargs["search_diagnostics"]
                frame_times = tuple(start_time + timedelta(minutes=index) for index in range(3))
                search_diagnostics.batch_diagnostics = (
                    DiscoveryBatchDiagnostics(
                        batch_index=1,
                        frame_indices=(0, 1, 2),
                        frame_times=frame_times,
                        frame_pixel_wcs=(_FakeWcs(), _FakeWcs(), _FakeWcs()),
                        residual_detections_by_frame=(
                            (DiscoveryResidualSnapshot(frame_index=0, observation_time=frame_times[0], x=10.0, y=12.0, local_snr=8.0),),
                            (DiscoveryResidualSnapshot(frame_index=1, observation_time=frame_times[1], x=11.0, y=12.0, local_snr=8.0),),
                            (DiscoveryResidualSnapshot(frame_index=2, observation_time=frame_times[2], x=12.0, y=12.0, local_snr=8.0),),
                        ),
                        linked_candidates=(),
                        prepare_seconds=1.5,
                        residual_scan_seconds=2.5,
                        link_seconds=3.5,
                        synthetic_sweep_seconds=0.0,
                        residual_detection_count=3,
                        linked_candidate_count=0,
                        synthetic_sweep_candidate_count=0,
                    ),
                )
                search_diagnostics.aggregated_linked_candidates = ()
                return MovingObjectDiscoveryResult(
                    reference_path=frame_paths[0],
                    preview_data=np.zeros((8, 8), dtype=np.float32),
                    candidate_count=0,
                    aligned_frame_count=3,
                    candidates=(),
                    summary_text="No blind candidates retained.",
                )

            with (
                patch("photometry_app.core.discovery_benchmark.inspect_fits_file", side_effect=inspect_side_effect),
                patch("photometry_app.core.discovery_benchmark.detect_known_solar_system_objects", return_value=known_detection_result),
                patch("photometry_app.core.discovery_benchmark.estimate_visible_magnitude_limit", return_value=estimate_result),
                patch("photometry_app.core.discovery_benchmark.discover_unmatched_moving_candidates", side_effect=discover_side_effect),
            ):
                report = run_discovery_benchmark(folder_path, settings=settings)

        self.assertEqual({item.label for item in report.stage_timings}, {"prepare", "residual_scan", "link", "synthetic_sweep"})
        self.assertAlmostEqual(next(item.seconds for item in report.stage_timings if item.label == "prepare"), 1.5)
        self.assertEqual(len(report.known_object_diagnostics), 1)
        self.assertEqual(report.known_object_diagnostics[0].status, "missed")
        self.assertEqual(report.known_object_diagnostics[0].failure_stage, "residuals_not_linked")
        self.assertEqual(report.known_object_diagnostics[0].residual_frame_matches, 3)


class DiscoveryBenchmarkHelpersTest(unittest.TestCase):

    def test_group_discovery_scan_results_orders_frames_by_timestamp(self) -> None:
        start_time = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        late_path = Path("frame_03.fits")
        early_path = Path("frame_01.fits")
        middle_path = Path("frame_02.fits")
        groups = group_discovery_scan_results(
            [
                self._scan_result(late_path, start_time + timedelta(minutes=2), filter_name="L", exposure_seconds=120.0),
                self._scan_result(early_path, start_time, filter_name="L", exposure_seconds=120.0),
                self._scan_result(middle_path, start_time + timedelta(minutes=1), filter_name="L", exposure_seconds=120.0),
            ]
        )

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].label, "Filter L | 120.0 s")
        self.assertEqual([path.name for path in groups[0].frame_paths], ["frame_01.fits", "frame_02.fits", "frame_03.fits"])

    def test_select_discovery_group_requires_selection_when_multiple_groups_exist(self) -> None:
        groups = group_discovery_scan_results(
            [
                self._scan_result(Path("l_01.fits"), datetime(2026, 1, 1, 0, 0, tzinfo=UTC), filter_name="L", exposure_seconds=120.0),
                self._scan_result(Path("r_01.fits"), datetime(2026, 1, 1, 0, 0, tzinfo=UTC), filter_name="R", exposure_seconds=60.0),
            ]
        )

        with self.assertRaisesRegex(ValueError, "Multiple frame groups"):
            select_discovery_group(groups)

    def test_select_discovery_group_and_reference_path_use_requested_values(self) -> None:
        groups = group_discovery_scan_results(
            [
                self._scan_result(Path("l_01.fits"), datetime(2026, 1, 1, 0, 0, tzinfo=UTC), filter_name="L", exposure_seconds=120.0),
                self._scan_result(Path("l_02.fits"), datetime(2026, 1, 1, 0, 1, tzinfo=UTC), filter_name="L", exposure_seconds=120.0),
                self._scan_result(Path("r_01.fits"), datetime(2026, 1, 1, 0, 0, tzinfo=UTC), filter_name="R", exposure_seconds=60.0),
            ]
        )

        selected_group = select_discovery_group(groups, filter_name="L", exposure_seconds=120.0)

        self.assertEqual(selected_group.label, "Filter L | 120.0 s")
        self.assertEqual(resolve_group_reference_path(selected_group, "l_02.fits").name, "l_02.fits")

    def _scan_result(
        self,
        path: Path,
        date_obs: datetime,
        *,
        filter_name: str,
        exposure_seconds: float,
    ) -> FileScanResult:
        return FileScanResult(
            path=path,
            object_folder="Demo",
            metadata=ObservationMetadata(
                date_obs=date_obs,
                filter_name=filter_name,
                exposure_seconds=exposure_seconds,
                width=100,
                height=100,
                object_name="Demo",
                frame_type="Light",
            ),
            wcs_status=WcsStatus.SOLVED,
        )