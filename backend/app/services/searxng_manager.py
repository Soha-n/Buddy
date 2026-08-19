"""Install, start and supervise a local SearXNG instance.

SearXNG is the only search backend that is simultaneously free, unlimited,
private and safe to ship in a commercial product - so Buddy runs its own rather
than asking the user to set one up. This module makes that automatic:

1. On first run, clone the source and install it into a dedicated virtualenv.
2. On every start, launch it as a child process bound to loopback.
3. Keep it supervised, and stop it when Buddy exits.

Three Windows-specific problems had to be solved, none of them obvious:

**No Docker.** The official distribution is source + Docker only, with no binary
release. So this installs from source into its own venv, which also keeps
SearXNG's pinned dependencies away from Buddy's.

**Python version.** SearXNG requires <= 3.12 while Buddy runs on 3.14, so the venv
is built with a separate interpreter located through the `py` launcher.

**`import pwd`.** searx/valkeydb.py imports the Unix-only `pwd` module at import
time, which is an immediate ModuleNotFoundError on Windows. A small shim is
written into SearXNG's venv; it is only ever consulted to build a Valkey socket
path, and Buddy runs with Valkey disabled.

Licence note: SearXNG is AGPL-3.0. It runs as a *separate process* reached over
HTTP - never imported or linked - so its licence stays confined to that process.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.config import settings
from app.paths import bundled_searxng_dir, data_root

logger = logging.getLogger(__name__)

SEARXNG_REPO = "https://github.com/searxng/searxng.git"

# SearXNG supports 3.10-3.12; Buddy itself runs on newer. Newest-first.
_SUPPORTED_PYTHONS = ("3.12", "3.11", "3.10")

# A cold first start compiles every engine definition; on Windows this has been
# observed to take over two minutes, so the budget is generous. Search is served
# by the fallback throughout, so a long wait costs the user nothing.
_START_TIMEOUT_S = 240.0
_PROBE_TIMEOUT = httpx.Timeout(3.0)

# Only ever bound to loopback: this is the user's own search instance, not a
# service for the rest of their network to reach.
_BIND_ADDRESS = "127.0.0.1"

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

#: pyvenv.cfg is newline-delimited regardless of platform.
NEWLINE = "\n"


@dataclass
class ManagerState:
    installed: bool = False
    running: bool = False
    installing: bool = False
    starting: bool = False
    #: Last failure, surfaced so the UI can explain a fallback rather than hide it.
    error: str | None = None
    url: str = ""


_state = ManagerState()
_process: subprocess.Popen | None = None
_install_lock = asyncio.Lock()
_start_lock = asyncio.Lock()


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #


def _root() -> Path:
    """Where SearXNG lives: inside Buddy's writable data directory.

    Not the install directory - SearXNG writes runtime state, and an update
    replaces the install wholesale.
    """
    if settings.searxng_install_dir:
        return Path(settings.searxng_install_dir).expanduser()
    return data_root() / "searxng"


def _source_dir() -> Path:
    return _root() / "src"


def _venv_python() -> Path:
    return _root() / "venv" / "Scripts" / "python.exe"


def _settings_file() -> Path:
    return _root() / "settings.yml"


def local_url() -> str:
    return f"http://{_BIND_ADDRESS}:{settings.searxng_port}"


# --------------------------------------------------------------------------- #
# Interpreter discovery
# --------------------------------------------------------------------------- #


def _find_supported_python() -> str | None:
    """Locate a 3.10-3.12 interpreter for SearXNG's venv.

    Buddy's own interpreter is tried last: it usually fails SearXNG's version
    ceiling, but where it happens to qualify there is no reason to require a
    second install.
    """
    has_launcher = shutil.which("py") is not None
    for version in _SUPPORTED_PYTHONS:
        # The py launcher is the reliable way to reach a specific version on
        # Windows; a bare "python3.11" is rarely on PATH there.
        if has_launcher:
            try:
                probe = subprocess.run(
                    ["py", f"-{version}", "-c", "print(1)"],
                    capture_output=True,
                    text=True,
                    timeout=20,
                    creationflags=_NO_WINDOW,
                )
                if probe.returncode == 0:
                    return f"py -{version}"
            except (subprocess.SubprocessError, OSError):
                pass
        exact = shutil.which(f"python{version}")
        if exact:
            return exact

    if (3, 10) <= sys.version_info[:2] <= (3, 12):
        return sys.executable
    return None


def _python_command(spec: str) -> list[str]:
    """Turn a discovered spec ("py -3.11" or a path) into an argv prefix."""
    return spec.split() if spec.startswith("py ") else [spec]


# --------------------------------------------------------------------------- #
# Install
# --------------------------------------------------------------------------- #

# Minimal config. Valkey/Redis is deliberately absent (Buddy needs no
# cross-process rate limiting) and the limiter is off because the only client is
# Buddy itself on this same machine.
_SETTINGS_TEMPLATE = """# Generated by Buddy. Edit freely; it is never overwritten once it exists.
use_default_settings: true

