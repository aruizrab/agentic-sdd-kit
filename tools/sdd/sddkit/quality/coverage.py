"""``sdd check coverage``: every changed hand-written line that the report instruments is covered.

Runs ``quality.coverage.command`` (unless ``--no-run`` or empty), then parses ``report`` (lcov
or Cobertura). Considered lines: the lines changed in the working tree since the merge-base,
in files of ``coverage.globs`` (``[]`` = the effective size globs), minus test code (generic
test directories, registry test patterns, ``quality.test_globs``, test regions such as Rust
``#[cfg(test)]`` items), ``quality.generated`` and the installed kit (``<paths.tools_dir>/**``). A considered line with 0 hits fails unless a
``cov:unreachable: <reason>`` comment marks it (at the end of the line, or on a comment-only
line directly above). A changed considered file absent from the report fails as
``coverage-unmeasured`` unless it cannot hold executable code.
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from . import QualityError, Result, Violation, kit_globs, langs, missing_programs, normalize_path, run_command, skip, tail, tool_env, vcs
from ..util import any_match
from .size import effective_globs

MARKER = re.compile(r"cov:unreachable\b:?(.*)")


def considered_files(root: Path, cfg: Dict[str, Any], changed: Dict[str, Set[int]], tool: str) -> Dict[str, Tuple[Set[int], langs.Source]]:
    """Changed hand-written files with their considered lines and lexed source."""
    q = cfg["quality"]
    globs = q[tool]["globs"] or effective_globs(cfg)
    out = {}
    for path, lines in sorted(changed.items()):
        if not any_match(path, globs) or any_match(path, q["generated"]) or any_match(path, kit_globs(cfg)) or langs.is_test_file(path, cfg):
            continue
        p = root / path
        if not p.is_file():
            continue
        lang = langs.language_for(path, cfg) or langs.PLAIN
        src = langs.lex(p.read_text(encoding="utf-8", errors="replace"), lang)
        test_lines = {ln + 1 for a, b in langs.test_regions(src) for ln in range(a, b + 1)}
        keep = {ln for ln in lines if ln not in test_lines}
        if keep:
            out[path] = (keep, src)
    return out


def parse_lcov(text: str, root: Path) -> Dict[str, Dict[int, int]]:
    out: Dict[str, Dict[int, int]] = {}
    cur: Optional[Dict[int, int]] = None
    for line in text.splitlines():
        if line.startswith("SF:"):
            cur = out.setdefault(normalize_path(line[3:], root), {})
        elif line.startswith("DA:") and cur is not None:
            parts = line[3:].split(",")
            try:
                ln, hits = int(parts[0]), int(float(parts[1]))
            except (IndexError, ValueError):
                raise QualityError(f"unparsable lcov line: {line!r}")
            cur[ln] = cur.get(ln, 0) + hits
        elif line.startswith("end_of_record"):
            cur = None
    return out


def parse_cobertura(text: str, root: Path) -> Dict[str, Dict[int, int]]:
    try:
        tree = ET.fromstring(text)
    except ET.ParseError as exc:
        raise QualityError(f"unparsable Cobertura report: {exc}")
    sources = [s.text.strip() for s in tree.iter("source") if s.text and s.text.strip()]
    out: Dict[str, Dict[int, int]] = {}
    for cls in tree.iter("class"):
        fname = cls.get("filename") or ""
        path = fname
        for s in sources:
            cand = os.path.join(s, fname)
            if Path(cand).is_file() or (root / cand).is_file():
                path = cand
                break
        rel = normalize_path(path, root)
        hits = out.setdefault(rel, {})
        for line in cls.iter("line"):
            try:
                ln, h = int(line.get("number", "")), int(float(line.get("hits", "0")))
            except ValueError:
                raise QualityError(f"unparsable Cobertura line in {fname}")
            hits[ln] = hits.get(ln, 0) + h
    return out


def markers(src: langs.Source) -> Dict[int, str]:
    """1-based line -> reason ('' when missing) of every line a marker excludes."""
    out: Dict[int, str] = {}
    for idx, comment in enumerate(src.comments):
        m = MARKER.search(comment)
        if not m:
            continue
        reason = m.group(1).strip()
        if src.is_code[idx]:
            out[idx + 1] = reason
        elif idx + 1 < len(src.lines):
            out[idx + 2] = reason
    return out


def no_code(path: str, src: langs.Source, cfg: Dict[str, Any]) -> bool:
    if any_match(path, cfg["quality"]["coverage"]["unmeasured_ok"]) or langs.is_no_code_file(path, cfg):
        return True
    return src.lang.name in langs.LANGS and not src.lang.top_level_statements and not langs.find_functions(src)


def run_tool(root: Path, cfg: Dict[str, Any], section: str, extra_cmd: Optional[str] = None) -> Tuple[Optional[Result], str]:
    """Run ``quality.<section>.command`` after deleting its report. Returns (failure result or None, output)."""
    t = cfg["quality"][section]
    env = tool_env(cfg, root)
    missing = missing_programs(t["requires"], env)
    if missing:
        return skip(section, ", ".join(missing), t["install"]), ""
    if t["report"]:
        rp = root / t["report"]
        if rp.is_file():
            rp.unlink()
    cmd = extra_cmd or t["command"]
    rc, out, timed_out = run_command(cmd, root, env, float(t["timeout"]))
    if timed_out:
        raise QualityError(f"{section} command timed out after {t['timeout']} s:\n{tail(out)}")
    if rc == 127:
        return skip(section, cmd.split()[0], t["install"]), out
    if rc not in (t["ok_exits"] or [0]):
        raise QualityError(f"{section} command exited {rc} (ok_exits {t['ok_exits']}); the tool did not complete:\n{tail(out)}")
    if t["report"] and not (root / t["report"]).is_file():
        raise QualityError(f"{section} command wrote no report at {t['report']}:\n{tail(out)}")
    return None, out


def check_report_fresh(root: Path, report: Path, files: List[str]) -> None:
    if not report.is_file():
        raise QualityError(f"no report at {report} (run the coverage command or drop --no-run)")
    newest = max(((root / f).stat().st_mtime for f in files if (root / f).is_file()), default=0)
    if report.stat().st_mtime < newest:
        raise QualityError(f"report {report} is older than the considered files (stale); re-run without --no-run")


def run(root: Path, cfg: Dict[str, Any], no_run: bool = False) -> Result:
    c = cfg["quality"]["coverage"]
    if not c["command"] and not c["report"]:
        raise QualityError("coverage not configured: set quality.coverage.command and report in sdd.config.json (see docs/ADAPTING.md)")
    if not c["report"]:
        raise QualityError("quality.coverage.report is empty: name the lcov or Cobertura file the command writes")
    base = vcs.merge_base(root, cfg)
    files = considered_files(root, cfg, vcs.changed_lines(root, base), "coverage")
    res = Result("coverage")
    if not files:
        res.notes.append("no changed hand-written lines")
        return res.finish()
    if c["command"] and not no_run:
        failed, _out = run_tool(root, cfg, "coverage")
        if failed:
            return failed
    else:
        if not c["report"]:
            raise QualityError("--no-run needs quality.coverage.report")
        check_report_fresh(root, root / c["report"], list(files))
    rp = root / c["report"]
    if not rp.is_file():
        raise QualityError(f"coverage report {c['report']} not found")
    text = rp.read_text(encoding="utf-8", errors="replace")
    report = parse_lcov(text, root) if c["format"] == "lcov" else parse_cobertura(text, root)
    for path, (lines, src) in files.items():
        marks = markers(src)
        for ln in sorted(lines):
            if ln in marks:
                res.markers.append({"file": path, "line": ln, "reason": marks[ln]})
                if not marks[ln]:
                    res.violations.append(Violation("coverage-marker", path, ln, {"file": path, "message": "marker without reason"}, 1,
                                                    "cov:unreachable marker without a reason"))
        hits = report.get(path)
        if hits is None:
            if not no_code(path, src, cfg):
                res.violations.append(Violation("coverage-unmeasured", path, None, {"file": path, "message": "absent from report"}, 1,
                                                "changed file absent from the coverage report (no test loads it?)"))
            continue
        for ln in sorted(lines):
            if hits.get(ln, 1) == 0 and ln not in marks:
                res.violations.append(Violation("coverage", path, ln, {"file": path, "message": "uncovered"}, 1, "changed line not covered"))
    return res.finish()
