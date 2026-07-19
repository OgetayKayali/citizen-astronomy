# Citizen Astronomy Alpha Review — Clean-Machine Validation Checklist

Use this checklist on a **fresh Windows VM or clean local user account** before sending the private alpha installer to reviewers. This is the final gate after dev-account build and smoke validation.

**Installer under test:**

```text
packaging\dist\CitizenAstronomyAlphaReview-Alpha-Setup.exe
```

**Do not upload, sign, or publicly release this build.**

---

## 1. Environment requirements

Prepare a machine or account that has **none** of the following installed:

- [ ] Python (including Microsoft Store Python)
- [ ] Anaconda / Miniconda / conda
- [ ] Git
- [ ] Qt / PySide / PyQt
- [ ] pip astronomy stacks (astroquery, astropy dev tools, xisf, etc.)
- [ ] A prior Citizen Astronomy install
- [ ] A copied `_tmp_alpha_review_dist` folder from the build machine

Recommended:

- [ ] Windows 10 or 11 x64, fully updated
- [ ] Dedicated test VM snapshot, or a new Windows local user account
- [ ] Internet available (for optional networked catalog features only)
- [ ] Copy of the installer onto the test machine via USB/local share (not a public URL)
- [ ] Copy of `packaging\fixtures\` from the build machine (for automated format smoke)
- [ ] Optional: copy of `scripts\run_packaged_alpha_smoke.py` and the project `.venv` is **not** required on the clean machine if you run EXE smoke flags directly

Record:

| Field | Value |
|-------|-------|
| Machine label / VM name | |
| Windows version | |
| Test account name | |
| Test date | |
| Installer file size (bytes) | |
| Installer SHA256 (optional) | |

---

## 2. Publisher signature / SmartScreen check

Reviewer releases must be Authenticode-signed and timestamped. Open the Setup
file's **Properties → Digital Signatures** and confirm the expected publisher.
Treat a missing/invalid signature or a SmartScreen **Unknown publisher** warning
as a failed release gate; do not bypass it.

Record whether SmartScreen appeared: [ ] Yes  [ ] No

---

## 3. Install steps

1. Close any running `CitizenAstronomyAlphaReview.exe` instances on the test machine.
2. Run the generated Velopack `*Setup.exe`.
3. Confirm the per-user managed installation appears at:

   ```text
   %LOCALAPPDATA%\CitizenAstronomy.CAst\
   ```

4. Finish setup and confirm these files exist:

   - [ ] `CitizenAstronomyAlphaReview.exe`
   - [ ] `Update.exe`
   - [ ] `current\CitizenAstronomyAlphaReview.exe`
   - [ ] `current\_internal\` folder

Record installed path:

```text

```

---

## 4. Launch checks

Launch the app from each path you expect reviewers to use:

- [ ] Start Menu → **Citizen Astronomy (CAst) Alpha Review**
- [ ] Desktop shortcut (if created during install)
- [ ] Direct EXE path under `%LOCALAPPDATA%\Programs\...`

For each launch:

- [ ] Main window appears within ~15 seconds
- [ ] No crash dialog on startup
- [ ] `%LOCALAPPDATA%\CitizenAstronomy\startup-error.log` is **not** created

Open **Help → About**:

- [ ] Dialog opens without crash
- [ ] Text includes “Developed by Ogetay.”
- [ ] Text includes “Alpha-reviewer build only. Do not distribute.”

---

## 5. Automated installed-app smoke (recommended)

From the build machine, copy `packaging\fixtures\` to the clean machine.

### Option A — direct EXE smoke flags (no Python required)

```powershell
$exe = "$env:LOCALAPPDATA\CitizenAstronomy.CAst\CitizenAstronomyAlphaReview.exe"
$fixtures = "C:\path\copied\packaging\fixtures"
$out = "$env:USERPROFILE\Desktop\installed_format_smoke.json"

& $exe --packaged-format-smoke `
  --packaged-format-smoke-fixtures $fixtures `
  --packaged-format-smoke-output $out

