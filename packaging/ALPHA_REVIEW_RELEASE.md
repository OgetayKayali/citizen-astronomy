# Citizen Astronomy — Alpha Update Release Guide

This guide is for the maintainer publishing a versioned Windows alpha prerelease and updater manifest to a public GitHub repository. The publishing script creates the GitHub release immediately; it is not a dry run.

## Prerequisites

- Windows 10/11 build machine
- A clean checkout whose tracked changes have been committed and pushed
- Project virtual environment at `.venv` with build and runtime dependencies installed
- Large runtime assets required by `CitizenAstronomyAlphaReview.spec`
- Inno Setup 6 (`ISCC.exe` on `PATH` or installed in its standard location)
- GitHub CLI (`gh`) authenticated to an account allowed to create releases
- A public GitHub repository

Before the first release, set `APP_UPDATE_GITHUB_REPOSITORY` in `photometry_app/app_metadata.py` to the same public `owner/name`. Set and commit the intended `APP_VERSION`; if `-Version` is supplied, the publisher verifies that it matches the embedded value.

## Exact publishing steps

1. Commit all tracked release changes and push the release commit to GitHub. Untracked build outputs such as `_tmp_alpha_review_dist` do not need to be committed or deleted.
2. Confirm authentication and public repository visibility:

```powershell
gh auth status
gh repo view "owner/name" --json nameWithOwner,visibility
```

3. From the repository root, publish the prerelease:

```powershell
.\packaging\publish_github_update.ps1 `
  -Repository "owner/name" `
  -Notes "Summary shown in the updater and GitHub release."
```

The optional `-Version` argument is only a consistency check and must match the embedded `APP_VERSION`:

```powershell
.\packaging\publish_github_update.ps1 `
  -Repository "owner/name" `
  -Version "0.1.1-alpha.1" `
  -Notes "Summary shown in the updater and GitHub release."
```

The script fails before building if `-Version` differs from the embedded version, the embedded update repository differs from `-Repository`, source files are modified/staged/untracked, authentication is invalid, the repository is not public, the release commit is not on GitHub, or the versioned release already exists. Known generated build artifacts are ignored by the source-cleanliness check.

## Canonical build and validation performed by the publisher

The publisher runs these commands and stops on the first failure:

```powershell
.\.venv\Scripts\python.exe packaging\generate_smoke_fixtures.py

.\.venv\Scripts\python.exe -m PyInstaller `
  --noconfirm `
  --clean `
  --distpath _tmp_alpha_review_dist `
  --workpath _tmp_alpha_review_build `
  CitizenAstronomyAlphaReview.spec

.\.venv\Scripts\python.exe -m py_compile photometry_app\main.py photometry_app\core\packaged_format_smoke.py scripts\run_packaged_alpha_smoke.py tests\test_packaged_format_smoke.py

.\.venv\Scripts\python.exe -m pytest tests\test_app_updates.py tests\test_release_update_contract.py -q

.\.venv\Scripts\python.exe -m pytest tests\test_workers.py tests\test_main_window.py -q -k "UpdateWorker or about_dialog_mentions or file_menu_shows_check_for_updates or update_installer_launches or update_check_completion"

.\.venv\Scripts\python.exe -m pytest tests\test_qt_image_formats.py tests\test_packaged_format_smoke.py -q

.\.venv\Scripts\python.exe _tmp_startup_smoke.py

.\.venv\Scripts\python.exe scripts\run_packaged_alpha_smoke.py `
  --exe _tmp_alpha_review_dist\CitizenAstronomyAlphaReview\CitizenAstronomyAlphaReview.exe `
  --fixtures packaging\fixtures `
  --output _tmp_packaged_alpha_smoke_result.json

_tmp_alpha_review_dist\CitizenAstronomyAlphaReview\CitizenAstronomyAlphaReview.exe `
  --packaged-format-smoke `
  --packaged-format-smoke-fixtures packaging\fixtures `
  --packaged-format-smoke-output _tmp_packaged_format_smoke_result.json
```

Passing `--exe` explicitly ensures the release smoke targets the newly built bundle even if an older untracked build folder exists.

The publisher then invokes Inno Setup with version defines equivalent to:

```powershell
ISCC.exe `
  /DAppVersion=0.1.1-alpha.1 `
  /DOutputBaseFilename=CitizenAstronomyAlphaReview-0.1.1-alpha.1-Setup `
  packaging\inno\CitizenAstronomyAlphaReview.iss
