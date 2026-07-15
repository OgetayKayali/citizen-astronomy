from __future__ import annotations



from collections.abc import Callable

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from datetime import UTC, datetime

from pathlib import Path

import os

import re



from astropy.io import fits

from astropy.io.fits import Header

from astropy.wcs import WCS

import numpy as np

try:
    from scipy import ndimage
except ImportError:
    ndimage = None



from photometry_app.core.image_io import read_header_and_shape, read_image_data

from photometry_app.core.wcs import validate_wcs



try:

    from reproject import reproject_interp

except ImportError:

    reproject_interp = None





_WCS_KEY_PATTERN = re.compile(

    r"^(?:WCSAXES|RADESYS|LONPOLE|LATPOLE|EQUINOX|EPOCH|MJDREF|MJD-OBS|MJD-END|MJD-AVG|DATE-AVG|DATE-END|"

    r"CRPIX\d+|CRVAL\d+|CDELT\d+|CTYPE\d+|CUNIT\d+|CROTA\d+|CD\d+_\d+|PC\d+_\d+|"

    r"PV\d+_\d+|PS\d+_\d+|A_ORDER|B_ORDER|AP_ORDER|BP_ORDER|A_\d+_\d+|B_\d+_\d+|AP_\d+_\d+|BP_\d+_\d+)$"

)

_MAX_REPROJECT_ALIGNMENT_PARALLEL_WORKERS = 2
_MAX_FAST_AFFINE_ALIGNMENT_PARALLEL_WORKERS = 4
_FAST_AFFINE_RESIDUAL_TOLERANCE_PX = 0.08
_STAR_RESIDUAL_MIN_MATCHES = 3
_STAR_RESIDUAL_BIN_SIZE_PX = 1.0
_STAR_RESIDUAL_MAX_SCATTER_PX = 1.5
_STAR_RESIDUAL_MIN_APPLY_SHIFT_PX = 0.05
_STAR_RESIDUAL_MAX_SHIFT_PX = 96.0
_STAR_RESIDUAL_MAX_SHIFT_FRACTION = 0.25
_ALIGNMENT_STAR_MAX_COUNT = 96
_ALIGNMENT_STAR_MIN_SEPARATION_PX = 5.0




@dataclass(frozen=True, slots=True)
class _AlignmentStar:

    row: float

    column: float

    brightness: float





@dataclass(slots=True)

class AlignedFrameOutput:

    source_path: Path

    output_path: Path

    is_reference: bool = False





@dataclass(slots=True)

class SequenceAlignmentResult:

    output_directory: Path

    reference_path: Path

    aligned_frames: tuple[AlignedFrameOutput, ...]

    summary_text: str





