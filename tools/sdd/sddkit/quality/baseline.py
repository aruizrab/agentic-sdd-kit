"""Shrink-only baseline of the violations that existed when the gates were adopted.

File (``quality.baseline_file``, default ``sdd.baseline.json``)::

    {
      "version": 1,
      "entries": [
        {"kind": "size-file", "file": "src/big.py", "lines": 1450},
        ...
      ]
    }

One entry per line, sorted. Identities never contain line numbers:

    size-file      file                               -> lines
    size-function  file, function, ordinal            -> lines
    arch-dep       file, dep ("from->to")             -> count
    arch-forbid    file, module, pattern              -> count
    <check name>   file, message                      -> count

A violation whose entry records a value >= the current one is ``baselined``; above it, it is
``grown`` and fails; without an entry it is ``new`` and fails (no grandfathering of new code).
An entry matching no current violation is stale and fails the check of its kind until
``sdd baseline --update`` removes it. ``--init``, ``--update`` and the merge resolution are the
only writers; once the file exists nothing adds an identity.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import QualityError, Result, Violation
from . import vcs

VALUE_FIELD = {"size-file": "lines", "size-function": "lines"}
IDENTITY = {
    "size-file": ("file",),
    "size-function": ("file", "function", "ordinal"),
    "arch-dep": ("file", "dep"),
    "arch-forbid": ("file", "module", "pattern"),
}
DEFAULT_IDENTITY = ("file", "message")


def value_field(kind: str) -> str:
    return VALUE_FIELD.get(kind, "count")


def identity(entry: Dict[str, Any]) -> Tuple:
    kind = entry.get("kind", "")
    return (kind,) + tuple(entry.get(f) for f in IDENTITY.get(kind, DEFAULT_IDENTITY))


def violation_identity(v: Violation) -> Tuple:
    return (v.kind,) + tuple(v.key.get(f) for f in IDENTITY.get(v.kind, DEFAULT_IDENTITY))


def path(root: Path, cfg: Dict[str, Any]) -> Path:
    return root / cfg["quality"]["baseline_file"]


def parse(text: str, where: str) -> List[Dict[str, Any]]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise QualityError(f"{where}:{exc.lineno}:{exc.colno}: invalid baseline JSON: {exc.msg}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        raise QualityError(f"{where}: a baseline is an object with an 'entries' list")
    for i, e in enumerate(data["entries"]):
        if not isinstance(e, dict) or not e.get("kind") or "file" not in e:
            raise QualityError(f"{where}: entries[{i}] needs 'kind' and 'file'")
    return data["entries"]


def load(root: Path, cfg: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    """The working-tree baseline entries, or None when there is no file."""
    p = path(root, cfg)
    if not p.is_file():
        return None
    return parse(p.read_text(encoding="utf-8"), cfg["quality"]["baseline_file"])


def render(entries: Iterable[Dict[str, Any]]) -> str:
    rows = sorted(entries, key=lambda e: json.dumps([str(x) for x in identity(e)]))
    body = ",\n".join("    " + json.dumps(e, ensure_ascii=False) for e in rows)
    return '{\n  "version": 1,\n  "entries": [\n' + body + ("\n" if body else "") + "  ]\n}\n"


def save(p: Path, entries: Iterable[Dict[str, Any]]) -> None:
    p.write_text(render(entries), encoding="utf-8")


def entry_for(v: Violation, value: int) -> Dict[str, Any]:
    e: Dict[str, Any] = {"kind": v.kind}
    for f in IDENTITY.get(v.kind, DEFAULT_IDENTITY):
        e[f] = v.key.get(f)
    e[value_field(v.kind)] = value
    return e


def aggregate(violations: Iterable[Violation]) -> Dict[Tuple, Tuple[int, List[Violation]]]:
    """identity -> (current value, violations). Size kinds take the measured lines; others count."""
    out: Dict[Tuple, Tuple[int, List[Violation]]] = {}
    for v in violations:
        ident = violation_identity(v)
        val, vs = out.get(ident, (0, []))
        val = max(val, v.value) if v.kind in VALUE_FIELD else val + 1
        out[ident] = (val, vs + [v])
    return out


def classify(res: Result, entries: Optional[List[Dict[str, Any]]], kinds: Iterable[str], stale: bool = True) -> Result:
    """Set the state of every violation of ``res`` and record stale entries of ``kinds``."""
    kinds = set(kinds)
    by_id = {identity(e): e for e in entries or []}
    agg = aggregate(res.violations)
    for ident, (val, vs) in agg.items():
        e = by_id.get(ident)
        state = "new" if e is None else ("baselined" if val <= int(e.get(value_field(ident[0]), 0)) else "grown")
        for v in vs:
            v.state = state
    if stale:
        res.stale.extend(e for e in entries or [] if e.get("kind") in kinds and identity(e) not in agg)
    return res


# ----------------------------------------------------------------------------
# Collecting every baseline-able violation
# ----------------------------------------------------------------------------


def collect(root: Path, cfg: Dict[str, Any]) -> Tuple[List[Violation], List[str], List[str]]:
    """All current violations of size, arch and the baseline-enabled command checks.

    Returns (violations, kinds checked, notes). A check that is skipped (tool missing) is
    reported in the notes and its kind is not considered (its entries are left alone).
    """
    from . import arch, checks, size

    violations: List[Violation] = []
    kinds: List[str] = []
    notes: List[str] = []
    r = size.measure(root, cfg)
    violations += r.violations
    kinds += ["size-file", "size-function"]
    r = arch.analyse(root, cfg)
    violations += r.violations
    kinds += ["arch-dep", "arch-forbid"]
    for chk in cfg["quality"]["checks"]:
        if chk.get("baseline", True) is False:
            continue
        r = checks.execute(root, cfg, chk)
        if r.status == "skip":
            notes.append(f"{chk['name']}: skipped ({'; '.join(r.notes)})")
            continue
        if r.status == "error":
            raise QualityError(f"check {chk['name']}: {'; '.join(r.notes)}")
        violations += r.violations
        kinds.append(chk["name"])
    return violations, kinds, notes


def init(root: Path, cfg: Dict[str, Any]) -> int:
    p = path(root, cfg)
    if p.exists():
        print(f"baseline: {p.name} exists; --init refuses to overwrite it (use --update to shrink it)", file=sys.stderr)
        return 1
    violations, _kinds, notes = collect(root, cfg)
    for n in notes:
        print(f"baseline: {n}")
    entries = [entry_for(vs[0], val) for _ident, (val, vs) in aggregate(violations).items()]
    save(p, entries)
    print(f"baseline: wrote {len(entries)} entr{'y' if len(entries) == 1 else 'ies'} to {cfg['quality']['baseline_file']}")
    return 0


def shrink(entries: List[Dict[str, Any]], violations: List[Violation], kinds: Iterable[str]) -> Tuple[List[Dict[str, Any]], int, int]:
    """Drop stale entries of ``kinds`` and lower recorded values; never add. (entries, removed, lowered)"""
    kinds = set(kinds)
    agg = aggregate(violations)
    out, removed, lowered = [], 0, 0
    for e in entries:
        ident = identity(e)
        if e.get("kind") not in kinds:
            out.append(e)
            continue
        if ident not in agg:
            removed += 1
            continue
        field_ = value_field(e["kind"])
        cur = agg[ident][0]
        if cur < int(e.get(field_, 0)):
            e = dict(e, **{field_: cur})
            lowered += 1
        out.append(e)
    return out, removed, lowered


def update(root: Path, cfg: Dict[str, Any]) -> int:
    entries = load(root, cfg)
    if entries is None:
        print("baseline: no baseline file (create it once with --init)", file=sys.stderr)
        return 2
    violations, kinds, notes = collect(root, cfg)
    for n in notes:
        print(f"baseline: {n}")
    out, removed, lowered = shrink(entries, violations, kinds)
    save(path(root, cfg), out)
    print(f"baseline: removed {removed} stale entr{'y' if removed == 1 else 'ies'}, lowered {lowered}; {len(out)} left")
    return 0


def intersect(a: List[Dict[str, Any]], b: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Identities present on both sides, each with the lower recorded value."""
    bmap = {identity(e): e for e in b}
    out = []
    for e in a:
        other = bmap.get(identity(e))
        if other is None:
            continue
        f = value_field(e["kind"])
        out.append(dict(e, **{f: min(int(e.get(f, 0)), int(other.get(f, 0)))}))
    return out


