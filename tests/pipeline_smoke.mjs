// Smoke test for tools/sdd/pipeline/feature-pipeline.js: runs the Workflow script with
// a fake agent() that answers each call from its JSON schema and label, and checks the
// control flow (stages, retries, pause, product-owner resolution, test arbiter, merge
// resolution, fix-checks, model tiering). Run: node tests/pipeline_smoke.mjs
import { readFileSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import assert from 'node:assert/strict'

const here = path.dirname(fileURLToPath(import.meta.url))
const src = readFileSync(path.join(here, '..', 'tools', 'sdd', 'pipeline', 'feature-pipeline.js'), 'utf8')
const body = src.replace(/^export const meta =/m, 'const meta =')
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const script = new AsyncFunction('args', 'agent', 'parallel', 'pipeline', 'phase', 'log', body)

const CONFIG = {
  root: '/proj',
  project: { name: 'demo', description: 'demo project', threat_model: 'tm' },
  paths: { features_dir: 'specs/features', templates_dir: 'specs/templates', roadmap: 'docs/ROADMAP.md', constitution: 'docs/CONSTITUTION.md', development: 'docs/DEVELOPMENT.md', logs_dir: '.sdd/logs', worktree_root: '.wt', tools_dir: 'tools/sdd' },
  features: { id_prefix: 'F', id_digits: 3, branch_prefix: 'feat/' },
  requirements: { budget: { min: 15, max: 45 }, clarification_marker: '[NEEDS CLARIFICATION:' },
  components: { word: 'package' },
  tests: { run: 'npm test', run_component: 'npm test -w {component}' },
  sources: { kind: 'api-contract', citation_format: '(Source: openapi.yaml#/paths/x)', lookup_command: 'sdd source' },
  models: { mechanical: 'm-mech', mid: 'm-mid', strong: 'm-strong', top: 'm-top' },
  pipeline: { max_critic_fix_rounds: 2, max_gate_fix_rounds: 3, skeptics: 2, max_build_jobs: 4, gate_poll_seconds: 5 },
  commit: { trailers: ['Co-Authored-By: X <x@y>'] },
  merge: { union_manifests: ['package.json'] },
  gates: { fast: ['lint'], full: ['lint', 'test'] },
}

function fromSchema(schema) {
  const out = {}
  for (const [k, p] of Object.entries(schema.properties || {})) {
    if (p.type === 'boolean') out[k] = true
    else if (p.type === 'integer' || p.type === 'number') out[k] = 0
    else if (p.type === 'array') out[k] = []
    else if (p.enum) out[k] = p.enum[0]
    else if (p.type === 'string') out[k] = 'x'
    else if (p.type === 'object') out[k] = fromSchema(p)
  }
  return out
}

function makeRuntime(rules, byModel) {
  const calls = []
  const agent = async (prompt, opts) => {
    const label = opts.label || ''
    const base = fromSchema(opts.schema || { properties: {} })
    calls.push({ label, model: opts.model, effort: opts.effort, agentType: opts.agentType, phase: opts.phase, isolation: opts.isolation, prompt, route: opts.route })
    if (byModel) {
      const hit = byModel(opts)
      if (hit !== undefined) return hit
    }
    for (const [rx, fn] of rules) if (rx.test(label)) return fn(base, label, prompt, calls)
    if (/^commit/.test(label)) return { ok: true, sha: 'abc1234' }
    if (/^spec status/.test(label)) return { status: 'Approved', needsClarification: 0, requirements: 20 }
    if (/^spec-critic/.test(label)) return { verdict: 'pass', issues: [] }
    if (/^red-check/.test(label)) return { compiles: true, failingTests: 5, unexpectedPasses: [], mustWithoutTest: [] }
    if (/^gates /.test(label)) return { pass: true, infraError: false, failed: [] }
    if (/^verify|^fix-check/.test(label)) return { refuted: true, confidence: 'high', reasoning: 'r' }
    if (/^architect-planner|^load tasks/.test(label)) return { ok: true, securitySensitive: false, waves: [{ wave: 1, tasks: [{ id: 'T01', title: 't', components: ['a'], files: ['a/x.ts'], verifies: ['REQ-A-R010'], depends: [], parallel: false }] }] }
    if (/^finalize/.test(label)) return { ok: true, traceCheck: 'pass', status: 'Implemented', commit: 'fff' }
    if (/^setup/.test(label)) return { ok: true, branch: 'feat/F011-x', baseSha: 'base', headSha: 'head', scaffolded: true }
    if (/^test-author/.test(label)) return { ok: true, components: ['a'], testsAdded: ['t'], summary: 's' }
    if (/reviewer/.test(label)) return { securitySensitive: false, findings: [] }
    if (/^T\d+ impl|^wave/.test(label)) return { ok: true, tasksDone: ['T01'], tasksFailed: [], summary: 's', headSha: 'lane', branch: 'wip' }
    if (/^merge/.test(label)) return { ok: true, merged: ['wip'], conflicted: [], headSha: 'm' }
    return base
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
  return { agent, parallel, pipeline, phase: () => {}, log: (m) => logs.push(m), calls, logs }
}

async function run(args, rules = [], byModel = null) {
  const rt = makeRuntime(rules, byModel)
  const result = await script({ config: CONFIG, feature: 'F011', slug: 'x', ...args }, rt.agent, rt.parallel, rt.pipeline, rt.phase, rt.log)
  return { result, ...rt }
}

// 1. Happy path, all stages, agent approval.
{
  const { result, calls } = await run({ approval: 'agent' })
  assert.equal(result.status, 'ready-to-merge', JSON.stringify(result))
  const labels = calls.map((c) => c.label)
  for (const l of ['setup', 'spec-author F011', 'spec-critic r1', 'architect-planner F011', 'test-author F011', 'red-check #1', 'T01 impl', 'gates post-implement #1', 'spec-compliance-reviewer', 'security-reviewer', 'test-adequacy-reviewer', 'perf-reviewer', 'finalize F011']) {
    assert.ok(labels.includes(l), `missing call ${l}: ${labels.join(', ')}`)
  }
  // Model tiering by role from config ids.
  const by = Object.fromEntries(calls.map((c) => [c.label, c.model]))
  assert.equal(by['setup'], 'm-mech')
  assert.equal(by['spec-author F011'], 'm-strong')
  assert.equal(by['T01 impl'], 'm-mid')
  assert.equal(by['perf-reviewer'], 'm-mid')
  assert.equal(by['security-reviewer'], 'm-strong')
  assert.equal(by['finalize F011'], 'm-mid')
  // Role fallback injects the role file path from the project's agents dir.
  assert.match(calls.find((c) => c.label === 'spec-author F011').prompt, /\/proj\/\.claude\/agents\/spec-author\.md/)
  // Nothing project-specific leaked: tools path and source rule come from config.
  assert.match(calls.find((c) => c.label === 'setup').prompt, /python3 -I \/proj\/tools\/sdd\/sdd\.py new-feature 011 x/)
  assert.match(calls.find((c) => c.label === 'gates post-implement #1').prompt, /gates --detached --feature F011 --tag post-implement-1/)
  assert.match(calls.find((c) => c.label === 'gates post-implement #1').prompt, /GATES_EXIT/)
  console.log('ok 1 happy path')
}

// 2. Orchestrator approval stops after critic with awaiting-approval.
{
  const { result } = await run({}, [[/^spec status/, (b) => ({ status: 'Draft', needsClarification: 0 })]])
  assert.equal(result.status, 'awaiting-approval')
  assert.match(result.nextAction, /Status: Approved/)
  console.log('ok 2 awaiting approval')
}

// 3. Non-converging critic -> product-owner resolution -> blocking-only check passes.
{
  let n = 0
  const { result, calls } = await run({ approval: 'agent' }, [
    [/^spec-critic r/, (b) => ({ verdict: 'revise', issues: [{ severity: 'major', kind: 'scope', problem: `p${n++}` }] })],
    [/^spec-critic final/, () => ({ verdict: 'pass', issues: [] })],
  ])
  assert.equal(result.status, 'ready-to-merge')
  const labels = calls.map((c) => c.label)
  assert.ok(labels.includes('spec-fix r1') && labels.includes('spec-fix r2'))
  assert.ok(labels.includes('product-owner resolve 1'))
  assert.equal(calls.find((c) => c.label === 'product-owner resolve 1').model, 'm-strong')
  assert.ok(labels.includes('spec-critic final 1'))
  assert.match(calls.find((c) => c.label === 'spec-critic r2').prompt, /CONVERGENCE ROUND/)
  console.log('ok 3 product-owner resolution')
}

// 4. Product-owner rejection -> one spec-author fix pass -> re-review approves.
{
  let k = 0
  const { result, calls } = await run({ approval: 'agent' }, [
    [/^spec status/, () => ({ status: k++ ? 'Approved' : 'Draft', needsClarification: 0 })],
    [/^product-owner 1/, () => ({ approved: false, reasons: ['scope creep'] })],
    [/^product-owner 2/, () => ({ approved: true, reasons: [] })],
  ])
  assert.equal(result.status, 'ready-to-merge')
  assert.ok(calls.some((c) => c.label === 'spec-fix (po)'))
  console.log('ok 4 po rejection fix pass')
}

// 5. Gate fix loop: test gate fails repeatedly -> test-arbiter after 2 rounds, top model for gate-fix #2 and arbiter.
{
  let g = 0
  const { result, calls } = await run({ stages: ['gates'] }, [
    [/^gates post-implement/, () => (g++ < 3 ? { pass: false, infraError: false, failed: [{ gate: 'test', excerpt: 'boom' }] } : { pass: true, infraError: false, failed: [] })],
  ])
  assert.equal(result.status, 'stopped')
  const labels = calls.map((c) => c.label)
  assert.ok(labels.includes('gate-fix post-implement #1'))
  assert.ok(labels.includes('test-arbiter post-implement #2'), labels.join(','))
  assert.equal(calls.find((c) => c.label === 'gate-fix post-implement #2').model, 'm-top')
  assert.equal(calls.find((c) => c.label === 'gate-fix post-implement #1').model, 'm-mid')
  assert.equal(calls.find((c) => c.label === 'test-arbiter post-implement #2').model, 'm-top')
  console.log('ok 5 gate loop and test arbiter')
}

// 6. Agent dies twice -> run pauses, no further calls.
{
  const { result, calls } = await run({ stages: ['plan'] }, [[/^architect-planner/, () => null]])
  assert.equal(result.status, 'paused')
  assert.match(result.nextAction, /resumeFromRunId/)
  assert.ok(calls.some((c) => c.label === 'architect-planner F011 (retry)'))
  assert.ok(!calls.some((c) => /^commit: docs\(F011\): design/.test(c.label)))
  console.log('ok 6 pause on outage')
}

// 7. Parallel lanes with a conflict: union-merge for manifests, semantic resolution with the strong model.
{
  const plan = { ok: true, securitySensitive: false, waves: [{ wave: 1, tasks: [
    { id: 'T01', title: 'a', components: ['a'], files: ['a/x.ts'], verifies: ['R-A-R01'], depends: [], parallel: true },
    { id: 'T02', title: 'b', components: ['b'], files: ['b/x.ts'], verifies: ['R-A-R02'], depends: [], parallel: true },
  ] }] }
  const { result, calls } = await run({ stages: ['implement'] }, [
    [/^load tasks/, () => plan],
    [/^merge wave 1$/, () => ({ ok: false, merged: ['wip/F011-w1-l1'], conflicted: ['package.json', 'src/index.ts'], headSha: 'm' })],
    [/^union-merge/, () => ({ ok: true, merged: [], conflicted: ['src/index.ts'], headSha: 'm' })],
    [/^merge-resolve/, () => ({ ok: true, summary: 'resolved' })],
    [/^merge wave 1 \(rest\)/, () => ({ ok: true, merged: ['wip/F011-w1-l2'], conflicted: [], headSha: 'm2' })],
  ])
  assert.equal(result.status, 'stopped', JSON.stringify(result))
  assert.equal(result.waves[0].mode, 'worktrees')
  const resolve = calls.find((c) => c.label === 'merge-resolve wave 1')
  assert.equal(resolve.model, 'm-top') // semantic merge resolution is an escalation (README "Model routing")
  assert.match(resolve.prompt, /never union source files/)
  assert.ok(calls.some((c) => c.label === 'merge wave 1 (rest)'))
  console.log('ok 7 merge conflict resolution')
}

// 8. Security-sensitive review: 2nd security review, confirmed finding fixed, two fix-checks, sign-off required.
{
  const finding = { severity: 'blocking', category: 'dos', file: 'a.ts', line: 3, claim: 'c', evidence: 'e' }
  const { result, calls } = await run({ stages: ['review', 'verify', 'finalize'] }, [
    [/^security-reviewer$/, () => ({ securitySensitive: true, findings: [finding] })],
    [/^verify R1 #1/, () => ({ refuted: false, confidence: 'high', reasoning: 'real' })],
    [/^fix-check/, () => ({ refuted: false, confidence: 'high', reasoning: 'fixed' })],
  ])
  assert.equal(result.status, 'ready-to-merge')
  assert.equal(result.needsSecuritySignoff, true)
  assert.equal(result.findings.confirmed, 1)
  assert.equal(result.findings.fixed, 1)
  const labels = calls.map((c) => c.label)
  assert.ok(labels.includes('security-reviewer (independent #2)'))
  assert.ok(labels.includes('fix-check R1 #1') && labels.includes('fix-check R1 #2'))
  assert.match(result.nextAction, /sign-off/)
  console.log('ok 8 security flow')
}

// 9. Resume with tier change from a phase: earlier phases keep modelsBefore.
{
  const { calls } = await run({ stages: ['plan', 'tests'], models: { strong: 'new-strong' }, modelsFromPhase: 'Tests', modelsBefore: { strong: 'old-strong' } })
  assert.equal(calls.find((c) => c.label === 'architect-planner F011').model, 'old-strong')
  assert.equal(calls.find((c) => c.label === 'test-author F011').model, 'new-strong')
  console.log('ok 9 tier change from phase')
}

// 10. Worktree root: setup creates the worktree, verifiers use detached scratch worktrees, merge steps mention main-into-branch.
{
  const finding = { severity: 'blocking', category: 'logic', file: 'a.ts', line: 3, claim: 'c', evidence: 'e' }
  const { result, calls } = await run({ root: '/proj/.wt/F011', stages: ['review', 'verify', 'finalize'] }, [
    [/^spec-compliance-reviewer$/, () => ({ securitySensitive: false, findings: [finding] })],
  ])
  assert.match(calls.find((c) => c.label === 'setup').prompt, /git worktree add -b feat\/F011-x \/proj\/\.wt\/F011 main/)
  assert.match(calls.find((c) => c.label === 'verify R1 #1').prompt, /worktree add --detach \/proj\/\.wt\/exp-F011-R1-1/)
  assert.match(result.nextAction, /merge main INTO feat\/F011-x first/)
  console.log('ok 10 worktree mode')
}

// 11. features.main_branch and paths.build_dirs are honoured in the Setup prompt, the DISK RULE and the merge steps.
{
  const config = { ...CONFIG, features: { ...CONFIG.features, main_branch: 'master' }, paths: { ...CONFIG.paths, build_dirs: ['dist', 'node_modules/.cache'] } }
  const { result, calls } = await run({ config, root: '/proj/.wt/F011', stages: ['finalize'] })
  const setup = calls.find((c) => c.label === 'setup').prompt
  assert.match(setup, /git worktree add -b feat\/F011-x \/proj\/\.wt\/F011 master/)
  assert.match(setup, /git merge-base feat\/F011-x master/)
  assert.match(setup, /DISK RULE: .*\(dist, node_modules\/\.cache\)/)
  assert.match(result.nextAction, /merge master INTO feat\/F011-x first/)
  assert.match(result.nextAction, /git switch master && git merge --no-ff feat\/F011-x/)
  assert.match(result.nextAction, /rm -rf \/proj\/\.wt\/F011\/dist \/proj\/\.wt\/F011\/node_modules\/\.cache/)
  assert.doesNotMatch(result.nextAction, /\bmain\b/)
  console.log('ok 11 main_branch and build_dirs')
}

// ----- WI2 pipeline robustness -----
const SCOPE = 'SCOPE RULE (hard rule)'
const BLOCKING = { severity: 'blocking', category: 'logic', file: 'a.ts', line: 3, claim: 'c', evidence: 'e' }
const indexOf = (calls, rx) => calls.findIndex((c) => rx.test(c.label))

// 12. Scope rule in every prompt by default (also the config-loading call); strictScope: false removes it.
{
  const { result, calls } = await run({ approval: 'agent', config: undefined }, [
    [/^load sdd\.config\.json/, () => ({ ok: true, configJson: JSON.stringify(CONFIG) })],
    [/^spec-compliance-reviewer$/, () => ({ securitySensitive: false, findings: [BLOCKING] })],
  ])
  assert.equal(result.status, 'ready-to-merge', JSON.stringify(result))
  assert.equal(calls[0].label, 'load sdd.config.json')
  for (const c of calls) assert.ok(c.prompt.includes(SCOPE), `no scope rule in ${c.label}`)
  const off = await run({ approval: 'agent', strictScope: false })
  assert.equal(off.result.status, 'ready-to-merge')
  for (const c of off.calls) assert.ok(!c.prompt.includes(SCOPE), `scope rule in ${c.label}`)
  console.log('ok 12 scope rule')
}

// 13. Incomplete gate runs are re-run (with --fresh) and never spend a fix round.
{
  let n = 0
  const incomplete = { pass: false, infraError: false, incomplete: true, failed: [{ gate: 'runner', excerpt: 'killed' }] }
  const { result, calls } = await run({ approval: 'agent' }, [
    [/^gates post-implement/, () => (n++ < 3 ? incomplete : { pass: true, infraError: false, incomplete: false, failed: [] })],
  ])
  assert.equal(result.status, 'ready-to-merge', JSON.stringify(result))
  assert.equal(result.gates.length, 1, JSON.stringify(result.gates))
  assert.ok(!calls.some((c) => /^gate-fix/.test(c.label)))
  const runs = calls.filter((c) => /^gates post-implement #1/.test(c.label))
  assert.deepEqual(runs.map((c) => c.label), ['gates post-implement #1', 'gates post-implement #1 rerun 1', 'gates post-implement #1 rerun 2', 'gates post-implement #1 rerun 3'])
  assert.doesNotMatch(runs[0].prompt, /--fresh/)
  for (const r of runs.slice(1)) assert.match(r.prompt, /--fresh/)
  assert.match(runs[0].prompt, /gates --detached --feature F011 --tag post-implement-1/)
  assert.match(runs[0].prompt, /--wait/)
  assert.match(runs[0].prompt, /background/)
  const four = await run({ approval: 'agent' }, [[/^gates post-implement/, () => incomplete]])
  assert.equal(four.result.status, 'blocked-infra', JSON.stringify(four.result))
  assert.ok(!four.calls.some((c) => /^gate-fix/.test(c.label)))
  assert.equal(four.calls.filter((c) => /^gates post-implement/.test(c.label)).length, 4)
  console.log('ok 13 incomplete gate runs')
}

// 14. readViaGit: read-only roles read through git, no worktrees, no Verify isolation; default unchanged.
{
  const rules = [[/^spec-compliance-reviewer$/, () => ({ securitySensitive: false, findings: [BLOCKING] })], [/^verify R1 #1/, () => ({ refuted: false, confidence: 'high', reasoning: 'real' })], [/^fix-check/, () => ({ refuted: false, confidence: 'high', reasoning: 'fixed' })]]
  const readOnly = /reviewer|^verify R|^fix-check|^spec-critic/
  const on = await run({ approval: 'agent', readViaGit: true }, rules)
  assert.equal(on.result.status, 'ready-to-merge', JSON.stringify(on.result))
  const ro = on.calls.filter((c) => readOnly.test(c.label))
  for (const l of ['spec-critic r1', 'spec-compliance-reviewer', 'security-reviewer', 'test-adequacy-reviewer', 'perf-reviewer', 'verify R1 #1', 'fix-check R1 #1']) assert.ok(ro.some((c) => c.label === l), l)
  for (const c of ro) {
    assert.match(c.prompt, /READ VIA GIT/, c.label)
    assert.doesNotMatch(c.prompt, /git worktree add/, c.label)
  }
  for (const c of on.calls.filter((x) => /^verify R/.test(x.label))) assert.equal(c.isolation, undefined)
  const off = await run({ approval: 'agent' }, rules)
  for (const c of off.calls.filter((x) => readOnly.test(x.label))) assert.doesNotMatch(c.prompt, /READ VIA GIT/, c.label)
  for (const c of off.calls.filter((x) => /^verify R/.test(x.label))) assert.equal(c.isolation, 'worktree')
  console.log('ok 14 read via git')
}

// 15. Review and Verify records are persisted before Finalize; a run paused in Verify keeps its findings.
{
  const rules = [[/^spec-compliance-reviewer$/, () => ({ securitySensitive: false, findings: [BLOCKING] })]]
  const { result, calls } = await run({ approval: 'agent' }, rules)
  assert.equal(result.status, 'ready-to-merge')
  const rf = indexOf(calls, /^review findings$/)
  const vr = indexOf(calls, /^verify record$/)
  const fin = indexOf(calls, /^finalize /)
  assert.ok(rf > indexOf(calls, /^perf-reviewer$/) && rf < indexOf(calls, /^verify R1/), `review findings at ${rf}`)
  assert.ok(vr > indexOf(calls, /^verify R1 #2/) && vr < fin, `verify record at ${vr}`)
  assert.match(calls[rf].prompt, /docs\(F011\): review findings/)
  assert.match(calls[rf].prompt, /"claim": "c"/)
  assert.match(calls[fin].prompt, /keep the existing §3 rows and §6 justifications/)
  const paused = await run({ approval: 'agent' }, rules.concat([[/^verify R1/, () => null]]))
  assert.equal(paused.result.status, 'paused')
  assert.ok(paused.calls.some((c) => c.label === 'review findings'))
  assert.ok(!paused.calls.some((c) => /^finalize/.test(c.label)))
  console.log('ok 15 persisted review record')
}

// 16. Red check cross-checks with --include; outsideScan blocks Implemented until scan_globs covers it.
{
  const red = { compiles: true, failingTests: 3, unexpectedPasses: [], mustWithoutTest: [], outsideScan: ['tools/x/test_a.py'] }
  const { result, calls } = await run({ approval: 'agent' }, [
    [/^test-author F011/, () => ({ ok: true, components: ['a'], testsAdded: ['tools/x/test_a.py', 'tests/b.test.ts'], summary: 's' })],
    [/^red-check/, () => red],
    [/^finalize/, () => ({ ok: false, traceCheck: 'fail', status: 'Approved', commit: 'fff', notes: 'R1 untested' })],
  ])
  const rc = calls.find((c) => c.label === 'red-check #1')
  assert.match(rc.prompt, /trace --feature 011 --json --include tools\/x\/test_a\.py --include tests\/b\.test\.ts/)
  assert.ok(calls.some((c) => c.label === 'commit: test(F011): failing tests for x'))
  assert.deepEqual(result.outsideScan, ['tools/x/test_a.py'])
  const fin = calls.find((c) => /^finalize/.test(c.label)).prompt
  assert.match(fin, /trace --check/)
  assert.doesNotMatch(fin, /--include/)
  assert.equal(result.status, 'blocked-finalize', JSON.stringify(result))
  assert.notEqual(result.final.status, 'Implemented')
  assert.match(result.nextAction, /^add these paths to `trace\.scan_globs` in sdd\.config\.json before merge: tools\/x\/test_a\.py/)
  assert.match(result.nextAction, /stages: \["finalize"\]/)
  // A passing run with outsideScan still starts its nextAction with the configuration gap.
  const ok = await run({ approval: 'agent' }, [[/^red-check/, () => red]])
  assert.equal(ok.result.status, 'ready-to-merge')
  assert.match(ok.result.nextAction, /^add these paths to `trace\.scan_globs`/)
  console.log('ok 16 red check outsideScan')
}

// 17. Build cache variables in the shared context.
{
  const config = { ...CONFIG, build_cache: { env: { RUSTC_WRAPPER: 'sccache', SCCACHE_DIR: '/cache/sccache' } } }
  const { calls } = await run({ config, stages: ['plan'] })
  assert.match(calls[0].prompt, /export these before building: RUSTC_WRAPPER=sccache SCCACHE_DIR=\/cache\/sccache/)
  assert.match(calls[0].prompt, /size-capped and never on tmpfs/)
  const none = await run({ stages: ['plan'] })
  assert.doesNotMatch(none.calls[0].prompt, /export these before building/)
  console.log('ok 17 build cache')
}

// 18. Interrupted work: uncommitted edits outside the feature dir stop Setup, nothing is discarded.
{
  const { result, calls } = await run({ approval: 'agent' }, [[/^setup/, () => ({ ok: true, branch: 'feat/F011-x', baseSha: 'b', headSha: 'h', dirty: ['src/a.ts'] })]])
  assert.equal(result.status, 'blocked-setup')
  assert.equal(calls.length, 1)
  assert.match(result.nextAction, /[Ii]nspect/)
  assert.match(result.nextAction, /src\/a\.ts/)
  assert.match(result.nextAction, /never discard/)
  assert.match(calls[0].prompt, /dirty/)
  console.log('ok 18 dirty setup')
}

// ----- WI3 model routing -----
// Candidates come from the Python implementation of the formula (one implementation, D-21).
function routingFor(table, mode = 'formula') {
  const code = 'import json,sys; sys.path.insert(0, sys.argv[1]); from sddkit import routing; t = routing.normalize(json.loads(sys.argv[2])); print(json.dumps({"mode": sys.argv[3], "table": t, "candidates": routing.candidates(t)}))'
  return JSON.parse(execFileSync('python3', ['-I', '-c', code, path.join(here, '..', 'tools', 'sdd'), JSON.stringify(table), mode], { encoding: 'utf8' }))
}
const pt = (model, effort, intelligence, cost, pool) => ({ model, effort, intelligence, cost_per_task: cost, tokens_per_task: 1000, pool })
const POOLS = { standard: { capped: false }, capped: { capped: true, scarcity: 3 }, p1: { capped: true, scarcity: 3 }, p2: { capped: true, scarcity: 3 } }
const TWO_POOL = { version: 1, pools: POOLS, points: [pt('cap-c', 'medium', 58, 1, 'capped'), pt('std-s', 'medium', 65, 2, 'standard')] }
const tel = (result, rx) => result.telemetry.filter((t) => rx.test(t.label))
const oneTask = (tasks) => ({ ok: true, securitySensitive: false, waves: [{ wave: 1, tasks }] })
const task = (id, extra = {}) => Object.assign({ id, title: id, components: ['a'], files: [`a/${id}.ts`], verifies: ['R-A-R01'], depends: [], parallel: false }, extra)

// 19. Tiers mode: model and effort from {id, effort} tiers; gate falls back to mid; call-site effort kept under a plain id.
{
  const models = { mechanical: 'm-mech', gate: '', mid: { id: 'm-mid', effort: 'medium' }, strong: 'm-strong', top: { id: 'm-top', effort: 'high' } }
  const { result, calls } = await run({ config: { ...CONFIG, models }, approval: 'agent' })
  assert.equal(result.status, 'ready-to-merge', JSON.stringify(result))
  const gate = calls.find((c) => c.label === 'gates post-implement #1')
  assert.deepEqual([gate.model, gate.effort], ['m-mid', 'medium'])
  const setup = calls.find((c) => c.label === 'setup')
  assert.deepEqual([setup.model, setup.effort], ['m-mech', 'low'])
  const impl = calls.find((c) => c.label === 'T01 impl')
  assert.deepEqual([impl.model, impl.effort], ['m-mid', 'medium'])
  assert.equal(calls.find((c) => c.label === 'spec-author F011').model, 'm-strong')
  for (const c of calls) assert.equal(c.route, undefined, `route leaked into agent() opts of ${c.label}`)
  const withGate = await run({ config: { ...CONFIG, models: { ...models, gate: { id: 'm-gate', effort: 'medium' }, mechanical: { id: 'm-mech', effort: 'high' } } }, stages: ['gates'] })
  const g2 = withGate.calls.find((c) => c.label === 'gates post-implement #1')
  assert.deepEqual([g2.model, g2.effort], ['m-gate', 'medium'])
  const s2 = withGate.calls.find((c) => c.label === 'setup')
  assert.deepEqual([s2.model, s2.effort], ['m-mech', 'high'])
  console.log('ok 19 tiers with effort')
}

// 20. Levels: role base and minimum, task complexity and risk, hard, retries, clamp to 1-6; lanes take the hardest task.
{
  const roles = (implementer) => ({ mode: 'tiers', table: { roles: { implementer, mechanical: { base: 1, min: 1 }, gate: { base: 2, min: 2 } } }, candidates: {} })
  const tasks = [task('T01', { complexity: 'low', risk: 'none' }), task('T02', { complexity: 'high', risk: 'security' }), task('T03')]
  let fails = 0
  const rules = [[/^load tasks/, () => oneTask(tasks)], [/^T03 impl$/, () => (fails++ ? null : { ok: false, tasksDone: [], tasksFailed: ['T03'], summary: 'no' })]]
  const level = (r, label) => r.telemetry.find((t) => t.label === label).level
  const a = await run({ config: { ...CONFIG, routing: roles({ base: 3, min: 3 }) }, stages: ['implement'] }, rules)
  assert.equal(level(a.result, 'T01 impl'), 3) // 3 - 1 = 2, floored at the role minimum 3
  assert.equal(level(a.result, 'T02 impl'), 5) // 3 + 1 + 1
  assert.equal(level(a.result, 'T03 impl'), 3)
  assert.equal(level(a.result, 'T03 impl retry'), 4) // one prior failed attempt
  assert.equal(level(a.result, 'setup'), 1)
  fails = 0
  const h = await run({ config: { ...CONFIG, routing: roles({ base: 3, min: 3 }) }, stages: ['implement'], hard: true }, rules)
  assert.equal(level(h.result, 'T02 impl'), 6) // 3 + 1 + 1 + 1
  assert.equal(level(h.result, 'setup'), 2)
  const clampUp = await run({ config: { ...CONFIG, routing: roles({ base: 5, min: 1 }) }, stages: ['implement'], hard: true }, [[/^load tasks/, () => oneTask([task('T02', { complexity: 'high', risk: 'concurrency' })])]])
  assert.equal(level(clampUp.result, 'T02 impl'), 6) // 5 + 1 + 1 + 1 = 8 -> 6
  const clampDown = await run({ config: { ...CONFIG, routing: roles({ base: 1, min: 1 }) }, stages: ['implement'] }, [[/^load tasks/, () => oneTask([task('T01', { complexity: 'low' })])]])
  assert.equal(level(clampDown.result, 'T01 impl'), 1) // 1 - 1 = 0 -> 1
  const lane = [task('T01', { complexity: 'low', risk: 'none', parallel: true }), task('T02', { complexity: 'high', risk: 'none', parallel: true }), task('T03', { complexity: 'medium', risk: 'concurrency', parallel: true })]
  const l = await run({ config: { ...CONFIG, routing: roles({ base: 3, min: 2 }) }, stages: ['implement'], isolation: 'worktree' }, [[/^load tasks/, () => oneTask(lane)]])
  const rec = l.result.telemetry.find((t) => t.label === 'wave 1 lane 1')
  assert.equal(rec.level, 5) // as (high, concurrency): 3 + 1 + 1
  assert.equal(rec.role, 'implementer')
  assert.equal(l.calls.find((c) => c.label === 'wave 1 lane 1').agentType, 'general-purpose')
  assert.equal(l.result.telemetry.find((t) => t.label === 'gates post-implement #1'), undefined)
  console.log('ok 20 levels')
}

// 21. Formula mode, monotonic escalation: L3 picks S; the retry at L4 would pick C (less intelligent) but stays on S.
{
  const routing = routingFor(TWO_POOL)
  assert.deepEqual(routing.candidates['3'].map((c) => c.model), ['std-s', 'cap-c'])
  assert.deepEqual(routing.candidates['4'].map((c) => c.model), ['cap-c', 'std-s'])
  const { result, calls } = await run({ config: { ...CONFIG, routing }, stages: ['implement'] }, [
    [/^load tasks/, () => oneTask([task('T01')])],
    [/^T01 impl$/, () => ({ ok: false, tasksDone: [], tasksFailed: ['T01'], summary: 'no' })],
  ])
  const first = calls.find((c) => c.label === 'T01 impl')
  const retry = calls.find((c) => c.label === 'T01 impl retry')
  assert.deepEqual([first.model, first.effort], ['std-s', 'medium'])
  assert.equal(retry.model, 'std-s')
  const t = result.telemetry.find((x) => x.label === 'T01 impl retry')
  assert.deepEqual([t.level, t.model, t.mode], [4, 'std-s', 'formula'])
  assert.equal(result.telemetry.find((x) => x.label === 'T01 impl').outcome, 'fail')
  // A fresh L4 call (no previous attempt) does take C.
  const other = await run({ config: { ...CONFIG, routing }, stages: ['plan'] })
  assert.equal(other.calls.find((c) => c.label === 'architect-planner F011').model, 'cap-c')
  console.log('ok 21 formula monotonic retries')
}

// 22. Usage caps: failure detected without error text; per-call fallbacks; exhausted pools; pause only without candidates.
{
  const THREE = { version: 1, pools: POOLS, roles: { 'test-author': { base: 6, min: 6 } }, points: [
    pt('cap-a', 'medium', 58, 1, 'p1'), pt('cap-a', 'high', 62, 1.4, 'p1'), pt('cap-b', 'medium', 60, 1.5, 'p2'), pt('std-s', 'medium', 65, 2, 'standard')] }
  const routing = routingFor(THREE)
  assert.deepEqual([routing.candidates['4'][0].model, routing.candidates['4'][0].effort], ['cap-a', 'medium'])
  assert.deepEqual([routing.candidates['6'][0].model, routing.candidates['6'][0].effort], ['cap-a', 'high'])
  const failOn = (models) => (o) => (models.includes(o.model) ? null : undefined)
  // (a) + (c): cap-a returns null (no error text) -> p1 exhausted -> planner's own fallback cap-b (cheapest I >= 58);
  // the test-author's pick cap-a/high (I 62) goes straight to ITS fallback std-s (cap-b is below 62).
  const ac = await run({ config: { ...CONFIG, routing }, stages: ['plan', 'tests'] }, [], failOn(['cap-a']))
  assert.notEqual(ac.result.status, 'paused', JSON.stringify(ac.result))
  const planner = ac.calls.filter((c) => /^architect-planner/.test(c.label))
  assert.deepEqual(planner.map((c) => [c.label, c.model, c.effort]), [['architect-planner F011', 'cap-a', 'medium'], ['architect-planner F011 (fallback)', 'cap-b', 'medium']])
  const ta = ac.calls.filter((c) => /^test-author F011/.test(c.label))
  assert.deepEqual(ta.map((c) => [c.label, c.model]), [['test-author F011 (fallback)', 'std-s']])
  assert.equal(tel(ac.result, /^test-author F011/)[0].fallback_of, 'cap-a')
  assert.equal(tel(ac.result, /^architect-planner F011 \(fallback\)/)[0].fallback_of, 'cap-a')
  assert.equal(tel(ac.result, /^architect-planner F011$/)[0].outcome, null)
  // (b): the fallback on p2 also returns null -> p2 exhausted -> standard point, no pause.
  const b = await run({ config: { ...CONFIG, routing }, stages: ['plan'] }, [], failOn(['cap-a', 'cap-b']))
  assert.notEqual(b.result.status, 'paused')
  assert.deepEqual(b.calls.filter((c) => /^architect-planner/.test(c.label)).map((c) => c.model), ['cap-a', 'cap-b', 'std-s'])
  assert.deepEqual(tel(b.result, /^architect-planner/).map((t) => t.fallback_of), [null, 'cap-a', 'cap-b'])
  // (d): no equal-or-smarter point outside the exhausted pools -> one retry on the same point -> paused.
  const D = routingFor({ version: 1, pools: POOLS, points: [pt('cap-x', 'medium', 65, 1, 'capped'), pt('low-s', 'medium', 50, 0.5, 'standard')] })
  const d = await run({ config: { ...CONFIG, routing: D }, stages: ['plan'] }, [], failOn(['cap-x']))
  assert.equal(d.result.status, 'paused')
  assert.deepEqual(d.calls.filter((c) => /^architect-planner/.test(c.label)).map((c) => [c.label, c.model]), [['architect-planner F011', 'cap-x'], ['architect-planner F011 (retry)', 'cap-x']])
  // (e): an uncapped failure is retried once on the same point, then the run pauses.
  const E = routingFor({ version: 1, pools: POOLS, points: [pt('std-s', 'medium', 65, 2, 'standard')] })
  const e = await run({ config: { ...CONFIG, routing: E }, stages: ['plan'] }, [[/^architect-planner/, () => null]])
  assert.equal(e.result.status, 'paused')
  assert.deepEqual(e.calls.filter((c) => /^architect-planner/.test(c.label)).map((c) => [c.label, c.model]), [['architect-planner F011', 'std-s'], ['architect-planner F011 (retry)', 'std-s']])
  // A thrown error is a failed attempt too.
  const thrown = await run({ config: { ...CONFIG, routing }, stages: ['plan'] }, [], (o) => { if (o.model === 'cap-a') throw new Error('') })
  assert.equal(thrown.calls.filter((c) => /^architect-planner/.test(c.label)).map((c) => c.model).join(), 'cap-a,cap-b')
  console.log('ok 22 usage caps')
}

// 23. Telemetry: one record per agent() attempt, deterministic fields only; outcomes; no logging call in the script.
{
  const FIELDS = ['call', 'attempt', 'label', 'role', 'level', 'model', 'effort', 'outcome', 'fallback_of', 'mode'].sort()
  let n = 0
  const r = await run({ approval: 'agent', runKey: 'F011-20260101T000000' }, [
    [/^architect-planner/, (b, label) => (n++ ? { ok: true, securitySensitive: false, waves: [{ wave: 1, tasks: [task('T01')] }] } : null)],
    [/^gates post-implement/, () => ({ pass: false, infraError: false, failed: [{ gate: 'lint', excerpt: 'x' }] })],
  ])
  assert.equal(r.result.runKey, 'F011-20260101T000000')
  assert.equal(r.result.telemetry.length, r.calls.length)
  for (const t of r.result.telemetry) assert.deepEqual(Object.keys(t).sort(), FIELDS)
  const pl = tel(r.result, /^architect-planner/)
  assert.deepEqual(pl.map((t) => [t.call, t.attempt, t.outcome, t.label]), [['architect-planner F011#1', 1, null, 'architect-planner F011'], ['architect-planner F011#1', 2, 'ok', 'architect-planner F011 (retry)']])
  assert.ok(tel(r.result, /^gates post-implement/).every((t) => t.outcome === 'ok' && t.role === 'gate'))
  const seen = {}
  for (const t of r.result.telemetry.filter((x) => x.attempt === 1)) {
    const base = t.label.replace(/ \((retry|fallback)\)$/, '')
    seen[base] = (seen[base] || 0) + 1
    assert.equal(t.call, `${base}#${seen[base]}`)
  }
  const twice = await run({ approval: 'agent' }, [[/^spec status/, (() => { let k = 0; return () => ({ status: k++ ? 'Approved' : 'Draft', needsClarification: 0 }) })()], [/^product-owner 1/, () => ({ approved: true, reasons: [] })]])
  assert.deepEqual(tel(twice.result, /^spec status/).map((t) => t.call), ['spec status#1', 'spec status#2'])
  assert.ok(r.calls.every((c) => !/routing log/.test(c.prompt)))
  assert.equal(r.result.status, 'blocked-gates')
  const derived = await run({ stages: ['plan'] })
  assert.equal(derived.result.runKey, 'F011:plan:head')
  const paused = await run({ stages: ['plan'] }, [[/^architect-planner/, () => null]])
  assert.ok(paused.calls.every((c) => !/routing log/.test(c.prompt)))
  assert.equal(paused.result.telemetry.length, paused.calls.length)
  console.log('ok 23 telemetry')
}

// 24. Resume in formula mode: earlier phases keep routingBefore, later ones use the current routing; exhausted pools clear at modelsFromPhase.
{
  const before = routingFor({ version: 1, pools: POOLS, points: [pt('old-cap', 'medium', 60, 1, 'capped'), pt('old-std', 'low', 65, 2, 'standard')] })
  const now = routingFor({ version: 1, pools: POOLS, points: [pt('new-cap', 'high', 60, 1, 'capped'), pt('new-std', 'medium', 65, 2, 'standard')] })
  const { result, calls } = await run({ config: { ...CONFIG, routing: now }, routingBefore: before, modelsFromPhase: 'Gates', stages: ['plan', 'gates', 'review'] }, [], (o) => (o.model === 'old-cap' ? null : undefined))
  const PRE = ['Setup', 'Approve', 'Plan']
  for (const c of calls) {
    const old = PRE.includes(c.phase)
    assert.ok(old ? /^old-/.test(c.model) : /^new-/.test(c.model), `${c.label} (${c.phase}) ran on ${c.model}`)
  }
  assert.deepEqual(calls.filter((c) => /^architect-planner/.test(c.label)).map((c) => [c.model, c.effort]), [['old-cap', 'medium'], ['old-std', 'low']])
  const scr = calls.find((c) => c.label === 'spec-compliance-reviewer')
  assert.deepEqual([scr.model, scr.effort], ['new-cap', 'high']) // the pool exhausted before Gates is usable again
  assert.deepEqual(result.routing, now)
  console.log('ok 24 formula resume')
}

console.log('pipeline smoke: all ok')
