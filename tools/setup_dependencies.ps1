<#
.SYNOPSIS
    Ensures the bundled Panda3D runtime and the Python dependencies are installed.

.DESCRIPTION
    This script is invoked by start.bat before the game is started. It makes a
    fresh ZIP/local installation playable without any manual steps:

      1. Reuses an existing runtime at <project>\Panda3D\python\ppython.exe.
      2. If the runtime was extracted somewhere else in the checkout
         (for example Panda3D-SDK\Panda3D\...), it is renamed and moved to
         <project>\Panda3D.
      3. If no runtime exists at all, the official Panda3D build is downloaded
         from GitHub and installed into <project>\Panda3D.
      4. The packages from requirements.txt are installed into the bundled
         interpreter.

    Everything is a no-op (and needs no network access) once the installation
    is complete, so offline launches keep working.
#>

[CmdletBinding()]
param(
    [string]$ProjectRoot = (Join-Path $PSScriptRoot ".."),
    [string]$RuntimeRepository = "toontown-archipelago/panda3d",
    [string]$PythonVersion = "3.11",
    [switch]$Force,
    [switch]$SkipRuntime,
    [switch]$SkipRequirements
)

$ErrorActionPreference = "Stop"

# PowerShell 5.1 renders a progress bar for every Invoke-WebRequest call, which
# makes large downloads many times slower. Disable it.
$ProgressPreference = "SilentlyContinue"

# Windows PowerShell 5.1 defaults to TLS 1.0 on older Windows builds, which
# GitHub rejects.
try {
    [Net.ServicePointManager]::SecurityProtocol =
        [Net.ServicePointManager]::SecurityProtocol -bor
        [Net.SecurityProtocolType]::Tls12
}
catch {
    # Older .NET versions do not expose Tls12; nothing to do about it.
}

function Write-Status {
    param([string]$Message)
    Write-Host "[Setup] $Message" -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Message)
    Write-Host "[Setup] $Message" -ForegroundColor Green
}

function Write-Note {
    param([string]$Message)
    Write-Host "[Setup] $Message" -ForegroundColor Yellow
}

function Write-ErrorLine {
    param([string]$Message)
    Write-Host "[Setup] ERROR: $Message" -ForegroundColor Red
}

# Report failures on a single line instead of as a raw PowerShell exception.
trap {
    Write-ErrorLine "$($_.Exception.Message)"
    exit 1
}

# Runs an external program without letting its stderr output abort the script.
# PowerShell 5.1 promotes native stderr to a terminating error while
# $ErrorActionPreference is 'Stop', which is why every call goes through here.
function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [switch]$Quiet
    )

    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"

    try {
        if ($Quiet) {
            $lines = & $FilePath @Arguments 2>&1 | ForEach-Object { "$_" }
            $output = ($lines -join [Environment]::NewLine)
        }
        else {
            & $FilePath @Arguments
            $output = $null
        }

        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previous
    }

    return [pscustomobject]@{
        ExitCode = $exitCode
        Output   = $output
    }
}

$RuntimeDirectoryName = "Panda3D"
$InterpreterRelativePath = "python\ppython.exe"
$RuntimeInstallerFilter = "Panda3D-*.exe"

# ------------------------------------------------------------
# Configuration and path validation
# ------------------------------------------------------------

if ($RuntimeRepository -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') {
    throw "Invalid Panda3D repository name: $RuntimeRepository"
}

# Normalize the project path. Some launchers accidentally pass a trailing
# quotation mark.
$ProjectRoot = $ProjectRoot.Trim().Trim('"')

if (-not (Test-Path -LiteralPath $ProjectRoot -PathType Container)) {
    throw "Project directory does not exist: $ProjectRoot"
}

