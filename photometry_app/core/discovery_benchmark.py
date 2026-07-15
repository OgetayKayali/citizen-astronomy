from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import cProfile
import io
from pathlib import Path
import pstats
import time

from photometry_app.core.discovery import DiscoverySearchDiagnostics, MovingObjectDiscoveryResult, _associate_candidates_with_known_detections, _detection_within_estimated_limit, _known_detection_association_radius_px, _known_prediction_paths_by_detection, discover_unmatched_moving_candidates
from photometry_app.core.models import FileScanResult, ObservationMetadata, WcsStatus
from photometry_app.core.scanner import inspect_fits_file
from photometry_app.core.settings import AppSettings
from photometry_app.core.solar_system import SolarSystemDetection, SolarSystemDetectionResult, SolarSystemVisibilityEstimateResult, detect_known_solar_system_objects, estimate_visible_magnitude_limit


_SUPPORTED_DISCOVERY_SUFFIXES = {".fit", ".fits", ".xisf"}
_MIN_UTC_DATETIME = datetime.min.replace(tzinfo=UTC)


@dataclass(slots=True)
class DiscoveryFrameGroup:
    filter_name: str
    exposure_seconds: float | None
    label: str
    scan_results: tuple[FileScanResult, ...]

    @property
    def frame_paths(self) -> tuple[Path, ...]:
        return tuple(result.path for result in self.scan_results)

    @property
    def frame_metadata(self) -> dict[str, ObservationMetadata]:
        return {str(result.path.resolve()): result.metadata for result in self.scan_results}


@dataclass(slots=True)
class BenchmarkProgressEvent:
    elapsed_seconds: float
    message: str


@dataclass(slots=True)
class DiscoveryBenchmarkReport:
    group_label: str
    frame_count: int
    reference_path: Path
    known_detection_result: SolarSystemDetectionResult
    estimate_result: SolarSystemVisibilityEstimateResult | None
    estimate_error: str | None
    discovery_result: MovingObjectDiscoveryResult
    generate_seconds: float
    estimate_seconds: float | None
    discover_seconds: float
    progress_events: tuple[BenchmarkProgressEvent, ...]
    profile_stats_text: str
    stage_timings: tuple[DiscoveryBenchmarkStageTiming, ...] = ()
    known_object_diagnostics: tuple[KnownObjectBenchmarkDiagnostic, ...] = ()


@dataclass(slots=True)
class DiscoveryBenchmarkStageTiming:
    label: str
    seconds: float


@dataclass(slots=True)
class KnownObjectBenchmarkDiagnostic:
    display_name: str
    status: str
    failure_stage: str | None
    likely_visible: bool
    within_estimated_limit: bool
    residual_frame_matches: int
    linked_candidate_matches: int
    detail_text: str


class _TimedProgressRecorder:
    def __init__(self) -> None:
        self._start_time = time.perf_counter()
        self._events: list[BenchmarkProgressEvent] = []

    def emit(self, message: str) -> None:
        self._events.append(
            BenchmarkProgressEvent(
                elapsed_seconds=time.perf_counter() - self._start_time,
                message=str(message),
            )
        )

    @property
    def events(self) -> tuple[BenchmarkProgressEvent, ...]:
        return tuple(self._events)


def scan_discovery_folder(folder_path: Path, *, observation_timezone: str) -> list[FileScanResult]:
    resolved_folder = folder_path.expanduser().resolve()
    if not resolved_folder.exists() or not resolved_folder.is_dir():
        raise ValueError(f"Discovery folder does not exist: {resolved_folder}")
    object_folder = resolved_folder.name or resolved_folder.parent.name
    scan_results: list[FileScanResult] = []
    for child in sorted(resolved_folder.iterdir(), key=lambda path: path.name.lower()):
        if not child.is_file() or child.suffix.lower() not in _SUPPORTED_DISCOVERY_SUFFIXES:
            continue
        scan_results.append(inspect_fits_file(child, object_folder, observation_timezone=observation_timezone))
    if not scan_results:
        raise ValueError(f"No supported FITS or XISF frames were found in {resolved_folder}")
    return scan_results


