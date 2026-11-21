"""doctor: what the flow left stale in a project, as data, and --fix for the safe repairs.

Each finding carries a severity that says what happens to it: "auto" is repaired by `doctor --fix`; "route" names
the command that repairs it (doctor never runs it); "mention" is only information. diagnose() reads and never writes:
not even the ledger, which core.ledger_read() would create.
"""
import json
import shutil
from pathlib import Path

import compat
from core import LEDGER_JSON, QUALITY_BASELINE, ROOT, RULES_DIR, RULES_LIBRARY, STATE, is_flow_repo
from guardrail import GIT_BASH_VAR, git_bash_state, hooks_static_problem
from installer import INSTALL_RECORD, RETIRED_PERMISSIONS, flow_version
from repo_scan import wanted_rules

SETTINGS = ROOT / ".claude" / "settings.json"
SETTINGS_LOCAL = ROOT / ".claude" / "settings.local.json"  # per machine; module level so tests can point it elsewhere
GUARD_DIR = STATE / "guard"
UPGRADE_DIR = STATE / "upgrade"
INIT = "/agentic-flow:init"
AGENTIC = "python3 .claude/bin/agentic.py"


def auto(code, message, fix):
    return {"code": code, "severity": "auto", "message": message, "fix": fix}


def route(code, message, command):
    return {"code": code, "severity": "route", "message": message, "route": command}


