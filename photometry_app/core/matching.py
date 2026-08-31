from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
import math
from statistics import median

import numpy as np
from astropy.coordinates import SkyCoord
from astropy import units as u

from photometry_app.core.error_calculations import compute_differential_mag_error, flux_error_to_mag_error
from photometry_app.core.models import CatalogStar, LightCurvePoint, LightCurveSeries, PhotometryMeasurement


_REFERENCE_MAGNITUDE_MIN = 8.0
_REFERENCE_MAGNITUDE_MAX = 16.0
_PREFERRED_REFERENCE_MAGNITUDE_MIN = 10.0
_PREFERRED_REFERENCE_MAGNITUDE_MAX = 13.5
_IDEAL_REFERENCE_MAGNITUDE = 11.5
# Wider bounds when building per-target pools so bright/faint variables can
# still match similar-brightness comparison stars (saturation is filtered later).
_PER_TARGET_REFERENCE_MAGNITUDE_MIN = -1.0
_PER_TARGET_REFERENCE_MAGNITUDE_MAX = 18.0


def measurement_has_usable_value(measurement: PhotometryMeasurement) -> bool:
    return (
        (measurement.flux is not None and measurement.flux > 0)
        or measurement.instrumental_magnitude is not None
        or measurement.differential_magnitude is not None
    )


def select_reference_stars(
    gaia_stars: list[CatalogStar],
    variable_stars: list[CatalogStar],
    limit: int = 25,
    exclusion_radius_arcsec: float = 30.0,
    minimum_magnitude: float | None = None,
    maximum_magnitude: float | None = None,
    per_target_count: int | None = None,
    target_stars: list[CatalogStar] | None = None,
) -> list[CatalogStar]:
    """Select Gaia comparison stars for differential photometry.

    When ``per_target_count`` is set and there are pool targets (``target_stars``
    if provided, otherwise ``variable_stars``), each target gets its own
    magnitude-matched pool of up to that many stars; the returned list is the
    union across targets. ``variable_stars`` always define the exclusion zone
    (stars near known variables are never used as comps).

    Otherwise a single field-wide mid-range pool of up to ``limit`` stars is
    returned (legacy / no per-target mode).
    """
    exclusion_stars = list(variable_stars)
    pool_targets = list(target_stars) if target_stars is not None else list(variable_stars)
    variable_coords = SkyCoord(
        [star.ra_deg for star in exclusion_stars] * u.deg,
        [star.dec_deg for star in exclusion_stars] * u.deg,
    ) if exclusion_stars else None

    use_per_target = per_target_count is not None and int(per_target_count) > 0 and bool(pool_targets)
    if use_per_target:
        active_minimum = (
            _PER_TARGET_REFERENCE_MAGNITUDE_MIN if minimum_magnitude is None else max(-5.0, float(minimum_magnitude))
        )
        active_maximum = (
            _PER_TARGET_REFERENCE_MAGNITUDE_MAX if maximum_magnitude is None else min(30.0, float(maximum_magnitude))
        )
    else:
        active_minimum = _REFERENCE_MAGNITUDE_MIN if minimum_magnitude is None else max(-5.0, float(minimum_magnitude))
        active_maximum = _REFERENCE_MAGNITUDE_MAX if maximum_magnitude is None else min(30.0, float(maximum_magnitude))
    if active_minimum > active_maximum:
        active_minimum, active_maximum = active_maximum, active_minimum

    magnitude_filtered = [
        star
        for star in gaia_stars
        if star.magnitude is not None and active_minimum <= star.magnitude <= active_maximum
    ]
    eligible = _exclude_stars_near_variables(magnitude_filtered, variable_coords, exclusion_radius_arcsec)

    if use_per_target:
        return _select_per_target_reference_stars(eligible, pool_targets, int(per_target_count))

    candidates = sorted(
        eligible,
        key=lambda star: _reference_candidate_sort_key(star, active_minimum, active_maximum),
    )
    return candidates[: max(0, int(limit))]