def align_wcs_image_sequence(

    frame_paths: list[Path],

    *,

    reference_path: Path,

    output_directory: Path,

    max_parallel_workers: int = 0,

    reference_crop_bounds: tuple[int, int, int, int] | None = None,

    worker_limit_override: int | None = None,

    progress_callback: Callable[[str], None] | None = None,

) -> SequenceAlignmentResult:

    if reproject_interp is None:

        raise RuntimeError("Image alignment requires the 'reproject' package to be installed.")

    if len(frame_paths) < 2:

        raise ValueError("Alignment requires at least two solved frames.")

    if reference_path not in frame_paths:

        raise ValueError("The selected reference frame is not part of the loaded sequence.")



    unique_parents = {path.parent.resolve() for path in frame_paths}

    if len(unique_parents) != 1:

        raise ValueError("Alignment requires all loaded asteroid/comet frames to come from the same folder.")



    output_directory.mkdir(parents=True, exist_ok=True)



    reference_header, reference_width, reference_height = read_header_and_shape(reference_path)

    reference_wcs, reference_reasons = _validated_celestial_wcs(reference_path, reference_header)

    if reference_width is None or reference_height is None:

        raise ValueError(f"Reference frame {reference_path.name} is missing image dimensions.")

    if reference_wcs is None:

        reason_text = reference_reasons[0] if reference_reasons else "No usable celestial WCS was found."

        raise ValueError(f"Reference frame {reference_path.name} is not plate-solved: {reason_text}")



    full_target_shape = (int(reference_height), int(reference_width))
    normalized_reference_crop_bounds = _validated_reference_crop_bounds(
        reference_crop_bounds,
        width=int(reference_width),
        height=int(reference_height),
    )
    if normalized_reference_crop_bounds is None:
        reference_alignment_wcs = reference_wcs
        target_shape = full_target_shape
    else:
        reference_alignment_wcs = _cropped_reference_wcs(reference_wcs, normalized_reference_crop_bounds)
        target_shape = (
            normalized_reference_crop_bounds[3] - normalized_reference_crop_bounds[1],
            normalized_reference_crop_bounds[2] - normalized_reference_crop_bounds[0],
        )
    reference_alignment_data = _copy_reference_data(
        np.asarray(read_image_data(reference_path), dtype=np.float32),
        target_shape,
        crop_bounds=normalized_reference_crop_bounds,
    )
    fast_affine_sequence = _sequence_supports_fast_affine_alignment(
        frame_paths,
        reference_path=reference_path,
        reference_wcs=reference_alignment_wcs,
        target_shape=target_shape,
    )
    worker_count = _resolve_alignment_worker_count(
        max_parallel_workers,
        len(frame_paths),
        fast_affine=fast_affine_sequence,
        worker_limit_override=worker_limit_override,
    )

    aligned_outputs: list[AlignedFrameOutput] = []

    if worker_count <= 1:

        for index, source_path in enumerate(frame_paths, start=1):

            _emit_progress(progress_callback, f"[Align {index}/{len(frame_paths)}] Aligning {source_path.name} onto {reference_path.name}.")

            aligned_outputs.append(

                _align_one_frame(

                    source_path,

                    reference_path=reference_path,

                    reference_wcs=reference_alignment_wcs,

                    target_shape=target_shape,

                    reference_alignment_data=reference_alignment_data,

                    reference_crop_bounds=normalized_reference_crop_bounds,

                    output_directory=output_directory,

                )

            )

    else:

        _emit_progress(

            progress_callback,

            f"Aligning {len(frame_paths)} frame(s) onto {reference_path.name} using {worker_count} worker(s) in parallel.",

        )

        aligned_outputs_by_source: dict[str, AlignedFrameOutput] = {}

        with ThreadPoolExecutor(max_workers=worker_count) as executor:

            future_to_source = {

                executor.submit(

                    _align_one_frame,

                    source_path,

                    reference_path=reference_path,

                    reference_wcs=reference_alignment_wcs,

                    target_shape=target_shape,

                    reference_alignment_data=reference_alignment_data,

                    reference_crop_bounds=normalized_reference_crop_bounds,

                    output_directory=output_directory,

                ): source_path

                for source_path in frame_paths

            }

            for index, future in enumerate(as_completed(future_to_source), start=1):

                source_path = future_to_source[future]

                aligned_outputs_by_source[str(source_path.resolve())] = future.result()

                _emit_progress(progress_callback, f"[Align {index}/{len(frame_paths)}] Aligned {source_path.name} onto {reference_path.name}.")

        aligned_outputs = [aligned_outputs_by_source[str(source_path.resolve())] for source_path in frame_paths]



    summary_text = (

        f"Aligned {len(aligned_outputs)} frame(s) onto {reference_path.name} and saved FITS copies to {output_directory}."

    )

    return SequenceAlignmentResult(

        output_directory=output_directory,

        reference_path=reference_path,

        aligned_frames=tuple(aligned_outputs),

        summary_text=summary_text,

    )





def _validated_celestial_wcs(source_path: Path, header: Header) -> tuple[WCS | None, list[str]]:

    solved, reasons = validate_wcs(header, source_path)

    if not solved:

        return None, reasons

    try:

        wcs = WCS(header).celestial

    except Exception as exc:

        return None, [*reasons, f"Astropy could not parse celestial WCS: {exc}"]

    if wcs.pixel_n_dim < 2 or wcs.world_n_dim < 2:

        return None, [*reasons, "WCS is not two-dimensional."]

    return wcs, reasons


