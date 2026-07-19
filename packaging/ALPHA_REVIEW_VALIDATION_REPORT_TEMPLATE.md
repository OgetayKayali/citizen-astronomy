# Citizen Astronomy Alpha Review — Validation Report

**Private alpha only. Do not distribute.**

---

## Report metadata

| Field | Value |
|-------|-------|
| Report date | |
| Reporter | |
| Build label / git commit (if known) | |
| Installer path | `packaging\dist\CitizenAstronomyAlphaReview-Alpha-Setup.exe` |
| Installer size (bytes) | |
| Installer SHA256 (optional) | |
| Bundle source used by Inno Setup | `_tmp_alpha_review_dist\CitizenAstronomyAlphaReview\` |
| Test environment | [ ] Clean VM  [ ] Clean local account  [ ] Dev account |

---

## Environment description

| Requirement | Present on test machine? |
|-------------|--------------------------|
| No Python | [ ] Yes [ ] No |
| No Anaconda | [ ] Yes [ ] No |
| No Git | [ ] Yes [ ] No |
| No Qt install | [ ] Yes [ ] No |
| No manual xisf/astroquery install | [ ] Yes [ ] No |
| No prior Citizen Astronomy install | [ ] Yes [ ] No |

Windows version:

```text

```

Machine / VM name:

```text

```

---

## Build-machine validation (pre-send)

| Check | Command / artifact | Result | Notes |
|-------|-------------------|--------|-------|
| Canonical PyInstaller rebuild | `pyinstaller ... CitizenAstronomyAlphaReview.spec` | [ ] Pass [ ] Fail | |
| Pre-install packaged smoke | `scripts\run_packaged_alpha_smoke.py` | [ ] Pass [ ] Fail | |
| Velopack full/delta pack | `vpk pack --channel alpha --delta BestSize ...` | [ ] Pass [ ] Fail | |
| Signed Setup/full/delta/feed exist | `packaging\dist\velopack\` | [ ] Pass [ ] Fail | |

---

## Clean-machine / installed validation

| Check | Result | Notes |
|-------|--------|-------|
| Authenticode signature valid / expected publisher | [ ] Pass [ ] Fail | |
| Install succeeded | [ ] Pass [ ] Fail | |
| Start Menu shortcut works | [ ] Pass [ ] Fail | |
| Desktop shortcut works (if created) | [ ] Pass [ ] N/A | |
| Launch from installed EXE | [ ] Pass [ ] Fail | |
| About dialog opens | [ ] Pass [ ] Fail | |
| No `startup-error.log` after launch | [ ] Pass [ ] Fail | |
| Installed automated smoke | [ ] Pass [ ] Fail | JSON path: |
| FITS load | [ ] Pass [ ] Fail | |
| XISF load | [ ] Pass [ ] Fail | |
| PNG load | [ ] Pass [ ] Fail | |
| WebP load | [ ] Pass [ ] Fail | |
| TIFF-LZW / TIFF load | [ ] Pass [ ] Fail | |
| Qt image plugins present (`qtiff.dll`, `qwebp.dll`) | [ ] Pass [ ] Fail | |
| Sky Atlas opens and search works | [ ] Pass [ ] Fail | |
| Milky Way toggle renders | [ ] Pass [ ] Fail | |
| Moon toggle renders | [ ] Pass [ ] Fail | |
| Constellation toggle renders | [ ] Pass [ ] Fail | |
| App exits cleanly | [ ] Pass [ ] Fail | |
| Uninstall clean (app closed first) | [ ] Pass [ ] Fail | |

Installed folder path:

```text
%LOCALAPPDATA%\CitizenAstronomy.CAst\
```

Installed smoke JSON (attach or paste path):

```text

```

---

## Failures and observations

### Blockers

```text

```

### Non-blockers / cosmetic issues

```text

```

### Missing assets or visual defects

```text

```

---

## Uninstall notes

| Item | Result |
|------|--------|
| App closed before uninstall | [ ] Yes [ ] No |
| Install folder removed | [ ] Yes [ ] No |
| Start Menu group removed | [ ] Yes [ ] No |
| Desktop shortcut removed | [ ] Yes [ ] No |
| Residual locked files after uninstall | [ ] Yes [ ] No |

Residual files (if any):

```text

```

User settings intentionally preserved under `%LOCALAPPDATA%\CitizenAstronomy\`:

- [ ] Confirmed expected behavior

---

## Final decision

| Decision | Selected |
|----------|----------|
| Approved to send to alpha reviewers | [ ] |
| Blocked — needs fix before send | [ ] |

Approver:

```text

```

Sign-off date:

```text

```

---

## Attachments checklist

- [ ] `installed_format_smoke.json` or summary JSON
- [ ] `startup-error.log` (only if startup failed)
- [ ] Screenshot of digital-signature properties
- [ ] Screenshots of Sky View layer issues (optional)
- [ ] Sample XISF/FITS used during manual open tests (optional)
