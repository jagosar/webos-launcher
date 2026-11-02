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


if __name__ == "__main__":
    unittest.main()