def group_discovery_scan_results(scan_results: list[FileScanResult]) -> tuple[DiscoveryFrameGroup, ...]:
    solved_results = [result for result in scan_results if result.wcs_status == WcsStatus.SOLVED]
    if not solved_results:
        raise ValueError("No solved frames are available for Discover benchmarking.")
    grouped_results: dict[tuple[str, float | None], list[FileScanResult]] = {}
    for result in solved_results:
        filter_name = (result.metadata.filter_name or "Unknown").strip() or "Unknown"
        exposure_seconds = None if result.metadata.exposure_seconds is None else float(result.metadata.exposure_seconds)
        grouped_results.setdefault((filter_name, exposure_seconds), []).append(result)
    groups: list[DiscoveryFrameGroup] = []
    for (filter_name, exposure_seconds), results in grouped_results.items():
        ordered_results = tuple(sorted(results, key=_scan_result_sort_key))
        groups.append(
            DiscoveryFrameGroup(
                filter_name=filter_name,
                exposure_seconds=exposure_seconds,
                label=f"Filter {filter_name} | {_exposure_label(exposure_seconds)}",
                scan_results=ordered_results,
            )
        )
    groups.sort(key=lambda item: (item.filter_name.lower(), item.exposure_seconds is None, item.exposure_seconds or 0.0))
    return tuple(groups)


def select_discovery_group(
    groups: tuple[DiscoveryFrameGroup, ...],
    *,
    filter_name: str | None = None,
    exposure_seconds: float | None = None,
) -> DiscoveryFrameGroup:
    if not groups:
        raise ValueError("No discovery frame groups are available.")
    if filter_name is None and exposure_seconds is None:
        if len(groups) == 1:
            return groups[0]
        available = "; ".join(group.label for group in groups)
        raise ValueError(f"Multiple frame groups are available. Select one with --filter and optionally --exposure. Available groups: {available}")
    normalized_filter = None if filter_name is None else (str(filter_name).strip() or "Unknown").lower()
    matched_groups = [
        group
        for group in groups
        if (normalized_filter is None or group.filter_name.lower() == normalized_filter)
        and (exposure_seconds is None or _exposure_matches(group.exposure_seconds, exposure_seconds))
    ]
    if not matched_groups:
        available = "; ".join(group.label for group in groups)
        raise ValueError(f"No frame group matched the requested selection. Available groups: {available}")
    if len(matched_groups) > 1:
        available = "; ".join(group.label for group in matched_groups)
        raise ValueError(f"The requested selection matched multiple groups. Narrow it with --exposure. Matching groups: {available}")
    return matched_groups[0]


def resolve_group_reference_path(group: DiscoveryFrameGroup, reference_name: str | None = None) -> Path:
    frame_paths = list(group.frame_paths)
    if not frame_paths:
        raise ValueError("The selected discovery frame group has no frames.")
    if not reference_name:
        return frame_paths[0]
    normalized_reference_name = str(reference_name).strip().lower()
    for frame_path in frame_paths:
        if frame_path.name.lower() == normalized_reference_name:
            return frame_path
    raise ValueError(f"Reference frame {reference_name} was not found in the selected group.")


def format_profile_stats(profile: cProfile.Profile, *, top_count: int) -> str:
    output = io.StringIO()
    stats = pstats.Stats(profile, stream=output)
    stats.sort_stats("cumulative")
    stats.print_stats(max(1, int(top_count)))
    return output.getvalue().strip()


