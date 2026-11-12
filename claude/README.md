# .claude template

`install.sh` copies this directory to `<target>/.claude/`. Agents, skills, hooks and
`settings.json` are generic; project specifics come from `sdd.config.json` at the target
root (protected paths, formatters, gates, source lookup, model ids).

- `agents/`: spec-author, spec-critic, product-owner, architect-planner, test-author,
  implementer, integrator, spec-compliance-reviewer, security-reviewer, perf-reviewer,
  test-adequacy-reviewer, verifier.
- `skills/`: feature, gates, trace, source, new-feature, review-feature.
- `hooks/`: thin wrappers around `sdd hook <name>` (protect-paths, bash-guard, format,
  stop-check).
- `settings.json`: hook wiring, permission allow/deny lists (deny includes destructive git
  commands), `worktree.baseRef: head`.

Add project-specific entries to the `allow` list (e.g. `Bash(cargo *)`, `Bash(npm *)`).
