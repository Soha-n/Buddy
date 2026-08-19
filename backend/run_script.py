"""Entry point for the sandboxed chart interpreter.

Frozen as ``buddy-runner``, a second executable that exists for one reason:
``code_runner`` needs something safe to spawn.

A frozen application cannot re-invoke ``sys.executable`` to run a script - that
starts another copy of the whole application. This script is what the runner
executable runs instead: it takes a path, executes it, and exits. Effectively a
minimal ``python script.py`` that happens to be frozen with pandas, numpy and
matplotlib already inside.

Argument handling mirrors the interpreter it replaces, so ``code_runner`` can
pass the same ``-I -B`` flags whether it is running from source or packaged;
they are accepted and ignored, since a frozen build is already isolated from
the user's site-packages and writes no bytecode.
"""

from __future__ import annotations

import runpy
import sys


def main() -> int:
    # Drop interpreter flags; only the script path and its arguments remain.
    argv = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not argv:
        print("usage: buddy-runner <script.py>", file=sys.stderr)
        return 2

    script = argv[0]
    # Present the script with the argv it would see under a real interpreter.
    sys.argv = argv

    # run_name="__main__" so the script's own main-guard fires, matching what
    # `python script.py` does.
    runpy.run_path(script, run_name="__main__")
    return 0


if __name__ == "__main__":
    sys.exit(main())
