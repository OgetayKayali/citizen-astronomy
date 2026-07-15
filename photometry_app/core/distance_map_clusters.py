from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Literal, Protocol, Sequence

import numpy as np

DistanceMapClusteringMethod = Literal["lightweight", "sklearn"]
DistanceMapClusterRequestedMethod = Literal["auto", "lightweight", "sklearn"]
DistanceMapClusterParallaxMode = Literal["auto", "always", "never"]
DistanceMapClusterPreset = Literal["default", "tight", "loose", "parallax", "custom"]


_DISTANCE_MAP_CLUSTER_PRESET_DEFAULTS: dict[
    str,
    tuple[DistanceMapClusterRequestedMethod, float, DistanceMapClusterParallaxMode, bool],
] = {
    "default": ("auto", 1.0, "auto", True),
    "tight": ("auto", 0.8, "auto", True),
    "loose": ("auto", 1.35, "never", False),
    "parallax": ("auto", 0.95, "always", True),
}


@dataclass(frozen=True, slots=True)
class DistanceMapClusterSettings:
    preset: DistanceMapClusterPreset = "default"
    method: DistanceMapClusterRequestedMethod = "auto"
    strictness: float = 1.0
    parallax_mode: DistanceMapClusterParallaxMode = "auto"
    refine_magnitude_consistency: bool = True
    auto_filter: bool = False
    mark_on_image: bool = True

    def normalized(self) -> "DistanceMapClusterSettings":
        normalized_preset = normalize_distance_map_cluster_preset(self.preset)
        normalized_auto_filter = bool(self.auto_filter)
        normalized_mark_on_image = bool(self.mark_on_image)
        if normalized_preset != "custom":
            method, strictness, parallax_mode, refine_magnitude_consistency = _DISTANCE_MAP_CLUSTER_PRESET_DEFAULTS[
                normalized_preset
            ]
            return DistanceMapClusterSettings(
                preset=normalized_preset,
                method=method,
                strictness=strictness,
                parallax_mode=parallax_mode,
                refine_magnitude_consistency=refine_magnitude_consistency,
                auto_filter=normalized_auto_filter,
                mark_on_image=normalized_mark_on_image,
            )
        return DistanceMapClusterSettings(
            preset="custom",
            method=normalize_distance_map_cluster_requested_method(self.method),
            strictness=min(2.5, max(0.4, float(self.strictness))),
            parallax_mode=normalize_distance_map_cluster_parallax_mode(self.parallax_mode),
            refine_magnitude_consistency=bool(self.refine_magnitude_consistency),
            auto_filter=normalized_auto_filter,
            mark_on_image=normalized_mark_on_image,
        )


def normalize_distance_map_cluster_preset(value: str) -> DistanceMapClusterPreset:
    normalized = str(value).strip().lower()
    if normalized in _DISTANCE_MAP_CLUSTER_PRESET_DEFAULTS:
        return normalized  # type: ignore[return-value]
    return "custom" if normalized == "custom" else "default"


def normalize_distance_map_cluster_requested_method(value: str) -> DistanceMapClusterRequestedMethod:
    normalized = str(value).strip().lower()
    if normalized == "sklearn":
        return "sklearn"
    if normalized == "auto":
        return "auto"
    return "lightweight"


def normalize_distance_map_cluster_parallax_mode(value: str) -> DistanceMapClusterParallaxMode:
    normalized = str(value).strip().lower()
    if normalized == "always":
        return "always"
    if normalized == "never":
        return "never"
    return "auto"


def distance_map_cluster_preset_label(preset: str) -> str:
    normalized = normalize_distance_map_cluster_preset(preset)
    if normalized == "tight":
        return "Tight"
    if normalized == "loose":
        return "Loose"
    if normalized == "parallax":
        return "Parallax Priority"
    if normalized == "custom":
        return "Custom"
    return "Default"


def distance_map_cluster_preset_description(preset: str) -> str:
    normalized = normalize_distance_map_cluster_preset(preset)
    if normalized == "tight":
        return "Uses a stricter cluster threshold to emphasize the dense core of a moving group."
    if normalized == "loose":
        return "Expands the astrometric search for broader associations and ignores parallax when the field is sparse."
    if normalized == "parallax":
        return "Requires positive Gaia parallax for every candidate member."
    if normalized == "custom":
        return "Keeps your manual expert settings for backend, strictness, parallax usage, and magnitude cleanup."
    return "Recommended balanced search using automatic backend selection and automatic parallax use when enough stars support it."