def _align_one_frame(

    source_path: Path,

    *,

    reference_path: Path,

    reference_wcs: WCS,

    target_shape: tuple[int, int],

    reference_alignment_data: np.ndarray,

    reference_crop_bounds: tuple[int, int, int, int] | None = None,

    output_directory: Path,

) -> AlignedFrameOutput:

    source_header, _source_width, _source_height = read_header_and_shape(source_path)

    source_wcs, source_reasons = _validated_celestial_wcs(source_path, source_header)

    if source_wcs is None:

        reason_text = source_reasons[0] if source_reasons else "No usable celestial WCS was found."

        raise ValueError(f"{source_path.name} is not plate-solved: {reason_text}")



    residual_shift: tuple[float, float] | None = None

    if source_path.resolve() == reference_path.resolve():

        aligned_data = np.asarray(reference_alignment_data, dtype=np.float32).copy()

    else:

        source_data = np.asarray(read_image_data(source_path), dtype=np.float32)

        aligned_data = _reproject_image_data(source_data, source_wcs, reference_wcs, target_shape)

        aligned_data, residual_shift = _refine_alignment_with_star_residual(reference_alignment_data, aligned_data)



    output_path = output_directory / f"{source_path.stem}.fits"

    output_header = _aligned_output_header(source_header, reference_wcs, source_path, reference_path, residual_shift=residual_shift)

    fits.PrimaryHDU(data=aligned_data, header=output_header).writeto(output_path, overwrite=True)

    return AlignedFrameOutput(source_path=source_path, output_path=output_path, is_reference=source_path.resolve() == reference_path.resolve())


def _resolve_alignment_worker_count(
    configured_workers: int,
    total_count: int,
    *,
    fast_affine: bool = False,
    worker_limit_override: int | None = None,
) -> int:

    if total_count <= 1:

        return 1

    worker_limit = (
        max(1, int(worker_limit_override))
        if worker_limit_override is not None and int(worker_limit_override) > 0
        else (_MAX_FAST_AFFINE_ALIGNMENT_PARALLEL_WORKERS if fast_affine else _MAX_REPROJECT_ALIGNMENT_PARALLEL_WORKERS)
    )

    if configured_workers > 0:

        return max(1, min(int(configured_workers), total_count, worker_limit, os.cpu_count() or 1))

    cpu_count = os.cpu_count() or 1

    return max(1, min(total_count, worker_limit, cpu_count))



def _sequence_supports_fast_affine_alignment(
    frame_paths: list[Path],
    *,
    reference_path: Path,
    reference_wcs: WCS,
    target_shape: tuple[int, int],
) -> bool:
    if ndimage is None:
        return False
    for source_path in frame_paths:
        if source_path.resolve() == reference_path.resolve():
            continue
        try:
            source_header, _source_width, _source_height = read_header_and_shape(source_path)
            source_wcs, _source_reasons = _validated_celestial_wcs(source_path, source_header)
        except Exception:
            return False
        if source_wcs is None:
            return False
        if _fast_affine_pixel_transform(source_wcs, reference_wcs, target_shape) is None:
            return False
    return True





def _copy_reference_data(
    source_data: np.ndarray,
    target_shape: tuple[int, int],
    *,
    crop_bounds: tuple[int, int, int, int] | None = None,
) -> np.ndarray:

    normalized = _normalize_image_axes(source_data)

    if crop_bounds is not None:

        x0, y0, x1, y1 = crop_bounds

        normalized = np.asarray(normalized[y0:y1, x0:x1], dtype=np.float32)

    if normalized.shape[:2] != target_shape:

        raise ValueError("Reference image data does not match the reference grid dimensions.")

    return normalized.astype(np.float32, copy=True)



def _validated_reference_crop_bounds(
    crop_bounds: tuple[int, int, int, int] | None,
    *,
    width: int,
    height: int,
) -> tuple[int, int, int, int] | None:

    if crop_bounds is None:

        return None

    x0, y0, x1, y1 = [int(value) for value in crop_bounds]

    normalized_x0 = max(0, min(width, min(x0, x1)))

    normalized_x1 = max(0, min(width, max(x0, x1)))

    normalized_y0 = max(0, min(height, min(y0, y1)))

    normalized_y1 = max(0, min(height, max(y0, y1)))

    if normalized_x1 - normalized_x0 < 2 or normalized_y1 - normalized_y0 < 2:

        return None

    if normalized_x0 == 0 and normalized_y0 == 0 and normalized_x1 == width and normalized_y1 == height:

        return None

    return normalized_x0, normalized_y0, normalized_x1, normalized_y1



