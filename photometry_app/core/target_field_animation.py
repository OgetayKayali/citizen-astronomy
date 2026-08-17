from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
import hashlib
import math
from pathlib import Path

from matplotlib.figure import Figure
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter

from photometry_app.core.alignment import (
    _alignment_detection_plane,
    _extract_alignment_stars,
    _fast_affine_pixel_transform,
)
from photometry_app.core.animation_export import export_qimages_to_gif
from photometry_app.core.exporters import AnimatedLightCurveExportCanceled
from photometry_app.core.image_io import read_photometry_image_data
from photometry_app.core.models import LightCurvePoint, LightCurveSeries, PhotometryMeasurement, ProcessingReport
from photometry_app.core.plotting import (
    LightCurvePlotPayload,
    _stretched_image_data,
    build_light_curve_plot_payload,
    plot_light_curve_payload,
)


DEFAULT_TARGET_FIELD_FOV_PX = 250
MIN_TARGET_FIELD_FOV_PX = 32
MAX_TARGET_FIELD_FOV_PX = 2000
DEFAULT_TARGET_FIELD_FPS = 12.0
MIN_TARGET_FIELD_FPS = 1.0
MAX_TARGET_FIELD_FPS = 30.0
DEFAULT_TARGET_FIELD_ALIGN = True
DEFAULT_TARGET_FIELD_STRETCH_MODE = "stf_bright"
TARGET_FIELD_STRETCH_MODES = ("stf_bright", "stf", "asinh", "sqrt", "log", "linear")
TARGET_FIELD_STRETCH_MODE_LABELS = {
    "stf_bright": "STF Bright",
    "stf": "STF",
    "asinh": "Asinh",
    "sqrt": "Sqrt",
    "log": "Log",
    "linear": "Linear",
}
TARGET_FIELD_ALIGN_ORIENTATIONS = ("identity", "rot180", "flip_lr", "flip_ud")
_STRETCH_LOW_PERCENTILE = 0.5
_STRETCH_HIGH_PERCENTILE = 99.85
_ALIGN_MAX_SHIFT_PX = 48.0
_ALIGN_MIN_SHIFT_PX = 10.0
_ALIGN_MAX_SHIFT_FRACTION = 0.10
_ALIGN_ORIENTATION_IMPROVEMENT = 1.12
_ALIGN_MIN_ORIENTATION_RUN = 2
_FULL_FRAME_ALIGN_MAX_EDGE = 512
_FULL_FRAME_ALIGN_SHIFT_FRACTION = 0.20


class TargetFieldAnimationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class TargetFieldFrame:
    measurement: PhotometryMeasurement
    point: LightCurvePoint | None


@dataclass(frozen=True, slots=True)
class StampAlignmentSolution:
    orientation: str = "identity"
    shift_y: float = 0.0
    shift_x: float = 0.0
    score: float = 0.0


@dataclass(frozen=True, slots=True)
class TargetFieldAnimationExportOptions:
    fov_px: int = DEFAULT_TARGET_FIELD_FOV_PX
    align: bool = DEFAULT_TARGET_FIELD_ALIGN
    fps: float = DEFAULT_TARGET_FIELD_FPS
    stretch_mode: str = DEFAULT_TARGET_FIELD_STRETCH_MODE

    def normalized(self) -> TargetFieldAnimationExportOptions:
        return TargetFieldAnimationExportOptions(
            fov_px=normalize_target_field_fov_px(self.fov_px),
            align=bool(self.align),
            fps=normalize_target_field_fps(self.fps),
            stretch_mode=normalize_target_field_stretch_mode(self.stretch_mode),
        )

    @property
    def frame_duration_ms(self) -> int:
        return target_field_frame_duration_ms(self.fps)


def normalize_target_field_fov_px(value: object, default: int = DEFAULT_TARGET_FIELD_FOV_PX) -> int:
    try:
        fov_px = int(value)
    except (TypeError, ValueError):
        fov_px = int(default)
    return min(MAX_TARGET_FIELD_FOV_PX, max(MIN_TARGET_FIELD_FOV_PX, fov_px))


def normalize_target_field_fps(value: object, default: float = DEFAULT_TARGET_FIELD_FPS) -> float:
    try:
        fps = float(value)
    except (TypeError, ValueError):
        fps = float(default)
    if not math.isfinite(fps):
        fps = float(default)
    return min(MAX_TARGET_FIELD_FPS, max(MIN_TARGET_FIELD_FPS, fps))


def target_field_frame_duration_ms(fps: object, default: float = DEFAULT_TARGET_FIELD_FPS) -> int:
    resolved_fps = normalize_target_field_fps(fps, default=default)
    return max(20, int(round(1000.0 / resolved_fps)))


def normalize_target_field_stretch_mode(
    value: object,
    default: str = DEFAULT_TARGET_FIELD_STRETCH_MODE,
) -> str:
    mode = str(value or default).strip().lower()
    if mode in TARGET_FIELD_STRETCH_MODES:
        return mode
    return default if default in TARGET_FIELD_STRETCH_MODES else DEFAULT_TARGET_FIELD_STRETCH_MODE


def collect_target_field_frames(
    report: ProcessingReport,
    source_id: str,
    *,
    filter_name: str | None = None,
) -> list[TargetFieldFrame]:
    wanted_id = str(source_id or "").strip()
    if not wanted_id:
        raise TargetFieldAnimationError("Select a target before exporting a target-field animation.")
    wanted_filter = None if filter_name is None else str(filter_name or "unknown")
    measurements = [
        measurement
        for measurement in report.measurements
        if not measurement.is_reference
        and str(measurement.source_id) == wanted_id
        and (wanted_filter is None or (measurement.filter_name or "unknown") == wanted_filter)
    ]
    if not measurements:
        raise TargetFieldAnimationError("The selected target has no measured frames to crop.")
    measurements.sort(key=lambda item: (_observation_sort_key(item.observation_time), str(item.file_path)))
    points_by_file = _light_curve_points_by_file(report, wanted_id, wanted_filter)
    return [
        TargetFieldFrame(measurement=measurement, point=points_by_file.get(_file_key(measurement.file_path)))
        for measurement in measurements
    ]


def crop_target_stamp(image: np.ndarray, x: float, y: float, fov_px: int) -> np.ndarray:
    plane = _as_grayscale(image)
    size = max(1, int(fov_px))
    height, width = plane.shape
    x0 = int(round(float(x) - (size - 1) / 2.0))
    y0 = int(round(float(y) - (size - 1) / 2.0))
    stamp = np.full((size, size), np.nan, dtype=float)
    src_x0 = max(0, x0)
    src_y0 = max(0, y0)
    src_x1 = min(width, x0 + size)
    src_y1 = min(height, y0 + size)
    if src_x1 <= src_x0 or src_y1 <= src_y0:
        return stamp
    dst_x0 = src_x0 - x0
    dst_y0 = src_y0 - y0
    stamp[dst_y0 : dst_y0 + (src_y1 - src_y0), dst_x0 : dst_x0 + (src_x1 - src_x0)] = plane[src_y0:src_y1, src_x0:src_x1]
    return stamp


