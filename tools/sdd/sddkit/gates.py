"""``sdd gates``: run the configured verification gates and print a summary.

Gates come from ``sdd.config.json``::

    "gates": {
      "fast": [{"name": "fmt", "command": "<formatter> --check", "timeout": 120}],
      "full": [{"name": "fmt", ...}, {"name": "test", "command": "<test runner>", "timeout": 1800,
                "optional": true, "skip_if_missing": "<program>"}]
    }

Three plans: ``fast`` (pre-push, every task), ``full`` (pipeline Gates stage, pull-request
CI) and ``nightly`` (full-repo coverage and mutation, long fuzzing, benchmarks); select one
with ``--plan`` (``--fast`` = ``--plan fast``).

Every gate runs even if an earlier one fails ("all gates, then the verdict");
the exit code is 1 if any gate failed. ``optional`` gates are skipped (not
failed) when their command exits 127 (tool missing) or when the program named
in ``skip_if_missing`` is not on PATH; a gate with ``skip_exit`` (an integer) is
skipped when its command exits with that code (the templates set ``"skip_exit": 3``
on the ``sdd check`` entries: 3 = required tool missing). The row names the
``install`` hint. In CI mode (``CI=true``) every such skip is a failure. A gate
that exceeds ``timeout`` seconds fails.

Detached mode (for agents whose shell calls are time-limited, and whose end of turn
would kill a foreground run)::

    sdd gates --detached --feature F011 --tag post-implement-1 [--fresh]
        # log <logs_dir>/gates/F011-post-implement-1-<sha7>[-dirty].log, runner in <log>.pid;
        # attaches to a live run of the same key, reuses a completed clean one, else starts
    sdd gates --wait <log>          # blocks until the GATES_EXIT=<n> marker appears

Every log ends with the line ``GATES_EXIT=<code>``; never report a gate run
that has no such line. ``--wait`` exits 124 when it timed out while the run is going
(wait again) and 125 when the run is incomplete (no marker, runner gone): re-run it with
``--fresh``; an incomplete run is not a gate failure.
"""

from __future__ import annotations

import json
import os
import queue
import secrets
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

MARKER = "GATES_EXIT="
NONCE_ENV = "SDD_GATES_NONCE"


def plan(cfg: Dict[str, Any], fast: bool, only: Optional[List[str]] = None, plan_name: Optional[str] = None) -> List[Dict[str, Any]]:
    plan_name = plan_name or ("fast" if fast else "full")
    gates = cfg["gates"].get(plan_name) or []
    if plan_name == "fast" and not gates:
        gates = cfg["gates"]["full"]
    if only:
        wanted = set(only)
        gates = [g for g in gates if g["name"] in wanted]
    return list(gates)


def _env(cfg: Dict[str, Any], root: Path, extra: Optional[Dict[str, str]]) -> Dict[str, str]:
    from .quality import tool_env

    env = tool_env(cfg, root, extra)
    env.pop(NONCE_ENV, None)  # a gate's own output must never be able to print the run's final marker
    return env


def _pump(stream, out: "queue.Queue[Optional[str]]") -> None:
    try:
        for line in iter(stream.readline, ""):
            out.put(line)
    finally:
        out.put(None)


def summary(results: List[Tuple[str, str, str, float]]) -> str:
    width = max([len(r[0]) for r in results] + [8])
    lines = ["", "+- gates summary " + "-" * 44]
    for name, outcome, detail, took in results:
        lines.append(f"| {name:<{width}} {outcome:<5} {took:>7.1f}s  {detail}".rstrip())
    failed = sum(1 for r in results if r[1] == "FAIL")
    lines.append(f"+- {len(results)} gate(s), {failed} failed")
    return "\n".join(lines) + "\n"


def _skip(name: str, detail: str, gate: Dict[str, Any], took: float) -> Tuple[str, str, str, float]:
    hint = gate.get("install")
    if hint and "install:" not in detail:
        detail += f" (install: {hint})"
    if os.environ.get("CI") == "true":
        return (name, "FAIL", f"{detail}; CI=true: a missing tool is a failure", took)
    return (name, "SKIP", detail, took)


def _skip_detail(output: str, rc: int) -> str:
    for line in reversed(output.splitlines()):
        if "not installed" in line:
            return line.split(": ", 1)[-1].strip() if line.count(": ") else line.strip()
    return f"skipped (exit {rc})"


