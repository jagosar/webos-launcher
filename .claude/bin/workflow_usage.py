"""The token usage of a native workflow run, read from Claude Code's own files (design 0027).

Claude Code keeps, per session, the record of each workflow run and the transcript of each of its agents:
    <config>/projects/<project>/<session>/workflows/<runId>.json
    <config>/projects/<project>/<session>/subagents/workflows/<runId>/agent-<agentId>.jsonl
That layout is internal and undocumented (seen in Claude Code 2.1.316), so nothing here raises: what cannot be read is
left out, and the caller is told why. Only token counts, turns, labels, agent types and models leave a transcript.
"""
import json
import os
import re
from pathlib import Path

from core import usage_summary

RUN_ID_RE = re.compile(r"wf_[A-Za-z0-9-]+")  # matched whole: nothing that could leave the folder
AGENT_ID_RE = re.compile(r"[A-Za-z0-9_-]+")  # matched whole: the record is not the flow's own file
PACKAGE_RE = re.compile(r"WP-\d+")
IDENTS = {"test-author": "AT", "qa": "QA", "doc-updater": "DOCS", "gatekeeper": "gatekeeper"}  # any other: REVIEW
NOT_AN_ID = "RUN_ID is not a workflow run id"
NO_RECORD = "no record of that run was found"
UNREADABLE = "the run's record could not be read"


def _config_dir(config_dir):
    return Path(config_dir or os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def find_run(run_id, config_dir=None):
    """The run record <config>/projects/*/*/workflows/<run_id>.json, the newest if several; None when run_id does
    not match RUN_ID_RE or no such file exists. config_dir: CLAUDE_CONFIG_DIR, or ~/.claude."""
    if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
        return None
    try:
        found = sorted(_config_dir(config_dir).glob(f"projects/*/*/workflows/{run_id}.json"),
                       key=lambda p: p.stat().st_mtime)
    except OSError:
        return None
    return found[-1] if found else None


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _transcript_usage(path):
    """{"input", "cache_read", "output", "turns"} of one agent's transcript, or None when it cannot be read. One API
    message is written on several lines with the same id: it counts once."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return None
    seen, total = set(), {"input": 0, "cache_read": 0, "output": 0, "turns": 0}
    for line in lines:
        try:
            message = json.loads(line).get("message")
        except (ValueError, AttributeError):
            continue
        used = message.get("usage") if isinstance(message, dict) else None
        ident = message.get("id") if isinstance(message, dict) else None
        if not isinstance(used, dict) or not isinstance(ident, str) or ident in seen:
            continue
        seen.add(ident)
        total["input"] += _count(used.get("input_tokens")) + _count(used.get("cache_creation_input_tokens"))
        total["cache_read"] += _count(used.get("cache_read_input_tokens"))
        total["output"] += _count(used.get("output_tokens"))
        total["turns"] += 1
    return total


def _text(value):
    return value if isinstance(value, str) else ""


def _agent_record(entry, folder):
    """The usage record of one agent of the run, or None when its transcript cannot be named or read."""
    agent_id = entry.get("agentId")
    if not isinstance(agent_id, str) or not AGENT_ID_RE.fullmatch(agent_id):
        return None
    used = _transcript_usage(folder / f"agent-{agent_id}.jsonl")
    if used is None:
        return None
    agent, label = _text(entry.get("agentType")), _text(entry.get("label"))
    package = PACKAGE_RE.match(label)
    return {"id": package.group(0) if package else IDENTS.get(agent, "REVIEW"), "agent": agent, "label": label,
            "model": entry.get("model") if isinstance(entry.get("model"), str) else None, **used, "cost_usd": None}


def usage_of_run(record):
    """The usage of the run whose record this is: usage_summary's shape, plus "source" and "missing" (agents of the
    record whose transcript could not be read). The run id is record.stem, never the record's own "runId". None when
    the record cannot be read, is not an object, or names no agent. Never raises."""
    try:
        data = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    progress = data.get("workflowProgress") if isinstance(data, dict) else None
    agents = [e for e in progress if isinstance(e, dict) and e.get("type") == "workflow_agent"] \
        if isinstance(progress, list) else []
    if not agents:
        return None
    folder = record.parent.parent / "subagents" / "workflows" / record.stem
    records = [r for r in (_agent_record(e, folder) for e in agents) if r]
    return {**usage_summary(records), "source": "claude-code", "missing": len(agents) - len(records)}


def usage_for(run_id, config_dir=None):
    """(usage, reason): the usage and "", or None and why in a few words."""
    if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
        return None, NOT_AN_ID
    record = find_run(run_id, config_dir)
    if record is None:
        return None, NO_RECORD
    found = usage_of_run(record)
    return (found, "") if found else (None, UNREADABLE)
