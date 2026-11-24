"""Acceptance tests of agentic-flow 2.21.1: AC-001 .. AC-008 of docs/architecture/0030. Without a test report, a
Pester header of passing tests names no package; every blocking phase lists its failing lines and a fix receives them
as its evidence, in both engines; and the denials of the zone stop gate reach the run's record.

Run: python3 -m unittest discover -s .claude/bin -p test_failure_evidence.py
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
import core  # noqa: E402
from test_agentic import MANIFEST, PY, Repo  # noqa: E402

FLOW_REPO_ONLY = unittest.skipUnless(core.is_flow_repo(ROOT), "the flow's own repo")
NEEDS_NODE = unittest.skipUnless(shutil.which("node"), "node is not installed")
BOM = "[-] XMM-Panel.ps1 has UTF-8 BOM, CRLF only and no non-ASCII"
GATE = ".claude/state/gate.json"
SUMMARY = "Tests Passed: 2, Failed: 1"
AC601 = ["Describing AC-601 launcher", "  [+] starts 3ms"]
AC602 = ["Describing AC-602 settings"]
AC605 = ["  Context AC-605 flags", "    [+] lists the flags 2ms"]
FLAGS = ["Describing FeatureFlagList", f"  {BOM} 5ms"]
CONSOLE = AC601 + AC602 + AC605 + FLAGS + [SUMMARY]
REGRESSION = "a regression's new failures are the fix's evidence"
ACCEPTANCE = "a failing acceptance phase gives its lines to the fix"
ZONES = "zone gate denials are summed from close-stage"


def script(lines, code):
    return "import sys\n" + "".join(f"print({line!r})\n" for line in lines) + f"sys.exit({code})\n"


def packages(acs_of_wp01=("AC-601",), **extra):
    wps = [{"id": "WP-01", "wave": 1, "depends_on": [], "files": ["src/one.py"], "acs": list(acs_of_wp01)},
           {"id": "WP-02", "wave": 1, "depends_on": [], "files": ["src/two.py"], "acs": ["AC-602"]},
           {"id": "WP-03", "wave": 1, "depends_on": [], "files": ["src/three.py"], "acs": ["AC-605"]}]
    return dict({"allow_bash": [], "acceptance": {"files": ["acceptance/"], "command": f"{PY} acc.py"},
                 "verify": {"tests": f"{PY} t.py"}, "work_packages": wps}, **extra)


class Case(unittest.TestCase):
    def repo(self, manifest):
        repo = Repo(manifest)
        self.addCleanup(repo.cleanup)
        repo.write("acc.py", script([], 0))
        return repo

    def verify(self, repo):
        r = repo.run("verify")
        self.assertNotIn("Traceback", r.stderr)
        return r.returncode, json.loads(r.stdout.splitlines()[-1])

    def suspects_of(self, console, manifest=None):
        repo = self.repo(manifest or packages())
        repo.write("t.py", script(console, 1))
        code, out = self.verify(repo)
        self.assertEqual(code, 3, out)
        self.assertIn("tests", out["blocking"])
        return out["suspects"]


class HeaderTests(Case):
    def test_ac_001_a_header_of_passing_tests_names_no_package(self):
        self.assertEqual(self.suspects_of(CONSOLE), {})

    def test_ac_001_the_summary_line_keeps_no_header(self):
        last = AC602 + AC605 + FLAGS + AC601
        self.assertEqual(self.suspects_of(last + [SUMMARY]), {})
        self.assertEqual(self.suspects_of(last + ["Tests Passed: 3, Failed: 0"]), {})

    def test_ac_002_a_failing_test_keeps_the_header_of_its_describe(self):
        console = AC601 + AC602 + [f"  {BOM}"] + AC605 + [SUMMARY]
        self.assertEqual(self.suspects_of(console), {"WP-02": {"phases": ["tests"], "files": [], "acs": ["AC-602"]}})

    def test_ac_002_a_failing_test_keeps_the_headers_of_its_context(self):
        console = AC601 + AC602 + AC605 + [f"    {BOM}"] + [SUMMARY]
        self.assertEqual(sorted(self.suspects_of(console)), ["WP-02", "WP-03"])

    def test_ac_002_a_header_is_not_kept_by_its_own_words(self):
        console = ["Describing AC-603 failure handling", "  [+] retries"] + AC601 + AC602 + [f"  {BOM}"] + AC605
        suspects = self.suspects_of(console + [SUMMARY], packages(acs_of_wp01=("AC-601", "AC-603")))
        self.assertEqual(sorted(suspects), ["WP-02"])


class StopGateTests(Case):
    def report(self, repo, wp):
        payload = {"tool_name": "StructuredOutput", "agent_type": "implementer",
                   "tool_input": {"status": "DONE", "wp": wp, "files": [], "checks": [], "internal_decisions": [],
                                  "blocker": None, "not_checked": [], "concerns": [], "missing_context": None}}
        r = repo.run("hook", stdin=json.dumps(payload), env={"AGENTIC_WP": wp})
        self.assertNotIn("Traceback", r.stderr)
        return r

    def gate(self, repo):
        path = repo.dir / GATE
        return json.loads(path.read_text(encoding="utf-8") or "{}") if path.exists() else {}

    def test_ac_003_a_passing_header_is_no_reason_to_refuse(self):
        repo = self.repo(packages())
        repo.write("acc.py", script(CONSOLE, 1))
        self.assertEqual(self.report(repo, "WP-02").returncode, 0)
        self.assertNotIn("WP-02", self.gate(repo))

    def test_ac_003_a_failure_under_the_header_still_refuses(self):
        repo = self.repo(packages())
        repo.write("acc.py", script(AC601 + AC602 + [f"  {BOM}"] + AC605 + [SUMMARY], 1))
        r = self.report(repo, "WP-02")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("AC-602", r.stderr)


class FailingLinesTests(Case):
    ONE = {"allow_bash": [], "acceptance": {"files": ["acceptance/"], "command": f"{PY} acc.py"},
           "verify": {"tests": f"{PY} t.py"},
           "work_packages": [{"id": "WP-01", "wave": 1, "depends_on": [], "files": ["src/a.py"], "acs": ["AC-001"]}]}

    def phases(self, tests, acceptance, code=1):
        repo = self.repo(self.ONE)
        repo.write("t.py", script(tests, code))
        repo.write("acc.py", script(acceptance, code))
        _, out = self.verify(repo)
        return {p["name"]: p for p in out["phases"]}

    def test_ac_004_every_blocking_phase_lists_its_failing_lines(self):
        phases = self.phases(["FAILED acceptance/test_x.py::test_ac_001_total - assert 3 == 4",
                              "1 failed, 4 passed in 0.12s"], [BOM])
        tests, acceptance = phases["tests"], phases["acceptance"]
        self.assertEqual((tests["status"], acceptance["status"]), ("fails", "fails"))
        self.assertTrue(any("test_ac_001_total" in line for line in tests["failing_lines"]), tests)
        self.assertFalse(any("0.12s" in line for line in tests["failing_lines"]), tests)
        self.assertEqual(acceptance["failing_lines"], [BOM])

    def test_ac_004_at_most_ten_lines(self):
        many = [f"FAILED acceptance/test_x.py::test_ac_001_case_{i:02d}" for i in range(15)]
        self.assertEqual(len(self.phases(many, [BOM])["tests"]["failing_lines"]), 10)

    def test_ac_004_a_phase_that_passes_lists_none(self):
        phases = self.phases(["ok"], ["ok"], code=0)
        self.assertTrue(phases)
        for phase in phases.values():
            self.assertNotIn("failing_lines", phase)


class ZoneGateLineTests(Case):
    def close(self, repo, stage, mark, gate):
        self.assertEqual(repo.run("open-stage", stage).returncode, 0)
        repo.write(GATE, json.dumps(gate))
        return repo.run("close-stage", stage, mark).stdout

    def test_ac_006_close_stage_prints_the_denials_of_a_zone(self):
        repo = self.repo(MANIFEST)
        denied = {"AT": {"denials": 1, "last": []}, "QA": {"denials": 2, "last": []}}
        out = self.close(repo, "acceptance", "AT=done", denied)
        self.assertIn("GATE: AT=1\n", out)
        self.assertLess(out.index("GATE: AT=1"), out.index("RESULT:"))
        self.assertIn("GATE: QA=2\n", self.close(repo, "qa", "QA=pass", denied))
        self.assertNotIn("GATE:", self.close(repo, "qa", "QA=pass", {}))

    def test_ac_006_a_wave_line_is_unchanged(self):
        repo = self.repo(MANIFEST)
        self.assertEqual(repo.run("open-stage", "1").returncode, 0)
        repo.write(GATE, json.dumps({"WP-01": {"denials": 1}}))
        out = repo.run("close-stage", "1", "WP-01=done", "WP-02=done").stdout
        self.assertIn("GATE: WP-01=1 WP-02=0\n", out)
        self.assertNotIn("=0 AT", out)


class EngineCase(unittest.TestCase):
    """The scenarios of conformance.json, run by the fallback engine and compared with the native one."""

    @classmethod
    def scenario(cls, name):
        import test_pipeline
        decl = next((d for d in test_pipeline.CONFORMANCE["scenarios"] if d["name"] == name), None)
        if decl is None:
            raise AssertionError(f"conformance.json has no scenario named {name!r}")
        return test_pipeline.run_scenario(decl)

    def native(self, name):
        r = subprocess.run(["node", str(BIN / "test_workflow.mjs"), "--dump"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return json.loads(r.stdout)[name]


class EvidenceTests(EngineCase):
    FIX = "implementer:WP-02 (fix)#1"

    def test_ac_005_a_regression_gives_its_new_failures(self):
        prompt = self.scenario(REGRESSION)["prompts"][self.FIX]
        self.assertIn(BOM, prompt)
        self.assertNotIn("Tests Passed: 40", prompt)

    def test_ac_005_a_failing_acceptance_phase_gives_its_lines(self):
        self.assertIn("FAILED acceptance/test_x.py::test_ac_002_total", self.scenario(ACCEPTANCE)["prompts"][self.FIX])

    @NEEDS_NODE
    def test_ac_005_both_engines_send_the_same_evidence(self):
        for name in (REGRESSION, ACCEPTANCE):
            with self.subTest(scenario=name):
                self.assertEqual(self.scenario(name)["prompts"], self.native(name)["prompts"])


class ZoneRecordTests(EngineCase):
    def test_ac_007_the_record_keeps_the_zone_denials(self):
        self.assertEqual(self.scenario(ZONES)["gate_denials"], {"AT": 1, "QA": 2})
        self.assertEqual(self.scenario("gate denials are summed from close-stage")["gate_denials"], {"WP-01": 1})

    @NEEDS_NODE
    def test_ac_007_both_engines_keep_them(self):
        self.assertEqual(self.native(ZONES)["gate_denials"], {"AT": 1, "QA": 2})


@FLOW_REPO_ONLY  # it reads this repository's docs and release files
class ReleaseTests(unittest.TestCase):
    def test_ac_008_release_2_21_1(self):
        plugin = json.loads((ROOT / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(plugin["version"], "2.21.1")
        import upgrade
        notes = upgrade.NOTES["2.21.1"]
        for word in ("Describing", "failing_lines", "gate_denials"):
            self.assertTrue(any(word in n for n in notes), (word, notes))
        keys = list(upgrade.NOTES)
        self.assertEqual(keys[keys.index("2.21.1") + 1], "2.21.0", "right above the release it fixes")
        works = (ROOT / "docs/how-it-works.md").read_text(encoding="utf-8")
        gate = next(line for line in works.splitlines() if line.startswith("- **Stop gate:**"))
        self.assertIn("AT=1", gate)
        self.assertIn("failing_lines", works)
        self.assertNotIn("2.21.1", works)
        implement = (ROOT / ".claude/commands/implement.md").read_text(encoding="utf-8")
        line = next(line for line in implement.splitlines() if "`gate_denials`" in line)
        self.assertIn("`AT`", line)
        self.assertIn("`QA`", line)
        self.assertIn('"2.21.1"', (BIN / "test_acceptance_v221.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
