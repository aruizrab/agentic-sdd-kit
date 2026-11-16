---
name: owner-rule
description: Record a standing rule or direction from the human owner in the orchestrator guide (paths.orchestrator, default docs/ORCHESTRATOR.md, the tracked source of truth) in the same turn, so it survives compaction. Use whenever the owner states how work should be done from now on ("always...", "never...", "from now on...", "keep N pipelines...", a priority, a limit, a policy, who decides what), corrects an earlier rule, or says "I already told you". Main session (orchestrator) only.
argument-hint: "[the rule, in the owner's words]"
allowed-tools: Read Grep Edit Bash(python3 -I tools/sdd/sdd.py config *) Bash(sdd config *) Bash(git status *) Bash(git diff *) Bash(git log *) Bash(git add *) Bash(git commit *)
---
# Record an owner rule

Rules kept only in session memory drift across compactions; a reference project's
orchestrator made the owner repeat the same rules many times (the kit's docs/LESSONS.md 49
and 53). This skill puts the rule in the repository, now. It is for the main session only: the
`main-only-skills` hook blocks it for subagents (`orchestrator.main_only_skills`); a subagent
reports an owner rule to the orchestrator in its final message instead.

## Where the rules live
!`f="$(python3 -I tools/sdd/sdd.py config --get paths.orchestrator 2>/dev/null || echo docs/ORCHESTRATOR.md)"; echo "rules file: $f"; echo "process rules for every agent: $(python3 -I tools/sdd/sdd.py config --get paths.development 2>/dev/null || echo docs/DEVELOPMENT.md)"; echo "commit trailers: $(python3 -I tools/sdd/sdd.py config --get commit.trailers 2>/dev/null | tr -d '\n')"; echo; grep -n '^## \|^### ' "$f" 2>/dev/null || echo "(no rules file yet: install it from the kit's templates/docs/ORCHESTRATOR.md)"`

## Steps
1. **Quote the rule.** Take the owner's words (`$ARGUMENTS`, or the owner's latest message) and
   restate it as one imperative line with the date and "owner" as the source, e.g.
   `- **Concurrency:** up to 4 implementation pipelines (owner, <yyyy-mm-dd>).`
2. **Find its home.** Read the rules file above. Put the rule in the section that owns its
   topic (authority and escalation, launching, how much runs at once, component claims and
   merge order, models and host, reporting); if none does, add a row to the owner-rules
   table at the end. A process rule for every agent (not only the orchestrator) belongs in
   the development guide instead. A rule that changes protected configuration (`.claude/**`,
   the constitution, `sdd.config.json`, CI) becomes a patch file for the owner to apply (the
   rules file's authority section; kit PLAYBOOK §16), not an edit.
3. **Check for conflicts.** If the new rule contradicts or narrows an existing line, do not
   keep both: replace the old line. If it is unclear which one the owner wants, ask one short
   question quoting both before writing anything.
4. **Edit and commit** in the same turn: `docs(orchestrator): <rule in a few words>` with the
   commit trailers above. Stage and commit only the rules file (or the development guide),
   even if other files are dirty.
5. **Tell the owner** in one line: the rule as recorded and where, e.g.
   "Recorded in docs/ORCHESTRATOR.md §4: up to 4 implementation pipelines."
6. **Apply it now.** If the rule changes what should be running (more or fewer pipelines, a
   pause, a new priority), act on it in this turn or schedule it, following the launch rules
   in the rules file.

Do not store the rule in session memory: memory may point to the rules file, never hold the
rule text.
