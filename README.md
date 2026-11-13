# agentic-sdd-kit

A reusable, project-agnostic kit for **spec-driven development by AI agents** under Claude
Code. It was extracted from [ferrocore](https://github.com/aruizrab/ferrocore) (a 3GPP 5G
core in Rust built almost entirely by agents), stripped of everything Rust- or 3GPP-specific,
and generalised for any language and any kind of normative source.

The idea in one paragraph: a **normative source** (a standard, an API contract, product
requirements) is turned into atomic requirements with stable ids; a different agent writes
failing tests from those requirements; implementers make them pass in small, wave-ordered
tasks; configured gates, four read-only reviewers and skeptical verifiers check the result;
every requirement is traced to an implementation site and a test; a product owner approves
specs and signs off security-sensitive changes. **Anything that must always happen is a
hook, a gate or a tool, never a prompt.**

## What is in the box

```
agentic-sdd-kit/
  install.sh                 non-destructive installer (never overwrites without --force)
  bin/sdd                    wrapper: finds the project's tools/sdd/sdd.py
  tools/sdd/                 the `sdd` CLI (Python 3.8+, standard library only)
    sdd.py, sddkit/          config, trace, gates, new-feature, progress, union-merge,
                             spec-info/markers/tasks, source, hooks, doctor
    sddkit/quality/          quality checks (size, arch, coverage, mutation, command checks,
                             commits, secrets), the shrink-only baseline, git hook logic
    githooks/                pre-commit, commit-msg, pre-push (installed by `sdd hooks install`)
    pipeline/feature-pipeline.js   the per-feature Workflow script (Claude Code Workflow tool)
  claude/                    -> .claude/: 12 agents, 6 skills, 4 hooks, settings.json
  templates/                 CLAUDE.md, docs/{CONSTITUTION,DEVELOPMENT,ROADMAP}.md,
                             specs/templates/{spec,design,tasks,review}.md, sdd.config.json
  examples/configs/          sdd.config.json for Rust, TypeScript, Python, Go (incl. quality gates)
  docs/                      PLAYBOOK (how to run it), LESSONS (why the rules exist),
                             ADAPTING (per-language gates), CONFIG (reference)
  tests/                     unit tests (python -m unittest) and a pipeline smoke test (node)
```

Everything project-specific lives in **one file, `sdd.config.json`** at the project root:
name and threat model, languages, gate commands (fast/full/nightly, each with name, command,
timeout), quality-gate rules (size limits, layers, coverage and mutation tools, command
checks, hook rules), test commands, requirement id regex, trace tag keywords and scan globs, component
regex, protected / append-only / role-scoped paths, formatters, normative source lookup,
explicit model ids per tier, worktree root, parallelism caps, commit trailers, shared build
cache environment.

## Quick start

```sh
git clone <this kit> ~/dev/agentic-sdd-kit
~/dev/agentic-sdd-kit/install.sh ~/dev/my-project --name my-project --config rust   # or typescript|python|go
cd ~/dev/my-project
$EDITOR sdd.config.json CLAUDE.md docs/CONSTITUTION.md docs/ROADMAP.md
python3 -I tools/sdd/sdd.py config --validate && python3 -I tools/sdd/sdd.py doctor
git add -A && git commit -m "chore: install agentic-sdd-kit"
python3 -I tools/sdd/sdd.py hooks install      # git hooks: pre-commit, commit-msg, pre-push
```

On an existing codebase, record today's violations once before the first feature
(`sdd baseline --init`, see "Quality gates" below and docs/PLAYBOOK.md).

Then, in Claude Code at the project root:

```
/feature F001 my-first-feature
```

The orchestrator session runs the pipeline in the worktree `.wt/F001`, stops after the critic
with `awaiting-approval`, you (or a product-owner agent with `approval: "agent"`) approve the
spec, and the run continues through tests, implementation, gates, review, verification and
finalisation to `ready-to-merge`. `docs/PLAYBOOK.md` describes the whole loop, including
parallel features, resuming paused runs, usage limits, model tiering and the security
sign-off.

The pipeline is built to survive long unattended runs: every agent gets a **scope rule**
(`strictScope`, on by default: a relayed chat message never redirects an agent), gate runs are
**detached and keyed by HEAD** (an agent ending its turn cannot kill them, and an incomplete run
is re-run up to 3 times instead of being counted as a failure), read-only roles can **read via
git** instead of building worktrees (`readViaGit`), review findings are **committed as soon as
they exist**, interrupted work is **never discarded**, reviewers flag **over-engineering**, and
worktrees can share a **size-capped build cache** (`build_cache.env`, never on tmpfs).

Put `bin/sdd` on your PATH to type `sdd gates --fast` instead of
`python3 -I tools/sdd/sdd.py gates --fast`.

## The tools

