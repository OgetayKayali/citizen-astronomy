from __future__ import annotations



from concurrent.futures import ThreadPoolExecutor, as_completed

from collections.abc import Callable

from dataclasses import dataclass, fields, is_dataclass
from enum import Enum

import json

import os

from datetime import datetime, UTC

from hashlib import sha256

from pathlib import Path

import numpy as np



from astropy.io.fits import Header



from photometry_app.core.catalogs import CatalogService

from photometry_app.core.catalog_filters import filter_variable_stars, format_designation_family_labels

from photometry_app.core.exporters import ScienceExportMetadata, export_processing_report, preview_aavso_extended_export

from photometry_app.core.image_io import read_header_and_shape

from photometry_app.core.matching import apply_differential_photometry, apply_measurement_quality_analysis, build_light_curve_series, select_reference_stars

from photometry_app.core.models import (

    CatalogStar,

    FieldCatalog,

    FileScanResult,

    LightCurvePoint,

    LightCurveSeries,

    ManualPhotometryConfig,

    ManualSourceConfig,

    ManualSourceRole,

    ObjectPhotometryMode,

    PhotometryMeasurement,

    PlateSolveResult,

    ProcessingReport,

    RecenterMode,

    RunHistoryEntry,

    ScanReport,

    VariableSelectionPreview,

    VariableStarDesignationFamily,

    VariableStarLimitMode,

    WcsStatus,

)

from photometry_app.core.photometry import measure_manual_sources, measure_targets, resolve_aperture_profile

from photometry_app.core.scanner import scan_fits_tree

from photometry_app.core.settings import AppSettings, resolve_shared_parallel_workers

from photometry_app.core.solar_system import estimate_visible_magnitude_limit

from photometry_app.core.local_wcs import infer_metadata_wcs_seed, solve_wcs_from_metadata_and_gaia

from photometry_app.core.wcs import AstrometryNetClient, AstrometrySolveHints, extract_solved_field, infer_astrometry_solve_hints, is_pixinsight_staralignment_output, validate_wcs





_DEFAULT_ASTROMETRY_PARALLEL_SUBMISSIONS = 3

_DEFAULT_PHOTOMETRY_AUTO_MAX_WORKERS = 8

_PROCESSING_REPORT_CACHE_MISS_REASON = "Cache miss: workflow files or settings changed since the cached light-curve results were written."

_AAVSO_ANALYZE_BEST_MIN_PEAK_ABOVE_SKY_ADU = 1000.0

_AAVSO_ANALYZE_BEST_MAX_SATURATION_FRACTION = 0.80





@dataclass(slots=True)

class _PendingAstrometrySolve:

    index: int

    file_result: FileScanResult

    initial_reasons: list[str]

    hints: AstrometrySolveHints | None

    reuse_key: tuple[str, int, int, str, float | None, float | None] | None = None

    try_local_gaia: bool = False





@dataclass(slots=True)

class _PendingPhotometryTask:

    index: int

    file_result: FileScanResult

    solve_result: PlateSolveResult





@dataclass(slots=True)

class _ComputedPhotometryTask:

    index: int

    measurements: list[PhotometryMeasurement]

    note: str | None = None





def science_export_metadata_from_settings(settings: AppSettings) -> ScienceExportMetadata:

    return ScienceExportMetadata(

        observer_code=settings.observer_code,

        observer_name=settings.observer_name,

        organization=settings.organization,

        site_name=settings.site_name,

        observing_site_latitude_deg=settings.observing_site_latitude_deg,

        observing_site_longitude_deg=settings.observing_site_longitude_deg,

        observing_site_elevation_m=settings.observing_site_elevation_m,

        telescope=settings.telescope,

        camera=settings.camera,

        filter_system=settings.filter_system,

        aavso_chart_id=settings.aavso_chart_id,

        time_standard=settings.time_standard,

        transformed=settings.transformed,

        reduction_notes=settings.reduction_notes,

    )





