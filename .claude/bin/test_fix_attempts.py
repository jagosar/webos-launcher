"""Tests of close-stage's fix attempt number (`ID=state@N --fix`) and of the placeholder diff (design 0031, WP-01).

Run: python3 -m unittest discover -s .claude/bin -p test_fix_attempts.py
"""
import json
import unittest

from test_agentic import MANIFEST, Repo


class FixAttemptNumberTests(unittest.TestCase):
    def setUp(self):
        self.repo = Repo(MANIFEST)
        self.addCleanup(self.repo.cleanup)
        self.repo.write("acc.py", "import sys\nsys.exit(0)\n")
        self.repo.write("src/b/x.py", "v1\n")
        self.repo.write("src/a.py", "a1\n")
        self.runs = self.repo.dir / ".claude/state/runs"

    def close(self, *items):
        self.repo.run("open-stage", "1")
        return self.repo.run("close-stage", "1", *items)

    def fix_count(self, jid):
        ledger = json.loads((self.repo.dir / ".claude/state/ledger.json").read_text(encoding="utf-8"))
        return ledger[jid].get("fix_count")

    def test_the_number_sets_the_ledger_and_names_the_diff(self):
        self.close("WP-02=done@2", "--fix")
        self.assertEqual(self.fix_count("WP-02"), 2)
        self.assertTrue((self.runs / "WP-02.fix-2.diff").exists())
        self.assertFalse((self.runs / "WP-02.fix-1.diff").exists())

    def test_the_ledger_state_is_the_part_before_the_number(self):
        r = self.close("WP-02=blocker@1", "--fix")
        ledger = json.loads((self.repo.dir / ".claude/state/ledger.json").read_text(encoding="utf-8"))
        self.assertEqual(ledger["WP-02"]["state"], "blocker", r.stdout + r.stderr)

    def test_each_package_keeps_its_own_number_and_one_without_it_counts(self):
        self.close("WP-02=done", "--fix")
        self.close("WP-01=done@5", "WP-02=done", "--fix")
        self.assertEqual((self.fix_count("WP-01"), self.fix_count("WP-02")), (5, 2))
        self.assertTrue((self.runs / "WP-01.fix-5.diff").exists())
        self.assertTrue((self.runs / "WP-02.fix-2.diff").exists())

    def test_a_malformed_number_is_an_invalid_entry(self):
        for item in ("WP-02=done@", "WP-02=done@x", "WP-02=done@-1", "WP-02=done@0", "WP-02=done@1.5", "WP-02=done@²"):
            with self.subTest(item=item):
                r = self.close(item, "--fix")
                self.assertEqual(r.returncode, 1, r.stdout)
                self.assertIn("invalid entry", r.stderr)
        r = self.close("WP-02=done@1")
        self.assertEqual(r.returncode, 1, "a number without --fix")
        self.assertIn("invalid entry", r.stderr)

    def test_the_placeholder_overwrites_a_stale_diff_only_for_packages_with_files(self):
        self.runs.mkdir(parents=True, exist_ok=True)
        (self.runs / "WP-02.fix-1.diff").write_text("STALE\n", encoding="utf-8")
        self.repo.run("open-stage", "1")
        (self.repo.dir / ".claude/state/tree-1.txt").unlink()
        self.repo.run("close-stage", "1", "WP-02=done@1", "AT=done", "--fix")
        self.assertEqual((self.runs / "WP-02.fix-1.diff").read_text(encoding="utf-8"),
                         "(the diff of this attempt could not be recorded)\n")
        self.assertFalse((self.runs / "AT.fix-1.diff").exists(), "AT owns no package files")

    def test_an_attempt_that_changed_nothing_says_so(self):
        self.close("WP-02=done@1", "--fix")
        self.assertEqual((self.runs / "WP-02.fix-1.diff").read_text(encoding="utf-8"),
                         "(this attempt changed no file)\n")


if __name__ == "__main__":
    unittest.main()
