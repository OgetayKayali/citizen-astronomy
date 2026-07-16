# Citizen Astronomy Alpha Review — Release Manifest

Private alpha-review packaging audit. Reviewers must not need Python, pip, Qt, astroquery, xisf, or any other runtime dependency beyond what this installer ships.

## Canonical build output

- **PyInstaller one-folder bundle:** `CitizenAstronomyAlphaReview/` produced by `CitizenAstronomyAlphaReview.spec`
- **Installer input:** the tested one-folder bundle only; Inno Setup wraps it, it does not replace it

## Runtime Python dependencies

| Package | Import style | Required for | Bundling action |
|---------|--------------|--------------|-----------------|
| `xisf` | `try/except ImportError` in `photometry_app/core/image_io.py` | `.xisf` science frames | `hiddenimports`, `packaging/hooks/hook-xisf.py`, `copy_metadata('xisf')` |
| `lz4`, `lz4.block` | imported by `xisf.py` | XISF decompression | `collect_all('lz4')`, hook hiddenimports |
| `zstandard` | imported by `xisf.py` | XISF decompression | `collect_all('zstandard')`, hook hiddenimports |
| `astropy` | direct | FITS/WCS/time | PyInstaller transitive analysis |
| `Pillow` | direct | TIFF/PNG/JPEG via `image_io` | transitive |
| `PySide6` | direct | UI, Qt image plugins | PySide6 hook + `plugins/imageformats` |
| `astroquery.vizier` | direct | catalog queries | `hiddenimports` |
| `astroquery.simbad` | direct | object metadata | `hiddenimports` + `query_criteria_fields.json` data |
| `astroquery.ipac.nexsci.nasa_exoplanet_archive` | direct | exoplanet archive | `hiddenimports` |
| `astroquery.imcce` | optional `try/except` in `solar_system.py` | SkyBoT/Miriade | `hiddenimports` |
| `astroquery.jplhorizons` | optional `try/except` in `solar_system.py` | Horizons ephemerides | `hiddenimports` |
| `astroquery.jplsbdb` | optional `try/except` in `solar_system.py` | small-body DB | `hiddenimports` |
| `reproject` | optional `try/except` in `alignment.py` | WCS reprojection | `hiddenimports` |
| `scipy.ndimage` | optional `try/except` in `alignment.py` | alignment fallback | transitive via scipy |
| `imageio.v2` | optional `try/except` in `animation_export.py` | MP4 export | `hiddenimports` + `imageio-ffmpeg` binary |
| `pyqtgraph`, `pyqtgraph.opengl` | optional `try/except` in `dialogs.py` | 3D orbit views | `collect_all('pyqtgraph')` |
| `OpenGL`, `OpenGL_accelerate` | via pyqtgraph OpenGL | 3D rendering | `hiddenimports` |
| `sklearn.*` | optional/lazy in `hr_motion_groups.py`, `candidate_training.py` | motion groups / training | `hiddenimports` + sklearn tree |
| `spiceypy` | `importlib.import_module` in `moon_system.py` | optional high-precision Moon ephemeris | intentionally optional; not bundled |
| `cupy` | optional in `array_backend.py` | GPU arrays | excluded in spec |

## Dynamic / lazy imports

| Location | Mechanism | Target |
|----------|-----------|--------|
| `photometry_app/ui/moon_system.py` | `importlib.import_module` | `spiceypy` (optional) |
| `photometry_app/core/sky_atlas.py` | `importlib.resources.files` | packaged JSON under `photometry_app/data` |
| `photometry_app/core/candidate_training.py` | inline import | `sklearn.ensemble`, `sklearn.metrics`, `sklearn.model_selection` |
| `photometry_app/ui/light_curve_widget.py`, `hr_plot_widget.py` | inline import | `PySide6.QtSvg.QSvgGenerator` |

## File-format handlers

| Format | Handler module | Extensions | Frozen validation |
|--------|----------------|------------|-------------------|
| FITS | `image_io.py` + `astropy.io.fits` | `.fits`, `.fit` | `packaging/fixtures/smoke_tiny.fits` |
| XISF | `image_io.py` + `xisf.XISF` | `.xisf` | `packaging/fixtures/smoke_tiny.xisf` |
| TIFF/PNG/JPEG | `image_io.py` + `PIL.Image` | `.tif`, `.tiff`, `.png`, `.jpg`, `.jpeg` | Qt + PIL paths |
| Qt tile decode | `qt_image_formats.py`, `qt_image_format_smoke.py` | TIFF-LZW, PNG, WebP tiles | embedded TIFF-LZW sample + fixture files |
| GIF/MP4 export | `animation_export.py` | export only | not part of alpha startup smoke |

