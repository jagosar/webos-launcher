"""The flow's shared core: paths, constants, manifest access, the ledger, locked state files and the flow-repo tests.

Every other module of the flow imports from here with explicit names; nothing here imports a domain module, so the
import graph has no cycles. The frozen copy of a maintainer run (.claude/state/guard/bin) carries its own core.py in
the same folder as its agentic.py, so HERE, ROOT and ENTRY resolve to the frozen tree exactly as agentic.py did.
"""
import fnmatch
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compat  # noqa: E402  - the operating-system layer, next to this file

HERE = Path(__file__).resolve().parent
ENTRY = HERE / "agentic.py"  # the entry script, live or frozen: what subprocesses and run_steps execute
# The frozen guard of a maintainer run: copies of this folder and of the pipeline spec.
GUARD_DIR = ".claude/state/guard"
GUARD_CMD = "python3 .claude/state/guard/bin/agentic.py"
LIVE_CMD = "python3 .claude/bin/agentic.py"
# A frozen copy runs from GUARD_DIR/bin, four levels below the repo instead of two.
FROZEN = HERE.name == "bin" and HERE.parent.name == "guard" and HERE.parent.parent.name == "state"
# Where the flow is installed from: this repo, or the plugin.
SOURCE_ROOT = HERE.parents[3] if FROZEN else HERE.parents[1]
ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or SOURCE_ROOT)
ARCH = ROOT / "architecture.md"
STATE = ROOT / ".claude" / "state"
USAGE = STATE / "usage.jsonl"
CLAIMS = STATE / "claims.json"
LEDGER_JSON = STATE / "ledger.json"
LEDGER_MD = STATE / "ledger.md"
LEDGER_STATES = ("running", "done", "blocker", "pass", "fail", "timeout", "stopped", "error")
GATE = STATE / "gate.json"
HOOK_SEEN = STATE / "hook-seen.json"  # the hook's heartbeat: its last call, and the last one from an agent of the flow
HOOK_SEEN_MAX_AGE_S = 120  # a heartbeat older than this does not prove the hook runs now
HOOK_SEEN_COMMAND_CHARS = 300
GATE_MAX_DENIALS = 2  # per WP and stage; afterwards the report is let through
GATE_MAX_LINES = 40  # lines shown to the agent in a denial
GATE_TIMEOUT_S = 840  # below the hook's 900 s, so the gate times out before the CLI kills the hook
GATE_STATUSES = ("DONE", "DONE_WITH_CONCERNS")
WP_ID_RE = re.compile(r"WP-\d+")
CONFIG = ROOT / ".claude" / "agentic.json"
BASE_TOOLS = ["Read", "Write", "Edit", "Glob", "Grep"]
QA_DEFAULT_FILES = ["qa/"]
AT_DEFAULT_FILES = ["acceptance/"]
DOCS_DEFAULT_FILES = ["README.md", "CHANGELOG.md", "docs/"]
DEFAULT_CODEMAP = "docs/CODEMAP.md"
RESERVED_IDS = {"QA", "AT", "REVIEW", "DOCS"}
ARCHIVE_DIR = "docs/architecture/"  # decision archive: written by /architect, never by a WP
# Never owned, not even in maintainer mode: the settings that register the hooks, the flow's state (the frozen guard
# included) and the two live hook scripts. An agent that could change them could reroute or replace the guard.
NEVER_OWNED = (".claude/settings.json", ".claude/settings.local.json", ".claude/state/", ".claude/bin/hook.py",
               ".claude/bin/waiting.py", ".claude/quality-baseline.json")
QUALITY_BASELINE = ".claude/quality-baseline.json"  # the clean-code ratchet: only a person writes it
FLOW_NAME = "agentic-flow"
TEMPLATE_MARKER = "<!-- agentic-flow-template -->"  # README.md's first line in the flow's own repo
RULES_LIBRARY = ROOT / ".claude" / "rules-library"
RULES_DIR = ROOT / ".claude" / "rules" / "agentic"  # Claude Code carga .claude/rules/; agentic/ lo gestiona rules-sync
# Repo scanning, shared by the stack detection, the code map, the callers and the ratchet.
SKIP_DIRS = {".git", ".claude", "node_modules", "vendor", ".venv", "venv", "env", "dist", "build", "target", "out",
             "__pycache__", ".tox", ".mypy_cache", ".pytest_cache", ".next", "coverage"}
