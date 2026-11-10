// Tests of the orchestration logic of .claude/workflows/agentic-implement.js with simulated agents.
// Run: node .claude/bin/test_workflow.mjs   (no real calls: agent() and parallel() are doubles)
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const source = readFileSync(join(here, '..', 'workflows', 'agentic-implement.js'), 'utf8').replace(/^export const meta/m, 'const meta')
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const script = new AsyncFunction('agent', 'parallel', 'phase', 'log', 'args', source)

const conformance = JSON.parse(readFileSync(join(here, 'conformance.json'), 'utf8'))
const spec = JSON.parse(readFileSync(join(here, '..', 'workflows', 'pipeline-spec.json'), 'utf8'))
const PLAN = conformance.plan

// A configurable scenario: each key overrides the default behaviour (everything fine).
async function simulate(scenario = {}, args = {}) {
  const calls = []
  const schemas = {}
  const executed = []
  let gateCalls = 0
  let reviewRound = 0
  let standardsRound = 0
  let qaRound = 0
  let auditRound = 0
  const implCalls = {}
  const respond = cmd => {
    executed.push(cmd)
    const forced = scenario.gate && scenario.gate(cmd)
    if (forced) return forced
    if (cmd.startsWith('pipeline-plan')) return { exit_code: 0, output: JSON.stringify({ ...PLAN, ...(scenario.plan || {}) }) }
    if (cmd === 'verify') return { exit_code: 0, output: JSON.stringify({ ok: true, blocking: [], preexisting_failures: [], suspects: {}, phases: [{ name: 'tests', passed: true }] }) }
    if (cmd === 'baseline') return { exit_code: 0, output: JSON.stringify({ phases: [{ name: 'tests', passed: true }], preexisting_failures: [] }) }
    if (cmd === 'codemap') return { exit_code: 0, output: JSON.stringify({ path: 'docs/CODEMAP.md', files: 3, changed: true }) }
    if (cmd === 'docs-check') return { exit_code: 0, output: JSON.stringify({ ok: true, checked: 2, broken: [] }) }
    if (cmd === 'callers') return { exit_code: 0, output: JSON.stringify({ changed: [], symbols: {}, callers: [], truncated: false }) }
    return { exit_code: 0, output: 'RESULTADO: OK' }
  }
  const agent = async (prompt, opts) => {
    calls.push({ type: opts.agentType, label: opts.label, prompt })
    schemas[opts.agentType] = opts.schema
    switch (opts.agentType) {
      case 'gatekeeper': {
        gateCalls += 1
        const cmd = prompt.split('agentic.py ')[1].trim()
        const cmds = cmd.startsWith('seq ') ? cmd.slice(4).split(' + ') : [cmd]
        // A lost gatekeeper call: a timeout or a dead agent, so the script gets nothing back.
        if ((scenario.lost || []).some(prefix => cmds.some(c => c.startsWith(prefix)))) throw new Error('gatekeeper returned nothing')
        if (!cmd.startsWith('seq ')) return respond(cmd)
        if (scenario.garble) return { exit_code: 0, output: 'sorry, here is a summary instead' }
        const steps = []
        for (const sub of cmd.slice(4).split(' + ')) {
          const r = respond(sub)
          steps.push({ cmd: sub, exit_code: r.exit_code, output: r.output })
          if (r.exit_code !== 0) break
        }
        const failed = steps.find(x => x.exit_code !== 0)
        return { exit_code: failed ? failed.exit_code : 0, output: JSON.stringify({ ok: !failed, steps }) }
      }
      case 'test-author':
        return scenario.testAuthor || { status: 'DONE', coverage: [{ ac: 'AC-001', tests: ['test_ac_001'] }], blocker: null }
      case 'test-auditor':
        auditRound += 1
        return scenario.audit ? scenario.audit(auditRound) : { status: 'PASS', findings: [] }
      case 'implementer': {
        const wp = opts.label.split(' ')[0]
        const base = { status: 'DONE', wp, files: [], checks: [], internal_decisions: [], blocker: null, not_checked: [], concerns: [], missing_context: null }
        let extra = scenario.reports && scenario.reports.implementer && scenario.reports.implementer[wp]
        if (Array.isArray(extra)) {  // one override per call of that package: the first, then the last
          implCalls[wp] = (implCalls[wp] || 0) + 1
          extra = pickRound(extra, implCalls[wp])
        }
        return extra ? { ...base, ...extra } : base
      }
      case 'reviewer': {
        reviewRound += 1
        const r = scenario.review ? scenario.review(reviewRound) : { status: 'PASS', findings: [] }
        return scenario.reports && scenario.reports.reviewer ? { ...r, ...scenario.reports.reviewer } : r
      }
      case 'standards-reviewer': {
        standardsRound += 1
        return scenario.standards ? scenario.standards(standardsRound) : { status: 'PASS', axis: 'standards', findings: [], not_checked: [] }
      }
      case 'verifier':
        return scenario.verifier || { is_real: true, confidence: 0.9, reasoning: 'it holds' }
      case 'qa': {
        qaRound += 1
        const q = scenario.qa ? scenario.qa(qaRound) : { status: 'PASS', plan: ['contracts'], defects: [], gaps: [] }
        return scenario.reports && scenario.reports.qa ? { ...q, ...scenario.reports.qa } : q
      }
      case 'doc-updater':
        return scenario.docs || { status: 'DONE', files: ['README.md', 'CHANGELOG.md'], changes: ['usage of the new command'], unverified: [], blocker: null }
      default:
        throw new Error(`unexpected agentType: ${opts.agentType}`)
    }
  }
  const parallel = thunks => Promise.all(thunks.map(t => t()))
  const result = await script(agent, parallel, () => {}, () => {}, args)
  return { result, calls, schemas, gateCalls, gates: executed }
}

