from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, replace
from functools import lru_cache
from importlib import resources
from typing import Sequence

import numpy as np
from astropy import units as u
from astropy.coordinates import SkyCoord

from photometry_app.core.distance_map import DistanceMapStar, parallax_to_distance_pc, star_cartesian_pc
from photometry_app.core.distance_map_clusters import (
    DistanceMapClusterSettings,
    find_distance_map_cluster,
)

_UNCERTAINTY_SEGMENT_LIMIT = 2500
_GLOBULAR_CLUSTER_POINT_SEARCH_RADIUS_DEG = 1.5
_GLOBULAR_CLUSTER_FOOTPRINT_MARGIN_DEG = 0.35
_GLOBULAR_CLUSTER_MIN_STARS_FOR_DENSITY_MATCH = 5
_GLOBULAR_CLUSTER_VISIBLE_RADIUS_FRACTION = 0.28
_GLOBULAR_CLUSTER_VISIBLE_RADIUS_MAX_DEG = 0.28
_GLOBULAR_CLUSTER_VISIBLE_RADIUS_MEMBER_SCALE = 1.15
_GLOBULAR_CLUSTER_TOY_DEPTH_EXPONENT = 2.2
_SPHERE_WIREFRAME_SEGMENTS = 36


@dataclass(frozen=True, slots=True)
class DistanceMapDisplayOptions:
    model_cluster_depth: bool = True
    show_parallax_uncertainty: bool = False
    use_external_cluster_catalog: bool = False

    def normalized(self) -> DistanceMapDisplayOptions:
        return DistanceMapDisplayOptions(
            model_cluster_depth=bool(self.model_cluster_depth),
            show_parallax_uncertainty=bool(self.show_parallax_uncertainty),
            use_external_cluster_catalog=bool(self.use_external_cluster_catalog),
        )


@dataclass(frozen=True, slots=True)
class GlobularClusterCatalogEntry:
    name: str
    names: tuple[str, ...]
    ra_deg: float
    dec_deg: float
    distance_pc: float
    radius_pc: float


@dataclass(frozen=True, slots=True)
class DistanceMapDisplayResult:
    display_stars: tuple[DistanceMapStar, ...]
    uncertainty_segments: np.ndarray | None
    sphere_wireframe: tuple[np.ndarray, ...] | None
    catalog_entry: GlobularClusterCatalogEntry | None
    cluster_distance_pc: float | None
    effective_member_indices: frozenset[int]
    auto_detected_members: bool
    notes: tuple[str, ...]


def coerce_distance_map_display_options(
    *,
    model_cluster_depth: object = True,
    show_parallax_uncertainty: object = False,
    use_external_cluster_catalog: object = False,
) -> DistanceMapDisplayOptions:
    return DistanceMapDisplayOptions(
        model_cluster_depth=_coerce_bool(model_cluster_depth, default=True),
        show_parallax_uncertainty=_coerce_bool(show_parallax_uncertainty, default=False),
        use_external_cluster_catalog=_coerce_bool(use_external_cluster_catalog, default=False),
    ).normalized()


