"""The 2.17.0 field fixes: annexes in every brief; dotfile and extensionless references and escaped pipes in section
4b; kept copies install and doctor clean up; the fallback's network check; another session refused by sessions-check;
their docs. Moved as they were from test_acceptance_v217.py (design 0024, agentic-flow 2.17.0) when 2.18.0 retired it
(design 0025); the release checks stayed behind.

Run: python3 -m unittest discover -s .claude/bin -p test_field_fixes.py
Everything runs in temporary repos or in-process. Nothing is spent, and nothing reaches the network.
"""
import http.server
import importlib.util
import json
import os
import shlex
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

BIN = Path(__file__).resolve().parent
ROOT = BIN.parents[1]
sys.path.insert(0, str(BIN))
import brief  # noqa: E402
import compat  # noqa: E402
import core  # noqa: E402
import manifest_check  # noqa: E402
import testutil  # noqa: E402
from test_agentic import MANIFEST, Repo  # noqa: E402
from test_install import InstallCase, commit  # noqa: E402

SEEN = Path(".claude/state/hook-seen.json")
PROXY_NAMES = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy",
               "NO_PROXY", "no_proxy")
UPGRADE = ".claude/state/upgrade/"
FLOW_REPO_ONLY = unittest.skipUnless(core.is_flow_repo(ROOT), "the flow's own repo")
CLAUDE = shlex.join([Path(sys.executable).as_posix()])  # shlex would eat a Windows path's backslashes


def temp_dir(test, prefix):
    d = Path(tempfile.mkdtemp(prefix=prefix))
    test.addCleanup(shutil.rmtree, d, ignore_errors=True)
    return d


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def closed_port():
    """A local port nothing listens on: bound, then released."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------- AC-001: annexes ----------

PLAN = """# Architecture: x

## 1. Goal
GOAL-TEXT
## 6. Contracts between modules
```
## Annex Z
```
## 8. Work packages and acceptance criteria
### Acceptance criteria
- AC-001 · one
  - body-one
