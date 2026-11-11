"""Tests of manifest_check.py beyond test_agentic's ValidateTests: commands that would allow anything, the never-owned
files of a plan for the flow itself, the waivers and the Source column of section 4b. Moved here from the retired
per-release acceptance files (designs 0017 and 0021).

Run: python3 -m unittest discover -s .claude/bin -p "test_manifest_check.py"
"""
import json
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agentic  # noqa: E402
import core  # noqa: E402
import manifest_check  # noqa: E402
from test_agentic import MANIFEST, Repo  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
WAIVER = {"rule": "No magic numbers", "scope": "src/b/x.py", "reason": "ana: tax table constants are the spec"}


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

class WaiverTests(unittest.TestCase):
    """waiver_errors: one error per waiver, the first that applies (design 0020)."""

    def errors(self, waivers, rules="no magic numbers in code"):
        manifest = dict(MANIFEST, waivers=waivers)
        with mock.patch.object(manifest_check, "installed_rules_text", return_value=rules):
            return manifest_check.waiver_errors(manifest)

    def test_no_waivers_is_fine_and_a_non_list_is_an_error(self):
        self.assertEqual(manifest_check.waiver_errors(MANIFEST), [])
        self.assertEqual(self.errors("all"), ['waivers: must be a list of {"rule", "scope", "reason"}.'])

    def test_a_bad_shape_is_one_error(self):
        for waiver in ({"rule": "x"}, {"rule": 1, "scope": "src/a.py", "reason": "r"}, "text"):
            with self.subTest(waiver=waiver):
                self.assertEqual(self.errors([waiver]), ['waivers[0]: must be {"rule", "scope", "reason"}, all text'])

    def test_a_scope_inside_a_folder_zone_is_narrow_enough(self):
        ok = {"rule": "Magic numbers", "scope": "src/b/x.py", "reason": "ana: the tax table"}
        self.assertEqual(self.errors([ok]), [])
        self.assertEqual(self.errors([dict(ok, scope="src/b/")]), [])
        self.assertIn("is wider than any package", self.errors([dict(ok, scope="lib/z.py")])[0])

    def test_without_installed_rules_the_rule_is_not_checked(self):
        ok = {"rule": "anything at all", "scope": "src/a.py", "reason": "ana: the tax table"}
        self.assertEqual(self.errors([ok], rules=None), [])
        self.assertIn("is not in the installed rules", self.errors([dict(ok, rule="no")])[0])


class ProvenanceTests(unittest.TestCase):
    """provenance_problems on texts built here: section 4b's Source column (design 0020)."""

    def section(self, *rows, header="| What | Where | Source |"):
        return "# A\n\n## 4b. Context\n" + "\n".join([header, "|---|---|---|", *rows]) + "\n\n## 5. Data\n"

    def test_a_pipe_inside_backticks_is_not_a_cell(self):
        self.assertEqual(manifest_check.table_cells("| a | `x | y` | read |"), ["a", "`x | y`", "read"])

    def test_no_section_or_no_table_has_nothing_to_say(self):
        self.assertEqual(manifest_check.provenance_problems("# A\n## 5. Data\n"), ([], []))
        self.assertEqual(manifest_check.provenance_problems("## 4b. Context\nprose\n## 5. Data\n"), ([], []))

    def test_inferred_rows_are_not_checked_and_source_is_case_blind(self):
        text = self.section("| a | nowhere | Inferred |", "| b | `nope.py:1-2` | `inferred` |")
        self.assertEqual(manifest_check.provenance_problems(text), ([], []))

    def test_a_read_row_needs_an_existing_reference(self):
        text = self.section("| a | `agentic.py:1-2` | read |")
        errors, _ = manifest_check.provenance_problems(text)
        self.assertEqual(errors, ["section 4b, row 1: agentic.py:1-2 does not exist or is out of range"],
                         "references are relative to the project root")
        ok = self.section("| a | `.claude/bin/agentic.py:1-2` | read |")
        self.assertEqual(manifest_check.provenance_problems(ok), ([], []))

    def test_a_fenced_heading_does_not_start_section_4b(self):
        text = "# A\n```\n## 4b. not a section\n| a | b | Source |\n|---|---|---|\n| x | y | z |\n```\n"
        self.assertEqual(manifest_check.provenance_problems(text), ([], []))


