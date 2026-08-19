<#
.SYNOPSIS
    Build every release asset and the manifest the web installer reads.

.DESCRIPTION
    Produces, into build/release/:

        buddy-app-<version>.zip     shell + backend + bundled UI
        searxng-<version>.zip       prebuilt search payload (optional)
        manifest.json               sizes and SHA-256 for each asset

    The installer stub pins exact URLs and hashes from the manifest rather than
    resolving "latest" at install time, so an installer already in a user's
    hands cannot be broken by a later release.

.PARAMETER SkipSearxng
    Skip the search payload. It takes several minutes and needs a Python
    3.10-3.12 interpreter; the app runs without it via its search fallbacks.
#>
[CmdletBinding()]
param(
    [string]$Version = '1.0.0',
    [switch]$SkipSearxng,
    [string]$RepoSlug = 'Soha-n/Buddy'
)

$ErrorActionPreference = 'Stop'

$root = Resolve-Path (Join-Path $PSScriptRoot '..\..')
$build = Join-Path $root 'build'
$release = Join-Path $build 'release'
$payload = Join-Path $build 'payload'

Write-Host "==> Building Buddy $Version" -ForegroundColor Cyan

New-Item -ItemType Directory -Force -Path $release | Out-Null

# --- 1. Frontend ------------------------------------------------------------
# Built first: the PyInstaller spec bundles frontend/dist and fails without it.
Write-Host '==> Building frontend' -ForegroundColor Cyan
Push-Location (Join-Path $root 'frontend')
try {
    & npm ci
    if ($LASTEXITCODE -ne 0) { throw 'npm ci failed' }
    & npm run build
    if ($LASTEXITCODE -ne 0) { throw 'frontend build failed' }
}
finally { Pop-Location }

# --- 2. SearXNG payload -----------------------------------------------------
if (-not $SkipSearxng) {
    & (Join-Path $PSScriptRoot 'build-searxng.ps1')
}
else {
    Write-Host '==> Skipping SearXNG payload' -ForegroundColor Yellow
}

# --- 3. Backend -------------------------------------------------------------
Write-Host '==> Freezing backend' -ForegroundColor Cyan
$venvPy = Join-Path $root 'backend\.venv\Scripts\python.exe'
if (-not (Test-Path $venvPy)) { throw "backend venv not found at $venvPy" }

& $venvPy -m PyInstaller (Join-Path $build 'backend.spec') `
    --distpath (Join-Path $build 'dist') `
    --workpath (Join-Path $build 'work') `
    --noconfirm
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed' }

# --- 4. Desktop shell -------------------------------------------------------
Write-Host '==> Building desktop shell' -ForegroundColor Cyan
Push-Location (Join-Path $root 'desktop\src-tauri')
try {
    & cargo build --release
    if ($LASTEXITCODE -ne 0) { throw 'cargo build failed' }
}
finally { Pop-Location }

# --- 5. Assemble ------------------------------------------------------------
# One directory laid out exactly as the installed app, so what is tested here
# is what ships.
Write-Host '==> Assembling app' -ForegroundColor Cyan
$staging = Join-Path $build 'staging'
if (Test-Path $staging) { Remove-Item -Recurse -Force $staging }
New-Item -ItemType Directory -Force -Path $staging | Out-Null

Copy-Item (Join-Path $build 'dist\buddy-backend\*') $staging -Recurse -Force
Copy-Item (Join-Path $root 'desktop\src-tauri\target\release\buddy-desktop.exe') `
    (Join-Path $staging 'Buddy.exe') -Force

$appZip = Join-Path $release "buddy-app-$Version.zip"
if (Test-Path $appZip) { Remove-Item -Force $appZip }
Compress-Archive -Path (Join-Path $staging '*') -DestinationPath $appZip -CompressionLevel Optimal

$searxZip = $null
$searxDir = Join-Path $payload 'searxng'
if (Test-Path $searxDir) {
    $searxZip = Join-Path $release "searxng-$Version.zip"
    if (Test-Path $searxZip) { Remove-Item -Force $searxZip }
    Compress-Archive -Path (Join-Path $searxDir '*') -DestinationPath $searxZip -CompressionLevel Optimal
}

# --- 6. Manifest ------------------------------------------------------------
# SHA-256 per asset: the installer verifies before extracting, so a truncated
# or tampered download fails loudly instead of installing a broken app.
Write-Host '==> Writing manifest' -ForegroundColor Cyan
$base = "https://github.com/$RepoSlug/releases/download/v$Version"

function New-AssetEntry($path) {
    $item = Get-Item $path
    [ordered]@{
        name   = $item.Name
        url    = "$base/$($item.Name)"
        size   = $item.Length
        sha256 = (Get-FileHash $path -Algorithm SHA256).Hash.ToLower()
    }
}

$assets = [ordered]@{ app = New-AssetEntry $appZip }
if ($searxZip) { $assets.searxng = New-AssetEntry $searxZip }

$manifest = [ordered]@{
    version = $Version
    assets  = $assets
} | ConvertTo-Json -Depth 6

Set-Content -Path (Join-Path $release 'manifest.json') -Value $manifest -Encoding utf8

Write-Host ''
Write-Host "==> Release assets in $release" -ForegroundColor Green
Get-ChildItem $release | ForEach-Object {
    $mb = [math]::Round($_.Length / 1MB, 1)
    Write-Host ("    {0,-34} {1,8} MB" -f $_.Name, $mb)
}
