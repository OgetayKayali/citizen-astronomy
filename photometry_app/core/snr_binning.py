from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median

import numpy as np

from photometry_app.core.matching import measurement_has_usable_value
from photometry_app.core.models import PhotometryMeasurement


_SHARP_TYPE_MARKERS = (
    "ea",
    "eb",
    "ew",
    "rr",
    "rrab",
    "rrc",
    "rrd",
    "dsct",
    "sxphe",
    "cep",
    "cepheid",
    "transit",
    "eclips",
)
_SMOOTH_TYPE_MARKERS = (
    "rot",
    "rotator",
    "rscvn",
    "sr",
    "slow",
    "sin",
    "spotted",
    "ell",
)

_MIN_UTC_DATETIME = datetime.min.replace(tzinfo=UTC)


@dataclass(slots=True)
class SnrBinningSettings:
    max_period_fraction: float = 0.03
    max_absolute_bin_duration_seconds: float = 600.0
    target_snr: float = 30.0
    max_frames_per_bin: int = 15
    min_frames_per_bin: int = 1
    variability_type_aware_thresholds: bool = True
    sharp_period_fraction_override: float = 0.015
    smooth_period_fraction_override: float = 0.05
    weighted_flux_binning: bool = True
    allow_direct_magnitude_averaging_fallback: bool = True
    minimum_valid_points_per_bin: int = 2
    outlier_rejection_enabled: bool = False
    sigma_clip_threshold: float = 3.5
    dataset_mode: str = "derived"
    apply_to_selected_measurements_only: bool = False
    allow_periodless_fallback: bool = False

    def normalized(self) -> "SnrBinningSettings":
        dataset_mode = str(self.dataset_mode).strip().lower()
        if dataset_mode not in {"derived", "replace"}:
            dataset_mode = "derived"
        return SnrBinningSettings(
            max_period_fraction=min(0.5, max(0.001, float(self.max_period_fraction))),
            max_absolute_bin_duration_seconds=min(86400.0, max(1.0, float(self.max_absolute_bin_duration_seconds))),
            target_snr=min(1000.0, max(1.0, float(self.target_snr))),
            max_frames_per_bin=max(1, int(self.max_frames_per_bin)),
            min_frames_per_bin=max(1, int(self.min_frames_per_bin)),
            variability_type_aware_thresholds=bool(self.variability_type_aware_thresholds),
            sharp_period_fraction_override=min(0.5, max(0.001, float(self.sharp_period_fraction_override))),
            smooth_period_fraction_override=min(0.5, max(0.001, float(self.smooth_period_fraction_override))),
            weighted_flux_binning=bool(self.weighted_flux_binning),
            allow_direct_magnitude_averaging_fallback=bool(self.allow_direct_magnitude_averaging_fallback),
            minimum_valid_points_per_bin=max(1, int(self.minimum_valid_points_per_bin)),
            outlier_rejection_enabled=bool(self.outlier_rejection_enabled),
            sigma_clip_threshold=min(10.0, max(1.0, float(self.sigma_clip_threshold))),
            dataset_mode=dataset_mode,
            apply_to_selected_measurements_only=bool(self.apply_to_selected_measurements_only),
            allow_periodless_fallback=bool(self.allow_periodless_fallback),
        )


@dataclass(slots=True)
class SnrBinningSeriesResult:
    source_id: str
    source_name: str
    catalog: str
    filter_name: str
    status: str
    reason: str = ""
    original_measurement_count: int = 0
    new_binned_measurement_count: int = 0
    chosen_frames_per_bin: int = 1
    effective_bin_duration_seconds: float = 0.0
    estimated_snr_improvement: float = 1.0
    period_days: float | None = None
    period_fraction_used: float | None = None
    cadence_seconds: float | None = None
    used_period_rule: bool = False
    used_type_rule: bool = False
    variability_type_label: str = ""
    binned_measurements: list[PhotometryMeasurement] = field(default_factory=list)


