"""``sdd progress``: lifecycle-stage completion per feature, milestone and overall.

Derived from the roadmap and git refs only (nothing is checked out):

=================  =======  ==========================================================
stage              percent  evidence
=================  =======  ==========================================================
not-started            0.0  no ``<features_dir>/<nnn>-*/spec.md`` anywhere
spec-draft             5.0  spec present, ``Status: Draft`` (or unknown status)
spec-approved         15.0  ``Status: Approved``
planned                20.0  ``design.md`` and ``tasks.md`` hold a written plan (not the
                            unfilled ``sdd new-feature`` scaffold, see below) committed
                            after the spec's latest ``Status:`` change (a plan made before
                            a re-specification or amendment is stale, unless a
                            ``test(F<NNN>)`` commit followed the re-approval)
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

Scaffold plans: ``sdd new-feature`` copies ``paths.templates_dir`` into every new feature
directory, so a feature holds ``design.md`` and ``tasks.md`` long before it is planned. The
markers come from the project's own templates (read at the main ref, else from the working
tree): a design is still the scaffold while its H1 keeps a ``<placeholder>`` of the template's
H1 (``<Feature title>``) or its text keeps the template's first placeholder paragraph line (the
overview); tasks are planned only with at least one real task line (template example lines and
``<placeholder>`` lines do not count). Without templates only a missing file is unplanned.

Idle features: a feature in a started stage (planned, tests, implementing, implemented) with a
branch also gets ``lastCommit`` (the newest commit on its unmerged branches, UTC; on all its
branches when every one is merged, as for a feature that merges in parts) and ``idle`` (no such
commit for 24 hours before ``generatedAt``), so a paused feature is never shown as moving; the
table marks it ``(idle since <date>)``.

Paused features: a feature the orchestrator deliberately holds is listed in ``paths.paused`` (default
``tools/progress/paused.json``), a JSON object mapping a feature id to a one-line public reason, and
its row gets ``paused: true`` and ``pausedReason``. Paused is a status beside the stage: ``stage``,
``percent``, ``idle`` and ``lastCommit`` are unchanged (a site shows Paused over Idle). The file is
read from the main ref only, never from the working tree or a feature branch, so a branch cannot
pause (or unpause) itself; an absent file pauses nothing. It is validated strictly and an invalid
file fails the run rather than being skipped: a JSON object without duplicate keys, every key a
roadmap feature id, every value a string of 1 to 160 characters without control characters, and
no e-mail address, URL or absolute filesystem path (reasons are published; the rest is on review).
The orchestrator lists a feature when it holds it on purpose and removes the entry when work resumes.

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
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

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
# A ``<placeholder>`` token (``<Feature title>``) and the template variables ``sdd new-feature``
# substitutes (so a scaffold no longer holds them and they cannot mark one).
_TOKEN = re.compile(r"<[^<>\n]+>")
_SUBSTITUTED = ("<NNN>", "<slug>", "{{")
PAUSED_REASON_MAX = 160
# Text a published progress feed must not carry; rejected mechanically in a pause reason.
_PAUSED_LEAKS = (
    ("an e-mail address", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z0-9.-]*[A-Za-z]")),
    ("a URL", re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://|\bwww\.", re.IGNORECASE)),
    ("a filesystem path", re.compile(r"(?<![\w.:/])(?:/home/|/Users/|/mnt/|/tmp/|/var/|/etc/|/root/|[A-Za-z]:\\)")),
)
# git -G (POSIX ERE, case-insensitive with -i) for a spec's Status line, as trace.STATUS_LINE reads it.
_STATUS_LINE_ERE = r"^[[:space:]>*_-]*status[*_]*[[:space:]]*:"
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


def _reject_duplicate_keys(pairs: List[Tuple[str, object]]) -> Dict[str, object]:
    obj: Dict[str, object] = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"duplicate key {key!r}")
        obj[key] = value
    return obj


def parse_paused(text: Optional[str], feature_ids: Set[str], path: str = "paused.json") -> Dict[str, str]:
    """``{feature id: reason}`` from the text of the paused file (None: absent, nothing is paused).
    Raises ValueError naming ``path`` and the offending entry when the file or an entry is invalid
    (rules in the module docstring); nothing is skipped silently."""
    if text is None:
        return {}
    try:
        data = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except ValueError as exc:  # json.JSONDecodeError is a ValueError
        raise ValueError(f"{path}: not valid JSON: {exc}") from None
    if not isinstance(data, dict):
        raise ValueError(f"{path}: must be a JSON object mapping feature ids to reasons")
    paused: Dict[str, str] = {}
    for fid in sorted(data):
        reason = data[fid]
        if fid not in feature_ids:
            raise ValueError(f"{path}: {fid!r} is not a feature in the roadmap")
        if not isinstance(reason, str):
            raise ValueError(f"{path}: {fid}: reason must be a string, got {type(reason).__name__}")
        if not reason.strip():
            raise ValueError(f"{path}: {fid}: reason must not be empty")
        if len(reason) > PAUSED_REASON_MAX:
            raise ValueError(f"{path}: {fid}: reason has {len(reason)} characters, at most {PAUSED_REASON_MAX} allowed")
        bad = next((ch for ch in reason if unicodedata.category(ch) in ("Cc", "Zl", "Zp")), None)
        if bad is not None:
            raise ValueError(f"{path}: {fid}: reason must be one line without control characters (found U+{ord(bad):04X})")
        for what, pattern in _PAUSED_LEAKS:
            if pattern.search(reason):
                raise ValueError(f"{path}: {fid}: reason is public and must not contain {what}")
        paused[fid] = reason
    return paused


def count_tasks(text: Optional[str], template_lines: frozenset = frozenset()) -> Tuple[int, int]:
    """``(done, total)`` task checkboxes in a tasks.md. Lines inside ``<!-- -->`` comments,
    ``<placeholder>`` lines and the tasks template's own task lines (``template_lines``) are not tasks."""
    if text is None:
        return (0, 0)
    done = total = 0
    for line in strip_html_comments(text).splitlines():
        if is_placeholder_line(line) or line.strip() in template_lines:
            continue
        m = _TASK_LINE.match(line)
        if not m:
            continue
        total += 1
        if m.group(1) in "xX":
            done += 1
    return (done, total)


