// Cache-key equivalence test for tools/sdd/pipeline/feature-pipeline.js (docs/PLAYBOOK.md "Changing the pipeline safely").
//
// A paused Workflow run is resumed with the same args, and the runtime replays every completed agent call whose
// (prompt, opts) key is unchanged. A pipeline change that alters the prompt or opts of an already completed call
// silently re-runs it (cost, and possibly a different result). So every new behaviour goes behind a new arg (or
// config key), and this test proves that without it every call is byte-identical to a baseline version of the script:
//   1. it runs the current script and the baseline (default: `git show 43c0883:<script>`) under a stub runtime
//      that records every agent(prompt, opts) and answers from the label and the per-label call count only, so both
//      scripts walk the same path; over several arg sets and scenarios it compares the sequences and the summaries;
//   2. it checks that the opt-in args change exactly what they should (redCheckTags, persistFindings).
//
// Run: node tests/pipeline_cachekeys.mjs [--baseline <git-rev>]
import { readFileSync, mkdirSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import assert from 'node:assert/strict'

const here = path.dirname(fileURLToPath(import.meta.url))
const KIT = path.join(here, '..')
const SCRIPT = 'tools/sdd/pipeline/feature-pipeline.js'
const argv = process.argv.slice(2)
const BASE_REV = argv.includes('--baseline') ? argv[argv.indexOf('--baseline') + 1] : '43c0883'

const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const compile = (src) => new AsyncFunction('args', 'agent', 'parallel', 'pipeline', 'phase', 'log', src.replace(/^export const meta =/m, 'const meta ='))
const currentSrc = readFileSync(path.join(KIT, SCRIPT), 'utf8')
let baselineSrc = null
try {
  baselineSrc = execFileSync('git', ['-C', KIT, 'show', `${BASE_REV}:${SCRIPT}`], { encoding: 'utf8', maxBuffer: 1 << 26, stdio: ['ignore', 'pipe', 'ignore'] })
} catch {
  // A copy without git history (e.g. an exported tarball): only the opt-in checks run.
  console.log(`skip - baseline ${BASE_REV} not available: byte-identity checks not run`)
}

const CONFIG = {
  root: '/proj',
  project: { name: 'demo', description: 'demo project', threat_model: 'tm' },
  paths: { features_dir: 'specs/features', templates_dir: 'specs/templates', roadmap: 'docs/ROADMAP.md', constitution: 'docs/CONSTITUTION.md', development: 'docs/DEVELOPMENT.md', logs_dir: '.sdd/logs', worktree_root: '.wt', tools_dir: 'tools/sdd', build_dirs: ['build'] },
  features: { id_prefix: 'F', id_digits: 3, branch_prefix: 'feat/' },
  requirements: { budget: { min: 15, max: 45 }, clarification_marker: '[NEEDS CLARIFICATION:', id_regex: '[A-Z][A-Z0-9]*(?:-[A-Z0-9.]+)*-R\\d{2,3}' },
  components: { word: 'package' },
  tests: { run: 'npm test', run_component: 'npm test -w {component}' },
  sources: { kind: 'api-contract', citation_format: '(Source: openapi.yaml#/paths/x)', lookup_command: 'sdd source' },
  models: { mechanical: 'm-mech', mid: { id: 'm-mid', effort: 'medium' }, strong: 'm-strong', top: { id: 'm-top', effort: 'high' } },
  pipeline: { max_critic_fix_rounds: 2, max_gate_fix_rounds: 3, skeptics: 2, max_build_jobs: 4, gate_poll_seconds: 5 },
  commit: { trailers: ['Co-Authored-By: X <x@example.com>'] },
  merge: { union_manifests: ['package.json'] },
  gates: { fast: ['lint'], full: ['lint', 'test'] },
  trace: { verifies_keywords: ['Verifies'], exclude_globs: ['**/node_modules/**'] },
}
function routingFor(table) {
  const code = 'import json,sys; sys.path.insert(0, sys.argv[1]); from sddkit import routing; t = routing.normalize(json.loads(sys.argv[2])); print(json.dumps({"mode": "formula", "table": t, "candidates": routing.candidates(t)}))'
  return JSON.parse(execFileSync('python3', ['-I', '-c', code, path.join(KIT, 'tools', 'sdd'), JSON.stringify(table)], { encoding: 'utf8' }))
}
const pt = (model, effort, intelligence, cost, pool) => ({ model, effort, intelligence, cost_per_task: cost, tokens_per_task: 1000, pool })
const FORMULA = routingFor({ version: 1, pools: { standard: { capped: false }, capped: { capped: true, scarcity: 3 } }, points: [pt('cap-c', 'medium', 58, 1, 'capped'), pt('std-s', 'medium', 65, 2, 'standard')] })

// ---------------------------------------------------------------------------
// Scripted answers: a function of the base label (fallback/retry suffix stripped), the per-label count and the
// scenario only, never of the prompt, so the old and the new script get the same answers.
// ---------------------------------------------------------------------------
const TASK = (id, extra) => Object.assign({ id, title: id, components: ['a'], files: [`a/${id}.ts`], verifies: ['REQ-A-R010'], depends: [], parallel: false }, extra)
function responder(scenario) {
  const n = {}
  return (opts) => {
    if (scenario.capped && opts.model === 'cap-c') return null
    const label = String(opts.label || '').replace(/ \((fallback|retry)\)$/, '')
    const k = (n[label] = (n[label] || 0) + 1)
    if (scenario.persisted && label === 'load persisted findings') return { found: true, records: scenario.persisted }
    if (label === 'setup') return { ok: true, branch: 'feat/F011-x', baseSha: 'base', headSha: 'head', scaffolded: true }
    if (/^commit/.test(label)) return { ok: true, sha: 'c' + label.length }
    if (label === 'spec status') return { status: k === 1 && !scenario.approved ? 'Draft' : 'Approved', needsClarification: 0, requirements: 20 }
    if (label === 'spec-critic r1') return { verdict: 'revise', issues: [{ severity: 'major', kind: 'coverage', reqId: 'REQ-A-R010', problem: 'gap' }] }
    if (/^spec-critic/.test(label)) return { verdict: 'pass', issues: [] }
    if (label === 'product-owner 1') return { approved: false, reasons: ['missing error case'] }
    if (/^product-owner/.test(label)) return { approved: true, reasons: ['ok'] }
    if (/^architect-planner|^load tasks/.test(label)) {
      return { ok: true, securitySensitive: false, waves: [
        { wave: 1, tasks: [TASK('T01', { complexity: 'high', risk: 'security' }), TASK('T02', { depends: ['T01'], complexity: 'low', risk: 'none' })] },
        { wave: 2, tasks: [TASK('T03', { components: ['b'], files: ['b/x.ts'], parallel: true }), TASK('T04', { components: ['c'], files: ['c/y.ts'], parallel: true, risk: 'concurrency' })] },
      ] }
    }
    if (/^test-author F/.test(label)) return { ok: true, components: ['a', 'b'], testsAdded: ['tests/a.test.ts', 'tools/x/test_b.py'], summary: 'red' }
    if (label === 'red-check #1') return { compiles: true, failingTests: 3, unexpectedPasses: [], mustWithoutTest: ['REQ-A-R011'] }
    if (label === 'red-check #2') return { compiles: true, failingTests: 4, unexpectedPasses: ['t0'], mustWithoutTest: [], outsideScan: ['tools/x/test_b.py'] }
    if (label === 'test-author fix') return { ok: true, components: ['a'], testsAdded: ['tests/c.test.ts'], summary: 'fixed' }
    if (label === 'T01 impl') return { ok: false, tasksDone: [], tasksFailed: ['T01'], testDefects: ['t1 wrong per section 5'], summary: 'test defect' }
    if (/^T\d+ impl/.test(label)) return { ok: true, tasksDone: [label.slice(0, 3)], tasksFailed: [], summary: 'ok' }
    if (/^wave \d lane \d$/.test(label)) return { ok: true, tasksDone: [label.endsWith('1') ? 'T03' : 'T04'], tasksFailed: [], headSha: 'lane' + label.slice(-1), branch: 'wip/x', worktreePath: '/proj/.wt/x', summary: 'ok' }
    if (/^merge/.test(label)) return { ok: true, merged: ['wip/x'], conflicted: [], headSha: 'm' }
    if (/^gates post-implement #[12]/.test(label)) return { pass: false, infraError: false, failed: [{ gate: 'test', excerpt: 'assertion failed' }] }
    if (/^gates /.test(label)) return { pass: true, infraError: false, failed: [] }
    if (label === 'spec-compliance-reviewer') return { securitySensitive: true, findings: [{ severity: 'blocking', category: 'compliance', reqId: 'REQ-A-R010', file: 'a/T01.ts', line: 3, claim: 'wrong | status', evidence: 'quote' }] }
    if (label === 'test-adequacy-reviewer' && k === 1) return null // exercises the reviewer retry
    if (/^security-reviewer/.test(label)) return { securitySensitive: true, findings: [{ severity: 'blocking', category: 'dos', file: 'a/T02.ts', line: 9, claim: 'unbounded alloc', evidence: 'len' }] }
    if (/reviewer/.test(label)) return { securitySensitive: false, findings: [{ severity: 'non-blocking', category: 'style', file: 'b/x.ts', line: 1, claim: 'naming', evidence: 'x' }] }
    if (/^verify R1/.test(label)) return { refuted: false, confidence: 'high', reasoning: 'real' }
    if (/^verify /.test(label)) return { refuted: true, confidence: 'medium', reasoning: 'not real' }
    if (/^fix-check /.test(label)) return { refuted: false, confidence: 'high', reasoning: 'fixed' }
    if (/^finalize/.test(label)) return { ok: true, traceCheck: 'pass', status: 'Implemented', commit: 'fin' }
    if (/^test-author|^test-arbiter|^spec-|^fix |^gate-fix|^review findings|^verify record|^merge-resolve/.test(label)) return { ok: true, summary: `done ${label}`, testDefects: [] }
    return { ok: true, summary: `done ${label}` }
  }
}

async function run(src, args, scenario = {}) {
  const calls = []
  const respond = responder(scenario)
  const agent = async (prompt, opts) => {
    calls.push({ prompt, opts: JSON.stringify(opts), label: opts.label, model: opts.model, effort: opts.effort })
    return respond(opts)
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
  const summary = await compile(src)({ config: CONFIG, feature: 'F011', slug: 'x', ...args }, agent, parallel, pipeline, () => {}, () => {})
  return { calls, summary }
}

let checks = 0
const ok = (m) => { checks++; console.log(`ok - ${m}`) }

// 1. Byte-identical without the new args ------------------------------------------------------------------
const ROOT = '/proj/.wt/F011'
const ARGSETS = [
  ['tiers, agent approval, worktree root', { approval: 'agent', root: ROOT }],
  ['tiers, hard, notes', { approval: 'agent', root: ROOT, hard: true, notes: 'keep it small' }],
  ['main checkout, worktree lanes, readViaGit', { approval: 'agent', readViaGit: true }],
  ['main checkout, auto isolation', { approval: 'agent' }],
  ['orchestrator approval (stops at approval)', {}],
  ['formula routing', { approval: 'agent', root: ROOT, config: Object.assign({}, CONFIG, { routing: FORMULA }) }],
  ['stages from implement, testsSha', { root: ROOT, stages: ['implement', 'gates', 'review', 'verify', 'finalize'], testsSha: 'tests000' }, { approved: true }],
  ['stages verify+finalize (no findings)', { root: ROOT, stages: ['verify', 'finalize'] }, { approved: true }],
]
const SCENARIOS = [['normal', {}], ['capped pool fails', { capped: true }]]
let compared = 0
if (baselineSrc) {
  for (const [name, args, extra] of ARGSETS) {
    for (const [sname, scen] of SCENARIOS) {
      const s = Object.assign({}, scen, extra || {})
      const before = await run(baselineSrc, args, s)
      const after = await run(currentSrc, args, s)
      assert.equal(after.calls.length, before.calls.length, `${name} / ${sname}: call count`)
      for (let i = 0; i < after.calls.length; i++) {
        assert.equal(after.calls[i].opts, before.calls[i].opts, `${name} / ${sname}: opts differ at call ${i} (${after.calls[i].label})`)
        assert.equal(after.calls[i].prompt, before.calls[i].prompt, `${name} / ${sname}: prompt differs at call ${i} (${after.calls[i].label})`)
      }
      assert.deepEqual(after.summary, before.summary, `${name} / ${sname}: summary differs`)
      compared += after.calls.length
      ok(`absent new args: ${name} / ${sname}: ${after.calls.length} agent calls identical to ${BASE_REV}`)
    }
  }
  // The scenarios must reach the interesting paths, or the comparison proves little.
  const r = await run(currentSrc, ARGSETS[0][1])
  for (const l of ['red-check #2', 'test-author fix', 'T01 impl retry', 'test-arbiter post-implement #2', 'security-reviewer (independent #2)', 'verify R1 #1', 'fix R1', 'fix-check R1 #2', 'finalize F011']) {
    assert.ok(r.calls.some((c) => c.label === l), `the normal scenario reaches ${l}`)
  }
  const capped = await run(currentSrc, ARGSETS[5][1], { capped: true })
  assert.ok(capped.calls.some((c) => / \(fallback\)$/.test(c.label)), 'the capped scenario exercises the fallback')
  ok('the scenarios reach retries, the test arbiter, the second security review, verify, fixes, fix-checks and the cap fallback')
}

// 2. Intended changes with the new args ---------------------------------------------------------------------
const REF = ARGSETS[0][1]
const ref = await run(currentSrc, REF)

// 2a. redCheckTags: only the red-check calls change (appended rule, schema with tagOnly).
{
  for (const [name, args] of [['arg', Object.assign({}, REF, { redCheckTags: true })], ['config', Object.assign({}, REF, { config: Object.assign({}, CONFIG, { pipeline: Object.assign({}, CONFIG.pipeline, { red_check_tags: true }) }) })]]) {
    const r = await run(currentSrc, args)
    assert.equal(r.calls.length, ref.calls.length)
    let changed = 0
    for (let i = 0; i < r.calls.length; i++) {
      if (/^red-check #\d/.test(r.calls[i].label)) {
        const p = r.calls[i].prompt
        assert.ok(p.startsWith(ref.calls[i].prompt + '\nTAG CROSS-CHECK'), `rule appended (${name})`)
        assert.ok(p.endsWith('`trace.scan_globs`).'))
        for (const s of ['WHOLE repository', 'Verifies', '-g \'!build/**\'', '-g \'!.wt/**\'', '-g \'!**/node_modules/**\'', 'several ids', 'NEITHER', 'tagOnly']) assert.ok(p.includes(s), `rule mentions ${s}`)
        const o = JSON.parse(r.calls[i].opts)
        const o0 = JSON.parse(ref.calls[i].opts)
        assert.ok(o.schema.properties.tagOnly && !o0.schema.properties.tagOnly)
        delete o.schema
        delete o0.schema
        assert.deepEqual(o, o0)
        changed++
      } else {
        assert.equal(r.calls[i].opts, ref.calls[i].opts, `opts unchanged: ${r.calls[i].label}`)
        assert.equal(r.calls[i].prompt, ref.calls[i].prompt, `prompt unchanged: ${r.calls[i].label}`)
      }
    }
    assert.equal(changed, 2)
  }
  ok('redCheckTags (arg or pipeline.red_check_tags): both red-check prompts get the repository-wide tag search; everything else identical')

  // The search command works on a fixture tree: multi-id and multi-line lists, a doc-comment tag, excluded dirs.
  const r = await run(currentSrc, Object.assign({}, REF, { redCheckTags: true }))
  const cmd = /^2\. [^`]*`([^`]+)`/m.exec(r.calls.find((c) => c.label === 'red-check #1').prompt)[1]
  const hasRg = (() => { try { execFileSync('rg', ['--version'], { stdio: 'ignore' }); return true } catch { return false } })()
  if (!hasRg) console.log('skip - rg not installed: tag search command not executed')
  else {
    mkdirSync(path.join(KIT, '.scratch'), { recursive: true }) // git-ignored; never /tmp
    const tmp = mkdtempSync(path.join(KIT, '.scratch', 'redcheck-'))
    try {
      const w = (rel, text) => { mkdirSync(path.dirname(path.join(tmp, rel)), { recursive: true }); writeFileSync(path.join(tmp, rel), text) }
      w('tools/x/test_tool.py', '@verifies("REQ-A-R010", "REQ-A-R011")\ndef test_a():\n    pass\n# Verifies: REQ-B-R020, REQ-B-R021\n')
      w('tests/b.test.ts', '// verifies(\n//   "REQ-C-R030",\n//   "REQ-C-R031")\nit("x", () => {})\n// this verifies nothing REQ-D-R040\n')
      w('build/gen.py', '# Verifies: REQ-E-R050\n')
      w('node_modules/m/i.js', '// Verifies: REQ-E-R051\n')
      const out = execFileSync('sh', ['-c', cmd], { cwd: tmp, encoding: 'utf8' }).trim().split('\n')
      assert.deepEqual(out, ['REQ-A-R010', 'REQ-A-R011', 'REQ-B-R020', 'REQ-B-R021', 'REQ-C-R030', 'REQ-C-R031'])
      ok('redCheckTags search command: ids from attribute, multi-line and doc-comment lists anywhere in the tree; prose and excluded dirs ignored')
    } finally {
      rmSync(tmp, { recursive: true, force: true })
    }
  }
}

// 2b. persistFindings: machine-readable block in the existing record calls, verdicts before fixes, reload on resume.
{
  const r = await run(currentSrc, Object.assign({}, REF, { persistFindings: true }))
  const EARLY = /^verify record$/
  // Every call except the extra verify record (before the fixes) matches; record and finalize prompts only gain text.
  const extra = r.calls.findIndex((c, i) => EARLY.test(c.label) && r.calls.slice(i + 1).some((d) => /^fix R1/.test(d.label)))
  assert.ok(extra > 0, 'a verify record is written before the first fix')
  const rest = r.calls.filter((_, i) => i !== extra)
  assert.equal(rest.length, ref.calls.length)
  for (let i = 0; i < rest.length; i++) {
    assert.equal(rest[i].opts, ref.calls[i].opts, `opts unchanged: ${rest[i].label}`)
    if (/^(review findings|verify record)$/.test(rest[i].label)) assert.ok(rest[i].prompt.startsWith(ref.calls[i].prompt + '\n\nAlso replace the machine-readable findings block'), rest[i].label)
    else if (/^finalize/.test(rest[i].label)) assert.ok(rest[i].prompt.includes('"<!-- pipeline-findings" block'))
    else assert.equal(rest[i].prompt, ref.calls[i].prompt, `prompt unchanged: ${rest[i].label}`)
  }
  const block = (c) => JSON.parse(/```json\n([\s\S]*?)\n```/.exec(c.prompt)[1])
  const out = (c) => Object.fromEntries(block(c).map((x) => [x.id, x.outcome]))
  const rf = r.calls.find((c) => c.label === 'review findings')
  const records = r.calls.filter((c) => c.label === 'verify record')
  assert.deepEqual(out(rf), { R1: 'pending verification', R2: 'pending verification', R3: 'non-blocking' })
  assert.deepEqual(out(records[0]), { R1: 'confirmed, fix pending', R2: 'refuted', R3: 'non-blocking' })
  assert.deepEqual(out(records[1]), { R1: 'confirmed, fixed', R2: 'refuted', R3: 'non-blocking' })
  const r1 = block(records[1])[0]
  for (const k of ['id', 'reviewer', 'severity', 'location', 'claim', 'votes', 'outcome']) assert.ok(k in r1, `record has ${k}`)
  assert.equal(r1.votes.length, 2)
  ok('persistFindings: review findings and verify records carry the findings block; verdicts are written before the fixes; outcomes pending -> confirmed/refuted -> fixed')

  // Resume from "verify": findings reload from review.md; refuted/fixed ones keep their outcome and are not verified again.
  const persisted = block(records[0]).map((x) => (x.id === 'R1' ? Object.assign({}, x, { outcome: 'confirmed, unresolved' }) : x))
  persisted.push({ id: 'R4', reviewer: 'perf-reviewer', severity: 'blocking', category: 'perf', file: 'c/y.ts', line: 5, claim: 'O(n) per request', evidence: 'loop', location: 'c/y.ts:5', votes: [], outcome: 'confirmed, fixed' })
  const v = await run(currentSrc, Object.assign({}, REF, { persistFindings: true, stages: ['verify', 'finalize'] }), { persisted, approved: true })
  const labels = v.calls.map((c) => c.label)
  assert.ok(labels.indexOf('load persisted findings') > 0 && labels.indexOf('load persisted findings') < labels.indexOf('verify R1 #1'))
  assert.deepEqual(labels.filter((l) => /^verify R/.test(l)), ['verify R1 #1', 'verify R1 #2'])
  const fin = v.calls.find((c) => /^finalize/.test(c.label)).prompt
  assert.ok(fin.includes('"verifier": "refuted (persisted)"') && fin.includes('"verifier": "confirmed, fixed (persisted)"'))
  assert.ok(!v.calls.find((c) => c.label === 'verify R1 #1').prompt.includes('"outcome"'), 'verifiers get the finding without the bookkeeping fields')
  assert.equal(v.summary.findings.total, 4)
  const lost = await run(currentSrc, Object.assign({}, REF, { stages: ['verify', 'finalize'] }), { persisted, approved: true })
  assert.ok(!lost.calls.some((c) => /^(load persisted|verify R)/.test(c.label)), 'without the option the resumed run has no findings')
  ok('persistFindings resume from "verify": findings reload; refuted/fixed keep their outcome, the unresolved one is verified again')
}

console.log(`\npipeline cache keys: ${checks} checks passed${baselineSrc ? ` (${compared} agent calls compared byte for byte against ${BASE_REV})` : ''}`)
