# {{FID}} — <Feature title>

Feature: {{FID}}
Slug: {{SLUG}}
Status: Draft
Milestone: <M1>
Roadmap: docs/ROADMAP.md#{{FID}}-{{SLUG}}
Owner (product owner): orchestrator
Requirement id block: {{ID_BLOCK}} (per clause/area key; ids are global across features)
Last critic round: <n> (see review.md)

<!--
Status lifecycle (single header line, parsed by `sdd trace` and `sdd spec-info`):
  Status: Draft        written by spec-author, refined after spec-critic
  Status: Approved     set ONLY by the product owner once the critic has no blocking/major
                       issues and no open clarification marker remains
  Status: Implemented  set by the pipeline's Finalize stage after gates + review + trace pass
Requirement ids never change once Approved. Changing an approved requirement means adding a
new id and marking the old one "(superseded by <ID>)" (outside backticks: inside a code span
the phrase is a quote, not a marker).
Budget: about 15-45 requirements (requirements.budget). Decide project-internal choices under
"Decisions"; use the clarification marker only for genuine ambiguity in the normative source.
-->

## 1. Summary

<2-4 sentences: which behaviour, which component(s), what the user of the project gets.>

## 2. Normative references

| Document | Version | Corpus file | Sections in scope |
|---|---|---|---|
| <standard / contract / requirements doc> | <version> | `<path>` | §<x.y.z> <title>, §<...> |

Source text is read with `sdd source clause <doc> <section>`. Never cite from memory.

## 3. Scope

### In scope
- <behaviour / message / interface / abnormal case, with source section>

### Out of scope
- <item> — <reason> (<section>), deferred to F<MMM> / not applicable

## 4. Actors and interfaces

| Actor | Role in this feature | Interface |
|---|---|---|
| <client / peer / component> | <...> | <API / protocol / file> |

### Procedure steps

<!--
Only when `trace.procedure_steps.enabled` is true in sdd.config.json; otherwise delete this
subsection. With the option on, every spec carries an explicit entry here, so that "never
checked" and "checked, none applies" look different. The entry is one of:
- one step table per procedure of the normative source in scope (call flow, sequence
  diagram, numbered steps), with the Responsibilities table below it;
- an exemption line per procedure in scope that gets no table:
  `No step table for <doc> §<section>: <reason>`;
- for a feature that runs no procedure at all, one feature-wide line:
  `No step table: <reason>` (for example "parsers only; no procedure of <doc> applies").

Step table rules (`sdd trace --check` enforces them once the spec is Approved; a Draft gets
warnings):
- Heading of level 2-4, exactly `Step table: <doc> §<section> [title]`; `<doc> §<section>`
  is what `sdd source clause <doc> <section>` opens. One table per procedure.
- Header `| Step | Description | Mapping |`; one row per step label of the section's own
  text (sub-sections excluded), in canonical form: `1`, `9a`, `7(A)`, `10.1`, ranges as
  `14a-c` or `2a-2c` (written `2a to 2c` or `2a. - 2c.` in the source). With
  `procedure_steps.source_check` the rows must match the source's labels both ways: a
  missing row and an invented step both fail. A void step gets a row
  `out of scope: void`; a line the label grammar recognises that is not a step gets
  `out of scope: not a step (<what it is>)`.
- A Mapping cell is accounted for when it holds at least one requirement id that some spec
  defines (cite existing ids of any feature instead of restating them), or a disposition
  `owned by F<NNN>: <reason>` (another feature of the roadmap) or `out of scope: <reason>`.
  Mix them with `;`: `PRJ-AUTH-R110; owned by F014: IdP side`.
- A step whose behaviour no other requirement states gets a requirement of this feature's
  block with the source `(Source: <doc> §<section> step <label>)`.
- Responsibilities table (required with any step table): heading `Responsibilities`, header
  `| Owner | Responsibility | Source | Requirements |`; each Responsibility is quoted from
  the cited `<doc> §<section>` (case, white space, escapes and a final full stop are
  ignored); the Requirements cell is accounted for like a Mapping cell.
- Golden-scenario rule: every step table is checked by at least one acceptance scenario that
  runs the procedure and checks its recorded events against the table with the call-flow
  helper (`sddkit.callflow` in Python tests, `sdd callflow` for any language). Example:

  ### AS-07 Login call flow — verifies: PRJ-AUTH-R110, PRJ-AUTH-R111
  - **Given** a client, the gateway and a stub identity provider
  - **When** the client logs in and the events of the run are recorded
  - **Then** `sdd callflow spec.md --procedure "<doc> §<section>"` with one expectation per
    step (ordered; unordered for steps the source runs in parallel) and a skip with a
    reason for each step the scenario does not observe reports no violation
  - Oracle: the step table (order from the source)

