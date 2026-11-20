"""The hook's import budget (design 0025): the PreToolUse hook runs on every tool call of every agent, so it loads only
the guard. Measured through `python3 -X importtime`, never through timing: names are stable on every machine.

Run: python3 -m unittest discover -s .claude/bin -p test_hook_path.py
The hook runs in a temporary copy of this checkout's .claude/bin, settings.json and agents.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parent
sys.path.insert(0, str(BIN))
from test_agentic import MANIFEST  # noqa: E402

# What made the hook slow in 2.17.0: none of them is needed to guard a tool call.
HOOK_FORBIDDEN_MODULES = ("launch", "installer", "manifest_check", "repo_scan", "brief", "findings",
                          "http.client", "ssl", "urllib.request", "email.parser")
IMPORT_LINE = "import time:"
BASH_CALL = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "session_id": "s1"}


def hook_copy(test):
    """A temporary git repo with copies of .claude/bin, settings.json and agents, and architecture.md holding
    test_agentic.MANIFEST; removed when the test ends. test_agentic.Repo does not copy .claude/bin."""
    project = Path(tempfile.mkdtemp(prefix="hook-path-"))
    test.addCleanup(shutil.rmtree, project, ignore_errors=True)
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    shutil.copytree(BIN, project / ".claude/bin", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(BIN.parent / "agents", project / ".claude/agents")
    shutil.copy(BIN.parent / "settings.json", project / ".claude/settings.json")
    body = json.dumps(MANIFEST, indent=2)
    (project / "architecture.md").write_text(f"# A\n\n```json wp-manifest\n{body}\n```\n", encoding="utf-8")
    return project


def hook_modules(project, payload, env=None):
    """(returncode, stderr, modules) of `python3 -X importtime <project>/.claude/bin/hook.py` with payload as JSON on
    stdin. modules: the names on the 'import time:' lines, header skipped; stderr: every other line."""
    run_env = {k: v for k, v in os.environ.items() if k not in ("AGENTIC_WP", "CLAUDECODE", "CLAUDE_CODE_SESSION_ID")}
    run_env.update(env or {}, CLAUDE_PROJECT_DIR=str(project))
    r = subprocess.run([sys.executable, "-X", "importtime", str(project / ".claude/bin/hook.py")], cwd=project,
                       input=json.dumps(payload), capture_output=True, text=True, env=run_env, encoding="utf-8",
                       errors="replace")
    modules, other = set(), []
    for line in r.stderr.splitlines():
        if not line.startswith(IMPORT_LINE):
            other.append(line)
            continue
        name = line.rsplit("|", 1)[-1].strip()
        if name != "imported package":
            modules.add(name)
    return r.returncode, "\n".join(other), modules


class HookImportBudgetTests(unittest.TestCase):
    def test_the_hook_loads_no_forbidden_module(self):
        code, stderr, modules = hook_modules(hook_copy(self), BASH_CALL)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(sorted(m for m in HOOK_FORBIDDEN_MODULES if m in modules), [],
                         "the hook path must load only the guard: import these lazily, or not from guardrail")

    def test_the_parser_reads_names_and_keeps_other_lines(self):
        code, stderr, modules = hook_modules(hook_copy(self), {"not": "a tool call"})
        self.assertEqual(code, 0, stderr)
        self.assertIn("guardrail", modules)
        self.assertNotIn("imported package", modules)
        self.assertFalse(any(line.startswith(IMPORT_LINE) for line in stderr.splitlines()))


if __name__ == "__main__":
    unittest.main()
