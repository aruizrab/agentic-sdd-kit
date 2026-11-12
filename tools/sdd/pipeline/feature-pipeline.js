export const meta = {
  name: 'feature-pipeline',
  description: 'Spec-driven lifecycle for one roadmap feature: specify, critique, approve, plan, tests-first, implement in waves, gates, review, adversarial verify, finalize',
  whenToUse:
    'Orchestrator runs this per feature with args {feature: "F011", slug: "user-login", config?: <sdd config --pipeline>, root?: "<abs worktree path>", stages?: [...], approval?: "orchestrator"|"agent", isolation?: "auto"|"none"|"worktree", security?: bool, hard?: bool, notes?: string, models?: {...}, modelsFromPhase?: "Gates"}. See docs/PLAYBOOK.md.',
  phases: [
    { title: 'Setup', detail: 'load sdd.config.json (unless passed as args.config), feature branch/worktree, scaffold the feature directory' },
    { title: 'Specify', detail: 'spec-author writes spec.md (atomic requirements with ids) from the normative source, within the requirement budget' },
    { title: 'Critic', detail: 'spec-critic verifies citations/coverage/testability; convergence rounds; product-owner resolution when it does not converge' },
    { title: 'Approve', detail: 'product owner: orchestrator (default, pipeline stops) or a product-owner agent sets Status: Approved (one fix pass on rejection)' },
    { title: 'Plan', detail: 'architect-planner writes design.md and tasks.md with waves; sdd tasks --validate checks the plan' },
    { title: 'Tests', detail: 'test-author writes failing tests; integrator confirms red' },
    { title: 'Implement', detail: 'per wave: same-component tasks sequential, disjoint-component lanes in parallel worktrees, then merge (manifests union, sources semantic)' },
    { title: 'Gates', detail: 'sdd gates (detached, GATES_EXIT marker); fixer loop; test-arbiter after 2 failing test gates' },
    { title: 'Review', detail: 'spec-compliance, security, test-adequacy, perf reviewers in parallel (+ independent 2nd security review if sensitive)' },
    { title: 'Verify', detail: 'N skeptical verifiers per blocking finding; confirmed findings fixed, re-gated, fix-checked (twice if security-sensitive)' },
    { title: 'Finalize', detail: 'review.md, Status: Implemented, sdd trace --check, conventional commit; merge is left to the orchestrator' },
  ],
}

// ---------------------------------------------------------------------------
// Arguments
// ---------------------------------------------------------------------------
const A = args || {}
const PHASE_ORDER = ['Setup', 'Specify', 'Critic', 'Approve', 'Plan', 'Tests', 'Implement', 'Gates', 'Review', 'Verify', 'Finalize']
const ALL_STAGES = ['specify', 'critic', 'approve', 'plan', 'tests', 'implement', 'gates', 'review', 'verify', 'finalize']

const idMatch = /^[A-Za-z]*(\d{1,4})$/.exec(String(A.feature || '').trim())
if (!idMatch || typeof A.slug !== 'string' || !/^[a-z0-9][a-z0-9-]*$/.test(A.slug)) {
  throw new Error('feature-pipeline: args must be {feature: "F011", slug: "kebab-case-slug", ...}')
}
const requested = Array.isArray(A.stages) && A.stages.length ? A.stages : ALL_STAGES
const unknown = requested.filter((s) => !ALL_STAGES.includes(s))
if (unknown.length) throw new Error(`feature-pipeline: unknown stages ${unknown.join(', ')}; valid: ${ALL_STAGES.join(', ')}`)
const STAGES = new Set(requested)
const want = (s) => STAGES.has(s)
const APPROVAL = A.approval === 'agent' ? 'agent' : 'orchestrator'
const ROLE_FALLBACK = A.roleFallback !== false
const TIERING = A.tiering !== false
const PAUSED = { value: false }

// ---------------------------------------------------------------------------
// Configuration (sdd.config.json): passed as args.config, else loaded by a mechanical agent.
// Nothing project-specific is hard-coded below this block.
// ---------------------------------------------------------------------------
phase('Setup')
let C = A.config && typeof A.config === 'object' ? A.config : null
const ROOT_ARG = typeof A.root === 'string' && A.root.startsWith('/') ? A.root : null
if (!C) {
  const boot = await agent(
    `Run \`python3 -I ${ROOT_ARG ? ROOT_ARG : '.'}/tools/sdd/sdd.py config --pipeline\` (or \`sdd config --pipeline\`) ` +
      `from the project root${ROOT_ARG ? ` (worktree ${ROOT_ARG})` : ' (the current checkout)'} and return its stdout verbatim as configJson. Do not modify anything.`,
    { label: 'load sdd.config.json', phase: 'Setup', effort: 'low', schema: { type: 'object', properties: { ok: { type: 'boolean' }, configJson: { type: 'string' } }, required: ['ok', 'configJson'] } },
  )
  if (!boot || !boot.ok) throw new Error('feature-pipeline: could not load sdd.config.json; pass it as args.config (sdd config --pipeline)')
  C = JSON.parse(boot.configJson)
}
const PROJECT_DIR = typeof A.projectDir === 'string' && A.projectDir.startsWith('/') ? A.projectDir : C.root
const ROOT = ROOT_ARG
const WHERE = ROOT
  ? `the feature worktree at ${ROOT} (first \`cd ${ROOT}\`; run every command and resolve every path there, NOT in the main checkout ${PROJECT_DIR})`
  : `the main checkout at ${PROJECT_DIR}`
const TOOLS = `${ROOT || PROJECT_DIR}/${C.paths.tools_dir || 'tools/sdd'}`
const SDD = `python3 -I ${TOOLS}/sdd.py`
const AGENTS_DIR = typeof A.agentsDir === 'string' ? A.agentsDir : `${PROJECT_DIR}/.claude/agents`

const NUM = idMatch[1].padStart(C.features.id_digits || 3, '0')
const FID = (C.features.id_prefix || 'F') + NUM
const SLUG = A.slug
const DIR = `${C.paths.features_dir}/${NUM}-${SLUG}`
const BRANCH = A.branch || `${C.features.branch_prefix || 'feat/'}${FID}-${SLUG}`
const ISOLATION = ROOT ? 'none' : ['auto', 'none', 'worktree'].includes(A.isolation) ? A.isolation : 'auto'
const P = C.pipeline || {}
const MAX_CRITIC_FIX_ROUNDS = P.max_critic_fix_rounds != null ? P.max_critic_fix_rounds : 2
const MAX_GATE_FIX_ROUNDS = P.max_gate_fix_rounds != null ? P.max_gate_fix_rounds : 3
const SKEPTICS = P.skeptics || 2
const POLL = P.gate_poll_seconds || 120
const TRAILERS = (C.commit && C.commit.trailers) || []
const TRAILER_TEXT = TRAILERS.length ? `the trailer line(s):\n${TRAILERS.join('\n')}` : 'no trailer'
const BUDGET = (C.requirements && C.requirements.budget) || { min: 15, max: 45 }
const MARKER = (C.requirements && C.requirements.clarification_marker) || '[NEEDS CLARIFICATION:'
const SRC = C.sources || {}
const SOURCE_RULE = SRC.lookup_command || SRC.text_dir
  ? `Normative source (${SRC.kind || 'requirements'}${SRC.description ? ': ' + SRC.description : ''}): read it with \`${SDD} source clause <doc> <clause>\` / \`${SDD} source find <doc> <regex>\`; never cite it from memory. Citation format: ${SRC.citation_format}.`
  : `Normative source (${SRC.kind || 'product requirements'}${SRC.description ? ': ' + SRC.description : ''}): cite as ${SRC.citation_format}; never invent requirements.`
const TESTS_RUN = (C.tests && C.tests.run) || ''
const TESTS_COMPONENT = (C.tests && C.tests.run_component) || ''
const SECURITY_HINT = P.security_sensitive_hint || 'authentication, authorization, cryptography, secrets, session/token handling, parsing of untrusted input'
const COMPONENT_WORD = (C.components && C.components.word) || 'component'
const WT_ROOT = `${PROJECT_DIR}/${C.paths.worktree_root || '.wt'}`

