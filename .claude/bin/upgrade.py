"""Upgrades of a repo that has an older flow: which files are intact, what blocks a write, what changed.

`agentic.py install` uses it. A file is intact when its content is one the flow shipped at some point, as recorded in
.claude/flow-history.json (built from the flow's git history by `agentic.py flow-history`). That is what lets a repo
created from the template, which has no install record, upgrade as safely as one installed by the plugin.
"""
import fnmatch
import hashlib
import re
import json
import subprocess
from pathlib import Path

# Files the template copies besides the flow itself: refreshed only where they already exist and are intact.
EXTRA_GLOBS = [".claude/bin/test_*.py", ".claude/bin/testutil.py", ".claude/bin/test_workflow.mjs", ".claude/bin/conformance.json",
               ".claude/bin/fixtures/*", ".github/workflows/agentic-flow-ci.yml", ".github/workflows/reset-readme.yml",
               "plugin/commands/*.md", ".claude-plugin/*.json"]

# The release tags: TAG_PREFIX + the version in .claude-plugin/plugin.json.
TAG_PREFIX = "agentic-flow--v"

# What a user upgrading from an older version must know, by the version that introduced it.
NOTES = {
    "1.1.0": [
        "Agents answer with the launcher's JSON schema; the text STATUS: format is gone. Agent files you customised "
        "still work: compare them with the new versions.",
        "The commands wave-plan, open-wave and close-wave were removed: update scripts or CI that call them.",
        "Plans need '#### WP-NN' and '- AC-NNN ·' headings to give each agent only its slice (briefs); older plans "
        "still work with the whole document.",
        "install now updates the flow's own tests and CI copied from the template when they are intact, and keeps "
        "edited files with the new version in .claude/state/upgrade/.",
    ],
    "1.1.4": [
        "settings.json gets env.BASH_DEFAULT_TIMEOUT_MS and BASH_MAX_TIMEOUT_MS (30 minutes) where the project has not set "
        "them, so the agents' long test runs stay in the foreground instead of being moved to the background.",
        "A confirmed review finding or a blocking QA defect now goes through a fix cycle; before, the run stopped as if "
        "the reviewer or QA had written outside its zone.",
        "validate is stricter: ownership paths written with ./, // or backslashes, and acceptance or verify commands "
        "that would allow anything (python3 or bash alone), are errors. If validate fails, fix the plan with /architect.",
        "Closing a stage also fails on a commit, a change to git's config or hooks, or a secret, skipped test or relaxed "
        "quality configuration added through Bash. Agents never commit during a run; the commit gate is at the end.",
        "verify compares an already failing phase failure by failure: a new failure in it now blocks. A baseline saved "
        "by an earlier version keeps the old comparison until the next baseline.",
        "settings.json gets hooks on PermissionRequest, PostToolUse, PostToolUseFailure and SubagentStop: agentic.py "
        "status shows which agent of a run is waiting for your permission or answer.",
        "The gatekeeper no longer guesses an exit code that Bash did not show: a step that exited 0 (for example a "
        "baseline with failing tests in a package that does not exist yet) no longer stops the run.",
        "run-pipeline also reads an agent's report written as a ```json block after its prose, as the claude CLI "
        "sometimes returns it, instead of stopping with NO REPORT.",
        "The agents run-pipeline starts list StructuredOutput in their tools: without it, claude -p --agent returns "
        "no structured report and every fallback run stopped at wave 0 with NO REPORT.",
    ],
    "2.3.0": [
        "run-wave --fix reads the newest review and QA wherever they were saved: the run record "
        "(.claude/state/runs/workflow-result.json, which the native workflow and run-pipeline both write), "
        "run-pipeline's REVIEW-<n>.json / QA-<n>.json, or the one-off REVIEW.json / QA.json; it never reads a "
        "finding the verifier refuted. --fix --all adds medium and low implementation defects; the fix-cycle cap "
        "is the same.",
        "The run result gains verification_failures: every failed verification (its blocking phases, suspects and "
        "failing lines), so a fix cycle that verification triggered keeps its reason. /implement presents them.",
    ],
    "2.4.0": [
        "The guardrail hook now leaves a heartbeat (.claude/state/hook-seen.json), and hooks-check says whether it runs "
        "in this session. /implement runs it first, and both engines run it before any agent: a session where Claude "
        "Code does not load the repository's hooks (a cloud session with several repositories, a thread inside a "
        "Claude project, disableAllHooks) stops instead of running without guardrails. Use /implement fallback there.",
        "close-stage refuses a stage whose files changed with no hook call from an agent of the flow "
        "(GUARDRAIL HOOK SAW NO FLOW AGENT): the hook did not guard those writes. If it repeats in every stage of the "
        "native workflow, use /implement fallback and report the agent_type recorded in hook-seen.json.",
    ],
    "2.4.1": [
        "The fix-cycle prompt and implementer.md say that a caller outside every package that a change breaks is "
        "kept working from the changed package's files (a default for a new parameter, a wrapper), and that BLOCKED "
        "is only for when no compatible change exists or section 6 explicitly forbids it. A customised "
        "implementer.md keeps the old wording: merge the two sentences.",
    ],
    "2.5.0": [
        "The PreToolUse hook now runs through a small launcher, .claude/bin/hook.py, instead of agentic.py directly: "
        "install rewrites the old command in .claude/settings.json (keeping your matcher and timeout) and ships "
        "hook.py. A settings.json you maintain by hand needs the new command: "
        "python3 \"$CLAUDE_PROJECT_DIR/.claude/bin/hook.py\".",
        "Maintainer mode, for the agentic-flow repository only: a plan with \"maintainer\": true may give work "
        "packages files under .claude/ (never settings.json, settings.local.json, .claude/state/, hook.py or "
        "waiting.py). The run is judged by a frozen copy of the guard, and the gatekeeper accepts its command. "
        "Projects are not affected: the flag is refused anywhere else.",
    ],
    "2.6.0": [
        "A skill for the main session: /red-green-proof (.claude/skills/red-green-proof/SKILL.md, by RooAGI, MIT). "
        "For a bug: verify the cause, write a test that fails on the current code, fix it, then revert the fix and "
        "watch the test fail again before restoring it. Nothing in the flow's stages changes.",
    ],
    "2.6.1": [
        "test_definitions.py no longer fails in a project: its check that every skill is listed and credited in the "
        "README ran against the project's README and the project's own skills; it now runs only in the flow's repo.",
    ],
    "2.7.0": [
        "A clean-code ratchet: install creates .claude/quality-baseline.json from your code as it is (commit it), and "
        "verify gains a quality phase that fails when a function gets longer or deeper, a file grows, or long lines "
        "are added, against that baseline (limits: 60-line functions, 100 in tests, nesting 4, 800-line files, "
        "120 characters). A package that makes the code worse is sent back. Lower the baseline with "
        "`agentic.py quality --update`; to accept something worse on purpose, edit the JSON by hand. "
        "Agents cannot write it.",
        "The hook no longer blocks commands that only mention a path such as .claude/bin next to a -p flag "
        "(unittest's). Maintainer mode (2.5.0) works for runs by hand only: Claude Code protects .claude/ from agents "
        "started with claude -p, and allow rules do not lift it.",
    ],
    "2.7.1": [
        "The shipped tests no longer fail in a project: test_agentic.py's check that the flow measures its own "
        "scripts, and test_definitions.py's check of the shipped skills, hold only in the flow's repo and now run "
        "only there (the latter also broke the import of its whole file in a project with no README).",
        "The shipped CI workflow (.github/workflows/agentic-flow-ci.yml, refreshed where yours is intact) drops macOS "
        "from its matrix and gains a job that installs the flow into an empty project and runs the shipped tests "
        "there; that job runs only in the agentic-flow repository.",
    ],
    "2.8.0": [
        "agentic.py is split into modules by domain, installed beside it: core.py, manifest_check.py, repo_scan.py, "
        "verification.py, stage.py, installer.py, launch.py and guardrail.py. Every command, argument, message and "
        "exit code is the same; agentic.py keeps the command table and the names tests read. If install kept an "
        "edited agentic.py of yours, it is the old self-contained file and keeps working alone; the new modules sit "
        "beside it unused until you take the new agentic.py from .claude/state/upgrade/.",
    ],
    "2.8.1": [
        "A command run from inside one repository while CLAUDE_PROJECT_DIR still names another (a cloud session's "
        "variable follows its working directory, which moves between tool calls) is refused, with both paths in the "
        "message, instead of validating, measuring or rewriting the other repository. install and flow-history, which "
        "act on their target and the source, are exempt.",
    ],
    "2.9.0": [
        "Maintainers: `agentic.py flow-history --check` says whether .claude/flow-history.json recognises every "
        "published version and, while plugin.json carries an untagged version, the current content (exit 3 if not), "
        "and `agentic.py release-notes [VERSION]` prints a version's upgrade notes. Projects see no change in "
        "behaviour.",
        "The shipped CI workflow gains a `history` job that only runs in the agentic-flow repository; install "
        "refreshes the file where yours is intact.",
    ],
    "2.10.0": [
        "Maintainer mode is retired: the frozen guard, `pipeline-plan --keep-guard` and `guard-release` are gone, and "
        "the hook always runs the live agentic.py. A plan with \"maintainer\": true is now only a plan for the "
        "agentic-flow repository itself, executed by hand: every command that starts agents refuses it. An edited "
        "agentic-implement.js that still calls guard-release must be merged with the new one (.claude/state/upgrade/).",
        "install removes the permission for the frozen guard's command from settings.json; a leftover "
        ".claude/state/guard/ folder can be deleted.",
        "The per-release acceptance test files in .claude/bin are retired; their remaining checks live in the unit "
        "suites (new: test_install, test_guardrail, test_launcher, test_ci, test_manifest_check, test_repo_scan, "
        "test_layout). install removes intact copies from projects and keeps edited ones.",
    ],
    "2.11.0": [
        "Agent shortcuts are blocked: the hook refuses, and close-stage fails on, an exception swallowed with pass, an "
        "empty catch block, an always-true assertion, a [DEBUG- log or a Python test without an assertion added to "
        "code. Only what a change adds counts; what was already there is never blamed.",
        "validate prints complexity warnings, never errors: a package with more than 15 files or 8 criteria, a wave "
        "with more than 8 packages, a plan that is a chain of one-package waves. The thresholds are not measured.",
        "A workflow-subagent (the type the Workflow tool's subagents may carry; not verified) cannot write files "
        "before it claims its zone. Bash stays open, so a gatekeeper with that type keeps working.",
    ],
    "2.2.0": [
        "The reviewer reads beyond the diff: a deterministic `callers` step lists, for every public symbol defined "
        "in a changed file (the code map's regexes, ten languages), the lines of tracked code outside the diff that "
        "use it as a whole word, tests included, at most 10 per symbol and 80 in total. The list travels in the "
        "reviewer's prompt, and a caller the change breaks is a finding on the package that owns the change.",
        "reviewer.md changes (the callers in its process and in its false-positive rule); install refreshes it "
        "unless the project edited it, in which case merge the two sentences. Nothing else changes for projects: "
        "the step joins the gatekeeper call that already runs verify and open-stage review.",
    ],
    "2.1.0": [
        "A stop gate: when an implementer submits a report that says DONE (or DONE_WITH_CONCERNS), the hook runs the "
        "manifest's acceptance command and refuses the report, with the failing lines, if a failure is the package's "
        "own (its files, or a criterion only it owns). Twice at most per package and stage; then the report goes "
        "through. It fails open on its own errors. The result's gate_denials counts the refusals per package.",
        "Four implementer statuses: DONE, DONE_WITH_CONCERNS (done; each concern goes to pending without severity "
        "and to the reviewer, who may confirm it as a finding), NEEDS_CONTEXT (one more call with the whole "
        "architecture.md instead of the brief, then a blocker) and BLOCKED (the old BLOCKER). The report schema gains "
        "concerns and missing_context. A customised implementer.md from 2.0 must be merged: BLOCKER is no longer in "
        "the schema, and a prompt that still says it will be forced into one of the four values.",
        "settings.json: the flow's PreToolUse hook now matches StructuredOutput too and has a 900 s timeout, so the "
        "gate can run the acceptance suite. install appends StructuredOutput to the existing matcher and sets the "
        "timeout; a project that hand-edited that entry should check it. The fallback engine's per-agent timeout_s "
        "(agentic.json) must leave room for the suite: with a slow suite, raise it or expect a TIMEOUT status.",
        "/implement presents the concern items and gate_denials; workflow-result.json carries both.",
    ],
    "2.0.0": [
        "Review findings and QA defects have four parts: the problem, the concrete case (evidence or repro), the "
        "smallest fix and what happens if skipped. Both judges share severity anchors. No concrete case, no finding.",
        "The reviewer and QA report needs_validation: a claim the code alone cannot settle (a deployment or provider "
        "setting, code outside the diff). It has no severity, goes to the result's pending list and never blocks: "
        "the person checks the fact.",
        "Every report carries not_checked (what the agent did not check, skipped, or sees as a risk); the result "
        "lists them per agent call and /implement presents them.",
        "The common rules gain a reuse ladder (does it need to exist, is it in the repo, the standard library, an "
        "installed dependency, one line, then the minimum) and the rule to grep every caller before editing shared "
        "code. rules-sync refreshes .claude/rules/agentic/ on the next /implement.",
        "The test-auditor rejects tautological tests (an expected value recomputed the way the code computes it).",
        "The doc-updater and /architect invent no figures: counts, costs, timings and claims come from the code, the "
        "plan or a measurement, or are written as unverified or not measured. /architect sequences wide refactors as "
        "expand-contract.",
        "Agent files you customised keep the 1.x report shape: the launchers now require the new fields, so compare "
        "them with the new versions (.claude/state/upgrade/) and merge.",
    ],
}


