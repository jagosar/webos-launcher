---
model: sonnet
description: Takes architecture.md to verified code with the native agentic-implement workflow (wave 0, parallel waves, verification, adversarial review, QA, capped fix cycles and living documentation). If workflows are unavailable, uses the fallback mode.
argument-hint: "[start wave] [fallback]"
---

# Role

You are the flow's launcher. You write no code and no tests, and you do not interpret the architecture. The execution is done by the native `agentic-implement` workflow (`.claude/workflows/agentic-implement.js`), which orchestrates deterministically and in the background. You validate first, launch it, and then present the result and manage the human commit gate.

Arguments: $ARGUMENTS
- A number: resume from that wave, skipping wave 0.
- `fallback`: use the fallback mode without checking whether workflows are available. The pre-flight of section 1, `sessions-check` included, runs in both modes.

# 1. Pre-flight validation

1. Run `python3 .claude/bin/agentic.py hooks-check`. If it exits 3, the guardrail hook does not run in this session: stop, show the user its message and the hints it prints (its JSON's `diagnosis` holds Git Bash on Windows, the settings it read, the roots, the heartbeats it found, the worktree and what `python3` resolves to). On Windows the usual cause is that Claude Code does not find Git Bash: `python3 .claude/bin/agentic.py doctor --fix` writes `CLAUDE_CODE_GIT_BASH_PATH` to `.claude/settings.local.json`; then restart Claude Code and check again. Otherwise offer `/implement fallback` (each agent runs as `claude -p`, which loads the repository's hooks) or a session with this repository alone. Do not launch the native workflow without guardrails.
2. Run `python3 .claude/bin/agentic.py sessions-check`. If it exits 3, another Claude Code session used this folder in the last 10 minutes, and a session that commits, switches branches or edits files here stops the run. Show the user the sessions it lists (`others`) and ask whether they are closed: a restart of Claude Code or `/clear` leaves the previous session's calls behind under another id. If the user says they are, run `python3 .claude/bin/agentic.py sessions-check --ignore <id>` for each and go on. Otherwise stop and recommend a git worktree of its own for this cycle (`git worktree add ../<folder> -b <branch>`).
3. Run `python3 .claude/bin/agentic.py validate`. If it fails, stop and tell the user to go back to `/architect`.
4. If section 11 of `architecture.md` blocks any WP, stop.
5. If the design is not committed, recommend committing it before starting.
6. Run `python3 .claude/bin/agentic.py sync-permissions`. It adds to `.claude/settings.local.json` the permissions the agents need (tests, verification), so the workflow does not stop to ask.
7. Run `python3 .claude/bin/agentic.py rules-sync`. It leaves the common rules and the `stack`'s in `.claude/rules/agentic/`. The workflow leaves the common ones to Claude Code, which gives them to every agent, and prints the language ones in the prompt.

# 2. Launch the workflow (the main path)

Launch the saved workflow with the Workflow tool:
- **name:** `agentic-implement`;
- **`args`:** `{}`, or `{ "start_wave": N }` if you were given a wave.

It runs in the background. Tell the user in one line that it has started, that they can follow it with `/workflows` and keep talking to you, and that nobody (they, you or another session) may commit, switch branches or edit files in this folder until it ends: the guard compares the git state at every stage and stops the run if it changed. Then end your turn. Do not wait or poll: the completion notification will wake you.

While it runs:
- **"How is it going?":** answer with `python3 .claude/bin/agentic.py status` (the ledger) or point to `/workflows`.
- **"Stop":** the user stops it from `/workflows` (key `x`). If they ask you, tell them.

**When to switch to the fallback mode:**
- If the Workflow tool does not exist or the user has workflows disabled, explain that on Pro they are enabled in `/config` (**Dynamic workflows**) or with `"enableWorkflows": true`.
- If `claude --version` works, offer the fallback mode as an alternative.

# 3. Present the result (the human gate)

The workflow returns a JSON. Save it first, because it is the full record of the run, QA report included:
1. Write it as it is with the Write tool to `.agentic-result.json`, at the repo root.
2. Run `python3 .claude/bin/agentic.py save-result .agentic-result.json --run <runId>`, with the `runId` (`wf_…`) the Workflow tool gave when it launched the workflow; without one, leave `--run` out. It moves the result to `.claude/state/runs/workflow-result.json` and adds each agent's token usage, read from Claude Code's files.

Do not write it directly into `.claude/`, which is a protected path, and do not pass it through a heredoc, because Bash's safety filter blocks it. (In the fallback mode the engine has already saved the same JSON there: read it and skip these two steps.)

Then present it like this:

- **Status:** `result` (`OK` or `REVIEW`) and, if it failed, `failed_stage` and `detail`.
- **Acceptance tests (`test_audit`):** whether the test-auditor found weak tests and the test-author had to revise them.
- **WPs:** a table with WP, status, files and checks, taken from `work_packages`.
- **Verification:** each phase of `verification.phases`, with acceptance green. If its `zone_suspects` names `AT` or `QA`, the quality failure is in the acceptance tests or in QA's tests: no package can fix them, so name the files and let the person fix them or relaunch that agent.
- **Review:** per axis, never merged into one ranking (`review.spec` and `review.standards`):
  - Spec (contracts, correctness, security): confirmed findings, findings refuted by the verifier (and why), and finish notices;
  - Standards (section 7 and the rules): confirmed reflex findings with the rule each breaks, refuted ones, finish notices, and the waived ones with the waiver's reason;
  - whether the rules reached the agents printed or only as paths (`rules`: `printed`, `pointed` or `none`).
