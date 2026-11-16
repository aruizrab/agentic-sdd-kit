"""Claude Code hook logic (``sdd hook <name>``), driven by ``sdd.config.json``.

The ``.claude/hooks/*.sh`` wrappers pipe the hook JSON from stdin into these
functions. Keeping the logic here makes it unit-testable and language-neutral.

Hooks:

* ``protect-paths`` (PreToolUse Edit|Write|MultiEdit|NotebookEdit): exit 2 for
  ``protected_paths`` (always), the kit's hooks and checkers
  (``<tools_dir>/{githooks/**,sddkit/**,scripts/**,sdd.py}``, every subagent, so role fallback to
  ``general-purpose`` cannot lift it), ``append_only_paths`` (only listed roles may
  *create* new files), ``role_denied_paths`` and ``role_write_scopes``.
* ``bash-guard`` (PreToolUse Bash): best-effort block of shell commands that
  would modify protected paths, and of destructive git commands in shared trees
  (``git checkout -- <path>``, ``git restore``, ``git clean``, ``git reset --hard``,
  ``git stash``): lesson 7, a read-only verifier once wiped another agent's
  edits with ``git checkout -- . && git clean -fd``. Also blocks bypassing the git
  hooks (``git_hook_bypass`` parses the git argv): ``--no-verify`` and its abbreviations on
  commit/push/merge, ``-n`` in a commit short-option cluster (``-nm``), setting or unsetting
  ``core.hooksPath`` (``git config``, ``git -c``/``--config-env`` after any global options,
  ``GIT_CONFIG_*`` variables), removing git's ``core`` config section; reading it stays allowed.
  Best effort: CI re-runs ``sdd check commits`` and ``sdd check secrets``.
* ``format`` (PostToolUse Edit|Write): runs the ``formatters`` entry whose glob
  matches the edited file; exit 2 with the output when it fails (a syntax
  error from the last edit).
* ``stop-check`` (Stop, SubagentStop): runs ``stop_check.command`` once when files
  matching ``stop_check.changed_globs`` changed; never loops. Light by design: not under the
  heavy-job lock (a stop hook must not wait in its queue), at ``stop_check.nice`` priority with
  ``stop_check.env`` (e.g. a low build parallelism), so it never competes with locked jobs
  (``stop_hook_active``), only for the roles listed.

Override for a human maintenance session: ``<override_env>=1 claude`` (default
``SDD_ALLOW_PROTECTED=1``). Agents never set it. Paths are resolved relative to
the git work tree that contains the file, so worktrees are handled; hooks act on
the input ``cwd``, never on ``CLAUDE_PROJECT_DIR`` (the main checkout).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import config as cfgmod
from .util import Git, any_match

DESTRUCTIVE_GIT = [
    (re.compile(r"^git(\s+-C\s+\S+)?\s+checkout\s+(?:.*\s)?--(\s|$)"), "git checkout -- <path> discards edits other agents may have made"),
    (re.compile(r"^git(\s+-C\s+\S+)?\s+checkout\s+\.(\s|$)"), "git checkout . discards every uncommitted edit in the tree"),
    (re.compile(r"^git(\s+-C\s+\S+)?\s+restore(\s|$)"), "git restore discards edits other agents may have made"),
    (re.compile(r"^git(\s+-C\s+\S+)?\s+clean(\s|$)"), "git clean deletes untracked files other agents may be creating"),
    (re.compile(r"^git(\s+-C\s+\S+)?\s+reset\s+(?:.*\s)?--(?:hard|merge)(\s|$)"), "git reset --hard/--merge rewrites the shared working tree"),
    (re.compile(r"^git(\s+-C\s+\S+)?\s+stash(?!\s+list)(\s|$)"), "git stash removes other agents' in-progress edits from the tree"),
    (re.compile(r"^git(\s+-C\s+\S+)?\s+push\s+(?:.*\s)?(?:-f|--force|--force-with-lease)(\s|$)"), "force-push is forbidden"),
]

_CONFIG_READ_FLAGS = {"--get", "--get-all", "--get-regexp", "-l", "--list", "--show-origin", "--show-scope", "get", "list"}


def hooks_path_change(cmd: str) -> Optional[str]:
    """Why a command would set or unset core.hooksPath (git config or git -c), else None."""
    for seg in _segments(cmd):
        try:
            words = shlex.split(seg)
        except ValueError:
            words = seg.split()
        if not words or words[0].rsplit("/", 1)[-1] != "git":
            continue
        i = 1
        while i < len(words) and words[i].startswith("-"):
            if words[i] == "-c" and i + 1 < len(words):
                if words[i + 1].lower().startswith("core.hookspath="):
                    return "git -c core.hooksPath=... switches the git hooks off"
                i += 2
                continue
            if words[i] == "-C" and i + 1 < len(words):
                i += 2
                continue
            if words[i].lower().startswith("-ccore.hookspath="):
                return "git -c core.hooksPath=... switches the git hooks off"
            i += 1
        if i >= len(words) or words[i] != "config":
            continue
        args = words[i + 1:]
        lowered = [a.lower() for a in args]
        if "core.hookspath" not in lowered:
            continue
        if any(a in ("--unset", "--unset-all", "unset") for a in lowered):
            return "unsetting core.hooksPath switches the SDD git hooks off"
        if any(a in _CONFIG_READ_FLAGS for a in lowered):
            continue
        k = lowered.index("core.hookspath")
        if k + 1 < len(args):
            return "changing core.hooksPath switches the SDD git hooks off (use `sdd hooks install`)"
    return None


def _read_input() -> Dict[str, Any]:
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return {"_invalid": True}
    return data if isinstance(data, dict) else {"_invalid": True}


def project_root(cwd: str, file_abs: Optional[Path] = None) -> Path:
    """The git work tree that holds the file (or the cwd), else CLAUDE_PROJECT_DIR."""
    probe = file_abs.parent if file_abs else Path(cwd)
    while not probe.is_dir() and probe != probe.parent:
        probe = probe.parent
    top = Git(probe).toplevel() if probe.is_dir() else None
    if top:
        return top
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(env) if env else Path(cwd)


def config_for(root: Path) -> Dict[str, Any]:
    """Config from the tree's root, else from the main checkout (worktrees share it)."""
    if (root / cfgmod.CONFIG_NAME).is_file():
        return cfgmod.load(root)
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env and (Path(env) / cfgmod.CONFIG_NAME).is_file():
        return cfgmod.load(Path(env))
    return cfgmod.load(root)