def _cropped_reference_wcs(reference_wcs: WCS, crop_bounds: tuple[int, int, int, int]) -> WCS:

    x0, y0, x1, y1 = crop_bounds

    return reference_wcs.slice((slice(y0, y1), slice(x0, x1)))





def _reproject_image_data(

    source_data: np.ndarray,

    source_wcs: WCS,

    reference_wcs: WCS,

    target_shape: tuple[int, int],

) -> np.ndarray:

    normalized = _normalize_image_axes(source_data)

    affine_transform = _fast_affine_pixel_transform(source_wcs, reference_wcs, target_shape)

    if affine_transform is not None:

        return _affine_transform_image_data(normalized, affine_transform, target_shape)

    if normalized.ndim == 2:

        return _reproject_plane(normalized, source_wcs, reference_wcs, target_shape)

    if normalized.ndim == 3:

        channels = [

            _reproject_plane(normalized[:, :, channel_index], source_wcs, reference_wcs, target_shape)

            for channel_index in range(normalized.shape[2])

        ]

        return np.stack(channels, axis=2).astype(np.float32, copy=False)

    raise ValueError("Alignment currently supports only 2D or simple channel-last image arrays.")



def _fast_affine_pixel_transform(
    source_wcs: WCS,
    reference_wcs: WCS,
    target_shape: tuple[int, int],
) -> tuple[np.ndarray, np.ndarray] | None:
    if ndimage is None:
        return None

    height, width = target_shape
    x_values = np.array([0.0, width - 1.0, 0.0, width - 1.0, (width - 1.0) * 0.5, (width - 1.0) * 0.5, 0.0, width - 1.0], dtype=np.float64)
    y_values = np.array([0.0, 0.0, height - 1.0, height - 1.0, (height - 1.0) * 0.5, 0.0, (height - 1.0) * 0.5, (height - 1.0) * 0.5], dtype=np.float64)
    try:
        world = reference_wcs.pixel_to_world_values(x_values, y_values)
        source_x, source_y = source_wcs.world_to_pixel_values(*world)
    except Exception:
        return None

    source_x = np.asarray(source_x, dtype=np.float64)
    source_y = np.asarray(source_y, dtype=np.float64)
    if not (np.all(np.isfinite(source_x)) and np.all(np.isfinite(source_y))):
        return None

    design = np.column_stack([x_values, y_values, np.ones_like(x_values)])
    try:
        coeff_x, *_unused_x = np.linalg.lstsq(design, source_x, rcond=None)
        coeff_y, *_unused_y = np.linalg.lstsq(design, source_y, rcond=None)
    except np.linalg.LinAlgError:
        return None

    predicted_x = design @ coeff_x
    predicted_y = design @ coeff_y
    max_residual = float(np.max(np.hypot(predicted_x - source_x, predicted_y - source_y)))
    if not np.isfinite(max_residual) or max_residual > _FAST_AFFINE_RESIDUAL_TOLERANCE_PX:
        return None

    matrix = np.array(
        [
            [coeff_y[1], coeff_y[0]],
            [coeff_x[1], coeff_x[0]],
        ],
        dtype=np.float64,
    )
    offset = np.array([coeff_y[2], coeff_x[2]], dtype=np.float64)
    return matrix, offset



def _affine_transform_image_data(
    normalized: np.ndarray,
    affine_transform: tuple[np.ndarray, np.ndarray],
    target_shape: tuple[int, int],
) -> np.ndarray:
    if normalized.ndim == 2:
        return _affine_transform_plane(normalized, affine_transform, target_shape)
    if normalized.ndim == 3:
        channels = [
            _affine_transform_plane(normalized[:, :, channel_index], affine_transform, target_shape)
            for channel_index in range(normalized.shape[2])
        ]
        return np.stack(channels, axis=2).astype(np.float32, copy=False)
    raise ValueError("Alignment currently supports only 2D or simple channel-last image arrays.")



def _affine_transform_plane(
    plane: np.ndarray,
    affine_transform: tuple[np.ndarray, np.ndarray],
    target_shape: tuple[int, int],
) -> np.ndarray:
    if ndimage is None:
        raise RuntimeError("Fast affine alignment requires scipy.ndimage.")
    matrix, offset = affine_transform
    output = np.empty(target_shape, dtype=np.float32)
    aligned_plane = ndimage.affine_transform(
        np.asarray(plane, dtype=np.float32),
        matrix,
        offset=offset,
        output_shape=target_shape,
        output=output,
        order=1,
        mode="constant",
        cval=0.0,
        prefilter=False,
    )
    aligned_plane = np.asarray(aligned_plane, dtype=np.float32)
    aligned_plane[~np.isfinite(aligned_plane)] = 0.0
    return aligned_plane



