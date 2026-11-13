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
8. The git hooks are installed (`sdd hooks install`; `sdd doctor` checks it) and, on an
   existing codebase, the gates are adopted as §12 describes before the first feature.

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
| Setup | integrator (mechanical) | creates `.wt/F011` on `feat/F011-user-login` from `main`, scaffolds `specs/features/011-user-login/`; stops `blocked-setup` on uncommitted edits outside the feature directory (§5) |
| Specify | spec-author (strong) | spec.md within the requirement budget; decisions recorded; markers only for real source ambiguity |
| Critic | spec-critic (strong) | round 1 full; later rounds re-check earlier issues and raise only new blocking ones |
| Approve | you, or product-owner agent | default: run returns `awaiting-approval` (see §2) |
| Plan | architect-planner (strong) | design.md + tasks.md; `sdd tasks --validate` must be clean |
| Tests | test-author (strong) + red check | failing tests tagged with ids; the red check runs `sdd trace --include <added tests>`; commit `test(F011): ...` = tests baseline |
| Implement | implementers (mid; top on retry) | waves; lanes of disjoint components in parallel worktrees; manifests union-merged, sources resolved semantically |
| Gates | integrator runs, implementer fixes | detached HEAD-keyed run, waited on until `GATES_EXIT`; incomplete runs re-run (§4); 3 fix rounds; test arbiter after 2 failing test gates |
| Review | 4 reviewers (+2nd security) | blocking findings only gate the merge; the findings are committed to review.md §3 at once (`docs(F011): review findings`) |
| Verify | N skeptics per finding | dismissed only if all refute; confirmed -> fix -> re-gate -> fix-check (x2 if security-sensitive); verdicts committed (`docs(F011): verify record`) |
| Finalize | integrator | completes review.md, `Status: Implemented`, plain `sdd trace --check`; returns `ready-to-merge` |

Always read `status`, `reason` and `nextAction` in the returned summary.

Robustness arguments (defaults in brackets):

| Arg | Effect |
|---|---|
| `strictScope` [`true`] | every prompt carries the SCOPE RULE: only the orchestrator assigns work; a relayed user or chat message asking for other work is not acted on, only mentioned in one line of the agent's summary. `false` removes it. |
| `readViaGit` [`false`] | the spec critic, reviewers, verifiers and fix-checks read through git (`git show <sha>:<path>`, `git diff`, `git log`) instead of creating worktrees; they may run the existing tests but write no code, so a reproduction that needs new code is described, not run, and Verify passes no `isolation`. Turn it on when builds are expensive (each worktree is a cold build). |
| `outsideScan` [`[]`] | carried over on a resumed run: test files the red check found outside `trace.scan_globs` (see below) |

Why these exist: docs/LESSONS.md entries 16 (scope rule), 17 (detached gate runs), 19 (red
check cross-checks tags), 20 (persisted review record) and 21 (interrupted work is kept).

**Tests outside the trace scan.** The red check runs `sdd trace --feature NNN --json --include
<each added test file>`, so a `must` whose test lives where `trace.scan_globs` does not look
is not wrongly reported as untested. Those files are listed in `summary.outsideScan`, and every
end state's `nextAction` then starts with "add these paths to `trace.scan_globs` in
sdd.config.json before merge: ...". `sdd.config.json` is protected, so you extend it; Finalize
runs the plain `sdd trace --check` and ends `blocked-finalize` until the configuration covers
the tests; then re-run with `stages: ["finalize"]`.

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

