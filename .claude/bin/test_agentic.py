"""Tests of the guardrails and the launcher.

Run: python3 -m unittest discover -s .claude/bin -p "test_*.py"
Each test works in a temporary git repo; the launcher uses a fake `claude`, with no real calls.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "agentic.py"
AGENTS_DIR = SCRIPT.parents[1] / "agents"

MANIFEST = {
    "allow_bash": ["Bash(python3 -m unittest:*)"],
    "ignore": ["dist/"],
    "acceptance": {"files": ["acceptance/"], "command": "python3 acc.py"},
    "work_packages": [
        {"id": "WP-01", "wave": 1, "depends_on": [], "files": ["src/a.py", "tests/test_a.py"]},
        {"id": "WP-02", "wave": 1, "depends_on": [], "files": ["src/b/"]},
        {"id": "WP-03", "wave": 2, "depends_on": ["WP-01"], "files": ["src/c.py"]},
    ],
}

FAKE_CLAUDE = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json, os, sys, time
    wp = os.environ["AGENTIC_WP"]
    with open(os.path.join(os.environ["FAKE_LOG_DIR"], wp + ".args"), "w") as f:
        json.dump(sys.argv[1:], f)
    with open(os.path.join(os.environ["FAKE_LOG_DIR"], wp + ".env"), "w") as f:
        json.dump({k: os.environ.get(k) for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}, f)
    time.sleep(float(os.environ.get("FAKE_SLEEP", "0")))
    status = os.environ.get("FAKE_STATUS", "DONE" if wp != "QA" else "PASS")
    print(json.dumps({"result": "STATUS: " + status + "\\nWP: " + wp}))
""")


class Repo:
    def __init__(self, manifest=MANIFEST, raw_manifest=None):
        self.dir = Path(tempfile.mkdtemp(prefix="agentic-test-"))
        body = raw_manifest if raw_manifest is not None else json.dumps(manifest, indent=2)
        (self.dir / "architecture.md").write_text(f"# A\n\n```json wp-manifest\n{body}\n```\n")
        shutil.copytree(AGENTS_DIR, self.dir / ".claude" / "agents")
        subprocess.run(["git", "init", "-q"], cwd=self.dir, check=True)
        self.env = dict(os.environ, CLAUDE_PROJECT_DIR=str(self.dir))
        self.env.pop("AGENTIC_WP", None)

    def run(self, *args, stdin=None, env=None):
        return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=self.dir, input=stdin,
                              capture_output=True, text=True, env=dict(self.env, **(env or {})))

    def hook(self, payload, wp=None):
        return self.run("hook", stdin=json.dumps(payload), env={"AGENTIC_WP": wp} if wp else None).returncode

    def write(self, rel, text="x"):
        p = self.dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def cleanup(self):
        shutil.rmtree(self.dir, ignore_errors=True)


def write_payload(path, agent_id=None, tool="Write"):
    data = {"tool_name": tool, "tool_input": {"file_path": path}}
    if agent_id:
        data.update(agent_id=agent_id, agent_type="implementer")
    return data


def bash_payload(command, agent_id=None):
    data = {"tool_name": "Bash", "tool_input": {"command": command}}
    if agent_id:
        data.update(agent_id=agent_id, agent_type="implementer")
    return data


class Base(unittest.TestCase):
    manifest = MANIFEST

    def setUp(self):
        self.repo = Repo(self.manifest)

    def tearDown(self):
        self.repo.cleanup()


class ValidateTests(unittest.TestCase):
    def errors(self, manifest):
        sys.path.insert(0, str(SCRIPT.parent))
        import agentic
        return agentic.validate(manifest)

    def test_valid_manifest(self):
        self.assertEqual(self.errors(MANIFEST), [])

    def test_overlapping_files(self):
        m = json.loads(json.dumps(MANIFEST))
        m["work_packages"][2]["files"] = ["src/b/x.py"]
        self.assertTrue(any("two owners" in e for e in self.errors(m)))

    def test_overlap_with_qa(self):
        m = dict(MANIFEST, qa={"files": ["src/"]})
        self.assertTrue(any("two owners" in e for e in self.errors(m)))

    def test_dependency_in_same_wave(self):
        m = json.loads(json.dumps(MANIFEST))
        m["work_packages"][1]["depends_on"] = ["WP-01"]
        self.assertTrue(any("earlier wave" in e for e in self.errors(m)))

    def test_broad_rules_rejected(self):
        for rule in ["Bash(*)", "Bash(python3:*)", "Bash(npm:*)", "Bash(bash:*)", "Bash(sh)", "Bash(git:*)", "Bash(np*)"]:
            with self.subTest(rule=rule):
                m = dict(MANIFEST, allow_bash=[rule])
                self.assertTrue(any("too broad" in e for e in self.errors(m)))

    def test_specific_rules_accepted(self):
        m = dict(MANIFEST, allow_bash=["Bash(npm test:*)", "Bash(python3 -m pytest:*)", "Bash(pytest:*)", "Bash(make test)"])
        self.assertEqual(self.errors(m), [])

    def test_cannot_own_claude_or_architecture(self):
        for f in [".claude/settings.json", "architecture.md"]:
            m = json.loads(json.dumps(MANIFEST))
            m["work_packages"][0]["files"] = [f]
            self.assertTrue(self.errors(m), f)

    def test_duplicate_and_reserved_ids(self):
        m = json.loads(json.dumps(MANIFEST))
        m["work_packages"][1]["id"] = "WP-01"
        self.assertTrue(any("duplicate" in e for e in self.errors(m)))
        m["work_packages"][1]["id"] = "QA"
        self.assertTrue(any("reserved" in e for e in self.errors(m)))