def run_discovery_benchmark(
    folder_path: Path,
    *,
    settings: AppSettings,
    filter_name: str | None = None,
    exposure_seconds: float | None = None,
    reference_name: str | None = None,
    assume_aligned: bool = False,
    top_profile_functions: int = 30,
    profile_output_path: Path | None = None,
) -> DiscoveryBenchmarkReport:
    scan_results = scan_discovery_folder(folder_path, observation_timezone=settings.observation_timezone)
    groups = group_discovery_scan_results(scan_results)
    selected_group = select_discovery_group(groups, filter_name=filter_name, exposure_seconds=exposure_seconds)
    reference_path = resolve_group_reference_path(selected_group, reference_name=reference_name)
    frame_metadata = selected_group.frame_metadata
    reference_metadata = frame_metadata.get(str(reference_path.resolve()))
    if reference_metadata is None or reference_metadata.date_obs is None:
        raise ValueError(f"Reference frame {reference_path.name} is missing an observation timestamp required for Discover benchmarking.")

    recorder = _TimedProgressRecorder()

    generate_start = time.perf_counter()
    recorder.emit("[Generate] Starting known-object detection for benchmark reference frame.")
    known_detection_result = detect_known_solar_system_objects(
        reference_path,
        observation_time=reference_metadata.date_obs,
        settings=settings,
        exposure_seconds=reference_metadata.exposure_seconds,
        observer_latitude_deg=settings.observing_site_latitude_deg,
        observer_longitude_deg=settings.observing_site_longitude_deg,
        observer_elevation_m=settings.observing_site_elevation_m,
        filter_name=reference_metadata.filter_name,
        magnitude_limit=float(settings.asteroid_default_magnitude_limit),
        observatory_code=str(settings.observer_code or "").strip() or None,
        progress_callback=lambda message: recorder.emit(f"[Generate] {message}"),
    )
    generate_seconds = time.perf_counter() - generate_start
    recorder.emit(f"[Generate] Completed in {generate_seconds:.2f}s with {len(known_detection_result.detections)} known detections.")

    estimate_result: SolarSystemVisibilityEstimateResult | None = None
    estimate_error: str | None = None
    estimate_seconds: float | None = None
    estimate_start = time.perf_counter()
    recorder.emit("[Estimate] Starting visible-limit benchmark estimate.")
    try:
        estimate_result = estimate_visible_magnitude_limit(
            reference_path,
            settings=settings,
            progress_callback=lambda message: recorder.emit(f"[Estimate] {message}"),
        )
        estimate_seconds = time.perf_counter() - estimate_start
        recorder.emit(f"[Estimate] Completed in {estimate_seconds:.2f}s.")
    except Exception as exc:
        estimate_error = str(exc)
        estimate_seconds = time.perf_counter() - estimate_start
        recorder.emit(f"[Estimate] Failed after {estimate_seconds:.2f}s: {estimate_error}")

    discover_profile = cProfile.Profile()
    search_diagnostics = DiscoverySearchDiagnostics()
    recorder.emit("[Discover] Starting discovery benchmark run.")
    discover_start = time.perf_counter()
    discover_profile.enable()
    try:
        discovery_result = discover_unmatched_moving_candidates(
            list(selected_group.frame_paths),
            frame_metadata=frame_metadata,
            known_detection_result=known_detection_result,
            reference_path=reference_path,
            assume_aligned=assume_aligned,
            max_parallel_workers=int(settings.asteroid_search_parallel_workers),
            residual_min_snr=float(settings.asteroid_discovery_min_residual_snr),
            residual_max_snr=float(settings.asteroid_discovery_max_residual_snr),
            frames_per_batch=int(settings.asteroid_discovery_frames_per_batch),
            binning_factor=int(settings.asteroid_discovery_binning_factor),
            use_temporary_cache=bool(settings.asteroid_discovery_use_temporary_cache),
            minimum_candidate_frames=int(settings.asteroid_discovery_min_candidate_frames),
            detection_sigma=float(settings.asteroid_discovery_detection_sigma),
            detection_fwhm=float(settings.asteroid_discovery_detection_fwhm),
            max_residuals_per_frame=int(settings.asteroid_discovery_max_residuals_per_frame),
            edge_margin_px=int(settings.asteroid_discovery_edge_margin_px),
            detector_mode=str(settings.asteroid_discovery_detector_mode or "hybrid"),
            streak_min_area_px=int(settings.asteroid_discovery_streak_min_area_px),
            streak_min_elongation=float(settings.asteroid_discovery_streak_min_elongation),
            potential_candidate_max_deflection_rms_px=float(settings.asteroid_discovery_potential_deflection_rms_px),
            review_candidate_max_deflection_rms_px=float(settings.asteroid_discovery_review_deflection_rms_px),
            enable_synthetic_sweep=bool(settings.asteroid_discovery_enable_synthetic_sweep),
            synthetic_sweep_max_motion_px_per_hour=float(settings.asteroid_discovery_synthetic_sweep_max_motion_px_per_hour),
            synthetic_sweep_motion_step_px_per_hour=float(settings.asteroid_discovery_synthetic_sweep_motion_step_px_per_hour),
            synthetic_sweep_angle_step_deg=float(settings.asteroid_discovery_synthetic_sweep_angle_step_deg),
            synthetic_sweep_direction_focus=str(settings.asteroid_discovery_synthetic_sweep_direction_focus or "all_directions"),
            synthetic_sweep_direction_focus_half_width_deg=float(settings.asteroid_discovery_synthetic_sweep_direction_focus_half_width_deg),
            synthetic_sweep_min_stacked_snr=float(settings.asteroid_discovery_synthetic_sweep_min_stacked_snr),
            synthetic_sweep_save_stacks=bool(settings.asteroid_discovery_synthetic_sweep_save_stacks),
            synthetic_tracking_combine_mode=str(settings.synthetic_tracking_combine_mode or "mean"),
            estimate_result=estimate_result,
            progress_callback=lambda message: recorder.emit(f"[Discover] {message}"),
            search_diagnostics=search_diagnostics,
        )
    finally:
        discover_profile.disable()
    discover_seconds = time.perf_counter() - discover_start
    recorder.emit(
        f"[Discover] Completed in {discover_seconds:.2f}s with {discovery_result.candidate_count} potential candidates, "
        f"{len(discovery_result.review_candidates)} review candidates, and {discovery_result.recovered_known_count} recovered known objects."
    )

    if profile_output_path is not None:
        profile_output_path.parent.mkdir(parents=True, exist_ok=True)
        discover_profile.dump_stats(str(profile_output_path))
    profile_stats_text = format_profile_stats(discover_profile, top_count=top_profile_functions)
    stage_timings = _build_stage_timings(search_diagnostics)
    known_object_diagnostics = _build_known_object_diagnostics(
        search_diagnostics,
        known_detection_result=known_detection_result,
        discovery_result=discovery_result,
        minimum_matched_frames=int(settings.asteroid_discovery_min_candidate_frames),
        estimated_magnitude_limit=(None if estimate_result is None else float(estimate_result.dimmest_visible_magnitude)),
        frame_count=len(selected_group.frame_paths),
    )

    return DiscoveryBenchmarkReport(
        group_label=selected_group.label,
        frame_count=len(selected_group.frame_paths),
        reference_path=reference_path,
        known_detection_result=known_detection_result,
        estimate_result=estimate_result,
        estimate_error=estimate_error,
        discovery_result=discovery_result,
        generate_seconds=generate_seconds,
        estimate_seconds=estimate_seconds,
        discover_seconds=discover_seconds,
        progress_events=recorder.events,
        profile_stats_text=profile_stats_text,
        stage_timings=stage_timings,
        known_object_diagnostics=known_object_diagnostics,
    )