### Work packages
#### WP-01 · mine
body-mine
## Anexo B
ANNEX-B-ROW
## 9. Test strategy
TESTS
## 10. Decisions
DECISION-TEXT
## Annex A · Catalogue
ANNEX-A-ROW
## 11. Risks
RISK-TEXT
"""
PLAN_MANIFEST = {"acceptance": {"files": ["a/"], "command": "x"},
                 "work_packages": [{"id": "WP-01", "wave": 1, "files": ["a.py"], "acs": ["AC-001"]}]}


class AnnexTests(unittest.TestCase):
    def test_ac_001_every_brief_keeps_every_annex(self):
        for ident in ("WP-01", "AT"):
            with self.subTest(brief=ident):
                out, sliced = brief.slice_architecture(PLAN, ident, PLAN_MANIFEST)
                self.assertTrue(sliced)
                self.assertIn("ANNEX-A-ROW", out)
                self.assertIn("ANNEX-B-ROW", out)
        out, _ = brief.slice_architecture(PLAN, "WP-01", PLAN_MANIFEST)
        self.assertNotIn("DECISION-TEXT", out, "section 10 stays out of a package's brief")

    def test_ac_001_split_finds_two_annexes_and_loses_nothing(self):
        parts = brief.split(PLAN)
        self.assertEqual([key for kind, key, _ in parts if kind == "annex"], ["Anexo B", "Annex A · Catalogue"])
        self.assertEqual("".join(line for _, _, lines in parts for line in lines), PLAN)

    def test_ac_001_a_plan_without_annexes_gives_the_same_brief(self):
        plain = PLAN.replace("## Anexo B\nANNEX-B-ROW\n", "").replace("## Annex A · Catalogue\nANNEX-A-ROW\n", "")
        out, _ = brief.slice_architecture(plain, "AT", PLAN_MANIFEST)
        self.assertIn("## Annex Z", out, "the fenced line is code, inside section 6")
        self.assertNotIn("ANNEX", out.replace("## Annex Z", ""))


# ---------- AC-002, AC-003: references and cells ----------

class ReferenceTests(unittest.TestCase):
    def section(self, *rows):
        return "# A\n\n## 4b. Context\n| What | Where | Source |\n|---|---|---|\n" + "\n".join(rows) + "\n\n## 5. D\n"

    def test_ac_002_a_dotfile_reference_is_checked(self):
        self.assertEqual(manifest_check.provenance_problems(self.section("| a | `.gitignore:1-2` | read |")), ([], []))
        errors, _ = manifest_check.provenance_problems(self.section("| a | `.gitignore:1-99999` | read |"))
        self.assertEqual(errors, ["section 4b, row 1: .gitignore:1-99999 does not exist or is out of range"])

    def test_ac_002_a_host_and_port_is_not_a_file_reference(self):
        errors, _ = manifest_check.provenance_problems(self.section("| a | `localhost:8080` | read |"))
        self.assertEqual(errors, ["section 4b, row 1: read needs a file:line"])

    def test_ac_002_a_file_without_an_extension_counts_when_it_exists(self):
        self.assertFalse(manifest_check.reference_candidate("Makefile:1"))
        root = temp_dir(self, "v217-root-")
        (root / "Makefile").write_text("all:\n\ttrue\n", encoding="utf-8")
        with mock.patch.object(manifest_check, "ROOT", root):
            self.assertTrue(manifest_check.reference_candidate("Makefile:1"))
            self.assertFalse(manifest_check.reference_candidate("localhost:8080"))

    def test_ac_003_an_escaped_pipe_stays_in_its_cell(self):
        self.assertEqual(manifest_check.table_cells("| a \\| b | `x` | read |"), ["a \\| b", "`x`", "read"])
        self.assertEqual(manifest_check.table_cells("| a | `x | y` | read |"), ["a", "`x | y`", "read"])


# ---------- AC-004, AC-005: kept copies ----------

class KeptCopyTests(InstallCase):
    def installed(self):
        t = self.target({}, "kept")
        r = self.install(t)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return t

    def run_flow(self, target, *args):
        return subprocess.run([sys.executable, str(target / ".claude/bin/agentic.py"), *args], cwd=target,
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              env=dict(self.env, CLAUDE_PROJECT_DIR=str(target)))

    def put(self, target, rel, text):
        p = target / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def test_ac_004_install_deletes_the_copies_it_does_not_need(self):
        t = self.installed()
        self.put(t, UPGRADE + ".claude/agents/qa.md", "an old new version\n")
        self.put(t, ".claude/agents/reviewer.md", "MY REVIEWER\n")
        commit(t, "edit reviewer")
        r = self.install(t, "--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((t / UPGRADE / ".claude/agents/qa.md").exists(), "a dry run deletes nothing")
        r = self.install(t)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse((t / UPGRADE / ".claude/agents/qa.md").exists())
        kept = t / UPGRADE / ".claude/agents/reviewer.md"
        self.assertEqual(kept.read_bytes(), (self.src / ".claude/agents/reviewer.md").read_bytes())

    def test_ac_005_doctor_tells_stale_from_live_and_fixes_the_stale(self):
        t = self.installed()
        self.put(t, ".claude/agents/reviewer.md", "MY REVIEWER\n")
        self.put(t, UPGRADE + ".claude/agents/qa.md", "stale: qa.md is intact\n")
        self.put(t, UPGRADE + ".claude/agents/reviewer.md", "live: reviewer.md is edited\n")
        self.put(t, UPGRADE + "unknown/path.md", "stale: not in the record\n")
        r = self.run_flow(t, "doctor")
        out = json.loads(r.stdout.splitlines()[-1])
        found = {f["code"]: f for f in out["findings"]}
        self.assertEqual(found["upgrade-stale"]["severity"], "auto")
        self.assertTrue(found["upgrade-stale"]["message"].startswith("2 kept version(s)"), found)
        self.assertTrue(found["upgrade-pending"]["message"].startswith("1 new version(s)"), found)
        self.assertTrue((t / UPGRADE / ".claude/agents/qa.md").exists(), "doctor without --fix writes nothing")
        r = self.run_flow(t, "doctor", "--fix")
        out = json.loads(r.stdout.splitlines()[-1])
        self.assertEqual(out["fixed"], ["upgrade-stale"])
        self.assertNotIn("upgrade-stale", [f["code"] for f in out["findings"]])
        self.assertFalse((t / UPGRADE / ".claude/agents/qa.md").exists())
        self.assertFalse((t / UPGRADE / "unknown/path.md").exists())
        self.assertTrue((t / UPGRADE / ".claude/agents/reviewer.md").exists())


# ---------- AC-006, AC-007: the network check ----------

class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.repo = Repo(MANIFEST)
        self.addCleanup(self.repo.cleanup)
        self.bin = temp_dir(self, "v217-fake-")
        self.logs = temp_dir(self, "v217-logs-")
        for name in PROXY_NAMES:
            self.repo.env.pop(name, None)
        self.repo.env.update(AGENTIC_CLAUDE=testutil.install_fake_claude(self.bin), FAKE_LOG_DIR=str(self.logs))
        self.repo.write("acc.py", "import sys\nsys.exit(0)\n")

    def test_ac_006_an_unreachable_api_stops_before_any_agent(self):
        port, proxy = closed_port(), closed_port()
        r = self.repo.run("run-acceptance", env={
            "FAKE_PROVIDER": "firstParty", "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{port}",
            "HTTPS_PROXY": f"http://user:s3cret@127.0.0.1:{proxy}"})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        for words in ("127.0.0.1", "proxy", "HTTPS_PROXY", "ANTHROPIC_BASE_URL"):
            self.assertIn(words, r.stderr)
        self.assertNotIn("s3cret", r.stderr + r.stdout)
        self.assertNotIn(f"127.0.0.1:{proxy}", r.stderr + r.stdout)
        self.assertNotIn("Traceback", r.stderr)
        self.assertTrue((self.logs / "auth.log").exists())
        self.assertEqual(testutil.fake_calls(self.logs), [])
        self.assertFalse((self.repo.dir / ".claude/state/runs").exists())

    def launch(self):
        launch = load("launch_v217", BIN / "launch.py")
        launch._CLI_CHECKED.clear()
        return launch

    def test_ac_007_any_answer_is_reachable(self):
        class Quiet(http.server.BaseHTTPRequestHandler):
            def do_HEAD(self):
                self.send_response(404)
                self.end_headers()

            def log_message(self, *args):
                pass
        server = http.server.HTTPServer(("127.0.0.1", 0), Quiet)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        launch = self.launch()
        self.assertEqual(launch._reachable(f"http://127.0.0.1:{server.server_port}", {}), (True, ""))

    def test_ac_007_each_failure_has_its_reason(self):
        launch = self.launch()
        url = "https://api.example.invalid"
        cases = [
            (urllib.error.URLError(socket.gaierror(-2, "x")), (False, "name not resolved")),
            (urllib.error.HTTPError(url, 407, "Proxy Authentication Required", {}, None),
             (False, "proxy asks for credentials (407)")),
            (urllib.error.URLError(OSError("Tunnel connection failed: 407 Proxy Authentication Required")),
             (False, "proxy asks for credentials (407)")),
            (urllib.error.URLError(ssl.SSLError(1, "certificate verify failed")), (True, "")),
            (urllib.error.HTTPError(url, 401, "Unauthorized", {}, None), (True, "")),
            (ConnectionRefusedError(), (False, "connection refused")),
            (urllib.error.URLError(ConnectionRefusedError()), (False, "connection refused")),
            (socket.timeout(), (False, "timed out")),
            (OSError("x"), (False, "connection failed")),
        ]
        for error, expected in cases:
            with self.subTest(error=repr(error)):
                def opener(request, timeout=None, error=error):
                    raise error
                self.assertEqual(launch._reachable(url, {}, opener=opener), expected)

    def test_ac_007_no_check_off_the_first_party_api(self):
        launch = self.launch()
        for status in ({"loggedIn": True, "apiProvider": "bedrock"}, {"loggedIn": True}):
            with self.subTest(status=status):
                launch._CLI_CHECKED.clear()
                with mock.patch.object(launch, "_auth_status", return_value=(0, status)), \
                        mock.patch.object(launch, "_reachable", side_effect=AssertionError("checked")) as reach, \
                        mock.patch.dict(os.environ, {"AGENTIC_CLAUDE": CLAUDE}):
                    launch.require_cli({})
                reach.assert_not_called()


# ---------- AC-008, AC-009, AC-012: sessions ----------

class SessionTests(unittest.TestCase):
    def setUp(self):
        self.repo = Repo(MANIFEST)
        self.addCleanup(self.repo.cleanup)
        self.repo.env.pop("CLAUDE_CODE_SESSION_ID", None)

    def seen(self):
        return json.loads((self.repo.dir / SEEN).read_text(encoding="utf-8"))

    def put_seen(self, data):
        self.repo.write(SEEN.as_posix(), json.dumps(data))

    def check(self, *args, env=None):
        r = self.repo.run("sessions-check", *args, env=dict({"CLAUDECODE": "1"}, **(env or {})))
        lines = [line for line in r.stdout.splitlines() if line.startswith("{")]
        return r, (json.loads(lines[-1]) if lines else None)

    def test_ac_008_the_hook_records_main_thread_sessions_only(self):
        self.put_seen({"ts": time.time(), "sessions": {"s-old": time.time() - 700}})
        bash = {"tool_name": "Bash", "tool_input": {"command": "ls"}}
        self.repo.hook(dict(bash, session_id="s-main"))
        self.repo.hook(dict(bash, session_id="s-agent", agent_type="implementer"))
        self.repo.hook(dict(bash, session_id="s-wp"), wp="WP-01")
        self.repo.hook(bash)
        seen = self.seen()
        self.assertEqual(sorted(seen["sessions"]), ["s-main"])
        self.assertEqual(seen["session_id"], "s-wp", "the last payload that carried an id")

    def test_ac_009_another_recent_session_refuses(self):
        now = time.time()
        self.put_seen({"ts": now, "session_id": "me-000000", "sessions": {"me-000000": now, "other-11111": now - 120}})
        r, out = self.check()
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertFalse(out["ok"])
        self.assertEqual([o["session"] for o in out["others"]], ["other-11"])
        self.assertAlmostEqual(out["others"][0]["age_s"], 120, delta=5)
        self.assertIn("git worktree", r.stderr)

    def test_ac_009_an_old_session_or_no_call_passes(self):
        now = time.time()
        self.put_seen({"ts": now, "session_id": "me-000000", "sessions": {"me-000000": now, "other-11111": now - 700}})
        r, out = self.check()
        self.assertEqual((r.returncode, out["others"]), (0, []), r.stdout + r.stderr)
        r = self.repo.run("sessions-check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(json.loads(r.stdout.splitlines()[-1])["mode"], "not checked")
        self.put_seen({"ts": now, "sessions": {"other-11111": now - 120}})
        r, out = self.check()
        self.assertEqual((r.returncode, out["mode"]), (0, "not checked"), r.stdout + r.stderr)

    def test_ac_009_the_environment_names_the_own_session(self):
        now = time.time()
        self.put_seen({"ts": now, "session_id": "me-000000", "sessions": {"me-000000": now, "other-11111": now - 120}})
        r, out = self.check(env={"CLAUDE_CODE_SESSION_ID": "other-11111"})
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertEqual([o["session"] for o in out["others"]], ["me-00000"])

    def test_ac_012_a_closed_session_can_be_let_through(self):
        now = time.time()
        self.put_seen({"ts": now, "session_id": "me-000000", "sessions": {"me-000000": now, "other-11111": now - 120,
                                                                          "third-2222": now - 60}})
        r, out = self.check("--ignore", "other-11", "--ignore", "third-22")
        self.assertEqual((r.returncode, out["others"]), (0, []), r.stdout + r.stderr)
        self.assertEqual(sorted(self.seen()["sessions"]), ["me-000000"])
        r, _ = self.check()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for args in (("--ignore",), ("--bogus",)):
            with self.subTest(args=args):
                r, _ = self.check(*args)
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                self.assertIn("Uso: sessions-check", r.stderr)

    def test_ac_012_only_the_given_sessions_are_forgotten(self):
        now = time.time()
        self.put_seen({"ts": now, "session_id": "me-000000", "sessions": {"me-000000": now, "other-11111": now - 120,
                                                                          "third-2222": now - 60}})
        r, out = self.check("--ignore", "other-11")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertEqual([o["session"] for o in out["others"]], ["third-22"])
        self.assertIn("third-2222", self.seen()["sessions"])


# ---------- AC-010, AC-011: docs and release ----------

@FLOW_REPO_ONLY  # a project does not carry the flow's own guides
class DocsTests(unittest.TestCase):
    def read(self, rel):
        return (ROOT / rel).read_text(encoding="utf-8")

    def test_ac_010_implement_runs_sessions_check(self):
        text = self.read(".claude/commands/implement.md")
        for words in ("agentic.py sessions-check", "--ignore", "git worktree"):
            self.assertIn(words, text)

    def test_ac_010_architect_documents_annexes(self):
        text = self.read(".claude/commands/architect.md")
        self.assertRegex(text, r"(?i)annex")

    def test_ac_010_guides_name_the_new_checks(self):
        hub = self.read("docs/how-to/README.md")
        self.assertIn("sessions-check", hub)
        self.assertLessEqual(len(hub.splitlines()), 260)
        works = self.read("docs/how-it-works.md")
        self.assertIn("sessions-check", works)
        self.assertRegex(works, r"(?i)network|reach the API")


if __name__ == "__main__":
    unittest.main()
