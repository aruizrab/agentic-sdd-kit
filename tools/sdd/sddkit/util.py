"""Small shared helpers: glob matching, git access, Markdown stripping."""

from __future__ import annotations

import fnmatch
import re
import subprocess
from pathlib import Path
from typing import Iterable, List, Optional


def _glob_to_regex(glob: str) -> str:
    """Translate a path glob to a regex. ``**`` spans directories, ``*`` and ``?`` do not."""
    out = []
    i = 0
    while i < len(glob):
        c = glob[i]
        if c == "*":
            if glob[i : i + 2] == "**":
                i += 2
                if glob[i : i + 1] == "/":
                    i += 1
                    out.append("(?:.*/)?")
                else:
                    out.append(".*")
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[":
            j = glob.find("]", i)
            if j < 0:
                out.append(re.escape(c))
            else:
                out.append(glob[i : j + 1])
                i = j
        else:
            out.append(re.escape(c))
        i += 1
    return "^" + "".join(out) + "$"


def glob_match(path: str, glob: str) -> bool:
    """Match a relative, slash-separated path against a glob.

    ``**/`` matches zero or more directories, so ``**/tests/**`` matches
    ``tests/x.rs`` as well as ``crates/a/tests/x.rs``. A glob without a slash
    matches the file name anywhere (``*.rs``), like ``.gitignore`` does.
    """
    path = path.replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    if "/" not in glob:
        return fnmatch.fnmatchcase(path.rsplit("/", 1)[-1], glob)
    return re.match(_glob_to_regex(glob), path) is not None


def any_match(path: str, globs: Iterable[str]) -> Optional[str]:
    for g in globs:
        if glob_match(path, g):
            return g
    return None


def strip_html_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)


def strip_code(text: str) -> str:
    """Remove fenced code blocks and inline code spans (keeps line count stable for fences)."""
    text = re.sub(r"```.*?```", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.DOTALL)
    return re.sub(r"`[^`\n]*`", "", text)


def rel_to(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


class Git:
    """Thin, read-mostly git wrapper (never runs destructive commands)."""

    def __init__(self, repo: Path) -> None:
        self.repo = Path(repo)

    def run(self, *args: str, check: bool = True) -> str:
        proc = subprocess.run(
            ["git", "-C", str(self.repo), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if check and proc.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
        return proc.stdout

    def ok(self, *args: str) -> bool:
        proc = subprocess.run(
            ["git", "-C", str(self.repo), *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return proc.returncode == 0

    def toplevel(self) -> Optional[Path]:
        try:
            return Path(self.run("rev-parse", "--show-toplevel").strip())
        except RuntimeError:
            return None

    def show(self, ref: str, path: str) -> Optional[str]:
        proc = subprocess.run(
            ["git", "-C", str(self.repo), "show", f"{ref}:{path}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return proc.stdout if proc.returncode == 0 else None

    def ls_dirs(self, ref: str, prefix: str) -> List[str]:
        out = self.run("ls-tree", "-d", "--name-only", ref, prefix.rstrip("/") + "/", check=False)
        return sorted(line.rstrip("/") for line in out.splitlines() if line.strip())

    def is_ancestor(self, ref: str, of: str) -> bool:
        return self.ok("merge-base", "--is-ancestor", ref, of)

    def resolve(self, *candidates: str) -> Optional[str]:
        for ref in candidates:
            if ref and self.ok("rev-parse", "--verify", "-q", f"{ref}^{{commit}}"):
                return ref
        return None

    def branches(self, pattern: str) -> List[str]:
        out = self.run("for-each-ref", "--format=%(refname:short)", pattern, check=False)
        return [line.strip() for line in out.splitlines() if line.strip()]

    def commit_time(self, ref: str) -> int:
        out = self.run("log", "-1", "--format=%ct", ref, check=False).strip()
        return int(out) if out.isdigit() else 0

    def subjects(self, rng: str) -> List[str]:
        return self.run("log", "--format=%s", rng, check=False).splitlines()

    def unmerged_files(self) -> List[str]:
        return [l for l in self.run("diff", "--name-only", "--diff-filter=U", check=False).splitlines() if l]
