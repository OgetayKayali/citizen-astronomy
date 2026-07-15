from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence


MILKY_WAY_TILE_MANIFEST_ENV_VAR = "CITIZEN_PHOTOMETRY_MILKY_WAY_TILE_MANIFEST"
MILKY_WAY_GLOBAL_TEXTURE_ENV_VAR = "CITIZEN_PHOTOMETRY_MILKY_WAY_GLOBAL_TEXTURE"
MILKY_WAY_TEXTURE_COMPAT_ENV_VAR = "CITIZEN_PHOTOMETRY_MILKY_WAY_TEXTURE"

_DEFAULT_INTERACTIVE_TILE_MANIFEST_PATHS = (
    Path("textures") / "milky_way_tiles_32k_padded_lzw_benchmark" / "manifest.json",
    Path("textures") / "milky_way_tiles_32k_padded_benchmark" / "manifest.json",
    Path("textures") / "milky_way_tiles_32k" / "manifest.json",
    Path("milky_way_tiles") / "manifest.json",
    Path("textures") / "milky_way_tiles" / "manifest.json",
)
_HIGH_DETAIL_TILE_MANIFEST_PATHS = (
    Path("textures") / "milky_way_tiles_32k_padded_lzw_benchmark" / "manifest.json",
    Path("textures") / "milky_way_tiles_32k_padded_benchmark" / "manifest.json",
    Path("textures") / "milky_way_tiles_32k" / "manifest.json",
    Path("milky_way_tiles") / "manifest.json",
    Path("textures") / "milky_way_tiles" / "manifest.json",
)
_DEFAULT_TILE_MANIFEST_PATHS = _DEFAULT_INTERACTIVE_TILE_MANIFEST_PATHS
_DEFAULT_PREVIEW_PATH = Path("textures") / "milkyway_2020_4k_preview.png"
_DEFAULT_GLOBAL_PATHS = (
    Path("textures") / "milkyway_2020_4k.png",
    Path("textures") / "milkyway_2020_8k.png",
)

_VALID_COLOR_SPACES = {"srgb", "linear", "unknown"}
_VALID_DEC_AXES = {"top_to_bottom"}
_VALID_PROJECTION = "equirectangular_celestial"
_VALID_RA_DIRECTIONS = {"left", "right"}
_VALID_TONE_PREPROCESSING = {"none", "source_like", "display_toned"}
_LEVEL_WIDTH_CAPS = {
    "low": 4096,
    "medium": 16384,
    "high": None,
}
_QUALITY_TARGETS = {
    "low": 0.75,
    "medium": 0.9,
    "high": 1.0,
}


class MilkyWayManifestValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class MilkyWayManifestLevel:
    level: int
    width: int
    height: int
    path: str


@dataclass(frozen=True, slots=True)
class MilkyWayAlphaMaskMetadata:
    version: int
    algorithm: str
    black_point: float
    white_point: float
    blur_radius_fraction: float
    min_blur_radius_px: int
    max_blur_radius_px: int
    blur_passes: int
    alpha_gamma: float
    empty_alpha_threshold: float
    empty_coverage_threshold: float


@dataclass(frozen=True, slots=True)
class MilkyWayTileSignalMetadata:
    level: int
    x_index: int
    y_index: int
    has_signal: bool
    alpha_coverage: float
    mean_alpha: float
    max_alpha: float


@dataclass(frozen=True, slots=True)
class MilkyWayTileManifest:
    schema_version: int
    projection: str
    source_name: str
    source_width: int
    source_height: int
    tile_size: int
    tile_width: int
    tile_height: int
    gutter_pixels: int
    content_region: tuple[int, int, int, int]
    padded_tile: bool
    tile_format: str
    compression: str
    file_extension: str
    codec: str
    color_space: str
    tone_preprocessing: str
    orientation_ra_increases: str
    orientation_dec_axis: str
    levels: tuple[MilkyWayManifestLevel, ...]
    manifest_path: Path | None = None
    alpha_mask: MilkyWayAlphaMaskMetadata | None = None
    tile_signal_stats: tuple[MilkyWayTileSignalMetadata, ...] = ()

    def level_by_number(self, level_number: int) -> MilkyWayManifestLevel:
        for level in self.levels:
            if level.level == level_number:
                return level
        raise KeyError(level_number)

    @property
    def highest_level(self) -> MilkyWayManifestLevel:
        return max(self.levels, key=lambda item: item.width)

    def tile_signal(self, level_number: int, x_index: int, y_index: int) -> MilkyWayTileSignalMetadata | None:
        for stats in self.tile_signal_stats:
            if stats.level == int(level_number) and stats.x_index == int(x_index) and stats.y_index == int(y_index):
                return stats
        return None


@dataclass(frozen=True, slots=True)
class MilkyWayGlobalAsset:
    path: Path
    mode: str


@dataclass(frozen=True, slots=True)
class MilkyWayTilePyramidAsset:
    manifest: MilkyWayTileManifest
    path: Path

    @property
    def asset_id(self) -> str:
        return str(self.path)


