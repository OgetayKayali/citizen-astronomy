"""Survey-as-primary tile model: identity, state, per-tile STF, and scheduling helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
import time
from typing import Mapping

import numpy as np

from photometry_app.core.survey_images import (
    SKY_EXPLORER_SURVEY_FIELD_DETAIL_PREVIEW,
    SKY_EXPLORER_SURVEY_FIELD_DETAIL_REFINE,
    SKY_EXPLORER_SURVEY_FIELD_MOSAIC_RADIUS,
    SKY_EXPLORER_SURVEY_FIELD_PREVIEW_MAX_EDGE,
    sky_explorer_survey_field_neighbor_tile_indices,
    sky_explorer_survey_field_preview_size,
    sky_explorer_survey_field_tile_spec,
)


# Bounded concurrency / queue policy (configurable constants, not magic numbers).
SURVEY_TILE_MAX_NETWORK_WORKERS = 2
SURVEY_TILE_MAX_PROCESS_WORKERS = 2
SURVEY_TILE_MAX_QUEUED_REQUESTS = 48
SURVEY_TILE_VIEWPORT_DEBOUNCE_MS = 120
SURVEY_TILE_MAX_RETRIES = 2
SURVEY_TILE_RETRY_BACKOFF_SECONDS = 2.5
SURVEY_TILE_MIN_VALID_PIXELS = 64
SURVEY_TILE_STF_LOWER_PERCENTILE = 0.5
SURVEY_TILE_STF_UPPER_PERCENTILE = 99.8
SURVEY_TILE_STF_MIN_DYNAMIC_RANGE = 1.0e-6
SURVEY_TILE_NO_DATA_MARKER = "no_survey_coverage"


class SurveyTileResolution(str, Enum):
    PREVIEW = SKY_EXPLORER_SURVEY_FIELD_DETAIL_PREVIEW
    REFINE = SKY_EXPLORER_SURVEY_FIELD_DETAIL_REFINE


class SurveyTileState(str, Enum):
    NOT_REQUESTED = "not_requested"
    QUEUED_LOW_RES = "queued_low_res"
    LOADING_LOW_RES = "loading_low_res"
    LOW_RES_READY = "low_res_ready"
    QUEUED_HIGH_RES = "queued_high_res"
    LOADING_HIGH_RES = "loading_high_res"
    HIGH_RES_READY = "high_res_ready"
    NO_DATA = "no_data"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_FINAL = "failed_final"
    CANCELLED = "cancelled"


class SurveyTileResultStatus(str, Enum):
    SUCCESS = "success"
    NO_DATA = "no_data"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_FINAL = "failed_final"


@dataclass(frozen=True, slots=True)
class SurveyTileKey:
    """Stable identity for one survey tile footprint (resolution is separate)."""

    survey_key: str
    tile_i: int
    tile_j: int
    origin_ra_deg: float
    origin_dec_deg: float
    fov_arcmin: float
    width_px: int
    height_px: int
    layer_id: str = "primary"

    def as_tuple(self) -> tuple[object, ...]:
        return (
            str(self.survey_key),
            int(self.tile_i),
            int(self.tile_j),
            round(float(self.origin_ra_deg), 6),
            round(float(self.origin_dec_deg), 6),
            round(float(self.fov_arcmin), 6),
            int(self.width_px),
            int(self.height_px),
            str(self.layer_id),
        )


@dataclass(frozen=True, slots=True)
class SurveyTileStfParameters:
    """Per-tile STF parameters computed only from that tile's valid pixels."""

    vmin: float
    vmax: float
    midtones_balance: float
    shadows_clip: float
    highlights_clip: float
    valid_pixel_fraction: float


@dataclass(frozen=True, slots=True)
class SurveyTileTiming:
    queue_ms: float = 0.0
    download_ms: float = 0.0
    process_ms: float = 0.0
    stf_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class SurveyTileLoadResult:
    session_id: int
    layer_id: str
    tile_key: SurveyTileKey
    resolution: SurveyTileResolution
    status: SurveyTileResultStatus
    image_data: np.ndarray | None
    display_rgba: np.ndarray | None
    stf_parameters: SurveyTileStfParameters | None
    valid_pixel_fraction: float
    error_message: str | None
    timing: SurveyTileTiming
    ra_deg: float
    dec_deg: float


