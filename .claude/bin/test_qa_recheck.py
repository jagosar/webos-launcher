"""QA after a fix and the rules per agent: QA re-checks only what a fix touched; a Standards-only fix keeps QA's
report; the rules are printed only where they are used; both engines stay equivalent; the definitions. Moved as they
were from test_acceptance_v219.py (design 0026, agentic-flow 2.19.0) when 2.20.0 retired it (design 0027); the release
checks stayed behind.

Run: python3 -m unittest discover -s .claude/bin -p test_qa_recheck.py
The engine runs on scripted gates and agents (test_pipeline.world), with scenarios declared here. Nothing is spent.
"""
import json
import sys
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parent
ROOT = BIN.parents[1]
CLAUDE = ROOT / ".claude"
sys.path.insert(0, str(BIN))
import pipeline  # noqa: E402
import test_pipeline  # noqa: E402

RULES_PLAN = {"rules": [".claude/rules/agentic/common.md"], "rules_sum": 4823,
              "rules_text": "\nRules you must follow (common and language). RULE-TEXT"}
QA_DEFECT = {"id": "D1", "severity": "high", "wp": "WP-02", "kind": "implementation", "section": "6",
             "repro": "QA-REPRO"}
QA_FAIL = {"status": "FAIL", "plan": [], "defects": [QA_DEFECT], "gaps": []}
QA_PASS = {"status": "PASS", "plan": [], "defects": [], "gaps": []}
LOW_DEFECT = {"id": "D2", "severity": "low", "wp": "WP-03", "kind": "implementation", "section": "6", "repro": "LOW"}
QA_PASS_WITH_LOW = {"status": "PASS", "plan": [], "defects": [LOW_DEFECT], "gaps": []}
REFLEX = {"status": "FAIL", "axis": "standards", "not_checked": [],
          "findings": [{"wp": "WP-02", "file": "src/b/x.py", "line": 3, "problem": "STANDARDS-PROBLEM", "evidence": "e",
                        "provenance": "read", "tier": "reflex", "rule": "No magic numbers"}]}
SPEC_FINDING = {"status": "FAIL", "needs_validation": [], "not_checked": [],
                "findings": [{"tier": "floor", "severity": "high", "provenance": "read", "wp": "WP-02",
                              "file": "src/b/x.py", "line": 3, "problem": "SPEC-PROBLEM", "evidence": "e", "fix": "f",
                              "if_skipped": "s"}]}
WP01_FAILS = {"exit_code": 3, "output": {
    "ok": False, "blocking": ["tests"], "preexisting_failures": [],
    "suspects": {"WP-01": {"phases": ["tests"], "files": ["src/a.py"], "acs": ["AC-001"]}},
    "phases": [{"name": "tests", "passed": False, "status": "fails", "tail": ["FAILED src/a.py test_ac_001"]}]}}
NEW_SCENARIOS = ("QA re-checks only what a fix touched", "a standards-only fix keeps the QA report",
                 "a verification fix is re-checked by QA", "a kept QA report is covered by the next re-check")


def run(decl):
    """(the run's record, the scripted world's state) of one scenario declared here."""
    state, gate, agent = test_pipeline.world(decl)
    engine = pipeline.Pipeline(test_pipeline.SPEC, gate, agent, parallel=lambda thunks: [t() for t in thunks])
    return engine.execute(), state


def calls_of(state, agent_type):
    """[(label, prompt, options)] of the agent's calls, in order."""
    return [(label, prompt, opts) for kind, label, prompt, opts in state["calls"] if kind == agent_type]


def modes(result):
    return [p["mode"] for p in result.get("qa_passes") or []]


