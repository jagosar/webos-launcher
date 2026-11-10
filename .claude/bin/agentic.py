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
  flow-history [--check]
                      Maintainers: writes .claude/flow-history.json, every content the flow ever shipped, from git.
                      --check writes nothing: exit 3 if a published version (or plugin.json's untagged one) is missing.
  release-notes [VERSION]
                      Maintainers: the upgrade notes of VERSION (plugin.json's by default) as a Markdown list.
  doctor [--fix]      JSON: what the flow left stale in this project; each finding is auto (--fix repairs it), route
                      (names the command that does) or mention. Exit 3 while an auto or route finding remains.
  plan-picture        Writes .claude/state/plan.html: the manifest's packages by wave, their dependencies and a table.
  claim ID            Binds the current subagent to a WP or to QA (recorded by the hook).
  status              Shows the ledger: the state of every agent and stage.
  mark ID ESTADO [NOTA]
                      Updates the ledger by hand: running, done, blocker, pass, fail, stopped.
  stop [ID...]        Stops the running agents (all of them if none is given) and records it in the ledger.
  hook                PreToolUse hook (reads JSON from stdin).
  hooks-check         JSON: whether the guardrail hook runs in this session (exit 3 if it does not).
  quality [--init|--update]
                      The clean-code ratchet: function length, nesting, file size and long lines against
                      .claude/quality-baseline.json (exit 3 if something got worse). --init creates it, --update lowers it.

The ledger (.claude/state/ledger.md) is written by this script, not by the model: it survives context
compaction and is the source for answering "how is it going?".

The manifest is a ```json wp-manifest``` block inside architecture.md.
Launcher timeout and authentication: .claude/agentic.json (optional) and AGENTIC_* variables.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compat  # noqa: E402  - the operating-system layer, next to this file
# The modules by domain. Some names are imported only to be re-exported: tests and the acceptance files read them as
# agentic.<name>, and test_definitions.StructureTests checks they are the modules' own objects.
from core import (  # noqa: E402
    ARCH, ARCHIVE_DIR, LEDGER_MD, LEDGER_STATES, QUALITY_BASELINE, ROOT, SKIP_DIRS, STATE, USAGE,
    ManifestError, die, entities, flow_path_refused, is_flow_repo, ledger_read, ledger_render, ledger_update,
    load_manifest, locked_json, maintainer_mode, never_owned_problem, now_hm, refuse_by_hand_plan, root_drift, sibling,
    stage_of, status_to_state, stop, usage_records, with_claims,
)
from manifest_check import (  # noqa: E402
    complexity_warnings, frontmatter_model, known_packs, provenance_problems, require_valid, validate
)
from repo_scan import PATH_RE, callers, codemap, declared_stack, detect_stack, docs_check, rules_sync  # noqa: E402
from verification import acceptance, attribute, baseline, quality, quality_files, run_command, verify  # noqa: E402
from stage import STAGE_LABELS, check_stage, close_stage, open_stage, snapshot, stage_label  # noqa: E402
from installer import (  # noqa: E402
    RESULT_FILE, flow_history, flow_history_check, flow_version, install, merge_settings, sync_permissions,
)
from launch import (  # noqa: E402
    load_spec, pipeline_plan, render_prompt, run_acceptance, run_docs, run_pipeline, run_qa,
    run_review, run_steps, run_wave, seq, structured_report, write_briefs,
)
from guardrail import hook, hooks_check  # noqa: E402

__all__ = [
    "ROOT", "QUALITY_BASELINE", "SKIP_DIRS", "USAGE", "is_flow_repo", "maintainer_mode",
    "never_owned_problem", "flow_path_refused", "locked_json", "usage_records", "status_to_state", "validate",
    "frontmatter_model", "require_valid", "PATH_RE", "attribute", "run_command", "quality_files", "merge_settings",
    "structured_report", "run_steps", "render_prompt", "load_spec", "compat", "COMMANDS", "main",
]


# ---------- CLI: one handler per command, with the command's own argument checks and prints ----------

def cmd_hook(args):
    hook()


def cmd_hooks_check(args):
    return hooks_check()


def cmd_claim(args):
    if not args:
        die("Uso: claim ID")
    registered = os.environ.get("AGENTIC_WP") or args[0] in with_claims(lambda c: None).values()
    print(f"{args[0]} reclamado." if registered
          else f"Warning: the hook did not record {args[0]}. Are the hooks in .claude/settings.json active?")


def require_runnable():
    """The valid manifest of a plan agents may run: a plan for the flow itself stops here, before any state."""
    manifest = require_valid()
    refuse_by_hand_plan(manifest)
    return manifest


def cmd_status(args):
    ledger_render()
    print(LEDGER_MD.read_text(encoding="utf-8"), end="")


def cmd_mark(args):
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


def cmd_stop(args):
    stop(args)


def plan_provenance():
    """The provenance of section 4b, checked before any agent runs: dies on an error, returns the warnings."""
    errors, warnings = provenance_problems(ARCH.read_text(encoding="utf-8")) if ARCH.exists() else ([], [])
    if errors:
        die("Invalid manifest:\n  " + "\n  ".join(errors))
    return warnings


def cmd_validate(args):
    manifest = require_valid()
    warnings = plan_provenance()
    print("Manifest valid.")
    for warning in complexity_warnings(manifest) + warnings:
        print(f"warning: {warning}")


def cmd_run_qa(args):
    run_qa(require_runnable())


def cmd_pipeline_plan(args):
    if args:
        die("Uso: pipeline-plan")
    manifest = require_runnable()
    plan_provenance()
    pipeline_plan(manifest)


def cmd_quality(args):
    return quality(args)


def cmd_open_stage(args):
    if not args:
        die("Uso: open-stage N|acceptance|review|qa")
    open_stage(require_valid(), stage_label(args[0]))


def cmd_close_stage(args):
    if not args:
        die("Uso: close-stage N|acceptance|review|qa ID=estado... [--expect red|green] [--fix]")
    close_stage(require_valid(), stage_label(args[0]), args[1:])


def cmd_briefs(args):
    info = write_briefs(require_valid())
    print(json.dumps({"full_words": len(ARCH.read_text(encoding="utf-8").split()), "briefs": info}))


def cmd_seq(args):
    seq(args)


def cmd_run_pipeline(args):
    run_pipeline(require_runnable(), args)


def cmd_verify(args):
    verify(require_valid())


def cmd_baseline(args):
    baseline(require_valid())


def cmd_save_result(args):
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


def cmd_sync_permissions(args):
    sync_permissions(require_valid())


def cmd_run_docs(args):
    run_docs(require_runnable())


def cmd_stack(args):
    declared, detected = declared_stack(), detect_stack()
    print(json.dumps({"declared": declared, "detected": detected, "packs": known_packs(),
                      "undeclared": sorted(set(detected) - set(declared)) if declared else []}, ensure_ascii=False))


def cmd_rules_sync(args):
    rules_sync()


def cmd_callers(args):
    callers()


def optional_manifest():
    """The manifest, or None before the plan exists: the architect may generate a code map first."""
    try:
        return load_manifest()
    except ManifestError:
        return None


def cmd_codemap(args):
    manifest = optional_manifest()
    target = args[0].replace("\\", "/") if args else None
    if target and (not target.endswith(".md") or target.startswith("/") or Path(target).is_absolute()
                   or ".." in Path(target).parts or not (ROOT / target).resolve().is_relative_to(ROOT.resolve())
                   or target == "architecture.md" or target.startswith((".claude/", ARCHIVE_DIR))):
        die(f"codemap: the map is a .md path inside the repo, outside .claude/, {ARCHIVE_DIR} and architecture.md.")
    codemap(manifest, args[0] if args else None)


def cmd_docs_check(args):
    docs_check(optional_manifest())


def cmd_install(args):
    flags = {a for a in args if a.startswith("--")}
    if flags - {"--force", "--dry-run", "--allow-dirty"}:
        die("Uso: install [DIR] [--force] [--dry-run] [--allow-dirty]")
    dirs = [a for a in args if not a.startswith("--")]
    install(Path(dirs[0] if dirs else os.getcwd()), force="--force" in flags, dry_run="--dry-run" in flags,
            allow_dirty="--allow-dirty" in flags)


def cmd_flow_history(args):
    if args not in ([], ["--check"]):
        die("Uso: flow-history [--check]")
    if not args:
        print(json.dumps(flow_history()))
        return 0
    summary = flow_history_check()
    print(json.dumps(summary))
    return 0 if summary["ok"] else 3


def cmd_release_notes(args):
    if len(args) > 1:
        die("Uso: release-notes [VERSION]")
    try:
        print(sibling("upgrade").release_notes(args[0] if args else flow_version()), end="")
    except ValueError as e:
        die(str(e))


def cmd_doctor(args):
    if args not in ([], ["--fix"]):
        die("Uso: doctor [--fix]")
    summary = sibling("doctor").report(fix=bool(args))
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["ok"] else 3


def cmd_plan_picture(args):
    if args:
        die("Uso: plan-picture")
    manifest = require_valid()
    packages = manifest.get("work_packages", [])
    out = STATE / "plan.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(sibling("plan_picture").render(manifest), encoding="utf-8")
    print(json.dumps({"path": out.relative_to(ROOT).as_posix(), "packages": len(packages),
                      "waves": len({wp.get("wave") for wp in packages})}))


def cmd_run_acceptance(args):
    run_acceptance(require_runnable())


def cmd_run_review(args):
    run_review(require_runnable())


def cmd_acceptance(args):
    if args[:1] != ["--expect"] or args[1:2] not in (["red"], ["green"]):
        die("Uso: acceptance --expect red|green")
    acceptance(require_valid(), args[1])


def stage_arg(cmd, args):
    """The stage of snapshot, check-wave and run-wave: a wave number, or a label (not for run-wave)."""
    if not args or not (args[0].isdigit() or (args[0] in STAGE_LABELS and cmd != "run-wave")):
        die(f"Uso: {cmd} N" + ("" if cmd == "run-wave" else " | " + " | ".join(STAGE_LABELS)))
    return args[0]


def cmd_snapshot(args):
    snapshot(stage_arg("snapshot", args))


def cmd_check_wave(args):
    return 0 if check_stage(require_valid(), stage_arg("check-wave", args)) else 3


def cmd_run_wave(args):
    label, rest = stage_arg("run-wave", args), args[1:]
    if "--all" in rest and "--fix" not in rest:
        die("Uso: run-wave N [WP...] [--fix [--all]]")
    run_wave(require_runnable(), int(label), only=[a for a in rest if a not in ("--fix", "--all")],
             fix="--fix" in rest, include_all="--all" in rest)


COMMANDS = {
    "hook": cmd_hook, "hooks-check": cmd_hooks_check, "claim": cmd_claim, "status": cmd_status, "mark": cmd_mark,
    "stop": cmd_stop, "validate": cmd_validate, "run-qa": cmd_run_qa, "pipeline-plan": cmd_pipeline_plan,
    "quality": cmd_quality, "open-stage": cmd_open_stage,
    "close-stage": cmd_close_stage, "briefs": cmd_briefs, "seq": cmd_seq, "run-pipeline": cmd_run_pipeline,
    "verify": cmd_verify, "baseline": cmd_baseline, "save-result": cmd_save_result,
    "sync-permissions": cmd_sync_permissions, "run-docs": cmd_run_docs, "stack": cmd_stack,
    "rules-sync": cmd_rules_sync, "callers": cmd_callers, "codemap": cmd_codemap, "docs-check": cmd_docs_check,
    "install": cmd_install, "flow-history": cmd_flow_history, "release-notes": cmd_release_notes,
    "run-acceptance": cmd_run_acceptance, "run-review": cmd_run_review, "acceptance": cmd_acceptance,
    "snapshot": cmd_snapshot, "check-wave": cmd_check_wave, "run-wave": cmd_run_wave, "doctor": cmd_doctor,
    "plan-picture": cmd_plan_picture,
}


# They act on their target and the source, never on ROOT.
ROOT_FREE_COMMANDS = {"install", "flow-history", "release-notes"}


def main():
    """The entry point: the handler of the command, whose int result (if any) is the exit code."""
    compat.utf8_stdio()
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        die(__doc__)
    problem = None if sys.argv[1] in ROOT_FREE_COMMANDS else root_drift()
    if problem:
        die(problem)
    code = COMMANDS[sys.argv[1]](sys.argv[2:])
    if isinstance(code, int):
        sys.exit(code)


if __name__ == "__main__":
    main()
