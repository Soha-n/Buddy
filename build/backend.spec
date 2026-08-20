# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Buddy's backend sidecar.

Deliberately --onedir, not --onefile. Two reasons, both load-bearing:

1. ``code_runner`` executes chart scripts in a separate interpreter. Under
   --onefile ``sys.executable`` is the bootstrap stub, so spawning it re-runs
   the entire application - a fork bomb rather than a chart. --onedir keeps a
   real interpreter on disk to point at.
2. --onefile unpacks ~250MB to a temp directory on every launch, adding seconds
   to each start. --onedir pays that cost once, at install time.

Builds two executables that share one ``_internal`` directory:

``buddy-backend``  the API server, windowed (no console flash)
``buddy-runner``   runs a single script and exits, for chart sandboxing

They need *separate* analyses despite sharing dependencies. Building both from
the server's entry script makes the runner a second copy of the server, so
spawning it starts another API server instead of running the chart - which
presents as the chart request hanging until it times out. MERGE ties the two
analyses together so the heavy libraries are collected once.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

BACKEND = Path(SPECPATH).parent / "backend"
FRONTEND_DIST = Path(SPECPATH).parent / "frontend" / "dist"
SEARXNG_BUNDLE = Path(SPECPATH).parent / "build" / "payload" / "searxng"
ICON = Path(SPECPATH).parent / "desktop" / "src-tauri" / "icons" / "icon.ico"
VERSION_INFO = Path(SPECPATH) / "version_info.txt"

# Blank Properties fields read as unidentified software, and both SmartScreen
# and antivirus heuristics weigh missing metadata.
_icon = str(ICON) if ICON.exists() else None
_version = str(VERSION_INFO) if VERSION_INFO.exists() else None

# matplotlib ships fonts and its matplotlibrc as data, not code; without these
# it raises at import. pandas needs its submodules collected because much of it
# is imported lazily by string name, which the dependency graph cannot see.
datas = collect_data_files("matplotlib")

# The app's own data files. PyInstaller collects .py modules but not data
# sitting beside them, so catalog.json - the curated model list every scoring
# request reads - has to be named explicitly. Without it /api/tiers raises
# FileNotFoundError and the UI reports "Could not score models".
for _data_file in (BACKEND / "app").rglob("*.json"):
    _rel = _data_file.parent.relative_to(BACKEND)
    datas.append((str(_data_file), str(_rel)))
hiddenimports = (
    collect_submodules("pandas")
    + collect_submodules("uvicorn")
    + [
        # uvicorn resolves these from config strings at runtime.
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan.on",
        # Imported by name through the router registry.
        "app.routers.attachments",
        "app.routers.capabilities",
        "app.routers.chat",
        "app.routers.conversations",
        "app.routers.models",
        "app.routers.system",
    ]
)

# The built UI ships inside the bundle and is served by the backend itself, so
# the packaged app is same-origin and needs no CORS.
if FRONTEND_DIST.is_dir():
    datas.append((str(FRONTEND_DIST), "web"))
else:
    raise SystemExit(
        f"frontend not built: {FRONTEND_DIST} is missing. "
        "Run 'npm run build' in frontend/ first."
    )

# Optional: present only when build-searxng.ps1 has produced the payload.
# Without it the app still runs and falls back to its other search paths.
if SEARXNG_BUNDLE.is_dir():
    datas.append((str(SEARXNG_BUNDLE), "searxng"))

# Trimming what the API server never touches. Tkinter alone is ~10MB and is
# only pulled in because matplotlib can target it; the Agg backend is forced in
# code_runner, so it is dead weight.
excludes = [
    "tkinter",
    "PyQt5",
    "PyQt6",
    "PySide2",
    "PySide6",
    "wx",
    "IPython",
    "jupyter",
    "notebook",
    "pytest",
    "sphinx",
    "setuptools",
    "pip",
]

