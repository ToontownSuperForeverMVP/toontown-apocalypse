[CmdletBinding()]
param(
    [string]$Repository = "ToontownSuperForeverMVP/toontown-apocalypse",
    [string]$Branch = "main",
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot ".."))
)

$ErrorActionPreference = "Stop"
$downloadDirectory = Join-Path $ProjectRoot "downloads"
$archivePath = Join-Path $downloadDirectory "toontown-apocalypse-main.zip"
$partialArchivePath = "$archivePath.download"
$commitPath = Join-Path $downloadDirectory "main.commit"
$apiUrl = "https://api.github.com/repos/$Repository/commits/$Branch"
$archiveUrl = "https://github.com/$Repository/archive/refs/heads/$Branch.zip"

function Write-Status([string]$Message) {
    Write-Host "[Updater] $Message"
}

if ((git -C $ProjectRoot status --porcelain --untracked-files=no)) {
    throw "Tracked local changes were found. Commit or stash them before updating."
}

New-Item -ItemType Directory -Force -Path $downloadDirectory | Out-Null
$headers = @{ "User-Agent" = "Toontown-Apocalypse-Launcher" }
$commit = (Invoke-RestMethod -Uri $apiUrl -Headers $headers).sha
$installedCommit = if (Test-Path $commitPath) { (Get-Content $commitPath -Raw).Trim() } else { "" }

if ($commit -eq $installedCommit) {
    Write-Status "Already up to date ($($commit.Substring(0, 7)))."
    exit 0
}

$temporaryDirectory = Join-Path ([IO.Path]::GetTempPath()) ("ttap-update-" + [guid]::NewGuid().ToString("N"))
try {
    Write-Status "Downloading main at $($commit.Substring(0, 7))..."
    if (Test-Path $partialArchivePath) {
        Remove-Item -LiteralPath $partialArchivePath -Force
    }
    Invoke-WebRequest -Uri $archiveUrl -Headers $headers -OutFile $partialArchivePath -TimeoutSec 600
    if ((Get-Item -LiteralPath $partialArchivePath).Length -eq 0) {
        throw "GitHub returned an empty archive."
    }
    Move-Item -LiteralPath $partialArchivePath -Destination $archivePath -Force

    New-Item -ItemType Directory -Force -Path $temporaryDirectory | Out-Null
    Expand-Archive -LiteralPath $archivePath -DestinationPath $temporaryDirectory -Force
    $sourceDirectory = Get-ChildItem -LiteralPath $temporaryDirectory -Directory | Select-Object -First 1
    if (-not $sourceDirectory) {
        throw "GitHub returned an archive without a project directory."
    }

    Write-Status "Applying update..."
    Get-ChildItem -LiteralPath $sourceDirectory.FullName -Force | Where-Object { $_.Name -ne ".git" } | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $ProjectRoot -Recurse -Force
    }
    Set-Content -LiteralPath $commitPath -Value $commit -NoNewline
    Write-Status "Updated to $($commit.Substring(0, 7))."
}
finally {
    if (Test-Path $temporaryDirectory) {
        Remove-Item -LiteralPath $temporaryDirectory -Recurse -Force
    }
}
