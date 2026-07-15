# FILE_INDEX

Scope: Phase 1 file-by-file index for live application code, feature tests, scripts, and configuration. Omitted on purpose: generated outputs, caches, datasets, binary assets, benchmark result folders, logs, and `_tmp*` files.

## Configuration And Entrypoints

| File path | Purpose | Key classes/functions | Likely reasons to edit this file | Files it interacts with |
| --- | --- | --- | --- | --- |
| `pyproject.toml` | Package metadata, dependencies, console entrypoint. | `citizen-photometry = photometry_app.main:main` | Dependency changes, package metadata, console script changes. | `photometry_app/main.py`, package install/runtime. |
| `CitizenPhotometryDebug.spec` | PyInstaller packaging spec. | `Analysis`, `EXE`, `COLLECT` declarations | Packaging or hidden-import fixes only. | `photometry_app/main.py`, PyInstaller build. |
| `.vscode/settings.json` | Local editor/search exclusion defaults. | N/A | Cursor/VS Code indexing or interpreter defaults. | workspace search, Python tooling. |
| `.vscode/tasks.json` | Local task entrypoints for paper build, startup smoke, benchmarks, and validators. | N/A | Updating automation tasks. | `docs/paper/build_pdf.py`, `.venv`, `_tmp*` helpers, scripts. |
| `photometry_app/__init__.py` | Package marker. | N/A | Rarely edited. | package import path only. |
| `photometry_app/app_metadata.py` | App identity and icon path helpers. | `application_root_path`, `application_icon_path` | App title/icon/root-path changes. | `photometry_app/main.py`, UI startup. |
| `photometry_app/main.py` | Process entrypoint and startup error handling. | `_parse_cli_args`, `_configure_qt_application_attributes`, `main` | Startup failures, CLI flags, Qt startup attributes. | `photometry_app/app_metadata.py`, `photometry_app/core/discovery.py`, `photometry_app/ui/main_window.py`, `photometry_app/core/qt_image_format_smoke.py`. |

## Core Modules