def _build_stage_timings(search_diagnostics: DiscoverySearchDiagnostics) -> tuple[DiscoveryBenchmarkStageTiming, ...]:
    batches = search_diagnostics.batch_diagnostics
    if not batches:
        return ()
    return (
        DiscoveryBenchmarkStageTiming("prepare", sum(batch.prepare_seconds for batch in batches)),
        DiscoveryBenchmarkStageTiming("residual_scan", sum(batch.residual_scan_seconds for batch in batches)),
        DiscoveryBenchmarkStageTiming("link", sum(batch.link_seconds for batch in batches)),
        DiscoveryBenchmarkStageTiming("synthetic_sweep", sum(batch.synthetic_sweep_seconds for batch in batches)),
    )


def _build_known_object_diagnostics(
    search_diagnostics: DiscoverySearchDiagnostics,
    *,
    known_detection_result: SolarSystemDetectionResult,
    discovery_result: MovingObjectDiscoveryResult,
    minimum_matched_frames: int,
    estimated_magnitude_limit: float | None,
    frame_count: int,
) -> tuple[KnownObjectBenchmarkDiagnostic, ...]:
    frame_context = _frame_context_from_search_diagnostics(search_diagnostics, frame_count=frame_count)
    if frame_context is None:
        return tuple(
            KnownObjectBenchmarkDiagnostic(
                display_name=_detection_display_name(detection),
                status=("recovered" if any(recovered.detection is detection for recovered in discovery_result.recovered_known_objects) else "missed"),
                failure_stage=(None if any(recovered.detection is detection for recovered in discovery_result.recovered_known_objects) else "diagnostics_unavailable"),
                likely_visible=bool(detection.likely_visible),
                within_estimated_limit=_detection_within_estimated_limit(detection, estimated_magnitude_limit),
                residual_frame_matches=0,
                linked_candidate_matches=0,
                detail_text="Benchmark diagnostics could not reconstruct per-frame search context from the captured batches.",
            )
            for detection in known_detection_result.detections
        )
    frame_times, frame_pixel_wcs = frame_context
    prediction_paths = _known_prediction_paths_by_detection(
        list(known_detection_result.detections),
        frame_times=list(frame_times),
        frame_pixel_wcs=list(frame_pixel_wcs),
        known_detection_result=known_detection_result,
    )
    recovered_detection_ids = {id(recovered.detection) for recovered in discovery_result.recovered_known_objects}
    diagnostics: list[KnownObjectBenchmarkDiagnostic] = []
    for detection_index, detection in enumerate(known_detection_result.detections):
        prediction_path = prediction_paths.get(detection_index, ())
        association_radius_px = _known_detection_association_radius_px(detection, known_detection_result.pixel_scale_arcsec_per_pixel)
        residual_frame_indices = _matched_residual_frame_indices(
            search_diagnostics,
            prediction_path=prediction_path,
            association_radius_px=association_radius_px,
        )
        linked_candidate_matches = 0
        if search_diagnostics.aggregated_linked_candidates:
            recovered_when_alone, _missed_when_alone, _unmatched = _associate_candidates_with_known_detections(
                list(search_diagnostics.aggregated_linked_candidates),
                frame_times=list(frame_times),
                frame_pixel_wcs=list(frame_pixel_wcs),
                known_detection_result=known_detection_result,
                candidate_detections=[detection],
                minimum_matched_frames=max(2, int(minimum_matched_frames)),
                estimated_magnitude_limit=estimated_magnitude_limit,
            )
            linked_candidate_matches = len(recovered_when_alone)
        recovered = id(detection) in recovered_detection_ids
        failure_stage: str | None
        if recovered:
            failure_stage = None
            detail_text = f"Recovered in blind search with {linked_candidate_matches} matching linked candidate(s)."
        elif not residual_frame_indices:
            failure_stage = "no_residual_support"
            detail_text = "No residual detections landed within the benchmark association radius of the predicted path in any searched frame."
        elif linked_candidate_matches <= 0:
            failure_stage = "residuals_not_linked"
            detail_text = (
                f"Residual detections matched the predicted path in {len(residual_frame_indices)} frame(s), but no linked blind candidate met the association threshold."
            )
        else:
            failure_stage = "association_conflict_or_gate"
            detail_text = (
                "A linked blind candidate matched this object when scored alone, but it was not retained in the final known-object association pass."
            )
        diagnostics.append(
            KnownObjectBenchmarkDiagnostic(
                display_name=_detection_display_name(detection),
                status=("recovered" if recovered else "missed"),
                failure_stage=failure_stage,
                likely_visible=bool(detection.likely_visible),
                within_estimated_limit=_detection_within_estimated_limit(detection, estimated_magnitude_limit),
                residual_frame_matches=len(residual_frame_indices),
                linked_candidate_matches=linked_candidate_matches,
                detail_text=detail_text,
            )
        )
    return tuple(diagnostics)


