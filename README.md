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
    pipeline/feature-pipeline.js   the per-feature Workflow script (Claude Code Workflow tool)
  claude/                    -> .claude/: 12 agents, 6 skills, 4 hooks, settings.json
  templates/                 CLAUDE.md, docs/{CONSTITUTION,DEVELOPMENT,ROADMAP}.md,
                             specs/templates/{spec,design,tasks,review}.md, sdd.config.json
  examples/configs/          sdd.config.json for Rust, TypeScript, Python, Go
  docs/                      PLAYBOOK (how to run it), LESSONS (why the rules exist),
                             ADAPTING (per-language gates), CONFIG (reference)
  tests/                     unit tests (python -m unittest) and a pipeline smoke test (node)
```

Everything project-specific lives in **one file, `sdd.config.json`** at the project root:
name and threat model, languages, gate commands (fast/full, each with name, command,
timeout), test commands, requirement id regex, trace tag keywords and scan globs, component
regex, protected / append-only / role-scoped paths, formatters, normative source lookup,
explicit model ids per tier, worktree root, parallelism caps, commit trailers.

## Quick start

```sh
git clone <this kit> ~/dev/agentic-sdd-kit
~/dev/agentic-sdd-kit/install.sh ~/dev/my-project --name my-project --config rust   # or typescript|python|go
cd ~/dev/my-project
$EDITOR sdd.config.json CLAUDE.md docs/CONSTITUTION.md docs/ROADMAP.md
python3 -I tools/sdd/sdd.py config --validate && python3 -I tools/sdd/sdd.py doctor
git add -A && git commit -m "chore: install agentic-sdd-kit"
```

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

Put `bin/sdd` on your PATH to type `sdd gates --fast` instead of
`python3 -I tools/sdd/sdd.py gates --fast`.

## The tools

| Command | What it does |
|---|---|
| `sdd config [--validate\|--pipeline [--compact]\|--get key]` | show/validate `sdd.config.json`; `--pipeline` prints the subset the Workflow script takes as `args.config` (`--compact`: one line, for embedding in a prompt) |
| `sdd trace [--feature NNN] [--check] [--json]` | requirement -> implementation -> test matrix; `--check` is the CI gate (orphans, duplicates, uncovered `must`) |
| `sdd gates [--fast] [--only g] [--detached] [--wait LOG]` | runs the configured gates, prints a summary table, exit code; every log ends with `GATES_EXIT=<n>` |
| `sdd new-feature NNN slug` | scaffolds the feature directory from the templates and prints the feature's requirement id block |
| `sdd progress [--json]` | lifecycle-stage progress per feature, milestone and overall from the roadmap and git refs |
| `sdd union-merge [files]` | resolves additive manifest conflicts only (union); refuses everything else |
| `sdd spec-info DIR`, `sdd markers FILE` | Status, open clarification markers (ignores comments, code spans, prose), requirement counts |
| `sdd tasks DIR [--lanes] [--validate]` | tasks.md as JSON: waves, tasks, components, lanes; plan exit criteria |
| `sdd source clause DOC SECTION`, `sdd source find DOC REGEX` | normative source lookup (configured command or built-in Markdown reader) |
| `sdd hook NAME` | the hook logic behind `.claude/hooks/*.sh` (protect-paths, bash-guard, format, stop-check) |
| `sdd doctor` | checks the installation and the configuration |

## Testing the kit

```sh
python3 -I -m unittest discover -s tests -v     # 31 tests (or: python3 -m pytest -q tests); synthetic git repos under .scratch/
node tests/pipeline_smoke.mjs                   # drives feature-pipeline.js with a fake agent()
```

## Status and provenance

Version 0.1.0. The workflow, roles, gates and lessons come from running ferrocore's pipeline
on a dozen features in November 2026; `docs/LESSONS.md` lists what went wrong there and which
rule or default in this kit prevents it. ferrocore is cited as the worked example throughout
the docs; nothing in the generic parts depends on it.

License: MIT.
