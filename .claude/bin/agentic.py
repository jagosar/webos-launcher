#!/usr/bin/env python3
"""Guardrails and parallel launcher for the agentic flow.

Subcommands:
  validate            Validates the manifest in architecture.md.
  snapshot L          Saves the state of the files before stage L (a wave number, or "qa").
  check-wave L        Checks that stage L only touched files its owners may write.
  run-wave N [WP...] [--fix [--all]]
                      Launches one `claude -p --agent implementer` per WP of wave N in parallel (or only
                      the WPs given). With --fix, asks them to fix their defects from the newest review or QA report
                      (the run record, run-pipeline's files or the one-off reports); --all adds medium and low ones.
  pipeline-plan       JSON with waves, WPs, acceptance, type, verify phases and the fix limit (workflow).
  open-stage L        Snapshot of stage L (a wave, acceptance, review, qa, docs) and its owners, as JSON.
  close-stage L ID=estado... [--expect red|green] [--fix]
                      Ownership + ledger states (+ acceptance) (+ fix count), in one call.
  run-pipeline [--start-wave N] [--resume]
                      The whole pipeline (wave 0, waves, verification, review, QA, fixes, docs) with one `claude -p` per
                      agent: the fallback mode and CI. Same stages and limits as the native workflow.
  seq CMD [ARGS] + CMD [ARGS] ...
                      Runs several of the commands above in order, stopping at the first that fails; one JSON line out.
  baseline            Baseline: the verify phases BEFORE touching anything (separates regressions from existing failures).
  verify              Build/types/lint/tests phases + green acceptance, compared with the baseline. JSON, with the
                      work packages that own what a failure mentions ("suspects").
  sync-permissions    Adds to .claude/settings.local.json the permissions the agents need.
  save-result [FILE]
                      Saves the workflow JSON in .claude/state/runs/workflow-result.json (from FILE, which it
                      deletes, or from stdin). The launcher cannot write in .claude/, a protected path.
  briefs              Writes the slice of architecture.md each WP and the test-author need, in .claude/state/briefs/.
  run-acceptance      Wave 0: launches the test-author agent, which writes the acceptance tests before the code.
  acceptance --expect red|green
                      Runs the manifest's acceptance command. In wave 0 they must fail (red);
                      after the waves, pass (green).
  run-review          Launches the code reviewer (read-only) on the integrated result.
  run-qa              Launches the QA agent (`claude -p --agent qa`) on the integrated result.
  run-docs            Launches the doc-updater, which brings the project's documentation up to date (the "docs" zone).
  stack               Detects the repo's languages and compares them with the manifest's "stack". JSON.
  rules-sync          Installs in .claude/rules/agentic/ the common rules and those of each language of the project.
  codemap [PATH]      Generates the code map (files and public symbols) without going through the model.
  callers             JSON: the public symbols of the changed files and their callers outside the diff (for the reviewer).
  docs-check          Checks links, anchors and paths cited in the documentation. JSON.
  install [DIR] [--force] [--dry-run] [--allow-dirty]
                      Installs or upgrades the flow in another repo (used by the plugin). Replaces only intact files,
                      keeps edited ones (new version in .claude/state/upgrade/), refuses over uncommitted changes or a run.
  flow-history        Maintainers: writes .claude/flow-history.json, every content the flow ever shipped, from git.
  claim ID            Binds the current subagent to a WP or to QA (recorded by the hook).
  status              Shows the ledger: the state of every agent and stage.
  mark ID ESTADO [NOTA]
                      Updates the ledger by hand: running, done, blocker, pass, fail, stopped.
  stop [ID...]        Stops the running agents (all of them if none is given) and records it in the ledger.
  hook                PreToolUse hook (reads JSON from stdin).
  hooks-check         JSON: whether the guardrail hook runs in this session (exit 3 if it does not).
  guard-release       Maintainer mode: the hook goes back to the live agentic.py (the engines call it at the end).

The ledger (.claude/state/ledger.md) is written by this script, not by the model: it survives context
compaction and is the source for answering "how is it going?".

The manifest is a ```json wp-manifest``` block inside architecture.md.
Launcher timeout and authentication: .claude/agentic.json (optional) and AGENTIC_* variables.
"""
import fnmatch
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compat  # noqa: E402  - the operating-system layer, next to this file

HERE = Path(__file__).resolve().parent
GUARD_DIR = ".claude/state/guard"  # the frozen guard of a maintainer run: copies of this folder and of the pipeline spec
GUARD_CMD = "python3 .claude/state/guard/bin/agentic.py"
LIVE_CMD = "python3 .claude/bin/agentic.py"
# A frozen copy runs from GUARD_DIR/bin, four levels below the repo instead of two.
FROZEN = HERE.name == "bin" and HERE.parent.name == "guard" and HERE.parent.parent.name == "state"
SOURCE_ROOT = HERE.parents[3] if FROZEN else HERE.parents[1]  # where the flow is installed from (this repo, or the plugin)
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
               ".claude/bin/waiting.py")
