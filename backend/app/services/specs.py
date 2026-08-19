"""System specification detection.

psutil is the primary source for CPU/RAM/disk. Every reading has a stdlib
fallback so a missing or broken psutil degrades the report rather than failing
the request outright.
"""

from __future__ import annotations

import ctypes
import logging
import os
import platform
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.models.schemas import (
    CpuInfo,
    DiskInfo,
    OsInfo,
    RamInfo,
    SystemSpecs,
)
from app.services.gpu import detect_gpus

logger = logging.getLogger(__name__)

_BYTES_PER_GB = 1024**3
_TIMEOUT = 8
_CREATE_NO_WINDOW = 0x08000000

# Below this, even the smallest useful model is a struggle.
_LOW_RAM_THRESHOLD_GB = 8.0

try:
    import psutil
except ImportError:  # pragma: no cover - defensive; psutil is in requirements
    psutil = None  # type: ignore[assignment]
    logger.warning("psutil unavailable; using stdlib fallbacks for CPU/RAM")

_cache: SystemSpecs | None = None


def _cpu_name_from_cim() -> str | None:
    """Get a human-readable CPU name.

    platform.processor() returns something like "AMD64 Family 25 Model 80
    Stepping 0" on Windows, which is useless to show a user, so ask CIM for the
    marketing name instead.
    """
    script = "(Get-CimInstance Win32_Processor | Select-Object -First 1).Name"
    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            creationflags=_CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("CIM CPU name lookup failed: %s", exc)
        return None
    name = result.stdout.strip()
    return name or None


def _total_ram_gb_via_ctypes() -> float | None:
    """Read total RAM through GlobalMemoryStatusEx, no third-party deps."""

    class MemoryStatusEx(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    try:
        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(MemoryStatusEx)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        return round(status.ullTotalPhys / _BYTES_PER_GB, 2)
    except (AttributeError, OSError) as exc:
        logger.debug("GlobalMemoryStatusEx failed: %s", exc)
        return None


def _detect_cpu() -> CpuInfo:
    name = _cpu_name_from_cim() or platform.processor() or "Unknown CPU"

    physical: int | None = None
    logical: int | None = None
    max_clock: int | None = None

    if psutil is not None:
        try:
            physical = psutil.cpu_count(logical=False)
            logical = psutil.cpu_count(logical=True)
            freq = psutil.cpu_freq()
            if freq and freq.max:
                max_clock = int(freq.max)
        except Exception as exc:  # psutil raises assorted OS errors
            logger.debug("psutil CPU query failed: %s", exc)

    if logical is None:
        logical = os.cpu_count()

    return CpuInfo(
        name=name,
        physical_cores=physical,
        logical_cores=logical,
        max_clock_mhz=max_clock,
        architecture=platform.machine() or "unknown",
    )


def _detect_ram() -> RamInfo:
    if psutil is not None:
        try:
            mem = psutil.virtual_memory()
            return RamInfo(
                total_gb=round(mem.total / _BYTES_PER_GB, 2),
                available_gb=round(mem.available / _BYTES_PER_GB, 2),
            )
        except Exception as exc:
            logger.debug("psutil memory query failed: %s", exc)

    total = _total_ram_gb_via_ctypes()
    return RamInfo(total_gb=total or 0.0, available_gb=None)


def _models_dir() -> Path:
    """Where Ollama stores model blobs.

    Disk space is measured here rather than on C:, since this may sit on a
    different volume and it is the volume the download actually consumes.
    """
    override = settings.ollama_models or os.environ.get("OLLAMA_MODELS")
    if override:
        return Path(override)
    return Path.home() / ".ollama" / "models"


def _detect_disk() -> DiskInfo:
    target = _models_dir()

    # The models dir may not exist yet on a fresh Ollama install; walk up to the
    # nearest existing ancestor so disk_usage has something real to stat.
    probe = target
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent

    try:
        usage = shutil.disk_usage(probe)
        return DiskInfo(
            path=str(target),
            total_gb=round(usage.total / _BYTES_PER_GB, 2),
            free_gb=round(usage.free / _BYTES_PER_GB, 2),
        )
    except OSError as exc:
        logger.warning("disk usage check failed for %s: %s", probe, exc)
        return DiskInfo(path=str(target), total_gb=0.0, free_gb=0.0)


def _detect_os() -> OsInfo:
    return OsInfo(
        system=platform.system() or "unknown",
        release=platform.release() or "unknown",
        version=platform.version() or "unknown",
        machine=platform.machine() or "unknown",
    )


def _build_warnings(
    ram: RamInfo, disk: DiskInfo, gpus: list, cpu: CpuInfo
) -> list[str]:
    warnings: list[str] = []

    if ram.total_gb and ram.total_gb < _LOW_RAM_THRESHOLD_GB:
        warnings.append(
            f"Only {ram.total_gb} GB of RAM detected. Expect slow responses and "
            "stick to the smallest models."
        )
    if not ram.total_gb:
        warnings.append("Could not determine total RAM; recommendations may be off.")

    if disk.free_gb and disk.free_gb < 10:
        warnings.append(
            f"Just {disk.free_gb} GB free on {disk.path}. Larger models may not fit."
        )

    if not gpus:
        warnings.append(
            "No GPU detected. Models will run on CPU only, which is noticeably slower."
        )
    else:
        unreliable = [g.name for g in gpus if g.vram_gb and not g.vram_reliable]
        if unreliable:
            warnings.append(
                "VRAM for "
                + ", ".join(unreliable)
                + " came from a Windows field that caps at 4 GB, so the real amount "
                "may be higher. GPU scoring ignores these values."
            )
        if all(g.vram_gb is None for g in gpus):
            warnings.append(
                "GPU found but VRAM could not be read; scoring assumes CPU-only."
            )

    if cpu.physical_cores and cpu.physical_cores <= 2:
        warnings.append(
            f"Only {cpu.physical_cores} physical CPU cores; CPU inference will be slow."
        )

    return warnings


def detect_specs(refresh: bool = False) -> SystemSpecs:
    """Detect full system specs, cached in-process until refresh=True."""
    global _cache
    if _cache is not None and not refresh:
        return _cache

    cpu = _detect_cpu()
    ram = _detect_ram()
    disk = _detect_disk()
    gpus = detect_gpus()
    os_info = _detect_os()

    _cache = SystemSpecs(
        cpu=cpu,
        ram=ram,
        gpus=gpus,
        disk=disk,
        os=os_info,
        warnings=_build_warnings(ram, disk, gpus, cpu),
        detected_at=datetime.now(timezone.utc).isoformat(),
    )
    return _cache