class QaAfterAFixTests(unittest.TestCase):
    def test_ac_001_qa_rechecks_only_what_a_fix_touched(self):
        result, state = run({"plan": RULES_PLAN, "qa": [QA_FAIL, QA_PASS]})
        qa = calls_of(state, "qa")
        self.assertEqual(len(qa), 2)
        (label1, first, opts1), (label2, second, opts2) = qa
        self.assertEqual(label1, "qa")
        self.assertTrue(first.startswith("Run the system's QA validation"), first)
        self.assertFalse(opts1.get("resume"))
        self.assertEqual(label2, "qa (re-check)")
        self.assertTrue(second.startswith("Re-check the system after a fix"), second)
        self.assertIn("Packages fixed since your last pass: WP-02.", second)
        self.assertIn(".claude/state/runs/WP-02.fix-1.diff", second)
        self.assertIn(json.dumps([QA_DEFECT], separators=(",", ":")), second)
        self.assertTrue(second.endswith("RULE-TEXT"), second[-80:])
        self.assertIs(opts2.get("resume"), True)
        for other in ("WP-01.fix", "WP-03.fix"):
            self.assertNotIn(other, second)
        self.assertEqual(modes(result), ["full", "scoped"])
        self.assertEqual(result["qa"], QA_PASS)
        self.assertEqual(result["qa_passes"][0], {"mode": "full", "status": "FAIL", "defects": 1})

    def test_ac_001_a_second_recheck_names_only_the_fixes_since_the_first(self):
        result, state = run({"qa": [QA_FAIL, QA_FAIL, QA_PASS]})
        self.assertEqual(modes(result), ["full", "scoped", "scoped"])
        third = calls_of(state, "qa")[2][1]
        self.assertIn(".claude/state/runs/WP-02.fix-2.diff", third)
        self.assertNotIn("WP-02.fix-1.diff", third, "QA already saw the first fix")

    def test_ac_002_a_standards_only_fix_keeps_the_report(self):
        result, state = run({"standards": [REFLEX], "qa": [QA_PASS_WITH_LOW, QA_FAIL]})
        self.assertEqual(len(calls_of(state, "qa")), 1, "QA is not launched again")
        self.assertEqual(result["qa"], QA_PASS_WITH_LOW)
        self.assertEqual(modes(result), ["full", "kept"])
        self.assertEqual(result["qa_passes"][1], {"mode": "kept", "status": "PASS", "defects": 1})
        self.assertEqual((result["result"], result["fix_cycles"]), ("OK", 1))
        closes = [g for g in state["gates"] if g.startswith("close-stage qa")]
        self.assertEqual(closes, ["close-stage qa QA=pass", "close-stage qa QA=pass"], "the stage still closes")
        self.assertEqual(len([g for g in state["gates"] if g == "open-stage qa"]), 2)

    def test_ac_003_a_verification_fix_is_rechecked(self):
        result, state = run({"qa": [QA_FAIL, QA_PASS], "verify": [None, WP01_FAILS, None]})
        self.assertEqual(modes(result), ["full", "scoped"])
        second = calls_of(state, "qa")[1][1]
        self.assertIn("Packages fixed since your last pass: WP-01, WP-02.", second)
        self.assertIn(".claude/state/runs/WP-01.fix-1.diff, .claude/state/runs/WP-02.fix-1.diff", second)

    def test_ac_003_a_kept_report_is_covered_by_the_next_recheck(self):
        verdicts = [{"verdict": "NOT_ADDRESSED"}, {"verdict": "ADDRESSED"}]
        result, state = run({"standards": [REFLEX], "verify": [None, None, WP01_FAILS, None],
                             "re_review": {"standards-reviewer": verdicts}})
        self.assertEqual(modes(result), ["full", "kept", "scoped"])
        qa = calls_of(state, "qa")
        self.assertEqual(len(qa), 2)
        self.assertIn("Packages fixed since your last pass: WP-01, WP-02.", qa[1][1])
        self.assertIn(".claude/state/runs/WP-01.fix-1.diff, .claude/state/runs/WP-02.fix-1.diff, "
                      ".claude/state/runs/WP-02.fix-2.diff", qa[1][1])
        self.assertEqual(result["fix_cycles"], 3)


class RulesTests(unittest.TestCase):
    def test_ac_004_the_rules_are_printed_only_where_they_are_used(self):
        result, state = run({"plan": dict(RULES_PLAN, docs=True), "review": [SPEC_FINDING], "standards": [REFLEX]})
        self.assertEqual(result["fix_cycles"], 1)
        labels = {label for _, label, _, _ in state["calls"]}
        for label in ("reviewer (re-review)", "standards-reviewer (re-review)", "qa (re-check)", "doc-updater",
                      "test-auditor"):
            self.assertIn(label, labels)
        for agent in ("implementer", "test-author", "standards-reviewer", "qa"):
            prompts = [p for _, p, _ in calls_of(state, agent)]
            self.assertTrue(prompts and all("RULE-TEXT" in p for p in prompts), agent)
        for agent in ("test-auditor", "reviewer", "doc-updater", "verifier"):
            prompts = [p for _, p, _ in calls_of(state, agent)]
            self.assertTrue(prompts, agent)
            self.assertEqual([p for p in prompts if "RULE-TEXT" in p or "Rules you must follow" in p], [], agent)


class EnginesTests(unittest.TestCase):
    def test_ac_005_both_engines_run_the_new_scenarios(self):
        names = {s["name"] for s in test_pipeline.CONFORMANCE["scenarios"]}
        for name in NEW_SCENARIOS:
            self.assertIn(name, names)
        compared = (BIN / "test_pipeline.py").read_text(encoding="utf-8").split("keys = (")[1].split(")")[0]
        self.assertIn('"qa_passes"', compared)
        self.assertIn("qa_passes", (BIN / "test_workflow.mjs").read_text(encoding="utf-8"))
        workflow = (CLAUDE / "workflows/agentic-implement.js").read_text(encoding="utf-8")
        self.assertIn("qa (re-check)", workflow)
        self.assertIn("Re-check the system after a fix", workflow)
        summary = test_pipeline.run_scenario(next(s for s in test_pipeline.CONFORMANCE["scenarios"]
                                                  if s["name"] == NEW_SCENARIOS[1]))
        self.assertEqual(summary["qa_passes"], ["full", "kept"])


class DefinitionTests(unittest.TestCase):
    def read(self, rel):
        return (CLAUDE / rel).read_text(encoding="utf-8")

    def test_ac_006_the_definitions_say_what_the_prompts_do(self):
        qa = self.read("agents/qa.md")
        recheck = qa.split("\n# Re-check\n")[1].split("\n# ")[0]
        self.assertIn("diffs", recheck)
        self.assertIn("every defect that is still open", recheck)
        self.assertNotIn("rules printed in your prompt", self.read("agents/reviewer.md"))
        docs = self.read("agents/doc-updater.md")
        self.assertNotIn("check that all its relative links", docs)
        self.assertNotIn("Create it if it is in your zone", docs)
        self.assertIn("only if it is in your zone", docs)
        self.assertIn("qa_passes", self.read("commands/implement.md"))
        for name in ("qa.md", "implementer.md", "test-author.md"):
            self.assertIn("the rules printed in your prompt", self.read("agents/" + name), name)


if __name__ == "__main__":
    unittest.main()
