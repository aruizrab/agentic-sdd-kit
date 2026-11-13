"""Procedure step tables: map the steps of a normative procedure to requirements and owners.

Opt-in with ``trace.procedure_steps.enabled`` (default off). A normative source that
describes architecture-level procedures (call flows, sequence diagrams, numbered steps) can
then be traced step by step. Every structure is recognised only outside fenced code blocks
and HTML comments:

* **Step table**: a heading of level 2-4 ``Step table: <doc> §<section> [title]`` followed,
  before the next heading, by a table with the header ``| Step | Description | Mapping |``
  and one row per step label of that section.
* **Responsibilities table**: a heading of level 2-4 ``Responsibilities`` (optionally
  followed by ``(<text>)``) with the header
  ``| Owner | Responsibility | Source | Requirements |``; required whenever the spec has a
  step table.
* **Exemption line**: ``No step table for <doc> §<section>: <reason>``.
* **Feature-wide reason**: ``No step table: <reason>`` (checked, none applies).

A Mapping or Requirements cell is **accounted for** when it holds at least one requirement
id that some spec defines, or a disposition ``out of scope: <reason>`` or
``owned by <FID>: <reason>`` (another roadmap feature); parts are separated by ``;``.

``sdd trace --check`` fails, for a spec whose status is in ``procedure_steps.statuses``
(default Approved, Implemented), on: a malformed step table (no table after the heading, a
wrong header, a duplicate step, a second table for the same procedure); a row whose cell is
not accounted for; an unknown requirement id; ``owned by`` naming the spec's own feature or
a feature without a roadmap entry; an empty reason; a step table without a Responsibilities
table; and a spec with no entry at all (no step table, exemption line or feature-wide
reason), so that an absent section is distinguishable from "checked, none applies". With
``procedure_steps.source_check`` (default on) the step labels of the section's own text
(read through ``sdd source``, labels found by ``procedure_steps.label_regex``) must match
the table's rows both ways, and each Responsibility must be quoted from its cited section.
Specs in other statuses get the same findings as warnings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import source

# Step labels at the start of a line of a section's own text, optionally after "Step ":
# a number with an optional letter (and digits), "(A)" suffix or dotted sub-step, an
# optional range ("14a-c", "4a - 4b", "14.-15.", "2a to 2c", "1a and 1b"), then a full stop,
# a closing parenthesis, a colon, white space or the end of the line.
DEFAULT_LABEL_REGEX = (
    r"^(?:Step\s+)?(?P<label>\d+(?:[a-z]\d*|\([A-Z]\)|\.\d+(?=\\?\.))?"
    r"(?:(?:\\?\.)?\s*-\s*(?:\d+[a-z]?\d*|[a-z])(?![\w(])|\s+(?:to|and)\s+\d+[a-z]?\d*)?)"
    r"(?:\\?[.)]|:|\s|$)"
)

HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
STEP_HEADING = re.compile(r"^Step table:\s*(?P<rest>.*)$")
RESP_HEADING = re.compile(r"^Responsibilities(?:\s+\([^()]*\))?$")
LOOKALIKE_HEADING = re.compile(r"(?i)^(?:step[ -]?table|responsibilities)\b")
PROC_REF = re.compile(r"^(?P<doc>.+?)\s+§\s*(?P<section>[0-9A-Za-z][0-9A-Za-z.]*?)\.?(?:\s+(?P<title>.*))?$")
EXEMPTION = re.compile(r"^(?:[-*+]\s+)?No step table for\s+(?P<proc>[^:]+):(?P<reason>.*)$")
FEATURE_WIDE = re.compile(r"^(?:[-*+]\s+)?No step table:(?P<reason>.*)$")
LOOKALIKE_LINE = re.compile(r"(?i)^(?:[-*+]\s+)?[*_`]*no step[ -]?table")
STEP_HEADER = ["step", "description", "mapping"]
RESP_HEADER = ["owner", "responsibility", "source", "requirements"]
SEPARATOR_CELL = re.compile(r"^:?-+:?$")
OUT_OF_SCOPE = re.compile(r"(?i)^out of scope\s*(?::(?P<reason>.*))?$")
OWNED_BY = re.compile(r"(?i)^owned by\s+(?P<feature>[^\s:]+)\s*(?::(?P<reason>.*))?$")


def settings(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return (cfg.get("trace") or {}).get("procedure_steps") or {}


def enabled(cfg: Dict[str, Any]) -> bool:
    return bool(settings(cfg).get("enabled"))


def canonical(label: str) -> str:
    """``2a. - 2c`` and ``2a to 2c`` -> ``2a-2c``; backslashes and spaces are dropped."""
    s = label.replace("\\", "").strip().strip("`")
    s = re.sub(r"\.?\s*(?:-|\bto\b|\band\b)\s*", "-", s)
    return re.sub(r"\s+", "", s)


def proc_key(text: str) -> Tuple[str, Optional[str], Optional[str], str]:
    """(key, doc, section, title) of a procedure reference such as ``PROTO §4.2 Login``."""
    text = re.sub(r"\s+", " ", text.strip())
    m = PROC_REF.match(text)
    if not m:
        return text, None, None, ""
    doc, sec = m.group("doc").strip(), m.group("section").rstrip(".")
    return f"{doc} §{sec}", doc, sec, (m.group("title") or "").strip()


# ----------------------------------------------------------------------------
# Parsing a spec.md
# ----------------------------------------------------------------------------


@dataclass
class Row:
    label: str
    description: str
    mapping: str
    line: int


@dataclass
class StepTable:
    key: str
    doc: Optional[str]
    section: Optional[str]
    title: str
    line: int
    rows: List[Row] = field(default_factory=list)
    defects: List[str] = field(default_factory=list)


@dataclass
class RespRow:
    owner: str
    responsibility: str
    source: str
    requirements: str
    line: int


@dataclass
class Parsed:
    tables: List[StepTable] = field(default_factory=list)
    resp_headings: int = 0
    responsibilities: List[RespRow] = field(default_factory=list)
    defects: List[Tuple[int, str]] = field(default_factory=list)  # Responsibilities table defects
    exemptions: List[Tuple[str, str, int]] = field(default_factory=list)  # key, reason, line
    reasons: List[Tuple[str, int]] = field(default_factory=list)  # feature-wide reasons
    lookalikes: List[Tuple[int, str]] = field(default_factory=list)

    def has_entry(self) -> bool:
        return bool(self.tables or self.exemptions or self.reasons)


def visible_lines(text: str) -> List[str]:
    """The spec's lines with fenced code blocks and HTML comments blanked (line numbers kept)."""
    out: List[str] = []
    fence: Optional[str] = None
    in_comment = False
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.lstrip()
        if fence:
            out.append("")
            if stripped.startswith(fence):
                fence = None
            continue
        if in_comment:
            out.append("")
            if "-->" in line:
                in_comment = False
            continue
        if stripped.startswith("```") or stripped.startswith("~~~"):
            fence = stripped[:3]
            out.append("")
            continue
        line = re.sub(r"<!--.*?-->", "", line)
        if "<!--" in line:
            in_comment = True
            line = line.split("<!--", 1)[0]
        out.append(line)
    return out


