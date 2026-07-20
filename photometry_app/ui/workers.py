from __future__ import annotations

from collections import deque
from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, ThreadPoolExecutor, as_completed, wait
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from itertools import combinations
import math
import multiprocessing
import os
from pathlib import Path
import sys
from statistics import median
from threading import Event
from time import perf_counter

import numpy as np
from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QImage

from photometry_app.core.alignment import align_wcs_image_sequence
from photometry_app.core.calibration import CalibrationPipelineRequest, calibrate_image_sequence
from photometry_app.core.catalogs import CatalogService, CatalogTargetAtCoordinate, CatalogTargetDetails, LiteraturePeriodResult, fetch_catalog_literature_period_result, fetch_catalog_target_details, fetch_catalog_targets_at_coordinate, summarize_catalog_service_error
from photometry_app.core.discovery import DiscoveryCancelledError, MovingObjectDiscoveryResult, MovingObjectRecoveryResult, _detection_within_estimated_limit, discover_unmatched_moving_candidates, export_discovery_residual_debug_outputs, recover_known_moving_objects
from photometry_app.core.exporters import AnimatedLightCurveExportCanceled, ScienceExportMetadata, export_light_curve_animated_gif
from photometry_app.core.matching import apply_differential_photometry, apply_measurement_quality_analysis, build_light_curve_series, measurement_has_usable_value, select_reference_stars
from photometry_app.core.models import CatalogStar, FileScanResult, LightCurveSeries, ManualPhotometryConfig, ObservationMetadata, PhotometryMeasurement, ProcessingReport, ScanReport, VariableSelectionPreview
from photometry_app.core.pipeline import PhotometryPipeline, _AAVSO_ANALYZE_BEST_MAX_SATURATION_FRACTION, _AAVSO_ANALYZE_BEST_MIN_PEAK_ABOVE_SKY_ADU, _resolve_photometry_parallel_workers
from photometry_app.core.photometry import measure_targets, resolve_aperture_profile
from photometry_app.core.period_tasks import calculate_period_for_series, calculate_period_task
from photometry_app.core.plotting import AnnotatedImageDisplay, AnnotatedImageRenderSettings, FitPeriodInferenceResult, LightCurveFitConfig, build_annotated_image_display, build_placeholder_annotated_image_display, render_image_path_for_display
from photometry_app.core.distance_map import build_distance_map
from photometry_app.core.sky_explorer import explore_sky_image
from photometry_app.core.settings import AppSettings, SkyAtlasCustomOverlayRecord
from photometry_app.core.sky_atlas_custom_overlay import import_sky_overlay
from photometry_app.core.solar_system import KnownObjectHeliocentricContext, SolarSystemDetection, SolarSystemDetectionResult, SolarSystemFrameMeasurement, SolarSystemVisibilityEstimateResult, build_known_object_heliocentric_context, build_multi_known_object_heliocentric_context, detect_known_solar_system_objects, estimate_visible_magnitude_limit, measure_detections_in_frame



from photometry_app.core.snr_binning import SnrBinningSettings, SnrBinningSourceResult, SnrBinningTask, process_snr_binning_task, summarize_snr_binning_result

from photometry_app.core.synthetic_tracking import SyntheticTrackingFrameTarget, SyntheticTrackingResult, build_synthetic_tracked_full_frame_stack, build_synthetic_tracked_stack

from photometry_app.core.transient import search_transients_in_folder





_MAX_LITERATURE_PERIOD_WORKERS = 4

_MAX_CALCULATED_PERIOD_WORKERS = 8

_MAX_COMPARISON_FIT_COMBINATIONS = 1024

_DISCOVERY_REFERENCE_POOL_LIMIT = 8

_DISCOVERY_MIN_VALID_POINTS = 4



def _configure_windows_process_pool_executable() -> None:

    if os.name != "nt" or getattr(sys, "frozen", False):

        return

    executable = Path(sys.executable).resolve()
    if executable.name.lower() == "pythonw.exe":
        console_executable = executable.with_name("python.exe")
        if console_executable.exists():
            executable = console_executable
    # ProcessPoolExecutor children need the console interpreter when the app is
    # launched from pythonw.exe; otherwise child imports can terminate abruptly.
    multiprocessing.set_executable(str(executable))



_configure_windows_process_pool_executable()



@dataclass(slots=True)

class CalculatedPeriodBatchResult:

    report_token: int

    results: dict[tuple[str, str], FitPeriodInferenceResult | None]

    success_count: int

    total_count: int

    worker_count: int

    execution_mode: str

    cancelled: bool = False

@dataclass(frozen=True, slots=True)
class _ComparisonGroupSeriesDiagnostics:

    comparison_source_ids: tuple[str, ...]

    comparison_source_names: tuple[str, ...]

    updated_target_row_count: int

    usable_target_row_count: int

    differential_value_count: int

    excluded_target_row_count: int


def _comparison_group_series_diagnostics_sort_key(diagnostics: _ComparisonGroupSeriesDiagnostics) -> tuple[int, int, int, int, str]:

    return (

        -int(diagnostics.usable_target_row_count),

        -int(diagnostics.differential_value_count),

        -int(diagnostics.updated_target_row_count),

        int(diagnostics.excluded_target_row_count),

        ",".join(diagnostics.comparison_source_ids),

    )


def _summarize_discovery_series_failure(

    diagnostics: _ComparisonGroupSeriesDiagnostics | None,

    *,

    raw_target_row_count: int,

) -> str:

    if diagnostics is None:

        return f"No tested comparison group produced a target series from {raw_target_row_count} raw target row(s)."

    comparison_label = ", ".join(diagnostics.comparison_source_names or diagnostics.comparison_source_ids)

    return (

        f"Raw target rows {raw_target_row_count}; best attempted comparison group ({comparison_label}) yielded "

        f"{diagnostics.updated_target_row_count} target row(s) after differential photometry, "

        f"{diagnostics.usable_target_row_count} usable value(s), "

        f"{diagnostics.differential_value_count} differential magnitude value(s), and "

        f"{diagnostics.excluded_target_row_count} excluded point(s)."

    )





@dataclass(slots=True)

class LiteraturePeriodBatchResult:

    report_token: int

    results: dict[tuple[str, str], LiteraturePeriodResult | None]

    success_count: int

    total_count: int

    worker_count: int

    execution_mode: str

    cancelled: bool = False





@dataclass(slots=True)

class IncreaseSnrBatchResult:

    report_token: int

    source_results: list[SnrBinningSourceResult]

    processed_count: int

    skipped_count: int

    failed_count: int

    cancelled: bool = False





@dataclass(slots=True)

class ComparisonFitTrialResult:

    comparison_source_ids: tuple[str, ...]

    comparison_source_names: tuple[str, ...]

    calculated_period_days: float | None

    match_index: float | None

    relative_error: float | None

    valid_point_count: int

    excluded_point_count: int

    period_convention: str = "standard"

    eclipsing_retry_attempted: bool = False

    eclipsing_retry_period_days: float | None = None

    eclipsing_retry_match_index: float | None = None





@dataclass(slots=True)

class ComparisonFitOptimizationResult:

    report_token: int

    target_source_id: str

    target_source_name: str

    filter_name: str

    actual_period_days: float

    total_combination_count: int

    evaluated_combination_count: int

    subset_size: int

    sampled_search: bool

    best_trial: ComparisonFitTrialResult | None

    best_measurements: list[PhotometryMeasurement] | None

    best_series: LightCurveSeries | None

    best_period_result: FitPeriodInferenceResult | None

    worker_count: int

    execution_mode: str

    cancelled: bool = False



@dataclass(slots=True)

class DiscoverSourceResult:

    entry: CatalogStar

    best_measurements: list[PhotometryMeasurement]

    best_series: LightCurveSeries

    best_period_result: FitPeriodInferenceResult | None

    total_combination_count: int

    evaluated_combination_count: int

    valid_point_count: int

    excluded_point_count: int

    median_comparison_scatter: float | None

    sampled_search: bool


@dataclass(slots=True)

class DiscoverTargetPlan:

    entry: CatalogStar

    search_type: str


@dataclass(slots=True)

class _DiscoverTargetEvaluationResult:

    candidate_index: int

    target_plan: DiscoverTargetPlan

    raw_target_row_count: int

    skip_message: str | None = None

    best_measurements: list[PhotometryMeasurement] | None = None

    best_series: LightCurveSeries | None = None

    best_failure_diagnostics: _ComparisonGroupSeriesDiagnostics | None = None

    total_group_count: int = 0

    evaluated_group_count: int = 0

    sampled_search: bool = False

    valid_point_count: int = 0

    excluded_point_count: int = 0

    median_comparison_scatter: float | None = None

    candidate_score: float = 0.0

@dataclass(slots=True)

class DiscoverBatchResult:

    report_token: int

    source_results: list[DiscoverSourceResult]

    tested_candidate_count: int

    retained_candidate_count: int

    reference_star_count: int

    total_candidate_count: int = 0

    current_stage_label: str | None = None

    cancelled: bool = False





@dataclass(slots=True)

class SourceReportExportTask:

    source_key: str

    source_name: str

    output_dir: Path

    measurements: list[PhotometryMeasurement]

    light_curves: list[LightCurveSeries]

    export_name_stem: str

    export_suffix: str | None = None





@dataclass(slots=True)

class SourceReportExportResult:

    source_key: str

    source_name: str

    output_dir: Path

    export_result: dict[str, Path | int]





@dataclass(slots=True)

class ReportExportBatchResult:

    export_root: Path

    source_results: list[SourceReportExportResult]

    cancelled: bool = False





@dataclass(slots=True)

class HrStarDetailsResult:

    row_key: tuple[str, str]

    details: CatalogTargetDetails | None





@dataclass(slots=True)

class AsteroidOrbitContextResult:

    detection: SolarSystemDetection | None

    frame_measurements: tuple[SolarSystemFrameMeasurement, ...]

    context: KnownObjectHeliocentricContext

    targets: tuple["AsteroidOrbitContextTarget", ...] = ()

    available_targets: tuple["AsteroidOrbitContextTarget", ...] = ()





@dataclass(slots=True)

class AsteroidOrbitContextTarget:

    detection: SolarSystemDetection

    frame_measurements: tuple[SolarSystemFrameMeasurement, ...]





def _fetch_literature_period_task(entry: object) -> tuple[tuple[str, str], str, str, LiteraturePeriodResult | None]:

    result = fetch_catalog_literature_period_result(entry)

    return (str(entry.catalog), str(entry.source_id)), str(entry.name), str(entry.catalog), result





def _recommended_calculated_period_workers(total_series: int) -> int:

    if total_series <= 0:

        return 1

    cpu_count = os.cpu_count() or 1

    return max(1, min(total_series, _MAX_CALCULATED_PERIOD_WORKERS, max(1, cpu_count - 1)))





def _catalog_source_key(entry: CatalogStar) -> tuple[str, str]:

    return (str(entry.catalog), str(entry.source_id))





def _discover_candidate_stars(

    field_catalog: object,

    reference_stars: list[CatalogStar],

    allowed_source_ids: set[str] | None = None,

) -> list[CatalogStar]:

    known_source_ids = {entry.source_id for entry in getattr(field_catalog, "variable_stars", [])}

    known_source_ids.update(entry.source_id for entry in getattr(field_catalog, "exoplanets", []))

    known_source_ids.update(entry.source_id for entry in reference_stars)

    return [

        entry

        for entry in getattr(field_catalog, "gaia_stars", [])

        if entry.source_id not in known_source_ids

        and entry.magnitude is not None

        and math.isfinite(float(entry.magnitude))

        and (allowed_source_ids is None or str(entry.source_id) in allowed_source_ids)

    ]


def _discover_raw_light_curve_target_stars(

    field_catalog: object,

    reference_stars: list[CatalogStar],

    allowed_source_ids: set[str] | None = None,

) -> list[CatalogStar]:

    known_source_ids = {entry.source_id for entry in getattr(field_catalog, "variable_stars", [])}

    known_source_ids.update(entry.source_id for entry in getattr(field_catalog, "exoplanets", []))

    known_source_ids.update(entry.source_id for entry in reference_stars)

    candidates = [

        entry

        for entry in getattr(field_catalog, "gaia_stars", [])

        if entry.source_id not in known_source_ids

        and (allowed_source_ids is None or str(entry.source_id) in allowed_source_ids)

    ]

    candidates.sort(

        key=lambda entry: (

            entry.magnitude is None or not math.isfinite(float(entry.magnitude)),

            float(entry.magnitude) if entry.magnitude is not None and math.isfinite(float(entry.magnitude)) else float("inf"),

            entry.name.lower(),

            entry.source_id,

        )

    )

    return candidates



def _discover_potential_candidate_stars(

    field_catalog: object,

    reference_stars: list[CatalogStar],

    settings: AppSettings,

    allowed_source_ids: set[str] | None = None,

) -> list[CatalogStar]:

    candidates = _discover_candidate_stars(field_catalog, reference_stars, allowed_source_ids)

    minimum_magnitude = settings.discovery_min_magnitude

    maximum_magnitude = settings.discovery_max_magnitude

    if minimum_magnitude is not None:

        candidates = [entry for entry in candidates if float(entry.magnitude) >= float(minimum_magnitude)]

    if maximum_magnitude is not None:

        candidates = [entry for entry in candidates if float(entry.magnitude) <= float(maximum_magnitude)]

    candidates.sort(key=lambda entry: (float(entry.magnitude), entry.name.lower(), entry.source_id))

    max_count = max(0, int(settings.discovery_max_candidate_count))

    if max_count > 0:

        return candidates[:max_count]

    return candidates





def _discovery_measurement_quality(

    measurements: list[PhotometryMeasurement],

    series: LightCurveSeries | None,

) -> tuple[int, int, float, float | None, float]:

    valid_point_count = sum(1 for measurement in measurements if measurement.differential_magnitude is not None)

    excluded_point_count = sum(1 for measurement in measurements if measurement.excluded_from_analysis)

    finite_quality_scores = [

        float(measurement.quality_score)

        for measurement in measurements

        if math.isfinite(float(measurement.quality_score))

    ]

    median_quality_score = float(median(finite_quality_scores)) if finite_quality_scores else 0.0

    finite_scatter = [

        float(measurement.comparison_scatter)

        for measurement in measurements

        if measurement.comparison_scatter is not None and math.isfinite(float(measurement.comparison_scatter))

    ]

    median_comparison_scatter = float(median(finite_scatter)) if finite_scatter else None

    candidate_score = 0.0 if series is None else float(series.candidate_score)

    return valid_point_count, excluded_point_count, median_quality_score, median_comparison_scatter, candidate_score





def _discovery_trial_is_better(

    candidate_measurements: list[PhotometryMeasurement],

    candidate_series: LightCurveSeries | None,

    best_measurements: list[PhotometryMeasurement] | None,

    best_series: LightCurveSeries | None,

) -> bool:

    candidate_valid, candidate_excluded, candidate_quality, candidate_scatter, candidate_score = _discovery_measurement_quality(

        candidate_measurements,

        candidate_series,

    )

    if best_measurements is None:

        return candidate_valid > 0

    best_valid, best_excluded, best_quality, best_scatter, best_score = _discovery_measurement_quality(

        best_measurements,

        best_series,

    )

    candidate_key = (

        -candidate_valid,

        candidate_excluded,

        float("inf") if candidate_scatter is None else candidate_scatter,

        -candidate_quality,

        -candidate_score,

    )

    best_key = (

        -best_valid,

        best_excluded,

        float("inf") if best_scatter is None else best_scatter,

        -best_quality,

        -best_score,

    )

    return candidate_key < best_key





def _prepare_discovery_target(entry: CatalogStar, *, discover_name: str, discover_type: str) -> CatalogStar:

    metadata = dict(entry.metadata)

    metadata["discover_candidate"] = True

    metadata["discover_name"] = discover_name

    metadata["discover_type"] = discover_type

    metadata.setdefault("discover_original_name", entry.name)

    metadata.setdefault("discover_origin", "gaia")

    return replace(entry, name=discover_name, is_variable=True, metadata=metadata)


def _measurement_matches_discovery_ideal_adu_range(measurement: PhotometryMeasurement) -> bool:

    peak_value = measurement.peak_pixel_value

    if peak_value is None or not np.isfinite(float(peak_value)):

        return False

    background_level = measurement.background_level if measurement.background_level is not None else 0.0

    peak_above_sky = float(peak_value) - float(background_level)

    if peak_above_sky < _AAVSO_ANALYZE_BEST_MIN_PEAK_ABOVE_SKY_ADU:

        return False

    saturation_threshold = measurement.saturation_threshold

    if saturation_threshold is not None and np.isfinite(float(saturation_threshold)) and float(saturation_threshold) > 0:

        if float(peak_value) > float(saturation_threshold) * _AAVSO_ANALYZE_BEST_MAX_SATURATION_FRACTION:

            return False

    if measurement.is_saturated:

        return False

    return True



def _resolved_calculated_period_workers(total_series: int, configured_workers: int) -> int:

    if total_series <= 0:

        return 1

    cpu_count = os.cpu_count() or 1

    if configured_workers > 0:

        return max(1, min(total_series, _MAX_CALCULATED_PERIOD_WORKERS, cpu_count, configured_workers))

    return _recommended_calculated_period_workers(total_series)





def _recommended_literature_period_workers(total_entries: int) -> int:

    if total_entries <= 0:

        return 1

    return max(1, min(total_entries, _MAX_LITERATURE_PERIOD_WORKERS))





def _resolved_literature_period_workers(total_entries: int, configured_workers: int) -> int:

    if total_entries <= 0:

        return 1

    if configured_workers > 0:

        return max(1, min(total_entries, _MAX_LITERATURE_PERIOD_WORKERS, configured_workers))

    return _recommended_literature_period_workers(total_entries)





def _recommended_comparison_fit_workers(total_groups: int) -> int:

    if total_groups <= 0:

        return 1

    cpu_count = os.cpu_count() or 1

    return max(1, min(total_groups, max(1, cpu_count - 1)))





def _resolved_comparison_fit_workers(total_groups: int, configured_workers: int) -> int:

    cpu_count = os.cpu_count() or 1

    if configured_workers > 0:

        return max(1, min(total_groups, cpu_count, configured_workers))

    return _recommended_comparison_fit_workers(total_groups)





def _comparison_measurement_key(measurement: PhotometryMeasurement) -> tuple[str, str, str, str]:

    return (

        measurement.source_id,

        measurement.filter_name or "-",

        measurement.file_path.name,

        measurement.observation_time.isoformat(sep=" ") if measurement.observation_time else "-",

    )





def _comparison_fit_relative_error(actual_period_days: float, fit_result: FitPeriodInferenceResult | None) -> tuple[float | None, float | None, float | None]:

    if fit_result is None or not math.isfinite(actual_period_days) or actual_period_days <= 0:

        return None, None, None

    calculated_period_days = fit_result.period_hours / 24.0

    if not math.isfinite(calculated_period_days) or calculated_period_days <= 0:

        return None, None, None

    relative_error = abs(calculated_period_days - actual_period_days) / actual_period_days

    match_index = max(0.0, 100.0 * (1.0 - relative_error))

    return calculated_period_days, relative_error, match_index





