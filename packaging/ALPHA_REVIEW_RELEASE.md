# Citizen Astronomy — Alpha Update Release Guide

Citizen Astronomy uses PyInstaller one-folder builds packaged and updated by
Velopack 1.2. Every release publishes a full package for recovery and, after the
first Velopack release, a binary delta from the previous alpha. GitHub Releases
remains the public update source.

## Prerequisites

- Windows 10/11 build machine and a clean, pushed release commit.
- Project `.venv` with all runtime/build dependencies, including
  `velopack==1.2.0`.
- .NET SDK and the matching CLI:

  ```powershell
  dotnet tool install --global vpk --version 1.2.0
  ```

- GitHub CLI (`gh`) authenticated to the public release repository.
- All large assets required by `CitizenAstronomyAlphaReview.spec`.
- Authenticode signing through either:
  - `CITIZEN_ASTRONOMY_SIGN_TEMPLATE`, containing a `{{file}}` placeholder; or
  - `CITIZEN_ASTRONOMY_AZURE_SIGN_FILE`, pointing to Azure Artifact Signing
    metadata.
- Inno Setup 6 only for the first Velopack release's legacy migration wrapper.
  That outer wrapper requires `CITIZEN_ASTRONOMY_SIGN_TEMPLATE`.

`APP_VERSION`, the PEP 440 version in `pyproject.toml`, the update channel, and
the public GitHub repository must be committed before publishing.

## First Velopack release

Existing `0.1.1-alpha.3` clients only understand the schema-v1 Inno updater.
The first Velopack release therefore includes a one-time signed migration
bootstrap:

```powershell
.\packaging\publish_github_update.ps1 `
  -Repository "OgetayKayali/citizen-astronomy" `
  -Notes "Introduces small delta updates for future hotfixes." `
  -FirstVelopackRelease `
  -IncludeLegacyBootstrap
```

The publisher:

1. builds and smoke-tests the PyInstaller one-folder app;
2. creates the first Velopack Setup and full `.nupkg`;
3. creates and publishes the alpha release feed;
4. wraps the Velopack Setup in a legacy-compatible Inno executable;
5. signs the wrapper;
6. writes the old `CitizenAstronomy-update.json` with the wrapper's exact size
   and SHA-256; and
7. attaches both migration assets to the same GitHub prerelease.

The old app downloads the verified wrapper, exits, and launches it with the
existing Inno switches. The wrapper installs Velopack first. Only after the
managed executable exists does it remove the old Inno install, repair
shortcuts, and start the new app. Settings and training data remain outside
both application directories.

This is the final unavoidable full application download for existing users.

## Subsequent delta releases

For each later alpha, bump/commit/push the version and run:

```powershell
.\packaging\publish_github_update.ps1 `
  -Repository "OgetayKayali/citizen-astronomy" `
  -Notes "Describe the reviewer-visible fixes."
```

The script uses `vpk download github` to retrieve the previous alpha package,
then packages with:

```text
--channel alpha
--runtime win-x64
--delta BestSize
--packId CitizenAstronomy.CAst
```

It publishes these assets through `vpk upload github`:

- the one-click Velopack Setup executable;
- the complete versioned `*-full.nupkg`;
- the versioned `*-delta.nupkg`;
- `releases.alpha.json` and Velopack's release metadata.

Keep each full/delta pair. Velopack chooses the delta chain only when the
installed base package matches. It automatically downloads the full package
when the base is missing, too old, damaged, or when the delta chain is larger.

For a release that changes only Citizen Astronomy Python code, add
`-EnforceSmallDelta`. Publishing then fails if the generated delta exceeds 10%
of the full package:

```powershell
.\packaging\publish_github_update.ps1 `
  -Repository "OgetayKayali/citizen-astronomy" `
  -Notes "Small WCS fallback fix." `
  -EnforceSmallDelta
```

`-AllowUnsigned` exists only for disposable local/test-repository validation.
Do not use it for reviewer releases.

## Runtime update behavior

**File > Check for Updates** creates a Velopack `GithubSource` for the public
repository with prereleases enabled and the explicit `alpha` channel.
Velopack:

1. compares the installed managed version with `releases.alpha.json`;
2. selects a compatible delta chain or the full fallback;
3. downloads into its managed package directory;
4. verifies package hashes while reconstructing the target full package; and
5. uses the external `Update.exe` to replace `current` atomically and restart.

The prompt shows whether the selected transfer is a delta or full package and
its actual byte size. If the reviewer downloads an update but chooses not to
restart immediately, Velopack applies that already-verified package on the next
launch. Development/source runs are intentionally not updateable;
the UI reports that a Velopack-managed installation is required.

Application binaries live under:

```text
%LOCALAPPDATA%\CitizenAstronomy.CAst\
```

Persistent user data remains under:

```text
%LOCALAPPDATA%\CitizenPhotometry\
%LOCALAPPDATA%\CitizenAstronomy\
```

Velopack replaces its `current` application directory during an update, so no
settings, logs, workspaces, or training data may be written there.

## Three-stage clean-VM validation

For the migration release and the first following delta, use a disposable
Windows account or VM:

```powershell
.\packaging\validate_two_version_update.ps1 `
  -LegacyInstaller "<alpha.3 Inno setup>" `
  -LegacyVersion "0.1.1-alpha.3" `
  -BootstrapInstaller "<alpha.4 migration setup>" `
  -BootstrapVersion "0.1.1-alpha.4" `
  -NextFullPackage "<alpha.5 full.nupkg>" `
  -NextDeltaPackage "<alpha.5 delta.nupkg>" `
  -NextVersion "0.1.1-alpha.5" `
  -ConfirmCleanTestEnvironment
```

The validator performs a real legacy install, writes a settings sentinel,
migrates through the bootstrap, confirms removal of the old registration,
rejects a deliberately truncated delta while confirming the installed version
still runs,
reconstructs the next full package from the installed base plus delta, compares
its SHA-256 to the published full package, applies it through `Update.exe`, and
checks About version, settings, shortcut, and uninstall registration.

Successful output is written to
`packaging/dist/three-stage-update-validation.json`.

## Release checklist

- [ ] Runtime and package versions match.
- [ ] Release commit is clean, committed, and pushed.
- [ ] `velopack==1.2.0` and `vpk` 1.2.0 match.
- [ ] Authenticode signing is configured and timestamped.
- [ ] PyInstaller, source tests, packaged startup, FITS/XISF, and About smokes pass.
- [ ] GitHub release is an alpha prerelease with Setup, full, delta, and feed assets.
- [ ] Code-only delta is at most 10% of the full package.
- [ ] A stale/missing-base installation falls back to the full package.
- [ ] Corrupt and interrupted downloads leave the installed version working.
- [ ] Three-stage validation passes for the migration boundary.
- [ ] Settings, training data, shortcuts, and uninstall registration survive.
