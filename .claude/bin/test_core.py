"""Tests of core.py: what every module shares. Today, the refusal of a command whose CLAUDE_PROJECT_DIR names another
repository than the one it runs in (issue 103).

Run: python3 -m unittest discover -s .claude/bin -p "test_core.py"
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_agentic import MANIFEST, SCRIPT, Base, Repo, write_payload  # noqa: E402


class RootDriftTests(Base):
    """CLAUDE_PROJECT_DIR naming a repository other than the one the command runs in (issue 103)."""

    def other(self):
        repo = Repo(MANIFEST)
        self.addCleanup(repo.cleanup)
        return repo

    def test_a_command_run_from_another_repo_is_refused_with_both_paths(self):
        other = self.other()
        r = self.repo.run("validate", env={"CLAUDE_PROJECT_DIR": str(other.dir)})  # cwd is self.repo, not other
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn(str(other.dir), r.stderr)
        self.assertIn(str(self.repo.dir.resolve()), r.stderr)
        self.assertFalse((other.dir / ".claude" / "state").exists(), "nothing was done in the other repository")

    def test_the_hook_is_refused_too_so_it_fails_closed(self):
        other = self.other()
        payload = json.dumps(write_payload("src/a.py"))
        r = self.repo.run("hook", stdin=payload, env={"CLAUDE_PROJECT_DIR": str(other.dir)})
        self.assertEqual(r.returncode, 1, r.stderr)

    def test_a_command_run_inside_the_named_repo_is_allowed(self):
        # The live script of this checkout, run with the variable naming a temporary repo and cwd inside it: the shape
        # of every test here, of a fallback agent and of install's own subprocesses.
        self.assertEqual(self.repo.run("validate").returncode, 0)

    def test_install_and_flow_history_are_exempt(self):
        target = self.other()
        r = subprocess.run([sys.executable, str(SCRIPT), "install", str(target.dir), "--dry-run"], cwd=self.repo.dir,
                           env=dict(self.repo.env, CLAUDE_PROJECT_DIR=str(target.dir)), capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("dry run: nothing is written", r.stdout)



if __name__ == "__main__":
    unittest.main()
