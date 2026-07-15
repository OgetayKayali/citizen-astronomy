# PROJECT_MAP

Scope: Phase 1 map for the live application code, rendering stack, tests, scripts, and configuration. This file intentionally skips generated outputs, caches, datasets, image tiles, benchmark result folders, logs, and `_tmp*` helpers.

## Top-Level Layout

- `photometry_app/main.py`
  - Process entrypoint.
  - Starts Qt, handles startup crash logging, supports the `--qt-image-format-smoke` diagnostic mode, and creates `MainWindow`.
- `photometry_app/ui/main_window.py`
  - Main UI orchestrator and the densest integration hub.
  - Owns `MainWindow`, the Sky View widget stack, multiple app modes, OpenGL helper layers, timing/detail dialogs, and mode-to-worker wiring.
- `photometry_app/core/`
  - Non-UI computation, persistence, file I/O, catalogs, WCS, photometry, export, discovery, solar-system logic, and benchmarking.
- `photometry_app/ui/`
  - Reusable UI widgets and specialized rendering subsystems.
  - The most important Phase 1 files are `main_window.py`, `moon_system.py`, `sky_view_milky_way_gl.py`, `constellation_overlay.py`, `sky_view_location.py`, `sky_view_simulation.py`, and `workers.py`.
- `tests/`
  - `unittest` coverage for core logic, widgets, rendering helpers, settings, and smoke-style regressions.
- `scripts/`
  - Asset builders, validation scripts, and benchmark harnesses.
- `pyproject.toml`, `CitizenPhotometryDebug.spec`, `.vscode/settings.json`, `.vscode/tasks.json`
  - Packaging, runtime dependencies, editor defaults, and local task entrypoints.

## Runtime Architecture

### Startup

1. `photometry_app/main.py:main()` parses CLI flags.
2. It configures Qt shared OpenGL contexts before creating `QApplication`.
3. It runs `cleanup_stale_discovery_temp_cache()` from `photometry_app/core/discovery.py`.
4. It constructs `photometry_app/ui/main_window.py:MainWindow`.

### Main UI Split

- `MainWindow` is the controller/orchestrator.
- `photometry_app/core/*` files hold most data and computation contracts.
- `photometry_app/ui/workers.py` wraps long-running core work in `QThread` workers.
- Settings persistence flows through `photometry_app/core/settings.py:AppSettings`.

### Sky View Stack

Sky View is the most specialized rendering subsystem in this repo.

- `photometry_app/ui/main_window.py`
  - `_SkyAtlasViewWidget(QOpenGLWidget)` is the active Sky View canvas.
  - `_QPainterSkyViewRenderer` is the main painter-based render path.
  - `_OpenGLFaintStarLayer`, `_OpenGLStarFieldLayer`, `_OpenGLSolidGroundLayer`, and `_OpenGLAtmosphereMaskLayer` are optional or specialized GL passes.
  - `_SkyViewTimingWindow`, `_SkyViewMilkyWayDetailWindow`, `_SkyViewGridFamilyDetailWindow`, and `_SkyViewGroundDetailWindow` expose diagnostics.
- `photometry_app/core/sky_atlas.py`
  - Loads packaged named objects and optional cached Hipparcos stars for Sky View searches and star-field population.
- `photometry_app/core/solar_system.py`
  - Supplies known-object predictions and visibility estimates.
- `photometry_app/ui/constellation_overlay.py`
  - Loads and renders constellation lines and labels.
- `photometry_app/ui/sky_view_milky_way_gl.py`
  - Handles Milky Way tile decode, preparation, texture caching, and GL draw submission.
- `photometry_app/core/milky_way_assets.py`
  - Resolves Milky Way manifests, level selection, UV/tile math, and runtime asset suitability.
- `photometry_app/core/milky_way_mask.py`
  - Builds and applies alpha masks for Milky Way textures.
- `photometry_app/ui/moon_system.py`
  - Moon ephemeris, orientation-provider selection, async sampling, tile management, and moon GL rendering.
- `photometry_app/ui/sky_view_location.py`
  - Observer location dialog and tile-backed map widget.
- `photometry_app/ui/sky_view_simulation.py`
  - `SkyViewSimulationClock` for simulation time flow.

## Non-Sky-View Application Surfaces

- Differential photometry pipeline
  - `photometry_app/core/scanner.py`, `image_io.py`, `wcs.py`, `photometry.py`, `matching.py`, `pipeline.py`, `exporters.py`, `plotting.py`.
- H-R diagram workflow
  - `photometry_app/core/hr_diagram.py`, `hr_motion_groups.py`, `photometry_app/ui/hr_plot_widget.py`.
- Asteroid/comet and transient workflows
  - `photometry_app/core/discovery.py`, `synthetic_tracking.py`, `solar_system.py`, `transient.py`, plus worker wiring in `photometry_app/ui/workers.py` and dialogs in `photometry_app/ui/dialogs.py`.
- Sky Explorer
  - `photometry_app/core/sky_explorer.py` with `SkyExplorerWorker` in `photometry_app/ui/workers.py` and UI wiring in `MainWindow`.

## File Ownership Heuristics

- Start in `photometry_app/main.py` only for process startup, CLI flags, or crash/startup issues.
- Start in `photometry_app/ui/main_window.py` when the problem is about mode activation, widget wiring, Sky View interaction, or cross-module UI flow.
- Start in `photometry_app/core/settings.py` when the problem is persistence or user-configurable defaults.
- Start in a `photometry_app/core/*` module when the task is algorithmic, file-format, astrometry, photometry, catalog, or export logic.
- Start in `photometry_app/ui/workers.py` when the bug is about background execution, progress, cancellation, or thread handoff.
- Start in `tests/` after locating the owning feature file; the tests are mostly feature-focused rather than framework-generic.

## What To Skip First

Skip these until the task explicitly requires them:

- `.venv/`, `__pycache__/`, `.pytest_cache/`
- `.photometry-cache/`
- `benchmark_results*/`
- `Exports/`, `Files/`, `images/`, `textures/`
- `_tmp*`
- logs such as `pyinstaller-*.log`, `*_output.txt`, `*_stderr.txt`, `*_stdout.txt`

## UNCERTAIN

- `photometry_app/ui/main_window.py` owns more feature-specific helper methods than are practical to summarize here; treat it as the integration hub, then hop to the imported `core/*` or `ui/*` module that actually computes the behavior.
- Some non-Sky-View mode wiring inside `MainWindow` is only partially mapped in this Phase 1 pass because the file is extremely large and the user requested a token-saving front door, not a full internal method catalog.