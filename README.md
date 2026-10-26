# agentic-flow

A small team of Claude Code agents that turns an idea into tested code.
Opus designs. Sonnet builds, in parallel. You make the two decisions that matter.

<p align="center">
  <img src="docs/assets/flow.gif" width="800" alt="The flow in eight scenes. Opus writes architecture.md (contracts, acceptance criteria, work packages) and a second Opus reviews it before you approve it. A separate agent writes the acceptance tests, which fail because no code exists yet. Implementers build the work packages in parallel, and the hook blocks a write outside a package's files. Build, types, lint, tests and acceptance are verified against a baseline. A reviewer, an adversarial verifier and QA find a defect; its owner fixes it in one cycle. The documentation is brought up to date. Ready: you approve the commit.">
</p>

## How it works

1. **Design.** `/architect` (latest Opus) studies the repo, interviews you, tells you which model it is running on and waits for your go-ahead (so you can switch to Opus first), then writes `architecture.md`: exact contracts, acceptance criteria that can be checked by a command, and work packages that own disjoint files. A second Opus, which never saw the interview, reviews it. **You approve the design.**
2. **Tests first.** A test-author turns every criterion into an acceptance test before any code exists. The tests must fail. Nobody else may edit them.
3. **Build in parallel.** One implementer per work package, all at once, each writing only its own files. The plan says which existing code to imitate, with `file:line`.
4. **Verify and judge.** Build, types, lint and tests are compared against a baseline taken before anything changed, so a regression blocks and a pre-existing failure does not. A reviewer, an adversarial verifier and QA look for what the tests miss. Each real defect goes back to the package that owns it, at most twice.
5. **Document.** With the code accepted, a code map is regenerated, an agent updates README and CHANGELOG, and every link and path is checked. **You approve the commit.**

`/implement` runs steps 2 to 5 as a native Claude Code workflow: deterministic, in the background, with every agent's report validated against a schema.

## Quick start

**New project**, from the template:

```bash
gh repo create my-project --template jagosar/agentic-flow --private --clone
```

**Existing project**, with the plugin. In a Claude Code session inside the repo:

```
/plugin install agentic-flow --marketplace jagosar/agentic-flow
```

```
/agentic-flow:init
```

`init` copies the flow into `.claude/`, merges your `settings.json` without removing anything and detects your languages. Run it again after a plugin update: it replaces only the files you have not modified.

Then open `claude` in the project, accept the trust prompt, and:

```
/architect A URL shortener with a CLI and JSON storage
```

```
/implement
```

**Requirements:** a Pro, Max, Team or Enterprise plan; Dynamic workflows enabled (on Pro, in `/config`); macOS, Linux or WSL 2; `python3` 3.9+; `git`; an up-to-date Claude Code.

## What's inside

| Piece | Model | Role |
|---|---|---|
| `/architect` | latest Opus | Classifies the request, researches, interviews and writes `architecture.md` |
| `plan-reviewer` | latest Opus | Reviews the plan in a clean context before you approve it |
| `/implement` | latest Sonnet | Validates, launches the workflow and holds the commit gate |
| `agentic-implement` | script | The native workflow: tests first, waves, verification, review, QA, fixes, docs |
| `test-author` | latest Sonnet | Writes the acceptance tests from the criteria, before the code |
| `implementer` | latest Sonnet | Builds one work package, in parallel with the rest of its wave |
| `test-auditor` | latest Sonnet | Read-only; checks the acceptance tests would not pass against an empty implementation |
| `reviewer` · `verifier` | latest Sonnet | Read-only review; the verifier tries to refute every serious finding |
| `qa` | latest Sonnet | Exploratory testing beyond the acceptance criteria |
| `doc-updater` | latest Sonnet | Updates README, CHANGELOG and guides, only inside its zone |
| `gatekeeper` | latest Haiku | Runs the deterministic steps of `agentic.py` for the workflow, several per call |
| `agentic.py` | — | The guardrail hook, the deterministic steps, the ledger and the installer |

Models are set by alias (`opus`, `sonnet`, and `haiku` for the gatekeeper only), which always resolve to the newest version your Claude Code knows. `validate` fails if anyone pins another model.

## Guardrails

