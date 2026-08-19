<#
.SYNOPSIS
    Compile the web installer stub, pinning it to a specific release.

.DESCRIPTION
    Reads build/release/manifest.json and passes each asset's URL, size and
    SHA-256 into the NSIS script as defines.

    Pinning at compile time rather than resolving "latest" at install time is
    deliberate: an installer already downloaded by a user then keeps working
    against the exact build it was made for, and publishing a new release
    cannot retroactively change what it fetches or break it.

.NOTES
    Requires NSIS with the INetC, nsisunz and Crypto plugins. INetC is what
    speaks HTTPS - stock NSISdl is plain HTTP only and cannot fetch from
    GitHub at all.
#>
[CmdletBinding()]
param(
    [string]$ManifestPath = (Join-Path $PSScriptRoot '..\release\manifest.json'),
    [string]$Nsis = 'makensis'
)

# Not 'Stop': Windows PowerShell turns stderr from a native executable into an
# ErrorRecord, and git, pip and makensis all log progress there. Exit codes are
# checked explicitly instead.
$ErrorActionPreference = 'Continue'
trap { Write-Host "FAILED: $_" -ForegroundColor Red; exit 1 }

if (-not (Test-Path $ManifestPath)) {
    throw "Manifest not found at $ManifestPath. Run build-all.ps1 first."
}

$manifest = Get-Content $ManifestPath -Raw | ConvertFrom-Json
$app = $manifest.assets.app
if (-not $app) { throw 'Manifest has no app asset.' }

$sizeMb = [math]::Round($app.size / 1MB, 0)

Write-Host "==> Building installer for v$($manifest.version)" -ForegroundColor Cyan
Write-Host "    payload: $($app.name) ($sizeMb MB)"
Write-Host "    sha256 : $($app.sha256)"

$nsi = Join-Path $PSScriptRoot '..\installer\buddy-web-installer.nsi'
$release = Join-Path $PSScriptRoot '..\release'
New-Item -ItemType Directory -Force -Path $release | Out-Null

# Verify the toolchain before invoking it, so a missing NSIS reports itself
# rather than surfacing as a confusing compile error.
$found = Get-Command $Nsis -ErrorAction SilentlyContinue
if (-not $found) {
    throw "NSIS not found on PATH as '$Nsis'. Install it (winget install NSIS.NSIS) and add its directory to PATH."
}

& $Nsis `
    "/DAPP_VERSION=$($manifest.version)" `
    "/DAPP_URL=$($app.url)" `
    "/DAPP_SHA256=$($app.sha256)" `
    "/DAPP_SIZE_MB=$sizeMb" `
    $nsi

if ($LASTEXITCODE -ne 0) { throw 'makensis failed' }

$output = Join-Path $release "Buddy-Setup-$($manifest.version).exe"
if (Test-Path $output) {
    $kb = [math]::Round((Get-Item $output).Length / 1KB, 0)
    Write-Host "==> Installer ready: $output ($kb KB)" -ForegroundColor Green
}
