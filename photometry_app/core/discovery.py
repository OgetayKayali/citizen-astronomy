from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
import math
import os
import shutil
import tempfile
import time

from astropy.stats import mad_std, sigma_clip, sigma_clipped_stats
from astropy.io import fits
from astropy.wcs.utils import proj_plane_pixel_scales
import numpy as np
from photutils.detection import DAOStarFinder

from photometry_app.core.alignment import _reproject_image_data, _validated_celestial_wcs
from photometry_app.core.image_io import read_header_and_shape, read_image_data
from photometry_app.core.models import ObservationMetadata
from photometry_app.core.solar_system import SolarSystemDetection, SolarSystemDetectionResult, SolarSystemVisibilityEstimateResult, predict_detection_coordinates
from photometry_app.core.synthetic_tracking import measure_synthetic_tracking_peak


_DISCOVERY_MIN_FRAMES = 3
_DISCOVERY_DETECTION_SIGMA = 5.0
_DISCOVERY_DETECTION_FWHM = 3.0
_DISCOVERY_MAX_RESIDUALS_PER_FRAME = 24
_DISCOVERY_MIN_DISPLACEMENT_PX = 1.5
_DISCOVERY_MATCH_RADIUS_PX = 2.8
_DISCOVERY_MAX_SEED_FRAME_GAP = 2
_DISCOVERY_KNOWN_EXCLUSION_RADIUS_PX = 6.0
_RECOVERY_ASSOCIATION_RADIUS_PX = 4.5
_DISCOVERY_EDGE_MARGIN_PX = 6
_DISCOVERY_MAX_CANDIDATES = 25
_DISCOVERY_BINNING_FACTORS = {1, 2, 3, 4}
_DISCOVERY_DETECTOR_MODE_POINT = "point"
_DISCOVERY_DETECTOR_MODE_HYBRID = "hybrid"
_DISCOVERY_DETECTOR_MODE_STREAK = "streak"
_DISCOVERY_DETECTOR_MODES = {
    _DISCOVERY_DETECTOR_MODE_POINT,
    _DISCOVERY_DETECTOR_MODE_HYBRID,
    _DISCOVERY_DETECTOR_MODE_STREAK,
}
_DISCOVERY_STREAK_MIN_AREA_PX = 6
_DISCOVERY_STREAK_MIN_ELONGATION = 1.8
_DISCOVERY_POTENTIAL_MAX_DEFLECTION_RMS_PX = 0.9
_DISCOVERY_REVIEW_MAX_DEFLECTION_RMS_PX = 1.8
_DISCOVERY_SYNTHETIC_SWEEP_MAX_MOTION_PX_PER_HOUR = 12.0
_DISCOVERY_SYNTHETIC_SWEEP_MOTION_STEP_PX_PER_HOUR = 1.0
_DISCOVERY_SYNTHETIC_SWEEP_ANGLE_STEP_DEG = 30.0
_DISCOVERY_SYNTHETIC_SWEEP_MIN_STACKED_SNR = 6.0
_DISCOVERY_SYNTHETIC_SWEEP_MAX_DETECTIONS_PER_VECTOR = 4
_DISCOVERY_SYNTHETIC_SWEEP_DIRECTION_FOCUS_ALL = "all_directions"
_DISCOVERY_SYNTHETIC_SWEEP_DIRECTION_FOCUS_MAIN_BELT = "main_belt"
_DISCOVERY_SYNTHETIC_SWEEP_MAIN_BELT_FOCUS_HALF_WIDTH_DEG = 45.0
_DISCOVERY_MOTION_PRIOR_BIAS_BALANCED = "balanced"
_DISCOVERY_MOTION_PRIOR_BIAS_MAIN_BELT = "main_belt"
_DISCOVERY_MOTION_PRIOR_BIAS_NEAR_EARTH = "near_earth"
_DISCOVERY_MOTION_PRIOR_BIAS_MODES = {
    _DISCOVERY_MOTION_PRIOR_BIAS_BALANCED,
    _DISCOVERY_MOTION_PRIOR_BIAS_MAIN_BELT,
    _DISCOVERY_MOTION_PRIOR_BIAS_NEAR_EARTH,
}
_DISCOVERY_MAX_TRAIL_PIXELS_PER_EXPOSURE = 4.0
_DISCOVERY_TEMP_CACHE_MAX_AGE_SECONDS = 48 * 3600
_DISCOVERY_TEMP_CACHE_ROOT_NAME = "discover-working-cache"
_DISCOVERY_CANDIDATE_METHOD_RESIDUAL = "residual"
_DISCOVERY_CANDIDATE_METHOD_SYNTHETIC_SWEEP = "synthetic_sweep"
_DISCOVERY_CANDIDATE_METHOD_LABELS = {
    _DISCOVERY_CANDIDATE_METHOD_RESIDUAL: "Residual",
    _DISCOVERY_CANDIDATE_METHOD_SYNTHETIC_SWEEP: "Sweep",
}


class DiscoveryCancelledError(RuntimeError):
    pass


@dataclass(slots=True)
class MovingObjectCandidateDetection:
    source_path: Path
    observation_time: datetime
    frame_index: int
    x: float
    y: float
    peak_value: float
    local_snr: float
    ra_deg: float | None = None
    dec_deg: float | None = None


@dataclass(slots=True)
class MovingObjectCandidate:
    candidate_id: str
    frame_detections: tuple[MovingObjectCandidateDetection, ...]
    average_snr: float
    peak_value: float
    fit_rms_px: float
    motion_px_per_hour: float
    motion_arcsec_per_hour: float | None
    displacement_px: float
    start_x: float
    start_y: float
    end_x: float
    end_y: float
    summary_text: str
    max_deflection_px: float = 0.0
    discovery_method: str = _DISCOVERY_CANDIDATE_METHOD_RESIDUAL


@dataclass(slots=True)
class MovingObjectDiscoveryResult:
    reference_path: Path
    preview_data: np.ndarray
    candidate_count: int
    aligned_frame_count: int
    candidates: tuple[MovingObjectCandidate, ...]
    summary_text: str
    report_text: str = ""
    review_candidates: tuple[MovingObjectCandidate, ...] = ()
    estimate_result: SolarSystemVisibilityEstimateResult | None = None
    benchmark_known_count: int = 0
    benchmark_recovered_count: int = 0
    recovered_known_count: int = 0
    known_detection_count: int = 0
    recovered_known_objects: tuple[RecoveredKnownMovingObject, ...] = ()
    missed_known_objects: tuple[MissedKnownMovingObject, ...] = ()
    known_predicted_magnitudes: tuple[float, ...] = ()
    methods_summary_text: str = ""

    def all_candidates(self) -> tuple[MovingObjectCandidate, ...]:
        return (*self.candidates, *self.review_candidates)


def candidate_discovery_method_label(candidate: MovingObjectCandidate) -> str:
    return _DISCOVERY_CANDIDATE_METHOD_LABELS.get(candidate.discovery_method, "Discovery")


@dataclass(slots=True)
class RecoveredKnownMovingObject:
    detection: SolarSystemDetection
    candidate: MovingObjectCandidate
    matched_frame_count: int
    expected_frame_count: int
    match_rms_px: float
    max_match_offset_px: float
    reference_x: float
    reference_y: float
    summary_text: str
    within_estimated_limit: bool = False


@dataclass(slots=True)
class MissedKnownMovingObject:
    detection: SolarSystemDetection
    expected_frame_count: int
    reference_x: float
    reference_y: float
    summary_text: str
    within_estimated_limit: bool = False


@dataclass(slots=True)
class MovingObjectRecoveryResult:
    reference_path: Path
    preview_data: np.ndarray
    aligned_frame_count: int
    benchmark_known_count: int
    recovered_known_count: int
    unmatched_candidate_count: int
    recovered_known_objects: tuple[RecoveredKnownMovingObject, ...]
    missed_known_objects: tuple[MissedKnownMovingObject, ...]
    unmatched_candidates: tuple[MovingObjectCandidate, ...]
    summary_text: str


@dataclass(slots=True)
class DiscoveryResidualSnapshot:
    frame_index: int
    observation_time: datetime
    x: float
    y: float
    local_snr: float
    ra_deg: float | None = None
    dec_deg: float | None = None


@dataclass(slots=True)
class DiscoveryBatchDiagnostics:
    batch_index: int
    frame_indices: tuple[int, ...]
    frame_times: tuple[datetime, ...]
    frame_pixel_wcs: tuple[object, ...]
    residual_detections_by_frame: tuple[tuple[DiscoveryResidualSnapshot, ...], ...]
    linked_candidates: tuple[MovingObjectCandidate, ...]
    prepare_seconds: float
    residual_scan_seconds: float
    link_seconds: float
    synthetic_sweep_seconds: float
    residual_detection_count: int
    linked_candidate_count: int
    synthetic_sweep_candidate_count: int


@dataclass(slots=True)
class DiscoverySearchDiagnostics:
    batch_diagnostics: tuple[DiscoveryBatchDiagnostics, ...] = ()
    aggregated_linked_candidates: tuple[MovingObjectCandidate, ...] = ()


@dataclass(slots=True)
class _ResidualDetection:
    detection_id: int
    frame_index: int
    source_path: Path
    observation_time: datetime
    x: float
    y: float
    peak_value: float
    local_snr: float
    ra_deg: float | None = None
    dec_deg: float | None = None


@dataclass(slots=True)
class _PreparedSearchFrame:
    frame_index: int
    frame_path: Path
    observation_time: datetime
    aligned_frame: np.ndarray
    frame_wcs: object
    cached_path: Path | None = None


@dataclass(slots=True)
class _PreparedResidualSearchContext:
    resolved_reference_path: Path
    reference_header: fits.Header
    frame_times: list[datetime]
    aligned_frames: list[np.ndarray]
    frame_pixel_wcs: list[object]
    preview_data: np.ndarray
    background_model: np.ndarray
    pixel_scale_arcsec_per_pixel: float | None
    known_positions: dict[int, tuple[tuple[float, float], ...]]
    motion_range_estimate: _DiscoveryMotionRangeEstimate | None
    effective_synthetic_sweep_settings: _SyntheticSweepSettings | None
    prepare_seconds: float


@dataclass(slots=True)
class _SyntheticSweepSettings:
    max_motion_px_per_hour: float
    motion_step_px_per_hour: float
    angle_step_deg: float
    min_stacked_snr: float
    combine_mode: str
    direction_focus_mode: str = _DISCOVERY_SYNTHETIC_SWEEP_DIRECTION_FOCUS_ALL
    direction_focus_half_width_deg: float = _DISCOVERY_SYNTHETIC_SWEEP_MAIN_BELT_FOCUS_HALF_WIDTH_DEG
    save_stacks: bool = False
    output_directory: Path | None = None


@dataclass(slots=True)
class _DiscoveryMotionRangeEstimate:
    min_motion_px_per_hour: float
    max_motion_px_per_hour: float
    pixel_scale_arcsec_per_pixel: float
    median_exposure_seconds: float


class _BinnedFrameWcs:
    def __init__(self, base_wcs: object, binning_factor: int) -> None:
        self._base_wcs = base_wcs
        self._binning_factor = max(1, int(binning_factor))

    def pixel_to_world_values(self, x_value: float, y_value: float) -> tuple[float, float]:
        return self._base_wcs.pixel_to_world_values(
            _binned_to_source_pixel_coordinate(x_value, self._binning_factor),
            _binned_to_source_pixel_coordinate(y_value, self._binning_factor),
        )

    def world_to_pixel_values(self, ra_deg: float, dec_deg: float) -> tuple[float, float]:
        source_x, source_y = self._base_wcs.world_to_pixel_values(ra_deg, dec_deg)
        return (
            _source_to_binned_pixel_coordinate(source_x, self._binning_factor),
            _source_to_binned_pixel_coordinate(source_y, self._binning_factor),
        )


def discover_temp_cache_root(root_path: Path | None = None) -> Path:
    if root_path is not None:
        return Path(root_path)
    return Path(tempfile.gettempdir()) / "CitizenAstronomy" / _DISCOVERY_TEMP_CACHE_ROOT_NAME


def cleanup_stale_discovery_temp_cache(root_path: Path | None = None, *, max_age_seconds: int = _DISCOVERY_TEMP_CACHE_MAX_AGE_SECONDS) -> None:
    cache_root = discover_temp_cache_root(root_path)
    if not cache_root.exists():
        return
    cutoff_timestamp = time.time() - max(0, int(max_age_seconds))
    for child in cache_root.iterdir():
        if not child.is_dir():
            continue
        try:
            if child.stat().st_mtime >= cutoff_timestamp:
                continue
        except OSError:
            continue
        shutil.rmtree(child, ignore_errors=True)


def _create_discovery_temp_cache_dir(root_path: Path | None = None) -> Path:
    cache_root = discover_temp_cache_root(root_path)
    cache_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S")
    candidate = cache_root / f"discover-{timestamp}-pid{os.getpid()}"
    suffix = 1
    while candidate.exists():
        suffix += 1
        candidate = cache_root / f"discover-{timestamp}-pid{os.getpid()}-{suffix:02d}"
    candidate.mkdir(parents=True, exist_ok=False)
    return candidate


def _cleanup_discovery_temp_cache_dir(cache_dir: Path | None) -> None:
    if cache_dir is None:
        return
    shutil.rmtree(cache_dir, ignore_errors=True)