@dataclass
class SurveyTileRecord:
    key: SurveyTileKey
    state: SurveyTileState = SurveyTileState.NOT_REQUESTED
    preview_image: np.ndarray | None = None
    refine_image: np.ndarray | None = None
    preview_display: np.ndarray | None = None
    refine_display: np.ndarray | None = None
    preview_stf: SurveyTileStfParameters | None = None
    refine_stf: SurveyTileStfParameters | None = None
    ra_deg: float = 0.0
    dec_deg: float = 0.0
    retry_count: int = 0
    last_error: str | None = None
    last_access_monotonic: float = field(default_factory=time.monotonic)
    request_generation: int = 0

    @property
    def display_rgba(self) -> np.ndarray | None:
        if self.refine_display is not None:
            return self.refine_display
        return self.preview_display

    @property
    def is_ready(self) -> bool:
        return self.display_rgba is not None and self.state in {
            SurveyTileState.LOW_RES_READY,
            SurveyTileState.QUEUED_HIGH_RES,
            SurveyTileState.LOADING_HIGH_RES,
            SurveyTileState.HIGH_RES_READY,
        }

    @property
    def is_terminal_no_data(self) -> bool:
        return self.state == SurveyTileState.NO_DATA

    @property
    def is_failed_final(self) -> bool:
        return self.state == SurveyTileState.FAILED_FINAL

    def touch(self) -> None:
        self.last_access_monotonic = time.monotonic()


@dataclass(frozen=True, slots=True)
class SurveyTileDrawItem:
    """GUI-thread draw payload for one tile (already stretched)."""

    tile_i: int
    tile_j: int
    x0: float
    y0: float
    width: float
    height: float
    display_rgba: np.ndarray | None
    state: SurveyTileState
    label: str = ""


def survey_tile_pixel_rect(
    tile_i: int,
    tile_j: int,
    *,
    width_px: int,
    height_px: int,
) -> tuple[float, float, float, float]:
    """
    Map tile indices to a stable image-pixel rectangle.

    +i (east / +RA) moves left because cdelt[0] < 0 on TAN survey plates.
    +j (north / +Dec) moves down in image y (FITS/image convention used by ImageView).
    Origin tile (0, 0) occupies [0, width) x [0, height).
    """
    width = float(max(1, int(width_px)))
    height = float(max(1, int(height_px)))
    x0 = -float(tile_i) * width
    y0 = float(tile_j) * height
    return x0, y0, width, height


def survey_tile_indices_for_image_point(
    image_x: float,
    image_y: float,
    *,
    width_px: int,
    height_px: int,
) -> tuple[int, int]:
    width = max(1, int(width_px))
    height = max(1, int(height_px))
    # Inverse of survey_tile_pixel_rect: +i moves left (negative x), +j moves +y.
    tile_i = -int(math.floor(float(image_x) / float(width)))
    tile_j = int(math.floor(float(image_y) / float(height)))
    return tile_i, tile_j


def is_survey_no_data_error(message: str) -> bool:
    text = str(message or "").strip().lower()
    return (
        "no coverage" in text
        or "no survey coverage" in text
        or "blank image" in text
        or "all finite pixels are zero" in text
        or SURVEY_TILE_NO_DATA_MARKER in text
    )


