from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import math

import numpy as np
from astropy.stats import mad_std, sigma_clip

from photometry_app.core.array_backend import NUMPY_ARRAY_BACKEND, SyntheticTrackingArrayBackend, resolve_full_frame_backend
from photometry_app.core.image_io import read_photometry_image_data
from photometry_app.core.solar_system import SolarSystemDetection, measure_detection_in_frame


_MAX_FULL_FRAME_SYNTHETIC_TRACKING_WORKERS = 2
_MAX_SIGMA_CLIPPED_STACK_BYTES = 512 * 1024 * 1024
_SYNTHETIC_TRACKING_INTEGRATION_MODES = frozenset({"average", "mean", "min", "max"})
_SYNTHETIC_TRACKING_WEIGHT_MODES = frozenset({"psf_signal_weight", "psf_snr", "snr", "average_signal_strength"})
_SYNTHETIC_TRACKING_REJECTION_MODES = frozenset({"no_rejection", "min_max", "sigma_clipping", "winsorized_sigma_clipping", "averaged_sigma_clipping"})
_FULL_FRAME_GPU_BACKEND_WARMED = False


@dataclass(slots=True)
class SyntheticTrackingFrameWeightMetrics:
    psf_signal_weight: float
    psf_snr: float
    snr: float
    average_signal_strength: float


@dataclass(slots=True)
class SyntheticTrackingFrameContribution:
    source_path: Path
    observation_time: datetime | None
    predicted_x: float | None
    predicted_y: float | None
    used: bool
    reason: str | None = None


@dataclass(slots=True)
class SyntheticTrackingFrameTarget:
    source_path: Path
    observation_time: datetime
    predicted_x: float
    predicted_y: float


@dataclass(slots=True)
class SyntheticTrackingResult:
    reference_path: Path
    stacked_data: np.ndarray
    center_x: float
    center_y: float
    measured_x: float | None
    measured_y: float | None
    match_offset_px: float | None
    local_snr: float | None
    local_peak_value: float | None
    local_flux: float | None
    used_frame_count: int
    skipped_frame_count: int
    crop_radius: int
    frame_contributions: tuple[SyntheticTrackingFrameContribution, ...]
    summary_text: str
    compute_backend_summary: str | None = None
    gpu_warmup_summary: str | None = None
    linear_stacked_data: np.ndarray | None = None
    motion_px_per_hour: float | None = None
    motion_arcsec_per_hour: float | None = None
    motion_angle_deg: float | None = None
    full_frame_mode: bool = False


@dataclass(slots=True)
class _PreparedSyntheticTrackingFrame:
    index: int
    stack_patch: np.ndarray | None
    linear_stack_patch: np.ndarray | None
    contribution: SyntheticTrackingFrameContribution
    progress_message: str
    frame_weight_metrics: SyntheticTrackingFrameWeightMetrics | None = None


@dataclass(slots=True)
class _PreparedFullFrameSyntheticTrackingFrame:
    index: int
    shifted_frame: object | None
    contribution: SyntheticTrackingFrameContribution
    progress_message: str
    frame_weight_metrics: SyntheticTrackingFrameWeightMetrics | None = None


def _resolve_synthetic_tracking_worker_count(total_frames: int, max_parallel_workers: int, *, full_frame_mode: bool = False) -> int:
    worker_count = min(total_frames, max(0, int(max_parallel_workers)))
    if full_frame_mode:
        worker_count = min(worker_count, _MAX_FULL_FRAME_SYNTHETIC_TRACKING_WORKERS)
    return worker_count


def _prepare_synthetic_tracking_frame(
    *,
    index: int,
    source_path: Path,
    detection: SolarSystemDetection,
    frame_observation_times: dict[str, datetime],
    frame_exposure_seconds: dict[str, float | None],
    frame_targets: dict[str, SyntheticTrackingFrameTarget] | None,
    reference_observation_time: datetime,
    crop_radius: int,
    patch_size: int,
    total_frames: int,
) -> _PreparedSyntheticTrackingFrame:
    resolved_key = str(source_path.resolve())
    observation_time = frame_observation_times.get(resolved_key)
    frame_target = None if frame_targets is None else frame_targets.get(resolved_key)
    predicted_x = None
    predicted_y = None
    if frame_target is not None:
        observation_time = frame_target.observation_time
        predicted_x = float(frame_target.predicted_x)
        predicted_y = float(frame_target.predicted_y)
    if observation_time is None:
        return _PreparedSyntheticTrackingFrame(
            index=index,
            stack_patch=None,
            linear_stack_patch=None,
            contribution=SyntheticTrackingFrameContribution(
                source_path=source_path,
                observation_time=None,
                predicted_x=None,
                predicted_y=None,
                used=False,
                reason="Missing observation timestamp.",
            ),
            progress_message=f"Skipped {source_path.name}: missing observation timestamp.",
        )

    if frame_target is None:
        measurement = measure_detection_in_frame(
            source_path,
            detection,
            reference_observation_time=reference_observation_time,
            observation_time=observation_time,
            exposure_seconds=frame_exposure_seconds.get(resolved_key),
            measure_local_match=False,
        )
        if measurement is None:
            return _PreparedSyntheticTrackingFrame(
                index=index,
                stack_patch=None,
                linear_stack_patch=None,
                contribution=SyntheticTrackingFrameContribution(
                    source_path=source_path,
                    observation_time=observation_time,
                    predicted_x=None,
                    predicted_y=None,
                    used=False,
                    reason="Could not predict the object position in this frame.",
                ),
                progress_message=f"Skipped {source_path.name}: object prediction unavailable.",
            )
        predicted_x = float(measurement.predicted_x)
        predicted_y = float(measurement.predicted_y)

    image_data = _collapsed_image_plane(read_photometry_image_data(source_path))
    if image_data.ndim != 2 or image_data.size == 0:
        return _PreparedSyntheticTrackingFrame(
            index=index,
            stack_patch=None,
            linear_stack_patch=None,
            contribution=SyntheticTrackingFrameContribution(
                source_path=source_path,
                observation_time=observation_time,
                predicted_x=predicted_x,
                predicted_y=predicted_y,
                used=False,
                reason="Frame is not a usable 2D image.",
            ),
            progress_message=f"Skipped {source_path.name}: image data is not a usable 2D frame.",
        )

    cleaned_image = _clean_image_data(image_data)
    patch = _sample_centered_patch(cleaned_image, predicted_x, predicted_y, crop_radius)
    finite_fraction = float(np.isfinite(patch).sum()) / float(patch_size * patch_size)
    if finite_fraction < 0.6:
        return _PreparedSyntheticTrackingFrame(
            index=index,
            stack_patch=None,
            linear_stack_patch=None,
            contribution=SyntheticTrackingFrameContribution(
                source_path=source_path,
                observation_time=observation_time,
                predicted_x=predicted_x,
                predicted_y=predicted_y,
                used=False,
                reason="Predicted position falls too close to the image edge.",
            ),
            progress_message=f"Skipped {source_path.name}: predicted position is too close to the image edge.",
        )

    patch_background = float(np.nanmedian(patch[np.isfinite(patch)]))
    linear_patch = np.asarray(patch, dtype=np.float32)
    display_patch = np.asarray(patch - patch_background, dtype=np.float32)
    return _PreparedSyntheticTrackingFrame(
        index=index,
        stack_patch=display_patch,
        linear_stack_patch=linear_patch,
        contribution=SyntheticTrackingFrameContribution(
            source_path=source_path,
            observation_time=observation_time,
            predicted_x=predicted_x,
            predicted_y=predicted_y,
            used=True,
        ),
        progress_message=f"Aligned {index}/{total_frames} frame(s) for synthetic tracking: {source_path.name}",
        frame_weight_metrics=_estimate_synthetic_tracking_frame_weight_metrics(
            display_patch,
            center_x=float(crop_radius),
            center_y=float(crop_radius),
        ),
    )