class PhotometryPipeline:

    def scan_workspace(self, root_path: Path) -> ScanReport:

        settings = AppSettings.from_root(root_path)

        return scan_fits_tree(root_path, observation_timezone=settings.observation_timezone)



    def load_settings(self, root_path: Path) -> AppSettings:

        return AppSettings.from_root(root_path)



    def save_settings(self, root_path: Path, settings: AppSettings) -> None:

        settings.save(root_path)



    def clear_catalog_cache(self, root_path: Path) -> int:

        settings = AppSettings.from_root(root_path)

        catalog_service = CatalogService(settings.cache_dir / "catalogs")

        return catalog_service.clear_cache()



    def clear_object_catalog_cache(self, root_path: Path, object_name: str) -> int:

        settings = AppSettings.from_root(root_path)

        report = self.scan_workspace(root_path)

        summary = next((item for item in report.object_summaries if item.object_name == object_name), None)

        if summary is None:

            raise ValueError(f"Object folder '{object_name}' was not found under Files.")



        representative_field = None

        for file_result in summary.files:

            header, width, height = _read_header(file_result.path)

            valid_wcs, _ = validate_wcs(header, file_result.path)

            if not valid_wcs:

                continue

            representative_field = extract_solved_field(header, width, height, file_result.path)

            if representative_field is not None:

                break



        if representative_field is None:

            return 0



        catalog_service = CatalogService(settings.cache_dir / "catalogs")

        return catalog_service.clear_field_cache(representative_field)



    def load_run_history(self, root_path: Path, object_name: str) -> list[RunHistoryEntry]:

        settings = AppSettings.from_root(root_path)

        run_log_path = settings.cache_dir / "runs" / f"{object_name}.jsonl"

        if not run_log_path.exists():

            return []



        entries: list[RunHistoryEntry] = []

        for line in run_log_path.read_text(encoding="utf-8").splitlines():

            line = line.strip()

            if not line:

                continue

            try:

                payload = json.loads(line)

                entries.append(

                    RunHistoryEntry(

                        timestamp_utc=datetime.fromisoformat(str(payload["timestamp_utc"])),

                        object_name=str(payload["object_name"]),

                        files_processed=int(payload["files_processed"]),

                        solved_files=int(payload["solved_files"]),

                        measurement_count=int(payload["measurement_count"]),

                        light_curve_count=int(payload["light_curve_count"]),

                        notes=[str(item) for item in payload.get("notes", [])],

                    )

                )

            except (json.JSONDecodeError, KeyError, TypeError, ValueError):

                continue



        return sorted(entries, key=lambda item: item.timestamp_utc, reverse=True)



    def load_cached_processing_report(

        self,

        root_path: Path,

        object_name: str,

        *,

        summary_files: list[FileScanResult] | None = None,

        manual_config_override: ManualPhotometryConfig | None = None,

        settings_override: AppSettings | None = None,

        analyze_best_targets: bool = False,

    ) -> tuple[ProcessingReport | None, str | None]:

        settings = settings_override if settings_override is not None else AppSettings.from_root(root_path)

        cache_path = settings.cache_dir / "processed" / f"{object_name}.json"

        if not cache_path.exists():

            return None, None

        if summary_files is None:

            scan_report = self.scan_workspace(root_path)

            summary = next((item for item in scan_report.object_summaries if item.object_name == object_name), None)

            if summary is None:

                return None, _PROCESSING_REPORT_CACHE_MISS_REASON

            summary_files = list(summary.files)

        manual_config = manual_config_override if manual_config_override is not None else self._manual_config_for_object(settings, object_name)

        current_inputs = _processing_report_cache_inputs(summary_files, settings, object_name, manual_config, analyze_best_targets=analyze_best_targets)

        try:

            payload = json.loads(cache_path.read_text(encoding="utf-8"))

        except (OSError, json.JSONDecodeError):

            return None, "Ignored corrupt cached light-curve results."

        if not isinstance(payload, dict):

            return None, "Ignored corrupt cached light-curve results."

        if payload.get("cache_key") != _processing_report_cache_key_from_inputs(current_inputs):

            return None, _PROCESSING_REPORT_CACHE_MISS_REASON

        try:

            report = _deserialize_processing_report_cache_value(payload.get("report"))

        except (TypeError, ValueError):

            return None, "Ignored corrupt cached light-curve results."

        if not isinstance(report, ProcessingReport):

            return None, "Ignored corrupt cached light-curve results."

        requested_manual_mode = _manual_config_ready_for_processing(manual_config)

        if not requested_manual_mode and (
            report.photometry_mode == ObjectPhotometryMode.MANUAL
            or (report.manual_config is not None and report.manual_config.mode == ObjectPhotometryMode.MANUAL)
        ):

            return None, "Ignored cached manual-only light-curve results because automatic processing is active."

        return report, "Reused cached light-curve results."



    def save_cached_processing_report(

        self,

        root_path: Path,

        report: ProcessingReport,

        *,

        summary_files: list[FileScanResult] | None = None,

        manual_config_override: ManualPhotometryConfig | None = None,

        settings_override: AppSettings | None = None,

        analyze_best_targets: bool = False,

    ) -> None:

        if not summary_files:

            return

        settings = settings_override if settings_override is not None else AppSettings.from_root(root_path)

        cache_dir = settings.cache_dir / "processed"

        cache_dir.mkdir(parents=True, exist_ok=True)

        manual_config = manual_config_override if manual_config_override is not None else report.manual_config

        cache_inputs = _processing_report_cache_inputs(summary_files, settings, report.object_name, manual_config, analyze_best_targets=analyze_best_targets)

        payload = {

            "cached_at_utc": datetime.now(UTC).isoformat(),

            "object_name": report.object_name,

            "cache_inputs": cache_inputs,

            "cache_key": _processing_report_cache_key_from_inputs(cache_inputs),

            "report": _serialize_processing_report_cache_value(report),

        }

        cache_path = cache_dir / f"{report.object_name}.json"

        cache_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")



    def preview_variable_selection(

        self,

        root_path: Path,

        object_name: str,

        progress_callback: Callable[[str], None] | None = None,

    ) -> VariableSelectionPreview:

        settings = AppSettings.from_root(root_path)

        report = self.scan_workspace(root_path)

        summary = next((item for item in report.object_summaries if item.object_name == object_name), None)

        if summary is None:

            raise ValueError(f"Object folder '{object_name}' was not found under Files.")



        manual_config = self._manual_config_for_object(settings, object_name)

        if _manual_config_ready_for_processing(manual_config):

            notes = [

                f"Manual mode is enabled for {object_name}.",

                f"Saved manual sources: {len(manual_config.sources)} total, {len(manual_config.comparison_sources)} comparison star(s).",

            ]

            if manual_config.reference_frame_name:

                notes.append(f"Reference frame: {manual_config.reference_frame_name}.")

            return VariableSelectionPreview(

                object_name=object_name,

                total_variable_stars_found=len(manual_config.measured_sources),

                analyzed_variable_star_count=len(manual_config.measured_sources),

                variable_star_limit_mode=settings.variable_star_limit_mode,

                variable_star_limit_value=settings.variable_star_limit_value,

                variable_star_designation_filters=settings.variable_star_designation_filters,

                candidate_sources=_manual_catalog_entries(manual_config, ManualSourceRole.TARGET, ManualSourceRole.CHECK),

                preselected_source_keys=[

                    _catalog_source_key(item)

                    for item in _manual_catalog_entries(manual_config, ManualSourceRole.TARGET, ManualSourceRole.CHECK)

                ],

                notes=notes,

            )



        _emit_progress(progress_callback, f"Preparing preview for {object_name}.")

        solved_results = self._resolve_summary_fields(

            summary.files,

            settings,

            progress_callback=progress_callback,

        )

        representative_field = next((result.solved_field for _, result in solved_results if result.solved_field is not None), None)

        if representative_field is None:

            return VariableSelectionPreview(

                object_name=object_name,

                total_variable_stars_found=0,

                analyzed_variable_star_count=0,

                variable_star_limit_mode=settings.variable_star_limit_mode,

                variable_star_limit_value=settings.variable_star_limit_value,

                variable_star_designation_filters=settings.variable_star_designation_filters,

                candidate_sources=[],

                preselected_source_keys=[],

                notes=["No solved WCS field is available yet, so the variable-star preview could not be determined."],

            )



        _emit_progress(

            progress_callback,

            "Representative solved field is ready. Catalog lookup and preview-source preparation are still running; the selection window will open automatically when ready.",

        )



        catalog_service = CatalogService(settings.cache_dir / "catalogs")

        field_catalog = self._best_field_catalog_for_solved_results(catalog_service, solved_results, progress_callback)

        total_variable_stars_found = len(field_catalog.variable_stars)

        eligible_variable_stars = filter_variable_stars(field_catalog.variable_stars, settings.variable_star_designation_filters)

        _emit_progress(

            progress_callback,

            f"Preparing the preview source list from {len(eligible_variable_stars)} designation-matched variable star(s). Large fields can take a while before the selection window appears.",

        )

        limited_preview_variable_stars = _limit_preview_variable_stars(

            eligible_variable_stars,

            settings.preview_variable_star_max_count,

            settings.preview_variable_star_min_magnitude,

            settings.preview_variable_star_max_magnitude,

        )

        saved_source_keys = set((settings.selected_catalog_source_ids or {}).get(object_name, []))

        selected_preview_variable_stars = [

            entry for entry in eligible_variable_stars if _catalog_source_key(entry) in saved_source_keys

        ]

        limited_preview_source_keys = {_catalog_source_key(entry) for entry in limited_preview_variable_stars}

        saved_outside_preview_variable_stars = [

            entry for entry in selected_preview_variable_stars if _catalog_source_key(entry) not in limited_preview_source_keys

        ]

        preview_candidate_variable_stars = _merge_catalog_entries(

            limited_preview_variable_stars,

            selected_preview_variable_stars,

        )

        selected_preview_exoplanets = [

            entry for entry in field_catalog.exoplanets if _catalog_source_key(entry) in saved_source_keys

        ]

        if selected_preview_variable_stars or selected_preview_exoplanets:

            analyzed_count = len(selected_preview_variable_stars)

            preselected_source_keys = [

                _catalog_source_key(item) for item in [*selected_preview_variable_stars, *selected_preview_exoplanets]

            ]

        else:

            default_preview_variable_stars = _select_brightest_variable_stars(

                preview_candidate_variable_stars,

                settings.variable_star_limit_mode,

                settings.variable_star_limit_value,

            )

            analyzed_count = len(default_preview_variable_stars)

            preselected_source_keys = [_catalog_source_key(item) for item in default_preview_variable_stars]

        notes: list[str] = []

        if len(eligible_variable_stars) != total_variable_stars_found:

            notes.append(

                f"Designation filter kept {len(eligible_variable_stars)} of {total_variable_stars_found} variable stars using {format_designation_family_labels(settings.variable_star_designation_filters)}."

            )

        preview_limit_note = _preview_limit_note(

            eligible_variable_stars,

            limited_preview_variable_stars,

            settings.preview_variable_star_max_count,

            settings.preview_variable_star_min_magnitude,

            settings.preview_variable_star_max_magnitude,

        )

        if preview_limit_note is not None:

            notes.append(preview_limit_note)

        if saved_outside_preview_variable_stars:

            notes.append(

                f"Preview list also retained {len(saved_outside_preview_variable_stars)} explicitly saved variable star(s) outside the preview cap."

            )

        _emit_progress(

            progress_callback,

            f"Preview complete: found {total_variable_stars_found} variable star(s); showing {len(preview_candidate_variable_stars)} variable candidate(s) in the selection list; current setting will analyze {analyzed_count} by default.",

        )

        return VariableSelectionPreview(

            object_name=object_name,

            total_variable_stars_found=total_variable_stars_found,

            analyzed_variable_star_count=analyzed_count,

            variable_star_limit_mode=settings.variable_star_limit_mode,

            variable_star_limit_value=settings.variable_star_limit_value,

            variable_star_designation_filters=settings.variable_star_designation_filters,

            candidate_sources=sorted(

                [*preview_candidate_variable_stars, *field_catalog.exoplanets],

                key=lambda item: (0 if item.object_type != "exoplanet" else 1, item.name.lower()),

            ),

            preselected_source_keys=preselected_source_keys,

            notes=notes,

        )



    def process_object(

        self,

        root_path: Path,

        object_name: str,

        manual_config_override: ManualPhotometryConfig | None = None,

        settings_override: AppSettings | None = None,

        analyze_best_targets: bool = False,

        progress_callback: Callable[[str], None] | None = None,

    ) -> ProcessingReport:

        settings = settings_override if settings_override is not None else AppSettings.from_root(root_path)

        report = self.scan_workspace(root_path)

        summary = next((item for item in report.object_summaries if item.object_name == object_name), None)

        if summary is None:

            raise ValueError(f"Object folder '{object_name}' was not found under Files.")



        _emit_progress(progress_callback, f"Starting processing for {object_name}.")

        _emit_progress(progress_callback, f"Found {len(summary.files)} file(s) queued for analysis.")



        catalog_cache_dir = settings.cache_dir / "catalogs"

        catalog_service = CatalogService(catalog_cache_dir)

        measurement_cache_dir = settings.cache_dir / "measurements" / summary.object_name

        run_log_path = settings.cache_dir / "runs" / f"{summary.object_name}.jsonl"

        manual_config = manual_config_override if manual_config_override is not None else self._manual_config_for_object(settings, object_name)

        using_manual_mode = _manual_config_ready_for_processing(manual_config)

        auto_manual_config = manual_config if _manual_config_ready_for_auto_append(manual_config) else None

        photometry_settings_signature = _photometry_settings_signature(
            settings,
            manual_config if using_manual_mode or auto_manual_config is not None else None,
        )



        solved_results = self._resolve_summary_fields(summary.files, settings, progress_callback)

        notes: list[str] = []

        file_notes: dict[str, list[str]] = {}



        if manual_config is not None and manual_config.mode == ObjectPhotometryMode.MANUAL and not using_manual_mode:

            notes.append(

                "Ignored incomplete manual aperture setup and continued with automatic catalog-based processing. Save at least one comparison star before reprocessing in Manual mode."

            )

            _emit_progress(

                progress_callback,

                "Ignored incomplete manual aperture setup; resuming automatic catalog-based processing until at least one comparison star is saved.",

            )



        for file_result, plate_solve_result in solved_results:

            notes.extend(plate_solve_result.reasons)

            file_notes[file_result.path.name] = list(plate_solve_result.reasons)



        representative_field = next((result.solved_field for _, result in solved_results if result.solved_field is not None), None)

        if representative_field is None:

            notes.append("No solved WCS field is available for this object, so catalog and photometry steps were skipped.")

            _emit_progress(progress_callback, "No solved WCS field is available, so catalog lookup and photometry were skipped.")

            return ProcessingReport(

                object_name=summary.object_name,

                files_processed=0,

                solved_files=0,

                field_catalog=None,

                total_variable_stars_found=0,

                variable_star_limit_mode=settings.variable_star_limit_mode,

                variable_star_limit_value=settings.variable_star_limit_value,

                variable_star_designation_filters=settings.variable_star_designation_filters,

                notes=_deduplicate(notes),

            )



        _emit_progress(progress_callback, "Querying Gaia DR3 and VSX catalogs for the representative solved field.")

        try:

            field_catalog = self._best_field_catalog_for_solved_results(catalog_service, solved_results, progress_callback)

        except Exception as exc:

            notes.append(f"Catalog lookup failed: {exc}")

            _emit_progress(progress_callback, f"Catalog lookup failed: {exc}")

            processing_report = ProcessingReport(

                object_name=summary.object_name,

                files_processed=len(summary.files),

                solved_files=sum(result.status == WcsStatus.SOLVED for _, result in solved_results),

                field_catalog=None,

                total_variable_stars_found=0,

                variable_star_limit_mode=settings.variable_star_limit_mode,

                variable_star_limit_value=settings.variable_star_limit_value,

                variable_star_designation_filters=settings.variable_star_designation_filters,

                notes=_deduplicate(notes),

                file_notes={key: _deduplicate(value) for key, value in file_notes.items()},

            )

            self._append_run_log(run_log_path, processing_report)

            return processing_report



        total_variable_stars_found = len(field_catalog.variable_stars)

        eligible_variable_stars = filter_variable_stars(field_catalog.variable_stars, settings.variable_star_designation_filters)

        explicit_source_keys = set((settings.selected_catalog_source_ids or {}).get(object_name, []))

        selected_variable_stars = [entry for entry in eligible_variable_stars if _catalog_source_key(entry) in explicit_source_keys]

        selected_exoplanets = [entry for entry in field_catalog.exoplanets if _catalog_source_key(entry) in explicit_source_keys]

        using_explicit_source_selection = bool(selected_variable_stars or selected_exoplanets)

        if not using_explicit_source_selection:

            if analyze_best_targets:

                representative_file_result, representative_solve_result = next(
                    (item for item in solved_results if item[1].solved_field is not None),
                    (None, None),
                )

                if representative_file_result is not None and representative_solve_result is not None:

                    selected_variable_stars, analyze_best_notes = _select_aavso_recommended_variable_stars(
                        eligible_variable_stars,
                        file_result=representative_file_result,
                        solve_result=representative_solve_result,
                        settings=settings,
                        catalog_service=catalog_service,
                        progress_callback=progress_callback,
                    )

                    for analyze_best_note in analyze_best_notes:

                        notes.append(analyze_best_note)

                        _emit_progress(progress_callback, analyze_best_note)

                else:

                    selected_variable_stars = []

            else:

                selected_variable_stars = _select_brightest_variable_stars(

                    eligible_variable_stars,

                    settings.variable_star_limit_mode,

                    settings.variable_star_limit_value,

                )

            selected_exoplanets = list(field_catalog.exoplanets)

        selected_field_catalog = FieldCatalog(

            center_ra_deg=field_catalog.center_ra_deg,

            center_dec_deg=field_catalog.center_dec_deg,

            radius_deg=field_catalog.radius_deg,

            gaia_stars=field_catalog.gaia_stars,

            variable_stars=selected_variable_stars,

            exoplanets=selected_exoplanets,

        )

        automatic_variable_stars = list(selected_variable_stars)

        manual_variable_entries: list[CatalogStar] = []

        manual_reference_entries: list[CatalogStar] = []

        if using_manual_mode and manual_config is not None:

            notes.append(

                f"Manual mode measured {len(manual_config.measured_sources)} saved source(s) using {len(manual_config.comparison_sources)} comparison star(s)."

            )

            selected_field_catalog = FieldCatalog(

                center_ra_deg=field_catalog.center_ra_deg,

                center_dec_deg=field_catalog.center_dec_deg,

                radius_deg=field_catalog.radius_deg,

                gaia_stars=field_catalog.gaia_stars,

                variable_stars=_manual_catalog_entries(manual_config, ManualSourceRole.TARGET, ManualSourceRole.CHECK),

                exoplanets=field_catalog.exoplanets,

            )

        elif auto_manual_config is not None:

            manual_variable_entries = _manual_catalog_entries(auto_manual_config, ManualSourceRole.TARGET, ManualSourceRole.CHECK)

            manual_reference_entries = _manual_catalog_entries(auto_manual_config, ManualSourceRole.COMPARISON)

            selected_field_catalog = FieldCatalog(

                center_ra_deg=field_catalog.center_ra_deg,

                center_dec_deg=field_catalog.center_dec_deg,

                radius_deg=field_catalog.radius_deg,

                gaia_stars=field_catalog.gaia_stars,

                variable_stars=[*automatic_variable_stars, *manual_variable_entries],

                exoplanets=selected_exoplanets,

            )

            notes.append(

                f"Manual aperture selections added {len(auto_manual_config.measured_sources)} saved source(s) to automatic Source Results using {len(auto_manual_config.comparison_sources)} comparison star(s)."

            )

        _emit_progress(

            progress_callback,

            f"Catalog lookup complete: {len(field_catalog.gaia_stars)} Gaia stars, {total_variable_stars_found} variable stars, and {len(field_catalog.exoplanets)} exoplanet entries found.",

        )

        if len(eligible_variable_stars) != total_variable_stars_found:

            filter_note = (

                f"Designation filter kept {len(eligible_variable_stars)} of {total_variable_stars_found} variable stars "

                f"using {format_designation_family_labels(settings.variable_star_designation_filters)}."

            )

            notes.append(filter_note)

            _emit_progress(progress_callback, filter_note)

        if len(selected_variable_stars) != len(eligible_variable_stars):

            selection_label = "Analyze Best AAVSO ADU range" if analyze_best_targets and not using_explicit_source_selection else _selection_label(settings.variable_star_limit_mode, settings.variable_star_limit_value)

            selection_note = (

                f"Analyzing {len(selected_variable_stars)} of {len(eligible_variable_stars)} designation-matched variable stars "

                f"using {selection_label}."

            )

            notes.append(selection_note)

            _emit_progress(progress_callback, selection_note)

        if using_explicit_source_selection:

            explicit_selection_note = (

                f"Using {len(selected_variable_stars)} explicitly selected variable star(s) and {len(selected_exoplanets)} explicitly selected exoplanet entry/entries."

            )

            notes.append(explicit_selection_note)

            _emit_progress(progress_callback, explicit_selection_note)



        if not eligible_variable_stars and not using_manual_mode and auto_manual_config is None:

            if total_variable_stars_found == 0:

                empty_note = "No known VSX variable stars were found in the solved field."

            else:

                empty_note = "No variable stars matched the current designation filter in the solved field."

            notes.append(empty_note)

            _emit_progress(progress_callback, empty_note)

            processing_report = ProcessingReport(

                object_name=summary.object_name,

                files_processed=len(summary.files),

                solved_files=sum(result.status == WcsStatus.SOLVED for _, result in solved_results),

                field_catalog=selected_field_catalog,

                total_variable_stars_found=total_variable_stars_found,

                variable_star_limit_mode=settings.variable_star_limit_mode,

                variable_star_limit_value=settings.variable_star_limit_value,

                variable_star_designation_filters=settings.variable_star_designation_filters,

                notes=_deduplicate(notes),

                file_notes={key: _deduplicate(value) for key, value in file_notes.items()},

            )

            self._append_run_log(run_log_path, processing_report)

            return processing_report



        if using_manual_mode and manual_config is not None:

            reference_stars = _manual_catalog_entries(manual_config, ManualSourceRole.COMPARISON)

            photometry_variable_stars = selected_field_catalog.variable_stars

            photometry_reference_stars = reference_stars

            _emit_progress(

                progress_callback,

                f"Manual mode active: using {len(reference_stars)} saved comparison star(s) for differential photometry.",

            )

        else:

            automatic_reference_stars = select_reference_stars(

                field_catalog.gaia_stars,

                field_catalog.variable_stars,

                minimum_magnitude=settings.reference_star_min_magnitude,

                maximum_magnitude=settings.reference_star_max_magnitude,

            )

            reference_stars = [*automatic_reference_stars, *manual_reference_entries]

            photometry_variable_stars = automatic_variable_stars

            photometry_reference_stars = automatic_reference_stars

            _emit_progress(

                progress_callback,

                f"Selected {len(automatic_reference_stars)} Gaia reference star(s) for differential photometry; using up to {settings.nearby_reference_count} nearby comparison star(s) per variable.",

            )

            if not automatic_reference_stars:

                notes.append("No suitable Gaia reference stars were selected; differential magnitudes may be unavailable.")

                _emit_progress(progress_callback, "No suitable Gaia reference stars were selected; differential magnitudes may be unavailable.")



        measurements = []

        measurable_files = [(file_result, solve_result) for file_result, solve_result in solved_results if solve_result.solved_field is not None]

        variable_source_ids = [item.source_id for item in selected_field_catalog.variable_stars]

        reference_source_ids = [item.source_id for item in reference_stars]

        ordered_measurements: list[list[PhotometryMeasurement] | None] = [None] * len(measurable_files)

        pending_photometry_tasks: list[_PendingPhotometryTask] = []

        for index, (file_result, solve_result) in enumerate(measurable_files, start=1):

            _emit_progress(progress_callback, f"[Photometry {index}/{len(measurable_files)}] Preparing {file_result.path.name}.")

            if solve_result.solved_field is None:

                continue

            cached_measurements, cache_note = self._load_cached_measurements(

                cache_dir=measurement_cache_dir,

                file_result=file_result,

                solve_result=solve_result,

                variable_source_ids=variable_source_ids,

                reference_source_ids=reference_source_ids,

                photometry_settings_signature=photometry_settings_signature,

            )

            if cache_note:

                file_notes[file_result.path.name] = [*file_notes.get(file_result.path.name, []), cache_note]

                _emit_progress(progress_callback, f"[Photometry {index}/{len(measurable_files)}] {cache_note}")

            if cached_measurements is not None:

                ordered_measurements[index - 1] = cached_measurements

                file_notes[file_result.path.name] = [*file_notes.get(file_result.path.name, []), "Reused cached photometry measurements."]

                _emit_progress(

                    progress_callback,

                    f"[Photometry {index}/{len(measurable_files)}] Reused cached measurements for {file_result.path.name} ({len(cached_measurements)} rows).",

                )

                continue



            pending_photometry_tasks.append(

                _PendingPhotometryTask(

                    index=index - 1,

                    file_result=file_result,

                    solve_result=solve_result,

                )

            )



        if pending_photometry_tasks:

            configured_workers, worker_label = _resolve_photometry_parallel_workers(settings)

            _emit_progress(

                progress_callback,

                f"Submitting {len(pending_photometry_tasks)} photometry file(s) with {min(configured_workers, len(pending_photometry_tasks))} worker(s) ({worker_label}).",

            )

            max_workers = min(configured_workers, len(pending_photometry_tasks))

            with ThreadPoolExecutor(max_workers=max_workers) as executor:

                future_map = {

                    executor.submit(

                        self._compute_photometry_for_file,

                        task,

                        using_manual_mode,

                        manual_config,

                        photometry_variable_stars,

                        photometry_reference_stars,

                        settings,

                        auto_manual_config,

                    ): task

                    for task in pending_photometry_tasks

                }

                for future in as_completed(future_map):

                    task = future_map[future]

                    result = future.result()

                    ordered_measurements[result.index] = result.measurements

                    if result.note:

                        file_notes[task.file_result.path.name] = [*file_notes.get(task.file_result.path.name, []), result.note]

                        _emit_progress(progress_callback, f"[Photometry {task.index + 1}/{len(measurable_files)}] {result.note}")

                    _emit_progress(

                        progress_callback,

                        f"[Photometry {task.index + 1}/{len(measurable_files)}] Measured {len(result.measurements)} source rows in {task.file_result.path.name}.",

                    )



            for task in pending_photometry_tasks:

                computed_measurements = ordered_measurements[task.index] or []

                self._store_cached_measurements(

                    cache_dir=measurement_cache_dir,

                    file_result=task.file_result,

                    solve_result=task.solve_result,

                    variable_source_ids=variable_source_ids,

                    reference_source_ids=reference_source_ids,

                    photometry_settings_signature=photometry_settings_signature,

                    measurements=computed_measurements,

                )



        for per_file_measurements in ordered_measurements:

            if per_file_measurements:

                measurements.extend(per_file_measurements)



        if settings.saturation_filter_enabled:

            saturated_source_ids = {

                measurement.source_id

                for measurement in measurements

                if measurement.is_variable and measurement.is_saturated

            }

            if saturated_source_ids:

                saturated_name_by_id = {

                    measurement.source_id: measurement.source_name

                    for measurement in measurements

                    if measurement.source_id in saturated_source_ids

                }

                for measurement in measurements:

                    if measurement.source_id not in saturated_source_ids or not measurement.is_saturated:

                        continue

                    file_notes[measurement.file_path.name] = [

                        *file_notes.get(measurement.file_path.name, []),

                        f"Skipped {measurement.source_name} from analysis because it was saturated.",

                    ]

                skipped_names = [saturated_name_by_id[source_id] for source_id in sorted(saturated_source_ids)]

                notes.append(

                    f"Skipped {len(skipped_names)} selected variable star(s) from analysis because they were saturated: {', '.join(skipped_names)}."

                )

                _emit_progress(

                    progress_callback,

                    f"Skipping {len(skipped_names)} selected variable star(s) because saturation was detected: {', '.join(skipped_names)}.",

                )

                selected_field_catalog = FieldCatalog(

                    center_ra_deg=selected_field_catalog.center_ra_deg,

                    center_dec_deg=selected_field_catalog.center_dec_deg,

                    radius_deg=selected_field_catalog.radius_deg,

                    gaia_stars=selected_field_catalog.gaia_stars,

                    variable_stars=[

                        entry for entry in selected_field_catalog.variable_stars if entry.source_id not in saturated_source_ids

                    ],

                    exoplanets=selected_field_catalog.exoplanets,

                )

                measurements = [

                    measurement

                    for measurement in measurements

                    if not (measurement.is_variable and measurement.source_id in saturated_source_ids)

                ]



        _emit_progress(progress_callback, f"Applying differential photometry to {len(measurements)} measurement row(s).")

        measurements = apply_differential_photometry(measurements, nearby_reference_count=settings.nearby_reference_count)

        _emit_progress(progress_callback, "Scoring measurement quality and marking low-quality points for the cleaned view.")

        measurements = apply_measurement_quality_analysis(measurements)

        excluded_count = sum(1 for measurement in measurements if measurement.excluded_from_analysis)

        if excluded_count:

            notes.append(f"Quality analysis marked {excluded_count} measurement row(s) as excluded in the cleaned view.")

        _emit_progress(progress_callback, "Building per-filter light curves.")

        light_curves = build_light_curve_series(measurements)

        _emit_progress(progress_callback, f"Completed processing: {len(light_curves)} light curve(s) generated.")



        processing_report = ProcessingReport(

            object_name=summary.object_name,

            files_processed=len(summary.files),

            solved_files=sum(result.status == WcsStatus.SOLVED for _, result in solved_results),

            field_catalog=selected_field_catalog,

            total_variable_stars_found=total_variable_stars_found,

            variable_star_limit_mode=settings.variable_star_limit_mode,

            variable_star_limit_value=settings.variable_star_limit_value,

            variable_star_designation_filters=settings.variable_star_designation_filters,

            reference_stars=reference_stars,

            measurements=measurements,

            light_curves=light_curves,

            notes=_deduplicate(notes),

            file_notes={key: _deduplicate(value) for key, value in file_notes.items()},

            photometry_mode=ObjectPhotometryMode.MANUAL if using_manual_mode else ObjectPhotometryMode.AUTO,

            manual_config=manual_config if using_manual_mode else auto_manual_config,

        )

        self._append_run_log(run_log_path, processing_report)

        return processing_report



    def _manual_config_for_object(self, settings: AppSettings, object_name: str) -> ManualPhotometryConfig | None:

        manual_configs = settings.manual_photometry_configs or {}

        return manual_configs.get(object_name)



    def _resolve_summary_fields(

        self,

        files: list[FileScanResult],

        settings: AppSettings,

        progress_callback: Callable[[str], None] | None = None,

    ) -> list[tuple[FileScanResult, PlateSolveResult]]:

        solve_cache_dir = settings.cache_dir / "astrometry"

        solved_results: list[tuple[FileScanResult, PlateSolveResult] | None] = [None] * len(files)

        pending_individual: list[_PendingAstrometrySolve] = []

        pending_grouped: dict[tuple[str, int, int, str, float | None, float | None], list[_PendingAstrometrySolve]] = {}

        for index, file_result in enumerate(files, start=1):

            _emit_progress(progress_callback, f"[WCS {index}/{len(files)}] Inspecting {file_result.path.name}.")

            immediate_result, pending_request = self._prepare_wcs_resolution(index - 1, file_result, settings)

            if immediate_result is not None:

                solved_results[index - 1] = (file_result, immediate_result)

                if immediate_result.solved_field is not None:

                    _emit_progress(progress_callback, f"[WCS {index}/{len(files)}] Solved field ready for {file_result.path.name}.")

                else:

                    reason = immediate_result.reasons[-1] if immediate_result.reasons else "No usable celestial WCS was found."

                    _emit_progress(progress_callback, f"[WCS {index}/{len(files)}] Skipped {file_result.path.name}: {reason}")

                continue



            if pending_request is None:

                continue

            if pending_request.reuse_key is None:

                pending_individual.append(pending_request)

            else:

                pending_grouped.setdefault(pending_request.reuse_key, []).append(pending_request)



        if not pending_individual and not pending_grouped:

            return [item for item in solved_results if item is not None]



        representative_requests = [*pending_individual, *(group[0] for group in pending_grouped.values())]

        if any(request.try_local_gaia for request in representative_requests):

            solve_plan = "metadata-seeded Gaia matching first"

            if settings.astrometry_api_key:

                solve_plan += "; astrometry.net only if Gaia cannot recover a WCS"

        elif settings.astrometry_api_key:

            solve_plan = "astrometry.net"

        else:

            solve_plan = "no available WCS fallback"

        _emit_progress(

            progress_callback,

            (
                f"Solving {len(representative_requests)} unresolved field(s) via {solve_plan}, "
                f"using up to {min(_DEFAULT_ASTROMETRY_PARALLEL_SUBMISSIONS, len(representative_requests))} concurrent solve(s)."
            ),

        )

        representative_results = self._solve_pending_requests(

            representative_requests,

            solve_cache_dir,

            settings.astrometry_api_key or "",

            progress_callback,

            len(files),

        )



        for request in pending_individual:

            solved_results[request.index] = (request.file_result, representative_results[request.index])



        fallback_requests: list[_PendingAstrometrySolve] = []

        for grouped_requests in pending_grouped.values():

            representative_request = grouped_requests[0]

            representative_result = representative_results[representative_request.index]

            solved_results[representative_request.index] = (representative_request.file_result, representative_result)

            if representative_result.solved_field is None:

                fallback_requests.extend(grouped_requests[1:])

                continue

            for request in grouped_requests[1:]:

                reused_result = self._reuse_aligned_frame_solution(request, representative_request, representative_result)

                solved_results[request.index] = (request.file_result, reused_result)

                _emit_progress(

                    progress_callback,

                    f"[WCS {request.index + 1}/{len(files)}] Reused solved WCS from aligned frame {representative_request.file_result.path.name} for {request.file_result.path.name}.",

                )



        if fallback_requests:

            _emit_progress(

                progress_callback,

                f"Retrying {len(fallback_requests)} aligned frame(s) individually because the representative solve failed.",

            )

            fallback_results = self._solve_pending_requests(

                fallback_requests,

                solve_cache_dir,

                settings.astrometry_api_key or "",

                progress_callback,

                len(files),

            )

            for request in fallback_requests:

                solved_results[request.index] = (request.file_result, fallback_results[request.index])



        return [item for item in solved_results if item is not None]



    def _prepare_wcs_resolution(

        self,

        index: int,

        file_result: FileScanResult,

        settings: AppSettings,

    ) -> tuple[PlateSolveResult | None, _PendingAstrometrySolve | None]:

        try:

            header, width, height = _read_header(file_result.path)

            valid_wcs, reasons = validate_wcs(header, file_result.path)

            if valid_wcs:

                solved_field = extract_solved_field(header, width, height, file_result.path)

                return (

                    PlateSolveResult(

                        source_path=file_result.path,

                        status=WcsStatus.SOLVED if solved_field else WcsStatus.UNSOLVED,

                        solved_field=solved_field,

                        reasons=reasons,

                    ),

                    None,

                )



            local_seed_available = infer_metadata_wcs_seed(header, width, height) is not None

            if not local_seed_available and not settings.astrometry_api_key:

                return (

                    PlateSolveResult(

                        source_path=file_result.path,

                        status=WcsStatus.UNSOLVED,

                        solved_field=None,

                        reasons=[*reasons, "Astrometry.net API key not configured; unsolved file skipped."],

                    ),

                    None,

                )



            hints = infer_astrometry_solve_hints(header, width, height, file_result.path)

            return (

                None,

                _PendingAstrometrySolve(

                    index=index,

                    file_result=file_result,

                    initial_reasons=list(reasons),

                    hints=hints,

                    reuse_key=_aligned_frame_reuse_key(file_result, hints, settings),

                    try_local_gaia=local_seed_available,

                ),

            )

        except Exception as exc:

            return (

                PlateSolveResult(

                    source_path=file_result.path,

                    status=WcsStatus.UNSOLVED,

                    solved_field=None,

                    reasons=[f"WCS resolution failed: {exc}"],

                ),

                None,

            )



    def _solve_pending_requests(

        self,

        requests_to_solve: list[_PendingAstrometrySolve],

        solve_cache_dir: Path,

        api_key: str,

        progress_callback: Callable[[str], None] | None,

        total_files: int,

    ) -> dict[int, PlateSolveResult]:

        if not requests_to_solve:

            return {}



        max_workers = min(_DEFAULT_ASTROMETRY_PARALLEL_SUBMISSIONS, len(requests_to_solve))

        results: dict[int, PlateSolveResult] = {}

        with ThreadPoolExecutor(max_workers=max_workers) as executor:

            future_map = {

                executor.submit(

                    self._solve_single_pending_request,

                    request,

                    solve_cache_dir,

                    api_key,

                    progress_callback,

                    total_files,

                ): request

                for request in requests_to_solve

            }

            for future in as_completed(future_map):

                request = future_map[future]

                result = future.result()

                results[request.index] = result

                if result.solved_field is not None:

                    if any("metadata-seeded Gaia" in reason for reason in result.reasons):

                        method_text = "via metadata-seeded Gaia matching"

                    elif any("astrometry.net" in reason.casefold() for reason in result.reasons):

                        method_text = "via astrometry.net"

                    else:

                        method_text = "with a recovered WCS"

                    _emit_progress(

                        progress_callback,

                        f"[WCS {request.index + 1}/{total_files}] Solved field ready for {request.file_result.path.name} {method_text}.",

                    )

                else:

                    reason = result.reasons[-1] if result.reasons else "No usable celestial WCS was found."

                    _emit_progress(progress_callback, f"[WCS {request.index + 1}/{total_files}] Skipped {request.file_result.path.name}: {reason}")

        return results



    def _solve_single_pending_request(

        self,

        request: _PendingAstrometrySolve,

        solve_cache_dir: Path,

        api_key: str,

        progress_callback: Callable[[str], None] | None = None,

        total_files: int = 1,

    ) -> PlateSolveResult:

        local_reasons: list[str] = []

        file_label = f"[WCS {request.index + 1}/{max(1, int(total_files))}]"

        file_name = request.file_result.path.name

        if request.try_local_gaia:

            _emit_progress(

                progress_callback,

                f"{file_label} Trying metadata-seeded Gaia matching for {file_name}.",

            )

            def _local_progress(message: str) -> None:

                text = str(message or "").strip()

                if not text:

                    return

                _emit_progress(progress_callback, f"{file_label} {text}")

            try:

                local_result = solve_wcs_from_metadata_and_gaia(

                    request.file_result.path,

                    solve_cache_dir / "local-gaia",

                    progress_callback=_local_progress,

                )

            except Exception as exc:

                local_result = PlateSolveResult(

                    source_path=request.file_result.path,

                    status=WcsStatus.UNSOLVED,

                    solved_field=None,

                    reasons=[f"Metadata-seeded Gaia WCS fallback failed: {exc}"],

                )

            if local_result.solved_field is not None:

                return PlateSolveResult(

                    source_path=request.file_result.path,

                    status=local_result.status,

                    solved_field=local_result.solved_field,

                    reasons=_deduplicate(

                        [

                            *request.initial_reasons,

                            *local_result.reasons,

                            "Recovered WCS via metadata-seeded Gaia matching.",

                        ]

                    ),

                )

            local_reasons.extend(local_result.reasons)

            if api_key:

                _emit_progress(

                    progress_callback,

                    f"{file_label} Gaia matching did not recover a WCS for {file_name}; trying astrometry.net.",

                )

        if not api_key:

            return PlateSolveResult(

                source_path=request.file_result.path,

                status=WcsStatus.UNSOLVED,

                solved_field=None,

                reasons=_deduplicate(

                    [

                        *request.initial_reasons,

                        *local_reasons,

                        "Astrometry.net API key not configured; no further WCS fallback is available.",

                    ]

                ),

            )

        try:

            client = AstrometryNetClient(api_key)

            result = client.solve_file(request.file_result.path, solve_cache_dir, hints=request.hints)

        except Exception as exc:

            return PlateSolveResult(

                source_path=request.file_result.path,

                status=WcsStatus.UNSOLVED,

                solved_field=None,

                reasons=_deduplicate([*request.initial_reasons, *local_reasons, f"WCS resolution failed: {exc}"]),

            )



        return PlateSolveResult(

            source_path=request.file_result.path,

            status=result.status,

            solved_field=result.solved_field,

            reasons=_deduplicate(

                [

                    *request.initial_reasons,

                    *local_reasons,

                    *result.reasons,

                    *(

                        ("Recovered WCS via astrometry.net.",)

                        if result.solved_field is not None

                        else ()

                    ),

                ]

            ),

        )



    def _reuse_aligned_frame_solution(

        self,

        request: _PendingAstrometrySolve,

        representative_request: _PendingAstrometrySolve,

        representative_result: PlateSolveResult,

    ) -> PlateSolveResult:

        solved_field = representative_result.solved_field

        if solved_field is None:

            return representative_result

        reused_field = type(solved_field)(

            center_ra_deg=solved_field.center_ra_deg,

            center_dec_deg=solved_field.center_dec_deg,

            radius_deg=solved_field.radius_deg,

            width=solved_field.width,

            height=solved_field.height,

            wcs_path=solved_field.wcs_path,

        )

        return PlateSolveResult(

            source_path=request.file_result.path,

            status=WcsStatus.SOLVED,

            solved_field=reused_field,

            reasons=_deduplicate(

                [

                    *request.initial_reasons,

                    f"Reused plate solve from aligned frame {representative_request.file_result.path.name}.",

                ]

            ),

        )



    def _compute_photometry_for_file(

        self,

        task: _PendingPhotometryTask,

        using_manual_mode: bool,

        manual_config: ManualPhotometryConfig | None,

        variable_stars: list[CatalogStar],

        reference_stars: list[CatalogStar],

        settings: AppSettings,

        auto_manual_config: ManualPhotometryConfig | None = None,

    ) -> _ComputedPhotometryTask:

        solve_result = task.solve_result

        if solve_result.solved_field is None:

            return _ComputedPhotometryTask(index=task.index, measurements=[])



        if using_manual_mode and manual_config is not None:

            computed_measurements = measure_manual_sources(

                source_path=task.file_result.path,

                scan_result=task.file_result,

                wcs_path=solve_result.solved_field.wcs_path,

                manual_config=manual_config,

                frame_edge_margin_percent=settings.frame_edge_margin_percent,

                saturation_filter_enabled=settings.saturation_filter_enabled,

            )

            return _ComputedPhotometryTask(index=task.index, measurements=computed_measurements)



        aperture_profile = resolve_aperture_profile(

            source_path=task.file_result.path,

            wcs_path=solve_result.solved_field.wcs_path,

            variable_stars=variable_stars,

            reference_stars=reference_stars,

            settings=settings,

        )

        computed_measurements = measure_targets(

            source_path=task.file_result.path,

            scan_result=task.file_result,

            wcs_path=solve_result.solved_field.wcs_path,

            variable_stars=variable_stars,

            reference_stars=reference_stars,

            aperture_radius=aperture_profile.aperture_radius,

            annulus_inner_radius=aperture_profile.annulus_inner_radius,

            annulus_outer_radius=aperture_profile.annulus_outer_radius,

            frame_edge_margin_percent=settings.frame_edge_margin_percent,

            saturation_filter_enabled=settings.saturation_filter_enabled,

            settings=settings,

            adaptive_frame_fwhm_pixels=aperture_profile.fwhm_pixels,

        )

        if auto_manual_config is not None:

            computed_measurements.extend(

                measure_manual_sources(

                    source_path=task.file_result.path,

                    scan_result=task.file_result,

                    wcs_path=solve_result.solved_field.wcs_path,

                    manual_config=auto_manual_config,

                    frame_edge_margin_percent=settings.frame_edge_margin_percent,

                    saturation_filter_enabled=settings.saturation_filter_enabled,

                )

            )

        return _ComputedPhotometryTask(index=task.index, measurements=computed_measurements, note=aperture_profile.note)



    def _representative_field_for_summary(

        self,

        files: list[FileScanResult],

        settings: AppSettings,

        progress_callback: Callable[[str], None] | None = None,

        preview_only: bool = False,

    ) -> object | None:

        solved_results = self._resolve_summary_fields(files, settings, progress_callback)

        representative_field = next(

            (result.solved_field for _, result in solved_results if result.solved_field is not None),

            None,

        )

        if preview_only and representative_field is not None:

            _emit_progress(progress_callback, "Representative solved field is ready for the variable-star preview.")

        return representative_field



    def _best_field_catalog_for_solved_results(

        self,

        catalog_service: CatalogService,

        solved_results: list[tuple[FileScanResult, PlateSolveResult]],

        progress_callback: Callable[[str], None] | None = None,

    ) -> FieldCatalog:

        solved_fields = [result.solved_field for _, result in solved_results if result.solved_field is not None]

        if not solved_fields:

            raise ValueError("No solved WCS field is available for catalog lookup.")



        best_catalog: FieldCatalog | None = None

        best_score = -1

        best_field = None

        seen_keys: set[tuple[float, float, float]] = set()

        for index, solved_field in enumerate(solved_fields, start=1):

            key = (round(solved_field.center_ra_deg, 5), round(solved_field.center_dec_deg, 5), round(solved_field.radius_deg, 5))

            if key in seen_keys:

                continue

            seen_keys.add(key)

            catalog = catalog_service.query_field_catalog(solved_field)

            score = len(catalog.variable_stars) * 100000 + len(catalog.gaia_stars)

            if score > best_score:

                best_catalog = catalog

                best_score = score

                best_field = solved_field

            if catalog.variable_stars:

                break

            if index < len(solved_fields):

                _emit_progress(

                    progress_callback,

                    f"Catalog lookup for one solved field returned no variables; trying another solved frame for a more reliable field center.",

                )



        if best_catalog is None or best_field is None:

            raise ValueError("Catalog lookup could not be completed for any solved field.")



        if len(best_catalog.variable_stars) == 0 and len(seen_keys) > 1:

            _emit_progress(

                progress_callback,

                "Catalog lookup did not find variables in any solved frame; using the richest available catalog result.",

            )

        return best_catalog



    def export_results(

        self,

        report: ProcessingReport,

        output_dir: Path,

        measurements: list[PhotometryMeasurement] | None = None,

        light_curves: list[LightCurveSeries] | None = None,

        export_suffix: str | None = None,

        export_name_stem: str | None = None,

        plot_y_axis_mode: str = "differential_magnitude",

        plot_theme: str = "normal",

        custom_theme_colors: dict[str, str] | None = None,

        science_metadata: ScienceExportMetadata | None = None,

    ) -> dict[str, Path | int]:

        return export_processing_report(

            report,

            output_dir,

            measurements=measurements,

            light_curves=light_curves,

            export_suffix=export_suffix,

            export_name_stem=export_name_stem,

            plot_y_axis_mode=plot_y_axis_mode,

            plot_theme=plot_theme,

            custom_theme_colors=custom_theme_colors,

            science_metadata=science_metadata,

        )



    def preview_aavso_export(

        self,

        report: ProcessingReport,

        measurements: list[PhotometryMeasurement] | None = None,

        science_metadata: ScienceExportMetadata | None = None,

    ) -> dict[str, object]:

        return preview_aavso_extended_export(

            report,

            measurements=measurements,

            science_metadata=science_metadata,

        )



    def _load_cached_measurements(

        self,

        cache_dir: Path,

        file_result: FileScanResult,

        solve_result: PlateSolveResult,

        variable_source_ids: list[str],

        reference_source_ids: list[str],

        photometry_settings_signature: str,

    ) -> tuple[list[PhotometryMeasurement] | None, str | None]:

        cache_path = cache_dir / f"{_stable_file_key(file_result.path)}.json"

        if not cache_path.exists() or solve_result.solved_field is None:

            return None, None



        try:

            payload = json.loads(cache_path.read_text(encoding="utf-8"))

        except (OSError, json.JSONDecodeError):

            return None, "Ignored corrupt cached photometry measurements."

        if payload.get("algorithm_version") != _PHOTOMETRY_APERTURE_ALGORITHM_VERSION:

            return None, "Cache miss: photometry aperture algorithm changed."



        current_inputs = _measurement_cache_inputs(

            file_result.path,

            solve_result.solved_field.wcs_path,

            variable_source_ids,

            reference_source_ids,

            photometry_settings_signature,

        )

        current_key = _measurement_cache_key_from_inputs(current_inputs)

        if payload.get("cache_key") != current_key:

            return None, _describe_measurement_cache_miss(payload.get("cache_inputs"), current_inputs)



        try:

            return [_measurement_from_payload(item) for item in payload.get("measurements", [])], None

        except (KeyError, TypeError, ValueError):

            return None, "Ignored corrupt cached photometry measurements."



    def _store_cached_measurements(

        self,

        cache_dir: Path,

        file_result: FileScanResult,

        solve_result: PlateSolveResult,

        variable_source_ids: list[str],

        reference_source_ids: list[str],

        photometry_settings_signature: str,

        measurements: list[PhotometryMeasurement],

    ) -> None:

        if solve_result.solved_field is None:

            return



        cache_dir.mkdir(parents=True, exist_ok=True)

        cache_path = cache_dir / f"{_stable_file_key(file_result.path)}.json"

        cache_inputs = _measurement_cache_inputs(

            file_result.path,

            solve_result.solved_field.wcs_path,

            variable_source_ids,

            reference_source_ids,

            photometry_settings_signature,

        )

        payload = {

            "algorithm_version": _PHOTOMETRY_APERTURE_ALGORITHM_VERSION,

            "cache_key": _measurement_cache_key_from_inputs(cache_inputs),

            "cache_inputs": cache_inputs,

            "measurements": [_measurement_to_payload(item) for item in measurements],

        }

        cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")



    def _append_run_log(self, run_log_path: Path, report: ProcessingReport) -> None:

        run_log_path.parent.mkdir(parents=True, exist_ok=True)

        payload = {

            "timestamp_utc": datetime.now(UTC).isoformat(),

            "object_name": report.object_name,

            "files_processed": report.files_processed,

            "solved_files": report.solved_files,

            "measurement_count": len(report.measurements),

            "light_curve_count": len(report.light_curves),

            "notes": report.notes,

            "file_notes": report.file_notes,

        }

        with run_log_path.open("a", encoding="utf-8") as handle:

            handle.write(json.dumps(payload) + "\n")



    def _resolve_wcs(

        self,

        file_result: object,

        solve_cache_dir: Path,

        astrometry_client: AstrometryNetClient | None,

    ) -> PlateSolveResult:

        header, width, height = _read_header(file_result.path)

        valid_wcs, reasons = validate_wcs(header, file_result.path)

        if valid_wcs:

            solved_field = extract_solved_field(header, width, height, file_result.path)

            return PlateSolveResult(

                source_path=file_result.path,

                status=WcsStatus.SOLVED if solved_field else WcsStatus.UNSOLVED,

                solved_field=solved_field,

                reasons=reasons,

            )



        if astrometry_client is None:

            return PlateSolveResult(

                source_path=file_result.path,

                status=WcsStatus.UNSOLVED,

                solved_field=None,

                reasons=[*reasons, "Astrometry.net API key not configured; unsolved file skipped."],

            )



        return astrometry_client.solve_file(file_result.path, solve_cache_dir)





