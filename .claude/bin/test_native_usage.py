"""The native workflow's usage and rules: the usage of a native run, read from Claude Code's own files; save-result
--run; a result saved even without them; a native plan leaves the common rules to Claude Code and the fallback keeps
them printed; /implement. Moved as they were from test_acceptance_v220.py (design 0027, agentic-flow 2.20.0) when
2.21.0 retired it (design 0029); the release checks stayed behind.

Run: python3 -m unittest discover -s .claude/bin -p test_native_usage.py
Claude Code's files are built in a temporary config folder (Annex A of design 0027). Nothing is spent.
"""
import importlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parent
ROOT = BIN.parents[1]
CLAUDE = ROOT / ".claude"
sys.path.insert(0, str(BIN))
from test_agentic import MANIFEST, Repo, testutil  # noqa: E402

RUN = "wf_test-001"
RUN_DIR = f"projects/p/s/subagents/workflows/{RUN}"
RECORD = {"runId": RUN, "status": "completed", "workflowProgress": [
    {"type": "workflow_phase", "index": 1, "title": "Wave 0"},
    {"type": "workflow_agent", "agentId": "a1", "agentType": "test-author", "label": "test-author",
     "model": "claude-sonnet-5-5", "durationMs": 91234},
    {"type": "workflow_agent", "agentId": "a2", "agentType": "implementer", "label": "WP-02 (fix)",
     "model": "claude-sonnet-5-5", "durationMs": 40000}]}


def usage(new, created, read, out):
    return {"input_tokens": new, "cache_creation_input_tokens": created, "cache_read_input_tokens": read,
            "output_tokens": out}


MSG_1 = {"type": "assistant", "message": {"id": "msg_1", "content": "ANSWER-TEXT", "usage": usage(3, 1200, 45000, 800)}}
A1 = [{"type": "user", "message": {"role": "user", "content": "PROMPT-TEXT"}}, MSG_1, MSG_1]
A2 = [{"type": "assistant", "message": {"id": "msg_2", "usage": usage(5, 300, 4000, 100)}},
      {"type": "attachment", "attachment": {"type": "instructions"}},
      {"type": "assistant", "message": {"id": "msg_3", "usage": usage(5, 200, 5000, 50)}}]
AT = {"id": "AT", "agent": "test-author", "label": "test-author", "model": "claude-sonnet-5-5", "input": 1203,
      "cache_read": 45000, "output": 800, "turns": 1, "cost_usd": None}
WP2 = {"id": "WP-02", "agent": "implementer", "label": "WP-02 (fix)", "model": "claude-sonnet-5-5", "input": 510,
       "cache_read": 9000, "output": 150, "turns": 2, "cost_usd": None}
TOTAL = {"input": 1713, "cache_read": 54000, "output": 950, "turns": 3, "cost_usd": None}
COMMON_RULE = "No speculative abstractions"
PRINTED = "\nRules you must follow (common and language). They are printed here, so do not read them from disk:"
POINTED = ("\nThe common rules are already in your context, as project instructions "
           "(.claude/rules/agentic/common.md): follow them.")


def jsonl(items):
    return "".join(json.dumps(i) + "\n" for i in items)


def temp_dir(test, prefix):
    d = Path(tempfile.mkdtemp(prefix=prefix))
    test.addCleanup(shutil.rmtree, d, ignore_errors=True)
    return d


def config_dir(test, record=RECORD, a1=jsonl(A1), a2=jsonl(A2)):
    """A Claude Code config folder with one workflow run: its record and its agents' transcripts (None: no file)."""
    d = temp_dir(test, "v220-config-")
    files = {f"projects/p/s/workflows/{RUN}.json": record if isinstance(record, str) else json.dumps(record),
             f"{RUN_DIR}/agent-a1.jsonl": a1, f"{RUN_DIR}/agent-a2.jsonl": a2}
    for rel, text in files.items():
        if text is not None:
            (d / rel).parent.mkdir(parents=True, exist_ok=True)
            (d / rel).write_text(text, encoding="utf-8")
    return d


def module(name):
    """The module under test; a missing one fails the test that needs it, not the whole file."""
    return importlib.import_module(name)


class UsageOfARunTests(unittest.TestCase):
    def test_ac_001_the_usage_is_read_from_claude_codes_files(self):
        found, reason = module("workflow_usage").usage_for(RUN, config_dir(self))
        self.assertEqual(reason, "")
        self.assertEqual(found["by_agent"], [AT, WP2])
        self.assertEqual(found["total"], TOTAL)
        self.assertEqual((found["source"], found["missing"]), ("claude-code", 0))
        text = json.dumps(found)
        self.assertNotIn("ANSWER-TEXT", text)
        self.assertNotIn("PROMPT-TEXT", text)


