"""Tests for WI2 pipeline robustness on the tools side: HEAD-keyed detached gate runs
(``sdd gates --detached --feature --tag [--fresh]``, ``--wait`` exit codes 124/125),
``build_cache.env`` (gates, config, doctor) and the reviewer agent checklists."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from helpers import KIT, GitRepo, make_tmp, rm, run_cli, test_config, write, write_config

from sddkit import config as cfgmod


def _start(root: Path, *extra: str) -> dict:
    proc = run_cli(root, "gates", "--detached", "--feature", "F011", "--tag", "post-implement-1", *extra)
    if proc.returncode != 0:
        raise AssertionError(proc.stdout + proc.stderr)
    return json.loads(proc.stdout)


def _wait(root: Path, log: str, timeout: str = "60") -> subprocess.CompletedProcess:
    return run_cli(root, "gates", "--wait", log, "--poll", "0.1", "--timeout", timeout)


class KeyedGateRunTest(unittest.TestCase):
    """AC 3.6-2: one log per feature, tag, round and HEAD; reuse, attach, dirty keys, --fresh."""

    def setUp(self) -> None:
        self.root = make_tmp("gate-runs-")
        self.repo = GitRepo(self.root)
        self.repo.init()
        cfg = test_config()
        cfg["gates"]["full"] = [
            {"name": "echo", "command": "echo full-ok", "timeout": 10},
            {"name": "slow", "command": "sleep 1.5", "timeout": 30},
        ]
        write_config(self.root, cfg)
        write(self.root, "src/app.py", "x = 1\n")
        self.repo.commit("chore: init")
        self.sha7 = self.repo.git("rev-parse", "--short=7", "HEAD").strip()
        self.gates_dir = self.root / ".sdd" / "logs" / "gates"
        self.clean_log = self.gates_dir / f"F011-post-implement-1-{self.sha7}.log"

    def tearDown(self) -> None:
        rm(self.root)

    def test_clean_key_started_running_then_done(self) -> None:
        first = _start(self.root)
        self.assertEqual(first["state"], "started")
        self.assertEqual(Path(first["log"]), self.clean_log)
        self.assertTrue(Path(first["log"] + ".pid").is_file())
        second = _start(self.root)
        self.assertEqual(second["state"], "running")
        self.assertEqual(second["pid"], first["pid"])
        proc = _wait(self.root, first["log"])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        nonce = json.loads(Path(first["log"] + ".pid").read_text())["nonce"]
        self.assertTrue(self.clean_log.read_text().rstrip().endswith(f"GATES_EXIT=0 {nonce}"))
        before = self.clean_log.read_text()
        third = _start(self.root)
        self.assertEqual(third["state"], "done")
        self.assertEqual(third["exit"], 0)
        self.assertEqual(self.clean_log.read_text(), before)
        self.assertFalse(Path(str(self.clean_log) + ".1").exists())

    def test_dirty_key_and_untracked_files(self) -> None:
        write(self.root, "src/app.py", "x = 2\n")
        dirty = _start(self.root)
        self.assertEqual(dirty["state"], "started")
        self.assertEqual(Path(dirty["log"]).name, f"F011-post-implement-1-{self.sha7}-dirty.log")
        again = _start(self.root)
        self.assertEqual(again["state"], "running")
        self.assertEqual(again["pid"], dirty["pid"])
        self.assertEqual(_wait(self.root, dirty["log"]).returncode, 0)
        # A completed dirty log is never reused: the tree may have changed since.
        rerun = _start(self.root)
        self.assertEqual(rerun["state"], "started")
        self.assertTrue(Path(dirty["log"] + ".1").is_file())
        self.assertEqual(_wait(self.root, rerun["log"]).returncode, 0)
        # Untracked tool output does not make the key dirty.
        write(self.root, "src/app.py", "x = 1\n")
        write(self.root, "lcov.info", "TN:\n")
        clean = _start(self.root)
        self.assertEqual(Path(clean["log"]), self.clean_log)

    def test_fresh_renames_a_completed_log_and_starts_a_new_run(self) -> None:
        first = _start(self.root)
        self.assertEqual(_wait(self.root, first["log"]).returncode, 0)
        reused = _start(self.root)
        self.assertEqual((reused["state"], reused["exit"]), ("done", 0))
        fresh = _start(self.root, "--fresh")
        self.assertEqual(fresh["state"], "started")
        self.assertEqual(Path(fresh["log"]), self.clean_log)
        self.assertIn("GATES_EXIT=0", Path(str(self.clean_log) + ".1").read_text())
        self.assertEqual(_wait(self.root, fresh["log"]).returncode, 0)

    def test_planted_or_forged_logs_are_never_reused(self) -> None:
        """S15: a log this tool did not start, or a GATES_EXIT line printed by a gate, is no result."""
        self.gates_dir.mkdir(parents=True)
        self.clean_log.write_text("==> [2/2] slow\n+- 2 gate(s), 0 failed\nGATES_EXIT=0\n")
        planted = _start(self.root)
        self.assertEqual(planted["state"], "started")
        self.assertIn("GATES_EXIT=0\n", Path(str(self.clean_log) + ".1").read_text())
        self.assertEqual(_wait(self.root, planted["log"]).returncode, 0)
        # A record with a nonce, but the log's only marker lacks it (a gate printed it, then the run died).
        rec = json.loads(Path(str(self.clean_log) + ".pid").read_text())
        self.clean_log.write_text("==> [1/2] echo\nGATES_EXIT=0\n")
        dead = subprocess.Popen(["true"])
        dead.wait()
        rec["pid"] = dead.pid
        Path(str(self.clean_log) + ".pid").write_text(json.dumps(rec))
        self.assertEqual(_wait(self.root, str(self.clean_log), timeout="5").returncode, 125)
        self.assertEqual(_start(self.root)["state"], "started")

    def test_reused_pid_counts_as_dead(self) -> None:
        self.gates_dir.mkdir(parents=True)
        self.clean_log.write_text("==> [1/2] echo\n")
        # The pid is alive (this test process) but its command line is not the recorded runner's.
        Path(str(self.clean_log) + ".pid").write_text(json.dumps({"pid": os.getpid(), "cmdline": ["python3", "sdd.py", "gates"]}))
        self.assertEqual(_wait(self.root, str(self.clean_log), timeout="5").returncode, 125)
        started = _start(self.root)
        self.assertEqual(started["state"], "started")
        self.assertEqual(_wait(self.root, started["log"]).returncode, 0)


class WaitExitCodeTest(unittest.TestCase):
    """AC 3.6-3: the marker's code, 125 incomplete (runner dead), 124 timeout (runner alive)."""

    def setUp(self) -> None:
        self.root = make_tmp("gate-wait-")
        write_config(self.root, test_config())

    def tearDown(self) -> None:
        rm(self.root)

    def test_marker_code(self) -> None:
        log = write(self.root, "a.log", "+- gates summary\nGATES_EXIT=3\n")
        self.assertEqual(_wait(self.root, str(log)).returncode, 3)

    def test_incomplete_and_timeout(self) -> None:
        log = write(self.root, "b.log", "==> [1/3] echo\n")
        dead = subprocess.Popen(["true"])
        dead.wait()
        Path(str(log) + ".pid").write_text(json.dumps({"pid": dead.pid, "cmdline": ["true"]}))
        proc = _wait(self.root, str(log), timeout="5")
        self.assertEqual(proc.returncode, 125, proc.stdout + proc.stderr)
        self.assertIn("incomplete", proc.stderr)
        live = subprocess.Popen(["sleep", "30"])
        try:
            Path(str(log) + ".pid").write_text(json.dumps({"pid": live.pid, "cmdline": ["sleep", "30"]}))
            self.assertEqual(_wait(self.root, str(log), timeout="0.3").returncode, 124)
        finally:
            live.kill()
            live.wait()


