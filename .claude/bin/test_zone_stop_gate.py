"""Tests of design 0029 (issue 134, agentic-flow 2.21.0), moved from its acceptance file when 2.22.0 shipped: the stop
gate also holds the test-author and QA to the quality limits of the files of their own zone. A finished report is
refused with the violation lines, at most twice, and never for somebody else's files, an unfinished report or a
baseline that cannot be read.

Run: python3 -m unittest discover -s .claude/bin -p test_zone_stop_gate.py
The real hook runs in temporary repos with a real quality baseline. Nothing is spent.
"""
import json
import sys
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parent
sys.path.insert(0, str(BIN))
from test_agentic import MANIFEST, Repo  # noqa: E402

LONG_LINE = "x = '" + "a" * 125 + "'\n"  # 131 characters: over the default limit of 120
ACCEPTANCE_FILE = "acceptance/test_money.py"
QA_FILE = "qa/test_qa.py"
BASELINE = ".claude/quality-baseline.json"
GATE = ".claude/state/gate.json"


class GateCase(unittest.TestCase):
    def project(self, before_baseline=None, baseline=True):
        """A repo of the default manifest with a quality baseline; `before_baseline`: files the baseline records."""
        repo = Repo(MANIFEST)
        self.addCleanup(repo.cleanup)
        repo.write("acc.py", "raise SystemExit(0)\n")
        repo.write("src/a.py", "x = 1\n")
        for rel, text in (before_baseline or {}).items():
            repo.write(rel, text)
        if baseline:
            self.assertEqual(repo.run("quality", "--init").returncode, 0)
        return repo

    def report(self, repo, owner, status):
        payload = {"tool_name": "StructuredOutput", "tool_input": {"status": status}}
        r = repo.run("hook", stdin=json.dumps(payload), env={"AGENTIC_WP": owner})
        self.assertNotIn("Traceback", r.stderr)
        return r

    def gate(self, repo):
        path = repo.dir / GATE
        return json.loads(path.read_text(encoding="utf-8") or "{}") if path.exists() else {}


class TestAuthorGateTests(GateCase):
    def test_ac_001_a_finished_report_is_refused_while_a_file_of_the_zone_is_over_a_limit(self):
        repo = self.project()
        repo.write(ACCEPTANCE_FILE, LONG_LINE)
        r = self.report(repo, "AT", "DONE")
        self.assertEqual(r.returncode, 2, r.stderr)
        for words in ("AT: the report says DONE", "over the quality limits", "denial 1 of 2",
                      f"FAIL file {ACCEPTANCE_FILE} exceeds its long_lines allowance",
                      "without changing what they check"):
            self.assertIn(words, r.stderr)
        self.assertEqual(self.gate(repo)["AT"]["denials"], 1)

    def test_ac_002_a_clean_zone_an_unfinished_report_and_other_files_do_not_refuse(self):
        repo = self.project()
        repo.write(ACCEPTANCE_FILE, "x = 1\n")
        self.assertEqual(self.report(repo, "AT", "DONE").returncode, 0, "the zone is within the limits")
        repo.write(ACCEPTANCE_FILE, LONG_LINE)
        self.assertEqual(self.report(repo, "AT", "BLOCKER").returncode, 0, "the report is not a finished one")
        repo.write(ACCEPTANCE_FILE, "x = 1\n")
        for rel in ("src/a.py", QA_FILE):
            repo.write(rel, LONG_LINE)
        self.assertEqual(self.report(repo, "AT", "DONE").returncode, 0, "those files are not the test-author's")
        self.assertNotIn("AT", self.gate(repo))

    def test_ac_003_two_denials_at_most_and_a_stage_that_opens_starts_again(self):
        repo = self.project()
        repo.write(ACCEPTANCE_FILE, LONG_LINE)
        first, second, third = (self.report(repo, "AT", "DONE") for _ in range(3))
        self.assertEqual((first.returncode, second.returncode, third.returncode), (2, 2, 0))
        self.assertIn("denial 1 of 2", first.stderr)
        self.assertIn("denial 2 of 2", second.stderr)
        repo.run("open-stage", "acceptance")
        again = self.report(repo, "AT", "DONE")
        self.assertEqual(again.returncode, 2)
        self.assertIn("denial 1 of 2", again.stderr)


class QaGateTests(GateCase):
    def test_ac_004_qa_is_held_to_its_own_zone(self):
        repo = self.project()
        repo.write(QA_FILE, LONG_LINE)
        passed = self.report(repo, "QA", "PASS")
        self.assertEqual(passed.returncode, 2, passed.stderr)
        for words in ("QA: the report says PASS", "denial 1 of 2", f"FAIL file {QA_FILE} exceeds"):
            self.assertIn(words, passed.stderr)
        failed = self.report(repo, "QA", "FAIL")
        self.assertEqual(failed.returncode, 2)
        self.assertIn("denial 2 of 2", failed.stderr)
        repo.run("open-stage", "qa")
        self.assertEqual(self.report(repo, "QA", "BLOCKER").returncode, 0)

    def test_ac_004_qa_is_not_refused_for_the_acceptance_zone(self):
        repo = self.project()
        repo.write(ACCEPTANCE_FILE, LONG_LINE)
        self.assertEqual(self.report(repo, "QA", "PASS").returncode, 0)
        self.assertNotIn("QA", self.gate(repo))


class FailOpenTests(GateCase):
    def test_ac_005_without_a_baseline_or_with_a_broken_one_nothing_is_refused(self):
        for text in (None, "[1]", '{"limits": {"line_chars": "x"}}'):
            with self.subTest(baseline=text):
                repo = self.project()
                repo.write(ACCEPTANCE_FILE, LONG_LINE)
                if text is None:
                    (repo.dir / BASELINE).unlink()
                else:
                    (repo.dir / BASELINE).write_text(text, encoding="utf-8")
                self.assertEqual(self.report(repo, "AT", "DONE").returncode, 0)
                self.assertNotIn("AT", self.gate(repo))

    def test_ac_005_what_the_baseline_already_allows_is_not_refused(self):
        repo = self.project(before_baseline={ACCEPTANCE_FILE: LONG_LINE})
        self.assertEqual(self.report(repo, "AT", "DONE").returncode, 0)
        self.assertNotIn("AT", self.gate(repo))


class PackageGateTests(GateCase):
    def test_ac_006_a_package_and_the_reviewer_are_checked_as_before(self):
        repo = self.project()
        repo.write(ACCEPTANCE_FILE, LONG_LINE)
        self.assertEqual(self.report(repo, "WP-01", "DONE").returncode, 0, "its acceptance tests pass")
        self.assertEqual(self.report(repo, "REVIEW", "PASS").returncode, 0)
        self.assertEqual(self.gate(repo), {})


if __name__ == "__main__":
    unittest.main()
