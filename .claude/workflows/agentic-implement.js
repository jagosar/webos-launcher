export const meta = {
  name: 'agentic-implement',
  description: 'Takes architecture.md to verified code: wave 0 (acceptance first), parallel waves, verification, adversarial review, QA, capped fix cycles and living documentation.',
  phases: [
    { title: 'Prepare', detail: 'pipeline-plan: waves, packages, acceptance and the fix limit' },
    { title: 'Baseline', detail: 'state of build, types, lint and tests before any change' },
    { title: 'Wave 0', detail: 'the test-author writes the acceptance tests; they must be red; the test-auditor checks they are not weak' },
    { title: 'Waves', detail: 'implementers in parallel per wave, with ownership checked' },
    { title: 'Verification', detail: 'build, types, lint, tests and green acceptance' },
    { title: 'Review', detail: 'reviewer and adversarial verification of serious findings' },
    { title: 'QA', detail: 'exploratory tests beyond the acceptance criteria' },
    { title: 'Fixes', detail: 'each defect goes back to its package, with a cycle limit' },
    { title: 'Documentation', detail: 'code map, doc-updater and a check of links and paths' },
  ],
}

// Optional input: args = { start_wave: N } to resume from a wave (skips wave 0).
// The script only orchestrates: it has no shell and no disk. The deterministic steps are run by the gatekeeper agent
// with agentic.py, several at a time (`agentic.py seq a + b + c`) to save round trips, and the guardrails (the
// PreToolUse hook) apply to every agent just as outside a workflow.
// The schemas, prompts and routing rules below mirror .claude/workflows/pipeline-spec.json, which the fallback engine
// (`agentic.py run-pipeline`) also reads; test_workflow.mjs fails if the two drift apart.
const opts = args || {}
const startWave = /^\d+$/.test(String(opts.start_wave ?? '')) ? Number(opts.start_wave) : null  // 2 or "2"
const AGENTIC = 'python3 .claude/bin/agentic.py'