@dataclass(slots=True)
class SnrBinningSourceResult:
    source_id: str
    source_name: str
    catalog: str
    status: str
    series_results: list[SnrBinningSeriesResult] = field(default_factory=list)
    apply_scope_label: str = "all measurements for the source"
    dataset_mode: str = "derived"
    message: str = ""

    @property
    def processed_count(self) -> int:
        return sum(1 for item in self.series_results if item.status == "processed")

    @property
    def skipped_count(self) -> int:
        return sum(1 for item in self.series_results if item.status == "skipped")

    @property
    def failed_count(self) -> int:
        return sum(1 for item in self.series_results if item.status == "failed")


@dataclass(slots=True)
class SnrBinningTask:
    source_id: str
    source_name: str
    catalog: str
    variability_type: str = ""
    object_type: str = "star"
    period_days: float | None = None
    measurements_by_filter: dict[str, list[PhotometryMeasurement]] = field(default_factory=dict)


def process_snr_binning_task(task: SnrBinningTask, settings: SnrBinningSettings) -> SnrBinningSourceResult:
    normalized_settings = settings.normalized()
    series_results: list[SnrBinningSeriesResult] = []
    for filter_name, rows in sorted(task.measurements_by_filter.items()):
        series_results.append(
            _bin_measurement_series(
                source_id=task.source_id,
                source_name=task.source_name,
                catalog=task.catalog,
                filter_name=filter_name,
                rows=rows,
                period_days=task.period_days,
                variability_type=task.variability_type,
                object_type=task.object_type,
                settings=normalized_settings,
            )
        )
    processed = sum(1 for item in series_results if item.status == "processed")
    failed = sum(1 for item in series_results if item.status == "failed")
    status = "processed" if processed else ("failed" if failed else "skipped")
    message = (
        f"Processed {processed} filter series, skipped {sum(1 for item in series_results if item.status == 'skipped')}, "
        f"failed {failed}."
    )
    return SnrBinningSourceResult(
        source_id=task.source_id,
        source_name=task.source_name,
        catalog=task.catalog,
        status=status,
        series_results=series_results,
        apply_scope_label=(
            "currently filtered measurements" if normalized_settings.apply_to_selected_measurements_only else "all measurements for the source"
        ),
        dataset_mode=normalized_settings.dataset_mode,
        message=message,
    )


def summarize_snr_binning_result(result: SnrBinningSourceResult) -> str:
    if not result.series_results:
        return f"{result.source_name} [{result.catalog}]: no matching light-curve series were available."
    parts = []
    for series_result in result.series_results:
        if series_result.status != "processed":
            parts.append(f"{series_result.filter_name}: {series_result.reason}")
            continue
        parts.append(
            f"{series_result.filter_name}: {series_result.original_measurement_count}->{series_result.new_binned_measurement_count} rows, "
            f"{series_result.chosen_frames_per_bin} frame/bin, {series_result.effective_bin_duration_seconds:.1f}s, "
            f"x{series_result.estimated_snr_improvement:.2f} SNR"
        )
    return f"{result.source_name} [{result.catalog}] ({result.apply_scope_label}): " + "; ".join(parts)


