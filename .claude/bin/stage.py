"""Stages: the snapshot taken when one opens, the check of what changed when it closes (ownership, git state, the
content guardrails), open-stage/close-stage, the fix diffs, and the stop gate that refuses a report whose acceptance
tests fail.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compat  # noqa: E402  - the operating-system layer, next to this file
from core import (  # noqa: E402
    ARCHIVE_DIR, BASELINE, CLAIMS, GATE, GATE_MAX_DENIALS, GATE_MAX_LINES, GATE_STATUSES, GATE_TIMEOUT_S,
    HOOK_SEEN, LEDGER_STATES, ManifestError, PROTECTED_CONFIG_RE, ROOT, SECRET_RES, SKIP_MARKER_RE, STATE,
    TEST_PATH_RE, WP_ID_RE, at_entity, codemap_path, die, docs_entity, entities, flow_path_refused, ignored,
    ledger_read, ledger_stage, ledger_update, load_manifest, locked_json, maintainer_mode, owns, qa_entity,
    review_entity, sibling, stage_of, wps
)
from verification import acceptance_check, attribute, normal_failure, run_command  # noqa: E402

# Caches and artefacts that test and build tools generate: they do not count as changes.
DEFAULT_IGNORE = ["__pycache__/", "*.pyc", ".pytest_cache/", ".mypy_cache/", ".ruff_cache/",
                  "node_modules/", ".coverage", "coverage/", ".nyc_output/", ".DS_Store", ".tsbuildinfo"]

STAGE_LABELS = {"qa": qa_entity, "acceptance": at_entity, "review": review_entity, "docs": docs_entity}

MAX_DIFF_BYTES = 200_000


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
    hooks = (sorted(p for p in (git_dir / "hooks").glob("*") if p.is_file())
             if git_dir and (git_dir / "hooks").is_dir() else [])
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
            print(f"git state changed during the stage: {', '.join(moved)} "
                  "(no agent commits or touches .git or the baseline)")
            ok = False
    if changed and not stage_guarded(label):
        print(f"GUARDRAIL HOOK SAW NO FLOW AGENT: {len(changed)} file(s) changed in stage {label} "
              "and no hook call came "
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

def stage_guarded(label):
    """True if an agent of the flow went through the hook since the stage opened."""
    seen = locked_json(HOOK_SEEN, lambda s: None)
    guarded, opened = seen.get("guarded_ts"), STATE / f"gitstate-{label}.json"
    if guarded is None:
        return False
    return not opened.exists() or guarded >= opened.stat().st_mtime


# ---------- open and close ----------

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
        ledger_update(jid, state=state, stage=stage_of(jid), pid=None,
                      note=state.upper() + (" (fix)" if fix else ""), **extra)
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
