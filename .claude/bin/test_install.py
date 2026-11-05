"""Tests of install end to end: a source (a copy of this flow with a test-made history) installed into a target git
repository. Moved here from the retired per-release acceptance files (design 0017); each class names its origin.

Run: python3 -m unittest discover -s .claude/bin -p "test_install.py"
The tests build their own source and target and never read this repository's history, tags or version, so they hold
in a project too.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parent
ROOT = BIN.parents[1]
sys.path.insert(0, str(BIN))
import agentic  # noqa: E402

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "core.autocrlf=false"]
BASELINE = ".claude/quality-baseline.json"
SOURCE_DIRS = (".claude/agents", ".claude/commands", ".claude/workflows", ".claude/rules-library", "plugin/commands",
               ".github/workflows")


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def write(root, files):
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, bytes):
            p.write_bytes(data)
        else:
            p.write_text(data, encoding="utf-8")


def commit(root, message="c"):
    subprocess.run(GIT + ["add", "-A"], cwd=root, check=True)
    subprocess.run(GIT + ["commit", "-qm", message, "--allow-empty"], cwd=root, check=True, capture_output=True)


def listed(out, kind):
    """The paths the installer lists under `<kind>: N`."""
    found, inside = [], False
    for line in out.splitlines():
        if line.strip().startswith(f"{kind}:"):
            inside = True
        elif inside and line.startswith("    "):
            found.append(line.strip())
        else:
            inside = False
    return found


class InstallCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="install-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_PROJECT_DIR", "CLAUDECODE", "AGENTIC_WP")}
        self.src = self.make_source()

    def make_source(self):
        src = self.tmp / "source"
        for rel in SOURCE_DIRS:
            if (ROOT / rel).is_dir():
                shutil.copytree(ROOT / rel, src / rel, dirs_exist_ok=True)
        shutil.copytree(BIN, src / ".claude/bin", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy(ROOT / ".claude/settings.json", src / ".claude/settings.json")
        write(src, {".claude-plugin/plugin.json": json.dumps({"name": "agentic-flow", "version": "1.1.0"})})
        self.history({}, src)
        return src

    def history(self, files, src=None):
        write(src or self.src, {".claude/flow-history.json": json.dumps(
            {"generated_from": "test", "files": {k: sorted(sha(t) for t in v) for k, v in files.items()}})})

    def target(self, files, name="target"):
        t = self.tmp / name
        t.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=t, check=True)
        write(t, dict({"main.py": "print(1)\n"}, **files))
        commit(t, "old flow")
        return t

    def install(self, target, *flags):
        script = self.src / ".claude/bin/agentic.py"
        return subprocess.run([sys.executable, str(script), "install", str(target), *flags], capture_output=True,
                              text=True, env=self.env, encoding="utf-8", errors="replace")

    def same_as_source(self, target, rel):
        return (target / rel).read_bytes() == (self.src / rel).read_bytes()


class IntactAndEditedTests(InstallCase):
    """From test_acceptance_upgrade ac_001, ac_002, ac_004 and test_acceptance_portable ac_007."""

    def test_an_intact_file_is_updated_without_an_install_record(self):
        self.history({".claude/agents/implementer.md": ["OLD IMPLEMENTER\n"]})
        t = self.target({".claude/agents/implementer.md": "OLD IMPLEMENTER\n"})
        r = self.install(t)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(self.same_as_source(t, ".claude/agents/implementer.md"))
        self.assertIn(".claude/agents/implementer.md", listed(r.stdout, "updated"))
        record = json.loads((t / ".claude/agentic-install.json").read_text(encoding="utf-8"))
        self.assertEqual(record["version"], "1.1.0")

    def test_a_crlf_copy_of_a_published_file_counts_as_intact(self):
        self.history({".claude/agents/qa.md": ["OLD\n"]})
        t = self.target({".claude/agents/qa.md": b"OLD\r\n"})
        r = self.install(t)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(".claude/agents/qa.md", listed(r.stdout, "updated"))
        self.assertNotIn(".claude/agents/qa.md", listed(r.stdout, "modified by you (left alone)"))

    def test_an_edited_file_is_kept_with_the_new_version_beside_it(self):
        t = self.target({".claude/agents/qa.md": "MY OWN QA\n"})
        r = self.install(t)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((t / ".claude/agents/qa.md").read_text(encoding="utf-8"), "MY OWN QA\n")
        copy = t / ".claude/state/upgrade/.claude/agents/qa.md"
        self.assertEqual(copy.read_bytes(), (self.src / ".claude/agents/qa.md").read_bytes())
        self.assertIn(".claude/agents/qa.md", listed(r.stdout, "modified by you (left alone)"))
        self.assertIn("kept files: new versions in .claude/state/upgrade/", r.stdout)
        dry = self.target({".claude/agents/qa.md": "MY OWN QA\n"}, "dry")
        self.assertEqual(self.install(dry, "--dry-run").returncode, 0)
        self.assertFalse((dry / ".claude/state/upgrade").exists())

    def test_a_retired_file_is_removed_only_if_intact(self):
        self.history({".claude/workflows/agentic-wave.js": ["OLD WAVE\n"], ".claude/commands/qa.md": ["OLD QA CMD\n"]})
        t = self.target({".claude/workflows/agentic-wave.js": "OLD WAVE\n", ".claude/commands/qa.md": "USER QA CMD\n"})
        r = self.install(t)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse((t / ".claude/workflows/agentic-wave.js").exists())
        self.assertIn(".claude/workflows/agentic-wave.js", listed(r.stdout, "removed"))
        self.assertEqual((t / ".claude/commands/qa.md").read_text(encoding="utf-8"), "USER QA CMD\n")
        self.assertNotIn(".claude/commands/qa.md", listed(r.stdout, "removed"))


class RefusalTests(InstallCase):
    """From test_acceptance_upgrade ac_005 and ac_006."""

    def dirty_target(self, name):
        self.history({".claude/agents/implementer.md": ["OLD IMPLEMENTER\n"]})
        t = self.target({".claude/agents/implementer.md": "OLD IMPLEMENTER\n"}, name)
        (t / ".claude/agents/implementer.md").write_text("EDITED\n", encoding="utf-8")
        return t

    def test_uncommitted_flow_files_stop_it_before_writing(self):
        t = self.dirty_target("dirty")
        r = self.install(t)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("uncommitted: .claude/agents/implementer.md", r.stderr)
        self.assertFalse((t / ".claude/agentic-install.json").exists())
        self.assertFalse((t / ".claude/bin/agentic.py").exists(), "nothing is written before refusing")
        self.assertEqual(self.install(t, "--allow-dirty").returncode, 0)

    def test_a_dry_run_only_warns_and_a_plain_folder_is_refused(self):
        r = self.install(self.dirty_target("dry"), "--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("warning: uncommitted: .claude/agents/implementer.md", r.stdout)
        plain = self.tmp / "plain"
        plain.mkdir()
        r = self.install(plain)
        self.assertEqual(r.returncode, 1)
        self.assertIn("not a git repository", r.stderr)

    def test_a_run_in_progress_stops_it_even_with_allow_dirty(self):
        t = self.target({})
        write(t, {".claude/state/ledger.json": json.dumps({"_stages": {"1": "OK"}, "WP-01": {"state": "running"}})})
        for flags in ((), ("--allow-dirty",)):
            with self.subTest(flags=flags):
                r = self.install(t, *flags)
                self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
                self.assertIn("a run is in progress (WP-01)", r.stderr)
                self.assertIn("agentic.py stop", r.stderr)
        (t / ".claude/state/ledger.json").write_text(json.dumps({"WP-01": {"state": "stopped"}}), encoding="utf-8")
        self.assertEqual(self.install(t).returncode, 0)


class ReportTests(InstallCase):
    """From test_acceptance_upgrade ac_007 and ac_009: the version line, the notes and validate afterwards."""

    def first_lines(self, record_version):
        files = {".claude/agents/qa.md": "OLD QA\n"} if record_version is None else {
            ".claude/agentic-install.json": json.dumps({"version": record_version, "files": {}})}
        r = self.install(self.target(files, f"v-{record_version}"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout.splitlines()[0], r.stdout

    def test_the_version_line_and_notes_only_when_the_version_rises(self):
        for old, shown, notes in (("1.0.0", "1.0.0", True), ("1.1.0", "1.1.0", False), (None, "unknown", True)):
            with self.subTest(old=old):
                line, out = self.first_lines(old)
                self.assertTrue(line.startswith(f"agentic-flow {shown} -> 1.1.0 -> "), line)
                self.assertEqual("upgrade notes:" in out, notes)

    def test_validate_runs_after_install_without_changing_its_result(self):
        bad = "```json wp-manifest\n" + json.dumps({"work_packages": []}) + "\n```\n"
        r = self.install(self.target({"architecture.md": bad}, "bad-plan"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertLess(r.stdout.index("validate: FAILED:"), r.stdout.index("RESULT: OK"))
        good = "```json wp-manifest\n" + json.dumps(
            {"work_packages": [{"id": "WP-01", "wave": 1, "files": ["src/a.py"]}]}) + "\n```\n"
        r = self.install(self.target({"architecture.md": good}, "good-plan"))
        self.assertIn("validate: OK", r.stdout, r.stdout + r.stderr)


class QualityBaselineTests(InstallCase):
    """From test_acceptance_v27 ac_007: install creates, keeps or replaces the project's quality baseline."""

    def own_baseline(self, flow_repo):
        return json.dumps({"version": 1, "flow_repo": flow_repo, "limits": {}, "exclude": [], "files": {},
                           "functions": {}}, indent=2) + "\n"

    def test_install_creates_a_baseline_that_passes(self):
        t = self.target({})
        r = self.install(t)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("quality baseline: created", r.stdout + r.stderr)
        self.assertIs(json.loads((t / BASELINE).read_text(encoding="utf-8"))["flow_repo"], False)
        check = subprocess.run([sys.executable, str(t / ".claude/bin/agentic.py"), "quality"], cwd=t,
                               env=dict(self.env, CLAUDE_PROJECT_DIR=str(t)), capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)

    def test_a_projects_baseline_is_kept_and_a_template_one_replaced(self):
        own = self.own_baseline(False)
        t = self.target({BASELINE: own}, "own")
        r = self.install(t)
        self.assertIn("quality baseline: kept", r.stdout + r.stderr)
        self.assertEqual((t / BASELINE).read_text(encoding="utf-8"), own)
        t = self.target({BASELINE: self.own_baseline(True)}, "template")
        r = self.install(t)
        self.assertIn("quality baseline: replaced", r.stdout + r.stderr)
        self.assertIs(json.loads((t / BASELINE).read_text(encoding="utf-8"))["flow_repo"], False)

    def test_a_dry_run_writes_no_baseline(self):
        t = self.target({})
        r = self.install(t, "--dry-run")
        self.assertIn("quality baseline: would be created", r.stdout + r.stderr)
        self.assertFalse((t / BASELINE).exists())