// ---------- schemas ----------
const GATE_SCHEMA = {
  type: 'object',
  required: ['exit_code', 'output'],
  properties: { exit_code: { type: 'integer' }, output: { type: 'string' } },
}
const SEVERITY = { type: 'string', enum: ['critical', 'high', 'medium', 'low'] }
// What happens to a finding: floor never ships, reflex blocks unless the plan waives it, finish never blocks.
const JUDGE_TIER = { type: 'string', enum: ['floor', 'finish'] }
const PROVENANCE = { type: 'string', enum: ['read', 'inferred'] }
// What the agent did not check, skipped, or sees as a risk; [] when nothing.
const NOT_CHECKED = { type: 'array', items: { type: 'string' } }
// A claim the code alone cannot settle: no severity, never blocks, the person checks the fact.
const NEEDS_VALIDATION = {
  type: 'array',
  items: {
    type: 'object',
    required: ['wp', 'file', 'kind', 'claim', 'fact', 'how_to_check'],
    properties: { wp: { type: 'string' }, file: { type: 'string' }, line: { type: 'integer' }, kind: { type: 'string', enum: ['external', 'unchanged_code'] }, claim: { type: 'string' }, fact: { type: 'string' }, how_to_check: { type: 'string' } },
  },
}
const AT_SCHEMA = {
  type: 'object',
  required: ['status', 'coverage', 'blocker', 'not_checked'],
  properties: {
    status: { type: 'string', enum: ['DONE', 'BLOCKER'] },
    coverage: { type: 'array', items: { type: 'object', required: ['ac', 'tests'], properties: { ac: { type: 'string' }, tests: { type: 'array', items: { type: 'string' } } } } },
    blocker: { type: ['string', 'null'] },
    not_checked: NOT_CHECKED,
  },
}
const REPORT_SCHEMA = {
  type: 'object',
  required: ['status', 'wp', 'files', 'checks', 'internal_decisions', 'blocker', 'not_checked', 'concerns', 'missing_context'],
  properties: {
    status: { type: 'string', enum: ['DONE', 'DONE_WITH_CONCERNS', 'NEEDS_CONTEXT', 'BLOCKED'] },
    wp: { type: 'string', pattern: '^WP-[0-9]+$' },
    files: { type: 'array', items: { type: 'string' } },
    checks: { type: 'array', items: { type: 'object', required: ['command', 'passed'], properties: { command: { type: 'string' }, passed: { type: 'boolean' }, detail: { type: 'string' } } } },
    internal_decisions: { type: 'array', items: { type: 'string' } },
    blocker: { type: ['object', 'null'], required: ['missing', 'section'], properties: { missing: { type: 'string' }, section: { type: 'string' }, options: { type: 'array', items: { type: 'string' } } } },
    not_checked: NOT_CHECKED,
    concerns: { type: 'array', items: { type: 'string' } },
    missing_context: { type: ['object', 'null'], required: ['what', 'section'], properties: { what: { type: 'string' }, section: { type: 'string' } } },
  },
}
const AUDIT_SCHEMA = {
  type: 'object',
  required: ['status', 'findings', 'not_checked'],
  properties: {
    status: { type: 'string', enum: ['PASS', 'FAIL'] },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        required: ['tier', 'severity', 'wp', 'file', 'problem', 'evidence'],
        properties: { tier: JUDGE_TIER, severity: SEVERITY, wp: { type: 'string' }, file: { type: 'string' }, line: { type: 'integer' }, problem: { type: 'string' }, evidence: { type: 'string' } },
      },
    },
    not_checked: NOT_CHECKED,
  },
}
// A finding has four parts: problem, evidence (the concrete case), fix and if_skipped.
const REVIEW_SCHEMA = {
  type: 'object',
  required: ['status', 'findings', 'needs_validation', 'not_checked'],
  properties: {
    status: { type: 'string', enum: ['PASS', 'FAIL'] },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        required: ['tier', 'severity', 'provenance', 'wp', 'file', 'problem', 'evidence', 'fix', 'if_skipped'],
        properties: { tier: JUDGE_TIER, severity: SEVERITY, wp: { type: 'string' }, file: { type: 'string' }, line: { type: 'integer' }, problem: { type: 'string' }, evidence: { type: 'string' }, fix: { type: 'string' }, if_skipped: { type: 'string' }, provenance: PROVENANCE },
      },
    },
    needs_validation: NEEDS_VALIDATION,
    not_checked: NOT_CHECKED,
  },
}
// The Standards axis: breaches of a written rule (reflex) and notes (finish); "axis" tells its report from the others.
const STANDARDS_SCHEMA = {
  type: 'object',
  required: ['status', 'axis', 'findings', 'not_checked'],
  properties: {
    status: { type: 'string', enum: ['PASS', 'FAIL'] },
    axis: { type: 'string', enum: ['standards'] },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        required: ['tier', 'rule', 'provenance', 'wp', 'file', 'problem', 'evidence', 'fix', 'if_skipped'],
        properties: { tier: { type: 'string', enum: ['reflex', 'finish'] }, rule: { type: 'string' }, provenance: PROVENANCE, wp: { type: 'string' }, file: { type: 'string' }, line: { type: 'integer' }, problem: { type: 'string' }, evidence: { type: 'string' }, fix: { type: 'string' }, if_skipped: { type: 'string' } },
      },
    },
    not_checked: NOT_CHECKED,
  },
}
const VERDICT_SCHEMA = {
  type: 'object',
  required: ['is_real', 'confidence', 'reasoning'],
  properties: { is_real: { type: 'boolean' }, confidence: { type: 'number', minimum: 0, maximum: 1 }, reasoning: { type: 'string' } },
}
const QA_SCHEMA = {
  type: 'object',
  required: ['status', 'plan', 'defects', 'gaps', 'needs_validation'],
  properties: {
    status: { type: 'string', enum: ['PASS', 'FAIL', 'BLOCKER'] },
    plan: { type: 'array', items: { type: 'string' } },
    defects: {
      type: 'array',
      items: {
        type: 'object',
        required: ['tier', 'id', 'severity', 'wp', 'kind', 'section', 'repro', 'fix', 'if_skipped'],
        properties: { tier: JUDGE_TIER, id: { type: 'string' }, severity: SEVERITY, wp: { type: 'string' }, kind: { type: 'string', enum: ['implementation', 'design'] }, section: { type: 'string' }, repro: { type: 'string' }, fix: { type: 'string' }, if_skipped: { type: 'string' } },
      },
    },
    gaps: { type: 'array', items: { type: 'string' } },
    needs_validation: NEEDS_VALIDATION,
  },
}
const DOCS_SCHEMA = {
  type: 'object',
  required: ['status', 'files', 'changes', 'unverified', 'blocker'],
  properties: {
    status: { type: 'string', enum: ['DONE', 'BLOCKER'] },
    files: { type: 'array', items: { type: 'string' } },
    changes: { type: 'array', items: { type: 'string' } },
    unverified: { type: 'array', items: { type: 'string' } },
    blocker: { type: ['string', 'null'] },
  },
}

// ---------- utilities ----------
const safe = (p, label) => p.catch(err => { log(`${label} failed: ${String((err && err.message) || err)}`); return null })
const gate = (cmd, label) =>
  safe(agent(`Run exactly this command from the repo root and return its exit code and its full output:\n\n${AGENTIC} ${cmd}`,
    { agentType: 'gatekeeper', label, schema: GATE_SCHEMA }), label)
const lastJson = out => {
  const lines = String(out || '').trim().split('\n').filter(l => l.trim().startsWith('{'))
  try { return lines.length ? JSON.parse(lines[lines.length - 1]) : null } catch (e) { return null }
}
// Several commands in one gatekeeper call. Returns { steps: [{ cmd, exit_code, output }] } (it stops at the first
// step that fails, so there can be fewer steps than commands) or null if the gatekeeper failed or garbled the output.
// A garbled reply is never retried: close-stage and open-stage change state, so repeating them is not safe.
const seq = async (cmds, label) => {
  if (cmds.length === 1) {
    const g = await gate(cmds[0], label)
    return g ? { steps: [{ cmd: cmds[0], exit_code: g.exit_code, output: g.output }] } : null
  }
  const g = await gate(`seq ${cmds.join(' + ')}`, label)
  const parsed = g ? lastJson(g.output) : null
  return parsed && Array.isArray(parsed.steps) ? parsed : null
}
const step = (res, i) => (res && res.steps[i]) || null
const ok = st => !!st && st.exit_code === 0
const BLOCKING = new Set(['critical', 'high'])
// findings.py, the same rules: a finding with no tier keeps its severity's meaning.
const TIERS = ['floor', 'reflex', 'finish']
const tierOf = f => (f && TIERS.includes(f.tier) ? f.tier : f && BLOCKING.has(f.severity) ? 'floor' : 'finish')
const inScope = (path, scope) => typeof path === 'string' && typeof scope === 'string' && scope !== '' &&
  (scope.endsWith('/') ? path.startsWith(scope) : path === scope)