@dataclass(frozen=True, slots=True)
class MilkyWayAssetInfo:
    asset_mode: str
    active_path: Path | None
    gl_suitable: bool
    cpu_suitable: bool
    fallback_reason: str = ""
    runtime_single_texture_mode: str = "missing"
    runtime_single_texture_path: Path | None = None
    active_source_level: str = "global"
    visible_tile_count: int = 0
    asset: MilkyWayGlobalAsset | MilkyWayTilePyramidAsset | None = None


def clamp_dec_deg(dec_deg: float) -> float:
    return max(-90.0, min(90.0, float(dec_deg)))


def normalize_ra_deg(ra_deg: float) -> float:
    return float(ra_deg) % 360.0


def milky_way_u_from_ra_deg(ra_deg: float, *, ra_increases: str = "left") -> float:
    ra_fraction = normalize_ra_deg(ra_deg) / 360.0
    if str(ra_increases).strip().casefold() == "right":
        return (0.5 + ra_fraction) % 1.0
    return (0.5 - ra_fraction) % 1.0


def milky_way_v_from_dec_deg(dec_deg: float) -> float:
    return 0.5 - (clamp_dec_deg(dec_deg) / 180.0)


def milky_way_ra_deg_from_u(u_value: float, *, ra_increases: str = "left") -> float:
    wrapped_u = float(u_value) % 1.0
    if str(ra_increases).strip().casefold() == "right":
        return normalize_ra_deg((wrapped_u - 0.5) * 360.0)
    return normalize_ra_deg((0.5 - wrapped_u) * 360.0)


def milky_way_dec_deg_from_v(v_value: float) -> float:
    return clamp_dec_deg((0.5 - float(v_value)) * 180.0)


def milky_way_uv_from_radec(ra_deg: float, dec_deg: float, *, ra_increases: str = "left") -> tuple[float, float]:
    return milky_way_u_from_ra_deg(ra_deg, ra_increases=ra_increases), milky_way_v_from_dec_deg(dec_deg)


def tile_grid_dimensions(level_width: int, level_height: int, tile_size: int) -> tuple[int, int]:
    resolved_tile_size = max(1, int(tile_size))
    return (
        max(1, int(math.ceil(float(level_width) / float(resolved_tile_size)))),
        max(1, int(math.ceil(float(level_height) / float(resolved_tile_size)))),
    )


def tile_pixel_bounds(
    x_index: int,
    y_index: int,
    *,
    level_width: int,
    level_height: int,
    tile_size: int,
) -> tuple[int, int, int, int]:
    resolved_tile_size = max(1, int(tile_size))
    left = max(0, int(x_index) * resolved_tile_size)
    top = max(0, int(y_index) * resolved_tile_size)
    right = min(int(level_width), left + resolved_tile_size)
    bottom = min(int(level_height), top + resolved_tile_size)
    return left, top, right, bottom


def tile_uv_bounds(
    x_index: int,
    y_index: int,
    *,
    level_width: int,
    level_height: int,
    tile_size: int,
) -> tuple[float, float, float, float]:
    left, top, right, bottom = tile_pixel_bounds(
        x_index,
        y_index,
        level_width=level_width,
        level_height=level_height,
        tile_size=tile_size,
    )
    return (
        float(left) / max(1.0, float(level_width)),
        float(right) / max(1.0, float(level_width)),
        float(top) / max(1.0, float(level_height)),
        float(bottom) / max(1.0, float(level_height)),
    )


def tile_radec_bounds(
    x_index: int,
    y_index: int,
    *,
    level_width: int,
    level_height: int,
    tile_size: int,
    ra_increases: str = "left",
) -> tuple[float, float, float, float]:
    u_min, u_max, v_min, v_max = tile_uv_bounds(
        x_index,
        y_index,
        level_width=level_width,
        level_height=level_height,
        tile_size=tile_size,
    )
    return (
        milky_way_ra_deg_from_u(u_min, ra_increases=ra_increases),
        milky_way_ra_deg_from_u(u_max, ra_increases=ra_increases),
        milky_way_dec_deg_from_v(v_max),
        milky_way_dec_deg_from_v(v_min),
    )


def resolve_manifest_tile_path(manifest: MilkyWayTileManifest, level_number: int, x_index: int, y_index: int) -> Path:
    level = manifest.level_by_number(level_number)
    if manifest.manifest_path is None:
        raise MilkyWayManifestValidationError("Milky Way manifest path is unavailable for tile resolution")
    return manifest.manifest_path.parent / level.path.format(x=int(x_index), y=int(y_index))


