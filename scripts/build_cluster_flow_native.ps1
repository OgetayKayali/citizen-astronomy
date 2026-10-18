# Builds photometry_app/native/cast_cluster_flow.dll from native/cluster_flow (MSVC + CMake).
# It uses the same MSVC OpenMP runtime (vcomp140.dll) that scripts\build_merger_native.ps1 copies.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$source = Join-Path $root "native\cluster_flow"
$build = Join-Path $source "build"
$output = Join-Path $root "photometry_app\native"

$cmake = (Get-Command cmake -ErrorAction SilentlyContinue).Source
if (-not $cmake) { $cmake = "C:\Program Files\CMake\bin\cmake.exe" }

& $cmake -S $source -B $build -G "Visual Studio 17 2022" -A x64
if ($LASTEXITCODE -ne 0) { throw "CMake configure failed." }
& $cmake --build $build --config Release
if ($LASTEXITCODE -ne 0) { throw "Native Cluster Flow build failed." }

New-Item -ItemType Directory -Force $output | Out-Null
Copy-Item (Join-Path $build "Release\cast_cluster_flow.dll") $output -Force
if (-not (Test-Path (Join-Path $output "vcomp140.dll"))) {
    Write-Warning "vcomp140.dll is missing from $output; run scripts\build_merger_native.ps1 to copy it."
}
Write-Host "Built $(Join-Path $output 'cast_cluster_flow.dll')"