def star_positions_by_file(
    report: ProcessingReport,
    *,
    filter_name: str | None = None,
) -> dict[str, dict[str, tuple[float, float]]]:
    wanted_filter = None if filter_name is None else str(filter_name or "unknown")
    mapping: dict[str, dict[str, tuple[float, float]]] = {}
    for measurement in report.measurements:
        if wanted_filter is not None and (measurement.filter_name or "unknown") != wanted_filter:
            continue
        if not math.isfinite(float(measurement.x)) or not math.isfinite(float(measurement.y)):
            continue
        file_key = _file_key(measurement.file_path)
        mapping.setdefault(file_key, {})[str(measurement.source_id)] = (float(measurement.x), float(measurement.y))
    return mapping


def local_comparison_scale_factors(
    report: ProcessingReport,
    frames: Sequence[TargetFieldFrame],
    *,
    fov_px: int,
) -> list[float]:
    frame_count = len(frames)
    if frame_count < 2:
        return [1.0] * frame_count
    targets_by_file = {
        _file_key(frame.measurement.file_path): frame.measurement
        for frame in frames
    }
    frame_index_by_file = {
        _file_key(frame.measurement.file_path): index
        for index, frame in enumerate(frames)
    }
    half_fov = max(1.0, float(fov_px) / 2.0)
    local_fluxes: dict[str, list[float | None]] = {}
    inside_counts: dict[str, int] = {}
    for measurement in report.measurements:
        if not measurement.is_reference:
            continue
        file_key = _file_key(measurement.file_path)
        target = targets_by_file.get(file_key)
        frame_index = frame_index_by_file.get(file_key)
        if target is None or frame_index is None:
            continue
        if (measurement.filter_name or "unknown") != (target.filter_name or "unknown"):
            continue
        if not all(
            math.isfinite(float(value))
            for value in (measurement.x, measurement.y, target.x, target.y)
        ):
            continue
        edge_margin = max(4.0, float(measurement.annulus_outer_radius or 0.0))
        usable_half_fov = half_fov - edge_margin
        if usable_half_fov <= 0.0:
            continue
        if (
            abs(float(measurement.x) - float(target.x)) > usable_half_fov
            or abs(float(measurement.y) - float(target.y)) > usable_half_fov
        ):
            continue
        source_id = str(measurement.source_id)
        values = local_fluxes.setdefault(source_id, [None] * frame_count)
        inside_counts[source_id] = inside_counts.get(source_id, 0) + 1
        if (
            measurement.flux is not None
            and math.isfinite(float(measurement.flux))
            and float(measurement.flux) > 0.0
            and not measurement.is_saturated
            and not measurement.is_near_saturated
        ):
            values[frame_index] = float(measurement.flux)

    minimum_inside = max(2, int(math.ceil(frame_count * 0.8)))
    minimum_fluxes = max(2, int(math.ceil(frame_count * 0.6)))
    reference_fluxes: dict[str, tuple[list[float | None], float]] = {}
    for source_id, values in local_fluxes.items():
        finite = [value for value in values if value is not None and math.isfinite(value) and value > 0.0]
        if inside_counts.get(source_id, 0) < minimum_inside or len(finite) < minimum_fluxes:
            continue
        reference_fluxes[source_id] = (values, float(np.median(finite)))
    if not reference_fluxes:
        return [1.0] * frame_count

    scales = np.full(frame_count, np.nan, dtype=float)
    for frame_index in range(frame_count):
        ratios = [
            reference_flux / values[frame_index]
            for values, reference_flux in reference_fluxes.values()
            if values[frame_index] is not None and values[frame_index] > 0.0
        ]
        if ratios:
            scales[frame_index] = float(np.median(ratios))
    valid = np.flatnonzero(np.isfinite(scales) & (scales > 0.0))
    if valid.size == 0:
        return [1.0] * frame_count
    if valid.size < frame_count:
        missing = np.flatnonzero(~np.isfinite(scales) | (scales <= 0.0))
        scales[missing] = np.interp(missing, valid, scales[valid])
    median_scale = float(np.median(scales[valid]))
    if math.isfinite(median_scale) and median_scale > 0.0:
        scales /= median_scale
    return [float(np.clip(value, 0.25, 4.0)) for value in scales]


def crop_comparison_scale_factors(
    stamps: Sequence[np.ndarray],
) -> list[float] | None:
    frame_count = len(stamps)
    if frame_count < 2:
        return [1.0] * frame_count
    reference = np.asarray(stamps[0], dtype=float)
    if reference.ndim != 2 or min(reference.shape) < 32:
        return None
    detected = []
    sample_indices = np.linspace(0, frame_count - 1, min(frame_count, 12), dtype=int)
    for sample_index in sample_indices:
        sample = np.asarray(stamps[int(sample_index)], dtype=float)
        if sample.shape != reference.shape:
            continue
        sample_stars = _extract_alignment_stars(_alignment_detection_plane(sample))
        if len(sample_stars) > len(detected):
            detected = sample_stars
    height, width = reference.shape
    center_y = (height - 1) / 2.0
    center_x = (width - 1) / 2.0
    target_exclusion_radius = max(12.0, min(height, width) * 0.08)
    aperture_radius = max(3.0, min(6.0, min(height, width) * 0.015))
    edge_margin = aperture_radius + 9.0
    candidates = [
        (float(star.column), float(star.row))
        for star in detected
        if math.hypot(float(star.column) - center_x, float(star.row) - center_y) >= target_exclusion_radius
        and edge_margin <= float(star.column) < (width - edge_margin)
        and edge_margin <= float(star.row) < (height - edge_margin)
    ][:32]
    if len(candidates) < 2:
        return None

    flux_rows: list[list[float | None]] = []
    minimum_fluxes = max(2, int(math.ceil(frame_count * 0.6)))
    for column, row in candidates:
        values = [
            _measure_crop_star_flux(stamp, column, row, aperture_radius=aperture_radius)
            for stamp in stamps
        ]
        finite = [value for value in values if value is not None and value > 0.0]
        if len(finite) >= minimum_fluxes:
            flux_rows.append(values)
    if len(flux_rows) < 2:
        return None

    scales = _comparison_scales_from_flux_rows(flux_rows, frame_count)
    if scales is None:
        return None
    return scales


def estimate_alignment_from_star_positions(
    reference_positions: dict[str, tuple[float, float]],
    source_positions: dict[str, tuple[float, float]],
    image_shape: tuple[int, int],
    *,
    previous_orientation: str = "identity",
) -> StampAlignmentSolution | None:
    shared_ids = [source_id for source_id in reference_positions if source_id in source_positions]
    if len(shared_ids) < 1:
        return None
    height, width = int(image_shape[0]), int(image_shape[1])
    candidates: dict[str, tuple[StampAlignmentSolution, float, int]] = {}
    for orientation in TARGET_FIELD_ALIGN_ORIENTATIONS:
        shifts = []
        for source_id in shared_ids:
            source_x, source_y = _oriented_source_xy(
                source_positions[source_id][0],
                source_positions[source_id][1],
                orientation,
                width,
                height,
            )
            reference_x, reference_y = reference_positions[source_id]
            shifts.append((reference_y - source_y, reference_x - source_x))
        candidates[orientation] = _robust_shift_solution(shifts, orientation=orientation)
    return _choose_orientation_from_candidates(
        candidates,
        previous_orientation=previous_orientation,
        match_count=len(shared_ids),
    )