def _read_header(path: Path) -> tuple[Header, int | None, int | None]:

    return read_header_and_shape(path)





def _aligned_frame_reuse_key(

    file_result: FileScanResult,

    hints: AstrometrySolveHints | None,

    settings: AppSettings,

) -> tuple[str, int, int, str, float | None, float | None] | None:

    width = file_result.metadata.width

    height = file_result.metadata.height

    if width is None or height is None:

        return None

    if settings.assume_aligned_images:

        return (f"aligned:{file_result.object_folder}", width, height, file_result.path.suffix.lower(), None, None)

    if not is_pixinsight_staralignment_output(file_result.path):

        return None

    center_ra = round(hints.center_ra_deg, 4) if hints is not None and hints.center_ra_deg is not None else None

    center_dec = round(hints.center_dec_deg, 4) if hints is not None and hints.center_dec_deg is not None else None

    return (str(file_result.path.parent.resolve()).lower(), width, height, file_result.path.suffix.lower(), center_ra, center_dec)





def _deduplicate(items: list[str]) -> list[str]:

    seen: set[str] = set()

    output: list[str] = []

    for item in items:

        if item and item not in seen:

            seen.add(item)

            output.append(item)

    return output





def _stable_file_key(path: Path) -> str:

    return sha256(str(path.resolve()).encode("utf-8")).hexdigest()





