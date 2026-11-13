---
name: gates
description: Run the project's verification gates (sdd gates, or --fast) and summarise failures per gate with the decisive error lines. Use before committing, before declaring a task done, and when a pipeline stage reports blocked-gates.
argument-hint: "[--fast] [--feature FNNN]"
allowed-tools: Bash(python3 -I tools/sdd/sdd.py gates *) Bash(sdd gates *) Bash(python3 -I tools/sdd/sdd.py check *) Bash(sdd check *) Bash(python3 -I tools/sdd/sdd.py baseline --report*) Bash(tail *) Bash(grep *) Read
---
# Gates

Run from the repository root (in a worktree, its root):

```sh
python3 -I tools/sdd/sdd.py gates --fast $ARGUMENTS     # fast gates: before every commit
python3 -I tools/sdd/sdd.py gates --detached            # full gates, in the background
python3 -I tools/sdd/sdd.py gates --wait <log> --poll 60   # block until GATES_EXIT=<n>
```

- `--fast` (= `--plan fast`) runs `gates.fast` from `sdd.config.json`; without it every
  `gates.full` gate runs; `--plan nightly` runs the nightly plan (full-repo coverage and
  mutation, long fuzzing, benchmarks).
- Quality checks run alone too: `sdd check <size|arch|coverage|mutation|baseline|commits|secrets|<name>> [--json]`.
  Exit 0 pass, 1 violations, 2 configuration/tool error, 3 skipped (a required tool is missing;
  the row says how to install it). With `CI=true` a missing tool is a failure, never a skip.
- `sdd baseline --report` lists every current violation by kind and file (`new`, `baselined`,
  `grown`, stale). Baselined violations pass; new or grown ones fail; a fixed violation fails as
  stale until `sdd baseline --update` removes its entry. Nothing ever adds a baseline entry
  except `sdd baseline --init` at adoption.
- Full runs can exceed a single shell call's time limit: always run them detached and poll.
  A log without a `GATES_EXIT=<n>` line is an incomplete run; never report it as a result.
- Logs are in `<paths.logs_dir>/`.

Report per failing gate: the gate name, the first decisive error (file:line), and the likely
cause; report a SKIP row with its install hint as an infrastructure gap. Fix root causes.
**Never** lower a threshold, skip or ignore a test, delete assertions, add a reasonless lint
suppression or `cov:unreachable` marker, add a baseline entry or exclude glob, bypass the git
hooks (`--no-verify`, `core.hooksPath`), or edit protected paths. If a failure is in a
test you believe is wrong against the spec, report it as a test defect with the citation
instead of touching it. If the gate tooling itself is broken or missing, say so explicitly:
that is an infrastructure issue, not something to work around.
