from __future__ import annotations

import gc
import os
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from astropy.io import fits

from photometry_app.core.alignment import align_wcs_image_sequence
from photometry_app.core.image_io import is_supported_image_path, read_header, read_image_data


_EXPOSURE_NUMBER_PATTERN = re.compile(r"[+-]?(?:\d+\.\d*|\d*\.\d+|\d+)(?:[Ee][+-]?\d+)?")
_EXPOSURE_HEADER_KEYS: tuple[tuple[str, float], ...] = (
    ("EXPTIME", 1.0),
    ("EXPOSURE", 1.0),
    ("EXPOSURE_TIME", 1.0),
    ("DARKTIME", 1.0),
    ("EXPOSUREMS", 0.001),
)


@dataclass(frozen=True, slots=True)
class CalibrationPipelineRequest:
    science_path: Path
    output_directory: Path
    bias_path: Path | None = None
    dark_path: Path | None = None
    flat_path: Path | None = None
    align_output: bool = False
    max_parallel_workers: int = 0


@dataclass(frozen=True, slots=True)
class CalibrationPipelineResult:
    output_directory: Path
    calibrated_directory: Path
    calibrated_frames: tuple[Path, ...]
    aligned_directory: Path | None
    aligned_frames: tuple[Path, ...]
    master_bias_path: Path | None
    master_dark_path: Path | None
    master_flat_path: Path | None
    summary_text: str