def apply_differential_photometry(
    measurements: list[PhotometryMeasurement],
    nearby_reference_count: int = 5,
) -> list[PhotometryMeasurement]:
    grouped_references: dict[tuple[str, str | None], list[PhotometryMeasurement]] = defaultdict(list)

    for measurement in measurements:
        if (
            measurement.is_reference
            and measurement.flux
            and measurement.flux > 0
            and not measurement.is_saturated
            and not measurement.is_near_saturated
            and not _has_hard_quality_flag(measurement.flags)
        ):
            key = (str(measurement.file_path), measurement.filter_name)
            grouped_references[key].append(measurement)

    from photometry_app.core.standard_magnitude import compute_standard_magnitude_context

    updated: list[PhotometryMeasurement] = []
    for measurement in measurements:
        differential_magnitude = measurement.differential_magnitude
        differential_magnitude_error = measurement.differential_magnitude_error
        calibrated_magnitude = measurement.calibrated_magnitude
        calibrated_magnitude_error = measurement.calibrated_magnitude_error
        zero_point_magnitude = measurement.zero_point_magnitude
        zero_point_magnitude_error = measurement.zero_point_magnitude_error
        zero_point_source_count = measurement.zero_point_source_count
        standard_magnitude = measurement.standard_magnitude
        standard_magnitude_error = measurement.standard_magnitude_error
        standard_zero_point_magnitude = measurement.standard_zero_point_magnitude
        standard_zero_point_magnitude_error = measurement.standard_zero_point_magnitude_error
        standard_zero_point_source_count = measurement.standard_zero_point_source_count
        standard_catalog_band = measurement.standard_catalog_band
        standard_catalog_source = measurement.standard_catalog_source
        flags = list(measurement.flags)
        comparison_source_ids = list(measurement.comparison_source_ids)
        comparison_source_names = list(measurement.comparison_source_names)
        comparison_reference_flux = measurement.comparison_reference_flux
        comparison_scatter = measurement.comparison_scatter
        quality_weight = measurement.quality_weight
        if not measurement.is_reference and measurement.flux and measurement.flux > 0:
            calibrated_magnitude = None
            calibrated_magnitude_error = None
            zero_point_magnitude = None
            zero_point_magnitude_error = None
            zero_point_source_count = 0
            standard_magnitude = None
            standard_magnitude_error = None
            standard_zero_point_magnitude = None
            standard_zero_point_magnitude_error = None
            standard_zero_point_source_count = 0
            standard_catalog_band = None
            standard_catalog_source = None
            key = (str(measurement.file_path), measurement.filter_name)
            reference_rows = grouped_references.get(key, [])
            if comparison_source_ids:
                nearby_references = [item for item in reference_rows if item.source_id in comparison_source_ids]
                nearby_references.sort(key=lambda item: comparison_source_ids.index(item.source_id))
            else:
                nearby_references = _select_comparison_measurements(
                    reference_rows,
                    measurement,
                    nearby_reference_count,
                )
            if nearby_references:
                comparison_source_ids = [item.source_id for item in nearby_references]
                comparison_source_names = [item.source_name for item in nearby_references]
                reference_flux, reference_flux_error, comparison_scatter = _weighted_reference_flux(nearby_references)
                if reference_flux > 0:
                    comparison_reference_flux = reference_flux
                    differential_magnitude = -2.5 * math.log10(measurement.flux / reference_flux)
                    differential_magnitude_error = _differential_magnitude_error(
                        measurement.flux,
                        measurement.flux_error,
                        reference_flux,
                        reference_flux_error,
                    )
                    calibrated_magnitude, calibrated_magnitude_error, zero_point_magnitude, zero_point_magnitude_error, zero_point_source_count = _calibrated_magnitude_context(
                        measurement,
                        nearby_references,
                    )
                    (
                        standard_magnitude,
                        standard_magnitude_error,
                        standard_zero_point_magnitude,
                        standard_zero_point_magnitude_error,
                        standard_zero_point_source_count,
                        standard_catalog_band,
                        standard_catalog_source,
                    ) = compute_standard_magnitude_context(measurement, nearby_references)
                    quality_weight = _measurement_quality_weight(differential_magnitude_error, measurement.quality_score)
            else:
                flags.append("No nearby reference stars with positive flux.")

        updated.append(
            replace(
                measurement,
                differential_magnitude=differential_magnitude,
                differential_magnitude_error=differential_magnitude_error,
                calibrated_magnitude=calibrated_magnitude,
                calibrated_magnitude_error=calibrated_magnitude_error,
                zero_point_magnitude=zero_point_magnitude,
                zero_point_magnitude_error=zero_point_magnitude_error,
                zero_point_source_count=zero_point_source_count,
                standard_magnitude=standard_magnitude,
                standard_magnitude_error=standard_magnitude_error,
                standard_zero_point_magnitude=standard_zero_point_magnitude,
                standard_zero_point_magnitude_error=standard_zero_point_magnitude_error,
                standard_zero_point_source_count=standard_zero_point_source_count,
                standard_catalog_band=standard_catalog_band,
                standard_catalog_source=standard_catalog_source,
                flags=_deduplicate_strings(flags),
                comparison_source_ids=comparison_source_ids,
                comparison_source_names=comparison_source_names,
                comparison_reference_flux=comparison_reference_flux,
                comparison_scatter=comparison_scatter,
                quality_weight=quality_weight,
            )
        )

    return updated


def apply_measurement_quality_analysis(
    measurements: list[PhotometryMeasurement],
    min_snr: float = 5.0,
    exclusion_snr: float = 3.0,
    outlier_sigma: float = 4.5,
    hampel_window: int = 2,
) -> list[PhotometryMeasurement]:
    grouped: dict[tuple[str, str | None], list[PhotometryMeasurement]] = defaultdict(list)
    for measurement in measurements:
        grouped[(measurement.source_id, measurement.filter_name)].append(measurement)

    updated_lookup: dict[tuple[str, str | None, str, str], PhotometryMeasurement] = {}
    for group_rows in grouped.values():
        ordered_rows = sorted(group_rows, key=lambda row: row.observation_time or row.file_path.name)
        analysis_values = [_series_analysis_value(row) for row in ordered_rows]
        finite_values = [value for value in analysis_values if value is not None and np.isfinite(value)]
        global_center = float(np.median(finite_values)) if finite_values else None
        global_scale = _robust_scale(finite_values)

        for index, measurement in enumerate(ordered_rows):
            flags = list(measurement.flags)
            exclusion_reasons = list(measurement.exclusion_reasons)
            quality_score = 1.0
            excluded = measurement.excluded_from_analysis

            snr = measurement.snr
            if snr is not None and np.isfinite(snr):
                if snr < min_snr:
                    flags.append(f"Low SNR ({snr:.1f}).")
                    quality_score -= 0.18
                if snr < exclusion_snr:
                    exclusion_reasons.append(f"SNR {snr:.1f} is below the analysis threshold.")
                    excluded = True

            centroid_shift = measurement.centroid_shift_pixels
            if centroid_shift is not None and np.isfinite(centroid_shift):
                if centroid_shift > 2.5:
                    flags.append(f"Large centroid shift ({centroid_shift:.2f} px).")
                    quality_score -= 0.10
                if centroid_shift > 4.0:
                    exclusion_reasons.append(f"Centroid shift {centroid_shift:.2f} px is too large for the cleaned view.")
                    excluded = True

            comparison_scatter = measurement.comparison_scatter
            if comparison_scatter is not None and np.isfinite(comparison_scatter):
                if comparison_scatter > 0.08:
                    flags.append(f"Comparison stars disagree ({comparison_scatter * 100.0:.1f}% scatter).")
                    quality_score -= 0.15
                if comparison_scatter > 0.18:
                    exclusion_reasons.append("Comparison-star agreement is too poor for the cleaned view.")
                    excluded = True

            value = analysis_values[index]
            robust_z = None
            if value is not None and global_center is not None and global_scale is not None and global_scale > 0:
                robust_z = abs(value - global_center) / global_scale
                if robust_z > outlier_sigma:
                    flags.append(f"Global robust outlier (z={robust_z:.2f}).")
                    exclusion_reasons.append("Point rejected by global MAD outlier detection.")
                    quality_score -= 0.28
                    excluded = True

            hampel_z = _hampel_score(analysis_values, index, hampel_window)
            if hampel_z is not None and hampel_z > outlier_sigma:
                flags.append(f"Local Hampel outlier (z={hampel_z:.2f}).")
                exclusion_reasons.append("Point rejected by local Hampel outlier detection.")
                quality_score -= 0.28
                excluded = True

            if _has_hard_quality_flag(flags):
                excluded = True
            quality_score = min(1.0, max(0.0, quality_score - (0.05 * _soft_flag_count(flags))))
            if quality_score < 0.35:
                excluded = True
                exclusion_reasons.append("Quality score fell below the cleaned-view threshold.")

            updated = replace(
                measurement,
                flags=_deduplicate_strings(flags),
                quality_score=quality_score,
                quality_weight=_measurement_quality_weight(
                    measurement.differential_magnitude_error or measurement.flux_error,
                    quality_score,
                ),
                excluded_from_analysis=excluded,
                exclusion_reasons=_deduplicate_strings(exclusion_reasons),
            )
            updated_lookup[_measurement_key(updated)] = updated

    return [updated_lookup.get(_measurement_key(measurement), measurement) for measurement in measurements]


