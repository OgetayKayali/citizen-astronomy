from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
import math

from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.wcs import WCS
import numpy as np

from photometry_app.core.catalogs import CatalogService
from photometry_app.core.image_io import read_header
from photometry_app.core.models import CatalogStar, SolvedField
from photometry_app.core.settings import AppSettings
from photometry_app.core.sky_explorer import _resolve_source_field


@dataclass(frozen=True, slots=True)
class DistanceMapStar:
    source_id: str
    name: str
    ra_deg: float
    dec_deg: float
    magnitude: float | None
    bp_rp: float | None
    parallax_mas: float | None
    parallax_error_mas: float | None
    pm_ra_mas_per_year: float | None
    pm_dec_mas_per_year: float | None
    distance_pc: float
    x_pc: float
    y_pc: float
    z_pc: float


@dataclass(frozen=True, slots=True)
class DistanceMapResult:
    source_path: Path
    solved_field: SolvedField
    used_astrometry_fallback: bool
    queried_star_count: int
    stars_with_parallax_count: int
    stars: tuple[DistanceMapStar, ...]
    notes: tuple[str, ...]
    report_text: str


def parallax_to_distance_pc(parallax_mas: float | None) -> float | None:
    if parallax_mas is None or not math.isfinite(parallax_mas) or parallax_mas <= 0.0:
        return None
    return float(1000.0 / float(parallax_mas))


def parallax_snr(parallax_mas: float | None, parallax_error_mas: float | None) -> float | None:
    if parallax_mas is None or parallax_error_mas is None:
        return None
    if not math.isfinite(parallax_mas) or not math.isfinite(parallax_error_mas):
        return None
    if parallax_mas <= 0.0 or parallax_error_mas <= 0.0:
        return None
    return float(parallax_mas / parallax_error_mas)


def catalog_star_meets_parallax_snr(star: CatalogStar, min_parallax_snr: float) -> bool:
    if float(min_parallax_snr) <= 0.0:
        return True
    parallax_mas, parallax_error_mas = _catalog_parallax_values(star)
    snr = parallax_snr(parallax_mas, parallax_error_mas)
    return snr is not None and snr >= float(min_parallax_snr)


def star_cartesian_pc(ra_deg: float, dec_deg: float, distance_pc: float) -> tuple[float, float, float]:
    coordinate = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, distance=distance_pc * u.pc, frame="icrs")
    cartesian = coordinate.cartesian
    return float(cartesian.x.to_value(u.pc)), float(cartesian.y.to_value(u.pc)), float(cartesian.z.to_value(u.pc))


def _catalog_parallax_values(star: CatalogStar) -> tuple[float | None, float | None]:
    metadata = star.metadata or {}
    parallax_mas = metadata.get("parallax_mas")
    parallax_error_mas = metadata.get("parallax_error_mas")
    try:
        parallax_value = None if parallax_mas is None else float(parallax_mas)
    except (TypeError, ValueError):
        parallax_value = None
    try:
        parallax_error_value = None if parallax_error_mas is None else float(parallax_error_mas)
    except (TypeError, ValueError):
        parallax_error_value = None
    if parallax_value is not None and (not math.isfinite(parallax_value) or parallax_value <= 0.0):
        parallax_value = None
    if parallax_error_value is not None and (not math.isfinite(parallax_error_value) or parallax_error_value < 0.0):
        parallax_error_value = None
    return parallax_value, parallax_error_value


def _catalog_motion_values(star: CatalogStar) -> tuple[float | None, float | None, float | None]:
    metadata = star.metadata or {}
    bp_rp = metadata.get("bp_rp")
    pm_ra = metadata.get("pm_ra")
    pm_dec = metadata.get("pm_dec")
    try:
        bp_rp_value = None if bp_rp is None else float(bp_rp)
    except (TypeError, ValueError):
        bp_rp_value = None
    try:
        pm_ra_value = None if pm_ra is None else float(pm_ra)
    except (TypeError, ValueError):
        pm_ra_value = None
    try:
        pm_dec_value = None if pm_dec is None else float(pm_dec)
    except (TypeError, ValueError):
        pm_dec_value = None
    if bp_rp_value is not None and not math.isfinite(bp_rp_value):
        bp_rp_value = None
    if pm_ra_value is not None and not math.isfinite(pm_ra_value):
        pm_ra_value = None
    if pm_dec_value is not None and not math.isfinite(pm_dec_value):
        pm_dec_value = None
    return bp_rp_value, pm_ra_value, pm_dec_value