// ---------------------------------------------------------------------------
// Model tiering by role (explicit ids from config.models; empty = inherit the session model).
// Tier changes on a resumed run apply from args.modelsFromPhase onward; earlier phases keep
// args.modelsBefore (default: config.models) so cached agent calls still match.
// ---------------------------------------------------------------------------
const MODELS_NOW = Object.assign({}, C.models || {}, A.models || {})
const MODELS_BEFORE = Object.assign({}, C.models || {}, A.modelsBefore || {})
const MODELS_FROM = A.modelsFromPhase ? Math.max(0, PHASE_ORDER.indexOf(A.modelsFromPhase)) : 0
const ROLE_TIER = Object.assign(
  {
    integrator: 'mid',
    implementer: 'mid',
    'perf-reviewer': 'mid',
    'test-adequacy-reviewer': 'mid',
    'spec-author': 'strong',
    'spec-critic': 'strong',
    'product-owner': 'strong',
    'architect-planner': 'strong',
    'test-author': 'strong',
    'spec-compliance-reviewer': 'strong',
    'security-reviewer': 'strong',
    verifier: 'strong',
  },
  P.role_tiers || {},
)
function tierFor(opts) {
  const role = opts.agentType || ''
  const label = String(opts.label || '')
  if (/^test-arbiter /.test(label) || /^gate-fix .* #([2-9]|\d\d)/.test(label) || /^product-owner resolve 2/.test(label) || /retry$/.test(label) && role === 'implementer') return 'top'
  if (A.hard === true && ['spec-author', 'spec-critic', 'architect-planner', 'verifier', 'security-reviewer'].includes(role)) return 'top'
  if (role === 'integrator' && opts.effort === 'low') return 'mechanical'
  if (/^finalize /.test(label)) return 'mid'
  return ROLE_TIER[role] || 'strong'
}
function pickModel(opts) {
  if (!TIERING || !opts || opts.model) return opts && opts.model
  const models = PHASE_ORDER.indexOf(opts.phase) >= MODELS_FROM ? MODELS_NOW : MODELS_BEFORE
  const id = models[tierFor(opts)]
  return id ? id : undefined
}

// ---------------------------------------------------------------------------
// Resilient agent call: role fallback, one retry, then pause the run (resumable from the journal).
// ---------------------------------------------------------------------------
function agentR(prompt, opts) {
  const m = pickModel(opts)
  if (m) opts = Object.assign({}, opts, { model: m })
  if (opts && opts.agentType && ROLE_FALLBACK) {
    const role = opts.agentType
    opts = Object.assign({}, opts, { agentType: 'general-purpose' })
    prompt = `ROLE: you are the "${role}" agent of ${C.project.name}. FIRST read ${AGENTS_DIR}/${role}.md and follow it exactly (scope, boundaries, allowed tools, output rules) for the rest of this task.\n\n` + prompt
  }
  if (PAUSED.value) return Promise.resolve(null)
  const attempt = (o) => agent(prompt, o).catch((e) => { log(`agent failed: ${(o && o.label) || 'agent'}: ${String(e).slice(0, 160)}`); return null })
  return attempt(opts)
    .then((r) => (r === null || r === undefined ? attempt(Object.assign({}, opts, { label: `${(opts && opts.label) || 'agent'} (retry)` })) : r))
    .then((r) => {
      if (r === null || r === undefined) { PAUSED.value = true; log(`pausing run: ${(opts && opts.label) || 'agent'} failed twice (usage limit or outage); resume with the same args`) }
      return r
    })
}

const CTX = [
  `Project: ${C.project.name}${C.project.description ? ' (' + C.project.description + ')' : ''}. Feature ${FID} "${SLUG}".`,
  `Working copy: ${WHERE}.`,
  `Feature directory: ${DIR}/ (spec.md, design.md, tasks.md, review.md). Feature branch: ${BRANCH}.`,
  `Binding rules: ${C.paths.constitution}. Process: ${C.paths.development || 'docs/DEVELOPMENT.md'}. Tools: \`${SDD} <command>\` (trace, gates, tasks, spec-info, source, new-feature).`,
  SOURCE_RULE,
  C.project.threat_model ? `Threat model (binding; prevents over-engineering): ${C.project.threat_model}` : '',
  `Commit messages: Conventional Commits scoped "${FID}" (e.g. feat(${FID}): T03 ...), ending with ${TRAILER_TEXT}. Never --no-verify, never force-push.`,
  'SHARED-TREE SAFETY (hard rule): other agents may be editing this working tree concurrently. NEVER run `git checkout -- <path|.>`, `git restore`, ' +
    '`git clean`, `git reset --hard`, `git stash`, `git switch`/`checkout <branch>`, or `rm` on files you did not create in this task. To revert your own ' +
    'scratch edits, undo exactly those edits. Read-only roles (reviewers, verifiers, fix-checks) must not edit tracked files at all: for experiments create a ' +
    `detached worktree (\`git worktree add --detach ${WT_ROOT}/exp-<unique> HEAD\`), work there, then \`git worktree remove --force\` it.`,
  `DISK RULE: never put build output, worktrees or large files under /tmp (small RAM tmpfs); use the project's build directory inside ${ROOT || PROJECT_DIR} and delete what you created when done. Cap parallel build jobs at ${P.max_build_jobs || 4}.`,
].filter(Boolean).concat(A.notes ? [`Orchestrator notes (apply in every stage): ${A.notes}`] : []).join('\n')

const summary = {
  feature: FID,
  slug: SLUG,
  dir: DIR,
  branch: BRANCH,
  root: ROOT,
  stages: requested,
  approval: APPROVAL,
  isolation: ISOLATION,
  status: 'running',
  reason: null,
  baseSha: null,
  testsSha: A.testsSha || null,
  critic: [],
  waves: [],
  gates: [],
  findings: { total: 0, blocking: 0, confirmed: 0, refuted: 0, fixed: 0, unresolved: [] },
  reviewGaps: [],
  securitySensitive: A.security === true,
  needsSecuritySignoff: false,
  perfException: null,
  final: null,
  nextAction: null,
}

function finish(status, reason, nextAction) {
  if (PAUSED.value) {
    reason = `agents stopped failing-fast after a usage limit or outage (was: ${status}${reason ? ' - ' + reason : ''})`
    status = 'paused'
    nextAction = 'Resume with Workflow({scriptPath, resumeFromRunId, args: <same args>}) once usage is available again. To change model tiers for the remaining phases pass models + modelsFromPhase (and modelsBefore for the completed ones).'
  }
  summary.status = status
  summary.reason = reason || null
  summary.nextAction = nextAction || null
  log(`${FID}: ${status}${reason ? ' - ' + reason : ''}`)
  return summary
}

// ---------------------------------------------------------------------------
// Schemas
// ---------------------------------------------------------------------------
const STEP = {
  type: 'object',
  properties: {
    ok: { type: 'boolean' },
    summary: { type: 'string' },
    files: { type: 'array', items: { type: 'string' } },
    commits: { type: 'array', items: { type: 'string' } },
    testDefects: { type: 'array', items: { type: 'string' }, description: 'tests believed wrong against the spec/source, with the citation; empty if none' },
  },
  required: ['ok', 'summary'],
}
const SETUP = {
  type: 'object',
  properties: {
    ok: { type: 'boolean' },
    branch: { type: 'string' },
    baseSha: { type: 'string', description: 'merge-base of the feature branch with main' },
    headSha: { type: 'string' },
    scaffolded: { type: 'boolean' },
    notes: { type: 'string' },
  },
  required: ['ok', 'branch', 'baseSha', 'headSha'],
}
const SHA = { type: 'object', properties: { ok: { type: 'boolean' }, sha: { type: 'string' }, notes: { type: 'string' } }, required: ['ok', 'sha'] }
const CRITIC = {
  type: 'object',
  properties: {
    verdict: { type: 'string', enum: ['pass', 'revise'] },
    issues: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          severity: { type: 'string', enum: ['blocking', 'major', 'minor'] },
          kind: { type: 'string', enum: ['citation', 'coverage', 'abnormal-case', 'atomicity', 'testability', 'ambiguity', 'contradiction', 'scope', 'format', 'observability', 'other'] },
          reqId: { type: 'string' },
          location: { type: 'string' },
          problem: { type: 'string' },
          evidence: { type: 'string', description: 'quoted source text with file:line' },
          suggestion: { type: 'string' },
        },
        required: ['severity', 'kind', 'problem'],
      },
    },
  },
  required: ['verdict', 'issues'],
}
const STATUS = {
  type: 'object',
  properties: {
    status: { type: 'string', enum: ['Draft', 'Approved', 'Implemented', 'Missing'] },
    needsClarification: { type: 'integer' },
    requirements: { type: 'integer' },
  },
  required: ['status', 'needsClarification'],
}
const APPROVAL_SCHEMA = { type: 'object', properties: { approved: { type: 'boolean' }, reasons: { type: 'array', items: { type: 'string' } } }, required: ['approved', 'reasons'] }
const TASK = {
  type: 'object',
  properties: {
    id: { type: 'string' },
    title: { type: 'string' },
    components: { type: 'array', items: { type: 'string' } },
    files: { type: 'array', items: { type: 'string' } },
    verifies: { type: 'array', items: { type: 'string' } },
    depends: { type: 'array', items: { type: 'string' } },
    parallel: { type: 'boolean' },
  },
  required: ['id', 'title', 'components', 'files', 'verifies', 'depends', 'parallel'],
}
const PLAN = {
  type: 'object',
  properties: {
    ok: { type: 'boolean' },
    securitySensitive: { type: 'boolean' },
    waves: { type: 'array', items: { type: 'object', properties: { wave: { type: 'integer' }, tasks: { type: 'array', items: TASK } }, required: ['wave', 'tasks'] } },
    problems: { type: 'array', items: { type: 'string' } },
    notes: { type: 'string' },
  },
  required: ['ok', 'securitySensitive', 'waves'],
}
const TESTS = {
  type: 'object',
  properties: {
    ok: { type: 'boolean' },
    components: { type: 'array', items: { type: 'string' } },
    testsAdded: { type: 'array', items: { type: 'string' } },
    fixturesAdded: { type: 'array', items: { type: 'string' } },
    alreadyPassing: { type: 'array', items: { type: 'string' } },
    summary: { type: 'string' },
  },
  required: ['ok', 'components', 'testsAdded', 'summary'],
}
const REDCHECK = {
  type: 'object',
  properties: {
    compiles: { type: 'boolean' },
    failingTests: { type: 'integer' },
    unexpectedPasses: { type: 'array', items: { type: 'string' } },
    mustWithoutTest: { type: 'array', items: { type: 'string' } },
    excerpt: { type: 'string' },
  },
  required: ['compiles', 'failingTests', 'unexpectedPasses', 'mustWithoutTest'],
}
const IMPL = {
  type: 'object',
  properties: {
    ok: { type: 'boolean' },
    tasksDone: { type: 'array', items: { type: 'string' } },
    tasksFailed: { type: 'array', items: { type: 'string' } },
    branch: { type: 'string' },
    headSha: { type: 'string' },
    worktreePath: { type: 'string' },
    commits: { type: 'array', items: { type: 'string' } },
    testDefects: { type: 'array', items: { type: 'string' } },
    summary: { type: 'string' },
  },
  required: ['ok', 'tasksDone', 'tasksFailed', 'summary'],
}
const MERGE = {
  type: 'object',
  properties: {
    ok: { type: 'boolean' },
    merged: { type: 'array', items: { type: 'string' } },
    conflicted: { type: 'array', items: { type: 'string' }, description: 'files still conflicted (manifests may be union-merged with sdd union-merge; anything else is left for semantic resolution)' },
    headSha: { type: 'string' },
    notes: { type: 'string' },
  },
  required: ['ok', 'merged', 'conflicted', 'headSha'],
}
const GATE = {
  type: 'object',
  properties: {
    pass: { type: 'boolean' },
    infraError: { type: 'boolean', description: 'true if the gate tooling itself is missing or broken' },
    failed: { type: 'array', items: { type: 'object', properties: { gate: { type: 'string' }, excerpt: { type: 'string' } }, required: ['gate', 'excerpt'] } },
    logPath: { type: 'string' },
  },
  required: ['pass', 'infraError', 'failed'],
}
const FINDINGS = {
  type: 'object',
  properties: {
    securitySensitive: { type: 'boolean' },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          severity: { type: 'string', enum: ['blocking', 'non-blocking'] },
          category: { type: 'string' },
          reqId: { type: 'string' },
          source: { type: 'string', description: 'cited clause / contract section' },
          file: { type: 'string' },
          line: { type: 'integer' },
          claim: { type: 'string' },
          evidence: { type: 'string' },
          suggestedFix: { type: 'string' },
        },
        required: ['severity', 'category', 'file', 'line', 'claim', 'evidence'],
      },
    },
  },
  required: ['securitySensitive', 'findings'],
}
const VERDICT = {
  type: 'object',
  properties: { refuted: { type: 'boolean' }, confidence: { type: 'string', enum: ['low', 'medium', 'high'] }, reasoning: { type: 'string' }, reproduction: { type: 'string' } },
  required: ['refuted', 'confidence', 'reasoning'],
}
const FINAL = {
  type: 'object',
  properties: {
    ok: { type: 'boolean' },
    traceCheck: { type: 'string', enum: ['pass', 'fail', 'not-run'] },
    status: { type: 'string', enum: ['Draft', 'Approved', 'Implemented'] },
    commit: { type: 'string' },
    notes: { type: 'string' },
  },
  required: ['ok', 'traceCheck', 'status', 'commit'],
}