def distance_map_cluster_method_label(method: str) -> str:
    normalized = normalize_distance_map_cluster_requested_method(method)
    if normalized == "sklearn":
        return "Sklearn DBSCAN"
    if normalized == "auto":
        return "Auto (recommended)"
    return "Lightweight"


def distance_map_cluster_parallax_mode_label(mode: str) -> str:
    normalized = normalize_distance_map_cluster_parallax_mode(mode)
    if normalized == "always":
        return "Require parallax"
    if normalized == "never":
        return "Proper motion only"
    return "Auto"


@dataclass(slots=True)
class DistanceMapClusterResult:
    member_indices: list[int]
    eligible_indices: list[int]
    cluster_count: int
    member_count: int
    astrometric_member_count: int
    refined_member_count: int
    used_parallax: bool
    used_magnitude_refinement: bool
    eps: float
    min_samples: int
    strictness: float
    clustering_method: DistanceMapClusteringMethod


class DistanceMapClusterStar(Protocol):
    pm_ra_mas_per_year: float | None
    pm_dec_mas_per_year: float | None
    parallax_mas: float | None
    magnitude: float | None
    bp_rp: float | None


class _DistanceMapClusterInput(Protocol):
    pm_ra_mas_per_year: float | None
    pm_dec_mas_per_year: float | None
    parallax_mas: float | None
    magnitude: float | None
    bp_rp: float | None


def find_distance_map_cluster(
    stars: Sequence[DistanceMapClusterStar],
    *,
    strictness: float = 1.0,
    method: DistanceMapClusterRequestedMethod = "auto",
    parallax_mode: DistanceMapClusterParallaxMode = "auto",
    refine_magnitude_consistency: bool = True,
) -> DistanceMapClusterResult | None:
    if not stars:
        return None

    normalized_strictness = min(2.5, max(0.4, float(strictness)))
    normalized_method = normalize_distance_map_cluster_requested_method(method)
    normalized_parallax_mode = normalize_distance_map_cluster_parallax_mode(parallax_mode)

    eligible_pm_indices = [
        index
        for index, star in enumerate(stars)
        if _finite_value(star.pm_ra_mas_per_year) is not None and _finite_value(star.pm_dec_mas_per_year) is not None
    ]
    if len(eligible_pm_indices) < 5:
        return None

    parallax_eligible_indices = [
        index
        for index in eligible_pm_indices
        if _finite_value(stars[index].parallax_mas) is not None and float(stars[index].parallax_mas) > 0.0
    ]
    if normalized_parallax_mode == "always":
        used_parallax = True
        eligible_indices = parallax_eligible_indices
    elif normalized_parallax_mode == "never":
        used_parallax = False
        eligible_indices = eligible_pm_indices
    else:
        used_parallax = len(parallax_eligible_indices) >= max(6, int(math.ceil(len(eligible_pm_indices) * 0.35)))
        eligible_indices = parallax_eligible_indices if used_parallax else eligible_pm_indices
    if len(eligible_indices) < 5:
        return None

    features = np.asarray(
        [_feature_vector(stars[index], include_parallax=used_parallax) for index in eligible_indices],
        dtype=float,
    )
    if features.ndim != 2 or features.shape[0] < 5:
        return None

    normalized_features = _robust_normalize(features)
    point_count = int(normalized_features.shape[0])
    min_samples = max(5, min(12, int(round(math.sqrt(point_count)))))
    if point_count <= min_samples:
        return None

    kth_neighbor_distances = _kth_neighbor_distances(normalized_features, k=max(1, min_samples - 1))
    finite_kth = kth_neighbor_distances[np.isfinite(kth_neighbor_distances)]
    if finite_kth.size == 0:
        return None
    eps = float(np.percentile(finite_kth, 30.0) * 1.25 * normalized_strictness)
    eps = min(2.4, max(0.25, eps))

    labels, clustering_method = _cluster_labels(
        normalized_features,
        eps=eps,
        min_samples=min_samples,
        method=_resolved_clustering_method(normalized_method, point_count=point_count),
    )
    unique_labels = [label for label in np.unique(labels).tolist() if int(label) >= 0]
    if not unique_labels:
        return None

    best_label = max(unique_labels, key=lambda label: int(np.count_nonzero(labels == label)))
    member_positions = np.flatnonzero(labels == best_label).tolist()
    if len(member_positions) < min_samples:
        return None

    astrometric_member_indices = [eligible_indices[position] for position in member_positions]
    member_indices = list(astrometric_member_indices)
    used_magnitude_refinement = False
    if refine_magnitude_consistency:
        refined_member_indices = _refine_members_by_magnitude_consistency(
            stars,
            astrometric_member_indices,
            strictness=normalized_strictness,
        )
        if refined_member_indices is not None and len(refined_member_indices) >= max(
            4, int(math.ceil(len(astrometric_member_indices) * 0.6))
        ):
            member_indices = refined_member_indices
            used_magnitude_refinement = len(member_indices) != len(astrometric_member_indices)

    return DistanceMapClusterResult(
        member_indices=member_indices,
        eligible_indices=list(eligible_indices),
        cluster_count=len(unique_labels),
        member_count=len(member_indices),
        astrometric_member_count=len(astrometric_member_indices),
        refined_member_count=len(member_indices),
        used_parallax=used_parallax,
        used_magnitude_refinement=used_magnitude_refinement,
        eps=eps,
        min_samples=min_samples,
        strictness=normalized_strictness,
        clustering_method=clustering_method,
    )


