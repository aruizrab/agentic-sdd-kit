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


def shell_ulimit(flag: str = "-d") -> str:
    return subprocess.run(["sh", "-c", f"ulimit {flag}"], stdout=subprocess.PIPE, text=True).stdout.strip()


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


class MutationRepoCase(unittest.TestCase):
    """A repository with a changed hand-written file on a feature branch (no tests of its own)."""

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

    def check(self, command: str, cli_env=None, **overrides):
        self.cfg["quality"]["mutation"].update({"command": command, **overrides})
        write_config(self.root, self.cfg)
        proc = run_cli(self.root, "check", "mutation", "--json", env=cli_env)
        return proc.returncode, json.loads(proc.stdout)

    def read(self, name: str) -> str:
        return (self.root / "mut" / name).read_text().strip()


class MutationGateLimitsTest(MutationRepoCase):
    """The limits reach the command `sdd check mutation` runs."""

    def test_whole_command_capped_by_default(self) -> None:
        code, out = self.check("ulimit -d > mut/limit; ulimit -v > mut/as; echo {jobs} $SDD_TEST_THREADS > mut/par")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.read("limit"), "1048576")
        self.assertEqual(self.read("as"), shell_ulimit("-v"))  # the address space stays as inherited
        self.assertEqual(self.read("par"), "2 3")

    def test_runner_hook_caps_only_the_test_process(self) -> None:
        code, out = self.check('ulimit -d > mut/outer; "$TEST_RUNNER" sh -c "ulimit -d" > mut/inner',
                               env={"TEST_RUNNER": "{memcap_runner}"})
        self.assertEqual(code, 0, out)
        self.assertEqual(self.read("inner"), "1048576")
        self.assertEqual(self.read("outer"), shell_ulimit())

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


