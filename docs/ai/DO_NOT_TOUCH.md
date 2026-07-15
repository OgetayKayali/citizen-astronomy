# DO_NOT_TOUCH

This file is about token discipline, not absolute prohibition. Future agents should usually skip these paths unless the user explicitly asks for them.

## Generated, Cached, Temporary, Or Binary Surfaces

- `.venv/`
  - Virtual environment.
- `__pycache__/`, `.pytest_cache/`
  - Interpreter/test cache.
- `.photometry-cache/`
  - Runtime cache data.
- `benchmark_results*/`
  - Generated benchmark outputs.
- `Exports/`
  - User/generated exports.
- `Files/`
  - User datasets.
- `images/`, `textures/`
  - Binary/image assets, including Milky Way tile trees.
- `_tmp*`
  - Temporary probes, validators, and one-off diagnostics.
- `*.log`, `*_stdout.txt`, `*_stderr.txt`, `*_output.txt`, `*_result.json`
  - Generated logs and captured run artifacts.

## Low-Priority Documentation/Packaging Surfaces

- `docs/paper/`
  - Paper build pipeline, not app runtime.
- `docs/screenshots/`
  - Generated/supporting documentation assets.
- `CitizenPhotometryDebug.spec`
  - Packaging only. Touch this only for PyInstaller work.

## Fragile High-Coupling Source Files

These are real source files, but agents should avoid starting here unless the task clearly belongs to them.

- `photometry_app/ui/main_window.py`
  - Huge integration hub. Start in a more local `core/*` or `ui/*` owner when possible, then come back here for wiring.
- `photometry_app/ui/moon_system.py`
  - Large specialized Moon subsystem. Avoid unless the task is explicitly about Moon state, Moon tiles, or Moon rendering.
- `photometry_app/ui/sky_view_milky_way_gl.py`
  - Specialized GL/cache path. Avoid for non-Milky-Way tasks.
- `photometry_app/ui/workers.py`
  - High fan-out background execution boundary. Avoid unless the issue is threading, progress, cancellation, or worker orchestration.

## Suggested First-Scan Paths Instead

Before touching the high-cost surfaces above, usually scan these first:

- `photometry_app/main.py`
- `photometry_app/core/settings.py`
- `photometry_app/core/pipeline.py`
- `photometry_app/core/image_io.py`
- `photometry_app/core/wcs.py`
- the feature-local owner from `FEATURE_ROUTING.md`

## UNCERTAIN

- `tests/test_sky_view_lzw_visual_smoke.py` is a real test file but it depends on a temporary `_tmp` smoke helper. Treat it as a secondary surface unless the task is specifically about that smoke harness.