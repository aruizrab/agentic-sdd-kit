"""Mutation resource limits: per-test memory cap, jobs/test threads, heavy-job lock, memory guard.

A mutant can turn a loop into a runaway allocator; with several jobs and test threads the test
processes exhausted a 16 GB host in seconds (LESSONS 28). These tests cover the configuration
validation, the gate's command construction and the three scripts in tools/sdd/scripts/.
No real mutation tool runs here.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shlex
import shutil
import signal
import subprocess
import time
import unittest
from pathlib import Path

from helpers import KIT, GitRepo, make_tmp, rm, run_cli, test_config, write, write_config
from sddkit import config as cfgmod
from sddkit.quality import mutation

SCRIPTS = KIT / "tools" / "sdd" / "scripts"
RUNNER = SCRIPTS / "memcap-runner.sh"
LOCK = SCRIPTS / "heavy-lock.sh"


def load_memguard():
    spec = importlib.util.spec_from_file_location("memguard", SCRIPTS / "memguard.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def shell_ulimit_v() -> str:
    return subprocess.run(["sh", "-c", "ulimit -v"], stdout=subprocess.PIPE, text=True).stdout.strip()


class ConfigValidationTest(unittest.TestCase):
    def problems(self, **mutation_overrides):
        cfg = test_config()
        cfg["quality"]["mutation"].update(mutation_overrides)
        return [p for p in cfgmod.validate(cfg) if "mutation" in p or "heavy_lock" in p]

    def test_safe_defaults(self) -> None:
        m = cfgmod.DEFAULTS["quality"]["mutation"]
        self.assertEqual((m["jobs"], m["test_threads"], m["memory_limit_mib"], m["env"]), (2, 3, 2048, {}))
        self.assertTrue(cfgmod.DEFAULTS["quality"]["heavy_lock"].startswith("/"))
        self.assertEqual(self.problems(), [])
        template = json.loads((KIT / "templates" / "sdd.config.json").read_text())["quality"]["mutation"]
        self.assertEqual((template["jobs"], template["test_threads"], template["memory_limit_mib"]), (2, 3, 2048))

    def test_bad_values(self) -> None:
        for overrides, word in (({"jobs": 0}, "jobs"), ({"jobs": True}, "jobs"), ({"test_threads": "3"}, "test_threads"),
                                ({"memory_limit_mib": 10}, "memory_limit_mib"), ({"memory_limit_mib": -1}, "memory_limit_mib"),
                                ({"memory_limit_mib": 1.5}, "memory_limit_mib"), ({"env": []}, "env"),
                                ({"env": {"BAD-NAME": "x"}}, "BAD-NAME"), ({"env": {"N": 3}}, "env.N")):
            with self.subTest(overrides=overrides):
                problems = self.problems(**overrides)
                self.assertEqual(len(problems), 1, problems)
                self.assertIn(word, problems[0])
        self.assertEqual(self.problems(memory_limit_mib=0), [])  # 0 = no cap
        self.assertEqual(self.problems(env={"CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_RUNNER": "{memcap_runner}"}), [])

    def test_heavy_lock_must_be_absolute(self) -> None:
        cfg = test_config()
        for value, ok in (("", True), ("/var/lock/sdd.lock", True), (".sdd/heavy.lock", False), (None, False)):
            with self.subTest(value=value):
                cfg["quality"]["heavy_lock"] = value
                problems = [p for p in cfgmod.validate(cfg) if "heavy_lock" in p]
                self.assertEqual(problems == [], ok, problems)

    def test_examples_validate(self) -> None:
        for path in sorted((KIT / "examples" / "configs").glob("*.json")):
            with self.subTest(config=path.name):
                cfg = cfgmod._merge(cfgmod.DEFAULTS, json.loads(path.read_text()))
                self.assertEqual([p for p in cfgmod.validate(cfg) if "mutation" in p or "heavy_lock" in p], [])


class BuildCommandTest(unittest.TestCase):
    def build(self, command: str, **overrides):
        cfg = test_config()
        cfg["quality"]["mutation"].update({"command": command, **overrides})
        return mutation.build_command(cfg, "/w/diff file.patch", ["src/b.py", "src/a.py"])

    def test_default_wraps_the_whole_command(self) -> None:
        cmd, env = self.build("tool --jobs {jobs} --threads {test_threads} --in-diff {diff_file} {changed_files}")
        inner = "tool --jobs 2 --threads 3 --in-diff '/w/diff file.patch' src/a.py src/b.py"
        self.assertEqual(cmd, f"sh {shlex.quote(str(RUNNER))} sh -c {shlex.quote(inner)}")
        self.assertEqual(env, {"SDD_MUTATION_JOBS": "2", "SDD_TEST_THREADS": "3", "SDD_MEM_LIMIT_KB": str(2048 * 1024)})

    def test_runner_hook_in_env_caps_tests_only(self) -> None:
        cmd, env = self.build("cargo mutants --jobs {jobs}", jobs=1, test_threads=4, memory_limit_mib=1024,
                              env={"CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_RUNNER": "{memcap_runner}",
                                   "NEXTEST_TEST_THREADS": "{test_threads}", "LIMIT": "{memory_limit_kb}", "RUSTC_WRAPPER": ""})
        self.assertEqual(cmd, "cargo mutants --jobs 1")  # the compiler is not capped
        self.assertEqual(env["CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_RUNNER"], str(RUNNER))
        self.assertEqual((env["NEXTEST_TEST_THREADS"], env["LIMIT"], env["RUSTC_WRAPPER"]), ("4", "1048576", ""))
        self.assertEqual(env["SDD_MEM_LIMIT_KB"], "1048576")

    def test_runner_hook_in_command(self) -> None:
        cmd, _env = self.build("tool --test-runner {memcap_runner}")
        self.assertEqual(cmd, f"tool --test-runner {shlex.quote(str(RUNNER))}")

    def test_no_cap(self) -> None:
        cmd, env = self.build("tool", memory_limit_mib=0)
        self.assertEqual((cmd, env["SDD_MEM_LIMIT_KB"]), ("tool", "0"))


class MutationGateLimitsTest(unittest.TestCase):
    """The limits reach the command `sdd check mutation` runs."""

    def setUp(self) -> None:
        self.root = make_tmp("mutlim-")
        self.repo = GitRepo(self.root)
        self.repo.init()
        self.cfg = test_config()
        self.cfg["quality"]["mutation"].update({"survivor_regex": r"(?m)^SURVIVED (?P<file>[^:\s]+):(?P<line>\d+): (?P<mutation>.+)$",
                                                "memory_limit_mib": 1024})
        write(self.root, ".gitignore", ".sdd/\nmut/\n")
        write(self.root, "src/calc.py", "def add(a, b):\n    return a + b\n")
        self.repo.commit("base")
        self.repo.git("switch", "-q", "-c", "feat/F011-calc")
        write(self.root, "src/calc.py", "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n")
        (self.root / "mut").mkdir()

    def tearDown(self) -> None:
        rm(self.root)

    def check(self, command: str, **overrides):
        self.cfg["quality"]["mutation"].update({"command": command, **overrides})
        write_config(self.root, self.cfg)
        proc = run_cli(self.root, "check", "mutation", "--json")
        return proc.returncode, json.loads(proc.stdout)

    def read(self, name: str) -> str:
        return (self.root / "mut" / name).read_text().strip()

    def test_whole_command_capped_by_default(self) -> None:
        code, out = self.check("ulimit -v > mut/limit; echo {jobs} $SDD_TEST_THREADS > mut/par")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.read("limit"), "1048576")
        self.assertEqual(self.read("par"), "2 3")

    def test_runner_hook_caps_only_the_test_process(self) -> None:
        code, out = self.check('ulimit -v > mut/outer; "$TEST_RUNNER" sh -c "ulimit -v" > mut/inner',
                               env={"TEST_RUNNER": "{memcap_runner}"})
        self.assertEqual(code, 0, out)
        self.assertEqual(self.read("inner"), "1048576")
        self.assertEqual(self.read("outer"), shell_ulimit_v())

    def test_runaway_allocation_fails_alone(self) -> None:
        """A test process that allocates past the cap dies; the tool goes on and reports it."""
        hog = "python3 -I -c 'b = bytearray(1536 * 1024 * 1024)' 2>/dev/null && echo SURVIVED src/calc.py:6: hog || echo caught > mut/hog"
        code, out = self.check(f'"$TEST_RUNNER" sh -c {shlex.quote(hog)}', env={"TEST_RUNNER": "{memcap_runner}"})
        self.assertEqual(code, 0, out)
        self.assertEqual(self.read("hog"), "caught")

    @unittest.skipUnless(shutil.which("flock"), "flock(1) not installed")
    def test_runs_under_the_heavy_job_lock(self) -> None:
        code, out = self.check('flock -n "$SDD_HEAVY_LOCK" true && echo free > mut/lock || echo held > mut/lock; '
                               'echo "$SDD_HEAVY_LOCK_HELD" > mut/held')
        self.assertEqual(code, 0, out)
        self.assertEqual(self.read("lock"), "held")
        self.assertEqual(self.read("held"), self.cfg["quality"]["heavy_lock"])
        self.cfg["quality"]["heavy_lock"] = ""
        code, out = self.check('echo "${SDD_HEAVY_LOCK_HELD:-none}" > mut/held')
        self.assertEqual((code, self.read("held")), (0, "none"))


class ScriptsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = make_tmp("scripts-")

    def tearDown(self) -> None:
        rm(self.tmp)

    def run_script(self, *args: str, env=None, timeout: float = 20):
        e = dict(os.environ)
        e.pop("SDD_HEAVY_LOCK_HELD", None)
        e.update(env or {})
        return subprocess.run(["sh", *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=e, timeout=timeout)

    def test_scripts_are_executable(self) -> None:
        for name in ("memcap-runner.sh", "heavy-lock.sh", "memguard.py"):
            self.assertTrue(os.access(SCRIPTS / name, os.X_OK), name)

    def test_memcap_runner(self) -> None:
        p = self.run_script(str(RUNNER), "sh", "-c", "ulimit -v", env={"SDD_MEM_LIMIT_KB": "524288"})
        self.assertEqual((p.returncode, p.stdout.strip()), (0, "524288"))
        p = self.run_script(str(RUNNER), "sh", "-c", "ulimit -v", env={"SDD_MEM_LIMIT_KB": "0"})
        self.assertEqual(p.stdout.strip(), shell_ulimit_v())
        e = dict(os.environ)
        e.pop("SDD_MEM_LIMIT_KB", None)
        p = subprocess.run(["sh", str(RUNNER), "sh", "-c", "ulimit -v"], stdout=subprocess.PIPE, text=True, env=e)
        self.assertEqual(p.stdout.strip(), "2097152")  # default 2 GiB
        p = self.run_script(str(RUNNER), "true", env={"SDD_MEM_LIMIT_KB": "not-a-number"})
        self.assertEqual(p.returncode, 125)  # an uncapped run never passes for a capped one
        self.assertIn("cannot set ulimit", p.stderr)
        self.assertEqual(self.run_script(str(RUNNER)).returncode, 2)

    @unittest.skipUnless(shutil.which("flock"), "flock(1) not installed")
    def test_heavy_lock_nested_and_exit_code(self) -> None:
        lock = str(self.tmp / "heavy.lock")
        env = {"SDD_HEAVY_LOCK": lock}
        p = self.run_script(str(LOCK), "sh", str(LOCK), "sh", "-c", 'echo "$SDD_HEAVY_LOCK_HELD"; exit 7', env=env)
        self.assertEqual((p.returncode, p.stdout.strip()), (7, lock))  # nested call does not deadlock
        holder = subprocess.Popen(["flock", lock, "sleep", "30"], start_new_session=True)
        try:
            for _ in range(100):  # until the holder really holds the lock
                if subprocess.run(["flock", "-n", lock, "true"]).returncode != 0:
                    break
                time.sleep(0.05)
            proc = subprocess.Popen(["sh", str(LOCK), "true"], env=dict(os.environ, **env), stderr=subprocess.PIPE, text=True)
            with self.assertRaises(subprocess.TimeoutExpired):
                proc.wait(timeout=1.5)  # queued behind the holder
        finally:
            os.killpg(holder.pid, signal.SIGKILL)  # the lock fd lives in flock's child too
            holder.wait()
        self.assertEqual(proc.wait(timeout=10), 0)
        with proc.stderr:
            self.assertIn("waiting for", proc.stderr.read())


class MemguardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.mg = load_memguard()
        self.proc = make_tmp("proc-")
        quiet = contextlib.redirect_stderr(io.StringIO())  # the guard logs to stderr too
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)

    def tearDown(self) -> None:
        rm(self.proc)

    def fake(self, pid: int, exe: str, rss_kib: int) -> None:
        d = self.proc / str(pid)
        d.mkdir()
        os.symlink(exe, d / "exe")
        (d / "status").write_text(f"Name:\tx\nVmRSS:\t{rss_kib} kB\n")

    def test_parsers(self) -> None:
        self.assertEqual(self.mg.mem_available_kib("MemTotal: 16 kB\nMemAvailable:    1234 kB\n"), 1234)
        self.assertIsNone(self.mg.mem_available_kib("MemTotal: 16 kB\n"))
        self.assertEqual(self.mg.rss_kib("Name:\tkthreadd\n"), 0)

    def test_picks_largest_matching_and_respects_floor(self) -> None:
        self.fake(10, "/usr/lib/systemd/systemd", 9_000_000)                 # largest, but not a test binary
        self.fake(11, "/w/proj/target/debug/deps/core-1a2b", 2_500_000)
        self.fake(12, "/w/proj/target/debug/deps/big-3c4d (deleted)", 2_600_000)
        self.fake(13, "/w/proj/target/debug/deps/small-5e6f", 100)
        (self.proc / "self").mkdir()
        self.assertEqual(self.mg.pick_victim(self.proc, ["*/target/*"]), (12, 2_600_000, "/w/proj/target/debug/deps/big-3c4d"))
        self.assertEqual(self.mg.pick_victim(self.proc, ["*/target/*"], skip=(12,))[0], 11)
        self.assertIsNone(self.mg.pick_victim(self.proc, ["*/build/*"]))
        (self.proc / "meminfo").write_text("MemAvailable: 4000000 kB\n")
        self.assertIsNone(self.mg.check_once(self.proc, 1536 * 1024, ["*/target/*"], None, dry_run=True))
        (self.proc / "meminfo").write_text("MemAvailable: 1000000 kB\n")
        log = self.proc / "guard.log"
        self.assertEqual(self.mg.check_once(self.proc, 1536 * 1024, ["*/target/*"], str(log), dry_run=True), 12)
        self.assertIn("would kill pid 12", log.read_text())

    def test_kills_a_real_process(self) -> None:
        target = self.proc / "w" / "target" / "debug"
        target.mkdir(parents=True)
        shutil.copy(shutil.which("sleep"), target / "runaway-test")
        victim = subprocess.Popen([str(target / "runaway-test"), "30"])
        fake = self.proc / "fakeproc"
        fake.mkdir()
        (fake / "meminfo").write_text("MemAvailable: 1 kB\n")
        os.symlink(f"/proc/{victim.pid}", fake / str(victim.pid))
        try:
            pid = self.mg.check_once(fake, 1536 * 1024, [f"{target}/*"], None, dry_run=False)
            self.assertEqual(pid, victim.pid)
            self.assertEqual(victim.wait(timeout=10), -signal.SIGKILL)
        finally:
            if victim.poll() is None:
                victim.kill()
                victim.wait()


if __name__ == "__main__":
    unittest.main(verbosity=2)
