# FEATURE_ROUTING

Scope: Phase 1 feature-to-file routing for the active app/runtime surfaces. Skip caches, generated outputs, datasets, binary assets, and `_tmp*` helpers unless the task explicitly requires them.

## Sky View main UI

- Primary files
  - `photometry_app/ui/main_window.py` (`MainWindow`, `_SkyAtlasViewWidget`, `_SkyAtlasOverlayContainer`, `_SkyAtlasPopupOverlay`, `_SkyAtlasModalOverlay`)
- Secondary files
  - `photometry_app/ui/sky_view_location.py`
  - `photometry_app/ui/sky_view_simulation.py`
  - `photometry_app/core/settings.py`
- Where to start
  - Start in `_SkyAtlasViewWidget` for canvas behavior.
  - Start in `MainWindow` for mode activation, control wiring, or settings application.
- What not to touch unless necessary
  - `_LegacySkyViewObservingSiteSelection`, `_LegacySkyViewLocationMapWidget`, `_LegacySkyViewLocationDialog` in `main_window.py`.
- Relevant tests
  - `tests/test_main_window.py`
  - `tests/test_main_window_image_panel.py`
  - `tests/test_sky_view_simulation.py`

## Star rendering

- Primary files
  - `photometry_app/ui/main_window.py` (`_QPainterSkyViewRenderer`, `_OpenGLFaintStarLayer`, `_OpenGLStarFieldLayer`, `_SkyAtlasViewWidget`)
  - `photometry_app/core/sky_atlas.py` (`SkyAtlasObject`, `load_sky_atlas_objects`, `load_scientific_sky_atlas_star_objects`, `search_sky_atlas_objects`)
- Secondary files
  - `photometry_app/core/benchmarking.py`
  - `scripts/run_sky_view_benchmark.py`
- Where to start
  - Start in `_QPainterSkyViewRenderer` for the default visible path.
  - Start in `_OpenGLStarFieldLayer` only when the issue is specific to the GL star path.
  - Start in `sky_atlas.py` when the problem is star catalog content, naming, or cache population.
- What not to touch unless necessary
  - GL star helpers in `main_window.py` if the task is catalog selection or label/search logic rather than rendering.
- Relevant tests
  - `tests/test_main_window.py`
  - `tests/test_sky_atlas.py`
  - `tests/test_benchmarking.py`

## Milky Way rendering

- Primary files
  - `photometry_app/ui/sky_view_milky_way_gl.py` (`OpenGLMilkyWayLayer`, `SkyViewMilkyWayGlUniforms`, `SkyViewMilkyWayTileDrawRequest`)
  - `photometry_app/core/milky_way_assets.py` (`MilkyWayAssetResolver`, `MilkyWayTileManifest`, `select_milky_way_level_with_hysteresis`)
  - `photometry_app/core/milky_way_mask.py` (`MilkyWayMaskParameters`, `apply_milky_way_alpha_mask`)
  - `photometry_app/ui/main_window.py` (`_SkyViewMilkyWay*` classes and integration)
- Secondary files
  - `photometry_app/core/milky_way_tile_generator.py`
  - `photometry_app/core/milky_way_tile_format_benchmark.py`
  - `scripts/generate_milky_way_tiles.py`
  - `scripts/preprocess_milky_way_texture.py`
- Where to start
  - Start in `milky_way_assets.py` for manifest resolution, tile math, or level selection.
  - Start in `sky_view_milky_way_gl.py` for tile decode/upload/cache/draw problems.
  - Start in `main_window.py` only when the bug is scene integration, layer ordering, or Sky View control flow.
- What not to touch unless necessary
  - `textures/` tile trees and benchmark-output folders.
  - `_tmp_sky_view_*` validators unless the task is explicitly about those tools.
- Relevant tests
  - `tests/test_milky_way_assets.py`
  - `tests/test_sky_view_milky_way_gl.py`
  - `tests/test_sky_view_milky_way_pixel_probe_support.py`
  - `tests/test_milky_way_tile_format_benchmark.py`
  - `tests/test_sky_view_lzw_visual_smoke.py`

