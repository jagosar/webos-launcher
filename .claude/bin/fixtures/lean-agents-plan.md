# Architecture: lean agents — per-agent briefs, one report format, token telemetry
Type: change · Size: standard

## 1. Goal
Every agent of the flow reads the whole `architecture.md`, even an implementer that only needs its own package; that is the largest token cost of a run, multiplied by every package and every fix attempt. This change gives the implementers and the test-author a **brief**: a deterministic slice of `architecture.md` with only what they need. It also removes the second report format (text `STATUS:`), so every launcher uses one runner with JSON schemas, deletes three dead commands, and records the token usage of every agent run in the fallback engine, so the next optimisations (see §11, the rules delivered twice) are decided with data.

## 2. Scope
### Included
- `brief.py`: the slicer. `agentic.py briefs`: writes one brief per WP and one for `AT`. Both engines point those agents to their brief.
- One agent runner in `agentic.py` (`run_agent`) used by `run-pipeline` and by the one-off `run-acceptance`, `run-wave`, `run-review`, `run-qa`, `run-docs`, all with `--json-schema` and the prompts of `pipeline-spec.json`. The `STATUS:` text path is deleted.
- Deleting the unused commands `wave-plan`, `open-wave`, `close-wave`.
- Token telemetry for every agent launched by `agentic.py` (`.claude/state/usage.jsonl`, `status`, the `run-pipeline` result).
- Agent definitions, `/architect` template, `/implement` and docs updated to match.
### Excluded (non-goals)
- Removing the language rules from prompts (the "rules delivered twice" finding): measured first with this telemetry, decided in a later cycle.
- Telemetry in the native workflow: the workflow's `agent()` does not expose usage (see §11).
- Briefs for reviewer, verifier, QA and doc-updater: they judge the integrated system and keep the whole document.
- Splitting `agentic.py` into modules.

## 3. Stack
Python 3 standard library only (no new dependency), Node ≥ 18 for the workflow tests. No package manager.
- Build: `python3 -m compileall -q .claude/bin`
- Tests: `python3 -m unittest discover -s .claude/bin -p "test_*.py"` (it also runs `node .claude/bin/test_workflow.mjs` through `WorkflowLogicTests`, and the conformance tests through `test_pipeline.py`).
- Acceptance: `python3 -m unittest discover -s .claude/bin -p "test_acceptance.py"`.

## 4. Repository structure
```
.claude/bin/brief.py                 WP-01 (new)
.claude/bin/test_brief.py            WP-01 (new)
.claude/bin/agentic.py               WP-02
.claude/bin/test_agentic.py          WP-02
.claude/workflows/pipeline-spec.json WP-02
.claude/workflows/agentic-implement.js WP-03
.claude/bin/pipeline.py              WP-03
.claude/bin/test_workflow.mjs        WP-03
.claude/bin/test_pipeline.py         WP-03
.claude/bin/conformance.json         WP-03
.claude/agents/*.md                  WP-04
.claude/commands/architect.md        WP-04
.claude/commands/implement.md        WP-04
.claude/bin/test_definitions.py      WP-04
README.md, docs/how-it-works.md, docs/how-to/README.md, docs/how-to/terminal.md   WP-05
Waves: WP-01 (wave 1) → WP-02 (wave 2) → WP-03, WP-04, WP-05 (wave 3); see §8.
.claude/bin/test_acceptance.py       AT
```

