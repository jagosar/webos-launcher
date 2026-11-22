"""Tests of repo_scan.py's callers beyond test_agentic's CallersTests (their order, their shape and whole-word
matching) and of the printed rules. Moved here from the retired test_acceptance_v22.py and v213 (designs 0017, 0021).

Run: python3 -m unittest discover -s .claude/bin -p "test_repo_scan.py"
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import repo_scan  # noqa: E402
from test_agentic import Repo  # noqa: E402

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
MONEY = "def split(cents, parts):\n    return [cents]\n\n\ndef parse_amount(text):\n    return int(text)\n"
LEDGER = ("from src.money import split, parse_amount\n\n"
          "shares = split(100, 3)\n"
          "total = parse_amount(\"1\")\n"
          "splitter = my_split(1)\n")
TEST_LEDGER = ("import unittest\nfrom src.money import split\n\n\nclass T(unittest.TestCase):\n"
               "    def test_split(self): assert split(1, 1) == [1]\n")


class CallersTests(unittest.TestCase):
    def test_callers_are_ordered_shaped_and_whole_words(self):
        repo = Repo()
        self.addCleanup(repo.cleanup)
        for rel, text in (("src/money.py", MONEY), ("src/ledger.py", LEDGER), ("tests/test_ledger.py", TEST_LEDGER),
                          ("notes.md", "split is documented here\n")):
            repo.write(rel, text)
        subprocess.run(["git", "add", "-A"], cwd=repo.dir, check=True, capture_output=True)
        subprocess.run([*GIT, "commit", "-qm", "x"], cwd=repo.dir, check=True, capture_output=True)
        repo.write("src/money.py", MONEY.replace("    return [cents]\n", "    cents = cents\n    return [cents]\n"))
        r = repo.run("callers")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = json.loads([line for line in r.stdout.splitlines() if line.startswith("{")][-1])
        where = [(c["symbol"], c["file"], c["line"]) for c in out["callers"]]
        self.assertEqual(where, [("split", "src/ledger.py", 1), ("split", "src/ledger.py", 3),
                                 ("split", "tests/test_ledger.py", 2), ("split", "tests/test_ledger.py", 6),
                                 ("parse_amount", "src/ledger.py", 1), ("parse_amount", "src/ledger.py", 4)])
        for item in out["callers"]:
            self.assertEqual(set(item), {"symbol", "defined_in", "file", "line", "text"})
        files = {c["file"] for c in out["callers"]}
        self.assertFalse(files & {"notes.md", "src/money.py"}, "no docs and not the defining file")


class PrintedRulesTests(unittest.TestCase):
    """From test_acceptance_v213 ac_006: the rules an agent's prompt carries, printed without their frontmatter."""

    def test_each_file_is_printed_under_its_name_without_frontmatter(self):
        with tempfile.TemporaryDirectory() as d:
            rule = "---\npaths: ['**/*.py']\n---\n# Python\nNo magic numbers.\n"
            (Path(d) / "a.md").write_text(rule, encoding="utf-8")
            (Path(d) / "b.md").write_text("Plain rule.\n", encoding="utf-8")
            text = repo_scan.render_rules(["a.md", "b.md"], d)
        self.assertTrue(text.startswith("\nRules you must follow (common and language). They are printed here, "
                                        "so do not read them from disk:\n"))
        self.assertIn("\n--- a.md ---\n# Python\nNo magic numbers.\n", text)
        self.assertIn("\n--- b.md ---\nPlain rule.\n", text)
        self.assertNotIn("paths:", text)

    def test_no_rules_print_nothing(self):
        self.assertEqual(repo_scan.render_rules([], "."), "")


class PowerShellStackTests(unittest.TestCase):
    """PowerShell is detected by its extensions and gets its pack; the ratchet does not measure it (design 0022)."""

    def test_scripts_modules_and_manifests_count_as_powershell(self):
        self.assertEqual(repo_scan.STACK_SIGNALS["powershell"], ((), (".ps1", ".psm1", ".psd1")))
        repo = Repo()
        self.addCleanup(repo.cleanup)
        for rel in ("tools/Get-Thing.ps1", "tools/Mod.psm1", "tools/Mod.psd1"):
            repo.write(rel, "function Get-Thing { }\n")
        detected = json.loads(repo.run("stack").stdout)["detected"]
        self.assertEqual(detected, {"powershell": {"markers": [], "files": 3}})

    def test_powershell_has_no_code_map_symbols(self):
        import core
        self.assertIsNone(core.symbol_re("tools/Get-Thing.ps1"), "it would put PowerShell under the brace ratchet")


class NativeRulesTests(unittest.TestCase):
    """Design 0027: the rules Claude Code loads for every agent are those without a `paths` filter."""

    def test_a_file_is_always_loaded_unless_its_frontmatter_filters_by_path(self):
        cases = {"# Rules\n": True, "---\ndescription: x\n---\n# Rules\n": True,
                 "---\npaths:\n  - \"**/*.py\"\n---\n# Rules\n": False, "---\npaths : [a]\n---\nx\n": False,
                 "# Rules\n\npaths: is only a word here\n": True, "--- not closed\npaths: x\n": True}
        with tempfile.TemporaryDirectory() as d:
            for i, (text, expected) in enumerate(cases.items()):
                path = Path(d) / f"r{i}.md"
                path.write_text(text, encoding="utf-8")
                self.assertEqual(repo_scan.always_loaded(path), expected, text)
            self.assertFalse(repo_scan.always_loaded(Path(d) / "missing.md"))

    def test_only_a_session_that_loads_instructions_counts_as_injecting(self):
        from unittest import mock
        for env, expected in (({}, False), ({"CLAUDECODE": "1"}, True),
                              ({"CLAUDECODE": "1", "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1"}, False),
                              ({"CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1"}, False)):
            with mock.patch.dict("os.environ", env, clear=True):
                self.assertEqual(repo_scan.injected_by_claude_code(), expected, env)


if __name__ == "__main__":
    unittest.main()
