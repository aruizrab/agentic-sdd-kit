"""``sdd check mutation``: zero surviving mutants in changed hand-written code, unless justified.

If no hand-written file changed, passes without running. Otherwise writes ``git diff
<merge-base>`` to ``<logs_dir>/quality/diff.patch``, substitutes ``{diff_file}`` and
``{changed_files}`` (shell-quoted) in ``quality.mutation.command``, runs it (deleting a stale
report first; an exit outside ``ok_exits`` is a tool error) and parses survivors from the
report or the output: ``regex`` (``survivor_regex`` with groups ``file``, ``mutation`` and
optional ``line``) or ``mutation-testing-report`` (the JSON Stryker writes; status
``Survived``). A survivor in a file that is not a changed hand-written file (whether or not it
has a line), or on a known line that is not a changed line or is in a test region, is ignored;
a survivor named by dotted module (``app.domain.calc``) is mapped to the changed file of that
module first. Any other survivor fails unless the current feature's review.md has a line
``- mutant: <file>[:<line>]: <mutation> — <reason>`` with a non-empty reason.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import QualityError, Result, Violation, normalize_path, quote_files, scratch_dir, vcs
from .coverage import check_report_fresh, considered_files, run_tool

JUSTIFICATION = re.compile(r"^\s*[-*]\s*mutant:\s*(?P<file>[^\s:]+)(?::(?P<line>\d+))?:\s*(?P<mutation>.+?)\s+[—]\s*(?P<reason>.*)$")


def parse_survivors(text: str, cfg: Dict[str, Any], root: Path) -> List[Tuple[str, Optional[int], str]]:
    m = cfg["quality"]["mutation"]
    out = []
    if m["format"] == "mutation-testing-report":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise QualityError(f"unparsable mutation-testing report: {exc}")
        for fname, entry in (data.get("files") or {}).items():
            for mut in entry.get("mutants") or []:
                if mut.get("status") != "Survived":
                    continue
                line = ((mut.get("location") or {}).get("start") or {}).get("line")
                desc = mut.get("mutatorName", "")
                if mut.get("replacement"):
                    desc += f": {mut['replacement']}"
                out.append((normalize_path(fname, root), int(line) if line else None, desc))
        return out
    if not m["survivor_regex"]:
        raise QualityError("quality.mutation.survivor_regex is empty (format 'regex')")
    for mm in re.finditer(m["survivor_regex"], text):
        line = mm.groupdict().get("line")
        out.append((normalize_path(mm.group("file"), root), int(line) if line else None, mm.group("mutation").strip()))
    return out


def module_path(name: str, files: Dict[str, Any]) -> str:
    """A survivor reported by dotted module name (mutmut: ``app.domain.calc``) mapped to the changed
    file it names (``src/app/domain/calc.py``); any other name is returned unchanged."""
    if name in files or "/" in name:
        return name
    for path in files:
        mod = path.rsplit(".", 1)[0].replace("/", ".")
        if mod == name or mod.endswith("." + name):
            return path
    return name


def justifications(root: Path, cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    fid = vcs.current_feature(root, cfg)
    d = vcs.feature_dir(root, cfg, fid) if fid else None
    review = d / "review.md" if d else None
    if not review or not review.is_file():
        return []
    out = []
    for line in review.read_text(encoding="utf-8", errors="replace").splitlines():
        m = JUSTIFICATION.match(line)
        if m and m.group("reason").strip():
            out.append({"file": m.group("file"), "line": int(m.group("line")) if m.group("line") else None,
                        "mutation": m.group("mutation").strip(), "reason": m.group("reason").strip()})
    return out


def run(root: Path, cfg: Dict[str, Any], no_run: bool = False) -> Result:
    m = cfg["quality"]["mutation"]
    if not m["command"] and not m["report"]:
        raise QualityError("mutation not configured: set quality.mutation.command in sdd.config.json (see docs/ADAPTING.md)")
    base = vcs.merge_base(root, cfg)
    changed = vcs.changed_lines(root, base)
    files = considered_files(root, cfg, changed, "mutation")
    res = Result("mutation")
    if not files:
        res.notes.append("no changed hand-written files: mutation not run")
        return res.finish()
    output = ""
    if m["command"] and not no_run:
        diff_file = scratch_dir(root, cfg) / "diff.patch"
        diff_file.write_text(vcs.diff_text(root, base), encoding="utf-8")
        cmd = m["command"].replace("{diff_file}", quote_files([str(diff_file)])).replace("{changed_files}", quote_files(sorted(files)))
        failed, output = run_tool(root, cfg, "mutation", cmd)
        if failed:
            return failed
    elif m["report"]:
        check_report_fresh(root, root / m["report"], list(files))
    else:
        raise QualityError("--no-run needs quality.mutation.report")
    text = (root / m["report"]).read_text(encoding="utf-8", errors="replace") if m["report"] else output
    just = justifications(root, cfg)
    for path, line, mutation in parse_survivors(text, cfg, root):
        path = module_path(path, files)
        considered = files.get(path)
        if considered is None or (line is not None and line not in considered[0]):
            continue  # unchanged file or line, test region or not hand-written code
        ok = [j for j in just if j["file"] == path and j["mutation"] == mutation and (j["line"] in (None, line))]
        if ok:
            res.notes.append(f"justified survivor {path}{':' + str(line) if line else ''}: {mutation} — {ok[0]['reason']}")
            continue
        res.violations.append(Violation("mutation", path, line, {"file": path, "message": mutation}, 1,
                                        f"surviving mutant: {mutation} (add a test, or justify it in review.md §6 as "
                                        f"`- mutant: {path}{':' + str(line) if line else ''}: {mutation} — <reason>`)"))
    return res.finish()