def _bin_measurement_series(
    *,
    source_id: str,
    source_name: str,
    catalog: str,
    filter_name: str,
    rows: list[PhotometryMeasurement],
    period_days: float | None,
    variability_type: str,
    object_type: str,
    settings: SnrBinningSettings,
) -> SnrBinningSeriesResult:
    ordered_rows = sorted(rows, key=lambda item: (item.observation_time or _MIN_UTC_DATETIME, item.file_path.name))
    usable_rows = [item for item in ordered_rows if measurement_has_usable_value(item)]
    if len(usable_rows) < 2:
        return SnrBinningSeriesResult(
            source_id=source_id,
            source_name=source_name,
            catalog=catalog,
            filter_name=filter_name,
            status="skipped",
            reason="not enough usable measurements",
            original_measurement_count=len(ordered_rows),
            new_binned_measurement_count=len(ordered_rows),
            variability_type_label=variability_type,
        )

    variability_class = _classify_variability_type(variability_type, object_type)
    period_fraction = settings.max_period_fraction
    used_type_rule = False
    if settings.variability_type_aware_thresholds:
        if variability_class == "sharp":
            period_fraction = min(period_fraction, settings.sharp_period_fraction_override)
            used_type_rule = True
        elif variability_class == "smooth":
            period_fraction = max(period_fraction, settings.smooth_period_fraction_override)
            used_type_rule = True

    resolved_period_days = None
    used_period_rule = False
    if period_days is not None:
        try:
            numeric_period = float(period_days)
        except (TypeError, ValueError):
            numeric_period = float("nan")
        if np.isfinite(numeric_period) and numeric_period > 0:
            resolved_period_days = numeric_period
            used_period_rule = True

    if resolved_period_days is None and not settings.allow_periodless_fallback:
        return SnrBinningSeriesResult(
            source_id=source_id,
            source_name=source_name,
            catalog=catalog,
            filter_name=filter_name,
            status="skipped",
            reason="missing usable period",
            original_measurement_count=len(ordered_rows),
            new_binned_measurement_count=len(ordered_rows),
            variability_type_label=variability_type,
            period_fraction_used=period_fraction,
            used_type_rule=used_type_rule,
        )

    cadence_seconds = _median_cadence_seconds(usable_rows)
    if cadence_seconds is None or not np.isfinite(cadence_seconds) or cadence_seconds <= 0:
        if resolved_period_days is None:
            return SnrBinningSeriesResult(
                source_id=source_id,
                source_name=source_name,
                catalog=catalog,
                filter_name=filter_name,
                status="skipped",
                reason="missing cadence and no period fallback",
                original_measurement_count=len(ordered_rows),
                new_binned_measurement_count=len(ordered_rows),
                variability_type_label=variability_type,
                period_fraction_used=period_fraction,
                used_type_rule=used_type_rule,
            )
        cadence_seconds = settings.max_absolute_bin_duration_seconds

    max_duration_seconds = settings.max_absolute_bin_duration_seconds
    if resolved_period_days is not None:
        max_duration_seconds = min(max_duration_seconds, resolved_period_days * 86400.0 * period_fraction)
    safe_max_frames = min(
        settings.max_frames_per_bin,
        max(1, int(math.floor(max_duration_seconds / max(cadence_seconds, 1.0)))),
    )
    if safe_max_frames < 2:
        return SnrBinningSeriesResult(
            source_id=source_id,
            source_name=source_name,
            catalog=catalog,
            filter_name=filter_name,
            status="skipped",
            reason="conservative period limits kept the series unbinned",
            original_measurement_count=len(ordered_rows),
            new_binned_measurement_count=len(ordered_rows),
            period_days=resolved_period_days,
            period_fraction_used=period_fraction,
            cadence_seconds=cadence_seconds,
            used_period_rule=used_period_rule,
            used_type_rule=used_type_rule,
            variability_type_label=variability_type,
        )

    target_frames = _target_frames_per_bin(usable_rows, settings.target_snr)
    chosen_frames = min(settings.max_frames_per_bin, safe_max_frames, target_frames)
    chosen_frames = max(settings.min_frames_per_bin, chosen_frames)
    if chosen_frames < 2:
        return SnrBinningSeriesResult(
            source_id=source_id,
            source_name=source_name,
            catalog=catalog,
            filter_name=filter_name,
            status="skipped",
            reason="the recommended bin size stayed below 2 frames",
            original_measurement_count=len(ordered_rows),
            new_binned_measurement_count=len(ordered_rows),
            period_days=resolved_period_days,
            period_fraction_used=period_fraction,
            cadence_seconds=cadence_seconds,
            used_period_rule=used_period_rule,
            used_type_rule=used_type_rule,
            variability_type_label=variability_type,
        )

    bins = _build_measurement_bins(usable_rows, chosen_frames, max_duration_seconds, cadence_seconds)
    derived_rows: list[PhotometryMeasurement] = []
    durations: list[float] = []
    binned_member_counts: list[int] = []
    used_fallback = False
    for bin_index, group in enumerate(bins, start=1):
        derived = _aggregate_measurement_bin(
            group,
            bin_index=bin_index,
            settings=settings,
            source_name=source_name,
            filter_name=filter_name,
        )
        if derived is None:
            derived_rows.extend(group)
            continue
        durations.append(_bin_duration_seconds(group, cadence_seconds))
        binned_member_counts.append(len(group))
        if any("fallback to magnitude averaging" in flag.lower() for flag in derived.flags):
            used_fallback = True
        derived_rows.append(derived)

    if not binned_member_counts:
        return SnrBinningSeriesResult(
            source_id=source_id,
            source_name=source_name,
            catalog=catalog,
            filter_name=filter_name,
            status="skipped",
            reason="no bins passed the minimum-valid-points threshold",
            original_measurement_count=len(ordered_rows),
            new_binned_measurement_count=len(ordered_rows),
            period_days=resolved_period_days,
            period_fraction_used=period_fraction,
            cadence_seconds=cadence_seconds,
            used_period_rule=used_period_rule,
            used_type_rule=used_type_rule,
            variability_type_label=variability_type,
        )

    improvement = _estimate_snr_improvement(usable_rows, derived_rows, binned_member_counts)
    reason = "weighted flux binning"
    if used_fallback:
        reason = "weighted flux binning with magnitude fallback"
    return SnrBinningSeriesResult(
        source_id=source_id,
        source_name=source_name,
        catalog=catalog,
        filter_name=filter_name,
        status="processed",
        reason=reason,
        original_measurement_count=len(ordered_rows),
        new_binned_measurement_count=len(derived_rows),
        chosen_frames_per_bin=chosen_frames,
        effective_bin_duration_seconds=float(median(durations)) if durations else 0.0,
        estimated_snr_improvement=improvement,
        period_days=resolved_period_days,
        period_fraction_used=period_fraction,
        cadence_seconds=cadence_seconds,
        used_period_rule=used_period_rule,
        used_type_rule=used_type_rule,
        variability_type_label=variability_type,
        binned_measurements=derived_rows,
    )


