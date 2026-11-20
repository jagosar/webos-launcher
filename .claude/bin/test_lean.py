"""The flow on a diet (design 0025): it may not depend on anything outside the standard library, it may not leave debt
markers that name no issue, and its size may not move past .claude/flow-budget.json without a visible decision.

Run: python3 -m unittest discover -s .claude/bin -p test_lean.py
The helpers take a root, so the acceptance tests run them on temporary roots too. The debt-marker words are built from
parts, so this file never holds one. Only the budget test needs the flow's own repository.
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
ROOT = BIN.parents[1]
sys.path.insert(0, str(BIN))
import core  # noqa: E402

FLOW_REPO_ONLY = unittest.skipUnless(core.is_flow_repo(ROOT), "the flow's own repo")
BUDGET_FILE = ".claude/flow-budget.json"
BUDGET_SLACK = 0.05  # a measure more than 5 % below its budget: lower the budget, so a diet sticks
MEASURES = ("code_lines", "prompt_bytes")
PROMPT_GLOBS = (".claude/agents/*.md", ".claude/commands/*.md", ".claude/rules-library/*.md")
DEBT_RE = re.compile(r"\b(" + "|".join(("TO" + "DO", "FIX" + "ME", "X" + "XX", "HA" + "CK")) + r")\b")
ISSUE_RE = re.compile(r"#\d+|\bissue \d+", re.I)
JS_SPEC_RES = (re.compile(r"""require\(\s*['"]([^'"]+)['"]\s*\)"""),
               re.compile(r"""^\s*import\s.*?\sfrom\s+['"]([^'"]+)['"]""", re.M))


def _code_files(root):
    root = Path(root)
    py = [p for p in sorted((root / ".claude/bin").glob("*.py")) if not p.name.startswith("test")]
    return py + sorted((root / ".claude/workflows").glob("*.js"))


def _rel(root, path):
    return path.relative_to(root).as_posix()


def code_lines(root):
    """Lines of the flow's code: .claude/bin/*.py but tests and testutil, and .claude/workflows/*.js."""
    return sum(len(p.read_text(encoding="utf-8").splitlines()) for p in _code_files(root))


def prompt_bytes(root):
    """Bytes of what the agents read as instructions: agents, commands and the rules library, with LF line endings
    (a Windows checkout writes CRLF)."""
    root = Path(root)
    return sum(len(p.read_bytes().replace(b"\r\n", b"\n")) for pattern in PROMPT_GLOBS for p in root.glob(pattern))


def budget_problems(root):
    """[message]: each measure against flow-budget.json, above it or more than BUDGET_SLACK below it; and its why."""
    path = Path(root) / BUDGET_FILE
    try:
        budget = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ["flow-budget.json is missing"]
    found = {"code_lines": code_lines(root), "prompt_bytes": prompt_bytes(root)}
    problems = []
    for name in MEASURES:
        m, b = found[name], budget.get(name, 0)
        if m > b:
            problems.append(f"{name} is {m}, above its budget {b} in flow-budget.json: raise it, with a why")
        elif m < b * (1 - BUDGET_SLACK):
            problems.append(f"{name} is {m}, more than 5 % below its budget {b} in flow-budget.json: lower it")
    if not str(budget.get("why") or "").strip():
        problems.append("flow-budget.json needs a why")
    return problems


def _python_imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module.split(".")[0]


def _node_builtins():
    """Node's built-in module names, or None when node is not installed."""
    node = shutil.which("node")
    if not node:
        return None
    r = subprocess.run([node, "-e", "console.log(JSON.stringify(require('module').builtinModules))"],
                       capture_output=True, text=True)
    return set(json.loads(r.stdout)) if r.returncode == 0 else None


def _js_imports(path):
    text = path.read_text(encoding="utf-8")
    return [s for rx in JS_SPEC_RES for s in rx.findall(text) if not s.startswith(".")]


def third_party_imports(root):
    """Sorted [(file, module)] of imports in the flow's code that are neither the standard library, a module of
    .claude/bin nor a Node built-in. Needs Python 3.10+ (sys.stdlib_module_names)."""
    root = Path(root)
    local = {p.stem for p in (root / ".claude/bin").glob("*.py")}
    allowed = set(sys.stdlib_module_names) | local
    found = set()
    for path in _code_files(root) + [p for p in (root / ".claude/bin").glob("test*.py")]:
        if path.suffix == ".py":
            found |= {(_rel(root, path), m) for m in _python_imports(path) if m not in allowed}
            continue
        specs = _js_imports(path)
        builtins = _node_builtins() if specs else set()
        if builtins is not None:
            found |= {(_rel(root, path), s) for s in specs if s.split("node:")[-1].split("/")[0] not in builtins}
    return sorted(found)


def unreferenced_markers(root):
    """[(file, line_no, text)] of debt markers in .claude/bin/*.py and .claude/workflows/*.js that name no issue on
    the same line; text is the line stripped."""
    root = Path(root)
    files = sorted((root / ".claude/bin").glob("*.py")) + sorted((root / ".claude/workflows").glob("*.js"))
    return [(_rel(root, path), n, line.strip())
            for path in files for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if DEBT_RE.search(line) and not ISSUE_RE.search(line)]


class LeanTests(unittest.TestCase):
    @unittest.skipIf(sys.version_info < (3, 10), "sys.stdlib_module_names is 3.10+")
    def test_only_the_standard_library(self):
        self.assertEqual(third_party_imports(ROOT), [],
                         "the flow ships with no dependencies: use the standard library, or justify it in a design")

    def test_debt_markers_name_an_issue(self):
        self.assertEqual(unreferenced_markers(ROOT), [], "open an issue and name it on the same line (#123)")

    @FLOW_REPO_ONLY  # projects do not carry the flow's budget
    def test_the_flow_stays_within_its_budget(self):
        self.assertEqual(budget_problems(ROOT), [])

    def test_prompt_bytes_ignore_line_endings(self):
        # A Windows checkout (core.autocrlf=true) writes CRLF: the budget must measure the same flow.
        with tempfile.TemporaryDirectory() as d:
            agents = Path(d) / ".claude/agents"
            agents.mkdir(parents=True)
            (agents / "lf.md").write_bytes(b"a\nb\n")
            (agents / "crlf.md").write_bytes(b"a\r\nb\r\n")
            self.assertEqual(prompt_bytes(d), 8)

    def test_relative_and_local_imports_are_never_reported(self):
        self.assertEqual(sorted(_python_imports_of("from . import x\nfrom .a import b\nimport os.path\n")), ["os"])


def _python_imports_of(text):
    """The checked names of a Python snippet, for the tests."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "snippet.py"
        p.write_text(text, encoding="utf-8")
        return list(_python_imports(p))


if __name__ == "__main__":
    unittest.main()