def calibrate_image_sequence(
    request: CalibrationPipelineRequest,
    *,
    progress_callback: Callable[[str], None] | None = None,
) -> CalibrationPipelineResult:
    science_path = Path(request.science_path).expanduser()
    output_directory = Path(request.output_directory).expanduser()
    calibrated_directory = output_directory / "calibrated"
    calibrated_directory.mkdir(parents=True, exist_ok=True)
    output_directory.mkdir(parents=True, exist_ok=True)

    science_frames = _collect_image_paths(science_path, exclude_roots=(output_directory,))
    if not science_frames:
        raise ValueError("Choose a science image folder or file containing supported FITS/XISF images.")

    worker_count = _resolve_calibration_parallel_worker_count(request.max_parallel_workers, len(science_frames))
    warning_messages: list[str] = []

    def _record_warning(message: str) -> None:
        warning_messages.append(message)
        _emit(progress_callback, f"Warning: {message}")

    _emit(progress_callback, f"Found {len(science_frames)} science image(s) for calibration.")
    bias_master: np.ndarray | None = None
    bias_exposure_seconds: float | None = None
    master_bias_path: Path | None = None
    if request.bias_path is None:
        bias_master, master_bias_path, bias_exposure_seconds, _ = _load_cached_master(output_directory, "bias", "master_bias.fits", progress_callback)
    else:
        bias_master, bias_exposure_seconds = _build_master(request.bias_path, "bias", progress_callback, warning_callback=_record_warning, max_parallel_workers=worker_count)
        master_bias_path = _write_master(output_directory, "master_bias.fits", bias_master, progress_callback, master_label="bias", exposure_seconds=bias_exposure_seconds)

    dark_master: np.ndarray | None = None
    dark_exposure_seconds: float | None = None
    dark_master_bias_corrected = False
    master_dark_path: Path | None = None
    if request.dark_path is None:
        dark_master, master_dark_path, dark_exposure_seconds, dark_master_bias_corrected = _load_cached_master(output_directory, "dark", "master_dark.fits", progress_callback)
    else:
        dark_master, dark_exposure_seconds = _build_master(request.dark_path, "dark", progress_callback, warning_callback=_record_warning, max_parallel_workers=worker_count)
        if bias_master is not None and dark_master is not None:
            dark_master = dark_master - _broadcast_master(bias_master, dark_master.shape, "bias", "dark")
            dark_master_bias_corrected = True
        master_dark_path = _write_master(
            output_directory,
            "master_dark.fits",
            dark_master,
            progress_callback,
            master_label="dark",
            exposure_seconds=dark_exposure_seconds,
            bias_corrected=dark_master_bias_corrected,
        )

    if dark_master is not None and not dark_master_bias_corrected and _any_frame_requires_dark_scaling(science_frames, dark_exposure_seconds):
        _record_warning(
            "Dark exposure scaling was disabled because the dark master is not bias-corrected; "
            "provide a bias master or matching dark exposure times for exact correction."
        )

    flat_normalized: np.ndarray | None = None
    flat_exposure_seconds: float | None = None
    master_flat_path: Path | None = None
    if request.flat_path is None:
        flat_normalized, master_flat_path, flat_exposure_seconds, _ = _load_cached_master(output_directory, "flat", "master_flat_normalized.fits", progress_callback)
    else:
        flat_master, flat_exposure_seconds = _build_master(request.flat_path, "flat", progress_callback, warning_callback=_record_warning, max_parallel_workers=worker_count)
        if dark_master is not None and not dark_master_bias_corrected and _target_requires_dark_scaling(flat_exposure_seconds, dark_exposure_seconds):
            _record_warning(
                "Dark exposure scaling was disabled while correcting flats because the dark master is not bias-corrected; "
                "provide a bias master or matching dark-flat exposure times for exact flat correction."
            )
        flat_normalized = (
            _normalized_flat_master(
                flat_master,
                bias_master,
                dark_master,
                flat_exposure_seconds=flat_exposure_seconds,
                dark_exposure_seconds=dark_exposure_seconds,
                dark_master_bias_corrected=dark_master_bias_corrected,
            )
            if flat_master is not None
            else None
        )
        master_flat_path = _write_master(
            output_directory,
            "master_flat_normalized.fits",
            flat_normalized,
            progress_callback,
            master_label="flat",
            exposure_seconds=flat_exposure_seconds,
        )

    calibrated_frames: list[Path] = []
    if worker_count <= 1:
        for index, source_path in enumerate(science_frames, start=1):
            _emit(progress_callback, f"[Calibrate {index}/{len(science_frames)}] Calibrating {source_path.name}.")
            calibrated_frames.append(
                _calibrate_one_frame(
                    source_path,
                    calibrated_directory,
                    bias_master=bias_master,
                    dark_master=dark_master,
                    dark_exposure_seconds=dark_exposure_seconds,
                    dark_master_bias_corrected=dark_master_bias_corrected,
                    flat_master=flat_normalized,
                )
            )
    else:
        _emit(progress_callback, f"Calibrating {len(science_frames)} science image(s) using {worker_count} worker(s).")
        calibrated_frames_by_source: dict[str, Path] = {}
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_to_source = {
                executor.submit(
                    _calibrate_one_frame,
                    source_path,
                    calibrated_directory,
                    bias_master=bias_master,
                    dark_master=dark_master,
                    dark_exposure_seconds=dark_exposure_seconds,
                    dark_master_bias_corrected=dark_master_bias_corrected,
                    flat_master=flat_normalized,
                ): source_path
                for source_path in science_frames
            }
            for index, future in enumerate(as_completed(future_to_source), start=1):
                source_path = future_to_source[future]
                calibrated_frames_by_source[str(source_path.resolve())] = future.result()
                _emit(progress_callback, f"[Calibrate {index}/{len(science_frames)}] Calibrated {source_path.name}.")
        calibrated_frames = [calibrated_frames_by_source[str(source_path.resolve())] for source_path in science_frames]

    aligned_directory: Path | None = None
    aligned_frames: tuple[Path, ...] = ()
    if request.align_output:
        if len(calibrated_frames) < 2:
            raise ValueError("Optional alignment requires at least two calibrated frames.")
        bias_master = None
        dark_master = None
        flat_normalized = None
        gc.collect()
        aligned_directory = output_directory / "aligned"
        _emit(progress_callback, "Aligning calibrated frames onto the first calibrated frame.")
        alignment_result = align_wcs_image_sequence(
            calibrated_frames,
            reference_path=calibrated_frames[0],
            output_directory=aligned_directory,
            max_parallel_workers=request.max_parallel_workers,
            progress_callback=progress_callback,
        )
        aligned_frames = tuple(frame.output_path for frame in alignment_result.aligned_frames)

    summary_text = (
        f"Calibrated {len(calibrated_frames)} image(s) into {calibrated_directory}."
        + (f" Aligned copies were saved to {aligned_directory}." if aligned_directory is not None else "")
    )
    if warning_messages:
        summary_text += " Warnings: " + " ".join(warning_messages)
    _emit(progress_callback, summary_text)
    return CalibrationPipelineResult(
        output_directory=output_directory,
        calibrated_directory=calibrated_directory,
        calibrated_frames=tuple(calibrated_frames),
        aligned_directory=aligned_directory,
        aligned_frames=aligned_frames,
        master_bias_path=master_bias_path,
        master_dark_path=master_dark_path,
        master_flat_path=master_flat_path,
        summary_text=summary_text,
    )