FLOW_NAME = "agentic-flow"
TEMPLATE_MARKER = "<!-- agentic-flow-template -->"  # README.md's first line in the flow's own repo
RULES_LIBRARY = ROOT / ".claude" / "rules-library"
RULES_DIR = ROOT / ".claude" / "rules" / "agentic"  # Claude Code carga .claude/rules/; agentic/ lo gestiona rules-sync
# Signals for each rules pack: project files (in any folder) and code extensions.
STACK_SIGNALS = {
    "python": (("pyproject.toml", "setup.py", "setup.cfg", "Pipfile", "requirements.txt"), (".py", ".pyi")),
    "typescript": (("package.json", "tsconfig.json", "deno.json"), (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")),
    "go": (("go.mod",), (".go",)),
    "rust": (("Cargo.toml",), (".rs",)),
    "java": (("pom.xml", "build.gradle"), (".java",)),
    "kotlin": ((), (".kt",)),
    "csharp": ((), (".cs", ".csproj", ".sln")),
    "ruby": (("Gemfile",), (".rb", ".rake")),
    "php": (("composer.json",), (".php",)),
    "swift": (("Package.swift",), (".swift",)),
}
SKIP_DIRS = {".git", ".claude", "node_modules", "vendor", ".venv", "venv", "env", "dist", "build", "target", "out",
             "__pycache__", ".tox", ".mypy_cache", ".pytest_cache", ".next", "coverage"}
# Top-level public symbols, by extension, for the code map. One regex per line; private ones are skipped.
SYMBOL_RES = {
    (".py", ".pyi"): re.compile(r"^(?:async\s+)?(?:def|class)\s+([A-Za-z]\w*)"),
    (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"): re.compile(
        r"^export\s+(?:default\s+)?(?:declare\s+)?(?:async\s+)?(?:function\*?|class|const|let|interface|type|enum)\s+([A-Za-z_$][\w$]*)"),
    (".go",): re.compile(r"^(?:func|type)\s+(?:\([^)]*\)\s*)?([A-Z]\w*)"),
    (".rs",): re.compile(r"^pub\s+(?:async\s+)?(?:fn|struct|enum|trait|type|mod|const)\s+(\w+)"),
    (".java", ".kt", ".cs", ".swift", ".php"): re.compile(
        r"^(?:public\s+|internal\s+|open\s+|final\s+|abstract\s+|sealed\s+|data\s+|static\s+|partial\s+|readonly\s+)*"
        r"(?:class|interface|record|enum|object|struct|protocol|trait)\s+(\w+)"),
    (".rb",): re.compile(r"^(?:class|module)\s+([A-Z][\w:]*)"),
}
# What `install` puts in another repo: the flow, without the flow's own tests.
INSTALL_GLOBS = [".claude/bin/agentic.py", ".claude/bin/hook.py", ".claude/bin/compat.py", ".claude/bin/waiting.py", ".claude/bin/pipeline.py", ".claude/bin/brief.py", ".claude/bin/upgrade.py", ".claude/agents/*.md", ".claude/commands/*.md", ".claude/skills/*/SKILL.md",
                 ".claude/workflows/*.js", ".claude/workflows/*.json", ".claude/rules-library/*.md"]
INSTALL_RECORD = ".claude/agentic-install.json"
RESULT_FILE = ".agentic-result.json"  # where the workflow leaves its result for save-result
INSTALL_GITIGNORE = [".claude/state/", RESULT_FILE]
VERIFY_PHASES = ("build", "types", "lint", "tests")
# A phase that already failed at baseline is compared failure by failure: the lines that name a failure, without the
# durations that change from run to run. A line not seen at baseline is a regression.
FAILURE_LINE_RE = re.compile(r"(?i)\b(?:fail(?:ed|ure|ures|ing|s)?|errors?)\b|[✕✗×❌]")
DURATION_RE = re.compile(r"\(?\b\d+(?:\.\d+)?\s?(?:ms|s|sec|secs|seconds)\b\)?|\b0x[0-9a-fA-F]+\b")  # and addresses
MAX_TAIL_LINE = 300  # a failure line can be thousands of characters long; the JSON report keeps it short
BASELINE = STATE / "baseline.json"
MIRROR_RE = re.compile(r"^([^:]+?)(?::(\d+)(?:-(\d+))?)?$")  # path[:start[-end]]
WORKFLOW_NAME = "agentic-implement"
TYPE_RE = re.compile(r"^(?:Type|Tipo):\s*(feature|change|fix|refactor)\b", re.M | re.I)
REVIEW_TOOLS = ["Read", "Glob", "Grep", "Bash(git diff:*)", "Bash(git status:*)", "Bash(git log:*)"]
# Every orchestrated agent (implementers and QA) always uses the latest Sonnet. `sonnet` is the alias
# Claude Code resolves to the newest Sonnet it knows; it cannot be changed by config or environment.
WORKER_MODEL = "sonnet"
# The architect always uses the latest Opus (alias `opus`, in /architect's frontmatter).
# The launcher (/implement) also runs on the latest Sonnet, from its frontmatter.
ARCHITECT_MODEL = "opus"
# The only exception to the worker model: the gatekeeper just relays a command and its output, so the fastest model
# does it. Nothing that reads or writes code may use it.
AGENT_MODEL_OVERRIDES = {"gatekeeper": "haiku"}
DEFAULT_CONFIG = {
    "timeout_s": 1800,
    # "subscription": agents use your Claude session (subscription) even if an API key is in the environment.
    # "inherit": they inherit the environment as is (useful in CI with an API key).
    "auth": "subscription",
    "max_fix_cycles": 2,  # further --fix cycles on the same WP escalate to the human or to /architect
}
# Variables that would make an agent bill the API instead of the subscription.
API_AUTH_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
# Caches and artefacts that test and build tools generate: they do not count as changes.
DEFAULT_IGNORE = ["__pycache__/", "*.pyc", ".pytest_cache/", ".mypy_cache/", ".ruff_cache/",
                  "node_modules/", ".coverage", "coverage/", ".nyc_output/", ".DS_Store", ".tsbuildinfo"]

MANIFEST_RE = re.compile(r"```json\s+wp-manifest\s*\n(.*?)\n```", re.S)
AC_RE = re.compile(r"AC-\d+")
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
    (re.compile(r"\b(pip[\d.]*|uv\s+pip)\s+install\b|\buv\s+(add|sync)\b|\bpoetry\s+(add|install)\b|\bcargo\s+add\b|\bgo\s+get\b"),
     "dependencies cannot be changed (section 3)"),
    (re.compile(r"(?:^|[\s\"'=])\.git[/\\]"), "git's own files (.git/) are not touched"),
    (re.compile(r"\bclaude\b(?=.*(\s-p\b|\s--print\b))", re.S),
     "other Claude sessions cannot be launched"),
]
# ---- Content guardrails (adapted from ECC: config-protection, block-no-verify, governance-capture) ----
# Bypassing git hooks: forbidden for everyone, the main session included.
# git accepts an unambiguous prefix of a long option (--no-veri), short options together (-an) and configuration
# through the environment (GIT_CONFIG_KEY_0=core.hooksPath).
GIT_HOOK_BYPASS_RE = re.compile(
    r"\bgit\b[^;&|\n]*?(\s--no-veri(?:f|fy)?\b|\s-c\s*[\"']?core\.hookspath=)"
    r"|\bGIT_CONFIG_(?:KEY_\d+\s*=\s*[\"']?core\.hookspath\b|PARAMETERS\s*=[^\n]*core\.hookspath)", re.I)
GIT_COMMIT_N_RE = re.compile(r"\bgit\b[^;&|\n]*?\scommit\b[^;&|\n]*?\s-[a-zA-Z]*n[a-zA-Z]*\b")  # without the quoted text
MAX_CHECKED_COMMAND = 10_000  # longer agent commands are refused: the rules are regexes, and the hook has a time limit
QUOTED_RE = re.compile(r"'[^']*'|\"[^\"]*\"")
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
TEST_PATH_RE = re.compile(r"(^|/)(tests?|spec|__tests__|acceptance|qa)/|(^|/)test_[^/]*$|_test\.\w+$|\.(test|spec)\.\w+$")
SECRET_RES = [
    (re.compile(r"AKIA[0-9A-Z]{16}"), "an AWS access key"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,}"), "a GitHub token"),
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"), "an Anthropic key"),
    (re.compile(r"\bsk-(proj-)?[A-Za-z0-9]{32,}"), "an sk- style API key"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"), "a Slack token"),
    (re.compile(r"AIza[0-9A-Za-z_\-]{35}"), "a Google key"),
    (re.compile(r"-----BEGIN (RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY"), "a private key"),
]

# First token of an allow_bash rule that would allow arbitrary execution on its own.
OPEN_ENDED = {"bash", "sh", "zsh", "fish", "dash", "python", "python3", "node", "deno", "bun", "ruby", "perl",
              "php", "env", "xargs", "sudo", "eval", "exec", "npx", "npm", "pnpm", "yarn", "uv", "poetry",
              "cargo", "go", "make", "git", "claude", "find", "awk", "sed"}


class ManifestError(Exception):
    pass


def die(msg, code=1):
    print(msg, file=sys.stderr)
    sys.exit(code)


# ---------- configuration ----------

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


def open_ended_rule(rule):
    inner = rule[len("Bash("):-1].strip()
    prefix = inner[:-2] if inner.endswith(":*") else inner
    tokens = prefix.split()
    return inner in ("*", ":*", "") or not tokens or "*" in prefix or (len(tokens) == 1 and tokens[0] in OPEN_ENDED)


def open_ended_command(cmd):
    """An acceptance or verify command that would become an open permission once synced into Bash(<cmd>:*), where
    Claude Code reads * as a wildcard: an open-ended program alone, a * in the program, or a * right after it (or
    after its -c / -e). A * in a later argument (`-p test_*.py`) keeps the program prefix fixed, so it is allowed."""
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        tokens = cmd.split()
    if not tokens or "*" in tokens[0] or (len(tokens) == 1 and tokens[0] in OPEN_ENDED):
        return True
    for i, token in enumerate(tokens[1:], 1):
        if "*" in token and (tokens[i - 1] in OPEN_ENDED or (tokens[i - 1] in ("-c", "-e") and i >= 2
                                                              and tokens[i - 2] in OPEN_ENDED)):
            return True
    return False


def validate(manifest):
    errors = []
    table = wps(manifest)
    flag = manifest.get("maintainer")
    if flag is not None and not isinstance(flag, bool):
        errors.append("maintainer: must be true or false.")
    elif flag and not is_flow_repo(ROOT):
        errors.append("maintainer: maintainer mode is only for the agentic-flow repository (.claude-plugin/plugin.json "
                      f"named {FLOW_NAME} and {TEMPLATE_MARKER} on README.md's first line).")
    maintainer = maintainer_mode(manifest)
    if not table:
        errors.append("The manifest has no work_packages.")
    for rid in RESERVED_IDS & set(table):
        errors.append(f"'{rid}' is a reserved ID.")
    at = manifest.get("acceptance")
    if at is not None and not (isinstance(at, dict) and isinstance(at.get("command"), str) and at["command"].strip()):
        errors.append("acceptance: needs 'command', the command that runs the acceptance tests.")
    seen = set()
    for wp in manifest.get("work_packages", []):
        if not isinstance(wp, dict) or "id" not in wp:
            errors.append(f"Work package without an id: {wp}")
            continue
        if wp["id"] in seen:
            errors.append(f"{wp['id']}: duplicate ID.")
        seen.add(wp["id"])
        if not WP_ID_RE.fullmatch(wp["id"]):
            errors.append(f"{wp['id']}: the ID must look like WP-<number>.")
        for key in ("wave", "files"):
            if key not in wp:
                errors.append(f"{wp['id']}: missing field '{key}'.")
        errors += mirror_errors(wp)
        acs = wp.get("acs", [])
        if not isinstance(acs, list) or any(not isinstance(a, str) or not AC_RE.fullmatch(a) for a in acs):
            errors.append(f"{wp['id']}: 'acs' must be a list of criteria such as [\"AC-001\"].")
        if "config_edit" in wp and not isinstance(wp["config_edit"], bool):
            errors.append(f"{wp['id']}: 'config_edit' must be true or false.")
        if not isinstance(wp.get("wave", 0), int):
            errors.append(f"{wp['id']}: 'wave' must be an integer.")
        if not isinstance(wp.get("depends_on", []), list):
            errors.append(f"{wp['id']}: 'depends_on' must be a list of WP IDs.")
            continue
        for dep in wp.get("depends_on", []):
            if dep not in table:
                errors.append(f"{wp['id']}: depends on {dep}, which does not exist.")
            elif not isinstance(table[dep].get("wave", 0), int) or not isinstance(wp.get("wave", 0), int):
                continue  # the wave type is already reported
            elif table[dep].get("wave", 0) >= wp.get("wave", 0):
                errors.append(f"{wp['id']} (wave {wp.get('wave')}) depends on {dep} (wave {table[dep].get('wave')}): it must be in an earlier wave.")
    for ent in entities(manifest).values():
        files = ent.get("files", [])
        if not isinstance(files, list) or not all(isinstance(f, str) for f in files):
            errors.append(f"{ent['id']}: 'files' must be a list of paths.")
            continue
        for f in files:
            # Paths are compared as text, so they are written as git shows them.
            if "\\" in f or "//" in f or f.startswith("./") or "/./" in f:
                errors.append(f"{ent['id']}: write the path as git shows it (no ./, // or backslashes): {f}")
                continue
            if f.startswith("/") or ".." in Path(f).parts:
                errors.append(f"{ent['id']}: path not relative to the repo: {f}")
            if f.startswith(".claude/") and maintainer:
                problem = never_owned_problem(f)
                if problem:
                    errors.append(f"{ent['id']}: cannot own '{f}': {problem}")
            elif f == "architecture.md" or f.startswith(".claude/") or f.startswith(ARCHIVE_DIR) or f in ("", "./", "/"):
                errors.append(f"{ent['id']}: cannot own '{f}'.")
    owners = [(f, e["id"]) for e in entities(manifest).values() if isinstance(e.get("files"), list)
              for f in e["files"] if isinstance(f, str)]
    for i, (fa, wa) in enumerate(owners):
        for fb, wb in owners[i + 1:]:
            if wa != wb and owner_conflict(fa, fb):
                errors.append(f"File with two owners: {fa} ({wa}) and {fb} ({wb}).")
    for rule in manifest.get("allow_bash", []) + qa_entity(manifest)["allow_bash"] + at_entity(manifest)["allow_bash"]:
        if not re.fullmatch(r"Bash\(.+\)", rule):
            errors.append(f"allow_bash: malformed rule: {rule}")
        elif open_ended_rule(rule):
            errors.append(f"allow_bash: rule too broad (it would allow arbitrary execution): {rule}. "
                          "Use the specific command, e.g. Bash(npm test:*) or Bash(python3 -m pytest:*).")
    commands = [at_entity(manifest)["command"]] + list(manifest.get("verify", {}).values()
                                                       if isinstance(manifest.get("verify", {}), dict) else [])
    for cmd in commands:
        if isinstance(cmd, str) and cmd.strip() and open_ended_command(cmd):
            errors.append(f"command too broad: '{cmd}' would let the agents run anything (Bash({cmd}:*)). "
                          "Use the specific command, e.g. python3 -m pytest.")
    if not isinstance(manifest.get("ignore", []), list):
        errors.append("'ignore' must be a list of patterns.")
    verify_cfg = manifest.get("verify", {})
    if not isinstance(verify_cfg, dict) or any(k not in VERIFY_PHASES or not isinstance(v, str) or not v.strip()
                                                  for k, v in verify_cfg.items()):
        errors.append(f"'verify' must be an object with commands (text) for {', '.join(VERIFY_PHASES)}.")
    errors += stack_errors(manifest) + docs_errors(manifest)
    errors += agent_model_errors(manifest)
    return errors


def known_packs():
    return sorted(p.stem for p in RULES_LIBRARY.glob("*.md") if p.stem != "common")


def stack_errors(manifest):
    stack = manifest.get("stack", [])
    if not isinstance(stack, list) or not all(isinstance(s, str) for s in stack):
        return ["'stack' must be a list of languages, e.g. [\"python\"]."]
    packs = known_packs()
    if not packs:
        return []  # no rules library (an old install): nothing to validate against
    return [f"stack: no rules for '{s}'. Available: {', '.join(packs)}. If the language is not there, "
            f"remove it from 'stack' and put its conventions in section 7." for s in stack if s not in packs]


def docs_errors(manifest):
    if "docs" not in manifest:
        return []
    docs = docs_entity(manifest)
    if not docs or not isinstance(docs["files"], list) or not all(isinstance(f, str) for f in docs["files"]):
        return ["'docs' must be an object with 'files' (a list of paths) and, optionally, 'codemap'."]
    cm = docs["codemap"]
    if cm is None:
        return []
    if not isinstance(cm, str) or not cm.endswith(".md") or cm.startswith("/") or ".." in Path(cm).parts \
            or cm.startswith((".claude/", ARCHIVE_DIR)) or cm == "architecture.md":
        return [f"docs.codemap must be a .md path relative to the repo, outside .claude/ and {ARCHIVE_DIR}: {cm}"]
    owners = [e["id"] for e in entities(manifest).values() if e["id"] != "DOCS" and any(owns(f, cm) for f in e["files"])]
    return [f"docs.codemap ({cm}) is generated by agentic.py: it cannot belong to {', '.join(owners)}."] if owners else []


FRONTMATTER_MODEL_RE = re.compile(r"\A---\s*\n(.*?)\n---", re.S)


def frontmatter_model(path):
    fm = FRONTMATTER_MODEL_RE.match(path.read_text(encoding="utf-8"))
    if not fm:
        return None
    m = re.search(r"^model:\s*(\S+)\s*$", fm.group(1), re.M)
    return m.group(1).strip("\"'") if m else None


def agent_model_errors(manifest):
    """Orchestrated agents on the latest Sonnet and the architect on the latest Opus, declared by alias."""
    errors = []
    names = {"implementer", "qa", "test-author", "test-auditor", "reviewer", "gatekeeper", "verifier"} | {wp.get("agent", "implementer") for wp in wps(manifest).values()}
    if docs_entity(manifest):
        names.add("doc-updater")
    for name in sorted(names):
        f = ROOT / ".claude" / "agents" / f"{name}.md"
        if not f.exists():
            errors.append(f"Agent '{name}': {f.relative_to(ROOT)} does not exist.")
            continue
        model = frontmatter_model(f)
        expected = AGENT_MODEL_OVERRIDES.get(name, WORKER_MODEL)
        if model != expected:
            errors.append(f"Agent '{name}': its frontmatter must say 'model: {expected}' "
                          f"(the latest {expected.capitalize()}), not '{model}'.")
    plan_reviewer = ROOT / ".claude" / "agents" / "plan-reviewer.md"
    if plan_reviewer.exists() and frontmatter_model(plan_reviewer) != ARCHITECT_MODEL:
        errors.append(f"Agent 'plan-reviewer': its frontmatter must say 'model: {ARCHITECT_MODEL}' (the latest Opus), "
                      f"not '{frontmatter_model(plan_reviewer)}'.")
    for command, expected, label in (("architect", ARCHITECT_MODEL, "the latest Opus"),
                                     ("implement", WORKER_MODEL, "the latest Sonnet")):
        f = ROOT / ".claude" / "commands" / f"{command}.md"
        if f.exists() and frontmatter_model(f) != expected:
            errors.append(f"/{command}: its frontmatter must say 'model: {expected}' ({label}), "
                          f"not '{frontmatter_model(f)}'.")
    return errors


def mirror_errors(wp):
    """References to existing code ("mirror") must really exist: catches invented paths or lines."""
    errors = []
    mirror = wp.get("mirror", [])
    if not isinstance(mirror, list):
        return [f"{wp['id']}: 'mirror' must be a list of 'path[:start[-end]]'."]
    for ref in mirror:
        m = MIRROR_RE.match(str(ref))
        if not m:
            errors.append(f"{wp['id']}: malformed mirror reference: {ref}")
            continue
        rel, start, end = m.group(1), m.group(2), m.group(3)
        f = ROOT / rel
        if ".." in Path(rel).parts or rel.startswith("/") or not f.is_file():
            errors.append(f"{wp['id']}: mirror points to a file that does not exist: {rel}")
            continue
        if start:
            n = sum(1 for _ in f.open(encoding="utf-8", errors="replace"))
            a, b = int(start), int(end or start)
            if not (1 <= a <= b <= n):
                errors.append(f"{wp['id']}: mirror {ref} out of range ({rel} has {n} lines).")
    return errors


def require_valid():
    try:
        manifest = load_manifest()
    except ManifestError as e:
        die(str(e))
    errors = validate(manifest)
    if errors:
        die("Invalid manifest:\n  " + "\n  ".join(errors))
    return manifest


# ---------- snapshots and stage checks ----------

def tracked_files():
    out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "-z"],
                         cwd=ROOT, capture_output=True, check=True).stdout
    return [p for p in out.decode().split("\0") if p and not p.startswith(".claude/state/")]


def digest():
    result = {}
    for rel in tracked_files():
        p = ROOT / rel
        if p.is_file():
            result[rel] = hashlib.sha1(p.read_bytes()).hexdigest()
    return result


STAGE_LABELS = {"qa": qa_entity, "acceptance": at_entity, "review": review_entity, "docs": docs_entity}


def stage_owners(manifest, label):
    if label in STAGE_LABELS:
        return [e for e in [STAGE_LABELS[label](manifest)] if e]
    return [wp for wp in wps(manifest).values() if wp["wave"] == int(label)]