def leftovers(shipped, present):
    """The template's leftovers to install: those already present, and the whole test suite of the flow (tests,
    fixtures, conformance) when any part of it is present, because a partial suite breaks the target's CI. The
    acceptance tests of each design check this repo's own docs, so they are only refreshed, never added."""
    suite = {rel for rel in shipped if rel.startswith(".claude/bin/") and "/test_acceptance" not in rel}
    return (set(present) | suite) if set(present) & suite else set(present)


def git(cwd, *args, **kw):
    opts = {"text": True} | kw
    if opts["text"]:  # only in text mode: an encoding would turn a text=False call back into text
        opts = {"encoding": "utf-8", "errors": "replace"} | opts
    try:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, **opts)
    except FileNotFoundError:
        return subprocess.CompletedProcess(args, 127, "", "")


def load_history(source_root):
    """{path: hashes} from source_root/.claude/flow-history.json; {} if missing or unreadable."""
    try:
        data = json.loads((Path(source_root) / ".claude" / "flow-history.json").read_text(encoding="utf-8"))
        return {path: set(hashes) for path, hashes in data["files"].items()}
    except (OSError, ValueError, KeyError, AttributeError, TypeError):
        return {}


def classify(dst_hash, src_hash, known, force):
    """"new", "unchanged", "updated" or "kept" for one file; known: the hashes that count as intact for its path."""
    if dst_hash is None:
        return "new"
    if dst_hash == src_hash:
        return "unchanged"
    return "updated" if force or dst_hash in known else "kept"


