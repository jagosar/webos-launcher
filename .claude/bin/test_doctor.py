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
STOP = "python3 .claude/bin/agentic.py stop"


def last_json(text):
    return json.loads([line for line in text.splitlines() if line.startswith("{")][-1])


def snapshot(folder):
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in sorted(Path(folder).rglob("*"))
            if p.is_file() and ".git" not in p.parts}




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


class ProjectStateCase(unittest.TestCase):
    """From test_acceptance_v212 (design 0019, AC-001 to AC-004): doctor on a stale project and on a clean one."""

    def repo(self):
        repo = Repo()
        self.addCleanup(repo.cleanup)
        return repo

    def stale(self):
        repo = self.repo()
        repo.write(".claude/state/guard/x", "old")
        settings = json.loads((repo.dir / ".claude/settings.json").read_text(encoding="utf-8"))
        settings["permissions"]["allow"].append(RETIRED)
        repo.write(".claude/settings.json", json.dumps(settings, indent=2) + "\n")
        repo.write(".claude/state/ledger.json", json.dumps({"WP-01": {"state": "running"}}))
        repo.write(".claude/state/upgrade/.claude/agents/qa.md", "new")
        return repo

    def clean(self):
        repo = self.repo()
        shutil.copytree(ROOT / ".claude/rules-library", repo.dir / ".claude/rules-library")
        repo.write(".claude/agentic-install.json", json.dumps({"version": version(), "files": {}}))
        repo.write(".claude/quality-baseline.json", json.dumps({"version": 1, "flow_repo": False, "limits": {},
                                                                "exclude": [], "files": {}, "functions": {}}))
        self.assertEqual(repo.run("rules-sync").returncode, 0)
        return repo

    def doctor(self, repo, *args):
        r = repo.run("doctor", *args)
        return r.returncode, last_json(r.stdout)


class ProjectStateTests(ProjectStateCase):
    def test_a_stale_project_lists_each_finding_and_writes_nothing(self):
        repo = self.stale()
        before = snapshot(repo.dir)
        code, out = self.doctor(repo)
        self.assertEqual(code, 3)
        self.assertFalse(out["ok"])
        got = [(f["code"], f["severity"]) for f in out["findings"]]
        self.assertEqual(got, [("guard-leftover", "auto"), ("retired-permission", "auto"), ("install-record", "route"),
                               ("quality-baseline", "route"), ("agents-running", "route"),
                               ("upgrade-pending", "mention")])
        routes = {f["code"]: f["route"] for f in out["findings"] if f["severity"] == "route"}
        self.assertEqual(routes["install-record"], "/agentic-flow:init")
        self.assertEqual(routes["quality-baseline"], "python3 .claude/bin/agentic.py quality --init")
        self.assertEqual(routes["agents-running"], STOP)
        self.assertEqual(snapshot(repo.dir), before, "doctor without --fix writes nothing")

    def test_fix_repairs_only_the_auto_findings(self):
        repo = self.stale()
        ledger = (repo.dir / ".claude/state/ledger.json").read_bytes()
        code, out = self.doctor(repo, "--fix")
        self.assertEqual(code, 3)
        self.assertEqual(out["fixed"], ["guard-leftover", "retired-permission"])
        self.assertFalse((repo.dir / ".claude/state/guard").exists())
        settings = json.loads((repo.dir / ".claude/settings.json").read_text(encoding="utf-8"))
        self.assertNotIn(RETIRED, settings["permissions"]["allow"])
        self.assertIn("Bash(python3 .claude/bin/agentic.py:*)", settings["permissions"]["allow"])
        self.assertIn("PreToolUse", settings["hooks"])
        self.assertEqual((repo.dir / ".claude/state/ledger.json").read_bytes(), ledger)
        self.assertTrue((repo.dir / ".claude/state/upgrade/.claude/agents/qa.md").exists())
        code, out = self.doctor(repo)
        self.assertEqual(code, 3)
        self.assertFalse([f for f in out["findings"] if f["severity"] == "auto"])

    def test_a_clean_project_passes_and_state_is_not_created(self):
        repo = self.clean()
        had_state = (repo.dir / ".claude/state").exists()
        code, out = self.doctor(repo)
        self.assertEqual((code, out["ok"], out["findings"]), (0, True, []), out)
        self.assertEqual((repo.dir / ".claude/state").exists(), had_state, "doctor creates nothing under state")
        self.assertFalse((repo.dir / ".claude/state/ledger.json").exists())
        repo.write(".claude/state/upgrade/a.md", "x")
        code, out = self.doctor(repo)
        self.assertEqual((code, out["ok"]), (0, True))
        self.assertEqual([f["code"] for f in out["findings"]], ["upgrade-pending"])
        flow = self.repo()
        # The flow's own repository by its two marks, written here: in a project, ROOT's files are the project's.
        flow.write(".claude-plugin/plugin.json", json.dumps({"name": "agentic-flow"}))
        flow.write("README.md", "<!-- agentic-flow-template -->\n# flow\n")
        self.assertNotIn("install-record", [f["code"] for f in self.doctor(flow)[1]["findings"]])

    def test_rules_out_of_sync_are_named_and_never_written(self):
        repo = self.clean()
        common = repo.dir / ".claude/rules/agentic/common.md"
        common.write_text(common.read_text(encoding="utf-8") + "\nlocal edit\n", encoding="utf-8")
        repo.write(".claude/rules/agentic/ruby.md", "x")
        rules_before = snapshot(repo.dir / ".claude/rules")
        code, out = self.doctor(repo)
        found = [f for f in out["findings"] if f["code"] == "rules"]
        self.assertEqual(len(found), 1, out)
        self.assertIn("extra: ruby", found[0]["message"])
        self.assertIn("changed: common", found[0]["message"])
        self.assertEqual(found[0]["route"], "python3 .claude/bin/agentic.py rules-sync")
        self.assertEqual(snapshot(repo.dir / ".claude/rules"), rules_before)
        self.assertEqual(repo.run("rules-sync").returncode, 0)
        self.assertEqual(self.doctor(repo)[1]["findings"], [])


if __name__ == "__main__":
    unittest.main()