Example (outside a code block it would be checked; the ids are illustrative):

```markdown
#### Step table: <doc> §<section> <procedure title>

| Step | Description | Mapping |
|---|---|---|
| 1 | Client to Gateway: Login Request | PRJ-AUTH-R110 |
| 2 | Gateway to IdP: Token Request | PRJ-AUTH-R111; owned by F014: IdP side |
| 3 | IdP to Gateway: Token | owned by F014: IdP side |
| 4 | Audit record | out of scope: <reason> |

#### Responsibilities

| Owner | Responsibility | Source | Requirements |
|---|---|---|---|
| Gateway | <text quoted from the source> | <doc> §<section> | PRJ-AUTH-R110 |

No step table for <doc> §<other section>: <reason>
```
-->

<Step tables with the Responsibilities table, exemption lines, or one "No step table: <reason>" line.>

## 5. Requirements (EARS)

Format, exactly one requirement per line:
`- **<ID>** [must|should|may] <EARS sentence>. <citation in the project's format>`

EARS keywords: `THE <X> SHALL` (ubiquitous), `WHEN ... THE <X> SHALL` (event), `WHILE <state>
... THE <X> SHALL` (state), `WHERE <option> ... THE <X> SHALL` (optional), `IF ... THEN THE
<X> SHALL` (unwanted). Append `[behaviour-only]` to a requirement verified by tests alone
(no implementation site, e.g. "SHALL NOT log").

### 5.1 <Behaviour / section title> (<doc> §<section>)

- **<ID-R<nn0>>** [must] WHEN <trigger> THE <X> SHALL <response>. (Source: <doc> §<section>)
- **<ID-R<nn1>>** [must] WHILE <state> WHEN <trigger> THE <X> SHALL <response>. (Source: <doc> §<section>)

### 5.2 Abnormal cases (<doc> §<section>)

- **<ID-R<nn2>>** [must] IF <abnormal condition> THEN THE <X> SHALL <response>. (Source: <doc> §<section>)

### 5.3 Project requirements (observability, configuration, deviations)

- **<PRJ-<AREA>-R<nn0>>** [must] WHEN <operation> THE <X> SHALL emit <event/log/metric> carrying <fields>. (Source: {{PROJECT}} <doc>)

## 6. Acceptance scenarios

Each scenario is Given/When/Then and lists the requirement ids it verifies. Every `must`
requirement appears in at least one scenario. Name the oracle where one applies.

### AS-01 <name> — verifies: <ID>, <ID>
- **Given** <initial state>
- **When** <stimulus: input, timer expiry, malformed data>
- **Then** <observable result: outputs and values, state, timers, events>
- Oracle: <unit | fixture: <doc> §<table> | external validator | reference implementation | contract test>

## 7. Data details

| Message / field / endpoint | Presence | Format / length | Our handling | Source |
|---|---|---|---|---|

Timers / retries:

| Timer | Value | Started when | Stopped when | On expiry | Source |
|---|---|---|---|---|---|

## 8. Error and abnormal cases

Every abnormal case of the in-scope sections. For each, the requirement that covers it, or
"out of scope: <reason>".

| # | Abnormal case (section, item) | Handling | Requirement |
|---|---|---|---|
| — | Malformed / truncated / unknown input in <message> | typed error, <specified reaction>, event, no crash | <ID> |

## 9. Observability

| Event / log / metric | Emitted when | Key fields | Requirement |
|---|---|---|---|

## 10. Security considerations

<Untrusted inputs and bounds, integrity/authentication preconditions, secrets handled,
constant-time points, what must not be logged. Apply the project threat model; do not
over-engineer beyond it.>

## 11. Open questions

<!-- One bullet per open question, in this form (the whole line, outside this comment):
- [NEEDS CLARIFICATION: <question about the normative source>] — affects <ID>; options:
  <a/b>; spec-author's recommendation: <...>
Leave the section empty when there are none.
Approval is blocked while any open clarification marker remains (`sdd spec-info`).
Resolved questions move to Decisions with the answer and who decided. -->

### Decisions
- **D-1 Id block.** {{FID}} uses the block {{ID_BLOCK}} per clause/area key.
- <question or project-internal choice> → <answer> (decided by <spec-author | product owner>, <date>; rationale)

### Deferred
- <requirement or scope moved out to a future feature> → F<MMM>