def rel_path(file: str, cwd: str) -> Tuple[str, Path, Path]:
    abs_path = Path(file) if os.path.isabs(file) else Path(cwd) / file
    abs_path = Path(os.path.normpath(str(abs_path)))
    root = project_root(cwd, abs_path)
    try:
        rel = abs_path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        rel = abs_path.as_posix()
    # A worktree seen from the main checkout: strip the worktree prefix.
    rel = re.sub(r"^\.claude/worktrees/[^/]+/", "", rel)
    return rel, abs_path, root


def protection_reason(rel: str, agent: str, tool: str, exists: bool, cfg: Dict[str, Any]) -> Optional[str]:
    for entry in cfg["protected_paths"]:
        if any_match(rel, [entry["glob"]]):
            return entry.get("reason") or f"{entry['glob']} is protected"
    for entry in cfg["append_only_paths"]:
        if any_match(rel, [entry["glob"]]):
            roles = entry.get("allowed_roles") or []
            if agent in roles and tool == "Write" and not exists:
                return None
            return entry.get("reason") or f"{entry['glob']} is append-only (only {', '.join(roles) or 'nobody'} may add NEW files)"
    # Every subagent, whatever its agent_type: under the pipeline's role fallback all roles run
    # as general-purpose, so a role-keyed denial alone would never fire. The main session (no
    # agent_type) may still patch the kit (lesson 14).
    if agent and any_match(rel, cfgmod.kit_checker_globs(cfg)):
        return f"no subagent may edit the kit's git hooks, checkers, scripts or pipeline ({rel}); report the problem, or propose a patch the owner applies"
    for role, globs in cfg["role_denied_paths"].items():
        if role == agent and any_match(rel, globs):
            return f"{agent} may not edit {rel} (owned by another role; report the problem instead of editing)"
    scopes = cfg["role_write_scopes"].get(agent)
    if scopes and not any_match(rel, scopes):
        return f"{agent} may only write under {', '.join(scopes)}"
    return None