def discover_unmatched_moving_candidates(
    frame_paths: list[Path],
    *,
    frame_metadata: dict[str, ObservationMetadata],
    known_detection_result: SolarSystemDetectionResult | None,
    reference_path: Path | None = None,
    assume_aligned: bool = False,
    max_parallel_workers: int = 0,
    residual_min_snr: float = 0.0,
    residual_max_snr: float = 0.0,
    frames_per_batch: int = 0,
    single_batch_only: bool = False,
    binning_factor: int = 1,
    use_temporary_cache: bool = False,
    minimum_candidate_frames: int = _DISCOVERY_MIN_FRAMES,
    detection_sigma: float = _DISCOVERY_DETECTION_SIGMA,
    detection_fwhm: float = _DISCOVERY_DETECTION_FWHM,
    max_residuals_per_frame: int = _DISCOVERY_MAX_RESIDUALS_PER_FRAME,
    edge_margin_px: int = _DISCOVERY_EDGE_MARGIN_PX,
    min_seed_displacement_px: float = _DISCOVERY_MIN_DISPLACEMENT_PX,
    detector_mode: str = _DISCOVERY_DETECTOR_MODE_HYBRID,
    streak_min_area_px: int = _DISCOVERY_STREAK_MIN_AREA_PX,
    streak_min_elongation: float = _DISCOVERY_STREAK_MIN_ELONGATION,
    potential_candidate_max_deflection_rms_px: float = _DISCOVERY_POTENTIAL_MAX_DEFLECTION_RMS_PX,
    review_candidate_max_deflection_rms_px: float = _DISCOVERY_REVIEW_MAX_DEFLECTION_RMS_PX,
    enable_synthetic_sweep: bool = False,
    synthetic_sweep_max_motion_px_per_hour: float = _DISCOVERY_SYNTHETIC_SWEEP_MAX_MOTION_PX_PER_HOUR,
    synthetic_sweep_motion_step_px_per_hour: float = _DISCOVERY_SYNTHETIC_SWEEP_MOTION_STEP_PX_PER_HOUR,
    synthetic_sweep_angle_step_deg: float = _DISCOVERY_SYNTHETIC_SWEEP_ANGLE_STEP_DEG,
    synthetic_sweep_direction_focus: str = _DISCOVERY_SYNTHETIC_SWEEP_DIRECTION_FOCUS_ALL,
    synthetic_sweep_direction_focus_half_width_deg: float = _DISCOVERY_SYNTHETIC_SWEEP_MAIN_BELT_FOCUS_HALF_WIDTH_DEG,
    synthetic_sweep_min_stacked_snr: float = _DISCOVERY_SYNTHETIC_SWEEP_MIN_STACKED_SNR,
    synthetic_sweep_save_stacks: bool = False,
    motion_prior_bias: str = _DISCOVERY_MOTION_PRIOR_BIAS_BALANCED,
    synthetic_tracking_combine_mode: str = "mean",
    estimate_result: SolarSystemVisibilityEstimateResult | None = None,
    progress_callback: Callable[[str], None] | None = None,
    cancel_callback: Callable[[], bool] | None = None,
    intermediate_result_callback: Callable[[MovingObjectDiscoveryResult], None] | None = None,
    search_diagnostics: DiscoverySearchDiagnostics | None = None,
) -> MovingObjectDiscoveryResult:
    normalized_min_snr, normalized_max_snr = _normalize_residual_snr_bounds(residual_min_snr, residual_max_snr)
    normalized_binning_factor = _normalize_discovery_binning_factor(binning_factor)
    normalized_minimum_candidate_frames = _normalize_discovery_min_candidate_frames(minimum_candidate_frames)
    normalized_detection_sigma = _normalize_discovery_detection_sigma(detection_sigma)
    normalized_detection_fwhm = _normalize_discovery_detection_fwhm(detection_fwhm)
    normalized_max_residuals_per_frame = _normalize_discovery_max_residuals_per_frame(max_residuals_per_frame)
    normalized_edge_margin_px = _normalize_discovery_edge_margin_px(edge_margin_px)
    normalized_min_seed_displacement_px = _normalize_discovery_min_seed_displacement_px(min_seed_displacement_px)
    normalized_detector_mode = _normalize_discovery_detector_mode(detector_mode)
    normalized_streak_min_area_px = _normalize_discovery_streak_min_area_px(streak_min_area_px)
    normalized_streak_min_elongation = _normalize_discovery_streak_min_elongation(streak_min_elongation)
    normalized_potential_candidate_max_deflection_rms_px, normalized_review_candidate_max_deflection_rms_px = _normalize_discovery_candidate_deflection_thresholds(
        potential_candidate_max_deflection_rms_px,
        review_candidate_max_deflection_rms_px,
    )
    resolved_reference_path = frame_paths[0] if reference_path is None else reference_path
    synthetic_sweep_settings = _normalize_discovery_synthetic_sweep_settings(
        enable_synthetic_sweep=enable_synthetic_sweep,
        max_motion_px_per_hour=synthetic_sweep_max_motion_px_per_hour,
        motion_step_px_per_hour=synthetic_sweep_motion_step_px_per_hour,
        angle_step_deg=synthetic_sweep_angle_step_deg,
        direction_focus_mode=synthetic_sweep_direction_focus,
        direction_focus_half_width_deg=synthetic_sweep_direction_focus_half_width_deg,
        min_stacked_snr=synthetic_sweep_min_stacked_snr,
        save_stacks=synthetic_sweep_save_stacks,
        output_directory=resolved_reference_path.parent / "synthetic_track",
        combine_mode=synthetic_tracking_combine_mode,
    )
    frame_batches = _discovery_frame_batches(
        frame_paths,
        resolved_reference_path,
        frames_per_batch,
        single_batch_only=single_batch_only,
        minimum_candidate_frames=normalized_minimum_candidate_frames,
    )
    preview_accumulator: np.ndarray | None = None
    preview_weight = 0
    aggregated_candidates: list[MovingObjectCandidate] = []
    batch_diagnostics: list[DiscoveryBatchDiagnostics] | None = [] if search_diagnostics is not None else None
    global_index_lookup = {str(path.resolve()): index for index, path in enumerate(frame_paths)}
    frame_times_by_index: list[datetime | None] = [None] * len(frame_paths)
    frame_wcs_by_index: list[object | None] = [None] * len(frame_paths)
    working_cache_dir = _create_discovery_temp_cache_dir() if use_temporary_cache else None
    if len(frame_batches) > 1:
        _emit_progress(progress_callback, f"Running discovery in {len(frame_batches)} batched search window(s).")
        if synthetic_sweep_settings is not None:
            _emit_progress(progress_callback, "Final synthetic sweep is enabled for each batched search window, so every batch will run its own configured velocity-grid pass before aggregation.")
    elif single_batch_only and len(frame_batches) == 1 and len(frame_batches[0]) < len(frame_paths):
        _emit_progress(progress_callback, f"Running discovery on a single sampled batch of {len(frame_batches[0])} frame(s) from the current group.")
    if normalized_binning_factor > 1:
        _emit_progress(progress_callback, f"Applying {normalized_binning_factor}x{normalized_binning_factor} working-image binning for Discover.")
    if working_cache_dir is not None:
        _emit_progress(progress_callback, "Creating a temporary Discover working cache that will be removed after the run.")
    try:
        for batch_index, batch_paths in enumerate(frame_batches, start=1):
            _raise_if_cancelled(cancel_callback, "Discover cancelled.")
            batch_cache_dir = None if working_cache_dir is None else working_cache_dir / f"batch_{batch_index:02d}"
            batch_frame_indices = [global_index_lookup[str(path.resolve())] for path in batch_paths]
            batch_sweep_settings = _synthetic_sweep_settings_for_batch(
                synthetic_sweep_settings,
                batch_index=batch_index,
                batch_count=len(frame_batches),
            )
            batch_reference_path, batch_preview_data, batch_frame_times, batch_frame_pixel_wcs, _pixel_scale_arcsec_per_pixel, batch_candidates = _search_moving_candidates(
                batch_paths,
                frame_metadata=frame_metadata,
                known_detection_result=known_detection_result,
                reference_path=resolved_reference_path,
                assume_aligned=assume_aligned,
                max_parallel_workers=max_parallel_workers,
                exclude_known_positions=False,
                residual_min_snr=normalized_min_snr,
                residual_max_snr=normalized_max_snr,
                binning_factor=normalized_binning_factor,
                minimum_candidate_frames=normalized_minimum_candidate_frames,
                detection_sigma=normalized_detection_sigma,
                detection_fwhm=normalized_detection_fwhm,
                max_residuals_per_frame=normalized_max_residuals_per_frame,
                edge_margin_px=normalized_edge_margin_px,
                min_seed_displacement_px=normalized_min_seed_displacement_px,
                detector_mode=normalized_detector_mode,
                streak_min_area_px=normalized_streak_min_area_px,
                streak_min_elongation=normalized_streak_min_elongation,
                motion_prior_bias=motion_prior_bias,
                synthetic_sweep_settings=batch_sweep_settings,
                working_cache_dir=batch_cache_dir,
                progress_callback=(
                    None
                    if progress_callback is None
                    else lambda message, batch_index=batch_index, batch_total=len(frame_batches): progress_callback(f"[Batch {batch_index}/{batch_total}] {message}")
                ),
                cancel_callback=cancel_callback,
                cancellation_message="Discover cancelled.",
                mode_label="discovery",
                diagnostics=batch_diagnostics,
                diagnostics_batch_index=batch_index,
                global_frame_indices=tuple(batch_frame_indices),
            )
            if preview_accumulator is None:
                preview_accumulator = np.zeros_like(batch_preview_data, dtype=np.float64)
            preview_accumulator += np.asarray(batch_preview_data, dtype=np.float64) * float(len(batch_paths))
            preview_weight += len(batch_paths)
            for batch_frame_index, global_frame_index in enumerate(batch_frame_indices):
                if batch_frame_index < len(batch_frame_times) and frame_times_by_index[global_frame_index] is None:
                    frame_times_by_index[global_frame_index] = batch_frame_times[batch_frame_index]
                if batch_frame_index < len(batch_frame_pixel_wcs) and frame_wcs_by_index[global_frame_index] is None:
                    frame_wcs_by_index[global_frame_index] = batch_frame_pixel_wcs[batch_frame_index]
            aggregated_candidates.extend(_remap_candidate_frame_indices(batch_candidates, batch_frame_indices))
            if intermediate_result_callback is not None:
                partial_result = _build_discovery_result_snapshot(
                    reference_path=resolved_reference_path,
                    preview_data=(
                        np.asarray(preview_accumulator / max(1, preview_weight), dtype=np.float32)
                        if preview_accumulator is not None
                        else np.zeros((1, 1), dtype=np.float32)
                    ),
                    frame_count=len(frame_paths),
                    aggregated_candidates=aggregated_candidates,
                    frame_times_by_index=frame_times_by_index,
                    frame_wcs_by_index=frame_wcs_by_index,
                    known_detection_result=known_detection_result,
                    estimate_result=estimate_result,
                    detector_mode=normalized_detector_mode,
                    potential_candidate_max_deflection_rms_px=normalized_potential_candidate_max_deflection_rms_px,
                    review_candidate_max_deflection_rms_px=normalized_review_candidate_max_deflection_rms_px,
                    minimum_candidate_frames=normalized_minimum_candidate_frames,
                    synthetic_sweep_summary_text=_discovery_synthetic_sweep_summary(
                        synthetic_sweep_settings,
                        ran=synthetic_sweep_settings is not None,
                    ),
                    allow_known_object_benchmarking=True,
                )
                if batch_index < len(frame_batches):
                    stage_label = "synthetic-sweep" if synthetic_sweep_settings is not None else "residual"
                    partial_result = replace(
                        partial_result,
                        summary_text=(
                            f"Discover processed {batch_index} of {len(frame_batches)} {stage_label} batch(es); "
                            f"currently showing {len(partial_result.candidates)} potential discovery candidate(s) and "
                            f"{len(partial_result.review_candidates)} Borderline Review tracklet(s)."
                        ),
                        report_text=(
                            f"Discover has completed {batch_index} of {len(frame_batches)} {stage_label} batch(es). "
                            "The results window will continue updating as additional batches finish."
                        ),
                    )
                intermediate_result_callback(partial_result)
    finally:
        _cleanup_discovery_temp_cache_dir(working_cache_dir)
    preview_data = (
        np.asarray(preview_accumulator / max(1, preview_weight), dtype=np.float32)
        if preview_accumulator is not None
        else np.zeros((1, 1), dtype=np.float32)
    )
    linked_candidates = _rank_and_number_candidates(aggregated_candidates)
    if search_diagnostics is not None and batch_diagnostics is not None:
        search_diagnostics.batch_diagnostics = tuple(batch_diagnostics)
        search_diagnostics.aggregated_linked_candidates = tuple(linked_candidates)
    return _build_discovery_result_snapshot(
        reference_path=resolved_reference_path,
        preview_data=preview_data,
        frame_count=len(frame_paths),
        aggregated_candidates=linked_candidates,
        frame_times_by_index=frame_times_by_index,
        frame_wcs_by_index=frame_wcs_by_index,
        known_detection_result=known_detection_result,
        estimate_result=estimate_result,
        detector_mode=normalized_detector_mode,
        potential_candidate_max_deflection_rms_px=normalized_potential_candidate_max_deflection_rms_px,
        review_candidate_max_deflection_rms_px=normalized_review_candidate_max_deflection_rms_px,
        minimum_candidate_frames=normalized_minimum_candidate_frames,
        synthetic_sweep_summary_text=_discovery_synthetic_sweep_summary(
            synthetic_sweep_settings,
            ran=synthetic_sweep_settings is not None,
        ),
        allow_known_object_benchmarking=True,
    )


def export_discovery_residual_debug_outputs(
    frame_paths: list[Path],
    *,
    frame_metadata: dict[str, ObservationMetadata],
    known_detection_result: SolarSystemDetectionResult | None,
    output_directory: Path,
    reference_path: Path | None = None,
    assume_aligned: bool = False,
    max_parallel_workers: int = 0,
    residual_min_snr: float = 0.0,
    residual_max_snr: float = 0.0,
    frames_per_batch: int = 0,
    single_batch_only: bool = False,
    binning_factor: int = 1,
    use_temporary_cache: bool = False,
    minimum_candidate_frames: int = _DISCOVERY_MIN_FRAMES,
    detection_sigma: float = _DISCOVERY_DETECTION_SIGMA,
    detection_fwhm: float = _DISCOVERY_DETECTION_FWHM,
    max_residuals_per_frame: int = _DISCOVERY_MAX_RESIDUALS_PER_FRAME,
    edge_margin_px: int = _DISCOVERY_EDGE_MARGIN_PX,
    min_seed_displacement_px: float = _DISCOVERY_MIN_DISPLACEMENT_PX,
    detector_mode: str = _DISCOVERY_DETECTOR_MODE_HYBRID,
    streak_min_area_px: int = _DISCOVERY_STREAK_MIN_AREA_PX,
    streak_min_elongation: float = _DISCOVERY_STREAK_MIN_ELONGATION,
    motion_prior_bias: str = _DISCOVERY_MOTION_PRIOR_BIAS_BALANCED,
    progress_callback: Callable[[str], None] | None = None,
    cancel_callback: Callable[[], bool] | None = None,
) -> Path:
    normalized_min_snr, normalized_max_snr = _normalize_residual_snr_bounds(residual_min_snr, residual_max_snr)
    normalized_binning_factor = _normalize_discovery_binning_factor(binning_factor)
    normalized_minimum_candidate_frames = _normalize_discovery_min_candidate_frames(minimum_candidate_frames)
    normalized_detection_sigma = _normalize_discovery_detection_sigma(detection_sigma)
    normalized_detection_fwhm = _normalize_discovery_detection_fwhm(detection_fwhm)
    normalized_max_residuals_per_frame = _normalize_discovery_max_residuals_per_frame(max_residuals_per_frame)
    normalized_edge_margin_px = _normalize_discovery_edge_margin_px(edge_margin_px)
    normalized_detector_mode = _normalize_discovery_detector_mode(detector_mode)
    normalized_streak_min_area_px = _normalize_discovery_streak_min_area_px(streak_min_area_px)
    normalized_streak_min_elongation = _normalize_discovery_streak_min_elongation(streak_min_elongation)
    resolved_reference_path = frame_paths[0] if reference_path is None else reference_path
    frame_batches = _discovery_frame_batches(
        frame_paths,
        resolved_reference_path,
        frames_per_batch,
        single_batch_only=single_batch_only,
        minimum_candidate_frames=normalized_minimum_candidate_frames,
    )
    working_cache_dir = _create_discovery_temp_cache_dir() if use_temporary_cache else None
    output_directory.mkdir(parents=True, exist_ok=True)
    if len(frame_batches) > 1:
        _emit_progress(progress_callback, f"Running residual debug export in {len(frame_batches)} batched search window(s).")
    elif single_batch_only and len(frame_batches) == 1 and len(frame_batches[0]) < len(frame_paths):
        _emit_progress(progress_callback, f"Running residual debug export on a single sampled batch of {len(frame_batches[0])} frame(s).")
    try:
        for batch_index, batch_paths in enumerate(frame_batches, start=1):
            _raise_if_cancelled(cancel_callback, "Discover residual debug export cancelled.")
            batch_cache_dir = None if working_cache_dir is None else working_cache_dir / f"batch_{batch_index:02d}"
            batch_output_dir = output_directory if len(frame_batches) == 1 else output_directory / f"batch_{batch_index:02d}"
            context = _prepare_residual_search_context(
                batch_paths,
                frame_metadata=frame_metadata,
                known_detection_result=known_detection_result,
                reference_path=resolved_reference_path,
                assume_aligned=assume_aligned,
                max_parallel_workers=max_parallel_workers,
                minimum_candidate_frames=normalized_minimum_candidate_frames,
                binning_factor=normalized_binning_factor,
                motion_prior_bias=motion_prior_bias,
                synthetic_sweep_settings=None,
                working_cache_dir=batch_cache_dir,
                exclude_known_positions=False,
                progress_callback=(
                    None
                    if progress_callback is None
                    else lambda message, batch_index=batch_index, batch_total=len(frame_batches): progress_callback(f"[Batch {batch_index}/{batch_total}] {message}")
                ),
                cancel_callback=cancel_callback,
                cancellation_message="Discover residual debug export cancelled.",
                mode_label="discovery",
            )
            residual_detections_by_frame = _scan_frames_for_residual_detections(
                batch_paths,
                frame_times=context.frame_times,
                frame_pixel_wcs=context.frame_pixel_wcs,
                aligned_frames=context.aligned_frames,
                background_model=context.background_model,
                known_positions=context.known_positions,
                exclude_known_positions=False,
                residual_min_snr=normalized_min_snr,
                residual_max_snr=normalized_max_snr,
                detection_sigma=normalized_detection_sigma,
                detection_fwhm=normalized_detection_fwhm,
                max_residuals_per_frame=normalized_max_residuals_per_frame,
                edge_margin_px=normalized_edge_margin_px,
                detector_mode=normalized_detector_mode,
                streak_min_area_px=normalized_streak_min_area_px,
                streak_min_elongation=normalized_streak_min_elongation,
                worker_count=_resolved_search_workers(len(batch_paths), max_parallel_workers),
                progress_callback=(
                    None
                    if progress_callback is None
                    else lambda message, batch_index=batch_index, batch_total=len(frame_batches): progress_callback(f"[Batch {batch_index}/{batch_total}] {message}")
                ),
                cancel_callback=cancel_callback,
                cancellation_message="Discover residual debug export cancelled.",
            )
            _write_discovery_residual_debug_artifacts(
                output_directory=batch_output_dir,
                reference_header=context.reference_header,
                frame_paths=batch_paths,
                frame_times=context.frame_times,
                aligned_frames=context.aligned_frames,
                background_model=context.background_model,
                residual_detections_by_frame=residual_detections_by_frame,
            )
            _emit_progress(progress_callback, f"Saved Discover residual debug outputs to {batch_output_dir}.")
    finally:
        _cleanup_discovery_temp_cache_dir(working_cache_dir)
    return output_directory


def _build_discovery_result_snapshot(
    *,
    reference_path: Path,
    preview_data: np.ndarray,
    frame_count: int,
    aggregated_candidates: list[MovingObjectCandidate] | tuple[MovingObjectCandidate, ...],
    frame_times_by_index: list[datetime | None],
    frame_wcs_by_index: list[object | None],
    known_detection_result: SolarSystemDetectionResult | None,
    estimate_result: SolarSystemVisibilityEstimateResult | None,
    detector_mode: str,
    potential_candidate_max_deflection_rms_px: float,
    review_candidate_max_deflection_rms_px: float,
    minimum_candidate_frames: int,
    synthetic_sweep_summary_text: str | None,
    allow_known_object_benchmarking: bool,
) -> MovingObjectDiscoveryResult:
    linked_candidates = _rank_and_number_candidates(list(aggregated_candidates))
    recovered_known_objects: list[RecoveredKnownMovingObject] = []
    missed_known_objects: list[MissedKnownMovingObject] = []
    candidates = list(linked_candidates)
    known_detections: list[SolarSystemDetection] = []
    if known_detection_result is not None and allow_known_object_benchmarking:
        available_frame_indices = [
            index
            for index, (frame_time, frame_wcs) in enumerate(zip(frame_times_by_index, frame_wcs_by_index))
            if frame_time is not None and frame_wcs is not None
        ]
        if not available_frame_indices:
            raise RuntimeError("Discover could not map any benchmarkable frames for known-object benchmarking.")
        compact_candidates = _compact_remap_candidate_frame_indices(linked_candidates, available_frame_indices)
        compact_candidate_lookup = {candidate.candidate_id: candidate for candidate in linked_candidates}
        recovered_compact, missed_known_objects, unmatched_compact = _associate_candidates_with_known_detections(
            compact_candidates,
            frame_times=[frame_times_by_index[index] for index in available_frame_indices if frame_times_by_index[index] is not None],
            frame_pixel_wcs=[frame_wcs_by_index[index] for index in available_frame_indices if frame_wcs_by_index[index] is not None],
            known_detection_result=known_detection_result,
            candidate_detections=list(known_detection_result.detections),
            minimum_matched_frames=minimum_candidate_frames,
            estimated_magnitude_limit=(None if estimate_result is None else float(estimate_result.dimmest_visible_magnitude)),
        )
        recovered_known_objects = [
            replace(
                recovered,
                candidate=compact_candidate_lookup.get(recovered.candidate.candidate_id, recovered.candidate),
            )
            for recovered in recovered_compact
        ]
        candidates = [compact_candidate_lookup.get(candidate.candidate_id, candidate) for candidate in unmatched_compact]
        known_detections = list(known_detection_result.detections)
    potential_candidates, review_candidates, suppressed_candidate_count = _split_discovery_candidates_by_linearity(
        candidates,
        potential_candidate_max_deflection_rms_px=potential_candidate_max_deflection_rms_px,
        review_candidate_max_deflection_rms_px=review_candidate_max_deflection_rms_px,
    )
    ranked_candidates = _rank_and_number_candidates(potential_candidates, candidate_prefix="C")
    ranked_review_candidates = _rank_and_number_candidates(review_candidates, candidate_prefix="R")
    summary_text, report_text, known_predicted_magnitudes, benchmark_known_count, benchmark_recovered_count, methods_summary_text = _summarize_discovery_results(
        frame_count=frame_count,
        known_detections=known_detections,
        estimate_result=estimate_result,
        recovered_known_objects=recovered_known_objects,
        unmatched_candidates=ranked_candidates,
        review_candidates=ranked_review_candidates,
        suppressed_candidate_count=suppressed_candidate_count,
        detector_mode=detector_mode,
        potential_candidate_max_deflection_rms_px=potential_candidate_max_deflection_rms_px,
        review_candidate_max_deflection_rms_px=review_candidate_max_deflection_rms_px,
        synthetic_sweep_summary_text=synthetic_sweep_summary_text,
    )
    return MovingObjectDiscoveryResult(
        reference_path=reference_path,
        preview_data=preview_data,
        candidate_count=len(ranked_candidates),
        aligned_frame_count=frame_count,
        candidates=tuple(ranked_candidates),
        summary_text=summary_text,
        report_text=report_text,
        review_candidates=tuple(ranked_review_candidates),
        estimate_result=estimate_result,
        benchmark_known_count=benchmark_known_count,
        benchmark_recovered_count=benchmark_recovered_count,
        recovered_known_count=len(recovered_known_objects),
        known_detection_count=len(known_detections),
        recovered_known_objects=tuple(recovered_known_objects),
        missed_known_objects=tuple(missed_known_objects),
        known_predicted_magnitudes=known_predicted_magnitudes,
        methods_summary_text=methods_summary_text,
    )


