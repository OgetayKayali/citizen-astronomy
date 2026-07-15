# CURRENT_ARCHITECTURE

Scope: current runtime/data-flow summary for the live app, with emphasis on the rendering path and the code surfaces future agents should inspect first.

## 1. Process Startup

- `photometry_app/main.py:main()` is the process entrypoint.
- Startup responsibilities:
  - parse CLI flags with `_parse_cli_args()`
  - support the `--qt-image-format-smoke` path through `photometry_app/core/qt_image_format_smoke.py`
  - enable Qt shared GL contexts with `_configure_qt_application_attributes()`
  - clean stale discovery temp cache via `photometry_app/core/discovery.py:cleanup_stale_discovery_temp_cache`
  - create `QApplication`
  - construct and show `photometry_app/ui/main_window.py:MainWindow`

## 2. Core Application Data Flow

For the main photometry workflow, the current architecture is:

1. `photometry_app/core/scanner.py:scan_fits_tree()` discovers candidate image files and extracts metadata.
2. `photometry_app/core/image_io.py` reads headers, shapes, and pixel data across FITS, XISF, and standard raster formats.
3. `photometry_app/core/wcs.py` validates or solves WCS using `AstrometryNetClient` and related helpers.
4. `photometry_app/core/photometry.py` measures sources.
5. `photometry_app/core/matching.py` builds differential results and light-curve series.
6. `photometry_app/core/pipeline.py:PhotometryPipeline` coordinates settings, scanning, catalog cache clearing, photometry execution, run-history loading, and cached report logic.
7. `photometry_app/core/exporters.py` and `photometry_app/core/plotting.py` generate user-visible outputs.
8. `photometry_app/ui/workers.py` moves these flows off the UI thread.

## 3. Settings And Persistence

- `photometry_app/core/settings.py:AppSettings` is the persistent configuration contract.
- `MainWindow` reads and applies settings, but `settings.py` is the authoritative schema.
- The file also contains coercion helpers for saved UI state, Sky Explorer options, theme values, observing-site presets, manual configs, and image display settings.

If a setting fails to persist or reload, start in `settings.py` before changing `MainWindow`.

## 4. Sky View Rendering Pipeline

The current Sky View path is a mixed controller + scene-building + layered renderer design.

### 4.1 State Inputs

- `photometry_app/ui/main_window.py:MainWindow`
  - owns app mode, widget creation, controls, timers, selected objects, and settings application.
- `photometry_app/ui/sky_view_simulation.py:SkyViewSimulationClock`
  - provides simulation time based on a monotonic clock.
- `photometry_app/ui/sky_view_location.py`
  - captures observer latitude, longitude, elevation, presets, and reverse-geocoded labels.
- `photometry_app/core/sky_atlas.py`
  - provides named objects and optional scientific star catalogs.
- `photometry_app/core/solar_system.py`
  - supplies known solar-system detections and visibility data.

### 4.2 Scene Collection

- `_SkyAtlasViewWidget` inside `photometry_app/ui/main_window.py` is the active canvas.
- It collects a `_SkyViewRenderScene` and related temporary state.
- That collection stage decides which stars, sky objects, constellation segments, Milky Way tiles, Moon state, grid families, and ground/horizon geometry are needed for the current view.

### 4.3 Sky Layers

The current layer ownership is split like this:

- Stars and general sky overlays
  - primary owner: `photometry_app/ui/main_window.py`
  - key classes: `_QPainterSkyViewRenderer`, `_OpenGLFaintStarLayer`, `_OpenGLStarFieldLayer`
- Constellations
  - primary owner: `photometry_app/ui/constellation_overlay.py`
  - integrated from `MainWindow` / `_SkyAtlasViewWidget`
- Milky Way
  - integration: `photometry_app/ui/main_window.py`
  - asset selection and tile math: `photometry_app/core/milky_way_assets.py`
  - alpha-mask logic: `photometry_app/core/milky_way_mask.py`
  - GL texture/cache/render path: `photometry_app/ui/sky_view_milky_way_gl.py:OpenGLMilkyWayLayer`
- Moon
  - astronomy + state: `photometry_app/ui/moon_system.py:MoonEphemeris`, `MoonAsyncEphemerisSampler`
  - texture/tile cache: `MoonCache`, `MoonTiledTextureManager`
  - GL drawing: `MoonRendererGL`
- Ground and atmosphere
  - integration and geometry: `photometry_app/ui/main_window.py`
  - optional GL helpers: `_OpenGLSolidGroundLayer`, `_OpenGLAtmosphereMaskLayer`

### 4.4 Draw Path

The renderer stack is not a pure GL pipeline.

- `QPainter` remains a first-class render path through `_QPainterSkyViewRenderer`.
- Specialized GL helpers exist for some layers.
- Milky Way and Moon have dedicated GL-oriented subsystems.
- Diagnostics windows inside `main_window.py` report timing and detail-state for specific layer families.

## 5. Milky Way Data Flow

1. `photometry_app/core/milky_way_assets.py:MilkyWayAssetResolver` resolves whether runtime should use a global texture or a tile pyramid manifest.
2. The same module computes UV bounds, tile indices, RA/Dec bounds, level selection, and hysteresis.
3. `photometry_app/ui/main_window.py` prepares `_SkyViewMilkyWay*` scene metadata and tile mesh requests.
4. `photometry_app/ui/sky_view_milky_way_gl.py:OpenGLMilkyWayLayer` decodes tiles, prepares upload images, manages LRU caches, and issues GL draw requests.
5. `photometry_app/core/milky_way_mask.py` is applied where alpha-mask behavior is needed.

## 6. Moon Data Flow

1. `photometry_app/ui/moon_system.py:MoonEphemeris` computes astronomical Moon state.
2. `select_lunar_orientation_provider()` picks approximate or SPICE-backed orientation logic.
3. `MoonAsyncEphemerisSampler` allows async state generation.
4. `MoonCache` and `MoonTiledTextureManager` prepare global or tiled texture resources.
5. `MoonRendererGL` consumes `MoonDrawState` and texture bindings; the intent is for astronomy to stay outside the renderer.

## 7. Background Execution Architecture

- `photometry_app/ui/workers.py` is the main background-execution boundary.
- It wraps:
  - pipeline scan/process/report work
  - Sky Explorer execution
  - solar-system detection
  - transient search
  - calibration
  - asteroid alignment, synthetic tracking, discovery, and recovery
  - image-display preload
  - period/literature/comparison-fit tasks
  - SNR binning helpers

If a change needs cancellation, progress, or UI-thread-safe result delivery, start in `workers.py` before adding ad hoc threads elsewhere.

## 8. Test And Benchmark Flow

- `tests/` uses `unittest` and covers both core and UI helpers.
- `photometry_app/core/benchmarking.py` owns the benchmark recorder contract and environment-flag enablement.
- `scripts/run_sky_view_benchmark.py` is the main benchmark harness for Sky View timing.
- `tests/test_benchmarking.py`, `tests/test_discovery_benchmark.py`, and renderer-specific tests verify pieces of that contract.

## UNCERTAIN

- The exact per-frame call order among all Sky View draw helpers inside `photometry_app/ui/main_window.py` is not exhaustively enumerated here because that file is very large and mixes many internal helper layers.
- `MainWindow` contains multiple non-Sky-View mode flows that are not fully expanded in this document; for those modes, use `FILE_INDEX.md` to jump into the more local `core/*`, `ui/dialogs.py`, and `ui/workers.py` owners.