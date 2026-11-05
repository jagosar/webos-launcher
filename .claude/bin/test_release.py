"""Tests of the release commands: `flow-history --check` and `release-notes` (design 0015).

Run: python3 -m unittest discover -s .claude/bin -p "test_release.py"
Each test builds its own git repository with a copy of the flow's scripts, its own plugin.json, tags and history: it
reads nothing of the repository it runs in, so it holds in a project too.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parent
GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
AGENT = ".claude/agents/qa.md"


class Copy:
    """A git repository holding the flow's scripts, a plugin.json with the given version and one agent file."""

    def __init__(self, version="1.0.0"):
        self.dir = Path(tempfile.mkdtemp(prefix="release-"))
        (self.dir / ".claude/bin").mkdir(parents=True)
        for f in BIN.glob("*.py"):
            if not f.name.startswith("test_"):
                shutil.copy(f, self.dir / ".claude/bin" / f.name)
        self.git("init", "-q")
        self.write(version=version, agent="A")
        self.commit()

    def git(self, *args):
        return subprocess.run(GIT + list(args), cwd=self.dir, capture_output=True, text=True, check=True)

    def write(self, version=None, agent=None):
        if version is not None:
            (self.dir / ".claude-plugin").mkdir(exist_ok=True)
            (self.dir / ".claude-plugin/plugin.json").write_text(json.dumps({"version": version}), encoding="utf-8")
        if agent is not None:
            (self.dir / ".claude/agents").mkdir(parents=True, exist_ok=True)
            (self.dir / AGENT).write_text(agent, encoding="utf-8")

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-qm", "c")

    def run(self, *args, env=None):
        base = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_PROJECT_DIR", "CLAUDECODE")}
        return subprocess.run([sys.executable, str(self.dir / ".claude/bin/agentic.py"), *args], cwd=self.dir,
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              env=dict(base, **(env or {})))

    def publish(self, tag):
        self.run("flow-history")
        self.commit()
        self.git("tag", tag)


class CheckCommandTests(unittest.TestCase):
    def copy(self, version="1.0.0"):
        repo = Copy(version)
        self.addCleanup(shutil.rmtree, repo.dir, ignore_errors=True)
        return repo

    def check(self, repo):
        r = repo.run("flow-history", "--check")
        return r.returncode, json.loads(r.stdout) if r.stdout.strip() else None

    def test_a_repo_without_history_or_tags_has_its_head_pending(self):
        code, data = self.check(self.copy())
        self.assertEqual(code, 3)
        self.assertEqual((data["tags"], data["pending"]), (0, "1.0.0"))
        self.assertIn({"ref": "HEAD", "path": AGENT}, data["missing"])

    def test_a_published_version_passes_and_later_edits_do_not_matter(self):
        repo = self.copy()
        repo.publish("agentic-flow--v1.0.0")
        repo.write(agent="B")
        repo.commit()
        self.assertEqual(self.check(repo), (0, {"ok": True, "tags": 1, "pending": None, "missing": []}))

    def test_a_bump_is_pending_until_the_history_is_regenerated(self):
        repo = self.copy()
        repo.publish("agentic-flow--v1.0.0")
        repo.write(version="1.1.0")
        repo.commit()
        code, data = self.check(repo)
        self.assertEqual((code, data["missing"]), (3, [{"ref": "HEAD", "path": ".claude-plugin/plugin.json"}]))
        repo.run("flow-history")
        repo.commit()
        self.assertEqual(self.check(repo)[0], 0)

    def test_an_unreadable_plugin_json_is_pending_as_dev(self):
        repo = self.copy()
        repo.publish("agentic-flow--v1.0.0")
        (repo.dir / ".claude-plugin/plugin.json").write_text("{", encoding="utf-8")
        self.assertEqual(self.check(repo)[1]["pending"], "dev")

    def test_the_check_writes_nothing(self):
        repo = self.copy()
        self.check(repo)
        self.assertFalse((repo.dir / ".claude/flow-history.json").exists())

    def test_a_published_tag_the_history_lacks_is_missing(self):
        """From test_acceptance_v29 ac_002."""
        repo = self.copy()
        repo.run("flow-history")
        repo.commit()
        repo.write(agent="B")
        repo.commit()
        repo.git("tag", "agentic-flow--v1.0.0")
        before = (repo.dir / ".claude/flow-history.json").read_bytes()
        code, data = self.check(repo)
        self.assertEqual((code, data["missing"]), (3, [{"ref": "agentic-flow--v1.0.0", "path": AGENT}]))
        self.assertEqual((repo.dir / ".claude/flow-history.json").read_bytes(), before)

    def test_a_shallow_clone_is_refused_not_judged(self):
        """From test_acceptance_v29 ac_005: a plain local path would ignore --depth, so the clone uses file://."""
        repo = self.copy()
        repo.publish("agentic-flow--v1.0.0")
        repo.write(agent="B")
        repo.commit()
        clone = Path(tempfile.mkdtemp(prefix="release-shallow-")) / "c"
        self.addCleanup(shutil.rmtree, clone.parent, ignore_errors=True)
        subprocess.run(["git", "clone", "-q", "--depth", "1", repo.dir.as_uri(), str(clone)], check=True)
        env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_PROJECT_DIR", "CLAUDECODE")}
        r = subprocess.run([sys.executable, str(clone / ".claude/bin/agentic.py"), "flow-history", "--check"],
                           cwd=clone, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("flow-history: needs the full git history", r.stderr)
        self.assertNotIn('"ok"', r.stdout)

    def test_other_arguments_are_refused_without_writing(self):
        repo = self.copy()
        for args in (["--chek"], ["--check", "x"], ["extra"]):
            with self.subTest(args=args):
                r = repo.run("flow-history", *args)
                self.assertEqual(r.returncode, 1)
                self.assertIn("Uso: flow-history [--check]", r.stderr)
        self.assertFalse((repo.dir / ".claude/flow-history.json").exists())

    def test_both_commands_are_exempt_from_root_drift(self):
        repo, other = self.copy(), self.copy()
        for args in (["flow-history", "--check"], ["release-notes", "1.0.0"]):
            with self.subTest(args=args):
                r = repo.run(*args, env={"CLAUDE_PROJECT_DIR": str(other.dir)})
                self.assertNotIn("CLAUDE_PROJECT_DIR names", r.stderr)


class NotesCommandTests(unittest.TestCase):
    def setUp(self):
        self.repo = Copy("1.0.0")
        self.addCleanup(shutil.rmtree, self.repo.dir, ignore_errors=True)
        upgrade = self.repo.dir / ".claude/bin/upgrade.py"  # the copy's notes: this test owns them
        notes = 'NOTES = {\n    "1.0.0": ["first", "second"],'
        text = upgrade.read_text(encoding="utf-8").replace("NOTES = {", notes, 1)
        upgrade.write_text(text, encoding="utf-8")

    def test_the_version_of_plugin_json_by_default(self):
        r = self.repo.run("release-notes")
        self.assertEqual((r.returncode, r.stdout), (0, "- first\n- second\n"))

    def test_a_version_without_notes_fails_with_nothing_on_stdout(self):
        r = self.repo.run("release-notes", "0.0.1")
        self.assertEqual((r.returncode, r.stdout), (1, ""))
        self.assertIn("no upgrade notes for 0.0.1", r.stderr)

    def test_two_arguments_are_refused(self):
        r = self.repo.run("release-notes", "1.0.0", "2.0.0")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Uso: release-notes [VERSION]", r.stderr)


if __name__ == "__main__":
    unittest.main()