def run(cfg: Dict[str, Any], root: Path, fast: bool, only: Optional[List[str]], log_path: Optional[Path], extra_env: Optional[Dict[str, str]] = None, quiet: bool = False, plan_name: Optional[str] = None) -> int:
    plan_name = plan_name or ("fast" if fast else "full")
    fast = plan_name == "fast"
    gates = plan(cfg, fast, only, plan_name)
    env = _env(cfg, root, extra_env)
    results: List[Tuple[str, str, str, float]] = []
    streams = [] if quiet and log_path else [sys.stdout]
    log_file = None
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = open(log_path, "w", encoding="utf-8", buffering=1)
        streams.append(log_file)

    class Tee:
        def write(self, s: str) -> int:
            for st in streams:
                st.write(s)
            return len(s)

        def flush(self) -> None:
            for st in streams:
                st.flush()

        def fileno(self) -> int:
            raise OSError("tee has no fileno")

    tee = Tee()
    if not gates:
        print("no gates configured in sdd.config.json (gates.fast / gates.full)", file=sys.stderr)
        code = 2
    else:
        for i, gate in enumerate(gates, start=1):
            tee.write(f"==> [{i}/{len(gates)}] {gate['name']}\n")
            tee.flush()
            # Subprocess output needs a real file: capture, then echo to all streams.
            start = time.monotonic()
            name = gate["name"]
            missing = gate.get("skip_if_missing")
            if missing and shutil.which(missing, path=env.get("PATH")) is None:
                results.append(_skip(name, f"{missing} not installed", gate, 0.0))
                continue
            gate_env = dict(env)
            for k, v in (gate.get("env") or {}).items():
                gate_env[k] = str(v)
            tee.write(f"    $ {gate['command']}\n")
            tee.flush()
            try:
                proc = subprocess.Popen(
                    gate["command"], shell=True, cwd=str(root), env=gate_env,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                )
                deadline = start + float(gate.get("timeout", 1800))
                assert proc.stdout is not None
                captured: List[str] = []
                lines: "queue.Queue[Optional[str]]" = queue.Queue()
                reader = threading.Thread(target=_pump, args=(proc.stdout, lines), daemon=True)
                reader.start()
                timed_out = False
                eof = False
                while not eof:
                    try:
                        line = lines.get(timeout=0.1)
                    except queue.Empty:
                        if time.monotonic() > deadline:
                            proc.kill()
                            timed_out = True
                            break
                        continue
                    if line is None:
                        eof = True
                    else:
                        tee.write(line)
                        captured.append(line)
                        del captured[:-40]
                tee.flush()
                rc = proc.wait()
                reader.join(timeout=1)
                proc.stdout.close()
            except OSError as exc:
                results.append((name, "FAIL", f"could not run: {exc}", time.monotonic() - start))
                continue
            took = time.monotonic() - start
            if timed_out:
                results.append((name, "FAIL", f"timed out after {int(gate.get('timeout', 1800))} s", took))
            elif rc == 0:
                results.append((name, "PASS", "", took))
            elif rc == 127 and gate.get("optional"):
                results.append(_skip(name, "tool not installed (exit 127)", gate, took))
            elif "skip_exit" in gate and rc == gate["skip_exit"]:
                results.append(_skip(name, _skip_detail("".join(captured), rc), gate, took))
            else:
                results.append((name, "FAIL", f"exit status {rc}", took))
        if fast:
            full_names = [g["name"] for g in cfg["gates"]["full"]]
            fast_names = {g["name"] for g in gates}
            for n in full_names:
                if n not in fast_names:
                    results.append((n, "SKIP", "--fast", 0.0))
        tee.write(summary(results))
        code = 1 if any(r[1] == "FAIL" for r in results) else 0
    nonce = os.environ.get(NONCE_ENV)
    tee.write(f"{MARKER}{code}" + (f" {nonce}" if nonce else "") + "\n")
    tee.flush()
    if log_file:
        log_file.close()
    return code


class GateRunError(Exception):
    """A detached run cannot be keyed (no git repository or no HEAD)."""


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _safe(part: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "-" for c in part) or "x"


def keyed_log(root: Path, logs_dir: Path, feature: str, tag: str) -> Path:
    """``<logs_dir>/gates/<feature>-<tag>-<sha7>[-dirty].log`` for the current HEAD.

    The tree is dirty when tracked files differ from HEAD; untracked files (tool output such
    as a coverage report) do not count.
    """
    head = _git(root, "rev-parse", "--short=7", "HEAD")
    if head.returncode != 0:
        raise GateRunError(f"cannot key the run on HEAD in {root}: {head.stderr.strip() or 'not a git repository'}")
    dirty = _git(root, "diff", "--quiet", "HEAD").returncode != 0
    name = f"{_safe(feature)}-{_safe(tag)}-{head.stdout.strip()}{'-dirty' if dirty else ''}.log"
    return logs_dir / "gates" / name


def _pid_file(log_path: Path) -> Path:
    return log_path.with_name(log_path.name + ".pid")