def _collect_image_paths(path: Path | None, *, exclude_roots: tuple[Path, ...] = ()) -> list[Path]:
    if path is None:
        return []
    resolved = Path(path).expanduser()
    if not resolved.exists():
        return []
    if resolved.is_file():
        return [resolved] if is_supported_image_path(resolved) else []
    excluded_roots = tuple(root.resolve() for root in exclude_roots if root.exists())
    return sorted(
        (
            candidate
            for candidate in resolved.rglob("*")
            if candidate.is_file()
            and is_supported_image_path(candidate)
            and not _is_under_any(candidate, excluded_roots)
        ),
        key=lambda candidate: str(candidate).lower(),
    )


def _is_under_any(path: Path, roots: tuple[Path, ...]) -> bool:
    resolved = path.resolve()
    return any(resolved == root or resolved.is_relative_to(root) for root in roots)


def _build_master(
    path: Path | None,
    label: str,
    progress_callback: Callable[[str], None] | None,
    *,
    warning_callback: Callable[[str], None] | None,
    max_parallel_workers: int,
) -> tuple[np.ndarray, float | None] | tuple[None, None]:
    frame_paths = _collect_image_paths(path)
    if not frame_paths:
        return None, None
    _emit(progress_callback, f"Building master {label} from {len(frame_paths)} frame(s).")
    exposure_seconds = _master_exposure_seconds(frame_paths, label, warning_callback=warning_callback)
    worker_count = _resolve_calibration_parallel_worker_count(max_parallel_workers, len(frame_paths))
    if worker_count > 1:
        _emit(progress_callback, f"Reading {label} frames using {worker_count} worker(s).")
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            stack = list(executor.map(_read_float32_image_data, frame_paths))
    else:
        stack = [_read_float32_image_data(frame_path) for frame_path in frame_paths]
    reference_shape: tuple[int, ...] | None = None
    for frame_path, data in zip(frame_paths, stack):
        if reference_shape is None:
            reference_shape = tuple(data.shape)
        elif tuple(data.shape) != reference_shape:
            raise ValueError(f"Master {label} frame {frame_path.name} has shape {data.shape}, expected {reference_shape}.")
    if len(stack) == 1:
        return stack[0].astype(np.float32, copy=True), exposure_seconds
    return np.asarray(np.nanmedian(np.stack(stack, axis=0), axis=0), dtype=np.float32), exposure_seconds


def _read_float32_image_data(path: Path) -> np.ndarray:
    return np.asarray(read_image_data(path), dtype=np.float32)


def _normalized_flat_master(
    flat_master: np.ndarray,
    bias_master: np.ndarray | None,
    dark_master: np.ndarray | None,
    *,
    flat_exposure_seconds: float | None,
    dark_exposure_seconds: float | None,
    dark_master_bias_corrected: bool,
) -> np.ndarray:
    corrected = np.asarray(flat_master, dtype=np.float32).copy()
    if bias_master is not None:
        corrected -= _broadcast_master(bias_master, corrected.shape, "bias", "flat")
    if dark_master is not None:
        corrected -= _broadcast_master(dark_master, corrected.shape, "dark", "flat") * _dark_exposure_scale(
            flat_exposure_seconds,
            dark_exposure_seconds,
            dark_master_bias_corrected=dark_master_bias_corrected,
        )
    finite = np.isfinite(corrected)
    positive = corrected[finite & (corrected > 0)]
    if positive.size == 0:
        raise ValueError("The flat master does not contain positive finite values after correction.")
    scale = float(np.nanmedian(positive))
    if not np.isfinite(scale) or scale <= 0.0:
        raise ValueError("Could not normalize the flat master.")
    normalized = corrected / scale
    normalized[~np.isfinite(normalized)] = 1.0
    normalized[normalized <= 1e-6] = 1.0
    return normalized.astype(np.float32, copy=False)


