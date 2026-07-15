[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$OlderInstaller,

    [Parameter(Mandatory = $true)]
    [string]$OlderVersion,

    [Parameter(Mandatory = $true)]
    [string]$NewerInstaller,

    [Parameter(Mandatory = $true)]
    [string]$NewerVersion,

    [string]$OutputPath = (Join-Path $PSScriptRoot "dist\two-version-update-validation.json"),

    [switch]$ConfirmCleanTestEnvironment
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$appName = "Citizen Astronomy (CAst) Alpha Review"
$exeName = "CitizenAstronomyAlphaReview.exe"
$installRoot = Join-Path $env:LOCALAPPDATA "Programs\$appName"
$installedExe = Join-Path $installRoot $exeName
$settingsPath = Join-Path $env:LOCALAPPDATA "CitizenPhotometry\settings.json"
$shortcutPath = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\$appName\$appName.lnk"
$uninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{A4D6F2B1-7C93-4E2A-9B61-3F8E5D0C1A72}_is1"
$versionPattern = '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?$'

if (-not $ConfirmCleanTestEnvironment) {
    throw "This test installs and upgrades the app. Run it only in a clean Windows account or VM and pass -ConfirmCleanTestEnvironment."
}
if ($OlderVersion -notmatch $versionPattern -or $NewerVersion -notmatch $versionPattern) {
    throw "OlderVersion and NewerVersion must be semantic versions such as 0.1.1-alpha.1."
}
if ($OlderVersion -eq $NewerVersion) {
    throw "OlderVersion and NewerVersion must differ."
}

$OlderInstaller = [IO.Path]::GetFullPath($OlderInstaller)
$NewerInstaller = [IO.Path]::GetFullPath($NewerInstaller)
foreach ($installer in @($OlderInstaller, $NewerInstaller)) {
    if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) {
        throw "Installer does not exist: $installer"
    }
}
if (Test-Path -LiteralPath $installRoot -PathType Container) {
    throw "The test install directory already exists: $installRoot"
}
if (Test-Path -LiteralPath $uninstallKey) {
    throw "Citizen Astronomy is already registered as installed for this user."
}

function Invoke-Installer {
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

function Invoke-AboutSmoke {
    param([Parameter(Mandatory = $true)][string]$ExpectedVersion)

    $output = @(& $installedExe "--about-dialog-smoke" 2>&1)
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

function Get-InstalledProcess {
    $resolvedExe = [IO.Path]::GetFullPath($installedExe)
    foreach ($process in @(Get-Process -Name "CitizenAstronomyAlphaReview" -ErrorAction SilentlyContinue)) {
        try {
            if ([IO.Path]::GetFullPath($process.Path).Equals($resolvedExe, [StringComparison]::OrdinalIgnoreCase)) {
                return $process
            }
        } catch {
            continue
        }
    }
    return $null
}

$commonInstallerArguments = @(
    "/VERYSILENT",
    "/SUPPRESSMSGBOXES",
    "/NORESTART",
    "/CLOSEAPPLICATIONS"
)
$previousQtPlatform = $env:QT_QPA_PLATFORM
$env:QT_QPA_PLATFORM = "offscreen"

try {
    Invoke-Installer -Path $OlderInstaller -Arguments $commonInstallerArguments -Description "Installing older alpha $OlderVersion"
    if (-not (Test-Path -LiteralPath $installedExe -PathType Leaf)) {
        throw "Older installer completed but the application executable is missing."
    }
    $olderAbout = Invoke-AboutSmoke -ExpectedVersion $OlderVersion

    $settingsDirectory = Split-Path -Parent $settingsPath
    if (-not (Test-Path -LiteralPath $settingsDirectory -PathType Container)) {
        $null = New-Item -ItemType Directory -Path $settingsDirectory
    }
    if (Test-Path -LiteralPath $settingsPath -PathType Leaf) {
        $settings = Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json
    } else {
        $settings = [pscustomobject]@{}
    }
    $sentinel = [Guid]::NewGuid().ToString("N")
    $settings | Add-Member -NotePropertyName "theme" -NotePropertyValue "nord" -Force
    $settings | Add-Member -NotePropertyName "updater_validation_sentinel" -NotePropertyValue $sentinel -Force
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText(
        $settingsPath,
        ($settings | ConvertTo-Json -Depth 100) + [Environment]::NewLine,
        $utf8NoBom
    )

    $oldProcess = Start-Process -FilePath $installedExe -PassThru
    Start-Sleep -Seconds 8
    if ($oldProcess.HasExited) {
        throw "The older installed application exited before the update test."
    }

    $updateArguments = @($commonInstallerArguments + "/UPDATE=1")
    Invoke-Installer -Path $NewerInstaller -Arguments $updateArguments -Description "Upgrading in place to alpha $NewerVersion"
    $oldProcess.WaitForExit(30000) | Out-Null
    if (-not $oldProcess.HasExited) {
        throw "The update installer did not close the older application process."
    }

    $relaunchedProcess = $null
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        $relaunchedProcess = Get-InstalledProcess
        if ($null -ne $relaunchedProcess) {
            break
        }
        Start-Sleep -Seconds 1
    }
    if ($null -eq $relaunchedProcess) {
        throw "The newer application was not relaunched after the update."
    }
    if ($relaunchedProcess.Id -eq $oldProcess.Id) {
        throw "The update did not create a new application process."
    }

    $null = $relaunchedProcess.CloseMainWindow()
    if (-not $relaunchedProcess.WaitForExit(10000)) {
        Stop-Process -Id $relaunchedProcess.Id -Force
        $relaunchedProcess.WaitForExit(5000) | Out-Null
    }

    $newerAbout = Invoke-AboutSmoke -ExpectedVersion $NewerVersion
    $retainedSettings = Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json
    if (
        ([string]$retainedSettings.theme -ne "nord") -or
        ([string]$retainedSettings.updater_validation_sentinel -ne $sentinel)
    ) {
        throw "The setting changed before the update was not retained."
    }
    if (-not (Test-Path -LiteralPath $shortcutPath -PathType Leaf)) {
        throw "The Start Menu shortcut is missing after the update."
    }
    $shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcutPath)
    if (-not ([IO.Path]::GetFullPath($shortcut.TargetPath)).Equals(
        [IO.Path]::GetFullPath($installedExe),
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "The Start Menu shortcut does not target the installed executable."
    }
    if (-not (Test-Path -LiteralPath $uninstallKey)) {
        throw "The per-user uninstall registration is missing after the update."
    }
    $uninstallRegistration = Get-ItemProperty -LiteralPath $uninstallKey
    if ([string]$uninstallRegistration.DisplayVersion -ne $NewerVersion) {
        throw "Uninstall DisplayVersion '$($uninstallRegistration.DisplayVersion)' does not match '$NewerVersion'."
    }

    $result = [ordered]@{
        success                         = $true
        older_version                   = $OlderVersion
        newer_version                   = $NewerVersion
        install_path                    = $installRoot
        older_about_version             = [string]$olderAbout.version
        newer_about_version             = [string]$newerAbout.version
        older_process_closed            = $true
        newer_process_relaunched        = $true
        setting_retained                = $true
        start_menu_shortcut_valid       = $true
        uninstall_registration_valid    = $true
        uninstall_display_version       = [string]$uninstallRegistration.DisplayVersion
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
    Write-Host "Two-version update validation passed: $resolvedOutputPath"
} finally {
    if ($null -eq $previousQtPlatform) {
        Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue
    } else {
        $env:QT_QPA_PLATFORM = $previousQtPlatform
    }
}