def _ordered_reference_candidate_ids(

    target_measurements: list[PhotometryMeasurement],

    reference_measurements: list[PhotometryMeasurement],

) -> list[str]:

    if not target_measurements:

        return []

    target = target_measurements[0]

    representative_rows: dict[str, PhotometryMeasurement] = {}

    for measurement in reference_measurements:

        if not measurement.is_reference:

            continue

        if measurement.flux is None or measurement.flux <= 0:

            continue

        if measurement.is_saturated or measurement.is_near_saturated:

            continue

        representative_rows.setdefault(measurement.source_id, measurement)

    ordered_rows = sorted(

        representative_rows.values(),

        key=lambda item: (

            ((item.ra_deg - target.ra_deg) ** 2) + ((item.dec_deg - target.dec_deg) ** 2),

            item.source_name.lower(),

            item.source_id,

        ),

    )

    return [item.source_id for item in ordered_rows]





def _magnitude_sorted_reference_candidate_ids(

    target_measurements: list[PhotometryMeasurement],

    reference_measurements: list[PhotometryMeasurement],

    target_magnitude: float | None,

    reference_source_magnitudes: dict[str, float],

    magnitude_tolerance: float,

    candidate_pool_size: int,

) -> list[str]:

    if not target_measurements or target_magnitude is None or not math.isfinite(target_magnitude):

        return []

    if magnitude_tolerance <= 0 or candidate_pool_size <= 0:

        return []

    target = target_measurements[0]

    representative_rows: dict[str, PhotometryMeasurement] = {}

    for measurement in reference_measurements:

        if not measurement.is_reference:

            continue

        if measurement.flux is None or measurement.flux <= 0:

            continue

        if measurement.is_saturated or measurement.is_near_saturated:

            continue

        source_magnitude = reference_source_magnitudes.get(measurement.source_id)

        if source_magnitude is None or not math.isfinite(source_magnitude):

            continue

        if abs(source_magnitude - target_magnitude) > magnitude_tolerance:

            continue

        representative_rows.setdefault(measurement.source_id, measurement)

    ordered_rows = sorted(

        representative_rows.values(),

        key=lambda item: (

            abs(reference_source_magnitudes.get(item.source_id, target_magnitude) - target_magnitude),

            ((item.ra_deg - target.ra_deg) ** 2) + ((item.dec_deg - target.dec_deg) ** 2),

            item.source_name.lower(),

            item.source_id,

        ),

    )

    return [item.source_id for item in ordered_rows[:candidate_pool_size]]





def _resolved_fallback_target_magnitude(

    target_measurements: list[PhotometryMeasurement],

    target_magnitude: float | None,

) -> float | None:

    if target_magnitude is not None and math.isfinite(target_magnitude):

        return float(target_magnitude)



    calibrated_magnitudes = [

        float(measurement.calibrated_magnitude)

        for measurement in target_measurements

        if measurement.calibrated_magnitude is not None and math.isfinite(measurement.calibrated_magnitude)

    ]

    if calibrated_magnitudes:

        return float(median(calibrated_magnitudes))



    catalog_magnitudes = [

        float(measurement.catalog_magnitude)

        for measurement in target_measurements

        if measurement.catalog_magnitude is not None and math.isfinite(measurement.catalog_magnitude)

    ]

    if catalog_magnitudes:

        return float(median(catalog_magnitudes))



    return None


def _discover_ranked_reference_candidate_ids(

    target_measurements: list[PhotometryMeasurement],

    reference_measurements: list[PhotometryMeasurement],

    target_magnitude: float | None,

    reference_source_magnitudes: dict[str, float],

) -> list[str]:

    resolved_target_magnitude = _resolved_fallback_target_magnitude(target_measurements, target_magnitude)

    if resolved_target_magnitude is None or not math.isfinite(resolved_target_magnitude):

        return _ordered_reference_candidate_ids(target_measurements, reference_measurements)

    representative_rows: dict[str, PhotometryMeasurement] = {}

    for measurement in reference_measurements:

        if not measurement.is_reference:

            continue

        if measurement.flux is None or measurement.flux <= 0:

            continue

        if measurement.is_saturated or measurement.is_near_saturated:

            continue

        representative_rows.setdefault(measurement.source_id, measurement)

    ordered_rows = sorted(

        representative_rows.values(),

        key=lambda item: (

            0 if item.source_id in reference_source_magnitudes and math.isfinite(reference_source_magnitudes[item.source_id]) else 1,

            abs(reference_source_magnitudes.get(item.source_id, resolved_target_magnitude) - resolved_target_magnitude),

            ((item.ra_deg - target_measurements[0].ra_deg) ** 2) + ((item.dec_deg - target_measurements[0].dec_deg) ** 2),

            item.source_name.lower(),

            item.source_id,

        ),

    )

    return [item.source_id for item in ordered_rows]





def _comparison_source_names_by_id(reference_measurements: list[PhotometryMeasurement]) -> dict[str, str]:

    name_lookup: dict[str, str] = {}

    for measurement in reference_measurements:

        name_lookup.setdefault(measurement.source_id, measurement.source_name)

    return name_lookup


def _reference_measurement_frame_key(measurement: PhotometryMeasurement) -> tuple[str, str | None]:

    return str(measurement.file_path), measurement.filter_name


def _index_reference_measurements_by_source_and_frame(

    reference_measurements: Sequence[PhotometryMeasurement],

) -> dict[str, dict[tuple[str, str | None], PhotometryMeasurement]]:

    index: dict[str, dict[tuple[str, str | None], PhotometryMeasurement]] = {}

    for measurement in reference_measurements:

        if not measurement.is_reference:

            continue

        source_rows = index.setdefault(measurement.source_id, {})

        source_rows.setdefault(_reference_measurement_frame_key(measurement), measurement)

    return index


def _comparison_group_reference_measurements(

    reference_index: dict[str, dict[tuple[str, str | None], PhotometryMeasurement]],

    comparison_source_ids: tuple[str, ...],

    target_measurements: Sequence[PhotometryMeasurement],

) -> list[PhotometryMeasurement]:

    target_frame_keys = list(dict.fromkeys(_reference_measurement_frame_key(measurement) for measurement in target_measurements))

    selected_measurements: list[PhotometryMeasurement] = []

    for source_id in comparison_source_ids:

        source_rows = reference_index.get(source_id)

        if not source_rows:

            continue

        for frame_key in target_frame_keys:

            measurement = source_rows.get(frame_key)

            if measurement is not None:

                selected_measurements.append(measurement)

    return selected_measurements





def _normalize_preferred_comparison_ids(

    preferred_ids: list[str],

    candidate_ids: list[str],

    subset_size: int,

) -> tuple[str, ...] | None:

    if subset_size <= 0:

        return None

    candidate_set = set(candidate_ids)

    normalized = [source_id for source_id in preferred_ids if source_id in candidate_set]

    if len(normalized) != subset_size or len(set(normalized)) != subset_size:

        return None

    return tuple(normalized)





def _comparison_source_groups(

    candidate_ids: list[str],

    subset_size: int,

    preferred_ids: list[str],

) -> tuple[list[tuple[str, ...]], int, bool]:

    if subset_size <= 0 or len(candidate_ids) < subset_size:

        return [], 0, False

    total_count = math.comb(len(candidate_ids), subset_size)

    preferred_group = _normalize_preferred_comparison_ids(preferred_ids, candidate_ids, subset_size)

    nearest_group = tuple(candidate_ids[:subset_size])

    preferred_groups = [group for group in (preferred_group, nearest_group) if group is not None]



    if total_count <= _MAX_COMPARISON_FIT_COMBINATIONS:

        all_groups = list(combinations(candidate_ids, subset_size))

        ordered_groups: list[tuple[str, ...]] = []

        seen_groups: set[tuple[str, ...]] = set()

        for group in preferred_groups:

            if group in all_groups and group not in seen_groups:

                ordered_groups.append(group)

                seen_groups.add(group)

        for group in all_groups:

            if group in seen_groups:

                continue

            ordered_groups.append(group)

        return ordered_groups, total_count, False



    ordered_groups = []

    seen_groups: set[tuple[str, ...]] = set()

    for group in preferred_groups:

        if group in seen_groups:

            continue

        ordered_groups.append(group)

        seen_groups.add(group)



    seed_value = "|".join(candidate_ids) + f":{subset_size}:{total_count}"

    randomizer = random.Random(seed_value)

    candidate_indexes = list(range(len(candidate_ids)))

    while len(ordered_groups) < _MAX_COMPARISON_FIT_COMBINATIONS:

        selected_indexes = tuple(sorted(randomizer.sample(candidate_indexes, subset_size)))

        group = tuple(candidate_ids[index] for index in selected_indexes)

        if group in seen_groups:

            continue

        ordered_groups.append(group)

        seen_groups.add(group)

    return ordered_groups, total_count, True





def _evaluate_comparison_source_group_core(

    target_measurements: list[PhotometryMeasurement],

    reference_measurements: list[PhotometryMeasurement],

    comparison_source_ids: tuple[str, ...],

    fit_config: LightCurveFitConfig | None,

    y_axis_mode: str,

    period_method: str,

    period_convention: str,

    *,

    calculate_period: bool = True,

) -> tuple[list[PhotometryMeasurement], LightCurveSeries | None, FitPeriodInferenceResult | None, _ComparisonGroupSeriesDiagnostics]:

    name_lookup = _comparison_source_names_by_id(reference_measurements)

    comparison_names = [name_lookup.get(source_id, source_id) for source_id in comparison_source_ids]

    filter_name = target_measurements[0].filter_name or "unknown"

    seeded_target_measurements = [

        replace(

            measurement,

            comparison_source_ids=list(comparison_source_ids),

            comparison_source_names=list(comparison_names),

        )

        for measurement in target_measurements

    ]

    updated_measurements = apply_differential_photometry(

        seeded_target_measurements + list(reference_measurements),

        nearby_reference_count=max(1, len(comparison_source_ids)),

    )

    updated_measurements = apply_measurement_quality_analysis(updated_measurements)

    updated_target_rows = [

        measurement

        for measurement in updated_measurements

        if not measurement.is_reference and measurement.source_id == target_measurements[0].source_id and (measurement.filter_name or "unknown") == filter_name

    ]

    diagnostics = _ComparisonGroupSeriesDiagnostics(

        comparison_source_ids=tuple(comparison_source_ids),

        comparison_source_names=tuple(comparison_names),

        updated_target_row_count=len(updated_target_rows),

        usable_target_row_count=sum(1 for measurement in updated_target_rows if measurement_has_usable_value(measurement)),

        differential_value_count=sum(1 for measurement in updated_target_rows if measurement.differential_magnitude is not None),

        excluded_target_row_count=sum(1 for measurement in updated_target_rows if measurement.excluded_from_analysis),

    )



    target_lookup = {

        _comparison_measurement_key(measurement): measurement

        for measurement in updated_measurements

        if not measurement.is_reference and measurement.source_id == target_measurements[0].source_id

    }

    optimized_target_measurements = [

        target_lookup.get(_comparison_measurement_key(measurement), measurement)

        for measurement in target_measurements

    ]



    optimized_series = None

    for series in build_light_curve_series(updated_measurements):

        if series.source_id == target_measurements[0].source_id and series.filter_name == filter_name:

            optimized_series = series

            break

    optimized_period_result = None if optimized_series is None or not calculate_period else calculate_period_for_series(

        optimized_series,

        fit_config=fit_config,

        y_axis_mode=y_axis_mode,

        period_method=period_method,

        period_convention=period_convention,

    )

    return optimized_target_measurements, optimized_series, optimized_period_result, diagnostics


def _evaluate_comparison_source_group(

    target_measurements: list[PhotometryMeasurement],

    reference_measurements: list[PhotometryMeasurement],

    comparison_source_ids: tuple[str, ...],

    fit_config: LightCurveFitConfig | None,

    y_axis_mode: str,

    period_method: str,

    period_convention: str,

) -> tuple[list[PhotometryMeasurement], LightCurveSeries | None, FitPeriodInferenceResult | None]:

    optimized_target_measurements, optimized_series, optimized_period_result, _diagnostics = _evaluate_comparison_source_group_core(

        target_measurements,

        reference_measurements,

        comparison_source_ids,

        fit_config,

        y_axis_mode,

        period_method,

        period_convention,

    )

    return optimized_target_measurements, optimized_series, optimized_period_result


def _evaluate_discover_target_plan(

    *,

    candidate_index: int,

    total_candidate_count: int,

    target_plan: DiscoverTargetPlan,

    target_measurements: list[PhotometryMeasurement],

    reference_candidate_measurements: list[PhotometryMeasurement],

    reference_measurement_index: dict[str, dict[tuple[str, str | None], PhotometryMeasurement]],

    reference_source_magnitudes: dict[str, float],

    nearby_reference_count: int,

    search_multiple_comparison_groups: bool,

    y_axis_mode: str,

    period_method: str,

    period_convention: str,

) -> _DiscoverTargetEvaluationResult:

    catalog_entry = target_plan.entry

    usable_target_measurements = [

        measurement for measurement in target_measurements if measurement.flux is not None or measurement.instrumental_magnitude is not None

    ]

    if len(usable_target_measurements) < _DISCOVERY_MIN_VALID_POINTS:

        return _DiscoverTargetEvaluationResult(

            candidate_index=candidate_index,

            target_plan=target_plan,

            raw_target_row_count=len(target_measurements),

            skip_message=(

                f"[Discover {candidate_index}/{total_candidate_count} | {target_plan.search_type}] Skipped {catalog_entry.name}: "

                f"only {len(usable_target_measurements)} usable measurement point(s)."

            ),

        )

    target_magnitude = (

        float(catalog_entry.magnitude)

        if catalog_entry.magnitude is not None and math.isfinite(float(catalog_entry.magnitude))

        else None

    )

    candidate_ids = _discover_ranked_reference_candidate_ids(

        target_measurements,

        reference_candidate_measurements,

        target_magnitude,

        reference_source_magnitudes,

    )

    if len(candidate_ids) < 1:

        return _DiscoverTargetEvaluationResult(

            candidate_index=candidate_index,

            target_plan=target_plan,

            raw_target_row_count=len(target_measurements),

            skip_message=(

                f"[Discover {candidate_index}/{total_candidate_count} | {target_plan.search_type}] Skipped {catalog_entry.name}: "

                "no usable comparison stars remained after quality screening."

            ),

        )

    subset_size = min(max(1, int(nearby_reference_count)), len(candidate_ids))

    if search_multiple_comparison_groups:

        pool_limit = min(len(candidate_ids), max(_DISCOVERY_REFERENCE_POOL_LIMIT, subset_size + 3))

        comparison_groups, total_group_count, sampled_search = _comparison_source_groups(

            candidate_ids[:pool_limit],

            subset_size,

            [],

        )

    else:

        comparison_groups = [tuple(candidate_ids[:subset_size])]

        total_group_count = len(comparison_groups)

        sampled_search = False

    if not comparison_groups:

        return _DiscoverTargetEvaluationResult(

            candidate_index=candidate_index,

            target_plan=target_plan,

            raw_target_row_count=len(target_measurements),

            skip_message=(

                f"[Discover {candidate_index}/{total_candidate_count} | {target_plan.search_type}] Skipped {catalog_entry.name}: "

                "no comparison-star group could be formed."

            ),

        )

    best_measurements: list[PhotometryMeasurement] | None = None

    best_series: LightCurveSeries | None = None

    best_failure_diagnostics: _ComparisonGroupSeriesDiagnostics | None = None

    for comparison_group in comparison_groups:

        selected_reference_measurements = _comparison_group_reference_measurements(

            reference_measurement_index,

            comparison_group,

            target_measurements,

        )

        optimized_measurements, optimized_series, _period_result, diagnostics = _evaluate_comparison_source_group_core(

            target_measurements,

            selected_reference_measurements,

            comparison_group,

            None,

            y_axis_mode,

            period_method,

            period_convention,

            calculate_period=False,

        )

        if optimized_series is None:

            if best_failure_diagnostics is None or _comparison_group_series_diagnostics_sort_key(diagnostics) < _comparison_group_series_diagnostics_sort_key(best_failure_diagnostics):

                best_failure_diagnostics = diagnostics

            continue

        if _discovery_trial_is_better(optimized_measurements, optimized_series, best_measurements, best_series):

            best_measurements = optimized_measurements

            best_series = optimized_series

    if best_measurements is None or best_series is None:

        return _DiscoverTargetEvaluationResult(

            candidate_index=candidate_index,

            target_plan=target_plan,

            raw_target_row_count=len(target_measurements),

            best_failure_diagnostics=best_failure_diagnostics,

            total_group_count=total_group_count,

            evaluated_group_count=len(comparison_groups),

            sampled_search=sampled_search,

            skip_message=(

                f"[Discover {candidate_index}/{total_candidate_count} | {target_plan.search_type}] Skipped {catalog_entry.name}: "

                f"no differential light curve could be built. {_summarize_discovery_series_failure(best_failure_diagnostics, raw_target_row_count=len(target_measurements))}"

            ),

        )

    valid_point_count, excluded_point_count, _median_quality_score, median_comparison_scatter, candidate_score = _discovery_measurement_quality(

        best_measurements,

        best_series,

    )

    return _DiscoverTargetEvaluationResult(

        candidate_index=candidate_index,

        target_plan=target_plan,

        raw_target_row_count=len(target_measurements),

        best_measurements=best_measurements,

        best_series=best_series,

        total_group_count=total_group_count,

        evaluated_group_count=len(comparison_groups),

        sampled_search=sampled_search,

        valid_point_count=valid_point_count,

        excluded_point_count=excluded_point_count,

        median_comparison_scatter=median_comparison_scatter,

        candidate_score=candidate_score,

    )





