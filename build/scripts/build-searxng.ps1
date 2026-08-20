<#
.SYNOPSIS
    Produce the prebuilt SearXNG payload shipped with the installer.

.DESCRIPTION
    SearXNG has no binary release: it is source plus Docker only. Buddy's
    runtime installer clones it and builds a venv, which works on a developer
    machine and cannot work on an end user's - there is no git and no Python
    there at all.

    So the payload is built once, here, and shipped. This script clones the
    source, builds a venv with a Python 3.10-3.12 interpreter (SearXNG does not
    support newer), installs the dependencies, and writes the Windows shim that
    SearXNG needs because searx/valkeydb.py imports the Unix-only `pwd` module
    at import time.

    The result is copied into place on first run by searxng_manager._adopt_bundle.

.NOTES
    Licence: SearXNG is AGPL-3.0. Shipping its source in an installer is
    distribution, so the release must carry its licence and a source offer.
#>
[CmdletBinding()]
param(
    [string]$OutDir = (Join-Path $PSScriptRoot '..\payload\searxng'),
    [string]$Ref = 'master',
    # Must be a version SearXNG supports (3.10-3.12). Shipped as the payload's
    # runtime, so it is what SearXNG actually executes on the user's machine.
    [string]$EmbedVersion = '3.11.9',
    # Explicit interpreter path, for environments without the py launcher -
    # GitHub's setup-python action installs Python without registering it.
    [string]$PythonPath
)

# Not 'Stop': Windows PowerShell turns stderr from a native executable into an
# ErrorRecord, and git, pip and makensis all log progress there. Exit codes are
# checked explicitly instead.
$ErrorActionPreference = 'Continue'
trap { Write-Host "FAILED: $_" -ForegroundColor Red; exit 1 }

Write-Host '==> Building SearXNG payload' -ForegroundColor Cyan

# SearXNG supports 3.10-3.12; newest first, matching searxng_manager.
$python = $null

if ($PythonPath) {
    if (-not (Test-Path $PythonPath)) { throw "PythonPath not found: $PythonPath" }
    $python = $PythonPath
}
else {
    foreach ($version in '3.12', '3.11', '3.10') {
        $probe = & py "-$version" -c 'import sys; print(sys.executable)' 2>$null
        if ($LASTEXITCODE -eq 0 -and $probe) { $python = $probe.Trim(); break }
    }
    # Fall back to whatever `python` resolves to, but only if its version is in
    # range - building the payload with 3.13+ produces one SearXNG cannot run.
    if (-not $python) {
        $probe = & python -c 'import sys; print(sys.executable if sys.version_info[:2] in ((3,10),(3,11),(3,12)) else "")' 2>$null
        if ($probe) { $python = $probe.Trim() }
    }
}

if (-not $python) {
    throw 'No Python 3.10-3.12 found. SearXNG does not support newer versions. Pass -PythonPath to point at one explicitly.'
}
Write-Host "    interpreter: $python"

$src = Join-Path $OutDir 'src'
$venv = Join-Path $OutDir 'venv'

if (Test-Path $OutDir) { Remove-Item -Recurse -Force $OutDir }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

