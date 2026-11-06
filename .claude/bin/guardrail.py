"""The PreToolUse hook: the guard that decides every tool call of an agent of the flow, the claims, the content checks
on writes, the heartbeat, and hooks-check. It fails closed: inside an agent, any error blocks.
"""
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compat  # noqa: E402  - the operating-system layer, next to this file
from core import (  # noqa: E402
    ARCHIVE_DIR, GATE_MAX_DENIALS, HOOK_SEEN, HOOK_SEEN_COMMAND_CHARS, HOOK_SEEN_MAX_AGE_S, ManifestError,
    PROTECTED_CONFIG_RE, READ_ONLY_AGENTS, ROOT, SECRET_RES, SKIP_MARKER_RE, TEST_PATH_RE, WP_ID_RE, at_entity,
    codemap_path, entities, flow_hooks, flow_path_refused, ledger_update, load_manifest, locked_json,
    now_hm, owns, stage_of, with_claims, wps
)
from stage import gate_check, gate_denials  # noqa: E402

# A claim is the whole command, never part of one: `claim WP-01 && git push` must still meet the Bash rules.
CLAIM_RE = re.compile(r"\s*(?:(?:python3?|sh)\s+)?[\w./-]*agentic(?:\.py)?\s+claim\s+(WP-\d+|QA|AT|REVIEW|DOCS)\s*")

# A write subcommand in the same command as `git`, even with options in front (`git -C . commit`)
# or inside quotes (`sh -c "git commit"`). Requires a space before it so `src/add.py` is not mistaken.
GIT_WRITE_RE = re.compile(
    r"\bgit\b[^;&|\n]*?\s[\"']?(commit|push|pull|fetch|checkout|switch|reset|rebase|merge|stash|branch|tag|restore|"
    r"clean|cherry-pick|revert|am|apply|config|remote|worktree|update-ref|update-index|filter-branch|gc|rm|mv|add|"
    r"submodule)(?=$|[\s\"';&|])")

FORBIDDEN_BASH = [
    (GIT_WRITE_RE, "git operations that write are not allowed (only status, diff, log, show)"),
    (re.compile(r"\b(npm|pnpm|yarn|bun)\s+(install|i|add|remove|uninstall|rm|ci)\b|\byarn\s*(?:$|[;&|])"),
     "dependencies cannot be changed (section 3)"),
    (re.compile(r"\b(pip[\d.]*|uv\s+pip)\s+install\b|\buv\s+(add|sync)\b|\bpoetry\s+(add|install)\b|"
                r"\bcargo\s+add\b|\bgo\s+get\b"),
     "dependencies cannot be changed (section 3)"),
    (re.compile(r"(?:^|[\s\"'=])\.git[/\\]"), "git's own files (.git/) are not touched"),
    (re.compile(r"\bagentic\.py\b[^;&|\n]*\bquality\b[^;&|\n]*\s--(?:init|update)\b"),
     "the quality baseline is written only by a person (agentic.py quality --init/--update)"),
    # `claude` as the command (alone, or at the end of a path like /usr/bin/claude), never inside a path such as
    # .claude/bin or docs/claude-notes.md, which a -p flag later on the line (unittest's) does not make a session.
    (re.compile(r"(?<![\w.-])claude(?:-code)?(?![\w./-])(?=.*(\s-p\b|\s--print\b))", re.S),
     "other Claude sessions cannot be launched"),
]

# ---- Content guardrails (adapted from ECC: config-protection, block-no-verify, governance-capture) ----
# Bypassing git hooks: forbidden for everyone, the main session included.
# git accepts an unambiguous prefix of a long option (--no-veri), short options together (-an) and configuration
# through the environment (GIT_CONFIG_KEY_0=core.hooksPath).
GIT_HOOK_BYPASS_RE = re.compile(
    r"\bgit\b[^;&|\n]*?(\s--no-veri(?:f|fy)?\b|\s-c\s*[\"']?core\.hookspath=)"
    r"|\bGIT_CONFIG_(?:KEY_\d+\s*=\s*[\"']?core\.hookspath\b|PARAMETERS\s*=[^\n]*core\.hookspath)", re.I)

