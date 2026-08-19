"""Where and when the user is, so "here" and "today" mean something.

A local model has no idea what day it is or where it is running. Without this,
"current temperature" is unanswerable even with web access, and "is that open
tomorrow" resolves against the model's training cutoff instead of the calendar.

Everything here comes from the user's own machine. No IP geolocation service, no
third-party API, nothing that carries terms of service or a non-commercial
clause - which matters because Buddy is a commercial product that runs entirely
on the user's device.

Three sources, in descending order of precision:

1. **Manual** - the user typed their city. Always wins; they know best.
2. **Windows Location Service** - `System.Device.Location.GeoCoordinateWatcher`
   returns real coordinates from the OS, using whatever WiFi/GPS signals Windows
   already has. Off by default in Windows privacy settings, so it may be denied,
   and asking is itself a permission prompt - hence it is only attempted when the
   user opts in.
3. **Regional settings** - timezone, culture and home country, read from the OS.
   Always available, no permission needed, no network. Coarse (a country, not a
   city) but enough to localize a search query.

Time always comes from the system clock and is never networked.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import subprocess
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# PowerShell calls are the only way to reach these Windows APIs from Python
# without a native extension. Bounded tightly: the location watcher blocks while
# it acquires a fix, and a hung call would stall a user-visible answer.
_POWERSHELL_TIMEOUT = 20.0
_QUICK_POWERSHELL_TIMEOUT = 8.0


@dataclass
class UserLocation:
    city: str | None = None
    region: str | None = None
    country: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    timezone: str | None = None
    #: "manual", "os_gps", "os_region", or "unavailable" - surfaced to the user
    #: so a coarse regional guess is never mistaken for something precise.
    source: str = "unavailable"

    @property
    def label(self) -> str:
        """Human-readable place name, most specific part first."""
        parts = [p for p in (self.city, self.region, self.country) if p]
        return ", ".join(parts) if parts else "unknown location"

    @property
    def has_coordinates(self) -> bool:
        return self.latitude is not None and self.longitude is not None


# Cached for the process lifetime: a machine does not move between messages, and
# re-running a PowerShell probe per turn would add latency for nothing.
_cached_location: UserLocation | None = None
_manual_location: UserLocation | None = None
_lookup_lock = asyncio.Lock()


# --------------------------------------------------------------------------- #
# Time - always local, never networked
# --------------------------------------------------------------------------- #


def local_time_context() -> dict[str, str]:
    """The current date, time and timezone as the user's machine sees them."""
    now = datetime.datetime.now().astimezone()
    try:
        tz_name = str(now.tzinfo) if now.tzinfo else time.tzname[0]
    except Exception:
        tz_name = time.tzname[0]

    offset = now.utcoffset() or datetime.timedelta(0)
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    offset_text = f"UTC{sign}{abs(total_minutes) // 60:02d}:{abs(total_minutes) % 60:02d}"

    return {
        "iso": now.isoformat(timespec="seconds"),
        "date": now.strftime("%A, %d %B %Y"),
        "time": now.strftime("%H:%M"),
        "timezone": tz_name,
        "offset": offset_text,
        "year": str(now.year),
    }


# --------------------------------------------------------------------------- #
# Place - from the operating system only
# --------------------------------------------------------------------------- #


def _run_powershell(script: str, timeout: float) -> str | None:
    """Run a PowerShell snippet and return its stdout, or None on any failure."""
    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            # Prevents a console window flashing up when Buddy is packaged as a
            # windowed desktop app.
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (subprocess.SubprocessError, OSError, FileNotFoundError):
        logger.debug("powershell probe failed", exc_info=True)
        return None
    output = (completed.stdout or "").strip()
    return output or None


# Reads the OS's own regional configuration. No permission prompt, no network,
# and available on every Windows install - so this is the always-there fallback.
_REGION_SCRIPT = r"""
$tz = (Get-TimeZone).Id
$culture = (Get-Culture).Name
$countryName = ''
# NOT $home: that is a read-only PowerShell built-in holding the user's home
# directory, and assigning to it fails while leaking the path into the output.
try { $countryName = (Get-WinHomeLocation).HomeLocation } catch { }
"$tz|$culture|$countryName"
"""

# Asks Windows for actual coordinates. Requires Location Services to be enabled
# and permission granted; both are user choices, and a denial is a normal outcome
# rather than an error.
_GPS_SCRIPT = r"""
try {
  Add-Type -AssemblyName System.Device
  $watcher = New-Object System.Device.Location.GeoCoordinateWatcher
  $watcher.Start()
  $waited = 0
  while (($watcher.Status -ne 'Ready') -and ($waited -lt 40)) {
    Start-Sleep -Milliseconds 250
    $waited++
  }
  if ($watcher.Permission -eq 'Denied') { 'DENIED' }
  elseif ($watcher.Position.Location.IsUnknown) { 'UNKNOWN' }
  else {
    $loc = $watcher.Position.Location
    "$($loc.Latitude),$($loc.Longitude)"
  }
  $watcher.Stop()
} catch { 'UNAVAILABLE' }
"""