def _exclude_stars_near_variables(
    stars: list[CatalogStar],
    variable_coords: SkyCoord | None,
    exclusion_radius_arcsec: float,
) -> list[CatalogStar]:
    if variable_coords is None or not stars:
        return list(stars)
    kept: list[CatalogStar] = []
    for star in stars:
        coord = SkyCoord(star.ra_deg * u.deg, star.dec_deg * u.deg)
        if coord.separation(variable_coords).arcsecond.min() < exclusion_radius_arcsec:
            continue
        kept.append(star)
    return kept


def _select_per_target_reference_stars(
    eligible: list[CatalogStar],
    variable_stars: list[CatalogStar],
    per_target_count: int,
) -> list[CatalogStar]:
    count = max(1, int(per_target_count))
    selected_by_id: dict[str, CatalogStar] = {}
    for variable in variable_stars:
        for star in _rank_reference_candidates_for_target(eligible, variable)[:count]:
            selected_by_id[star.source_id] = star
    return list(selected_by_id.values())


def _rank_reference_candidates_for_target(
    eligible: list[CatalogStar],
    target: CatalogStar,
) -> list[CatalogStar]:
    target_magnitude = _finite_magnitude(target.magnitude)
    candidates = [star for star in eligible if star.source_id != target.source_id]
    return sorted(
        candidates,
        key=lambda star: _catalog_reference_sort_key(star, target, target_magnitude),
    )


def _catalog_reference_sort_key(
    star: CatalogStar,
    target: CatalogStar,
    target_magnitude: float | None,
) -> tuple[float, float, float, str]:
    star_magnitude = _finite_magnitude(star.magnitude)
    if target_magnitude is None or star_magnitude is None:
        delta_mag = math.inf
    else:
        delta_mag = abs(star_magnitude - target_magnitude)
    separation = _catalog_sky_distance_squared(star, target)
    magnitude_for_tie = star_magnitude if star_magnitude is not None else 99.0
    return (delta_mag, separation, magnitude_for_tie, str(star.source_id))


def _select_comparison_measurements(
    references: list[PhotometryMeasurement],
    target: PhotometryMeasurement,
    nearby_reference_count: int,
) -> list[PhotometryMeasurement]:
    """Pick comparison stars for one target: closest catalog magnitude, then sky distance."""
    usable = [reference for reference in references if not _has_hard_quality_flag(reference.flags)]
    target_magnitude = _finite_magnitude(target.catalog_magnitude)
    ordered = sorted(
        usable,
        key=lambda measurement: _comparison_measurement_sort_key(measurement, target, target_magnitude),
    )
    return ordered[: max(0, int(nearby_reference_count))]


def _comparison_measurement_sort_key(
    reference: PhotometryMeasurement,
    target: PhotometryMeasurement,
    target_magnitude: float | None,
) -> tuple[float, float, float, str]:
    reference_magnitude = _finite_magnitude(reference.catalog_magnitude)
    if target_magnitude is None or reference_magnitude is None:
        delta_mag = math.inf
    else:
        delta_mag = abs(reference_magnitude - target_magnitude)
    return (
        delta_mag,
        _sky_distance_squared(reference, target),
        reference_magnitude if reference_magnitude is not None else 99.0,
        str(reference.source_id),
    )


def _finite_magnitude(value: float | None) -> float | None:
    if value is None:
        return None
    try:
        magnitude = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(magnitude):
        return None
    return magnitude


