from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from types import MappingProxyType
from typing import Any

from astroquery.hips2fits import hips2fits
from astropy.wcs import WCS
import numpy as np


_CACHE_DIRECTORY_NAME = "sky-explorer-surveys"
SKY_EXPLORER_SURVEY_CACHE_DIR_NAME = _CACHE_DIRECTORY_NAME
SKY_EXPLORER_SURVEY_FIELD_CACHE_DIR_NAME = "sky-explorer-survey-fields"
_CACHE_FORMAT_VERSION = 1
_MAX_CACHE_FILES = 64

# Survey-as-primary mosaic: ~3x3 tiles (~9x FOV area), hard-capped near 10x.
SKY_EXPLORER_SURVEY_FIELD_MOSAIC_RADIUS = 1
SKY_EXPLORER_SURVEY_FIELD_MAX_CACHED_TILES = 10
SKY_EXPLORER_SURVEY_FIELD_PREVIEW_MAX_EDGE = 256
SKY_EXPLORER_SURVEY_FIELD_DETAIL_PREVIEW = "preview"
SKY_EXPLORER_SURVEY_FIELD_DETAIL_REFINE = "refine"


@dataclass(frozen=True, slots=True)
class SurveyDefinition:
    key: str
    title: str
    hips_id: str

    @property
    def survey_id(self) -> str:
        return self.hips_id


SURVEY_DEFINITIONS: tuple[SurveyDefinition, ...] = (
    SurveyDefinition("dss2_blue", "DSS2 Blue", "CDS/P/DSS2/blue"),
    SurveyDefinition("shs_ha", "SHS Ha", "CDS/P/SHS"),
    SurveyDefinition("panstarrs", "PanSTARRS", "CDS/P/PanSTARRS/DR1/color-i-r-g"),
    SurveyDefinition("iphas_dr2_ha", "IPHAS DR2 Ha", "CDS/P/IPHAS/DR2/Ha"),
)
SURVEY_IMAGE_DEFINITIONS = SURVEY_DEFINITIONS
SURVEY_DEFINITIONS_BY_KEY: Mapping[str, SurveyDefinition] = MappingProxyType(
    {definition.key: definition for definition in SURVEY_DEFINITIONS}
)


def survey_definition_for_key(survey_key: str) -> SurveyDefinition:
    normalized_key = str(survey_key or "").strip().lower().replace("-", "_").replace(" ", "_")
    try:
        return SURVEY_DEFINITIONS_BY_KEY[normalized_key]
    except KeyError as exc:
        available = ", ".join(definition.key for definition in SURVEY_DEFINITIONS)
        raise ValueError(f"Unknown survey key {survey_key!r}; expected one of: {available}.") from exc


def get_survey_definition(survey_key: str) -> SurveyDefinition:
    return survey_definition_for_key(survey_key)


@dataclass(frozen=True, slots=True)
class SurveyImageRequest:
    survey_key: str
    wcs: WCS
    width: int
    height: int
    target_rect: tuple[float, float, float, float]
    cache_dir: Path
    progress_callback: Callable[[str], None] | None = None

    @property
    def target_rectangle(self) -> tuple[float, float, float, float]:
        return self.target_rect


@dataclass(frozen=True, slots=True)
class SurveyImageResult:
    survey: SurveyDefinition
    image_data: np.ndarray
    target_rect: tuple[float, float, float, float]
    loaded_from_cache: bool

    @property
    def data(self) -> np.ndarray:
        return self.image_data

    @property
    def target_rectangle(self) -> tuple[float, float, float, float]:
        return self.target_rect

    @property
    def from_cache(self) -> bool:
        return self.loaded_from_cache


