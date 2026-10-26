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

const PLAN = {
  waves: [1, 2], acceptance: true, type: 'feature', verify: ['tests'], max_fix_cycles: 2, baseline: true,
  work_packages: { 1: ['WP-01', 'WP-02'], 2: ['WP-03'] },
  agents: { 'WP-01': 'implementer', 'WP-02': 'implementer', 'WP-03': 'implementer' },
  wave_of: { 'WP-01': 1, 'WP-02': 1, 'WP-03': 2 },
}

// A configurable scenario: each key overrides the default behaviour (everything fine).
async function simulate(scenario = {}, args = {}) {
  const calls = []
  let reviewRound = 0
  let qaRound = 0
  const agent = async (prompt, opts) => {
    calls.push({ type: opts.agentType, label: opts.label, prompt })
    switch (opts.agentType) {
      case 'gatekeeper': {
        const cmd = prompt.split('agentic.py ')[1].trim()
        if (scenario.gate && scenario.gate(cmd)) return scenario.gate(cmd)
        if (cmd === 'pipeline-plan') return { exit_code: 0, output: JSON.stringify({ ...PLAN, ...(scenario.plan || {}) }) }
        if (cmd === 'verify') return { exit_code: 0, output: JSON.stringify({ ok: true, blocking: [], preexisting_failures: [], phases: [{ name: 'tests', passed: true }] }) }
        if (cmd === 'baseline') return { exit_code: 0, output: JSON.stringify({ phases: [{ name: 'tests', passed: true }], preexisting_failures: [] }) }
        if (cmd === 'codemap') return { exit_code: 0, output: JSON.stringify({ path: 'docs/CODEMAP.md', files: 3, changed: true }) }
        if (cmd === 'docs-check') return { exit_code: 0, output: JSON.stringify({ ok: true, checked: 2, broken: [] }) }
        return { exit_code: 0, output: 'RESULTADO: OK' }
      }
      case 'test-author':
        return scenario.testAuthor || { status: 'DONE', coverage: [{ ac: 'AC-001', tests: ['test_ac_001'] }], blocker: null }
      case 'implementer': {
        const wp = opts.label.split(' ')[0]
        return { status: 'DONE', wp, files: [], checks: [], internal_decisions: [], blocker: null }
      }
      case 'reviewer':
        reviewRound += 1
        return scenario.review ? scenario.review(reviewRound) : { status: 'PASS', findings: [] }
      case 'verifier':
        return scenario.verifier || { is_real: true, confidence: 0.9, reasoning: 'it holds' }
      case 'qa':
        qaRound += 1
        return scenario.qa ? scenario.qa(qaRound) : { status: 'PASS', plan: ['contracts'], defects: [], gaps: [] }
      case 'doc-updater':
        return scenario.docs || { status: 'DONE', files: ['README.md', 'CHANGELOG.md'], changes: ['usage of the new command'], unverified: [], blocker: null }
      default:
        throw new Error(`agentType inesperado: ${opts.agentType}`)
    }
  }
  const parallel = thunks => Promise.all(thunks.map(t => t()))
  const result = await script(agent, parallel, () => {}, () => {}, args)
  return { result, calls, gates: calls.filter(c => c.type === 'gatekeeper').map(c => c.prompt.split('agentic.py ')[1].trim()) }
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
  assert.equal(gates[1], 'baseline')
  assert.equal(gates[2], 'acceptance --expect green')
})

test('baseline: goes before wave 0 and its result is saved', async () => {
  const { result, gates } = await simulate()
  assert.equal(gates[1], 'baseline')
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

test('without verify commands there is no baseline', async () => {
  const { gates } = await simulate({ plan: { baseline: false } })
  assert.ok(!gates.includes('baseline'))
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
  for (const type of ['test-author', 'implementer', 'reviewer', 'qa', 'doc-updater']) {
    const call = calls.find(c => c.type === type)
    assert.ok(call.prompt.includes(rules.join(', ')), `${type} does not receive the rules`)
  }
  assert.ok(!calls.find(c => c.type === 'gatekeeper').prompt.includes('Rules'), 'the gatekeeper does not need them')
  const none = await simulate({ plan: { rules: [] } })
  assert.ok(!none.calls.find(c => c.type === 'implementer').prompt.includes('Rules'))
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