def _refine_alignment_with_star_residual(reference_data: np.ndarray, aligned_data: np.ndarray) -> tuple[np.ndarray, tuple[float, float] | None]:

    if ndimage is None:

        return aligned_data, None

    residual_shift = _estimate_star_residual_shift(reference_data, aligned_data)

    if residual_shift is None:

        return aligned_data, None

    shift_row, shift_column = residual_shift

    if float(np.hypot(shift_row, shift_column)) < _STAR_RESIDUAL_MIN_APPLY_SHIFT_PX:

        return aligned_data, None

    shifted = _shift_image_data(aligned_data, residual_shift)

    return shifted, residual_shift



def _estimate_star_residual_shift(reference_data: np.ndarray, aligned_data: np.ndarray) -> tuple[float, float] | None:

    reference_plane = _alignment_detection_plane(reference_data)

    aligned_plane = _alignment_detection_plane(aligned_data)

    if reference_plane.shape != aligned_plane.shape:

        return None

    reference_stars = _extract_alignment_stars(reference_plane)

    aligned_stars = _extract_alignment_stars(aligned_plane)

    if len(reference_stars) < _STAR_RESIDUAL_MIN_MATCHES or len(aligned_stars) < _STAR_RESIDUAL_MIN_MATCHES:

        return None

    max_shift = _max_star_residual_shift(reference_plane.shape)

    offsets_by_bin: dict[tuple[int, int], list[tuple[float, float]]] = {}

    for reference_star in reference_stars:

        for aligned_star in aligned_stars:

            shift_row = reference_star.row - aligned_star.row

            shift_column = reference_star.column - aligned_star.column

            if abs(shift_row) > max_shift or abs(shift_column) > max_shift:

                continue

            bin_key = (
                int(round(shift_row / _STAR_RESIDUAL_BIN_SIZE_PX)),
                int(round(shift_column / _STAR_RESIDUAL_BIN_SIZE_PX)),
            )

            offsets_by_bin.setdefault(bin_key, []).append((float(shift_row), float(shift_column)))

    if not offsets_by_bin:

        return None

    candidate_offsets = max(offsets_by_bin.values(), key=len)

    if len(candidate_offsets) < _STAR_RESIDUAL_MIN_MATCHES:

        return None

    offset_array = np.asarray(candidate_offsets, dtype=np.float64)

    median_shift = np.median(offset_array, axis=0)

    residual_distances = np.hypot(offset_array[:, 0] - median_shift[0], offset_array[:, 1] - median_shift[1])

    inlier_offsets = offset_array[residual_distances <= _STAR_RESIDUAL_MAX_SCATTER_PX]

    if inlier_offsets.shape[0] < _STAR_RESIDUAL_MIN_MATCHES:

        return None

    final_shift = np.median(inlier_offsets, axis=0)

    final_distances = np.hypot(inlier_offsets[:, 0] - final_shift[0], inlier_offsets[:, 1] - final_shift[1])

    if float(np.median(final_distances)) > _STAR_RESIDUAL_MAX_SCATTER_PX:

        return None

    shift_length = float(np.hypot(final_shift[0], final_shift[1]))

    if not np.isfinite(shift_length) or shift_length > max_shift:

        return None

    return float(final_shift[0]), float(final_shift[1])



def _alignment_detection_plane(data: np.ndarray) -> np.ndarray:

    normalized = _normalize_image_axes(data)

    if normalized.ndim == 2:

        plane = np.asarray(normalized, dtype=np.float64)

    else:

        plane = np.asarray(normalized[:, :, : min(3, normalized.shape[2])], dtype=np.float64)

        plane = np.nanmean(plane, axis=2)

    plane = np.asarray(plane, dtype=np.float64)

    finite = np.isfinite(plane)

    if not np.any(finite):

        return np.zeros(plane.shape, dtype=np.float64)

    fill_value = float(np.nanmedian(plane[finite]))

    return np.where(finite, plane, fill_value)