class SaveResultTests(unittest.TestCase):
    def setUp(self):
        self.repo = Repo(MANIFEST)
        self.addCleanup(self.repo.cleanup)

    def save(self, config, *args, result=None):
        self.repo.write(".agentic-result.json", json.dumps(result or {"result": "OK"}))
        r = self.repo.run("save-result", ".agentic-result.json", *args, env={"CLAUDE_CONFIG_DIR": str(config)})
        saved = self.repo.dir / ".claude/state/runs/workflow-result.json"
        return r, (json.loads(saved.read_text(encoding="utf-8")) if saved.exists() else None)

    def test_ac_002_save_result_adds_the_usage(self):
        config = config_dir(self)
        r, saved = self.save(config, "--run", RUN)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((saved["usage"]["by_agent"], saved["usage"]["total"]), ([AT, WP2], TOTAL))
        self.assertFalse((self.repo.dir / ".agentic-result.json").exists())
        self.assertTrue(r.stdout.strip().endswith(" · usage: 2 agent(s)"), r.stdout)
        own = {"total": {}, "by_agent": []}
        r, saved = self.save(config, "--run", RUN, result={"result": "OK", "usage": own})
        self.assertEqual(saved["usage"], own)
        self.assertTrue(r.stdout.strip().endswith(" · usage: kept"), r.stdout)
        r, saved = self.save(config)
        self.assertNotIn("usage", saved)
        self.assertNotIn("usage", r.stdout)
        r, saved = self.save(config, "--run", RUN, result={"result": "OK"})  # the option may come first, too
        self.repo.write(".agentic-result.json", '{"result": "OK"}')
        r = self.repo.run("save-result", "--run", RUN, ".agentic-result.json", env={"CLAUDE_CONFIG_DIR": str(config)})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_ac_002_a_wrong_command_line_is_refused(self):
        for args in (["--run"], ["--run", "a", "--run", "b"], ["extra", "--run", RUN]):
            with self.subTest(args=args):
                self.repo.write(".agentic-result.json", '{"result": "OK"}')
                r = self.repo.run("save-result", ".agentic-result.json", *args)
                self.assertEqual(r.returncode, 1)
                self.assertIn("Uso: save-result [FILE] [--run RUN_ID]", r.stderr)

    def not_recorded(self, config, run=RUN):
        r, saved = self.save(config, "--run", run)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(saved["result"], "OK")
        self.assertNotIn("usage", saved)
        self.assertIn(" · usage: not recorded (", r.stdout)

    def test_ac_003_without_the_files_the_result_is_still_saved(self):
        self.not_recorded(config_dir(self), "wf_none-000")
        self.not_recorded(config_dir(self), "../../x")
        self.not_recorded(config_dir(self, record="[1]"))
        self.not_recorded(config_dir(self, record="{not json"))

    def test_ac_003_a_missing_or_damaged_transcript_does_not_cost_the_rest(self):
        r, saved = self.save(config_dir(self, a2=None), "--run", RUN)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((saved["usage"]["by_agent"], saved["usage"]["missing"]), ([AT], 1))
        self.assertTrue(r.stdout.strip().endswith(" · usage: 1 agent(s), 1 without a transcript"), r.stdout)
        damaged = jsonl(A2[:1]) + "{not json\n" + jsonl(A2[1:])
        r, saved = self.save(config_dir(self, a2=damaged), "--run", RUN)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(saved["usage"]["total"], TOTAL)
        self.assertTrue(r.stdout.strip().endswith(" · usage: 2 agent(s)"), r.stdout)

    def test_ac_003_an_agent_id_cannot_name_a_file_outside_the_run(self):
        record = json.loads(json.dumps(RECORD))
        record["workflowProgress"][2]["agentId"] = "../../../../outside"
        config = config_dir(self, record=record, a2=None)
        (config / RUN_DIR / "agent-..").mkdir()
        outside = config / "projects/p/s/subagents/outside.jsonl"
        outside.write_text(jsonl([{"type": "assistant", "message": {"id": "m", "usage": usage(1, 1, 1, 777)}}]),
                           encoding="utf-8")
        self.assertTrue((config / RUN_DIR / "agent-../../../../outside.jsonl").exists(), "the path would resolve")
        r, saved = self.save(config, "--run", RUN)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((saved["usage"]["by_agent"], saved["usage"]["missing"]), ([AT], 1))
        self.assertNotIn("777", json.dumps(saved))