// ---------------------------------------------------------------------------
// Helpers (all side effects go through agents)
// ---------------------------------------------------------------------------
async function commit(message, ph) {
  const r = await agentR(
    `${CTX}\n\nIn ${WHERE} on branch ${BRANCH}: if \`git status --porcelain\` shows changes (ignore build output and ${C.paths.worktree_root || '.wt'}/), ` +
      `stage them and commit with subject "${message}", a blank line, then ${TRAILER_TEXT}. If there is nothing to commit, do nothing. ` +
      'Return ok and the resulting `git rev-parse HEAD`.',
    { label: `commit: ${message}`, phase: ph, schema: SHA, agentType: 'integrator', effort: 'low' },
  )
  return r && r.ok ? r.sha : null
}

async function specStatus(ph) {
  return agentR(
    `${CTX}\n\nRun \`${SDD} spec-info ${DIR}\` in ${WHERE} (do not modify anything) and report its "status", "needsClarification" and "requirements" fields. ` +
      'The tool already ignores HTML comments, code spans and prose mentions of the marker; do not re-count by eye.',
    { label: 'spec status', phase: ph, schema: STATUS, agentType: 'integrator', effort: 'low' },
  )
}

async function loadPlan(ph) {
  return agentR(
    `${CTX}\n\nRun \`${SDD} tasks ${DIR} --validate\` in ${WHERE} and return its JSON as the plan (waves with tasks: id, title, components, files, verifies, depends, parallel; ` +
      'securitySensitive; problems). Done tasks ([x]) are already excluded by the tool. Do not modify anything.',
    { label: 'load tasks.md', phase: ph, schema: PLAN, agentType: 'integrator', effort: 'low' },
  )
}

// Group a wave's tasks into lanes: tasks sharing a component (or any non-[P] task) share a lane.
function buildLanes(tasks) {
  if (tasks.some((t) => !t.parallel)) return [{ components: ['*'], tasks: tasks.slice() }]
  const lanes = []
  for (const t of tasks) {
    const comps = t.components && t.components.length ? t.components : ['workspace']
    const hits = lanes.filter((l) => l.components.some((c) => comps.includes(c)))
    if (!hits.length) {
      lanes.push({ components: comps.slice(), tasks: [t] })
      continue
    }
    const into = hits[0]
    for (const other of hits.slice(1)) {
      into.components.push(...other.components.filter((c) => !into.components.includes(c)))
      into.tasks.push(...other.tasks)
      lanes.splice(lanes.indexOf(other), 1)
    }
    into.components.push(...comps.filter((c) => !into.components.includes(c)))
    into.tasks.push(t)
  }
  for (const l of lanes) l.tasks.sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))
  return lanes
}

function taskBlock(t) {
  return `- ${t.id} ${t.title} | ${COMPONENT_WORD}s: ${t.components.join(', ')} | files: ${t.files.join(', ')} | verifies: ${t.verifies.join(', ')} | depends: ${t.depends.join(', ') || '-'}`
}

async function testDefectRoute(defects, ph, tag) {
  if (!defects || !defects.length) return
  log(`${tag}: ${defects.length} reported test defect(s) routed to test-author`)
  await agentR(
    `${CTX}\n\nAn implementer reports these tests as defective (wrong against the spec / normative source):\n${defects.map((d) => '- ' + d).join('\n')}\n\n` +
      'Follow your "Test-defect reports" instructions: re-read each cited requirement and source, fix a test only if it is wrong against them ' +
      `(explain with a quote), otherwise state that the test is correct. Commit any test change as "test(${FID}): fix <test> per <citation>" with the trailer.`,
    { label: `test-defects ${tag}`, phase: ph, schema: STEP, agentType: 'test-author' },
  )
}