const grave = (wp, severity = 'high') => ({ severity, wp, file: 'src/a.py', line: 3, problem: 'p', evidence: 'e' })
const tests = []
const test = (name, fn) => tests.push({ name, fn })

test('happy path: wave 0, two waves, verification, review and QA in OK', async () => {
  const { result, calls, gates } = await simulate()
  assert.equal(result.result, 'OK')
  assert.equal(result.fix_cycles, 0)
  assert.ok(gates.includes('close-stage acceptance AT=done --expect red'), 'wave 0 demands red')
  assert.ok(gates.includes('close-stage 1 WP-01=done WP-02=done'))
  assert.ok(gates.includes('close-stage 2 WP-03=done'))
  assert.deepEqual(calls.filter(c => c.type === 'implementer').map(c => c.label), ['WP-01', 'WP-02', 'WP-03'])
  assert.ok(gates.indexOf('verify') > gates.indexOf('close-stage 2 WP-03=done'), 'verification comes after the waves')
})

test('test-author with BLOCKER: stops at wave 0 without launching implementers', async () => {
  const { result, calls, gates } = await simulate({
    testAuthor: { status: 'BLOCKER', coverage: [], blocker: 'AC-003 is not observable' },
    gate: cmd => (cmd.startsWith('close-stage acceptance') ? { exit_code: 3, output: 'RESULT: REVIEW' } : null),
  })
  assert.equal(result.result, 'REVIEW')
  assert.equal(result.failed_stage, 'wave 0')
  assert.ok(gates.includes('close-stage acceptance AT=blocker --expect red'))
  assert.equal(calls.filter(c => c.type === 'implementer').length, 0)
})

test('a wave in REVIEW stops the pipeline before the next one', async () => {
  const { result, calls } = await simulate({
    gate: cmd => (cmd.startsWith('close-stage 1 ') ? { exit_code: 3, output: 'OUTSIDE OWNERSHIP' } : null),
  })
  assert.equal(result.failed_stage, 'wave 1')
  assert.ok(!calls.some(c => c.label === 'WP-03'), 'wave 2 is not launched')
})

test('failed verification: stops before reviewing', async () => {
  const { result, calls } = await simulate({
    gate: cmd => (cmd === 'verify' ? { exit_code: 3, output: JSON.stringify({ ok: false, phases: [{ name: 'tests', passed: false }] }) } : null),
  })
  assert.equal(result.failed_stage, 'verification')
  assert.ok(!calls.some(c => c.type === 'reviewer'))
})

test('a serious finding refuted with confidence: does not block', async () => {
  const { result } = await simulate({
    review: () => ({ status: 'FAIL', findings: [grave('WP-01')] }),
    verifier: { is_real: false, confidence: 0.9, reasoning: 'validated in the caller' },
  })
  assert.equal(result.result, 'OK')
  assert.equal(result.review.refuted.length, 1)
  assert.equal(result.fix_cycles, 0)
})

test('uncertain refutation: the finding stands and its WP is fixed', async () => {
  const { result, calls, gates } = await simulate({
    review: round => (round === 1 ? { status: 'FAIL', findings: [grave('WP-02')] } : { status: 'PASS', findings: [] }),
    verifier: { is_real: false, confidence: 0.4, reasoning: 'not sure' },
  })
  assert.equal(result.result, 'OK')
  assert.equal(result.fix_cycles, 1)
  const fix = calls.find(c => c.label === 'WP-02 (fix)')
  assert.ok(fix && fix.prompt.includes('Fix WP-02'), 'the WP receives its defects')
  assert.ok(gates.includes('close-stage 1 WP-02=done --fix'), 'the fix counts as a fix cycle')
  assert.ok(!calls.some(c => c.label === 'WP-01 (fix)'), 'only the affected WP is fixed')
})

