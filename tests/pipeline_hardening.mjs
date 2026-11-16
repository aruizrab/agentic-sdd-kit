// Safety defaults of tools/sdd/pipeline/feature-pipeline.js (sync 2026-11-16): arg whitelist and types, role fallback off, hook
// probe, script-judged gate results, two-vote refutation, script-parsed persisted findings, checked Finalize evidence, the
// security rule from configured paths, the testsSha check, red-check passes, the heavy-job lock rule, the setup scope and the
// high-complexity implementer effort. A stub runtime answers each call from its label (like tests/pipeline_smoke.mjs);
// tests/pipeline_cachekeys.mjs covers legacy: true (byte-identity with the earlier script).
//
// Run: node tests/pipeline_hardening.mjs
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import assert from 'node:assert/strict'

const here = path.dirname(fileURLToPath(import.meta.url))
const src = readFileSync(path.join(here, '..', 'tools', 'sdd', 'pipeline', 'feature-pipeline.js'), 'utf8')
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const script = new AsyncFunction('args', 'agent', 'parallel', 'pipeline', 'phase', 'log', src.replace(/^export const meta =/m, 'const meta ='))

const HEAD = 'a'.repeat(40)
const OLD = 'b'.repeat(40)
const BLOCKED = 'BLOCKED by SDD hook: .claude/.hook-probe is protected (agent configuration is human-owned).'
const CONFIG = {
  root: '/proj',
  project: { name: 'demo', description: 'demo project', threat_model: 'tm' },
  paths: { features_dir: 'specs/features', templates_dir: 'specs/templates', roadmap: 'docs/ROADMAP.md', constitution: 'docs/CONSTITUTION.md', development: 'docs/DEVELOPMENT.md', logs_dir: '.sdd/logs', worktree_root: '.wt', tools_dir: 'tools/sdd' },
  features: { id_prefix: 'F', id_digits: 3, branch_prefix: 'feat/' },
  requirements: { budget: { min: 15, max: 45 }, clarification_marker: '[NEEDS CLARIFICATION:' },
  components: { word: 'package' },
  tests: { run: 'npm test', run_component: 'npm test -w {component}' },
  sources: { kind: 'api-contract', citation_format: '(Source: openapi.yaml#/paths/x)', lookup_command: 'sdd source' },
  models: { mechanical: 'm-mech', mid: { id: 'm-mid', effort: 'medium' }, strong: 'm-strong', top: { id: 'm-top', effort: 'high' } },
  pipeline: { max_critic_fix_rounds: 2, max_gate_fix_rounds: 3, skeptics: 2, max_build_jobs: 4, gate_poll_seconds: 5, security_paths: ['src/auth/**', '**/crypto/*.ts'] },
  commit: { trailers: ['Co-Authored-By: X <x@example.com>'] },
  merge: { union_manifests: ['package.json'] },
  gates: { fast: ['lint'], full: ['lint', 'test'] },
  heavy_lock: true,
  test_globs: ['tests/**', '**/tests/**', '**/*.test.ts'],
}
const REVIEWERS = ['spec-compliance-reviewer', 'security-reviewer', 'test-adequacy-reviewer', 'perf-reviewer']
const vote = (refuted) => ({ refuted, confidence: 'high', reasoning: 'r' })
const block = (rec) => `<!-- pipeline-findings: record -->\n\`\`\`json\n${JSON.stringify(rec)}\n\`\`\`\n<!-- /pipeline-findings -->`
const GOOD_BLOCK = block({ reviewers: REVIEWERS, securitySensitive: false, findings: [
  { id: 'R1', reviewer: 'spec-compliance-reviewer', severity: 'blocking', claim: 'c', outcome: 'refuted', votes: [vote(true), vote(true)] },
  { id: 'R2', reviewer: 'perf-reviewer', severity: 'blocking', claim: 'c', outcome: 'confirmed, fixed', votes: [vote(false), vote(false)] },
  { id: 'R3', reviewer: 'perf-reviewer', severity: 'non-blocking', claim: 'c', outcome: 'non-blocking', votes: [] },
] })
const GATE_OK = { pass: true, infraError: false, failed: [], exitLine: 'GATES_EXIT=0 n0nce', logHead: `GATES_HEAD=${HEAD}`, headSha: HEAD, logPath: '.sdd/logs/gates/x.log' }
const task = (id, extra) => Object.assign({ id, title: id, components: ['a'], files: [`a/${id}.ts`], verifies: ['REQ-A-R010'], depends: [], parallel: false, complexity: 'medium', risk: 'none' }, extra)
const PLAN = { ok: true, securitySensitive: false, waves: [
  { wave: 1, tasks: [task('T01', { complexity: 'high' }), task('T02', { complexity: 'low' })] },
  { wave: 2, tasks: [task('T03', { components: ['b'], files: ['b/x.ts'], parallel: true, complexity: 'high' }), task('T04', { components: ['c'], files: ['c/y.ts'], parallel: true })] },
] }

