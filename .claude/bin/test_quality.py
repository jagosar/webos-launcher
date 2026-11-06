"""Unit tests of quality.py: measurement edge cases and the ratchet's bookkeeping.

Run: python3 -m unittest discover -s .claude/bin -p "test_quality.py"
Pure functions on inline sources; the files test uses a temporary folder.
"""
import ast
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parent
sys.path.insert(0, str(BIN))
import quality  # noqa: E402
from test_agentic import PY, Repo  # noqa: E402

BASELINE = ".claude/quality-baseline.json"

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



# ---------- moved from the retired per-release acceptance files (design 0017) ----------

NESTED_PY = """def f():
    a = 1
    b = 2
    return a + b


class C:
    def g(self):
        if self:
            for i in range(3):
                while i:
                    i -= 1


def chain(x):
    if x == 1:
        return 1
    elif x == 2:
        return 2
    else:
        return 0


def outer():
    def inner():
        if True:
            return 1
    return inner


LONG = \"""" + "x" * 120 + """\"
"""

MIXED_JS = """function a(x, y) {
  if (x) {
    return 1
  } else if (y) {
    return 2
  } else {
    return 3
  }
}
const b = async (p) => {
  const s = `template { with } braces ${p}`
  // a comment with { braces } and }
  for (const i of p) {
    try {
      if (i) {
        items.map(j => { return j })
      }
    } catch (e) {
      throw e
    }
  }
}
class C {
  m() {
    return '{'
  }
}
"""


def stored(**extra):
    data = {"version": 1, "flow_repo": False, "limits": dict(quality.DEFAULT_LIMITS), "exclude": [],
            "files": {"p.py": {"lines": 900, "long_lines": 2}},
            "functions": {"p.py::big": {"lines": 90, "nesting": 5}}}
    data.update(extra)
    return data


def measured(big=(90, 5), file=(900, 2), functions=None, files=None):
    funcs = {"p.py::big": {"lines": big[0], "nesting": big[1], "test": False}}
    funcs.update(functions or {})
    all_files = {"p.py": {"lines": file[0], "long_lines": file[1]}}
    all_files.update(files or {})
    return {"files": all_files, "functions": funcs}


def last_json(text):
    return json.loads([line for line in text.splitlines() if line.strip().startswith("{")][-1])


class NestingTests(unittest.TestCase):
    """From test_acceptance_v27 ac_001 and ac_002."""

    def test_python_elif_chains_and_nested_defs(self):
        m = quality.measure_file("m.py", NESTED_PY)
        fns = m["functions"]
        self.assertEqual(fns["C.g"]["nesting"], 3)
        self.assertEqual(fns["chain"]["nesting"], 1, "an elif chain is one level")
        self.assertEqual(fns["outer"]["nesting"], 0, "a nested def does not add to its parent")
        self.assertEqual(fns["outer.inner"]["nesting"], 1)
        self.assertEqual((m["lines"], m["long_lines"]), (len(NESTED_PY.splitlines()), 1))

    def test_a_python_syntax_error_still_counts_lines(self):
        m = quality.measure_file("broken.py", "def f(:\n    pass\n")
        self.assertEqual((m["functions"], m["lines"]), ({}, 2))

    def test_javascript_strings_templates_classes_and_callbacks(self):
        fns = quality.measure_file("w.js", MIXED_JS)["functions"]
        self.assertEqual(fns["a"]["nesting"], 1, "else if is not a deeper level")
        self.assertEqual(fns["b"]["nesting"], 3, "braces in strings, templates and comments do not count")
        self.assertEqual((fns["a"]["lines"], fns["b"]["lines"], fns["C.m"]["lines"]), (9, 13, 3))
        self.assertFalse([k for k in fns if k not in ("a", "b", "C", "C.m")], "a callback is not a function")