def read_json(path):
    """The parsed file, or None if it is missing or not valid JSON."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def retired_rules():
    data = read_json(SETTINGS)
    allow = ((data.get("permissions") or {}).get("allow") or []) if isinstance(data, dict) else []
    return [rule for rule in allow if rule in RETIRED_PERMISSIONS]


def install_problem():
    record = ROOT / INSTALL_RECORD
    if not record.exists():
        return f"no {INSTALL_RECORD}"
    data = read_json(record)
    if not isinstance(data, dict):
        return f"{INSTALL_RECORD} is not valid JSON"
    if data.get("version") != flow_version():
        return f"the install record says {data.get('version')}, the installed flow is {flow_version()}"
    return None


def rules_problem():
    wanted = wanted_rules()
    have = {p.stem: p for p in RULES_DIR.glob("*.md")} if RULES_DIR.is_dir() else {}
    missing = [n for n in wanted if n not in have]
    extra = sorted(n for n in have if n not in wanted)
    changed = [n for n in wanted if n in have and have[n].read_text(encoding="utf-8")
               != (RULES_LIBRARY / f"{n}.md").read_text(encoding="utf-8")]
    parts = [f"{label}: {', '.join(names)}" for label, names in
             (("missing", missing), ("extra", extra), ("changed", changed)) if names]
    return f"{RULES_DIR.relative_to(ROOT).as_posix()}/ does not match the stack ({'; '.join(parts)})" if parts else None


def running_ids():
    data = read_json(LEDGER_JSON) if LEDGER_JSON.exists() else None
    if not isinstance(data, dict):
        return []
    return sorted(k for k, v in data.items() if isinstance(v, dict) and v.get("state") == "running")


def git_bash_finding(state):
    """The "git-bash" finding for a git_bash_state() result, or None (not Windows, configured, or a default install)."""
    if not state:
        return None
    if state["needs_path"]:
        return auto("git-bash", f"Claude Code may not find Git Bash at {state['derived']} (outside Program Files): "
                                "hooks then run in PowerShell and break; restart Claude Code after the fix",
                    f"write {GIT_BASH_VAR}={state['derived']} to .claude/settings.local.json")
    if state["missing"]:
        return {"code": "git-bash", "severity": "mention",
                "message": f"Git Bash was not found: install Git for Windows, or set {GIT_BASH_VAR}"}
    return None


def write_git_bash_path(settings_local, bash):
    """Sets env.CLAUDE_CODE_GIT_BASH_PATH in the JSON file settings_local, creating the file or the env object and
    keeping every other key. A file that is not a JSON object is left alone: ValueError."""
    settings_local = Path(settings_local)
    data = json.loads(settings_local.read_text(encoding="utf-8")) if settings_local.exists() else {}
    if not isinstance(data, dict) or not isinstance(data.get("env", {}), dict):
        raise ValueError(f"{settings_local} is not a JSON object with an env object")
    data.setdefault("env", {})[GIT_BASH_VAR] = bash
    settings_local.parent.mkdir(parents=True, exist_ok=True)
    settings_local.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def diagnose(git_bash=git_bash_state):
    """The findings for ROOT, in a fixed order. Reads only; never writes. `git_bash` gives the Git Bash state."""
    found = []
    if GUARD_DIR.exists():
        found.append(auto("guard-leftover", ".claude/state/guard/ is left over from the frozen guard retired in 2.10.0",
                          "delete .claude/state/guard/"))
    for rule in retired_rules()[:1]:
        found.append(auto("retired-permission", f"settings.json still allows {rule}",
                          "remove it from permissions.allow"))
    hook = hooks_static_problem()
    if hook:
        found.append(route("hook-missing", hook, INIT))
    install = None if is_flow_repo(ROOT) else install_problem()
    if install:
        found.append(route("install-record", install, INIT))
    if not (ROOT / QUALITY_BASELINE).exists():
        found.append(route("quality-baseline", f"no {QUALITY_BASELINE}", f"{AGENTIC} quality --init"))
    rules = rules_problem() if (RULES_LIBRARY / "common.md").exists() else None
    if rules:
        found.append(route("rules", rules, f"{AGENTIC} rules-sync"))
    running = running_ids()
    if running:
        found.append(route("agents-running", f"the ledger says {', '.join(running)} running; if no run is in progress, "
                                             "stop them", f"{AGENTIC} stop"))
    stale, live = kept_copies()
    if stale:
        found.append(auto("upgrade-stale", f"{len(stale)} kept version(s) in .claude/state/upgrade/ are obsolete: the "
                                           "installed file is no longer edited", "delete them"))
    if live:
        found.append({"code": "upgrade-pending", "severity": "mention",
                      "message": f"{len(live)} new version(s) kept beside your edits in .claude/state/upgrade/"})
    bash = git_bash_finding(git_bash())
    if bash:
        found.append(bash)
    return found


def kept_copies():
    """(stale, live): the files under UPGRADE_DIR, as sorted posix paths relative to it. A copy is stale when the
    installed file is gone, the record does not know the path, or the installed file is the one recorded (no longer
    edited). Without a readable record every copy is live."""
    if not UPGRADE_DIR.is_dir():
        return [], []
    copies = sorted(p.relative_to(UPGRADE_DIR).as_posix() for p in UPGRADE_DIR.rglob("*") if p.is_file())
    record = read_json(ROOT / INSTALL_RECORD)
    files = record.get("files") if isinstance(record, dict) else None
    if not isinstance(files, dict):
        return [], copies
    stale = [rel for rel in copies if not (ROOT / rel).is_file() or rel not in files
             or compat.content_hash(ROOT / rel) == files[rel]]
    return stale, [rel for rel in copies if rel not in stale]


def remove_stale_copies():
    for rel in kept_copies()[0]:
        (UPGRADE_DIR / rel).unlink()


def remove_retired_rules():
    data = read_json(SETTINGS)
    data["permissions"]["allow"] = [r for r in data["permissions"]["allow"] if r not in RETIRED_PERMISSIONS]
    SETTINGS.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


REPAIRS = {"guard-leftover": lambda: shutil.rmtree(GUARD_DIR), "retired-permission": remove_retired_rules,
           "upgrade-stale": remove_stale_copies}


def repair_git_bash(git_bash):
    """Writes the derived Git Bash path; False when there is none or the local settings cannot take it."""
    derived = (git_bash() or {}).get("derived")
    if not derived:
        return False
    try:
        write_git_bash_path(SETTINGS_LOCAL, derived)
    except (OSError, ValueError):
        return False
    return True


def repair(findings, git_bash=git_bash_state):
    """Applies the fix of each "auto" finding; returns the codes it fixed, in order."""
    fixed = []
    for finding in findings:
        if finding["severity"] != "auto":
            continue
        if finding["code"] == "git-bash":
            if repair_git_bash(git_bash):
                fixed.append("git-bash")
            continue
        REPAIRS[finding["code"]]()
        fixed.append(finding["code"])
    return fixed


def report(fix=False, git_bash=git_bash_state):
    """doctor's output: {"ok", "fixed", "findings"}, the findings read again after any repair."""
    fixed = repair(diagnose(git_bash), git_bash) if fix else []
    findings = diagnose(git_bash)
    ok = not any(f["severity"] in ("auto", "route") for f in findings)
    return {"ok": ok, "fixed": fixed, "findings": findings}
