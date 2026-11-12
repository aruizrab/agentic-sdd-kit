# Playbook: running the workflow end to end

This is the orchestrator's manual. The orchestrator is the main Claude Code session at the
project root (or a human driving it). It never writes product code; it runs the pipeline,
acts as product owner, signs off security, merges, and keeps the machine healthy.

## 0. Before the first run

1. `sdd config --validate` and `sdd doctor` are clean.
2. `docs/CONSTITUTION.md` is ratified (articles 7-9 filled or deleted) and
   `project.threat_model` in `sdd.config.json` says what is untrusted and what is not.
3. `docs/ROADMAP.md` lists features with numbers, slugs, acceptance criteria, dependencies,
   `Security-sensitive` and `Hard` flags, and waves of features that can run in parallel.
4. `models` in `sdd.config.json` holds **explicit model ids** per tier (see §7). Empty means
   "inherit the session model".
5. Performance gates have a **recorded baseline** (the gate command compares against it).
6. The host: build output and worktrees are **not** on a RAM tmpfs; disk has headroom; OS
   auto-updates are off for the duration; `pipeline.max_build_jobs` fits the machine.
7. Commit everything. Worktrees branch from committed HEAD; uncommitted work is invisible.

## 1. One feature, step by step

```
/feature F011 user-login
```
or directly:
```js
Workflow({
  scriptPath: "<repo>/tools/sdd/pipeline/feature-pipeline.js",
  args: { feature: "F011", slug: "user-login", root: "<repo>/.wt/F011", config: <output of `sdd config --pipeline`> }
})
```

| Stage | Who | What you see / do |
|---|---|---|
| Setup | integrator (mechanical) | creates `.wt/F011` on `feat/F011-user-login` from `main`, scaffolds `specs/features/011-user-login/` |
| Specify | spec-author (strong) | spec.md within the requirement budget; decisions recorded; markers only for real source ambiguity |
| Critic | spec-critic (strong) | round 1 full; later rounds re-check earlier issues and raise only new blocking ones |
| Approve | you, or product-owner agent | default: run returns `awaiting-approval` (see §2) |
| Plan | architect-planner (strong) | design.md + tasks.md; `sdd tasks --validate` must be clean |
| Tests | test-author (strong) + red check | failing tests tagged with ids; commit `test(F011): ...` = tests baseline |
| Implement | implementers (mid; top on retry) | waves; lanes of disjoint components in parallel worktrees; manifests union-merged, sources resolved semantically |
| Gates | integrator runs, implementer fixes | detached run, polled until `GATES_EXIT`; 3 fix rounds; test arbiter after 2 failing test gates |
| Review | 4 reviewers (+2nd security) | blocking findings only gate the merge |
| Verify | N skeptics per finding | dismissed only if all refute; confirmed -> fix -> re-gate -> fix-check (x2 if security-sensitive) |
| Finalize | integrator | review.md, `Status: Implemented`, `sdd trace --check`; returns `ready-to-merge` |

Always read `status`, `reason` and `nextAction` in the returned summary.

## 2. Approval (you are the product owner)

When the run returns `awaiting-approval`:
1. `sdd spec-info specs/features/011-user-login` must report `Draft`, zero open clarification
   markers and a proportionate requirement count. Read the critic summary in the run output.
2. Read spec.md against the roadmap entry: scope, no creep, every `must` in a scenario,
   abnormal cases covered or explicitly out of scope.
3. Decide open product questions under *Decisions*. Never approve by inventing normative
   behaviour: a genuine source ambiguity becomes a documented project decision.
4. Set `Status: Approved`, commit `docs(F011): approve spec` with the trailer, and re-run
   with `stages: ["plan","tests","implement","gates","review","verify","finalize"]`.

For fully autonomous runs pass `approval: "agent"`: a `product-owner` agent approves, and a
rejection triggers one spec-author fix pass and a re-review. A spec the critic cannot
converge on goes to the product-owner agent for resolution (decide, cut scope to the roadmap
entry, defer to named features), up to two passes, each followed by a blocking-only check.
If it still blocks, resolve it yourself and re-run with `poResolved: true`.

## 3. Parallel features (waves, worktrees)

- Pick a wave from the roadmap: features touching disjoint components.
- Run each in its own worktree: `root: "<repo>/.wt/F0NN"`. Never branch-switch in a checkout
  another agent is using; never run more than `pipeline.max_parallel_features` at once.
- Requirement ids are global. Each feature owns an id block (`sdd new-feature` prints it);
  `sdd trace --check` on main catches collisions.
