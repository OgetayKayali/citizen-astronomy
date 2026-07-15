# Moon SPICE Kernels

This directory is the default project-local place for optional SPICE/NAIF kernels used by the Sky View Moon orientation backend.

Photometry does not require these files for normal use. If SPICE is not configured, the Moon renderer keeps using the built-in approximate lunar orientation provider. The renderer path (`MoonRendererGL`) is unchanged either way.

## Expected Kernel Types

Place compatible NAIF kernels in this directory, or point the environment variables below at another location.

- Leapseconds kernel, for example `naif0012.tls`
  - Converts UTC timestamps to SPICE ephemeris time.
- Planetary ephemeris kernel, for example `de440.bsp` or another compatible DE ephemeris
  - Supplies the standard solar-system ephemeris context expected by many lunar kernel sets.
- Lunar binary PCK/orientation kernel, for example a `moon_pa_*.bpc` file
  - Supplies high-precision lunar pole, spin, and physical libration orientation.
- Lunar frame kernel, for example `moon_de440_*.tf` or another compatible lunar frame kernel
  - Defines lunar body frames such as `MOON_ME` and connects them to the SPICE frame system.

Some kernel sets may also require a generic text PCK such as `pck00010.tpc`. Use the compatibility notes from the NAIF kernel set you install.

Supported kernel suffixes are `.tls`, `.bsp`, `.bpc`, `.bc`, `.tf`, `.tpc`, and `.tsc`.

## Provider Modes

`CITIZEN_PHOTOMETRY_MOON_ORIENTATION_PROVIDER` controls provider selection:

- `auto`: default. Use SPICE when `spiceypy` and kernels are available, otherwise use the approximate fallback.
- `approx`: force the built-in approximate provider and never attempt SPICE.
- `spice`: request SPICE. If `spiceypy` or kernels are missing, Photometry still starts and reports an approximate fallback diagnostic.

Kernel discovery uses these variables:

- `CITIZEN_PHOTOMETRY_MOON_SPICE_KERNELS`: explicit ordered kernel file list. Uses the OS path separator (`;` on Windows, `:` on Linux/macOS).
- `CITIZEN_PHOTOMETRY_MOON_SPICE_KERNEL_DIR`: directory to scan recursively for supported kernel suffixes.

If neither kernel variable is set, Photometry scans these project-local directories in order:

1. `assets/spice`
2. `kernels/spice`
3. `spice`
4. `textures/spice`

## Windows PowerShell Examples

Use the project-local directory and keep safe fallback enabled:

```powershell
$env:CITIZEN_PHOTOMETRY_MOON_ORIENTATION_PROVIDER = "auto"
$env:CITIZEN_PHOTOMETRY_MOON_SPICE_KERNEL_DIR = "$PWD\assets\spice"
.\.venv\Scripts\python.exe scripts\check_moon_spice.py
```

Force SPICE for validation while still preserving safe startup fallback:

```powershell
$env:CITIZEN_PHOTOMETRY_MOON_ORIENTATION_PROVIDER = "spice"
$env:CITIZEN_PHOTOMETRY_MOON_SPICE_KERNEL_DIR = "$PWD\assets\spice"
.\.venv\Scripts\python.exe scripts\check_moon_spice.py --mode spice
```

Use an explicit kernel list:

```powershell
$kernels = @(
    "$PWD\assets\spice\naif0012.tls",
    "$PWD\assets\spice\de440.bsp",
    "$PWD\assets\spice\moon_pa_de440_200625.bpc",
    "$PWD\assets\spice\moon_de440_220930.tf"
)
$env:CITIZEN_PHOTOMETRY_MOON_SPICE_KERNELS = $kernels -join ";"
.\.venv\Scripts\python.exe scripts\check_moon_spice.py
```

Return to the built-in approximation:

```powershell
$env:CITIZEN_PHOTOMETRY_MOON_ORIENTATION_PROVIDER = "approx"
Remove-Item Env:CITIZEN_PHOTOMETRY_MOON_SPICE_KERNELS -ErrorAction SilentlyContinue
Remove-Item Env:CITIZEN_PHOTOMETRY_MOON_SPICE_KERNEL_DIR -ErrorAction SilentlyContinue
```

## Linux/macOS Shell Examples

Use the project-local directory and keep safe fallback enabled:

```sh
export CITIZEN_PHOTOMETRY_MOON_ORIENTATION_PROVIDER=auto
export CITIZEN_PHOTOMETRY_MOON_SPICE_KERNEL_DIR="$PWD/assets/spice"
./.venv/bin/python scripts/check_moon_spice.py
```

Force SPICE for validation while still preserving safe startup fallback:

```sh
export CITIZEN_PHOTOMETRY_MOON_ORIENTATION_PROVIDER=spice
export CITIZEN_PHOTOMETRY_MOON_SPICE_KERNEL_DIR="$PWD/assets/spice"
./.venv/bin/python scripts/check_moon_spice.py --mode spice
```

Use an explicit kernel list:

```sh
export CITIZEN_PHOTOMETRY_MOON_SPICE_KERNELS="$PWD/assets/spice/naif0012.tls:$PWD/assets/spice/de440.bsp:$PWD/assets/spice/moon_pa_de440_200625.bpc:$PWD/assets/spice/moon_de440_220930.tf"
./.venv/bin/python scripts/check_moon_spice.py
```

Return to the built-in approximation:

```sh
export CITIZEN_PHOTOMETRY_MOON_ORIENTATION_PROVIDER=approx
unset CITIZEN_PHOTOMETRY_MOON_SPICE_KERNELS
unset CITIZEN_PHOTOMETRY_MOON_SPICE_KERNEL_DIR
```

## Validation

Run:

```sh
python scripts/check_moon_spice.py
```

The command exits with success when the active provider, including approximate fallback, produces finite and orthonormal representative Moon orientation states. Check `active_orientation_provider` in the JSON output to confirm whether SPICE is actually active.
