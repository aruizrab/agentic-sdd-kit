# {{PROJECT}} orchestrator guide

The standing rules for the session that orchestrates this project's pipelines. They live in
this file, not in the session's memory: a long session compacts its context many times, and
rules kept only in memory drift or vanish (the kit's docs/LESSONS.md 49). This file is the
source of truth; session memory may point here but never overrides it. **When the owner sets
a new standing rule, edit this file in the same turn and commit it**
(`docs(orchestrator): <rule>`). When a new rule conflicts with this file, ask the owner which
one wins, then update the file.

## 1. Start-up checklist (every new session, and after every compaction)

1. **Start the session at the repository root** (`cd <repo> && claude`). Claude Code loads
   `.claude/settings.json`, and with it every hook (protected paths, bash guard, formatters,
   stop check), only for a session started there; a session started in a parent directory
   runs every agent without hooks, silently. The pipeline's Setup probes the hooks and stops
   with `blocked-hooks` when they are not active (`pipeline.hook_probe`). Never orchestrate
   from an override session (`SDD_ALLOW_PROTECTED=1`).
2. Read, in order: this file, `specs/MERGE-OBLIGATIONS.md`, the roadmap's order of work,
   `git log --oneline -20`, `git worktree list`, and the latest handoff
   (`.wt/handoff/STATE.md`) if there is one.
3. Check the host: free memory and disk, and the heavy-job queue
   (`ls .wt/locks/heavy.lock.queue/`).
4. Check that `main` is green: `tools/sdd/scripts/heavy.sh sdd gates --fast`.

## 2. Roles and authority

| Decision | Who decides | Note |
|---|---|---|
| Spec approval | orchestrator as product owner | escalate per docs/DEVELOPMENT.md §11; the approval commit quotes the critic's final verdict and its open issues |
| Security sign-off | **owner** | the pipeline ends with `securitySignoff: "pending-owner"`; the orchestrator writes a one-page summary (what is security-relevant, the reviewers' findings and outcomes, open risks) and waits. The orchestrator never signs off |
| Protected paths (`.claude/**`, the constitution, `sdd.config.json`, CI configuration, the installed kit) | **owner** | the orchestrator or an agent drafts a patch file (e.g. `tools/patches/<name>.patch`) and asks; the owner applies it and the commit body carries `Owner-approved: <date> <scope>` |
| Merge to `main` | orchestrator | only when the definition of done holds (CLAUDE.md), `specs/MERGE-OBLIGATIONS.md` has no open row due before this merge, the owner signed security if needed, and `main` stays green (§5) |
| Push | orchestrator | push `main` after every merge; push feature branches after approval or after any commit worth keeping; never force-push |

## 3. Launching pipelines

- **One pipeline script**: the installed `tools/sdd/pipeline/feature-pipeline.js` (protected
  as part of the installed kit) is the only copy. Named copies from
  `tools/sdd/scripts/named-pipeline.sh` differ only in `meta.name`. No other tracked or live
  copy exists; a change to the pipeline is an owner patch (§2) and a kit change.
- **Arguments**: `feature`, `slug`, `root`, `stages`, `tasks`, `notes`, plus `testsSha` when a
  run starts after Tests and `evidence` when it starts at Finalize. The safety options are on
  by default (kit PLAYBOOK §17); never pass `false` for one unless the owner asks. Unknown
  arguments stop the run.
- **Launch turn**: launch only from a turn whose user message asks for that run, or from a
  notification or scheduled turn (the harness relays the launching turn's message to every
  agent of the run). If a launch is due during an unrelated chat turn, schedule a one-shot
  wake-up a minute or two later whose prompt is the launch instruction.
- **Resume**: do not resume a run across a script or input change (a resume replays cached
  results, failures included); start a new run from the next stage with `testsSha`.
- **Worktrees**: one worktree per feature (`.wt/F<NNN>` on its feature branch); remove it,
  build output included, after the merge.

## 4. How much runs at once

- **WIP limit**: no new spec run (Specify stage) starts while more than `<N, e.g. 10>`
  approved specs are unimplemented, unless the owner asks. Implementation first.
- **Concurrency**: up to `pipeline.max_parallel_features` implementation pipelines; the real
  limit is the heavy-job memory budget (`quality.heavy_budget_mb`). Agent-heavy, build-light
  work (plans, reviews, documents) may run next to execution-heavy phases.
- **Never idle**: if nothing runs and the usage window is not used up, start the cheapest
  runnable work (kit PLAYBOOK §6 and §14 for pacing and unattended pauses).

## 5. Component claims, merge order, main stays green

- **One in-flight branch per component**: a component (its source and its tests; the
  `components.path_regex` unit, e.g. a crate, package or module) belongs to at most one
  unmerged branch that changes code. Before launching Implement, check
  `git diff --stat main...<branch>` of every in-flight branch. A refactor or chore that
  touches a claimed component waits until that branch is merged.
- **Merge obligations**: every cross-feature obligation (supersession marks, edits to other
  specs or documents, tests of other features, required merge order, open product-owner
  items, pending owner sign-offs) is a row in `specs/MERGE-OBLIGATIONS.md`. Check the register
  before every merge and close each row with the merge commit's sha.
- **Requirement text is never written at merge time**: successor wording goes through a spec
  change with a critic round before either feature merges.
- **Main stays green**: no merge into `main` (chores and fixture additions included) without
  a passing `tools/sdd/scripts/heavy.sh sdd gates --fast` on the merge result before the push;
  feature merges also keep the full-gate rule.

## 6. Models, effort and host

- The tiers in `sdd.config.json` (`models`, or the formula router's table) decide models and
  efforts; record here the owner's standing choices (e.g. the fallback when a capped model's
  pool is exhausted, efforts never to use).
- Every heavy command goes through `tools/sdd/scripts/heavy.sh` (kit PLAYBOOK §14). The memory
  guard (`tools/sdd/scripts/memguard.py`) is a last resort; a test it kills in a mutation run
  is reported, never counted as caught.
- Host notes: `<memory, disk, where build output may live, systemd user scopes, ...>`.
- Never kill other jobs by process group; stop only your own background jobs.

## 7. Reporting to the owner

- Say when work is paused or blocked, and why; never let a summary show a paused feature as
  moving (`sdd progress` marks it idle).
- Separate verified from believed: a diagnosis is a hypothesis until it is checked.
- **Compaction**: before compacting, write the live state (running run ids and their args, the
  queue, held patches, pending owner items) to `.wt/handoff/STATE.md`, so the summary does not
  have to carry it; read it back first after the compaction.

## 8. Owner rules (append here, newest last)

| Date | Rule | Why |
|---|---|---|
| `<yyyy-mm-dd>` | `<the rule as the owner stated it>` | `<the reason, if given>` |
