"""Tests of brief.py beyond the acceptance criteria: edge cases of the slicing and a real plan."""
import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import brief  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = {"acceptance": {"files": ["a/"], "command": "x"},
            "work_packages": [{"id": "WP-01", "wave": 1, "files": ["a.py"], "acs": ["AC-001", "AC-003"]}]}
TEXT = """# T

## 1. Goal
GOAL
## 4. Tree
TREE
## 4b. Context
CONTEXT
## 8. Work packages and acceptance criteria
### Acceptance criteria
- AC-001 · one
  - body-one
- AC-002 · two
  - body-two
- AC-003 · three
  - body-three
### Work packages
#### WP-01 · mine
body-mine
"""


class SlicerEdgeTests(unittest.TestCase):
    def test_a_plan_without_section_8_is_not_sliced(self):
        # Without its criteria and its package, a brief would leave the agent with nothing to build.
        for text in (TEXT.split("## 8. ")[0], TEXT.replace("## 8. ", "## 8 ")):
            out, sliced = brief.slice_architecture(text, "WP-01", MANIFEST)
            self.assertEqual((out, sliced), (text, False))

    def test_tilde_fences_hide_headings_too(self):
        text = TEXT.replace("CONTEXT\n", "CONTEXT\n~~~\n## 10. inside a fence\n~~~\n", 1)
        out, _ = brief.slice_architecture(text, "WP-01", MANIFEST)
        self.assertIn("inside a fence", out, "a fenced line is not a section")

    def test_sections_that_are_absent_are_skipped(self):
        out, sliced = brief.slice_architecture(TEXT, "WP-01", MANIFEST)
        self.assertTrue(sliced)
        self.assertIn("GOAL", out)
        self.assertNotIn("## 2.", out)

    def test_4b_is_not_taken_for_4(self):
        out, _ = brief.slice_architecture(TEXT, "WP-01", MANIFEST)
        self.assertIn("CONTEXT", out)
        self.assertNotIn("TREE", out)

    def test_ac_blocks_keep_document_order_whatever_the_order_in_acs(self):
        m = json.loads(json.dumps(MANIFEST))
        m["work_packages"][0]["acs"] = ["AC-003", "AC-001"]
        out, _ = brief.slice_architecture(TEXT, "WP-01", m)
        self.assertLess(out.index("body-one"), out.index("body-three"))
        self.assertNotIn("body-two", out)

    def test_ac_ids_are_compared_exactly(self):
        m = json.loads(json.dumps(MANIFEST))
        m["work_packages"][0]["acs"] = ["AC-01"]
        self.assertEqual(brief.slice_architecture(TEXT, "WP-01", m), (TEXT, False))

    def test_a_plan_without_the_acceptance_subsection_falls_back(self):
        text = TEXT.replace("### Acceptance criteria", "### Criteria")
        self.assertEqual(brief.slice_architecture(text, "AT", MANIFEST), (text, False))

    def test_a_missing_work_packages_subsection_falls_back_for_a_wp_but_not_for_the_test_author(self):
        text = TEXT.replace("### Work packages", "### Packages")
        self.assertEqual(brief.slice_architecture(text, "WP-01", MANIFEST), (text, False))
        self.assertTrue(brief.slice_architecture(text, "AT", MANIFEST)[1])

    def test_the_manifest_block_never_reaches_a_brief(self):
        text = TEXT + "\n```json wp-manifest\n" + json.dumps(MANIFEST) + "\n```\n\n## 9. Tests\nTESTS\n"
        for ident in ("WP-01", "AT"):
            out, _ = brief.slice_architecture(text, ident, MANIFEST)
            self.assertNotIn("wp-manifest", out)
            self.assertIn("TESTS", out)


class RealPlanTests(unittest.TestCase):
    def test_a_real_plan_is_sliced_and_shrinks(self):
        text = (Path(__file__).resolve().parent / "fixtures" / "lean-agents-plan.md").read_text(encoding="utf-8")
        manifest = json.loads(re.search(r"```json wp-manifest\n(.*?)\n```", text, re.S).group(1))
        full = len(text.split())
        for wp in manifest["work_packages"]:
            out, sliced = brief.slice_architecture(text, wp["id"], manifest)
            self.assertTrue(sliced, wp["id"])
            self.assertLess(len(out.split()), full, wp["id"])
            for other in manifest["work_packages"]:
                if other["id"] != wp["id"]:
                    self.assertIsNone(re.search(rf"^#### {other['id']}\b", out, re.M), "another WP's block leaked")



# ---------- moved from the retired test_acceptance.py (design 0017): the brief contracts and the command ----------

def plan_manifest():
    return {"allow_bash": ["Bash(python3 -m unittest:*)"],
            "acceptance": {"files": ["acceptance/"], "command": "python3 acc.py"},
            "work_packages": [{"id": "WP-01", "wave": 1, "depends_on": [], "files": ["src/a.py"], "acs": ["AC-001"]},
                              {"id": "WP-02", "wave": 1, "depends_on": [], "files": ["src/b/"], "acs": ["AC-002"]}]}


