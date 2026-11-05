"""Tests of waiting.py, the hook that records the agents waiting for a permission or an answer.

Run: python3 -m unittest discover -s .claude/bin -p test_waiting.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import waiting  # noqa: E402

WAITING = Path(__file__).resolve().parent / "waiting.py"

NOW = 1_800_000_000.0


def request(agent_id="ag1", tool="Bash", tool_input=None, agent_type="implementer"):
    return {"hook_event_name": "PermissionRequest", "agent_id": agent_id, "agent_type": agent_type, "tool_name": tool,
            "tool_input": {"command": "npm test"} if tool_input is None else tool_input}


class StateDir(unittest.TestCase):
    def setUp(self):
        self.state = Path(tempfile.mkdtemp(prefix="waiting-unit-")).resolve() / "state"
        self.addCleanup(shutil.rmtree, self.state.parent, ignore_errors=True)


class HandleTests(StateDir):
    def test_a_request_is_recorded_with_its_claim(self):
        self.state.mkdir()
        (self.state / "claims.json").write_text(json.dumps({"ag1": "WP-02"}), encoding="utf-8")
        waiting.handle(request(), self.state, NOW)
        [w] = waiting.current(self.state)
        self.assertEqual((w["who"], w["agent_type"], w["tool"], w["detail"], w["since"], w["agent_id"]),
                         ("WP-02", "implementer", "Bash", "npm test", NOW, "ag1"))

    def test_without_claims_nothing_is_created_for_them(self):
        waiting.handle(request(), self.state, NOW)
        self.assertIsNone(waiting.current(self.state)[0]["who"])
        self.assertFalse((self.state / "claims.json").exists())

    def test_each_ending_removes_only_that_agent(self):
        for ending in waiting.ENDS:
            with self.subTest(ending=ending):
                waiting.handle(request("a"), self.state, NOW)
                waiting.handle(request("b"), self.state, NOW + 1)
                waiting.handle({"hook_event_name": ending, "agent_id": "a"}, self.state, NOW + 2)
                self.assertEqual([w["agent_id"] for w in waiting.current(self.state)], ["b"])
                waiting.handle({"hook_event_name": ending, "agent_id": "b"}, self.state, NOW + 3)

    def test_an_ending_with_no_file_creates_nothing(self):
        waiting.handle({"hook_event_name": "PostToolUse", "agent_id": "a"}, self.state, NOW)
        self.assertFalse(self.state.exists())

    def test_the_main_session_and_other_events_are_ignored(self):
        main = request()
        del main["agent_id"]
        waiting.handle(main, self.state, NOW)
        waiting.handle({"hook_event_name": "Notification", "agent_id": "a"}, self.state, NOW)
        self.assertEqual(waiting.current(self.state), [])

    def test_detail_is_one_short_line(self):
        long_cmd = "echo " + "x" * 300 + "\nsecond line"
        waiting.handle(request(tool_input={"command": long_cmd}), self.state, NOW)
        detail = waiting.current(self.state)[0]["detail"]
        self.assertEqual(len(detail), waiting.MAX_DETAIL + 1)
        self.assertTrue(detail.endswith("…"))
        self.assertNotIn("\n", detail)
        waiting.handle(request("b", tool_input="not an object"), self.state, NOW)
        self.assertEqual(waiting.current(self.state)[1]["detail"], "")

    def test_current_is_ordered_and_tolerates_a_corrupt_file(self):
        waiting.handle(request("z"), self.state, NOW)
        waiting.handle(request("a"), self.state, NOW)
        waiting.handle(request("m"), self.state, NOW - 5)
        self.assertEqual([w["agent_id"] for w in waiting.current(self.state)], ["m", "a", "z"])
        (self.state / waiting.WAITING_FILE).write_text("{broken", encoding="utf-8")
        self.assertEqual(waiting.current(self.state), [])

    def test_clear(self):
        waiting.clear(self.state)
        self.assertFalse(self.state.exists())
        waiting.handle(request(), self.state, NOW)
        waiting.clear(self.state)
        self.assertEqual(waiting.current(self.state), [])


class DescribeTests(unittest.TestCase):
    def test_lines(self):
        since = time.strftime("%H:%M", time.localtime(NOW))
        base = {"tool": "Bash", "detail": "npm test", "since": NOW}
        self.assertEqual(waiting.describe(dict(base, who="WP-02", agent_type="implementer"), NOW + 750),
                         f"WP-02 (implementer) needs your permission for Bash: npm test — waiting 12 min (since {since})")
        self.assertTrue(waiting.describe(dict(base, who=None, agent_type="qa", tool="AskUserQuestion"), NOW)
                        .startswith("qa agent has a question for you — waiting 0 min"))
        self.assertTrue(waiting.describe(dict(base, who=None, agent_type=None, detail=""), NOW)
                        .startswith("subagent needs your permission for Bash — waiting"))


class MainTests(StateDir):
    def run_hook(self, stdin):
        env = dict(os.environ, CLAUDE_PROJECT_DIR=str(self.state.parent))
        return subprocess.run([sys.executable, str(Path(waiting.__file__))], input=stdin, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=env)

    def test_never_blocks_and_never_prints_to_stdout(self):
        for stdin in ("not json", "[]", json.dumps(request())):
            with self.subTest(stdin=stdin[:10]):
                r = self.run_hook(stdin)
                self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertEqual(len(waiting.current(self.state.parent / ".claude" / "state")), 1)



class StatusTests(unittest.TestCase):
    """From test_acceptance_waiting ac_001, ac_003, ac_004 and ac_005: the real hook, then agentic.py status."""

    def setUp(self):
        from test_agentic import Repo
        self.repo = Repo()
        self.addCleanup(self.repo.cleanup)

    def hook(self, payload):
        return subprocess.run([sys.executable, str(WAITING)], cwd=self.repo.dir, input=json.dumps(payload),
                              capture_output=True, text=True, encoding="utf-8", errors="replace", env=self.repo.env)

    def claim(self, agent_id, ident):
        self.repo.write(".claude/state/claims.json", json.dumps({agent_id: ident}))

    def section(self):
        out = self.repo.run("status").stdout
        if "## Waiting for you" not in out:
            return out, None
        body = out.split("## Waiting for you", 1)[1].split("\n## ", 1)[0]
        return out, [line for line in body.splitlines() if line.startswith("- ")]

    def test_a_waiting_agent_is_listed_before_the_stages_until_it_moves_on(self):
        self.claim("ag1", "WP-01")
        r = self.hook(request("ag1", tool_input={"command": "npm run e2e"}))
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        out, lines = self.section()
        self.assertLess(out.index("## Waiting for you"), out.index("## Stages"))
        self.assertRegex(lines[0], r"^- WP-01 \(implementer\) needs your permission for Bash: npm run e2e — "
                                   r"waiting 0 min \(since \d\d:\d\d\)$")
        self.hook({"hook_event_name": "PostToolUse", "agent_id": "ag1", "tool_name": "Bash"})
        self.assertIsNone(self.section()[1])

    def test_a_url_is_the_detail_of_a_web_request(self):
        self.hook(request("x1", tool="WebFetch", tool_input={"url": "https://example.com"}))
        self.assertTrue(any("WebFetch: https://example.com" in line for line in self.section()[1]))

    def test_a_new_stage_clears_the_list(self):
        self.claim("ag1", "WP-01")
        self.hook(request("ag1"))
        self.assertIsNotNone(self.section()[1])
        self.assertEqual(self.repo.run("snapshot", "1").returncode, 0)
        self.assertIsNone(self.section()[1])

    def test_the_hook_imports_nothing_of_the_flow(self):
        import ast
        tree = ast.parse(WAITING.read_text(encoding="utf-8"))
        imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        imported |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        self.assertFalse(imported & {"agentic", "pipeline", "brief", "upgrade", "core"}, imported)

if __name__ == "__main__":
    unittest.main()
