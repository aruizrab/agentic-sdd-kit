"""Git hooks shipped in ``<tools_dir>/githooks/`` and their logic (``sdd hooks run <name>``).

``sdd hooks install`` sets ``core.hooksPath`` to the relative ``<tools_dir>/githooks`` in the
repository at the cwd (only when the user runs it) and refuses to switch off hooks already in
use. Hooks never stash, reset, check out, or write the index, the working tree or git config;
checks that need file content read the staged blob, commands run on the working tree. The only
file a hook writes is its timing note ``sdd-hook-time`` in the git dir.

* pre-commit: secret scan of the staged added lines (built-in rules, then gitleaks when
  enabled and installed); ``sdd check size --staged``; ``quality.hooks.pre_commit`` steps whose
  ``glob`` matches a staged file (``{files}`` = those files, shell-quoted).
* commit-msg: secret scan of the message; merge commits (``MERGE_HEAD``) stop there; the
  Conventional Commit header; required trailers; the test step for the components of staged
  source and test files (skipped for ``skip_tests_for_types``); the time budget warning.
* pre-push: ``sdd gates --fast``.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import stat
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .. import config as cfgmod
from . import QualityError, Result, ci_mode, langs, run_command, size, tail, tool_env, vcs
from . import commits, secrets
from ..util import any_match

HOOK_NAMES = ("pre-commit", "commit-msg", "pre-push")
TIME_NOTE = "sdd-hook-time"


def hooks_rel(cfg: Dict[str, Any]) -> str:
    return cfg["paths"]["tools_dir"].rstrip("/") + "/githooks"


def _local_hooks_path(root: Path) -> str:
    return vcs.git(root, "config", "--local", "--get", "core.hooksPath", check=False).strip()


def _default_hooks_dir(root: Path) -> Path:
    common = Path(vcs.git(root, "rev-parse", "--git-common-dir").strip())
    return (common if common.is_absolute() else root / common) / "hooks"


def _active_hooks(d: Path) -> List[str]:
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir()
                  if p.is_file() and not p.name.endswith(".sample") and os.stat(p).st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))


def install(root: Path, cfg: Dict[str, Any], force: bool) -> int:
    rel = hooks_rel(cfg)
    current = _local_hooks_path(root)
    if current == rel:
        print(f"hooks: already installed (core.hooksPath = {rel}); nothing changed")
        return 0
    found = []
    if current:
        found.append(f"core.hooksPath is already set to {current!r} (husky or another hook manager?)")
    active = _active_hooks(_default_hooks_dir(root))
    if active:
        found.append(f"executable hooks in {_default_hooks_dir(root)}: {', '.join(active)} (git-lfs, pre-commit framework, ...)")
    if found and not force:
        print("hooks: NOT installed; setting core.hooksPath would switch off hooks already in use:", file=sys.stderr)
        for f in found:
            print(f"  - {f}", file=sys.stderr)
        print("Chain them instead (docs/ADAPTING.md, 'Chaining the git hooks'), or re-run with --force.", file=sys.stderr)
        return 1
    if not (root / rel).is_dir():
        print(f"hooks: warning: {rel}/ does not exist in this checkout (install the kit tools first)", file=sys.stderr)
    vcs.git(root, "config", "--local", "core.hooksPath", rel)
    print(f"hooks: installed (core.hooksPath = {rel}); pre-commit, commit-msg and pre-push now run the SDD checks")
    return 0


def installed(root: Path, cfg: Dict[str, Any]) -> bool:
    """True when core.hooksPath is the kit dir, or every hook of the active dir calls ``hooks run <name>``."""
    if _local_hooks_path(root) == hooks_rel(cfg):
        return True
    active = vcs.git_path(root, "hooks") if not _local_hooks_path(root) else root / _local_hooks_path(root)
    for name in HOOK_NAMES:
        p = active / name
        try:
            if f"hooks run {name}" not in p.read_text(encoding="utf-8", errors="replace"):
                return False
        except OSError:
            return False
    return True


# ----------------------------------------------------------------------------
# running hooks
# ----------------------------------------------------------------------------


class _Hook:
    def __init__(self, name: str) -> None:
        self.name = name
        cwd = Path(os.getcwd())
        self.root = vcs.toplevel(cwd) or cwd
        self.cfg = cfgmod.load(self.root)
        problems = [p for p in cfgmod.validate(self.cfg) if p.startswith("quality") or p.startswith("unknown key 'quality")]
        if problems:
            raise QualityError("invalid quality configuration: " + "; ".join(problems))
        self.h = self.cfg["quality"]["hooks"]
        self.env = tool_env(self.cfg, self.root)
        self.steps: List[Tuple[str, float]] = []
        self.start = time.monotonic()

    def say(self, msg: str) -> None:
        print(f"[{self.name}] {msg}", file=sys.stderr)

    def timed(self, label: str, t0: float) -> None:
        self.steps.append((label, time.monotonic() - t0))

    def run_step(self, label: str, cmd: str, timeout: float) -> bool:
        t0 = time.monotonic()
        self.say(f"{label}: $ {cmd}")
        rc, out, timed_out = run_command(cmd, self.root, self.env, timeout)
        self.timed(label, t0)
        if timed_out:
            self.say(f"{label}: timed out after {int(timeout)} s; commit rejected\n{tail(out, 20)}")
            return False
        if rc != 0:
            self.say(f"{label}: failed (exit {rc}); commit rejected\n{tail(out, 40)}")
            return False
        return True

    def secret_report(self, found: List[Any]) -> bool:
        for v in found:
            self.say(f"secret detected: rule {v.key['message']} at {v.where()} (value not shown); remove it and rotate it if it was real")
        return not found


def _pre_commit(hook: _Hook) -> int:
    cfg, root = hook.cfg, hook.root
    t0 = time.monotonic()
    found = secrets.scan_added(cfg, vcs.staged_added_lines(root))
    if secrets.gitleaks_available(cfg):
        found += secrets.gitleaks(root, cfg, ["git", "--pre-commit", "--staged", "."])
    elif cfg["quality"]["secrets"]["gitleaks"]:
        res = Result("secrets")
        secrets.gitleaks_note(cfg, res)
        for n in res.notes:
            hook.say(n)
        found += res.violations
    hook.timed("secret scan", t0)
    ok = hook.secret_report([v for v in found if v.kind == "secret"])
    for v in found:
        if v.kind != "secret":
            hook.say(v.message)
            ok = False
    t0 = time.monotonic()
    res = size.run(root, cfg, staged=True)
    hook.timed("size --staged", t0)
    for v in res.failing():
        hook.say(f"size: {v.where()}: {v.message}")
        ok = False
    staged = vcs.staged_files(root)
    for step in hook.h["pre_commit"]:
        glob = step.get("glob")
        files = [f for f in staged if any_match(f, [glob])] if glob else staged
        if glob and not files:
            continue
        cmd = step["command"].replace("{files}", " ".join(shlex.quote(f) for f in files))
        ok = hook.run_step(step["name"], cmd, float(step.get("timeout", 300))) and ok
    _write_note(hook)
    return 0 if ok else 1


def _write_note(hook: _Hook) -> None:
    slowest = max(hook.steps, key=lambda s: s[1]) if hook.steps else ("", 0.0)
    try:
        vcs.git_path(hook.root, TIME_NOTE).write_text(json.dumps({
            "written": time.time(), "elapsed": time.monotonic() - hook.start, "slowest": [slowest[0], slowest[1]]}), encoding="utf-8")
    except OSError:
        pass


def _read_note(hook: _Hook) -> Optional[Dict[str, Any]]:
    p = vcs.git_path(hook.root, TIME_NOTE)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = None
    try:
        p.unlink()
    except OSError:
        pass
    if not data or time.time() - float(data.get("written", 0)) > 600:
        return None
    return data


def _test_commands(hook: _Hook, staged: List[str]) -> List[str]:
    cfg = hook.cfg
    relevant = [f for f in staged if not any_match(f, cfg["quality"]["generated"])
                and (size.measured(f, cfg) or langs.is_test_file(f, cfg))]
    if not relevant:
        return []
    tests = cfg["tests"]
    rx = cfg["components"].get("path_regex")
    comps: List[Optional[str]] = []
    for f in relevant:
        m = re.search(rx, f) if rx else None
        comp = m.group(1) if m else None
        if comp not in comps:
            comps.append(comp)
    if not tests.get("run_component") or None in comps:
        return [tests["run"]] if tests.get("run") else []
    return [tests["run_component"].replace("{component}", shlex.quote(c)) for c in comps if c]


def _commit_msg(hook: _Hook, msg_file: str) -> int:
    cfg, root = hook.cfg, hook.root
    p = Path(msg_file)
    if not p.is_absolute():
        p = Path(os.getcwd()) / p
    message = p.read_text(encoding="utf-8", errors="replace")
    t0 = time.monotonic()
    found = secrets.scan_message(cfg, commits.clean(message))
    if secrets.gitleaks_available(cfg):
        found += secrets.gitleaks(root, cfg, ["stdin"], stdin=commits.clean(message), in_message=True)
    elif cfg["quality"]["secrets"]["gitleaks"]:
        res = Result("secrets")
        secrets.gitleaks_note(cfg, res)
        found += res.violations
        for n in res.notes:
            hook.say(n)
    hook.timed("message secret scan", t0)
    if not hook.secret_report([v for v in found if v.kind == "secret"]) or any(v.kind != "secret" for v in found):
        for v in found:
            if v.kind != "secret":
                hook.say(v.message)
        return 1
    if vcs.git_path(root, "MERGE_HEAD").exists():
        hook.say("merge commit: format, trailer and test checks skipped (both sides were checked when committed)")
        return 0
    problems = commits.message_problems(message, cfg)
    for rule, detail in problems:
        hook.say(f"rejected ({rule}): {detail}")
    if problems:
        return 1
    ok = True
    if not hook.h["commit_tests"]:
        pass
    elif commits.commit_type(message) in hook.h["skip_tests_for_types"]:
        hook.say(f"test step skipped: commit type {commits.commit_type(message)!r} is in skip_tests_for_types (red-by-design test commit)")
    else:
        cmds = _test_commands(hook, vcs.staged_files(root))
        if not cmds:
            hook.say("test step skipped: no source or test files staged")
        deadline = time.monotonic() + float(hook.h["test_timeout"])
        for cmd in cmds:
            left = deadline - time.monotonic()
            if left <= 0 or not hook.run_step("tests", cmd, left):
                if left <= 0:
                    hook.say(f"tests: exceeded test_timeout {hook.h['test_timeout']} s; commit rejected")
                ok = False
                break
    note = _read_note(hook)
    mine = max(hook.steps, key=lambda s: s[1]) if hook.steps else ("", 0.0)
    total = time.monotonic() - hook.start + (float(note["elapsed"]) if note else 0.0)
    if total > float(hook.h["budget_seconds"]):
        slow = mine
        if note and float(note["slowest"][1]) > mine[1]:
            slow = (f"pre-commit: {note['slowest'][0]}", float(note["slowest"][1]))
        else:
            slow = (f"commit-msg: {mine[0]}", mine[1])
        hook.say(f"WARNING: hooks took {total:.1f} s, above the {hook.h['budget_seconds']} s budget; slowest step: {slow[0]} "
                 f"({slow[1]:.1f} s). Move slow checks to gates.fast (pre-push) or speed them up.")
    return 0 if ok else 1


def _pre_push(hook: _Hook) -> int:
    from .. import gates

    hook.say("running sdd gates --fast")
    code = gates.run(hook.cfg, hook.root, fast=True, only=None, log_path=None)
    if code != 0:
        hook.say("gates --fast failed; push blocked (fix the failures; never bypass the hooks)")
    return code


def run(name: str, args: List[str]) -> int:
    try:
        hook = _Hook(name)
        if name == "pre-commit":
            return _pre_commit(hook)
        if name == "commit-msg":
            if not args:
                print("[commit-msg] no message file argument", file=sys.stderr)
                return 2
            return _commit_msg(hook, args[0])
        if name == "pre-push":
            return _pre_push(hook)
    except (QualityError, cfgmod.ConfigError) as exc:
        print(f"[{name}] configuration or tool error (commit rejected): {exc}", file=sys.stderr)
        return 2
    print(f"unknown hook {name}; known: {', '.join(HOOK_NAMES)}", file=sys.stderr)
    return 2