def block(rel: str, reason: str, cfg: Dict[str, Any]) -> int:
    env = cfg.get("override_env") or "SDD_ALLOW_PROTECTED"
    sys.stderr.write(
        f"BLOCKED by SDD hook: {rel} is protected ({reason}).\n"
        "Do not work around this. If the change is genuinely required, stop and report it to the orchestrator; "
        f"a human can re-run with {env}=1. See the constitution's 'gates are law' article.\n"
    )
    return 2


def protect_paths(data: Optional[Dict[str, Any]] = None) -> int:
    data = _read_input() if data is None else data
    cwd = data.get("cwd") or os.getcwd()
    cfg = config_for(project_root(cwd))
    override = cfg.get("override_env") or "SDD_ALLOW_PROTECTED"
    if os.environ.get(override) == "1":
        return 0
    if data.get("_invalid"):
        sys.stderr.write("protect-paths: could not parse hook input JSON; refusing the edit.\n")
        return 2
    ti = data.get("tool_input") or {}
    file = ti.get("file_path") or ti.get("notebook_path") or ""
    if not file:
        return 0
    rel, abs_path, root = rel_path(file, cwd)
    cfg = config_for(root)
    reason = protection_reason(rel, data.get("agent_type") or "", data.get("tool_name") or "", abs_path.exists(), cfg)
    return block(rel, reason, cfg) if reason else 0


# ----------------------------------------------------------------------------
# bash-guard
# ----------------------------------------------------------------------------

MUTATING_ALL = {"rm", "rmdir", "unlink", "mv", "tee", "truncate", "shred", "chmod", "chown", "touch", "patch"}
MUTATING_LAST = {"cp", "install", "ln", "rsync"}
WRAPPERS = {"sudo", "env", "command", "nohup", "time", "exec", "xargs", "timeout"}


def _segments(cmd: str) -> List[str]:
    return [s for s in re.split(r"&&|\|\||;|\||\(|\)|\n", cmd) if s.strip()]


def command_targets(cmd: str) -> List[str]:
    """Paths a shell command would modify (best effort)."""
    targets: List[str] = []
    for m in re.finditer(r"[0-9&]*>>?\|?\s*([^\s;&|<>()]+)", cmd):
        if re.match(r"^&\d", m.group(0)) or m.group(1).startswith("&"):
            continue
        targets.append(m.group(1))
    for seg in _segments(cmd):
        try:
            words = shlex.split(seg)
        except ValueError:
            words = seg.split()
        i = 0
        while i < len(words) and (("=" in words[i] and not words[i].startswith("-")) or words[i] in WRAPPERS or (i > 0 and words[i].startswith("-"))):
            i += 1
        if i >= len(words):
            continue
        verb = words[i].rsplit("/", 1)[-1]
        args = words[i + 1 :]
        mode = None
        if verb in MUTATING_ALL:
            mode = "all"
        elif verb in MUTATING_LAST:
            mode = "last"
        elif verb == "dd":
            targets.extend(a[3:] for a in args if a.startswith("of="))
        elif verb in ("sed", "perl") and any(a.startswith("--in-place") or re.match(r"^-[a-zA-Z]*i", a) for a in args):
            mode = "all"
        elif verb == "find" and any(a in ("-delete", "-exec", "-execdir") for a in args):
            mode = "all"
        elif verb == "git" and args and args[0] in ("checkout", "restore", "rm", "mv", "apply", "am", "clean"):
            mode = "all"
        if mode == "all":
            targets.extend(a for a in args if not a.startswith("-"))
        elif mode == "last":
            plain = [a for a in args if not a.startswith("-")]
            if plain:
                targets.append(plain[-1])
    return [t.strip("\"'") for t in targets if t and not t.startswith("-") and not t.startswith("/dev/")]


_GIT_VALUE_OPTS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env", "--exec-path", "--super-prefix"}
_COMMIT_VALUE_SHORT = set("mFcCt")
_COMMIT_VALUE_LONG = {"--message", "--file", "--author", "--date", "--reuse-message", "--reedit-message", "--fixup", "--squash",
                      "--template", "--cleanup", "--trailer", "--pathspec-from-file", "--gpg-sign"}
_GIT_CONFIG_ENV = re.compile(r"^(GIT_CONFIG_PARAMETERS|GIT_CONFIG_COUNT|GIT_CONFIG_KEY_\d+|GIT_CONFIG_VALUE_\d+)=")


