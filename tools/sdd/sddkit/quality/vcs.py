"""Git helpers for the quality checks: base ref, merge-base, changed and staged lines,
tracked and staged files, staged blobs and the current feature. Read-only: nothing here
writes the index, the working tree or git config."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from . import QualityError
from ..util import any_match


def git(root: Path, *args: str, check: bool = True, input_text: Optional[str] = None) -> str:
    proc = subprocess.run(
        ["git", "-c", "core.quotePath=false", "-C", str(root), *args],
        input=input_text, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
    )
    if check and proc.returncode != 0:
        raise QualityError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def git_ok(root: Path, *args: str) -> bool:
    return subprocess.run(["git", "-C", str(root), *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def toplevel(cwd: Path) -> Optional[Path]:
    proc = subprocess.run(["git", "-C", str(cwd), "rev-parse", "--show-toplevel"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    return Path(proc.stdout.strip()) if proc.returncode == 0 else None


def main_ref(root: Path, cfg: Dict[str, Any]) -> str:
    main = cfg["features"].get("main_branch") or "main"
    if git_ok(root, "rev-parse", "--verify", "-q", f"refs/heads/{main}"):
        return main
    return f"origin/{main}"


def base_ref(root: Path, cfg: Dict[str, Any]) -> str:
    """``SDD_BASE_REF``, else ``quality.base_ref`` (not with ``CI=true``: the branch under test
    must not pick its own reference point), else the local main branch, else origin/<main>."""
    env = os.environ.get("SDD_BASE_REF")
    if env:
        return env
    configured = (cfg.get("quality") or {}).get("base_ref")
    if configured and os.environ.get("CI") != "true":
        return configured
    return main_ref(root, cfg)


def merge_base(root: Path, cfg: Dict[str, Any]) -> str:
    ref = base_ref(root, cfg)
    proc = subprocess.run(["git", "-C", str(root), "merge-base", "HEAD", ref], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise QualityError(f"no merge-base between HEAD and {ref!r} (set SDD_BASE_REF or quality.base_ref; CI needs full history)")
    mb = proc.stdout.strip()
    if not os.environ.get("SDD_BASE_REF") and ref != main_ref(root, cfg):
        # A configured base that resolves to HEAD would hide every commit of the branch.
        head = git(root, "rev-parse", "HEAD").strip()
        main = main_ref(root, cfg)
        if mb == head and git_ok(root, "rev-parse", "--verify", "-q", main) and not git_ok(root, "merge-base", "--is-ancestor", "HEAD", main):
            raise QualityError(f"quality.base_ref {ref!r} resolves to HEAD while HEAD has commits not on {main}: "
                               f"every change would be hidden (set SDD_BASE_REF to the target branch)")
    return mb


_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
_ESC = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, '"': 34, "\\": 92}


def unquote_path(name: str) -> str:
    """A path as git prints it in a diff header: C-quoted (``"b/q\\"uote.txt"``) or followed by a
    TAB when it holds a space. Returns the plain path, still with its ``a/``/``b/`` prefix."""
    if name.endswith("\t"):
        name = name.rstrip("\t")
    if len(name) < 2 or not (name.startswith('"') and name.endswith('"')):
        return name
    out = bytearray()
    body, i = name[1:-1], 0
    while i < len(body):
        c = body[i]
        if c == "\\" and i + 1 < len(body):
            nxt = body[i + 1]
            if nxt in "0123" and re.match(r"[0-7]{3}", body[i + 1:i + 4]):
                out.append(int(body[i + 1:i + 4], 8))
                i += 4
                continue
            out.append(_ESC.get(nxt, ord(nxt)))
            i += 2
            continue
        out += c.encode("utf-8")
        i += 1
    return out.decode("utf-8", errors="replace")


def parse_diff(text: str) -> Dict[str, List[Tuple[int, str]]]:
    """Added lines per new path from a ``git diff -U0`` output: {path: [(line, text)]}."""
    out: Dict[str, List[Tuple[int, str]]] = {}
    path: Optional[str] = None
    line = 0
    in_hunk = False
    for raw in text.split("\n"):
        if raw.startswith("diff --git "):
            path, in_hunk = None, False
            continue
        if not in_hunk and raw.startswith("+++ "):
            target = unquote_path(raw[4:])
            path = None if target == "/dev/null" else (target[2:] if target.startswith("b/") else target)
            if path is not None:
                out.setdefault(path, [])
            continue
        m = _HUNK.match(raw)
        if m:
            in_hunk = True
            line = int(m.group(1))
            continue
        if in_hunk and path is not None and raw.startswith("+"):
            out[path].append((line, raw[1:]))
            line += 1
    return out


def _diff(root: Path, *args: str) -> str:
    # --text --no-textconv: a file git treats as binary (a NUL byte, `-diff` or `binary` in
    # .gitattributes) or shows through a textconv driver is still scanned as its real bytes.
    # T: a type change (symlink -> file) adds content too.
    return git(root, "diff", "-U0", "--no-color", "--no-ext-diff", "--text", "--no-textconv", "--no-renames", "--diff-filter=ACMRT", *args)


def binary_paths(root: Path, *args: str) -> List[str]:
    """Added or changed paths git treats as binary (``git diff --numstat`` prints ``-``)."""
    out = []
    for row in git(root, "diff", "--numstat", "-z", "--no-ext-diff", "--no-renames", "--diff-filter=ACMRT", *args).split("\0"):
        parts = row.split("\t", 2)
        if len(parts) == 3 and parts[0] == "-" and parts[1] == "-":
            out.append(parts[2])
    return out


EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def binary_blob_ids(root: Path, *args: str) -> List[Tuple[str, str]]:
    """[(path, blob id)] of the added or changed paths git treats as binary in ``git diff <args>``
    (``--cached``: the staged blobs). Blob ids come from ``--raw``, never from a ``<rev>:<path>``
    spec, which git can misread (``:0:x.dat`` is stage 0 of ``x.dat``)."""
    binary = set(binary_paths(root, *args))
    if not binary:
        return []
    tokens = git(root, "diff", "--raw", "-z", "--no-abbrev", "--no-ext-diff", "--no-renames", "--diff-filter=ACMRT", *args).split("\0")
    out = []
    for meta, path in zip(tokens[::2], tokens[1::2]):
        fields = meta.split()
        if meta.startswith(":") and len(fields) >= 4 and path in binary:
            out.append((path, fields[3]))
    return out


def binary_blobs(root: Path, base: str, head: str, skip: List[str]) -> List[Tuple[str, str]]:
    """Every distinct blob a commit in ``base..head`` adds or changes at a path git treats as
    binary and not matching ``skip`` (``allow_globs``), oldest first: [(path, blob id)].
    Each commit is compared with its first parent, so a secret added and removed again on the
    branch is still found. Skipped paths are dropped before deduplication: a blob first added at
    an allowed path is still scanned where it is copied to."""
    out: List[Tuple[str, str]] = []
    seen: Set[str] = set()
    for row in git(root, "rev-list", "--reverse", "--parents", f"{base}..{head}").splitlines():
        shas = row.split()
        if not shas:
            continue
        for p, oid in binary_blob_ids(root, shas[1] if len(shas) > 1 else EMPTY_TREE, shas[0]):
            if any_match(p, skip) or oid in seen:
                continue
            seen.add(oid)
            out.append((p, oid))
    return out


def changed_lines(root: Path, base: str) -> Dict[str, Set[int]]:
    """Lines added or changed in the working tree relative to ``base`` (tracked files)."""
    return {p: {ln for ln, _ in rows} for p, rows in parse_diff(_diff(root, base)).items()}


def added_lines(root: Path, base: str) -> Dict[str, List[Tuple[int, str]]]:
    return parse_diff(_diff(root, base))


def staged_added_lines(root: Path) -> Dict[str, List[Tuple[int, str]]]:
    return parse_diff(_diff(root, "--cached"))


def diff_text(root: Path, base: str) -> str:
    return git(root, "diff", "--no-color", "--no-ext-diff", base)


def tracked_files(root: Path) -> List[str]:
    return sorted({p for p in git(root, "ls-files", "-z").split("\0") if p})


def staged_files(root: Path) -> List[str]:
    return [p for p in git(root, "diff", "--cached", "--name-only", "-z", "--diff-filter=ACMRT").split("\0") if p]


def staged_content(root: Path, path: str) -> Optional[str]:
    proc = subprocess.run(["git", "-C", str(root), "show", f":{path}"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    return proc.stdout.decode("utf-8", errors="replace") if proc.returncode == 0 else None


def write_blobs(root: Path, files: Dict[str, Path]) -> Set[str]:
    """Write each blob id's raw bytes to its file with one ``git cat-file --batch`` (streamed, never
    held in memory); returns the ids written (a missing id is left out)."""
    if not files:
        return set()
    done: Set[str] = set()
    req = files[next(iter(files))].parent / "cat-file-input"
    req.write_text("".join(f"{oid}\n" for oid in files), encoding="ascii")
    with req.open("rb") as inp:
        proc = subprocess.Popen(["git", "-C", str(root), "cat-file", "--batch"], stdin=inp, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        assert proc.stdout is not None
        for oid in files:
            header = proc.stdout.readline().decode("ascii", "replace").split()
            if len(header) != 3 or header[1] != "blob":
                continue  # "<id> missing" / "ambiguous": no body follows
            left = int(header[2])
            with files[oid].open("wb") as fh:
                while left:
                    chunk = proc.stdout.read(min(left, 1 << 20))
                    if not chunk:
                        break
                    fh.write(chunk)
                    left -= len(chunk)
            proc.stdout.read(1)  # the newline after the body
            if not left and header[0] == oid:
                done.add(oid)
        proc.stdout.close()
        proc.wait()
    req.unlink()
    return done


def show(root: Path, ref: str, path: str) -> Optional[str]:
    proc = subprocess.run(["git", "-C", str(root), "show", f"{ref}:{path}"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    return proc.stdout.decode("utf-8", errors="replace") if proc.returncode == 0 else None


def git_path(root: Path, name: str) -> Path:
    p = Path(git(root, "rev-parse", "--git-path", name).strip())
    return p if p.is_absolute() else root / p


def current_feature(root: Path, cfg: Dict[str, Any]) -> Optional[str]:
    """``SDD_FEATURE``, else parsed from the branch ``<branch_prefix><id_prefix><digits>-<slug>``."""
    env = os.environ.get("SDD_FEATURE")
    if env:
        return env
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD", check=False).strip()
    f = cfg["features"]
    m = re.match(re.escape(f["branch_prefix"]) + "(" + re.escape(f["id_prefix"]) + r"\d+)(?:-|$)", branch)
    return m.group(1) if m else None


def feature_dir(root: Path, cfg: Dict[str, Any], fid: str) -> Optional[Path]:
    digits = re.sub(r"\D", "", fid)
    if not digits:
        return None
    base = root / cfg["paths"]["features_dir"]
    if not base.is_dir():
        return None
    for d in sorted(base.iterdir()):
        m = re.match(r"(\d+)-", d.name)
        if d.is_dir() and m and int(m.group(1)) == int(digits):
            return d
    return None