def split_cells(line: str) -> List[str]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", body)]


def _table_after(lines: List[str], start: int) -> Tuple[Optional[List[str]], List[Tuple[int, List[str]]], int]:
    """The first table between ``start`` and the next heading: (header, rows, index after it)."""
    i = start
    while i < len(lines) and not HEADING.match(lines[i]):
        if lines[i].lstrip().startswith("|"):
            header = [c.lower() for c in split_cells(lines[i])]
            rows: List[Tuple[int, List[str]]] = []
            i += 1
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                cells = split_cells(lines[i])
                if not all(SEPARATOR_CELL.match(c) for c in cells):
                    rows.append((i + 1, cells))
                i += 1
            return header, rows, i
        i += 1
    return None, [], start


def parse(text: str) -> Parsed:
    lines = visible_lines(text)
    p = Parsed()
    seen: Dict[str, int] = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        hm = HEADING.match(line)
        if hm and 2 <= len(hm.group(1)) <= 4:
            title = hm.group(2)
            sm = STEP_HEADING.match(title)
            if sm:
                key, doc, sec, ptitle = proc_key(sm.group("rest"))
                table = StepTable(key, doc, sec, ptitle, i + 1)
                if not key:
                    table.defects.append("the step-table heading names no procedure")
                if key in seen:
                    table.defects.append(f"a second step table for {key} (the first is at line {seen[key]})")
                seen.setdefault(key, i + 1)
                header, rows, nxt = _table_after(lines, i + 1)
                if header is None:
                    table.defects.append("no table follows the step-table heading")
                elif header != STEP_HEADER:
                    table.defects.append("the table header is not | Step | Description | Mapping |")
                else:
                    labels: Dict[str, int] = {}
                    for ln, cells in rows:
                        if len(cells) != 3:
                            table.defects.append(f"line {ln}: a row needs 3 cells, has {len(cells)}")
                            continue
                        label = canonical(cells[0])
                        if label in labels:
                            table.defects.append(f"step {label} has two rows (lines {labels[label]} and {ln})")
                            continue
                        labels[label] = ln
                        table.rows.append(Row(label, cells[1], cells[2], ln))
                p.tables.append(table)
                i = max(nxt, i + 1)
                continue
            if RESP_HEADING.match(title):
                p.resp_headings += 1
                header, rows, nxt = _table_after(lines, i + 1)
                if header is None:
                    p.defects.append((i + 1, "no table follows the Responsibilities heading"))
                elif header != RESP_HEADER:
                    p.defects.append((i + 1, "the Responsibilities header is not | Owner | Responsibility | Source | Requirements |"))
                else:
                    for ln, cells in rows:
                        if len(cells) != 4:
                            p.defects.append((ln, f"a Responsibilities row needs 4 cells, has {len(cells)}"))
                        else:
                            p.responsibilities.append(RespRow(*cells, line=ln))
                i = max(nxt, i + 1)
                continue
            if LOOKALIKE_HEADING.match(title):
                p.lookalikes.append((i + 1, f"heading {title!r} looks like a step-table or Responsibilities heading but does not match the exact form"))
        else:
            stripped = line.strip()
            em = EXEMPTION.match(stripped)
            fm = FEATURE_WIDE.match(stripped)
            if em:
                p.exemptions.append((proc_key(em.group("proc"))[0], em.group("reason").strip(), i + 1))
            elif fm:
                p.reasons.append((fm.group("reason").strip(), i + 1))
            elif LOOKALIKE_LINE.match(stripped):
                p.lookalikes.append((i + 1, f"line {stripped[:60]!r} looks like an exemption line but does not match `No step table for <doc> §<section>: <reason>` or `No step table: <reason>`"))
        i += 1
    return p


