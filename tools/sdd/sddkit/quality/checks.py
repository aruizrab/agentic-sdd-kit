"""``sdd check <name>`` for the command checks of ``quality.checks`` (docs/warnings, unused
dependencies, flaky tests, ...).

An entry ``{name, command, violation_regex?, baseline?: true, requires?, install?, timeout?,
ok_exits?}``. Exit 127 or a missing ``requires`` program skips (exit 3; CI: fail). Without
``violation_regex`` the verdict is the exit code (0 pass, anything else a violation). With it,
each match is examined: a captured ``file`` becomes the current file (a match without ``file``
inherits the previous one, so a tool that prints a file header followed by indented items
needs only one regex), and a captured ``message`` is a violation of kind ``<name>`` at the
current file (optional ``line``). With ``ok_exits`` an exit outside it is a tool error (exit
2); without it, a non-zero exit with no violation is a tool error. ``baseline: false`` means
every violation fails (flaky tests).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Optional

from . import QualityError, Result, Violation, baseline, missing_programs, normalize_path, run_command, skip, tail, tool_env


def find(cfg: Dict[str, Any], name: str) -> Optional[Dict[str, Any]]:
    for c in cfg["quality"]["checks"]:
        if c.get("name") == name:
            return c
    return None


def parse(chk: Dict[str, Any], text: str, root: Path) -> list:
    out = []
    current = ""
    for m in re.finditer(chk["violation_regex"], text):
        gd = m.groupdict()
        if gd.get("file"):
            current = normalize_path(gd["file"], root)
        if gd.get("message") is None:
            continue
        line = gd.get("line")
        msg = gd["message"].strip()
        out.append(Violation(chk["name"], current, int(line) if line else None, {"file": current, "message": msg}, 1, msg))
    return out


def execute(root: Path, cfg: Dict[str, Any], chk: Dict[str, Any]) -> Result:
    """Run the check and parse its violations (baseline not applied)."""
    name = chk["name"]
    env = tool_env(cfg, root)
    missing = missing_programs(chk.get("requires") or [], env)
    if missing:
        return skip(name, ", ".join(missing), chk.get("install", ""))
    rc, out, timed_out = run_command(chk["command"], root, env, float(chk.get("timeout", 1800)))
    if timed_out:
        raise QualityError(f"{name}: timed out after {chk.get('timeout', 1800)} s:\n{tail(out)}")
    if rc == 127:
        return skip(name, f"a program `{chk['command'].split()[0]}` runs (exit 127)", chk.get("install", ""))
    res = Result(name)
    if not chk.get("violation_regex"):
        if rc != 0:
            res.violations.append(Violation(name, "", None, {"file": "", "message": f"exit status {rc}"}, 1, f"exit status {rc}:\n{tail(out)}"))
        return res
    res.violations = parse(chk, out, root)
    ok_exits = chk.get("ok_exits")
    if ok_exits is not None and rc not in ok_exits:
        raise QualityError(f"{name}: command exited {rc} (ok_exits {ok_exits}):\n{tail(out)}")
    if ok_exits is None and rc != 0 and not res.violations:
        raise QualityError(f"{name}: command exited {rc} and printed no violation the regex recognises:\n{tail(out)}")
    return res


def run(root: Path, cfg: Dict[str, Any], name: str) -> Result:
    chk = find(cfg, name)
    if chk is None:
        raise QualityError(f"no check named {name!r} (built-in: size, arch, coverage, mutation, baseline, commits, secrets; "
                           f"configured: {', '.join(c['name'] for c in cfg['quality']['checks']) or 'none'})")
    res = execute(root, cfg, chk)
    if res.status != "pass":
        return res
    if chk.get("baseline", True) is False or not chk.get("violation_regex"):
        return res.finish()
    return baseline.classify(res, baseline.load(root, cfg), [name]).finish()
