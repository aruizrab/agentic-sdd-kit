"""Quality checks (``sdd check <name>``), the shrink-only baseline and the git hooks.

Shared conventions (docs/design/quality-gates-and-routing.md, section 1):

* exit codes of every check: ``0`` pass (warnings allowed), ``1`` violations, ``2``
  configuration, input or tool error, ``3`` skipped because a required tool is missing;
* CI mode (``CI=true``): a missing tool is a failure (exit 1), never a skip;
* scratch output goes to ``<logs_dir>/quality/``, never ``/tmp``.

Language knowledge lives only in :mod:`.langs`.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

EXIT_PASS, EXIT_FAIL, EXIT_ERROR, EXIT_SKIP = 0, 1, 2, 3

BUILTIN_CHECKS = ("size", "arch", "coverage", "mutation", "baseline", "commits", "secrets", "protected")


class QualityError(Exception):
    """A configuration, input or tool error: the check exits 2."""


def ci_mode() -> bool:
    return os.environ.get("CI") == "true"


@dataclass
class Violation:
    kind: str
    file: str
    line: Optional[int] = None
    key: Dict[str, Any] = field(default_factory=dict)
    value: int = 1
    message: str = ""
    state: str = "new"

    def to_json(self) -> Dict[str, Any]:
        return {"kind": self.kind, "file": self.file, "line": self.line, "key": self.key,
                "value": self.value, "state": self.state, "message": self.message}

    def where(self) -> str:
        return f"{self.file}:{self.line}" if self.line else self.file


@dataclass
class Result:
    check: str
    status: str = "pass"  # pass | fail | skip | error
    violations: List[Violation] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    markers: List[Dict[str, Any]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    stale: List[Dict[str, Any]] = field(default_factory=list)
    tool_missing: bool = False  # set by skip(), whatever the CI mode: the check did not run

    def failing(self) -> List[Violation]:
        return [v for v in self.violations if v.state in ("new", "grown")]

    def finish(self) -> "Result":
        """Set the status from the violations (unless skip/error was set)."""
        if self.status in ("skip", "error"):
            return self
        self.status = "fail" if self.failing() or self.stale else "pass"
        return self


def skip(check: str, tool: str, install: str = "") -> Result:
    hint = f" (install: {install})" if install else ""
    res = Result(check, status="skip", notes=[f"{tool} not installed{hint}"], tool_missing=True)
    if ci_mode():
        res.status = "fail"
        res.notes.append("CI=true: a missing tool is a failure")
    return res


def error(check: str, message: str) -> Result:
    return Result(check, status="error", notes=[message])


def exit_code(res: Result) -> int:
    return {"pass": EXIT_PASS, "fail": EXIT_FAIL, "error": EXIT_ERROR, "skip": EXIT_SKIP}[res.status]


def emit(res: Result, as_json: bool, out=None) -> int:
    out = out or sys.stdout
    if as_json:
        out.write(json.dumps({
            "check": res.check, "status": res.status,
            "violations": [v.to_json() for v in res.violations],
            "stale": res.stale, "warnings": res.warnings, "markers": res.markers, "notes": res.notes,
        }, indent=2) + "\n")
        return exit_code(res)
    for note in res.notes:
        out.write(f"{res.check}: {note}\n")
    for w in res.warnings:
        out.write(f"WARN  {w}\n")
    for v in res.violations:
        tag = {"new": "FAIL ", "grown": "FAIL ", "baselined": "base "}.get(v.state, "     ")
        if v.state == "baselined" and not os.environ.get("SDD_VERBOSE"):
            continue
        out.write(f"{tag} {v.kind} {v.where()}: {v.message}{' (grown above baseline)' if v.state == 'grown' else ''}\n")
    for e in res.stale:
        out.write(f"FAIL  stale baseline entry {json.dumps(e, sort_keys=True)}; run `sdd baseline --update`\n")
    if res.markers:
        out.write(f"{res.check}: {len(res.markers)} cov:unreachable marker(s) on changed lines\n")
        for m in res.markers:
            out.write(f"      {m['file']}:{m['line']}: {m['reason'] or '(no reason)'}\n")
    based = sum(1 for v in res.violations if v.state == "baselined")
    failing = len(res.failing()) + len(res.stale)
    out.write(f"{res.check}: {res.status.upper()}" + (f" ({failing} violation(s)" + (f", {based} baselined" if based else "") + ")" if failing or based else "") + "\n")
    return exit_code(res)


# ----------------------------------------------------------------------------
# Running tools
# ----------------------------------------------------------------------------


# git exports these to hooks (GIT_INDEX_FILE is the committing repo's index.lock with `commit -a`).
# A project command that runs git in another repository (a test creating throwaway repos) must
# not inherit them, or it writes into the committing repo's index. The kit's own git calls use
# os.environ, not this environment, and keep them.
GIT_HOOK_ENV = ("GIT_INDEX_FILE", "GIT_DIR", "GIT_WORK_TREE", "GIT_PREFIX", "GIT_OBJECT_DIRECTORY",
                "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CONFIG_PARAMETERS", "GIT_COMMON_DIR")


def tool_env(cfg: Dict[str, Any], root: Path, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Environment for every command the kit runs for gates, checks and git hooks.

    ``quality.tool_dirs`` (root-relative, e.g. ``node_modules/.bin``) go in front of ``PATH``, so
    ``requires`` finds project-local tools and a missing one SKIPs instead of being downloaded.
    git's hook variables (``GIT_HOOK_ENV``) are removed; ``SDD_GIT_INDEX_FILE`` keeps the index
    git is committing from (absolute) for a step that must read exactly what is staged."""
    env = dict(os.environ)
    if env.get("GIT_INDEX_FILE"):
        env["SDD_GIT_INDEX_FILE"] = os.path.abspath(env["GIT_INDEX_FILE"])  # the staged set, for steps that need it
    for k in GIT_HOOK_ENV:
        env.pop(k, None)
    dirs = [str(root / d) for d in (cfg.get("quality") or {}).get("tool_dirs") or []]
    if dirs:
        env["PATH"] = os.pathsep.join(dirs + ([env["PATH"]] if env.get("PATH") else []))
    env["SDD_ROOT"] = str(root)
    env.setdefault("SDD_FEATURE", "")
    for k, v in ((cfg.get("build_cache") or {}).get("env") or {}).items():
        env[str(k)] = str(v)
    if extra:
        env.update(extra)
    return env