$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).ProviderPath
$ProjectRoot = $ProjectRoot.TrimEnd('\', '/')

$runtimeDirectory = Join-Path $ProjectRoot $RuntimeDirectoryName
$downloadDirectory = Join-Path $ProjectRoot "downloads"
$runtimeCacheDirectory = Join-Path $downloadDirectory "panda3d"
$markerPath = Join-Path $downloadDirectory "dependencies.json"

$headers = @{
    "User-Agent"           = "Toontown-Apocalypse-Launcher"
    "Accept"               = "application/vnd.github+json"
    "X-GitHub-Api-Version" = "2022-11-28"
}

function Test-RuntimeDirectory {
    param([string]$Directory)

    if ([string]::IsNullOrWhiteSpace($Directory)) {
        return $false
    }

    return (Test-Path -LiteralPath (
        Join-Path $Directory $InterpreterRelativePath
    ) -PathType Leaf)
}

function Get-RuntimeInterpreter {
    param([string]$Directory)

    return (Join-Path $Directory $InterpreterRelativePath)
}

# ------------------------------------------------------------
# Locate an existing runtime
# ------------------------------------------------------------

function Find-ExistingRuntime {
    <#
        Searches the checkout for an extracted Panda3D runtime. This catches
        the common case where the SDK was extracted into a nested folder such
        as "Panda3D-SDK\Panda3D\python\ppython.exe" and still has to be
        renamed/moved into place at the project root.
    #>

    $results = @()

    $candidates = @(
        Get-ChildItem `
            -LiteralPath $ProjectRoot `
            -Recurse -Force -File -Filter "ppython.exe" `
            -ErrorAction SilentlyContinue
    )

    foreach ($candidate in $candidates) {
        # Only "python\ppython.exe" inside a runtime root is a real match.
        if ($candidate.Directory.Name -ne "python") {
            continue
        }

        $relative = $candidate.FullName.Substring($ProjectRoot.Length).Trim('\', '/')

        # Never adopt anything inside updater-managed or VCS directories.
        if ($relative -match '^(\.git|downloads)(\\|/)') {
            continue
        }

        $root = $candidate.Directory.Parent.FullName

        if ($root -eq $runtimeDirectory) {
            continue
        }

        $results += $root
    }

    if ($results.Count -eq 0) {
        return $null
    }

    # Prefer the shallowest candidate; nested copies are usually leftovers.
    return @(
        $results | Sort-Object { ($_ -split '[\\/]').Count }, { $_.Length }
    )[0]
}

function Move-RuntimeIntoPlace {
    param([string]$Source)

    Write-Status "Found the Panda3D runtime at: $Source"
    Write-Status "Moving it to: $runtimeDirectory"

    New-Item -ItemType Directory -Force -Path $runtimeCacheDirectory | Out-Null

    # Move through a staging path so a runtime that currently lives *inside*
    # the destination (for example Panda3D\Panda3D-1.11.0\...) can be
    # relocated without deleting itself.
    $stagingPath = Join-Path `
        $runtimeCacheDirectory `
        ("move-" + [guid]::NewGuid().ToString("N"))

    Move-Item -LiteralPath $Source -Destination $stagingPath

    if (Test-Path -LiteralPath $runtimeDirectory) {
        Remove-Item -LiteralPath $runtimeDirectory -Recurse -Force
    }

    Move-Item -LiteralPath $stagingPath -Destination $runtimeDirectory

    Write-Ok "The Panda3D runtime is now at the project root."
}

# ------------------------------------------------------------
# Download the runtime
# ------------------------------------------------------------

function Get-RuntimeAsset {
    Write-Status "Looking up the latest Panda3D build..."

    $release = Invoke-RestMethod `
        -Uri "https://api.github.com/repos/$RuntimeRepository/releases/latest" `
        -Headers $headers `
        -TimeoutSec 60

    $assets = @($release.assets)

    if ($assets.Count -eq 0) {
        throw "The Panda3D release does not contain any downloadable files."
    }

    $exactPattern = '^Panda3D-.*-py' + [regex]::Escape($PythonVersion) + '-x64\.exe$'

    $asset = @(
        $assets | Where-Object { $_.name -match $exactPattern }
    ) | Select-Object -First 1

    if (-not $asset) {
        $asset = @(
            $assets | Where-Object { $_.name -match '^Panda3D-.*-x64\.exe$' }
        ) | Select-Object -First 1
    }

    if (-not $asset) {
        throw "No Windows x64 Panda3D build was found in release '$($release.tag_name)'."
    }

    return $asset
}

function Save-RuntimeInstaller {
    param($Asset)

    New-Item -ItemType Directory -Force -Path $runtimeCacheDirectory | Out-Null

    # Drop installers for older builds so the cache cannot grow forever.
    Get-ChildItem `
        -LiteralPath $runtimeCacheDirectory `
        -Filter $RuntimeInstallerFilter -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -ne $Asset.name } |
        Remove-Item -Force -ErrorAction SilentlyContinue

    $expectedSize = [int64]$Asset.size
    $installerPath = Join-Path $runtimeCacheDirectory $Asset.name

    if (Test-Path -LiteralPath $installerPath) {
        if ((Get-Item -LiteralPath $installerPath).Length -eq $expectedSize) {
            Write-Status "Using the cached Panda3D runtime download."
            return $installerPath
        }

        Remove-Item -LiteralPath $installerPath -Force
    }

    $partialPath = "$installerPath.part"

    if ((Test-Path -LiteralPath $partialPath) -and
        (Get-Item -LiteralPath $partialPath).Length -gt $expectedSize) {
        Remove-Item -LiteralPath $partialPath -Force
    }

    Write-Status ("Downloading $($Asset.name) ({0:N0} MB)..." -f ($expectedSize / 1MB))

    $downloaded = $false

    for ($attempt = 1; $attempt -le 3 -and -not $downloaded; $attempt++) {
        try {
            $parameters = @{
                Uri             = $Asset.browser_download_url
                Headers         = $headers
                OutFile         = $partialPath
                UseBasicParsing = $true
                TimeoutSec      = 1800
            }

            # Resume a partially downloaded file instead of starting over.
            if ((Test-Path -LiteralPath $partialPath) -and
                (Get-Item -LiteralPath $partialPath).Length -gt 0) {
                $parameters["Resume"] = $true
            }

            Invoke-WebRequest @parameters

            $downloaded = $true
        }
        catch {
            if ($attempt -eq 3) {
                throw "Could not download the Panda3D runtime: $_"
            }

            Write-Note "Download interrupted; retrying ($attempt/3)..."
            Start-Sleep -Seconds 3
        }
    }

    $actualSize = (Get-Item -LiteralPath $partialPath).Length

    if ($actualSize -ne $expectedSize) {
        throw "The Panda3D download is incomplete ($actualSize of $expectedSize bytes)."
    }

    Move-Item -LiteralPath $partialPath -Destination $installerPath -Force

    return $installerPath
}

# ------------------------------------------------------------
# Install the runtime
# ------------------------------------------------------------

function Get-SevenZipPath {
    $candidates = @(
        (Join-Path $env:ProgramFiles "7-Zip\7z.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "7-Zip\7z.exe")
    )

    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return $candidate
        }
    }

    $command = Get-Command "7z.exe" -ErrorAction SilentlyContinue

    if ($command) {
        return $command.Source
    }

    return $null
}