# ----------------------------------------------------------------------------
# Cells, labels, source text
# ----------------------------------------------------------------------------


@dataclass
class Cell:
    ids: List[str]
    owned: List[Tuple[str, str]]  # feature, reason
    out_of_scope: List[str]  # reasons

    @property
    def empty(self) -> bool:
        return not (self.ids or self.owned or self.out_of_scope)


def parse_cell(cell: str, id_regex: str) -> Cell:
    id_rx = re.compile(rf"(?<![\w-])(?:{id_regex})(?![\w-])")
    out = Cell([], [], [])
    for part in (x.strip() for x in cell.split(";")):
        om = OUT_OF_SCOPE.match(part)
        wm = OWNED_BY.match(part)
        if om:
            out.out_of_scope.append((om.group("reason") or "").strip())
        elif wm:
            out.owned.append((wm.group("feature"), (wm.group("reason") or "").strip()))
        else:
            out.ids.extend(m.group(0) for m in id_rx.finditer(part))
    return out


def own_text(text: str, section: str, heading_regex: str) -> List[str]:
    """The section's own lines: after its heading up to the next heading (sub-sections excluded)."""
    lines = text.splitlines()
    rx = re.compile(heading_regex.replace("{clause}", re.escape(section)))
    start = next((i + 1 for i, line in enumerate(lines) if rx.match(line)), 0)
    out: List[str] = []
    for line in lines[start:]:
        if source.HEADING.match(line):
            if start or any(x.strip() for x in out):
                break
            continue
        out.append(line)
    return out


def labels_of(lines: List[str], label_regex: str) -> List[str]:
    rx = re.compile(label_regex)
    labels: List[str] = []
    for line in lines:
        m = rx.match(line)
        if m and m.group("label"):
            label = canonical(m.group("label"))
            if label not in labels:
                labels.append(label)
    return labels


def normalized(text: str) -> str:
    s = re.sub(r"\\(.)", r"\1", text)
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s[:-1] if s.endswith(".") else s


def roadmap_features(root: Path, cfg: Dict[str, Any]) -> List[str]:
    path = root / cfg["paths"]["roadmap"]
    if not path.is_file():
        return []
    rx = re.compile(cfg["features"]["roadmap_feature_regex"])
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = rx.match(line)
        if m:
            out.append(m.group(1))
    return out