class SwitchAndTestFilterTest(MutationRepoCase):
    """SDD_MUTATION=skip for a loaded host (never in CI) and the {test_filter} placeholder."""

    def test_switch(self) -> None:
        ran = "echo ran > mut/ran"
        for env, code, status in (({"SDD_MUTATION": "skip", "CI": ""}, 3, "skip"), ({"SDD_MUTATION": "skip", "CI": "true"}, 1, "fail"),
                                  ({"SDD_MUTATION": "maybe", "CI": ""}, 2, "error"), ({"SDD_MUTATION": "Skip", "CI": "true"}, 2, "error")):
            with self.subTest(env=env):
                got, out = self.check(ran, cli_env=env)
                self.assertEqual((got, out["status"]), (code, status), out)
                self.assertFalse((self.root / "mut" / "ran").exists(), "the switch never runs the tool")
                self.assertIn("SDD_MUTATION", " ".join(out["notes"]))
        self.assertIn("not allowed in CI", " ".join(self.check(ran, cli_env={"SDD_MUTATION": "skip", "CI": "true"})[1]["notes"]))
        got, out = self.check(ran, cli_env={"SDD_MUTATION": "run", "CI": ""})
        self.assertEqual((got, self.read("ran")), (0, "ran"), out)

    def test_test_filter_on_the_command_line(self) -> None:
        cmd = "printf '%s\\n' x {test_filter} > mut/argv; echo \"$FILTER\" > mut/env"
        filt = "-E 'not binary(/^e2e_/)'"
        over = {"test_filter": filt, "test_filter_when": ["**/tests/e2e_*.py"], "env": {"FILTER": "{test_filter}"}}
        got, out = self.check(cmd, **over)
        self.assertEqual(got, 0, out)
        self.assertEqual(self.read("argv"), "x")  # no end-to-end test is tracked: no filter (runners reject one that matches nothing)
        self.assertEqual(self.read("env"), "")
        write(self.root, "pkg/tests/e2e_hooks.py", "def test_hooks():\n    pass\n")
        self.repo.git("add", "pkg/tests/e2e_hooks.py")
        got, out = self.check(cmd, **over)
        self.assertEqual(got, 0, out)
        self.assertEqual(self.read("argv").split("\n"), ["x", "-E", "not binary(/^e2e_/)"])
        self.assertEqual(self.read("env"), filt)
        over["test_filter_when"] = []  # no condition: always applied
        (self.root / "pkg/tests/e2e_hooks.py").unlink()
        self.repo.git("rm", "-q", "--cached", "pkg/tests/e2e_hooks.py")
        self.check(cmd, **over)
        self.assertEqual(self.read("argv").split("\n"), ["x", "-E", "not binary(/^e2e_/)"])

    def test_filter_validation(self) -> None:
        cfg = test_config()
        cfg["quality"]["mutation"].update({"test_filter": ["-E"], "test_filter_when": "e2e_*"})
        problems = [p for p in cfgmod.validate(cfg) if "test_filter" in p]
        self.assertEqual(len(problems), 2, problems)


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
        p = self.run_script(str(RUNNER), "sh", "-c", 'echo "$(ulimit -d) $(ulimit -v)"', env={"SDD_MEM_LIMIT_KB": "524288"})
        self.assertEqual((p.returncode, p.stdout.strip()), (0, f"524288 {shell_ulimit('-v')}"))  # data capped, address space untouched
        p = self.run_script(str(RUNNER), "sh", "-c", "ulimit -d", env={"SDD_MEM_LIMIT_KB": "0"})
        self.assertEqual(p.stdout.strip(), shell_ulimit())
        e = dict(os.environ)
        e.pop("SDD_MEM_LIMIT_KB", None)
        p = subprocess.run(["sh", str(RUNNER), "sh", "-c", "ulimit -d"], stdout=subprocess.PIPE, text=True, env=e)
        self.assertEqual(p.stdout.strip(), "2097152")  # default 2 GiB
        p = self.run_script(str(RUNNER), "true", env={"SDD_MEM_LIMIT_KB": "not-a-number"})
        self.assertEqual(p.returncode, 125)  # an uncapped run never passes for a capped one
        self.assertIn("cannot set ulimit", p.stderr)
        self.assertEqual(self.run_script(str(RUNNER)).returncode, 2)

    def test_memcap_runner_stops_a_touched_allocation_but_not_a_reservation(self) -> None:
        """The data cap stops memory a test writes to; address space a runtime only reserves (Go,
        V8: PROT_NONE ranges) does not count, unlike under an address-space cap."""
        env = {"SDD_MEM_LIMIT_KB": str(256 << 10)}
        touch = "b = bytearray({} << 20); b[::4096] = b'\\x01' * len(b[::4096]); print(len(b) >> 20)"
        small = self.run_script(str(RUNNER), "python3", "-I", "-c", touch.format(64), env=env)
        self.assertEqual((small.returncode, small.stdout.strip()), (0, "64"), small.stderr)
        big = self.run_script(str(RUNNER), "python3", "-I", "-c", touch.format(512), env=env)
        self.assertNotEqual(big.returncode, 0)
        self.assertIn("MemoryError", big.stderr)
        # prot=0 is PROT_NONE: 1 GiB reserved, never writable, four times the cap.
        reserve = "import mmap; m = mmap.mmap(-1, 1 << 30, flags=mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS, prot=0); print('reserved')"
        r = self.run_script(str(RUNNER), "python3", "-I", "-c", reserve, env=env)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "reserved"), r.stderr)

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

    @unittest.skipUnless(shutil.which("flock"), "flock(1) not installed")
    def test_heavy_lock_is_first_come_first_served(self) -> None:
        """A job that loops over locked steps queues behind a job that arrived while it held the lock
        (flock alone let the loop re-take the lock at once and starve the short job for hours)."""
        lock = str(self.tmp / "heavy.lock")
        out = self.tmp / "order"
        env = dict(os.environ, SDD_HEAVY_LOCK=lock, SDD_HEAVY_LOCK_POLL="0.05")
        env.pop("SDD_HEAVY_LOCK_HELD", None)
        step = f'echo L$i >> {shlex.quote(str(out))}; sleep 0.8'
        loop = subprocess.Popen(["sh", "-c", f'for i in 1 2 3; do sh {shlex.quote(str(LOCK))} sh -c "{step}"; done'], env=env)
        try:
            for _ in range(200):  # until the first step holds the lock
                if out.exists():
                    break
                time.sleep(0.02)
            short = subprocess.run(["sh", str(LOCK), "sh", "-c", f"echo S >> {shlex.quote(str(out))}"], env=env,
                                   stderr=subprocess.PIPE, text=True, timeout=30)
            self.assertEqual(short.returncode, 0, short.stderr)
            self.assertIn("heavy-lock: ", short.stderr)
            self.assertEqual(loop.wait(timeout=30), 0)
        finally:
            if loop.poll() is None:
                loop.kill()
        self.assertEqual(out.read_text().split(), ["L1", "S", "L2", "L3"])
        self.assertEqual([p.name for p in Path(lock + ".queue").iterdir() if not p.name.startswith(".")], [])

    @unittest.skipUnless(shutil.which("flock"), "flock(1) not installed")
    def test_heavy_lock_drops_dead_and_cancelled_tickets(self) -> None:
        lock = str(self.tmp / "heavy.lock")
        queue = Path(lock + ".queue")
        queue.mkdir()
        dead = subprocess.Popen(["true"])
        dead.wait()
        (queue / f"{1:012d}.{dead.pid}").write_text("")  # a caller killed while queued
        env = {"SDD_HEAVY_LOCK": lock, "SDD_HEAVY_LOCK_POLL": "0.05"}
        p = self.run_script(str(LOCK), "sh", "-c", "echo ran", env=env, timeout=10)
        self.assertEqual((p.returncode, p.stdout.strip()), (0, "ran"))
        self.assertEqual([x.name for x in queue.iterdir() if not x.name.startswith(".")], [])
        # A caller cancelled while it waits removes its own ticket.
        holder = subprocess.Popen(["flock", lock, "sleep", "30"], start_new_session=True)
        try:
            for _ in range(100):
                if subprocess.run(["flock", "-n", lock, "true"]).returncode != 0:
                    break
                time.sleep(0.05)
            waiter = subprocess.Popen(["sh", str(LOCK), "true"], env=dict(os.environ, **env), stderr=subprocess.DEVNULL)
            for _ in range(100):
                if any(not x.name.startswith(".") for x in queue.iterdir()):
                    break
                time.sleep(0.05)
            self.assertEqual(len([x for x in queue.iterdir() if not x.name.startswith(".")]), 1)
            waiter.terminate()
            self.assertNotEqual(waiter.wait(timeout=10), 0)
        finally:
            os.killpg(holder.pid, signal.SIGKILL)
            holder.wait()
        self.assertEqual([x.name for x in queue.iterdir() if not x.name.startswith(".")], [])