def run_command(cmd: str, cwd: Path, env: Dict[str, str], timeout: float, stdin: Optional[str] = None) -> Tuple[int, str, bool]:
    """Run a shell command in its own process group; kill the whole group on timeout.

    Returns (exit code, combined output, timed_out).
    """
    proc = subprocess.Popen(
        cmd, shell=True, cwd=str(cwd), env=env, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
        start_new_session=True,
    )
    try:
        out, _ = proc.communicate(stdin, timeout=timeout)
        return proc.returncode, out or "", False
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        out, _ = proc.communicate()
        return -9, out or "", True


def missing_programs(requires: List[str], env: Optional[Dict[str, str]] = None) -> List[str]:
    path = (env or os.environ).get("PATH")
    return [p for p in requires or [] if shutil.which(p, path=path) is None]


def kit_globs(cfg: Dict[str, Any]) -> List[str]:
    """The installed kit itself (``<paths.tools_dir>/**``): never project code.

    Size, arch, coverage, mutation, the commit test step and ``{files}`` of the pre-commit steps
    skip it, so adopting or upgrading the kit never trips the project's own thresholds.
    """
    d = (cfg.get("paths") or {}).get("tools_dir", "").strip().strip("/")
    return [f"{d}/**"] if d else []


def tail(text: str, n: int = 40) -> str:
    return "\n".join(text.splitlines()[-n:])


def quote_files(files: List[str]) -> str:
    """Shell-quoted file arguments; a name starting with ``-`` gets ``./`` so no tool reads it as an option."""
    return " ".join(shlex.quote("./" + f if f.startswith("-") else f) for f in files)


def scratch_dir(root: Path, cfg: Dict[str, Any]) -> Path:
    d = root / cfg["paths"]["logs_dir"] / "quality"
    d.mkdir(parents=True, exist_ok=True)
    return d


def normalize_path(path: str, root: Path) -> str:
    """A tool-reported path made relative to the root, slash-separated."""
    p = path.strip().replace("\\", "/")
    if os.path.isabs(p):
        try:
            p = Path(p).resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            try:
                p = Path(p).relative_to(root).as_posix()
            except ValueError:
                return p
    while p.startswith("./"):
        p = p[2:]
    return p
