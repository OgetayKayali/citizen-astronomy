[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Repository,

    [string]$Version,

    [string]$Notes
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-PythonStringConstant {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Source,

        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    $pattern = '(?m)^\s*' + [regex]::Escape($Name) + '\s*=\s*["''](?<value>[^"'']*)["'']\s*$'
    $match = [regex]::Match($Source, $pattern)
    if (-not $match.Success) {
        throw "Could not read $Name from photometry_app/app_metadata.py."
    }
    return $match.Groups["value"].Value
}

function Resolve-RequiredCommand {
    param([Parameter(Mandatory = $true)][string]$Name)

    $command = Get-Command $Name -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $command) {
        throw "Required command '$Name' was not found on PATH."
    }
    return $command.Source
}

function Resolve-Iscc {
    $command = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -ne $command) {
        return $command.Source
    }

    $candidates = @()
    $localAppData = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
    $programFilesX86 = [Environment]::GetFolderPath([Environment+SpecialFolder]::ProgramFilesX86)
    $programFiles = [Environment]::GetFolderPath([Environment+SpecialFolder]::ProgramFiles)
    if (-not [string]::IsNullOrWhiteSpace($localAppData)) {
        $candidates += Join-Path $localAppData "Programs\Inno Setup 6\ISCC.exe"
    }
    if (-not [string]::IsNullOrWhiteSpace($programFilesX86)) {
        $candidates += Join-Path $programFilesX86 "Inno Setup 6\ISCC.exe"
    }
    if (-not [string]::IsNullOrWhiteSpace($programFiles)) {
        $candidates += Join-Path $programFiles "Inno Setup 6\ISCC.exe"
    }

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
    }

    throw "Inno Setup 6 compiler (ISCC.exe) was not found. Install Inno Setup 6 or add ISCC.exe to PATH."
}

function Invoke-NativeCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,

        [Parameter(Mandatory = $true)]
        [string[]]$ArgumentList,

        [Parameter(Mandatory = $true)]
        [string]$Description
    )

    Write-Host "==> $Description"
    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE."
    }
}

function Invoke-NativeCapture {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,

        [Parameter(Mandatory = $true)]
        [string[]]$ArgumentList,

        [Parameter(Mandatory = $true)]
        [string]$Description
    )

    $output = @(& $FilePath @ArgumentList 2>&1)
    if ($LASTEXITCODE -ne 0) {
        $details = ($output | Out-String).Trim()
        if ([string]::IsNullOrWhiteSpace($details)) {
            throw "$Description failed with exit code $LASTEXITCODE."
        }
        throw "$Description failed with exit code $LASTEXITCODE.`n$details"
    }
    return $output
}

$Repository = $Repository.Trim()
if ($Repository -notmatch '^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?/[A-Za-z0-9_.-]+$') {
    throw "Repository must use the GitHub owner/name form (for example, ogetay/citizen-astronomy)."
}

$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$metadataPath = Join-Path $projectRoot "photometry_app\app_metadata.py"
if (-not (Test-Path -LiteralPath $metadataPath -PathType Leaf)) {
    throw "Application metadata was not found at '$metadataPath'."
}

$metadataSource = Get-Content -LiteralPath $metadataPath -Raw
$metadataVersion = Get-PythonStringConstant -Source $metadataSource -Name "APP_VERSION"
if ([string]::IsNullOrWhiteSpace($Version)) {
    $Version = $metadataVersion
}
$Version = $Version.Trim()
if (-not $Version.Equals($metadataVersion, [StringComparison]::Ordinal)) {
    throw "-Version '$Version' does not match the version embedded in the application ('$metadataVersion'). Update APP_VERSION first."
}
$versionPattern = '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-(?<prerelease>[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*))?(?:\+[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?$'
$versionMatch = [regex]::Match($Version, $versionPattern)
if (($Version.Length -gt 64) -or (-not $versionMatch.Success)) {
    throw "Version '$Version' is not a safe semantic version (for example, 0.1.1-alpha.1)."
}
$prerelease = $versionMatch.Groups["prerelease"].Value
foreach ($identifier in @($prerelease -split "\.")) {
    if (($identifier -match '^[0-9]+$') -and ($identifier.Length -gt 1) -and $identifier.StartsWith("0")) {
        throw "Version '$Version' has a numeric prerelease identifier with a forbidden leading zero."
    }
}

