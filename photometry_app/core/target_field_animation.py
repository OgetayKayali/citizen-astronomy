from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
import hashlib
import math
import os
from pathlib import Path

from matplotlib.figure import Figure
import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen

from photometry_app.core.alignment import (
    _alignment_detection_plane,
    _extract_alignment_stars,
    _fast_affine_pixel_transform,
)
from photometry_app.core.animation_export import export_qimages_to_gif, export_qimages_to_mp4
from photometry_app.core.exporters import AnimatedLightCurveExportCanceled
from photometry_app.core.image_io import read_header_and_shape, read_photometry_image_data
from photometry_app.core.models import LightCurvePoint, LightCurveSeries, PhotometryMeasurement, ProcessingReport
from photometry_app.core.plotting import (
    LightCurvePlotPayload,
    _stretched_image_data,
    build_light_curve_plot_payload,
    plot_light_curve_payload,
)
from photometry_app.core.target_markers import (
    DEFAULT_TARGET_FIELD_MARKER_STYLE,
    TARGET_FIELD_MARKER_NONE,
    TargetMarkerAppearance,
    coerce_target_field_marker_style,
    pointer_marker_segments,
)


DEFAULT_TARGET_FIELD_FOV_PX = 250
MIN_TARGET_FIELD_FOV_PX = 32
MAX_TARGET_FIELD_FOV_PX = 2000
DEFAULT_TARGET_FIELD_FPS = 12.0
MIN_TARGET_FIELD_FPS = 1.0
MAX_TARGET_FIELD_FPS = 30.0
DEFAULT_TARGET_FIELD_DURATION_SECONDS = 8.0
MIN_TARGET_FIELD_DURATION_SECONDS = 0.5
MAX_TARGET_FIELD_DURATION_SECONDS = 120.0
DEFAULT_TARGET_FIELD_LOOP_COUNT = 1
MIN_TARGET_FIELD_LOOP_COUNT = 1
MAX_TARGET_FIELD_LOOP_COUNT = 20
DEFAULT_TARGET_FIELD_SCALE_PERCENT = 100
MIN_TARGET_FIELD_SCALE_PERCENT = 10
MAX_TARGET_FIELD_SCALE_PERCENT = 200
DEFAULT_TARGET_FIELD_MARKER_LENGTH_PERCENT = 36
MIN_TARGET_FIELD_MARKER_LENGTH_PERCENT = 10
MAX_TARGET_FIELD_MARKER_LENGTH_PERCENT = 90
DEFAULT_TARGET_FIELD_MARKER_LINE_WIDTH = 2.0
MIN_TARGET_FIELD_MARKER_LINE_WIDTH = 0.5
MAX_TARGET_FIELD_MARKER_LINE_WIDTH = 12.0
DEFAULT_TARGET_FIELD_MARKER_LINE_COLOR = "#ef4444"
DEFAULT_TARGET_FIELD_MARKER_GAP_PERCENT = 20
MIN_TARGET_FIELD_MARKER_GAP_PERCENT = 2
MAX_TARGET_FIELD_MARKER_GAP_PERCENT = 80
MIN_TARGET_FIELD_STAR_HEIGHT_PERCENT = 20.0
MAX_TARGET_FIELD_STAR_HEIGHT_PERCENT = 80.0
DEFAULT_TARGET_FIELD_STAR_HEIGHT_PERCENT = 100.0 * 5.0 / 9.0
DEFAULT_TARGET_FIELD_ALIGN = True
TARGET_FIELD_ALIGN_CROP_THEN_ALIGN = "crop_then_align"
TARGET_FIELD_ALIGN_ALIGN_THEN_CROP = "align_then_crop"
TARGET_FIELD_ALIGN_NONE = "none"
DEFAULT_TARGET_FIELD_ALIGN_MODE = TARGET_FIELD_ALIGN_CROP_THEN_ALIGN
TARGET_FIELD_ALIGN_MODES = (
    TARGET_FIELD_ALIGN_CROP_THEN_ALIGN,
    TARGET_FIELD_ALIGN_ALIGN_THEN_CROP,
    TARGET_FIELD_ALIGN_NONE,
)
TARGET_FIELD_ALIGN_MODE_LABELS = {
    TARGET_FIELD_ALIGN_CROP_THEN_ALIGN: "Crop, then align",
    TARGET_FIELD_ALIGN_ALIGN_THEN_CROP: "Align, then crop (slower)",
    TARGET_FIELD_ALIGN_NONE: "Crop only",
}
TARGET_FIELD_EXPORT_FORMATS = ("gif", "mp4")
TARGET_FIELD_EXPORT_FORMAT_LABELS = {
    "gif": "GIF",
    "mp4": "MP4",
}
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
_TARGET_FIELD_AUTO_MAX_WORKERS = 8
_WORKING_IMAGE_DTYPE = np.float32
_FULL_FRAME_WORKER_BUDGET_BYTES = 512 * 1024 * 1024
_SMALL_FRAME_WORKER_BYTES = 8 * 1024 * 1024
TARGET_FIELD_OUTPUT_ASPECT_RATIO = 16.0 / 9.0
TARGET_FIELD_PLOT_SEPARATOR_PX = 5
TARGET_FIELD_STAR_ASPECT_RATIO = 16.0 / 5.0
TARGET_FIELD_PLOT_ASPECT_RATIO = 16.0 / 4.0
TARGET_FIELD_ASPECT_REMAINING = "remaining"
TARGET_FIELD_ASPECT_RATIOS = {
    "16:9": 16.0 / 9.0,
    "16:5": 16.0 / 5.0,
    "16:4": 16.0 / 4.0,
    "16:3": 16.0 / 3.0,
    "2:1": 2.0,
    "1:1": 1.0,
}
TARGET_FIELD_STAR_ASPECTS = ("16:5", "16:9", "16:4", "1:1", TARGET_FIELD_ASPECT_REMAINING)
TARGET_FIELD_PLOT_ASPECTS = ("16:4", "16:3", "16:5", "16:9", "2:1")
TARGET_FIELD_ASPECT_LABELS = {
    "16:9": "16:9",
    "16:5": "16:5",
    "16:4": "16:4",
    "16:3": "16:3",
    "2:1": "2:1",
    "1:1": "1:1",
    TARGET_FIELD_ASPECT_REMAINING: "Remaining",
}
TARGET_FIELD_FIT_STRETCH = "stretch"
TARGET_FIELD_FIT_FIT = "fit"
TARGET_FIELD_FIT_FILL = "fill"
TARGET_FIELD_FIT_MODES = (TARGET_FIELD_FIT_STRETCH, TARGET_FIELD_FIT_FIT, TARGET_FIELD_FIT_FILL)
TARGET_FIELD_FIT_MODE_LABELS = {
    TARGET_FIELD_FIT_STRETCH: "Stretch to fit",
    TARGET_FIELD_FIT_FIT: "Fit (letterbox)",
    TARGET_FIELD_FIT_FILL: "Fill (crop)",
}
DEFAULT_TARGET_FIELD_STAR_ASPECT = "16:5"
DEFAULT_TARGET_FIELD_PLOT_ASPECT = "16:4"
DEFAULT_TARGET_FIELD_STAR_FIT = TARGET_FIELD_FIT_FILL
DEFAULT_TARGET_FIELD_PLOT_FIT = TARGET_FIELD_FIT_STRETCH
DEFAULT_TARGET_FIELD_VIDEO_WIDTH = 1920
DEFAULT_TARGET_FIELD_VIDEO_HEIGHT = 1080
MIN_TARGET_FIELD_VIDEO_WIDTH = 640
MAX_TARGET_FIELD_VIDEO_WIDTH = 3840
MIN_TARGET_FIELD_VIDEO_HEIGHT = 360
MAX_TARGET_FIELD_VIDEO_HEIGHT = 2160
TARGET_FIELD_VIDEO_RESOLUTIONS = (
    (1280, 720),
    (1920, 1080),
    (2560, 1440),
    (3840, 2160),
)
_TARGET_FIELD_MIN_FRAME_WIDTH = 640
_TARGET_FIELD_MAX_FRAME_WIDTH = 3840


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
    align_mode: str = DEFAULT_TARGET_FIELD_ALIGN_MODE
    duration_seconds: float = DEFAULT_TARGET_FIELD_DURATION_SECONDS
    loop_count: int = DEFAULT_TARGET_FIELD_LOOP_COUNT
    scale_percent: int = DEFAULT_TARGET_FIELD_SCALE_PERCENT
    stretch_mode: str = DEFAULT_TARGET_FIELD_STRETCH_MODE
    export_format: str = "gif"
    marker_style: str = DEFAULT_TARGET_FIELD_MARKER_STYLE
    marker_length_percent: int = DEFAULT_TARGET_FIELD_MARKER_LENGTH_PERCENT
    marker_gap_percent: int = DEFAULT_TARGET_FIELD_MARKER_GAP_PERCENT
    marker_line_width: float = DEFAULT_TARGET_FIELD_MARKER_LINE_WIDTH
    marker_line_color: str = DEFAULT_TARGET_FIELD_MARKER_LINE_COLOR
    video_width: int = DEFAULT_TARGET_FIELD_VIDEO_WIDTH
    video_height: int = DEFAULT_TARGET_FIELD_VIDEO_HEIGHT
    star_aspect: str = DEFAULT_TARGET_FIELD_STAR_ASPECT
    plot_aspect: str = DEFAULT_TARGET_FIELD_PLOT_ASPECT
    star_fit: str = DEFAULT_TARGET_FIELD_STAR_FIT
    plot_fit: str = DEFAULT_TARGET_FIELD_PLOT_FIT
    save_star_separately: bool = False
    save_plot_separately: bool = False

    def normalized(self) -> TargetFieldAnimationExportOptions:
        return TargetFieldAnimationExportOptions(
            fov_px=normalize_target_field_fov_px(self.fov_px),
            align_mode=normalize_target_field_align_mode(self.align_mode),
            duration_seconds=normalize_target_field_duration_seconds(self.duration_seconds),
            loop_count=normalize_target_field_loop_count(self.loop_count),
            scale_percent=normalize_target_field_scale_percent(self.scale_percent),
            stretch_mode=normalize_target_field_stretch_mode(self.stretch_mode),
            export_format=normalize_target_field_export_format(self.export_format),
            marker_style=normalize_target_field_marker_style(self.marker_style),
            marker_length_percent=normalize_target_field_marker_length_percent(self.marker_length_percent),
            marker_gap_percent=normalize_target_field_marker_gap_percent(self.marker_gap_percent),
            marker_line_width=normalize_target_field_marker_line_width(self.marker_line_width),
            marker_line_color=normalize_target_field_marker_line_color(self.marker_line_color),
            video_width=normalize_target_field_video_width(self.video_width),
            video_height=normalize_target_field_video_height(self.video_height),
            star_aspect=normalize_target_field_star_aspect(self.star_aspect),
            plot_aspect=normalize_target_field_plot_aspect(self.plot_aspect),
            star_fit=normalize_target_field_fit_mode(self.star_fit, default=DEFAULT_TARGET_FIELD_STAR_FIT),
            plot_fit=normalize_target_field_fit_mode(self.plot_fit, default=DEFAULT_TARGET_FIELD_PLOT_FIT),
            save_star_separately=bool(self.save_star_separately),
            save_plot_separately=bool(self.save_plot_separately),
        )

    def marker_appearance(self) -> TargetMarkerAppearance:
        options = self.normalized()
        return TargetMarkerAppearance(
            line_color=options.marker_line_color,
            outline_color="",
            line_width=options.marker_line_width,
            length_percent=float(options.marker_length_percent),
            gap_percent=float(options.marker_gap_percent),
        )

    @property
    def align(self) -> bool:
        return self.align_mode != TARGET_FIELD_ALIGN_NONE


