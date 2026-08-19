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

# matplotlib ships fonts and its matplotlibrc as data, not code; without these
# it raises at import. pandas needs its submodules collected because much of it
# is imported lazily by string name, which the dependency graph cannot see.
datas = collect_data_files("matplotlib")
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
    console=True,
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
