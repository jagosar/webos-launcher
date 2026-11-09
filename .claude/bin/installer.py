"""Installing the flow in another repository (the plugin's job): the copy with its intact-file rule, the settings merge,
the agents' permissions, and the shipped-content history maintainers publish.
"""
import fnmatch
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compat  # noqa: E402  - the operating-system layer, next to this file
from core import (  # noqa: E402
    DEFAULT_CONFIG, ENTRY, ROOT, SOURCE_ROOT, at_entity, die, flow_hooks, is_old_hook_command, qa_entity, sibling
)
from verification import install_quality_baseline  # noqa: E402

# What `install` puts in another repo: the flow, without the flow's own tests.
INSTALL_GLOBS = [
    ".claude/bin/agentic.py",
    ".claude/bin/core.py",
    ".claude/bin/manifest_check.py",
    ".claude/bin/repo_scan.py",
    ".claude/bin/verification.py",
    ".claude/bin/stage.py",
    ".claude/bin/installer.py",
    ".claude/bin/launch.py",
    ".claude/bin/guardrail.py",
    ".claude/bin/hook.py",
    ".claude/bin/quality.py",
    ".claude/bin/compat.py",
    ".claude/bin/waiting.py",
    ".claude/bin/pipeline.py",
    ".claude/bin/brief.py",
    ".claude/bin/upgrade.py",
    ".claude/bin/doctor.py",
    ".claude/bin/plan_picture.py",
    ".claude/agents/*.md",
    ".claude/commands/*.md",
    ".claude/skills/*/SKILL.md",
    ".claude/workflows/*.js",
    ".claude/workflows/*.json",
    ".claude/rules-library/*.md",
]

INSTALL_RECORD = ".claude/agentic-install.json"

RESULT_FILE = ".agentic-result.json"  # where the workflow leaves its result for save-result

INSTALL_GITIGNORE = [".claude/state/", RESULT_FILE]

WORKFLOW_NAME = "agentic-implement"


# ---------- settings ----------

def flow_version():
    manifest = SOURCE_ROOT / ".claude-plugin" / "plugin.json"
    try:
        return json.loads(manifest.read_text(encoding="utf-8")).get("version", "dev")
    except (OSError, json.JSONDecodeError):
        return "dev"

# Rules an older flow added and none needs any more: install removes exactly these from the project's settings.
RETIRED_PERMISSIONS = ("Bash(python3 .claude/state/guard/bin/agentic.py:*)",)  # the frozen guard, gone in 2.10.0


def merge_settings(dst, src):
    """Adds to the project's settings.json the flow's permissions, hook and workflows, removing nothing of theirs but
    the RETIRED_PERMISSIONS the flow itself once added."""
    data = json.loads(dst.read_text(encoding="utf-8")) if dst.exists() else {}
    added = []
    perms = data.setdefault("permissions", {})
    for key in ("allow", "deny"):
        current = perms.setdefault(key, [])
        for rule in src.get("permissions", {}).get(key, []):
            if rule not in current and rule not in RETIRED_PERMISSIONS:
                current.append(rule)
                added.append(f"{key} {rule}")
    for rule in [r for r in perms.get("allow", []) if r in RETIRED_PERMISSIONS]:
        perms["allow"].remove(rule)
        added.append(f"removed allow {rule}")
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
            # 2.5: the hook runs through the launcher hook.py. Only the command changes; the project's matcher and
            # timeout stay.
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

def sync_permissions(manifest):
    """Writes to .claude/settings.local.json the permissions the agents need, so they do not ask."""
    rules = {"Workflow(" + WORKFLOW_NAME + ")", "Bash(python3 .claude/bin/agentic.py:*)"}
    rules |= set(manifest.get("allow_bash", [])) | set(qa_entity(manifest)["allow_bash"])
    rules |= set(at_entity(manifest)["allow_bash"])
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


# ---------- install ----------

def install(target, force=False, dry_run=False, allow_dirty=False):
    """Copies the flow to TARGET, or upgrades it. A flow file is replaced only if it is intact: its content is one the
    flow shipped (flow-history.json) or the previous install recorded. Edited ones are kept, with the new version in
    .claude/state/upgrade/, unless --force. It refuses to write over uncommitted changes or a run in progress."""
    upgrade = sibling("upgrade")
    target = target.resolve()
    prep = install_plan(target, upgrade, force)
    problems = install_guard(target, prep, dry_run, allow_dirty)
    written = install_write(target, prep, dry_run)
    install_report(target, prep, written, dry_run, problems)

def install_plan(target, upgrade, force):
    """What install would do: the record, the history, the files to ship and how each is classified, the removals."""
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
    return {"record": record, "previous": previous, "history": history, "record_path": record_path,
            "sources": sources, "plan": plan, "removed": removed, "intact": intact}

