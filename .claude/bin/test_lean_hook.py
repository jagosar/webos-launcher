"""The lean guardrails and the fast hook: the hook loads only the guard; the standard library only; the size budget
both ways; debt markers that name an issue; their docs and notes. Moved as they were from test_acceptance_v218.py
(design 0025, agentic-flow 2.18.0) when 2.19.0 retired it (design 0026); the release checks stayed behind.

Run: python3 -m unittest discover -s .claude/bin -p test_lean_hook.py
The hook runs in temporary copies of this branch's .claude/; the guardrails run on this repository and on temporary
roots. Nothing is spent. The debt-marker words are built from parts, so this file never holds one.
"""
import importlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parent
ROOT = BIN.parents[1]
sys.path.insert(0, str(BIN))
import core  # noqa: E402

FLOW_REPO_ONLY = unittest.skipUnless(core.is_flow_repo(ROOT), "the flow's own repo")

FORBIDDEN = ("launch", "installer", "manifest_check", "repo_scan", "brief", "findings",
             "http.client", "ssl", "urllib.request", "email.parser")
PAYLOAD = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "session_id": "s1"}
MARK = "TO" + "DO"


def temp_dir(test, prefix):
    d = Path(tempfile.mkdtemp(prefix=prefix))
    test.addCleanup(shutil.rmtree, d, ignore_errors=True)
    return d


def module(name):
    """The module under test; a missing one fails the test that needs it, not the whole file."""
    return importlib.import_module(name)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write(root, rel, text):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


# ---------- AC-001, AC-002: the hook ----------

class HookPathTests(unittest.TestCase):
    def setUp(self):
        self.path = module("test_hook_path")
        self.project = self.path.hook_copy(self)

    def test_ac_001_the_hook_loads_only_the_guard(self):
        code, stderr, modules = self.path.hook_modules(self.project, PAYLOAD)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(sorted(m for m in FORBIDDEN if m in modules), [])
        self.assertIn("guardrail", modules)
        self.assertIn("stage", modules)
        seen = json.loads((self.project / ".claude/state/hook-seen.json").read_text(encoding="utf-8"))
        self.assertEqual(seen["session_id"], "s1")

    def test_ac_002_an_unowned_write_is_still_refused(self):
        payload = {"tool_name": "Write", "tool_input": {"file_path": str(self.project / "outside.py"), "content": "x"}}
        code, stderr, _ = self.path.hook_modules(self.project, payload, env={"AGENTIC_WP": "WP-01"})
        self.assertEqual(code, 2, stderr)
        self.assertIn("WP-01 does not own outside.py", stderr)

    def test_ac_002_a_root_drift_is_still_refused(self):
        other = temp_dir(self, "v218-other-")
        subprocess.run(["git", "init", "-q"], cwd=other, check=True)
        away = temp_dir(self, "v218-away-")
        script = str(self.project / ".claude/bin/agentic.py")
        env = dict(__import__("os").environ, CLAUDE_PROJECT_DIR=str(other))
        for var in ("AGENTIC_WP", "CLAUDECODE"):
            env.pop(var, None)

        def run(*args):
            return subprocess.run([sys.executable, script, *args], cwd=away, input=json.dumps(PAYLOAD),
                                  capture_output=True, text=True, env=env, encoding="utf-8", errors="replace")
        hook, validate = run("hook"), run("validate")
        self.assertNotEqual(validate.returncode, 0, "the environment must drift")
        self.assertEqual((hook.returncode, hook.stderr), (validate.returncode, validate.stderr))


# ---------- AC-003, AC-004, AC-005: the lean guardrails ----------

