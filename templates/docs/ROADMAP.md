# {{PROJECT}} roadmap

Status: **Draft**. Owner: the architect. The roadmap owns feature numbers: `F<NNN>` is
unique and never reused, and the slug here is the slug of the feature directory and branch.
`sdd progress` and `sdd new-feature` parse the `### M<n> — <name>` and `#### F<NNN> <slug>`
headings (regexes in `sdd.config.json` `features`).

## 1. Milestones

| Milestone | Outcome | Features |
|---|---|---|
| **M0 Foundations** | <what works at the end of M0> | F001-F00x |
| **M1 <name>** | <outcome> | F0xx-F0yy |

## 2. Waves (concurrency plan)

Features that touch disjoint components can run in parallel, each in its own worktree. Keep
at most `pipeline.max_parallel_features` running.

| Wave | Features | Why they can run together |
|---|---|---|
| 1 | F001 | foundations, everything depends on it |
| 2 | F002, F003 | disjoint components |

## 3. Dependency graph

```mermaid
graph LR
  F001 --> F002
  F001 --> F003
  F002 --> F004
```

## 4. Features

### M0 — Foundations

#### F001 <slug>
- **Goal:** <one sentence>
- **Scope:** <in-scope items with source sections>
- **Out of scope:** <items and the feature that owns them>
- **Acceptance criteria:** <observable results the spec's `must` requirements must cover; the
  spec budget is derived from these, not the other way round>
- **Depends on:** -
- **Security-sensitive:** no
- **Hard:** no (set yes to run spec/critic/plan/security/verifier roles on the top model tier)

#### F002 <slug>
- **Goal:** ...
- **Depends on:** F001

### M1 — <name>

#### F003 <slug>
- ...
