"""Git hooks shipped in ``<tools_dir>/githooks/`` and their logic (``sdd hooks run <name>``).

``sdd hooks install`` sets ``core.hooksPath`` to the relative ``<tools_dir>/githooks`` in the
repository of the project root (``--root``, else the cwd; only when the user runs it). It
refuses when the cwd lies in another repository, when the hook scripts are missing there, and
when it would switch off hooks already in use: a ``core.hooksPath`` from any scope (local,
global, system) or executable hooks in the hooks directory git uses now. Hooks never stash, reset, check out, or write the index, the working tree or git config;
checks that need file content read the staged blob, commands run on the working tree. The only
files a hook writes are its notes in the git dir: the timing note ``sdd-hook-time`` and
``sdd-tests-green`` (test commands that passed since the branch's red-by-design test commit).

* pre-commit: secret scan of the staged added lines (built-in rules, then gitleaks when
  enabled and installed); ``sdd check size --staged``; ``quality.hooks.pre_commit`` steps whose
  ``glob`` matches a staged file outside the installed kit and the step's ``exclude`` globs
  (``{files}`` = those files, shell-quoted).
* commit-msg: secret scan of the message; merge commits (``MERGE_HEAD``) stop there; the
  Conventional Commit header; required trailers; the test step for the components of staged
  source and test files (skipped for ``skip_tests_for_types``; while the branch holds a
  red-by-design test commit, only a regression rejects); the time budget warning.
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
from . import QualityError, Result, ci_mode, kit_globs, langs, run_command, size, tail, tool_env, vcs
from . import commits, secrets
from ..util import any_match

HOOK_NAMES = ("pre-commit", "commit-msg", "pre-push")
TIME_NOTE = "sdd-hook-time"
GREEN_NOTE = "sdd-tests-green"


def hooks_rel(cfg: Dict[str, Any]) -> str:
    return cfg["paths"]["tools_dir"].rstrip("/") + "/githooks"


def _local_hooks_path(root: Path) -> str:
    return vcs.git(root, "config", "--local", "--get", "core.hooksPath", check=False).strip()


def _effective_hooks_path(root: Path) -> Tuple[str, str]:
    """(origin, value) of core.hooksPath over every scope (system, global, local, command line)."""
    out = vcs.git(root, "config", "--show-origin", "--get", "core.hooksPath", check=False).strip()
    origin, _, value = out.partition("\t")
    return (origin, value) if value else ("", "")


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
    else:
        origin, value = _effective_hooks_path(root)
        if value:
            found.append(f"core.hooksPath is set to {value!r} in {origin} (organisation-wide hooks?); a local value would shadow it")
    dirs = [_default_hooks_dir(root)]
    if not current:
        eff = vcs.git_path(root, "hooks")
        if eff.resolve() != dirs[0].resolve():
            dirs.append(eff)
    for d in dirs:
        active = _active_hooks(d)
        if active:
            found.append(f"executable hooks in {d}: {', '.join(active)} (git-lfs, pre-commit framework, ...)")
    if found and not force:
        print("hooks: NOT installed; setting core.hooksPath would switch off hooks already in use:", file=sys.stderr)
        for f in found:
            print(f"  - {f}", file=sys.stderr)
        print("Chain them instead (docs/ADAPTING.md, 'Chaining the git hooks'), or re-run with --force.", file=sys.stderr)
        return 1
    missing = [n for n in HOOK_NAMES if not (root / rel / n).is_file()]
    if missing:
        print(f"hooks: NOT installed: {rel}/ lacks {', '.join(missing)} in {root} (install the kit tools into this repository first); "
              "git would silently run no hooks", file=sys.stderr)
        return 1
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
    for n in secrets.binary_notes(root, cfg, "--cached"):
        hook.say(n)
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
        skip = kit_globs(cfg) + list(step.get("exclude") or [])
        files = [f for f in staged if (not glob or any_match(f, [glob])) and not any_match(f, skip)]
        if not files and (glob or "{files}" in step["command"]):
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
    relevant = [f for f in staged if not any_match(f, cfg["quality"]["generated"]) and not any_match(f, kit_globs(cfg))
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


def _red_test_commit(hook: _Hook) -> Optional[str]:
    """The newest red-by-design test commit (type in ``skip_tests_for_types``) in merge-base..HEAD."""
    try:
        base = vcs.merge_base(hook.root, hook.cfg)
    except QualityError:
        return None
    log = vcs.git(hook.root, "log", "--no-merges", "--format=%H%x00%s", f"{base}..HEAD", check=False)
    for row in log.splitlines():
        sha, _, subject = row.partition("\0")
        if commits.commit_type(subject) in hook.h["skip_tests_for_types"]:
            return sha
    return None


def _green_note(hook: _Hook, red: str) -> List[str]:
    """Test commands that passed since the red test commit ``red`` (per worktree, in the git dir)."""
    try:
        data = json.loads(vcs.git_path(hook.root, GREEN_NOTE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return list(data.get("green") or []) if isinstance(data, dict) and data.get("red") == red else []


def _record_green(hook: _Hook, red: str, cmd: str) -> None:
    green = _green_note(hook, red)
    if cmd in green:
        return
    try:
        vcs.git_path(hook.root, GREEN_NOTE).write_text(json.dumps({"red": red, "green": green + [cmd]}), encoding="utf-8")
    except OSError:
        pass


def _run_tests(hook: _Hook, cmds: List[str]) -> bool:
    """Run the test commands; reject only regressions (docs/design D-04).

    The Tests stage commits failing tests for every task as one red-by-design commit
    (``test(...)``), and implementers then commit task by task: the tests of the tasks not done
    yet are still red. While merge-base..HEAD holds such a commit and the failing command has
    not passed since it (a per-worktree note in the git dir), a failure is reported and the
    commit is accepted; the gates stay the authority. Timeouts always reject.
    """
    red = _red_test_commit(hook) if cmds else None
    deadline = time.monotonic() + float(hook.h["test_timeout"])
    for cmd in cmds:
        left = deadline - time.monotonic()
        if left <= 0:
            hook.say(f"tests: exceeded test_timeout {hook.h['test_timeout']} s; commit rejected")
            return False
        t0 = time.monotonic()
        hook.say(f"tests: $ {cmd}")
        rc, out, timed_out = run_command(cmd, hook.root, hook.env, left)
        hook.timed("tests", t0)
        if timed_out:
            hook.say(f"tests: timed out after {int(left)} s; commit rejected\n{tail(out, 20)}")
            return False
        if rc == 0:
            if red:
                _record_green(hook, red, cmd)
            continue
        if red and cmd not in _green_note(hook, red):
            hook.say(f"tests: failed (exit {rc}), but {red[:12]} on this branch committed red-by-design tests and this "
                     f"command has not passed since: accepted as tests of tasks not done yet (the gates stay the authority)\n"
                     f"{tail(out, 15)}")
            continue
        hook.say(f"tests: failed (exit {rc}); commit rejected" + (" (these tests passed earlier on this branch: a regression)" if red else "")
                 + f"\n{tail(out, 40)}")
        return False
    return True


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
        ok = _run_tests(hook, cmds)
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