test('a defect that does not get fixed: capped at 2 cycles and left pending', async () => {
  const defect = { id: 'D1', severity: 'high', wp: 'WP-03', kind: 'implementation', section: '6.2', repro: 'x' }
  const { result, calls } = await simulate({ qa: () => ({ status: 'FAIL', plan: [], defects: [defect], gaps: [] }) })
  assert.equal(result.result, 'REVIEW')
  assert.equal(result.fix_cycles, 2)
  assert.equal(calls.filter(c => c.label === 'WP-03 (fix)').length, 2)
  assert.ok(result.pending.some(p => p.type === 'fix limit reached' && p.id === 'D1'))
})

test('a design defect: not fixed in a loop, returned as pending', async () => {
  const defect = { id: 'D2', severity: 'high', wp: 'WP-01', kind: 'design', section: '6.3', repro: 'y' }
  const { result, calls } = await simulate({ qa: () => ({ status: 'FAIL', plan: [], defects: [defect], gaps: [] }) })
  assert.equal(result.result, 'REVIEW')
  assert.equal(result.fix_cycles, 0)
  assert.ok(!calls.some(c => (c.label || '').endsWith('(fix)')))
  assert.ok(result.pending.some(p => p.type === 'design' && p.id === 'D2'))
})

test('refactor: no test-author, the existing suite is the baseline', async () => {
  const { calls, gates } = await simulate({ plan: { type: 'refactor' } })
  assert.ok(!calls.some(c => c.type === 'test-author'))
  assert.equal(gates[3], 'baseline')
  assert.equal(gates[4], 'acceptance --expect green')
})

test('baseline: goes before wave 0 and its result is saved', async () => {
  const { result, gates } = await simulate()
  assert.equal(gates[3], 'baseline')
  assert.ok(gates.indexOf('baseline') < gates.indexOf('open-stage acceptance'))
  assert.deepEqual(result.baseline.preexisting_failures, [])
})

test('baseline with a pre-existing failure: does not block; a command that cannot run does', async () => {
  const pre = await simulate({ gate: c => (c === 'baseline' ? { exit_code: 0, output: JSON.stringify({ phases: [], preexisting_failures: ['lint'] }) } : null) })
  assert.equal(pre.result.result, 'OK')
  assert.deepEqual(pre.result.baseline.preexisting_failures, ['lint'])
  const broken = await simulate({ gate: c => (c === 'baseline' ? { exit_code: 1, output: '{}' } : null) })
  assert.equal(broken.result.failed_stage, 'baseline')
})

test('pipeline-plan and the briefs travel in one gatekeeper call; the baseline goes next, alone', async () => {
  const { calls } = await simulate()
  const [first, second] = calls.filter(c => c.type === 'gatekeeper')
  assert.ok(first.prompt.endsWith('agentic.py seq hooks-check + pipeline-plan + briefs'))
  assert.ok(second.prompt.endsWith('agentic.py baseline'))
})

test('start_wave: skips the baseline, wave 0 and earlier waves', async () => {
  const { calls, gates } = await simulate({}, { start_wave: 2 })
  assert.ok(!gates.includes('baseline'))
  assert.ok(!calls.some(c => c.type === 'test-author'))
  assert.deepEqual(calls.filter(c => c.type === 'implementer').map(c => c.label), ['WP-03'])
})

test('an agent that fails is recorded as an error and the wave ends in REVIEW', async () => {
  const failing = { gate: cmd => (cmd.startsWith('close-stage 1 ') ? { exit_code: 3, output: 'REVIEW' } : null) }
  const { result, gates } = await simulate(failing)
  assert.equal(result.failed_stage, 'wave 1')
  assert.ok(gates.some(g => g.startsWith('close-stage 1 ')))
})

const DOCS_PLAN = { docs: true, codemap: 'docs/CODEMAP.md' }

test('living documentation: after green QA, codemap, doc-updater within its zone and docs-check', async () => {
  const { result, calls, gates } = await simulate({ plan: DOCS_PLAN })
  assert.equal(result.result, 'OK')
  const order = ['codemap', 'open-stage docs', 'close-stage docs DOCS=done', 'docs-check'].map(g => gates.indexOf(g))
  assert.ok(order.every(i => i > gates.indexOf('close-stage qa QA=pass')), 'documentation comes after QA')
  assert.deepEqual([...order].sort((a, b) => a - b), order, 'codemap, the stage with ownership, then the check, in that order')
  assert.equal(calls.filter(c => c.type === 'doc-updater').length, 1)
  assert.equal(result.docs.codemap.path, 'docs/CODEMAP.md')
})

