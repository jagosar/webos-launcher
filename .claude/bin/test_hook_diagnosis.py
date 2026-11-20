"""The hook diagnosis and the fallback's CLI check: why the hook looks dead (Git Bash first on Windows, python3,
settings, worktree heartbeats), doctor writing the Git Bash path, the agents losing CLAUDE_CODE_SIMPLE, and the
fallback's CLI and login check before any state. Moved as they were from test_acceptance_v216.py (design 0023,
agentic-flow 2.16.0) when 2.17.0 retired it (design 0024); the release checks stayed behind.

Run: python3 -m unittest discover -s .claude/bin -p test_hook_diagnosis.py
Hooks-check runs in temporary repos with a fixed PATH and HOME; the fallback runs the fake `claude`. Nothing is spent.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parent
ROOT = BIN.parents[1]
sys.path.insert(0, str(BIN))
import compat  # noqa: E402
import core  # noqa: E402
import testutil  # noqa: E402
from test_agentic import MANIFEST, PY, SCRIPT, Repo  # noqa: E402

SEEN = Path(".claude/state/hook-seen.json")
GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
DIAGNOSIS_KEYS = {"cwd", "project_dir", "root", "settings", "hook_command", "heartbeats", "worktree", "python3",
                  "hints", "git_bash"}
GIT_BASH_KEYS = {"configured", "default", "derived", "needs_path", "missing"}
FLOW_REPO_ONLY = unittest.skipUnless(core.is_flow_repo(ROOT), "the flow's own repo")
USER_GIT = "C:\\Users\\u\\AppData\\Local\\Programs\\Git"


def temp_dir(test, prefix):
    d = Path(tempfile.mkdtemp(prefix=prefix))
    test.addCleanup(shutil.rmtree, d, ignore_errors=True)
    return d


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DiagnosisTests(unittest.TestCase):
    """AC-001, AC-002, AC-003, AC-011: every run fixes PATH (a fake python3 and git) and HOME."""

    def setUp(self):
        self.repo = Repo(MANIFEST)
        self.addCleanup(self.repo.cleanup)
        self.home = temp_dir(self, "v216-home-")
        git = shutil.which("git")
        # git's own folder, unless it also holds python3 (/usr/bin): then a folder with a link to git alone. A
        # linked or copied git.exe on Windows cannot find its installation, so it never gets there.
        self.git_dir = Path(git).parent
        if shutil.which("python3", path=str(self.git_dir)):
            self.git_dir = temp_dir(self, "v216-git-")
            try:
                os.symlink(git, self.git_dir / Path(git).name)
            except OSError:
                self.skipTest("git's folder holds python3 and no link to git can be made")

    def env(self, python3="plain"):
        """python3: "plain" (a fake on PATH), "none" (no python3), or "store" (under a WindowsApps folder)."""
        folders = [str(self.git_dir)]
        if python3 != "none":
            where = temp_dir(self, "v216-py-") / ("WindowsApps" if python3 == "store" else "bin")
            testutil.fake_exe(where, "python3", "print('Python 3.12')\n")
            folders.insert(0, str(where))
        return {"PATH": os.pathsep.join(folders), "HOME": str(self.home), "USERPROFILE": str(self.home),
                "CLAUDECODE": "1"}

    @staticmethod
    def parse(r):
        lines = [line for line in r.stdout.splitlines() if line.strip().startswith("{")]
        return json.loads(lines[-1]) if lines else None

    def check(self, python3="plain"):
        r = self.repo.run("hooks-check", env=self.env(python3))
        return r, self.parse(r)

    def test_ac_001_a_failed_live_check_carries_the_diagnosis(self):
        r, out = self.check()
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertEqual((out["ok"], out["mode"]), (False, "live"))
        self.assertIn("reason", out)
        d = out["diagnosis"]
        self.assertEqual(set(d), DIAGNOSIS_KEYS)
        if compat.IS_WINDOWS:
            self.assertEqual(set(d["git_bash"]), GIT_BASH_KEYS)
        else:
            self.assertIsNone(d["git_bash"])
        self.assertEqual(Path(d["root"]).resolve(), self.repo.dir.resolve())
        self.assertTrue(d["settings"][0]["declares_hook"], d["settings"])
        self.assertIn("hook.py", d["hook_command"])
        first = d["heartbeats"][0]
        self.assertTrue(first["path"].replace("\\", "/").endswith(".claude/state/hook-seen.json"), first)
        self.assertFalse(first["exists"])
        self.assertIn("did not run for this call", r.stderr)
        self.assertTrue(any(line.startswith("- ") for line in r.stderr.splitlines()), r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_ac_001_no_diagnosis_when_the_check_passes_or_is_static(self):
        self.repo.write(SEEN.as_posix(), json.dumps({"ts": time.time(), "tool": "Bash", "command": "x",
                                                     "agent_type": None, "guarded_ts": None}))
        r, out = self.check()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("diagnosis", out)
        env = self.env()
        env.pop("CLAUDECODE")
        out = self.parse(self.repo.run("hooks-check", env=env))
        self.assertEqual(out["mode"], "static")
        self.assertNotIn("diagnosis", out)

    def test_ac_002_a_heartbeat_under_the_main_checkout_is_found(self):
        subprocess.run(["git", "add", "-A"], cwd=self.repo.dir, check=True, capture_output=True)
        subprocess.run(GIT + ["commit", "-qm", "init"], cwd=self.repo.dir, check=True, capture_output=True)
        worktree = self.repo.dir / ".claude" / "worktrees" / "w"
        subprocess.run(["git", "worktree", "add", "-q", str(worktree), "-b", "w"], cwd=self.repo.dir, check=True,
                       capture_output=True)
        self.repo.write(SEEN.as_posix(), json.dumps({"ts": time.time(), "tool": "Bash", "command": "x",
                                                     "agent_type": None, "guarded_ts": None}))
        env = dict(self.repo.env, **self.env(), CLAUDE_PROJECT_DIR=str(worktree))
        r = subprocess.run([sys.executable, str(SCRIPT), "hooks-check"], cwd=worktree, env=env, capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        out = self.parse(r)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertFalse(out["ok"])
        d = out["diagnosis"]
        self.assertTrue(d["worktree"]["inside"], d["worktree"])
        self.assertEqual(Path(d["worktree"]["main"]).resolve(), self.repo.dir.resolve())
        main_seen = (self.repo.dir / SEEN).resolve()
        others = [h for h in d["heartbeats"][1:] if Path(h["path"]).resolve() == main_seen]
        self.assertEqual(len(others), 1, d["heartbeats"])
        self.assertTrue(others[0]["exists"])
        self.assertLessEqual(others[0]["age_s"], 600)
        hint = [h for h in d["hints"] if "different project roots" in h]
        self.assertEqual(len(hint), 1, d["hints"])
        self.assertIn("hook-seen.json", hint[0])

    def test_ac_003_python3_and_the_settings_are_named_in_the_hints(self):
        r, out = self.check("none")
        self.assertIsNone(out["diagnosis"]["python3"])
        self.assertTrue(any("python3 is not on the PATH" in h for h in out["diagnosis"]["hints"]), out)
        r, out = self.check("store")
        self.assertTrue(any("Microsoft Store" in h for h in out["diagnosis"]["hints"]), out)
        r, out = self.check()
        self.assertEqual(len(out["diagnosis"]["hints"]), 1, out["diagnosis"]["hints"])
        self.assertIn("/hooks", out["diagnosis"]["hints"][0])
        self.repo.write(".claude/settings.local.json", json.dumps({"disableAllHooks": True}))
        r, out = self.check()
        hints = out["diagnosis"]["hints"]
        self.assertTrue(any("disableAllHooks is true in" in h and "settings.local.json" in h for h in hints), hints)
        self.assertFalse(any("/hooks" in h for h in hints), "the /hooks hint only when nothing else explains it")

    def test_ac_003_settings_of_the_wrong_shape_do_not_raise(self):
        self.repo.write(".claude/settings.local.json", json.dumps({"hooks": {"PreToolUse": ["not an entry"]}}))
        (self.home / ".claude").mkdir()
        (self.home / ".claude/settings.json").write_text(json.dumps({"hooks": "x"}), encoding="utf-8")
        r, out = self.check()
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual([s["declares_hook"] for s in out["diagnosis"]["settings"]], [True, False, False])

    def test_ac_011_the_dead_hook_message_names_git_bash(self):
        import guardrail
        for words in ("Git Bash", "doctor --fix", "python3", "worktree", "did not run for this call",
                      "/implement fallback"):
            self.assertIn(words, guardrail.HOOKS_DEAD_MSG)


def fake_fs(git=None, files=()):
    """which and exists fakes: `git` is what which("git") answers; `files` are the paths that exist."""
    present = set(files)
    return (lambda name: git if name == "git" else None), (lambda path: path in present)


class GitBashTests(unittest.TestCase):
    """AC-004, AC-005: git_bash_state with injected inputs, and the hint it gives."""

    def state(self, git=None, files=(), environ=None, settings_envs=None, is_windows=True):
        import guardrail
        which, exists = fake_fs(git, files)
        return guardrail.git_bash_state(is_windows=is_windows, environ=environ or {},
                                        settings_envs=settings_envs or [], which=which, exists=exists)

    def user_install(self, **extra):
        return self.state(git=USER_GIT + "\\cmd\\git.exe", files=[USER_GIT + "\\bin\\bash.exe"], **extra)

    def test_ac_004_the_five_cases(self):
        one = self.user_install()
        self.assertEqual(set(one), GIT_BASH_KEYS)
        self.assertTrue(one["needs_path"])
        self.assertEqual(one["derived"], USER_GIT + "\\bin\\bash.exe")
        two = self.user_install(environ={"CLAUDE_CODE_GIT_BASH_PATH": "D:\\Git\\bin\\bash.exe"})
        self.assertEqual((two["configured"], two["needs_path"]), ("D:\\Git\\bin\\bash.exe", False))
        three = self.user_install(settings_envs=[{}, {"CLAUDE_CODE_GIT_BASH_PATH": "D:\\x.exe"}])
        self.assertEqual(three["configured"], "D:\\x.exe")
        four = self.state(files=["C:\\Program Files\\Git\\bin\\bash.exe"])
        self.assertEqual((bool(four["default"]), four["needs_path"], four["missing"]), (True, False, False))
        five = self.state()
        self.assertTrue(five["missing"])

    def test_ac_004_off_windows_mingw64_and_bad_settings(self):
        self.assertIsNone(self.state(is_windows=False))
        mingw = self.state(git="D:\\G\\mingw64\\bin\\git.exe", files=["D:\\G\\bin\\bash.exe"])
        self.assertEqual(mingw["derived"], "D:\\G\\bin\\bash.exe")
        odd = self.user_install(settings_envs=["not an object", None, {"CLAUDE_CODE_GIT_BASH_PATH": 3}])
        self.assertTrue(odd["needs_path"])

    def hints(self, git_bash):
        import guardrail
        return guardrail._hints({"python3": "C:\\Python312\\python.exe",
                                 "settings": [{"path": "s", "exists": True, "declares_hook": True,
                                               "disable_all_hooks": False}],
                                 "heartbeats": [{"path": "h", "exists": False, "age_s": None}],
                                 "git_bash": git_bash})

    def test_ac_005_the_diagnosis_puts_git_bash_first(self):
        hints = self.hints(self.user_install())
        for words in ("Git Bash", USER_GIT + "\\bin\\bash.exe", "doctor --fix"):
            self.assertIn(words, hints[0])
        self.assertFalse(any("/hooks" in h for h in hints), hints)
        self.assertTrue(any("Git Bash was not found" in h for h in self.hints(self.state())))
        self.assertFalse(any("Git Bash" in h for h in self.hints(None)))


class DoctorGitBashTests(unittest.TestCase):
    """AC-006: doctor's git-bash finding, the write, and --fix with injected inputs and a temporary settings file."""

    def setUp(self):
        import doctor
        self.doctor = doctor
        self.dir = temp_dir(self, "v216-doctor-")

    def state(self, **kw):
        return GitBashTests.state(self, **kw)

    def user_state(self, configured=None):
        environ = {"CLAUDE_CODE_GIT_BASH_PATH": configured} if configured else {}
        return self.state(git=USER_GIT + "\\cmd\\git.exe", files=[USER_GIT + "\\bin\\bash.exe"], environ=environ)

    def test_ac_006_the_finding_for_each_state(self):
        finding = self.doctor.git_bash_finding(self.user_state())
        self.assertEqual(finding["severity"], "auto")
        self.assertIn(USER_GIT + "\\bin\\bash.exe", finding["message"])
        self.assertIn("settings.local.json", finding["fix"])
        self.assertIsNone(self.doctor.git_bash_finding(self.user_state(configured="D:\\b.exe")))
        self.assertIsNone(self.doctor.git_bash_finding(self.state(files=["C:\\Program Files\\Git\\bin\\bash.exe"])))
        self.assertEqual(self.doctor.git_bash_finding(self.state())["severity"], "mention")
        self.assertIsNone(self.doctor.git_bash_finding(None))

    def test_ac_006_the_write_keeps_every_other_key(self):
        cases = {"missing.json": None, "perm.json": {"permissions": {"allow": ["Bash(x)"]}},
                 "env.json": {"env": {"OTHER": "1"}}}
        for name, data in cases.items():
            with self.subTest(file=name):
                path = self.dir / name
                if data is not None:
                    path.write_text(json.dumps(data), encoding="utf-8")
                self.doctor.write_git_bash_path(path, "D:\\b.exe")
                out = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(out["env"]["CLAUDE_CODE_GIT_BASH_PATH"], "D:\\b.exe")
                for key, value in (data or {}).items():
                    if key == "env":
                        self.assertEqual(out["env"]["OTHER"], "1")
                    else:
                        self.assertEqual(out[key], value)
        listed = self.dir / "list.json"
        listed.write_text("[]", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.doctor.write_git_bash_path(listed, "D:\\b.exe")
        self.assertEqual(listed.read_text(encoding="utf-8"), "[]")

    def test_ac_006_fix_writes_the_path_and_the_finding_goes_away(self):
        from unittest import mock
        local = self.dir / "settings.local.json"

        def live_state():
            data = json.loads(local.read_text(encoding="utf-8")) if local.exists() else {}
            return self.user_state(configured=(data.get("env") or {}).get("CLAUDE_CODE_GIT_BASH_PATH"))
        with mock.patch.object(self.doctor, "SETTINGS_LOCAL", local):
            out = self.doctor.report(fix=True, git_bash=live_state)
        self.assertIn("git-bash", out["fixed"])
        self.assertNotIn("git-bash", [f["code"] for f in out["findings"]])
        self.assertEqual(json.loads(local.read_text(encoding="utf-8"))["env"]["CLAUDE_CODE_GIT_BASH_PATH"],
                         USER_GIT + "\\bin\\bash.exe")
        local.write_text("[]", encoding="utf-8")
        with mock.patch.object(self.doctor, "SETTINGS_LOCAL", local):
            out = self.doctor.report(fix=True, git_bash=lambda: self.user_state())
        self.assertNotIn("git-bash", out["fixed"])
        self.assertIn("git-bash", [f["code"] for f in out["findings"]])

    def test_ac_006_no_finding_off_windows_or_with_a_default_install(self):
        import guardrail
        state = guardrail.git_bash_state()
        if compat.IS_WINDOWS and not (state and state["default"]):
            self.skipTest("a Windows host without Git under Program Files")
        repo = Repo(MANIFEST)
        self.addCleanup(repo.cleanup)
        r = repo.run("doctor")
        out = json.loads(r.stdout.splitlines()[-1])
        self.assertNotIn("git-bash", [f["code"] for f in out["findings"]])


class CliTests(unittest.TestCase):
    """AC-007, AC-008: the agents' environment, and the fallback's CLI and login, checked once, before any state."""

    def setUp(self):
        self.repo = Repo(MANIFEST)
        self.addCleanup(self.repo.cleanup)
        self.bin = temp_dir(self, "v216-fake-")
        self.logs = temp_dir(self, "v216-logs-")
        self.repo.env.update(AGENTIC_CLAUDE=testutil.install_fake_claude(self.bin), FAKE_LOG_DIR=str(self.logs))
        self.repo.write("acc.py", "import sys\nsys.exit(0)\n")

    def state(self, rel):
        return self.repo.dir / ".claude/state" / rel

    def auth_lines(self):
        path = self.logs / "auth.log"
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def ledger(self):
        path = self.state("ledger.json")
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def test_ac_007_the_agents_lose_claude_code_simple_and_nothing_else(self):
        import core
        from unittest import mock
        env = {"CLAUDE_CODE_SIMPLE": "1", "CLAUDE_CODE_SESSION_ID": "s", "ANTHROPIC_BASE_URL": "u", "HTTPS_PROXY": "p"}
        with mock.patch.dict(os.environ, env):
            for auth in ("subscription", "inherit"):
                child = core.child_env({"auth": auth})
                self.assertNotIn("CLAUDE_CODE_SIMPLE", child)
                for name in ("CLAUDE_CODE_SESSION_ID", "ANTHROPIC_BASE_URL", "HTTPS_PROXY"):
                    self.assertEqual(child[name], env[name])

    def test_ac_008_the_cli_must_exist(self):
        self.repo.env["AGENTIC_CLAUDE"] = "no-such-claude-xyz"
        for args in (("run-wave", "1"), ("run-pipeline",), ("run-wave", "1", "WP-01", "--fix")):
            with self.subTest(command=args):
                r = self.repo.run(*args)
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                for words in ("no-such-claude-xyz", "AGENTIC_CLAUDE", "Claude Desktop"):
                    self.assertIn(words, r.stderr)
                self.assertNotIn("Traceback", r.stderr)
                self.assertFalse(self.state("snapshot-1.json").exists())
                self.assertFalse(self.state("briefs").exists())
                self.assertNotIn("fix_count", self.ledger().get("WP-01", {}))
        self.assertEqual(testutil.fake_calls(self.logs), [])

    def test_ac_008_a_cli_that_is_not_logged_in_stops_before_any_agent(self):
        r = self.repo.run("run-wave", "1", env={"FAKE_AUTH": "out", "ANTHROPIC_AAA_FAKE_INHERITED": "secretvalue123"})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("auth login", r.stderr)
        self.assertIn('"auth": "inherit"', r.stderr)
        self.assertIn("ANTHROPIC_AAA_FAKE_INHERITED", r.stderr)
        self.assertNotIn("secretvalue123", r.stderr)
        for claim in ("not signed in", "not shared"):
            self.assertNotIn(claim, r.stderr)
        self.assertEqual(testutil.fake_calls(self.logs), [])
        self.assertEqual(len(self.auth_lines()), 1)
        self.assertFalse(self.state("snapshot-1.json").exists())
        self.assertFalse(self.state("briefs").exists())
        self.assertEqual(self.ledger(), {})

    def test_ac_008_an_unreadable_status_warns_and_the_run_goes_on(self):
        r = self.repo.run("run-wave", "1", env={"FAKE_AUTH": "none"})
        warnings = [line for line in r.stderr.splitlines()
                    if line.startswith("[agentic] could not read the login status of")]
        self.assertEqual(len(warnings), 1, r.stderr)
        self.assertEqual(len(testutil.fake_calls(self.logs, "WP-01")), 1)
        self.assertEqual(len(testutil.fake_calls(self.logs, "WP-02")), 1)

    def test_ac_008_the_status_is_read_once_with_the_agents_environment(self):
        import test_pipeline
        repo = Repo(test_pipeline.E2E_MANIFEST)
        self.addCleanup(repo.cleanup)
        repo.env.update(AGENTIC_CLAUDE=self.repo.env["AGENTIC_CLAUDE"], FAKE_LOG_DIR=str(self.logs), FAKE_WRITE="1",
                        FAKE_MODE="nousage", ANTHROPIC_API_KEY="x")
        repo.write("acc.py", "import os, sys\nsys.exit(0 if all(os.path.exists(p) for p in "
                             "('src/a.py', 'src/b/x.py', 'src/c.py')) else 1)\n")
        subprocess.run(["git", "add", "-A"], cwd=repo.dir, check=True, capture_output=True)
        subprocess.run(GIT + ["commit", "-qm", "init"], cwd=repo.dir, check=True, capture_output=True)
        r = repo.run("run-pipeline")
        self.assertIn("RESULT: OK", r.stdout, r.stdout[-2000:] + r.stderr[-2000:])
        self.assertEqual(self.auth_lines(), ["-"])
        self.assertGreater(len(testutil.fake_calls(self.logs)), 3)


@FLOW_REPO_ONLY  # a project does not carry the flow's own guides
class DocsTests(unittest.TestCase):
    def read(self, rel):
        return (ROOT / rel).read_text(encoding="utf-8")

    def test_ac_009_the_docs_say_how_to_fix_git_bash_and_point_the_fallback_at_a_cli(self):
        implement = self.read(".claude/commands/implement.md")
        for words in ("doctor --fix", "CLAUDE_CODE_GIT_BASH_PATH", "AGENTIC_CLAUDE", "diagnosis"):
            self.assertIn(words, implement)
        fallback = implement.split("# 4. Fallback mode")[1]
        self.assertRegex(fallback, r"(?s)inherited.{0,200}(CLAUDE_|ANTHROPIC_)|(CLAUDE_|ANTHROPIC_).{0,200}inherited")
        hub = self.read("docs/how-to/README.md")
        for words in ("CLAUDE_CODE_GIT_BASH_PATH", "AGENTIC_CLAUDE"):
            self.assertIn(words, hub)
        self.assertLessEqual(len(hub.splitlines()), 260)
        works = self.read("docs/how-it-works.md")
        self.assertTrue("git_bash" in works or "Git Bash" in works)
        self.assertIn("diagnosis", works)
        self.assertIn("auth status", works)
        for text in (implement, hub, works):
            for claim in ("not shared", "not signed in"):
                self.assertNotIn(claim, text)


if __name__ == "__main__":
    unittest.main()