| File path | Purpose | Key classes/functions | Likely reasons to edit this file | Files it interacts with |
| --- | --- | --- | --- | --- |
| `photometry_app/core/__init__.py` | Package marker. | N/A | Rarely edited. | package import path only. |
| `photometry_app/core/alignment.py` | WCS/image alignment for frame sequences. | `AlignedFrameOutput`, `SequenceAlignmentResult`, `align_wcs_image_sequence` | Alignment math, worker-count policy, reprojection behavior. | `image_io.py`, `wcs.py`, `ui/workers.py`, tests. |
| `photometry_app/core/animation_export.py` | GIF/MP4 export from `QImage` frames. | `export_qimages_to_gif`, `export_qimages_to_mp4` | Animation export bugs or format handling. | `exporters.py`, tests. |
| `photometry_app/core/array_backend.py` | NumPy/CuPy abstraction for synthetic tracking. | `SyntheticTrackingArrayBackend`, `create_array_backend`, `resolve_full_frame_backend` | GPU/CPU backend selection changes. | `synthetic_tracking.py`, tests. |
| `photometry_app/core/benchmarking.py` | Benchmark recorder and environment-gated timing model. | `BenchmarkRecorder`, `BenchmarkSectionSample`, `BenchmarkFrameRecord`, `get_benchmark_recorder` | Frame timing, benchmark schema, env-flag behavior. | `main_window.py`, `constellation_overlay.py`, `sky_view_milky_way_gl.py`, scripts, tests. |
| `photometry_app/core/calibration.py` | Calibration pipeline for dark/flat correction. | `CalibrationPipelineRequest`, `CalibrationPipelineResult`, `calibrate_image_sequence` | Calibration math, scaling, cache/master handling. | `alignment.py`, `image_io.py`, `ui/workers.py`, tests. |
| `photometry_app/core/candidate_training.py` | Candidate-label storage and feature extraction. | `CandidateLabelRecord`, `CandidatePrediction`, `CandidateTrainingResult`, `CandidateTrainingStore` | Training DB, feature vectors, prediction payloads. | `discovery.py`, `transient.py`, `models.py`, tests. |
| `photometry_app/core/catalog_filters.py` | Variable-star designation filters. | `classify_variable_star_designation`, `filter_variable_stars`, `format_designation_family_labels` | Designation-family filtering or labels. | `models.py`, `pipeline.py`, dialogs/tests. |
| `photometry_app/core/catalogs.py` | Catalog queries, cache use, and detail/literature lookups. | `LiteraturePeriodResult`, `CatalogTargetDetails`, `CatalogService`, `fetch_catalog_target_details` | Catalog fetch behavior, cache policy, detail payloads. | `pipeline.py`, `sky_explorer.py`, `workers.py`, tests. |
| `photometry_app/core/discovery.py` | Moving-object discovery and recovery pipeline. | `MovingObjectCandidate`, `MovingObjectDiscoveryResult`, `MovingObjectRecoveryResult`, `discover_unmatched_moving_candidates`, `recover_known_moving_objects` | Discovery search, residual linking, cancellation, debug output. | `solar_system.py`, `synthetic_tracking.py`, `ui/workers.py`, scripts, tests. |
| `photometry_app/core/discovery_benchmark.py` | Benchmark harness for discovery runs. | `DiscoveryBenchmarkReport`, `run_discovery_benchmark` | Discovery profiling or report formatting. | `discovery.py`, `scanner.py`, `settings.py`, scripts/tests. |
| `photometry_app/core/error_calculations.py` | Photometric/error propagation helpers. | `compute_flux_error`, `compute_total_mag_error` | Error model changes. | `matching.py`, tests. |
| `photometry_app/core/exporters.py` | Report/export bundle generation. | `ScienceExportMetadata`, `export_processing_report`, `export_light_curve_plots`, `export_annotated_images`, `export_light_curve_animated_gif` | CSV/JSON/report export changes, AAVSO export, annotated images. | `animation_export.py`, `image_io.py`, `plotting.py`, `pipeline.py`, `workers.py`, tests. |
| `photometry_app/core/hr_diagram.py` | H-R source measurement and working-table construction. | `HrMeasurementRow`, `HrWorkingTable`, `measure_hr_sources` | H-R measurement pipeline or Gaia matching changes. | `image_io.py`, `photometry.py`, `settings.py`, `ui/hr_plot_widget.py`, tests. |
| `photometry_app/core/hr_motion_groups.py` | Common-motion group clustering for H-R results. | `HrMotionGroupSettings`, `HrCommonMotionGroupResult`, `find_common_motion_group` | Motion-group clustering or refinement changes. | `hr_diagram.py`, dialogs/tests. |
| `photometry_app/core/image_io.py` | Unified image/header readers for FITS, XISF, and raster inputs. | `read_header`, `read_header_and_shape`, `read_image_data`, `read_photometry_image_data`, `write_fits_copy` | File-format support, XISF metadata/WCS handling, preview/photometry data issues. | `scanner.py`, `wcs.py`, `plotting.py`, `pipeline.py`, tests. |
| `photometry_app/core/matching.py` | Differential photometry and light-curve series building. | `select_reference_stars`, `apply_differential_photometry`, `apply_measurement_quality_analysis`, `build_light_curve_series` | Reference-star logic, variability metrics, measurement filtering. | `error_calculations.py`, `models.py`, `pipeline.py`, `workers.py`, tests. |
| `photometry_app/core/milky_way_assets.py` | Milky Way manifest parsing, tile math, level selection, and resolver logic. | `MilkyWayAssetResolver`, `MilkyWayTileManifest`, `select_milky_way_level_with_hysteresis`, `resolve_manifest_tile_path` | Manifest bugs, runtime asset selection, tile UV/RA/Dec mapping. | `main_window.py`, `sky_view_milky_way_gl.py`, `milky_way_tile_generator.py`, tests. |
| `photometry_app/core/milky_way_mask.py` | Milky Way alpha-mask generation and statistics. | `MilkyWayMaskParameters`, `MilkyWayTileSignalStats`, `apply_milky_way_alpha_mask` | Alpha-mask behavior or tile signal stats. | `milky_way_assets.py`, `sky_view_milky_way_gl.py`, generator/tests. |
| `photometry_app/core/milky_way_tile_format_benchmark.py` | Tile-format decode and size benchmarking. | `MilkyWayTileFormatVariant`, `benchmark_decode_variants`, `benchmark_prepared_tile_variants`, `write_benchmark_report` | Tile format benchmarking or report structure. | `milky_way_assets.py`, `sky_view_milky_way_gl.py`, tests. |
| `photometry_app/core/milky_way_tile_generator.py` | Offline Milky Way tile-pyramid generation. | `LoadedMilkyWaySource`, `MilkyWayTileGenerationSummary`, `generate_milky_way_tile_pyramid` | Asset-generation pipeline changes. | `milky_way_assets.py`, `milky_way_mask.py`, scripts/tests. |
| `photometry_app/core/models.py` | Shared enums and dataclasses for the app. | `AppMode`, `ObservationMetadata`, `FileScanResult`, `CatalogStar`, `PhotometryMeasurement`, `ProcessingReport`, `RunHistoryEntry` | Schema/dataclass changes used across the repo. | nearly all `core/*`, `ui/*`, tests. |
| `photometry_app/core/photometry.py` | Aperture photometry engine and measurement building. | `ApertureProfile`, `PhotometryFrameContext`, `resolve_aperture_profile`, `measure_targets`, `measure_manual_sources` | Aperture logic, centroiding, saturation/usable-area flags. | `image_io.py`, `settings.py`, `pipeline.py`, tests. |
| `photometry_app/core/pipeline.py` | Top-level photometry pipeline orchestration and cache logic. | `PhotometryPipeline`, `science_export_metadata_from_settings`, `_measurement_cache_key_from_inputs`, `_processing_report_cache_key_from_inputs` | Workflow orchestration, cache signatures, run history, catalog-cache clearing. | `scanner.py`, `catalogs.py`, `photometry.py`, `matching.py`, `exporters.py`, `settings.py`, `ui/workers.py`, tests. |
| `photometry_app/core/plotting.py` | Light-curve plotting and annotated-image rendering. | `LightCurveFitConfig`, `AnnotatedImageDisplay`, `AnnotatedImageRenderSettings`, `FitPeriodInferenceResult`, `plot_light_curve`, `build_annotated_image_display`, `render_annotated_image` | Plot appearance, fit logic, image-stretch/display behavior. | `image_io.py`, `settings.py`, `ui/image_view.py`, `ui/light_curve_widget.py`, exporters/tests. |
| `photometry_app/core/qt_image_format_smoke.py` | Qt image-format smoke runner. | `run_qt_image_format_smoke` | Shipping/runtime image-format validation. | `qt_image_formats.py`, `main.py`, tests. |
| `photometry_app/core/qt_image_formats.py` | Runtime Qt image-format support inspection. | `QtImageFormatSupport`, `query_qt_image_format_support`, `qt_image_decode_failure_reason` | Plugin support diagnostics. | `main_window.py`, `qt_image_format_smoke.py`, tests. |
| `photometry_app/core/scanner.py` | Dataset/file discovery and metadata extraction. | `scan_fits_tree`, `inspect_fits_file`, `parse_observation_timestamp` | Workspace scanning, filename metadata fallback, time parsing. | `image_io.py`, `wcs.py`, `pipeline.py`, tests. |
| `photometry_app/core/settings.py` | App settings schema, coercion, and persistence. | `ObservingSitePreset`, `AppSettings`, `default_sky_explorer_enabled_layers`, `resolve_shared_parallel_workers` | Saved-state bugs, new settings, defaults, settings migrations. | `main_window.py`, `dialogs.py`, `pipeline.py`, `workers.py`, tests. |
| `photometry_app/core/sky_atlas.py` | Sky View object/star catalog loading and search. | `SkyAtlasObject`, `load_sky_atlas_objects`, `load_scientific_sky_atlas_star_objects`, `search_sky_atlas_objects` | Object naming, cache logic, catalog depth, Sky View search. | `main_window.py`, scripts, tests. |
| `photometry_app/core/sky_explorer.py` | Sky Explorer query/model layer. | `SkyExplorerResult`, `SkyExplorerObject`, `SkyExplorerLayerSummary`, `explore_sky_image`, `_sky_explorer_numeric_magnitude` | Sky Explorer query behavior, object typing, result shaping. | `workers.py`, `main_window.py`, `settings.py`, tests. |
| `photometry_app/core/snr_binning.py` | SNR-based measurement binning. | `SnrBinningSettings`, `SnrBinningSourceResult`, `SnrBinningTask`, `process_snr_binning_task` | Derived-series behavior or binning policy changes. | `workers.py`, `settings.py`, tests. |
| `photometry_app/core/solar_system.py` | Known-object prediction, lookup, visibility, and measurement helpers. | `SolarSystemDetection`, `KnownObjectHeliocentricContext`, `SolarSystemQueryService`, `detect_known_solar_system_objects`, `estimate_visible_magnitude_limit`, `build_known_object_heliocentric_context` | Solar-system detection, Skybot/Horizons behavior, visibility limits. | `main_window.py`, `workers.py`, `discovery.py`, `synthetic_tracking.py`, tests. |
| `photometry_app/core/synthetic_tracking.py` | Synthetic tracking stack building and measurement helpers. | `SyntheticTrackingResult`, `SyntheticTrackingFrameTarget`, `build_synthetic_tracked_stack`, `build_synthetic_tracked_full_frame_stack` | Synthetic-tracking math, backend handling, stack modes. | `array_backend.py`, `solar_system.py`, `workers.py`, scripts/tests. |
| `photometry_app/core/transient.py` | Transient search pipeline. | `TransientCandidate`, `TransientSearchResult`, `search_transients_in_folder` | Transient detection/grouping or catalog-veto behavior. | `catalogs.py`, `scanner.py`, `wcs.py`, `workers.py`, tests. |
| `photometry_app/core/wcs.py` | WCS validation, solve preparation, and astrometry client. | `AstrometrySolveHints`, `AstrometryNetClient`, `validate_wcs`, `extract_solved_field`, `infer_astrometry_solve_hints`, `celestial_wcs` | Plate-solving, cached solve behavior, WCS normalization. | `image_io.py`, `scanner.py`, `pipeline.py`, `transient.py`, tests. |

