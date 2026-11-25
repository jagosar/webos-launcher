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
// No shell, no disk: the gatekeeper runs agentic.py, several steps at a time (`seq a + b`), under the same hooks.
// Schemas, prompts and routing mirror pipeline-spec.json (also run-pipeline's); test_workflow.mjs checks it.
const opts = args || {}
const startWave = /^\d+$/.test(String(opts.start_wave ?? '')) ? Number(opts.start_wave) : null  // 2 or "2"
const AGENTIC = 'python3 .claude/bin/agentic.py'

// ---------- schemas ----------
const obj = (required, properties) => ({ type: 'object', required, properties })
const GATE_SCHEMA = obj(['exit_code', 'output'], { exit_code: { type: 'integer' }, output: { type: 'string' } })
const SEVERITY = { type: 'string', enum: ['critical', 'high', 'medium', 'low'] }
// What happens to a finding: floor never ships, reflex blocks unless the plan waives it, finish never blocks.
const JUDGE_TIER = { type: 'string', enum: ['floor', 'finish'] }
const PROVENANCE = { type: 'string', enum: ['read', 'inferred'] }
// What the agent did not check, skipped, or sees as a risk; [] when nothing.
const NOT_CHECKED = { type: 'array', items: { type: 'string' } }
// A claim the code alone cannot settle: no severity, never blocks, the person checks the fact.
const NEEDS_VALIDATION = { type: 'array', items: obj(['wp', 'file', 'kind', 'claim', 'fact', 'how_to_check'], { wp: { type: 'string' }, file: { type: 'string' }, line: { type: 'integer' }, kind: { type: 'string', enum: ['external', 'unchanged_code'] }, claim: { type: 'string' }, fact: { type: 'string' }, how_to_check: { type: 'string' } }) }
const AT_SCHEMA = obj(['status', 'coverage', 'blocker', 'not_checked'], {
  status: { type: 'string', enum: ['DONE', 'BLOCKER'] },
  coverage: { type: 'array', items: { type: 'object', required: ['ac', 'tests'], properties: { ac: { type: 'string' }, tests: { type: 'array', items: { type: 'string' } } } } },
  blocker: { type: ['string', 'null'] },
  not_checked: NOT_CHECKED,
})
const REPORT_SCHEMA = obj(['status', 'wp', 'files', 'checks', 'internal_decisions', 'blocker', 'not_checked',
  'concerns', 'missing_context'], {
  status: { type: 'string', enum: ['DONE', 'DONE_WITH_CONCERNS', 'NEEDS_CONTEXT', 'BLOCKED'] },
  wp: { type: 'string', pattern: '^WP-[0-9]+$' },
  files: { type: 'array', items: { type: 'string' } },
  checks: { type: 'array', items: { type: 'object', required: ['command', 'passed'], properties: { command: { type: 'string' }, passed: { type: 'boolean' }, detail: { type: 'string' } } } },
  internal_decisions: { type: 'array', items: { type: 'string' } },
  blocker: { type: ['object', 'null'], required: ['missing', 'section'], properties: { missing: { type: 'string' }, section: { type: 'string' }, options: { type: 'array', items: { type: 'string' } },
    in: { type: 'string', enum: ['design', 'acceptance'] } } },
  not_checked: NOT_CHECKED,
  concerns: { type: 'array', items: { type: 'string' } },
  missing_context: { type: ['object', 'null'], required: ['what', 'section'], properties: { what: { type: 'string' }, section: { type: 'string' } } },
})
const AUDIT_SCHEMA = obj(['status', 'findings', 'not_checked'], {
  status: { type: 'string', enum: ['PASS', 'FAIL'] },
  findings: { type: 'array', items: obj(['tier', 'severity', 'wp', 'file', 'problem', 'evidence'], { tier: JUDGE_TIER, severity: SEVERITY, wp: { type: 'string' }, file: { type: 'string' }, line: { type: 'integer' }, problem: { type: 'string' }, evidence: { type: 'string' } }) },
  not_checked: NOT_CHECKED,
})
// A finding has four parts: problem, evidence (the concrete case), fix and if_skipped.
const REVIEW_SCHEMA = obj(['status', 'findings', 'needs_validation', 'not_checked'], {
  status: { type: 'string', enum: ['PASS', 'FAIL'] },
  findings: { type: 'array', items: obj(['tier', 'severity', 'provenance', 'wp', 'file', 'problem', 'evidence', 'fix', 'if_skipped'], { tier: JUDGE_TIER, severity: SEVERITY, wp: { type: 'string' }, file: { type: 'string' }, line: { type: 'integer' }, problem: { type: 'string' }, evidence: { type: 'string' }, fix: { type: 'string' }, if_skipped: { type: 'string' }, provenance: PROVENANCE }) },
  needs_validation: NEEDS_VALIDATION,
  not_checked: NOT_CHECKED,
})
// The Standards axis: breaches of a written rule (reflex) and notes (finish); "axis" tells its report from the others.
const STANDARDS_SCHEMA = obj(['status', 'axis', 'findings', 'not_checked'], {
  status: { type: 'string', enum: ['PASS', 'FAIL'] },
  axis: { type: 'string', enum: ['standards'] },
  findings: { type: 'array', items: obj(['tier', 'rule', 'provenance', 'wp', 'file', 'problem', 'evidence', 'fix', 'if_skipped'], { tier: { type: 'string', enum: ['reflex', 'finish'] }, rule: { type: 'string' }, provenance: PROVENANCE, wp: { type: 'string' }, file: { type: 'string' }, line: { type: 'integer' }, problem: { type: 'string' }, evidence: { type: 'string' }, fix: { type: 'string' }, if_skipped: { type: 'string' } }) },
  not_checked: NOT_CHECKED,
})
const VERDICT_SCHEMA = obj(['is_real', 'confidence', 'reasoning'], { is_real: { type: 'boolean' }, confidence: { type: 'number', minimum: 0, maximum: 1 }, reasoning: { type: 'string' } })
const QA_SCHEMA = obj(['status', 'plan', 'defects', 'gaps', 'needs_validation'], {
  status: { type: 'string', enum: ['PASS', 'FAIL', 'BLOCKER'] },
  plan: { type: 'array', items: { type: 'string' } },
  defects: { type: 'array', items: obj(['tier', 'id', 'severity', 'wp', 'kind', 'section', 'repro', 'fix', 'if_skipped'], { tier: JUDGE_TIER, id: { type: 'string' }, severity: SEVERITY, wp: { type: 'string' }, kind: { type: 'string', enum: ['implementation', 'design'] }, section: { type: 'string' }, repro: { type: 'string' }, fix: { type: 'string' }, if_skipped: { type: 'string' } }) },
  gaps: { type: 'array', items: { type: 'string' } },
  needs_validation: NEEDS_VALIDATION,
  // Optional: a BLOCKER the environment caused (a command refused, a tool missing), and the commands QA could not run.
  blocker_kind: { type: 'string', enum: ['environment', 'design'] },
  not_run: { type: 'array', items: { type: 'string' } },
})
// A re-review: one verdict per fingerprint it was given; anything new is recorded and never reopens the fix.
const TEXT = { type: 'string' }
const VERDICT_ITEM = obj(['fingerprint', 'verdict', 'evidence'], { fingerprint: TEXT, verdict: { type: 'string', enum: ['ADDRESSED', 'NOT_ADDRESSED'] }, evidence: TEXT })
const NEW_FINDING = obj(['tier', 'wp', 'file', 'problem', 'evidence'], { tier: { type: 'string', enum: ['floor', 'reflex', 'finish'] }, wp: TEXT, file: TEXT, problem: TEXT, evidence: TEXT })
const RE_REVIEW_SCHEMA = obj(['status', 'verdicts', 'new_findings', 'not_checked'], {
  status: { type: 'string', enum: ['PASS', 'FAIL'] },
  verdicts: { type: 'array', items: VERDICT_ITEM },
  new_findings: { type: 'array', items: NEW_FINDING },
  not_checked: NOT_CHECKED,
})
const DOCS_SCHEMA = obj(['status', 'files', 'changes', 'unverified', 'blocker'], {
  status: { type: 'string', enum: ['DONE', 'BLOCKER'] },
  files: { type: 'array', items: { type: 'string' } },
  changes: { type: 'array', items: { type: 'string' } },
  unverified: { type: 'array', items: { type: 'string' } },
  blocker: { type: ['string', 'null'] },
})