const GATE_RUN_TEXT =
  `Run the FULL gates with \`${SDD} gates --feature ${FID} --detached --tag <tag>\` at the root of ${WHERE} on ${BRANCH}; it prints the log path. ` +
  `A full run can take longer than one shell command may block, so poll with \`${SDD} gates --wait <log> --poll ${POLL}\` (repeat the wait if it times out) until the log ends with GATES_EXIT=<n>. ` +
  'NEVER report an incomplete run: no GATES_EXIT line means it is still running or was killed.'

async function gateLoop(tag, ph) {
  let last = null
  let testFails = 0
  for (let round = 0; round <= MAX_GATE_FIX_ROUNDS; round++) {
    last = await agentR(
      `${CTX}\n\n${GATE_RUN_TEXT.replace('<tag>', `${FID}-${tag}-${round + 1}`)} ` +
        'Report pass (GATES_EXIT=0), each failed gate with the shortest decisive excerpt from the log, and infraError=true only if the gate tooling itself is missing or broken. Do not fix anything.',
      { label: `gates ${tag} #${round + 1}`, phase: ph, schema: GATE, agentType: 'integrator', effort: 'low' },
    )
    if (!last) last = { pass: false, infraError: true, failed: [{ gate: 'runner', excerpt: 'gate agent returned nothing' }] }
    summary.gates.push({ tag, round: round + 1, pass: last.pass, failed: last.failed.map((f) => f.gate) })
    if (last.pass || last.infraError || round === MAX_GATE_FIX_ROUNDS) break
    const testGate = last.failed.some((f) => /test/i.test(f.gate))
    testFails = testGate ? testFails + 1 : 0
    const perfGate = last.failed.some((f) => /perf|bench/i.test(f.gate))
    const fix = await agentR(
      `${CTX}\n\nGates failed on ${BRANCH} (round ${round + 1}/${MAX_GATE_FIX_ROUNDS}). Failures:\n` +
        last.failed.map((f) => `### ${f.gate}\n${f.excerpt}`).join('\n') +
        `\n\nFix the root causes in product code following your implementer role and the constitution (never weaken tests, lints or thresholds; never edit test files owned by test-author). ` +
        (perfGate ? 'A PERFORMANCE regression is never fixed by loosening the threshold: either remove the regression or report it in testDefects as "perf-exception-needed: <measurement>" for an explicit product-owner decision. ' : '') +
        `Re-run \`${SDD} gates --fast --feature ${FID}\`, then commit as "fix(${FID}): gate failures (<gates>)" with the trailer. ` +
        'If a failure is caused by a wrong test, list it in testDefects with the citation instead of touching it.',
      { label: `gate-fix ${tag} #${round + 1}`, phase: ph, schema: STEP, agentType: 'implementer' },
    )
    if (fix) {
      const perfEx = (fix.testDefects || []).filter((d) => /perf-exception-needed/i.test(d))
      if (perfEx.length) summary.perfException = perfEx.join('; ')
      await testDefectRoute((fix.testDefects || []).filter((d) => !/perf-exception-needed/i.test(d)), ph, `gate-fix ${tag}`)
    }
    // Lesson 5: tests-first with implementers forbidden from editing tests deadlocks when a test or
    // snapshot is wrong. After 2 consecutive failing test gates a test-arbiter decides which side is wrong.
    if (testFails >= 2) {
      await agentR(
        `${CTX}\n\nThe test gate has failed ${testFails} rounds in a row on ${BRANCH}; implementers cannot edit tests. Arbitrate as test-author:\n` +
          last.failed.filter((f) => /test/i.test(f.gate)).map((f) => f.excerpt).join('\n') +
          `\n\nReproduce each failing test, then decide per test whether the TEST/expected value (snapshot, fixture, assertion) or the IMPLEMENTATION is wrong, ` +
          `judged against ${DIR}/spec.md and the cited normative source. Fix the wrong side minimally (review snapshots field by field; never blind-accept). ` +
          `Record the reasoning in a "Test amendments" note in ${DIR}/design.md. Run \`${SDD} gates --fast --feature ${FID}\` and commit with the trailer.`,
        { label: `test-arbiter ${tag} #${round + 1}`, phase: ph, schema: STEP, agentType: 'test-author' },
      )
    }
  }
  return last
}

// ---------------------------------------------------------------------------
// Setup (always)
// ---------------------------------------------------------------------------
const setup = await agentR(
  `${CTX}\n\nPrepare the feature workspace:\n` +
    (ROOT
      ? `1. If ${ROOT} does not exist: from the main checkout ${PROJECT_DIR} run \`git worktree add ${ROOT} ${BRANCH}\` if the branch exists, else \`git worktree add -b ${BRANCH} ${ROOT} main\`. Then \`cd ${ROOT}\`.\n` +
        `2. \`git status\` in ${ROOT} must show no uncommitted changes outside ${DIR}/ (otherwise ok=false, list them in notes). The checked-out branch must be ${BRANCH}.\n`
      : `1. \`git status\` must show no uncommitted changes outside ${DIR}/ (otherwise ok=false, list them in notes).\n` +
        `2. If the current branch is not ${BRANCH}: switch to it if it exists, else create it from the current HEAD (\`git switch -c ${BRANCH}\`).\n`) +
    `3. If ${DIR}/ does not exist, run \`${SDD} new-feature ${NUM} ${SLUG}\` (scaffolds it from ${C.paths.templates_dir}); set scaffolded=true.\n` +
    `4. baseSha = \`git merge-base ${BRANCH} main\` (or HEAD if there is no main yet); headSha = \`git rev-parse HEAD\`.\n` +
    'Do not commit.',
  { label: 'setup', phase: 'Setup', schema: SETUP, agentType: 'integrator', effort: 'low' },
)
if (!setup || !setup.ok) return finish('blocked-setup', setup ? setup.notes : 'setup agent failed', 'Clean the working tree or fix the branch/worktree, then re-run.')
summary.baseSha = setup.baseSha

// ---------------------------------------------------------------------------
// Specify
// ---------------------------------------------------------------------------
if (want('specify')) {
  phase('Specify')
  const r = await agentR(
    `${CTX}\n\nWrite ${DIR}/spec.md for ${FID} following your spec-author role and the template ${C.paths.templates_dir}/spec.md.\n` +
      `Start from the ${FID} entry in ${C.paths.roadmap} (scope, acceptance criteria, milestone). Open and read every source clause you cite, including abnormal/error cases. ` +
      `Use the exact requirement line format and this feature's requirement id block (printed by \`${SDD} new-feature\`; ids are global across features). Leave "Status: Draft".\n` +
      `PROPORTIONALITY (binding): specify only what the roadmap acceptance criteria of this feature need. Budget: about ${BUDGET.min}-${BUDGET.max} requirements ` +
      '(go higher only for normative abnormal cases the source mandates). Do not specify behaviour owned by other roadmap features; list it under Out of scope with the owning feature id. ' +
      'Do not over-specify implementation details that tests do not need.\n' +
      'Project-internal design choices (not normative behaviour) are YOURS to decide: record them under "Decisions" with a one-line rationale. ' +
      `Use ${MARKER} ...] ONLY for genuine ambiguity in the normative source that you cannot resolve from its text.` +
      (A.notes ? `\n\nOrchestrator notes: ${A.notes}` : ''),
    { label: `spec-author ${FID}`, phase: 'Specify', schema: STEP, agentType: 'spec-author' },
  )
  if (!r || !r.ok) return finish('blocked-spec', r ? r.summary : 'spec-author failed', 'Inspect spec.md and the roadmap entry; re-run with stages ["specify", ...].')
}