class BuildCacheTest(unittest.TestCase):
    """AC 3.6-8: build_cache.env reaches gate commands, the pipeline view and doctor."""

    def setUp(self) -> None:
        self.root = make_tmp("build-cache-")
        self.cfg = test_config()
        self.cfg["build_cache"] = {"env": {"SCCACHE_DIR": "/tmp/x", "SCCACHE_CACHE_SIZE": "10G"}}
        self.cfg["gates"]["full"].append({"name": "env", "command": "env", "timeout": 10})
        write_config(self.root, self.cfg)

    def tearDown(self) -> None:
        rm(self.root)

    def test_gate_environment(self) -> None:
        proc = run_cli(self.root, "gates", "--only", "env")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("SCCACHE_DIR=/tmp/x", proc.stdout)
        self.assertIn("SCCACHE_CACHE_SIZE=10G", proc.stdout)

    def test_config_validates_and_reaches_the_pipeline(self) -> None:
        cfg = cfgmod.load(self.root)
        self.assertEqual(cfgmod.validate(cfg), [])
        self.assertEqual(cfgmod.pipeline_view(cfg)["build_cache"]["env"]["SCCACHE_DIR"], "/tmp/x")
        cfg["build_cache"]["env"]["N"] = 3
        self.assertTrue(any("build_cache.env" in p for p in cfgmod.validate(cfg)))
        self.assertEqual(cfgmod.DEFAULTS["build_cache"], {"env": {}})

    def test_doctor_warns_for_tmpfs_cache(self) -> None:
        write(self.root, "specs/templates/spec.md", "x")
        write(self.root, "docs/CONSTITUTION.md", "x")
        proc = run_cli(self.root, "doctor")
        line = [ln for ln in proc.stdout.splitlines() if "SCCACHE_DIR" in ln]
        self.assertEqual(len(line), 1, proc.stdout)
        self.assertTrue(line[0].startswith("[warn]"), line)
        self.assertIn("tmpfs", line[0])
        self.assertFalse(any("SCCACHE_CACHE_SIZE" in ln for ln in proc.stdout.splitlines()))


class ReviewerChecklistTest(unittest.TestCase):
    """AC 3.6-9: maintainability checklist and marker/justification checks in the reviewer files."""

    def test_reviewer_files(self) -> None:
        agents = KIT / "claude" / "agents"
        compliance = (agents / "spec-compliance-reviewer.md").read_text()
        for phrase in ("Maintainability", "over-engineering", "speculative complexity", "simplest design that meets the spec", "threat_model"):
            self.assertIn(phrase, compliance)
        adequacy = (agents / "test-adequacy-reviewer.md").read_text()
        for phrase in ("cov:unreachable", "review.md", "§6", "surviving mutant"):
            self.assertIn(phrase, adequacy)


if __name__ == "__main__":
    unittest.main(verbosity=2)