def _prepare_full_frame_synthetic_tracking_frame(
    *,
    index: int,
    source_path: Path,
    frame_observation_times: dict[str, datetime],
    reference_time: datetime,
    motion_x_px_per_hour: float,
    motion_y_px_per_hour: float,
    total_frames: int,
    array_backend: SyntheticTrackingArrayBackend,
    estimate_weight_metrics: bool,
) -> _PreparedFullFrameSyntheticTrackingFrame:
    resolved_key = str(source_path.resolve())
    observation_time = frame_observation_times.get(resolved_key)
    if observation_time is None:
        return _PreparedFullFrameSyntheticTrackingFrame(
            index=index,
            shifted_frame=None,
            contribution=SyntheticTrackingFrameContribution(
                source_path=source_path,
                observation_time=None,
                predicted_x=None,
                predicted_y=None,
                used=False,
                reason="Missing observation timestamp.",
            ),
            progress_message=f"Skipped {source_path.name}: missing observation timestamp.",
        )

    image_data = _collapsed_image_plane(read_photometry_image_data(source_path))
    if image_data.ndim != 2 or image_data.size == 0:
        return _PreparedFullFrameSyntheticTrackingFrame(
            index=index,
            shifted_frame=None,
            contribution=SyntheticTrackingFrameContribution(
                source_path=source_path,
                observation_time=observation_time,
                predicted_x=None,
                predicted_y=None,
                used=False,
                reason="Frame is not a usable 2D image.",
            ),
            progress_message=f"Skipped {source_path.name}: image data is not a usable 2D frame.",
        )

    cleaned_image = _clean_image_data(image_data)
    delta_hours = (observation_time - reference_time).total_seconds() / 3600.0
    shifted_frame = _shift_image_with_zero_fill_array_backend(
        cleaned_image,
        motion_x_px_per_hour * delta_hours,
        motion_y_px_per_hour * delta_hours,
        array_backend=array_backend,
    )
    return _PreparedFullFrameSyntheticTrackingFrame(
        index=index,
        shifted_frame=shifted_frame,
        contribution=SyntheticTrackingFrameContribution(
            source_path=source_path,
            observation_time=observation_time,
            predicted_x=None,
            predicted_y=None,
            used=True,
        ),
        progress_message=f"Aligned {index}/{total_frames} frame(s) for full-image synthetic tracking: {source_path.name}",
        frame_weight_metrics=(
            _estimate_full_frame_weight_metrics_array_backend(shifted_frame, array_backend=array_backend)
            if estimate_weight_metrics
            else None
        ),
    )


def build_synthetic_tracked_stack(
    frame_paths: list[Path],
    detection: SolarSystemDetection,
    *,
    reference_path: Path,
    frame_observation_times: dict[str, datetime],
    frame_exposure_seconds: dict[str, float | None] | None,
    frame_targets: dict[str, SyntheticTrackingFrameTarget] | None = None,
    reference_observation_time: datetime,
    crop_radius: int = 24,
    combine_mode: str = "mean",
    integration_mode: str | None = None,
    weight_mode: str | None = None,
    rejection_mode: str | None = None,
    motion_arcsec_per_hour: float | None = None,
    max_parallel_workers: int = 0,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> SyntheticTrackingResult:
    if crop_radius < 4:
        raise ValueError("Synthetic tracking crop radius must be at least 4 pixels.")
    if not frame_paths:
        raise ValueError("Synthetic tracking requires at least one frame.")
    resolved_integration_mode, resolved_weight_mode, resolved_rejection_mode = _resolve_synthetic_tracking_stack_modes(
        combine_mode=combine_mode,
        integration_mode=integration_mode,
        weight_mode=weight_mode,
        rejection_mode=rejection_mode,
    )

    patch_size = (crop_radius * 2) + 1
    stack_patches: list[np.ndarray] = []
    linear_stack_patches: list[np.ndarray] = []
    frame_weight_metrics: list[SyntheticTrackingFrameWeightMetrics] = []
    frame_contributions: list[SyntheticTrackingFrameContribution] = []
    exposure_lookup = frame_exposure_seconds or {}
    total_frames = len(frame_paths)

    prepared_frames: list[_PreparedSyntheticTrackingFrame]
    worker_count = _resolve_synthetic_tracking_worker_count(total_frames, max_parallel_workers)
    if worker_count > 1:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(
                    _prepare_synthetic_tracking_frame,
                    index=index,
                    source_path=source_path,
                    detection=detection,
                    frame_observation_times=frame_observation_times,
                    frame_exposure_seconds=exposure_lookup,
                    frame_targets=frame_targets,
                    reference_observation_time=reference_observation_time,
                    crop_radius=crop_radius,
                    patch_size=patch_size,
                    total_frames=total_frames,
                ): index
                for index, source_path in enumerate(frame_paths, start=1)
            }
            prepared_by_index: dict[int, _PreparedSyntheticTrackingFrame] = {}
            for completed_count, future in enumerate(as_completed(futures), start=1):
                prepared = future.result()
                prepared_by_index[prepared.index] = prepared
                _emit_progress(progress_callback, completed_count, total_frames, prepared.progress_message)
            prepared_frames = [prepared_by_index[index] for index in range(1, total_frames + 1)]
    else:
        prepared_frames = [
            _prepare_synthetic_tracking_frame(
                index=index,
                source_path=source_path,
                detection=detection,
                frame_observation_times=frame_observation_times,
                frame_exposure_seconds=exposure_lookup,
                frame_targets=frame_targets,
                reference_observation_time=reference_observation_time,
                crop_radius=crop_radius,
                patch_size=patch_size,
                total_frames=total_frames,
            )
            for index, source_path in enumerate(frame_paths, start=1)
        ]

    for display_index, prepared in enumerate(prepared_frames, start=1):
        if prepared.stack_patch is not None:
            stack_patches.append(prepared.stack_patch)
            linear_stack_patches.append(prepared.linear_stack_patch if prepared.linear_stack_patch is not None else prepared.stack_patch)
            if prepared.frame_weight_metrics is not None:
                frame_weight_metrics.append(prepared.frame_weight_metrics)
        frame_contributions.append(prepared.contribution)
        if worker_count <= 1:
            _emit_progress(progress_callback, display_index, total_frames, prepared.progress_message)

    if not stack_patches:
        raise ValueError("Synthetic tracking could not build a stack from the available frames.")

    stacked_data = _combine_stack_patches(
        stack_patches,
        frame_weight_metrics=frame_weight_metrics,
        integration_mode=resolved_integration_mode,
        weight_mode=resolved_weight_mode,
        rejection_mode=resolved_rejection_mode,
    )
    stacked_data = np.nan_to_num(stacked_data, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32, copy=False)
    linear_stacked_data = _combine_stack_patches(
        linear_stack_patches,
        frame_weight_metrics=frame_weight_metrics,
        integration_mode=resolved_integration_mode,
        weight_mode=resolved_weight_mode,
        rejection_mode=resolved_rejection_mode,
    )
    linear_stacked_data = np.nan_to_num(linear_stacked_data, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32, copy=False)
    center_x = float(crop_radius)
    center_y = float(crop_radius)
    measured_x, measured_y, match_offset_px, local_snr, local_peak_value, local_flux = measure_synthetic_tracking_peak(
        stacked_data,
        center_x,
        center_y,
    )
    used_frame_count = sum(1 for contribution in frame_contributions if contribution.used)
    skipped_frame_count = len(frame_contributions) - used_frame_count
    motion_px_per_hour, motion_angle_deg = _infer_synthetic_tracking_motion(frame_contributions)
    summary_text = format_synthetic_tracking_summary(
        used_frame_count=used_frame_count,
        total_frame_count=len(frame_contributions),
        local_snr=local_snr,
        match_offset_px=match_offset_px,
        motion_px_per_hour=motion_px_per_hour,
        motion_arcsec_per_hour=motion_arcsec_per_hour,
        motion_angle_deg=motion_angle_deg,
    )
    return SyntheticTrackingResult(
        reference_path=reference_path,
        stacked_data=stacked_data,
        center_x=center_x,
        center_y=center_y,
        motion_px_per_hour=motion_px_per_hour,
        motion_arcsec_per_hour=motion_arcsec_per_hour,
        motion_angle_deg=motion_angle_deg,
        measured_x=measured_x,
        measured_y=measured_y,
        match_offset_px=match_offset_px,
        local_snr=local_snr,
        local_peak_value=local_peak_value,
        local_flux=local_flux,
        used_frame_count=used_frame_count,
        skipped_frame_count=skipped_frame_count,
        crop_radius=crop_radius,
        frame_contributions=tuple(frame_contributions),
        summary_text=summary_text,
        compute_backend_summary="Compute backend: CPU (object-centered crop mode)",
        linear_stacked_data=linear_stacked_data,
    )


