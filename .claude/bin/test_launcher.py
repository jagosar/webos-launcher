"""Tests of the launchers (launch.py: run-wave and its --fix sources, run-review, the fallback engine's agent callable
and the plan's configuration) and the usage they record, with a fake `claude`. Moved here from the retired
per-release acceptance files (designs 0017 and 0021); each class names its origin.

Run: python3 -m unittest discover -s .claude/bin -p "test_launcher.py"
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import launch  # noqa: E402
from test_agentic import MANIFEST, Repo, testutil  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def finding(problem, severity="high", wp="WP-02"):
    return {"severity": severity, "wp": wp, "file": "src/b/x.py", "line": 1, "problem": problem, "evidence": "e",
            "fix": "f", "if_skipped": "s"}


def defect(repro, severity="high", wp="WP-02", kind="implementation"):
    return {"id": "D1", "severity": severity, "wp": wp, "kind": kind, "section": "6", "repro": repro, "fix": "f",
            "if_skipped": "s"}


def structured(report):
    return json.dumps({"structured_output": report})


class LauncherCase(unittest.TestCase):
    """A temporary project whose agents are the fake `claude`; runs/ files get increasing modification times."""

    def setUp(self):
        self.repo = Repo()
        self.addCleanup(self.repo.cleanup)
        self.bin = Path(tempfile.mkdtemp(prefix="fake-claude-"))
        self.logs = Path(tempfile.mkdtemp(prefix="fake-logs-"))
        self.addCleanup(shutil.rmtree, self.bin, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.logs, ignore_errors=True)
        self.repo.env.update(AGENTIC_CLAUDE=testutil.install_fake_claude(self.bin), FAKE_LOG_DIR=str(self.logs))
        self.runs = self.repo.dir / ".claude" / "state" / "runs"
        self.runs.mkdir(parents=True, exist_ok=True)
        self.clock = time.time() - 1000

    def put(self, name, text):
        path = self.runs / name
        path.write_text(text, encoding="utf-8")
        self.clock += 10
        os.utime(path, (self.clock, self.clock))

    def clear(self):
        shutil.rmtree(self.runs)
        self.runs.mkdir(parents=True)
        for name in ("ledger.json", "ledger.md"):
            (self.repo.dir / ".claude" / "state" / name).unlink(missing_ok=True)

    def calls(self, ident=None):
        return testutil.fake_calls(self.logs, ident) if ident else testutil.fake_calls(self.logs)

    def fix(self, *extra):
        return self.repo.run("run-wave", "1", "WP-02", *extra)

    def prompt(self):
        return self.calls("WP-02")[-1]["prompt"]

    def ledger(self):
        return json.loads((self.repo.dir / ".claude/state/ledger.json").read_text(encoding="utf-8"))


class RunWaveTests(LauncherCase):
    """From test_acceptance ac_007 and ac_008 and test_acceptance_portable ac_008."""

    def test_the_prompt_goes_on_stdin_never_in_an_argument(self):
        r = self.repo.run("run-wave", "1", env={"FAKE_MODE": "structured"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        calls = self.calls()
        self.assertEqual(sorted(c["id"] for c in calls), ["WP-01", "WP-02"])
        for call in calls:
            self.assertFalse(any("\n" in arg for arg in call["argv"]), call["argv"])
            self.assertIn("--json-schema", call["argv"])

    def test_an_agent_that_answers_in_text_is_an_error_with_no_report(self):
        r = self.repo.run("run-wave", "1", env={"FAKE_MODE": "text"})
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertEqual(self.ledger()["WP-01"]["state"], "error")
        self.assertIn("NO REPORT", self.ledger()["WP-01"]["note"])

    def test_fix_for_a_package_with_no_defect_starts_no_agent(self):
        self.put("REVIEW.json", structured({"status": "FAIL", "findings": [finding("p")]}))
        r = self.repo.run("run-wave", "1", "WP-01", "--fix")
        self.assertEqual(r.returncode, 1)
        self.assertIn("WP-01: no blocking defect from review or QA is attributed to it.", r.stderr)
        self.assertEqual(self.calls(), [])


class UsageTests(LauncherCase):
    """From test_acceptance ac_010: every agent's usage is recorded and summed by status."""

    def test_each_agent_appends_a_usage_record_and_status_sums_them(self):
        self.assertEqual(self.repo.run("run-wave", "1", env={"FAKE_MODE": "structured"}).returncode, 0)
        usage = self.repo.dir / ".claude/state/usage.jsonl"
        records = [json.loads(line) for line in usage.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(records), 2)
        self.assertIn({"id": "WP-01", "agent": "implementer", "label": "WP-01", "input": 120, "cache_read": 300,
                       "output": 50, "cost_usd": 0.01, "turns": 3}, records)
        out = self.repo.run("status").stdout
        self.assertIn("## Usage", out)
        self.assertIn("Total: in 240", out)

    def test_missing_usage_fields_are_zero_and_an_unknown_cost(self):
        self.assertEqual(self.repo.run("run-wave", "1", env={"FAKE_MODE": "nousage"}).returncode, 0)
        line = (self.repo.dir / ".claude/state/usage.jsonl").read_text(encoding="utf-8").splitlines()[0]
        record = json.loads(line)
        self.assertEqual((record["input"], record["cache_read"], record["output"], record["cost_usd"]),
                         (0, 0, 0, None))
        self.assertIn("$?", self.repo.run("status").stdout)


