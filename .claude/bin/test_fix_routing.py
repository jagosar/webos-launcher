"""Acceptance tests of agentic-flow 2.21.2: AC-001 .. AC-011 of docs/architecture/0031. A fix's diff carries the run's
attempt number; a finding that names other packages' files is split by owner; QA that the environment blocked is
pending and never kept; and no PowerShell rule runs arbitrary code.

Run: python3 -m unittest discover -s .claude/bin -p test_fix_routing.py
Everything runs in temporary repos and simulated runs. Nothing is spent.
"""
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parent
ROOT = BIN.parents[1]
sys.path.insert(0, str(BIN))
import agentic  # noqa: E402
import core  # noqa: E402
import findings  # noqa: E402
from test_agentic import MANIFEST, PY, Repo  # noqa: E402
from test_manifest_check import MANIFEST as CHECK_MANIFEST  # noqa: E402
from test_pipeline import raw_run, run_scenario, scenario  # noqa: E402

FLOW_REPO_ONLY = unittest.skipUnless(core.is_flow_repo(ROOT), "the flow's own repo")
NEEDS_NODE = unittest.skipUnless(shutil.which("node"), "node is not installed")
CAPPED = "a defect that does not get fixed is capped"
SPLIT = "a finding that names another package's files is split"
QA_ENV = "QA blocked by the environment is pending, not a verdict"
QA_AGAIN = "QA blocked by the environment is called again after a standards-only fix"
QA_KEPT = "a standards-only fix keeps the QA report"
RUNNER = "powershell -NoProfile -NonInteractive -File build/Invoke-Tests.ps1"
PESTER = 'powershell -NoProfile -NonInteractive -Command "Invoke-Pester -EnableExit"'
COMPARED = ("result", "failed_stage", "detail", "fix_cycles", "pending", "gates", "gate_calls", "agents", "not_checked",
            "gate_denials", "review_callers", "verification_failures", "review_axes", "rules", "agent_calls", "models",
            "re_review", "qa_passes", "prompts")
_DUMP = {}


