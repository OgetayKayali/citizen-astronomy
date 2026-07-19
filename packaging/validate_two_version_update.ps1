[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$LegacyInstaller,

    [Parameter(Mandatory = $true)]
    [string]$LegacyVersion,

    [Parameter(Mandatory = $true)]
    [string]$BootstrapInstaller,

    [Parameter(Mandatory = $true)]
    [string]$BootstrapVersion,

    [Parameter(Mandatory = $true)]
    [string]$NextFullPackage,

    [Parameter(Mandatory = $true)]
    [string]$NextDeltaPackage,

    [Parameter(Mandatory = $true)]
    [string]$NextVersion,

    [string]$OutputPath = (Join-Path $PSScriptRoot "dist\three-stage-update-validation.json"),

    [switch]$ConfirmCleanTestEnvironment
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$legacyAppName = "Citizen Astronomy (CAst) Alpha Review"
$exeName = "CitizenAstronomyAlphaReview.exe"
$legacyInstallRoot = Join-Path $env:LOCALAPPDATA "Programs\$legacyAppName"
$legacyExe = Join-Path $legacyInstallRoot $exeName
$velopackRoot = Join-Path $env:LOCALAPPDATA "CitizenAstronomy.CAst"
$managedExe = Join-Path $velopackRoot $exeName
$updateExe = Join-Path $velopackRoot "Update.exe"
$packagesDirectory = Join-Path $velopackRoot "packages"
$settingsPath = Join-Path $env:LOCALAPPDATA "CitizenPhotometry\settings.json"
$shortcutPath = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Citizen Astronomy (CAst).lnk"
$legacyUninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{A4D6F2B1-7C93-4E2A-9B61-3F8E5D0C1A72}_is1"
$uninstallRegistryRoot = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall"
$versionPattern = '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?$'

if (-not $ConfirmCleanTestEnvironment) {
    throw "This test installs, migrates, and updates the app. Run it only in a clean Windows account or VM and pass -ConfirmCleanTestEnvironment."
}
foreach ($version in @($LegacyVersion, $BootstrapVersion, $NextVersion)) {
    if ($version -notmatch $versionPattern) {
        throw "All versions must be semantic versions such as 0.1.1-alpha.3."
    }
}
if (($LegacyVersion -eq $BootstrapVersion) -or ($BootstrapVersion -eq $NextVersion)) {
    throw "LegacyVersion, BootstrapVersion, and NextVersion must be successive distinct versions."
}

$inputPaths = @(
    [IO.Path]::GetFullPath($LegacyInstaller),
    [IO.Path]::GetFullPath($BootstrapInstaller),
    [IO.Path]::GetFullPath($NextFullPackage),
    [IO.Path]::GetFullPath($NextDeltaPackage)
)
foreach ($inputPath in $inputPaths) {
    if (-not (Test-Path -LiteralPath $inputPath -PathType Leaf)) {
        throw "Update validation input does not exist: $inputPath"
    }
}
$LegacyInstaller = $inputPaths[0]
$BootstrapInstaller = $inputPaths[1]
$NextFullPackage = $inputPaths[2]
$NextDeltaPackage = $inputPaths[3]

foreach ($installPath in @($legacyInstallRoot, $velopackRoot)) {
    if (Test-Path -LiteralPath $installPath -PathType Container) {
        throw "The test install directory already exists: $installPath"
    }
}
if (Test-Path -LiteralPath $legacyUninstallKey) {
    throw "The legacy Citizen Astronomy build is already registered for this user."
}

function Invoke-ProcessChecked {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$Description
    )

    Write-Host "==> $Description"
    $process = Start-Process -FilePath $Path -ArgumentList $Arguments -PassThru -Wait
    if ($process.ExitCode -ne 0) {
        throw "$Description failed with exit code $($process.ExitCode)."
    }
}

function Invoke-ProcessExpectFailure {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$Description
    )

    Write-Host "==> $Description"
    $process = Start-Process -FilePath $Path -ArgumentList $Arguments -PassThru -Wait
    if ($process.ExitCode -eq 0) {
        throw "$Description unexpectedly succeeded."
    }
}

function Invoke-AboutSmoke {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string]$ExpectedVersion
    )

    $output = @(& $Executable "--about-dialog-smoke" 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "About smoke failed with exit code $LASTEXITCODE.`n$($output -join [Environment]::NewLine)"
    }
    try {
        $result = ($output -join [Environment]::NewLine) | ConvertFrom-Json
    } catch {
        throw "About smoke did not return valid JSON.`n$($output -join [Environment]::NewLine)"
    }
    if (($result.success -ne $true) -or ([string]$result.version -ne $ExpectedVersion)) {
        throw "Installed About version '$($result.version)' does not match expected version '$ExpectedVersion'."
    }
    return $result
}