class FixSourceTests(LauncherCase):
    """From test_acceptance_v23 ac_001 and ac_002: which report --fix reads, and --all."""

    def test_the_run_record_gives_confirmed_findings_never_refuted_ones(self):
        self.put("workflow-result.json", json.dumps({"result": "REVIEW", "review": {
            "confirmed": [finding("confirmed one")], "refuted": [finding("refuted one")], "advisory": []},
            "qa": {"status": "PASS", "defects": []}}))
        r = self.fix("--fix")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("confirmed one", self.prompt())
        self.assertNotIn("refuted one", self.prompt())

    def test_only_a_reviewer_shaped_review_file_counts(self):
        self.put("REVIEW-7.json", structured({"status": "FAIL", "findings": [finding("reviewer seven")],
                                              "needs_validation": [], "not_checked": []}))
        self.put("REVIEW-9.json", structured({"is_real": True, "confidence": 0.9, "reasoning": "a verifier"}))
        self.put("REVIEW-2.json", structured({"status": "FAIL", "findings": [finding("auditor two")],
                                              "not_checked": []}))
        r = self.fix("--fix")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("reviewer seven", self.prompt())
        self.assertNotIn("auditor two", self.prompt())

    def test_all_adds_non_blocking_defects_but_not_design_ones(self):
        self.put("workflow-result.json", json.dumps({"result": "REVIEW", "review": {
            "confirmed": [], "refuted": [], "advisory": [finding("medium one", "medium")]},
            "qa": {"status": "FAIL", "defects": [defect("low one", "low"),
                                                 defect("design one", "high", kind="design")]}}))
        r = self.fix("--fix")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no blocking defect", r.stderr)
        self.assertIn("workflow-result.json", r.stderr)
        self.assertEqual(self.fix("--fix", "--all").returncode, 0)
        self.assertIn("medium one", self.prompt())
        self.assertIn("low one", self.prompt())
        self.assertNotIn("design one", self.prompt())
        self.assertEqual(self.fix("--fix", "--all").returncode, 0)
        self.assertEqual(self.fix("--fix", "--all").returncode, 4, "the fix-cycle cap applies with --all too")

    def test_all_needs_fix_and_a_missing_report_names_its_sources(self):
        r = self.fix("--all")
        self.assertEqual(r.returncode, 1)
        self.assertIn("--fix [--all]", r.stderr)
        r = self.fix("--fix")
        self.assertEqual(r.returncode, 1)
        self.assertIn("run-pipeline, run-review or run-qa", r.stderr)


RULE = "No magic numbers"