def _distance_map_star_from_catalog(
    star: CatalogStar,
    *,
    max_distance_pc: float,
    min_parallax_snr: float = 0.0,
) -> DistanceMapStar | None:
    if not catalog_star_meets_parallax_snr(star, min_parallax_snr):
        return None
    parallax_mas, parallax_error_mas = _catalog_parallax_values(star)
    bp_rp, pm_ra_mas_per_year, pm_dec_mas_per_year = _catalog_motion_values(star)
    distance_pc = parallax_to_distance_pc(parallax_mas)
    if distance_pc is None or distance_pc > max_distance_pc:
        return None
    x_pc, y_pc, z_pc = star_cartesian_pc(star.ra_deg, star.dec_deg, distance_pc)
    display_name = str(star.metadata.get("preferred_display_name") or star.name or star.source_id)
    return DistanceMapStar(
        source_id=star.source_id,
        name=display_name,
        ra_deg=float(star.ra_deg),
        dec_deg=float(star.dec_deg),
        magnitude=star.magnitude,
        bp_rp=bp_rp,
        parallax_mas=parallax_mas,
        parallax_error_mas=parallax_error_mas,
        pm_ra_mas_per_year=pm_ra_mas_per_year,
        pm_dec_mas_per_year=pm_dec_mas_per_year,
        distance_pc=distance_pc,
        x_pc=x_pc,
        y_pc=y_pc,
        z_pc=z_pc,
    )


DISTANCE_MAP_GAIA_QUERY_ROW_FACTOR = 4
DISTANCE_MAP_GAIA_QUERY_ROW_MIN = 500
DISTANCE_MAP_MAX_STAR_COUNT = 50000
DISTANCE_MAP_DEFAULT_MIN_PARALLAX_SNR = 5.0


def distance_map_gaia_query_row_limit(max_star_count: int) -> int:
    star_limit = max(1, int(max_star_count))
    return max(star_limit * DISTANCE_MAP_GAIA_QUERY_ROW_FACTOR, DISTANCE_MAP_GAIA_QUERY_ROW_MIN)


