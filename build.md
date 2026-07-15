# Building an Installable Windows `.exe`

This guide explains how to build **Citizen Astronomy (CAst)** as a Windows executable and wrap it in an installable setup program.

The canonical release path uses:

- **PyInstaller** → one-folder application bundle (`CitizenAstronomyAlphaReview.exe` + `_internal\`)
- **Inno Setup 6** → single-file installer (`CitizenAstronomyAlphaReview-Alpha-Setup.exe`)

For dependency details, bundled assets, and validation expectations, see:

- `packaging/release_manifest.md`
- `packaging/ALPHA_REVIEW_RELEASE.md`
- `packaging/ALPHA_REVIEW_CLEAN_MACHINE_CHECKLIST.md`

---

## Prerequisites

Build on **Windows 10/11 x64** from the repository root.

| Requirement | Notes |
|-------------|-------|
| Python **3.11+** | Match `requires-python` in `pyproject.toml` |
| Git | To clone the repo |
| Large runtime assets | Required by `CitizenAstronomyAlphaReview.spec` (see below) |
| **PyInstaller** | Installed into the project virtual environment |
| **Inno Setup 6** | Optional, but required for the installable setup `.exe` (`ISCC.exe` on `PATH`) |

### Required asset trees

The alpha-review spec fails fast if these are missing:

- `photometry_app/data/`
- `assets/moon_tiles/`
- `textures/milky_way_tiles_32k_padded_lzw_benchmark/`
- `textures/moon_lroc_color_16bit_srgb_8k.tif`
- `textures/moon_ldem_16.tif`
- `textures/milkyway_2020_4k_preview.png`
- `textures/constellation_figures_4k.tif`
- `assets/citizen_astronomy.ico`

If you do not have the large texture/tile trees locally, obtain them before building. Sky View and Moon fidelity depend on them being present at build time.

---

## 1. Prepare the build environment

Open PowerShell in the project root:

```powershell
cd C:\Users\Kay\Desktop\Projects\Photometry

python -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
pip install -e .
pip install pyinstaller
```

Verify the app runs from source:

```powershell
python -m photometry_app.main
```

---

## 2. Generate packaging smoke fixtures

These small FITS/XISF files are used by automated packaging validation:

```powershell
.\.venv\Scripts\python.exe packaging\generate_smoke_fixtures.py
```

---

## 3. Build the application bundle (`.exe`)

Use the alpha-review spec. This is the supported shipping-style bundle:

```powershell
.\.venv\Scripts\python.exe -m PyInstaller `
  --noconfirm `
  --clean `
  --distpath _tmp_alpha_review_dist `
  --workpath _tmp_alpha_review_build `
  CitizenAstronomyAlphaReview.spec
```

### Build output

```text
_tmp_alpha_review_dist\CitizenAstronomyAlphaReview\
  CitizenAstronomyAlphaReview.exe
  _internal\
```

You can launch the bundle directly:

```powershell
.\_tmp_alpha_review_dist\CitizenAstronomyAlphaReview\CitizenAstronomyAlphaReview.exe
```

The bundle is large (hundreds of MB) because it includes Python, Qt, scientific libraries, and Sky View / Moon assets.

---

## 4. Validate the bundle (recommended)

Run compile and unit checks:

```powershell
.\.venv\Scripts\python.exe -m py_compile photometry_app\main.py photometry_app\core\packaged_format_smoke.py scripts\run_packaged_alpha_smoke.py

.\.venv\Scripts\python.exe -m pytest tests\test_qt_image_formats.py tests\test_packaged_format_smoke.py -q
```

Run packaged startup and format smoke:

```powershell
.\.venv\Scripts\python.exe scripts\run_packaged_alpha_smoke.py
```

Quick frozen format check:

```powershell
_tmp_alpha_review_dist\CitizenAstronomyAlphaReview\CitizenAstronomyAlphaReview.exe `
  --packaged-format-smoke `
  --packaged-format-smoke-fixtures packaging\fixtures `
  --packaged-format-smoke-output _tmp_packaged_format_smoke_result.json
```

Expected:

- `scripts/run_packaged_alpha_smoke.py` reports `"success": true`
- `_tmp_packaged_format_smoke_result.json` shows `xisf.success: true`
- No new `startup-error.log` under `%LOCALAPPDATA%\CitizenAstronomy\`

---

## 5. Build the installable setup `.exe`

Install **Inno Setup 6**, then compile the installer script:

```powershell
ISCC.exe packaging\inno\CitizenAstronomyAlphaReview.iss
```

The installer script expects the PyInstaller output at:

```text
_tmp_alpha_review_dist\CitizenAstronomyAlphaReview\
```

### Installer output

```text
packaging\dist\CitizenAstronomyAlphaReview-Alpha-Setup.exe
```

This is the file you distribute to reviewers or testers. It installs per-user (no admin required) to:

```text
%LOCALAPPDATA%\Programs\Citizen Astronomy (CAst) Alpha Review\
```

The installer also adds Start Menu shortcuts and an uninstall entry.

---

## 6. Test the installed application

After running the setup program on a clean Windows account or VM:

```powershell
& "$env:LOCALAPPDATA\Programs\Citizen Astronomy (CAst) Alpha Review\CitizenAstronomyAlphaReview.exe" `
  --packaged-format-smoke `
  --packaged-format-smoke-fixtures "C:\path\to\repo\packaging\fixtures"
```

Use `packaging/ALPHA_REVIEW_CLEAN_MACHINE_CHECKLIST.md` for the full clean-machine gate.

---

## Optional: developer debug bundle

For local debugging with a console window and a smaller asset set, use the legacy debug spec:

```powershell
.\.venv\Scripts\python.exe -m PyInstaller `
  --noconfirm `
  --clean `
  --distpath _tmp_shipping_dist `
  --workpath _tmp_shipping_build `
  CitizenPhotometryDebug.spec
```

Output:

```text
_tmp_shipping_dist\CitizenPhotometryDebug\CitizenPhotometryDebug.exe
```

This path is **not** the canonical alpha-review installer input. Prefer `CitizenAstronomyAlphaReview.spec` for distributable builds.

---

## Troubleshooting

| Issue | What to check |
|-------|----------------|
| PyInstaller fails on missing files | Confirm all asset paths listed in `CitizenAstronomyAlphaReview.spec` exist |
| Inno Setup cannot find bundle | Build step 3 first; path must be `_tmp_alpha_review_dist\CitizenAstronomyAlphaReview\` |
| Windows SmartScreen warning | Installer is unsigned; expected for private alpha builds |
| XISF or TIFF fails in frozen build | Re-run packaged smoke; confirm `qtiff.dll` exists under `_internal\PySide6\plugins\imageformats\` |
| Network catalog features fail | Internet required on first use; not all optional ephemeris kernels are bundled |

---

## Quick reference

| Artifact | Path |
|----------|------|
| App executable (folder bundle) | `_tmp_alpha_review_dist\CitizenAstronomyAlphaReview\CitizenAstronomyAlphaReview.exe` |
| Setup installer | `packaging\dist\CitizenAstronomyAlphaReview-Alpha-Setup.exe` |
| PyInstaller spec | `CitizenAstronomyAlphaReview.spec` |
| Inno Setup script | `packaging\inno\CitizenAstronomyAlphaReview.iss` |