def _target_frames_per_bin(rows: list[PhotometryMeasurement], target_snr: float) -> int:
    snr_values = [float(item.snr) for item in rows if item.snr is not None and np.isfinite(item.snr) and item.snr > 0]
    if not snr_values:
        return max(1, len(rows))
    median_snr = float(median(snr_values))
    if median_snr >= target_snr:
        return 1
    return max(1, int(math.ceil((target_snr / median_snr) ** 2)))


def _median_cadence_seconds(rows: list[PhotometryMeasurement]) -> float | None:
    timestamps = [item.observation_time for item in rows if item.observation_time is not None]
    if len(timestamps) < 2:
        return None
    ordered = sorted(timestamps)
    deltas = [
        (later - earlier).total_seconds()
        for earlier, later in zip(ordered, ordered[1:])
        if (later - earlier).total_seconds() > 0
    ]
    if not deltas:
        return None
    return float(median(deltas))


def _build_measurement_bins(
    rows: list[PhotometryMeasurement],
    chosen_frames: int,
    max_duration_seconds: float,
    cadence_seconds: float,
) -> list[list[PhotometryMeasurement]]:
    bins: list[list[PhotometryMeasurement]] = []
    current_bin: list[PhotometryMeasurement] = []
    for measurement in rows:
        if not current_bin:
            current_bin = [measurement]
            continue
        candidate_bin = current_bin + [measurement]
        duration = _bin_duration_seconds(candidate_bin, cadence_seconds)
        if len(candidate_bin) > chosen_frames or duration > max_duration_seconds:
            bins.append(current_bin)
            current_bin = [measurement]
            continue
        current_bin = candidate_bin
    if current_bin:
        bins.append(current_bin)
    return bins


