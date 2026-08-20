"""Run model-written Python to produce a chart, under restrictions.

This executes code an LLM generated, so the threat model is real: prompt
injection hidden inside an uploaded document could try to steer the model into
emitting code that reads files, opens sockets or deletes things. Nothing here is
a security boundary strong enough to run genuinely hostile code - that needs a
container or a VM - so the design leans on four independent limits instead, and
the user still has to click Run before any of it happens:

1. A static import check rejects the script before a process exists. Cheap, and
   catches the obvious cases (os, sys, subprocess, socket, shutil, requests).
2. Execution happens in a *separate* interpreter, so a crash or a segfault in
   matplotlib cannot take the API server down with it.
3. The working directory is a fresh temp dir that is deleted afterwards, and the
   generated chart may only be written inside it.
4. A wall-clock timeout kills runaway loops, and stdout is capped so a print
   loop cannot exhaust memory.

matplotlib is forced onto the non-interactive Agg backend; without it, a plt.show()
call on Windows tries to open a GUI window and hangs the subprocess forever.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.paths import IS_FROZEN, install_root

logger = logging.getLogger(__name__)

# Windows: keep the child from flashing a console window on a GUI build.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0

#: Name of the interpreter shipped beside the executable in a packaged build.
_RUNNER_EXE = "buddy-runner.exe" if sys.platform == "win32" else "buddy-runner"


def _interpreter() -> str | None:
    """Path to the interpreter that executes chart scripts.

    From source this is simply ``sys.executable``. Frozen, it must not be:
    ``sys.executable`` is Buddy itself, so re-launching it would start another
    copy of the whole application instead of running the script - a fork bomb,
    not a chart. PyInstaller's --onefile mode makes this unavoidable, which is
    why the packaged build uses --onedir and ships a small separate interpreter
    (``buddy-runner``) next to the main executable.

    Returns None when frozen and that interpreter is missing, so the caller can
    report a clear error rather than spawning something destructive.
    """
    if not IS_FROZEN:
        return sys.executable

    # bin/ first: the installed layout keeps helper executables out of the
    # install root. Alongside is the fallback for a plain PyInstaller build.
    root = install_root()
    for candidate in (root / "bin" / _RUNNER_EXE, root / _RUNNER_EXE):
        if candidate.exists():
            return str(candidate)

    logger.error("chart runner interpreter not found under %s", root)
    return None


# Only what data analysis and plotting actually need. Anything absent from this
# set is rejected rather than sandboxed further - a chart script has no business
# importing os.
ALLOWED_IMPORTS = {
    "pandas", "numpy", "matplotlib", "math", "statistics", "datetime",
    "json", "csv", "collections", "itertools", "functools", "re",
    "decimal", "fractions", "random", "string", "textwrap", "warnings",
    "io", "typing",
}

# Called out explicitly so the error message can name them; the allowlist above
# already excludes everything not in it.
BLOCKED_IMPORTS = {
    "os", "sys", "subprocess", "socket", "shutil", "pathlib", "requests",
    "httpx", "urllib", "http", "ftplib", "telnetlib", "pickle", "shelve",
    "ctypes", "importlib", "builtins", "multiprocessing", "threading",
    "webbrowser", "tempfile", "glob", "sqlite3", "asyncio",
}

# Source-level constructs that defeat the import check by resolving names at
# runtime, so they are refused outright.
_FORBIDDEN_PATTERNS = [
    (re.compile(r"\b__import__\s*\("), "__import__()"),
    (re.compile(r"\beval\s*\("), "eval()"),
    (re.compile(r"\bexec\s*\("), "exec()"),
    (re.compile(r"\bcompile\s*\("), "compile()"),
    (re.compile(r"\bglobals\s*\(\s*\)"), "globals()"),
    (re.compile(r"\b__subclasses__\b"), "__subclasses__"),
    (re.compile(r"\bopen\s*\("), "open()"),
]

TIMEOUT_SECONDS = 30.0
MAX_OUTPUT_CHARS = 8_000
CHART_FILENAME = "chart.png"

# Prepended to every script. Sets the headless backend before pyplot is imported
# (importing it first would already have picked a GUI backend), and neutralizes
# plt.show() so a script written for a notebook still saves its figure here.
_PREAMBLE = """
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as _plt
_plt.show = lambda *a, **k: None
"""

# Appended after the script. Saves whatever figure the script left open, so the
# model does not have to know the exact output path or remember to call savefig.
_EPILOGUE = f"""
import matplotlib.pyplot as _plt_out
_figures = [_plt_out.figure(n) for n in _plt_out.get_fignums()]
if _figures:
    _figures[-1].savefig({CHART_FILENAME!r}, dpi=140, bbox_inches="tight")