def _read_os_region() -> UserLocation:
    """Country and timezone from Windows regional settings."""
    clock = local_time_context()
    raw = _run_powershell(_REGION_SCRIPT, _QUICK_POWERSHELL_TIMEOUT)
    if not raw:
        return UserLocation(timezone=clock["timezone"], source="unavailable")

    parts = raw.split("|")
    country = parts[2].strip() if len(parts) > 2 and parts[2].strip() else None
    if not country and len(parts) > 1 and "-" in parts[1]:
        # Fall back to the culture's region subtag: "en-IN" -> "IN".
        country = parts[1].strip().split("-")[-1] or None

    return UserLocation(
        country=country,
        timezone=(parts[0].strip() if parts else None) or clock["timezone"],
        source="os_region" if country else "unavailable",
    )


def _read_os_coordinates() -> tuple[float, float] | None:
    """Coordinates from the Windows Location Service, if the user allows it."""
    raw = _run_powershell(_GPS_SCRIPT, _POWERSHELL_TIMEOUT)
    if not raw or raw in {"DENIED", "UNKNOWN", "UNAVAILABLE"}:
        if raw:
            logger.info("Windows location service returned %s", raw)
        return None
    try:
        latitude_text, longitude_text = raw.split(",", 1)
        return float(latitude_text), float(longitude_text)
    except ValueError:
        return None


def set_manual_location(
    city: str,
    region: str | None = None,
    country: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
) -> UserLocation:
    """Override the detected location. Wins over everything else.

    Timezone still comes from the OS: someone correcting a wrong city is not also
    telling us their clock is wrong.
    """
    global _manual_location
    _manual_location = UserLocation(
        city=city.strip() or None,
        region=region,
        country=country,
        latitude=latitude,
        longitude=longitude,
        timezone=local_time_context()["timezone"],
        source="manual",
    )
    logger.info("location set manually to %s", _manual_location.label)
    return _manual_location


def clear_manual_location() -> None:
    global _manual_location
    _manual_location = None


async def get_location(
    force_refresh: bool = False, allow_precise: bool = False
) -> UserLocation:
    """The user's location, from their own machine.

    allow_precise gates the Windows Location Service specifically. It triggers an
    OS permission prompt and returns exact coordinates, so it is only attempted
    when the user has asked for precise location - never as a side effect of
    asking a question.

    Never raises: an undetectable location returns source="unavailable", which
    callers treat as "answer without a place".
    """
    global _cached_location

    if _manual_location is not None:
        return _manual_location
    if _cached_location is not None and not force_refresh:
        # A cached coarse result is upgraded if precise detection is now allowed.
        if not (allow_precise and not _cached_location.has_coordinates):
            return _cached_location

    async with _lookup_lock:
        if _manual_location is not None:
            return _manual_location

        # Regional settings first: instant, permissionless, always present.
        result = await asyncio.to_thread(_read_os_region)

        if allow_precise:
            coordinates = await asyncio.to_thread(_read_os_coordinates)
            if coordinates:
                result.latitude, result.longitude = coordinates
                result.source = "os_gps"

        _cached_location = result
        logger.info(
            "location resolved to %s (%s)", result.label, result.source
        )
        return result


def cached_location() -> UserLocation | None:
    """Whatever is already known, without triggering any probe."""
    return _manual_location or _cached_location


# --------------------------------------------------------------------------- #
# Prompt fragment
# --------------------------------------------------------------------------- #


def build_context(location: UserLocation | None = None) -> str:
    """A short system note giving the model today's date and the user's place.

    Always injected, search or no search. Its most important job is anchoring the
    model in the present: told the real date, a model stops describing a
    current-year event as something that "has not happened yet".
    """
    clock = local_time_context()
    lines = [
        "CURRENT CONTEXT",
        f"Today is {clock['date']}. The local time is {clock['time']} "
        f"({clock['timezone']}, {clock['offset']}).",
        f"The current year is {clock['year']}. Your training data ends earlier than "
        "this, so treat this date as the present and never describe something dated "
        "before it as being in the future.",
    ]

    if location and location.source != "unavailable" and (
        location.city or location.country
    ):
        origin = {
            "manual": "they told us this",
            "os_gps": "from this device's location service",
            "os_region": "from this device's regional settings, so it is approximate",
        }.get(location.source, "from this device")
        lines.append(
            f"The user is in {location.label} ({origin}). When they say 'here', "
            "'my area', or ask about local conditions without naming a place, they "
            f"mean {location.label}."
        )

    return "\n".join(lines)
