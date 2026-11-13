"""Secret scanning: a small built-in high-precision rule set (stdlib only) plus gitleaks
(>= 8.19) when it is installed and ``quality.secrets.gitleaks`` is true.

Findings are reported as rule id and location (``file:line`` or ``commit message``); the
matched text is never printed or stored. gitleaks runs with ``--redact`` and its JSON report
(written under ``<logs_dir>/quality/``) is read for ``RuleID``, ``File`` and ``StartLine`` only
and deleted right after.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import QualityError, Result, Violation, ci_mode, scratch_dir, vcs
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


def gitleaks(root: Path, cfg: Dict[str, Any], args: List[str], stdin: Optional[str] = None, in_message: bool = False) -> List[Violation]:
    """Run gitleaks with a redacted JSON report; return findings as violations."""
    report = scratch_dir(root, cfg) / f"gitleaks-{abs(hash((tuple(args), stdin))) % 10**8}.json"
    cmd = ["gitleaks", *args, "--redact", "--no-banner", "--exit-code", "1", "--report-format", "json", "--report-path", str(report)]
    try:
        proc = subprocess.run(cmd, cwd=str(root), input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=600)
        if proc.returncode not in (0, 1):
            raise QualityError(f"gitleaks exited {proc.returncode}: {proc.stderr.strip()[-400:]}")
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
        path = str(f.get("File", ""))
        if any_match(path, allow):
            continue
        line = f.get("StartLine")
        out.append(Violation("secret", path, int(line) if line else None, {"file": path, "message": f"gitleaks:{rid}"}, 1, f"gitleaks rule {rid}"))
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
        head = vcs.git(root, "rev-parse", "HEAD").strip()
        if head != base:
            res.violations += gitleaks(root, cfg, ["git", f"--log-opts={base}..HEAD", "."])
    else:
        gitleaks_note(cfg, res)
    return res.finish()
