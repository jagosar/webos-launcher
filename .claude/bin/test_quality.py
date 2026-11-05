"""Unit tests of quality.py: measurement edge cases and the ratchet's bookkeeping.

Run: python3 -m unittest discover -s .claude/bin -p "test_quality.py"
Pure functions on inline sources; the files test uses a temporary folder.
"""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import quality  # noqa: E402

SCAN_TIMEOUT_S = 20  # a scan of three lines takes milliseconds; a hang must fail, not stall the suite


def fn(name, lines):
    return f"def {name}():\n" + "    x = 1\n" * (lines - 1)


class TestFileTests(unittest.TestCase):
    def test_test_files_by_name_or_folder(self):
        for rel in ("test_a.py", "a_test.py", "src/a.test.ts", "web/b.spec.js", "tests/helpers.py", "x/__tests__/h.js"):
            with self.subTest(rel=rel):
                self.assertTrue(quality.is_test_file(rel))
        for rel in ("src/a.py", "contest.py", "testing/a.py", "src/spec.js"):
            with self.subTest(rel=rel):
                self.assertFalse(quality.is_test_file(rel))

    def test_exclude_prefixes_match_whole_segments(self):
        self.assertTrue(quality.excluded("vendor/x.py", ["vendor"]))
        self.assertTrue(quality.excluded("vendor", ["vendor/"]))
        self.assertFalse(quality.excluded("vendor2/x.py", ["vendor"]))
        self.assertTrue(quality.excluded("bench/fixtures/a/b.py", ["bench/fixtures/*"]))
        self.assertFalse(quality.excluded("src/a.py", []))


class PythonTests(unittest.TestCase):
    def test_repeated_names_are_numbered_in_order(self):
        src = "def f():\n    pass\n\n\ndef f():\n    pass\n    pass\n"
        self.assertEqual(quality.measure_file("a.py", src)["functions"], {
            "f": {"lines": 2, "nesting": 0}, "f #2": {"lines": 3, "nesting": 0}})

    def test_async_and_with_count_as_levels(self):
        src = "async def f(a):\n    async with a:\n        for x in a:\n            try:\n                pass\n" \
              "            except ValueError:\n                pass\n"
        self.assertEqual(quality.measure_file("a.py", src)["functions"]["f"]["nesting"], 3)

    def test_an_else_block_is_not_an_elif(self):
        src = "def f(x):\n    if x:\n        pass\n    else:\n        if x:\n            pass\n"
        self.assertEqual(quality.measure_file("a.py", src)["functions"]["f"]["nesting"], 2)

    def test_pyi_goes_through_ast(self):
        self.assertIn("f", quality.measure_file("a.pyi", "def f() -> int: ...\n")["functions"])

    def test_other_languages_only_get_file_metrics(self):
        m = quality.measure_file("a.go", "func F() {\n}\n" + "x" * 130 + "\n")
        self.assertEqual((m["lines"], m["long_lines"], m["functions"]), (3, 1, {}))


class JavaScriptTests(unittest.TestCase):
    def test_control_keywords_are_not_methods(self):
        src = "function f(x) {\n  if (x) {\n    while (x) {\n      x--\n    }\n  }\n}\n"
        fns = quality.measure_file("a.js", src)["functions"]
        self.assertEqual(list(fns), ["f"])
        self.assertEqual(fns["f"]["nesting"], 2)

    def test_object_literal_methods_take_the_object_name(self):
        src = "const api = {\n  get(id) {\n    return id\n  },\n  put: function (x) {\n    return x\n  },\n}\n"
        self.assertEqual(sorted(quality.measure_file("a.js", src)["functions"]), ["api.get", "api.put"])

    def test_catch_and_finally_stay_at_the_try_level(self):
        src = "function f() {\n  try {\n    a()\n  } catch (e) {\n    b()\n  } finally {\n    c()\n  }\n}\n"
        self.assertEqual(quality.measure_file("a.ts", src)["functions"]["f"]["nesting"], 1)

    def test_escaped_quotes_and_regex_free_strings(self):
        src = "function f() {\n  const s = 'a \\' { b'\n  const t = \"}\"\n  return s + t\n}\n"
        self.assertEqual(quality.measure_file("a.js", src)["functions"]["f"]["lines"], 5)

    def test_unclosed_comments_and_strings_end_the_scan(self):
        # An unclosed /* (or a regex literal such as /\/\*/ read as one) once sent the scanner backwards forever.
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import quality; "
                "print(quality.measure_file('a.js', 'function f() {\\n}\\n/* never closed')['lines'])")
        r = subprocess.run([sys.executable, "-c", code, str(Path(__file__).resolve().parent)],
                           capture_output=True, text=True, timeout=SCAN_TIMEOUT_S)
        self.assertEqual(r.stdout.strip(), "3", r.stderr)

    def test_an_unclosed_brace_does_not_raise(self):
        self.assertEqual(quality.measure_file("a.js", "function f() {\n  if (x) {\n")["functions"], {})


class MeasureTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="quality-"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def test_files_and_test_flag(self):
        (self.dir / "a.py").write_text(fn("big", 3), encoding="utf-8")
        (self.dir / "test_a.py").write_bytes(b"def test_x():\r\n    pass\r\n")
        got = quality.measure(self.dir, ["a.py", "test_a.py", "missing.py"], quality.DEFAULT_LIMITS)
        self.assertEqual(sorted(got["files"]), ["a.py", "test_a.py"])
        self.assertIs(got["functions"]["test_a.py::test_x"]["test"], True)
        self.assertIs(got["functions"]["a.py::big"]["test"], False)
        self.assertEqual(got["files"]["test_a.py"]["long_lines"], 0, "CRLF is not counted as characters")


class RatchetTests(unittest.TestCase):
    def current(self, lines, nesting=1, test=False):
        return {"files": {"a.py": {"lines": 900, "long_lines": 1}},
                "functions": {"a.py::f": {"lines": lines, "nesting": nesting, "test": test}}}

    def test_initial_records_offenders_and_passes(self):
        cur = self.current(70)
        base = quality.initial(cur, {}, False)
        self.assertEqual(base["functions"], {"a.py::f": {"lines": 70, "nesting": 1}})
        self.assertEqual(base["files"], {"a.py": {"lines": 900, "long_lines": 1}})
        self.assertTrue(quality.check(base, cur)["ok"])

    def test_initial_keeps_a_within_limits_test_out(self):
        self.assertEqual(quality.initial(self.current(90, 7, test=True), {}, False)["functions"], {})

    def test_missing_limit_keys_fall_back_to_defaults(self):
        base = {"limits": {"function_lines": 10}, "functions": {}, "files": {}}
        current = {"files": {}, "functions": {"a.py::f": {"lines": 11, "nesting": 5, "test": False}}}
        result = quality.check(base, current)
        self.assertEqual(len(result["violations"]), 2)

    def test_details_carry_the_values(self):
        base = quality.initial(self.current(70), {}, False)
        result = quality.check(base, self.current(75))
        self.assertEqual(result["details"], ["  a.py::f lines: now 75, allowed 70"])

    def test_tighten_drops_what_no_longer_exists(self):
        base = quality.initial(self.current(70), {}, False)
        empty = quality.tighten(base, {"files": {}, "functions": {}})
        self.assertEqual((empty["functions"], empty["files"]), ({}, {}))


if __name__ == "__main__":
    unittest.main()