Write-Host '    cloning source...'
# SearXNG's tree contains 'utils/templates/etc/httpd/.../searxng.conf:socket',
# and a colon is not a legal character in a Windows filename - a plain clone
# fails at checkout with "invalid path". So the clone is done without a working
# tree, then a sparse checkout pulls only what the payload actually runs, which
# excludes utils/ entirely.
#
# Shallow throughout: the payload needs the tree, not the history.
# No --filter=blob:none: the pathspec checkout below needs the blobs locally,
# and a partial clone makes it fail on unreadable objects instead.
& git clone --depth 1 --branch $Ref --no-checkout `
    https://github.com/searxng/searxng.git $src
if ($LASTEXITCODE -ne 0) { throw 'git clone failed' }

Push-Location $src
try {
    # Cone mode keeps this to whole directories, which is all that is needed.
    & git sparse-checkout init --cone
    if ($LASTEXITCODE -ne 0) { throw 'git sparse-checkout init failed' }

    # searx/ is the application; the rest are what its install and runtime read.
    & git sparse-checkout set searx searxng_extra
    if ($LASTEXITCODE -ne 0) { throw 'git sparse-checkout set failed' }

    # Restricted to the sparse paths and the few root files needed. A bare
    # `git checkout $Ref` would re-expand the whole tree and hit the illegal
    # paths again regardless of the sparse config.
    & git checkout $Ref -- searx searxng_extra requirements.txt LICENSE
    if ($LASTEXITCODE -ne 0) { throw 'git checkout failed' }
}
finally { Pop-Location }
if (-not (Test-Path (Join-Path $src 'requirements.txt'))) {
    throw 'requirements.txt missing after checkout - cannot install dependencies'
}

# Drop the clone's own .git - it is ~100 MB of history the payload never needs.
$dotGit = Join-Path $src '.git'
if (Test-Path $dotGit) { Remove-Item -Recurse -Force $dotGit }

# A virtualenv is NOT usable here. Its python.exe is a ~270 KB launcher that
# resolves the real runtime through pyvenv.cfg's `home`, so it needs the base
# interpreter's DLL and stdlib to exist on the machine. On an end-user machine
# they do not, and the launcher simply hangs. Rewriting pyvenv.cfg does not
# help - there is no local runtime for it to point at.
#
# The embeddable distribution is the fix: a self-contained ~11 MB runtime with
# its own python.dll and a zipped stdlib, explicitly designed to be shipped
# inside an application. Dependencies are installed into it with --target,
# since it has no venv machinery.
Write-Host '    downloading embeddable runtime...'
$embedZip = Join-Path $OutDir 'embed.zip'
$embedUrl = "https://www.python.org/ftp/python/$EmbedVersion/python-$EmbedVersion-embed-amd64.zip"
Invoke-WebRequest -Uri $embedUrl -OutFile $embedZip -UseBasicParsing
Expand-Archive -LiteralPath $embedZip -DestinationPath $venv -Force
Remove-Item -Force $embedZip

$venvPy = Join-Path $venv 'python.exe'
if (-not (Test-Path $venvPy)) { throw "embeddable runtime missing python.exe at $venvPy" }

# The embeddable build ships a ._pth file that pins sys.path and disables
# site-packages entirely. Removing it restores normal path handling so the
# site-packages directory below is importable.
Get-ChildItem -Path $venv -Filter '*._pth' | Remove-Item -Force

$sitePackages = Join-Path $venv 'Lib\site-packages'
New-Item -ItemType Directory -Force -Path $sitePackages | Out-Null

Write-Host '    installing dependencies (slow)...'
# No pip in the embeddable build, so it is bootstrapped from the build
# machine's interpreter and told to install into the payload.
& $python -m pip install --quiet --upgrade --target $sitePackages pip
& $python -m pip install --quiet --target $sitePackages -r (Join-Path $src 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'dependency install failed' }
# Not in requirements.txt, but searx needs it for timezone data on Windows.
& $python -m pip install --quiet --target $sitePackages tzdata
if ($LASTEXITCODE -ne 0) { throw 'tzdata install failed' }

# searx/valkeydb.py does `import pwd` at import time, which is an immediate
# ModuleNotFoundError on Windows. Buddy runs with Valkey disabled, so the shim
# only ever has to satisfy the import.
Write-Host '    writing pwd shim...'
$shim = @'
"""Minimal `pwd` stand-in for Windows.

SearXNG imports this at module load to build a Valkey socket path. Buddy runs
with Valkey disabled, so nothing here is ever consulted for real.
"""

import os


class struct_passwd(tuple):
    def __new__(cls, entry):
        return super().__new__(cls, entry)

    pw_name = property(lambda self: self[0])
    pw_passwd = property(lambda self: self[1])
    pw_uid = property(lambda self: self[2])
    pw_gid = property(lambda self: self[3])
    pw_gecos = property(lambda self: self[4])
    pw_dir = property(lambda self: self[5])
    pw_shell = property(lambda self: self[6])


def _entry():
    name = os.environ.get("USERNAME", "buddy")
    home = os.path.expanduser("~")
    return struct_passwd((name, "x", 0, 0, name, home, ""))


def getpwuid(_uid):
    return _entry()


def getpwnam(_name):
    return _entry()


def getpwall():
    return [_entry()]
'@
Set-Content -Path (Join-Path $sitePackages 'pwd.py') -Value $shim -Encoding utf8

# Trim what the payload never needs. pip alone is ~13 MB.
Write-Host '    trimming...'
foreach ($junk in 'pip', 'setuptools', 'wheel', 'pkg_resources') {
    Get-ChildItem -Path $sitePackages -Filter "$junk*" -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
}
Get-ChildItem -Path $OutDir -Include '__pycache__' -Recurse -Directory -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem -Path $OutDir -Include '*.pyc' -Recurse -File -ErrorAction SilentlyContinue |
    Remove-Item -Force -ErrorAction SilentlyContinue

# Ship the licence alongside: AGPL-3.0 compliance, not decoration.
$license = Join-Path $src 'LICENSE'
if (Test-Path $license) { Copy-Item $license (Join-Path $OutDir 'LICENSE-SEARXNG') }

$sizeMb = [math]::Round((Get-ChildItem $OutDir -Recurse -File | Measure-Object Length -Sum).Sum / 1MB, 1)
Write-Host "==> SearXNG payload ready: $OutDir ($sizeMb MB)" -ForegroundColor Green