def _emit_progress(progress_callback: Callable[[str], None] | None, message: str) -> None:

    if progress_callback is not None:

        progress_callback(message)





def _resolve_photometry_parallel_workers(settings: AppSettings) -> tuple[int, str]:

    configured_workers = resolve_shared_parallel_workers(settings)

    if configured_workers > 0:

        return configured_workers, "manual"



    cpu_count = os.cpu_count() or 2

    auto_workers = max(1, min(_DEFAULT_PHOTOMETRY_AUTO_MAX_WORKERS, cpu_count - 1))

    return auto_workers, f"auto from {cpu_count} logical CPU thread(s)"





def _select_aavso_recommended_variable_stars(
    variable_stars: list[CatalogStar],
    *,
    file_result: FileScanResult,
    solve_result: PlateSolveResult,
    settings: AppSettings,
    catalog_service: CatalogService,
    progress_callback: Callable[[str], None] | None,
) -> tuple[list[CatalogStar], list[str]]:

    if not variable_stars or solve_result.solved_field is None:

        return [], []

    notes: list[str] = []

    candidates = list(variable_stars)

    try:

        estimate_result = estimate_visible_magnitude_limit(
            file_result.path,
            settings=settings,
            progress_callback=progress_callback,
            catalog_service=catalog_service,
        )

        magnitude_limit = float(estimate_result.dimmest_visible_magnitude)

        visible_candidates = [
            star
            for star in candidates
            if star.magnitude is None or (np.isfinite(float(star.magnitude)) and float(star.magnitude) <= magnitude_limit)
        ]

        notes.append(
            f"Analyze Best estimated a visible limit of Gaia G {magnitude_limit:.1f} and kept {len(visible_candidates)} of {len(candidates)} designation-matched variable star(s) before ADU screening."
        )

        candidates = visible_candidates

    except Exception as exc:

        notes.append(f"Analyze Best could not estimate a visible magnitude limit ({exc}); continuing with ADU screening only.")

    if not candidates:

        notes.append("Analyze Best found no variable stars inside the estimated visible magnitude limit.")

        return [], notes

    try:

        aperture_profile = resolve_aperture_profile(
            source_path=file_result.path,
            wcs_path=solve_result.solved_field.wcs_path,
            variable_stars=candidates,
            reference_stars=[],
            settings=settings,
        )

        sampled_measurements = measure_targets(
            source_path=file_result.path,
            scan_result=file_result,
            wcs_path=solve_result.solved_field.wcs_path,
            variable_stars=candidates,
            reference_stars=[],
            aperture_radius=aperture_profile.aperture_radius,
            annulus_inner_radius=aperture_profile.annulus_inner_radius,
            annulus_outer_radius=aperture_profile.annulus_outer_radius,
            frame_edge_margin_percent=settings.frame_edge_margin_percent,
            saturation_filter_enabled=settings.saturation_filter_enabled,
            settings=settings,
            adaptive_frame_fwhm_pixels=aperture_profile.fwhm_pixels,
        )

    except Exception as exc:

        notes.append(f"Analyze Best ADU screening failed ({exc}); falling back to the brightest-variable percentage.")

        return _select_brightest_variable_stars(
            variable_stars,
            settings.variable_star_limit_mode,
            settings.variable_star_limit_value,
        ), notes

    star_by_key = {(star.catalog, star.source_id): star for star in candidates}

    selected_keys: set[tuple[str, str]] = set()

    for measurement in sampled_measurements:

        key = (measurement.catalog, measurement.source_id)

        if not measurement.is_variable or key not in star_by_key:

            continue

        peak_value = measurement.peak_pixel_value

        if peak_value is None or not np.isfinite(float(peak_value)):

            continue

        background_level = measurement.background_level if measurement.background_level is not None else 0.0

        peak_above_sky = float(peak_value) - float(background_level)

        if peak_above_sky < _AAVSO_ANALYZE_BEST_MIN_PEAK_ABOVE_SKY_ADU:

            continue

        saturation_threshold = measurement.saturation_threshold

        if saturation_threshold is not None and np.isfinite(float(saturation_threshold)) and float(saturation_threshold) > 0:

            if float(peak_value) > float(saturation_threshold) * _AAVSO_ANALYZE_BEST_MAX_SATURATION_FRACTION:

                continue

        if measurement.is_saturated:

            continue

        selected_keys.add(key)

    selected = [star for star in candidates if (star.catalog, star.source_id) in selected_keys]

    selected.sort(key=lambda star: (star.magnitude is None, star.magnitude if star.magnitude is not None else float("inf"), star.name.lower()))

    notes.append(
        f"Analyze Best ADU screen kept {len(selected)} of {len(candidates)} candidate variable star(s) between {_AAVSO_ANALYZE_BEST_MIN_PEAK_ABOVE_SKY_ADU:.0f} ADU above sky and {_AAVSO_ANALYZE_BEST_MAX_SATURATION_FRACTION * 100.0:.0f}% of saturation."
    )

    return selected, notes