## Moon rendering

- Primary files
  - `photometry_app/ui/moon_system.py` (`MoonEphemeris`, `MoonAsyncEphemerisSampler`, `MoonCache`, `MoonTiledTextureManager`, `MoonRendererGL`, `select_lunar_orientation_provider`)
  - `photometry_app/ui/main_window.py` (Moon integration inside Sky View)
- Secondary files
  - `scripts/build_moon_tiles.py`
  - `scripts/check_moon_spice.py`
  - `scripts/moon_visual_smoke.py`
- Where to start
  - Start in `moon_system.py` for nearly all Moon behavior.
  - Start in `main_window.py` only when the issue is camera/UI integration rather than Moon state or rendering internals.
- What not to touch unless necessary
  - `assets/moon_tiles/`, `assets/spice/`, and generated Moon tile trees.
  - Existing Moon phase documents under `docs/` unless the task is documentation or audit work.
- Relevant tests
  - `tests/test_moon_system.py`
  - `tests/test_moon_visual_smoke.py`
  - `tests/test_build_moon_tiles.py`

## Constellation lines and labels

- Primary files
  - `photometry_app/ui/constellation_overlay.py` (`ConstellationDataLoader`, `ConstellationLineRenderer`, `ConstellationLabelRenderer`, `ConstellationOverlay`)
  - `photometry_app/ui/main_window.py` (overlay integration)
- Secondary files
  - `photometry_app/core/benchmarking.py`
- Where to start
  - Start in `constellation_overlay.py`; it is the local owner.
- What not to touch unless necessary
  - `photometry_app/ui/main_window.py` render plumbing if the bug is inside line/label projection or styling.
- Relevant tests
  - `tests/test_constellation_overlay.py`

## Grid rendering

- Primary files
  - `photometry_app/ui/main_window.py` (`_SkyViewProjectedGridCacheEntry`, `_SkyViewGridFamilyDetailWindow`, `_QPainterSkyViewRenderer`, `_SkyAtlasViewWidget`)
- Secondary files
  - `photometry_app/core/settings.py`
  - `photometry_app/core/benchmarking.py`
- Where to start
  - Start in `main_window.py`; grid logic is integrated there.
- What not to touch unless necessary
  - unrelated overlays such as Milky Way or Moon subsystems.
- Relevant tests
  - `tests/test_main_window.py`
  - `tests/test_benchmarking.py`
  - `UNCERTAIN: no grid-only dedicated test file was found in this Phase 1 pass.`

## Ground/horizon rendering

- Primary files
  - `photometry_app/ui/main_window.py` (`_SkyGroundViewportGeometry`, `_SkyRenderedGroundLayerCacheEntry`, `_OpenGLSolidGroundLayer`, `_OpenGLAtmosphereMaskLayer`, `_SkyViewGroundDetailWindow`)
- Secondary files
  - `photometry_app/core/benchmarking.py`
  - `scripts/run_sky_view_benchmark.py`
- Where to start
  - Start in `main_window.py`; both geometry and layer integration live there.
- What not to touch unless necessary
  - GL helpers when the issue is scene state or visibility math rather than the GL pass itself.
- Relevant tests
  - `tests/test_main_window.py`
  - `UNCERTAIN: no ground/horizon-specific dedicated test file was found in this Phase 1 pass.`

## Time simulation

- Primary files
  - `photometry_app/ui/sky_view_simulation.py` (`SkyViewSimulationClock`)
  - `photometry_app/ui/main_window.py` (timer wiring and controls)
- Secondary files
  - `photometry_app/core/settings.py`
- Where to start
  - Start in `SkyViewSimulationClock` for time flow semantics.
  - Start in `MainWindow` for play/pause buttons, scrubbing, or control wiring.
- What not to touch unless necessary
  - render-layer code if the issue is only the simulation clock state.
- Relevant tests
  - `tests/test_sky_view_simulation.py`
  - `tests/test_main_window.py`

## Camera controls

- Primary files
  - `photometry_app/ui/main_window.py` (`_SkyAtlasViewWidget` and related Sky View helper classes)
