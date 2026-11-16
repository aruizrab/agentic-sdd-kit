#!/usr/bin/env python3
"""memguard.py: keep a host alive during unattended heavy test runs.

    python3 -I tools/sdd/scripts/memguard.py [--floor-mib 1536] [--ceiling-mib 0] [--interval 0.5]
        [--pattern '*/target/*' ...] [--log FILE] [--once] [--dry-run]

Every ``--interval`` seconds it reads MemAvailable from /proc/meminfo. Below ``--floor-mib``
it kills (SIGKILL) the largest process (resident memory) whose executable path matches a
``--pattern`` glob (default ``*/target/*``: test binaries in a build target directory) and
logs it, so the kernel's OOM killer never has to pick a victim itself (it once took the init
system and unrelated user services). Only processes the user may signal are candidates.
With ``--ceiling-mib`` > 0 it also kills every matching process above that resident size at
once (no legitimate test needs more; a runaway mutant grows by about a gigabyte a second, so the
ceiling acts before the host runs short). Kills are logged to ``--log``: a relative path is
resolved against the main checkout of the git repository the guard starts in (the same for
every worktree); default ``.wt/locks/memguard.log`` there, none outside a repository. A test
the guard kills during a mutation run is reported by ``sdd check mutation`` as killed by the
host, not caught (``quality.mutation.outcomes``).
Interpreted test runners (python, node) have the interpreter as executable and do not match:
cap those with the per-test memory limit instead (docs/PLAYBOOK.md §14). Linux only.
Standard library only; run it under nohup or as a user service for the length of the run.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

DEFAULT_PATTERNS = ["*/target/*"]


def mem_available_kib(meminfo: str) -> Optional[int]:
    for line in meminfo.splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1])
    return None


def rss_kib(status: str) -> int:
    for line in status.splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1])
    return 0  # kernel threads and zombies have no VmRSS


def pick_victim(proc: Path, patterns: List[str], skip: Tuple[int, ...] = ()) -> Optional[Tuple[int, int, str]]:
    """(pid, rss KiB, executable) of the largest matching process, or None."""
    return max(matching(proc, patterns, skip), key=lambda v: v[1], default=None)


def log(path: Optional[str], msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} memguard: {msg}"
    print(line, file=sys.stderr, flush=True)
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def matching(proc: Path, patterns: List[str], skip: Tuple[int, ...] = ()) -> List[Tuple[int, int, str]]:
    """(pid, rss KiB, executable) of every process whose executable matches a pattern."""
    out = []
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) in skip:
            continue
        try:
            exe = os.readlink(str(entry / "exe"))
            rss = rss_kib((entry / "status").read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        exe = exe[: -len(" (deleted)")] if exe.endswith(" (deleted)") else exe
        if any(fnmatch.fnmatchcase(exe, p) for p in patterns):
            out.append((int(entry.name), rss, exe))
    return out


def check_ceiling(proc: Path, ceiling_kib: int, patterns: List[str], log_path: Optional[str], dry_run: bool) -> List[int]:
    """Kill every matching process above the resident-size ceiling; returns the pids killed (or chosen)."""
    hits = []
    for pid, rss, exe in matching(proc, patterns, skip=(os.getpid(),)):
        if rss <= ceiling_kib:
            continue
        if not dry_run:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError as exc:
                log(log_path, f"cannot kill pid {pid} ({exe}): {exc}")
                continue
        log(log_path, f"ceiling {ceiling_kib // 1024} MiB: {'would kill' if dry_run else 'killed'} pid {pid} rss {rss // 1024} MiB {exe}")
        hits.append(pid)
    return hits


def default_log(arg: Optional[str], cwd: Path) -> Optional[str]:
    """``--log`` resolved: a relative path against the main checkout of the repository at ``cwd``
    (default ``.wt/locks/memguard.log`` there); an absolute path as given; None outside a repository
    without ``--log``."""
    if arg and os.path.isabs(arg):
        return arg
    try:
        p = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=str(cwd),
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        common = p.stdout.strip() if p.returncode == 0 else ""
    except OSError:
        common = ""
    if not common or not os.path.isabs(common):
        return str(cwd / arg) if arg else None
    path = Path(common).resolve().parent / (arg or ".wt/locks/memguard.log")
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


def check_once(proc: Path, floor_kib: int, patterns: List[str], log_path: Optional[str], dry_run: bool) -> Optional[int]:
    """Kill one victim if MemAvailable is under the floor; returns the pid killed (or chosen)."""
    avail = mem_available_kib((proc / "meminfo").read_text(encoding="utf-8"))
    if avail is None or avail >= floor_kib:
        return None
    victim = pick_victim(proc, patterns, skip=(os.getpid(),))
    if victim is None:
        log(log_path, f"MemAvailable {avail // 1024} MiB < floor {floor_kib // 1024} MiB, no process matches {patterns}")
        return None
    pid, rss, exe = victim
    action = "would kill" if dry_run else "killed"
    if not dry_run:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError as exc:
            log(log_path, f"cannot kill pid {pid} ({exe}): {exc}")
            return None
    log(log_path, f"MemAvailable {avail // 1024} MiB < floor {floor_kib // 1024} MiB: {action} pid {pid} rss {rss // 1024} MiB {exe}")
    return pid


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--floor-mib", type=int, default=1536, help="kill below this MemAvailable (default 1536)")
    ap.add_argument("--interval", type=float, default=0.5, help="seconds between checks (default 0.5)")
    ap.add_argument("--pattern", action="append", help="executable path glob of killable processes (repeatable; default */target/*)")
    ap.add_argument("--ceiling-mib", type=int, default=0, help="also kill any matching process above this resident size (0 = off)")
    ap.add_argument("--log", help="append each kill to this file (relative: in the main checkout; default .wt/locks/memguard.log there)")
    ap.add_argument("--once", action="store_true", help="check once and exit")
    ap.add_argument("--dry-run", action="store_true", help="log the victim, do not kill")
    ap.add_argument("--proc", default="/proc", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    proc, floor, patterns = Path(args.proc), args.floor_mib * 1024, args.pattern or DEFAULT_PATTERNS
    if not (proc / "meminfo").is_file():
        print("memguard: no /proc/meminfo (Linux only)", file=sys.stderr)
        return 2
    log_path = default_log(args.log, Path.cwd())
    log(log_path, f"start pid {os.getpid()} floor {args.floor_mib} MiB ceiling {args.ceiling_mib or 'off'} MiB patterns {patterns}")
    while True:
        if args.ceiling_mib > 0:
            check_ceiling(proc, args.ceiling_mib * 1024, patterns, log_path, args.dry_run)
        check_once(proc, floor, patterns, log_path, args.dry_run)
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
