"""The layout of .claude/bin: at most one per-release acceptance file, the current release's (design 0017).

A release that adds its own test_acceptance_*.py moves the previous release's unique checks into the unit suites and
deletes that file, so acceptance files never pile up and pin yesterday's behaviour.
Run: python3 -m unittest discover -s .claude/bin -p "test_layout.py"
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parent
sys.path.insert(0, str(BIN))
from test_definitions import FLOW_REPO_ONLY  # noqa: E402

MAX_ACCEPTANCE_FILES = 1


def acceptance_files(folder):
    """The test_acceptance*.py files directly in folder, sorted by name."""
    return sorted(p.name for p in Path(folder).glob("test_acceptance*.py"))


class AcceptanceFilesTests(unittest.TestCase):
    def folder(self, *names):
        d = Path(tempfile.mkdtemp(prefix="layout-"))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        for name in names:
            (d / name).write_text("", encoding="utf-8")
        return d

    def test_only_acceptance_files_are_listed(self):
        d = self.folder("test_acceptance_v2.py", "test_agentic.py", "acceptance_notes.md")
        self.assertEqual(acceptance_files(d), ["test_acceptance_v2.py"])

    def test_two_acceptance_files_break_the_rule(self):
        d = self.folder("test_acceptance_v29.py", "test_acceptance_v210.py")
        self.assertGreater(len(acceptance_files(d)), MAX_ACCEPTANCE_FILES)

    @FLOW_REPO_ONLY  # a project may keep an edited copy that install left alone
    def test_this_repository_keeps_at_most_one(self):
        found = acceptance_files(BIN)
        self.assertLessEqual(len(found), MAX_ACCEPTANCE_FILES, f"retire all but the current release's: {found}")


if __name__ == "__main__":
    unittest.main()
