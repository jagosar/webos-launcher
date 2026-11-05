"""Launching the agents: one `claude -p` per agent with its schema and permissions, the one-off run-* commands, the
workflow's seq, the fallback pipeline, pipeline-plan, and finish.
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
    ARCH, BASE_TOOLS, ENTRY, READ_ONLY_AGENTS, ROOT, STATE, USAGE,
    WORKER_MODEL, at_entity, child_env, codemap_path, die, docs_entity, entities, ledger_read, ledger_stage,
    ledger_update, load_config, now_hm, qa_entity, sibling, stage_of, status_to_state,
    usage_records, usage_summary, wps
)
from stage import check_stage, snapshot, stage_owners  # noqa: E402
from repo_scan import rule_files, rules_hint  # noqa: E402

TYPE_RE = re.compile(r"^(?:Type|Tipo):\s*(feature|change|fix|refactor)\b", re.M | re.I)

REVIEW_TOOLS = ["Read", "Glob", "Grep", "Bash(git diff:*)", "Bash(git status:*)", "Bash(git log:*)"]

REPORT_FILE_RE = re.compile(r"^(REVIEW|QA)-(\d+)\.json$")

REPORT_KEYS = {"REVIEW": "findings", "QA": "defects"}

# Ties in modification time: the one-off report, then run-pipeline's per-agent file, then the run record.
SOURCE_RANK = {"one-off": 2, "per-agent": 1, "record": 0}

SEQ_OUTPUT_CAP = 12_000

SEQ_COMMANDS = {"pipeline-plan", "briefs", "baseline", "open-stage", "close-stage", "verify", "codemap", "docs-check",
                "acceptance", "callers", "hooks-check"}

PIPELINE_STATE = STATE / "pipeline.json"

PIPELINE_SPEC = ROOT / ".claude" / "workflows" / "pipeline-spec.json"

FENCED_JSON_RE = re.compile(r"```json[ \t]*\n(.*?)\n```", re.S)


# ---------- launchers ----------

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
        with open(out_path, "w", encoding="utf-8") as out, \
                open(out_path.with_suffix(".log"), "w", encoding="utf-8") as err:
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
        (lambda j=j: run_agent(manifest, cfg, spec, j[1], j[0], j[0], j[2], STATE / "runs" / f"{j[0]}.json",
                               final=True))
        for j in jobs])
    finish(manifest, label, {j[0]: r["status"] for j, r in zip(jobs, runs)}, expected)

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

def render_prompt(manifest, name, ident=None, **values):
    brief = f".claude/state/briefs/{ident}.md" if ident else ""
    return pipeline_module().render(load_spec()["prompts"][name], wp=ident, brief=brief, rules=rules_hint(manifest),
                                    **values)

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
            die(f"{', '.join(over)} has used up its {cap} --fix cycles. "
                "This is not an implementation failure that yields "
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


# ---------- native workflow commands (run by the gatekeeper agent) ----------

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

def run_steps(groups, env=None):
    """Runs each command (an argv list) as its own process, in order, and stops at the first that fails.
    `env`: the steps' environment (default: this process's, with CLAUDE_PROJECT_DIR). Returns (ok, [{"cmd", "exit_code",
    "output"}])."""
    results, ok = [], True
    env = env if env is not None else dict(os.environ, CLAUDE_PROJECT_DIR=str(ROOT))
    for g in groups:
        r = subprocess.run([sys.executable, str(ENTRY), *g], cwd=ROOT, capture_output=True, text=True,
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

    def gate(cmds, label):
        ok, results = run_steps([shlex.split(c) for c in cmds], env=gate_env)
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

def pipeline_plan(manifest):
    """The plan the engines follow, as one JSON line."""
    waves = sorted({wp["wave"] for wp in wps(manifest).values()})
    plan = {"waves": waves, "acceptance": bool(at_entity(manifest)["command"]), "type": architecture_type(),
            "verify": sorted(manifest.get("verify", {})), "max_fix_cycles": int(load_config().get("max_fix_cycles", 2)),
            "agents": {wp["id"]: wp.get("agent", "implementer") for wp in wps(manifest).values()},
            "baseline": bool(manifest.get("verify")),
            "docs": bool(docs_entity(manifest)), "codemap": codemap_path(manifest), "rules": rule_files(manifest),
            "wave_of": {wp["id"]: wp["wave"] for wp in wps(manifest).values()},
            "work_packages": {str(w): sorted(wp["id"] for wp in stage_owners(manifest, str(w))) for w in waves}}
    print(json.dumps(plan))
