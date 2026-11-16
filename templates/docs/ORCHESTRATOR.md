# {{PROJECT}} orchestrator guide

The standing rules for the session that orchestrates this project's pipelines. They live in
this file, not in the session's memory: a long session compacts its context many times, and
rules kept only in memory drift or vanish (the kit's docs/LESSONS.md 49). This file is the
source of truth; session memory may point here but never overrides it. **When the owner sets
a new standing rule, record it in this file in the same turn** (§7 "Recording an owner rule":
edit and commit, `docs(orchestrator): <rule>`). When a new rule conflicts with this file,
replace the old line or ask the owner which one wins.

## 1. Start-up checklist (every new session, and after every compaction)

1. **Start the session at the repository root** (`cd <repo> && claude`). Claude Code loads
   `.claude/settings.json`, and with it every hook (protected paths, bash guard, formatters,
   stop check), only for a session started there; a session started in a parent directory
   runs every agent without hooks, silently. The pipeline's Setup probes the hooks and stops
   with `blocked-hooks` when they are not active (`pipeline.hook_probe`). Never orchestrate
   from an override session (`SDD_ALLOW_PROTECTED=1`).
2. Read, in order: this file, `specs/MERGE-OBLIGATIONS.md`, the roadmap's order of work,
   `git log --oneline -20`, `git worktree list`, and the latest handoff
   (`.wt/handoff/STATE.md`) if there is one. The `orchestrator-context` SessionStart hook
   reminds the main session of this file and its habits at startup, resume, clear and
   after every compaction (then: read the handoff first).
3. Check the host: free memory and disk, and the heavy-job queue
   (`ls .wt/locks/heavy.lock.queue/`).
4. Check that `main` is green: `tools/sdd/scripts/heavy.sh sdd gates --fast`.
5. Make sure the heartbeat runs. Run `CronList`; if no "{{PROJECT}} heartbeat" job exists,
   create it as §4 "Heartbeat and wake-ups" says, and note its id and creation date in the
   handoff file.

## 2. Roles and authority

| Decision | Who decides | Note |
|---|---|---|
| Spec approval | orchestrator as product owner | escalate per docs/DEVELOPMENT.md §11; the approval commit quotes the critic's final verdict and its open issues |
| Security sign-off | orchestrator as product owner (`pipeline.security_signoff: "po"`, the default) | the pipeline records `securitySignoff: "pending-po"` after two independent security reviews; the orchestrator demands cheap hardening fixes, has a fresh verifier re-check every fix, and signs in review.md §4 with the reasoning (what is security-relevant, each finding and how it was closed, accepted limitations and why they fit the threat model). Escalate only per the list below. With `pipeline.security_signoff: "owner"` the **owner** signs instead (`securitySignoff: "pending-owner"`): the orchestrator writes a one-page summary (what is security-relevant, the reviewers' findings and outcomes, open risks) and waits, and never signs off |
| Protected paths (`.claude/**`, the constitution, `sdd.config.json`, CI configuration, the installed kit) | **owner** | the orchestrator or an agent drafts a patch file (e.g. `tools/patches/<name>.patch`) and asks; the owner applies it and the commit body carries `Owner-approved: <date> <scope>` |
| Merge to `main` | orchestrator | only when the definition of done holds (CLAUDE.md), `specs/MERGE-OBLIGATIONS.md` has no open row due before this merge, the security sign-off is recorded if needed, and `main` stays green (§5) |
| Push | orchestrator | push `main` after every merge; push feature branches after approval or after any commit worth keeping; never force-push |

**What goes to the owner.** The owner says what to build, which quality checks must pass and
the direction; the orchestrator decides everything else as product owner and reports
afterwards. Bring something to the owner only when it needs a human eye:
1. **Protected configuration**: changes to `.claude/**`, the constitution, `sdd.config.json`,
   CI configuration or the installed kit. The orchestrator drafts the patch; the owner applies
   it. An agent must not be able to change its own gates.
2. **Risk acceptance beyond the threat model**: accepting, unfixed, a confirmed vulnerability
   whose harm reaches outside what the project's threat model covers (`<e.g. secrets leaving
   the machine or entering published data, a network-exposed service unauthenticated by
   default>`).
3. **Outward-facing or irreversible actions outside the routine**: making the repository or
   its data public, new external services or accounts, spending money, licence changes,
   deleting remote branches or history.
4. **The owner's own systems outside the project**: personal accounts, bots, tokens, machine
   configuration that needs elevated rights.
5. **Stuck**: the same blocker after the orchestrator has re-planned and escalated the model
   and effort; report it with the options and a recommendation.

Everything else (spec decisions, merge order, security sign-off within the threat model when
`pipeline.security_signoff` is `po`, re-measurements, routine progress reports) the
orchestrator does itself.

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
- **Pacing bands** (kit PLAYBOOK §6): aim to end each usage window at 85-90 % used. If the
  projected use is below 70 %, add a run; between 70 and 90 %, hold; above 95 %, pause the run
  furthest from a milestone. Wake at each window reset with a one-shot cron (below).

### Heartbeat and wake-ups

The harness gives the orchestrator no wake-up of its own: work keeps flowing only if the
session schedules its own turns. Session crons (`CronCreate`) live only as long as the session,
fire only while it is idle (an hourly job may run up to 30 minutes late), and recurring jobs
expire after 7 days. So:

- **Hourly heartbeat:** a recurring `CronCreate` with cron `<orchestrator.heartbeat.minute> * * * *`
  (default `17 * * * *`; an off-the-hour minute) and the prompt
  `{{PROJECT}} heartbeat: follow docs/ORCHESTRATOR.md §4 "Heartbeat and wake-ups", heartbeat steps.`
  Create it at session start (§1 step 5). Never create a second one: check `CronList` first.
  `orchestrator.heartbeat.enabled: false` drops this habit from the SessionStart reminder.
- **Renewal:** when the heartbeat is 6 or more days old (creation date in the handoff file),
  create a fresh one, then `CronDelete` the old one.
- **Reset wake-up:** when the usage window passes 70 % (or a limit is hit, or a run pauses on
  one), add a one-shot cron at the reset time plus 2 minutes (local time), with the prompt
  `{{PROJECT}} wake-up after the usage reset: follow docs/ORCHESTRATOR.md §4 "Heartbeat and wake-ups", heartbeat steps.`
  Keep at most one pending reset wake-up.
- **Cron turns are launch turns:** they carry no relayed user message, so pipelines may be
  launched from them (§3).

Heartbeat steps, on every heartbeat or wake-up turn:
1. **Usage:** read the usage source (`orchestrator.usage_file`: a JSON capture of Claude Code's
   status-line input, e.g. written by the project's status-line script; its `rate_limits` hold
   the used percentages and `resets_at`, a Unix epoch), e.g. `jq .rate_limits <usage_file>`.
   Without one, go by limit messages and paused runs. Compute the projection (used % plus the
   burn rate times the hours left in the window) and decide per the bands above. Schedule the
   reset wake-up if it is due.
2. **Work in flight:** check the running workflows and background agents (their notifications
   and outputs). Act on finished ones: review, merge per §2 and §5, start the next stage.
3. **Fill the window:** if the bands allow more work and the memory budget has room, launch the
   next runnable work in the roadmap's order (§4 concurrency, §5 component claims). If nothing
   runs at all, launch the cheapest runnable work.
4. **Host:** check free memory and disk and the heavy-job queue. A job waiting much longer than
   its peers means something is stuck; look into it.
5. **Report only changes:** one or two lines if something happened (a launch, a merge, a
   blocker, a decision the owner should know). Nothing if nothing changed.
6. **Renew** the heartbeat if it is due (above).

## 5. Component claims, merge order, main stays green

- **One in-flight branch per component**: a component (its source and its tests; the
  `components.path_regex` unit, e.g. a crate, package or module) belongs to at most one
  unmerged branch that changes code. Before launching Implement, check
  `git diff --stat main...<branch>` of every in-flight branch. A refactor or chore that
  touches a claimed component waits until that branch is merged.
- **Merge obligations**: every cross-feature obligation (supersession marks, edits to other
  specs or documents, tests of other features, required merge order, open product-owner
  items, pending security sign-offs) is a row in `specs/MERGE-OBLIGATIONS.md`. Check the register
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
- **Keep standing rules in this file.** Whenever the owner states a standing rule or
  direction, record it here in that same turn ("Recording an owner rule" below).
- **Compaction:** the owner triggers it. Before any `/compact`, or when the owner says the
  context is long, run "Handoff before compaction" below, so the summary only needs to point
  to the handoff file (`paths.handoff`, default `.wt/handoff/STATE.md`).
- **How these two habits are wired** (kit PLAYBOOK §18): the SessionStart hook
  `.claude/hooks/orchestrator-context.sh` reminds the main session of both habits (and of the
  heartbeat, §4 "Heartbeat and wake-ups") at startup, on resume, on clear and after every
  compaction, and never reaches a subagent. The owner can
  also type `/owner-rule <rule>` or `/compact-handoff`; both skills set
  `disable-model-invocation`, so no model context lists them and subagents never see them,
  and their bodies pull the procedures below (one copy). The `main-only-skills` hook
  (`orchestrator.main_only_skills`) is the backstop: it blocks either skill for any subagent,
  and every other skill stays available to subagents. A subagent reports rules or state to the
  orchestrator in its final message.

### Recording an owner rule

1. **Quote the rule.** Restate the owner's words as one imperative line with the date and
   "owner" as the source, e.g. `- **Concurrency:** up to 4 implementation pipelines (owner, <yyyy-mm-dd>).`
2. **Find its home** in this file: §2 authority and escalation, §3 launching, §4 how much runs
   at once, §5 component claims and merge order, §6 models, effort and host, §7 reporting; if
   none fits, add a row to the §8 table. A process rule for every agent (not only the
   orchestrator) belongs in `docs/DEVELOPMENT.md`. A rule that changes protected configuration
   (`.claude/**`, the constitution, `sdd.config.json`, CI) becomes a patch file for the owner
   (§2; kit PLAYBOOK §16), not an edit.
3. **Check for conflicts.** If the rule contradicts or narrows an existing line, replace that
   line; never keep both. If it is unclear which one the owner wants, ask one short question
   quoting both before writing.
4. **Edit and commit in the same turn:** `docs(orchestrator): <rule in a few words>`, with the
   configured trailer lines (`commit.trailers`). Commit only this file (or
   `docs/DEVELOPMENT.md`), even if other files are dirty.
5. **Confirm in one line**, e.g. "Recorded in docs/ORCHESTRATOR.md §4: up to 4 implementation
   pipelines."
6. **Act on it now.** If the rule changes what should be running (more or fewer pipelines, a
   pause, a new priority), do it in this turn or schedule it (§3 launch turn).

Never keep rule text in session memory. Memory may point to this file.

### Handoff before compaction

1. **Collect the live state** and check each item against git, `sdd progress` or the task
   list, not memory:
   - running workflows: run id, task id, feature, stage, arguments, what to do when it ends;
   - background agents and jobs: purpose, worktree and branch, what to do on their report;
   - the heavy-job queue (`ls .wt/locks/heavy.lock.queue/`) and what is waiting on it;
   - in-flight branches and worktrees with uncommitted or unpushed work, and the next step for
     each;
   - decisions taken this session that no tracked file holds yet, and open questions for the
     owner;
   - wake-ups and crons this session created (id, time, purpose);
   - owner instructions from this session: each must already be in this file. Record any that
     is missing first ("Recording an owner rule").
2. **Rewrite the handoff file** (`paths.handoff`, default `.wt/handoff/STATE.md`: under the
   git-ignored worktree root, under about 150 lines). First copy the old file to
   `STATE-<yyyymmdd-hhmm>.md` next to it. Sections: a dated header, `## Running`,
   `## In flight`, `## Waiting on the owner`, `## Next actions` (ordered), and `## Rules` with
   only a pointer to this file.
3. **Commit what a tracked file owns:** merge-obligation rows, roadmap edits and the like.
   Never leave such decisions only in the handoff file.
4. **Tell the owner it is safe to compact,** and suggest:
   `/compact Read .wt/handoff/STATE.md and docs/ORCHESTRATOR.md first; keep run ids and the next actions.`

## 8. Owner rules (append here, newest last)

| Date | Rule | Why |
|---|---|---|
| `<yyyy-mm-dd>` | `<the rule as the owner stated it>` | `<the reason, if given>` |
