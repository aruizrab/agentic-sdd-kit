"""``sdd check protected``: files a branch may change only with the owner's approval.

The protected set is taken from the merge-base's ``sdd.config.json`` (the branch under test
must not pick its own rules): its ``protected_paths`` (constitution, agent configuration, the
normative source, ...) and the installed kit (``<paths.tools_dir>/**``: hooks, checkers and the
pipeline that CI itself runs). Always protected, whatever either configuration says:
``sdd.config.json``, the kit directory of the branch's configuration too, and gitleaks' own
configuration (``.gitleaks.toml``, ``.gitleaksignore``, which a branch could use to allowlist
its secrets). A ``.gitattributes`` line the branch adds with ``binary`` or ``-diff`` fails too:
it hides a file's content from diffs and from ``gitleaks git``.

Any such change in ``<merge-base>..`` (commits and the working tree) fails, unless the owner
approved it by running the check with ``SDD_ALLOW_PROTECTED=1``. The variable is fixed: a
configuration cannot rename it (``override_env`` applies to the Claude Code hooks only). In CI,
set it only on an owner's approval, e.g. a label. Agents never set it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List

from . import Result, Violation, kit_globs, vcs
from .. import config as cfgmod
from ..util import any_match

APPROVAL_ENV = "SDD_ALLOW_PROTECTED"
GITLEAKS_FILES = [".gitleaks.toml", ".gitleaksignore"]
HIDING_ATTRIBUTES = ("binary", "-diff")


def approved() -> bool:
    """The owner's approval for protected changes; the name is fixed, never read from a config."""
    return os.environ.get(APPROVAL_ENV) == "1"


def base_config(root: Path, mb: str) -> Dict[str, Any]:
    """The merge-base's ``sdd.config.json`` over the defaults (the defaults alone when it is
    missing or unreadable)."""
    text = vcs.show(root, mb, cfgmod.CONFIG_NAME)
    try:
        data = json.loads(text) if text is not None else {}
    except json.JSONDecodeError:
        data = {}
    return cfgmod._merge(cfgmod.DEFAULTS, data if isinstance(data, dict) else {})


def protected_globs(base_cfg: Dict[str, Any], branch_cfg: Dict[str, Any]) -> List[str]:
    entries = base_cfg.get("protected_paths")
    globs = [e["glob"] for e in entries if isinstance(e, dict) and isinstance(e.get("glob"), str) and e["glob"]] \
        if isinstance(entries, list) else []
    return globs + [cfgmod.CONFIG_NAME] + kit_globs(base_cfg) + kit_globs(branch_cfg) + GITLEAKS_FILES


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
    globs = protected_globs(base_config(root, base), cfg)
    hits = sorted(p for p in changed if any_match(p, globs))
    attrs = sorted(hiding_attributes(root, base))
    if (hits or attrs) and approved():
        res.notes.append(f"{APPROVAL_ENV}=1: owner-approved changes to protected files: {', '.join(sorted(set(hits + attrs)))}")
        return res.finish()
    for p in hits:
        res.violations.append(Violation("protected-changed", p, None, {"file": p, "message": "changed"}, 1,
                                        f"protected file changed on this branch (owner approval: re-run with {APPROVAL_ENV}=1)"))
    for p in attrs:
        res.violations.append(Violation("protected-gitattributes", p, None, {"file": p, "message": "binary"}, 1,
                                        "adds a `binary` or `-diff` attribute, which hides content from diffs and gitleaks "
                                        f"(owner approval: re-run with {APPROVAL_ENV}=1)"))
    return res.finish()
