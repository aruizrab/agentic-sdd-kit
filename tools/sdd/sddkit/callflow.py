"""Call-flow conformance: check a recorded event trace against a spec's step table.

A test runs a scenario, records the events the system emitted (each with an integer
``seq``, the trace's total order) and calls ``check(spec_text, procedure, expectations,
skips, events)``. The step order comes from the spec's step table (``procedures``), so it
has a single source.

* An **expectation** names a step label and a predicate over one event: a callable, or a
  dict of field values that must all be equal (``{"kind": "msg", "src": "client"}``). It is
  ordered (the default) or unordered.
* A **skip** names a step label and a non-empty reason.

Rules:
1. The spec text has exactly one well-formed step table for ``procedure``.
2. Every step label of the table has exactly one expectation or skip; none names a label
   outside the table.
3. Ordered expectations are taken in step-table row order; each matches the first event,
   in ascending ``seq``, that satisfies its predicate and comes after the event of the
   last ordered expectation that matched. One that finds none is reported **out of order**
   (with the last matched step) when an earlier event satisfies it, else **missing**.
4. An unordered expectation fails only when no event satisfies it (**missing**).
5. Events are considered in ``seq`` order only; no other field, clock or random source is
   read, so the same inputs always give the same result and text.

``check`` returns the violations in step-table order (empty = conforms);
``assert_call_flow`` raises ``AssertionError`` with them. ``sdd callflow`` runs the same
check on JSON files, for test suites in any language.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Sequence, Union

from .procedures import canonical, parse, proc_key

Predicate = Union[Callable[[Any], bool], Mapping[str, Any]]


@dataclass
class Expect:
    step: str
    match: Predicate
    ordered: bool = True


@dataclass
class Skip:
    step: str
    reason: str


def _field(event: Any, name: str) -> Any:
    if isinstance(event, Mapping):
        return event.get(name)
    return getattr(event, name, None)


def _satisfies(pred: Predicate, event: Any) -> bool:
    if callable(pred):
        return bool(pred(event))
    return all(_field(event, k) == v for k, v in pred.items())


def check(spec_text: str, procedure: str, expectations: Sequence[Expect], skips: Sequence[Skip], events: Sequence[Any]) -> List[str]:
    key = proc_key(procedure)[0]
    tables = [t for t in parse(spec_text).tables if t.key == key]
    if not tables:
        return [f"{key}: the spec has no step table for this procedure"]
    defects = [d for t in tables for d in t.defects]
    if defects:
        return [f"{key}: step table defect: {d}" for d in defects]
    order = [r.label for r in tables[0].rows]
    by_step: Dict[str, Union[Expect, Skip]] = {}
    problems: List[str] = []
    for item in [*expectations, *skips]:
        label = canonical(item.step)
        if label not in order:
            problems.append(f"{key}: step {label} is not in the step table")
        elif label in by_step:
            problems.append(f"{key}: step {label} has more than one expectation or skip")
        else:
            by_step[label] = item
        if isinstance(item, Skip) and not item.reason.strip():
            problems.append(f"{key}: the skip of step {label} has no reason")
    problems.extend(f"{key}: step {label} has neither an expectation nor a skip" for label in order if label not in by_step)
    if problems:
        return problems
    ordered = sorted(events, key=lambda e: _field(e, "seq"))
    last_seq = None
    last_step = None
    for label in order:
        item = by_step[label]
        if isinstance(item, Skip):
            continue
        if not item.ordered:
            if not any(_satisfies(item.match, e) for e in ordered):
                problems.append(f"{key}: step {label} missing")
            continue
        hit = next((e for e in ordered if (last_seq is None or _field(e, "seq") > last_seq) and _satisfies(item.match, e)), None)
        if hit is not None:
            last_seq, last_step = _field(hit, "seq"), label
        elif any(_satisfies(item.match, e) for e in ordered):
            problems.append(f"{key}: step {label} out of order: it occurs before step {last_step}")
        else:
            problems.append(f"{key}: step {label} missing")
    return problems


def assert_call_flow(spec_text: str, procedure: str, expectations: Sequence[Expect], skips: Sequence[Skip], events: Sequence[Any]) -> None:
    problems = check(spec_text, procedure, expectations, skips, events)
    if problems:
        raise AssertionError("call flow does not conform:\n" + "\n".join(f"  {p}" for p in problems))


def load_events(path: Path) -> List[Any]:
    """A JSON array of events, or JSON Lines (one event object per line)."""
    text = path.read_text(encoding="utf-8")
    stripped = text.lstrip()
    if stripped.startswith("["):
        return json.loads(text)
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def run(spec: Path, procedure: str, expect_file: Path, events_file: Path, as_json: bool = False) -> int:
    """``sdd callflow``: exit 0 conforms, 1 violations, 2 input error."""
    try:
        data = json.loads(expect_file.read_text(encoding="utf-8"))
        expectations = [Expect(str(e["step"]), dict(e["match"]), bool(e.get("ordered", True))) for e in data.get("expect", [])]
        skips = [Skip(str(s["step"]), str(s.get("reason", ""))) for s in data.get("skip", [])]
        events = load_events(events_file)
        if not all(isinstance(e, dict) and isinstance(e.get("seq"), int) for e in events):
            raise ValueError("every event must be an object with an integer seq")
        spec_text = spec.read_text(encoding="utf-8")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        print(f"callflow: invalid input: {exc}")
        return 2
    problems = check(spec_text, procedure, expectations, skips, events)
    if as_json:
        print(json.dumps({"ok": not problems, "violations": problems}, indent=2))
    else:
        for p in problems:
            print(f"violation: {p}")
        print(f"callflow: {'OK' if not problems else f'{len(problems)} violation(s)'}")
    return 1 if problems else 0