def _calibrate_one_frame(
    source_path: Path,
    calibrated_directory: Path,
    *,
    bias_master: np.ndarray | None,
    dark_master: np.ndarray | None,
    dark_exposure_seconds: float | None,
    dark_master_bias_corrected: bool,
    flat_master: np.ndarray | None,
) -> Path:
    source_data = np.asarray(read_image_data(source_path), dtype=np.float32)
    header = read_header(source_path)
    source_exposure_seconds = _header_exposure_seconds(header)
    dark_scale = _dark_exposure_scale(
        source_exposure_seconds,
        dark_exposure_seconds,
        dark_master_bias_corrected=dark_master_bias_corrected,
    )
    calibrated = source_data.copy()
    if bias_master is not None:
        calibrated -= _broadcast_master(bias_master, calibrated.shape, "bias", source_path.name)
    if dark_master is not None:
        calibrated -= _broadcast_master(dark_master, calibrated.shape, "dark", source_path.name) * dark_scale
    if flat_master is not None:
        calibrated /= _broadcast_master(flat_master, calibrated.shape, "flat", source_path.name)
    calibrated[~np.isfinite(calibrated)] = 0.0

    header["CALIBRAT"] = (True, "Image calibrated by Citizen Astronomy")
    header["CALBIAS"] = (bias_master is not None, "Bias correction applied")
    header["CALDARK"] = (dark_master is not None, "Dark correction applied")
    header["CALFLAT"] = (flat_master is not None, "Flat correction applied")
    if dark_master is not None:
        header["CALDSCL"] = (float(dark_scale), "Dark master exposure scale")
        header["CALDBIAS"] = (bool(dark_master_bias_corrected), "Dark master was bias-corrected before scaling")
    header.add_history("Citizen Astronomy calibration: calibrated = (light - bias - scaled_dark) / normalized_flat.")

    output_path = calibrated_directory / f"{source_path.stem}_calibrated.fits"
    fits.PrimaryHDU(data=calibrated.astype(np.float32, copy=False), header=header).writeto(output_path, overwrite=True)
    return output_path


def _broadcast_master(master: np.ndarray, target_shape: tuple[int, ...], master_label: str, target_label: str) -> np.ndarray:
    if tuple(master.shape) == target_shape:
        return master
    if len(target_shape) == 3 and master.ndim == 2 and tuple(master.shape) == tuple(target_shape[:2]):
        return master[:, :, np.newaxis]
    raise ValueError(
        f"Master {master_label} shape {master.shape} cannot be applied to {target_label} shape {target_shape}."
    )


def _write_master(
    output_directory: Path,
    file_name: str,
    master: np.ndarray | None,
    progress_callback: Callable[[str], None] | None,
    *,
    master_label: str,
    exposure_seconds: float | None,
    bias_corrected: bool | None = None,
) -> Path | None:
    if master is None:
        return None
    output_path = output_directory / file_name
    header = fits.Header()
    header["MASTTYPE"] = (master_label.upper(), "Calibration master type")
    if master_label.lower() == "dark" and bias_corrected is not None:
        header["BIASCOR"] = (bool(bias_corrected), "Master dark has had bias subtracted")
    if exposure_seconds is not None and np.isfinite(float(exposure_seconds)):
        header["EXPTIME"] = (float(exposure_seconds), "Source exposure time in seconds")
    fits.PrimaryHDU(data=np.asarray(master, dtype=np.float32), header=header).writeto(output_path, overwrite=True)
    _emit(progress_callback, f"Saved {output_path.name}.")
    return output_path


