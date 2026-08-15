<#
.SYNOPSIS
  Pack everything needed to build the unsigned macOS CAst app into one Drive-ready zip.

.DESCRIPTION
  Run on your Windows machine from the Photometry repo root. Uploads the resulting
  zip to Google Drive (or similar), download/unzip on a Mac, then run
  packaging/build_macos_unsigned.sh.

  Includes source + gitignored Sky Atlas / Moon assets required by
  CitizenAstronomyMacUnsigned.spec.
#>
[CmdletBinding()]
param(
    [string]$RepoRoot = "",
    [string]$OutputDir = "",
    [switch]$SkipZip
)

$ErrorActionPreference = "Stop"

if (-not $RepoRoot) {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}
$RepoRoot = (Resolve-Path $RepoRoot).Path
Set-Location $RepoRoot

if (-not $OutputDir) {
    $OutputDir = Join-Path $RepoRoot "packaging\dist\macos-build-kit"
}

$KitName = "CitizenAstronomy-macos-build-kit"
$StagingRoot = Join-Path $OutputDir $KitName
$ZipPath = Join-Path $OutputDir "$KitName.zip"

$RequiredPaths = @(
    "photometry_app",
    "packaging\hooks",
    "packaging\runtime_hooks",
    "packaging\build_macos_unsigned.sh",
    "packaging\prepare_macos_icon.py",
    "packaging\generate_smoke_fixtures.py",
    "packaging\MACOS_UNSIGNED.md",
    "packaging\MACOS_UNSIGNED_NOTICE.txt",
    "packaging\fixtures",
    "guides",
    "assets\citizen_astronomy.ico",
    "assets\mode_launcher",
    "assets\moon_tiles",
    "photometry_app\data",
    "textures\milky_way_tiles_32k_padded_lzw_benchmark",
    "textures\moon_lroc_color_16bit_srgb_8k.tif",
    "textures\moon_ldem_16.tif",
    "textures\milkyway_2020_4k_preview.png",
    "textures\constellation_figures_4k.tif",
    "CitizenAstronomyMacUnsigned.spec",
    "pyproject.toml",
    "README.md",
    "LICENSE",
    "build.md",
    "version.md"
)

function Get-PathSizeBytes([string]$Path) {
    if (-not (Test-Path $Path)) { return 0 }
    $item = Get-Item $Path -Force
    if ($item.PSIsContainer) {
        return (Get-ChildItem $Path -Recurse -File -Force -ErrorAction SilentlyContinue |
            Measure-Object -Property Length -Sum).Sum
    }
    return [int64]$item.Length
}

function Format-Size([int64]$Bytes) {
    if ($Bytes -ge 1GB) { return "{0:N2} GB" -f ($Bytes / 1GB) }
    if ($Bytes -ge 1MB) { return "{0:N1} MB" -f ($Bytes / 1MB) }
    if ($Bytes -ge 1KB) { return "{0:N1} KB" -f ($Bytes / 1KB) }
    return "$Bytes B"
}

Write-Host "Checking required Mac build inputs under:"
Write-Host "  $RepoRoot"
Write-Host ""

