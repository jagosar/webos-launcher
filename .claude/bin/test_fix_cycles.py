"""Fix cycles: escalation on the last attempt, the fallback's resumed attempts, the scoped re-review by fingerprint,
the budget in agent calls and the advisor experiment. Moved as they were from test_acceptance_v214.py (design 0021,
agentic-flow 2.14.0) when 2.15.0 retired it (design 0022); the release checks stayed behind.

Run: python3 -m unittest discover -s .claude/bin -p test_fix_cycles.py
Engines run on conformance scenarios with simulated agents; commands run in temporary projects with a fake `claude`.
Nothing is spent.
"""
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parent
ROOT = BIN.parents[1]
sys.path.insert(0, str(BIN))
import core  # noqa: E402
from test_agentic import Repo, testutil  # noqa: E402

# The advisor variant and the bench live only in the flow's own repo: an installed project has no bench/.
FLOW_REPO_ONLY = unittest.skipUnless(core.is_flow_repo(ROOT), "the flow's own repo")

CONFORMANCE = json.loads((BIN / "conformance.json").read_text(encoding="utf-8"))
SPEC = json.loads((ROOT / ".claude/workflows/pipeline-spec.json").read_text(encoding="utf-8"))
FINGERPRINT_RE = re.compile(r"^F[0-9a-f]{8}$")


def decl_of(name):
    return next(d for d in CONFORMANCE["scenarios"] if d["name"] == name)


def scenario(decl):
    import test_pipeline
    return test_pipeline.run_scenario(decl_of(decl) if isinstance(decl, str) else decl)


def node_agrees(test):
    if not shutil.which("node"):
        test.skipTest("node is not installed")
    r = subprocess.run(["node", str(BIN / "test_workflow.mjs")], capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    test.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-3000:])


def prompts_of(summary, key):
    return [p for k, p in summary["prompts"].items() if k.startswith(key + "#")]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def temp_dir(test, prefix):
    d = Path(tempfile.mkdtemp(prefix=prefix))
    test.addCleanup(shutil.rmtree, d, ignore_errors=True)
    return d


RE_SPEC = "reviewer:reviewer (re-review)"
RE_STANDARDS = "standards-reviewer:standards-reviewer (re-review)"


class FingerprintTests(unittest.TestCase):
    def test_ac_001_fingerprints(self):
        import findings
        first = {"axis": "spec", "tier": "floor", "file": "src/a.py", "problem": "  Off by ONE in total  "}
        same = dict(first, problem="off by one in total", line=7, evidence="another text")
        other = dict(first, file="src/b.py")
        fp = findings.fingerprint(first)
        self.assertRegex(fp, FINGERPRINT_RE)
        self.assertEqual(findings.fingerprint(same), fp)
        self.assertEqual(findings.fingerprint(dict(reversed(list(first.items())))), fp, "dict order is irrelevant")
        self.assertNotEqual(findings.fingerprint(other), fp)
        listed = findings.with_fingerprints([first, same, other])
        self.assertEqual([f["fingerprint"] for f in listed], [fp, fp + "-2", findings.fingerprint(other)])
        self.assertNotIn("fingerprint", first, "copies, not the findings given")
        decl = decl_of("a fix is re-reviewed on its open findings only")
        expected = findings.fingerprint(dict(decl["review"][0]["findings"][0], axis="spec"))
        s = scenario(decl)
        self.assertIn(expected, prompts_of(s, RE_SPEC)[0])
        node_agrees(self)


class ReReviewTests(unittest.TestCase):
    def test_ac_002_scoped(self):
        s = scenario("a fix is re-reviewed on its open findings only")
        self.assertEqual((s["result"], s["fix_cycles"]), ("OK", 1))
        self.assertEqual(s["agents"].count(RE_SPEC), 1)
        self.assertNotIn(RE_STANDARDS, s["agents"])
        self.assertEqual(s["agents"].count("reviewer:reviewer"), 1, "no second full review")
        self.assertEqual(s["agents"].count("standards-reviewer:standards-reviewer"), 1)
        self.assertEqual(len([a for a in s["agents"] if a.startswith("verifier:")]), 1, "the re-review is not verified")
        self.assertIn(".claude/state/runs/WP-01.fix-1.diff", prompts_of(s, RE_SPEC)[0])
        self.assertEqual(s["re_review"], {"spec": [1, 0, 0], "standards": [0, 0, 0]})
        node_agrees(self)

    def test_ac_003_verdicts(self):
        import findings
        for name in ("a finding not addressed is fixed again", "a missing verdict counts as not addressed"):
            with self.subTest(name=name):
                s = scenario(name)
                self.assertEqual((s["result"], s["fix_cycles"]), ("REVIEW", 2))
                self.assertIn("fix limit reached", s["pending"])
                self.assertEqual(len([a for a in s["agents"] if a == "implementer:WP-01 (fix)"]), 2)
        new = scenario("a new finding in a re-review goes to pending")
        self.assertEqual((new["result"], new["fix_cycles"]), ("REVIEW", 1), "a new finding never starts a cycle")
        self.assertIn("new finding", new["pending"])
        self.assertEqual(new["re_review"]["spec"][2], 1)
        decl = decl_of("a finding fixed before a verification failure is still re-reviewed")
        late = scenario(decl)
        prompt = prompts_of(late, RE_SPEC)[0]
        self.assertIn(findings.fingerprint(dict(decl["review"][0]["findings"][0], axis="spec")), prompt)
        self.assertIn(".claude/state/runs/WP-01.fix-1.diff, .claude/state/runs/WP-02.fix-1.diff", prompt)
        outside = scenario("a finding outside every package survives the re-review")
        self.assertEqual(outside["result"], "REVIEW")
        self.assertIn("no WP", outside["pending"])
        self.assertNotIn("WP-99", prompts_of(outside, RE_SPEC)[0], "only the fixed package's findings are open")
        node_agrees(self)