# Top-level public symbols, by extension, for the code map. One regex per line; private ones are skipped.
SYMBOL_RES = {
    (".py", ".pyi"): re.compile(r"^(?:async\s+)?(?:def|class)\s+([A-Za-z]\w*)"),
    (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"): re.compile(
        r"^export\s+(?:default\s+)?(?:declare\s+)?(?:async\s+)?"
        r"(?:function\*?|class|const|let|interface|type|enum)\s+([A-Za-z_$][\w$]*)"),
    (".go",): re.compile(r"^(?:func|type)\s+(?:\([^)]*\)\s*)?([A-Z]\w*)"),
    (".rs",): re.compile(r"^pub\s+(?:async\s+)?(?:fn|struct|enum|trait|type|mod|const)\s+(\w+)"),
    (".java", ".kt", ".cs", ".swift", ".php"): re.compile(
        r"^(?:public\s+|internal\s+|open\s+|final\s+|abstract\s+|sealed\s+|data\s+|static\s+|partial\s+|readonly\s+)*"
        r"(?:class|interface|record|enum|object|struct|protocol|trait)\s+(\w+)"),
    (".rb",): re.compile(r"^(?:class|module)\s+([A-Z][\w:]*)"),
}
VERIFY_PHASES = ("build", "types", "lint", "tests")
BASELINE = STATE / "baseline.json"
# Every orchestrated agent (implementers and QA) always uses the latest Sonnet. `sonnet` is the alias
# Claude Code resolves to the newest Sonnet it knows; it cannot be changed by config or environment.
WORKER_MODEL = "sonnet"
READ_ONLY_AGENTS = {"verifier", "test-auditor"}  # no zone of their own: they run as the read-only REVIEW entity
DEFAULT_CONFIG = {
    "timeout_s": 1800,
    # "subscription": agents use your Claude session (subscription) even if an API key is in the environment.
    # "inherit": they inherit the environment as is (useful in CI with an API key).
    "auth": "subscription",
    "max_fix_cycles": 2,  # further --fix cycles on the same WP escalate to the human or to /architect
}
# Variables that would make an agent bill the API instead of the subscription.
API_AUTH_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
MANIFEST_RE = re.compile(r"```json\s+wp-manifest\s*\n(.*?)\n```", re.S)
# ---- Content guardrails (adapted from ECC: config-protection, block-no-verify, governance-capture) ----
# Shared by the hook (content_checks) and the stage check (content_problems).
# Quality config: creating it is allowed; modifying existing config is not (unless "config_edit": true on the WP).
PROTECTED_CONFIG_RE = re.compile(
    r"^(\.eslintrc(\.(js|cjs|json|ya?ml))?|eslint\.config\.[cm]?[jt]s|\.prettierrc(\.(js|cjs|json|ya?ml))?|"
    r"prettier\.config\.[cm]?js|\.?biome\.jsonc?|\.?ruff\.toml|\.flake8|\.pylintrc|pylintrc|\.?mypy\.ini|"
    r"pytest\.ini|tox\.ini|setup\.cfg|\.coveragerc|pyproject\.toml|tsconfig(\.[\w-]+)?\.json|"
    r"(jest|vitest|playwright)\.config\.[cm]?[jt]s|\.stylelintrc(\.\w+)?|\.golangci\.ya?ml|\.?rustfmt\.toml|"
    r"clippy\.toml|\.editorconfig|\.shellcheckrc|\.(eslint|prettier|stylelint)ignore)$", re.I)
# Unconditionally disabling tests to turn them green (conditional skipIf/skipUnless are allowed).
SKIP_MARKER_RE = re.compile(
    r"@unittest\.skip\b(?!If|Unless)|\bunittest\.skip\(|@skip\(|\bexpectedFailure\b|"
    r"pytest\.mark\.(skip|xfail)\b(?!if)|\bpytest\.(skip|xfail)\(|"
    r"\b(it|test|describe|context)\.(skip|only|todo)\(|\bx(it|describe|test)\(|\bf(it|describe)\(|"
    r"\bt\.Skip(Now)?\(|#\[ignore\]|"
    r"\.skipTest\(|\braise\s+(?:unittest\.)?SkipTest\b|\bskip(?:If|if)\(\s*True\b|\bskipUnless\(\s*False\b")
TEST_PATH_RE = re.compile(r"(^|/)(tests?|spec|__tests__|acceptance|qa)/|(^|/)test_[^/]*$|"
                          r"_test\.\w+$|\.(test|spec)\.\w+$")