def build_synthetic_tracked_cumulative_stacks(
    frame_paths: list[Path],
    detection: SolarSystemDetection,
    *,
    reference_path: Path,
    frame_observation_times: dict[str, datetime],
    frame_exposure_seconds: dict[str, float | None] | None,
    frame_targets: dict[str, SyntheticTrackingFrameTarget] | None = None,
    reference_observation_time: datetime,
    crop_radius: int = 24,
    combine_mode: str = "mean",
    integration_mode: str | None = None,
    weight_mode: str | None = None,
    rejection_mode: str | None = None,
    motion_arcsec_per_hour: float | None = None,
    max_parallel_workers: int = 0,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> tuple[SyntheticTrackingResult, ...]:
    if crop_radius < 4:
        raise ValueError("Synthetic tracking crop radius must be at least 4 pixels.")
    if not frame_paths:
        raise ValueError("Synthetic tracking requires at least one frame.")
    resolved_integration_mode, resolved_weight_mode, resolved_rejection_mode = _resolve_synthetic_tracking_stack_modes(
        combine_mode=combine_mode,
        integration_mode=integration_mode,
        weight_mode=weight_mode,
        rejection_mode=rejection_mode,
    )

    patch_size = (crop_radius * 2) + 1
    exposure_lookup = frame_exposure_seconds or {}
    total_frames = len(frame_paths)
    worker_count = _resolve_synthetic_tracking_worker_count(total_frames, max_parallel_workers)

    if worker_count > 1:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(
                    _prepare_synthetic_tracking_frame,
                    index=index,
                    source_path=source_path,
                    detection=detection,
                    frame_observation_times=frame_observation_times,
                    frame_exposure_seconds=exposure_lookup,
                    frame_targets=frame_targets,
                    reference_observation_time=reference_observation_time,
                    crop_radius=crop_radius,
                    patch_size=patch_size,
                    total_frames=total_frames,
                ): index
                for index, source_path in enumerate(frame_paths, start=1)
            }
            prepared_by_index: dict[int, _PreparedSyntheticTrackingFrame] = {}
            for completed_count, future in enumerate(as_completed(futures), start=1):
                prepared = future.result()
                prepared_by_index[prepared.index] = prepared
                _emit_progress(progress_callback, completed_count, total_frames, prepared.progress_message)
            prepared_frames = [prepared_by_index[index] for index in range(1, total_frames + 1)]
    else:
        prepared_frames = []
        for index, source_path in enumerate(frame_paths, start=1):
            prepared = _prepare_synthetic_tracking_frame(
                index=index,
                source_path=source_path,
                detection=detection,
                frame_observation_times=frame_observation_times,
                frame_exposure_seconds=exposure_lookup,
                frame_targets=frame_targets,
                reference_observation_time=reference_observation_time,
                crop_radius=crop_radius,
                patch_size=patch_size,
                total_frames=total_frames,
            )
            prepared_frames.append(prepared)
            _emit_progress(progress_callback, index, total_frames, prepared.progress_message)

    use_streaming_average = resolved_rejection_mode == "no_rejection" and resolved_integration_mode in {"average", "mean"}
    use_streaming_extrema = resolved_rejection_mode == "no_rejection" and resolved_integration_mode in {"min", "max"}

    stack_patches: list[np.ndarray] = []
    linear_stack_patches: list[np.ndarray] = []
    frame_weight_metrics: list[SyntheticTrackingFrameWeightMetrics] = []
    frame_contributions: list[SyntheticTrackingFrameContribution] = []
    cumulative_results: list[SyntheticTrackingResult] = []
    accumulated_sum: np.ndarray | None = None
    accumulated_weight: np.ndarray | None = None
    accumulated_linear_sum: np.ndarray | None = None
    accumulated_linear_weight: np.ndarray | None = None
    extrema_stack: np.ndarray | None = None
    linear_extrema_stack: np.ndarray | None = None

    for prepared in prepared_frames:
        frame_contributions.append(prepared.contribution)
        if prepared.stack_patch is not None:
            stack_patches.append(prepared.stack_patch)
            linear_patch = prepared.linear_stack_patch if prepared.linear_stack_patch is not None else prepared.stack_patch
            linear_stack_patches.append(linear_patch)
            if prepared.frame_weight_metrics is not None:
                frame_weight_metrics.append(prepared.frame_weight_metrics)
            frame_weight = _synthetic_tracking_frame_weight_value(
                prepared.frame_weight_metrics,
                resolved_integration_mode,
                resolved_weight_mode,
            )
            if use_streaming_average:
                accumulated_sum, accumulated_weight = _accumulate_average_stack_frame(
                    accumulated_sum,
                    accumulated_weight,
                    prepared.stack_patch,
                    frame_weight=frame_weight,
                )
                accumulated_linear_sum, accumulated_linear_weight = _accumulate_average_stack_frame(
                    accumulated_linear_sum,
                    accumulated_linear_weight,
                    linear_patch,
                    frame_weight=frame_weight,
                )
            elif use_streaming_extrema:
                extrema_stack = _accumulate_extrema_stack_frame(
                    extrema_stack,
                    prepared.stack_patch,
                    use_max=resolved_integration_mode == "max",
                )
                linear_extrema_stack = _accumulate_extrema_stack_frame(
                    linear_extrema_stack,
                    linear_patch,
                    use_max=resolved_integration_mode == "max",
                )

        if not stack_patches:
            raise ValueError("Synthetic tracking could not build a stack from the available frames.")

        if use_streaming_average:
            if accumulated_sum is None or accumulated_weight is None or accumulated_linear_sum is None or accumulated_linear_weight is None:
                raise ValueError("Synthetic tracking could not build a stack from the available frames.")
            stacked_data = _finalize_average_stack(accumulated_sum, accumulated_weight)
            linear_stacked_data = _finalize_average_stack(accumulated_linear_sum, accumulated_linear_weight)
        elif use_streaming_extrema:
            if extrema_stack is None or linear_extrema_stack is None:
                raise ValueError("Synthetic tracking could not build a stack from the available frames.")
            stacked_data = np.asarray(extrema_stack, dtype=np.float32, copy=True)
            linear_stacked_data = np.asarray(linear_extrema_stack, dtype=np.float32, copy=True)
        else:
            stacked_data = _combine_stack_patches(
                stack_patches,
                frame_weight_metrics=frame_weight_metrics,
                integration_mode=resolved_integration_mode,
                weight_mode=resolved_weight_mode,
                rejection_mode=resolved_rejection_mode,
            )
            linear_stacked_data = _combine_stack_patches(
                linear_stack_patches,
                frame_weight_metrics=frame_weight_metrics,
                integration_mode=resolved_integration_mode,
                weight_mode=resolved_weight_mode,
                rejection_mode=resolved_rejection_mode,
            )

        stacked_data = np.nan_to_num(stacked_data, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32, copy=False)
        linear_stacked_data = np.nan_to_num(linear_stacked_data, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32, copy=False)
        center_x = float(crop_radius)
        center_y = float(crop_radius)
        measured_x, measured_y, match_offset_px, local_snr, local_peak_value, local_flux = measure_synthetic_tracking_peak(
            stacked_data,
            center_x,
            center_y,
        )
        used_frame_count = sum(1 for contribution in frame_contributions if contribution.used)
        skipped_frame_count = len(frame_contributions) - used_frame_count
        motion_px_per_hour, motion_angle_deg = _infer_synthetic_tracking_motion(frame_contributions)
        summary_text = format_synthetic_tracking_summary(
            used_frame_count=used_frame_count,
            total_frame_count=len(frame_contributions),
            local_snr=local_snr,
            match_offset_px=match_offset_px,
            motion_px_per_hour=motion_px_per_hour,
            motion_arcsec_per_hour=motion_arcsec_per_hour,
            motion_angle_deg=motion_angle_deg,
        )
        cumulative_results.append(
            SyntheticTrackingResult(
                reference_path=reference_path,
                stacked_data=stacked_data,
                center_x=center_x,
                center_y=center_y,
                motion_px_per_hour=motion_px_per_hour,
                motion_arcsec_per_hour=motion_arcsec_per_hour,
                motion_angle_deg=motion_angle_deg,
                measured_x=measured_x,
                measured_y=measured_y,
                match_offset_px=match_offset_px,
                local_snr=local_snr,
                local_peak_value=local_peak_value,
                local_flux=local_flux,
                used_frame_count=used_frame_count,
                skipped_frame_count=skipped_frame_count,
                crop_radius=crop_radius,
                frame_contributions=tuple(frame_contributions),
                summary_text=summary_text,
                compute_backend_summary="Compute backend: CPU (object-centered cumulative crop mode)",
                linear_stacked_data=linear_stacked_data,
            )
        )

    return tuple(cumulative_results)