// ---------------------------------------------------------------------------
// Critic (convergence rounds; product-owner resolution when it does not converge)
// ---------------------------------------------------------------------------
if (want('critic')) {
  phase('Critic')
  let round = 0
  let prevIssues = []
  for (;;) {
    const c = await agentR(
      `${CTX}\n\nCritique ${DIR}/spec.md (round ${round + 1}) following your spec-critic role. Verify every citation against the normative source. ` +
        'Return only issues you verified, with quoted evidence.' +
        (round === 0
          ? ' Calibrate severity: blocking = contradicts the source / untestable must / missing in-scope normative abnormal case / internal contradiction; ' +
            'major = would likely cause a wrong implementation; wording, style and nice-to-have detail are minor. Flag scope creep beyond the roadmap entry as major. ' +
            `Flag a requirement count far above the budget (${BUDGET.min}-${BUDGET.max}) as major over-specification.`
          : ' CONVERGENCE ROUND: check whether the previously reported issues below are resolved. Report an unresolved previous issue with its original severity. ' +
            'Report a NEW issue only if it is blocking (as defined before); do not raise new major/minor issues.\nPrevious issues:\n' +
            JSON.stringify(prevIssues.map((i) => ({ severity: i.severity, reqId: i.reqId, problem: i.problem })), null, 1)),
      { label: `spec-critic r${round + 1}`, phase: 'Critic', schema: CRITIC, agentType: 'spec-critic' },
    )
    if (!c) return finish('blocked-spec', 'spec-critic failed', 'Re-run with stages ["critic", ...].')
    const serious = c.issues.filter((i) => i.severity !== 'minor')
    summary.critic.push({
      round: round + 1,
      verdict: c.verdict,
      blocking: c.issues.filter((i) => i.severity === 'blocking').length,
      major: c.issues.filter((i) => i.severity === 'major').length,
      minor: c.issues.filter((i) => i.severity === 'minor').length,
    })
    if (!serious.length) break
    prevIssues = serious
    if (round >= (A.poFirst ? 0 : MAX_CRITIC_FIX_ROUNDS) && APPROVAL === 'agent' && !A.poResolved) {
      // Product-owner resolution instead of a dead stop: decide, cut scope, defer; then a blocking-only check (up to 2 passes).
      let poIssues = serious
      let blk = []
      for (let pass = 0; pass < 2; pass++) {
        const po = await agentR(
          `${CTX}\n\nAct as PRODUCT OWNER for ${FID} (follow the product-owner role). The spec critic has not converged on ${DIR}/spec.md. Resolve it decisively:\n` +
            `1. Resolve every ${MARKER} ...] and open question: decide project-internal choices yourself and record them under "Decisions" (one line each, with rationale). ` +
            'For genuine ambiguity in the normative source pick the reading closest to its text and to mainstream implementations; record it.\n' +
            `2. Cut scope creep: requirements beyond the roadmap entry and acceptance criteria of this feature, or over-specified implementation detail, move to a "Deferred" section ` +
            `(with the future feature id) or are deleted. Target about ${BUDGET.min}-${BUDGET.max} requirements. Keep the ids of kept requirements stable.\n` +
            '3. Fix each remaining issue below by a concrete edit (contradictions must be removed).\n' +
            'Never invent normative behaviour. Return ok and a summary of decisions.\n\nRemaining issues:\n' + JSON.stringify(poIssues, null, 1),
          { label: `product-owner resolve ${pass + 1}`, phase: 'Critic', schema: STEP, agentType: 'product-owner' },
        )
        const chk = await agentR(
          `${CTX}\n\nFinal check of ${DIR}/spec.md following your spec-critic role. Report ONLY blocking issues ` +
            `(contradiction with the source, internal contradiction, untestable must requirement, missing in-scope normative abnormal case, remaining ${MARKER} marker). Verify citations.`,
          { label: `spec-critic final ${pass + 1}`, phase: 'Critic', schema: CRITIC, agentType: 'spec-critic' },
        )
        blk = chk ? chk.issues.filter((i) => i.severity === 'blocking') : [{ problem: 'critic failed' }]
        summary.critic.push({ round: `po${pass + 1}`, verdict: chk ? chk.verdict : 'error', blocking: blk.length, major: 0, minor: 0, po: po ? po.summary : null })
        if (!blk.length) break
        poIssues = blk
      }
      if (!blk.length) break
      await commit(`docs(${FID}): draft spec (critic unresolved)`, 'Critic')
      return finish('blocked-spec', `${blk.length} blocking issue(s) after product-owner resolution`, `Orchestrator: resolve ${DIR}/spec.md; re-run with stages ["critic", ...] and poResolved: true.`)
    }
    if (round >= MAX_CRITIC_FIX_ROUNDS) {
      await commit(`docs(${FID}): draft spec (critic unresolved)`, 'Critic')
      return finish(
        'blocked-spec',
        `${serious.length} blocking/major critic issue(s) remain after ${MAX_CRITIC_FIX_ROUNDS} fix rounds`,
        `Product owner: resolve the issues in ${DIR}/spec.md (or decide them under Decisions), then re-run with stages ["critic", "approve", ...]; or re-run with approval: "agent" to let a product-owner agent resolve them.`,
      )
    }
    await agentR(
      `${CTX}\n\nAddress these spec-critic issues in ${DIR}/spec.md (follow your spec-author role; re-read the cited source). ` +
        `Fix each issue. Decide project-internal choices yourself under "Decisions"; use ${MARKER} ...] only for genuine source ambiguity. ` +
        'Prefer simplifying or deferring over adding more requirements; scope creep should be cut. Never delete a requirement just to silence an issue.\n\n' +
        JSON.stringify(serious, null, 1),
      { label: `spec-fix r${round + 1}`, phase: 'Critic', schema: STEP, agentType: 'spec-author' },
    )
    round++
  }
  await commit(`docs(${FID}): draft spec for ${SLUG}`, 'Critic')
}

// ---------------------------------------------------------------------------
// Approve (product owner)
// ---------------------------------------------------------------------------
const LATER = ['plan', 'tests', 'implement', 'gates', 'review', 'verify', 'finalize']
if (want('approve') || LATER.some(want)) {
  phase('Approve')
  let st = await specStatus('Approve')
  if (st && st.status === 'Draft' && APPROVAL === 'orchestrator') log(`spec is Draft: waiting for the orchestrator (product owner) to approve ${DIR}/spec.md`)
  if (want('approve') && APPROVAL === 'agent' && st && st.status === 'Draft') {
    let po = null
    for (let attempt = 0; attempt < 2; attempt++) {
      po = await agentR(
        `${CTX}\n\nAct as PRODUCT OWNER for ${FID} (follow the product-owner role). You did not write this spec. Decide whether ${DIR}/spec.md can be approved:\n` +
          `- it matches the roadmap scope (${C.paths.roadmap}) with no creep and a proportionate requirement count (about ${BUDGET.min}-${BUDGET.max});\n` +
          `- no ${MARKER} markers remain (resolve product questions yourself by recording them under "Decisions", but never invent normative behaviour);\n` +
          '- every in-scope abnormal/error case is covered or explicitly out of scope;\n- the critic has no open blocking/major issues;\n' +
          '- each `must` has an acceptance scenario.\nIf approvable, change the header line to "Status: Approved" (only that line plus Decisions). Otherwise leave it Draft. ' +
          'Return approved and your reasons.',
        { label: `product-owner ${attempt + 1}`, phase: 'Approve', schema: APPROVAL_SCHEMA, agentType: 'product-owner' },
      )
      if (!po || po.approved || attempt > 0) break
      // Lesson 2: a rejection triggers one spec-author fix pass and a re-review, not a dead stop.
      await agentR(
        `${CTX}\n\nThe product owner did not approve ${DIR}/spec.md. Fix the spec to resolve these reasons (follow your spec-author role; re-read cited sources; ` +
          'keep requirement ids stable where possible; keep scope). Leave Status: Draft. Commit nothing.\n\nReasons:\n' + po.reasons.join('\n'),
        { label: 'spec-fix (po)', phase: 'Approve', schema: STEP, agentType: 'spec-author' },
      )
      await commit(`docs(${FID}): address product-owner review`, 'Approve')
    }
    if (po && po.approved) {
      await commit(`docs(${FID}): approve spec`, 'Approve')
      st = await specStatus('Approve')
    } else {
      return finish('awaiting-approval', po ? po.reasons.join('; ') : 'product-owner agent failed', `Resolve and approve ${DIR}/spec.md, then re-run from "plan".`)
    }
  }
  if (LATER.some(want)) {
    if (!st || !['Approved', 'Implemented'].includes(st.status) || st.needsClarification > 0) {
      return finish(
        'awaiting-approval',
        `spec status ${st ? st.status : 'unknown'}, ${st ? st.needsClarification : '?'} open clarification(s)`,
        `Orchestrator (product owner): review ${DIR}/spec.md and the critic summary, resolve ${MARKER} markers, set "Status: Approved", ` +
          `commit "docs(${FID}): approve spec", then re-run with stages ["plan","tests","implement","gates","review","verify","finalize"].`,
      )
    }
  }
}

// ---------------------------------------------------------------------------
// Plan
// ---------------------------------------------------------------------------
let plan = null
if (want('plan')) {
  phase('Plan')
  plan = await agentR(
    `${CTX}\n\nWrite ${DIR}/design.md and ${DIR}/tasks.md for the Approved spec, following your architect-planner role and ${C.paths.templates_dir}/{design,tasks}.md. ` +
      `Read the existing code first. Include the constitution check. Mark the plan security-sensitive if it touches ${SECURITY_HINT}. ` +
      `Then run \`${SDD} tasks ${DIR} --validate\` and fix any problem it reports (dependencies only to earlier waves, [P] tasks with disjoint files, known requirement ids). ` +
      `Return its JSON (waves with tasks) and whether the plan is security-sensitive.`,
    { label: `architect-planner ${FID}`, phase: 'Plan', schema: PLAN, agentType: 'architect-planner' },
  )
  if (!plan || !plan.ok || !plan.waves.length) return finish('blocked-plan', plan ? (plan.problems || []).join('; ') || plan.notes : 'architect-planner failed', 'Inspect design.md/tasks.md; re-run from "plan".')
  await commit(`docs(${FID}): design and tasks`, 'Plan')
}
if (plan && plan.securitySensitive) summary.securitySensitive = true