def version_key(version):
    """(major, minor, patch) of '1.1', 'v1.1.0' or '1.1.4-rc1'; None for anything else ('dev')."""
    m = re.fullmatch(r"v?(\d+(?:\.\d+){0,2})(?:[-+].*)?", str(version).strip())
    if not m:
        return None
    parts = tuple(int(p) for p in m.group(1).split("."))
    return parts + (0,) * (3 - len(parts))


def notes_between(from_version, to_version):
    """The notes of every version after from_version up to to_version, oldest first. An unknown from_version gets
    them all; an unknown to_version ("dev") has no upper bound."""
    low, high = version_key(from_version), version_key(to_version)
    return [note for v in sorted(NOTES, key=version_key)
            if (low is None or version_key(v) > low) and (high is None or version_key(v) <= high) for note in NOTES[v]]


def guard_problems(target, paths):
    """Reasons not to write in target: not a git repo, uncommitted changes to tracked files among paths, a run in
    progress. Untracked files never block: an intact one can be rebuilt from the history and an edited one is kept."""
    target = Path(target)
    if git(target, "rev-parse", "--is-inside-work-tree").stdout.strip() != "true":
        return ["not a git repository"]
    status = git(target, "status", "--porcelain", "--untracked-files=no", "--", *paths).stdout if paths else ""
    problems = [f"uncommitted: {line[3:].strip()}" for line in status.splitlines() if line.strip()]
    ledger_path = target / ".claude" / "state" / "ledger.json"
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        ledger = {}
    except (OSError, ValueError):  # unreadable or corrupt: it cannot tell whether a run is in progress
        problems.append("the run state cannot be read: stop any run and try again "
                        "(if none is running, delete .claude/state/ledger.json)")
        ledger = {}
    running = sorted(k for k, v in ledger.items()
                     if not k.startswith("_") and isinstance(v, dict) and v.get("state") == "running")
    if running:
        problems.append(f"a run is in progress ({', '.join(running)}): finish it or run "
                        "`python3 .claude/bin/agentic.py stop`")
    return problems