$missing = @()
$manifestRows = @()
foreach ($relative in $RequiredPaths) {
    $full = Join-Path $RepoRoot $relative
    if (-not (Test-Path $full)) {
        $missing += $relative
        continue
    }
    $bytes = Get-PathSizeBytes $full
    $manifestRows += [pscustomobject]@{
        path = $relative.Replace("\", "/")
        bytes = $bytes
        size = Format-Size $bytes
    }
    Write-Host ("  OK  {0,-62} {1,10}" -f $relative, (Format-Size $bytes))
}

if ($missing.Count -gt 0) {
    Write-Host ""
    Write-Host "Missing required paths:" -ForegroundColor Red
    $missing | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    throw "Cannot pack Mac build kit until all required assets/source paths exist."
}

$totalBytes = ($manifestRows | Measure-Object -Property bytes -Sum).Sum
Write-Host ""
Write-Host ("Total kit payload (before zip): {0}" -f (Format-Size ([int64]$totalBytes)))
Write-Host ""

if (Test-Path $StagingRoot) {
    Write-Host "Removing previous staging folder..."
    Remove-Item $StagingRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $StagingRoot -Force | Out-Null

function Copy-KitItem([string]$Relative) {
    $source = Join-Path $RepoRoot $Relative
    $destination = Join-Path $StagingRoot $Relative
    $parent = Split-Path $destination -Parent
    if (-not (Test-Path $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    if ((Get-Item $source).PSIsContainer) {
        Write-Host "Copying directory $Relative ..."
        & robocopy $source $destination /E /NFL /NDL /NJH /NJS /nc /ns /np /R:1 /W:1 | Out-Null
        $code = $LASTEXITCODE
        if ($code -ge 8) {
            throw "robocopy failed for $Relative (exit $code)"
        }
        $global:LASTEXITCODE = 0
    }
    else {
        Write-Host "Copying file $Relative ..."
        Copy-Item -LiteralPath $source -Destination $destination -Force
    }
}

foreach ($relative in $RequiredPaths) {
    Copy-KitItem $relative
}

# Extra small packaging helpers useful on the Mac side.
$optionalExtras = @(
    "assets\README.md",
    "assets\citizen_astronomy.png",
    "packaging\macos\.gitkeep",
    "UPDATES.md",
    "CODEBASE_MAP.md"
)
foreach ($relative in $optionalExtras) {
    $full = Join-Path $RepoRoot $relative
    if (Test-Path $full) {
        Copy-KitItem $relative
    }
}

$startHere = @"
Citizen Astronomy — macOS unsigned build kit
===========================================

This folder contains the source and large local assets needed to build the
unsigned Mac app on another machine.

1) On this Mac, install Python 3.11+ if needed.
2) Open Terminal in THIS folder (the kit root).
3) Create a venv and build:

   python3 -m venv .venv
   source .venv/bin/activate
   python -m pip install -U pip
   pip install -e .
   pip install "pyinstaller>=6.0"
   chmod +x packaging/build_macos_unsigned.sh
   ./packaging/build_macos_unsigned.sh

4) Output zip:
   packaging/dist/macos-unsigned/CitizenAstronomy-macos-unsigned.zip

5) First launch for testers: right-click Citizen Astronomy.app → Open → Open.

See packaging/MACOS_UNSIGNED.md for details.

Note: This kit is ~$(Format-Size ([int64]$totalBytes)) before compression (mostly Sky Atlas tiles).
"@
Set-Content -Path (Join-Path $StagingRoot "START_HERE_MAC.txt") -Value $startHere -Encoding UTF8

$manifestPath = Join-Path $StagingRoot "MACOS_BUILD_KIT_MANIFEST.csv"
$manifestRows |
    Sort-Object path |
    Select-Object path, size, bytes |
    Export-Csv -Path $manifestPath -NoTypeInformation -Encoding UTF8

if ($SkipZip) {
    Write-Host ""
    Write-Host "Staging ready (zip skipped):"
    Write-Host "  $StagingRoot"
    Write-Host "Upload that folder, or re-run without -SkipZip to create one Drive file."
    exit 0
}

if (Test-Path $ZipPath) {
    Remove-Item $ZipPath -Force
}

Write-Host ""
Write-Host "Creating zip (this can take several minutes for ~1.5+ GB of tiles)..."
Push-Location $OutputDir
try {
    # Windows tar creates large zips more reliably than Compress-Archive.
    & tar -a -c -f $ZipPath $KitName
    if ($LASTEXITCODE -ne 0) {
        throw "tar failed while creating $ZipPath (exit $LASTEXITCODE)"
    }
}
finally {
    Pop-Location
}

$zipSize = Format-Size ((Get-Item $ZipPath).Length)
Write-Host ""
Write-Host "Mac build kit ready for Drive upload:"
Write-Host "  $ZipPath"
Write-Host "  Zip size: $zipSize"
Write-Host ""
Write-Host "On the Mac: download → unzip → open START_HERE_MAC.txt → run the build commands."
