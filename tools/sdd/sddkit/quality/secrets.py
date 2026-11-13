"""Secret scanning: a small built-in high-precision rule set (stdlib only) plus gitleaks
(>= 8.19) when it is installed and ``quality.secrets.gitleaks`` is true.

The built-in rules read ``git diff --text --no-textconv``: a file git treats as binary (a NUL
byte, ``-diff`` or ``binary`` in .gitattributes) is still scanned. ``gitleaks git`` skips such
files, so each one's blob (the staged blob in the hook; every distinct blob the branch's commits
add in ``sdd check secrets``) is piped to ``gitleaks stdin`` with the same trusted config; a blob
that cannot be read or scanned fails as ``secret-unscanned`` unless ``allow_globs`` covers it.

Findings are reported as rule id and location (``file:line`` or ``commit message``); the
matched text is never printed or stored. gitleaks runs with ``--redact`` and its JSON report
(written under ``<logs_dir>/quality/``) is read for ``RuleID``, ``File`` and ``StartLine`` only
and deleted right after. gitleaks runs with an explicit ``--config``: the ``.gitleaks.toml`` of a
trusted commit (HEAD in the hook, the merge-base in ``sdd check secrets``) or its default rules,
never the scanned tree's own file; a ``.gitleaksignore`` that differs from the trusted commit
fails.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

from . import QualityError, Result, Violation, ci_mode, scratch_dir, vcs
from .protected import APPROVAL_ENV, approved
from ..util import any_match

BUILTIN_RULES: List[Tuple[str, str]] = [
    ("private-key", r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----"),
    ("aws-access-key-id", r"\b(?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16}\b"),
    ("github-token", r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,})\b"),
    ("gitlab-token", r"\bglpat-[A-Za-z0-9_-]{20,}"),
    ("slack-token", r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    ("stripe-live-key", r"\b(?:sk|rk)_live_[A-Za-z0-9]{20,}"),
    ("google-api-key", r"\bAIza[0-9A-Za-z_-]{35}\b"),
    ("anthropic-api-key", r"\bsk-ant-[A-Za-z0-9_-]{20,}"),
    ("npm-token", r"\bnpm_[A-Za-z0-9]{36}\b"),
]


def rules(cfg: Dict[str, Any]) -> List[Tuple[str, "re.Pattern[str]"]]:
    out = [(rid, re.compile(rx)) for rid, rx in BUILTIN_RULES]
    for p in cfg["quality"]["secrets"]["patterns"]:
        out.append((p["id"], re.compile(p["regex"])))
    return out


def scan_lines(cfg: Dict[str, Any], rows: Iterable[Tuple[str, int, str]]) -> List[Violation]:
    """rows: (location file or '', line, text). Returns violations without the matched text."""
    rs = rules(cfg)
    out = []
    for f, ln, text in rows:
        for rid, rx in rs:
            if rx.search(text):
                where = f or "commit message"
                out.append(Violation("secret", where, ln if f else None, {"file": where, "message": rid}, 1, f"secret rule {rid}"))
    return out


def scan_added(cfg: Dict[str, Any], added: Dict[str, List[Tuple[int, str]]]) -> List[Violation]:
    allow = cfg["quality"]["secrets"]["allow_globs"]
    rows = [(f, ln, text) for f, lines in added.items() if not any_match(f, allow) for ln, text in lines]
    return scan_lines(cfg, rows)


def scan_message(cfg: Dict[str, Any], message: str) -> List[Violation]:
    return scan_lines(cfg, [("", i + 1, line) for i, line in enumerate(message.splitlines())])


def gitleaks_available(cfg: Dict[str, Any]) -> bool:
    return bool(cfg["quality"]["secrets"]["gitleaks"]) and shutil.which("gitleaks") is not None


DEFAULT_GITLEAKS_CONFIG = "[extend]\nuseDefault = true\n"


def gitleaks_config(root: Path, cfg: Dict[str, Any], trusted_ref: str) -> Path:
    """gitleaks' configuration as of ``trusted_ref`` (HEAD in the hook, the merge-base in the check).

    gitleaks reads ``.gitleaks.toml`` from the scanned tree, so a branch could allowlist every
    path; an explicit ``--config`` wins over that file."""
    text = vcs.show(root, trusted_ref, ".gitleaks.toml") if trusted_ref else None
    p = scratch_dir(root, cfg) / "gitleaks-config.toml"
    p.write_text(text if text is not None else DEFAULT_GITLEAKS_CONFIG, encoding="utf-8")
    return p


def gitleaks_ignore_problem(root: Path, cfg: Dict[str, Any], trusted_ref: str) -> List[Violation]:
    """gitleaks always honours ``.gitleaksignore`` in the scanned tree: one that differs from
    ``trusted_ref`` fails (an owner approves with ``SDD_ALLOW_PROTECTED=1``; a configuration
    cannot rename that variable)."""
    if approved():
        return []
    p = root / ".gitleaksignore"
    now = p.read_text(encoding="utf-8", errors="replace") if p.is_file() else None
    before = vcs.show(root, trusted_ref, ".gitleaksignore") if trusted_ref else None
    if now == before or (not now and not before):
        return []
    return [Violation("secret-config", ".gitleaksignore", None, {"file": ".gitleaksignore", "message": "changed"}, 1,
                      f".gitleaksignore differs from {trusted_ref[:12]}: it would hide gitleaks findings; change it on the main "
                      f"branch (owner approval: {APPROVAL_ENV}=1)")]


def gitleaks(root: Path, cfg: Dict[str, Any], args: List[str], stdin: Optional[Union[str, bytes]] = None, in_message: bool = False,
             trusted_ref: str = "HEAD", path: Optional[str] = None) -> List[Violation]:
    """Run gitleaks with a redacted JSON report and the trusted configuration; return findings as violations.

    ``path`` names the file whose content arrives on stdin (gitleaks reports no file for stdin)."""
    report = scratch_dir(root, cfg) / f"gitleaks-{abs(hash((tuple(args), stdin, path))) % 10**8}.json"
    conf = gitleaks_config(root, cfg, trusted_ref)
    cmd = ["gitleaks", *args, "--config", str(conf), "--redact", "--no-banner", "--exit-code", "1", "--report-format", "json",
           "--report-path", str(report)]
    data_in = stdin.encode("utf-8") if isinstance(stdin, str) else stdin
    try:
        try:
            proc = subprocess.run(cmd, cwd=str(root), input=data_in, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise QualityError(f"gitleaks did not run: {exc}")
        if proc.returncode not in (0, 1):
            raise QualityError(f"gitleaks exited {proc.returncode}: {proc.stderr.decode('utf-8', 'replace').strip()[-400:]}")
        try:
            data = json.loads(report.read_text(encoding="utf-8")) if report.is_file() else []
        except json.JSONDecodeError as exc:
            raise QualityError(f"unparsable gitleaks report: {exc}")
    finally:
        if report.exists():
            report.unlink()
    allow = cfg["quality"]["secrets"]["allow_globs"]
    out = []
    for f in data or []:
        rid = str(f.get("RuleID", "gitleaks"))
        if in_message:
            out.append(Violation("secret", "commit message", None, {"file": "commit message", "message": f"gitleaks:{rid}"}, 1, f"gitleaks rule {rid}"))
            continue
        file = path if path is not None else str(f.get("File", ""))
        if any_match(file, allow):
            continue
        line = f.get("StartLine")
        out.append(Violation("secret", file, int(line) if line else None, {"file": file, "message": f"gitleaks:{rid}"}, 1, f"gitleaks rule {rid}"))
    return out


def gitleaks_blobs(root: Path, cfg: Dict[str, Any], blobs: List[Tuple[str, str]], trusted_ref: str) -> List[Violation]:
    """Scan blobs that ``gitleaks git`` skips (paths git treats as binary: a NUL byte, ``-diff`` or
    ``binary`` in .gitattributes) with ``gitleaks stdin``. ``blobs``: (path, blob id). A blob that
    cannot be read or scanned fails closed as ``secret-unscanned``."""
    allow = cfg["quality"]["secrets"]["allow_globs"]
    out: List[Violation] = []
    for path, oid in blobs:
        if any_match(path, allow):
            continue
        data = vcs.blob(root, oid)
        problem = "" if data is not None else f"cannot read blob {oid}"
        if data is not None:
            try:
                out += gitleaks(root, cfg, ["stdin"], stdin=data, trusted_ref=trusted_ref, path=path)
            except QualityError as exc:
                problem = str(exc)
        if problem:
            out.append(Violation("secret-unscanned", path, None, {"file": path, "message": "unscanned"}, 1,
                                 f"{path}: binary to git and gitleaks could not scan it ({problem}); secret scan incomplete"))
    return out


def gitleaks_note(cfg: Dict[str, Any], res: Result) -> None:
    """Record a SKIP note for a missing gitleaks (a failure in CI mode)."""
    if not cfg["quality"]["secrets"]["gitleaks"]:
        return
    msg = "gitleaks not installed (install: gitleaks >= 8.19 from your package manager or its release binaries); built-in rules only"
    if ci_mode():
        res.violations.append(Violation("secret-tool", "", None, {"file": "", "message": "gitleaks missing"}, 1, msg + " — CI=true: failure"))
    else:
        res.notes.append("SKIP " + msg)


def check(root: Path, cfg: Dict[str, Any]) -> Result:
    """``sdd check secrets``: added lines since the merge-base (working tree) and the commits' history."""
    res = Result("secrets")
    base = vcs.merge_base(root, cfg)
    res.violations += scan_added(cfg, vcs.added_lines(root, base))
    if gitleaks_available(cfg):
        res.violations += gitleaks_ignore_problem(root, cfg, base)
        head = vcs.git(root, "rev-parse", "HEAD").strip()
        if head != base:
            res.violations += gitleaks(root, cfg, ["git", f"--log-opts={base}..{head}", "."], trusted_ref=base)
            res.violations += gitleaks_blobs(root, cfg, vcs.binary_blobs(root, base, head, cfg["quality"]["secrets"]["allow_globs"]), base)
    else:
        gitleaks_note(cfg, res)
    return res.finish()