def _is_no_verify(arg: str) -> bool:
    """``--no-verify`` or an abbreviation git accepts (``--no-v``, ``--no-ver``, ...)."""
    name = arg.split("=", 1)[0]
    return len(name) >= len("--no-v") and "--no-verify".startswith(name)


def git_hook_bypass(cmd: str) -> Optional[str]:
    """Why a command would skip or switch off the git hooks, else None (best effort).

    Parses each segment's argv: leading ``VAR=value`` assignments and wrappers (``env``,
    ``command``, ...), git's global options in both ``--opt=value`` and ``--opt value`` forms,
    ``-c``/``--config-env`` and ``GIT_CONFIG_*`` assignments that set core.hooksPath,
    ``--no-verify`` and its abbreviations on commit/push/merge, ``-n`` in a commit short-option
    cluster (``-nm``, ``-anm``), and ``git config`` removing or renaming the ``core`` section.
    """
    for seg in _segments(cmd):
        try:
            words = shlex.split(seg)
        except ValueError:
            words = seg.split()
        i = 0
        while i < len(words):
            w = words[i]
            if _GIT_CONFIG_ENV.match(w) and "hookspath" in seg.lower():
                return "GIT_CONFIG_* environment variables that set core.hooksPath switch the git hooks off"
            if ("=" in w and not w.startswith("-")) or w in WRAPPERS or (i > 0 and words[i - 1] in WRAPPERS and w.startswith("-")):
                i += 1
                continue
            break
        if i >= len(words) or words[i].rsplit("/", 1)[-1] != "git":
            continue
        i += 1
        while i < len(words) and words[i].startswith("-"):
            opt, _, inline = words[i].partition("=")
            if opt in ("-c", "--config-env") or words[i].startswith("-c"):
                value = inline if inline else (words[i][2:] if opt.startswith("-c") and len(opt) > 2 else (words[i + 1] if i + 1 < len(words) else ""))
                if "hookspath" in value.lower():
                    return "git -c/--config-env core.hooksPath=... switches the git hooks off"
            if opt in _GIT_VALUE_OPTS and not inline and not (opt.startswith("-c") and len(opt) > 2):
                i += 2
            else:
                i += 1
        if i >= len(words):
            continue
        sub, args = words[i], words[i + 1:]
        if sub in ("commit", "push", "merge", "cherry-pick", "revert", "am", "rebase"):
            j = 0
            while j < len(args):
                a = args[j]
                if a == "--":
                    break
                if a.startswith("--"):
                    if _is_no_verify(a):
                        return f"git {sub} --no-verify skips the git hooks"
                    if sub == "commit" and a.split("=", 1)[0] in _COMMIT_VALUE_LONG and "=" not in a:
                        j += 1
                elif sub == "commit" and a.startswith("-") and len(a) > 1:
                    for ch in a[1:]:
                        if ch == "n":
                            return "git commit -n skips the git hooks"
                        if ch in _COMMIT_VALUE_SHORT:
                            if a.endswith(ch):
                                j += 1  # the value is the next word
                            break
                j += 1
        if sub == "config":
            low = [a.lower() for a in args]
            for k, a in enumerate(low):
                if a in ("--remove-section", "--rename-section", "remove-section", "rename-section") and k + 1 < len(low) and low[k + 1] == "core":
                    return "removing or renaming git's core section unsets core.hooksPath"
    return None


def destructive_git(cmd: str) -> Optional[str]:
    for seg in _segments(cmd):
        s = seg.strip()
        for rx, why in DESTRUCTIVE_GIT:
            if rx.match(s):
                return why
    return hooks_path_change(cmd) or git_hook_bypass(cmd)


