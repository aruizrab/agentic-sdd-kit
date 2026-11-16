---
name: compact-handoff
description: Write the live orchestration state to the handoff file (paths.handoff, default .wt/handoff/STATE.md) before the context is compacted, so run ids, the queue and pending decisions do not depend on the compaction summary. Use when the owner asks to compact or says the context is long, before any /compact, and when you notice the session is getting long. Main session (orchestrator) only.
allowed-tools: Read Grep Write Bash(python3 -I tools/sdd/sdd.py config *) Bash(python3 -I tools/sdd/sdd.py progress *) Bash(sdd config *) Bash(sdd progress *) Bash(git status *) Bash(git log *) Bash(git worktree *) Bash(git branch *) Bash(git rev-parse *) Bash(ls *) Bash(cat *) Bash(cp *) Bash(mkdir *) Bash(tail *) Bash(free *) Bash(df *)
---
# Handoff before compaction

The compaction summary is lossy: a reference project's orchestrator lost or misread standing
rules and in-flight state across seven compactions (the kit's docs/LESSONS.md 49 and 53).
Write the state to a file first; then the summary only needs to say "read the handoff file".
This skill is for the main session only: the `main-only-skills` hook blocks it for subagents
(`orchestrator.main_only_skills`).

## Facts to start from
!`echo "handoff file: $(python3 -I tools/sdd/sdd.py config --get paths.handoff 2>/dev/null || echo .wt/handoff/STATE.md)"; echo "rules file: $(python3 -I tools/sdd/sdd.py config --get paths.orchestrator 2>/dev/null || echo docs/ORCHESTRATOR.md)"; echo; git log --oneline -8; echo; git worktree list; echo; git status --short | head -20; echo; eval "$(python3 -I tools/sdd/sdd.py config --heavy-env 2>/dev/null)"; if [ -n "${SDD_HEAVY_LOCK:-}" ]; then echo "heavy-job queue:"; ls "$SDD_HEAVY_LOCK.queue/" 2>/dev/null; tail -3 "$SDD_HEAVY_LOCK.jobs.log" 2>/dev/null; fi; echo; free -g 2>/dev/null | head -2; df -h . | tail -1`

## Steps
1. **Collect the live state** from the facts above and from this session, and check each
   item against git, `sdd progress` or the task list rather than memory:
   - running workflows: run id, task id, feature, stage, arguments, what to do when it ends;
   - background agents and jobs: what each does, its worktree and branch, what to do on its
     report;
   - the heavy-job queue and anything waiting on it;
   - in-flight branches and worktrees with uncommitted or unpushed work, and the next step
     for each;
   - decisions taken this session that are not yet in a tracked file, and open questions to
     the owner;
   - scheduled wake-ups or cron jobs this session created (id, time, purpose);
   - owner instructions from this session: each must already be in the rules file (run the
     `owner-rule` skill for any that is not, before compacting).
2. **Rewrite the handoff file** (under `paths.worktree_root`, git-ignored; keep it under about
   150 lines): first copy the previous one to `STATE-<yyyymmdd-hhmm>.md` next to it, then
   write a dated header, `## Running`, `## In flight`, `## Waiting on the owner`,
   `## Next actions` (ordered) and `## Rules` holding only a pointer to the rules file.
3. **Commit tracked changes that must survive** (merge-obligation rows, roadmap edits) with
   their normal commit messages; never leave a decision only in the handoff file when a
   tracked file owns it.
4. **Tell the owner** that it is safe to compact, and suggest the instruction to pass, e.g.
   `/compact Read <handoff file> and <rules file> first; keep run ids and the next actions.`