def build_distance_map(
    source_path: Path,
    *,
    settings: AppSettings,
    max_magnitude: float,
    max_distance_pc: float,
    max_star_count: int,
    progress_callback: Callable[[str], None] | None = None,
) -> DistanceMapResult:
    magnitude_limit = max(-5.0, min(30.0, float(max_magnitude)))
    distance_limit_pc = max(1.0, float(max_distance_pc))
    star_limit = max(1, int(max_star_count))
    query_row_limit = distance_map_gaia_query_row_limit(star_limit)
    notes: list[str] = []

    if progress_callback is not None:
        progress_callback("Checking the image WCS and field footprint.")
    solved_field, used_astrometry_fallback = _resolve_source_field(source_path, settings, progress_callback=progress_callback)
    if used_astrometry_fallback:
        notes.append("Plate-solved the image with astrometry.net before querying catalogs.")

    catalog_service = CatalogService(settings.cache_dir / "distance-map-catalogs")
    if progress_callback is not None:
        snr_message = (
            f", parallax SNR >= {settings.distance_map_min_parallax_snr:.1f}"
            if float(settings.distance_map_min_parallax_snr) > 0.0
            else ""
        )
        progress_callback(
            f"Querying Gaia DR3 for stars with G <= {magnitude_limit:.1f} mag within the solved field "
            f"(up to {query_row_limit} catalog row(s); map keeps brightest {star_limit} with parallax{snr_message})."
        )
    catalog_stars = catalog_service.query_gaia_stars_limited(
        solved_field,
        magnitude_limit,
        row_limit=query_row_limit,
        progress_callback=progress_callback,
    )

    min_parallax_snr = max(0.0, float(settings.distance_map_min_parallax_snr))
    limit_to_image_footprint = bool(settings.distance_map_limit_to_image_footprint)
    field_wcs: WCS | None = None
    if limit_to_image_footprint:
        try:
            field_wcs = WCS(read_header(solved_field.wcs_path))
        except Exception:
            field_wcs = None
            notes.append("Could not read the image WCS for footprint filtering; kept all in-radius Gaia stars.")

    rejected_low_snr = 0
    rejected_parallax_or_distance = 0
    rejected_outside_footprint = 0
    candidates: list[DistanceMapStar] = []
    for star in catalog_stars:
        if min_parallax_snr > 0.0 and not catalog_star_meets_parallax_snr(star, min_parallax_snr):
            rejected_low_snr += 1
            continue
        mapped_star = _distance_map_star_from_catalog(
            star,
            max_distance_pc=distance_limit_pc,
            min_parallax_snr=0.0,
        )
        if mapped_star is None:
            rejected_parallax_or_distance += 1
            continue
        if field_wcs is not None and distance_map_pixel_position(
            mapped_star,
            solved_field=solved_field,
            wcs=field_wcs,
        ) is None:
            rejected_outside_footprint += 1
            continue
        candidates.append(mapped_star)

    if min_parallax_snr > 0.0 and rejected_low_snr > 0:
        notes.append(
            f"Rejected {rejected_low_snr} Gaia star(s) with parallax SNR below {min_parallax_snr:.1f}."
        )
    if rejected_outside_footprint > 0:
        notes.append(
            f"Rejected {rejected_outside_footprint} Gaia star(s) outside the solved image rectangle."
        )

    candidates.sort(
        key=lambda item: (
            item.magnitude if item.magnitude is not None else float("inf"),
            item.distance_pc,
            item.name,
        )
    )
    selected_stars = tuple(candidates[:star_limit])
    if len(candidates) > star_limit:
        notes.append(f"Limited the 3D map to the brightest {star_limit} star(s) within the distance cut.")

    report_lines = [
        f"Distance Map built from {source_path.name}.",
        f"Gaia query returned {len(catalog_stars)} star(s) with G <= {magnitude_limit:.1f} "
        f"(catalog row cap {query_row_limit} = Max Stars {star_limit} x {DISTANCE_MAP_GAIA_QUERY_ROW_FACTOR}).",
        f"Stars passing parallax quality"
        f"{'' if min_parallax_snr <= 0.0 else f' (SNR >= {min_parallax_snr:.1f})'}"
        f"{' and image footprint' if field_wcs is not None else ''}"
        f" with distance <= {distance_limit_pc:.1f} pc: {len(candidates)}.",
        f"Stars drawn in the 3D map: {len(selected_stars)}.",
    ]
    if notes:
        report_lines.append("")
        report_lines.append("Notes:")
        report_lines.extend(f"- {note}" for note in notes)

    return DistanceMapResult(
        source_path=source_path,
        solved_field=solved_field,
        used_astrometry_fallback=used_astrometry_fallback,
        queried_star_count=len(catalog_stars),
        stars_with_parallax_count=len(candidates),
        stars=selected_stars,
        notes=tuple(notes),
        report_text="\n".join(report_lines),
    )


def distance_map_positions(stars: Sequence[DistanceMapStar]) -> np.ndarray:
    if not stars:
        return np.zeros((0, 3), dtype=float)
    return np.asarray([(star.x_pc, star.y_pc, star.z_pc) for star in stars], dtype=float)


@dataclass(frozen=True, slots=True)
class DistanceMapImagingAxes:
    line_of_sight: tuple[float, float, float]
    east: tuple[float, float, float]
    north: tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class DistanceMapDepthRulerGeometry:
    axis_points: np.ndarray
    tick_segments: np.ndarray
    tick_labels: tuple[tuple[tuple[float, float, float], str], ...]


def distance_map_depth_ruler_tick_values(
    depth_min_pc: float,
    depth_max_pc: float,
    *,
    max_ticks: int = 7,
) -> tuple[float, ...]:
    if not math.isfinite(depth_min_pc) or not math.isfinite(depth_max_pc):
        return ()
    lower = float(min(depth_min_pc, depth_max_pc))
    upper = float(max(depth_min_pc, depth_max_pc))
    if upper - lower <= 1e-6:
        center = 0.5 * (lower + upper)
        return (center - 5.0, center, center + 5.0)
    span = upper - lower
    tick_limit = max(3, int(max_ticks))
    raw_step = span / float(max(tick_limit - 1, 1))
    magnitude = 10.0 ** math.floor(math.log10(max(raw_step, 1e-9)))
    normalized = raw_step / magnitude
    if normalized <= 1.0:
        nice_step = 1.0
    elif normalized <= 2.0:
        nice_step = 2.0
    elif normalized <= 5.0:
        nice_step = 5.0
    else:
        nice_step = 10.0
    step = nice_step * magnitude
    start = math.ceil(lower / step) * step
    ticks: list[float] = []
    value = start
    while value <= upper + (step * 0.001):
        ticks.append(float(value))
        value += step
    if not ticks:
        ticks = [lower, upper]
    elif ticks[0] > lower + (step * 0.01):
        ticks.insert(0, lower)
    if ticks[-1] < upper - (step * 0.01):
        ticks.append(upper)
    return tuple(ticks)