def resolve(root: Path, cfg: Dict[str, Any]) -> int:
    rel = cfg["quality"]["baseline_file"]
    ours = vcs.show(root, ":2", rel)
    theirs = vcs.show(root, ":3", rel)
    if ours is None or theirs is None:
        print(f"baseline: {rel} is not conflicted (no index stages 2 and 3); nothing to resolve", file=sys.stderr)
        return 2
    merged = intersect(parse(ours, f"{rel} (ours)"), parse(theirs, f"{rel} (theirs)"))
    save(path(root, cfg), merged)
    violations, kinds, notes = collect(root, cfg)
    for n in notes:
        print(f"baseline: {n}")
    out, removed, lowered = shrink(merged, violations, kinds)
    save(path(root, cfg), out)
    print(f"baseline: resolved {rel} as the intersection of both sides ({len(merged)} entries), then removed {removed} "
          f"and lowered {lowered}; {len(out)} left. Review and `git add {rel}` (not staged by this tool).")
    return 0


def merge_driver(base: str, ours: str, theirs: str) -> int:
    """git merge driver ``%O %A %B``: write the intersection of A and B to A."""
    try:
        a = parse(Path(ours).read_text(encoding="utf-8"), "ours")
        b = parse(Path(theirs).read_text(encoding="utf-8"), "theirs")
    except (OSError, QualityError) as exc:
        print(f"baseline merge driver: {exc}", file=sys.stderr)
        return 2
    save(Path(ours), intersect(a, b))
    return 0