function Install-Runtime {
    param([string]$InstallerPath)

    if (Test-Path -LiteralPath $runtimeDirectory) {
        Write-Status "Removing the previous (incomplete) runtime..."
        Remove-Item -LiteralPath $runtimeDirectory -Recurse -Force
    }

    $sevenZip = Get-SevenZipPath
    $installed = $false

    if ($sevenZip) {
        # Extracting the installer directly is quick and leaves no registry
        # entries, so it is preferred whenever 7-Zip is available.
        Write-Status "Extracting the Panda3D runtime..."

        $result = Invoke-Native `
            -FilePath $sevenZip `
            -Arguments @(
                "x", $InstallerPath, "-o$runtimeDirectory", "-y", '-x!$PLUGINSDIR'
            ) `
            -Quiet

        if ($result.ExitCode -eq 0 -and (Test-RuntimeDirectory $runtimeDirectory)) {
            $installed = $true
        }
        else {
            Write-Note "7-Zip could not extract the runtime; using the installer instead."
            Remove-Item -LiteralPath $runtimeDirectory -Recurse -Force
        }
    }

    if (-not $installed) {
        # The official NSIS installer. This build reports exit code 2 on
        # success, so the resulting layout is verified instead of the exit code.
        Write-Status "Installing the Panda3D runtime (this can take a minute)..."

        $process = Start-Process `
            -FilePath $InstallerPath `
            -ArgumentList @("/S", "/D=$runtimeDirectory") `
            -Wait -PassThru

        if (-not (Test-RuntimeDirectory $runtimeDirectory)) {
            throw "The Panda3D installer did not create a usable runtime (exit code $($process.ExitCode))."
        }
    }

    Write-Ok "Installed the Panda3D runtime into: $runtimeDirectory"
}

# ------------------------------------------------------------
# Verify the interpreter and pip
# ------------------------------------------------------------

