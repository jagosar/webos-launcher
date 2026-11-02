"""Tests of the guardrails and the launcher.

Run: python3 -m unittest discover -s .claude/bin -p "test_*.py"
Each test works in a temporary git repo; the launcher uses a fake `claude`, with no real calls.
"""
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parent / "agentic.py"
sys.path.insert(0, str(SCRIPT.parent))
import agentic  # noqa: E402
import testutil  # noqa: E402

# The manifests' commands run this interpreter: on Windows `python3` may be missing or the Store stub.
PY = shlex.join([Path(sys.executable).as_posix()])
AGENTS_DIR = SCRIPT.parents[1] / "agents"
WORKFLOWS_DIR = SCRIPT.parents[1] / "workflows"
SETTINGS = SCRIPT.parents[1] / "settings.json"
FINDING = {"severity": "high", "wp": "WP-02", "file": "src/b/x.py", "problem": "p", "evidence": "e"}

MANIFEST = {
    "allow_bash": ["Bash(python3 -m unittest:*)"],
    "ignore": ["dist/"],
    "acceptance": {"files": ["acceptance/"], "command": f"{PY} acc.py"},
    "work_packages": [
        {"id": "WP-01", "wave": 1, "depends_on": [], "files": ["src/a.py", "tests/test_a.py"]},
        {"id": "WP-02", "wave": 1, "depends_on": [], "files": ["src/b/"]},
        {"id": "WP-03", "wave": 2, "depends_on": ["WP-01"], "files": ["src/c.py"]},
    ],
}

class Repo:
    def __init__(self, manifest=MANIFEST, raw_manifest=None):
        self.dir = Path(tempfile.mkdtemp(prefix="agentic-test-"))
        body = raw_manifest if raw_manifest is not None else json.dumps(manifest, indent=2)
        (self.dir / "architecture.md").write_text(f"# A\n\n```json wp-manifest\n{body}\n```\n", encoding="utf-8")
        shutil.copytree(AGENTS_DIR, self.dir / ".claude" / "agents")
        shutil.copytree(WORKFLOWS_DIR, self.dir / ".claude" / "workflows")
        shutil.copy(SETTINGS, self.dir / ".claude" / "settings.json")  # hooks-check reads it
        subprocess.run(["git", "init", "-q"], cwd=self.dir, check=True)
        self.env = dict(os.environ, CLAUDE_PROJECT_DIR=str(self.dir))
        # CLAUDECODE: tests run inside Claude Code inherit it, and it would put hooks-check in live mode.
        for var in ("AGENTIC_WP", "AGENTIC_CLAUDE", "CLAUDECODE"):
            self.env.pop(var, None)

    def run(self, *args, stdin=None, env=None):
        return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=self.dir, input=stdin,
                              capture_output=True, text=True, env=dict(self.env, **(env or {})), encoding="utf-8", errors="replace")

    def hook(self, payload, wp=None):
        return self.run("hook", stdin=json.dumps(payload), env={"AGENTIC_WP": wp} if wp else None).returncode

    def as_agent(self, wp="WP-01"):
        """One tool call of a fallback agent through the hook: what a real agent's work leaves for the stage check."""
        return self.hook({"tool_name": "Bash", "tool_input": {"command": "true"}}, wp=wp)

    def write(self, rel, text="x"):
        p = self.dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def cleanup(self):
        shutil.rmtree(self.dir, ignore_errors=True)


def write_payload(path, agent_id=None, tool="Write"):
    data = {"tool_name": tool, "tool_input": {"file_path": path}}
    if agent_id:
        data.update(agent_id=agent_id, agent_type="implementer")
    return data


def report_payload(status, wp="WP-01", agent_id=None, agent_type="implementer"):
    """What the hook sees when an agent submits its report with StructuredOutput: the report is the tool input."""
    data = {"tool_name": "StructuredOutput", "agent_type": agent_type,
            "tool_input": {"status": status, "wp": wp, "files": [], "checks": [], "internal_decisions": [],
                           "blocker": None, "not_checked": [], "concerns": [], "missing_context": None}}
    if agent_id:
        data["agent_id"] = agent_id
    return data


def bash_payload(command, agent_id=None, agent_type="implementer"):
    data = {"tool_name": "Bash", "tool_input": {"command": command}}
    if agent_id:
        data.update(agent_id=agent_id, agent_type=agent_type)
    return data


class Base(unittest.TestCase):
    manifest = MANIFEST

    def setUp(self):
        self.repo = Repo(self.manifest)

    def tearDown(self):
        self.repo.cleanup()