// ---------- utilities ----------
const safe = (p, label) => p.catch(err => { log(`${label} failed: ${String((err && err.message) || err)}`); return null })
const gate = (cmd, label) =>
  safe(agent(`Run exactly this command from the repo root and return its exit code and its full output:\n\n${AGENTIC} ${cmd}`,
    { agentType: 'gatekeeper', label, schema: GATE_SCHEMA }), label)
const lastJson = out => {
  const lines = String(out || '').trim().split('\n').filter(l => l.trim().startsWith('{'))
  try { return lines.length ? JSON.parse(lines[lines.length - 1]) : null } catch (e) { return null }
}
// Several commands in one gatekeeper call: { steps: [{ cmd, exit_code, output }] }, up to the first failing step, or
// null when the reply is lost or garbled. Not retried (close-stage changes state), except through seqRetry.
const seq = async (cmds, label) => {
  if (cmds.length === 1) {
    const g = await gate(cmds[0], label)
    return g ? { steps: [{ cmd: cmds[0], exit_code: g.exit_code, output: g.output }] } : null
  }
  const g = await gate(`seq ${cmds.join(' + ')}`, label)
  const parsed = g ? lastJson(g.output) : null
  return parsed && Array.isArray(parsed.steps) ? parsed : null
}
// Once more if lost, or crashed before agentic.py answered (exit not 0/1/3, no JSON): prepare and lone open-stages.
const ANSWERED = [0, 1, 3]
const seqRetry = async (cmds, label) => {
  const res = await seq(cmds, label)
  const last = res && res.steps.length ? res.steps[res.steps.length - 1] : null
  const crashed = !!last && !ANSWERED.includes(last.exit_code) && !lastJson(last.output)
  if (res && !crashed) return res
  const output = String(last ? last.output : '').slice(0, 300)
  run.environment_retries.push({ label, cmd: cmds.join(' + '), exit_code: last ? last.exit_code : null, output })
  log(`${label}: retried once (environment error)`)
  return seq(cmds, label)
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
// findings.py's FNV-1a over axis|tier|rule|file|problem (trimmed, lowered, 80 code points); no line or evidence.
const fingerprint = f => {
  const part = k => (f[k] === undefined || f[k] === null ? '' : String(f[k]))
  const trimmed = part('problem').replace(/^[ \t\r\n]+|[ \t\r\n]+$/g, '').toLowerCase()
  const problem = Array.from(trimmed).slice(0, 80).join('')
  const text = [part('axis'), part('tier'), part('rule'), part('file'), problem].join('|')
  let h = 2166136261
  for (const ch of text) h = Math.imul(h ^ ch.codePointAt(0), 16777619) >>> 0
  return 'F' + h.toString(16).padStart(8, '0')
}
// Copies with "fingerprint"; the second and later of a repeated value get "-2", "-3".
const withFingerprints = findings => {
  const seen = {}
  return findings.map(f => {
    const fp = fingerprint(f)
    seen[fp] = (seen[fp] || 0) + 1
    return { ...f, fingerprint: seen[fp] === 1 ? fp : `${fp}-${seen[fp]}` }
  })
}
// findings.py's split_by_owner: a confirmed finding whose evidence or fix names files of other packages (whole paths,
// root removed, ASCII case folded) keeps its own files in "scope" and gets one part per other owner, in id order.
const lowAscii = t => t.replace(/[A-Z]/g, c => c.toLowerCase())
const noRoot = (text, root) => {
  const t = text.replace(/\\/g, '/')
  const needle = lowAscii(root.replace(/\\/g, '/') + '/')
  const low = lowAscii(t)
  if (!root) return t
  let out = ''
  let i = 0
  for (let j = low.indexOf(needle); j >= 0; j = low.indexOf(needle, i)) { out += t.slice(i, j); i = j + needle.length }
  return out + t.slice(i)
}
const pathRe = f => new RegExp('(?<![A-Za-z0-9_./-])' + lowAscii(f).replace(/[.*+?^${}()|[\]\\]/g, '\\$&') +
  (f.endsWith('/') ? '' : '(?![A-Za-z0-9_-])'))
const named = (files, low) => files.filter(f => typeof f === 'string' && f && pathRe(f).test(low))
const str = v => (typeof v === 'string' ? v : '')
const splitByOwner = findings => {
  const owners = plan.owners && typeof plan.owners === 'object' && !Array.isArray(plan.owners) ? plan.owners : {}
  const root = str(plan.root)
  const taken = new Set(findings.map(f => f && f.fingerprint))
  return findings.flatMap(f => {
    if (!f || typeof f !== 'object') return [f]
    const wp = typeof f.wp === 'string' ? f.wp : null
    const low = lowAscii(noRoot(str(f.evidence) + '\n' + str(f.fix), root))
    const others = Object.keys(owners).sort().filter(o => o !== wp && Array.isArray(owners[o]))
      .map(o => [o, named(owners[o], low)]).filter(([, files]) => files.length)
    if (!others.length) return [f]
    const own = noRoot(str(f.file), root)
    const mine = wp !== null && Array.isArray(owners[wp]) ? named(owners[wp], low) : []
    return [{ ...f, scope: [...new Set([...(own ? [own] : []), ...mine])].sort() }, ...others.map(([o, files]) => {
      const scope = [...new Set(files)].sort()
      const part = { ...f, wp: o, file: scope[0], scope, split_from: f.fingerprint ?? null }
      delete part.line
      const base = fingerprint(part)
      let fp = base
      for (let n = 2; taken.has(fp); n += 1) fp = `${base}-${n}`
      taken.add(fp)
      return { ...part, fingerprint: fp }
    })]
  })
}
// findings.py's QA helpers: a report the environment blocked is pending, one item per command QA could not run.
const QA_NOT_RUN_DETAIL = 'QA did not run: the environment refused its commands ' +
  '(see the pending items of kind "qa not run")'
const qaEnvironmentBlocked = qa => !!qa && qa.status === 'BLOCKER' && qa.blocker_kind === 'environment'
const notRunItem = (cmd, how) => ({ type: 'needs validation', source: 'qa', wp: null, file: null,
  kind: 'qa not run', problem: cmd, claim: cmd, fact: 'not run: the environment refused it', how_to_check: how })
const qaNotRunItems = qa => {
  if (!qaEnvironmentBlocked(qa)) return []
  const cmds = [...new Set((Array.isArray(qa.not_run) ? qa.not_run : []).filter(c => typeof c === 'string'))]
  return cmds.length ? cmds.map(c => notRunItem(c, `run ${c}`))
    : [notRunItem('QA\'s validation', 'allow QA\'s test commands, then run /implement again')]
}
// Each agent reads the slice of architecture.md that `agentic.py briefs` wrote for it, not the whole document.
const brief = id => `.claude/state/briefs/${id}.md`
// The writers' prompts carry no rules (they end their brief); this sentence asks for an acceptance repair.
const ACCEPTANCE_BLOCKER = ' If your package is done and the acceptance tests themselves fail (an error inside a test or ' +
  'its doubles, not in your code), return BLOCKED with blocker.in "acceptance" and, in missing, each failing test, ' +
  'its file and its error.'
const REFUTE_CONFIDENCE = 0.7
// verify and baseline run the whole suite: each goes in a gatekeeper call of its own, so it fits in one Bash timeout.
const WAVE_TAIL = ['verify', 'open-stage review', 'callers']
const HOOKS_DEAD = 'the guardrail hook is not running in this session: run `agentic.py hooks-check` for the cause, or use /implement fallback'
const LOST = 'the gatekeeper returned no usable result (a timeout or garbled output); the command may have run anyway: check `agentic.py status` before resuming'
// Files close-stage saw change for an ID ("WP-01: 2 file(s) changed: a, b"), to compare an agent's claims with disk.
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
const run = { result: 'REVIEW', stages: [], baseline: null, work_packages: {}, verification: null, review: null, qa: null, test_audit: null, docs: null, fix_cycles: 0, pending: [], not_checked: [], gate_denials: {}, verification_failures: [], agent_calls: 0, environment_retries: [], qa_passes: [], acceptance_repairs: [] }
// Keeps each report's not_checked per call; passes `model` only when set (escalation); counts every call, failed too.
const report = async (prompt, opts) => {
  const { model, ...rest } = opts
  run.agent_calls += 1
  const r = await safe(agent(prompt, model ? opts : rest), opts.label)
  if (r && Array.isArray(r.not_checked) && r.not_checked.length) run.not_checked.push({ agent: opts.agentType, label: opts.label, items: r.not_checked })
  return r
}
// A needs-validation item goes to pending with its declared keys only: a severity an agent adds must never block.
const needsItem = (source, i) => ({ type: 'needs validation', source, wp: i.wp, file: i.file, ...(Number.isInteger(i.line) ? { line: i.line } : {}), kind: i.kind, claim: i.claim, fact: i.fact, how_to_check: i.how_to_check })
let needsReview = []  // the last review's needs-validation items
const concernPending = []  // DONE_WITH_CONCERNS doubts, kept across cycles and handed to every review
const contextGiven = new Set()  // packages already re-dispatched once with the whole document
let repaired = false  // the acceptance repair runs once per run (design 0032)
// A pending item for a DONE_WITH_CONCERNS doubt: no severity, so it never blocks; the reviewer weighs it.
const concernItem = (wp, text) => ({ type: 'concern', source: wp, wp, problem: text })
// Each check the implementer could not run (passed false, detail "not run: ..."): a needs-validation item, no blocker.
const notRun = c => c && c.passed === false && typeof c.command === 'string' && typeof c.detail === 'string' &&
  /^\s*not run:/i.test(c.detail)
const untestedItem = (wp, c) => ({ type: 'needs validation', source: wp, wp, file: null, kind: 'tests not run',
  problem: c.command, claim: c.command, fact: c.detail, how_to_check: `run ${c.command}` })
const untestedItems = (wp, rep) =>
  (rep && Array.isArray(rep.checks) ? rep.checks : []).filter(notRun).map(c => untestedItem(wp, c))
// Close-stage marks: DONE and DONE_WITH_CONCERNS are done, no report is error, anything else is a blocker.
const DONE_STATUSES = new Set(['DONE', 'DONE_WITH_CONCERNS'])
// In a fix wave (counts given) each mark carries the attempt in this run, @N: it names the diff the next attempt reads.
const waveStates = (wpIds, reports, counts) => wpIds.map((wp, i) => `${wp}=${!reports[i] ? 'error' : DONE_STATUSES.has(reports[i].status) ? 'done' : 'blocker'}${counts ? `@${(counts[wp] || 0) + 1}` : ''}`)
// The stop gate's denials in a close-stage output ("GATE: WP-01=1 WP-02=0"), per package; {} if absent.
const gateLine = out => {
  const m = String(out || '').match(/^GATE: (.*)$/m)
  const found = {}
  if (m) for (const [, wp, n] of m[1].matchAll(/(WP-\d+|AT|QA)=(\d+)/g)) found[wp] = parseInt(n, 10)
  return found
}
const addGateDenials = out => {
  for (const [id, n] of Object.entries(gateLine(out))) if (n) run.gate_denials[id] = (run.gate_denials[id] || 0) + n
}
// seq, adding the stop gate's denials its first step (a close-stage) reports to the run's record.
const closeSeq = async (cmds, label) => {
  const res = await seq(cmds, label)
  addGateDenials((step(res, 0) || {}).output)
  return res
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
const newPending = []  // what a re-review found that was not open: listed, never fixed in this run
const pendingNow = () => [...qaPending, ...limitPending, ...concernPending, ...newPending]
// The review after the first full one is scoped: the confirmed findings of the packages fixed since then.
const AXES = ['spec', 'standards']
let reviewed = false
let fixedSinceReview = new Set()
let fixCountAtReview = {}
// QA after its first pass re-checks the packages fixed since, unless only Standards findings were fixed.
let fixedSinceQa = new Set(), fixCountAtQa = {}, sourcesSinceQa = new Set()
let lastConfirmed = { spec: [], standards: [] }
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

// A failing phase's lines for a fix: its new failures, else its failing lines, else the end of its output.
const failingLines = p => [p.new_failures, p.failing_lines].find(x => Array.isArray(x) && x.length) || p.tail || []
// A verification failure becomes defects of the packages that own what it mentions (agentic.py attributes them).
const verificationDefects = v => {
  const suspects = (v && v.suspects) || {}
  const failing = ((v && v.phases) || []).filter(p => p.status === 'regression' || p.status === 'fails')
  const evidence = failing.map(p => `${p.name}: ${failingLines(p).join('\n')}`).join('\n').slice(0, 1500)
  return Object.entries(suspects).filter(([wp]) => isWp(wp)).map(([wp, info]) => ({
    source: 'verification', severity: 'high', wp, file: (info.files || [])[0] || '',
    problem: `verification fails (${(info.phases || []).join(', ')})${info.acs && info.acs.length ? ` on ${info.acs.join(', ')}` : ''}`, evidence,
  }))
}

// Living documentation, only with accepted code: codemap (deterministic), the doc-updater in its zone, docs-check.
// A documentation problem becomes pending unless a write left its zone. `steps`: [codemap, open-stage docs] results.
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
  const doc = await report('Update the documentation according to architecture.md and the integrated code.', { agentType: 'doc-updater', label: 'doc-updater', schema: DOCS_SCHEMA })
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

// Runs one wave's agents (fixers when defectsByWp is given) in its open stage; the caller closes it with what follows.
async function launchWave(wave, wpIds, defectsByWp) {
  const reports = await parallel(wpIds.map(wp => () => {
    let prompt = `Implement ${wp}. Your brief is ${brief(wp)}: the parts of architecture.md your package needs.` +
      ACCEPTANCE_BLOCKER
    if (defectsByWp) {
      const attempt = (fixCount[wp] || 0) + 1
      prompt = `Fix ${wp}. Your brief is ${brief(wp)}. The code already exists. These are the defects the judges (reviewer, QA and verification) attribute to your package, as JSON:\n` +
        `${JSON.stringify(defectsByWp[wp])}\nStay within the contract, do not touch other packages' files or the acceptance tests, and keep the acceptance tests of your criteria green. A defect in a file no package owns (for example, a caller your change broke) is fixed from your own files: keep that caller working with a compatible change, such as a default for a new parameter or a wrapper that keeps the old signature, unless section 6 explicitly forbids it. Return BLOCKED only when no compatible change exists in your files.` + ACCEPTANCE_BLOCKER
      if (attempt > 1) {
        prompt += `\nThis is attempt ${attempt} of ${plan.max_fix_cycles}: the previous one did not fix it. The defects it was given:\n${JSON.stringify((fixHistory[wp] || []).slice(-2, -1)[0])}\n` +
          `What it changed is in .claude/state/runs/${wp}.fix-${attempt - 1}.diff: read it first and do not repeat that approach. ` +
          'If you try something different, say in internal_decisions how it differs and why it should work.'
      }
    }
    // The last allowed fix attempt (2 or later) runs on the escalation model; the prompt is the same.
    const attempt = (fixCount[wp] || 0) + 1
    const last = defectsByWp && attempt >= 2 && attempt === plan.max_fix_cycles
    const model = last && plan.escalation_model ? plan.escalation_model : null
    const label = defectsByWp ? `${wp} (fix)` : wp
    const agentType = plan.agents[wp] || 'implementer'
    return report(prompt, { agentType, label, schema: REPORT_SCHEMA, model })
  }))
  const withContext = await withWholeDocument(wpIds, reports, !!defectsByWp)
  wpIds.forEach((wp, i) => {
    const rep = withContext[i]
    run.work_packages[wp] = rep
    if (rep && rep.status === 'DONE_WITH_CONCERNS') for (const c of rep.concerns || []) if (typeof c === 'string') concernPending.push(concernItem(wp, c))
    for (const item of untestedItems(wp, rep)) {
      if (!concernPending.some(p => pendingKey(p) === pendingKey(item))) concernPending.push(item)
    }
  })
  return { reports: withContext, closeCmd: `close-stage ${wave} ${waveStates(wpIds, withContext, defectsByWp ? fixCount : null).join(' ')}${defectsByWp ? ' --fix' : ''}` }
}

// NEEDS_CONTEXT in an implement wave: one more call with the whole document, once, replacing the report; not in fixes.
async function withWholeDocument(wpIds, reports, fixing) {
  const again = []
  wpIds.forEach((wp, i) => {
    const rep = reports[i]
    if (!rep || rep.status !== 'NEEDS_CONTEXT' || fixing || contextGiven.has(wp)) return
    const missing = missingContext(rep)
    if (missing === null) return
    contextGiven.add(wp)
    const prompt = `Implement ${wp}. Your first attempt returned NEEDS_CONTEXT: ${missing}. This time your brief is ` +
      `architecture.md, whole: read it and implement. The rules you must follow are at the end of ${brief(wp)}. ` +
      'If the document does not settle it, return BLOCKED with the section that should.'
    again.push({ i, run: () => report(prompt, { agentType: plan.agents[wp] || 'implementer', label: `${wp} (context)`, schema: REPORT_SCHEMA }) })
  })
  if (!again.length) return reports
  const second = await parallel(again.map(a => a.run))
  const out = [...reports]
  again.forEach((a, k) => { out[a.i] = second[k] })
  return out
}

// Runs waves in order; each close-stage travels with the next open-stage, the last with nothing (verify and open-stage
// review follow alone). `opened`: the first wave is open. Returns { post: [verify, open-stage review] } or { stopped }.
async function runWaves(waves, byWave, defectsByWp, opened) {
  const fixing = !!defectsByWp
  if (!waves.length) {
    const t = await seq(WAVE_TAIL, 'verify')
    return { post: (t && t.steps) || [] }
  }
  for (let i = 0; i < waves.length; i += 1) {
    const wave = waves[i]
    if (!opened) {
      const open = await seqRetry([`open-stage ${wave}`], `open-stage ${wave}`)
      if (!ok(step(open, 0))) return { stopped: stop(`${fixing ? 'fix ' : ''}wave ${wave}`, 'the stage could not be opened') }
    }
    const { closeCmd } = await launchWave(wave, byWave[wave], defectsByWp)
    const last = i === waves.length - 1
    const res = await seq([closeCmd, ...(last ? [] : [`open-stage ${waves[i + 1]}`])], `close-stage ${wave}`)
    const close = step(res, 0)
    if (!close) return { stopped: stop(fixing ? `fix wave ${wave}` : `wave ${wave}`, LOST) }
    let good = ok(close)
    run.stages.push({ stage: fixing ? `wave ${wave} (fix)` : `wave ${wave}`, ok: good, check: close && close.output })
    addGateDenials(close.output)
    let again = false
    if (!good && !fixing) {
      const out = await repairAcceptance(wave, byWave[wave], close.output)
      if (out && out.stopped) return out
      again = good = !!out
    }
    if (!good) {
      return { stopped: stop(fixing ? `fix wave ${wave}` : `wave ${wave}`, fixing
        ? 'the fix ended with blockers or writes outside ownership'
        : 'the wave ended with blockers, missing reports or writes outside ownership') }
    }
    if (last) {
      const t = await seq(WAVE_TAIL, 'verify')
      return { post: (t && t.steps) || [] }
    }
    if (again) { opened = false; continue }  // the failed close carried the next open-stage: it did not run
    if (!ok(step(res, 1))) return { stopped: stop(`wave ${waves[i + 1]}`, 'the stage could not be opened') }
    opened = true
  }
}

// The packages BLOCKED only by the acceptance tests, sorted; [] unless every other report is done.
const acceptanceBlocked = (wpIds, reports) => {
  const blocked = wpIds.filter((wp, i) => reports[i] && reports[i].status === 'BLOCKED' && reports[i].blocker &&
    typeof reports[i].blocker === 'object' && reports[i].blocker.in === 'acceptance')
  const done = wpIds.every((wp, i) => blocked.includes(wp) || (reports[i] && DONE_STATUSES.has(reports[i].status)))
  return blocked.length && done ? [...blocked].sort() : []
}
// Whether the budget pays for the repair and the rest of the run, counted as budgetAtStart counts it.
const fitsRepair = (wave, blocked) => {
  const most = plan.max_agent_calls
  if (most === null || most === undefined) return true
  const later = Object.entries(plan.work_packages).filter(([w]) => Number(w) > Number(wave))
    .reduce((n, [, ids]) => n + ids.length, 0)
  return 1 + blocked.length + later + AXES.length + 1 + (plan.docs ? 1 : 0) <= most - run.agent_calls
}
const NOT_REPAIRED = 'the test-author could not repair the acceptance tests'
const WAVE_STOPPED = 'the wave ended with blockers, missing reports or writes outside ownership'
// A wave blocked only by the acceptance tests, once per run: the test-author repairs them, then the blocked packages
// run again. null when it does not apply; { stopped } or { ok } otherwise.
async function repairAcceptance(wave, wpIds, output) {
  const blocked = acceptanceBlocked(wpIds, wpIds.map(wp => run.work_packages[wp]))
  const strays = String(output || '').includes('OUTSIDE OWNERSHIP')
  if (repaired || !blocked.length || strays || !fitsRepair(wave, blocked)) return null
  repaired = true
  const where = `wave ${wave}`
  const open = step(await seqRetry(['open-stage acceptance'], 'open-stage acceptance'), 0)
  if (!ok(open)) return { stopped: stop(where, 'the acceptance stage could not be reopened to repair the tests') }
  const blockers = blocked.map(wp => ({ wp, missing: run.work_packages[wp].blocker.missing ?? null }))
  const at = await report(`Repair the acceptance tests (your brief is ${brief('AT')}). The code of wave ${wave} is ` +
    'written, and these packages could not finish because the acceptance tests themselves fail, as JSON:\n' +
    `${JSON.stringify(blockers)}\nFix the tests or their doubles in your zone so that they test what the criteria ` +
    'say. Do not weaken what a criterion checks and do not touch production code. Run the acceptance command ' +
    'before you report.',
  { agentType: 'test-author', label: 'test-author (repair)', schema: AT_SCHEMA })
  const status = at ? at.status : null
  const cmd = `close-stage acceptance AT=${status === 'DONE' ? 'done' : 'blocker'}`
  const close = step(await closeSeq([cmd], 'close-stage acceptance'), 0)
  run.stages.push({ stage: `acceptance repair (wave ${wave})`, ok: ok(close), check: close && close.output })
  run.acceptance_repairs.push({ wave, wps: blocked, test_author: status })
  if (status !== 'DONE' || !ok(close)) return { stopped: stop(where, NOT_REPAIRED) }
  const reopen = step(await seqRetry([`open-stage ${wave}`], `open-stage ${wave}`), 0)
  if (!ok(reopen)) return { stopped: stop(where, 'the stage could not be opened') }
  const { closeCmd } = await launchWave(wave, blocked, null)
  const again = step(await seq([closeCmd], `close-stage ${wave}`), 0)
  if (!again) return { stopped: stop(where, LOST) }
  run.stages.push({ stage: `wave ${wave} (after repair)`, ok: ok(again), check: again.output })
  addGateDenials(again.output)
  if (!ok(again)) return { stopped: stop(where, WAVE_STOPPED) }
  return { ok: true }
}

// One fix cycle for the given defects (already filtered by the per-package limit). `opened`: the first fix wave is open.
async function fixCycle(defects, opened) {
  const short = budgetForCycle(defects)
  if (short) return { stopped: short }
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
  for (const wp of Object.keys(by)) { fixedSinceReview.add(wp); fixedSinceQa.add(wp) }
  for (const d of defects) sourcesSinceQa.add(d.source)
  return out
}

// ---------- the budget, in model agent calls ----------
// A stop when the budget cannot pay for wave 0, every package once, both reviewers, QA and the docs.
const budgetAtStart = () => {
  const most = plan.max_agent_calls
  if (most === null || most === undefined) return null
  const wave0 = startWave === null && plan.acceptance && plan.type !== 'refactor' ? 2 : 0
  const packages = Object.entries(plan.work_packages).filter(([w]) => startWave === null || Number(w) >= startWave)
    .reduce((n, [, wps]) => n + wps.length, 0)
  const minimum = wave0 + packages + 2 + 1 + (plan.docs ? 1 : 0)
  if (minimum <= most) return null
  return stop('budget', `the budget of ${most} agent calls cannot fund the run: it needs at least ${minimum}`)
}
// A stop, with the cycle's defects in pending, when the cycle and the review and QA after it do not fit.
const budgetForCycle = defects => {
  const most = plan.max_agent_calls
  if (most === null || most === undefined) return null
  const packages = Object.keys(groupByWp(defects))
  const scope = new Set([...packages, ...fixedSinceReview])
  const reviewers = !reviewed ? AXES.length : AXES.filter(axis => lastConfirmed[axis].some(f => scope.has(f.wp))).length
  const needed = packages.length + reviewers + 1 + (plan.docs ? 1 : 0)
  const left = most - run.agent_calls
  if (needed <= left) return null
  run.pending = [...run.pending, ...defects.map(d => ({ type: 'over budget', ...d }))]
  return stop('budget', `the budget of ${most} agent calls cannot fund another fix cycle and the review and QA ` +
    `after it: ${needed} needed, ${left} left`)
}

// ---------- Prepare + Baseline ----------
// The baseline (build, types, lint, tests before anything changes) makes a phase that already failed a pre-existing
// failure, blamed on no package. A resumed run (start_wave) keeps the original baseline.
phase('Prepare')
// hooks-check goes first: a session whose guardrail hook does not fire must not pay for a single agent.
const first = await seqRetry(['hooks-check', 'pipeline-plan', 'briefs'], 'prepare')
const hookStep = step(first, 0)
if (hookStep && !ok(hookStep)) return stop('prepare', HOOKS_DEAD, { gate: hookStep })
const planStep = step(first, 1)
const plan = ok(planStep) ? lastJson(planStep.output) : null
// Only a pipeline-plan that ran and refused the manifest says the manifest is invalid; a lost or empty reply does not.
if (!first) return stop('prepare', LOST)
if (!plan) return stop('prepare', planStep && !ok(planStep) ? 'pipeline-plan failed: the manifest is not valid'
  : 'pipeline-plan produced no readable output', { gate: planStep })
// The rules reach every agent that writes or judges code: printed when the checksum holds, else as their paths.
const rulesIntact = typeof plan.rules_text === 'string' && plan.rules_text !== '' &&
  textSum(plan.rules_text) === plan.rules_sum
const RULES = rulesIntact ? plan.rules_text
  : plan.rules && plan.rules.length ? ` Rules you must follow (language and common): ${plan.rules.join(', ')}.` : ''
run.rules = rulesIntact ? 'printed' : RULES ? 'pointed' : 'none'
const WAIVERS = Array.isArray(plan.waivers) ? plan.waivers : []
const REVIEW_SOURCES = { spec: 'reviewer', standards: 'standards-reviewer' }
// Both reviewers in parallel, one verifier per finding that blocks, the record per axis (never reranked).
const fullReview = async post => {
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
    () => report('Review the integrated code according to architecture.md.' + callers + concerns, { agentType: 'reviewer', label: 'reviewer', schema: REVIEW_SCHEMA }),
    () => report('Review the integrated code against section 7 of architecture.md and the rules below. Report only breaches of a written rule: name it in "rule".' + RULES, { agentType: 'standards-reviewer', label: 'standards-reviewer', schema: STANDARDS_SCHEMA }),
  ])
  if (!specR) return { stopped: stop('review', 'the reviewer returned no report') }
  if (!stdR) return { stopped: stop('review', 'the standards reviewer returned no report') }
  const marked = withFingerprints([...specR.findings.map(f => ({ ...f, axis: 'spec' })),
    ...stdR.findings.map(f => ({ ...f, axis: 'standards' }))])
  const axes = { spec: marked.slice(0, specR.findings.length), standards: marked.slice(specR.findings.length) }
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
      confirmed: splitByOwner(mine.filter(f => !refuted.includes(f))),
      refuted: mine.filter(f => refuted.includes(f)),
      advisory: axes[axis].filter(f => !graves.includes(f) && !waiverFor(f, WAIVERS)),
    }
  }
  record.standards.waived = waived
  const union = k => [...record.spec[k], ...record.standards[k]]
  run.review = {
    scoped: false, ...record, confirmed: union('confirmed'), refuted: union('refuted'), advisory: union('advisory'),
    waived,
    opened: ok(post[1]), callers: callerItems.length,
  }
  needsReview = (specR.needs_validation || []).map(i => needsItem('reviewer', i))
  return closeReview({ spec: record.spec.confirmed, standards: record.standards.confirmed })
}
// Each axis re-reviews its open findings of the packages fixed since, against the fixes' diffs, by fingerprint, with no
// verifiers. A missing verdict is NOT_ADDRESSED; anything new goes to pending and never starts a fix cycle.
const AXIS_NAMES = { spec: 'Spec', standards: 'Standards' }
const NO_RE_REVIEW = {
  spec: 'the reviewer returned no re-review report',
  standards: 'the standards reviewer returned no re-review report',
}
const reReviewStage = async post => {
  phase('Review')
  const scope = fixedSinceReview
  const open = {}
  for (const axis of AXES) open[axis] = lastConfirmed[axis].filter(f => scope.has(f.wp))
  const diffs = fixDiffs([...scope].sort(), fixCountAtReview)
  const asked = AXES.filter(axis => open[axis].length)
  const answers = await parallel(asked.map(axis => () => report(
    `Re-review the fix, on the ${AXIS_NAMES[axis]} axis only. ` +
    'These findings were confirmed before the fix, each with its fingerprint, as JSON:\n' +
    `${JSON.stringify(open[axis])}\n` +
    `The fix's diffs are ${diffs}: read them and the code they changed. ` +
    'For each fingerprint say ADDRESSED or NOT_ADDRESSED, with the line that shows it. ' +
    'A finding with a "scope" is judged only in those files: ' +
    'what its evidence says about other files is another package\'s finding. ' +
    'Report anything new in new_findings: it is recorded and does not block this run.' + (axis === 'standards' ? RULES : ''),
    { agentType: REVIEW_SOURCES[axis], label: `${REVIEW_SOURCES[axis]} (re-review)`, schema: RE_REVIEW_SCHEMA })))
  const reports = {}
  asked.forEach((axis, i) => { reports[axis] = answers[i] })
  for (const axis of asked) if (!reports[axis]) return { stopped: stop('review', NO_RE_REVIEW[axis]) }
  const record = {}
  const found = []
  for (const axis of AXES) {
    const rep = reports[axis] || {}
    const said = {}
    for (const v of rep.verdicts || []) if (v && typeof v === 'object') said[v.fingerprint] = v.verdict
    const addressed = open[axis].filter(f => said[f.fingerprint] === 'ADDRESSED')
    const confirmed = lastConfirmed[axis].filter(f => !addressed.includes(f))
    record[axis] = { confirmed, refuted: [], advisory: [], addressed }
    for (const f of rep.new_findings || []) if (f && typeof f === 'object') found.push({ ...f, axis })
  }
  record.standards.waived = []
  for (const f of found) {
    const item = { type: 'new finding', source: REVIEW_SOURCES[f.axis], ...f }
    if (!newPending.some(p => pendingKey(p) === pendingKey(item))) newPending.push(item)
  }
  const union = k => [...record.spec[k], ...record.standards[k]]
  run.review = {
    scoped: true, ...record, confirmed: union('confirmed'), refuted: [], advisory: [], waived: [], new: found,
    opened: ok(post[1]), callers: 0,
  }
  return closeReview({ spec: record.spec.confirmed, standards: record.standards.confirmed })
}
// The next review starts from these findings, no package fixed since; a change at close means someone wrote.
const closeReview = async byAxis => {
  const confirmed = [...byAxis.spec, ...byAxis.standards]
  reviewed = true
  lastConfirmed = byAxis
  fixedSinceReview = new Set()
  fixCountAtReview = { ...fixCount }
  const close = `close-stage review REVIEW=${confirmed.length ? 'fail' : 'pass'}`
  const afterReview = await seq([close, 'open-stage qa'], 'close-stage review')
  if (!ok(step(afterReview, 0))) {
    return { stopped: stop('review', 'files changed during the review stage (the reviewer must not write)') }
  }
  return { confirmed }
}
// The diffs of every fix of these packages since the count in `countAt`.
const fixDiffs = (wps, countAt) => wps.flatMap(wp => {
  const out = []
  for (let n = (countAt[wp] || 0) + 1; n <= (fixCount[wp] || 0); n += 1) {
    out.push(`.claude/state/runs/${wp}.fix-${n}.diff`)
  }
  return out
}).join(', ')
// QA's pass: whole the first time. After fixes, a re-check of the packages fixed since its last pass; or no call at
// all, its report standing, when every defect fixed since came from the standards reviewer. Null when QA gave none.
const qaStage = async () => {
  phase('QA')
  const onlyStandards = sourcesSinceQa.size === 1 && sourcesSinceQa.has('standards-reviewer')
  // A report the environment blocked validated nothing: it is never kept, and never re-checked in part.
  const full = !run.qa || sourcesSinceQa.size === 0 || qaEnvironmentBlocked(run.qa)
  const mode = full ? 'full' : onlyStandards ? 'kept' : 'scoped'
  const scope = [...fixedSinceQa].sort()
  const prompt = mode === 'full' ? 'Run the system\'s QA validation according to architecture.md.'
    : 'Re-check the system after a fix, according to architecture.md. ' +
      `Packages fixed since your last pass: ${scope.join(', ')}. ` +
      `The fixes' diffs are ${fixDiffs(scope, fixCountAtQa)}: read them and the code they changed. ` +
      `Your last report listed these defects, as JSON:\n${JSON.stringify(run.qa.defects)}\n` +
      'Run the tests in your zone and the full suite again, and add tests only for what the diffs changed. ' +
      'Report every defect that is still open, from that list or new, with the same fields; ' +
      'leave out the ones the fix closed. Do not repeat the rest of your first pass.'
  if (mode !== 'kept') {
    const label = mode === 'full' ? 'qa' : 'qa (re-check)'
    const qa = await report(prompt + RULES, { agentType: 'qa', label, schema: QA_SCHEMA })
    if (!qa) return null
    run.qa = qa
    fixedSinceQa = new Set()
    fixCountAtQa = { ...fixCount }
    sourcesSinceQa = new Set()
  }
  run.qa_passes.push({ mode, status: run.qa.status, defects: run.qa.defects.length })
  return run.qa
}