def retrieve_survey_image(request: SurveyImageRequest) -> SurveyImageResult:
    survey = survey_definition_for_key(request.survey_key)
    width, height, target_rect = _validated_request_geometry(request)
    query_wcs = _query_wcs(request.wcs, width=width, height=height)
    wcs_header = _serialized_wcs_header(query_wcs)
    cache_metadata = _cache_metadata(
        survey=survey,
        wcs_header=wcs_header,
        width=width,
        height=height,
        target_rect=target_rect,
    )
    cache_root = Path(request.cache_dir).expanduser() / _CACHE_DIRECTORY_NAME
    cache_path = cache_root / f"{_cache_key(cache_metadata)}.npz"

    cached_data = _load_cached_image(cache_path, cache_metadata, width=width, height=height)
    if cached_data is not None:
        _emit_progress(request.progress_callback, f"Loaded cached {survey.title} survey image.")
        return SurveyImageResult(survey, cached_data, target_rect, True)

    _emit_progress(request.progress_callback, f"Querying CDS hips2fits for {survey.title}.")
    response = _query_hips2fits(survey, query_wcs)
    try:
        image_data = _image_data_from_response(response, width=width, height=height)
    finally:
        close_response = getattr(response, "close", None)
        if callable(close_response):
            close_response()

    _store_cached_image(cache_path, cache_metadata, image_data)
    _prune_cache(cache_root)
    _emit_progress(request.progress_callback, f"Downloaded {survey.title} survey image.")
    return SurveyImageResult(survey, image_data, target_rect, False)


def fetch_survey_image(request: SurveyImageRequest) -> SurveyImageResult:
    return retrieve_survey_image(request)


def sky_explorer_survey_image_cache_dirs(cache_dir: Path | str) -> tuple[Path, Path]:
    """Return (hips cutout cache, survey-field WCS canvas cache) under settings.cache_dir."""
    root = Path(cache_dir).expanduser()
    return (
        root / SKY_EXPLORER_SURVEY_CACHE_DIR_NAME,
        root / SKY_EXPLORER_SURVEY_FIELD_CACHE_DIR_NAME,
    )


def clear_sky_explorer_survey_image_caches(cache_dir: Path | str | None) -> int:
    """
    Delete on-disk Sky Explorer survey image caches.

    Survey downloads are session-only: they speed up pans within a run, but are
    removed when the app closes (and again on startup if a previous exit left them).
    Returns the number of top-level cache directories removed.
    """
    if cache_dir is None:
        return 0
    import shutil

    removed = 0
    for path in sky_explorer_survey_image_cache_dirs(cache_dir):
        if not path.exists():
            continue
        try:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=False)
            else:
                path.unlink(missing_ok=True)
            removed += 1
        except OSError:
            # Best-effort cleanup; leave whatever Windows still has locked.
            try:
                shutil.rmtree(path, ignore_errors=True)
                if not path.exists():
                    removed += 1
            except Exception:
                pass
    return removed


def build_sky_explorer_field_wcs(
    *,
    ra_deg: float,
    dec_deg: float,
    fov_arcmin: float,
    width_px: int,
    height_px: int,
) -> WCS:
    """Build a simple TAN WCS covering the requested field of view."""
    width = max(1, int(width_px))
    height = max(1, int(height_px))
    fov_deg = max(1.0e-6, float(fov_arcmin) / 60.0)
    scale_deg = fov_deg / float(max(width, height))
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.crval = [float(ra_deg) % 360.0, float(dec_deg)]
    wcs.wcs.crpix = [(width + 1) * 0.5, (height + 1) * 0.5]
    wcs.wcs.cdelt = [-scale_deg, scale_deg]
    wcs.wcs.cunit = ["deg", "deg"]
    wcs.pixel_shape = (width, height)
    wcs.array_shape = (height, width)
    return wcs


def write_survey_image_fits(
    output_path: Path,
    *,
    image_data: np.ndarray,
    wcs: WCS,
) -> Path:
    """Persist a survey cutout as a FITS file usable as a Sky Explorer source image."""
    from astropy.io import fits

    destination = Path(output_path).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    pixels = np.asarray(image_data)
    if pixels.ndim == 3 and pixels.shape[0] in {3, 4} and pixels.shape[-1] not in {3, 4}:
        # Channel-first RGB → keep as (planes, y, x) for FITS.
        data = np.asarray(pixels[:3], dtype=np.float32)
    elif pixels.ndim == 3 and pixels.shape[-1] in {3, 4}:
        data = np.asarray(np.moveaxis(pixels[..., :3], -1, 0), dtype=np.float32)
    else:
        data = np.asarray(pixels, dtype=np.float32)
    header = wcs.to_header(relax=True)
    fits.PrimaryHDU(data=data, header=header).writeto(destination, overwrite=True)
    return destination


@dataclass(frozen=True, slots=True)
class SurveyFieldTileSpec:
    tile_i: int
    tile_j: int
    ra_deg: float
    dec_deg: float