def install_guard(target, prep, dry_run, allow_dirty):
    """Refuses to write over uncommitted changes or a run in progress; returns the problems found."""
    sources, removed, upgrade = prep["sources"], prep["removed"], sibling("upgrade")
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
            print("Commit or stash your changes (or use --allow-dirty), and stop any run, then try again.",
                  file=sys.stderr)
            sys.exit(3)
    return problems

def install_write(target, prep, dry_run):
    """Copies, removes and records; merges settings, .gitignore and agentic.json. Nothing is written in a dry run."""
    sources, plan, previous, removed = prep["sources"], prep["plan"], prep["previous"], prep["removed"]
    record_path = prep["record_path"]
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
    source = json.loads((SOURCE_ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    settings, added = merge_settings(target / ".claude" / "settings.json", source)
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
            (target / ".claude" / "agentic.json").write_text(json.dumps(DEFAULT_CONFIG, indent=2) + "\n",
                                                            encoding="utf-8")
        record_path.write_text(json.dumps({"version": flow_version(), "files": new_record}, indent=2) + "\n",
                               encoding="utf-8")
    return report, added, missing_ignore, config_missing

def install_report(target, prep, written, dry_run, problems):
    """Prints what happened, installs the rules, starts the ratchet, lists the notes and validates the target."""
    record, plan, removed = prep["record"], prep["plan"], prep["removed"]
    report, added, missing_ignore, config_missing = written
    upgrade = sibling("upgrade")
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(target))
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
        r = subprocess.run([sys.executable, str(ENTRY), "rules-sync"], cwd=target,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
        if r.returncode == 0:
            print(f"  rules: {', '.join(json.loads(r.stdout)['rules'])}")
        else:
            print(f"  rules: could not be installed ({(r.stderr.strip().splitlines() or ['no detail'])[-1]})")
    print(f"  quality baseline: {install_quality_baseline(target, dry_run)}")
    if report["modified by you (left alone)"]:
        hint = ("run without --dry-run to get their new versions in .claude/state/upgrade/" if dry_run else
                "new versions in .claude/state/upgrade/ (compare with: diff -ru .claude/state/upgrade/.claude .claude)")
        print("kept files: " + hint)
    # Notes are for upgrades: a repo that had no flow at all has nothing to adapt.
    had_flow = bool(record) or bool(removed) or any(kind != "new" for kind in plan.values())
    notes = upgrade.notes_between(record.get("version"), flow_version()) if had_flow else []
    if notes:
        print("upgrade notes:", *notes, sep="\n  - ")
    if dry_run:
        for problem in problems:
            print(f"warning: {problem}")
    elif (target / "architecture.md").exists():
        r = subprocess.run([sys.executable, str(ENTRY), "validate"], cwd=target, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", env=env)
        errors = [line.strip() for line in r.stderr.splitlines()
                  if line.strip() and line.strip() != "Invalid manifest:"]
        print("validate: OK" if r.returncode == 0 else f"validate: FAILED: {(errors or ['no detail'])[0]}")
    print("RESULT: OK")


# ---------- flow history ----------

def flow_history():
    """Maintainers: writes .claude/flow-history.json, every content the flow ever shipped, from git. The summary."""
    upgrade = sibling("upgrade")
    try:
        data = upgrade.build_history(SOURCE_ROOT, INSTALL_GLOBS + upgrade.EXTRA_GLOBS)
    except ValueError as e:
        die(f"flow-history: {e}")
    (SOURCE_ROOT / ".claude" / "flow-history.json").write_text(json.dumps(data, indent=1, sort_keys=True) + "\n",
                                                               encoding="utf-8")
    return {"paths": len(data["files"]), "hashes": sum(len(h) for h in data["files"].values()),
            "generated_from": data["generated_from"]}


def flow_history_check():
    """Maintainers: whether .claude/flow-history.json recognises every published version, and the pending one (the
    version in plugin.json while it has no tag: "HEAD" is checked too). Writes nothing. The summary."""
    upgrade = sibling("upgrade")
    try:
        tags = upgrade.release_tags(SOURCE_ROOT)
        pending = None if upgrade.TAG_PREFIX + flow_version() in tags else flow_version()
        missing = upgrade.history_gaps(SOURCE_ROOT, INSTALL_GLOBS + upgrade.EXTRA_GLOBS,
                                       upgrade.load_history(SOURCE_ROOT), tags + (["HEAD"] if pending else []))
    except ValueError as e:
        die(f"flow-history: {e}")
    return {"ok": not missing, "tags": len(tags), "pending": pending, "missing": missing}
