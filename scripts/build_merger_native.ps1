# Builds photometry_app/native/cast_merger.dll from native/merger (MSVC + CMake),
# and copies the MSVC OpenMP runtime (vcomp140.dll) that the physics library uses.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$source = Join-Path $root "native\merger"
$build = Join-Path $source "build"
$output = Join-Path $root "photometry_app\native"

$cmake = (Get-Command cmake -ErrorAction SilentlyContinue).Source
if (-not $cmake) { $cmake = "C:\Program Files\CMake\bin\cmake.exe" }

& $cmake -S $source -B $build -G "Visual Studio 17 2022" -A x64
if ($LASTEXITCODE -ne 0) { throw "CMake configure failed." }
& $cmake --build $build --config Release
if ($LASTEXITCODE -ne 0) { throw "Native galaxy merger build failed." }

New-Item -ItemType Directory -Force $output | Out-Null
Copy-Item (Join-Path $build "Release\cast_merger.dll") $output -Force

$vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
$vs = & $vswhere -latest -products * -property installationPath
$openmp = Get-ChildItem (Join-Path $vs "VC\Redist\MSVC") -Recurse -Filter vcomp140.dll |
    Where-Object { $_.FullName -match '\\x64\\Microsoft\.VC\d+\.OpenMP\\' -and $_.FullName -notmatch 'onecore|debug' } |
    Sort-Object FullName -Descending | Select-Object -First 1
if (-not $openmp) { throw "vcomp140.dll (MSVC OpenMP redistributable) not found." }
Copy-Item $openmp.FullName $output -Force
Write-Host "Built $(Join-Path $output 'cast_merger.dll') with $($openmp.Name)"
