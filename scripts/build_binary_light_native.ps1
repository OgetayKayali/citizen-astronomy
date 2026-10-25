$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$source = Join-Path $root "native\binary_light"
$build = Join-Path $root "build\native_binary_light"
$cmake = (Get-Command cmake -ErrorAction SilentlyContinue).Source
if (-not $cmake) { $cmake = "C:\Program Files\CMake\bin\cmake.exe" }
& $cmake -S $source -B $build -A x64
if ($LASTEXITCODE -ne 0) { throw "Binary Light Lab configure failed." }
& $cmake --build $build --config Release -- /m:1 /nr:false
if ($LASTEXITCODE -ne 0) { throw "Binary Light Lab build failed." }
Copy-Item (Join-Path $build "Release\cast_stellar_lab.dll") (Join-Path $root "photometry_app\native\cast_stellar_lab.dll") -Force
