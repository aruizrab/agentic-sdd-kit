"""``sdd check arch``: declarative module (layer) dependency rules and forbidden patterns.

``quality.arch.modules`` lists modules ``{name, paths, imports, may_depend_on, forbid}``. A file
belongs to the first module whose ``paths`` globs match; files in no module or in
``quality.arch.exclude`` or the installed kit (``<paths.tools_dir>/**``) are not checked. Each dependency the language registry extracts is
resolved to a module: a relative specifier by path (``paths`` globs), anything else by the
``imports`` regexes; no match means external and is ignored. A dependency on another module
that is not in ``may_depend_on`` is an ``arch-dep`` violation; every ``forbid`` pattern that
matches a code line (comments excluded) of the module's files is an ``arch-forbid`` violation.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import Result, Violation, baseline, kit_globs, langs, vcs
from ..util import any_match


class _Modules:
    def __init__(self, cfg: Dict[str, Any]) -> None:
        self.mods = cfg["quality"]["arch"]["modules"]
        self.rx = [(m["name"], [re.compile(r) for r in m.get("imports") or []]) for m in self.mods]

    def by_path(self, path: str) -> Optional[Dict[str, Any]]:
        for m in self.mods:
            if any_match(path, m["paths"]):
                return m
        return None

    def by_spec(self, spec: str) -> Optional[str]:
        for name, rxs in self.rx:
            if any(r.search(spec) for r in rxs):
                return name
        return None


def analyse(root: Path, cfg: Dict[str, Any]) -> Result:
    res = Result("arch")
    mods = _Modules(cfg)
    if not mods.mods:
        return res
    exclude = list(cfg["quality"]["arch"]["exclude"]) + kit_globs(cfg)
    files = vcs.tracked_files(root)
    for path in files:
        if any_match(path, exclude):
            continue
        mod = mods.by_path(path)
        if mod is None:
            continue
        lang = langs.language_for(path, cfg)
        p = root / path
        if lang is None or not p.is_file():
            continue
        src = langs.lex(p.read_text(encoding="utf-8", errors="replace"), lang)
        allowed = set(mod.get("may_depend_on") or [])
        seen = set()
        for dep in langs.extract_deps(src, path, files, lambda s: mods.by_spec(s) is not None):
            if dep.relative:
                target_mod = next((mods.by_path(c) for c in langs.relative_candidates(dep.spec, lang) if mods.by_path(c)), None)
                target = target_mod["name"] if target_mod else None
            else:
                target = mods.by_spec(dep.spec)
            if target is None or target == mod["name"] or target in allowed:
                continue
            key = (dep.line, target)
            if key in seen:
                continue
            seen.add(key)
            edge = f"{mod['name']}->{target}"
            res.violations.append(Violation("arch-dep", path, dep.line, {"file": path, "dep": edge}, 1,
                                            f"{edge} via {dep.spec} ({target} is not in {mod['name']}.may_depend_on)"))
        for rule in mod.get("forbid") or []:
            rx = re.compile(rule["pattern"])
            for idx, line in enumerate(src.code):
                if src.is_code[idx] and rx.search(line):
                    res.violations.append(Violation("arch-forbid", path, idx + 1,
                                                    {"file": path, "module": mod["name"], "pattern": rule["pattern"]}, 1,
                                                    f"forbidden in {mod['name']}: /{rule['pattern']}/: {rule.get('reason', '')}".rstrip(": ")))
    return res


def run(root: Path, cfg: Dict[str, Any]) -> Result:
    res = analyse(root, cfg)
    if not cfg["quality"]["arch"]["modules"]:
        res.notes.append("no modules configured (quality.arch.modules): nothing to check")
    return baseline.classify(res, baseline.load(root, cfg), ["arch-dep", "arch-forbid"]).finish()
