"""``sdd union-merge [files...]``: resolve ADDITIVE manifest conflicts only.

For each conflicted file that matches ``merge.union_manifests`` (globs such as
``package.json``, ``go.mod``, ``pyproject.toml``, ``<manifest>``), both
sides of every conflict hunk are kept (ours then theirs) and the file is staged.
Any other conflicted file is refused with exit code 3: resolve it semantically
(an agent reads both sides and the spec), never by union. Lesson: a union merge
of a source file once produced a compiling but wrong main branch that had to be
reverted.

Lockfiles in ``merge.lockfiles`` are resolved by taking "theirs" and then
``merge.after_union`` is run (the project's ``<lockfile regenerate>`` command) so the tool regenerates
them. Duplicate ``key = value`` lines inside one table (the typical result of a
union of two added dependencies) are removed, keeping the first.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .util import Git, any_match

HUNK = re.compile(r"<<<<<<< [^\n]*\n(.*?)(?:\|\|\|\|\|\|\| [^\n]*\n.*?)?=======\n(.*?)>>>>>>> [^\n]*\n", re.DOTALL)


def union_text(text: str) -> Tuple[str, int]:
    count = 0

    def repl(m: "re.Match[str]") -> str:
        nonlocal count
        count += 1
        ours, theirs = m.group(1), m.group(2)
        # Keep each line once, in order (identical additions on both sides collapse).
        seen = set()
        out = []
        for line in ours.splitlines(True) + theirs.splitlines(True):
            key = line.strip()
            if key and key in seen:
                continue
            seen.add(key)
            out.append(line)
        return "".join(out)

    return HUNK.sub(repl, text), count


def dedupe_toml_keys(text: str) -> str:
    out, seen, table = [], set(), ""
    for line in text.splitlines():
        t = line.strip()
        if t.startswith("["):
            table = t
            seen = set()
        key = None
        if "=" in t and not t.startswith("#"):
            key = (table, t.split("=", 1)[0].strip())
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def resolve(root: Path, cfg: Dict[str, Any], files: List[str]) -> Tuple[int, List[str]]:
    git = Git(root)
    manifests = cfg["merge"]["union_manifests"]
    lockfiles = cfg["merge"].get("lockfiles") or []
    messages: List[str] = []
    if not files:
        files = git.unmerged_files()
    if not files:
        return 0, ["nothing to resolve (no unmerged files)"]
    refused = [f for f in files if not any_match(f, manifests) and not any_match(f, lockfiles)]
    if refused:
        for f in refused:
            messages.append(f"REFUSE: {f} is not an additive manifest; resolve it semantically")
        return 3, messages
    for f in files:
        path = root / f
        if any_match(f, lockfiles):
            subprocess.run(["git", "-C", str(root), "checkout", "--theirs", "--", f], check=False)
            messages.append(f"lockfile {f}: took theirs (regenerate with merge.after_union)")
            continue
        text = path.read_text(encoding="utf-8")
        merged, n = union_text(text)
        if "<<<<<<<" in merged:
            messages.append(f"{f}: could not parse every conflict hunk")
            return 1, messages
        if f.endswith(".toml"):
            merged = dedupe_toml_keys(merged)
        path.write_text(merged, encoding="utf-8")
        git.run("add", "--", f)
        messages.append(f"{f}: union of {n} hunk(s)")
    after = cfg["merge"].get("after_union")
    if after:
        proc = subprocess.run(after, shell=True, cwd=str(root))
        if proc.returncode != 0:
            messages.append(f"after_union command failed (exit {proc.returncode}): {after}")
            return 1, messages
        for f in files:
            if any_match(f, lockfiles):
                git.run("add", "--", f)
    left = git.unmerged_files()
    if left:
        messages.append("UNRESOLVED: " + ", ".join(left))
        return 1, messages
    return 0, messages