class CheckTests(unittest.TestCase):
    """From test_acceptance_v27 ac_003: every kind of regression fails, and the ratchet only lowers."""

    def assert_fails(self, cur, path="p.py"):
        result = quality.check(stored(), cur)
        self.assertFalse(result["ok"], cur)
        for line in result["violations"]:
            self.assertTrue(line.startswith("FAIL ") and path in line, line)
            self.assertIsNone(re.search(r"\b\d+\b", line.replace(path, "")), f"no values: {line}")
        return result["violations"]

    def test_every_kind_of_regression_fails_without_values(self):
        cases = [measured(big=(91, 5)), measured(big=(90, 6)),
                 measured(functions={"p.py::new": {"lines": 61, "nesting": 1, "test": False}}),
                 measured(functions={"p.py::new": {"lines": 10, "nesting": 5, "test": False}}),
                 measured(file=(931, 2)), measured(file=(900, 3))]
        for cur in cases:
            with self.subTest(cur=cur):
                self.assertTrue(self.assert_fails(cur))
        test_file = {"test_x.py": {"lines": 120, "long_lines": 0}}
        self.assert_fails(measured(functions={"test_x.py::test_a": {"lines": 101, "nesting": 1, "test": True}},
                                   files=test_file), path="test_x.py")
        self.assert_fails(measured(files={"n.py": {"lines": 801, "long_lines": 0}}), path="n.py")
        self.assert_fails(measured(files={"n.py": {"lines": 10, "long_lines": 1}}), path="n.py")
        self.assertEqual(self.assert_fails(measured(big=(95, 5))), self.assert_fails(measured(big=(92, 5))))

    def test_same_or_better_passes_and_improvements_are_listed(self):
        self.assertTrue(quality.check(stored(), measured())["ok"])
        improved = quality.check(stored(), measured(big=(50, 3)))
        self.assertTrue(improved["ok"])
        self.assertTrue(any("p.py::big" in item for item in improved["improvable"]), improved)
        self.assertTrue(quality.check(stored(), measured(file=(930, 2)))["ok"], "growth up to file_growth")
        tests = measured(functions={"test_x.py::test_a": {"lines": 100, "nesting": 9, "test": True}},
                         files={"test_x.py": {"lines": 120, "long_lines": 0}})
        self.assertTrue(quality.check(stored(), tests)["ok"], "a test function's nesting is not limited")

    def test_tighten_only_lowers(self):
        self.assertEqual(quality.tighten(stored(), measured(big=(80, 5)))["functions"]["p.py::big"],
                         {"lines": 80, "nesting": 5})
        self.assertNotIn("p.py::big", quality.tighten(stored(), measured(big=(50, 3)))["functions"])
        never_raised = quality.tighten(stored(), measured(big=(95, 6), file=(920, 4)))
        self.assertEqual(never_raised["functions"]["p.py::big"], {"lines": 90, "nesting": 5})
        self.assertEqual(never_raised["files"]["p.py"], {"lines": 900, "long_lines": 2})
        new = measured(functions={"p.py::new": {"lines": 70, "nesting": 1, "test": False}})
        none_added = quality.tighten(stored(), new)
        self.assertNotIn("p.py::new", none_added["functions"])
        self.assertEqual(none_added["limits"], quality.DEFAULT_LIMITS)


class QualityCase(unittest.TestCase):
    def repo(self, manifest=None):
        from test_agentic import MANIFEST
        r = Repo(manifest or MANIFEST)
        self.addCleanup(r.cleanup)
        return r


class CommandTests(QualityCase):
    """From test_acceptance_v27 ac_004: the quality command in a project."""

    def test_init_check_update(self):
        r = self.repo()
        r.write("src/a.py", fn("big", 80) + "\n\n" + fn("small", 5))
        self.assertIn("skipped", last_json(r.run("quality").stdout))
        self.assertEqual(r.run("quality", "--init").returncode, 0)
        path = r.dir / BASELINE
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertIs(data["flow_repo"], False)
        self.assertEqual(data["functions"]["src/a.py::big"]["lines"], 80)
        before = path.read_bytes()
        self.assertEqual(r.run("quality", "--init").returncode, 1)
        self.assertEqual(path.read_bytes(), before)
        r.write("src/a.py", fn("big", 90) + "\n\n" + fn("small", 5))
        worse = r.run("quality")
        self.assertEqual(worse.returncode, 3, worse.stdout)
        self.assertTrue([line for line in worse.stdout.splitlines() if line.startswith("FAIL") and "src/a.py" in line])
        self.assertEqual(path.read_bytes(), before, "a plain check never writes the baseline")
        r.write("src/a.py", fn("big", 70) + "\n\n" + fn("small", 5))
        self.assertEqual(r.run("quality", "--update").returncode, 0)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["functions"]["src/a.py::big"]["lines"], 70)

    def test_flow_files_stay_out_of_a_projects_baseline(self):
        r = self.repo()
        r.write("src/a.py", fn("small", 5))
        r.write(".claude/bin/tool.py", fn("huge", 200))
        self.assertEqual(r.run("quality", "--init").returncode, 0)
        data = json.loads((r.dir / BASELINE).read_text(encoding="utf-8"))
        self.assertFalse([k for k in list(data["functions"]) + list(data["files"]) if k.startswith(".claude/")])

    def test_a_baseline_from_the_flow_repo_is_ignored_in_a_project(self):
        r = self.repo()
        r.write("src/a.py", fn("big", 80))
        r.write(BASELINE, json.dumps(dict(stored(), flow_repo=True)))
        result = r.run("quality")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("skipped", last_json(result.stdout))