def _comparison_fit_task(

    target_measurements: list[PhotometryMeasurement],

    reference_measurements: list[PhotometryMeasurement],

    comparison_source_ids: tuple[str, ...],

    actual_period_days: float,

    fit_config: LightCurveFitConfig | None,

    y_axis_mode: str,

    period_method: str,

    period_convention: str,

    eclipsing_binary_match_tolerance: float = 0.0,

) -> tuple[tuple[str, ...], float | None, float | None, float | None, int, int, str, bool, float | None, float | None]:

    optimized_measurements, _optimized_series, optimized_period_result = _evaluate_comparison_source_group(

        target_measurements,

        reference_measurements,

        comparison_source_ids,

        fit_config,

        y_axis_mode,

        period_method,

        period_convention,

    )

    calculated_period_days, relative_error, match_index = _comparison_fit_relative_error(

        actual_period_days,

        optimized_period_result,

    )

    valid_point_count = sum(1 for item in optimized_measurements if item.differential_magnitude is not None)

    excluded_point_count = sum(1 for item in optimized_measurements if item.excluded_from_analysis)

    resolved_period_convention = str(period_convention).strip().lower() or "standard"

    eclipsing_retry_attempted = False

    eclipsing_retry_period_days: float | None = None

    eclipsing_retry_match_index: float | None = None



    if (

        resolved_period_convention != "eclipsing_binary"

        and eclipsing_binary_match_tolerance > 0

        and match_index is not None

        and abs(match_index - 50.0) <= eclipsing_binary_match_tolerance

    ):

        eclipsing_retry_attempted = True

        eclipsing_measurements, _eclipsing_series, eclipsing_period_result = _evaluate_comparison_source_group(

            target_measurements,

            reference_measurements,

            comparison_source_ids,

            fit_config,

            y_axis_mode,

            period_method,

            "eclipsing_binary",

        )

        eclipsing_period_days, eclipsing_relative_error, eclipsing_match_index = _comparison_fit_relative_error(

            actual_period_days,

            eclipsing_period_result,

        )

        eclipsing_retry_period_days = eclipsing_period_days

        eclipsing_retry_match_index = eclipsing_match_index

        eclipsing_valid_point_count = sum(1 for item in eclipsing_measurements if item.differential_magnitude is not None)

        if eclipsing_match_index is not None and (

            match_index is None

            or eclipsing_match_index > match_index

            or (

                math.isclose(eclipsing_match_index, match_index, rel_tol=1e-9, abs_tol=1e-9)

                and eclipsing_valid_point_count > valid_point_count

            )

        ):

            calculated_period_days = eclipsing_period_days

            relative_error = eclipsing_relative_error

            match_index = eclipsing_match_index

            valid_point_count = eclipsing_valid_point_count

            excluded_point_count = sum(1 for item in eclipsing_measurements if item.excluded_from_analysis)

            resolved_period_convention = "eclipsing_binary"

    return (

        comparison_source_ids,

        calculated_period_days,

        relative_error,

        match_index,

        valid_point_count,

        excluded_point_count,

        resolved_period_convention,

        eclipsing_retry_attempted,

        eclipsing_retry_period_days,

        eclipsing_retry_match_index,

    )





def _comparison_fit_trial_from_task_result(

    task_result: tuple[tuple[str, ...], float | None, float | None, float | None, int, int, str, bool, float | None, float | None],

    name_lookup: dict[str, str],

) -> ComparisonFitTrialResult:

    (

        comparison_source_ids,

        calculated_period_days,

        relative_error,

        match_index,

        valid_point_count,

        excluded_point_count,

        period_convention,

        eclipsing_retry_attempted,

        eclipsing_retry_period_days,

        eclipsing_retry_match_index,

    ) = task_result

    return ComparisonFitTrialResult(

        comparison_source_ids=tuple(comparison_source_ids),

        comparison_source_names=tuple(name_lookup.get(source_id, source_id) for source_id in comparison_source_ids),

        calculated_period_days=calculated_period_days,

        match_index=match_index,

        relative_error=relative_error,

        valid_point_count=valid_point_count,

        excluded_point_count=excluded_point_count,

        period_convention=str(period_convention).strip().lower() or "standard",

        eclipsing_retry_attempted=bool(eclipsing_retry_attempted),

        eclipsing_retry_period_days=eclipsing_retry_period_days,

        eclipsing_retry_match_index=eclipsing_retry_match_index,

    )





def _comparison_fit_trial_is_better(candidate: ComparisonFitTrialResult, best_trial: ComparisonFitTrialResult | None) -> bool:

    if candidate.match_index is None:

        return False

    if best_trial is None or best_trial.match_index is None:

        return True

    if candidate.match_index > best_trial.match_index:

        return True

    return bool(

        math.isclose(candidate.match_index, best_trial.match_index, rel_tol=1e-9, abs_tol=1e-9)

        and candidate.valid_point_count > best_trial.valid_point_count

    )





def _comparison_fit_progress_message(index: int, total_count: int, trial: ComparisonFitTrialResult) -> str:

    comparison_label = ", ".join(trial.comparison_source_names)

    if trial.calculated_period_days is None or trial.match_index is None:

        return f"[Comparison Fit {index}/{total_count}] {comparison_label}: no valid calculated period result."

    message = (

        f"[Comparison Fit {index}/{total_count}] {comparison_label}: period={trial.calculated_period_days:.4f} d, "

        f"match={trial.match_index:.2f}, usable points={trial.valid_point_count}"

    )

    if trial.eclipsing_retry_attempted:

        if trial.eclipsing_retry_period_days is not None and trial.eclipsing_retry_match_index is not None:

            if trial.period_convention == "eclipsing_binary":

                message += (

                    f"; eclipsing-binary retry selected period={trial.eclipsing_retry_period_days:.4f} d, "

                    f"match={trial.eclipsing_retry_match_index:.2f}"

                )

            else:

                message += (

                    f"; eclipsing-binary retry tested period={trial.eclipsing_retry_period_days:.4f} d, "

                    f"match={trial.eclipsing_retry_match_index:.2f}"

                )

        else:

            message += "; eclipsing-binary retry attempted"

    elif trial.period_convention == "eclipsing_binary":

        message += "; using eclipsing-binary convention"

    return message + "."





class ScanWorker(QThread):

    scan_completed = Signal(object)

    scan_failed = Signal(str)



    def __init__(self, root_path: Path, parent: object | None = None) -> None:

        super().__init__(parent)

        self._root_path = root_path

        self._pipeline = PhotometryPipeline()



    def run(self) -> None:

        try:

            report: ScanReport = self._pipeline.scan_workspace(self._root_path)

        except Exception as exc:

            self.scan_failed.emit(str(exc))

            return



        self.scan_completed.emit(report)





class ProcessWorker(QThread):

    process_completed = Signal(object)

    process_failed = Signal(str)

    progress_updated = Signal(str)



    def __init__(

        self,

        root_path: Path,

        object_name: str,

        manual_config_override: ManualPhotometryConfig | None = None,

        settings_override: AppSettings | None = None,

        analyze_best_targets: bool = False,

        parent: object | None = None,

    ) -> None:

        super().__init__(parent)

        self._root_path = root_path

        self._object_name = object_name

        self._manual_config_override = manual_config_override

        self._settings_override = settings_override

        self._analyze_best_targets = bool(analyze_best_targets)

        self._pipeline = PhotometryPipeline()



    def run(self) -> None:

        try:

            report: ProcessingReport = self._pipeline.process_object(

                self._root_path,

                self._object_name,

                manual_config_override=self._manual_config_override,

                settings_override=self._settings_override,

                analyze_best_targets=self._analyze_best_targets,

                progress_callback=self.progress_updated.emit,

            )

        except Exception as exc:

            self.process_failed.emit(str(exc))

            return



        self.process_completed.emit(report)





class CachedProcessingReportWorker(QThread):

    cache_loaded = Signal(object)

    cache_failed = Signal(str)



    def __init__(

        self,

        root_path: Path,

        object_name: str,

        summary_files: list[FileScanResult] | None = None,

        manual_config_override: ManualPhotometryConfig | None = None,

        settings_override: AppSettings | None = None,

        analyze_best_targets: bool = False,

        parent: object | None = None,

    ) -> None:

        super().__init__(parent)

        self._root_path = root_path

        self._object_name = object_name

        self._summary_files = None if summary_files is None else list(summary_files)

        self._manual_config_override = manual_config_override

        self._settings_override = settings_override

        self._analyze_best_targets = bool(analyze_best_targets)

        self._pipeline = PhotometryPipeline()



    def run(self) -> None:

        try:

            report, cache_note = self._pipeline.load_cached_processing_report(

                self._root_path,

                self._object_name,

                summary_files=self._summary_files,

                manual_config_override=self._manual_config_override,

                settings_override=self._settings_override,

                analyze_best_targets=self._analyze_best_targets,

            )

        except Exception as exc:

            self.cache_failed.emit(str(exc))

            return

        self.cache_loaded.emit((report, cache_note))




class PreviewWorker(QThread):

    preview_completed = Signal(object)

    preview_failed = Signal(str)

    progress_updated = Signal(str)



    def __init__(self, root_path: Path, object_name: str, parent: object | None = None) -> None:

        super().__init__(parent)

        self._root_path = root_path

        self._object_name = object_name

        self._pipeline = PhotometryPipeline()



    def run(self) -> None:

        try:

            preview: VariableSelectionPreview = self._pipeline.preview_variable_selection(

                self._root_path,

                self._object_name,

                progress_callback=self.progress_updated.emit,

            )

        except Exception as exc:

            self.preview_failed.emit(str(exc))

            return



        self.preview_completed.emit(preview)





class ReportExportWorker(QThread):

    export_completed = Signal(object)

    export_failed = Signal(str)

    progress_updated = Signal(str)



    def __init__(

        self,

        report: ProcessingReport,

        export_root: Path,

        export_tasks: list[SourceReportExportTask],

        plot_y_axis_mode: str,

        plot_theme: str,

        custom_theme_colors: dict[str, str] | None,

        science_metadata: ScienceExportMetadata,

        parent: object | None = None,

    ) -> None:

        super().__init__(parent)

        self._report = report

        self._export_root = export_root

        self._export_tasks = list(export_tasks)

        self._plot_y_axis_mode = plot_y_axis_mode

        self._plot_theme = plot_theme

        self._custom_theme_colors = None if custom_theme_colors is None else dict(custom_theme_colors)

        self._science_metadata = science_metadata

        self._pipeline = PhotometryPipeline()

        self._cancel_requested = Event()



    def request_cancel(self) -> None:

        self._cancel_requested.set()



    def cancellation_requested(self) -> bool:

        return self._cancel_requested.is_set()



    def run(self) -> None:

        source_results: list[SourceReportExportResult] = []

        total_count = len(self._export_tasks)

        if total_count == 0:

            self.export_completed.emit(ReportExportBatchResult(export_root=self._export_root, source_results=[]))

            return



        for index, task in enumerate(self._export_tasks, start=1):

            if self._cancel_requested.is_set():

                self.export_completed.emit(

                    ReportExportBatchResult(

                        export_root=self._export_root,

                        source_results=source_results,

                        cancelled=True,

                    )

                )

                return

            self.progress_updated.emit(

                f"Exporting science bundle for {task.source_name} ({index}/{total_count}) to {task.output_dir}."

            )

            try:

                export_result = self._pipeline.export_results(

                    self._report,

                    task.output_dir,

                    measurements=task.measurements,

                    light_curves=task.light_curves,

                    export_suffix=task.export_suffix,

                    export_name_stem=task.export_name_stem,

                    plot_y_axis_mode=self._plot_y_axis_mode,

                    plot_theme=self._plot_theme,

                    custom_theme_colors=self._custom_theme_colors,

                    science_metadata=self._science_metadata,

                )

            except Exception as exc:

                self.export_failed.emit(f"{task.source_name}: {exc}")

                return

            source_results.append(

                SourceReportExportResult(

                    source_key=task.source_key,

                    source_name=task.source_name,

                    output_dir=task.output_dir,

                    export_result=export_result,

                )

            )



        self.export_completed.emit(ReportExportBatchResult(export_root=self._export_root, source_results=source_results))



class LightCurveGifExportWorker(QThread):

    export_completed = Signal(object)

    export_failed = Signal(str)

    export_cancelled = Signal(str)

    progress_updated = Signal(int, int, str)



    def __init__(

        self,

        series: LightCurveSeries,

        output_path: Path,

        *,

        fit_config: object | None = None,

        y_axis_mode: str = "differential_magnitude",

        x_axis_mode: str = "datetime",

        phase_period_hours: float | None = None,

        phase_anchor_mode: str = "first_observation",

        plot_theme: str = "normal",

        custom_theme_colors: dict[str, str] | None = None,

        x_limits: tuple[float, float] | None = None,

        y_limits: tuple[float, float] | None = None,

        figure_size_inches: tuple[float, float] = (11.5, 6.6),

        parent: object | None = None,

    ) -> None:

        super().__init__(parent)

        self._series = series

        self._output_path = output_path

        self._fit_config = fit_config

        self._y_axis_mode = y_axis_mode

        self._x_axis_mode = x_axis_mode

        self._phase_period_hours = phase_period_hours

        self._phase_anchor_mode = phase_anchor_mode

        self._plot_theme = plot_theme

        self._custom_theme_colors = None if custom_theme_colors is None else dict(custom_theme_colors)

        self._x_limits = x_limits

        self._y_limits = y_limits

        self._figure_size_inches = figure_size_inches

        self._cancel_requested = Event()



    def request_cancel(self) -> None:

        self._cancel_requested.set()



    def cancellation_requested(self) -> bool:

        return self._cancel_requested.is_set()



    def run(self) -> None:

        try:

            export_light_curve_animated_gif(

                self._series,

                self._output_path,

                fit_config=self._fit_config,

                y_axis_mode=self._y_axis_mode,

                x_axis_mode=self._x_axis_mode,

                phase_period_hours=self._phase_period_hours,

                phase_anchor_mode=self._phase_anchor_mode,

                plot_theme=self._plot_theme,

                custom_theme_colors=self._custom_theme_colors,

                x_limits=self._x_limits,

                y_limits=self._y_limits,

                figure_size_inches=self._figure_size_inches,

                progress_callback=self.progress_updated.emit,

                is_cancelled=self._cancel_requested.is_set,

            )

        except AnimatedLightCurveExportCanceled as exc:

            self.export_cancelled.emit(str(exc))

            return

        except Exception as exc:

            self.export_failed.emit(str(exc))

            return

        self.export_completed.emit(self._output_path)



class HrPrepareWorker(QThread):

    preparation_completed = Signal(object)

    preparation_failed = Signal(str)

    progress_updated = Signal(str)



    def __init__(self, prepare_callable: Callable[[Callable[[str], None]], object], parent: object | None = None) -> None:

        super().__init__(parent)

        self._prepare_callable = prepare_callable



    def run(self) -> None:

        try:

            result = self._prepare_callable(self.progress_updated.emit)

        except Exception as exc:

            self.preparation_failed.emit(str(exc))

            return



        self.preparation_completed.emit(result)




class SkyExplorerWorker(QThread):

    exploration_completed = Signal(object)

    exploration_failed = Signal(str)

    progress_updated = Signal(str)



    def __init__(

        self,

        *,

        source_path: Path,

        settings: AppSettings,

        selected_layers: Sequence[str] | None = None,

        gaia_object_limit: int = 250,

        include_dense_galaxy_catalog: bool = False,

        ignore_gaia_hard_cap: bool = False,

        parent: object | None = None,

    ) -> None:

        super().__init__(parent)

        self._source_path = source_path

        self._settings = settings

        self._selected_layers = tuple(selected_layers or ())

        self._gaia_object_limit = max(0, int(gaia_object_limit))

        self._include_dense_galaxy_catalog = bool(include_dense_galaxy_catalog)

        self._ignore_gaia_hard_cap = bool(ignore_gaia_hard_cap)



    def run(self) -> None:

        try:

            result = explore_sky_image(

                self._source_path,

                settings=self._settings,

                selected_layers=self._selected_layers,

                gaia_object_limit=self._gaia_object_limit,

                include_dense_galaxy_catalog=self._include_dense_galaxy_catalog,

                ignore_gaia_hard_cap=self._ignore_gaia_hard_cap,

                progress_callback=self.progress_updated.emit,

            )

        except Exception as exc:

            self.exploration_failed.emit(str(exc))

            return



        self.exploration_completed.emit(result)



class SkyExplorerSurveyWorker(QThread):

    survey_completed = Signal(object)

    survey_failed = Signal(str)

    progress_updated = Signal(str)

    def __init__(

        self,

        *,

        request: object,

        fetcher: Callable[..., object],

        parent: object | None = None,

    ) -> None:

        super().__init__(parent)

        self._request = request

        self._fetcher = fetcher

    def run(self) -> None:

        try:

            try:

                request = replace(

                    self._request,

                    progress_callback=self.progress_updated.emit,

                )

            except TypeError:

                request = self._request

            result = self._fetcher(

                request,

            )

        except Exception as exc:

            self.survey_failed.emit(str(exc))

            return

        self.survey_completed.emit(result)



class DistanceMapWorker(QThread):

    map_completed = Signal(object)

    map_failed = Signal(str)

    progress_updated = Signal(str)



    def __init__(

        self,

        *,

        source_path: Path,

        settings: AppSettings,

        max_magnitude: float,

        max_distance_pc: float,

        max_star_count: int,

        parent: object | None = None,

    ) -> None:

        super().__init__(parent)

        self._source_path = source_path

        self._settings = settings

        self._max_magnitude = float(max_magnitude)

        self._max_distance_pc = float(max_distance_pc)

        self._max_star_count = int(max_star_count)



    def run(self) -> None:

        try:

            result = build_distance_map(

                self._source_path,

                settings=self._settings,

                max_magnitude=self._max_magnitude,

                max_distance_pc=self._max_distance_pc,

                max_star_count=self._max_star_count,

                progress_callback=self.progress_updated.emit,

            )

        except Exception as exc:

            self.map_failed.emit(str(exc))

            return



        self.map_completed.emit(result)




class SkyAtlasCustomOverlayImportWorker(QThread):

    import_completed = Signal(object)

    import_failed = Signal(str)

    progress_updated = Signal(str)



    def __init__(

        self,

        *,

        source_path: Path,

        settings: AppSettings,

        survey_id: str,

        parent: object | None = None,

    ) -> None:

        super().__init__(parent)

        self._source_path = source_path

        self._settings = settings

        self._survey_id = survey_id



    def run(self) -> None:

        try:

            record = import_sky_overlay(

                self._source_path,

                self._settings,

                survey_id=self._survey_id,

                progress_callback=self.progress_updated.emit,

            )

        except Exception as exc:

            self.import_failed.emit(str(exc).strip() or exc.__class__.__name__)

            return



        self.import_completed.emit(record)




class SolarSystemDetectionWorker(QThread):

    detection_completed = Signal(object)

    detection_failed = Signal(str)

    progress_updated = Signal(str)



    def __init__(self, *, parent: object | None = None, **kwargs: object) -> None:

        super().__init__(parent)

        self._kwargs = dict(kwargs)



    def run(self) -> None:

        try:

            result = detect_known_solar_system_objects(

                progress_callback=self.progress_updated.emit,

                **self._kwargs,

            )

        except Exception as exc:

            self.detection_failed.emit(str(exc))

            return



        self.detection_completed.emit(result)