function Ensure-Interpreter {
    param([string]$Interpreter)

    $probe = Invoke-Native `
        -FilePath $Interpreter `
        -Arguments @("-c", "import sys; print('%d.%d' % sys.version_info[:2])") `
        -Quiet

    if ($probe.ExitCode -ne 0) {
        throw "The bundled Python interpreter could not be started: $Interpreter"
    }

    $version = ("$($probe.Output)").Trim()

    if ($version -ne $PythonVersion) {
        Write-Note "The bundled runtime uses Python $version (expected $PythonVersion)."
    }

    $panda = Invoke-Native `
        -FilePath $Interpreter `
        -Arguments @("-c", "import panda3d.core") `
        -Quiet

    if ($panda.ExitCode -ne 0) {
        throw "The Panda3D runtime is not importable. Delete '$runtimeDirectory' and run start.bat again."
    }

    $pip = Invoke-Native -FilePath $Interpreter -Arguments @("-c", "import pip") -Quiet

    if ($pip.ExitCode -ne 0) {
        Write-Status "Bootstrapping pip in the bundled interpreter..."

        $ensure = Invoke-Native `
            -FilePath $Interpreter `
            -Arguments @("-m", "ensurepip", "--upgrade", "--default-pip") `
            -Quiet

        $pip = Invoke-Native -FilePath $Interpreter -Arguments @("-c", "import pip") -Quiet

        if ($pip.ExitCode -ne 0) {
            throw "pip is not available in the bundled Python interpreter: $($ensure.Output)"
        }
    }
}

# ------------------------------------------------------------
# Python dependencies
# ------------------------------------------------------------

function Install-Requirements {
    param([string]$Interpreter)

    $requirementsPath = Join-Path $ProjectRoot "requirements.txt"

    if (-not (Test-Path -LiteralPath $requirementsPath -PathType Leaf)) {
        return $true
    }

    $requirementsHash = (
        Get-FileHash -LiteralPath $requirementsPath -Algorithm SHA256
    ).Hash.ToLowerInvariant()

    $upToDate = $false

    if (-not $Force -and (Test-Path -LiteralPath $markerPath)) {
        $marker = $null

        try {
            $marker = Get-Content -LiteralPath $markerPath -Raw | ConvertFrom-Json
        }
        catch {
            $marker = $null
        }

        if ($marker -and
            $marker.requirementsSha256 -eq $requirementsHash -and
            $marker.interpreter -eq $Interpreter) {
            # This exact requirements file was installed before. Verifying
            # offline is instant and keeps launches working without internet.
            $verify = Invoke-Native `
                -FilePath $Interpreter `
                -Arguments @(
                    "-m", "pip", "install",
                    "--no-index",
                    "--disable-pip-version-check",
                    "--quiet",
                    "--requirement", $requirementsPath
                ) `
                -Quiet

            $upToDate = ($verify.ExitCode -eq 0)
        }
    }

    $installed = $true

    if ($upToDate) {
        Write-Ok "Python dependencies are up to date."
    }
    else {
        Write-Status "Installing Python dependencies..."

        & $Interpreter -m pip install `
            --disable-pip-version-check `
            --quiet `
            --retries 3 `
            --timeout 60 `
            --requirement $requirementsPath

        $installed = ($LASTEXITCODE -eq 0)

        if (-not $installed) {
            Write-Note "Some Python dependencies could not be installed. The game may not start correctly."
        }
        else {
            Write-Ok "Python dependencies installed."
        }
    }

    if ($installed) {
        New-Item -ItemType Directory -Force -Path $downloadDirectory | Out-Null

        @{
            requirementsSha256 = $requirementsHash
            interpreter        = $Interpreter
            updated            = (Get-Date).ToString("o")
        } | ConvertTo-Json | Set-Content -LiteralPath $markerPath -Encoding UTF8
    }

    return $installed
}

# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

New-Item -ItemType Directory -Force -Path $downloadDirectory | Out-Null

if (-not $SkipRuntime) {
    if (-not (Test-RuntimeDirectory $runtimeDirectory)) {
        $existing = Find-ExistingRuntime

        if ($existing) {
            Move-RuntimeIntoPlace -Source $existing
        }
    }

    if (Test-RuntimeDirectory $runtimeDirectory) {
        Write-Ok "Panda3D runtime found at $runtimeDirectory"
    }
    else {
        Write-Status "The Panda3D runtime is missing; downloading it now."

        $asset = Get-RuntimeAsset
        $installerPath = Save-RuntimeInstaller -Asset $asset

        Install-Runtime -InstallerPath $installerPath
    }

    Ensure-Interpreter -Interpreter (Get-RuntimeInterpreter $runtimeDirectory)
}

if (-not $SkipRequirements) {
    $null = Install-Requirements -Interpreter (Get-RuntimeInterpreter $runtimeDirectory)
}

Write-Ok "Installation is ready."
exit 0