def _frame_context_from_search_diagnostics(
    search_diagnostics: DiscoverySearchDiagnostics,
    *,
    frame_count: int,
) -> tuple[tuple[datetime, ...], tuple[object, ...]] | None:
    frame_times_by_index: list[datetime | None] = [None] * frame_count
    frame_wcs_by_index: list[object | None] = [None] * frame_count
    for batch in search_diagnostics.batch_diagnostics:
        for local_frame_index, global_frame_index in enumerate(batch.frame_indices):
            if global_frame_index < 0 or global_frame_index >= frame_count:
                continue
            if local_frame_index < len(batch.frame_times) and frame_times_by_index[global_frame_index] is None:
                frame_times_by_index[global_frame_index] = batch.frame_times[local_frame_index]
            if local_frame_index < len(batch.frame_pixel_wcs) and frame_wcs_by_index[global_frame_index] is None:
                frame_wcs_by_index[global_frame_index] = batch.frame_pixel_wcs[local_frame_index]
    if any(item is None for item in frame_times_by_index) or any(item is None for item in frame_wcs_by_index):
        return None
    return (
        tuple(item for item in frame_times_by_index if item is not None),
        tuple(item for item in frame_wcs_by_index if item is not None),
    )


def _matched_residual_frame_indices(
    search_diagnostics: DiscoverySearchDiagnostics,
    *,
    prediction_path: tuple[tuple[float, float] | None, ...],
    association_radius_px: float,
) -> tuple[int, ...]:
    matched_indices: set[int] = set()
    for batch in search_diagnostics.batch_diagnostics:
        for local_frame_index, residual_detections in enumerate(batch.residual_detections_by_frame):
            if local_frame_index >= len(batch.frame_indices):
                continue
            global_frame_index = batch.frame_indices[local_frame_index]
            if global_frame_index < 0 or global_frame_index >= len(prediction_path):
                continue
            predicted_position = prediction_path[global_frame_index]
            if predicted_position is None:
                continue
            predicted_x, predicted_y = predicted_position
            if any((((residual.x - predicted_x) ** 2) + ((residual.y - predicted_y) ** 2)) ** 0.5 <= association_radius_px for residual in residual_detections):
                matched_indices.add(global_frame_index)
    return tuple(sorted(matched_indices))


def _detection_display_name(detection: SolarSystemDetection) -> str:
    return detection.name or detection.designation or "Unknown"


def _scan_result_sort_key(result: FileScanResult) -> tuple[datetime, str]:
    return result.metadata.date_obs or _MIN_UTC_DATETIME, result.path.name.lower()


def _exposure_label(exposure_seconds: float | None) -> str:
    return "Unknown" if exposure_seconds is None else f"{float(exposure_seconds):.1f} s"


def _exposure_matches(first_value: float | None, second_value: float | None) -> bool:
    if first_value is None or second_value is None:
        return first_value is None and second_value is None
    return abs(float(first_value) - float(second_value)) <= 1e-6