function answer(label, prompt) {
  if (label === 'setup') return { ok: true, branch: 'feat/F011-x', baseSha: 'base', headSha: HEAD, scaffolded: true, hookProbe: BLOCKED }
  if (label.startsWith('commit: ')) return { ok: true, sha: 'c' + label.length }
  if (label === 'tests-commit check') return { changed: ['a/tests/t.test.ts'], testFiles: ['a/tests/t.test.ts'] }
  if (label === 'testsSha check') {
    const shas = JSON.parse(/For each commit sha in (\[[^\]]*\])/.exec(prompt)[1])
    return { commits: shas.map((sha) => ({ sha, full: sha.padEnd(40, '0'), ancestorExit: 0, files: ['a/src/x.ts', 'a/tests/t.test.ts'], subject: 'test(F011): failing tests' })) }
  }
  if (label === 'spec status') return { status: 'Approved', needsClarification: 0, requirements: 20 }
  if (/^architect-planner|^load tasks/.test(label)) return PLAN
  if (/^test-author F/.test(label)) return { ok: true, components: ['a'], testsAdded: ['a/tests/t.test.ts'], summary: 'red' }
  if (/^red-check/.test(label)) return { compiles: true, failingTests: 3, unexpectedPasses: [], passingVerifies: [], mustWithoutTest: [] }
  if (/^T0\d impl/.test(label)) return { ok: true, tasksDone: [label.slice(0, 3)], tasksFailed: [], summary: 'ok' }
  if (/^wave \d lane \d/.test(label)) return { ok: true, tasksDone: [], tasksFailed: [], headSha: 'lane', branch: 'wip/x', worktreePath: '/proj/.wt/x', summary: 'ok' }
  if (/^merge/.test(label)) return { ok: true, merged: ['wip/x'], conflicted: [], headSha: 'm' }
  if (/^gates /.test(label)) return GATE_OK
  if (label === 'security facts') return { files: ['a/src/x.ts', 'specs/features/011-x/spec.md'], tasksSecurityLine: 'Security-sensitive: no' }
  if (label === 'spec-compliance-reviewer') return { securitySensitive: false, findings: [{ severity: 'blocking', category: 'compliance', reqId: 'REQ-A-R010', file: 'a/x.ts', line: 3, claim: 'wrong status', evidence: 'q' }] }
  if (/reviewer/.test(label)) return { securitySensitive: false, findings: [] }
  if (/^verify |^fix-check /.test(label)) return vote(false)
  if (/^finalize F/.test(label)) return { ok: true, traceCheck: 'pass', status: 'Implemented', commit: 'fin' }
  if (label === 'finalize evidence') return { headSha: HEAD, logHead: `GATES_HEAD=${HEAD}`, exitLine: 'GATES_EXIT=0 n0nce', changedSinceGate: [], findingsBlock: GOOD_BLOCK }
  if (label === 'load persisted findings') return { found: true, block: GOOD_BLOCK }
  return { ok: true, summary: `done ${label}`, testDefects: [] }
}