@dataclass(frozen=True, slots=True)
class SurveyFieldTileCacheEntry:
    image_data: np.ndarray
    detail_tier: str

    @property
    def is_refine(self) -> bool:
        return str(self.detail_tier) == SKY_EXPLORER_SURVEY_FIELD_DETAIL_REFINE


def sky_explorer_survey_field_preview_size(
    width_px: int,
    height_px: int,
    *,
    max_edge: int = SKY_EXPLORER_SURVEY_FIELD_PREVIEW_MAX_EDGE,
) -> tuple[int, int]:
    """Return a fast preview pixel size that preserves aspect ratio."""
    width = max(1, int(width_px))
    height = max(1, int(height_px))
    edge = max(32, int(max_edge))
    long_edge = max(width, height)
    if long_edge <= edge:
        return width, height
    scale = float(edge) / float(long_edge)
    return max(1, int(round(width * scale))), max(1, int(round(height * scale)))


def sky_explorer_survey_field_tile_step_deg(
    *,
    fov_arcmin: float,
    origin_dec_deg: float,
) -> tuple[float, float]:
    """Return (ra_step_deg eastward, dec_step_deg northward) for one survey field tile."""
    fov_deg = max(1.0e-6, float(fov_arcmin) / 60.0)
    # Match spherical RA from TAN pixel_to_world at tile edges (≈ fov / cos(dec)).
    cos_dec = max(0.05, abs(math.cos(math.radians(float(origin_dec_deg)))))
    return fov_deg / cos_dec, fov_deg


def sky_explorer_survey_field_tile_spec(
    *,
    origin_ra_deg: float,
    origin_dec_deg: float,
    fov_arcmin: float,
    tile_i: int,
    tile_j: int,
    width_px: int | None = None,
    height_px: int | None = None,
) -> SurveyFieldTileSpec:
    """Map integer tile indices to a sky center. +i is east (+RA), +j is north (+Dec)."""
    del width_px, height_px  # optional; step is FOV-based for square TAN plates
    ra_step, dec_step = sky_explorer_survey_field_tile_step_deg(
        fov_arcmin=fov_arcmin,
        origin_dec_deg=origin_dec_deg,
    )
    ra_deg = (float(origin_ra_deg) + float(tile_i) * ra_step) % 360.0
    dec_deg = min(90.0, max(-90.0, float(origin_dec_deg) + float(tile_j) * dec_step))
    return SurveyFieldTileSpec(int(tile_i), int(tile_j), ra_deg, dec_deg)


def sky_explorer_survey_field_tile_indices_for_sky(
    *,
    ra_deg: float,
    dec_deg: float,
    origin_ra_deg: float,
    origin_dec_deg: float,
    fov_arcmin: float,
    width_px: int | None = None,
    height_px: int | None = None,
) -> tuple[int, int]:
    """Nearest tile indices for a sky position relative to the mosaic origin."""
    del width_px, height_px
    ra_step, dec_step = sky_explorer_survey_field_tile_step_deg(
        fov_arcmin=fov_arcmin,
        origin_dec_deg=origin_dec_deg,
    )
    delta_ra = ((float(ra_deg) - float(origin_ra_deg) + 180.0) % 360.0) - 180.0
    tile_i = int(round(delta_ra / ra_step))
    tile_j = int(round((float(dec_deg) - float(origin_dec_deg)) / dec_step))
    return tile_i, tile_j


def sky_explorer_survey_field_neighbor_tile_indices(
    center_i: int,
    center_j: int,
    *,
    radius: int = SKY_EXPLORER_SURVEY_FIELD_MOSAIC_RADIUS,
) -> tuple[tuple[int, int], ...]:
    """Return mosaic tile indices with the center first, then surrounding ring (Aladin-style)."""
    radius = max(0, int(radius))
    ordered: list[tuple[int, int]] = [(int(center_i), int(center_j))]
    for ring in range(1, radius + 1):
        ring_tiles: list[tuple[int, int, float]] = []
        for tile_i in range(center_i - ring, center_i + ring + 1):
            for tile_j in range(center_j - ring, center_j + ring + 1):
                if max(abs(tile_i - center_i), abs(tile_j - center_j)) != ring:
                    continue
                distance = math.hypot(float(tile_i - center_i), float(tile_j - center_j))
                ring_tiles.append((tile_i, tile_j, distance))
        ring_tiles.sort(key=lambda item: (item[2], item[0], item[1]))
        ordered.extend((tile_i, tile_j) for tile_i, tile_j, _distance in ring_tiles)
    return tuple(ordered)