test('without a docs section, or with the code in REVIEW, nothing is documented', async () => {
  const off = await simulate()
  assert.ok(!off.calls.some(c => c.type === 'doc-updater'))
  assert.ok(!off.gates.includes('codemap'))
  const defect = { id: 'D9', severity: 'high', wp: 'WP-01', kind: 'design', section: '6', repro: 'z' }
  const reviewed = await simulate({ plan: DOCS_PLAN, qa: () => ({ status: 'FAIL', plan: [], defects: [defect], gaps: [] }) })
  assert.equal(reviewed.result.result, 'REVIEW')
  assert.ok(!reviewed.calls.some(c => c.type === 'doc-updater'), 'code that is not accepted is not documented')
})

test('documentation with broken links: the code stays OK and a medium pending item remains', async () => {
  const broken = [{ file: 'README.md', line: 4, target: 'docs/x.md', kind: 'link' }]
  const { result } = await simulate({
    plan: DOCS_PLAN,
    gate: cmd => (cmd === 'docs-check' ? { exit_code: 3, output: JSON.stringify({ ok: false, checked: 2, broken }) } : null),
  })
  assert.equal(result.result, 'OK')
  assert.ok(result.pending.some(p => p.type === 'documentation' && p.severity === 'medium' && p.broken.length === 1))
})

test('doc-updater outside its zone: REVIEW', async () => {
  const { result } = await simulate({
    plan: DOCS_PLAN,
    gate: cmd => (cmd.startsWith('close-stage docs') ? { exit_code: 3, output: 'OUTSIDE OWNERSHIP' } : null),
  })
  assert.equal(result.result, 'REVIEW')
  assert.ok(result.pending.some(p => p.type === 'documentation' && p.severity === 'high'))
})

test('the doc-updater claims changes it did not make: a medium pending item with the files', async () => {
  const { result } = await simulate({
    plan: DOCS_PLAN,
    gate: cmd => (cmd.startsWith('close-stage docs') ? { exit_code: 0, output: 'DOCS: 0 file(s) changed\nRESULT: OK' } : null),
  })
  assert.equal(result.result, 'OK')
  const p = result.pending.find(x => x.problem.includes('not on disk'))
  assert.ok(p, 'it must be left pending')
  assert.deepEqual(p.files, ['README.md', 'CHANGELOG.md'])
  const honest = await simulate({
    plan: DOCS_PLAN,
    gate: cmd => (cmd.startsWith('close-stage docs') ? { exit_code: 0, output: 'DOCS: 2 file(s) changed: README.md, CHANGELOG.md' } : null),
  })
  assert.ok(!honest.result.pending.some(x => (x.problem || '').includes('not on disk')))
})

test('absolute paths in the doc-updater report: count as changed', async () => {
  const { result } = await simulate({
    plan: DOCS_PLAN,
    docs: { status: 'DONE', files: ['/repo/README.md', 'CHANGELOG.md'], changes: [], unverified: [], blocker: null },
    gate: cmd => (cmd.startsWith('close-stage docs') ? { exit_code: 0, output: 'DOCS: 2 file(s) changed: CHANGELOG.md, README.md' } : null),
  })
  assert.ok(!result.pending.some(x => (x.problem || '').includes('not on disk')))
})

test('language rules: reach the agents that write or judge code', async () => {
  const rules = ['.claude/rules/agentic/common.md', '.claude/rules/agentic/python.md']
  const { calls } = await simulate({ plan: { ...DOCS_PLAN, rules } })
  for (const type of ['test-author', 'test-auditor', 'implementer', 'reviewer', 'standards-reviewer', 'qa', 'doc-updater']) {
    const call = calls.find(c => c.type === type)
    assert.ok(call.prompt.includes(rules.join(', ')), `${type} does not receive the rules`)
  }
  assert.ok(!calls.find(c => c.type === 'gatekeeper').prompt.includes('Rules'), 'the gatekeeper does not need them')
  const none = await simulate({ plan: { rules: [] } })
  assert.ok(!none.calls.find(c => c.type === 'implementer').prompt.includes('Rules'))
})


test('gatekeeper calls are batched: close-stage travels with the next open-stage', async () => {
  const { gates, gateCalls } = await simulate()
  assert.equal(gateCalls, 10, `a run with two waves needs 10 gatekeeper calls, not one per command (${gates.length} commands)`)
  assert.deepEqual(gates, [
    'hooks-check', 'pipeline-plan', 'briefs', 'baseline', 'open-stage acceptance', 'close-stage acceptance AT=done --expect red', 'open-stage 1',
    'close-stage 1 WP-01=done WP-02=done', 'open-stage 2',
    'close-stage 2 WP-03=done', 'verify', 'open-stage review', 'callers',
    'close-stage review REVIEW=pass', 'open-stage qa', 'close-stage qa QA=pass',
  ])
})

