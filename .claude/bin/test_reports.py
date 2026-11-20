"""Tests of reports.py: the failing tests of JUnit and NUnit reports, and the declared report paths.

Run: python3 -m unittest discover -s .claude/bin -p "test_reports.py"
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

BIN = Path(__file__).resolve().parent
sys.path.insert(0, str(BIN))
import reports  # noqa: E402

FIXTURES = BIN / "fixtures" / "reports"


class FailedTestsTests(unittest.TestCase):
    def temp(self):
        d = Path(tempfile.mkdtemp(prefix="reports-"))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        return d

    def write(self, text):
        path = self.temp() / "r.xml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_junit_failures_and_errors_in_document_order(self):
        tests = reports.failed_tests(FIXTURES / "junit-pytest.xml")
        self.assertEqual([t["name"] for t in tests], ["acceptance.test_cart::test_ac_002_rounding",
                                                       "acceptance.test_cart::test_ac_003_empty"])
        self.assertEqual(tests[1]["message"], "ValueError: empty")

    def test_junit_with_a_single_testsuite_root_and_no_classname(self):
        tests = reports.failed_tests(self.write('<testsuite><testcase name="t  one"><failure>first\nsecond'
                                                '</failure></testcase></testsuite>'))
        self.assertEqual(tests, [{"name": "t one", "file": None, "message": "first"}])

    def test_nunit2_names_join_the_suites_and_skip_ignored_cases(self):
        tests = reports.failed_tests(FIXTURES / "nunit2-pester.xml")
        self.assertEqual(tests, [{"name": "Pester > AC-313 panel > AC-313 panel.keeps the title", "file": None,
                                  "message": "Expected: {a} But was: {b}"}])

    def test_nunit3_failed_case(self):
        tests = reports.failed_tests(FIXTURES / "nunit3.xml")
        self.assertEqual([t["name"] for t in tests], ["shop.dll > CartTests > Totals"])
        self.assertEqual(tests[0]["message"], "Expected 4")

    def test_nunit_error_result_counts_as_failed(self):
        tests = reports.failed_tests(self.write('<test-results><test-suite name="S"><results>'
                                                '<test-case name="c" result="Error"/></results></test-suite>'
                                                '</test-results>'))
        self.assertEqual([t["name"] for t in tests], ["S > c"])

    def test_a_report_with_nothing_failed_is_an_empty_list(self):
        self.assertEqual(reports.failed_tests(self.write('<test-run><test-case name="a" result="Passed"/></test-run>')),
                         [])

    def test_unusable_reports_give_none(self):
        self.assertIsNone(reports.failed_tests(FIXTURES / "broken.xml"))
        self.assertIsNone(reports.failed_tests(self.write("<html><body/></html>")))
        self.assertIsNone(reports.failed_tests(self.temp() / "missing.xml"))
        self.assertIsNone(reports.failed_tests(self.temp()), "a folder")

    def test_a_long_message_is_cut(self):
        tests = reports.failed_tests(self.write(f'<testsuite><testcase name="t"><failure message="{"x" * 500}"/>'
                                                '</testcase></testsuite>'))
        self.assertEqual(len(tests[0]["message"]), reports.MAX_MESSAGE)

    def test_failure_text(self):
        self.assertEqual(reports.failure_text([{"name": "a", "file": "f.py", "message": "m"},
                                               {"name": "b", "file": None, "message": ""}]),
                         ["FAILED a [f.py] m", "FAILED b"])


class PathsTests(unittest.TestCase):
    def test_declared_paths_and_malformed_manifests(self):
        m = {"reports": {"tests": "out/r.xml", "acceptance": "a.xml", "lint": "", "types": 3}}
        self.assertEqual(reports.report_path(m, "tests"), "out/r.xml")
        self.assertIsNone(reports.report_path(m, "lint"))
        self.assertIsNone(reports.report_path(m, "types"))
        self.assertEqual(reports.report_paths(m), ["out/r.xml", "a.xml"])
        for bad in ({}, {"reports": ["x"]}, None, "x"):
            self.assertEqual(reports.report_paths(bad), [])

    def test_clear_report_deletes_inside_the_root_only(self):
        root = Path(tempfile.mkdtemp(prefix="reports-root-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        outside = Path(tempfile.mkdtemp(prefix="reports-outside-"))
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        (root / "out").mkdir()
        (root / "out/r.xml").write_text("x", encoding="utf-8")
        (outside / "keep.xml").write_text("x", encoding="utf-8")
        with mock.patch.object(reports, "ROOT", root):
            reports.clear_report({"reports": {"tests": "out/r.xml"}}, "tests")
            reports.clear_report({"reports": {"tests": f"../{outside.name}/keep.xml"}}, "tests")
            reports.clear_report({"reports": {"tests": "out"}}, "tests")  # a folder is left alone
            reports.clear_report({"reports": {"tests": "missing.xml"}}, "tests")
        self.assertFalse((root / "out/r.xml").exists())
        self.assertTrue((outside / "keep.xml").exists())
        self.assertTrue((root / "out").is_dir())


class ScanLinesTests(unittest.TestCase):
    """verification.scan_lines: the console without passing lines, or the report's failing tests (design 0022)."""

    def test_passing_lines_are_skipped_but_failure_lines_are_kept(self):
        import verification
        console = ["PASSED tests/test_a.py::test_ac_001", "  [+] adds 3ms", "ok  \texample.com/pkg 0.1s",
                   "--- PASS: TestX", "\u2713 renders", "FAILED tests/test_b.py::test_x - assert 'PASSED' in out",
                   "  [-] rounds 5ms", "plain context line"]
        kept, state = verification.scan_lines({}, "tests", console)
        self.assertIsNone(state)
        self.assertEqual(kept, ["FAILED tests/test_b.py::test_x - assert 'PASSED' in out", "  [-] rounds 5ms",
                                "plain context line"])

    def test_the_baseline_records_whether_a_report_was_read(self):
        from test_agentic import MANIFEST, PY, Repo
        report = ('<testsuite><testcase classname="t" name="test_old" file="tests/test_old.py">'
                  '<failure message="x"/></testcase></testsuite>')
        repo = Repo(dict(MANIFEST, verify={"tests": f"{PY} t.py"}, reports={"tests": "r.xml"}))
        self.addCleanup(repo.cleanup)
        repo.write("t.py", "import sys\nopen('r.xml', 'w').write(%r)\nsys.exit(1)\n" % report)
        repo.run("baseline")
        saved = json.loads((repo.dir / ".claude/state/baseline.json").read_text(encoding="utf-8"))
        tests = next(ph for ph in saved["phases"] if ph["name"] == "tests")
        self.assertEqual((tests["report"], tests["failures"]), ("read", ["FAILED t::test_old [tests/test_old.py] x"]))


if __name__ == "__main__":
    unittest.main()
