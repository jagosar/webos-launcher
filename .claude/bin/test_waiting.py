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


if __name__ == "__main__":
    unittest.main()
