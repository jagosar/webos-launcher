"""Tests of workflow_usage.py beyond the acceptance criteria of design 0027: the odd shapes a file the flow did not
write can have. Everything is built in temporary folders.

Run: python3 -m unittest discover -s .claude/bin -p test_workflow_usage.py
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import workflow_usage  # noqa: E402

RUN = "wf_odd-1"


def run_files(root, agents, transcripts):
    """Writes the record of RUN and its transcripts ({agentId: [line objects or raw strings]}); returns the record."""
    session = Path(root) / "projects/p/s"
    record = session / "workflows" / f"{RUN}.json"
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps({"workflowProgress": agents}), encoding="utf-8")
    folder = session / "subagents/workflows" / RUN
    folder.mkdir(parents=True)
    for agent_id, lines in transcripts.items():
        text = "".join((line if isinstance(line, str) else json.dumps(line)) + "\n" for line in lines)
        (folder / f"agent-{agent_id}.jsonl").write_text(text, encoding="utf-8")
    return record


def agent(agent_id, **more):
    return {"type": "workflow_agent", "agentId": agent_id, **more}


def message(ident, **used):
    return {"message": {"id": ident, "usage": used}}


class OddShapesTests(unittest.TestCase):
    def usage(self, agents, transcripts):
        with tempfile.TemporaryDirectory() as d:
            return workflow_usage.usage_of_run(run_files(d, agents, transcripts))

    def test_values_that_are_not_whole_numbers_count_as_zero(self):
        found = self.usage([agent("a", agentType="qa", label="qa", model="m")],
                           {"a": [message("m1", input_tokens="9", output_tokens=True, cache_read_input_tokens=None,
                                          cache_creation_input_tokens=2.5),
                                  message("m2", input_tokens=4, output_tokens=6)]})
        self.assertEqual(found["by_agent"], [{"id": "QA", "agent": "qa", "label": "qa", "model": "m", "input": 4,
                                              "cache_read": 0, "output": 6, "turns": 2, "cost_usd": None}])

    def test_lines_without_an_id_or_a_usage_are_skipped(self):
        found = self.usage([agent("a", agentType="reviewer", label="reviewer")],
                           {"a": ["", "[1, 2]", '"text"', {"message": "text"},
                                  {"message": {"usage": {"output_tokens": 5}}},
                                  {"message": {"id": 7, "usage": {"output_tokens": 5}}},
                                  {"message": {"id": "m", "usage": "none"}}, message("ok", output_tokens=3)]})
        record = found["by_agent"][0]
        self.assertEqual((record["output"], record["turns"], record["id"], record["model"]), (3, 1, "REVIEW", None))

    def test_a_label_or_type_that_is_not_text_becomes_empty(self):
        found = self.usage([agent("a", agentType=5, label=None), agent("b", agentType="doc-updater", label=["x"])],
                           {"a": [message("m", output_tokens=1)], "b": [message("m", output_tokens=2)]})
        self.assertEqual([(r["id"], r["agent"], r["label"]) for r in found["by_agent"]],
                         [("REVIEW", "", ""), ("DOCS", "doc-updater", "")])

    def test_entries_that_are_not_agents_and_ids_that_are_not_names_are_left_out(self):
        found = self.usage(["text", {"type": "workflow_phase"}, agent(5), agent("a b"), agent(""), agent("ok")],
                           {"ok": [message("m", output_tokens=1)]})
        self.assertEqual((len(found["by_agent"]), found["missing"]), (1, 3))

    def test_a_record_without_agents_is_no_usage(self):
        for progress in ([], "text", None, [{"type": "workflow_phase"}]):
            with self.subTest(progress=progress):
                self.assertIsNone(self.usage(progress, {}))

    def test_the_newest_record_wins_and_a_wrong_id_finds_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            record = run_files(d, [agent("a")], {})
            self.assertEqual(workflow_usage.find_run(RUN, d), record)
            for wrong in ("", "wf_", "wf_a/../b", "*", None, 5):
                self.assertIsNone(workflow_usage.find_run(wrong, d), wrong)
            self.assertIsNone(workflow_usage.find_run(RUN, Path(d) / "nowhere"))


if __name__ == "__main__":
    unittest.main()
