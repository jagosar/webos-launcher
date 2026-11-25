"""Unit cases of design 0032 (agentic-flow 2.22.0): the rules section a brief ends with (launch.brief_rules).

Run: python3 -m unittest discover -s .claude/bin -p test_brief_rules.py
Everything runs in temporary repos. Nothing is spent.
"""
import shutil
import sys
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parent
sys.path.insert(0, str(BIN))
from test_agentic import MANIFEST, Repo  # noqa: E402

PRINTED = "Rules you must follow"
LIMITS = "Quality limits, checked on every file you write"


class BriefRulesTests(unittest.TestCase):
    def repo(self, rules=True, baseline=True):
        repo = Repo(dict(MANIFEST, stack=["python"]))
        self.addCleanup(repo.cleanup)
        if rules:
            shutil.copytree(BIN.parent / "rules-library", repo.dir / ".claude/rules-library")
            self.assertEqual(repo.run("rules-sync").returncode, 0)
        if baseline:
            self.assertEqual(repo.run("quality", "--init").returncode, 0)
        r = repo.run("briefs")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return repo

    def brief(self, repo, ident="AT"):
        return (repo.dir / f".claude/state/briefs/{ident}.md").read_text(encoding="utf-8")

    def test_without_rules_installed_only_the_limits_sentence_is_added(self):
        text = self.brief(self.repo(rules=False))
        self.assertNotIn(PRINTED, text)
        self.assertTrue(text.rstrip().endswith(".claude/quality-baseline.json."), text[-200:])

    def test_without_a_quality_baseline_there_is_no_limits_sentence(self):
        text = self.brief(self.repo(baseline=False))
        self.assertIn("--- .claude/rules/agentic/python.md ---", text)
        self.assertNotIn(LIMITS, text)

    def test_without_rules_or_baseline_the_brief_is_the_plan_alone(self):
        repo = self.repo(rules=False, baseline=False)
        self.assertEqual(self.brief(repo), (repo.dir / "architecture.md").read_text(encoding="utf-8"))

    def test_each_rules_file_is_printed_once_without_its_frontmatter(self):
        text = self.brief(self.repo(), "WP-02")
        self.assertEqual(text.count("--- .claude/rules/agentic/common.md ---"), 1)
        self.assertEqual(text.count(PRINTED), 1)
        self.assertNotIn("\npaths:", text)


if __name__ == "__main__":
    unittest.main()
