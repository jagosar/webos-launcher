"""Helpers for the flow's tests that must work on every system: fake executables and the fake `claude` command.

Used by the flow's test files (test_*.py). On Windows a script with a shebang cannot be run
directly, so a fake program is a `.py` file with a `.cmd` beside it; on POSIX it is an executable script.
"""
import json
import shlex
import sys
from pathlib import Path

import compat

AGENTIC_PATH = Path(__file__).resolve().parent / "agentic.py"


def fake_exe(directory, name: str, python_source: str) -> Path:
    """An executable `name` that can be found on PATH (with `directory` on it) and runs python_source with this
    interpreter. Returns the path of the program to run. For project commands in tests, not for `claude`."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if compat.IS_WINDOWS:
        (directory / f"{name}.py").write_text(python_source, encoding="utf-8")
        cmd = directory / f"{name}.cmd"
        cmd.write_text(f'@"{sys.executable}" "%~dp0{name}.py" %*\r\n', encoding="utf-8")
        return cmd
    script = directory / name
    script.write_text(f"#!{sys.executable}\n{python_source}", encoding="utf-8")
    script.chmod(0o755)
    return script


def claude_command(fake_py) -> str:
    """The AGENTIC_CLAUDE value that runs the Python file fake_py with this interpreter."""
    return shlex.join([Path(sys.executable).as_posix(), Path(fake_py).as_posix()])


# The fake `claude -p` every launcher test runs, through AGENTIC_CLAUDE. It logs each call to its own file in
# FAKE_LOG_DIR (parallel appends to one shared file can lose a line on Windows) and answers with the fields the
# schema requires. Environment knobs:
#   FAKE_STATUS  the status to report (default: PASS for judges, DONE for the rest)
#   FAKE_SLEEP   seconds to wait before answering
#   FAKE_WRITE   implementers go through the hook once, then write their package's file;  FAKE_ROGUE  WP-02 also writes a file nobody owns
#   FAKE_MODE    "structured" (default, with token usage), "nousage", or "text" (the old STATUS: format)
#   FAKE_REVIEW_FINDING / FAKE_QA_DEFECT  a WP: the reviewer / QA reports one high defect on it, on its first call only
#   FAKE_NEEDS_VALIDATION  a WP: the reviewer adds one needs_validation item on it;  FAKE_NOT_CHECKED  every report
#   FAKE_CONCERNS  implementers answer DONE_WITH_CONCERNS with one concern;  FAKE_NEEDS_CONTEXT  a WP: it answers
#   NEEDS_CONTEXT on its first call only
#   whose schema requires not_checked says ["nothing else"]
#   FAKE_RE_REVIEW  the verdict a re-review gives every fingerprint of its prompt (default ADDRESSED)
#   FAKE_RESUME_FAILS  a call with --resume answers nothing
# Every answer names the session "s-<the fake's process id>"; the log records that pid.
FAKE_CLAUDE = """\
import json, os, re, sys, time
argv = sys.argv[1:]
prompt = sys.stdin.buffer.read().decode("utf-8")
agent = argv[argv.index("--agent") + 1]
ident = os.environ.get("AGENTIC_WP")
start = time.time_ns()
log = os.path.join(os.environ["FAKE_LOG_DIR"], f"call-{start}-{os.getpid()}.json")
call = {"t": start, "pid": os.getpid(), "agent": agent, "id": ident, "argv": argv, "prompt": prompt,
        "env": {k: os.environ.get(k) for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                                               "CLAUDE_CODE_DISABLE_CLAUDE_MDS", "CLAUDE_CODE_DISABLE_AUTO_MEMORY")}}
def save():
    with open(log, "w", encoding="utf-8") as f:
        json.dump(call, f)
save()
time.sleep(float(os.environ.get("FAKE_SLEEP", "0")))
call["end"] = time.time_ns()
save()
mode = os.environ.get("FAKE_MODE", "structured")
if "--resume" in argv and os.environ.get("FAKE_RESUME_FAILS"):
    sys.exit(0)
if mode == "text":
    print(json.dumps({"result": "STATUS: DONE"}))
    sys.exit(0)
def write(rel):
    os.makedirs(os.path.dirname(rel) or ".", exist_ok=True)
    with open(rel, "w", encoding="utf-8") as f:
        f.write("x = 1\\n")
def through_hook():
    # A real agent's tool calls go through the PreToolUse hook; the fake goes through it once before it writes, so the
    # heartbeat the stage check reads exists (AGENTIC_WP and CLAUDE_PROJECT_DIR come from the launcher).
    import subprocess
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "true"}}).encode("utf-8")
    subprocess.run([sys.executable, AGENTIC, "hook"], input=payload, capture_output=True)