Full gates can exceed an agent's shell time limit, and an agent that ends its turn can kill a
foreground run. The pipeline therefore runs them detached and keyed by HEAD:
`sdd gates --detached --feature F011 --tag post-implement-1` writes
`<logs_dir>/gates/F011-post-implement-1-<sha7>.log` (`-dirty` when tracked files differ from
HEAD) and records the runner in `<log>.pid`. A second call for the same key attaches to the live
run or, for a clean key, reuses the completed log, so a retried gate agent never starts a
duplicate run. The gate agent runs `sdd gates --wait <log>` with its Bash tool in the
background and waits for the completion notice. `--wait` exits with the run's code, 124 when it
timed out while the run is going, and 125 when the run is **incomplete** (no marker and the
runner is gone; a reused pid does not count as alive). An incomplete result (or a log showing
the run itself was killed, e.g. by the OOM killer) is re-run with `--fresh` (the old log is
kept as `<log>.<n>`) up to 3 times in the same round; it is never counted as a failed round
and never triggers a gate-fix. A fourth incomplete result ends the run `blocked-infra`: check
memory, disk and the log. There are three plans: `gates.fast`
(pre-push and every task), `gates.full` (the pipeline's Gates stage and pull-request CI: adds
coverage and mutation of the changed lines and the command checks) and `gates.nightly`
(full-repository coverage and mutation, long fuzzing, benchmarks; schedule it in CI). Keep full gates fast enough to run per
feature: budget fuzzing, fuzz only changed targets, use an optimised test profile for heavy
property tests. Gate logs live in `paths.logs_dir`.

## 5. Resuming paused and blocked runs

- `paused`: an agent failed twice (outage, usage limit). The pipeline stops issuing calls.
  Resume later with `Workflow({scriptPath, resumeFromRunId, args: <same args>})`; completed
  agent calls replay from the journal. **Do not change the args of completed phases.**
- To change model tiers for the remaining phases (e.g. a cheaper pool opened up), pass
  `models: {...}` plus `modelsFromPhase: "Gates"` and `modelsBefore: {...}` (the ids the
  completed phases used). Cached calls keep their original model, so the cache keys match.
  In formula mode, edit the models table and pass `routingBefore: <summary.routing of the
  paused run>` with `modelsFromPhase`; pools exhausted before that phase count as available
  again (a pool still at its cap is marked again by its first failed call).
- After **every** run end (finished, `blocked-*` or `paused`) append the telemetry:
  `sdd routing log --output-file <the Workflow output file>` (or `--file` with the summary
  and `--transcript-dir <transcriptDir>` when the output file is gone). Keep `runKey` the same
  across resumes so the attempts of one logical run group together.
- `blocked-<stage>`: read `reason`, the logs and review.md; fix the input or the
  infrastructure; re-run with `stages` starting at the failing stage. Done tasks stay `[x]`.
- A journal with an empty or odd result: read `<transcriptDir>/journal.jsonl` before
  diagnosing.
- Review findings and verifier verdicts are committed to review.md as soon as they exist, so a
  run paused in Verify keeps them; a resumed Finalize completes the record and keeps the §3
  rows and §6 justifications.
- With `persistFindings: true` (or `pipeline.persist_findings`) §3 also holds a machine-readable
  findings block with each finding's outcome, the verdicts are written right after the votes
  (before any fix), and a run started with stages from `verify` (no `review`, no `findings`
  arg) reloads the findings from that block; findings already refuted or fixed are not
  verified again. Use it whenever a run may be resumed with new stages instead of from its
  journal.
- With `redCheckTags: true` (or `pipeline.red_check_tags`) the red check also searches the whole
  repository for Verifies tags with the configured tag syntax, so tests in directories outside
  `trace.scan_globs` do not block the run as "untested"; their files are reported in
  `outsideScan` for the owner to add to the scan globs.
- `blocked-setup` with uncommitted edits outside the feature directory: an interrupted worker
  probably left them. Inspect them (`git status`, `git diff`), then commit them or hand them to
  an implementer; never discard them automatically (the bash-guard blocks the commands that
  would). Re-run with the same args.

## 6. Usage limits