SECRET_RES = [
    (re.compile(r"AKIA[0-9A-Z]{16}"), "an AWS access key"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,}"), "a GitHub token"),
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"), "an Anthropic key"),
    (re.compile(r"\bsk-(proj-)?[A-Za-z0-9]{32,}"), "an sk- style API key"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"), "a Slack token"),
    (re.compile(r"AIza[0-9A-Za-z_\-]{35}"), "a Google key"),
    (re.compile(r"-----BEGIN (RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY"), "a private key"),
]


def root_drift():
    """Why this call would act on the wrong repository, or None. CLAUDE_PROJECT_DIR follows a session's working
    directory, and in a cloud session that directory moves between tool calls: a command run from inside one repository
    while the variable still names another would validate, measure or rewrite the other one. The signature: the
    variable is set, this script belongs to a different repository, and the working directory is not inside the one
    the variable names. (install and flow-history act on their target and the source: agentic.py exempts them.)"""
    if not os.environ.get("CLAUDE_PROJECT_DIR") or SOURCE_ROOT.resolve() == ROOT.resolve():
        return None
    cwd, root = Path.cwd().resolve(), ROOT.resolve()
    if cwd == root or root in cwd.parents:
        return None
    return (f"CLAUDE_PROJECT_DIR names {ROOT}, but this agentic.py belongs to {SOURCE_ROOT} and the working directory "
            f"is {cwd}: the command would act on {ROOT}. Run it from inside that repository, or unset the variable.")


# ---------- errors and configuration ----------

class ManifestError(Exception):
    pass

def die(msg, code=1):
    print(msg, file=sys.stderr)
    sys.exit(code)

def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if CONFIG.exists():
        user = json.loads(CONFIG.read_text(encoding="utf-8"))
        for key, value in user.items():
            if isinstance(value, dict) and isinstance(cfg.get(key), dict):
                cfg[key].update(value)
            else:
                cfg[key] = value
    env = os.environ
    if "AGENTIC_TIMEOUT_S" in env:
        cfg["timeout_s"] = int(env["AGENTIC_TIMEOUT_S"])
    return cfg


# ---------- manifest ----------

def load_manifest():
    if not ARCH.exists():
        raise ManifestError(f"{ARCH} does not exist. Run /architect first.")
    m = MANIFEST_RE.search(ARCH.read_text(encoding="utf-8"))
    if not m:
        raise ManifestError("architecture.md has no ```json wp-manifest``` block.")
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        raise ManifestError(f"The manifest is not valid JSON: {e}")
    if not isinstance(data, dict):
        raise ManifestError("The manifest must be a JSON object.")
    return data

def wps(manifest):
    return {wp["id"]: wp for wp in manifest.get("work_packages", []) if isinstance(wp, dict) and "id" in wp}

def qa_entity(manifest):
    qa = manifest.get("qa", {})
    return {"id": "QA", "files": qa.get("files", QA_DEFAULT_FILES), "allow_bash": qa.get("allow_bash", [])}

def at_entity(manifest):
    at = manifest.get("acceptance", {})
    return {"id": "AT", "files": at.get("files", AT_DEFAULT_FILES), "allow_bash": at.get("allow_bash", []),
            "command": at.get("command")}

def review_entity(manifest):
    return {"id": "REVIEW", "files": [], "allow_bash": []}  # read-only: the hook blocks any write

def docs_entity(manifest):
    """The doc-updater's zone. It only exists if the manifest has a "docs" section (living documentation)."""
    docs = manifest.get("docs")
    if not isinstance(docs, dict):
        return None
    return {"id": "DOCS", "files": docs.get("files", DOCS_DEFAULT_FILES), "allow_bash": [],
            "codemap": docs.get("codemap", DEFAULT_CODEMAP)}

def codemap_path(manifest):
    docs = docs_entity(manifest) if manifest else None
    return docs["codemap"] if docs else None

def entities(manifest):
    """Everything that can claim ownership of files: WPs, QA, acceptance, the reviewer and documentation."""
    table = wps(manifest)
    table["QA"] = qa_entity(manifest)
    table["AT"] = at_entity(manifest)
    table["REVIEW"] = review_entity(manifest)
    docs = docs_entity(manifest)
    if docs:
        table["DOCS"] = docs
    return table

def owns(pattern, path):
    """An exact path, or a directory prefix if the pattern ends in '/'."""
    return path.startswith(pattern) if pattern.endswith("/") else path == pattern

def owner_conflict(a, b):
    return a == b or (a.endswith("/") and b.startswith(a)) or (b.endswith("/") and a.startswith(b))

def ignored(path, patterns):
    parts = path.split("/")
    for pat in patterns:
        if pat.endswith("/"):
            if pat[:-1] in parts[:-1] or path.startswith(pat):
                return True
        elif fnmatch.fnmatch(path, pat) or fnmatch.fnmatch(parts[-1], pat):
            return True
    return False


# ---------- the flow's own repo and maintainer mode ----------

def is_flow_repo(root):
    """The flow's own repo: its plugin manifest is named agentic-flow and README.md starts with the template marker
    (a copy made from the template loses the marker)."""
    try:
        plugin = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        first = (root / "README.md").read_text(encoding="utf-8").split("\n", 1)[0]
    except (OSError, ValueError):
        return False
    return isinstance(plugin, dict) and plugin.get("name") == FLOW_NAME and first.strip() == TEMPLATE_MARKER

def maintainer_mode(manifest):
    """The flag and the flow's own repo: the hook and close-stage never run validate, so a stray flag in a project
    relaxes nothing."""
    return manifest.get("maintainer") is True and is_flow_repo(ROOT)

def never_owned_problem(path):
    """Why `path` cannot be owned even in maintainer mode, or None."""
    hit = next((n for n in NEVER_OWNED if owner_conflict(path, n)), None)
    if hit is None:
        return None
    return f"{hit} is never owned, not even in maintainer mode (it decides which guard runs)."

def flow_path_refused(path, maintainer):
    """A path under .claude/ that no agent writes: all of them, or in maintainer mode only the never-owned ones."""
    return path.startswith(".claude/") and (not maintainer or never_owned_problem(path) is not None)


# ---------- repo files, siblings and the agents' environment ----------

def child_env(cfg, **extra):
    env = dict(os.environ, **extra)
    if cfg.get("auth", "subscription") == "subscription":
        for var in API_AUTH_VARS:
            env.pop(var, None)
    return env

def source_files(root=None):
    """Repo files for detecting the stack and building the code map: git's if there is a repo, without dependencies
    or builds."""
    root = root or ROOT
    try:
        out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=root,
                             capture_output=True, check=True).stdout.decode()
        files = [p for p in out.split("\0") if p]
    except (subprocess.CalledProcessError, FileNotFoundError):
        files = [p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()]
    return sorted(f for f in files if not set(f.split("/")[:-1]) & SKIP_DIRS and (root / f).is_file())