def _aggregate_measurement_bin(
    rows: list[PhotometryMeasurement],
    *,
    bin_index: int,
    settings: SnrBinningSettings,
    source_name: str,
    filter_name: str,
) -> PhotometryMeasurement | None:
    working_rows = list(rows)
    if settings.outlier_rejection_enabled and len(working_rows) >= 3:
        clipped_rows = _sigma_clip_measurements(working_rows, settings.sigma_clip_threshold)
        if len(clipped_rows) >= settings.minimum_valid_points_per_bin:
            working_rows = clipped_rows
    if len(working_rows) < settings.minimum_valid_points_per_bin:
        return None

    flux_result = None
    if settings.weighted_flux_binning:
        flux_result = _weighted_flux_bin(working_rows)

    flags: list[str] = []
    if flux_result is None and settings.allow_direct_magnitude_averaging_fallback:
        flux_result = _direct_magnitude_fallback_bin(working_rows)
        if flux_result is not None:
            flags.append(f"SNR binning fallback to magnitude averaging for {source_name} [{filter_name}].")
    if flux_result is None:
        return None

    timestamp = _average_observation_time(working_rows)
    anchor = working_rows[0]
    member_count = len(working_rows)
    flags.append(f"Derived SNR bin from {member_count} measurement(s).")
    if settings.outlier_rejection_enabled and len(working_rows) < len(rows):
        flags.append(f"Sigma-clipped {len(rows) - len(working_rows)} outlier(s) before binning.")

    source_flags = []
    for row in working_rows:
        for item in row.flags:
            if item not in source_flags:
                source_flags.append(item)
    flags.extend(source_flags)
    return replace(
        anchor,
        observation_time=timestamp,
        flux=flux_result.get("flux"),
        flux_error=flux_result.get("flux_error"),
        instrumental_magnitude=flux_result.get("instrumental_magnitude"),
        differential_magnitude=flux_result.get("differential_magnitude"),
        differential_magnitude_error=flux_result.get("differential_magnitude_error"),
        calibrated_magnitude=flux_result.get("calibrated_magnitude"),
        calibrated_magnitude_error=flux_result.get("calibrated_magnitude_error"),
        zero_point_magnitude=flux_result.get("zero_point_magnitude"),
        zero_point_magnitude_error=flux_result.get("zero_point_magnitude_error"),
        zero_point_source_count=int(round(flux_result.get("zero_point_source_count") or 0)),
        comparison_reference_flux=flux_result.get("comparison_reference_flux"),
        comparison_scatter=flux_result.get("comparison_scatter"),
        quality_score=float(flux_result.get("quality_score") or anchor.quality_score),
        quality_weight=flux_result.get("quality_weight"),
        snr=flux_result.get("snr"),
        background_level=flux_result.get("background_level"),
        background_scatter=flux_result.get("background_scatter"),
        centroid_shift_pixels=flux_result.get("centroid_shift_pixels"),
        peak_pixel_value=flux_result.get("peak_pixel_value"),
        saturation_threshold=flux_result.get("saturation_threshold"),
        saturated_pixel_count=int(round(flux_result.get("saturated_pixel_count") or 0)),
        is_saturated=bool(flux_result.get("is_saturated", False)),
        flags=flags,
        excluded_from_analysis=all(row.excluded_from_analysis for row in working_rows),
        exclusion_reasons=list({reason for row in working_rows for reason in row.exclusion_reasons}),
        x=float(np.mean([row.x for row in working_rows])),
        y=float(np.mean([row.y for row in working_rows])),
    )