def estimate_full_frame_alignment(
    reference: np.ndarray,
    source: np.ndarray,
    *,
    previous_orientation: str = "identity",
    reference_positions: dict[str, tuple[float, float]] | None = None,
    source_positions: dict[str, tuple[float, float]] | None = None,
) -> StampAlignmentSolution:
    source_plane = np.asarray(_as_grayscale(source), dtype=float)
    if reference_positions and source_positions:
        solution = estimate_alignment_from_star_positions(
            reference_positions,
            source_positions,
            source_plane.shape,
            previous_orientation=previous_orientation,
        )
        if solution is not None:
            return solution
    detected = _estimate_alignment_from_detected_stars(
        reference,
        source_plane,
        previous_orientation=previous_orientation,
    )
    if detected is not None:
        return detected
    return _translation_only_full_frame_alignment(reference, source_plane)


def crop_wcs_aligned_stamp(
    source_image: np.ndarray,
    source_wcs,
    reference_wcs,
    *,
    reference_shape: tuple[int, int],
    center_x: float,
    center_y: float,
    fov_px: int,
) -> np.ndarray | None:
    if source_wcs is None or reference_wcs is None:
        return None
    affine = _fast_affine_pixel_transform(source_wcs, reference_wcs, reference_shape)
    if affine is None:
        return None
    try:
        from scipy import ndimage
    except ImportError:
        return None
    matrix, offset = affine
    size = max(1, int(fov_px))
    y0 = float(center_y) - (size - 1) / 2.0
    x0 = float(center_x) - (size - 1) / 2.0
    crop_offset = matrix @ np.asarray([y0, x0], dtype=float) + offset
    plane = np.asarray(_as_grayscale(source_image), dtype=np.float32)
    sampled = ndimage.affine_transform(
        plane,
        matrix,
        offset=crop_offset,
        output_shape=(size, size),
        order=1,
        mode="constant",
        cval=np.nan,
        prefilter=False,
    )
    return np.asarray(sampled, dtype=float)


def crop_image_aligned_stamp(
    source_image: np.ndarray,
    solution: StampAlignmentSolution,
    *,
    center_x: float,
    center_y: float,
    fov_px: int,
) -> np.ndarray:
    plane = np.asarray(_as_grayscale(source_image), dtype=float)
    size = max(1, int(fov_px))
    height, width = plane.shape
    y0 = float(center_y) - (size - 1) / 2.0
    x0 = float(center_x) - (size - 1) / 2.0
    rows, columns = np.mgrid[0:size, 0:size]
    sample_y = rows + y0 - float(solution.shift_y)
    sample_x = columns + x0 - float(solution.shift_x)
    if solution.orientation == "rot180":
        sample_y = (height - 1) - sample_y
        sample_x = (width - 1) - sample_x
    elif solution.orientation == "flip_lr":
        sample_x = (width - 1) - sample_x
    elif solution.orientation == "flip_ud":
        sample_y = (height - 1) - sample_y
    try:
        from scipy import ndimage
    except ImportError:
        ndimage = None
    if ndimage is not None:
        sampled = ndimage.map_coordinates(
            np.nan_to_num(plane, nan=float(estimate_stamp_background(plane))),
            [sample_y, sample_x],
            order=1,
            mode="constant",
            cval=np.nan,
            prefilter=False,
        )
        return np.asarray(sampled, dtype=float)
    stamp = np.full((size, size), np.nan, dtype=float)
    src_y = np.rint(sample_y).astype(int)
    src_x = np.rint(sample_x).astype(int)
    valid = (src_y >= 0) & (src_y < height) & (src_x >= 0) & (src_x < width)
    stamp[valid] = plane[src_y[valid], src_x[valid]]
    return stamp


def crop_target_centered_aligned_stamp(
    source_image: np.ndarray,
    solution: StampAlignmentSolution,
    *,
    target_x: float,
    target_y: float,
    fov_px: int,
) -> np.ndarray:
    stamp = crop_target_stamp(source_image, target_x, target_y, fov_px)
    return orient_target_stamp(stamp, solution.orientation)


def estimate_stamp_background(stamp: np.ndarray) -> float:
    values = np.asarray(stamp, dtype=float)
    if values.size == 0:
        return 0.0
    samples = values[np.isfinite(values)]
    if samples.size == 0:
        return 0.0
    for _iteration in range(4):
        center = float(np.median(samples))
        mad = float(np.median(np.abs(samples - center)))
        sigma = 1.4826 * mad
        if not math.isfinite(sigma) or sigma <= 1.0e-12:
            break
        clipped = samples[np.abs(samples - center) <= (3.5 * sigma)]
        if clipped.size < 16 or clipped.size == samples.size:
            break
        samples = clipped
    return float(np.median(samples))


def align_target_stamps(stamps: Sequence[np.ndarray]) -> list[np.ndarray]:
    if not stamps:
        return []
    solutions = estimate_stamp_alignments(stamps)
    return [apply_stamp_alignment(stamp, solution) for stamp, solution in zip(stamps, solutions, strict=True)]


def estimate_stamp_alignments(stamps: Sequence[np.ndarray]) -> list[StampAlignmentSolution]:
    if not stamps:
        return []
    reference = np.asarray(stamps[0], dtype=float)
    max_shift = _max_align_shift(reference.shape)
    candidate_maps = [
        _stamp_alignment_candidates(reference, np.asarray(stamp, dtype=float), max_shift=max_shift)
        for stamp in stamps[1:]
    ]
    orientations = ["identity"]
    for options in candidate_maps:
        orientations.append(_choose_alignment_orientation(options, previous=orientations[-1]))
    orientations = _stabilize_orientations(orientations)
    solutions = [StampAlignmentSolution(score=1.0)]
    for options, orientation in zip(candidate_maps, orientations[1:], strict=True):
        solutions.append(options.get(orientation, StampAlignmentSolution(orientation=orientation)))
    return solutions


def estimate_stamp_alignment(
    reference: np.ndarray,
    stamp: np.ndarray,
    *,
    max_shift: float | None = None,
    previous_orientation: str = "identity",
) -> StampAlignmentSolution:
    limit = _max_align_shift(np.asarray(reference).shape) if max_shift is None else float(max_shift)
    options = _stamp_alignment_candidates(reference, stamp, max_shift=limit)
    orientation = _choose_alignment_orientation(options, previous=previous_orientation)
    return options.get(orientation, StampAlignmentSolution(orientation=orientation))


def estimate_stamp_alignment_shift(
    reference: np.ndarray,
    stamp: np.ndarray,
    *,
    max_shift: float | None = None,
) -> tuple[float, float]:
    solution = estimate_stamp_alignment(reference, stamp, max_shift=max_shift)
    return solution.shift_y, solution.shift_x


def orient_target_stamp(stamp: np.ndarray, orientation: str) -> np.ndarray:
    values = np.asarray(stamp, dtype=float)
    if orientation == "rot180":
        return np.rot90(values, 2)
    if orientation == "flip_lr":
        return np.fliplr(values)
    if orientation == "flip_ud":
        return np.flipud(values)
    return values.copy()


def apply_stamp_alignment(stamp: np.ndarray, solution: StampAlignmentSolution) -> np.ndarray:
    oriented = orient_target_stamp(stamp, solution.orientation)
    return shift_target_stamp(oriented, (solution.shift_y, solution.shift_x))