def _coerce_bool(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


@lru_cache(maxsize=1)
def load_globular_cluster_catalog() -> tuple[GlobularClusterCatalogEntry, ...]:
    data_path = resources.files("photometry_app").joinpath("data/globular_clusters.json")
    payload = json.loads(data_path.read_text(encoding="utf-8"))
    entries: list[GlobularClusterCatalogEntry] = []
    for raw_entry in payload:
        if not isinstance(raw_entry, dict):
            continue
        name = str(raw_entry.get("name", "")).strip()
        if not name:
            continue
        names_raw = raw_entry.get("names", [name])
        if not isinstance(names_raw, list):
            names_raw = [name]
        names = tuple(str(item).strip() for item in names_raw if str(item).strip())
        if not names:
            names = (name,)
        try:
            ra_deg = float(raw_entry["ra_deg"])
            dec_deg = float(raw_entry["dec_deg"])
            distance_pc = float(raw_entry["distance_pc"])
            radius_pc = float(raw_entry["radius_pc"])
        except (KeyError, TypeError, ValueError):
            continue
        if distance_pc <= 0.0 or radius_pc <= 0.0:
            continue
        entries.append(
            GlobularClusterCatalogEntry(
                name=name,
                names=names,
                ra_deg=ra_deg,
                dec_deg=dec_deg,
                distance_pc=distance_pc,
                radius_pc=radius_pc,
            )
        )
    return tuple(entries)


def angular_separation_deg(ra_a_deg: float, dec_a_deg: float, ra_b_deg: float, dec_b_deg: float) -> float:
    coord_a = SkyCoord(ra=ra_a_deg * u.deg, dec=dec_a_deg * u.deg, frame="icrs")
    coord_b = SkyCoord(ra=ra_b_deg * u.deg, dec=dec_b_deg * u.deg, frame="icrs")
    return float(coord_a.separation(coord_b).to_value(u.deg))


def catalog_angular_radius_deg(entry: GlobularClusterCatalogEntry) -> float:
    return float(math.degrees(math.atan(entry.radius_pc / entry.distance_pc)))


def catalog_visible_radius_pc(entry: GlobularClusterCatalogEntry) -> float:
    visual_cap_pc = float(entry.distance_pc) * math.tan(math.radians(_GLOBULAR_CLUSTER_VISIBLE_RADIUS_MAX_DEG))
    return max(4.0, min(float(entry.radius_pc) * _GLOBULAR_CLUSTER_VISIBLE_RADIUS_FRACTION, visual_cap_pc))


def catalog_visible_angular_radius_deg(entry: GlobularClusterCatalogEntry) -> float:
    return float(math.degrees(math.atan(catalog_visible_radius_pc(entry) / entry.distance_pc)))


def find_globular_cluster_catalog_match(
    ra_deg: float,
    dec_deg: float,
    *,
    search_radius_deg: float = _GLOBULAR_CLUSTER_POINT_SEARCH_RADIUS_DEG,
) -> GlobularClusterCatalogEntry | None:
    best_entry: GlobularClusterCatalogEntry | None = None
    best_separation = float(search_radius_deg)
    for entry in load_globular_cluster_catalog():
        separation = angular_separation_deg(ra_deg, dec_deg, entry.ra_deg, entry.dec_deg)
        if separation <= best_separation:
            best_entry = entry
            best_separation = separation
    return best_entry


def _catalog_star_count_near_entry(
    stars: Sequence[DistanceMapStar],
    entry: GlobularClusterCatalogEntry,
) -> int:
    search_radius_deg = catalog_visible_angular_radius_deg(entry) * _GLOBULAR_CLUSTER_VISIBLE_RADIUS_MEMBER_SCALE
    return sum(
        1
        for star in stars
        if angular_separation_deg(star.ra_deg, star.dec_deg, entry.ra_deg, entry.dec_deg) <= search_radius_deg
    )


def find_globular_cluster_catalog_match_for_field(
    field_center_ra_deg: float,
    field_center_dec_deg: float,
    field_radius_deg: float,
    stars: Sequence[DistanceMapStar],
    *,
    member_indices: frozenset[int] | None = None,
) -> GlobularClusterCatalogEntry | None:
    members = tuple(stars[index] for index in sorted(member_indices or ()) if 0 <= index < len(stars))
    lookup_positions = [(float(field_center_ra_deg), float(field_center_dec_deg))]
    if members:
        lookup_positions.append(_mean_sky_position_deg(members))

    best_entry: GlobularClusterCatalogEntry | None = None
    best_score = -1.0
    field_radius = max(0.1, float(field_radius_deg))
    for entry in load_globular_cluster_catalog():
        angular_radius = catalog_visible_angular_radius_deg(entry)
        footprint_limit = field_radius + angular_radius + _GLOBULAR_CLUSTER_FOOTPRINT_MARGIN_DEG
        in_footprint = any(
            angular_separation_deg(lookup_ra, lookup_dec, entry.ra_deg, entry.dec_deg) <= footprint_limit
            for lookup_ra, lookup_dec in lookup_positions
        )
        star_count = _catalog_star_count_near_entry(stars, entry)
        density_match = star_count >= _GLOBULAR_CLUSTER_MIN_STARS_FOR_DENSITY_MATCH
        if not in_footprint and not density_match:
            continue
        center_separation = angular_separation_deg(
            field_center_ra_deg,
            field_center_dec_deg,
            entry.ra_deg,
            entry.dec_deg,
        )
        score = float(star_count) * 10.0
        if in_footprint:
            score += 100.0
        score -= center_separation
        if score > best_score:
            best_entry = entry
            best_score = score
    return best_entry


def _filter_indices_to_catalog_visible_radius(
    stars: Sequence[DistanceMapStar],
    member_indices: frozenset[int],
    entry: GlobularClusterCatalogEntry,
) -> frozenset[int]:
    catalog_limit_deg = catalog_visible_angular_radius_deg(entry) * _GLOBULAR_CLUSTER_VISIBLE_RADIUS_MEMBER_SCALE
    return frozenset(
        index
        for index in member_indices
        if 0 <= index < len(stars)
        and angular_separation_deg(
            stars[index].ra_deg,
            stars[index].dec_deg,
            entry.ra_deg,
            entry.dec_deg,
        )
        <= catalog_limit_deg
    )


def resolve_display_member_indices(
    stars: Sequence[DistanceMapStar],
    manual_member_indices: frozenset[int],
    options: DistanceMapDisplayOptions,
    *,
    catalog_entry: GlobularClusterCatalogEntry | None,
    cluster_settings: DistanceMapClusterSettings | None = None,
) -> tuple[frozenset[int], bool]:
    if manual_member_indices:
        if catalog_entry is not None:
            filtered_indices = _filter_indices_to_catalog_visible_radius(stars, manual_member_indices, catalog_entry)
            if len(filtered_indices) >= _GLOBULAR_CLUSTER_MIN_STARS_FOR_DENSITY_MATCH:
                return filtered_indices, False
        return manual_member_indices, False

    normalized = options.normalized()
    if not normalized.model_cluster_depth:
        return frozenset(), False

    settings = (cluster_settings or DistanceMapClusterSettings()).normalized()
    cluster_result = find_distance_map_cluster(
        stars,
        strictness=settings.strictness,
        method=settings.method,
        parallax_mode=settings.parallax_mode,
        refine_magnitude_consistency=settings.refine_magnitude_consistency,
    )
    if cluster_result is None:
        return frozenset(), False

    member_indices = frozenset(cluster_result.member_indices)
    if catalog_entry is not None:
        filtered_indices = _filter_indices_to_catalog_visible_radius(stars, member_indices, catalog_entry)
        if len(filtered_indices) >= 5:
            member_indices = filtered_indices

    if not member_indices:
        return frozenset(), False
    return member_indices, True


def _stable_uniform01(key: str) -> float:
    digest = hashlib.md5(str(key).encode("utf-8")).hexdigest()
    return int(digest[:8], 16) / float(0xFFFFFFFF)


def toy_globular_cluster_member_distance_pc(
    star: DistanceMapStar,
    *,
    cluster_distance_pc: float,
    cluster_radius_pc: float,
    cluster_center_ra_deg: float,
    cluster_center_dec_deg: float,
) -> float:
    """Place the star along its observed sightline through a center-weighted toy sphere."""
    cluster_distance_pc = max(1.0, float(cluster_distance_pc))
    cluster_radius_pc = max(1.0, float(cluster_radius_pc))
    line_of_sight = _unit_direction(star.ra_deg, star.dec_deg)
    cluster_axis = _unit_direction(cluster_center_ra_deg, cluster_center_dec_deg)
    cluster_center = cluster_axis * cluster_distance_pc
    closest_distance_pc = max(1.0, float(np.dot(cluster_center, line_of_sight)))
    closest_point = line_of_sight * closest_distance_pc
    projected_radius_pc = float(np.linalg.norm(closest_point - cluster_center))
    if projected_radius_pc >= cluster_radius_pc:
        return closest_distance_pc

    depth_capacity_pc = cluster_radius_pc * math.sqrt(max(0.0, 1.0 - (projected_radius_pc / cluster_radius_pc) ** 2))
    sign = -1.0 if _stable_uniform01(f"{star.source_id}:sign") < 0.5 else 1.0
    depth_fraction = _stable_uniform01(f"{star.source_id}:depth") ** _GLOBULAR_CLUSTER_TOY_DEPTH_EXPONENT
    depth_offset_pc = sign * depth_capacity_pc * depth_fraction
    return closest_distance_pc + depth_offset_pc


def estimate_cluster_radius_pc(
    members: Sequence[DistanceMapStar],
    cluster_distance_pc: float,
    catalog_entry: GlobularClusterCatalogEntry | None,
) -> float:
    if catalog_entry is not None:
        return catalog_visible_radius_pc(catalog_entry)
    if not members:
        return max(10.0, float(cluster_distance_pc) * 0.01)
    center_ra_deg, center_dec_deg = _mean_sky_position_deg(members)
    max_theta_deg = max(
        angular_separation_deg(star.ra_deg, star.dec_deg, center_ra_deg, center_dec_deg) for star in members
    )
    transverse_pc = float(cluster_distance_pc) * math.tan(math.radians(max(max_theta_deg, 1e-6)))
    return max(10.0, min(float(cluster_distance_pc) * 0.08, transverse_pc * 0.65))


def apply_cluster_depth_model(
    display_stars: list[DistanceMapStar],
    member_indices: frozenset[int],
    *,
    cluster_distance_pc: float,
    cluster_radius_pc: float,
    cluster_center_ra_deg: float,
    cluster_center_dec_deg: float,
) -> None:
    for index in member_indices:
        if index < 0 or index >= len(display_stars):
            continue
        star = display_stars[index]
        distance_pc = toy_globular_cluster_member_distance_pc(
            star,
            cluster_distance_pc=cluster_distance_pc,
            cluster_radius_pc=cluster_radius_pc,
            cluster_center_ra_deg=cluster_center_ra_deg,
            cluster_center_dec_deg=cluster_center_dec_deg,
        )
        display_stars[index] = _star_at_distance(star, distance_pc)


def cluster_mean_distance_pc(members: Sequence[DistanceMapStar]) -> float | None:
    if not members:
        return None
    weighted_parallax_sum = 0.0
    weight_sum = 0.0
    distances: list[float] = []
    for star in members:
        distances.append(float(star.distance_pc))
        if star.parallax_mas is None or star.parallax_error_mas is None:
            continue
        if star.parallax_mas <= 0.0 or star.parallax_error_mas <= 0.0:
            continue
        if not math.isfinite(star.parallax_mas) or not math.isfinite(star.parallax_error_mas):
            continue
        weight = 1.0 / float(star.parallax_error_mas) ** 2
        weighted_parallax_sum += float(star.parallax_mas) * weight
        weight_sum += weight
    if weight_sum > 0.0:
        mean_parallax = weighted_parallax_sum / weight_sum
        if mean_parallax > 0.0:
            return float(1000.0 / mean_parallax)
    if distances:
        return float(np.median(distances))
    return None


def parallax_distance_interval_pc(
    parallax_mas: float | None,
    parallax_error_mas: float | None,
) -> tuple[float, float] | None:
    if parallax_mas is None or parallax_error_mas is None:
        return None
    if not math.isfinite(parallax_mas) or not math.isfinite(parallax_error_mas):
        return None
    if parallax_mas <= 0.0 or parallax_error_mas <= 0.0:
        return None
    nearer_distance = parallax_to_distance_pc(parallax_mas + parallax_error_mas)
    farther_distance = parallax_to_distance_pc(max(parallax_mas - parallax_error_mas, 1e-6))
    if nearer_distance is None or farther_distance is None:
        return None
    near_pc = min(nearer_distance, farther_distance)
    far_pc = max(nearer_distance, farther_distance)
    if not math.isfinite(near_pc) or not math.isfinite(far_pc) or near_pc <= 0.0 or far_pc <= 0.0:
        return None
    return float(near_pc), float(far_pc)


def _mean_sky_position_deg(stars: Sequence[DistanceMapStar]) -> tuple[float, float]:
    if not stars:
        return 0.0, 0.0
    # Average unit vectors in Cartesian space. SkyCoord.mean() is unavailable
    # on some Astropy versions, so avoid relying on that API.
    directions = np.asarray(
        [_unit_direction(star.ra_deg, star.dec_deg) for star in stars],
        dtype=float,
    )
    mean_direction = directions.mean(axis=0)
    norm = float(np.linalg.norm(mean_direction))
    if norm <= 0.0:
        return float(stars[0].ra_deg), float(stars[0].dec_deg)
    x_pc, y_pc, z_pc = mean_direction / norm
    ra_deg = math.degrees(math.atan2(y_pc, x_pc)) % 360.0
    dec_deg = math.degrees(math.asin(max(-1.0, min(1.0, z_pc))))
    return float(ra_deg), float(dec_deg)


def _star_at_distance(star: DistanceMapStar, distance_pc: float) -> DistanceMapStar:
    x_pc, y_pc, z_pc = star_cartesian_pc(star.ra_deg, star.dec_deg, distance_pc)
    return replace(star, distance_pc=float(distance_pc), x_pc=x_pc, y_pc=y_pc, z_pc=z_pc)


def _unit_direction(ra_deg: float, dec_deg: float) -> np.ndarray:
    x_pc, y_pc, z_pc = star_cartesian_pc(ra_deg, dec_deg, 1.0)
    direction = np.asarray((x_pc, y_pc, z_pc), dtype=float)
    norm = float(np.linalg.norm(direction))
    if norm <= 0.0:
        return np.asarray((0.0, 0.0, 1.0), dtype=float)
    return direction / norm


def globular_cluster_sphere_wireframe(
    ra_deg: float,
    dec_deg: float,
    distance_pc: float,
    radius_pc: float,
    *,
    segments: int = _SPHERE_WIREFRAME_SEGMENTS,
) -> tuple[np.ndarray, ...]:
    los = _unit_direction(ra_deg, dec_deg)
    center = los * float(distance_pc)
    reference = np.asarray((0.0, 0.0, 1.0), dtype=float)
    if abs(float(los[2])) > 0.9:
        reference = np.asarray((1.0, 0.0, 0.0), dtype=float)
    east = np.cross(los, reference)
    east_norm = float(np.linalg.norm(east))
    if east_norm <= 0.0:
        east = np.asarray((1.0, 0.0, 0.0), dtype=float)
    else:
        east = east / east_norm
    north = np.cross(los, east)
    north = north / max(float(np.linalg.norm(north)), 1e-12)
    diagonal = east + north
    diagonal = diagonal / max(float(np.linalg.norm(diagonal)), 1e-12)

    theta = np.linspace(0.0, 2.0 * math.pi, max(8, int(segments)) + 1)
    circle_bases = (east, north, diagonal)
    line_segments: list[np.ndarray] = []
    for basis_a, basis_b in ((circle_bases[0], circle_bases[1]), (circle_bases[0], los), (circle_bases[1], los)):
        circle = center + float(radius_pc) * (
            np.cos(theta)[:, None] * basis_a[None, :] + np.sin(theta)[:, None] * basis_b[None, :]
        )
        line_segments.append(np.ascontiguousarray(circle, dtype=float))
    return tuple(line_segments)


def _uncertainty_candidate_indices(
    stars: Sequence[DistanceMapStar],
    member_indices: frozenset[int],
    visible_indices: Sequence[int],
) -> list[int]:
    if member_indices:
        candidates = [index for index in visible_indices if index in member_indices]
    else:
        candidates = list(visible_indices)
    if len(candidates) <= _UNCERTAINTY_SEGMENT_LIMIT:
        return candidates

    def sort_key(index: int) -> tuple[float, float]:
        star = stars[index]
        magnitude = star.magnitude if star.magnitude is not None else 99.0
        return (magnitude, float(index))

    candidates.sort(key=sort_key)
    return candidates[:_UNCERTAINTY_SEGMENT_LIMIT]


def build_parallax_uncertainty_segments(
    stars: Sequence[DistanceMapStar],
    *,
    member_indices: frozenset[int],
    visible_indices: Sequence[int],
) -> np.ndarray | None:
    candidate_indices = _uncertainty_candidate_indices(stars, member_indices, visible_indices)
    segments: list[list[float]] = []
    for index in candidate_indices:
        star = stars[index]
        interval = parallax_distance_interval_pc(star.parallax_mas, star.parallax_error_mas)
        if interval is None:
            continue
        near_pc, far_pc = interval
        near_position = star_cartesian_pc(star.ra_deg, star.dec_deg, near_pc)
        far_position = star_cartesian_pc(star.ra_deg, star.dec_deg, far_pc)
        segments.append(list(near_position))
        segments.append(list(far_position))
    if not segments:
        return None
    return np.ascontiguousarray(segments, dtype=float)


def prepare_distance_map_display(
    stars: tuple[DistanceMapStar, ...],
    *,
    member_indices: frozenset[int],
    options: DistanceMapDisplayOptions,
    field_center_ra_deg: float,
    field_center_dec_deg: float,
    field_radius_deg: float,
    cluster_settings: DistanceMapClusterSettings | None = None,
) -> DistanceMapDisplayResult:
    normalized = options.normalized()
    notes: list[str] = []
    display_stars = list(stars)
    cluster_distance_pc: float | None = None
    catalog_entry: GlobularClusterCatalogEntry | None = None

    if normalized.use_external_cluster_catalog:
        catalog_entry = find_globular_cluster_catalog_match_for_field(
            field_center_ra_deg,
            field_center_dec_deg,
            field_radius_deg,
            stars,
            member_indices=member_indices,
        )
        if catalog_entry is None:
            notes.append("No named globular cluster matched the current field for catalog distances.")
        else:
            notes.append(
                f"Matched catalog cluster {catalog_entry.name} "
                f"({catalog_entry.distance_pc:.0f} pc, r={catalog_entry.radius_pc:.0f} pc)."
            )

    effective_member_indices, auto_detected_members = resolve_display_member_indices(
        stars,
        member_indices,
        normalized,
        catalog_entry=catalog_entry,
        cluster_settings=cluster_settings,
    )
    members = tuple(stars[index] for index in sorted(effective_member_indices) if 0 <= index < len(stars))

    if auto_detected_members and effective_member_indices:
        notes.append(
            f"Auto-detected {len(effective_member_indices)} moving-group member(s) for cluster depth modeling."
        )

    if normalized.model_cluster_depth and members:
        if catalog_entry is not None and normalized.use_external_cluster_catalog:
            cluster_distance_pc = float(catalog_entry.distance_pc)
            cluster_center_ra_deg = float(catalog_entry.ra_deg)
            cluster_center_dec_deg = float(catalog_entry.dec_deg)
            notes.append(
                f"Cluster members placed in a toy globular model at {cluster_distance_pc:.0f} pc."
            )
        else:
            cluster_distance_pc = cluster_mean_distance_pc(members)
            cluster_center_ra_deg, cluster_center_dec_deg = _mean_sky_position_deg(members)
            if cluster_distance_pc is not None:
                notes.append(
                    f"Cluster members placed in a toy globular model around {cluster_distance_pc:.0f} pc."
                )
        if cluster_distance_pc is not None:
            cluster_radius_pc = estimate_cluster_radius_pc(members, cluster_distance_pc, catalog_entry)
            apply_cluster_depth_model(
                display_stars,
                effective_member_indices,
                cluster_distance_pc=cluster_distance_pc,
                cluster_radius_pc=cluster_radius_pc,
                cluster_center_ra_deg=cluster_center_ra_deg,
                cluster_center_dec_deg=cluster_center_dec_deg,
            )
            notes.append(
                f"Applied center-weighted globular depth model with radius {cluster_radius_pc:.0f} pc."
            )

    uncertainty_segments: np.ndarray | None = None
    if normalized.show_parallax_uncertainty:
        visible_indices = list(range(len(stars)))
        uncertainty_segments = build_parallax_uncertainty_segments(
            stars,
            member_indices=effective_member_indices,
            visible_indices=visible_indices,
        )
        if uncertainty_segments is None:
            notes.append("Parallax uncertainty segments were requested but no usable parallax errors were available.")
        else:
            segment_count = int(uncertainty_segments.shape[0] // 2)
            notes.append(f"Drew parallax uncertainty for {segment_count} star(s).")

    sphere_wireframe: tuple[np.ndarray, ...] | None = None
    if normalized.use_external_cluster_catalog and catalog_entry is not None:
        sphere_radius_pc = catalog_visible_radius_pc(catalog_entry)
        sphere_wireframe = globular_cluster_sphere_wireframe(
            catalog_entry.ra_deg,
            catalog_entry.dec_deg,
            catalog_entry.distance_pc,
            sphere_radius_pc,
        )
        notes.append(f"Rendered visible-core sphere for {catalog_entry.name} (r={sphere_radius_pc:.0f} pc).")

    return DistanceMapDisplayResult(
        display_stars=tuple(display_stars),
        uncertainty_segments=uncertainty_segments,
        sphere_wireframe=sphere_wireframe,
        catalog_entry=catalog_entry,
        cluster_distance_pc=cluster_distance_pc,
        effective_member_indices=effective_member_indices,
        auto_detected_members=auto_detected_members,
        notes=tuple(notes),
    )
