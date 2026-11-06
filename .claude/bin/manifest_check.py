"""The manifest's validation: every family of checks `validate` runs, and `require_valid`, which dies on a bad one.

The families return lists of messages in a fixed order; the tests assert on specific messages and on the first one.
"""
import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compat  # noqa: E402  - the operating-system layer, next to this file
from core import (  # noqa: E402
    ARCHIVE_DIR, FLOW_NAME, ManifestError, RESERVED_IDS, ROOT, RULES_LIBRARY, TEMPLATE_MARKER, VERIFY_PHASES,
    WORKER_MODEL, WP_ID_RE, at_entity, die, docs_entity, entities, is_flow_repo, load_manifest, maintainer_mode,
    never_owned_problem, owner_conflict, owns, qa_entity, wps
)

MIRROR_RE = re.compile(r"^([^:]+?)(?::(\d+)(?:-(\d+))?)?$")  # path[:start[-end]]

# The architect always uses the latest Opus (alias `opus`, in /architect's frontmatter).
# The launcher (/implement) also runs on the latest Sonnet, from its frontmatter.
ARCHITECT_MODEL = "opus"

# The only exception to the worker model: the gatekeeper just relays a command and its output, so the fastest model
# does it. Nothing that reads or writes code may use it.
AGENT_MODEL_OVERRIDES = {"gatekeeper": "haiku"}

AC_RE = re.compile(r"AC-\d+")

# First token of an allow_bash rule that would allow arbitrary execution on its own.
OPEN_ENDED = {"bash", "sh", "zsh", "fish", "dash", "python", "python3", "node", "deno", "bun", "ruby", "perl",
              "php", "env", "xargs", "sudo", "eval", "exec", "npx", "npm", "pnpm", "yarn", "uv", "poetry",
              "cargo", "go", "make", "git", "claude", "find", "awk", "sed"}

FRONTMATTER_MODEL_RE = re.compile(r"\A---\s*\n(.*?)\n---", re.S)


# ---------- the manifest's checks ----------

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
    """Every problem of the manifest, in the order the families below report them; [] when it is valid."""
    table = wps(manifest)
    maintainer = maintainer_mode(manifest)
    errors = maintainer_errors(manifest) + package_errors(manifest, table) + ownership_errors(manifest, maintainer)
    errors += command_errors(manifest) + shape_errors(manifest)
    errors += stack_errors(manifest) + docs_errors(manifest)
    errors += agent_model_errors(manifest)
    return errors

# Advice from validate, never errors (issue 60): just above the largest values of the 17 archived plans (14 files,
# 8 criteria, 7 packages in a wave). Not calibrated against bench runs.
WP_FILES_WARN = 15
WP_ACS_WARN = 8
WAVE_WPS_WARN = 8

def complexity_warnings(manifest):
    """What makes a plan hard to run in parallel or to review: oversized packages, crowded waves, a chain."""
    warnings, waves = [], {}
    packages = [wp for wp in manifest.get("work_packages", []) if isinstance(wp, dict) and "id" in wp]
    for wp in packages:
        files, acs = wp.get("files", []), wp.get("acs", [])
        if isinstance(files, list) and len(files) > WP_FILES_WARN:
            warnings.append(f"{wp['id']}: owns {len(files)} files (more than {WP_FILES_WARN}): consider splitting it")
        if isinstance(acs, list) and len(acs) > WP_ACS_WARN:
            warnings.append(f"{wp['id']}: answers for {len(acs)} criteria (more than {WP_ACS_WARN}): "
                            "consider splitting it")
        if isinstance(wp.get("wave"), int):
            waves.setdefault(wp["wave"], []).append(wp["id"])
    for wave in sorted(waves):
        if len(waves[wave]) > WAVE_WPS_WARN:
            warnings.append(f"wave {wave}: {len(waves[wave])} packages (more than {WAVE_WPS_WARN}): "
                            "consider another wave")
    if len(waves) >= 2 and all(len(ids) == 1 for ids in waves.values()):
        warnings.append("every wave holds one package: the plan is a chain, nothing runs in parallel")
    return warnings

def maintainer_errors(manifest):
    """The maintainer flag (a plan for the flow itself, run by hand): a boolean, and true only in the flow's repo."""
    errors = []
    flag = manifest.get("maintainer")
    if flag is not None and not isinstance(flag, bool):
        errors.append("maintainer: must be true or false.")
    elif flag and not is_flow_repo(ROOT):
        errors.append("maintainer: a plan run by hand is only for the agentic-flow repository "
                      "(.claude-plugin/plugin.json "
                      f"named {FLOW_NAME} and {TEMPLATE_MARKER} on README.md's first line).")
    return errors

def package_errors(manifest, table):
    """The work packages: at least one, no reserved or duplicate IDs, the acceptance command, each WP's fields,
    its mirror references and its dependencies."""
    errors = []
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
                errors.append(f"{wp['id']} (wave {wp.get('wave')}) depends on {dep} (wave {table[dep].get('wave')}): "
                              "it must be in an earlier wave.")
    return errors

def ownership_errors(manifest, maintainer):
    """Every entity's files: paths as git shows them, inside the repo, ownable, and owned by one entity."""
    errors = []
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
            elif (f == "architecture.md" or f.startswith(".claude/") or f.startswith(ARCHIVE_DIR)
                  or f in ("", "./", "/")):
                errors.append(f"{ent['id']}: cannot own '{f}'.")
    owners = [(f, e["id"]) for e in entities(manifest).values() if isinstance(e.get("files"), list)
              for f in e["files"] if isinstance(f, str)]
    for i, (fa, wa) in enumerate(owners):
        for fb, wb in owners[i + 1:]:
            if wa != wb and owner_conflict(fa, fb):
                errors.append(f"File with two owners: {fa} ({wa}) and {fb} ({wb}).")
    return errors

def command_errors(manifest):
    """allow_bash rules and the acceptance/verify commands: well formed and not open-ended."""
    errors = []
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
    return errors

def shape_errors(manifest):
    """ignore and verify: the right shapes."""
    errors = []
    if not isinstance(manifest.get("ignore", []), list):
        errors.append("'ignore' must be a list of patterns.")
    verify_cfg = manifest.get("verify", {})
    if not isinstance(verify_cfg, dict) or any(k not in VERIFY_PHASES or not isinstance(v, str) or not v.strip()
                                                  for k, v in verify_cfg.items()):
        errors.append(f"'verify' must be an object with commands (text) for {', '.join(VERIFY_PHASES)}.")
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
    owners = [e["id"] for e in entities(manifest).values()
              if e["id"] != "DOCS" and any(owns(f, cm) for f in e["files"])]
    if not owners:
        return []
    return [f"docs.codemap ({cm}) is generated by agentic.py: it cannot belong to {', '.join(owners)}."]

def frontmatter_model(path):
    fm = FRONTMATTER_MODEL_RE.match(path.read_text(encoding="utf-8"))
    if not fm:
        return None
    m = re.search(r"^model:\s*(\S+)\s*$", fm.group(1), re.M)
    return m.group(1).strip("\"'") if m else None

def agent_model_errors(manifest):
    """Orchestrated agents on the latest Sonnet and the architect on the latest Opus, declared by alias."""
    errors = []
    names = ({"implementer", "qa", "test-author", "test-auditor", "reviewer", "gatekeeper", "verifier"}
             | {wp.get("agent", "implementer") for wp in wps(manifest).values()})
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