def shift_target_stamp(stamp: np.ndarray, shift: tuple[float, float]) -> np.ndarray:
    values = np.asarray(stamp, dtype=float)
    shift_y, shift_x = float(shift[0]), float(shift[1])
    if abs(shift_y) < 1.0e-6 and abs(shift_x) < 1.0e-6:
        return values.copy()
    try:
        from scipy import ndimage
    except ImportError:
        ndimage = None
    if ndimage is not None:
        shifted = ndimage.shift(
            np.nan_to_num(values, nan=float(estimate_stamp_background(values))),
            shift=(shift_y, shift_x),
            order=3,
            mode="constant",
            cval=np.nan,
            prefilter=True,
        )
        return np.asarray(shifted, dtype=float)
    return _integer_shift_stamp(values, int(round(shift_y)), int(round(shift_x)))


def _measure_crop_star_flux(
    stamp: np.ndarray,
    expected_x: float,
    expected_y: float,
    *,
    aperture_radius: float,
) -> float | None:
    values = np.asarray(stamp, dtype=float)
    if values.ndim != 2:
        return None
    search_radius = 3
    center_x = int(round(expected_x))
    center_y = int(round(expected_y))
    search_y0 = max(0, center_y - search_radius)
    search_y1 = min(values.shape[0], center_y + search_radius + 1)
    search_x0 = max(0, center_x - search_radius)
    search_x1 = min(values.shape[1], center_x + search_radius + 1)
    search_patch = values[search_y0:search_y1, search_x0:search_x1]
    finite_search = np.isfinite(search_patch)
    if not np.any(finite_search):
        return None
    peak_index = int(np.argmax(np.where(finite_search, search_patch, -np.inf)))
    peak_y_local, peak_x_local = np.unravel_index(peak_index, search_patch.shape)
    peak_y = float(search_y0 + peak_y_local)
    peak_x = float(search_x0 + peak_x_local)

    outer_radius = aperture_radius + 4.0
    y0 = max(0, int(math.floor(peak_y - outer_radius)))
    y1 = min(values.shape[0], int(math.ceil(peak_y + outer_radius)) + 1)
    x0 = max(0, int(math.floor(peak_x - outer_radius)))
    x1 = min(values.shape[1], int(math.ceil(peak_x + outer_radius)) + 1)
    patch = values[y0:y1, x0:x1]
    rows, columns = np.indices(patch.shape, dtype=float)
    radius_squared = (rows + y0 - peak_y) ** 2 + (columns + x0 - peak_x) ** 2
    finite = np.isfinite(patch)
    aperture = finite & (radius_squared <= aperture_radius * aperture_radius)
    annulus = finite & (radius_squared >= (aperture_radius + 2.0) ** 2) & (
        radius_squared <= outer_radius * outer_radius
    )
    if np.count_nonzero(aperture) < 8 or np.count_nonzero(annulus) < 12:
        return None
    background = float(np.median(patch[annulus]))
    flux = float(np.sum(patch[aperture] - background))
    return flux if math.isfinite(flux) and flux > 0.0 else None


def _comparison_scales_from_flux_rows(
    flux_rows: Sequence[Sequence[float | None]],
    frame_count: int,
) -> list[float] | None:
    reference_rows: list[tuple[Sequence[float | None], float]] = []
    for values in flux_rows:
        finite = [
            float(value)
            for value in values
            if value is not None and math.isfinite(float(value)) and float(value) > 0.0
        ]
        if len(finite) >= 2:
            reference_rows.append((values, float(np.median(finite))))
    if len(reference_rows) < 2:
        return None
    scales = np.full(frame_count, np.nan, dtype=float)
    for frame_index in range(frame_count):
        ratios = [
            reference_flux / float(values[frame_index])
            for values, reference_flux in reference_rows
            if values[frame_index] is not None
            and math.isfinite(float(values[frame_index]))
            and float(values[frame_index]) > 0.0
        ]
        if len(ratios) >= 2:
            scales[frame_index] = float(np.median(ratios))
    valid = np.flatnonzero(np.isfinite(scales) & (scales > 0.0))
    if valid.size == 0:
        return None
    if valid.size < frame_count:
        missing = np.flatnonzero(~np.isfinite(scales) | (scales <= 0.0))
        scales[missing] = np.interp(missing, valid, scales[valid])
    median_scale = float(np.median(scales[valid]))
    if math.isfinite(median_scale) and median_scale > 0.0:
        scales /= median_scale
    if len(reference_rows) >= 5:
        maximum_scale = 25.0
    elif len(reference_rows) >= 3:
        maximum_scale = 12.0
    else:
        maximum_scale = 4.0
    return [float(np.clip(value, 0.25, maximum_scale)) for value in scales]


def match_stamp_backgrounds(
    stamps: Sequence[np.ndarray],
    *,
    scale_factors: Sequence[float] | None = None,
) -> list[np.ndarray]:
    backgrounds = [estimate_stamp_background(stamp) for stamp in stamps]
    finite_backgrounds = [value for value in backgrounds if math.isfinite(value)]
    reference = float(np.median(finite_backgrounds)) if finite_backgrounds else 0.0
    scales = list(scale_factors) if scale_factors is not None else [1.0] * len(stamps)
    if len(scales) != len(stamps):
        raise ValueError("Background scale count must match the number of stamps.")
    matched: list[np.ndarray] = []
    for stamp, background, scale in zip(stamps, backgrounds, scales, strict=True):
        resolved_background = background if math.isfinite(background) else 0.0
        resolved_scale = float(scale) if math.isfinite(float(scale)) and float(scale) > 0.0 else 1.0
        matched.append(
            (np.asarray(stamp, dtype=float) - resolved_background) * resolved_scale
            + reference
        )
    return matched


def stretch_stamps_to_shared_display(
    stamps: Sequence[np.ndarray],
    stretch_mode: str = DEFAULT_TARGET_FIELD_STRETCH_MODE,
) -> list[np.ndarray]:
    mode = normalize_target_field_stretch_mode(stretch_mode)
    filled: list[np.ndarray] = []
    for stamp in stamps:
        values = np.asarray(stamp, dtype=float)
        background = estimate_stamp_background(values)
        fill = background if math.isfinite(background) else 0.0
        filled.append(np.where(np.isfinite(values), values, fill))
    finite_values = [values[np.isfinite(values)] for values in filled]
    combined = (
        np.concatenate([values for values in finite_values if values.size > 0])
        if any(values.size for values in finite_values)
        else np.asarray([], dtype=float)
    )
    if combined.size == 0:
        return [np.zeros(np.asarray(stamp).shape, dtype=np.float32) for stamp in stamps]
    low = float(np.percentile(combined, _STRETCH_LOW_PERCENTILE))
    high = float(np.percentile(combined, _STRETCH_HIGH_PERCENTILE))
    if not math.isfinite(low) or not math.isfinite(high) or high <= low:
        low = float(np.nanmin(combined))
        high = float(np.nanmax(combined))
    span = high - low if high > low else 1.0
    normalized_stamps = [np.clip((values - low) / span, 0.0, 1.0) for values in filled]
    stats = _shared_stretch_statistics(normalized_stamps)
    stretched: list[np.ndarray] = []
    for normalized in normalized_stamps:
        display = _stretched_image_data(normalized, stretch_mode=mode, statistics_normalized=stats)
        stretched.append(np.asarray(display, dtype=np.float32))
    return stretched


