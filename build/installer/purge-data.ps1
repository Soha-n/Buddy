# Remove Buddy's per-user data from every profile on the machine.
#
# Run by the uninstaller, elevated. It cannot simply use $env:LOCALAPPDATA:
# elevated, that resolves to the administrator's profile, not the person who
# actually used Buddy, and a shared machine may hold one copy per user.
#
# ProfileList is the authoritative list of real user profiles. Enumerating
# C:\Users by directory name instead would also match Default, Public and the
# service accounts, none of which are users.
#
# Never fails the uninstall. A profile on a disconnected network share, or one
# whose ACLs deny even an administrator, must not leave the app half-removed
# and unremovable - so every failure is reported and skipped.

$ErrorActionPreference = 'Continue'

$profileList = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\*'

# Guard against a malformed ProfileImagePath removing something unintended: a
# target must sit under a real directory and be named exactly "Buddy".
function Remove-BuddyData([string]$root) {
    if (-not $root) { return }
    if (-not (Test-Path -LiteralPath $root -PathType Container)) { return }

    $target = Join-Path $root 'AppData\Local\Buddy'
    if (-not (Test-Path -LiteralPath $target -PathType Container)) { return }

    # Refuse anything that did not resolve to a leaf named Buddy.
    $leaf = Split-Path $target -Leaf
    if ($leaf -ne 'Buddy') {
        Write-Output "skipping unexpected path: $target"
        return
    }

    try {
        Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction Stop
        Write-Output "removed $target"
    }
    catch {
        Write-Output "could not remove $target : $($_.Exception.Message)"
    }
}

foreach ($entry in Get-ItemProperty $profileList -ErrorAction SilentlyContinue) {
    Remove-BuddyData $entry.ProfileImagePath
}

# The uninstaller must succeed even when a profile could not be cleaned.
exit 0