def tile_xy_from_uv(u_value: float, v_value: float, *, level_width: int, level_height: int, tile_size: int) -> tuple[int, int]:
    tile_count_x, tile_count_y = tile_grid_dimensions(level_width, level_height, tile_size)
    wrapped_u = float(u_value) % 1.0
    clamped_v = max(0.0, min(1.0, float(v_value)))
    x_index = int(math.floor(wrapped_u * tile_count_x)) % tile_count_x
    epsilon = 1.0e-9
    y_index = int(math.floor(min(max(clamped_v, 0.0), 1.0 - epsilon) * tile_count_y))
    y_index = max(0, min(tile_count_y - 1, y_index))
    return x_index, y_index


def expand_tile_selection(
    tiles: Iterable[tuple[int, int]],
    *,
    tile_count_x: int,
    tile_count_y: int,
    margin: int,
) -> set[tuple[int, int]]:
    resolved_margin = max(0, int(margin))
    expanded: set[tuple[int, int]] = set()
    for x_index, y_index in tiles:
        for offset_y in range(-resolved_margin, resolved_margin + 1):
            next_y = max(0, min(tile_count_y - 1, int(y_index) + offset_y))
            for offset_x in range(-resolved_margin, resolved_margin + 1):
                next_x = (int(x_index) + offset_x) % max(1, tile_count_x)
                expanded.add((next_x, next_y))
    return expanded


def tiles_from_radec_bounds(
    min_ra_deg: float,
    max_ra_deg: float,
    min_dec_deg: float,
    max_dec_deg: float,
    *,
    level_width: int,
    level_height: int,
    tile_size: int,
    ra_increases: str = "left",
    margin: int = 0,
) -> set[tuple[int, int]]:
    tile_count_x, tile_count_y = tile_grid_dimensions(level_width, level_height, tile_size)
    if tile_count_x <= 0 or tile_count_y <= 0:
        return set()

    raw_ra_span = abs(float(max_ra_deg) - float(min_ra_deg))
    ra_start = normalize_ra_deg(min_ra_deg)
    ra_end = normalize_ra_deg(max_ra_deg)
    dec_start = clamp_dec_deg(min_dec_deg)
    dec_end = clamp_dec_deg(max_dec_deg)
    dec_low = min(dec_start, dec_end)
    dec_high = max(dec_start, dec_end)
    v_start = milky_way_v_from_dec_deg(dec_high)
    v_end = milky_way_v_from_dec_deg(dec_low)
    y_start = tile_xy_from_uv(0.0, v_start, level_width=level_width, level_height=level_height, tile_size=tile_size)[1]
    y_end = tile_xy_from_uv(0.0, v_end, level_width=level_width, level_height=level_height, tile_size=tile_size)[1]
    y_low = min(y_start, y_end)
    y_high = max(y_start, y_end)

    full_sky = raw_ra_span >= 359.999 or _ra_span_degrees(ra_start, ra_end) >= 359.999
    selected_tiles: set[tuple[int, int]] = set()
    if full_sky:
        for x_index in range(tile_count_x):
            for y_index in range(y_low, y_high + 1):
                selected_tiles.add((x_index, y_index))
        return expand_tile_selection(selected_tiles, tile_count_x=tile_count_x, tile_count_y=tile_count_y, margin=margin)

    for interval_start, interval_end in _ra_intervals(ra_start, ra_end):
        u_start = milky_way_u_from_ra_deg(interval_start, ra_increases=ra_increases)
        u_end = milky_way_u_from_ra_deg(interval_end, ra_increases=ra_increases)
        for x_start, x_end in _tile_x_ranges_from_u_values(u_start, u_end, tile_count_x):
            if x_start <= x_end:
                x_values = range(x_start, x_end + 1)
            else:
                x_values = tuple(range(x_start, tile_count_x)) + tuple(range(0, x_end + 1))
            for x_index in x_values:
                for y_index in range(y_low, y_high + 1):
                    selected_tiles.add((x_index, y_index))

    return expand_tile_selection(selected_tiles, tile_count_x=tile_count_x, tile_count_y=tile_count_y, margin=margin)


def select_milky_way_level(
    levels: Sequence[MilkyWayManifestLevel],
    *,
    viewport_width_px: int,
    viewport_height_px: int,
    field_width_deg: float,
    field_height_deg: float,
    resolution_mode: str,
) -> MilkyWayManifestLevel:
    sorted_levels = _sorted_allowed_levels(levels, resolution_mode)
    if not sorted_levels:
        raise ValueError("Milky Way levels must not be empty")
    target_ratio = _QUALITY_TARGETS.get(_normalize_resolution_mode(resolution_mode), 1.0)
    for level in sorted_levels:
        if _level_screen_ratio(
            level,
            viewport_width_px=viewport_width_px,
            viewport_height_px=viewport_height_px,
            field_width_deg=field_width_deg,
            field_height_deg=field_height_deg,
        ) >= target_ratio:
            return level
    return sorted_levels[-1]