def distance_map_format_depth_pc(depth_pc: float) -> str:
    value = float(depth_pc)
    if not math.isfinite(value):
        return "- pc"
    if abs(value) >= 100.0:
        return f"{value:.0f} pc"
    if abs(value) >= 10.0:
        return f"{value:.1f} pc"
    return f"{value:.2f} pc"


def distance_map_depth_ruler_geometry(
    stars: Sequence[DistanceMapStar],
    imaging_axes: DistanceMapImagingAxes,
    *,
    max_ticks: int = 7,
) -> DistanceMapDepthRulerGeometry | None:
    positions = distance_map_positions(stars)
    if positions.size == 0:
        return None
    los = np.asarray(imaging_axes.line_of_sight, dtype=float)
    east = np.asarray(imaging_axes.east, dtype=float)
    north = np.asarray(imaging_axes.north, dtype=float)
    depths = positions @ los
    depth_min = float(np.min(depths))
    depth_max = float(np.max(depths))
    if depth_max - depth_min <= 1e-6:
        depth_min -= 5.0
        depth_max += 5.0
    perp = positions - (los * depths[:, np.newaxis])
    perp_distances = np.linalg.norm(perp, axis=1)
    offset_mag = max(3.0, float(np.percentile(perp_distances, 85)) * 0.22)
    offset = east * offset_mag
    axis_points = np.asarray(
        [los * depth_min + offset, los * depth_max + offset],
        dtype=float,
    )
    tick_half_len = max(1.5, offset_mag * 0.32)
    tick_values = distance_map_depth_ruler_tick_values(depth_min, depth_max, max_ticks=max_ticks)
    tick_segments: list[np.ndarray] = []
    tick_labels: list[tuple[tuple[float, float, float], str]] = []
    label_offset = north * (tick_half_len * 1.35) + east * (offset_mag * 0.18)
    for depth_pc in tick_values:
        center = los * float(depth_pc) + offset
        tick_segments.append(
            np.asarray([center - (north * tick_half_len), center + (north * tick_half_len)], dtype=float)
        )
        label_position = center + label_offset
        tick_labels.append(
            (
                (float(label_position[0]), float(label_position[1]), float(label_position[2])),
                distance_map_format_depth_pc(depth_pc),
            )
        )
    if not tick_segments:
        return None
    return DistanceMapDepthRulerGeometry(
        axis_points=axis_points,
        tick_segments=np.asarray(tick_segments, dtype=float),
        tick_labels=tuple(tick_labels),
    )


def distance_map_imaging_axes(center_ra_deg: float, center_dec_deg: float) -> DistanceMapImagingAxes:
    line_of_sight = _distance_map_sky_direction(center_ra_deg, center_dec_deg)
    north_hint = _distance_map_sky_direction(center_ra_deg, center_dec_deg + 1.0)
    north = north_hint - (line_of_sight * float(np.dot(north_hint, line_of_sight)))
    north_norm = float(np.linalg.norm(north))
    if north_norm <= 1e-12:
        north = np.array([0.0, 0.0, 1.0], dtype=float)
    else:
        north = north / north_norm

    east = np.cross(north, line_of_sight)
    east_norm = float(np.linalg.norm(east))
    if east_norm <= 1e-12:
        east = np.array([0.0, 1.0, 0.0], dtype=float)
    else:
        east = east / east_norm

    north = np.cross(line_of_sight, east)
    north_norm = float(np.linalg.norm(north))
    if north_norm <= 1e-12:
        north = np.array([0.0, 0.0, 1.0], dtype=float)
    else:
        north = north / north_norm

    return DistanceMapImagingAxes(
        line_of_sight=(float(line_of_sight[0]), float(line_of_sight[1]), float(line_of_sight[2])),
        east=(float(east[0]), float(east[1]), float(east[2])),
        north=(float(north[0]), float(north[1]), float(north[2])),
    )


