# {{FID}} — <Feature title> — Review record

Feature: {{FID}}
Branch: {{BRANCH}}
Diff range: <base sha>..<head sha>
Tests commit (red check): <sha>
Pipeline run: <workflow run id / date>
Result: <ready-to-merge | blocked: reason>

## 1. Spec approval (product owner)

| Item | Value |
|---|---|
| Critic rounds | <n>; last verdict <pass/revise>; product-owner resolution passes <n> |
| Requirement count at approval | <n> (budget <min>-<max>) |
| Open clarification markers at approval | 0 |
| Approved by | orchestrator (product owner) / product-owner agent / human <name> |
| Approval commit | <sha> |
| Notes | <decisions taken, scope trade-offs, deferred items> |

## 2. Gates

| Gate | Result | Notes / log |
|---|---|---|
| fmt | pass | |
| lint | pass | |
| test | pass | |
| trace | pass | must reqs: <n>/<n> implemented + verified |
| <oracle> | pass | |
| <fuzz> | pass / skipped | |
| <coverage / mutation> | <x %> / <missed n> | survivors justified below |
| <supply chain> | pass | |
| <perf> | <delta %> vs baseline <sha> | exception: none / recorded in §5 |

Gate fix rounds: <n> (`.sdd/logs/*.log`). Test arbitration: none / <tests amended, see design.md §13>.

## 3. Reviewer findings

| ID | Reviewer | Severity | Req / source | Location | Claim | Verifier votes (refuted?) | Outcome |
|---|---|---|---|---|---|---|---|
| R1 | spec-compliance | blocking | <ID> / §<section> | <file:line> | <...> | no / yes | confirmed → fixed in <sha>, fix-check passed |

Non-blocking findings (recorded, not gating):
- <...>

## 4. Security

| Item | Value |
|---|---|
| Security-sensitive | yes / no (why) |
| Security review #1 | <n findings, blocking n> |
| Independent security review #2 (if sensitive) | <n findings, blocking n> |
| Fix re-verification | <each confirmed finding re-checked by two independent verifiers> |
| Product-owner security sign-off | <name/role, date> or "not required" |

## 5. Product-owner decisions and exceptions

- Constitution exceptions: <none, or: article, justification, sign-off>.
- Performance exception: <none, or: measurement, reason, decided by, date>.

## 6. Coverage gaps and mutation survivors (justified)

`sdd check mutation` accepts a surviving mutant only with a line in exactly this form (same file
and mutation text as the tool prints, an em dash, a non-empty reason; the line is optional):

<!--
- mutant: <file>[:<line>]: <mutation> — <why no test can kill it (equivalent mutant, ...)>
-->

Coverage gaps are not justified here: they are marked in the code with
`cov:unreachable: <reason>` (comment syntax of the language) and listed by `sdd check coverage`.
Markers on changed lines (count and list from the coverage gate): <n>; reviewed by
test-adequacy-reviewer: <yes/no>.

## 7. Independent evidence

<oracle runs, contract tests, interop or differential results, paths>

## 8. Final

- Trace check: `sdd trace --check --feature {{NNN}}` → <pass/fail>
- Status set to Implemented in commit: <sha>
- Merged into main: <sha / pending orchestrator>; worktree removed, build output deleted: <yes/no>