def require_full_history(root):
    """Raises ValueError unless root is a git work tree with its whole history (a shallow clone cannot answer)."""
    if git(root, "rev-parse", "--is-inside-work-tree").stdout.strip() != "true":
        raise ValueError("not a git repository")
    if git(root, "rev-parse", "--is-shallow-repository").stdout.strip() == "true":
        raise ValueError("needs the full git history: run `git fetch --unshallow` (or use a full clone)")


def matching_blobs(root, ref, globs):
    """[(path, blob)] of the files of ref whose path matches a glob."""
    found = []
    for line in git(root, "ls-tree", "-r", ref).stdout.splitlines():
        meta, path = line.split("\t", 1)
        _, kind, blob = meta.split()
        if kind == "blob" and any(fnmatch.fnmatch(path, g) for g in globs):
            found.append((path, blob))
    return found


def _hashes(root, blobs):
    """{blob: sha256 of its content with CRLF read as LF}, all read in one `git cat-file --batch` call."""
    blobs = sorted(set(blobs))
    out = git(root, "cat-file", "--batch", input="".join(b + "\n" for b in blobs).encode(), text=False).stdout
    digests, pos = {}, 0
    for blob in blobs:  # each answer: "<sha> blob <size>\n<content>\n"
        end = out.index(b"\n", pos)
        size = int(out[pos:end].split()[2])
        digests[blob] = hashlib.sha256(out[end + 1:end + 1 + size].replace(b"\r\n", b"\n")).hexdigest()  # as compat.content_hash
        pos = end + 1 + size + 1
    return digests