def target_field_stamp_cache_path(
    cache_dir: Path,
    *,
    source_id: str,
    fov_px: int,
    file_path: Path,
    x: float,
    y: float,
    align: bool = False,
    crop_x: float | None = None,
    crop_y: float | None = None,
) -> Path:
    token = "|".join(
        (
            str(source_id),
            str(int(fov_px)),
            "target-center-v5" if align else "raw-crop",
            str(Path(file_path)),
            f"{float(x):.3f}",
            f"{float(y):.3f}",
            f"{float(crop_x if crop_x is not None else x):.3f}",
            f"{float(crop_y if crop_y is not None else y):.3f}",
        )
    )
    digest = hashlib.sha1(token.encode("utf-8")).hexdigest()[:20]
    return Path(cache_dir) / "target-field-animation" / f"{digest}.npy"


def load_or_create_target_stamp(
    measurement: PhotometryMeasurement,
    *,
    fov_px: int,
    cache_dir: Path | None,
    source_id: str,
) -> np.ndarray:
    cache_path = None
    if cache_dir is not None:
        cache_path = target_field_stamp_cache_path(
            cache_dir,
            source_id=source_id,
            fov_px=fov_px,
            file_path=measurement.file_path,
            x=measurement.x,
            y=measurement.y,
        )
        if cache_path.exists():
            try:
                cached = np.load(cache_path)
                if cached.shape == (int(fov_px), int(fov_px)):
                    return np.asarray(cached, dtype=float)
            except (OSError, ValueError):
                cache_path.unlink(missing_ok=True)
    image_path = Path(measurement.file_path)
    if not image_path.exists():
        raise TargetFieldAnimationError(f"Missing image for target-field animation: {image_path}")
    stamp = crop_target_stamp(read_photometry_image_data(image_path), measurement.x, measurement.y, fov_px)
    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        np.save(cache_path, stamp)
    return stamp


def load_or_create_full_aligned_stamp(
    measurement: PhotometryMeasurement,
    *,
    reference_measurement: PhotometryMeasurement,
    reference_image: np.ndarray,
    fov_px: int,
    cache_dir: Path | None,
    source_id: str,
    previous_orientation: str = "identity",
    reference_positions: dict[str, tuple[float, float]] | None = None,
    source_positions: dict[str, tuple[float, float]] | None = None,
    reference_crop: np.ndarray | None = None,
) -> tuple[np.ndarray, str]:
    cache_path = None
    if cache_dir is not None:
        cache_path = target_field_stamp_cache_path(
            cache_dir,
            source_id=source_id,
            fov_px=fov_px,
            file_path=measurement.file_path,
            x=measurement.x,
            y=measurement.y,
            align=True,
            crop_x=reference_measurement.x,
            crop_y=reference_measurement.y,
        )
        if cache_path.exists():
            try:
                cached = np.load(cache_path)
                if cached.shape == (int(fov_px), int(fov_px)):
                    return np.asarray(cached, dtype=float), previous_orientation
            except (OSError, ValueError):
                cache_path.unlink(missing_ok=True)
    image_path = Path(measurement.file_path)
    if not image_path.exists():
        raise TargetFieldAnimationError(f"Missing image for target-field animation: {image_path}")
    is_reference_frame = image_path == Path(reference_measurement.file_path)
    source_image = (
        np.asarray(reference_image, dtype=float)
        if is_reference_frame
        else _as_grayscale(read_photometry_image_data(image_path))
    )
    stamp = crop_target_stamp(source_image, measurement.x, measurement.y, fov_px)
    if is_reference_frame:
        orientation = "identity"
    else:
        solution = None
        if reference_positions and source_positions:
            shared_count = len(reference_positions.keys() & source_positions.keys())
            if shared_count >= 2:
                solution = estimate_alignment_from_star_positions(
                    reference_positions,
                    source_positions,
                    source_image.shape,
                    previous_orientation=previous_orientation,
                )
        if solution is None:
            aligned_reference_crop = reference_crop
            if aligned_reference_crop is None:
                aligned_reference_crop = crop_target_stamp(
                    reference_image,
                    reference_measurement.x,
                    reference_measurement.y,
                    fov_px,
                )
            solution = estimate_full_frame_alignment(
                aligned_reference_crop,
                stamp,
                previous_orientation=previous_orientation,
            )
        orientation = solution.orientation
        stamp = orient_target_stamp(stamp, orientation)
        # The measurement already centers the selected target. Do not translate
        # this crop to match the surrounding field: residual distortion after
        # an orientation change can otherwise move the target off center.
    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        np.save(cache_path, stamp)
    return stamp, orientation


def stamp_to_qimage(stamp: np.ndarray) -> QImage:
    display = np.clip(np.nan_to_num(np.asarray(stamp, dtype=float), nan=0.0), 0.0, 1.0)
    pixels = np.ascontiguousarray(np.round(display * 255.0).astype(np.uint8))
    height, width = pixels.shape
    image = QImage(pixels.data, width, height, width, QImage.Format.Format_Grayscale8)
    return image.copy()


def compose_target_field_animation_frame(stamp_image: QImage, plot_image: QImage) -> QImage:
    height = max(1, stamp_image.height(), plot_image.height())
    stamp_scaled = stamp_image if stamp_image.height() == height else stamp_image.scaledToHeight(height, Qt.TransformationMode.SmoothTransformation)
    plot_scaled = plot_image if plot_image.height() == height else plot_image.scaledToHeight(height, Qt.TransformationMode.SmoothTransformation)
    composed = QImage(max(1, stamp_scaled.width() + plot_scaled.width()), height, QImage.Format.Format_RGB888)
    composed.fill(QColor("#111111"))
    painter = QPainter(composed)
    try:
        painter.drawImage(0, 0, stamp_scaled.convertToFormat(QImage.Format.Format_RGB888))
        painter.drawImage(stamp_scaled.width(), 0, plot_scaled.convertToFormat(QImage.Format.Format_RGB888))
    finally:
        painter.end()
    return composed