general:
  debug: false
  instance_name: "Buddy Search"
  donation_url: false
  contact_url: false

server:
  secret_key: "{secret}"
  bind_address: "{bind}"
  port: {port}
  limiter: false
  public_instance: false
  image_proxy: false

search:
  safe_search: 0
  autocomplete: ""
  formats:
    - html
    - json

outgoing:
  request_timeout: 6.0
  max_request_timeout: 12.0
  pool_connections: 20
  pool_maxsize: 10
"""

# Written into SearXNG's venv as pwd.py. Built by joining lines so this file does
# not need to nest a triple-quoted string inside a triple-quoted string.
_PWD_SHIM = "\n".join(
    [
        '"""Windows stand-in for the Unix `pwd` module, installed by Buddy.',
        "",
        "searx/valkeydb.py imports pwd at module scope to build a default Valkey",
        "socket path. Buddy runs SearXNG with Valkey disabled so that path is never",
        'used, but the import happens before that is known - hence this shim."""',
        "",
        "import os",
        "from collections import namedtuple",
        "",
        "struct_passwd = namedtuple(",
        '    "struct_passwd",',
        '    "pw_name pw_passwd pw_uid pw_gid pw_gecos pw_dir pw_shell",',
        ")",
        "",
        "",
        "def _entry():",
        '    name = os.environ.get("USERNAME") or "buddy"',
        '    return struct_passwd(name, "x", 0, 0, name, os.path.expanduser("~"), "")',
        "",
        "",
        "def getpwuid(_uid):",
        "    return _entry()",
        "",
        "",
        "def getpwnam(_name):",
        "    return _entry()",
        "",
        "",
        "def getpwall():",
        "    return [_entry()]",
        "",
    ]
)


def is_installed() -> bool:
    return _venv_python().exists() and (_source_dir() / "searx" / "webapp.py").exists()


def _adopt_bundle() -> bool:
    """Move a prebuilt SearXNG shipped with the installer into place.

    The from-source path below needs git and a Python 3.10-3.12 interpreter.
    Neither exists on a typical end-user machine, so the packaged build ships
    SearXNG prebuilt and this copies it into the writable data directory on
    first run.

    Copied rather than used in place because SearXNG writes into its own
    directory, and the install directory is both read-only in an all-users
    install and replaced by the next update.
    """
    bundle = bundled_searxng_dir()
    if not (bundle / "src" / "searx" / "webapp.py").exists():
        return False

    root = _root()
    root.mkdir(parents=True, exist_ok=True)
    logger.info("adopting bundled SearXNG from %s", bundle)

    for name in ("src", "venv"):
        source = bundle / name
        target = root / name
        if not source.exists() or target.exists():
            continue
        try:
            shutil.copytree(source, target)
        except OSError as exc:
            logger.warning("copying bundled SearXNG %s failed: %s", name, exc)
            return False

    # A venv records absolute paths from build time; on a user's machine those
    # point at the build agent. Rewriting pyvenv.cfg keeps the interpreter
    # resolvable after the move.
    _repair_venv_paths(root / "venv")
    return is_installed()