def build_history(repo_root, globs):
    """Every content each path matching globs ever had in repo_root's history, as sha256 lists."""
    root = Path(repo_root)
    require_full_history(root)
    paths_of = {}  # blob -> the matching paths that held it
    for commit in git(root, "rev-list", "HEAD").stdout.split():
        for path, blob in matching_blobs(root, commit, globs):
            paths_of.setdefault(blob, set()).add(path)
    files = {}
    for blob, digest in _hashes(root, paths_of).items():
        for path in paths_of[blob]:
            files.setdefault(path, set()).add(digest)
    head = git(root, "rev-parse", "HEAD").stdout.strip()
    return {"generated_from": head, "files": {p: sorted(h) for p, h in sorted(files.items())}}


def release_tags(repo_root):
    """The TAG_PREFIX tags of repo_root whose suffix is a version, oldest version first; [] without git or tags."""
    names = git(Path(repo_root), "tag", "-l", TAG_PREFIX + "*").stdout.split()
    tags = [name for name in names if version_key(name[len(TAG_PREFIX):]) is not None]
    return sorted(tags, key=lambda name: version_key(name[len(TAG_PREFIX):]))


def history_gaps(repo_root, globs, history, refs):
    """[{"ref", "path"}] for every file of each ref (in the order given, then by path) that matches a glob and whose
    content is not among history.get(path). Raises ValueError like build_history without git or the full history."""
    root = Path(repo_root)
    require_full_history(root)
    files = {ref: sorted(matching_blobs(root, ref, globs)) for ref in refs}
    digests = _hashes(root, [blob for found in files.values() for _, blob in found])
    return [{"ref": ref, "path": path} for ref in refs for path, blob in files[ref]
            if digests[blob] not in history.get(path, set())]


def release_notes(version):
    """NOTES[version] as a Markdown list, one "- note" line each. Raises ValueError when the version has none."""
    notes = NOTES.get(version)
    if not notes:
        raise ValueError(f"no upgrade notes for {version}")
    return "".join(f"- {note}\n" for note in notes)