def git_tree():
    """Hash of a tree holding the whole working state (tracked, modified and new files), built in a temporary index so
    that the real one is left alone. None if this is not a git repo."""
    try:
        git_index = subprocess.run(["git", "rev-parse", "--git-path", "index"], cwd=ROOT, capture_output=True,
                                   text=True, encoding="utf-8", errors="replace", check=True).stdout.strip()
        tmp = STATE / "tree.idx"
        tmp.unlink(missing_ok=True)
        if (ROOT / git_index).exists():
            shutil.copyfile(ROOT / git_index, tmp)
        env = dict(os.environ, GIT_INDEX_FILE=str(tmp))
        subprocess.run(["git", "add", "-A"], cwd=ROOT, env=env, capture_output=True, check=True)
        tree = subprocess.run(["git", "write-tree"], cwd=ROOT, env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", check=True).stdout.strip()
        tmp.unlink(missing_ok=True)
        return tree
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None


def snapshot(label):
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / f"snapshot-{label}.json").write_text(json.dumps(digest()), encoding="utf-8")
    tree = git_tree()
    if tree:  # what a fix attempt changed is later diffed against this
        (STATE / f"tree-{label}.txt").write_text(tree, encoding="utf-8")
    (STATE / f"gitstate-{label}.json").write_text(json.dumps(git_state()), encoding="utf-8")
    locked_json(CLAIMS, lambda c: {})  # every stage starts with no claims (emptied: Windows cannot delete an open file)
    locked_json(HOOK_SEEN, lambda s: dict(s, guarded_ts=None))  # and with no agent of the flow seen by the hook
    try:  # nor waits: an agent of an earlier stage that was stopped never sent the event that ends its wait
        sibling("waiting").clear(STATE)
    except Exception:  # noqa: BLE001 - a missing or broken waiting.py must not stop a stage from opening
        pass
    ledger_stage(label, "in progress")
    print(f"Snapshot of stage {label} saved.")


def git_state():
    """What no agent may change during a stage, whatever tool it uses: the commit and the refs, git's own configuration,
    hooks and excludes, and the baseline that verification compares against."""
    def git(*args):
        r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True)
        return r.stdout if r.returncode == 0 else b""

    def file_hash(path):
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None

    common = git("rev-parse", "--git-common-dir").decode().strip()
    git_dir = (ROOT / common) if common else None
    hooks = sorted(p for p in (git_dir / "hooks").glob("*") if p.is_file()) if git_dir and (git_dir / "hooks").is_dir() else []
    return {"HEAD": git("rev-parse", "-q", "--verify", "HEAD").decode().strip() or None,
            "refs": hashlib.sha256(git("for-each-ref")).hexdigest(),
            "config": file_hash(git_dir / "config") if git_dir else None,
            "hooks": hashlib.sha256(b"".join(p.name.encode() + p.read_bytes() for p in hooks)).hexdigest(),
            "info/exclude": file_hash(git_dir / "info" / "exclude") if git_dir else None,
            "baseline": file_hash(BASELINE)}


def index_files():
    """The paths git tracks (the index): never treated as artefacts, whatever folder they are in."""
    r = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True)
    return set(r.stdout.decode().split("\0")) - {""} if r.returncode == 0 else set()


def content_problems(manifest, label, changes):
    """Secrets, unconditional test skips and relaxed quality configuration in what a stage changed, compared with the
    content each file had when the stage opened. It catches what the hook cannot see (a write through Bash).
    changes: [(path, owner entity)]."""
    tree_file = STATE / f"tree-{label}.txt"
    if not tree_file.exists():
        return []
    tree = tree_file.read_text(encoding="utf-8").strip()
    problems = []
    for path, ent in changes:
        r = subprocess.run(["git", "cat-file", "-p", f"{tree}:{path}"], cwd=ROOT, capture_output=True)
        existed = r.returncode == 0
        old = r.stdout.decode("utf-8", "replace") if existed else ""
        try:
            new = (ROOT / path).read_text(encoding="utf-8", errors="replace") if (ROOT / path).is_file() else ""
        except OSError:
            new = ""
        for rx, what in SECRET_RES:
            if len(rx.findall(new)) > len(rx.findall(old)):
                problems.append(f"{path}: looks like {what}")
        if TEST_PATH_RE.search(path) and len(SKIP_MARKER_RE.findall(new)) > len(SKIP_MARKER_RE.findall(old)):
            problems.append(f"{path}: a test was disabled (skip, xfail, only…)")
        if existed and PROTECTED_CONFIG_RE.match(Path(path).name) and not ent.get("config_edit"):
            problems.append(f"{path}: existing quality configuration changed without config_edit")
    return problems


def check_stage(manifest, label):
    snap_file = STATE / f"snapshot-{label}.json"
    if not snap_file.exists():
        die(f"There is no snapshot of stage {label}. Run `snapshot {label}` before launching it.")
    before = json.loads(snap_file.read_text(encoding="utf-8"))
    after = digest()
    patterns = DEFAULT_IGNORE + manifest.get("ignore", [])
    tracked = index_files()
    changed = sorted(p for p in before.keys() | after.keys()
                     if before.get(p) != after.get(p) and (p in tracked or not ignored(p, patterns)))
    owners = stage_owners(manifest, label)
    report = {o["id"]: [] for o in owners}
    violations, owned = [], []
    nobodys = ("architecture.md", codemap_path(manifest))
    maintainer = maintainer_mode(manifest)
    for path in changed:
        owner = next((o for o in owners if any(owns(f, path) for f in o["files"])), None)
        # Nobody writes the plan, the flow, the decision archive or the code map, even inside a zone that covers them.
        if owner and (path in nobodys or path.startswith(ARCHIVE_DIR) or flow_path_refused(path, maintainer)):
            owner = None
        if owner:
            report[owner["id"]].append(path)
            owned.append((path, owner))
        else:
            violations.append(path)
    for oid, files in report.items():
        print(f"{oid}: {len(files)} file(s) changed" + (": " + ", ".join(files) if files else ""))
    ok = True
    if violations:
        print("OUTSIDE OWNERSHIP (no owner of this stage owns these files):")
        for v in violations:
            print(f"  {v}")
        ok = False
    git_file = STATE / f"gitstate-{label}.json"
    if git_file.exists():
        was, now = json.loads(git_file.read_text(encoding="utf-8")), git_state()
        moved = [k for k in was if was[k] != now.get(k)]
        if moved:
            print(f"git state changed during the stage: {', '.join(moved)} (no agent commits or touches .git or the baseline)")
            ok = False
    if changed and not stage_guarded(label):
        print(f"GUARDRAIL HOOK SAW NO FLOW AGENT: {len(changed)} file(s) changed in stage {label} and no hook call came "
              "from an agent of the flow (agent_type or AGENTIC_WP). The ownership check above is the only guard that "
              "applied; if this repeats in every stage, run /implement fallback and report the agent_type the hook "
              f"recorded in {HOOK_SEEN.relative_to(ROOT).as_posix()}.")
        ok = False
    problems = content_problems(manifest, label, owned)
    if problems:
        print("CONTENT NOT ALLOWED:")
        for problem in problems:
            print(f"  {problem}")
        ok = False
    if ok:
        print(f"Stage {label}: all changes are within their owners.")
    return ok


# ---------- launchers ----------

def child_env(cfg, **extra):
    env = dict(os.environ, **extra)
    if cfg.get("auth", "subscription") == "subscription":
        for var in API_AUTH_VARS:
            env.pop(var, None)
    return env


def load_spec():
    if not PIPELINE_SPEC.exists():
        die(f"{PIPELINE_SPEC.relative_to(ROOT)} does not exist: reinstall the flow (/agentic-flow:init).")
    return json.loads(PIPELINE_SPEC.read_text(encoding="utf-8"))


def usage_of(path):
    """Token usage and cost of a `claude -p --output-format json` output; zeros/None if there is none."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        data = None
    data = data if isinstance(data, dict) else {}
    u = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    return {"input": (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0),
            "cache_read": u.get("cache_read_input_tokens") or 0, "output": u.get("output_tokens") or 0,
            "cost_usd": data.get("total_cost_usd"), "turns": data.get("num_turns") or 0}


def run_agent(manifest, cfg, spec, agent_type, ident, label, prompt, out_path, final=False):
    """One `claude -p` run with the agent's JSON schema. Returns {"report", "status", "usage"} and never raises.
    `final`: also write the end state in the ledger (the one-off commands; the pipeline's close-stage does it there)."""
    schema = spec["schemas"].get(agent_type, spec["schemas"]["implementer"])
    # The prompt goes on stdin: on Windows a `claude.cmd` would pass it through cmd.exe, which cuts newlines.
    cmd = compat.resolve(shlex.split(os.environ.get("AGENTIC_CLAUDE", "claude"))) + [
        "-p", "--agent", agent_type, "--model", WORKER_MODEL, "--output-format", "json",
        "--json-schema", json.dumps(schema, separators=(",", ":")), "--permission-mode", "acceptEdits",
        "--strict-mcp-config", "--allowedTools", *pipeline_tools(manifest, agent_type),
        "Bash(python3 .claude/bin/agentic.py claim:*)"]
    env = child_env(cfg, AGENTIC_WP=ident or "REVIEW", CLAUDE_PROJECT_DIR=str(ROOT))
    tracked = agent_type not in READ_ONLY_AGENTS and ident in entities(manifest)
    report, status = None, "NO REPORT"
    try:
        with open(out_path, "w", encoding="utf-8") as out, open(out_path.with_suffix(".log"), "w", encoding="utf-8") as err:
            proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=out, stderr=err, stdin=subprocess.PIPE,
                                    **compat.group_kwargs())  # its own group: stop and timeout kill it whole
            if tracked:
                ledger_update(ident, state="running", stage=stage_of(ident), mode="fallback", pid=proc.pid,
                              started=now_hm(), note="")
            try:
                proc.communicate(input=prompt.encode("utf-8"), timeout=cfg["timeout_s"])
                if tracked and ledger_read().get(ident, {}).get("state") == "stopped":
                    status = "STOPPED"
                else:
                    report = structured_report(out_path, schema)
                    status = report.get("status", "DONE") if report else "NO REPORT"
            except subprocess.TimeoutExpired:
                compat.kill_tree(proc.pid)
                proc.wait()
                status = f"TIMEOUT ({cfg['timeout_s']}s)"
    except OSError as e:
        status = f"ERROR ({e})"
    usage = usage_of(out_path)
    with open(USAGE, "a", encoding="utf-8") as f, compat.locked(f):  # agents finish in parallel threads
        f.write(json.dumps({"id": ident or agent_type, "agent": agent_type, "label": label, **usage}) + "\n")
    if final and tracked:
        ledger_update(ident, state=status_to_state(status), pid=None, note=status)
    print(f"{label}: {status}", flush=True)
    return {"report": report, "status": status, "usage": usage}


def write_briefs(manifest):
    """The slice of architecture.md each WP (and the test-author, "AT") needs, in .claude/state/briefs/."""
    brief, text = brief_module(), ARCH.read_text(encoding="utf-8")
    (STATE / "briefs").mkdir(parents=True, exist_ok=True)
    info = {}
    for ident in [*wps(manifest), *(["AT"] if manifest.get("acceptance") else [])]:
        body, sliced = brief.slice_architecture(text, ident, manifest)
        (STATE / "briefs" / f"{ident}.md").write_text(body, encoding="utf-8")
        info[ident] = {"path": f".claude/state/briefs/{ident}.md", "sliced": sliced, "words": len(body.split())}
    return info


def run_stage(manifest, label, jobs, expected):
    """A one-off stage: snapshot, the agents of `jobs` ([(id, agent type, prompt)]) in parallel, ownership check."""
    cfg, spec = load_config(), load_spec()
    (STATE / "runs").mkdir(parents=True, exist_ok=True)
    snapshot(label)
    runs = pipeline_module().threaded([
        (lambda j=j: run_agent(manifest, cfg, spec, j[1], j[0], j[0], j[2], STATE / "runs" / f"{j[0]}.json", final=True))
        for j in jobs])
    finish(manifest, label, {j[0]: r["status"] for j, r in zip(jobs, runs)}, expected)


def render_prompt(manifest, name, ident=None, **values):
    brief = f".claude/state/briefs/{ident}.md" if ident else ""
    return pipeline_module().render(load_spec()["prompts"][name], wp=ident, brief=brief, rules=rules_hint(manifest), **values)


REPORT_FILE_RE = re.compile(r"^(REVIEW|QA)-(\d+)\.json$")
REPORT_KEYS = {"REVIEW": "findings", "QA": "defects"}
# Ties in modification time: the one-off report, then run-pipeline's per-agent file, then the run record.
SOURCE_RANK = {"one-off": 2, "per-agent": 1, "record": 0}


def _report_items(report, kind, shared):
    """The items of a review or QA report. In run-pipeline's per-agent files (shared: the test-auditor and the
    verifier also write REVIEW-<n>) only a report with the reviewer's or QA's shape counts: their schemas require
    needs_validation, the others' do not. REVIEW.json and QA.json are only written by run-review and run-qa."""
    if not isinstance(report, dict) or (shared and "needs_validation" not in report):
        return None
    items = report.get(REPORT_KEYS[kind])
    return items if isinstance(items, list) else None


def _record_items(record, kind, include_all):
    """The items of the run record for one kind, or None when that part is missing (a run that stopped early)."""
    part = record.get("review" if kind == "REVIEW" else "qa") if isinstance(record, dict) else None
    if not isinstance(part, dict):
        return None
    if kind == "QA":
        items = part.get("defects")
        return items if isinstance(items, list) else None
    # The verifier's refuted findings are in "refuted" and are never read.
    items = list(part.get("confirmed") or [])
    return items + list(part.get("advisory") or []) if include_all else items