class Scaffold:
    """What an unfilled ``sdd new-feature`` scaffold keeps, read from the project's templates."""

    def __init__(self, design_template: Optional[str] = None, tasks_template: Optional[str] = None) -> None:
        self.title_tokens: Tuple[str, ...] = ()
        self.body_marker: Optional[str] = None
        if design_template:
            fenced = seen_h1 = False
            for line in strip_html_comments(design_template).splitlines():
                stripped = line.strip()
                if stripped.startswith("```"):
                    fenced = not fenced
                    continue
                if fenced:
                    continue
                if not seen_h1 and line.startswith("# "):
                    seen_h1 = True
                    self.title_tokens = tuple(t for t in _TOKEN.findall(line) if not any(s in t for s in _SUBSTITUTED))
                elif seen_h1 and stripped.startswith("<") and not any(s in stripped for s in _SUBSTITUTED):
                    self.body_marker = stripped
                    break
        self.task_lines = frozenset(
            line.strip() for line in strip_html_comments(tasks_template or "").splitlines() if _TASK_LINE.match(line)
        )

    def is_scaffold_design(self, text: Optional[str]) -> bool:
        """True for a design.md that is absent or still the unfilled template."""
        if text is None:
            return True
        body = strip_html_comments(text)
        h1 = next((line for line in body.splitlines() if line.startswith("# ")), "")
        if any(token in h1 for token in self.title_tokens):
            return True
        return bool(self.body_marker) and self.body_marker in body

    def is_plan(self, design: Optional[str], tasks: Optional[str]) -> bool:
        """True when design.md and tasks.md hold a written plan, not the new-feature scaffold."""
        return not self.is_scaffold_design(design) and count_tasks(tasks, self.task_lines)[1] > 0


