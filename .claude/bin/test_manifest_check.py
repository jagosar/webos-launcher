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



def packages(*waves, files=1, acs=None):
    return [{"id": f"WP-{i:02d}", "wave": w, "depends_on": [], "files": [f"src/f{i}_{n}.py" for n in range(files)],
             **({"acs": acs} if acs is not None else {})} for i, w in enumerate(waves, 1)]


class ComplexityWarningTests(unittest.TestCase):
    """Design 0018 (issue 60): validate's advice, never errors."""

    def test_a_plain_plan_has_no_warnings(self):
        self.assertEqual(manifest_check.complexity_warnings(MANIFEST), [])

    def test_oversized_packages(self):
        plan = {"work_packages": packages(1, 1, files=16, acs=[f"AC-{i:03d}" for i in range(9)])}
        found = manifest_check.complexity_warnings(plan)
        self.assertIn("WP-01: owns 16 files (more than 15): consider splitting it", found)
        self.assertIn("WP-02: answers for 9 criteria (more than 8): consider splitting it", found)
        at_limit = {"work_packages": packages(1, 1, files=15, acs=["AC-001"] * 8)}
        self.assertEqual(manifest_check.complexity_warnings(at_limit), [], "the limits themselves are fine")

    def test_a_crowded_wave_and_a_chain(self):
        self.assertEqual(manifest_check.complexity_warnings({"work_packages": packages(*[1] * 9)}),
                         ["wave 1: 9 packages (more than 8): consider another wave"])
        self.assertEqual(manifest_check.complexity_warnings({"work_packages": packages(1, 2, 3)}),
                         ["every wave holds one package: the plan is a chain, nothing runs in parallel"])
        self.assertEqual(manifest_check.complexity_warnings({"work_packages": packages(1)}), [], "one wave is no chain")
        self.assertEqual(manifest_check.complexity_warnings({"work_packages": packages(1, 1, 2)}), [])

    def test_odd_values_skip_only_their_rule(self):
        plan = {"work_packages": [{"id": "WP-01", "wave": "x", "files": "src/", "acs": None}, "junk"]}
        self.assertEqual(manifest_check.complexity_warnings(plan), [])

    def test_validate_prints_them_and_other_commands_do_not(self):
        from test_agentic import Repo
        repo = Repo(dict(MANIFEST, work_packages=packages(1, 2, 3)))
        self.addCleanup(repo.cleanup)
        r = repo.run("validate")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines()[0], "Manifest valid.")
        self.assertIn("warning: every wave holds one package", r.stdout)
        self.assertNotIn("warning:", repo.run("pipeline-plan").stdout)

if __name__ == "__main__":
    unittest.main()
