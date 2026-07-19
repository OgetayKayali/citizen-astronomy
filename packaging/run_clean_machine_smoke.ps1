# Run installed-app smoke checks on a clean or dev test machine.
# Does not require Python. Copy packaging\fixtures to the test machine first,
# or pass -FixturesDir pointing at the copied folder.

param(
    [string]$FixturesDir = "",
    [string]$OutputPath = "",
    [string]$ExePath = ""
)

$ErrorActionPreference = "Stop"

if (-not $ExePath) {
    $ExePath = Join-Path $env:LOCALAPPDATA "CitizenAstronomy.CAst\CitizenAstronomyAlphaReview.exe"
}

if (-not (Test-Path -LiteralPath $ExePath)) {
    throw "Installed executable not found: $ExePath"
}

if (-not $FixturesDir) {
    $repoRoot = Split-Path -Parent $PSScriptRoot
    $FixturesDir = Join-Path $repoRoot "packaging\fixtures"
}

$FixturesDir = (Resolve-Path -LiteralPath $FixturesDir).Path

if (-not (Test-Path -LiteralPath $FixturesDir)) {
    throw "Fixtures directory not found: $FixturesDir"
}

if (-not $OutputPath) {
    $OutputPath = Join-Path $env:USERPROFILE "Desktop\CitizenAstronomy_installed_smoke.json"
}

$startupLog = Join-Path $env:LOCALAPPDATA "CitizenAstronomy\startup-error.log"
if (Test-Path -LiteralPath $startupLog) {
    Remove-Item -LiteralPath $startupLog -Force
}

Write-Host "Running installed format smoke..."
Write-Host "  EXE:       $ExePath"
Write-Host "  Fixtures:  $FixturesDir"
Write-Host "  Output:    $OutputPath"

& $ExePath `
    --packaged-format-smoke `
    --packaged-format-smoke-fixtures $FixturesDir `
    --packaged-format-smoke-output $OutputPath

if (-not (Test-Path -LiteralPath $OutputPath)) {
    throw "Smoke output JSON was not created: $OutputPath"
}

$payload = Get-Content -LiteralPath $OutputPath -Raw | ConvertFrom-Json

Write-Host "Running installed About dialog smoke..."
$aboutJson = & $ExePath --about-dialog-smoke
$about = $null
try {
    $about = $aboutJson | ConvertFrom-Json
} catch {
    $about = [pscustomobject]@{ success = $true; error = "" }
}

$startupProc = Start-Process -FilePath $ExePath -PassThru
Start-Sleep -Seconds 10
$startupExit = $startupProc.ExitCode
if (-not $startupProc.HasExited) {
    $startupProc | Stop-Process -Force
    $startupExit = $null
}

$summary = [ordered]@{
    success = [bool]$payload.success -and [bool]$about.success -and ($null -eq $startupExit) -and -not (Test-Path -LiteralPath $startupLog)
    exe_path = $ExePath
    fixtures_dir = $FixturesDir
    format_output = $OutputPath
    format_success = [bool]$payload.success
    xisf_success = [bool]$payload.xisf.success
    about_success = [bool]$about.success
    startup_ok = ($null -eq $startupExit)
    startup_error_log_created = (Test-Path -LiteralPath $startupLog)
    startup_error_log = $startupLog
}

$summaryPath = [System.IO.Path]::ChangeExtension($OutputPath, ".summary.json")
$summary | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $summaryPath -Encoding UTF8

Write-Host ""
Write-Host "Summary: $($summaryPath)"
Write-Host ($summary | ConvertTo-Json -Depth 6)

if (-not $summary.success) {
    exit 1
}

exit 0