// over(label, n, prompt) may replace the default answer (undefined keeps it).
async function run(args, over = () => undefined) {
  const calls = []
  const n = {}
  const agent = async (prompt, opts) => {
    const label = String(opts.label || '').replace(/ \((fallback|retry)\)$/, '')
    const k = (n[label] = (n[label] || 0) + 1)
    calls.push({ label: opts.label, prompt, opts })
    const o = over(label, k, prompt, opts)
    return o !== undefined ? o : answer(label, prompt)
  }
  const parallel = (thunks) => Promise.all(thunks.map((t) => t().catch(() => null)))
  const pipeline = async (items, ...stages) => {
    const out = []
    for (const [i, it] of items.entries()) {
      let v = it
      for (const s of stages) v = await s(v, it, i)
      out.push(v)
    }
    return out
  }
  const logs = []
  const summary = await script(Object.assign({ config: CONFIG, feature: 'F011', slug: 'x', approval: 'agent', root: '/proj/.wt/F011' }, args), agent, parallel, pipeline, () => {}, (m) => logs.push(m))
  return { summary, calls, logs, labels: calls.map((c) => c.label), find: (l) => calls.find((c) => c.label === l) }
}
// A run that starts after Tests: the red tests commit, and an earlier run's gate log for Finalize when this run has no gates.
const AFTER = (stages, extra) => Object.assign({ stages, testsSha: 'abc1234', evidence: { gateLog: '.sdd/logs/gates/x.log' } }, extra || {})
const FROM_PLAN = ['plan', 'tests', 'implement', 'gates', 'review', 'verify', 'finalize']
let checks = 0
const ok = (m) => { checks++; console.log(`ok - ${m}`) }

// 1. Arg whitelist and types; legacy is a known arg.
{
  await assert.rejects(run({ readViaGlt: true }), /unknown args readViaGlt/)
  await assert.rejects(run({ persistFindings: 'yes' }), /args\.persistFindings must be true or false/)
  await assert.rejects(run({ stages: 'gates' }), /args\.stages must be a list/)
  await assert.rejects(run({ capFallback: 'max' }), /args\.capFallback must be/)
  await assert.rejects(run(AFTER(['gates'], { testsSha: 'HEAD' })), /args\.testsSha must be a commit sha/)
  await assert.rejects(run({ evidence: { gateLog: '../x.log' } }), /args\.evidence must be/)
  await assert.rejects(run({ allowPassing: { 'REQ-A-R010': '' } }), /args\.allowPassing must map/)
  const legacy = await run({ legacy: true, stages: ['plan'] })
  assert.equal(legacy.summary.status, 'stopped')
  ok('unknown arg names and wrongly typed values stop the script before any agent call')
}

// 2. Happy path with the defaults: hook probe, read via git, persisted findings with reviewers, heavy rule, sign-off not required.
{
  const r = await run({ stages: FROM_PLAN }, (l, k) => (/^verify R1/.test(l) ? vote(true) : undefined))
  assert.equal(r.summary.status, 'ready-to-merge', JSON.stringify(r.summary))
  assert.equal(r.summary.securitySignoff, 'not-required')
  const setup = r.find('setup')
  assert.equal(setup.opts.agentType, 'integrator')
  assert.ok(setup.opts.schema.required.includes('hookProbe'))
  assert.match(setup.prompt, /HOOK PROBE \(required, after steps 1-4\): with your Write tool \(not Bash\), write the text "probe" to \/proj\/\.wt\/F011\/\.claude\/\.hook-probe/)
  assert.match(setup.prompt, /SCOPE OF THIS STEP: only prepare the workspace/)
  assert.match(r.find('spec-compliance-reviewer').prompt, /READ VIA GIT/)
  for (const c of r.calls) assert.ok(!/^ROLE: /.test(c.prompt) && c.opts.agentType !== 'general-purpose', `role fallback is off: ${c.label}`)
  const rf = r.find('review findings').prompt
  const rec = JSON.parse(/```json\n([\s\S]*?)\n```/.exec(rf)[1])
  assert.deepEqual(rec.reviewers, REVIEWERS)
  assert.equal(rec.securitySensitive, false)
  assert.equal(rec.findings[0].id, 'R1')
  ok('defaults: hook probe and setup scope in the setup prompt, roles run as their own agent types, findings record names the reviewers')
}