const ruleKey = t => (typeof t === 'string' ? t.trim().toLowerCase() : '')
const waiverFor = (f, waivers) => {
  const rule = f && f.tier === 'reflex' ? ruleKey(f.rule) : ''
  if (!rule) return null
  return (waivers || []).find(w => w && typeof w === 'object' && ruleKey(w.rule) &&
    (rule.includes(ruleKey(w.rule)) || ruleKey(w.rule).includes(rule)) && inScope(f.file, w.scope)) || null
}
const blocks = (f, waivers) => {
  const tier = tierOf(f)
  return tier === 'reflex' ? !waiverFor(f, waivers) : tier === 'floor'
}
const textSum = text => {
  let sum = 0
  for (const ch of text) sum = (sum + ch.codePointAt(0)) % 1000000007
  return sum
}
// Each agent reads the slice of architecture.md that `agentic.py briefs` wrote for it, not the whole document.
const brief = id => `.claude/state/briefs/${id}.md`
const REFUTE_CONFIDENCE = 0.7
// verify and baseline run the whole suite: each goes in a gatekeeper call of its own, so it fits in one Bash timeout.
const WAVE_TAIL = ['verify', 'open-stage review', 'callers']
const HOOKS_DEAD = 'the guardrail hook is not running in this session: run `agentic.py hooks-check` for the cause, or use /implement fallback'
const LOST = 'the gatekeeper returned no usable result (a timeout or garbled output); the command may have run anyway: check `agentic.py status` before resuming'
// Files that close-stage saw change for an ID ("WP-01: 2 file(s) changed: a, b"). Used to compare what an agent
// says it did with what is on disk.
const changedBy = (out, id) => {
  const m = String(out || '').match(new RegExp(`^${id}: \\d+ file\\(s\\) changed(?:: (.*))?$`, 'm'))
  return m ? (m[1] ? m[1].split(', ') : []) : null
}
// Agents sometimes give absolute paths: it counts as seen if it ends in the relative path close-stage saw.
const unchangedClaims = (report, out, id) => {
  const seen = changedBy(out, id)
  return report && Array.isArray(report.files) && seen
    ? report.files.filter(f => !seen.some(s => f === s || f.endsWith('/' + s)))
    : []
}
const run = { result: 'REVIEW', stages: [], baseline: null, work_packages: {}, verification: null, review: null, qa: null, test_audit: null, docs: null, fix_cycles: 0, pending: [], not_checked: [], gate_denials: {}, verification_failures: [] }
// Every report says what it did not check; the run keeps them per call, fix calls and revisions included.
const report = async (prompt, opts) => {
  const r = await safe(agent(prompt, opts), opts.label)
  if (r && Array.isArray(r.not_checked) && r.not_checked.length) run.not_checked.push({ agent: opts.agentType, label: opts.label, items: r.not_checked })
  return r
}
// A needs-validation item goes to pending with its declared keys only: a severity an agent adds must never block.
const needsItem = (source, i) => ({ type: 'needs validation', source, wp: i.wp, file: i.file, ...(Number.isInteger(i.line) ? { line: i.line } : {}), kind: i.kind, claim: i.claim, fact: i.fact, how_to_check: i.how_to_check })
let needsReview = []  // the last review's needs-validation items
const concernPending = []  // DONE_WITH_CONCERNS doubts, kept across cycles and handed to every review
const contextGiven = new Set()  // packages already re-dispatched once with the whole document
// A pending item for a DONE_WITH_CONCERNS doubt: no severity, so it never blocks; the reviewer weighs it.
const concernItem = (wp, text) => ({ type: 'concern', source: wp, wp, problem: text })
// The close-stage marks of a wave. DONE and DONE_WITH_CONCERNS are done; no report is error; anything else
// (NEEDS_CONTEXT after its re-dispatch, BLOCKED, or a status outside the schema) is a blocker.
const DONE_STATUSES = new Set(['DONE', 'DONE_WITH_CONCERNS'])
const waveStates = (wpIds, reports) => wpIds.map((wp, i) => `${wp}=${!reports[i] ? 'error' : DONE_STATUSES.has(reports[i].status) ? 'done' : 'blocker'}`)
// The stop gate's denials in a close-stage output ("GATE: WP-01=1 WP-02=0"), per package; {} if absent.
const gateLine = out => {
  const m = String(out || '').match(/^GATE: (.*)$/m)
  const found = {}
  if (m) for (const [, wp, n] of m[1].matchAll(/(WP-\d+)=(\d+)/g)) found[wp] = parseInt(n, 10)
  return found
}
// What a NEEDS_CONTEXT report asked for, as text, or null when the report is malformed (then it is a blocker).
const missingContext = rep => {
  const mc = rep.missing_context
  if (!mc || typeof mc !== 'object' || typeof mc.what !== 'string') return null
  return mc.what + (typeof mc.section === 'string' && mc.section ? ` (section ${mc.section})` : '')
}
const stop = (stage, detail, extra) => ({ ...run, result: 'REVIEW', failed_stage: stage, detail, ...(extra || {}) })