def select_milky_way_level_with_hysteresis(
    levels: Sequence[MilkyWayManifestLevel],
    *,
    viewport_width_px: int,
    viewport_height_px: int,
    field_width_deg: float,
    field_height_deg: float,
    resolution_mode: str,
    previous_level: int | None,
    hysteresis_ratio: float = 0.12,
) -> MilkyWayManifestLevel:
    sorted_levels = _sorted_allowed_levels(levels, resolution_mode)
    if not sorted_levels:
        raise ValueError("Milky Way levels must not be empty")
    candidate = select_milky_way_level(
        sorted_levels,
        viewport_width_px=viewport_width_px,
        viewport_height_px=viewport_height_px,
        field_width_deg=field_width_deg,
        field_height_deg=field_height_deg,
        resolution_mode=resolution_mode,
    )
    if previous_level is None:
        return candidate
    previous = next((level for level in sorted_levels if level.level == int(previous_level)), None)
    if previous is None:
        return candidate
    target_ratio = _QUALITY_TARGETS.get(_normalize_resolution_mode(resolution_mode), 1.0)
    previous_ratio = _level_screen_ratio(
        previous,
        viewport_width_px=viewport_width_px,
        viewport_height_px=viewport_height_px,
        field_width_deg=field_width_deg,
        field_height_deg=field_height_deg,
    )
    lower_bound = target_ratio * max(0.0, 1.0 - float(hysteresis_ratio))
    upper_bound = target_ratio * (1.0 + float(hysteresis_ratio))
    if lower_bound <= previous_ratio <= upper_bound:
        return previous
    return candidate