test('baseline and verify run the whole suite: each travels without another long command', async () => {
  // One Bash call per long command keeps each within the gatekeeper's timeout; bundling them could exceed it.
  const { calls } = await simulate()
  const prompts = calls.filter(c => c.type === 'gatekeeper').map(c => c.prompt.split('agentic.py ')[1])
  const withBaseline = prompts.filter(p => /(^|[ +])baseline( |$)/.test(p))
  const withVerify = prompts.filter(p => /(^|[ +])verify( |$)/.test(p))
  assert.deepEqual(withBaseline, ['baseline'])
  assert.deepEqual(withVerify, ['seq verify + open-stage review + callers'])
})

test('briefs that cannot be written stop the run before anything starts', async () => {
  const { result, calls } = await simulate({ gate: cmd => (cmd === 'briefs' ? { exit_code: 1, output: 'boom' } : null) })
  assert.equal(result.failed_stage, 'prepare')
  assert.equal(result.detail, 'briefs failed')
  assert.equal(calls.filter(c => c.type !== 'gatekeeper').length, 0)
})

test('agents read their brief, the judges the whole document', async () => {
  const { calls } = await simulate({ review: round => (round === 1 ? { status: 'FAIL', findings: [grave('WP-02')] } : { status: 'PASS', findings: [] }), plan: { docs: true, codemap: 'docs/CODEMAP.md' } })
  const prompt = type => calls.find(c => c.type === type).prompt
  assert.ok(prompt('implementer').includes('.claude/state/briefs/WP-01.md'))
  assert.ok(prompt('test-author').includes('.claude/state/briefs/AT.md'))
  assert.ok(prompt('test-auditor').includes('.claude/state/briefs/AT.md'))
  assert.ok(calls.find(c => c.label === 'WP-02 (fix)').prompt.includes('.claude/state/briefs/WP-02.md'))
  for (const type of ['reviewer', 'qa', 'verifier', 'doc-updater']) assert.ok(!prompt(type).includes('briefs/'), type)
})

test('a garbled gatekeeper reply is never retried and stops the run', async () => {
  const { result, gateCalls } = await simulate({ garble: true })
  assert.equal(result.failed_stage, 'prepare')
  assert.equal(gateCalls, 1, 'close-stage and open-stage change state: repeating them is not safe')
})

test('a stage that fails stops the batch: nothing after it runs', async () => {
  const { gates } = await simulate({ gate: cmd => (cmd.startsWith('close-stage 2 ') ? { exit_code: 3, output: 'OUTSIDE OWNERSHIP' } : null) })
  assert.ok(!gates.includes('verify'), 'verify must not run after a failed close-stage')
  assert.ok(!gates.includes('open-stage review'))
})

const suspectsOf = (wp, extra = {}) => ({
  ok: false, blocking: ['tests'], preexisting_failures: [], suspects: { [wp]: { phases: ['tests'], files: ['src/b/x.py'], acs: ['AC-002'], ...extra } },
  phases: [{ name: 'tests', passed: false, status: 'fails', tail: ['FAILED src/b/x.py test_ac_002'] }],
})

test('verification that fails on a package: that package is fixed, then verification runs again', async () => {
  let verifies = 0
  const { result, calls, gates } = await simulate({
    gate: cmd => {
      if (cmd !== 'verify') return null
      verifies += 1
      return verifies === 1 ? { exit_code: 3, output: JSON.stringify(suspectsOf('WP-02')) } : null
    },
  })
  assert.equal(result.result, 'OK')
  assert.equal(result.fix_cycles, 1)
  const fix = calls.find(c => c.label === 'WP-02 (fix)')
  assert.ok(fix, 'WP-02 receives the failure')
  assert.ok(fix.prompt.includes('verification fails (tests) on AC-002') && fix.prompt.includes('FAILED src/b/x.py'), 'with the evidence')
  assert.ok(!calls.some(c => c.type === 'reviewer') || gates.indexOf('verify') < gates.indexOf('close-stage review REVIEW=pass'), 'review only after verification passes')
  assert.equal(verifies, 2)
  assert.ok(gates.includes('close-stage 1 WP-02=done --fix'))
})

test('verification that fails with no owner: stops, as there is nobody to fix it', async () => {
  const { result } = await simulate({ gate: cmd => (cmd === 'verify' ? { exit_code: 3, output: JSON.stringify({ ok: false, suspects: {}, phases: [] }) } : null) })
  assert.equal(result.failed_stage, 'verification')
})