@dataclass(frozen=True, slots=True)
class TargetFieldPlotScanLayout:
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    left: float
    top: float
    right: float
    bottom: float
    invert_y: bool = True
    color: str = "#f4d35e"


TARGET_FIELD_PROGRESS_PREPARE = "prepare"
TARGET_FIELD_PROGRESS_NORMALIZE = "normalize"
TARGET_FIELD_PROGRESS_COMPOSE = "compose"
TARGET_FIELD_PROGRESS_ENCODE = "encode"
TARGET_FIELD_PROGRESS_STAGES = (
    TARGET_FIELD_PROGRESS_PREPARE,
    TARGET_FIELD_PROGRESS_NORMALIZE,
    TARGET_FIELD_PROGRESS_COMPOSE,
    TARGET_FIELD_PROGRESS_ENCODE,
)


@dataclass(frozen=True, slots=True)
class TargetFieldAnimationProgress:
    stage: str
    completed: int
    total: int
    message: str
    done: bool = False


def target_field_progress_stage_title(
    stage: str,
    *,
    align_mode: str = DEFAULT_TARGET_FIELD_ALIGN_MODE,
    export_format: str = "gif",
) -> str:
    resolved_stage = str(stage or "").strip().lower()
    resolved_mode = normalize_target_field_align_mode(align_mode)
    resolved_format = normalize_target_field_export_format(export_format)
    if resolved_stage == TARGET_FIELD_PROGRESS_PREPARE:
        if resolved_mode == TARGET_FIELD_ALIGN_ALIGN_THEN_CROP:
            return "Align, then crop"
        if resolved_mode == TARGET_FIELD_ALIGN_NONE:
            return "Crop frames"
        return "Crop, then align"
    if resolved_stage == TARGET_FIELD_PROGRESS_NORMALIZE:
        return "Normalize & stretch"
    if resolved_stage == TARGET_FIELD_PROGRESS_COMPOSE:
        return "Compose frames"
    if resolved_stage == TARGET_FIELD_PROGRESS_ENCODE:
        return f"Encode {resolved_format.upper()}"
    return resolved_stage or "Pipeline"


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


def normalize_target_field_duration_seconds(
    value: object,
    default: float = DEFAULT_TARGET_FIELD_DURATION_SECONDS,
) -> float:
    try:
        duration = float(value)
    except (TypeError, ValueError):
        duration = float(default)
    if not math.isfinite(duration):
        duration = float(default)
    return min(MAX_TARGET_FIELD_DURATION_SECONDS, max(MIN_TARGET_FIELD_DURATION_SECONDS, duration))


def normalize_target_field_loop_count(
    value: object,
    default: int = DEFAULT_TARGET_FIELD_LOOP_COUNT,
) -> int:
    try:
        count = int(round(float(value)))
    except (TypeError, ValueError):
        count = int(default)
    return min(MAX_TARGET_FIELD_LOOP_COUNT, max(MIN_TARGET_FIELD_LOOP_COUNT, count))


def normalize_target_field_scale_percent(
    value: object,
    default: int = DEFAULT_TARGET_FIELD_SCALE_PERCENT,
) -> int:
    try:
        percent = int(value)
    except (TypeError, ValueError):
        percent = int(default)
    return min(MAX_TARGET_FIELD_SCALE_PERCENT, max(MIN_TARGET_FIELD_SCALE_PERCENT, percent))


def normalize_target_field_video_width(
    value: object,
    default: int = DEFAULT_TARGET_FIELD_VIDEO_WIDTH,
) -> int:
    try:
        width = int(round(float(value)))
    except (TypeError, ValueError):
        width = int(default)
    return _even_dimension(
        min(MAX_TARGET_FIELD_VIDEO_WIDTH, max(MIN_TARGET_FIELD_VIDEO_WIDTH, width)),
        minimum=MIN_TARGET_FIELD_VIDEO_WIDTH,
    )


def normalize_target_field_video_height(
    value: object,
    default: int = DEFAULT_TARGET_FIELD_VIDEO_HEIGHT,
) -> int:
    try:
        height = int(round(float(value)))
    except (TypeError, ValueError):
        height = int(default)
    return _even_dimension(
        min(MAX_TARGET_FIELD_VIDEO_HEIGHT, max(MIN_TARGET_FIELD_VIDEO_HEIGHT, height)),
        minimum=MIN_TARGET_FIELD_VIDEO_HEIGHT,
    )


def normalize_target_field_star_aspect(
    value: object,
    default: str = DEFAULT_TARGET_FIELD_STAR_ASPECT,
) -> str:
    key = str(value or default).strip().lower().replace(" ", "")
    if key in {"remaining", "rest", "fill-remaining", "fillremaining"}:
        return TARGET_FIELD_ASPECT_REMAINING
    canonical = _canonical_aspect_key(key)
    if canonical is not None:
        return canonical
    fallback = _canonical_aspect_key(str(default).strip().lower().replace(" ", ""))
    return fallback or DEFAULT_TARGET_FIELD_STAR_ASPECT


def normalize_target_field_plot_aspect(
    value: object,
    default: str = DEFAULT_TARGET_FIELD_PLOT_ASPECT,
) -> str:
    key = str(value or default).strip().lower().replace(" ", "")
    canonical = _canonical_aspect_key(key)
    if canonical is not None and canonical != TARGET_FIELD_ASPECT_REMAINING:
        return canonical
    fallback = _canonical_aspect_key(str(default).strip().lower().replace(" ", ""))
    if fallback is not None and fallback != TARGET_FIELD_ASPECT_REMAINING:
        return fallback
    return DEFAULT_TARGET_FIELD_PLOT_ASPECT


def normalize_target_field_fit_mode(
    value: object,
    default: str = TARGET_FIELD_FIT_STRETCH,
) -> str:
    mode = str(value or default).strip().lower()
    aliases = {
        "stretch": TARGET_FIELD_FIT_STRETCH,
        "stretch-to-fit": TARGET_FIELD_FIT_STRETCH,
        "ignore": TARGET_FIELD_FIT_STRETCH,
        "fit": TARGET_FIELD_FIT_FIT,
        "letterbox": TARGET_FIELD_FIT_FIT,
        "contain": TARGET_FIELD_FIT_FIT,
        "fill": TARGET_FIELD_FIT_FILL,
        "crop": TARGET_FIELD_FIT_FILL,
        "cover": TARGET_FIELD_FIT_FILL,
    }
    resolved = aliases.get(mode, mode)
    if resolved in TARGET_FIELD_FIT_MODES:
        return resolved
    return default if default in TARGET_FIELD_FIT_MODES else TARGET_FIELD_FIT_STRETCH


def normalize_target_field_align_mode(
    value: object,
    default: str = DEFAULT_TARGET_FIELD_ALIGN_MODE,
) -> str:
    if isinstance(value, bool):
        return TARGET_FIELD_ALIGN_CROP_THEN_ALIGN if value else TARGET_FIELD_ALIGN_NONE
    mode = str(value if value is not None else default).strip().lower()
    if mode in {"true", "1", "yes", "on"}:
        return TARGET_FIELD_ALIGN_CROP_THEN_ALIGN
    if mode in {"false", "0", "no", "off"}:
        return TARGET_FIELD_ALIGN_NONE
    if mode in TARGET_FIELD_ALIGN_MODES:
        return mode
    return default if default in TARGET_FIELD_ALIGN_MODES else DEFAULT_TARGET_FIELD_ALIGN_MODE


