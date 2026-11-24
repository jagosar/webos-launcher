"""Unit cases of design 0030 (agentic-flow 2.21.1): the console lines read without a report (Pester's block headers)
and the zones in close-stage's GATE line.

Run: python3 -m unittest discover -s .claude/bin -p test_failure_lines.py
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_agentic import MANIFEST, Repo  # noqa: E402
from verification import console_lines  # noqa: E402

FAILED = "  [-] keeps the title 4ms"


class ConsoleLinesTests(unittest.TestCase):
    def test_without_headers_only_passing_lines_are_dropped(self):
        lines = ["PASSED a::test_ac_001", "FAILED a::test_ac_002", "Traceback (most recent call last):"]
        self.assertEqual(console_lines(lines), lines[1:])

    def test_a_header_with_no_test_under_it_is_dropped(self):
        self.assertEqual(console_lines(["Describing AC-001 empty", "Describing AC-002 also empty"]), [])

    def test_a_header_at_the_end_of_the_output_is_dropped(self):
        self.assertEqual(console_lines(["Describing AC-001 x", FAILED, "Describing AC-002 last"]),
                         ["Describing AC-001 x", FAILED])

    def test_two_failures_under_one_header_keep_it_once(self):
        lines = ["Describing AC-001 x", FAILED, "  [-] closes 1ms"]
        self.assertEqual(console_lines(lines), lines)

    def test_a_context_keeps_its_describe_and_itself(self):
        lines = ["Describing AC-001 x", "  [+] opens", "  Context AC-002 y", FAILED]
        self.assertEqual(console_lines(lines), ["Describing AC-001 x", "  Context AC-002 y", FAILED])

    def test_a_new_context_closes_the_previous_one(self):
        lines = ["Describing AC-001 x", "  Context AC-002 y", "    [+] ok", "  Context AC-003 z", FAILED]
        self.assertEqual(console_lines(lines), ["Describing AC-001 x", "  Context AC-003 z", FAILED])

    def test_a_failure_at_the_describe_level_after_a_context_counts_in_the_context(self):
        # blocks end by header, not by indentation (design 0030, section 11): pinned so a change is deliberate
        lines = ["Describing AC-001 x", "  Context AC-002 y", "    [+] ok", FAILED]
        self.assertEqual(console_lines(lines), ["Describing AC-001 x", "  Context AC-002 y", FAILED])

    def test_a_summary_keeps_no_header_and_closes_the_blocks(self):
        lines = ["Describing AC-001 x", "  [+] ok", "Tests Passed: 1, Failed: 1", "  [-] after the summary"]
        self.assertEqual(console_lines(lines), ["Tests Passed: 1, Failed: 1", "  [-] after the summary"])

    def test_a_failing_test_that_reads_like_a_summary_is_a_failure(self):
        lines = ["Describing AC-001 x", "  [-] reports Errors: 0 for a clean file"]
        self.assertEqual(console_lines(lines), lines)

    def test_a_header_is_not_kept_by_its_own_words(self):
        self.assertEqual(console_lines(["Describing AC-001 failure handling", "  [+] retries"]), [])


class GateLineTests(unittest.TestCase):
    def setUp(self):
        self.repo = Repo(MANIFEST)
        self.addCleanup(self.repo.cleanup)
        self.repo.write("acc.py", "raise SystemExit(0)\n")

    def close(self, stage, *marks, gate):
        self.assertEqual(self.repo.run("open-stage", stage).returncode, 0)
        self.repo.write(".claude/state/gate.json", json.dumps(gate))
        return self.repo.run("close-stage", stage, *marks).stdout

    def test_a_zone_without_denials_prints_no_line(self):
        self.assertNotIn("GATE:", self.close("acceptance", "AT=done", gate={"AT": {"denials": 0}}))

    def test_packages_come_first_and_a_zone_with_denials_after(self):
        out = self.close("1", "WP-01=done", "WP-02=done", gate={"WP-02": {"denials": 2}})
        self.assertIn("GATE: WP-01=0 WP-02=2\n", out)


if __name__ == "__main__":
    unittest.main()
