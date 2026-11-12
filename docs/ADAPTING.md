# Adapting the kit to a language and a normative source

Everything below is `sdd.config.json`. The full example files are in `examples/configs/`
(`rust.json`, `typescript.json`, `python.json`, `go.json`); `install.sh --config <name>`
installs one of them. Without `--config`, the language-neutral `templates/sdd.config.json`
is installed: its `fmt`/`lint`/`test` gates and `tests.run` are placeholders that fail with
"not configured" until you replace them (`sdd doctor` warns about each one); only the
`trace` gate is real.

## 1. Gates

A gate is `{"name", "command", "timeout", "optional"?, "skip_if_missing"?, "env"?}`.
`sdd gates` runs every gate of the chosen list (fast or full), streams output to the log,
prints a summary and exits non-zero if any gate failed. `optional` gates are skipped (not
failed) when the command exits 127 or `skip_if_missing` names a program that is not on
PATH. Keep `trace` in both lists.

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
Fast: `prettier --check`, `eslint --max-warnings 0`, `tsc --noEmit`, `npm test -- --run`,
trace. Full adds coverage, `npm audit --audit-level=high`, contract tests, a benchmark
compare. Components: `^packages/([^/]+)/` (monorepo) or drop the regex for a single package.
Union manifests: `package.json`, `packages/*/package.json`; lockfile `package-lock.json`;
`after_union: npm install --package-lock-only --ignore-scripts`. Tags: `// Implements: ID`
comments or `implements("ID")` / `verifies("ID")` calls in test helpers. Implementer-denied
paths: `**/*.test.ts`, `**/__tests__/**`.

### Python
Fast: `ruff format --check`, `ruff check`, `mypy src` (optional), `pytest -q -x`, trace. Full
adds `--cov --cov-fail-under`, `pip-audit`, hypothesis property tests, `pytest-benchmark
--benchmark-compare`. Components: `^src/([^/]+)/`. Union manifests: `pyproject.toml`;
lockfiles `poetry.lock`/`uv.lock`. Tags: `# Implements: ID` comments or `@implements("ID")`
/ `@verifies("ID")` decorators (define them as no-op decorators in a tiny `trace` module).
Stop check: `python -m compileall -q src`.

### Go
Fast: `gofmt -l`, `go vet`, `golangci-lint` (optional), `go test ./...`, trace. Full adds
`-race -cover`, `govulncheck`, native fuzzing of changed targets, benchstat compare.
Components: `^(?:internal|pkg|cmd)/([^/]+)/`, root `module`. Union manifests: `go.mod`;
lockfile `go.sum`; `after_union: go mod tidy`. Tags: `// Implements: ID` and
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

## 3. Paths and roles

- `protected_paths`: always blocked (constitution, `.claude/**`, `sdd.config.json`, corpus).
- `append_only_paths`: fixtures/vectors; `allowed_roles` may only create new files.
- `role_write_scopes`: spec-author, architect-planner, product-owner -> feature dir;
  integrator -> feature dir + lockfiles.
- `role_denied_paths`: implementer -> test paths.
- `formatters`: `{"glob", "command" with {file}, "exclude"?, "timeout"?}`.
- `stop_check`: a cheap compile/typecheck run when matching files changed, for the roles listed.

## 4. Models

`models.mechanical|mid|strong|top`: explicit ids. Empty inherits the session model.
`pipeline.role_tiers` overrides the role -> tier mapping (`{"test-adequacy-reviewer": "strong"}`).

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