def normalize_target_field_export_format(value: object, default: str = "gif") -> str:
    fmt = str(value or default).strip().lower().lstrip(".")
    if fmt in TARGET_FIELD_EXPORT_FORMATS:
        return fmt
    return default if default in TARGET_FIELD_EXPORT_FORMATS else "gif"


def normalize_target_field_marker_style(
    value: object,
    default: str = DEFAULT_TARGET_FIELD_MARKER_STYLE,
) -> str:
    if value is None or str(value).strip() == "":
        return coerce_target_field_marker_style(default)
    return coerce_target_field_marker_style(value)


def normalize_target_field_marker_length_percent(
    value: object,
    default: int = DEFAULT_TARGET_FIELD_MARKER_LENGTH_PERCENT,
) -> int:
    try:
        length = int(round(float(value)))
    except (TypeError, ValueError):
        length = int(default)
    return min(MAX_TARGET_FIELD_MARKER_LENGTH_PERCENT, max(MIN_TARGET_FIELD_MARKER_LENGTH_PERCENT, length))


def normalize_target_field_marker_gap_percent(
    value: object,
    default: int = DEFAULT_TARGET_FIELD_MARKER_GAP_PERCENT,
) -> int:
    try:
        gap = int(round(float(value)))
    except (TypeError, ValueError):
        gap = int(default)
    return min(MAX_TARGET_FIELD_MARKER_GAP_PERCENT, max(MIN_TARGET_FIELD_MARKER_GAP_PERCENT, gap))


def normalize_target_field_marker_line_width(
    value: object,
    default: float = DEFAULT_TARGET_FIELD_MARKER_LINE_WIDTH,
) -> float:
    try:
        width = float(value)
    except (TypeError, ValueError):
        width = float(default)
    if not math.isfinite(width):
        width = float(default)
    return min(MAX_TARGET_FIELD_MARKER_LINE_WIDTH, max(MIN_TARGET_FIELD_MARKER_LINE_WIDTH, width))


def normalize_target_field_marker_line_color(
    value: object,
    default: str = DEFAULT_TARGET_FIELD_MARKER_LINE_COLOR,
) -> str:
    color = QColor(str(value or "").strip() or default)
    if not color.isValid():
        color = QColor(default)
    if not color.isValid():
        return DEFAULT_TARGET_FIELD_MARKER_LINE_COLOR
    return color.name(QColor.NameFormat.HexRgb).lower()


def target_field_frame_duration_ms(fps: object, default: float = DEFAULT_TARGET_FIELD_FPS) -> int:
    resolved_fps = normalize_target_field_fps(fps, default=default)
    return max(20, int(round(1000.0 / resolved_fps)))


def target_field_duration_frame_ms(
    duration_seconds: object,
    frame_count: int,
    *,
    gif: bool = False,
    default: float = DEFAULT_TARGET_FIELD_DURATION_SECONDS,
) -> int:
    resolved_duration = normalize_target_field_duration_seconds(duration_seconds, default=default)
    per_frame = int(round(resolved_duration * 1000.0 / max(1, int(frame_count))))
    return max(20 if gif else 1, per_frame)


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
    packed = _star_position_alignment_candidates(reference_positions, source_positions, image_shape)
    if packed is None:
        return None
    candidates, match_count = packed
    return _choose_orientation_from_candidates(
        candidates,
        previous_orientation=previous_orientation,
        match_count=match_count,
    )


def estimate_full_frame_alignment(
    reference: np.ndarray,
    source: np.ndarray,
    *,
    previous_orientation: str = "identity",
    reference_positions: dict[str, tuple[float, float]] | None = None,
    source_positions: dict[str, tuple[float, float]] | None = None,
) -> StampAlignmentSolution:
    candidates, match_count, fallback = _collect_full_frame_alignment_inputs(
        reference,
        source,
        reference_positions=reference_positions,
        source_positions=source_positions,
    )
    if candidates is not None:
        return _choose_orientation_from_candidates(
            candidates,
            previous_orientation=previous_orientation,
            match_count=match_count,
        )
    return fallback if fallback is not None else StampAlignmentSolution()


def _star_position_alignment_candidates(
    reference_positions: dict[str, tuple[float, float]],
    source_positions: dict[str, tuple[float, float]],
    image_shape: tuple[int, int],
) -> tuple[dict[str, tuple[StampAlignmentSolution, float, int]], int] | None:
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
    return candidates, len(shared_ids)


def _collect_full_frame_alignment_inputs(
    reference: np.ndarray | None,
    source: np.ndarray | None,
    *,
    source_shape: tuple[int, ...] | None = None,
    reference_positions: dict[str, tuple[float, float]] | None = None,
    source_positions: dict[str, tuple[float, float]] | None = None,
) -> tuple[dict[str, tuple[StampAlignmentSolution, float, int]] | None, int, StampAlignmentSolution | None]:
    if source_shape is None and source is not None:
        source_shape = tuple(int(axis) for axis in np.asarray(source).shape[:2])
    if reference_positions and source_positions and source_shape is not None and len(source_shape) >= 2:
        packed = _star_position_alignment_candidates(
            reference_positions,
            source_positions,
            (int(source_shape[0]), int(source_shape[1])),
        )
        if packed is not None:
            candidates, match_count = packed
            return candidates, match_count, None
    if reference is None or source is None:
        return None, 0, None
    source_small, scale = _downsample_for_alignment(source)
    reference_small, _reference_scale = _downsample_for_alignment(reference, target_shape=source_small.shape)
    packed = _detected_star_alignment_candidates(reference_small, source_small)
    if packed is not None:
        candidates, match_count = packed
        return _scale_alignment_candidates(candidates, scale), match_count, None
    fallback = _translation_only_full_frame_alignment(reference_small, source_small)
    return None, 0, _scale_alignment_solution(fallback, scale)


def _scale_alignment_solution(solution: StampAlignmentSolution, scale: float) -> StampAlignmentSolution:
    if abs(float(scale) - 1.0) < 0.01:
        return solution
    return StampAlignmentSolution(
        orientation=solution.orientation,
        shift_y=float(solution.shift_y) * float(scale),
        shift_x=float(solution.shift_x) * float(scale),
        score=solution.score,
    )


def _scale_alignment_candidates(
    candidates: dict[str, tuple[StampAlignmentSolution, float, int]],
    scale: float,
) -> dict[str, tuple[StampAlignmentSolution, float, int]]:
    if abs(float(scale) - 1.0) < 0.01:
        return candidates
    scaled: dict[str, tuple[StampAlignmentSolution, float, int]] = {}
    for orientation, (solution, residual, count) in candidates.items():
        scaled_residual = residual * float(scale) if math.isfinite(residual) else residual
        scaled[orientation] = (_scale_alignment_solution(solution, scale), scaled_residual, count)
    return scaled


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
    plane = np.asarray(_as_grayscale(source_image), dtype=np.float32)
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
    align_mode: str | None = None,
    crop_x: float | None = None,
    crop_y: float | None = None,
) -> Path:
    resolved_mode = normalize_target_field_align_mode(
        align_mode if align_mode is not None else (TARGET_FIELD_ALIGN_CROP_THEN_ALIGN if align else TARGET_FIELD_ALIGN_NONE)
    )
    mode_token = {
        TARGET_FIELD_ALIGN_CROP_THEN_ALIGN: "crop-then-align-v5",
        TARGET_FIELD_ALIGN_ALIGN_THEN_CROP: "align-then-crop-v1",
    }.get(resolved_mode, "raw-crop")
    token = "|".join(
        (
            str(source_id),
            str(int(fov_px)),
            mode_token,
            str(Path(file_path)),
            f"{float(x):.3f}",
            f"{float(y):.3f}",
            f"{float(crop_x if crop_x is not None else x):.3f}",
            f"{float(crop_y if crop_y is not None else y):.3f}",
        )
    )
    digest = hashlib.sha1(token.encode("utf-8")).hexdigest()[:20]
    return Path(cache_dir) / "target-field-animation" / f"{digest}.npy"


def _full_frame_bytes(frame_shape: tuple[int, int]) -> int:
    height, width = int(frame_shape[0]), int(frame_shape[1])
    return max(1, height * width * int(np.dtype(_WORKING_IMAGE_DTYPE).itemsize))


