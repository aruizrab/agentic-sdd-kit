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

Resource limits (a mutant can turn a loop into a runaway allocator): ``{jobs}``,
``{test_threads}``, ``{memory_limit_kb}`` and ``{memcap_runner}`` are substituted in the
command and in the values of ``quality.mutation.env`` (set for the command), and
``SDD_MUTATION_JOBS``, ``SDD_TEST_THREADS`` and ``SDD_MEM_LIMIT_KB`` are exported. With
``memory_limit_mib`` > 0 and no ``{memcap_runner}`` in the command or env, the whole command
runs under ``scripts/memcap-runner.sh`` (each process capped); with it, the tool's test-runner
hook caps the test processes only (Rust: ``CARGO_TARGET_<TRIPLE>_RUNNER``). The command runs
under the host-wide heavy-job lock (``quality.heavy_lock``).

``{test_filter}`` is replaced by ``quality.mutation.test_filter`` (shell text, for example a
filter that leaves out end-to-end tests that need the real repository or minutes per run; a
mutation tool runs the tests in a copy of the tree), or by nothing while ``test_filter_when``
names globs and no tracked file matches one (some runners reject a filter that matches no test
binary). The filter lives on the command line, not in a test-runner profile file, because such
files are also read by fixture projects that have no such tests.