def _repair_venv_paths(venv: Path) -> None:
    """Rewrite a relocated venv's recorded home so its interpreter still runs."""
    cfg = venv / "pyvenv.cfg"
    if not cfg.exists():
        return
    try:
        lines = cfg.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    scripts = venv / "Scripts"
    rewritten = []
    for line in lines:
        if line.lower().startswith("home ="):
            rewritten.append(f"home = {scripts}")
        elif line.lower().startswith("executable ="):
            rewritten.append(f"executable = {scripts / 'python.exe'}")
        else:
            rewritten.append(line)
    try:
        cfg.write_text(NEWLINE.join(rewritten) + NEWLINE, encoding="utf-8")
    except OSError as exc:
        logger.warning("repairing venv paths failed: %s", exc)


def _run(
    argv: list[str], cwd: Path | None = None, timeout: float = 900.0
) -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=_NO_WINDOW,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        return False, str(exc)
    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout or "").strip().splitlines()
        return False, "\n".join(tail[-6:]) if tail else f"exit {completed.returncode}"
    return True, ""


def _install_blocking() -> tuple[bool, str]:
    """Clone and install SearXNG. Returns (ok, error). Safe to re-run."""
    root = _root()
    root.mkdir(parents=True, exist_ok=True)

    if not shutil.which("git"):
        return False, "Git is not installed, so SearXNG cannot be downloaded."

    python_spec = _find_supported_python()
    if not python_spec:
        return False, (
            "SearXNG needs Python 3.10-3.12 and none was found. Install Python 3.12 "
            "from python.org and restart Buddy."
        )

    source = _source_dir()
    if not (source / "searx" / "webapp.py").exists():
        if source.exists():
            shutil.rmtree(source, ignore_errors=True)
        logger.info("cloning SearXNG into %s", source)
        # Shallow: the history is irrelevant here and costs a few hundred MB.
        #
        # --no-checkout is deliberate. A handful of nginx/uwsgi template files in
        # the repo have ':' in their filenames, which Windows cannot represent, so
        # a normal clone aborts the checkout and leaves an EMPTY working tree even
        # though the objects downloaded fine. Fetching first and checking out
        # separately lets the valid files land and the impossible ones be skipped.
        ok, err = _run(
            ["git", "clone", "--depth", "1", "--no-checkout", SEARXNG_REPO, str(source)],
            timeout=900.0,
        )
        if not ok:
            return False, f"Could not download SearXNG: {err}"

        # Reports failure for the ':' paths while still writing everything else -
        # so its exit code is ignored and success is judged by whether the app is
        # actually on disk.
        _run(["git", "checkout", "HEAD", "--", "."], cwd=source, timeout=600.0)

        if not (source / "searx" / "webapp.py").exists():
            # Last resort: sparse-checkout just the directories we need to run.
            _run(
                ["git", "sparse-checkout", "set", "searx", "requirements.txt"],
                cwd=source,
                timeout=300.0,
            )
            _run(["git", "checkout", "HEAD"], cwd=source, timeout=600.0)

        if not (source / "searx" / "webapp.py").exists():
            return False, (
                "SearXNG downloaded but its files could not be written to disk. "
                "This usually means a path-length or permissions limit."
            )
        logger.info(
            "SearXNG checked out (some non-runtime template files were skipped, "
            "which is expected on Windows)"
        )

    venv_dir = root / "venv"
    if not _venv_python().exists():
        logger.info("creating SearXNG virtualenv with %s", python_spec)
        ok, err = _run(
            _python_command(python_spec) + ["-m", "venv", str(venv_dir)], timeout=300.0
        )
        if not ok or not _venv_python().exists():
            return False, f"Could not create the SearXNG environment: {err}"

    logger.info("installing SearXNG dependencies (first run only)")
    _run(
        [str(_venv_python()), "-m", "pip", "install", "--quiet", "--upgrade", "pip"],
        timeout=300.0,
    )
    ok, err = _run(
        [str(_venv_python()), "-m", "pip", "install", "--quiet", "-r", "requirements.txt"],
        cwd=source,
        timeout=1800.0,
    )
    if not ok:
        return False, f"Could not install SearXNG dependencies: {err}"

    # Windows ships no system zoneinfo database, so several engines fail to load
    # with "No time zone found". The pip package supplies one.
    _run(
        [str(_venv_python()), "-m", "pip", "install", "--quiet", "tzdata"],
        timeout=300.0,
    )

    # The shim goes into site-packages rather than the source tree, so the
    # checkout stays clean and can be updated with a plain git pull.
    shim_target = venv_dir / "Lib" / "site-packages" / "pwd.py"
    if not shim_target.exists():
        shim_target.parent.mkdir(parents=True, exist_ok=True)
        shim_target.write_text(_PWD_SHIM, encoding="utf-8")

    if not _settings_file().exists():
        _settings_file().write_text(
            _SETTINGS_TEMPLATE.format(
                secret=secrets.token_hex(32),
                bind=_BIND_ADDRESS,
                port=settings.searxng_port,
            ),
            encoding="utf-8",
        )

    return True, ""