def _select_brightest_variable_stars(

    variable_stars: list[object],

    limit_mode: VariableStarLimitMode,

    limit_value: int,

) -> list[object]:

    if not variable_stars:

        return []

    selected_count = _selected_variable_star_count(len(variable_stars), limit_mode, limit_value)

    if selected_count >= len(variable_stars):

        return list(variable_stars)



    ordered = sorted(

        variable_stars,

        key=lambda star: (

            star.magnitude is None,

            star.magnitude if star.magnitude is not None else float("inf"),

            star.name.lower(),

        ),

    )

    return ordered[:selected_count]





def _selected_variable_star_count(total_count: int, limit_mode: VariableStarLimitMode, limit_value: int) -> int:

    if total_count <= 0:

        return 0

    if limit_mode == VariableStarLimitMode.COUNT:

        return min(total_count, max(1, limit_value))

    return min(total_count, max(1, int(total_count * limit_value / 100)))





def _selection_label(limit_mode: VariableStarLimitMode, limit_value: int) -> str:

    if limit_mode == VariableStarLimitMode.COUNT:

        return f"top {limit_value} brightest stars"

    return f"top {limit_value}% brightest stars"





def _limit_preview_variable_stars(

    variable_stars: list[CatalogStar],

    max_count: int,

    min_magnitude: float | None,

    max_magnitude: float | None,

) -> list[CatalogStar]:

    if not variable_stars:

        return []



    ordered = sorted(

        variable_stars,

        key=lambda star: (

            star.magnitude is None,

            star.magnitude if star.magnitude is not None else float("inf"),

            star.name.lower(),

        ),

    )

    if min_magnitude is not None or max_magnitude is not None:

        ordered = [

            star

            for star in ordered

            if star.magnitude is not None

            and (min_magnitude is None or star.magnitude >= min_magnitude)

            and (max_magnitude is None or star.magnitude <= max_magnitude)

        ]

    if max_count > 0:

        ordered = ordered[:max_count]

    return ordered





