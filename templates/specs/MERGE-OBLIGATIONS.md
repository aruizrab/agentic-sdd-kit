# Merge obligations register

The single tracked register of cross-feature obligations that approved specs create:
supersession marks in other specs, edits of other specs and documents, tests of other features
to update, merge order, open product-owner items and pending owner security sign-offs. It
replaces the orchestrator's memory for these (docs/ORCHESTRATOR.md §5). It is not parsed by
`sdd trace`, which reads only the feature specs.

## How to use

1. **Before every merge** of a feature branch into `main`, the orchestrator (or the pipeline's
   integrator) reads that feature's section, every row whose *Affects* column names it, and
   the merge order rows. A merge waits while any of its rows of type ORDER, SPEC, SEC or PO is
   open, unless the product owner says otherwise in writing.
2. **At the merge**, rows of type MARK, EDIT, DOCS and TEST are applied in the merge commit or
   in a `docs(F<NNN>): merge-time edits` commit right after it, and each row is closed with
   that commit's short sha in *Status* (`closed 1a2b3c4`). Rows that the feature's own plan
   applies on its branch are checked at the merge and closed with the merge commit's sha.
3. **Requirement text is never written at merge time.** A MARK row appends
   `(superseded by <ID>)` to an approved requirement line and names an id that already exists
   in an approved spec. When a successor's wording is not yet in an approved spec (a qualified
   successor, a successor that combines two features, a partial change), it goes through a
   spec change on the owning branch with the spec critic first (row type SPEC); the merge then
   only applies the mark. Supersession is whole: a partly changed requirement gets a qualified
   successor id, never a partial mark.
4. A newly approved spec adds its escalations, merge-time edits and "merged before"
   constraints here in its approval commit. Rows are never deleted; closed rows stay as the
   record.

Row types: **MARK** supersession mark on another spec's line · **EDIT** prose or table edit
in another spec · **DOCS** documentation or ADR edit · **TEST** tests of another feature ·
**ORDER** merge order · **SPEC** spec amendment through the critic before merge or
implementation · **SEC** owner security sign-off · **PO** open product-owner item.

Source notation: `F012:893` = line 893 of that feature's spec.md on its branch; `F012 tasks:162`
and `F012 design:542` likewise for tasks.md and design.md; `main F009:164` = that spec on
`main`. Mark a row `[unverified]` when it could not be checked against the branch text.

---

## Obligations by merging feature

### F<NNN> <slug>

| ID | Type | Affects | Action | Source | Status |
|---|---|---|---|---|---|
| MO-F<NNN>-01 | ORDER | F<MMM> | <what must happen, in one or two sentences> | <source notation> | open |
