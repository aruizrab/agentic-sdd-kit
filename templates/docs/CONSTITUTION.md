# {{PROJECT}} Constitution

Status: **Draft** (ratify by setting a date). Protected file: agents cannot edit it (hook
`protect-paths`). Amendments need a human-approved ADR in `docs/adr/` and a session started
with `SDD_ALLOW_PROTECTED=1`.

These articles are non-negotiable. They bind every contributor, human or agent, and every
stage of the lifecycle in `docs/DEVELOPMENT.md`. Where a plan, task, or review conflicts with an
article, the article wins. A deviation is legal only when it is recorded in the feature's
`design.md` under **Complexity / Constitution exceptions**, with the article number, a
justification, and the product owner's sign-off in `review.md`.

"Enforced by" names the mechanism that catches a violation. Articles 1-6 and 10-12 are
project-independent and come with the kit; articles 7-9 carry project-specific placeholders.
Add articles rather than weakening these.

---

## Article 1. The normative source is the authority

All specified behaviour derives from the project's normative source (<a standard, an API
contract, product requirements>; versions pinned in <path>). Every requirement cites its
source section in the project's citation format, and every citation is checked against the
corpus. Source text is never quoted or paraphrased from memory: read it with `sdd source
clause <doc> <section>`. Behaviour the source does not define, or where the project
deliberately deviates, gets a project-internal requirement that states the decision and why.

*Rationale.* Language models confidently invent section numbers, constants and error codes.
The corpus is the only ground truth, so citations are the cheapest place to catch
hallucination.

*Enforced by:* spec-critic (citation check), `sdd trace --check` (unknown ids, uncovered
`must` requirements), spec-compliance-reviewer, verifier.

## Article 2. Requirements are proportionate and decided

A feature spec contains what its roadmap entry needs: about <15-45> requirements, more only
for normative abnormal cases the source mandates. Project-internal choices are decided by the
author and recorded under *Decisions*; the clarification marker is reserved for genuine
ambiguity in the source. A spec that does not converge after two critic rounds goes to the
product owner, who decides, cuts scope to the roadmap entry and defers the rest to named
future features.

*Rationale.* Over-specified specs (100+ requirements) never converge: every critic round
finds new issues, and the tests and code built on them bloat accordingly.

*Enforced by:* spec-author and spec-critic budgets, `sdd spec-info` (requirement count,
open markers), the pipeline's convergence rounds and product-owner resolution.

## Article 3. No crash, hang or exhaustion on untrusted input

Input from outside the trust boundary (<peers, clients, files, network>) is untrusted. Code
that handles it never crashes, never indexes or slices without a bound check, never does
unchecked arithmetic on lengths, and never allocates in proportion to an attacker-chosen
value without a cap. Malformed input produces a typed error, the reaction the source
specifies, and an observable event. The project threat model (CLAUDE.md) says what is
untrusted; nothing beyond it needs hardening.

*Enforced by:* lints (<list>), property and fuzz tests, security-reviewer.

## Article 4. Tests first, traceability always

A different agent (test-author) writes tests and fixtures from the approved requirements
before implementation, and they are confirmed failing ("red") before an implementer starts.
Implementers do not edit those tests or fixtures; a disputed test goes to the test-author
(test-defect report) or, after two failing gate rounds, to a test arbiter who decides which
side is wrong against the spec. Every `must` requirement has at least one implementation site
(Implements tag) and at least one passing test (Verifies tag). Code that implements specified
behaviour and carries no requirement tag is a defect.

*Rationale.* An agent that writes both the code and its tests can make them agree with each
other while both are wrong. Separating authorship and tracing every line back to a source
makes gaps visible; the arbiter step keeps the separation from deadlocking.

*Enforced by:* protect-paths hook (role scopes), the red check, `sdd trace --check`,
test-adequacy-reviewer.

## Article 5. Independent oracles beat self-consistency