def _distance_map_sky_direction(ra_deg: float, dec_deg: float) -> np.ndarray:
    return np.asarray(star_cartesian_pc(ra_deg, dec_deg, 1.0), dtype=float)


def distance_map_display_pixel_to_world(
    wcs: WCS,
    display_x: float,
    display_y: float,
    *,
    image_height: int,
) -> tuple[float, float]:
    del image_height
    ra_deg, dec_deg = wcs.pixel_to_world_values(float(display_x), float(display_y))
    return float(ra_deg), float(dec_deg)


def distance_map_tomography_line_of_sight(solved_field: SolvedField, wcs: WCS) -> np.ndarray:
    center_x = (float(solved_field.width) - 1.0) * 0.5
    center_y = (float(solved_field.height) - 1.0) * 0.5
    center_ra, center_dec = distance_map_display_pixel_to_world(
        wcs,
        center_x,
        center_y,
        image_height=int(solved_field.height),
    )
    direction = _distance_map_sky_direction(center_ra, center_dec)
    norm = float(np.linalg.norm(direction))
    if norm <= 1e-12:
        return np.array([1.0, 0.0, 0.0], dtype=float)
    return direction / norm


def distance_map_ray_plane_point(
    ra_deg: float,
    dec_deg: float,
    *,
    depth_pc: float,
    line_of_sight: Sequence[float],
) -> np.ndarray:
    direction = _distance_map_sky_direction(ra_deg, dec_deg)
    norm = float(np.linalg.norm(direction))
    if norm <= 1e-12:
        return np.asarray(tuple(line_of_sight), dtype=float) * float(depth_pc)
    direction = direction / norm
    los = np.asarray(tuple(line_of_sight), dtype=float)
    dot_los = float(np.dot(direction, los))
    if dot_los <= 1e-9:
        return los * float(depth_pc)
    return direction * (float(depth_pc) / dot_los)


def distance_map_imaging_axes_from_field(solved_field: SolvedField, wcs: WCS) -> DistanceMapImagingAxes:
    line_of_sight = distance_map_tomography_line_of_sight(solved_field, wcs)
    width = max(1, int(solved_field.width))
    height = max(1, int(solved_field.height))
    depth_pc = 100.0
    top_left = distance_map_ray_plane_point(
        *distance_map_display_pixel_to_world(wcs, 0.0, 0.0, image_height=height),
        depth_pc=depth_pc,
        line_of_sight=line_of_sight,
    )
    top_right = distance_map_ray_plane_point(
        *distance_map_display_pixel_to_world(wcs, float(width - 1), 0.0, image_height=height),
        depth_pc=depth_pc,
        line_of_sight=line_of_sight,
    )
    bottom_left = distance_map_ray_plane_point(
        *distance_map_display_pixel_to_world(wcs, 0.0, float(height - 1), image_height=height),
        depth_pc=depth_pc,
        line_of_sight=line_of_sight,
    )
    image_x = top_right - top_left
    image_y = bottom_left - top_left
    image_x = image_x - (line_of_sight * float(np.dot(image_x, line_of_sight)))
    image_y = image_y - (line_of_sight * float(np.dot(image_y, line_of_sight)))
    image_x_norm = float(np.linalg.norm(image_x))
    image_y_norm = float(np.linalg.norm(image_y))
    if image_x_norm <= 1e-12:
        image_x = np.cross(line_of_sight, np.array([0.0, 0.0, 1.0], dtype=float))
        image_x_norm = float(np.linalg.norm(image_x))
    if image_y_norm <= 1e-12:
        image_y = np.cross(image_x, line_of_sight)
        image_y_norm = float(np.linalg.norm(image_y))
    east = image_x / max(image_x_norm, 1e-12)
    north = image_y / max(image_y_norm, 1e-12)
    return DistanceMapImagingAxes(
        line_of_sight=(float(line_of_sight[0]), float(line_of_sight[1]), float(line_of_sight[2])),
        east=(float(east[0]), float(east[1]), float(east[2])),
        north=(float(north[0]), float(north[1]), float(north[2])),
    )