## UI Modules

| File path | Purpose | Key classes/functions | Likely reasons to edit this file | Files it interacts with |
| --- | --- | --- | --- | --- |
| `photometry_app/ui/__init__.py` | Package marker. | N/A | Rarely edited. | package import path only. |
| `photometry_app/ui/constellation_overlay.py` | Constellation data load and overlay rendering. | `ConstellationDataLoader`, `ConstellationLineRenderer`, `ConstellationLabelRenderer`, `ConstellationOverlay`, `ConstellationOverlaySettings` | Constellation line/label projection or styling. | `main_window.py`, `benchmarking.py`, tests. |
| `photometry_app/ui/curves_widget.py` | Histogram/curve widget used by image-level controls. | `HistogramCurvesWidget` | Curve-widget UI behavior. | `levels_dialog.py`. |
| `photometry_app/ui/dialogs.py` | Shared dialogs across workflows. | `SettingsDialog`, `LightCurveFilterDialog`, `ResultsViewFilterDialog`, `AsteroidDiscoveryDialog`, `CalibrationPipelineDialog`, `ThemeCustomizeDialog`, many other dialogs | Dialog layout, validation, or mode-specific control issues. | `models.py`, `settings.py`, `discovery.py`, `hr_motion_groups.py`, `main_window.py`, tests. |
| `photometry_app/ui/differential_label_dialog.py` | Quick-label dialog for differential results. | `DifferentialQuickLabelDialog` | Differential quick-label UI behavior. | `models.py`, `plotting.py`, `light_curve_widget.py`, tests. |
| `photometry_app/ui/hr_plot_widget.py` | H-R diagram plot widget. | `HrDiagramPlotWidget` | H-R plotting, selection, or styling changes. | `hr_diagram.py`, `plotting.py`, tests. |
| `photometry_app/ui/image_view.py` | Annotated image viewer and overlay panels. | `AnnotatedImageView`, `ImageOverlay`, `EquatorialGridOverlay`, `SelectionOverlay`, `MotionVectorOverlay`, `ImageInfoPanel` | Annotated-image interaction or overlay display changes. | `plotting.py`, `main_window.py`, `levels_dialog.py`, tests. |
| `photometry_app/ui/levels_dialog.py` | Curves dialog for annotated-image levels. | `CurvesDialog`, `_stored_curve_points` | Image-level/curve editing UI. | `plotting.py`, `curves_widget.py`, `image_view.py`, `main_window.py`. |
| `photometry_app/ui/levels_strip.py` | Histogram levels strip widget. | `HistogramLevelsStrip` | Histogram strip interaction/rendering. | `levels_dialog.py`, image controls. |
| `photometry_app/ui/light_curve_widget.py` | Interactive light-curve plot widget. | `LightCurvePlotWidget` | Plot interaction or display behavior. | `plotting.py`, `models.py`, `main_window.py`, tests. |
| `photometry_app/ui/main_window.py` | Main window and large integration hub for all major modes, especially Sky View. | `MainWindow`, `_SkyAtlasViewWidget`, `_QPainterSkyViewRenderer`, `_OpenGLFaintStarLayer`, `_OpenGLStarFieldLayer`, `_OpenGLSolidGroundLayer`, `_OpenGLAtmosphereMaskLayer`, `_SkyViewTimingWindow`, `_SkyViewMilkyWayDetailWindow` | UI wiring, Sky View behavior, layer ordering, mode switching, diagnostics windows. | nearly every `core/*` module, most `ui/*` modules, tests. |
| `photometry_app/ui/moon_system.py` | Moon ephemeris/orientation/tile/cache/GL subsystem. | `MoonEphemeris`, `MoonAsyncEphemerisSampler`, `MoonCache`, `MoonTiledTextureManager`, `MoonRendererGL`, `select_lunar_orientation_provider` | Moon rendering, Moon state, SPICE fallback, tiled Moon behavior. | `main_window.py`, scripts, tests. |
| `photometry_app/ui/moving_object_label_dialog.py` | Quick-label dialog for moving-object detections. | `MovingObjectQuickLabelDialog`, `_draw_target_marker` | Moving-object labeling UI. | `discovery.py`, `image_io.py`, tests. |
| `photometry_app/ui/sky_view_location.py` | Observer-location dialog and map widget. | `_SkyViewLocationDialog`, `_SkyViewLocationMapWidget`, `_SkyViewObservingSiteSelection`, `_reverse_geocode_observing_site` | Location presets, reverse geocode, or map UI behavior. | `settings.py`, `main_window.py`, tests indirectly. |
| `photometry_app/ui/sky_view_milky_way_gl.py` | Milky Way GL decode/cache/draw helper layer. | `OpenGLMilkyWayLayer`, `SkyViewMilkyWayGlUniforms`, `SkyViewMilkyWayTileDrawRequest` | Milky Way tile upload, cache eviction, shader sampling, draw submission. | `milky_way_assets.py`, `milky_way_mask.py`, `main_window.py`, tests. |
| `photometry_app/ui/sky_view_milky_way_pixel_probe_support.py` | Pixel-probe/debug helpers for Milky Way validation. | `IncrementalJsonReport`, `decode_packed_uv`, `decode_tile_id_exact`, `evaluate_sampling_models` | Pixel diagnostics or readback-model comparison tools. | Milky Way validators/tests. |
| `photometry_app/ui/sky_view_simulation.py` | Simulation clock model for Sky View. | `SkyViewSimulationClock` | Play/pause/scrub/time-rate semantics. | `main_window.py`, tests. |
| `photometry_app/ui/transient_label_dialog.py` | Quick-label dialog for transient detections. | `TransientQuickLabelDialog`, `_draw_center_marker` | Transient labeling UI. | `transient.py`, `wcs.py`, `image_io.py`, tests. |
| `photometry_app/ui/workers.py` | QThread worker layer for background execution. | `ScanWorker`, `ProcessWorker`, `SkyExplorerWorker`, `SolarSystemDetectionWorker`, `TransientSearchWorker`, `CalibrationWorker`, `AsteroidDiscoveryWorker`, `ImageDisplayPreloadWorker`, `CalculatePeriodWorker`, `IncreaseSnrWorker` | Progress/cancel/threading bugs, worker orchestration, preload behavior. | `pipeline.py`, `plotting.py`, `discovery.py`, `solar_system.py`, `sky_explorer.py`, tests. |

