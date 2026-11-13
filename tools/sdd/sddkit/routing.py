"""Model routing: tiers with effort, the models table, candidates per level, telemetry log and refit.

The formula router (``pipeline.routing = "formula"``) is implemented here, once: every task level
L1-L6 has an intelligence floor, and the eligible points of the models table (intelligence >=
floor) are ordered by expected cost

    E = cost_per_task * (1 + p0 * exp(-(I - floor) / s) * retry_mult) * pool_mult

with ``pool_mult`` = the pool's scarcity for a capped pool on L1-L3, else 1. ``sdd config
--pipeline`` hands the ordered candidates per level to the pipeline script, which only computes
levels and walks the lists (retry and usage-cap rules). Telemetry: the script records one
deterministic record per agent attempt; ``sdd routing log`` appends them, with host times, to
``<logs_dir>/routing.jsonl``; ``sdd routing refit`` reports per-level failure rates and floor
suggestions and never changes a file. Its input is pluggable (``REFIT_INPUTS``): the telemetry
JSONL (default) or Workflow journals, where a routed call's label carries its level as a
`` [L<n>]`` suffix and the model comes from the ``agent-<id>.meta.json`` next to the journal.
"""

from __future__ import annotations

import copy
import glob as _glob
import json
import math
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

TIERS = ("mechanical", "gate", "mid", "strong", "top")
EFFORTS = ("", "low", "medium", "high", "xhigh", "max")
MODES = ("tiers", "formula")
LEVELS = range(1, 7)
KIT_TABLE = Path(__file__).resolve().parent.parent / "pipeline" / "models.json"
PLACEHOLDER_PREFIX = "example-"
RECORD_FIELDS = ("call", "attempt", "label", "role", "level", "model", "effort", "outcome", "fallback_of", "mode")
OUTCOMES = ("ok", "fail", None)

DEFAULT_TABLE: Dict[str, Any] = {
    "version": 1,
    "params": {"p0": 0.3, "s": 4, "retry_mult": 2},
    "floors": {"1": 0, "2": 40, "3": 47, "4": 53, "5": 56, "6": 61},
    "pools": {"standard": {"capped": False}},
    "roles": {
        "mechanical": {"base": 1, "min": 1}, "gate": {"base": 2, "min": 2},
        "integrator": {"base": 3, "min": 2}, "implementer": {"base": 3, "min": 2},
        "perf-reviewer": {"base": 3, "min": 3}, "test-adequacy-reviewer": {"base": 3, "min": 3},
        "spec-author": {"base": 4, "min": 4}, "spec-critic": {"base": 4, "min": 4},
        "product-owner": {"base": 4, "min": 4}, "architect-planner": {"base": 4, "min": 4},
        "test-author": {"base": 4, "min": 4}, "spec-compliance-reviewer": {"base": 4, "min": 4},
        "security-reviewer": {"base": 4, "min": 4}, "verifier": {"base": 4, "min": 4},
    },
    "points": [],
}


class RoutingError(Exception):
    """An unreadable models table or telemetry input."""


# ---------------------------------------------------------------------------
# Model ids and tiers
# ---------------------------------------------------------------------------

def model_id_problem(value: Any) -> Optional[str]:
    """Why ``value`` is not an explicit model id, or None.

    An explicit id is lower-case letters, digits, dots and dashes and contains a dash or a digit;
    a bare word (``opus``, ``fast``) is an alias that may silently move to another model.
    """
    if not isinstance(value, str) or not re.match(r"^[a-z0-9][a-z0-9.-]*$", value):
        return f"use an explicit model id (lower-case, digits, dots, dashes), got {value!r}"
    if not re.search(r"[-0-9]", value):
        return f"{value!r} looks like an alias (a bare word); use the explicit model id"
    return None


def tier_problems(models: Dict[str, Any]) -> List[str]:
    """Validate ``models``: each tier is "" (inherit), an id string, or {"id", "effort"}."""
    problems: List[str] = []
    for tier in TIERS:
        value = models.get(tier, "")
        where = f"models.{tier}"
        if isinstance(value, dict):
            for key in sorted(set(value) - {"id", "effort"}):
                problems.append(f"{where}: unknown key {key!r} (use id and effort)")
            if value.get("effort", "") not in EFFORTS:
                problems.append(f"{where}: unknown effort {value.get('effort')!r} (one of {', '.join(repr(x) for x in EFFORTS)})")
            if "id" not in value:
                problems.append(f"{where}: an object tier needs 'id'")
                continue
            value = value["id"]
        if value == "":
            continue
        why = model_id_problem(value)
        if why:
            problems.append(f"{where}: {why}")
    return problems


