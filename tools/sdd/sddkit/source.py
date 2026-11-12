"""``sdd source``: look up the normative source (standard clause, API contract
section, product requirement) so that agents never cite from memory.

Configured by ``sources``:

* ``lookup_command``: a shell command with ``{args}`` (all arguments joined) or
  ``{doc}`` / ``{clause}`` placeholders, e.g. ``<lookup tool> clause {doc} {clause}``.
  When set, ``sdd source clause <doc> <clause>`` runs it.
* ``text_dir``: a directory of Markdown documents. Without a lookup command the
  built-in reader finds the file whose name starts with ``<doc>`` (case-
  insensitive, dots and spaces ignored) and prints the clause whose heading
  matches ``heading_regex`` (``{clause}`` substituted) up to the next heading of
  the same or a smaller depth.
* ``sdd source find <doc> <regex>`` searches a document and prints matching lines
  with their enclosing heading.
"""

from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HEADING = re.compile(r"^(#+)\s+(.*?)\s*$")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def find_doc(text_dir: Path, doc: str) -> Optional[Path]:
    if not text_dir.is_dir():
        return None
    key = _norm(doc)
    candidates = sorted(p for p in text_dir.rglob("*") if p.is_file() and p.suffix.lower() in (".md", ".txt", ".markdown"))
    exact = [p for p in candidates if _norm(p.stem) == key]
    if exact:
        return exact[-1]
    containing = [p for p in candidates if key and key in _norm(p.stem)]
    if not containing:
        return None
    # Prefer the shortest stem (e.g. "<doc>" over "<doc>-annex-notes"); latest version last.
    shortest = min(len(_norm(p.stem)) for p in containing)
    return sorted(p for p in containing if len(_norm(p.stem)) == shortest)[-1]


def clause_depth(clause: str) -> int:
    return len([x for x in clause.replace("Annex", "").strip().split(".") if x])


def extract_clause(text: str, clause: str, heading_regex: str) -> Optional[Tuple[int, List[str]]]:
    rx = re.compile(heading_regex.replace("{clause}", re.escape(clause)))
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if rx.match(line):
            start = i
            break
    if start is None:
        return None
    depth = clause_depth(clause)
    out = [lines[start]]
    for line in lines[start + 1 :]:
        hm = HEADING.match(line)
        if hm:
            num = hm.group(2).split()[0] if hm.group(2).split() else ""
            if re.match(r"^[A-Z0-9][0-9A-Za-z.]*$", num) and clause_depth(num) <= depth:
                break
        out.append(line)
    return start + 1, out


def run_lookup(cfg: Dict[str, Any], root: Path, args: List[str]) -> int:
    cmd = cfg["sources"].get("lookup_command") or ""
    if not cmd:
        return 127
    doc = args[0] if args else ""
    clause = args[1] if len(args) > 1 else ""
    full = cmd.replace("{args}", " ".join(shlex.quote(a) for a in args)).replace("{doc}", shlex.quote(doc)).replace("{clause}", shlex.quote(clause))
    return subprocess.run(full, shell=True, cwd=str(root)).returncode


def clause(cfg: Dict[str, Any], root: Path, doc: str, clause_id: str) -> int:
    rc = run_lookup(cfg, root, [doc, clause_id])
    if rc != 127:
        return rc
    text_dir = root / (cfg["sources"].get("text_dir") or "")
    if not cfg["sources"].get("text_dir") or not text_dir.is_dir():
        print("sources.lookup_command and sources.text_dir are both unset; configure one in sdd.config.json", flush=True)
        return 2
    path = find_doc(text_dir, doc)
    if path is None:
        print(f"no document matching {doc!r} under {text_dir}")
        return 1
    found = extract_clause(path.read_text(encoding="utf-8", errors="replace"), clause_id, cfg["sources"]["heading_regex"])
    if not found:
        print(f"clause {clause_id} not found in {path.relative_to(root)}; do not guess, write a [NEEDS CLARIFICATION] marker")
        return 1
    line, lines = found
    print(f"# {path.relative_to(root)}:{line}")
    print("\n".join(lines))
    return 0


def find(cfg: Dict[str, Any], root: Path, doc: str, pattern: str, context: int = 0) -> int:
    text_dir = root / (cfg["sources"].get("text_dir") or "")
    if not cfg["sources"].get("text_dir") or not text_dir.is_dir():
        return run_lookup(cfg, root, ["find", doc, pattern]) if cfg["sources"].get("lookup_command") else 2
    path = find_doc(text_dir, doc)
    if path is None:
        print(f"no document matching {doc!r} under {text_dir}")
        return 1
    rx = re.compile(pattern, re.IGNORECASE)
    heading = ""
    hits = 0
    for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        hm = HEADING.match(line)
        if hm:
            heading = hm.group(2)
        if rx.search(line):
            hits += 1
            print(f"{path.name}:{i} [{heading}] {line.strip()}")
    print(f"{hits} hit(s)")
    return 0 if hits else 1