def _full_frame_worker_cap(frame_shape: tuple[int, int]) -> int | None:
    bytes_per_frame = _full_frame_bytes(frame_shape)
    if bytes_per_frame < _SMALL_FRAME_WORKER_BYTES:
        return None
    return max(1, int(_FULL_FRAME_WORKER_BUDGET_BYTES // (2 * bytes_per_frame)))


def resolve_target_field_parallel_workers(
    max_workers: int | None = None,
    *,
    frame_shape: tuple[int, int] | None = None,
) -> int:
    if max_workers is not None and int(max_workers) > 0:
        workers = max(1, min(32, int(max_workers)))
    else:
        cpu_count = os.cpu_count() or 2
        workers = max(1, min(_TARGET_FIELD_AUTO_MAX_WORKERS, cpu_count - 1))
    if frame_shape is not None:
        cap = _full_frame_worker_cap(frame_shape)
        if cap is not None:
            workers = min(workers, cap)
    return workers


@dataclass(slots=True)
class _PreparedTargetFieldFrame:
    index: int
    cache_path: Path | None = None
    cached_stamp: np.ndarray | None = None
    image_path: Path | None = None
    stamp: np.ndarray | None = None
    candidates: dict[str, tuple[StampAlignmentSolution, float, int]] | None = None
    match_count: int = 0
    fallback_solution: StampAlignmentSolution | None = None
    is_reference: bool = False


def _save_target_field_stamp_cache(cache_path: Path | None, stamp: np.ndarray) -> None:
    if cache_path is None:
        return
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    np.save(cache_path, stamp)


def _load_cached_target_field_stamp(cache_path: Path | None, fov_px: int) -> np.ndarray | None:
    if cache_path is None or not cache_path.exists():
        return None
    try:
        cached = np.load(cache_path)
    except (OSError, ValueError):
        cache_path.unlink(missing_ok=True)
        return None
    if cached.shape != (int(fov_px), int(fov_px)):
        return None
    return np.asarray(cached, dtype=float)


def _load_working_image(path: Path) -> np.ndarray:
    return _as_grayscale(read_photometry_image_data(path, dtype=_WORKING_IMAGE_DTYPE))


def _image_shape_hw(path: Path, fallback: np.ndarray | None = None) -> tuple[int, int] | None:
    try:
        _header, width, height = read_header_and_shape(path)
    except Exception:
        width = height = None
    if width and height:
        return int(height), int(width)
    if fallback is not None:
        plane = np.asarray(fallback)
        if plane.ndim >= 2:
            return int(plane.shape[0]), int(plane.shape[1])
    return None


def _prepare_target_field_frame(
    index: int,
    measurement: PhotometryMeasurement,
    *,
    align_mode: str,
    fov_px: int,
    cache_dir: Path | None,
    source_id: str,
    reference_measurement: PhotometryMeasurement,
    reference_image: np.ndarray | None,
    reference_crop: np.ndarray | None,
    reference_positions: dict[str, tuple[float, float]] | None,
    source_positions: dict[str, tuple[float, float]] | None,
) -> _PreparedTargetFieldFrame:
    cache_path = None
    if cache_dir is not None:
        cache_path = target_field_stamp_cache_path(
            cache_dir,
            source_id=source_id,
            fov_px=fov_px,
            file_path=measurement.file_path,
            x=measurement.x,
            y=measurement.y,
            align=align_mode == TARGET_FIELD_ALIGN_CROP_THEN_ALIGN,
            align_mode=align_mode,
            crop_x=reference_measurement.x,
            crop_y=reference_measurement.y,
        )
        cached = _load_cached_target_field_stamp(cache_path, fov_px)
        if cached is not None:
            return _PreparedTargetFieldFrame(index=index, cache_path=cache_path, cached_stamp=cached)
    image_path = Path(measurement.file_path)
    if not image_path.exists():
        raise TargetFieldAnimationError(f"Missing image for target-field animation: {image_path}")
    is_reference_frame = image_path == Path(reference_measurement.file_path)
    if align_mode == TARGET_FIELD_ALIGN_NONE:
        stamp = crop_target_stamp(_load_working_image(image_path), measurement.x, measurement.y, fov_px)
        return _PreparedTargetFieldFrame(index=index, cache_path=cache_path, stamp=stamp)
    if is_reference_frame:
        source_image = _as_grayscale(reference_image) if reference_image is not None else _load_working_image(image_path)
        center_x = reference_measurement.x if align_mode == TARGET_FIELD_ALIGN_ALIGN_THEN_CROP else measurement.x
        center_y = reference_measurement.y if align_mode == TARGET_FIELD_ALIGN_ALIGN_THEN_CROP else measurement.y
        stamp = crop_target_stamp(source_image, center_x, center_y, fov_px)
        return _PreparedTargetFieldFrame(
            index=index,
            cache_path=cache_path,
            stamp=stamp,
            is_reference=True,
        )
    source_shape = _image_shape_hw(image_path, fallback=reference_image)
    if align_mode == TARGET_FIELD_ALIGN_ALIGN_THEN_CROP:
        candidates = None
        match_count = 0
        fallback = None
        if source_shape is not None:
            candidates, match_count, fallback = _collect_full_frame_alignment_inputs(
                None,
                None,
                source_shape=source_shape,
                reference_positions=reference_positions,
                source_positions=source_positions,
            )
        if candidates is None and fallback is None:
            source_image = _load_working_image(image_path)
            try:
                candidates, match_count, fallback = _collect_full_frame_alignment_inputs(
                    reference_image if reference_image is not None else source_image,
                    source_image,
                    source_shape=source_image.shape,
                    reference_positions=reference_positions,
                    source_positions=source_positions,
                )
            finally:
                del source_image
        return _PreparedTargetFieldFrame(
            index=index,
            cache_path=cache_path,
            image_path=image_path,
            candidates=candidates,
            match_count=match_count,
            fallback_solution=fallback,
        )
    source_image = _load_working_image(image_path)
    try:
        stamp = crop_target_stamp(source_image, measurement.x, measurement.y, fov_px)
        source_shape = tuple(int(axis) for axis in source_image.shape[:2])
    finally:
        del source_image
    candidates = None
    match_count = 0
    fallback = None
    if reference_positions and source_positions and source_shape is not None:
        shared_count = len(reference_positions.keys() & source_positions.keys())
        if shared_count >= 2:
            packed = _star_position_alignment_candidates(
                reference_positions,
                source_positions,
                source_shape,
            )
            if packed is not None:
                candidates, match_count = packed
    if candidates is None:
        aligned_reference_crop = reference_crop
        if aligned_reference_crop is None and reference_image is not None:
            aligned_reference_crop = crop_target_stamp(
                reference_image,
                reference_measurement.x,
                reference_measurement.y,
                fov_px,
            )
        if aligned_reference_crop is not None:
            candidates, match_count, fallback = _collect_full_frame_alignment_inputs(
                aligned_reference_crop,
                stamp,
            )
    return _PreparedTargetFieldFrame(
        index=index,
        cache_path=cache_path,
        stamp=stamp,
        candidates=candidates,
        match_count=match_count,
        fallback_solution=fallback,
    )


def _finalize_prepared_target_field_frame(
    prepared: _PreparedTargetFieldFrame,
    *,
    align_mode: str,
    previous_orientation: str,
    reference_measurement: PhotometryMeasurement,
    fov_px: int,
) -> tuple[np.ndarray, str]:
    if prepared.cached_stamp is not None:
        return prepared.cached_stamp, previous_orientation
    if prepared.is_reference or align_mode == TARGET_FIELD_ALIGN_NONE:
        stamp = prepared.stamp
        if stamp is None:
            raise TargetFieldAnimationError("Prepared target-field frame is missing a crop.")
        _save_target_field_stamp_cache(prepared.cache_path, stamp)
        return stamp, "identity" if prepared.is_reference else previous_orientation
    if prepared.candidates is not None:
        solution = _choose_orientation_from_candidates(
            prepared.candidates,
            previous_orientation=previous_orientation,
            match_count=prepared.match_count,
        )
    else:
        solution = prepared.fallback_solution or StampAlignmentSolution()
    if align_mode == TARGET_FIELD_ALIGN_ALIGN_THEN_CROP:
        if prepared.image_path is None:
            raise TargetFieldAnimationError("Prepared target-field frame is missing the source image path.")
        source_image = _load_working_image(prepared.image_path)
        try:
            stamp = crop_image_aligned_stamp(
                source_image,
                solution,
                center_x=reference_measurement.x,
                center_y=reference_measurement.y,
                fov_px=fov_px,
            )
        finally:
            del source_image
    else:
        if prepared.stamp is None:
            raise TargetFieldAnimationError("Prepared target-field frame is missing a crop.")
        stamp = orient_target_stamp(prepared.stamp, solution.orientation)
    _save_target_field_stamp_cache(prepared.cache_path, stamp)
    return stamp, solution.orientation


def _load_target_field_stamps_parallel(
    frames: Sequence[TargetFieldFrame],
    *,
    align_mode: str,
    fov_px: int,
    cache_dir: Path | None,
    source_id: str,
    reference_measurement: PhotometryMeasurement,
    reference_image: np.ndarray | None,
    reference_crop: np.ndarray | None,
    positions_by_file: dict[str, dict[str, tuple[float, float]]],
    max_workers: int,
    progress_callback: Callable[[TargetFieldAnimationProgress], None] | None,
    is_cancelled: Callable[[], bool] | None,
    progress_message: Callable[[int, int], str],
) -> list[np.ndarray]:
    frame_count = len(frames)
    frame_shape = None
    if reference_image is not None:
        frame_shape = tuple(int(axis) for axis in np.asarray(reference_image).shape[:2])
    elif frames:
        frame_shape = _image_shape_hw(Path(frames[0].measurement.file_path))
    worker_count = max(1, min(int(max_workers), frame_count))
    if frame_shape is not None:
        ram_cap = _full_frame_worker_cap(frame_shape)
        if ram_cap is not None:
            worker_count = max(1, min(worker_count, ram_cap))
    stamps: list[np.ndarray | None] = [None] * frame_count
    previous_orientation = "identity"
    reference_positions = positions_by_file.get(_file_key(reference_measurement.file_path))

    def _prepare(index: int) -> _PreparedTargetFieldFrame:
        frame = frames[index]
        return _prepare_target_field_frame(
            index,
            frame.measurement,
            align_mode=align_mode,
            fov_px=fov_px,
            cache_dir=cache_dir,
            source_id=source_id,
            reference_measurement=reference_measurement,
            reference_image=reference_image,
            reference_crop=reference_crop,
            reference_positions=reference_positions,
            source_positions=positions_by_file.get(_file_key(frame.measurement.file_path)),
        )

    def _finalize_ready(prepared_by_index: dict[int, _PreparedTargetFieldFrame], next_index: int) -> int:
        nonlocal previous_orientation
        while next_index in prepared_by_index:
            _raise_if_canceled(is_cancelled)
            prepared = prepared_by_index.pop(next_index)
            stamp, previous_orientation = _finalize_prepared_target_field_frame(
                prepared,
                align_mode=align_mode,
                previous_orientation=previous_orientation,
                reference_measurement=reference_measurement,
                fov_px=fov_px,
            )
            stamps[next_index] = stamp
            completed = next_index + 1
            _emit_progress(
                progress_callback,
                stage=TARGET_FIELD_PROGRESS_PREPARE,
                completed=completed,
                total=frame_count,
                message=progress_message(completed, frame_count),
            )
            next_index += 1
        return next_index

    if worker_count <= 1:
        prepared_by_index: dict[int, _PreparedTargetFieldFrame] = {}
        next_index = 0
        for index in range(frame_count):
            _raise_if_canceled(is_cancelled)
            prepared_by_index[index] = _prepare(index)
            next_index = _finalize_ready(prepared_by_index, next_index)
    else:
        prepared_by_index = {}
        next_index = 0
        next_submit = 0
        in_flight: dict = {}
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            def _submit_available() -> None:
                nonlocal next_submit
                while (
                    next_submit < frame_count
                    and (len(in_flight) + len(prepared_by_index)) < worker_count
                ):
                    _raise_if_canceled(is_cancelled)
                    future = executor.submit(_prepare, next_submit)
                    in_flight[future] = next_submit
                    next_submit += 1

            try:
                while next_index < frame_count:
                    _raise_if_canceled(is_cancelled)
                    _submit_available()
                    if next_index in prepared_by_index:
                        next_index = _finalize_ready(prepared_by_index, next_index)
                        continue
                    if not in_flight:
                        raise TargetFieldAnimationError(
                            "Target-field animation did not finish preparing every frame."
                        )
                    done, _pending = wait(in_flight, return_when=FIRST_COMPLETED)
                    for future in done:
                        in_flight.pop(future, None)
                        prepared = future.result()
                        prepared_by_index[prepared.index] = prepared
            except BaseException:
                for future in list(in_flight):
                    future.cancel()
                raise
    if any(stamp is None for stamp in stamps):
        raise TargetFieldAnimationError("Target-field animation did not finish preparing every frame.")
    return [np.asarray(stamp, dtype=float) for stamp in stamps if stamp is not None]


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
    stamp = crop_target_stamp(_load_working_image(image_path), measurement.x, measurement.y, fov_px)
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
            align_mode=TARGET_FIELD_ALIGN_CROP_THEN_ALIGN,
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
        _as_grayscale(reference_image)
        if is_reference_frame
        else _load_working_image(image_path)
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


def load_or_create_align_then_crop_stamp(
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
            align_mode=TARGET_FIELD_ALIGN_ALIGN_THEN_CROP,
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
        _as_grayscale(reference_image)
        if is_reference_frame
        else _load_working_image(image_path)
    )
    if is_reference_frame:
        orientation = "identity"
        stamp = crop_target_stamp(source_image, reference_measurement.x, reference_measurement.y, fov_px)
    else:
        solution = estimate_full_frame_alignment(
            reference_image,
            source_image,
            previous_orientation=previous_orientation,
            reference_positions=reference_positions,
            source_positions=source_positions,
        )
        orientation = solution.orientation
        stamp = crop_image_aligned_stamp(
            source_image,
            solution,
            center_x=reference_measurement.x,
            center_y=reference_measurement.y,
            fov_px=fov_px,
        )
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


def _stamp_marker_pens(appearance: TargetMarkerAppearance) -> tuple[QPen | None, QPen]:
    line_color = QColor(appearance.line_color)
    if not line_color.isValid():
        line_color = QColor("#ef4444")
    line_width = normalize_target_field_marker_line_width(appearance.line_width)
    pen = QPen(line_color, line_width)
    pen.setCosmetic(True)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    outline_color = QColor(str(appearance.outline_color or "").strip())
    outline_pen: QPen | None = None
    if outline_color.isValid() and outline_color.alpha() > 0 and line_width > 0.0:
        outline_pen = QPen(outline_color, line_width + 1.6)
        outline_pen.setCosmetic(True)
        outline_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        outline_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return outline_pen, pen


def target_field_marker_extents(
    width: float,
    height: float,
    length_percent: object = DEFAULT_TARGET_FIELD_MARKER_LENGTH_PERCENT,
    gap_percent: object = DEFAULT_TARGET_FIELD_MARKER_GAP_PERCENT,
) -> tuple[float, float]:
    size = max(1.0, min(float(width), float(height)))
    radius = size * 0.5
    outer = max(8.0, radius * (normalize_target_field_marker_length_percent(length_percent) / 100.0))
    gap = radius * (normalize_target_field_marker_gap_percent(gap_percent) / 100.0)
    gap = min(outer - 3.0, max(2.0, gap))
    if gap >= outer - 2.0:
        gap = max(2.0, outer * 0.45)
    return outer, gap


def _paint_stamp_marker_shape(
    painter: QPainter,
    style: str,
    *,
    center_x: float,
    center_y: float,
    width: float,
    height: float,
    length_percent: object = DEFAULT_TARGET_FIELD_MARKER_LENGTH_PERCENT,
    gap_percent: object = DEFAULT_TARGET_FIELD_MARKER_GAP_PERCENT,
) -> None:
    outer, gap = target_field_marker_extents(width, height, length_percent, gap_percent)
    left = center_x - outer
    top = center_y - outer
    right = center_x + outer
    bottom = center_y + outer
    painter.setBrush(Qt.BrushStyle.NoBrush)
    if style == "pointer":
        for start, end in pointer_marker_segments(center_x, center_y, left=left, top=top, gap=gap):
            painter.drawLine(QPointF(start[0], start[1]), QPointF(end[0], end[1]))
        return
    if style == "crosshair":
        painter.drawLine(QPointF(center_x - outer, center_y), QPointF(center_x - gap, center_y))
        painter.drawLine(QPointF(center_x + gap, center_y), QPointF(center_x + outer, center_y))
        painter.drawLine(QPointF(center_x, center_y - outer), QPointF(center_x, center_y - gap))
        painter.drawLine(QPointF(center_x, center_y + gap), QPointF(center_x, center_y + outer))
        return
    if style == "brackets":
        arm = max(4.0, min(outer * 0.5, outer - 1.5))
        corners = (
            (center_x - outer, center_y - outer, arm, arm, 1, 1),
            (center_x + outer, center_y - outer, arm, arm, -1, 1),
            (center_x - outer, center_y + outer, arm, arm, 1, -1),
            (center_x + outer, center_y + outer, arm, arm, -1, -1),
        )
        for corner_x, corner_y, arm_x, arm_y, sign_x, sign_y in corners:
            painter.drawLine(
                QPointF(corner_x, corner_y),
                QPointF(corner_x + sign_x * arm_x, corner_y),
            )
            painter.drawLine(
                QPointF(corner_x, corner_y),
                QPointF(corner_x, corner_y + sign_y * arm_y),
            )
        return
    if style == "target":
        edge_gap = max(2.0, min(10.0, outer * 0.35))
        painter.drawRect(QRectF(left, top, max(1.0, right - left), max(1.0, bottom - top)))
        painter.drawLine(QPointF(left - edge_gap, center_y), QPointF(left, center_y))
        painter.drawLine(QPointF(right, center_y), QPointF(right + edge_gap, center_y))
        painter.drawLine(QPointF(center_x, top - edge_gap), QPointF(center_x, top))
        painter.drawLine(QPointF(center_x, bottom), QPointF(center_x, bottom + edge_gap))
        return
    painter.drawEllipse(QPointF(center_x, center_y), max(6.0, outer * 0.72), max(6.0, outer * 0.72))


def apply_target_field_marker(
    stamp_image: QImage,
    *,
    style: str,
    appearance: TargetMarkerAppearance | None = None,
) -> QImage:
    resolved_style = normalize_target_field_marker_style(style)
    if resolved_style == TARGET_FIELD_MARKER_NONE:
        return stamp_image
    marked = stamp_image.convertToFormat(QImage.Format.Format_RGB888)
    if marked.isNull():
        return stamp_image
    resolved_appearance = appearance or TargetMarkerAppearance()
    painter = QPainter(marked)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        outline_pen, pen = _stamp_marker_pens(resolved_appearance)
        center_x = marked.width() * 0.5
        center_y = marked.height() * 0.5
        paint_kwargs = {
            "center_x": center_x,
            "center_y": center_y,
            "width": float(marked.width()),
            "height": float(marked.height()),
            "length_percent": resolved_appearance.length_percent,
            "gap_percent": resolved_appearance.gap_percent,
        }
        if outline_pen is not None:
            painter.setPen(outline_pen)
            _paint_stamp_marker_shape(painter, resolved_style, **paint_kwargs)
        painter.setPen(pen)
        _paint_stamp_marker_shape(painter, resolved_style, **paint_kwargs)
    finally:
        painter.end()
    return marked


def synthetic_target_field_preview_stamp(fov_px: int) -> np.ndarray:
    size = normalize_target_field_fov_px(fov_px)
    yy, xx = np.mgrid[0:size, 0:size]
    center = (size - 1) / 2.0
    radius = max(1.2, size * 0.018)
    stamp = np.full((size, size), 0.10, dtype=float)
    stamp += np.exp(-((xx - center) ** 2 + (yy - center) ** 2) / (2.0 * radius * radius))
    return stamp


def render_target_field_marker_preview(
    image: np.ndarray | None,
    x: float | None,
    y: float | None,
    *,
    fov_px: int,
    stretch_mode: str = DEFAULT_TARGET_FIELD_STRETCH_MODE,
    marker_style: str = DEFAULT_TARGET_FIELD_MARKER_STYLE,
    appearance: TargetMarkerAppearance | None = None,
) -> QImage:
    resolved_fov = normalize_target_field_fov_px(fov_px)
    if image is None or x is None or y is None:
        stamp = synthetic_target_field_preview_stamp(resolved_fov)
    else:
        stamp = crop_target_stamp(image, float(x), float(y), resolved_fov)
    stretched = stretch_stamps_to_shared_display([stamp], stretch_mode=stretch_mode)[0]
    stamp_image = stamp_to_qimage(stretched)
    return apply_target_field_marker(
        stamp_image,
        style=marker_style,
        appearance=appearance,
    )


def load_target_field_preview_source(frame: TargetFieldFrame) -> tuple[np.ndarray | None, float, float]:
    x = float(frame.measurement.x)
    y = float(frame.measurement.y)
    try:
        image = read_photometry_image_data(frame.measurement.file_path)
    except Exception:
        return None, x, y
    return image, x, y


def _even_dimension(value: int, *, minimum: int = 2) -> int:
    size = max(minimum, int(value))
    if size % 2:
        size += 1
    return size


def target_field_output_size(
    options: TargetFieldAnimationExportOptions | None = None,
    *,
    stamp_image: QImage | None = None,
    plot_image: QImage | None = None,
) -> tuple[int, int]:
    if options is not None:
        resolved = options.normalized()
        return resolved.video_width, resolved.video_height
    stamp = stamp_image if stamp_image is not None else QImage()
    plot = plot_image if plot_image is not None else QImage()
    return target_field_animation_frame_size(stamp, plot)


def target_field_animation_frame_size(stamp_image: QImage, plot_image: QImage) -> tuple[int, int]:
    width = max(int(plot_image.width()), int(stamp_image.width()), _TARGET_FIELD_MIN_FRAME_WIDTH)
    width = min(_TARGET_FIELD_MAX_FRAME_WIDTH, width)
    width = _even_dimension(width, minimum=_TARGET_FIELD_MIN_FRAME_WIDTH)
    height = _even_dimension(int(round(width / TARGET_FIELD_OUTPUT_ASPECT_RATIO)))
    return width, height


def _parse_numeric_aspect(aspect: str) -> float | None:
    if ":" not in aspect:
        return None
    left, right = aspect.split(":", 1)
    try:
        width = float(left)
        height = float(right)
    except ValueError:
        return None
    if width <= 0.0 or height <= 0.0 or not math.isfinite(width) or not math.isfinite(height):
        return None
    return width / height


def _format_aspect_part(value: float) -> str:
    if abs(value - round(value)) < 0.05:
        return str(int(round(value)))
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _canonical_aspect_key(aspect: str) -> str | None:
    key = str(aspect or "").strip().lower().replace(" ", "")
    if key in {"remaining", "rest", "fill-remaining", "fillremaining"}:
        return TARGET_FIELD_ASPECT_REMAINING
    if key in TARGET_FIELD_ASPECT_RATIOS:
        return key
    ratio = _parse_numeric_aspect(key)
    if ratio is None:
        return None
    for name, known in TARGET_FIELD_ASPECT_RATIOS.items():
        if abs(ratio - known) <= 0.02:
            return name
    left, right = key.split(":", 1)
    return f"{_format_aspect_part(float(left))}:{_format_aspect_part(float(right))}"


def _aspect_ratio_value(aspect: str) -> float | None:
    if aspect == TARGET_FIELD_ASPECT_REMAINING:
        return None
    if aspect in TARGET_FIELD_ASPECT_RATIOS:
        return TARGET_FIELD_ASPECT_RATIOS[aspect]
    return _parse_numeric_aspect(aspect)


def target_field_star_height_percent(
    star_aspect: str = DEFAULT_TARGET_FIELD_STAR_ASPECT,
    plot_aspect: str = DEFAULT_TARGET_FIELD_PLOT_ASPECT,
) -> float:
    star_ratio = _aspect_ratio_value(normalize_target_field_star_aspect(star_aspect))
    plot_ratio = _aspect_ratio_value(normalize_target_field_plot_aspect(plot_aspect)) or TARGET_FIELD_PLOT_ASPECT_RATIO
    if star_ratio is None or star_ratio <= 0.0:
        plot_fraction = min(0.8, max(0.2, (16.0 / 9.0) / plot_ratio))
        percent = (1.0 - plot_fraction) * 100.0
    else:
        percent = (plot_ratio / (star_ratio + plot_ratio)) * 100.0
    return min(MAX_TARGET_FIELD_STAR_HEIGHT_PERCENT, max(MIN_TARGET_FIELD_STAR_HEIGHT_PERCENT, percent))


def target_field_aspects_from_star_height_percent(percent: object) -> tuple[str, str]:
    try:
        value = float(percent)
    except (TypeError, ValueError):
        value = DEFAULT_TARGET_FIELD_STAR_HEIGHT_PERCENT
    if not math.isfinite(value):
        value = DEFAULT_TARGET_FIELD_STAR_HEIGHT_PERCENT
    fraction = min(MAX_TARGET_FIELD_STAR_HEIGHT_PERCENT, max(MIN_TARGET_FIELD_STAR_HEIGHT_PERCENT, value)) / 100.0
    star_height_units = 9.0 * fraction
    plot_height_units = 9.0 * (1.0 - fraction)
    return (
        normalize_target_field_star_aspect(f"16:{star_height_units:.3f}"),
        normalize_target_field_plot_aspect(f"16:{plot_height_units:.3f}"),
    )


def target_field_panel_heights(
    frame_width: int,
    frame_height: int,
    *,
    star_aspect: str = DEFAULT_TARGET_FIELD_STAR_ASPECT,
    plot_aspect: str = DEFAULT_TARGET_FIELD_PLOT_ASPECT,
) -> tuple[int, int]:
    width = max(2, int(frame_width))
    height = max(4, int(frame_height))
    plot_ratio = _aspect_ratio_value(normalize_target_field_plot_aspect(plot_aspect)) or TARGET_FIELD_PLOT_ASPECT_RATIO
    plot_height = _even_dimension(max(2, int(round(width / plot_ratio))))
    star_key = normalize_target_field_star_aspect(star_aspect)
    star_ratio = _aspect_ratio_value(star_key)
    if star_ratio is None:
        plot_height = min(plot_height, height - 2)
        return max(2, height - plot_height), plot_height
    star_height = _even_dimension(max(2, int(round(width / star_ratio))))
    total = star_height + plot_height
    if total != height and total > 0:
        scale = height / total
        star_height = _even_dimension(max(2, int(round(star_height * scale))))
        plot_height = max(2, height - star_height)
    else:
        plot_height = min(plot_height, height - 2)
        star_height = height - plot_height
    return star_height, plot_height


def cinematic_center_crop(image: QImage, dest_width: int, dest_height: int) -> QImage:
    source = image.convertToFormat(QImage.Format.Format_RGB888)
    dest_width = max(1, int(dest_width))
    dest_height = max(1, int(dest_height))
    source_width = max(1, source.width())
    source_height = max(1, source.height())
    dest_aspect = dest_width / dest_height
    source_aspect = source_width / source_height
    if source_aspect < dest_aspect:
        crop_height = max(1, min(source_height, int(round(source_width / dest_aspect))))
        top = (source_height - crop_height) // 2
        cropped = source.copy(0, top, source_width, crop_height)
    elif source_aspect > dest_aspect:
        crop_width = max(1, min(source_width, int(round(source_height * dest_aspect))))
        left = (source_width - crop_width) // 2
        cropped = source.copy(left, 0, crop_width, source_height)
    else:
        cropped = source
    return cropped.scaled(
        dest_width,
        dest_height,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def _plot_panel_fill_color(plot_image: QImage) -> QColor:
    if plot_image.isNull() or plot_image.width() <= 0 or plot_image.height() <= 0:
        return QColor("#111111")
    color = QColor(plot_image.pixelColor(0, 0))
    return color if color.isValid() else QColor("#111111")


def fit_image_to_panel(
    image: QImage,
    dest_width: int,
    dest_height: int,
    *,
    fit_mode: str = TARGET_FIELD_FIT_STRETCH,
    fill: QColor | None = None,
) -> QImage:
    panel_width = max(1, int(dest_width))
    panel_height = max(1, int(dest_height))
    background = fill if fill is not None and fill.isValid() else QColor("#000000")
    mode = normalize_target_field_fit_mode(fit_mode)
    source = image.convertToFormat(QImage.Format.Format_RGB888)
    if source.isNull() or source.width() <= 0 or source.height() <= 0:
        panel = QImage(panel_width, panel_height, QImage.Format.Format_RGB888)
        panel.fill(background)
        return panel
    if mode == TARGET_FIELD_FIT_FILL:
        return cinematic_center_crop(source, panel_width, panel_height)
    if mode == TARGET_FIELD_FIT_STRETCH:
        return source.scaled(
            panel_width,
            panel_height,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    panel = QImage(panel_width, panel_height, QImage.Format.Format_RGB888)
    panel.fill(background)
    fitted = source.scaled(
        panel_width,
        panel_height,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    painter = QPainter(panel)
    try:
        painter.drawImage(
            (panel_width - fitted.width()) // 2,
            (panel_height - fitted.height()) // 2,
            fitted,
        )
    finally:
        painter.end()
    return panel


def scale_plot_scan_layout(
    layout: TargetFieldPlotScanLayout,
    source_width: int,
    source_height: int,
    dest_width: int,
    dest_height: int,
) -> TargetFieldPlotScanLayout:
    scale_x = dest_width / max(1.0, float(source_width))
    scale_y = dest_height / max(1.0, float(source_height))
    return TargetFieldPlotScanLayout(
        x_min=layout.x_min,
        x_max=layout.x_max,
        y_min=layout.y_min,
        y_max=layout.y_max,
        left=layout.left * scale_x,
        top=layout.top * scale_y,
        right=layout.right * scale_x,
        bottom=layout.bottom * scale_y,
        invert_y=layout.invert_y,
        color=layout.color,
    )


def apply_target_field_plot_scanner(
    plot_image: QImage,
    x_value: float | None,
    layout: TargetFieldPlotScanLayout,
    *,
    y_value: float | None = None,
) -> QImage:
    if x_value is None or not math.isfinite(x_value) or layout.x_max == layout.x_min:
        return plot_image
    span = layout.x_max - layout.x_min
    fraction = (float(x_value) - layout.x_min) / span
    if not math.isfinite(fraction):
        return plot_image
    fraction = min(1.0, max(0.0, fraction))
    x = layout.left + fraction * (layout.right - layout.left)
    result = plot_image.copy()
    color = QColor(layout.color)
    if not color.isValid():
        color = QColor("#f4d35e")
    color.setAlpha(220)
    pen = QPen(color, max(2.0, plot_image.width() / 480.0))
    pen.setCosmetic(True)
    painter = QPainter(result)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(pen)
        painter.drawLine(QPointF(x, layout.top), QPointF(x, layout.bottom))
        if y_value is not None and math.isfinite(y_value) and layout.y_max != layout.y_min:
            y_low = min(layout.y_min, layout.y_max)
            y_high = max(layout.y_min, layout.y_max)
            y_span = y_high - y_low
            y_fraction = (float(y_value) - y_low) / y_span
            if not math.isfinite(y_fraction):
                y_fraction = 0.0
            y_fraction = min(1.0, max(0.0, y_fraction))
            if not layout.invert_y:
                y_fraction = 1.0 - y_fraction
            y = layout.top + y_fraction * (layout.bottom - layout.top)
            radius = max(4.0, plot_image.width() / 160.0)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(x, y), radius, radius)
    finally:
        painter.end()
    return result


def compose_target_field_animation_frame(
    stamp_image: QImage,
    plot_image: QImage,
    *,
    options: TargetFieldAnimationExportOptions | None = None,
    frame_width: int | None = None,
    frame_height: int | None = None,
) -> QImage:
    resolved = None if options is None else options.normalized()
    if frame_width is not None and frame_height is not None:
        width = max(2, int(frame_width))
        height = max(2, int(frame_height))
    elif resolved is None:
        width, height = target_field_animation_frame_size(stamp_image, plot_image)
    else:
        width, height = resolved.video_width, resolved.video_height
    if resolved is None:
        star_aspect = DEFAULT_TARGET_FIELD_STAR_ASPECT
        plot_aspect = DEFAULT_TARGET_FIELD_PLOT_ASPECT
        star_fit = DEFAULT_TARGET_FIELD_STAR_FIT
        plot_fit = DEFAULT_TARGET_FIELD_PLOT_FIT
    else:
        star_aspect = resolved.star_aspect
        plot_aspect = resolved.plot_aspect
        star_fit = resolved.star_fit
        plot_fit = resolved.plot_fit
    star_height, plot_height = target_field_panel_heights(
        width,
        height,
        star_aspect=star_aspect,
        plot_aspect=plot_aspect,
    )
    fill = _plot_panel_fill_color(plot_image)
    composed = QImage(width, height, QImage.Format.Format_RGB888)
    composed.fill(QColor("#000000"))
    separator = min(TARGET_FIELD_PLOT_SEPARATOR_PX, max(0, plot_height - 2))
    plot_draw_height = max(2, plot_height - separator)
    star_panel = fit_image_to_panel(
        stamp_image,
        width,
        star_height,
        fit_mode=star_fit,
        fill=QColor("#000000"),
    )
    plot_panel = fit_image_to_panel(
        plot_image,
        width,
        plot_draw_height,
        fit_mode=plot_fit,
        fill=fill,
    )
    painter = QPainter(composed)
    try:
        painter.drawImage(0, 0, star_panel)
        if separator > 0:
            painter.fillRect(0, star_height, width, separator, fill)
        painter.drawImage(0, star_height + separator, plot_panel)
    finally:
        painter.end()
    return composed


def export_target_field_animation(
    report: ProcessingReport,
    source_id: str,
    output_path: Path,
    *,
    fov_px: int = DEFAULT_TARGET_FIELD_FOV_PX,
    align: bool | None = None,
    align_mode: str | None = None,
    fps: float | None = None,
    duration_seconds: float = DEFAULT_TARGET_FIELD_DURATION_SECONDS,
    loop_count: int = DEFAULT_TARGET_FIELD_LOOP_COUNT,
    scale_percent: int = DEFAULT_TARGET_FIELD_SCALE_PERCENT,
    stretch_mode: str = DEFAULT_TARGET_FIELD_STRETCH_MODE,
    export_format: str | None = None,
    marker_style: str = DEFAULT_TARGET_FIELD_MARKER_STYLE,
    marker_appearance: TargetMarkerAppearance | None = None,
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
    plot_image: QImage | None = None,
    plot_scan_layout: TargetFieldPlotScanLayout | None = None,
    video_width: int = DEFAULT_TARGET_FIELD_VIDEO_WIDTH,
    video_height: int = DEFAULT_TARGET_FIELD_VIDEO_HEIGHT,
    star_aspect: str = DEFAULT_TARGET_FIELD_STAR_ASPECT,
    plot_aspect: str = DEFAULT_TARGET_FIELD_PLOT_ASPECT,
    star_fit: str = DEFAULT_TARGET_FIELD_STAR_FIT,
    plot_fit: str = DEFAULT_TARGET_FIELD_PLOT_FIT,
    save_star_separately: bool = False,
    save_plot_separately: bool = False,
    layout_options: TargetFieldAnimationExportOptions | None = None,
    frame_duration_ms: int | None = None,
    max_workers: int | None = None,
    progress_callback: Callable[[TargetFieldAnimationProgress], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> None:
    frames = collect_target_field_frames(report, source_id, filter_name=filter_name)
    resolved_fov = normalize_target_field_fov_px(fov_px)
    resolved_mode = normalize_target_field_align_mode(
        align_mode
        if align_mode is not None
        else (TARGET_FIELD_ALIGN_CROP_THEN_ALIGN if (DEFAULT_TARGET_FIELD_ALIGN if align is None else align) else TARGET_FIELD_ALIGN_NONE)
    )
    resolved_format = normalize_target_field_export_format(
        export_format if export_format is not None else Path(output_path).suffix
    )
    resolved_marker_style = normalize_target_field_marker_style(marker_style)
    resolved_marker_appearance = marker_appearance or TargetMarkerAppearance()
    resolved_scale = normalize_target_field_scale_percent(scale_percent)
    resolved_loop_count = normalize_target_field_loop_count(loop_count)
    if frame_duration_ms is not None:
        duration_ms = max(1, int(frame_duration_ms))
    elif fps is not None:
        duration_ms = target_field_frame_duration_ms(fps)
    else:
        duration_ms = target_field_duration_frame_ms(
            duration_seconds,
            len(frames),
            gif=resolved_format == "gif",
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
    frame_count = len(frames)
    if resolved_mode == TARGET_FIELD_ALIGN_ALIGN_THEN_CROP:
        progress_label = "Aligning frames, then cropping the target field..."
    elif resolved_mode == TARGET_FIELD_ALIGN_CROP_THEN_ALIGN:
        progress_label = "Cropping and aligning target-field frames..."
    else:
        progress_label = "Cropping target-field frames..."
    _emit_progress(
        progress_callback,
        stage=TARGET_FIELD_PROGRESS_PREPARE,
        completed=0,
        total=frame_count,
        message=progress_label,
    )
    _raise_if_canceled(is_cancelled)
    reference_measurement = frames[0].measurement
    reference_image = None
    reference_crop = None
    positions_by_file: dict[str, dict[str, tuple[float, float]]] = {}
    if resolved_mode != TARGET_FIELD_ALIGN_NONE:
        reference_image = _load_working_image(reference_measurement.file_path)
        positions_by_file = star_positions_by_file(report, filter_name=filter_name)
        if resolved_mode == TARGET_FIELD_ALIGN_CROP_THEN_ALIGN:
            reference_crop = crop_target_stamp(reference_image, reference_measurement.x, reference_measurement.y, resolved_fov)
    if resolved_mode == TARGET_FIELD_ALIGN_ALIGN_THEN_CROP:
        def _prepare_progress(completed: int, total: int) -> str:
            return f"Aligning, then cropping frame {completed}/{total}..."
    elif resolved_mode == TARGET_FIELD_ALIGN_CROP_THEN_ALIGN:
        def _prepare_progress(completed: int, total: int) -> str:
            return f"Cropping and aligning frame {completed}/{total}..."
    else:
        def _prepare_progress(completed: int, total: int) -> str:
            return f"Cropping target-field frame {completed}/{total}..."
    frame_shape = None
    if reference_image is not None:
        frame_shape = tuple(int(axis) for axis in np.asarray(reference_image).shape[:2])
    elif frames:
        frame_shape = _image_shape_hw(Path(frames[0].measurement.file_path))
    stamps = _load_target_field_stamps_parallel(
        frames,
        align_mode=resolved_mode,
        fov_px=resolved_fov,
        cache_dir=cache_dir,
        source_id=source_id,
        reference_measurement=reference_measurement,
        reference_image=reference_image,
        reference_crop=reference_crop,
        positions_by_file=positions_by_file,
        max_workers=resolve_target_field_parallel_workers(max_workers, frame_shape=frame_shape),
        progress_callback=progress_callback,
        is_cancelled=is_cancelled,
        progress_message=_prepare_progress,
    )
    _raise_if_canceled(is_cancelled)
    _emit_progress(
        progress_callback,
        stage=TARGET_FIELD_PROGRESS_NORMALIZE,
        completed=0,
        total=1,
        message="Normalizing local comparison stars and stretching frames...",
    )
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
    _emit_progress(
        progress_callback,
        stage=TARGET_FIELD_PROGRESS_NORMALIZE,
        completed=1,
        total=1,
        message="Normalized local comparison stars and stretched frames.",
    )
    constant_plot = plot_image.copy() if plot_image is not None and not plot_image.isNull() else None
    layout = (
        layout_options.normalized()
        if layout_options is not None
        else TargetFieldAnimationExportOptions(
            video_width=video_width,
            video_height=video_height,
            star_aspect=star_aspect,
            plot_aspect=plot_aspect,
            star_fit=star_fit,
            plot_fit=plot_fit,
            save_star_separately=save_star_separately,
            save_plot_separately=save_plot_separately,
            export_format=resolved_format,
            scale_percent=resolved_scale,
        ).normalized()
    )
    star_height, plot_height = target_field_panel_heights(
        layout.video_width,
        layout.video_height,
        star_aspect=layout.star_aspect,
        plot_aspect=layout.plot_aspect,
    )
    if constant_plot is None:
        dpi = 150.0
        rendered = _render_light_curve_payload_with_highlight(
            payload,
            highlight_x=None,
            highlight_y=None,
            plot_theme=plot_theme,
            custom_theme_colors=custom_theme_colors,
            figure_size_inches=(max(1.0, layout.video_width / dpi), max(1.0, plot_height / dpi)),
            dpi=int(dpi),
            return_scan_layout=True,
        )
        if isinstance(rendered, tuple):
            constant_plot, inferred_layout = rendered
        else:
            constant_plot, inferred_layout = rendered, None
        if plot_scan_layout is None:
            plot_scan_layout = inferred_layout
    composed_frames: list[QImage] = []
    star_frames: list[QImage] = []
    plot_panel = fit_image_to_panel(
        constant_plot,
        layout.video_width,
        plot_height,
        fit_mode=layout.plot_fit,
        fill=_plot_panel_fill_color(constant_plot),
    )
    if plot_scan_layout is not None:
        plot_scan_layout = scale_plot_scan_layout(
            plot_scan_layout,
            constant_plot.width(),
            constant_plot.height(),
            plot_panel.width(),
            plot_panel.height(),
        )
    _emit_progress(
        progress_callback,
        stage=TARGET_FIELD_PROGRESS_COMPOSE,
        completed=0,
        total=frame_count,
        message="Composing animation frames...",
    )
    for index, (frame, stamp) in enumerate(zip(frames, display_stamps, strict=True), start=1):
        _raise_if_canceled(is_cancelled)
        stamp_image = stamp_to_qimage(stamp)
        if resolved_marker_style != TARGET_FIELD_MARKER_NONE:
            stamp_image = apply_target_field_marker(
                stamp_image,
                style=resolved_marker_style,
                appearance=resolved_marker_appearance,
            )
        star_panel = fit_image_to_panel(
            stamp_image,
            layout.video_width,
            star_height,
            fit_mode=layout.star_fit,
            fill=QColor("#000000"),
        )
        plot_for_frame = plot_panel
        if plot_scan_layout is not None:
            highlight = _highlight_for_frame(payload, frame)
            plot_for_frame = apply_target_field_plot_scanner(
                plot_panel,
                None if highlight is None else highlight[0],
                plot_scan_layout,
                y_value=None if highlight is None else highlight[1],
            )
        composed_frames.append(compose_target_field_animation_frame(stamp_image, plot_for_frame, options=layout))
        if layout.save_star_separately:
            star_frames.append(star_panel)
        _emit_progress(
            progress_callback,
            stage=TARGET_FIELD_PROGRESS_COMPOSE,
            completed=index,
            total=frame_count,
            message=f"Composing animation frame {index}/{frame_count}...",
        )
    _raise_if_canceled(is_cancelled)
    format_label = "MP4" if resolved_format == "mp4" else "GIF"
    _emit_progress(
        progress_callback,
        stage=TARGET_FIELD_PROGRESS_ENCODE,
        completed=0,
        total=1,
        message=f"Encoding target-field {format_label}...",
    )
    if resolved_format == "mp4":
        export_qimages_to_mp4(
            composed_frames,
            output_path,
            frame_duration_ms=duration_ms,
            scale_percent=resolved_scale,
            repeat_count=resolved_loop_count,
        )
    else:
        export_qimages_to_gif(
            composed_frames,
            output_path,
            frame_duration_ms=max(20, duration_ms),
            loop_count=0,
            scale_percent=resolved_scale,
        )
    if layout.save_star_separately and star_frames:
        star_path = Path(output_path).with_name(f"{Path(output_path).stem}_star{Path(output_path).suffix}")
        if resolved_format == "mp4":
            export_qimages_to_mp4(
                star_frames,
                star_path,
                frame_duration_ms=duration_ms,
                scale_percent=resolved_scale,
                repeat_count=resolved_loop_count,
            )
        else:
            export_qimages_to_gif(
                star_frames,
                star_path,
                frame_duration_ms=max(20, duration_ms),
                loop_count=0,
                scale_percent=resolved_scale,
            )
    if layout.save_plot_separately:
        plot_path = Path(output_path).with_name(f"{Path(output_path).stem}_plot.png")
        if not plot_panel.save(str(plot_path)):
            raise OSError(f"Unable to save the target-field light curve to {plot_path}")
    _emit_progress(
        progress_callback,
        stage=TARGET_FIELD_PROGRESS_ENCODE,
        completed=1,
        total=1,
        message=f"Saved target-field {format_label} to {output_path.name}.",
        done=True,
    )


def _as_grayscale(image: np.ndarray) -> np.ndarray:
    data = np.asarray(image, dtype=_WORKING_IMAGE_DTYPE)
    if data.ndim == 2:
        return data
    if data.ndim == 3 and data.shape[-1] in {1, 3, 4}:
        return np.asarray(np.mean(data[..., :3], axis=-1), dtype=_WORKING_IMAGE_DTYPE)
    if data.ndim == 3 and data.shape[0] in {1, 3, 4}:
        return np.asarray(np.mean(data[:3], axis=0), dtype=_WORKING_IMAGE_DTYPE)
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
    return_scan_layout: bool = False,
) -> QImage | tuple[QImage, TargetFieldPlotScanLayout | None]:
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
        scan_layout = TargetFieldPlotScanLayout(
            x_min=float(min(axis.get_xlim())),
            x_max=float(max(axis.get_xlim())),
            y_min=float(min(axis.get_ylim())),
            y_max=float(max(axis.get_ylim())),
            left=float(axis.get_position().x0) * figure.get_size_inches()[0] * dpi,
            top=(1.0 - float(axis.get_position().y1)) * figure.get_size_inches()[1] * dpi,
            right=float(axis.get_position().x1) * figure.get_size_inches()[0] * dpi,
            bottom=(1.0 - float(axis.get_position().y0)) * figure.get_size_inches()[1] * dpi,
            invert_y=bool(payload.invert_y or axis.get_ylim()[1] < axis.get_ylim()[0]),
            color="#f4d35e",
        )
        buffer = BytesIO()
        figure.savefig(buffer, format="png", dpi=dpi, facecolor=figure.get_facecolor())
        image = QImage()
        if not image.loadFromData(buffer.getvalue(), "PNG"):
            raise OSError("Unable to render the target-field light-curve frame.")
        if return_scan_layout:
            return image, scan_layout
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
    progress_callback: Callable[[TargetFieldAnimationProgress], None] | None,
    *,
    stage: str,
    completed: int,
    total: int,
    message: str,
    done: bool = False,
) -> None:
    if progress_callback is None:
        return
    progress_callback(
        TargetFieldAnimationProgress(
            stage=str(stage),
            completed=max(0, int(completed)),
            total=max(1, int(total)),
            message=str(message),
            done=bool(done),
        )
    )


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
    packed = _detected_star_alignment_candidates(reference, source)
    if packed is None:
        return None
    candidates, match_count = packed
    return _choose_orientation_from_candidates(
        candidates,
        previous_orientation=previous_orientation,
        match_count=match_count,
    )


def _detected_star_alignment_candidates(
    reference: np.ndarray,
    source: np.ndarray,
) -> tuple[dict[str, tuple[StampAlignmentSolution, float, int]], int] | None:
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
    return candidates, match_count


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