# Matched without the quoted text (QUOTED_RE blanks it first).
GIT_COMMIT_N_RE = re.compile(r"\bgit\b[^;&|\n]*?\scommit\b[^;&|\n]*?\s-[a-zA-Z]*n[a-zA-Z]*\b")

MAX_CHECKED_COMMAND = 10_000  # longer agent commands are refused: the rules are regexes, and the hook has a time limit

QUOTED_RE = re.compile(r"'[^']*'|\"[^\"]*\"")

# What each of the flow's agent types may claim; an implementer (or a WP's own agent type) claims its packages.
CLAIM_KINDS = {"qa": {"QA"}, "test-author": {"AT"}, "doc-updater": {"DOCS"}, "reviewer": {"REVIEW"},
               "verifier": {"REVIEW"}, "test-auditor": {"REVIEW"}}

HOOKS_DEAD_MSG = (
    "The guardrail hook (`agentic.py hook`) did not run for this call, so no guardrail of the flow is active in this "
    "session. Known causes: a cloud session with several repositories attached, or a thread inside a Claude project "
    "(Claude Code does not read the repository's hooks there); `disableAllHooks` in a settings file; "
    "Windows without Git "
    "for Windows. The fallback mode runs each agent as `claude -p`, which does load the repository's hooks: "
    "`/implement fallback`. Or open a session with this repository alone.")


# ---------- claims and refusals ----------

def block(msg):
    print(f"[agentic guard] {msg}", file=sys.stderr)
    sys.exit(2)

def claimable(manifest, agent_type):
    """The IDs an agent of this type may claim, or None if the type is not one of the flow's (no restriction)."""
    if agent_type in CLAIM_KINDS:
        return CLAIM_KINDS[agent_type]
    packages = {wid for wid, wp in wps(manifest).items() if wp.get("agent", "implementer") == agent_type}
    return packages if packages or agent_type == "implementer" else None

def claim(agent_id, wanted, agent_type=None):
    manifest = load_manifest()
    if wanted not in entities(manifest):
        block(f"{wanted} does not exist in the manifest.")
    allowed = claimable(manifest, agent_type)
    if allowed is not None and wanted not in allowed:
        block(f"a {agent_type} agent cannot claim {wanted}: claim your own ID "
              f"({', '.join(sorted(allowed)) or 'none'}).")
    conflict = {}

    def update(claims):
        current = claims.get(agent_id)
        holder = next((a for a, w in claims.items() if w == wanted and a != agent_id), None)
        if current and current != wanted:
            conflict["msg"] = f"this agent already claimed {current}; it cannot also claim {wanted}."
        elif holder:
            conflict["msg"] = f"{wanted} was already claimed by another agent in this stage."
        else:
            return dict(claims, **{agent_id: wanted})
        return None

    with_claims(update)
    if conflict:
        block(conflict["msg"])
    try:  # the ledger is informational: a failure here must not block the claim
        ledger_update(wanted, state="running", stage=stage_of(wanted), mode="workflow", pid=None, started=now_hm(),
                      note="")
    except Exception:  # noqa: BLE001
        pass

def claim_required_types():
    """The flow's agents with a zone of their own, including those a WP declares in "agent": without a claim, they
    do not write."""
    types = {"implementer", "qa", "test-author", "reviewer", "doc-updater"}
    try:
        types |= {wp["agent"] for wp in wps(load_manifest()).values() if isinstance(wp.get("agent"), str)}
    except ManifestError:
        pass
    return types


# ---------- content checks on writes ----------