def latest_reports(include_all=False):
    """{"REVIEW": (items, origin), "QA": (items, origin)}: for each kind, the newest source by modification time among
    the run record (workflow-result.json), run-pipeline's per-agent files (REVIEW-<n>.json, QA-<n>.json) and the
    one-off reports (REVIEW.json, QA.json). A kind with no source is absent; an unreadable file is skipped."""
    runs = STATE / "runs"
    candidates = {"REVIEW": [], "QA": []}

    def stamp(path):
        try:
            return path.stat().st_mtime_ns
        except OSError:
            return None

    record_path = runs / "workflow-result.json"
    if record_path.is_file() and stamp(record_path) is not None:
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            record = None
        for kind in candidates:
            items = _record_items(record, kind, include_all)
            if items is not None:
                candidates[kind].append((stamp(record_path), SOURCE_RANK["record"], items, record_path.name))
    for path in sorted(runs.glob("*.json")) if runs.is_dir() else []:
        m = REPORT_FILE_RE.match(path.name)
        kind = m.group(1) if m else path.stem if path.stem in candidates else None
        if kind is None:
            continue
        items = _report_items(structured_report(path, {}), kind, shared=bool(m))
        when = stamp(path)
        if items is not None and when is not None:
            candidates[kind].append((when, SOURCE_RANK["per-agent" if m else "one-off"], items, path.name))
    return {kind: (best[2], best[3]) for kind, found in candidates.items() if found
            for best in [max(found, key=lambda c: (c[0], c[1]))]}


def fix_defects(spec, wp_id, include_all=False, reports=None):
    """The implementation defects review and QA attribute to a WP, tagged with who found them. Blocking severities
    only, unless include_all."""
    blocking, found = spec["constants"]["blocking"], []
    reports = latest_reports(include_all) if reports is None else reports
    for kind, tag in (("REVIEW", "reviewer"), ("QA", "qa")):
        items = reports.get(kind, ([], None))[0]
        found += [{"source": tag, **d} for d in items
                  if isinstance(d, dict) and d.get("wp") == wp_id and (include_all or d.get("severity") in blocking)
                  and d.get("kind", "implementation") == "implementation"]
    return found


def run_wave(manifest, wave, only=None, fix=False, include_all=False):
    cfg, spec = load_config(), load_spec()
    wave_wps = sorted(stage_owners(manifest, wave), key=lambda w: w["id"])
    if only:
        unknown = set(only) - {wp["id"] for wp in wave_wps}
        if unknown:
            die(f"Not in wave {wave}: {', '.join(sorted(unknown))}")
        wave_wps = [wp for wp in wave_wps if wp["id"] in only]
    if not wave_wps:
        die(f"There are no WPs in wave {wave}.")
    reports = latest_reports(include_all) if fix else {}
    if fix and not reports:
        die("--fix needs a review or QA report: .claude/state/runs/workflow-result.json, REVIEW-<n>.json / QA-<n>.json "
            "or REVIEW.json / QA.json. Run run-pipeline, run-review or run-qa first.")
    defects = {wp["id"]: fix_defects(spec, wp["id"], include_all, reports) for wp in wave_wps} if fix else {}
    origins = ", ".join(sorted({origin for _, origin in reports.values()}))
    for wp_id in [w for w, d in defects.items() if not d]:
        die(f"{wp_id}: no {'' if include_all else 'blocking '}defect from review or QA is attributed to it. "
            f"Read from {origins}.")
    if fix:
        cap = int(cfg.get("max_fix_cycles", 2))
        ledger = ledger_read()
        over = [wp["id"] for wp in wave_wps if ledger.get(wp["id"], {}).get("fix_count", 0) >= cap]
        if over:
            die(f"{', '.join(over)} has used up its {cap} --fix cycles. This is not an implementation failure that yields "
                "to repetition: decide yourself, or revisit the contract with /architect.", code=4)
        for wp in wave_wps:
            ledger_update(wp["id"], fix_count=ledger.get(wp["id"], {}).get("fix_count", 0) + 1)
    write_briefs(manifest)
    pipeline = pipeline_module()
    run_stage(manifest, str(wave), [
        (wp["id"], wp.get("agent", "implementer"),
         render_prompt(manifest, "fix", wp["id"], defects=pipeline.compact(defects[wp["id"]]), retry="") if fix
         else render_prompt(manifest, "implement", wp["id"])) for wp in wave_wps], "DONE")


def run_acceptance(manifest):
    if not at_entity(manifest)["command"]:
        die("The manifest has no 'acceptance' section: there is no wave 0 to launch.")
    write_briefs(manifest)
    run_stage(manifest, "acceptance", [("AT", "test-author", render_prompt(manifest, "test_author", "AT"))], "DONE")


def run_review(manifest):
    run_stage(manifest, "review", [("REVIEW", "reviewer", render_prompt(manifest, "reviewer"))], "PASS")


def run_qa(manifest):
    run_stage(manifest, "qa", [("QA", "qa", render_prompt(manifest, "qa"))], "PASS")


def run_docs(manifest):
    if not docs_entity(manifest):
        die("The manifest has no 'docs' section: there is no living documentation to update.")
    run_stage(manifest, "docs", [("DOCS", "doc-updater", render_prompt(manifest, "doc_updater"))], "DONE")


# ---------- language rules ----------

def source_files(root=None):
    """Repo files for detecting the stack and building the code map: git's if there is a repo, without dependencies or builds."""
    root = root or ROOT
    try:
        out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=root,
                             capture_output=True, check=True).stdout.decode()
        files = [p for p in out.split("\0") if p]
    except (subprocess.CalledProcessError, FileNotFoundError):
        files = [p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()]
    return sorted(f for f in files if not set(f.split("/")[:-1]) & SKIP_DIRS and (root / f).is_file())


def detect_stack():
    """Languages with signals in the repo: {language: {"markers": [...], "files": N}}."""
    found = {}
    for rel in source_files():
        name = rel.rsplit("/", 1)[-1]
        for pack, (markers, exts) in STACK_SIGNALS.items():
            is_marker = name in markers or (pack == "python" and name.startswith("requirements") and name.endswith(".txt"))
            if is_marker or name.endswith(exts):
                entry = found.setdefault(pack, {"markers": [], "files": 0})
                if is_marker:
                    entry["markers"].append(rel)
                else:
                    entry["files"] += 1
    return found


def declared_stack():
    try:
        stack = load_manifest().get("stack", [])
    except ManifestError:
        return []
    return [s for s in stack if isinstance(s, str)] if isinstance(stack, list) else []


def rule_files(manifest):
    """Rules the agents must follow: the common ones and those of each language of the stack (or, if none was
    declared, the detected ones), as long as they are installed."""
    packs = manifest.get("stack") or sorted(detect_stack())
    names = ["common"] + [p for p in packs if isinstance(p, str)]
    return [f".claude/rules/agentic/{n}.md" for n in names if (RULES_DIR / f"{n}.md").exists()]


def rules_hint(manifest):
    files = rule_files(manifest)
    return f" Rules you must follow (language and common): {', '.join(files)}." if files else ""


def rules_sync():
    """Copies the library to the directory Claude Code loads: common + declared or detected languages."""
    if not (RULES_LIBRARY / "common.md").exists():
        die(f"{RULES_LIBRARY.relative_to(ROOT)}/common.md does not exist: install the full flow (plugin or template).")
    declared, detected = declared_stack(), sorted(detect_stack())
    # Stack entries are names, never paths: "../../x" must not copy a file out of the library or write outside it.
    wanted = {"common"} | {p for p in set(declared) | set(detected)
                           if re.fullmatch(r"[a-z][a-z0-9+-]*", str(p)) and (RULES_LIBRARY / f"{p}.md").exists()}
    RULES_DIR.mkdir(parents=True, exist_ok=True)
    installed, removed = [], []
    for name in sorted(wanted):
        src = (RULES_LIBRARY / f"{name}.md").read_text(encoding="utf-8")
        dst = RULES_DIR / f"{name}.md"
        if not dst.exists() or dst.read_text(encoding="utf-8") != src:
            dst.write_text(src, encoding="utf-8")
            installed.append(name)
    for old in sorted(RULES_DIR.glob("*.md")):
        if old.stem not in wanted:
            old.unlink()
            removed.append(old.stem)
    print(json.dumps({"rules": sorted(wanted), "updated": installed, "removed": removed, "declared": declared,
                      "detected": detected, "missing": sorted(set(declared) - wanted)}, ensure_ascii=False))


# ---------- living documentation ----------

def symbol_re(name):
    return next((rx for exts, rx in SYMBOL_RES.items() if name.endswith(exts)), None)


def codemap(manifest, target=None):
    """Deterministic code map: code files by folder, with lines and public symbols. No date, so it only changes
    when the code does."""
    rel_out = target or codemap_path(manifest) or DEFAULT_CODEMAP
    out = ROOT / rel_out
    rows, langs = {}, set()
    for rel in source_files():
        rx = symbol_re(rel)
        if rx is None or rel == rel_out:
            continue
        text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        symbols = [m.group(1) for line in text.splitlines() if (m := rx.match(line))]
        langs |= {p for p, (_, exts) in STACK_SIGNALS.items() if rel.endswith(exts)}
        folder = rel.rsplit("/", 1)[0] if "/" in rel else "."
        rows.setdefault(folder, []).append((rel, len(text.splitlines()), symbols))
    total = sum(len(v) for v in rows.values())
    lines = ["# Code map", "",
             "> Generated by `python3 .claude/bin/agentic.py codemap`. Do not edit it by hand: the flow regenerates it when "
             "a cycle closes.", "",
             f"{total} code files in {len(rows)} folders. Languages: {', '.join(sorted(langs)) or 'none'}.", ""]
    for folder in sorted(rows):
        lines += [f"## `{folder}/`" if folder != "." else "## Root", "", "| File | Lines | Public symbols |",
                  "|---|---|---|"]
        for rel, n, symbols in rows[folder]:
            shown = ", ".join(f"`{s}`" for s in symbols[:12]) + (f" and {len(symbols) - 12} more" if len(symbols) > 12 else "")
            lines.append(f"| `{rel.rsplit('/', 1)[-1]}` | {n} | {shown or '—'} |")
        lines.append("")
    content = "\n".join(lines)
    changed = not out.exists() or out.read_text(encoding="utf-8") != content
    if changed:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content, encoding="utf-8")
    print(json.dumps({"path": rel_out, "files": total, "folders": len(rows), "changed": changed}, ensure_ascii=False))


# ---------- callers: what the reviewer must read beyond the diff ----------

CALLERS_PER_SYMBOL = 10
CALLERS_MAX = 80
CALLER_TEXT_MAX = 160
CALLER_RE_TEMPLATE = r"(?<![\w$])%s(?![\w$])"  # the symbol as a whole word; $ for JS identifiers
EMPTY_CALLERS = {"changed": [], "symbols": {}, "callers": [], "truncated": False}


