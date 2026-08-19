"""GPU and VRAM detection on Windows.

VRAM is genuinely awkward to read on Windows, so this tries three sources in
descending order of trustworthiness:

1. ``nvidia-smi``  - exact, but NVIDIA only.
2. Registry ``HardwareInformation.qwMemorySize`` - a real 64-bit value, works
   for any vendor whose driver populates it.
3. ``Win32_VideoController`` via CIM - reliable for adapter *names*, but its
   ``AdapterRAM`` field is a signed 32-bit int that saturates at 4 GB, so a
   4 GB reading from an 8 GB card is indistinguishable from a real 4 GB card.
   Values from this source are flagged ``vram_reliable=False``.
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

from app.models.schemas import GpuInfo

logger = logging.getLogger(__name__)

_BYTES_PER_GB = 1024**3
_TIMEOUT = 8

# Keeps a console window from flashing when the app is later wrapped in Tauri.
_CREATE_NO_WINDOW = 0x08000000

# The Windows "Display adapters" device class.
_DISPLAY_CLASS_GUID = "{4d36e968-e325-11ce-bfc1-08002be10318}"

# AdapterRAM saturates here; at or above this the value tells us nothing.
_ADAPTER_RAM_CAP_GB = 4.0


def _run(cmd: list[str]) -> str | None:
    """Run a command, returning stdout or None on any failure."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            creationflags=_CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("command %s failed: %s", cmd[0], exc)
        return None
    if result.returncode != 0:
        logger.debug("command %s exited %s", cmd[0], result.returncode)
        return None
    return result.stdout


def _vendor_from_name(name: str) -> str:
    lowered = name.lower()
    if "nvidia" in lowered or "geforce" in lowered or "quadro" in lowered:
        return "NVIDIA"
    if "amd" in lowered or "radeon" in lowered:
        return "AMD"
    if "intel" in lowered or "arc" in lowered:
        return "Intel"
    return "Unknown"


def _find_nvidia_smi() -> str:
    """Locate nvidia-smi.exe, which lives in System32 rather than on PATH."""
    candidate = Path(r"C:\Windows\System32\nvidia-smi.exe")
    if candidate.exists():
        return str(candidate)
    return "nvidia-smi"  # fall back to a PATH lookup


def _detect_nvidia() -> list[GpuInfo]:
    """Query NVIDIA GPUs. Authoritative when it works."""
    output = _run(
        [
            _find_nvidia_smi(),
            "--query-gpu=name,memory.total,memory.free,driver_version",
            "--format=csv,noheader,nounits",
        ]
    )
    if not output:
        return []

    gpus: list[GpuInfo] = []
    for line in output.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4:
            continue
        name, total_mib, free_mib, driver = parts[0], parts[1], parts[2], parts[3]
        try:
            total_gb = round(float(total_mib) / 1024, 2)
            free_gb = round(float(free_mib) / 1024, 2)
        except ValueError:
            continue
        gpus.append(
            GpuInfo(
                name=name,
                vram_gb=total_gb,
                vram_free_gb=free_gb,
                driver_version=driver,
                vendor="NVIDIA",
                source="nvidia-smi",
                vram_reliable=True,
            )
        )
    return gpus


def _detect_registry_vram() -> dict[str, float]:
    """Map adapter name -> VRAM GB from the driver registry keys.

    Some subkeys raise PermissionError even for an admin process, so each one is
    isolated; a single inaccessible adapter must not abort the whole scan.
    """
    try:
        import winreg
    except ImportError:  # non-Windows
        return {}

    found: dict[str, float] = {}
    base_path = rf"SYSTEM\CurrentControlSet\Control\Class\{_DISPLAY_CLASS_GUID}"

    try:
        base = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base_path)
    except OSError as exc:
        logger.debug("cannot open display class key: %s", exc)
        return {}

    with base:
        index = 0
        while True:
            try:
                subkey_name = winreg.EnumKey(base, index)
            except OSError:
                break  # no more subkeys
            index += 1

            # Only numbered device instances (0000, 0001, ...) hold adapter info.
            if not subkey_name.isdigit():
                continue

            try:
                with winreg.OpenKey(base, subkey_name) as subkey:
                    size_bytes, _ = winreg.QueryValueEx(
                        subkey, "HardwareInformation.qwMemorySize"
                    )
                    try:
                        name, _ = winreg.QueryValueEx(subkey, "DriverDesc")
                    except OSError:
                        name = f"Adapter {subkey_name}"
                    if isinstance(size_bytes, int) and size_bytes > 0:
                        found[str(name)] = round(size_bytes / _BYTES_PER_GB, 2)
            except (OSError, PermissionError) as exc:
                logger.debug("skipping registry subkey %s: %s", subkey_name, exc)
                continue

    return found


def _detect_cim_adapters() -> list[tuple[str, float | None]]:
    """List adapter names via CIM, plus their (untrustworthy) AdapterRAM."""
    script = (
        "Get-CimInstance Win32_VideoController | ForEach-Object { "
        "'{0}|{1}|{2}' -f $_.Name, $_.AdapterRAM, $_.DriverVersion }"
    )
    output = _run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ]
    )
    if not output:
        return []

    adapters: list[tuple[str, float | None]] = []
    for line in output.strip().splitlines():
        fields = line.split("|")
        if not fields or not fields[0].strip():
            continue
        name = fields[0].strip()
        ram_gb: float | None = None
        if len(fields) > 1 and fields[1].strip().isdigit():
            ram_gb = round(int(fields[1]) / _BYTES_PER_GB, 2)
        adapters.append((name, ram_gb))
    return adapters


def _normalize(name: str) -> str:
    """Loose key for matching adapter names across data sources."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def detect_gpus() -> list[GpuInfo]:
    """Detect all display adapters, merging the three sources by name."""
    nvidia = _detect_nvidia()
    nvidia_keys = {_normalize(g.name) for g in nvidia}

    registry_by_key = {
        _normalize(name): vram for name, vram in _detect_registry_vram().items()
    }

    gpus = list(nvidia)

    for name, adapter_ram_gb in _detect_cim_adapters():
        key = _normalize(name)

        # Already covered by the authoritative nvidia-smi result.
        if any(key in nk or nk in key for nk in nvidia_keys):
            continue

        vram_gb: float | None = None
        source = "unknown"
        reliable = True

        matched_key = key if key in registry_by_key else None
        if matched_key is None:
            matched_key = next(
                (rk for rk in registry_by_key if rk in key or key in rk), None
            )

        if matched_key is not None:
            vram_gb = registry_by_key[matched_key]
            source = "registry"
        elif adapter_ram_gb is not None:
            vram_gb = adapter_ram_gb
            source = "cim"
            # At the 4 GB cap the true size is unknowable from this field.
            reliable = adapter_ram_gb < _ADAPTER_RAM_CAP_GB

        gpus.append(
            GpuInfo(
                name=name,
                vram_gb=vram_gb,
                vram_free_gb=None,
                driver_version=None,
                vendor=_vendor_from_name(name),
                source=source,
                vram_reliable=reliable,
            )
        )

    return gpus