// Fix attempts are counted per work package: a package that used up its attempts is left pending, the others go on.
const fixCount = {}
const fixHistory = {}
const limitPending = []
let qaPending = []  // the last QA's design and no-WP items: they stay listed whatever comes after
// A package's own key: plan.wave_of comes from JSON, so a name like "constructor" must not read as a package.
const isWp = wp => Object.prototype.hasOwnProperty.call(plan.wave_of, wp)
const pendingKey = d => [d.wp, d.source, d.file, d.problem || d.repro].join('|')
const splitByLimit = defects => {
  const eligible = []
  for (const d of defects) {
    if ((fixCount[d.wp] || 0) < plan.max_fix_cycles) eligible.push(d)
    else if (!limitPending.some(p => pendingKey(p) === pendingKey(d))) limitPending.push({ type: 'fix limit reached', ...d })
  }
  return eligible
}
const groupByWp = defects => {
  const by = Object.create(null)
  for (const d of defects) (by[d.wp] = by[d.wp] || []).push(d)
  return by
}
const wavesOf = by => [...new Set(Object.keys(by).map(wp => plan.wave_of[wp]))].sort((a, b) => a - b)

// A verification failure becomes defects of the packages that own what it mentions (agentic.py attributes them).
const verificationDefects = v => {
  const suspects = (v && v.suspects) || {}
  const failing = ((v && v.phases) || []).filter(p => p.status === 'regression' || p.status === 'fails')
  const evidence = failing.map(p => `${p.name}: ${(p.tail || []).join('\n')}`).join('\n').slice(0, 1500)
  return Object.entries(suspects).filter(([wp]) => isWp(wp)).map(([wp, info]) => ({
    source: 'verification', severity: 'high', wp, file: (info.files || [])[0] || '',
    problem: `verification fails (${(info.phases || []).join(', ')})${info.acs && info.acs.length ? ` on ${info.acs.join(', ')}` : ''}`, evidence,
  }))
}

// Living documentation: only with accepted code. The code map is generated by agentic.py (deterministic); the
// doc-updater brings README, CHANGELOG and guides up to date inside its zone, and docs-check checks links and paths.
// A documentation problem does not invalidate the code: it becomes a pending item, unless a write left its zone.
// `steps` are the [codemap, open-stage docs] results that came with the call that closed QA.
async function documentStage(steps) {
  phase('Documentation')
  run.docs = { codemap: null, report: null, ownership_ok: false, check: null }
  let i = 0
  if (plan.codemap) {
    const cm = step({ steps }, i++)
    run.docs.codemap = ok(cm) ? lastJson(cm.output) : null
  }
  if (!ok(step({ steps }, i))) {
    run.pending.push({ type: 'documentation', severity: 'medium', problem: 'the documentation stage could not be opened' })
    return
  }
  const doc = await report('Update the documentation according to architecture.md and the integrated code.' + RULES, { agentType: 'doc-updater', label: 'doc-updater', schema: DOCS_SCHEMA })
  const fin = await seq([`close-stage docs DOCS=${doc && doc.status === 'DONE' ? 'done' : 'blocker'}`, 'docs-check'], 'close-stage docs')
  const close = step(fin, 0)
  const check = step(fin, 1)
  run.docs.report = doc
  run.docs.ownership_ok = ok(close)
  run.docs.check = check ? lastJson(check.output) : null
  if (ok(close) && !run.docs.check) {
    run.pending.push({ type: 'documentation', severity: 'medium', problem: 'the documentation check did not run or its output could not be read' })
  }
  run.stages.push({ stage: 'documentation', ok: run.docs.ownership_ok && ok(check), check: close && close.output })
  const ghost = unchangedClaims(doc, close && close.output, 'DOCS')
  if (doc && doc.status === 'DONE' && ghost.length) {
    run.pending.push({ type: 'documentation', severity: 'medium', problem: 'the doc-updater claims changes that are not on disk', files: ghost })
  }
  if (!doc || doc.status !== 'DONE') {
    run.pending.push({ type: 'documentation', severity: 'medium', problem: doc ? `doc-updater blocked: ${doc.blocker}` : 'the doc-updater returned no report' })
  } else if (!run.docs.ownership_ok) {
    run.pending.push({ type: 'documentation', severity: 'high', problem: 'the doc-updater wrote outside its zone (see docs.check)' })
  }
  const broken = (run.docs.check && run.docs.check.broken) || []
  if (broken.length) run.pending.push({ type: 'documentation', severity: 'medium', problem: `${broken.length} broken link(s) or path(s) in the documentation`, broken })
  if (doc && doc.unverified && doc.unverified.length) run.pending.push({ type: 'documentation', severity: 'low', problem: 'claims the doc-updater could not verify', unverified: doc.unverified })
}