def recover_known_moving_objects(
    frame_paths: list[Path],
    *,
    frame_metadata: dict[str, ObservationMetadata],
    known_detection_result: SolarSystemDetectionResult | None,
    reference_path: Path | None = None,
    assume_aligned: bool = False,
    max_parallel_workers: int = 0,
    progress_callback: Callable[[str], None] | None = None,
    cancel_callback: Callable[[], bool] | None = None,
) -> MovingObjectRecoveryResult:
    if known_detection_result is None:
        raise ValueError("Recovery requires a prior Generate result for the current group.")
    benchmark_detections = [detection for detection in known_detection_result.detections if detection.likely_visible]
    reference_path, preview_data, frame_times, frame_pixel_wcs, _pixel_scale_arcsec_per_pixel, candidates = _search_moving_candidates(
        frame_paths,
        frame_metadata=frame_metadata,
        known_detection_result=known_detection_result,
        reference_path=reference_path,
        assume_aligned=assume_aligned,
        max_parallel_workers=max_parallel_workers,
        exclude_known_positions=False,
        residual_min_snr=0.0,
        residual_max_snr=0.0,
        binning_factor=1,
        minimum_candidate_frames=_DISCOVERY_MIN_FRAMES,
        detection_sigma=_DISCOVERY_DETECTION_SIGMA,
        detection_fwhm=_DISCOVERY_DETECTION_FWHM,
        max_residuals_per_frame=_DISCOVERY_MAX_RESIDUALS_PER_FRAME,
        edge_margin_px=_DISCOVERY_EDGE_MARGIN_PX,
        detector_mode=_DISCOVERY_DETECTOR_MODE_POINT,
        streak_min_area_px=_DISCOVERY_STREAK_MIN_AREA_PX,
        streak_min_elongation=_DISCOVERY_STREAK_MIN_ELONGATION,
        motion_prior_bias=_DISCOVERY_MOTION_PRIOR_BIAS_BALANCED,
        synthetic_sweep_settings=None,
        working_cache_dir=None,
        progress_callback=progress_callback,
        cancel_callback=cancel_callback,
        cancellation_message="Recover Known cancelled.",
        mode_label="recovery",
    )
    recovered_known_objects, missed_known_objects, unmatched_candidates = _associate_candidates_with_known_detections(
        candidates,
        frame_times=frame_times,
        frame_pixel_wcs=frame_pixel_wcs,
        known_detection_result=known_detection_result,
        candidate_detections=benchmark_detections,
    )
    benchmark_known_count = len(recovered_known_objects) + len(missed_known_objects)
    if benchmark_known_count > 0:
        summary_text = (
            f"Recovered {len(recovered_known_objects)} of {benchmark_known_count} likely visible known object(s) "
            f"and found {len(unmatched_candidates)} unmatched moving candidate(s) from {len(frame_paths)} frame(s)."
        )
    else:
        summary_text = (
            f"No likely visible known objects were available for recovery benchmarking; "
            f"found {len(unmatched_candidates)} unmatched moving candidate(s) from {len(frame_paths)} frame(s)."
        )
    return MovingObjectRecoveryResult(
        reference_path=reference_path,
        preview_data=preview_data,
        aligned_frame_count=len(frame_paths),
        benchmark_known_count=benchmark_known_count,
        recovered_known_count=len(recovered_known_objects),
        unmatched_candidate_count=len(unmatched_candidates),
        recovered_known_objects=tuple(recovered_known_objects),
        missed_known_objects=tuple(missed_known_objects),
        unmatched_candidates=tuple(unmatched_candidates),
        summary_text=summary_text,
    )


def _prediction_magnitude_values(detections: list[SolarSystemDetection]) -> tuple[float, ...]:
    magnitudes: list[float] = []
    for detection in detections:
        if detection.predicted_magnitude is None:
            continue
        magnitude = float(detection.predicted_magnitude)
        if np.isfinite(magnitude):
            magnitudes.append(magnitude)
    magnitudes.sort()
    return tuple(magnitudes)