// ---------------------------------------------------------------------------
// Tests first
// ---------------------------------------------------------------------------
if (want('tests')) {
  phase('Tests')
  const t = await agentR(
    `${CTX}\n\nWrite the failing tests for ${FID} BEFORE implementation, following your test-author role: every requirement in ${DIR}/spec.md that tasks.md ` +
      'schedules gets a test tagged with its id (Verifies tag); abnormal/error cases, time via the test clock, fixtures/vectors as NEW files only with provenance, ' +
      `property tests where cheap, minimal skeletons so the tests compile. Run the red check and \`${SDD} trace --feature ${NUM}\`. Do not commit.`,
    { label: `test-author ${FID}`, phase: 'Tests', schema: TESTS, agentType: 'test-author' },
  )
  if (!t || !t.ok) return finish('blocked-tests', t ? t.summary : 'test-author failed', 'Inspect the tests; re-run from "tests".')
  const testCmd = TESTS_COMPONENT && t.components.length
    ? t.components.map((c) => TESTS_COMPONENT.replace('{component}', c)).join(' ; ')
    : TESTS_RUN || 'the project test command'
  let red = null
  for (let attempt = 0; attempt < 2; attempt++) {
    red = await agentR(
      `${CTX}\n\nIndependent red check (do not modify anything): run \`${testCmd}\` and \`${SDD} trace --feature ${NUM}\`. ` +
        'Report whether everything compiles, how many tests fail, which new tests unexpectedly pass, and which `must` requirements have no verifying test.',
      { label: `red-check #${attempt + 1}`, phase: 'Tests', schema: REDCHECK, agentType: 'integrator', effort: 'low' },
    )
    if (red && red.compiles && !red.mustWithoutTest.length) break
    if (attempt === 1) break
    await agentR(
      `${CTX}\n\nThe red check found problems with the new tests for ${FID}:\n${JSON.stringify(red, null, 1)}\n\nMake all tests compile (skeletons only, no logic) and add tests for ` +
        'every listed `must` requirement without one. Re-run the red check yourself. Do not commit.',
      { label: 'test-author fix', phase: 'Tests', schema: TESTS, agentType: 'test-author' },
    )
  }
  if (!red || !red.compiles || red.mustWithoutTest.length) {
    return finish('blocked-tests', `red check failed: compiles=${red ? red.compiles : '?'}, untested musts=${red ? red.mustWithoutTest.join(', ') : '?'}`, 'Fix tests, re-run from "tests".')
  }
  if (red.unexpectedPasses.length) log(`tests already passing before implementation (recorded for review): ${red.unexpectedPasses.join(', ')}`)
  summary.testsSha = await commit(`test(${FID}): failing tests for ${SLUG}`, 'Tests')
}

// ---------------------------------------------------------------------------
// Implement, wave by wave
// ---------------------------------------------------------------------------
async function resolveConflicts(conflicted, wave) {
  // Manifests: union (additive only). Anything else: semantic resolution by an agent (never automatic).
  const union = await agentR(
    `${CTX}\n\nA lane merge into ${BRANCH} in ${WHERE} left these files conflicted: ${conflicted.join(', ')}. ` +
      `Run \`${SDD} union-merge\` (it unions ONLY the additive manifests listed in sdd.config.json merge.union_manifests and refuses everything else). ` +
      'Report the files that are still conflicted. Do not resolve source files yourself and do not abort the merge.',
    { label: `union-merge wave ${wave}`, phase: 'Implement', schema: MERGE, agentType: 'integrator', effort: 'low' },
  )
  const left = union && union.conflicted ? union.conflicted : conflicted
  if (!left.length) {
    await commit(`merge(${FID}): wave ${wave} lanes`, 'Implement')
    return []
  }
  const sem = await agentR(
    `${CTX}\n\nResolve these merge conflicts SEMANTICALLY in ${WHERE} on ${BRANCH} (follow your implementer role): ${left.join(', ')}. ` +
      `Read both sides, ${DIR}/spec.md and ${DIR}/design.md; keep the behaviour both lanes intended; never pick a side blindly and never union source files. ` +
      `Then run \`${SDD} gates --fast --feature ${FID}\`, stage the files and complete the merge commit as "merge(${FID}): wave ${wave} lanes (resolved)" with the trailer.`,
    { label: `merge-resolve wave ${wave}`, phase: 'Implement', schema: STEP, agentType: 'implementer', model: MODELS_NOW.strong || undefined },
  )
  return sem && sem.ok ? [] : left
}

