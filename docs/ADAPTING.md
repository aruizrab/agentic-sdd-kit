# Adapting the kit to a language and a normative source

Everything below is `sdd.config.json`. The full example files are in `examples/configs/`
(`rust.json`, `typescript.json`, `python.json`, `go.json`); `install.sh --config <name>`
installs one of them. Without `--config`, the language-neutral `templates/sdd.config.json`
is installed: its `fmt`/`lint`/`test` gates and `tests.run` are placeholders that fail with
"not configured" until you replace them (`sdd doctor` warns about each one); only the
`trace` gate is real.

## 1. Gates

A gate is `{"name", "command", "timeout", "optional"?, "skip_if_missing"?, "env"?,
"install"?, "skip_exit"?}`. `sdd gates` runs every gate of the chosen plan (fast, full or
nightly; section 6 covers the quality checks), streams output to the log,
prints a summary and exits non-zero if any gate failed. `optional` gates are skipped (not
failed) when the command exits 127 or `skip_if_missing` names a program that is not on
PATH. Keep `trace` (`python3 -I tools/sdd/sdd.py trace --check`, i.e. `sdd trace --check`) in
every plan; the kit's quality checks are gates of the form `sdd check <name>` (section 6).

### Rust
```json
"gates": {
  "fast": [
    {"name": "fmt", "command": "cargo fmt --all -- --check", "timeout": 120},
    {"name": "clippy", "command": "cargo clippy --workspace --all-targets -- -D warnings", "timeout": 1200},
    {"name": "test", "command": "cargo nextest run --workspace --no-tests=pass || cargo test --workspace", "timeout": 1800},
    {"name": "trace", "command": "python3 -I tools/sdd/sdd.py trace --check", "timeout": 60}
  ],
  "full": [
    ...the fast ones...,
    {"name": "doctest", "command": "cargo test --workspace --doc", "timeout": 1200},
    {"name": "deny", "command": "cargo deny check", "timeout": 600, "optional": true, "skip_if_missing": "cargo-deny"},
    {"name": "audit", "command": "cargo audit", "timeout": 600, "optional": true, "skip_if_missing": "cargo-audit"},
    {"name": "fuzz", "command": "FUZZ_BUDGET_SECS=300 scripts/fuzz-changed.sh", "timeout": 900, "optional": true, "skip_if_missing": "cargo-fuzz"},
    {"name": "bench", "command": "scripts/bench-compare.sh benches/baseline.json 5", "timeout": 1800, "optional": true}
  ]
}
```
Formatter: `rustfmt --edition 2024 {file}`. Stop check: `cargo check -q --workspace
--all-targets`. Components: `^crates/([^/]+)/`, root `workspace`. Union manifests:
`Cargo.toml`, `crates/*/Cargo.toml`; lockfile `Cargo.lock`, `after_union: cargo metadata -q`.
Trace tags: `/// Implements: ID` doc lines and `#[trace::implements("ID")]` /
`#[trace::verifies("ID")]` attributes (any attribute path ending in `implements(`/`verifies(`
is recognised).

### TypeScript
Fast: `npx --no-install prettier --check . '!tools/sdd/**'`, `npx --no-install eslint . --ignore-pattern 'tools/sdd/**' --max-warnings 0`, `npx --no-install tsc --noEmit -p .`,
`npm test -- --run`, trace. Full adds coverage, `npm audit --audit-level=high`, contract tests
and a benchmark compare as package scripts (`npm run test:contract --if-present`,
`npm run bench:compare --if-present`: a project without the script passes). Formatter and
pre-commit step: `npx --no-install prettier --write {file}` / `npx --no-install prettier --check {files}`; stop check:
`npx --no-install tsc --noEmit -p .`. Components: `^packages/([^/]+)/` (monorepo) or drop the regex for a single package.
Union manifests: `package.json`, `packages/*/package.json`; lockfile `package-lock.json`;
`after_union: npm install --package-lock-only --ignore-scripts`. Tags: `// Implements: ID`
comments or `implements("ID")` / `verifies("ID")` calls in test helpers. Implementer-denied
paths: `**/*.test.ts`, `**/__tests__/**`.