def compute_survey_tile_stf_parameters(image_data: np.ndarray) -> SurveyTileStfParameters:
    """Compute PixInsight-like STF parameters from one tile alone."""
    from photometry_app.core.plotting import (
        _adaptive_display_function_parameters,
        _annotated_image_display_limits,
        _auto_stretch_source_normalized_data,
    )

    pixels = _normalized_tile_array(image_data)
    if pixels.ndim == 3:
        plane = np.mean(pixels[..., : min(3, pixels.shape[-1])], axis=-1)
    else:
        plane = pixels
    finite = plane[np.isfinite(plane)]
    if finite.size < SURVEY_TILE_MIN_VALID_PIXELS:
        return SurveyTileStfParameters(
            vmin=0.0,
            vmax=1.0,
            midtones_balance=0.5,
            shadows_clip=0.0,
            highlights_clip=1.0,
            valid_pixel_fraction=0.0,
        )
    # Exclude exact zeros that are typical reprojection/fill borders when mixed with signal.
    signal = finite[finite != 0.0]
    stats_source = signal if signal.size >= SURVEY_TILE_MIN_VALID_PIXELS else finite
    vmin = float(np.nanpercentile(stats_source, SURVEY_TILE_STF_LOWER_PERCENTILE))
    vmax = float(np.nanpercentile(stats_source, SURVEY_TILE_STF_UPPER_PERCENTILE))
    if not np.isfinite(vmin) or not np.isfinite(vmax) or (vmax - vmin) < SURVEY_TILE_STF_MIN_DYNAMIC_RANGE:
        limits_vmin, limits_vmax = _annotated_image_display_limits(plane)
        vmin = float(limits_vmin)
        vmax = float(limits_vmax)
    if not np.isfinite(vmin) or not np.isfinite(vmax) or (vmax - vmin) < SURVEY_TILE_STF_MIN_DYNAMIC_RANGE:
        return SurveyTileStfParameters(
            vmin=0.0,
            vmax=1.0,
            midtones_balance=0.5,
            shadows_clip=0.0,
            highlights_clip=1.0,
            valid_pixel_fraction=float(finite.size) / float(max(1, plane.size)),
        )
    normalized = np.clip((plane - vmin) / (vmax - vmin), 0.0, 1.0).astype(np.float32, copy=False)
    # Mask fill zeros after normalization so they do not dominate median/MAD.
    if signal.size >= SURVEY_TILE_MIN_VALID_PIXELS:
        stats_plane = normalized.copy()
        stats_plane[plane == 0.0] = np.nan
        auto_source = _auto_stretch_source_normalized_data(np.nan_to_num(stats_plane, nan=np.nanmedian(normalized)))
    else:
        auto_source = _auto_stretch_source_normalized_data(normalized)
    midtones, shadows, highlights = _adaptive_display_function_parameters(auto_source)
    # Keep stretch bright but bounded: avoid near-zero shadows that blow noise to white.
    shadows = float(np.clip(shadows, 0.0, 0.35))
    highlights = float(np.clip(highlights, max(shadows + 0.05, 0.55), 1.0))
    midtones = float(np.clip(midtones, 0.05, 0.95))
    return SurveyTileStfParameters(
        vmin=vmin,
        vmax=vmax,
        midtones_balance=midtones,
        shadows_clip=shadows,
        highlights_clip=highlights,
        valid_pixel_fraction=float(finite.size) / float(max(1, plane.size)),
    )


def render_survey_tile_display_rgba(
    image_data: np.ndarray,
    stf: SurveyTileStfParameters,
) -> np.ndarray:
    """Apply stored per-tile STF and return contiguous HxWx4 uint8 RGBA."""
    from photometry_app.core.plotting import _apply_display_function

    pixels = _normalized_tile_array(image_data)
    color = pixels.ndim == 3 and pixels.shape[-1] >= 3
    if color:
        working = pixels[..., :3].astype(np.float32, copy=False)
    else:
        working = pixels.astype(np.float32, copy=False)
    span = max(SURVEY_TILE_STF_MIN_DYNAMIC_RANGE, float(stf.vmax) - float(stf.vmin))
    normalized = np.clip((working - float(stf.vmin)) / span, 0.0, 1.0)
    stretched = _apply_display_function(
        normalized,
        midtones_balance=float(stf.midtones_balance),
        shadows_clip=float(stf.shadows_clip),
        highlights_clip=float(stf.highlights_clip),
    )
    rgb8 = np.ascontiguousarray(np.clip(stretched * 255.0, 0.0, 255.0).astype(np.uint8))
    if rgb8.ndim == 2:
        rgb8 = np.repeat(rgb8[:, :, None], 3, axis=2)
    alpha = np.full(rgb8.shape[:2], 255, dtype=np.uint8)
    # Transparent where source was non-finite so seams do not paint opaque black.
    if color:
        valid = np.isfinite(pixels[..., :3]).all(axis=-1)
    else:
        valid = np.isfinite(pixels)
    alpha = np.where(valid, alpha, np.uint8(0))
    return np.ascontiguousarray(np.dstack([rgb8[..., 0], rgb8[..., 1], rgb8[..., 2], alpha]))