// 3. Hook probe fails closed; a custom marker and path come from the config; hookProbe: false removes it.
{
  const r = await run({ stages: FROM_PLAN }, (l) => (l === 'setup' ? Object.assign(answer('setup'), { hookProbe: 'File created successfully at: /proj/.wt/F011/.claude/.hook-probe' }) : undefined))
  assert.equal(r.summary.status, 'blocked-hooks')
  assert.deepEqual(r.labels, ['setup'])
  assert.match(r.summary.nextAction, /repository root/)
  const cfg = Object.assign({}, CONFIG, { pipeline: Object.assign({}, CONFIG.pipeline, { hook_probe: { path: 'agents/.probe', blocked_marker: 'DENIED by guard' } }) })
  const c = await run({ config: cfg, stages: ['plan'] }, (l) => (l === 'setup' ? Object.assign(answer('setup'), { hookProbe: 'DENIED by guard: agents/ is owner-only' }) : undefined))
  assert.equal(c.summary.status, 'stopped')
  assert.match(c.find('setup').prompt, /write the text "probe" to \/proj\/\.wt\/F011\/agents\/\.probe/)
  const off = await run({ stages: ['plan'], hookProbe: false }, (l) => (l === 'setup' ? Object.assign(answer('setup'), { hookProbe: '' }) : undefined))
  assert.equal(off.summary.status, 'stopped')
  assert.doesNotMatch(off.find('setup').prompt, /HOOK PROBE/)
  const cfgOff = Object.assign({}, CONFIG, { pipeline: Object.assign({}, CONFIG.pipeline, { hook_probe: { enabled: false } }) })
  assert.doesNotMatch((await run({ config: cfgOff, stages: ['plan'] })).find('setup').prompt, /HOOK PROBE/)
  ok('hook probe: a write that was not blocked stops the run with blocked-hooks after setup; marker and path configurable; can be turned off')
}