def _weighted_flux_bin(rows: list[PhotometryMeasurement]) -> dict[str, float | int | None] | None:
    flux_values = [float(row.flux) for row in rows if row.flux is not None and np.isfinite(row.flux) and row.flux > 0]
    if len(flux_values) < 2:
        return None

    flux_array, flux_error = _weighted_mean_and_error(
        [row.flux for row in rows],
        [row.flux_error for row in rows],
        [row.quality_weight for row in rows],
    )
    if flux_array is None or flux_array <= 0:
        return None

    reference_flux, reference_error = _weighted_mean_and_error(
        [row.comparison_reference_flux for row in rows],
        [
            (row.comparison_reference_flux * row.comparison_scatter)
            if row.comparison_reference_flux is not None and row.comparison_scatter is not None and row.comparison_scatter > 0
            else None
            for row in rows
        ],
        [row.quality_weight for row in rows],
    )

    zero_point, zero_point_error = _weighted_mean_and_error(
        [row.zero_point_magnitude for row in rows],
        [row.zero_point_magnitude_error for row in rows],
        [row.quality_weight for row in rows],
    )
    calibrated_magnitude = None
    calibrated_error = None
    instrumental_magnitude = -2.5 * math.log10(flux_array) if flux_array > 0 else None
    if instrumental_magnitude is not None and zero_point is not None:
        calibrated_magnitude = instrumental_magnitude + zero_point
        target_mag_error = 1.085736 * (flux_error / flux_array) if flux_error is not None and flux_error >= 0 else None
        if target_mag_error is not None and zero_point_error is not None:
            calibrated_error = math.sqrt((target_mag_error * target_mag_error) + (zero_point_error * zero_point_error))

    differential_magnitude = None
    differential_error = None
    if reference_flux is not None and reference_flux > 0:
        differential_magnitude = -2.5 * math.log10(flux_array / reference_flux)
        if flux_error is not None and reference_error is not None and flux_array > 0 and reference_flux > 0:
            scale = 2.5 / math.log(10.0)
            differential_error = scale * math.sqrt(((flux_error / flux_array) ** 2) + ((reference_error / reference_flux) ** 2))

    comparison_scatter = _fractional_scatter([row.comparison_reference_flux for row in rows])
    quality_score = _weighted_scalar([row.quality_score for row in rows], [row.quality_weight for row in rows])
    quality_weight = _derived_quality_weight(flux_array, flux_error, quality_score)
    return {
        "flux": flux_array,
        "flux_error": flux_error,
        "snr": (flux_array / flux_error) if flux_error is not None and flux_error > 0 else None,
        "instrumental_magnitude": instrumental_magnitude,
        "differential_magnitude": differential_magnitude,
        "differential_magnitude_error": differential_error,
        "calibrated_magnitude": calibrated_magnitude,
        "calibrated_magnitude_error": calibrated_error,
        "zero_point_magnitude": zero_point,
        "zero_point_magnitude_error": zero_point_error,
        "zero_point_source_count": _weighted_scalar([row.zero_point_source_count for row in rows], [row.quality_weight for row in rows]),
        "comparison_reference_flux": reference_flux,
        "comparison_scatter": comparison_scatter,
        "quality_score": quality_score,
        "quality_weight": quality_weight,
        "background_level": _weighted_scalar([row.background_level for row in rows], [row.quality_weight for row in rows]),
        "background_scatter": _weighted_scalar([row.background_scatter for row in rows], [row.quality_weight for row in rows]),
        "centroid_shift_pixels": _weighted_scalar([row.centroid_shift_pixels for row in rows], [row.quality_weight for row in rows]),
        "peak_pixel_value": max([row.peak_pixel_value for row in rows if row.peak_pixel_value is not None], default=None),
        "saturation_threshold": _weighted_scalar([row.saturation_threshold for row in rows], [row.quality_weight for row in rows]),
        "saturated_pixel_count": max([row.saturated_pixel_count for row in rows], default=0),
        "is_saturated": any(row.is_saturated for row in rows),
    }