if agent == "implementer" and os.environ.get("FAKE_WRITE"):
    through_hook()
    write({"WP-01": "src/a.py", "WP-02": "src/b/x.py", "WP-03": "src/c.py"}[ident])
    if os.environ.get("FAKE_ROGUE") and ident == "WP-02":
        write("other.py")
judges = ("qa", "reviewer", "standards-reviewer", "test-auditor")
values = {"status": os.environ.get("FAKE_STATUS", "PASS" if agent in judges else "DONE"), "wp": ident, "blocker": None,
          "coverage": [{"ac": "AC-001", "tests": ["t"]}], "concerns": [], "missing_context": None}
if agent == "implementer" and os.environ.get("FAKE_CONCERNS"):
    values.update(status="DONE_WITH_CONCERNS", concerns=[f"the retry loop in src/{ident}.py may spin"])
context_marker = os.path.join(os.environ["FAKE_LOG_DIR"], "FAKE_NEEDS_CONTEXT")
if agent == "implementer" and os.environ.get("FAKE_NEEDS_CONTEXT") == ident and not os.path.exists(context_marker):
    open(context_marker, "w").close()
    values.update(status="NEEDS_CONTEXT", missing_context={"what": "the error type of the contract", "section": "6"})
once = {"reviewer": "FAKE_REVIEW_FINDING", "qa": "FAKE_QA_DEFECT"}.get(agent)
first = once and os.environ.get(once) and not os.path.exists(os.path.join(os.environ["FAKE_LOG_DIR"], once))
if first:
    open(os.path.join(os.environ["FAKE_LOG_DIR"], once), "w").close()
    wp = os.environ[once]
    if agent == "reviewer":
        values.update(status="FAIL", findings=[{"severity": "high", "wp": wp, "file": "src/a.py", "problem": "p",
                                                 "evidence": "e", "fix": "f", "if_skipped": "s"}])
    else:
        values.update(status="FAIL", defects=[{"id": "D1", "severity": "high", "wp": wp, "kind": "implementation",
                                                "section": "6", "repro": "r", "fix": "f", "if_skipped": "s"}])
if agent == "reviewer" and os.environ.get("FAKE_NEEDS_VALIDATION"):
    values["needs_validation"] = [{"wp": os.environ["FAKE_NEEDS_VALIDATION"], "file": "src/a.py", "kind": "external",
                                   "claim": "c", "fact": "a deployment setting", "how_to_check": "h"}]
if agent == "standards-reviewer":
    values["axis"] = "standards"
if os.environ.get("FAKE_NOT_CHECKED"):
    values["not_checked"] = ["nothing else"]
required = json.loads(argv[argv.index("--json-schema") + 1]).get("required", [])
if "verdicts" in required:
    verdict = os.environ.get("FAKE_RE_REVIEW", "ADDRESSED")
    values.update(status="PASS" if verdict == "ADDRESSED" else "FAIL", verdicts=[
        {"fingerprint": fp, "verdict": verdict, "evidence": "line 1"}
        for fp in re.findall(r'"fingerprint":"(F[0-9a-f]{8}(?:-\\d+)?)"', prompt)])
out = {"type": "result", "session_id": f"s-{os.getpid()}",
       "structured_output": {k: values.get(k, []) for k in required}}
if mode == "structured":
    out.update(usage={"input_tokens": 100, "cache_creation_input_tokens": 20, "cache_read_input_tokens": 300,
                      "output_tokens": 50}, total_cost_usd=0.01, num_turns=3)
print(json.dumps(out))
"""


def install_fake_claude(directory) -> str:
    """Writes FAKE_CLAUDE in directory and returns the AGENTIC_CLAUDE value that runs it."""
    fake = Path(directory) / "claude_fake.py"
    # The temporary repos have no .claude/bin: the fake runs the hook of the flow these tests belong to.
    fake.write_text(f"AGENTIC = {str(AGENTIC_PATH)!r}\n" + FAKE_CLAUDE, encoding="utf-8")
    return claude_command(fake)


def fake_calls(log_dir, ident=None) -> list:
    """The fake's calls, oldest first (optionally only those of one AGENTIC_WP)."""
    calls = sorted((json.loads(f.read_text(encoding="utf-8")) for f in Path(log_dir).glob("call-*.json")),
                   key=lambda c: c["t"])
    return [c for c in calls if ident is None or c["id"] == ident]