- Secondary files
  - `photometry_app/ui/moon_system.py` for Moon-focus-specific clamping/state interactions
  - `photometry_app/core/sky_atlas.py` for object targeting/search source data
- Where to start
  - Start in `_SkyAtlasViewWidget` for drag, wheel, field width, target selection, or view-state issues.
- What not to touch unless necessary
  - Milky Way and Moon renderers if the issue is navigation math rather than layer output.
- Relevant tests
  - `tests/test_main_window.py`
  - `UNCERTAIN: no dedicated camera-control test module was found in this Phase 1 pass.`

## Settings/UI controls

- Primary files
  - `photometry_app/core/settings.py` (`AppSettings`)
  - `photometry_app/ui/dialogs.py` (`SettingsDialog`, `LightCurveFilterDialog`, `ResultsViewFilterDialog`, `ThemeCustomizeDialog`, and related dialogs)
  - `photometry_app/ui/main_window.py` (UI wiring and application of settings)
- Secondary files
  - `photometry_app/ui/levels_dialog.py`
  - `photometry_app/ui/levels_strip.py`
  - `photometry_app/ui/image_view.py`
  - `photometry_app/ui/light_curve_widget.py`
- Where to start
  - Start in `settings.py` for persistence or defaults.
  - Start in `dialogs.py` for actual control widgets.
  - Start in `main_window.py` for signal wiring or mode-specific application.
- What not to touch unless necessary
  - `main_window.py` if the bug is a bad stored value rather than UI event wiring.
- Relevant tests
  - `tests/test_settings.py`
  - `tests/test_image_view.py`
  - `tests/test_light_curve_widget.py`
  - `tests/test_main_window.py`

## Performance diagnostics

- Primary files
  - `photometry_app/core/benchmarking.py` (`BenchmarkRecorder`, `get_benchmark_recorder`, `reset_global_benchmark_recorder`)
  - `photometry_app/ui/main_window.py` (`_SkyViewTimingWindow`, `_SkyViewMilkyWayDetailWindow`, `_SkyViewGridFamilyDetailWindow`, `_SkyViewGroundDetailWindow`, `_SkyViewCollectDetailWindow`)
  - `scripts/run_sky_view_benchmark.py` (`SkyViewBenchmarkScenario`, `SkyViewBenchmarkHarness`)
- Secondary files
  - `photometry_app/core/discovery_benchmark.py`
  - `scripts/profile_discover_run.py`
- Where to start
  - Start in `benchmarking.py` for recorder semantics.
  - Start in `run_sky_view_benchmark.py` for scenario/harness behavior.
  - Start in `main_window.py` for on-screen detail windows.
- What not to touch unless necessary
  - `benchmark_results*/` and `_tmp_*benchmark*` outputs.
- Relevant tests
  - `tests/test_benchmarking.py`
  - `tests/test_discovery_benchmark.py`
  - `tests/test_main_window.py`

## Tests

- Primary files
  - Rendering/UI: `tests/test_main_window.py`, `tests/test_main_window_image_panel.py`, `tests/test_constellation_overlay.py`, `tests/test_sky_view_milky_way_gl.py`, `tests/test_moon_system.py`, `tests/test_sky_view_simulation.py`
  - Core pipeline: `tests/test_pipeline_integration.py`, `tests/test_photometry.py`, `tests/test_wcs.py`, `tests/test_settings.py`, `tests/test_image_io.py`
  - Performance/asset helpers: `tests/test_benchmarking.py`, `tests/test_milky_way_assets.py`, `tests/test_milky_way_tile_format_benchmark.py`, `tests/test_moon_visual_smoke.py`
- Secondary files
  - `tests/test_workers.py`, `tests/test_solar_system.py`, `tests/test_sky_explorer.py`, `tests/test_transient.py`, `tests/test_discovery.py`
- Where to start
  - Start from the test file closest to the feature owner named above.
- What not to touch unless necessary
  - smoke tests that depend on temporary `_tmp*` helpers unless the task is explicitly about those harnesses.