def _format_predicted_magnitude(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _detection_within_estimated_limit(detection: SolarSystemDetection, estimated_magnitude_limit: float | None) -> bool:
    if estimated_magnitude_limit is None or detection.predicted_magnitude is None:
        return False
    magnitude = float(detection.predicted_magnitude)
    return bool(np.isfinite(magnitude) and magnitude <= float(estimated_magnitude_limit) + 1e-6)


def _discovery_methods_summary(detector_mode: str) -> str:
    if detector_mode == _DISCOVERY_DETECTOR_MODE_POINT:
        detector_label = "point-source residual detector"
    elif detector_mode == _DISCOVERY_DETECTOR_MODE_STREAK:
        detector_label = "streak residual detector"
    else:
        detector_label = "hybrid point-and-streak residual detector"
    return f"Recoveries used a Gaia visible-limit estimate, the {detector_label}, and linear tracklet linking across the current group."


def _discovery_methods_summary_with_linearity_screen(
    detector_mode: str,
    *,
    potential_candidate_max_deflection_rms_px: float,
    review_candidate_max_deflection_rms_px: float,
    synthetic_sweep_summary_text: str | None,
) -> str:
    base_summary = _discovery_methods_summary(detector_mode)
    summary = (
        f"{base_summary} A post-link linear-motion screen keeps potential discoveries at <= {potential_candidate_max_deflection_rms_px:.2f} px RMS, "
        f"keeps Borderline Review tracklets at <= {review_candidate_max_deflection_rms_px:.2f} px RMS, and suppresses larger-deflection tracklets."
    )
    if synthetic_sweep_summary_text:
        return f"{summary} {synthetic_sweep_summary_text}"
    return summary


def _discovery_synthetic_sweep_summary(settings: _SyntheticSweepSettings | None, *, ran: bool) -> str | None:
    if settings is None:
        return None
    stage_label = "ran" if ran else "was configured"
    combine_label = "sigma-clipped mean" if settings.combine_mode == "sigma_clipped_mean" else "mean"
    if settings.direction_focus_mode == _DISCOVERY_SYNTHETIC_SWEEP_DIRECTION_FOCUS_MAIN_BELT:
        direction_label = (
            "used a main-belt direction focus with a "
            f"+/-{settings.direction_focus_half_width_deg:.1f} deg window when that image-plane motion could be inferred, "
            "otherwise fell back to all directions"
        )
    else:
        direction_label = "covered all directions"
    save_label = " and saved each tested velocity stack to synthetic_track FITS output" if settings.save_stacks else ""
    return (
        "The optional final synthetic sweep "
        f"{stage_label} and {direction_label} up to {settings.max_motion_px_per_hour:.1f} px/h, "
        f"used {settings.motion_step_px_per_hour:.1f} px/h motion steps and {settings.angle_step_deg:.1f} deg angle steps, "
        f"required stacked SNR >= {settings.min_stacked_snr:.1f}, and combined each velocity stack with {combine_label}{save_label}."
    )


def _summarize_discovery_results(
    *,
    frame_count: int,
    known_detections: list[SolarSystemDetection],
    estimate_result: SolarSystemVisibilityEstimateResult | None,
    recovered_known_objects: list[RecoveredKnownMovingObject],
    unmatched_candidates: list[MovingObjectCandidate],
    review_candidates: list[MovingObjectCandidate],
    suppressed_candidate_count: int,
    detector_mode: str,
    potential_candidate_max_deflection_rms_px: float,
    review_candidate_max_deflection_rms_px: float,
    synthetic_sweep_summary_text: str | None,
) -> tuple[str, str, tuple[float, ...], int, int, str]:
    known_predicted_magnitudes = _prediction_magnitude_values(known_detections)
    estimated_magnitude_limit = None if estimate_result is None else float(estimate_result.dimmest_visible_magnitude)
    benchmark_known_count = sum(_detection_within_estimated_limit(detection, estimated_magnitude_limit) for detection in known_detections)
    benchmark_recovered_count = sum(recovered.within_estimated_limit for recovered in recovered_known_objects)
    methods_summary_text = _discovery_methods_summary_with_linearity_screen(
        detector_mode,
        potential_candidate_max_deflection_rms_px=potential_candidate_max_deflection_rms_px,
        review_candidate_max_deflection_rms_px=review_candidate_max_deflection_rms_px,
        synthetic_sweep_summary_text=synthetic_sweep_summary_text,
    )
    potential_discovery_count = len(unmatched_candidates)
    review_candidate_count = len(review_candidates)
    if potential_discovery_count == 0 and review_candidate_count == 0:
        discovery_line = "Discover did not retain any additional unmatched moving candidates after the linear-motion review."
    elif review_candidate_count == 0:
        discovery_line = f"Discover also found {potential_discovery_count} potential discovery candidate(s)."
    elif potential_discovery_count == 0:
        discovery_line = f"Discover kept {review_candidate_count} tracklet(s) in Borderline Review for manual review."
    else:
        discovery_line = (
            f"Discover also found {potential_discovery_count} potential discovery candidate(s) and kept {review_candidate_count} "
            "tracklet(s) in Borderline Review for manual review."
        )
    report_lines: list[str] = []
    if estimate_result is not None:
        summary_text = (
            f"Estimated visible limit {estimate_result.dimmest_visible_magnitude:.1f} mag; recovered {benchmark_recovered_count} of {benchmark_known_count} known object(s) within that limit, "
            f"found {potential_discovery_count} potential discovery candidate(s), and kept {review_candidate_count} Borderline Review tracklet(s) from {frame_count} frame(s)."
            if benchmark_known_count > 0
            else (
                f"Estimated visible limit {estimate_result.dimmest_visible_magnitude:.1f} mag; recovered {len(recovered_known_objects)} known object(s), "
                f"found {potential_discovery_count} potential discovery candidate(s), and kept {review_candidate_count} Borderline Review tracklet(s) from {frame_count} frame(s)."
            )
        )
        report_lines.append(f"Estimated visible limit for Discover: Gaia G {estimate_result.dimmest_visible_magnitude:.1f}.")
        if benchmark_known_count > 0:
            report_lines.append(f"{benchmark_known_count} known object(s) are within the estimated visible limit, and Discover recovered {benchmark_recovered_count} of them.")
        else:
            report_lines.append("No known objects fall within the estimated visible limit for this frame.")
    else:
        summary_text = (
            f"Recovered {len(recovered_known_objects)} known object(s), found {potential_discovery_count} potential discovery candidate(s), "
            f"and kept {review_candidate_count} Borderline Review tracklet(s) from {frame_count} frame(s)."
        )
        report_lines.append("Discover could not derive a visible-limit estimate for this run, so the magnitude-limit benchmark is unavailable.")
    if known_detections:
        report_lines.append(f"Generate predicted {len(known_detections)} known object(s) in the current field.")
        if estimate_result is not None and not known_predicted_magnitudes:
            report_lines.append("None of the generated known objects included a usable predicted magnitude for estimate-based benchmarking.")
        report_lines.append(f"Discover matched {len(recovered_known_objects)} known object(s) from the current Generate list and listed them separately from the unmatched candidates.")
    else:
        report_lines.append("No Generate-side known-object list was available for this Discover run.")
    report_lines.append(methods_summary_text)
    report_lines.append(discovery_line)
    if review_candidate_count > 0:
        report_lines.append(
            f"Tracklets with linear-motion deflection RMS above {potential_candidate_max_deflection_rms_px:.2f} px and at or below {review_candidate_max_deflection_rms_px:.2f} px were moved into Borderline Review for manual review."
        )
    if suppressed_candidate_count > 0:
        report_lines.append(
            f"The linear-motion screen suppressed {suppressed_candidate_count} high-deflection tracklet(s) above {review_candidate_max_deflection_rms_px:.2f} px RMS."
        )
    return summary_text, "\n".join(report_lines), known_predicted_magnitudes, benchmark_known_count, benchmark_recovered_count, methods_summary_text


def _search_moving_candidates(
    frame_paths: list[Path],
    *,
    frame_metadata: dict[str, ObservationMetadata],
    known_detection_result: SolarSystemDetectionResult | None,
    reference_path: Path | None,
    assume_aligned: bool,
    max_parallel_workers: int,
    exclude_known_positions: bool,
    residual_min_snr: float,
    residual_max_snr: float,
    binning_factor: int,
    minimum_candidate_frames: int,
    detection_sigma: float,
    detection_fwhm: float,
    max_residuals_per_frame: int,
    edge_margin_px: int,
    min_seed_displacement_px: float,
    detector_mode: str,
    streak_min_area_px: int,
    streak_min_elongation: float,
    motion_prior_bias: str,
    synthetic_sweep_settings: _SyntheticSweepSettings | None,
    working_cache_dir: Path | None,
    progress_callback: Callable[[str], None] | None,
    cancel_callback: Callable[[], bool] | None,
    cancellation_message: str,
    mode_label: str,
    diagnostics: list[DiscoveryBatchDiagnostics] | None = None,
    diagnostics_batch_index: int = 1,
    global_frame_indices: tuple[int, ...] | None = None,
) -> tuple[Path, np.ndarray, list[datetime], list[object], float | None, list[MovingObjectCandidate]]:
    context = _prepare_residual_search_context(
        frame_paths,
        frame_metadata=frame_metadata,
        known_detection_result=known_detection_result,
        reference_path=reference_path,
        assume_aligned=assume_aligned,
        max_parallel_workers=max_parallel_workers,
        minimum_candidate_frames=minimum_candidate_frames,
        binning_factor=binning_factor,
        motion_prior_bias=motion_prior_bias,
        synthetic_sweep_settings=synthetic_sweep_settings,
        working_cache_dir=working_cache_dir,
        exclude_known_positions=exclude_known_positions,
        progress_callback=progress_callback,
        cancel_callback=cancel_callback,
        cancellation_message=cancellation_message,
        mode_label=mode_label,
    )
    resolved_reference_path = context.resolved_reference_path
    frame_times = context.frame_times
    aligned_frames = context.aligned_frames
    frame_pixel_wcs = context.frame_pixel_wcs
    preview_data = context.preview_data
    background_model = context.background_model
    pixel_scale_arcsec_per_pixel = context.pixel_scale_arcsec_per_pixel
    known_positions = context.known_positions
    motion_range_estimate = context.motion_range_estimate
    effective_synthetic_sweep_settings = context.effective_synthetic_sweep_settings
    prepare_seconds = context.prepare_seconds
    worker_count = _resolved_search_workers(len(frame_paths), max_parallel_workers)
    residual_scan_start = time.perf_counter()
    residual_detections_by_frame = _scan_frames_for_residual_detections(
        frame_paths,
        frame_times=frame_times,
        frame_pixel_wcs=frame_pixel_wcs,
        aligned_frames=aligned_frames,
        background_model=background_model,
        known_positions=known_positions,
        exclude_known_positions=exclude_known_positions,
        residual_min_snr=residual_min_snr,
        residual_max_snr=residual_max_snr,
        detection_sigma=detection_sigma,
        detection_fwhm=detection_fwhm,
        max_residuals_per_frame=max_residuals_per_frame,
        edge_margin_px=edge_margin_px,
        detector_mode=detector_mode,
        streak_min_area_px=streak_min_area_px,
        streak_min_elongation=streak_min_elongation,
        worker_count=worker_count,
        progress_callback=progress_callback,
        cancel_callback=cancel_callback,
        cancellation_message=cancellation_message,
    )
    residual_scan_seconds = time.perf_counter() - residual_scan_start
    if len(residual_detections_by_frame) != len(frame_times):
        raise RuntimeError(
            "Discover residual detection output does not match the current search window frame count."
        )

    _raise_if_cancelled(cancel_callback, cancellation_message)
    _emit_progress(progress_callback, "Linking residual detections into motion-consistent candidates.")
    link_start = time.perf_counter()
    candidates = _link_moving_candidates(
        residual_detections_by_frame,
        frame_times=frame_times,
        pixel_scale_arcsec_per_pixel=pixel_scale_arcsec_per_pixel,
        minimum_candidate_frames=minimum_candidate_frames,
        min_seed_displacement_px=min_seed_displacement_px,
        min_motion_px_per_hour=(None if motion_range_estimate is None else motion_range_estimate.min_motion_px_per_hour),
        max_motion_px_per_hour=(None if motion_range_estimate is None else motion_range_estimate.max_motion_px_per_hour),
        cancel_callback=cancel_callback,
        cancellation_message=cancellation_message,
    )
    link_seconds = time.perf_counter() - link_start
    synthetic_sweep_seconds = 0.0
    synthetic_sweep_candidate_count = 0
    if effective_synthetic_sweep_settings is not None and mode_label == "discovery":
        if effective_synthetic_sweep_settings.direction_focus_mode == _DISCOVERY_SYNTHETIC_SWEEP_DIRECTION_FOCUS_MAIN_BELT:
            _emit_progress(progress_callback, "Running the final main-belt-focused synthetic sweep across the current discovery search window.")
        else:
            _emit_progress(progress_callback, "Running the final all-direction synthetic sweep across the current discovery search window.")
        synthetic_sweep_start = time.perf_counter()
        synthetic_sweep_candidates = _discover_candidates_with_synthetic_sweep(
                frame_paths=frame_paths,
                frame_times=frame_times,
                frame_pixel_wcs=frame_pixel_wcs,
                aligned_frames=aligned_frames,
                background_model=background_model,
                reference_path=resolved_reference_path,
                known_detection_result=known_detection_result,
                pixel_scale_arcsec_per_pixel=pixel_scale_arcsec_per_pixel,
                minimum_candidate_frames=minimum_candidate_frames,
                detection_sigma=detection_sigma,
                detection_fwhm=detection_fwhm,
                edge_margin_px=edge_margin_px,
                min_seed_displacement_px=min_seed_displacement_px,
                sweep_settings=effective_synthetic_sweep_settings,
                max_parallel_workers=max_parallel_workers,
                progress_callback=progress_callback,
                cancel_callback=cancel_callback,
                cancellation_message=cancellation_message,
            )
        synthetic_sweep_seconds = time.perf_counter() - synthetic_sweep_start
        synthetic_sweep_candidate_count = len(synthetic_sweep_candidates)
        candidates.extend(synthetic_sweep_candidates)
    if diagnostics is not None:
        resolved_frame_indices = tuple(range(len(frame_paths))) if global_frame_indices is None else tuple(global_frame_indices)
        remapped_candidates = (
            tuple(candidates)
            if global_frame_indices is None
            else tuple(_remap_candidate_frame_indices(candidates, list(resolved_frame_indices)))
        )
        diagnostics.append(
            DiscoveryBatchDiagnostics(
                batch_index=max(1, int(diagnostics_batch_index)),
                frame_indices=resolved_frame_indices,
                frame_times=tuple(frame_times),
                frame_pixel_wcs=tuple(frame_pixel_wcs),
                residual_detections_by_frame=tuple(
                    tuple(
                        DiscoveryResidualSnapshot(
                            frame_index=resolved_frame_indices[detection.frame_index],
                            observation_time=detection.observation_time,
                            x=detection.x,
                            y=detection.y,
                            local_snr=detection.local_snr,
                            ra_deg=detection.ra_deg,
                            dec_deg=detection.dec_deg,
                        )
                        for detection in frame_detections
                    )
                    for frame_detections in residual_detections_by_frame
                ),
                linked_candidates=remapped_candidates,
                prepare_seconds=prepare_seconds,
                residual_scan_seconds=residual_scan_seconds,
                link_seconds=link_seconds,
                synthetic_sweep_seconds=synthetic_sweep_seconds,
                residual_detection_count=sum(len(frame_detections) for frame_detections in residual_detections_by_frame),
                linked_candidate_count=len(remapped_candidates),
                synthetic_sweep_candidate_count=synthetic_sweep_candidate_count,
            )
        )
    return resolved_reference_path, preview_data, frame_times, frame_pixel_wcs, pixel_scale_arcsec_per_pixel, candidates


def _prepare_residual_search_context(
    frame_paths: list[Path],
    *,
    frame_metadata: dict[str, ObservationMetadata],
    known_detection_result: SolarSystemDetectionResult | None,
    reference_path: Path | None,
    assume_aligned: bool,
    max_parallel_workers: int,
    minimum_candidate_frames: int,
    binning_factor: int,
    motion_prior_bias: str,
    synthetic_sweep_settings: _SyntheticSweepSettings | None,
    working_cache_dir: Path | None,
    exclude_known_positions: bool,
    progress_callback: Callable[[str], None] | None,
    cancel_callback: Callable[[], bool] | None,
    cancellation_message: str,
    mode_label: str,
) -> _PreparedResidualSearchContext:
    if len(frame_paths) < minimum_candidate_frames:
        action_label = "Recovery" if mode_label == "recovery" else "Discovery"
        raise ValueError(f"{action_label} requires at least {minimum_candidate_frames} solved frames in the current group.")
    resolved_reference_path = frame_paths[0] if reference_path is None else reference_path
    if resolved_reference_path not in frame_paths:
        raise ValueError("The selected reference frame is not part of the loaded group.")

    _raise_if_cancelled(cancel_callback, cancellation_message)
    if mode_label == "recovery":
        if assume_aligned:
            _emit_progress(progress_callback, f"Preparing known-object recovery residual search for {len(frame_paths)} already aligned frame(s).")
        else:
            _emit_progress(progress_callback, f"Preparing known-object recovery alignment for {len(frame_paths)} frame(s).")
    elif assume_aligned:
        _emit_progress(progress_callback, f"Preparing discovery residual search for {len(frame_paths)} already aligned frame(s).")
    else:
        _emit_progress(progress_callback, f"Preparing discovery alignment for {len(frame_paths)} frame(s).")
    reference_header, reference_width, reference_height = read_header_and_shape(resolved_reference_path)
    reference_wcs, reference_reasons = _validated_celestial_wcs(resolved_reference_path, reference_header)
    if reference_wcs is None:
        reason_text = reference_reasons[0] if reference_reasons else "No usable celestial WCS was found."
        raise ValueError(f"Reference frame {resolved_reference_path.name} is not plate-solved: {reason_text}")
    if reference_width is None or reference_height is None:
        raise ValueError(f"Reference frame {resolved_reference_path.name} is missing image dimensions.")
    target_shape = (int(reference_height), int(reference_width))
    worker_count = _resolved_search_workers(len(frame_paths), max_parallel_workers)
    _emit_progress(progress_callback, f"Using {worker_count} worker thread(s) for moving-object {mode_label} search.")

    prepare_start = time.perf_counter()
    prepared_frames = _prepare_frames_for_search(
        frame_paths,
        frame_metadata=frame_metadata,
        resolved_reference_path=resolved_reference_path,
        reference_wcs=reference_wcs,
        target_shape=target_shape,
        assume_aligned=assume_aligned,
        worker_count=worker_count,
        binning_factor=binning_factor,
        working_cache_dir=working_cache_dir,
        progress_callback=progress_callback,
        cancel_callback=cancel_callback,
        cancellation_message=cancellation_message,
    )
    prepare_seconds = time.perf_counter() - prepare_start
    frame_times = [item.observation_time for item in prepared_frames]
    aligned_frames = [item.aligned_frame for item in prepared_frames]
    frame_pixel_wcs = [item.frame_wcs for item in prepared_frames]

    _raise_if_cancelled(cancel_callback, cancellation_message)
    _emit_progress(progress_callback, f"Building the residual preview and background model from {len(prepared_frames)} aligned frame(s).")
    aligned_cube = np.stack(aligned_frames, axis=0)
    preview_data = np.asarray(np.nanmean(aligned_cube, axis=0), dtype=np.float32)
    background_model = np.asarray(np.nanmedian(aligned_cube, axis=0), dtype=np.float32)
    pixel_scale_arcsec_per_pixel = _reference_pixel_scale_arcsec(reference_wcs, known_detection_result)
    if pixel_scale_arcsec_per_pixel is not None and binning_factor > 1:
        pixel_scale_arcsec_per_pixel = float(pixel_scale_arcsec_per_pixel) * float(binning_factor)
    motion_range_estimate = _estimate_discovery_motion_range(
        frame_paths,
        frame_metadata=frame_metadata,
        pixel_scale_arcsec_per_pixel=pixel_scale_arcsec_per_pixel,
        motion_prior_bias=motion_prior_bias,
    )
    effective_synthetic_sweep_settings = _apply_motion_range_to_synthetic_sweep_settings(
        synthetic_sweep_settings,
        motion_range_estimate=motion_range_estimate,
    )
    if motion_range_estimate is not None:
        _emit_progress(
            progress_callback,
            "Using adaptive discovery motion range "
            f"{motion_range_estimate.min_motion_px_per_hour:.1f}-{motion_range_estimate.max_motion_px_per_hour:.1f} px/h "
            f"from {motion_range_estimate.pixel_scale_arcsec_per_pixel:.2f} arcsec/px and median "
            f"{motion_range_estimate.median_exposure_seconds:.0f}s exposures.",
        )
    if exclude_known_positions and known_detection_result is not None:
        _emit_progress(progress_callback, "Projecting known object positions for residual exclusion.")
    known_positions = _known_positions_by_frame(
        frame_times,
        frame_pixel_wcs=frame_pixel_wcs,
        known_detection_result=known_detection_result,
    )
    return _PreparedResidualSearchContext(
        resolved_reference_path=resolved_reference_path,
        reference_header=reference_header.copy(),
        frame_times=frame_times,
        aligned_frames=aligned_frames,
        frame_pixel_wcs=frame_pixel_wcs,
        preview_data=preview_data,
        background_model=background_model,
        pixel_scale_arcsec_per_pixel=pixel_scale_arcsec_per_pixel,
        known_positions=known_positions,
        motion_range_estimate=motion_range_estimate,
        effective_synthetic_sweep_settings=effective_synthetic_sweep_settings,
        prepare_seconds=prepare_seconds,
    )


def _write_discovery_residual_debug_artifacts(
    *,
    output_directory: Path,
    reference_header: fits.Header,
    frame_paths: list[Path],
    frame_times: list[datetime],
    aligned_frames: list[np.ndarray],
    background_model: np.ndarray,
    residual_detections_by_frame: list[list[_ResidualDetection]],
) -> None:
    output_directory.mkdir(parents=True, exist_ok=True)
    residual_images = [np.asarray(aligned_frame - background_model, dtype=np.float32) for aligned_frame in aligned_frames]
    residual_stack = np.asarray(np.nanmean(np.stack(residual_images, axis=0), axis=0), dtype=np.float32)
    _write_discovery_debug_fits(
        output_directory / "background_model.fits",
        background_model,
        reference_header=reference_header,
        debug_label="Discover residual background model",
    )
    _write_discovery_debug_fits(
        output_directory / "residual_stack_mean.fits",
        residual_stack,
        reference_header=reference_header,
        debug_label="Mean stack of per-frame residual images",
    )
    for frame_index, (frame_path, residual_image) in enumerate(zip(frame_paths, residual_images), start=1):
        _write_discovery_debug_fits(
            output_directory / f"residual_{frame_index:03d}_{frame_path.stem}.fits",
            residual_image,
            reference_header=reference_header,
            debug_label=f"Per-frame residual image for {frame_path.name}",
        )
    manifest_lines = [
        f"Reference grid: {frame_paths[0].name}",
        f"Frame count: {len(frame_paths)}",
        "Saved files:",
        "- background_model.fits",
        "- residual_stack_mean.fits",
    ]
    for frame_index, (frame_path, frame_time, detections) in enumerate(zip(frame_paths, frame_times, residual_detections_by_frame), start=1):
        manifest_lines.append(
            f"- residual_{frame_index:03d}_{frame_path.stem}.fits | {frame_time.isoformat()} | residual detections={len(detections)}"
        )
    (output_directory / "manifest.txt").write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")


def _write_discovery_debug_fits(
    output_path: Path,
    image_data: np.ndarray,
    *,
    reference_header: fits.Header,
    debug_label: str,
) -> None:
    header = reference_header.copy()
    header.add_history(debug_label)
    fits.PrimaryHDU(data=np.asarray(image_data, dtype=np.float32), header=header).writeto(output_path, overwrite=True)


def _discover_candidates_with_synthetic_sweep(
    *,
    frame_paths: list[Path],
    frame_times: list[datetime],
    frame_pixel_wcs: list[object],
    aligned_frames: list[np.ndarray],
    background_model: np.ndarray,
    reference_path: Path,
    known_detection_result: SolarSystemDetectionResult | None,
    pixel_scale_arcsec_per_pixel: float | None,
    minimum_candidate_frames: int,
    min_seed_displacement_px: float,
    detection_sigma: float,
    detection_fwhm: float,
    edge_margin_px: int,
    sweep_settings: _SyntheticSweepSettings,
    max_parallel_workers: int,
    progress_callback: Callable[[str], None] | None,
    cancel_callback: Callable[[], bool] | None,
    cancellation_message: str,
) -> list[MovingObjectCandidate]:
    frame_hours = np.asarray(
        [(timestamp - frame_times[0]).total_seconds() / 3600.0 for timestamp in frame_times],
        dtype=float,
    )
    positive_residual_frames = [
        np.asarray(np.maximum(frame - background_model, 0.0), dtype=np.float32)
        for frame in aligned_frames
    ]
    angle_values = _synthetic_sweep_angle_values(
        sweep_settings,
        frame_times=frame_times,
        frame_pixel_wcs=frame_pixel_wcs,
        known_detection_result=known_detection_result,
        progress_callback=progress_callback,
    )
    vectors = _synthetic_sweep_velocity_vectors(sweep_settings, angle_values=angle_values)
    if not vectors:
        return []

    sample_y_grid, sample_x_grid = _shift_sampling_grids(*positive_residual_frames[0].shape)
    sweep_worker_count = _resolved_synthetic_sweep_workers(len(vectors), max_parallel_workers)
    if sweep_worker_count > 1:
        _emit_progress(progress_callback, f"Using {sweep_worker_count} worker thread(s) for synthetic sweep vectors.")

    candidates: list[MovingObjectCandidate] = []
    if sweep_worker_count <= 1 or len(vectors) <= 1:
        for vector_index, (speed_px_per_hour, angle_deg, velocity_x, velocity_y) in enumerate(vectors, start=1):
            _raise_if_cancelled(cancel_callback, cancellation_message)
            vector_candidates, stack_peak_count = _evaluate_synthetic_sweep_vector(
                vector_index=vector_index,
                speed_px_per_hour=speed_px_per_hour,
                angle_deg=angle_deg,
                velocity_x=velocity_x,
                velocity_y=velocity_y,
                frame_paths=frame_paths,
                frame_times=frame_times,
                frame_pixel_wcs=frame_pixel_wcs,
                residual_frames=positive_residual_frames,
                frame_hours=frame_hours,
                reference_path=reference_path,
                pixel_scale_arcsec_per_pixel=pixel_scale_arcsec_per_pixel,
                minimum_candidate_frames=minimum_candidate_frames,
                min_seed_displacement_px=min_seed_displacement_px,
                detection_sigma=detection_sigma,
                detection_fwhm=detection_fwhm,
                edge_margin_px=edge_margin_px,
                sweep_settings=sweep_settings,
                sample_x_grid=sample_x_grid,
                sample_y_grid=sample_y_grid,
                cancel_callback=cancel_callback,
                cancellation_message=cancellation_message,
            )
            candidates.extend(vector_candidates)
            _emit_progress(
                progress_callback,
                f"[Synthetic sweep {vector_index}/{len(vectors)}] {speed_px_per_hour:.1f} px/h at {angle_deg:.1f} deg produced {stack_peak_count} stacked peak(s).",
            )
        return candidates

    executor = ThreadPoolExecutor(max_workers=sweep_worker_count)
    try:
        futures = {
            executor.submit(
                _evaluate_synthetic_sweep_vector,
                vector_index=vector_index,
                speed_px_per_hour=speed_px_per_hour,
                angle_deg=angle_deg,
                velocity_x=velocity_x,
                velocity_y=velocity_y,
                frame_paths=frame_paths,
                frame_times=frame_times,
                frame_pixel_wcs=frame_pixel_wcs,
                residual_frames=positive_residual_frames,
                frame_hours=frame_hours,
                reference_path=reference_path,
                pixel_scale_arcsec_per_pixel=pixel_scale_arcsec_per_pixel,
                minimum_candidate_frames=minimum_candidate_frames,
                min_seed_displacement_px=min_seed_displacement_px,
                detection_sigma=detection_sigma,
                detection_fwhm=detection_fwhm,
                edge_margin_px=edge_margin_px,
                sweep_settings=sweep_settings,
                sample_x_grid=sample_x_grid,
                sample_y_grid=sample_y_grid,
                cancel_callback=cancel_callback,
                cancellation_message=cancellation_message,
            ): (vector_index, speed_px_per_hour, angle_deg)
            for vector_index, (speed_px_per_hour, angle_deg, velocity_x, velocity_y) in enumerate(vectors, start=1)
        }
        completed_count = 0
        for future in _iter_completed_futures(
            executor,
            futures,
            cancel_callback=cancel_callback,
            cancellation_message=cancellation_message,
        ):
            vector_index, speed_px_per_hour, angle_deg = futures[future]
            vector_candidates, stack_peak_count = future.result()
            completed_count += 1
            candidates.extend(vector_candidates)
            _emit_progress(
                progress_callback,
                f"[Synthetic sweep {completed_count}/{len(vectors)} | vector {vector_index}] {speed_px_per_hour:.1f} px/h at {angle_deg:.1f} deg produced {stack_peak_count} stacked peak(s).",
            )
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return candidates


def _evaluate_synthetic_sweep_vector(
    *,
    vector_index: int,
    speed_px_per_hour: float,
    angle_deg: float,
    velocity_x: float,
    velocity_y: float,
    frame_paths: list[Path],
    frame_times: list[datetime],
    frame_pixel_wcs: list[object],
    residual_frames: list[np.ndarray],
    frame_hours: np.ndarray,
    reference_path: Path,
    pixel_scale_arcsec_per_pixel: float | None,
    minimum_candidate_frames: int,
    min_seed_displacement_px: float,
    detection_sigma: float,
    detection_fwhm: float,
    edge_margin_px: int,
    sweep_settings: _SyntheticSweepSettings,
    sample_x_grid: np.ndarray,
    sample_y_grid: np.ndarray,
    cancel_callback: Callable[[], bool] | None,
    cancellation_message: str,
) -> tuple[list[MovingObjectCandidate], int]:
    stacked_frames: list[np.ndarray] = []
    for frame_index, residual_frame in enumerate(residual_frames):
        _raise_if_cancelled(cancel_callback, cancellation_message)
        stacked_frames.append(
            _shift_image_with_zero_fill(
                residual_frame,
                velocity_x * float(frame_hours[frame_index] - frame_hours[0]),
                velocity_y * float(frame_hours[frame_index] - frame_hours[0]),
                sample_x_grid=sample_x_grid,
                sample_y_grid=sample_y_grid,
            )
        )
    stacked_data = _combine_synthetic_sweep_frames(stacked_frames, combine_mode=sweep_settings.combine_mode)
    if sweep_settings.save_stacks:
        _write_synthetic_sweep_stack(
            stacked_data,
            output_directory=sweep_settings.output_directory,
            reference_path=reference_path,
            vector_index=vector_index,
            speed_px_per_hour=speed_px_per_hour,
            angle_deg=angle_deg,
            combine_mode=sweep_settings.combine_mode,
            direction_focus_mode=sweep_settings.direction_focus_mode,
            direction_focus_half_width_deg=sweep_settings.direction_focus_half_width_deg,
        )
    stack_detections = _detect_synthetic_sweep_stack_peaks(
        stacked_data,
        source_path=reference_path,
        observation_time=frame_times[0],
        min_stacked_snr=sweep_settings.min_stacked_snr,
        detection_sigma=detection_sigma,
        detection_fwhm=detection_fwhm,
        edge_margin_px=edge_margin_px,
    )
    candidates: list[MovingObjectCandidate] = []
    for stack_detection in stack_detections:
        _raise_if_cancelled(cancel_callback, cancellation_message)
        candidate = _build_synthetic_sweep_candidate(
            stack_detection,
            frame_paths=frame_paths,
            frame_times=frame_times,
            frame_pixel_wcs=frame_pixel_wcs,
            residual_frames=residual_frames,
            frame_hours=frame_hours,
            velocity_x=velocity_x,
            velocity_y=velocity_y,
            pixel_scale_arcsec_per_pixel=pixel_scale_arcsec_per_pixel,
            minimum_candidate_frames=minimum_candidate_frames,
            min_seed_displacement_px=min_seed_displacement_px,
            cancel_callback=cancel_callback,
            cancellation_message=cancellation_message,
        )
        if candidate is not None:
            candidates.append(candidate)
    return candidates, len(stack_detections)


def _build_synthetic_sweep_candidate(
    stack_detection: _ResidualDetection,
    *,
    frame_paths: list[Path],
    frame_times: list[datetime],
    frame_pixel_wcs: list[object],
    residual_frames: list[np.ndarray],
    frame_hours: np.ndarray,
    velocity_x: float,
    velocity_y: float,
    pixel_scale_arcsec_per_pixel: float | None,
    minimum_candidate_frames: int,
    min_seed_displacement_px: float,
    cancel_callback: Callable[[], bool] | None,
    cancellation_message: str,
) -> MovingObjectCandidate | None:
    detections: list[_ResidualDetection] = []
    for frame_index, (frame_path, observation_time, residual_frame) in enumerate(zip(frame_paths, frame_times, residual_frames)):
        _raise_if_cancelled(cancel_callback, cancellation_message)
        delta_hours = float(frame_hours[frame_index] - frame_hours[0])
        predicted_x = float(stack_detection.x + (velocity_x * delta_hours))
        predicted_y = float(stack_detection.y + (velocity_y * delta_hours))
        measured_x, measured_y, match_offset_px, local_snr, peak_value, _local_flux = measure_synthetic_tracking_peak(
            residual_frame,
            predicted_x,
            predicted_y,
            anchor_x=predicted_x,
            anchor_y=predicted_y,
            search_radius=2,
        )
        if measured_x is None or measured_y is None or match_offset_px is None or local_snr is None or peak_value is None:
            continue
        if match_offset_px > _DISCOVERY_MATCH_RADIUS_PX:
            continue
        ra_deg, dec_deg = _world_coordinates_for_detection(frame_pixel_wcs[frame_index], measured_x, measured_y)
        detections.append(
            _ResidualDetection(
                detection_id=frame_index + 1,
                frame_index=frame_index,
                source_path=frame_path,
                observation_time=observation_time,
                x=float(measured_x),
                y=float(measured_y),
                peak_value=float(peak_value),
                local_snr=float(local_snr),
                ra_deg=ra_deg,
                dec_deg=dec_deg,
            )
        )
    if len(detections) < minimum_candidate_frames:
        return None
    candidate = _build_candidate(
        detections,
        frame_hours=frame_hours,
        pixel_scale_arcsec_per_pixel=pixel_scale_arcsec_per_pixel,
        discovery_method=_DISCOVERY_CANDIDATE_METHOD_SYNTHETIC_SWEEP,
    )
    if candidate.displacement_px < float(min_seed_displacement_px):
        return None
    return candidate


def _synthetic_sweep_angle_values(
    settings: _SyntheticSweepSettings,
    *,
    frame_times: list[datetime],
    frame_pixel_wcs: list[object],
    known_detection_result: SolarSystemDetectionResult | None,
    progress_callback: Callable[[str], None] | None,
) -> list[float]:
    default_angle_values = list(np.arange(0.0, 360.0, float(settings.angle_step_deg), dtype=float))
    if not default_angle_values:
        default_angle_values = [0.0]
    if settings.direction_focus_mode != _DISCOVERY_SYNTHETIC_SWEEP_DIRECTION_FOCUS_MAIN_BELT:
        return default_angle_values
    focus_angle_values = _main_belt_sweep_angle_values(
        known_detection_result,
        frame_times=frame_times,
        frame_pixel_wcs=frame_pixel_wcs,
        angle_step_deg=float(settings.angle_step_deg),
        focus_half_width_deg=float(settings.direction_focus_half_width_deg),
    )
    if not focus_angle_values:
        _emit_progress(
            progress_callback,
            "Synthetic sweep could not infer a main-belt image-plane direction from the current known detections, so it is falling back to all directions.",
        )
        return default_angle_values
    return focus_angle_values


def _main_belt_sweep_angle_values(
    known_detection_result: SolarSystemDetectionResult | None,
    *,
    frame_times: list[datetime],
    frame_pixel_wcs: list[object],
    angle_step_deg: float,
    focus_half_width_deg: float,
) -> list[float]:
    if known_detection_result is None:
        return []
    main_belt_detections = [
        detection
        for detection in known_detection_result.detections
        if "main-belt" in str(detection.orbit_class or "").strip().lower()
    ]
    if not main_belt_detections:
        return []
    prediction_paths = _known_prediction_paths_by_detection(
        main_belt_detections,
        frame_times=frame_times,
        frame_pixel_wcs=frame_pixel_wcs,
        known_detection_result=known_detection_result,
    )
    motion_angles: list[float] = []
    for positions in prediction_paths.values():
        valid_positions = [item for item in positions if item is not None]
        if len(valid_positions) < 2:
            continue
        start_x, start_y = valid_positions[0]
        end_x, end_y = valid_positions[-1]
        delta_x = float(end_x - start_x)
        delta_y = float(end_y - start_y)
        if math.hypot(delta_x, delta_y) <= 1e-6:
            continue
        motion_angles.append(_normalize_angle_deg(math.degrees(math.atan2(delta_y, delta_x))))
    if not motion_angles:
        return []
    center_angle = _circular_mean_angle_deg(motion_angles)
    half_width = min(180.0, max(1.0, float(focus_half_width_deg)))
    angle_values = [
        angle_value
        for angle_value in np.arange(0.0, 360.0, float(angle_step_deg), dtype=float)
        if _circular_angular_distance_deg(float(angle_value), center_angle) <= half_width + 1e-6
    ]
    if not angle_values:
        return [center_angle]
    return [float(angle_value) for angle_value in angle_values]


def _circular_mean_angle_deg(angle_values_deg: list[float]) -> float:
    sine_sum = sum(math.sin(math.radians(value)) for value in angle_values_deg)
    cosine_sum = sum(math.cos(math.radians(value)) for value in angle_values_deg)
    if abs(sine_sum) <= 1e-9 and abs(cosine_sum) <= 1e-9:
        return _normalize_angle_deg(angle_values_deg[0])
    return _normalize_angle_deg(math.degrees(math.atan2(sine_sum, cosine_sum)))


def _circular_angular_distance_deg(first_angle_deg: float, second_angle_deg: float) -> float:
    difference = abs(_normalize_angle_deg(first_angle_deg) - _normalize_angle_deg(second_angle_deg))
    return min(difference, 360.0 - difference)


def _normalize_angle_deg(angle_deg: float) -> float:
    return float(angle_deg % 360.0)


def _normalize_synthetic_sweep_direction_focus_mode(direction_focus_mode: str) -> str:
    normalized = str(direction_focus_mode or _DISCOVERY_SYNTHETIC_SWEEP_DIRECTION_FOCUS_ALL).strip().lower()
    if normalized == _DISCOVERY_SYNTHETIC_SWEEP_DIRECTION_FOCUS_MAIN_BELT:
        return normalized
    return _DISCOVERY_SYNTHETIC_SWEEP_DIRECTION_FOCUS_ALL


def _synthetic_sweep_velocity_vectors(
    settings: _SyntheticSweepSettings,
    *,
    angle_values: list[float] | None = None,
) -> list[tuple[float, float, float, float]]:
    speeds: list[float] = []
    motion_value = float(settings.motion_step_px_per_hour)
    max_motion = float(settings.max_motion_px_per_hour)
    while motion_value < max_motion - 1e-6:
        speeds.append(motion_value)
        motion_value += float(settings.motion_step_px_per_hour)
    if not speeds or abs(speeds[-1] - max_motion) > 1e-6:
        speeds.append(max_motion)
    resolved_angle_values = list(angle_values or np.arange(0.0, 360.0, float(settings.angle_step_deg), dtype=float))
    if not resolved_angle_values:
        resolved_angle_values = [0.0]
    vectors: list[tuple[float, float, float, float]] = []
    for speed_px_per_hour in speeds:
        for angle_deg in resolved_angle_values:
            angle_radians = math.radians(angle_deg)
            vectors.append(
                (
                    float(speed_px_per_hour),
                    float(angle_deg),
                    float(speed_px_per_hour * math.cos(angle_radians)),
                    float(speed_px_per_hour * math.sin(angle_radians)),
                )
            )
    return vectors


def _combine_synthetic_sweep_frames(frames: list[np.ndarray], *, combine_mode: str) -> np.ndarray:
    stacked_cube = np.stack(frames, axis=0)
    if combine_mode == "sigma_clipped_mean":
        clipped = sigma_clip(
            stacked_cube,
            sigma=3.0,
            axis=0,
            masked=True,
            cenfunc="median",
            stdfunc=mad_std,
            maxiters=5,
        )
        return np.asarray(clipped.mean(axis=0).filled(0.0), dtype=np.float32)
    return np.asarray(np.nanmean(stacked_cube, axis=0), dtype=np.float32)


def _write_synthetic_sweep_stack(
    stacked_data: np.ndarray,
    *,
    output_directory: Path | None,
    reference_path: Path,
    vector_index: int,
    speed_px_per_hour: float,
    angle_deg: float,
    combine_mode: str,
    direction_focus_mode: str,
    direction_focus_half_width_deg: float,
) -> None:
    if output_directory is None:
        return
    output_directory.mkdir(parents=True, exist_ok=True)
    file_path = output_directory / (
        f"{_safe_filename_token(reference_path.stem)}"
        f"_discover_sweep_v{int(vector_index):04d}"
        f"_speed_{_float_filename_token(speed_px_per_hour, decimals=1)}pxph"
        f"_angle_{_float_filename_token(angle_deg, decimals=1)}deg"
        f"_{_safe_filename_token(direction_focus_mode)}"
        f"_width_{_float_filename_token(direction_focus_half_width_deg, decimals=1)}deg"
        f"_{_safe_filename_token(combine_mode)}.fits"
    )
    header = fits.Header()
    header["SWPVEC"] = int(vector_index)
    header["SWPSPD"] = float(speed_px_per_hour)
    header["SWPANG"] = float(angle_deg)
    header["SWPFOCUS"] = str(direction_focus_mode)
    header["SWPWIDTH"] = float(direction_focus_half_width_deg)
    header["SWPCOMB"] = str(combine_mode)
    fits.PrimaryHDU(data=np.asarray(stacked_data, dtype=np.float32), header=header).writeto(file_path, overwrite=True)


def _synthetic_sweep_settings_for_batch(
    settings: _SyntheticSweepSettings | None,
    *,
    batch_index: int,
    batch_count: int,
) -> _SyntheticSweepSettings | None:
    if settings is None:
        return None
    if batch_count <= 1 or settings.output_directory is None:
        return settings
    return replace(
        settings,
        output_directory=settings.output_directory / f"batch_{int(batch_index):02d}",
    )


def _safe_filename_token(value: object) -> str:
    text = "".join(character if str(character).isalnum() else "_" for character in str(value or "value").strip())
    collapsed = "_".join(part for part in text.split("_") if part)
    return collapsed or "value"


def _float_filename_token(value: float, *, decimals: int) -> str:
    return f"{float(value):.{decimals}f}".replace("-", "m").replace(".", "p")


@lru_cache(maxsize=8)
def _shift_sampling_grids(height: int, width: int) -> tuple[np.ndarray, np.ndarray]:
    return np.indices((height, width), dtype=float)


def _shift_image_with_zero_fill(
    image_data: np.ndarray,
    shift_x_px: float,
    shift_y_px: float,
    *,
    sample_x_grid: np.ndarray | None = None,
    sample_y_grid: np.ndarray | None = None,
) -> np.ndarray:
    if sample_x_grid is None or sample_y_grid is None or sample_x_grid.shape != image_data.shape or sample_y_grid.shape != image_data.shape:
        sample_y_grid, sample_x_grid = _shift_sampling_grids(*image_data.shape)
    return _bilinear_shift_sample(image_data, sample_x_grid + float(shift_x_px), sample_y_grid + float(shift_y_px))


def _bilinear_shift_sample(image_data: np.ndarray, sample_x: np.ndarray, sample_y: np.ndarray) -> np.ndarray:
    sample_x, sample_y = np.broadcast_arrays(sample_x, sample_y)
    height, width = image_data.shape
    x0 = np.floor(sample_x).astype(int)
    y0 = np.floor(sample_y).astype(int)
    x1 = x0 + 1
    y1 = y0 + 1
    valid = (x0 >= 0) & (y0 >= 0) & (x1 < width) & (y1 < height)
    shifted = np.zeros(sample_x.shape, dtype=np.float32)
    if not np.any(valid):
        return shifted
    x_fraction = sample_x - x0
    y_fraction = sample_y - y0
    top_left = image_data[y0[valid], x0[valid]]
    top_right = image_data[y0[valid], x1[valid]]
    bottom_left = image_data[y1[valid], x0[valid]]
    bottom_right = image_data[y1[valid], x1[valid]]
    top = top_left * (1.0 - x_fraction[valid]) + top_right * x_fraction[valid]
    bottom = bottom_left * (1.0 - x_fraction[valid]) + bottom_right * x_fraction[valid]
    shifted[valid] = np.asarray(top * (1.0 - y_fraction[valid]) + bottom * y_fraction[valid], dtype=np.float32)
    return shifted


def _world_coordinates_for_detection(frame_wcs: object, x_value: float, y_value: float) -> tuple[float | None, float | None]:
    pixel_to_world_values = getattr(frame_wcs, "pixel_to_world_values", None)
    if pixel_to_world_values is None:
        return None, None
    try:
        ra_deg, dec_deg = pixel_to_world_values(x_value, y_value)
    except Exception:
        return None, None
    if not (np.isfinite(ra_deg) and np.isfinite(dec_deg)):
        return None, None
    return float(ra_deg), float(dec_deg)


def _detect_synthetic_sweep_stack_peaks(
    stacked_data: np.ndarray,
    *,
    source_path: Path,
    observation_time: datetime,
    min_stacked_snr: float,
    detection_sigma: float,
    detection_fwhm: float,
    edge_margin_px: int,
) -> list[_ResidualDetection]:
    rows = _detect_residual_sources(
        stacked_data,
        source_path=source_path,
        observation_time=observation_time,
        frame_index=0,
        next_detection_id=1,
        excluded_positions=(),
        residual_min_snr=min_stacked_snr,
        residual_max_snr=0.0,
        detection_sigma=detection_sigma,
        detection_fwhm=detection_fwhm,
        max_residuals_per_frame=_DISCOVERY_SYNTHETIC_SWEEP_MAX_DETECTIONS_PER_VECTOR,
        edge_margin_px=edge_margin_px,
        detector_mode=_DISCOVERY_DETECTOR_MODE_POINT,
        streak_min_area_px=_DISCOVERY_STREAK_MIN_AREA_PX,
        streak_min_elongation=_DISCOVERY_STREAK_MIN_ELONGATION,
    )
    if rows:
        return rows

    finite = stacked_data[np.isfinite(stacked_data)]
    if finite.size == 0:
        return []
    background = float(np.nanmedian(finite))
    mad = float(np.nanmedian(np.abs(finite - background)))
    sigma = max(1e-6, 1.4826 * mad, float(np.nanstd(finite)))
    threshold_value = background + (max(0.1, float(min_stacked_snr)) * sigma)
    height, width = stacked_data.shape
    candidates: list[_ResidualDetection] = []
    for y_value in range(max(1, edge_margin_px), min(height - 1, height - edge_margin_px)):
        for x_value in range(max(1, edge_margin_px), min(width - 1, width - edge_margin_px)):
            peak_value = float(stacked_data[y_value, x_value])
            if not np.isfinite(peak_value) or peak_value < threshold_value:
                continue
            local_patch = stacked_data[y_value - 1:y_value + 2, x_value - 1:x_value + 2]
            if local_patch.size == 0 or peak_value < float(np.nanmax(local_patch)):
                continue
            candidates.append(
                _ResidualDetection(
                    detection_id=len(candidates) + 1,
                    frame_index=0,
                    source_path=source_path,
                    observation_time=observation_time,
                    x=float(x_value),
                    y=float(y_value),
                    peak_value=peak_value,
                    local_snr=float((peak_value - background) / sigma),
                )
            )
    return _merge_residual_detections(
        candidates,
        merge_radius_px=max(1.5, float(detection_fwhm)),
        max_residuals_per_frame=_DISCOVERY_SYNTHETIC_SWEEP_MAX_DETECTIONS_PER_VECTOR,
    )


def _resolved_search_workers(total_frame_count: int, configured_workers: int) -> int:
    if total_frame_count <= 0:
        return 1
    cpu_count = os.cpu_count() or 1
    if configured_workers > 0:
        return max(1, min(total_frame_count, cpu_count, configured_workers))
    return max(1, min(total_frame_count, max(1, cpu_count - 1)))


def _resolved_synthetic_sweep_workers(vector_count: int, configured_workers: int) -> int:
    if vector_count <= 0:
        return 1
    cpu_count = os.cpu_count() or 1
    if configured_workers > 0:
        return max(1, min(vector_count, cpu_count, configured_workers))
    return max(1, min(vector_count, max(1, cpu_count - 1)))


def _iter_completed_futures(
    executor: ThreadPoolExecutor,
    futures: dict[object, object],
    *,
    cancel_callback: Callable[[], bool] | None,
    cancellation_message: str,
):
    pending = set(futures)
    while pending:
        _raise_if_cancelled(cancel_callback, cancellation_message)
        completed, pending = wait(pending, timeout=0.05, return_when=FIRST_COMPLETED)
        if not completed:
            continue
        for future in completed:
            yield future


def _prepare_frames_for_search(
    frame_paths: list[Path],
    *,
    frame_metadata: dict[str, ObservationMetadata],
    resolved_reference_path: Path,
    reference_wcs,
    target_shape: tuple[int, int],
    assume_aligned: bool,
    worker_count: int,
    binning_factor: int,
    working_cache_dir: Path | None,
    progress_callback: Callable[[str], None] | None,
    cancel_callback: Callable[[], bool] | None,
    cancellation_message: str,
) -> list[_PreparedSearchFrame]:
    if worker_count <= 1 or len(frame_paths) <= 1:
        prepared_frames: list[_PreparedSearchFrame] = []
        for index, frame_path in enumerate(frame_paths):
            _raise_if_cancelled(cancel_callback, cancellation_message)
            prepared_frame = _prepare_single_frame_for_search(
                frame_path,
                frame_index=index,
                frame_metadata=frame_metadata,
                resolved_reference_path=resolved_reference_path,
                reference_wcs=reference_wcs,
                target_shape=target_shape,
                assume_aligned=assume_aligned,
                binning_factor=binning_factor,
                working_cache_dir=working_cache_dir,
            )
            prepared_frames.append(prepared_frame)
            _emit_progress(progress_callback, f"[{index + 1}/{len(frame_paths)}] Prepared {frame_path.name} for residual search.")
        return prepared_frames

    prepared_frames_by_index: list[_PreparedSearchFrame | None] = [None] * len(frame_paths)
    executor = ThreadPoolExecutor(max_workers=worker_count)
    try:
        futures = {
            executor.submit(
                _prepare_single_frame_for_search,
                frame_path,
                frame_index=index,
                frame_metadata=frame_metadata,
                resolved_reference_path=resolved_reference_path,
                reference_wcs=reference_wcs,
                target_shape=target_shape,
                assume_aligned=assume_aligned,
                binning_factor=binning_factor,
                working_cache_dir=working_cache_dir,
            ): frame_path
            for index, frame_path in enumerate(frame_paths)
        }
        completed = 0
        for future in _iter_completed_futures(
            executor,
            futures,
            cancel_callback=cancel_callback,
            cancellation_message=cancellation_message,
        ):
            prepared_frame = future.result()
            prepared_frames_by_index[prepared_frame.frame_index] = prepared_frame
            completed += 1
            _emit_progress(progress_callback, f"[{completed}/{len(frame_paths)}] Prepared {prepared_frame.frame_path.name} for residual search.")
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return [item for item in prepared_frames_by_index if item is not None]


def _prepare_single_frame_for_search(
    frame_path: Path,
    *,
    frame_index: int,
    frame_metadata: dict[str, ObservationMetadata],
    resolved_reference_path: Path,
    reference_wcs,
    target_shape: tuple[int, int],
    assume_aligned: bool,
    binning_factor: int,
    working_cache_dir: Path | None,
) -> _PreparedSearchFrame:
    metadata = frame_metadata.get(str(frame_path.resolve()))
    observation_time = _frame_midpoint_time(metadata)
    if observation_time is None:
        raise ValueError(f"Frame {frame_path.name} is missing an observation timestamp required for moving-object search.")
    source_header, _source_width, _source_height = read_header_and_shape(frame_path)
    source_wcs, source_reasons = _validated_celestial_wcs(frame_path, source_header)
    if source_wcs is None:
        reason_text = source_reasons[0] if source_reasons else "No usable celestial WCS was found."
        raise ValueError(f"Frame {frame_path.name} is not plate-solved: {reason_text}")
    source_data = np.asarray(read_image_data(frame_path), dtype=np.float32)
    if assume_aligned:
        aligned = np.asarray(_collapse_image_plane(source_data), dtype=np.float32)
        if aligned.shape != target_shape:
            raise ValueError(
                "The selected frames were marked as already aligned, but at least one frame does not match the reference dimensions. "
                "Choose No in the alignment prompt for mixed or unaligned frames."
            )
        frame_wcs = source_wcs
    else:
        if frame_path.resolve() == resolved_reference_path.resolve():
            aligned = np.asarray(_collapse_image_plane(source_data), dtype=np.float32)
            if aligned.shape != target_shape:
                raise ValueError("Reference frame dimensions do not match the target alignment grid.")
        else:
            aligned = np.asarray(_collapse_image_plane(_reproject_image_data(source_data, source_wcs, reference_wcs, target_shape)), dtype=np.float32)
        frame_wcs = reference_wcs
    normalized_binning_factor = _normalize_discovery_binning_factor(binning_factor)
    if normalized_binning_factor > 1:
        aligned = _bin_image_plane(aligned, normalized_binning_factor)
        frame_wcs = _BinnedFrameWcs(frame_wcs, normalized_binning_factor)
    cached_path = _write_discovery_cached_frame(working_cache_dir, frame_index, frame_path, aligned, normalized_binning_factor)
    return _PreparedSearchFrame(
        frame_index=frame_index,
        frame_path=frame_path,
        observation_time=observation_time,
        aligned_frame=_clean_image_plane(aligned),
        frame_wcs=frame_wcs,
        cached_path=cached_path,
    )


def _scan_frames_for_residual_detections(
    frame_paths: list[Path],
    *,
    frame_times: list[datetime],
    frame_pixel_wcs: list[object],
    aligned_frames: list[np.ndarray],
    background_model: np.ndarray,
    known_positions: dict[int, tuple[tuple[float, float], ...]],
    exclude_known_positions: bool,
    residual_min_snr: float,
    residual_max_snr: float,
    detection_sigma: float,
    detection_fwhm: float,
    max_residuals_per_frame: int,
    edge_margin_px: int,
    detector_mode: str,
    streak_min_area_px: int,
    streak_min_elongation: float,
    worker_count: int,
    progress_callback: Callable[[str], None] | None,
    cancel_callback: Callable[[], bool] | None,
    cancellation_message: str,
) -> list[list[_ResidualDetection]]:
    if worker_count <= 1 or len(frame_paths) <= 1:
        residual_detections_by_frame: list[list[_ResidualDetection]] = []
        for frame_index, (frame_path, observation_time, aligned_frame, frame_wcs) in enumerate(zip(frame_paths, frame_times, aligned_frames, frame_pixel_wcs)):
            _raise_if_cancelled(cancel_callback, cancellation_message)
            _frame_index, residual_detections = _scan_single_frame_for_residuals(
                frame_index,
                frame_path,
                observation_time,
                aligned_frame,
                frame_wcs,
                background_model,
                known_positions.get(frame_index, ()) if exclude_known_positions else (),
                residual_min_snr,
                residual_max_snr,
                detection_sigma,
                detection_fwhm,
                max_residuals_per_frame,
                edge_margin_px,
                detector_mode,
                streak_min_area_px,
                streak_min_elongation,
            )
            residual_detections_by_frame.append(residual_detections)
            _emit_progress(progress_callback, f"[{frame_index + 1}/{len(frame_paths)}] Scanned residuals in {frame_path.name}.")
        _renumber_residual_detections(residual_detections_by_frame)
        return residual_detections_by_frame

    residuals_by_index: list[list[_ResidualDetection] | None] = [None] * len(frame_paths)
    executor = ThreadPoolExecutor(max_workers=worker_count)
    try:
        futures = {
            executor.submit(
                _scan_single_frame_for_residuals,
                frame_index,
                frame_path,
                observation_time,
                aligned_frame,
                frame_pixel_wcs[frame_index],
                background_model,
                known_positions.get(frame_index, ()) if exclude_known_positions else (),
                residual_min_snr,
                residual_max_snr,
                detection_sigma,
                detection_fwhm,
                max_residuals_per_frame,
                edge_margin_px,
                detector_mode,
                streak_min_area_px,
                streak_min_elongation,
            ): frame_index
            for frame_index, (frame_path, observation_time, aligned_frame) in enumerate(zip(frame_paths, frame_times, aligned_frames))
        }
        completed = 0
        for future in _iter_completed_futures(
            executor,
            futures,
            cancel_callback=cancel_callback,
            cancellation_message=cancellation_message,
        ):
            frame_index, residual_detections = future.result()
            residuals_by_index[frame_index] = residual_detections
            completed += 1
            _emit_progress(progress_callback, f"[{completed}/{len(frame_paths)}] Scanned residuals in {frame_paths[frame_index].name}.")
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    residual_detections_by_frame = [item if item is not None else [] for item in residuals_by_index]
    _renumber_residual_detections(residual_detections_by_frame)
    return residual_detections_by_frame


def _scan_single_frame_for_residuals(
    frame_index: int,
    frame_path: Path,
    observation_time: datetime,
    aligned_frame: np.ndarray,
    frame_wcs,
    background_model: np.ndarray,
    excluded_positions: tuple[tuple[float, float], ...],
    residual_min_snr: float,
    residual_max_snr: float,
    detection_sigma: float,
    detection_fwhm: float,
    max_residuals_per_frame: int,
    edge_margin_px: int,
    detector_mode: str,
    streak_min_area_px: int,
    streak_min_elongation: float,
) -> tuple[int, list[_ResidualDetection]]:
    residual_detections = _detect_residual_sources(
        aligned_frame - background_model,
        source_path=frame_path,
        observation_time=observation_time,
        frame_index=frame_index,
        next_detection_id=1,
        excluded_positions=excluded_positions,
        residual_min_snr=residual_min_snr,
        residual_max_snr=residual_max_snr,
        detection_sigma=detection_sigma,
        detection_fwhm=detection_fwhm,
        max_residuals_per_frame=max_residuals_per_frame,
        edge_margin_px=edge_margin_px,
        detector_mode=detector_mode,
        streak_min_area_px=streak_min_area_px,
        streak_min_elongation=streak_min_elongation,
    )
    _assign_sky_coordinates_to_detections(residual_detections, frame_wcs)
    return frame_index, residual_detections


def _assign_sky_coordinates_to_detections(residual_detections: list[_ResidualDetection], frame_wcs) -> None:
    if not residual_detections:
        return
    pixel_to_world_values = getattr(frame_wcs, "pixel_to_world_values", None)
    if pixel_to_world_values is None:
        return
    for detection in residual_detections:
        try:
            ra_deg, dec_deg = pixel_to_world_values(detection.x, detection.y)
        except Exception:
            continue
        if not (np.isfinite(ra_deg) and np.isfinite(dec_deg)):
            continue
        detection.ra_deg = float(ra_deg)
        detection.dec_deg = float(dec_deg)


def _renumber_residual_detections(residual_detections_by_frame: list[list[_ResidualDetection]]) -> None:
    detection_id = 1
    for frame_detections in residual_detections_by_frame:
        for detection in frame_detections:
            detection.detection_id = detection_id
            detection_id += 1


def _frame_midpoint_time(metadata: ObservationMetadata | None) -> datetime | None:
    if metadata is None or metadata.date_obs is None:
        return None
    timestamp = metadata.date_obs if metadata.date_obs.tzinfo is not None else metadata.date_obs.replace(tzinfo=UTC)
    if metadata.exposure_seconds is None:
        return timestamp
    return timestamp + timedelta(seconds=max(0.0, float(metadata.exposure_seconds)) / 2.0)


def _normalize_discovery_motion_prior_bias(value: object) -> str:
    normalized_value = str(value or _DISCOVERY_MOTION_PRIOR_BIAS_BALANCED).strip().lower()
    if normalized_value not in _DISCOVERY_MOTION_PRIOR_BIAS_MODES:
        return _DISCOVERY_MOTION_PRIOR_BIAS_BALANCED
    return normalized_value


def _discovery_motion_prior_arcsec_per_hour_range(motion_prior_bias: str) -> tuple[float, float]:
    normalized_bias = _normalize_discovery_motion_prior_bias(motion_prior_bias)
    if normalized_bias == _DISCOVERY_MOTION_PRIOR_BIAS_MAIN_BELT:
        return (8.0, 90.0)
    if normalized_bias == _DISCOVERY_MOTION_PRIOR_BIAS_NEAR_EARTH:
        return (20.0, 360.0)
    return (12.0, 180.0)


def _estimate_discovery_motion_range(
    frame_paths: list[Path],
    *,
    frame_metadata: dict[str, ObservationMetadata],
    pixel_scale_arcsec_per_pixel: float | None,
    motion_prior_bias: str = _DISCOVERY_MOTION_PRIOR_BIAS_BALANCED,
) -> _DiscoveryMotionRangeEstimate | None:
    if pixel_scale_arcsec_per_pixel is None or not np.isfinite(pixel_scale_arcsec_per_pixel) or pixel_scale_arcsec_per_pixel <= 0:
        return None
    exposure_seconds: list[float] = []
    for frame_path in frame_paths:
        metadata = frame_metadata.get(str(frame_path.resolve()))
        if metadata is None or metadata.exposure_seconds is None:
            continue
        exposure_value = float(metadata.exposure_seconds)
        if exposure_value > 0 and np.isfinite(exposure_value):
            exposure_seconds.append(exposure_value)
    if not exposure_seconds:
        return None
    median_exposure_seconds = float(np.median(np.asarray(exposure_seconds, dtype=float)))
    if median_exposure_seconds <= 0:
        return None
    min_motion_arcsec_per_hour, max_motion_arcsec_per_hour = _discovery_motion_prior_arcsec_per_hour_range(motion_prior_bias)
    min_motion_px_per_hour = float(min_motion_arcsec_per_hour / pixel_scale_arcsec_per_pixel)
    literature_max_motion_px_per_hour = float(max_motion_arcsec_per_hour / pixel_scale_arcsec_per_pixel)
    trail_limited_max_motion_px_per_hour = float((_DISCOVERY_MAX_TRAIL_PIXELS_PER_EXPOSURE * 3600.0) / median_exposure_seconds)
    max_motion_px_per_hour = min(literature_max_motion_px_per_hour, trail_limited_max_motion_px_per_hour)
    if max_motion_px_per_hour < min_motion_px_per_hour:
        max_motion_px_per_hour = min_motion_px_per_hour
    return _DiscoveryMotionRangeEstimate(
        min_motion_px_per_hour=min_motion_px_per_hour,
        max_motion_px_per_hour=max_motion_px_per_hour,
        pixel_scale_arcsec_per_pixel=float(pixel_scale_arcsec_per_pixel),
        median_exposure_seconds=median_exposure_seconds,
    )


def _apply_motion_range_to_synthetic_sweep_settings(
    settings: _SyntheticSweepSettings | None,
    *,
    motion_range_estimate: _DiscoveryMotionRangeEstimate | None,
) -> _SyntheticSweepSettings | None:
    if settings is None or motion_range_estimate is None:
        return settings
    capped_max_motion_px_per_hour = min(float(settings.max_motion_px_per_hour), motion_range_estimate.max_motion_px_per_hour)
    return replace(
        settings,
        max_motion_px_per_hour=capped_max_motion_px_per_hour,
        motion_step_px_per_hour=min(float(settings.motion_step_px_per_hour), capped_max_motion_px_per_hour),
    )


def _collapse_image_plane(image_data: np.ndarray) -> np.ndarray:
    data = np.asarray(image_data, dtype=float)
    if data.ndim == 2:
        return data
    if data.ndim == 3 and data.shape[-1] in {1, 3, 4}:
        return np.nanmean(data, axis=-1)
    if data.ndim == 3 and data.shape[0] in {1, 3, 4}:
        return np.nanmean(np.moveaxis(data, 0, -1), axis=-1)
    raise ValueError("Discovery currently supports only 2D or simple RGB/RGBA image data.")


def _normalize_discovery_binning_factor(binning_factor: int | float | None) -> int:
    try:
        normalized = int(binning_factor or 1)
    except (TypeError, ValueError):
        return 1
    if normalized not in _DISCOVERY_BINNING_FACTORS:
        return 1
    return normalized


def _bin_image_plane(image_data: np.ndarray, binning_factor: int) -> np.ndarray:
    factor = _normalize_discovery_binning_factor(binning_factor)
    data = np.asarray(image_data, dtype=np.float32)
    if factor <= 1:
        return data
    height, width = data.shape[:2]
    binned_height = height // factor
    binned_width = width // factor
    if binned_height < 1 or binned_width < 1:
        raise ValueError("Discover binning would collapse the working image below 1 pixel in at least one dimension.")
    trimmed = data[: binned_height * factor, : binned_width * factor]
    reshaped = trimmed.reshape(binned_height, factor, binned_width, factor)
    return np.nansum(reshaped, axis=(1, 3), dtype=np.float32)


def _source_to_binned_pixel_coordinate(value: float, binning_factor: int) -> float:
    return ((float(value) + 0.5) / float(max(1, int(binning_factor)))) - 0.5


def _binned_to_source_pixel_coordinate(value: float, binning_factor: int) -> float:
    return ((float(value) + 0.5) * float(max(1, int(binning_factor)))) - 0.5


def _write_discovery_cached_frame(
    working_cache_dir: Path | None,
    frame_index: int,
    frame_path: Path,
    aligned_frame: np.ndarray,
    binning_factor: int,
) -> Path | None:
    if working_cache_dir is None:
        return None
    working_cache_dir.mkdir(parents=True, exist_ok=True)
    output_path = working_cache_dir / f"{frame_index:03d}_{frame_path.stem}_bin{binning_factor}x{binning_factor}.npz"
    np.savez_compressed(output_path, image=np.asarray(aligned_frame, dtype=np.float32))
    return output_path


def _clean_image_plane(image_data: np.ndarray) -> np.ndarray:
    finite_pixels = image_data[np.isfinite(image_data)]
    if finite_pixels.size == 0:
        raise ValueError("Discovery image contains no finite pixel values.")
    return np.nan_to_num(
        np.asarray(image_data, dtype=np.float32),
        nan=float(np.median(finite_pixels)),
        posinf=float(np.max(finite_pixels)),
        neginf=float(np.min(finite_pixels)),
    )


def _reference_pixel_scale_arcsec(reference_wcs, known_detection_result: SolarSystemDetectionResult | None) -> float | None:
    if known_detection_result is not None and known_detection_result.pixel_scale_arcsec_per_pixel is not None:
        return float(known_detection_result.pixel_scale_arcsec_per_pixel)
    try:
        scales = proj_plane_pixel_scales(reference_wcs)
    except Exception:
        return None
    if scales.size < 2 or not np.all(np.isfinite(scales[:2])):
        return None
    return float(np.mean(scales[:2]) * 3600.0)


def _known_positions_by_frame(
    frame_times: list[datetime],
    *,
    frame_pixel_wcs: list[object],
    known_detection_result: SolarSystemDetectionResult | None,
) -> dict[int, tuple[tuple[float, float], ...]]:
    if known_detection_result is None or not known_detection_result.detections:
        return {}
    positions_by_frame: dict[int, tuple[tuple[float, float], ...]] = {}
    for frame_index, (observation_time, frame_wcs) in enumerate(zip(frame_times, frame_pixel_wcs)):
        positions: list[tuple[float, float]] = []
        for detection in known_detection_result.detections:
            predicted_ra_deg, predicted_dec_deg = predict_detection_coordinates(
                detection,
                reference_observation_time=known_detection_result.prediction_time,
                observation_time=observation_time,
            )
            predicted_x, predicted_y = frame_wcs.world_to_pixel_values(predicted_ra_deg, predicted_dec_deg)
            if np.isfinite(predicted_x) and np.isfinite(predicted_y):
                positions.append((float(predicted_x), float(predicted_y)))
        positions_by_frame[frame_index] = tuple(positions)
    return positions_by_frame


def _detect_residual_sources(
    residual_image: np.ndarray,
    *,
    source_path: Path,
    observation_time: datetime,
    frame_index: int,
    next_detection_id: int,
    excluded_positions: tuple[tuple[float, float], ...],
    residual_min_snr: float = 0.0,
    residual_max_snr: float = 0.0,
    detection_sigma: float = _DISCOVERY_DETECTION_SIGMA,
    detection_fwhm: float = _DISCOVERY_DETECTION_FWHM,
    max_residuals_per_frame: int = _DISCOVERY_MAX_RESIDUALS_PER_FRAME,
    edge_margin_px: int = _DISCOVERY_EDGE_MARGIN_PX,
    detector_mode: str = _DISCOVERY_DETECTOR_MODE_HYBRID,
    streak_min_area_px: int = _DISCOVERY_STREAK_MIN_AREA_PX,
    streak_min_elongation: float = _DISCOVERY_STREAK_MIN_ELONGATION,
) -> list[_ResidualDetection]:
    _, background_median, background_std = sigma_clipped_stats(residual_image, sigma=3.0, maxiters=5)
    if not np.isfinite(background_std) or background_std <= 0:
        return []
    detection_image = np.asarray(residual_image, dtype=float) - float(background_median)
    normalized_detection_sigma = _normalize_discovery_detection_sigma(detection_sigma)
    normalized_detection_fwhm = _normalize_discovery_detection_fwhm(detection_fwhm)
    normalized_max_residuals_per_frame = _normalize_discovery_max_residuals_per_frame(max_residuals_per_frame)
    normalized_edge_margin_px = _normalize_discovery_edge_margin_px(edge_margin_px)
    normalized_detector_mode = _normalize_discovery_detector_mode(detector_mode)
    normalized_streak_min_area_px = _normalize_discovery_streak_min_area_px(streak_min_area_px)
    normalized_streak_min_elongation = _normalize_discovery_streak_min_elongation(streak_min_elongation)
    threshold_value = max(float(background_std) * normalized_detection_sigma, 1e-6)

    rows: list[_ResidualDetection] = []
    if normalized_detector_mode in {_DISCOVERY_DETECTOR_MODE_POINT, _DISCOVERY_DETECTOR_MODE_HYBRID}:
        rows.extend(
            _detect_point_residual_sources(
                detection_image,
                source_path=source_path,
                observation_time=observation_time,
                frame_index=frame_index,
                background_std=float(background_std),
                threshold_value=threshold_value,
                excluded_positions=excluded_positions,
                residual_min_snr=residual_min_snr,
                residual_max_snr=residual_max_snr,
                detection_fwhm=normalized_detection_fwhm,
                edge_margin_px=normalized_edge_margin_px,
            )
        )
    if normalized_detector_mode in {_DISCOVERY_DETECTOR_MODE_STREAK, _DISCOVERY_DETECTOR_MODE_HYBRID}:
        rows.extend(
            _detect_streak_residual_sources(
                detection_image,
                source_path=source_path,
                observation_time=observation_time,
                frame_index=frame_index,
                background_std=float(background_std),
                threshold_value=threshold_value,
                excluded_positions=excluded_positions,
                residual_min_snr=residual_min_snr,
                residual_max_snr=residual_max_snr,
                detection_fwhm=normalized_detection_fwhm,
                edge_margin_px=normalized_edge_margin_px,
                streak_min_area_px=normalized_streak_min_area_px,
                streak_min_elongation=normalized_streak_min_elongation,
            )
        )

    merged_rows = _merge_residual_detections(
        rows,
        merge_radius_px=max(1.5, normalized_detection_fwhm),
        max_residuals_per_frame=normalized_max_residuals_per_frame,
    )
    for detection_id, detection in enumerate(merged_rows, start=next_detection_id):
        detection.detection_id = detection_id
    return merged_rows


def _detect_point_residual_sources(
    detection_image: np.ndarray,
    *,
    source_path: Path,
    observation_time: datetime,
    frame_index: int,
    background_std: float,
    threshold_value: float,
    excluded_positions: tuple[tuple[float, float], ...],
    residual_min_snr: float,
    residual_max_snr: float,
    detection_fwhm: float,
    edge_margin_px: int,
) -> list[_ResidualDetection]:
    finder = DAOStarFinder(
        fwhm=detection_fwhm,
        threshold=threshold_value,
        exclude_border=True,
    )
    detections = finder(detection_image)
    if detections is None or len(detections) == 0:
        return []

    rows: list[_ResidualDetection] = []
    height, width = detection_image.shape
    for row in detections:
        x = _coerce_float(row.get("xcentroid"))
        y = _coerce_float(row.get("ycentroid"))
        peak_value = _coerce_float(row.get("peak"))
        if x is None or y is None or peak_value is None:
            continue
        if not (edge_margin_px <= x < width - edge_margin_px and edge_margin_px <= y < height - edge_margin_px):
            continue
        if peak_value <= 0:
            continue
        local_snr = float(peak_value / background_std)
        if residual_min_snr > 0.0 and local_snr < residual_min_snr:
            continue
        if residual_max_snr > 0.0 and local_snr > residual_max_snr:
            continue
        if any(math.hypot(x - known_x, y - known_y) <= _DISCOVERY_KNOWN_EXCLUSION_RADIUS_PX for known_x, known_y in excluded_positions):
            continue
        rows.append(
            _ResidualDetection(
                detection_id=0,
                frame_index=frame_index,
                source_path=source_path,
                observation_time=observation_time,
                x=float(x),
                y=float(y),
                peak_value=float(peak_value),
                local_snr=local_snr,
            )
        )
    return rows


def _detect_streak_residual_sources(
    detection_image: np.ndarray,
    *,
    source_path: Path,
    observation_time: datetime,
    frame_index: int,
    background_std: float,
    threshold_value: float,
    excluded_positions: tuple[tuple[float, float], ...],
    residual_min_snr: float,
    residual_max_snr: float,
    detection_fwhm: float,
    edge_margin_px: int,
    streak_min_area_px: int,
    streak_min_elongation: float,
) -> list[_ResidualDetection]:
    positive_mask = np.isfinite(detection_image) & (detection_image >= threshold_value)
    if not np.any(positive_mask):
        return []

    rows: list[_ResidualDetection] = []
    height, width = detection_image.shape
    for component in _connected_positive_components(positive_mask):
        if len(component) < streak_min_area_px:
            continue
        y_indices = np.asarray([pixel[0] for pixel in component], dtype=float)
        x_indices = np.asarray([pixel[1] for pixel in component], dtype=float)
        weights = np.asarray([detection_image[int(y_value), int(x_value)] for y_value, x_value in component], dtype=float)
        weights = np.where(np.isfinite(weights), weights, 0.0)
        weights = np.where(weights > 0.0, weights, 0.0)
        total_weight = float(np.sum(weights))
        if total_weight <= 0.0:
            continue

        x = float(np.sum(x_indices * weights) / total_weight)
        y = float(np.sum(y_indices * weights) / total_weight)
        if not (edge_margin_px <= x < width - edge_margin_px and edge_margin_px <= y < height - edge_margin_px):
            continue
        peak_value = float(np.max(weights))
        if peak_value <= 0.0:
            continue

        centered_x = x_indices - x
        centered_y = y_indices - y
        covariance = np.asarray(
            [
                [float(np.sum(weights * centered_x * centered_x) / total_weight), float(np.sum(weights * centered_x * centered_y) / total_weight)],
                [float(np.sum(weights * centered_x * centered_y) / total_weight), float(np.sum(weights * centered_y * centered_y) / total_weight)],
            ],
            dtype=float,
        )
        eigenvalues = np.linalg.eigvalsh(covariance)
        major_sigma = math.sqrt(max(float(eigenvalues[-1]), 0.0))
        minor_sigma = math.sqrt(max(float(eigenvalues[0]), 0.0))
        major_fwhm = 2.354820045 * major_sigma
        minor_fwhm = 2.354820045 * minor_sigma
        elongation = math.inf if minor_fwhm <= 1e-6 else float(major_fwhm / minor_fwhm)
        if major_fwhm < max(2.0, detection_fwhm * 1.15):
            continue
        if elongation < streak_min_elongation:
            continue

        integrated_snr = float(total_weight / max(background_std * max(1.0, math.sqrt(float(len(component)))), 1e-6))
        local_snr = max(float(peak_value / background_std), integrated_snr)
        if residual_min_snr > 0.0 and local_snr < residual_min_snr:
            continue
        if residual_max_snr > 0.0 and local_snr > residual_max_snr:
            continue
        exclusion_radius_px = max(_DISCOVERY_KNOWN_EXCLUSION_RADIUS_PX, major_fwhm * 0.5)
        if any(math.hypot(x - known_x, y - known_y) <= exclusion_radius_px for known_x, known_y in excluded_positions):
            continue

        rows.append(
            _ResidualDetection(
                detection_id=0,
                frame_index=frame_index,
                source_path=source_path,
                observation_time=observation_time,
                x=x,
                y=y,
                peak_value=peak_value,
                local_snr=local_snr,
            )
        )
    return rows


def _connected_positive_components(mask: np.ndarray) -> list[list[tuple[int, int]]]:
    height, width = mask.shape
    visited = np.zeros(mask.shape, dtype=bool)
    components: list[list[tuple[int, int]]] = []
    for start_y in range(height):
        for start_x in range(width):
            if not bool(mask[start_y, start_x]) or bool(visited[start_y, start_x]):
                continue
            stack = [(start_y, start_x)]
            visited[start_y, start_x] = True
            component: list[tuple[int, int]] = []
            while stack:
                y_value, x_value = stack.pop()
                component.append((y_value, x_value))
                for neighbor_y in range(max(0, y_value - 1), min(height, y_value + 2)):
                    for neighbor_x in range(max(0, x_value - 1), min(width, x_value + 2)):
                        if bool(mask[neighbor_y, neighbor_x]) and not bool(visited[neighbor_y, neighbor_x]):
                            visited[neighbor_y, neighbor_x] = True
                            stack.append((neighbor_y, neighbor_x))
            components.append(component)
    return components


def _merge_residual_detections(
    detections: list[_ResidualDetection],
    *,
    merge_radius_px: float,
    max_residuals_per_frame: int,
) -> list[_ResidualDetection]:
    ordered = sorted(detections, key=lambda item: (-item.local_snr, -item.peak_value))
    merged: list[_ResidualDetection] = []
    for detection in ordered:
        if any(math.hypot(detection.x - existing.x, detection.y - existing.y) <= merge_radius_px for existing in merged):
            continue
        merged.append(detection)
        if len(merged) >= max_residuals_per_frame:
            break
    merged.sort(key=lambda item: (-item.local_snr, -item.peak_value))
    return merged


def _link_moving_candidates(
    residual_detections_by_frame: list[list[_ResidualDetection]],
    *,
    frame_times: list[datetime],
    pixel_scale_arcsec_per_pixel: float | None,
    minimum_candidate_frames: int,
    min_seed_displacement_px: float = _DISCOVERY_MIN_DISPLACEMENT_PX,
    min_motion_px_per_hour: float | None = None,
    max_motion_px_per_hour: float | None = None,
    cancel_callback: Callable[[], bool] | None = None,
    cancellation_message: str = "Discover cancelled.",
) -> list[MovingObjectCandidate]:
    frame_hours = np.asarray(
        [(timestamp - frame_times[0]).total_seconds() / 3600.0 for timestamp in frame_times],
        dtype=float,
    )
    candidate_map: dict[tuple[tuple[int, int], ...], MovingObjectCandidate] = {}
    frame_count = len(residual_detections_by_frame)
    for start_index in range(frame_count - 1):
        _raise_if_cancelled(cancel_callback, cancellation_message)
        max_end_index = min(frame_count, start_index + 1 + _DISCOVERY_MAX_SEED_FRAME_GAP)
        for end_index in range(start_index + 1, max_end_index):
            _raise_if_cancelled(cancel_callback, cancellation_message)
            delta_hours = frame_hours[end_index] - frame_hours[start_index]
            if delta_hours <= 0:
                continue
            for start_detection in residual_detections_by_frame[start_index]:
                for end_detection in residual_detections_by_frame[end_index]:
                    displacement_px = math.hypot(end_detection.x - start_detection.x, end_detection.y - start_detection.y)
                    if displacement_px < float(min_seed_displacement_px):
                        continue
                    motion_px_per_hour = float(displacement_px / delta_hours)
                    if min_motion_px_per_hour is not None and motion_px_per_hour < float(min_motion_px_per_hour) - 1e-6:
                        continue
                    if max_motion_px_per_hour is not None and motion_px_per_hour > float(max_motion_px_per_hour) + 1e-6:
                        continue
                    matched = _match_tracklet(
                        residual_detections_by_frame,
                        frame_hours=frame_hours,
                        start_detection=start_detection,
                        end_detection=end_detection,
                    )
                    if len(matched) < minimum_candidate_frames:
                        continue
                    candidate = _build_candidate(matched, frame_hours=frame_hours, pixel_scale_arcsec_per_pixel=pixel_scale_arcsec_per_pixel)
                    signature = tuple(sorted((detection.frame_index, detection.detection_id) for detection in matched))
                    current = candidate_map.get(signature)
                    if current is None or _candidate_rank(candidate) > _candidate_rank(current):
                        candidate_map[signature] = candidate
    return _rank_and_number_candidates(list(candidate_map.values()))


def _match_tracklet(
    residual_detections_by_frame: list[list[_ResidualDetection]],
    *,
    frame_hours: np.ndarray,
    start_detection: _ResidualDetection,
    end_detection: _ResidualDetection,
) -> list[_ResidualDetection]:
    intercept_x, velocity_x, intercept_y, velocity_y = _seed_tracklet_motion_model(
        frame_hours,
        start_detection=start_detection,
        end_detection=end_detection,
    )
    fixed_detections = {
        start_detection.frame_index: start_detection,
        end_detection.frame_index: end_detection,
    }
    previous_signature: tuple[tuple[int, int], ...] | None = None
    matched = _rematch_tracklet_detections(
        residual_detections_by_frame,
        frame_hours=frame_hours,
        fixed_detections=fixed_detections,
        intercept_x=intercept_x,
        velocity_x=velocity_x,
        intercept_y=intercept_y,
        velocity_y=velocity_y,
    )
    for _ in range(3):
        signature = tuple((detection.frame_index, detection.detection_id) for detection in matched)
        if signature == previous_signature:
            break
        previous_signature = signature
        intercept_x, velocity_x, intercept_y, velocity_y = _fit_tracklet_motion_model(
            matched,
            frame_hours=frame_hours,
            fallback_start_detection=start_detection,
            fallback_end_detection=end_detection,
        )
        matched = _rematch_tracklet_detections(
            residual_detections_by_frame,
            frame_hours=frame_hours,
            fixed_detections=fixed_detections,
            intercept_x=intercept_x,
            velocity_x=velocity_x,
            intercept_y=intercept_y,
            velocity_y=velocity_y,
        )
    matched.sort(key=lambda detection: detection.observation_time)
    return matched


def _seed_tracklet_motion_model(
    frame_hours: np.ndarray,
    *,
    start_detection: _ResidualDetection,
    end_detection: _ResidualDetection,
) -> tuple[float, float, float, float]:
    start_time_hours = float(frame_hours[start_detection.frame_index])
    end_time_hours = float(frame_hours[end_detection.frame_index])
    delta_hours = end_time_hours - start_time_hours
    if delta_hours <= 0:
        return float(start_detection.x), 0.0, float(start_detection.y), 0.0
    velocity_x = float((end_detection.x - start_detection.x) / delta_hours)
    velocity_y = float((end_detection.y - start_detection.y) / delta_hours)
    intercept_x = float(start_detection.x - (velocity_x * start_time_hours))
    intercept_y = float(start_detection.y - (velocity_y * start_time_hours))
    return intercept_x, velocity_x, intercept_y, velocity_y


def _fit_tracklet_motion_model(
    detections: list[_ResidualDetection],
    *,
    frame_hours: np.ndarray,
    fallback_start_detection: _ResidualDetection,
    fallback_end_detection: _ResidualDetection,
) -> tuple[float, float, float, float]:
    if len(detections) < 2:
        return _seed_tracklet_motion_model(
            frame_hours,
            start_detection=fallback_start_detection,
            end_detection=fallback_end_detection,
        )
    times = np.asarray([frame_hours[detection.frame_index] for detection in detections], dtype=float)
    design = np.column_stack((np.ones_like(times), times))
    x_values = np.asarray([detection.x for detection in detections], dtype=float)
    y_values = np.asarray([detection.y for detection in detections], dtype=float)
    x_fit = np.linalg.lstsq(design, x_values, rcond=None)[0]
    y_fit = np.linalg.lstsq(design, y_values, rcond=None)[0]
    return float(x_fit[0]), float(x_fit[1]), float(y_fit[0]), float(y_fit[1])


def _rematch_tracklet_detections(
    residual_detections_by_frame: list[list[_ResidualDetection]],
    *,
    frame_hours: np.ndarray,
    fixed_detections: dict[int, _ResidualDetection],
    intercept_x: float,
    velocity_x: float,
    intercept_y: float,
    velocity_y: float,
) -> list[_ResidualDetection]:
    matched: list[_ResidualDetection] = []
    for frame_index, frame_detections in enumerate(residual_detections_by_frame):
        fixed_detection = fixed_detections.get(frame_index)
        if fixed_detection is not None:
            matched.append(fixed_detection)
            continue
        if not frame_detections:
            continue
        predicted_x = float(intercept_x + (velocity_x * frame_hours[frame_index]))
        predicted_y = float(intercept_y + (velocity_y * frame_hours[frame_index]))
        nearest = min(
            frame_detections,
            key=lambda detection: math.hypot(detection.x - predicted_x, detection.y - predicted_y),
            default=None,
        )
        if nearest is None:
            continue
        if math.hypot(nearest.x - predicted_x, nearest.y - predicted_y) <= _DISCOVERY_MATCH_RADIUS_PX:
            matched.append(nearest)
    return matched


def _build_candidate(
    detections: list[_ResidualDetection],
    *,
    frame_hours: np.ndarray,
    pixel_scale_arcsec_per_pixel: float | None,
    discovery_method: str = _DISCOVERY_CANDIDATE_METHOD_RESIDUAL,
) -> MovingObjectCandidate:
    times = np.asarray([frame_hours[detection.frame_index] for detection in detections], dtype=float)
    x_values = np.asarray([detection.x for detection in detections], dtype=float)
    y_values = np.asarray([detection.y for detection in detections], dtype=float)
    design = np.column_stack((np.ones_like(times), times))
    x_fit = np.linalg.lstsq(design, x_values, rcond=None)[0]
    y_fit = np.linalg.lstsq(design, y_values, rcond=None)[0]
    fit_x = design @ x_fit
    fit_y = design @ y_fit
    residual_distances = np.sqrt(((fit_x - x_values) ** 2) + ((fit_y - y_values) ** 2))
    fit_rms_px = float(np.sqrt(np.mean(np.square(residual_distances))))
    max_deflection_px = float(np.max(residual_distances)) if residual_distances.size > 0 else 0.0
    start_time = float(np.min(times))
    end_time = float(np.max(times))
    start_x = float(x_fit[0] + (x_fit[1] * start_time))
    start_y = float(y_fit[0] + (y_fit[1] * start_time))
    end_x = float(x_fit[0] + (x_fit[1] * end_time))
    end_y = float(y_fit[0] + (y_fit[1] * end_time))
    motion_px_per_hour = float(math.hypot(float(x_fit[1]), float(y_fit[1])))
    motion_arcsec_per_hour = None if pixel_scale_arcsec_per_pixel is None else float(motion_px_per_hour * pixel_scale_arcsec_per_pixel)
    displacement_px = float(math.hypot(end_x - start_x, end_y - start_y))
    average_snr = float(np.mean([detection.local_snr for detection in detections]))
    peak_value = float(max(detection.peak_value for detection in detections))
    return MovingObjectCandidate(
        candidate_id="",
        frame_detections=tuple(
            MovingObjectCandidateDetection(
                source_path=detection.source_path,
                observation_time=detection.observation_time,
                frame_index=detection.frame_index,
                x=detection.x,
                y=detection.y,
                peak_value=detection.peak_value,
                local_snr=detection.local_snr,
                ra_deg=detection.ra_deg,
                dec_deg=detection.dec_deg,
            )
            for detection in detections
        ),
        average_snr=average_snr,
        peak_value=peak_value,
        fit_rms_px=fit_rms_px,
        motion_px_per_hour=motion_px_per_hour,
        motion_arcsec_per_hour=motion_arcsec_per_hour,
        displacement_px=displacement_px,
        start_x=start_x,
        start_y=start_y,
        end_x=end_x,
        end_y=end_y,
        summary_text="",
        max_deflection_px=max_deflection_px,
        discovery_method=discovery_method,
    )


def _associate_candidates_with_known_detections(
    candidates: list[MovingObjectCandidate],
    *,
    frame_times: list[datetime],
    frame_pixel_wcs: list[object],
    known_detection_result: SolarSystemDetectionResult,
    candidate_detections: list[SolarSystemDetection] | None = None,
    minimum_matched_frames: int = _DISCOVERY_MIN_FRAMES,
    estimated_magnitude_limit: float | None = None,
) -> tuple[list[RecoveredKnownMovingObject], list[MissedKnownMovingObject], list[MovingObjectCandidate]]:
    associated_detections = list(known_detection_result.detections if candidate_detections is None else candidate_detections)
    if not associated_detections:
        return [], [], list(candidates)

    pixel_scale_arcsec_per_pixel = known_detection_result.pixel_scale_arcsec_per_pixel

    prediction_paths = _known_prediction_paths_by_detection(
        associated_detections,
        frame_times=frame_times,
        frame_pixel_wcs=frame_pixel_wcs,
        known_detection_result=known_detection_result,
    )
    candidate_matches: list[tuple[int, float, float, float, int, int]] = []
    match_details: dict[tuple[int, int], tuple[int, float, float]] = {}
    for candidate_index, candidate in enumerate(candidates):
        for detection_index, detection in enumerate(associated_detections):
            path = prediction_paths[detection_index]
            offsets: list[float] = []
            association_radius_px = _known_detection_association_radius_px(detection, pixel_scale_arcsec_per_pixel)
            for frame_detection in candidate.frame_detections:
                if frame_detection.frame_index >= len(path):
                    continue
                predicted_position = path[frame_detection.frame_index]
                pixel_offset_px = None if predicted_position is None else math.hypot(frame_detection.x - predicted_position[0], frame_detection.y - predicted_position[1])
                sky_offset_arcsec = _candidate_known_detection_sky_offset_arcsec(
                    frame_detection,
                    detection=detection,
                    reference_observation_time=known_detection_result.prediction_time,
                    observation_time=frame_times[frame_detection.frame_index],
                )
                sky_tolerance_arcsec = None if pixel_scale_arcsec_per_pixel is None else association_radius_px * float(pixel_scale_arcsec_per_pixel)
                if detection.positional_uncertainty_arcsec is not None:
                    uncertainty_arcsec = float(detection.positional_uncertainty_arcsec)
                    sky_tolerance_arcsec = uncertainty_arcsec if sky_tolerance_arcsec is None else max(sky_tolerance_arcsec, uncertainty_arcsec)
                matched = (
                    pixel_offset_px is not None and pixel_offset_px <= association_radius_px
                ) or (
                    sky_offset_arcsec is not None and sky_tolerance_arcsec is not None and sky_offset_arcsec <= sky_tolerance_arcsec
                )
                if not matched:
                    continue
                if pixel_offset_px is not None:
                    offsets.append(float(pixel_offset_px))
                elif pixel_scale_arcsec_per_pixel is not None and sky_offset_arcsec is not None and pixel_scale_arcsec_per_pixel > 0:
                    offsets.append(float(sky_offset_arcsec / float(pixel_scale_arcsec_per_pixel)))
                else:
                    offsets.append(0.0)
            if len(offsets) < minimum_matched_frames:
                continue
            match_rms_px = float(np.sqrt(np.mean(np.square(np.asarray(offsets, dtype=float)))))
            max_match_offset_px = float(max(offsets))
            candidate_matches.append(
                (-len(offsets), match_rms_px, max_match_offset_px, -candidate.average_snr, candidate_index, detection_index)
            )
            match_details[(candidate_index, detection_index)] = (len(offsets), match_rms_px, max_match_offset_px)
    candidate_matches.sort()

    used_candidate_indices: set[int] = set()
    used_detection_indices: set[int] = set()
    recovered_known_objects: list[RecoveredKnownMovingObject] = []
    for _neg_frame_count, _rms_px, _max_offset_px, _neg_snr, candidate_index, detection_index in candidate_matches:
        if candidate_index in used_candidate_indices or detection_index in used_detection_indices:
            continue
        used_candidate_indices.add(candidate_index)
        used_detection_indices.add(detection_index)
        candidate = candidates[candidate_index]
        detection = associated_detections[detection_index]
        matched_frame_count, match_rms_px, max_match_offset_px = match_details[(candidate_index, detection_index)]
        reference_position = prediction_paths[detection_index][0]
        reference_x = float(detection.predicted_x) if reference_position is None else float(reference_position[0])
        reference_y = float(detection.predicted_y) if reference_position is None else float(reference_position[1])
        display_name = detection.name or detection.designation or "Unknown"
        summary_text = (
            f"Recovered {display_name} in {matched_frame_count}/{len(frame_times)} frame(s) with match RMS {match_rms_px:.2f} px "
            f"and average SNR {candidate.average_snr:.2f}."
        )
        recovered_known_objects.append(
            RecoveredKnownMovingObject(
                detection=detection,
                candidate=candidate,
                matched_frame_count=matched_frame_count,
                expected_frame_count=len(frame_times),
                match_rms_px=match_rms_px,
                max_match_offset_px=max_match_offset_px,
                reference_x=reference_x,
                reference_y=reference_y,
                summary_text=summary_text,
                within_estimated_limit=_detection_within_estimated_limit(detection, estimated_magnitude_limit),
            )
        )

    missed_known_objects: list[MissedKnownMovingObject] = []
    for detection_index, detection in enumerate(associated_detections):
        if detection_index in used_detection_indices:
            continue
        reference_position = prediction_paths[detection_index][0]
        reference_x = float(detection.predicted_x) if reference_position is None else float(reference_position[0])
        reference_y = float(detection.predicted_y) if reference_position is None else float(reference_position[1])
        display_name = detection.name or detection.designation or "Unknown"
        predicted_mag_text = "-" if detection.predicted_magnitude is None else f"{detection.predicted_magnitude:.1f}"
        missed_known_objects.append(
            MissedKnownMovingObject(
                detection=detection,
                expected_frame_count=len(frame_times),
                reference_x=reference_x,
                reference_y=reference_y,
                summary_text=(
                    f"Missed {display_name}; predicted magnitude {predicted_mag_text}, confidence {detection.confidence_score:.2f}, status: {detection.status}."
                ),
                within_estimated_limit=_detection_within_estimated_limit(detection, estimated_magnitude_limit),
            )
        )

    suppressed_candidate_indices = set(used_candidate_indices)
    for _neg_frame_count, _rms_px, _max_offset_px, _neg_snr, candidate_index, detection_index in candidate_matches:
        if detection_index in used_detection_indices:
            suppressed_candidate_indices.add(candidate_index)

    unmatched_candidates = [candidate for index, candidate in enumerate(candidates) if index not in suppressed_candidate_indices]
    return recovered_known_objects, missed_known_objects, unmatched_candidates


def _known_prediction_paths_by_detection(
    detections: list[SolarSystemDetection],
    *,
    frame_times: list[datetime],
    frame_pixel_wcs: list[object],
    known_detection_result: SolarSystemDetectionResult,
) -> dict[int, tuple[tuple[float, float] | None, ...]]:
    paths: dict[int, tuple[tuple[float, float] | None, ...]] = {}
    for detection_index, detection in enumerate(detections):
        positions: list[tuple[float, float] | None] = []
        for observation_time, frame_wcs in zip(frame_times, frame_pixel_wcs):
            predicted_ra_deg, predicted_dec_deg = predict_detection_coordinates(
                detection,
                reference_observation_time=known_detection_result.prediction_time,
                observation_time=observation_time,
            )
            predicted_x, predicted_y = frame_wcs.world_to_pixel_values(predicted_ra_deg, predicted_dec_deg)
            if np.isfinite(predicted_x) and np.isfinite(predicted_y):
                positions.append((float(predicted_x), float(predicted_y)))
            else:
                positions.append(None)
        paths[detection_index] = tuple(positions)
    return paths


def _known_detection_association_radius_px(
    detection: SolarSystemDetection,
    pixel_scale_arcsec_per_pixel: float | None,
) -> float:
    radius_px = float(_RECOVERY_ASSOCIATION_RADIUS_PX)
    if (
        detection.positional_uncertainty_arcsec is not None
        and pixel_scale_arcsec_per_pixel is not None
        and pixel_scale_arcsec_per_pixel > 0
    ):
        radius_px = max(radius_px, float(detection.positional_uncertainty_arcsec) / float(pixel_scale_arcsec_per_pixel))
    return radius_px


def _candidate_known_detection_sky_offset_arcsec(
    frame_detection: MovingObjectCandidateDetection,
    *,
    detection: SolarSystemDetection,
    reference_observation_time: datetime,
    observation_time: datetime,
) -> float | None:
    if frame_detection.ra_deg is None or frame_detection.dec_deg is None:
        return None
    predicted_ra_deg, predicted_dec_deg = predict_detection_coordinates(
        detection,
        reference_observation_time=reference_observation_time,
        observation_time=observation_time,
    )
    cos_dec = math.cos(math.radians(float(predicted_dec_deg)))
    delta_ra_arcsec = (float(frame_detection.ra_deg) - float(predicted_ra_deg)) * cos_dec * 3600.0
    delta_dec_arcsec = (float(frame_detection.dec_deg) - float(predicted_dec_deg)) * 3600.0
    return float(math.hypot(delta_ra_arcsec, delta_dec_arcsec))


def _normalize_residual_snr_bounds(minimum: float | None, maximum: float | None) -> tuple[float, float]:
    normalized_minimum = max(0.0, float(minimum or 0.0))
    normalized_maximum = max(0.0, float(maximum or 0.0))
    if normalized_maximum > 0.0 and normalized_minimum > normalized_maximum:
        normalized_minimum, normalized_maximum = normalized_maximum, normalized_minimum
    return normalized_minimum, normalized_maximum


def _normalize_discovery_min_candidate_frames(value: int | float | None) -> int:
    try:
        return max(2, int(value or _DISCOVERY_MIN_FRAMES))
    except (TypeError, ValueError):
        return _DISCOVERY_MIN_FRAMES


def _normalize_discovery_detection_sigma(value: float | int | None) -> float:
    try:
        return min(100.0, max(0.5, float(value if value is not None else _DISCOVERY_DETECTION_SIGMA)))
    except (TypeError, ValueError):
        return _DISCOVERY_DETECTION_SIGMA


def _normalize_discovery_detection_fwhm(value: float | int | None) -> float:
    try:
        return min(20.0, max(0.8, float(value if value is not None else _DISCOVERY_DETECTION_FWHM)))
    except (TypeError, ValueError):
        return _DISCOVERY_DETECTION_FWHM


def _normalize_discovery_max_residuals_per_frame(value: int | float | None) -> int:
    try:
        return max(1, min(500, int(value or _DISCOVERY_MAX_RESIDUALS_PER_FRAME)))
    except (TypeError, ValueError):
        return _DISCOVERY_MAX_RESIDUALS_PER_FRAME


def _normalize_discovery_edge_margin_px(value: int | float | None) -> int:
    try:
        return max(0, min(512, int(value if value is not None else _DISCOVERY_EDGE_MARGIN_PX)))
    except (TypeError, ValueError):
        return _DISCOVERY_EDGE_MARGIN_PX


def _normalize_discovery_min_seed_displacement_px(value: int | float | None) -> float:
    try:
        return min(100.0, max(0.0, float(value if value is not None else _DISCOVERY_MIN_DISPLACEMENT_PX)))
    except (TypeError, ValueError):
        return _DISCOVERY_MIN_DISPLACEMENT_PX


def _normalize_discovery_detector_mode(value: object) -> str:
    normalized = str(value or _DISCOVERY_DETECTOR_MODE_HYBRID).strip().lower()
    return normalized if normalized in _DISCOVERY_DETECTOR_MODES else _DISCOVERY_DETECTOR_MODE_HYBRID


def _normalize_discovery_streak_min_area_px(value: int | float | None) -> int:
    try:
        return max(2, min(4096, int(value if value is not None else _DISCOVERY_STREAK_MIN_AREA_PX)))
    except (TypeError, ValueError):
        return _DISCOVERY_STREAK_MIN_AREA_PX


def _normalize_discovery_streak_min_elongation(value: float | int | None) -> float:
    try:
        return min(50.0, max(1.0, float(value if value is not None else _DISCOVERY_STREAK_MIN_ELONGATION)))
    except (TypeError, ValueError):
        return _DISCOVERY_STREAK_MIN_ELONGATION


def _discovery_frame_batches(
    frame_paths: list[Path],
    reference_path: Path,
    frames_per_batch: int,
    *,
    single_batch_only: bool = False,
    minimum_candidate_frames: int = _DISCOVERY_MIN_FRAMES,
) -> list[list[Path]]:
    normalized_batch_size = max(0, int(frames_per_batch))
    if normalized_batch_size <= 0 or len(frame_paths) <= normalized_batch_size:
        return [list(frame_paths)]
    normalized_required_frames = _normalize_discovery_min_candidate_frames(minimum_candidate_frames)
    normalized_batch_size = max(normalized_required_frames, normalized_batch_size)
    reference_key = str(reference_path.resolve())
    remaining_paths: list[Path] = []
    reference_found = False
    for path in frame_paths:
        if not reference_found and str(path.resolve()) == reference_key:
            reference_found = True
            continue
        remaining_paths.append(path)
    if not reference_found:
        return [list(frame_paths)]
    non_reference_batch_size = max(normalized_required_frames - 1, normalized_batch_size - 1)
    overlap_count = max(0, normalized_required_frames - 2)
    step = max(1, non_reference_batch_size - overlap_count)
    batches: list[list[Path]] = []
    start_index = 0
    while start_index < len(remaining_paths):
        end_index = min(len(remaining_paths), start_index + non_reference_batch_size)
        if end_index - start_index < normalized_required_frames - 1 and batches:
            start_index = max(0, len(remaining_paths) - non_reference_batch_size)
            end_index = len(remaining_paths)
        batch_paths = [reference_path, *remaining_paths[start_index:end_index]]
        if batches and [str(path.resolve()) for path in batches[-1]] == [str(path.resolve()) for path in batch_paths]:
            break
        batches.append(batch_paths)
        if end_index >= len(remaining_paths):
            break
        start_index += step
    if single_batch_only and batches:
        return [batches[0]]
    return batches or [list(frame_paths)]


def _remap_candidate_frame_indices(candidates: list[MovingObjectCandidate], batch_frame_indices: list[int]) -> list[MovingObjectCandidate]:
    remapped_candidates: list[MovingObjectCandidate] = []
    for candidate in candidates:
        remapped_candidates.append(
            MovingObjectCandidate(
                candidate_id=candidate.candidate_id,
                frame_detections=tuple(
                    MovingObjectCandidateDetection(
                        source_path=detection.source_path,
                        observation_time=detection.observation_time,
                        frame_index=batch_frame_indices[detection.frame_index],
                        x=detection.x,
                        y=detection.y,
                        peak_value=detection.peak_value,
                        local_snr=detection.local_snr,
                        ra_deg=detection.ra_deg,
                        dec_deg=detection.dec_deg,
                    )
                    for detection in candidate.frame_detections
                ),
                average_snr=candidate.average_snr,
                peak_value=candidate.peak_value,
                fit_rms_px=candidate.fit_rms_px,
                motion_px_per_hour=candidate.motion_px_per_hour,
                motion_arcsec_per_hour=candidate.motion_arcsec_per_hour,
                displacement_px=candidate.displacement_px,
                start_x=candidate.start_x,
                start_y=candidate.start_y,
                end_x=candidate.end_x,
                end_y=candidate.end_y,
                summary_text=candidate.summary_text,
                max_deflection_px=candidate.max_deflection_px,
                discovery_method=candidate.discovery_method,
            )
        )
    return remapped_candidates


def _compact_remap_candidate_frame_indices(candidates: list[MovingObjectCandidate], available_frame_indices: list[int]) -> list[MovingObjectCandidate]:
    frame_index_lookup = {frame_index: compact_index for compact_index, frame_index in enumerate(available_frame_indices)}
    remapped_candidates: list[MovingObjectCandidate] = []
    for candidate in candidates:
        remapped_detections = [
            MovingObjectCandidateDetection(
                source_path=detection.source_path,
                observation_time=detection.observation_time,
                frame_index=frame_index_lookup[detection.frame_index],
                x=detection.x,
                y=detection.y,
                peak_value=detection.peak_value,
                local_snr=detection.local_snr,
                ra_deg=detection.ra_deg,
                dec_deg=detection.dec_deg,
            )
            for detection in candidate.frame_detections
            if detection.frame_index in frame_index_lookup
        ]
        if not remapped_detections:
            continue
        remapped_candidates.append(
            MovingObjectCandidate(
                candidate_id=candidate.candidate_id,
                frame_detections=tuple(remapped_detections),
                average_snr=candidate.average_snr,
                peak_value=candidate.peak_value,
                fit_rms_px=candidate.fit_rms_px,
                motion_px_per_hour=candidate.motion_px_per_hour,
                motion_arcsec_per_hour=candidate.motion_arcsec_per_hour,
                displacement_px=candidate.displacement_px,
                start_x=candidate.start_x,
                start_y=candidate.start_y,
                end_x=candidate.end_x,
                end_y=candidate.end_y,
                summary_text=candidate.summary_text,
                max_deflection_px=candidate.max_deflection_px,
                discovery_method=candidate.discovery_method,
            )
        )
    return remapped_candidates


def _rank_and_number_candidates(candidates: list[MovingObjectCandidate], *, candidate_prefix: str = "C") -> list[MovingObjectCandidate]:
    ordered = sorted(candidates, key=_candidate_sort_key)
    unique_candidates: list[MovingObjectCandidate] = []
    for candidate in ordered:
        if any(_candidates_are_near_duplicates(candidate, existing) for existing in unique_candidates):
            continue
        unique_candidates.append(candidate)
        if len(unique_candidates) >= _DISCOVERY_MAX_CANDIDATES:
            break
    numbered: list[MovingObjectCandidate] = []
    for index, candidate in enumerate(unique_candidates, start=1):
        summary_text = (
            f"Frames: {len(candidate.frame_detections)} | Motion: {candidate.motion_px_per_hour:.2f} px/h"
            + ("" if candidate.motion_arcsec_per_hour is None else f" ({candidate.motion_arcsec_per_hour:.2f} arcsec/h)")
            + f" | Residual: {candidate.average_snr:.2f} | Deflection RMS: {candidate.fit_rms_px:.2f} px"
        )
        numbered.append(
            MovingObjectCandidate(
                candidate_id=f"{candidate_prefix}{index}",
                frame_detections=candidate.frame_detections,
                average_snr=candidate.average_snr,
                peak_value=candidate.peak_value,
                fit_rms_px=candidate.fit_rms_px,
                motion_px_per_hour=candidate.motion_px_per_hour,
                motion_arcsec_per_hour=candidate.motion_arcsec_per_hour,
                displacement_px=candidate.displacement_px,
                start_x=candidate.start_x,
                start_y=candidate.start_y,
                end_x=candidate.end_x,
                end_y=candidate.end_y,
                summary_text=summary_text,
                max_deflection_px=candidate.max_deflection_px,
                discovery_method=candidate.discovery_method,
            )
        )
    return numbered


def _split_discovery_candidates_by_linearity(
    candidates: list[MovingObjectCandidate],
    *,
    potential_candidate_max_deflection_rms_px: float,
    review_candidate_max_deflection_rms_px: float,
) -> tuple[list[MovingObjectCandidate], list[MovingObjectCandidate], int]:
    potential_candidates: list[MovingObjectCandidate] = []
    review_candidates: list[MovingObjectCandidate] = []
    suppressed_candidate_count = 0
    for candidate in candidates:
        if candidate.fit_rms_px <= potential_candidate_max_deflection_rms_px + 1e-6:
            potential_candidates.append(candidate)
            continue
        if candidate.fit_rms_px <= review_candidate_max_deflection_rms_px + 1e-6:
            review_candidates.append(candidate)
            continue
        suppressed_candidate_count += 1
    return potential_candidates, review_candidates, suppressed_candidate_count


def _normalize_discovery_candidate_deflection_thresholds(
    potential_candidate_max_deflection_rms_px: float,
    review_candidate_max_deflection_rms_px: float,
) -> tuple[float, float]:
    normalized_potential = min(20.0, max(0.1, float(potential_candidate_max_deflection_rms_px)))
    normalized_review = min(20.0, max(0.1, float(review_candidate_max_deflection_rms_px)))
    if normalized_review < normalized_potential:
        normalized_review = normalized_potential
    return normalized_potential, normalized_review


def _normalize_discovery_synthetic_sweep_settings(
    *,
    enable_synthetic_sweep: bool,
    max_motion_px_per_hour: float,
    motion_step_px_per_hour: float,
    angle_step_deg: float,
    direction_focus_mode: str,
    direction_focus_half_width_deg: float,
    min_stacked_snr: float,
    save_stacks: bool,
    output_directory: Path | None,
    combine_mode: str,
) -> _SyntheticSweepSettings | None:
    if not enable_synthetic_sweep:
        return None
    normalized_max_motion = min(500.0, max(0.1, float(max_motion_px_per_hour)))
    normalized_motion_step = min(normalized_max_motion, max(0.1, float(motion_step_px_per_hour)))
    normalized_angle_step = min(180.0, max(1.0, float(angle_step_deg)))
    normalized_direction_focus_mode = _normalize_synthetic_sweep_direction_focus_mode(direction_focus_mode)
    normalized_direction_focus_half_width_deg = min(180.0, max(1.0, float(direction_focus_half_width_deg)))
    normalized_min_stacked_snr = min(500.0, max(0.5, float(min_stacked_snr)))
    normalized_combine_mode = "sigma_clipped_mean" if str(combine_mode or "mean").strip().lower() == "sigma_clipped_mean" else "mean"
    return _SyntheticSweepSettings(
        max_motion_px_per_hour=normalized_max_motion,
        motion_step_px_per_hour=normalized_motion_step,
        angle_step_deg=normalized_angle_step,
        direction_focus_mode=normalized_direction_focus_mode,
        direction_focus_half_width_deg=normalized_direction_focus_half_width_deg,
        min_stacked_snr=normalized_min_stacked_snr,
        save_stacks=bool(save_stacks),
        output_directory=None if output_directory is None else Path(output_directory),
        combine_mode=normalized_combine_mode,
    )


def _candidate_rank(candidate: MovingObjectCandidate) -> tuple[int, float, float, float]:
    return (
        len(candidate.frame_detections),
        -candidate.fit_rms_px,
        candidate.average_snr,
        candidate.displacement_px,
    )


def _candidate_sort_key(candidate: MovingObjectCandidate) -> tuple[int, float, float, float]:
    return (
        -len(candidate.frame_detections),
        candidate.fit_rms_px,
        -candidate.average_snr,
        -candidate.displacement_px,
    )


def _candidates_are_near_duplicates(first: MovingObjectCandidate, second: MovingObjectCandidate) -> bool:
    if abs(first.motion_px_per_hour - second.motion_px_per_hour) > 0.6:
        return False
    if math.hypot(first.start_x - second.start_x, first.start_y - second.start_y) > 2.0:
        return False
    if math.hypot(first.end_x - second.end_x, first.end_y - second.end_y) > 2.0:
        return False
    return True


def _coerce_float(value: object) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _emit_progress(progress_callback: Callable[[str], None] | None, message: str) -> None:
    if progress_callback is not None:
        progress_callback(message)


def _raise_if_cancelled(cancel_callback: Callable[[], bool] | None, message: str = "Discover cancelled.") -> None:
    if cancel_callback is not None and cancel_callback():
        raise DiscoveryCancelledError(message)