def _reference_candidate_sort_key(
    star: CatalogStar,
    active_minimum: float,
    active_maximum: float,
) -> tuple[int, float, float]:
    magnitude = float(star.magnitude) if star.magnitude is not None else 99.0
    preferred_band_rank = 0 if minimum_or_default(active_minimum, _PREFERRED_REFERENCE_MAGNITUDE_MIN) <= magnitude <= maximum_or_default(active_maximum, _PREFERRED_REFERENCE_MAGNITUDE_MAX) else 1
    midpoint = (active_minimum + active_maximum) / 2.0
    return (preferred_band_rank, abs(magnitude - midpoint), magnitude)


def minimum_or_default(value: float, default: float) -> float:
    return max(value, default)


def maximum_or_default(value: float, default: float) -> float:
    return min(value, default)


def _catalog_sky_distance_squared(first: CatalogStar, second: CatalogStar) -> float:
    delta_ra = first.ra_deg - second.ra_deg
    delta_dec = first.dec_deg - second.dec_deg
    return (delta_ra * delta_ra) + (delta_dec * delta_dec)


def _sky_distance_squared(first: PhotometryMeasurement, second: PhotometryMeasurement) -> float:
    delta_ra = first.ra_deg - second.ra_deg
    delta_dec = first.dec_deg - second.dec_deg
    return (delta_ra * delta_ra) + (delta_dec * delta_dec)


def _reference_flux_error(references: list[PhotometryMeasurement]) -> float | None:
    valid_errors = [item.flux_error for item in references if item.flux_error is not None]
    if not valid_errors:
        return None
    return math.sqrt(sum(error * error for error in valid_errors)) / len(valid_errors)


def _weighted_reference_flux(references: list[PhotometryMeasurement]) -> tuple[float, float | None, float | None]:
    valid_rows = [item for item in references if item.flux is not None and item.flux > 0]
    if not valid_rows:
        return 0.0, None, None

    weighted_rows = [item for item in valid_rows if item.flux_error is not None and item.flux_error > 0]
    if not weighted_rows:
        reference_flux = median(item.flux for item in valid_rows if item.flux is not None)
        return reference_flux, _reference_flux_error(valid_rows), _fractional_reference_scatter(valid_rows)

    weights = np.asarray([1.0 / (item.flux_error * item.flux_error) for item in weighted_rows], dtype=float)
    fluxes = np.asarray([item.flux for item in weighted_rows if item.flux is not None], dtype=float)
    weight_sum = float(np.sum(weights))
    if not np.isfinite(weight_sum) or weight_sum <= 0:
        reference_flux = median(item.flux for item in valid_rows if item.flux is not None)
        return reference_flux, _reference_flux_error(valid_rows), _fractional_reference_scatter(valid_rows)

    reference_flux = float(np.sum(fluxes * weights) / weight_sum)
    reference_error = math.sqrt(1.0 / weight_sum)
    return reference_flux, reference_error, _fractional_reference_scatter(weighted_rows, weights=weights)


def _fractional_reference_scatter(
    references: list[PhotometryMeasurement],
    weights: np.ndarray | None = None,
) -> float | None:
    fluxes = np.asarray([item.flux for item in references if item.flux is not None and item.flux > 0], dtype=float)
    if fluxes.size < 2:
        return None
    center = float(np.average(fluxes, weights=weights)) if weights is not None and weights.size == fluxes.size else float(np.median(fluxes))
    if not np.isfinite(center) or center <= 0:
        return None
    if weights is not None and weights.size == fluxes.size:
        variance = float(np.average((fluxes - center) ** 2, weights=weights))
    else:
        variance = float(np.var(fluxes))
    return math.sqrt(max(0.0, variance)) / center


def _differential_magnitude_error(
    target_flux: float | None,
    target_flux_error: float | None,
    reference_flux: float | None,
    reference_flux_error: float | None,
) -> float | None:
    if (
        target_flux is None
        or reference_flux is None
        or target_flux_error is None
        or reference_flux_error is None
        or target_flux <= 0
        or reference_flux <= 0
    ):
        return None
    scale = 2.5 / math.log(10.0)
    fractional_variance = ((target_flux_error / target_flux) ** 2) + ((reference_flux_error / reference_flux) ** 2)
    return scale * math.sqrt(max(0.0, fractional_variance))


def _instrumental_magnitude_error(measurement: PhotometryMeasurement) -> float | None:
    if measurement.flux is None or measurement.flux_error is None:
        return None
    value = flux_error_to_mag_error(measurement.flux, measurement.flux_error)
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        return None
    return resolved if np.isfinite(resolved) and resolved >= 0 else None


def _calibrated_magnitude_context(
    target: PhotometryMeasurement,
    references: list[PhotometryMeasurement],
) -> tuple[float | None, float | None, float | None, float | None, int]:
    if target.instrumental_magnitude is None:
        return None, None, None, None, 0

    zero_points: list[float] = []
    reference_errors: list[float] = []
    for reference in references:
        if reference.instrumental_magnitude is None or reference.catalog_magnitude is None:
            continue
        zero_point = reference.catalog_magnitude - reference.instrumental_magnitude
        if not np.isfinite(zero_point):
            continue
        zero_points.append(float(zero_point))
        reference_error = _instrumental_magnitude_error(reference)
        reference_errors.append(reference_error if reference_error is not None and reference_error > 0 else float("nan"))

    if not zero_points:
        return None, None, None, None, 0

    zero_point_array = np.asarray(zero_points, dtype=float)
    error_array = np.asarray(reference_errors, dtype=float)
    finite_error_mask = np.isfinite(error_array) & (error_array > 0)

    if finite_error_mask.all():
        weights = 1.0 / np.square(error_array)
        zero_point_magnitude = float(np.average(zero_point_array, weights=weights))
        zero_point_magnitude_error = math.sqrt(1.0 / float(np.sum(weights)))
    else:
        zero_point_magnitude = float(np.mean(zero_point_array))
        if zero_point_array.size > 1:
            zero_point_magnitude_error = float(np.std(zero_point_array, ddof=1) / math.sqrt(zero_point_array.size))
        else:
            zero_point_magnitude_error = None

    calibrated_magnitude = float(target.instrumental_magnitude + zero_point_magnitude)
    target_magnitude_error = _instrumental_magnitude_error(target)
    calibrated_magnitude_error = None
    if target_magnitude_error is not None and zero_point_magnitude_error is not None:
        calibrated_magnitude_error = float(compute_differential_mag_error(target_magnitude_error, zero_point_magnitude_error))
    elif target.differential_magnitude_error is not None and zero_point_array.size == 1:
        calibrated_magnitude_error = float(target.differential_magnitude_error)

    return (
        calibrated_magnitude,
        calibrated_magnitude_error,
        zero_point_magnitude,
        zero_point_magnitude_error,
        int(zero_point_array.size),
    )