class LeanTests(unittest.TestCase):
    def setUp(self):
        self.lean = module("test_lean")

    @unittest.skipIf(sys.version_info < (3, 10), "sys.stdlib_module_names is 3.10+")
    def test_ac_003_only_the_standard_library(self):
        self.assertEqual(self.lean.third_party_imports(ROOT), [])
        root = temp_dir(self, "v218-std-")
        shutil.copytree(BIN, root / ".claude/bin", ignore=shutil.ignore_patterns("__pycache__"))
        write(root, ".claude/bin/extra.py", '"""import requests is only words here"""\nimport requests\n'
                                            'import xml.etree.ElementTree\nimport http.client\nfrom . import core\n'
                                            'from .vendored import thing\n')
        self.assertEqual(self.lean.third_party_imports(root), [(".claude/bin/extra.py", "requests")])

    @FLOW_REPO_ONLY  # projects do not carry the flow's budget
    def test_ac_004_the_budget_holds_here(self):
        self.assertEqual(self.lean.budget_problems(ROOT), [])

    def budget_root(self, budget):
        root = temp_dir(self, "v218-budget-")
        write(root, ".claude/bin/a.py", "x = 1\n" * 100)
        write(root, ".claude/agents/a.md", "y" * 1000)
        if budget is not None:
            write(root, ".claude/flow-budget.json", json.dumps(budget))
        return root

    def test_ac_004_the_budget_holds_both_ways(self):
        lean = self.lean
        self.assertEqual(lean.budget_problems(self.budget_root({"code_lines": 100, "prompt_bytes": 1000, "why": "w"})),
                         [])
        over = lean.budget_problems(self.budget_root({"code_lines": 99, "prompt_bytes": 1000, "why": "w"}))
        self.assertEqual(len(over), 1, over)
        for words in ("code_lines", "100", "99", "above its budget", "flow-budget.json"):
            self.assertIn(words, over[0])
        loose = lean.budget_problems(self.budget_root({"code_lines": 100, "prompt_bytes": 1053, "why": "w"}))
        self.assertEqual(len(loose), 1, loose)
        self.assertIn("prompt_bytes", loose[0])
        self.assertIn("lower it", loose[0])
        edge = lean.budget_problems(self.budget_root({"code_lines": 105, "prompt_bytes": 1000, "why": "w"}))
        self.assertEqual(edge, [], "100 is not below 105 * 0.95 = 99.75")
        for why in ({"why": ""}, {"why": "  "}, {}):
            with self.subTest(why=why):
                found = lean.budget_problems(self.budget_root(dict({"code_lines": 100, "prompt_bytes": 1000}, **why)))
                self.assertIn("flow-budget.json needs a why", found)
        self.assertEqual(lean.budget_problems(self.budget_root(None)), ["flow-budget.json is missing"])

    def test_ac_005_debt_markers_name_an_issue(self):
        self.assertEqual(self.lean.unreferenced_markers(ROOT), [])
        root = temp_dir(self, "v218-debt-")
        write(root, ".claude/bin/a.py", f"# {MARK}: later\n# {MARK}: later (#42)\n# {MARK}: issue 42\n")
        write(root, ".claude/workflows/a.js", f"// FIX" + f"ME soon\n")
        found = self.lean.unreferenced_markers(root)
        self.assertEqual([(f, n) for f, n, _ in found], [(".claude/bin/a.py", 1), (".claude/workflows/a.js", 1)])
        self.assertEqual(found[0][2], f"# {MARK}: later")


# ---------- AC-006: docs and release ----------

@FLOW_REPO_ONLY  # a project does not carry the flow's own guides
class ReleaseTests(unittest.TestCase):
    def read(self, rel):
        return (ROOT / rel).read_text(encoding="utf-8")

    def test_ac_006_docs_and_notes_of_2_18_0(self):
        notes = load("upgrade_v218", BIN / "upgrade.py").NOTES["2.18.0"]
        self.assertGreaterEqual(len(notes), 2)
        for words in ("hook", "flow-budget.json"):
            self.assertTrue(any(words in n for n in notes), words)
        works = self.read("docs/how-it-works.md")
        self.assertIn("flow-budget.json", works)
        self.assertIn("standard library", works)
        self.assertIn("flow-budget.json", self.read("docs/how-to/plugin.md"))
        moved = self.read(".claude/bin/test_field_fixes.py")
        for name in ("AnnexTests", "ReferenceTests", "KeptCopyTests", "NetworkTests", "SessionTests", "DocsTests"):
            self.assertIn(f"\nclass {name}(", moved)


if __name__ == "__main__":
    unittest.main()