def _extract_alignment_stars(plane: np.ndarray) -> list[_AlignmentStar]:

    if ndimage is None:

        return []

    image = np.asarray(plane, dtype=np.float64)

    if image.ndim != 2 or image.shape[0] < 7 or image.shape[1] < 7:

        return []

    finite = np.isfinite(image)

    if not np.any(finite):

        return []

    finite_values = image[finite]

    background = float(np.nanmedian(finite_values))

    absolute_deviation = np.abs(finite_values - background)

    noise = 1.4826 * float(np.nanmedian(absolute_deviation))

    if not np.isfinite(noise) or noise <= 0.0:

        noise = float(np.nanstd(finite_values))

    if not np.isfinite(noise) or noise <= 0.0:

        return []

    high_value = float(np.nanpercentile(finite_values, 99.5))

    threshold = max(background + 5.0 * noise, background + 0.15 * max(0.0, high_value - background))

    local_maximum = ndimage.maximum_filter(image, size=5, mode="nearest")

    peak_mask = finite & (image == local_maximum) & (image > threshold)

    peak_mask[:2, :] = False

    peak_mask[-2:, :] = False

    peak_mask[:, :2] = False

    peak_mask[:, -2:] = False

    peak_rows, peak_columns = np.nonzero(peak_mask)

    if peak_rows.size == 0:

        return []

    order = np.argsort(image[peak_rows, peak_columns])[::-1]

    selected_stars: list[_AlignmentStar] = []

    min_separation_squared = _ALIGNMENT_STAR_MIN_SEPARATION_PX * _ALIGNMENT_STAR_MIN_SEPARATION_PX

    for peak_index in order:

        peak_row = int(peak_rows[peak_index])

        peak_column = int(peak_columns[peak_index])

        if any((star.row - peak_row) ** 2 + (star.column - peak_column) ** 2 < min_separation_squared for star in selected_stars):

            continue

        centroid_row, centroid_column = _centroid_peak(image, peak_row, peak_column, background)

        selected_stars.append(
            _AlignmentStar(
                row=centroid_row,
                column=centroid_column,
                brightness=float(image[peak_row, peak_column]),
            )
        )

        if len(selected_stars) >= _ALIGNMENT_STAR_MAX_COUNT:

            break

    return selected_stars



def _centroid_peak(image: np.ndarray, peak_row: int, peak_column: int, background: float) -> tuple[float, float]:

    row_start = max(0, peak_row - 2)

    row_stop = min(image.shape[0], peak_row + 3)

    column_start = max(0, peak_column - 2)

    column_stop = min(image.shape[1], peak_column + 3)

    patch = np.asarray(image[row_start:row_stop, column_start:column_stop], dtype=np.float64)

    weights = np.clip(patch - background, 0.0, None)

    weights[~np.isfinite(weights)] = 0.0

    total_weight = float(np.sum(weights))

    if total_weight <= 0.0:

        return float(peak_row), float(peak_column)

    row_indices, column_indices = np.indices(patch.shape, dtype=np.float64)

    centroid_row = float(np.sum((row_indices + row_start) * weights) / total_weight)

    centroid_column = float(np.sum((column_indices + column_start) * weights) / total_weight)

    return centroid_row, centroid_column



def _max_star_residual_shift(shape: tuple[int, int]) -> float:

    height, width = shape

    fractional_limit = float(min(height, width)) * _STAR_RESIDUAL_MAX_SHIFT_FRACTION

    return max(4.0, min(_STAR_RESIDUAL_MAX_SHIFT_PX, fractional_limit))



def _shift_image_data(data: np.ndarray, shift: tuple[float, float]) -> np.ndarray:

    if ndimage is None:

        return np.asarray(data, dtype=np.float32)

    shift_row, shift_column = shift

    normalized = _normalize_image_axes(data)

    if normalized.ndim == 2:

        shifted = ndimage.shift(
            np.asarray(normalized, dtype=np.float32),
            shift=(shift_row, shift_column),
            order=1,
            mode="constant",
            cval=0.0,
            prefilter=False,
        )

    else:

        shifted = ndimage.shift(
            np.asarray(normalized, dtype=np.float32),
            shift=(shift_row, shift_column, 0.0),
            order=1,
            mode="constant",
            cval=0.0,
            prefilter=False,
        )

    shifted = np.asarray(shifted, dtype=np.float32)

    shifted[~np.isfinite(shifted)] = 0.0

    return shifted