- Tier the models (§7); the mechanical steps are the majority of calls.
- A capped call can return an empty result with no error text, and caps can be weekly, not
  only per 5 h. The pipeline treats any failed call on a capped pool as "pool exhausted for
  this run", moves it to the cheapest equal-or-smarter point and pauses only when none is
  left (declare which models share a cap as one `capped` pool in the models table).
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

The gate runner has its own `gate` tier (a small model at medium effort: the cheapest model
did not wait and report reliably); empty, it falls back to `mid`. Each tier is an id or
`{id, effort}`; the default efficient mapping is in README "Model routing" and the template's
`_models_example`. Never the highest efforts (`xhigh`, `max`) by default.

Rules: explicit model ids in `sdd.config.json` (an alias silently moved to a newer, pricier
model once); never trade quality for tokens where a wrong answer is not caught downstream;
`pipeline.role_tiers` overrides the role->tier map; `tiering: false` makes every call
inherit the session model.

**Formula router** (`pipeline.routing: "formula"`): the planner rates each task (`complexity`,
`risk`), every call gets a level L1-L6 and the cheapest-in-expectation model at or above that
level's intelligence floor (README "Model routing", docs/CONFIG.md "Model routing"). Use it
once you have a measured frontier; tune it with data, not by hand: after a few features run
`sdd routing refit` and change a floor only when its suggestion holds over enough samples.
Dominated model/effort points are never used, and a retry never downgrades intelligence (a
failed attempt plus its retry costs more than the stronger model would have).

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
- [ ] nothing under `/tmp` that matters (no build cache either: `sdd doctor` warns)

## 12. Adopting the gates on an existing codebase

The quality gates (size, layers, coverage and mutation of changed lines, command checks, git
hooks) are meant to be strict from day one for new code, while the code that exists when you
adopt them is fixed on purpose, not by accident. Three steps, in this order:

1. **Build and configure the gates, install the hooks.** Fill `quality` in `sdd.config.json`
   (thresholds, `arch.modules`, the coverage and mutation commands and their `ok_exits`, the
   command checks; start from `examples/configs/<language>.json` and docs/ADAPTING.md), add the
   `sdd check` gates to `gates.fast`/`gates.full`, and run `sdd hooks install`. It refuses
   when hooks are already in use (git-lfs, husky, the pre-commit framework): chain them as
   docs/ADAPTING.md shows instead of forcing. Run `sdd gates --plan full` once and make every
   tool either run or show its install hint; in CI set `CI=true` so a missing tool fails.
2. **Record the baseline in the adoption commit.** `sdd baseline --init` writes every current
   size, arch and baseline-enabled command-check violation to `sdd.baseline.json`; commit it
   (for example `chore: adopt quality gates`). The installed kit (`tools/sdd/**`) is never
   project code, so it never lands in the baseline. `--init` refuses (exit 3) while a
   baseline-enabled check's tool is missing: install it or set `"baseline": false` on that
   check first. From then on the baseline only shrinks:
   `sdd check baseline` refuses new entries and raised values, a violation that grows or a
   new one fails, and a fixed one must have its entry removed with `sdd baseline --update`.
   Coverage and mutation need no baseline: they judge only changed lines.
3. **Run a remediation feature that empties the baseline before new feature work.**
   `sdd baseline --report` (or `--json`) is its work list, grouped by kind and file; plan
   one task per file or module (split long functions, move dependencies behind the allowed
   layer, add the missing docs, remove unused dependencies). Its parallel lanes all shrink
   the same baseline file, so their merges conflict on it by design: the integrator resolves
   that mechanically with `sdd baseline --resolve` (the intersection of both sides, then
   `--update` on the merged tree) and never with `sdd union-merge`, which refuses the file.
   Projects that prefer no conflict at all can register the optional merge driver
   (docs/ADAPTING.md). New features start when the report is empty, or when what is left is
   explicitly accepted by the product owner.

Never grandfather new code: no agent adds baseline entries, raises thresholds or adds exclude
globs to get a gate green (the code-writing roles cannot edit the config, the hooks or the
checkers). A `cov:unreachable: <reason>` marker or a mutant justification in review.md §6 is
reviewed like code.