def _fix_native_libs(binaries):
    """Keep native libraries belonging to the interpreter we froze with.

    Two ways a foreign copy gets in, both fatal and both silent:

    **Wrong pythonXY.dll.** The build machine may have several Pythons - CI
    installs 3.12 for SearXNG beside Buddy's 3.14 - and an extra runtime DLL
    shadows the right one at load time.

    **Wrong OpenSSL.** More subtle, and what actually broke v1.0.0-beta.4 and
    -beta.6. The SearXNG payload under build/payload/ carries its own
    libssl-3.dll and libcrypto-3.dll from *its* Python 3.11. PyInstaller
    scanned that directory and collected those instead of 3.14's, so _ssl.pyd
    was paired with an OpenSSL it was not built against and raised
    "DLL load failed while importing _ssl: The specified procedure could not be
    found".

    Neither failure is caught by anything else. run_server publishes its port
    before uvicorn starts, so the shell sees a healthy handshake and opens a
    window onto a backend that died during import.

    Both are fixed the same way: for these libraries, take the copy that lives
    with the running interpreter and discard any other.

    **Only at the bundle root.** Both rules apply to the top level of
    ``_internal`` and nowhere else, because only the top level is on the DLL
    search path for our own ``_ssl.pyd``. The SearXNG payload is a *different*
    interpreter - Python's embeddable 3.11, since SearXNG does not support 3.14
    - living in its own subdirectory, and it needs its own ``python311.dll``
    and its own matching OpenSSL beside it.

    Filtering it too is what broke v1.0.0-beta.8. PyInstaller reclassifies
    ``.dll`` files out of ``datas`` into ``binaries``, so the payload's runtime
    reached this function, matched "foreign python DLL" and was dropped -
    leaving an embeddable ``python.exe`` with no runtime. Launching SearXNG then
    failed with "The code execution cannot proceed because python311.dll was not
    found", from a dialog titled ``python.exe`` rather than Buddy, while Buddy
    itself ran fine. Repointing the payload's OpenSSL is the same mistake one
    layer down: it would pair 3.11's ``_ssl.pyd`` with 3.14's libssl.
    """
    import sys

    keep_runtime = f"python{sys.version_info.major}{sys.version_info.minor}.dll"

    # Libraries that must match the interpreter exactly. Resolved from the base
    # prefix rather than trusted from wherever PyInstaller found them.
    base = Path(sys.base_prefix)
    pinned = {}
    for name in ("libssl-3.dll", "libcrypto-3.dll"):
        for candidate in (base / "DLLs" / name, base / name):
            if candidate.exists():
                pinned[name] = str(candidate)
                break

    cleaned = []
    for entry in binaries:
        dest, source = entry[0], entry[1]
        name = Path(dest).name.lower()
        # A nested destination belongs to a bundled payload, not to our own
        # interpreter, so neither rule below may touch it.
        at_bundle_root = Path(dest).parent == Path(".")

        if not at_bundle_root:
            cleaned.append(entry)
            continue

        if (
            name.startswith("python")
            and name.endswith(".dll")
            and name not in (keep_runtime, "python3.dll")
        ):
            print(f"backend.spec: dropping foreign runtime {name}")
            continue

        if name in pinned and Path(source).resolve() != Path(pinned[name]).resolve():
            print(f"backend.spec: repointing {name} at the interpreter's own copy")
            cleaned.append((dest, pinned[name], *entry[2:]))
            continue

        cleaned.append(entry)
    return cleaned


analysis = Analysis(
    [str(BACKEND / "run_server.py")],
    pathex=[str(BACKEND)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

analysis.binaries = _fix_native_libs(analysis.binaries)

# The runner's own entry point. Without this it would be a duplicate of the
# server (see the module docstring).
runner_analysis = Analysis(
    [str(BACKEND / "run_script.py")],
    pathex=[str(BACKEND)],
    binaries=[],
    datas=[],
    hiddenimports=collect_submodules("pandas") + ["matplotlib", "numpy"],
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

runner_analysis.binaries = _fix_native_libs(runner_analysis.binaries)

# Collect shared dependencies once. The first entry owns the files; the second
# references them, which is what lets both executables use one _internal dir.
MERGE(
    (analysis, "buddy-backend", "buddy-backend"),
    (runner_analysis, "buddy-runner", "buddy-runner"),
)

pyz = PYZ(analysis.pure)
runner_pyz = PYZ(runner_analysis.pure)

backend_exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="buddy-backend",
    debug=False,
    strip=False,
    upx=False,
    # Windowed: the shell owns the UI, and a console window appearing behind it
    # looks broken. Startup errors go to the crash log in run_server.py.
    console=False,
    icon=_icon,
    version=_version,
)

# A minimal interpreter for code_runner to spawn. Console-mode because its
# stdout/stderr are captured through a pipe, never shown.
runner_exe = EXE(
    runner_pyz,
    runner_analysis.scripts,
    [],
    exclude_binaries=True,
    name="buddy-runner",
    debug=False,
    strip=False,
    upx=False,
    # Console mode: its stdout and stderr are captured through a pipe, never
    # shown, and CREATE_NO_WINDOW keeps the window itself hidden.
    console=True,
    icon=_icon,
    version=_version,
)

COLLECT(
    backend_exe,
    runner_exe,
    analysis.binaries,
    analysis.datas,
    runner_analysis.binaries,
    runner_analysis.datas,
    strip=False,
    upx=False,
    name="buddy-backend",
)