if (want('implement')) {
  phase('Implement')
  if (!plan) plan = await loadPlan('Implement')
  if (!plan || !plan.ok) return finish('blocked-plan', plan && plan.problems ? plan.problems.join('; ') : 'could not load tasks.md', 'Fix tasks.md (sdd tasks --validate) or re-run from "plan".')
  if (plan.securitySensitive) summary.securitySensitive = true
  const waves = plan.waves.slice().sort((a, b) => a.wave - b.wave).filter((w) => w.tasks.length)
  const testsRef = summary.testsSha ? ` Tests were committed at ${summary.testsSha}; they must stay unchanged.` : ''

  for (const w of waves) {
    const lanes = buildLanes(w.tasks)
    const useWorktrees = ISOLATION === 'worktree' || (ISOLATION === 'auto' && lanes.length > 1)
    const record = { wave: w.wave, lanes: lanes.length, mode: useWorktrees ? 'worktrees' : 'main-tree', done: [], failed: [] }
    summary.waves.push(record)
    log(`wave ${w.wave}: ${w.tasks.length} task(s) in ${lanes.length} lane(s), ${record.mode}`)

    if (!useWorktrees) {
      for (const lane of lanes) {
        for (const t of lane.tasks) {
          let r = null
          for (let attempt = 0; attempt < 2 && !(r && r.ok); attempt++) {
            r = await agentR(
              `${CTX}\n\nImplement task ${t.id} of ${DIR}/tasks.md in ${WHERE} on ${BRANCH}, following your implementer role.${testsRef}\n` +
                taskBlock(t) +
                (attempt ? `\n\nPrevious attempt failed: ${r ? r.summary : 'agent died'}. Address that first.` : '') +
                `\n\nWhen green: mark the task [x] in tasks.md, run \`${SDD} gates --fast --feature ${FID}\`, commit "feat(${FID}): ${t.id} <summary>" with "Implements: <ids>" in the body and the trailer.`,
              { label: `${t.id} impl${attempt ? ' retry' : ''}`, phase: 'Implement', schema: IMPL, agentType: 'implementer' },
            )
            if (r) await testDefectRoute(r.testDefects, 'Implement', t.id)
          }
          if (r && r.ok) record.done.push(t.id)
          else record.failed.push(t.id)
        }
      }
    } else {
      const base = await commit(`chore(${FID}): wave ${w.wave} base`, 'Implement')
      if (!base) return finish('blocked-implement', `could not determine base commit for wave ${w.wave}`, 'Check git state.')
      const results = await parallel(
        lanes.map((lane, i) => () =>
          agentR(
            `${CTX}\n\nYou are running in an ISOLATED GIT WORKTREE. Base commit: ${base}. Lane ${i + 1} of wave ${w.wave} (${COMPONENT_WORD}s: ${lane.components.join(', ')}).${testsRef}\n` +
              'Follow your implementer role, section "Worktree lanes": ensure HEAD contains the base commit, implement these tasks IN ORDER, ' +
              `commit after each ("feat(${FID}): <task> <summary>" + "Implements: <ids>" + trailer). Do NOT edit tasks.md (the integrator ticks tasks after merging), ` +
              `then \`git branch -f wip/${FID}-w${w.wave}-l${i + 1} HEAD\` and report branch, headSha and worktreePath. Touch only these tasks' files.\n` +
              lane.tasks.map(taskBlock).join('\n'),
            { label: `wave ${w.wave} lane ${i + 1}`, phase: 'Implement', schema: IMPL, agentType: 'implementer', isolation: 'worktree' },
          ),
        ),
      )
      const good = []
      results.forEach((r, i) => {
        const ids = lanes[i].tasks.map((t) => t.id)
        if (r && r.headSha) good.push({ lane: i + 1, sha: r.headSha, branch: r.branch || `wip/${FID}-w${w.wave}-l${i + 1}`, path: r.worktreePath || '', done: r.tasksDone })
        if (r) {
          record.done.push(...r.tasksDone)
          record.failed.push(...r.tasksFailed)
        } else record.failed.push(...ids)
      })
      for (const r of results) if (r) await testDefectRoute(r.testDefects, 'Implement', `wave ${w.wave}`)
      if (good.length) {
        const m = await agentR(
          `${CTX}\n\nMerge these worktree lane results into ${BRANCH} in ${WHERE}, one at a time, following your integrator role ` +
            `(git merge --no-ff --no-edit <sha>). On a conflict do NOT abort and do NOT hand-edit: report the conflicted files and stop at that lane. Merge message: "merge(${FID}): wave ${w.wave} lane <n>". ` +
            `After all lanes merged: tick the merged lanes' tasks ([ ] -> [x]) in ${DIR}/tasks.md and commit "chore(${FID}): wave ${w.wave} tasks done" with the trailer, and remove the merged worktrees and their fully merged wip/* branches.\n` +
            good.map((g) => `- lane ${g.lane}: sha ${g.sha} branch ${g.branch} worktree ${g.path || '(unknown)'} tasks ${g.done.join(', ') || '(none reported done)'}`).join('\n'),
          { label: `merge wave ${w.wave}`, phase: 'Implement', schema: MERGE, agentType: 'integrator' },
        )
        if (!m) return finish('blocked-merge', `wave ${w.wave}: merge agent failed`, 'Check git state; re-run the wave.')
        if (m.conflicted.length) {
          const left = await resolveConflicts(m.conflicted, w.wave)
          if (left.length) return finish('blocked-merge', `wave ${w.wave} unresolved conflicts: ${left.join(', ')}`, 'Resolve manually, then re-run with stages ["implement", ...] (done tasks are skipped).')
          // Remaining lanes (if the integrator stopped early) are merged on re-run; record them.
          const merged = new Set(m.merged)
          const pending = good.filter((g) => !merged.has(g.branch) && !merged.has(g.sha))
          if (pending.length) {
            const m2 = await agentR(
              `${CTX}\n\nContinue merging the remaining lane results into ${BRANCH} in ${WHERE} (integrator role; same rules, report conflicts without aborting), then tick all merged tasks in ${DIR}/tasks.md, commit "chore(${FID}): wave ${w.wave} tasks done" with the trailer, and clean up worktrees/wip branches:\n` +
                pending.map((g) => `- lane ${g.lane}: sha ${g.sha} branch ${g.branch}`).join('\n'),
              { label: `merge wave ${w.wave} (rest)`, phase: 'Implement', schema: MERGE, agentType: 'integrator' },
            )
            if (!m2 || m2.conflicted.length) {
              const left2 = m2 ? await resolveConflicts(m2.conflicted, w.wave) : ['(merge agent failed)']
              if (left2.length) return finish('blocked-merge', `wave ${w.wave} unresolved conflicts: ${left2.join(', ')}`, 'Resolve manually, then re-run from "implement".')
            }
          }
        }
      }
    }
    if (record.failed.length) {
      return finish('blocked-implement', `wave ${w.wave} failed tasks: ${record.failed.join(', ')}`, `Inspect the failing tasks; fix tasks.md or tests, re-run with stages ["implement", ...] (done tasks are [x] and skipped).`)
    }
  }
}

// ---------------------------------------------------------------------------
// Gates
// ---------------------------------------------------------------------------
if (want('gates')) {
  phase('Gates')
  const g = await gateLoop('post-implement', 'Gates')
  if (!g.pass) {
    return finish(g.infraError ? 'blocked-infra' : 'blocked-gates', g.failed.map((f) => f.gate).join(', '), `See ${C.paths.logs_dir}/*.log; fix and re-run from "gates".${summary.perfException ? ' A performance exception was requested: record an explicit product-owner decision in review.md or fix the regression.' : ''}`)
  }
}

// ---------------------------------------------------------------------------
// Review (parallel reviewers; barrier needed for dedup + security escalation)
// ---------------------------------------------------------------------------
let findings = Array.isArray(A.findings) ? A.findings : []
const RANGE = `${summary.baseSha}..HEAD`
if (want('review')) {
  phase('Review')
  const testsNote = summary.testsSha ? ` The red-check tests commit is ${summary.testsSha}.` : ''
  const reviewPrompt = (focus) =>
    `${CTX}\n\nReview the feature diff \`git diff ${RANGE}\` on ${BRANCH} (requirements in ${DIR}/spec.md, design in ${DIR}/design.md).${testsNote}\n` +
    `${focus}\nReport only verified findings with file:line and evidence; mark blocking only per your role definition. Set securitySensitive=true if the diff touches ${SECURITY_HINT}.`
  const REVIEWERS = [
    { type: 'spec-compliance-reviewer', focus: 'Check the code line by line against each requirement and the cited normative source.' },
    { type: 'security-reviewer', focus: 'Apply your full security checklist (untrusted input, crashes/DoS, auth, crypto, secrets, supply chain) within the project threat model.' },
    { type: 'test-adequacy-reviewer', focus: `Check requirement coverage, assertion strength and independent oracles; diff the tests since the red-check commit; use \`${SDD} trace --feature ${NUM}\`.` },
    { type: 'perf-reviewer', focus: 'Check the performance budgets and determinism rules of the constitution; compare against the recorded baseline where one exists.' },
  ]
  const res = await parallel(
    REVIEWERS.map((r) => () =>
      agentR(reviewPrompt(r.focus), { label: r.type, phase: 'Review', schema: FINDINGS, agentType: r.type }).then(
        (x) => x || agentR(reviewPrompt(r.focus), { label: `${r.type} (retry)`, phase: 'Review', schema: FINDINGS, agentType: r.type }),
      ),
    ),
  )
  const all = []
  res.forEach((r, i) => {
    if (!r) {
      summary.reviewGaps.push(REVIEWERS[i].type)
      return
    }
    if (r.securitySensitive) summary.securitySensitive = true
    for (const f of r.findings) all.push(Object.assign({ reviewer: REVIEWERS[i].type }, f))
  })
  if (summary.securitySensitive) {
    const second = await agentR(
      reviewPrompt('INDEPENDENT SECOND SECURITY REVIEW (you have not seen the first review). Lens: correctness of authentication/authorization/crypto against the normative source, key and secret lifecycle, constant-time comparisons, integrity-before-processing and replay, information leaks via errors, logs, events and timing, resource exhaustion from untrusted peers.'),
      { label: 'security-reviewer (independent #2)', phase: 'Review', schema: FINDINGS, agentType: 'security-reviewer' },
    )
    if (second) for (const f of second.findings) all.push(Object.assign({ reviewer: 'security-reviewer-2' }, f))
    else summary.reviewGaps.push('security-reviewer-2')
    summary.needsSecuritySignoff = true
  }
  const seen = new Set()
  findings = []
  for (const f of all) {
    const key = `${f.file}:${f.line}:${(f.reqId || f.source || f.category || '').toLowerCase()}`
    if (seen.has(key)) continue
    seen.add(key)
    findings.push(Object.assign({ id: `R${findings.length + 1}` }, f))
  }
  summary.findings.total = findings.length
  summary.findings.blocking = findings.filter((f) => f.severity === 'blocking').length
  if (summary.reviewGaps.length) log(`review coverage gap - reviewers that returned nothing: ${summary.reviewGaps.join(', ')}`)
  log(`review: ${findings.length} finding(s), ${summary.findings.blocking} blocking`)
}