def bash_guard(data: Optional[Dict[str, Any]] = None) -> int:
    data = _read_input() if data is None else data
    cwd = data.get("cwd") or os.getcwd()
    cfg = config_for(project_root(cwd))
    override = cfg.get("override_env") or "SDD_ALLOW_PROTECTED"
    if os.environ.get(override) == "1":
        return 0
    cmd = (data.get("tool_input") or {}).get("command") or ""
    if not cmd:
        return 0
    why = destructive_git(cmd)
    if why and os.environ.get("SDD_ALLOW_DESTRUCTIVE_GIT") != "1":
        sys.stderr.write(
            f"BLOCKED by SDD hook: {why}. Shared-tree safety: never run destructive git commands in a tree other "
            "agents may be editing. Undo only your own edits; for experiments use a detached throwaway worktree "
            "(git worktree add --detach <worktree_root>/exp-<unique> HEAD) and remove it afterwards.\n"
        )
        return 2
    agent = data.get("agent_type") or ""
    for tok in command_targets(cmd):
        rel, abs_path, root = rel_path(tok, cwd)
        tree_cfg = config_for(root)
        # A directory (existing or not) counts as everything below it.
        for candidate in (rel, rel.rstrip("/") + "/."):
            reason = protection_reason(candidate, agent, "Bash", True, tree_cfg)
            if reason:
                return block(rel, f"{reason} (via Bash: {cmd[:160]})", cfg)
    return 0


# ----------------------------------------------------------------------------
# format and stop-check
# ----------------------------------------------------------------------------


def format_file(data: Optional[Dict[str, Any]] = None) -> int:
    data = _read_input() if data is None else data
    cwd = data.get("cwd") or os.getcwd()
    file = (data.get("tool_input") or {}).get("file_path") or ""
    if not file:
        return 0
    rel, abs_path, root = rel_path(file, cwd)
    if not abs_path.is_file():
        return 0
    cfg = config_for(root)
    for entry in cfg["formatters"]:
        if any_match(rel, [entry["glob"]]) and not any_match(rel, entry.get("exclude") or []):
            cmd = entry["command"].replace("{file}", shlex.quote(str(abs_path)))
            proc = subprocess.run(cmd, shell=True, cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=float(entry.get("timeout", 60)))
            if proc.returncode == 127:
                return 0  # formatter not installed: never block on that
            if proc.returncode != 0:
                sys.stderr.write(f"formatter failed for {rel} (left unchanged). Usually a syntax error from the last edit:\n")
                sys.stderr.write("\n".join(proc.stdout.splitlines()[:25]) + "\n")
                return 2
            return 0
    return 0


def _lower_priority(nice: Any):
    """A preexec function that raises the child's niceness by ``nice`` (None when off or unsupported)."""
    if not isinstance(nice, int) or isinstance(nice, bool) or nice <= 0 or not hasattr(os, "nice"):
        return None

    def lower() -> None:
        try:
            os.nice(nice)
        except OSError:
            pass

    return lower


def stop_check(data: Optional[Dict[str, Any]] = None) -> int:
    data = _read_input() if data is None else data
    if os.environ.get("SDD_STOP_CHECK") == "0" or data.get("stop_hook_active"):
        return 0
    cwd = data.get("cwd") or os.getcwd()
    root = project_root(cwd)
    cfg = config_for(root)
    sc = cfg["stop_check"]
    if not sc.get("command"):
        return 0
    if data.get("hook_event_name") == "SubagentStop" and (data.get("agent_type") or "") not in (sc.get("roles") or []):
        return 0
    git = Git(root)
    changed = [l[3:] for l in git.run("status", "--porcelain", "-uall", check=False).splitlines() if len(l) > 3]
    globs = sc.get("changed_globs") or []
    if globs and not any(any_match(f, globs) for f in changed):
        return 0
    env = dict(os.environ)
    env.update({str(k): str(v) for k, v in (sc.get("env") or {}).items()})
    try:
        proc = subprocess.run(sc["command"], shell=True, cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                              timeout=float(sc.get("timeout", 240)), env=env, preexec_fn=_lower_priority(sc.get("nice")))
    except subprocess.TimeoutExpired:
        sys.stderr.write("stop-check: command timed out; not blocking.\n")
        return 0
    if proc.returncode == 0:
        return 0
    if proc.returncode == 127:
        sys.stderr.write(f"stop-check: command not found ({sc['command'].split()[0]!r}); not blocking. Fix stop_check.command in sdd.config.json.\n")
        return 0
    sys.stderr.write(f"stop-check failed in {root}. Fix these before finishing (or report them if they are outside your task):\n")
    lines = [l for l in proc.stdout.splitlines() if re.search(r"error|FAIL|Error", l)][:40] or proc.stdout.splitlines()[-20:]
    sys.stderr.write("\n".join(lines) + "\n")
    return 2


HOOKS = {
    "protect-paths": protect_paths,
    "bash-guard": bash_guard,
    "format": format_file,
    "stop-check": stop_check,
}