class TransientSearchWorker(QThread):

    search_completed = Signal(object)

    search_failed = Signal(str)

    search_cancelled = Signal(str)

    progress_updated = Signal(str)



    def __init__(

        self,

        *,

        root_path: Path,

        settings: AppSettings,

        min_frame_count: int = 2,

        detection_sigma: float = 5.0,

        edge_margin_fraction: float = 0.0,

        parent: object | None = None,

    ) -> None:

        super().__init__(parent)

        self._root_path = root_path

        self._settings = settings

        self._min_frame_count = max(1, int(min_frame_count))

        self._detection_sigma = max(0.5, float(detection_sigma))

        self._edge_margin_fraction = max(0.0, min(0.45, float(edge_margin_fraction)))

        self._cancel_requested = Event()



    def request_cancel(self) -> None:

        self._cancel_requested.set()



    def cancellation_requested(self) -> bool:

        return self._cancel_requested.is_set()



    def run(self) -> None:

        try:

            result = search_transients_in_folder(

                self._root_path,

                self._settings,

                min_frame_count=self._min_frame_count,

                detection_sigma=self._detection_sigma,

                edge_margin_fraction=self._edge_margin_fraction,

                progress_callback=self.progress_updated.emit,

                cancel_callback=self.cancellation_requested,

            )

        except RuntimeError as exc:

            if "cancelled" in str(exc).lower():

                self.search_cancelled.emit(str(exc))

                return

            self.search_failed.emit(str(exc))

            return

        except Exception as exc:

            self.search_failed.emit(str(exc))

            return

        self.search_completed.emit(result)



class CalibrationWorker(QThread):

    calibration_completed = Signal(object)

    calibration_failed = Signal(str)

    progress_updated = Signal(str)



    def __init__(self, request: CalibrationPipelineRequest, parent: object | None = None) -> None:

        super().__init__(parent)

        self._request = request



    def run(self) -> None:

        try:

            result = calibrate_image_sequence(self._request, progress_callback=self.progress_updated.emit)

        except Exception as exc:

            self.calibration_failed.emit(str(exc))

            return

        self.calibration_completed.emit(result)



class AsteroidVisibleMagnitudeWorker(QThread):

    estimate_completed = Signal(object)

    estimate_failed = Signal(str)

    progress_updated = Signal(str)



    def __init__(self, *, parent: object | None = None, **kwargs: object) -> None:

        super().__init__(parent)

        self._kwargs = dict(kwargs)



    def run(self) -> None:

        try:

            result = estimate_visible_magnitude_limit(

                progress_callback=self.progress_updated.emit,

                **self._kwargs,

            )

        except Exception as exc:

            self.estimate_failed.emit(str(exc))

            return



        self.estimate_completed.emit(result)





class AsteroidOrbitContextWorker(QThread):

    context_completed = Signal(object)

    context_failed = Signal(str)

    progress_updated = Signal(str)



    def __init__(

        self,

        *,

        detection: SolarSystemDetection | None = None,

        frame_measurements: tuple[SolarSystemFrameMeasurement, ...] | None = None,

        targets: tuple[AsteroidOrbitContextTarget, ...] | None = None,

        available_targets: tuple[AsteroidOrbitContextTarget, ...] | None = None,

        arc_padding_days: float = 45.0,

        sample_count: int = 61,

        include_major_planets: bool = False,

        window_start: datetime | None = None,

        window_end: datetime | None = None,

        observation_times: tuple[datetime, ...] | None = None,

        parent: object | None = None,

    ) -> None:

        super().__init__(parent)

        if targets is not None:

            self._targets = tuple(targets)

        elif detection is not None and frame_measurements is not None:

            self._targets = (AsteroidOrbitContextTarget(detection=detection, frame_measurements=tuple(frame_measurements)),)

        else:

            self._targets = ()

        self._available_targets = tuple(available_targets) if available_targets is not None else self._targets

        self._arc_padding_days = float(arc_padding_days)

        self._sample_count = max(2, int(sample_count))

        self._include_major_planets = bool(include_major_planets)

        self._window_start = window_start

        self._window_end = window_end

        self._observation_times = tuple(observation_times) if observation_times is not None else None



    def run(self) -> None:

        try:

            observation_times = self._resolve_observation_times()

            if not self._targets:

                context = build_multi_known_object_heliocentric_context(

                    (),

                    observation_times=observation_times,

                    arc_padding_days=self._arc_padding_days,

                    sample_count=self._sample_count,

                    include_major_planets=self._include_major_planets,

                    window_start=self._window_start,

                    window_end=self._window_end,

                    progress_callback=self.progress_updated.emit,

                )

                self.context_completed.emit(

                    AsteroidOrbitContextResult(

                        detection=None,

                        frame_measurements=(),

                        context=context,

                        targets=(),

                        available_targets=self._available_targets,

                    )

                )

                return

            primary_target = self._targets[0]

            if len(self._targets) == 1:

                context = build_known_object_heliocentric_context(

                    primary_target.detection,

                    observation_times=observation_times,

                    arc_padding_days=self._arc_padding_days,

                    sample_count=self._sample_count,

                    include_major_planets=self._include_major_planets,

                    window_start=self._window_start,

                    window_end=self._window_end,

                    progress_callback=self.progress_updated.emit,

                )

            else:

                context = build_multi_known_object_heliocentric_context(

                    tuple(target.detection for target in self._targets),

                    observation_times=observation_times,

                    arc_padding_days=self._arc_padding_days,

                    sample_count=self._sample_count,

                    include_major_planets=self._include_major_planets,

                    window_start=self._window_start,

                    window_end=self._window_end,

                    progress_callback=self.progress_updated.emit,

                )

        except Exception as exc:

            self.context_failed.emit(str(exc))

            return



        self.context_completed.emit(

            AsteroidOrbitContextResult(

                detection=primary_target.detection,

                frame_measurements=primary_target.frame_measurements,

                context=context,

                targets=self._targets,

                available_targets=self._available_targets,

            )

        )



    def _resolve_observation_times(self) -> tuple[datetime, ...]:

        if self._observation_times:

            return tuple(self._observation_times)

        for target in self._targets:

            if target.frame_measurements:

                return tuple(measurement.observation_time for measurement in target.frame_measurements)

        if self._window_start is not None and self._window_end is not None:

            return (self._window_start, self._window_end)

        raise ValueError("No observation times are available for the Trajectory View window.")





class AsteroidSequenceAlignmentWorker(QThread):

    alignment_completed = Signal(object)

    alignment_failed = Signal(str)

    progress_updated = Signal(str)



    def __init__(self, *, parent: object | None = None, **kwargs: object) -> None:

        super().__init__(parent)

        self._kwargs = dict(kwargs)



    def run(self) -> None:

        try:

            result = align_wcs_image_sequence(

                progress_callback=self.progress_updated.emit,

                **self._kwargs,

            )

        except Exception as exc:

            self.alignment_failed.emit(str(exc))

            return



        self.alignment_completed.emit(result)





class AsteroidSyntheticTrackingWorker(QThread):

    tracking_completed = Signal(object)

    tracking_failed = Signal(str)

    progress_updated = Signal(int, int, str)



    def __init__(

        self,

        *,

        frame_paths: list[Path],

        frame_metadata: dict[str, ObservationMetadata],

        detection_result: SolarSystemDetectionResult,

        detection_index: int,

        reference_path: Path,

        frame_targets: dict[str, SyntheticTrackingFrameTarget] | None = None,

        crop_radius: int = 24,

        integration_mode: str = "average",
        weight_mode: str = "psf_signal_weight",
        rejection_mode: str = "no_rejection",

        full_frame_mode: bool = False,

        manual_motion_px_per_hour: float | None = None,

        manual_motion_angle_deg: float | None = None,

        motion_arcsec_per_hour_override: float | None = None,

        backend_preference: str = "auto",

        max_parallel_workers: int = 0,

        parent: object | None = None,

    ) -> None:

        super().__init__(parent)

        self._frame_paths = list(frame_paths)

        self._frame_metadata = dict(frame_metadata)

        self._detection_result = detection_result

        self._detection_index = int(detection_index)

        self._reference_path = reference_path

        self._frame_targets = {} if frame_targets is None else dict(frame_targets)

        self._crop_radius = int(crop_radius)

        self._integration_mode = str(integration_mode).strip() or "average"
        self._weight_mode = str(weight_mode).strip() or "psf_signal_weight"
        self._rejection_mode = str(rejection_mode).strip() or "no_rejection"

        self._full_frame_mode = bool(full_frame_mode)

        self._manual_motion_px_per_hour = None if manual_motion_px_per_hour is None else float(manual_motion_px_per_hour)

        self._manual_motion_angle_deg = None if manual_motion_angle_deg is None else float(manual_motion_angle_deg)

        self._motion_arcsec_per_hour_override = None if motion_arcsec_per_hour_override is None else float(motion_arcsec_per_hour_override)

        self._backend_preference = str(backend_preference).strip().lower() or "auto"

        self._max_parallel_workers = max(0, int(max_parallel_workers))



    def run(self) -> None:

        if self._detection_index < 0 or self._detection_index >= len(self._detection_result.detections):

            self.tracking_failed.emit("Select a valid asteroid/comet row before running Synthetic Track.")

            return

        frame_observation_times: dict[str, datetime] = {}

        frame_exposure_seconds: dict[str, float | None] = {}

        for frame_path in self._frame_paths:

            resolved_key = str(frame_path.resolve())

            metadata = self._frame_metadata.get(resolved_key)

            midpoint_time, exposure_seconds = _synthetic_tracking_frame_midpoint_time(metadata, self._detection_result)

            frame_target = self._frame_targets.get(resolved_key)

            if frame_target is not None:

                midpoint_time = frame_target.observation_time

            if midpoint_time is not None:

                frame_observation_times[resolved_key] = midpoint_time

            frame_exposure_seconds[resolved_key] = exposure_seconds

        try:

            if self._full_frame_mode:
                if self._manual_motion_px_per_hour is None or self._manual_motion_angle_deg is None:
                    raise ValueError("Advanced Synthetic Track requires a motion rate and angle.")
                result = build_synthetic_tracked_full_frame_stack(
                    self._frame_paths,
                    reference_path=self._reference_path,
                    frame_observation_times=frame_observation_times,
                    motion_px_per_hour=self._manual_motion_px_per_hour,
                    motion_angle_deg=self._manual_motion_angle_deg,
                    integration_mode=self._integration_mode,
                    weight_mode=self._weight_mode,
                    rejection_mode=self._rejection_mode,
                    motion_arcsec_per_hour=self._motion_arcsec_per_hour_override,
                    array_backend_preference=self._backend_preference,
                    max_parallel_workers=self._max_parallel_workers,
                    progress_callback=self.progress_updated.emit,
                )
            else:
                result = build_synthetic_tracked_stack(

                    self._frame_paths,

                    self._detection_result.detections[self._detection_index],

                    reference_path=self._reference_path,

                    frame_observation_times=frame_observation_times,

                    frame_exposure_seconds=frame_exposure_seconds,

                    frame_targets=self._frame_targets,

                    reference_observation_time=self._detection_result.prediction_time,

                    crop_radius=self._crop_radius,

                    integration_mode=self._integration_mode,

                    weight_mode=self._weight_mode,

                    rejection_mode=self._rejection_mode,

                    motion_arcsec_per_hour=(
                        self._motion_arcsec_per_hour_override
                        if self._motion_arcsec_per_hour_override is not None
                        else self._detection_result.detections[self._detection_index].motion_rate_arcsec_per_hour
                    ),

                    max_parallel_workers=self._max_parallel_workers,

                    progress_callback=self.progress_updated.emit,

                )

        except Exception as exc:

            self.tracking_failed.emit(str(exc))

            return

        self.tracking_completed.emit(result)





class AsteroidDiscoveryWorker(QThread):

    discovery_completed = Signal(object)

    discovery_intermediate_result = Signal(object)

    sweep_confirmation_requested = Signal(object)

    discovery_failed = Signal(str)

    discovery_cancelled = Signal(str)

    progress_updated = Signal(str)



    def __init__(

        self,

        *,

        frame_paths: list[Path],

        frame_metadata: dict[str, ObservationMetadata],

        known_detection_result: SolarSystemDetectionResult | None,

        reference_path: Path,

        settings: AppSettings,

        assume_aligned: bool,

        max_parallel_workers: int = 0,

        residual_min_snr: float = 0.0,

        residual_max_snr: float = 0.0,

        frames_per_batch: int = 0,

        single_batch_only: bool = False,

        binning_factor: int = 1,

        use_temporary_cache: bool = False,

        minimum_candidate_frames: int = 3,

        detection_sigma: float = 5.0,

        detection_fwhm: float = 3.0,

        max_residuals_per_frame: int = 24,

        edge_margin_px: int = 6,

        min_seed_displacement_px: float = 1.5,

        detector_mode: str = "hybrid",

        streak_min_area_px: int = 6,

        streak_min_elongation: float = 1.8,

        potential_candidate_max_deflection_rms_px: float = 0.9,

        review_candidate_max_deflection_rms_px: float = 1.8,

        enable_synthetic_sweep: bool = False,

        synthetic_sweep_max_motion_px_per_hour: float = 12.0,

        synthetic_sweep_motion_step_px_per_hour: float = 1.0,

        synthetic_sweep_angle_step_deg: float = 30.0,

        synthetic_sweep_direction_focus: str = "all_directions",

        synthetic_sweep_direction_focus_half_width_deg: float = 45.0,

        synthetic_sweep_min_stacked_snr: float = 6.0,

        synthetic_sweep_save_stacks: bool = False,

        motion_prior_bias: str = "balanced",

        auto_continue_synthetic_sweep: bool = False,

        retry_with_detailed_search: bool = False,

        parent: object | None = None,

    ) -> None:

        super().__init__(parent)

        self._frame_paths = list(frame_paths)

        self._frame_metadata = dict(frame_metadata)

        self._known_detection_result = known_detection_result

        self._reference_path = reference_path

        self._settings = settings

        self._assume_aligned = bool(assume_aligned)

        self._max_parallel_workers = max(0, int(max_parallel_workers))

        self._residual_min_snr = max(0.0, float(residual_min_snr))

        self._residual_max_snr = max(0.0, float(residual_max_snr))

        self._frames_per_batch = max(0, int(frames_per_batch))

        self._single_batch_only = bool(single_batch_only)

        self._binning_factor = 1 if int(binning_factor) not in {1, 2, 3, 4} else int(binning_factor)

        self._use_temporary_cache = bool(use_temporary_cache)

        self._minimum_candidate_frames = max(2, int(minimum_candidate_frames))

        self._detection_sigma = max(0.5, float(detection_sigma))

        self._detection_fwhm = max(0.8, float(detection_fwhm))

        self._max_residuals_per_frame = max(1, int(max_residuals_per_frame))

        self._edge_margin_px = max(0, int(edge_margin_px))

        self._min_seed_displacement_px = max(0.0, float(min_seed_displacement_px))

        self._detector_mode = str(detector_mode or "hybrid").strip().lower()

        self._streak_min_area_px = max(2, int(streak_min_area_px))

        self._streak_min_elongation = max(1.0, float(streak_min_elongation))

        self._potential_candidate_max_deflection_rms_px = max(0.1, float(potential_candidate_max_deflection_rms_px))

        self._review_candidate_max_deflection_rms_px = max(self._potential_candidate_max_deflection_rms_px, float(review_candidate_max_deflection_rms_px))

        self._enable_synthetic_sweep = bool(enable_synthetic_sweep)

        self._synthetic_sweep_max_motion_px_per_hour = max(0.1, float(synthetic_sweep_max_motion_px_per_hour))

        self._synthetic_sweep_motion_step_px_per_hour = max(0.1, float(synthetic_sweep_motion_step_px_per_hour))

        self._synthetic_sweep_angle_step_deg = max(1.0, float(synthetic_sweep_angle_step_deg))

        self._synthetic_sweep_direction_focus = str(synthetic_sweep_direction_focus or "all_directions").strip().lower() or "all_directions"

        self._synthetic_sweep_direction_focus_half_width_deg = max(1.0, float(synthetic_sweep_direction_focus_half_width_deg))

        self._synthetic_sweep_min_stacked_snr = max(0.5, float(synthetic_sweep_min_stacked_snr))

        self._synthetic_sweep_save_stacks = bool(synthetic_sweep_save_stacks)

        self._motion_prior_bias = str(motion_prior_bias or "balanced").strip().lower() or "balanced"

        self._auto_continue_synthetic_sweep = bool(auto_continue_synthetic_sweep)

        self._retry_with_detailed_search = bool(retry_with_detailed_search)

        self._cancel_requested = Event()

        self._sweep_confirmation_ready = Event()

        self._sweep_confirmation_response = False



    def request_cancel(self) -> None:

        self._cancel_requested.set()



    def cancellation_requested(self) -> bool:

        return self._cancel_requested.is_set()

    def provide_sweep_confirmation(self, should_continue: bool) -> None:

        self._sweep_confirmation_response = bool(should_continue)

        self._sweep_confirmation_ready.set()

    def _confirm_sweep_phase(self, residual_result: MovingObjectDiscoveryResult) -> bool:

        self._sweep_confirmation_response = False

        self._sweep_confirmation_ready.clear()

        self.sweep_confirmation_requested.emit(residual_result)

        while not self._sweep_confirmation_ready.wait(0.1):

            if self.cancellation_requested():

                raise DiscoveryCancelledError("Discover cancelled.")

        return self._sweep_confirmation_response

    def _run_discovery_pass(

        self,

        *,

        estimate_result: object | None,

        enable_synthetic_sweep: bool,

        residual_min_snr: float,

        residual_max_snr: float,

        frames_per_batch: int,

        binning_factor: int,

    ) -> MovingObjectDiscoveryResult:

        return discover_unmatched_moving_candidates(

            self._frame_paths,

            frame_metadata=self._frame_metadata,

            known_detection_result=self._known_detection_result,

            reference_path=self._reference_path,

            assume_aligned=self._assume_aligned,

            max_parallel_workers=self._max_parallel_workers,

            residual_min_snr=residual_min_snr,

            residual_max_snr=residual_max_snr,

            frames_per_batch=frames_per_batch,

            single_batch_only=self._single_batch_only,

            binning_factor=binning_factor,

            use_temporary_cache=self._use_temporary_cache,

            minimum_candidate_frames=self._minimum_candidate_frames,

            detection_sigma=self._detection_sigma,

            detection_fwhm=self._detection_fwhm,

            max_residuals_per_frame=self._max_residuals_per_frame,

            edge_margin_px=self._edge_margin_px,

            min_seed_displacement_px=self._min_seed_displacement_px,

            detector_mode=self._detector_mode,

            streak_min_area_px=self._streak_min_area_px,

            streak_min_elongation=self._streak_min_elongation,

            potential_candidate_max_deflection_rms_px=self._potential_candidate_max_deflection_rms_px,

            review_candidate_max_deflection_rms_px=self._review_candidate_max_deflection_rms_px,

            enable_synthetic_sweep=enable_synthetic_sweep,

            synthetic_sweep_max_motion_px_per_hour=self._synthetic_sweep_max_motion_px_per_hour,

            synthetic_sweep_motion_step_px_per_hour=self._synthetic_sweep_motion_step_px_per_hour,

            synthetic_sweep_angle_step_deg=self._synthetic_sweep_angle_step_deg,

            synthetic_sweep_direction_focus=self._synthetic_sweep_direction_focus,

            synthetic_sweep_direction_focus_half_width_deg=self._synthetic_sweep_direction_focus_half_width_deg,

            synthetic_sweep_min_stacked_snr=self._synthetic_sweep_min_stacked_snr,

            synthetic_sweep_save_stacks=self._synthetic_sweep_save_stacks,

            motion_prior_bias=self._motion_prior_bias,

            synthetic_tracking_combine_mode=self._settings.synthetic_tracking_combine_mode,

            estimate_result=estimate_result,

            progress_callback=self.progress_updated.emit,

            cancel_callback=self.cancellation_requested,

            intermediate_result_callback=self.discovery_intermediate_result.emit,

        )

    def _emit_estimate_stage_result(self, estimate_result: SolarSystemVisibilityEstimateResult | None) -> None:

        known_detections = [] if self._known_detection_result is None else list(self._known_detection_result.detections)
        estimated_magnitude_limit = None if estimate_result is None else float(estimate_result.dimmest_visible_magnitude)
        benchmark_known_count = sum(
            _detection_within_estimated_limit(detection, estimated_magnitude_limit)
            for detection in known_detections
        )
        if estimate_result is None:
            summary_text = (
                f"Discover is starting residual search on {len(self._frame_paths)} frame(s). "
                f"Known-object benchmark counts remain unavailable because the visible-limit estimate did not complete."
            )
        else:
            summary_text = (
                f"Estimated visible limit Gaia G {estimate_result.dimmest_visible_magnitude:.1f}; "
                f"{benchmark_known_count} known object(s) are within that estimate. Discover is starting residual search."
            )
        self.discovery_intermediate_result.emit(
            MovingObjectDiscoveryResult(
                reference_path=self._reference_path,
                preview_data=np.zeros((1, 1), dtype=np.float32),
                candidate_count=0,
                aligned_frame_count=len(self._frame_paths),
                candidates=(),
                summary_text=summary_text,
                report_text=summary_text,
                estimate_result=estimate_result,
                benchmark_known_count=benchmark_known_count,
            )
        )

    def run(self) -> None:
        try:
            estimate_result: SolarSystemVisibilityEstimateResult | None = None
            if self._known_detection_result is not None and self._known_detection_result.detections:
                try:
                    self.progress_updated.emit("Running visible-limit estimate for Discover benchmarking.")
                    estimate_result = estimate_visible_magnitude_limit(
                        self._reference_path,
                        settings=self._settings,
                        progress_callback=self.progress_updated.emit,
                    )
                except Exception as exc:
                    self.progress_updated.emit(f"Discover estimate unavailable: {exc}. Continuing without magnitude-limit benchmarking.")

            self._emit_estimate_stage_result(estimate_result)

            if self.cancellation_requested():
                raise DiscoveryCancelledError("Discover cancelled.")

            result = self._run_discovery_pass(
                estimate_result=estimate_result,
                enable_synthetic_sweep=False,
                residual_min_snr=self._residual_min_snr,
                residual_max_snr=self._residual_max_snr,
                frames_per_batch=self._frames_per_batch,
                binning_factor=self._binning_factor,
            )

            if self._enable_synthetic_sweep:
                if self.cancellation_requested():
                    raise DiscoveryCancelledError("Discover cancelled.")
                self.progress_updated.emit(
                    "Residual batches are complete. Review the current Discover results, then choose whether to continue into the final synthetic sweep."
                )
                should_continue_sweep = self._auto_continue_synthetic_sweep or self._confirm_sweep_phase(result)
                if should_continue_sweep:
                    self.progress_updated.emit("Continuing into the final synthetic sweep across the selected discovery batches.")
                    result = self._run_discovery_pass(
                        estimate_result=estimate_result,
                        enable_synthetic_sweep=True,
                        residual_min_snr=self._residual_min_snr,
                        residual_max_snr=self._residual_max_snr,
                        frames_per_batch=self._frames_per_batch,
                        binning_factor=self._binning_factor,
                    )
                else:
                    self.progress_updated.emit("Discover kept the residual-only results and skipped the final synthetic sweep.")

            if self._retry_with_detailed_search and not result.candidates and result.recovered_known_count == 0:
                if self.cancellation_requested():
                    raise DiscoveryCancelledError("Discover cancelled.")
                self.progress_updated.emit("Fast scan found no candidates; retrying Discover with the detailed full-resolution search.")
                result = self._run_discovery_pass(
                    estimate_result=estimate_result,
                    enable_synthetic_sweep=False,
                    residual_min_snr=0.0,
                    residual_max_snr=0.0,
                    frames_per_batch=self._frames_per_batch,
                    binning_factor=1,
                )
                if self._enable_synthetic_sweep:
                    if self.cancellation_requested():
                        raise DiscoveryCancelledError("Discover cancelled.")
                    self.progress_updated.emit(
                        "Detailed residual batches are complete. Review the current results, then choose whether to continue into the final synthetic sweep."
                    )
                    should_continue_sweep = self._auto_continue_synthetic_sweep or self._confirm_sweep_phase(result)
                    if should_continue_sweep:
                        self.progress_updated.emit("Continuing into the final synthetic sweep across the detailed search batches.")
                        result = self._run_discovery_pass(
                            estimate_result=estimate_result,
                            enable_synthetic_sweep=True,
                            residual_min_snr=0.0,
                            residual_max_snr=0.0,
                            frames_per_batch=self._frames_per_batch,
                            binning_factor=1,
                        )
                    else:
                        self.progress_updated.emit("Discover kept the detailed residual-only results and skipped the final synthetic sweep.")

        except DiscoveryCancelledError as exc:
            self.discovery_cancelled.emit(str(exc))
            return
        except Exception as exc:
            self.discovery_failed.emit(str(exc))
            return

        self.discovery_completed.emit(result)


