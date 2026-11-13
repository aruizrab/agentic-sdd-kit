"""``sdd check protected``: files a branch may change only with the owner's approval.

The protected set is ``protected_paths`` (constitution, agent configuration, ``sdd.config.json``,
the normative source, ...), the installed kit (``<paths.tools_dir>/**``: hooks, checkers and the
pipeline that CI itself runs) and gitleaks' own configuration (``.gitleaks.toml``,
``.gitleaksignore``, which a branch could use to allowlist its secrets). Any such file changed
in ``<merge-base>..`` (commits and the working tree) fails, and so does a ``.gitattributes`` line
the branch adds with ``binary`` or ``-diff`` (it hides a file's content from diffs and from
``gitleaks git``), unless the owner approved the change
by running the check with ``<override_env>=1`` (default ``SDD_ALLOW_PROTECTED``; in CI, set it
only on an owner's approval, e.g. a label). Agents never set it.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List

from . import Result, Violation, kit_globs, vcs
from ..util import any_match

GITLEAKS_FILES = [".gitleaks.toml", ".gitleaksignore"]
HIDING_ATTRIBUTES = ("binary", "-diff")


def protected_globs(cfg: Dict[str, Any]) -> List[str]:
    return [e["glob"] for e in cfg["protected_paths"] if isinstance(e, dict) and e.get("glob")] + kit_globs(cfg) + GITLEAKS_FILES


def hiding_attributes(root: Path, base: str) -> List[str]:
    """``.gitattributes`` files (any directory) with an added line that sets ``binary`` or ``-diff``."""
    out = []
    for path, lines in vcs.added_lines(root, base).items():
        if path.rsplit("/", 1)[-1] != ".gitattributes":
            continue
        for _, text in lines:
            tokens = text.split()
            if tokens and not tokens[0].startswith("#") and any(t in HIDING_ATTRIBUTES for t in tokens[1:]):
                out.append(path)
                break
    return out


def check(root: Path, cfg: Dict[str, Any]) -> Result:
    res = Result("protected")
    base = vcs.merge_base(root, cfg)
    changed = [p for p in vcs.git(root, "diff", "--name-only", "-z", "--no-renames", base).split("\0") if p]
    hits = sorted(p for p in changed if any_match(p, protected_globs(cfg)))
    attrs = sorted(hiding_attributes(root, base))
    env = cfg.get("override_env") or "SDD_ALLOW_PROTECTED"
    if (hits or attrs) and os.environ.get(env) == "1":
        res.notes.append(f"{env}=1: owner-approved changes to protected files: {', '.join(sorted(set(hits + attrs)))}")
        return res.finish()
    for p in hits:
        res.violations.append(Violation("protected-changed", p, None, {"file": p, "message": "changed"}, 1,
                                        f"protected file changed on this branch (owner approval: re-run with {env}=1)"))
    for p in attrs:
        res.violations.append(Violation("protected-gitattributes", p, None, {"file": p, "message": "binary"}, 1,
                                        "adds a `binary` or `-diff` attribute, which hides content from diffs and gitleaks "
                                        f"(owner approval: re-run with {env}=1)"))
    return res.finish()
