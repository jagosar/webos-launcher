"""Tests of `agentic.py doctor` (doctor.py) and repo_scan.wanted_rules, beyond the acceptance criteria of design 0019:
the messages of each finding, the edge cases and the command line.

Run: python3 -m unittest discover -s .claude/bin -p "test_doctor.py"
Each test builds a temporary project with test_agentic.Repo; nothing reads the repository it runs in.
"""
import json
import shutil
import sys
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parent
ROOT = BIN.parents[1]
sys.path.insert(0, str(BIN))
from test_agentic import Repo  # noqa: E402

RETIRED = "Bash(python3 .claude/state/guard/bin/agentic.py:*)"


def version():
    return json.loads((ROOT / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))["version"]


class DoctorCase(unittest.TestCase):
    def setUp(self):
        self.repo = Repo()
        self.addCleanup(self.repo.cleanup)
        self.repo.write(".claude/agentic-install.json", json.dumps({"version": version(), "files": {}}))
        self.repo.write(".claude/quality-baseline.json", "{}")

    def settings(self):
        return json.loads((self.repo.dir / ".claude/settings.json").read_text(encoding="utf-8"))

    def set_settings(self, data):
        self.repo.write(".claude/settings.json", json.dumps(data, indent=2) + "\n")

    def doctor(self, *args):
        r = self.repo.run("doctor", *args)
        return r.returncode, json.loads(r.stdout.splitlines()[-1])

    def finding(self, code):
        found = [f for f in self.doctor()[1]["findings"] if f["code"] == code]
        self.assertEqual(len(found), 1, found)
        return found[0]


class FindingTests(DoctorCase):
    def test_a_project_without_a_rules_library_reports_nothing(self):
        self.assertEqual(self.doctor(), (0, {"ok": True, "fixed": [], "findings": []}))

    def test_an_invalid_install_record_is_named(self):
        self.repo.write(".claude/agentic-install.json", "{not json")
        found = self.finding("install-record")
        self.assertEqual(found["message"], ".claude/agentic-install.json is not valid JSON")
        self.assertEqual(found["route"], "/agentic-flow:init")

    def test_an_install_record_of_another_version_names_both(self):
        self.repo.write(".claude/agentic-install.json", json.dumps({"version": "0.1.0"}))
        self.assertEqual(self.finding("install-record")["message"],
                         f"the install record says 0.1.0, the installed flow is {version()}")

    def test_a_missing_hook_routes_to_init_with_its_reason(self):
        data = self.settings()
        data["hooks"] = {}
        self.set_settings(data)
        found = self.finding("hook-missing")
        self.assertEqual(found["message"], "no agentic.py hook in .claude/settings.json PreToolUse")
        self.assertEqual(found["route"], "/agentic-flow:init")

    def test_a_missing_rule_file_is_named(self):
        shutil.copytree(ROOT / ".claude/rules-library", self.repo.dir / ".claude/rules-library")
        self.assertIn("missing: common", self.finding("rules")["message"])

    def test_several_running_entries_are_sorted_and_others_ignored(self):
        self.repo.write(".claude/state/ledger.json", json.dumps({"WP-02": {"state": "running"}, "WP-01": {
            "state": "running"}, "WP-03": {"state": "done"}}))
        self.assertEqual(self.finding("agents-running")["message"],
                         "the ledger says WP-01, WP-02 running; if no run is in progress, stop them")

    def test_an_unreadable_ledger_reports_no_running_agent(self):
        self.repo.write(".claude/state/ledger.json", "{broken")
        self.assertEqual(self.doctor()[1]["findings"], [])

    def test_upgrade_files_are_counted_recursively(self):
        self.repo.write(".claude/state/upgrade/.claude/agents/a.md")
        self.repo.write(".claude/state/upgrade/.claude/bin/b.py")
        code, out = self.doctor()
        self.assertEqual((code, out["ok"]), (0, True))
        self.assertEqual(out["findings"][0]["message"],
                         "2 new version(s) kept beside your edits in .claude/state/upgrade/")


class FixTests(DoctorCase):
    def test_one_finding_for_retired_rules_and_fix_removes_them_keeping_the_rest(self):
        data = self.settings()
        data["permissions"]["allow"] += [RETIRED, RETIRED]
        data["extra"] = "ñ"
        self.set_settings(data)
        self.assertEqual(self.finding("retired-permission")["fix"], "remove it from permissions.allow")
        code, out = self.doctor("--fix")
        self.assertEqual((code, out["fixed"], out["findings"]), (0, ["retired-permission"], []))
        after = self.settings()
        self.assertNotIn(RETIRED, after["permissions"]["allow"])
        self.assertEqual(after["extra"], "ñ")
        text = (self.repo.dir / ".claude/settings.json").read_text(encoding="utf-8")
        self.assertTrue(text.endswith("}\n"))
        self.assertIn('\n  "permissions"', text)

    def test_fix_with_nothing_to_repair_changes_nothing(self):
        before = (self.repo.dir / ".claude/settings.json").read_bytes()
        self.assertEqual(self.doctor("--fix"), (0, {"ok": True, "fixed": [], "findings": []}))
        self.assertEqual((self.repo.dir / ".claude/settings.json").read_bytes(), before)

    def test_any_other_argument_is_a_usage_error(self):
        for args in (["--all"], ["--fix", "x"], ["fix"]):
            with self.subTest(args=args):
                r = self.repo.run("doctor", *args)
                self.assertEqual(r.returncode, 1)
                self.assertIn("Uso: doctor [--fix]", r.stderr)


class WantedRulesTests(unittest.TestCase):
    def test_rules_sync_installs_what_wanted_rules_names(self):
        repo = Repo(dict(stack=["python", "../../etc", "cobol"], work_packages=[
            {"id": "WP-01", "wave": 1, "depends_on": [], "files": ["src/a.py"]}],
            acceptance={"files": ["acceptance/"], "command": "true"}))
        self.addCleanup(repo.cleanup)
        shutil.copytree(ROOT / ".claude/rules-library", repo.dir / ".claude/rules-library")
        r = repo.run("rules-sync")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["rules"], ["common", "python"])
        self.assertEqual(sorted(p.stem for p in (repo.dir / ".claude/rules/agentic").glob("*.md")),
                         ["common", "python"])


if __name__ == "__main__":
    unittest.main()
