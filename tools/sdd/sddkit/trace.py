"""``sdd trace``: requirement -> implementation -> test traceability matrix.

Sources of truth (all configurable in ``sdd.config.json``):

* **Requirements** are lines in ``<features_dir>/*/spec.md``::

      - **<ID>** [must|should|may] <text>

  ``<ID>`` matches ``requirements.id_regex``. A requirement line may carry
  ``[behaviour-only]`` (no implementation site needed, e.g. a "SHALL NOT log"
  property verified only by tests) or ``(superseded by <ID>)``.
* **Feature status** is the header line ``Status: Draft|Approved|Implemented``.
* **Implementation sites** are tags in source files matched by
  ``trace.scan_globs``: a comment line ``Implements: ID, ID`` (any comment
  syntax: ``//``, ``///``, ``#``, ``*``, ``--``, ``;``) whose ID list may wrap
  onto following comment lines, or an attribute/decorator/call form
  ``implements("ID", "ID")`` (Rust ``#[trace::implements("..")]``, Python
  ``@implements("..")``, TypeScript ``implements("..")``, ...).
* **Verification sites** are the same with ``Verifies``.

``--check`` fails when
1. a tag references an ID that no spec defines (orphan or typo);
2. a requirement ID is defined twice (IDs are global across features);
3. a ``must`` requirement of a feature whose status is in
   ``requirements.enforce_for_statuses`` has no verifying test;
4. a ``must`` requirement of a feature whose status is in
   ``requirements.require_impl_for_statuses`` has no implementation site and
   is not ``[behaviour-only]``;
5. ``(superseded by <ID>)`` names an unknown ID;
6. with ``trace.procedure_steps.enabled``, a procedure-step defect (see ``procedures``).

A superseded requirement is exempt from rules 3 and 4.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import procedures
from .util import any_match, glob_match, strip_html_comments

# A comment line in any of the supported syntaxes, or the first line of a Python docstring
# (``"""Implements: ID"""``). ``body`` is the text without the opener/closer.
COMMENT_PREFIX = re.compile(r"^[ \t]*(?P<open>//[/!]?|#+|\*|--|;+|<!--|[rRuU]?\"\"\"|[rRuU]?''')[ \t]*(?P<body>.*?)(?:-->|\"\"\"|''')?[ \t]*$")
DOCSTRING_OPEN = re.compile(r"^[rRuU]?(?:\"\"\"|''')$")
NEXT_ITEM = re.compile(
    r"\b(?:fn|def|func|function|class|struct|enum|trait|impl|mod|interface|type|const|static|let|var|val|"
    r"describe|it|test|proc|sub|method)\b\s*(?:<[^>{]*>\s*)?(?P<name>[A-Za-z_][A-Za-z0-9_:.]*)"
)
STATUS_LINE = re.compile(r"(?mi)^[\s>*_-]*status[*_]*\s*:\s*[*_`]*\s*(?P<status>[A-Za-z][A-Za-z-]*)")
BEHAVIOUR_ONLY = re.compile(r"(?i)[\[(]behaviou?r-only[\])]")


@dataclass
class Requirement:
    id: str
    priority: str
    text: str
    feature: str  # directory name, e.g. "012-session-timeout"
    file: str
    line: int
    status: str
    superseded_by: Optional[str] = None
    behaviour_only: bool = False

    @property
    def number(self) -> str:
        return self.feature.split("-", 1)[0]


@dataclass
class Tag:
    id: str
    kind: str  # "implements" | "verifies"
    file: str
    line: int
    item: Optional[str] = None

    @property
    def site(self) -> str:
        return f"{self.file}:{self.line}" + (f" ({self.item})" if self.item else "")


@dataclass
class Matrix:
    requirements: Dict[str, Requirement] = field(default_factory=dict)
    duplicates: List[Tuple[str, str, str]] = field(default_factory=list)  # id, first site, second site
    tags: List[Tag] = field(default_factory=list)
    features: Dict[str, str] = field(default_factory=dict)  # feature dir -> status
    malformed: List[str] = field(default_factory=list)
    outside_scan: List[str] = field(default_factory=list)  # --include files that scan_globs does not cover

    def sites(self, req_id: str, kind: str) -> List[Tag]:
        return [t for t in self.tags if t.id == req_id and t.kind == kind]


# ----------------------------------------------------------------------------
# Spec parsing
# ----------------------------------------------------------------------------


def spec_status(text: str) -> str:
    m = STATUS_LINE.search(strip_html_comments(text))
    return m.group("status") if m else "Draft"


def parse_spec(text: str, id_regex: str, priorities: Iterable[str], feature: str, file: str, status: str) -> Tuple[List[Requirement], List[str]]:
    """Requirement lines of one spec.md (HTML comments are ignored)."""
    prio = "|".join(re.escape(p) for p in priorities)
    req_line = re.compile(rf"^- \*\*(?P<id>{id_regex})\*\* \[(?P<prio>{prio})\]\s*(?P<text>.*)$")
    req_like = re.compile(r"^- \*\*[^*\s]+\*\*")
    superseded = re.compile(rf"\(superseded by (?P<id>{id_regex})\)")
    reqs: List[Requirement] = []
    malformed: List[str] = []
    in_comment = False
    for idx, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip()
        if in_comment:
            if "-->" in line:
                in_comment = False
            continue
        if "<!--" in line and "-->" not in line:
            in_comment = True
            continue
        if "<!--" in line:
            line = re.sub(r"<!--.*?-->", "", line)
        m = req_line.match(line)
        if m:
            sup = superseded.search(m.group("text"))
            reqs.append(
                Requirement(
                    id=m.group("id"),
                    priority=m.group("prio"),
                    text=m.group("text").strip(),
                    feature=feature,
                    file=file,
                    line=idx,
                    status=status,
                    superseded_by=sup.group("id") if sup else None,
                    behaviour_only=bool(BEHAVIOUR_ONLY.search(m.group("text"))),
                )
            )
        elif req_like.match(line) and "<" not in line.split("**")[1] and re.search(rf"\[(?:{prio})\]", line):
            # Template placeholders (**<ID>**) are not malformed lines.
            malformed.append(f"{file}:{idx}: looks like a requirement line but does not match the exact format")
    return reqs, malformed


# ----------------------------------------------------------------------------
# Source scanning
# ----------------------------------------------------------------------------


def _id_list(body: str, id_re: re.Pattern) -> Optional[List[str]]:
    """IDs in a comma/space separated list; None if anything else is in it."""
    items = [x for x in re.split(r"[,\s]+", body.strip()) if x]
    if not items:
        return []
    ids = []
    for item in items:
        item = item.strip("`'\"()[]{}.;")
        if not item:
            continue
        if not id_re.fullmatch(item):
            return None
        ids.append(item)
    return ids


def _next_item(lines: List[str], start: int) -> Optional[str]:
    for line in lines[start : start + 12]:
        if COMMENT_PREFIX.match(line) and not line.strip().startswith("@"):
            # Still in a comment block (or attribute); keep looking.
            if not re.match(r"^\s*(?:#\[|@)", line):
                continue
        m = NEXT_ITEM.search(line)
        if m:
            return m.group("name")
    return None


def _prev_item(lines: List[str], start: int) -> Optional[str]:
    """The item a docstring belongs to: the nearest ``def``/``class`` line above it."""
    for line in reversed(lines[max(0, start - 4) : start]):
        m = NEXT_ITEM.search(line)
        if m:
            return m.group("name")
    return None


def scan_source(text: str, file: str, cfg: Dict[str, Any]) -> List[Tag]:
    id_re = re.compile(cfg["requirements"]["id_regex"])
    keywords = {}
    for k in cfg["trace"]["implements_keywords"]:
        keywords[k.lower()] = "implements"
    for k in cfg["trace"]["verifies_keywords"]:
        keywords[k.lower()] = "verifies"
    kw_alt = "|".join(re.escape(k) for k in sorted(keywords, key=len, reverse=True))
    doc_tag = re.compile(rf"(?i)\b(?P<kw>{kw_alt})\s*:\s*(?P<rest>.*)$")
    attr_open = re.compile(rf"(?i)\b(?P<kw>{kw_alt})\s*\(")
    lines = text.splitlines()
    tags: List[Tag] = []
    seen_attr_spans: set = set()
    i = 0
    while i < len(lines):
        line = lines[i]
        cm = COMMENT_PREFIX.match(line)
        if cm:
            dm = doc_tag.search(cm.group("body"))
            if dm:
                kind = keywords[dm.group("kw").lower()]
                body = dm.group("rest")
                j = i + 1
                # Wrapped ID lists on following comment lines made only of IDs.
                while j < len(lines):
                    nm = COMMENT_PREFIX.match(lines[j])
                    if not nm or not nm.group("body").strip():
                        break
                    if _id_list(nm.group("body"), id_re) in (None, []):
                        break
                    body += " " + nm.group("body")
                    j += 1
                ids = _id_list(body, id_re)
                if ids:
                    item = _prev_item(lines, i) if DOCSTRING_OPEN.match(cm.group("open")) else _next_item(lines, j)
                    for rid in ids:
                        tags.append(Tag(rid, kind, file, i + 1, item))
                    i = j
                    continue
        if cfg["trace"].get("attribute_forms", True):
            for am in attr_open.finditer(line):
                kind = keywords[am.group("kw").lower()]
                # Collect the argument text up to the matching ')' (may span lines).
                depth, k, buf = 0, i, ""
                pos = am.end() - 1
                done = False
                while k < len(lines) and not done:
                    seg = lines[k][pos:] if k == i else lines[k]
                    for ch in seg:
                        buf += ch
                        if ch == "(":
                            depth += 1
                        elif ch == ")":
                            depth -= 1
                            if depth == 0:
                                done = True
                                break
                    k += 1
                    pos = 0
                if not done:
                    continue
                strings = re.findall(r"[\"'`]([^\"'`]+)[\"'`]", buf)
                ids = [s for s in strings if id_re.fullmatch(s)]
                if not ids or len(ids) != len(strings):
                    continue
                key = (i, am.start())
                if key in seen_attr_spans:
                    continue
                seen_attr_spans.add(key)
                item = _next_item(lines, k)
                for rid in ids:
                    tags.append(Tag(rid, kind, file, i + 1, item))
        i += 1
    return tags


def iter_source_files(root: Path, cfg: Dict[str, Any]) -> List[Path]:
    includes = cfg["trace"]["scan_globs"]
    excludes = cfg["trace"]["exclude_globs"]
    out: List[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if any_match(rel, excludes):
            continue
        if not any_match(rel, includes):
            continue
        out.append(path)
    return out


# ----------------------------------------------------------------------------
# Matrix
# ----------------------------------------------------------------------------


def load(root: Path, cfg: Dict[str, Any], include: Optional[Iterable[str]] = None) -> Matrix:
    """Parse every spec and scan the configured sources.

    ``include`` (``sdd trace --include``) adds files whatever ``trace.scan_globs`` says; those
    that the globs do not cover are listed in ``outside_scan`` (a configuration gap to close).
    """
    m = Matrix()
    features_dir = root / cfg["paths"]["features_dir"]
    id_regex = cfg["requirements"]["id_regex"]
    prios = cfg["requirements"]["priorities"]
    if features_dir.is_dir():
        for spec in sorted(features_dir.glob("*/spec.md")):
            feature = spec.parent.name
            text = spec.read_text(encoding="utf-8", errors="replace")
            status = spec_status(text)
            m.features[feature] = status
            rel = spec.relative_to(root).as_posix()
            reqs, bad = parse_spec(text, id_regex, prios, feature, rel, status)
            m.malformed.extend(bad)
            for r in reqs:
                if r.id in m.requirements:
                    first = m.requirements[r.id]
                    m.duplicates.append((r.id, f"{first.file}:{first.line}", f"{r.file}:{r.line}"))
                    continue
                m.requirements[r.id] = r
    files = iter_source_files(root, cfg)
    scanned = {p.resolve() for p in files}
    for item in include or []:
        path = (root / item).resolve()
        if path.is_file() and path not in scanned:
            scanned.add(path)
            files.append(path)
            m.outside_scan.append(_rel(root, path))
    for path in files:
        rel = _rel(root, path)
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if not re.search(r"(?i)implements|verifies", text):
            continue
        m.tags.extend(scan_source(text, rel, cfg))
    return m


def _rel(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def select(m: Matrix, feature: Optional[str]) -> List[Requirement]:
    reqs = list(m.requirements.values())
    if feature:
        f = feature.lstrip("Ff")
        reqs = [r for r in reqs if r.number == f.zfill(len(r.number)) or r.feature == feature or r.number == f]
    return sorted(reqs, key=lambda r: (r.feature, r.line))


def check(m: Matrix, cfg: Dict[str, Any], feature: Optional[str] = None) -> List[str]:
    problems: List[str] = []
    enforce = set(cfg["requirements"]["enforce_for_statuses"])
    require_impl = set(cfg["requirements"]["require_impl_for_statuses"])
    must = cfg["requirements"]["priorities"][0]
    for rid, a, b in m.duplicates:
        problems.append(f"duplicate requirement id {rid}: {a} and {b} (IDs are global; give each feature its own block)")
    problems.extend(m.malformed)
    known = set(m.requirements)
    for t in m.tags:
        if t.id not in known:
            problems.append(f"{t.site}: {t.kind} tag references unknown requirement {t.id}")
    for r in select(m, feature):
        if r.superseded_by and r.superseded_by not in known:
            problems.append(f"{r.file}:{r.line}: {r.id} is superseded by unknown id {r.superseded_by}")
        if r.superseded_by or r.priority != must:
            continue
        if r.status in enforce and not m.sites(r.id, "verifies"):
            problems.append(f"{r.id} ({r.feature}, {r.status}): must requirement has no verifying test")
        if r.status in require_impl and not r.behaviour_only and not m.sites(r.id, "implements"):
            problems.append(f"{r.id} ({r.feature}, {r.status}): must requirement has no implementation site")
    return problems


def status_of(m: Matrix, r: Requirement) -> str:
    if r.superseded_by:
        return f"superseded by {r.superseded_by}"
    impl = bool(m.sites(r.id, "implements")) or r.behaviour_only
    test = bool(m.sites(r.id, "verifies"))
    if impl and test:
        return "implemented+tested"
    if test:
        return "tested, not implemented"
    if impl:
        return "implemented, untested"
    return "uncovered"


def to_markdown(m: Matrix, feature: Optional[str] = None) -> str:
    reqs = select(m, feature)
    lines = ["| Requirement | Prio | Feature | Status | Implementation | Tests |", "|---|---|---|---|---|---|"]
    for r in reqs:
        impl = "; ".join(t.site for t in m.sites(r.id, "implements")) or ("(behaviour-only)" if r.behaviour_only else "-")
        tests = "; ".join(t.site for t in m.sites(r.id, "verifies")) or "-"
        lines.append(f"| {r.id} | {r.priority} | {r.feature} ({r.status}) | {status_of(m, r)} | {impl} | {tests} |")
    total = len(reqs)
    musts = [r for r in reqs if r.priority == "must" and not r.superseded_by]
    covered = [r for r in musts if status_of(m, r) == "implemented+tested"]
    lines.append("")
    lines.append(f"{total} requirement(s); must: {len(musts)}, implemented+tested: {len(covered)}")
    return "\n".join(lines) + "\n"


def to_json(m: Matrix, feature: Optional[str] = None) -> Dict[str, Any]:
    return {
        "features": m.features,
        "outsideScan": list(m.outside_scan),
        "requirements": [
            {
                "id": r.id,
                "priority": r.priority,
                "feature": r.feature,
                "featureStatus": r.status,
                "status": status_of(m, r),
                "behaviourOnly": r.behaviour_only,
                "supersededBy": r.superseded_by,
                "implements": [t.site for t in m.sites(r.id, "implements")],
                "verifies": [t.site for t in m.sites(r.id, "verifies")],
                "text": r.text,
            }
            for r in select(m, feature)
        ],
    }


def run(root: Path, cfg: Dict[str, Any], feature: Optional[str], do_check: bool, as_json: bool, allow_missing_impl: bool = False, include: Optional[List[str]] = None) -> int:
    m = load(root, cfg, include)
    if allow_missing_impl:
        cfg = json.loads(json.dumps(cfg))
        cfg["requirements"]["require_impl_for_statuses"] = []
    steps = procedures.evaluate(root, cfg, m, feature) if procedures.enabled(cfg) else None
    if as_json:
        out = to_json(m, feature)
        problems = check(m, cfg, feature) + (steps.errors if steps else []) if do_check else []
        if steps:
            out["procedures"] = steps.coverage
        if do_check:
            out["problems"] = problems
            if steps:
                out["warnings"] = steps.warnings
        print(json.dumps(out, indent=2))
        return 1 if problems else 0
    print(to_markdown(m, feature), end="")
    if steps:
        print(procedures.to_markdown(steps.coverage), end="")
    if m.outside_scan:
        print(f"outside trace.scan_globs (found only via --include): {', '.join(m.outside_scan)}")
    if do_check:
        problems = check(m, cfg, feature) + (steps.errors if steps else [])
        for w in steps.warnings if steps else []:
            print(f"warning: {w}")
        for p in problems:
            print(f"error: {p}")
        if problems:
            print(f"trace --check: {len(problems)} problem(s)")
            return 1
        print(f"trace --check: OK ({len(m.features)} feature(s), {len(m.requirements)} requirement(s))")
    return 0