def export_target_field_animation(
    report: ProcessingReport,
    source_id: str,
    output_path: Path,
    *,
    fov_px: int = DEFAULT_TARGET_FIELD_FOV_PX,
    align: bool = DEFAULT_TARGET_FIELD_ALIGN,
    fps: float = DEFAULT_TARGET_FIELD_FPS,
    stretch_mode: str = DEFAULT_TARGET_FIELD_STRETCH_MODE,
    filter_name: str | None = None,
    cache_dir: Path | None = None,
    series: LightCurveSeries | None = None,
    fit_config: object | None = None,
    y_axis_mode: str = "differential_magnitude",
    x_axis_mode: str = "datetime",
    phase_period_hours: float | None = None,
    phase_anchor_mode: str = "first_observation",
    plot_theme: str = "normal",
    custom_theme_colors: dict[str, str] | None = None,
    frame_duration_ms: int | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> None:
    frames = collect_target_field_frames(report, source_id, filter_name=filter_name)
    resolved_fov = normalize_target_field_fov_px(fov_px)
    align_enabled = bool(align)
    duration_ms = max(
        20,
        int(frame_duration_ms) if frame_duration_ms is not None else target_field_frame_duration_ms(fps),
    )
    plot_series = series or _series_for_source(report, source_id, filter_name)
    if plot_series is None or not plot_series.points:
        raise TargetFieldAnimationError("The selected target does not have a light curve to animate.")
    payload = build_light_curve_plot_payload(
        plot_series,
        "No light-curve points are available for animation.",
        fit_config=fit_config,
        y_axis_mode=y_axis_mode,
        x_axis_mode=x_axis_mode,
        phase_period_hours=phase_period_hours,
        phase_anchor_mode=phase_anchor_mode,
    )
    progress_total = len(frames) + 2
    progress_label = "Cropping and aligning target-field frames..." if align_enabled else "Cropping target-field frames..."
    _emit_progress(progress_callback, 0, progress_total, progress_label)
    _raise_if_canceled(is_cancelled)
    stamps: list[np.ndarray] = []
    reference_measurement = frames[0].measurement
    reference_image = None
    reference_crop = None
    previous_orientation = "identity"
    positions_by_file: dict[str, dict[str, tuple[float, float]]] = {}
    if align_enabled:
        reference_image = _as_grayscale(read_photometry_image_data(reference_measurement.file_path))
        reference_crop = crop_target_stamp(reference_image, reference_measurement.x, reference_measurement.y, resolved_fov)
        positions_by_file = star_positions_by_file(report, filter_name=filter_name)
    for index, frame in enumerate(frames, start=1):
        _raise_if_canceled(is_cancelled)
        if align_enabled:
            stamp, previous_orientation = load_or_create_full_aligned_stamp(
                frame.measurement,
                reference_measurement=reference_measurement,
                reference_image=reference_image,
                fov_px=resolved_fov,
                cache_dir=cache_dir,
                source_id=source_id,
                previous_orientation=previous_orientation,
                reference_positions=positions_by_file.get(_file_key(reference_measurement.file_path)),
                source_positions=positions_by_file.get(_file_key(frame.measurement.file_path)),
                reference_crop=reference_crop,
            )
            stamps.append(stamp)
            _emit_progress(
                progress_callback,
                index,
                progress_total,
                f"Cropping and aligning frame {index}/{len(frames)}...",
            )
        else:
            stamps.append(
                load_or_create_target_stamp(
                    frame.measurement,
                    fov_px=resolved_fov,
                    cache_dir=cache_dir,
                    source_id=source_id,
                )
            )
            _emit_progress(progress_callback, index, progress_total, f"Cropping target-field frame {index}/{len(frames)}...")
    _raise_if_canceled(is_cancelled)
    _emit_progress(progress_callback, len(frames), progress_total, "Normalizing local comparison stars and stretching frames...")
    comparison_scales = crop_comparison_scale_factors(stamps)
    if comparison_scales is None:
        comparison_scales = local_comparison_scale_factors(
            report,
            frames,
            fov_px=resolved_fov,
        )
    display_stamps = stretch_stamps_to_shared_display(
        match_stamp_backgrounds(stamps, scale_factors=comparison_scales),
        stretch_mode=stretch_mode,
    )
    composed_frames: list[QImage] = []
    for index, (frame, stamp) in enumerate(zip(frames, display_stamps, strict=True), start=1):
        _raise_if_canceled(is_cancelled)
        highlight = _highlight_for_frame(payload, frame)
        plot_image = _render_light_curve_payload_with_highlight(
            payload,
            highlight_x=None if highlight is None else highlight[0],
            highlight_y=None if highlight is None else highlight[1],
            plot_theme=plot_theme,
            custom_theme_colors=custom_theme_colors,
        )
        composed_frames.append(compose_target_field_animation_frame(stamp_to_qimage(stamp), plot_image))
        _emit_progress(
            progress_callback,
            len(frames),
            progress_total,
            f"Composing animation frame {index}/{len(frames)}...",
        )
    _raise_if_canceled(is_cancelled)
    _emit_progress(progress_callback, len(frames) + 1, progress_total, "Encoding target-field GIF...")
    export_qimages_to_gif(
        composed_frames,
        output_path,
        frame_duration_ms=duration_ms,
        loop_count=0,
    )
    _emit_progress(progress_callback, progress_total, progress_total, f"Saved target-field GIF to {output_path.name}.")


def _as_grayscale(image: np.ndarray) -> np.ndarray:
    data = np.asarray(image, dtype=float)
    if data.ndim == 2:
        return data
    if data.ndim == 3 and data.shape[-1] in {1, 3, 4}:
        return np.asarray(np.mean(data[..., :3], axis=-1), dtype=float)
    if data.ndim == 3 and data.shape[0] in {1, 3, 4}:
        return np.asarray(np.mean(data[:3], axis=0), dtype=float)
    raise TargetFieldAnimationError("Image is not a usable grayscale or RGB frame.")


def _light_curve_points_by_file(
    report: ProcessingReport,
    source_id: str,
    filter_name: str | None,
) -> dict[str, LightCurvePoint]:
    mapping: dict[str, LightCurvePoint] = {}
    for series in report.light_curves:
        if str(series.source_id) != source_id:
            continue
        if filter_name is not None and (series.filter_name or "unknown") != filter_name:
            continue
        for point in series.points:
            mapping[_file_key(point.file_path)] = point
    return mapping


def _series_for_source(
    report: ProcessingReport,
    source_id: str,
    filter_name: str | None,
) -> LightCurveSeries | None:
    matches = [
        series
        for series in report.light_curves
        if str(series.source_id) == source_id
        and (filter_name is None or (series.filter_name or "unknown") == filter_name)
    ]
    return matches[0] if matches else None


def _highlight_for_frame(payload: LightCurvePlotPayload, frame: TargetFieldFrame) -> tuple[float, float] | None:
    if frame.point is None:
        return None
    for render_point in payload.points:
        if _file_key(render_point.source_point.file_path) == _file_key(frame.point.file_path):
            return float(render_point.x), float(render_point.y)
    return None


def _render_light_curve_payload_with_highlight(
    payload: LightCurvePlotPayload,
    *,
    highlight_x: float | None,
    highlight_y: float | None,
    plot_theme: str,
    custom_theme_colors: dict[str, str] | None,
    figure_size_inches: tuple[float, float] = (8.4, 4.8),
    dpi: int = 120,
) -> QImage:
    figure = Figure(figsize=figure_size_inches, dpi=dpi)
    axis = figure.add_subplot(111)
    try:
        plot_light_curve_payload(
            axis,
            payload,
            theme=plot_theme,
            custom_theme_colors=custom_theme_colors,
            export_style="themed",
            show_empty_message=False,
        )
        if highlight_x is not None and math.isfinite(highlight_x):
            axis.axvline(highlight_x, color="#f4d35e", alpha=0.45, linewidth=1.4, zorder=8)
        if highlight_x is not None and highlight_y is not None and math.isfinite(highlight_x) and math.isfinite(highlight_y):
            axis.scatter(
                [highlight_x],
                [highlight_y],
                s=90,
                facecolors="none",
                edgecolors="#f4d35e",
                linewidths=2.0,
                zorder=9,
            )
        figure.tight_layout()
        buffer = BytesIO()
        figure.savefig(buffer, format="png", dpi=dpi, facecolor=figure.get_facecolor())
        image = QImage()
        if not image.loadFromData(buffer.getvalue(), "PNG"):
            raise OSError("Unable to render the target-field light-curve frame.")
        return image
    finally:
        figure.clear()


def _file_key(path: Path) -> str:
    return str(Path(path))


def _observation_sort_key(value: datetime | None) -> datetime:
    if value is None:
        return datetime.min
    return value.replace(tzinfo=None) if value.tzinfo is not None else value


def _emit_progress(
    progress_callback: Callable[[int, int, str], None] | None,
    completed: int,
    total: int,
    message: str,
) -> None:
    if progress_callback is not None:
        progress_callback(completed, total, message)


def _raise_if_canceled(is_cancelled: Callable[[], bool] | None) -> None:
    if is_cancelled is not None and is_cancelled():
        raise AnimatedLightCurveExportCanceled("Target-field animation export canceled.")


def _filled_stamp(stamp: np.ndarray) -> np.ndarray:
    values = np.asarray(stamp, dtype=float)
    filled = values.copy()
    background = estimate_stamp_background(values)
    fill = background if math.isfinite(background) else 0.0
    filled[~np.isfinite(filled)] = fill
    return filled


def _alignment_plane(stamp: np.ndarray) -> np.ndarray:
    plane = _filled_stamp(stamp)
    try:
        from scipy import ndimage
    except ImportError:
        ndimage = None
    if ndimage is not None and min(plane.shape) >= 16:
        plane = plane - ndimage.gaussian_filter(plane, sigma=2.5, mode="nearest")
    plane = plane - float(np.mean(plane))
    window = np.outer(np.hanning(plane.shape[0]), np.hanning(plane.shape[1]))
    return np.asarray(plane * window, dtype=float)


def _phase_correlate_shift(
    reference_plane: np.ndarray,
    stamp_plane: np.ndarray,
    *,
    max_shift: float,
) -> tuple[float, float, float]:
    if reference_plane.shape != stamp_plane.shape or min(reference_plane.shape) < 8:
        return 0.0, 0.0, 0.0
    cross_power = np.fft.rfft2(reference_plane) * np.conj(np.fft.rfft2(stamp_plane))
    magnitude = np.abs(cross_power)
    cross_power = np.divide(cross_power, np.maximum(magnitude, 1.0e-12))
    correlation = np.fft.irfft2(cross_power, s=reference_plane.shape)
    peak_y, peak_x = np.unravel_index(int(np.argmax(correlation)), correlation.shape)
    height, width = correlation.shape
    shift_y = float(peak_y if peak_y <= height // 2 else peak_y - height)
    shift_x = float(peak_x if peak_x <= width // 2 else peak_x - width)
    shift_y += _quadratic_peak_offset(
        float(correlation[(peak_y - 1) % height, peak_x]),
        float(correlation[peak_y, peak_x]),
        float(correlation[(peak_y + 1) % height, peak_x]),
    )
    shift_x += _quadratic_peak_offset(
        float(correlation[peak_y, (peak_x - 1) % width]),
        float(correlation[peak_y, peak_x]),
        float(correlation[peak_y, (peak_x + 1) % width]),
    )
    score = float(correlation[peak_y, peak_x])
    if abs(shift_y) > max_shift or abs(shift_x) > max_shift:
        return 0.0, 0.0, 0.0
    return shift_y, shift_x, score


def _quadratic_peak_offset(previous: float, center: float, following: float) -> float:
    denominator = previous - (2.0 * center) + following
    if abs(denominator) < 1e-12:
        return 0.0
    return float(np.clip(0.5 * (previous - following) / denominator, -0.6, 0.6))


def _robust_shift_solution(
    shifts: Sequence[tuple[float, float]],
    *,
    orientation: str,
) -> tuple[StampAlignmentSolution, float, int]:
    if not shifts:
        return StampAlignmentSolution(orientation=orientation, score=0.0), float("inf"), 0
    values = np.asarray(shifts, dtype=float)
    median_shift = np.median(values, axis=0)
    distances = np.hypot(values[:, 0] - median_shift[0], values[:, 1] - median_shift[1])
    scatter = float(np.median(distances))
    inlier_limit = max(2.0, scatter * 2.5 if math.isfinite(scatter) else 2.0)
    inliers = values[distances <= inlier_limit] if np.any(distances <= inlier_limit) else values
    final_shift = np.median(inliers, axis=0)
    final_scatter = float(np.median(np.hypot(inliers[:, 0] - final_shift[0], inliers[:, 1] - final_shift[1])))
    if not math.isfinite(final_scatter):
        final_scatter = float("inf")
    inlier_count = int(inliers.shape[0])
    score = float(inlier_count) / max(1.0, 1.0 + (final_scatter if math.isfinite(final_scatter) else 100.0))
    return (
        StampAlignmentSolution(
            orientation=orientation,
            shift_y=float(final_shift[0]),
            shift_x=float(final_shift[1]),
            score=score,
        ),
        final_scatter,
        inlier_count,
    )


def _oriented_source_xy(
    x: float,
    y: float,
    orientation: str,
    width: int,
    height: int,
) -> tuple[float, float]:
    if orientation == "rot180":
        return (width - 1) - x, (height - 1) - y
    if orientation == "flip_lr":
        return (width - 1) - x, y
    if orientation == "flip_ud":
        return x, (height - 1) - y
    return x, y


def _orientation_is_clear_win(
    challenger_scatter: float,
    challenger_count: int,
    incumbent_scatter: float,
    incumbent_count: int,
) -> bool:
    if challenger_count < 3 or not math.isfinite(challenger_scatter):
        return False
    if not math.isfinite(incumbent_scatter):
        return True
    if challenger_count > incumbent_count and challenger_scatter < 3.0:
        return True
    retained_match_fraction = challenger_count / max(1, incumbent_count)
    return retained_match_fraction >= 0.6 and incumbent_scatter > max(
        12.0,
        challenger_scatter * 4.0,
    )


def _choose_orientation_from_candidates(
    candidates: dict[str, tuple[StampAlignmentSolution, float, int]],
    *,
    previous_orientation: str,
    match_count: int,
) -> StampAlignmentSolution:
    identity_solution, identity_scatter, identity_count = candidates.get(
        "identity",
        (StampAlignmentSolution(), float("inf"), 0),
    )
    if match_count < 2:
        return identity_solution
    previous = previous_orientation if previous_orientation in candidates else "identity"
    previous_solution, previous_scatter, previous_count = candidates[previous]
    best_orientation = "identity"
    best_solution = identity_solution
    best_scatter = identity_scatter
    best_count = identity_count
    for orientation, (solution, scatter, count) in candidates.items():
        if count < 2 or not math.isfinite(scatter):
            continue
        better = scatter < (best_scatter - 0.5) or (abs(scatter - best_scatter) <= 0.5 and count > best_count)
        if better:
            best_orientation = orientation
            best_solution = solution
            best_scatter = scatter
            best_count = count
    if previous != "identity" and previous_count >= 2:
        if best_orientation != previous and _orientation_is_clear_win(
            best_scatter,
            best_count,
            previous_scatter,
            previous_count,
        ):
            return best_solution
        return previous_solution
    if best_orientation != "identity" and _orientation_is_clear_win(
        best_scatter,
        best_count,
        identity_scatter,
        identity_count,
    ):
        return best_solution
    return identity_solution


def _estimate_alignment_from_detected_stars(
    reference: np.ndarray,
    source: np.ndarray,
    *,
    previous_orientation: str,
) -> StampAlignmentSolution | None:
    reference_stars = _extract_alignment_stars(_alignment_detection_plane(reference))
    source_stars = _extract_alignment_stars(_alignment_detection_plane(source))
    if len(reference_stars) < 2 or len(source_stars) < 2:
        return None
    height, width = np.asarray(source).shape[:2]
    reference_xy = [(star.column, star.row) for star in reference_stars]
    candidates: dict[str, tuple[StampAlignmentSolution, float, int]] = {}
    match_count = 0
    for orientation in TARGET_FIELD_ALIGN_ORIENTATIONS:
        oriented_xy = [
            _oriented_source_xy(star.column, star.row, orientation, width, height)
            for star in source_stars
        ]
        shifts = _consensus_star_shifts(reference_xy, oriented_xy)
        match_count = max(match_count, len(shifts))
        candidates[orientation] = _robust_shift_solution(shifts, orientation=orientation)
    if match_count == 0:
        return None
    return _choose_orientation_from_candidates(
        candidates,
        previous_orientation=previous_orientation,
        match_count=match_count,
    )


def _consensus_star_shifts(
    reference_xy: Sequence[tuple[float, float]],
    source_xy: Sequence[tuple[float, float]],
    *,
    max_shift: float = 96.0,
) -> list[tuple[float, float]]:
    bins: dict[tuple[int, int], list[tuple[float, float]]] = {}
    for ref_x, ref_y in reference_xy:
        for src_x, src_y in source_xy:
            shift_y = ref_y - src_y
            shift_x = ref_x - src_x
            if abs(shift_y) > max_shift or abs(shift_x) > max_shift:
                continue
            key = (int(round(shift_y)), int(round(shift_x)))
            bins.setdefault(key, []).append((shift_y, shift_x))
    if not bins:
        return []
    return max(bins.values(), key=len)


def _translation_only_full_frame_alignment(reference: np.ndarray, source: np.ndarray) -> StampAlignmentSolution:
    reference_small, scale = _downsample_for_alignment(reference)
    source_small, _source_scale = _downsample_for_alignment(source, target_shape=reference_small.shape)
    max_shift = max(8.0, min(reference_small.shape) * _FULL_FRAME_ALIGN_SHIFT_FRACTION)
    shift_y, shift_x, score = _phase_correlate_shift(
        _alignment_plane(reference_small),
        _alignment_plane(source_small),
        max_shift=max_shift,
    )
    return StampAlignmentSolution(
        orientation="identity",
        shift_y=shift_y * scale,
        shift_x=shift_x * scale,
        score=score,
    )


def _downsample_for_alignment(
    image: np.ndarray,
    *,
    max_edge: int = _FULL_FRAME_ALIGN_MAX_EDGE,
    target_shape: tuple[int, int] | None = None,
) -> tuple[np.ndarray, float]:
    plane = np.asarray(_as_grayscale(image), dtype=np.float32)
    height, width = plane.shape
    if target_shape is not None and target_shape == plane.shape:
        return plane, 1.0
    if target_shape is not None:
        scale_y = height / max(1, int(target_shape[0]))
        scale_x = width / max(1, int(target_shape[1]))
        scale = float((scale_y + scale_x) * 0.5)
        try:
            from scipy import ndimage
        except ImportError:
            ndimage = None
        if ndimage is not None:
            zoomed = ndimage.zoom(
                plane,
                (int(target_shape[0]) / height, int(target_shape[1]) / width),
                order=1,
                prefilter=False,
            )
            return np.asarray(zoomed, dtype=np.float32), max(1.0, scale)
        step = max(1, int(round(scale)))
        return plane[::step, ::step], float(step)
    scale = max(1.0, max(height, width) / float(max(8, int(max_edge))))
    if scale <= 1.01:
        return plane, 1.0
    step = max(1, int(round(scale)))
    return plane[::step, ::step], float(step)


def _shared_stretch_statistics(normalized_stamps: Sequence[np.ndarray]) -> np.ndarray:
    samples: list[np.ndarray] = []
    for stamp in normalized_stamps:
        values = np.asarray(stamp, dtype=float).reshape(-1)
        if values.size == 0:
            continue
        stride = max(1, values.size // 20000)
        samples.append(values[::stride])
    if not samples:
        return np.asarray([0.0, 1.0], dtype=float)
    return np.concatenate(samples)


def _max_align_shift(shape: tuple[int, ...]) -> float:
    if not shape:
        return _ALIGN_MIN_SHIFT_PX
    return max(_ALIGN_MIN_SHIFT_PX, min(_ALIGN_MAX_SHIFT_PX, min(int(shape[0]), int(shape[1])) * _ALIGN_MAX_SHIFT_FRACTION))


def _stamp_alignment_candidates(
    reference: np.ndarray,
    stamp: np.ndarray,
    *,
    max_shift: float,
) -> dict[str, StampAlignmentSolution]:
    reference_plane = _alignment_plane(reference)
    options: dict[str, StampAlignmentSolution] = {}
    if reference_plane.shape != np.asarray(stamp).shape[:2] or min(reference_plane.shape) < 8:
        return {"identity": StampAlignmentSolution(), "rot180": StampAlignmentSolution(orientation="rot180")}
    for orientation in ("identity", "rot180"):
        oriented = orient_target_stamp(stamp, orientation)
        shift_y, shift_x, score = _phase_correlate_shift(reference_plane, _alignment_plane(oriented), max_shift=max_shift)
        options[orientation] = StampAlignmentSolution(
            orientation=orientation,
            shift_y=shift_y,
            shift_x=shift_x,
            score=score,
        )
    return options


def _choose_alignment_orientation(
    options: dict[str, StampAlignmentSolution],
    *,
    previous: str = "identity",
) -> str:
    identity = options.get("identity", StampAlignmentSolution())
    flipped = options.get("rot180", StampAlignmentSolution(orientation="rot180"))
    preferred = previous if previous in {"identity", "rot180"} else "identity"
    if preferred == "rot180":
        if identity.score >= (flipped.score * _ALIGN_ORIENTATION_IMPROVEMENT) and identity.score > 0.0:
            return "identity"
        return "rot180" if flipped.score > 0.0 else "identity"
    if flipped.score >= (identity.score * _ALIGN_ORIENTATION_IMPROVEMENT) and flipped.score > 0.0:
        return "rot180"
    return "identity"


def _stabilize_orientations(orientations: Sequence[str], *, min_run: int = _ALIGN_MIN_ORIENTATION_RUN) -> list[str]:
    resolved = [str(item or "identity") for item in orientations]
    if len(resolved) <= 1:
        return resolved
    index = 0
    while index < len(resolved):
        end = index + 1
        while end < len(resolved) and resolved[end] == resolved[index]:
            end += 1
        if index > 0 and (end - index) < min_run:
            fill = resolved[index - 1]
            resolved[index:end] = [fill] * (end - index)
        index = end
    return resolved


def _integer_shift_stamp(stamp: np.ndarray, shift_y: int, shift_x: int) -> np.ndarray:
    values = np.asarray(stamp, dtype=float)
    result = np.full_like(values, np.nan, dtype=float)
    if values.ndim != 2:
        return result
    height, width = values.shape
    src_y0 = max(0, -shift_y)
    src_x0 = max(0, -shift_x)
    dst_y0 = max(0, shift_y)
    dst_x0 = max(0, shift_x)
    copy_h = min(height - src_y0, height - dst_y0)
    copy_w = min(width - src_x0, width - dst_x0)
    if copy_h <= 0 or copy_w <= 0:
        return result
    result[dst_y0 : dst_y0 + copy_h, dst_x0 : dst_x0 + copy_w] = values[src_y0 : src_y0 + copy_h, src_x0 : src_x0 + copy_w]
    return result