def prune_sky_explorer_survey_field_tiles(
    tiles: Mapping[tuple[int, int], SurveyFieldTileCacheEntry | np.ndarray],
    *,
    center_i: int,
    center_j: int,
    max_tiles: int = SKY_EXPLORER_SURVEY_FIELD_MAX_CACHED_TILES,
) -> dict[tuple[int, int], SurveyFieldTileCacheEntry]:
    """Keep at most max_tiles, preferring those nearest the current mosaic center."""
    limit = max(1, int(max_tiles))
    normalized = {
        key: _coerce_survey_field_tile_entry(value)
        for key, value in tiles.items()
    }
    if len(normalized) <= limit:
        return normalized
    ranked = sorted(
        normalized.items(),
        key=lambda item: (
            0 if item[1].is_refine else 1,
            math.hypot(float(item[0][0] - center_i), float(item[0][1] - center_j)),
            item[0][0],
            item[0][1],
        ),
    )
    return {key: value for key, value in ranked[:limit]}


def compose_sky_explorer_survey_field_mosaic(
    tiles: Mapping[tuple[int, int], SurveyFieldTileCacheEntry | np.ndarray],
    *,
    center_i: int,
    center_j: int,
    origin_ra_deg: float,
    origin_dec_deg: float,
    fov_arcmin: float,
    width_px: int,
    height_px: int,
    radius: int = SKY_EXPLORER_SURVEY_FIELD_MOSAIC_RADIUS,
) -> tuple[np.ndarray, WCS]:
    """
    Compose a TAN mosaic around (center_i, center_j).

    Missing tiles stay black/zero. Callers should freeze display stretch from the first
    loaded tile so empty cells do not wash the sky when auto-levels would otherwise
    see a near-zero histogram. Preview tiles are upsampled into their mosaic slots.
    """
    radius = max(0, int(radius))
    width = max(1, int(width_px))
    height = max(1, int(height_px))
    cells = 2 * radius + 1
    mosaic_width = cells * width
    mosaic_height = cells * height
    center_spec = sky_explorer_survey_field_tile_spec(
        origin_ra_deg=origin_ra_deg,
        origin_dec_deg=origin_dec_deg,
        fov_arcmin=fov_arcmin,
        tile_i=center_i,
        tile_j=center_j,
        width_px=width,
        height_px=height,
    )
    normalized_tiles = {
        key: _coerce_survey_field_tile_entry(value)
        for key, value in tiles.items()
    }
    sample_entry = next(iter(normalized_tiles.values()), None)
    sample = None if sample_entry is None else np.asarray(sample_entry.image_data)
    color = sample is not None and sample.ndim == 3 and sample.shape[-1] >= 3
    if color:
        mosaic = np.zeros((mosaic_height, mosaic_width, 3), dtype=np.float32)
    else:
        mosaic = np.zeros((mosaic_height, mosaic_width), dtype=np.float32)

    for tile_i in range(center_i - radius, center_i + radius + 1):
        for tile_j in range(center_j - radius, center_j + radius + 1):
            entry = normalized_tiles.get((tile_i, tile_j))
            if entry is None:
                continue
            pixels = resize_survey_field_tile_pixels(entry.image_data, width=width, height=height)
            di = tile_i - center_i
            dj = tile_j - center_j
            # cdelt[0] < 0: +RA (east) moves left on the mosaic.
            x0 = (radius - di) * width
            y0 = (radius + dj) * height
            if pixels.ndim == 3:
                if mosaic.ndim == 2:
                    mosaic = np.repeat(mosaic[:, :, None], 3, axis=2)
                mosaic[y0 : y0 + height, x0 : x0 + width, :3] = pixels[..., :3]
            else:
                if mosaic.ndim == 3:
                    mosaic[y0 : y0 + height, x0 : x0 + width, :] = pixels[:, :, None]
                else:
                    mosaic[y0 : y0 + height, x0 : x0 + width] = pixels

    mosaic_wcs = build_sky_explorer_field_wcs(
        ra_deg=center_spec.ra_deg,
        dec_deg=center_spec.dec_deg,
        fov_arcmin=float(fov_arcmin) * float(cells),
        width_px=mosaic_width,
        height_px=mosaic_height,
    )
    return mosaic, mosaic_wcs