def build_light_curve_series(measurements: list[PhotometryMeasurement]) -> list[LightCurveSeries]:
    grouped: dict[tuple[str, str], list[PhotometryMeasurement]] = defaultdict(list)
    for measurement in measurements:
        if measurement.is_reference:
            continue
        if not measurement_has_usable_value(measurement):
            continue
        filter_name = measurement.filter_name or "unknown"
        grouped[(measurement.source_id, filter_name)].append(measurement)

    series_list: list[LightCurveSeries] = []
    for (source_id, filter_name), rows in grouped.items():
        series_list.append(_build_light_curve_series_from_rows(rows, filter_name))

    return sorted(series_list, key=lambda item: (item.source_name.lower(), item.filter_name.lower()))


def build_light_curve_series_for_target(
    measurements: list[PhotometryMeasurement],
    source_id: str,
    filter_name: str,
) -> LightCurveSeries | None:
    rows = [
        measurement
        for measurement in measurements
        if (
            not measurement.is_reference
            and measurement.source_id == source_id
            and (measurement.filter_name or "unknown") == filter_name
            and measurement_has_usable_value(measurement)
        )
    ]
    if not rows:
        return None
    return _build_light_curve_series_from_rows(rows, filter_name)


@dataclass(frozen=True, slots=True)
class OverviewLightCurveLayer:
    role: str  # target | comparison | check
    legend_label: str
    series: LightCurveSeries


def build_overview_light_curve_layers(
    measurements: list[PhotometryMeasurement],
    target_source_id: str,
    *,
    max_comparison_stars: int = 8,
    preferred_comparison_source_ids: list[str] | None = None,
) -> tuple[list[OverviewLightCurveLayer], str | None]:
    """Build multi-series Overview layers for one science target.

    Comparison stars match the target's ensemble: prefer
    ``preferred_comparison_source_ids`` (UI sticky set), otherwise the most
    common exact comparison set across target rows. A frequency-ranked union is
    only a last resort, still capped by ``max_comparison_stars``.
    """
    target_id = str(target_source_id).strip()
    if not target_id:
        return [], "Select a target source to show the Overview light curve."

    target_rows = [
        measurement
        for measurement in measurements
        if measurement.source_id == target_id and not measurement.is_reference and measurement_has_usable_value(measurement)
    ]
    if not target_rows:
        return [], "Select a science target (not a bare comparison star) to show the Overview light curve."

    target_name = target_rows[0].source_name or target_id
    comparison_counts: dict[str, int] = defaultdict(int)
    comparison_names: dict[str, str] = {}
    comparison_set_counts: dict[tuple[str, ...], int] = defaultdict(int)
    for measurement in target_rows:
        ordered_ids: list[str] = []
        for index, source_id in enumerate(measurement.comparison_source_ids):
            resolved_id = str(source_id).strip()
            if not resolved_id:
                continue
            ordered_ids.append(resolved_id)
            comparison_counts[resolved_id] += 1
            if resolved_id not in comparison_names:
                if index < len(measurement.comparison_source_names) and measurement.comparison_source_names[index]:
                    comparison_names[resolved_id] = str(measurement.comparison_source_names[index])
                else:
                    comparison_names[resolved_id] = resolved_id
        if ordered_ids:
            comparison_set_counts[tuple(ordered_ids)] += 1

    check_ids: dict[str, str] = {}
    for measurement in measurements:
        if not measurement.is_check:
            continue
        if measurement.source_id == target_id:
            continue
        check_ids.setdefault(measurement.source_id, measurement.source_name or measurement.source_id)

    preferred_ids = [
        str(source_id).strip()
        for source_id in (preferred_comparison_source_ids or [])
        if str(source_id).strip() and str(source_id).strip() not in check_ids
    ]
    if preferred_ids:
        selected_comps = preferred_ids[: max(0, int(max_comparison_stars))]
        truncated = max(0, len(preferred_ids) - len(selected_comps))
    elif comparison_set_counts:
        most_common_set = max(
            comparison_set_counts.items(),
            key=lambda item: (item[1], -len(item[0]), item[0]),
        )[0]
        selected_comps = [source_id for source_id in most_common_set if source_id not in check_ids]
        selected_comps = selected_comps[: max(0, int(max_comparison_stars))]
        truncated = 0
    else:
        ranked_comps = sorted(
            comparison_counts.items(),
            key=lambda item: (-item[1], comparison_names.get(item[0], item[0]).lower()),
        )
        comp_candidates = [source_id for source_id, _count in ranked_comps if source_id not in check_ids]
        truncated = max(0, len(comp_candidates) - max(0, int(max_comparison_stars)))
        selected_comps = comp_candidates[: max(0, int(max_comparison_stars))]

    frame_contexts = _overview_frame_contexts(target_rows)
    rows_by_source_id: dict[str, list[PhotometryMeasurement]] = defaultdict(list)
    for measurement in measurements:
        if measurement_has_usable_value(measurement):
            rows_by_source_id[measurement.source_id].append(measurement)

    layers: list[OverviewLightCurveLayer] = []
    layers.extend(
        _overview_layers_for_source(
            target_rows,
            role="target",
            display_name=target_name,
            frame_contexts=frame_contexts,
        )
    )

    for source_id in selected_comps:
        comp_rows = rows_by_source_id.get(source_id, [])
        layers.extend(
            _overview_layers_for_source(
                comp_rows,
                role="comparison",
                display_name=_short_overview_name(comparison_names.get(source_id, source_id)),
                frame_contexts=frame_contexts,
            )
        )

    for source_id, source_name in sorted(check_ids.items(), key=lambda item: item[1].lower()):
        check_rows = rows_by_source_id.get(source_id, [])
        layers.extend(
            _overview_layers_for_source(
                check_rows,
                role="check",
                display_name=_short_overview_name(source_name),
                frame_contexts=frame_contexts,
            )
        )

    if not layers:
        return [], "No Overview series available for the selected target."

    status_note = None
    if truncated > 0:
        status_note = f"Overview shows {len(selected_comps)} of {len(preferred_ids) if preferred_ids else len(comparison_counts)} comparison stars."
    return layers, status_note


