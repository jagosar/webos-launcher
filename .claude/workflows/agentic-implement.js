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
const REFUTE_CONFIDENCE = 0.7
const WAVE_TAIL = ['verify', 'open-stage review']
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
const run = { result: 'REVIEW', stages: [], baseline: null, work_packages: {}, verification: null, review: null, qa: null, test_audit: null, docs: null, fix_cycles: 0, pending: [] }
const stop = (stage, detail, extra) => ({ ...run, result: 'REVIEW', failed_stage: stage, detail, ...(extra || {}) })

// Fix attempts are counted per work package: a package that used up its attempts is left pending, the others go on.
const fixCount = {}
const fixHistory = {}
const limitPending = []
const splitByLimit = defects => {
  const eligible = []
  for (const d of defects) {
    if ((fixCount[d.wp] || 0) >= plan.max_fix_cycles) limitPending.push({ type: 'fix limit reached', ...d })
    else eligible.push(d)
  }
  return eligible
}
const groupByWp = defects => {
  const by = {}
  for (const d of defects) (by[d.wp] = by[d.wp] || []).push(d)
  return by
}
const wavesOf = by => [...new Set(Object.keys(by).map(wp => plan.wave_of[wp]))].sort((a, b) => a - b)

// A verification failure becomes defects of the packages that own what it mentions (agentic.py attributes them).
const verificationDefects = v => {
  const suspects = (v && v.suspects) || {}
  const failing = ((v && v.phases) || []).filter(p => p.status === 'regression' || p.status === 'fails')
  const evidence = failing.map(p => `${p.name}: ${(p.tail || []).join('\n')}`).join('\n').slice(0, 1500)
  return Object.entries(suspects).filter(([wp]) => plan.wave_of[wp] !== undefined).map(([wp, info]) => ({
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
  const doc = await safe(agent('Update the documentation according to architecture.md and the integrated code.' + RULES, { agentType: 'doc-updater', label: 'doc-updater', schema: DOCS_SCHEMA }), 'doc-updater')
  const fin = await seq([`close-stage docs DOCS=${doc && doc.status === 'DONE' ? 'done' : 'blocker'}`, 'docs-check'], 'close-stage docs')
  const close = step(fin, 0)
  const check = step(fin, 1)
  run.docs.report = doc
  run.docs.ownership_ok = ok(close)
  run.docs.check = check ? lastJson(check.output) : null
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
    let prompt = `Implement ${wp} according to architecture.md.`
    if (defectsByWp) {
      const attempt = (fixCount[wp] || 0) + 1
      prompt = `Fix ${wp} according to architecture.md. The code already exists. These are the defects the judges (reviewer, QA and verification) attribute to your package, as JSON:\n` +
        `${JSON.stringify(defectsByWp[wp])}\nStay within the contract, do not touch other packages' files or the acceptance tests, and keep the acceptance tests of your criteria green.`
      if (attempt > 1) {
        prompt += `\nThis is attempt ${attempt} of ${plan.max_fix_cycles}: the previous one did not fix it. The defects it was given:\n${JSON.stringify((fixHistory[wp] || []).slice(-2, -1)[0])}\n` +
          `What it changed is in .claude/state/runs/${wp}.fix-${attempt - 1}.diff: read it first and do not repeat that approach. ` +
          'If you try something different, say in internal_decisions how it differs and why it should work.'
      }
    }
    return safe(agent(prompt + RULES, { agentType: plan.agents[wp] || 'implementer', label: defectsByWp ? `${wp} (fix)` : wp, schema: REPORT_SCHEMA }), wp)
  }))
  wpIds.forEach((wp, i) => { run.work_packages[wp] = reports[i] })
  const states = wpIds.map((wp, i) => `${wp}=${!reports[i] ? 'error' : reports[i].status === 'DONE' ? 'done' : 'blocker'}`)
  return { reports, closeCmd: `close-stage ${wave} ${states.join(' ')}${defectsByWp ? ' --fix' : ''}` }
}