def symbol_re(name):
    return next((rx for exts, rx in SYMBOL_RES.items() if name.endswith(exts)), None)

def sibling(name):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    return __import__(name)


# ---------- locked state files ----------

def locked_json(path, fn):
    """Runs fn(data) with the JSON file locked; if it returns a dict, saves it. Returns the previous data."""
    STATE.mkdir(parents=True, exist_ok=True)
    with open(path, "a+", encoding="utf-8") as fh, compat.locked(fh):
        fh.seek(0)
        raw = fh.read()
        try:
            data = json.loads(raw) if raw.strip() else {}
        except ValueError:  # a crash mid-write: keep the broken copy for inspection and start again
            Path(f"{path}.corrupt").write_text(raw, encoding="utf-8")
            print(f"[agentic] {Path(path).name} was corrupt: set aside as {Path(path).name}.corrupt", file=sys.stderr)
            data = {}
        new = fn(data)
        if new is not None:
            fh.seek(0)
            fh.truncate()
            fh.write(json.dumps(new, indent=2))
        return data

def with_claims(fn):
    return locked_json(CLAIMS, fn)


# ---------- ledger ----------

def now_hm():
    return time.strftime("%H:%M")

def ledger_read():
    return locked_json(LEDGER_JSON, lambda d: None)

def ledger_update(jid, **fields):
    def upd(d):
        entry = dict(d.get(jid, {}), **fields, updated=now_hm())
        return dict(d, **{jid: entry})
    locked_json(LEDGER_JSON, upd)
    ledger_render()

def ledger_stage(label, result):
    def upd(d):
        stages = dict(d.get("_stages", {}), **{str(label): f"{result} ({now_hm()})"})
        return dict(d, _stages=stages)
    locked_json(LEDGER_JSON, upd)
    ledger_render()

def status_to_state(status):
    head = status.split()[0] if status else ""
    return {"DONE": "done", "DONE_WITH_CONCERNS": "done", "NEEDS_CONTEXT": "blocker", "BLOCKED": "blocker",
            "BLOCKER": "blocker", "PASS": "pass", "FAIL": "fail", "TIMEOUT": "timeout",
            "STOPPED": "stopped"}.get(head, "error")

