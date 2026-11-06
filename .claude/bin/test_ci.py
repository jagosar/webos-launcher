"""Tests of the agentic-flow repository's own GitHub workflows: the CI matrix, the ratchet, the history job and the
release workflow's safety. Moved here from the retired per-release acceptance files (design 0017).

Run: python3 -m unittest discover -s .claude/bin -p "test_ci.py"
Every test is for the agentic-flow repository only: projects receive the CI file and may change it.
"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_definitions import FLOW_REPO_ONLY, ROOT  # noqa: E402

WORKFLOWS = ROOT / ".github" / "workflows"


def block(text, header_re):
    """The lines from the first line matching header_re up to the next line at the same or a lower indentation."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if re.match(header_re, line))
    indent = len(lines[start]) - len(lines[start].lstrip())
    out = [lines[start]]
    for line in lines[start + 1:]:
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        out.append(line)
    return "\n".join(out)


def run_scripts(text):
    """The text of every run: step, its continuation lines included."""
    lines = text.splitlines()
    return [block("\n".join(lines[i:]), r"^\s*(?:- )?run:") for i, line in enumerate(lines)
            if re.match(r"^\s*(?:- )?run:", line)]


@FLOW_REPO_ONLY
class CiTests(unittest.TestCase):
    """From test_acceptance_portable ac_011, test_acceptance_v27 ac_008 and test_acceptance_v29 ac_009."""

    def setUp(self):
        self.ci = (WORKFLOWS / "agentic-flow-ci.yml").read_text(encoding="utf-8")

    def test_the_matrix_covers_linux_and_windows_and_three_pythons_in_bash(self):
        self.assertRegex(self.ci, r"runs-on:\s*\$\{\{\s*matrix\.os\s*\}\}")
        for needle in ("ubuntu-latest", "windows-latest", '"3.9"', '"3.12"', '"3.14"'):
            self.assertIn(needle, self.ci)
        self.assertRegex(self.ci, r"shell:\s*bash")

    def test_the_ratchet_runs(self):
        self.assertIn("python .claude/bin/agentic.py quality", self.ci)

    def test_the_history_job_checks_on_a_full_clone_and_the_others_stay_shallow(self):
        job = block(self.ci, r"^  history:")
        for needle in ("github.repository == 'jagosar/agentic-flow'", "fetch-depth: 0", "persist-credentials: false",
                       "agentic.py flow-history --check"):
            self.assertIn(needle, job)
        for name in ("test", "installed", "plugin"):
            self.assertNotIn("fetch-depth", block(self.ci, rf"^  {name}:"))
        paths = re.findall(r"^\s*paths:\s*\[(.*)\]", self.ci, re.M)
        self.assertEqual(len(paths), 2)
        for line in paths:
            self.assertIn('".github/workflows/agentic-flow-release.yml"', line)


@FLOW_REPO_ONLY
class ReleaseWorkflowTests(unittest.TestCase):
    """From test_acceptance_v29 ac_008: the release workflow only publishes from main, after its checks."""

    def setUp(self):
        self.text = (WORKFLOWS / "agentic-flow-release.yml").read_text(encoding="utf-8")

    def test_triggers_and_permissions(self):
        on = block(self.text, r"^on:")
        self.assertIn("workflow_dispatch", on)
        self.assertRegex(on, r"push:\s*\n\s+branches:\s*\[main\]")
        self.assertIn(".claude-plugin/plugin.json", on)
        for forbidden in ("pull_request", "schedule", "tags"):
            self.assertNotIn(forbidden, on)
        self.assertRegex(block(self.text, r"^permissions:"), r"permissions:\s*\n\s+contents: read\s*$")
        self.assertRegex(block(self.text, r"^jobs:"), r"permissions:\s*\n\s+contents: write")

    def test_guards_and_hardening(self):
        for needle in ("fetch-depth: 0", "persist-credentials: false", "github.repository == 'jagosar/agentic-flow'",
                       "github.ref == 'refs/heads/main'", "GH_TOKEN"):
            self.assertIn(needle, self.text)
        self.assertNotIn("softprops", self.text)
        for script in run_scripts(self.text):
            self.assertNotIn("${{", script, "values reach scripts through env:, never through ${{ }}")

    def test_nothing_is_tagged_before_the_checks(self):
        order = [self.text.find(s) for s in ("already exists", "agentic.py flow-history --check",
                                             "agentic.py release-notes", "gh release create")]
        self.assertTrue(all(i >= 0 for i in order), order)
        self.assertEqual(order, sorted(order))


if __name__ == "__main__":
    unittest.main()
