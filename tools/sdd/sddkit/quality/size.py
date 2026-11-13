"""``sdd check size``: code lines per function and per file.

A code line is a non-blank line that is not comment-only (documentation comments and Python
docstrings included, see :mod:`.langs`). Measured files: ``git ls-files`` (or, with
``--staged``, the staged files and their staged content) matching the effective
``quality.size.globs`` (``[]`` = every extension the language registry knows), minus
``quality.size.exclude`` and ``quality.generated``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from . import Result, Violation, baseline, langs, vcs
from ..util import any_match


def effective_globs(cfg: Dict[str, Any]) -> List[str]:
    return list(cfg["quality"]["size"]["globs"]) or langs.known_globs(cfg)


def measured(path: str, cfg: Dict[str, Any]) -> bool:
    q = cfg["quality"]
    return bool(any_match(path, effective_globs(cfg))) and not any_match(path, q["size"]["exclude"]) and not any_match(path, q["generated"])


def measure_text(path: str, text: str, cfg: Dict[str, Any], res: Result) -> None:
    s = cfg["quality"]["size"]
    lang = langs.language_for(path, cfg) or langs.PLAIN
    src = langs.lex(text, lang)
    total = langs.code_line_count(src)
    if s["file_fail"] and total > s["file_fail"]:
        res.violations.append(Violation("size-file", path, None, {"file": path}, total, f"{total} code lines (fail above {s['file_fail']})"))
    elif s["file_warn"] and total > s["file_warn"]:
        res.warnings.append(f"size-file {path}: {total} code lines (warn above {s['file_warn']})")
    for fn in langs.find_functions(src):
        if s["function_fail"] and fn.lines > s["function_fail"]:
            res.violations.append(Violation("size-function", path, fn.start + 1, {"file": path, "function": fn.name, "ordinal": fn.ordinal},
                                            fn.lines, f"function {fn.name}: {fn.lines} code lines (fail above {s['function_fail']})"))
        elif s["function_warn"] and fn.lines > s["function_warn"]:
            res.warnings.append(f"size-function {path}:{fn.start + 1} {fn.name}: {fn.lines} code lines (warn above {s['function_warn']})")


def measure(root: Path, cfg: Dict[str, Any], staged: bool = False) -> Result:
    """Measure without consulting the baseline (states stay ``new``)."""
    res = Result("size")
    files = vcs.staged_files(root) if staged else vcs.tracked_files(root)
    for path in files:
        if not measured(path, cfg):
            continue
        if staged:
            text: Optional[str] = vcs.staged_content(root, path)
        else:
            p = root / path
            text = p.read_text(encoding="utf-8", errors="replace") if p.is_file() else None
        if text is None:
            continue
        measure_text(path, text, cfg, res)
    return res


def run(root: Path, cfg: Dict[str, Any], staged: bool = False) -> Result:
    res = measure(root, cfg, staged)
    return baseline.classify(res, baseline.load(root, cfg), ["size-file", "size-function"], stale=not staged).finish()
