# `sdd.config.json` reference

One JSON file at the project root. Missing keys take the defaults in
`tools/sdd/sddkit/config.py`; `sdd config` prints the effective configuration,
`sdd config --validate` reports problems, `sdd config --pipeline` prints the subset the
Workflow script takes as `args.config`.

| Key | Meaning | Default |
|---|---|---|
| `project.name` | project name (used in prompts, `{{PROJECT}}` in templates) | `my-project` |
| `project.description` | one line for prompts | `""` |
| `project.languages` | informational | `[]` |
| `project.threat_model` | binding statement of what is untrusted and what is out of scope; quoted to every agent | `""` |
| `paths.features_dir` | per-feature spec directories | `specs/features` |
| `paths.templates_dir` | templates copied by `sdd new-feature` | `specs/templates` |
| `paths.roadmap` / `constitution` / `development` | document paths | `docs/ROADMAP.md`, `docs/CONSTITUTION.md`, `docs/DEVELOPMENT.md` |
| `paths.logs_dir` | gate logs (`gates-<tag>.log`; pipeline runs keyed by HEAD under `gates/<feature>-<tag>-<sha7>[-dirty].log` with a `.pid` runner record) | `.sdd/logs` |
| `paths.worktree_root` | per-feature and throwaway worktrees | `.wt` |
| `paths.build_dirs` | build output to delete after merging; plain relative paths inside the project (no leading `/`, no `..`, no whitespace: the pipeline refuses them, and also refuses a project root or worktree path with whitespace or shell metacharacters) | `[]` |
| `paths.tools_dir` | where the kit tools are installed | `tools/sdd` |
| `features.id_prefix`, `id_digits`, `branch_prefix` | `F`, `3`, `feat/` -> `F011`, `feat/F011-slug` | |
| `features.main_branch` | the integration branch worktrees are created from and merged into (`master`, `trunk`, `develop`...) | `main` |
| `features.roadmap_milestone_regex`, `roadmap_feature_regex` | how `sdd progress` and `sdd new-feature` parse the roadmap; text after the slug of the form ` — <title>` is the feature's display title until its spec has an H1 title | `### M<n> — name`, `#### F<NNN> slug` |
| `features.roadmap_cross_cutting_regex` | heading of a roadmap section whose features belong to no milestone: `sdd progress` lists them as one extra group (`kind: "cross-cutting"`, id `CC`), counted in overall only; group 1 (optional) is the group name | `### Cross-cutting — name` |
| `features.done_without_spec` | feature ids counted as merged although they predate the pipeline | `[]` |
| `requirements.id_regex` | requirement id syntax (used by trace, spec-info, tasks) | `[A-Z][A-Z0-9]*(?:-[A-Z0-9.]+)*-R\d{2,3}` |
| `requirements.priorities` | first is the enforced one | `["must","should","may"]` |
| `requirements.block_size` | ids per feature block (`F013` -> `R130-R139`) | `10` |
| `requirements.budget` (`min`, `max`) | proportionality budget (requirements per spec) | `{"min":15,"max":45}` |
| `requirements.statuses` | the spec lifecycle statuses, in order (informational: the enforcing keys are the two below) | `["Draft","Approved","Implemented"]` |
| `requirements.clarification_marker` | open-question marker counted by `sdd markers` | `[NEEDS CLARIFICATION:` |
| `requirements.enforce_for_statuses` | statuses whose `must` requirements need a verifying test | `["Implemented"]` |
| `requirements.require_impl_for_statuses` | statuses whose `must` requirements need an implementation site | `["Implemented"]` |
| `trace.scan_globs` / `exclude_globs` | files scanned for tags | `src/**/*`, `tests/**/*` / build dirs |
| `trace.implements_keywords` / `verifies_keywords` | tag keywords (case-insensitive) | `Implements` / `Verifies` |
| `trace.attribute_forms` | also recognise `implements("ID")` call/attribute/decorator forms | `true` |
| `trace.procedure_steps.enabled` | procedure step tables (PLAYBOOK §15): `sdd trace` parses step tables, Responsibilities tables, exemption lines and feature-wide reasons, prints the procedure step coverage (JSON: `procedures`), and `--check` fails on their defects and on a checked spec with no entry at all | `false` |
| `trace.procedure_steps.statuses` | spec statuses whose procedure-step defects fail `trace --check`; specs in other statuses get warnings | `["Approved","Implemented"]` |
| `trace.procedure_steps.source_check` | read each step table's `<doc> §<section>` through the `sources` lookup: its step labels must match the table's rows both ways, and each Responsibility must be quoted from its cited section. `false`: only the cells are checked (sources without machine-readable steps) | `true` |
| `trace.procedure_steps.label_regex` | step labels at the start of a line of a section's own text; named group `label`, canonicalised (`2a to 2c` -> `2a-2c`); `""` = the built-in grammar (`1`, `9a`, `7(A)`, `10.1`, `Step 25:`, ranges such as `14a-c`, `4a - 4b`, `1a and 1b`) | `""` |
| `components.path_regex` | first capture group = component of a file (lanes) | `^src/([^/]+)/` |
| `components.root`, `components.word` | component of root files; the word used in prompts | `workspace`, `component` |
| `gates.fast` / `gates.full` / `gates.nightly` | gate plans (`sdd gates --plan`): `{name, command, timeout, optional?, skip_if_missing?, env?, install?, skip_exit?}`; `install` is the hint shown on a SKIP row; `skip_exit` (0-255) is the exit code that means "tool missing, SKIP" (the templates set `3` on `sdd check` entries only); with `CI=true` every skip fails | `[]` |
| `quality.baseline_file` | the committed, shrink-only baseline; `sdd check baseline` uses the merge-base's value and fails a branch that renames it (`baseline-moved`) | `sdd.baseline.json` |
| `quality.base_ref` | base branch for "changed lines" (`SDD_BASE_REF` wins; ignored with `CI=true`, where `SDD_BASE_REF` is required; a value resolving to HEAD on a branch with its own commits is an error; else `features.main_branch`, else `origin/<main_branch>`) | `""` |
| `quality.generated` | globs of generated files: excluded from size, coverage and mutation | `[]` |
| `quality.test_globs` | extra test-code globs, added to `tests/**`, `**/tests/**`, `test/**`, `**/test/**` and the language registry's patterns; test code is excluded from coverage and mutation, still size-checked | `[]` |
| `quality.tool_dirs` | project-local tool directories (root-relative, e.g. `node_modules/.bin`) put in front of `PATH` for every gate, check and git hook command; `requires` and `skip_if_missing` look there too, so a missing project tool SKIPs with its install hint instead of being downloaded | `[]` |
| `quality.languages` | extra extension -> language (`rust`, `python`, `typescript`, `go`, or `none` = plain: file size only) | `{}` |
| `quality.size.globs` / `exclude` | measured files (git-tracked only); `[]` = every extension the registry knows. The installed kit (`<paths.tools_dir>/**`) is never project code: size, arch, coverage, mutation, the commit test step and `{files}` of the pre-commit steps always skip it | `[]` / `[]` |
| `quality.size.function_warn` / `function_fail` | code lines per function; `0` disables | `0` / `80` |
| `quality.size.file_warn` / `file_fail` | code lines per file; `0` disables | `600` / `1000` |
| `quality.arch.modules` | `[{name, paths, imports?, may_depend_on?, forbid?: [{pattern, reason}]}]`: a file belongs to the first module whose `paths` match; `imports` regexes map import specifiers to modules | `[]` |
| `quality.arch.exclude` | files not arch-checked | `[]` |
| `quality.coverage.command` / `report` / `format` | produces the report (`""` = read an existing one); lcov or Cobertura file; `lcov` or `cobertura` | `""` / `""` / `lcov` |
| `quality.coverage.globs` | hand-written source; `[]` = the effective size globs | `[]` |
| `quality.coverage.unmeasured_ok` | changed files that may be absent from the report (no executable code) | `[]` |
| `quality.coverage.ok_exits` | exit codes meaning "report written"; others are a tool error (exit 2) | `[0]` |
| `quality.coverage.requires` / `install` / `timeout` | programs needed on PATH, install hint, seconds | `[]` / `""` / `3600` |
| `quality.mutation.command` | diff-mutation command; `{diff_file}` and `{changed_files}` are substituted (shell-quoted), and so are the resource placeholders `{jobs}`, `{test_threads}`, `{memory_limit_kb}` and `{memcap_runner}` (the absolute path of `<tools_dir>/scripts/memcap-runner.sh`) | `""` |
| `quality.mutation.report` / `format` | file holding the results (`""` = the command output); `regex` or `mutation-testing-report` (Stryker's JSON schema) | `""` / `regex` |
| `quality.mutation.survivor_regex` | named groups `file`, `mutation`, optional `line` | `""` |
| `quality.mutation.ok_exits` | exit codes meaning "ran to completion" (cargo-mutants: `[0, 2, 3]`) | `[0]` |
| `quality.mutation.globs` / `requires` / `install` / `timeout` | as for coverage | `[]` / `[]` / `""` / `3600` |
| `quality.mutation.jobs` / `test_threads` | mutants tested in parallel and test threads per mutant run: pass them to the tool with `{jobs}` / `{test_threads}` (cargo-mutants `--jobs {jobs}` and `NEXTEST_TEST_THREADS`/`RUST_TEST_THREADS`, mutmut `--max-children`, Stryker `--concurrency`, gremlins `--workers`/`--test-cpu`); also exported as `SDD_MUTATION_JOBS` / `SDD_TEST_THREADS`. A mutant can turn a loop into a runaway allocator, so every parallel test process is a possible multi-GB allocation (docs/PLAYBOOK.md §14) | `2` / `3` |
| `quality.mutation.memory_limit_mib` | data memory cap of each test process (`ulimit -d`, RLIMIT_DATA: private writable memory, not reserved address space; exported as `SDD_MEM_LIMIT_KB`); `0` = no cap. With `{memcap_runner}` in the command or `env`, the tool's test-runner hook applies it to the test processes only (Rust: `env` `CARGO_TARGET_<TRIPLE>_RUNNER: "{memcap_runner}"`); otherwise the whole command runs under `memcap-runner.sh`, every process (compilers too) with its own cap. A runaway mutant then fails alone and counts as caught. If the unmutated baseline fails only because of the cap, raise it; never remove it. Node may use `0` and a heap cap instead (`NODE_OPTIONS=--max-old-space-size=<MiB>`), any runtime a cgroup (`systemd-run --user --scope -p MemoryMax=<size> -p MemorySwapMax=0 <command>`); where `ulimit -d` cannot be set the runner refuses to run (exit 125); where RLIMIT_DATA is not enforced for mmap (Linux before 4.7, macOS) add the memory guard | `2048` |
| `quality.mutation.test_filter` | shell text substituted for `{test_filter}` in the mutation command and `env` (unquoted): a test filter that leaves out tests a mutation run cannot or should not run per mutant, such as end-to-end tests that need the real repository (the mutation tool tests a copy of the tree, often without `.git`) or take minutes each. Example (cargo-mutants with nextest): `"command": "cargo mutants ... --test-tool nextest -- {test_filter}"`, `"test_filter": "-E 'not binary(/^e2e_/)'"`. It belongs on the command line, not in a test-runner profile file: such a file is also read by fixture projects that have no such tests, and nextest rejects a `binary()` filter that matches no binary. Survivors of a filtered run are re-run with the filtered tests before they count as gaps (PLAYBOOK §14) | `""` |
| `quality.mutation.test_filter_when` | globs: `{test_filter}` is substituted only while a tracked file matches one (for example `["**/tests/e2e_*.rs"]`), else it is empty; `[]` = always | `[]` |
| `quality.mutation.outcomes` | Rust profile: cargo-mutants' `outcomes.json` (e.g. `mutants.out/outcomes.json`, the per-mutant `log_path`s relative to it). Every caught mutant is refined by its log: a nextest `SIGKILL [` status line means the host killed the test (memory guard, cgroup cap, OOM killer), not caught: the gate fails and names the mutant for a re-run; `SIGABRT [` with `memory allocation of N bytes failed` counts as caught by the memory cap and is listed separately. `""` = off | `""` |
| `quality.mutation.env` | environment variables for the mutation command (string values; the resource placeholders are substituted, unquoted), for example the runner hook, test threads and an empty `RUSTC_WRAPPER` (no compiler cache for mutant rebuilds) | `{}` |
| `quality.heavy_lock` | the heavy-job lock file: a relative path is resolved against the **main checkout** (via `git rev-parse --git-common-dir`), so the main checkout and every worktree of the project share one queue wherever they run (no dependence on `/tmp` policies: tmp cleaners, private or sandboxed `/tmp`, reboots; the job log `<lock>.jobs.log` stays with the project); an absolute path (e.g. `/var/tmp/sdd-heavy-job.lock`) shares the queue among several projects on the host. `tools/sdd/scripts/heavy.sh <command>` runs any command under it (agents and people; `sdd config --heavy-env` gives it the settings), every command `sdd` runs (gates, checks, git hooks) gets `SDD_HEAVY_LOCK` set to it, and `sdd check coverage` and `sdd check mutation` run their command under `<tools_dir>/scripts/heavy-lock.sh` with it, so heavy jobs of concurrent pipelines queue instead of overlapping, first come first served (a ticket queue in `<lock>.queue/`; `SDD_HEAVY_LOCK_POLL` seconds between checks, default 2); wrap other heavy gate commands (full test build, nightly runs) with the script yourself. One job at a time by default; with `SDD_HEAVY_BUDGET_MB` set, as many as fit in a memory budget, each capped in a systemd scope (PLAYBOOK §14, environment variables below). Needs `flock` (util-linux; without it the command runs unlocked with a warning); `""` = no lock | `".wt/locks/heavy.lock"` |
| `quality.heavy_budget_mb` / `heavy_job_mb` | defaults for `SDD_HEAVY_BUDGET_MB` (the memory the heavy jobs may use together; `0` = one job at a time) and `SDD_HEAVY_JOB_MB` (a job's declared size; `0` = half the budget), set by `heavy.sh` and by `sdd` for its commands unless the environment already sets them (a job's own `SDD_HEAVY_JOB_MB=...` wins). All callers of one lock use the same budget, so set it here, not per shell | `0` / `0` |
| `quality.checks` | command checks `[{name, command, violation_regex?, baseline?: true, requires?, install?, timeout?, ok_exits?}]`; the regex has groups `file`, `message`, optional `line` (a match without `file` inherits the previous one; a match without `message` only sets the file); `baseline: false` = every match fails | `[]` |
| `quality.secrets.gitleaks` | also run gitleaks (>= 8.19) when installed; it runs with the `.gitleaks.toml` of a trusted commit (HEAD in the hook, the merge-base in `sdd check secrets`) or its default rules, never the tree's own file, and a `.gitleaksignore` that differs from that commit fails. `gitleaks git` skips paths git treats as binary (a NUL byte, `binary` or `-diff` in `.gitattributes`), so their blobs (staged in the hook, every one the branch's commits add in the check) are written under neutral names to one scratch directory under `paths.logs_dir` and scanned by a single `gitleaks dir .` run inside it with the same config (its path allowlists see only the neutral names); one that cannot be read or scanned fails as `secret-unscanned` unless `allow_globs` covers it. gitleaks' default path allowlist (for example `*.png`, `package-lock.json`) still applies to text files by name; binary blobs are scanned under neutral names, so it does not apply to them | `true` |
| `quality.secrets.patterns` | extra `[{id, regex}]` rules for the built-in scanner | `[]` |
| `quality.secrets.allow_globs` | paths whose content is not scanned (published test vectors) | `[]` |
| `quality.hooks.types` | Conventional Commit types | `build chore ci docs feat fix perf refactor revert style test` |
| `quality.hooks.scope_regex` | `""` = scope optional and free; else required and must match | `""` |
| `quality.hooks.required_trailers` | regexes; each must match a line of the message's last paragraph | `[]` |
| `quality.hooks.required_trailers_env` | the commit-msg hook requires the trailers only when this env var is `1` (agent sessions); `""` = always (hook and `sdd check commits`) | `CLAUDECODE` |
| `quality.hooks.trailer_authors` | regex on a commit's author or committer `Name <email>`: `sdd check commits` (CI) requires the trailers on matching commits, whatever the environment of the checking process; `""` = the check does not test trailers (it notes that) | `""` |
| `quality.hooks.pre_commit` | fast steps `[{name, command, glob?, exclude?, timeout?: 300}]`; `{files}` = staged files matching `glob` (shell-quoted; a name starting with `-` gets `./`), minus `exclude` globs and the installed kit (`<paths.tools_dir>/**`); a step whose `{files}` would be empty is skipped | `[]` |
| `quality.hooks.commit_tests` | commit-msg runs `tests.run_component` per component of the staged source and test files (`tests.run` for root files) | `true` |
| `quality.hooks.skip_tests_for_types` | commit types whose test step is skipped (red-by-design test commits) | `["test"]` |
| `quality.hooks.test_timeout` | seconds for the commit-msg test step (exceeding it rejects; keep below the agent shell limit, 600 s) | `300` |
| `quality.hooks.budget_seconds` | time budget for pre-commit and commit-msg together; exceeding it warns | `60` |
| `tests.run`, `tests.run_component` (`{component}`), `tests.red_check` | test commands for the red check | `""` |
| `sources.kind`, `description`, `citation_format` | what the normative source is and how to cite it | `product-requirements` |
| `sources.lookup_command` | `{args}`/`{doc}`/`{clause}` placeholders; wins over the built-in reader | `""` |
| `sources.text_dir`, `sources.heading_regex` | built-in Markdown clause reader; any document kind (standard, RFC, technical report, API contract): the file name is matched ignoring case, dots, dashes and spaces (`24.501`, `RFC 9048`, `tr38.901`), never as a prefix of a longer number (`rfc9048` is not `rfc90481`) | `""`, `^#+\s+{clause}\b` |
| `protected_paths` | `[{glob, reason}]`, always blocked for agents; `sdd check protected` also fails a branch that changes them (and the installed kit, `.gitleaks.toml`, `.gitleaksignore`) until the owner approves with `SDD_ALLOW_PROTECTED=1`. That check reads `protected_paths` and `paths.tools_dir` from the merge-base's `sdd.config.json`, never the branch's, and always protects `sdd.config.json` and the kit directories of both configurations. The kit's hooks, checkers and gate scripts (`<tools_dir>/githooks/**`, `<tools_dir>/sddkit/**`, `<tools_dir>/scripts/**`, `<tools_dir>/sdd.py`) are always denied to every subagent | constitution, `.claude/**`, `sdd.config.json` |
| `append_only_paths` | `[{glob, allowed_roles, reason}]`: listed roles may only create new files | `[]` |
| `role_write_scopes` | role -> globs it may write | `spec-author`, `architect-planner`, `product-owner`, `integrator` -> `specs/features/**` |
| `role_denied_paths` | role -> globs it may not write | implementer -> test paths and the kit's hooks and checkers (`tools/sdd/githooks/**`, `tools/sdd/sddkit/**`, `tools/sdd/sdd.py`); test-author -> the same kit paths |
| `formatters` | `[{glob, command with {file}, exclude?, timeout?}]` for the PostToolUse hook | `[]` |
| `stop_check` | `{command, changed_globs, roles, timeout}` for Stop/SubagentStop | disabled |
| `build_cache.env` | environment variables for a shared build cache across worktrees (e.g. `RUSTC_WRAPPER`, `SCCACHE_DIR`, `SCCACHE_CACHE_SIZE`; `CCACHE_DIR`, `CCACHE_MAXSIZE`; `npm_config_store_dir`); string values, set for every gate, check and git hook command and quoted to agents in the DISK RULE; `sdd doctor` warns for a path under `/tmp` or on a tmpfs mount. Recipes: docs/ADAPTING.md §9 | `{}` |
| `models.mechanical|gate|mid|strong|top` | per tier: an explicit model id string, or `{"id": "<id>", "effort": "low\|medium\|high\|xhigh\|max\|"}`; `""` = inherit the session model; `gate` (the gate runner) empty falls back to `mid`. Validation rejects aliases (a bare word without a dash or digit), unknown efforts and unknown keys. A tier's effort wins over the call site's; a plain id keeps the call site's effort | `""` |
| `_models_example` | top-level `_` keys are ignored; the template's copy holds the default efficient mapping (README "Model routing") | - |
| `pipeline.max_parallel_features` | orchestrator cap | `2` |
| `pipeline.max_critic_fix_rounds`, `max_gate_fix_rounds`, `skeptics` | loop bounds; `skeptics` at least 2 (a blocking finding is dismissed only by two refuting votes and no confirming one) | `2`, `3`, `2` |
| `pipeline.max_build_jobs` | per-worktree build parallelism (quoted to agents) | `4` |
| `pipeline.gate_poll_seconds` | `sdd gates --wait --poll` interval used by the pipeline | `120` |
| `pipeline.security_sensitive_hint` | what makes a change security-sensitive | auth, crypto, secrets, tokens, untrusted parsing |
| `pipeline.role_tiers` | override role -> tier (tiers mode) | `{}` |
| `pipeline.red_check_tags` | the red check also searches the whole repository for Verifies tags (`trace.verifies_keywords` + `requirements.id_regex`, every id of a multi-id list); a `must` is untested only if neither the trace tool nor that search finds a test (same as the pipeline arg `redCheckTags`; on by default since the safety defaults) | `true` |
| `pipeline.persist_findings` | review.md §3 also gets a machine-readable findings block with each finding's outcome, verdicts are written before any fix, and a run resumed from `verify` reloads the findings (same as the pipeline arg `persistFindings`). The block also names the reviewers that returned a result and the security classification; the script parses it (a malformed block stops the run with `blocked-evidence`) and Finalize uses it as evidence | `true` |
| `pipeline.cap_fallback` | when a capped model's call has no equal-or-smarter point left outside the exhausted pools: `equal` pauses the run until the cap resets (never downgrade); `strongest` runs it on the most intelligent point left, e.g. another model at `max` effort (same as the pipeline arg `capFallback`). Put that point in the models table | `"strongest"` |
| `pipeline.security_paths` | path globs of security-relevant code (authentication, crypto, parsers of untrusted input, ...): a feature diff that touches one makes the feature security-sensitive, decided by the script (also: the arg `security`, the plan's `Security-sensitive:` line, a reviewer). Security-sensitive means two independent security reviews and `securitySignoff: "pending-owner"` in the run summary: the owner signs off, never the orchestrator | `[]` |
| `pipeline.hook_probe.enabled` / `path` / `blocked_marker` | Setup's hook probe: the setup agent writes `path` (repository-relative, under `protected_paths`) with its Write tool and the run stops with `blocked-hooks` unless the result contains `blocked_marker` (the protect-paths hook's message). A session started outside the repository root loads no project hooks; this makes that visible before any agent works. The arg `hookProbe: false` also turns it off | `true` / `".claude/.hook-probe"` / `"BLOCKED by SDD hook"` |
| `pipeline.routing` | `tiers` (role -> tier) or `formula` (levels and the models table); the pipeline arg `routing` overrides | `"tiers"` |
| `pipeline.models_table` | the project's models table; when absent `<tools_dir>/pipeline/models.json` (the template) is used. The project file replaces the template, it is not merged. Formula mode rejects a table whose ids still start with `example-` | `"sdd.models.json"` |
| `merge.union_manifests` | globs `sdd union-merge` may union | `[]` |
| `merge.lockfiles`, `merge.after_union` | lockfiles taken "theirs" and regenerated by the command | `[]`, `""` |
| `commit.trailers` | trailer lines appended to every commit | `[]` (validation requires at least one) |
| `commit.scope_format` | Conventional Commit scope of feature commits (`{feature}` = the feature id, e.g. `feat(F011): ...`); informational, the pipeline prompts use the feature id | `{feature}` |
| `override_env` | env var that disables the Claude Code protection hooks for a human session. The CI checks (`sdd check protected`, the `.gitleaksignore` rule of `sdd check secrets`) ignore it: their owner approval is always `SDD_ALLOW_PROTECTED=1`, which no configuration can rename | `SDD_ALLOW_PROTECTED` |