// Runs the agents of one wave (implementers, or fixers when defectsByWp is given). The stage is already open; the
// caller closes it together with whatever comes next.
async function launchWave(wave, wpIds, defectsByWp) {
  const reports = await parallel(wpIds.map(wp => () => {
    let prompt = `Implement ${wp}. Your brief is ${brief(wp)}: the parts of architecture.md your package needs.`
    if (defectsByWp) {
      const attempt = (fixCount[wp] || 0) + 1
      prompt = `Fix ${wp}. Your brief is ${brief(wp)}. The code already exists. These are the defects the judges (reviewer, QA and verification) attribute to your package, as JSON:\n` +
        `${JSON.stringify(defectsByWp[wp])}\nStay within the contract, do not touch other packages' files or the acceptance tests, and keep the acceptance tests of your criteria green. A defect in a file no package owns (for example, a caller your change broke) is fixed from your own files: keep that caller working with a compatible change, such as a default for a new parameter or a wrapper that keeps the old signature, unless section 6 explicitly forbids it. Return BLOCKED only when no compatible change exists in your files.`
      if (attempt > 1) {
        prompt += `\nThis is attempt ${attempt} of ${plan.max_fix_cycles}: the previous one did not fix it. The defects it was given:\n${JSON.stringify((fixHistory[wp] || []).slice(-2, -1)[0])}\n` +
          `What it changed is in .claude/state/runs/${wp}.fix-${attempt - 1}.diff: read it first and do not repeat that approach. ` +
          'If you try something different, say in internal_decisions how it differs and why it should work.'
      }
    }
    return report(prompt + IMPLEMENTER_RULES, { agentType: plan.agents[wp] || 'implementer', label: defectsByWp ? `${wp} (fix)` : wp, schema: REPORT_SCHEMA })
  }))
  const withContext = await withWholeDocument(wpIds, reports, !!defectsByWp)
  wpIds.forEach((wp, i) => {
    const rep = withContext[i]
    run.work_packages[wp] = rep
    if (rep && rep.status === 'DONE_WITH_CONCERNS') for (const c of rep.concerns || []) if (typeof c === 'string') concernPending.push(concernItem(wp, c))
  })
  return { reports: withContext, closeCmd: `close-stage ${wave} ${waveStates(wpIds, withContext).join(' ')}${defectsByWp ? ' --fix' : ''}` }
}

// NEEDS_CONTEXT in an implement wave: one more call, with the whole document, the first time only. The second report
// replaces the first. In a fix wave the agent already had the code and the defects: it stays as it is.
async function withWholeDocument(wpIds, reports, fixing) {
  const again = []
  wpIds.forEach((wp, i) => {
    const rep = reports[i]
    if (!rep || rep.status !== 'NEEDS_CONTEXT' || fixing || contextGiven.has(wp)) return
    const missing = missingContext(rep)
    if (missing === null) return
    contextGiven.add(wp)
    const prompt = `Implement ${wp}. Your first attempt returned NEEDS_CONTEXT: ${missing}. This time your brief is architecture.md, whole: read it and implement. If the document does not settle it, return BLOCKED with the section that should.`
    again.push({ i, run: () => report(prompt + IMPLEMENTER_RULES, { agentType: plan.agents[wp] || 'implementer', label: `${wp} (context)`, schema: REPORT_SCHEMA }) })
  })
  if (!again.length) return reports
  const second = await parallel(again.map(a => a.run))
  const out = [...reports]
  again.forEach((a, k) => { out[a.i] = second[k] })
  return out
}

// Runs a list of waves in order. Each close-stage travels in the same gatekeeper call as the open-stage of the next
// wave; after the last one, verify and open-stage review go in a call of their own. `opened`: the first wave is open.
// Returns { post: [verify step, open-stage review step] } or { stopped }.
async function runWaves(waves, byWave, defectsByWp, opened) {
  const fixing = !!defectsByWp
  if (!waves.length) {
    const t = await seq(WAVE_TAIL, 'verify')
    return { post: (t && t.steps) || [] }
  }
  for (let i = 0; i < waves.length; i += 1) {
    const wave = waves[i]
    if (!opened) {
      const open = await seq([`open-stage ${wave}`], `open-stage ${wave}`)
      if (!ok(step(open, 0))) return { stopped: stop(`${fixing ? 'fix ' : ''}wave ${wave}`, 'the stage could not be opened') }
    }
    const { closeCmd } = await launchWave(wave, byWave[wave], defectsByWp)
    const last = i === waves.length - 1
    const res = await seq([closeCmd, ...(last ? [] : [`open-stage ${waves[i + 1]}`])], `close-stage ${wave}`)
    const close = step(res, 0)
    if (!close) return { stopped: stop(fixing ? `fix wave ${wave}` : `wave ${wave}`, LOST) }
    const good = ok(close)
    run.stages.push({ stage: fixing ? `wave ${wave} (fix)` : `wave ${wave}`, ok: good, check: close && close.output })
    for (const [wp, n] of Object.entries(gateLine(close.output))) if (n) run.gate_denials[wp] = (run.gate_denials[wp] || 0) + n
    if (!good) {
      return { stopped: stop(fixing ? `fix wave ${wave}` : `wave ${wave}`, fixing
        ? 'the fix ended with blockers or writes outside ownership'
        : 'the wave ended with blockers, missing reports or writes outside ownership') }
    }
    if (last) {
      const t = await seq(WAVE_TAIL, 'verify')
      return { post: (t && t.steps) || [] }
    }
    if (!ok(step(res, 1))) return { stopped: stop(`wave ${waves[i + 1]}`, 'the stage could not be opened') }
    opened = true
  }
}