## 4b. Existing code context
| What an implementer needs to know | Where it is | Pattern or rule |
|---|---|---|
| Module style for a helper imported by `agentic.py` | `.claude/bin/pipeline.py:1-45` | Module docstring stating purpose and callers; pure functions; stdlib only |
| How `agentic.py` imports a sibling module | `agentic.py:1103-1106` (`pipeline_module`) | `sys.path.insert(0, dir)` then `import`; mirror it as `brief_module()` |
| The manifest regex | `agentic.py:149` (`MANIFEST_RE`) | Same regex shape in `brief.py` (it must not import `agentic`) |
| Today's agent launcher inside `run-pipeline` | `agentic.py:1136-1162` (`agent` closure) | It becomes the module-level `run_agent` (§6); argv, env, timeout and process group stay identical |
| Parsing the structured answer | `agentic.py:1082-1101` (`structured_report`) | Reuse as is |
| Old text launcher to delete | `agentic.py:581-623` (`launch`; it also calls `snapshot(label)` at 585, which the one-off commands must keep), `1397-1407` (`parse_status`), `152` (`STATUS_RE`) | Delete; `finish` (`1386-1395`) stays |
| One-off launchers to rewire | `agentic.py:625-694` (`run_wave`, `run_acceptance`, `run_review`, `run_docs`), `1376-1384` (`run_qa`) | Keep names, CLI and exit codes (0 OK, 3 REVIEW, 4 fix limit, 1 usage) |
| Dead commands | `agentic.py:1763-1794` (`wave-plan`, `open-wave`, `close-wave` branches) | Delete; `snapshot` and `check-wave` stay (`1803-1810`) |
| What the installer copies | `agentic.py:117-118` (`INSTALL_GLOBS`) | Add `.claude/bin/brief.py`, or installed repos fail on `import brief` |
| Hook messages that name the old format | `agentic.py:1572, 1581, 1642, 1645` | `return STATUS: BLOCKER` → `return BLOCKER` (the report is JSON now) |
| Commands the gatekeeper may batch | `agentic.py:1018` (`SEQ_COMMANDS`) | Add `briefs` |
| Ledger writes and rendering | `agentic.py:1440-1446` (`ledger_update`), `1471-1492` (`ledger_render`) | Rendering is pure from `ledger.json`; add the Usage section there, from `usage.jsonl` |
| Rules suffix of a prompt | `agentic.py:740-742` (`rules_hint`), `agentic-implement.js:294` (`RULES`) | Byte-identical strings; keep them |
| Prompt rendering in both engines | `pipeline.py:26-36` (`compact`, `render`); `agentic-implement.js:218-230, 313, 326, 335` | Prompts live in `pipeline-spec.json`; the JS carries copies (no disk access) |
| Prepare step of both engines | `agentic-implement.js:289-300`, `pipeline.py:148-160` | `seq` of `pipeline-plan` + `baseline`; steps read by index |
| Conformance between engines | `test_pipeline.py:29-140`, `test_workflow.mjs` (`fromDecl`, `summarize`, `--dump`) | Same simulated world in both; prompts must match byte for byte |
| Fake `claude` for end-to-end tests | `test_pipeline.py:188-225`, `test_agentic.py:32-43` | A Python script in a temp `PATH` dir, logging argv per call |
| Agent report contracts in tests | `test_definitions.py:22-33, 105-113` | The `status` key is the text format; it is replaced (§8, WP-04) |
| Spec location for engines | `agentic.py:1060` (`PIPELINE_SPEC = ROOT/.claude/workflows/pipeline-spec.json`) | Same location for the one-off commands; test repos must copy `.claude/workflows/` (`test_pipeline.py:237`) |
| Files nobody may own | `agentic.py:353-357` (validate), hook `agentic.py:1636-1642` | `.claude/` is protected: see §10 D1 |

## 5. Data model
- **Brief file:** `.claude/state/briefs/<ID>.md`, `<ID>` ∈ the manifest's WP ids ∪ {`AT`}. Markdown, UTF-8.
- **Usage record:** one JSON object per line in `.claude/state/usage.jsonl`, appended after each agent run launched by `agentic.py`:
  `{"id": "WP-01", "agent": "implementer", "label": "WP-01", "input": 120, "cache_read": 300, "output": 50, "cost_usd": 0.01, "turns": 3}`
  - `input` = `usage.input_tokens + usage.cache_creation_input_tokens`; `cache_read` = `usage.cache_read_input_tokens`; `output` = `usage.output_tokens`; `cost_usd` = `total_cost_usd`; `turns` = `num_turns`. Missing numbers count as `0`; a missing cost is `null`.
- **Usage summary** (in the `run-pipeline` result, key `usage`): `{"total": {"input", "cache_read", "output", "cost_usd", "turns"}, "by_agent": [<records of this run>]}`; `total.cost_usd` is `null` if every record's cost is `null`.

## 6. Contracts between modules
**`brief.py`** (WP-01)
```python
def slice_architecture(text: str, ident: str, manifest: dict) -> tuple[str, bool]:
    """Returns (brief markdown, sliced). ident is a WP id of the manifest or "AT".
    sliced=False means the full text is returned unchanged (fallback)."""
```
Rules, applied to `text` with the ```` ```json wp-manifest ```` block removed first:
- A **section** starts at a line `## <n>. ` (n is digits, optionally followed by `b`, e.g. `4b`) and runs to the next such line. The **preamble** is everything before the first section. A line whose `lstrip()` starts with ```` ``` ```` toggles "inside a fence"; lines inside a fence never start or end a section, subsection or block.
- Inside section 8: a **subsection** starts at `### ` and runs to the next `### `. An **AC block** starts at a line matching `^- (AC-\d+)\b` inside the subsection whose heading contains `Acceptance criteria`, and runs to the next AC block or the end of the subsection. A **WP block** starts at `^#### (WP-\d+)\b` inside the subsection whose heading contains `Work packages`, and runs to the next `#### ` or the end of the subsection. Text before the first block of a subsection is its intro.
- Brief for a WP: first line `> Brief for <ID>: the parts of architecture.md this agent needs. Read architecture.md only if this brief points to something it does not contain.`, a blank line, then — in document order — the preamble and sections 1, 2, 3, 4b, 5, 6, 7, 9, 11 whole; from section 8 its heading, its intro, each subsection heading and intro, the AC blocks whose id is in the WP's `acs`, and the WP's own block; finally a ```` ```json ```` fence with the WP's manifest entry (`json.dumps(entry, indent=2)`).
- Brief for `AT`: same first line; the preamble and sections 1, 2, 3, 4b, 5, 6, 7, 9 whole; from section 8 its heading, its intro and the whole `Acceptance criteria` subsection; finally a ```` ```json ```` fence with `{"acceptance": <manifest acceptance object>}`.
- Fallback (return `(text, False)`, the original text including the manifest): `ident` is a WP whose `#### <ID>` block is missing, whose `acs` is missing or empty, or with an id in `acs` that has no AC block; or `ident` is `AT` and there is no `Acceptance criteria` subsection; or `ident` is unknown.
- Absent sections are skipped silently (e.g. no 4b in an empty repo).

