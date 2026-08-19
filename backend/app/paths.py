r"""Filesystem locations, resolved differently for source runs and frozen builds.

Running from a checkout, everything lives under ``backend/`` where it always
has: the database in ``data/``, SearXNG beside it. That keeps ``uvicorn
app.main:app`` behaving exactly as before.

Packaged, those two roots have to split apart:

**Read-only resources** ship inside the install directory. Under PyInstaller
that is ``sys._MEIPASS``, which for a --onedir build is the ``_internal``
folder next to the executable. Bundled payloads (the built frontend, the
prebuilt SearXNG) are read from there.

**Writable state** cannot live there. The install directory is replaced
wholesale by an update, and on a machine where Buddy was installed for all
users it is not writable at all. So the database, logs and any runtime-created
files go to ``%LOCALAPPDATA%\Buddy`` instead, which survives updates and needs
no elevation.

Every path in the app should come from here rather than from ``__file__``,
which points inside the frozen bundle and is wrong for anything writable.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

#: True when running from a PyInstaller build rather than a source checkout.
IS_FROZEN = getattr(sys, "frozen", False)

APP_NAME = "Buddy"


def resource_root() -> Path:
    """Directory holding read-only bundled resources.

    Frozen, this is PyInstaller's extraction root (``_internal`` for --onedir).
    From source it is ``backend/``, so bundled-resource lookups resolve to the
    same relative layout in both modes.
    """
    if IS_FROZEN:
        # _MEIPASS is absent only if someone froze this without PyInstaller;
        # falling back to the executable's own directory is the sane guess.
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def install_root() -> Path:
    """Directory containing the executable itself.

    Distinct from :func:`resource_root` for --onedir builds, where resources sit
    one level down in ``_internal``. Sidecar binaries shipped next to the exe
    are found here.
    """
    if IS_FROZEN:
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def data_root() -> Path:
    r"""Writable per-user directory for state that must survive updates.

    ``BUDDY_DATA_DIR`` overrides it, which is what makes a portable build
    possible: point it at a folder on the USB stick and nothing touches the
    user profile. Otherwise ``%LOCALAPPDATA%\Buddy`` on Windows and
    ``~/.local/share/Buddy`` elsewhere.

    From source the old ``backend/data`` location is kept, so a developer's
    existing database and SearXNG install are still found.
    """
    override = os.environ.get("BUDDY_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()

    if not IS_FROZEN:
        return Path(__file__).resolve().parent.parent / "data"

    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / APP_NAME
        return Path.home() / "AppData" / "Local" / APP_NAME

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME

    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / APP_NAME
    return Path.home() / ".local" / "share" / APP_NAME


def ensure_data_root() -> Path:
    """Create the writable data directory and return it."""
    root = data_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def frontend_dir() -> Path:
    """Built frontend assets, served by the backend in packaged builds.

    Bundled under ``web/``; from source it resolves to ``frontend/dist`` so a
    local ``npm run build`` can be smoke-tested through the same code path.
    """
    if IS_FROZEN:
        return resource_root() / "web"
    return Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


def bundled_searxng_dir() -> Path:
    """Prebuilt SearXNG payload shipped inside the install, if present.

    Empty/missing means the installer did not include it, and the manager falls
    back to installing from source the way a source checkout does.
    """
    return resource_root() / "searxng"


def logs_dir() -> Path:
    """Writable log directory. Created on demand."""
    path = data_root() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path
