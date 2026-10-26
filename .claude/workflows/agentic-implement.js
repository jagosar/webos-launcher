export const meta = {
  name: 'agentic-implement',
  description: 'Takes architecture.md to verified code: wave 0 (acceptance first), parallel waves, verification, adversarial review, QA, capped fix cycles and living documentation.',
  phases: [
    { title: 'Prepare', detail: 'pipeline-plan: waves, packages, acceptance and the fix limit' },
    { title: 'Baseline', detail: 'state of build, types, lint and tests before any change' },
    { title: 'Wave 0', detail: 'the test-author writes the acceptance tests; they must be red' },
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
// with agentic.py, and the guardrails (the PreToolUse hook) apply to every agent just as outside a workflow.
const opts = args || {}
const startWave = Number.isInteger(opts.start_wave) ? opts.start_wave : null
const AGENTIC = 'python3 .claude/bin/agentic.py'

// ---------- schemas ----------
const GATE_SCHEMA = {
  type: 'object',
  required: ['exit_code', 'output'],
  properties: { exit_code: { type: 'integer' }, output: { type: 'string' } },
}
const SEVERITY = { type: 'string', enum: ['critical', 'high', 'medium', 'low'] }
const AT_SCHEMA = {
  type: 'object',
  required: ['status', 'coverage', 'blocker'],
  properties: {
    status: { type: 'string', enum: ['DONE', 'BLOCKER'] },
    coverage: { type: 'array', items: { type: 'object', required: ['ac', 'tests'], properties: { ac: { type: 'string' }, tests: { type: 'array', items: { type: 'string' } } } } },
    blocker: { type: ['string', 'null'] },
  },
}
const REPORT_SCHEMA = {
  type: 'object',
  required: ['status', 'wp', 'files', 'checks', 'internal_decisions', 'blocker'],
  properties: {
    status: { type: 'string', enum: ['DONE', 'BLOCKER'] },
    wp: { type: 'string', pattern: '^WP-[0-9]+$' },
    files: { type: 'array', items: { type: 'string' } },
    checks: { type: 'array', items: { type: 'object', required: ['command', 'passed'], properties: { command: { type: 'string' }, passed: { type: 'boolean' }, detail: { type: 'string' } } } },
    internal_decisions: { type: 'array', items: { type: 'string' } },
    blocker: { type: ['object', 'null'], required: ['missing', 'section'], properties: { missing: { type: 'string' }, section: { type: 'string' }, options: { type: 'array', items: { type: 'string' } } } },
  },
}
const FINDINGS_SCHEMA = {
  type: 'object',
  required: ['status', 'findings'],
  properties: {
    status: { type: 'string', enum: ['PASS', 'FAIL'] },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        required: ['severity', 'wp', 'file', 'problem', 'evidence'],
        properties: { severity: SEVERITY, wp: { type: 'string' }, file: { type: 'string' }, line: { type: 'integer' }, problem: { type: 'string' }, evidence: { type: 'string' } },
      },
    },
  },
}
const VERDICT_SCHEMA = {
  type: 'object',
  required: ['is_real', 'confidence', 'reasoning'],
  properties: { is_real: { type: 'boolean' }, confidence: { type: 'number', minimum: 0, maximum: 1 }, reasoning: { type: 'string' } },
}
const QA_SCHEMA = {
  type: 'object',
  required: ['status', 'plan', 'defects', 'gaps'],
  properties: {
    status: { type: 'string', enum: ['PASS', 'FAIL', 'BLOCKER'] },
    plan: { type: 'array', items: { type: 'string' } },
    defects: {
      type: 'array',
      items: {
        type: 'object',
        required: ['id', 'severity', 'wp', 'kind', 'section', 'repro'],
        properties: { id: { type: 'string' }, severity: SEVERITY, wp: { type: 'string' }, kind: { type: 'string', enum: ['implementation', 'design'] }, section: { type: 'string' }, repro: { type: 'string' } },
      },
    },
    gaps: { type: 'array', items: { type: 'string' } },
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
const BLOCKING = new Set(['critical', 'high'])
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
const run = { result: 'REVIEW', stages: [], baseline: null, work_packages: {}, verification: null, review: null, qa: null, docs: null, fix_cycles: 0, pending: [] }
const stop = (stage, detail, extra) => ({ ...run, failed_stage: stage, detail, ...(extra || {}) })

// Living documentation: only with accepted code. The code map is generated by agentic.py (deterministic); the
// doc-updater brings README, CHANGELOG and guides up to date inside its zone, and docs-check checks links and paths.
// A documentation problem does not invalidate the code: it becomes a pending item, unless a write left its zone.
async function documentStage() {
  phase('Documentation')
  run.docs = { codemap: null, report: null, ownership_ok: false, check: null }
  if (plan.codemap) {
    const cm = await gate('codemap', 'codemap')
    run.docs.codemap = cm && cm.exit_code === 0 ? lastJson(cm.output) : null
  }
  const open = await gate('open-stage docs', 'open-stage docs')
  if (!open || open.exit_code !== 0) {
    run.pending.push({ type: 'documentation', severity: 'medium', problem: 'the documentation stage could not be opened' })
    return
  }
  const doc = await safe(agent('Update the documentation according to architecture.md and the integrated code.' + RULES, { agentType: 'doc-updater', label: 'doc-updater', schema: DOCS_SCHEMA }), 'doc-updater')
  const close = await gate(`close-stage docs DOCS=${doc && doc.status === 'DONE' ? 'done' : 'blocker'}`, 'close-stage docs')
  const check = await gate('docs-check', 'docs-check')
  run.docs.report = doc
  run.docs.ownership_ok = !!close && close.exit_code === 0
  run.docs.check = check ? lastJson(check.output) : null
  run.stages.push({ stage: 'documentation', ok: run.docs.ownership_ok && !!check && check.exit_code === 0, check: close && close.output })
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

async function runWave(wave, wpIds, defectsByWp) {
  const fixing = !!defectsByWp
  const open = await gate(`open-stage ${wave}`, `open-stage ${wave}`)
  if (!open || open.exit_code !== 0) return { ok: false, open }
  const reports = await parallel(wpIds.map(wp => () => {
    const prompt = fixing
      ? `Fix ${wp} according to architecture.md. The code already exists. These are the defects the judges (reviewer and QA) attribute to your package, as JSON:\n` +
        `${JSON.stringify(defectsByWp[wp])}\nStay within the contract, do not touch other packages' files or the acceptance tests, and keep the acceptance tests of your criteria green.`
      : `Implement ${wp} according to architecture.md.`
    return safe(agent(prompt + RULES, { agentType: plan.agents[wp] || 'implementer', label: fixing ? `${wp} (fix)` : wp, schema: REPORT_SCHEMA }), wp)
  }))
  wpIds.forEach((wp, i) => { run.work_packages[wp] = reports[i] })
  const states = wpIds.map((wp, i) => `${wp}=${!reports[i] ? 'error' : reports[i].status === 'DONE' ? 'done' : 'blocker'}`)
  const close = await gate(`close-stage ${wave} ${states.join(' ')}${fixing ? ' --fix' : ''}`, `close-stage ${wave}`)
  const ok = !!close && close.exit_code === 0
  run.stages.push({ stage: fixing ? `wave ${wave} (fix)` : `wave ${wave}`, ok, check: close && close.output })
  return { ok, reports, close }
}

// ---------- Prepare ----------
phase('Prepare')
const planGate = await gate('pipeline-plan', 'pipeline-plan')
const plan = planGate && planGate.exit_code === 0 ? lastJson(planGate.output) : null
if (!plan) return stop('prepare', 'pipeline-plan failed: the manifest is not valid', { gate: planGate })
// Language rules (rules-sync): every agent that writes or judges code receives them in its prompt.
const RULES = plan.rules && plan.rules.length ? ` Rules you must follow (language and common): ${plan.rules.join(', ')}.` : ''

// ---------- Baseline ----------
// Before touching anything: that way, at the end, a phase that already failed is a "pre-existing failure" and is
// blamed on no package. When resuming from a wave (start_wave), the original run's baseline is kept.
if (startWave === null && plan.baseline) {
  phase('Baseline')
  const base = await gate('baseline', 'baseline')
  run.baseline = base ? lastJson(base.output) : null
  if (!base || base.exit_code !== 0) return stop('baseline', 'a verify command could not be run (it does not exist or timed out)', { gate: base })
}

// ---------- Wave 0 ----------
if (startWave === null && plan.acceptance) {
  phase('Wave 0')
  if (plan.type === 'refactor') {
    // In a refactor, acceptance is the existing suite: it must be green before anything changes.
    const base = await gate('acceptance --expect green', 'baseline')
    run.stages.push({ stage: 'baseline', ok: !!base && base.exit_code === 0, check: base && base.output })
    if (!base || base.exit_code !== 0) return stop('wave 0', 'the existing suite is not green before the refactor', { gate: base })
  } else {
    const open = await gate('open-stage acceptance', 'open-stage acceptance')
    if (!open || open.exit_code !== 0) return stop('wave 0', 'the acceptance stage could not be opened', { gate: open })
    const at = await safe(agent('Write the acceptance tests according to architecture.md.' + RULES, { agentType: 'test-author', label: 'test-author', schema: AT_SCHEMA }), 'test-author')
    const state = at && at.status === 'DONE' ? 'done' : 'blocker'
    const close = await gate(`close-stage acceptance AT=${state} --expect red`, 'close-stage acceptance')
    run.stages.push({ stage: 'wave 0', ok: !!close && close.exit_code === 0, test_author: at, check: close && close.output })
    if (!close || close.exit_code !== 0) {
      return stop('wave 0', at && at.status === 'BLOCKER'
        ? 'the test-author cannot test some criterion: architecture.md needs review'
        : 'acceptance did not end up red for the right reason (or files outside its zone were touched)', { test_author: at })
    }
  }
}

// ---------- Waves ----------
phase('Waves')
for (const wave of plan.waves.filter(w => startWave === null || w >= startWave)) {
  const res = await runWave(wave, plan.work_packages[String(wave)], null)
  if (!res.ok) return stop(`wave ${wave}`, 'the wave ended with blockers, missing reports or writes outside ownership')
}

// ---------- Verification, review, QA and fixes ----------
while (true) {
  phase('Verification')
  const v = await gate('verify', 'verify')
  run.verification = v ? lastJson(v.output) : null
  if (!v || v.exit_code !== 0) return stop('verification', 'a verification phase fails (see verification)')

  phase('Review')
  const openR = await gate('open-stage review', 'open-stage review')
  const review = await safe(agent('Review the integrated code according to architecture.md.' + RULES, { agentType: 'reviewer', label: 'reviewer', schema: FINDINGS_SCHEMA }), 'reviewer')
  if (!review) return stop('review', 'the reviewer returned no report')
  const graves = review.findings.filter(f => BLOCKING.has(f.severity))
  const verdicts = await parallel(graves.map(f => () =>
    safe(agent(`Try to refute this finding from the reviewer, as JSON:\n${JSON.stringify(f)}`, { agentType: 'verifier', label: `verify ${f.file}`, schema: VERDICT_SCHEMA }), 'verifier')))
  // A finding is only dismissed if the verifier refutes it with confidence. Uncertainty never downgrades a blocker.
  const confirmed = graves.filter((f, i) => !(verdicts[i] && verdicts[i].is_real === false && verdicts[i].confidence >= 0.7))
  const refuted = graves.filter(f => !confirmed.includes(f))
  const advisory = review.findings.filter(f => !BLOCKING.has(f.severity))
  await gate(`close-stage review REVIEW=${confirmed.length ? 'fail' : 'pass'}`, 'close-stage review')
  run.review = { confirmed, refuted, advisory, opened: !!openR && openR.exit_code === 0 }

  phase('QA')
  await gate('open-stage qa', 'open-stage qa')
  const qa = await safe(agent('Run the system\'s QA validation according to architecture.md.' + RULES, { agentType: 'qa', label: 'qa', schema: QA_SCHEMA }), 'qa')
  if (!qa) return stop('qa', 'QA returned no report')
  await gate(`close-stage qa QA=${qa.status === 'PASS' ? 'pass' : 'fail'}`, 'close-stage qa')
  run.qa = qa

  const implDefects = [
    ...confirmed.map(f => ({ source: 'reviewer', ...f })),
    ...qa.defects.filter(d => d.kind === 'implementation' && BLOCKING.has(d.severity)).map(d => ({ source: 'qa', ...d })),
  ].filter(d => plan.wave_of[d.wp] !== undefined)
  const designDefects = qa.defects.filter(d => d.kind === 'design')
  const unassigned = [...confirmed, ...qa.defects.filter(d => d.kind === 'implementation' && BLOCKING.has(d.severity))]
    .filter(d => plan.wave_of[d.wp] === undefined)
  run.pending = [...designDefects.map(d => ({ type: 'design', ...d })), ...unassigned.map(d => ({ type: 'no WP', ...d }))]

  if (implDefects.length === 0) {
    run.result = qa.status === 'BLOCKER' || run.pending.some(p => BLOCKING.has(p.severity)) ? 'REVIEW' : 'OK'
    if (run.result === 'OK' && plan.docs) {
      await documentStage()
      if (run.pending.some(p => BLOCKING.has(p.severity))) run.result = 'REVIEW'
    }
    return run
  }
  if (run.fix_cycles >= plan.max_fix_cycles) {
    run.pending.push(...implDefects.map(d => ({ type: 'fix limit reached', ...d })))
    return run
  }

  run.fix_cycles += 1
  phase('Fixes')
  const byWp = {}
  for (const d of implDefects) (byWp[d.wp] = byWp[d.wp] || []).push(d)
  const waves = [...new Set(Object.keys(byWp).map(wp => plan.wave_of[wp]))].sort((a, b) => a - b)
  for (const wave of waves) {
    const ids = Object.keys(byWp).filter(wp => plan.wave_of[wp] === wave).sort()
    const res = await runWave(wave, ids, byWp)
    if (!res.ok) return stop(`fix wave ${wave}`, 'the fix ended with blockers or writes outside ownership')
  }
}