def plan(m, contract_extra=""):
    """An architecture.md with the template's sections, a block per AC and per WP, and the manifest."""
    acs = [a for wp in m["work_packages"] for a in wp["acs"]]
    ac_blocks = "".join(f"- {a} · title {a}\n  - Scenario: body of {a}\n" for a in acs)
    wp_blocks = "".join(f"#### {wp['id']} · goal {wp['id']}\nbody of {wp['id']}\n\n" for wp in m["work_packages"])
    return ("# Architecture: demo\nType: feature · Size: standard\n\n"
            "## 1. Goal\nTEXT-GOAL\n\n## 2. Scope\nTEXT-SCOPE\n\n## 3. Stack\nTEXT-STACK\n\n"
            "## 4. Repository structure\nTEXT-TREE\n\n## 4b. Existing code context\nTEXT-CONTEXT\n\n"
            "## 5. Data model\nTEXT-MODEL\n\n"
            f"## 6. Contracts between modules\nTEXT-CONTRACTS\n{contract_extra}\n"
            "## 7. Conventions\nTEXT-CONVENTIONS\n\n"
            f"## 8. Work packages and acceptance criteria\n### Acceptance criteria\n{ac_blocks}\n"
            f"### Work packages\n{wp_blocks}```json wp-manifest\n{json.dumps(m, indent=2)}\n```\n\n"
            "## 9. Test strategy\nTEXT-TESTS\n\n## 10. Decisions\nTEXT-DECISIONS\n\n"
            "## 11. Risks and open questions\nTEXT-RISKS\n")


COMMON = ("TEXT-GOAL", "TEXT-SCOPE", "TEXT-STACK", "TEXT-CONTEXT", "TEXT-MODEL", "TEXT-CONTRACTS", "TEXT-CONVENTIONS",
          "TEXT-TESTS")


class BriefContractTests(unittest.TestCase):
    """From test_acceptance ac_001 to ac_004: what each brief holds and leaves out."""

    def setUp(self):
        self.m = plan_manifest()
        self.text = plan(self.m)

    def test_a_package_brief_has_its_slice_and_nothing_of_the_others(self):
        text, sliced = brief.slice_architecture(self.text, "WP-01", self.m)
        self.assertTrue(sliced)
        self.assertTrue(text.startswith("> Brief for WP-01:"))
        for token in COMMON + ("TEXT-RISKS", "- AC-001 · title", "#### WP-01 · goal"):
            self.assertIn(token, text)
        self.assertIn("```json\n" + json.dumps(self.m["work_packages"][0], indent=2) + "\n```", text)
        for token in ("AC-002", "WP-02", "TEXT-TREE", "TEXT-DECISIONS", "wp-manifest"):
            self.assertNotIn(token, text)

    def test_the_test_authors_brief_has_every_criterion_and_no_package(self):
        text, sliced = brief.slice_architecture(self.text, "AT", self.m)
        self.assertTrue(sliced)
        for token in COMMON + ("- AC-001 · title", "- AC-002 · title"):
            self.assertIn(token, text)
        self.assertIn("```json\n" + json.dumps({"acceptance": self.m["acceptance"]}, indent=2) + "\n```", text)
        for token in ("#### WP-", "TEXT-TREE", "TEXT-DECISIONS", "TEXT-RISKS", "wp-manifest"):
            self.assertNotIn(token, text)

    def test_a_missing_heading_or_an_unknown_package_gets_the_whole_document(self):
        no_heading = self.text.replace("#### WP-01 · goal WP-01", "WP-01 · goal WP-01")
        self.assertEqual(brief.slice_architecture(no_heading, "WP-01", self.m), (no_heading, False))
        self.assertEqual(brief.slice_architecture(self.text, "WP-77", self.m), (self.text, False))

    def test_headings_inside_a_backtick_fence_start_nothing(self):
        fenced = "```text\n## 10. Decisions\n#### WP-02 · fake\nFENCED-LINE\n```\n"
        text, sliced = brief.slice_architecture(plan(self.m, contract_extra=fenced), "WP-01", self.m)
        self.assertTrue(sliced)
        self.assertIn(fenced, text)
        self.assertNotIn("TEXT-DECISIONS", text)
        self.assertNotIn("#### WP-02", text.replace(fenced, ""))


class BriefsCommandTests(unittest.TestCase):
    """From test_acceptance ac_005: `agentic.py briefs` writes what the slicer returns, and nothing else."""

    def setUp(self):
        from test_agentic import Repo
        self.repo = Repo(plan_manifest())
        self.addCleanup(self.repo.cleanup)
        self.repo.write("architecture.md", plan(plan_manifest()))

    def files(self):
        return {p.relative_to(self.repo.dir).as_posix() for p in self.repo.dir.rglob("*")
                if p.is_file() and ".git" not in p.parts}

    def test_briefs_are_written_and_reported(self):
        before = self.files()
        r = self.repo.run("briefs")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        info = json.loads(r.stdout.strip().splitlines()[-1])
        text = (self.repo.dir / "architecture.md").read_text(encoding="utf-8")
        for ident in ("WP-01", "WP-02", "AT"):
            expected, sliced = brief.slice_architecture(text, ident, plan_manifest())
            written = (self.repo.dir / ".claude/state/briefs" / f"{ident}.md").read_text(encoding="utf-8")
            self.assertEqual((written, info["briefs"][ident]["sliced"]), (expected, sliced))
        self.assertLess(info["briefs"]["WP-01"]["words"], info["full_words"])
        self.assertTrue(all(f.startswith(".claude/state/briefs/") for f in self.files() - before))

    def test_briefs_run_inside_seq_and_fail_without_a_manifest(self):
        steps = json.loads(self.repo.run("seq", "pipeline-plan", "+", "briefs").stdout)["steps"]
        self.assertEqual([s["exit_code"] for s in steps], [0, 0])
        self.repo.write("architecture.md", "# no manifest\n")
        self.assertEqual(self.repo.run("briefs").returncode, 1)

if __name__ == "__main__":
    unittest.main()
