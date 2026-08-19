r"""Entry point for the packaged backend.

PyInstaller freezes this rather than uvicorn's CLI, because a packaged app has
two needs a plain ``uvicorn app.main:app`` cannot meet:

**A port that is actually free.** A hardcoded 8000 collides with whatever else
the user runs - Node dev servers and Jupyter both like that port. Binding port 0
lets the OS choose, and the chosen port is then published so the shell can find
it.

**A way to tell the shell where to connect.** The port is written to
``%LOCALAPPDATA%\Buddy\runtime.json`` and echoed on stdout as a
``BUDDY_PORT=<n>`` line. The shell can use whichever is convenient; the file
also lets a second launch detect an instance that is already running.

Run from source it behaves like the old command, defaulting to the configured
port so existing workflows and the Vite proxy keep working.
"""

from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path

import uvicorn

from app.config import settings
from app.paths import ensure_data_root, logs_dir

#: Written next to the database so the shell and any second launch can find it.
RUNTIME_FILE = "runtime.json"


def _free_port() -> int:
    """Ask the OS for an unused loopback port.

    There is an unavoidable race between closing this socket and uvicorn
    binding it, but the window is microseconds and the alternative - passing a
    pre-bound socket through PyInstaller - is far more fragile.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _publish(port: int) -> Path:
    """Record the live port where the shell can read it."""
    path = ensure_data_root() / RUNTIME_FILE
    payload = {"port": port, "pid": os.getpid(), "url": f"http://127.0.0.1:{port}"}
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def main() -> None:
    # Port 0 asks for an ephemeral port; anything else is taken literally so a
    # developer can still pin one.
    port = settings.port or _free_port()
    _publish(port)

    # The shell reads this line from the sidecar's stdout. Flushed explicitly:
    # stdout is a pipe here, so it is block-buffered and would otherwise sit
    # unseen until the buffer filled.
    print(f"BUDDY_PORT={port}", flush=True)

    # Frozen, uvicorn cannot resolve "app.main:app" as an import string - the
    # bundle has no importable module path - so hand it the object directly.
    # Importing here rather than at module scope keeps the port handshake above
    # instant; importing the app pulls in pandas and matplotlib.
    from app.main import app as asgi_app

    uvicorn.run(
        asgi_app,
        host="127.0.0.1",
        port=port,
        log_level=settings.log_level,
        # Reload spawns a child process by re-executing the interpreter, which
        # in a frozen build means launching the whole app again.
        reload=False,
        # One worker: the app keeps state in module-level singletons (the SQLite
        # connection, the SearXNG supervisor) that multiple workers would each
        # duplicate and fight over.
        workers=1,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # A frozen GUI build has no console, so a traceback would vanish. Keep
        # it on disk instead - this file is the first thing to ask a user for.
        import traceback

        crash = logs_dir() / "backend-crash.log"
        crash.write_text(traceback.format_exc(), encoding="utf-8")
        raise
