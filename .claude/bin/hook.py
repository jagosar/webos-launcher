"""The flow's PreToolUse hook launcher: runs `agentic.py hook` from the frozen guard while a maintainer run has one
active (.claude/state/guard/active.json), and from the live .claude/bin otherwise.

It is never owned by a work package, not even in maintainer mode: an agent that could edit it could pick the guard
that judges it. Standard library only, no type annotations (Python 3.9), and it never imports agentic.py at module
level, so a broken live agentic.py cannot break the choice.
"""
import os
import runpy
import sys
from pathlib import Path


def target():
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])
    guard = root / ".claude" / "state" / "guard"
    frozen = guard / "bin" / "agentic.py"
    if (guard / "active.json").is_file() and frozen.is_file():
        return frozen
    return root / ".claude" / "bin" / "agentic.py"


def main():
    try:
        script = target()
    except Exception as e:  # noqa: BLE001 - no guard can be chosen: fail closed
        print(f"[agentic hook.py] cannot choose the guard, blocked for safety: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(2)
    sys.argv = [str(script), "hook"]
    runpy.run_path(str(script), run_name="__main__")


if __name__ == "__main__":
    main()