test('verification that never gets fixed: capped per package and left pending', async () => {
  const { result, calls } = await simulate({ gate: cmd => (cmd === 'verify' ? { exit_code: 3, output: JSON.stringify(suspectsOf('WP-01')) } : null) })
  assert.equal(result.result, 'REVIEW')
  assert.equal(calls.filter(c => c.label === 'WP-01 (fix)').length, 2)
  assert.ok(result.pending.some(p => p.type === 'fix limit reached' && p.wp === 'WP-01'))
})

test('the second fix attempt gets the first one\'s defects and its diff; the first does not', async () => {
  const defect = { id: 'D1', severity: 'high', wp: 'WP-03', kind: 'implementation', section: '6.2', repro: 'x' }
  const { calls } = await simulate({ qa: () => ({ status: 'FAIL', plan: [], defects: [defect], gaps: [] }) })
  const fixes = calls.filter(c => c.label === 'WP-03 (fix)')
  assert.equal(fixes.length, 2)
  assert.ok(!fixes[0].prompt.includes('.diff') && !fixes[0].prompt.includes('attempt'))
  assert.ok(fixes[1].prompt.includes('.claude/state/runs/WP-03.fix-1.diff'))
  assert.ok(fixes[1].prompt.includes('attempt 2 of 2') && fixes[1].prompt.includes('"id":"D1"') && fixes[1].prompt.includes('internal_decisions'))
})

test('the fix limit is per package: one that is used up does not stop another that is still fixable', async () => {
  const d = (id, wp) => ({ id, severity: 'high', wp, kind: 'implementation', section: '6', repro: 'x' })
  const { result, calls } = await simulate({
    qa: round => ({ status: 'FAIL', plan: [], defects: round === 1 ? [d('A', 'WP-01'), d('B', 'WP-03')] : round === 2 ? [d('A', 'WP-01')] : round === 3 ? [d('A', 'WP-01'), d('C', 'WP-03')] : [d('C', 'WP-03')], gaps: [] }),
  })
  assert.equal(calls.filter(c => c.label === 'WP-01 (fix)').length, 2, 'WP-01 stops at its limit')
  assert.equal(calls.filter(c => c.label === 'WP-03 (fix)').length, 2, 'WP-03 keeps its own count')
  assert.ok(result.pending.some(p => p.type === 'fix limit reached'))
  assert.equal(result.result, 'REVIEW')
})

test('the review and QA stages are closed with ownership: a write there stops the run', async () => {
  const review = await simulate({ gate: cmd => (cmd.startsWith('close-stage review') ? { exit_code: 3, output: 'OUTSIDE OWNERSHIP' } : null) })
  assert.equal(review.result.failed_stage, 'review')
  assert.ok(!review.calls.some(c => c.type === 'qa'), 'QA does not run after a violation')
  const qa = await simulate({ gate: cmd => (cmd.startsWith('close-stage qa') ? { exit_code: 3, output: 'OUTSIDE OWNERSHIP' } : null) })
  assert.equal(qa.result.failed_stage, 'qa')
  assert.notEqual(qa.result.result, 'OK')
})

test('test-auditor: a weak test sends the test-author back once, red again, and then the waves start', async () => {
  const weak = { severity: 'high', wp: 'AT', file: 'acceptance/test_a.py', problem: 'assert x is not None', evidence: 'an empty stub passes' }
  const { result, calls, gates } = await simulate({ audit: round => (round === 1 ? { status: 'FAIL', findings: [weak] } : { status: 'PASS', findings: [] }) })
  assert.equal(result.result, 'OK')
  assert.equal(calls.filter(c => c.type === 'test-author').length, 2)
  const revision = calls.find(c => c.label === 'test-author (revision)')
  assert.ok(revision.prompt.includes('assert x is not None') && revision.prompt.includes('empty or trivial implementation'))
  assert.equal(gates.filter(g => g === 'open-stage acceptance').length, 2, 'the acceptance stage is reopened')
  assert.equal(gates.filter(g => g.startsWith('close-stage acceptance')).length, 2, 'and red is demanded again')
  assert.ok(gates.indexOf('open-stage 1') > gates.lastIndexOf('close-stage acceptance AT=done --expect red'), 'no implementer before the tests are good')
  assert.equal(result.test_audit.rounds, 2)
})

test('test-auditor: tests that are still weak after the revision stop wave 0; advisory findings do not', async () => {
  const weak = { severity: 'critical', wp: 'AT', file: 'acceptance/test_a.py', problem: 'p', evidence: 'e' }
  const stuck = await simulate({ audit: () => ({ status: 'FAIL', findings: [weak] }) })
  assert.equal(stuck.result.failed_stage, 'wave 0')
  assert.equal(stuck.calls.filter(c => c.type === 'implementer').length, 0)
  const minor = await simulate({ audit: () => ({ status: 'PASS', findings: [{ ...weak, severity: 'low' }] }) })
  assert.equal(minor.result.result, 'OK')
  assert.equal(minor.calls.filter(c => c.type === 'test-author').length, 1)
  assert.equal(minor.result.test_audit.advisory.length, 1)
})

