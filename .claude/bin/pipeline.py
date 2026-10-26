"""The agentic-implement pipeline, driven from Python: the engine of the fallback mode and of CI.

It is the same pipeline as .claude/workflows/agentic-implement.js, step by step: the same agents, the same prompts and
schemas (.claude/workflows/pipeline-spec.json), the same routing and the same limits. The native workflow runs inside
Claude Code; this one runs `claude -p` processes, so it works wherever the CLI does. Keep both in step:
test_pipeline.py runs the same scenarios (conformance.json) against the two and fails if they diverge.

`Pipeline` knows nothing about processes: it receives
  gate(cmds, label)                      -> {"steps": [{"cmd", "exit_code", "output"}]} or None
  agent(prompt, agent_type, label, id)   -> the agent's structured report (a dict) or None
which agentic.py binds to real commands and real `claude -p` runs, and the tests bind to a simulated world.
"""
import concurrent.futures
import json


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


def render(template, **values):
    out = template
    for key, value in values.items():
        out = out.replace("{" + key + "}", str(value))
    return out


def threaded(thunks):
    if not thunks:
        return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(thunks)) as pool:
        return list(pool.map(lambda t: t(), thunks))


WAVE_TAIL = ["verify", "open-stage review"]


class Halted(BaseException):
    """Someone ran `agentic.py stop`: not an error of an agent, so it is not swallowed with them."""


