"""``sdd check protected``: files a branch may change only with the owner's approval.

The protected set is ``protected_paths`` (constitution, agent configuration, ``sdd.config.json``,
the normative source, ...), the installed kit (``<paths.tools_dir>/**``: hooks, checkers and the
pipeline that CI itself runs) and gitleaks' own configuration (``.gitleaks.toml``,
``.gitleaksignore``, which a branch could use to allowlist its secrets). Any such file changed
in ``<merge-base>..`` (commits and the working tree) fails, unless the owner approved the change
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


def protected_globs(cfg: Dict[str, Any]) -> List[str]:
    return [e["glob"] for e in cfg["protected_paths"] if isinstance(e, dict) and e.get("glob")] + kit_globs(cfg) + GITLEAKS_FILES


def check(root: Path, cfg: Dict[str, Any]) -> Result:
    res = Result("protected")
    base = vcs.merge_base(root, cfg)
    changed = [p for p in vcs.git(root, "diff", "--name-only", "-z", "--no-renames", base).split("\0") if p]
    hits = sorted(p for p in changed if any_match(p, protected_globs(cfg)))
    env = cfg.get("override_env") or "SDD_ALLOW_PROTECTED"
    if hits and os.environ.get(env) == "1":
        res.notes.append(f"{env}=1: owner-approved changes to protected files: {', '.join(hits)}")
        return res.finish()
    for p in hits:
        res.violations.append(Violation("protected-changed", p, None, {"file": p, "message": "changed"}, 1,
                                        f"protected file changed on this branch (owner approval: re-run with {env}=1)"))
    return res.finish()
