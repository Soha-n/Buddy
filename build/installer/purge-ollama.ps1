# Remove Ollama and its models - but only when Buddy is what installed it.
#
# The uninstaller checks that attribution before running this script, so
# reaching here means Buddy put Ollama on this machine and is taking it back
# off. A pre-existing Ollama is never passed to this script: it may serve other
# applications, and its models are tens of gigabytes nobody asked us to discard.
#
# Ollama installs per-user, so - like Buddy's own data - it has to be found by
# walking every profile rather than trusting the elevated $env:LOCALAPPDATA.
#
# The uninstaller executable is discovered rather than assumed. Ollama has
# shipped both Inno Setup (unins000.exe) and NSIS (Uninstall.exe) builds, and
# hardcoding either name means silently skipping the real uninstall on the other.
#
# Never fails the uninstall: leaving Ollama behind is a far better outcome than
# an uninstaller that cannot complete.

$ErrorActionPreference = 'Continue'

$profileList = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\*'

# Model stores and program files, relative to a profile root. Models default to
# ~/.ollama, which is also where the manifests and blobs live.
$leaves = @(
    'AppData\Local\Programs\Ollama',
    'AppData\Local\Ollama',
    '.ollama'
)

function Invoke-OllamaUninstaller([string]$root) {
    $dir = Join-Path $root 'AppData\Local\Programs\Ollama'
    if (-not (Test-Path -LiteralPath $dir -PathType Container)) { return }

    # Whichever installer produced this build.
    $exe = Get-ChildItem -LiteralPath $dir -Filter '*.exe' -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^(unins\d*|uninstall)\.exe$' } |
        Select-Object -First 1
    if (-not $exe) { return }

    # /S covers NSIS; Inno accepts /SILENT and ignores /S, so pass both rather
    # than guessing which build this is.
    try {
        Write-Output "running $($exe.FullName)"
        Start-Process -FilePath $exe.FullName -ArgumentList '/S', '/SILENT', '/NORESTART' `
            -Wait -ErrorAction Stop
    }
    catch {
        Write-Output "uninstaller failed: $($_.Exception.Message)"
    }
}

function Remove-OllamaTree([string]$root) {
    foreach ($leaf in $leaves) {
        $target = Join-Path $root $leaf
        if (-not (Test-Path -LiteralPath $target -PathType Container)) { continue }
        try {
            Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction Stop
            Write-Output "removed $target"
        }
        catch {
            Write-Output "could not remove $target : $($_.Exception.Message)"
        }
    }
}

foreach ($entry in Get-ItemProperty $profileList -ErrorAction SilentlyContinue) {
    $root = $entry.ProfileImagePath
    if (-not $root) { continue }
    if (-not (Test-Path -LiteralPath $root -PathType Container)) { continue }

    Invoke-OllamaUninstaller $root
    Remove-OllamaTree $root
}

# A relocated model store is not under any profile, so the profile walk above
# cannot find it. OLLAMA_MODELS is the documented override and is what the app
# itself reads, so it is the one extra place worth checking.
$override = [Environment]::GetEnvironmentVariable('OLLAMA_MODELS', 'Machine')
if ($override -and (Test-Path -LiteralPath $override -PathType Container)) {
    try {
        Remove-Item -LiteralPath $override -Recurse -Force -ErrorAction Stop
        Write-Output "removed relocated model store $override"
    }
    catch {
        Write-Output "could not remove $override : $($_.Exception.Message)"
    }
}

# Ollama adds itself to PATH and registers a per-user uninstall entry; both are
# stale once the directory is gone.
foreach ($hive in 'HKCU:', 'HKLM:') {
    $key = "$hive\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Ollama"
    if (Test-Path -LiteralPath $key) {
        Remove-Item -LiteralPath $key -Recurse -Force -ErrorAction SilentlyContinue
        Write-Output "removed registry entry $key"
    }
}

exit 0