def _resolved_clustering_method(method: DistanceMapClusterRequestedMethod, *, point_count: int) -> DistanceMapClusteringMethod:
    if method == "sklearn":
        return "sklearn"
    if method == "auto" and point_count >= 180:
        return "sklearn"
    return "lightweight"


def _cluster_labels(
    features: np.ndarray,
    *,
    eps: float,
    min_samples: int,
    method: DistanceMapClusteringMethod,
) -> tuple[np.ndarray, DistanceMapClusteringMethod]:
    if method == "sklearn":
        try:
            from sklearn.cluster import DBSCAN
        except Exception:
            return _dbscan_labels(features, eps=eps, min_samples=min_samples), "lightweight"
        model = DBSCAN(eps=float(eps), min_samples=int(min_samples), metric="euclidean")
        return np.asarray(model.fit_predict(features), dtype=int), "sklearn"
    return _dbscan_labels(features, eps=eps, min_samples=min_samples), "lightweight"


def _feature_vector(star: _DistanceMapClusterInput, *, include_parallax: bool) -> tuple[float, ...]:
    pm_ra = float(star.pm_ra_mas_per_year)
    pm_dec = float(star.pm_dec_mas_per_year)
    if include_parallax:
        return (pm_ra, pm_dec, float(star.parallax_mas))
    return (pm_ra, pm_dec)


def _absolute_g_magnitude(star: _DistanceMapClusterInput) -> float | None:
    magnitude = _finite_value(star.magnitude)
    parallax_mas = _finite_value(star.parallax_mas)
    if magnitude is None or parallax_mas is None or parallax_mas <= 0.0:
        return None
    distance_pc = 1000.0 / parallax_mas
    if distance_pc <= 0.0:
        return None
    return float(magnitude - (5.0 * math.log10(distance_pc)) + 5.0)


def _finite_value(value: float | None) -> float | None:
    if value is None:
        return None
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return None
    return numeric_value if np.isfinite(numeric_value) else None


def _robust_normalize(features: np.ndarray) -> np.ndarray:
    medians = np.nanmedian(features, axis=0)
    mad = np.nanmedian(np.abs(features - medians), axis=0)
    scales = np.where(mad > 1e-9, mad * 1.4826, np.nanstd(features, axis=0))
    scales = np.where(scales > 1e-9, scales, 1.0)
    return (features - medians) / scales


def _kth_neighbor_distances(features: np.ndarray, *, k: int, block_size: int = 256) -> np.ndarray:
    point_count = int(features.shape[0])
    kth_distances = np.full(point_count, np.inf, dtype=float)
    for start in range(0, point_count, block_size):
        end = min(point_count, start + block_size)
        block = features[start:end]
        distances = np.linalg.norm(block[:, None, :] - features[None, :, :], axis=2)
        row_indices = np.arange(start, end)
        distances[np.arange(end - start), row_indices] = np.inf
        kth_distances[start:end] = np.partition(distances, k, axis=1)[:, k]
    return kth_distances


