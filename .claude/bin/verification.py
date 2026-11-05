"""Verification: manifest commands run without a shell, the acceptance check, the baseline and verify phases with their
failure attribution, and the clean-code ratchet (quality.py) as a command and as a phase.
"""
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compat  # noqa: E402  - the operating-system layer, next to this file
from core import (  # noqa: E402
    BASELINE, ENTRY, QUALITY_BASELINE, ROOT, STATE, VERIFY_PHASES, at_entity, child_env, die, is_flow_repo,
    ledger_stage, load_config, sibling, source_files, symbol_re, wps
)

# A phase that already failed at baseline is compared failure by failure: the lines that name a failure, without the
# durations that change from run to run. A line not seen at baseline is a regression.
FAILURE_LINE_RE = re.compile(r"(?i)\b(?:fail(?:ed|ure|ures|ing|s)?|errors?)\b|[✕✗×❌]")

DURATION_RE = re.compile(r"\(?\b\d+(?:\.\d+)?\s?(?:ms|s|sec|secs|seconds)\b\)?|\b0x[0-9a-fA-F]+\b")  # and addresses

MAX_TAIL_LINE = 300  # a failure line can be thousands of characters long; the JSON report keeps it short


# ---------- commands and acceptance ----------

def run_command(cmd, timeout=900, scan=None):
    """Runs a manifest command without a shell. Returns (code, last lines of output). With a list in `scan`, the
    last 200 lines are appended to it, for attributing a failure to the packages that own what it mentions."""
    try:
        # Its own process group: on timeout the whole tree is ended (`npm test` would otherwise leave `vitest` running).
        proc = subprocess.Popen(compat.resolve(shlex.split(cmd)), cwd=ROOT, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
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


# ---------- the clean-code ratchet (quality.py) ----------

def quality_module():
    return sibling("quality")

def quality_state():
    """The quality baseline; None when missing or foreign (copied from the template into another repo);
    {"unreadable": True} when it is not a JSON object."""
    path = ROOT / QUALITY_BASELINE
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"unreadable": True}
    if not isinstance(data, dict):
        return {"unreadable": True}
    if data.get("flow_repo") is True and not is_flow_repo(ROOT):
        return None
    return data

def quality_files(state=None):
    """The code files the ratchet measures. The flow's own scripts only count in the flow's repo: in a project they
    are installed code, not the project's to keep clean."""
    files = [f for f in source_files() if symbol_re(f) is not None]
    if is_flow_repo(ROOT):
        own = [(".claude/bin", "*.py"), (".claude/workflows", "*.js")]
        files += [p.relative_to(ROOT).as_posix() for folder, pattern in own for p in (ROOT / folder).glob(pattern)]
    exclude = (state or {}).get("exclude") or []
    return sorted({f for f in files if not quality_module().excluded(f, exclude)})

def quality_measure(state=None):
    q = quality_module()
    return q.measure(ROOT, quality_files(state), q.limits_of(state))

def quality_check(state):
    """(ok, output lines, summary): each violation followed by its detail line."""
    if state.get("unreadable"):
        line = f"FAIL quality baseline unreadable: {QUALITY_BASELINE}"
        return False, [line], {"ok": False, "files": 0, "functions": 0, "violations": 1, "improvable": []}
    current = quality_measure(state)
    result = quality_module().check(state, current)
    lines = [row for pair in zip(result["violations"], result["details"]) for row in pair]
    summary = {"ok": result["ok"], "files": len(current["files"]), "functions": len(current["functions"]),
               "violations": len(result["violations"]), "improvable": result["improvable"]}
    return result["ok"], lines, summary

def quality_phase():
    """The `quality` phase of baseline and verify, run in this process (a frozen guard measures with its frozen
    quality.py); None without a baseline."""
    state = quality_state()
    if state is None:
        return None
    ok, lines, _ = quality_check(state)
    return {"name": "quality", "command": "agentic.py quality", "exit": 0 if ok else 3, "passed": ok,
            "tail": [row[:MAX_TAIL_LINE] for row in lines[-8:]], "_scan": lines[-200:]}

def write_quality_baseline(data):
    (ROOT / QUALITY_BASELINE).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

def quality_init(state):
    if state is not None and not state.get("unreadable"):
        print(f"{QUALITY_BASELINE} already exists: lower it with --update, or edit it by hand.", file=sys.stderr)
        return 1
    data = quality_module().initial(quality_measure(), None, is_flow_repo(ROOT))
    write_quality_baseline(data)
    print(json.dumps({"ok": True, "created": QUALITY_BASELINE, "functions": len(data["functions"]),
                      "files": len(data["files"])}))
    return 0

def quality(args):
    """quality [--init | --update]: the ratchet's check (exit 0 or 3), its creation, or its lowering."""
    if args not in ([], ["--init"], ["--update"]):
        die("Uso: quality [--init | --update]")
    state = quality_state()
    if args == ["--init"]:
        return quality_init(state)
    if state is None and not args:
        print(json.dumps({"ok": True, "skipped": "no quality baseline: run agentic.py quality --init"}))
        return 0
    if args == ["--update"]:
        if state is None or state.get("unreadable"):
            print(f"{QUALITY_BASELINE} is missing or unreadable: create it with --init.", file=sys.stderr)
            return 1
        write_quality_baseline(quality_module().tighten(state, quality_measure(state)))
        state = quality_state()
    ok, lines, summary = quality_check(state)
    for row in lines:
        print(row)
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if ok else 3

def install_quality_baseline(target, dry_run):
    """Creates the target's ratchet when it has none, or has the template's; keeps a project's own. Returns what
    happened, for install's report."""
    path = target / QUALITY_BASELINE
    foreign = False
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            foreign = isinstance(data, dict) and data.get("flow_repo") is True and not is_flow_repo(target)
        except (OSError, ValueError):
            pass
        if not foreign:
            return "kept"
    if dry_run:
        return "would be replaced (copied from the template)" if foreign else "would be created"
    r = subprocess.run([sys.executable, str(ENTRY), "quality", "--init"], cwd=target,
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=dict(os.environ, CLAUDE_PROJECT_DIR=str(target)))
    if r.returncode != 0:
        return f"could not be created ({(r.stderr.strip().splitlines() or ['no detail'])[-1]})"
    return "replaced (copied from the template)" if foreign else "created"


# ---------- baseline and verify ----------

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
        phases.append({"name": name, "command": cfg[name], "exit": code, "passed": code == 0, "tail": tail,
                       "_scan": scan})
    extra = quality_phase()
    if extra:
        phases.append(extra)
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
    if not manifest.get("verify") and quality_state() is None:
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
    saved = json.loads(BASELINE.read_text(encoding="utf-8")).get("phases", []) if BASELINE.exists() else []
    base = {p["name"]: p for p in saved}
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
