"""Clean UTF-8 in this repository's tracked text (design 0018, issue 58): valid UTF-8, no byte-order mark and no
double encoding (UTF-8 bytes decoded once more as Latin-1 or Windows-1252 and saved again).

Run: python3 -m unittest discover -s .claude/bin -p "test_encoding.py"
Special characters are written as escapes, so this file stays clean itself.
"""
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parent
sys.path.insert(0, str(BIN))
from test_definitions import FLOW_REPO_ONLY, ROOT  # noqa: E402

BINARY_PROBE_BYTES = 8000
BOM = "\ufeff"
DOUBLE_ENCODED = re.compile("\u00c3[\u0080-\u00bf]|\u00e2\u20ac|\u00c2[\u00a0-\u00bf]")


def text_problems(root, paths):
    """'<path>: <problem>' for the text files among paths (repository-relative) that are not clean UTF-8, sorted.
    A path that is not a regular file, or whose first bytes hold a NUL (binary), is skipped."""
    problems = []
    for rel in sorted(paths):
        path = Path(root) / rel
        if not path.is_file():
            continue
        data = path.read_bytes()
        if b"\0" in data[:BINARY_PROBE_BYTES]:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            problems.append(f"{rel}: not valid UTF-8")
            continue
        if text.startswith(BOM):
            problems.append(f"{rel}: starts with a byte-order mark")
        elif DOUBLE_ENCODED.search(text):
            problems.append(f"{rel}: double-encoded text")
    return problems


class TextProblemsTests(unittest.TestCase):
    def folder(self, files):
        d = Path(tempfile.mkdtemp(prefix="utf8-"))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        for name, data in files.items():
            (d / name).write_bytes(data)
        return d

    def test_each_problem_is_named_and_clean_or_binary_files_are_not(self):
        d = self.folder({"ok.md": "\u00f1 \u20ac\n".encode("utf-8"), "latin.txt": b"caf\xe9\n",
                         "double.md": "caf\u00c3\u00a9\n".encode("utf-8"), "bom.md": "\ufeffx\n".encode("utf-8"),
                         "bin.dat": b"\x00\xff"})
        self.assertEqual(text_problems(d, ["ok.md", "latin.txt", "double.md", "bom.md", "bin.dat"]),
                         ["bom.md: starts with a byte-order mark", "double.md: double-encoded text",
                          "latin.txt: not valid UTF-8"])

    def test_missing_paths_and_folders_are_skipped(self):
        d = self.folder({"ok.md": b"x\n"})
        (d / "sub").mkdir()
        self.assertEqual(text_problems(d, ["ok.md", "gone.md", "sub"]), [])


@FLOW_REPO_ONLY  # a project may keep files in other encodings on purpose
class RepositoryTests(unittest.TestCase):
    def test_tracked_text_is_clean_utf8(self):
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(text_problems(ROOT, [line for line in out.stdout.splitlines() if line]), [])


if __name__ == "__main__":
    unittest.main()
