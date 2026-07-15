from __future__ import annotations

import argparse
import json
import math
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from time import perf_counter

import numpy as np

from photometry_app.core.image_io import read_header
from photometry_app.core.scanner import date_obs_has_explicit_timezone, filename_observation_timestamp, inspect_fits_file, parse_observation_timestamp
from photometry_app.core.settings import AppSettings, resolve_shared_parallel_workers
from photometry_app.core.solar_system import detect_known_solar_system_objects, measure_detections_in_frame
from photometry_app.core.synthetic_tracking import build_synthetic_tracked_full_frame_stack


def _dataset_frame_paths(dataset_path: Path) -> list[Path]:
    return sorted(path for path in dataset_path.iterdir() if path.is_file() and path.suffix.lower() in {".fit", ".fits"})


def _alternate_observation_times(source_image: Path, primary_time: datetime, settings: AppSettings) -> tuple[datetime, ...]:
    candidates: list[datetime] = []
    try:
        header = read_header(source_image)
        date_obs_value = header.get("DATE-OBS")
    except Exception:
        date_obs_value = None
    if date_obs_value is not None and not date_obs_has_explicit_timezone(date_obs_value):
        utc_header_time = parse_observation_timestamp(date_obs_value, observation_timezone="UTC")
        if utc_header_time is not None and abs((utc_header_time - primary_time).total_seconds()) >= 60.0:
            candidates.append(utc_header_time)
    filename_time = filename_observation_timestamp(source_image, observation_timezone=settings.observation_timezone)
    if filename_time is not None and abs((filename_time - primary_time).total_seconds()) >= 60.0:
        if all(abs((filename_time - existing).total_seconds()) >= 60.0 for existing in candidates):
            candidates.append(filename_time)
    return tuple(candidates)


def _scan_frame_metadata(frame_paths: list[Path], *, object_name: str, settings: AppSettings) -> dict[str, object]:
    frame_metadata: dict[str, object] = {}
    for frame_path in frame_paths:
        scan = inspect_fits_file(frame_path, object_name, observation_timezone=settings.observation_timezone)
        frame_metadata[str(frame_path.resolve())] = scan.metadata
    return frame_metadata


def _frame_midpoint_time(metadata, prediction_time: datetime, exposure_seconds_fallback: float | None) -> tuple[datetime | None, float | None]:
    if metadata is None or metadata.date_obs is None:
        return prediction_time, exposure_seconds_fallback
    exposure_seconds = exposure_seconds_fallback if metadata.exposure_seconds is None else metadata.exposure_seconds
    if exposure_seconds is None:
        return metadata.date_obs, exposure_seconds
    return metadata.date_obs + timedelta(seconds=max(0.0, float(exposure_seconds)) / 2.0), exposure_seconds


def _prepare_dataset_tracking_context(dataset_path: Path, target_name: str, frame_limit: int | None) -> dict[str, object]:
    root_path = Path.cwd()
    settings = AppSettings.from_root(root_path)
    frame_paths = _dataset_frame_paths(dataset_path)
    if frame_limit is not None:
        frame_paths = frame_paths[: max(1, int(frame_limit))]
    if not frame_paths:
        raise RuntimeError(f"No FITS frames found under {dataset_path}.")

    frame_metadata = _scan_frame_metadata(frame_paths, object_name=target_name, settings=settings)
    reference_path = frame_paths[0]
    reference_key = str(reference_path.resolve())
    reference_metadata = frame_metadata.get(reference_key)
    reference_time = None if reference_metadata is None else reference_metadata.date_obs
    if reference_time is None:
        raise RuntimeError("Reference frame has no observation time.")

    alternate_times = _alternate_observation_times(reference_path, reference_time, settings)
    detection_result = detect_known_solar_system_objects(
        reference_path,
        observation_time=reference_time,
        alternate_observation_times=alternate_times,
        settings=settings,
        exposure_seconds=reference_metadata.exposure_seconds,
        observer_latitude_deg=settings.observing_site_latitude_deg,
        observer_longitude_deg=settings.observing_site_longitude_deg,
        observer_elevation_m=settings.observing_site_elevation_m,
        filter_name=reference_metadata.filter_name,
        magnitude_limit=settings.asteroid_default_magnitude_limit,
    )
    detection = next((item for item in detection_result.detections if target_name.lower() in (item.name or "").lower()), None)
    if detection is None:
        raise RuntimeError(f"Could not find {target_name} in the reference detection result.")

    adjustment_seconds = 0.0
    result_time = detection_result.observation_time
    if result_time is not None:
        offset = result_time - reference_time
        if 60.0 <= abs(offset.total_seconds()) <= 43200.0:
            adjustment_seconds = offset.total_seconds()
            for key, metadata in list(frame_metadata.items()):
                if metadata is not None and metadata.date_obs is not None:
                    frame_metadata[key] = replace(metadata, date_obs=metadata.date_obs + offset)

    frame_observation_times: dict[str, datetime] = {}
    frame_exposure_seconds: dict[str, float | None] = {}
    measurements = []
    for frame_path in frame_paths:
        key = str(frame_path.resolve())
        metadata = frame_metadata.get(key)
        midpoint_time, exposure_seconds = _frame_midpoint_time(metadata, detection_result.prediction_time, detection_result.exposure_seconds)
        if midpoint_time is not None:
            frame_observation_times[key] = midpoint_time
            measurement = measure_detections_in_frame(
                frame_path,
                [detection],
                reference_observation_time=detection_result.prediction_time,
                observation_time=midpoint_time,
                exposure_seconds=exposure_seconds,
                measure_local_match=False,
            )[0]
            if measurement is not None:
                measurements.append(measurement)
        frame_exposure_seconds[key] = exposure_seconds

    if len(measurements) < 2:
        raise RuntimeError(f"Need at least 2 target measurements to derive motion, got {len(measurements)}.")

    first_measurement = measurements[0]
    last_measurement = measurements[-1]
    delta_hours = (last_measurement.observation_time - first_measurement.observation_time).total_seconds() / 3600.0
    if abs(delta_hours) <= 1.0e-9:
        raise RuntimeError("Target measurement interval is too small to derive motion.")

    delta_x = float(last_measurement.predicted_x) - float(first_measurement.predicted_x)
    delta_y = float(last_measurement.predicted_y) - float(first_measurement.predicted_y)
    motion_px_per_hour = math.hypot(delta_x, delta_y) / abs(delta_hours)
    motion_angle_deg = math.degrees(math.atan2(delta_y, delta_x)) % 360.0

    return {
        "settings": settings,
        "frame_paths": frame_paths,
        "frame_observation_times": frame_observation_times,
        "reference_path": reference_path,
        "detection_result": detection_result,
        "detection": detection,
        "measurements": measurements,
        "motion_px_per_hour": float(motion_px_per_hour),
        "motion_angle_deg": float(motion_angle_deg),
        "timestamp_adjustment_seconds": adjustment_seconds,
    }


