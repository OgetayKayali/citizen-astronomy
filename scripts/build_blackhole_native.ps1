# Builds photometry_app/native/cast_blackhole.dll from native/blackhole (MSVC + CMake).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$source = Join-Path $root "native\blackhole"
$build = Join-Path $source "build"
$output = Join-Path $root "photometry_app\native"

$cmake = (Get-Command cmake -ErrorAction SilentlyContinue).Source
if (-not $cmake) { $cmake = "C:\Program Files\CMake\bin\cmake.exe" }

& $cmake -S $source -B $build -G "Visual Studio 17 2022" -A x64
if ($LASTEXITCODE -ne 0) { throw "CMake configure failed." }
& $cmake --build $build --config Release
if ($LASTEXITCODE -ne 0) { throw "Native black hole build failed." }

New-Item -ItemType Directory -Force $output | Out-Null
Copy-Item (Join-Path $build "Release\cast_blackhole.dll") $output -Force
Write-Host "Built $(Join-Path $output 'cast_blackhole.dll')"
