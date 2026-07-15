from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
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
) -> list[CatalogStar]:
    variable_coords = SkyCoord(
        [star.ra_deg for star in variable_stars] * u.deg,
        [star.dec_deg for star in variable_stars] * u.deg,
    ) if variable_stars else None

    active_minimum = _REFERENCE_MAGNITUDE_MIN if minimum_magnitude is None else max(-5.0, float(minimum_magnitude))
    active_maximum = _REFERENCE_MAGNITUDE_MAX if maximum_magnitude is None else min(30.0, float(maximum_magnitude))
    if active_minimum > active_maximum:
        active_minimum, active_maximum = active_maximum, active_minimum

    candidates = sorted(
        [
            star
            for star in gaia_stars
            if star.magnitude is not None and active_minimum <= star.magnitude <= active_maximum
        ],
        key=lambda star: _reference_candidate_sort_key(star, active_minimum, active_maximum),
    )

    selected: list[CatalogStar] = []
    for star in candidates:
        if variable_coords is not None:
            coord = SkyCoord(star.ra_deg * u.deg, star.dec_deg * u.deg)
            if coord.separation(variable_coords).arcsecond.min() < exclusion_radius_arcsec:
                continue
        selected.append(star)
        if len(selected) >= limit:
            break
    return selected


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

    updated: list[PhotometryMeasurement] = []
    for measurement in measurements:
        differential_magnitude = measurement.differential_magnitude
        differential_magnitude_error = measurement.differential_magnitude_error
        calibrated_magnitude = measurement.calibrated_magnitude
        calibrated_magnitude_error = measurement.calibrated_magnitude_error
        zero_point_magnitude = measurement.zero_point_magnitude
        zero_point_magnitude_error = measurement.zero_point_magnitude_error
        zero_point_source_count = measurement.zero_point_source_count
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
            key = (str(measurement.file_path), measurement.filter_name)
            reference_rows = grouped_references.get(key, [])
            if comparison_source_ids:
                nearby_references = [item for item in reference_rows if item.source_id in comparison_source_ids]
                nearby_references.sort(key=lambda item: comparison_source_ids.index(item.source_id))
            else:
                nearby_references = _nearest_reference_measurements(
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


def _nearest_reference_measurements(
    references: list[PhotometryMeasurement],
    target: PhotometryMeasurement,
    nearby_reference_count: int,
) -> list[PhotometryMeasurement]:
    ordered = sorted(
        [reference for reference in references if not _has_hard_quality_flag(reference.flags)],
        key=lambda measurement: _sky_distance_squared(measurement, target),
    )
    return ordered[:nearby_reference_count]


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


def _build_light_curve_series_from_rows(rows: list[PhotometryMeasurement], filter_name: str) -> LightCurveSeries:
    ordered_rows = sorted(rows, key=lambda row: row.observation_time or row.file_path.name)
    first = ordered_rows[0]
    points = [
        LightCurvePoint(
            observation_time=row.observation_time,
            file_path=row.file_path,
            differential_magnitude=row.differential_magnitude,
            instrumental_magnitude=row.instrumental_magnitude,
            flux=row.flux,
            flux_error=row.flux_error,
            calibrated_magnitude=row.calibrated_magnitude,
            calibrated_magnitude_error=row.calibrated_magnitude_error,
            comparison_reference_flux=row.comparison_reference_flux,
            differential_magnitude_error=row.differential_magnitude_error,
            quality_score=row.quality_score,
            quality_weight=row.quality_weight,
            excluded_from_analysis=row.excluded_from_analysis,
            exclusion_reasons=list(row.exclusion_reasons),
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