def _dbscan_labels(features: np.ndarray, *, eps: float, min_samples: int) -> np.ndarray:
    point_count = int(features.shape[0])
    labels = np.full(point_count, -1, dtype=int)
    visited = np.zeros(point_count, dtype=bool)
    neighbor_cache: dict[int, np.ndarray] = {}
    cluster_id = 0

    def region_query(index: int) -> np.ndarray:
        cached = neighbor_cache.get(index)
        if cached is not None:
            return cached
        distances = np.linalg.norm(features - features[index], axis=1)
        neighbors = np.flatnonzero(distances <= eps)
        neighbor_cache[index] = neighbors
        return neighbors

    for index in range(point_count):
        if visited[index]:
            continue
        visited[index] = True
        neighbors = region_query(index)
        if neighbors.size < min_samples:
            continue
        labels[index] = cluster_id
        seed_queue = list(int(value) for value in neighbors.tolist() if int(value) != index)
        queued = set(seed_queue)
        while seed_queue:
            neighbor_index = seed_queue.pop()
            queued.discard(neighbor_index)
            if not visited[neighbor_index]:
                visited[neighbor_index] = True
                neighbor_neighbors = region_query(neighbor_index)
                if neighbor_neighbors.size >= min_samples:
                    for value in neighbor_neighbors.tolist():
                        normalized_value = int(value)
                        if normalized_value not in queued:
                            seed_queue.append(normalized_value)
                            queued.add(normalized_value)
            if labels[neighbor_index] < 0:
                labels[neighbor_index] = cluster_id
        cluster_id += 1
    return labels


def _refine_members_by_magnitude_consistency(
    stars: Sequence[DistanceMapClusterStar],
    member_indices: list[int],
    *,
    strictness: float,
) -> list[int] | None:
    magnitude_positions: list[int] = []
    magnitude_features: list[tuple[float, float]] = []
    for position, row_index in enumerate(member_indices):
        star = stars[row_index]
        color_index = _finite_value(star.bp_rp)
        absolute_magnitude = _absolute_g_magnitude(star)
        if color_index is None or absolute_magnitude is None:
            continue
        magnitude_positions.append(position)
        magnitude_features.append((color_index, absolute_magnitude))

    if len(magnitude_features) < 5:
        return None

    normalized_features = _robust_normalize(np.asarray(magnitude_features, dtype=float))
    kth_distances = _kth_neighbor_distances(normalized_features, k=2)
    finite_kth = kth_distances[np.isfinite(kth_distances)]
    if finite_kth.size == 0:
        return None

    median_distance = float(np.nanmedian(finite_kth))
    mad_distance = float(np.nanmedian(np.abs(finite_kth - median_distance)))
    baseline_spread = median_distance * (0.4 + (0.2 * float(strictness)))
    adaptive_spread = max(1.2, 2.4 * float(strictness)) * max(0.05, mad_distance)
    threshold = median_distance + max(baseline_spread, adaptive_spread)
    keep_positions = {
        magnitude_positions[index]
        for index, value in enumerate(kth_distances.tolist())
        if np.isfinite(value) and float(value) <= threshold
    }
    if len(keep_positions) < max(4, int(math.ceil(len(magnitude_positions) * 0.6))):
        return None

    refined_indices: list[int] = []
    for position, row_index in enumerate(member_indices):
        if position not in magnitude_positions or position in keep_positions:
            refined_indices.append(row_index)
    return refined_indices


def coerce_distance_map_cluster_settings(
    *,
    preset: object,
    method: object,
    strictness: object,
    parallax_mode: object,
    refine_magnitude_consistency: object,
    auto_filter: object,
    mark_on_image: object,
) -> DistanceMapClusterSettings:
    return DistanceMapClusterSettings(
        preset=normalize_distance_map_cluster_preset(str(preset)),
        method=normalize_distance_map_cluster_requested_method(str(method)),
        strictness=float(strictness),
        parallax_mode=normalize_distance_map_cluster_parallax_mode(str(parallax_mode)),
        refine_magnitude_consistency=bool(refine_magnitude_consistency),
        auto_filter=bool(auto_filter),
        mark_on_image=bool(mark_on_image),
    ).normalized()
