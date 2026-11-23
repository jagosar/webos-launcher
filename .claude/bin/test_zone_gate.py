"""Unit cases of the stop gate's zone check (design 0029, issue 134): the quality violations of one zone, and what
the gate does at its edges. What measures files runs in a temporary repo; the rest patches the baseline's reader.

Run: python3 -m unittest discover -s .claude/bin -p test_zone_gate.py
"""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stage  # noqa: E402
import verification  # noqa: E402
from test_agentic import MANIFEST, Repo  # noqa: E402

LONG_LINE = "x = '" + "a" * 125 + "'\n"
LONG_FUNCTION = "def test_everything():\n" + "    x = 1\n" * 120
ONE_FILE_ZONE = dict(MANIFEST, acceptance={"files": ["e2e/smoke.py"], "command": "true"})


class ZoneCase(unittest.TestCase):
    def project(self, manifest=MANIFEST):
        repo = Repo(manifest)
        self.addCleanup(repo.cleanup)
        repo.write("src/a.py", "x = 1\n")
        self.assertEqual(repo.run("quality", "--init").returncode, 0)
        return repo

    def report(self, repo, owner, status="DONE"):
        payload = {"tool_name": "StructuredOutput", "tool_input": {"status": status}}
        return repo.run("hook", stdin=json.dumps(payload), env={"AGENTIC_WP": owner})


class RefusalTests(ZoneCase):
    def test_a_function_over_its_limit_is_named_with_its_detail(self):
        repo = self.project()
        repo.write("acceptance/test_big.py", LONG_FUNCTION)
        r = self.report(repo, "AT")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("FAIL function acceptance/test_big.py::test_everything exceeds its lines allowance", r.stderr)
        self.assertIn("acceptance/test_big.py::test_everything lines: now 121, allowed 100", r.stderr)

    def test_a_zone_declared_as_one_file_is_only_that_file(self):
        repo = self.project(ONE_FILE_ZONE)
        repo.write("e2e/other.py", LONG_LINE)
        self.assertEqual(self.report(repo, "AT").returncode, 0)
        repo.write("e2e/smoke.py", LONG_LINE)
        r = self.report(repo, "AT")
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("e2e/other.py", r.stderr)

    def test_a_denial_shows_at_most_the_gate_s_lines(self):
        repo = self.project()
        for n in range(30):  # 30 files: 60 lines of violations and details
            repo.write(f"acceptance/test_{n:02d}.py", LONG_LINE)
        r = self.report(repo, "AT")
        self.assertEqual(r.returncode, 2)
        shown = [line for line in r.stderr.splitlines() if "acceptance/test_" in line]
        self.assertEqual(len(shown), 40)
        last = json.loads((repo.dir / ".claude/state/gate.json").read_text(encoding="utf-8"))["AT"]["last"]
        self.assertEqual(len(last), 40)

    def test_files_that_are_not_code_are_not_measured(self):
        repo = self.project()
        repo.write("acceptance/notes.md", "a" * 500 + "\n")
        repo.write("acceptance/data.json", '{"k": "' + "a" * 500 + '"}\n')
        self.assertEqual(self.report(repo, "AT").returncode, 0)


class LetThroughTests(unittest.TestCase):
    def test_owners_without_a_zone_and_unfinished_reports_cost_no_measurement(self):
        with mock.patch.object(stage, "zone_quality_lines", side_effect=AssertionError("measured")):
            for owner, status in (("WP-01", "DONE"), ("REVIEW", "PASS"), ("DOCS", "DONE"), (None, "DONE"),
                                  ("AT", "BLOCKER"), ("AT", "PASS"), ("QA", "BLOCKER"), ("QA", "DONE"),
                                  ("AT", None), ("AT", 5)):
                self.assertIsNone(stage.zone_gate_check(owner, status), (owner, status))

    def test_any_error_inside_the_check_lets_the_report_through(self):
        for error in (TypeError("bad limits"), AttributeError("bad files"), KeyError("k"), RuntimeError("x")):
            with mock.patch.object(stage, "zone_quality_lines", side_effect=error), \
                    mock.patch.object(stage, "gate_denials", return_value={}), \
                    mock.patch.object(stage, "load_manifest", return_value={}):
                self.assertIsNone(stage.zone_gate_check("AT", "DONE"), error)

    def test_no_baseline_or_an_unreadable_one_has_no_violations(self):
        for state in (None, {"unreadable": True}):
            with mock.patch.object(verification, "quality_state", return_value=state):
                self.assertEqual(verification.zone_quality_lines({}, "AT"), [])
        with mock.patch.object(verification, "quality_state", return_value={}):
            self.assertEqual(verification.zone_quality_lines({}, "WP-01"), [])


if __name__ == "__main__":
    unittest.main()
