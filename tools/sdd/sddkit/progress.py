"""``sdd progress``: lifecycle-stage completion per feature, milestone and overall.

Derived from the roadmap and git refs only (nothing is checked out):

=================  =======  ==========================================================
stage              percent  evidence
=================  =======  ==========================================================
not-started            0.0  no ``<features_dir>/<nnn>-*/spec.md`` anywhere
spec-draft             5.0  spec present, ``Status: Draft`` (or unknown status)
spec-approved         15.0  ``Status: Approved``
planned                20.0  ``design.md`` and ``tasks.md`` present
tests                  30.0  a ``test(F<NNN>)`` commit on the branch (red tests)
implementing      30 + 60r  r = checked tasks / total tasks in ``tasks.md`` (r > 0)
implemented           95.0  ``Status: Implemented`` but not merged into main
merged               100.0  the feature branch is an ancestor of main, the spec on
                            main says ``Status: Implemented``, or the feature is in
                            ``features.done_without_spec``
=================  =======  ==========================================================

Milestone and overall percentages are the unweighted mean of feature percents.
The evidence ref for an unmerged feature is its newest ``<branch_prefix>F<NNN>-*``
branch (remote first, then local), else main.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .trace import spec_status
from .util import Git, is_placeholder_line, strip_html_comments

SCHEMA = 1
STAGES = {
    "not-started": 0.0,
    "spec-draft": 5.0,
    "spec-approved": 15.0,
    "planned": 20.0,
    "tests": 30.0,
    "implemented": 95.0,
    "merged": 100.0,
}
IMPLEMENTING_BASE = 30.0
IMPLEMENTING_SPAN = 60.0
_TASK_LINE = re.compile(r"^- \[([ xX])\] T\d{2,3}\b")


def parse_roadmap(text: str, cfg: Dict[str, Any]) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    m_rx = re.compile(cfg["features"]["roadmap_milestone_regex"])
    f_rx = re.compile(cfg["features"]["roadmap_feature_regex"])
    milestones: List[Dict[str, str]] = []
    features: List[Dict[str, str]] = []
    current: Optional[str] = None
    seen_m, seen_f = set(), set()
    for line in text.splitlines():
        m = m_rx.match(line)
        if m:
            current = m.group(1)
            if current not in seen_m:
                seen_m.add(current)
                milestones.append({"id": current, "name": m.group(2).strip()})
            continue
        f = f_rx.match(line)
        if f:
            fid, slug = f.group(1), f.group(2).strip()
            if fid in seen_f:
                raise ValueError(f"duplicate feature heading {fid} in the roadmap")
            seen_f.add(fid)
            features.append({"id": fid, "name": slug, "milestone": current or "unassigned"})
    if not features:
        raise ValueError("no feature headings found in the roadmap (features.roadmap_feature_regex)")
    if any(f["milestone"] == "unassigned" for f in features) and "unassigned" not in seen_m:
        milestones.append({"id": "unassigned", "name": "No milestone"})
    return milestones, features


def count_tasks(text: Optional[str]) -> Tuple[int, int]:
    if text is None:
        return (0, 0)
    done = total = 0
    for line in strip_html_comments(text).splitlines():
        if is_placeholder_line(line):
            continue
        m = _TASK_LINE.match(line)
        if not m:
            continue
        total += 1
        if m.group(1) in "xX":
            done += 1
    return (done, total)


class Repo:
    def __init__(self, root: Path, cfg: Dict[str, Any], remote: str) -> None:
        self.git = Git(root)
        self.cfg = cfg
        self.remote = remote
        self.features_dir = cfg["paths"]["features_dir"]
        self.prefix = cfg["features"]["branch_prefix"]
        self.num_rx = re.compile(r"^(\d{%d,})-" % int(cfg["features"]["id_digits"]))

    def feature_dirs(self, ref: str) -> Dict[str, str]:
        dirs: Dict[str, str] = {}
        for line in self.git.ls_dirs(ref, self.features_dir):
            base = line.rsplit("/", 1)[-1]
            m = self.num_rx.match(base)
            if m and m.group(1) not in dirs:
                dirs[m.group(1)] = line
        return dirs

    def feature_branches(self) -> Dict[str, List[str]]:
        refs = []
        if self.remote:
            refs += self.git.branches(f"refs/remotes/{self.remote}/{self.prefix}")
        refs += self.git.branches(f"refs/heads/{self.prefix}")
        rows = sorted(((-self.git.commit_time(r), r) for r in refs))
        out: Dict[str, List[str]] = {}
        id_rx = re.compile(rf"{re.escape(self.cfg['features']['id_prefix'])}(\d+)")
        for _, ref in rows:
            short = ref.split(self.prefix, 1)[-1]
            m = id_rx.match(short)
            if m:
                out.setdefault(m.group(1), []).append(ref)
        return out

    def has_test_commit(self, fid: str, base: str, ref: str) -> bool:
        rx = re.compile(rf"^test\({re.escape(fid)}\)")
        return any(rx.match(s) for s in self.git.subjects(f"{base}..{ref}"))


def feature_stage(repo: Repo, fid: str, main_ref: str, main_dirs: Dict[str, str], branches: List[str], done_without_spec: set) -> Tuple[str, float]:
    num = fid[len(repo.cfg["features"]["id_prefix"]):]
    if fid in done_without_spec:
        return "merged", STAGES["merged"]
    main_dir = main_dirs.get(num)
    if main_dir:
        spec = repo.git.show(main_ref, f"{main_dir}/spec.md")
        if spec is not None and spec_status(spec) == "Implemented":
            return "merged", STAGES["merged"]
    for ref in branches:
        if repo.git.is_ancestor(ref, main_ref):
            return "merged", STAGES["merged"]
    ref = branches[0] if branches else main_ref
    dirs = repo.feature_dirs(ref) if ref != main_ref else main_dirs
    fdir = dirs.get(num)
    if fdir is None:
        return "not-started", 0.0
    spec = repo.git.show(ref, f"{fdir}/spec.md")
    if spec is None:
        return "not-started", 0.0
    status = spec_status(spec)
    if status == "Implemented":
        return "implemented", STAGES["implemented"]
    stage = "spec-approved" if status == "Approved" else "spec-draft"
    design = repo.git.show(ref, f"{fdir}/design.md")
    tasks = repo.git.show(ref, f"{fdir}/tasks.md")
    if design is None or tasks is None:
        return stage, STAGES[stage]
    stage = "planned"
    if ref != main_ref and repo.has_test_commit(fid, main_ref, ref):
        stage = "tests"
    done, total = count_tasks(tasks)
    if total > 0 and done > 0:
        return "implementing", IMPLEMENTING_BASE + IMPLEMENTING_SPAN * done / total
    return stage, STAGES[stage]


def _mean(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def build_report(root: Path, cfg: Dict[str, Any], main_ref: Optional[str] = None, remote: str = "origin", now: Optional[_dt.datetime] = None) -> Dict[str, Any]:
    repo = Repo(root, cfg, remote)
    main_branch = cfg.get("features", {}).get("main_branch") or "main"
    resolved = repo.git.resolve(main_ref or "", f"{remote}/{main_branch}" if remote else "", main_branch, "main", "master")
    if not resolved:
        raise RuntimeError("could not resolve the main branch (pass --main-ref)")
    roadmap = repo.git.show(resolved, cfg["paths"]["roadmap"])
    if roadmap is None:
        raise RuntimeError(f"{cfg['paths']['roadmap']} not found at {resolved}")
    milestones, features = parse_roadmap(roadmap, cfg)
    main_dirs = repo.feature_dirs(resolved)
    branches = repo.feature_branches()
    done_set = set(cfg["features"].get("done_without_spec") or [])
    prefix = cfg["features"]["id_prefix"]
    rows, raw = [], {}
    for f in features:
        num = f["id"][len(prefix):]
        stage, pct = feature_stage(repo, f["id"], resolved, main_dirs, branches.get(num, []), done_set)
        raw[f["id"]] = pct
        rows.append({"id": f["id"], "name": f["name"], "milestone": f["milestone"], "stage": stage, "percent": round(pct, 1)})
    ms = []
    for m in milestones:
        ids = [f["id"] for f in features if f["milestone"] == m["id"]]
        ms.append({"id": m["id"], "name": m["name"], "percent": round(_mean([raw[i] for i in ids]), 1), "features": ids})
    if now is None:
        epoch = os.environ.get("SOURCE_DATE_EPOCH")
        now = _dt.datetime.fromtimestamp(int(epoch), tz=_dt.timezone.utc) if epoch else _dt.datetime.now(tz=_dt.timezone.utc)
    return {
        "schema": SCHEMA,
        "generatedAt": now.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "commit": repo.git.run("rev-parse", "--short=7", f"{resolved}^{{commit}}").strip(),
        "method": "lifecycle-stage weights per roadmap feature, equal feature weights",
        "overall": round(_mean(list(raw.values())), 1),
        "milestones": ms,
        "features": rows,
    }


def render_table(report: Dict[str, Any]) -> str:
    lines = [f"overall {report['overall']:.1f}% (commit {report['commit']})"]
    for m in report["milestones"]:
        lines.append(f"  {m['id']:<4} {m['percent']:5.1f}%  {m['name']} ({len(m['features'])} features)")
    lines.append("")
    for f in report["features"]:
        lines.append(f"  {f['id']:<6} {f['percent']:5.1f}%  {f['stage']:<14} {f['name']}")
    return "\n".join(lines) + "\n"


def render_json(report: Dict[str, Any]) -> str:
    return json.dumps(report, indent=2, ensure_ascii=False) + "\n"