def written_text(tool, tinput):
    """What the tool is about to write, and what it replaces (for comparison)."""
    if tool == "Write":
        return tinput.get("content") or "", None
    if tool == "Edit":
        return tinput.get("new_string") or "", tinput.get("old_string") or ""
    if tool == "MultiEdit":
        edits = tinput.get("edits") or []
        return ("\n".join(e.get("new_string", "") for e in edits),
                "\n".join(e.get("old_string", "") for e in edits))
    if tool == "NotebookEdit":
        return tinput.get("new_source") or "", None
    return "", None

def result_text(tool, tinput, abs_path):
    """The whole file as the tool would leave it, and as it is now: (new, old). (None, None) if it cannot be worked
    out (an edit whose old text is not in the file fails anyway)."""
    try:
        current = abs_path.read_text(encoding="utf-8", errors="replace") if abs_path.is_file() else ""
    except OSError:
        return None, None
    if tool == "Write":
        return tinput.get("content") or "", current
    edits = [tinput] if tool == "Edit" else tinput.get("edits") if tool == "MultiEdit" else None
    if not isinstance(edits, list):
        return None, None
    text = current
    for e in edits:
        old, new = e.get("old_string") or "", e.get("new_string") or ""
        if old not in text:
            return None, None
        text = text.replace(old, new) if e.get("replace_all") else text.replace(old, new, 1)
    return text, current

def content_checks(owner_id, ent, rel, abs_path, tool, tinput):
    # Whole files, not fragments: two harmless-looking edits must not assemble a secret or a skip.
    new, old = result_text(tool, tinput, abs_path)
    if new is None:
        new, old = written_text(tool, tinput)
    for rx, what in SECRET_RES:
        if len(rx.findall(new)) > len(rx.findall(old or "")):
            block(f"{owner_id}: what you are about to write to {rel} looks like {what}. "
                  "Use a synthetic value or an environment variable; never a real secret.")
    if PROTECTED_CONFIG_RE.match(Path(rel).name) and abs_path.exists() and not ent.get("config_edit"):
        block(f"{owner_id}: {rel} is existing quality configuration. A rule is not relaxed to pass "
              "a check: fix the code. If it really has to change, return the blocked status your schema allows "
              "(BLOCKED for an implementer, BLOCKER for the others).")
    if TEST_PATH_RE.search(rel):
        if old is None:
            try:
                old = (abs_path.read_text(encoding="utf-8", errors="replace")
                       if tool == "Write" and abs_path.exists() else "")
            except OSError:
                old = ""
        if len(SKIP_MARKER_RE.findall(new)) > len(SKIP_MARKER_RE.findall(old)):
            block(f"{owner_id}: tests are not disabled to turn them green (skip, xfail, only…) in {rel}. "
                  "A failing test is fixed or reported: return the blocked status your schema allows (BLOCKED for an "
                  "implementer, BLOCKER for the others). "
                  "Conditional platform skips (skipIf/skipUnless) are allowed.")


# ---------- the guard ----------

def universal_checks(tool, tinput, agent_id, owner_id):
    """What no agent may do, claimed or not: unverifiable commands and git hook bypasses."""
    bash_cmd = tinput.get("command", "") if tool == "Bash" else ""
    if len(bash_cmd) > MAX_CHECKED_COMMAND and (agent_id or owner_id):
        block(f"a command longer than {MAX_CHECKED_COMMAND} characters cannot be checked: "
              "split it or use a script file.")
    if bash_cmd and (GIT_HOOK_BYPASS_RE.search(bash_cmd) or GIT_COMMIT_N_RE.search(QUOTED_RE.sub("''", bash_cmd))):
        block("bypassing git hooks (--no-verify, commit -n, core.hooksPath) is not allowed for any agent. "
              "If a hook fails, fix the cause or ask the user to decide.")

def resolve_owner(data, tool, tinput, agent_id, owner_id):
    """(claimed, owner_id): a claim command claims and ends the call; otherwise the owner from the environment or
    the claims file, or None."""
    if tool == "Bash" and agent_id and not owner_id:
        m = CLAIM_RE.fullmatch(tinput.get("command", ""))
        if m:
            claim(agent_id, m.group(1), data.get("agent_type"))
            return True, None

    if not owner_id and agent_id:
        owner_id = with_claims(lambda c: None).get(agent_id)
    return False, owner_id