def selected(name: str, feature: Optional[str]) -> bool:
    if not feature:
        return True
    number = name.split("-", 1)[0]
    f = feature.lstrip("Ff")
    return name == feature or number == f or number == f.zfill(len(number))


# ----------------------------------------------------------------------------
# Evaluation
# ----------------------------------------------------------------------------


@dataclass
class Result:
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    coverage: List[Dict[str, Any]] = field(default_factory=list)


class _Checker:
    def __init__(self, root: Path, cfg: Dict[str, Any], matrix: Any) -> None:
        ps = settings(cfg)
        self.root, self.cfg, self.m = root, cfg, matrix
        self.source_check = bool(ps.get("source_check", True))
        self.label_regex = ps.get("label_regex") or DEFAULT_LABEL_REGEX
        self.id_regex = cfg["requirements"]["id_regex"]
        self.must = cfg["requirements"]["priorities"][0]
        self.roadmap = set(roadmap_features(root, cfg))
        self._texts: Dict[Tuple[str, str], Optional[str]] = {}

    def text(self, doc: str, section: str) -> Optional[str]:
        key = (doc, section)
        if key not in self._texts:
            self._texts[key] = source.read_clause(self.cfg, self.root, doc, section)
        return self._texts[key]

    def cell(self, cell: Cell, fid: str, where: str, problems: List[Tuple[int, str]], line: int) -> None:
        for rid in cell.ids:
            if rid not in self.m.requirements:
                problems.append((line, f"{where}: unknown requirement {rid} (no spec defines it)"))
        for feat, reason in cell.owned:
            if feat == fid:
                problems.append((line, f"{where}: `owned by {feat}` names this spec's own feature"))
            elif feat not in self.roadmap:
                problems.append((line, f"{where}: `owned by {feat}` names a feature without a roadmap entry"))
            if not reason:
                problems.append((line, f"{where}: empty reason in `owned by {feat}`"))
        for reason in cell.out_of_scope:
            if not reason:
                problems.append((line, f"{where}: empty reason in `out of scope`"))

    def verified(self, cell: Cell) -> bool:
        for rid in cell.ids:
            r = self.m.requirements.get(rid)
            if r and r.priority == self.must and not r.superseded_by and not self.m.sites(rid, "verifies"):
                return False
        return True

    def spec(self, text: str, fid: str) -> Tuple[List[Tuple[int, str]], Parsed, List[Dict[str, Any]]]:
        p = parse(text)
        problems: List[Tuple[int, str]] = []
        tables: List[Dict[str, Any]] = []
        for t in p.tables:
            problems.extend((t.line, f"step table {t.key}: {d}") for d in t.defects)
            labels: Optional[List[str]] = None
            if self.source_check:
                if not t.doc:
                    problems.append((t.line, f"step table {t.key}: the heading does not name `<doc> §<section>`, so its steps cannot be read from the source"))
                else:
                    body = self.text(t.doc, t.section or "")
                    if body is None:
                        problems.append((t.line, f"step table {t.key}: section not found in the normative source (`sdd source clause {t.doc} {t.section}`)"))
                    else:
                        labels = labels_of(own_text(body, t.section or "", self.cfg["sources"]["heading_regex"]), self.label_regex)
                        if not labels:
                            problems.append((t.line, f"step table {t.key}: the section's own text has no step label"))
                            labels = None
            counts = {"procedure": t.key, "title": t.title, "steps": 0, "mapped": 0, "ownedByOtherFeatures": 0, "outOfScope": 0, "verified": 0}
            rows = {r.label: r for r in t.rows}
            for r in t.rows:
                where = f"step table {t.key} step {r.label}"
                if labels is not None and r.label not in labels:
                    problems.append((r.line, f"step table {t.key}: step {r.label} is not a step of the section"))
                    continue
                c = parse_cell(r.mapping, self.id_regex)
                self.cell(c, fid, where, problems, r.line)
                if c.empty:
                    problems.append((r.line, f"{where} is not mapped (cite a requirement id, `owned by <feature>: <reason>` or `out of scope: <reason>`)"))
                elif c.ids:
                    counts["mapped"] += 1
                    counts["verified"] += int(self.verified(c))
                elif c.owned:
                    counts["ownedByOtherFeatures"] += 1
                else:
                    counts["outOfScope"] += 1
            if labels is not None:
                for label in labels:
                    if label not in rows:
                        problems.append((t.line, f"step table {t.key}: step {label} has no row"))
                counts["steps"] = len(labels)
            else:
                counts["steps"] = len(t.rows)
            tables.append(counts)
        if p.tables and not p.resp_headings:
            problems.append((p.tables[0].line, "has a step table but no Responsibilities table"))
        problems.extend(p.defects)
        for r in p.responsibilities:
            where = f"Responsibilities {r.owner or '<no owner>'}"
            if not r.owner:
                problems.append((r.line, f"{where}: the Owner cell is empty"))
            c = parse_cell(r.requirements, self.id_regex)
            self.cell(c, fid, where, problems, r.line)
            if c.empty:
                problems.append((r.line, f"{where}: the Requirements cell is not accounted for"))
            if self.source_check:
                _key, doc, sec, rest = proc_key(r.source)
                if not doc or rest:
                    problems.append((r.line, f"{where}: Source {r.source!r} is not `<doc> §<section>`"))
                else:
                    body = self.text(doc, sec or "")
                    if body is None:
                        problems.append((r.line, f"{where}: {doc} §{sec} not found in the normative source"))
                    elif not r.responsibility.strip() or normalized(r.responsibility) not in normalized(body):
                        problems.append((r.line, f"{where}: the Responsibility is not quoted from {doc} §{sec}"))
        for key, reason, line in p.exemptions:
            if not reason:
                problems.append((line, f"empty reason in `No step table for {key}:`"))
        for reason, line in p.reasons:
            if not reason:
                problems.append((line, "empty reason in `No step table:`"))
        if not p.has_entry():
            problems.append((1, "no procedure-step entry: add step tables, `No step table for <doc> §<section>: <reason>` lines, or one `No step table: <reason>` line"))
        return problems, p, tables