// 4. Gate result from raw lines: a pass claim without GATES_EXIT=0, another HEAD, or a dirty tree is not a pass.
{
  const lie = await run(AFTER(['gates']), (l) => (/^gates /.test(l) ? Object.assign({}, GATE_OK, { exitLine: 'GATES_EXIT=1 n0nce', failed: [] }) : undefined))
  assert.equal(lie.summary.gates[0].pass, false)
  assert.deepEqual(lie.summary.gates[0].failed, ['unknown'])
  assert.equal(lie.summary.gates[0].exit, 1)
  const other = await run(AFTER(['gates']), (l) => (/^gates /.test(l) ? Object.assign({}, GATE_OK, { logHead: `GATES_HEAD=${OLD}` }) : undefined))
  assert.equal(other.summary.status, 'blocked-infra')
  assert.equal(other.labels.filter((l) => /^gates post-implement #1/.test(l)).length, 4, 'a mismatched HEAD is an incomplete run: re-run up to 3 times')
  assert.match(other.find('gates post-implement #1 rerun 1').prompt, /--fresh/)
  let g = 0
  const dirty = await run(AFTER(['gates']), (l) => (/^gates /.test(l) && g++ === 0 ? Object.assign({}, GATE_OK, { logHead: `GATES_HEAD=${HEAD} dirty` }) : undefined))
  assert.deepEqual(dirty.summary.gates.map((g) => [g.pass, g.failed.join()]), [[false, 'dirty-tree'], [true, '']])
  assert.ok(dirty.labels.includes('gate-fix post-implement #1'))
  const good = await run(AFTER(['gates']))
  assert.deepEqual(good.summary.gates, [{ tag: 'post-implement', round: 1, pass: true, failed: [], head: HEAD, exit: 0 }])
  assert.match(good.find('gates post-implement #1').prompt, /exitLine = the last line of the log starting with GATES_EXIT=/)
  ok('gates: pass/fail from GATES_EXIT and GATES_HEAD against the current HEAD; mismatch = incomplete (re-run), dirty tree = failed gate')
}

// 5. Two refuting votes and no confirming one dismiss a blocking finding; a missing vote is not a refutation.
{
  const oneEach = await run(AFTER(['review', 'verify', 'finalize']), (l, k) => (/^verify R1 #1/.test(l) ? vote(true) : /^fix-check/.test(l) ? vote(false) : undefined))
  assert.equal(oneEach.summary.findings.confirmed, 1, 'one refute + one confirm = confirmed')
  assert.ok(oneEach.labels.includes('fix R1'))
  const dead = await run(AFTER(['review', 'verify', 'finalize']), (l) => (/^verify R1 #1/.test(l) ? vote(true) : /^verify R1 #2/.test(l) ? null : /^fix-check/.test(l) ? vote(false) : undefined))
  assert.equal(dead.summary.findings.confirmed, 1, 'one refute + a dead skeptic = not refuted')
  const both = await run(AFTER(['review', 'verify', 'finalize']), (l) => (/^verify R1/.test(l) ? vote(true) : undefined))
  assert.equal(both.summary.findings.refuted, 1)
  assert.equal(both.summary.status, 'ready-to-merge')
  ok('verify: two valid refuting votes (and none confirming) dismiss a finding; one refutation with a confirm or a dead skeptic does not')
}

// 6. Persisted findings: the block is returned verbatim and parsed by the script; only two stored refutations skip verification.
{
  const r = await run(AFTER(['verify', 'finalize'], { evidence: { gateLog: '.sdd/logs/gates/x.log' } }), (l) => (/^verify R2/.test(l) ? vote(true) : undefined))
  const load = r.find('load persisted findings')
  assert.ok(load.opts.schema.properties.block)
  assert.deepEqual(r.labels.filter((l) => /^verify R/.test(l)), ['verify R2 #1', 'verify R2 #2'], 'R1 (two refutations) skipped, R2 (stored "confirmed, fixed") verified again')
  assert.equal(r.summary.status, 'ready-to-merge', JSON.stringify(r.summary))
  for (const bad of ['no fence', block({ findings: [{ id: 'R1', severity: 'severe', claim: 'c', outcome: 'x' }] }), block({ findings: [{ id: 'R1', severity: 'blocking', claim: 'c', outcome: 'x' }, { id: 'R1', severity: 'blocking', claim: 'd', outcome: 'x' }] }), '```json\n{oops\n```']) {
    const b = await run(AFTER(['verify', 'finalize']), (l) => (l === 'load persisted findings' ? { found: true, block: bad } : undefined))
    assert.equal(b.summary.status, 'blocked-evidence', bad)
  }
  ok('persisted findings: parsed and validated by the script (malformed -> blocked-evidence); only findings with two stored refutations are skipped')
}

// 7. Finalize: same-run evidence, or args.evidence checked from raw facts.
{
  const none = await run(AFTER(['finalize'], { evidence: undefined }))
  assert.equal(none.summary.status, 'blocked-evidence')
  assert.match(none.summary.reason, /not established in this run: gates, review; no args\.evidence/)
  assert.ok(!none.labels.some((l) => /^finalize F/.test(l)), 'no finalize call without evidence')
  const EV = { evidence: { gateLog: '.sdd/logs/gates/F011-post-implement-1-aaaaaaa.log' } }
  const good = await run(AFTER(['finalize'], EV))
  assert.equal(good.summary.status, 'ready-to-merge', JSON.stringify(good.summary))
  assert.deepEqual(good.summary.evidence, { parts: ['gates', 'review'], head: HEAD, gateLog: EV.evidence.gateLog, gateHead: HEAD, findings: 3 })
  assert.match(good.find('finalize evidence').prompt, /the first line of \.sdd\/logs\/gates\/F011-post-implement-1-aaaaaaa\.log starting with GATES_HEAD=/)
  const cases = [
    [{ logHead: `GATES_HEAD=${OLD}`, changedSinceGate: ['a/src/x.ts'] }, /ran on bbbbbbbbbbbb, not HEAD aaaaaaaaaaaa \(changed since: a\/src\/x\.ts\)/],
    [{ exitLine: 'GATES_EXIT=1 n0nce' }, /GATES_EXIT=1/],
    [{ logHead: '' }, /no GATES_HEAD line/],
    [{ logHead: `GATES_HEAD=${HEAD} dirty` }, /dirty tree/],
    [{ findingsBlock: block({ reviewers: REVIEWERS.slice(0, 3), findings: [] }) }, /reviewers without a result perf-reviewer/],
    [{ findingsBlock: block({ reviewers: REVIEWERS, securitySensitive: true, findings: [] }) }, /reviewers without a result security-reviewer-2/],
    [{ findingsBlock: block({ reviewers: REVIEWERS, findings: [{ id: 'R1', severity: 'blocking', claim: 'c', outcome: 'refuted', votes: [vote(true)] }] }) }, /R1: refuted without two refuting votes/],
    [{ findingsBlock: block({ reviewers: REVIEWERS, findings: [{ id: 'R1', severity: 'blocking', claim: 'c', outcome: 'confirmed, unresolved', votes: [] }] }) }, /R1: confirmed, unresolved/],
    [{ findingsBlock: '' }, /no persisted findings block/],
  ]
  for (const [facts, rx] of cases) {
    const b = await run(AFTER(['finalize'], EV), (l) => (l === 'finalize evidence' ? Object.assign(answer(l), facts) : undefined))
    assert.equal(b.summary.status, 'blocked-evidence', JSON.stringify(facts))
    assert.match(b.summary.reason, rx)
  }
  const reviewOnly = await run(AFTER(['finalize'], EV), (l) => (l === 'finalize evidence' ? Object.assign(answer(l), { logHead: `GATES_HEAD=${OLD}`, changedSinceGate: ['specs/features/011-x/review.md'] }) : undefined))
  assert.equal(reviewOnly.summary.status, 'ready-to-merge', 'commits after the gate run that change only review.md keep the evidence')
  const unfixed = await run(AFTER(['gates', 'review', 'verify', 'finalize']), (l) => (/^fix-check/.test(l) ? vote(true) : undefined))
  assert.equal(unfixed.summary.status, 'blocked-review')
  assert.match(unfixed.summary.reason, /blocking finding R1 not fixed \(fix-check failed\)/)
  ok('finalize: without same-run gates/review it needs args.evidence; the script checks gate HEAD, exit, dirty flag, reviewers and two-vote refutations')
}

// 8. Security rule: configured paths, the plan line, a reviewer or the arg; second security review; sign-off pending by the
// product owner (pipeline.security_signoff 'po', the default) or the human owner ('owner').
{
  const touch = await run(AFTER(['review', 'verify', 'finalize']), (l) => (l === 'security facts' ? { files: ['src/auth/login.ts', 'pkg/crypto/aes.ts', 'README.md'], tasksSecurityLine: '' } : /^verify R1/.test(l) ? vote(true) : undefined))
  assert.equal(touch.summary.securitySensitive, true)
  assert.deepEqual(touch.summary.securityReasons, ['diff touches pkg/crypto/aes.ts, src/auth/login.ts'])
  assert.ok(touch.labels.includes('security-reviewer (independent #2)'))
  assert.equal(touch.summary.securitySignoff, 'pending-po')
  assert.equal(touch.summary.status, 'ready-to-merge')
  assert.match(touch.summary.nextAction, /pending-po\. The orchestrator \(product owner\) reviews the security findings of both independent security reviews .* records the sign-off in review\.md/)
  assert.match(touch.summary.nextAction, /Escalate to the human owner only per the escalation list \(docs\/ORCHESTRATOR\.md §2, docs\/DEVELOPMENT\.md §11\)/)
  assert.doesNotMatch(touch.summary.nextAction, /The OWNER reviews/)
  assert.ok(touch.find('finalize F011').prompt.includes('"PENDING - the orchestrator (product owner) signs off before merge, after both independent security reviews"'))
  const sensitive = (l) => (l === 'security facts' ? { files: ['src/auth/login.ts'], tasksSecurityLine: '' } : /^verify R1/.test(l) ? vote(true) : undefined)
  const withSignoff = (v) => ({ config: Object.assign({}, CONFIG, { pipeline: Object.assign({}, CONFIG.pipeline, { security_signoff: v }) }) })
  const po = await run(AFTER(['review', 'verify', 'finalize'], withSignoff('po')), sensitive)
  assert.equal(po.summary.securitySignoff, 'pending-po')
  assert.equal(po.summary.nextAction, (await run(AFTER(['review', 'verify', 'finalize']), sensitive)).summary.nextAction, "'po' is the default")
  const owner = await run(AFTER(['review', 'verify', 'finalize'], withSignoff('owner')), sensitive)
  assert.equal(owner.summary.securitySignoff, 'pending-owner')
  assert.ok(owner.labels.includes('security-reviewer (independent #2)'))
  assert.equal(owner.summary.status, 'ready-to-merge')
  assert.match(owner.summary.nextAction, /pending-owner\. The OWNER reviews the security findings .* does not merge before the sign-off/)
  assert.ok(owner.find('finalize F011').prompt.includes('"PENDING - the owner signs off before merge (the orchestrator never does)"'))
  await assert.rejects(run(AFTER(['review', 'verify', 'finalize'], withSignoff('orchestrator'))), /pipeline\.security_signoff must be 'po' or 'owner'/)
  const ownerPlain = await run(AFTER(['review', 'verify', 'finalize'], withSignoff('owner')), (l) => (/^verify R1/.test(l) ? vote(true) : undefined))
  assert.equal(ownerPlain.summary.securitySignoff, 'not-required')
  const legacyBad = await run({ legacy: true, stages: ['plan'], config: withSignoff('orchestrator').config })
  assert.equal(legacyBad.summary.securitySignoff, undefined, 'legacy ignores pipeline.security_signoff')
  const planLine = await run(AFTER(['review', 'verify', 'finalize']), (l) => (l === 'security facts' ? { files: ['a/x.ts'], tasksSecurityLine: '**Security-sensitive:** yes' } : /^verify R1/.test(l) ? vote(true) : undefined))
  assert.deepEqual(planLine.summary.securityReasons, ['plan: Security-sensitive'])
  const arg = await run(AFTER(['review', 'verify', 'finalize'], { security: true }), (l) => (/^verify R1/.test(l) ? vote(true) : undefined))
  assert.deepEqual(arg.summary.securityReasons, ['args.security'])
  const failClosed = await run(AFTER(['review', 'verify', 'finalize']), (l) => (l === 'security facts' ? null : /^verify R1/.test(l) ? vote(true) : undefined))
  assert.deepEqual(failClosed.summary.securityReasons, ['feature diff unavailable (fail closed)'])
  const plain = await run(AFTER(['review', 'verify', 'finalize']), (l) => (/^verify R1/.test(l) ? vote(true) : undefined))
  assert.equal(plain.summary.securitySignoff, 'not-required')
  assert.ok(!plain.labels.includes('security-reviewer (independent #2)'))
  ok("security rule: pipeline.security_paths, the plan line, args.security or an unavailable diff make the feature sensitive; sign-off pending-po by default, pending-owner with pipeline.security_signoff 'owner'")
}

// 9. testsSha: required after Tests; a commit, an ancestor of HEAD, a test(...) subject, at least one test path.
{
  const missing = await run({ stages: ['implement'] })
  assert.equal(missing.summary.status, 'blocked-tests')
  assert.match(missing.summary.reason, /args\.testsSha is missing/)
  const facts = (o) => (l, k, p) => {
    if (l !== 'testsSha check') return undefined
    const shas = JSON.parse(/For each commit sha in (\[[^\]]*\])/.exec(p)[1])
    return { commits: shas.map((sha) => Object.assign({ sha, full: sha.padEnd(40, '0'), ancestorExit: 0, files: ['a/tests/t.test.ts'], subject: 'test(F011): red' }, o)) }
  }
  for (const [o, rx] of [[{ ancestorExit: 1 }, /not an ancestor of HEAD/], [{ full: '' }, /not a commit/], [{ subject: 'feat(F011): T01 x' }, /not a test\(\.\.\.\) commit/],
    [{ files: ['a/src/x.ts', 'README.md'] }, /changes no test path/], [{ files: [] }, /changes no file/]]) {
    const r = await run(AFTER(['implement']), facts(o))
    assert.equal(r.summary.status, 'blocked-tests', JSON.stringify(o))
    assert.match(r.summary.reason, rx)
    assert.ok(!r.labels.some((l) => / impl$/.test(l)))
  }
  const skel = await run(AFTER(['implement'], { testsSha: ['abc1234', 'def5678'] }), facts({ files: ['a/src/skeleton.ts', 'a/x.test.ts'] }))
  assert.notEqual(skel.summary.status, 'blocked-tests', 'compile skeletons next to the tests are allowed; *.test.ts is a test path')
  assert.match(skel.find('testsSha check').prompt, /\["abc1234","def5678"\]/)
  ok('testsSha: missing, non-commit, non-ancestor, non-test(...) subject or no test path -> blocked-tests before Implement; skeletons allowed')
}

// 10. Red check: a new test that already passes blocks unless every id it verifies is in allowPassing.
{
  const red = { compiles: true, failingTests: 2, unexpectedPasses: ['t_ok'], passingVerifies: [{ test: 't_ok', ids: ['REQ-A-R010'] }], mustWithoutTest: [] }
  const blocked = await run({ stages: FROM_PLAN }, (l) => (/^red-check/.test(l) ? red : undefined))
  assert.equal(blocked.summary.status, 'blocked-tests')
  assert.match(blocked.summary.reason, /new tests already pass before implementation: t_ok \(REQ-A-R010\)/)
  assert.ok(!blocked.labels.some((l) => /^commit: test\(/.test(l)))
  assert.ok(blocked.find('red-check #1').opts.schema.required.includes('passingVerifies'))
  const allowed = await run({ stages: FROM_PLAN, allowPassing: { 'REQ-A-R010': 'behaviour already provided by the framework' } }, (l) => (/^red-check/.test(l) ? red : /^verify R1/.test(l) ? vote(true) : undefined))
  assert.equal(allowed.summary.status, 'ready-to-merge')
  assert.deepEqual(allowed.summary.allowedPassing, [{ test: 't_ok', ids: ['REQ-A-R010'], reasons: ['behaviour already provided by the framework'] }])
  ok('red check: unexpected passes block the Tests stage unless allowPassing names every id with a reason (recorded in the summary)')
}

// 11. Heavy-job lock rule: in every role that may build or run tests, not in the others; off with heavyLock: false or no lock.
{
  const r = await run({ stages: FROM_PLAN }, (l) => (/^verify R1/.test(l) ? vote(true) : undefined))
  const LOCK = '/proj/.wt/F011/tools/sdd/scripts/heavy.sh'
  const has = (c) => c.prompt.includes('HEAVY-JOB LOCK (hard rule, shared host)')
  for (const c of r.calls) {
    const want = ['implementer', 'test-author', 'verifier', 'spec-compliance-reviewer', 'security-reviewer', 'test-adequacy-reviewer', 'perf-reviewer'].includes(c.opts.agentType) || /^red-check /.test(c.label)
    assert.equal(has(c), want, c.label)
  }
  const impl = r.find('T01 impl').prompt
  assert.ok(impl.includes(`\`${LOCK} <command>\``) && impl.includes('`npm test`, `npm test -w {component}`') && impl.includes(`SDD_HEAVY_JOB_MB=2048 ${LOCK}`))
  assert.ok(!(await run({ stages: FROM_PLAN, heavyLock: false }, (l) => (/^verify R1/.test(l) ? vote(true) : undefined))).calls.some(has))
  assert.ok(!(await run({ config: Object.assign({}, CONFIG, { heavy_lock: false }), stages: ['plan', 'tests'] })).calls.some(has))
  ok('heavy-job lock rule in implementer, test-author, reviewer, verifier and red-check prompts only; off without a lock or with heavyLock: false')
}

// 12. Tiers: high-complexity implementer tasks and lanes run on the mid tier's model at high effort; others keep medium.
{
  const r = await run({ stages: ['implement'], testsSha: 'abc1234', root: undefined }) // main checkout: parallel lanes in worktrees
  const me = (l) => { const c = r.find(l); return [c.opts.model, c.opts.effort] }
  assert.deepEqual(me('T01 impl'), ['m-mid', 'high'])
  assert.deepEqual(me('T02 impl'), ['m-mid', 'medium'])
  assert.deepEqual(me('wave 2 lane 1'), ['m-mid', 'high'])
  assert.deepEqual(me('wave 2 lane 2'), ['m-mid', 'medium'])
  const legacy = await run({ stages: ['implement'], legacy: true })
  assert.equal(legacy.find('T01 impl').opts.effort, 'medium')
  ok('tiers: implementer tasks and lanes rated complexity high run at high effort on the mid tier model (not under legacy)')
}

console.log(`\npipeline hardening: ${checks} checks passed`)
