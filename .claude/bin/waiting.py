"""The agents of a run that are waiting for the user: a permission to approve or a question to answer.

Claude Code runs this hook on PermissionRequest (a wait starts), PostToolUse, PostToolUseFailure and SubagentStop (it
ends). It records the waits in .claude/state/waiting.json; agentic.py shows them at the top of `status` and the ledger.
It only observes: it always exits 0 and prints nothing to stdout (an exit 2 on PermissionRequest would deny the
permission, and a hook's stdout can reach the agent). It loads the standard library and compat, never agentic.py, so
the hook that runs after every tool call stays short.
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compat  # noqa: E402  - the operating-system layer, next to this file

WAITING_FILE = "waiting.json"
STARTS = "PermissionRequest"
ENDS = ("PostToolUse", "PostToolUseFailure", "SubagentStop")
DETAIL_KEYS = ("command", "file_path", "url", "description", "prompt")
MAX_DETAIL = 120


def _read(path: Path) -> dict:
    """A JSON object from path, read under the lock (on Windows a lock is mandatory); {} if missing or corrupt."""
    if not path.is_file():
        return {}
    try:
        with open(path, encoding="utf-8") as fh, compat.locked(fh):
            data = json.loads(fh.read() or "{}")
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _update(path: Path, fn) -> None:
    """fn(data) -> new data, with the file locked (as agentic.py's locked_json)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+", encoding="utf-8") as fh, compat.locked(fh):
        fh.seek(0)
        try:
            data = json.loads(fh.read() or "{}")
        except ValueError:
            data = {}
        new = fn(data if isinstance(data, dict) else {})
        fh.seek(0)
        fh.truncate()
        fh.write(json.dumps(new, indent=2))


def _detail(tool_input) -> str:
    if not isinstance(tool_input, dict):
        return ""
    text = next((tool_input[k] for k in DETAIL_KEYS if isinstance(tool_input.get(k), str) and tool_input[k]), "")
    text = " ".join(text.split())
    return text if len(text) <= MAX_DETAIL else text[:MAX_DETAIL] + "…"


def handle(payload: dict, state_dir: Path, now: float) -> None:
    """Records a subagent's permission request, or removes its wait when the tool ran, failed or the agent stopped."""
    agent_id = payload.get("agent_id") if isinstance(payload, dict) else None
    if not isinstance(agent_id, str) or not agent_id:
        return  # the main session: the user is already looking at its prompt
    event = payload.get("hook_event_name")
    path = Path(state_dir) / WAITING_FILE
    if event == STARTS:
        who = _read(Path(state_dir) / "claims.json").get(agent_id)
        entry = {"who": who if isinstance(who, str) else None, "agent_type": payload.get("agent_type") or None,
                 "tool": payload.get("tool_name") or "?", "detail": _detail(payload.get("tool_input")), "since": now}
        _update(path, lambda d: dict(d, **{agent_id: entry}))
    elif event in ENDS and path.is_file():
        _update(path, lambda d: {k: v for k, v in d.items() if k != agent_id})


def current(state_dir: Path) -> list[dict]:
    """The waits sorted by (since, agent_id), each with its agent_id. [] if there are none."""
    data = _read(Path(state_dir) / WAITING_FILE)
    waits = [dict(v, agent_id=k) for k, v in data.items() if isinstance(v, dict)]
    return sorted(waits, key=lambda w: (w.get("since") or 0, w["agent_id"]))


def describe(entry: dict, now: float) -> str:
    """One readable line: who, what it asks for, and for how long."""
    if entry.get("who"):
        name = f"{entry['who']} ({entry.get('agent_type') or 'agent'})"
    else:
        name = f"{entry['agent_type']} agent" if entry.get("agent_type") else "subagent"
    if entry.get("tool") == "AskUserQuestion":
        what = f"{name} has a question for you"
    else:
        what = f"{name} needs your permission for {entry.get('tool') or '?'}"
        if entry.get("detail"):
            what += f": {entry['detail']}"
    since = float(entry.get("since") or now)
    return f"{what} — waiting {int((now - since) // 60)} min (since {time.strftime('%H:%M', time.localtime(since))})"


def clear(state_dir: Path) -> None:
    """No waits left (a new stage starts); creates nothing if there is no file."""
    path = Path(state_dir) / WAITING_FILE
    if path.is_file():
        _update(path, lambda d: {})


def main() -> None:
    try:
        root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])
        handle(json.loads(compat.read_stdin_text()), root / ".claude" / "state", time.time())
    except Exception as e:  # noqa: BLE001 - an observer never blocks a tool: report and let it run
        print(f"[agentic waiting] {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    main()