def _merge_catalog_entries(primary_entries: list[CatalogStar], extra_entries: list[CatalogStar]) -> list[CatalogStar]:

    merged: list[CatalogStar] = []

    seen_source_keys: set[str] = set()

    for entry in [*primary_entries, *extra_entries]:

        source_key = _catalog_source_key(entry)

        if source_key in seen_source_keys:

            continue

        seen_source_keys.add(source_key)

        merged.append(entry)

    return merged





def _preview_limit_note(

    eligible_variable_stars: list[CatalogStar],

    preview_candidate_variable_stars: list[CatalogStar],

    max_count: int,

    min_magnitude: float | None,

    max_magnitude: float | None,

) -> str | None:

    if not eligible_variable_stars:

        return None



    preview_limit_parts: list[str] = []

    if max_count > 0:

        preview_limit_parts.append(f"brightest {max_count}")

    if min_magnitude is not None and max_magnitude is not None:

        preview_limit_parts.append(f"magnitude {min_magnitude:.1f} to {max_magnitude:.1f}")

    elif min_magnitude is not None:

        preview_limit_parts.append(f"magnitude >= {min_magnitude:.1f}")

    elif max_magnitude is not None:

        preview_limit_parts.append(f"magnitude <= {max_magnitude:.1f}")

    if not preview_limit_parts:

        return None



    if len(preview_candidate_variable_stars) == len(eligible_variable_stars):

        return None

    return (

        f"Preview list kept {len(preview_candidate_variable_stars)} of {len(eligible_variable_stars)} designation-matched variable stars "

        f"using {' and '.join(preview_limit_parts)}."

    )





