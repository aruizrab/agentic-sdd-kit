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
Every run of that script is listed as "feature-pipeline" in the progress view (the list shows the
script's literal `meta.name`; the Workflow `title` argument is ignored). To see which feature a run
is, launch a named copy instead: `scriptPath: $(tools/sdd/scripts/named-pipeline.sh F011 user-login)`
writes `.wt/pipeline-runs/F011-user-login.js`, the same script with only the name changed, so
resumes replay the same agents. An optional third argument tells runs of one feature apart
(`... F011 user-login spec`).

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
| `readViaGit` [`false`] | the spec critic, the product-owner approval, reviewers, verifiers and fix-checks read code through git (`git show <sha>:<path>`, `git diff`, `git log`, `git grep`) instead of creating worktrees, and the feature directory's Markdown from the working copy (the spec is still uncommitted while the critic and the product owner read it); they may run the existing tests without editing anything, and an experiment that needs a build or new code runs in a detached throwaway worktree (`<worktree_root>/exp-<unique>`) that the agent removes before it finishes. Verify passes no `isolation`. Turn it on when builds are expensive (each worktree is a cold build). A run started with `readViaGit` before the 2026-11-16 kit re-runs its read-only calls when resumed (the rule text changed). |
| `outsideScan` [`[]`] | carried over on a resumed run: test files the red check found outside `trace.scan_globs` (see below) |
| `tasks` [all open tasks] | `["T01", "T02"]`: Tests and Implement keep only these tasks of tasks.md, so one work item of a large plan runs on its own branch (§12). The test author and the red check are scoped to the requirements those tasks verify, waves left empty are skipped, dependencies on unlisted tasks count as satisfied, and the test commit names the tasks. Listed ids tasks.md does not return (unknown, or already `[x]`) are in `summary.taskFilter.<stage>.missing`; no open listed task ends the run `blocked-tests` before any test is written. Changes only Tests and Implement calls. |

Why these exist: docs/LESSONS.md entries 16 (scope rule), 17 (detached gate runs), 19 (red
check cross-checks tags), 20 (persisted review record) and 21 (interrupted work is kept).

**Launch from the right turn.** The Workflow harness relays the user message of the turn that
launches a run to every agent of that run, framed as the user's request that wins over the
computed task. A run launched while answering "how many pipelines are running?" had its setup
agent answer that question and skip the setup; the scope rule in the prompt cannot override
that framing. So launch (and resume) runs only in a turn whose user message asks for that run,
or in a notification or scheduled turn (no relayed message). When an unrelated question
arrives while a launch is due, answer it and schedule a one-shot wake-up a minute or two later
whose prompt is the launch instruction; that prompt is then the relayed request.

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
- Requirement ids are global. Each feature owns an id block per clause/area key (`sdd
  new-feature` prints it); `sdd trace --check` on main catches collisions. A feature whose
  block is full takes the lowest free block of that key, never "the next id above the
  highest": that rule walked one area key to R995 within weeks, so the next feature had no
  three-digit id left, and it lands in the blocks later features own.
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

**The script decides the gate command.** Gate agents ignored orchestrator notes asking them to
wrap the gate run in the heavy-job lock. In the kit the gate prompt names the exact command
(`sdd gates --detached ...`), the integrator role says to run it unchanged, and what the
plan runs comes from `gates.full` in `sdd.config.json`: coverage and mutation take the
host-wide lock themselves, and other heavy gates are wrapped in
`tools/sdd/scripts/heavy-lock.sh` in the configuration (the examples do this for the full
test gate). Never ask for a different command through `notes`; change the configuration.

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
- **Pace each usage window by projection, not by its current level.** Where the plan has a
  rolling window (for example 5 hours) and a weekly cap, read both before every launch.
  Projection = used % + the burn rate of the last hour x the hours left in the window. Aim to
  end each window near 85-90 %: below about 70 % projected, start another agent-heavy run
  (spec, critic, review); between 70 and 90 % hold; above about 95 %, pause the run furthest
  from a milestone (resume it after the reset; completed calls replay). When the weekly cap is
  the binding limit, give each remaining window an allowance, `(100 - weekly used %) / windows
  left until the weekly reset`, and stop launching token-heavy runs once a window has used it.
  Runs that spend host time but few tokens (a long mutation series) do not count against
  either. Never sit idle: when nothing runs and the window is not exhausted, start the
  cheapest runnable work (a resume, a plan-only run, an execution-heavy stage).
- While the owner is away, schedule one-shot wake-ups at each window reset (§14) as well as
  at pauses, so a window that filled early does not leave the next one unused.

## 7. Model tiering

| Tier | Roles | Why |
|---|---|---|
| mechanical | integrator with `effort: low` (setup, status, commits, gate runs, red check) | deterministic steps; the tools do the work |
| mid | implementer, perf-reviewer, test-adequacy-reviewer, integrator merges, finalize | checked by stronger reviewers and verifiers |
| strong | spec-author, spec-critic, product-owner, architect-planner, test-author, spec-compliance-reviewer, security-reviewer, verifier | a wrong answer here propagates unchecked |
| top | a fix that already failed once, test arbitration, second product-owner resolution pass, semantic merge resolution, features flagged `hard` | hardest judgement calls |

Setup is mechanical, but a cheap model once drafted a whole spec at Setup and tried to commit
it: the integrator role now says "prepare the workspace only, write no spec content". If the
mechanical model still overreaches, map `integrator` to `mid` in `pipeline.role_tiers` (that
moves every integrator call) rather than weakening the rule.

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

**Supersession is all or nothing.** `(superseded by <ID>)` removes the old requirement from
the trace check completely (no test, no implementation site required). A product-owner
decision that changes only part of an approved requirement ("superseded by X for case Y")
therefore cannot be expressed by the marker: it would silently drop the part that still
holds. Write a qualified successor (a new id that restates the remaining behaviour plus the
change), supersede the old id by it, and record the decision. The spec critic treats a partial
supersession as blocking. The marker counts only outside backticks: a requirement that quotes
`(superseded by X)` in a code span is not superseded.

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

**Large remediation features run as work items.** A remediation feature over a whole codebase
is too large for one branch. What worked:
- **Merge the plan first.** Run the feature to `plan`, then merge its branch (spec, design,
  tasks, review only) into `main`, so every work-item branch cut from `main` has the plan and
  `sdd trace` knows the feature's ids.
- **One work item per branch**, each a launch with the task filter (`tasks: ["T04", "T05"]`)
  on its own branch and worktree (`args.branch`, `root`), merged when green. `sdd progress`
  keeps the feature unfinished until its spec on main says Implemented, whatever branches
  have merged.
- **Whole-codebase mutation baselines one package at a time**, restartable: a script loops
  over the packages, runs each under `heavy-lock.sh` (one lock hold per package, never the
  whole loop), writes a done marker per package (accepting the tool's "survivors found" exit
  codes) and skips packages that have one, so a crash or a reboot resumes where it stopped.
  Leave a pause between packages and run short jobs (other features' gates) first; while the
  series runs, local gate runs may set `SDD_MUTATION=skip` (CI still runs mutation testing).

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
   key that defaults to off (examples: `readViaGit`, `redCheckTags`, `persistFindings`, `tasks`).
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
   One deliberate exception is always on: the Tests stage ends `blocked-tests` when the tests
   commit returns no sha (a ferrocore feature built 15 tasks on uncommitted red tests), and
   otherwise runs one read-only `tests-commit check` call (integrator, effort low) that reads
   the commit by sha and requires it to change a file defining a test the test author reported.
   It is the only added call; the test drops it from the current run before comparing
   (`--prev2 <rev>` compares with the script before it) and has null-commit and wrong-commit
   scenarios.
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
  session. Queue heavy jobs (or run them side by side only within a memory budget, each capped,
  below); `pipeline.max_parallel_features` and `pipeline.max_build_jobs` bound the pipeline,
  but jobs started by hand count too.
- **Queue heavy jobs on the host-wide lock**: `tools/sdd/scripts/heavy-lock.sh <command>`
  runs a command under `flock` on one lock file per host (`quality.heavy_lock`, default
  `/tmp/sdd-heavy-job.lock`, the same in every worktree). `sdd check coverage` and
  `sdd check mutation` take it themselves; wrap the other heavy commands (the full plan's test
  build, nightly runs, cache measurements, jobs started by hand) the same way. A nested call
  for the same lock runs directly, so wrapping a whole gate plan cannot deadlock. The queue is
  **first come, first served**: each caller draws a ticket in `<lock>.queue/` and only the
  oldest live ticket waits on the lock (plain `flock` is not fair: a job looping over long
  locked steps re-took the lock the moment it released it and kept short jobs waiting for four
  hours). So lock each step of a long loop separately (per package, per crate, per module),
  never the whole loop: jobs that arrive meanwhile get the lock between steps. Run short jobs
  (a feature's gates, follow-up fixes) before starting a long series. A tool that takes the
  lock file with `flock` directly bypasses the queue; give it `heavy-lock.sh` instead.
- **Run several heavy jobs within a memory budget once you know their sizes**: one at a time
  is the safe default but leaves a 16-core host idle. Export `SDD_HEAVY_BUDGET_MB` (the memory
  the heavy jobs may use together: RAM minus what sessions, agents and editors keep, e.g.
  10240 on a 16 GB host running several agent sessions) for every caller of the lock (put it
  in the wrapper the pipelines call), and let each job declare `SDD_HEAVY_JOB_MB` (default half
  the budget). The oldest waiting job starts when its MB fit next to the running jobs' MB;
  nobody overtakes it, and a job bigger than the budget runs alone. With `systemd-run --user`
  (systemd user session with the memory controller delegated: `systemd-run --user --scope -p
  MemoryMax=200M true` works) each job runs in its own scope with `MemoryMax=<job MB>` and no
  swap, so a job that outgrows its declaration is OOM-killed alone (exit 137 or a failed test)
  instead of taking the host down; without it the job runs uncapped with a warning. Every job
  appends a line to `<lock>.jobs.log` (MB declared, `peak_mb` actually used, exit code, OOM
  kills, cwd, command). Start with generous declarations (half the budget for full gates and
  mutation runs, a quarter for one package's tests), then size them from the logged peaks
  plus about 25 % headroom; a declaration that keeps hitting the cap (`oom_kills` > 0) is too
  small, and a cap hit can falsify results (a mutation tool whose compiler is killed records
  the mutant as unviable): re-run such a job with a larger declaration. Switching a busy lock is safe: one-at-a-time callers (an older `heavy-lock.sh`, or
  one without the budget) keep their places in the same queue, a job holding the lock the old
  way counts as `SDD_HEAVY_LEGACY_MB` (default half the budget), and they never start on top
  of budget jobs (those keep their tickets while running and hold a shared flock). Replace
  the script with `mv` (a new file), never by rewriting it in place: queued shells are still
  reading the old one.
- **Cap every mutation test process**: a mutant can turn a loop into a runaway allocator that
  grows a test process by gigabytes in seconds, far faster than a test timeout stops it. With
  3 jobs and 8 test threads per job that took a 16 GB host down, init system and unrelated user
  services included. `sdd check mutation` runs at most `quality.mutation.jobs` (2) mutants with
  `test_threads` (3) each and caps each test process's data memory at `memory_limit_mib`
  (2048) through `tools/sdd/scripts/memcap-runner.sh` (`ulimit -d`; an address-space cap,
  `ulimit -v`, made Go tools such as gitleaks panic at start-up, lesson 30); for Rust the runner is the
  cargo target runner (`CARGO_TARGET_<TRIPLE>_RUNNER`), so the compiler stays uncapped. A
  runaway mutant then fails alone and counts as caught. If the unmutated baseline fails only
  because of the cap, raise it; never remove it. Other ecosystems: docs/ADAPTING.md §6.
- **Run the memory guard during unattended heavy runs**:
  `nohup python3 -I tools/sdd/scripts/memguard.py --log .sdd/logs/memguard.log &` (or a user
  service). When MemAvailable drops under `--floor-mib` (1536) it kills the largest process
  whose executable matches `--pattern` (default `*/target/*`, test binaries in a build target
  directory) and logs it, so the kernel never picks a system service. It is a backstop for the
  cap, not a replacement: interpreted test runners (python, node) do not match by executable.
- **Re-check survivors with the slow tests before calling them test gaps**: if the mutation
  command excludes slow end-to-end tests for speed, re-run the surviving mutants with those
  tests included first; a survivor they kill is not a gap.
- **Exclude tests that need the real repository on the mutation command line**
  (`quality.mutation.test_filter`, `{test_filter}`): mutation tools test a copy of the tree,
  often without `.git`, so end-to-end tests that run git or the project's tools fail the
  unmutated baseline there. Put the filter on the command line, not in a test-runner profile:
  fixture projects copy that profile and some runners reject a filter that matches nothing
  (`test_filter_when` applies it only while such tests exist).
- **Skip mutation testing on a busy host, never in CI**: `SDD_MUTATION=skip` makes `sdd check
  mutation` report SKIP without running (for example while a long mutation series of another
  feature holds the heavy-job lock); with `CI=true` it fails, so the pull request's CI run
  still tests every changed line. Record in review.md that the local run was skipped.
- **Measure build caches only on an idle host**: a cache measurement is a series of cold
  builds, and its timings are worthless (and its memory use dangerous) next to another build.
- **Run mutation testing without the compiler-cache wrapper** (for example an empty
  `RUSTC_WRAPPER=` for Rust): every mutant rebuild is a guaranteed miss that writes a cache
  entry nobody reuses and evicts useful ones.
- **Expect cross-worktree cache hits mainly for registry dependencies**: they have the same
  absolute paths in every worktree; the project's own packages are compiled from a different
  path per worktree and usually miss across worktrees (hits come from rebuilds in the same one).

## 15. Procedure step tables (optional)

Some normative sources describe architecture-level **procedures**: call flows, sequence
diagrams, numbered steps between parties (for example the procedures of a 3GPP Stage 2
specification, an OAuth or payment flow, a protocol handshake). Requirements traced clause by
clause can still skip a step, invent one or give a step to the wrong party. Procedure step
tables make that mechanical. They are off by default; turn them on with
`trace.procedure_steps.enabled` (docs/CONFIG.md).

**What a spec carries.** The "Procedure steps" subsection of the spec template (section 4)
defines the exact forms. With the option on, every spec has an explicit entry, one of:
- a **step table** per procedure in scope: heading `Step table: <doc> §<section> [title]`,
  header `| Step | Description | Mapping |`, one row per step label of the section's own
  text, each Mapping cell holding requirement ids (any feature's: cite, do not restate),
  `owned by F<NNN>: <reason>` or `out of scope: <reason>`, joined with `;`; plus a
  **Responsibilities** table (`| Owner | Responsibility | Source | Requirements |`) whose
  quotes come from the cited source sections;
- an **exemption line** per procedure in scope that gets no table:
  `No step table for <doc> §<section>: <reason>`;
- a **feature-wide reason** when no procedure applies: `No step table: <reason>`.

The explicit entry is the point: an absent section ("never checked") must look different from
"checked, none applies", so the trace check can enforce the review uniformly.

**Checks** (`sdd trace --check`, the `trace` gate; for a spec whose status is in
`trace.procedure_steps.statuses`, by default Approved and Implemented; a Draft gets the same
findings as `warning:` lines and never fails CI):
- a step table that is malformed: no table after the heading, a wrong header, a row with the
  wrong number of cells, a step with two rows, a second table for the same procedure;
- a row whose cell is not accounted for (no requirement id and no disposition), a requirement
  id no spec defines, `owned by` naming the spec's own feature or a feature without a roadmap
  entry, an empty reason in a disposition, an exemption line or a feature-wide reason;
- with `source_check` (default): a step of the section with no row, a row for a label that is
  not a step of the section, a section the `sources` lookup cannot open or whose own text has
  no step label; a Responsibility not quoted from its cited section;
- a step table without a Responsibilities table;
- a spec with no entry at all.

Headings and lines that look like these structures but do not match (`### Step Table ...`,
`No step-table ...`) give a warning, so a typo cannot silently drop a table. Fenced code
blocks and HTML comments are ignored, so docs and the template can show examples.

Step labels are read from the section's **own text** (up to its first sub-section) by
`label_regex`; the built-in grammar recognises `1.`, `1\.`, `9a.`, `9b ` (no full stop),
`7(A).`, `3a)`, `3c:`, `10.1.`, `Step 25:` and ranges (`14a-c.`, `13-20.`, `4a - 4b.`,
`14.-15.`, `2a. - 2c.`, `2a to 2c.`, `1a and 1b.`), each compared in canonical form
(`2a-2c`) and as a set. It errs on the side of recognising: a numbered line that is not a
step costs one row `out of scope: not a step (<what it is>)`, and a void step one row
`out of scope: void`. A source whose steps the grammar cannot see gets its own `label_regex`,
or `source_check: false` (cells only) when the steps are not machine-readable at all.

**Coverage report.** Every `sdd trace` run (plain, `--feature`, `--json` under
`procedures`) lists, per feature with an entry, each step table with its number of steps and
of rows mapped to requirements, owned by other features, out of scope, and mapped rows whose
`must` requirements all have a verifying test (each row counts in the first category that
fits), then the exemption lines and the feature-wide reason.

**Call-flow conformance helper.** A spec's step table is also the oracle for the order of a
run: `sddkit.callflow.check(spec_text, procedure, expectations, skips, events)` in Python
tests, or `sdd callflow <spec.md> --procedure "<doc> §<section>" --expect <json> --events
<json|jsonl>` from a test suite in any language (exit 0 conforms, 1 violations, 2 bad input).
Events are records with an integer `seq` (the recorded total order); an expectation names a
step and a predicate (a callable in Python, or field values that must all be equal), ordered
by default or unordered for steps the source runs in parallel; a skip names a step and a
reason. Every step of the table needs exactly one expectation or skip. Ordered expectations
are matched in table order, each to the first matching event after the previous match; one
with no such event is reported **out of order** (naming the last matched step) when an
earlier event matches, else **missing**. Only `seq` orders events, so the result is
deterministic. The **golden-scenario rule**: every step table is checked by at least one
acceptance scenario that records a run and passes it through the helper (the template has an
example); the spec critic checks the scenario exists and the test-adequacy reviewer checks the
test.

**Roles.** The spec author reads every procedure in scope and writes its table or exemption
line; the spec critic checks completeness against the source text, the owners against the
source, consistency between procedure-level and detailed requirements, and the golden
scenario; the trace gate does the mechanical part.

**Migration note (turning the option on in a project with specs).** Every spec in a checked
status fails until it has an entry, so retrofit them in the commit that enables the option:
- most merged specs get one feature-wide line, for example `No step table: parsers and data
  types only; no procedure of <doc> applies`;
- a merged spec that does implement a procedure gets its step table and Responsibilities
  table (or an exemption line with a reason, and a follow-up feature);
- a feature that is paused or in progress adds its tables, exemption lines and golden
  scenarios when it resumes, before it is (re-)approved; Drafts only warn meanwhile.
Run `sdd trace --check` on main after the retrofit; the coverage report then lists every
feature.

## 16. Owner patches to protected files

Agents cannot edit protected paths (`.claude/**`, the constitution, the CI configuration); a
feature that needs such a change ships it as a patch file in an unprotected, tracked place
(for example `tools/patches/F0NN-<what>.patch`) for the owner to review and apply with
`SDD_ALLOW_PROTECTED=1`. Tests that check the patch (it applies, the patched hook behaves)
must also pass **after** the owner applied it: test "applied or applicable", never "applies".
A patch that reverses cleanly counts as applied:

```sh
if git apply --reverse --check tools/patches/F0NN-x.patch 2>/dev/null; then
  : # already applied on this branch: test the files as they are
else
  git apply --check tools/patches/F0NN-x.patch   # else it must apply cleanly
fi
```

Without the reverse check the patch tests failed on every branch merged after the owner
applied the patch on `main`. Apply patches in a detached throwaway worktree in tests, never in
a shared tree.
