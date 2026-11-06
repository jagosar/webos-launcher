"""Tests of manifest_check.py beyond test_agentic's ValidateTests: commands that would allow anything, and the
never-owned files of a plan for the flow itself. Moved here from the retired per-release acceptance files (design 0017).

Run: python3 -m unittest discover -s .claude/bin -p "test_manifest_check.py"
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agentic  # noqa: E402
import core  # noqa: E402
import manifest_check  # noqa: E402
from test_agentic import MANIFEST  # noqa: E402


def errors(**fields):
    return agentic.validate(dict(MANIFEST, **fields))


class OpenEndedCommandTests(unittest.TestCase):
    """From test_acceptance_waiting ac_007: a command whose program or code is a glob allows anything."""

    def test_a_glob_as_the_program_or_its_code_is_too_broad(self):
        for cmd in ("bash -c *", "python3 -c *", "npm *"):
            with self.subTest(cmd=cmd):
                self.assertTrue(any("too broad" in e for e in errors(verify={"tests": cmd})), cmd)

    def test_a_glob_in_a_later_argument_is_accepted(self):
        good = errors(verify={"tests": "python3 -m unittest discover -s .claude/bin -p test_*.py"},
                      acceptance={"files": ["acceptance/"], "command": "python3 -m pytest tests/*_acceptance.py"})
        self.assertEqual(good, [])
        self.assertTrue(errors(allow_bash=["Bash(npm:*)"]), "allow_bash keeps its own rule")


class ByHandPlanTests(unittest.TestCase):
    """From test_acceptance_v27 ac_006: a plan for the flow itself still cannot own the quality baseline."""

    def test_the_quality_baseline_is_never_owned(self):
        plan = {"maintainer": True, "acceptance": {"files": ["acceptance/"], "command": "x"},
                "work_packages": [{"id": "WP-01", "wave": 1, "depends_on": [], "files": [core.QUALITY_BASELINE]}]}
        with mock.patch.object(core, "is_flow_repo", return_value=True), \
                mock.patch.object(manifest_check, "is_flow_repo", return_value=True):
            self.assertTrue([e for e in agentic.validate(plan) if "quality-baseline" in e and "never owned" in e])


if __name__ == "__main__":
    unittest.main()