class CommandTimeoutTests(unittest.TestCase):
    """A manifest command that exceeds its time is ended with every process it started: `npm test` leaves `vitest`
    running otherwise, competing with the next step (and on Windows, subprocess.run would wait for it)."""

    def test_a_timeout_ends_the_whole_process_tree(self):
        import agentic
        tmp = Path(tempfile.mkdtemp(prefix="cmd-timeout-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        marker = tmp / "grandchild-survived"
        script = tmp / "parent.py"
        script.write_text(textwrap.dedent(f"""\
            import subprocess, sys, time
            subprocess.Popen([sys.executable, "-c",
                              "import time, pathlib; time.sleep(1.5); pathlib.Path({str(marker)!r}).write_text('x')"])
            time.sleep(60)
        """), encoding="utf-8")
        start = time.time()
        code, tail = agentic.run_command(shlex.join([Path(sys.executable).as_posix(), script.as_posix()]), timeout=1)
        self.assertEqual(code, 124, tail)
        self.assertLess(time.time() - start, 15, "it must return soon after the timeout")
        time.sleep(2)
        self.assertFalse(marker.exists(), "the grandchild kept running after the timeout")


class UsageLockTests(unittest.TestCase):
    """On Windows a lock is mandatory: reading usage.jsonl while a finishing agent appends to it under the lock raises
    PermissionError, so every reader must take the lock too."""

    def test_usage_records_are_read_under_the_lock(self):
        import agentic
        tmp = Path(tempfile.mkdtemp(prefix="usage-lock-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        usage = tmp / "usage.jsonl"
        usage.write_text('{"id": "WP-01"}\n', encoding="utf-8")
        locked_paths, real_locked = [], agentic.compat.locked

        def spy(fh):
            locked_paths.append(Path(fh.name).name)
            return real_locked(fh)

        with mock.patch.object(agentic, "USAGE", usage), mock.patch.object(agentic.compat, "locked", spy):
            self.assertEqual(agentic.usage_records(), [{"id": "WP-01"}])
        self.assertIn("usage.jsonl", locked_paths)


class ValidateTests(unittest.TestCase):
    def errors(self, manifest):
        sys.path.insert(0, str(SCRIPT.parent))
        import agentic
        return agentic.validate(manifest)

    def with_wp(self, **fields):
        m = json.loads(json.dumps(MANIFEST))
        m["work_packages"][0].update(fields)
        return m

    def test_paths_must_be_written_as_the_repo_shows_them(self):
        # The hook and close-stage compare paths as text: './src/a.py' would never match the file it means.
        for f in ("./src/a.py", "src//a.py", "src\\a.py", "./.claude/x", "./architecture.md"):
            with self.subTest(f=f):
                self.assertTrue(self.errors(self.with_wp(files=[f])), f)

    def test_wrong_types_are_errors_not_crashes(self):
        for fields in ({"files": "src/a.py"}, {"files": [None]}, {"wave": "1"}, {"depends_on": "WP-02"}):
            with self.subTest(fields=fields):
                self.assertTrue(self.errors(self.with_wp(**fields)))
        m = self.with_wp(wave="1")
        m["work_packages"][2]["depends_on"] = ["WP-01"]
        self.assertTrue(self.errors(m))

    def test_commands_that_would_allow_anything_are_rejected(self):
        # The acceptance and verify commands become permissions: 'python3' alone would mean Bash(python3:*).
        self.assertTrue(self.errors(dict(MANIFEST, acceptance={"files": ["acceptance/"], "command": "python3"})))
        self.assertTrue(self.errors(dict(MANIFEST, verify={"tests": "bash"})))
        self.assertEqual(self.errors(dict(MANIFEST, verify={"tests": "npm test"})), [])

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
        self.assertEqual(self.repo.hook(write_payload(str(Path(tempfile.gettempdir()) / "outside.py")), wp="WP-01"), 2)
        self.assertEqual(self.repo.hook(write_payload(str(self.repo.dir / "src/../src/b/x.py")), wp="WP-01"), 2)

    def test_qa_only_its_zone(self):
        self.assertEqual(self.repo.hook(write_payload(str(self.repo.dir / "qa/t.py")), wp="QA"), 0)
        self.assertEqual(self.repo.hook(write_payload(str(self.repo.dir / "src/a.py")), wp="QA"), 2)

    def test_workflow_mode_needs_claim(self):
        self.assertEqual(self.repo.hook(write_payload(str(self.repo.dir / "src/a.py"), agent_id="ag1")), 2)
        self.assertEqual(self.repo.hook(bash_payload("python3 .claude/bin/agentic.py claim WP-01", agent_id="ag1")), 0)
        self.assertEqual(self.repo.hook(write_payload(str(self.repo.dir / "src/a.py"), agent_id="ag1")), 0)
        self.assertEqual(self.repo.hook(write_payload(str(self.repo.dir / "src/c.py"), agent_id="ag1")), 2)

    def test_a_claim_is_only_the_whole_command(self):
        # A claim inside a longer command must not carry the rest past the Bash rules.
        claim = "python3 .claude/bin/agentic.py claim WP-01"
        self.assertEqual(self.repo.hook(bash_payload(claim, agent_id="ag1")), 0)
        for cmd in (f"{claim} && git push origin HEAD --force", "git commit -am x # agentic claim WP-01",
                    f"git commit -am x; {claim}"):
            self.assertEqual(self.repo.hook(bash_payload(cmd, agent_id="ag1")), 2, cmd)
        self.assertEqual(self.repo.hook(bash_payload("sh .claude/bin/agentic claim WP-02", agent_id="ag2")), 0)

    def test_a_claim_matches_the_agent_type(self):
        # An implementer claims a package, QA claims QA, and so on: no agent takes another kind's zone.
        cases = [("implementer", "QA", 2), ("qa", "WP-01", 2), ("test-author", "REVIEW", 2), ("doc-updater", "WP-02", 2),
                 ("qa", "QA", 0), ("verifier", "REVIEW", 0), ("test-auditor", "REVIEW", 0)]
        for i, (kind, ident, expected) in enumerate(cases):
            with self.subTest(kind=kind, ident=ident):
                payload = bash_payload(f"python3 .claude/bin/agentic.py claim {ident}", agent_id=f"k{i}", agent_type=kind)
                self.assertEqual(self.repo.hook(payload), expected)
                self.repo.run("snapshot", "1")

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
        for ident, agent, kind in (("AT", "a1", "test-author"), ("REVIEW", "a2", "reviewer")):
            self.assertEqual(self.repo.hook(bash_payload(f"python3 .claude/bin/agentic.py claim {ident}", agent_id=agent,
                                                         agent_type=kind)), 0)
        self.assertEqual(self.repo.hook(write_payload(self.p("acceptance/t.py"), agent_id="a1")), 0)
        self.assertEqual(self.repo.hook(write_payload(self.p("src/a.py"), agent_id="a2")), 2)

    def write_manifest(self, m):
        (self.repo.dir / "architecture.md").write_text(f"```json wp-manifest\n{json.dumps(m)}\n```", encoding="utf-8")

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
                original = f.read_text(encoding="utf-8")
                good = "model: opus" if name == "plan-reviewer" else "model: sonnet"
                f.write_text(original.replace(good, f"model: {wrong}"), encoding="utf-8")
                r = self.repo.run("validate")
                self.assertEqual(r.returncode, 1, r.stderr)
                self.assertIn(name, r.stderr)
                f.write_text(original, encoding="utf-8")
        self.assertEqual(self.repo.run("validate").returncode, 0)

    def acc(self, code):
        (self.repo.dir / "acc.py").write_text(f"import sys\nprint('tests')\nsys.exit({code})\n", encoding="utf-8")

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
        for cmd in ["git commit -nm x", "git commit -an -m x", "git commit --no-verif -m x", "git commit --no-veri -m x",
                    'git -c "core.hooksPath=/dev/null" commit -m x',
                    "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=/dev/null git commit -m x"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(self.repo.hook(bash_payload(cmd)), 2, "a variant of the same bypass")
        for cmd in ["git commit -m 'arregla -n'", "git log -n 5", "git commit -m x", "git config core.hooksPath .githooks",
                    "git commit --amend -m x"]:
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

    def test_unconditional_skips_in_other_forms(self):
        for body in ["self.skipTest('x')", "raise unittest.SkipTest('x')", "@unittest.skipIf(True, 'x')",
                     "@pytest.mark.skipif(True, reason='x')", "@unittest.skipUnless(False, 'x')"]:
            with self.subTest(body=body):
                self.assertEqual(self.w("tests/test_a.py", f"{body}\ndef test(): pass\n"), 2)

    def test_an_edit_is_checked_by_the_file_it_leaves(self):
        # Checking only the edited fragment let two harmless-looking edits assemble a secret or a skip.
        self.repo.write("src/a.py", "K = 'AKIAQQ'\n")
        self.assertEqual(self.w("src/a.py", "QQ" + "X" * 14 + "'", tool="Edit", old="QQ'"), 2)
        self.repo.write("tests/test_a.py", "import unittest\n@unittest.sk\ndef test(): pass\n")
        self.assertEqual(self.w("tests/test_a.py", "skip('x')\n", tool="Edit", old="sk\n"), 2)
        fixture = "K = 'AKIA" + "Q" * 16 + "'\n"
        self.repo.write("src/a.py", fixture)
        self.assertEqual(self.w("src/a.py", fixture + "Y = 1\n"), 0, "a key that was already there is not new")

    def test_secrets(self):
        for secret in ["AKIA" + "A" * 16, "ghp_" + "a" * 36, "sk-ant-" + "a" * 30,
                       "-----BEGIN RSA PRIVATE KEY-----", "xoxb-" + "1" * 12]:
            with self.subTest(secret=secret[:8]):
                self.assertEqual(self.w("src/a.py", f"TOKEN = '{secret}'\n"), 2)
        self.assertEqual(self.w("src/a.py", "TOKEN = os.environ['API_TOKEN']\n"), 0)


class WorkflowCommandTests(Base):
    """Deterministic commands the native workflow uses through the gatekeeper agent."""

    def test_open_stage_resets_the_previous_stages_claims(self):
        self.repo.hook(bash_payload("python3 .claude/bin/agentic.py claim WP-01", agent_id="old"))
        self.assertEqual(self.repo.run("open-stage", "1").returncode, 0)
        self.assertEqual(self.repo.hook(bash_payload("python3 .claude/bin/agentic.py claim WP-01", agent_id="new")), 0)


class PipelineCommandTests(Base):
    """Commands the agentic-implement workflow uses (run by the gatekeeper)."""
    manifest = dict(MANIFEST, verify={"tests": f"{PY} ok.py", "lint": f"{PY} lint.py"})

    def setUp(self):
        super().setUp()
        self.repo.write("ok.py", "print('tests ok')\n")
        self.repo.write("lint.py", "import sys\nsys.exit(0)\n")
        self.repo.write("acc.py", "import sys\nsys.exit(0)\n")

    def test_pipeline_plan(self):
        (self.repo.dir / "architecture.md").write_text(
            "# A\nType: fix · Size: small\n\n```json wp-manifest\n" + json.dumps(self.manifest) + "\n```\n", encoding="utf-8")
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
        (self.repo.dir / "acc.py").write_text("import sys\nsys.exit(1)\n", encoding="utf-8")  # before the snapshot: it belongs to nobody
        r = self.repo.run("open-stage", "acceptance")
        self.assertEqual(json.loads(r.stdout), {"stage": "acceptance", "owners": ["AT"]})
        self.repo.as_agent("AT")
        self.repo.write("acceptance/test_ac_001.py")
        r = self.repo.run("close-stage", "acceptance", "AT=done", "--expect", "red")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("RED", r.stdout)
        (self.repo.dir / "acc.py").write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
        self.repo.run("open-stage", "acceptance")
        self.assertEqual(self.repo.run("close-stage", "acceptance", "AT=done", "--expect", "red").returncode, 3,
                         "if they already pass without code, wave 0 fails")
        self.assertEqual(self.repo.run("open-stage", "inventada").returncode, 1)

    def test_close_stage_fix_counts_cycles(self):
        for _ in range(2):
            self.repo.run("open-stage", "1")
            self.repo.run("close-stage", "1", "WP-02=done", "--fix")
        ledger = json.loads((self.repo.dir / ".claude/state/ledger.json").read_text(encoding="utf-8"))
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
        (self.repo.dir / "architecture.md").write_text("```json wp-manifest\n" + json.dumps(m) + "\n```", encoding="utf-8")
        self.assertEqual(self.repo.run("validate").returncode, 1)

    def test_sync_permissions(self):
        local = self.repo.dir / ".claude/settings.local.json"
        local.write_text(json.dumps({"permissions": {"allow": ["Read"]}, "otra": 1}), encoding="utf-8")
        r = self.repo.run("sync-permissions")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(local.read_text(encoding="utf-8"))
        allow = data["permissions"]["allow"]
        for rule in ["Read", "Workflow(agentic-implement)", "Bash(python3 -m unittest:*)", f"Bash({PY} acc.py)",
                     f"Bash({PY} ok.py:*)"]:
            self.assertIn(rule, allow)
        self.assertEqual(data["otra"], 1, "keeps the rest of the file")
        self.repo.run("sync-permissions")
        self.assertEqual(len(json.loads(local.read_text(encoding="utf-8"))["permissions"]["allow"]), len(allow), "idempotente")


class AttributionTests(Base):
    """verify names the work packages behind a failure; seq batches commands; fixes leave a diff for the next attempt."""
    manifest = dict(MANIFEST, verify={"tests": f"{PY} fail.py"},
                    work_packages=[dict(wp, acs=["AC-00%d" % (i + 1)]) for i, wp in enumerate(MANIFEST["work_packages"])])

    def setUp(self):
        super().setUp()
        self.repo.write("acc.py", "import sys\nsys.exit(0)\n")
        self.repo.write("fail.py", "print('FAILED src/b/impl.py:4 test_ac_002_x')\nraise SystemExit(1)\n")

    def test_verify_names_the_suspects_by_path_and_by_criterion(self):
        r = self.repo.run("verify")
        self.assertEqual(r.returncode, 3)
        suspects = json.loads(r.stdout)["suspects"]
        self.assertEqual(sorted(suspects), ["WP-02"])
        self.assertEqual(suspects["WP-02"]["files"], ["src/b/"])
        self.assertEqual(suspects["WP-02"]["acs"], ["AC-002"])
        self.assertEqual(suspects["WP-02"]["phases"], ["tests"])

    def test_failure_without_an_owner_has_no_suspects(self):
        self.repo.write("fail.py", "print('boom')\nraise SystemExit(1)\n")
        self.assertEqual(json.loads(self.repo.run("verify").stdout)["suspects"], {})

    def test_acceptance_failure_is_attributed_through_acs(self):
        self.repo.write("acc.py", "print('FAILED acceptance/test_ac_003_y')\nraise SystemExit(1)\n")
        self.repo.write("fail.py", "raise SystemExit(0)\n")
        suspects = json.loads(self.repo.run("verify").stdout)["suspects"]
        self.assertEqual(list(suspects), ["WP-03"])

    def test_attribution_matches_whole_paths(self):
        sys.path.insert(0, str(SCRIPT.parent))
        import agentic
        m = {"work_packages": [{"id": "WP-01", "files": ["src/a.py"], "acs": ["AC-030"]},
                               {"id": "WP-02", "files": ["util.py", "src/a.pyx"]}]}
        self.assertEqual(list(agentic.attribute(m, ["FAILED src/a.pyx::test"])), ["WP-02"])
        self.assertEqual(agentic.attribute(m, ["error in lib/util.py"]), {}, "lib/util.py is not util.py")
        self.assertEqual(agentic.attribute(m, ["<object at 0x7f91ac30e2b0>"]), {}, "a hex address is not AC-030")
        self.assertEqual(list(agentic.attribute(m, [f"{agentic.ROOT}/src/a.py:3: AssertionError"])), ["WP-01"])
        self.assertEqual(list(agentic.attribute(m, ["test_ac_030_total FAILED"])), ["WP-01"])
        self.assertEqual(list(agentic.attribute(m, ["src\\a.py:3: AssertionError"])), ["WP-01"], "as Windows prints it")

    def test_bad_acs_rejected(self):
        sys.path.insert(0, str(SCRIPT.parent))
        import agentic
        m = json.loads(json.dumps(self.manifest))
        m["work_packages"][0]["acs"] = ["criterion 1"]
        self.assertTrue(any("acs" in e for e in agentic.validate(m)))

    def test_fix_leaves_a_diff_only_with_that_wps_files(self):
        self.repo.write("src/b/x.py", "v1\n")
        self.repo.write("src/a.py", "a1\n")
        self.repo.run("open-stage", "1")
        self.repo.write("src/b/x.py", "v2\n")
        self.repo.write("src/a.py", "a2\n")  # WP-01's, but this attempt only closes WP-02
        r = self.repo.run("close-stage", "1", "WP-02=done", "--fix")
        diff = (self.repo.dir / ".claude/state/runs/WP-02.fix-1.diff").read_text(encoding="utf-8")
        self.assertIn("-v1", diff)
        self.assertIn("+v2", diff)
        self.assertNotIn("a2", diff)
        self.assertFalse((self.repo.dir / ".claude/state/runs/WP-01.fix-1.diff").exists(), r.stdout)

    def test_seq_runs_in_order_and_stops_at_the_first_failure(self):
        r = self.repo.run("seq", "open-stage", "1", "+", "close-stage", "1", "WP-01=done", "WP-02=done", "+", "verify")
        out = json.loads(r.stdout)
        self.assertEqual([s["cmd"].split()[0] for s in out["steps"]], ["open-stage", "close-stage", "verify"])
        self.assertFalse(out["ok"])
        self.assertEqual(r.returncode, 3)
        self.assertEqual(out["steps"][1]["exit_code"], 0)
        r = self.repo.run("seq", "open-stage", "1", "+", "close-stage", "1", "WP-01=blocker", "+", "verify")
        out = json.loads(r.stdout)
        self.assertEqual(len(out["steps"]), 2, "verify is not reached after a failed close-stage")

    def test_seq_only_accepts_the_workflows_commands(self):
        self.assertEqual(self.repo.run("seq", "install", "+", "verify").returncode, 1)
        self.assertEqual(self.repo.run("seq", "verify", "+").returncode, 1)

    def test_gatekeeper_may_be_haiku_but_nobody_else(self):
        r = self.repo.run("validate")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        path = self.repo.dir / ".claude/agents/implementer.md"
        path.write_text(path.read_text(encoding="utf-8").replace("model: sonnet", "model: haiku"), encoding="utf-8")
        r = self.repo.run("validate")
        self.assertEqual(r.returncode, 1)
        self.assertIn("implementer", r.stdout + r.stderr)


class BrownfieldTests(Base):
    """PRP-style plan (validated mirror references) and the regression baseline."""
    manifest = dict(MANIFEST, verify={"tests": f"{PY} t.py", "lint": f"{PY} l.py"})

    def set_manifest(self, m):
        (self.repo.dir / "architecture.md").write_text("```json wp-manifest\n" + json.dumps(m) + "\n```", encoding="utf-8")

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

    def suite(self, *failures):
        lines = "".join(f"print({f!r})\n" for f in failures)
        self.repo.write("t.py", f"{lines}import sys\nsys.exit({1 if failures else 0})\n")
        self.repo.write("l.py", "import sys\nsys.exit(0)\n")
        self.repo.write("acc.py", "import sys\nsys.exit(0)\n")

    def test_a_new_failure_in_a_phase_that_already_failed_is_a_regression(self):
        # Compared failure by failure, not phase by phase: one red test at baseline must not hide every new one.
        self.suite("FAILED tests/test_old.py::test_old (0.12s)")
        self.assertEqual(self.repo.run("baseline").returncode, 0)
        self.suite("FAILED tests/test_old.py::test_old (0.31s)")  # the same failure, another duration
        out = json.loads(self.repo.run("verify").stdout)
        self.assertTrue(out["ok"], out)
        self.suite("FAILED tests/test_old.py::test_old (0.12s)", "<Mock at 0x7f91ac30e2b0> error")
        self.repo.run("baseline")
        self.suite("FAILED tests/test_old.py::test_old (0.12s)", "<Mock at 0x7f00dead0001> error")  # another address
        out = json.loads(self.repo.run("verify").stdout)
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["preexisting_failures"], ["tests"])
        self.suite("FAILED tests/test_old.py::test_old (0.12s)", "FAILED src/a.py::test_x - AssertionError")
        r = self.repo.run("verify")
        self.assertEqual(r.returncode, 3, r.stdout)
        out = json.loads(r.stdout)
        tests = next(p for p in out["phases"] if p["name"] == "tests")
        self.assertEqual(tests["status"], "regression")
        self.assertEqual(tests["new_failures"], ["FAILED src/a.py::test_x - AssertionError"])
        self.assertEqual(sorted(out["suspects"]), ["WP-01"], "only the new failure is attributed")

    def test_a_baseline_without_failure_lines_compares_by_phase(self):
        # A baseline saved by an earlier version has no failure lines: the phase-level comparison stays.
        self.suite("FAILED tests/test_old.py::test_old")
        self.repo.run("baseline")
        path = self.repo.dir / ".claude/state/baseline.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        for ph in data["phases"]:
            ph.pop("failures", None)
        path.write_text(json.dumps(data), encoding="utf-8")
        self.suite("FAILED tests/test_old.py::test_old", "FAILED src/a.py::test_x")
        out = json.loads(self.repo.run("verify").stdout)
        self.assertEqual(out["preexisting_failures"], ["tests"])

    def test_long_failure_lines_keep_verify_parseable_through_seq(self):
        # seq caps each step's output; the JSON verdict on the last line must survive it, or the engine loses the owner.
        self.suite(*[f"FAILED src/a.py::test_{i} " + "x" * 2000 for i in range(8)])
        r = self.repo.run("seq", "verify")
        step = json.loads(r.stdout)["steps"][0]
        verdict = json.loads(step["output"].splitlines()[-1])
        self.assertEqual(sorted(verdict["suspects"]), ["WP-01"])

    def test_baseline_with_nonexistent_command_fails(self):
        m = dict(self.manifest, verify={"tests": "command-that-does-not-exist"})
        self.set_manifest(m)
        self.assertEqual(self.repo.run("baseline").returncode, 1)

    def test_save_result(self):
        payload = {"result": "OK", "qa": {"status": "PASS"}, "note": "ñandú"}
        r = self.repo.run("save-result", stdin=json.dumps(payload))
        self.assertEqual(r.returncode, 0, r.stderr)
        saved = json.loads((self.repo.dir / ".claude/state/runs/workflow-result.json").read_text(encoding="utf-8"))
        self.assertEqual(saved, payload)
        self.assertEqual(self.repo.run("save-result", stdin="not json").returncode, 1)
        (self.repo.dir / ".agentic-result.json").write_text(json.dumps({"result": "REVIEW"}), encoding="utf-8")
        r = self.repo.run("save-result", ".agentic-result.json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.repo.dir / ".agentic-result.json").exists(), "the temporary file is deleted")
        self.assertEqual(json.loads((self.repo.dir / ".claude/state/runs/workflow-result.json").read_text(encoding="utf-8"))["result"], "REVIEW")
        self.repo.write("src/x.json", "{}")
        self.assertEqual(self.repo.run("save-result", "src/x.json").returncode, 1, "only from the root")
        self.repo.write("package.json", '{"name": "app"}')
        self.assertEqual(self.repo.run("save-result", "package.json").returncode, 1, "only the workflow's own file")
        self.assertTrue((self.repo.dir / "package.json").exists(), "a project file is never deleted")

    def test_codemap_writes_only_a_map_inside_the_repo(self):
        for target in ("../outside.md", "architecture.md", ".claude/x.md", "docs/architecture/m.md", "/tmp/m.md", "map.txt"):
            with self.subTest(target=target):
                self.assertEqual(self.repo.run("codemap", target).returncode, 1)
        self.assertFalse((self.repo.dir.parent / "outside.md").exists())
        self.assertNotIn("Code map", (self.repo.dir / "architecture.md").read_text(encoding="utf-8"))

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
        (self.repo.dir / "architecture.md").write_text("```json wp-manifest\n" + json.dumps(m) + "\n```", encoding="utf-8")

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
        lib = (self.repo.dir / ".claude/rules-library/go.md").read_text(encoding="utf-8")
        self.assertEqual((self.repo.dir / ".claude/rules/agentic/go.md").read_text(encoding="utf-8"), lib, "copia exacta: paths: arriba")

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


class StopGateTests(Base):
    """The stop gate: a finished report is refused while the package's own acceptance tests fail, twice at most."""
    manifest = dict(MANIFEST, work_packages=[
        {"id": "WP-01", "wave": 1, "depends_on": [], "files": ["src/a.py", "tests/test_a.py"], "acs": ["AC-001"]},
        {"id": "WP-02", "wave": 1, "depends_on": [], "files": ["src/b/"], "acs": ["AC-002"]},
    ])

    def suite(self, lines=("FAILED acceptance/test_x.py::test_ac_001_x - assert",), code=1):
        body = "".join(f"print({line!r})\n" for line in lines)
        self.repo.write("acc.py", f"import sys\nopen('ran', 'w').close()\n{body}sys.exit({code})\n")

    def ran(self):
        marker = self.repo.dir / "ran"
        if not marker.exists():
            return False
        marker.unlink()
        return True

    def test_the_gate_refuses_a_finished_report_and_counts_it(self):
        self.suite()
        for status in ("DONE", "DONE_WITH_CONCERNS"):
            with self.subTest(status=status):
                r = self.repo.run("hook", stdin=json.dumps(report_payload(status)), env={"AGENTIC_WP": "WP-01"})
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertIn("test_ac_001_x", r.stderr)
                self.assertTrue(self.ran())
        self.assertEqual(json.loads((self.repo.dir / ".claude/state/gate.json").read_text(encoding="utf-8"))["WP-01"]["denials"], 2)

    def test_only_finished_implementer_reports_are_gated(self):
        self.suite()
        cases = {
            "a blocked report": (report_payload("BLOCKED"), {"AGENTIC_WP": "WP-01"}),
            "a malformed status": (dict(report_payload("DONE"), tool_input={"status": 7}), {"AGENTIC_WP": "WP-01"}),
            "QA's report": (report_payload("PASS", wp="QA", agent_type="qa"), {"AGENTIC_WP": "QA"}),
            "a reviewer that never claimed (native mode)": (report_payload("PASS", agent_id="r1", agent_type="reviewer"), None),
        }
        for name, (payload, env) in cases.items():
            with self.subTest(case=name):
                r = self.repo.run("hook", stdin=json.dumps(payload), env=env)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertFalse(self.ran(), "the acceptance command must not run")

    def test_the_gate_lets_through_what_it_cannot_judge(self):
        # Another package's failure, a command that cannot run, no acceptance command, a corrupt counter file.
        self.suite(("FAILED acceptance/test_y.py::test_ac_002_y",))
        self.assertEqual(self.repo.hook(report_payload("DONE"), "WP-01"), 0)
        self.repo.write("acc.py", "import sys\nsys.exit(127)\n")
        self.assertEqual(self.repo.hook(report_payload("DONE"), "WP-01"), 0)
        self.suite()
        self.repo.write(".claude/state/gate.json", "{not json")
        r = self.repo.run("hook", stdin=json.dumps(report_payload("DONE")), env={"AGENTIC_WP": "WP-01"})
        self.assertEqual(r.returncode, 2, "a corrupt counter is set aside and the gate still works")
        without = json.dumps(dict(self.manifest, acceptance={"files": ["acceptance/"]}))
        (self.repo.dir / "architecture.md").write_text(f"```json wp-manifest\n{without}\n```", encoding="utf-8")
        self.assertEqual(self.repo.hook(report_payload("DONE"), "WP-01"), 0)

    def test_status_to_state_maps_the_four_statuses(self):
        for status, state in (("DONE", "done"), ("DONE_WITH_CONCERNS", "done"), ("NEEDS_CONTEXT", "blocker"),
                              ("BLOCKED", "blocker"), ("BLOCKER", "blocker"), ("NO REPORT", "error")):
            self.assertEqual(agentic.status_to_state(status), state, status)


class CallersTests(Base):
    """`callers`: the public symbols of the changed files and the lines outside the diff that use them."""

    def commit(self):
        subprocess.run(["git", "add", "-A"], cwd=self.repo.dir, check=True, capture_output=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"], cwd=self.repo.dir,
                       check=True, capture_output=True)

    def callers(self, *args):
        r = self.repo.run("callers", *args)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads([ln for ln in r.stdout.strip().splitlines() if ln.startswith("{")][-1])

    def test_a_changed_file_without_public_symbols_lists_nothing(self):
        self.repo.write("src/a.py", "x = 1\n")
        self.commit()
        self.repo.write("src/a.py", "x = 2\n")
        out = self.callers()
        self.assertEqual((out["changed"], out["symbols"], out["callers"]), (["src/a.py"], {}, []))

    def test_a_symbol_used_only_in_the_diff_has_no_callers_and_two_symbols_on_one_line_count_twice(self):
        self.repo.write("src/a.py", "def f():\n    pass\n\n\ndef g():\n    return f()\n\n\ndef f():\n    pass\n")
        self.repo.write("src/b.py", "y = g(f())\n")
        self.commit()
        self.repo.write("src/a.py", "def f():\n    return 1\n\n\ndef g():\n    return f()\n\n\ndef f():\n    pass\n")
        out = self.callers()
        self.assertEqual(out["symbols"], {"src/a.py": ["f", "g"]}, "a symbol defined twice is listed once")
        self.assertEqual([(c["symbol"], c["file"], c["line"]) for c in out["callers"]],
                         [("f", "src/b.py", 1), ("g", "src/b.py", 1)])

    def test_deleted_files_state_and_js_dollar_identifiers(self):
        self.repo.write("src/gone.py", "def gone():\n    pass\n")
        self.repo.write("web/a.js", "export function $go() {}\n")
        self.repo.write("web/b.js", "$go();\nconst x$go = 1;\n")
        self.repo.write(".claude/state/note.py", "$go()\n")
        self.commit()
        (self.repo.dir / "src/gone.py").unlink()
        self.repo.write("web/a.js", "export function $go() { return 1 }\n")
        out = self.callers()
        self.assertEqual(out["changed"], ["web/a.js"])
        self.assertEqual([(c["file"], c["line"]) for c in out["callers"]], [("web/b.js", 1)])

    def test_caps_per_symbol_and_in_total_set_truncated(self):
        names = [f"s{i}" for i in range(9)]
        self.repo.write("src/a.py", "".join(f"def {n}():\n    pass\n\n\n" for n in names))
        self.repo.write("src/b.py", "".join(f"{n}()\n" for n in names for _ in range(11)))
        self.commit()
        self.repo.write("src/a.py", "".join(f"def {n}():\n    return 1\n\n\n" for n in names))
        out = self.callers()
        self.assertEqual(len(out["callers"]), 80)
        self.assertTrue(out["truncated"])
        self.assertEqual([c["symbol"] for c in out["callers"]][:10], ["s0"] * 10)

    def test_without_git_or_without_a_commit_it_still_answers(self):
        self.repo.write("src/a.py", "def f():\n    pass\n")
        self.repo.write("src/b.py", "f()\n")
        out = self.callers()  # the test repo has git init but no commit: HEAD does not exist
        self.assertEqual(out["changed"], ["src/a.py", "src/b.py"])
        self.assertEqual(out["callers"], [], "both files are in the diff")
        shutil.rmtree(self.repo.dir / ".git")
        out = self.callers()
        self.assertEqual((out["changed"], out["callers"]), ([], []))

    def test_callers_runs_inside_seq(self):
        self.repo.write("src/a.py", "def f():\n    pass\n")
        r = self.repo.run("seq", "callers")
        self.assertEqual(r.returncode, 0, r.stderr)
        steps = json.loads(r.stdout)["steps"]
        self.assertEqual(len(steps), 1)
        self.assertIn("f", json.loads(steps[0]["output"].strip().splitlines()[-1])["symbols"]["src/a.py"])


class LivingDocsTests(Base):
    """Living documentation: the DOCS zone, the deterministic code map and docs-check."""
    manifest = dict(MANIFEST, docs={"files": ["README.md", "CHANGELOG.md", "docs/"]})

    def set_manifest(self, m):
        (self.repo.dir / "architecture.md").write_text("```json wp-manifest\n" + json.dumps(m) + "\n```", encoding="utf-8")

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
        self.repo.hook(bash_payload("python3 .claude/bin/agentic.py claim DOCS", agent_id="d1", agent_type="doc-updater"))
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
        text = (self.repo.dir / "docs/CODEMAP.md").read_text(encoding="utf-8")
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

    def test_docs_check_accepts_links_that_work_on_github(self):
        self.repo.write("docs/guide.md", "# Guide\n\n## Usage\n\n## Usage\n")
        self.repo.write("docs/my guide.md", "# Other\n")
        self.repo.write("README.md", "[second](docs/guide.md#usage-1) [root](/docs/guide.md) [space](docs/my%20guide.md)\n")
        r = self.repo.run("docs-check")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.repo.write("README.md", "[third](docs/guide.md#usage-2)\n")
        self.assertEqual(self.repo.run("docs-check").returncode, 3)

    def test_a_corrupt_ledger_is_set_aside(self):
        state = self.repo.dir / ".claude/state"
        state.mkdir(parents=True, exist_ok=True)
        (state / "ledger.json").write_text('{"WP-01": {"state": "runn', encoding="utf-8")
        r = self.repo.run("status")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((state / "ledger.json.corrupt").exists(), "the broken copy is kept for inspection")

    def test_rules_sync_ignores_stack_names_that_are_not_names(self):
        shutil.copytree(SCRIPT.parents[1] / "rules-library", self.repo.dir / ".claude/rules-library")
        m = dict(json.loads(json.dumps(MANIFEST)), stack=["python", "../../architecture"])
        (self.repo.dir / "architecture.md").write_text(f"# A\n\n```json wp-manifest\n{json.dumps(m)}\n```\n",
                                                       encoding="utf-8")
        self.repo.run("rules-sync")
        self.assertFalse((self.repo.dir / ".claude/architecture.md").exists(), "nothing is written outside the rules")
        self.assertTrue((self.repo.dir / ".claude/rules/agentic/python.md").exists())

    def test_docs_check_ignores_the_decision_archive(self):
        self.repo.write("docs/architecture/0001-x.md", "[old](../../src/deleted.py)\n")
        self.assertEqual(self.repo.run("docs-check").returncode, 0)

    def test_pipeline_plan_and_docs_stage(self):
        plan = json.loads(self.repo.run("pipeline-plan").stdout)
        self.assertTrue(plan["docs"])
        self.assertEqual(plan["codemap"], "docs/CODEMAP.md")
        r = self.repo.run("open-stage", "docs")
        self.assertEqual(json.loads(r.stdout)["owners"], ["DOCS"])
        self.repo.as_agent("DOCS")
        self.repo.write("README.md", "# new")
        self.assertEqual(self.repo.run("close-stage", "docs", "DOCS=done").returncode, 0)
        self.repo.run("open-stage", "docs")
        self.repo.write("src/a.py", "x = 2")
        self.assertEqual(self.repo.run("close-stage", "docs", "DOCS=done").returncode, 3, "outside its zone")

    def test_the_decision_archive_and_the_plan_are_nobodys(self):
        # The docs zone includes docs/ by prefix; the hook already blocks docs/architecture/, close-stage must too.
        self.repo.run("open-stage", "docs")
        self.repo.write("docs/architecture/0001-x.md", "rewritten")
        self.assertEqual(self.repo.run("close-stage", "docs", "DOCS=done").returncode, 3)
        (self.repo.dir / "docs/architecture/0001-x.md").unlink()
        self.repo.run("open-stage", "docs")
        with open(self.repo.dir / "architecture.md", "a", encoding="utf-8") as f:
            f.write("\nA line nobody may add.\n")
        self.assertEqual(self.repo.run("close-stage", "docs", "DOCS=done").returncode, 3)

    def test_a_judge_stage_that_fails_closes_cleanly(self):
        # review, qa and docs record a fail or a blocker in the ledger; only a write outside the zone is an error.
        for label, state in (("review", "REVIEW=fail"), ("qa", "QA=fail"), ("docs", "DOCS=blocker")):
            self.repo.run("open-stage", label)
            r = self.repo.run("close-stage", label, state)
            self.assertEqual(r.returncode, 0, f"{label}: {r.stdout}")
            self.assertEqual(json.loads((self.repo.dir / ".claude/state/ledger.json").read_text(encoding="utf-8"))
                             [state.split("=")[0]]["state"], state.split("=")[1])
        self.repo.run("open-stage", "qa")
        self.repo.write("src/a.py", "x = 3")
        self.assertEqual(self.repo.run("close-stage", "qa", "QA=fail").returncode, 3, "outside its zone")
        self.repo.run("open-stage", "1")
        self.assertEqual(self.repo.run("close-stage", "1", "WP-01=blocker").returncode, 3, "a wave still stops on a blocker")

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
            {"permissions": {"allow": ["Bash(make test)"]}, "hooks": {"PostToolUse": []}, "model": "sonnet"}), encoding="utf-8")
        (self.dir / ".gitignore").write_text("dist/\n", encoding="utf-8")
        (self.dir / "main.py").write_text("print(1)\n", encoding="utf-8")
        self.env = dict(os.environ)
        self.env.pop("CLAUDE_PROJECT_DIR", None)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def install(self, *flags):
        return subprocess.run([sys.executable, str(SCRIPT), "install", str(self.dir), *flags], capture_output=True,
                              text=True, env=self.env, encoding="utf-8", errors="replace")

    def test_a_first_install_has_no_upgrade_notes(self):
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith("agentic-flow unknown -> "))
        self.assertNotIn("upgrade notes:", r.stdout)

    def test_flags_and_usage(self):
        self.assertEqual(self.install("--allow-dirty").returncode, 0)
        r = self.install("--everything")
        self.assertEqual(r.returncode, 1)
        self.assertIn("--allow-dirty", r.stderr)
        r = subprocess.run([sys.executable, str(SCRIPT), "flow-history", "extra"], capture_output=True, text=True,
                           env=self.env, encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Uso: flow-history", r.stderr)

    def test_installs_the_flow_and_keeps_project_configuration(self):
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stderr)
        for rel in (".claude/bin/agentic.py", ".claude/agents/implementer.md", ".claude/commands/architect.md",
                    ".claude/workflows/agentic-implement.js", ".claude/rules-library/common.md",
                    ".claude/rules/agentic/python.md", ".claude/agentic.json", ".claude/agentic-install.json"):
            self.assertTrue((self.dir / rel).exists(), rel)
        self.assertFalse((self.dir / ".claude/bin/test_agentic.py").exists(), "the flow's tests are not installed")
        settings = json.loads((self.dir / ".claude/settings.json").read_text(encoding="utf-8"))
        self.assertEqual(settings["model"], "sonnet")
        self.assertIn("Bash(make test)", settings["permissions"]["allow"])
        self.assertIn("Workflow(agentic-implement)", settings["permissions"]["allow"])
        self.assertIn("Bash(rm -rf:*)", settings["permissions"]["deny"])
        self.assertTrue(settings["enableWorkflows"])
        self.assertEqual(len(settings["hooks"]["PreToolUse"]), 1)
        self.assertIn(".claude/state/", (self.dir / ".gitignore").read_text(encoding="utf-8").splitlines())
        self.assertTrue((self.dir / ".gitignore").read_text(encoding="utf-8").startswith("dist/\n"))

    def test_raises_the_bash_timeouts_without_overriding_the_project(self):
        # Long suites must run in the foreground: a backgrounded command invites agents to improvise wait loops.
        settings_path = self.dir / ".claude/settings.json"
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        data["env"] = {"BASH_DEFAULT_TIMEOUT_MS": "300000", "OTHER": "x"}
        settings_path.write_text(json.dumps(data), encoding="utf-8")
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stderr)
        env = json.loads(settings_path.read_text(encoding="utf-8"))["env"]
        self.assertEqual(env, {"BASH_DEFAULT_TIMEOUT_MS": "300000", "OTHER": "x", "BASH_MAX_TIMEOUT_MS": "1800000"})
        self.assertIn("env BASH_MAX_TIMEOUT_MS", r.stdout)
        self.assertNotIn("env BASH_DEFAULT_TIMEOUT_MS", r.stdout, "the project's own value is kept")

    def test_the_waiting_hooks_go_in_once_even_next_to_an_empty_event(self):
        # The fixture's settings already have "PostToolUse": [] (an event the project declared with no hooks).
        first = self.install()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("hook PostToolUse", first.stdout)
        settings = json.loads((self.dir / ".claude/settings.json").read_text(encoding="utf-8"))
        commands = [h["command"] for e in settings["hooks"]["PostToolUse"] for h in e["hooks"]]
        self.assertEqual(len([c for c in commands if "waiting.py" in c]), 1)
        self.assertTrue(all(c.endswith("|| true") for c in commands), "a missing waiting.py must never deny anything")
        subprocess.run(["git", "add", "-A"], cwd=self.dir, check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i"], cwd=self.dir, check=True)
        self.assertIn("settings.json: unchanged", self.install().stdout)

    def test_removes_only_flow_files_inside_the_target(self):
        # The install record comes from the target repo: it must not make install delete anything else.
        victim = self.dir.parent / f"{self.dir.name}-victim.txt"
        victim.write_text("precious", encoding="utf-8")
        self.addCleanup(victim.unlink, missing_ok=True)
        (self.dir / "notes.txt").write_text("mine", encoding="utf-8")
        record = {"version": "1.0.0", "files": {f"../{victim.name}": hashlib.sha256(b"precious").hexdigest(),
                                                "notes.txt": hashlib.sha256(b"mine").hexdigest()}}
        (self.dir / ".claude/agentic-install.json").write_text(json.dumps(record), encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=self.dir, check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i"], cwd=self.dir, check=True)
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(victim.exists(), "a file outside the target was deleted")
        self.assertTrue((self.dir / "notes.txt").exists(), "a file the flow never shipped was deleted")
        self.assertNotRegex(r.stdout, r"(?m)^  removed:")

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
        mine.write_text(mine.read_text(encoding="utf-8") + "\nTeam rule of our own.\n", encoding="utf-8")
        stale = self.dir / ".claude/agents/implementer.md"
        stale.write_text("old version, untouched", encoding="utf-8")
        record = json.loads((self.dir / ".claude/agentic-install.json").read_text(encoding="utf-8"))
        record["files"][".claude/agents/implementer.md"] = hashlib.sha256(b"old version, untouched").hexdigest()
        (self.dir / ".claude/agentic-install.json").write_text(json.dumps(record), encoding="utf-8")
        r = self.install()
        self.assertIn("modified by you (left alone): 1", r.stdout)
        self.assertIn("Team rule of our own.", mine.read_text(encoding="utf-8"))
        self.assertNotEqual(stale.read_text(encoding="utf-8"), "old version, untouched", "what was not modified gets updated")
        self.assertIn("modified by you", self.install().stdout, "still protected on the next update")
        self.install("--force")
        self.assertNotIn("Team rule of our own.", mine.read_text(encoding="utf-8"))

    def test_old_copy_without_record_is_kept_and_rules_are_installed(self):
        old = self.dir / ".claude/bin/agentic.py"
        old.parent.mkdir(parents=True)
        old.write_text("import sys\nprint('old version')\nsys.exit(1)\n", encoding="utf-8")
        r = self.install()
        self.assertIn("modified by you (left alone): 1", r.stdout)
        self.assertIn("old version", old.read_text(encoding="utf-8"), "no record, different: not overwritten")
        self.assertIn("rules: common, python", r.stdout)
        self.assertTrue((self.dir / ".claude/rules/agentic/python.md").exists())

    def test_dry_run_writes_nothing(self):
        r = self.install("--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("dry run", r.stdout)
        self.assertFalse((self.dir / ".claude/bin/agentic.py").exists())

    def test_cannot_install_over_itself(self):
        r = subprocess.run([sys.executable, str(SCRIPT), "install", str(SCRIPT.parents[2])], capture_output=True,
                           text=True, env=self.env, encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 1)

    def test_installed_flow_works(self):
        self.install()
        (self.dir / "architecture.md").write_text("```json wp-manifest\n" + json.dumps(dict(MANIFEST, stack=["python"])) + "\n```", encoding="utf-8")
        r = subprocess.run([sys.executable, ".claude/bin/agentic.py", "validate"], cwd=self.dir, capture_output=True,
                           text=True, env=self.env, encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 0, r.stderr)


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class WorkflowLogicTests(unittest.TestCase):
    def test_workflow_scenarios(self):
        r = subprocess.run(["node", str(SCRIPT.parent / "test_workflow.mjs")], capture_output=True, text=True, encoding="utf-8", errors="replace")
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
        "pip3.12 install requests", "yarn", "python3 -m pip install x", "echo 'src/' >> .git/info/exclude",
        "cp hook .git/hooks/pre-commit", "printf '[core]' >> .git/config",
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

    def test_a_command_too_long_to_check_is_blocked(self):
        # The rules are regular expressions: a huge command could take longer than the hook may run.
        self.assertEqual(self.repo.hook(bash_payload("echo " + "git " * 5000), wp="WP-01"), 2)
        self.assertEqual(self.repo.hook(bash_payload("echo " + "x" * 20000)), 0, "the main thread is not limited")


class CheckStageTests(Base):
    def setUp(self):
        super().setUp()
        self.repo.write("src/a.py", "v1")
        subprocess.run(["git", "add", "-A"], cwd=self.repo.dir, check=True)
        self.repo.run("snapshot", "1")

    def test_own_changes_ok(self):
        self.repo.as_agent()
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


class SafetyNetTests(Base):
    """What close-stage catches whatever tool made the change (Bash included): the hook is the first line, this the second."""

    def setUp(self):
        super().setUp()
        self.repo.write("src/a.py", "v1\n")
        self.repo.write("tests/test_a.py", "def test_a():\n    assert True\n")
        self.repo.write("src/coverage/calc.py", "x = 1\n")  # tracked source in a folder named like an artefact
        self.repo.write("pyproject.toml", "[tool.ruff]\nline-length = 88\n")
        subprocess.run(["git", "add", "-A"], cwd=self.repo.dir, check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base"], cwd=self.repo.dir,
                       check=True)
        self.repo.run("snapshot", "1")

    def check(self):
        return self.repo.run("check-wave", "1")

    def test_a_commit_during_the_stage_is_caught(self):
        self.repo.write("src/a.py", "v2\n")
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "x"], cwd=self.repo.dir,
                       check=True)
        r = self.check()
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertIn("git state changed", r.stdout)

    def test_git_config_and_hooks_are_watched(self):
        with open(self.repo.dir / ".git/config", "a", encoding="utf-8") as f:
            f.write("[core]\n\thooksPath = /dev/null\n")
        self.assertEqual(self.check().returncode, 3)

    def test_the_baseline_cannot_be_rewritten(self):
        state = self.repo.dir / ".claude/state"
        (state / "baseline.json").write_text('{"phases": []}', encoding="utf-8")
        self.repo.run("snapshot", "2")
        (state / "baseline.json").write_text('{"phases": [{"name": "tests", "passed": false}]}', encoding="utf-8")
        self.assertEqual(self.repo.run("check-wave", "2").returncode, 3)

    def test_tracked_files_are_never_ignored(self):
        self.repo.write("src/coverage/calc.py", "x = 2\n")
        self.assertEqual(self.check().returncode, 3, "nobody owns src/coverage/calc.py")
        self.repo.write("src/coverage/calc.py", "x = 1\n")
        self.repo.write("coverage/lcov.info")  # an untracked artefact is still ignored
        self.assertEqual(self.check().returncode, 0)

    def test_content_written_through_bash_is_checked(self):
        self.repo.write("src/a.py", "KEY = 'AKIA" + "Q" * 16 + "'\n")
        r = self.check()
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertIn("AWS access key", r.stdout)
        self.repo.write("src/a.py", "v1\n")
        self.repo.write("tests/test_a.py", "import unittest\n@unittest.skip('x')\ndef test_a():\n    assert True\n")
        self.assertEqual(self.check().returncode, 3)

    def test_a_secret_that_was_already_there_is_not_blamed(self):
        self.repo.write("src/a.py", "KEY = 'AKIA" + "Q" * 16 + "'\n")
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "fixture"], cwd=self.repo.dir,
                       check=True)
        self.repo.run("snapshot", "2")
        self.repo.write("src/a.py", "KEY = 'AKIA" + "Q" * 16 + "'\nY = 1\n")
        self.assertEqual(self.repo.run("check-wave", "2").returncode, 3, "src/a.py is wave 1's") 
        self.repo.write("src/a.py", "KEY = 'AKIA" + "Q" * 16 + "'\n")
        self.repo.run("snapshot", "1")
        self.repo.as_agent()
        self.repo.write("src/a.py", "KEY = 'AKIA" + "Q" * 16 + "'\nY = 1\n")
        self.assertEqual(self.check().returncode, 0, "the key was there before the stage")

    def test_existing_quality_config_needs_config_edit(self):
        m = json.loads(json.dumps(MANIFEST))
        m["work_packages"][0]["files"].append("pyproject.toml")
        (self.repo.dir / "architecture.md").write_text(f"# A\n\n```json wp-manifest\n{json.dumps(m)}\n```\n",
                                                       encoding="utf-8")
        self.repo.run("snapshot", "1")
        self.repo.write("pyproject.toml", "[tool.ruff]\nline-length = 300\n")
        self.assertEqual(self.check().returncode, 3)
        self.repo.write("pyproject.toml", "[tool.ruff]\nline-length = 88\n")
        m["work_packages"][0]["config_edit"] = True
        (self.repo.dir / "architecture.md").write_text(f"# A\n\n```json wp-manifest\n{json.dumps(m)}\n```\n",
                                                       encoding="utf-8")
        self.repo.run("snapshot", "1")
        self.repo.as_agent()
        self.repo.write("pyproject.toml", "[tool.ruff]\nline-length = 300\n")
        self.assertEqual(self.check().returncode, 0)