class AsteroidDiscoveryResidualDebugWorker(QThread):

    export_completed = Signal(object)

    export_failed = Signal(str)

    export_cancelled = Signal(str)

    progress_updated = Signal(str)

    def __init__(
        self,
        *,
        frame_paths: list[Path],
        frame_metadata: dict[str, ObservationMetadata],
        known_detection_result: SolarSystemDetectionResult | None,
        reference_path: Path,
        output_directory: Path,
        assume_aligned: bool = False,
        max_parallel_workers: int = 0,
        residual_min_snr: float = 0.0,
        residual_max_snr: float = 0.0,
        frames_per_batch: int = 0,
        single_batch_only: bool = False,
        binning_factor: int = 1,
        use_temporary_cache: bool = False,
        minimum_candidate_frames: int = 3,
        detection_sigma: float = 5.0,
        detection_fwhm: float = 3.0,
        max_residuals_per_frame: int = 24,
        edge_margin_px: int = 6,
        min_seed_displacement_px: float = 1.5,
        detector_mode: str = "hybrid",
        streak_min_area_px: int = 6,
        streak_min_elongation: float = 1.8,
        motion_prior_bias: str = "balanced",
        parent: object | None = None,
    ) -> None:
        super().__init__(parent)
        self._frame_paths = list(frame_paths)
        self._frame_metadata = dict(frame_metadata)
        self._known_detection_result = known_detection_result
        self._reference_path = reference_path
        self._output_directory = output_directory
        self._assume_aligned = bool(assume_aligned)
        self._max_parallel_workers = max(0, int(max_parallel_workers))
        self._residual_min_snr = max(0.0, float(residual_min_snr))
        self._residual_max_snr = max(0.0, float(residual_max_snr))
        self._frames_per_batch = max(0, int(frames_per_batch))
        self._single_batch_only = bool(single_batch_only)
        self._binning_factor = 1 if int(binning_factor) not in {1, 2, 3, 4} else int(binning_factor)
        self._use_temporary_cache = bool(use_temporary_cache)
        self._minimum_candidate_frames = max(2, int(minimum_candidate_frames))
        self._detection_sigma = max(0.5, float(detection_sigma))
        self._detection_fwhm = max(0.8, float(detection_fwhm))
        self._max_residuals_per_frame = max(1, int(max_residuals_per_frame))
        self._edge_margin_px = max(0, int(edge_margin_px))
        self._min_seed_displacement_px = max(0.0, float(min_seed_displacement_px))
        self._detector_mode = str(detector_mode or "hybrid").strip().lower()
        self._streak_min_area_px = max(2, int(streak_min_area_px))
        self._streak_min_elongation = max(1.0, float(streak_min_elongation))
        self._motion_prior_bias = str(motion_prior_bias or "balanced").strip().lower() or "balanced"
        self._cancel_requested = Event()

    def request_cancel(self) -> None:
        self._cancel_requested.set()

    def cancellation_requested(self) -> bool:
        return self._cancel_requested.is_set()

    def run(self) -> None:
        try:
            output_directory = export_discovery_residual_debug_outputs(
                self._frame_paths,
                frame_metadata=self._frame_metadata,
                known_detection_result=self._known_detection_result,
                output_directory=self._output_directory,
                reference_path=self._reference_path,
                assume_aligned=self._assume_aligned,
                max_parallel_workers=self._max_parallel_workers,
                residual_min_snr=self._residual_min_snr,
                residual_max_snr=self._residual_max_snr,
                frames_per_batch=self._frames_per_batch,
                single_batch_only=self._single_batch_only,
                binning_factor=self._binning_factor,
                use_temporary_cache=self._use_temporary_cache,
                minimum_candidate_frames=self._minimum_candidate_frames,
                detection_sigma=self._detection_sigma,
                detection_fwhm=self._detection_fwhm,
                max_residuals_per_frame=self._max_residuals_per_frame,
                edge_margin_px=self._edge_margin_px,
                min_seed_displacement_px=self._min_seed_displacement_px,
                detector_mode=self._detector_mode,
                streak_min_area_px=self._streak_min_area_px,
                streak_min_elongation=self._streak_min_elongation,
                motion_prior_bias=self._motion_prior_bias,
                progress_callback=self.progress_updated.emit,
                cancel_callback=self.cancellation_requested,
            )
        except DiscoveryCancelledError as exc:
            self.export_cancelled.emit(str(exc))
            return
        except Exception as exc:
            self.export_failed.emit(str(exc))
            return
        self.export_completed.emit(output_directory)


class AsteroidRecoveryWorker(QThread):

    recovery_completed = Signal(object)

    recovery_failed = Signal(str)

    recovery_cancelled = Signal(str)

    progress_updated = Signal(str)



    def __init__(

        self,

        *,

        frame_paths: list[Path],

        frame_metadata: dict[str, ObservationMetadata],

        known_detection_result: SolarSystemDetectionResult | None,

        reference_path: Path,

        assume_aligned: bool,

        max_parallel_workers: int = 0,

        parent: object | None = None,

    ) -> None:

        super().__init__(parent)

        self._frame_paths = list(frame_paths)

        self._frame_metadata = dict(frame_metadata)

        self._known_detection_result = known_detection_result

        self._reference_path = reference_path

        self._assume_aligned = bool(assume_aligned)

        self._max_parallel_workers = max(0, int(max_parallel_workers))

        self._cancel_requested = Event()



    def request_cancel(self) -> None:

        self._cancel_requested.set()



    def cancellation_requested(self) -> bool:

        return self._cancel_requested.is_set()



    def run(self) -> None:

        try:

            result = recover_known_moving_objects(

                self._frame_paths,

                frame_metadata=self._frame_metadata,

                known_detection_result=self._known_detection_result,

                reference_path=self._reference_path,

                assume_aligned=self._assume_aligned,

                max_parallel_workers=self._max_parallel_workers,

                progress_callback=self.progress_updated.emit,

                cancel_callback=self.cancellation_requested,

            )

        except DiscoveryCancelledError as exc:

            self.recovery_cancelled.emit(str(exc))

            return

        except Exception as exc:

            self.recovery_failed.emit(str(exc))

            return

        self.recovery_completed.emit(result)





def _synthetic_tracking_frame_midpoint_time(

    metadata: ObservationMetadata | None,

    result: SolarSystemDetectionResult,

) -> tuple[datetime | None, float | None]:

    if metadata is None or metadata.date_obs is None:

        return result.prediction_time, result.exposure_seconds

    timestamp = metadata.date_obs if metadata.date_obs.tzinfo is not None else metadata.date_obs.replace(tzinfo=UTC)

    exposure_seconds = result.exposure_seconds if metadata.exposure_seconds is None else metadata.exposure_seconds

    if exposure_seconds is None:

        return timestamp, exposure_seconds

    return timestamp + timedelta(seconds=max(0.0, float(exposure_seconds)) / 2.0), exposure_seconds





@dataclass(slots=True)

class ImageDisplayPreloadResult:

    image_displays: dict[tuple[str, int, int], AnnotatedImageDisplay]

    loaded_frame_count: int

    warning_messages: list[str] | None = None




@dataclass(slots=True)

class AsteroidBlinkPreloadResult:

    image_displays: dict[tuple[str, int, int], AnnotatedImageDisplay]

    rendered_qimages: dict[tuple[str, int, int], QImage]

    frame_measurements: dict[tuple[str, int, int, int], list[SolarSystemFrameMeasurement | None]]

    sequence_signature: tuple[tuple[str, int, int], ...]

    loaded_frame_count: int

    measured_frame_count: int

    render_settings: AnnotatedImageRenderSettings | None = None

    measurement_generation: int | None = None

    warning_messages: list[str] | None = None





def _blink_frame_cache_key(image_path: Path) -> tuple[str, int, int]:

    stat = image_path.stat()

    return (str(image_path.resolve()), int(stat.st_mtime_ns), int(stat.st_size))




def _preload_image_display(

    image_path: Path,

) -> tuple[tuple[str, int, int], AnnotatedImageDisplay | None, str | None]:

    cache_key = _blink_frame_cache_key(image_path)

    try:

        return cache_key, build_annotated_image_display(image_path), None

    except Exception as exc:

        return cache_key, None, f"Could not preload {image_path.name}: {exc}"




def preload_image_displays(

    *,

    frame_paths: list[Path],

    max_parallel_workers: int = 0,

    progress_callback: Callable[[int, int, str], None] | None = None,

) -> ImageDisplayPreloadResult:

    if not frame_paths:

        return ImageDisplayPreloadResult(image_displays={}, loaded_frame_count=0, warning_messages=[])



    frame_count = len(frame_paths)

    worker_count = _resolve_blink_preload_worker_count(frame_count, max_parallel_workers)

    image_displays: dict[tuple[str, int, int], AnnotatedImageDisplay] = {}

    warning_messages: list[str] = []

    completed = 0

    with ThreadPoolExecutor(max_workers=worker_count) as executor:

        futures = {

            executor.submit(_preload_image_display, image_path): image_path

            for image_path in frame_paths

        }

        while futures:

            done, _pending = wait(tuple(futures.keys()), return_when=FIRST_COMPLETED)

            for future in done:

                image_path = futures.pop(future)

                cache_key, display, warning_message = future.result()

                if display is not None:

                    image_displays[cache_key] = display

                if warning_message:

                    warning_messages.append(warning_message)

                completed += 1

                if progress_callback is not None:

                    progress_callback(completed, frame_count, f"Prepared {completed}/{frame_count} frame preview(s): {image_path.name}")

    return ImageDisplayPreloadResult(

        image_displays=image_displays,

        loaded_frame_count=len(image_displays),

        warning_messages=warning_messages,

    )





def _blink_frame_midpoint_time(metadata: ObservationMetadata | None, result: SolarSystemDetectionResult):

    if metadata is None or metadata.date_obs is None:

        if result.exposure_seconds is None:

            return result.prediction_time

        return result.observation_time + timedelta(seconds=max(0.0, float(result.exposure_seconds)) / 2.0)

    timestamp = metadata.date_obs if metadata.date_obs.tzinfo is not None else metadata.date_obs.replace(tzinfo=UTC)

    exposure_seconds = metadata.exposure_seconds

    if exposure_seconds is None:

        return timestamp

    return timestamp + timedelta(seconds=max(0.0, float(exposure_seconds)) / 2.0)





def _rendered_blink_qimage(image_path: Path, render_settings: AnnotatedImageRenderSettings) -> QImage:

    rendered = render_image_path_for_display(image_path, render_settings)

    if rendered.ndim == 2:

        height, width = rendered.shape

        return QImage(rendered.data, width, height, rendered.strides[0], QImage.Format.Format_Grayscale8).copy()

    if rendered.ndim == 3 and rendered.shape[2] == 3:

        height, width, _channels = rendered.shape

        return QImage(rendered.data, width, height, rendered.strides[0], QImage.Format.Format_RGB888).copy()

    raise ValueError("Rendered asteroid blink image must be grayscale or RGB.")



def _resolve_blink_preload_worker_count(frame_count: int, max_parallel_workers: int = 0) -> int:

    configured_workers = max(0, int(max_parallel_workers))

    if configured_workers > 0:

        return min(frame_count, configured_workers)

    return min(frame_count, max(1, min(8, (os.cpu_count() or 1))))



def _preload_asteroid_blink_frame(

    image_path: Path,

    *,

    frame_metadata: dict[str, ObservationMetadata],

    detection_result: SolarSystemDetectionResult | None,

    render_settings: AnnotatedImageRenderSettings | None,

    preload_displays: bool,

    preload_rendered_qimages: bool,

    preload_measurements: bool,

) -> tuple[tuple[str, int, int], AnnotatedImageDisplay | None, QImage | None, list[SolarSystemFrameMeasurement | None] | None, str | None]:

    cache_key = _blink_frame_cache_key(image_path)

    warning_message: str | None = None

    display: AnnotatedImageDisplay | None = None

    rendered_qimage: QImage | None = None

    if preload_rendered_qimages and render_settings is not None:

        try:

            rendered_qimage = _rendered_blink_qimage(image_path, render_settings)

        except Exception as exc:

            warning_message = f"Could not preload {image_path.name}: {exc}"

    if (preload_displays or preload_rendered_qimages) and warning_message is None:

        display = build_placeholder_annotated_image_display(image_path)

    frame_measurements: list[SolarSystemFrameMeasurement | None] | None = None

    if preload_measurements and detection_result is not None:

        metadata = frame_metadata.get(str(image_path.resolve()))

        midpoint_time = _blink_frame_midpoint_time(metadata, detection_result)

        exposure_seconds = detection_result.exposure_seconds if metadata is None else metadata.exposure_seconds

        try:

            frame_measurements = measure_detections_in_frame(

                image_path,
                detection_result.detections,
                reference_observation_time=detection_result.prediction_time,

                observation_time=midpoint_time,

                exposure_seconds=exposure_seconds,
                fallback_solved_field=detection_result.solved_field,

            )

        except Exception:

            frame_measurements = [None] * len(detection_result.detections)

    return cache_key, display, rendered_qimage, frame_measurements, warning_message



