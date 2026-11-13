"""WI1 AC 11 and 12: command checks, gate skip/CI semantics, plans, and the shrink-only baseline."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest

from helpers import KIT, GitRepo, make_tmp, rm, run_cli, test_config, write, write_config

FIVE = "a = 1\nb = 2\nc = 3\nd = 4\ne = 5\n"


class CommandCheckTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_tmp("chk-")
        self.repo = GitRepo(self.root)
        self.repo.init()
        self.cfg = test_config()
        write(self.root, "out/docs.txt",
              "warning: missing docs\n  --> src/a.rs:3:1\nwarning: unresolved link\n  --> ./src/b.rs:9:5\nwarning: `x` generated 2 warnings\n")
        write(self.root, "out/deps.txt", "pkg -- crates/a/Cargo.toml:\n\tserde\n\tanyhow\nother -- crates/b/Cargo.toml:\n\tregex\n")
        self.cfg["quality"]["checks"] = [
            {"name": "docs", "command": "cat out/docs.txt",
             "violation_regex": r"(?m)^warning: (?P<message>.+)\n\s*--> (?P<file>[^:\n]+):(?P<line>\d+)"},
            {"name": "unused-deps", "command": "cat out/deps.txt; exit 1", "ok_exits": [0, 1],
             "violation_regex": r"(?m)^\S+ -- (?P<file>\S+?):$|^\t(?P<message>\S+)$"},
            {"name": "flaky", "command": "echo 'FLAKY tests::t'", "violation_regex": r"(?m)^FLAKY (?P<file>\S+?)::(?P<message>\S+)$", "baseline": False},
            {"name": "plain", "command": "exit 5"},
            {"name": "internal", "command": "echo 'INTERNALERROR'; exit 3", "violation_regex": r"(?m)^E (?P<file>\S+) (?P<message>.+)$"},
            {"name": "needs-tool", "command": "true", "requires": ["definitely-not-a-tool-xyz"], "install": "cargo install xyz"},
            {"name": "gone", "command": "definitely-not-a-tool-xyz --check"},
        ]
        self.cfg["gates"]["full"] = [
            {"name": "needs-tool", "command": f"{sys.executable} -I {KIT}/tools/sdd/sdd.py check needs-tool", "skip_exit": 3},
            {"name": "raw-exit-3", "command": "exit 3"},
        ]
        self.cfg["gates"]["nightly"] = [{"name": "night", "command": "echo nightly-ran"}]
        write_config(self.root, self.cfg)
        self.repo.commit("base")

    def tearDown(self) -> None:
        rm(self.root)

    def check(self, name, env=None):
        proc = run_cli(self.root, "check", name, "--json", env=env)
        return proc.returncode, json.loads(proc.stdout)

    def test_regex_checks(self) -> None:
        code, out = self.check("docs")
        self.assertEqual(code, 1)
        self.assertEqual([(v["file"], v["line"], v["message"]) for v in out["violations"]],
                         [("src/a.rs", 3, "missing docs"), ("src/b.rs", 9, "unresolved link")])
        code, out = self.check("unused-deps")  # file headers carry over to the indented items
        self.assertEqual([(v["file"], v["message"]) for v in out["violations"]],
                         [("crates/a/Cargo.toml", "serde"), ("crates/a/Cargo.toml", "anyhow"), ("crates/b/Cargo.toml", "regex")])
        code, out = self.check("plain")
        self.assertEqual(code, 1)
        code, out = self.check("internal")  # a wrapped tool's own exit 3 is a tool error, never a skip
        self.assertEqual(code, 2)
        code, out = self.check("gone")
        self.assertEqual(code, 3)
        code, out = self.check("nope")
        self.assertEqual(code, 2)

    def test_skip_exit_and_ci(self) -> None:
        code, out = self.check("needs-tool", env={"CI": ""})
        self.assertEqual(code, 3)
        self.assertIn("definitely-not-a-tool-xyz not installed (install: cargo install xyz)", out["notes"][0])
        proc = run_cli(self.root, "gates", env={"CI": ""})
        self.assertRegex(proc.stdout, r"needs-tool +SKIP .*definitely-not-a-tool-xyz not installed \(install: cargo install xyz\)")
        self.assertRegex(proc.stdout, r"raw-exit-3 +FAIL +.*exit status 3")
        code, out = self.check("needs-tool", env={"CI": "true"})
        self.assertEqual(code, 1)
        proc = run_cli(self.root, "gates", env={"CI": "true"})
        self.assertRegex(proc.stdout, r"needs-tool +FAIL")
        self.assertEqual(proc.returncode, 1)

    def test_ci_fails_skip_if_missing_and_optional(self) -> None:
        self.cfg["gates"]["full"] = [
            {"name": "deny", "command": "true", "skip_if_missing": "definitely-not-a-tool-xyz", "install": "cargo install deny"},
            {"name": "opt", "command": "definitely-not-a-tool-xyz", "optional": True},
        ]
        write_config(self.root, self.cfg)
        proc = run_cli(self.root, "gates", env={"CI": ""})
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertRegex(proc.stdout, r"deny +SKIP .*\(install: cargo install deny\)")
        proc = run_cli(self.root, "gates", env={"CI": "true"})
        self.assertEqual(proc.returncode, 1)
        self.assertRegex(proc.stdout, r"deny +FAIL")
        self.assertRegex(proc.stdout, r"opt +FAIL")

    def test_nightly_plan(self) -> None:
        proc = run_cli(self.root, "gates", "--plan", "nightly")
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("nightly-ran", proc.stdout)
        self.assertIn("1 gate(s), 0 failed", proc.stdout)


class BaselineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_tmp("base-")
        self.repo = GitRepo(self.root)
        self.repo.init()
        self.cfg = test_config()
        self.cfg["quality"]["size"].update({"file_fail": 3, "file_warn": 0})
        write_config(self.root, self.cfg)
        for name in "abcd":
            write(self.root, f"src/{name}.py", FIVE)
        self.repo.commit("legacy")

    def tearDown(self) -> None:
        rm(self.root)

    def entries(self, root=None):
        return json.loads(((root or self.root) / "sdd.baseline.json").read_text())["entries"]

    def test_lifecycle(self) -> None:
        self.assertEqual(run_cli(self.root, "check", "size").returncode, 1)
        proc = run_cli(self.root, "baseline", "--init")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual([(e["file"], e["lines"]) for e in self.entries()], [(f"src/{n}.py", 5) for n in "abcd"])
        text = (self.root / "sdd.baseline.json").read_text()
        self.assertEqual(text.count("\n    {"), 4)  # one entry per line
        self.assertEqual(run_cli(self.root, "baseline", "--init").returncode, 1)
        self.assertEqual(run_cli(self.root, "check", "size").returncode, 0)  # baselined
        self.repo.commit("chore: adopt baseline")
        # No grandfathering: a grown file and a new file fail.
        write(self.root, "src/a.py", FIVE + "f = 6\n")
        write(self.root, "src/e.py", FIVE)
        self.repo.git("add", "-A")
        proc = run_cli(self.root, "check", "size", "--json")
        out = json.loads(proc.stdout)
        self.assertEqual(sorted((v["file"], v["state"]) for v in out["violations"] if v["state"] != "baselined"),
                         [("src/a.py", "grown"), ("src/e.py", "new")])
        write(self.root, "src/a.py", FIVE)
        (self.root / "src/e.py").unlink()
        self.repo.git("add", "-A")
        # A fixed violation makes the check fail as stale until --update removes the entry.
        write(self.root, "src/b.py", "x = 1\n")
        write(self.root, "src/c.py", "a\nb\nc\nd\n")  # shrank from 5 to 4: still a violation, value lowered
        proc = run_cli(self.root, "check", "size")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("stale baseline entry", proc.stdout)
        self.assertEqual(run_cli(self.root, "baseline", "--update").returncode, 0)
        self.assertEqual([(e["file"], e["lines"]) for e in self.entries()], [("src/a.py", 5), ("src/c.py", 4), ("src/d.py", 5)])
        self.assertEqual(run_cli(self.root, "check", "size").returncode, 0)
        # --update never adds an identity.
        write(self.root, "src/f.py", FIVE)
        self.repo.git("add", "src/f.py")
        run_cli(self.root, "baseline", "--update")
        self.assertNotIn("src/f.py", [e["file"] for e in self.entries()])
        # Report.
        proc = run_cli(self.root, "baseline", "--report", "--json")
        rep = json.loads(proc.stdout)
        self.assertIn({"kind": "size-file", "file": "src/f.py", "line": None, "state": "new", "value": 5,
                       "message": "5 code lines (fail above 3)"}, rep["violations"])
        self.assertEqual(rep["totals"]["size-file"], {"new": 1, "baselined": 3, "grown": 0, "stale": 0})
        human = run_cli(self.root, "baseline", "--report").stdout
        self.assertIn("== size-file", human)
        self.assertIn("baselined", human)

    def test_missing_tool_never_drops_entries(self) -> None:
        """C6: a baseline-enabled check whose tool is missing leaves its entries alone (also with
        CI=true), and --init refuses rather than write a baseline without its kind."""
        tool_dir = self.root / "fakebin"
        write(self.root, "fakebin/fakelint", "#!/bin/sh\necho 'src/a.py: L1 bad'\necho 'src/b.py: L1 bad'\n")
        (tool_dir / "fakelint").chmod(0o755)
        self.cfg["quality"]["checks"] = [{"name": "lint", "command": "fakelint", "requires": ["fakelint"], "install": "pip install fakelint",
                                          "violation_regex": r"(?m)^(?P<file>\S+): (?P<message>L\d+ .+)$"}]
        write_config(self.root, self.cfg)
        with_tool = {"PATH": f"{tool_dir}:{__import__('os').environ['PATH']}", "CI": ""}
        proc = run_cli(self.root, "baseline", "--init", env={"CI": ""})
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertIn("lint: skipped", proc.stdout)
        self.assertFalse((self.root / "sdd.baseline.json").exists())
        self.assertEqual(run_cli(self.root, "baseline", "--init", env=dict(with_tool)).returncode, 0)
        self.assertEqual(sorted(e["kind"] for e in self.entries()).count("lint"), 2)
        for env in ({"CI": "true"}, {"CI": ""}):
            proc = run_cli(self.root, "baseline", "--update", env=env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("lint: skipped", proc.stdout)
            self.assertEqual([e["kind"] for e in self.entries()].count("lint"), 2, env)
            rep = json.loads(run_cli(self.root, "baseline", "--report", "--json", env=env).stdout)
            self.assertNotIn("lint", rep["totals"])
            self.assertEqual(rep["stale"], [])

    def test_ratchet(self) -> None:
        self.repo.git("switch", "-q", "-c", "feat/F001-x")
        self.assertEqual(run_cli(self.root, "check", "baseline").returncode, 0)  # no baseline at the merge-base
        run_cli(self.root, "baseline", "--init")
        self.assertEqual(run_cli(self.root, "check", "baseline").returncode, 0)  # adoption commit
        self.repo.git("switch", "-q", "main")
        self.repo.commit("chore: adopt baseline")
        self.repo.git("switch", "-q", "-c", "feat/F002-y")
        data = json.loads((self.root / "sdd.baseline.json").read_text())
        data["entries"][0]["lines"] = 50
        (self.root / "sdd.baseline.json").write_text(json.dumps(data))
        proc = run_cli(self.root, "check", "baseline")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("raised from 5 to 50", proc.stdout)
        data["entries"][0]["lines"] = 5
        data["entries"].append({"kind": "size-file", "file": "src/new.py", "lines": 9})
        (self.root / "sdd.baseline.json").write_text(json.dumps(data))
        proc = run_cli(self.root, "check", "baseline")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("entry added to the baseline", proc.stdout)
        (self.root / "sdd.baseline.json").write_text('{\n  "version": 1,\n  "entries": [\n    {"kind": "size-file",, }\n  ]\n}\n')
        proc = run_cli(self.root, "check", "size")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("sdd.baseline.json:4:", proc.stdout)

    # -- merges of parallel shrinks ------------------------------------------------

    def _branch(self, name: str, fixes: dict) -> None:
        self.repo.git("switch", "-q", "-c", name, "main")
        for f, text in fixes.items():
            write(self.root, f, text)
        self.assertEqual(run_cli(self.root, "baseline", "--update").returncode, 0)
        self.repo.commit(f"refactor: {name}")
        self.repo.git("switch", "-q", "main")

    def _merge(self, branch: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "merge", "--no-ff", "-m", f"merge {branch}", branch], cwd=str(self.root), env=self.repo.env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def _adopt(self) -> None:
        run_cli(self.root, "baseline", "--init")
        self.repo.commit("chore: adopt baseline")

    def test_resolve_adjacent_and_last_entries(self) -> None:
        rm(self.root)  # each case below uses its own repository
        for removed in (("src/b.py", "src/c.py"), ("src/c.py", "src/d.py")):
            with self.subTest(removed=removed):
                root = make_tmp("basemerge-")
                try:
                    self.root, self.repo = root, GitRepo(root)
                    self.repo.init()
                    write_config(root, self.cfg)
                    for name in "abcd":
                        write(root, f"src/{name}.py", FIVE)
                    self.repo.commit("legacy")
                    self._adopt()
                    self._branch("lane-a", {removed[0]: "x = 1\n", "src/a.py": "a\nb\nc\nd\n"})
                    self._branch("lane-b", {removed[1]: "x = 1\n"})
                    self.assertEqual(self._merge("lane-a").returncode, 0)
                    proc = self._merge("lane-b")
                    self.assertNotEqual(proc.returncode, 0, "parallel shrinks of adjacent entries conflict")
                    self.assertIn("sdd.baseline.json", self.repo.git("diff", "--name-only", "--diff-filter=U"))
                    um = run_cli(root, "union-merge", "sdd.baseline.json")
                    self.assertEqual(um.returncode, 3)
                    self.assertIn("sdd baseline --resolve", um.stdout + um.stderr)
                    proc = run_cli(root, "baseline", "--resolve")
                    self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                    left = {e["file"]: e["lines"] for e in self.entries(root)}
                    expected = {f"src/{n}.py": 5 for n in "abcd" if f"src/{n}.py" not in removed}
                    expected["src/a.py"] = 4  # the lower value of the two sides
                    self.assertEqual(left, expected)
                    self.assertIn("sdd.baseline.json", self.repo.git("diff", "--name-only", "--diff-filter=U"))  # never staged
                    self.assertEqual(run_cli(root, "check", "baseline").returncode, 0)
                    self.assertEqual(run_cli(root, "check", "size").returncode, 0)
                finally:
                    rm(root)

    def test_merge_driver(self) -> None:
        self._adopt()
        write(self.root, ".gitattributes", "sdd.baseline.json merge=sdd-baseline\n")
        self.repo.commit("chore: baseline merge driver")
        self.repo.git("config", "merge.sdd-baseline.driver", f"{sys.executable} -I {KIT}/tools/sdd/sdd.py baseline --merge-driver %O %A %B")
        self._branch("lane-a", {"src/b.py": "x = 1\n"})
        self._branch("lane-b", {"src/c.py": "x = 1\n"})
        self.assertEqual(self._merge("lane-a").returncode, 0)
        proc = self._merge("lane-b")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual([e["file"] for e in self.entries()], ["src/a.py", "src/d.py"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
