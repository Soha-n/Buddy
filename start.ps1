<#
    Buddy launcher.

    Checks prerequisites, then opens the backend and frontend in their own
    PowerShell windows. The two-terminal + venv-activation dance is the most
    common place to stumble on Windows, so this wraps it.

    Usage:  .\start.ps1
#>

$ErrorActionPreference = 'Stop'

$root = $PSScriptRoot
$backend = Join-Path $root 'backend'
$frontend = Join-Path $root 'frontend'
$venvPython = Join-Path $backend '.venv\Scripts\python.exe'

function Write-Step($message) {
    Write-Host "==> $message" -ForegroundColor Cyan
}

function Write-Warn($message) {
    Write-Host "    $message" -ForegroundColor Yellow
}

Write-Step 'Checking prerequisites'

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    throw 'Node.js was not found. Install it from https://nodejs.org'
}

# --- Backend venv ---------------------------------------------------------- #

if (-not (Test-Path $venvPython)) {
    Write-Warn 'Backend virtual environment missing; creating it now.'
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        throw 'Python was not found. Install it from https://python.org'
    }
    Push-Location $backend
    python -m venv .venv
    & $venvPython -m pip install --upgrade pip --quiet
    & $venvPython -m pip install -r requirements.txt --quiet
    Pop-Location
    Write-Host '    Backend dependencies installed.' -ForegroundColor Green
}

# --- Frontend deps --------------------------------------------------------- #

if (-not (Test-Path (Join-Path $frontend 'node_modules'))) {
    Write-Warn 'Frontend dependencies missing; installing now (this can take a minute).'
    Push-Location $frontend
    npm install --no-fund --no-audit
    Pop-Location
    Write-Host '    Frontend dependencies installed.' -ForegroundColor Green
}

# --- Ollama ---------------------------------------------------------------- #

Write-Step 'Checking Ollama'

$ollamaOk = $false
try {
    $version = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/version' -TimeoutSec 5
    Write-Host "    Ollama $($version.version) is running." -ForegroundColor Green
    $ollamaOk = $true
} catch {
    $ollamaOk = $false
}

if (-not $ollamaOk) {
    if (Get-Command ollama -ErrorAction SilentlyContinue) {
        Write-Warn 'Ollama is installed but not responding on port 11434.'
        Write-Warn 'Start it with "ollama serve" or by launching the Ollama app.'
    } else {
        Write-Warn 'Ollama was not found. Install it from https://ollama.com/download'
    }
    Write-Warn 'Buddy will still start and will show setup instructions in the UI.'
}

# --- Launch ---------------------------------------------------------------- #

Write-Step 'Starting backend on http://127.0.0.1:8000'
Start-Process powershell -ArgumentList @(
    '-NoExit',
    '-NoProfile',
    '-Command',
    "Set-Location '$backend'; & '$venvPython' -m uvicorn app.main:app --reload --port 8000"
)

Write-Step 'Starting frontend on http://localhost:5173'
Start-Process powershell -ArgumentList @(
    '-NoExit',
    '-NoProfile',
    '-Command',
    "Set-Location '$frontend'; npm run dev"
)

Start-Sleep -Seconds 4
Write-Host ''
Write-Host 'Buddy is starting up. Opening http://localhost:5173' -ForegroundColor Green
Write-Host 'Close the two PowerShell windows to stop it.' -ForegroundColor DarkGray
Start-Process 'http://localhost:5173'
