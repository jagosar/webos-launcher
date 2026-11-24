"""Tests of the PowerShell code rules (design 0031, WP-03, issue 118): runs_code, open_ended_rule's PowerShell case,
code_runner_commands and sync_permissions' exact-only rule.

Run: python3 -m unittest discover -s .claude/bin -p test_code_runner_rules.py
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import manifest_check  # noqa: E402
from manifest_check import code_runner_commands, open_ended_rule, runs_code, words  # noqa: E402
from test_agentic import MANIFEST, PY, Repo  # noqa: E402

PESTER = 'pwsh -Command "Invoke-Pester -EnableExit"'


class RunsCodeTests(unittest.TestCase):
    def test_a_table_of_command_lines(self):
        cases = {
            "powershell -Command x": True,
            "powershell -command x": True,
            "powershell -com x": True,
            "powershell -co x": True,
            "powershell -c x": True,
            "powershell /Command x": True,
            "powershell /c x": True,
            "pwsh -e AAAA": True,
            "pwsh -ec AAAA": True,
            "pwsh -enc AAAA": True,
            "pwsh -en t.ps1 -File t.ps1": False,  # three letters are no abbreviation of -EncodedCommand
            "pwsh -enco AAAA": True,
            "pwsh -EncodedCommand AAAA": True,
            "powershell -NoProfile Invoke-Pester": True,
            "powershell": True,
            "powershell -File t.ps1": False,
            "powershell -f t.ps1": False,
            "powershell /File t.ps1": False,
            "powershell -NoProfile -FILE t.ps1 -Command x": False,
            "powershell -Command x -File t.ps1": True,
            "PowerShell.EXE -File t.ps1": False,
            "PWSH.exe -c x": True,
            "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe -Command x": True,
            "C:\\Program Files\\PowerShell\\7\\pwsh.exe -File t.ps1": False,
            "/usr/bin/pwsh -c x": True,
            "python3 -c x": False,
            "cmd /c x": False,
            "": False,
        }
        for line, expected in cases.items():
            with self.subTest(line=line):
                self.assertIs(runs_code(words(line)), expected)

    def test_words_keep_windows_paths_and_quoted_arguments(self):
        self.assertEqual(words('C:\\x\\pwsh.exe -Command "a b"'), ["C:/x/pwsh.exe", "-Command", "a b"])
        self.assertEqual(words('pwsh -Command "unclosed'), ["pwsh", "-Command", '"unclosed'])


class OpenEndedRuleTests(unittest.TestCase):
    def test_prefix_rules_that_run_code_and_shells_alone(self):
        for rule in ("Bash(pwsh -c x:*)", "Bash(PWSH:*)", "Bash(Powershell.exe)", "Bash(cmd)", "Bash(cmd.exe:*)"):
            with self.subTest(rule=rule):
                self.assertTrue(open_ended_rule(rule))
        for rule in ("Bash(pwsh -c x)", "Bash(pwsh -File t.ps1:*)", "Bash(cmd /c x:*)", "Bash(npm test:*)"):
            with self.subTest(rule=rule):
                self.assertFalse(open_ended_rule(rule))

    def test_verify_commands_keep_their_rule(self):
        self.assertNotIn("pwsh", manifest_check.OPEN_ENDED)
        self.assertFalse(manifest_check.open_ended_command(PESTER))


class CodeRunnerCommandsTests(unittest.TestCase):
    def test_acceptance_then_verify_without_repeats(self):
        manifest = {"acceptance": {"command": PESTER}, "verify": {"lint": "ruff check .", "tests": PESTER,
                                                                  "types": "pwsh -c Check"}}
        self.assertEqual(code_runner_commands(manifest), [PESTER, "pwsh -c Check"])

    def test_malformed_shapes_are_skipped(self):
        for manifest in ({}, {"acceptance": None, "verify": ["pwsh -c x"]}, {"acceptance": {"command": None}},
                         {"verify": {"tests": 3}}):
            with self.subTest(manifest=manifest):
                self.assertEqual(code_runner_commands(manifest), [])


class SyncPermissionsTests(unittest.TestCase):
    def test_exact_rule_only_and_idempotent(self):
        repo = Repo(dict(MANIFEST, verify={"tests": PESTER}))
        self.addCleanup(repo.cleanup)
        local = repo.dir / ".claude/settings.local.json"
        mine = "Bash(pwsh -c Mine:*)"  # the user's own rule, even an invalid one, is theirs
        local.write_text(json.dumps({"permissions": {"allow": [mine, f"Bash({PESTER}:*)"]}}), encoding="utf-8")
        first = repo.run("sync-permissions")
        self.assertIn("1 rule(s) removed", first.stdout)
        allow = json.loads(local.read_text(encoding="utf-8"))["permissions"]["allow"]
        self.assertIn(mine, allow)
        self.assertIn(f"Bash({PESTER})", allow)
        self.assertIn(f"Bash({PY} acc.py:*)", allow, "the acceptance command keeps both forms")
        second = repo.run("sync-permissions")
        self.assertNotIn("removed", second.stdout)
        self.assertEqual(json.loads(local.read_text(encoding="utf-8"))["permissions"]["allow"], allow)


if __name__ == "__main__":
    unittest.main()