// ---------------------------------------------------------------------------
// Verify: N skeptics per blocking finding; survivors are fixed, re-gated, fix-checked
// ---------------------------------------------------------------------------
let verdicts = []
if (want('verify')) {
  phase('Verify')
  const blocking = findings.filter((f) => f.severity === 'blocking')
  if (!blocking.length) {
    log('no blocking findings to verify')
  } else {
    const scratch = (k) => `${WT_ROOT}/exp-${FID}-${k}`
    verdicts = await pipeline(blocking, (f) =>
      parallel(
        Array.from({ length: SKEPTICS }, (_, k) => () =>
          agentR(
            `${CTX}\n\nTry to REFUTE this ${f.reviewer} finding (skeptic ${k + 1}/${SKEPTICS}; follow your verifier role; default to refuted=true if evidence is insufficient). ` +
              `${ROOT ? `Code is in ${ROOT} (branch ${BRANCH}); do NOT modify it. For throwaway reproduction tests create a detached worktree (\`git -C ${ROOT} worktree add --detach ${scratch(f.id + '-' + (k + 1))} HEAD\`) and remove it (\`git worktree remove --force\`) before you finish.` : `You are in an isolated worktree of ${BRANCH}; throwaway reproduction tests are allowed and must be discarded before you finish (undo your own edits; never git checkout/clean).`}\n` +
              JSON.stringify(f, null, 1),
            Object.assign({ label: `verify ${f.id} #${k + 1}`, phase: 'Verify', schema: VERDICT, agentType: 'verifier' }, ROOT ? {} : { isolation: 'worktree' }),
          ),
        ),
      ).then((votes) => {
        const valid = votes.filter(Boolean)
        // A finding is dismissed only if every skeptic that answered refuted it (and at least one answered).
        const refuted = valid.length > 0 && valid.every((v) => v.refuted)
        return { finding: f, refuted, votes: valid.map((v) => ({ refuted: v.refuted, confidence: v.confidence, reasoning: v.reasoning })) }
      }),
    )
    verdicts = verdicts.filter(Boolean)
    const confirmed = verdicts.filter((v) => !v.refuted).map((v) => v.finding)
    summary.findings.confirmed = confirmed.length
    summary.findings.refuted = verdicts.length - confirmed.length

    if (confirmed.length) {
      for (const f of confirmed) {
        const toTests = /test/i.test(f.reviewer) || /test/i.test(f.category)
        const r = await agentR(
          `${CTX}\n\nFix this CONFIRMED review finding on ${BRANCH} in ${WHERE}. ` +
            (toTests
              ? 'Follow your test-author role: strengthen or add tests (fixtures/vectors: new files only). '
              : 'Follow your implementer role: fix product code; never edit tests owned by test-author (report testDefects instead). Prefer the cheap, complete hardening fix over a partial one. ') +
            `Run \`${SDD} gates --fast --feature ${FID}\` and commit as "fix(${FID}): ${f.id} <summary>" with the trailer.\n` +
            JSON.stringify(f, null, 1),
          { label: `fix ${f.id}`, phase: 'Verify', schema: STEP, agentType: toTests ? 'test-author' : 'implementer' },
        )
        if (r && !toTests) await testDefectRoute(r.testDefects, 'Verify', f.id)
      }
      const g = await gateLoop('post-fix', 'Verify')
      if (!g.pass) return finish(g.infraError ? 'blocked-infra' : 'blocked-gates', `after review fixes: ${g.failed.map((x) => x.gate).join(', ')}`, `See ${C.paths.logs_dir}/*.log.`)
      // Lesson 11: incomplete fixes were found twice; security-sensitive fixes get two independent fix-checks.
      const FIXCHECKS = summary.securitySensitive ? 2 : 1
      const checks = await parallel(
        confirmed.map((f) => () =>
          parallel(
            Array.from({ length: FIXCHECKS }, (_, k) => () =>
              agentR(
                `${CTX}\n\nCompletion claim to REFUTE (follow your verifier role; read-only in ${WHERE}; fix-check ${k + 1}/${FIXCHECKS}): "finding ${f.id} is fixed on ${BRANCH}". ` +
                  'refuted=true means it is NOT fixed or only partially fixed (the root cause remains reachable). Re-run the original failure scenario against the current code.\n' +
                  JSON.stringify(f, null, 1),
                { label: `fix-check ${f.id} #${k + 1}`, phase: 'Verify', schema: VERDICT, agentType: 'verifier' },
              ),
            ),
          ).then((vs) => vs.filter(Boolean)),
        ),
      )
      checks.forEach((vs, i) => {
        if (!vs || !vs.length || vs.some((c) => c.refuted)) summary.findings.unresolved.push(confirmed[i].id)
        else summary.findings.fixed++
      })
    }
  }
}

// ---------------------------------------------------------------------------
// Finalize
// ---------------------------------------------------------------------------
if (want('finalize')) {
  phase('Finalize')
  const failedGates = summary.gates.length > 0 && !summary.gates[summary.gates.length - 1].pass
  const unresolved = summary.findings.unresolved.length > 0
  const gaps = summary.reviewGaps.length > 0
  const canImplement = !failedGates && !unresolved && !gaps
  const record = {
    baseSha: summary.baseSha,
    testsSha: summary.testsSha,
    critic: summary.critic,
    waves: summary.waves,
    gates: summary.gates,
    securitySensitive: summary.securitySensitive,
    perfException: summary.perfException,
    reviewGaps: summary.reviewGaps,
    findings: findings.map((f) => {
      const v = verdicts.find((x) => x.finding.id === f.id)
      return Object.assign({}, f, {
        verifier: v ? (v.refuted ? 'refuted' : 'confirmed') : f.severity === 'blocking' ? 'not-verified' : 'n/a',
        votes: v ? v.votes : [],
        unresolved: summary.findings.unresolved.includes(f.id),
      })
    }),
  }
  const fin = await agentR(
    `${CTX}\n\nFinalize ${FID} in ${WHERE} on ${BRANCH} (follow your integrator role):\n` +
      `1. Write ${DIR}/review.md from ${C.paths.templates_dir}/review.md using the pipeline record below (findings table with verifier votes, gates, security section; ` +
      `"Product-owner security sign-off" = ${summary.securitySensitive ? '"PENDING - orchestrator must sign off before merge"' : '"not required"'}${summary.perfException ? '; record the requested performance exception as PENDING product-owner decision' : ''}).\n` +
      (canImplement
        ? `2. Set the spec header line to "Status: Implemented" in ${DIR}/spec.md.\n3. Run \`${SDD} trace --check --feature ${NUM}\` then \`${SDD} trace --check\`. ` +
          'If either fails, set the Status line back to "Approved" and record the failure in review.md.\n'
        : `2. Do NOT change the Status line (blocked: ${failedGates ? 'gates failing; ' : ''}${unresolved ? 'unresolved findings ' + summary.findings.unresolved.join(', ') + '; ' : ''}${gaps ? 'missing reviews ' + summary.reviewGaps.join(', ') : ''}). Skip trace --check.\n`) +
      `4. Commit everything as "docs(${FID}): ${canImplement ? 'mark implemented, review record' : 'review record (blocked)'}" with the trailer. Do not merge to main.\n\n` +
      'Pipeline record:\n' +
      JSON.stringify(record, null, 1),
    { label: `finalize ${FID}`, phase: 'Finalize', schema: FINAL, agentType: 'integrator' },
  )
  summary.final = fin
  if (!canImplement) {
    return finish(
      'blocked-review',
      [failedGates ? 'gates failing' : '', unresolved ? `unresolved findings ${summary.findings.unresolved.join(', ')}` : '', gaps ? `missing reviews ${summary.reviewGaps.join(', ')}` : ''].filter(Boolean).join('; '),
      'Read review.md; fix or re-review, then re-run with stages ["gates","review","verify","finalize"].',
    )
  }
  if (!fin || !fin.ok || fin.status !== 'Implemented') {
    return finish(
      'blocked-finalize',
      fin ? `status ${fin.status}, trace ${fin.traceCheck}${fin.notes ? ': ' + fin.notes : ''}` : 'finalize agent failed',
      'Read review.md; fix the trace gaps / findings, then re-run with stages ["gates","review","verify","finalize"].',
    )
  }
  const mergeSteps = ROOT
    ? `merge main INTO ${BRANCH} first (\`git -C ${ROOT} merge main\`; manifests via \`${SDD} union-merge\`, sources semantically), run full \`${SDD} gates\` there, then \`git switch main && git merge --no-ff ${BRANCH}\` in ${PROJECT_DIR} (conflict-free), run \`${SDD} trace --check\` on main, delete the worktree's build output and \`git worktree remove ${ROOT}\`.`
    : `\`git switch main && git merge --no-ff ${BRANCH}\`, then \`${SDD} trace --check\` on main.`
  return finish(
    'ready-to-merge',
    null,
    summary.needsSecuritySignoff
      ? `Security-sensitive: orchestrator must review the security findings in ${DIR}/review.md, demand any cheap hardening fix, have an independent verifier re-check every fix, and record the sign-off before merging. Then: ${mergeSteps}`
      : `Orchestrator: ${mergeSteps}${summary.perfException ? ' Decide the requested performance exception explicitly in review.md first.' : ''}`,
  )
}

return finish('stopped', `ran stages: ${requested.join(', ')}`, null)