// One fix cycle for the given defects (already filtered by the per-package limit). `opened`: the first fix wave is open.
async function fixCycle(defects, opened) {
  run.fix_cycles += 1
  phase('Fixes')
  const by = groupByWp(defects)
  for (const wp of Object.keys(by)) {
    fixHistory[wp] = [...(fixHistory[wp] || []), by[wp]]
  }
  const waves = wavesOf(by)
  const byWave = {}
  for (const w of waves) byWave[w] = Object.keys(by).filter(wp => plan.wave_of[wp] === w).sort()
  const out = await runWaves(waves, byWave, by, opened)
  for (const wp of Object.keys(by)) fixCount[wp] = (fixCount[wp] || 0) + 1
  return out
}

// ---------- Prepare + Baseline ----------
// pipeline-plan and the baseline travel together. The baseline is the state of build, types, lint and tests before
// touching anything: that way a phase that already failed at the end is a "pre-existing failure" and is blamed on no
// package. When resuming from a wave (start_wave), the original run's baseline is kept.
phase('Prepare')
// hooks-check goes first: a session whose guardrail hook does not fire must not pay for a single agent.
const first = await seq(['hooks-check', 'pipeline-plan', 'briefs'], 'prepare')
const hookStep = step(first, 0)
if (hookStep && !ok(hookStep)) return stop('prepare', HOOKS_DEAD, { gate: hookStep })
const planStep = step(first, 1)
const plan = ok(planStep) ? lastJson(planStep.output) : null
if (!plan) return stop('prepare', 'pipeline-plan failed: the manifest is not valid', { gate: planStep })
// Language rules (rules-sync): every agent that writes or judges code receives them, printed when their text arrived
// whole through the gatekeeper (the checksum), else as their paths.
const rulesIntact = typeof plan.rules_text === 'string' && plan.rules_text !== '' &&
  textSum(plan.rules_text) === plan.rules_sum
const RULES = rulesIntact ? plan.rules_text
  : plan.rules && plan.rules.length ? ` Rules you must follow (language and common): ${plan.rules.join(', ')}.` : ''