def _run_backend_comparison(*, context: dict[str, object], integration_mode: str, rejection_mode: str, backend: str, max_parallel_workers: int) -> tuple[dict[str, object], np.ndarray]:
    frame_paths = context["frame_paths"]
    reference_path = context["reference_path"]
    frame_observation_times = context["frame_observation_times"]
    detection = context["detection"]
    motion_px_per_hour = context["motion_px_per_hour"]
    motion_angle_deg = context["motion_angle_deg"]

    def progress(completed: int, total: int, message: str) -> None:
        if completed == 0 or completed == total or completed % 10 == 0:
            print(f"[{backend.upper()}] {completed}/{total}: {message}", flush=True)

    start = perf_counter()
    result = build_synthetic_tracked_full_frame_stack(
        frame_paths,
        reference_path=reference_path,
        frame_observation_times=frame_observation_times,
        motion_px_per_hour=motion_px_per_hour,
        motion_angle_deg=motion_angle_deg,
        integration_mode=integration_mode,
        rejection_mode=rejection_mode,
        motion_arcsec_per_hour=detection.motion_rate_arcsec_per_hour,
        array_backend_preference=backend,
        max_parallel_workers=max_parallel_workers,
        progress_callback=progress,
    )
    elapsed_ms = (perf_counter() - start) * 1000.0
    run_summary = {
        "backend_request": backend,
        "elapsed_ms": elapsed_ms,
        "compute_backend_summary": result.compute_backend_summary,
        "gpu_warmup_summary": result.gpu_warmup_summary,
        "summary_text": result.summary_text,
        "shape": list(result.stacked_data.shape),
        "used_frame_count": result.used_frame_count,
        "skipped_frame_count": result.skipped_frame_count,
        "measured_x": None if result.measured_x is None else float(result.measured_x),
        "measured_y": None if result.measured_y is None else float(result.measured_y),
        "match_offset_px": None if result.match_offset_px is None else float(result.match_offset_px),
        "local_snr": None if result.local_snr is None else float(result.local_snr),
        "local_peak_value": None if result.local_peak_value is None else float(result.local_peak_value),
        "local_flux": None if result.local_flux is None else float(result.local_flux),
    }
    return run_summary, np.asarray(result.stacked_data, dtype=np.float32)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare real full-frame Synthetic Track CPU and GPU results for a dataset using the same detection and timestamp logic as the UI.")
    parser.add_argument("dataset_path", help="Folder containing aligned FITS frames.")
    parser.add_argument("--target", default="Davida", help="Substring to match against the detected object name.")
    parser.add_argument("--integration", choices=["average", "mean", "min", "max"], default="average")
    parser.add_argument("--rejection", choices=["no_rejection", "min_max", "sigma_clipping", "winsorized_sigma_clipping", "averaged_sigma_clipping"], default="no_rejection")
    parser.add_argument("--backend", choices=["both", "cpu", "gpu"], default="both", help="Choose which backend to run. Only 'both' computes the CPU/GPU image-difference block.")
    parser.add_argument("--frame-limit", type=int, default=None, help="Optional limit for the number of frames to compare.")
    parser.add_argument("--workers", type=int, default=None, help="Override the max worker count passed into full-frame Synthetic Track. Defaults to the current saved shared worker setting.")
    parser.add_argument("--output-json", type=str, default=None, help="Optional path to write the final JSON payload.")
    args = parser.parse_args()

    dataset_path = Path(args.dataset_path).expanduser()
    if not dataset_path.exists():
        raise SystemExit(f"Dataset path does not exist: {dataset_path}")

    print("Preparing dataset context...", flush=True)
    context = _prepare_dataset_tracking_context(dataset_path, args.target, args.frame_limit)
    settings = context["settings"]
    max_parallel_workers = max(0, int(args.workers)) if args.workers is not None else resolve_shared_parallel_workers(settings)

    print(f"Dataset: {dataset_path}", flush=True)
    print(f"Target: {context['detection'].name}", flush=True)
    print(f"Frames: {len(context['frame_paths'])}", flush=True)
    print(f"Image shape: {context['detection_result'].solved_field.width}x{context['detection_result'].solved_field.height}", flush=True)
    print(f"Timestamp adjustment (s): {context['timestamp_adjustment_seconds']:.3f}", flush=True)
    print(f"Derived motion: {context['motion_px_per_hour']:.4f} px/h at {context['motion_angle_deg']:.3f} deg", flush=True)
    print(f"Requested workers: {max_parallel_workers}", flush=True)

    runs: list[dict[str, object]] = []
    cpu_stack: np.ndarray | None = None
    gpu_stack: np.ndarray | None = None
    if args.backend in {"both", "cpu"}:
        print("Running CPU full-frame Synthetic Track...", flush=True)
        cpu_run, cpu_stack = _run_backend_comparison(
            context=context,
            integration_mode=args.integration,
            rejection_mode=args.rejection,
            backend="cpu",
            max_parallel_workers=max_parallel_workers,
        )
        runs.append(cpu_run)
        print(f"CPU done in {cpu_run['elapsed_ms']:.2f} ms", flush=True)

    if args.backend in {"both", "gpu"}:
        print("Running GPU full-frame Synthetic Track...", flush=True)
        gpu_run, gpu_stack = _run_backend_comparison(
            context=context,
            integration_mode=args.integration,
            rejection_mode=args.rejection,
            backend="gpu",
            max_parallel_workers=max_parallel_workers,
        )
        runs.append(gpu_run)
        print(f"GPU done in {gpu_run['elapsed_ms']:.2f} ms", flush=True)

    comparison = None
    if cpu_stack is not None and gpu_stack is not None:
        diff = np.asarray(cpu_stack - gpu_stack, dtype=np.float64)
        abs_diff = np.abs(diff)
        comparison = {
            "max_abs_diff": float(abs_diff.max()),
            "mean_abs_diff": float(abs_diff.mean()),
            "rmse": float(np.sqrt(np.mean(diff * diff))),
            "allclose_1e-5": bool(np.allclose(cpu_stack, gpu_stack, rtol=1e-5, atol=1e-5)),
            "allclose_1e-4": bool(np.allclose(cpu_stack, gpu_stack, rtol=1e-4, atol=1e-4)),
            "cpu_peak": float(np.nanmax(cpu_stack)),
            "gpu_peak": float(np.nanmax(gpu_stack)),
            "peak_abs_diff": float(abs(float(np.nanmax(cpu_stack)) - float(np.nanmax(gpu_stack)))),
        }
    payload = {
        "dataset_path": str(dataset_path),
        "target_name": context["detection"].name,
        "frame_count": len(context["frame_paths"]),
        "reference_path": str(context["reference_path"]),
        "reference_detection_time_utc": context["detection_result"].observation_time.isoformat(),
        "reference_prediction_time_utc": context["detection_result"].prediction_time.isoformat(),
        "timestamp_adjustment_seconds": context["timestamp_adjustment_seconds"],
        "davida_predicted_xy_reference": [float(context["detection"].predicted_x), float(context["detection"].predicted_y)],
        "davida_motion_rate_arcsec_per_hour": None if context["detection"].motion_rate_arcsec_per_hour is None else float(context["detection"].motion_rate_arcsec_per_hour),
        "derived_motion_px_per_hour": context["motion_px_per_hour"],
        "derived_motion_angle_deg": context["motion_angle_deg"],
        "first_predicted_xy": [float(context["measurements"][0].predicted_x), float(context["measurements"][0].predicted_y)],
        "last_predicted_xy": [float(context["measurements"][-1].predicted_x), float(context["measurements"][-1].predicted_y)],
        "measured_frame_count": len(context["measurements"]),
        "requested_workers": max_parallel_workers,
        "cpu_gpu_comparison": comparison,
        "runs": runs,
    }
    payload_text = json.dumps(payload, indent=2)
    if args.output_json:
        output_path = Path(args.output_json).expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(payload_text, encoding="utf-8")
        print(f"Wrote JSON to {output_path}", flush=True)
    print(payload_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())