def build_synthetic_tracked_full_frame_stack(
    frame_paths: list[Path],
    *,
    reference_path: Path,
    frame_observation_times: dict[str, datetime],
    motion_px_per_hour: float,
    motion_angle_deg: float,
    combine_mode: str = "mean",
    integration_mode: str | None = None,
    weight_mode: str | None = None,
    rejection_mode: str | None = None,
    motion_arcsec_per_hour: float | None = None,
    max_parallel_workers: int = 0,
    progress_callback: Callable[[int, int, str], None] | None = None,
    array_backend_preference: str = "auto",
) -> SyntheticTrackingResult:
    if not frame_paths:
        raise ValueError("Synthetic tracking requires at least one frame.")
    resolved_integration_mode, resolved_weight_mode, resolved_rejection_mode = _resolve_synthetic_tracking_stack_modes(
        combine_mode=combine_mode,
        integration_mode=integration_mode,
        weight_mode=weight_mode,
        rejection_mode=rejection_mode,
    )

    first_frame_key = str(frame_paths[0].resolve())
    reference_time = frame_observation_times.get(first_frame_key)
    if reference_time is None:
        raise ValueError("Synthetic tracking requires valid observation timestamps for full-image stacking.")

    motion_angle_rad = math.radians(float(motion_angle_deg))
    motion_x_px_per_hour = float(motion_px_per_hour) * math.cos(motion_angle_rad)
    motion_y_px_per_hour = float(motion_px_per_hour) * math.sin(motion_angle_rad)
    array_backend, backend_note = resolve_full_frame_backend(
        preference=array_backend_preference,
        integration_mode=resolved_integration_mode,
        rejection_mode=resolved_rejection_mode,
    )
    frame_contributions: list[SyntheticTrackingFrameContribution] = []
    total_frames = len(frame_paths)
    accumulated_sum: object | None = None
    accumulated_weight: object | None = None
    extrema_stack: object | None = None
    gpu_warmup_summary: str | None = None

    if array_backend.is_gpu:
        warmed_before = _FULL_FRAME_GPU_BACKEND_WARMED
        if warmed_before:
            gpu_warmup_summary = "GPU warm-up cache: Ready for this session."
        else:
            _emit_progress(progress_callback, 0, total_frames, "Warming up GPU backend for full-frame Synthetic Track...")
        try:
            _warm_up_full_frame_gpu_backend(array_backend=array_backend)
        except Exception as exc:
            array_backend = NUMPY_ARRAY_BACKEND
            backend_note = f"Using CPU fallback: GPU warm-up failed ({_summarize_backend_error(exc)})."
            gpu_warmup_summary = None
        else:
            if not warmed_before:
                gpu_warmup_summary = "GPU warm-up cache: Prepared during this run."

    worker_count = _resolve_synthetic_tracking_worker_count(total_frames, max_parallel_workers, full_frame_mode=True)
    if array_backend.is_gpu:
        worker_count = 1
    use_streaming_average = resolved_rejection_mode == "no_rejection" and resolved_integration_mode in {"average", "mean"}
    use_streaming_extrema = resolved_rejection_mode == "no_rejection" and resolved_integration_mode in {"min", "max"}
    needs_frame_weight_metrics = resolved_integration_mode == "mean"
    if use_streaming_average and worker_count > 1:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(
                    _prepare_full_frame_synthetic_tracking_frame,
                    index=index,
                    source_path=source_path,
                    frame_observation_times=frame_observation_times,
                    reference_time=reference_time,
                    motion_x_px_per_hour=motion_x_px_per_hour,
                    motion_y_px_per_hour=motion_y_px_per_hour,
                    total_frames=total_frames,
                    array_backend=array_backend,
                    estimate_weight_metrics=needs_frame_weight_metrics,
                ): index
                for index, source_path in enumerate(frame_paths, start=1)
            }
            contributions_by_index: dict[int, SyntheticTrackingFrameContribution] = {}
            for completed_count, future in enumerate(as_completed(futures), start=1):
                prepared = future.result()
                if prepared.shifted_frame is not None:
                    frame_weight = _synthetic_tracking_frame_weight_value(
                        prepared.frame_weight_metrics,
                        resolved_integration_mode,
                        resolved_weight_mode,
                    )
                    accumulated_sum, accumulated_weight = _accumulate_average_stack_frame_array_backend(
                        accumulated_sum,
                        accumulated_weight,
                        prepared.shifted_frame,
                        frame_weight=frame_weight,
                        array_backend=array_backend,
                        frame_has_only_finite_values=True,
                    )
                contributions_by_index[prepared.index] = prepared.contribution
                _emit_progress(progress_callback, completed_count, total_frames, prepared.progress_message)
            frame_contributions = [contributions_by_index[index] for index in range(1, total_frames + 1)]
    elif use_streaming_average:
        for display_index, source_path in enumerate(frame_paths, start=1):
            prepared = _prepare_full_frame_synthetic_tracking_frame(
                index=display_index,
                source_path=source_path,
                frame_observation_times=frame_observation_times,
                reference_time=reference_time,
                motion_x_px_per_hour=motion_x_px_per_hour,
                motion_y_px_per_hour=motion_y_px_per_hour,
                total_frames=total_frames,
                array_backend=array_backend,
                estimate_weight_metrics=needs_frame_weight_metrics,
            )
            if prepared.shifted_frame is not None:
                frame_weight = _synthetic_tracking_frame_weight_value(
                    prepared.frame_weight_metrics,
                    resolved_integration_mode,
                    resolved_weight_mode,
                )
                accumulated_sum, accumulated_weight = _accumulate_average_stack_frame_array_backend(
                    accumulated_sum,
                    accumulated_weight,
                    prepared.shifted_frame,
                    frame_weight=frame_weight,
                    array_backend=array_backend,
                    frame_has_only_finite_values=True,
                )
            frame_contributions.append(prepared.contribution)
            _emit_progress(progress_callback, display_index, total_frames, prepared.progress_message)
    elif use_streaming_extrema:
        for display_index, source_path in enumerate(frame_paths, start=1):
            prepared = _prepare_full_frame_synthetic_tracking_frame(
                index=display_index,
                source_path=source_path,
                frame_observation_times=frame_observation_times,
                reference_time=reference_time,
                motion_x_px_per_hour=motion_x_px_per_hour,
                motion_y_px_per_hour=motion_y_px_per_hour,
                total_frames=total_frames,
                array_backend=array_backend,
                estimate_weight_metrics=needs_frame_weight_metrics,
            )
            if prepared.shifted_frame is not None:
                extrema_stack = _accumulate_extrema_stack_frame_array_backend(
                    extrema_stack,
                    prepared.shifted_frame,
                    use_max=resolved_integration_mode == "max",
                    array_backend=array_backend,
                    frame_has_only_finite_values=True,
                )
            frame_contributions.append(prepared.contribution)
            _emit_progress(progress_callback, display_index, total_frames, prepared.progress_message)
    else:
        shifted_frames: list[np.ndarray] = []
        frame_weight_metrics: list[SyntheticTrackingFrameWeightMetrics] = []
        prepared_frames: list[_PreparedFullFrameSyntheticTrackingFrame]
        if worker_count > 1:
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                futures = {
                    executor.submit(
                        _prepare_full_frame_synthetic_tracking_frame,
                        index=index,
                        source_path=source_path,
                        frame_observation_times=frame_observation_times,
                        reference_time=reference_time,
                        motion_x_px_per_hour=motion_x_px_per_hour,
                        motion_y_px_per_hour=motion_y_px_per_hour,
                        total_frames=total_frames,
                        array_backend=array_backend,
                        estimate_weight_metrics=needs_frame_weight_metrics,
                    ): index
                    for index, source_path in enumerate(frame_paths, start=1)
                }
                prepared_by_index: dict[int, _PreparedFullFrameSyntheticTrackingFrame] = {}
                for completed_count, future in enumerate(as_completed(futures), start=1):
                    prepared = future.result()
                    prepared_by_index[prepared.index] = prepared
                    _emit_progress(progress_callback, completed_count, total_frames, prepared.progress_message)
                prepared_frames = [prepared_by_index[index] for index in range(1, total_frames + 1)]
        else:
            prepared_frames = [
            _prepare_full_frame_synthetic_tracking_frame(
                index=index,
                source_path=source_path,
                frame_observation_times=frame_observation_times,
                reference_time=reference_time,
                motion_x_px_per_hour=motion_x_px_per_hour,
                motion_y_px_per_hour=motion_y_px_per_hour,
                total_frames=total_frames,
                array_backend=array_backend,
                estimate_weight_metrics=needs_frame_weight_metrics,
            )
            for index, source_path in enumerate(frame_paths, start=1)
            ]

        for display_index, prepared in enumerate(prepared_frames, start=1):
            if prepared.shifted_frame is not None:
                shifted_frames.append(prepared.shifted_frame)
                if prepared.frame_weight_metrics is not None:
                    frame_weight_metrics.append(prepared.frame_weight_metrics)
            frame_contributions.append(prepared.contribution)
            if worker_count <= 1:
                _emit_progress(progress_callback, display_index, total_frames, prepared.progress_message)

        if not shifted_frames:
            raise ValueError("Synthetic tracking could not build a stack from the available frames.")

        stacked_data = _combine_stack_patches(
            shifted_frames,
            frame_weight_metrics=frame_weight_metrics,
            integration_mode=resolved_integration_mode,
            weight_mode=resolved_weight_mode,
            rejection_mode=resolved_rejection_mode,
        )

    if use_streaming_average:
        if accumulated_sum is None or accumulated_weight is None:
            raise ValueError("Synthetic tracking could not build a stack from the available frames.")
        stacked_data = _finalize_average_stack_array_backend(accumulated_sum, accumulated_weight, array_backend=array_backend)
    elif use_streaming_extrema:
        if extrema_stack is None:
            raise ValueError("Synthetic tracking could not build a stack from the available frames.")
        stacked_data = extrema_stack

    stacked_data = _materialize_backend_array(stacked_data, array_backend=array_backend)
    peak_coordinates = _global_peak_coordinates(stacked_data)
    if peak_coordinates is None:
        raise ValueError("Synthetic tracking could not measure a peak in the stacked full-image result.")
    center_x, center_y = peak_coordinates
    measured_x, measured_y, match_offset_px, local_snr, local_peak_value, local_flux = measure_synthetic_tracking_peak(
        stacked_data,
        center_x,
        center_y,
        anchor_x=center_x,
        anchor_y=center_y,
        search_radius=4,
    )
    used_frame_count = sum(1 for contribution in frame_contributions if contribution.used)
    skipped_frame_count = len(frame_contributions) - used_frame_count
    summary_text = format_synthetic_tracking_summary(
        used_frame_count=used_frame_count,
        total_frame_count=len(frame_contributions),
        local_snr=local_snr,
        match_offset_px=match_offset_px,
        motion_px_per_hour=float(motion_px_per_hour),
        motion_arcsec_per_hour=motion_arcsec_per_hour,
        motion_angle_deg=float(motion_angle_deg),
    )
    compute_backend_summary = "Compute backend: CPU"
    if array_backend.is_gpu:
        compute_backend_summary = "Compute backend: GPU (CuPy full-frame no-rejection stack)"
    elif backend_note:
        compute_backend_summary = f"Compute backend: CPU fallback. {backend_note}"
    return SyntheticTrackingResult(
        reference_path=reference_path,
        stacked_data=stacked_data,
        center_x=float(center_x),
        center_y=float(center_y),
        measured_x=measured_x,
        measured_y=measured_y,
        match_offset_px=match_offset_px,
        local_snr=local_snr,
        local_peak_value=local_peak_value,
        local_flux=local_flux,
        used_frame_count=used_frame_count,
        skipped_frame_count=skipped_frame_count,
        crop_radius=0,
        frame_contributions=tuple(frame_contributions),
        summary_text=summary_text,
        compute_backend_summary=compute_backend_summary,
        gpu_warmup_summary=gpu_warmup_summary,
        linear_stacked_data=stacked_data,
        motion_px_per_hour=float(motion_px_per_hour),
        motion_arcsec_per_hour=motion_arcsec_per_hour,
        motion_angle_deg=float(motion_angle_deg),
        full_frame_mode=True,
    )


