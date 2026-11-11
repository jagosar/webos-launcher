"""Tests of findings.py: tiers, the fall-back to severity, waivers, the text checksum and the fingerprints.

Run: python3 -m unittest discover -s .claude/bin -p "test_findings.py"
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import findings  # noqa: E402

BLOCKING = ["critical", "high"]
WAIVER = {"rule": "magic numbers", "scope": "src/", "reason": "ana: tax table"}


def reflex(rule="No magic numbers", file="src/a.py"):
    return {"tier": "reflex", "rule": rule, "file": file}


class TierTests(unittest.TestCase):
    def test_an_explicit_tier_wins_over_the_severity(self):
        self.assertEqual(findings.tier_of({"tier": "finish", "severity": "critical"}, BLOCKING), "finish")
        self.assertEqual(findings.tier_of({"tier": "floor", "severity": "low"}, BLOCKING), "floor")

    def test_without_a_tier_the_severity_decides(self):
        self.assertEqual(findings.tier_of({"severity": "high"}, BLOCKING), "floor")
        self.assertEqual(findings.tier_of({"severity": "medium"}, BLOCKING), "finish")
        self.assertEqual(findings.tier_of({}, BLOCKING), "finish")

    def test_an_unknown_tier_is_ignored(self):
        self.assertEqual(findings.tier_of({"tier": "urgent", "severity": "critical"}, BLOCKING), "floor")

    def test_something_that_is_not_a_finding_does_not_block(self):
        self.assertFalse(findings.blocks("text", BLOCKING))


class WaiverTests(unittest.TestCase):
    def test_scope_is_a_file_or_a_folder(self):
        self.assertTrue(findings.in_scope("src/a.py", "src/"))
        self.assertTrue(findings.in_scope("src/a.py", "src/a.py"))
        self.assertFalse(findings.in_scope("src/a.py", "src"))
        self.assertFalse(findings.in_scope("lib/a.py", "src/"))
        self.assertFalse(findings.in_scope(None, "src/"))
        self.assertFalse(findings.in_scope("src/a.py", ""))

    def test_rule_names_agree_without_case_by_containment(self):
        self.assertIs(findings.waiver_for(reflex(), [WAIVER]), WAIVER)
        self.assertIs(findings.waiver_for(reflex("MAGIC NUMBERS"), [WAIVER]), WAIVER)
        self.assertIsNone(findings.waiver_for(reflex("Short functions"), [WAIVER]))

    def test_only_reflex_findings_are_waived(self):
        for tier in ("floor", "finish"):
            self.assertIsNone(findings.waiver_for(dict(reflex(), tier=tier), [WAIVER]))

    def test_an_empty_rule_never_matches(self):
        self.assertIsNone(findings.waiver_for(reflex(""), [WAIVER]))
        self.assertIsNone(findings.waiver_for(reflex(), [dict(WAIVER, rule="  ")]))

    def test_a_file_outside_the_scope_is_not_waived(self):
        self.assertIsNone(findings.waiver_for(reflex(file="lib/a.py"), [WAIVER]))
        self.assertIsNone(findings.waiver_for({"tier": "reflex", "rule": "No magic numbers"}, [WAIVER]))

    def test_the_first_covering_waiver_is_returned(self):
        other = dict(WAIVER, reason="bo: later")
        self.assertIs(findings.waiver_for(reflex(), ["junk", WAIVER, other]), WAIVER)


class BlocksTests(unittest.TestCase):
    def test_each_tier(self):
        self.assertTrue(findings.blocks({"tier": "floor"}, BLOCKING, [WAIVER]))
        self.assertFalse(findings.blocks({"tier": "finish", "severity": "critical"}, BLOCKING))
        self.assertTrue(findings.blocks(reflex(), BLOCKING))
        self.assertFalse(findings.blocks(reflex(), BLOCKING, [WAIVER]))

    def test_old_findings_block_by_severity(self):
        self.assertTrue(findings.blocks({"severity": "critical"}, BLOCKING))
        self.assertFalse(findings.blocks({"severity": "low"}, BLOCKING))


class TextSumTests(unittest.TestCase):
    def test_sum_of_code_points(self):
        self.assertEqual(findings.text_sum(""), 0)
        self.assertEqual(findings.text_sum("ab"), 97 + 98)
        self.assertEqual(findings.text_sum("\u00f1"), 241)
        self.assertEqual(findings.text_sum("\U0001f600"), 0x1F600)

    def test_one_changed_character_changes_the_sum(self):
        self.assertNotEqual(findings.text_sum("No magic numbers"), findings.text_sum("No magic number."))


class FingerprintTests(unittest.TestCase):
    FINDING = {"axis": "spec", "tier": "floor", "file": "src/a.py", "problem": "  Off by ONE in total  "}

    def test_a_pinned_value_that_the_workflow_must_equal(self):
        self.assertEqual(findings.fingerprint(self.FINDING), "Fb361d20c")

    def test_the_line_the_evidence_and_the_case_of_the_problem_do_not_count(self):
        same = dict(self.FINDING, problem="off by one in total", line=9, evidence="other words")
        self.assertEqual(findings.fingerprint(same), findings.fingerprint(self.FINDING))

    def test_each_named_part_counts(self):
        base = findings.fingerprint(self.FINDING)
        for key, value in (("axis", "standards"), ("tier", "reflex"), ("rule", "No magic numbers"),
                           ("file", "src/b.py"), ("problem", "off by two")):
            with self.subTest(key=key):
                self.assertNotEqual(findings.fingerprint(dict(self.FINDING, **{key: value})), base)

    def test_only_the_first_80_code_points_of_the_problem_count(self):
        long = "\u00e9" * 80
        self.assertEqual(findings.fingerprint({"problem": long + "x"}), findings.fingerprint({"problem": long + "y"}))
        self.assertNotEqual(findings.fingerprint({"problem": long[:79] + "x"}),
                            findings.fingerprint({"problem": long[:79] + "y"}))

    def test_the_problem_is_lowered_not_casefolded_as_the_workflow_does(self):
        self.assertNotEqual(findings.fingerprint({"problem": "\u00df"}), findings.fingerprint({"problem": "ss"}))
        self.assertEqual(findings.fingerprint({"problem": "\u00c9"}), findings.fingerprint({"problem": "\u00e9"}))

    def test_missing_fields_are_empty_text(self):
        self.assertEqual(findings.fingerprint({}), findings.fingerprint({"axis": None, "rule": ""}))
        self.assertRegex(findings.fingerprint({}), r"^F[0-9a-f]{8}$")

    def test_repeated_values_are_numbered_and_the_input_is_not_changed(self):
        other = dict(self.FINDING, file="src/b.py")
        given = [self.FINDING, dict(self.FINDING), other, dict(self.FINDING)]
        out = findings.with_fingerprints(given)
        fp = findings.fingerprint(self.FINDING)
        self.assertEqual([f["fingerprint"] for f in out], [fp, fp + "-2", findings.fingerprint(other), fp + "-3"])
        self.assertFalse(any("fingerprint" in f for f in given))
        self.assertEqual(findings.with_fingerprints([]), [])


if __name__ == "__main__":
    unittest.main()