class EscalationTests(unittest.TestCase):
    def test_ac_004_escalation(self):
        up = scenario("the last fix attempt escalates")
        self.assertEqual(up["models"], ["implementer:WP-03 (fix)#2=opus"])
        flat = scenario("without an escalation model no attempt escalates")
        self.assertEqual(flat["models"], [])
        impl = lambda s: {k: p for k, p in s["prompts"].items() if k.startswith("implementer:")}  # noqa: E731
        self.assertEqual(impl(up), impl(flat), "the model changes, the prompt does not")
        once = json.loads(json.dumps(decl_of("the last fix attempt escalates")))
        once["plan"]["max_fix_cycles"] = 1
        self.assertEqual(scenario(once)["models"], [], "a single attempt is never escalated")
        node_agrees(self)

    def project(self, **env):
        from test_pipeline import E2E_MANIFEST
        repo = Repo(E2E_MANIFEST)
        self.addCleanup(repo.cleanup)
        logs, bindir = temp_dir(self, "fake-logs-"), temp_dir(self, "fake-claude-")
        repo.env.update(AGENTIC_CLAUDE=testutil.install_fake_claude(bindir), FAKE_LOG_DIR=str(logs), FAKE_WRITE="1",
                        FAKE_MODE="nousage", FAKE_REVIEW_FINDING="WP-02", **env)
        repo.write("acc.py", "import os, sys\nsys.exit(0 if all(os.path.exists(p) for p in "
                             "('src/a.py', 'src/b/x.py', 'src/c.py')) else 1)\n")
        subprocess.run(["git", "add", "-A"], cwd=repo.dir, check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"], cwd=repo.dir,
                       check=True)
        r = repo.run("run-pipeline")
        result = json.loads((repo.dir / ".claude/state/runs/workflow-result.json").read_text(encoding="utf-8"))
        return r, result, testutil.fake_calls(logs)

    @staticmethod
    def opt(call, flag):
        argv = call["argv"]
        return argv[argv.index(flag) + 1] if flag in argv else None

    def test_ac_005_fallback_resume(self):
        r, result, calls = self.project(FAKE_RE_REVIEW="NOT_ADDRESSED")
        wp2 = [c for c in calls if c["agent"] == "implementer" and c["id"] == "WP-02"]
        first, fixes = wp2[0], [c for c in wp2 if c["prompt"].startswith("Fix WP-02.")]
        self.assertEqual(len(fixes), 2, r.stdout)
        self.assertIsNone(self.opt(first, "--resume"))
        self.assertEqual(self.opt(fixes[0], "--resume"), f"s-{first['pid']}", "its own session, not another's")
        self.assertEqual(fixes[0]["argv"][fixes[0]["argv"].index("-p") + 1], "--resume")
        self.assertEqual(self.opt(fixes[1], "--model"), "opus")
        self.assertIsNone(self.opt(fixes[1], "--resume"), "the escalated attempt starts fresh")
        for c in calls:
            if c is not fixes[1]:
                self.assertEqual(self.opt(c, "--model"), "sonnet", c["agent"])
        self.assertEqual(result["agent_calls"], len(calls))

    def test_ac_006_resume_retry(self):
        r, result, calls = self.project(FAKE_RESUME_FAILS="1")
        fixes = [c for c in calls if c["agent"] == "implementer" and c["prompt"].startswith("Fix WP-02.")]
        self.assertEqual(len(fixes), 2, r.stdout)
        self.assertIsNotNone(self.opt(fixes[0], "--resume"))
        self.assertIsNone(self.opt(fixes[1], "--resume"), "retried once, fresh")
        self.assertEqual((result["result"], result["fix_cycles"]), ("OK", 1), r.stdout)
        self.assertEqual(result["agent_calls"], len(calls) - 1, "the retry is the same attempt")