async def install() -> tuple[bool, str]:
    """Install SearXNG if it is not already present."""
    async with _install_lock:
        if is_installed():
            _state.installed = True
            return True, ""
        # Prefer the shipped payload; cloning is the source-checkout path.
        if _adopt_bundle():
            _state.installed = True
            logger.info("bundled SearXNG adopted")
            return True, ""
        _state.installing = True
        _state.error = None
        try:
            ok, err = await asyncio.to_thread(_install_blocking)
        finally:
            _state.installing = False
        _state.installed = ok
        if not ok:
            _state.error = err
            logger.warning("SearXNG install failed: %s", err)
        return ok, err


# --------------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------------- #


async def is_responding() -> bool:
    """Whether a SearXNG instance answers JSON at the expected port."""
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT) as client:
            response = await client.get(
                f"{local_url()}/search", params={"q": "ping", "format": "json"}
            )
            return response.status_code == 200 and "results" in response.json()
    except Exception:
        return False


def _spawn() -> tuple[bool, str]:
    global _process
    env = dict(os.environ)
    env["SEARXNG_SETTINGS_PATH"] = str(_settings_file())
    # Keeps SearXNG's dependency tree isolated from anything inherited.
    env.pop("PYTHONPATH", None)

    try:
        _process = subprocess.Popen(
            [str(_venv_python()), "-m", "searx.webapp"],
            cwd=str(_source_dir()),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    return True, ""


async def start() -> tuple[bool, str]:
    """Ensure a local SearXNG is running. Idempotent.

    Returns (ok, error). A failure is never fatal - search falls back to other
    providers - so this reports rather than raises.
    """
    global _process

    async with _start_lock:
        if await is_responding():
            # Already up: either a previous Buddy run or one the user started.
            _state.running = True
            _state.url = local_url()
            return True, ""

        if not is_installed():
            ok, err = await install()
            if not ok:
                return False, err

        _state.starting = True
        try:
            ok, err = await asyncio.to_thread(_spawn)
            if not ok:
                _state.error = err
                return False, f"Could not start SearXNG: {err}"

            loop = asyncio.get_running_loop()
            deadline = loop.time() + _START_TIMEOUT_S
            # Polled rather than a fixed sleep: a cold start loads every engine
            # definition and takes anywhere from seconds to most a minute.
            while loop.time() < deadline:
                if _process is not None and _process.poll() is not None:
                    detail = ""
                    if _process.stderr is not None:
                        raw = _process.stderr.read(2000)
                        if isinstance(raw, bytes):
                            detail = raw.decode("utf-8", "replace")
                        else:
                            detail = str(raw)
                    lines = [line for line in detail.strip().splitlines() if line.strip()]
                    _state.error = lines[-1] if lines else "process exited"
                    return False, f"SearXNG exited during startup: {_state.error}"
                if await is_responding():
                    _state.running = True
                    _state.url = local_url()
                    _state.error = None
                    logger.info("SearXNG is serving on %s", local_url())
                    return True, ""
                await asyncio.sleep(1.5)

            _state.error = "SearXNG did not become ready in time."
            return False, _state.error
        finally:
            _state.starting = False


def stop() -> None:
    """Terminate the child process. Called on Buddy shutdown."""
    global _process
    if _process is None:
        return
    if _process.poll() is None:
        _process.terminate()
        try:
            _process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _process.kill()
    _process = None
    _state.running = False


def state() -> ManagerState:
    _state.installed = is_installed()
    _state.url = local_url()
    return _state