"""


class CodeRejected(Exception):
    """The script failed static checks, so it was never executed."""


@dataclass
class RunResult:
    ok: bool
    stdout: str
    error: str | None
    #: base64-encoded PNG, if the script produced a figure.
    image_base64: str | None
    duration_s: float


def _find_imports(source: str) -> set[str]:
    """Collect top-level module names from import statements via the AST.

    AST rather than regex: a regex over source text is fooled by imports inside
    strings or comments, in both directions.
    """
    import ast

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise CodeRejected(f"That code has a syntax error: {exc.msg} (line {exc.lineno})") from exc

    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                modules.add(node.module.split(".")[0])
            elif node.level:
                raise CodeRejected("Relative imports are not allowed.")
    return modules


def validate(source: str) -> None:
    """Reject a script before any process is started. Raises CodeRejected."""
    if not source.strip():
        raise CodeRejected("There is no code to run.")

    for pattern, label in _FORBIDDEN_PATTERNS:
        if pattern.search(source):
            raise CodeRejected(
                f"{label} is not allowed in chart code. "
                "Use the data already provided in the conversation."
            )

    for module in sorted(_find_imports(source)):
        if module in BLOCKED_IMPORTS:
            raise CodeRejected(
                f"Importing '{module}' is not allowed. Chart code may only use "
                "pandas, numpy and matplotlib."
            )
        if module not in ALLOWED_IMPORTS:
            raise CodeRejected(
                f"Importing '{module}' is not allowed. Chart code may only use "
                f"{', '.join(sorted(ALLOWED_IMPORTS)[:6])} and similar analysis libraries."
            )


async def run_chart_code(source: str, data_files: dict[str, bytes] | None = None) -> RunResult:
    """Validate, then execute the script in a throwaway process and temp dir.

    data_files maps filename -> bytes and is written into the working directory
    before the run, which is how an uploaded CSV reaches pandas.read_csv without
    the script ever touching the real filesystem.
    """
    validate(source)

    loop = asyncio.get_running_loop()
    started = loop.time()

    interpreter = _interpreter()
    if interpreter is None:
        return RunResult(
            ok=False,
            stdout="",
            error=(
                "The chart runner is unavailable because this installation is "
                "missing its Python component. Reinstalling Buddy will restore it."
            ),
            image_base64=None,
            duration_s=0.0,
        )

    workdir = Path(tempfile.mkdtemp(prefix="buddy-chart-"))

    try:
        for name, payload in (data_files or {}).items():
            # Flatten any path components: a filename from an upload must not be
            # able to escape the working directory.
            safe_name = Path(name).name
            (workdir / safe_name).write_bytes(payload)

        script = _PREAMBLE + "\n" + source + "\n" + _EPILOGUE
        script_path = workdir / "_buddy_chart.py"
        script_path.write_text(script, encoding="utf-8")

        # -I isolates the interpreter: no site-packages from the user's
        # environment beyond what this venv provides, no PYTHON* env vars, and
        # the script's own directory is kept off sys.path.
        # -I isolates a real interpreter from the user's environment; the
        # frozen runner is already isolated and takes only a path.
        argv = [interpreter] if IS_FROZEN else [interpreter, "-I", "-B"]

        process = await asyncio.create_subprocess_exec(
            *argv,
            str(script_path),
            cwd=str(workdir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=_NO_WINDOW,
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(), timeout=TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return RunResult(
                ok=False,
                stdout="",
                error=f"The code ran longer than {int(TIMEOUT_SECONDS)}s and was stopped.",
                image_base64=None,
                duration_s=loop.time() - started,
            )

        stdout = stdout_bytes.decode("utf-8", errors="replace")[:MAX_OUTPUT_CHARS]
        stderr = stderr_bytes.decode("utf-8", errors="replace")[:MAX_OUTPUT_CHARS]

        image_base64: str | None = None
        chart_path = workdir / CHART_FILENAME
        if chart_path.exists():
            image_base64 = base64.b64encode(chart_path.read_bytes()).decode("ascii")

        if process.returncode != 0:
            return RunResult(
                ok=False,
                stdout=stdout,
                error=_summarize_traceback(stderr),
                image_base64=image_base64,
                duration_s=loop.time() - started,
            )

        if image_base64 is None and not stdout.strip():
            return RunResult(
                ok=False,
                stdout=stdout,
                error="The code ran but produced no chart and no output.",
                image_base64=None,
                duration_s=loop.time() - started,
            )

        return RunResult(
            ok=True,
            stdout=stdout,
            error=None,
            image_base64=image_base64,
            duration_s=loop.time() - started,
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _summarize_traceback(stderr: str) -> str:
    """Surface the exception line rather than the whole traceback.

    The frames all point into a temp file the user cannot see, so they are noise;
    the final line is the part that says what actually went wrong.
    """
    lines = [line for line in stderr.strip().splitlines() if line.strip()]
    if not lines:
        return "The code failed without reporting an error."
    for line in reversed(lines):
        if not line.startswith(" ") and ":" in line:
            return line.strip()
    return lines[-1].strip()