class ValidateCommandTests(unittest.TestCase):
    """Waivers and the provenance of section 4b, through `validate`, `pipeline-plan` and `briefs` in a project."""

    def project(self, waivers=None, arch=None):
        manifest = json.loads(json.dumps(MANIFEST))
        if waivers is not None:
            manifest["waivers"] = waivers
        repo = Repo(manifest)
        self.addCleanup(repo.cleanup)
        shutil.copytree(ROOT / ".claude/rules-library", repo.dir / ".claude/rules-library")
        if arch is not None:
            text = (repo.dir / "architecture.md").read_text(encoding="utf-8")
            repo.write("architecture.md", text.replace("# A\n", "# A\n\n" + arch + "\n", 1))
        return repo

    def test_waivers_are_checked_by_validate(self):
        for waivers in (None, [WAIVER]):
            r = self.project(waivers).run("validate")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        bad = [([dict(WAIVER, scope="src/")], 'waivers[0]: scope "src/" is wider than any package\'s files'),
               ([dict(WAIVER, scope="**")], 'waivers[0]: scope "**" must be one file or one folder ending in "/"'),
               ([dict(WAIVER, reason="because")], 'waivers[0]: reason must be "<who decided>: <evidence>"'),
               ([dict(WAIVER, rule="no such rule at all")],
                'waivers[0]: rule "no such rule at all" is not in the installed rules'),
               ([WAIVER, WAIVER], "waivers[1]: repeats waivers[0]")]
        for waivers, message in bad:
            with self.subTest(message=message):
                r = self.project(waivers).run("validate")
                self.assertEqual(r.returncode, 1, r.stdout)
                self.assertIn(message, r.stderr)

    def table(self, *rows, source=True):
        head = "| What | Where | Rule |" + (" Source |" if source else "")
        sep = "|---|---|---|" + ("---|" if source else "")
        return "## 4b. Existing code context\n" + "\n".join([head, sep, *rows]) + "\n\n## 5. Data model\n"

    def test_the_source_column_is_checked_by_validate_and_pipeline_plan(self):
        cases = [(self.table("| a | `src/a.py:1-3` | r |", source=False), 0,
                  "warning: section 4b has no Source column: mark each row read or inferred"),
                 (self.table("| a | `src/a.py:1-3` | r | read |", "| b | nowhere | r | inferred |"), 0, None),
                 (self.table("| a | `src/a.py:1-9` | r | read |"), 1,
                  "section 4b, row 1: src/a.py:1-9 does not exist or is out of range"),
                 (self.table("| a | nowhere | r | read |"), 1, "section 4b, row 1: read needs a file:line"),
                 (self.table("| a | `src/a.py:1-3` | r | seen |"), 1,
                  "section 4b, row 1: Source must be read or inferred")]
        for arch, code, message in cases:
            with self.subTest(message=message):
                repo = self.project(arch=arch)
                repo.write("src/a.py", "a = 1\nb = 2\nc = 3\n")
                r = repo.run("validate")
                self.assertEqual(r.returncode, code, r.stdout + r.stderr)
                if message:
                    self.assertIn(message, r.stdout + r.stderr)
                elif code == 0:
                    self.assertNotIn("section 4b", r.stdout)
                if "1-9" in arch:
                    plan = repo.run("pipeline-plan")
                    self.assertEqual(plan.returncode, 1)
                    self.assertIn(message, plan.stderr)
                    self.assertEqual(repo.run("briefs").returncode, 0, "only validate and pipeline-plan check it")
        for arch in ("", "## 4b. Existing code context\nNo table here.\n\n## 5. Data model\n"):
            self.assertEqual(self.project(arch=arch).run("validate").returncode, 0)


if __name__ == "__main__":
    unittest.main()