const reviewStage = post => (reviewed ? reReviewStage(post) : fullReview(post))
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
    const open = step(await seqRetry(['open-stage acceptance'], 'open-stage acceptance'), 0)
    if (!ok(open)) return stop('wave 0', 'the acceptance stage could not be opened', { gate: open })
    let prompt = `Write the acceptance tests. Your brief is ${brief('AT')}: the parts of architecture.md they need.`
    for (let round = 0; ; round += 1) {
      const at = await report(prompt, { agentType: 'test-author', label: round ? 'test-author (revision)' : 'test-author', schema: AT_SCHEMA })
      const state = at && at.status === 'DONE' ? 'done' : 'blocker'
      const close = step(await closeSeq([`close-stage acceptance AT=${state} --expect red`], 'close-stage acceptance'), 0)
      run.stages.push({ stage: round ? 'wave 0 (revision)' : 'wave 0', ok: ok(close), test_author: at, check: close && close.output })
      if (!ok(close)) {
        return stop('wave 0', at && at.status === 'BLOCKER'
          ? 'the test-author cannot test some criterion: architecture.md needs review'
          : 'acceptance did not end up red for the right reason (or files outside its zone were touched)', { test_author: at })
      }
      if (!at || at.status !== 'DONE') return stop('wave 0', 'the test-author returned no usable report', { test_author: at })
      // Red is not enough: a test can fail only because the code does not exist yet and still pass an empty stub.
      const audit = await report(`Audit the acceptance tests against the brief they were written from, ${brief('AT')}. The test-author reports this coverage, as JSON:\n${JSON.stringify(at.coverage)}`,
        { agentType: 'test-auditor', label: round ? 'test-auditor (revision)' : 'test-auditor', schema: AUDIT_SCHEMA })
      if (!audit) return stop('wave 0', 'the test-auditor returned no report')
      const weak = audit.findings.filter(f => blocks(f))
      run.test_audit = { rounds: round + 1, weak, advisory: audit.findings.filter(f => !blocks(f)) }
      if (!weak.length) break
      if (round >= 1) return stop('wave 0', 'the acceptance tests are still weak after one revision: architecture.md or the criteria need review', { test_audit: run.test_audit })
      const reopen = step(await seqRetry(['open-stage acceptance'], 'open-stage acceptance'), 0)
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
    run.pending = pendingNow()
    if (!eligible.length) return run
    const fixed = await fixCycle(eligible, false)
    if (fixed.stopped) return fixed.stopped
    post = fixed.post
    continue
  }

  const reviewed = await reviewStage(post)
  if (reviewed.stopped) return reviewed.stopped
  const confirmed = reviewed.confirmed

  const qa = await qaStage()
  if (!qa) return stop('qa', 'QA returned no report')

  const implDefects = [
    ...confirmed.map(f => ({ source: REVIEW_SOURCES[f.axis], ...f })),
    ...qa.defects.filter(d => d.kind === 'implementation' && blocks(d)).map(d => ({ source: 'qa', ...d })),
  ].filter(d => isWp(d.wp))
  const designDefects = qa.defects.filter(d => d.kind === 'design')
  const unassigned = [...confirmed, ...qa.defects.filter(d => d.kind === 'implementation' && blocks(d))]
    .filter(d => !isWp(d.wp))
  const baseline = [...designDefects.map(d => ({ type: 'design', ...d })), ...unassigned.map(d => ({ type: 'no WP', ...d })),
    ...needsReview, ...(qa.needs_validation || []).map(i => needsItem('qa', i)), ...qaNotRunItems(qa)]
  const closeQa = `close-stage qa QA=${qa.status === 'PASS' ? 'pass' : 'fail'}`

  const eligible = implDefects.length ? splitByLimit(implDefects) : []
  qaPending = baseline
  run.pending = pendingNow()
  if (eligible.length === 0) {
    if (qaEnvironmentBlocked(qa)) run.detail = QA_NOT_RUN_DETAIL
    run.result = implDefects.length || qa.status === 'BLOCKER' || run.pending.some(p => blocks(p)) ? 'REVIEW' : 'OK'
    const docs = run.result === 'OK' && plan.docs
    const fin = await closeSeq([closeQa, ...(docs ? [...(plan.codemap ? ['codemap'] : []), 'open-stage docs'] : [])], 'close-stage qa')
    if (!ok(step(fin, 0))) return stop('qa', 'files changed during the QA stage outside its zone')
    if (docs) {
      await documentStage(fin.steps.slice(1))
      if (run.pending.some(p => blocks(p))) run.result = 'REVIEW'
    }
    return run
  }

  const firstFixWave = wavesOf(groupByWp(eligible))[0]
  const fin = await closeSeq([closeQa, `open-stage ${firstFixWave}`], 'close-stage qa')
  if (!ok(step(fin, 0))) return stop('qa', 'files changed during the QA stage outside its zone')
  if (!ok(step(fin, 1))) return stop(`fix wave ${firstFixWave}`, 'the stage could not be opened')
  const fixed = await fixCycle(eligible, true)
  if (fixed.stopped) return fixed.stopped
  post = fixed.post
}
}
return budgetAtStart() || await runAfterPlan()