& $exe --about-dialog-smoke
```

Pass criteria in `installed_format_smoke.json`:

- [ ] `"success": true`
- [ ] `qt_tiff_lzw.success: true`
- [ ] `png.success: true`
- [ ] `webp.success: true`
- [ ] `fits.success: true`
- [ ] `xisf_import.success: true`
- [ ] `xisf.success: true`
- [ ] `qt_image_support.has_tiff: true`
- [ ] `qt_image_support.has_png: true`
- [ ] `qt_image_support.has_webp: true`
- [ ] `imageformats_plugins` includes `qtiff.dll` and `qwebp.dll`

### Option B — wrapper script from repo

If the repo is available on the clean machine:

```powershell
.\packaging\run_clean_machine_smoke.ps1
```

Record smoke JSON path and overall result: [ ] Pass  [ ] Fail

---

## 6. Manual file-format checks (GUI)

Using **File → Open** (or equivalent workflow), open each sample on the clean machine:

| Format | Sample to try | Opens without error | Image/header visible |
|--------|---------------|---------------------|----------------------|
| FITS | `packaging\fixtures\smoke_tiny.fits` | [ ] | [ ] |
| XISF | `packaging\fixtures\smoke_tiny.xisf` | [ ] | [ ] |
| PNG | `packaging\fixtures\smoke_tiny.png` | [ ] | [ ] |
| TIFF | any small `.tif` / `.tiff` science frame you provide | [ ] | [ ] |
| JPEG | any small `.jpg` sample | [ ] | [ ] |

XISF is the highest-risk format on clean machines. A failure here usually means the frozen bundle is missing `xisf`, `lz4`, or `zstandard` support.

---

## 7. Sky Atlas manual checks

Open Sky Atlas / Sky View (wording may vary by build):

- [ ] Sky Atlas panel opens without crash
- [ ] Packaged named targets appear
- [ ] Search finds at least one known object (e.g. “M42”, “Orion”, “Sirius”)
- [ ] Selecting an object does not crash the UI

Optional networked check (requires internet):

- [ ] First-time brighter catalog load completes or fails gracefully with a visible message (not a silent hang)

---

## 8. Milky Way / Moon / constellation toggle checks

Open Sky View and verify packaged visual layers:

### Milky Way

- [ ] Open Sky View
- [ ] Enable Milky Way layer
- [ ] Tiled background renders (not a flat empty panel)
- [ ] No repeated “tile decode failed” or CPU-fallback spam in the UI
- [ ] Disable Milky Way cleanly

### Moon

- [ ] Enable Moon layer
- [ ] Moon texture renders
- [ ] Disable Moon cleanly

### Constellations

- [ ] Enable constellation overlay
- [ ] Lines/figures appear over the sky background
- [ ] Disable constellation overlay cleanly

Record any missing textures, black tiles, or OpenGL fallback warnings:

```text

```

---

## 9. Exit and relaunch

- [ ] Close the app from the window controls
- [ ] Relaunch from Start Menu
- [ ] Second launch still succeeds
- [ ] No new `startup-error.log`

---

## 10. Uninstall checks

**Important:** close the app completely before uninstalling. Uninstall while the EXE is running can leave locked DLLs behind.

1. Confirm `CitizenAstronomyAlphaReview.exe` is not running.
2. Run **Uninstall Citizen Astronomy (CAst)** from Start Menu (or Windows Settings → Apps).
3. Verify:

   - [ ] Install folder under `%LOCALAPPDATA%\CitizenAstronomy.CAst\` is removed
   - [ ] Start Menu shortcut is removed
   - [ ] Desktop shortcut is removed (if one was created)

### User settings survive uninstall by design

These paths are **not** removed by the alpha installer and may remain after uninstall:

```text
%LOCALAPPDATA%\CitizenPhotometry\
%LOCALAPPDATA%\CitizenAstronomy\startup-error.log
```

This is expected. A reinstall on the same account may reuse prior settings.

---

## 11. Final pass / fail

| Gate | Result |
|------|--------|
| Clean environment confirmed | [ ] Pass [ ] Fail |
| Install succeeded | [ ] Pass [ ] Fail |
| Launch / About | [ ] Pass [ ] Fail |
| Installed automated smoke | [ ] Pass [ ] Fail |
| FITS / XISF / PNG / WebP / TIFF manual open | [ ] Pass [ ] Fail |
| Sky Atlas manual checks | [ ] Pass [ ] Fail |
| Milky Way / Moon / constellation toggles | [ ] Pass [ ] Fail |
| Clean uninstall (app closed first) | [ ] Pass [ ] Fail |

**Overall clean-machine result:** [ ] APPROVED FOR ALPHA SEND  [ ] BLOCKED

If blocked, file findings in `packaging/ALPHA_REVIEW_VALIDATION_REPORT_TEMPLATE.md` and do not send the installer until resolved.

---

## Quick reference commands

```powershell
# Installed EXE path
$exe = "$env:LOCALAPPDATA\CitizenAstronomy.CAst\CitizenAstronomyAlphaReview.exe"

# Startup-error log
$log = "$env:LOCALAPPDATA\CitizenAstronomy\startup-error.log"

# Check app not running before uninstall
Get-Process -Name 'CitizenAstronomyAlphaReview' -ErrorAction SilentlyContinue
```