test('test-auditor: without a report the run stops; in a refactor it is not used', async () => {
  const none = await simulate({ audit: () => null })
  assert.equal(none.result.failed_stage, 'wave 0')
  const refactor = await simulate({ plan: { type: 'refactor' } })
  assert.ok(!refactor.calls.some(c => c.type === 'test-auditor'))
})


// ---------- conformance with the fallback engine ----------
// The scenarios of conformance.json are described declaratively so that pipeline.py can run them too.
// `node test_workflow.mjs --dump` prints what this engine did in each one; test_pipeline.py compares it with its own.
const pickRound = (list, round) => list[Math.min(round, list.length) - 1]
function fromDecl(d) {
  let verifies = 0
  const text = o => (typeof o === 'string' ? o : JSON.stringify(o))
  return {
    plan: d.plan,
    gate: cmd => {
      for (const f of d.gate_fail || []) if (cmd.startsWith(f.prefix)) return { exit_code: f.exit_code, output: text(f.output) }
      if (cmd === 'verify' && d.verify) {
        const v = pickRound(d.verify, verifies + 1)
        verifies += 1
        if (v) return { exit_code: v.exit_code, output: text(v.output) }
      }
      return null
    },
    lost: d.gate_lost,
    testAuthor: d.test_author,
    audit: d.audit ? round => pickRound(d.audit, round) : undefined,
    review: d.review ? round => pickRound(d.review, round) : undefined,
    standards: d.standards ? round => pickRound(d.standards, round) : undefined,
    qa: d.qa ? round => pickRound(d.qa, round) : undefined,
    verifier: d.verifier,
    docs: d.docs,
    reports: d.reports,
  }
}
// Counts of the last review, per axis: spec [confirmed, refuted, advisory], standards [..., waived].
const reviewAxes = review => {
  if (!review) return null
  const spec = review.spec || {}
  const std = review.standards || {}
  return { spec: ['confirmed', 'refuted', 'advisory'].map(k => (spec[k] || []).length), standards: ['confirmed', 'refuted', 'advisory', 'waived'].map(k => (std[k] || []).length) }
}
const summarize = ({ result, calls, gates, gateCalls }) => {
  const seen = {}
  const prompts = {}
  const agents = []
  for (const c of calls.filter(x => x.type !== 'gatekeeper')) {
    const key = `${c.type}:${c.label}`
    seen[key] = (seen[key] || 0) + 1
    prompts[`${key}#${seen[key]}`] = c.prompt
    agents.push(key)
  }
  return {
    result: result.result, failed_stage: result.failed_stage ?? null, detail: result.detail ?? null, fix_cycles: result.fix_cycles,
    pending: (result.pending || []).map(p => p.type).sort(), gates, gate_calls: gateCalls, agents: agents.sort(), prompts,
    not_checked: (result.not_checked || []).map(e => `${e.agent}:${e.label}`).sort(),
    gate_denials: result.gate_denials || {},
    review_callers: (result.review && result.review.callers) || 0,
    verification_failures: (result.verification_failures || []).length,
    review_axes: reviewAxes(result.review), rules: result.rules ?? null,
  }
}
if (process.argv.includes('--dump')) {
  const out = {}
  for (const d of conformance.scenarios) out[d.name] = summarize(await simulate(fromDecl(d), d.args || {}))
  // No process.exit: past 64 KB a pipe would be cut before stdout drains; the process ends when the write is done.
  process.stdout.write(JSON.stringify(out) + '\n')
} else {


test('the schemas of the workflow are the ones in pipeline-spec.json', async () => {
  const { schemas } = await simulate({ plan: { docs: true, codemap: 'docs/CODEMAP.md' } })
  for (const type of ['gatekeeper', 'test-author', 'test-auditor', 'implementer', 'reviewer', 'standards-reviewer', 'verifier', 'qa', 'doc-updater']) {
    // the verifier only runs with a serious finding
    if (type === 'verifier') continue
    assert.deepEqual(schemas[type], spec.schemas[type], `${type}: the schema drifted from pipeline-spec.json`)
  }
  const grave = { severity: 'high', wp: 'WP-01', file: 'src/a.py', line: 3, problem: 'p', evidence: 'e' }
  const withFinding = await simulate({ review: () => ({ status: 'FAIL', findings: [grave] }), verifier: { is_real: false, confidence: 0.9, reasoning: 'r' } })
  assert.deepEqual(withFinding.schemas.verifier, spec.schemas.verifier)
})

test('the constants of the workflow are the ones in pipeline-spec.json', async () => {
  const source = readFileSync(join(here, '..', 'workflows', 'agentic-implement.js'), 'utf8')
  assert.ok(source.includes(`const REFUTE_CONFIDENCE = ${spec.constants.refute_confidence}`))
  for (const sev of spec.constants.blocking) assert.ok(source.includes(`'${sev}'`))
  assert.deepEqual(spec.constants.blocking, ['critical', 'high'])
  assert.equal(spec.constants.audit_rounds, 2, 'the workflow allows one revision of the acceptance tests')
})

test('every conformance scenario ends as described in its name', async () => {
  const byName = Object.fromEntries(conformance.scenarios.map(d => [d.name, d]))
  const run1 = async name => summarize(await simulate(fromDecl(byName[name]), byName[name].args || {}))
  assert.equal((await run1('happy path')).result, 'OK')
  assert.equal((await run1('verification fails on a package: it is fixed and verified again')).fix_cycles, 1)
  assert.equal((await run1('the test-auditor returns nothing')).failed_stage, 'wave 0')
  assert.equal((await run1('the fix limit is per package')).result, 'REVIEW')
  const once = await run1('a package at its fix limit is listed once')
  assert.deepEqual(once.pending, ['fix limit reached'], 'listed once, not once per cycle')
  assert.deepEqual((await run1('a design defect survives a later verification failure')).pending, ['design', 'fix limit reached'])
  assert.ok((await run1('a documentation check that cannot run is reported')).pending.includes('documentation'))
  const needs = await run1('needs validation goes to pending and does not block')
  assert.deepEqual([needs.result, needs.pending], ['OK', ['needs validation', 'needs validation']])
  assert.deepEqual((await run1('not_checked is collected from every report')).not_checked, ['implementer:WP-02', 'reviewer:reviewer'])
  assert.deepEqual((await run1('gate denials are summed from close-stage')).gate_denials, { 'WP-01': 1 })
  const concerns = await run1('concerns go to pending and to the reviewer')
  assert.deepEqual([concerns.result, concerns.pending], ['OK', ['concern']])
  assert.ok(concerns.prompts['reviewer:reviewer#1'].includes('may spin'))
  const ctxOnce = await run1('needs context is re-dispatched once with the whole document')
  assert.deepEqual([ctxOnce.result, ctxOnce.agents.filter(a => a === 'implementer:WP-02 (context)').length], ['OK', 1])
  const twice = await run1('needs context twice is a blocker')
  assert.deepEqual([twice.result, twice.failed_stage, twice.agents.filter(a => a === 'implementer:WP-02 (context)').length], ['REVIEW', 'wave 1', 1])
  const inFix = await run1('needs context in a fix wave is a blocker')
  assert.deepEqual([inFix.result, inFix.failed_stage, inFix.agents.filter(a => a.includes('(context)')).length], ['REVIEW', 'fix wave 1', 0])
  const reached = await run1('callers reach the reviewer')
  assert.equal(reached.review_callers, 1)
  assert.ok(reached.prompts['reviewer:reviewer#1'].includes('src/b/x.py'))
  const ignoredCallers = await run1('callers that are not JSON are ignored')
  assert.equal(ignoredCallers.review_callers, 0)
  for (const [name, count] of [['verification fails on a package: it is fixed and verified again', 1], ['verification never gets fixed', 3], ['verification fails with no owner', 1], ['happy path', 0]]) {
    assert.equal((await run1(name)).verification_failures, count, name)
  }
  assert.ok(!ignoredCallers.prompts['reviewer:reviewer#1'].includes('Callers'))
  const odd = await run1('a finding on an unknown package named like a built-in goes to no WP')
  assert.deepEqual([odd.result, odd.pending], ['REVIEW', ['no WP']])
  const asText = await run1('start_wave given as text works like a number')
  assert.ok(!asText.gates.includes('baseline'))
  assert.deepEqual(asText.agents.filter(a => a.startsWith('implementer')), ['implementer:WP-03'])
  // A lost gatekeeper call is reported as such, not as blockers, an ownership breach or an unowned failure.
  for (const [name, stage] of [['a lost gatekeeper call at the baseline stops the run', 'baseline'],
    ['a lost gatekeeper call when closing the last wave stops the run', 'wave 2'],
    ['a lost gatekeeper call at verification stops the run', 'verification']]) {
    const r = await run1(name)
    assert.equal(r.failed_stage, stage, name)
    assert.match(r.detail || '', /^the gatekeeper returned no usable result/, name)
  }
})

let failed = 0
for (const { name, fn } of tests) {
  try {
    await fn()
    console.log(`ok   ${name}`)
  } catch (err) {
    failed += 1
    console.log(`FAIL ${name}\n     ${err.message}`)
  }
}
console.log(`\n${tests.length - failed}/${tests.length} scenarios OK`)
process.exit(failed ? 1 : 0)
}