// Runs a list of waves in order. Each close-stage travels in the same gatekeeper call as the open-stage of the next
// wave; the last one also carries verify and open-stage review. `opened`: the first wave is already open.
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
    const res = await seq([closeCmd, ...(last ? WAVE_TAIL : [`open-stage ${waves[i + 1]}`])], `close-stage ${wave}`)
    const close = step(res, 0)
    const good = ok(close)
    run.stages.push({ stage: fixing ? `wave ${wave} (fix)` : `wave ${wave}`, ok: good, check: close && close.output })
    if (!good) {
      return { stopped: stop(fixing ? `fix wave ${wave}` : `wave ${wave}`, fixing
        ? 'the fix ended with blockers or writes outside ownership'
        : 'the wave ended with blockers, missing reports or writes outside ownership') }
    }
    if (last) return { post: res.steps.slice(1) }
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
const first = await seq(startWave === null ? ['pipeline-plan', 'baseline'] : ['pipeline-plan'], 'prepare')
const planStep = step(first, 0)
const plan = ok(planStep) ? lastJson(planStep.output) : null
if (!plan) return stop('prepare', 'pipeline-plan failed: the manifest is not valid', { gate: planStep })
// Language rules (rules-sync): every agent that writes or judges code receives them in its prompt.
const RULES = plan.rules && plan.rules.length ? ` Rules you must follow (language and common): ${plan.rules.join(', ')}.` : ''
if (startWave === null) {
  phase('Baseline')
  const base = step(first, 1)
  run.baseline = base ? lastJson(base.output) : null
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
    let prompt = 'Write the acceptance tests according to architecture.md.'
    for (let round = 0; ; round += 1) {
      const at = await safe(agent(prompt + RULES, { agentType: 'test-author', label: round ? 'test-author (revision)' : 'test-author', schema: AT_SCHEMA }), 'test-author')
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
      const audit = await safe(agent(`Audit the acceptance tests against architecture.md. The test-author reports this coverage, as JSON:\n${JSON.stringify(at.coverage)}` + RULES,
        { agentType: 'test-auditor', label: round ? 'test-auditor (revision)' : 'test-auditor', schema: FINDINGS_SCHEMA }), 'test-auditor')
      if (!audit) return stop('wave 0', 'the test-auditor returned no report')
      const weak = audit.findings.filter(f => BLOCKING.has(f.severity))
      run.test_audit = { rounds: round + 1, weak, advisory: audit.findings.filter(f => !BLOCKING.has(f.severity)) }
      if (!weak.length) break
      if (round >= 1) return stop('wave 0', 'the acceptance tests are still weak after one revision: architecture.md or the criteria need review', { test_audit: run.test_audit })
      const reopen = step(await seq(['open-stage acceptance'], 'open-stage acceptance'), 0)
      if (!ok(reopen)) return stop('wave 0', 'the acceptance stage could not be reopened', { gate: reopen })
      prompt = 'Revise the acceptance tests according to architecture.md. An independent auditor found these weak points, as JSON:\n' +
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
  run.verification = v ? lastJson(v.output) : null
  if (!ok(v)) {
    // A phase fails: if what it mentions belongs to packages, they get the failure as a defect and go through a fix
    // cycle; if it belongs to nobody (a pre-existing, environmental or cross-cutting failure), a person decides.
    const defects = verificationDefects(run.verification)
    if (!defects.length) return stop('verification', 'a verification phase fails and no work package owns the failure (see verification)')
    const eligible = splitByLimit(defects)
    run.pending = [...limitPending]
    if (!eligible.length) return run
    const fixed = await fixCycle(eligible, false)
    if (fixed.stopped) return fixed.stopped
    post = fixed.post
    continue
  }

  phase('Review')
  const review = await safe(agent('Review the integrated code according to architecture.md.' + RULES, { agentType: 'reviewer', label: 'reviewer', schema: FINDINGS_SCHEMA }), 'reviewer')
  if (!review) return stop('review', 'the reviewer returned no report')
  const graves = review.findings.filter(f => BLOCKING.has(f.severity))
  const verdicts = await parallel(graves.map(f => () =>
    safe(agent(`Try to refute this finding from the reviewer, as JSON:\n${JSON.stringify(f)}`, { agentType: 'verifier', label: `verify ${f.file}`, schema: VERDICT_SCHEMA }), 'verifier')))
  // A finding is only dismissed if the verifier refutes it with confidence. Uncertainty never downgrades a blocker.
  const confirmed = graves.filter((f, i) => !(verdicts[i] && verdicts[i].is_real === false && verdicts[i].confidence >= REFUTE_CONFIDENCE))
  const refuted = graves.filter(f => !confirmed.includes(f))
  const advisory = review.findings.filter(f => !BLOCKING.has(f.severity))
  run.review = { confirmed, refuted, advisory, opened: ok(post[1]) }
  // The reviewer is read-only: if closing its stage finds changes, someone wrote where nobody may.
  const afterReview = await seq([`close-stage review REVIEW=${confirmed.length ? 'fail' : 'pass'}`, 'open-stage qa'], 'close-stage review')
  if (!ok(step(afterReview, 0))) return stop('review', 'files changed during the review stage (the reviewer must not write)')

  phase('QA')
  const qa = await safe(agent('Run the system\'s QA validation according to architecture.md.' + RULES, { agentType: 'qa', label: 'qa', schema: QA_SCHEMA }), 'qa')
  if (!qa) return stop('qa', 'QA returned no report')
  run.qa = qa

  const implDefects = [
    ...confirmed.map(f => ({ source: 'reviewer', ...f })),
    ...qa.defects.filter(d => d.kind === 'implementation' && BLOCKING.has(d.severity)).map(d => ({ source: 'qa', ...d })),
  ].filter(d => plan.wave_of[d.wp] !== undefined)
  const designDefects = qa.defects.filter(d => d.kind === 'design')
  const unassigned = [...confirmed, ...qa.defects.filter(d => d.kind === 'implementation' && BLOCKING.has(d.severity))]
    .filter(d => plan.wave_of[d.wp] === undefined)
  const baseline = [...designDefects.map(d => ({ type: 'design', ...d })), ...unassigned.map(d => ({ type: 'no WP', ...d }))]
  const closeQa = `close-stage qa QA=${qa.status === 'PASS' ? 'pass' : 'fail'}`

  const eligible = implDefects.length ? splitByLimit(implDefects) : []
  run.pending = [...baseline, ...limitPending]
  if (eligible.length === 0) {
    run.result = implDefects.length || qa.status === 'BLOCKER' || run.pending.some(p => BLOCKING.has(p.severity)) ? 'REVIEW' : 'OK'
    const docs = run.result === 'OK' && plan.docs
    const fin = await seq([closeQa, ...(docs ? [...(plan.codemap ? ['codemap'] : []), 'open-stage docs'] : [])], 'close-stage qa')
    if (!ok(step(fin, 0))) return stop('qa', 'files changed during the QA stage outside its zone')
    if (docs) {
      await documentStage(fin.steps.slice(1))
      if (run.pending.some(p => BLOCKING.has(p.severity))) run.result = 'REVIEW'
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
