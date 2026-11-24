"""Tests of findings.split_by_owner and the QA environment helpers (design 0031, WP-02, issues 141 and 142). The
workflow's mirrors are held equal to these functions by the conformance scenarios (test_pipeline.ConformanceTests).

Run: python3 -m unittest discover -s .claude/bin -p test_owner_split.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import findings  # noqa: E402
from findings import qa_environment_blocked, qa_not_run_items, split_by_owner, with_fingerprints  # noqa: E402

OWNERS = {"WP-01": ["src/a.py"], "WP-02": ["src/b/"], "WP-03": ["src/c.py"], "WP-10": ["lib/z.py"]}
BASE = {"axis": "spec", "tier": "floor", "rule": "", "wp": "WP-01", "file": "src/a.py", "line": 3, "problem": "P"}


def one(evidence="", fix="", **fields):
    return with_fingerprints([dict(BASE, evidence=evidence, fix=fix, **fields)])


class SplitTests(unittest.TestCase):
    def test_a_directory_owner_matches_files_under_it_and_not_a_longer_name(self):
        out = split_by_owner(one("src/b/x.py:1"), OWNERS, "")
        self.assertEqual([(f["wp"], f["scope"]) for f in out], [("WP-01", ["src/a.py"]), ("WP-02", ["src/b/"])])
        for text in ("src/bb/x.py", "lib/src/b/x.py", "src/c.pyx", "src/c.py-old", "src/a.py"):
            with self.subTest(text=text):
                self.assertEqual(len(split_by_owner(one(text), OWNERS, "")), 1)

    def test_the_fix_counts_and_parts_go_in_id_order(self):
        out = split_by_owner(one("lib/z.py:4", fix="also src/c.py"), OWNERS, "")
        self.assertEqual([f["wp"] for f in out], ["WP-01", "WP-03", "WP-10"])
        self.assertEqual({f["split_from"] for f in out[1:]}, {out[0]["fingerprint"]})
        self.assertNotIn("split_from", out[0])
        self.assertEqual(out[0]["line"], 3, "the original keeps its line")

    def test_root_and_backslashes_with_a_drive_letter_of_another_case(self):
        item = one("c:\\Repo\\src\\C.py:7", file="C:\\REPO\\src\\a.py")
        out = split_by_owner(item, {"WP-01": ["src/a.py"], "WP-03": ["src/C.py"]}, "C:/repo")
        self.assertEqual(out[0]["scope"], ["src/a.py"])
        self.assertEqual((out[1]["file"], out[1]["scope"]), ("src/C.py", ["src/C.py"]), "the owner's spelling kept")

    def test_the_original_scope_holds_its_file_and_its_own_named_files(self):
        out = split_by_owner(one("src/a.py and src/c.py", file="/abs/x.py"), OWNERS, "")
        self.assertEqual(out[0]["scope"], ["/abs/x.py", "src/a.py"])

    def test_a_package_outside_the_owners_and_a_finding_without_file(self):
        out = split_by_owner(one("src/c.py", wp="WP-99", file=None), OWNERS, "")
        self.assertEqual([(f["wp"], f["scope"]) for f in out], [("WP-99", []), ("WP-03", ["src/c.py"])])

    def test_collisions_with_the_input_and_with_earlier_parts(self):
        part_fp = findings.fingerprint(dict(BASE, wp="WP-03", file="src/c.py"))
        other = {"axis": "spec", "tier": "floor", "rule": "", "file": "src/c.py", "problem": "P", "wp": "WP-03",
                 "fingerprint": part_fp}
        items = [other] + one("src/c.py")
        out = split_by_owner(items, OWNERS, "")
        self.assertEqual(out[-1]["fingerprint"], f"{part_fp}-2")

    def test_non_string_fields_and_bad_shapes_never_raise(self):
        weird = dict(BASE, evidence=3, fix=None, file=["x"], wp=["WP-01"], fingerprint="F1")
        self.assertEqual(split_by_owner([weird], OWNERS, ""), [weird])
        self.assertEqual(split_by_owner([weird], {"WP-02": "src/b/"}, 5), [weird])
        self.assertEqual(split_by_owner(["x", None], OWNERS, ""), ["x", None])
        self.assertEqual(split_by_owner([], None, None), [])

    def test_an_unsplit_finding_is_the_same_object(self):
        items = one("nothing here")
        self.assertIs(split_by_owner(items, OWNERS, "")[0], items[0])


class QaHelperTests(unittest.TestCase):
    def test_only_an_environment_blocker_counts(self):
        self.assertTrue(qa_environment_blocked({"status": "BLOCKER", "blocker_kind": "environment"}))
        for qa in ({"status": "BLOCKER"}, {"status": "BLOCKER", "blocker_kind": "design"}, None, [],
                   {"status": "FAIL", "blocker_kind": "environment"}):
            with self.subTest(qa=qa):
                self.assertFalse(qa_environment_blocked(qa))
                self.assertEqual(qa_not_run_items(qa), [])

    def test_one_item_per_command_once_each_in_order(self):
        qa = {"status": "BLOCKER", "blocker_kind": "environment", "not_run": ["b", 3, "a", "b"]}
        items = qa_not_run_items(qa)
        self.assertEqual([(i["claim"], i["how_to_check"]) for i in items], [("b", "run b"), ("a", "run a")])
        self.assertEqual(items[0], {"type": "needs validation", "source": "qa", "wp": None, "file": None,
                                    "kind": "qa not run", "problem": "b", "claim": "b",
                                    "fact": "not run: the environment refused it", "how_to_check": "run b"})

    def test_no_command_named_gives_the_default_item(self):
        for not_run in (None, "pytest", [], [1, None]):
            with self.subTest(not_run=not_run):
                qa = {"status": "BLOCKER", "blocker_kind": "environment", "not_run": not_run}
                self.assertEqual([(i["claim"], i["how_to_check"]) for i in qa_not_run_items(qa)],
                                 [("QA's validation", "allow QA's test commands, then run /implement again")])


if __name__ == "__main__":
    unittest.main()
