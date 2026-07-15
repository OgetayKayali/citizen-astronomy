from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Literal

import numpy as np

from photometry_app.core.hr_diagram import HrMeasurementRow


HrMotionGroupingMethod = Literal["lightweight", "sklearn"]
HrMotionGroupingRequestedMethod = Literal["auto", "lightweight", "sklearn"]
HrMotionGroupingParallaxMode = Literal["auto", "always", "never"]
HrMotionGroupingPreset = Literal["default", "tight", "loose", "parallax", "custom"]


_HR_MOTION_GROUP_PRESET_DEFAULTS: dict[str, tuple[HrMotionGroupingRequestedMethod, float, HrMotionGroupingParallaxMode, bool]] = {
    "default": ("auto", 1.0, "auto", True),
    "tight": ("auto", 0.8, "auto", True),
    "loose": ("auto", 1.35, "never", False),
    "parallax": ("auto", 0.95, "always", True),
}


@dataclass(frozen=True, slots=True)
class HrMotionGroupSettings:
    preset: HrMotionGroupingPreset = "default"
    method: HrMotionGroupingRequestedMethod = "auto"
    strictness: float = 1.0
    parallax_mode: HrMotionGroupingParallaxMode = "auto"
    refine_hr_consistency: bool = True
    auto_filter: bool = False

    def normalized(self) -> "HrMotionGroupSettings":
        normalized_preset = normalize_hr_motion_group_preset(self.preset)
        normalized_auto_filter = bool(self.auto_filter)
        if normalized_preset != "custom":
            method, strictness, parallax_mode, refine_hr_consistency = _HR_MOTION_GROUP_PRESET_DEFAULTS[normalized_preset]
            return HrMotionGroupSettings(
                preset=normalized_preset,
                method=method,
                strictness=strictness,
                parallax_mode=parallax_mode,
                refine_hr_consistency=refine_hr_consistency,
                auto_filter=normalized_auto_filter,
            )
        return HrMotionGroupSettings(
            preset="custom",
            method=normalize_hr_motion_group_requested_method(self.method),
            strictness=min(2.5, max(0.4, float(self.strictness))),
            parallax_mode=normalize_hr_motion_group_parallax_mode(self.parallax_mode),
            refine_hr_consistency=bool(self.refine_hr_consistency),
            auto_filter=normalized_auto_filter,
        )


def normalize_hr_motion_group_preset(value: str) -> HrMotionGroupingPreset:
    normalized = str(value).strip().lower()
    if normalized in _HR_MOTION_GROUP_PRESET_DEFAULTS:
        return normalized  # type: ignore[return-value]
    return "custom" if normalized == "custom" else "default"


def normalize_hr_motion_group_requested_method(value: str) -> HrMotionGroupingRequestedMethod:
    normalized = str(value).strip().lower()
    if normalized == "sklearn":
        return "sklearn"
    if normalized == "auto":
        return "auto"
    return "lightweight"


def normalize_hr_motion_group_parallax_mode(value: str) -> HrMotionGroupingParallaxMode:
    normalized = str(value).strip().lower()
    if normalized == "always":
        return "always"
    if normalized == "never":
        return "never"
    return "auto"


def hr_motion_group_preset_label(preset: str) -> str:
    normalized = normalize_hr_motion_group_preset(preset)
    if normalized == "tight":
        return "Tight"
    if normalized == "loose":
        return "Loose"
    if normalized == "parallax":
        return "Parallax Priority"
    if normalized == "custom":
        return "Custom"
    return "Default"


def hr_motion_group_preset_description(preset: str) -> str:
    normalized = normalize_hr_motion_group_preset(preset)
    if normalized == "tight":
        return "Uses a stricter cluster threshold to emphasize the dense core of a moving group while keeping HR cleanup enabled."
    if normalized == "loose":
        return "Expands the astrometric search to catch broader associations, disables HR cleanup, and ignores parallax so sparse fields stay usable."
    if normalized == "parallax":
        return "Requires positive Gaia parallax for every candidate member and keeps HR cleanup enabled for a more physically constrained search."
    if normalized == "custom":
        return "Keeps your manual expert settings for backend, strictness, parallax usage, and HR cleanup."
    return "Recommended balanced search using automatic backend selection, automatic parallax use when enough stars support it, and HR cleanup."


def hr_motion_group_method_label(method: str) -> str:
    normalized = normalize_hr_motion_group_requested_method(method)
    if normalized == "sklearn":
        return "Sklearn DBSCAN"
    if normalized == "auto":
        return "Auto (recommended)"
    return "Lightweight"


def hr_motion_group_parallax_mode_label(mode: str) -> str:
    normalized = normalize_hr_motion_group_parallax_mode(mode)
    if normalized == "always":
        return "Require parallax"
    if normalized == "never":
        return "Proper motion only"
    return "Auto"


@dataclass(slots=True)
class HrCommonMotionGroupResult:
    member_indices: list[int]
    eligible_indices: list[int]
    cluster_count: int
    member_count: int
    astrometric_member_count: int
    hr_refined_member_count: int
    used_parallax: bool
    used_hr_refinement: bool
    eps: float
    min_samples: int
    strictness: float
    clustering_method: HrMotionGroupingMethod


