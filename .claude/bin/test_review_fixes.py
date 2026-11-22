"""Tests of the bugs a full review of the flow found: the hook's git rules (a line continuation, a slow regex, false
alarms), the cost of a resumed agent counted twice, a stale resume state, save-result losing its file, tracebacks on a
malformed manifest or command, Windows paths in commands, and a child that ignores the request to end.

Run: python3 -m unittest discover -s .claude/bin -p test_review_fixes.py
"""
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compat  # noqa: E402
import launch  # noqa: E402
import verification  # noqa: E402
from test_agentic import MANIFEST, Repo  # noqa: E402

HOOK_SECONDS = 10  # far above a healthy hook call, far below what the slow rules took (20 s)


class RepoCase(unittest.TestCase):
    manifest = MANIFEST

    def setUp(self):
        self.repo = Repo(self.manifest)
        self.addCleanup(self.repo.cleanup)

    def bash(self, command, wp=None):
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
        return subprocess.run([sys.executable, str(Path(__file__).with_name("agentic.py")), "hook"],
                              cwd=self.repo.dir, input=payload, capture_output=True, text=True, timeout=HOOK_SECONDS,
                              env=dict(self.repo.env, **({"AGENTIC_WP": wp} if wp else {}))).returncode


class GitHookBypassTests(RepoCase):
    def test_a_line_continuation_does_not_hide_the_bypass(self):
        for command in ('git commit -m "m" \\\n  --no-verify', 'git commit -m "m" \\\n  -n',
                        "git commit \\\n -an -m x"):
            with self.subTest(command=command):
                self.assertEqual(self.bash(command), 2)

    def test_every_way_to_set_the_hooks_path_is_refused(self):
        for command in ("git --config-env=core.hooksPath=X commit -m x", "git -c 'core.hooksPath'=/x commit -m x",
                        "git -c core.hooksPath=/x commit -m x", "git -C . commit -n -m x", "git commit -nm x"):
            with self.subTest(command=command):
                self.assertEqual(self.bash(command), 2)

    def test_other_commands_and_flags_are_not_mistaken_for_it(self):
        for command in ("git log --grep commit -n 5", "git commit -uno -m x", "git commit -m 'skip -n here'",
                        "git commit -m x && head -n 3 file", "git show -n 1"):
            with self.subTest(command=command):
                self.assertEqual(self.bash(command), 0)

    def test_a_long_command_is_judged_in_time(self):
        self.assertEqual(self.bash("git commit " * 900), 0)
        self.assertEqual(self.bash("git commit " * 900 + "-n"), 2)
        self.assertEqual(self.bash("agentic.py quality " * 520, wp="WP-01"), 0)
        self.assertEqual(self.bash("python3 .claude/bin/agentic.py quality --init", wp="WP-01"), 2)


class ProtectedPathTests(RepoCase):
    def test_another_case_of_the_archive_is_still_the_archive(self):
        # On a case-insensitive disk (macOS, Windows) docs/Architecture/ is docs/architecture/.
        manifest = dict(MANIFEST, docs={"files": ["docs/"]})
        repo = Repo(manifest)
        self.addCleanup(repo.cleanup)
        payload = {"tool_name": "Write", "tool_input": {"file_path": str(repo.dir / "docs/Architecture/0001-x.md"),
                                                         "content": "x"}}
        self.assertEqual(repo.hook(payload, wp="DOCS"), 2)
        payload["tool_input"]["file_path"] = str(repo.dir / "docs/guide.md")
        self.assertEqual(repo.hook(payload, wp="DOCS"), 0)


class ResumedCostTests(unittest.TestCase):
    def output(self, total):
        path = Path(tempfile.mkdtemp(prefix="review-cost-")) / "out.json"
        self.addCleanup(lambda: __import__("shutil").rmtree(path.parent, ignore_errors=True))
        path.write_text(json.dumps({"total_cost_usd": total, "usage": {"input_tokens": 5, "output_tokens": 7}}),
                        encoding="utf-8")
        return path

    def test_a_resumed_session_reports_only_its_own_cost(self):
        self.assertEqual(launch.usage_of(self.output(0.1363), spent=0.1038)["cost_usd"], 0.0325)
        self.assertEqual(launch.usage_of(self.output(0.1363))["cost_usd"], 0.1363)
        self.assertIsNone(launch.usage_of(self.output(None), spent=0.1)["cost_usd"])

    def test_the_engine_hands_a_resumed_call_what_its_session_already_cost(self):
        calls = []

        def fake_launch(prompt, agent_type, label, ident, **opts):
            calls.append(opts)
            return {"report": {"status": "DONE"}, "session_id": "s1", "session_cost": 0.25}
        agent = launch.resuming(fake_launch, {"WP-01"})
        agent("p", "implementer", "WP-01", "WP-01")
        agent("p", "implementer", "WP-01 (fix)", "WP-01", resume=True)
        self.assertEqual((calls[1]["resume"], calls[1]["spent"]), ("s1", 0.25))
        self.assertNotIn("resume", calls[0])


class ResumeStateTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(tempfile.mkdtemp(prefix="review-resume-")) / "pipeline.json"
        self.addCleanup(lambda: __import__("shutil").rmtree(self.state.parent, ignore_errors=True))
        patch = mock.patch.object(launch, "PIPELINE_STATE", self.state)
        patch.start()
        self.addCleanup(patch.stop)

    def test_a_resume_continues_after_the_last_finished_wave(self):
        self.state.write_text('{"completed_waves": [1, 2]}', encoding="utf-8")
        self.assertEqual(launch.resume_wave(True), 3)
        self.assertTrue(self.state.exists())

    def test_a_fresh_run_forgets_the_waves_of_the_one_before(self):
        self.state.write_text('{"completed_waves": [1, 2]}', encoding="utf-8")
        self.assertIsNone(launch.resume_wave(False))
        self.assertFalse(self.state.exists(), "or a later --resume would skip waves this run never finished")
        self.assertIsNone(launch.resume_wave(True))


class TracebackTests(RepoCase):
    def clean_failure(self, result, words):
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn(words, result.stdout + result.stderr)

    def test_save_result_keeps_a_file_it_cannot_save(self):
        self.repo.write(".agentic-result.json", "[1, 2]")
        self.clean_failure(self.repo.run("save-result", ".agentic-result.json"), "save-result")
        self.assertTrue((self.repo.dir / ".agentic-result.json").exists())

    def test_save_result_still_saves_and_deletes(self):
        self.repo.write(".agentic-result.json", '{"result": "OK"}')
        self.assertEqual(self.repo.run("save-result", ".agentic-result.json").returncode, 0)
        self.assertFalse((self.repo.dir / ".agentic-result.json").exists())
        self.assertTrue((self.repo.dir / ".claude/state/runs/workflow-result.json").exists())

    def test_a_manifest_with_a_wrong_type_is_invalid_not_a_crash(self):
        for change in ({"allow_bash": "x"}, {"allow_bash": [5]}, {"qa": []},
                       {"work_packages": [dict(MANIFEST["work_packages"][0], id=5)]}):
            with self.subTest(change=change):
                repo = Repo(dict(MANIFEST, **change))
                self.addCleanup(repo.cleanup)
                self.clean_failure(repo.run("validate"), "Invalid manifest")

    def test_docs_check_reads_a_linked_file_that_is_not_utf8(self):
        self.repo.write("README.md", "[a](docs/other.md#title)\n")
        (self.repo.dir / "docs").mkdir()
        (self.repo.dir / "docs/other.md").write_bytes(b"# Title\n\xff\xfe\n")
        result = self.repo.run("docs-check")
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(json.loads(result.stdout)["broken"], [])


class RunCommandTests(unittest.TestCase):
    def test_a_command_that_cannot_start_is_a_code_not_a_crash(self):
        with tempfile.TemporaryDirectory() as d:
            plain = Path(d) / "plain.txt"
            plain.write_text("not a program", encoding="utf-8")
            for command, code in ((plain.as_posix(), 126), ('"unbalanced', 126), ("no-such-program-xyz", 127)):
                with self.subTest(command=command):
                    self.assertEqual(verification.run_command(command)[0], code)


class CompatTests(unittest.TestCase):
    def test_split_keeps_windows_paths(self):
        with mock.patch.object(compat, "IS_WINDOWS", True):
            self.assertEqual(compat.split(r'C:\Tools\claude.exe -p "a b"'), ["C:/Tools/claude.exe", "-p", "a b"])
            self.assertEqual(compat.split(r".\gradlew.bat test"), ["./gradlew.bat", "test"])
        with mock.patch.object(compat, "IS_WINDOWS", False):
            self.assertEqual(compat.split(r"grep a\ b 'c d'"), ["grep", "a b", "c d"])

    @unittest.skipIf(compat.IS_WINDOWS, "taskkill /F already forces it")
    def test_end_kills_a_child_that_ignores_the_request(self):
        code = ("import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('up', flush=True); "
                "time.sleep(60)")
        proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, **compat.group_kwargs())
        proc.stdout.readline()  # the handler is installed
        started = time.monotonic()
        compat.end(proc, grace=0.5)
        self.assertIsNotNone(proc.poll())
        self.assertLess(time.monotonic() - started, 10)
        proc.stdout.close()


if __name__ == "__main__":
    unittest.main()