Round-trip tests (`decode(encode(x)) == x`) are necessary but never sufficient. Every
parser, codec, protocol handler and cryptographic function is also checked against at least
one oracle the implementer cannot influence: published test vectors with provenance, an
external decoder or validator, a reference implementation, a contract test against the
real peer. Fixtures are immutable once committed.

*Enforced by:* gates (<which>), test-adequacy-reviewer ("which oracle covers this?"),
append-only fixture paths in the hook.

## Article 6. Security by default

- Secret comparisons (MACs, tokens, digests) are constant time.
- Key material and credentials live in types that are wiped on drop and never reveal content
  through debug or serialisation output.
- Integrity is checked before any processing the source does not explicitly allow unprotected.
- Secrets never appear in logs, events, errors or diagnostics. <List the explicit exceptions,
  e.g. a configured key-log for debugging, as labelled artefacts.>
- A security-sensitive change gets a second, independent security review, cheap hardening
  fixes before merge, an independent re-verification of every fix, and a recorded
  product-owner sign-off.

*Enforced by:* security-reviewer (x2 when sensitive), verifier fix-checks (x2 when
sensitive), review.md sign-off field.

## Article 7. <Project property: e.g. determinism / reproducibility>

<State the property, the mechanism (e.g. all time, randomness and I/O go through an injected
context) and what is forbidden in core code. Delete this article if the project has no such
property.>

*Enforced by:* <gate / reviewer>.

## Article 8. Performance budgets are requirements

Hot paths have budgets that are tested, not hoped for: <list the initial budgets>. Budgets
change only by ADR. A benchmark gate needs a recorded baseline (<path>); a regression above
<N %> fails unless the product owner records an explicit exception in review.md with the
measurement and the reason. A threshold is never loosened to pass a gate.

*Enforced by:* <benchmark gate>, perf-reviewer, product-owner.

## Article 9. Observability by default

<State what every operation emits: e.g. every message in/out, state transition, timer and
rejected input emits a typed event with ids and a cause link.> A feature's spec lists what it
emits and tests assert it. A schema change is a versioned, documented change.

*Enforced by:* spec template "Observability" section, test-author, spec-compliance-reviewer.

## Article 10. Simplicity; no speculative abstraction

Build what the approved requirements need now. No interface with one implementation "for
later", no generic framework, no new module or dependency unless `design.md` justifies it
under **Complexity / Constitution exceptions**. Keep each task's diff small (about 400
changed lines or fewer, excluding tests and fixtures). Do not re-implement mature libraries
for a marginal property the threat model does not require.

*Rationale.* Agents over-engineer by default, and every abstraction is something the next
agent must understand.

*Enforced by:* architect-planner (constitution check), spec-critic (creep), reviewers
("untraced code"), dependency gate.

## Article 11. Licensing hygiene

{{PROJECT}} is <licence>. Never copy, translate or closely paraphrase code from projects
with incompatible licences (<list>). They may be used as black-box peers and sources of
ideas only. New dependencies must pass the licence allowlist. Published test vectors may be
embedded with provenance. Normative source text stays in its directory as reference and is
not copied into source files beyond short excerpts in comments.

*Enforced by:* <licence gate>, security-reviewer (provenance of non-trivial algorithms),
review.md provenance field.

## Article 12. Gates are law; shared trees are safe

No agent weakens a gate:
- no lowered thresholds, newly skipped or ignored tests, deleted or loosened assertions, or
  lint suppressions without a reason;
- no edits to `.claude/**`, this constitution, the normative source, `sdd.config.json` or
  committed fixtures;
- no `--no-verify`, no skipped gate stage, no reported gate run without its exit marker.

No agent runs destructive git commands (`checkout -- <path>`, `restore`, `clean`,
`reset --hard`, `stash`) in a tree another agent may be using. Read-only roles experiment in
a detached throwaway worktree. An agent that cannot meet a gate stops and reports. Gaming a
gate is a blocking finding.

*Enforced by:* hooks (`protect-paths`, `bash-guard`), permission deny rules in
`.claude/settings.json`, test-adequacy-reviewer (diff of tests since the red-check commit),
verifier, CI rechecks.