**`agentic.py`** (WP-02)
- `python3 .claude/bin/agentic.py briefs` → validates the manifest (exit 1 if invalid), writes `.claude/state/briefs/<ID>.md` for every WP and for `AT` when the manifest has `acceptance`, prints one JSON line `{"full_words": int, "briefs": {"<ID>": {"path": ".claude/state/briefs/<ID>.md", "sliced": bool, "words": int}}}`, exit 0. Words = `len(text.split())`. Allowed in `seq`.
- `write_briefs(manifest) -> dict` returns the `briefs` mapping above; used by `briefs`, `run-wave` and `run-acceptance`.
- `run_agent(manifest, cfg, spec, agent_type, ident, label, prompt, out_path, final=False) -> dict` → `{"report": dict | None, "status": str, "usage": dict}`. `status` ∈ `DONE BLOCKER PASS FAIL` (the report's `status`), `TIMEOUT (<n>s)`, `STOPPED`, `NO REPORT`. It takes the output schema from `spec["schemas"].get(agent_type, spec["schemas"]["implementer"])` (a WP may declare a custom agent in `agent`), appends the usage record (§5) to `usage.jsonl` (`id` is `ident`, or the agent type when `ident` is `None`), sets the ledger to `running` with the pid for tracked agents as the current closure does, and when `final=True` also sets the final ledger state (`status_to_state(status)`, note = status). `run-pipeline` calls it with `final=False` (its `close-stage` writes the states) and `out_path = runs/<ID>-<n>.json`; the one-off commands call it with `final=True` and `out_path = runs/<ID>.json` (so `REVIEW.json` and `QA.json` keep existing, and `run-wave --fix` reads them). It never raises.
- `usage_of(path) -> dict` → the record's numeric fields (§5) from a `claude -p` JSON output; zeros/`null` if missing or unreadable.
- One-off commands keep their CLI. They call `snapshot(label)` before launching, run their agents in parallel with `pipeline.threaded`, and end with the existing `finish` (ownership check, `RESULT:` line, exit codes). They read `pipeline-spec.json` from `ROOT` (`PIPELINE_SPEC`) and exit 1 with the existing "reinstall the flow" message if it is missing. Prompts come from `pipeline-spec.json` rendered with `render` from `pipeline.py`: `run-wave` uses `implement` (or `fix`), `run-acceptance` `test_author`, `run-review` `reviewer`, `run-qa` `qa`, `run-docs` `doc_updater`; `{rules}` = `rules_hint(manifest)`; `{brief}` = `.claude/state/briefs/<ID>.md`; `{retry}` = `""`.
- `run-wave N [WP...] --fix`: the defects of each WP are the blocking (`critical`, `high`) reviewer findings in `runs/REVIEW.json` and the blocking `implementation` defects in `runs/QA.json` (read with `structured_report`) whose `wp` is that WP, each tagged `"source": "reviewer"` or `"source": "qa"` as `pipeline.py:344-345` does, serialised with `pipeline.compact`, in `{defects}`. If a selected WP has none: exit 1, stderr `<ID>: no blocking defect from review or QA is attributed to it.` The per-WP cap and exit 4 stay.
- `ledger_render` (so `ledger.md` and `status`) adds, when `usage.jsonl` exists, a section `## Usage` with one line per record (`<label>: in <input> · cache <cache_read> · out <output> · $<cost>`) and a final `Total: in <input> · cache <cache_read> · out <output> · $<cost>` line; a null cost shows as `$?`.
- `run-pipeline`'s result gains `usage` (§5) with the records appended during that run, both in `runs/workflow-result.json` and in the JSON line it prints. `status` shows a null cost as `$?`.
- `INSTALL_GLOBS` gains `.claude/bin/brief.py`. The hook's block messages say `return BLOCKER` instead of `return STATUS: BLOCKER`. `briefs` is dispatched as `cmd == "briefs"` in `main()`.

**Prompts** (WP-02 edits `pipeline-spec.json`; WP-03 edits the JS copies and `pipeline.py`'s rendering) — new placeholder `{brief}`:
- `implement`: `Implement {wp}. Your brief is {brief}: the parts of architecture.md your package needs.{rules}`
- `fix`: `Fix {wp}. Your brief is {brief}. The code already exists. These are the defects the judges (reviewer, QA and verification) attribute to your package, as JSON:\n{defects}\nStay within the contract, do not touch other packages' files or the acceptance tests, and keep the acceptance tests of your criteria green.{retry}{rules}`
- `test_author`: `Write the acceptance tests. Your brief is {brief}: the parts of architecture.md they need.{rules}`
- `test_author_revision`: `Revise the acceptance tests (your brief is {brief}). An independent auditor found these weak points, as JSON:\n{weak}\nStrengthen them: the tests of each criterion must fail against an empty or trivial implementation (a stub that returns nothing, None or a constant) and must cover the criterion's "must not". Keep them red for the right reason.{rules}`
- `test_auditor`: `Audit the acceptance tests against the brief they were written from, {brief}. The test-author reports this coverage, as JSON:\n{coverage}{rules}`
- Unchanged: `fix_retry`, `reviewer`, `verifier`, `qa`, `doc_updater`.
- `{brief}` is `.claude/state/briefs/<WP-ID>.md` for implementers and `.claude/state/briefs/AT.md` for the test-author and the test-auditor.
- Prepare step in both engines: `seq pipeline-plan + briefs + baseline` (`pipeline-plan + briefs` when resuming from a wave). Step indices: plan 0, briefs 1, baseline 2. A failed `briefs` step stops at `prepare` with detail `briefs failed`.

## 7. Conventions
- Code and messages in English, as the rest of the flow. Comments only where the reason is not obvious, matching the density of `agentic.py`.
- Stdlib only. No new file outside §4.
- Style: `agentic.py`'s own style wins over `rules-library/python.md` where they differ (no type annotations, and `except Exception` where a function promises not to raise, as `pipeline.py:88-94` does). Do not spend the line budget on annotations.
- Elegance budget: a change must not grow `agentic.py`. WP-02 must delete at least as many lines as it adds (net ≤ 0 in `git diff --stat .claude/bin/agentic.py`), `brief.py` stays under 120 lines.
- Exit codes and existing command names do not change, except the deleted commands.
- Commit format: imperative subject ≤ 72 chars, body with the why; trailers as the repo uses.

## 8. Work packages and acceptance criteria
### Acceptance criteria
- AC-001 · A WP brief holds only its slice
  - Scenario: an `architecture.md` with sections 1–11 (including 4, 4b, 10), AC-001 and AC-002 blocks, `#### WP-01 · a` and `#### WP-02 · b` blocks, and a manifest with an `acceptance` object (`files: ["acceptance/"]`, a `command`) and WP-01 and WP-02 both in wave 1 where WP-01 has `acs: ["AC-001"]` and WP-02 `acs: ["AC-002"]`.
  - Action: `slice_architecture(text, "WP-01", manifest)`.
  - Expected: `sliced` is `True`; the brief starts with `> Brief for WP-01:`; it contains the text of sections 1, 2, 3, 4b, 5, 6, 7, 9, 11, the AC-001 block, the WP-01 block, and a JSON fence equal to WP-01's manifest entry.
  - Must not: contain the AC-002 block, the WP-02 block, any text of sections 4 or 10, or the `json wp-manifest` block.
  - Check: `test_acceptance.py::test_ac_001_wp_brief`.
  - WP: WP-01
- AC-002 · The test-author's brief
  - Scenario: the document of AC-001.
  - Action: `slice_architecture(text, "AT", manifest)`.
  - Expected: `sliced` is `True`; it contains sections 1, 2, 3, 4b, 5, 6, 7, 9, both AC blocks, and a JSON fence `{"acceptance": ...}` equal to the manifest's acceptance object.
  - Must not: contain any WP block, or text of sections 4, 10 or 11.
  - Check: `test_acceptance.py::test_ac_002_at_brief`.
  - WP: WP-01
- AC-003 · Fallback to the whole document
  - Scenario: three variants of the AC-001 document: no `#### WP-01` heading; WP-01 with `acs: []`; WP-01 with `acs: ["AC-009"]` (no such block).
  - Action: `slice_architecture(text, "WP-01", manifest)` on each, and `slice_architecture(text, "WP-77", manifest)`.
  - Expected: `(text, False)` with `text` identical to the input in every case.
  - Must not: return a partial brief or raise.
  - Check: `test_acceptance.py::test_ac_003_fallback`.
  - WP: WP-01
- AC-004 · Headings inside code fences are not boundaries
  - Scenario: the AC-001 document whose section 6 contains a fenced block with the lines `## 10. Decisions` and `#### WP-02 · fake`.
  - Action: `slice_architecture(text, "WP-01", manifest)`.
  - Expected: the brief contains the whole fenced block of section 6.
  - Must not: cut section 6 at the fenced heading, nor include section 10's real text.
  - Check: `test_acceptance.py::test_ac_004_fences`.
  - WP: WP-01
- AC-005 · `briefs` writes every brief and reports their size
  - Scenario: a temporary git repo with the AC-001 document as `architecture.md` and the flow's agents copied (as `test_agentic.Repo` does).
  - Action: `python3 .claude/bin/agentic.py briefs`, and `agentic.py seq pipeline-plan + briefs`.
  - Expected: exit 0; files `.claude/state/briefs/WP-01.md`, `WP-02.md`, `AT.md` exist and equal `slice_architecture` of each id; the JSON line has `briefs.WP-01.sliced == true` and `briefs.WP-01.words < full_words`; the `seq` run has 2 steps, both exit 0.
  - Must not: write any file outside `.claude/state/briefs/`; exit 0 with an invalid manifest.
  - Check: `test_acceptance.py::test_ac_005_briefs_command`.
  - WP: WP-02
- AC-006 · Agents are pointed to their briefs
  - Scenario: the conformance world of `test_workflow.mjs`.
  - Action: `node .claude/bin/test_workflow.mjs --dump`.
  - Expected: in `happy path`, the prompt `implementer:WP-01#1` contains `.claude/state/briefs/WP-01.md`, `test-author:test-author#1` and `test-auditor:test-auditor#1` contain `.claude/state/briefs/AT.md`, and the first three gates are `pipeline-plan`, `briefs`, `baseline`; in `start_wave skips…` the first two gates are `pipeline-plan`, `briefs`.
  - Must not: point reviewer, qa, verifier or doc-updater prompts to a brief.
  - Check: `test_acceptance.py::test_ac_006_prompts_use_briefs`.
  - WP: WP-03
- AC-007 · One report format for every launcher
  - Scenario: the repo of AC-005 with `.claude/workflows/` copied into it (as `test_pipeline.py:237` does), a fake `claude` on `PATH` that prints `{"structured_output": {...}, "usage": {...}, "total_cost_usd": 0.01, "num_turns": 3}` valid for each agent's schema; a second fake mode prints only `{"result": "STATUS: DONE"}`.
  - Action: `agentic.py run-wave 1` with each fake.
  - Expected: with the first, exit 0 and every argv contains `--json-schema` equal to `pipeline-spec.json`'s implementer schema; with the second, exit 3 and the ledger state of WP-01 is `error` with note `NO REPORT`.
  - Must not: accept the text `STATUS: DONE` as a report.
  - Check: `test_acceptance.py::test_ac_007_single_report_format`.
  - WP: WP-02
- AC-008 · One-off fixes receive their attributed defects
  - Scenario: the repo of AC-007; `runs/REVIEW.json` holds `{"structured_output": {"status": "FAIL", "findings": [{"severity": "high", "wp": "WP-02", "file": "src/b/x.py", "problem": "p", "evidence": "e"}]}}`.
  - Action: `agentic.py run-wave 1 WP-02 --fix`, then `agentic.py run-wave 1 WP-01 --fix`.
  - Expected: the first exits 0 and WP-02's prompt (argv after `-p`) starts with `Fix WP-02.` and contains `"problem":"p"`; the second exits 1 with `WP-01: no blocking defect from review or QA is attributed to it.` on stderr.
  - Must not: launch an agent for WP-01.
  - Check: `test_acceptance.py::test_ac_008_fix_defects`.
  - WP: WP-02
- AC-009 · Dead commands are gone
  - Scenario: the repo of AC-005.
  - Action: run `agentic.py` with the arguments `wave-plan 1`, then `open-wave 1`, then `close-wave 1 WP-01=done`.
  - Expected: each exits 1 and prints the usage text (the module docstring) on stderr.
  - Must not: create `.claude/state/snapshot-1.json`.
  - Check: `test_acceptance.py::test_ac_009_dead_commands`.
  - WP: WP-02
- AC-010 · Token telemetry
  - Scenario: the first fake of AC-007 with `usage = {"input_tokens": 100, "cache_creation_input_tokens": 20, "cache_read_input_tokens": 300, "output_tokens": 50}`; a third fake mode prints a report without `usage` or cost.
  - Action: `agentic.py run-wave 1`, then `agentic.py status`; then `agentic.py run-pipeline` with the first fake.
  - Expected: `usage.jsonl` has one record per launched agent, WP-01's being `{"id": "WP-01", "agent": "implementer", "label": "WP-01", "input": 120, "cache_read": 300, "output": 50, "cost_usd": 0.01, "turns": 3}`; `status` prints `## Usage` and a `Total:` line with `in 240` after the two wave-1 agents; the `run-pipeline` result's `usage.total.output` equals 50 × the number of agents it launched; with the third fake the record has zeros and `cost_usd: null` and the run does not fail because of it.
  - Must not: count a run twice or crash on missing fields.
  - Check: `test_acceptance.py::test_ac_010_usage`.
  - WP: WP-02
- AC-011 · Agent definitions speak JSON only
  - Scenario: `.claude/agents/`.
  - Action: read `implementer.md`, `test-author.md`, `test-auditor.md`, `qa.md`, `reviewer.md`, `doc-updater.md`.
  - Expected: none contains `STATUS:`; each has a `# Report` section whose body contains `schema` and `words`; `test-auditor.md` reads the brief named in its prompt (the AT brief); `implementer.md` and `test-author.md` tell the agent to read the brief named in its prompt.
  - Must not: remove the injection-defence or `claim` sections (the existing `test_definitions.py` checks stay green).
  - Check: `test_acceptance.py::test_ac_011_agents_json_only`.
  - WP: WP-04
- AC-012 · The architect writes sliceable plans
  - Scenario: `.claude/commands/architect.md` and `.claude/agents/plan-reviewer.md`.
  - Action: read them.
  - Expected: the template of section 8 in `architect.md` shows a `#### WP-NN · <goal>` heading per WP and says the brief depends on it; `plan-reviewer.md` checks that every WP has that heading and every AC an `- AC-NNN ·` line.
  - Must not: drop the `acs` requirement.
  - Check: `test_acceptance.py::test_ac_012_architect_template`.
  - WP: WP-04
- AC-013 · Docs describe briefs and telemetry
  - Scenario: the repo docs.
  - Action: read `docs/how-it-works.md` and `README.md`.
  - Expected: the glossary of `how-it-works.md` has a `**Brief:**` entry and its files tree lists `briefs/` and `usage.jsonl`; no doc cites `wave-plan`, `open-wave` or `close-wave`; `README.md`'s paragraph that starts `Not confirmed with real agents` lists the briefs and the telemetry.
  - Must not: break the existing documentation tests (`test_definitions.DocumentationDriftTests`).
  - Check: `test_acceptance.py::test_ac_013_docs`.
  - WP: WP-05

### Work packages
#### WP-01 · The slicer
- Wave 1. Files: `.claude/bin/brief.py`, `.claude/bin/test_brief.py`.
- Implements `slice_architecture` (§6). Consumes nothing.
- ACs: AC-001, AC-002, AC-003, AC-004.
- Patterns to mirror: `pipeline.py:1-45` (docstring, pure helpers), `test_pipeline.py:1-30` (test module header, imports through `sys.path`).
- Gotchas: track ```` ``` ```` fences before matching headings; section `4b` must not be matched as `4`; keep the document order; compare AC ids exactly (`AC-001` ≠ `AC-01`); the manifest block sits at the end of section 8, inside the last WP block, so remove it before slicing.
- Imports: `json`, `re` only.
- Validation: `python3 -m unittest discover -s .claude/bin -p "test_brief.py"`.

#### WP-02 · `agentic.py`: briefs, one runner, telemetry, dead code, spec prompts
- Wave 2, `depends_on: ["WP-01"]` (it imports `brief.py`). Files: `.claude/bin/agentic.py`, `.claude/bin/test_agentic.py`, `.claude/workflows/pipeline-spec.json`.
- Implements `briefs`, `write_briefs`, `run_agent`, `usage_of`, the rewired `run-*` commands, the `status` Usage section, `usage` in the `run-pipeline` result; deletes `launch`, `parse_status`, `STATUS_RE`, `wave-plan`, `open-wave`, `close-wave` and their docstring lines. Also edits the prompts of `pipeline-spec.json` (§6), reworded hook messages and `INSTALL_GLOBS`. Consumes `slice_architecture` from WP-01 by implementation.
- ACs: AC-005, AC-007, AC-008, AC-009, AC-010.
- Patterns to mirror: `agentic.py:1136-1162` (the closure that becomes `run_agent`), `agentic.py:1103-1106` (sibling import), `agentic.py:1386-1395` (`finish`), `test_pipeline.py:188-260` (fake `claude` and end-to-end setup).
- Gotchas: `run_agent` runs in threads for one-off waves — append to `usage.jsonl` under `locked_json`'s lock or with one `write` of a line < 4 KB; `run-pipeline`'s `agent` closure must still raise `pipeline.Halted` on `STOPPED`; tests that used the text fake (`test_agentic.py:32-43`, `LauncherTests`) move to the structured fake; `test_agentic.py:313-335` tests `open-wave`/`close-wave` and must go; `run-wave --fix` previously only required a report to exist (`agentic.py:640-643`); `test_agentic.Repo` must also copy `.claude/workflows/` into its temporary repo (`brief.py` and `pipeline.py` are imported from the script's own folder, so they need no copy); the conformance tests stay red between WP-02 and WP-03 (the spec's prompts change first), so WP-02 validates with `test_agentic.py` only.
- Imports: `pipeline.render`, `pipeline.compact`, `pipeline.threaded` via `pipeline_module()`; `brief.slice_architecture` via a `brief_module()` mirroring `pipeline_module()`.
- Validation: `python3 -m unittest discover -s .claude/bin -p "test_agentic.py"`; net line delta of `agentic.py` ≤ 0 (`git diff --stat` against the commit the run starts from).

#### WP-03 · Both engines point to the briefs
- Wave 3, `depends_on: ["WP-02"]` (its end-to-end tests call the real `briefs`, which `seq` only accepts after WP-02, and it reads the new prompts from the spec). Files: `.claude/workflows/agentic-implement.js`, `.claude/bin/pipeline.py`, `.claude/bin/test_workflow.mjs`, `.claude/bin/test_pipeline.py`, `.claude/bin/conformance.json`.
- Implements the prompts and the prepare step of §6 in both engines. Consumes `briefs` and the spec's prompts from WP-02 by implementation.
- ACs: AC-006.
- Patterns to mirror: `agentic-implement.js:289-300` and `pipeline.py:148-160` (prepare), `pipeline.py:226-251` (`launch_wave` rendering), `test_pipeline.py:29-108` (simulated world: add a default response for `briefs`).
- Gotchas: the step index of `baseline` moves from 1 to 2 in both engines; the simulated worlds in `test_workflow.mjs` and `test_pipeline.py` must answer `briefs` identically; prompts must stay byte-identical across engines (the conformance test proves it); the JS prompt strings are copies of the spec.
- Validation: `node .claude/bin/test_workflow.mjs` and `python3 -m unittest discover -s .claude/bin -p "test_pipeline.py"`.

#### WP-04 · Agent definitions and commands
- Wave 3, `depends_on: ["WP-02"]` (`implement.md` cites `agentic.py briefs`, which the documentation drift test checks). Files: `.claude/agents/implementer.md`, `test-author.md`, `test-auditor.md`, `qa.md`, `reviewer.md`, `doc-updater.md`, `plan-reviewer.md`, `.claude/commands/architect.md`, `.claude/commands/implement.md`, `.claude/bin/test_definitions.py`.
- Replaces each `# Report` section by a JSON-only one (fields of its schema in `pipeline-spec.json`, a word cap); implementer and test-author read the brief named in the prompt instead of the whole document (they may open `architecture.md` for something the brief points to and lacks); `architect.md` section 8 template uses `#### WP-NN · <goal>`; `plan-reviewer.md` checks the headings; `test-auditor.md` also reads the AT brief instead of the whole document; `implement.md` mentions `briefs` and `usage.jsonl`.
- ACs: AC-011, AC-012.
- Patterns to mirror: `test-auditor.md:1-32` (shape of an agent definition; its own Report section is text and is replaced by a JSON-only one too), `test_definitions.py:22-33, 105-113` (contracts table: replace the `status` key by a `schema` check).
- Gotchas: `plan-reviewer.md` keeps its text verdict (it is not launched with a schema); do not lengthen any definition — net words per file ≤ 0.
- Validation: `python3 -m unittest discover -s .claude/bin -p "test_definitions.py"`.

#### WP-05 · Documentation
- Wave 3, `depends_on: ["WP-02"]` (same drift test). Files: `README.md`, `docs/how-it-works.md`, `docs/how-to/README.md`, `docs/how-to/terminal.md`.
- Documents briefs (sequence diagram: `pipeline-plan + briefs + baseline`; glossary; files tree), the single runner, telemetry (`status`, `usage.jsonl`, `usage` in the result) and removes nothing else.
- ACs: AC-013.
- Patterns to mirror: `docs/how-it-works.md:315-330` (glossary entries), `docs/how-it-works.md:280-310` (files tree).
- Validation: `python3 -m unittest discover -s .claude/bin -p "test_definitions.py"`.

```json wp-manifest
{
  "stack": ["python", "typescript"],
  "allow_bash": ["Bash(python3 -m unittest:*)", "Bash(python3 -m compileall:*)", "Bash(node .claude/bin/test_workflow.mjs:*)"],
  "acceptance": {"files": [".claude/bin/test_acceptance.py"], "command": "python3 -m unittest discover -s .claude/bin -p test_acceptance.py"},
  "verify": {"build": "python3 -m compileall -q .claude/bin", "tests": "python3 -m unittest discover -s .claude/bin -p test_*.py"},
  "work_packages": [
    {"id": "WP-01", "wave": 1, "depends_on": [], "files": [".claude/bin/brief.py", ".claude/bin/test_brief.py"],
     "acs": ["AC-001", "AC-002", "AC-003", "AC-004"], "mirror": [".claude/bin/pipeline.py:1-45", ".claude/bin/test_pipeline.py:1-30"]},
    {"id": "WP-02", "wave": 2, "depends_on": ["WP-01"], "files": [".claude/bin/agentic.py", ".claude/bin/test_agentic.py", ".claude/workflows/pipeline-spec.json"],
     "acs": ["AC-005", "AC-007", "AC-008", "AC-009", "AC-010"],
     "mirror": [".claude/bin/agentic.py:1136-1162", ".claude/bin/agentic.py:1103-1106", ".claude/bin/agentic.py:1386-1395", ".claude/bin/test_pipeline.py:188-260"]},
    {"id": "WP-03", "wave": 3, "depends_on": ["WP-02"], "files": [".claude/workflows/agentic-implement.js", ".claude/bin/pipeline.py", ".claude/bin/test_workflow.mjs", ".claude/bin/test_pipeline.py", ".claude/bin/conformance.json"],
     "acs": ["AC-006"], "mirror": [".claude/workflows/agentic-implement.js:289-300", ".claude/bin/pipeline.py:148-160", ".claude/bin/pipeline.py:226-251", ".claude/bin/test_pipeline.py:29-108"]},
    {"id": "WP-04", "wave": 3, "depends_on": ["WP-02"], "files": [".claude/agents/implementer.md", ".claude/agents/test-author.md", ".claude/agents/test-auditor.md", ".claude/agents/qa.md", ".claude/agents/reviewer.md", ".claude/agents/doc-updater.md", ".claude/agents/plan-reviewer.md", ".claude/commands/architect.md", ".claude/commands/implement.md", ".claude/bin/test_definitions.py"],
     "acs": ["AC-011", "AC-012"], "mirror": [".claude/agents/test-auditor.md:1-32", ".claude/bin/test_definitions.py:22-33"]},
    {"id": "WP-05", "wave": 3, "depends_on": ["WP-02"], "files": ["README.md", "docs/how-it-works.md", "docs/how-to/README.md", "docs/how-to/terminal.md"],
     "acs": ["AC-013"], "mirror": ["docs/how-it-works.md:315-330", "docs/how-it-works.md:280-310"]}
  ]
}
```

## 9. Test strategy
- Unit: each WP in its own test file (§4). `test_brief.py` covers the slicer's edge cases beyond the ACs (absent sections, `4b` vs `4`, document order).
- Acceptance: `.claude/bin/test_acceptance.py`, written first from AC-001…AC-013, red before the code (imports of `brief` fail; prompts, commands and docs do not match yet). It reuses `test_agentic.Repo` and the fake-`claude` pattern of `test_pipeline.py`.
- Regression: the whole existing suite (`verify.tests`), including the engine conformance tests, stays green.
- Expected baseline failure: `architecture.md` is committed and cites `agentic.py briefs`, so `DocumentationDriftTests.test_cited_commands_exist` fails until WP-02 lands (wave 3 starts after it).
- No QA zone: the change has no runtime surface beyond the commands the acceptance tests drive.

## 10. Decisions
- **D1 · Implemented outside `/implement`.** Every file of this change is under `.claude/`, which the flow protects from its own agents (`validate` rejects owning it, the hook blocks writes). That guardrail stays: the flow does not rewrite its own guardrails. `validate` therefore rejects this manifest on purpose; the plan is executed from the main session, WP by WP, with the same acceptance tests first and the same gates. Discarded: an opt-in to let agents write `.claude/` (it would weaken the guardrail for every user).
- **D2 · Deterministic slicing, not a model.** A brief written by a model costs tokens and can drop a contract; slicing by headings costs nothing and is testable. Discarded: an LLM summariser.
- **D3 · Sections kept whole in a WP brief:** 1, 2, 3, 4b, 5, 6, 7, 9, 11. Contracts (6) and existing-code context (4b) are not sliced per WP: consumers need contracts they do not implement, and 4b's rows are not attributed. Dropped: 4 (the tree; the WP's files are in its block and manifest entry) and 10 (rationale; rules belong in 7). Risks (11) stay because a risk may block the WP.
- **D4 · Fallback to the whole document** when the headings are missing: old plans keep working; no new `validate` error (the plan-reviewer checks the headings instead).
- **D5 · One runner, schema only.** `run-*` reuse `run-pipeline`'s launcher and prompts; the text `STATUS:` contract disappears from code and agent definitions. Discarded: keeping `run-*` on text for backwards compatibility (two contracts to maintain, longer prompts).
- **D6 · Telemetry in a JSONL file**, not in `ledger.json`: append-only, one line per run, works for read-only agents that have no ledger entry. Fallback-only (see §11).
- **D7 · Rules duplication measured, not removed** (user's choice): the telemetry gives the per-agent input tokens to compare a run with and without the rules line.
- **D8 · `typescript` in `stack`** because WP-03 edits the workflow script.
- **D9 · The spec stays with WP-02.** `run-*` render its prompts, so WP-02 must change them; WP-03 then follows with the JS copies, and the conformance tests are red in between (stated in WP-02).
- **D10 · Waves 1 → 2 → 3.** WP-02 needs `brief.py`; WP-03 to WP-05 need `briefs` in `agentic.py`. Parallel only where the validation commands do not need the other package's code (review round 1).

## 11. Risks and open questions
- **Native-workflow telemetry:** the workflow's `agent()` returns only the schema result; if it exposes usage in a future runtime, add it then. Until then, measurements come from `run-pipeline`.
- **`claude -p` usage fields:** names follow the CLI's JSON output (`usage.*_tokens`, `total_cost_usd`, `num_turns`); `usage_of` must tolerate their absence (AC-010).
- **Screenshots:** `docs/assets/screens/hook.png` shows the old `STATUS: BLOCKER` hook text; regenerating images is out of scope.
- **Plans without WP headings** get no savings until `/architect` writes the new template (D4).
- **Rollback:** each WP is a separate commit; reverting WP-03 restores the old prompts while keeping the `briefs` command harmless.
