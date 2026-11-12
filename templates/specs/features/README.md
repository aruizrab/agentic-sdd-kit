# Feature specs

One directory per feature: `specs/features/<NNN>-<slug>/`, for example
`specs/features/011-user-login/`.

## Numbering

- The feature id is `F<NNN>`: three digits, zero-padded, unique, never reused. The number
  comes from the feature's entry in `docs/ROADMAP.md`, which the architect owns. Do not
  invent numbers. If a feature is missing from the roadmap, ask the architect or orchestrator
  to add it first.
- The directory name is `<NNN>-<slug>`, where the slug is kebab-case and matches the roadmap.
- The branch is `feat/F<NNN>-<slug>`; the worktree is `.wt/F<NNN>`. The commit scope is
  `F<NNN>`, e.g. `feat(F011): T03 handle login response`.
- Requirement ids are **global across features** (`sdd trace --check` fails on duplicates).
  A feature numbers the requirements it defines within a clause/area key in its own block
  `R<NNN*10>..R<NNN*10+9>` (printed by `sdd new-feature`), so two features specified in
  parallel can never collide. Reuse an existing id (cite it) instead of redefining the same
  rule.

## Create a feature

```sh
python3 -I tools/sdd/sdd.py new-feature 011 user-login   # copies specs/templates/* into specs/features/011-user-login/
```

Or run the whole lifecycle from the orchestrator with `/feature F011 user-login`.

## Files

| File | Written by | Purpose |
|---|---|---|
| `spec.md` | spec-author (critic: spec-critic; approver: product owner) | what and why: references, scope, requirements, scenarios, abnormal cases, observability, decisions |
| `design.md` | architect-planner | how: modules, state machines, types, sequences, perf, security, test strategy, constitution check |
| `tasks.md` | architect-planner | wave-ordered tasks with file ownership and requirement ids |
| `review.md` | integrator (from reviewer and verifier outputs) | gates, findings, verifier verdicts, sign-offs, exceptions |

## Lifecycle and `Status:`

`spec.md` has one header line, `Status: Draft | Approved | Implemented`, which `sdd trace`
and `sdd spec-info` read.

```
Draft ──(critic clean, no open clarification marker, product owner approves)──▶ Approved
Approved ──(tests red → implement → gates → review → verify → trace --check)──▶ Implemented
```

- **Draft**: editable by spec-author. Trace does not enforce coverage yet.
- **Approved**: requirements are frozen; ids are never renumbered or reused. Changes add new
  ids and mark old ones `(superseded by <ID>)`; this needs the critic and re-approval.
- **Implemented**: set by the pipeline's Finalize stage only after all gates pass, no
  confirmed blocking findings remain, and `sdd trace --check` passes. Trace then enforces
  that every `must` has an implementation site and a verifying test.

The full process is in `docs/DEVELOPMENT.md`. The rules are in `docs/CONSTITUTION.md`.