@dataclass(frozen=True, slots=True)
class _OverviewFrameContext:
    comparison_reference_flux: float | None
    zero_point_magnitude: float | None
    zero_point_magnitude_error: float | None
    standard_zero_point_magnitude: float | None
    standard_zero_point_magnitude_error: float | None


def _overview_frame_contexts(target_rows: list[PhotometryMeasurement]) -> dict[tuple[str, str | None], _OverviewFrameContext]:
    contexts: dict[tuple[str, str | None], _OverviewFrameContext] = {}
    for row in target_rows:
        contexts[(str(row.file_path), row.filter_name)] = _OverviewFrameContext(
            comparison_reference_flux=row.comparison_reference_flux,
            zero_point_magnitude=row.zero_point_magnitude,
            zero_point_magnitude_error=row.zero_point_magnitude_error,
            standard_zero_point_magnitude=row.standard_zero_point_magnitude,
            standard_zero_point_magnitude_error=row.standard_zero_point_magnitude_error,
        )
    return contexts


def _overview_layers_for_source(
    rows: list[PhotometryMeasurement],
    *,
    role: str,
    display_name: str,
    frame_contexts: dict[tuple[str, str | None], _OverviewFrameContext] | None = None,
) -> list[OverviewLightCurveLayer]:
    if not rows:
        return []
    grouped: dict[str, list[PhotometryMeasurement]] = defaultdict(list)
    for measurement in rows:
        grouped[measurement.filter_name or "unknown"].append(measurement)

    layers: list[OverviewLightCurveLayer] = []
    for filter_name in sorted(grouped.keys(), key=str.lower):
        series = _build_light_curve_series_from_rows(
            grouped[filter_name],
            filter_name,
            role=role,
            frame_contexts=frame_contexts,
        )
        if role == "target":
            legend_label = f"{display_name} [{filter_name}]"
        elif role == "check":
            legend_label = f"Check {display_name} [{filter_name}]"
        else:
            legend_label = f"Comp {display_name} [{filter_name}]"
        layers.append(OverviewLightCurveLayer(role=role, legend_label=legend_label, series=series))
    return layers


def _short_overview_name(name: str, *, max_length: int = 18) -> str:
    text = str(name or "").strip() or "source"
    if len(text) <= max_length:
        return text
    return f"{text[: max_length - 1]}…"


def _build_light_curve_series_from_rows(
    rows: list[PhotometryMeasurement],
    filter_name: str,
    *,
    role: str = "target",
    frame_contexts: dict[tuple[str, str | None], _OverviewFrameContext] | None = None,
) -> LightCurveSeries:
    ordered_rows = sorted(rows, key=lambda row: row.observation_time or row.file_path.name)
    first = ordered_rows[0]
    points = [
        _overview_light_curve_point_from_row(row, role=role, frame_contexts=frame_contexts)
        if role in {"comparison", "check"} and frame_contexts
        else LightCurvePoint(
            observation_time=row.observation_time,
            file_path=row.file_path,
            differential_magnitude=row.differential_magnitude,
            instrumental_magnitude=row.instrumental_magnitude,
            flux=row.flux,
            flux_error=row.flux_error,
            calibrated_magnitude=row.calibrated_magnitude,
            calibrated_magnitude_error=row.calibrated_magnitude_error,
            standard_magnitude=row.standard_magnitude,
            standard_magnitude_error=row.standard_magnitude_error,
            comparison_reference_flux=row.comparison_reference_flux,
            differential_magnitude_error=row.differential_magnitude_error,
            quality_score=row.quality_score,
            quality_weight=row.quality_weight,
            excluded_from_analysis=row.excluded_from_analysis,
            exclusion_reasons=list(row.exclusion_reasons),
            is_saturated=row.is_saturated,
        )
        for row in ordered_rows
    ]
    variability_metrics = _compute_variability_metrics(points)
    return LightCurveSeries(
        object_name=first.object_name,
        source_id=first.source_id,
        source_name=first.source_name,
        filter_name=filter_name,
        points=points,
        candidate_score=_candidate_score_from_metrics(variability_metrics),
        variability_metrics=variability_metrics,
    )