class HookWriteTests(Base):
    def test_main_thread_unrestricted(self):
        self.assertEqual(self.repo.hook(write_payload(str(self.repo.dir / "cualquiera.py"))), 0)

    def test_fallback_mode_own_and_foreign(self):
        self.assertEqual(self.repo.hook(write_payload(str(self.repo.dir / "src/a.py")), wp="WP-01"), 0)
        self.assertEqual(self.repo.hook(write_payload(str(self.repo.dir / "src/b/x.py")), wp="WP-01"), 2)
        self.assertEqual(self.repo.hook(write_payload(str(self.repo.dir / "src/b/x.py")), wp="WP-02"), 0)

    def test_relative_path_and_outside_the_repo(self):
        self.assertEqual(self.repo.hook({**write_payload("src/a.py"), "cwd": str(self.repo.dir)}, wp="WP-01"), 0)
        self.assertEqual(self.repo.hook(write_payload("/tmp/outside.py"), wp="WP-01"), 2)
        self.assertEqual(self.repo.hook(write_payload(str(self.repo.dir / "src/../src/b/x.py")), wp="WP-01"), 2)

    def test_qa_only_its_zone(self):
        self.assertEqual(self.repo.hook(write_payload(str(self.repo.dir / "qa/t.py")), wp="QA"), 0)
        self.assertEqual(self.repo.hook(write_payload(str(self.repo.dir / "src/a.py")), wp="QA"), 2)

    def test_workflow_mode_needs_claim(self):
        self.assertEqual(self.repo.hook(write_payload(str(self.repo.dir / "src/a.py"), agent_id="ag1")), 2)
        self.assertEqual(self.repo.hook(bash_payload("python3 .claude/bin/agentic.py claim WP-01", agent_id="ag1")), 0)
        self.assertEqual(self.repo.hook(write_payload(str(self.repo.dir / "src/a.py"), agent_id="ag1")), 0)
        self.assertEqual(self.repo.hook(write_payload(str(self.repo.dir / "src/c.py"), agent_id="ag1")), 2)

    def test_claim_is_exclusive(self):
        claim = "python3 .claude/bin/agentic.py claim WP-01"
        self.assertEqual(self.repo.hook(bash_payload(claim, agent_id="ag1")), 0)
        self.assertEqual(self.repo.hook(bash_payload(claim, agent_id="ag1")), 0, "claiming your own again is valid")
        self.assertEqual(self.repo.hook(bash_payload(claim, agent_id="ag2")), 2, "another agent cannot claim it")
        self.assertEqual(self.repo.hook(bash_payload("python3 .claude/bin/agentic.py claim WP-02", agent_id="ag1")), 2,
                         "an agent cannot claim two WPs")
        self.assertEqual(self.repo.hook(bash_payload("python3 .claude/bin/agentic.py claim WP-99", agent_id="ag3")), 2)

    def test_snapshot_resets_claims(self):
        claim = "python3 .claude/bin/agentic.py claim WP-01"
        self.assertEqual(self.repo.hook(bash_payload(claim, agent_id="ag1")), 0)
        self.repo.run("snapshot", "1")
        self.assertEqual(self.repo.hook(bash_payload(claim, agent_id="ag2")), 0)


class V2EntityTests(Base):
    """Wave 0 (AT), the read-only reviewer and the v2 flow validations."""

    def p(self, rel):
        return str(self.repo.dir / rel)

    def test_implementer_cannot_touch_acceptance(self):
        self.assertEqual(self.repo.hook(write_payload(self.p("acceptance/test_ac_001.py")), wp="WP-01"), 2)
        self.assertEqual(self.repo.hook(write_payload(self.p("acceptance/test_ac_001.py")), wp="AT"), 0)
        self.assertEqual(self.repo.hook(write_payload(self.p("src/a.py")), wp="AT"), 2, "the test-author does not implement")

    def test_reviewer_writes_nothing(self):
        for rel in ("src/a.py", "review.md", "acceptance/x.py", "qa/x.py"):
            with self.subTest(rel=rel):
                self.assertEqual(self.repo.hook(write_payload(self.p(rel)), wp="REVIEW"), 2)
        self.assertEqual(self.repo.hook(bash_payload("git diff"), wp="REVIEW"), 0)

    def test_claim_at_and_review_in_workflow_mode(self):
        for ident, agent in (("AT", "a1"), ("REVIEW", "a2")):
            self.assertEqual(self.repo.hook(bash_payload(f"python3 .claude/bin/agentic.py claim {ident}", agent_id=agent)), 0)
        self.assertEqual(self.repo.hook(write_payload(self.p("acceptance/t.py"), agent_id="a1")), 0)
        self.assertEqual(self.repo.hook(write_payload(self.p("src/a.py"), agent_id="a2")), 2)

    def write_manifest(self, m):
        (self.repo.dir / "architecture.md").write_text(f"```json wp-manifest\n{json.dumps(m)}\n```")

    def test_v2_validations(self):
        cases = {
            "reserved IDs": lambda m: m["work_packages"][0].update(id="AT"),
            "acceptance without command": lambda m: m.update(acceptance={"files": ["acceptance/"]}),
            "WP owning the decision archive": lambda m: m["work_packages"][0].update(files=["docs/architecture/x.md"]),
            "overlap with acceptance": lambda m: m["work_packages"][0].update(files=["acceptance/"]),
        }
        for name, mutate in cases.items():
            with self.subTest(name):
                m = json.loads(json.dumps(MANIFEST))
                mutate(m)
                self.write_manifest(m)
                self.assertEqual(self.repo.run("validate").returncode, 1)

    def test_v2_agent_models(self):
        agents = self.repo.dir / ".claude/agents"
        for name, wrong in (("test-author", "opus"), ("reviewer", "opus"), ("plan-reviewer", "sonnet")):
            with self.subTest(name):
                f = agents / f"{name}.md"
                original = f.read_text()
                good = "model: opus" if name == "plan-reviewer" else "model: sonnet"
                f.write_text(original.replace(good, f"model: {wrong}"))
                r = self.repo.run("validate")
                self.assertEqual(r.returncode, 1, r.stderr)
                self.assertIn(name, r.stderr)
                f.write_text(original)
        self.assertEqual(self.repo.run("validate").returncode, 0)

    def acc(self, code):
        (self.repo.dir / "acc.py").write_text(f"import sys\nprint('tests')\nsys.exit({code})\n")

    def test_acceptance_red_and_green(self):
        self.acc(1)
        self.assertEqual(self.repo.run("acceptance", "--expect", "red").returncode, 0)
        self.assertEqual(self.repo.run("acceptance", "--expect", "green").returncode, 3)
        self.acc(0)
        r = self.repo.run("acceptance", "--expect", "red")
        self.assertEqual(r.returncode, 3)
        self.assertIn("ALREADY PASS WITHOUT CODE", r.stdout)
        self.assertEqual(self.repo.run("acceptance", "--expect", "green").returncode, 0)
        self.assertIn("acceptance (green): OK", self.repo.run("status").stdout)

    def test_nonexistent_acceptance_command_is_not_red(self):
        m = json.loads(json.dumps(MANIFEST))
        m["acceptance"]["command"] = "command-that-does-not-exist --x"
        self.write_manifest(m)
        r = self.repo.run("acceptance", "--expect", "red")
        self.assertEqual(r.returncode, 3)
        self.assertIn("ERROR", r.stdout)


