---
name: feature
description: Run the full spec-driven lifecycle for one roadmap feature (Specify, Critic, Approve, Plan, Tests, Implement, Gates, Review, Verify, Finalize) via the feature-pipeline workflow in its own worktree. Use when the orchestrator starts or resumes work on a feature id like F011.
argument-hint: "<F-id e.g. F011> <slug e.g. user-login> [stage ...]"
allowed-tools: Read Grep Bash(git status *) Bash(git log *) Bash(git branch *) Bash(git worktree *) Bash(python3 -I tools/sdd/sdd.py *) Bash(sdd *)
---
# Feature lifecycle: $0 $1

## Current state
!`git branch --show-current 2>/dev/null; git status --short 2>/dev/null | head -20; python3 -I tools/sdd/sdd.py spec-info "$(python3 -I tools/sdd/sdd.py config --get paths.features_dir)/${0#F}-$1" 2>/dev/null || echo "no spec yet"`

## Pipeline config (pass as args.config)
!`python3 -I tools/sdd/sdd.py config --pipeline 2>/dev/null | head -120`

## Steps (you are the orchestrator and product owner)
1. **Check the roadmap.** It must contain $0 with this slug and its dependencies merged. If
   not, stop and ask the architect or human: the roadmap owns feature numbers.
2. **Worktree.** Run each feature in its own worktree `<worktree_root>/$0` (the pipeline's
   Setup creates it from `main` when `root` is given). Never switch branches in a checkout
   another agent is using.
3. **Run the pipeline** with the Workflow tool:
   `Workflow({scriptPath: "${CLAUDE_PROJECT_DIR}/tools/sdd/pipeline/feature-pipeline.js", args: {feature: "$0", slug: "$1", root: "${CLAUDE_PROJECT_DIR}/<worktree_root>/$0", config: <the JSON above>}})`.
   Add `stages: [...]` when extra arguments are given (`$ARGUMENTS` beyond the first two),
   chosen from: specify, critic, approve, plan, tests, implement, gates, review, verify,
   finalize. Optional args: `approval: "agent"` (a product-owner agent approves; the default
   `"orchestrator"` stops for you), `isolation`, `notes`, `security: true`, `hard: true`
   (top-tier model for spec/critic/plan/security/verifier roles), `models`/`modelsFromPhase`
   (tier change on resume), `poResolved: true` (you resolved a non-converging spec).
4. **Act on `status`.** Always read `nextAction`.
   - `awaiting-approval`: you are the product owner. Read spec.md and the critic counts;
     check scope against the roadmap and that `sdd spec-info` reports zero clarification
     markers; decide open product questions under *Decisions*; never invent normative
     behaviour. Set `Status: Approved`, commit `docs($0): approve spec` with the trailer, and
     re-run with `stages: ["plan","tests","implement","gates","review","verify","finalize"]`.
   - `blocked-*`: read the reason, the gate logs (`<logs_dir>/*.log`) and review.md. Fix the
     input (spec, tasks, tests) or the infrastructure, then re-run from the failing stage.
   - `paused`: usage limit or outage. Resume later with `resumeFromRunId` and the same args
     (completed agents replay from the journal); to change model tiers for the remaining
     phases pass `models` + `modelsFromPhase` + `modelsBefore`.
   - `ready-to-merge`: if `needsSecuritySignoff`, review the security sections of review.md,
     demand cheap hardening fixes, have a fresh `verifier` re-check each fix, record the
     sign-off in review.md. Then merge `main` into the feature branch (in the worktree),
     resolve conflicts (`sdd union-merge` for manifests, an implementer for sources), run full
     `sdd gates` there, merge the branch into `main` with `--no-ff`, run `sdd trace --check`
     on main, delete the worktree's build output and remove the worktree.
5. Keep at most `pipeline.max_parallel_features` features running; watch host disk.

Details and reasons: docs/PLAYBOOK.md (kit) and docs/DEVELOPMENT.md (project).
