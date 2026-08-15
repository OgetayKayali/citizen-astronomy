# Unsigned macOS distribution

This is the free alpha path for Mac testers: a PyInstaller `.app` zipped for download. It is **not** signed or notarized, and it does **not** use Velopack auto-update.

Windows remains the primary shipping platform (signed Velopack Setup).

## Transfer kit (Windows → Drive → Mac)

Large Sky Atlas / Moon assets are gitignored, so a normal git clone on the Mac is not enough. Pack everything on Windows into **one zip**, upload that zip to Drive, then download it on the Mac.

### On Windows (this PC)

From the Photometry repo root (PowerShell):

```powershell
.\packaging\pack_macos_build_kit.ps1
```

Output (single file to upload):

```text
packaging\dist\macos-build-kit\CitizenAstronomy-macos-build-kit.zip
```

That zip includes:

- App source needed for the Mac PyInstaller build
- Packaging scripts / Mac unsigned spec
- Guides and smoke fixtures
- Required large assets (`assets/moon_tiles`, Milky Way tile tree, Moon textures, icons, mode-launcher art)

Expect roughly **1.5–2 GB** before/after zip (mostly Milky Way tiles).

Optional: stage only (no zip) with `-SkipZip`.

### On Mac

1. Download `CitizenAstronomy-macos-build-kit.zip` from Drive
2. Unzip
3. Open `START_HERE_MAC.txt` in the kit folder and follow it

Or run:

```bash
cd CitizenAstronomy-macos-build-kit
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
pip install -e .
pip install "pyinstaller>=6.0"
chmod +x packaging/build_macos_unsigned.sh
./packaging/build_macos_unsigned.sh
```

## What Mac testers get (after you build)

1. Download `CitizenAstronomy-macos-unsigned.zip` (the *app* zip, not the build kit)
2. Unzip
3. Optional: move `Citizen Astronomy.app` to Applications
4. **Right-click → Open → Open** the first time (Gatekeeper)

Double-click alone is often blocked for unsigned apps.

## Build machine requirements

- macOS (Apple Silicon recommended)
- Python 3.11+
- The **Mac build kit zip** from `packaging/pack_macos_build_kit.ps1` (or an equivalent folder that already contains the paths listed in `MACOS_BUILD_KIT_MANIFEST.csv`)

## Build outputs

```text
_tmp_macos_unsigned_dist/Citizen Astronomy.app
packaging/dist/macos-unsigned/CitizenAstronomy-macos-unsigned.zip
```

Optional format smoke after build:

```bash
"_tmp_macos_unsigned_dist/Citizen Astronomy.app/Contents/MacOS/CitizenAstronomy" \
  --packaged-format-smoke \
  --packaged-format-smoke-fixtures packaging/fixtures
```

## Publish the app (manual)

Attach `packaging/dist/macos-unsigned/CitizenAstronomy-macos-unsigned.zip` to a GitHub prerelease (or share privately). Include a short note:

- Unsigned alpha; first open via right-click → Open
- No in-app updater on macOS yet
- Prefer Apple Silicon unless you built on Intel

Do **not** publish the large build-kit zip to end users — that is only for your Mac build machine.

## Limitations vs Windows Setup

| Topic | Windows (Velopack) | macOS unsigned |
|-------|--------------------|----------------|
| Installer | Signed Setup | Zip of `.app` |
| Gatekeeper / SmartScreen | Signed path | Right-click Open |
| In-app updates | Yes | No — download a new zip |
| Packaging script | `publish_github_update.ps1` | `build_macos_unsigned.sh` |
| Asset transfer | Already on Windows build PC | `pack_macos_build_kit.ps1` → Drive |

## Later (paid) upgrade path

Apple Developer Program ($99/year) enables Developer ID signing + notarization and a cleaner first-launch experience. That can reuse the same `.app` build with an added sign/notarize/staple step.