def _direct_magnitude_fallback_bin(rows: list[PhotometryMeasurement]) -> dict[str, float | int | None] | None:
    mode = None
    values = [row.calibrated_magnitude for row in rows if row.calibrated_magnitude is not None and np.isfinite(row.calibrated_magnitude)]
    errors = [row.calibrated_magnitude_error for row in rows]
    if values:
        mode = "calibrated"
    else:
        values = [row.differential_magnitude for row in rows if row.differential_magnitude is not None and np.isfinite(row.differential_magnitude)]
        errors = [row.differential_magnitude_error for row in rows]
        if values:
            mode = "differential"
        else:
            values = [row.instrumental_magnitude for row in rows if row.instrumental_magnitude is not None and np.isfinite(row.instrumental_magnitude)]
            errors = [None for _row in rows]
            if values:
                mode = "instrumental"
    if mode is None or len(values) < 2:
        return None

    average_value, average_error = _weighted_mean_and_error(
        [
            row.calibrated_magnitude if mode == "calibrated" else (
                row.differential_magnitude if mode == "differential" else row.instrumental_magnitude
            )
            for row in rows
        ],
        errors,
        [row.quality_weight for row in rows],
    )
    if average_value is None:
        return None
    quality_score = _weighted_scalar([row.quality_score for row in rows], [row.quality_weight for row in rows])
    return {
        "flux": None,
        "flux_error": None,
        "snr": None,
        "instrumental_magnitude": average_value if mode == "instrumental" else _weighted_scalar([row.instrumental_magnitude for row in rows], [row.quality_weight for row in rows]),
        "differential_magnitude": average_value if mode == "differential" else _weighted_scalar([row.differential_magnitude for row in rows], [row.quality_weight for row in rows]),
        "differential_magnitude_error": average_error if mode == "differential" else _weighted_scalar([row.differential_magnitude_error for row in rows], [row.quality_weight for row in rows]),
        "calibrated_magnitude": average_value if mode == "calibrated" else _weighted_scalar([row.calibrated_magnitude for row in rows], [row.quality_weight for row in rows]),
        "calibrated_magnitude_error": average_error if mode == "calibrated" else _weighted_scalar([row.calibrated_magnitude_error for row in rows], [row.quality_weight for row in rows]),
        "zero_point_magnitude": _weighted_scalar([row.zero_point_magnitude for row in rows], [row.quality_weight for row in rows]),
        "zero_point_magnitude_error": _weighted_scalar([row.zero_point_magnitude_error for row in rows], [row.quality_weight for row in rows]),
        "zero_point_source_count": _weighted_scalar([row.zero_point_source_count for row in rows], [row.quality_weight for row in rows]),
        "comparison_reference_flux": _weighted_scalar([row.comparison_reference_flux for row in rows], [row.quality_weight for row in rows]),
        "comparison_scatter": _fractional_scatter([row.comparison_reference_flux for row in rows]),
        "quality_score": quality_score,
        "quality_weight": _weighted_scalar([row.quality_weight for row in rows], [row.quality_weight for row in rows]),
        "background_level": _weighted_scalar([row.background_level for row in rows], [row.quality_weight for row in rows]),
        "background_scatter": _weighted_scalar([row.background_scatter for row in rows], [row.quality_weight for row in rows]),
        "centroid_shift_pixels": _weighted_scalar([row.centroid_shift_pixels for row in rows], [row.quality_weight for row in rows]),
        "peak_pixel_value": max([row.peak_pixel_value for row in rows if row.peak_pixel_value is not None], default=None),
        "saturation_threshold": _weighted_scalar([row.saturation_threshold for row in rows], [row.quality_weight for row in rows]),
        "saturated_pixel_count": max([row.saturated_pixel_count for row in rows], default=0),
        "is_saturated": any(row.is_saturated for row in rows),
    }


def _weighted_mean_and_error(
    values: list[float | None],
    errors: list[float | None] | None,
    quality_weights: list[float | None] | None,
) -> tuple[float | None, float | None]:
    valid_rows = []
    for index, value in enumerate(values):
        if value is None:
            continue
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            continue
        if not np.isfinite(numeric_value):
            continue
        error_value = None
        if errors is not None and index < len(errors):
            raw_error = errors[index]
            if raw_error is not None:
                try:
                    numeric_error = float(raw_error)
                except (TypeError, ValueError):
                    numeric_error = float("nan")
                if np.isfinite(numeric_error) and numeric_error >= 0:
                    error_value = numeric_error
        quality_weight = 1.0
        if quality_weights is not None and index < len(quality_weights):
            raw_quality = quality_weights[index]
            if raw_quality is not None:
                try:
                    numeric_quality = float(raw_quality)
                except (TypeError, ValueError):
                    numeric_quality = float("nan")
                if np.isfinite(numeric_quality) and numeric_quality > 0:
                    quality_weight = numeric_quality
        valid_rows.append((numeric_value, error_value, quality_weight))
    if not valid_rows:
        return None, None

    explicit_weight_rows = [item for item in valid_rows if item[1] is not None and item[1] > 0]
    if explicit_weight_rows:
        weights = np.asarray([(item[2] / (item[1] * item[1])) for item in explicit_weight_rows], dtype=float)
        values_array = np.asarray([item[0] for item in explicit_weight_rows], dtype=float)
        weight_sum = float(np.sum(weights))
        if np.isfinite(weight_sum) and weight_sum > 0:
            return float(np.sum(values_array * weights) / weight_sum), math.sqrt(1.0 / weight_sum)

    values_array = np.asarray([item[0] for item in valid_rows], dtype=float)
    weights = np.asarray([item[2] for item in valid_rows], dtype=float)
    weight_sum = float(np.sum(weights))
    mean_value = float(np.sum(values_array * weights) / weight_sum) if np.isfinite(weight_sum) and weight_sum > 0 else float(np.mean(values_array))
    if values_array.size < 2:
        return mean_value, 0.0
    variance = float(np.average((values_array - mean_value) ** 2, weights=weights)) if np.isfinite(weight_sum) and weight_sum > 0 else float(np.var(values_array))
    return mean_value, math.sqrt(max(0.0, variance) / max(1, values_array.size))


