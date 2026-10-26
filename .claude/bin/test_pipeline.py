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
import textwrap
import unittest
from pathlib import Path

from test_agentic import SCRIPT, Repo

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
        if cmd == "pipeline-plan":
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
        return {"exit_code": 0, "output": "RESULTADO: OK"}

    def gate(cmds, label):
        state["gate_calls"] += 1
        steps = []
        for c in cmds:
            r = respond(c)
            steps.append({"cmd": c, "exit_code": r["exit_code"], "output": r["output"]})
            if r["exit_code"] != 0:
                break
        return {"steps": steps}

    def agent(prompt, agent_type, label, ident):
        state["calls"].append((agent_type, label, prompt))
        if agent_type == "test-author":
            return decl.get("test_author") or {"status": "DONE", "coverage": [{"ac": "AC-001", "tests": ["test_ac_001"]}], "blocker": None}
        if agent_type == "test-auditor":
            state["audits"] += 1
            return pick(decl["audit"], state["audits"]) if decl.get("audit") else {"status": "PASS", "findings": []}
        if agent_type == "implementer":
            return {"status": "DONE", "wp": label.split(" ")[0], "files": [], "checks": [], "internal_decisions": [], "blocker": None}
        if agent_type == "reviewer":
            state["reviews"] += 1
            return pick(decl["review"], state["reviews"]) if decl.get("review") else {"status": "PASS", "findings": []}
        if agent_type == "verifier":
            return decl.get("verifier") or {"is_real": True, "confidence": 0.9, "reasoning": "it holds"}
        if agent_type == "qa":
            state["qas"] += 1
            return pick(decl["qa"], state["qas"]) if decl.get("qa") else {"status": "PASS", "plan": ["contracts"], "defects": [], "gaps": []}
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
    return {"result": result["result"], "failed_stage": result.get("failed_stage"), "fix_cycles": result["fix_cycles"],
            "pending": sorted(p.get("type") for p in result.get("pending", [])), "gates": state["gates"],
            "gate_calls": state["gate_calls"], "agents": sorted(agents), "prompts": prompts}


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
        r = subprocess.run(["node", str(BIN / "test_workflow.mjs"), "--dump"], capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
        cls.workflow = json.loads(r.stdout)

    def test_every_scenario_is_the_same_in_both_engines(self):
        self.assertEqual(set(self.workflow), {d["name"] for d in CONFORMANCE["scenarios"]})
        for decl in CONFORMANCE["scenarios"]:
            with self.subTest(scenario=decl["name"]):
                mine, theirs = run_scenario(decl), self.workflow[decl["name"]]
                for key in ("result", "failed_stage", "fix_cycles", "pending", "gates", "gate_calls", "agents"):
                    self.assertEqual(mine[key], theirs[key], key)
                self.assertEqual(mine["prompts"], theirs["prompts"], "the agents must receive the same prompts")

    def test_the_spec_has_everything_the_agents_are_asked_for(self):
        for name in ("test-author", "test-auditor", "implementer", "reviewer", "verifier", "qa", "doc-updater", "gatekeeper"):
            self.assertIn(name, SPEC["schemas"])
        for name in ("implement", "fix", "fix_retry", "test_author", "test_author_revision", "test_auditor", "reviewer",
                     "verifier", "qa", "doc_updater"):
            self.assertIn(name, SPEC["prompts"])


class EngineTests(unittest.TestCase):
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
            "missing required key": ({"structured_output": {"wp": "WP-01"}}, None),
        }
        for name, (data, expected) in cases.items():
            with self.subTest(case=name):
                f = tmp / "r.json"
                f.write_text(json.dumps(data))
                self.assertEqual(agentic.structured_report(f, schema), expected)
        f.write_text("not json")
        self.assertIsNone(agentic.structured_report(f, schema))
        self.assertIsNone(agentic.structured_report(tmp / "missing.json", schema))


