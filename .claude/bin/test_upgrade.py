"""Tests of upgrade.py: classification, versions and notes, guards and the history built from git."""
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import upgrade  # noqa: E402

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


class Repo:
    def __init__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="upgrade-unit-"))
        subprocess.run(["git", "init", "-q"], cwd=self.dir, check=True)

    def commit(self, files):
        for rel, text in files.items():
            p = self.dir / rel
            if text is None:
                p.unlink()
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=self.dir, check=True)
        subprocess.run(GIT + ["commit", "-qm", "c"], cwd=self.dir, check=True)


class ClassifyTests(unittest.TestCase):
    def test_table(self):
        cases = [((None, "s", set(), False), "new"), (("s", "s", set(), False), "unchanged"),
                 (("old", "s", {"old"}, False), "updated"), (("mine", "s", {"old"}, False), "kept"),
                 (("mine", "s", set(), True), "updated")]
        for args, expected in cases:
            with self.subTest(args=args):
                self.assertEqual(upgrade.classify(*args), expected)


class LeftoverTests(unittest.TestCase):
    def test_a_partial_test_suite_is_completed_ci_and_plugin_files_are_not(self):
        shipped = {".claude/bin/test_a.py", ".claude/bin/conformance.json", ".github/workflows/ci.yml", "plugin/commands/init.md",
                   ".claude/bin/test_acceptance_x.py"}
        self.assertEqual(upgrade.leftovers(shipped, {".claude/bin/test_a.py"}),
                         {".claude/bin/test_a.py", ".claude/bin/conformance.json"})
        self.assertEqual(upgrade.leftovers(shipped, {".github/workflows/ci.yml"}), {".github/workflows/ci.yml"})
        self.assertEqual(upgrade.leftovers(shipped, set()), set())