```

The `.iss` file also has safe versioned defaults for a manual compile. Its existing Inno `AppId` and per-user install location remain unchanged.

## Release assets and manifest

For version `0.1.1-alpha.1`, the publisher creates:

```text
packaging\dist\CitizenAstronomyAlphaReview-0.1.1-alpha.1-Setup.exe
packaging\dist\CitizenAstronomy-update.json
```

It computes the installer byte size and SHA-256, writes a manifest containing exactly these fields, and uploads both files to the `v0.1.1-alpha.1` GitHub prerelease:

```json
{
  "schema_version": 1,
  "app_id": "CitizenAstronomy.CAst",
  "channel": "alpha",
  "version": "0.1.1-alpha.1",
  "installer_asset": "CitizenAstronomyAlphaReview-0.1.1-alpha.1-Setup.exe",
  "installer_size": 123456789,
  "installer_sha256": "<64 lowercase hexadecimal characters>",
  "notes": "Summary shown in the updater and GitHub release."
}
```

## Installer update behavior

The updater runs the downloaded installer in silent update mode:

```powershell
CitizenAstronomyAlphaReview-0.1.1-alpha.1-Setup.exe `
  /SILENT `
  /SUPPRESSMSGBOXES `
  /NORESTART `
  /CLOSEAPPLICATIONS `
  /UPDATE=1
```

`/UPDATE=1` closes the running `CitizenAstronomyAlphaReview.exe`, installs over the same per-user application directory, and relaunches the application as the original user. User settings, state, and training data under `%LOCALAPPDATA%\CitizenPhotometry`, plus update files and startup logs under `%LOCALAPPDATA%\CitizenAstronomy`, are outside the installation directory and are not deleted or replaced.

Normal double-click installation remains interactive, including the final “Launch Citizen Astronomy” option.

## Required two-version update validation

Keep the installer from the preceding alpha and the newly published installer. On a clean disposable Windows account or VM, run:

```powershell
.\packaging\validate_two_version_update.ps1 `
  -OlderInstaller "<path-to-older-setup.exe>" `
  -OlderVersion "0.1.1-alpha.1" `
  -NewerInstaller "<path-to-newer-setup.exe>" `
  -NewerVersion "0.1.1-alpha.2" `
  -ConfirmCleanTestEnvironment
```

The safety switch is required because this test performs real per-user installs. It refuses to run if this product is already installed. The test installs the older version, confirms the embedded About version, changes a persisted theme setting, starts the old app, upgrades with the same silent `/UPDATE=1` arguments used by the menu, and verifies:

- the old process closes and the new version relaunches;
- the displayed About version changes;
- the setting survives;
- the Start Menu shortcut still targets the installed executable;
- the stable uninstall registration reports the new version.

Successful results are written to `packaging\dist\two-version-update-validation.json`. Leave the updated installation in the disposable account long enough to inspect it manually, then discard the account/VM.

## Maintainer checklist

- [ ] `APP_VERSION` and `APP_UPDATE_GITHUB_REPOSITORY` are correct and committed
- [ ] Release commit is pushed to the public repository
- [ ] `_tmp_packaged_alpha_smoke_result.json` reports `"success": true`
- [ ] `_tmp_packaged_format_smoke_result.json` reports `"success": true`
- [ ] `_tmp_app_startup_smoke_result.json` reports `startup_ok`
- [ ] No new `%LOCALAPPDATA%\CitizenAstronomy\startup-error.log`
- [ ] GitHub release is marked prerelease and has exactly the installer and manifest assets
- [ ] Manifest size and SHA-256 match the uploaded installer
- [ ] `two-version-update-validation.json` reports success on a clean Windows account or VM
- [ ] A normal interactive install still offers the final launch option

## Unsigned installer warning

The installer is currently unsigned. Windows SmartScreen may display “Windows protected your PC” or an “Unknown publisher” warning even when the download is valid. Maintainers should tell reviewers to download only from the project’s public GitHub release and verify the installer SHA-256 against `CitizenAstronomy-update.json`. Do not describe the warning as proof of malware or instruct reviewers to bypass a hash mismatch.

Other alpha risks remain: networked catalog features require internet on first use, optional `spiceypy` kernels are not bundled, and full Sky View/Moon fidelity depends on the large texture/tile trees being present at build time.