def _collapsed_image_plane(image_data: np.ndarray) -> np.ndarray:
    data = np.asarray(image_data, dtype=float)
    if data.ndim == 2:
        return data
    if data.ndim == 3 and data.shape[-1] in {1, 3, 4}:
        return np.nanmean(data, axis=-1)
    if data.ndim == 3 and data.shape[0] in {1, 3, 4}:
        return np.nanmean(np.moveaxis(data, 0, -1), axis=-1)
    raise ValueError("Synthetic tracking supports only 2D or simple RGB/RGBA image data.")


def _clean_image_data(image_data: np.ndarray) -> np.ndarray:
    finite_pixels = image_data[np.isfinite(image_data)]
    if finite_pixels.size == 0:
        raise ValueError("Image contains no finite pixel values.")
    return np.nan_to_num(
        np.asarray(image_data, dtype=float),
        nan=float(np.median(finite_pixels)),
        posinf=float(np.max(finite_pixels)),
        neginf=float(np.min(finite_pixels)),
    )


def _sample_centered_patch(image_data: np.ndarray, center_x: float, center_y: float, crop_radius: int) -> np.ndarray:
    offsets = np.arange(-crop_radius, crop_radius + 1, dtype=float)
    sample_x = center_x + offsets[None, :]
    sample_y = center_y + offsets[:, None]
    return _bilinear_sample(image_data, sample_x, sample_y)


def _shift_image_with_zero_fill(
    image_data: np.ndarray,
    shift_x_px: float,
    shift_y_px: float,
    *,
    sample_x_grid: np.ndarray,
    sample_y_grid: np.ndarray,
) -> np.ndarray:
    return _bilinear_shift_sample(image_data, sample_x_grid + float(shift_x_px), sample_y_grid + float(shift_y_px))


def _shift_image_with_zero_fill_array_backend(
    image_data: np.ndarray,
    shift_x_px: float,
    shift_y_px: float,
    *,
    array_backend: SyntheticTrackingArrayBackend,
) -> object:
    xp = array_backend.xp
    image = array_backend.asarray(image_data, dtype=xp.float32)
    sample_y_grid, sample_x_grid = xp.indices(image.shape, dtype=xp.float32)
    return _bilinear_shift_sample_array_backend(
        image,
        sample_x_grid + float(shift_x_px),
        sample_y_grid + float(shift_y_px),
        array_backend=array_backend,
    )


def _bilinear_sample(image_data: np.ndarray, sample_x: np.ndarray, sample_y: np.ndarray) -> np.ndarray:
    sample_x, sample_y = np.broadcast_arrays(sample_x, sample_y)
    height, width = image_data.shape
    x0 = np.floor(sample_x).astype(int)
    y0 = np.floor(sample_y).astype(int)
    x1 = x0 + 1
    y1 = y0 + 1
    valid = (x0 >= 0) & (y0 >= 0) & (x1 < width) & (y1 < height)
    sampled = np.full(sample_x.shape, np.nan, dtype=float)
    if not np.any(valid):
        return sampled

    x_fraction = sample_x - x0
    y_fraction = sample_y - y0
    top_left = image_data[y0[valid], x0[valid]]
    top_right = image_data[y0[valid], x1[valid]]
    bottom_left = image_data[y1[valid], x0[valid]]
    bottom_right = image_data[y1[valid], x1[valid]]
    top = top_left * (1.0 - x_fraction[valid]) + top_right * x_fraction[valid]
    bottom = bottom_left * (1.0 - x_fraction[valid]) + bottom_right * x_fraction[valid]
    sampled[valid] = top * (1.0 - y_fraction[valid]) + bottom * y_fraction[valid]
    return sampled


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
    shifted[valid] = top * (1.0 - y_fraction[valid]) + bottom * y_fraction[valid]
    return shifted


def _bilinear_shift_sample_array_backend(
    image_data: object,
    sample_x: object,
    sample_y: object,
    *,
    array_backend: SyntheticTrackingArrayBackend,
) -> object:
    xp = array_backend.xp
    sample_x, sample_y = xp.broadcast_arrays(sample_x, sample_y)
    height, width = image_data.shape
    x0 = xp.floor(sample_x).astype(int)
    y0 = xp.floor(sample_y).astype(int)
    x1 = x0 + 1
    y1 = y0 + 1
    valid = (x0 >= 0) & (y0 >= 0) & (x1 < width) & (y1 < height)
    shifted = xp.zeros(sample_x.shape, dtype=xp.float32)
    if not _array_backend_has_any_true(valid, array_backend=array_backend):
        return shifted

    x_fraction = sample_x - x0
    y_fraction = sample_y - y0
    top_left = image_data[y0[valid], x0[valid]]
    top_right = image_data[y0[valid], x1[valid]]
    bottom_left = image_data[y1[valid], x0[valid]]
    bottom_right = image_data[y1[valid], x1[valid]]
    top = top_left * (1.0 - x_fraction[valid]) + top_right * x_fraction[valid]
    bottom = bottom_left * (1.0 - x_fraction[valid]) + bottom_right * x_fraction[valid]
    shifted[valid] = top * (1.0 - y_fraction[valid]) + bottom * y_fraction[valid]
    return shifted