def _git_paths(args):
    """The NUL-separated paths a git command prints, or None when git or the revision is not there."""
    try:
        out = subprocess.run(["git", *args, "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None
    return [p for p in out.decode("utf-8", errors="replace").split("\0") if p]


def changed_code_files():
    """Code files modified since HEAD or untracked, filtered like source_files(): sorted, relative, existing."""
    modified = _git_paths(["diff", "--name-only", "--relative", "HEAD"]) or []
    untracked = _git_paths(["ls-files", "--others", "--exclude-standard"]) or []
    found = set()
    for rel in modified + untracked:
        rel = rel.replace("\\", "/")
        if set(rel.split("/")[:-1]) & SKIP_DIRS or rel.startswith(".claude/state/") or symbol_re(rel) is None:
            continue
        if (ROOT / rel).is_file():
            found.add(rel)
    return sorted(found)


def file_symbols(rel):
    """The public symbols of one file, as the code map sees them, in order and without repeats."""
    rx = symbol_re(rel)
    if rx is None:
        return []
    try:
        text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    seen = []
    for line in text.split("\n"):
        m = rx.match(line.rstrip("\r"))
        if m and m.group(1) not in seen:
            seen.append(m.group(1))
    return seen


def find_callers(changed, symbols):
    """The lines of tracked code files outside `changed` that use a symbol as a whole word. symbols: {file: [name]}.
    Symbol-major (symbols in file order), then file order, then line order; capped per symbol and in total.
    Returns (callers, truncated)."""
    names = []
    defined_in = {}
    for rel in sorted(symbols):
        for name in symbols[rel]:
            if name not in defined_in:
                defined_in[name] = rel
                names.append(name)
    if not names:
        return [], False
    changed_set = set(changed)
    whole = re.compile(CALLER_RE_TEMPLATE % ("(?:" + "|".join(re.escape(n) for n in names) + ")"))
    per_name = {n: re.compile(CALLER_RE_TEMPLATE % re.escape(n)) for n in names}
    hits = {n: [] for n in names}
    for rel in source_files():
        if rel in changed_set or symbol_re(rel) is None or rel.startswith(".claude/state/"):
            continue
        try:
            text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not any(n in text for n in names):
            continue
        for number, line in enumerate(text.split("\n"), 1):
            line = line.rstrip("\r")
            if not whole.search(line):
                continue
            for n in names:
                if per_name[n].search(line):
                    hits[n].append({"symbol": n, "defined_in": defined_in[n], "file": rel, "line": number,
                                    "text": line.strip()[:CALLER_TEXT_MAX]})
    truncated = False
    out = []
    for n in names:
        if len(hits[n]) > CALLERS_PER_SYMBOL:
            truncated = True
        out.extend(hits[n][:CALLERS_PER_SYMBOL])
    if len(out) > CALLERS_MAX:
        truncated = True
        out = out[:CALLERS_MAX]
    return out, truncated


def callers():
    """One JSON line for the engines: the changed code files, their public symbols and the callers outside the diff.
    Exits 0 whatever happens: an instrument never stops the review."""
    try:
        changed = changed_code_files()
        symbols = {rel: syms for rel in changed for syms in [file_symbols(rel)] if syms}
        found, truncated = find_callers(changed, symbols)
        print(json.dumps({"changed": changed, "symbols": symbols, "callers": found, "truncated": truncated},
                         ensure_ascii=False))
    except Exception as e:  # noqa: BLE001 - the step must not stop the review
        print(f"[agentic] callers skipped: {type(e).__name__}: {e}", file=sys.stderr)
        print(json.dumps(dict(EMPTY_CALLERS, error=f"{type(e).__name__}: {e}"), ensure_ascii=False))
    sys.exit(0)


def heading_anchors(text):
    """The anchors GitHub gives the headings: a repeated heading gets -1, -2..."""
    anchors, seen = set(), {}
    for h in re.findall(r"^#+ (.+)$", text, re.M):
        slug = re.sub(r"[^\w\s-]", "", h.strip().lower(), flags=re.U).replace(" ", "-")
        anchors.add(f"{slug}-{seen[slug]}" if slug in seen else slug)
        seen[slug] = seen.get(slug, 0) + 1
    return anchors


def docs_files(manifest):
    docs = docs_entity(manifest) if manifest else None
    scope = docs["files"] if docs else ["README.md", "docs/"]
    return [rel for rel in source_files() if rel.endswith(".md") and any(owns(f, rel) for f in scope)
            and not rel.startswith(ARCHIVE_DIR)]


LINK_RE = re.compile(r"\]\(([^)\s]+)\)")
PATH_RE = re.compile(r"`([\w.-]+/[\w./-]*)`")


def docs_check(manifest):
    """Relative links, anchors and repo paths cited in backticks: everything must exist."""
    top_dirs = {p.name for p in ROOT.iterdir() if p.is_dir()}
    broken, files = [], docs_files(manifest)
    for rel in files:
        path = ROOT / rel
        fenced = False
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if line.lstrip().startswith("```"):
                fenced = not fenced
                continue
            if fenced:
                continue
            for link in LINK_RE.findall(line):
                if link.startswith(("http://", "https://", "mailto:")):
                    continue
                target_rel, _, frag = link.partition("#")
                target_rel = urllib.parse.unquote(target_rel)
                # a link starting with / is relative to the repo root, as GitHub renders it
                target = (ROOT / target_rel.lstrip("/") if target_rel.startswith("/") else path.parent / target_rel) \
                    if target_rel else path
                if not target.exists():
                    broken.append({"file": rel, "line": n, "target": link, "kind": "link"})
                elif frag and target.suffix == ".md" and frag not in heading_anchors(target.read_text(encoding="utf-8")):
                    broken.append({"file": rel, "line": n, "target": link, "kind": "anchor"})
            for cited in PATH_RE.findall(line):
                first = cited.split("/", 1)[0]
                if first in top_dirs and first not in SKIP_DIRS and not (ROOT / cited).exists():
                    broken.append({"file": rel, "line": n, "target": cited, "kind": "path"})
    ok = not broken
    ledger_stage("docs-check", "OK" if ok else f"REVIEW: {len(broken)} broken reference(s)")
    print(json.dumps({"ok": ok, "checked": len(files), "broken": broken}, ensure_ascii=False))
    sys.exit(0 if ok else 3)


# ---------- installing in another repo (plugin) ----------

def flow_version():
    manifest = SOURCE_ROOT / ".claude-plugin" / "plugin.json"
    try:
        return json.loads(manifest.read_text(encoding="utf-8")).get("version", "dev")
    except (OSError, json.JSONDecodeError):
        return "dev"


def merge_settings(dst, src):
    """Adds to the project's settings.json the flow's permissions, hook and workflows, removing nothing of theirs."""
    data = json.loads(dst.read_text(encoding="utf-8")) if dst.exists() else {}
    added = []
    perms = data.setdefault("permissions", {})
    for key in ("allow", "deny"):
        current = perms.setdefault(key, [])
        for rule in src.get("permissions", {}).get(key, []):
            if rule not in current:
                current.append(rule)
                added.append(f"{key} {rule}")
    hooks = data.setdefault("hooks", {})
    pre = hooks.setdefault("PreToolUse", [])
    ours = flow_hooks(pre)
    if not ours:
        pre.extend(src["hooks"]["PreToolUse"])
        added.append("hook PreToolUse")
    else:
        # 2.1: the stop gate needs the report tool in the matcher and time to run the acceptance command. The project's
        # matcher is extended, never replaced (it may have been trimmed on purpose).
        source_hook = src["hooks"]["PreToolUse"][0]["hooks"][0]
        for entry, hook_ in ours:
            # 2.5: the hook runs through the launcher, which picks the frozen guard of a maintainer run. Only the
            # command changes; the project's matcher and timeout stay.
            if is_old_hook_command(hook_.get("command", "")) and not is_old_hook_command(source_hook["command"]):
                hook_["command"] = source_hook["command"]
                added.append("hook PreToolUse (hook.py)")
            if "StructuredOutput" not in entry.get("matcher", ""):
                entry["matcher"] = (entry.get("matcher", "").rstrip("|") + "|StructuredOutput").lstrip("|")
                hook_.setdefault("timeout", source_hook.get("timeout", 900))
                added.append("hook PreToolUse (StructuredOutput)")
    # The other events (the waiting notice): each of the flow's entries once, next to the project's own.
    for event, entries in src.get("hooks", {}).items():
        if event == "PreToolUse":
            continue
        present = {h.get("command") for e in hooks.get(event, []) for h in e.get("hooks", [])}
        new = [e for e in entries if not any(h.get("command") in present for h in e.get("hooks", []))]
        if new:
            hooks.setdefault(event, []).extend(new)
            added.append(f"hook {event}")
    # Bash timeouts: only where the project has not set its own value.
    env = data.setdefault("env", {})
    for key, value in src.get("env", {}).items():
        if key not in env:
            env[key] = value
            added.append(f"env {key}")
    if not env:
        data.pop("env")
    if data.get("enableWorkflows") is not True:
        data["enableWorkflows"] = True
        added.append("enableWorkflows")
    return data, added


def install(target, force=False, dry_run=False, allow_dirty=False):
    """Copies the flow to TARGET, or upgrades it. A flow file is replaced only if it is intact: its content is one the
    flow shipped (flow-history.json) or the previous install recorded. Edited ones are kept, with the new version in
    .claude/state/upgrade/, unless --force. It refuses to write over uncommitted changes or a run in progress."""
    upgrade = sibling("upgrade")
    target = target.resolve()
    if target == SOURCE_ROOT:
        die("install: the target is the flow itself. Run it from the repo where you want to install it.")
    if not target.is_dir():
        die(f"install: the folder {target} does not exist")
    record_path = target / INSTALL_RECORD
    record = json.loads(record_path.read_text(encoding="utf-8")) if record_path.exists() else {}
    previous, history = record.get("files", {}), upgrade.load_history(SOURCE_ROOT)

    def shipped(globs):
        return {p.relative_to(SOURCE_ROOT).as_posix() for g in globs for p in SOURCE_ROOT.glob(g) if p.is_file()}

    def intact(rel):
        digest = compat.content_hash(target / rel)
        return digest in history.get(rel, set()) or digest == previous.get(rel)

    # The flow everywhere; the template's leftovers (its tests, CI, plugin files) only where they already are.
    extras = shipped(upgrade.EXTRA_GLOBS)
    sources = sorted(shipped(INSTALL_GLOBS) | upgrade.leftovers(extras, {r for r in extras if (target / r).is_file()}))
    plan = {rel: upgrade.classify(compat.content_hash(target / rel) if (target / rel).is_file() else None,
                                  compat.content_hash(SOURCE_ROOT / rel),
                                  history.get(rel, set()) | {previous.get(rel)}, force) for rel in sources}
    # The record comes from the target repo: only paths the flow ships, inside the target, may be removed.
    flow_globs = INSTALL_GLOBS + upgrade.EXTRA_GLOBS
    removed = sorted(r for r in set(previous) | set(history)
                     if ".." not in Path(r).parts and not Path(r).is_absolute()
                     and any(fnmatch.fnmatch(r, g) for g in flow_globs)
                     and not (SOURCE_ROOT / r).exists() and (target / r).is_file() and intact(r))
    guarded = [r for r in sources if (target / r).exists()] + removed + [
        ".claude/settings.json", ".gitignore", ".claude/agentic.json", INSTALL_RECORD]
    problems = upgrade.guard_problems(target, guarded)
    if not dry_run:
        if "not a git repository" in problems:
            die(f"install: {target} is not a git repository: the flow needs git to undo an upgrade and to check what "
                "each agent changes (git init).")
        blocking = [p for p in problems if not (allow_dirty and p.startswith("uncommitted: "))]
        if blocking:
            print("install: nothing was written:", *blocking, sep="\n  ", file=sys.stderr)
            print("Commit or stash your changes (or use --allow-dirty), and stop any run, then try again.", file=sys.stderr)
            sys.exit(3)
    report = {"new": [], "updated": [], "unchanged": [], "modified by you (left alone)": [], "removed": removed}
    new_record = {}
    for rel in sources:
        kind, src, dst = plan[rel], SOURCE_ROOT / rel, target / rel
        if kind == "kept":
            report["modified by you (left alone)"].append(rel)
            new_record[rel] = previous.get(rel, "")
            dst = target / ".claude" / "state" / "upgrade" / rel  # the new version, beside the user's
        else:
            report[kind].append(rel)
            new_record[rel] = compat.content_hash(src)
        if kind != "unchanged" and not dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(src.read_bytes())
    if not dry_run:
        for rel in removed:
            (target / rel).unlink()
    settings, added = merge_settings(target / ".claude" / "settings.json",
                                     json.loads((SOURCE_ROOT / ".claude" / "settings.json").read_text(encoding="utf-8")))
    gitignore = target / ".gitignore"
    present = gitignore.read_text(encoding="utf-8").splitlines() if gitignore.exists() else []
    missing_ignore = [line for line in INSTALL_GITIGNORE if line not in present]
    config_missing = not (target / ".claude" / "agentic.json").exists()
    if not dry_run:
        (target / ".claude" / "settings.json").write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n",
                                                         encoding="utf-8")
        if missing_ignore:
            prefix = "" if not present or present[-1] == "" else "\n"
            with gitignore.open("a", encoding="utf-8") as fh:
                fh.write(prefix + "\n".join(missing_ignore) + "\n")
        if config_missing:
            (target / ".claude" / "agentic.json").write_text(json.dumps(DEFAULT_CONFIG, indent=2) + "\n", encoding="utf-8")
        record_path.write_text(json.dumps({"version": flow_version(), "files": new_record}, indent=2) + "\n",
                               encoding="utf-8")
    print(f"agentic-flow {record.get('version', 'unknown')} -> {flow_version()} -> {target}"
          + (" (dry run: nothing is written)" if dry_run else ""))
    for kind, files in report.items():
        if files:
            print(f"  {kind}: {len(files)}" + ("".join(f"\n    {f}" for f in files) if kind != "unchanged" else ""))
    print(f"  settings.json: {', '.join(added) if added else 'unchanged'}")
    if missing_ignore:
        print(f"  .gitignore: {', '.join(missing_ignore)}")
    if config_missing:
        print("  .claude/agentic.json: created with the defaults")
    if not dry_run:
        # With this version's agentic.py, pointed at the target: the repo's own may be an old one that was kept.
        r = subprocess.run([sys.executable, str(Path(__file__).resolve()), "rules-sync"], cwd=target,
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=dict(os.environ, CLAUDE_PROJECT_DIR=str(target)))
        if r.returncode == 0:
            print(f"  rules: {', '.join(json.loads(r.stdout)['rules'])}")
        else:
            print(f"  rules: could not be installed ({(r.stderr.strip().splitlines() or ['no detail'])[-1]})")
    if report["modified by you (left alone)"]:
        print("kept files: " + ("run without --dry-run to get their new versions in .claude/state/upgrade/" if dry_run else
              "new versions in .claude/state/upgrade/ (compare with: diff -ru .claude/state/upgrade/.claude .claude)"))
    # Notes are for upgrades: a repo that had no flow at all has nothing to adapt.
    had_flow = bool(record) or bool(removed) or any(kind != "new" for kind in plan.values())
    notes = upgrade.notes_between(record.get("version"), flow_version()) if had_flow else []
    if notes:
        print("upgrade notes:", *notes, sep="\n  - ")
    if dry_run:
        for problem in problems:
            print(f"warning: {problem}")
    elif (target / "architecture.md").exists():
        r = subprocess.run([sys.executable, str(Path(__file__).resolve()), "validate"], cwd=target, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", env=dict(os.environ, CLAUDE_PROJECT_DIR=str(target)))
        errors = [line.strip() for line in r.stderr.splitlines() if line.strip() and line.strip() != "Invalid manifest:"]
        print("validate: OK" if r.returncode == 0 else f"validate: FAILED: {(errors or ['no detail'])[0]}")
    print("RESULT: OK")


def run_command(cmd, timeout=900, scan=None):
    """Runs a manifest command without a shell. Returns (code, last lines of output). With a list in `scan`, the
    last 200 lines are appended to it, for attributing a failure to the packages that own what it mentions."""
    try:
        # Its own process group: on timeout the whole tree is ended (`npm test` would otherwise leave `vitest` running).
        proc = subprocess.Popen(compat.resolve(shlex.split(cmd)), cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                                env=child_env(load_config(), CLAUDE_PROJECT_DIR=str(ROOT)), **compat.group_kwargs())
    except FileNotFoundError:
        return 127, [f"command not found: {cmd}"]
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        compat.kill_tree(proc.pid)
        proc.communicate()
        return 124, [f"the command exceeded {max(timeout // 60, 1)} minute(s)"]
    lines = (out + err).strip().splitlines()
    if scan is not None:
        scan.extend(lines[-200:])
    return proc.returncode, [line[:MAX_TAIL_LINE] for line in lines[-8:]]


def acceptance_check(manifest, expect, quiet=False, scan=None):
    """red: they must fail (wave 0). green: they must pass (after the waves). Returns (ok, verdict)."""
    cmd = at_entity(manifest)["command"]
    if not cmd:
        die("The manifest has no 'acceptance' section.")
    code, tail = run_command(cmd, scan=scan)
    if not quiet:
        print(f"$ {cmd}  ->  exit {code}")
        for line in tail:
            print("  " + line)
    if code in (124, 126, 127):
        ok, verdict = False, "ERROR: the command could not be run; that counts as neither red nor green"
    elif expect == "red":
        ok = code != 0
        verdict = "RED, as it should be before the code" if ok else "ALREADY PASS WITHOUT CODE: the tests prove nothing"
    else:
        ok = code == 0
        verdict = "GREEN" if ok else "STILL FAILING"
    ledger_stage(f"acceptance ({expect})", "OK" if ok else "REVIEW")
    return ok, verdict


def acceptance(manifest, expect):
    ok, verdict = acceptance_check(manifest, expect)
    print(f"ACCEPTANCE: {verdict}")
    print("RESULT:", "OK" if ok else "REVIEW")
    sys.exit(0 if ok else 3)


# ---------- native workflow commands (run by the gatekeeper agent) ----------

SEQ_OUTPUT_CAP = 12_000
SEQ_COMMANDS = {"pipeline-plan", "briefs", "baseline", "open-stage", "close-stage", "verify", "codemap", "docs-check",
                "acceptance", "callers", "hooks-check", "guard-release"}


def cap_output(out):
    """A step's output within SEQ_OUTPUT_CAP. A JSON last line is the verdict the engines parse: it is kept whole and
    only the text before it is cut."""
    if len(out) <= SEQ_OUTPUT_CAP:
        return out
    head, _, last = out.rpartition("\n")
    if last.lstrip().startswith("{"):
        room = max(SEQ_OUTPUT_CAP - len(last), 0)
        return "[truncated]\n" + (head[-room:] + "\n" if room else "") + last
    return "[truncated]\n" + out[-SEQ_OUTPUT_CAP:]


def run_steps(groups, env=None, script=None):
    """Runs each command (an argv list) as its own process, in order, and stops at the first that fails.
    `env`: the steps' environment (default: this process's, with CLAUDE_PROJECT_DIR). `script`: the agentic.py to run
    (default: this file). Returns (ok, [{"cmd", "exit_code", "output"}])."""
    results, ok = [], True
    env = env if env is not None else dict(os.environ, CLAUDE_PROJECT_DIR=str(ROOT))
    script = Path(script) if script else Path(__file__).resolve()
    for g in groups:
        r = subprocess.run([sys.executable, str(script), *g], cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env)
        out = (r.stdout + r.stderr).strip()
        results.append({"cmd": " ".join(g), "exit_code": r.returncode,
                        "output": cap_output(out)})
        if r.returncode != 0:
            ok = False
            break
    return ok, results


def seq(steps):
    """seq CMD ARGS + CMD ARGS ...: runs several of the workflow's commands one after another and stops at the first
    that fails. One call, one JSON line: {"ok", "steps": [{"cmd", "exit_code", "output"}]}. It saves the workflow's
    gatekeeper round trips; each step still runs as its own process, exactly as if it had been called alone."""
    groups, current = [], []
    for token in steps:
        if token == "+":
            groups.append(current)
            current = []
        else:
            current.append(token)
    groups.append(current)
    if any(not g or g[0] not in SEQ_COMMANDS for g in groups):
        die(f"Uso: seq CMD [ARGS] + CMD [ARGS] ...  (commands: {', '.join(sorted(SEQ_COMMANDS))})")
    ok, results = run_steps(groups)
    print(json.dumps({"ok": ok, "steps": results}, ensure_ascii=False))
    sys.exit(0 if ok else next(x["exit_code"] for x in results if x["exit_code"] != 0))


# ---------- the whole pipeline, without a workflow (fallback mode and CI) ----------

PIPELINE_STATE = STATE / "pipeline.json"
# A frozen copy reads the spec frozen with it, so an agent that edits the live spec cannot change the run's prompts.
PIPELINE_SPEC = (HERE.parent if FROZEN else ROOT / ".claude") / "workflows" / "pipeline-spec.json"
READ_ONLY_AGENTS = {"verifier", "test-auditor"}  # no zone of their own: they run as the read-only REVIEW entity


def pipeline_tools(manifest, agent_type):
    """The tools each kind of agent may use (the same sets the one-off run-* commands give them)."""
    bash = manifest.get("allow_bash", [])
    if agent_type in READ_ONLY_AGENTS:
        return ["Read", "Glob", "Grep"]
    if agent_type == "reviewer":
        return REVIEW_TOOLS + bash
    if agent_type == "doc-updater":
        return BASE_TOOLS + ["Bash(git diff:*)", "Bash(git status:*)", "Bash(git log:*)"]
    if agent_type == "test-author":
        return BASE_TOOLS + bash + at_entity(manifest)["allow_bash"]
    if agent_type == "qa":
        return BASE_TOOLS + bash + qa_entity(manifest)["allow_bash"]
    allowed = BASE_TOOLS + bash  # implementers, and the custom agents a WP declares
    cmd = at_entity(manifest)["command"]
    return allowed + [f"Bash({cmd}:*)", f"Bash({cmd})"] if cmd else allowed


FENCED_JSON_RE = re.compile(r"```json[ \t]*\n(.*?)\n```", re.S)


def text_report(text):
    """A report written as text: the last line that is a JSON object, else the last ```json block."""
    report = pipeline_module().lines_json(text)
    if report is not None:
        return report
    blocks = FENCED_JSON_RE.findall(text)
    try:
        return json.loads(blocks[-1]) if blocks else None
    except ValueError:
        return None


def structured_report(path, schema):
    """The structured answer of a `claude -p --output-format json --json-schema` run, or None. The CLI puts it in
    `structured_output`; older output may carry it as JSON text in `result` (one line, or the last ```json block
    after the agent's prose), so all are accepted."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    report = data.get("structured_output")
    if report is None:
        result = data.get("result")
        report = result if isinstance(result, dict) else text_report(result) if isinstance(result, str) else None
    if isinstance(report, str):
        try:
            report = json.loads(report)
        except ValueError:
            report = None
    if not isinstance(report, dict) or any(k not in report for k in schema.get("required", [])):
        return None
    return report


def sibling(name):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    return __import__(name)


def pipeline_module():
    return sibling("pipeline")


def brief_module():
    return sibling("brief")


def run_pipeline(manifest, args):
    """run-pipeline [--start-wave N] [--resume]: the agentic-implement pipeline with one `claude -p` per agent. The
    same stages, prompts, schemas and limits as the native workflow; it only changes who runs the agents."""
    flags = {a for a in args if a == "--resume"}
    rest = [a for a in args if a not in flags]
    start_wave = None
    if rest[:1] == ["--start-wave"] and len(rest) == 2 and rest[1].isdigit():
        start_wave = int(rest[1])
    elif rest:
        die("Uso: run-pipeline [--start-wave N] [--resume]")
    if "--resume" in flags and start_wave is None and PIPELINE_STATE.exists():
        done = json.loads(PIPELINE_STATE.read_text(encoding="utf-8")).get("completed_waves", [])
        start_wave = max(done) + 1 if done else None
    pipeline, spec, cfg = pipeline_module(), load_spec(), load_config()
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / "runs").mkdir(parents=True, exist_ok=True)
    counter = iter(range(1, 10 ** 6))
    completed = []
    seen = len(usage_records())

    # The fallback's own checks never take hooks-check's live branch: launched by /implement from a Bash tool whose
    # hook may be dead, a live check would close the way out. Its agents are `claude -p` processes, which load the
    # project's hooks; the stage check judges them by their AGENTIC_WP heartbeats.
    gate_env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    gate_env["CLAUDE_PROJECT_DIR"] = str(ROOT)

    def gate(cmds, label, agentic=LIVE_CMD):
        script = ROOT / GUARD_DIR / "bin" / "agentic.py" if agentic == GUARD_CMD else None
        ok, results = run_steps([shlex.split(c) for c in cmds], env=gate_env, script=script)
        return {"steps": results}

    def agent(prompt, agent_type, label, ident):
        res = run_agent(manifest, cfg, spec, agent_type, ident, label, prompt,
                        STATE / "runs" / f"{ident or agent_type}-{next(counter)}.json")
        if res["status"] == "STOPPED":
            raise pipeline.Halted()
        return res["report"]

    def wave_done(wave):
        completed.append(wave)
        PIPELINE_STATE.write_text(json.dumps({"completed_waves": sorted(set(completed))}), encoding="utf-8")

    engine = pipeline.Pipeline(spec, gate, agent, log=lambda m: print(m, flush=True), start_wave=start_wave,
                               phase=lambda t: print(f"== {t}", flush=True), wave_done=wave_done)
    result = engine.execute()
    result["usage"] = usage_summary(usage_records()[seen:])
    out = STATE / "runs" / "workflow-result.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"result": result["result"], "failed_stage": result.get("failed_stage"),
                      "detail": result.get("detail"), "usage": result["usage"]["total"],
                      "saved": str(out.relative_to(ROOT))}, ensure_ascii=False))
    print("RESULT:", "OK" if result["result"] == "OK" else "REVIEW")
    sys.exit(0 if result["result"] == "OK" else 3)


def architecture_type():
    m = TYPE_RE.search(ARCH.read_text(encoding="utf-8")) if ARCH.exists() else None
    return m.group(1).lower() if m else None


def pipeline_plan(manifest, keep_guard=False):
    """The plan the engines follow, as one JSON line. A maintainer run freezes the guard first (a resumed one with
    --keep-guard keeps the copy of its first start); any other run releases a guard a crashed run may have left."""
    maintainer = maintainer_mode(manifest)
    if not maintainer:
        release_guard()
    elif not (keep_guard and (ROOT / GUARD_DIR / "active.json").exists()):
        freeze_guard()
    waves = sorted({wp["wave"] for wp in wps(manifest).values()})
    plan = {"waves": waves, "acceptance": bool(at_entity(manifest)["command"]), "type": architecture_type(),
            "verify": sorted(manifest.get("verify", {})), "max_fix_cycles": int(load_config().get("max_fix_cycles", 2)),
            "agents": {wp["id"]: wp.get("agent", "implementer") for wp in wps(manifest).values()},
            "baseline": bool(manifest.get("verify")),
            "docs": bool(docs_entity(manifest)), "codemap": codemap_path(manifest), "rules": rule_files(manifest),
            "wave_of": {wp["id"]: wp["wave"] for wp in wps(manifest).values()},
            "work_packages": {str(w): sorted(wp["id"] for wp in stage_owners(manifest, str(w))) for w in waves},
            "maintainer": maintainer, "agentic": GUARD_CMD if maintainer else LIVE_CMD}
    print(json.dumps(plan))


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


def freeze_guard():
    """Copies the live flow scripts (not the tests) and the pipeline spec into GUARD_DIR and marks it active. A
    maintainer run is then judged by this copy, whatever its agents change in .claude/bin."""
    guard_dir = ROOT / GUARD_DIR
    release_guard()
    for sub in ("bin", "workflows"):
        shutil.rmtree(guard_dir / sub, ignore_errors=True)
    (guard_dir / "bin").mkdir(parents=True)
    (guard_dir / "workflows").mkdir(parents=True)
    files = {}
    sources = [(p, f"bin/{p.name}") for p in sorted((ROOT / ".claude" / "bin").glob("*.py"))
               if not p.name.startswith("test_") and p.name != "testutil.py"]
    sources.append((ROOT / ".claude" / "workflows" / "pipeline-spec.json", "workflows/pipeline-spec.json"))
    for src, rel in sources:
        shutil.copyfile(src, guard_dir / rel)
        files[rel] = hashlib.sha256(src.read_bytes()).hexdigest()
    active = {"root": str(ROOT), "frozen_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "files": files}
    (guard_dir / "active.json").write_text(json.dumps(active, indent=2) + "\n", encoding="utf-8")
    return active


def release_guard():
    """The hook goes back to the live agentic.py. The copies stay until the next freeze replaces them."""
    try:
        (ROOT / GUARD_DIR / "active.json").unlink()
    except FileNotFoundError:
        pass


def stage_label(arg):
    if not (arg.isdigit() or arg in STAGE_LABELS):
        die(f"Unknown stage: {arg}. Use a wave number or {', '.join(STAGE_LABELS)}.")
    return arg


def open_stage(manifest, label):
    owners = sorted(o["id"] for o in stage_owners(manifest, label))
    if owners:
        gate_reset(owners)
        with open(os.devnull, "w", encoding="utf-8") as quiet:  # the snapshot must not pollute the JSON output
            old, sys.stdout = sys.stdout, quiet
            try:
                snapshot(label)
            finally:
                sys.stdout = old
    print(json.dumps({"stage": label, "owners": owners}))
    sys.exit(0 if owners else 3)


def close_stage(manifest, label, items):
    """close-stage L ID=state... [--expect red|green] [--fix]: ownership, states, acceptance and fix cycles."""
    expect = None
    if "--expect" in items:
        i = items.index("--expect")
        expect = items[i + 1] if i + 1 < len(items) else None
        if expect not in ("red", "green"):
            die("close-stage: --expect necesita red o green")
        items = items[:i] + items[i + 2:]
    fix = "--fix" in items
    items = [x for x in items if x != "--fix"]
    known = entities(manifest)
    marks = []
    for item in items:
        jid, _, state = item.partition("=")
        if jid not in known or state not in LEDGER_STATES:
            die(f"close-stage: invalid entry '{item}' (a manifest ID and a state in {', '.join(LEDGER_STATES)})")
        marks.append((jid, state))
    ledger = ledger_read()
    for jid, state in marks:
        extra = {"fix_count": ledger.get(jid, {}).get("fix_count", 0) + 1} if fix else {}
        ledger_update(jid, state=state, stage=stage_of(jid), pid=None, note=state.upper() + (" (fix)" if fix else ""), **extra)
    if fix:
        write_fix_diffs(manifest, label, [(jid, ledger.get(jid, {}).get("fix_count", 0) + 1) for jid, _ in marks])
    # In the judge stages a fail or a blocker is a verdict, already in the ledger: only a write outside the zone stops.
    judged = label in ("review", "qa", "docs")
    ok = check_stage(manifest, label) and (judged or all(state in ("done", "pass") for _, state in marks))
    if expect:
        acc_ok, verdict = acceptance_check(manifest, expect, quiet=True)
        print(f"ACCEPTANCE ({expect}): {verdict}")
        ok = ok and acc_ok
    wp_marks = [jid for jid, _ in marks if WP_ID_RE.fullmatch(jid)]
    if wp_marks:  # the stop gate's denials, for the engines' run record
        denials = gate_denials(lambda d: None)
        print("GATE: " + " ".join(f"{jid}={denials.get(jid, {}).get('denials', 0)}" for jid in wp_marks))
    ledger_stage(label, "OK" if ok else "REVIEW")
    print("RESULT:", "OK" if ok else "REVIEW")
    sys.exit(0 if ok else 3)


MAX_DIFF_BYTES = 200_000


def write_fix_diffs(manifest, label, attempts):
    """What each package changed in this fix attempt, as `runs/<ID>.fix-<N>.diff`: the next attempt reads it to avoid
    repeating a fix that did not work. attempts: [(ID, N)]."""
    tree_file = STATE / f"tree-{label}.txt"
    after = git_tree()
    if not tree_file.exists() or not after:
        return
    before = tree_file.read_text(encoding="utf-8").strip()
    table = wps(manifest)
    runs = STATE / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    for jid, n in attempts:
        files = table.get(jid, {}).get("files", [])
        if not files:
            continue
        r = subprocess.run(["git", "diff", "--no-color", "--no-ext-diff", before, after, "--", *files], cwd=ROOT,
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        text = r.stdout
        if len(text) > MAX_DIFF_BYTES:
            text = text[:MAX_DIFF_BYTES] + "\n[diff truncated]\n"
        (runs / f"{jid}.fix-{n}.diff").write_text(text or "(this attempt changed no file)\n", encoding="utf-8")


def normal_failure(line):
    """A failure line without durations or repeated spaces: the same failure reads the same in every run."""
    return " ".join(DURATION_RE.sub("", line).split())[:MAX_TAIL_LINE]


def failure_lines(lines):
    """The lines of an output that name a failure, normalised, without repeats."""
    return sorted({normal_failure(line) for line in lines if FAILURE_LINE_RE.search(line)} - {""})


def run_phases(manifest):
    cfg = manifest.get("verify", {})
    phases = []
    for name in VERIFY_PHASES:
        if name not in cfg:
            phases.append({"name": name, "skipped": True})
            continue
        scan = []
        code, tail = run_command(cfg[name], scan=scan)
        phases.append({"name": name, "command": cfg[name], "exit": code, "passed": code == 0, "tail": tail, "_scan": scan})
    return phases


def attribute(manifest, lines):
    """Maps the output of a failure to the work packages that own what it mentions: a file of theirs (by path) or one
    of their criteria (AC-NNN in a test name, through the WP's "acs"). Deterministic; no model."""
    # Absolute paths read as the repo's relative ones, with forward slashes whatever the system printed.
    text = "\n".join(lines).replace("\\", "/").replace(ROOT.as_posix() + "/", "")
    # Whole criteria and whole paths: 0x7f91ac30 is not AC-030, src/a.pyx is not src/a.py, lib/util.py is not util.py.
    seen = {f"AC-{int(n):03d}" for n in re.findall(r"(?<![A-Za-z0-9])AC[-_ ]?(\d+)", text, re.I)}

    def mentioned(f):
        return re.search(r"(?<![\w./-])" + re.escape(f) + ("" if f.endswith("/") else r"(?![\w-])"), text)

    found = {}
    for wid, wp in sorted(wps(manifest).items()):
        files = [f for f in wp.get("files", []) if mentioned(f)]
        acs = [a for a in wp.get("acs", []) if f"AC-{int(a[3:]):03d}" in seen]
        if files or acs:
            found[wid] = {"files": files, "acs": acs}
    return found


def baseline(manifest):
    """Baseline: the state of the verify phases BEFORE touching anything. Separates regressions from existing failures.
    Without verify commands there is nothing to measure: it does nothing (the workflow always asks for it)."""
    if not manifest.get("verify"):
        BASELINE.unlink(missing_ok=True)
        print(json.dumps({"phases": [], "preexisting_failures": [], "skipped": True}))
        sys.exit(0)
    phases = run_phases(manifest)
    saved = []
    for ph in phases:
        scan = ph.pop("_scan", None)
        saved.append(dict(ph, failures=failure_lines(scan)) if ph.get("passed") is False else ph)
    STATE.mkdir(parents=True, exist_ok=True)
    BASELINE.write_text(json.dumps({"phases": saved}, ensure_ascii=False, indent=2), encoding="utf-8")
    failing = [p["name"] for p in phases if p.get("passed") is False]
    ledger_stage("baseline", "OK" if not failing else "with existing failures: " + ", ".join(failing))
    print(json.dumps({"phases": phases, "preexisting_failures": failing}, ensure_ascii=False))
    errored = [p["name"] for p in phases if p.get("exit") in (124, 126, 127)]
    sys.exit(1 if errored else 0)


def verify(manifest):
    """The verify phases and green acceptance. With a baseline, a phase that already failed is a 'pre-existing failure'
    (reported, does not block) and one that passed and now fails is a 'regression' (blocks). JSON output."""
    base = {p["name"]: p for p in json.loads(BASELINE.read_text(encoding="utf-8")).get("phases", [])} if BASELINE.exists() else {}
    phases = run_phases(manifest)
    for ph in phases:
        if ph.get("skipped"):
            continue
        was = base.get(ph["name"], {}).get("passed")
        ph["baseline_passed"] = was
        known = base.get(ph["name"], {}).get("failures")  # absent in a baseline from an earlier version
        new = [f for f in failure_lines(ph["_scan"]) if f not in set(known)] if known is not None else []
        if ph["passed"]:
            ph["status"] = "ok"
        elif was is False and not new:
            ph["status"] = "pre-existing failure"
        elif was is False:
            ph["status"] = "regression"
            ph["new_failures"] = new[:10]
            # only the new failures say who broke something: the old ones belong to nobody in this run
            ph["_scan"] = [line for line in ph["_scan"] if normal_failure(line) in new]
        else:
            ph["status"] = "regression" if was else "fails"
    if at_entity(manifest)["command"]:
        scan = []
        acc_ok, verdict = acceptance_check(manifest, "green", quiet=True, scan=scan)
        phases.append({"name": "acceptance", "command": at_entity(manifest)["command"], "passed": acc_ok,
                       "verdict": verdict, "status": "ok" if acc_ok else "fails", "_scan": scan})
    blocking = [p["name"] for p in phases if p.get("status") in ("regression", "fails")]
    suspects = {}
    for ph in phases:
        scan = ph.pop("_scan", [])
        if ph.get("status") in ("regression", "fails"):
            for wid, info in attribute(manifest, scan).items():
                entry = suspects.setdefault(wid, {"phases": [], "files": [], "acs": []})
                entry["phases"].append(ph["name"])
                entry["files"] = sorted(set(entry["files"]) | set(info["files"]))
                entry["acs"] = sorted(set(entry["acs"]) | set(info["acs"]))
    preexisting = [p["name"] for p in phases if p.get("status") == "pre-existing failure"]
    ok = not blocking
    ledger_stage("verification", "OK" if ok else "REVIEW: " + ", ".join(blocking))
    print(json.dumps({"ok": ok, "blocking": blocking, "preexisting_failures": preexisting, "suspects": suspects,
                      "phases": phases}, ensure_ascii=False))
    sys.exit(0 if ok else 3)


def sync_permissions(manifest):
    """Writes to .claude/settings.local.json the permissions the agents need, so they do not ask."""
    rules = {"Workflow(" + WORKFLOW_NAME + ")", "Bash(python3 .claude/bin/agentic.py:*)"}
    rules |= set(manifest.get("allow_bash", [])) | set(qa_entity(manifest)["allow_bash"]) | set(at_entity(manifest)["allow_bash"])
    for cmd in [at_entity(manifest)["command"], *manifest.get("verify", {}).values()]:
        if cmd:
            rules |= {f"Bash({cmd})", f"Bash({cmd}:*)"}
    local = ROOT / ".claude" / "settings.local.json"
    data = json.loads(local.read_text(encoding="utf-8")) if local.exists() else {}
    allow = data.setdefault("permissions", {}).setdefault("allow", [])
    added = sorted(r for r in rules if r not in allow)
    allow.extend(added)
    local.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(added)} rule(s) added to .claude/settings.local.json" + (": " + ", ".join(added) if added else ""))


def finish(manifest, label, statuses, expected):
    ownership_ok = check_stage(manifest, label)
    print(f"\nSTAGE {label} SUMMARY")
    for jid, st in statuses.items():
        print(f"  {jid}: {st}   report: .claude/state/runs/{jid}.json")
    # By ledger state, not by text: DONE_WITH_CONCERNS is as finished as DONE.
    ok = ownership_ok and all(status_to_state(s) == status_to_state(expected) for s in statuses.values())
    ledger_stage(label, "OK" if ok else "REVIEW")
    print("RESULT:", "OK" if ok else "REVIEW")
    sys.exit(0 if ok else 3)


# ---------- hook PreToolUse ----------

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


# ---------- the stop gate ----------

def gate_denials(fn):
    return locked_json(GATE, fn)


def gate_reset(owners):
    """Drops the gate counters of those packages: a stage that opens gets its own denials."""
    def drop(data):
        return {k: v for k, v in data.items() if k not in owners}
    gate_denials(drop)


def own_failure(manifest, owner_id, lines):
    """The lines of a failing acceptance run that are the package's own: its files mentioned, or a criterion only it
    owns. A criterion shared with another package, or another package's failure, is nobody's reason to stop."""
    found = attribute(manifest, lines).get(owner_id)
    if not found:
        return []
    others = {a for wid, wp in wps(manifest).items() if wid != owner_id for a in wp.get("acs", [])}
    alone = [a for a in found["acs"] if a not in others]
    if not found["files"] and not alone:
        return []
    text = "\n".join(lines).replace("\\", "/").replace(ROOT.as_posix() + "/", "")
    markers = found["files"] + alone
    own = [line for line in text.splitlines() if any(m in line for m in markers)]
    return own or lines[-3:]


def gate_check(owner_id, status):
    """The stop gate. None lets the report through; a list of lines refuses it (the agent reads them and goes on).
    Lets through when the report is not finished, there is no acceptance command, the package used up its denials,
    the command cannot run or times out, it passes, or the failure is not the package's own. Fails open on its own
    errors: a gate that refused every report would stall a run with no cap."""
    if not isinstance(status, str) or status not in GATE_STATUSES:
        return None
    try:
        manifest = load_manifest()
        cmd = at_entity(manifest)["command"]
        if not cmd or gate_denials(lambda d: None).get(owner_id, {}).get("denials", 0) >= GATE_MAX_DENIALS:
            return None
        lines = []
        code, _ = run_command(cmd, timeout=GATE_TIMEOUT_S, scan=lines)
        if code == 0 or code in (124, 126, 127):
            return None
        own = own_failure(manifest, owner_id, lines)
        if not own:
            return None
        shown = [normal_failure(line) for line in own][:GATE_MAX_LINES]
        gate_denials(lambda d: {**d, owner_id: {"denials": d.get(owner_id, {}).get("denials", 0) + 1, "last": shown}})
        return shown
    except (ManifestError, OSError, ValueError) as e:
        print(f"[agentic guard] gate skipped: {type(e).__name__}: {e}", file=sys.stderr)
        return None


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
            "BLOCKER": "blocker", "PASS": "pass", "FAIL": "fail", "TIMEOUT": "timeout", "STOPPED": "stopped"}.get(head, "error")


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
    lines += [f"- {'wave ' + k if k.isdigit() else k}: {stages[k]}" for k in sorted(stages, key=order_st)] or ["- (none)"]
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


def block(msg):
    print(f"[agentic guard] {msg}", file=sys.stderr)
    sys.exit(2)


# What each of the flow's agent types may claim; an implementer (or a WP's own agent type) claims its packages.
CLAIM_KINDS = {"qa": {"QA"}, "test-author": {"AT"}, "doc-updater": {"DOCS"}, "reviewer": {"REVIEW"},
               "verifier": {"REVIEW"}, "test-auditor": {"REVIEW"}}


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
        block(f"a {agent_type} agent cannot claim {wanted}: claim your own ID ({', '.join(sorted(allowed)) or 'none'}).")
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
        ledger_update(wanted, state="running", stage=stage_of(wanted), mode="workflow", pid=None, started=now_hm(), note="")
    except Exception:  # noqa: BLE001
        pass


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
                old = abs_path.read_text(encoding="utf-8", errors="replace") if tool == "Write" and abs_path.exists() else ""
            except OSError:
                old = ""
        if len(SKIP_MARKER_RE.findall(new)) > len(SKIP_MARKER_RE.findall(old)):
            block(f"{owner_id}: tests are not disabled to turn them green (skip, xfail, only…) in {rel}. "
                  "A failing test is fixed or reported: return the blocked status your schema allows (BLOCKED for an "
                  "implementer, BLOCKER for the others). "
                  "Conditional platform skips (skipIf/skipUnless) are allowed.")


def claim_required_types():
    """The flow's agents with a zone of their own, including those a WP declares in "agent": without a claim, they do not write."""
    types = {"implementer", "qa", "test-author", "reviewer", "doc-updater"}
    try:
        types |= {wp["agent"] for wp in wps(load_manifest()).values() if isinstance(wp.get("agent"), str)}
    except ManifestError:
        pass
    return types


def guard(data):
    tool = data.get("tool_name", "")
    tinput = data.get("tool_input", {}) or {}
    agent_id = data.get("agent_id")
    owner_id = os.environ.get("AGENTIC_WP")

    bash_cmd = tinput.get("command", "") if tool == "Bash" else ""
    if len(bash_cmd) > MAX_CHECKED_COMMAND and (agent_id or owner_id):
        block(f"a command longer than {MAX_CHECKED_COMMAND} characters cannot be checked: split it or use a script file.")
    if bash_cmd and (GIT_HOOK_BYPASS_RE.search(bash_cmd) or GIT_COMMIT_N_RE.search(QUOTED_RE.sub("''", bash_cmd))):
        block("bypassing git hooks (--no-verify, commit -n, core.hooksPath) is not allowed for any agent. "
              "If a hook fails, fix the cause or ask the user to decide.")

    if tool == "Bash" and agent_id and not owner_id:
        m = CLAIM_RE.fullmatch(tinput.get("command", ""))
        if m:
            claim(agent_id, m.group(1), data.get("agent_type"))
            return

    if not owner_id and agent_id:
        owner_id = with_claims(lambda c: None).get(agent_id)
    if tool == "StructuredOutput":
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
        return
    if not owner_id and agent_id:
        if not owner_id and data.get("agent_type") in claim_required_types():
            block("First of all, run `python3 .claude/bin/agentic.py claim <ID>` with your WP-ID (or QA, AT, REVIEW, DOCS).")
    if not owner_id:
        return  # hilo principal u otro subagente: sin restricciones

    if tool == "Bash":
        cmd = tinput.get("command", "")
        for rx, why in FORBIDDEN_BASH:
            if rx.search(cmd):
                block(f"{owner_id}: command blocked, {why}: {cmd}")
        return

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
            or flow_path_refused(rel, maintainer_mode(manifest))):
        block(f"{owner_id}: {rel} is not written by any agent of the flow (plan, configuration, decision archive "
              "or generated map). If it has to change, return the blocked status your schema allows (BLOCKED for an "
              "implementer, BLOCKER for the others).")
    if not any(owns(f, rel) for f in ent["files"]):
        block(f"{owner_id} does not own {rel}. Allowed files: {', '.join(ent['files'])}. "
              "If you need to touch it, return the blocked status your schema allows (BLOCKED for an implementer, "
              "BLOCKER for the others; QA reports it as a defect).")
    content_checks(owner_id, ent, rel, abs_path, tool, tinput)


