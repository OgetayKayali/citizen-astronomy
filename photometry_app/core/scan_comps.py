from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations
import math
from typing import Any


# Defaults match automatic Generate: magnitude-first pools without a hard Δmag cut.
# Color and separation remain optional UI filters (0 disables the hard limit).
DEFAULT_MAX_DELTA_MAG = 0.0
DEFAULT_MAX_DELTA_COLOR = 0.0
DEFAULT_MAX_SEPARATION_ARCMIN = 0.0
DEFAULT_POOL_SIZE = 30
DEFAULT_COMPS_PER_SET = 5
MAX_COMBINATIONS_SOFT_CAP = 200


@dataclass(frozen=True, slots=True)
class ScanCompReferenceInput:
    source_id: str
    source_name: str
    ra_deg: float
    dec_deg: float
    magnitude: float | None = None
    bp_rp: float | None = None


@dataclass(frozen=True, slots=True)
class ScanCompCandidate:
    source_id: str
    source_name: str
    ra_deg: float
    dec_deg: float
    magnitude: float | None
    bp_rp: float | None
    separation_arcmin: float
    delta_mag: float | None
    delta_color: float | None
    score: float
    color_used: bool


def angular_separation_arcmin(ra1_deg: float, dec1_deg: float, ra2_deg: float, dec2_deg: float) -> float:
    ra1 = math.radians(float(ra1_deg))
    dec1 = math.radians(float(dec1_deg))
    ra2 = math.radians(float(ra2_deg))
    dec2 = math.radians(float(dec2_deg))
    cos_sep = math.sin(dec1) * math.sin(dec2) + math.cos(dec1) * math.cos(dec2) * math.cos(ra1 - ra2)
    cos_sep = min(1.0, max(-1.0, cos_sep))
    return math.degrees(math.acos(cos_sep)) * 60.0


def coerce_finite_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return numeric


def coerce_bp_rp(value: Any) -> float | None:
    return coerce_finite_float(value)


def catalog_star_bp_rp(star: Any) -> float | None:
    metadata = getattr(star, "metadata", None)
    if isinstance(metadata, Mapping):
        return coerce_bp_rp(metadata.get("bp_rp"))
    return None


def combination_count(favourite_count: int, comps_per_set: int) -> int:
    if comps_per_set <= 0 or favourite_count < comps_per_set:
        return 0
    return math.comb(int(favourite_count), int(comps_per_set))


def iter_unordered_comp_sets(favourite_ids: Sequence[str], comps_per_set: int) -> list[tuple[str, ...]]:
    unique_ids = sorted({str(source_id) for source_id in favourite_ids if str(source_id).strip()})
    size = int(comps_per_set)
    if size <= 0 or len(unique_ids) < size:
        return []
    return list(combinations(unique_ids, size))


def build_scan_comp_candidates(
    *,
    target_ra_deg: float,
    target_dec_deg: float,
    target_magnitude: float | None,
    target_bp_rp: float | None,
    references: Sequence[ScanCompReferenceInput],
    max_delta_mag: float = DEFAULT_MAX_DELTA_MAG,
    max_delta_color: float = DEFAULT_MAX_DELTA_COLOR,
    max_separation_arcmin: float = DEFAULT_MAX_SEPARATION_ARCMIN,
    pool_size: int = DEFAULT_POOL_SIZE,
) -> tuple[list[ScanCompCandidate], bool]:
    """Rank measured reference stars for Scan Comps.

    Ranking matches automatic Generate: closest catalog magnitude first, then
    sky distance. Optional hard limits (Δmag / color / separation) apply only
    when their values are > 0. Color is used only when the target and a
    reference both have finite Gaia BP−RP.

    Returns ``(candidates, color_filter_active)``.
    """
    mag_limit = max(0.0, float(max_delta_mag))
    color_limit = max(0.0, float(max_delta_color))
    sep_limit = max(0.0, float(max_separation_arcmin))
    limit = max(1, int(pool_size))
    target_mag = coerce_finite_float(target_magnitude)
    target_color = coerce_bp_rp(target_bp_rp)
    color_filter_active = target_color is not None and color_limit > 0

    ranked: list[ScanCompCandidate] = []
    for reference in references:
        if not reference.source_id:
            continue
        separation = angular_separation_arcmin(
            target_ra_deg,
            target_dec_deg,
            reference.ra_deg,
            reference.dec_deg,
        )
        if sep_limit > 0 and separation > sep_limit:
            continue

        ref_mag = coerce_finite_float(reference.magnitude)
        delta_mag = None if target_mag is None or ref_mag is None else abs(ref_mag - target_mag)
        if mag_limit > 0 and delta_mag is not None and delta_mag > mag_limit:
            continue
        if mag_limit > 0 and target_mag is not None and ref_mag is None:
            continue

        ref_color = coerce_bp_rp(reference.bp_rp)
        delta_color = None if target_color is None or ref_color is None else abs(ref_color - target_color)
        color_used = bool(color_filter_active and delta_color is not None)
        if color_filter_active and delta_color is not None and delta_color > color_limit:
            continue

        # Score mirrors Generate priority: |Δmag| primary, separation secondary.
        mag_score = float(delta_mag) if delta_mag is not None else math.inf
        sep_score = float(separation)
        score = mag_score + (sep_score / 60.0)
        ranked.append(
            ScanCompCandidate(
                source_id=str(reference.source_id),
                source_name=str(reference.source_name or reference.source_id),
                ra_deg=float(reference.ra_deg),
                dec_deg=float(reference.dec_deg),
                magnitude=ref_mag,
                bp_rp=ref_color,
                separation_arcmin=float(separation),
                delta_mag=delta_mag,
                delta_color=delta_color,
                score=float(score),
                color_used=color_used,
            )
        )

    ranked.sort(
        key=lambda item: (
            math.inf if item.delta_mag is None else float(item.delta_mag),
            float(item.separation_arcmin),
            item.source_name.casefold(),
            item.source_id,
        )
    )
    color_used_any = any(item.color_used for item in ranked[:limit])
    return ranked[:limit], bool(color_filter_active and color_used_any)