### Python
Fast: `ruff format --check`, `ruff check` (both with `--extend-exclude tools/sdd`: the installed
kit is not project code), `mypy src` (optional), `pytest -q -x`, trace. Full
adds `--cov --cov-fail-under`, `pip-audit`, hypothesis property tests, `pytest-benchmark
--benchmark-compare`. Components: `^src/([^/]+)/` (lanes); `tests.run_component` stays empty in
the example because a src layout usually has one flat `tests/`, so the commit test step runs
`tests.run` (set `pytest -q --no-header tests/{component}` only when tests mirror the packages). Union manifests: `pyproject.toml`;
lockfiles `poetry.lock`/`uv.lock`, regenerated after a union merge by
`uv lock >/dev/null 2>&1 || poetry lock --no-update >/dev/null 2>&1 || true` (whichever the
project uses). Tags: `# Implements: ID` comments, a docstring whose
first line is the tag (`"""Implements: ID"""` or `"""Verifies: ID` on the opening line; a tag
on a later docstring line is NOT scanned), or `@implements("ID")` / `@verifies("ID")`
decorators (define them as no-op decorators in a tiny `trace` module).
Stop check: `python3 -m compileall -q src` (not `python`: many hosts have no such alias).

### Go
Fast: `gofmt -l` (as `out=$(gofmt -l .) && test -z "$out"`, so a gofmt error is not an empty
list), `go vet`, `golangci-lint` (optional), `go test ./...`, trace. Full adds
`-race -cover`, `govulncheck`, native fuzzing of changed targets, benchstat compare.
Components: `^(?:internal|pkg|cmd)/([^/]+)/`, root `module`. Union manifests: `go.mod`;
lockfile `go.sum`; `after_union: go mod tidy`. Stop check: `go build ./...`. Tags: `// Implements: ID` and
`// Verifies: ID` comments above functions. Implementer-denied: `**/*_test.go`,
`**/testdata/**`; append-only: `**/testdata/**` for test-author.

### Other languages
Any language works if you can express: a formatter check, a linter, a test command, and
comment-line tags. The trace scanner understands `//`, `///`, `//!`, `#`, `*`, `--`, `;` and
`<!--` comment prefixes and `keyword("ID", ...)` call/attribute forms; add keywords with
`trace.implements_keywords` / `trace.verifies_keywords`.

## 2. Normative sources

`sources` describes where specified behaviour comes from and how to cite it:

| Kind | `kind` | `citation_format` | Lookup |
|---|---|---|---|
| Standard (3GPP, IETF RFC, ISO, W3C) as Markdown/text corpus | `standard` | `(Source: TS 24.501 §5.5.1.2.4)` / `(Source: RFC 9110 §15.4)` | `text_dir` + built-in reader (`sdd source clause 24.501 5.5.1.2.4`), or `lookup_command` for a project tool (ferrocore: `cargo xtask spec clause {doc} {clause}`) |
| API contract (OpenAPI, protobuf, GraphQL schema) | `api-contract` | `(Source: openapi/users.yaml #getUser)` | `lookup_command`, e.g. a small script that prints an operation or schema by id |
| Product requirements (numbered Markdown) | `product-requirements` | `(Source: REQUIREMENTS §3.2)` | `text_dir: docs/requirements` + built-in reader |

Requirement ids: `requirements.id_regex`. Examples:
- standard: `(?:TS\d{5}-[0-9A-Z.]+|FC-[A-Z0-9]+)-R\d{2,3}` (ferrocore: clause-keyed plus
  project areas);
- contract: `(?:API-[A-Za-z0-9]+|PRJ-[A-Z0-9]+)-R\d{2,3}` keyed by operationId;
- product: `REQ-[A-Z0-9]+-R\d{2,3}` keyed by area.
Mark the corpus read-only in `protected_paths` (e.g. `{"glob": "specs/3gpp/**"}`).