class BudgetTests(unittest.TestCase):
    def test_ac_007_budget(self):
        small = scenario("a budget too small for the run stops before any agent")
        self.assertEqual((small["result"], small["failed_stage"]), ("REVIEW", "budget"))
        self.assertEqual(small["agents"], [])
        self.assertIn("it needs at least 8", small["detail"])
        gap = scenario("a budget that cannot fund a fix cycle stops with the gap")
        self.assertEqual((gap["result"], gap["failed_stage"]), ("REVIEW", "budget"))
        self.assertIn("over budget", gap["pending"])
        self.assertIn("3 needed, 2 left", gap["detail"])
        self.assertFalse([a for a in gap["agents"] if a.endswith("(fix)")])
        decl = decl_of("no budget runs as before")
        free = scenario(decl)
        without = scenario({k: v for k, v in decl.items() if k != "plan"})
        for key in ("result", "fix_cycles", "pending", "agents"):
            self.assertEqual(free[key], without[key], key)
        for s in (small, gap, free):
            self.assertEqual(s["agent_calls"], len(s["agents"]))
        node_agrees(self)


class ConfigTests(unittest.TestCase):
    def plan(self, config):
        repo = Repo()
        self.addCleanup(repo.cleanup)
        repo.write(".claude/agentic.json", json.dumps(config))
        return repo.run("pipeline-plan")

    def test_ac_008_config(self):
        r = self.plan({"max_agent_calls": 40})
        self.assertEqual(r.returncode, 0, r.stderr)
        plan = json.loads(r.stdout.splitlines()[-1])
        self.assertEqual((plan["max_agent_calls"], plan["escalation_model"]), (40, "opus"))
        off = json.loads(self.plan({"escalation": False}).stdout.splitlines()[-1])
        self.assertEqual((off["escalation_model"], off["max_agent_calls"]), (None, None))
        calls = "agentic.json: max_agent_calls must be a whole number of agent calls (1 or more), or null"
        for config, message in (({"max_agent_calls": 0}, calls), ({"max_agent_calls": "ten"}, calls),
                                ({"max_agent_calls": True}, calls),
                                ({"escalation": "no"}, "agentic.json: escalation must be true or false")):
            with self.subTest(config=config):
                r = self.plan(config)
                self.assertEqual(r.returncode, 1, r.stdout)
                self.assertIn(message, r.stderr)


@FLOW_REPO_ONLY
class AdvisorTests(unittest.TestCase):
    def argv_with(self, spec_text):
        repo = Repo()
        self.addCleanup(repo.cleanup)
        repo.write(".claude/workflows/pipeline-spec.json", spec_text)
        logs, bindir = temp_dir(self, "fake-logs-"), temp_dir(self, "fake-claude-")
        repo.env.update(AGENTIC_CLAUDE=testutil.install_fake_claude(bindir), FAKE_LOG_DIR=str(logs))
        r = repo.run("run-wave", "1", "WP-01")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return testutil.fake_calls(logs, "WP-01")[-1]["argv"]

    def test_ac_009_advisor(self):
        self.assertIsNone(SPEC["constants"]["advisor_model"])
        before = (ROOT / ".claude/workflows/pipeline-spec.json").read_text(encoding="utf-8")
        self.assertNotIn("--advisor", self.argv_with(before))
        variant = load("bench_variant_v214", ROOT / "bench" / "variant.py")
        out = variant.build("advisor-opus", temp_dir(self, "variant-") / "v")
        text = (out / ".claude/workflows/pipeline-spec.json").read_text(encoding="utf-8")
        constants = json.loads(text)["constants"]
        self.assertEqual((constants["advisor_model"], constants["escalation_model"]), ("opus", None))
        argv = self.argv_with(text)
        self.assertEqual(argv[argv.index("--advisor") + 1], "opus")
        self.assertEqual((ROOT / ".claude/workflows/pipeline-spec.json").read_text(encoding="utf-8"), before)


@FLOW_REPO_ONLY
class BenchTests(unittest.TestCase):
    def test_ac_010_bench(self):
        base = ROOT / "bench" / "scenarios" / "re-review"
        for name, expect in (("fixed.json", {"status": "PASS", "all_verdicts": "ADDRESSED"}),
                             ("unfixed.json", {"status": "FAIL", "all_verdicts": "NOT_ADDRESSED"})):
            meta = json.loads((base / name).read_text(encoding="utf-8"))
            self.assertEqual(meta["expect"], expect, name)
            self.assertEqual((meta["agent"], meta["prompt"], meta["schema_name"]),
                             ("reviewer", "re_review", "re-review"))
            self.assertRegex(meta["values"]["findings"], r'"fingerprint":"F[0-9a-f]{8}"')
        self.assertFalse(list((ROOT / "bench" / "scenarios").rglob("__init__.py")))
        bench = load("bench_v214", ROOT / "bench" / "bench.py")
        runs = [{"fixture": "f", "arm": arm, "result": "OK", "agent_calls": n} for arm, n in (("a", 10), ("b", 14))]
        self.assertEqual(bench.compare(runs, seed=0)["fixtures"]["f"]["agent_calls"], 4)


if __name__ == "__main__":
    unittest.main()
