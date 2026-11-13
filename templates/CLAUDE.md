# {{PROJECT}}: agent guide

<!-- Keep this file short. It is guidance; anything that must always happen is a hook, a
gate or a tool (docs/DEVELOPMENT.md principle 1). Replace every <...> placeholder. -->

{{PROJECT}} is <one sentence: what it is, what it is not (prototype / production / library)>.
It is built largely by AI agents through a spec-driven workflow.

- Binding rules: @docs/CONSTITUTION.md. Process: `docs/DEVELOPMENT.md`.
- Architecture: `docs/ARCHITECTURE.md`, `docs/adr/`. Plan: `docs/ROADMAP.md`.
- Configuration of the workflow (paths, gates, tags, protected paths): `sdd.config.json`.

## Where things are
| Path | What |
|---|---|
| `<src dir>` | <components / packages / crates> |
| `tools/sdd/` | kit tools: `sdd trace|gates|new-feature|progress|union-merge|source|tasks|spec-info`; `tools/sdd/pipeline/feature-pipeline.js` |
| `<normative source dir>` | <standard texts / API contracts / requirements>. **Read-only.** |
| `specs/templates/` | spec/design/tasks/review templates |
| `specs/features/NNN-slug/` | per-feature spec.md, design.md, tasks.md, review.md |
| `.claude/` | agents, skills, hooks, settings. **Human-owned, read-only for agents.** |

## Commands
```sh
<build command>
<test command for one component>
python3 -I tools/sdd/sdd.py gates --fast          # before every commit
python3 -I tools/sdd/sdd.py gates --detached      # all gates in the background; poll with --wait <log>
python3 -I tools/sdd/sdd.py trace --feature 011   # traceability matrix for one feature
python3 -I tools/sdd/sdd.py trace --check         # CI trace gate
python3 -I tools/sdd/sdd.py source clause <doc> <section>   # read the normative source
python3 -I tools/sdd/sdd.py new-feature 011 <slug>          # scaffold specs/features/011-<slug>/
```
(`sdd <cmd>` works when `bin/sdd` of the kit is on your PATH.)

## Reading the normative source (never from memory)
- Always open the section before citing or implementing it: `sdd source clause <doc>
  <section>` or the `/source` skill. Read the parent section and the error/abnormal-case
  sections too.
- If something is missing or ambiguous, write `[NEEDS CLARIFICATION: ...]`. Never guess.
  Project-internal choices are decided under *Decisions* in spec.md, not left open.

## Feature lifecycle (brief)
`Specify -> Critic -> Approve -> Plan -> Tests (red) -> Implement (waves) -> Gates -> Review -> Verify -> Finalize -> Merge`.
Run it with `/feature F011 <slug>` (runs `tools/sdd/pipeline/feature-pipeline.js` in the
worktree `.wt/F011`). The orchestrator is the product owner and approves specs
(`Status: Draft -> Approved`). Only the pipeline sets `Implemented`. Bugfixes and refactors
take the quick path in `docs/DEVELOPMENT.md`.

## Traceability conventions (exact)
- Requirement line in spec.md: `- **<ID>** [must] WHEN <trigger> THE <component> SHALL <response>. <citation>`
- Requirement ids are global across features (`sdd trace --check` fails on duplicates).
  Feature F<NNN> numbers the requirements it defines within a clause/area key in its own
  block R<NNN*10>..R<NNN*10+9> (printed by `sdd new-feature`); reuse an existing id instead
  of redefining the same rule.
- spec.md header line: `Status: Draft|Approved|Implemented`.
- Code: `<Implements tag syntax, e.g. // Implements: <ID>, """Implements: <ID>""" as a docstring's first line, or #[trace::implements("<ID>")]>`.
- Tests: `<Verifies tag syntax, e.g. // Verifies: <ID> or @verifies("<ID>")>`.
- Only comment lines, a docstring's opening line and attribute/decorator forms are scanned; a tag buried inside a longer docstring is not.
- A `must` requirement with no implementation site fails `trace --check` once the feature is
  Implemented, unless its spec line carries `[behaviour-only]`.
- <Only with `trace.procedure_steps.enabled`; else delete this bullet> Procedure steps: a
  heading `Step table: <doc> §<section>` with `| Step | Description | Mapping |`, Mapping
  cells holding requirement ids, `owned by F<NNN>: <reason>` or `out of scope: <reason>`
  (joined with `;`); a `Responsibilities` table with every step table; otherwise
  `No step table for <doc> §<section>: <reason>` or one `No step table: <reason>`. A step no
  other requirement states gets an id of this feature's block with
  `(Source: <doc> §<section> step <label>)`.

## Coding rules (from the constitution)
1. <rule 1: e.g. no crash/hang/unbounded allocation on untrusted input; typed errors>
2. <rule 2: e.g. determinism: no wall clock / unseeded randomness / unordered iteration in core logic>
3. **Tests first, by test-author.** Implementers never edit `<test paths>` or fixtures.
   Report test defects with the citation instead.
4. **Independent oracles.** Round-trip alone is not proof; use fixtures with provenance,
   external validators, reference implementations.
5. **Security.** <constant-time comparisons, secret types, no secrets in logs/events/errors>.
   *Threat model:* <copy `project.threat_model` from sdd.config.json; it prevents
   over-engineering>.
6. **Performance.** <budgets and the regression threshold; baseline recorded in <path>>.
   An exception is an explicit product-owner decision in review.md, never a loosened threshold.
7. **Observability.** <what every operation must emit>.
8. **Simplicity.** No speculative abstraction, dependency or module. About 400 changed lines
   per task.
9. **Licensing.** <what may not be copied>.

## Protected paths (hook-enforced; do not work around)
See `protected_paths`, `append_only_paths`, `role_write_scopes` and `role_denied_paths` in
`sdd.config.json`. A human can override with `SDD_ALLOW_PROTECTED=1`. If a protected change is
truly needed, stop and report. Destructive git commands (`checkout -- <path>`, `restore`,
`clean`, `reset --hard`, `stash`) are blocked in shared trees: experiment in a detached
throwaway worktree under `.wt/`.

## Commits
Conventional Commits, scoped by feature id: `feat(F011): T03 ...`, `test(F011): ...`,
`docs(F011): approve spec`, `fix(F011): R2 ...`. Outside a feature, scope by component. Add
`Implements: <ids>` in the body when relevant. Always end with the trailer line(s) from
`commit.trailers` in `sdd.config.json`:

```
<trailer line(s)>
```

Never use `--no-verify`, force-push, or commit build output or secrets. Commit only when
your task or the orchestrator says so.

## Definition of done
A **task** is done when its tests are green, `sdd gates --fast` passes, the code is tagged,
it is committed, and it stays inside its files.

A **feature** is done when: every `must` has an implementation site and a passing verifying
test (`sdd trace --check`); full `sdd gates` passes; the four reviewers ran and every blocking
finding was refuted by all verifiers or fixed and re-checked; the security sign-off is
recorded if the change is security-sensitive; review.md is written and `Status: Implemented`
is set.

## Gotchas
- In worktrees, hooks act on the hook input `cwd`; `CLAUDE_PROJECT_DIR` is the main checkout.
- Worktrees branch from the current HEAD (`worktree.baseRef: "head"`), so commit before
  spawning worktree agents. Uncommitted work is not copied.
- Never put build output or worktrees under `/tmp` (RAM tmpfs). Watch host disk; delete
  build output after merging a feature.
- <project-specific gotchas>