def report_gate(owner_id, tinput):
    """StructuredOutput: the report itself, checked by the stop gate for a finished package."""
    # The report itself. A finished package is checked by the stop gate; a report is never refused for a missing claim.
    if owner_id and WP_ID_RE.fullmatch(owner_id):
        failing = gate_check(owner_id, tinput.get("status"))
        if failing:
            denials = gate_denials(lambda d: None).get(owner_id, {}).get("denials", 0)
            cmd = at_entity(load_manifest())["command"]
            block(f"{owner_id}: the report says {tinput.get('status')} but the acceptance tests of your package fail "
                  f"(denial {denials} of {GATE_MAX_DENIALS}). Failing lines:\n  " + "\n  ".join(failing)
                  + f"\nRun `{cmd}`, fix your files and submit the report again. If a test contradicts "
                  "architecture.md, return BLOCKED and say which.")

def require_claim(data, agent_id, owner_id):
    """An agent of a kind that must claim its zone cannot write before doing so."""
    if not owner_id and agent_id:
        if not owner_id and data.get("agent_type") in claim_required_types():
            block("First of all, run `python3 .claude/bin/agentic.py claim <ID>` with your WP-ID "
                  "(or QA, AT, REVIEW, DOCS).")

def bash_rules(owner_id, cmd):
    """The Bash rules for an agent with a zone."""
    for rx, why in FORBIDDEN_BASH:
        if rx.search(cmd):
            block(f"{owner_id}: command blocked, {why}: {cmd}")

def write_rules(data, owner_id, tool, tinput):
    """A write: inside the repo, outside the protected paths, inside the owner's zone, and clean."""
    path = tinput.get("file_path") or tinput.get("notebook_path")
    if not path:
        return
    abs_path = Path(path) if os.path.isabs(path) else Path(data.get("cwd") or ROOT) / path
    try:
        rel = abs_path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        block(f"{owner_id}: you cannot write outside the repo: {path}")
    manifest = load_manifest()
    ent = entities(manifest).get(owner_id)
    if not ent:
        block(f"{owner_id} does not exist in the manifest of architecture.md.")
    # No agent of the flow writes the plan, the flow's configuration, the decision archive or the generated
    # code map, even if its zone includes them by prefix (for example, "docs/").
    if (rel == "architecture.md" or rel.startswith(ARCHIVE_DIR) or rel == codemap_path(manifest)
            or flow_path_refused(rel)):
        block(f"{owner_id}: {rel} is not written by any agent of the flow (plan, configuration, decision archive "
              "or generated map). If it has to change, return the blocked status your schema allows (BLOCKED for an "
              "implementer, BLOCKER for the others).")
    if not any(owns(f, rel) for f in ent["files"]):
        block(f"{owner_id} does not own {rel}. Allowed files: {', '.join(ent['files'])}. "
              "If you need to touch it, return the blocked status your schema allows (BLOCKED for an implementer, "
              "BLOCKER for the others; QA reports it as a defect).")
    content_checks(owner_id, ent, rel, abs_path, tool, tinput)

def guard(data):
    """The PreToolUse decision, in today's order: the checks for everyone, the claim and the owner, the report gate,
    the claim requirement, then the Bash rules or the write rules for an agent with a zone."""
    tool = data.get("tool_name", "")
    tinput = data.get("tool_input", {}) or {}
    agent_id = data.get("agent_id")
    owner_id = os.environ.get("AGENTIC_WP")
    universal_checks(tool, tinput, agent_id, owner_id)
    claimed, owner_id = resolve_owner(data, tool, tinput, agent_id, owner_id)
    if claimed:
        return
    if tool == "StructuredOutput":
        report_gate(owner_id, tinput)
        return
    require_claim(data, agent_id, owner_id)
    if not owner_id:
        return  # hilo principal u otro subagente: sin restricciones
    if tool == "Bash":
        bash_rules(owner_id, tinput.get("command", ""))
        return
    write_rules(data, owner_id, tool, tinput)


