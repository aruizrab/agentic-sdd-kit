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

Detached mode (for agents whose shell calls are time-limited)::

    sdd gates --detached            # starts the run in the background, prints the log path
    sdd gates --wait <log>          # blocks until the GATES_EXIT=<n> marker appears

Every log ends with the line ``GATES_EXIT=<code>``; never report a gate run
that has no such line (it is still running or was killed).
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

MARKER = "GATES_EXIT="


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

    return tool_env(cfg, root, extra)


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
            if missing and shutil.which(missing) is None:
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
    tee.write(f"{MARKER}{code}\n")
    tee.flush()
    if log_file:
        log_file.close()
    return code


def start_detached(root: Path, argv: List[str], log_path: Path) -> int:
    """Re-run this command in the background with output to ``log_path``."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as fh:
        fh.write(f"# detached gate run started; waiting for {MARKER}<code>\n")
    cmd = [sys.executable, "-I", str(Path(__file__).resolve().parent.parent / "sdd.py"), "gates", *argv, "--log", str(log_path), "--no-stdout"]
    with open(log_path, "a", encoding="utf-8") as fh:
        proc = subprocess.Popen(
            cmd, cwd=str(root), stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    print(json.dumps({"detached": True, "pid": proc.pid, "log": str(log_path), "marker": MARKER}))
    return 0


def wait(log_path: Path, poll_seconds: float, timeout: float) -> int:
    """Block until the marker line appears; print the summary; return its code."""
    deadline = time.monotonic() + timeout
    while True:
        if log_path.is_file():
            text = log_path.read_text(encoding="utf-8", errors="replace")
            for line in reversed(text.splitlines()):
                if line.startswith(MARKER):
                    code = int(line[len(MARKER):].strip() or "1")
                    start = text.rfind("+- gates summary")
                    print(text[start:] if start >= 0 else line, end="" if text.endswith("\n") else "\n")
                    return code
        if time.monotonic() > deadline:
            print(f"timed out waiting for {MARKER} in {log_path}; the run is still going or was killed", file=sys.stderr)
            return 124
        time.sleep(poll_seconds)