def preload_asteroid_blink_frames(

    *,

    frame_paths: list[Path],

    frame_metadata: dict[str, ObservationMetadata],

    detection_result: SolarSystemDetectionResult | None,

    detection_generation: int | None,

    render_settings: AnnotatedImageRenderSettings | None,

    preload_displays: bool,

    preload_rendered_qimages: bool,

    preload_measurements: bool,

    max_parallel_workers: int = 0,

    progress_callback: Callable[[int, int, str], None] | None = None,

) -> AsteroidBlinkPreloadResult:

    measurement_generation = detection_generation if preload_measurements else None

    effective_render_settings = render_settings if preload_rendered_qimages else None

    if not frame_paths:

        return AsteroidBlinkPreloadResult(

            image_displays={},

            rendered_qimages={},

            frame_measurements={},

            sequence_signature=(),

            loaded_frame_count=0,

            measured_frame_count=0,

            render_settings=effective_render_settings,

            measurement_generation=measurement_generation,

            warning_messages=[],

        )

    frame_count = len(frame_paths)

    worker_count = _resolve_blink_preload_worker_count(frame_count, max_parallel_workers)

    image_displays: dict[tuple[str, int, int], AnnotatedImageDisplay] = {}

    rendered_qimages: dict[tuple[str, int, int], QImage] = {}

    frame_measurements: dict[tuple[str, int, int, int], list[SolarSystemFrameMeasurement | None]] = {}

    warning_messages: list[str] = []

    completed = 0

    with ThreadPoolExecutor(max_workers=worker_count) as executor:

        futures = {

            executor.submit(

                _preload_asteroid_blink_frame,

                image_path,

                frame_metadata=frame_metadata,

                detection_result=detection_result,

                render_settings=effective_render_settings,

                preload_displays=preload_displays,

                preload_rendered_qimages=bool(preload_rendered_qimages and effective_render_settings is not None),

                preload_measurements=bool(preload_measurements and detection_result is not None),

            ): image_path

            for image_path in frame_paths

        }

        while futures:

            done, _pending = wait(tuple(futures.keys()), return_when=FIRST_COMPLETED)

            for future in done:

                image_path = futures.pop(future)

                cache_key, display, rendered_qimage, measurements, warning_message = future.result()

                if display is not None:

                    image_displays[cache_key] = display

                if rendered_qimage is not None:

                    rendered_qimages[cache_key] = rendered_qimage

                if measurements is not None and measurement_generation is not None:

                    frame_measurements[(cache_key[0], cache_key[1], cache_key[2], measurement_generation)] = measurements

                if warning_message:

                    warning_messages.append(warning_message)

                completed += 1

                if progress_callback is not None:

                    progress_callback(completed, frame_count, f"Prepared {completed}/{frame_count} blink frame(s): {image_path.name}")

    return AsteroidBlinkPreloadResult(

        image_displays=image_displays,

        rendered_qimages=rendered_qimages,

        frame_measurements=frame_measurements,

        sequence_signature=tuple(_blink_frame_cache_key(path) for path in frame_paths),

        loaded_frame_count=len(rendered_qimages) if rendered_qimages else len(image_displays),

        measured_frame_count=len(frame_measurements),

        render_settings=effective_render_settings,

        measurement_generation=measurement_generation,

        warning_messages=warning_messages,

    )



class AsteroidBlinkPreloadWorker(QThread):

    preload_completed = Signal(object)

    preload_failed = Signal(str)

    progress_updated = Signal(int, int, str)



    def __init__(

        self,

        *,

        frame_paths: list[Path],

        frame_metadata: dict[str, ObservationMetadata],

        detection_result: SolarSystemDetectionResult | None,

        detection_generation: int | None,

        render_settings: AnnotatedImageRenderSettings | None,

        preload_displays: bool,

        preload_rendered_qimages: bool,

        preload_measurements: bool,

        max_parallel_workers: int = 0,

        parent: object | None = None,

    ) -> None:

        super().__init__(parent)

        self._frame_paths = list(frame_paths)

        self._frame_metadata = dict(frame_metadata)

        self._detection_result = detection_result

        self._detection_generation = detection_generation if preload_measurements else None

        self._render_settings = render_settings

        self._preload_displays = bool(preload_displays)

        self._preload_rendered_qimages = bool(preload_rendered_qimages and render_settings is not None)

        self._preload_measurements = bool(preload_measurements and detection_result is not None)

        self._max_parallel_workers = max(0, int(max_parallel_workers))



    def run(self) -> None:

        try:

            result = preload_asteroid_blink_frames(

                frame_paths=self._frame_paths,

                frame_metadata=self._frame_metadata,

                detection_result=self._detection_result,

                detection_generation=self._detection_generation,

                render_settings=self._render_settings,

                preload_displays=self._preload_displays,

                preload_rendered_qimages=self._preload_rendered_qimages,

                preload_measurements=self._preload_measurements,

                max_parallel_workers=self._max_parallel_workers,

                progress_callback=self.progress_updated.emit,

            )

        except Exception as exc:

            self.preload_failed.emit(str(exc))

            return



        self.preload_completed.emit(result)




class ImageDisplayPreloadWorker(QThread):

    preload_completed = Signal(object)

    preload_failed = Signal(str)

    progress_updated = Signal(int, int, str)



    def __init__(

        self,

        *,

        frame_paths: list[Path],

        max_parallel_workers: int = 0,

        parent: object | None = None,

    ) -> None:

        super().__init__(parent)

        self._frame_paths = list(frame_paths)

        self._max_parallel_workers = max(0, int(max_parallel_workers))



    def run(self) -> None:

        try:

            result = preload_image_displays(

                frame_paths=self._frame_paths,

                max_parallel_workers=self._max_parallel_workers,

                progress_callback=self.progress_updated.emit,

            )

        except Exception as exc:

            self.preload_failed.emit(str(exc))

            return



        self.preload_completed.emit(result)




class HrStarDetailsWorker(QThread):

    details_completed = Signal(object)

    details_failed = Signal(object, str)



    def __init__(

        self,

        row_key: tuple[str, str],

        ra_deg: float,

        dec_deg: float,

        *,

        timeout_seconds: float = 10.0,

        parent: object | None = None,

    ) -> None:

        super().__init__(parent)

        self._row_key = (str(row_key[0]), str(row_key[1]))

        self._ra_deg = float(ra_deg)

        self._dec_deg = float(dec_deg)

        self._timeout_seconds = float(timeout_seconds)



    def run(self) -> None:

        try:

            details = fetch_catalog_target_details(

                self._ra_deg,

                self._dec_deg,

                timeout_seconds=self._timeout_seconds,

            )

        except Exception as exc:

            self.details_failed.emit(self._row_key, str(exc))

            return



        self.details_completed.emit(HrStarDetailsResult(row_key=self._row_key, details=details))





@dataclass(frozen=True)

class SkyExplorerDetectResult:

    image_key: str

    click_ra_deg: float

    click_dec_deg: float

    radius_arcsec: float

    targets: tuple[CatalogTargetAtCoordinate, ...]





class SkyExplorerDetectWorker(QThread):

    detect_completed = Signal(object)

    detect_failed = Signal(str, str)



    def __init__(

        self,

        image_key: str,

        ra_deg: float,

        dec_deg: float,

        *,

        radius_arcsec: float,

        timeout_seconds: float = 10.0,

        parent: object | None = None,

    ) -> None:

        super().__init__(parent)

        self._image_key = str(image_key)

        self._ra_deg = float(ra_deg)

        self._dec_deg = float(dec_deg)

        self._radius_arcsec = float(radius_arcsec)

        self._timeout_seconds = float(timeout_seconds)



    def run(self) -> None:

        try:

            targets = fetch_catalog_targets_at_coordinate(

                self._ra_deg,

                self._dec_deg,

                radius_arcsec=self._radius_arcsec,

                timeout_seconds=self._timeout_seconds,

            )

        except Exception as exc:

            self.detect_failed.emit(self._image_key, str(exc))

            return



        self.detect_completed.emit(

            SkyExplorerDetectResult(

                image_key=self._image_key,

                click_ra_deg=self._ra_deg,

                click_dec_deg=self._dec_deg,

                radius_arcsec=self._radius_arcsec,

                targets=targets,

            )

        )





class CalculatePeriodWorker(QThread):

    calculation_completed = Signal(object)

    calculation_failed = Signal(str)

    progress_updated = Signal(str)



    def __init__(

        self,

        report_token: int,

        series_items: list[tuple[tuple[str, str], object]],

        fit_config: LightCurveFitConfig | None,

        y_axis_mode: str,

        period_method: str,

        period_convention: str,

        max_parallel_workers: int = 0,

        include_bls_duration: bool = True,

        parent: object | None = None,

    ) -> None:

        super().__init__(parent)

        self._report_token = report_token

        self._series_items = list(series_items)

        self._fit_config = fit_config

        self._y_axis_mode = y_axis_mode

        self._period_method = period_method

        self._period_convention = period_convention

        self._max_parallel_workers = max(0, int(max_parallel_workers))

        self._include_bls_duration = bool(include_bls_duration)

        self._cancel_requested = Event()



    def request_cancel(self) -> None:

        self._cancel_requested.set()



    def cancellation_requested(self) -> bool:

        return self._cancel_requested.is_set()



    def run(self) -> None:

        try:

            results: dict[tuple[str, str], FitPeriodInferenceResult | None] = {}

            successful_series = 0

            total_series = len(self._series_items)

            cancelled = self._cancel_requested.is_set()

            if total_series <= 1:

                worker_count = 1

                execution_mode = "serial"

                self.progress_updated.emit("Calculating period data in background using 1 CPU worker (serial mode).")

                for index, (series_key, series) in enumerate(self._series_items, start=1):

                    if self._cancel_requested.is_set():

                        cancelled = True

                        self.progress_updated.emit("Calculated period cancellation requested; stopping before the next series.")

                        break

                    self.progress_updated.emit(f"[Calculated Period {index}/{total_series}] {series.source_name} [{series.filter_name}]")

                    merged_result = calculate_period_for_series(

                        series,

                        fit_config=self._fit_config,

                        y_axis_mode=self._y_axis_mode,

                        period_method=self._period_method,

                        period_convention=self._period_convention,

                        include_bls_duration=self._include_bls_duration,

                    )

                    if merged_result is None:

                        results[series_key] = None

                        self.progress_updated.emit(

                            f"[Calculated Period {index}/{total_series}] No calculated period result for {series.source_name} [{series.filter_name}]"

                        )

                        continue

                    results[series_key] = merged_result

                    successful_series += 1

                    self.progress_updated.emit(

                        f"[Calculated Period {index}/{total_series}] Period={merged_result.period_hours / 24.0:.4f} d for {series.source_name} [{series.filter_name}]"

                    )

            else:

                worker_count = _resolved_calculated_period_workers(total_series, self._max_parallel_workers)

                execution_mode = "process"

                self.progress_updated.emit(

                    f"Calculating period data across {total_series} series with {worker_count} CPU worker(s) in parallel process mode."

                )

                pending_items = deque(self._series_items)

                future_map: dict[object, tuple[tuple[str, str], object]] = {}

                completion_index = 0

                cancellation_message_emitted = False

                executor = ProcessPoolExecutor(max_workers=worker_count)

                try:

                    while pending_items and len(future_map) < worker_count and not self._cancel_requested.is_set():

                        series_key, series = pending_items.popleft()

                        future = executor.submit(

                            calculate_period_task,

                            series_key,

                            series,

                            self._fit_config,

                            self._y_axis_mode,

                            self._period_method,

                            self._period_convention,

                            self._include_bls_duration,

                        )

                        future_map[future] = (series_key, series)



                    while future_map:

                        done, _pending = wait(set(future_map), timeout=0.1, return_when=FIRST_COMPLETED)

                        if not done:

                            if self._cancel_requested.is_set() and not cancellation_message_emitted:

                                cancelled = True

                                cancellation_message_emitted = True

                                pending_items.clear()

                                self.progress_updated.emit(

                                    "Calculated period cancellation requested; waiting for active CPU tasks to finish."

                                )

                            continue

                        for future in done:

                            _series_key, _series = future_map.pop(future)

                            completion_index += 1

                            computed_key, source_name, filter_name, merged_result = future.result()

                            if merged_result is None:

                                results[computed_key] = None

                                self.progress_updated.emit(

                                    f"[Calculated Period {completion_index}/{total_series}] No calculated period result for {source_name} [{filter_name}]"

                                )

                            else:

                                results[computed_key] = merged_result

                                successful_series += 1

                                self.progress_updated.emit(

                                    f"[Calculated Period {completion_index}/{total_series}] Period={merged_result.period_hours / 24.0:.4f} d for {source_name} [{filter_name}]"

                                )

                        while pending_items and len(future_map) < worker_count and not self._cancel_requested.is_set():

                            series_key, series = pending_items.popleft()

                            future = executor.submit(

                                calculate_period_task,

                                series_key,

                                series,

                                self._fit_config,

                                self._y_axis_mode,

                                self._period_method,

                                self._period_convention,

                                self._include_bls_duration,

                            )

                            future_map[future] = (series_key, series)

                        if self._cancel_requested.is_set() and not cancellation_message_emitted:

                            cancelled = True

                            cancellation_message_emitted = True

                            pending_items.clear()

                            self.progress_updated.emit(

                                "Calculated period cancellation requested; waiting for active CPU tasks to finish."

                            )

                finally:

                    executor.shutdown(wait=True, cancel_futures=True)

        except Exception as exc:

            self.calculation_failed.emit(str(exc))

            return



        self.calculation_completed.emit(

            CalculatedPeriodBatchResult(

                report_token=self._report_token,

                results=results,

                success_count=successful_series,

                total_count=total_series,

                worker_count=worker_count,

                execution_mode=execution_mode,

                cancelled=cancelled,

            )

        )





class LiteraturePeriodWorker(QThread):

    literature_completed = Signal(object)

    literature_failed = Signal(str)

    progress_updated = Signal(str)



    def __init__(

        self,

        report_token: int,

        entries: list[object],

        max_parallel_workers: int = 0,

        parent: object | None = None,

    ) -> None:

        super().__init__(parent)

        self._report_token = report_token

        self._entries = list(entries)

        self._max_parallel_workers = max(0, int(max_parallel_workers))

        self._cancel_requested = Event()



    def request_cancel(self) -> None:

        self._cancel_requested.set()



    def cancellation_requested(self) -> bool:

        return self._cancel_requested.is_set()



    def run(self) -> None:

        try:

            results: dict[tuple[str, str], LiteraturePeriodResult | None] = {}

            successful_entries = 0

            total_entries = len(self._entries)

            cancelled = self._cancel_requested.is_set()

            if total_entries <= 1:

                worker_count = 1

                execution_mode = "serial"

                self.progress_updated.emit("Pulling literature period data in background using 1 network worker (serial mode).")

                for index, entry in enumerate(self._entries, start=1):

                    if self._cancel_requested.is_set():

                        cancelled = True

                        self.progress_updated.emit("Literature period cancellation requested; stopping before the next source row.")

                        break

                    self.progress_updated.emit(f"[Literature Period {index}/{total_entries}] {entry.name} [{entry.catalog}]")

                    result = fetch_catalog_literature_period_result(entry)

                    results[(entry.catalog, entry.source_id)] = result

                    if result is None:

                        self.progress_updated.emit(

                            f"[Literature Period {index}/{total_entries}] No literature period result for {entry.name} [{entry.catalog}]"

                        )

                        continue

                    successful_entries += 1

                    self.progress_updated.emit(

                        f"[Literature Period {index}/{total_entries}] Period={result.period_days:.4f} d from {result.source or entry.catalog.upper()} for {entry.name} [{entry.catalog}]"

                    )

            else:

                worker_count = _resolved_literature_period_workers(total_entries, self._max_parallel_workers)

                execution_mode = "thread"

                self.progress_updated.emit(

                    f"Pulling literature period data across {total_entries} source row(s) with {worker_count} network worker(s) in parallel thread mode."

                )

                pending_entries = deque(self._entries)

                future_map: dict[object, object] = {}

                completion_index = 0

                cancellation_message_emitted = False

                executor = ThreadPoolExecutor(max_workers=worker_count)

                try:

                    while pending_entries and len(future_map) < worker_count and not self._cancel_requested.is_set():

                        entry = pending_entries.popleft()

                        future_map[executor.submit(_fetch_literature_period_task, entry)] = entry



                    while future_map:

                        done, _pending = wait(set(future_map), timeout=0.1, return_when=FIRST_COMPLETED)

                        if not done:

                            if self._cancel_requested.is_set() and not cancellation_message_emitted:

                                cancelled = True

                                cancellation_message_emitted = True

                                pending_entries.clear()

                                self.progress_updated.emit(

                                    "Literature period cancellation requested; waiting for active network requests to finish."

                                )

                            continue

                        for future in done:

                            _entry = future_map.pop(future)

                            completion_index += 1

                            cache_key, source_name, catalog_name, result = future.result()

                            results[cache_key] = result

                            if result is None:

                                self.progress_updated.emit(

                                    f"[Literature Period {completion_index}/{total_entries}] No literature period result for {source_name} [{catalog_name}]"

                                )

                                continue

                            successful_entries += 1

                            self.progress_updated.emit(

                                f"[Literature Period {completion_index}/{total_entries}] Period={result.period_days:.4f} d from {result.source or catalog_name.upper()} for {source_name} [{catalog_name}]"

                            )

                        while pending_entries and len(future_map) < worker_count and not self._cancel_requested.is_set():

                            entry = pending_entries.popleft()

                            future_map[executor.submit(_fetch_literature_period_task, entry)] = entry

                        if self._cancel_requested.is_set() and not cancellation_message_emitted:

                            cancelled = True

                            cancellation_message_emitted = True

                            pending_entries.clear()

                            self.progress_updated.emit(

                                "Literature period cancellation requested; waiting for active network requests to finish."

                            )

                finally:

                    executor.shutdown(wait=True, cancel_futures=True)

        except Exception as exc:

            self.literature_failed.emit(str(exc))

            return



        self.literature_completed.emit(

            LiteraturePeriodBatchResult(

                report_token=self._report_token,

                results=results,

                success_count=successful_entries,

                total_count=total_entries,

                worker_count=worker_count,

                execution_mode=execution_mode,

                cancelled=cancelled,

            )

        )