def make_survey_tile_placeholder_rgba(
    *,
    width: int,
    height: int,
    state: SurveyTileState,
) -> np.ndarray:
    """Distinct loading / no-data / error patterns (not plain black)."""
    width = max(1, int(width))
    height = max(1, int(height))
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    yy, xx = np.indices((height, width))
    if state == SurveyTileState.NO_DATA:
        # Dark diagonal hatch.
        band = ((xx + yy) // 12) % 2 == 0
        rgba[..., 0] = np.where(band, 42, 28)
        rgba[..., 1] = np.where(band, 46, 30)
        rgba[..., 2] = np.where(band, 52, 34)
        rgba[..., 3] = 220
    elif state in {SurveyTileState.FAILED_FINAL, SurveyTileState.FAILED_RETRYABLE}:
        band = ((xx // 10) % 2) == ((yy // 10) % 2)
        rgba[..., 0] = np.where(band, 72, 40)
        rgba[..., 1] = np.where(band, 36, 24)
        rgba[..., 2] = np.where(band, 24, 20)
        rgba[..., 3] = 220
    else:
        # Loading: muted vertical stripe.
        band = (xx // 16) % 2 == 0
        rgba[..., 0] = np.where(band, 24, 18)
        rgba[..., 1] = np.where(band, 28, 20)
        rgba[..., 2] = np.where(band, 36, 26)
        rgba[..., 3] = 200
    return np.ascontiguousarray(rgba)


def survey_tile_request_priority(
    *,
    tile_i: int,
    tile_j: int,
    view_i: int,
    view_j: int,
    resolution: SurveyTileResolution,
    pan_di: float = 0.0,
    pan_dj: float = 0.0,
) -> float:
    """
    Lower score = higher priority.

    Order: visible low → visible high → adjacent low → adjacent high → distant prefetch.
    Prefer tiles ahead of the pan direction.
    """
    chebyshev = max(abs(int(tile_i) - int(view_i)), abs(int(tile_j) - int(view_j)))
    euclidean = math.hypot(float(tile_i - view_i), float(tile_j - view_j))
    ahead = 0.0
    if pan_di or pan_dj:
        ahead = -0.35 * (
            float(tile_i - view_i) * float(pan_di) + float(tile_j - view_j) * float(pan_dj)
        )
    res_bias = 0.0 if resolution is SurveyTileResolution.PREVIEW else 0.55
    ring_bias = 0.0
    if chebyshev == 0:
        ring_bias = 0.0
    elif chebyshev == 1:
        ring_bias = 2.0
    else:
        ring_bias = 4.0 + float(chebyshev)
    return ring_bias + res_bias + 0.05 * euclidean + ahead


def initial_survey_tile_work_order(
    center_i: int = 0,
    center_j: int = 0,
    *,
    radius: int = SKY_EXPLORER_SURVEY_FIELD_MOSAIC_RADIUS,
) -> tuple[tuple[int, int, SurveyTileResolution], ...]:
    """Center low → center high → neighbor lows → neighbor highs."""
    ordered = sky_explorer_survey_field_neighbor_tile_indices(center_i, center_j, radius=radius)
    work: list[tuple[int, int, SurveyTileResolution]] = []
    if ordered:
        work.append((ordered[0][0], ordered[0][1], SurveyTileResolution.PREVIEW))
        work.append((ordered[0][0], ordered[0][1], SurveyTileResolution.REFINE))
        for tile_i, tile_j in ordered[1:]:
            work.append((tile_i, tile_j, SurveyTileResolution.PREVIEW))
        for tile_i, tile_j in ordered[1:]:
            work.append((tile_i, tile_j, SurveyTileResolution.REFINE))
    return tuple(work)


def build_viewport_survey_tile_work(
    *,
    view_i: int,
    view_j: int,
    records: Mapping[tuple[int, int], SurveyTileRecord],
    radius: int = SKY_EXPLORER_SURVEY_FIELD_MOSAIC_RADIUS,
    pan_di: float = 0.0,
    pan_dj: float = 0.0,
) -> list[tuple[float, int, int, SurveyTileResolution]]:
    """Return prioritized missing work for the current viewport ring."""
    candidates: list[tuple[float, int, int, SurveyTileResolution]] = []
    for tile_i, tile_j in sky_explorer_survey_field_neighbor_tile_indices(view_i, view_j, radius=radius):
        record = records.get((tile_i, tile_j))
        for resolution in (SurveyTileResolution.PREVIEW, SurveyTileResolution.REFINE):
            if not _tile_needs_resolution(record, resolution):
                continue
            score = survey_tile_request_priority(
                tile_i=tile_i,
                tile_j=tile_j,
                view_i=view_i,
                view_j=view_j,
                resolution=resolution,
                pan_di=pan_di,
                pan_dj=pan_dj,
            )
            candidates.append((score, tile_i, tile_j, resolution))
    candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3].value))
    return candidates


def _tile_needs_resolution(record: SurveyTileRecord | None, resolution: SurveyTileResolution) -> bool:
    if record is None:
        return True
    if record.state in {SurveyTileState.NO_DATA, SurveyTileState.FAILED_FINAL, SurveyTileState.CANCELLED}:
        return False
    if resolution is SurveyTileResolution.PREVIEW:
        if record.preview_display is not None or record.refine_display is not None:
            return False
        return record.state in {
            SurveyTileState.NOT_REQUESTED,
            SurveyTileState.FAILED_RETRYABLE,
        }
    # High-res
    if record.refine_display is not None or record.state == SurveyTileState.HIGH_RES_READY:
        return False
    if record.state in {
        SurveyTileState.QUEUED_HIGH_RES,
        SurveyTileState.LOADING_HIGH_RES,
        SurveyTileState.QUEUED_LOW_RES,
        SurveyTileState.LOADING_LOW_RES,
    }:
        return False
    # Prefer finishing lows before queueing highs for non-visible tiles; caller orders by priority.
    if record.state == SurveyTileState.LOW_RES_READY:
        return True
    if record.state == SurveyTileState.NOT_REQUESTED:
        # Visible missing tile: low will be queued first due to priority bias.
        return True
    if record.state == SurveyTileState.FAILED_RETRYABLE and record.preview_display is not None:
        return True
    return False


def survey_tile_fetch_size(
    *,
    width_px: int,
    height_px: int,
    resolution: SurveyTileResolution,
) -> tuple[int, int]:
    if resolution is SurveyTileResolution.PREVIEW:
        return sky_explorer_survey_field_preview_size(
            width_px,
            height_px,
            max_edge=SKY_EXPLORER_SURVEY_FIELD_PREVIEW_MAX_EDGE,
        )
    return max(1, int(width_px)), max(1, int(height_px))


def make_survey_tile_key(
    *,
    survey_key: str,
    tile_i: int,
    tile_j: int,
    origin_ra_deg: float,
    origin_dec_deg: float,
    fov_arcmin: float,
    width_px: int,
    height_px: int,
    layer_id: str = "primary",
) -> SurveyTileKey:
    return SurveyTileKey(
        survey_key=str(survey_key).strip().lower(),
        tile_i=int(tile_i),
        tile_j=int(tile_j),
        origin_ra_deg=float(origin_ra_deg),
        origin_dec_deg=float(origin_dec_deg),
        fov_arcmin=float(fov_arcmin),
        width_px=int(width_px),
        height_px=int(height_px),
        layer_id=str(layer_id),
    )


def survey_tile_sky_center(
    key: SurveyTileKey,
) -> tuple[float, float]:
    spec = sky_explorer_survey_field_tile_spec(
        origin_ra_deg=key.origin_ra_deg,
        origin_dec_deg=key.origin_dec_deg,
        fov_arcmin=key.fov_arcmin,
        tile_i=key.tile_i,
        tile_j=key.tile_j,
    )
    return float(spec.ra_deg), float(spec.dec_deg)


def _normalized_tile_array(image_data: np.ndarray) -> np.ndarray:
    pixels = np.asarray(image_data, dtype=np.float32)
    if pixels.ndim == 3 and pixels.shape[0] in {3, 4} and pixels.shape[-1] not in {3, 4}:
        pixels = np.moveaxis(pixels[:3], 0, -1)
    return pixels