| Command | What it does |
|---|---|
| `sdd config [--validate\|--pipeline [--compact]\|--get key]` | show/validate `sdd.config.json`; `--pipeline` prints the subset the Workflow script takes as `args.config` (`--compact`: one line, for embedding in a prompt) |
| `sdd trace [--feature NNN] [--check] [--json] [--include PATH]` | requirement -> implementation -> test matrix; `--check` is the CI gate (orphans, duplicates, uncovered `must`); `--include` also scans a file outside `trace.scan_globs` and lists it as `outsideScan` (the pipeline's red check) |
| `sdd gates [--fast\|--plan fast\|full\|nightly] [--only g] [--detached [--feature F --tag T] [--fresh]] [--wait LOG]` | runs the configured gate plan, prints a summary table, exit code; every log ends with `GATES_EXIT=<n>`; missing tools SKIP with an install hint, or FAIL when `CI=true`. `--detached --feature --tag` keys the log on HEAD (`<logs_dir>/gates/F-T-<sha7>[-dirty].log`), attaches to a live run or reuses a completed one (`--fresh`: never reuse); `--wait` exits 124 while still running, 125 for an incomplete run |
| `sdd check NAME [--json] [--staged] [--no-run]` | one quality check: `size`, `arch`, `coverage`, `mutation`, `commits`, `secrets`, `baseline` or a `quality.checks` entry; exit 0 pass, 1 violations, 2 config/tool error, 3 tool missing |
| `sdd baseline [--report [--json]\|--init\|--update\|--resolve\|--merge-driver O A B]` | the shrink-only violation baseline: report every violation by kind and file, record it once at adoption, shrink it, resolve merge conflicts mechanically |
| `sdd hooks install [--force]`, `sdd hooks run NAME` | sets `core.hooksPath` to the kit's git hooks (refuses over hooks already in use); the hook logic |
| `sdd new-feature NNN slug` | scaffolds the feature directory from the templates and prints the feature's requirement id block |
| `sdd progress [--json]` | lifecycle-stage progress per feature, milestone and overall from the roadmap and git refs |
| `sdd union-merge [files]` | resolves additive manifest conflicts only (union); refuses everything else |
| `sdd spec-info DIR`, `sdd markers FILE` | Status, open clarification markers (ignores comments, code spans, prose), requirement counts |
| `sdd tasks DIR [--lanes] [--validate]` | tasks.md as JSON: waves, tasks, components, lanes; plan exit criteria |
| `sdd source clause DOC SECTION`, `sdd source find DOC REGEX` | normative source lookup (configured command or built-in Markdown reader) |
| `sdd hook NAME` | the hook logic behind `.claude/hooks/*.sh` (protect-paths, bash-guard, format, stop-check) |
| `sdd doctor` | checks the installation and the configuration |

## Quality gates

Gates are the law; prompts are not. Besides the project's own fmt/lint/test commands, the kit
ships language-agnostic quality checks that every plan can run (`sdd check <name>`):

| Check | Rule (defaults) |
|---|---|
| `size` | code lines per function (fail above 80) and per file (warn above 600, fail above 1000); blank, comment and doc lines do not count; functions found by documented heuristics for Rust, Python, TypeScript/JavaScript and Go (`quality.languages` maps more extensions) |
| `arch` | declarative layers: module globs -> allowed dependencies, forbidden APIs per layer as regexes; dependencies extracted from imports and qualified usages per language |
| `coverage` | 100 % of the hand-written lines changed since the merge-base with the main branch, from any lcov or Cobertura producer; generated code excluded; `cov:unreachable: <reason>` excludes a line and is listed; a marker without a reason fails; a changed file no test loads fails |
| `mutation` | zero surviving mutants in changed code from any diff-mutation tool (cargo-mutants, mutmut, Stryker, ...) unless review.md §6 justifies one |
| command checks | `quality.checks`: docs/warnings, unused dependencies, flaky tests, ... as commands whose output a regex turns into violations |
| `commits`, `secrets` | the git-hook rules re-checked over the whole branch (CI backstop) |
| `baseline` | the baseline file may only shrink |

**Plans.** `gates.fast` (pre-push, every task) holds fmt, lint, test, trace, size, arch,
baseline, commits, secrets; `gates.full` (pipeline Gates stage, pull-request CI) adds coverage,
mutation and the command checks; `gates.nightly` holds the full-repository runs. A check whose
tool is not installed shows as SKIP with the install command; with `CI=true` it fails.

**Git hooks** (`sdd hooks install`): pre-commit scans the staged lines for secrets (built-in
rules, plus gitleaks when installed), checks staged sizes and runs the configured fast steps;
commit-msg enforces Conventional Commits, the required trailer lines and runs the tests of the
touched components (skipped for `test(...)` commits, docs-only and merge commits); pre-push runs
`sdd gates --fast`. Agents never bypass them; CI re-runs the same rules.

**Baseline.** Violations that exist when the gates are adopted go into `sdd.baseline.json`
(`sdd baseline --init`). It only shrinks: a fixed violation's entry must be removed
(`--update`), a grown or new violation fails, nothing ever adds an entry again. New code is
never grandfathered. `sdd baseline --report` is the work list of the remediation feature
(docs/PLAYBOOK.md, "Adopting the gates on an existing codebase").

Configuration: docs/CONFIG.md (`quality.*`); per-language commands and heuristics:
docs/ADAPTING.md.

## Testing the kit

```sh
python3 -I -m unittest discover -s tests -v     # 105 tests (or: python3 -m pytest -q tests); synthetic git repos under .scratch/
node tests/pipeline_smoke.mjs                   # drives feature-pipeline.js with a fake agent()
```

## Status and provenance

Version 0.1.0. The workflow, roles, gates and lessons come from running ferrocore's pipeline
on a dozen features in November 2026; `docs/LESSONS.md` lists what went wrong there and which
rule or default in this kit prevents it. ferrocore is cited as the worked example throughout
the docs; nothing in the generic parts depends on it.

License: MIT.