HOOKS_DEAD_MSG = (
    "The guardrail hook (`agentic.py hook`) did not run for this call, so no guardrail of the flow is active in this "
    "session. Known causes: a cloud session with several repositories attached, or a thread inside a Claude project "
    "(Claude Code does not read the repository's hooks there); `disableAllHooks` in a settings file; Windows without Git "
    "for Windows. The fallback mode runs each agent as `claude -p`, which does load the repository's hooks: "
    "`/implement fallback`. Or open a session with this repository alone.")


def is_old_hook_command(command):
    """The hook command before 2.5.0: `agentic.py ... hook`."""
    return "agentic.py" in command and command.rstrip().endswith("hook")


def flow_hooks(pre_tool_use):
    """The flow's own entries in a PreToolUse list: (entry, hook) pairs whose command runs the launcher `hook.py`, or
    `agentic.py ... hook` as before 2.5.0."""
    return [(e, h) for e in pre_tool_use for h in e.get("hooks", [])
            if ".claude/bin/hook.py" in h.get("command", "") or is_old_hook_command(h.get("command", ""))]


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


def stage_guarded(label):
    """True if an agent of the flow went through the hook since the stage opened."""
    seen = locked_json(HOOK_SEEN, lambda s: None)
    guarded, opened = seen.get("guarded_ts"), STATE / f"gitstate-{label}.json"
    if guarded is None:
        return False
    return not opened.exists() or guarded >= opened.stat().st_mtime


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
    hook_cmd = flow_hooks(json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))["hooks"]["PreToolUse"])[0][1]["command"]
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