## Scripts

| File path | Purpose | Key classes/functions | Likely reasons to edit this file | Files it interacts with |
| --- | --- | --- | --- | --- |
| `scripts/benchmark_full_frame_synthetic_tracking.py` | Standalone synthetic-tracking benchmark. | `run_benchmark`, `main` | Synthetic-tracking performance harness work. | `settings.py`, `synthetic_tracking.py`. |
| `scripts/build_bright_object_planner_database.py` | Build planner DB for bright solar-system objects. | `main` | Planner database maintenance. | `solar_system.py`. |
| `scripts/build_moon_tiles.py` | Moon tile generation and validation tooling. | `build_moon_tile_tree`, `validate_tile_uv_sampling`, `validate_global_vs_tiled_registration`, `validate_moon_tile_tree`, `main` | Moon asset-generation or validation changes. | `ui/moon_system.py`, tests. |
| `scripts/check_moon_spice.py` | SPICE diagnostics/report generator. | `build_moon_spice_diagnostics_report`, `main` | Moon orientation-provider/SPICE validation. | `ui/moon_system.py`, tests. |
| `scripts/compare_full_frame_synthetic_tracking_dataset.py` | Compare tracking backends on a dataset. | `_run_backend_comparison`, `main` | Dataset comparison harness changes. | `image_io.py`, `scanner.py`, `settings.py`, `solar_system.py`, `synthetic_tracking.py`. |
| `scripts/generate_demo_dataset.py` | Generate demo/test dataset. | `main`, `_build_wcs`, `_draw_star` | Demo dataset generation. | `catalogs.py`, `matching.py`, `models.py`, `settings.py`. |
| `scripts/generate_doc_screenshots.py` | Generate documentation screenshots from live widgets. | `generate_loaded_results_dialog`, `generate_workspace_strip`, `generate_source_results_actions`, `main` | Docs screenshot regeneration. | `ui/dialogs.py`, `ui/main_window.py`, `models.py`. |
| `scripts/generate_milky_way_tiles.py` | CLI wrapper for Milky Way tile generation. | `main` | Tile-generation CLI argument changes. | `core/milky_way_tile_generator.py`. |
| `scripts/generate_synthetic_tracking_comparison_plots.py` | Build comparison dashboards/plots from benchmark JSON. | `build_dashboard`, `build_table_figure`, `main` | Post-benchmark reporting/plot styling. | benchmark JSON outputs. |
| `scripts/moon_visual_smoke.py` | Moon visual smoke report builder. | `build_moon_visual_smoke_report`, `build_moon_phase5k_normal_continuity_report`, `main` | Moon smoke harness/report changes. | `ui/moon_system.py`, `core/sky_atlas.py`, tests. |
| `scripts/preprocess_milky_way_texture.py` | Preprocess a source Milky Way texture. | `process_milky_way`, `main` | Source texture preprocessing only. | external texture assets. |
| `scripts/profile_discover_run.py` | Discovery benchmark/profile entrypoint. | `main` | Discovery profiling CLI changes. | `core/discovery_benchmark.py`, `settings.py`. |
| `scripts/run_demo_smoke_test.py` | Non-UI demo smoke runner. | `main` | Smoke-test workflow changes. | `pipeline.py`, `settings.py`. |
| `scripts/run_sky_view_benchmark.py` | Main Sky View benchmark harness. | `SkyViewBenchmarkScenario`, `SkyViewBenchmarkHarness`, `main` | Scenario definitions, benchmark reporting, timing exports. | `benchmarking.py`, `main_window.py`. |