def _load_cached_master(
    output_directory: Path,
    label: str,
    file_name: str,
    progress_callback: Callable[[str], None] | None,
) -> tuple[np.ndarray | None, Path | None, float | None, bool]:
    for candidate in (output_directory / file_name, output_directory / "masters" / file_name):
        if not candidate.exists() or not candidate.is_file():
            continue
        _emit(
            progress_callback,
            f"Using cached {label} master from {candidate}. Delete that file to rebuild it from the selected source frames.",
        )
        header = read_header(candidate)
        return _read_float32_image_data(candidate), candidate, _header_exposure_seconds(header), bool(header.get("BIASCOR", False))
    return None, None, None, False


def _master_exposure_seconds(
    frame_paths: list[Path],
    label: str,
    *,
    warning_callback: Callable[[str], None] | None,
) -> float | None:
    exposure_values = [value for frame_path in frame_paths if (value := _image_exposure_seconds(frame_path)) is not None]
    if not exposure_values:
        return None
    median_exposure = float(np.median(np.asarray(exposure_values, dtype=float)))
    tolerance = max(1e-3, abs(median_exposure) * 1e-3)
    if any(abs(float(value) - median_exposure) > tolerance for value in exposure_values):
        message = (
            f"Master {label} frames have different exposure times; continuing with the current mismatched set "
            f"using median exposure {median_exposure:.6g} s for scaling."
        )
        if warning_callback is not None:
            warning_callback(message)
    return median_exposure


def _image_exposure_seconds(path: Path) -> float | None:
    try:
        return _header_exposure_seconds(read_header(path))
    except Exception:
        return None


def _header_exposure_seconds(header: fits.Header) -> float | None:
    for key, scale in _EXPOSURE_HEADER_KEYS:
        if key not in header:
            continue
        value = header.get(key)
        exposure_seconds = _coerce_exposure_seconds(value, scale)
        if exposure_seconds is not None:
            return exposure_seconds
    return None


def _coerce_exposure_seconds(value: object, scale: float) -> float | None:
    if value is None:
        return None
    try:
        exposure = float(value) * scale
    except (TypeError, ValueError):
        match = _EXPOSURE_NUMBER_PATTERN.search(str(value))
        if match is None:
            return None
        exposure = float(match.group(0)) * scale
    if not np.isfinite(exposure) or exposure < 0.0:
        return None
    return exposure


def _dark_exposure_scale(
    target_exposure_seconds: float | None,
    dark_exposure_seconds: float | None,
    *,
    dark_master_bias_corrected: bool,
) -> float:
    scale = _raw_dark_exposure_scale(target_exposure_seconds, dark_exposure_seconds)
    if dark_master_bias_corrected or not _dark_scale_is_significant(scale):
        return scale
    return 1.0


def _raw_dark_exposure_scale(target_exposure_seconds: float | None, dark_exposure_seconds: float | None) -> float:
    if target_exposure_seconds is None or dark_exposure_seconds is None:
        return 1.0
    if dark_exposure_seconds <= 0.0 or not np.isfinite(float(dark_exposure_seconds)):
        return 1.0
    if target_exposure_seconds < 0.0 or not np.isfinite(float(target_exposure_seconds)):
        return 1.0
    return float(target_exposure_seconds) / float(dark_exposure_seconds)


def _dark_scale_is_significant(scale: float) -> bool:
    return bool(np.isfinite(float(scale)) and abs(float(scale) - 1.0) > 1e-3)


def _target_requires_dark_scaling(target_exposure_seconds: float | None, dark_exposure_seconds: float | None) -> bool:
    return _dark_scale_is_significant(_raw_dark_exposure_scale(target_exposure_seconds, dark_exposure_seconds))


def _any_frame_requires_dark_scaling(frame_paths: list[Path], dark_exposure_seconds: float | None) -> bool:
    return any(_target_requires_dark_scaling(_image_exposure_seconds(frame_path), dark_exposure_seconds) for frame_path in frame_paths)


def _resolve_calibration_parallel_worker_count(configured_workers: int, total_count: int) -> int:
    if total_count <= 1:
        return 1
    if configured_workers > 0:
        return max(1, min(int(configured_workers), total_count))
    cpu_count = os.cpu_count() or 1
    return max(1, min(total_count, 8, cpu_count))


def _emit(progress_callback: Callable[[str], None] | None, message: str) -> None:
    if progress_callback is not None:
        progress_callback(message)