| Risk | Protection |
|---|---|
| An agent writes files that are not its own | A `PreToolUse` hook checks every write against the manifest. Reviewers write nothing |
| An implementer passes by weakening the test | The tests are written by another agent, before the code, and are locked. Red first, green after |
| Relaxing a linter or skipping a test | Editing existing quality config is blocked; so are unconditional `skip`, `xfail` and `only` |
| Secrets in code | Writes containing credential patterns are blocked |
| Bypassing git hooks | `--no-verify`, `commit -n` and `core.hooksPath` are blocked for everyone |
| Git writes, new dependencies, nested sessions | Blocked inside every agent of the flow |
| Writes that dodge the hook | Each stage compares file fingerprints before and after |
| Breaking what already worked | A baseline before any change: a regression blocks, a pre-existing failure is reported |
| False positives from review | An independent verifier must refute a finding with confidence to dismiss it |
| Endless fix loops | At most two fix cycles per package; then you decide |
| Documentation that invents things | The doc-updater must verify each claim; `docs-check` validates links and paths; the code map is generated without a model; claimed edits are compared with the files that actually changed |
| Prompt injection from the repo | Every agent treats repository content as data, not instructions |
| A failure in the guardrail itself | The hook fails closed inside an agent |
| Usage billed to the API by mistake | Agents run on your subscription; API keys are removed from their environment |

The full decision diagram is in [How it works](docs/how-it-works.md#4-guardrails-the-hooks-decision).

## Language rules

Common rules plus rules for Python, TypeScript/JavaScript, Go, Rust, Java, Kotlin, C#, Ruby, PHP and Swift, each with the same sections: style, errors, tests, security and the usual verify commands. `agentic.py stack` detects the languages in the repo, `rules-sync` installs the relevant ones in `.claude/rules/agentic/`, and every agent that writes or judges code receives them. When they disagree, `architecture.md` wins, then the existing code, then your own rules, then these.

## Status

Tested end to end with real agents:
- **A new project:** acceptance tests red first, two parallel waves, verification, review and QA, in about five minutes.
- **An existing codebase:** the plan cited exact `file:line` references, 13 new criteria passed and 16 existing ones stayed green. Data files from the previous version still read byte for byte.
- **The fully English flow:** the whole pipeline (prompts, ledger, hook messages, reports) ran on a new feature: wave 0, a parallel wave, the fix cycle, QA and the documentation.
- **A full cycle with an injected defect:** the reviewer and QA found it independently, the verifier upheld it, its package fixed it in one cycle, and the doc-updater brought README and CHANGELOG in line with the real CLI. About four minutes.
- **A badly written acceptance test:** the implementer could not touch it, did not cheat, and reported the exact cause. The flow stopped, and resumed from the same wave once the test was fixed.
- **The plugin and the fallback mode:** `/agentic-flow:init` installed the flow in a TypeScript repo in a real session. The earlier fallback mode (one `agentic.py run-*` command per stage) also ran end to end.

The flow tests itself with 149 Python tests and 36 workflow scenarios with simulated agents. CI pins every action by SHA, runs with minimal permissions, installs npm packages without install scripts, and validates the plugin strictly.

Not yet tested with real agents: the test-auditor, the Haiku gatekeeper, batched gatekeeper calls (`seq`), fix attempts that receive the previous diff, and the new `run-pipeline` fallback engine, including the exact shape of `claude -p --json-schema` output (`structured_report` accepts `structured_output` or JSON in `result`). They are covered by simulated agents and by a fake `claude`.

Not yet tested: the interactive interview in the UI, installing the plugin from the marketplace, and the VS Code, JetBrains, cloud and GitHub Actions setups.

## Documentation

- [How it works](docs/how-it-works.md): diagrams of the flow, the sequence, the lifecycle of a package, the hook and the permission matrix.
- [Guides](docs/how-to/README.md): terminal, Desktop, VS Code, JetBrains, web and iOS, GitHub Actions and the [plugin](docs/how-to/plugin.md).

## Credits

Several ideas are adapted from [ECC](https://github.com/affaan-m/ECC) (MIT, © 2026 Affaan Mustafa):
- the independent judge and ceremony scaled to the size of the change;
- confidence-filtered review and phased verification;
- config protection, the `--no-verify` block and the prompt-injection defence;
- the language rules and the documentation agent with its code map.
