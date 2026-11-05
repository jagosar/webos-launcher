"""The flow's PreToolUse hook launcher: runs `agentic.py hook` from the repository's .claude/bin.

settings.json names this file, so it stays where projects expect it. It is never owned by a work package: an agent that
could edit it could replace the guard that judges it. Standard library only, no type annotations (Python 3.9), and
it never imports agentic.py at module level, so a broken agentic.py cannot break the launcher itself.
"""
import os
import runpy
import sys
from pathlib import Path


def target():
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])
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