class RulesRepo:
    """A temporary project with the python stack's rules installed; `env` runs it as a Claude Code session would."""

    def __init__(self, test, manifest=None):
        self.repo = Repo(dict(manifest or MANIFEST, stack=["python"]))
        test.addCleanup(self.repo.cleanup)
        shutil.copytree(CLAUDE / "rules-library", self.repo.dir / ".claude/rules-library")
        test.assertEqual(self.repo.run("rules-sync").returncode, 0)
        self.repo.env.pop("CLAUDE_CODE_DISABLE_CLAUDE_MDS", None)  # the suite may run where it is set

    def plan(self, **env):
        return json.loads(self.repo.run("pipeline-plan", env=env).stdout.splitlines()[-1])


class NativeRulesTests(unittest.TestCase):
    def test_ac_004_a_native_plan_leaves_the_common_rules_to_claude_code(self):
        import findings
        project = RulesRepo(self)
        native = project.plan(CLAUDECODE="1")
        text = native["rules_text"]
        self.assertTrue(text.startswith(POINTED), text[:200])
        self.assertIn("--- .claude/rules/agentic/python.md ---", text)
        self.assertIn("PEP 8", text)
        self.assertNotIn("--- .claude/rules/agentic/common.md ---", text)
        self.assertNotIn(COMMON_RULE, text)
        self.assertNotIn("paths:", text)
        self.assertEqual(native["rules_sum"], findings.text_sum(text))
        self.assertEqual(native["rules"], [".claude/rules/agentic/common.md", ".claude/rules/agentic/python.md"])
        for env in ({}, {"CLAUDECODE": "1", "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1"}):
            with self.subTest(env=env):
                whole = project.plan(**env)["rules_text"]
                self.assertTrue(whole.startswith(PRINTED), whole[:200])
                self.assertIn("--- .claude/rules/agentic/common.md ---", whole)
                self.assertIn(COMMON_RULE, whole)
                self.assertIn("--- .claude/rules/agentic/python.md ---", whole)
                self.assertNotIn("already in your context", whole)
                self.assertNotIn("paths:", whole)

    def first_implementer_prompt(self, project, logs, command):
        r = project.repo.run(*command, env={"CLAUDECODE": "1"})
        calls = [c for c in testutil.fake_calls(logs) if c["agent"] == "implementer"]
        self.assertTrue(calls, r.stdout + r.stderr)
        return calls[0]["prompt"]

    def test_ac_005_the_fallback_keeps_the_whole_rules_even_from_a_session(self):
        from test_pipeline import E2E_MANIFEST
        for command in (["run-wave", "1"], ["run-pipeline"]):
            with self.subTest(command=command):
                project = RulesRepo(self, E2E_MANIFEST)
                logs, bindir = temp_dir(self, "v220-logs-"), temp_dir(self, "v220-claude-")
                project.repo.env.update(AGENTIC_CLAUDE=testutil.install_fake_claude(bindir), FAKE_LOG_DIR=str(logs),
                                        FAKE_WRITE="1", FAKE_MODE="nousage")
                project.repo.write("acc.py", "import os, sys\nsys.exit(0 if all(os.path.exists(p) for p in "
                                             "('src/a.py', 'src/b/x.py', 'src/c.py')) else 1)\n")
                git = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
                subprocess.run(["git", "add", "-A"], cwd=project.repo.dir, check=True)
                subprocess.run([*git, "commit", "-qm", "init"], cwd=project.repo.dir, check=True)
                prompt = self.first_implementer_prompt(project, logs, command)
                # the rules travel, whole, at the end of the brief (design 0032); the prompt names the brief
                brief = (project.repo.dir / ".claude/state/briefs/WP-01.md").read_text(encoding="utf-8")
                self.assertIn(COMMON_RULE, brief)
                self.assertIn(PRINTED.strip(), brief)
                self.assertNotIn("already in your context", brief)
                self.assertNotIn(PRINTED.strip(), prompt)


class DefinitionTests(unittest.TestCase):
    def test_ac_006_implement_passes_the_run_id_and_presents_the_usage(self):
        text = (CLAUDE / "commands/implement.md").read_text(encoding="utf-8")
        self.assertIn("save-result .agentic-result.json --run", text)
        self.assertIn("`runId`", text)
        usage_lines = [line for line in text.splitlines() if line.startswith("- **Usage")]
        self.assertEqual(len(usage_lines), 1)
        self.assertIn("usage.by_agent", usage_lines[0])
        self.assertNotIn("save-result .agentic-result.json`", text, "the step without --run")
        rules_step = next(line for line in text.splitlines() if "agentic.py rules-sync" in line)
        self.assertIn("Claude Code", rules_step)


if __name__ == "__main__":
    unittest.main()