## Tests

| File path | Purpose | Key classes/functions | Likely reasons to edit this file | Files it interacts with |
| --- | --- | --- | --- | --- |
| `tests/test_alignment.py` | Alignment regression tests. | `AlignmentTest` | Alignment behavior changes. | `core/alignment.py`. |
| `tests/test_animation_export.py` | GIF/MP4 export tests. | `AnimationExportTest` | Animation export changes. | `core/animation_export.py`. |
| `tests/test_benchmarking.py` | Benchmark recorder tests. | `BenchmarkRecorderTest` | Benchmark schema/behavior changes. | `core/benchmarking.py`. |
| `tests/test_build_moon_tiles.py` | Moon tile-build script tests. | `MoonTileBuildScriptTest` | Moon tile-generation script changes. | `scripts/build_moon_tiles.py`. |
| `tests/test_calibration.py` | Calibration pipeline tests. | `CalibrationPipelineTest` | Calibration behavior changes. | `core/calibration.py`, `core/alignment.py`, `core/settings.py`. |
| `tests/test_candidate_training.py` | Candidate-training store tests. | `CandidateTrainingStoreTest` | Training store/feature changes. | `core/candidate_training.py`, `core/discovery.py`, `core/transient.py`. |
| `tests/test_catalogs.py` | Catalog service tests. | `CatalogServiceTest` | Catalog query/cache/detail changes. | `core/catalogs.py`, `core/models.py`. |
| `tests/test_constellation_overlay.py` | Constellation overlay render/data tests. | `ConstellationDataLoaderTest`, `ConstellationOverlayRenderTest` | Constellation overlay changes. | `ui/constellation_overlay.py`. |
| `tests/test_differential_label_dialog.py` | Differential quick-label dialog tests. | `DifferentialQuickLabelDialogTest` | Differential labeling UI changes. | `ui/differential_label_dialog.py`. |
| `tests/test_discovery.py` | Discovery-pipeline tests. | `DiscoverMovingCandidatesTest` | Discovery logic changes. | `core/discovery.py`, `core/solar_system.py`. |
| `tests/test_discovery_benchmark.py` | Discovery benchmark/report tests. | `DiscoveryBenchmarkTest`, `DiscoveryBenchmarkHelpersTest` | Discovery benchmark changes. | `core/discovery_benchmark.py`, `core/discovery.py`. |
| `tests/test_error_calculations.py` | Error-calculation tests. | `ErrorCalculationsTest` | Error-propagation changes. | `core/error_calculations.py`. |
| `tests/test_exporters.py` | Export/report tests. | `ExportersTest` | Export-format/report changes. | `core/exporters.py`, `core/plotting.py`, `core/models.py`. |
| `tests/test_hr_diagram.py` | H-R helper tests. | `HrDiagramHelperTest` | H-R measurement changes. | `core/hr_diagram.py`, `core/settings.py`. |
| `tests/test_hr_motion_groups.py` | H-R motion-group tests. | `HrMotionGroupTest` | Motion-group clustering changes. | `core/hr_motion_groups.py`, `core/hr_diagram.py`. |
| `tests/test_hr_plot_widget.py` | H-R widget tests. | `HrDiagramPlotWidgetTest` | H-R UI changes. | `ui/hr_plot_widget.py`, `core/hr_diagram.py`. |
| `tests/test_image_io.py` | Image I/O tests. | `ImageIoTest` | Format/WCS/header handling changes. | `core/image_io.py`. |
| `tests/test_image_view.py` | Annotated-image widget tests. | `ImageViewInfoPanelTest` | Image panel/overlay changes. | `ui/image_view.py`, `core/plotting.py`. |
| `tests/test_light_curve_widget.py` | Light-curve widget tests. | `LightCurvePlotWidgetTest` | Light-curve UI changes. | `ui/light_curve_widget.py`, `core/plotting.py`. |
| `tests/test_main.py` | Startup-entry tests. | `MainStartupTest` | Startup/entrypoint changes. | `photometry_app/main.py`. |
| `tests/test_main_window.py` | Large MainWindow and Sky View regression surface. | `MainWindowLightCurveSegmentTest`, `SkyViewMilkyWayTextureTest` | Cross-feature UI regressions, Sky View integration, MainWindow wiring. | `ui/main_window.py`, many `core/*` modules. |
| `tests/test_main_window_image_panel.py` | MainWindow image-panel tests. | `MainWindowImagePanelTest` | Annotated-image panel integration changes. | `ui/main_window.py`, `core/plotting.py`, `core/models.py`. |
| `tests/test_matching.py` | Matching/differential photometry tests. | `MatchingTest` | Reference-star or light-curve-series changes. | `core/matching.py`. |
| `tests/test_milky_way_assets.py` | Milky Way manifest/resolver/generator tests. | `MilkyWayManifestTest`, `MilkyWayAssetResolverTest`, `MilkyWayTileGeneratorTest` | Milky Way asset-selection or tile-generation changes. | `core/milky_way_assets.py`, `core/milky_way_tile_generator.py`. |
| `tests/test_milky_way_tile_format_benchmark.py` | Milky Way tile-format benchmark tests. | `MilkyWayTileFormatBenchmarkTest` | Benchmark/report changes. | `core/milky_way_tile_format_benchmark.py`. |
| `tests/test_moon_system.py` | Main Moon subsystem tests. | `MoonSystemTest` plus fake provider/executor helpers | Moon ephemeris/orientation/tile/render changes. | `ui/moon_system.py`, `scripts/check_moon_spice.py`. |
| `tests/test_moon_visual_smoke.py` | Moon visual smoke report tests. | `MoonVisualSmokeReportTest` | Moon smoke-report changes. | `scripts/build_moon_tiles.py`, `scripts/moon_visual_smoke.py`. |
| `tests/test_moving_object_label_dialog.py` | Moving-object label dialog tests. | `MovingObjectQuickLabelDialogTest` | Moving-object labeling UI changes. | `ui/moving_object_label_dialog.py`. |
| `tests/test_photometry.py` | Photometry-engine tests. | `PhotometryErrorTest` | Measurement/aperture/flagging changes. | `core/photometry.py`, `core/settings.py`. |
| `tests/test_pipeline_integration.py` | End-to-end pipeline integration tests. | `PipelineIntegrationTest` and fake service/client helpers | Pipeline orchestration, cache, or failure-handling changes. | `core/pipeline.py`, `core/wcs.py`, `core/exporters.py`, `core/settings.py`. |
| `tests/test_plotting.py` | Plotting and annotated-image payload tests. | `AnnotatedImageDisplayTest`, `LightCurvePlotPayloadTest` | Plotting/payload changes. | `core/plotting.py`. |
| `tests/test_qt_image_formats.py` | Qt image-format support/smoke tests. | `QtImageFormatSupportTest` | Runtime image-format support changes. | `core/qt_image_formats.py`, `core/qt_image_format_smoke.py`. |
| `tests/test_recovery_audit_regressions.py` | Recovery-audit regression tests around UI/worker flows. | `RecoveryAuditRegressionTest` | Regression fixes from recovery/audit work. | `ui/dialogs.py`, `ui/main_window.py`, `ui/workers.py`, `core/solar_system.py`. |
| `tests/test_runtime_dependencies.py` | Import/runtime dependency smoke tests. | `RuntimeDependencyImportTest` | Dependency/import-path changes. | runtime imports across the repo. |
| `tests/test_scanner.py` | Scanner tests. | `ScannerTest` | File-discovery or timestamp parsing changes. | `core/scanner.py`. |
| `tests/test_settings.py` | Settings persistence tests. | `SettingsTest` | `AppSettings` changes. | `core/settings.py`, `core/models.py`. |
| `tests/test_sky_atlas.py` | Sky atlas cache/catalog tests. | `SkyAtlasCatalogCacheTest` | Sky atlas cache/load/search changes. | `core/sky_atlas.py`. |
| `tests/test_sky_explorer.py` | Sky Explorer tests. | `SkyExplorerTest` | Sky Explorer query/result changes. | `core/sky_explorer.py`, `core/settings.py`, `core/solar_system.py`. |
| `tests/test_sky_view_lzw_visual_smoke.py` | Authority smoke wrapper for Milky Way visual checks. | `LzwVisualSmokeAuthorityTest` | Smoke harness behavior. | `_tmp_sky_view_lzw_visual_smoke.py` helper, temporary smoke artifacts. |
| `tests/test_sky_view_milky_way_gl.py` | Milky Way GL helper tests. | `SkyViewMilkyWayGlHelperTest` | Tile upload/cache/sample behavior changes. | `ui/sky_view_milky_way_gl.py`. |
| `tests/test_sky_view_milky_way_pixel_probe_support.py` | Milky Way pixel-probe helper tests. | function-based tests such as `test_decode_helpers_round_trip` | Pixel probe/debug helper changes. | `ui/sky_view_milky_way_pixel_probe_support.py`. |
| `tests/test_sky_view_simulation.py` | Simulation clock tests. | `SkyViewSimulationClockTest` | Time simulation semantics changes. | `ui/sky_view_simulation.py`. |
| `tests/test_snr_binning.py` | SNR binning tests. | `SnrBinningTest` | SNR-binning behavior changes. | `core/snr_binning.py`. |
| `tests/test_solar_system.py` | Solar-system query/detection tests. | `SolarSystemTest` and stub services | Prediction/visibility/detection changes. | `core/solar_system.py`, `core/settings.py`. |
| `tests/test_synthetic_tracking.py` | Synthetic-tracking tests. | `SyntheticTrackingTest` | Synthetic-tracking math/backend changes. | `core/synthetic_tracking.py`, `core/array_backend.py`, `core/solar_system.py`. |
| `tests/test_transient.py` | Transient finder tests. | `TransientFinderTest` and fake services | Transient detection changes. | `core/transient.py`, `core/wcs.py`, `core/settings.py`. |
| `tests/test_transient_label_dialog.py` | Transient quick-label dialog tests. | `TransientQuickLabelDialogTest` | Transient labeling UI changes. | `ui/transient_label_dialog.py`. |
| `tests/test_wcs.py` | WCS/astrometry client tests. | `AstrometryNetClientTest` | WCS solve/validation changes. | `core/wcs.py`. |
| `tests/test_workers.py` | Worker and preload concurrency tests. | `AsteroidDiscoveryWorkerTest`, `ImageDisplayPreloadWorkerTest`, `WorkerConcurrencyTest` | Worker thread/progress/cancel/preload changes. | `ui/workers.py`, `core/plotting.py`, `core/settings.py`, `core/discovery.py`. |

## UNCERTAIN

- `tests/test_main_window.py` is extremely large. The class names above are exact, but they understate the total helper/assertion surface inside the file.
- `photometry_app/ui/main_window.py` contains many internal helper methods and feature-specific nested flows beyond what this Phase 1 index can list compactly.