# {{FID}} — <Feature title> — Tasks

Feature: {{FID}}
Design: ./design.md
Security-sensitive: no
Tests commit (red check): <sha, filled in by the pipeline>

<!--
Task line format (one line; parsed by `sdd tasks` and the feature pipeline):

- [ ] T01 [P] <imperative description> — files: <path>, <path> — verifies: <ID>, <ID> — depends: <T ids or -> — complexity: low|medium|high — risk: security|concurrency|none

  - `[ ]` / `[x]`       open / done (set by the implementer's commit, or by the integrator after a lane merge)
  - `T01`               task id, unique within the feature, two digits
  - `[P]`               optional: may run in parallel with the other [P] tasks of the same wave.
                        Only when its files (and component) are disjoint from them.
  - files:              files the task owns (create/modify). Implementers stay inside them.
  - verifies:           requirement ids this task implements (its tests already exist)
  - depends:            tasks that must be done first (always in an earlier wave), or "-"
  - complexity:         optional (default medium): low = a local change following an existing pattern;
                        high = a new algorithm or state machine, a cross-component change, or concurrency
  - risk:               optional (default none): security (authentication, cryptography, secrets,
                        untrusted input) or concurrency (shared state, ordering, async/threads)
                        The model router uses both to pick the model of the task's implementer.

Optional indented context bullets make the task self-contained for an implementer:
  - context: component <name>; source <doc> §<section> (read via `sdd source clause <doc> <section>`)
  - context: tests to turn green: <path>::<test name>
  - context: emit: <event names>

Waves: tasks in Wave N depend only on Waves < N. Within a wave the pipeline runs [P] tasks of
disjoint components in parallel worktrees, and runs same-component tasks sequentially.
Root files count as the root component (`components.root`). Validate with
`sdd tasks <feature dir> --validate`.
-->

## Wave 1

- [ ] T01 <description> — files: <path> — verifies: <ID> — depends: - — complexity: low — risk: none
  - context: <...>

## Wave 2

- [ ] T02 [P] <description> — files: <path in component a> — verifies: <ID>, <ID> — depends: T01 — complexity: high — risk: security
  - context: <...>
- [ ] T03 [P] <description> — files: <path in component b> — verifies: <ID> — depends: T01
  - context: <...>

## Wave 3

- [ ] T04 <wire-up / integration> — files: <path> — verifies: <ID> — depends: T02, T03
  - context: <...>
