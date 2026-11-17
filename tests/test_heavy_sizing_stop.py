"""Heavy jobs: per-command job sizes (quality.heavy_job_mb_by_command) and heavy-stop.sh."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import time
import unittest
from pathlib import Path

from helpers import KIT, make_tmp, rm, test_config
from sddkit import config as cfgmod
from sddkit.quality import coverage

STOP = KIT / "tools" / "sdd" / "scripts" / "heavy-stop.sh"
RULES = [
    {"match": "xtask gates", "unless": ["--fast"], "mb": 5120},
    {"match": "make coverage", "mb": 3000},
]


def cfg_with(rules, job_mb=0) -> dict:
    cfg = test_config()
    cfg["quality"]["heavy_job_mb_by_command"] = rules
    cfg["quality"]["heavy_job_mb"] = job_mb
    return cfg


class JobSizeTest(unittest.TestCase):
    def test_first_matching_rule_wins_and_unless_excludes(self) -> None:
        cfg = cfg_with(RULES)
        self.assertEqual(coverage.job_mb_for(cfg, "cargo xtask gates"), 5120)
        self.assertEqual(coverage.job_mb_for(cfg, "cargo   xtask  gates --plan full"), 5120)
        self.assertEqual(coverage.job_mb_for(cfg, "cargo xtask gates --fast"), 0)
        self.assertEqual(coverage.job_mb_for(cfg, "make coverage"), 3000)
        self.assertEqual(coverage.job_mb_for(cfg, "cargo test"), 0)
        self.assertEqual(coverage.job_mb_for(cfg, "cargo xtask gatesx"), 0)  # whole words only
        self.assertEqual(coverage.job_mb_for(cfg_with([]), "cargo xtask gates"), 0)

    def test_heavy_env(self) -> None:
        cfg = cfg_with(RULES, job_mb=4096)
        root = Path(cfg.get("_root") or ".")
        old = os.environ.pop("SDD_HEAVY_JOB_MB", None)
        try:
            self.assertEqual(coverage.heavy_env(cfg, root, "cargo xtask gates")["SDD_HEAVY_JOB_MB"], "5120")
            self.assertEqual(coverage.heavy_env(cfg, root, "cargo xtask gates --fast")["SDD_HEAVY_JOB_MB"], "4096")
            self.assertEqual(coverage.heavy_env(cfg, root)["SDD_HEAVY_JOB_MB"], "4096")
            self.assertNotIn("SDD_HEAVY_JOB_MB", coverage.heavy_env(cfg_with(RULES), root, "cargo test"))
            os.environ["SDD_HEAVY_JOB_MB"] = "2048"  # the job's own declaration wins
            self.assertNotEqual(coverage.heavy_env(cfg, root, "cargo xtask gates").get("SDD_HEAVY_JOB_MB"), "5120")
        finally:
            os.environ.pop("SDD_HEAVY_JOB_MB", None)
            if old is not None:
                os.environ["SDD_HEAVY_JOB_MB"] = old

    def test_validation(self) -> None:
        self.assertEqual(cfgmod.DEFAULTS["quality"]["heavy_job_mb_by_command"], [])
        self.assertEqual([p for p in cfgmod.validate(cfg_with(RULES)) if "heavy_job_mb_by_command" in p], [])
        for bad in ("x", [{"match": "a"}], [{"match": "", "mb": 5}], [{"match": "a", "mb": 0}], [{"match": "a", "mb": "5"}],
                    [{"match": "a", "mb": 5, "unless": "--fast"}], [{"match": "a", "mb": 5, "other": 1}], ["a"]):
            with self.subTest(bad=bad):
                self.assertTrue([p for p in cfgmod.validate(cfg_with(bad)) if "heavy_job_mb_by_command" in p])


class HeavyStopTest(unittest.TestCase):
    """heavy-stop.sh with a stub systemctl and a fake cgroup tree; the processes are real, so their cwd is."""

    def setUp(self) -> None:
        self.tmp = make_tmp("heavy-stop-")
        self.procs = []
        self.log = self.tmp / "stopped.log"
        stub = self.tmp / "systemctl"
        stub.write_text(
            "#!/bin/sh\n"
            f'LOG="{self.log}"\nROOT="{self.tmp}/cg"\n'
            'shift  # --user\n'
            'case "$1" in\n'
            '  list-units) for u in "$ROOT"/*.scope; do [ -e "$u" ] && echo "$(basename "$u") loaded active running x"; done ;;\n'
            '  show) for a; do u="$a"; done; echo "/$u" ;;\n'
            '  stop) echo "$2" >> "$LOG" ;;\n'
            'esac\n')
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
        self.env = dict(os.environ, SDD_HEAVY_STOP_SYSTEMCTL=str(stub), SDD_CGROUP_ROOT=str(self.tmp / "cg"))

    def tearDown(self) -> None:
        for p in self.procs:
            p.kill()
            p.wait()
        rm(self.tmp)

    def scope(self, name: str, cwd: Path) -> None:
        cwd.mkdir(parents=True, exist_ok=True)
        p = subprocess.Popen(["sleep", "60"], cwd=cwd)
        self.procs.append(p)
        d = self.tmp / "cg" / name
        d.mkdir(parents=True)
        (d / "cgroup.procs").write_text(f"{p.pid}\n")

    def run_stop(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["sh", str(STOP), *args], env=self.env, text=True, capture_output=True)

    def stopped(self) -> list:
        return self.log.read_text().split() if self.log.exists() else []

    def test_stops_only_the_scopes_of_that_worktree(self) -> None:
        wt, other = self.tmp / "wt" / "F001", self.tmp / "wt" / "F0011"
        self.scope("sdd-heavy-1-10.scope", wt)
        self.scope("sdd-heavy-2-11.scope", wt / "crates" / "a")  # below the worktree
        self.scope("sdd-heavy-3-12.scope", other)  # a sibling whose name starts the same
        self.scope("unrelated.scope", wt)  # not a heavy scope
        r = self.run_stop(str(wt))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sorted(self.stopped()), ["sdd-heavy-1-10.scope", "sdd-heavy-2-11.scope"])
        self.assertIn("stopped sdd-heavy-1-10.scope", r.stdout)

    def test_dry_run_and_nothing_to_stop(self) -> None:
        wt = self.tmp / "wt" / "F001"
        self.scope("sdd-heavy-1-10.scope", wt)
        r = self.run_stop("--dry-run", str(wt))
        self.assertIn("would stop sdd-heavy-1-10.scope", r.stdout)
        self.assertEqual(self.stopped(), [])
        elsewhere = self.tmp / "wt" / "F002"
        elsewhere.mkdir(parents=True)
        r = self.run_stop(str(elsewhere))
        self.assertEqual((r.returncode, self.stopped()), (0, []))
        self.assertIn("no heavy scope runs in", r.stdout)

    def test_usage(self) -> None:
        self.assertEqual(self.run_stop().returncode, 2)
        self.assertEqual(self.run_stop(str(self.tmp / "missing")).returncode, 2)


if __name__ == "__main__":
    unittest.main()