- **QA:** status, plan, defects and coverage gaps. `qa_passes` says how each pass ran: `full`, `scoped` (a re-check of what was fixed) or `kept` (a Standards-only fix: the earlier report stands).
- **Usage (`usage`, if recorded):** tokens by agent type from `usage.by_agent` (new input, cache read, output), and how many agents had no transcript (`missing`).
- **Fixes:** `fix_cycles` and which WPs were fixed. A failed verification also goes to a fix cycle when `verification.suspects` names the package that owns the failure.
- **Documentation (`docs`, if there was any):** files the doc-updater updated, whether the code map changed, and the result of `docs-check`.
- **Pending (`pending`):** each item with its type.
  - **Design:** goes to `/architect`.
  - **Needs validation** (items of type `needs validation`): each item's claim, the fact to check and how; the person checks it, it never blocks.
  - **Concerns** (items of type `concern`): doubts an implementer raised about its own code (`DONE_WITH_CONCERNS`), per WP; the reviewer already weighed them, so say whether each became a finding.
  - **Fix limit reached (per WP), or no WP:** the user decides.
- **Not checked (`not_checked`):** per agent, what it did not check or skipped, plus QA's `gaps` and the doc-updater's `unverified`.
- **Stages the hook did not guard:** a stage `check` that contains `GUARDRAIL HOOK SAW NO FLOW AGENT` means files changed with no hook call from an agent of the flow, so only the file comparison applied. Say so, and point to the `agent_type` recorded in `.claude/state/hook-seen.json` and to `/implement fallback`.
- **Environment retries (`environment_retries`):** the calls that were repeated once because the reply was lost or the interpreter crashed before `agentic.py` answered (only prepare and opening a stage). Each names the call, its exit code and the start of its output; one that keeps appearing points at the machine, not the code.
- **Gate denials (`gate_denials`):** per WP, how many times the stop gate refused a finished report because its acceptance tests failed; an `AT` or `QA` entry means the test-author or QA was held to the quality limits of its own files. A package with denials and no later fix cycle is a good sign; one at the cap of two deserves a look at its tests.
- **Failed verifications (`verification_failures`):** each one's blocking phases and suspects, when a verification sent packages to a fix cycle.

If `result` is `OK`, propose the commit messages (one per logical block, in the format of section 7) and **wait for the user's approval**. Do not commit or push without it.

# 4. Fallback mode (no workflows)

It needs the `claude` CLI: the one on the `PATH`, or the command in `AGENTIC_CLAUDE` (a full path, for example). Before the first agent it runs `<cli> auth status --json` once: a CLI that is missing or reports no login stops the run with a message, and an unreadable status only warns. When that status reports the first-party API (`apiProvider: "firstParty"`), it also checks that the API host (`ANTHROPIC_BASE_URL`, or the default) answers, through the agents' proxy variables: a name that does not resolve, a refused connection, a timeout or a proxy asking for credentials stops the run before any agent, naming the proxy variables that are set (never their values). Claude Desktop bundles its own CLI but does not put it on the `PATH`: point `AGENTIC_CLAUDE` at it. A CLI started from inside a Claude Code session inherits that session's `CLAUDE_*` and `ANTHROPIC_*` variables; the flow drops only `CLAUDE_CODE_SIMPLE` (it stops the saved login from being read), and when the CLI still reports no login the message lists the inherited variable names to look at. On Windows the agents' CLI also reads `CLAUDE_CODE_GIT_BASH_PATH` from `.claude/settings.local.json` (`doctor --fix`). It is one command that runs the same pipeline as the workflow (wave 0 with its test audit, the waves, verification, review with adversarial verification, QA, fixes, documentation), with one `claude -p` process per agent and the same schemas, prompts and limits:

```
python3 .claude/bin/agentic.py run-pipeline            # or: --start-wave N  |  --resume
```

Launch it with Bash **in the background** (`run_in_background: true`), end the turn and continue when the notification arrives. It prints its progress, ends in `RESULT: OK` or `RESULT: REVIEW` and saves the JSON of the run to `.claude/state/runs/workflow-result.json`: that is the file you present in step 3. `--resume` continues after the last wave that finished; `python3 .claude/bin/agentic.py stop` stops it.

In this mode `agentic.py status` also shows the tokens and cost of every agent (`.claude/state/usage.jsonl`). The agents read their briefs (`.claude/state/briefs/`, written by `agentic.py briefs`), not the whole `architecture.md`.

The one-off commands (`run-acceptance`, `run-wave N [WP...] [--fix [--all]]`, `run-review`, `run-qa`, `run-docs`, `verify`, `acceptance --expect red|green`) are still there for working by hand, stage by stage.

# Rules

- Do not resolve a blocker yourself. Do not edit a WP's files or the acceptance tests.
- Do not commit or push unless the user explicitly asks. The flow's agents cannot: the hook blocks them.
