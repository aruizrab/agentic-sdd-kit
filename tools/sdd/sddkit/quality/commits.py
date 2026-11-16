"""Commit-message rules, shared by the commit-msg hook and ``sdd check commits``.

* first line ``<type>[(<scope>)][!]: <subject>`` with ``<type>`` in ``quality.hooks.types``, the
  scope rule of ``scope_regex`` (``""`` = optional and free; otherwise required and matching)
  and a non-empty subject, or git's ``Revert "<subject>"``;
* each ``required_trailers`` regex matches a line of the trailer block. The commit-msg hook
  requires them when ``required_trailers_env`` is ``""`` or that environment variable equals
  ``1`` (the committing process is an agent session). ``sdd check commits`` decides from the
  commit itself, never from the environment of the process running the check: trailers are
  required for a commit whose author or committer (``Name <email>``) matches
  ``trailer_authors``; with ``trailer_authors`` empty the check does not test trailers (with
  ``required_trailers_env`` empty it always does);
* the message holds no secret (built-in rules; gitleaks in the hook when installed).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import Result, Violation, secrets, vcs

HEADER = re.compile(r"^(?P<type>[A-Za-z]+)(?:\((?P<scope>[^()]*)\))?(?P<bang>!)?: (?P<subject>.*)$")


def clean(message: str) -> str:
    """Drop comment lines and everything below git's scissors line."""
    out = []
    for line in message.splitlines():
        if line.startswith("# ------------------------ >8 ------------------------"):
            break
        if line.startswith("#"):
            continue
        out.append(line.rstrip())
    while out and not out[-1]:
        out.pop()
    while out and not out[0]:
        out.pop(0)
    return "\n".join(out)


VERBOSE_CUT = re.compile(r"^(?P<c>\S) -{24} >8 -{24}$")


def secret_scan_text(message: str) -> str:
    """The text git may store as the commit message, for the secret scan: comment lines and a bare
    scissors line are kept (``git commit -m``/``-F`` use the ``whitespace`` cleanup, and
    ``--cleanup=verbatim|scissors`` keep them too); only git's own ``commit -v`` diff section is
    cut: a scissors line followed by git's ``Do not modify or remove the line above.`` comment,
    which no cleanup mode stores. Works for any ``core.commentChar``."""
    lines = message.splitlines()
    for i, line in enumerate(lines):
        m = VERBOSE_CUT.match(line)
        if m and i + 1 < len(lines) and lines[i + 1] == f"{m.group('c')} Do not modify or remove the line above.":
            return "\n".join(lines[:i]) + "\n"
    return message


def expected_format(cfg: Dict[str, Any]) -> str:
    h = cfg["quality"]["hooks"]
    scope = "(<scope>)" if h["scope_regex"] else "[(<scope>)]"
    return f"expected `<type>{scope}[!]: <subject>` with type one of {', '.join(h['types'])}" + (
        f" and scope matching /{h['scope_regex']}/" if h["scope_regex"] else "") + ", or `Revert \"<subject>\"`"


def header_problem(first: str, cfg: Dict[str, Any]) -> Optional[str]:
    h = cfg["quality"]["hooks"]
    if re.match(r'^Revert ".+"$', first):
        return None
    m = HEADER.match(first)
    if not m:
        return f"first line {first!r} is not a Conventional Commit header"
    if m.group("type") not in h["types"]:
        return f"unknown commit type {m.group('type')!r}"
    if not m.group("subject").strip():
        return "empty subject"
    if h["scope_regex"]:
        scope = m.group("scope")
        if not scope:
            return "missing scope"
        if not re.fullmatch(h["scope_regex"], scope):
            return f"scope {scope!r} does not match /{h['scope_regex']}/"
    return None


def trailers_required(cfg: Dict[str, Any]) -> bool:
    var = cfg["quality"]["hooks"]["required_trailers_env"]
    return not var or os.environ.get(var) == "1"


def trailer_block(message: str) -> List[str]:
    paras = [p for p in re.split(r"\n\s*\n", message.strip()) if p.strip()]
    if len(paras) < 2:
        return []
    return paras[-1].splitlines()


def message_problems(message: str, cfg: Dict[str, Any], need_trailers: Optional[bool] = None) -> List[Tuple[str, str]]:
    """(rule, detail) for the format and trailer rules (no secret scan).

    ``need_trailers`` None = decide from the environment (the commit-msg hook)."""
    msg = clean(message)
    first = msg.splitlines()[0] if msg else ""
    out = []
    p = header_problem(first, cfg)
    if p:
        out.append(("format", f"{p}; {expected_format(cfg)}"))
    if trailers_required(cfg) if need_trailers is None else need_trailers:
        block = trailer_block(msg)
        for rx in cfg["quality"]["hooks"]["required_trailers"]:
            if not any(re.search(rx, line) for line in block):
                out.append(("trailer", f"missing required trailer matching /{rx}/ in the last paragraph"))
    return out


def commit_type(message: str) -> Optional[str]:
    m = HEADER.match(clean(message).split("\n", 1)[0])
    return m.group("type") if m else None


def check(root: Path, cfg: Dict[str, Any]) -> Result:
    """``sdd check commits``: every non-merge commit in ``<merge-base>..HEAD``."""
    res = Result("commits")
    base = vcs.merge_base(root, cfg)
    shas = [s for s in vcs.git(root, "rev-list", "--no-merges", "--reverse", f"{base}..HEAD").split() if s]
    h = cfg["quality"]["hooks"]
    for sha in shas:
        msg = vcs.git(root, "log", "-1", "--format=%B", sha)
        if not h["required_trailers_env"]:
            need = True
        elif h["trailer_authors"]:
            who = vcs.git(root, "log", "-1", "--format=%an <%ae>%n%cn <%ce>", sha).splitlines()
            need = any(re.search(h["trailer_authors"], w) for w in who)
        else:
            need = False
        for rule, detail in message_problems(msg, cfg, need):
            res.violations.append(Violation("commit-" + rule, sha[:12], None, {"file": sha[:12], "message": rule}, 1, detail))
        for v in secrets.scan_message(cfg, msg):
            res.violations.append(Violation("commit-secret", sha[:12], None, {"file": sha[:12], "message": v.key["message"]}, 1,
                                            f"{v.message} in the commit message"))
    if not shas:
        res.notes.append(f"no commits in {base[:12]}..HEAD")
    elif h["required_trailers"] and h["required_trailers_env"] and not h["trailer_authors"]:
        res.notes.append("trailers not checked: set quality.hooks.trailer_authors to the agent commit identity to check them here")
    return res.finish()