function Stop-CitizenAstronomyProcesses {
    foreach ($process in @(Get-Process -Name "CitizenAstronomyAlphaReview" -ErrorAction SilentlyContinue)) {
        $null = $process.CloseMainWindow()
        if (-not $process.WaitForExit(10000)) {
            Stop-Process -Id $process.Id -Force
            $process.WaitForExit(5000) | Out-Null
        }
    }
}

function Get-VelopackUninstallRegistration {
    foreach ($key in @(Get-ChildItem -LiteralPath $uninstallRegistryRoot -ErrorAction SilentlyContinue)) {
        try {
            $entry = Get-ItemProperty -LiteralPath $key.PSPath
            $displayName = if ($null -ne $entry.PSObject.Properties["DisplayName"]) {
                [string]$entry.DisplayName
            } else { "" }
            $installLocation = if ($null -ne $entry.PSObject.Properties["InstallLocation"]) {
                [string]$entry.InstallLocation
            } else { "" }
            $uninstallString = if ($null -ne $entry.PSObject.Properties["UninstallString"]) {
                [string]$entry.UninstallString
            } else { "" }
            if (($displayName -like "Citizen Astronomy*") -and
                (($installLocation -like "$velopackRoot*") -or
                 ($uninstallString -like "*CitizenAstronomy.CAst*"))) {
                return $entry
            }
        } catch {
            continue
        }
    }
    return $null
}

$legacyInstallerArguments = @(
    "/VERYSILENT",
    "/SUPPRESSMSGBOXES",
    "/NORESTART",
    "/CLOSEAPPLICATIONS"
)
$bootstrapArguments = @($legacyInstallerArguments + "/UPDATE=1")
$previousQtPlatform = $env:QT_QPA_PLATFORM
$env:QT_QPA_PLATFORM = "offscreen"