def tier_point(value: Any) -> Optional[Dict[str, str]]:
    """``{"model", "effort"}`` of a tier value, or None when the tier is empty (inherit)."""
    if isinstance(value, dict):
        return {"model": value.get("id", ""), "effort": value.get("effort", "")} if value.get("id") else None
    return {"model": value, "effort": ""} if value else None


# ---------------------------------------------------------------------------
# Models table
# ---------------------------------------------------------------------------

def table_path(root: Path, cfg: Dict[str, Any]) -> Path:
    """The project table if present, else the installed template, else the kit's template."""
    project = root / (cfg["pipeline"].get("models_table") or "sdd.models.json")
    if project.is_file():
        return project
    installed = root / cfg["paths"].get("tools_dir", "tools/sdd") / "pipeline" / "models.json"
    return installed if installed.is_file() else KIT_TABLE


def normalize(table: Dict[str, Any]) -> Dict[str, Any]:
    """Fill the documented defaults for params, floors and roles (never points or pools)."""
    out = copy.deepcopy(table)
    for key in ("params", "floors", "roles"):
        merged = copy.deepcopy(DEFAULT_TABLE[key])
        merged.update(out.get(key) or {})
        out[key] = merged
    out.setdefault("pools", copy.deepcopy(DEFAULT_TABLE["pools"]))
    out.setdefault("points", [])
    return {k: v for k, v in out.items() if not k.startswith("_")}