- Merging (per feature, when `ready-to-merge`):
  1. In the worktree: `git merge main` into the feature branch. Manifests: `sdd union-merge`.
     Source conflicts: an implementer agent resolves them semantically (never union, never
     "take theirs"). Run full `sdd gates` **in the worktree**.
  2. In the main checkout: `git switch main && git merge --no-ff feat/F0NN-slug` (conflict-free
     by construction), `sdd trace --check`, push if the project pushes.
  3. Delete the worktree's build output, `git worktree remove .wt/F0NN`, check disk.
- Shared-tree safety is a hard rule for everyone including you: no `git checkout -- <path>`,
  `git restore`, `git clean`, `git reset --hard`, `git stash` in a tree an agent may be using.
  The bash-guard hook and the settings deny list block them; experiments go to a detached
  throwaway worktree (`git worktree add --detach .wt/exp-<name> HEAD`).

## 4. Gates that take long

Full gates can exceed an agent's shell time limit. The pipeline runs them detached
(`sdd gates --detached`) and polls `sdd gates --wait <log>`; a log without `GATES_EXIT=<n>` is
an incomplete run and is never reported as a result. Keep full gates fast enough to run per
feature: budget fuzzing, fuzz only changed targets, use an optimised test profile for heavy
property tests. Gate logs live in `paths.logs_dir`.

## 5. Resuming paused and blocked runs

- `paused`: an agent failed twice (outage, usage limit). The pipeline stops issuing calls.
  Resume later with `Workflow({scriptPath, resumeFromRunId, args: <same args>})`; completed
  agent calls replay from the journal. **Do not change the args of completed phases.**
- To change model tiers for the remaining phases (e.g. a cheaper pool opened up), pass
  `models: {...}` plus `modelsFromPhase: "Gates"` and `modelsBefore: {...}` (the ids the
  completed phases used). Cached calls keep their original model, so the cache keys match.
- `blocked-<stage>`: read `reason`, the logs and review.md; fix the input or the
  infrastructure; re-run with `stages` starting at the failing stage. Done tasks stay `[x]`.
- A journal with an empty or odd result: read `<transcriptDir>/journal.jsonl` before
  diagnosing.

## 6. Usage limits

- Tier the models (§7); the mechanical steps are the majority of calls.
- Spread load across model pools when a pool is near its limit, using `modelsFromPhase` on
  resume rather than restarting.
- Do not interrupt running agents to switch models; let the phase finish.
- Keep `pipeline.max_parallel_features` small; parallelism multiplies spend without
  improving any single feature.

## 7. Model tiering

| Tier | Roles | Why |
|---|---|---|
| mechanical | integrator with `effort: low` (setup, status, commits, gate runs, red check) | deterministic steps; the tools do the work |
| mid | implementer, perf-reviewer, test-adequacy-reviewer, integrator merges, finalize | checked by stronger reviewers and verifiers |
| strong | spec-author, spec-critic, product-owner, architect-planner, test-author, spec-compliance-reviewer, security-reviewer, verifier | a wrong answer here propagates unchecked |
| top | a fix that already failed once, test arbitration, second product-owner resolution pass, semantic merge resolution, features flagged `hard` | hardest judgement calls |

Rules: explicit model ids in `sdd.config.json` (an alias silently moved to a newer, pricier
model once); never trade quality for tokens where a wrong answer is not caught downstream;
`pipeline.role_tiers` overrides the role->tier map; `tiering: false` makes every call
inherit the session model.

## 8. Security sign-off

A feature is security-sensitive when the plan or any reviewer says so (`pipeline.security_sensitive_hint`
lists the triggers). Then:
1. A second, independent security review runs; `needsSecuritySignoff` is set.
2. Confirmed findings are fixed; each fix gets **two** independent fix-checks.
3. Before merging, you read review.md §4, demand any cheap hardening fix that is still
   missing (bounds, caps, redaction), have a fresh `verifier` re-check each fix, and record
   the sign-off (role, date) in review.md. Incomplete fixes were found twice in practice;
   re-verify.
4. Escalate to a human on any confirmed crypto/integrity finding or new key-handling design.

## 9. Performance regressions

A failed performance gate is never passed by loosening the threshold. The implementer
removes the regression or reports `perf-exception-needed: <measurement>`; the pipeline
surfaces it as `perfException` and the product owner records an explicit decision in
review.md. Budgets themselves change only by ADR.

## 10. Quick path and small changes

Bugfixes, refactors and perf work without new requirements: regression test first (tagged
with the violated id), fix, `sdd gates`, `/review-feature` (stages gates/review/verify),
commit. A behaviour change to an Approved requirement takes the full path with a superseding
id.

## 11. Machine hygiene checklist (every merge)

- [ ] build output of the merged worktree deleted, worktree removed
- [ ] `df -h` headroom checked (a full host disk crashed the VM twice in ferrocore)
- [ ] `sdd trace --check` and `sdd progress` run on main
- [ ] nothing under `/tmp` that matters