def sky_explorer_survey_field_frozen_levels(
    image_data: np.ndarray,
) -> tuple[float, float, float]:
    """Return non-default black/mid/white levels from one tile for frozen mosaic stretch."""
    pixels = _normalized_survey_field_tile_array(image_data)
    if pixels.ndim == 3:
        plane = np.mean(pixels[..., : min(3, pixels.shape[-1])], axis=-1)
    else:
        plane = pixels
    finite = plane[np.isfinite(plane)]
    if finite.size < 16:
        return (0.01, 0.5, 1.0)
    vmin = float(np.nanpercentile(finite, 0.5))
    vmax = float(np.nanpercentile(finite, 99.8))
    if not np.isfinite(vmin) or not np.isfinite(vmax) or vmax <= vmin:
        return (0.01, 0.5, 1.0)
    normalized = np.clip((finite - vmin) / (vmax - vmin), 0.0, 1.0)
    black = float(np.nanpercentile(normalized, 1.0))
    white = float(np.nanpercentile(normalized, 99.5))
    mid = float(np.nanpercentile(normalized, 50.0))
    black = max(0.0, min(0.45, black))
    white = max(black + 0.05, min(1.0, white))
    mid = max(black + 0.01, min(white - 0.01, mid))
    # Nudge off the UI default so STF auto-stretch is not re-enabled.
    if abs(black) <= 1e-6 and abs(mid - 0.5) <= 1e-6 and abs(white - 1.0) <= 1e-6:
        black = 0.01
    return (black, mid, white)


def resize_survey_field_tile_pixels(
    image_data: np.ndarray,
    *,
    width: int,
    height: int,
) -> np.ndarray:
    """Normalize and resize a survey tile into the mosaic cell size."""
    pixels = _normalized_survey_field_tile_array(image_data)
    width = max(1, int(width))
    height = max(1, int(height))
    if pixels.shape[0] == height and pixels.shape[1] == width:
        return np.asarray(pixels, dtype=np.float32)
    try:
        from scipy import ndimage
    except Exception as exc:  # pragma: no cover - scipy is a project dependency
        raise RuntimeError("Survey field tile resizing requires scipy.ndimage.") from exc
    zoom_y = float(height) / float(pixels.shape[0])
    zoom_x = float(width) / float(pixels.shape[1])
    if pixels.ndim == 2:
        resized = ndimage.zoom(pixels, (zoom_y, zoom_x), order=1)
    else:
        resized = ndimage.zoom(pixels, (zoom_y, zoom_x, 1.0), order=1)
    return np.asarray(resized, dtype=np.float32)


def _coerce_survey_field_tile_entry(
    value: SurveyFieldTileCacheEntry | np.ndarray,
) -> SurveyFieldTileCacheEntry:
    if isinstance(value, SurveyFieldTileCacheEntry):
        return value
    return SurveyFieldTileCacheEntry(
        image_data=np.asarray(value),
        detail_tier=SKY_EXPLORER_SURVEY_FIELD_DETAIL_REFINE,
    )


def _normalized_survey_field_tile_array(image_data: np.ndarray) -> np.ndarray:
    pixels = np.asarray(image_data, dtype=np.float32)
    if pixels.ndim == 3 and pixels.shape[0] in {3, 4} and pixels.shape[-1] not in {3, 4}:
        pixels = np.moveaxis(pixels[:3], 0, -1)
    elif pixels.ndim == 3 and pixels.shape[-1] >= 3:
        pixels = pixels[..., :3]
    elif pixels.ndim != 2:
        raise ValueError(f"Unsupported survey tile shape {pixels.shape}.")
    return pixels


def _normalized_survey_field_tile_pixels(
    image_data: np.ndarray,
    *,
    width: int,
    height: int,
) -> np.ndarray:
    return resize_survey_field_tile_pixels(image_data, width=width, height=height)


def scale_wcs_for_pixel_sampling(wcs: WCS, sampling_step: int) -> WCS:
    """Expand a sliced WCS pixel scale when native pixels were subsampled."""
    step = int(sampling_step)
    if step <= 1:
        return wcs
    if not wcs.wcs.has_cd():
        return wcs
    scaled = wcs.deepcopy()
    scaled.wcs.cd = np.asarray(scaled.wcs.cd, dtype=float) * float(step)
    return scaled


