"""Unit cases of the quality phase's attribution (design 0028, issue 134): which files of the acceptance zone and of
QA's zone a quality failure names, and the sentence of limits printed for whoever writes code.

Run: python3 -m unittest discover -s .claude/bin -p test_zone_attribution.py
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verification  # noqa: E402

MANIFEST = {"acceptance": {"files": ["acceptance/", "e2e/smoke.spec.ts"]}, "qa": {"files": ["qa/"]},
            "work_packages": [{"id": "WP-01", "wave": 1, "files": ["src/"], "acs": ["AC-001"]}]}


def fail(kind, key, metric="lines"):
    return [f"FAIL {kind} {key} exceeds its {metric} allowance", f"  {key} {metric}: now 9, allowed 1"]


class ZoneFilesTests(unittest.TestCase):
    def test_a_file_and_a_function_of_a_zone_name_the_file_once(self):
        lines = fail("file", "acceptance/a.test.ts", "long_lines") + fail("function", "acceptance/a.test.ts::it AC-001")
        self.assertEqual(verification.zone_files(MANIFEST, lines), {"AT": ["acceptance/a.test.ts"]})

    def test_each_zone_gets_its_own_files_sorted(self):
        lines = (fail("file", "qa/z.py") + fail("file", "qa/a.py") + fail("file", "e2e/smoke.spec.ts")
                 + fail("file", "src/x.py"))
        self.assertEqual(verification.zone_files(MANIFEST, lines),
                         {"AT": ["e2e/smoke.spec.ts"], "QA": ["qa/a.py", "qa/z.py"]})

    def test_only_a_failure_line_names_a_file(self):
        for lines in ([], ["  acceptance/a.py lines: now 9, allowed 1"], ["acceptance/a.py is fine"],
                      ["FAIL quality baseline unreadable: .claude/quality-baseline.json"],
                      fail("file", "acceptance.py"), fail("file", "src/acceptance/a.py"), fail("file", "e2e/other.ts")):
            self.assertEqual(verification.zone_files(MANIFEST, lines), {}, lines)

    def test_the_default_zones_apply_without_a_declaration(self):
        lines = fail("file", "acceptance/a.py") + fail("file", "qa/b.py")
        self.assertEqual(verification.zone_files({"work_packages": []}, lines),
                         {"AT": ["acceptance/a.py"], "QA": ["qa/b.py"]})

    def test_a_package_is_a_suspect_of_a_quality_failure_only_by_path(self):
        named = fail("function", "acceptance/a.py::test_ac_001_total")
        self.assertEqual(verification.attribute(MANIFEST, named, by_criteria=False), {})
        mine = fail("file", "src/x.py") + named
        self.assertEqual(verification.attribute(MANIFEST, mine, by_criteria=False),
                         {"WP-01": {"files": ["src/"], "acs": []}})


class LimitsTextTests(unittest.TestCase):
    def text(self, state):
        with mock.patch.object(verification, "quality_state", return_value=state):
            return verification.quality_limits_text()

    def test_no_baseline_or_an_unreadable_one_prints_nothing(self):
        self.assertEqual(self.text(None), "")
        self.assertEqual(self.text({"unreadable": True}), "")

    def test_the_baseline_s_own_limits_are_the_ones_printed(self):
        text = self.text({"limits": {"line_chars": 100, "function_lines": 40, "nesting": 3}})
        for words in ("no line over 100 characters", "no new file over 800 lines",
                      "no new function over 40 lines (100 in a test) or nested deeper than 3"):
            self.assertIn(words, text)
        self.assertTrue(text.startswith("\nQuality limits") and text.endswith(".claude/quality-baseline.json.\n"))


if __name__ == "__main__":
    unittest.main()