class ReviewAxesLaunchTests(LauncherCase):
    """From test_acceptance_v213 ac_003, ac_006 and ac_007: what a fix takes from each axis, the rules the one-off
    commands print, and run-review starting both reviewers."""

    def reports(self, repo):
        floor = dict(finding("FLOOR-ONE"), tier="floor", provenance="read")
        rule_hit = dict(finding("REFLEX-ONE"), tier="reflex", rule=RULE, provenance="read")
        note = dict(rule_hit, tier="finish", problem="FINISH-ONE")
        repo.write(".claude/state/runs/REVIEW.json", structured({
            "status": "FAIL", "findings": [floor], "needs_validation": [], "not_checked": []}))
        repo.write(".claude/state/runs/STANDARDS.json", structured({
            "status": "FAIL", "axis": "standards", "findings": [rule_hit, note], "not_checked": []}))

    def test_a_fix_takes_floor_and_reflex_and_a_waiver_drops_the_reflex(self):
        self.reports(self.repo)
        r = self.fix("--fix")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for word in ("FLOOR-ONE", "REFLEX-ONE"):
            self.assertIn(word, self.prompt())
        self.assertNotIn("FINISH-ONE", self.prompt())
        manifest = json.loads(json.dumps(MANIFEST))
        manifest["waivers"] = [{"rule": RULE, "scope": "src/b/", "reason": "ana: constants of the tax table"}]
        waived = Repo(manifest)
        self.addCleanup(waived.cleanup)
        shutil.copytree(ROOT / ".claude/rules-library", waived.dir / ".claude/rules-library")
        waived.env.update(self.repo.env, CLAUDE_PROJECT_DIR=str(waived.dir))
        self.reports(waived)
        for args in (["--fix"], ["--fix", "--all"]):
            r = waived.run("run-wave", "1", "WP-02", *args)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("FLOOR-ONE", self.prompt())
            self.assertNotIn("REFLEX-ONE", self.prompt(), args)

    def test_the_plan_and_run_wave_print_the_rules(self):
        import findings
        repo = Repo(dict(MANIFEST, stack=["python"]))
        self.addCleanup(repo.cleanup)
        shutil.copytree(ROOT / ".claude/rules-library", repo.dir / ".claude/rules-library")
        self.assertEqual(repo.run("rules-sync").returncode, 0)
        plan = json.loads(repo.run("pipeline-plan").stdout.splitlines()[-1])
        text = plan["rules_text"]
        self.assertTrue(text.startswith("\nRules you must follow (common and language). They are printed here, "
                                        "so do not read them from disk:"), text[:200])
        self.assertIn("--- .claude/rules/agentic/common.md ---", text)
        self.assertIn(RULE, text)
        self.assertNotIn("paths:", text)
        self.assertEqual(plan["rules_sum"], findings.text_sum(text))
        repo.env.update(self.repo.env, CLAUDE_PROJECT_DIR=str(repo.dir))
        self.assertEqual(repo.run("run-wave", "1").returncode, 0)
        self.assertIn(RULE, self.calls("WP-01")[-1]["prompt"])
        self.assertEqual(json.loads(self.repo.run("pipeline-plan").stdout.splitlines()[-1])["rules_text"], "")

    def test_run_review_starts_both_reviewers(self):
        r = self.repo.run("run-review")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sorted(c["agent"] for c in self.calls()), ["reviewer", "standards-reviewer"])
        for name in ("REVIEW.json", "STANDARDS.json"):
            self.assertTrue((self.runs / name).is_file(), name)
        self.assertIn("REVIEW:", r.stdout)
        self.assertIn("STANDARDS:", r.stdout)
        self.assertIn("REVIEW", self.ledger())
        self.assertIn("STANDARDS", self.ledger())


class ResumingAgentTests(unittest.TestCase):
    """launch.resuming: the fallback engine's agent callable, over a recorded launch."""

    def setUp(self):
        self.launched, self.answers = [], []

    def launch(self, prompt, agent_type, label, ident, **opts):
        self.launched.append((ident, opts))
        report, sid = self.answers.pop(0) if self.answers else ({"status": "DONE"}, f"s-{len(self.launched)}")
        return {"report": report, "status": "DONE" if report else "NO REPORT", "usage": {}, "session_id": sid}

    def test_a_fix_attempt_continues_its_own_package_session(self):
        agent = launch.resuming(self.launch, {"WP-01", "WP-02"})
        agent("p", "implementer", "WP-01", "WP-01")
        agent("p", "implementer", "WP-02", "WP-02")
        agent("p", "implementer", "WP-01 (fix)", "WP-01", resume=True)
        self.assertEqual(self.launched[2], ("WP-01", {"resume": "s-1", "model": None, "schema_name": None}))

    def test_without_a_session_or_with_a_model_the_call_is_fresh(self):
        agent = launch.resuming(self.launch, {"WP-01"})
        agent("p", "implementer", "WP-01 (fix)", "WP-01", resume=True)
        agent("p", "implementer", "WP-01 (fix)", "WP-01", model="opus")
        agent("p", "reviewer", "reviewer (re-review)", "REVIEW", schema="re-review")
        self.assertEqual([o for _, o in self.launched], [
            {"model": None, "schema_name": None}, {"model": "opus", "schema_name": None},
            {"model": None, "schema_name": "re-review"}])

    def test_a_resumed_call_with_no_report_is_retried_fresh_once(self):
        agent = launch.resuming(self.launch, {"WP-01"})
        agent("p", "implementer", "WP-01", "WP-01")
        self.answers = [(None, "s-x"), (None, None)]
        self.assertIsNone(agent("p", "implementer", "WP-01 (fix)", "WP-01", resume=True))
        self.assertEqual([("resume" in o) for _, o in self.launched], [False, True, False])

    def test_only_package_sessions_are_kept(self):
        agent = launch.resuming(self.launch, {"WP-01"})
        agent("p", "reviewer", "reviewer", "REVIEW")
        agent("p", "reviewer", "reviewer", "REVIEW", resume=True)
        self.assertNotIn("resume", self.launched[1][1])