def survey_target_rect_in_source_pixels(
    source_wcs: WCS,
    viewport_wcs: WCS,
    *,
    output_width: int,
    output_height: int,
) -> tuple[float, float, float, float]:
    """Map survey output corners back onto the source-image pixel grid."""
    if output_width <= 0 or output_height <= 0:
        raise ValueError("Survey output width and height must be positive.")
    corner_points = (
        (0.0, 0.0),
        (float(output_width), 0.0),
        (0.0, float(output_height)),
        (float(output_width), float(output_height)),
    )
    source_x_values: list[float] = []
    source_y_values: list[float] = []
    for output_x, output_y in corner_points:
        world_x, world_y = viewport_wcs.pixel_to_world_values(output_x, output_y)
        source_x, source_y = source_wcs.world_to_pixel_values(world_x, world_y)
        source_x_values.append(float(source_x))
        source_y_values.append(float(source_y))
    left = min(source_x_values)
    top = min(source_y_values)
    right = max(source_x_values)
    bottom = max(source_y_values)
    width = right - left
    height = bottom - top
    if width <= 0.0 or height <= 0.0:
        raise ValueError("Survey target rectangle has non-positive size.")
    return left, top, width, height


def _validated_request_geometry(
    request: SurveyImageRequest,
) -> tuple[int, int, tuple[float, float, float, float]]:
    if isinstance(request.width, bool) or isinstance(request.height, bool):
        raise ValueError("Survey image width and height must be positive integers.")
    try:
        width = int(request.width)
        height = int(request.height)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Survey image width and height must be positive integers.") from exc
    if width != request.width or height != request.height or width <= 0 or height <= 0:
        raise ValueError("Survey image width and height must be positive integers.")

    try:
        target_rect = tuple(float(value) for value in request.target_rect)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Survey image target_rect must contain four finite numeric values.") from exc
    if len(target_rect) != 4 or not np.all(np.isfinite(target_rect)):
        raise ValueError("Survey image target_rect must contain four finite numeric values.")
    if target_rect[2] <= 0.0 or target_rect[3] <= 0.0:
        raise ValueError("Survey image target_rect width and height must be positive.")
    return width, height, target_rect


def _query_wcs(source_wcs: WCS, *, width: int, height: int) -> WCS:
    if not isinstance(source_wcs, WCS):
        raise ValueError("Survey image requests require an astropy WCS.")
    if not source_wcs.has_celestial:
        raise ValueError("Survey image requests require a celestial astropy WCS.")
    query_wcs = source_wcs.celestial.deepcopy()
    if query_wcs.pixel_n_dim != 2 or query_wcs.world_n_dim != 2:
        raise ValueError("Survey image requests require a two-dimensional celestial WCS.")
    query_wcs.array_shape = (height, width)
    if query_wcs.array_shape != (height, width) or query_wcs.pixel_shape != (width, height):
        raise ValueError("Could not apply the requested pixel shape to the survey WCS.")
    return query_wcs


def _serialized_wcs_header(query_wcs: WCS) -> str:
    return query_wcs.to_header(relax=True).tostring(sep="\n", endcard=False, padding=False)


def _cache_metadata(
    *,
    survey: SurveyDefinition,
    wcs_header: str,
    width: int,
    height: int,
    target_rect: tuple[float, float, float, float],
) -> dict[str, Any]:
    return {
        "version": _CACHE_FORMAT_VERSION,
        "survey_key": survey.key,
        "survey_id": survey.hips_id,
        "wcs_header": wcs_header,
        "width": width,
        "height": height,
        "target_rect": list(target_rect),
    }


