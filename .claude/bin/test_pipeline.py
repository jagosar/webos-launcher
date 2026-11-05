"""Tests of the fallback engine (pipeline.py, `agentic.py run-pipeline`) and of its parity with the native workflow.

Run: python3 -m unittest discover -s .claude/bin -p "test_*.py"
Conformance: the scenarios of conformance.json run through both engines (this one in Python, the workflow in Node with
simulated agents) and must produce exactly the same gate commands, agent calls, prompts and result.
End to end: `run-pipeline` against a temporary git repo with a fake `claude`; no real calls.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_agentic import PY, SCRIPT, Repo, testutil

BIN = SCRIPT.parent
WORKFLOWS = SCRIPT.parents[1] / "workflows"
sys.path.insert(0, str(BIN))
import pipeline  # noqa: E402

CONFORMANCE = json.loads((BIN / "conformance.json").read_text(encoding="utf-8"))
SPEC = json.loads((WORKFLOWS / "pipeline-spec.json").read_text(encoding="utf-8"))


def world(decl):
    """The simulated world of one scenario: the same defaults and overrides that test_workflow.mjs builds."""
    state = {"gate_calls": 0, "gates": [], "calls": [], "verifies": 0, "reviews": 0, "qas": 0, "audits": 0}
    base = CONFORMANCE["plan"]

    def text(o):
        return o if isinstance(o, str) else json.dumps(o)

    def pick(items, n):
        return items[min(n, len(items)) - 1]

    def respond(cmd):
        state["gates"].append(cmd)
        for f in decl.get("gate_fail", []):
            if cmd.startswith(f["prefix"]):
                return {"exit_code": f["exit_code"], "output": text(f["output"])}
        if cmd == "verify" and decl.get("verify"):
            state["verifies"] += 1
            v = pick(decl["verify"], state["verifies"])
            if v:
                return {"exit_code": v["exit_code"], "output": text(v["output"])}
        if cmd.startswith("pipeline-plan"):
            return {"exit_code": 0, "output": json.dumps({**base, **decl.get("plan", {})})}
        if cmd == "verify":
            return {"exit_code": 0, "output": json.dumps({"ok": True, "blocking": [], "preexisting_failures": [], "suspects": {},
                                                          "phases": [{"name": "tests", "passed": True}]})}
        if cmd == "baseline":
            return {"exit_code": 0, "output": json.dumps({"phases": [{"name": "tests", "passed": True}], "preexisting_failures": []})}
        if cmd == "codemap":
            return {"exit_code": 0, "output": json.dumps({"path": "docs/CODEMAP.md", "files": 3, "changed": True})}
        if cmd == "docs-check":
            return {"exit_code": 0, "output": json.dumps({"ok": True, "checked": 2, "broken": []})}
        if cmd == "callers":
            return {"exit_code": 0, "output": json.dumps({"changed": [], "symbols": {}, "callers": [], "truncated": False})}
        return {"exit_code": 0, "output": "RESULTADO: OK"}

    def gate(cmds, label):
        state["gate_calls"] += 1
        if any(c.startswith(prefix) for prefix in decl.get("gate_lost", []) for c in cmds):
            raise RuntimeError("gatekeeper returned nothing")  # a timeout or a dead agent
        steps = []
        for c in cmds:
            r = respond(c)
            steps.append({"cmd": c, "exit_code": r["exit_code"], "output": r["output"]})
            if r["exit_code"] != 0:
                break
        return {"steps": steps}

    reports = decl.get("reports") or {}
    calls = {}

    def override(report, key):
        extra = reports.get(key)
        return {**report, **extra} if extra else report

    def agent(prompt, agent_type, label, ident):
        state["calls"].append((agent_type, label, prompt))
        if agent_type == "test-author":
            return decl.get("test_author") or {"status": "DONE", "coverage": [{"ac": "AC-001", "tests": ["test_ac_001"]}], "blocker": None}
        if agent_type == "test-auditor":
            state["audits"] += 1
            return pick(decl["audit"], state["audits"]) if decl.get("audit") else {"status": "PASS", "findings": []}
        if agent_type == "implementer":
            wp = label.split(" ")[0]
            base = {"status": "DONE", "wp": wp, "files": [], "checks": [], "internal_decisions": [], "blocker": None,
                    "not_checked": [], "concerns": [], "missing_context": None}
            extra = (reports.get("implementer") or {}).get(wp)
            if isinstance(extra, list):  # one override per call of that package: the first, then the last
                calls[wp] = calls.get(wp, 0) + 1
                extra = pick(extra, calls[wp])
            return {**base, **extra} if extra else base
        if agent_type == "reviewer":
            state["reviews"] += 1
            return override(pick(decl["review"], state["reviews"]) if decl.get("review") else {"status": "PASS", "findings": []}, "reviewer")
        if agent_type == "verifier":
            return decl.get("verifier") or {"is_real": True, "confidence": 0.9, "reasoning": "it holds"}
        if agent_type == "qa":
            state["qas"] += 1
            return override(pick(decl["qa"], state["qas"]) if decl.get("qa") else {"status": "PASS", "plan": ["contracts"], "defects": [], "gaps": []}, "qa")
        if agent_type == "doc-updater":
            return decl.get("docs") or {"status": "DONE", "files": ["README.md", "CHANGELOG.md"],
                                        "changes": ["usage of the new command"], "unverified": [], "blocker": None}
        raise AssertionError(f"unexpected agent type {agent_type}")

    return state, gate, agent


def summarize(result, state):
    seen, prompts, agents = {}, {}, []
    for agent_type, label, prompt in state["calls"]:
        key = f"{agent_type}:{label}"
        seen[key] = seen.get(key, 0) + 1
        prompts[f"{key}#{seen[key]}"] = prompt
        agents.append(key)
    return {"result": result["result"], "failed_stage": result.get("failed_stage"), "detail": result.get("detail"),
            "fix_cycles": result["fix_cycles"],
            "pending": sorted(p.get("type") for p in result.get("pending", [])), "gates": state["gates"],
            "gate_calls": state["gate_calls"], "agents": sorted(agents), "prompts": prompts,
            "not_checked": sorted(f"{e['agent']}:{e['label']}" for e in result.get("not_checked", [])),
            "gate_denials": result.get("gate_denials", {}),
            "review_callers": (result.get("review") or {}).get("callers", 0),
            "verification_failures": len(result.get("verification_failures") or [])}


def run_scenario(decl):
    state, gate, agent = world(decl)
    engine = pipeline.Pipeline(SPEC, gate, agent, parallel=lambda thunks: [t() for t in thunks],
                               start_wave=(decl.get("args") or {}).get("start_wave"))
    return summarize(engine.execute(), state)


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class ConformanceTests(unittest.TestCase):
    """The two engines must do exactly the same in every scenario."""

    @classmethod
    def setUpClass(cls):
        r = subprocess.run(["node", str(BIN / "test_workflow.mjs"), "--dump"], capture_output=True, text=True, encoding="utf-8", errors="replace")
        assert r.returncode == 0, r.stdout + r.stderr
        cls.workflow = json.loads(r.stdout)

    def test_every_scenario_is_the_same_in_both_engines(self):
        self.assertEqual(set(self.workflow), {d["name"] for d in CONFORMANCE["scenarios"]})
        for decl in CONFORMANCE["scenarios"]:
            with self.subTest(scenario=decl["name"]):
                mine, theirs = run_scenario(decl), self.workflow[decl["name"]]
                for key in ("result", "failed_stage", "detail", "fix_cycles", "pending", "gates", "gate_calls", "agents", "not_checked", "gate_denials", "review_callers", "verification_failures"):
                    self.assertEqual(mine[key], theirs[key], key)
                self.assertEqual(mine["prompts"], theirs["prompts"], "the agents must receive the same prompts")

    def test_the_spec_has_everything_the_agents_are_asked_for(self):
        for name in ("test-author", "test-auditor", "implementer", "reviewer", "verifier", "qa", "doc-updater", "gatekeeper"):
            self.assertIn(name, SPEC["schemas"])
        for name, text in SPEC["prompts"].items():
            if name in ("implement", "fix", "test_author", "test_author_revision", "test_auditor"):
                self.assertIn("{brief}", text, f"{name} must point to the agent's brief")
        for name in ("implement", "fix", "fix_retry", "test_author", "test_author_revision", "test_auditor", "reviewer",
                     "verifier", "qa", "doc_updater"):
            self.assertIn(name, SPEC["prompts"])


class EngineTests(unittest.TestCase):
    def test_new_scenarios_end_as_described(self):
        by_name = {d["name"]: d for d in CONFORMANCE["scenarios"]}
        needs = run_scenario(by_name["needs validation goes to pending and does not block"])
        self.assertEqual((needs["result"], needs["pending"]), ("OK", ["needs validation", "needs validation"]))
        collected = run_scenario(by_name["not_checked is collected from every report"])
        self.assertEqual(collected["not_checked"], ["implementer:WP-02", "reviewer:reviewer"])
        self.assertEqual(run_scenario(by_name["gate denials are summed from close-stage"])["gate_denials"], {"WP-01": 1})
        concerns = run_scenario(by_name["concerns go to pending and to the reviewer"])
        self.assertEqual((concerns["result"], concerns["pending"]), ("OK", ["concern"]))
        self.assertIn("may spin", concerns["prompts"]["reviewer:reviewer#1"])
        once = run_scenario(by_name["needs context is re-dispatched once with the whole document"])
        self.assertEqual((once["result"], once["agents"].count("implementer:WP-02 (context)")), ("OK", 1))
        twice = run_scenario(by_name["needs context twice is a blocker"])
        self.assertEqual((twice["result"], twice["failed_stage"], twice["agents"].count("implementer:WP-02 (context)")),
                         ("REVIEW", "wave 1", 1))
        in_fix = run_scenario(by_name["needs context in a fix wave is a blocker"])
        self.assertEqual((in_fix["result"], in_fix["failed_stage"]), ("REVIEW", "fix wave 1"))
        self.assertFalse([a for a in in_fix["agents"] if "(context)" in a])
        reached = run_scenario(by_name["callers reach the reviewer"])
        self.assertEqual(reached["review_callers"], 1)
        self.assertIn("src/b/x.py", reached["prompts"]["reviewer:reviewer#1"])
        ignored = run_scenario(by_name["callers that are not JSON are ignored"])
        self.assertEqual(ignored["review_callers"], 0)
        self.assertNotIn("Callers", ignored["prompts"]["reviewer:reviewer#1"])

    def test_halted_is_not_swallowed_like_an_agent_error(self):
        def agent(prompt, agent_type, label, ident):
            raise pipeline.Halted()
        state, gate, _ = world({})
        result = pipeline.Pipeline(SPEC, gate, agent, parallel=lambda ts: [t() for t in ts]).execute()
        self.assertEqual(result["failed_stage"], "stopped")

    def test_an_agent_that_blows_up_is_a_missing_report(self):
        state, gate, _ = world({})

        def agent(prompt, agent_type, label, ident):
            raise RuntimeError("boom")
        result = pipeline.Pipeline(SPEC, gate, agent, parallel=lambda ts: [t() for t in ts]).execute()
        self.assertEqual(result["failed_stage"], "wave 0", "the test-author returned nothing")

    def test_a_gate_that_blows_up_stops_at_prepare(self):
        def gate(cmds, label):
            raise OSError("no process")
        result = pipeline.Pipeline(SPEC, gate, lambda *a: None).execute()
        self.assertEqual(result["failed_stage"], "prepare")

    def test_structured_report_formats(self):
        import agentic
        schema = {"required": ["status"]}
        tmp = Path(tempfile.mkdtemp(prefix="report-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        cases = {
            "structured_output": ({"structured_output": {"status": "DONE"}}, {"status": "DONE"}),
            "json text in result": ({"result": '{"status": "PASS"}'}, {"status": "PASS"}),
            "object in result": ({"result": {"status": "PASS"}}, {"status": "PASS"}),
            "prose": ({"result": "STATUS: DONE"}, None),
            # seen in a real run (CLI 2.1.316): no structured_output, the report as a fenced block after the prose
            "fenced json after prose": ({"result": 'Done.\n\n```json\n{"status": "DONE",\n "x": [1]}\n```'},
                                        {"status": "DONE", "x": [1]}),
            "last fenced block wins": ({"result": '```json\n{"status": "FAIL"}\n```\nfix\n```json\n{"status": "DONE"}\n```'},
                                       {"status": "DONE"}),
            "broken fenced json": ({"result": '```json\n{"status": \n```'}, None),
            "missing required key": ({"structured_output": {"wp": "WP-01"}}, None),
        }
        for name, (data, expected) in cases.items():
            with self.subTest(case=name):
                f = tmp / "r.json"
                f.write_text(json.dumps(data), encoding="utf-8")
                self.assertEqual(agentic.structured_report(f, schema), expected)
        f.write_text("not json", encoding="utf-8")
        self.assertIsNone(agentic.structured_report(f, schema))
        self.assertIsNone(agentic.structured_report(tmp / "missing.json", schema))



def raw_run(decl, agent_extra=None):
    """The whole result of one scenario in the Python engine; agent_extra(agent_type, label) adds report fields."""
    state, gate, base_agent = world(decl)

    def agent(prompt, agent_type, label, ident):
        report = base_agent(prompt, agent_type, label, ident)
        extra = agent_extra(agent_type, label) if agent_extra else None
        return {**report, **extra} if extra else report

    return pipeline.Pipeline(SPEC, gate, agent, parallel=lambda ts: [t() for t in ts],
                             start_wave=(decl.get("args") or {}).get("start_wave")).execute()


def scenario(name):
    return next(d for d in CONFORMANCE["scenarios"] if d["name"] == name)


class SpecSchemaTests(unittest.TestCase):
    """From test_acceptance_v2 ac_001 and test_acceptance_v21 ac_005: what the shared spec makes agents report."""

    def test_judges_report_needs_validation_and_findings_have_four_parts(self):
        schemas = SPEC["schemas"]
        reviewer, qa = schemas["reviewer"], schemas["qa"]
        self.assertIn("needs_validation", reviewer["required"])
        self.assertIn("needs_validation", qa["required"])
        self.assertTrue({"fix", "if_skipped"} <= set(reviewer["properties"]["findings"]["items"]["required"]))
        self.assertTrue({"fix", "if_skipped"} <= set(qa["properties"]["defects"]["items"]["required"]))
        for owner in (reviewer, qa):
            item = owner["properties"]["needs_validation"]["items"]
            self.assertEqual(set(item["required"]), {"wp", "file", "kind", "claim", "fact", "how_to_check"})
            self.assertEqual(item["properties"]["kind"]["enum"], ["external", "unchanged_code"])

    def test_writers_report_what_they_did_not_check(self):
        for name in ("implementer", "test-author", "test-auditor"):
            with self.subTest(agent=name):
                self.assertIn("not_checked", SPEC["schemas"][name]["required"])
        self.assertNotIn("needs_validation", SPEC["schemas"]["test-auditor"]["required"])

    def test_the_implementer_has_exactly_four_statuses(self):
        implementer = SPEC["schemas"]["implementer"]
        self.assertEqual(implementer["properties"]["status"]["enum"],
                         ["DONE", "DONE_WITH_CONCERNS", "NEEDS_CONTEXT", "BLOCKED"])
        self.assertTrue({"concerns", "missing_context"} <= set(implementer["required"]))


class EngineRecordTests(unittest.TestCase):
    """The records the Python engine keeps. From test_acceptance_v2 ac_002/ac_003, test_acceptance_v21 ac_004,
    test_acceptance_v23 ac_003 and test_acceptance_v24 ac_005."""

    def test_needs_validation_becomes_pending_without_severity_and_keeps_its_line(self):
        item = {"wp": "WP-01", "file": "src/a.py", "line": 3, "kind": "external", "claim": "c", "fact": "f",
                "how_to_check": "h"}
        qa_item = dict(item, wp="WP-02", line=None, kind="unchanged_code", severity="critical")
        extra = {"reviewer": {"needs_validation": [item], "not_checked": []}, "qa": {"needs_validation": [qa_item]}}
        result = raw_run({}, lambda agent_type, label: extra.get(agent_type))
        self.assertEqual((result["result"], result["fix_cycles"]), ("OK", 0))
        items = [p for p in result["pending"] if p.get("type") == "needs validation"]
        self.assertEqual([p["source"] for p in items], ["reviewer", "qa"])
        for entry in items:
            self.assertNotIn("severity", entry)
            self.assertTrue({"claim", "fact", "how_to_check", "kind", "wp", "file"} <= set(entry))
        self.assertEqual(items[0]["line"], 3)

    def test_not_checked_entries_carry_their_items(self):
        extra = {("reviewer", "reviewer"): {"not_checked": ["load"]}, ("implementer", "WP-02"): {"not_checked": ["x"]}}
        result = raw_run({}, lambda agent_type, label: extra.get((agent_type, label.split(" ")[0])))
        self.assertIn({"agent": "implementer", "label": "WP-02", "items": ["x"]}, result["not_checked"])
        self.assertIn({"agent": "reviewer", "label": "reviewer", "items": ["load"]}, result["not_checked"])

    def test_statuses_map_to_close_stage_and_prompts(self):
        concern = run_scenario(scenario("concerns go to pending and to the reviewer"))
        self.assertTrue(any(g.startswith("close-stage 1 WP-01=done") for g in concern["gates"]), concern["gates"])
        context = run_scenario(scenario("needs context is re-dispatched once with the whole document"))
        self.assertIn("architecture.md, whole", context["prompts"]["implementer:WP-02 (context)#1"])
        self.assertTrue(any("WP-02=done" in g for g in context["gates"]))
        twice = run_scenario(scenario("needs context twice is a blocker"))
        self.assertTrue(any("WP-02=blocker" in g for g in twice["gates"]))
        in_fix = run_scenario(scenario("needs context in a fix wave is a blocker"))
        self.assertTrue(any(g.startswith("close-stage 1 WP-01=blocker --fix") for g in in_fix["gates"]))
        concerns = [p for p in raw_run(scenario("concerns go to pending and to the reviewer"))["pending"]
                    if p.get("type") == "concern"]
        self.assertEqual([(c.get("severity"), c["wp"]) for c in concerns], [(None, "WP-01")])

    def test_a_failed_verification_is_recorded_with_its_cause(self):
        result = raw_run(scenario("verification fails on a package: it is fixed and verified again"))
        first = result["verification_failures"][0]
        self.assertEqual((first["fix_cycle"], first["blocking"]), (0, ["tests"]))
        self.assertIn("WP-02", first["suspects"])
        self.assertEqual(first["failing"][0]["name"], "tests")

    def test_a_dead_hook_stops_the_run_before_any_agent(self):
        s = run_scenario(scenario("the guardrail hook is not running"))
        self.assertEqual((s["result"], s["failed_stage"], s["gates"], s["agents"]),
                         ("REVIEW", "prepare", ["hooks-check"], []))
        self.assertIn("guardrail hook is not running", s["detail"])

E2E_MANIFEST = {
    "allow_bash": ["Bash(python3 -m unittest:*)"],
    "acceptance": {"files": ["acceptance/"], "command": f"{PY} acc.py"},
    "work_packages": [
        {"id": "WP-01", "wave": 1, "depends_on": [], "files": ["src/a.py"], "acs": ["AC-001"]},
        {"id": "WP-02", "wave": 1, "depends_on": [], "files": ["src/b/"]},
        {"id": "WP-03", "wave": 2, "depends_on": ["WP-01"], "files": ["src/c.py"]},
    ],
}


class RunPipelineEndToEndTests(unittest.TestCase):
    """`agentic.py run-pipeline` with its real gate commands, over a temporary repo, with a fake `claude`."""

    def setUp(self):
        self.repo = Repo(E2E_MANIFEST)
        self.bin = Path(tempfile.mkdtemp(prefix="fake-claude-"))
        self.logs = Path(tempfile.mkdtemp(prefix="fake-logs-"))
        self.repo.env.update(AGENTIC_CLAUDE=testutil.install_fake_claude(self.bin), FAKE_LOG_DIR=str(self.logs),
                             FAKE_WRITE="1", FAKE_MODE="nousage")
        # red until the three packages exist
        self.repo.write("acc.py", "import os, sys\nsys.exit(0 if all(os.path.exists(p) for p in ('src/a.py', 'src/b/x.py', 'src/c.py')) else 1)\n")
        subprocess.run(["git", "add", "-A"], cwd=self.repo.dir, check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"], cwd=self.repo.dir, check=True)

    def tearDown(self):
        self.repo.cleanup()
        shutil.rmtree(self.bin, ignore_errors=True)
        shutil.rmtree(self.logs, ignore_errors=True)

    def calls(self):
        return testutil.fake_calls(self.logs)

    def saved(self):
        return json.loads((self.repo.dir / ".claude/state/runs/workflow-result.json").read_text(encoding="utf-8"))

    def test_a_whole_run_goes_to_ok(self):
        r = self.repo.run("run-pipeline")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("RESULT: OK", r.stdout)
        result = self.saved()
        self.assertEqual(result["result"], "OK")
        self.assertEqual(result["fix_cycles"], 0)
        self.assertEqual(result["test_audit"]["rounds"], 1)
        order = [c["agent"] for c in self.calls()]
        self.assertEqual(order[0], "test-author")
        self.assertEqual(order[1], "test-auditor")
        self.assertEqual(sorted(order[2:4]), ["implementer", "implementer"])
        self.assertEqual(order[4:], ["implementer", "reviewer", "qa"], "wave 2, then the judges")
        state = json.loads((self.repo.dir / ".claude/state/pipeline.json").read_text(encoding="utf-8"))
        self.assertEqual(state["completed_waves"], [1, 2])
        ledger = json.loads((self.repo.dir / ".claude/state/ledger.json").read_text(encoding="utf-8"))
        self.assertTrue(all(ledger[i]["state"] in ("done", "pass") for i in ("WP-01", "WP-02", "WP-03", "AT", "QA", "REVIEW")), ledger)

    def test_every_agent_runs_with_its_schema_identity_and_tools(self):
        self.repo.run("run-pipeline")
        by = {}
        for c in self.calls():
            by.setdefault(c["agent"], c)
        for name, call in by.items():
            argv = call["argv"]
            self.assertEqual(json.loads(argv[argv.index("--json-schema") + 1]), SPEC["schemas"][name], name)
            self.assertEqual(argv[argv.index("--model") + 1], "sonnet")
            self.assertIn("--strict-mcp-config", argv)
        self.assertEqual(by["test-author"]["id"], "AT")
        self.assertEqual(by["test-auditor"]["id"], "REVIEW", "it runs as the read-only entity")
        tools = by["test-auditor"]["argv"][by["test-auditor"]["argv"].index("--allowedTools") + 1:]
        self.assertEqual(tools[:3], ["Read", "Glob", "Grep"])
        self.assertNotIn("Write", tools[:4])

    def test_a_write_outside_ownership_stops_the_wave(self):
        r = self.repo.run("run-pipeline", env={"FAKE_ROGUE": "1"})
        self.assertEqual(r.returncode, 3, r.stdout)
        result = self.saved()
        self.assertEqual(result["result"], "REVIEW")
        self.assertEqual(result["failed_stage"], "wave 1")
        self.assertFalse(any(c["id"] == "WP-03" for c in self.calls()), "wave 2 is not launched")

    def test_resume_continues_after_the_waves_that_finished(self):
        self.repo.run("run-pipeline", env={"FAKE_ROGUE": "1"})  # wave 1 fails: nothing completed
        state = self.repo.dir / ".claude/state/pipeline.json"
        self.assertFalse(state.exists() and json.loads(state.read_text(encoding="utf-8"))["completed_waves"])
        state.write_text(json.dumps({"completed_waves": [1]}), encoding="utf-8")
        for f in self.logs.glob("call-*.json"):
            f.unlink()
        self.repo.write("src/a.py")
        self.repo.write("src/b/x.py")
        r = self.repo.run("run-pipeline", "--resume")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual([c["id"] for c in self.calls() if c["agent"] == "implementer"], ["WP-03"], "only wave 2 runs")
        self.assertNotIn("test-author", [c["agent"] for c in self.calls()], "no wave 0 again")

    def test_a_confirmed_review_finding_goes_through_a_fix_cycle(self):
        # The real close-stage: a failed review is a judgement, not a write outside ownership.
        r = self.repo.run("run-pipeline", env={"FAKE_REVIEW_FINDING": "WP-01"})
        result = self.saved()
        self.assertEqual((result["result"], result.get("failed_stage"), result["fix_cycles"]), ("OK", None, 1), r.stdout)
        self.assertIn("WP-01 (fix)", [c["prompt"].split(".")[0].replace("Fix ", "") + " (fix)" for c in self.calls()
                                      if c["prompt"].startswith("Fix ")])

    def test_a_blocking_qa_defect_goes_through_a_fix_cycle(self):
        r = self.repo.run("run-pipeline", env={"FAKE_QA_DEFECT": "WP-02"})
        result = self.saved()
        self.assertEqual((result["result"], result.get("failed_stage"), result["fix_cycles"]), ("OK", None, 1), r.stdout)
        self.assertTrue(any(c["prompt"].startswith("Fix WP-02.") for c in self.calls()))

    def test_usage_errors(self):
        self.assertEqual(self.repo.run("run-pipeline", "--wave", "2").returncode, 1)
        self.assertEqual(self.repo.run("run-pipeline", "--start-wave", "x").returncode, 1)


    def run_with(self, **env):
        self.repo.env.update(env)
        r = self.repo.run("run-pipeline")
        self.assertIn("RESULT: OK", r.stdout, r.stdout + r.stderr)
        return self.saved()

    def test_reports_not_checked_and_needs_validation_reach_the_record(self):
        """From test_acceptance_v2 ac_003."""
        saved = self.run_with(FAKE_NOT_CHECKED="1", FAKE_NEEDS_VALIDATION="WP-01")
        self.assertGreaterEqual(len(saved["not_checked"]), 5)
        self.assertEqual(len([p for p in saved["pending"] if p.get("type") == "needs validation"]), 1)

    def test_concerns_reach_the_record_and_nothing_is_denied(self):
        """From test_acceptance_v21 ac_005."""
        saved = self.run_with(FAKE_CONCERNS="1")
        concerns = [p for p in saved["pending"] if p.get("type") == "concern"]
        self.assertEqual(sorted(c["wp"] for c in concerns), ["WP-01", "WP-02", "WP-03"])
        self.assertEqual(saved["gate_denials"], {})

    def test_launched_from_a_session_whose_hook_is_dead_it_still_runs(self):
        """From test_acceptance_v24 ac_009: the gates check the hook statically, not by heartbeat."""
        live = self.repo.run("hooks-check", env={"CLAUDECODE": "1"})
        self.assertEqual(live.returncode, 3, "no heartbeat: a live check fails")
        self.assertEqual(self.run_with(CLAUDECODE="1")["result"], "OK")

    def test_the_record_carries_usage_and_implementers_read_their_brief(self):
        """From test_acceptance ac_010."""
        saved = self.run_with(FAKE_MODE="structured")
        agents = [c for c in self.calls()]
        self.assertEqual(len(saved["usage"]["by_agent"]), len(agents))
        self.assertEqual(saved["usage"]["total"]["output"], 50 * len(agents))
        for call in (c for c in agents if c["agent"] == "implementer"):
            self.assertIn(f".claude/state/briefs/{call['id']}.md", call["prompt"])

if __name__ == "__main__":
    unittest.main()