Host kills (Rust profile, cargo-mutants): with ``quality.mutation.outcomes`` set (its
``outcomes.json``), every caught mutant is refined by its per-mutant log. With nextest a test
process killed by a signal gives the same "caught" result as a failed assertion; only the
status word of the test line tells them apart. A ``SIGKILL [`` status line is a kill by the
host (memory guard, cgroup cap, kernel OOM killer): the mutant was not measured, so the gate
fails and names it for a re-run. A ``SIGABRT [`` status line with ``memory allocation of N
bytes failed`` is the per-test memory cap stopping a runaway allocation: still caught, listed
separately. Any other ending stays caught. The word SIGKILL elsewhere in a log (a test's own
message) is not a status line.

``SDD_MUTATION``: unset or ``run`` runs the check; ``skip`` reports SKIP (exit 3) without
running anything, for a loaded host; in CI mode ``skip`` fails, so CI never skips mutation
testing; any other value is an error (exit 2).
"""

from __future__ import annotations

import json
import os
import re
import shlex
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import QualityError, Result, Violation, ci_mode, normalize_path, quote_files, scratch_dir, vcs
from ..util import any_match
from .coverage import SCRIPTS, check_report_fresh, considered_files, run_tool

JUSTIFICATION = re.compile(r"^\s*[-*]\s*mutant:\s*(?P<file>[^\s:]+)(?::(?P<line>\d+))?:\s*(?P<mutation>.+?)\s+[—]\s*(?P<reason>.*)$")


KILLED_BY_HOST = "killed-by-host"
CAUGHT_BY_MEMORY_CAP = "caught-by-memory-cap"
CAUGHT = "caught"
_MUTANT_NAME = re.compile(r"^(?P<file>[^:\n]+):(?P<line>\d+):\d+: (?P<mutation>.+)$")


def _status_line(line: str, word: str) -> bool:
    """A test-runner status line whose status word is ``word`` (``<spaces><word> [``)."""
    rest = line.lstrip()
    return rest.startswith(word) and rest[len(word):].startswith(" [")


def classify_caught(log: str) -> str:
    """How a caught mutant's tests ended, from its log: killed by the host, caught by the memory cap, or caught."""
    lines = log.splitlines()
    if any(_status_line(line, "SIGKILL") for line in lines):
        return KILLED_BY_HOST
    alloc_failed = any(line.strip().startswith("memory allocation of ") and line.strip().endswith(" bytes failed") for line in lines)
    if alloc_failed and any(_status_line(line, "SIGABRT") for line in lines):
        return CAUGHT_BY_MEMORY_CAP
    return CAUGHT


def caught_endings(root: Path, cfg: Dict[str, Any]) -> List[Tuple[str, Optional[int], str, str]]:
    """``(file, line, mutation, ending)`` of every caught mutant in ``quality.mutation.outcomes``
    (cargo-mutants' ``outcomes.json``; each ``log_path`` is relative to its directory).
    A caught mutant without a log file stays caught; no outcomes file means none."""
    rel = cfg["quality"]["mutation"].get("outcomes") or ""
    path = root / rel if rel else None
    if not path or not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise QualityError(f"unparsable mutation outcomes {rel}: {exc}")
    out = []
    for o in (data.get("outcomes") or []) if isinstance(data, dict) else []:
        scenario = o.get("scenario") if isinstance(o, dict) else None
        mutant = scenario.get("Mutant") if isinstance(scenario, dict) else None
        if not isinstance(mutant, dict) or o.get("summary") != "CaughtMutant" or not o.get("log_path"):
            continue
        try:
            log = (path.parent / str(o["log_path"])).read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise QualityError(f"cannot read the mutant log {o['log_path']}: {exc}")
        name = str(mutant.get("name") or "")
        m = _MUTANT_NAME.match(name)
        file = str(mutant.get("file") or (m.group("file") if m else ""))
        span_line = ((mutant.get("span") or {}).get("start") or {}).get("line")
        line = int(span_line) if isinstance(span_line, int) else int(m.group("line")) if m else None
        out.append((normalize_path(file, root), line, m.group("mutation").strip() if m else name, classify_caught(log)))
    return out


def switch(cfg: Dict[str, Any]) -> Optional[Result]:
    """The ``SDD_MUTATION`` switch: None runs the check (unset or ``run``); otherwise the result
    reported without running the tool (SKIP for ``skip``, a failure for ``skip`` in CI mode)."""
    value = os.environ.get("SDD_MUTATION")
    if value is None or value == "run":
        return None
    if value == "skip":
        if ci_mode():
            return Result("mutation", status="fail", notes=["SDD_MUTATION=skip is not allowed in CI: CI never skips mutation testing"])
        return Result("mutation", status="skip", notes=["SDD_MUTATION=skip: mutation testing skipped on this host (CI runs it)"])
    raise QualityError(f"SDD_MUTATION must be `run` or `skip`, got {value!r}")


def test_filter(root: Path, cfg: Dict[str, Any]) -> str:
    """``quality.mutation.test_filter``, or "" while ``test_filter_when`` matches no tracked file."""
    m = cfg["quality"]["mutation"]
    text = m.get("test_filter") or ""
    when = m.get("test_filter_when") or []
    if text and when and not any(any_match(f, when) for f in vcs.git(root, "ls-files", "-z").split("\0") if f):
        return ""
    return text


def build_command(cfg: Dict[str, Any], diff_file: str, files: List[str], filter_text: str = "") -> Tuple[str, Dict[str, str]]:
    """The mutation command and its extra environment, with the resource limits and the test filter applied."""
    m = cfg["quality"]["mutation"]
    runner = str(SCRIPTS / "memcap-runner.sh")
    limit_kb = int(m["memory_limit_mib"]) * 1024
    values = {"{jobs}": str(m["jobs"]), "{test_threads}": str(m["test_threads"]), "{memory_limit_kb}": str(limit_kb), "{test_filter}": filter_text}

    def fill(text: str, runner_text: str) -> str:
        for key, value in {**values, "{memcap_runner}": runner_text}.items():
            text = text.replace(key, value)
        return text

    env = {k: fill(v, runner) for k, v in (m.get("env") or {}).items()}
    env.update({"SDD_MUTATION_JOBS": values["{jobs}"], "SDD_TEST_THREADS": values["{test_threads}"], "SDD_MEM_LIMIT_KB": str(limit_kb)})
    hooked = "{memcap_runner}" in m["command"] or any("{memcap_runner}" in v for v in (m.get("env") or {}).values())
    cmd = fill(m["command"], shlex.quote(runner)).replace("{diff_file}", quote_files([diff_file])).replace("{changed_files}", quote_files(sorted(files)))
    if limit_kb and not hooked:
        cmd = f"sh {shlex.quote(runner)} sh -c {shlex.quote(cmd)}"
    return cmd, env


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
    switched = switch(cfg)
    if switched:
        return switched
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
        cmd, env = build_command(cfg, str(diff_file), list(files), test_filter(root, cfg))
        failed, output = run_tool(root, cfg, "mutation", cmd, env)
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
    capped = 0
    for path, line, mutation, ending in caught_endings(root, cfg):
        considered = files.get(path)
        if ending == CAUGHT or considered is None or (line is not None and line not in considered[0]):
            continue
        where = f"{path}{':' + str(line) if line else ''}"
        if ending == CAUGHT_BY_MEMORY_CAP:
            capped += 1
            res.notes.append(f"caught by memory cap {where}: {mutation}")
            continue
        # Not justifiable in review.md: the mutant was not measured at all.
        res.violations.append(Violation("mutation", path, line, {"file": path, "message": f"killed by host: {mutation}"}, 1,
                                        f"test killed by the host (SIGKILL: memory guard, cgroup cap or OOM killer), not caught: {mutation}; "
                                        "re-run this mutant with fewer jobs and without memory pressure"))
    if capped:
        res.notes.append(f"{capped} mutant(s) caught by the memory cap (a runaway allocation stopped by quality.mutation.memory_limit_mib)")
    return res.finish()