FAKE_CLAUDE = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json, os, sys
    argv = sys.argv[1:]
    agent = argv[argv.index("--agent") + 1]
    ident = os.environ.get("AGENTIC_WP")
    with open(os.path.join(os.environ["FAKE_LOG_DIR"], "calls.jsonl"), "a") as f:
        f.write(json.dumps({"agent": agent, "id": ident, "argv": argv}) + "\\n")
    def write(rel):
        os.makedirs(os.path.dirname(rel) or ".", exist_ok=True)
        open(rel, "w").write("x = 1\\n")
    report = {}
    if agent == "implementer":
        write({"WP-01": "src/a.py", "WP-02": "src/b/x.py", "WP-03": "src/c.py"}[ident])
        if os.environ.get("FAKE_ROGUE") and ident == "WP-02":
            write("other.py")  # a file nobody owns
        report = {"status": "DONE", "wp": ident, "files": [], "checks": [], "internal_decisions": [], "blocker": None}
    elif agent == "test-author":
        report = {"status": "DONE", "coverage": [{"ac": "AC-001", "tests": ["t"]}], "blocker": None}
    elif agent in ("test-auditor", "reviewer"):
        report = {"status": "PASS", "findings": []}
    elif agent == "qa":
        report = {"status": "PASS", "plan": [], "defects": [], "gaps": []}
    print(json.dumps({"type": "result", "structured_output": report}))
""")

E2E_MANIFEST = {
    "allow_bash": ["Bash(python3 -m unittest:*)"],
    "acceptance": {"files": ["acceptance/"], "command": "python3 acc.py"},
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
        (self.bin / "claude").write_text(FAKE_CLAUDE)
        (self.bin / "claude").chmod(0o755)
        self.logs = Path(tempfile.mkdtemp(prefix="fake-logs-"))
        self.repo.env.update(PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}", FAKE_LOG_DIR=str(self.logs))
        # red until the three packages exist
        self.repo.write("acc.py", "import os, sys\nsys.exit(0 if all(os.path.exists(p) for p in ('src/a.py', 'src/b/x.py', 'src/c.py')) else 1)\n")
        (self.repo.dir / ".claude" / "workflows").mkdir(parents=True)
        shutil.copy(WORKFLOWS / "pipeline-spec.json", self.repo.dir / ".claude" / "workflows" / "pipeline-spec.json")
        subprocess.run(["git", "add", "-A"], cwd=self.repo.dir, check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"], cwd=self.repo.dir, check=True)

    def tearDown(self):
        self.repo.cleanup()
        shutil.rmtree(self.bin, ignore_errors=True)
        shutil.rmtree(self.logs, ignore_errors=True)

    def calls(self):
        return [json.loads(line) for line in (self.logs / "calls.jsonl").read_text().splitlines()]

    def saved(self):
        return json.loads((self.repo.dir / ".claude/state/runs/workflow-result.json").read_text())

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
        state = json.loads((self.repo.dir / ".claude/state/pipeline.json").read_text())
        self.assertEqual(state["completed_waves"], [1, 2])
        ledger = json.loads((self.repo.dir / ".claude/state/ledger.json").read_text())
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
        self.assertFalse(state.exists() and json.loads(state.read_text())["completed_waves"])
        state.write_text(json.dumps({"completed_waves": [1]}))
        (self.logs / "calls.jsonl").unlink()
        self.repo.write("src/a.py")
        self.repo.write("src/b/x.py")
        r = self.repo.run("run-pipeline", "--resume")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual([c["id"] for c in self.calls() if c["agent"] == "implementer"], ["WP-03"], "only wave 2 runs")
        self.assertNotIn("test-author", [c["agent"] for c in self.calls()], "no wave 0 again")

    def test_usage_errors(self):
        self.assertEqual(self.repo.run("run-pipeline", "--wave", "2").returncode, 1)
        self.assertEqual(self.repo.run("run-pipeline", "--start-wave", "x").returncode, 1)


if __name__ == "__main__":
    unittest.main()