def verify_manifest(with_verify=True):
    m = {"allow_bash": ["Bash(python3 -m unittest:*)"],
         "acceptance": {"files": ["acceptance/"], "command": f"{PY} acc.py"},
         "work_packages": [{"id": "WP-01", "wave": 1, "depends_on": [], "files": ["src/a.py"], "acs": ["AC-001"]}]}
    if with_verify:
        m["verify"] = {"tests": f"{PY} -c pass"}
    return m


class VerifyPhaseTests(QualityCase):
    """From test_acceptance_v27 ac_005: the quality phase of baseline and verify."""

    def prepared(self, with_verify=True):
        r = self.repo(verify_manifest(with_verify))
        r.write("acc.py", "")
        r.write("src/a.py", fn("small", 5))
        self.assertEqual(r.run("quality", "--init").returncode, 0)
        return r

    def phase(self, out):
        return next((p for p in last_json(out)["phases"] if p["name"] == "quality"), None)

    def test_a_worse_package_blocks_and_is_named(self):
        r = self.prepared()
        self.assertEqual(r.run("baseline").returncode, 0)
        r.write("src/a.py", fn("small", 70))
        out = r.run("verify")
        self.assertEqual(out.returncode, 3, out.stdout + out.stderr)
        self.assertIn(self.phase(out.stdout)["status"], ("regression", "fails"))
        report = last_json(out.stdout)
        self.assertIn("quality", report["blocking"])
        self.assertIn("src/a.py", report["suspects"]["WP-01"]["files"])

    def test_a_pre_existing_violation_does_not_block(self):
        r = self.prepared()
        r.write("src/a.py", fn("small", 70))
        r.run("baseline")
        out = r.run("verify")
        self.assertEqual(self.phase(out.stdout)["status"], "pre-existing failure", out.stdout)
        self.assertNotIn("quality", last_json(out.stdout)["blocking"])

    def test_baseline_measures_quality_without_verify_commands(self):
        report = last_json(self.prepared(with_verify=False).run("baseline").stdout)
        self.assertNotIn("skipped", report)
        self.assertTrue([p for p in report["phases"] if p["name"] == "quality"], report)

    def test_without_a_baseline_there_is_no_phase(self):
        r = self.repo(verify_manifest())
        r.write("acc.py", "")
        r.write("src/a.py", fn("small", 70))
        r.run("baseline")
        self.assertIsNone(self.phase(r.run("verify").stdout))


class EncodingTests(unittest.TestCase):
    """From test_acceptance_portable ac_004: text I/O in the flow's modules always names its encoding."""

    def test_every_text_open_and_subprocess_declares_an_encoding(self):
        missing = []
        for path in sorted(p for p in BIN.glob("*.py") if not p.name.startswith("test")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                problem = undeclared_encoding(node)
                if problem:
                    missing.append(f"{path.name}:{node.lineno} {problem}")
        self.assertEqual(missing, [])


def undeclared_encoding(node):
    """The call's name when it reads or writes text without an encoding; None otherwise."""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
    kws = {k.arg: k.value for k in node.keywords}
    if "encoding" in kws:
        return None
    if name in ("open", "read_text", "write_text"):
        mode = kws.get("mode")
        if mode is None and name == "open":
            positional = node.args[1:2] if isinstance(func, ast.Name) else node.args[0:1]
            mode = positional[0] if positional else None
        binary = isinstance(mode, ast.Constant) and isinstance(mode.value, str) and "b" in mode.value
        return None if binary else name
    is_subprocess = isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and \
        func.value.id == "subprocess" and name in ("run", "Popen")
    text = kws.get("text")
    return f"subprocess.{name}" if is_subprocess and isinstance(text, ast.Constant) and text.value is True else None

if __name__ == "__main__":
    unittest.main()