class NotesTests(unittest.TestCase):
    NOTES = {"1.2.0": ["two"], "1.10.0": ["ten"], "1.1.0": ["one"]}

    def test_versions_written_other_ways(self):
        self.assertEqual(upgrade.version_key("1.1"), upgrade.version_key("1.1.0"))
        self.assertEqual(upgrade.version_key("v1.1.0"), (1, 1, 0))
        self.assertEqual(upgrade.version_key("1.1.4-rc1"), (1, 1, 4))
        self.assertIsNone(upgrade.version_key("dev"))
        with mock.patch.dict(upgrade.NOTES, self.NOTES, clear=True):
            self.assertEqual(upgrade.notes_between("v1.1", "1.2.0"), ["two"])

    def test_order_and_bounds(self):
        with mock.patch.dict(upgrade.NOTES, self.NOTES, clear=True):
            self.assertEqual(upgrade.notes_between("1.1.0", "1.10.0"), ["two", "ten"], "1.10 sorts after 1.2")
            self.assertEqual(upgrade.notes_between("1.10.0", "1.10.0"), [])
            self.assertEqual(upgrade.notes_between(None, "1.2.0"), ["one", "two"])
            self.assertEqual(upgrade.notes_between("unknown", "dev"), ["one", "two", "ten"])

    def test_real_notes_name_the_removed_commands(self):
        self.assertIn("wave-plan", " ".join(upgrade.notes_between("1.0.0", "1.1.0")))

    def test_the_2_0_notes_name_the_new_report_fields(self):
        notes = " ".join(upgrade.notes_between("1.1.4", "2.0.0"))
        for word in ("needs_validation", "not_checked", "four parts", "customised"):
            self.assertIn(word, notes)
        self.assertEqual(upgrade.notes_between("1.1.4", "1.1.4"), [])

    def test_the_2_1_notes_name_the_gate_and_the_statuses(self):
        notes = " ".join(upgrade.notes_between("2.0.0", "2.1.0"))
        for word in ("gate", "DONE_WITH_CONCERNS", "NEEDS_CONTEXT", "BLOCKED", "settings.json", "StructuredOutput", "customised"):
            self.assertIn(word, notes)
        self.assertEqual(upgrade.notes_between("2.0.0", "2.0.0"), [])
        self.assertEqual(len(upgrade.NOTES["2.0.0"]), 7, "the 2.0.0 notes are frozen")

    def test_the_2_2_notes_name_the_callers(self):
        notes = " ".join(upgrade.notes_between("2.1.0", "2.2.0"))
        for word in ("callers", "reviewer", "install", "whole word"):
            self.assertIn(word, notes)
        self.assertEqual(len(upgrade.NOTES["2.1.0"]), 4, "the 2.1.0 notes are frozen")

    def test_the_2_3_notes_name_the_fix_sources_and_the_failures(self):
        notes = " ".join(upgrade.notes_between("2.2.0", "2.3.0"))
        for word in ("--fix", "--all", "workflow-result.json", "refuted", "verification_failures"):
            self.assertIn(word, notes)
        self.assertEqual(len(upgrade.NOTES["2.2.0"]), 2, "the 2.2.0 notes are frozen")


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.repo = Repo()
        self.addCleanup(shutil.rmtree, self.repo.dir, ignore_errors=True)
        self.repo.commit({"a.md": "x\n"})

    def test_not_a_git_repo(self):
        plain = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, plain, ignore_errors=True)
        self.assertEqual(upgrade.guard_problems(plain, ["a.md"]), ["not a git repository"])

    def test_tracked_changes_block_untracked_do_not(self):
        (self.repo.dir / "a.md").write_text("y\n", encoding="utf-8")
        (self.repo.dir / "new.md").write_text("z\n", encoding="utf-8")
        self.assertEqual(upgrade.guard_problems(self.repo.dir, ["a.md", "new.md"]), ["uncommitted: a.md"])
        self.assertEqual(upgrade.guard_problems(self.repo.dir, ["new.md"]), [])

    def test_a_running_agent_blocks_and_stage_keys_are_ignored(self):
        state = self.repo.dir / ".claude/state"
        state.mkdir(parents=True)
        (state / "ledger.json").write_text(json.dumps({"_stages": {"state": "running"}, "QA": {"state": "running"},
                                                       "WP-01": {"state": "done"}}), encoding="utf-8")
        problems = upgrade.guard_problems(self.repo.dir, [])
        self.assertEqual(len(problems), 1)
        self.assertIn("a run is in progress (QA)", problems[0])

    def test_an_unreadable_ledger_blocks(self):
        (self.repo.dir / ".claude/state/ledger.json").mkdir(parents=True)  # reading it raises OSError on every system
        problems = upgrade.guard_problems(self.repo.dir, [])
        self.assertEqual(len(problems), 1)
        self.assertTrue(problems[0].startswith("the run state cannot be read"), problems)

    def test_a_corrupt_ledger_blocks(self):
        # It cannot tell whether a run is in progress: like an unreadable one, it stops the upgrade.
        state = self.repo.dir / ".claude/state"
        state.mkdir(parents=True)
        (state / "ledger.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(len(upgrade.guard_problems(self.repo.dir, [])), 1)


class HistoryTests(unittest.TestCase):
    def test_every_version_of_every_matching_path(self):
        repo = Repo()
        self.addCleanup(shutil.rmtree, repo.dir, ignore_errors=True)
        repo.commit({".claude/agents/qa.md": "A\n", "README.md": "r\n"})
        repo.commit({".claude/agents/qa.md": "B\n", ".claude/workflows/old.js": "X\n"})
        repo.commit({".claude/workflows/old.js": None})
        data = upgrade.build_history(repo.dir, [".claude/agents/*.md", ".claude/workflows/*.js"])
        self.assertEqual(data["files"], {".claude/agents/qa.md": sorted([sha("A\n"), sha("B\n")]),
                                         ".claude/workflows/old.js": [sha("X\n")]})
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo.dir, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()
        self.assertEqual(data["generated_from"], head)

    def test_crlf_contents_hash_like_lf(self):
        repo = Repo()
        self.addCleanup(shutil.rmtree, repo.dir, ignore_errors=True)
        (repo.dir / ".claude/agents").mkdir(parents=True)
        (repo.dir / ".claude/agents/qa.md").write_bytes(b"A\r\n")
        subprocess.run(["git", "-c", "core.autocrlf=false", "add", "-A"], cwd=repo.dir, check=True)  # keep the CRLF blob
        subprocess.run(GIT + ["-c", "core.autocrlf=false", "commit", "-qm", "c"], cwd=repo.dir, check=True)
        data = upgrade.build_history(repo.dir, [".claude/agents/*.md"])
        self.assertEqual(data["files"], {".claude/agents/qa.md": [sha("A\n")]})

    def test_git_text_mode_is_utf8_and_binary_mode_stays_binary(self):
        with mock.patch.object(upgrade.subprocess, "run") as run:
            upgrade.git(".", "status")
            upgrade.git(".", "cat-file", "--batch", input=b"x\n", text=False)
        text_kw, bytes_kw = run.call_args_list[0][1], run.call_args_list[1][1]
        self.assertEqual((text_kw["text"], text_kw["encoding"]), (True, "utf-8"))
        self.assertFalse(bytes_kw["text"])
        self.assertNotIn("encoding", bytes_kw)

    def test_refuses_without_git(self):
        plain = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, plain, ignore_errors=True)
        with self.assertRaisesRegex(ValueError, "not a git repository"):
            upgrade.build_history(plain, ["*"])

    def test_load_history_tolerates_missing_or_bad_files(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.assertEqual(upgrade.load_history(tmp), {})
        (tmp / ".claude").mkdir()
        (tmp / ".claude/flow-history.json").write_text("not json", encoding="utf-8")
        self.assertEqual(upgrade.load_history(tmp), {})
        (tmp / ".claude/flow-history.json").write_text(json.dumps({"files": {"a": ["h"]}}), encoding="utf-8")
        self.assertEqual(upgrade.load_history(tmp), {"a": {"h"}})



class GapTests(unittest.TestCase):
    """release_tags, history_gaps and release_notes: what `flow-history --check` and `release-notes` stand on."""

    def repo(self):
        repo = Repo()
        self.addCleanup(shutil.rmtree, repo.dir, ignore_errors=True)
        return repo

    def tag(self, repo, name):
        subprocess.run(["git", "tag", name], cwd=repo.dir, check=True)

    def test_release_tags_are_sorted_by_version_and_skip_non_versions(self):
        repo = self.repo()
        repo.commit({"a.md": "A\n"})
        for name in ("agentic-flow--v2.10.0", "agentic-flow--v2.9.0", "agentic-flow--vnext", "other-v1.0.0"):
            self.tag(repo, name)
        self.assertEqual(upgrade.release_tags(repo.dir), ["agentic-flow--v2.9.0", "agentic-flow--v2.10.0"])

    def test_release_tags_without_git_is_empty(self):
        plain = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, plain, ignore_errors=True)
        self.assertEqual(upgrade.release_tags(plain), [])

    def test_gaps_list_unknown_contents_by_ref_then_path(self):
        repo = self.repo()
        repo.commit({"b.md": "B\n", "a.md": "A\n", "skip.txt": "S\n"})
        self.tag(repo, "one")
        repo.commit({"a.md": "A2\n", "b.md": "B2\n"})
        history = {"a.md": {sha("A\n")}, "b.md": {sha("B\n"), sha("B2\n")}}
        gaps = upgrade.history_gaps(repo.dir, ["*.md"], history, ["HEAD", "one"])
        self.assertEqual(gaps, [{"ref": "HEAD", "path": "a.md"}])
        self.assertEqual(upgrade.history_gaps(repo.dir, ["*.md"], {}, ["one"]),
                         [{"ref": "one", "path": "a.md"}, {"ref": "one", "path": "b.md"}])

    def test_gaps_read_crlf_as_lf(self):
        repo = self.repo()
        (repo.dir / "a.md").write_bytes(b"A\r\n")
        subprocess.run(["git", "-c", "core.autocrlf=false", "add", "-A"], cwd=repo.dir, check=True)
        subprocess.run(GIT + ["-c", "core.autocrlf=false", "commit", "-qm", "c"], cwd=repo.dir, check=True)
        self.assertEqual(upgrade.history_gaps(repo.dir, ["*.md"], {"a.md": {sha("A\n")}}, ["HEAD"]), [])

    def test_gaps_refuse_a_shallow_clone(self):
        repo = self.repo()
        repo.commit({"a.md": "A\n"})
        repo.commit({"a.md": "B\n"})
        clone = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, clone, ignore_errors=True)
        subprocess.run(["git", "clone", "-q", "--depth", "1", repo.dir.as_uri(), str(clone / "c")], check=True)
        with self.assertRaisesRegex(ValueError, "needs the full git history"):
            upgrade.history_gaps(clone / "c", ["*.md"], {}, ["HEAD"])

    def test_release_notes_are_a_markdown_list_of_one_version(self):
        with mock.patch.dict(upgrade.NOTES, {"9.0.0": ["one", "two"], "9.0.1": ["three"]}):
            self.assertEqual(upgrade.release_notes("9.0.0"), "- one\n- two\n")
        with mock.patch.dict(upgrade.NOTES, {"9.0.2": []}):
            for version in ("9.0.2", "9.9.9"):
                with self.subTest(version=version):
                    with self.assertRaisesRegex(ValueError, f"no upgrade notes for {version}"):
                        upgrade.release_notes(version)

if __name__ == "__main__":
    unittest.main()
