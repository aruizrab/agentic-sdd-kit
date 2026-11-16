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
merged               100.0  the spec on main says ``Status: Implemented``, a feature
                            branch is an ancestor of main while main holds no spec of
                            the feature, or the feature is in
                            ``features.done_without_spec``
=================  =======  ==========================================================

Groups: every milestone heading (``features.roadmap_milestone_regex``) is a group with
``kind: "milestone"``. Features under a cross-cutting heading
(``features.roadmap_cross_cutting_regex``, default ``### Cross-cutting — <name>``) belong to
no milestone but to one extra group with id ``CC`` and ``kind: "cross-cutting"``, listed after
the milestones in the same ``milestones`` array (their ``milestone`` field names ``CC``).
Features above any heading form the ``unassigned`` group (``kind: "unassigned"``).
Group and overall percentages are the unweighted mean of feature percents; overall covers
every feature, cross-cutting ones included.

Display names: a feature's name is the title of its spec's H1 (``# F002 — <title>``, from the
newest feature branch, else main), else the roadmap heading title after the slug
(``#### F016 container-images — Container images and charts``), else the slug. A trailing
placeholder note such as ``(placeholder, specs later)`` or ``(TBD)`` is dropped from feature
titles and group names.

Idle features: a feature in a started stage (planned, tests, implementing, implemented) with an
unmerged branch also gets ``lastCommit`` (the newest commit on its unmerged branches, UTC) and
``idle`` (no such commit for 24 hours before ``generatedAt``), so a paused feature is never shown
as moving; the table marks it ``(idle since <date>)``.

The evidence ref for an unfinished feature is its newest unmerged ``<branch_prefix>F<NNN>-*``
branch (remote first, then local), else main. A feature that merges in parts (the plan first,
then one branch per work item) therefore shows its real stage until its spec on main says
Implemented, not "merged" after its first partial merge.
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
# A started feature whose unmerged branches saw no commit for this long before generatedAt is idle.
IDLE_AFTER_SECONDS = 24 * 3600
ACTIVE_STAGES = ("planned", "tests", "implementing", "implemented")
_TASK_LINE = re.compile(r"^- \[([ xX])\] T\d{2,3}\b")
KIND_MILESTONE = "milestone"
KIND_CROSS_CUTTING = "cross-cutting"
KIND_UNASSIGNED = "unassigned"
CROSS_CUTTING_ID = "CC"
DEFAULT_CROSS_CUTTING = r"(?i)^###\s+Cross-cutting\b\s*(?:[—–:-]+\s*(.*?))?\s*$"
NAME_MAX = 120
# A trailing "(placeholder, specs later)", "(placeholder)", "(TBD)", "(to be defined)" note.
_PLACEHOLDER_NOTE = re.compile(r"\s*\((?:placeholder|tbd|to be defined)\b[^()]*\)\s*$", re.IGNORECASE)
# Optional display title after the slug: "#### F016 container-images — Container images and charts".
_ROADMAP_TITLE = re.compile(r"^\s*[—–-]+\s*(\S.*?)\s*$")


def display_name(name: str) -> str:
    """``name`` without a trailing placeholder note; a name that is only the note is kept as is."""
    stripped = _PLACEHOLDER_NOTE.sub("", name).strip()
    return stripped or name.strip()


def spec_title(text: Optional[str], id_prefix: str) -> Optional[str]:
    """The title of a spec.md H1 (``# F002 — Runtime scheduler``); None without one or for the template placeholder."""
    if text is None:
        return None
    rx = re.compile(rf"^#\s+{re.escape(id_prefix)}\d+\s*[—–:-]+\s*(\S.*?)\s*$")
    for line in text.splitlines():
        if line.startswith("# "):
            m = rx.match(line)
            if not m or re.fullmatch(r"<[^>]*>", m.group(1)):
                return None
            return display_name(m.group(1))[:NAME_MAX]
    return None


def parse_roadmap(text: str, cfg: Dict[str, Any]) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    """``(groups, features)``: milestone groups in roadmap order, then the cross-cutting group (if any),
    then the unassigned group (if any). Each feature carries its ``slug``, its display ``name``
    (roadmap title, else slug) and its group id in ``milestone``."""
    m_rx = re.compile(cfg["features"]["roadmap_milestone_regex"])
    f_rx = re.compile(cfg["features"]["roadmap_feature_regex"])
    c_rx = re.compile(cfg["features"].get("roadmap_cross_cutting_regex") or DEFAULT_CROSS_CUTTING)
    milestones: List[Dict[str, str]] = []
    cross: Optional[Dict[str, str]] = None
    features: List[Dict[str, str]] = []
    current: Optional[str] = None
    seen_m, seen_f = set(), set()
    for line in text.splitlines():
        m = m_rx.match(line)
        if m:
            current = m.group(1)
            if current not in seen_m:
                seen_m.add(current)
                milestones.append({"id": current, "name": display_name(m.group(2).strip()), "kind": KIND_MILESTONE})
            continue
        c = c_rx.match(line)
        if c:
            current = CROSS_CUTTING_ID
            if cross is None:
                name = display_name((c.group(1) if c.re.groups else "") or "")
                cross = {"id": CROSS_CUTTING_ID, "name": (name[:1].upper() + name[1:]) or "Cross-cutting", "kind": KIND_CROSS_CUTTING}
            continue
        f = f_rx.match(line)
        if f:
            fid, slug = f.group(1), f.group(2).strip()
            if fid in seen_f:
                raise ValueError(f"duplicate feature heading {fid} in the roadmap")
            seen_f.add(fid)
            t = _ROADMAP_TITLE.match(f.group(3)) if f.re.groups >= 3 and f.group(3) else None
            title = display_name(t.group(1))[:NAME_MAX] if t else ""
            features.append({"id": fid, "slug": slug, "name": title or slug, "milestone": current or KIND_UNASSIGNED})
    if not features:
        raise ValueError("no feature headings found in the roadmap (features.roadmap_feature_regex)")
    groups = milestones + ([cross] if cross is not None else [])
    if any(f["milestone"] == KIND_UNASSIGNED for f in features) and KIND_UNASSIGNED not in seen_m:
        groups.append({"id": KIND_UNASSIGNED, "name": "No milestone", "kind": KIND_UNASSIGNED})
    return groups, features


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
    unmerged = [ref for ref in branches if not repo.git.is_ancestor(ref, main_ref)]
    # A merged branch means "finished" only while main holds no spec of the feature. A feature that
    # merges in parts (the plan first, then one branch per work item) has its spec on main and is
    # finished only when that spec says Implemented (above); until then its stage comes from the
    # newest unmerged branch, else from main's spec and tasks.
    if not main_dir and len(unmerged) < len(branches):
        return "merged", STAGES["merged"]
    ref = unmerged[0] if unmerged else main_ref
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


def feature_name(repo: "Repo", fid: str, fallback: str, main_ref: str, main_dirs: Dict[str, str], branches: List[str]) -> str:
    """Spec H1 title from the newest feature branch, else main; else ``fallback`` (roadmap title or slug)."""
    prefix = repo.cfg["features"]["id_prefix"]
    num = fid[len(prefix):]
    for ref in branches[:1] + [main_ref]:
        dirs = main_dirs if ref == main_ref else repo.feature_dirs(ref)
        fdir = dirs.get(num)
        title = spec_title(repo.git.show(ref, f"{fdir}/spec.md"), prefix) if fdir else None
        if title:
            return title
    return fallback


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
    if now is None:
        epoch = os.environ.get("SOURCE_DATE_EPOCH")
        now = _dt.datetime.fromtimestamp(int(epoch), tz=_dt.timezone.utc) if epoch else _dt.datetime.now(tz=_dt.timezone.utc)
    now_ts = int(now.timestamp())
    rows, raw = [], {}
    for f in features:
        num = f["id"][len(prefix):]
        fbranches = branches.get(num, [])
        stage, pct = feature_stage(repo, f["id"], resolved, main_dirs, fbranches, done_set)
        raw[f["id"]] = pct
        name = feature_name(repo, f["id"], f["name"], resolved, main_dirs, fbranches)
        rows.append({"id": f["id"], "name": name, "slug": f["slug"], "milestone": f["milestone"], "stage": stage, "percent": round(pct, 1)})
        if stage in ACTIVE_STAGES:
            unmerged = [ref for ref in fbranches if not repo.git.is_ancestor(ref, resolved)]
            last = max((repo.git.commit_time(ref) for ref in unmerged), default=0)
            if last:
                rows[-1]["lastCommit"] = _dt.datetime.fromtimestamp(last, tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                rows[-1]["idle"] = now_ts - last > IDLE_AFTER_SECONDS
    ms = []
    for m in milestones:
        ids = [f["id"] for f in features if f["milestone"] == m["id"]]
        ms.append({"id": m["id"], "name": m["name"], "kind": m["kind"], "percent": round(_mean([raw[i] for i in ids]), 1), "features": ids})
    return {
        "schema": SCHEMA,
        "generatedAt": now.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "commit": repo.git.run("rev-parse", "--short=7", f"{resolved}^{{commit}}").strip(),
        "method": "lifecycle-stage weights per roadmap feature, equal feature weights; cross-cutting features form their own group and count in overall only",
        "overall": round(_mean(list(raw.values())), 1),
        "milestones": ms,
        "features": rows,
    }


def render_table(report: Dict[str, Any]) -> str:
    lines = [f"overall {report['overall']:.1f}% (commit {report['commit']})"]
    for m in report["milestones"]:
        kind = "" if m.get("kind") == KIND_MILESTONE else f" [{m.get('kind')}]"
        lines.append(f"  {m['id']:<4} {m['percent']:5.1f}%  {m['name']}{kind} ({len(m['features'])} features)")
    lines.append("")
    for f in report["features"]:
        idle = f" (idle since {f['lastCommit'][:10]})" if f.get("idle") else ""
        lines.append(f"  {f['id']:<6} {f['percent']:5.1f}%  {f['stage']:<14} {f['name']}{idle}")
    return "\n".join(lines) + "\n"


def render_json(report: Dict[str, Any]) -> str:
    return json.dumps(report, indent=2, ensure_ascii=False) + "\n"
