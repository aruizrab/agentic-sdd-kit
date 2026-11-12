"""Spec and task file helpers used by the pipeline and the skills.

* ``count_markers``: open ``[NEEDS CLARIFICATION: ...]`` markers, ignoring
  HTML comments, fenced code blocks, inline code spans and prose mentions
  (lesson: an agent once counted the template's explanatory comment as an
  open question and blocked approval).
* ``spec_info``: the ``Status:`` header, marker count and requirement count.
* ``parse_tasks``: the wave/task structure of ``tasks.md`` as data, with the
  component of each task derived from its files (``components.path_regex``).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .trace import spec_status
from .util import strip_code, strip_html_comments

TASK_LINE = re.compile(
    r"^- \[(?P<done>[ xX])\] (?P<id>T\d{2,3})(?P<par>\s+\[P\])?\s+(?P<rest>.*)$"
)
WAVE_HEADING = re.compile(r"^##\s+Wave\s+(?P<n>\d+)", re.IGNORECASE)
FIELD_SPLIT = re.compile(r"\s+[—–-]{1,2}\s+(?=(?:files|verifies|depends)\s*:)", re.IGNORECASE)


def count_markers(text: str, marker: str = "[NEEDS CLARIFICATION:") -> int:
    cleaned = strip_code(strip_html_comments(text))
    count = 0
    needle = marker.lower()
    for line in cleaned.splitlines():
        low = line.lower()
        start = 0
        while True:
            idx = low.find(needle, start)
            if idx < 0:
                break
            # A prose mention: the marker text appears inside quotes or after "the marker".
            before = low[:idx].rstrip()
            if before.endswith(('"', "'", "the marker", "marker", "write", "writes", "e.g.", "like")):
                start = idx + len(needle)
                continue
            count += 1
            start = idx + len(needle)
    return count


def spec_info(path: Path, cfg: Dict[str, Any]) -> Dict[str, Any]:
    if not path.is_file():
        return {"status": "Missing", "needsClarification": 0, "requirements": 0, "must": 0, "path": str(path)}
    text = path.read_text(encoding="utf-8", errors="replace")
    id_regex = cfg["requirements"]["id_regex"]
    prios = "|".join(re.escape(p) for p in cfg["requirements"]["priorities"])
    req_line = re.compile(rf"^- \*\*(?:{id_regex})\*\* \[(?P<prio>{prios})\]", re.MULTILINE)
    body = strip_html_comments(text)
    reqs = req_line.findall(body)
    must = cfg["requirements"]["priorities"][0]
    budget = cfg["requirements"]["budget"]
    return {
        "status": spec_status(text),
        "needsClarification": count_markers(text, cfg["requirements"]["clarification_marker"]),
        "requirements": len(reqs),
        "must": sum(1 for p in reqs if p == must),
        "withinBudget": budget["min"] <= len(reqs) <= budget["max"],
        "budget": budget,
        "path": str(path),
    }


def component_of(file: str, cfg: Dict[str, Any]) -> str:
    rx = cfg["components"].get("path_regex") or ""
    if rx:
        m = re.search(rx, file)
        if m and m.groups():
            return m.group(1)
    return cfg["components"].get("root", "workspace")


def _split_list(value: str) -> List[str]:
    value = value.strip()
    if value in ("", "-", "none", "—", "–"):
        return []
    return [x.strip().strip("`") for x in re.split(r"[,\s]+", value) if x.strip() and x.strip() not in ("-", "—")]


def parse_tasks(text: str, cfg: Dict[str, Any], include_done: bool = False) -> Dict[str, Any]:
    body = strip_html_comments(text)
    waves: Dict[int, List[Dict[str, Any]]] = {}
    wave = 0
    security = False
    tests_sha = None
    for raw in body.splitlines():
        line = raw.rstrip()
        sm = re.match(r"^Security-sensitive:\s*(\S+)", line, re.IGNORECASE)
        if sm:
            security = sm.group(1).strip().lower() in ("yes", "true")
            continue
        tm = re.match(r"^Tests commit \(red check\):\s*([0-9a-f]{7,40})\b", line, re.IGNORECASE)
        if tm:
            tests_sha = tm.group(1)
            continue
        wm = WAVE_HEADING.match(line)
        if wm:
            wave = int(wm.group("n"))
            continue
        m = TASK_LINE.match(line)
        if not m:
            continue
        if "<imperative description>" in line:
            continue
        done = m.group("done") in "xX"
        if done and not include_done:
            continue
        parts = FIELD_SPLIT.split(m.group("rest"))
        title = parts[0].strip()
        fields = {"files": [], "verifies": [], "depends": []}
        for part in parts[1:]:
            k, _, v = part.partition(":")
            k = k.strip().lower()
            if k in fields:
                fields[k] = _split_list(v)
        comps = sorted({component_of(f, cfg) for f in fields["files"]}) or [cfg["components"].get("root", "workspace")]
        waves.setdefault(wave, []).append(
            {
                "id": m.group("id"),
                "title": title,
                "done": done,
                "parallel": bool(m.group("par")),
                "files": fields["files"],
                "verifies": fields["verifies"],
                "depends": fields["depends"],
                "components": comps,
                "wave": wave,
            }
        )
    return {
        "ok": True,
        "securitySensitive": security,
        "testsSha": tests_sha,
        "waves": [{"wave": w, "tasks": waves[w]} for w in sorted(waves)],
    }


def build_lanes(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Group a wave's tasks into lanes of disjoint components (same rule as the pipeline)."""
    if any(not t["parallel"] for t in tasks):
        return [{"components": ["*"], "tasks": sorted(tasks, key=lambda t: t["id"])}]
    lanes: List[Dict[str, Any]] = []
    for t in tasks:
        comps = t["components"] or ["workspace"]
        hits = [l for l in lanes if any(c in comps for c in l["components"])]
        if not hits:
            lanes.append({"components": list(comps), "tasks": [t]})
            continue
        into = hits[0]
        for other in hits[1:]:
            into["components"].extend(c for c in other["components"] if c not in into["components"])
            into["tasks"].extend(other["tasks"])
            lanes.remove(other)
        into["components"].extend(c for c in comps if c not in into["components"])
        into["tasks"].append(t)
    for l in lanes:
        l["tasks"].sort(key=lambda t: t["id"])
    return lanes


def validate_tasks(plan: Dict[str, Any], known_ids: Optional[List[str]] = None) -> List[str]:
    """Plan-stage exit criteria: deps point to earlier waves; [P] tasks in a wave have disjoint files."""
    problems: List[str] = []
    wave_of: Dict[str, int] = {}
    for w in plan["waves"]:
        for t in w["tasks"]:
            if t["id"] in wave_of:
                problems.append(f"duplicate task id {t['id']}")
            wave_of[t["id"]] = w["wave"]
    for w in plan["waves"]:
        files_seen: Dict[str, str] = {}
        for t in w["tasks"]:
            for d in t["depends"]:
                if d in wave_of and wave_of[d] >= w["wave"]:
                    problems.append(f"{t['id']} depends on {d} which is not in an earlier wave")
            if t["parallel"]:
                for f in t["files"]:
                    if f in files_seen:
                        problems.append(f"[P] tasks {files_seen[f]} and {t['id']} in wave {w['wave']} share file {f}")
                    files_seen[f] = t["id"]
            if not t["files"]:
                problems.append(f"{t['id']} lists no files")
            if known_ids is not None:
                for rid in t["verifies"]:
                    if rid not in known_ids:
                        problems.append(f"{t['id']} verifies unknown requirement {rid}")
    return problems
