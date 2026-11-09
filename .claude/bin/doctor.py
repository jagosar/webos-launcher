"""doctor: what the flow left stale in a project, as data, and --fix for the safe repairs.

Each finding carries a severity that says what happens to it: "auto" is repaired by `doctor --fix`; "route" names
the command that repairs it (doctor never runs it); "mention" is only information. diagnose() reads and never writes:
not even the ledger, which core.ledger_read() would create.
"""
import json
import shutil

from core import LEDGER_JSON, QUALITY_BASELINE, ROOT, RULES_DIR, RULES_LIBRARY, STATE, is_flow_repo
from guardrail import hooks_static_problem
from installer import INSTALL_RECORD, RETIRED_PERMISSIONS, flow_version
from repo_scan import wanted_rules

SETTINGS = ROOT / ".claude" / "settings.json"
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


def diagnose():
    """The findings for ROOT, in a fixed order. Reads only; never writes."""
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
    pending = sum(1 for p in UPGRADE_DIR.rglob("*") if p.is_file()) if UPGRADE_DIR.is_dir() else 0
    if pending:
        found.append({"code": "upgrade-pending", "severity": "mention",
                      "message": f"{pending} new version(s) kept beside your edits in .claude/state/upgrade/"})
    return found


def remove_retired_rules():
    data = read_json(SETTINGS)
    data["permissions"]["allow"] = [r for r in data["permissions"]["allow"] if r not in RETIRED_PERMISSIONS]
    SETTINGS.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


REPAIRS = {"guard-leftover": lambda: shutil.rmtree(GUARD_DIR), "retired-permission": remove_retired_rules}


def repair(findings):
    """Applies the fix of each "auto" finding; returns their codes, in order."""
    fixed = []
    for finding in findings:
        if finding["severity"] == "auto":
            REPAIRS[finding["code"]]()
            fixed.append(finding["code"])
    return fixed


def report(fix=False):
    """doctor's output: {"ok", "fixed", "findings"}, the findings read again after any repair."""
    fixed = repair(diagnose()) if fix else []
    findings = diagnose()
    ok = not any(f["severity"] in ("auto", "route") for f in findings)
    return {"ok": ok, "fixed": fixed, "findings": findings}