class LauncherTests(Base):
    def setUp(self):
        super().setUp()
        self.bin = Path(tempfile.mkdtemp(prefix="fake-claude-"))
        self.logs = Path(tempfile.mkdtemp(prefix="fake-logs-"))
        self.repo.env.update(AGENTIC_CLAUDE=testutil.install_fake_claude(self.bin), FAKE_LOG_DIR=str(self.logs))

    def tearDown(self):
        super().tearDown()
        shutil.rmtree(self.bin, ignore_errors=True)
        shutil.rmtree(self.logs, ignore_errors=True)

    def last_call(self, wp):
        return testutil.fake_calls(self.logs, wp)[-1]

    def args(self, wp):
        return self.last_call(wp)["argv"]

    def called(self, wp):
        return bool(testutil.fake_calls(self.logs, wp))

    def test_wave_runs_in_parallel(self):
        r = self.repo.run("run-wave", "1", env={"FAKE_SLEEP": "1.5"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # Overlapping runs, not a wall-clock bound: starting processes is much slower on Windows.
        (s1, e1), (s2, e2) = ((self.last_call(wp)["t"], self.last_call(wp)["end"]) for wp in ("WP-01", "WP-02"))
        self.assertTrue(s1 < e2 and s2 < e1, "the two WPs should run at the same time")
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
        self.assertFalse(self.called("WP-03"), "WP-03 belongs to wave 2")

    def test_model_cannot_be_changed(self):
        (self.repo.dir / ".claude/agentic.json").write_text(json.dumps({"models": {"qa": "claude-opus-5-5"}}), encoding="utf-8")
        self.repo.run("run-qa", env={"AGENTIC_QA_MODEL": "opus", "AGENTIC_IMPLEMENTER_MODEL": "opus"})
        a = self.args("QA")
        self.assertEqual(a[a.index("--model") + 1], "sonnet")
        self.assertEqual(a[a.index("--agent") + 1], "qa")

    def test_agent_with_another_model_does_not_validate(self):
        f = self.repo.dir / ".claude/agents/qa.md"
        f.write_text(f.read_text(encoding="utf-8").replace("model: sonnet", "model: claude-opus-5-5"), encoding="utf-8")
        r = self.repo.run("run-wave", "1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("model: sonnet", r.stderr)
        self.assertFalse(self.called("WP-01"))

    def test_architect_must_be_opus(self):
        cmds = self.repo.dir / ".claude/commands"
        cmds.mkdir(parents=True, exist_ok=True)
        (cmds / "architect.md").write_text("---\ndescription: x\nmodel: opus\n---\nx", encoding="utf-8")
        self.assertEqual(self.repo.run("validate").returncode, 0)
        (cmds / "architect.md").write_text("---\ndescription: x\nmodel: claude-opus-5-5\n---\nx", encoding="utf-8")
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
        (cmds / "implement.md").write_text("---\ndescription: x\nmodel: opus\n---\nx", encoding="utf-8")
        r = self.repo.run("validate")
        self.assertEqual(r.returncode, 1)
        self.assertIn("/implement", r.stderr)

    def test_specialised_agent_must_exist_and_be_sonnet(self):
        m = json.loads(json.dumps(MANIFEST))
        m["work_packages"][0]["agent"] = "frontend"
        (self.repo.dir / "architecture.md").write_text(f"```json wp-manifest\n{json.dumps(m)}\n```", encoding="utf-8")
        r = self.repo.run("validate")
        self.assertIn("does not exist", r.stderr)
        (self.repo.dir / ".claude/agents/frontend.md").write_text("---\nname: frontend\nmodel: haiku\n---\nx", encoding="utf-8")
        self.assertIn("not 'haiku'", self.repo.run("validate").stderr)
        (self.repo.dir / ".claude/agents/frontend.md").write_text("---\nname: frontend\nmodel: sonnet\n---\nx", encoding="utf-8")
        self.assertEqual(self.repo.run("validate").returncode, 0)

    def env_of(self, wp):
        return self.last_call(wp)["env"]

    def test_subscription_does_not_inherit_api_key(self):
        self.repo.run("run-wave", "1", env={"ANTHROPIC_API_KEY": "sk-test", "ANTHROPIC_AUTH_TOKEN": "tok"})
        env = self.env_of("WP-01")
        self.assertEqual((env["ANTHROPIC_API_KEY"], env["ANTHROPIC_AUTH_TOKEN"]), (None, None))

    def test_auth_inherit_keeps_api_key(self):
        (self.repo.dir / ".claude").mkdir(exist_ok=True)
        (self.repo.dir / ".claude/agentic.json").write_text(json.dumps({"auth": "inherit"}), encoding="utf-8")
        self.repo.run("run-wave", "1", env={"ANTHROPIC_API_KEY": "sk-test"})
        self.assertEqual(self.env_of("WP-01")["ANTHROPIC_API_KEY"], "sk-test")

    def test_only_one_wp(self):
        r = self.repo.run("run-wave", "1", "WP-02")
        self.assertEqual(r.returncode, 0)
        self.assertFalse(self.called("WP-01"))
        self.assertEqual(self.repo.run("run-wave", "1", "WP-03").returncode, 1)

    def prompt_of(self, wp):
        return self.last_call(wp)["prompt"]

    def test_fix_only_when_requested(self):
        runs = self.repo.dir / ".claude/state/runs"
        runs.mkdir(parents=True, exist_ok=True)
        (runs / "REVIEW.json").write_text(json.dumps({"structured_output": {"status": "FAIL", "findings": [FINDING]}}), encoding="utf-8")
        self.repo.run("run-wave", "1", "WP-02")
        self.assertNotIn('"problem"', self.prompt_of("WP-02"), "without --fix it must not use an old report")
        self.repo.run("run-wave", "1", "WP-02", "--fix")
        self.assertIn('"problem":"p"', self.prompt_of("WP-02"))

    def test_fix_without_report_fails(self):
        r = self.repo.run("run-wave", "1", "WP-01", "--fix")
        self.assertEqual(r.returncode, 1)
        self.assertIn("run-review or run-qa", r.stderr)

    def write_run(self, name, data, when):
        runs = self.repo.dir / ".claude/state/runs"
        runs.mkdir(parents=True, exist_ok=True)
        path = runs / name
        path.write_text(json.dumps(data), encoding="utf-8")
        os.utime(path, (when, when))

    def reviewer(self, problem, severity="high"):
        return {"structured_output": {"status": "FAIL", "needs_validation": [], "not_checked": [],
                                      "findings": [dict(FINDING, problem=problem, severity=severity)]}}

    def test_fix_reads_the_newest_source_and_ties_go_to_the_one_off_report(self):
        base = time.time() - 100
        self.write_run("workflow-result.json", {"review": {"confirmed": [dict(FINDING, problem="record")]}, "qa": None}, base)
        self.write_run("REVIEW-3.json", self.reviewer("per-agent"), base)
        self.write_run("REVIEW.json", self.reviewer("one-off"), base)
        self.repo.run("run-wave", "1", "WP-02", "--fix")
        self.assertIn("one-off", self.prompt_of("WP-02"))
        self.write_run("REVIEW-3.json", self.reviewer("per-agent"), base + 10)
        self.repo.run("mark", "WP-02", "done")
        self.repo.dir.joinpath(".claude/state/ledger.json").unlink()
        self.repo.run("run-wave", "1", "WP-02", "--fix")
        self.assertIn("per-agent", self.prompt_of("WP-02"))

    def test_a_record_without_review_lets_an_older_file_provide_it(self):
        base = time.time() - 100
        self.write_run("REVIEW.json", self.reviewer("older review"), base)
        self.write_run("workflow-result.json", {"review": None, "qa": {"defects": []}}, base + 10)
        r = self.repo.run("run-wave", "1", "WP-02", "--fix")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("older review", self.prompt_of("WP-02"))

    def test_unreadable_reports_are_skipped(self):
        base = time.time() - 100
        self.write_run("REVIEW.json", self.reviewer("good one"), base)
        runs = self.repo.dir / ".claude/state/runs"
        (runs / "workflow-result.json").write_text("{broken", encoding="utf-8")
        (runs / "REVIEW-x.json").write_text("{}", encoding="utf-8")
        r = self.repo.run("run-wave", "1", "WP-02", "--fix")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("good one", self.prompt_of("WP-02"))

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
                                env=dict(self.repo.env, FAKE_SLEEP="30"), encoding="utf-8", errors="replace")
        ledger = self.repo.dir / ".claude/state/ledger.json"
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                d = json.loads(ledger.read_text(encoding="utf-8"))
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
        (self.logs / "x").write_text("", encoding="utf-8")
        r = self.repo.run("run-review", env={"FAKE_STATUS": "PASS"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        a = self.args("REVIEW")
        self.assertEqual(a[a.index("--agent") + 1], "reviewer")
        for tool in ("Write", "Edit"):
            self.assertNotIn(tool, a)

    def test_implementers_may_run_acceptance(self):
        self.repo.run("run-wave", "1")
        self.assertIn(f"Bash({PY} acc.py:*)", self.args("WP-01"))

    def test_fix_limit(self):
        runs = self.repo.dir / ".claude/state/runs"
        runs.mkdir(parents=True, exist_ok=True)
        findings = [FINDING, dict(FINDING, wp="WP-01")]
        (runs / "REVIEW.json").write_text(json.dumps({"structured_output": {"status": "FAIL", "findings": findings}}), encoding="utf-8")
        for _ in range(2):
            self.assertEqual(self.repo.run("run-wave", "1", "WP-02", "--fix").returncode, 0)
            self.assertTrue(self.prompt_of("WP-02").startswith("Fix WP-02."))
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
        (self.repo.dir / "architecture.md").write_text(f"```json wp-manifest\n{json.dumps(m)}\n```", encoding="utf-8")
        self.assertEqual(self.repo.run("run-wave", "1").returncode, 1)
        self.assertFalse(self.called("WP-01"))


if __name__ == "__main__":
    unittest.main()