def _normalize_image_axes(source_data: np.ndarray) -> np.ndarray:

    data = np.asarray(source_data)

    if data.ndim == 2:

        return data

    if data.ndim == 3 and data.shape[-1] in {1, 3, 4}:

        return data

    if data.ndim == 3 and data.shape[0] in {1, 3, 4}:

        return np.moveaxis(data, 0, -1)

    raise ValueError("Alignment currently supports grayscale or simple RGB/RGBA images only.")





def _reproject_plane(

    plane: np.ndarray,

    source_wcs: WCS,

    reference_wcs: WCS,

    target_shape: tuple[int, int],

) -> np.ndarray:

    aligned_plane = reproject_interp(

        (np.asarray(plane, dtype=np.float32), source_wcs),

        reference_wcs,

        shape_out=target_shape,

        return_footprint=False,

        order="bilinear",

    )

    aligned_plane = np.asarray(aligned_plane, dtype=np.float32)

    aligned_plane[~np.isfinite(aligned_plane)] = 0.0

    return aligned_plane





def _aligned_output_header(
    source_header: Header,
    reference_wcs: WCS,
    source_path: Path,
    reference_path: Path,
    *,
    residual_shift: tuple[float, float] | None = None,
) -> Header:

    output_header = Header()

    for card in source_header.cards:

        keyword = card.keyword

        if keyword in {"SIMPLE", "BITPIX", "NAXIS", "NAXIS1", "NAXIS2", "NAXIS3", "EXTEND", "PCOUNT", "GCOUNT", "CHECKSUM", "DATASUM"}:

            continue

        if _WCS_KEY_PATTERN.match(keyword):

            continue

        try:

            output_header.append((card.keyword, card.value, card.comment))

        except Exception:

            continue

    output_header.extend(reference_wcs.to_header(relax=True).cards, update=True)

    for keyword in ("EXPTIME", "FILTER", "OBJECT"):

        if keyword in source_header:

            output_header[keyword] = source_header[keyword]

    _apply_aligned_time_keywords(output_header, source_header)

    if residual_shift is not None:

        output_header["ALNDY"] = (float(residual_shift[0]), "Star residual alignment shift in Y pixels")

        output_header["ALNDX"] = (float(residual_shift[1]), "Star residual alignment shift in X pixels")

        output_header.add_history(
            f"Citizen Photometry applied star residual shift dy={residual_shift[0]:.3f}, dx={residual_shift[1]:.3f}."
        )

    output_header.add_history(f"Citizen Photometry aligned this frame onto the reference grid of {reference_path.name}.")

    output_header.add_history(f"Original source frame: {source_path.name}.")

    return output_header





def _apply_aligned_time_keywords(output_header: Header, source_header: Header) -> None:

    for date_keyword, mjd_keyword in (("DATE-OBS", "MJD-OBS"), ("DATE-END", "MJD-END"), ("DATE-AVG", "MJD-AVG")):

        if date_keyword not in source_header and mjd_keyword not in source_header:

            continue

        normalized_text = _normalized_utc_time_text(source_header.get(date_keyword))

        if normalized_text is not None:

            output_header[date_keyword] = normalized_text

            output_header[mjd_keyword] = _mjd_from_time_text(normalized_text)

            continue

        if date_keyword in source_header:

            output_header[date_keyword] = source_header[date_keyword]

        if mjd_keyword in source_header:

            output_header[mjd_keyword] = source_header[mjd_keyword]





def _normalized_utc_time_text(value: object) -> str | None:

    if value is None:

        return None

    text = str(value).strip()

    if not text:

        return None

    try:

        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))

    except ValueError:

        return None

    if parsed.tzinfo is None:

        return None

    utc_value = parsed.astimezone(UTC)

    time_text = utc_value.strftime("%Y-%m-%dT%H:%M:%S.%f").rstrip("0").rstrip(".")

    return f"{time_text}Z"





def _mjd_from_time_text(value: str) -> float:

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))

    return ((parsed.timestamp() / 86400.0) + 40587.0)





def _emit_progress(progress_callback: Callable[[str], None] | None, message: str) -> None:

    if progress_callback is not None:

        progress_callback(message)