"""Tests of guardrail.py and the stage checks it feeds: the hook's heartbeat, hooks-check, a stage's proof that the
hook ran, and the stop gate's messages. Moved here from the retired per-release acceptance files (design 0017); each
class names its origin.

Run: python3 -m unittest discover -s .claude/bin -p "test_guardrail.py"
"""
import json
import shutil
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_agentic import MANIFEST, PY, Repo, bash_payload, write_payload  # noqa: E402

SEEN = Path(".claude/state/hook-seen.json")
NO_GUARD = "GUARDRAIL HOOK SAW NO FLOW AGENT"
FAILED = "FAILED acceptance/test_x.py::test_ac_001_x - assert"
GATE_MANIFEST = {
    "allow_bash": ["Bash(python3 -m unittest:*)"],
    "acceptance": {"files": ["acceptance/"], "command": f"{PY} acc.py"},
    "work_packages": [
        {"id": "WP-01", "wave": 1, "depends_on": [], "files": ["src/a.py"], "acs": ["AC-001"]},
        {"id": "WP-02", "wave": 1, "depends_on": [], "files": ["src/b/"], "acs": ["AC-002"]},
    ],
}


def finished_report(wp="WP-01"):
    return {"tool_name": "StructuredOutput", "agent_type": "implementer",
            "tool_input": {"status": "DONE", "wp": wp, "files": [], "checks": [], "internal_decisions": [],
                           "blocker": None, "not_checked": [], "concerns": [], "missing_context": None}}


def acceptance_script(lines, code):
    body = "\n".join(f"print({line!r})" for line in lines)
    return f"import sys\n{body}\nsys.exit({code})\n"


class RepoCase(unittest.TestCase):
    manifest = MANIFEST

    def setUp(self):
        self.repo = Repo(self.manifest)
        self.addCleanup(self.repo.cleanup)

    def seen(self):
        return json.loads((self.repo.dir / SEEN).read_text(encoding="utf-8"))

    def put_seen(self, raw):
        self.repo.write(SEEN.as_posix(), raw if isinstance(raw, str) else json.dumps(raw))

    def hook(self, payload, wp=None):
        return self.repo.run("hook", stdin=json.dumps(payload), env={"AGENTIC_WP": wp} if wp else None)

    def check(self, live=False):
        r = self.repo.run("hooks-check", env={"CLAUDECODE": "1"} if live else None)
        lines = [line for line in r.stdout.splitlines() if line.strip().startswith("{")]
        return r, (json.loads(lines[-1]) if lines else None)

    def gate(self):
        path = self.repo.dir / ".claude/state/gate.json"
        return json.loads(path.read_text(encoding="utf-8") or "{}") if path.exists() else {}


class HeartbeatTests(RepoCase):
    """From test_acceptance_v24 ac_001: every hook call leaves a heartbeat; only a flow agent counts as a guard."""

    def test_every_call_records_itself_and_only_agents_move_the_guard(self):
        self.hook(bash_payload("ls", agent_id="a1", agent_type="implementer"))
        first = self.seen()
        self.assertEqual((first["tool"], first["command"], first["agent_type"]), ("Bash", "ls", "implementer"))
        self.assertIsNotNone(first["guarded_ts"])
        self.assertLessEqual(abs(first["ts"] - time.time()), 60)
        self.assertEqual(self.hook(write_payload("src/a.py")).returncode, 0)
        second = self.seen()
        self.assertEqual((second["tool"], second["command"], second["agent_type"]), ("Write", "", None))
        self.assertEqual(second["guarded_ts"], first["guarded_ts"], "the main thread never counts as a guard")

    def test_fallback_and_read_only_agents_count_but_the_gatekeeper_does_not(self):
        blank = {"ts": 1, "tool": "Bash", "command": "", "agent_type": None, "guarded_ts": None}
        cases = [(lambda: self.hook(bash_payload("ls"), wp="WP-01"), True),
                 (lambda: self.hook(bash_payload("ls", agent_id="v1", agent_type="verifier")), True),
                 (lambda: self.hook(bash_payload("ls", agent_id="g1", agent_type="gatekeeper")), False)]
        for call, guards in cases:
            self.put_seen(blank)
            call()
            self.assertEqual(self.seen()["guarded_ts"] is not None, guards)