Environment variables: `SDD_ROOT` (project root override), `SDD_ALLOW_PROTECTED=1`,
`SDD_ALLOW_DESTRUCTIVE_GIT=1`, `SDD_STOP_CHECK=0`, `SDD_FEATURE` (set by `sdd gates
--feature`, available to gate commands; else the feature is parsed from the branch name),
`SDD_BASE_REF` (base ref for changed lines, wins over `quality.base_ref`; required with `CI=true`, where every check that diffs against a base exits 2 without it), `CI=true` (CI
mode: a missing tool fails instead of skipping), `SDD_MUTATION` (`run` or unset: `sdd check
mutation` runs; `skip`: it reports SKIP, exit 3, without running anything, for a host that is
busy with another heavy job; with `CI=true` `skip` fails, so CI never skips mutation testing;
any other value exits 2), `SDD_HEAVY_LOCK_POLL` (seconds between a queued heavy job's checks,
default 2), `SDD_HEAVY_BUDGET_MB` (memory budget in MB shared by the heavy jobs of one lock
file; unset or `0`: one job at a time; set it the same for every caller of the lock),
`SDD_HEAVY_JOB_MB` (MB this job declares and is capped at, default half the budget; a job
bigger than the budget runs alone), `SDD_HEAVY_LEGACY_MB` (what a job holding the lock one at
a time counts for, default half the budget), `SDD_HEAVY_CAP=0` (no systemd scope cap; the
budget still applies), `SDD_HEAVY_HIGH_PCT` (also set `MemoryHigh` at this percentage of the
cap; default 0 = off), `SDD_VERBOSE=1` (also list baselined
violations), `SOURCE_DATE_EPOCH` (deterministic `sdd progress`).