def load_milky_way_manifest(manifest_path: str | Path, *, strict: bool = False) -> MilkyWayTileManifest:
    path = Path(manifest_path).expanduser().resolve()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise MilkyWayManifestValidationError(f"Milky Way manifest does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise MilkyWayManifestValidationError(f"Milky Way manifest is not valid JSON: {path}: {exc}") from exc
    return parse_milky_way_manifest(payload, manifest_path=path, strict=strict)


def parse_milky_way_manifest(payload: dict[str, Any], *, manifest_path: Path | None = None, strict: bool = False) -> MilkyWayTileManifest:
    if not isinstance(payload, dict):
        raise MilkyWayManifestValidationError("Milky Way manifest must be a JSON object")
    orientation_payload = payload.get("orientation")
    if not isinstance(orientation_payload, dict):
        raise MilkyWayManifestValidationError("Milky Way manifest orientation must be an object")

    levels_payload = payload.get("levels")
    if not isinstance(levels_payload, list) or not levels_payload:
        raise MilkyWayManifestValidationError("Milky Way manifest must define at least one level")

    levels: list[MilkyWayManifestLevel] = []
    seen_levels: set[int] = set()
    for index, level_payload in enumerate(levels_payload):
        if not isinstance(level_payload, dict):
            raise MilkyWayManifestValidationError(f"Milky Way manifest level {index} must be an object")
        level_number = _require_positive_int(level_payload.get("level"), f"levels[{index}].level", allow_zero=True)
        if level_number in seen_levels:
            raise MilkyWayManifestValidationError(f"Milky Way manifest contains duplicate level {level_number}")
        seen_levels.add(level_number)
        width = _require_positive_int(level_payload.get("width"), f"levels[{index}].width")
        height = _require_positive_int(level_payload.get("height"), f"levels[{index}].height")
        _validate_two_to_one(width, height, f"levels[{index}]")
        path_pattern = str(level_payload.get("path") or "").strip()
        if "{x}" not in path_pattern or "{y}" not in path_pattern:
            raise MilkyWayManifestValidationError(f"Milky Way manifest level {level_number} path must include {{x}} and {{y}}")
        levels.append(MilkyWayManifestLevel(level=level_number, width=width, height=height, path=path_pattern))

    source_width = _require_positive_int(payload.get("source_width"), "source_width")
    source_height = _require_positive_int(payload.get("source_height"), "source_height")
    _validate_two_to_one(source_width, source_height, "source")

    tile_size = _require_positive_int(payload.get("tile_size"), "tile_size")
    gutter_pixels = _require_positive_int(payload.get("gutter_pixels", 0), "gutter_pixels", allow_zero=True)
    padded_tile = bool(payload.get("padded_tile", False))
    tile_width = _require_positive_int(payload.get("tile_width", tile_size + (gutter_pixels * 2)), "tile_width")
    tile_height = _require_positive_int(payload.get("tile_height", tile_size + (gutter_pixels * 2)), "tile_height")
    default_content_region = [gutter_pixels, gutter_pixels, tile_size, tile_size]
    content_region = _parse_content_region(payload.get("content_region", default_content_region), field_name="content_region")
    content_x, content_y, content_width, content_height = content_region
    if content_x + content_width > tile_width or content_y + content_height > tile_height:
        raise MilkyWayManifestValidationError("Milky Way manifest content_region must fit inside tile_width and tile_height")
    if padded_tile and gutter_pixels <= 0:
        raise MilkyWayManifestValidationError("Milky Way manifest padded_tile=true requires gutter_pixels > 0")
    if not padded_tile and gutter_pixels == 0 and content_region != (0, 0, tile_size, tile_size):
        raise MilkyWayManifestValidationError(
            "Milky Way manifest without padded tiles must use content_region [0, 0, tile_size, tile_size]"
        )

    inferred_extension = _infer_manifest_file_extension(levels)
    file_extension = _normalize_manifest_file_extension(payload.get("file_extension"), fallback=inferred_extension)
    tile_format = str(payload.get("tile_format") or _infer_manifest_tile_format(file_extension)).strip().casefold()
    compression = str(payload.get("compression") or _infer_manifest_compression(tile_format)).strip().casefold()
    codec = str(payload.get("codec") or _infer_manifest_codec(tile_format, compression)).strip().casefold()

    color_space = str(payload.get("color_space") or "unknown").strip().casefold()
    if color_space not in _VALID_COLOR_SPACES:
        raise MilkyWayManifestValidationError(f"Unsupported Milky Way manifest color_space: {color_space}")

    tone_preprocessing = str(payload.get("tone_preprocessing") or "unknown").strip().casefold()
    if tone_preprocessing not in _VALID_TONE_PREPROCESSING:
        raise MilkyWayManifestValidationError(f"Unsupported Milky Way manifest tone_preprocessing: {tone_preprocessing}")

    ra_increases = str(orientation_payload.get("ra_increases") or "").strip().casefold()
    if ra_increases not in _VALID_RA_DIRECTIONS:
        raise MilkyWayManifestValidationError(f"Unsupported Milky Way manifest orientation.ra_increases: {ra_increases}")

    dec_axis = str(orientation_payload.get("dec_axis") or "").strip().casefold()
    if dec_axis not in _VALID_DEC_AXES:
        raise MilkyWayManifestValidationError(f"Unsupported Milky Way manifest orientation.dec_axis: {dec_axis}")

    projection = str(payload.get("projection") or "").strip().casefold()
    if projection != _VALID_PROJECTION:
        raise MilkyWayManifestValidationError(f"Unsupported Milky Way manifest projection: {projection}")

    alpha_mask, tile_signal_stats = _parse_alpha_mask_metadata(payload.get("alpha_mask"))

    manifest = MilkyWayTileManifest(
        schema_version=int(payload.get("schema_version") or 0),
        projection=projection,
        source_name=str(payload.get("source_name") or "").strip() or "unknown",
        source_width=source_width,
        source_height=source_height,
        tile_size=tile_size,
        tile_width=tile_width,
        tile_height=tile_height,
        gutter_pixels=gutter_pixels,
        content_region=content_region,
        padded_tile=padded_tile,
        tile_format=tile_format,
        compression=compression,
        file_extension=file_extension,
        codec=codec,
        color_space=color_space,
        tone_preprocessing=tone_preprocessing,
        orientation_ra_increases=ra_increases,
        orientation_dec_axis=dec_axis,
        levels=tuple(sorted(levels, key=lambda item: item.width)),
        manifest_path=manifest_path,
        alpha_mask=alpha_mask,
        tile_signal_stats=tile_signal_stats,
    )
    if manifest.schema_version != 1:
        raise MilkyWayManifestValidationError(f"Unsupported Milky Way manifest schema_version: {manifest.schema_version}")

    highest_level = manifest.highest_level
    if highest_level.width > manifest.source_width or highest_level.height > manifest.source_height:
        raise MilkyWayManifestValidationError("Milky Way manifest highest level exceeds source dimensions")

    if strict:
        _validate_manifest_tile_files(manifest)

    return manifest


def _parse_alpha_mask_metadata(raw_payload: Any) -> tuple[MilkyWayAlphaMaskMetadata | None, tuple[MilkyWayTileSignalMetadata, ...]]:
    if raw_payload is None:
        return None, ()
    if not isinstance(raw_payload, dict):
        raise MilkyWayManifestValidationError("Milky Way manifest alpha_mask must be an object")

    metadata = MilkyWayAlphaMaskMetadata(
        version=_require_positive_int(raw_payload.get("version", 1), "alpha_mask.version"),
        algorithm=str(raw_payload.get("algorithm") or "unknown").strip() or "unknown",
        black_point=_require_unit_float(raw_payload.get("black_point", 0.0), "alpha_mask.black_point"),
        white_point=_require_unit_float(raw_payload.get("white_point", 1.0), "alpha_mask.white_point"),
        blur_radius_fraction=max(0.0, float(raw_payload.get("blur_radius_fraction", 0.0) or 0.0)),
        min_blur_radius_px=_require_positive_int(raw_payload.get("min_blur_radius_px", 0), "alpha_mask.min_blur_radius_px", allow_zero=True),
        max_blur_radius_px=_require_positive_int(raw_payload.get("max_blur_radius_px", 0), "alpha_mask.max_blur_radius_px", allow_zero=True),
        blur_passes=_require_positive_int(raw_payload.get("blur_passes", 1), "alpha_mask.blur_passes"),
        alpha_gamma=max(0.001, float(raw_payload.get("alpha_gamma", 1.0) or 1.0)),
        empty_alpha_threshold=_require_unit_float(raw_payload.get("empty_alpha_threshold", 0.0), "alpha_mask.empty_alpha_threshold"),
        empty_coverage_threshold=_require_unit_float(raw_payload.get("empty_coverage_threshold", 0.0), "alpha_mask.empty_coverage_threshold"),
    )
    if metadata.white_point <= metadata.black_point:
        raise MilkyWayManifestValidationError("Milky Way manifest alpha_mask.white_point must be greater than black_point")

    tiles_payload = raw_payload.get("tiles", [])
    if not isinstance(tiles_payload, list):
        raise MilkyWayManifestValidationError("Milky Way manifest alpha_mask.tiles must be a list")
    tile_stats: list[MilkyWayTileSignalMetadata] = []
    seen_keys: set[tuple[int, int, int]] = set()
    for index, tile_payload in enumerate(tiles_payload):
        if not isinstance(tile_payload, dict):
            raise MilkyWayManifestValidationError(f"Milky Way manifest alpha_mask.tiles[{index}] must be an object")
        level = _require_positive_int(tile_payload.get("level"), f"alpha_mask.tiles[{index}].level", allow_zero=True)
        x_index = _require_positive_int(tile_payload.get("x"), f"alpha_mask.tiles[{index}].x", allow_zero=True)
        y_index = _require_positive_int(tile_payload.get("y"), f"alpha_mask.tiles[{index}].y", allow_zero=True)
        key = (level, x_index, y_index)
        if key in seen_keys:
            raise MilkyWayManifestValidationError(f"Milky Way manifest alpha_mask contains duplicate tile stats for {key!r}")
        seen_keys.add(key)
        tile_stats.append(
            MilkyWayTileSignalMetadata(
                level=level,
                x_index=x_index,
                y_index=y_index,
                has_signal=bool(tile_payload.get("has_signal", False)),
                alpha_coverage=_require_unit_float(tile_payload.get("alpha_coverage", 0.0), f"alpha_mask.tiles[{index}].alpha_coverage"),
                mean_alpha=_require_unit_float(tile_payload.get("mean_alpha", 0.0), f"alpha_mask.tiles[{index}].mean_alpha"),
                max_alpha=_require_unit_float(tile_payload.get("max_alpha", 0.0), f"alpha_mask.tiles[{index}].max_alpha"),
            )
        )
    return metadata, tuple(tile_stats)


def _infer_manifest_file_extension(levels: Sequence[MilkyWayManifestLevel]) -> str:
    for level in levels:
        suffix = Path(level.path.replace("{x}", "0").replace("{y}", "0")).suffix.strip().casefold()
        if suffix:
            return suffix
    return ".png"


def _normalize_manifest_file_extension(raw_value: Any, *, fallback: str) -> str:
    value = str(raw_value or fallback).strip().casefold()
    if not value:
        return ".png"
    if not value.startswith("."):
        value = f".{value}"
    if value in {".tif", ".tiff"}:
        return ".tiff"
    return value


def _infer_manifest_tile_format(file_extension: str) -> str:
    if file_extension == ".png":
        return "png"
    if file_extension == ".tiff":
        return "tiff"
    if file_extension == ".webp":
        return "webp"
    return file_extension.lstrip(".") or "png"


def _infer_manifest_compression(tile_format: str) -> str:
    if tile_format == "png":
        return "png"
    if tile_format == "tiff":
        return "unknown"
    return "unknown"


def _infer_manifest_codec(tile_format: str, compression: str) -> str:
    if tile_format == "tiff" and compression and compression != "unknown":
        return f"{tile_format}_{compression}"
    if tile_format:
        return tile_format
    return "unknown"


class MilkyWayAssetResolver:
    def __init__(
        self,
        *,
        project_root: Path,
        tile_manifest_path: str | Path | None = None,
        global_texture_path: str | Path | None = None,
        preview_texture_path: str | Path | None = None,
    ) -> None:
        self._project_root = Path(project_root).resolve()
        self._tile_manifest_override = _resolve_optional_path(tile_manifest_path, project_root=self._project_root)
        self._global_texture_override = _resolve_optional_path(global_texture_path, project_root=self._project_root)
        self._preview_texture_override = _resolve_optional_path(preview_texture_path, project_root=self._project_root)

    @classmethod
    def from_environment(cls, project_root: Path) -> "MilkyWayAssetResolver":
        env = os.environ
        return cls(
            project_root=project_root,
            tile_manifest_path=env.get(MILKY_WAY_TILE_MANIFEST_ENV_VAR),
            global_texture_path=env.get(MILKY_WAY_GLOBAL_TEXTURE_ENV_VAR) or env.get(MILKY_WAY_TEXTURE_COMPAT_ENV_VAR),
        )

    def resolve(self, *, strict_manifest: bool = False) -> MilkyWayAssetInfo:

        return self._resolve_with_manifest_paths(_DEFAULT_TILE_MANIFEST_PATHS, strict_manifest=strict_manifest)

    def resolve_high_detail(self, *, strict_manifest: bool = False) -> MilkyWayAssetInfo:

        return self._resolve_with_manifest_paths(_HIGH_DETAIL_TILE_MANIFEST_PATHS, strict_manifest=strict_manifest)

    def _resolve_with_manifest_paths(
        self,
        default_manifest_paths: Sequence[Path],
        *,
        strict_manifest: bool = False,
    ) -> MilkyWayAssetInfo:

        fallback_reasons: list[str] = []
        manifest_candidate = self._tile_manifest_override or self._first_existing_path(default_manifest_paths)
        global_candidate = self._global_texture_override or self._first_existing_path(_DEFAULT_GLOBAL_PATHS)
        preview_candidate = self._preview_texture_override or (self._project_root / _DEFAULT_PREVIEW_PATH)

        runtime_mode = "missing"
        runtime_path: Path | None = None
        if global_candidate is not None and global_candidate.is_file():
            runtime_mode = "single_global"
            runtime_path = global_candidate
        elif preview_candidate.is_file():
            runtime_mode = "preview_fallback"
            runtime_path = preview_candidate

        if manifest_candidate is not None and manifest_candidate.is_file():
            try:
                manifest = load_milky_way_manifest(manifest_candidate, strict=strict_manifest)
                return MilkyWayAssetInfo(
                    asset_mode="tiled_manifest",
                    active_path=manifest_candidate,
                    gl_suitable=True,
                    cpu_suitable=False,
                    fallback_reason="; ".join(fallback_reasons),
                    runtime_single_texture_mode=runtime_mode,
                    runtime_single_texture_path=runtime_path,
                    active_source_level=f"L{manifest.levels[0].level}" if manifest.levels else "global",
                    asset=MilkyWayTilePyramidAsset(manifest=manifest, path=manifest_candidate),
                )
            except MilkyWayManifestValidationError as exc:
                fallback_reasons.append(str(exc))
        elif manifest_candidate is not None and not manifest_candidate.exists():
            fallback_reasons.append(f"Milky Way tile manifest missing: {manifest_candidate}")

        if global_candidate is not None and global_candidate.is_file():
            return MilkyWayAssetInfo(
                asset_mode="single_global",
                active_path=global_candidate,
                gl_suitable=True,
                cpu_suitable=True,
                fallback_reason="; ".join(fallback_reasons),
                runtime_single_texture_mode="single_global",
                runtime_single_texture_path=global_candidate,
                active_source_level="global",
                asset=MilkyWayGlobalAsset(path=global_candidate, mode="single_global"),
            )

        if preview_candidate.is_file():
            return MilkyWayAssetInfo(
                asset_mode="preview_fallback",
                active_path=preview_candidate,
                gl_suitable=True,
                cpu_suitable=True,
                fallback_reason="; ".join(fallback_reasons),
                runtime_single_texture_mode="preview_fallback",
                runtime_single_texture_path=preview_candidate,
                active_source_level="preview",
                asset=MilkyWayGlobalAsset(path=preview_candidate, mode="preview_fallback"),
            )

        if global_candidate is not None and not global_candidate.exists():
            fallback_reasons.append(f"Milky Way global texture missing: {global_candidate}")

        return MilkyWayAssetInfo(
            asset_mode="missing",
            active_path=None,
            gl_suitable=False,
            cpu_suitable=False,
            fallback_reason="; ".join(reason for reason in fallback_reasons if reason),
            runtime_single_texture_mode="missing",
            runtime_single_texture_path=None,
            active_source_level="missing",
            asset=None,
        )

    def _first_existing_path(self, relative_paths: Sequence[Path]) -> Path | None:
        for relative_path in relative_paths:
            candidate = self._project_root / relative_path
            if candidate.is_file():
                return candidate
        return None


def _level_screen_ratio(
    level: MilkyWayManifestLevel,
    *,
    viewport_width_px: int,
    viewport_height_px: int,
    field_width_deg: float,
    field_height_deg: float,
) -> float:
    screen_ppd_x = max(1.0e-6, float(viewport_width_px)) / max(1.0e-6, float(field_width_deg))
    screen_ppd_y = max(1.0e-6, float(viewport_height_px)) / max(1.0e-6, float(field_height_deg))
    source_ppd_x = float(level.width) / 360.0
    source_ppd_y = float(level.height) / 180.0
    return min(source_ppd_x / screen_ppd_x, source_ppd_y / screen_ppd_y)


def _normalize_resolution_mode(resolution_mode: str) -> str:
    resolved_mode = str(resolution_mode or "high").strip().casefold()
    if resolved_mode not in {"low", "medium", "high"}:
        return "high"
    return resolved_mode


def _ra_intervals(start_ra_deg: float, end_ra_deg: float) -> tuple[tuple[float, float], ...]:
    if _ra_span_degrees(start_ra_deg, end_ra_deg) >= 359.999:
        return ((0.0, 360.0),)
    if end_ra_deg < start_ra_deg:
        return ((start_ra_deg, 360.0), (0.0, end_ra_deg))
    return ((start_ra_deg, end_ra_deg),)


def _ra_span_degrees(start_ra_deg: float, end_ra_deg: float) -> float:
    start = normalize_ra_deg(start_ra_deg)
    end = normalize_ra_deg(end_ra_deg)
    if end < start:
        return (360.0 - start) + end
    return end - start


def _resolve_optional_path(path_value: str | Path | None, *, project_root: Path) -> Path | None:
    if path_value is None:
        return None
    raw_path = Path(path_value).expanduser()
    if not raw_path.is_absolute():
        raw_path = (project_root / raw_path).resolve()
    else:
        raw_path = raw_path.resolve()
    return raw_path


def _require_positive_int(value: Any, field_name: str, *, allow_zero: bool = False) -> int:
    try:
        resolved = int(value)
    except (TypeError, ValueError) as exc:
        raise MilkyWayManifestValidationError(f"Milky Way manifest field {field_name} must be an integer") from exc
    minimum = 0 if allow_zero else 1
    if resolved < minimum:
        comparator = "non-negative" if allow_zero else "positive"
        raise MilkyWayManifestValidationError(f"Milky Way manifest field {field_name} must be {comparator}")
    return resolved


def _require_unit_float(value: Any, field_name: str) -> float:
    try:
        resolved = float(value)
    except (TypeError, ValueError) as exc:
        raise MilkyWayManifestValidationError(f"Milky Way manifest field {field_name} must be a number") from exc
    if not math.isfinite(resolved) or resolved < 0.0 or resolved > 1.0:
        raise MilkyWayManifestValidationError(f"Milky Way manifest field {field_name} must be between 0 and 1")
    return resolved


def _parse_content_region(value: Any, *, field_name: str) -> tuple[int, int, int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise MilkyWayManifestValidationError(f"Milky Way manifest field {field_name} must contain four integers")
    return tuple(_require_positive_int(item, f"{field_name}[{index}]", allow_zero=True) for index, item in enumerate(value))


def _sorted_allowed_levels(levels: Sequence[MilkyWayManifestLevel], resolution_mode: str) -> list[MilkyWayManifestLevel]:
    resolved_mode = _normalize_resolution_mode(resolution_mode)
    sorted_levels = sorted(levels, key=lambda item: item.width)
    width_cap = _LEVEL_WIDTH_CAPS.get(resolved_mode)
    if width_cap is None:
        return sorted_levels
    allowed = [level for level in sorted_levels if level.width <= width_cap]
    return allowed or sorted_levels


def _tile_x_ranges_from_u_values(start_u: float, end_u: float, tile_count_x: int) -> tuple[tuple[int, int], ...]:
    resolved_start = float(start_u) % 1.0
    resolved_end = float(end_u) % 1.0
    epsilon = 1.0e-9
    if abs(resolved_start - resolved_end) <= epsilon:
        index, _ = tile_xy_from_uv(resolved_start, 0.5, level_width=tile_count_x, level_height=1, tile_size=1)
        return ((index, index),)
    if resolved_end > resolved_start:
        return ((_tile_index_from_u(resolved_start, tile_count_x), _tile_index_from_u(max(resolved_end - epsilon, 0.0), tile_count_x)),)
    return (
        (_tile_index_from_u(resolved_start, tile_count_x), tile_count_x - 1),
        (0, _tile_index_from_u(max(resolved_end - epsilon, 0.0), tile_count_x)),
    )


def _tile_index_from_u(u_value: float, tile_count_x: int) -> int:
    return int(math.floor((float(u_value) % 1.0) * max(1, tile_count_x))) % max(1, tile_count_x)


def _validate_manifest_tile_files(manifest: MilkyWayTileManifest) -> None:
    if manifest.manifest_path is None:
        return
    base_directory = manifest.manifest_path.parent
    missing_paths: list[str] = []
    for level in manifest.levels:
        tile_count_x, tile_count_y = tile_grid_dimensions(level.width, level.height, manifest.tile_size)
        for x_index in range(tile_count_x):
            for y_index in range(tile_count_y):
                tile_path = base_directory / level.path.format(x=x_index, y=y_index)
                if not tile_path.is_file():
                    missing_paths.append(str(tile_path))
                    if len(missing_paths) >= 3:
                        raise MilkyWayManifestValidationError(
                            "Milky Way manifest strict validation found missing tile files, for example: " + ", ".join(missing_paths)
                        )
    if missing_paths:
        raise MilkyWayManifestValidationError(
            "Milky Way manifest strict validation found missing tile files, for example: " + ", ".join(missing_paths)
        )


def _validate_two_to_one(width: int, height: int, field_name: str) -> None:
    if width != height * 2:
        raise MilkyWayManifestValidationError(f"Milky Way manifest {field_name} dimensions must keep a 2:1 aspect ratio")