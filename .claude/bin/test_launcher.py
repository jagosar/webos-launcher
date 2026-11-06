"""Tests of the one-off launchers (launch.py: run-wave and its --fix sources) and the usage they record, with a fake
`claude`. Moved here from the retired per-release acceptance files (design 0017); each class names its origin.

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
from test_agentic import Repo, testutil  # noqa: E402


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
