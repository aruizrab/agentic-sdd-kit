# .claude template

`install.sh` copies this directory to `<target>/.claude/`. Agents, skills, hooks and
`settings.json` are generic; project specifics come from `sdd.config.json` at the target
root (protected paths, formatters, gates, source lookup, model ids).

- `agents/`: spec-author, spec-critic, product-owner, architect-planner, test-author,
  implementer, integrator, spec-compliance-reviewer, security-reviewer, perf-reviewer,
  test-adequacy-reviewer, verifier.
- `skills/`: feature, gates, trace, source, new-feature, review-feature, and the two
  orchestrator-only skills owner-rule (record an owner's standing rule in
  `paths.orchestrator` in the same turn) and compact-handoff (write the live state to
  `paths.handoff` before `/compact`). Both set `disable-model-invocation: true`: no model
  context lists them, the owner types them, and their bodies pull the procedure from the
  orchestrator guide's §7 (one copy).
- `hooks/`: thin wrappers around `sdd hook <name>` (protect-paths, bash-guard,
  main-only-skills, orchestrator-context, format, stop-check). `orchestrator-context`
  (SessionStart, main session only) reminds the orchestrator of its two habits at startup,
  resume, clear and after every compaction. `main-only-skills` (PreToolUse `Skill`) is the
  backstop that blocks the skills listed in `orchestrator.main_only_skills` for every
  subagent; that is the only restriction on skills, all other skills stay available to
  subagents.
- `settings.json`: hook wiring, permission allow/deny lists (deny includes destructive git
  commands), `worktree.baseRef: head`.

Add project-specific entries to the `allow` list (e.g. `Bash(cargo *)`, `Bash(npm *)`).