def _global_peak_coordinates(image_data: np.ndarray) -> tuple[float, float] | None:
    finite = np.isfinite(image_data)
    if not np.any(finite):
        return None
    peak_index = int(np.nanargmax(np.where(finite, image_data, np.nan)))
    peak_y, peak_x = np.unravel_index(peak_index, image_data.shape)
    return float(peak_x), float(peak_y)


def measure_synthetic_tracking_peak(
    image_data: np.ndarray,
    predicted_x: float,
    predicted_y: float,
    *,
    anchor_x: float | None = None,
    anchor_y: float | None = None,
    search_radius: int = 6,
) -> tuple[float | None, float | None, float | None, float | None, float | None, float | None]:
    if image_data.ndim != 2 or image_data.size == 0:
        return None, None, None, None, None, None
    search_radius = max(0, int(search_radius))
    x_center = int(round(predicted_x if anchor_x is None else anchor_x))
    y_center = int(round(predicted_y if anchor_y is None else anchor_y))
    x0 = max(0, x_center - search_radius)
    x1 = min(image_data.shape[1], x_center + search_radius + 1)
    y0 = max(0, y_center - search_radius)
    y1 = min(image_data.shape[0], y_center + search_radius + 1)
    if x0 >= x1 or y0 >= y1:
        return None, None, None, None, None, None

    local = np.asarray(image_data[y0:y1, x0:x1], dtype=float)
    finite = local[np.isfinite(local)]
    if finite.size == 0:
        return None, None, None, None, None, None
    background = float(np.nanmedian(finite))
    mad = float(np.nanmedian(np.abs(finite - background)))
    sigma = max(1e-6, 1.4826 * mad)
    peak_index = int(np.nanargmax(local))
    peak_y_local, peak_x_local = np.unravel_index(peak_index, local.shape)
    measured_x = float(x0 + peak_x_local)
    measured_y = float(y0 + peak_y_local)
    peak_value = float(local[peak_y_local, peak_x_local])
    local_snr = (peak_value - background) / sigma
    match_offset_px = float(math.hypot(measured_x - predicted_x, measured_y - predicted_y))
    aperture_radius = 3.5
    yy, xx = np.indices(local.shape, dtype=float)
    aperture_mask = ((xx - peak_x_local) ** 2 + (yy - peak_y_local) ** 2) <= (aperture_radius ** 2)
    local_flux = float(np.nansum(local[aperture_mask] - background)) if np.any(aperture_mask) else None
    return measured_x, measured_y, match_offset_px, float(local_snr), peak_value, local_flux


def format_synthetic_tracking_summary(
    *,
    used_frame_count: int,
    total_frame_count: int,
    local_snr: float | None,
    match_offset_px: float | None,
    motion_px_per_hour: float | None = None,
    motion_arcsec_per_hour: float | None = None,
    motion_angle_deg: float | None = None,
) -> str:
    summary = (
        f"Synthetic-tracking stack built from {used_frame_count} of {total_frame_count} frame(s). "
        f"Stacked SNR: {_format_optional_float(local_snr, 2)}. "
        f"Center offset: {_format_optional_float(match_offset_px, 2)} px."
    )
    if motion_px_per_hour is not None and motion_angle_deg is not None:
        motion_text = f"{_format_optional_float(motion_px_per_hour, 2)} px/h"
        if motion_arcsec_per_hour is not None:
            motion_text += f" ({_format_optional_float(motion_arcsec_per_hour, 2)} arcsec/h)"
        summary += (
            " "
            f"Stacking motion: {motion_text} "
            f"at {_format_optional_float(motion_angle_deg, 1)} deg."
        )
    return summary


def _infer_synthetic_tracking_motion(
    frame_contributions: list[SyntheticTrackingFrameContribution],
) -> tuple[float | None, float | None]:
    used_contributions = [item for item in frame_contributions if item.used and item.observation_time is not None and item.predicted_x is not None and item.predicted_y is not None]
    if len(used_contributions) < 2:
        return None, None
    first = used_contributions[0]
    last = used_contributions[-1]
    delta_seconds = (last.observation_time - first.observation_time).total_seconds()
    if delta_seconds <= 0.0:
        return None, None
    delta_x = float(last.predicted_x) - float(first.predicted_x)
    delta_y = float(last.predicted_y) - float(first.predicted_y)
    displacement_px = float(math.hypot(delta_x, delta_y))
    if displacement_px <= 1e-9:
        return 0.0, 0.0
    motion_px_per_hour = displacement_px * (3600.0 / delta_seconds)
    motion_angle_deg = math.degrees(math.atan2(delta_y, delta_x)) % 360.0
    return float(motion_px_per_hour), float(motion_angle_deg)


def _combine_stack_patches(
    stack_patches: list[np.ndarray],
    *,
    frame_weight_metrics: list[SyntheticTrackingFrameWeightMetrics],
    integration_mode: str,
    weight_mode: str,
    rejection_mode: str,
) -> np.ndarray:
    if not stack_patches:
        raise ValueError("Synthetic tracking requires at least one aligned frame.")
    if rejection_mode == "no_rejection":
        if integration_mode in {"average", "mean"}:
            accumulated_sum: np.ndarray | None = None
            accumulated_weight: np.ndarray | None = None
            for stack_patch, metrics in zip(stack_patches, frame_weight_metrics, strict=False):
                frame_weight = _synthetic_tracking_frame_weight_value(metrics, integration_mode, weight_mode)
                accumulated_sum, accumulated_weight = _accumulate_average_stack_frame(
                    accumulated_sum,
                    accumulated_weight,
                    stack_patch,
                    frame_weight=frame_weight,
                )
            if accumulated_sum is None or accumulated_weight is None:
                raise ValueError("Synthetic tracking requires at least one aligned frame.")
            return _finalize_average_stack(accumulated_sum, accumulated_weight)
        if integration_mode == "min":
            return np.asarray(np.nanmin(np.stack(stack_patches, axis=0), axis=0), dtype=np.float32)
        return np.asarray(np.nanmax(np.stack(stack_patches, axis=0), axis=0), dtype=np.float32)

    _ensure_sigma_clipped_stack_fits_memory(stack_patches, rejection_mode=rejection_mode, integration_mode=integration_mode)
    stacked_cube = np.stack(stack_patches, axis=0)
    valid_mask = np.isfinite(stacked_cube)
    working_cube, working_mask = _apply_synthetic_tracking_rejection(stacked_cube, valid_mask, rejection_mode=rejection_mode)
    frame_weights = _synthetic_tracking_frame_weights(
        frame_weight_metrics,
        integration_mode=integration_mode,
        weight_mode=weight_mode,
    )
    return _reduce_stack_cube(
        working_cube,
        valid_mask=working_mask,
        integration_mode=integration_mode,
        frame_weights=frame_weights,
    )


def _ensure_sigma_clipped_stack_fits_memory(
    stack_patches: list[np.ndarray],
    *,
    rejection_mode: str,
    integration_mode: str,
) -> None:
    if not stack_patches:
        return
    estimated_bytes = int(len(stack_patches)) * int(stack_patches[0].size) * np.dtype(np.float32).itemsize
    if estimated_bytes <= _MAX_SIGMA_CLIPPED_STACK_BYTES:
        return
    estimated_gib = estimated_bytes / float(1024 ** 3)
    if rejection_mode == "sigma_clipping" and integration_mode == "average":
        operation_label = "sigma-clipped mean"
        recommendation = "Use Mean or a smaller crop radius."
    else:
        operation_label = rejection_mode.replace("_", " ")
        recommendation = "Use a lighter rejection mode or a smaller crop radius."
    raise ValueError(
        f"Synthetic tracking {operation_label} would require about {estimated_gib:.2f} GiB of working memory. {recommendation}"
    )


def _accumulate_average_stack_frame(
    accumulated_sum: np.ndarray | None,
    accumulated_weight: np.ndarray | None,
    frame_data: np.ndarray,
    *,
    frame_weight: float,
) -> tuple[np.ndarray, np.ndarray]:
    frame = np.asarray(frame_data, dtype=np.float32)
    if accumulated_sum is None or accumulated_weight is None:
        accumulated_sum = np.zeros(frame.shape, dtype=np.float32)
        accumulated_weight = np.zeros(frame.shape, dtype=np.float32)
    valid = np.isfinite(frame)
    if np.any(valid):
        weight_value = max(0.0, float(frame_weight))
        accumulated_sum[valid] += frame[valid] * weight_value
        accumulated_weight[valid] += weight_value
    return accumulated_sum, accumulated_weight