run.rules = rulesIntact ? 'printed' : RULES ? 'pointed' : 'none'
const IMPLEMENTER_RULES = plan.implementer_rules === false ? '' : RULES
const WAIVERS = Array.isArray(plan.waivers) ? plan.waivers : []
const REVIEW_SOURCES = { spec: 'reviewer', standards: 'standards-reviewer' }
// Both reviewers in parallel, one verifier per finding that blocks, the record per axis (never reranked).
const reviewStage = async post => {
  phase('Review')
  const concerns = concernPending.length
    ? `\nThe implementers finished with these concerns about their own code, as JSON:\n${JSON.stringify(concernPending.map(c => ({ wp: c.wp, problem: c.problem })))}\nCheck each one: confirm it as a finding with its severity, or leave it out if the code is right.`
    : ''
  // What the `callers` step reported: the lines outside the diff that use a changed public symbol; [] when the step is
  // missing, failed or is not its JSON.
  const foundCallers = post[2] ? lastJson(post[2].output) : null
  const callerItems = foundCallers && Array.isArray(foundCallers.callers) && foundCallers.callers.length ? foundCallers.callers : []
  const callers = callerItems.length
    ? `\nCallers, outside the diff, of the public symbols this change defines or modifies (the reviewer must read them), as JSON:\n${JSON.stringify(callerItems)}\nA change that breaks one of them is a finding on the package that owns the changed symbol, even though the caller's file was not touched.${foundCallers.truncated ? ' The list is truncated.' : ''}`
    : ''
  const [specR, stdR] = await parallel([
    () => report('Review the integrated code according to architecture.md.' + callers + concerns + RULES, { agentType: 'reviewer', label: 'reviewer', schema: REVIEW_SCHEMA }),
    () => report('Review the integrated code against section 7 of architecture.md and the rules below. Report only breaches of a written rule: name it in "rule".' + RULES, { agentType: 'standards-reviewer', label: 'standards-reviewer', schema: STANDARDS_SCHEMA }),
  ])
  if (!specR) return { stopped: stop('review', 'the reviewer returned no report') }
  if (!stdR) return { stopped: stop('review', 'the standards reviewer returned no report') }
  const axes = {
    spec: specR.findings.map(f => ({ ...f, axis: 'spec' })),
    standards: stdR.findings.map(f => ({ ...f, axis: 'standards' })),
  }
  const waived = axes.standards.filter(f => waiverFor(f, WAIVERS))
    .map(f => ({ ...f, waiver: waiverFor(f, WAIVERS).reason }))
  const graves = [...axes.spec, ...axes.standards].filter(f => blocks(f, WAIVERS))
  const verdicts = await parallel(graves.map(f => () =>
    report(`Try to refute this finding from the reviewer, as JSON:\n${JSON.stringify(f)}`, { agentType: 'verifier', label: `verify ${f.file}`, schema: VERDICT_SCHEMA })))
  // A finding is only dismissed if the verifier refutes it with confidence. Uncertainty never downgrades a blocker.
  const refuted = graves.filter((f, i) =>
    verdicts[i] && verdicts[i].is_real === false && verdicts[i].confidence >= REFUTE_CONFIDENCE)
  const record = {}
  for (const axis of ['spec', 'standards']) {
    const mine = graves.filter(f => f.axis === axis)
    record[axis] = {
      confirmed: mine.filter(f => !refuted.includes(f)),
      refuted: mine.filter(f => refuted.includes(f)),
      advisory: axes[axis].filter(f => !graves.includes(f) && !waiverFor(f, WAIVERS)),
    }
  }
  record.standards.waived = waived
  const union = k => [...record.spec[k], ...record.standards[k]]
  run.review = {
    ...record, confirmed: union('confirmed'), refuted: union('refuted'), advisory: union('advisory'), waived,
    opened: ok(post[1]), callers: callerItems.length,
  }
  needsReview = (specR.needs_validation || []).map(i => needsItem('reviewer', i))
  // The reviewers are read-only: if closing their stage finds changes, someone wrote where nobody may.
  const closeReview = `close-stage review REVIEW=${run.review.confirmed.length ? 'fail' : 'pass'}`
  const afterReview = await seq([closeReview, 'open-stage qa'], 'close-stage review')
  if (!ok(step(afterReview, 0))) {
    return { stopped: stop('review', 'files changed during the review stage (the reviewer must not write)') }
  }
  return { confirmed: run.review.confirmed }
}
// The rest of the run, from briefs on.
const runAfterPlan = async () => {
if (!ok(step(first, 2))) return stop('prepare', 'briefs failed', { gate: step(first, 2) })
if (startWave === null) {
  phase('Baseline')
  const base = step(await seq(['baseline'], 'baseline'), 0)
  if (!base) return stop('baseline', LOST)
  run.baseline = lastJson(base.output)
  if (!ok(base)) return stop('baseline', 'a verify command could not be run (it does not exist or timed out)', { gate: base })
}

// ---------- Wave 0 ----------
if (startWave === null && plan.acceptance) {
  phase('Wave 0')
  if (plan.type === 'refactor') {
    // In a refactor, acceptance is the existing suite: it must be green before anything changes.
    const base = step(await seq(['acceptance --expect green'], 'baseline'), 0)
    run.stages.push({ stage: 'baseline', ok: ok(base), check: base && base.output })
    if (!ok(base)) return stop('wave 0', 'the existing suite is not green before the refactor', { gate: base })
  } else {
    const open = step(await seq(['open-stage acceptance'], 'open-stage acceptance'), 0)
    if (!ok(open)) return stop('wave 0', 'the acceptance stage could not be opened', { gate: open })
    let prompt = `Write the acceptance tests. Your brief is ${brief('AT')}: the parts of architecture.md they need.`
    for (let round = 0; ; round += 1) {
      const at = await report(prompt + RULES, { agentType: 'test-author', label: round ? 'test-author (revision)' : 'test-author', schema: AT_SCHEMA })
      const state = at && at.status === 'DONE' ? 'done' : 'blocker'
      const close = step(await seq([`close-stage acceptance AT=${state} --expect red`], 'close-stage acceptance'), 0)
      run.stages.push({ stage: round ? 'wave 0 (revision)' : 'wave 0', ok: ok(close), test_author: at, check: close && close.output })
      if (!ok(close)) {
        return stop('wave 0', at && at.status === 'BLOCKER'
          ? 'the test-author cannot test some criterion: architecture.md needs review'
          : 'acceptance did not end up red for the right reason (or files outside its zone were touched)', { test_author: at })
      }
      if (!at || at.status !== 'DONE') return stop('wave 0', 'the test-author returned no usable report', { test_author: at })
      // Red is not enough: a test can fail only because the code does not exist yet and still pass an empty stub.
      const audit = await report(`Audit the acceptance tests against the brief they were written from, ${brief('AT')}. The test-author reports this coverage, as JSON:\n${JSON.stringify(at.coverage)}` + RULES,
        { agentType: 'test-auditor', label: round ? 'test-auditor (revision)' : 'test-auditor', schema: AUDIT_SCHEMA })
      if (!audit) return stop('wave 0', 'the test-auditor returned no report')
      const weak = audit.findings.filter(f => blocks(f))
      run.test_audit = { rounds: round + 1, weak, advisory: audit.findings.filter(f => !blocks(f)) }
      if (!weak.length) break
      if (round >= 1) return stop('wave 0', 'the acceptance tests are still weak after one revision: architecture.md or the criteria need review', { test_audit: run.test_audit })
      const reopen = step(await seq(['open-stage acceptance'], 'open-stage acceptance'), 0)
      if (!ok(reopen)) return stop('wave 0', 'the acceptance stage could not be reopened', { gate: reopen })
      prompt = `Revise the acceptance tests (your brief is ${brief('AT')}). An independent auditor found these weak points, as JSON:\n` +
        `${JSON.stringify(weak)}\nStrengthen them: the tests of each criterion must fail against an empty or trivial implementation (a stub that returns nothing, ` +
        'None or a constant) and must cover the criterion\'s "must not". Keep them red for the right reason.'
    }
  }
}

// ---------- Waves ----------
phase('Waves')
const waves = plan.waves.filter(w => startWave === null || w >= startWave)
const wavesRun = await runWaves(waves, plan.work_packages, null, false)
if (wavesRun.stopped) return wavesRun.stopped
let post = wavesRun.post

// ---------- Verification, review, QA and fixes ----------
// `post` holds the results that came in the call that closed the last wave: [verify, open-stage review].
while (true) {
  phase('Verification')
  const v = post[0]
  if (!v) return stop('verification', LOST)
  run.verification = lastJson(v.output)
  if (!ok(v)) {
    // Each failed verification stays in the result: the run's last verification overwrites the earlier ones.
    const V = run.verification && typeof run.verification === 'object' ? run.verification : {}
    run.verification_failures.push({
      fix_cycle: run.fix_cycles, blocking: V.blocking || [], suspects: V.suspects || {},
      failing: (V.phases || []).filter(p => p.status === 'regression' || p.status === 'fails').map(p => ({ name: p.name, status: p.status, tail: p.tail || [] })),
    })
    // A phase fails: if what it mentions belongs to packages, they get the failure as a defect and go through a fix
    // cycle; if it belongs to nobody (a pre-existing, environmental or cross-cutting failure), a person decides.
    const defects = verificationDefects(run.verification)
    if (!defects.length) return stop('verification', 'a verification phase fails and no work package owns the failure (see verification)')
    const eligible = splitByLimit(defects)
    run.pending = [...qaPending, ...limitPending, ...concernPending]
    if (!eligible.length) return run
    const fixed = await fixCycle(eligible, false)
    if (fixed.stopped) return fixed.stopped
    post = fixed.post
    continue
  }

  const reviewed = await reviewStage(post)
  if (reviewed.stopped) return reviewed.stopped
  const confirmed = reviewed.confirmed

  phase('QA')
  const qa = await report('Run the system\'s QA validation according to architecture.md.' + RULES, { agentType: 'qa', label: 'qa', schema: QA_SCHEMA })
  if (!qa) return stop('qa', 'QA returned no report')
  run.qa = qa

  const implDefects = [
    ...confirmed.map(f => ({ source: REVIEW_SOURCES[f.axis], ...f })),
    ...qa.defects.filter(d => d.kind === 'implementation' && blocks(d)).map(d => ({ source: 'qa', ...d })),
  ].filter(d => isWp(d.wp))
  const designDefects = qa.defects.filter(d => d.kind === 'design')
  const unassigned = [...confirmed, ...qa.defects.filter(d => d.kind === 'implementation' && blocks(d))]
    .filter(d => !isWp(d.wp))
  const baseline = [...designDefects.map(d => ({ type: 'design', ...d })), ...unassigned.map(d => ({ type: 'no WP', ...d })),
    ...needsReview, ...(qa.needs_validation || []).map(i => needsItem('qa', i))]
  const closeQa = `close-stage qa QA=${qa.status === 'PASS' ? 'pass' : 'fail'}`

  const eligible = implDefects.length ? splitByLimit(implDefects) : []
  qaPending = baseline
  run.pending = [...qaPending, ...limitPending, ...concernPending]
  if (eligible.length === 0) {
    run.result = implDefects.length || qa.status === 'BLOCKER' || run.pending.some(p => blocks(p)) ? 'REVIEW' : 'OK'
    const docs = run.result === 'OK' && plan.docs
    const fin = await seq([closeQa, ...(docs ? [...(plan.codemap ? ['codemap'] : []), 'open-stage docs'] : [])], 'close-stage qa')
    if (!ok(step(fin, 0))) return stop('qa', 'files changed during the QA stage outside its zone')
    if (docs) {
      await documentStage(fin.steps.slice(1))
      if (run.pending.some(p => blocks(p))) run.result = 'REVIEW'
    }
    return run
  }

  const firstFixWave = wavesOf(groupByWp(eligible))[0]
  const fin = await seq([closeQa, `open-stage ${firstFixWave}`], 'close-stage qa')
  if (!ok(step(fin, 0))) return stop('qa', 'files changed during the QA stage outside its zone')
  if (!ok(step(fin, 1))) return stop(`fix wave ${firstFixWave}`, 'the stage could not be opened')
  const fixed = await fixCycle(eligible, true)
  if (fixed.stopped) return fixed.stopped
  post = fixed.post
}
}
return await runAfterPlan()