class PlanConfigTests(unittest.TestCase):
    """pipeline-plan's escalation_model and max_agent_calls, and the configuration it refuses."""

    def plan(self, config=None):
        repo = Repo()
        self.addCleanup(repo.cleanup)
        if config is not None:
            repo.write(".claude/agentic.json", json.dumps(config))
        return repo.run("pipeline-plan")

    def test_the_defaults_escalate_and_have_no_budget(self):
        plan = json.loads(self.plan().stdout.splitlines()[-1])
        self.assertEqual((plan["escalation_model"], plan["max_agent_calls"]), ("opus", None))

    def test_a_budget_and_escalation_off_reach_the_plan(self):
        plan = json.loads(self.plan({"max_agent_calls": 1, "escalation": False}).stdout.splitlines()[-1])
        self.assertEqual((plan["escalation_model"], plan["max_agent_calls"]), (None, 1))

    def test_bad_values_are_refused(self):
        for config in ({"max_agent_calls": 0}, {"max_agent_calls": -3}, {"max_agent_calls": 2.5},
                       {"max_agent_calls": False}, {"escalation": None}, {"escalation": 1}):
            with self.subTest(config=config):
                r = self.plan(config)
                self.assertEqual(r.returncode, 1, r.stdout)
                self.assertIn("agentic.json: ", r.stderr)
                self.assertEqual(r.stdout, "")



class ByHandPlanTests(unittest.TestCase):
    """From test_acceptance_v210 ac_002 and ac_003: a plan for the flow itself ("maintainer": true) is executed by
    hand; every command that starts agents refuses it before writing anything."""

    def flow_repo(self):
        manifest = {"maintainer": True, "acceptance": {"files": ["acceptance/"], "command": "python3 acc.py"},
                    "work_packages": [{"id": "WP-01", "wave": 1, "depends_on": [],
                                       "files": [".claude/bin/x.py", "src/a.py"]}]}
        repo = Repo(manifest)
        self.addCleanup(repo.cleanup)
        repo.write(".claude-plugin/plugin.json", json.dumps({"name": "agentic-flow"}))
        repo.write("README.md", "<!-- agentic-flow-template -->\n# flow\n")
        return repo

    def test_every_agent_command_refuses_before_writing(self):
        repo = self.flow_repo()
        fake = repo.dir / "fake_claude.py"
        fake.write_text(f"open({str(repo.dir / 'MARKER')!r}, 'w').close()\nprint('{{}}')\n", encoding="utf-8")
        env = {"AGENTIC_CLAUDE": f"{sys.executable} {fake}"}
        for args in (["pipeline-plan"], ["run-wave", "1"], ["run-review"], ["run-acceptance"], ["run-pipeline"]):
            with self.subTest(args=args):
                r = repo.run(*args, env=env)
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                self.assertIn("executed by hand", r.stderr)
        state = repo.dir / ".claude/state"
        self.assertFalse((repo.dir / "MARKER").exists(), "no agent was started")
        for leftover in ("briefs", "runs", "guard"):
            self.assertFalse((state / leftover).exists(), leftover)
        self.assertFalse(list(state.glob("snapshot-*.json")) if state.exists() else [])

if __name__ == "__main__":
    unittest.main()