Exit codes of `sdd check`: `0` pass (warnings allowed), `1` violations, `2` configuration,
input or tool error (bad regex, unparsable report or baseline, stale or missing report, no
merge-base, a tool exiting outside its `ok_exits`, a timeout), `3` skipped because a required
tool is missing (`1` with `CI=true`). A wrapped tool's own exit code is never passed through.

## Model routing

### Models table (`sdd.models.json`)

```json
{"version": 1,
 "params": {"p0": 0.3, "s": 4, "retry_mult": 2},
 "floors": {"1": 0, "2": 40, "3": 47, "4": 53, "5": 56, "6": 61},
 "pools": {"standard": {"capped": false}, "capped": {"capped": true, "scarcity": 3}},
 "roles": {"implementer": {"base": 3, "min": 2}, "...": {}},
 "points": [{"model": "<id>", "effort": "medium", "intelligence": 57, "cost_per_task": 1.0,
             "tokens_per_task": 90000, "pool": "standard"}]}
```

| Key | Meaning | Default when absent |
|---|---|---|
| `params.p0`, `s`, `retry_mult` | retry-risk model: `p0 * exp(-(I - floor) / s)` is the chance a task fails at intelligence `I`, a failure costs `retry_mult` more | `0.3`, `4`, `2` |
| `floors` | level 1-6 -> minimum intelligence (non-decreasing) | `0, 40, 47, 53, 56, 61` |
| `pools` | `{capped: bool, scarcity}`: models that share a usage cap share a pool; `scarcity` (>= 1) multiplies the cost of a capped pool on L1-L3 | required (at least the pools the points use) |
| `roles` | routing role -> `{base, min}` levels (roles: `mechanical`, `gate` and the agent roles) | mechanical 1/1, gate 2/2, integrator 3/2, implementer 3/2, perf- and test-adequacy-reviewer 3/3, the judgment roles 4/4 |
| `points` | the frontier: one entry per model and effort; `intelligence` from a public intelligence index, `cost_per_task` and `tokens_per_task` measured in your runs | required |