class OptimizeComparisonFitWorker(QThread):

    optimization_completed = Signal(object)

    optimization_failed = Signal(str)

    progress_updated = Signal(str)



    def __init__(

        self,

        report_token: int,

        target_source_id: str,

        target_source_name: str,

        filter_name: str,

        target_measurements: list[PhotometryMeasurement],

        reference_measurements: list[PhotometryMeasurement],

        actual_period_days: float,

        preferred_comparison_source_ids: list[str],

        nearby_reference_count: int,

        fit_config: LightCurveFitConfig | None,

        y_axis_mode: str,

        period_method: str,

        period_convention: str,

        target_magnitude: float | None = None,

        reference_source_magnitudes: dict[str, float] | None = None,

        max_parallel_workers: int = 0,

        stop_match_index_threshold: float = 0.0,

        eclipsing_binary_match_tolerance: float = 0.0,

        fallback_candidate_pool_size: int = 0,

        fallback_magnitude_tolerance: float = 0.0,

        parent: object | None = None,

    ) -> None:

        super().__init__(parent)

        self._report_token = report_token

        self._target_source_id = target_source_id

        self._target_source_name = target_source_name

        self._filter_name = filter_name

        self._target_measurements = list(target_measurements)

        self._reference_measurements = list(reference_measurements)

        self._actual_period_days = actual_period_days

        self._preferred_comparison_source_ids = list(preferred_comparison_source_ids)

        self._nearby_reference_count = max(1, nearby_reference_count)

        self._target_magnitude = target_magnitude if target_magnitude is None or math.isfinite(target_magnitude) else None

        self._reference_source_magnitudes = {

            str(source_id): float(magnitude)

            for source_id, magnitude in (reference_source_magnitudes or {}).items()

            if isinstance(source_id, str) and math.isfinite(float(magnitude))

        }

        self._fit_config = fit_config

        self._y_axis_mode = y_axis_mode

        self._period_method = period_method

        self._period_convention = period_convention

        self._max_parallel_workers = max(0, int(max_parallel_workers))

        self._stop_match_index_threshold = min(100.0, max(0.0, float(stop_match_index_threshold)))

        self._eclipsing_binary_match_tolerance = min(50.0, max(0.0, float(eclipsing_binary_match_tolerance)))

        self._fallback_candidate_pool_size = max(0, int(fallback_candidate_pool_size))

        self._fallback_magnitude_tolerance = min(10.0, max(0.0, float(fallback_magnitude_tolerance)))

        self._cancel_requested = Event()



    def request_cancel(self) -> None:

        self._cancel_requested.set()



    def cancellation_requested(self) -> bool:

        return self._cancel_requested.is_set()



    def _match_threshold_reached(self, trial: ComparisonFitTrialResult | None) -> bool:

        return bool(

            self._stop_match_index_threshold > 0

            and trial is not None

            and trial.match_index is not None

            and trial.match_index >= self._stop_match_index_threshold

        )



    def _search_candidate_groups(

        self,

        comparison_groups: list[tuple[str, ...]],

        *,

        candidate_ids: list[str],

        subset_size: int,

        sampled_search: bool,

        name_lookup: dict[str, str],

        search_label: str,

    ) -> tuple[ComparisonFitTrialResult | None, int, int, str, bool, bool]:

        search_mode = "sampled" if sampled_search else "exhaustive"

        total_groups = len(comparison_groups)

        label_prefix = f"{search_label} " if search_label else ""

        best_trial: ComparisonFitTrialResult | None = None

        cancelled = False

        threshold_reached = False



        if total_groups <= 1:

            worker_count = 1

            execution_mode = "serial"

            self.progress_updated.emit(

                f"Evaluating {total_groups} {search_mode} {label_prefix}comparison-star combination(s) for {self._target_source_name} [{self._filter_name}] against literature period {self._actual_period_days:.4f} d using subset size {subset_size} from {len(candidate_ids)} reference star(s) in serial mode."

            )

            completed_count = 0

            for comparison_group in comparison_groups:

                if self._cancel_requested.is_set():

                    cancelled = True

                    self.progress_updated.emit("Comparison-fit search cancellation requested; stopping before the next combination.")

                    break

                task_result = _comparison_fit_task(

                    self._target_measurements,

                    self._reference_measurements,

                    comparison_group,

                    self._actual_period_days,

                    self._fit_config,

                    self._y_axis_mode,

                    self._period_method,

                    self._period_convention,

                    self._eclipsing_binary_match_tolerance,

                )

                completed_count += 1

                trial = _comparison_fit_trial_from_task_result(task_result, name_lookup)

                self.progress_updated.emit(_comparison_fit_progress_message(completed_count, total_groups, trial))

                if _comparison_fit_trial_is_better(trial, best_trial):

                    best_trial = trial

                if self._match_threshold_reached(best_trial):

                    threshold_reached = True

                    self.progress_updated.emit(

                        f"Reached the configured match-index threshold ({self._stop_match_index_threshold:.2f}) for {self._target_source_name} [{self._filter_name}]; stopping further {label_prefix}comparison-fit evaluation."

                    )

                    break

            return best_trial, completed_count, worker_count, execution_mode, threshold_reached, cancelled



        worker_count = _resolved_comparison_fit_workers(total_groups, self._max_parallel_workers)

        execution_mode = "process"

        self.progress_updated.emit(

            f"Evaluating {total_groups} {search_mode} {label_prefix}comparison-star combination(s) for {self._target_source_name} [{self._filter_name}] against literature period {self._actual_period_days:.4f} d using subset size {subset_size} from {len(candidate_ids)} reference star(s) with {worker_count} CPU worker(s) in parallel process mode."

        )

        pending_groups = deque(comparison_groups)

        future_map: dict[object, tuple[str, ...]] = {}

        completed_count = 0

        stop_message_emitted = False

        executor = ProcessPoolExecutor(max_workers=worker_count)

        try:

            while pending_groups and len(future_map) < worker_count and not self._cancel_requested.is_set():

                comparison_group = pending_groups.popleft()

                future = executor.submit(

                    _comparison_fit_task,

                    self._target_measurements,

                    self._reference_measurements,

                    comparison_group,

                    self._actual_period_days,

                    self._fit_config,

                    self._y_axis_mode,

                    self._period_method,

                    self._period_convention,

                    self._eclipsing_binary_match_tolerance,

                )

                future_map[future] = comparison_group



            while future_map:

                done, _pending = wait(set(future_map), timeout=0.1, return_when=FIRST_COMPLETED)

                if not done:

                    if self._cancel_requested.is_set() and not stop_message_emitted:

                        cancelled = True

                        stop_message_emitted = True

                        pending_groups.clear()

                        self.progress_updated.emit("Comparison-fit cancellation requested; waiting for active CPU tasks to finish.")

                    continue

                for future in done:

                    future_map.pop(future)

                    completed_count += 1

                    trial = _comparison_fit_trial_from_task_result(future.result(), name_lookup)

                    self.progress_updated.emit(_comparison_fit_progress_message(completed_count, total_groups, trial))

                    if _comparison_fit_trial_is_better(trial, best_trial):

                        best_trial = trial

                    if self._match_threshold_reached(best_trial):

                        threshold_reached = True

                if threshold_reached and not stop_message_emitted:

                    stop_message_emitted = True

                    pending_groups.clear()

                    self.progress_updated.emit(

                        f"Reached the configured match-index threshold ({self._stop_match_index_threshold:.2f}) for {self._target_source_name} [{self._filter_name}]; waiting for active CPU tasks to finish before finalizing the {label_prefix}best fit."

                    )

                while pending_groups and len(future_map) < worker_count and not self._cancel_requested.is_set() and not threshold_reached:

                    comparison_group = pending_groups.popleft()

                    future = executor.submit(

                        _comparison_fit_task,

                        self._target_measurements,

                        self._reference_measurements,

                        comparison_group,

                        self._actual_period_days,

                        self._fit_config,

                        self._y_axis_mode,

                        self._period_method,

                        self._period_convention,

                        self._eclipsing_binary_match_tolerance,

                    )

                    future_map[future] = comparison_group

                if self._cancel_requested.is_set() and not stop_message_emitted:

                    cancelled = True

                    stop_message_emitted = True

                    pending_groups.clear()

                    self.progress_updated.emit("Comparison-fit cancellation requested; waiting for active CPU tasks to finish.")

        finally:

            executor.shutdown(wait=True, cancel_futures=True)

        return best_trial, completed_count, worker_count, execution_mode, threshold_reached, cancelled



    def run(self) -> None:

        try:

            candidate_ids = _ordered_reference_candidate_ids(self._target_measurements, self._reference_measurements)

            if not candidate_ids:

                self.optimization_failed.emit("No usable reference-star pool is available for the selected target and filter.")

                return

            subset_size = min(self._nearby_reference_count, len(candidate_ids))

            comparison_groups, total_group_count, sampled_search = _comparison_source_groups(

                candidate_ids,

                subset_size,

                self._preferred_comparison_source_ids,

            )

            if not comparison_groups:

                self.optimization_failed.emit("No comparison-star combinations are available for the selected target.")

                return



            best_trial: ComparisonFitTrialResult | None

            best_measurements = list(self._target_measurements)

            best_series: LightCurveSeries | None = None

            best_period_result: FitPeriodInferenceResult | None = None

            name_lookup = _comparison_source_names_by_id(self._reference_measurements)

            total_combination_count = total_group_count

            sampled_search_any = sampled_search

            best_trial, completed_count, worker_count, execution_mode, threshold_reached, cancelled = self._search_candidate_groups(

                comparison_groups,

                candidate_ids=candidate_ids,

                subset_size=subset_size,

                sampled_search=sampled_search,

                name_lookup=name_lookup,

                search_label="",

            )



            if (

                not cancelled

                and not threshold_reached

                and self._stop_match_index_threshold > 0

                and (best_trial is None or best_trial.match_index is None or best_trial.match_index < self._stop_match_index_threshold)

            ):

                resolved_target_magnitude = _resolved_fallback_target_magnitude(

                    self._target_measurements,

                    self._target_magnitude,

                )

                fallback_candidate_ids = _magnitude_sorted_reference_candidate_ids(

                    self._target_measurements,

                    self._reference_measurements,

                    resolved_target_magnitude,

                    self._reference_source_magnitudes,

                    self._fallback_magnitude_tolerance,

                    self._fallback_candidate_pool_size,

                )

                if resolved_target_magnitude is None:

                    self.progress_updated.emit(

                        f"Configured match-index threshold ({self._stop_match_index_threshold:.2f}) was not met for {self._target_source_name} [{self._filter_name}], but fallback magnitude-matched retry is unavailable because the target has no catalog or calibrated magnitude estimate."

                    )

                elif len(fallback_candidate_ids) < subset_size:

                    self.progress_updated.emit(

                        f"Configured match-index threshold ({self._stop_match_index_threshold:.2f}) was not met for {self._target_source_name} [{self._filter_name}], but fallback magnitude-matched retry found only {len(fallback_candidate_ids)} usable reference star(s) within +/- {self._fallback_magnitude_tolerance:.2f} mag."

                    )

                elif len(fallback_candidate_ids) == len(candidate_ids) and set(fallback_candidate_ids) == set(candidate_ids):

                    self.progress_updated.emit(

                        f"Configured match-index threshold ({self._stop_match_index_threshold:.2f}) was not met for {self._target_source_name} [{self._filter_name}], but fallback magnitude-matched retry would reuse the same {len(candidate_ids)} reference star(s) as the initial search."

                    )

                else:

                    self.progress_updated.emit(

                        f"Configured match-index threshold ({self._stop_match_index_threshold:.2f}) was not met for {self._target_source_name} [{self._filter_name}]; retrying with up to {len(fallback_candidate_ids)} magnitude-matched reference star(s) within +/- {self._fallback_magnitude_tolerance:.2f} mag."

                    )

                    fallback_groups, fallback_total_count, fallback_sampled_search = _comparison_source_groups(

                        fallback_candidate_ids,

                        subset_size,

                        [],

                    )

                    if fallback_groups:

                        fallback_best_trial, fallback_completed_count, fallback_worker_count, fallback_execution_mode, fallback_threshold_reached, fallback_cancelled = self._search_candidate_groups(

                            fallback_groups,

                            candidate_ids=fallback_candidate_ids,

                            subset_size=subset_size,

                            sampled_search=fallback_sampled_search,

                            name_lookup=name_lookup,

                            search_label="fallback magnitude-matched",

                        )

                        total_combination_count += fallback_total_count

                        completed_count += fallback_completed_count

                        sampled_search_any = sampled_search_any or fallback_sampled_search

                        worker_count = max(worker_count, fallback_worker_count)

                        execution_mode = "process" if "process" in {execution_mode, fallback_execution_mode} else execution_mode

                        threshold_reached = threshold_reached or fallback_threshold_reached

                        cancelled = cancelled or fallback_cancelled

                        if _comparison_fit_trial_is_better(fallback_best_trial, best_trial):

                            best_trial = fallback_best_trial



            if best_trial is not None and not cancelled:

                best_measurements, best_series, best_period_result = _evaluate_comparison_source_group(

                    self._target_measurements,

                    self._reference_measurements,

                    best_trial.comparison_source_ids,

                    self._fit_config,

                    self._y_axis_mode,

                    self._period_method,

                    best_trial.period_convention,

                )



            if best_trial is not None:

                convention_suffix = " using eclipsing-binary convention" if best_trial.period_convention == "eclipsing_binary" else ""

                self.progress_updated.emit(

                    f"Best comparison fit for {self._target_source_name} [{self._filter_name}] uses {', '.join(best_trial.comparison_source_names)} with period {best_trial.calculated_period_days:.4f} d and match {best_trial.match_index:.2f}{convention_suffix}."

                )

            else:

                self.progress_updated.emit(

                    f"No valid comparison-star combination produced a period match for {self._target_source_name} [{self._filter_name}]."

                )

        except Exception as exc:

            self.optimization_failed.emit(str(exc))

            return



        self.optimization_completed.emit(

            ComparisonFitOptimizationResult(

                report_token=self._report_token,

                target_source_id=self._target_source_id,

                target_source_name=self._target_source_name,

                filter_name=self._filter_name,

                actual_period_days=self._actual_period_days,

                total_combination_count=total_combination_count,

                evaluated_combination_count=completed_count if 'completed_count' in locals() else 0,

                subset_size=subset_size,

                sampled_search=sampled_search_any,

                best_trial=best_trial,

                best_measurements=best_measurements,

                best_series=best_series,

                best_period_result=best_period_result,

                worker_count=worker_count,

                execution_mode=execution_mode,

                cancelled=cancelled,

            )

        )