class Repo:
    def __init__(self, root: Path, cfg: Dict[str, Any], remote: str) -> None:
        self.git = Git(root)
        self.cfg = cfg
        self.remote = remote
        self.features_dir = cfg["paths"]["features_dir"]
        self.prefix = cfg["features"]["branch_prefix"]
        self.num_rx = re.compile(r"^(\d{%d,})-" % int(cfg["features"]["id_digits"]))
        self.scaffold = Scaffold()

    def load_templates(self, ref: str, root: Path) -> None:
        """Read the design and tasks templates at ``ref``, else from the working tree."""
        texts = []
        for name in ("design.md", "tasks.md"):
            rel = f"{self.cfg['paths']['templates_dir'].rstrip('/')}/{name}"
            text = self.git.show(ref, rel)
            if text is None and (root / rel).is_file():
                text = (root / rel).read_text(encoding="utf-8", errors="replace")
            texts.append(text)
        self.scaffold = Scaffold(*texts)

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

    def last_change(self, ref: str, paths: List[str], regex: Optional[str] = None) -> Optional[str]:
        """Newest commit on ``ref`` that changed ``paths`` (only lines matching ``regex``, a
        case-insensitive POSIX ERE, when given), or None."""
        extra = ["-i", f"-G{regex}"] if regex else []
        out = self.git.run("log", "-1", "--format=%H", *extra, ref, "--", *paths, check=False).strip()
        return out or None

    def plan_is_stale(self, fid: str, ref: str, fdir: str) -> bool:
        """True when the spec's ``Status:`` line changed after design.md/tasks.md were last
        committed (re-specified or amended through Draft and re-approved since the plan) and no
        ``test(F<NNN>)`` commit since then shows the pipeline went on with that plan. Spec edits
        that keep the Status line do not make a plan stale."""
        status = self.last_change(ref, [f"{fdir}/spec.md"], _STATUS_LINE_ERE)
        if status is None:
            return False
        plan = self.last_change(ref, [f"{fdir}/design.md", f"{fdir}/tasks.md"])
        if plan is None or self.git.is_ancestor(status, plan):
            return False
        return not self.has_test_commit(fid, status, ref)


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
    if not repo.scaffold.is_plan(design, tasks) or repo.plan_is_stale(fid, ref, fdir):
        return stage, STAGES[stage]
    stage = "planned"
    if ref != main_ref and repo.has_test_commit(fid, main_ref, ref):
        stage = "tests"
    done, total = count_tasks(tasks, repo.scaffold.task_lines)
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


def last_activity(git: Git, main_ref: str, branches: List[str]) -> Optional[int]:
    """Newest commit time on the feature's unmerged branches, else on all its branches (a feature
    that merges in parts has every branch merged while it is still in progress), or None when it
    has no branch."""
    refs = [ref for ref in branches if not git.is_ancestor(ref, main_ref)] or branches
    return max((git.commit_time(ref) for ref in refs), default=0) or None


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
    paused_path = cfg["paths"].get("paused") or ""
    # From the main ref only (never the working tree or a feature branch): a branch cannot pause itself.
    paused = parse_paused(repo.git.show(resolved, paused_path) if paused_path else None, {f["id"] for f in features}, paused_path)
    main_dirs = repo.feature_dirs(resolved)
    repo.load_templates(resolved, root)
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
            last = last_activity(repo.git, resolved, fbranches)
            if last:
                rows[-1]["lastCommit"] = _dt.datetime.fromtimestamp(last, tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                rows[-1]["idle"] = now_ts - last > IDLE_AFTER_SECONDS
        if f["id"] in paused:
            rows[-1]["paused"] = True
            rows[-1]["pausedReason"] = paused[f["id"]]
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
        if f.get("paused"):
            idle += f" (paused: {f['pausedReason']})"
        lines.append(f"  {f['id']:<6} {f['percent']:5.1f}%  {f['stage']:<14} {f['name']}{idle}")
    return "\n".join(lines) + "\n"


def render_json(report: Dict[str, Any]) -> str:
    return json.dumps(report, indent=2, ensure_ascii=False) + "\n"