def find_common_motion_group(
    rows: list[HrMeasurementRow],
    *,
    strictness: float = 1.0,
    method: HrMotionGroupingRequestedMethod = "auto",
    parallax_mode: HrMotionGroupingParallaxMode = "auto",
    refine_hr_consistency: bool = True,
) -> HrCommonMotionGroupResult | None:
    if not rows:
        return None

    normalized_strictness = min(2.5, max(0.4, float(strictness)))
    normalized_method = normalize_hr_motion_group_requested_method(method)
    normalized_parallax_mode = normalize_hr_motion_group_parallax_mode(parallax_mode)

    eligible_pm_indices = [
        index
        for index, row in enumerate(rows)
        if _finite_value(row.pm_ra_mas_per_year) is not None and _finite_value(row.pm_dec_mas_per_year) is not None
    ]
    if len(eligible_pm_indices) < 5:
        return None

    parallax_eligible_indices = [
        index
        for index in eligible_pm_indices
        if _finite_value(rows[index].parallax_mas) is not None and float(rows[index].parallax_mas) > 0.0
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

    features = np.asarray([
        _feature_vector(rows[index], include_parallax=used_parallax)
        for index in eligible_indices
    ], dtype=float)
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
    used_hr_refinement = False
    if refine_hr_consistency:
        refined_member_indices = _refine_members_by_hr_consistency(rows, astrometric_member_indices, strictness=normalized_strictness)
        if refined_member_indices is not None and len(refined_member_indices) >= max(4, int(math.ceil(len(astrometric_member_indices) * 0.6))):
            member_indices = refined_member_indices
            used_hr_refinement = len(member_indices) != len(astrometric_member_indices)

    return HrCommonMotionGroupResult(
        member_indices=member_indices,
        eligible_indices=list(eligible_indices),
        cluster_count=len(unique_labels),
        member_count=len(member_indices),
        astrometric_member_count=len(astrometric_member_indices),
        hr_refined_member_count=len(member_indices),
        used_parallax=used_parallax,
        used_hr_refinement=used_hr_refinement,
        eps=eps,
        min_samples=min_samples,
        strictness=normalized_strictness,
        clustering_method=clustering_method,
    )


def _resolved_clustering_method(method: HrMotionGroupingRequestedMethod, *, point_count: int) -> HrMotionGroupingMethod:
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
    method: HrMotionGroupingMethod,
) -> tuple[np.ndarray, HrMotionGroupingMethod]:
    if method == "sklearn":
        try:
            from sklearn.cluster import DBSCAN
        except Exception:
            return _dbscan_labels(features, eps=eps, min_samples=min_samples), "lightweight"
        model = DBSCAN(eps=float(eps), min_samples=int(min_samples), metric="euclidean")
        return np.asarray(model.fit_predict(features), dtype=int), "sklearn"
    return _dbscan_labels(features, eps=eps, min_samples=min_samples), "lightweight"


def _feature_vector(row: HrMeasurementRow, *, include_parallax: bool) -> tuple[float, ...]:
    pm_ra = float(row.pm_ra_mas_per_year)
    pm_dec = float(row.pm_dec_mas_per_year)
    if include_parallax:
        return (pm_ra, pm_dec, float(row.parallax_mas))
    return (pm_ra, pm_dec)


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


def _refine_members_by_hr_consistency(
    rows: list[HrMeasurementRow],
    member_indices: list[int],
    *,
    strictness: float,
) -> list[int] | None:
    hr_positions: list[int] = []
    hr_features: list[tuple[float, float]] = []
    for position, row_index in enumerate(member_indices):
        row = rows[row_index]
        color_index = _finite_value(row.gaia_bp_rp)
        magnitude = _hr_refinement_magnitude(row)
        if color_index is None or magnitude is None:
            continue
        hr_positions.append(position)
        hr_features.append((color_index, magnitude))

    if len(hr_features) < 5:
        return None

    normalized_hr_features = _robust_normalize(np.asarray(hr_features, dtype=float))
    kth_distances = _kth_neighbor_distances(normalized_hr_features, k=2)
    finite_kth = kth_distances[np.isfinite(kth_distances)]
    if finite_kth.size == 0:
        return None

    median_distance = float(np.nanmedian(finite_kth))
    mad_distance = float(np.nanmedian(np.abs(finite_kth - median_distance)))
    baseline_spread = median_distance * (0.4 + (0.2 * float(strictness)))
    adaptive_spread = max(1.2, 2.4 * float(strictness)) * max(0.05, mad_distance)
    threshold = median_distance + max(baseline_spread, adaptive_spread)
    keep_hr_positions = {
        hr_positions[index]
        for index, value in enumerate(kth_distances.tolist())
        if np.isfinite(value) and float(value) <= threshold
    }
    if len(keep_hr_positions) < max(4, int(math.ceil(len(hr_positions) * 0.6))):
        return None

    refined_indices: list[int] = []
    for position, row_index in enumerate(member_indices):
        if position not in hr_positions or position in keep_hr_positions:
            refined_indices.append(row_index)
    return refined_indices


def _hr_refinement_magnitude(row: HrMeasurementRow) -> float | None:
    gaia_absolute_magnitude = _finite_value(row.gaia_absolute_magnitude)
    if gaia_absolute_magnitude is not None:
        return gaia_absolute_magnitude
    return _finite_value(row.absolute_magnitude_proxy)