# ---------- the hook and its heartbeat ----------

def is_flow_agent(data):
    """A call from an agent of the flow: a flow agent type, or a fallback agent (AGENTIC_WP). Never the gatekeeper or
    the main thread, which run whether or not the agents' hooks do."""
    if os.environ.get("AGENTIC_WP"):
        return True
    return data.get("agent_type") in claim_required_types() | READ_ONLY_AGENTS

def record_hook_seen(data):
    """The heartbeat: every hook call records itself; calls from agents of the flow also move guarded_ts."""
    now = time.time()
    tinput = data.get("tool_input") or {}
    command = tinput.get("command", "") if data.get("tool_name") == "Bash" else ""
    guarded = is_flow_agent(data)

    def update(seen):
        new = dict(seen, ts=now, tool=data.get("tool_name", ""), command=str(command)[:HOOK_SEEN_COMMAND_CHARS],
                   agent_type=data.get("agent_type"))
        new["guarded_ts"] = now if guarded else seen.get("guarded_ts")
        return new
    locked_json(HOOK_SEEN, update)

def hooks_static_problem():
    """Why the project's settings would not run the flow's hook, or None."""
    settings = ROOT / ".claude" / "settings.json"
    try:
        data = json.loads(settings.read_text(encoding="utf-8")) if settings.exists() else {}
    except ValueError:
        return "settings.json is not valid JSON"
    if not flow_hooks((data.get("hooks") or {}).get("PreToolUse", [])):
        return "no agentic.py hook in .claude/settings.json PreToolUse"
    for name in ("settings.json", "settings.local.json"):
        path = ROOT / ".claude" / name
        try:
            local = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except ValueError:
            continue
        if local.get("disableAllHooks") is True:
            return f"disableAllHooks is true in .claude/{name}"
    return None

def hooks_check():
    """hooks-check: inside a Claude tool call (CLAUDECODE), the hook must have fired for it; elsewhere, the project
    settings must configure it. Prints one JSON line; returns 0 or 3."""
    if os.environ.get("CLAUDECODE"):
        seen = locked_json(HOOK_SEEN, lambda s: None) if HOOK_SEEN.exists() else {}
        ts = seen.get("ts")
        fresh = isinstance(ts, (int, float)) and time.time() - ts <= HOOK_SEEN_MAX_AGE_S
        if fresh:
            print(json.dumps({"ok": True, "mode": "live", "seen": ts}))
            return 0
        print(json.dumps({"ok": False, "mode": "live", "seen": ts if isinstance(ts, (int, float)) else None,
                          "reason": "the guardrail hook did not run for this call"}))
        print(HOOKS_DEAD_MSG, file=sys.stderr)
        return 3
    problem = hooks_static_problem()
    if problem:
        print(json.dumps({"ok": False, "mode": "static", "reason": problem}))
        print(f"The guardrail hook is not configured: {problem}. Run `python3 .claude/bin/agentic.py install .` from a "
              "copy of the flow to restore .claude/settings.json, or remove disableAllHooks.", file=sys.stderr)
        return 3
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    hook_cmd = flow_hooks(settings["hooks"]["PreToolUse"])[0][1]["command"]
    print(json.dumps({"ok": True, "mode": "static", "hook": hook_cmd}))
    return 0

def hook():
    """Fails closed: any error inside a WP or QA blocks the tool."""
    restricted = bool(os.environ.get("AGENTIC_WP"))
    try:
        data = json.loads(compat.read_stdin_text())
        restricted = restricted or bool(data.get("agent_id"))
        record_hook_seen(data)
        guard(data)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001 - any failure is a reason to block
        if restricted:
            block(f"guardrail error, blocked for safety: {type(e).__name__}: {e}")
        print(f"[agentic guard] aviso: {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(0)