def distance_map_tomography_plane_transform_from_field(
    solved_field: SolvedField,
    wcs: WCS,
    depth_pc: float,
    *,
    texture_shape: tuple[int, int] | None = None,
) -> tuple[np.ndarray, DistanceMapImagingAxes]:
    field_width = max(1, int(solved_field.width))
    field_height = max(1, int(solved_field.height))
    if texture_shape is not None:
        texture_rows = max(1, int(texture_shape[0]))
        texture_cols = max(1, int(texture_shape[1]))
    else:
        texture_rows = field_height
        texture_cols = field_width
    line_of_sight = distance_map_tomography_line_of_sight(solved_field, wcs)
    top_left = distance_map_ray_plane_point(
        *distance_map_display_pixel_to_world(wcs, -0.5, -0.5, image_height=field_height),
        depth_pc=depth_pc,
        line_of_sight=line_of_sight,
    )
    top_right = distance_map_ray_plane_point(
        *distance_map_display_pixel_to_world(wcs, float(field_width) - 0.5, -0.5, image_height=field_height),
        depth_pc=depth_pc,
        line_of_sight=line_of_sight,
    )
    bottom_left = distance_map_ray_plane_point(
        *distance_map_display_pixel_to_world(wcs, -0.5, float(field_height) - 0.5, image_height=field_height),
        depth_pc=depth_pc,
        line_of_sight=line_of_sight,
    )
    matrix = np.eye(4, dtype=float)
    # GLImageItem spans local [0, texture_rows] x [0, texture_cols] across the full pixel grid.
    matrix[:3, 0] = (bottom_left - top_left) / float(texture_rows)
    matrix[:3, 1] = (top_right - top_left) / float(texture_cols)
    matrix[:3, 2] = -line_of_sight
    matrix[:3, 3] = top_left
    axes = distance_map_imaging_axes_from_field(solved_field, wcs)
    return matrix, axes


def distance_map_tomography_depth_range(
    stars: Sequence[DistanceMapStar],
    line_of_sight: Sequence[float],
) -> tuple[float, float]:
    if not stars:
        return (0.0, 100.0)
    los = np.asarray(tuple(line_of_sight), dtype=float)
    depths = distance_map_positions(stars) @ los
    depth_min = float(np.min(depths))
    depth_max = float(np.max(depths))
    span = max(1.0, depth_max - depth_min)
    margin = max(1.0, span * 0.05)
    return (depth_min - margin, depth_max + margin)


def distance_map_tomography_default_depth(
    stars: Sequence[DistanceMapStar],
    line_of_sight: Sequence[float],
) -> float:
    depth_min, depth_max = distance_map_tomography_depth_range(stars, line_of_sight)
    return 0.5 * (depth_min + depth_max)


def distance_map_tomography_plane_size_pc(
    solved_field: SolvedField,
    depth_pc: float,
) -> tuple[float, float]:
    depth_value = max(1.0, float(depth_pc))
    radius_rad = math.radians(max(1e-4, float(solved_field.radius_deg)))
    half_extent_pc = depth_value * math.tan(radius_rad)
    width_pc = 2.0 * half_extent_pc
    height_pc = width_pc * (float(solved_field.height) / max(1.0, float(solved_field.width)))
    return width_pc, height_pc


def distance_map_tomography_plane_transform(
    *,
    line_of_sight: Sequence[float],
    east: Sequence[float],
    north: Sequence[float],
    depth_pc: float,
    width_pc: float,
    height_pc: float,
    texture_width: int,
    texture_height: int,
) -> np.ndarray:
    los = np.asarray(tuple(line_of_sight), dtype=float)
    east_vector = np.asarray(tuple(east), dtype=float)
    north_vector = np.asarray(tuple(north), dtype=float)
    center = los * float(depth_pc)
    tex_width = max(1, int(texture_width))
    tex_height = max(1, int(texture_height))
    scale_x = float(width_pc) / float(tex_width)
    scale_y = float(height_pc) / float(tex_height)
    matrix = np.eye(4, dtype=float)
    matrix[:3, 0] = east_vector * scale_x
    matrix[:3, 1] = north_vector * scale_y
    matrix[:3, 2] = -los
    matrix[:3, 3] = center - (east_vector * (float(width_pc) * 0.5)) - (north_vector * (float(height_pc) * 0.5))
    return matrix