class ContentGuardTests(Base):
    """Content guardrails adapted from ECC: hook bypass, config, disabled tests and secrets."""
    manifest = dict(MANIFEST, work_packages=[
        {"id": "WP-01", "wave": 1, "depends_on": [], "files": ["src/a.py", "tests/test_a.py", "pyproject.toml", "setup.cfg"]},
        {"id": "WP-02", "wave": 1, "depends_on": [], "files": ["ruff.toml"], "config_edit": True},
    ])

    def w(self, rel, content, wp="WP-01", tool="Write", old=None):
        tinput = {"file_path": str(self.repo.dir / rel)}
        if tool == "Write":
            tinput["content"] = content
        else:
            tinput.update(new_string=content, old_string=old or "")
        return self.repo.hook({"tool_name": tool, "tool_input": tinput}, wp=wp)

    def test_no_verify_blocked_for_everyone(self):
        for cmd in ["git commit --no-verify -m x", "git commit -n -m x", "git -c core.hooksPath=/dev/null commit -m x", "sh -c 'git commit --no-verify'",
                    "git push --no-verify"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(self.repo.hook(bash_payload(cmd)), 2, "in the main session too")
        for cmd in ["git commit -m 'arregla -n'", "git log -n 5", "git commit -m x"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(self.repo.hook(bash_payload(cmd)), 0)

    def test_existing_config_is_protected(self):
        self.repo.write("pyproject.toml", "[tool.ruff]\nline-length = 88\n")
        self.assertEqual(self.w("pyproject.toml", "[tool.ruff]\nline-length = 300\n"), 2)
        self.assertEqual(self.w("setup.cfg", "x"), 0, "creating new configuration is allowed")
        self.repo.write("ruff.toml", "a")
        self.assertEqual(self.w("ruff.toml", "b", wp="WP-02"), 0, "config_edit: true lo permite")

    def test_tests_cannot_be_disabled(self):
        self.assertEqual(self.w("tests/test_a.py", "import unittest\n@unittest.skip('x')\ndef test(): pass\n"), 2)
        self.assertEqual(self.w("tests/test_a.py", "import pytest\n@pytest.mark.xfail\ndef test(): pass\n"), 2)
        self.assertEqual(self.w("tests/test_a.py", "it.only('x', () => {})", tool="Edit"), 2)
        self.assertEqual(self.w("tests/test_a.py", "@unittest.skipIf(os.geteuid() == 0, 'root')\ndef test(): pass\n"), 0)
        self.assertEqual(self.w("src/a.py", "# pytest.mark.skip in a code comment\n"), 0, "only in test files")
        self.assertEqual(self.w("tests/test_a.py", "@unittest.skip('was already there')\nX", tool="Edit",
                                old="@unittest.skip('was already there')\n"), 0, "what already existed does not count")

    def test_secrets(self):
        for secret in ["AKIA" + "A" * 16, "ghp_" + "a" * 36, "sk-ant-" + "a" * 30,
                       "-----BEGIN RSA PRIVATE KEY-----", "xoxb-" + "1" * 12]:
            with self.subTest(secret=secret[:8]):
                self.assertEqual(self.w("src/a.py", f"TOKEN = '{secret}'\n"), 2)
        self.assertEqual(self.w("src/a.py", "TOKEN = os.environ['API_TOKEN']\n"), 0)


class WorkflowCommandTests(Base):
    """Deterministic commands the native workflow uses through the gatekeeper agent."""

    def test_open_wave_snapshot_and_json(self):
        self.repo.hook(bash_payload("python3 .claude/bin/agentic.py claim WP-01", agent_id="old"))
        r = self.repo.run("open-wave", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), {"wave": 1, "work_packages": ["WP-01", "WP-02"]}, "only JSON on stdout")
        self.assertTrue((self.repo.dir / ".claude/state/snapshot-1.json").exists())
        self.assertEqual(self.repo.hook(bash_payload("python3 .claude/bin/agentic.py claim WP-01", agent_id="new")), 0,
                         "open-wave resets the previous stage's claims")
        self.assertEqual(self.repo.run("open-wave", "9").returncode, 3)

    def test_close_wave(self):
        self.repo.run("open-wave", "1")
        self.repo.write("src/a.py")
        r = self.repo.run("close-wave", "1", "WP-01=done", "WP-02=done")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        st = self.repo.run("status").stdout
        self.assertIn("wave 1: OK", st)
        self.assertIn("[done]", st)
        self.repo.run("open-wave", "1")
        self.assertEqual(self.repo.run("close-wave", "1", "WP-01=done", "WP-02=blocker").returncode, 3)
        self.assertIn("wave 1: REVIEW", self.repo.run("status").stdout)
        self.repo.run("open-wave", "1")
        self.repo.write("README.md")
        self.assertEqual(self.repo.run("close-wave", "1", "WP-01=done", "WP-02=done").returncode, 3, "escritura ajena")
        self.assertEqual(self.repo.run("close-wave", "1", "WP-77=done").returncode, 1)


class PipelineCommandTests(Base):
    """Commands the agentic-implement workflow uses (run by the gatekeeper)."""
    manifest = dict(MANIFEST, verify={"tests": "python3 ok.py", "lint": "python3 lint.py"})

    def setUp(self):
        super().setUp()
        self.repo.write("ok.py", "print('tests ok')\n")
        self.repo.write("lint.py", "import sys\nsys.exit(0)\n")
        self.repo.write("acc.py", "import sys\nsys.exit(0)\n")

    def test_pipeline_plan(self):
        (self.repo.dir / "architecture.md").write_text(
            "# A\nType: fix · Size: small\n\n```json wp-manifest\n" + json.dumps(self.manifest) + "\n```\n")
        r = self.repo.run("pipeline-plan")
        self.assertEqual(r.returncode, 0, r.stderr)
        plan = json.loads(r.stdout)
        self.assertEqual(plan["waves"], [1, 2])
        self.assertEqual(plan["work_packages"]["1"], ["WP-01", "WP-02"])
        self.assertEqual(plan["type"], "fix")
        self.assertTrue(plan["acceptance"])
        self.assertEqual(plan["max_fix_cycles"], 2)
        self.assertEqual(plan["wave_of"]["WP-03"], 2)
        self.assertEqual(plan["agents"]["WP-01"], "implementer")

    def test_open_and_close_stage_with_labels(self):
        (self.repo.dir / "acc.py").write_text("import sys\nsys.exit(1)\n")  # before the snapshot: it belongs to nobody
        r = self.repo.run("open-stage", "acceptance")
        self.assertEqual(json.loads(r.stdout), {"stage": "acceptance", "owners": ["AT"]})
        self.repo.write("acceptance/test_ac_001.py")
        r = self.repo.run("close-stage", "acceptance", "AT=done", "--expect", "red")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("RED", r.stdout)
        (self.repo.dir / "acc.py").write_text("import sys\nsys.exit(0)\n")
        self.repo.run("open-stage", "acceptance")
        self.assertEqual(self.repo.run("close-stage", "acceptance", "AT=done", "--expect", "red").returncode, 3,
                         "if they already pass without code, wave 0 fails")
        self.assertEqual(self.repo.run("open-stage", "inventada").returncode, 1)

    def test_close_stage_fix_counts_cycles(self):
        for _ in range(2):
            self.repo.run("open-stage", "1")
            self.repo.run("close-stage", "1", "WP-02=done", "--fix")
        ledger = json.loads((self.repo.dir / ".claude/state/ledger.json").read_text())
        self.assertEqual(ledger["WP-02"]["fix_count"], 2)

    def test_verify_json(self):
        r = self.repo.run("verify")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = json.loads(r.stdout)
        names = [p["name"] for p in out["phases"]]
        self.assertEqual(names, ["build", "types", "lint", "tests", "acceptance"])
        self.assertTrue(out["phases"][0]["skipped"])
        self.repo.write("lint.py", "import sys\nsys.exit(2)\n")
        r = self.repo.run("verify")
        self.assertEqual(r.returncode, 3)
        self.assertFalse(json.loads(r.stdout)["ok"])

    def test_verify_malformed(self):
        m = dict(self.manifest, verify={"deploy": "x"})
        (self.repo.dir / "architecture.md").write_text("```json wp-manifest\n" + json.dumps(m) + "\n```")
        self.assertEqual(self.repo.run("validate").returncode, 1)

    def test_sync_permissions(self):
        local = self.repo.dir / ".claude/settings.local.json"
        local.write_text(json.dumps({"permissions": {"allow": ["Read"]}, "otra": 1}))
        r = self.repo.run("sync-permissions")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(local.read_text())
        allow = data["permissions"]["allow"]
        for rule in ["Read", "Workflow(agentic-implement)", "Bash(python3 -m unittest:*)", "Bash(python3 acc.py)",
                     "Bash(python3 ok.py:*)"]:
            self.assertIn(rule, allow)
        self.assertEqual(data["otra"], 1, "keeps the rest of the file")
        self.repo.run("sync-permissions")
        self.assertEqual(len(json.loads(local.read_text())["permissions"]["allow"]), len(allow), "idempotente")


class BrownfieldTests(Base):
    """PRP-style plan (validated mirror references) and the regression baseline."""
    manifest = dict(MANIFEST, verify={"tests": "python3 t.py", "lint": "python3 l.py"})

    def set_manifest(self, m):
        (self.repo.dir / "architecture.md").write_text("```json wp-manifest\n" + json.dumps(m) + "\n```")

    def test_mirror_validated(self):
        self.repo.write("legacy/orders.py", "\n".join(f"linea {i}" for i in range(1, 41)))
        cases = {
            "legacy/orders.py": 0, "legacy/orders.py:5": 0, "legacy/orders.py:1-40": 0,
            "legacy/orders.py:30-41": 1, "legacy/invented.py": 1, "legacy/orders.py:abc": 1, "../outside.py": 1,
            "legacy/orders.py:20-10": 1,
        }
        for ref, expected in cases.items():
            with self.subTest(ref=ref):
                m = json.loads(json.dumps(self.manifest))
                m["work_packages"][0]["mirror"] = [ref]
                self.set_manifest(m)
                r = self.repo.run("validate")
                self.assertEqual(r.returncode, expected, r.stderr)

    def scripts(self, tests, lint):
        self.repo.write("t.py", f"import sys\nsys.exit({tests})\n")
        self.repo.write("l.py", f"import sys\nsys.exit({lint})\n")
        self.repo.write("acc.py", "import sys\nsys.exit(0)\n")

    def test_regression_versus_existing_failure(self):
        self.scripts(tests=0, lint=1)  # before touching anything: lint already fails
        r = self.repo.run("baseline")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["preexisting_failures"], ["lint"])
        out = json.loads(self.repo.run("verify").stdout)
        self.assertTrue(out["ok"], "a pre-existing failure does not block")
        self.assertEqual(out["preexisting_failures"], ["lint"])
        self.scripts(tests=1, lint=1)  # now the tests, which passed, fail
        r = self.repo.run("verify")
        self.assertEqual(r.returncode, 3)
        out = json.loads(r.stdout)
        self.assertEqual(out["blocking"], ["tests"])
        status = {p["name"]: p.get("status") for p in out["phases"]}
        self.assertEqual(status["tests"], "regression")
        self.assertEqual(status["lint"], "pre-existing failure")
        self.assertIn("baseline: with existing failures: lint", self.repo.run("status").stdout)

    def test_without_baseline_a_failure_blocks(self):
        self.scripts(tests=0, lint=1)
        r = self.repo.run("verify")
        self.assertEqual(r.returncode, 3)
        self.assertEqual(json.loads(r.stdout)["blocking"], ["lint"])

    def test_baseline_with_nonexistent_command_fails(self):
        m = dict(self.manifest, verify={"tests": "command-that-does-not-exist"})
        self.set_manifest(m)
        self.assertEqual(self.repo.run("baseline").returncode, 1)

    def test_save_result(self):
        payload = {"result": "OK", "qa": {"status": "PASS"}, "note": "ñandú"}
        r = self.repo.run("save-result", stdin=json.dumps(payload))
        self.assertEqual(r.returncode, 0, r.stderr)
        saved = json.loads((self.repo.dir / ".claude/state/runs/workflow-result.json").read_text())
        self.assertEqual(saved, payload)
        self.assertEqual(self.repo.run("save-result", stdin="not json").returncode, 1)
        (self.repo.dir / ".agentic-result.json").write_text(json.dumps({"result": "REVIEW"}))
        r = self.repo.run("save-result", ".agentic-result.json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.repo.dir / ".agentic-result.json").exists(), "the temporary file is deleted")
        self.assertEqual(json.loads((self.repo.dir / ".claude/state/runs/workflow-result.json").read_text())["result"], "REVIEW")
        self.repo.write("src/x.json", "{}")
        self.assertEqual(self.repo.run("save-result", "src/x.json").returncode, 1, "only from the root")

    def test_ledger_order(self):
        self.scripts(0, 0)
        self.repo.run("baseline")
        self.repo.run("open-stage", "1")
        self.repo.run("close-stage", "1", "WP-01=done", "WP-02=done")
        self.repo.run("verify")
        st = self.repo.run("status").stdout
        self.assertLess(st.index("baseline"), st.index("wave 1"))
        self.assertLess(st.index("wave 1"), st.index("verification"))

    def test_pipeline_plan_reports_baseline(self):
        self.scripts(0, 0)
        self.assertTrue(json.loads(self.repo.run("pipeline-plan").stdout)["baseline"])


class RulesTests(Base):
    """Stack detection and per-language rules (rules-sync)."""

    def setUp(self):
        super().setUp()
        shutil.copytree(SCRIPT.parents[1] / "rules-library", self.repo.dir / ".claude" / "rules-library")

    def set_manifest(self, m):
        (self.repo.dir / "architecture.md").write_text("```json wp-manifest\n" + json.dumps(m) + "\n```")

    def installed(self):
        return sorted(p.stem for p in (self.repo.dir / ".claude/rules/agentic").glob("*.md"))

    def test_detects_by_files_and_extensions_ignoring_dependencies(self):
        self.repo.write("api/go.mod", "module x")
        self.repo.write("web/src/app.tsx", "export const A = 1")
        self.repo.write("node_modules/dep/index.rb", "puts 1")  # dependencias: no cuentan
        self.repo.write(".claude/bin/tool.py", "x = 1")  # the flow itself: does not count
        detected = json.loads(self.repo.run("stack").stdout)["detected"]
        self.assertEqual(sorted(detected), ["go", "typescript"])
        self.assertEqual(detected["go"]["markers"], ["api/go.mod"])

    def test_rules_sync_installs_common_declared_and_detected_and_removes_the_rest(self):
        self.set_manifest(dict(MANIFEST, stack=["rust"]))
        self.repo.write("src/a.py", "def f(): pass")
        out = json.loads(self.repo.run("rules-sync").stdout)
        self.assertEqual(self.installed(), ["common", "python", "rust"])
        self.assertEqual(out["detected"], ["python"])
        (self.repo.dir / "src/a.py").unlink()
        self.set_manifest(dict(MANIFEST, stack=["go"]))
        out = json.loads(self.repo.run("rules-sync").stdout)
        self.assertEqual(self.installed(), ["common", "go"])
        self.assertEqual(sorted(out["removed"]), ["python", "rust"])
        lib = (self.repo.dir / ".claude/rules-library/go.md").read_text()
        self.assertEqual((self.repo.dir / ".claude/rules/agentic/go.md").read_text(), lib, "copia exacta: paths: arriba")

    def test_validate_rejects_a_stack_without_rules(self):
        self.set_manifest(dict(MANIFEST, stack=["cobol"]))
        r = self.repo.run("validate")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no rules for 'cobol'", r.stderr)
        self.set_manifest(dict(MANIFEST, stack="python"))
        self.assertEqual(self.repo.run("validate").returncode, 1)

    def test_pipeline_plan_lists_installed_rules(self):
        self.set_manifest(dict(MANIFEST, stack=["python"]))
        self.assertEqual(json.loads(self.repo.run("pipeline-plan").stdout)["rules"], [])
        self.repo.run("rules-sync")
        self.assertEqual(json.loads(self.repo.run("pipeline-plan").stdout)["rules"],
                         [".claude/rules/agentic/common.md", ".claude/rules/agentic/python.md"])


class CustomAgentTests(Base):
    """A custom agent declared in a WP's "agent" must also claim before writing."""
    manifest = dict(MANIFEST, work_packages=[dict(MANIFEST["work_packages"][0], agent="implementer-rapido")]
                    + MANIFEST["work_packages"][1:])

    def test_custom_agent_without_claim_is_blocked(self):
        payload = dict(write_payload("src/a.py", agent_id="x1"), agent_type="implementer-rapido")
        self.assertEqual(self.repo.hook(payload), 2)
        self.repo.hook(dict(bash_payload("python3 .claude/bin/agentic.py claim WP-01", agent_id="x1"),
                            agent_type="implementer-rapido"))
        self.assertEqual(self.repo.hook(payload), 0)


class LivingDocsTests(Base):
    """Living documentation: the DOCS zone, the deterministic code map and docs-check."""
    manifest = dict(MANIFEST, docs={"files": ["README.md", "CHANGELOG.md", "docs/"]})

    def set_manifest(self, m):
        (self.repo.dir / "architecture.md").write_text("```json wp-manifest\n" + json.dumps(m) + "\n```")

    def test_validate_docs_zone(self):
        self.assertEqual(self.repo.run("validate").returncode, 0)
        m = json.loads(json.dumps(self.manifest))
        m["work_packages"][0]["files"].append("README.md")
        self.set_manifest(m)
        r = self.repo.run("validate")
        self.assertIn("two owners", r.stderr)
        m = dict(MANIFEST, docs={"files": ["README.md"], "codemap": "src/b/MAP.md"})
        self.set_manifest(m)
        self.assertIn("is generated by agentic.py", self.repo.run("validate").stderr)
        self.set_manifest(dict(MANIFEST, docs={"files": ["README.md"], "codemap": "docs/architecture/MAP.md"}))
        self.assertEqual(self.repo.run("validate").returncode, 1)
        self.set_manifest(dict(MANIFEST, docs={"files": ["README.md"], "codemap": None}))
        self.assertEqual(self.repo.run("validate").returncode, 0)

    def test_hook_docs_zone_and_paths_nobody_writes(self):
        self.repo.hook(bash_payload("python3 .claude/bin/agentic.py claim DOCS", agent_id="d1"))
        doc = lambda path: dict(write_payload(path, agent_id="d1"), agent_type="doc-updater")
        self.assertEqual(self.repo.hook(doc("README.md")), 0)
        self.assertEqual(self.repo.hook(doc("docs/guide.md")), 0)
        for path in ("src/a.py", "docs/CODEMAP.md", "docs/architecture/0001-x.md", "architecture.md"):
            with self.subTest(path=path):
                self.assertEqual(self.repo.hook(doc(path)), 2)

    def test_doc_updater_without_claim_is_blocked(self):
        payload = dict(write_payload("README.md", agent_id="d2"), agent_type="doc-updater")
        self.assertEqual(self.repo.hook(payload), 2)

    def test_codemap_deterministic_with_public_symbols(self):
        self.repo.write("src/a.py", "def publica():\n    pass\n\ndef _privada():\n    pass\n\nclass Modelo:\n    pass\n")
        self.repo.write("web/x.ts", "export function hola(): void {}\nconst interna = 1\nexport interface Opciones {}\n")
        self.repo.write("srv/main.go", "package main\nfunc Arranca() {}\nfunc interno() {}\nfunc (s *S) Metodo() {}\n")
        first = json.loads(self.repo.run("codemap").stdout)
        self.assertEqual(first["path"], "docs/CODEMAP.md")
        self.assertTrue(first["changed"])
        text = (self.repo.dir / "docs/CODEMAP.md").read_text()
        for sym in ("`publica`", "`Modelo`", "`hola`", "`Opciones`", "`Arranca`", "`Metodo`"):
            self.assertIn(sym, text)
        for hidden in ("_privada", "interna", "interno"):
            self.assertNotIn(hidden, text)
        self.assertFalse(json.loads(self.repo.run("codemap").stdout)["changed"], "no code changes, no diff")

    def test_docs_check_links_anchors_and_paths(self):
        self.repo.write("src/a.py", "x = 1")
        self.repo.write("docs/guide.md", "# Guide\n\n## Quick start\n")
        self.repo.write("README.md", "[ok](docs/guide.md#quick-start) and `src/a.py`\n```\n`src/no.py` in a block\n```\n")
        r = self.repo.run("docs-check")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.repo.write("README.md", "[roto](docs/nada.md) [ancla](docs/guide.md#nada) `src/falta.py` `http/v1/x`\n")
        r = self.repo.run("docs-check")
        self.assertEqual(r.returncode, 3)
        kinds = sorted(b["kind"] for b in json.loads(r.stdout)["broken"])
        self.assertEqual(kinds, ["anchor", "link", "path"], "`http/v1/x` is not a folder of the repo: it does not count")

    def test_docs_check_ignores_the_decision_archive(self):
        self.repo.write("docs/architecture/0001-x.md", "[old](../../src/deleted.py)\n")
        self.assertEqual(self.repo.run("docs-check").returncode, 0)

    def test_pipeline_plan_and_docs_stage(self):
        plan = json.loads(self.repo.run("pipeline-plan").stdout)
        self.assertTrue(plan["docs"])
        self.assertEqual(plan["codemap"], "docs/CODEMAP.md")
        r = self.repo.run("open-stage", "docs")
        self.assertEqual(json.loads(r.stdout)["owners"], ["DOCS"])
        self.repo.write("README.md", "# new")
        self.assertEqual(self.repo.run("close-stage", "docs", "DOCS=done").returncode, 0)
        self.repo.run("open-stage", "docs")
        self.repo.write("src/a.py", "x = 2")
        self.assertEqual(self.repo.run("close-stage", "docs", "DOCS=done").returncode, 3, "outside its zone")

    def test_no_docs_section_means_no_stage(self):
        self.set_manifest(MANIFEST)
        self.assertFalse(json.loads(self.repo.run("pipeline-plan").stdout)["docs"])
        self.assertEqual(self.repo.run("open-stage", "docs").returncode, 3)


class InstallTests(unittest.TestCase):
    """install: what the plugin does when installing or updating the flow in another repo."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="agentic-install-"))
        subprocess.run(["git", "init", "-q"], cwd=self.dir, check=True)
        (self.dir / ".claude").mkdir()
        (self.dir / ".claude/settings.json").write_text(json.dumps(
            {"permissions": {"allow": ["Bash(make test)"]}, "hooks": {"PostToolUse": []}, "model": "sonnet"}))
        (self.dir / ".gitignore").write_text("dist/\n")
        (self.dir / "main.py").write_text("print(1)\n")
        self.env = dict(os.environ)
        self.env.pop("CLAUDE_PROJECT_DIR", None)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def install(self, *flags):
        return subprocess.run([sys.executable, str(SCRIPT), "install", str(self.dir), *flags], capture_output=True,
                              text=True, env=self.env)

    def test_installs_the_flow_and_keeps_project_configuration(self):
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stderr)
        for rel in (".claude/bin/agentic.py", ".claude/agents/implementer.md", ".claude/commands/architect.md",
                    ".claude/workflows/agentic-implement.js", ".claude/rules-library/common.md",
                    ".claude/rules/agentic/python.md", ".claude/agentic.json", ".claude/agentic-install.json"):
            self.assertTrue((self.dir / rel).exists(), rel)
        self.assertFalse((self.dir / ".claude/bin/test_agentic.py").exists(), "the flow's tests are not installed")
        settings = json.loads((self.dir / ".claude/settings.json").read_text())
        self.assertEqual(settings["model"], "sonnet")
        self.assertIn("Bash(make test)", settings["permissions"]["allow"])
        self.assertIn("Workflow(agentic-implement)", settings["permissions"]["allow"])
        self.assertIn("Bash(rm -rf:*)", settings["permissions"]["deny"])
        self.assertTrue(settings["enableWorkflows"])
        self.assertEqual(len(settings["hooks"]["PreToolUse"]), 1)
        self.assertIn(".claude/state/", (self.dir / ".gitignore").read_text().splitlines())
        self.assertTrue((self.dir / ".gitignore").read_text().startswith("dist/\n"))

    def test_reinstalling_is_idempotent(self):
        self.install()
        before = {p: p.read_bytes() for p in self.dir.rglob("*") if p.is_file() and ".git/" not in p.as_posix()}
        r = self.install()
        self.assertIn("settings.json: unchanged", r.stdout)
        after = {p: p.read_bytes() for p in self.dir.rglob("*") if p.is_file() and ".git/" not in p.as_posix()}
        self.assertEqual(before, after)

    def test_update_does_not_overwrite_your_changes_except_with_force(self):
        self.install()
        mine = self.dir / ".claude/agents/reviewer.md"
        mine.write_text(mine.read_text() + "\nTeam rule of our own.\n")
        stale = self.dir / ".claude/agents/implementer.md"
        stale.write_text("old version, untouched")
        record = json.loads((self.dir / ".claude/agentic-install.json").read_text())
        record["files"][".claude/agents/implementer.md"] = hashlib.sha256(b"old version, untouched").hexdigest()
        (self.dir / ".claude/agentic-install.json").write_text(json.dumps(record))
        r = self.install()
        self.assertIn("modified by you (left alone): 1", r.stdout)
        self.assertIn("Team rule of our own.", mine.read_text())
        self.assertNotEqual(stale.read_text(), "old version, untouched", "what was not modified gets updated")
        self.assertIn("modified by you", self.install().stdout, "still protected on the next update")
        self.install("--force")
        self.assertNotIn("Team rule of our own.", mine.read_text())

    def test_old_copy_without_record_is_kept_and_rules_are_installed(self):
        old = self.dir / ".claude/bin/agentic.py"
        old.parent.mkdir(parents=True)
        old.write_text("import sys\nprint('old version')\nsys.exit(1)\n")
        r = self.install()
        self.assertIn("modified by you (left alone): 1", r.stdout)
        self.assertIn("old version", old.read_text(), "no record, different: not overwritten")
        self.assertIn("rules: common, python", r.stdout)
        self.assertTrue((self.dir / ".claude/rules/agentic/python.md").exists())

    def test_dry_run_writes_nothing(self):
        r = self.install("--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("dry run", r.stdout)
        self.assertFalse((self.dir / ".claude/bin/agentic.py").exists())

    def test_cannot_install_over_itself(self):
        r = subprocess.run([sys.executable, str(SCRIPT), "install", str(SCRIPT.parents[2])], capture_output=True,
                           text=True, env=self.env)
        self.assertEqual(r.returncode, 1)

    def test_installed_flow_works(self):
        self.install()
        (self.dir / "architecture.md").write_text("```json wp-manifest\n" + json.dumps(dict(MANIFEST, stack=["python"])) + "\n```")
        r = subprocess.run([sys.executable, ".claude/bin/agentic.py", "validate"], cwd=self.dir, capture_output=True,
                           text=True, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class WorkflowLogicTests(unittest.TestCase):
    def test_workflow_scenarios(self):
        r = subprocess.run(["node", str(SCRIPT.parent / "test_workflow.mjs")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class HookFailClosedTests(unittest.TestCase):
    def setUp(self):
        self.repo = Repo(raw_manifest='{"work_packages": [ roto')

    def tearDown(self):
        self.repo.cleanup()

    def test_broken_manifest_blocks_inside_a_wp(self):
        self.assertEqual(self.repo.hook(write_payload(str(self.repo.dir / "src/a.py")), wp="WP-01"), 2)

    def test_broken_manifest_blocks_subagent(self):
        self.assertEqual(self.repo.hook(write_payload(str(self.repo.dir / "src/a.py"), agent_id="ag1")), 2)

    def test_broken_manifest_does_not_block_main_thread(self):
        self.assertEqual(self.repo.hook(write_payload(str(self.repo.dir / "src/a.py"))), 0)

    def test_unreadable_stdin_inside_a_wp(self):
        r = self.repo.run("hook", stdin="not json", env={"AGENTIC_WP": "WP-01"})
        self.assertEqual(r.returncode, 2)


class HookBashTests(Base):
    BLOCKED = [
        "git commit -am x", "git -C . commit -am x", "git -c user.name=x commit -m y", "command git push",
        "sh -c 'git commit -m x'", 'bash -c "git checkout main"', "cd src && git add .", "git stash",
        "npm install lodash", "cd x && pnpm add zod", "pip install requests", "uv add httpx",
        "claude -p hola", "claude --print hola",
    ]
    ALLOWED = [
        "git status", "git diff src/add.py", "git log --oneline -5", "git show HEAD:src/a.py",
        "python3 -m unittest tests.test_a", "npm test", "npm run build", "grep -r commit src",
        "claude --version",
    ]

    def test_blocked(self):
        for cmd in self.BLOCKED:
            with self.subTest(cmd=cmd):
                self.assertEqual(self.repo.hook(bash_payload(cmd), wp="WP-01"), 2)

    def test_allowed(self):
        for cmd in self.ALLOWED:
            with self.subTest(cmd=cmd):
                self.assertEqual(self.repo.hook(bash_payload(cmd), wp="WP-01"), 0)

    def test_main_thread_may_use_git(self):
        self.assertEqual(self.repo.hook(bash_payload("git commit -am x")), 0)


class CheckStageTests(Base):
    def setUp(self):
        super().setUp()
        self.repo.write("src/a.py", "v1")
        subprocess.run(["git", "add", "-A"], cwd=self.repo.dir, check=True)
        self.repo.run("snapshot", "1")

    def test_own_changes_ok(self):
        self.repo.write("src/a.py", "v2")
        self.repo.write("src/b/new.py")
        self.assertEqual(self.repo.run("check-wave", "1").returncode, 0)

    def test_foreign_change_and_deletion_fail(self):
        self.repo.write("README.md")
        r = self.repo.run("check-wave", "1")
        self.assertEqual(r.returncode, 3)
        self.assertIn("README.md", r.stdout)

    def test_deletion_counts_as_change(self):
        (self.repo.dir / "src/a.py").unlink()
        self.repo.run("snapshot", "2")
        self.repo.write("src/a.py", "v1")
        self.assertEqual(self.repo.run("check-wave", "2").returncode, 3, "src/a.py belongs to wave 1, not 2")

    def test_ignored_artifacts(self):
        self.repo.write("src/__pycache__/a.cpython-314.pyc")
        self.repo.write("node_modules/x/index.js")
        self.repo.write("dist/out.js")  # ignored by the manifest
        self.repo.write("coverage/lcov.info")
        self.assertEqual(self.repo.run("check-wave", "1").returncode, 0)

    def test_next_wave_cannot_touch_the_previous(self):
        self.repo.run("snapshot", "2")
        self.repo.write("src/a.py", "v3")
        self.assertEqual(self.repo.run("check-wave", "2").returncode, 3)


class LauncherTests(Base):
    def setUp(self):
        super().setUp()
        self.bin = Path(tempfile.mkdtemp(prefix="fake-claude-"))
        fake = self.bin / "claude"
        fake.write_text(FAKE_CLAUDE)
        fake.chmod(0o755)
        self.logs = Path(tempfile.mkdtemp(prefix="fake-logs-"))
        self.repo.env.update(PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}", FAKE_LOG_DIR=str(self.logs))

    def tearDown(self):
        super().tearDown()
        shutil.rmtree(self.bin, ignore_errors=True)
        shutil.rmtree(self.logs, ignore_errors=True)

    def args(self, wp):
        return json.loads((self.logs / f"{wp}.args").read_text())

    def test_wave_runs_in_parallel(self):
        start = time.time()
        r = self.repo.run("run-wave", "1", env={"FAKE_SLEEP": "1.5"})
        elapsed = time.time() - start
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertLess(elapsed, 2.8, "the two WPs should run at the same time")
        self.assertNotIn("USD", r.stdout, "no cost information")

    def test_flags_of_each_agent(self):
        self.repo.run("run-wave", "1")
        a = self.args("WP-01")
        self.assertEqual(a[a.index("--agent") + 1], "implementer")
        self.assertEqual(a[a.index("--model") + 1], "sonnet")
        self.assertNotIn("--max-budget-usd", a)
        self.assertIn("--strict-mcp-config", a)
        self.assertIn("Bash(python3 -m unittest:*)", a)
        self.assertNotIn("Bash", a, "never unrestricted Bash")
        self.assertFalse((self.logs / "WP-03.args").exists(), "WP-03 belongs to wave 2")

    def test_model_cannot_be_changed(self):
        (self.repo.dir / ".claude/agentic.json").write_text(json.dumps({"models": {"qa": "claude-opus-5-5"}}))
        self.repo.run("run-qa", env={"AGENTIC_QA_MODEL": "opus", "AGENTIC_IMPLEMENTER_MODEL": "opus"})
        a = self.args("QA")
        self.assertEqual(a[a.index("--model") + 1], "sonnet")
        self.assertEqual(a[a.index("--agent") + 1], "qa")

    def test_agent_with_another_model_does_not_validate(self):
        f = self.repo.dir / ".claude/agents/qa.md"
        f.write_text(f.read_text().replace("model: sonnet", "model: claude-opus-5-5"))
        r = self.repo.run("run-wave", "1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("model: sonnet", r.stderr)
        self.assertFalse((self.logs / "WP-01.args").exists())

    def test_architect_must_be_opus(self):
        cmds = self.repo.dir / ".claude/commands"
        cmds.mkdir(parents=True, exist_ok=True)
        (cmds / "architect.md").write_text("---\ndescription: x\nmodel: opus\n---\nx")
        self.assertEqual(self.repo.run("validate").returncode, 0)
        (cmds / "architect.md").write_text("---\ndescription: x\nmodel: claude-opus-5-5\n---\nx")
        r = self.repo.run("validate")
        self.assertEqual(r.returncode, 1)
        self.assertIn("latest Opus", r.stderr)

    def test_real_definitions_use_aliases(self):
        sys.path.insert(0, str(SCRIPT.parent))
        import agentic
        root = SCRIPT.parents[2]
        self.assertEqual(agentic.frontmatter_model(root / ".claude/commands/architect.md"), "opus")
        for name in ("implementer", "qa"):
            self.assertEqual(agentic.frontmatter_model(root / f".claude/agents/{name}.md"), "sonnet")
        for name in ("implement",):
            self.assertEqual(agentic.frontmatter_model(root / f".claude/commands/{name}.md"), "sonnet")

    def test_launcher_must_be_sonnet(self):
        cmds = self.repo.dir / ".claude/commands"
        cmds.mkdir(parents=True, exist_ok=True)
        (cmds / "implement.md").write_text("---\ndescription: x\nmodel: opus\n---\nx")
        r = self.repo.run("validate")
        self.assertEqual(r.returncode, 1)
        self.assertIn("/implement", r.stderr)

    def test_specialised_agent_must_exist_and_be_sonnet(self):
        m = json.loads(json.dumps(MANIFEST))
        m["work_packages"][0]["agent"] = "frontend"
        (self.repo.dir / "architecture.md").write_text(f"```json wp-manifest\n{json.dumps(m)}\n```")
        r = self.repo.run("validate")
        self.assertIn("does not exist", r.stderr)
        (self.repo.dir / ".claude/agents/frontend.md").write_text("---\nname: frontend\nmodel: haiku\n---\nx")
        self.assertIn("not 'haiku'", self.repo.run("validate").stderr)
        (self.repo.dir / ".claude/agents/frontend.md").write_text("---\nname: frontend\nmodel: sonnet\n---\nx")
        self.assertEqual(self.repo.run("validate").returncode, 0)

    def env_of(self, wp):
        return json.loads((self.logs / f"{wp}.env").read_text())

    def test_subscription_does_not_inherit_api_key(self):
        self.repo.run("run-wave", "1", env={"ANTHROPIC_API_KEY": "sk-test", "ANTHROPIC_AUTH_TOKEN": "tok"})
        self.assertEqual(self.env_of("WP-01"), {"ANTHROPIC_API_KEY": None, "ANTHROPIC_AUTH_TOKEN": None})

    def test_auth_inherit_keeps_api_key(self):
        (self.repo.dir / ".claude").mkdir(exist_ok=True)
        (self.repo.dir / ".claude/agentic.json").write_text(json.dumps({"auth": "inherit"}))
        self.repo.run("run-wave", "1", env={"ANTHROPIC_API_KEY": "sk-test"})
        self.assertEqual(self.env_of("WP-01")["ANTHROPIC_API_KEY"], "sk-test")

    def test_only_one_wp(self):
        r = self.repo.run("run-wave", "1", "WP-02")
        self.assertEqual(r.returncode, 0)
        self.assertFalse((self.logs / "WP-01.args").exists())
        self.assertEqual(self.repo.run("run-wave", "1", "WP-03").returncode, 1)

    def prompt_of(self, wp):
        return self.args(wp)[1]

    def test_fix_only_when_requested(self):
        runs = self.repo.dir / ".claude/state/runs"
        runs.mkdir(parents=True, exist_ok=True)
        (runs / "QA.json").write_text(json.dumps({"result": "STATUS: FAIL"}))
        self.repo.run("run-wave", "1", "WP-02")
        self.assertNotIn("QA.json", self.prompt_of("WP-02"), "without --fix it must not use an old QA report")
        self.repo.run("run-wave", "1", "WP-02", "--fix")
        self.assertIn("QA.json", self.prompt_of("WP-02"))

    def test_fix_without_report_fails(self):
        r = self.repo.run("run-wave", "1", "WP-01", "--fix")
        self.assertEqual(r.returncode, 1)
        self.assertIn("run-review or run-qa", r.stderr)

    # ---------- ledger, mark y stop ----------

    def status(self):
        return self.repo.run("status").stdout

    def test_ledger_after_a_wave(self):
        self.assertEqual(self.repo.run("run-wave", "1").returncode, 0)
        st = self.status()
        self.assertIn("[done]", st)
        self.assertIn("WP-01 (sonnet)", st)
        self.assertIn("mode fallback", st)
        self.assertIn("wave 1: OK", st)
        self.assertNotIn("WP-03", st, "WP-03 belongs to wave 2 and was not launched")

    def test_mark(self):
        r = self.repo.run("mark", "WP-02", "blocker", "missing", "the", "contract")
        self.assertEqual(r.returncode, 0, r.stderr)
        st = self.status()
        self.assertIn("[blocker]", st)
        self.assertIn("missing the contract", st)
        self.assertEqual(self.repo.run("mark", "WP-02", "raro").returncode, 1)
        self.assertEqual(self.repo.run("mark", "WP-99", "done").returncode, 1)

    def test_claim_records_workflow_mode(self):
        self.repo.hook(bash_payload("python3 .claude/bin/agentic.py claim WP-01", agent_id="ag1"))
        st = self.status()
        self.assertIn("[running]", st)
        self.assertIn("WP-01 (sonnet)", st)
        self.assertIn("mode workflow", st)

    def test_stop_stops_the_wave(self):
        proc = subprocess.Popen([sys.executable, str(SCRIPT), "run-wave", "1"], cwd=self.repo.dir,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                env=dict(self.repo.env, FAKE_SLEEP="30"))
        ledger = self.repo.dir / ".claude/state/ledger.json"
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                d = json.loads(ledger.read_text())
                if all(d.get(w, {}).get("pid") for w in ("WP-01", "WP-02")):
                    break
            except (OSError, json.JSONDecodeError):
                pass
            time.sleep(0.1)
        start = time.time()
        r = self.repo.run("stop")
        self.assertIn("stopped", r.stdout)
        out, _ = proc.communicate(timeout=15)
        self.assertLess(time.time() - start, 10, "stop must cut the wave without waiting for the agents")
        self.assertEqual(proc.returncode, 3)
        self.assertIn("STOPPED", out)
        self.assertIn("[stopped]", self.status())
        self.assertIn("No agents are running", self.repo.run("stop").stdout)

    def test_wave_0_launches_test_author(self):
        self.assertEqual(self.repo.run("run-acceptance").returncode, 0)
        a = self.args("AT")
        self.assertEqual(a[a.index("--agent") + 1], "test-author")
        self.assertEqual(a[a.index("--model") + 1], "sonnet")

    def test_review_without_write_tools(self):
        (self.logs / "x").write_text("")
        r = self.repo.run("run-review", env={"FAKE_STATUS": "PASS"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        a = self.args("REVIEW")
        self.assertEqual(a[a.index("--agent") + 1], "reviewer")
        for tool in ("Write", "Edit"):
            self.assertNotIn(tool, a)

    def test_implementers_may_run_acceptance(self):
        self.repo.run("run-wave", "1")
        self.assertIn("Bash(python3 acc.py:*)", self.args("WP-01"))

    def test_fix_limit(self):
        runs = self.repo.dir / ".claude/state/runs"
        runs.mkdir(parents=True, exist_ok=True)
        (runs / "REVIEW.json").write_text(json.dumps({"result": "STATUS: FAIL"}))
        for _ in range(2):
            self.assertEqual(self.repo.run("run-wave", "1", "WP-02", "--fix").returncode, 0)
            self.assertIn("REVIEW.json", self.prompt_of("WP-02"))
        r = self.repo.run("run-wave", "1", "WP-02", "--fix")
        self.assertEqual(r.returncode, 4)
        self.assertIn("used up", r.stderr)
        self.assertEqual(self.repo.run("run-wave", "1", "WP-01", "--fix").returncode, 0, "the limit is per WP")

    def test_timeout(self):
        start = time.time()
        r = self.repo.run("run-wave", "1", env={"FAKE_SLEEP": "30", "AGENTIC_TIMEOUT_S": "1"})
        self.assertLess(time.time() - start, 10)
        self.assertEqual(r.returncode, 3)
        self.assertIn("TIMEOUT", r.stdout)

    def test_blocker_marks_review(self):
        r = self.repo.run("run-wave", "1", env={"FAKE_STATUS": "BLOCKER"})
        self.assertEqual(r.returncode, 3)
        self.assertIn("RESULT: REVIEW", r.stdout)

    def test_invalid_manifest_does_not_launch(self):
        m = json.loads(json.dumps(MANIFEST))
        m["allow_bash"] = ["Bash(*)"]
        (self.repo.dir / "architecture.md").write_text(f"```json wp-manifest\n{json.dumps(m)}\n```")
        self.assertEqual(self.repo.run("run-wave", "1").returncode, 1)
        self.assertFalse((self.logs / "WP-01.args").exists())


if __name__ == "__main__":
    unittest.main()