def _read_runner(log_path: Path) -> Optional[Dict[str, Any]]:
    try:
        rec = json.loads(_pid_file(log_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) else None


def runner_alive(rec: Optional[Dict[str, Any]]) -> bool:
    """True when the recorded pid exists AND runs the recorded command line (a reused pid does not count)."""
    if not rec:
        return False
    pid, cmdline = rec.get("pid"), rec.get("cmdline")
    if not isinstance(pid, int) or pid <= 0 or not isinstance(cmdline, list) or not cmdline:
        return False
    if Path("/proc/self").exists():
        try:
            raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        except OSError:
            return False
        return raw.decode("utf-8", "replace").split("\0")[:-1] == [str(c) for c in cmdline]
    try:
        out = subprocess.run(["ps", "-o", "args=", "-p", str(pid)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    except OSError:
        return False
    return out.returncode == 0 and out.stdout.strip() == " ".join(str(c) for c in cmdline)


def _marker_code(log_path: Path, nonce: Optional[str] = None) -> Optional[int]:
    """The exit code of the final marker line; with ``nonce`` only a marker carrying it counts
    (a gate's own output printing ``GATES_EXIT=0`` or a hand-written log never does)."""
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in reversed(text.splitlines()):
        if line.startswith(MARKER):
            parts = line[len(MARKER):].split()
            if nonce is not None and (len(parts) < 2 or parts[1] != nonce):
                continue
            try:
                return int(parts[0]) if parts else 1
            except ValueError:
                return 1
    return None


def _nonce(rec: Optional[Dict[str, Any]]) -> Optional[str]:
    n = rec.get("nonce") if rec else None
    return n if isinstance(n, str) and n else None


def _keep(log_path: Path) -> None:
    """Rename an existing log to ``<log>.<n>`` (n = 1, 2, ...) so its evidence survives a new run."""
    if not log_path.exists():
        return
    n = 1
    while log_path.with_name(f"{log_path.name}.{n}").exists():
        n += 1
    log_path.rename(log_path.with_name(f"{log_path.name}.{n}"))


def detached(root: Path, argv: List[str], log_path: Path, keyed: bool, fresh: bool) -> int:
    """Start the run in the background, or attach to the live run, or reuse a completed one.

    - a live runner for ``log_path`` is always attached to (one log never has two writers);
    - a keyed, clean log with its marker is reused unless ``fresh`` (``-dirty`` logs never are);
    - otherwise a new run starts; a keyed run first renames the old log to ``<log>.<n>``.
    Prints ``{"log", "state": "started|running|done", "pid", "exit"?}``.
    """
    rec = _read_runner(log_path)
    if runner_alive(rec):
        print(json.dumps({"log": str(log_path), "state": "running", "pid": rec["pid"]}))
        return 0
    # Reuse only a run this tool started: its runner record holds a nonce that the final marker repeats.
    code = _marker_code(log_path, _nonce(rec)) if _nonce(rec) else None
    if keyed and not fresh and code is not None and not log_path.name.endswith("-dirty.log"):
        print(json.dumps({"log": str(log_path), "state": "done", "pid": rec.get("pid") if rec else None, "exit": code}))
        return 0
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if keyed:
        _keep(log_path)
    nonce = secrets.token_hex(8)
    with open(log_path, "w", encoding="utf-8") as fh:
        fh.write(f"# detached gate run started (nonce {nonce}); waiting for {MARKER}<code> {nonce}\n")
    cmd = [sys.executable, "-I", str(Path(__file__).resolve().parent.parent / "sdd.py"), "gates", *argv, "--log", str(log_path), "--no-stdout"]
    with open(log_path, "a", encoding="utf-8") as fh:
        proc = subprocess.Popen(
            cmd, cwd=str(root), stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            start_new_session=True, env=dict(os.environ, **{NONCE_ENV: nonce}),
        )
    _pid_file(log_path).write_text(json.dumps({"pid": proc.pid, "cmdline": cmd, "nonce": nonce}) + "\n", encoding="utf-8")
    print(json.dumps({"log": str(log_path), "state": "started", "pid": proc.pid}))
    return 0


def wait(log_path: Path, poll_seconds: float, timeout: float) -> int:
    """Block until the marker line appears; print the summary; return its code.

    124: timed out while the run is still going; 125: incomplete, i.e. no marker and the
    recorded runner (``<log>.pid``) is no longer alive.
    """
    deadline = time.monotonic() + timeout
    while True:
        rec = _read_runner(log_path)
        code = _marker_code(log_path, _nonce(rec))
        if code is None:
            if rec and not runner_alive(rec):
                code = _marker_code(log_path, _nonce(rec))  # it may have finished between the two reads
                if code is None:
                    print(f"incomplete run: no {MARKER} line in {log_path} and its runner (pid {rec.get('pid')}) is gone; "
                          "re-run it (sdd gates --detached ... --fresh); it is not a gate failure", file=sys.stderr)
                    return 125
        if code is not None:
            text = log_path.read_text(encoding="utf-8", errors="replace")
            start = text.rfind("+- gates summary")
            print(text[start:] if start >= 0 else f"{MARKER}{code}", end="" if text.endswith("\n") or start < 0 else "\n")
            if start < 0:
                print()
            return code
        if time.monotonic() > deadline:
            print(f"timed out waiting for {MARKER} in {log_path}; the run is still going: wait again", file=sys.stderr)
            return 124
        time.sleep(poll_seconds)