def _cache_key(metadata: Mapping[str, Any]) -> str:
    serialized = json.dumps(metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _query_hips2fits(survey: SurveyDefinition, query_wcs: WCS) -> Any:
    return hips2fits.query_with_wcs(hips=survey.hips_id, wcs=query_wcs, format="fits")


def _image_data_from_response(response: Any, *, width: int, height: int) -> np.ndarray:
    if response is None:
        raise ValueError("CDS hips2fits returned no image response.")

    candidates: list[Any] = []
    direct_data = getattr(response, "data", None)
    if direct_data is not None:
        candidates.append(direct_data)
    else:
        try:
            hdus = iter(response)
        except TypeError:
            hdus = iter(())
        for hdu in hdus:
            hdu_data = getattr(hdu, "data", None)
            if hdu_data is not None:
                candidates.append(hdu_data)

    if not candidates:
        raise ValueError("CDS hips2fits returned a non-image response with no FITS image data.")

    image_errors: list[ValueError] = []
    for candidate in candidates:
        try:
            return _validated_image_data(candidate, width=width, height=height)
        except ValueError as exc:
            image_errors.append(exc)
    raise image_errors[-1]


def _validated_image_data(data: Any, *, width: int, height: int) -> np.ndarray:
    try:
        image_data = np.asanyarray(data)
    except Exception as exc:
        raise ValueError("CDS hips2fits returned data that is not a usable numeric image.") from exc
    if image_data.size == 0:
        raise ValueError("CDS hips2fits returned a blank image with no pixels.")
    if not np.issubdtype(image_data.dtype, np.number) or np.issubdtype(image_data.dtype, np.complexfloating):
        raise ValueError("CDS hips2fits returned a non-image response; numeric pixels were expected.")

    expected_gray_shape = (height, width)
    expected_color_shape = (height, width, 3)
    expected_channel_first_shape = (3, height, width)
    if image_data.ndim == 2:
        if image_data.shape != expected_gray_shape:
            raise ValueError(
                f"CDS hips2fits returned image shape {image_data.shape}; expected {expected_gray_shape}."
            )
    elif image_data.ndim == 3:
        if image_data.shape == expected_channel_first_shape:
            image_data = np.moveaxis(image_data, 0, -1)
        elif image_data.shape != expected_color_shape:
            raise ValueError(
                "CDS hips2fits returned a non-image channel layout; expected HxWx3 or 3xHxW data."
            )
    else:
        raise ValueError("CDS hips2fits returned a non-image response; expected a 2D or 3-channel image.")

    finite_mask = np.isfinite(image_data)
    if not np.any(finite_mask):
        raise ValueError("Survey image has no coverage: the response contains no finite pixels.")
    finite_values = image_data[finite_mask]
    if not np.any(finite_values != 0):
        raise ValueError("Survey image is blank: all finite pixels are zero.")

    if not np.all(finite_mask):
        finite_minimum = np.min(finite_values)
        finite_maximum = np.max(finite_values)
        image_data = np.nan_to_num(
            image_data,
            copy=True,
            nan=0.0,
            posinf=finite_maximum,
            neginf=finite_minimum,
        )
    return np.array(image_data, copy=True, order="C")


def _load_cached_image(
    cache_path: Path,
    expected_metadata: Mapping[str, Any],
    *,
    width: int,
    height: int,
) -> np.ndarray | None:
    if not cache_path.is_file():
        return None
    try:
        with np.load(cache_path, allow_pickle=False) as cached:
            metadata_value = cached["metadata"]
            if metadata_value.ndim != 0:
                raise ValueError("Invalid survey cache metadata.")
            metadata = json.loads(str(metadata_value.item()))
            if metadata != dict(expected_metadata):
                raise ValueError("Survey cache metadata does not match its request.")
            image_data = _validated_image_data(cached["image_data"], width=width, height=height)
            return np.array(image_data, copy=True, order="C")
    except Exception:
        _discard_cache_file(cache_path)
        return None


def _store_cached_image(cache_path: Path, metadata: Mapping[str, Any], image_data: np.ndarray) -> None:
    temporary_path: Path | None = None
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w+b",
            prefix=f".{cache_path.stem}-",
            suffix=".tmp",
            dir=cache_path.parent,
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            np.savez_compressed(
                temporary_file,
                image_data=image_data,
                metadata=np.asarray(
                    json.dumps(dict(metadata), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
                ),
            )
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, cache_path)
    except OSError:
        if temporary_path is not None:
            _discard_cache_file(temporary_path)


def _prune_cache(cache_root: Path, *, max_files: int = _MAX_CACHE_FILES) -> None:
    try:
        cache_files = sorted(
            cache_root.glob("*.npz"),
            key=lambda path: (path.stat().st_mtime_ns, path.name),
            reverse=True,
        )
    except OSError:
        return
    for cache_path in cache_files[max(0, int(max_files)) :]:
        _discard_cache_file(cache_path)


def _discard_cache_file(cache_path: Path) -> None:
    try:
        cache_path.unlink(missing_ok=True)
    except OSError:
        pass


def _emit_progress(progress_callback: Callable[[str], None] | None, message: str) -> None:
    if progress_callback is not None:
        progress_callback(message)