Sources that describe procedures (call flows, sequence diagrams, numbered steps between
parties: 3GPP Stage 2 procedures, an OAuth flow, a payment or provisioning flow) can also be
traced step by step with `trace.procedure_steps` (docs/PLAYBOOK.md §15). The step labels are
read through the same lookup, so `sdd source clause <doc> <section>` must print the section
with its heading. If the source numbers steps in a way the built-in grammar does not see
(`S1:`, `[1]`), set `trace.procedure_steps.label_regex` with a named group `label`; if the
flows are pictures, set `source_check: false` and let the spec critic compare the tables with
the diagrams.

## 3. Paths and roles

- `protected_paths`: always blocked (constitution, `.claude/**`, `sdd.config.json`, corpus).
- `append_only_paths`: fixtures/vectors; `allowed_roles` may only create new files.
- `role_write_scopes`: spec-author, architect-planner, product-owner -> feature dir;
  integrator -> feature dir + lockfiles.
- `role_denied_paths`: implementer -> test paths.
- `formatters`: `{"glob", "command" with {file}, "exclude"?, "timeout"?}`.
- `stop_check`: a cheap compile/typecheck run when matching files changed, for the roles listed.

## 4. Models

`models.mechanical|gate|mid|strong|top`: explicit ids, or `{"id": ..., "effort": ...}`. Empty
inherits the session model; start from the template's `_models_example` (README "Model
routing"). `pipeline.role_tiers` overrides the role -> tier mapping
(`{"test-adequacy-reviewer": "strong"}`). For the formula router copy
`tools/sdd/pipeline/models.json` to `sdd.models.json`, replace every `example-` point with your
provider's models and efforts (intelligence from a public index, cost and tokens per task from
your own runs, models that share a usage cap in one capped pool) and set
`pipeline.routing: "formula"`; check it with `sdd routing table`.

## 5. Worked example: ferrocore

ferrocore's `sdd.config.json` would be the Rust example plus: `sources.kind: standard`,
`lookup_command: cargo xtask spec clause {doc} {clause}`, `text_dir: specs/3gpp/text`,
`protected_paths` with `specs/3gpp/**`, `append_only_paths` with `crates/*/tests/vectors/**`
for test-author, extra gates `tshark` (independent decoder oracle on captures) and `fuzz`
(`cargo xtask fuzz`, budgeted, changed targets only), `components.path_regex: ^crates/([^/]+)/`,
`threat_model` = "research simulator; untrusted input must never crash, hang or exhaust
memory; secrets never leak into datasets/logs; scenario keys are test data, do not
re-implement libraries to zeroize transient buffers", and explicit model ids per tier.
Its constitution articles 7-9 are determinism (sans-IO state machines), performance budgets
per message/UE, and typed events for every message and transition.

## 6. Quality gates per language

`quality` holds the rules; the gate plans call `python3 -I tools/sdd/sdd.py check <name>`
with `"skip_exit": 3` so a missing tool shows as SKIP with its `install` hint (a failure
with `CI=true`). Full examples, including the regexes, are in `examples/configs/`; the
captured outputs they are tested against are in `tests/fixtures/quality/tools/`. Tool output
formats change between versions: run each check once on purpose-made violations and adjust
the regex before relying on it.

**Coverage** needs a report that lists every source file, or a brand-new module no test
imports is invisible (the gate fails such a changed file as `coverage-unmeasured` unless it
has no executable code).

| Language | `coverage.command` -> `report` (`format`) | List every file |
|---|---|---|
| Rust | `cargo llvm-cov --workspace --lcov --output-path lcov.info` -> `lcov.info` (lcov) | every crate member is compiled, so every file is listed |
| Python | `coverage run --source=src -m pytest -q && coverage lcov -o lcov.info` -> `lcov.info` (lcov) | `--source=src` (or `[tool.coverage.run] source`) |
| TypeScript | `vitest run --coverage.enabled --coverage.reporter=lcov --coverage.all` -> `coverage/lcov.info` (lcov) | `--coverage.all` / `coverage.include`; Jest: `collectCoverageFrom` |
| Go | `go test -coverprofile=cover.out ./... && gocover-cobertura < cover.out > coverage.xml` -> `coverage.xml` (cobertura) | `-coverpkg=./...` for cross-package coverage |

**Mutation** must judge only the diff, or at least only report it: survivors on unchanged
lines are ignored, so a file-level tool works too. `ok_exits` lists the codes that mean "the
run completed" (any other code, e.g. a failing unmutated baseline, is a tool error, never
"zero survivors").