def _accumulate_extrema_stack_frame(
    extrema_stack: np.ndarray | None,
    frame_data: np.ndarray,
    *,
    use_max: bool,
) -> np.ndarray:
    frame = np.asarray(frame_data, dtype=np.float32)
    if extrema_stack is None:
        return np.asarray(frame, dtype=np.float32)
    valid = np.isfinite(frame)
    if not np.any(valid):
        return extrema_stack
    existing = np.isfinite(extrema_stack)
    replace_mask = valid & (~existing)
    if use_max:
        replace_mask |= valid & existing & (frame > extrema_stack)
    else:
        replace_mask |= valid & existing & (frame < extrema_stack)
    updated = np.asarray(extrema_stack, dtype=np.float32, copy=True)
    updated[replace_mask] = frame[replace_mask]
    return updated


def _finalize_average_stack(accumulated_sum: np.ndarray, accumulated_weight: np.ndarray) -> np.ndarray:
    result = np.full(accumulated_sum.shape, np.nan, dtype=np.float32)
    valid = accumulated_weight > 0
    if np.any(valid):
        result[valid] = accumulated_sum[valid] / accumulated_weight[valid]
    return result


def _resolve_synthetic_tracking_stack_modes(
    *,
    combine_mode: str | None,
    integration_mode: str | None,
    weight_mode: str | None,
    rejection_mode: str | None,
) -> tuple[str, str, str]:
    resolved_integration_mode = str(integration_mode or "").strip().lower()
    resolved_weight_mode = str(weight_mode or "").strip().lower()
    resolved_rejection_mode = str(rejection_mode or "").strip().lower()
    if not resolved_integration_mode or not resolved_rejection_mode:
        legacy_integration, legacy_weight, legacy_rejection = _legacy_synthetic_tracking_stack_modes(combine_mode)
        if not resolved_integration_mode:
            resolved_integration_mode = legacy_integration
        if not resolved_weight_mode:
            resolved_weight_mode = legacy_weight
        if not resolved_rejection_mode:
            resolved_rejection_mode = legacy_rejection
    if resolved_integration_mode not in _SYNTHETIC_TRACKING_INTEGRATION_MODES:
        resolved_integration_mode = "average"
    if resolved_weight_mode not in _SYNTHETIC_TRACKING_WEIGHT_MODES:
        resolved_weight_mode = "psf_signal_weight"
    if resolved_rejection_mode not in _SYNTHETIC_TRACKING_REJECTION_MODES:
        resolved_rejection_mode = "no_rejection"
    return resolved_integration_mode, resolved_weight_mode, resolved_rejection_mode


def _legacy_synthetic_tracking_stack_modes(combine_mode: str | None) -> tuple[str, str, str]:
    normalized = str(combine_mode or "mean").strip().lower()
    if normalized == "sigma_clipped_mean":
        return "average", "psf_signal_weight", "sigma_clipping"
    return "average", "psf_signal_weight", "no_rejection"


def _estimate_synthetic_tracking_frame_weight_metrics(
    frame_data: np.ndarray,
    *,
    center_x: float | None = None,
    center_y: float | None = None,
) -> SyntheticTrackingFrameWeightMetrics:
    finite = frame_data[np.isfinite(frame_data)]
    if finite.size == 0:
        return SyntheticTrackingFrameWeightMetrics(1.0, 1.0, 1.0, 1.0)
    background = float(np.nanmedian(finite))
    sigma = max(1.0e-6, 1.4826 * float(np.nanmedian(np.abs(finite - background))))
    positive_signal = np.maximum(finite - background, 0.0)
    average_signal_strength = float(np.nanmean(positive_signal)) if np.any(positive_signal > 0.0) else max(0.0, float(np.nanmax(finite) - background))
    snr = max(0.0, float(np.nanmax(finite) - background) / sigma)
    psf_signal_weight = average_signal_strength
    psf_snr = snr
    if center_x is not None and center_y is not None:
        measured_x, measured_y, _match_offset_px, local_snr, local_peak_value, local_flux = measure_synthetic_tracking_peak(
            frame_data,
            float(center_x),
            float(center_y),
            anchor_x=float(center_x),
            anchor_y=float(center_y),
            search_radius=max(2, min(6, int(min(frame_data.shape) / 2))),
        )
        del measured_x, measured_y
        if local_flux is not None and np.isfinite(local_flux):
            psf_signal_weight = max(0.0, float(local_flux))
        elif local_peak_value is not None and np.isfinite(local_peak_value):
            psf_signal_weight = max(0.0, float(local_peak_value) - background)
        if local_snr is not None and np.isfinite(local_snr):
            psf_snr = max(0.0, float(local_snr))
    return SyntheticTrackingFrameWeightMetrics(
        psf_signal_weight=max(1.0e-6, psf_signal_weight),
        psf_snr=max(1.0e-6, psf_snr),
        snr=max(1.0e-6, snr),
        average_signal_strength=max(1.0e-6, average_signal_strength),
    )


def _estimate_full_frame_weight_metrics_array_backend(
    frame_data: object,
    *,
    array_backend: SyntheticTrackingArrayBackend,
) -> SyntheticTrackingFrameWeightMetrics:
    xp = array_backend.xp
    finite = frame_data[xp.isfinite(frame_data)]
    if int(finite.size) == 0:
        return SyntheticTrackingFrameWeightMetrics(1.0, 1.0, 1.0, 1.0)
    background = _array_backend_scalar_to_float(xp.median(finite), array_backend=array_backend)
    sigma = max(1.0e-6, 1.4826 * _array_backend_scalar_to_float(xp.median(xp.abs(finite - background)), array_backend=array_backend))
    positive_signal = xp.maximum(finite - background, 0.0)
    if _array_backend_has_any_true(positive_signal > 0.0, array_backend=array_backend):
        average_signal_strength = _array_backend_scalar_to_float(xp.mean(positive_signal), array_backend=array_backend)
    else:
        average_signal_strength = max(0.0, _array_backend_scalar_to_float(xp.max(finite), array_backend=array_backend) - background)
    snr = max(0.0, (_array_backend_scalar_to_float(xp.max(finite), array_backend=array_backend) - background) / sigma)
    return SyntheticTrackingFrameWeightMetrics(
        psf_signal_weight=max(1.0e-6, average_signal_strength),
        psf_snr=max(1.0e-6, snr),
        snr=max(1.0e-6, snr),
        average_signal_strength=max(1.0e-6, average_signal_strength),
    )


def _synthetic_tracking_frame_weight_value(
    metrics: SyntheticTrackingFrameWeightMetrics | None,
    integration_mode: str,
    weight_mode: str,
) -> float:
    if integration_mode != "mean" or metrics is None:
        return 1.0
    value = getattr(metrics, weight_mode, 1.0)
    if not np.isfinite(value) or value <= 0.0:
        return 1.0
    return float(value)


def _synthetic_tracking_frame_weights(
    frame_weight_metrics: list[SyntheticTrackingFrameWeightMetrics],
    *,
    integration_mode: str,
    weight_mode: str,
) -> np.ndarray:
    if integration_mode != "mean":
        return np.ones(len(frame_weight_metrics), dtype=np.float32)
    weights = np.asarray([
        _synthetic_tracking_frame_weight_value(metrics, integration_mode, weight_mode)
        for metrics in frame_weight_metrics
    ], dtype=np.float32)
    if not np.any(np.isfinite(weights) & (weights > 0.0)):
        return np.ones(len(frame_weight_metrics), dtype=np.float32)
    weights[~np.isfinite(weights) | (weights <= 0.0)] = 1.0
    return weights