def _weighted_scalar(values: list[float | int | None], weights: list[float | None] | None) -> float | None:
    weighted_value, _weighted_error = _weighted_mean_and_error([None if value is None else float(value) for value in values], None, weights)
    return weighted_value


def _fractional_scatter(values: list[float | None]) -> float | None:
    finite = np.asarray([float(value) for value in values if value is not None and np.isfinite(value) and value > 0], dtype=float)
    if finite.size < 2:
        return None
    center = float(np.median(finite))
    if center <= 0:
        return None
    return float(np.std(finite, ddof=1) / center)


def _sigma_clip_measurements(rows: list[PhotometryMeasurement], sigma_threshold: float) -> list[PhotometryMeasurement]:
    fluxes = np.asarray([
        float(row.flux)
        for row in rows
        if row.flux is not None and np.isfinite(row.flux) and row.flux > 0
    ], dtype=float)
    if fluxes.size < 3:
        return rows
    center = float(np.median(fluxes))
    mad = float(np.median(np.abs(fluxes - center)))
    if not np.isfinite(mad) or mad <= 0:
        return rows
    scale = 1.4826 * mad
    if scale <= 0:
        return rows
    clipped: list[PhotometryMeasurement] = []
    for row in rows:
        if row.flux is None or not np.isfinite(row.flux) or row.flux <= 0:
            clipped.append(row)
            continue
        if abs(float(row.flux) - center) <= (sigma_threshold * scale):
            clipped.append(row)
    return clipped if len(clipped) >= 2 else rows


def _average_observation_time(rows: list[PhotometryMeasurement]) -> datetime | None:
    timestamps = [row.observation_time for row in rows if row.observation_time is not None]
    if not timestamps:
        return None
    epoch = min(timestamps)
    offsets = [(timestamp - epoch).total_seconds() for timestamp in timestamps]
    return epoch + timedelta(seconds=float(np.mean(offsets)))


def _bin_duration_seconds(rows: list[PhotometryMeasurement], cadence_seconds: float) -> float:
    timestamps = [row.observation_time for row in rows if row.observation_time is not None]
    if len(timestamps) >= 2:
        ordered = sorted(timestamps)
        return max(0.0, (ordered[-1] - ordered[0]).total_seconds())
    return max(0.0, (len(rows) - 1) * cadence_seconds)


def _estimate_snr_improvement(
    original_rows: list[PhotometryMeasurement],
    derived_rows: list[PhotometryMeasurement],
    bin_sizes: list[int],
) -> float:
    original_snr = [float(row.snr) for row in original_rows if row.snr is not None and np.isfinite(row.snr) and row.snr > 0]
    derived_snr = [float(row.snr) for row in derived_rows if row.snr is not None and np.isfinite(row.snr) and row.snr > 0]
    if original_snr and derived_snr:
        base = float(median(original_snr))
        improved = float(median(derived_snr))
        if base > 0 and np.isfinite(improved):
            return max(1.0, improved / base)
    if not bin_sizes:
        return 1.0
    return max(1.0, math.sqrt(float(median(bin_sizes))))


def _derived_quality_weight(flux: float | None, flux_error: float | None, quality_score: float | None) -> float | None:
    if flux is None or flux_error is None or flux <= 0 or flux_error < 0:
        return quality_score
    quality = 1.0 if quality_score is None or not np.isfinite(quality_score) else max(0.0, float(quality_score))
    if flux_error == 0:
        return quality
    return quality / (flux_error * flux_error)


def _classify_variability_type(variability_type: str, object_type: str) -> str | None:
    normalized = str(variability_type or "").strip().lower()
    if object_type == "exoplanet" or "transit" in normalized:
        return "sharp"
    if any(marker in normalized for marker in _SHARP_TYPE_MARKERS):
        return "sharp"
    if any(marker in normalized for marker in _SMOOTH_TYPE_MARKERS):
        return "smooth"
    return None