class DiscoverSourcesWorker(QThread):

    discovery_batch_ready = Signal(object)

    discovery_completed = Signal(object)

    discovery_failed = Signal(str)

    progress_updated = Signal(str)



    def __init__(

        self,

        report_token: int,

        pipeline: PhotometryPipeline,

        source_files: list[FileScanResult],

        settings: AppSettings,

        object_name: str,

        fit_config: LightCurveFitConfig | None,

        y_axis_mode: str,

        period_method: str,

        period_convention: str,

        allowed_source_ids: set[str] | None = None,

        search_multiple_comparison_groups: bool = False,

        parent: object | None = None,

    ) -> None:

        super().__init__(parent)

        self._report_token = report_token

        self._pipeline = pipeline

        self._source_files = list(source_files)

        self._settings = settings

        self._object_name = object_name

        self._fit_config = fit_config

        self._y_axis_mode = y_axis_mode

        self._period_method = period_method

        self._period_convention = period_convention

        self._allowed_source_ids = None if allowed_source_ids is None else {str(source_id) for source_id in allowed_source_ids}

        self._search_multiple_comparison_groups = bool(search_multiple_comparison_groups)

        self._cancel_requested = Event()



    def request_cancel(self) -> None:

        self._cancel_requested.set()



    def cancellation_requested(self) -> bool:

        return self._cancel_requested.is_set()



    def _emit_progress(self, message: str) -> None:

        self.progress_updated.emit(message)



    def _measure_reference_file(

        self,

        file_result: FileScanResult,

        solved_field: object,

        field_catalog: object,

        reference_stars: list[CatalogStar],

    ) -> tuple[str, tuple[float, float, float, float | None], list[PhotometryMeasurement], str | None]:

        aperture_profile = resolve_aperture_profile(

            source_path=file_result.path,

            wcs_path=solved_field.wcs_path,

            variable_stars=list(getattr(field_catalog, "variable_stars", [])),

            reference_stars=reference_stars,

            settings=self._settings,

        )

        reference_measurements = measure_targets(

            source_path=file_result.path,

            scan_result=file_result,

            wcs_path=solved_field.wcs_path,

            variable_stars=[],

            reference_stars=reference_stars,

            aperture_radius=aperture_profile.aperture_radius,

            annulus_inner_radius=aperture_profile.annulus_inner_radius,

            annulus_outer_radius=aperture_profile.annulus_outer_radius,

            frame_edge_margin_percent=self._settings.frame_edge_margin_percent,

            saturation_filter_enabled=self._settings.saturation_filter_enabled,

            settings=self._settings,

            adaptive_frame_fwhm_pixels=aperture_profile.fwhm_pixels,

        )

        return (

            str(file_result.path),

            (

                aperture_profile.aperture_radius,

                aperture_profile.annulus_inner_radius,

                aperture_profile.annulus_outer_radius,

                aperture_profile.fwhm_pixels,

            ),

            reference_measurements,

            aperture_profile.note,

        )



    def _measure_candidate_file(

        self,

        file_result: FileScanResult,

        solved_field: object,

        discovery_target: CatalogStar,

        aperture_values: tuple[float, float, float, float | None],

        reference_measurements: list[PhotometryMeasurement],

    ) -> tuple[list[PhotometryMeasurement], list[PhotometryMeasurement]]:

        aperture_radius, annulus_inner_radius, annulus_outer_radius, adaptive_frame_fwhm_pixels = aperture_values

        target_measurements = measure_targets(

            source_path=file_result.path,

            scan_result=file_result,

            wcs_path=solved_field.wcs_path,

            variable_stars=[discovery_target],

            reference_stars=[],

            aperture_radius=aperture_radius,

            annulus_inner_radius=annulus_inner_radius,

            annulus_outer_radius=annulus_outer_radius,

            frame_edge_margin_percent=self._settings.frame_edge_margin_percent,

            saturation_filter_enabled=self._settings.saturation_filter_enabled,

            settings=self._settings,

            adaptive_frame_fwhm_pixels=adaptive_frame_fwhm_pixels,

        )

        return target_measurements, list(reference_measurements)


    def _measure_candidate_batch_file(

        self,

        file_result: FileScanResult,

        solved_field: object,

        discovery_targets: Sequence[CatalogStar],

        aperture_values: tuple[float, float, float, float | None],

    ) -> list[PhotometryMeasurement]:

        if not discovery_targets:

            return []

        aperture_radius, annulus_inner_radius, annulus_outer_radius, adaptive_frame_fwhm_pixels = aperture_values

        return measure_targets(

            source_path=file_result.path,

            scan_result=file_result,

            wcs_path=solved_field.wcs_path,

            variable_stars=list(discovery_targets),

            reference_stars=[],

            aperture_radius=aperture_radius,

            annulus_inner_radius=annulus_inner_radius,

            annulus_outer_radius=annulus_outer_radius,

            frame_edge_margin_percent=self._settings.frame_edge_margin_percent,

            saturation_filter_enabled=self._settings.saturation_filter_enabled,

            settings=self._settings,

            adaptive_frame_fwhm_pixels=adaptive_frame_fwhm_pixels,

        )


    def _relabel_discovery_measurements(

        self,

        measurements: Sequence[PhotometryMeasurement],

        *,

        source_name: str,

    ) -> list[PhotometryMeasurement]:

        return [replace(measurement, source_name=source_name) for measurement in measurements]


    def _relabel_discovery_series(self, series: LightCurveSeries, *, source_name: str) -> LightCurveSeries:

        return replace(series, source_name=source_name)


    def _screen_random_search_candidates_by_ideal_adu(

        self,

        candidates: Sequence[CatalogStar],

        measurable_files: Sequence[tuple[FileScanResult, object]],

        per_file_apertures: dict[str, tuple[float, float, float, float | None]],

    ) -> list[CatalogStar]:

        if not candidates or not measurable_files:

            return []

        representative_file, representative_result = measurable_files[0]

        solved_field = getattr(representative_result, "solved_field", None)

        if solved_field is None:

            return []

        aperture_values = per_file_apertures.get(str(representative_file.path))

        if aperture_values is None:

            return []

        aperture_radius, annulus_inner_radius, annulus_outer_radius, adaptive_frame_fwhm_pixels = aperture_values

        screened_measurements = measure_targets(

            source_path=representative_file.path,

            scan_result=representative_file,

            wcs_path=solved_field.wcs_path,

            variable_stars=list(candidates),

            reference_stars=[],

            aperture_radius=aperture_radius,

            annulus_inner_radius=annulus_inner_radius,

            annulus_outer_radius=annulus_outer_radius,

            frame_edge_margin_percent=self._settings.frame_edge_margin_percent,

            saturation_filter_enabled=self._settings.saturation_filter_enabled,

            settings=self._settings,

            adaptive_frame_fwhm_pixels=adaptive_frame_fwhm_pixels,

        )

        selected_keys = {

            (measurement.catalog, measurement.source_id)

            for measurement in screened_measurements

            if _measurement_matches_discovery_ideal_adu_range(measurement)

        }

        selected = [entry for entry in candidates if (entry.catalog, entry.source_id) in selected_keys]

        selected.sort(key=lambda entry: (float(entry.magnitude), entry.name.lower(), entry.source_id))

        return selected


    def _emit_discovery_batch(

        self,

        source_results: list[DiscoverSourceResult],

        *,

        tested_candidate_count: int,

        retained_candidate_count: int,

        reference_star_count: int,

        total_candidate_count: int,

        current_stage_label: str,

    ) -> None:

        self.discovery_batch_ready.emit(

            DiscoverBatchResult(

                report_token=self._report_token,

                source_results=source_results,

                tested_candidate_count=tested_candidate_count,

                retained_candidate_count=retained_candidate_count,

                reference_star_count=reference_star_count,

                total_candidate_count=total_candidate_count,

                current_stage_label=current_stage_label,

                cancelled=False,

            )

        )



    def run(self) -> None:

        try:

            if not self._source_files:

                self.discovery_failed.emit("No source files are available for Discover.")

                return

            self._emit_progress(f"Preparing Discover for {self._object_name}.")

            solved_results = self._pipeline._resolve_summary_fields(self._source_files, self._settings, self._emit_progress)

            measurable_files = [

                (file_result, solve_result)

                for file_result, solve_result in solved_results

                if solve_result.solved_field is not None

            ]

            if not measurable_files:

                self.discovery_failed.emit("Discover requires at least one solved frame.")

                return

            if self._cancel_requested.is_set():

                self.discovery_completed.emit(

                    DiscoverBatchResult(

                        report_token=self._report_token,

                        source_results=[],

                        tested_candidate_count=0,

                        retained_candidate_count=0,

                        reference_star_count=0,

                        total_candidate_count=0,

                        cancelled=True,

                    )

                )

                return

            catalog_service = CatalogService(self._settings.cache_dir / "catalogs")

            try:

                field_catalog = self._pipeline._best_field_catalog_for_solved_results(catalog_service, solved_results, self._emit_progress)

            except Exception as exc:

                self.discovery_failed.emit(f"Discover catalog lookup failed: {summarize_catalog_service_error(exc)}")

                return

            reference_stars = select_reference_stars(

                field_catalog.gaia_stars,

                [*field_catalog.variable_stars, *field_catalog.exoplanets],

                minimum_magnitude=self._settings.reference_star_min_magnitude,

                maximum_magnitude=self._settings.reference_star_max_magnitude,

            )

            if not reference_stars:

                self.discovery_failed.emit("Discover could not build a usable comparison-star pool.")

                return

            configured_workers, worker_label = _resolve_photometry_parallel_workers(self._settings)

            photometry_worker_count = min(configured_workers, len(measurable_files))

            self._emit_progress(

                f"Discover photometry will use {photometry_worker_count} worker(s) ({worker_label}) across {len(measurable_files)} solved frame(s)."

            )

            per_file_reference_measurements: dict[str, list[PhotometryMeasurement]] = {}

            per_file_apertures: dict[str, tuple[float, float, float, float | None]] = {}

            aperture_note_emitted = False

            with ThreadPoolExecutor(max_workers=max(1, photometry_worker_count)) as executor:

                reference_future_map = {

                    executor.submit(

                        self._measure_reference_file,

                        file_result,

                        solve_result.solved_field,

                        field_catalog,

                        reference_stars,

                    ): (index, file_result)

                    for index, (file_result, solve_result) in enumerate(measurable_files, start=1)

                    if solve_result.solved_field is not None

                }

                for future in as_completed(reference_future_map):

                    if self._cancel_requested.is_set():

                        executor.shutdown(wait=False, cancel_futures=True)

                        self.discovery_completed.emit(

                            DiscoverBatchResult(

                                report_token=self._report_token,

                                source_results=[],

                                tested_candidate_count=0,

                                retained_candidate_count=0,

                                reference_star_count=len(reference_stars),

                                total_candidate_count=0,

                                cancelled=True,

                            )

                        )

                        return

                    index, file_result = reference_future_map[future]

                    file_key, aperture_values, reference_measurements, aperture_note = future.result()

                    if aperture_note and not aperture_note_emitted:

                        self._emit_progress(aperture_note)

                        aperture_note_emitted = True

                    per_file_apertures[file_key] = aperture_values

                    per_file_reference_measurements[file_key] = reference_measurements

                    self._emit_progress(

                        f"[Discover setup {index}/{len(measurable_files)}] Cached reusable reference measurements for {file_result.path.name}."

                    )

            discover_targets = _discover_raw_light_curve_target_stars(
                field_catalog,
                reference_stars,
                self._allowed_source_ids,
            )

            target_plans = [

                DiscoverTargetPlan(entry=entry, search_type="raw light curve")

                for entry in discover_targets

            ]

            if not target_plans:

                self.discovery_completed.emit(

                    DiscoverBatchResult(

                        report_token=self._report_token,

                        source_results=[],

                        tested_candidate_count=0,

                        retained_candidate_count=0,

                        reference_star_count=len(reference_stars),

                        total_candidate_count=0,

                        cancelled=False,

                    )

                )

                return

            self._emit_progress(

                f"Discover will build raw light curves for {len(target_plans)} non-variable star(s) with {len(reference_stars)} reusable comparison star(s)."

            )

            retained_results: list[DiscoverSourceResult] = []

            tested_count = 0

            target_entries_by_id = {target_plan.entry.source_id: target_plan.entry for target_plan in target_plans}

            candidate_measurements_by_id: dict[str, list[PhotometryMeasurement]] = {

                source_id: []

                for source_id in target_entries_by_id

            }

            reference_measurements = [

                measurement

                for per_file_measurements in per_file_reference_measurements.values()

                for measurement in per_file_measurements

            ]

            reference_measurement_index = _index_reference_measurements_by_source_and_frame(reference_measurements)

            reference_candidate_measurements = []

            for source_rows in reference_measurement_index.values():

                representative_row = next(

                    (

                        measurement

                        for measurement in source_rows.values()

                        if measurement.flux is not None

                        and measurement.flux > 0

                        and not measurement.is_saturated

                        and not measurement.is_near_saturated

                    ),

                    None,

                )

                if representative_row is not None:

                    reference_candidate_measurements.append(representative_row)

            reference_source_magnitudes = {

                entry.source_id: float(entry.magnitude)

                for entry in reference_stars

                if entry.magnitude is not None and math.isfinite(float(entry.magnitude))

            }

            evaluation_worker_count = min(max(1, configured_workers), len(target_plans))

            worker_pool_count = max(1, photometry_worker_count, evaluation_worker_count)

            with ThreadPoolExecutor(max_workers=worker_pool_count) as executor:

                candidate_entries = [

                    _prepare_discovery_target(

                        target_plan.entry,

                        discover_name=target_plan.entry.name,

                        discover_type=target_plan.search_type,

                    )

                    for target_plan in target_plans

                ]

                candidate_future_map = {

                    executor.submit(

                        self._measure_candidate_batch_file,

                        file_result,

                        solve_result.solved_field,

                        candidate_entries,

                        per_file_apertures[str(file_result.path)],

                    ): (index, file_result)

                    for index, (file_result, solve_result) in enumerate(measurable_files, start=1)

                    if solve_result.solved_field is not None

                }

                for future in as_completed(candidate_future_map):

                    if self._cancel_requested.is_set():

                        executor.shutdown(wait=False, cancel_futures=True)

                        self.discovery_completed.emit(

                            DiscoverBatchResult(

                                report_token=self._report_token,

                                source_results=[],

                                tested_candidate_count=0,

                                retained_candidate_count=0,

                                reference_star_count=len(reference_stars),

                                total_candidate_count=len(target_plans),

                                cancelled=True,

                            )

                        )

                        return

                    index, file_result = candidate_future_map[future]

                    batch_measurements = future.result()

                    for measurement in batch_measurements:

                        candidate_measurements_by_id.setdefault(measurement.source_id, []).append(measurement)

                    self._emit_progress(

                        f"[Discover targets {index}/{len(measurable_files)}] Measured {len(batch_measurements)} candidate row(s) in {file_result.path.name}."

                    )

                self._emit_progress(

                    f"Discover post-measurement evaluation will use {evaluation_worker_count} worker(s) across {len(target_plans)} target star(s)."

                )

                evaluation_future_map = {

                    executor.submit(

                        _evaluate_discover_target_plan,

                        candidate_index=candidate_index,

                        total_candidate_count=len(target_plans),

                        target_plan=target_plan,

                        target_measurements=list(candidate_measurements_by_id.get(target_plan.entry.source_id, [])),

                        reference_candidate_measurements=reference_candidate_measurements,

                        reference_measurement_index=reference_measurement_index,

                        reference_source_magnitudes=reference_source_magnitudes,

                        nearby_reference_count=int(self._settings.nearby_reference_count),

                        search_multiple_comparison_groups=self._search_multiple_comparison_groups,

                        y_axis_mode=self._y_axis_mode,

                        period_method=self._period_method,

                        period_convention=self._period_convention,

                    ): candidate_index

                    for candidate_index, target_plan in enumerate(target_plans, start=1)

                }

                pending_evaluations: dict[int, _DiscoverTargetEvaluationResult] = {}

                next_result_index = 1

                for future in as_completed(evaluation_future_map):

                    if self._cancel_requested.is_set():

                        executor.shutdown(wait=False, cancel_futures=True)

                        self.discovery_completed.emit(

                            DiscoverBatchResult(

                                report_token=self._report_token,

                                source_results=[],

                                tested_candidate_count=tested_count,

                                retained_candidate_count=len(retained_results),

                                reference_star_count=len(reference_stars),

                                total_candidate_count=len(target_plans),

                                cancelled=True,

                            )

                        )

                        return

                    evaluation_result = future.result()

                    pending_evaluations[evaluation_result.candidate_index] = evaluation_result

                    while next_result_index in pending_evaluations:

                        ordered_result = pending_evaluations.pop(next_result_index)

                        target_plan = ordered_result.target_plan

                        catalog_entry = target_plan.entry

                        tested_count += 1

                        if ordered_result.skip_message:

                            self._emit_progress(ordered_result.skip_message)

                            next_result_index += 1

                            continue

                        if ordered_result.best_measurements is None or ordered_result.best_series is None:

                            self._emit_progress(

                                f"[Discover {ordered_result.candidate_index}/{len(target_plans)} | {target_plan.search_type}] Skipped {catalog_entry.name}: no differential light curve could be built. {_summarize_discovery_series_failure(ordered_result.best_failure_diagnostics, raw_target_row_count=ordered_result.raw_target_row_count)}"

                            )

                            next_result_index += 1

                            continue

                        discovery_target = _prepare_discovery_target(

                            catalog_entry,

                            discover_name=f"Discover {len(retained_results) + 1}",

                            discover_type=target_plan.search_type,

                        )

                        relabeled_measurements = self._relabel_discovery_measurements(

                            ordered_result.best_measurements,

                            source_name=discovery_target.name,

                        )

                        relabeled_series = self._relabel_discovery_series(ordered_result.best_series, source_name=discovery_target.name)

                        retained_result = DiscoverSourceResult(

                            entry=discovery_target,

                            best_measurements=relabeled_measurements,

                            best_series=relabeled_series,

                            best_period_result=None,

                            total_combination_count=ordered_result.total_group_count,

                            evaluated_combination_count=ordered_result.evaluated_group_count,

                            valid_point_count=ordered_result.valid_point_count,

                            excluded_point_count=ordered_result.excluded_point_count,

                            median_comparison_scatter=ordered_result.median_comparison_scatter,

                            sampled_search=ordered_result.sampled_search,

                        )

                        retained_results.append(retained_result)

                        self._emit_discovery_batch(

                            [retained_result],

                            tested_candidate_count=tested_count,

                            retained_candidate_count=len(retained_results),

                            reference_star_count=len(reference_stars),

                            total_candidate_count=len(target_plans),

                            current_stage_label=target_plan.search_type,

                        )

                        scatter_text = "-" if ordered_result.median_comparison_scatter is None else f"{ordered_result.median_comparison_scatter:.4f}"

                        self._emit_progress(

                            f"[Discover {ordered_result.candidate_index}/{len(target_plans)} | {target_plan.search_type}] Added {discovery_target.name} from {catalog_entry.name}: score={ordered_result.candidate_score:.1f}, valid={ordered_result.valid_point_count}, excluded={ordered_result.excluded_point_count}, scatter={scatter_text}."

                        )

                        next_result_index += 1

            retained_results.sort(key=lambda item: (-item.best_series.candidate_score, item.entry.name.lower(), item.entry.source_id))

        except Exception as exc:

            self.discovery_failed.emit(str(exc))

            return

        self.discovery_completed.emit(

            DiscoverBatchResult(

                report_token=self._report_token,

                source_results=retained_results,

                tested_candidate_count=tested_count if 'tested_count' in locals() else 0,

                retained_candidate_count=len(retained_results),

                reference_star_count=len(reference_stars) if 'reference_stars' in locals() else 0,

                total_candidate_count=len(target_plans) if 'target_plans' in locals() else 0,

                cancelled=False,

            )

        )





class IncreaseSnrWorker(QThread):

    increase_snr_completed = Signal(object)

    increase_snr_failed = Signal(str)

    progress_updated = Signal(str)



    def __init__(

        self,

        report_token: int,

        tasks: list[SnrBinningTask],

        settings: SnrBinningSettings,

        parent: object | None = None,

    ) -> None:

        super().__init__(parent)

        self._report_token = report_token

        self._tasks = list(tasks)

        self._settings = settings.normalized()

        self._cancel_requested = Event()



    def request_cancel(self) -> None:

        self._cancel_requested.set()



    def cancellation_requested(self) -> bool:

        return self._cancel_requested.is_set()



    def run(self) -> None:

        try:

            total_tasks = len(self._tasks)

            source_results: list[SnrBinningSourceResult] = []

            processed_count = 0

            skipped_count = 0

            failed_count = 0

            cancelled = False

            self.progress_updated.emit(

                f"Increase SNR running for {total_tasks} selected source(s) using conservative period-aware binning in serial background mode."

            )

            for index, task in enumerate(self._tasks, start=1):

                if self._cancel_requested.is_set():

                    cancelled = True

                    self.progress_updated.emit("Increase SNR cancellation requested; stopping before the next selected source.")

                    break

                self.progress_updated.emit(f"[Increase SNR {index}/{total_tasks}] {task.source_name} [{task.catalog}]")

                try:

                    result = process_snr_binning_task(task, self._settings)

                except Exception as exc:

                    failed_count += 1

                    self.progress_updated.emit(f"[Increase SNR {index}/{total_tasks}] Failed for {task.source_name} [{task.catalog}]: {exc}")

                    source_results.append(

                        SnrBinningSourceResult(

                            source_id=task.source_id,

                            source_name=task.source_name,

                            catalog=task.catalog,

                            status="failed",

                            series_results=[],

                            apply_scope_label=(

                                "currently filtered measurements" if self._settings.apply_to_selected_measurements_only else "all measurements for the source"

                            ),

                            dataset_mode=self._settings.dataset_mode,

                            message=str(exc),

                        )

                    )

                    continue

                source_results.append(result)

                processed_count += 1 if result.status == "processed" else 0

                skipped_count += 1 if result.status == "skipped" else 0

                failed_count += 1 if result.status == "failed" else 0

                self.progress_updated.emit(f"[Increase SNR {index}/{total_tasks}] {summarize_snr_binning_result(result)}")

        except Exception as exc:

            self.increase_snr_failed.emit(str(exc))

            return



        self.increase_snr_completed.emit(

            IncreaseSnrBatchResult(

                report_token=self._report_token,

                source_results=source_results,

                processed_count=processed_count,

                skipped_count=skipped_count,

                failed_count=failed_count,

                cancelled=cancelled,

            )

        )


class UpdateCheckWorker(QThread):

    update_check_completed = Signal(object)

    update_check_failed = Signal(str)

    progress_updated = Signal(str)



    def run(self) -> None:

        try:

            from photometry_app.core.app_updates import check_for_updates



            self.progress_updated.emit("Checking GitHub for Citizen Astronomy updates...")

            result = check_for_updates()

        except Exception as exc:

            self.update_check_failed.emit(str(exc))

            return

        self.update_check_completed.emit(result)



class UpdateDownloadWorker(QThread):

    update_download_completed = Signal(object)

    update_download_failed = Signal(str)

    progress_updated = Signal(int, int)



    def __init__(self, update: object, parent: object | None = None) -> None:

        super().__init__(parent)

        self._update = update

        self._cancel_requested = Event()



    def request_cancel(self) -> None:

        self._cancel_requested.set()



    def run(self) -> None:

        try:

            from photometry_app.core.app_updates import download_update_package



            downloaded_update = download_update_package(

                self._update,

                progress_callback=lambda downloaded, total: self.progress_updated.emit(downloaded, total),

                cancellation_requested=self._cancel_requested.is_set,

            )

        except Exception as exc:

            self.update_download_failed.emit(str(exc))

            return

        self.update_download_completed.emit(downloaded_update)

