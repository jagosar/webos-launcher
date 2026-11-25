"""Tests of design 0028 (issue 134, agentic-flow 2.20.1): AC-001 .. AC-006. Whoever writes code is told the quality
limits; a quality failure is attributed by the file it names, never by a criterion's number in a test's name; and a
violation in the acceptance zone or in QA's zone is reported as that zone's, not as a package's.

Run: python3 -m unittest discover -s .claude/bin -p test_quality_limits.py
Everything runs in temporary repos with a real quality baseline. Nothing is spent.
"""
import json
import shutil
import sys
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parent
ROOT = BIN.parents[1]
CLAUDE = ROOT / ".claude"
sys.path.insert(0, str(BIN))
import core  # noqa: E402
import verification  # noqa: E402
from test_agentic import MANIFEST, PY, Repo  # noqa: E402

FLOW_REPO_ONLY = unittest.skipUnless(core.is_flow_repo(ROOT), "the flow's own repo")
ONE_PACKAGE = {"allow_bash": [], "acceptance": {"files": ["acceptance/"], "command": f"{PY} acc.py"},
               "work_packages": [{"id": "WP-01", "wave": 1, "depends_on": [], "files": ["src/a.py"],
                                  "acs": ["AC-001"]}]}
LONG_LINE = "x = '" + "a" * 125 + "'\n"  # 131 characters: over the default limit of 120
ACCEPTANCE_FILE = "acceptance/test_ac_001_money.py"
BASELINE = ".claude/quality-baseline.json"


def limits_sentence(line_chars=120):
    return (f"\nQuality limits, checked on every file you write, tests included: no line over {line_chars} "
            "characters; no new file over 800 lines; no new function over 60 lines (100 in a test) or nested deeper "
            "than 4. What is already over a limit has its own allowance in .claude/quality-baseline.json.\n")


class VerifyCase(unittest.TestCase):
    """A repo of one package whose only phase that can fail is quality: no verify commands, acceptance passes."""

    def setUp(self):
        self.repo = Repo(ONE_PACKAGE)
        self.addCleanup(self.repo.cleanup)
        self.repo.write("acc.py", "raise SystemExit(0)\n")
        self.repo.write("src/a.py", "x = 1\n")
        self.assertEqual(self.repo.run("quality", "--init").returncode, 0)

    def verify(self):
        r = self.repo.run("verify")
        self.assertNotIn("Traceback", r.stderr)
        return r, json.loads(r.stdout.splitlines()[-1])


class ZoneAttributionTests(VerifyCase):
    def test_ac_001_a_quality_failure_on_an_acceptance_file_names_no_package(self):
        self.repo.write(ACCEPTANCE_FILE, LONG_LINE)
        r, out = self.verify()
        self.assertEqual(r.returncode, 3)
        self.assertIn("quality", out["blocking"])
        self.assertEqual(out["suspects"], {}, "WP-01 answers for AC-001, but it did not write that line")
        self.assertEqual(out["zone_suspects"], {"AT": {"phases": ["quality"], "files": [ACCEPTANCE_FILE]}})

    def test_ac_002_the_owner_of_the_file_is_the_suspect_and_each_zone_is_named(self):
        for rel in (ACCEPTANCE_FILE, "src/a.py", "qa/test_qa.py"):
            self.repo.write(rel, LONG_LINE)
        _, out = self.verify()
        self.assertEqual(out["suspects"], {"WP-01": {"phases": ["quality"], "files": ["src/a.py"], "acs": []}})
        self.assertEqual(out["zone_suspects"], {"AT": {"phases": ["quality"], "files": [ACCEPTANCE_FILE]},
                                                "QA": {"phases": ["quality"], "files": ["qa/test_qa.py"]}})

    def test_ac_002_a_function_over_its_limit_names_its_file_once(self):
        body = "def test_ac_001_everything():\n" + "    x = 1\n" * 120
        self.repo.write(ACCEPTANCE_FILE, body + LONG_LINE)
        _, out = self.verify()
        self.assertEqual(out["suspects"], {})
        self.assertEqual(out["zone_suspects"]["AT"]["files"], [ACCEPTANCE_FILE])

    def test_ac_003_outside_the_quality_phase_nothing_changes(self):
        failing = ["test_ac_001_total FAILED"]
        self.assertEqual(verification.attribute(ONE_PACKAGE, failing), {"WP-01": {"files": [], "acs": ["AC-001"]}})
        self.assertEqual(verification.attribute(ONE_PACKAGE, failing, by_criteria=False), {})
        r, out = self.verify()
        self.assertEqual((r.returncode, out["zone_suspects"], out["suspects"]), (0, {}, {}))

    def test_ac_003_a_failing_test_still_names_the_package_through_its_criterion(self):
        repo = Repo(dict(ONE_PACKAGE, verify={"tests": f"{PY} fail.py"}))
        self.addCleanup(repo.cleanup)
        repo.write("acc.py", "raise SystemExit(0)\n")
        repo.write("fail.py", f"print('FAILED {ACCEPTANCE_FILE}::test_ac_001_total')\nraise SystemExit(1)\n")
        out = json.loads(repo.run("verify").stdout.splitlines()[-1])
        self.assertEqual(out["suspects"], {"WP-01": {"phases": ["tests"], "files": [], "acs": ["AC-001"]}})
        self.assertEqual(out["zone_suspects"], {}, "only the quality phase names a zone")


