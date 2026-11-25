"""The module-level helpers of the fallback engine (pipeline.py): pure functions and constants with no state of the
Pipeline class, moved here to keep pipeline.py within the quality limits (design 0032)."""
import concurrent.futures
import json
import re

from findings import blocks, text_sum


def lines_json(output):
    """The last line of the output that is a JSON object, parsed; None if there is none."""
    lines = [ln for ln in str(output or "").strip().split("\n") if ln.strip().startswith("{")]
    try:
        return json.loads(lines[-1]) if lines else None
    except ValueError:
        return None


def compact(value):
    """JSON as the workflow's JSON.stringify writes it, so both engines send agents the same text."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


def render(template, **values):
    """Every {name} of the template replaced in one pass: inserted text (a printed rule, a caller) is never expanded."""
    return PLACEHOLDER_RE.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), template)


def brief(ident):
    """Where `agentic.py briefs` writes the slice of architecture.md an agent reads."""
    return f".claude/state/briefs/{ident}.md"


def threaded(thunks):
    if not thunks:
        return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(thunks)) as pool:
        return list(pool.map(lambda t: t(), thunks))


# verify and baseline run the whole suite: each goes in a gatekeeper call of its own, so it fits in one Bash timeout.
WAVE_TAIL = ["verify", "open-stage review", "callers"]
ANSWERED = (0, 1, 3)  # agentic.py's own exits: success, die(), a refusal; any other with no JSON is a crash
RETRY_OUTPUT = 300  # characters of a crashed step's output kept in environment_retries
NOT_RUN_RE = re.compile(r"^\s*not run:", re.I)  # an implementer check it could not run
HOOKS_DEAD = ("the guardrail hook is not running in this session: run `agentic.py hooks-check` for the cause, "
              "or use /implement fallback")
LOST = ("the gatekeeper returned no usable result (a timeout or garbled output); the command may have run anyway: "
        "check `agentic.py status` before resuming")


class Halted(BaseException):
    """Someone ran `agentic.py stop`: not an error of an agent, so it is not swallowed with them."""


CALLERS_PROMPT = ("\nCallers, outside the diff, of the public symbols this change defines or modifies "
                  "(the reviewer must read them), as JSON:\n{items}\nA change that breaks one of them is a finding "
                  "on the package that "
                  "owns the changed symbol, even though the caller's file was not touched.{truncated}")
CALLERS_TRUNCATED = " The list is truncated."


def callers_of(step):
    """(callers, truncated) a `callers` step reported; ([], False) when the step is missing, failed or is not its
    JSON."""
    found = lines_json(step["output"]) if step else None
    items = found.get("callers") if isinstance(found, dict) else None
    return (items, bool(found.get("truncated"))) if isinstance(items, list) and items else ([], False)


CONCERNS_PROMPT = ("\nThe implementers finished with these concerns about their own code, as JSON:\n{items}\n"
                   "Check each one: confirm it as a finding with its severity, or leave it out if the code is right.")


def wave_states(wp_ids, reports, fix_count=None):
    """The close-stage marks of a wave. DONE and DONE_WITH_CONCERNS are done; no report is error; anything else
    (NEEDS_CONTEXT after its re-dispatch, BLOCKED, or a status outside the schema) is a blocker. In a fix wave
    (fix_count given) each mark carries the attempt in this run, @N, which names the diff the next attempt reads."""
    done = ("DONE", "DONE_WITH_CONCERNS")
    return [f"{wp}={'error' if not rep else 'done' if rep.get('status') in done else 'blocker'}"
            + ("" if fix_count is None else f"@{fix_count.get(wp, 0) + 1}") for wp, rep in zip(wp_ids, reports)]


def failing_lines(phase):
    """A failing phase's lines for a fix: its new failures, else its failing lines, else the end of its output."""
    for key in ("new_failures", "failing_lines"):
        if isinstance(phase.get(key), list) and phase[key]:
            return phase[key]
    return phase.get("tail") or []


def gate_line(output):
    """The stop gate's denials in a close-stage output ("GATE: WP-01=1 WP-02=0"), per package; {} if absent."""
    m = re.search(r"^GATE: (.*)$", str(output or ""), re.M)
    return {k: int(v) for k, v in re.findall(r"(WP-\d+|AT|QA)=(\d+)", m.group(1))} if m else {}


def concern_item(wp, text):
    """A pending item for a DONE_WITH_CONCERNS doubt: no severity, so it never blocks; the reviewer weighs it."""
    return {"type": "concern", "source": wp, "wp": wp, "problem": text}


def untested_items(wp, report):
    """A needs-validation item for each check the implementer reported it could not run (passed false, a detail that
    starts with "not run:"): never a blocker; the person runs it."""
    checks = report.get("checks") if isinstance(report, dict) else None
    return [{"type": "needs validation", "source": wp, "wp": wp, "file": None, "kind": "tests not run",
             "problem": c["command"], "claim": c["command"], "fact": c["detail"], "how_to_check": f"run {c['command']}"}
            for c in checks or [] if not_run(c)]


def not_run(check):
    """A check reported as not run: passed false and a detail that starts with "not run:"."""
    return (isinstance(check, dict) and check.get("passed") is False and isinstance(check.get("command"), str)
            and isinstance(check.get("detail"), str) and bool(NOT_RUN_RE.match(check["detail"])))


def missing_context(report):
    """What a NEEDS_CONTEXT report asked for, as text, or None when the report is malformed (then it is a blocker)."""
    mc = report.get("missing_context")
    if not isinstance(mc, dict) or not isinstance(mc.get("what"), str):
        return None
    return mc["what"] + (f" (section {mc['section']})" if isinstance(mc.get("section"), str) and mc["section"] else "")


def pending_key(d):
    """One pending item per package, source, file and problem: the same defect is listed once, not once per cycle."""
    return (d.get("wp"), d.get("source"), d.get("file"), d.get("problem") or d.get("repro"))


REVIEW_SOURCES = {"spec": "reviewer", "standards": "standards-reviewer"}
AXES = ("spec", "standards")
AXIS_NAMES = {"spec": "Spec", "standards": "Standards"}
NO_RE_REVIEW = {"spec": "the reviewer returned no re-review report",
                "standards": "the standards reviewer returned no re-review report"}
RE_REVIEW_SCHEMA = "re-review"
POINTED_RULES = " Rules you must follow (language and common): {paths}."


def rules_of(plan):
    """(text for the prompts, how it arrived): the printed rules when they arrived whole, else their paths."""
    text = plan.get("rules_text")
    if isinstance(text, str) and text and text_sum(text) == plan.get("rules_sum"):
        return text, "printed"
    if plan.get("rules"):
        return render(POINTED_RULES, paths=", ".join(plan["rules"])), "pointed"
    return "", "none"


def acceptance_blocked(wp_ids, reports):
    """The packages that are BLOCKED only by the acceptance tests (blocker.in "acceptance"), sorted; [] unless every
    other report of the wave is DONE or DONE_WITH_CONCERNS. A missing report counts as not done (design 0032)."""
    pairs = list(zip(wp_ids, reports))
    blocked = [wp for wp, rep in pairs if rep and rep.get("status") == "BLOCKED"
               and isinstance(rep.get("blocker"), dict) and rep["blocker"].get("in") == "acceptance"]
    others = [rep for wp, rep in pairs if wp not in blocked]
    if not blocked or any(not rep or rep.get("status") not in ("DONE", "DONE_WITH_CONCERNS") for rep in others):
        return []
    return sorted(blocked)