| Language | `mutation.command` | Survivors | `ok_exits` |
|---|---|---|---|
| Rust | `cargo mutants --workspace --in-diff {diff_file} --no-shuffle --jobs {jobs}` | `report: mutants.out/missed.txt`, regex `^(?P<file>[^:\n]+):(?P<line>\d+):\d+: (?P<mutation>.+)$` | `[0, 2, 3]` (2 = missed mutants, 3 = timeouts; 1 usage error, 4 baseline failed) |
| Python | `mutmut run --max-children {jobs} >/dev/null && mutmut results` (`&&`: a failed run is a tool error, never "no survivors") | stdout, regex on `    <module>.x_<fn>__mutmut_<n>: survived` (file = dotted module, mapped to the changed file of that module; survivors in unchanged modules are ignored; no line: every survivor in a changed module needs a fix or a justification) | `[0]` |
| TypeScript | `stryker run --incremental --reporters json --concurrency {jobs}` | `report: reports/mutation/mutation.json`, `format: mutation-testing-report` | `[0]` |
| Go | `gremlins unleash --workers {jobs} --test-cpu {test_threads} ./...` | stdout, regex `LIVED (?P<mutation>\S+) at (?P<file>...):(?P<line>\d+)` | `[0]` |

A survivor that no test can kill is justified in the feature's review.md §6 as
`- mutant: <file>[:<line>]: <mutation> — <reason>`.

**Mutation resource limits.** A mutant can turn a loop into a runaway allocator: one test
process grows by gigabytes in seconds, far faster than any test timeout, and several jobs with
several test threads each exhausted a 16 GB host (docs/LESSONS.md 28). `sdd check mutation`
therefore applies `quality.mutation.jobs` (default 2), `test_threads` (3) and a per-test-process
memory cap, `memory_limit_mib` (2048), through `tools/sdd/scripts/memcap-runner.sh`
(`ulimit -d`, the data limit: an address-space cap, `ulimit -v`, made Go tools that reserve
large unused ranges panic at start-up; docs/LESSONS.md 30), and runs under the host-wide heavy-job lock (`tools/sdd/scripts/heavy-lock.sh`,
`quality.heavy_lock`). Cap the test processes, not the compiler:

| Ecosystem | How the cap reaches the tests |
|---|---|
| Rust | target runner hook: `env` `{"CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_RUNNER": "{memcap_runner}", "NEXTEST_TEST_THREADS": "{test_threads}", "RUST_TEST_THREADS": "{test_threads}", "RUSTC_WRAPPER": ""}` (use your host triple: `rustc -vV`, upper-case, `-` to `_`); cargo and nextest start every test binary through the runner, rustc stays uncapped |
| Python, Go | no `{memcap_runner}`: the whole command runs under the runner, each process (the Go compiler too) with its own cap |
| TypeScript (Node) | the example keeps `memory_limit_mib: 0` and caps the heap per process: `env` `{"NODE_OPTIONS": "--max-old-space-size=2048"}`, which gives a clearer error than a killed worker. The data cap also works for plain Node (V8's reserved ranges do not count; a 1.3 GB allocation fails under a 512 MiB cap), but it is not verified with Stryker's worker pool |
| any (systemd) | a cgroup for the whole run instead: `systemd-run --user --scope -p MemoryMax=8G -p MemorySwapMax=0 <command>` (the kernel then kills the largest process inside the scope only) |

A runaway mutant then fails alone and counts as caught. If the unmutated baseline fails only
because of the cap, raise `memory_limit_mib`; never remove the cap. If it fails because tests
need the real repository (end-to-end tests that run git in the checkout; the tool tests a copy
without `.git`), leave them out with `quality.mutation.test_filter` on the command line (Rust
with nextest: `--test-tool nextest -- {test_filter}` and `"-E 'not binary(/^e2e_/)'"`, with
`test_filter_when: ["**/tests/e2e_*.rs"]`; pytest: `-m 'not e2e'`; Vitest: `--exclude`). For unattended runs also
start the memory guard (`python3 -I tools/sdd/scripts/memguard.py`, docs/PLAYBOOK.md §14).

**Nightly** (`gates.nightly`, scheduled in CI): the whole repository, not only the diff, with
an absolute floor instead of the 100 % changed-lines rule.

| Language | full-repository coverage | full-repository mutation |
|---|---|---|
| Rust | `cargo llvm-cov --workspace --fail-under-lines 90` | `cargo mutants --workspace --no-shuffle --jobs 2` with the runner hook, test threads and `RUSTC_WRAPPER=` set inline |
| Python | `coverage run --source=src -m pytest -q && coverage report --fail-under=90` | `tools/sdd/scripts/memcap-runner.sh mutmut run --max-children 2` |
| TypeScript | `npx --no-install vitest run --coverage.enabled --coverage.all --coverage.thresholds.lines=90` | `NODE_OPTIONS=--max-old-space-size=2048 npx --no-install stryker run --concurrency 2` |
| Go | `go test -cover ./...` | `tools/sdd/scripts/memcap-runner.sh gremlins unleash --workers 2 --test-cpu 3 ./...` |

The examples wrap these and the full plan's `test` gate in `tools/sdd/scripts/heavy-lock.sh`
(`sdd check coverage` and `sdd check mutation` take the lock themselves), so heavy jobs of
concurrent pipelines queue instead of overlapping.

Long fuzzing (`FUZZ_BUDGET_SECS=3600 scripts/fuzz-changed.sh`) and benchmarks belong here too.

**Command checks** (`quality.checks`, run in the full plan; the examples name them `docs`,
`unused-deps` and `flaky`, so the gates are `sdd check docs`, `sdd check unused-deps` and
`sdd check flaky`):

| Language | docs / warnings | unused dependencies | flaky tests (`baseline: false`) |
|---|---|---|---|
| Rust | `cargo doc --no-deps --workspace` (`warning: ...` + `--> file:line`) | `cargo machete --skip-target-dir` (`ok_exits [0, 1]`; `<crate> -- <Cargo.toml>:` header, tab-indented names) | `cargo nextest run --workspace --retries 2` (`FLAKY n/m [..] <binary> <test>`; `ok_exits [0, 100]`) |
| Python | `ruff check --select D --output-format concise src` (`ok_exits [0, 1]`) | `deptry src` (`DEP00n` lines; `ok_exits [0, 1]`) | `python3 -c 'import pytest_rerunfailures' 2>/dev/null \|\| exit 127; pytest -p rerunfailures --reruns 2 -rR` (`RERUN <file>::<test>`; the guard turns a missing plugin into a SKIP) |
| TypeScript | `eslint --format unix src` with eslint-plugin-jsdoc (only `[.../jsdoc/...]` lines) | `depcheck` (`ok_exits [0, 255]`) | `vitest run --retry=2 --reporter=verbose` (`(retry xN)`) |
| Go | `revive -formatter default ./...` (`exported ...` lines) | `go mod tidy -diff` (Go 1.23+; `ok_exits [0, 1]`) | `gotestsum --rerun-fails=2 --format testname` (`PASS <pkg>.<Test> (re-run n)`) |

The regex has the named groups `file` and `message` and optionally `line`. A match without
`file` inherits the file of the previous match and a match without `message` only sets the
file, so "header line, then indented items" outputs need one regex with two alternatives.
Paths are made relative to the project root.

**A missing tool must SKIP, never pass.** `requires` names the programs the command runs, so a
missing one shows SKIP with the `install` hint (exit 3; a failure with `CI=true`). Never list
a launcher such as `npx` there: it is always present, then downloads an unpinned package or
fails with an exit code that `ok_exits` may accept as "no findings". Put project-local tool
directories in `quality.tool_dirs` (the TypeScript example: `["node_modules/.bin"]`) and name
the tools themselves (`vitest`, `eslint`, `stryker`, `depcheck`); gates that still go through
npx use `npx --no-install`. Guard plugin-dependent commands so a missing plugin exits 127
(`python3 -c 'import pytest_rerunfailures' 2>/dev/null || exit 127; pytest ...`), and chain
steps with `&&`, never `;`, so a failed run is not followed by an empty report.

**Layers** (`quality.arch.modules`): a module is `{name, paths, imports, may_depend_on,
forbid}`. Relative imports resolve by path; any other specifier is matched against the
`imports` regexes (`^crate::domain\b`, `^app\.domain\b`, `^@app/domain(/|$)`,
`^example\.com/app/internal/domain\b`); no match = external, ignored. Forbid third-party
APIs by **usage**, not by import line, so qualified uses without an import are caught:
`\btokio::` (also matches `use tokio::...`), `\brequests\.` together with
`^\s*(import|from)\s+requests\b`.

### Heuristics and their limits

Everything language-specific is in `tools/sdd/sddkit/quality/langs.py`. A lexer blanks
comments and string/char/template literal contents first, so braces, keywords and imports in
them never count. Lines of code: non-blank lines that are not comment-only; doc comments
(`///`, `//!`, `/** */`) and Python docstrings are documentation, not code.

| Language | Functions | Dependencies | Test code |
|---|---|---|---|
| Rust | `fn name` with `pub(...)`/`const`/`async`/`unsafe`/`extern "..."`; generics, `->` types and `where` clauses are skipped; `fn f();` has no body | `use` trees (`{a, b::{c}}`), `extern crate`, and qualified paths starting with `crate::`, `super::`, `self::` or a mapped name; `super`/`self` resolve from the file's module path (nearest `lib.rs`/`main.rs`) | `**/benches/**`, `**/examples/**`, `#[cfg(test)]` items |
| Python | `def`/`async def`; the signature may span lines (Black's `) -> T:`); the body ends at the next code line indented no deeper, outside brackets and multi-line strings | `import a.b [as x]`, `from a.b import c`, relative `from .x import y`, and dotted chains of imported names (`app.infra.db.connect()`) | `**/test_*.py`, `**/*_test.py`, `**/conftest.py` |
| TS/JS | `function name`, class and object methods (never `if`/`for`/`while`/`switch`/`catch`/...), `const name = (...) => {` with a block body | `import ... from`, `export ... from`, `require()`, `import()` | `**/__tests__/**`, `**/*.test.*`, `**/*.spec.*`; `*.d.ts` has no code |
| Go | `func name` and `func (recv) name`, type parameters, `interface{...}`/`struct{...}` in signatures | `import "p"`, aliased imports and import blocks | `**/*_test.go` |

Known limits: functions produced by macros; braces inside JavaScript regex literals; a TS
return type written as an unparenthesised function type (`(): () => T => {`); Allman-style
TS/JS braces after a newline without a return type are found, other line breaks between `)`
and `{` are not; Python decorators are not counted. Put such files into
`quality.size.exclude`. Add an extension with `quality.languages` (`{".pyi": "python",
".vue": "typescript", ".txt": "none"}`); a `none` file gets the file-size check only.

## 7. Git hooks

`sdd hooks install` sets `core.hooksPath` to the relative `tools/sdd/githooks`, so every
linked worktree runs its own checkout's hooks. It acts on the repository of the project root
and refuses when run from inside another repository or when `tools/sdd/githooks/` is missing
there. It refuses (and changes nothing) when `core.hooksPath` already points elsewhere in any
scope (husky locally, organisation hooks in the global or system config; the message names the
file) or the hooks directory git uses holds executable hooks (git-lfs, the pre-commit
framework), because git would then ignore them. Chain instead:

- **git-lfs / your own hooks directory**: keep it, and add the kit call to each hook:
  ```sh
  #!/bin/sh
  # .git/hooks/pre-push (or .husky/pre-push)
  git lfs pre-push "$@" || exit 1
  exec python3 -I tools/sdd/sdd.py hooks run pre-push "$@"
  ```
  Do the same for `pre-commit` and `commit-msg` (`hooks run commit-msg "$1"`). `sdd doctor`
  counts the hooks as installed when each of the three calls `hooks run <name>`.
- **pre-commit framework**: add local hooks to `.pre-commit-config.yaml`:
  ```yaml
  - repo: local
    hooks:
      - {id: sdd-pre-commit, name: sdd pre-commit, entry: python3 -I tools/sdd/sdd.py hooks run pre-commit, language: system, pass_filenames: false}
      - {id: sdd-commit-msg, name: sdd commit-msg, entry: python3 -I tools/sdd/sdd.py hooks run commit-msg, language: system, stages: [commit-msg]}
      - {id: sdd-pre-push, name: sdd pre-push, entry: python3 -I tools/sdd/sdd.py hooks run pre-push, language: system, pass_filenames: false, stages: [pre-push]}
  ```

The hooks never stash, reset, check out, or write the index, the working tree or git
config; the only files they write are two notes in the git directory: the timing note
`sdd-hook-time` and `sdd-tests-green`. The commit-msg test step rejects only regressions:
after a red-by-design `test(...)` commit on the branch (the pipeline's Tests stage), a failing
test command is reported and the task commit is accepted until that command has passed once
on the branch (recorded in `sdd-tests-green`); from then on a failure rejects. Without such a
commit every failure rejects. The step runs the configured component tests and must stay below an agent's
shell limit (`quality.hooks.test_timeout`, default 300 s); agents give `git commit` a 600 s
shell timeout. Hook-run commands do not inherit git's hook variables (`GIT_INDEX_FILE`,
`GIT_DIR`, `GIT_WORK_TREE`, ...), so a test suite that creates throwaway repositories never
writes the committing repository's index; a step that must read exactly what `git commit -a`
or `git commit <paths>` is committing finds that index in `SDD_GIT_INDEX_FILE`. Put slow checks in `gates.fast` (pre-push) instead; the hooks warn when
pre-commit and commit-msg together exceed `quality.hooks.budget_seconds`.

### Optional: a merge driver for the baseline

Parallel lanes that shrink `sdd.baseline.json` conflict on it. `sdd baseline --resolve`
resolves that after the fact; to avoid the conflict, register the intersection as a merge
driver (the kit never sets this for you):

```sh
echo 'sdd.baseline.json merge=sdd-baseline' >> .gitattributes
git config merge.sdd-baseline.driver 'python3 -I tools/sdd/sdd.py baseline --merge-driver %O %A %B'
```

Run `sdd baseline --update` after such a merge to drop entries only the merged code fixed.

## 8. CI wiring

The git hooks are a fast local check, not the authority: CI is. A pull-request job runs

```sh
git fetch --unshallow || true            # merge-base and commit range need full history
CI=true python3 -I tools/sdd/sdd.py gates --plan full
```

which includes `sdd check commits` (every commit message on the branch) and `sdd check
secrets` (added lines and commits, gitleaks when installed). With `CI=true` a missing tool
fails instead of skipping, so install every tool the plan names. Always set `SDD_BASE_REF` to
the pull request's target branch (for example `SDD_BASE_REF=origin/main` or
`origin/release-2`): the reference points must not come from the branch under test. With
`CI=true` the kit fails every check that diffs against a base (exit 2) while `SDD_BASE_REF` is
unset (the branch's `features.main_branch` could name the branch itself and empty every diff),
ignores the branch's `quality.base_ref`, refuses a configured base that resolves to HEAD while
the branch has its own commits, and `sdd check baseline` takes
`quality.baseline_file` from the merge-base's configuration (a renamed baseline fails as
`baseline-moved`). A project gate of your own that selects work by changed files (fuzz targets,
benchmarks, affected packages) takes its base from `sdd merge-base`, which applies the same
rules, never from a hard-coded `main`: a pull-request checkout has no local `main`, and such a
gate silently fuzzed nothing in CI in the reference project.

Install CI tools from pinned versions **and pinned checksums** (`echo "<sha256>  <file>" |
sha256sum -c` before extracting, or the release's signed checksum file): a version pin fixes a
name, not the content, and the secret scanner is itself the control a replaced release asset
would disable. The commit-message rules of `sdd check commits` include the secret scan of every
message on the branch (git stores messages that the diff scans never see).
**Do not let the branch grade itself.** The plan above runs the branch's own `tools/sdd` and
`sdd.config.json`, so a branch that weakens a checker or drops a gate would pass. Two
controls: the full plan's `protected` gate (`sdd check protected`) fails a branch that
changes `protected_paths`, the installed kit or `.gitleaks.toml`/`.gitleaksignore`, or adds
a `binary`/`-diff` line to a `.gitattributes` (gitleaks and reviewers' diffs skip such files;
`sdd check secrets` still scans them, with gitleaks too), until an owner approves (set
`SDD_ALLOW_PROTECTED=1` in the job only on that approval, e.g. a label; the name is fixed,
`override_env` does not rename it). The check takes `protected_paths` and `paths.tools_dir`
from the merge-base's `sdd.config.json` and always protects `sdd.config.json` and the kit
directories of both configurations, so a branch cannot unprotect anything; and run that check from the target branch's copy of
the kit, which the branch cannot edit:

```sh
base=$(git merge-base HEAD "$SDD_BASE_REF")
mkdir -p .sdd/base-tools && git archive "$base" tools/sdd | tar -x -C .sdd/base-tools
CI=true python3 -I .sdd/base-tools/tools/sdd/sdd.py --root . check protected
```

Add your CI configuration to `protected_paths` too (for example `.github/**`,
`.gitlab-ci.yml`, `.circleci/**`): a branch that edits the workflow could set
`SDD_ALLOW_PROTECTED=1` or `SDD_BASE_REF` itself, or drop the check, and CI providers run the
branch's own workflow file.

Agents cannot edit the hooks and checkers locally either: `<tools_dir>/githooks/**`,
`<tools_dir>/sddkit/**`, `<tools_dir>/scripts/**` and `<tools_dir>/sdd.py` are denied to every subagent (the pipeline's
role fallback runs all roles as `general-purpose`, so a role-keyed denial would never fire).

Schedule `CI=true python3 -I tools/sdd/sdd.py gates --plan nightly` for the full-repository
coverage and mutation runs, long fuzzing and benchmarks.

## 9. Shared build cache across worktrees

Every feature (and every implementation lane) runs in its own worktree, so each one starts with
a cold build unless the compiler output is cached outside the worktree. `build_cache.env` is
the hook: its variables are set for every gate, `sdd check` and git hook command and are quoted
to the agents ("export these before building"). Rules: the cache is **size-capped** and lives
**on disk, never on tmpfs** (`/tmp` is RAM on many hosts; `sdd doctor` warns); values are
literal strings, so write absolute paths (no `~` or `$HOME`). Only set a wrapper once the tool
is installed: `RUSTC_WRAPPER=sccache` without sccache breaks every build.

| Ecosystem | `build_cache.env` |
|---|---|
| Rust (sccache) | `{"RUSTC_WRAPPER": "sccache", "SCCACHE_DIR": "/var/cache/<project>/sccache", "SCCACHE_CACHE_SIZE": "20G"}` |
| C/C++ (ccache) | `{"CCACHE_DIR": "/var/cache/<project>/ccache", "CCACHE_MAXSIZE": "10G"}` (and `CC="ccache gcc"` or the build system's launcher setting) |
| TypeScript (pnpm) | `{"npm_config_store_dir": "/var/cache/<project>/pnpm-store"}`: one content-addressed store, hard-linked into each worktree's `node_modules` (prune with `pnpm store prune`) |
| Go | usually nothing: `GOCACHE` and `GOMODCACHE` are per user and shared already; set `GOCACHE` only to move it off a small or RAM-backed home |
| Python | usually nothing: no compile step; `PIP_CACHE_DIR` / `UV_CACHE_DIR` move the download cache |

Keep `paths.build_dirs` for the per-worktree output that is deleted after a merge; the shared
cache is not deleted per feature, its size cap bounds it.

Before enabling a cache, measure it (cold build with an empty cache, the same build after
deleting the build dir, and a baseline without the wrapper) **on an idle host only**: the
measurement is a series of cold builds, and next to another heavy job its timings are worthless
and its memory use can run a small host out of memory (docs/PLAYBOOK.md §14). Expect hits
across worktrees mainly for registry dependencies (same absolute paths everywhere); the
project's own packages compile from a different path per worktree and usually hit only on
rebuilds in the same worktree. Run mutation testing without the wrapper (for example
`RUSTC_WRAPPER=` empty): every mutant rebuild is a guaranteed miss that evicts useful entries.