$appId = Get-PythonStringConstant -Source $metadataSource -Name "APP_USER_MODEL_ID"
$channel = Get-PythonStringConstant -Source $metadataSource -Name "APP_UPDATE_CHANNEL"
$configuredRepository = Get-PythonStringConstant -Source $metadataSource -Name "APP_UPDATE_GITHUB_REPOSITORY"
$manifestAssetName = Get-PythonStringConstant -Source $metadataSource -Name "APP_UPDATE_MANIFEST_ASSET_NAME"
if ([string]::IsNullOrWhiteSpace($appId)) {
    throw "APP_USER_MODEL_ID must be configured before publishing."
}
if ($channel -notmatch '^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$') {
    throw "APP_UPDATE_CHANNEL '$channel' is invalid."
}
if ([string]::IsNullOrWhiteSpace($manifestAssetName) -or
    [IO.Path]::GetFileName($manifestAssetName) -ne $manifestAssetName) {
    throw "APP_UPDATE_MANIFEST_ASSET_NAME must be a plain file name."
}
if ($channel -eq "stable") {
    if (-not [string]::IsNullOrWhiteSpace($prerelease)) {
        throw "Stable channel version '$Version' must not contain a prerelease identifier."
    }
} else {
    $firstPrereleaseIdentifier = @($prerelease -split "\.")[0]
    if ([string]::IsNullOrWhiteSpace($prerelease) -or
        (-not $firstPrereleaseIdentifier.Equals($channel, [StringComparison]::OrdinalIgnoreCase))) {
        throw "Version '$Version' must start its prerelease identifier with channel '$channel'."
    }
}
if ([string]::IsNullOrWhiteSpace($configuredRepository)) {
    throw "Set APP_UPDATE_GITHUB_REPOSITORY to the public '$Repository' repository before publishing."
}
if (-not $configuredRepository.Equals($Repository, [StringComparison]::OrdinalIgnoreCase)) {
    throw "APP_UPDATE_GITHUB_REPOSITORY is '$configuredRepository', but -Repository is '$Repository'."
}
$effectiveNotes = if ($PSBoundParameters.ContainsKey("Notes")) {
    $Notes.Trim()
} else {
    "Citizen Astronomy $channel update $Version."
}
if ([string]::IsNullOrWhiteSpace($effectiveNotes)) {
    throw "Notes must not be empty when -Notes is supplied."
}

$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "Project Python was not found at '$pythonPath'. Create the .venv and install the release dependencies first."
}

$gitPath = Resolve-RequiredCommand -Name "git.exe"
$ghPath = Resolve-RequiredCommand -Name "gh.exe"
$isccPath = Resolve-Iscc

$specPath = Join-Path $projectRoot "CitizenAstronomyAlphaReview.spec"
$issPath = Join-Path $projectRoot "packaging\inno\CitizenAstronomyAlphaReview.iss"
$bundleExe = Join-Path $projectRoot "_tmp_alpha_review_dist\CitizenAstronomyAlphaReview\CitizenAstronomyAlphaReview.exe"
$fixturesPath = Join-Path $projectRoot "packaging\fixtures"
$packagedSmokeOutput = Join-Path $projectRoot "_tmp_packaged_alpha_smoke_result.json"
$formatSmokeOutput = Join-Path $projectRoot "_tmp_packaged_format_smoke_result.json"
$distDirectory = Join-Path $projectRoot "packaging\dist"
$outputBaseFilename = "CitizenAstronomyAlphaReview-$Version-Setup"
$installerAssetName = "$outputBaseFilename.exe"
$installerPath = Join-Path $distDirectory $installerAssetName
$manifestPath = Join-Path $distDirectory $manifestAssetName
$tag = "v$Version"

$startupSmokePath = Join-Path $projectRoot "_tmp_startup_smoke.py"
foreach ($requiredPath in @($specPath, $issPath, $startupSmokePath)) {
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        throw "Required release input is missing: $requiredPath"
    }
}

