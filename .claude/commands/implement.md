---
model: sonnet
description: Takes architecture.md to verified code with the native agentic-implement workflow (wave 0, parallel waves, verification, adversarial review, QA, capped fix cycles and living documentation). If workflows are unavailable, uses the fallback mode.
argument-hint: "[start wave] [fallback]"
---

# Role

You are the flow's launcher. You write no code and no tests, and you do not interpret the architecture. The execution is done by the native `agentic-implement` workflow (`.claude/workflows/agentic-implement.js`), which orchestrates deterministically and in the background. You validate first, launch it, and then present the result and manage the human commit gate.

Arguments: $ARGUMENTS
- A number: resume from that wave, skipping wave 0.
- `fallback`: use the fallback mode without checking anything.

# 1. Pre-flight validation

1. Run `python3 .claude/bin/agentic.py validate`. If it fails, stop and tell the user to go back to `/architect`.
2. If section 11 of `architecture.md` blocks any WP, stop.
3. If the design is not committed, recommend committing it before starting.
4. Run `python3 .claude/bin/agentic.py sync-permissions`. It adds to `.claude/settings.local.json` the permissions the agents need (tests, verification), so the workflow does not stop to ask.
5. Run `python3 .claude/bin/agentic.py rules-sync`. It leaves the common rules and the `stack`'s in `.claude/rules/agentic/`, which the agents receive in their prompt.

# 2. Launch the workflow (the main path)

Launch the saved workflow with the Workflow tool:
- **name:** `agentic-implement`;
- **`args`:** `{}`, or `{ "start_wave": N }` if you were given a wave.

It runs in the background. Tell the user in one line that it has started, that they can follow it with `/workflows` and keep talking to you, and end your turn. Do not wait or poll: the completion notification will wake you.

While it runs:
- **"How is it going?":** answer with `python3 .claude/bin/agentic.py status` (the ledger) or point to `/workflows`.
- **"Stop":** the user stops it from `/workflows` (key `x`). If they ask you, tell them.

**When to switch to the fallback mode:**
- If the Workflow tool does not exist or the user has workflows disabled, explain that on Pro they are enabled in `/config` (**Dynamic workflows**) or with `"enableWorkflows": true`.
- If `claude --version` works, offer the fallback mode as an alternative.

# 3. Present the result (the human gate)

The workflow returns a JSON. Save it first, because it is the full record of the run, QA report included:
1. Write it as it is with the Write tool to `.agentic-result.json`, at the repo root.
2. Run `python3 .claude/bin/agentic.py save-result .agentic-result.json`, which moves it to `.claude/state/runs/workflow-result.json`.

Do not write it directly into `.claude/`, which is a protected path, and do not pass it through a heredoc, because Bash's safety filter blocks it. (In the fallback mode the engine has already saved the same JSON there: read it and skip these two steps.)

Then present it like this:

- **Status:** `result` (`OK` or `REVIEW`) and, if it failed, `failed_stage` and `detail`.
- **Acceptance tests (`test_audit`):** whether the test-auditor found weak tests and the test-author had to revise them.
- **WPs:** a table with WP, status, files and checks, taken from `work_packages`.
- **Verification:** each phase of `verification.phases`, with acceptance green.
- **Review:**
  - confirmed findings;
  - findings refuted by the verifier (and why);
  - medium and low notices.
- **QA:** status, plan, defects and coverage gaps.
- **Fixes:** `fix_cycles` and which WPs were fixed. A failed verification also goes to a fix cycle when `verification.suspects` names the package that owns the failure.
- **Documentation (`docs`, if there was any):** files the doc-updater updated, whether the code map changed, and the result of `docs-check`.
- **Pending (`pending`):** each item with its type.
  - **Design:** goes to `/architect`.
  - **Fix limit reached (per WP), or no WP:** the user decides.

If `result` is `OK`, propose the commit messages (one per logical block, in the format of section 7) and **wait for the user's approval**. Do not commit or push without it.

# 4. Fallback mode (no workflows)

It needs the `claude` CLI on the `PATH`. It is one command that runs the same pipeline as the workflow (wave 0 with its test audit, the waves, verification, review with adversarial verification, QA, fixes, documentation), with one `claude -p` process per agent and the same schemas, prompts and limits:

```
python3 .claude/bin/agentic.py run-pipeline            # or: --start-wave N  |  --resume
```

Launch it with Bash **in the background** (`run_in_background: true`), end the turn and continue when the notification arrives. It prints its progress, ends in `RESULT: OK` or `RESULT: REVIEW` and saves the JSON of the run to `.claude/state/runs/workflow-result.json`: that is the file you present in step 3. `--resume` continues after the last wave that finished; `python3 .claude/bin/agentic.py stop` stops it.

The one-off commands (`run-acceptance`, `run-wave N [WP...] [--fix]`, `run-review`, `run-qa`, `run-docs`, `verify`, `acceptance --expect red|green`) are still there for working by hand, stage by stage.

# Rules

- Do not resolve a blocker yourself. Do not edit a WP's files or the acceptance tests.
- Do not commit or push unless the user explicitly asks. The flow's agents cannot: the hook blocks them.