def systemd_scope_works() -> bool:
    if not shutil.which("systemd-run"):
        return False
    env = dict(os.environ)
    if not env.get("XDG_RUNTIME_DIR") and os.path.isdir(f"/run/user/{os.getuid()}"):
        env["XDG_RUNTIME_DIR"] = f"/run/user/{os.getuid()}"
    try:
        return subprocess.run(["systemd-run", "--user", "--scope", "-q", "--collect", "-p", "MemoryMax=64M", "--", "true"],
                              env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


OLD_LOCK = KIT / "tests" / "fixtures" / "heavy-lock-one-at-a-time.sh"  # the release before the memory budget


@unittest.skipUnless(shutil.which("flock"), "flock(1) not installed")
class HeavyBudgetTest(unittest.TestCase):
    """SDD_HEAVY_BUDGET_MB: several heavy jobs at once within a memory budget, still first come first served."""

    def setUp(self) -> None:
        self.tmp = make_tmp("budget-")
        self.lock = str(self.tmp / "heavy.lock")
        self.queue = Path(self.lock + ".queue")
        self.events = self.tmp / "events"
        self.procs = []

    def tearDown(self) -> None:
        for p in self.procs:
            if p.poll() is None:
                p.kill()
                p.wait()
        rm(self.tmp)

    def env(self, mb=None, budget="300", **extra) -> dict:
        e = dict(os.environ, SDD_HEAVY_LOCK=self.lock, SDD_HEAVY_LOCK_POLL="0.05", SDD_HEAVY_CAP="0")
        for k in ("SDD_HEAVY_LOCK_HELD", "SDD_HEAVY_BUDGET_MB", "SDD_HEAVY_JOB_MB", "SDD_HEAVY_LEGACY_MB"):
            e.pop(k, None)
        if budget is not None:
            e["SDD_HEAVY_BUDGET_MB"] = budget
        if mb is not None:
            e["SDD_HEAVY_JOB_MB"] = str(mb)
        e.update(extra)
        return e

    def step(self, name: str, secs: float) -> str:
        ev = shlex.quote(str(self.events))
        return f"echo {name}+ >> {ev}; sleep {secs}; echo {name}- >> {ev}"

    def start(self, name: str, mb, secs: float = 0.6, script=LOCK, **env) -> subprocess.Popen:
        p = subprocess.Popen(["sh", str(script), "sh", "-c", self.step(name, secs)], env=self.env(mb, **env),
                             stderr=subprocess.PIPE, text=True)
        self.procs.append(p)
        return p

    def wait_for(self, what, timeout: float = 10) -> None:
        end = time.time() + timeout
        while time.time() < end:
            if what():
                return
            time.sleep(0.02)
        self.fail("timed out")

    def seen(self, mark: str) -> bool:
        return self.events.exists() and mark in self.events.read_text().split()

    def queued(self, count: int) -> bool:
        return len([x for x in self.queue.iterdir() if not x.name.startswith(".")]) >= count if self.queue.exists() else False

    def order(self):
        return self.events.read_text().split()

    def finish(self, *procs) -> None:
        for p in procs:
            self.assertEqual(p.wait(timeout=30), 0, p.stderr.read())
            p.stderr.close()

    def log_lines(self):
        path = Path(self.lock + ".jobs.log")
        if not path.exists():
            return []
        return [dict(f.split("=", 1) for f in line.split("\t")) for line in path.read_text().splitlines()]

    def leftovers(self):
        names = [x.name for x in self.queue.iterdir() if not x.name.startswith(".")]
        running = self.queue / ".running"
        return names + ([x.name for x in running.iterdir()] if running.exists() else [])

    def test_jobs_that_fit_run_together(self) -> None:
        a = self.start("A", 100)
        b = self.start("B", 150)
        self.finish(a, b)
        o = self.order()
        self.assertLess(max(o.index("A+"), o.index("B+")), min(o.index("A-"), o.index("B-")), o)
        self.assertEqual(sorted(x["mb"] for x in self.log_lines()), ["100", "150"])
        self.assertEqual(self.leftovers(), [])

    def test_a_job_that_does_not_fit_waits_and_nobody_overtakes_it(self) -> None:
        a = self.start("A", 200)
        self.wait_for(lambda: self.seen("A+"))
        b = self.start("B", 200, secs=0.3)  # 200 + 200 > 300: waits for A
        self.wait_for(lambda: self.queued(2))
        c = self.start("C", 50, secs=0.1)  # would fit next to A, but B came first
        self.finish(a, b, c)
        o = self.order()
        self.assertLess(o.index("A-"), o.index("B+"), o)
        self.assertLess(o.index("B+"), o.index("C+"), o)
        self.assertLess(o.index("C+"), o.index("B-"), o)  # C fits next to B: 200 + 50 <= 300
        self.assertEqual(self.leftovers(), [])

    def test_a_job_bigger_than_the_budget_runs_alone(self) -> None:
        a = self.start("A", 50)
        self.wait_for(lambda: self.seen("A+"))
        big = self.start("G", 1000, secs=0.3)
        self.wait_for(lambda: self.queued(2))
        c = self.start("C", 10, secs=0.1)
        self.finish(a, big, c)
        o = self.order()
        self.assertEqual(o[o.index("G+"):o.index("G-") + 1], ["G+", "G-"], o)
        self.assertLess(o.index("A-"), o.index("G+"), o)
        p = subprocess.run(["sh", str(LOCK), "true"], env=self.env(1000), stderr=subprocess.PIPE, text=True, timeout=10)
        self.assertIn("runs alone", p.stderr)

    def test_jobs_log_and_exit_code(self) -> None:
        long_arg = "x" * 400
        p = subprocess.run(["sh", str(LOCK), "sh", "-c", 'echo "$SDD_HEAVY_LOCK_HELD $PWD"; exit 7', long_arg],
                           env=self.env(120), cwd=self.tmp, stdout=subprocess.PIPE, text=True, timeout=10)
        self.assertEqual((p.returncode, p.stdout.strip()), (7, f"{self.lock} {self.tmp}"))
        (line,) = self.log_lines()
        self.assertEqual(list(line), ["start", "end", "secs", "mb", "peak_mb", "rc", "oom_kills", "cap", "cwd", "cmd"])
        self.assertEqual((line["mb"], line["rc"], line["cwd"], line["cap"], line["peak_mb"]), ("120", "7", str(self.tmp), "none", "-"))
        self.assertTrue(line["cmd"].startswith("sh -c echo"), line["cmd"])
        self.assertLessEqual(len(line["cmd"]), 300)
        self.assertEqual(p.returncode, 7)
        # Default job size: half the budget. A bad number is a usage error.
        subprocess.run(["sh", str(LOCK), "true"], env=self.env(None), timeout=10)
        self.assertEqual(self.log_lines()[-1]["mb"], "150")
        bad = subprocess.run(["sh", str(LOCK), "true"], env=self.env("lots"), stderr=subprocess.PIPE, text=True, timeout=10)
        self.assertEqual(bad.returncode, 2)

    def test_nested_call_runs_inside_its_parent(self) -> None:
        inner = f'SDD_HEAVY_JOB_MB=5000 sh {shlex.quote(str(LOCK))} sh -c "echo nested \\$SDD_HEAVY_LOCK_HELD"'
        p = subprocess.run(["sh", str(LOCK), "sh", "-c", inner], env=self.env(100), stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, text=True, timeout=10)
        self.assertEqual((p.returncode, p.stdout.strip(), p.stderr), (0, f"nested {self.lock}", ""))
        self.assertEqual(len(self.log_lines()), 1)

    def test_dead_jobs_and_tickets_are_dropped(self) -> None:
        dead = subprocess.Popen(["true"])
        dead.wait()
        (self.queue / ".running").mkdir(parents=True)
        name = f"{1:012d}.{dead.pid}"
        (self.queue / name).write_text("")
        (self.queue / ".running" / name).write_text("300 2026-01-01T00:00:00+0000 killed job\n")  # a job killed with -9
        p = subprocess.run(["sh", str(LOCK), "true"], env=self.env(200), timeout=10)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(self.leftovers(), [])

    def test_cancelled_waiter_and_terminated_job_free_their_places(self) -> None:
        a = self.start("A", 200, secs=30)
        self.wait_for(lambda: self.seen("A+"))
        b = self.start("B", 200)
        self.wait_for(lambda: self.queued(2))
        b.terminate()
        self.assertNotEqual(b.wait(timeout=10), 0)
        b.stderr.close()
        self.assertEqual(len(self.leftovers()), 2)  # A's ticket and running record
        a.terminate()
        self.assertEqual(a.wait(timeout=10), 143)
        a.stderr.close()
        self.assertEqual(self.leftovers(), [])
        self.assertEqual(self.log_lines()[-1]["rc"], "143")

    def test_without_a_budget_jobs_run_one_at_a_time(self) -> None:
        a = self.start("A", 10, budget=None)
        self.wait_for(lambda: self.seen("A+"))
        b = self.start("B", 10, budget=None)
        self.finish(a, b)
        self.assertEqual(self.order(), ["A+", "A-", "B+", "B-"])
        self.assertFalse(Path(self.lock + ".jobs.log").exists())
        self.assertFalse((self.queue / ".running").exists())

    def test_exclusive_holder_counts_as_one_job(self) -> None:
        """A job holding the lock the one-at-a-time way (flock, an older heavy-lock.sh) counts as
        SDD_HEAVY_LEGACY_MB (default half the budget)."""
        holder = subprocess.Popen(["flock", self.lock, "sleep", "1"], start_new_session=True)
        self.procs.append(holder)
        self.wait_for(lambda: subprocess.run(["flock", "-n", self.lock, "true"]).returncode != 0)
        small = self.start("S", 100, secs=0.1)  # 150 + 100 <= 300: runs next to the holder
        self.finish(small)
        self.assertIsNone(holder.poll())
        big = self.start("B", 200, secs=0.1)  # 150 + 200 > 300: waits for the holder
        self.assertEqual(big.wait(timeout=10), 0)
        big.stderr.close()
        self.assertIsNotNone(holder.poll())

    def test_one_at_a_time_callers_wait_for_budget_jobs(self) -> None:
        """Mixed callers on one lock: a caller without a budget (this script or the previous
        release) never starts on top of running budget jobs, and keeps its place in the queue."""
        for script in (LOCK, OLD_LOCK):
            with self.subTest(script=script.name):
                self.events.unlink(missing_ok=True)
                a = self.start("A", 100)
                b = self.start("B", 100)
                self.wait_for(lambda: self.seen("A+") and self.seen("B+"))
                old = self.start("O", None, secs=0.3, script=script, budget=None)
                self.wait_for(lambda: self.queued(3))
                c = self.start("C", 50, secs=0.1)  # fits the budget, but arrived after the old-style caller
                self.finish(a, b, old, c)
                o = self.order()
                self.assertLess(max(o.index("A-"), o.index("B-")), o.index("O+"), o)
                self.assertLess(o.index("O+"), o.index("C+"), o)
                self.assertLess(o.index("C+"), o.index("O-"), o)  # C runs next to the exclusive holder (150 + 50)
                self.assertEqual(self.leftovers(), [])

    @unittest.skipUnless(systemd_scope_works(), "systemd-run --user --scope not usable here")
    def test_memory_cap_kills_only_the_job_that_outgrows_it(self) -> None:
        other = self.start("A", 100, secs=1.5, SDD_HEAVY_CAP="1")
        hog = "import os; b = bytearray(256 << 20); b[::4096] = b'\\x01' * len(b[::4096]); print('survived')"
        self.wait_for(lambda: self.seen("A+"))
        p = subprocess.run(["sh", str(LOCK), "sh", "-c", f'echo "$SDD_X $PWD"; exec python3 -I -c {shlex.quote(hog)}'],
                           env=self.env(64, SDD_HEAVY_CAP="1", SDD_X="kept"), cwd=self.tmp, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, text=True, timeout=30)
        self.assertEqual(p.returncode, 137, p.stderr)
        self.assertEqual(p.stdout.split("\n")[0], f"kept {self.tmp}")  # environment and working directory kept
        self.finish(other)
        line = next(x for x in self.log_lines() if x["mb"] == "64")
        self.assertEqual((line["rc"], line["cap"], line["oom_kills"]), ("137", "64M", "1"))
        self.assertGreaterEqual(int(line["peak_mb"]), 60)
        a_line = next(x for x in self.log_lines() if x["mb"] == "100")
        self.assertEqual((a_line["rc"], a_line["cap"], a_line["oom_kills"]), ("0", "100M", "0"))
        self.assertEqual(self.leftovers(), [])


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