def _overview_light_curve_point_from_row(
    row: PhotometryMeasurement,
    *,
    role: str,
    frame_contexts: dict[tuple[str, str | None], _OverviewFrameContext],
) -> LightCurvePoint:
    """Build Overview points for comps/check, filling differential/calibrated/standard from the target ensemble."""
    del role  # reserved for future role-specific display rules
    differential_magnitude = row.differential_magnitude
    differential_magnitude_error = row.differential_magnitude_error
    calibrated_magnitude = row.calibrated_magnitude
    calibrated_magnitude_error = row.calibrated_magnitude_error
    standard_magnitude = row.standard_magnitude
    standard_magnitude_error = row.standard_magnitude_error
    comparison_reference_flux = row.comparison_reference_flux
    context = frame_contexts.get((str(row.file_path), row.filter_name))
    if context is not None:
        reference_flux = context.comparison_reference_flux
        if (
            differential_magnitude is None
            and row.flux is not None
            and row.flux > 0
            and reference_flux is not None
            and reference_flux > 0
        ):
            differential_magnitude = -2.5 * math.log10(row.flux / reference_flux)
            differential_magnitude_error = _differential_magnitude_error(
                row.flux,
                row.flux_error,
                reference_flux,
                row.flux_error,
            )
            if differential_magnitude_error is None:
                differential_magnitude_error = _instrumental_magnitude_error(row)
            comparison_reference_flux = reference_flux
        if (
            calibrated_magnitude is None
            and row.instrumental_magnitude is not None
            and context.zero_point_magnitude is not None
        ):
            calibrated_magnitude = float(row.instrumental_magnitude + context.zero_point_magnitude)
            if row.flux is not None and row.flux > 0 and row.flux_error is not None and row.flux_error >= 0:
                target_mag_error = (2.5 / math.log(10.0)) * (row.flux_error / row.flux)
                if context.zero_point_magnitude_error is not None:
                    calibrated_magnitude_error = float(
                        compute_differential_mag_error(target_mag_error, context.zero_point_magnitude_error)
                    )
                else:
                    calibrated_magnitude_error = float(target_mag_error)
        if (
            standard_magnitude is None
            and row.instrumental_magnitude is not None
            and context.standard_zero_point_magnitude is not None
        ):
            standard_magnitude = float(row.instrumental_magnitude + context.standard_zero_point_magnitude)
            if row.flux is not None and row.flux > 0 and row.flux_error is not None and row.flux_error >= 0:
                target_mag_error = (2.5 / math.log(10.0)) * (row.flux_error / row.flux)
                if context.standard_zero_point_magnitude_error is not None:
                    standard_magnitude_error = float(
                        compute_differential_mag_error(target_mag_error, context.standard_zero_point_magnitude_error)
                    )
                else:
                    standard_magnitude_error = float(target_mag_error)
    return LightCurvePoint(
        observation_time=row.observation_time,
        file_path=row.file_path,
        differential_magnitude=differential_magnitude,
        instrumental_magnitude=row.instrumental_magnitude,
        flux=row.flux,
        flux_error=row.flux_error,
        calibrated_magnitude=calibrated_magnitude,
        calibrated_magnitude_error=calibrated_magnitude_error,
        standard_magnitude=standard_magnitude,
        standard_magnitude_error=standard_magnitude_error,
        comparison_reference_flux=comparison_reference_flux,
        differential_magnitude_error=differential_magnitude_error,
        quality_score=row.quality_score,
        quality_weight=row.quality_weight,
        excluded_from_analysis=row.excluded_from_analysis,
        exclusion_reasons=list(row.exclusion_reasons),
        is_saturated=row.is_saturated,
    )


def _measurement_key(measurement: PhotometryMeasurement) -> tuple[str, str | None, str, str]:
    observation = measurement.observation_time.isoformat(sep=" ") if measurement.observation_time else "-"
    return (measurement.source_id, measurement.filter_name, measurement.file_path.name, observation)


def _series_analysis_value(measurement: PhotometryMeasurement) -> float | None:
    if measurement.differential_magnitude is not None:
        return measurement.differential_magnitude
    if measurement.instrumental_magnitude is not None:
        return measurement.instrumental_magnitude
    if measurement.flux is not None and measurement.flux > 0:
        return measurement.flux
    return None


def _robust_scale(values: list[float]) -> float | None:
    if len(values) < 3:
        return None
    median_value = float(np.median(values))
    deviations = np.abs(np.asarray(values, dtype=float) - median_value)
    mad = float(np.median(deviations))
    if not np.isfinite(mad) or mad <= 0:
        return None
    return 1.4826 * mad


def _hampel_score(values: list[float | None], index: int, window: int) -> float | None:
    start = max(0, index - window)
    end = min(len(values), index + window + 1)
    local_values = [value for value in values[start:end] if value is not None and np.isfinite(value)]
    if len(local_values) < 3 or values[index] is None:
        return None
    scale = _robust_scale(local_values)
    if scale is None or scale <= 0:
        return None
    local_center = float(np.median(local_values))
    return abs(float(values[index]) - local_center) / scale


def _has_hard_quality_flag(flags: list[str]) -> bool:
    hard_markers = (
        "outside the usable image area",
        "non-positive background-subtracted flux",
        "no nearby reference stars with positive flux",
        "non-2d image",
        "saturated",
    )
    return any(any(marker in flag.lower() for marker in hard_markers) for flag in flags)


def _soft_flag_count(flags: list[str]) -> int:
    return sum(1 for flag in flags if not _has_hard_quality_flag([flag]))


def _measurement_quality_weight(error_value: float | None, quality_score: float) -> float | None:
    if error_value is None or error_value <= 0:
        return max(0.0, quality_score)
    return max(0.0, quality_score) / (error_value * error_value)


def _deduplicate_strings(values: list[str]) -> list[str]:
    ordered: list[str] = []
    for value in values:
        normalized = str(value).strip()
        if normalized and normalized not in ordered:
            ordered.append(normalized)
    return ordered