def load_table(root: Path, cfg: Dict[str, Any]) -> Tuple[Dict[str, Any], Path]:
    path = table_path(root, cfg)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RoutingError(f"{path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RoutingError(f"{path}: top level must be a JSON object")
    return normalize(data), path


def _num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def table_problems(table: Dict[str, Any], formula: bool = False) -> List[str]:
    """Problems of a normalized table; ``formula`` also rejects placeholder model ids."""
    problems: List[str] = []
    params = table["params"]
    for key in ("p0", "s", "retry_mult"):
        if not _num(params.get(key)) or params[key] < 0 or (key == "s" and params[key] == 0):
            problems.append(f"params.{key} must be a non-negative number (s > 0)")
    floors = table["floors"]
    if sorted(floors) != [str(i) for i in LEVELS] or not all(_num(v) for v in floors.values()):
        problems.append("floors must map the levels 1-6 to numbers")
    elif [floors[str(i)] for i in LEVELS] != sorted(floors[str(i)] for i in LEVELS):
        problems.append("floors must not decrease with the level")
    for name, pool in table["pools"].items():
        if not isinstance(pool, dict) or not isinstance(pool.get("capped"), bool):
            problems.append(f"pools.{name} needs 'capped' (true or false)")
        elif pool["capped"] and (not _num(pool.get("scarcity")) or pool["scarcity"] < 1):
            problems.append(f"pools.{name}: a capped pool needs 'scarcity' >= 1")
    for name, role in table["roles"].items():
        if not isinstance(role, dict) or not all(isinstance(role.get(k), int) and 1 <= role[k] <= 6 for k in ("base", "min")):
            problems.append(f"roles.{name} needs integer 'base' and 'min' in 1-6")
    point_problems = _point_problems(table, formula)
    problems.extend(point_problems)
    if formula and not point_problems and not any(p.startswith("floors") for p in problems):
        # A level without a candidate would run its calls on the inherited session model.
        best = max(p["intelligence"] for p in table["points"])
        for level in LEVELS:
            if floors[str(level)] > best:
                problems.append(f"level {level}: no point reaches its floor {floors[str(level)]} (best intelligence {best}); "
                                "add a point or lower floors")
    return problems


def _point_problems(table: Dict[str, Any], formula: bool) -> List[str]:
    problems: List[str] = []
    points = table["points"]
    if not isinstance(points, list) or not points:
        return ["points must be a non-empty list"]
    seen = set()
    for i, p in enumerate(points):
        where = f"points[{i}]"
        if not isinstance(p, dict):
            problems.append(f"{where} must be an object")
            continue
        why = model_id_problem(p.get("model"))
        if why:
            problems.append(f"{where}.model: {why}")
        elif formula and p["model"].startswith(PLACEHOLDER_PREFIX):
            problems.append(f"{where}: model id {p['model']!r} is a template placeholder; fill the models table with your frontier (docs/CONFIG.md)")
        if p.get("effort", "") not in EFFORTS:
            problems.append(f"{where}: unknown effort {p.get('effort')!r}")
        for key in ("intelligence", "cost_per_task", "tokens_per_task"):
            if not _num(p.get(key)) or p[key] < 0:
                problems.append(f"{where}.{key} must be a non-negative number")
        if p.get("pool") not in table["pools"]:
            problems.append(f"{where}: unknown pool {p.get('pool')!r}")
        key = point_key(p)
        if key in seen:
            problems.append(f"{where}: duplicate point {key}")
        seen.add(key)
    return problems


def point_key(p: Dict[str, Any]) -> str:
    return f"{p.get('model')}/{p.get('effort', '')}"


def pool_mult(table: Dict[str, Any], point: Dict[str, Any], level: int) -> float:
    pool = table["pools"].get(point["pool"]) or {}
    return float(pool.get("scarcity", 1)) if pool.get("capped") and level <= 3 else 1.0


def expected_cost(table: Dict[str, Any], point: Dict[str, Any], level: int) -> float:
    prm = table["params"]
    floor = table["floors"][str(level)]
    risk = prm["p0"] * math.exp(-(point["intelligence"] - floor) / prm["s"]) * prm["retry_mult"]
    return point["cost_per_task"] * (1 + risk) * pool_mult(table, point, level)


def candidates(table: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """Per level, the eligible points ordered by E (ties: higher intelligence, then model id)."""
    out: Dict[str, List[Dict[str, Any]]] = {}
    for level in LEVELS:
        floor = table["floors"][str(level)]
        rows = [
            {"model": p["model"], "effort": p.get("effort", ""), "intelligence": p["intelligence"], "pool": p["pool"],
             "cost_per_task": p["cost_per_task"], "E": expected_cost(table, p, level)}
            for p in table["points"] if p["intelligence"] >= floor
        ]
        rows.sort(key=lambda r: (r["E"], -r["intelligence"], r["model"], r["effort"]))
        out[str(level)] = rows
    return out


def dominated(table: Dict[str, Any]) -> List[str]:
    """Points another point beats on both intelligence and cost (never picked at L4-L6)."""
    out = []
    for p in table["points"]:
        for q in table["points"]:
            if q is p:
                continue
            if q["intelligence"] >= p["intelligence"] and q["cost_per_task"] <= p["cost_per_task"] and (
                    q["intelligence"] > p["intelligence"] or q["cost_per_task"] < p["cost_per_task"]):
                out.append(point_key(p))
                break
    return out


def frontier_intelligence(table: Dict[str, Any]) -> List[float]:
    dom = set(dominated(table))
    return sorted({p["intelligence"] for p in table["points"] if point_key(p) not in dom})


def mode_of(cfg: Dict[str, Any]) -> str:
    return cfg["pipeline"].get("routing") or "tiers"


def pipeline_routing(root: Path, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """``{mode, table, candidates}`` for ``args.config.routing`` (table null when unusable)."""
    mode = mode_of(cfg)
    try:
        table, path = load_table(root, cfg)
    except RoutingError as exc:
        return {"mode": mode, "table": None, "candidates": {}, "problems": [str(exc)]}
    problems = table_problems(table, formula=mode == "formula")
    if problems:
        return {"mode": mode, "table": None, "candidates": {}, "problems": [f"{path}: {p}" for p in problems]}
    return {"mode": mode, "table": table, "candidates": candidates(table)}


def config_problems(cfg: Dict[str, Any]) -> List[str]:
    """Problems of ``models`` and the routing keys (used by ``config.validate``)."""
    problems = tier_problems(cfg["models"])
    mode = cfg["pipeline"].get("routing", "tiers")
    if mode not in MODES:
        problems.append(f"pipeline.routing must be 'tiers' or 'formula', got {mode!r}")
    elif mode == "formula":
        root = Path(cfg.get("_root") or ".")
        try:
            table, path = load_table(root, cfg)
        except RoutingError as exc:
            return problems + [f"models table: {exc}"]
        problems.extend(f"models table {path}: {p}" for p in table_problems(table, formula=True))
    return problems


def doctor_warnings(root: Path, cfg: Dict[str, Any]) -> List[str]:
    models = cfg["models"]
    empty = [t for t in TIERS if not tier_point(models.get(t, ""))]
    if "gate" in empty and tier_point(models.get("mid", "")):
        empty.remove("gate")
    out = []
    if empty:
        out.append(f"models: {', '.join(empty)} empty: these roles inherit the session model (set explicit ids; README \"Model routing\")")
    try:
        table, _ = load_table(root, cfg)
        keys = {point_key(p) for p in table["points"] if isinstance(p, dict)}
    except RoutingError:
        keys = set()
    for tier in TIERS:
        pt = tier_point(models.get(tier, ""))
        if pt and point_key(pt) not in keys:
            out.append(f"models.{tier} ({point_key(pt).rstrip('/')}): no cap fallback, not a point of the models table")
    return out


# ---------------------------------------------------------------------------
# sdd routing table
# ---------------------------------------------------------------------------

def cmd_table(root: Path, cfg: Dict[str, Any], as_json: bool) -> int:
    try:
        table, path = load_table(root, cfg)
    except RoutingError as exc:
        print(f"routing: {exc}", file=sys.stderr)
        return 1
    problems = table_problems(table, formula=mode_of(cfg) == "formula")
    levels = candidates(table) if not problems else {}
    out = {"valid": not problems, "problems": problems, "source": str(path), "mode": mode_of(cfg),
           "floors": table["floors"], "dominated": dominated(table) if not problems else [], "levels": levels}
    if as_json:
        print(json.dumps(out, indent=2))
        return 0 if not problems else 1
    print(f"models table {path} (mode {out['mode']})")
    for p in problems:
        print(f"error: {p}")
    for level, rows in levels.items():
        print(f"L{level} floor {table['floors'][level]}:")
        for r in rows:
            mark = "  (dominated)" if point_key(r) in out["dominated"] else ""
            print(f"  E={r['E']:.4f}  {point_key(r):<32} I={r['intelligence']:<5} pool={r['pool']}{mark}")
    return 0 if not problems else 1


# ---------------------------------------------------------------------------
# sdd routing log
# ---------------------------------------------------------------------------

def _read_json(path: str) -> Any:
    try:
        text = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
        return json.loads(text)
    except (OSError, json.JSONDecodeError) as exc:
        raise RoutingError(f"{path}: {exc}") from exc


def _telemetry(data: Any) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """(summary, records) from a run summary or a bare telemetry array."""
    if isinstance(data, list):
        return {}, data
    if isinstance(data, dict) and isinstance(data.get("telemetry"), list):
        return data, data["telemetry"]
    raise RoutingError("no 'telemetry' list in the input (expected the pipeline summary or its telemetry array)")


def validate_records(records: List[Any]) -> List[str]:
    problems = []
    for i, r in enumerate(records):
        if not isinstance(r, dict):
            problems.append(f"telemetry[{i}] is not an object")
            continue
        missing = [f for f in RECORD_FIELDS if f not in r]
        if missing:
            problems.append(f"telemetry[{i}] misses {', '.join(missing)}")
        elif r["outcome"] not in OUTCOMES or not isinstance(r["attempt"], int) or not isinstance(r["level"], int):
            problems.append(f"telemetry[{i}]: outcome must be ok, fail or null; attempt and level integers")
    return problems


def _iso_ms(epoch_ms: Any) -> Optional[str]:
    if not _num(epoch_ms):
        return None
    dt = datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(epoch_ms) % 1000:03d}Z"


def _parse_iso(text: Any) -> Optional[datetime]:
    if not isinstance(text, str):
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def times_from_progress(progress: Any) -> Optional[Dict[str, List[Dict[str, Any]]]]:
    """label -> [{started_at, duration_ms, replayed}] in call order, or None for an unknown format."""
    if not isinstance(progress, list):
        return None
    entries = [e for e in progress if isinstance(e, dict) and e.get("type") == "workflow_agent" and "label" in e]
    entries.sort(key=lambda e: e.get("index", 0))
    out: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for e in entries:
        cached = e.get("cached") is True
        out[e["label"]].append({
            "started_at": None if cached else _iso_ms(e.get("startedAt")),
            "duration_ms": None if cached or not _num(e.get("durationMs")) else int(e["durationMs"]),
            "replayed": cached,
        })
    return out


def times_from_transcripts(tdir: Path) -> Optional[Dict[str, List[Dict[str, Any]]]]:
    """Same mapping from ``journal.jsonl`` and the agents' transcript timestamps."""
    journal = tdir / "journal.jsonl"
    if not journal.is_file():
        return None
    out: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for line in journal.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict) and entry.get("type") == "started" and "label" in entry:
            out[entry["label"]].append(_transcript_span(tdir / f"agent-{entry.get('agentId')}.jsonl"))
    return out


def _transcript_span(path: Path) -> Dict[str, Any]:
    stamps: List[datetime] = []
    raw: List[str] = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                ts = json.loads(line).get("timestamp")
            except (json.JSONDecodeError, AttributeError):
                continue
            dt = _parse_iso(ts)
            if dt:
                stamps.append(dt)
                raw.append(ts)
    if not stamps:
        return {"started_at": None, "duration_ms": None, "replayed": False}
    return {"started_at": raw[0], "duration_ms": int(round((stamps[-1] - stamps[0]).total_seconds() * 1000)), "replayed": False}


def _dedup_key(r: Dict[str, Any]) -> Tuple:
    return (r.get("run"), r.get("call"), r.get("attempt"), r.get("model"), r.get("effort"), r.get("outcome"))


def cmd_log(root: Path, cfg: Dict[str, Any], output_file: Optional[str], file: Optional[str], transcript_dir: Optional[str]) -> int:
    try:
        data = _read_json(output_file or file or "-")
        host = None
        if output_file:
            if not isinstance(data, dict) or "result" not in data:
                raise RoutingError(f"{output_file}: no 'result' (expected the Workflow output file)")
            host = times_from_progress(data.get("workflowProgress"))
            data = json.loads(data["result"]) if isinstance(data["result"], str) else data["result"]
        summary, records = _telemetry(data)
    except (RoutingError, json.JSONDecodeError) as exc:
        print(f"routing log: {exc}", file=sys.stderr)
        return 2
    problems = validate_records(records)
    if problems:
        for p in problems:
            print(f"routing log: {p}", file=sys.stderr)
        return 2
    if host is None and transcript_dir:
        host = times_from_transcripts(Path(transcript_dir))
    rows, unmatched = _join(records, summary, host or {})
    return _append(root, cfg, rows, unmatched)


def _join(records: List[Dict[str, Any]], summary: Dict[str, Any], host: Dict[str, List[Dict[str, Any]]]) -> Tuple[List[Dict[str, Any]], int]:
    seen: Dict[str, int] = defaultdict(int)
    rows, unmatched = [], 0
    logged = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for r in records:
        n = seen[r["label"]]
        seen[r["label"]] += 1
        entries = host.get(r["label"]) or []
        t = entries[n] if n < len(entries) else {"started_at": None, "duration_ms": None, "replayed": False}
        if t["started_at"] is None and not t["replayed"]:
            unmatched += 1
        row = {f: r[f] for f in RECORD_FIELDS}
        row.update({"run": summary.get("runKey"), "feature": summary.get("feature"), "status": summary.get("status"),
                    "started_at": t["started_at"], "duration_ms": t["duration_ms"], "replayed": t["replayed"], "logged_at": logged})
        rows.append(row)
    return rows, unmatched


def _append(root: Path, cfg: Dict[str, Any], rows: List[Dict[str, Any]], unmatched: int) -> int:
    path = root / cfg["paths"]["logs_dir"] / "routing.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = set()
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                existing.add(_dedup_key(json.loads(line)))
            except json.JSONDecodeError:
                continue
    added = 0
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            if _dedup_key(row) in existing:
                continue
            existing.add(_dedup_key(row))
            fh.write(json.dumps(row, sort_keys=True) + "\n")
            added += 1
    print(f"routing log: appended {added}, skipped {len(rows) - added} already logged, {unmatched} without times -> {path}")
    return 0


# ---------------------------------------------------------------------------
# sdd routing refit
# ---------------------------------------------------------------------------

def _outcome(value: Any) -> Any:
    """Outcome vocabulary: "ok", "fail" or None (infrastructure); "failed" (other telemetry formats) is "fail"."""
    return "fail" if value == "failed" else value


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise RoutingError(f"{path}: {exc}") from exc
    out = []
    for i, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RoutingError(f"{path}:{i}: {exc}") from exc
        if not isinstance(row, dict) or "level" not in row or "outcome" not in row:
            raise RoutingError(f"{path}:{i}: not a routing record")
        row["outcome"] = _outcome(row["outcome"])
        out.append(row)
    if not out:
        raise RoutingError(f"{path}: no records")
    return out


LEVEL_SUFFIX = re.compile(r" \[L([1-6])\]")
RETRY_SUFFIX = re.compile(r"(?: retry| \([^()]*\b(?:retry|fallback)\))$")  # "x retry", "x (retry)", "x (capped fallback)"
DEFAULT_JOURNAL_GLOBS = (
    "~/.claude/projects/*/*/subagents/workflows/*/journal.jsonl",
    "~/.claude/projects/*/subagents/workflows/*/journal.jsonl",
)


def journal_paths(specs: Iterable[str]) -> List[Path]:
    """Journal files from paths, directories (holding journal.jsonl) and globs, sorted and unique."""
    out: List[Path] = []
    for spec in specs:
        spec = os.path.expanduser(spec)
        hits = sorted(_glob.glob(spec)) if any(ch in spec for ch in "*?[") else [spec]
        for h in hits:
            p = Path(h)
            p = p / "journal.jsonl" if p.is_dir() else p
            if p.is_file() and p not in out:
                out.append(p)
    return out


def read_journal(path: Path) -> List[Dict[str, Any]]:
    """Attempt records of one Workflow journal: only calls whose label carries a ` [L<n>]` level suffix.

    Outcome: a result with ``ok: false`` is "fail", another non-null result "ok"; a null result, a
    ``failed`` entry or a call without a result is None (usage cap, outage, killed session).
    The model is ``model`` from ``agent-<id>.meta.json`` next to the journal ("inherit" when absent:
    the call ran on the session model); journals carry no effort. A label ending in ``retry``,
    ``(retry)`` or ``(... fallback)`` is attempt 2.
    """
    started: Dict[str, Dict[str, Any]] = {}
    results: Dict[str, Dict[str, Any]] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise RoutingError(f"{path}: {exc}") from exc
    for line in lines:
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(d, dict):
            continue
        if d.get("type") == "started":
            started[d.get("key")] = d
        elif d.get("type") in ("result", "failed"):
            results[d.get("key")] = d
    rows = []
    for key, st in started.items():
        label = str(st.get("label") or "")
        m = LEVEL_SUFFIX.search(label)
        if not m:
            continue  # not routed by level
        model = "inherit"
        meta = path.parent / f"agent-{st.get('agentId')}.meta.json"
        try:
            model = json.loads(meta.read_text(encoding="utf-8")).get("model") or "inherit"
        except (OSError, ValueError, AttributeError):
            pass
        r = results.get(key)
        res = r.get("result") if r and r.get("type") == "result" else None
        outcome = None if res is None else "fail" if isinstance(res, dict) and res.get("ok") is False else "ok"
        base = LEVEL_SUFFIX.sub("", label)
        rows.append({"run": path.parent.name, "label": base, "level": int(m.group(1)), "model": model, "effort": None,
                     "outcome": outcome, "attempt": 2 if RETRY_SUFFIX.search(base) else 1, "source": "journal"})
    return rows


def refit_from_telemetry(root: Path, cfg: Dict[str, Any], files: List[str]) -> Tuple[List[Dict[str, Any]], List[str]]:
    paths = [Path(f) for f in files] or [root / cfg["paths"]["logs_dir"] / "routing.jsonl"]
    rows: List[Dict[str, Any]] = []
    for p in paths:
        rows.extend(_read_jsonl(p))
    return rows, [str(p) for p in paths]


def refit_from_journals(root: Path, cfg: Dict[str, Any], specs: List[str]) -> Tuple[List[Dict[str, Any]], List[str]]:
    paths = journal_paths(specs or DEFAULT_JOURNAL_GLOBS)
    if not paths:
        raise RoutingError(f"no workflow journals found ({', '.join(specs or DEFAULT_JOURNAL_GLOBS)})")
    rows: List[Dict[str, Any]] = []
    for p in paths:
        rows.extend(read_journal(p))
    if not rows:
        raise RoutingError(f"no routed attempts (labels with a ' [L<n>]' suffix) in {len(paths)} journal(s)")
    return rows, [f"{len(paths)} journal(s)"]


# Refit inputs: name -> reader(root, cfg, sources) -> (rows, source descriptions). Each row needs "level" and
# "outcome" ("ok" | "fail" | None) and may carry "model" and "attempt". Add a reader here for another log format.
REFIT_INPUTS = {"telemetry": refit_from_telemetry, "journal": refit_from_journals}


def _rates(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = list(rows)
    judged = [r for r in rows if r["outcome"] in ("ok", "fail")]
    fails = sum(1 for r in judged if r["outcome"] == "fail")
    retries = sum(1 for r in judged if isinstance(r.get("attempt"), int) and r["attempt"] > 1)
    n = len(judged)
    return {"samples": n, "fail": fails, "infra": len(rows) - n,
            "failure_rate": fails / n if n else 0.0, "retry_rate": retries / n if n else 0.0}


def refit(rows: List[Dict[str, Any]], table: Dict[str, Any], min_samples: int, max_fail: float) -> Dict[str, Any]:
    by_level: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_level[int(r["level"])].append(r)
    frontier = frontier_intelligence(table)
    levels, suggestions = {}, []
    for level in sorted(by_level):
        stats = _rates(by_level[level])
        models: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for r in by_level[level]:
            models[str(r.get("model"))].append(r)
        stats["models"] = {m: _rates(rs) for m, rs in sorted(models.items())}
        floor = table["floors"].get(str(level), 0)
        stats["floor"] = floor
        levels[str(level)] = stats
        n = stats["samples"]
        if n >= min_samples and stats["failure_rate"] > max_fail:
            up = [i for i in frontier if i > floor]
            if up:
                suggestions.append({"level": level, "floor": floor, "suggest": up[0], "direction": "raise",
                                    "reason": f"failure rate {stats['failure_rate']:.0%} over {n} samples exceeds {max_fail:.0%}"})
        elif level > 1 and stats["fail"] == 0 and n >= 3 * min_samples:
            down = [i for i in frontier if i < floor]
            if down:
                suggestions.append({"level": level, "floor": floor, "suggest": down[-1], "direction": "lower",
                                    "reason": f"no failures over {n} samples (>= 3x {min_samples})"})
    return {"levels": levels, "suggestions": suggestions, "min_samples": min_samples, "max_fail": max_fail}


def cmd_refit(root: Path, cfg: Dict[str, Any], file: Optional[str], as_json: bool, min_samples: int, max_fail: float,
              inputs: Optional[List[str]] = None, journals: Optional[List[str]] = None) -> int:
    """``inputs``: names from REFIT_INPUTS (default telemetry, or journal when ``journals`` are given); rows of all are pooled."""
    inputs = list(dict.fromkeys(inputs or (["journal"] if journals else ["telemetry"])))
    sources = {"telemetry": [file] if file else [], "journal": list(journals or [])}
    rows: List[Dict[str, Any]] = []
    described: List[str] = []
    try:
        for name in inputs:
            if name not in REFIT_INPUTS:
                raise RoutingError(f"unknown input {name!r} (known: {', '.join(REFIT_INPUTS)})")
            got, desc = REFIT_INPUTS[name](root, cfg, sources.get(name, []))
            rows.extend(got)
            described.extend(desc)
        table, _ = load_table(root, cfg)
    except RoutingError as exc:
        print(f"routing refit: {exc}", file=sys.stderr)
        return 2
    out = refit(rows, table, min_samples, max_fail)
    out["inputs"] = inputs
    if as_json:
        print(json.dumps(out, indent=2))
        return 0
    print(f"routing refit over {len(rows)} attempt(s) from {', '.join(described)} (report only; nothing is changed)")
    for level, s in out["levels"].items():
        print(f"L{level} floor {s['floor']}: {s['samples']} samples, failure {s['failure_rate']:.0%}, retries {s['retry_rate']:.0%}, infrastructure failures {s['infra']}")
        for model, m in s["models"].items():
            print(f"    {model}: {m['samples']} samples, failure {m['failure_rate']:.0%}")
    for sug in out["suggestions"]:
        print(f"suggest: {sug['direction']} the L{sug['level']} floor {sug['floor']} -> {sug['suggest']} ({sug['reason']})")
    if not out["suggestions"]:
        print("no floor changes suggested")
    return 0
