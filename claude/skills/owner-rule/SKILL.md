---
name: owner-rule
description: Record a standing rule or direction from the owner in the orchestrator guide (paths.orchestrator) now; the procedure is that file's section "Recording an owner rule".
argument-hint: "[the rule, in the owner's words]"
disable-model-invocation: true
allowed-tools: Read Grep Edit Bash(python3 -I tools/sdd/sdd.py config *) Bash(sdd config *) Bash(awk *) Bash(grep *) Bash(git status *) Bash(git diff *) Bash(git log *) Bash(git add *) Bash(git commit *)
---
# Record an owner rule: $ARGUMENTS

Hidden from every model context (`disable-model-invocation`), so no subagent sees it; the owner
types `/owner-rule <rule>`. The orchestrator follows the same procedure on its own: the
SessionStart hook `orchestrator-context` reminds the main session to record every standing rule
in the same turn. The `main-only-skills` hook (`orchestrator.main_only_skills`) is the backstop:
it blocks this skill for any subagent.

Follow the procedure below for the rule above (or, if empty, the owner's latest message). It
lives in the rules file, in one copy.

## Where the rules live
!`f="$(python3 -I tools/sdd/sdd.py config --get paths.orchestrator 2>/dev/null || echo docs/ORCHESTRATOR.md)"; echo "rules file: $f"; echo "process rules for every agent: $(python3 -I tools/sdd/sdd.py config --get paths.development 2>/dev/null || echo docs/DEVELOPMENT.md)"; echo "commit trailers: $(python3 -I tools/sdd/sdd.py config --get commit.trailers 2>/dev/null | tr -d '\n')"; echo; grep -n '^## \|^### ' "$f" 2>/dev/null || echo "(no rules file yet: install it from the kit's templates/docs/ORCHESTRATOR.md)"`

## Procedure (from the rules file)
!`f="$(python3 -I tools/sdd/sdd.py config --get paths.orchestrator 2>/dev/null || echo docs/ORCHESTRATOR.md)"; out="$(awk '/^#+ /{if(p)exit; if($0 ~ /^### Recording an owner rule[[:space:]]*$/)p=1} p' "$f" 2>/dev/null)"; if [ -n "$out" ]; then printf '%s\n' "$out"; else echo "(no section \"Recording an owner rule\" in $f: add it from the kit's templates/docs/ORCHESTRATOR.md)"; fi`