def _catalog_source_key(entry: CatalogStar) -> str:

    return f"{entry.catalog}:{entry.source_id}"





_PHOTOMETRY_APERTURE_ALGORITHM_VERSION = "aperture-v7-local-fwhm"


def _measurement_cache_key(

    source_path: Path,

    wcs_path: Path,

    variable_source_ids: list[str],

    reference_source_ids: list[str],

    photometry_settings_signature: str,

) -> str:

    return _measurement_cache_key_from_inputs(

        _measurement_cache_inputs(

            source_path,

            wcs_path,

            variable_source_ids,

            reference_source_ids,

            photometry_settings_signature,

        )

    )





def _measurement_cache_inputs(

    source_path: Path,

    wcs_path: Path,

    variable_source_ids: list[str],

    reference_source_ids: list[str],

    photometry_settings_signature: str,

) -> dict[str, object]:

    source_stat = source_path.stat()

    wcs_stat = wcs_path.stat()

    return {

        "source_path": str(source_path.resolve()),

        "source_mtime_ns": int(source_stat.st_mtime_ns),

        "source_size": int(source_stat.st_size),

        "wcs_path": str(wcs_path.resolve()),

        "wcs_mtime_ns": int(wcs_stat.st_mtime_ns),

        "wcs_size": int(wcs_stat.st_size),

        "variable_source_ids": sorted(variable_source_ids),

        "reference_source_ids": sorted(reference_source_ids),

        "photometry_settings_signature": photometry_settings_signature,

    }





def _measurement_cache_key_from_inputs(cache_inputs: dict[str, object]) -> str:

    digest = sha256()

    digest.update(str(cache_inputs.get("source_path", "")).encode("utf-8"))

    digest.update(str(cache_inputs.get("source_mtime_ns", "")).encode("utf-8"))

    digest.update(str(cache_inputs.get("source_size", "")).encode("utf-8"))

    digest.update(str(cache_inputs.get("wcs_path", "")).encode("utf-8"))

    digest.update(str(cache_inputs.get("wcs_mtime_ns", "")).encode("utf-8"))

    digest.update(str(cache_inputs.get("wcs_size", "")).encode("utf-8"))

    digest.update("|".join(_coerce_cache_id_list(cache_inputs.get("variable_source_ids"))).encode("utf-8"))

    digest.update("|".join(_coerce_cache_id_list(cache_inputs.get("reference_source_ids"))).encode("utf-8"))

    digest.update(str(cache_inputs.get("photometry_settings_signature", "")).encode("utf-8"))

    return digest.hexdigest()





def _coerce_cache_id_list(value: object) -> list[str]:

    if not isinstance(value, list):

        return []

    return [str(item) for item in value]





def _describe_measurement_cache_miss(cached_inputs: object, current_inputs: dict[str, object]) -> str:

    if not isinstance(cached_inputs, dict):

        return "Cache miss: photometry inputs changed since the cached result was written."



    reasons: list[str] = []

    if cached_inputs.get("source_path") != current_inputs.get("source_path") or cached_inputs.get("source_mtime_ns") != current_inputs.get("source_mtime_ns") or cached_inputs.get("source_size") != current_inputs.get("source_size"):

        reasons.append("source image changed")

    if cached_inputs.get("wcs_path") != current_inputs.get("wcs_path") or cached_inputs.get("wcs_mtime_ns") != current_inputs.get("wcs_mtime_ns") or cached_inputs.get("wcs_size") != current_inputs.get("wcs_size"):

        reasons.append("solved WCS changed")

    if _coerce_cache_id_list(cached_inputs.get("variable_source_ids")) != _coerce_cache_id_list(current_inputs.get("variable_source_ids")):

        reasons.append("selected variable stars changed")

    if _coerce_cache_id_list(cached_inputs.get("reference_source_ids")) != _coerce_cache_id_list(current_inputs.get("reference_source_ids")):

        reasons.append("reference star selection changed")

    if cached_inputs.get("photometry_settings_signature") != current_inputs.get("photometry_settings_signature"):

        reasons.append("photometry settings changed")



    if not reasons:

        return "Cache miss: photometry inputs changed since the cached result was written."

    return f"Cache miss: {'; '.join(reasons)}."





def _photometry_settings_signature(settings: AppSettings, manual_config: ManualPhotometryConfig | None = None) -> str:

    parts = [

        _PHOTOMETRY_APERTURE_ALGORITHM_VERSION,

        "xisf-photometry-scale-v1",

        settings.observation_timezone,

        settings.photometry_aperture_mode.value,

        f"{settings.aperture_radius_pixels:.3f}",

        f"{settings.annulus_inner_radius_pixels:.3f}",

        f"{settings.annulus_outer_radius_pixels:.3f}",

        f"{settings.aperture_radius_fwhm_scale:.3f}",

        f"{settings.annulus_inner_radius_fwhm_scale:.3f}",

        f"{settings.annulus_outer_radius_fwhm_scale:.3f}",

        f"{settings.frame_edge_margin_percent:.3f}",

        str(int(settings.saturation_filter_enabled)),

    ]

    if manual_config is not None:

        parts.extend(

            [

                "manual-v1",

                manual_config.mode.value,

                manual_config.reference_frame_name or "-",

                str(int(manual_config.keep_comparison_stars)),

                manual_config.recenter_mode.value,

                f"{manual_config.max_recenter_radius_pixels:.3f}",

                str(int(manual_config.fallback_to_wcs_on_centroid_failure)),

            ]

        )

        for item in sorted(manual_config.sources, key=lambda source: source.source_id):

            parts.extend(

                [

                    item.source_id,

                    item.role.value,

                    f"{item.ra_deg:.8f}",

                    f"{item.dec_deg:.8f}",

                    f"{item.reference_x:.3f}",

                    f"{item.reference_y:.3f}",

                    f"{item.aperture_radius:.3f}",

                    f"{item.annulus_inner_radius:.3f}",

                    f"{item.annulus_outer_radius:.3f}",

                ]

            )

    return "|".join(parts)



_PROCESSING_REPORT_CACHE_DATACLASS_TYPES = {
    cls.__name__: cls
    for cls in (
        CatalogStar,
        FieldCatalog,
        LightCurvePoint,
        LightCurveSeries,
        ManualPhotometryConfig,
        ManualSourceConfig,
        PhotometryMeasurement,
        ProcessingReport,
    )
}



_PROCESSING_REPORT_CACHE_ENUM_TYPES = {
    cls.__name__: cls
    for cls in (
        ManualSourceRole,
        ObjectPhotometryMode,
        RecenterMode,
        VariableStarDesignationFamily,
        VariableStarLimitMode,
    )
}



def _processing_report_cache_inputs(
    summary_files: list[FileScanResult],
    settings: AppSettings,
    object_name: str,
    manual_config: ManualPhotometryConfig | None,
    *,
    analyze_best_targets: bool = False,
) -> dict[str, object]:
    file_entries: list[dict[str, object]] = []
    for file_result in sorted(summary_files, key=lambda item: str(item.path).lower()):
        file_stat = file_result.path.stat()
        file_entries.append(
            {
                "path": str(file_result.path.resolve()),
                "mtime_ns": int(file_stat.st_mtime_ns),
                "size": int(file_stat.st_size),
                "wcs_status": file_result.wcs_status.value,
            }
        )
    return {
        "object_name": object_name,
        "files": file_entries,
        "assume_aligned_images": bool(settings.assume_aligned_images),
        "variable_star_limit_mode": settings.variable_star_limit_mode.value,
        "variable_star_limit_value": int(settings.variable_star_limit_value),
        "variable_star_designation_filters": sorted(item.value for item in settings.variable_star_designation_filters),
        "analyze_best_targets": bool(analyze_best_targets),
        "selected_source_keys": sorted((settings.selected_catalog_source_ids or {}).get(object_name, [])),
        "reference_star_min_magnitude": settings.reference_star_min_magnitude,
        "reference_star_max_magnitude": settings.reference_star_max_magnitude,
        "nearby_reference_count": int(settings.nearby_reference_count),
        "photometry_settings_signature": _photometry_settings_signature(settings, manual_config),
    }