def native(name):
    """What the workflow did in a named conformance scenario (`test_workflow.mjs --dump`, run once)."""
    if "all" not in _DUMP:
        r = subprocess.run(["node", str(BIN / "test_workflow.mjs"), "--dump"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        assert r.returncode == 0, r.stdout + r.stderr
        _DUMP["all"] = json.loads(r.stdout)
    return _DUMP["all"][name]


def ledger(repo):
    return json.loads((repo.dir / ".claude/state/ledger.json").read_text(encoding="utf-8"))


class FixAttemptTests(unittest.TestCase):
    """Issue 140: close-stage numbers a fix by the engine's attempt, not by the ledger left by earlier runs."""

    def setUp(self):
        self.repo = Repo(MANIFEST)
        self.addCleanup(self.repo.cleanup)
        self.repo.write("acc.py", "import sys\nsys.exit(0)\n")
        self.repo.write("src/b/x.py", "v1\n")
        state = self.repo.dir / ".claude/state"
        (state / "runs").mkdir(parents=True, exist_ok=True)
        (state / "ledger.json").write_text(json.dumps({"WP-02": {"state": "done", "fix_count": 3}}), encoding="utf-8")
        self.diff = state / "runs/WP-02.fix-1.diff"
        self.diff.write_text("STALE\n", encoding="utf-8")

    def test_ac_001_the_diff_carries_the_runs_attempt(self):
        self.repo.run("open-stage", "1")
        self.repo.write("src/b/x.py", "v2\n")
        r = self.repo.run("close-stage", "1", "WP-02=done@1", "--fix")
        self.assertNotIn("invalid entry", r.stderr)
        text = self.diff.read_text(encoding="utf-8")
        self.assertIn("+v2", text)
        self.assertNotIn("STALE", text)
        self.assertEqual(ledger(self.repo)["WP-02"]["fix_count"], 1)
        self.assertFalse((self.repo.dir / ".claude/state/runs/WP-02.fix-4.diff").exists())

    def test_ac_002_a_diff_that_cannot_be_computed_is_a_placeholder(self):
        self.repo.run("open-stage", "1")
        (self.repo.dir / ".claude/state/tree-1.txt").unlink()
        r = self.repo.run("close-stage", "1", "WP-02=done@1", "--fix")
        self.assertNotIn("invalid entry", r.stderr)
        self.assertEqual(self.diff.read_text(encoding="utf-8"), "(the diff of this attempt could not be recorded)\n")
        for args in (("WP-02=done@1",), ("WP-02=done@0", "--fix")):
            with self.subTest(args=args):
                self.repo.run("open-stage", "1")
                r = self.repo.run("close-stage", "1", *args)
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                self.assertIn("invalid entry", r.stderr)


class EngineCase(unittest.TestCase):
    def assert_same_in_both(self, name, mine):
        theirs = native(name)
        for key in COMPARED:
            self.assertEqual(mine[key], theirs[key], key)


class FixNumberingTests(EngineCase):
    """AC-003: both engines number each fix close by the in-run attempt."""

    def test_ac_003_fix_closes_carry_the_attempt(self):
        mine = run_scenario(scenario(CAPPED))
        fixes = [g for g in mine["gates"] if " --fix" in g]
        self.assertEqual(fixes, ["close-stage 2 WP-03=done@1 --fix", "close-stage 2 WP-03=done@2 --fix"])
        self.assertFalse([g for g in mine["gates"] if "@" in g and " --fix" not in g], mine["gates"])
        self.assertIn(".claude/state/runs/WP-03.fix-1.diff", mine["prompts"]["implementer:WP-03 (fix)#2"])

    @NEEDS_NODE
    def test_ac_003_the_workflow_does_the_same(self):
        self.assert_same_in_both(CAPPED, run_scenario(scenario(CAPPED)))


class OwnerSplitTests(EngineCase):
    """Issue 141: a finding that names other packages' files is split, and each part goes to its owner."""

    def test_ac_004_each_part_goes_to_its_owner(self):
        mine = run_scenario(scenario(SPLIT))
        self.assertEqual((mine["result"], mine["fix_cycles"]), ("OK", 1))
        prompts = mine["prompts"]
        wp03 = prompts["implementer:WP-03 (fix)#1"]
        for text in ('"wp":"WP-03"', '"file":"src/c.py"', '"scope":["src/c.py"]', '"split_from":"F'):
            self.assertIn(text, wp03)
        wp01 = prompts["implementer:WP-01 (fix)#1"]
        self.assertIn('"scope":["src/a.py"]', wp01)
        self.assertNotIn('"scope":["src/c.py"]', wp01)
        self.assertEqual(mine["re_review"], {"spec": [0, 0, 0], "standards": [2, 0, 0]})
        self.assertNotIn("fix limit reached", mine["pending"])

    @NEEDS_NODE
    def test_ac_004_the_workflow_does_the_same(self):
        self.assert_same_in_both(SPLIT, run_scenario(scenario(SPLIT)))

    def test_ac_005_the_edges_of_the_split(self):
        owners = {"WP-01": ["src/a.py"], "WP-02": ["src/b/"], "WP-03": ["lib/util.py"]}
        base = {"axis": "standards", "tier": "reflex", "rule": "No magic numbers", "wp": "WP-01", "file": "src/a.py",
                "line": 3, "problem": "P"}
        a, b, c = (dict(base, evidence=e) for e in ("src/a.py:3 only", "C:\\repo\\src\\b\\x.py:7 and src/a.py:3",
                                                       "util.py:9"))
        items = findings.with_fingerprints([a, b, c])
        out = findings.split_by_owner(items, owners, "C:/repo")
        self.assertIs(out[0], items[0])
        self.assertNotIn("scope", out[0])
        self.assertEqual([(f["wp"], f["file"], f["scope"]) for f in out[1:3]],
                         [("WP-01", "src/a.py", ["src/a.py"]), ("WP-02", "src/b/", ["src/b/"])])
        self.assertNotIn("line", out[2])
        self.assertIs(out[3], items[2])
        self.assertEqual(len(out), 4)
        twins = findings.with_fingerprints([dict(base, evidence="src/b/x.py:1"), dict(base, evidence="src/b/x.py:2")])
        parts = [f for f in findings.split_by_owner(twins, owners, "") if f["wp"] == "WP-02"]
        self.assertEqual(len(parts), 2)
        self.assertNotEqual(parts[0]["fingerprint"], parts[1]["fingerprint"])
        self.assertTrue(parts[1]["fingerprint"].endswith("-2"))
        for empty in (None, {}):
            self.assertEqual(findings.split_by_owner(items, empty, None), items)
        prompt = run_scenario(scenario(SPLIT))["prompts"]["standards-reviewer:standards-reviewer (re-review)#1"]
        self.assertIn('A finding with a "scope" is judged only in those files', prompt)


class QaEnvironmentTests(EngineCase):
    """Issue 142: QA that the environment blocked is pending, explained in detail, and never kept."""

    def test_ac_006_an_environment_blocker_is_pending(self):
        mine = run_scenario(scenario(QA_ENV))
        self.assertEqual((mine["result"], mine["fix_cycles"]), ("REVIEW", 0))
        self.assertEqual(mine["detail"], findings.QA_NOT_RUN_DETAIL)
        items = [p for p in raw_run(scenario(QA_ENV))["pending"] if p.get("kind") == "qa not run"]
        self.assertEqual([(p["claim"], p["how_to_check"]) for p in items], [(RUNNER, "run " + RUNNER)])
        design = json.loads(json.dumps(scenario(QA_ENV)))
        design["qa"][0]["blocker_kind"] = "design"
        self.assertIsNone(run_scenario(design)["detail"])
        self.assertFalse([p for p in raw_run(design)["pending"] if p.get("kind") == "qa not run"])
        for other in (scenario(CAPPED), {}):
            self.assertFalse([p for p in raw_run(other)["pending"] if p.get("kind") == "qa not run"])

    @NEEDS_NODE
    def test_ac_006_the_workflow_does_the_same(self):
        self.assert_same_in_both(QA_ENV, run_scenario(scenario(QA_ENV)))

    def test_ac_007_an_environment_blocked_report_is_never_kept(self):
        mine = run_scenario(scenario(QA_AGAIN))
        self.assertEqual((mine["result"], mine["qa_passes"]), ("OK", ["full", "full"]))
        self.assertEqual(mine["agents"].count("qa:qa"), 2)
        self.assertEqual(run_scenario(scenario(QA_KEPT))["qa_passes"], ["full", "kept"])

    @NEEDS_NODE
    def test_ac_007_the_workflow_does_the_same(self):
        self.assert_same_in_both(QA_AGAIN, run_scenario(scenario(QA_AGAIN)))


class CodeRunnerRuleTests(unittest.TestCase):
    """Issue 118: no PowerShell prefix rule runs arbitrary code, and a code command is allowed exactly."""

    BAD = ["Bash(powershell -NoProfile -NonInteractive -Command Invoke-Pester:*)", "Bash(pwsh -c Invoke-Pester:*)",
           "Bash(C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe -Command x:*)",
           "Bash(powershell -NoProfile Invoke-Pester:*)", "Bash(pwsh -EncodedCommand AAAA:*)",
           "Bash(powershell -Command x -File y.ps1:*)", "Bash(pwsh:*)", "Bash(powershell)"]
    GOOD = [f"Bash({RUNNER}:*)", "Bash(pwsh -f build/Invoke-Tests.ps1:*)",
            "Bash(powershell -ExecutionPolicy Bypass -File build/t.ps1:*)",
            "Bash(powershell -NoProfile -NonInteractive -Command Invoke-Pester -EnableExit)"]

    def errors(self, **fields):
        return agentic.validate(dict(CHECK_MANIFEST, **fields))

    def test_ac_008_validate_rejects_powershell_rules_that_run_code(self):
        for rule in self.BAD:
            with self.subTest(rule=rule):
                self.assertTrue(any("too broad" in e for e in self.errors(allow_bash=[rule])), rule)
        for rule in self.GOOD:
            with self.subTest(rule=rule):
                self.assertEqual(self.errors(allow_bash=[rule]), [])
        self.assertEqual(self.errors(verify={"tests": PESTER}), [])

    def repo(self):
        repo = Repo(dict(MANIFEST, verify={"tests": PESTER, "lint": f"{PY} lint.py"}))
        self.addCleanup(repo.cleanup)
        return repo

    def test_ac_009_sync_permissions_writes_the_exact_rule_only(self):
        repo = self.repo()
        local = repo.dir / ".claude/settings.local.json"
        local.write_text(json.dumps({"permissions": {"allow": ["Read", f"Bash({PESTER}:*)"]}}), encoding="utf-8")
        first = repo.run("sync-permissions")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("removed", first.stdout)
        allow = json.loads(local.read_text(encoding="utf-8"))["permissions"]["allow"]
        self.assertIn("Read", allow)
        self.assertIn(f"Bash({PESTER})", allow)
        self.assertNotIn(f"Bash({PESTER}:*)", allow)
        self.assertIn(f"Bash({PY} lint.py)", allow)
        self.assertIn(f"Bash({PY} lint.py:*)", allow)
        repo.run("sync-permissions")
        self.assertEqual(json.loads(local.read_text(encoding="utf-8"))["permissions"]["allow"], allow)

    def test_ac_010_the_warning_points_to_a_runner_with_file(self):
        r = self.repo().run("validate")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        warnings = [line for line in r.stdout.splitlines() if line.startswith("warning:")]
        pointed = [w for w in warnings if "Invoke-Pester -EnableExit" in w and "-File" in w and "powershell.md" in w]
        self.assertEqual(len(pointed), 1, r.stdout)
        self.assertFalse([w for w in warnings if "lint.py" in w and "powershell.md" in w])

    @FLOW_REPO_ONLY
    def test_ac_010_the_guidance_recommends_file(self):
        pack = (ROOT / ".claude/rules-library/powershell.md").read_text(encoding="utf-8")
        self.assertIn("-File build/Invoke-Tests.ps1", pack)
        self.assertIn(f"Bash({RUNNER}:*)", pack)
        self.assertNotIn('-Command "Invoke-', pack)
        rows = [line.split("|")[1].strip() for line in pack.splitlines()
                if line.startswith("| ") and line.split("|")[1].strip() in ("build", "types", "lint", "tests")]
        self.assertEqual(rows, ["build", "types", "lint", "tests"])
        architect = (ROOT / ".claude/commands/architect.md").read_text(encoding="utf-8")
        line = next(x for x in architect.splitlines() if x.startswith("- **`allow_bash`:**"))
        self.assertIn("-File", line)


@FLOW_REPO_ONLY  # it reads this repository's docs and release files
class ReleaseTests(unittest.TestCase):
    def read(self, rel):
        return (ROOT / rel).read_text(encoding="utf-8")

    def test_ac_011_release_2_21_2(self):
        import upgrade
        notes = upgrade.NOTES["2.21.2"]
        for word in ("@N", "scope", "qa not run", "-File"):
            self.assertTrue(any(word in n for n in notes), (word, notes))
        keys = list(upgrade.NOTES)
        self.assertEqual(keys[keys.index("2.21.2") + 1], "2.21.1", "right above the release it fixes")

    def test_ac_011_agents_and_docs(self):
        qa = self.read(".claude/agents/qa.md")
        self.assertIn("blocker_kind", qa)
        self.assertIn("not_run", qa)
        for name in ("reviewer", "standards-reviewer"):
            self.assertIn("one finding per package", self.read(f".claude/agents/{name}.md"), name)
        works = self.read("docs/how-it-works.md")
        for word in ("scope", "blocker_kind", "qa not run"):
            self.assertIn(word, works)
        attempt = next(line for line in works.splitlines() if line.startswith("- **Fix attempt:**"))
        self.assertIn("in this run", attempt)
        self.assertNotIn("2.21.2", works)
        line = next(x for x in self.read(".claude/commands/implement.md").splitlines() if "`qa_passes`" in x)
        self.assertIn("environment", line)


if __name__ == "__main__":
    unittest.main()
