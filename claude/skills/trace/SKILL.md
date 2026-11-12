---
name: trace
description: Show the requirement traceability matrix (requirements -> implementation sites -> verifying tests) for a feature or the whole project, and explain gaps. Use to check coverage before review/finalize or when trace --check fails.
argument-hint: "[--feature NNN] [--check]"
allowed-tools: Bash(python3 -I tools/sdd/sdd.py trace *) Bash(sdd trace *) Read Grep
---
# Traceability

!`python3 -I tools/sdd/sdd.py trace $ARGUMENTS 2>&1 | tail -60 || true`

Commands:
- `sdd trace --feature 011` shows the matrix for one feature.
- `sdd trace --check [--feature 011]` is the CI gate. It fails on: unknown or malformed ids
  in tags (orphans, typos); duplicate requirement ids across features (ids are global); a
  `must` requirement of a feature in `requirements.enforce_for_statuses` with no verifying
  test or no implementation site (unless `[behaviour-only]`).

Tag syntax (configured in `sdd.config.json` `trace`; defaults):
- Implementation: a comment line `Implements: <ID>, <ID>` (any comment syntax) or an
  attribute/decorator/call `implements("<ID>")`.
- Tests: `Verifies: <ID>` or `verifies("<ID>")`.
- Requirement line in spec.md: `- **<ID>** [must] WHEN ... THE X SHALL ... <citation>`.

When reporting gaps, group them as: not implemented (implementer work); not tested
(test-author work); tag typos or orphans; requirements that should be superseded
(spec-author work, needs re-approval). Never "fix" a gap by adding a tag to code that does
not implement the requirement, or to a test that does not assert it.