class LimitsInThePromptTests(unittest.TestCase):
    def project(self, line_chars=None):
        repo = Repo(dict(MANIFEST, stack=["python"]))
        self.addCleanup(repo.cleanup)
        shutil.copytree(CLAUDE / "rules-library", repo.dir / ".claude/rules-library")
        self.assertEqual(repo.run("rules-sync").returncode, 0)
        self.assertEqual(repo.run("quality", "--init").returncode, 0)
        if line_chars:
            path = repo.dir / BASELINE
            data = json.loads(path.read_text(encoding="utf-8"))
            data["limits"] = dict(data.get("limits") or {}, line_chars=line_chars)
            path.write_text(json.dumps(data), encoding="utf-8")
        return repo

    def plan(self, repo):
        return json.loads(repo.run("pipeline-plan").stdout.splitlines()[-1])

    def implementer_brief(self, repo):
        """The implementer reads the rules and the limits at the end of its brief (design 0032), not in its prompt."""
        r = repo.run("briefs")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return (repo.dir / ".claude/state/briefs/WP-01.md").read_text(encoding="utf-8")

    def test_ac_004_the_limits_are_printed_where_the_rules_are(self):
        import findings
        repo = self.project(line_chars=100)
        plan = self.plan(repo)
        text = plan["rules_text"]
        self.assertTrue(text.endswith(limits_sentence(100)), text[-400:])
        self.assertEqual(text.count("Quality limits"), 1)
        self.assertLess(text.index("--- .claude/rules/agentic/common.md ---"), text.index("Quality limits"))
        self.assertEqual(plan["rules_sum"], findings.text_sum(text))
        brief = self.implementer_brief(repo)
        self.assertTrue(brief.endswith(limits_sentence(100)), brief[-400:])
        (repo.dir / BASELINE).unlink()
        self.assertNotIn("Quality limits", self.plan(repo)["rules_text"])
        self.assertNotIn("Quality limits", self.implementer_brief(repo))

    def test_ac_004_the_defaults_are_printed_and_a_broken_baseline_prints_nothing(self):
        repo = self.project()
        self.assertTrue(self.plan(repo)["rules_text"].endswith(limits_sentence()))
        (repo.dir / BASELINE).write_text("[1]", encoding="utf-8")
        self.assertNotIn("Quality limits", self.plan(repo)["rules_text"])


class DefinitionTests(unittest.TestCase):
    def test_ac_005_implement_presents_the_zone(self):
        text = (CLAUDE / "commands/implement.md").read_text(encoding="utf-8")
        lines = [line for line in text.splitlines() if "`zone_suspects`" in line]
        self.assertEqual(len(lines), 1)
        for words in ("`AT`", "`QA`", "no package can fix them"):
            self.assertIn(words, lines[0])
        self.assertNotIn("fix cycle", lines[0])


@FLOW_REPO_ONLY  # it reads this repository's docs and release files
class ReleaseTests(unittest.TestCase):
    def test_ac_006_release_2_20_1(self):
        import upgrade
        notes = upgrade.NOTES["2.20.1"]
        self.assertGreaterEqual(len(notes), 2)
        keys = list(upgrade.NOTES)
        self.assertEqual(keys[keys.index("2.20.1") + 1], "2.20.0", "right above the release it fixes")
        self.assertTrue(any("quality limits" in n for n in notes))
        self.assertTrue(any("zone_suspects" in n for n in notes))
        works = (ROOT / "docs/how-it-works.md").read_text(encoding="utf-8")
        self.assertIn("zone_suspects", works)
        self.assertNotIn("2.20.1", works)


if __name__ == "__main__":
    unittest.main()