def _processing_report_cache_key_from_inputs(cache_inputs: dict[str, object]) -> str:
    normalized = json.dumps(cache_inputs, sort_keys=True, separators=(",", ":"))
    return sha256(normalized.encode("utf-8")).hexdigest()



def _serialize_processing_report_cache_value(value: object) -> object:
    if is_dataclass(value):
        return {
            "__dataclass__": type(value).__name__,
            "fields": {
                field.name: _serialize_processing_report_cache_value(getattr(value, field.name))
                for field in fields(value)
            },
        }
    if isinstance(value, Path):
        return {"__path__": str(value)}
    if isinstance(value, datetime):
        return {"__datetime__": value.isoformat()}
    if isinstance(value, Enum):
        return {"__enum__": type(value).__name__, "value": value.value}
    if isinstance(value, list):
        return [_serialize_processing_report_cache_value(item) for item in value]
    if isinstance(value, tuple):
        return [_serialize_processing_report_cache_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _serialize_processing_report_cache_value(item) for key, item in value.items()}
    return value



def _deserialize_processing_report_cache_value(value: object) -> object:
    if isinstance(value, list):
        return [_deserialize_processing_report_cache_value(item) for item in value]
    if not isinstance(value, dict):
        return value
    if "__path__" in value:
        return Path(str(value["__path__"]))
    if "__datetime__" in value:
        return datetime.fromisoformat(str(value["__datetime__"]))
    if "__enum__" in value:
        enum_type = _PROCESSING_REPORT_CACHE_ENUM_TYPES.get(str(value["__enum__"]))
        if enum_type is None:
            raise ValueError(f"Unsupported cached enum type: {value['__enum__']}")
        return enum_type(value["value"])
    if "__dataclass__" in value:
        dataclass_type = _PROCESSING_REPORT_CACHE_DATACLASS_TYPES.get(str(value["__dataclass__"]))
        if dataclass_type is None:
            raise ValueError(f"Unsupported cached dataclass type: {value['__dataclass__']}")
        payload = value.get("fields")
        if not isinstance(payload, dict):
            raise ValueError("Cached dataclass payload is invalid.")
        return dataclass_type(**{key: _deserialize_processing_report_cache_value(item) for key, item in payload.items()})
    return {str(key): _deserialize_processing_report_cache_value(item) for key, item in value.items()}

def _measurement_to_payload(measurement: PhotometryMeasurement) -> dict[str, object]:

    return {

        "source_id": measurement.source_id,

        "source_name": measurement.source_name,

        "catalog": measurement.catalog,

        "object_name": measurement.object_name,

        "file_path": str(measurement.file_path),

        "observation_time": measurement.observation_time.isoformat() if measurement.observation_time else None,

        "filter_name": measurement.filter_name,

        "ra_deg": measurement.ra_deg,

        "dec_deg": measurement.dec_deg,

        "x": measurement.x,

        "y": measurement.y,

        "flux": measurement.flux,

        "flux_error": measurement.flux_error,

        "instrumental_magnitude": measurement.instrumental_magnitude,

        "differential_magnitude": measurement.differential_magnitude,

        "differential_magnitude_error": measurement.differential_magnitude_error,

        "catalog_magnitude": measurement.catalog_magnitude,

        "calibrated_magnitude": measurement.calibrated_magnitude,

        "calibrated_magnitude_error": measurement.calibrated_magnitude_error,

        "zero_point_magnitude": measurement.zero_point_magnitude,

        "zero_point_magnitude_error": measurement.zero_point_magnitude_error,

        "zero_point_source_count": measurement.zero_point_source_count,

        "is_variable": measurement.is_variable,

        "is_reference": measurement.is_reference,

        "is_check": measurement.is_check,

        "flags": measurement.flags,

        "comparison_source_ids": measurement.comparison_source_ids,

        "comparison_source_names": measurement.comparison_source_names,

        "comparison_reference_flux": measurement.comparison_reference_flux,

        "aperture_radius": measurement.aperture_radius,

        "annulus_inner_radius": measurement.annulus_inner_radius,

        "annulus_outer_radius": measurement.annulus_outer_radius,

        "snr": measurement.snr,

        "background_level": measurement.background_level,

        "background_scatter": measurement.background_scatter,

        "centroid_shift_pixels": measurement.centroid_shift_pixels,

        "peak_pixel_value": measurement.peak_pixel_value,

        "saturation_threshold": measurement.saturation_threshold,

        "saturated_pixel_count": measurement.saturated_pixel_count,

        "is_saturated": measurement.is_saturated,

        "comparison_scatter": measurement.comparison_scatter,

        "quality_score": measurement.quality_score,

        "quality_weight": measurement.quality_weight,

        "excluded_from_analysis": measurement.excluded_from_analysis,

        "exclusion_reasons": measurement.exclusion_reasons,

    }





def _measurement_from_payload(payload: dict[str, object]) -> PhotometryMeasurement:

    observation_time = payload.get("observation_time")

    return PhotometryMeasurement(

        source_id=str(payload["source_id"]),

        source_name=str(payload["source_name"]),

        catalog=str(payload["catalog"]),

        object_name=str(payload["object_name"]),

        file_path=Path(str(payload["file_path"])),

        observation_time=datetime.fromisoformat(str(observation_time)) if observation_time else None,

        filter_name=str(payload["filter_name"]) if payload.get("filter_name") is not None else None,

        ra_deg=float(payload["ra_deg"]),

        dec_deg=float(payload["dec_deg"]),

        x=float(payload["x"]),

        y=float(payload["y"]),

        flux=float(payload["flux"]) if payload.get("flux") is not None else None,

        flux_error=float(payload["flux_error"]) if payload.get("flux_error") is not None else None,

        instrumental_magnitude=float(payload["instrumental_magnitude"]) if payload.get("instrumental_magnitude") is not None else None,

        differential_magnitude=float(payload["differential_magnitude"]) if payload.get("differential_magnitude") is not None else None,

        differential_magnitude_error=float(payload["differential_magnitude_error"]) if payload.get("differential_magnitude_error") is not None else None,

        catalog_magnitude=float(payload["catalog_magnitude"]) if payload.get("catalog_magnitude") is not None else None,

        calibrated_magnitude=float(payload["calibrated_magnitude"]) if payload.get("calibrated_magnitude") is not None else None,

        calibrated_magnitude_error=float(payload["calibrated_magnitude_error"]) if payload.get("calibrated_magnitude_error") is not None else None,

        zero_point_magnitude=float(payload["zero_point_magnitude"]) if payload.get("zero_point_magnitude") is not None else None,

        zero_point_magnitude_error=float(payload["zero_point_magnitude_error"]) if payload.get("zero_point_magnitude_error") is not None else None,

        zero_point_source_count=int(payload.get("zero_point_source_count", 0) or 0),

        is_variable=bool(payload["is_variable"]),

        is_reference=bool(payload["is_reference"]),

        is_check=bool(payload.get("is_check", False)),

        flags=[str(item) for item in payload.get("flags", [])],

        comparison_source_ids=[str(item) for item in payload.get("comparison_source_ids", [])],

        comparison_source_names=[str(item) for item in payload.get("comparison_source_names", [])],

        comparison_reference_flux=float(payload["comparison_reference_flux"]) if payload.get("comparison_reference_flux") is not None else None,

        aperture_radius=float(payload["aperture_radius"]) if payload.get("aperture_radius") is not None else None,

        annulus_inner_radius=float(payload["annulus_inner_radius"]) if payload.get("annulus_inner_radius") is not None else None,

        annulus_outer_radius=float(payload["annulus_outer_radius"]) if payload.get("annulus_outer_radius") is not None else None,

        snr=float(payload["snr"]) if payload.get("snr") is not None else None,

        background_level=float(payload["background_level"]) if payload.get("background_level") is not None else None,

        background_scatter=float(payload["background_scatter"]) if payload.get("background_scatter") is not None else None,

        centroid_shift_pixels=float(payload["centroid_shift_pixels"]) if payload.get("centroid_shift_pixels") is not None else None,

        peak_pixel_value=float(payload["peak_pixel_value"]) if payload.get("peak_pixel_value") is not None else None,

        saturation_threshold=float(payload["saturation_threshold"]) if payload.get("saturation_threshold") is not None else None,

        saturated_pixel_count=int(payload.get("saturated_pixel_count", 0) or 0),

        is_saturated=bool(payload.get("is_saturated", False)),

        comparison_scatter=float(payload["comparison_scatter"]) if payload.get("comparison_scatter") is not None else None,

        quality_score=float(payload["quality_score"]) if payload.get("quality_score") is not None else 1.0,

        quality_weight=float(payload["quality_weight"]) if payload.get("quality_weight") is not None else None,

        excluded_from_analysis=bool(payload.get("excluded_from_analysis", False)),

        exclusion_reasons=[str(item) for item in payload.get("exclusion_reasons", [])],

    )





def _manual_catalog_entries(manual_config: ManualPhotometryConfig, *roles: ManualSourceRole) -> list[CatalogStar]:

    wanted_roles = set(roles)

    entries: list[CatalogStar] = []

    for item in manual_config.sources:

        if item.role not in wanted_roles:

            continue

        entries.append(

            CatalogStar(

                catalog="manual",

                source_id=item.source_id,

                name=item.name,

                ra_deg=item.ra_deg,

                dec_deg=item.dec_deg,

                magnitude=None,

                is_variable=item.role != ManualSourceRole.COMPARISON,

                metadata={"manual_role": item.role.value},

            )

        )

    return entries





def _manual_config_ready_for_processing(manual_config: ManualPhotometryConfig | None) -> bool:

    return bool(

        manual_config is not None

        and manual_config.mode == ObjectPhotometryMode.MANUAL

        and manual_config.target_source is not None

        and manual_config.comparison_sources

    )


def _manual_config_ready_for_auto_append(manual_config: ManualPhotometryConfig | None) -> bool:

    return bool(

        manual_config is not None

        and manual_config.mode == ObjectPhotometryMode.AUTO

        and manual_config.measured_sources

        and manual_config.comparison_sources

    )