# ---------- CLI ----------

def main():
    compat.utf8_stdio()
    if len(sys.argv) < 2:
        die(__doc__)
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "hook":
        hook()
    elif cmd == "hooks-check":
        sys.exit(hooks_check())
    elif cmd == "claim":
        if not args:
            die("Uso: claim ID")
        registered = os.environ.get("AGENTIC_WP") or args[0] in with_claims(lambda c: None).values()
        print(f"{args[0]} reclamado." if registered
              else f"Warning: the hook did not record {args[0]}. Are the hooks in .claude/settings.json active?")
    elif cmd == "status":
        ledger_render()
        print(LEDGER_MD.read_text(encoding="utf-8"), end="")
    elif cmd == "mark":
        if len(args) < 2 or args[1] not in LEDGER_STATES:
            die(f"Uso: mark ID ESTADO [NOTA]  (estados: {', '.join(LEDGER_STATES)})")
        jid, state = args[0], args[1]
        if jid not in entities(require_valid()):
            die(f"{jid} does not exist in the manifest.")
        prev = ledger_read().get(jid, {})
        fields = dict(state=state, stage=stage_of(jid), mode=prev.get("mode", "workflow"), note=" ".join(args[2:]))
        if not prev:
            fields["started"] = now_hm()
        ledger_update(jid, **fields)
        print(f"{jid}: {state}")
    elif cmd == "stop":
        stop(args)
    elif cmd == "validate":
        require_valid()
        print("Manifest valid.")
    elif cmd == "run-qa":
        run_qa(require_valid())
    elif cmd == "pipeline-plan":
        if args not in ([], ["--keep-guard"]):
            die("Uso: pipeline-plan [--keep-guard]")
        pipeline_plan(require_valid(), keep_guard=bool(args))
    elif cmd == "guard-release":
        release_guard()
        print("Guard released.")
    elif cmd == "open-stage":
        if not args:
            die("Uso: open-stage N|acceptance|review|qa")
        open_stage(require_valid(), stage_label(args[0]))
    elif cmd == "close-stage":
        if not args:
            die("Uso: close-stage N|acceptance|review|qa ID=estado... [--expect red|green] [--fix]")
        close_stage(require_valid(), stage_label(args[0]), args[1:])
    elif cmd == "briefs":
        info = write_briefs(require_valid())
        print(json.dumps({"full_words": len(ARCH.read_text(encoding="utf-8").split()), "briefs": info}))
    elif cmd == "seq":
        seq(args)
    elif cmd == "run-pipeline":
        run_pipeline(require_valid(), args)
    elif cmd == "verify":
        verify(require_valid())
    elif cmd == "baseline":
        baseline(require_valid())
    elif cmd == "save-result":
        # The launcher cannot write in .claude/ (a protected path): this saves the workflow JSON here, read from stdin.
        # With FILE (e.g. .agentic-result.json at the root), it reads and deletes it; without it, it reads stdin.
        src = (ROOT / args[0]) if args else None
        if src is not None and (Path(args[0]).as_posix() != RESULT_FILE or not src.is_file()):
            die(f"save-result: only {RESULT_FILE}, at the repo root, is read (and then deleted).")
        try:
            data = json.loads(src.read_text(encoding="utf-8")) if src else json.loads(compat.read_stdin_text())
        except json.JSONDecodeError as e:
            die(f"save-result: not valid JSON: {e}")
        if src:
            src.unlink()
        out = STATE / "runs" / "workflow-result.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Saved to {out.relative_to(ROOT)} (result: {data.get('result', '?')})")
    elif cmd == "sync-permissions":
        sync_permissions(require_valid())
    elif cmd == "run-docs":
        run_docs(require_valid())
    elif cmd == "stack":
        declared, detected = declared_stack(), detect_stack()
        print(json.dumps({"declared": declared, "detected": detected, "packs": known_packs(),
                          "undeclared": sorted(set(detected) - set(declared)) if declared else []}, ensure_ascii=False))
    elif cmd == "rules-sync":
        rules_sync()
    elif cmd == "callers":
        callers()
    elif cmd == "codemap":
        try:
            manifest = load_manifest()
        except ManifestError:
            manifest = None  # the architect may generate it before the plan exists
        target = args[0].replace("\\", "/") if args else None
        if target and (not target.endswith(".md") or target.startswith("/") or Path(target).is_absolute()
                       or ".." in Path(target).parts or not (ROOT / target).resolve().is_relative_to(ROOT.resolve())
                       or target == "architecture.md" or target.startswith((".claude/", ARCHIVE_DIR))):
            die(f"codemap: the map is a .md path inside the repo, outside .claude/, {ARCHIVE_DIR} and architecture.md.")
        codemap(manifest, args[0] if args else None)
    elif cmd == "docs-check":
        try:
            manifest = load_manifest()
        except ManifestError:
            manifest = None
        docs_check(manifest)
    elif cmd == "install":
        flags = {a for a in args if a.startswith("--")}
        if flags - {"--force", "--dry-run", "--allow-dirty"}:
            die("Uso: install [DIR] [--force] [--dry-run] [--allow-dirty]")
        dirs = [a for a in args if not a.startswith("--")]
        install(Path(dirs[0] if dirs else os.getcwd()), force="--force" in flags, dry_run="--dry-run" in flags,
                allow_dirty="--allow-dirty" in flags)
    elif cmd == "flow-history":
        if args:
            die("Uso: flow-history")
        upgrade = sibling("upgrade")
        try:
            data = upgrade.build_history(SOURCE_ROOT, INSTALL_GLOBS + upgrade.EXTRA_GLOBS)
        except ValueError as e:
            die(f"flow-history: {e}")
        (SOURCE_ROOT / ".claude" / "flow-history.json").write_text(json.dumps(data, indent=1, sort_keys=True) + "\n",
                                                                       encoding="utf-8")
        print(json.dumps({"paths": len(data["files"]), "hashes": sum(len(h) for h in data["files"].values()),
                          "generated_from": data["generated_from"]}))
    elif cmd == "run-acceptance":
        run_acceptance(require_valid())
    elif cmd == "run-review":
        run_review(require_valid())
    elif cmd == "acceptance":
        if args[:1] != ["--expect"] or args[1:2] not in (["red"], ["green"]):
            die("Uso: acceptance --expect red|green")
        acceptance(require_valid(), args[1])
    elif cmd in ("snapshot", "check-wave", "run-wave"):
        if not args or not (args[0].isdigit() or (args[0] in STAGE_LABELS and cmd != "run-wave")):
            die(f"Uso: {cmd} N" + ("" if cmd == "run-wave" else " | " + " | ".join(STAGE_LABELS)))
        label = args[0]
        if cmd == "snapshot":
            snapshot(label)
        elif cmd == "check-wave":
            sys.exit(0 if check_stage(require_valid(), label) else 3)
        else:
            rest = args[1:]
            if "--all" in rest and "--fix" not in rest:
                die("Uso: run-wave N [WP...] [--fix [--all]]")
            run_wave(require_valid(), int(label), only=[a for a in rest if a not in ("--fix", "--all")],
                     fix="--fix" in rest, include_all="--all" in rest)
    else:
        die(__doc__)


if __name__ == "__main__":
    main()