def _compute_variability_metrics(points: list[LightCurvePoint]) -> dict[str, float]:
    usable_points = [point for point in points if not point.excluded_from_analysis and _point_analysis_value(point) is not None]
    if len(usable_points) < 3:
        usable_points = [point for point in points if _point_analysis_value(point) is not None]
    values = np.asarray([_point_analysis_value(point) for point in usable_points], dtype=float)
    if values.size < 2:
        return {
            "point_count": float(len(points)),
            "cleaned_point_count": float(len(usable_points)),
            "rms": float("nan"),
            "mad": float("nan"),
            "amplitude": float("nan"),
            "typical_error": float("nan"),
            "reduced_chi2": float("nan"),
            "von_neumann": float("nan"),
            "stetson_j": float("nan"),
            "stetson_k": float("nan"),
        }

    errors = np.asarray([_point_analysis_error(point) for point in usable_points], dtype=float)
    finite_errors = errors[np.isfinite(errors) & (errors > 0)]
    typical_error = float(np.median(finite_errors)) if finite_errors.size else float(np.std(values, ddof=1) or 1.0)
    rms = float(np.std(values, ddof=1)) if values.size > 1 else 0.0
    mad = float(np.median(np.abs(values - np.median(values))))
    amplitude = float(np.percentile(values, 95) - np.percentile(values, 5)) if values.size >= 5 else float(np.max(values) - np.min(values))
    reduced_chi2 = _reduced_chi_square(values, errors)
    von_neumann = _von_neumann_ratio(values)
    stetson_j, stetson_k = _stetson_indices(values, errors, typical_error)
    return {
        "point_count": float(len(points)),
        "cleaned_point_count": float(len(usable_points)),
        "rms": rms,
        "mad": mad,
        "amplitude": amplitude,
        "typical_error": typical_error,
        "reduced_chi2": reduced_chi2 if reduced_chi2 is not None else float("nan"),
        "von_neumann": von_neumann if von_neumann is not None else float("nan"),
        "stetson_j": stetson_j if stetson_j is not None else float("nan"),
        "stetson_k": stetson_k if stetson_k is not None else float("nan"),
    }


def _candidate_score_from_metrics(metrics: dict[str, float]) -> float:
    if not metrics:
        return 0.0
    typical_error = metrics.get("typical_error") or 0.02
    score = 0.0
    reduced_chi2 = metrics.get("reduced_chi2")
    if reduced_chi2 is not None and np.isfinite(reduced_chi2) and reduced_chi2 > 1.0:
        score += min(np.log10(reduced_chi2) / 1.4, 1.0) * 30.0
    mad = metrics.get("mad")
    if mad is not None and np.isfinite(mad):
        score += min(mad / max(typical_error, 1e-6) / 6.0, 1.0) * 20.0
    amplitude = metrics.get("amplitude")
    if amplitude is not None and np.isfinite(amplitude):
        score += min(amplitude / max(typical_error, 1e-6) / 10.0, 1.0) * 20.0
    stetson_j = metrics.get("stetson_j")
    if stetson_j is not None and np.isfinite(stetson_j) and stetson_j > 0:
        score += min(stetson_j / 4.0, 1.0) * 20.0
    von_neumann = metrics.get("von_neumann")
    if von_neumann is not None and np.isfinite(von_neumann):
        score += min(max(0.0, 2.0 - von_neumann) / 1.5, 1.0) * 10.0
    return round(min(100.0, max(0.0, score)), 2)


def _point_analysis_value(point: LightCurvePoint) -> float | None:
    if point.differential_magnitude is not None:
        return point.differential_magnitude
    if point.instrumental_magnitude is not None:
        return point.instrumental_magnitude
    if point.flux is not None and point.flux > 0:
        return point.flux
    return None


def _point_analysis_error(point: LightCurvePoint) -> float:
    if point.differential_magnitude_error is not None and point.differential_magnitude_error > 0:
        return point.differential_magnitude_error
    if point.flux_error is not None and point.flux_error > 0:
        return point.flux_error
    return float("nan")


def _reduced_chi_square(values: np.ndarray, errors: np.ndarray) -> float | None:
    valid = np.isfinite(values) & np.isfinite(errors) & (errors > 0)
    if np.count_nonzero(valid) < 2:
        return None
    weighted_mean = float(np.average(values[valid], weights=1.0 / (errors[valid] ** 2)))
    chi2 = float(np.sum(((values[valid] - weighted_mean) / errors[valid]) ** 2))
    return chi2 / max(1, int(np.count_nonzero(valid)) - 1)


def _von_neumann_ratio(values: np.ndarray) -> float | None:
    if values.size < 3:
        return None
    variance = float(np.var(values, ddof=1))
    if not np.isfinite(variance) or variance <= 0:
        return None
    return float(np.mean(np.diff(values) ** 2) / variance)


def _stetson_indices(values: np.ndarray, errors: np.ndarray, typical_error: float) -> tuple[float | None, float | None]:
    if values.size < 3:
        return None, None
    safe_errors = np.where(np.isfinite(errors) & (errors > 0), errors, typical_error)
    if np.any(safe_errors <= 0):
        return None, None
    centered = values - float(np.mean(values))
    residuals = np.sqrt(values.size / max(1, values.size - 1)) * centered / safe_errors
    pair_products = residuals[:-1] * residuals[1:]
    stetson_j = float(np.mean(np.sign(pair_products) * np.sqrt(np.abs(pair_products))))
    squared_mean = float(np.mean(residuals ** 2))
    if squared_mean <= 0:
        return stetson_j, None
    stetson_k = float(np.mean(np.abs(residuals)) / np.sqrt(squared_mean))
    return stetson_j, stetson_k