Push-Location $projectRoot
try {
    $gitRootLines = @(Invoke-NativeCapture -FilePath $gitPath -ArgumentList @(
        "rev-parse", "--show-toplevel"
    ) -Description "Locating the Git repository")
    $pathTrimCharacters = [char[]]@("\", "/")
    $gitRoot = [IO.Path]::GetFullPath([string]$gitRootLines[0]).TrimEnd($pathTrimCharacters)
    if (-not $gitRoot.Equals($projectRoot.TrimEnd($pathTrimCharacters), [StringComparison]::OrdinalIgnoreCase)) {
        throw "Run this publisher from the Photometry repository; Git resolved the root as '$gitRoot'."
    }

    $statusLines = @(Invoke-NativeCapture -FilePath $gitPath -ArgumentList @(
        "status", "--porcelain=v1", "--untracked-files=all"
    ) -Description "Checking release inputs")
    $allowedUntrackedPatterns = @(
        '^_tmp_alpha_review_(?:build|dist)(?:/|$)',
        '^packaging/dist(?:/|$)',
        '^packaging/fixtures(?:/|$)',
        '^_tmp_packaged_(?:alpha_smoke|format_smoke)_result\.json$',
        '^_tmp_(?:app_)?startup_smoke_(?:err|out|result)\.(?:txt|json)$',
        '^_tmp_(?:pyinstaller|inno)_build\.log$'
    )
    $releaseInputChanges = @()
    foreach ($statusLine in $statusLines) {
        $line = [string]$statusLine
        if ([string]::IsNullOrWhiteSpace($line)) {
            continue
        }
        if ($line.StartsWith("?? ")) {
            $untrackedPath = $line.Substring(3).Trim('"').Replace("\", "/")
            $allowed = $false
            foreach ($pattern in $allowedUntrackedPatterns) {
                if ($untrackedPath -match $pattern) {
                    $allowed = $true
                    break
                }
            }
            if ($allowed) {
                continue
            }
        }
        $releaseInputChanges += $line
    }
    if ($releaseInputChanges.Count -ne 0) {
        throw "Release source files are modified, staged, or untracked. Commit or restore them before publishing:`n$($releaseInputChanges -join [Environment]::NewLine)"
    }
    Write-Host "==> Release source files are clean (known generated build artifacts are ignored)"

    $commitLines = @(Invoke-NativeCapture -FilePath $gitPath -ArgumentList @(
        "rev-parse", "HEAD"
    ) -Description "Reading the release commit")
    $commit = ([string]$commitLines[0]).Trim()

    Invoke-NativeCommand -FilePath $ghPath -ArgumentList @(
        "auth", "status"
    ) -Description "Validating GitHub CLI authentication"

    $repositoryJsonLines = @(Invoke-NativeCapture -FilePath $ghPath -ArgumentList @(
        "repo", "view", $Repository, "--json", "nameWithOwner,isPrivate,visibility"
    ) -Description "Checking GitHub repository accessibility")
    $repositoryData = ($repositoryJsonLines -join [Environment]::NewLine) | ConvertFrom-Json
    if (($repositoryData.isPrivate -eq $true) -or ([string]$repositoryData.visibility -ne "PUBLIC")) {
        throw "GitHub repository '$Repository' is not public. Update publishing requires a public repository."
    }
    if (-not ([string]$repositoryData.nameWithOwner).Equals($Repository, [StringComparison]::OrdinalIgnoreCase)) {
        throw "GitHub resolved '$Repository' as '$($repositoryData.nameWithOwner)'; check the owner/name value."
    }

    Invoke-NativeCommand -FilePath $ghPath -ArgumentList @(
        "api", "repos/$Repository/commits/$commit", "--silent"
    ) -Description "Verifying the release commit is on GitHub"

    $existingTags = @(Invoke-NativeCapture -FilePath $ghPath -ArgumentList @(
        "api", "--paginate", "repos/$Repository/releases?per_page=100", "--jq", ".[].tag_name"
    ) -Description "Checking existing GitHub releases")
    if ($existingTags -contains $tag) {
        throw "GitHub release '$tag' already exists in '$Repository'. Refusing to overwrite it."
    }

    Invoke-NativeCommand -FilePath $pythonPath -ArgumentList @(
        "packaging\generate_smoke_fixtures.py"
    ) -Description "Generating packaged smoke fixtures"

    Invoke-NativeCommand -FilePath $pythonPath -ArgumentList @(
        "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath", "_tmp_alpha_review_dist",
        "--workpath", "_tmp_alpha_review_build",
        "CitizenAstronomyAlphaReview.spec"
    ) -Description "Building the canonical PyInstaller bundle"

    if (-not (Test-Path -LiteralPath $bundleExe -PathType Leaf)) {
        throw "PyInstaller completed but the packaged executable was not created at '$bundleExe'."
    }

    Invoke-NativeCommand -FilePath $pythonPath -ArgumentList @(
        "-m", "py_compile",
        "photometry_app\main.py",
        "photometry_app\core\packaged_format_smoke.py",
        "scripts\run_packaged_alpha_smoke.py",
        "tests\test_packaged_format_smoke.py"
    ) -Description "Compiling release smoke sources"

    Invoke-NativeCommand -FilePath $pythonPath -ArgumentList @(
        "-m", "pytest",
        "tests\test_app_updates.py",
        "tests\test_release_update_contract.py",
        "-q"
    ) -Description "Running updater core and release contract tests"

    Invoke-NativeCommand -FilePath $pythonPath -ArgumentList @(
        "-m", "pytest",
        "tests\test_workers.py",
        "tests\test_main_window.py",
        "-q",
        "-k", "UpdateWorker or about_dialog_mentions or file_menu_shows_check_for_updates or update_installer_launches or update_check_completion"
    ) -Description "Running updater worker and menu tests"

    Invoke-NativeCommand -FilePath $pythonPath -ArgumentList @(
        "-m", "pytest",
        "tests\test_qt_image_formats.py",
        "tests\test_packaged_format_smoke.py",
        "-q"
    ) -Description "Running release packaging tests"

    Invoke-NativeCommand -FilePath $pythonPath -ArgumentList @(
        "_tmp_startup_smoke.py"
    ) -Description "Running source startup smoke"

    Invoke-NativeCommand -FilePath $pythonPath -ArgumentList @(
        "scripts\run_packaged_alpha_smoke.py",
        "--exe", $bundleExe,
        "--fixtures", $fixturesPath,
        "--output", $packagedSmokeOutput
    ) -Description "Running packaged startup, format, and About smoke"

    Invoke-NativeCommand -FilePath $bundleExe -ArgumentList @(
        "--packaged-format-smoke",
        "--packaged-format-smoke-fixtures", $fixturesPath,
        "--packaged-format-smoke-output", $formatSmokeOutput
    ) -Description "Running the direct frozen format smoke"

    if (-not (Test-Path -LiteralPath $distDirectory -PathType Container)) {
        $null = New-Item -ItemType Directory -Path $distDirectory
    }
    Remove-Item -LiteralPath $installerPath, $manifestPath -Force -ErrorAction SilentlyContinue

    Invoke-NativeCommand -FilePath $isccPath -ArgumentList @(
        "/DAppVersion=$Version",
        "/DOutputBaseFilename=$outputBaseFilename",
        $issPath
    ) -Description "Building the versioned Inno Setup installer"

    if (-not (Test-Path -LiteralPath $installerPath -PathType Leaf)) {
        throw "ISCC completed but the versioned installer was not created at '$installerPath'."
    }

    $installerInfo = Get-Item -LiteralPath $installerPath
    $installerHash = (Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $manifest = [ordered]@{
        schema_version    = 1
        app_id            = $appId
        channel           = $channel
        version           = $Version
        installer_asset   = $installerAssetName
        installer_size    = [int64]$installerInfo.Length
        installer_sha256 = $installerHash
        notes             = $effectiveNotes
    }

    $manifestJson = $manifest | ConvertTo-Json -Depth 3
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($manifestPath, $manifestJson + [Environment]::NewLine, $utf8NoBom)

    $expectedFields = @(
        "schema_version",
        "app_id",
        "channel",
        "version",
        "installer_asset",
        "installer_size",
        "installer_sha256",
        "notes"
    )
    $manifestCheck = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    $actualFields = @($manifestCheck.PSObject.Properties.Name)
    $fieldDifference = @(Compare-Object -ReferenceObject $expectedFields -DifferenceObject $actualFields)
    if ($fieldDifference.Count -ne 0) {
        throw "Generated update manifest does not contain exactly the required fields."
    }
    if (($manifestCheck.schema_version -ne 1) -or
        ($manifestCheck.app_id -ne $appId) -or
        ($manifestCheck.channel -ne $channel) -or
        ($manifestCheck.version -ne $Version) -or
        ($manifestCheck.installer_asset -ne $installerAssetName) -or
        ([int64]$manifestCheck.installer_size -ne [int64]$installerInfo.Length) -or
        ($manifestCheck.installer_sha256 -ne $installerHash) -or
        ($manifestCheck.notes -ne $effectiveNotes)) {
        throw "Generated update manifest failed its value validation."
    }

    Write-Host "==> Installer: $installerAssetName"
    Write-Host "    Size: $($installerInfo.Length) bytes"
    Write-Host "    SHA256: $installerHash"

    Invoke-NativeCommand -FilePath $ghPath -ArgumentList @(
        "release", "create", $tag,
        $installerPath,
        $manifestPath,
        "--repo", $Repository,
        "--target", $commit,
        "--title", "Citizen Astronomy $Version",
        "--notes", $effectiveNotes,
        "--prerelease"
    ) -Description "Publishing the GitHub prerelease"

    $releaseUrlLines = @(Invoke-NativeCapture -FilePath $ghPath -ArgumentList @(
        "release", "view", $tag,
        "--repo", $Repository,
        "--json", "url",
        "--jq", ".url"
    ) -Description "Reading the published release URL")
    Write-Host "Published prerelease: $(([string]$releaseUrlLines[0]).Trim())"
} finally {
    Pop-Location
}