def _apply_synthetic_tracking_rejection(
    stacked_cube: np.ndarray,
    valid_mask: np.ndarray,
    *,
    rejection_mode: str,
) -> tuple[np.ndarray, np.ndarray]:
    if rejection_mode == "min_max":
        keep_mask = np.array(valid_mask, copy=True)
        valid_count = np.sum(valid_mask, axis=0)
        eligible = valid_count > 2
        if np.any(eligible):
            yy, xx = np.indices(stacked_cube.shape[1:])
            low_fill = np.where(valid_mask, stacked_cube, np.inf)
            high_fill = np.where(valid_mask, stacked_cube, -np.inf)
            min_indices = np.argmin(low_fill, axis=0)
            max_indices = np.argmax(high_fill, axis=0)
            keep_mask[min_indices[eligible], yy[eligible], xx[eligible]] = False
            keep_mask[max_indices[eligible], yy[eligible], xx[eligible]] = False
        return stacked_cube, keep_mask
    if rejection_mode in {"sigma_clipping", "averaged_sigma_clipping"}:
        clipped = sigma_clip(
            stacked_cube,
            sigma=3.0,
            axis=0,
            masked=True,
            cenfunc=("mean" if rejection_mode == "averaged_sigma_clipping" else "median"),
            stdfunc=mad_std,
            maxiters=5,
        )
        return stacked_cube, valid_mask & ~np.ma.getmaskarray(clipped)
    if rejection_mode == "winsorized_sigma_clipping":
        clipped = sigma_clip(
            stacked_cube,
            sigma=3.0,
            axis=0,
            masked=True,
            cenfunc="median",
            stdfunc=mad_std,
            maxiters=5,
        )
        clip_mask = np.ma.getmaskarray(clipped)
        filled = np.asarray(clipped.filled(np.nan), dtype=np.float32)
        lower = np.nanmin(filled, axis=0)
        upper = np.nanmax(filled, axis=0)
        winsorized = np.asarray(stacked_cube, dtype=np.float32, copy=True)
        lower_broadcast = np.broadcast_to(lower, stacked_cube.shape)
        upper_broadcast = np.broadcast_to(upper, stacked_cube.shape)
        winsorized[clip_mask & (stacked_cube < lower_broadcast)] = lower_broadcast[clip_mask & (stacked_cube < lower_broadcast)]
        winsorized[clip_mask & (stacked_cube > upper_broadcast)] = upper_broadcast[clip_mask & (stacked_cube > upper_broadcast)]
        winsorized[~valid_mask] = np.nan
        return winsorized, valid_mask
    return stacked_cube, valid_mask


def _reduce_stack_cube(
    stacked_cube: np.ndarray,
    *,
    valid_mask: np.ndarray,
    integration_mode: str,
    frame_weights: np.ndarray,
) -> np.ndarray:
    working = np.where(valid_mask, stacked_cube, np.nan)
    if integration_mode == "min":
        return np.asarray(np.nanmin(working, axis=0), dtype=np.float32)
    if integration_mode == "max":
        return np.asarray(np.nanmax(working, axis=0), dtype=np.float32)
    weights = np.ones(stacked_cube.shape[0], dtype=np.float32) if integration_mode == "average" else np.asarray(frame_weights, dtype=np.float32)
    weights = np.where(np.isfinite(weights) & (weights > 0.0), weights, 1.0)
    weighted_cube = np.where(valid_mask, stacked_cube * weights[:, None, None], 0.0)
    weighted_sum = np.sum(weighted_cube, axis=0, dtype=np.float64)
    weight_sum = np.sum(np.where(valid_mask, weights[:, None, None], 0.0), axis=0, dtype=np.float64)
    result = np.full(stacked_cube.shape[1:], np.nan, dtype=np.float32)
    positive_weight = weight_sum > 0.0
    if np.any(positive_weight):
        result[positive_weight] = np.asarray(weighted_sum[positive_weight] / weight_sum[positive_weight], dtype=np.float32)
    return result


def _emit_progress(
    progress_callback: Callable[[int, int, str], None] | None,
    completed: int,
    total: int,
    message: str,
) -> None:
    if progress_callback is not None:
        progress_callback(completed, total, message)


def _accumulate_average_stack_frame_array_backend(
    accumulated_sum: object | None,
    accumulated_weight: object | None,
    frame_data: object,
    *,
    frame_weight: float,
    array_backend: SyntheticTrackingArrayBackend,
    frame_has_only_finite_values: bool = False,
) -> tuple[object, object]:
    xp = array_backend.xp
    frame = array_backend.asarray(frame_data, dtype=xp.float32)
    if accumulated_sum is None or accumulated_weight is None:
        accumulated_sum = xp.zeros(frame.shape, dtype=xp.float32)
        accumulated_weight = xp.zeros(frame.shape, dtype=xp.float32)
    weight_value = max(0.0, float(frame_weight))
    if frame_has_only_finite_values:
        accumulated_sum += frame * weight_value
        accumulated_weight += weight_value
        return accumulated_sum, accumulated_weight
    valid = xp.isfinite(frame)
    if _array_backend_has_any_true(valid, array_backend=array_backend):
        accumulated_sum[valid] += frame[valid] * weight_value
        accumulated_weight[valid] += weight_value
    return accumulated_sum, accumulated_weight


def _accumulate_extrema_stack_frame_array_backend(
    extrema_stack: object | None,
    frame_data: object,
    *,
    use_max: bool,
    array_backend: SyntheticTrackingArrayBackend,
    frame_has_only_finite_values: bool = False,
) -> object:
    xp = array_backend.xp
    frame = array_backend.asarray(frame_data, dtype=xp.float32)
    if extrema_stack is None:
        return xp.asarray(frame, dtype=xp.float32)
    if frame_has_only_finite_values:
        if use_max:
            return xp.maximum(extrema_stack, frame)
        return xp.minimum(extrema_stack, frame)
    valid = xp.isfinite(frame)
    if not _array_backend_has_any_true(valid, array_backend=array_backend):
        return extrema_stack
    existing = xp.isfinite(extrema_stack)
    replace_mask = valid & (~existing)
    if use_max:
        replace_mask |= valid & existing & (frame > extrema_stack)
    else:
        replace_mask |= valid & existing & (frame < extrema_stack)
    updated = xp.asarray(extrema_stack, dtype=xp.float32).copy()
    updated[replace_mask] = frame[replace_mask]
    return updated


def _finalize_average_stack_array_backend(
    accumulated_sum: object,
    accumulated_weight: object,
    *,
    array_backend: SyntheticTrackingArrayBackend,
) -> object:
    xp = array_backend.xp
    result = xp.full(accumulated_sum.shape, xp.nan, dtype=xp.float32)
    valid = accumulated_weight > 0
    if _array_backend_has_any_true(valid, array_backend=array_backend):
        result[valid] = accumulated_sum[valid] / accumulated_weight[valid]
    return result


def _materialize_backend_array(array_data: object, *, array_backend: SyntheticTrackingArrayBackend) -> np.ndarray:
    return np.nan_to_num(array_backend.to_numpy(array_data, dtype=np.float32), nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32, copy=False)


def _array_backend_scalar_to_float(value: object, *, array_backend: SyntheticTrackingArrayBackend) -> float:
    scalar = array_backend.to_numpy(value).reshape(())
    return float(scalar.item())


def _array_backend_has_any_true(mask: object, *, array_backend: SyntheticTrackingArrayBackend) -> bool:
    scalar = array_backend.to_numpy(array_backend.xp.any(mask)).reshape(())
    return bool(scalar.item())


def _warm_up_full_frame_gpu_backend(*, array_backend: SyntheticTrackingArrayBackend) -> None:
    global _FULL_FRAME_GPU_BACKEND_WARMED
    if _FULL_FRAME_GPU_BACKEND_WARMED or not array_backend.is_gpu:
        return
    tiny_frame = np.zeros((8, 8), dtype=np.float32)
    tiny_frame[3:5, 3:5] = 1.0
    shifted_frame = _shift_image_with_zero_fill_array_backend(
        tiny_frame,
        0.25,
        -0.25,
        array_backend=array_backend,
    )
    _estimate_full_frame_weight_metrics_array_backend(shifted_frame, array_backend=array_backend)
    accumulated_sum, accumulated_weight = _accumulate_average_stack_frame_array_backend(
        None,
        None,
        shifted_frame,
        frame_weight=1.0,
        array_backend=array_backend,
    )
    _ = _finalize_average_stack_array_backend(accumulated_sum, accumulated_weight, array_backend=array_backend)
    _ = _accumulate_extrema_stack_frame_array_backend(
        None,
        shifted_frame,
        use_max=True,
        array_backend=array_backend,
    )
    _materialize_backend_array(shifted_frame, array_backend=array_backend)
    _FULL_FRAME_GPU_BACKEND_WARMED = True


def _summarize_backend_error(exc: Exception) -> str:
    error_text = " ".join(str(exc).split())
    if len(error_text) > 220:
        error_text = f"{error_text[:217]}..."
    return error_text


def _format_optional_float(value: float | None, decimals: int) -> str:
    if value is None or not np.isfinite(value):
        return "-"
    return f"{float(value):.{decimals}f}"