def evaluate(root: Path, cfg: Dict[str, Any], matrix: Any, feature: Optional[str] = None) -> Result:
    """Check the selected specs and count their coverage (``matrix`` is a ``trace.Matrix``)."""
    res = Result()
    statuses = set(settings(cfg).get("statuses") or [])
    checker = _Checker(root, cfg, matrix)
    features_dir = root / cfg["paths"]["features_dir"]
    if not features_dir.is_dir():
        return res
    for spec in sorted(features_dir.glob("*/spec.md")):
        name = spec.parent.name
        if not selected(name, feature):
            continue
        status = matrix.features.get(name, "Draft")
        fid = f"{cfg['features']['id_prefix']}{name.split('-', 1)[0]}"
        rel = spec.relative_to(root).as_posix()
        problems, p, tables = checker.spec(spec.read_text(encoding="utf-8", errors="replace"), fid)
        for line, msg in problems:
            text = f"{rel}:{line}: {fid}: {msg}"
            if status in statuses:
                res.errors.append(text)
            else:
                res.warnings.append(f"{text} (procedure-step defect in a {status} spec; fails once {' or '.join(sorted(statuses)) or 'checked'})")
        for line, msg in p.lookalikes:
            res.warnings.append(f"{rel}:{line}: {fid}: {msg}")
        if p.has_entry():
            res.coverage.append({
                "feature": name,
                "status": status,
                "stepTables": tables,
                "exemptions": [{"procedure": k, "reason": r} for k, r, _ in p.exemptions],
                "featureWideReasons": [r for r, _ in p.reasons],
            })
    return res


def to_markdown(coverage: List[Dict[str, Any]]) -> str:
    if not coverage:
        return ""
    lines = [
        "",
        "## Procedure step coverage",
        "",
        "| Feature | Step table | Steps | Mapped | Owned by other features | Out of scope | Verified |",
        "|---|---|---|---|---|---|---|",
    ]
    notes = []
    for f in coverage:
        for t in f["stepTables"]:
            lines.append(f"| {f['feature']} ({f['status']}) | {t['procedure']} | {t['steps']} | {t['mapped']} | "
                         f"{t['ownedByOtherFeatures']} | {t['outOfScope']} | {t['verified']} |")
        notes.extend(f"- {f['feature']}: No step table for {e['procedure']}: {e['reason']}" for e in f["exemptions"])
        notes.extend(f"- {f['feature']}: No step table: {r}" for r in f["featureWideReasons"])
    if notes:
        lines.append("")
        lines.extend(notes)
    return "\n".join(lines) + "\n"