class SettingsTests(InstallCase):
    """From test_acceptance_v21 ac_008 and test_acceptance_waiting ac_006: what install does to settings.json."""

    def test_an_old_flow_hook_is_upgraded_once(self):
        src = json.loads((ROOT / ".claude/settings.json").read_text(encoding="utf-8"))
        own = {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo mine"}]}
        old = {"matcher": "Write|Edit|Bash",
               "hooks": [{"type": "command", "command": 'python3 "$CLAUDE_PROJECT_DIR/.claude/bin/agentic.py" hook'}]}
        dst = self.tmp / "settings.json"
        dst.write_text(json.dumps({"hooks": {"PreToolUse": [own, old]}}), encoding="utf-8")
        settings, added = agentic.merge_settings(dst, src)
        self.assertIn("hook PreToolUse (StructuredOutput)", added)
        pre = settings["hooks"]["PreToolUse"]
        self.assertEqual(pre[0], own)
        flow = [e for e in pre if "hook.py" in e["hooks"][0]["command"]]
        self.assertEqual(len(flow), 1)
        self.assertEqual(flow[0]["matcher"], "Write|Edit|Bash|StructuredOutput")
        self.assertEqual(flow[0]["hooks"][0]["timeout"], 900)
        dst.write_text(json.dumps(settings), encoding="utf-8")
        again, added = agentic.merge_settings(dst, src)
        self.assertEqual((added, again["hooks"]["PreToolUse"]), ([], pre))

    def test_the_waiting_hooks_go_in_once_per_event_next_to_the_projects(self):
        mine = {"matcher": "Write", "hooks": [{"type": "command", "command": "echo mine"}]}
        t = self.target({".claude/settings.json": json.dumps({"hooks": {"PostToolUse": [mine]}})})
        first = self.install(t)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        commit(t, "installed")
        settings = json.loads((t / ".claude/settings.json").read_text(encoding="utf-8"))
        for event in ("PermissionRequest", "PostToolUse", "PostToolUseFailure", "SubagentStop"):
            with self.subTest(event=event):
                ours = [h for e in settings["hooks"].get(event, []) for h in e.get("hooks", [])
                        if "waiting.py" in h.get("command", "")]
                self.assertEqual(len(ours), 1)
        self.assertIn(mine, settings["hooks"]["PostToolUse"])
        self.assertIn("hook PermissionRequest", first.stdout)
        self.assertTrue((t / ".claude/bin/waiting.py").is_file())
        second = self.install(t)
        self.assertNotIn("hook PermissionRequest", second.stdout)


if __name__ == "__main__":
    unittest.main()
