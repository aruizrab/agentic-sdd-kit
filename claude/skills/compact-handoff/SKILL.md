---
name: compact-handoff
description: Write the live orchestration state to the handoff file (paths.handoff) before compacting; the procedure is the orchestrator guide's section "Handoff before compaction".
disable-model-invocation: true
allowed-tools: Read Grep Write Bash(python3 -I tools/sdd/sdd.py config *) Bash(python3 -I tools/sdd/sdd.py progress *) Bash(sdd config *) Bash(sdd progress *) Bash(awk *) Bash(git status *) Bash(git log *) Bash(git worktree *) Bash(git branch *) Bash(git rev-parse *) Bash(ls *) Bash(cat *) Bash(cp *) Bash(mkdir *) Bash(tail *) Bash(free *) Bash(df *)
---
# Handoff before compaction

Hidden from every model context (`disable-model-invocation`), so no subagent sees it; the owner
types `/compact-handoff` before `/compact`. The orchestrator does the same on its own: the
SessionStart hook `orchestrator-context` tells the main session to run this procedure before any
compaction, and to read the handoff file first after one. The `main-only-skills` hook
(`orchestrator.main_only_skills`) is the backstop: it blocks this skill for any subagent.

## Facts to start from
!`echo "handoff file: $(python3 -I tools/sdd/sdd.py config --get paths.handoff 2>/dev/null || echo .wt/handoff/STATE.md)"; echo "rules file: $(python3 -I tools/sdd/sdd.py config --get paths.orchestrator 2>/dev/null || echo docs/ORCHESTRATOR.md)"; echo; git log --oneline -8; echo; git worktree list; echo; git status --short | head -20; echo; eval "$(python3 -I tools/sdd/sdd.py config --heavy-env 2>/dev/null)"; if [ -n "${SDD_HEAVY_LOCK:-}" ]; then echo "heavy-job queue:"; ls "$SDD_HEAVY_LOCK.queue/" 2>/dev/null; tail -3 "$SDD_HEAVY_LOCK.jobs.log" 2>/dev/null; fi; echo; free -g 2>/dev/null | head -2; df -h . | tail -1`

## Procedure (from the rules file, one copy)
!`f="$(python3 -I tools/sdd/sdd.py config --get paths.orchestrator 2>/dev/null || echo docs/ORCHESTRATOR.md)"; out="$(awk '/^#+ /{if(p)exit; if($0 ~ /^### Handoff before compaction[[:space:]]*$/)p=1} p' "$f" 2>/dev/null)"; if [ -n "$out" ]; then printf '%s\n' "$out"; else echo "(no section \"Handoff before compaction\" in $f: add it from the kit's templates/docs/ORCHESTRATOR.md)"; fi`