## Packaged data files (spec `datas`)

### Documentation and version

- `README.md`, `DOCUMENTATION.md`, `guides/hr_diagram.md`

### Icons

- `assets/citizen_astronomy.ico`

### Packaged JSON catalogs

- `photometry_app/data/constellations.json`
- `photometry_app/data/constellations.lines.json`
- `photometry_app/data/sky_atlas_bright_objects.json`
- `photometry_app/data/sky_atlas_star_names.json`

### Sky View / Moon runtime assets

- `textures/milkyway_2020_4k_preview.png`
- `textures/constellation_figures_4k.tif`
- `textures/moon_lroc_color_16bit_srgb_8k.tif`
- `textures/moon_ldem_16.tif`
- `textures/milky_way_tiles_32k_padded_lzw_benchmark/` (manifest + tile pyramid)
- `assets/moon_tiles/` (tile pyramid + `build_manifest.json`)

### Third-party package data

- `astroquery/CITATION`
- `astroquery/simbad/data/query_criteria_fields.json`
- `photutils/CITATION.rst`
- `copy_metadata('xisf')`, `copy_metadata('lz4')`, `copy_metadata('zstandard')`

## Qt plugins required at runtime

Bundled under `_internal/PySide6/plugins/`:

- `imageformats/qgif.dll`
- `imageformats/qjpeg.dll`
- `imageformats/qtiff.dll`
- `imageformats/qwebp.dll`
- `imageformats/qsvg.dll`
- `platforms/qwindows.dll`
- `styles/qmodernwindowsstyle.dll`

Validated by `--qt-image-format-smoke` and `--packaged-format-smoke`.

## User-writable runtime paths (not bundled)

- `%LOCALAPPDATA%\CitizenAstronomy\settings.json`
- `%LOCALAPPDATA%\CitizenAstronomy\state.json`
- `%LOCALAPPDATA%\CitizenAstronomy\startup-error.log` (created only on startup failure)
- Catalog / cache directories under the Citizen Astronomy app data root

## Network-only runtime data (not bundled)

- VizieR, Simbad, Horizons, SkyBoT, NASA Exoplanet Archive queries
- Astrometry.net plate solving
- Hipparcos full-sky cache download on first Sky View use
- Observer-location map tiles (HTTP PNG)

## PyInstaller spec summary

File: `CitizenAstronomyAlphaReview.spec`

- One-folder `COLLECT` output, windowed EXE, UPX enabled
- Custom hook path: `packaging/hooks/`
- `hook-xisf.py` bundles xisf metadata and lz4/zstandard binary libs
- `collect_all('lz4')`, `collect_all('zstandard')`, `collect_all('pyqtgraph')`
- Preserved Simbad `query_criteria_fields.json` fix
- Preserved Milky Way / Moon / constellation asset trees
- Excludes: `pytest`, `matplotlib.tests`, `astroquery.dace`, `cupy`

## Packaged smoke coverage

| Check | Command / harness |
|-------|---------------------|
| Source startup | `_tmp_startup_smoke.py` or `python -m photometry_app.main` |
| Packaged startup | `scripts/run_packaged_alpha_smoke.py` |
| Qt image plugins + TIFF-LZW | `--qt-image-format-smoke` |
| PNG / WebP / FITS / XISF | `--packaged-format-smoke` with `packaging/fixtures/` |
| About dialog path | `--about-dialog-smoke` |
| Frozen integration pytest | `tests/test_packaged_format_smoke.py` |

## Known alpha-review risks

1. Large texture/moon tile pyramids must be present before building; manifests alone are not enough for full Sky View visuals.
2. `spiceypy` is optional and not bundled; Moon ephemeris falls back to built-in paths.
3. MP4 export depends on `imageio-ffmpeg`; GIF/PNG export is the safer alpha path.
4. First-use networked catalog features require internet access and may be slow on first query.
5. Windows SmartScreen may warn on unsigned local installers; this is expected for private alpha builds.