class HooksCheckTests(RepoCase):
    """From test_acceptance_v24 ac_002 and ac_003: live and static hooks-check."""

    def test_a_fresh_heartbeat_passes_live(self):
        self.put_seen({"ts": time.time(), "tool": "Bash", "command": "x", "agent_type": None, "guarded_ts": None})
        r, out = self.check(live=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((out["ok"], out["mode"]), (True, "live"))

    def test_a_stale_missing_or_corrupt_heartbeat_fails_live(self):
        stale = json.dumps({"ts": time.time() - 1000, "tool": "Bash", "command": "", "agent_type": None,
                            "guarded_ts": None})
        for name, raw in (("stale", stale), ("missing", None), ("corrupt", "{not json")):
            with self.subTest(heartbeat=name):
                path = self.repo.dir / SEEN
                if path.exists():
                    path.unlink()
                if raw is not None:
                    self.put_seen(raw)
                r, out = self.check(live=True)
                self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
                self.assertEqual((out["ok"], out["mode"]), (False, "live"))
                self.assertIn("did not run for this call", r.stderr)
                self.assertNotIn("Traceback", r.stderr)

    def test_static_mode_reads_the_settings_and_never_the_heartbeat(self):
        r, out = self.check()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((out["ok"], out["mode"]), (True, "static"))
        self.assertFalse((self.repo.dir / SEEN).exists())

    def test_static_mode_fails_without_the_hook_or_with_hooks_disabled(self):
        self.repo.write(".claude/settings.json", json.dumps({"hooks": {"PreToolUse": []}}))
        r, out = self.check()
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("settings.json", out["reason"])
        self.repo.write(".claude/settings.json", (Path(__file__).resolve().parents[1] / "settings.json")
                        .read_text(encoding="utf-8"))
        self.repo.write(".claude/settings.local.json", json.dumps({"disableAllHooks": True}))
        r, out = self.check()
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("disableAllHooks", out["reason"])


class StageGuardTests(RepoCase):
    """From test_acceptance_v24 ac_004: a stage whose files changed with no flow-agent hook call fails."""

    def close(self):
        return self.repo.run("close-stage", "1", "WP-01=done")

    def test_a_stage_written_with_no_hook_call_fails(self):
        self.repo.run("open-stage", "1")
        self.repo.write("src/a.py", "x = 1\n")
        r = self.close()
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn(NO_GUARD, r.stdout)

    def test_a_guarded_stage_closes(self):
        self.repo.run("open-stage", "1")
        self.hook(bash_payload("ls"), wp="WP-01")
        self.repo.write("src/a.py", "x = 1\n")
        r = self.close()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn(NO_GUARD, r.stdout)

    def test_the_gatekeeper_and_the_main_thread_do_not_guard(self):
        self.repo.run("open-stage", "1")
        self.hook(bash_payload("ls", agent_id="g1", agent_type="gatekeeper"))
        self.hook(write_payload("src/a.py"))
        self.repo.write("src/a.py", "x = 1\n")
        self.assertIn(NO_GUARD, self.close().stdout)

    def test_a_guard_from_before_the_stage_does_not_count(self):
        self.hook(bash_payload("ls"), wp="WP-01")
        time.sleep(0.05)  # the stage's start is a file time: it must come after the call
        self.repo.run("open-stage", "1")
        self.repo.write("src/a.py", "x = 1\n")
        self.assertIn(NO_GUARD, self.close().stdout)

    def test_a_stage_that_changed_nothing_needs_no_guard(self):
        self.repo.run("open-stage", "1")
        r = self.close()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn(NO_GUARD, r.stdout)


class GateMessageTests(RepoCase):
    """From test_acceptance_v21 ac_001 to ac_003: what the stop gate says, its cap, its reset and its summary line."""

    manifest = GATE_MANIFEST

    def report(self, wp="WP-01"):
        return self.repo.run("hook", stdin=json.dumps(finished_report(wp)), env={"AGENTIC_WP": wp})

    def test_a_denial_names_the_package_its_count_and_the_failing_line(self):
        self.repo.write("acc.py", acceptance_script([FAILED, "1 failed"], 1))
        r = self.report()
        self.assertEqual(r.returncode, 2, r.stderr)
        for word in ("WP-01", "denial 1 of 2", FAILED):
            self.assertIn(word, r.stderr)

    def test_a_criterion_shared_with_another_package_is_not_its_own_failure(self):
        shared = dict(GATE_MANIFEST, work_packages=[dict(wp, acs=["AC-001", "AC-003"])
                                                    for wp in GATE_MANIFEST["work_packages"]])
        repo = Repo(shared)
        self.addCleanup(repo.cleanup)
        repo.write("acc.py", acceptance_script(["FAILED acceptance/test_x.py::test_ac_003_x - assert"], 1))
        r = repo.run("hook", stdin=json.dumps(finished_report()), env={"AGENTIC_WP": "WP-01"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((repo.dir / ".claude/state/gate.json").exists() and
                         json.loads((repo.dir / ".claude/state/gate.json").read_text(encoding="utf-8") or "{}"))

    def test_the_cap_lets_the_third_report_through_and_open_stage_resets_it(self):
        self.repo.write("acc.py", acceptance_script([FAILED], 1))
        self.repo.write(".claude/state/gate.json", json.dumps({"WP-01": {"denials": 1, "last": []}}))
        r = self.report()
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("denial 2 of 2", r.stderr)
        self.assertEqual(self.report().returncode, 0, "after two denials the report goes through")
        self.assertEqual(self.gate()["WP-01"]["denials"], 2)
        self.assertEqual(self.repo.run("open-stage", "1").returncode, 0)
        self.assertNotIn("WP-01", self.gate())
        self.assertIn("denial 1 of 2", self.report().stderr)

    def test_close_stage_reports_the_denials_of_a_wave_only(self):
        self.repo.write("acc.py", acceptance_script([], 0))
        self.assertEqual(self.repo.run("open-stage", "1").returncode, 0)
        self.repo.write(".claude/state/gate.json", json.dumps({"WP-01": {"denials": 1, "last": []}}))
        r = self.repo.run("close-stage", "1", "WP-01=done", "WP-02=done")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertLess(r.stdout.index("GATE: WP-01=1 WP-02=0"), r.stdout.index("RESULT: OK"))
        self.assertEqual(self.repo.run("open-stage", "qa").returncode, 0)
        self.assertNotIn("GATE:", self.repo.run("close-stage", "qa", "QA=pass").stdout)



class ShortcutHookTests(RepoCase):
    """Design 0018 (issue 57): the hook blocks the shortcuts a write adds to code."""

    def write(self, rel, content, tool="Write", old=None):
        tinput = {"file_path": str(self.repo.dir / rel)}
        tinput.update({"old_string": old, "new_string": content} if tool == "Edit" else {"content": content})
        return self.repo.run("hook", stdin=json.dumps({"tool_name": tool, "tool_input": tinput}),
                             env={"AGENTIC_WP": "WP-01"})

    def test_a_swallowed_exception_is_blocked_with_the_way_out(self):
        r = self.write("src/a.py", "try:\n    x()\nexcept Exception:\n    pa" "ss\n")
        self.assertEqual(r.returncode, 2)
        self.assertIn("WP-01: an exception swallowed with pass in src/a.py", r.stderr)
        self.assertIn("BLOCKED", r.stderr)

    def test_an_edit_that_keeps_an_old_shortcut_passes_and_one_that_adds_it_does_not(self):
        self.repo.write("src/a.py", "x = 1\n# [DEB" "UG-1]\n")
        self.assertEqual(self.write("src/a.py", "x = 2", tool="Edit", old="x = 1").returncode, 0)
        r = self.write("src/a.py", "x = 1  # [DEB" "UG-2]", tool="Edit", old="x = 1")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_documentation_may_name_the_shortcuts(self):
        manifest = dict(MANIFEST, work_packages=[dict(MANIFEST["work_packages"][0], files=["docs/a.md", "src/a.py"])])
        repo = Repo(manifest)
        self.addCleanup(repo.cleanup)
        payload = {"tool_name": "Write", "tool_input": {"file_path": str(repo.dir / "docs/a.md"),
                                                        "content": "Never leave [DEB" "UG- logs.\n"}}
        self.assertEqual(repo.run("hook", stdin=json.dumps(payload), env={"AGENTIC_WP": "WP-01"}).returncode, 0)


class WorkflowSubagentTests(RepoCase):
    """Issue 68: the Workflow tool's subagents carry this type, in any workflow (verified in a real run, where
    2.11.0's claim rule blocked a workflow unrelated to the flow). Unclaimed, they are free like any other unknown
    type; once claimed, the flow's zone rules apply."""

    AGENT = {"agent_id": "w1", "agent_type": "workflow-subagent"}

    def call(self, payload):
        return self.repo.run("hook", stdin=json.dumps({**payload, **self.AGENT}))

    def test_without_a_claim_it_writes_and_runs_commands(self):
        for tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
            with self.subTest(tool=tool):
                r = self.call(write_payload(str(self.repo.dir / "notes/chapter-1.md"), tool=tool))
                self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.call(bash_payload("ls")).returncode, 0)

    def test_once_claimed_it_writes_only_its_zone(self):
        self.assertEqual(self.call(bash_payload("python3 .claude/bin/agentic.py claim WP-01")).returncode, 0)
        self.assertEqual(self.call(write_payload(str(self.repo.dir / "src/a.py"))).returncode, 0)
        self.assertEqual(self.call(write_payload(str(self.repo.dir / "src/b/x.py"))).returncode, 2)


class HookLauncherTests(RepoCase):
    """From test_acceptance_v210 ac_005: hook.py runs the live guard, whatever a 2.9 run left behind."""

    def test_a_leftover_frozen_copy_is_never_run(self):
        bin_dir = self.repo.dir / ".claude/bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        for f in Path(__file__).resolve().parent.glob("*.py"):
            if not f.name.startswith("test_"):
                shutil.copy(f, bin_dir / f.name)
        self.repo.write(".claude/state/guard/active.json", "{}")
        self.repo.write(".claude/state/guard/bin/agentic.py", "print('FROZEN')\n")
        r = subprocess.run([sys.executable, str(bin_dir / "hook.py")], cwd=self.repo.dir, capture_output=True,
                           text=True, input=json.dumps(write_payload("src/c.py")),
                           env=dict(self.repo.env, AGENTIC_WP="WP-01"), encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertNotIn("FROZEN", r.stdout + r.stderr)

if __name__ == "__main__":
    unittest.main()