DISTANCE_MAP_DEFAULT_STAR_MAGNITUDE = 13.0
DISTANCE_MAP_MIN_POINT_SIZE = 2.0
DISTANCE_MAP_MAX_POINT_SIZE = 10.0
_DISTANCE_MAP_BP_RP_COLOR_ANCHORS: tuple[tuple[float, tuple[int, int, int]], ...] = (
    (-0.4, (56, 112, 255)),
    (0.0, (96, 154, 255)),
    (0.4, (176, 206, 255)),
    (0.8, (255, 245, 214)),
    (1.2, (255, 214, 142)),
    (1.6, (255, 168, 78)),
    (2.0, (255, 120, 28)),
    (2.5, (255, 76, 6)),
    (3.0, (240, 32, 0)),
)
_DISTANCE_MAP_UNKNOWN_STAR_COLOR = (0.45, 0.88, 0.42, 0.92)


def distance_map_reference_magnitude(stars: Sequence[DistanceMapStar]) -> float:
    magnitudes = [float(star.magnitude) for star in stars if star.magnitude is not None and math.isfinite(star.magnitude)]
    if not magnitudes:
        return DISTANCE_MAP_DEFAULT_STAR_MAGNITUDE
    return float(np.median(np.asarray(magnitudes, dtype=float)))


def distance_map_star_color_rgba(star: DistanceMapStar) -> tuple[float, float, float, float]:
    return _distance_map_color_rgba_from_bp_rp(star.bp_rp)


def distance_map_star_color_hex(star: DistanceMapStar) -> str:
    red, green, blue, _alpha = distance_map_star_color_rgba(star)
    return f"#{int(round(red * 255.0)):02x}{int(round(green * 255.0)):02x}{int(round(blue * 255.0)):02x}"


def distance_map_star_point_size(
    star: DistanceMapStar,
    *,
    reference_magnitude: float = DISTANCE_MAP_DEFAULT_STAR_MAGNITUDE,
) -> float:
    magnitude = star.magnitude
    if magnitude is None or not math.isfinite(magnitude):
        magnitude = reference_magnitude
    size = 13.0 - float(magnitude)
    return max(DISTANCE_MAP_MIN_POINT_SIZE, min(DISTANCE_MAP_MAX_POINT_SIZE, size))


def _distance_map_color_rgba_from_bp_rp(bp_rp: float | None) -> tuple[float, float, float, float]:
    if bp_rp is None or not math.isfinite(bp_rp):
        return _DISTANCE_MAP_UNKNOWN_STAR_COLOR
    color_index = float(bp_rp)
    anchors = _DISTANCE_MAP_BP_RP_COLOR_ANCHORS
    if color_index <= anchors[0][0]:
        red, green, blue = anchors[0][1]
        return _distance_map_rgb_to_rgba(red, green, blue)
    if color_index >= anchors[-1][0]:
        red, green, blue = anchors[-1][1]
        return _distance_map_rgb_to_rgba(red, green, blue)
    for (left_index, left_rgb), (right_index, right_rgb) in zip(anchors, anchors[1:], strict=False):
        if left_index <= color_index <= right_index:
            span = max(1e-9, right_index - left_index)
            ratio = (color_index - left_index) / span
            red = int(round(left_rgb[0] + ((right_rgb[0] - left_rgb[0]) * ratio)))
            green = int(round(left_rgb[1] + ((right_rgb[1] - left_rgb[1]) * ratio)))
            blue = int(round(left_rgb[2] + ((right_rgb[2] - left_rgb[2]) * ratio)))
            return _distance_map_rgb_to_rgba(red, green, blue)
    return _DISTANCE_MAP_UNKNOWN_STAR_COLOR


def _distance_map_rgb_to_rgba(red: int, green: int, blue: int, *, alpha: float = 0.92) -> tuple[float, float, float, float]:
    return (
        max(0.0, min(1.0, red / 255.0)),
        max(0.0, min(1.0, green / 255.0)),
        max(0.0, min(1.0, blue / 255.0)),
        alpha,
    )


def distance_map_pixel_position(
    star: DistanceMapStar,
    *,
    solved_field: SolvedField,
    wcs: object,
) -> tuple[float, float] | None:
    try:
        pixel_x, pixel_y = wcs.world_to_pixel_values(float(star.ra_deg), float(star.dec_deg))  # type: ignore[attr-defined]
    except Exception:
        return None
    if not math.isfinite(pixel_x) or not math.isfinite(pixel_y):
        return None
    if pixel_x < 0.0 or pixel_y < 0.0 or pixel_x >= float(solved_field.width) or pixel_y >= float(solved_field.height):
        return None
    return float(pixel_x), float(pixel_y)