class Pipeline:
    def __init__(self, spec, gate, agent, parallel=threaded, log=lambda m: None, start_wave=None, phase=lambda t: None,
                 wave_done=lambda wave: None):
        self.spec = spec
        self.prompts = spec["prompts"]
        self.blocking = set(spec["constants"]["blocking"])
        self.refute_confidence = spec["constants"]["refute_confidence"]
        self._gate, self._agent, self.parallel, self.log, self.phase = gate, agent, parallel, log, phase
        self.start_wave, self.wave_done = start_wave, wave_done
        self.plan = None
        self.rules = ""
        self.fix_count, self.fix_history, self.limit_pending = {}, {}, []
        self.run = {"result": "REVIEW", "stages": [], "baseline": None, "work_packages": {}, "verification": None,
                    "review": None, "qa": None, "test_audit": None, "docs": None, "fix_cycles": 0, "pending": []}

    # ---------- utilities ----------
    def stop(self, stage, detail, **extra):
        return {**self.run, "result": "REVIEW", "failed_stage": stage, "detail": detail, **extra}

    def seq(self, cmds, label):
        """Several commands in one call; None if the gate failed. Stops at the first step that fails."""
        try:
            res = self._gate(cmds, label)
        except Exception as e:  # noqa: BLE001 - a gate that blows up is a failed gate, never a crash of the pipeline
            self.log(f"{label} failed: {e}")
            return None
        return res if res and isinstance(res.get("steps"), list) else None

    @staticmethod
    def step(res, i):
        return res["steps"][i] if res and i < len(res["steps"]) else None

    @staticmethod
    def ok(st):
        return bool(st) and st["exit_code"] == 0

    def call(self, agent_type, label, prompt, id=None):
        try:
            return self._agent(prompt, agent_type, label, id)
        except Exception as e:  # noqa: BLE001
            self.log(f"{label} failed: {e}")
            return None

    @staticmethod
    def changed_by(out, ident):
        import re
        m = re.search(rf"^{ident}: \d+ file\(s\) changed(?:: (.*))?$", str(out or ""), re.M)
        return None if not m else (m.group(1).split(", ") if m.group(1) else [])

    def unchanged_claims(self, report, out, ident):
        seen = self.changed_by(out, ident)
        if not (report and isinstance(report.get("files"), list) and seen is not None):
            return []
        return [f for f in report["files"] if not any(f == s or f.endswith("/" + s) for s in seen)]

    def split_by_limit(self, defects):
        eligible = []
        for d in defects:
            if self.fix_count.get(d["wp"], 0) >= self.plan["max_fix_cycles"]:
                self.limit_pending.append({"type": "fix limit reached", **d})
            else:
                eligible.append(d)
        return eligible

    @staticmethod
    def group_by_wp(defects):
        by = {}
        for d in defects:
            by.setdefault(d["wp"], []).append(d)
        return by

    def waves_of(self, by):
        return sorted({self.plan["wave_of"][wp] for wp in by})

    def verification_defects(self, v):
        v = v or {}
        suspects = v.get("suspects") or {}
        failing = [p for p in v.get("phases") or [] if p.get("status") in ("regression", "fails")]
        evidence = "\n".join(f"{p['name']}: " + "\n".join(p.get("tail") or []) for p in failing)[:1500]
        out = []
        for wp, info in suspects.items():
            if wp not in self.plan["wave_of"]:
                continue
            acs = info.get("acs") or []
            out.append({"source": "verification", "severity": "high", "wp": wp, "file": (info.get("files") or [""])[0],
                        "problem": f"verification fails ({', '.join(info.get('phases') or [])})"
                                   + (f" on {', '.join(acs)}" if acs else ""), "evidence": evidence})
        return out

    # ---------- the pipeline ----------
    def execute(self):
        try:
            return self._execute()
        except Halted:
            return self.stop("stopped", "the run was stopped with `agentic.py stop`")

    def _execute(self):
        self.phase("Prepare")
        first = self.seq(["pipeline-plan"] if self.start_wave is not None else ["pipeline-plan", "baseline"], "prepare")
        plan_step = self.step(first, 0)
        self.plan = lines_json(plan_step["output"]) if self.ok(plan_step) else None
        if not self.plan:
            return self.stop("prepare", "pipeline-plan failed: the manifest is not valid", gate=plan_step)
        plan = self.plan
        self.rules = (f" Rules you must follow (language and common): {', '.join(plan['rules'])}."
                      if plan.get("rules") else "")
        if self.start_wave is None:
            self.phase("Baseline")
            base = self.step(first, 1)
            self.run["baseline"] = lines_json(base["output"]) if base else None
            if not self.ok(base):
                return self.stop("baseline", "a verify command could not be run (it does not exist or timed out)", gate=base)

        if self.start_wave is None and plan["acceptance"]:
            stopped = self.wave_zero()
            if stopped:
                return stopped

        self.phase("Waves")
        waves = [w for w in plan["waves"] if self.start_wave is None or w >= self.start_wave]
        by_wave = {int(k): v for k, v in plan["work_packages"].items()}
        built = self.run_waves(waves, by_wave, None, False)
        if built.get("stopped"):
            return built["stopped"]
        return self.judge(built["post"])

    def wave_zero(self):
        plan = self.plan
        self.phase("Wave 0")
        if plan["type"] == "refactor":
            base = self.step(self.seq(["acceptance --expect green"], "baseline"), 0)
            self.run["stages"].append({"stage": "baseline", "ok": self.ok(base), "check": base and base["output"]})
            if not self.ok(base):
                return self.stop("wave 0", "the existing suite is not green before the refactor", gate=base)
            return None
        open_ = self.step(self.seq(["open-stage acceptance"], "open-stage acceptance"), 0)
        if not self.ok(open_):
            return self.stop("wave 0", "the acceptance stage could not be opened", gate=open_)
        prompt = render(self.prompts["test_author"], rules=self.rules)
        round_ = 0
        while True:
            label = "test-author (revision)" if round_ else "test-author"
            at = self.call("test-author", label, prompt, "AT")
            state = "done" if at and at.get("status") == "DONE" else "blocker"
            close = self.step(self.seq([f"close-stage acceptance AT={state} --expect red"], "close-stage acceptance"), 0)
            self.run["stages"].append({"stage": "wave 0 (revision)" if round_ else "wave 0", "ok": self.ok(close),
                                       "test_author": at, "check": close and close["output"]})
            if not self.ok(close):
                return self.stop("wave 0", "the test-author cannot test some criterion: architecture.md needs review"
                                 if at and at.get("status") == "BLOCKER"
                                 else "acceptance did not end up red for the right reason (or files outside its zone were touched)",
                                 test_author=at)
            if not at or at.get("status") != "DONE":
                return self.stop("wave 0", "the test-author returned no usable report", test_author=at)
            # Red is not enough: a test can fail only because the code does not exist yet and still pass an empty stub.
            audit = self.call("test-auditor", "test-auditor (revision)" if round_ else "test-auditor",
                              render(self.prompts["test_auditor"], coverage=compact(at.get("coverage")),
                                     rules=self.rules), "REVIEW")
            if not audit:
                return self.stop("wave 0", "the test-auditor returned no report")
            weak = [f for f in audit["findings"] if f["severity"] in self.blocking]
            self.run["test_audit"] = {"rounds": round_ + 1, "weak": weak,
                                      "advisory": [f for f in audit["findings"] if f["severity"] not in self.blocking]}
            if not weak:
                return None
            if round_ >= self.spec["constants"]["audit_rounds"] - 1:
                return self.stop("wave 0", "the acceptance tests are still weak after one revision: architecture.md or "
                                 "the criteria need review", test_audit=self.run["test_audit"])
            reopen = self.step(self.seq(["open-stage acceptance"], "open-stage acceptance"), 0)
            if not self.ok(reopen):
                return self.stop("wave 0", "the acceptance stage could not be reopened", gate=reopen)
            prompt = render(self.prompts["test_author_revision"], weak=compact(weak), rules=self.rules)
            round_ += 1

    def launch_wave(self, wave, wp_ids, defects_by_wp):
        plan = self.plan

        def thunk(wp):
            def go():
                if not defects_by_wp:
                    prompt = render(self.prompts["implement"], wp=wp, rules=self.rules)
                else:
                    attempt = self.fix_count.get(wp, 0) + 1
                    retry = ""
                    if attempt > 1:
                        history = self.fix_history.get(wp, [])
                        previous = history[-2] if len(history) >= 2 else None
                        retry = render(self.prompts["fix_retry"], attempt=attempt, max=plan["max_fix_cycles"],
                                       previous=compact(previous),
                                       diff=f".claude/state/runs/{wp}.fix-{attempt - 1}.diff")
                    prompt = render(self.prompts["fix"], wp=wp, defects=compact(defects_by_wp[wp]),
                                    retry=retry, rules=self.rules)
                return self.call(plan["agents"].get(wp) or "implementer", f"{wp} (fix)" if defects_by_wp else wp, prompt, wp)
            return go
        reports = self.parallel([thunk(wp) for wp in wp_ids])
        for wp, rep in zip(wp_ids, reports):
            self.run["work_packages"][wp] = rep
        states = [f"{wp}={'error' if not rep else 'done' if rep.get('status') == 'DONE' else 'blocker'}"
                  for wp, rep in zip(wp_ids, reports)]
        return f"close-stage {wave} {' '.join(states)}{' --fix' if defects_by_wp else ''}"

    def run_waves(self, waves, by_wave, defects_by_wp, opened):
        fixing = bool(defects_by_wp)
        if not waves:
            tail = self.seq(WAVE_TAIL, "verify")
            return {"post": (tail and tail["steps"]) or []}
        for i, wave in enumerate(waves):
            if not opened:
                open_ = self.seq([f"open-stage {wave}"], f"open-stage {wave}")
                if not self.ok(self.step(open_, 0)):
                    return {"stopped": self.stop(f"{'fix ' if fixing else ''}wave {wave}", "the stage could not be opened")}
            close_cmd = self.launch_wave(wave, by_wave[wave], defects_by_wp)
            last = i == len(waves) - 1
            res = self.seq([close_cmd] + (WAVE_TAIL if last else [f"open-stage {waves[i + 1]}"]), f"close-stage {wave}")
            close = self.step(res, 0)
            good = self.ok(close)
            self.run["stages"].append({"stage": f"wave {wave} (fix)" if fixing else f"wave {wave}", "ok": good,
                                       "check": close and close["output"]})
            if not good:
                return {"stopped": self.stop(f"fix wave {wave}" if fixing else f"wave {wave}",
                                             "the fix ended with blockers or writes outside ownership" if fixing
                                             else "the wave ended with blockers, missing reports or writes outside ownership")}
            if not fixing:
                self.wave_done(wave)
            if last:
                return {"post": res["steps"][1:]}
            if not self.ok(self.step(res, 1)):
                return {"stopped": self.stop(f"wave {waves[i + 1]}", "the stage could not be opened")}
            opened = True

    def fix_cycle(self, defects, opened):
        self.run["fix_cycles"] += 1
        self.phase("Fixes")
        by = self.group_by_wp(defects)
        for wp in by:
            self.fix_history[wp] = self.fix_history.get(wp, []) + [by[wp]]
        waves = self.waves_of(by)
        by_wave = {w: sorted(wp for wp in by if self.plan["wave_of"][wp] == w) for w in waves}
        out = self.run_waves(waves, by_wave, by, opened)
        for wp in by:
            self.fix_count[wp] = self.fix_count.get(wp, 0) + 1
        return out

    def judge(self, post):
        plan, run = self.plan, self.run
        while True:
            self.phase("Verification")
            v = post[0] if post else None
            run["verification"] = lines_json(v["output"]) if v else None
            if not self.ok(v):
                # A phase fails: the packages that own what it mentions get it as a defect; if nobody owns it, a person decides.
                defects = self.verification_defects(run["verification"])
                if not defects:
                    return self.stop("verification", "a verification phase fails and no work package owns the failure (see verification)")
                eligible = self.split_by_limit(defects)
                run["pending"] = list(self.limit_pending)
                if not eligible:
                    return run
                fixed = self.fix_cycle(eligible, False)
                if fixed.get("stopped"):
                    return fixed["stopped"]
                post = fixed["post"]
                continue

            self.phase("Review")
            review = self.call("reviewer", "reviewer", render(self.prompts["reviewer"], rules=self.rules), "REVIEW")
            if not review:
                return self.stop("review", "the reviewer returned no report")
            graves = [f for f in review["findings"] if f["severity"] in self.blocking]
            verdicts = self.parallel([
                (lambda f=f: self.call("verifier", f"verify {f['file']}",
                                       render(self.prompts["verifier"], finding=compact(f)), "REVIEW"))
                for f in graves])
            # A finding is only dismissed if the verifier refutes it with confidence. Uncertainty never downgrades a blocker.
            confirmed = [f for f, vd in zip(graves, verdicts)
                         if not (vd and vd.get("is_real") is False and vd.get("confidence", 0) >= self.refute_confidence)]
            refuted = [f for f in graves if f not in confirmed]
            advisory = [f for f in review["findings"] if f["severity"] not in self.blocking]
            run["review"] = {"confirmed": confirmed, "refuted": refuted, "advisory": advisory,
                             "opened": self.ok(post[1] if len(post) > 1 else None)}
            after = self.seq([f"close-stage review REVIEW={'fail' if confirmed else 'pass'}", "open-stage qa"], "close-stage review")
            if not self.ok(self.step(after, 0)):
                return self.stop("review", "files changed during the review stage (the reviewer must not write)")

            self.phase("QA")
            qa = self.call("qa", "qa", render(self.prompts["qa"], rules=self.rules), "QA")
            if not qa:
                return self.stop("qa", "QA returned no report")
            run["qa"] = qa

            def blocking_impl(d):
                return d["kind"] == "implementation" and d["severity"] in self.blocking
            impl = ([{"source": "reviewer", **f} for f in confirmed]
                    + [{"source": "qa", **d} for d in qa["defects"] if blocking_impl(d)])
            impl = [d for d in impl if d["wp"] in plan["wave_of"]]
            design = [d for d in qa["defects"] if d["kind"] == "design"]
            unassigned = [d for d in confirmed + [d for d in qa["defects"] if blocking_impl(d)] if d["wp"] not in plan["wave_of"]]
            baseline = [{"type": "design", **d} for d in design] + [{"type": "no WP", **d} for d in unassigned]
            close_qa = f"close-stage qa QA={'pass' if qa['status'] == 'PASS' else 'fail'}"

            eligible = self.split_by_limit(impl) if impl else []
            run["pending"] = baseline + list(self.limit_pending)
            if not eligible:
                run["result"] = ("REVIEW" if impl or qa["status"] == "BLOCKER"
                                 or any(p.get("severity") in self.blocking for p in run["pending"]) else "OK")
                docs = run["result"] == "OK" and plan.get("docs")
                fin = self.seq([close_qa] + ((["codemap"] if plan.get("codemap") else []) + ["open-stage docs"] if docs else []),
                               "close-stage qa")
                if not self.ok(self.step(fin, 0)):
                    return self.stop("qa", "files changed during the QA stage outside its zone")
                if docs:
                    self.document(fin["steps"][1:])
                    if any(p.get("severity") in self.blocking for p in run["pending"]):
                        run["result"] = "REVIEW"
                return run

            first_wave = self.waves_of(self.group_by_wp(eligible))[0]
            fin = self.seq([close_qa, f"open-stage {first_wave}"], "close-stage qa")
            if not self.ok(self.step(fin, 0)):
                return self.stop("qa", "files changed during the QA stage outside its zone")
            if not self.ok(self.step(fin, 1)):
                return self.stop(f"fix wave {first_wave}", "the stage could not be opened")
            fixed = self.fix_cycle(eligible, True)
            if fixed.get("stopped"):
                return fixed["stopped"]
            post = fixed["post"]

    def document(self, steps):
        """Living documentation. `steps`: the [codemap, open-stage docs] results that came with the call that closed QA."""
        run, plan = self.run, self.plan
        self.phase("Documentation")
        run["docs"] = {"codemap": None, "report": None, "ownership_ok": False, "check": None}
        i = 0
        if plan.get("codemap"):
            cm = self.step({"steps": steps}, i)
            i += 1
            run["docs"]["codemap"] = lines_json(cm["output"]) if self.ok(cm) else None
        if not self.ok(self.step({"steps": steps}, i)):
            run["pending"].append({"type": "documentation", "severity": "medium",
                                   "problem": "the documentation stage could not be opened"})
            return
        doc = self.call("doc-updater", "doc-updater", render(self.prompts["doc_updater"], rules=self.rules), "DOCS")
        fin = self.seq([f"close-stage docs DOCS={'done' if doc and doc.get('status') == 'DONE' else 'blocker'}", "docs-check"],
                       "close-stage docs")
        close, check = self.step(fin, 0), self.step(fin, 1)
        run["docs"]["report"] = doc
        run["docs"]["ownership_ok"] = self.ok(close)
        run["docs"]["check"] = lines_json(check["output"]) if check else None
        run["stages"].append({"stage": "documentation", "ok": run["docs"]["ownership_ok"] and self.ok(check),
                              "check": close and close["output"]})
        ghost = self.unchanged_claims(doc, close and close["output"], "DOCS")
        if doc and doc.get("status") == "DONE" and ghost:
            run["pending"].append({"type": "documentation", "severity": "medium",
                                   "problem": "the doc-updater claims changes that are not on disk", "files": ghost})
        if not doc or doc.get("status") != "DONE":
            run["pending"].append({"type": "documentation", "severity": "medium",
                                   "problem": f"doc-updater blocked: {doc.get('blocker')}" if doc else "the doc-updater returned no report"})
        elif not run["docs"]["ownership_ok"]:
            run["pending"].append({"type": "documentation", "severity": "high",
                                   "problem": "the doc-updater wrote outside its zone (see docs.check)"})
        broken = (run["docs"]["check"] or {}).get("broken") or []
        if broken:
            run["pending"].append({"type": "documentation", "severity": "medium",
                                   "problem": f"{len(broken)} broken link(s) or path(s) in the documentation", "broken": broken})
        if doc and doc.get("unverified"):
            run["pending"].append({"type": "documentation", "severity": "low",
                                   "problem": "claims the doc-updater could not verify", "unverified": doc["unverified"]})
