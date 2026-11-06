"""Tests of the agent-shortcut detection in core.py (design 0018, issue 57) and of close-stage reporting it.

Run: python3 -m unittest discover -s .claude/bin -p "test_shortcuts.py"
The fixtures split the shortcut tokens, so this file itself adds no match.
"""
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import core  # noqa: E402
from test_agentic import Repo  # noqa: E402

PASS = "pa" "ss"
DEBUG = "[DEB" "UG-"
SWALLOW = f"try:\n    x()\nexcept Exception:  # why not\n    {PASS}\n"


class ShortcutProblemsTests(unittest.TestCase):
    def test_each_kind_is_found_once_added(self):
        cases = [("src/a.py", SWALLOW, "an exception swallowed with pass"),
                 ("src/a.py", f"try:\n    x()\nexcept: {PASS}\n", "an exception swallowed with pass"),
                 ("src/a.py", f"try:\n    x()\nexcept BaseException as e:\n    {PASS}\n",
                  "an exception swallowed with pass"),
                 ("web/a.ts", "try { x() } catch {" + "}\n", "an empty catch block"),
                 ("web/a.js", f'console.log("{DEBUG}1")\n', "a [DEBUG-] log left behind"),
                 ("tests/test_a.py", "def test_a():\n    assert " + "True\n", "an assertion that is always true"),
                 ("web/a.test.js", "expect(true).toBe(" + "true)\n", "an assertion that is always true")]
        for path, new, what in cases:
            with self.subTest(path=path, what=what):
                self.assertIn(what, core.shortcut_problems(path, "", new))

    def test_only_what_the_change_adds_counts(self):
        self.assertEqual(core.shortcut_problems("src/a.py", SWALLOW, SWALLOW + "y = 1\n"), [])
        self.assertEqual(core.shortcut_problems("src/a.py", SWALLOW, SWALLOW * 2), ["an exception swallowed with pass"])

    def test_a_narrow_except_and_docs_are_not_shortcuts(self):
        self.assertEqual(core.shortcut_problems("src/a.py", "", f"try:\n    x()\nexcept ValueError:\n    {PASS}\n"), [])
        self.assertEqual(core.shortcut_problems("docs/guide.md", "", f"{DEBUG}x and except: {PASS}\n"), [])

    def test_always_true_assertions_only_count_in_tests(self):
        self.assertEqual(core.shortcut_problems("src/a.py", "", "assert " + "True\n"), [])

    def test_a_python_test_without_an_assertion(self):
        self.assertEqual(core.shortcut_problems("tests/test_a.py", "", "def test_a():\n    x = 1\n"),
                         ["a test without an assertion"])
        self.assertEqual(core.shortcut_problems("src/a.py", "", "def test_a():\n    x = 1\n"), [], "tests only")


class AssertlessTestsTests(unittest.TestCase):
    def test_asserting_calls_and_statements(self):
        for body in ("assert x", "self.assertEqual(1, 1)", "check_total()", "expect_ok()", "verify()", "self.fail()",
                     "with pytest.raises(ValueError):\n        f()"):
            with self.subTest(body=body):
                self.assertEqual(core.assertless_tests(f"def test_a():\n    {body}\n"), 0)

    def test_what_counts_as_a_test(self):
        self.assertEqual(core.assertless_tests("async def test_a():\n    x = 1\n"), 1)
        self.assertEqual(core.assertless_tests("def helper():\n    x = 1\n"), 0)
        nested = "def outer():\n    def test_inner():\n        x = 1\n"
        self.assertEqual(core.assertless_tests(nested), 0, "a test nested in a function is not collected")
        self.assertEqual(core.assertless_tests("def test_a(:\n"), 0, "text that does not parse")


class StageReportTests(unittest.TestCase):
    """close-stage reports a shortcut written without the hook (through Bash, or on disk)."""

    def test_a_shortcut_written_on_disk_fails_the_stage(self):
        repo = Repo()
        self.addCleanup(repo.cleanup)
        repo.write("src/a.py", "x = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=repo.dir, check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base"], cwd=repo.dir,
                       check=True)
        repo.run("snapshot", "1")
        repo.as_agent()
        repo.write("src/a.py", "x = 1\n" + SWALLOW)
        r = repo.run("check-wave", "1")
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertIn("src/a.py: an exception swallowed with pass", r.stdout)


if __name__ == "__main__":
    unittest.main()