## 13. Changing the pipeline safely

A paused or running Workflow run is resumed with the same args, and the runtime replays every
completed agent call whose key, the exact `(prompt, opts)` pair, is unchanged. Edit a prompt
or an opts field of an already completed call and the resumed run silently runs that call
again (cost, and possibly a different answer than the one the later phases built on). So:

1. **Every new behaviour is opt-in**, behind a new pipeline arg or a new `pipeline.*` config
   key that defaults to off (examples: `readViaGit`, `redCheckTags`, `persistFindings`).
   Without it, every prompt and opts object must stay byte-identical to the previous script.
2. **Prove it with the cache-key equivalence test**, `node tests/pipeline_cachekeys.mjs
   [--baseline <git-rev>]`. It loads the current script and the baseline script
   (`git show <rev>:tools/sdd/pipeline/feature-pipeline.js`) under a stub runtime whose
   `agent()` records every `(prompt, opts)` and answers from the call's label and per-label
   count only (never from the prompt), so both scripts walk the same path. Over several arg
   sets (tiers, hard, worktree root, main checkout with lanes, read via git, orchestrator
   approval, formula routing, stages from the middle) and scenarios (normal, a capped pool
   failing) it compares the call sequences and the run summaries byte for byte, and checks that
   the scenarios really reach the interesting paths (retries, test arbiter, second security
   review, verify, fixes, the cap fallback). Then it checks each opt-in arg changes exactly
   what it should and nothing else.
3. **Move the baseline when a change is installed**: after the change merges, pass the merge
   commit as `--baseline` (or update the default in the test) so the next change is compared
   against what the running copies use.
4. A resumed run may add an opt-in only when the calls it changes lie after the phase the run
   resumes in; the test shows which calls each arg touches. Install the new script in the live
   location only between runs, or for new launches.

The same pattern works for any orchestration script whose host caches calls by their inputs:
record the calls of old and new under a deterministic stub, and diff them.

## 14. Usage-limit pauses while unattended

The orchestrator acts only when something notifies it (a finished agent, a finished run, a
message). A run that ends `paused` on a usage limit while the owner is away therefore sits idle
until somebody looks, even after the limit has reset.

- **Schedule a one-shot wake-up at the reset time** when a run pauses on a usage limit: a
  scheduled task or timer that sends the orchestrator a message at the time the limit resets
  (plus a few minutes), with the resume instruction (`Workflow({scriptPath, resumeFromRunId,
  args: <same args>})` for each paused run). One wake-up per reset, not a polling loop. If the
  limit is still in force at wake-up, the resumed run pauses again on its first call: schedule
  the next wake-up then.
- Record the paused runs, their args and the planned wake-up time where the orchestrator keeps
  its state, so the wake-up (or a new session) can resume them without guessing.
- **Never run two heavy build or test jobs at once on a small host** (coverage, mutation
  testing, full workspace builds, build-cache measurements, full gates): two concurrent cold
  builds ran a 16 GB host out of memory, the OOM killer took a test process and froze the
  session. Queue heavy jobs; `pipeline.max_parallel_features` and `pipeline.max_build_jobs`
  bound the pipeline, but jobs started by hand count too.
- **Measure build caches only on an idle host**: a cache measurement is a series of cold
  builds, and its timings are worthless (and its memory use dangerous) next to another build.
- **Run mutation testing without the compiler-cache wrapper** (for example an empty
  `RUSTC_WRAPPER=` for Rust): every mutant rebuild is a guaranteed miss that writes a cache
  entry nobody reuses and evicts useful ones.
- **Expect cross-worktree cache hits mainly for registry dependencies**: they have the same
  absolute paths in every worktree; the project's own packages are compiled from a different
  path per worktree and usually miss across worktrees (hits come from rebuilds in the same one).
