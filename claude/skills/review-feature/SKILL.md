---
name: review-feature
description: Review an implemented feature branch with the four reviewer agents (spec-compliance, security, test-adequacy, perf), adversarially verify blocking findings with skeptical verifiers, fix confirmed findings and re-gate. Use after implementation, on PRs, or to re-review after changes.
argument-hint: "<F-id e.g. F011> <slug> [finalize]"
allowed-tools: Read Grep Bash(git *) Bash(python3 -I tools/sdd/sdd.py config *)
---
# Review $0 ($1)

## Diff under review
!`git log --oneline main..HEAD 2>/dev/null | head -30; git diff --stat main...HEAD 2>/dev/null | tail -5`

## Run
Use the Workflow tool:
`Workflow({scriptPath: "${CLAUDE_PROJECT_DIR}/tools/sdd/pipeline/feature-pipeline.js", args: {feature: "$0", slug: "$1", root: "<worktree path if any>", config: <sdd config --pipeline>, stages: ["gates", "review", "verify"]}})`.
Add `"finalize"` to `stages` when the third argument is `finalize`. That writes review.md,
sets `Status: Implemented`, runs `sdd trace --check`, and commits.

What happens:
- **Review**: four reviewers in parallel, each read-only with a fresh context. A
  security-sensitive diff gets a second, independent `security-reviewer` run and
  `needsSecuritySignoff` is set.
- **Verify**: each blocking finding goes to `pipeline.skeptics` verifiers. A finding is
  dismissed only if every skeptic refutes it. Confirmed findings go to `implementer` (code)
  or `test-author` (tests); gates re-run; a verifier fix-checks each fix (two verifiers when
  security-sensitive).

Without the workflow (a quick manual review), launch the four reviewer agents in parallel
with the Agent tool, giving each the diff range `$(git merge-base main HEAD)..HEAD` and the
feature directory. Then send each blocking finding to the `verifier` agent. Act only on
findings the verifier could not refute.

Report the blocking findings that were confirmed, refuted, fixed, or still unresolved, and
any reviewer that failed to return (a coverage gap blocks finalization).