def stage_of(jid):
    if jid in ("QA", "AT", "REVIEW", "DOCS"):
        return {"QA": "qa", "AT": "acceptance", "REVIEW": "review", "DOCS": "docs"}[jid]
    try:
        return str(wps(load_manifest()).get(jid, {}).get("wave", "?"))
    except ManifestError:
        return "?"

def usage_records():
    """The usage lines, read under the lock: on Windows a lock is mandatory, so reading while an agent appends would
    raise PermissionError."""
    if not USAGE.exists():
        return []
    with open(USAGE, encoding="utf-8") as fh, compat.locked(fh):
        text = fh.read()
    return [json.loads(line) for line in text.splitlines() if line.strip()]

def usage_summary(records):
    costs = [r["cost_usd"] for r in records if r.get("cost_usd") is not None]
    total = {k: sum(r.get(k) or 0 for r in records) for k in ("input", "cache_read", "output", "turns")}
    return {"total": {**total, "cost_usd": sum(costs) if costs else None}, "by_agent": records}

def usage_line(r):
    cost = "?" if r.get("cost_usd") is None else f"{r['cost_usd']:.2f}"
    return f"in {r['input']} · cache {r['cache_read']} · out {r['output']} · ${cost}"

def ledger_render():
    d = ledger_read()
    stages = d.get("_stages", {})
    agents = {k: v for k, v in d.items() if not k.startswith("_")}
    order = lambda kv: (kv[1].get("stage") in ("qa", "docs"), str(kv[1].get("stage")), kv[0])
    lines = ["# Flow ledger", "", f"Updated: {now_hm()}", ""]
    try:  # the agents waiting for the user's permission or answer, first: that is why a run looks stuck
        waiting = sibling("waiting")
        waits = [waiting.describe(w, time.time()) for w in waiting.current(STATE)]
    except Exception:  # noqa: BLE001 - a missing or broken waiting.py only hides this section
        waits = []
    if waits:
        lines += ["## Waiting for you", ""] + [f"- {w}" for w in waits] + [""]
    lines += ["## Stages", ""]
    pipeline = {"baseline": -1, "acceptance": 0, "acceptance (red)": 1, "acceptance (green)": 3, "verification": 3.5,
                "review": 4, "qa": 5, "docs": 6, "docs-check": 6.5}
    order_st = lambda k: (2, k.zfill(4)) if k.isdigit() else (pipeline.get(k, 7), k)  # pipeline order
    lines += ([f"- {'wave ' + k if k.isdigit() else k}: {stages[k]}" for k in sorted(stages, key=order_st)]
              or ["- (none)"])
    lines += ["", "## Agents", ""]
    for jid, e in sorted(agents.items(), key=order):
        parts = [f"- [{e.get('state', '?')}]".ljust(11) + f" {jid} ({WORKER_MODEL})",
                 f"stage {e.get('stage', '?')}", f"mode {e.get('mode', '?')}", f"started {e.get('started', '?')}"]
        if e.get("pid"):
            parts.append(f"pid {e['pid']}")
        if e.get("note"):
            parts.append(e["note"])
        lines.append(" — ".join(parts))
    if not agents:
        lines.append("- (none)")
    records = usage_records()
    if records:
        lines += ["", "## Usage", ""] + [f"- {r['label']}: {usage_line(r)}" for r in records]
        lines.append("Total: " + usage_line(usage_summary(records)["total"]))
    LEDGER_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")

def stop(ids):
    d = ledger_read()
    targets = [k for k, v in d.items() if not k.startswith("_") and v.get("state") == "running"
               and (not ids or k in ids)]
    if not targets:
        print("No agents are running" + (f" with those IDs: {', '.join(ids)}." if ids else "."))
        return
    for jid in targets:
        e = d[jid]
        ledger_update(jid, state="stopped", note="stopped on request")
        if e.get("pid"):
            compat.kill_tree(e["pid"])
            print(f"{jid}: stopped (pid {e['pid']}).")
        else:
            print(f"{jid}: marked as stopped. It is a workflow agent: stop it from /workflows (key x).")


# ---------- the flow's hook in a settings file ----------

def is_old_hook_command(command):
    """The hook command before 2.5.0: `agentic.py ... hook`."""
    return "agentic.py" in command and command.rstrip().endswith("hook")

def flow_hooks(pre_tool_use):
    """The flow's own entries in a PreToolUse list: (entry, hook) pairs whose command runs the launcher `hook.py`, or
    `agentic.py ... hook` as before 2.5.0."""
    return [(e, h) for e in pre_tool_use for h in e.get("hooks", [])
            if ".claude/bin/hook.py" in h.get("command", "") or is_old_hook_command(h.get("command", ""))]