def ratchet(root: Path, cfg: Dict[str, Any]) -> Result:
    """``sdd check baseline``: the working-tree baseline may only shrink relative to the merge-base."""
    res = Result("baseline")
    rel = cfg["quality"]["baseline_file"]
    current = load(root, cfg) or []
    mb = vcs.merge_base(root, cfg)
    base_text = vcs.show(root, mb, rel)
    if base_text is None:
        res.notes.append(f"no {rel} at the merge-base (adoption commit): nothing to compare")
        return res.finish()
    base = {identity(e): e for e in parse(base_text, f"{rel} at {mb[:12]}")}
    for e in current:
        ident = identity(e)
        f = value_field(e["kind"])
        old = base.get(ident)
        if old is None:
            res.violations.append(Violation("baseline-added", e.get("file", ""), None, {"file": e.get("file"), "message": json.dumps(e)},
                                            1, f"entry added to the baseline (new code is never grandfathered): {json.dumps(e, sort_keys=True)}"))
        elif int(e.get(f, 0)) > int(old.get(f, 0)):
            res.violations.append(Violation("baseline-raised", e.get("file", ""), None, {"file": e.get("file"), "message": json.dumps(e)},
                                            1, f"{f} raised from {old.get(f)} to {e.get(f)}: {json.dumps(e, sort_keys=True)}"))
    return res.finish()


def report(root: Path, cfg: Dict[str, Any], as_json: bool) -> int:
    entries = load(root, cfg) or []
    violations, kinds, notes = collect(root, cfg)
    res = classify(Result("report", violations=violations), entries, kinds)
    totals: Dict[str, Dict[str, int]] = {}
    for v in violations:
        t = totals.setdefault(v.kind, {"new": 0, "baselined": 0, "grown": 0, "stale": 0})
        t[v.state] += 1
    for e in res.stale:
        totals.setdefault(e["kind"], {"new": 0, "baselined": 0, "grown": 0, "stale": 0})["stale"] += 1
    if as_json:
        print(json.dumps({
            "violations": [{"kind": v.kind, "file": v.file, "line": v.line, "state": v.state, "value": v.value, "message": v.message}
                           for v in sorted(violations, key=lambda v: (v.kind, v.file, v.line or 0))],
            "stale": res.stale, "totals": totals, "notes": notes,
        }, indent=2))
        return 0
    for n in notes:
        print(f"note: {n}")
    by_kind: Dict[str, Dict[str, List[Violation]]] = {}
    for v in violations:
        by_kind.setdefault(v.kind, {}).setdefault(v.file, []).append(v)
    for kind in sorted(by_kind):
        print(f"== {kind}")
        for f in sorted(by_kind[kind]):
            print(f"   {f}")
            for v in sorted(by_kind[kind][f], key=lambda v: v.line or 0):
                print(f"      {v.line or '-':>5} {v.state:<9} {v.message}")
    for e in res.stale:
        print(f"stale: {json.dumps(e, sort_keys=True)}")
    print("totals: " + (", ".join(f"{k}: " + " ".join(f"{s} {n}" for s, n in t.items() if n) for k, t in sorted(totals.items())) or "no violations"))
    return 0