try {
    Invoke-ProcessChecked -Path $LegacyInstaller -Arguments $legacyInstallerArguments -Description "Installing legacy alpha $LegacyVersion"
    if (-not (Test-Path -LiteralPath $legacyExe -PathType Leaf)) {
        throw "The legacy installer completed but its executable is missing."
    }
    $legacyAbout = Invoke-AboutSmoke -Executable $legacyExe -ExpectedVersion $LegacyVersion

    $settingsDirectory = Split-Path -Parent $settingsPath
    if (-not (Test-Path -LiteralPath $settingsDirectory -PathType Container)) {
        $null = New-Item -ItemType Directory -Path $settingsDirectory
    }
    $sentinel = [Guid]::NewGuid().ToString("N")
    $settings = if (Test-Path -LiteralPath $settingsPath -PathType Leaf) {
        Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json
    } else {
        [pscustomobject]@{}
    }
    $settings | Add-Member -NotePropertyName "theme" -NotePropertyValue "nord" -Force
    $settings | Add-Member -NotePropertyName "updater_validation_sentinel" -NotePropertyValue $sentinel -Force
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText(
        $settingsPath,
        ($settings | ConvertTo-Json -Depth 100) + [Environment]::NewLine,
        $utf8NoBom
    )

    $legacyProcess = Start-Process -FilePath $legacyExe -PassThru
    Start-Sleep -Seconds 8
    if ($legacyProcess.HasExited) {
        throw "The legacy installed application exited before migration."
    }
    Invoke-ProcessChecked -Path $BootstrapInstaller -Arguments $bootstrapArguments -Description "Migrating to Velopack alpha $BootstrapVersion"
    $legacyProcess.WaitForExit(30000) | Out-Null
    if (-not $legacyProcess.HasExited) {
        throw "The migration bootstrap did not close the legacy application."
    }

    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        if ((Test-Path -LiteralPath $managedExe -PathType Leaf) -and
            (Test-Path -LiteralPath $updateExe -PathType Leaf)) {
            break
        }
        Start-Sleep -Seconds 1
    }
    if ((-not (Test-Path -LiteralPath $managedExe -PathType Leaf)) -or
        (-not (Test-Path -LiteralPath $updateExe -PathType Leaf))) {
        throw "The migration did not create a Velopack-managed installation."
    }
    Stop-CitizenAstronomyProcesses
    $bootstrapAbout = Invoke-AboutSmoke -Executable $managedExe -ExpectedVersion $BootstrapVersion
    if (Test-Path -LiteralPath $legacyUninstallKey) {
        throw "The legacy Inno uninstall registration remains after migration."
    }

    $basePackages = @(Get-ChildItem -LiteralPath $packagesDirectory -File -Filter "*-full.nupkg" |
        Sort-Object LastWriteTimeUtc -Descending)
    if ($basePackages.Count -eq 0) {
        throw "Velopack did not retain the installed full package required for delta reconstruction."
    }
    $corruptDelta = Join-Path $packagesDirectory "validation-corrupt-delta.nupkg"
    $corruptOutput = Join-Path $packagesDirectory "validation-corrupt-output.nupkg"
    $inputStream = [IO.File]::OpenRead($NextDeltaPackage)
    try {
        $outputStream = [IO.File]::Create($corruptDelta)
        try {
            $buffer = [byte[]]::new(1048576)
            $bytesRead = $inputStream.Read($buffer, 0, $buffer.Length)
            if ($bytesRead -gt 0) {
                $outputStream.Write($buffer, 0, $bytesRead)
            }
        } finally {
            $outputStream.Dispose()
        }
    } finally {
        $inputStream.Dispose()
    }
    Invoke-ProcessExpectFailure -Path $updateExe -Arguments @(
        "patch",
        "--old", $basePackages[0].FullName,
        "--delta", $corruptDelta,
        "--output", $corruptOutput
    ) -Description "Confirming an interrupted/corrupt delta is rejected"
    $postFailureAbout = Invoke-AboutSmoke -Executable $managedExe -ExpectedVersion $BootstrapVersion

    $reconstructedPackage = Join-Path $packagesDirectory "validation-$NextVersion-full.nupkg"
    Remove-Item -LiteralPath $reconstructedPackage -Force -ErrorAction SilentlyContinue
    Invoke-ProcessChecked -Path $updateExe -Arguments @(
        "patch",
        "--old", $basePackages[0].FullName,
        "--delta", $NextDeltaPackage,
        "--output", $reconstructedPackage
    ) -Description "Reconstructing alpha $NextVersion from the delta package"
    if (-not (Test-Path -LiteralPath $reconstructedPackage -PathType Leaf)) {
        throw "Velopack did not reconstruct the target full package from the delta."
    }
    $expectedHash = (Get-FileHash -LiteralPath $NextFullPackage -Algorithm SHA256).Hash
    $reconstructedHash = (Get-FileHash -LiteralPath $reconstructedPackage -Algorithm SHA256).Hash
    if (-not $expectedHash.Equals($reconstructedHash, [StringComparison]::OrdinalIgnoreCase)) {
        throw "The delta-reconstructed package does not match the published full package."
    }

    Invoke-ProcessChecked -Path $updateExe -Arguments @(
        "--silent",
        "apply",
        "--norestart",
        "--package", $reconstructedPackage
    ) -Description "Applying the reconstructed Velopack alpha $NextVersion"
    $nextAbout = Invoke-AboutSmoke -Executable $managedExe -ExpectedVersion $NextVersion

    $retainedSettings = Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json
    if (([string]$retainedSettings.theme -ne "nord") -or
        ([string]$retainedSettings.updater_validation_sentinel -ne $sentinel)) {
        throw "Persisted settings were not retained through migration and delta update."
    }
    if (-not (Test-Path -LiteralPath $shortcutPath -PathType Leaf)) {
        throw "The Velopack Start Menu shortcut is missing."
    }
    $shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcutPath)
    if (-not ([IO.Path]::GetFullPath($shortcut.TargetPath)).Equals(
        [IO.Path]::GetFullPath($managedExe),
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "The Start Menu shortcut does not target the Velopack launcher."
    }
    $uninstallRegistration = Get-VelopackUninstallRegistration
    if ($null -eq $uninstallRegistration) {
        throw "The Velopack per-user uninstall registration is missing."
    }
    if ([string]$uninstallRegistration.DisplayVersion -ne $NextVersion) {
        throw "Uninstall DisplayVersion '$($uninstallRegistration.DisplayVersion)' does not match '$NextVersion'."
    }

    $result = [ordered]@{
        success                          = $true
        legacy_version                   = $LegacyVersion
        bootstrap_version                = $BootstrapVersion
        next_version                     = $NextVersion
        velopack_install_path             = $velopackRoot
        legacy_about_version              = [string]$legacyAbout.version
        bootstrap_about_version           = [string]$bootstrapAbout.version
        post_failure_about_version        = [string]$postFailureAbout.version
        next_about_version                = [string]$nextAbout.version
        legacy_process_closed             = $true
        bootstrap_migration_succeeded     = $true
        delta_reconstructed               = $true
        delta_matches_full_sha256          = $true
        corrupt_delta_rejected             = $true
        working_version_preserved_on_error = $true
        delta_applied                      = $true
        setting_retained                  = $true
        start_menu_shortcut_valid         = $true
        uninstall_registration_valid     = $true
        uninstall_display_version        = [string]$uninstallRegistration.DisplayVersion
    }
    $resolvedOutputPath = [IO.Path]::GetFullPath($OutputPath)
    $outputDirectory = Split-Path -Parent $resolvedOutputPath
    if (-not (Test-Path -LiteralPath $outputDirectory -PathType Container)) {
        $null = New-Item -ItemType Directory -Path $outputDirectory
    }
    [IO.File]::WriteAllText(
        $resolvedOutputPath,
        ($result | ConvertTo-Json -Depth 4) + [Environment]::NewLine,
        $utf8NoBom
    )
    Write-Host "Three-stage update validation passed: $resolvedOutputPath"
} finally {
    Stop-CitizenAstronomyProcesses
    if ($null -eq $previousQtPlatform) {
        Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue
    } else {
        $env:QT_QPA_PLATFORM = $previousQtPlatform
    }
}