Level of a call: `L = clamp(max(role.min, role.base + complexity + risk + hard + retries), 1, 6)`
with complexity low -1 / medium 0 / high +1 (implementation calls: from the task; a worktree
lane: its hardest task), risk +1 for `security` or `concurrency` (other calls: `security` in
Review, Verify and fix calls of a security-sensitive feature), hard +1 for `args.hard`, and
retries = prior failed attempts at the same work (implementer retry, gate-fix round n counts
n-1, the 2nd product-owner pass, test arbitration and semantic merge resolution count one).
Expected cost `E = cost_per_task * (1 + p0 * exp(-(I - floor) / s) * retry_mult) * pool_mult`;
the candidates of a level are its eligible points (I >= floor) in E order (ties: higher
intelligence, then id). `sdd config --pipeline` passes them as `routing.candidates`;
`sdd routing table` prints them and marks dominated points.

Pipeline arguments: `routing` (mode override), `routingBefore` (the `summary.routing` of the
paused run, used with `modelsFromPhase` for the completed phases), `runKey` (identity of a
logical run across resumes; the `/feature` skill sets `<FID>-<UTC timestamp>`; derived from the
feature, first stage and setup HEAD otherwise).

### Telemetry

`summary.telemetry` holds one record per agent attempt: `call` (`<label>#<n>`), `attempt`,
`label`, `role`, `level`, `model`, `effort`, `outcome` (`ok`; `fail` only when the agent
returned `ok: false`; `null` for an infrastructure failure: null, undefined or thrown),
`fallback_of` and `mode`. `sdd routing log --output-file <file> [--transcript-dir <dir>]` (or
`--file <summary JSON | ->`) appends them to `<logs_dir>/routing.jsonl` with `run`, `feature`,
`status`, `started_at`, `duration_ms`, `replayed` and `logged_at`; times come from the Workflow
output file (`workflowProgress`) or the run's transcripts and are null when they cannot be
matched. Lines already logged (same run, call, attempt, model, effort, outcome) are skipped, so
logging a paused run and then its resume records each attempt once. `sdd routing refit
[--json] [--file F] [--min-samples 10] [--max-fail 0.25]` prints per level and model the
samples (`ok` + `fail`), failure and retry rates and the infrastructure failures, and suggests
raising a floor to the next frontier intelligence when the failure rate exceeds `--max-fail`
with enough samples, or lowering it when there were no failures over 3x the samples. It never
writes a file. Its input is pluggable (`--input`, repeatable, rows pooled): `telemetry` (the
default, the JSONL above) or `journal`: Workflow journals given with `--journal <file | run dir
| glob>` (default: the journals under `~/.claude/projects`), for runs whose telemetry was never
logged. A journal call counts only when its label carries the level as a ` [L<n>]` suffix (an
orchestration script that routes by level adds it); the model comes from the
`agent-<id>.meta.json` next to the journal (`inherit` when it names none), journals carry no
effort, a null result or a failed or unfinished call is an infrastructure failure, and a label
ending in `retry`, `(retry)` or `(... fallback)` is a second attempt. Telemetry outcomes
`failed` are read as `fail`. Another log format is one more reader in `REFIT_INPUTS`
(`sddkit/routing.py`).
