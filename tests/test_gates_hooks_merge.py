"""Tests for sdd gates (incl. detached mode), hooks, union-merge, progress, source and config."""

from __future__ import annotations

import io
import json
import os
import sys
import time
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from helpers import KIT, GitRepo, make_tmp, rm, run_cli, test_config, write, write_config

from sddkit import config as cfgmod
from sddkit import gates, hooks, progress, source, union_merge
from sddkit.util import glob_match


class GlobTest(unittest.TestCase):
    def test_globs(self) -> None:
        self.assertTrue(glob_match("crates/fc-amf/tests/vectors/a.bin", "crates/*/tests/vectors/**"))
        self.assertTrue(glob_match("tests/x.rs", "**/tests/**"))
        self.assertTrue(glob_match("a/b/tests/x.rs", "**/tests/**"))
        self.assertFalse(glob_match("a/b/src/x.rs", "**/tests/**"))
        self.assertTrue(glob_match("docs/CONSTITUTION.md", "docs/CONSTITUTION.md"))
        self.assertTrue(glob_match(".claude/agents/x.md", ".claude/**"))
        self.assertTrue(glob_match("x/y.rs", "*.rs"))
        self.assertFalse(glob_match("x/y.py", "*.rs"))
        self.assertTrue(glob_match("src/a/b.ts", "src/**/*.ts"))
        self.assertTrue(glob_match("src/b.ts", "src/**/*.ts"))
        self.assertTrue(glob_match("src/app/x.test.ts", "**/*.test.*"))


class ConfigTest(unittest.TestCase):
    def test_defaults_merge_and_validate(self) -> None:
        root = make_tmp("cfg-")
        try:
            write(root, "sdd.config.json", json.dumps({"project": {"name": "p"}, "gates": {"full": [{"name": "t", "command": "true"}]}, "commit": {"trailers": ["X: y"]}, "bogus": 1}))
            cfg = cfgmod.load(root)
            self.assertEqual(cfg["project"]["name"], "p")
            self.assertEqual(cfg["paths"]["features_dir"], "specs/features")
            problems = cfgmod.validate(cfg)
            self.assertEqual(problems, ["unknown top-level key 'bogus'"])
            write(root, "sdd.config.json", json.dumps({
                "gates": {"fsat": [], "full": [{"name": "t", "command": "true"}]}, "pipeline": {"skeptcs": 1, "role_tiers": {"critic": "top"}},
                "components": {"word": "crate"}, "role_write_scopes": {"my-role": ["x/**"]}, "requirements": {"budget": {"mni": 1}},
                "commit": {"trailers": ["Co-Authored-By: <agent> <noreply@example.com>", "Signed-off-by: Jane Doe <jane@example.com>"]},
            }))
            problems = cfgmod.validate(cfgmod.load(root))
            self.assertEqual([p for p in problems if p.startswith("unknown")],
                             ["unknown key 'requirements.budget.mni'", "unknown key 'gates.fsat'", "unknown key 'pipeline.skeptcs'"])
            self.assertEqual([p for p in problems if "placeholder" in p],
                             ["commit.trailers[0]: still the template placeholder ('Co-Authored-By: <agent> <noreply@example.com>'); set the real attribution line"])
            view = cfgmod.pipeline_view(cfg)
            self.assertEqual(view["gates"], {"fast": [], "full": ["t"]})
            self.assertEqual(view["root"], str(root))
            write(root, "sdd.config.json", "{not json")
            with self.assertRaises(cfgmod.ConfigError):
                cfgmod.load(root)
            proc = run_cli(root, "config", "--validate")
            self.assertEqual(proc.returncode, 2)
        finally:
            rm(root)

    def test_find_root_walks_up(self) -> None:
        root = make_tmp("root-")
        try:
            write(root, "sdd.config.json", "{}")
            (root / "a" / "b").mkdir(parents=True)
            os.environ.pop("SDD_ROOT", None)
            self.assertEqual(cfgmod.find_root(str(root / "a" / "b")), root.resolve())
        finally:
            rm(root)


class GatesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_tmp("gates-")
        self.cfg = test_config()
        write_config(self.root, self.cfg)

    def tearDown(self) -> None:
        rm(self.root)

    def test_run_full_with_timeout_and_optional(self) -> None:
        log = self.root / ".sdd" / "logs" / "gates-full.log"
        buf = io.StringIO()
        real = sys.stdout
        sys.stdout = buf
        try:
            code = gates.run(self.cfg, self.root, fast=False, only=None, log_path=log)
        finally:
            sys.stdout = real
        out = buf.getvalue()
        self.assertEqual(code, 1)
        self.assertRegex(out, r"echo +PASS")
        self.assertRegex(out, r"sleepy +FAIL")
        self.assertIn("timed out after 1 s", out)
        self.assertRegex(out, r"missing +SKIP")
        self.assertIn("3 gate(s), 1 failed", out)
        self.assertTrue(log.read_text().rstrip().endswith("GATES_EXIT=1"))

    def test_fast_marks_skipped_full_gates(self) -> None:
        buf = io.StringIO()
        real = sys.stdout
        sys.stdout = buf
        try:
            code = gates.run(self.cfg, self.root, fast=True, only=None, log_path=None)
        finally:
            sys.stdout = real
        self.assertEqual(code, 0)
        self.assertRegex(buf.getvalue(), r"sleepy +SKIP +0\.0s +--fast")
        self.assertIn("GATES_EXIT=0", buf.getvalue())

    def test_detached_and_wait_cli(self) -> None:
        log = self.root / "detached.log"
        proc = run_cli(self.root, "gates", "--fast", "--detached", "--log", str(log))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        info = json.loads(proc.stdout)
        self.assertEqual(info["log"], str(log))
        proc = run_cli(self.root, "gates", "--wait", str(log), "--poll", "0.2", "--timeout", "30")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("gates summary", proc.stdout)
        self.assertIn("GATES_EXIT=0", log.read_text())
        # Waiting on a log without the marker times out with 124, never reports success.
        write(self.root, "partial.log", "==> [1/3] echo\n")
        proc = run_cli(self.root, "gates", "--wait", str(self.root / "partial.log"), "--poll", "0.1", "--timeout", "0.3")
        self.assertEqual(proc.returncode, 124)

    def test_only_and_env(self) -> None:
        self.cfg["gates"]["full"].append({"name": "env", "command": "test \"$SDD_FEATURE\" = F011 && test -n \"$SDD_ROOT\"", "timeout": 5})
        write_config(self.root, self.cfg)
        proc = run_cli(self.root, "gates", "--only", "env", "--feature", "F011")
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("1 gate(s), 0 failed", proc.stdout)


class HooksTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_tmp("hooks-")
        repo = GitRepo(self.root)
        repo.init()
        self.cfg = test_config()
        self.cfg["protected_paths"] = list(cfgmod.DEFAULTS["protected_paths"]) + [{"glob": "specs/source/**", "reason": "normative source is read-only"}]
        self.cfg["append_only_paths"] = [{"glob": "crates/*/tests/vectors/**", "allowed_roles": ["test-author"]}]
        self.cfg["role_denied_paths"] = {"implementer": ["crates/*/tests/**"]}
        self.cfg["formatters"] = [{"glob": "*.py", "command": "python3 -c \"import ast,sys; ast.parse(open(sys.argv[1]).read())\" {file}"}]
        write_config(self.root, self.cfg)
        write(self.root, "crates/fc-nas/tests/vectors/old.bin", "x")
        write(self.root, "docs/CONSTITUTION.md", "# c")
        self.env_backup = dict(os.environ)
        os.environ.pop("SDD_ALLOW_PROTECTED", None)
        os.environ["CLAUDE_PROJECT_DIR"] = str(self.root)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.env_backup)
        rm(self.root)

    def _edit(self, file: str, agent: str = "", tool: str = "Edit", cwd: str = None) -> int:
        data = {"tool_name": tool, "tool_input": {"file_path": file}, "cwd": cwd or str(self.root), "agent_type": agent}
        with redirect_stderr(io.StringIO()):
            return hooks.protect_paths(data)

    def test_protect_paths(self) -> None:
        self.assertEqual(self._edit("docs/CONSTITUTION.md"), 2)
        self.assertEqual(self._edit(str(self.root / ".claude" / "agents" / "x.md")), 2)
        self.assertEqual(self._edit("specs/source/ts.md", "spec-author"), 2)
        self.assertEqual(self._edit("sdd.config.json"), 2)
        self.assertEqual(self._edit("crates/fc-nas/src/lib.rs", "implementer"), 0)
        self.assertEqual(self._edit("crates/fc-nas/tests/reg.rs", "implementer"), 2)
        self.assertEqual(self._edit("crates/fc-nas/tests/reg.rs", "test-author"), 0)
        self.assertEqual(self._edit("crates/fc-nas/tests/vectors/old.bin", "test-author", "Write"), 2)
        self.assertEqual(self._edit("crates/fc-nas/tests/vectors/new.bin", "test-author", "Write"), 0)
        self.assertEqual(self._edit("crates/fc-nas/tests/vectors/new.bin", "test-author", "Edit"), 2)
        self.assertEqual(self._edit("crates/fc-nas/src/lib.rs", "spec-author"), 2)
        self.assertEqual(self._edit("specs/features/011-x/spec.md", "spec-author"), 0)
        self.assertEqual(self._edit("specs/features/011-x/review.md", "integrator"), 0)
        self.assertEqual(self._edit("README.md"), 0)
        # Paths resolve against the tree that holds the file: a worktree edit of a protected path.
        wt = self.root / ".wt" / "F011"
        repo = GitRepo(self.root)
        repo.commit("base")
        repo.git("worktree", "add", "-q", "--detach", str(wt))
        os.makedirs(wt / "docs", exist_ok=True)
        self.assertEqual(self._edit(str(wt / "docs" / "CONSTITUTION.md"), cwd=str(wt)), 2)
        self.assertEqual(self._edit(str(wt / "src" / "ok.rs"), "implementer", cwd=str(wt)), 0)
        # Override and bad input.
        os.environ["SDD_ALLOW_PROTECTED"] = "1"
        self.assertEqual(self._edit("docs/CONSTITUTION.md"), 0)
        os.environ.pop("SDD_ALLOW_PROTECTED")
        with redirect_stderr(io.StringIO()):
            self.assertEqual(hooks.protect_paths({"_invalid": True, "cwd": str(self.root)}), 2)

    def test_bash_guard(self) -> None:
        def guard(cmd: str, agent: str = "") -> int:
            with redirect_stderr(io.StringIO()):
                return hooks.bash_guard({"tool_input": {"command": cmd}, "cwd": str(self.root), "agent_type": agent})

        self.assertEqual(guard("git status && cat docs/CONSTITUTION.md"), 0)
        self.assertEqual(guard("echo x > docs/CONSTITUTION.md"), 2)
        self.assertEqual(guard("rm -rf specs/source"), 2)
        self.assertEqual(guard("sed -i 's/a/b/' docs/CONSTITUTION.md"), 2)
        self.assertEqual(guard("cp x crates/fc-nas/tests/vectors/old.bin"), 2)
        self.assertEqual(guard("tee crates/fc-nas/tests/reg.rs < x", "implementer"), 2)
        self.assertEqual(guard("tee crates/fc-nas/tests/reg.rs < x", "test-author"), 0)
        self.assertEqual(guard("cargo test 2>&1 | tail -3"), 0)
        # Shared-tree safety: destructive git is blocked for everyone, including the orchestrator.
        self.assertEqual(guard("git checkout -- . && git clean -fd"), 2)
        self.assertEqual(guard("git restore src/x.rs"), 2)
        self.assertEqual(guard("git -C /x stash"), 2)
        self.assertEqual(guard("git stash list"), 0)
        self.assertEqual(guard("git reset --hard HEAD~1"), 2)
        self.assertEqual(guard("git reset HEAD -- x"), 0)
        self.assertEqual(guard("git push --force origin main"), 2)
        self.assertEqual(guard("git commit --no-verify -m x"), 2)
        self.assertEqual(guard("git checkout -b feat/x"), 0)
        self.assertEqual(guard("git worktree add --detach .wt/exp-1 HEAD"), 0)
        os.environ["SDD_ALLOW_DESTRUCTIVE_GIT"] = "1"
        self.assertEqual(guard("git stash"), 0)
        os.environ.pop("SDD_ALLOW_DESTRUCTIVE_GIT")

    def test_format_hook(self) -> None:
        good = write(self.root, "src/ok.py", "x = 1\n")
        bad = write(self.root, "src/bad.py", "def (:\n")
        with redirect_stderr(io.StringIO()) as err:
            self.assertEqual(hooks.format_file({"tool_input": {"file_path": str(good)}, "cwd": str(self.root)}), 0)
            self.assertEqual(hooks.format_file({"tool_input": {"file_path": str(bad)}, "cwd": str(self.root)}), 2)
        self.assertIn("formatter failed", err.getvalue())
        self.assertEqual(hooks.format_file({"tool_input": {"file_path": str(self.root / "x.rs")}, "cwd": str(self.root)}), 0)

    def test_stop_check(self) -> None:
        self.cfg["stop_check"] = {"command": "false", "changed_globs": ["*.py"], "roles": ["implementer"], "timeout": 10}
        write_config(self.root, self.cfg)
        base = {"cwd": str(self.root), "hook_event_name": "SubagentStop", "agent_type": "implementer"}
        with redirect_stderr(io.StringIO()):
            self.assertEqual(hooks.stop_check(dict(base)), 0)  # nothing changed matching *.py
            write(self.root, "src/x.py", "x\n")
            self.assertEqual(hooks.stop_check(dict(base)), 2)
            self.assertEqual(hooks.stop_check(dict(base, stop_hook_active=True)), 0)
            self.assertEqual(hooks.stop_check(dict(base, agent_type="verifier")), 0)
            self.cfg["stop_check"]["command"] = "definitely-not-a-program-xyz -m compileall src"
            write_config(self.root, self.cfg)
            err = io.StringIO()
            with redirect_stderr(err):
                self.assertEqual(hooks.stop_check(dict(base)), 0)  # exit 127: tool missing, never block
            self.assertIn("command not found", err.getvalue())

    def test_cli_hook_reads_stdin(self) -> None:
        import subprocess

        proc = subprocess.run(
            [sys.executable, "-I", str(KIT / "tools/sdd/sdd.py"), "hook", "protect-paths"],
            input=json.dumps({"tool_name": "Edit", "tool_input": {"file_path": "docs/CONSTITUTION.md"}, "cwd": str(self.root)}),
            cwd=str(self.root), env={**os.environ, "SDD_ROOT": str(self.root)}, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("BLOCKED by SDD hook", proc.stderr)


class UnionMergeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_tmp("merge-")
        self.repo = GitRepo(self.root)
        self.repo.init()
        self.cfg = test_config()
        write_config(self.root, self.cfg)
        write(self.root, "Cargo.toml", "[workspace]\nmembers = [\"a\"]\n\n[workspace.dependencies]\nserde = \"1\"\n")
        write(self.root, "src/lib.rs", "fn a() {}\n")
        self.repo.commit("base")
        self.repo.git("checkout", "-q", "-b", "feat/one")
        write(self.root, "Cargo.toml", "[workspace]\nmembers = [\"a\"]\n\n[workspace.dependencies]\nserde = \"1\"\nanyhow = \"1\"\n")
        write(self.root, "src/lib.rs", "fn a() {}\nfn one() {}\n")
        self.repo.commit("one")
        self.repo.git("checkout", "-q", "main")
        write(self.root, "Cargo.toml", "[workspace]\nmembers = [\"a\"]\n\n[workspace.dependencies]\nserde = \"1\"\nthiserror = \"2\"\n")
        write(self.root, "src/lib.rs", "fn a() {}\nfn two() {}\n")
        self.repo.commit("two")

    def tearDown(self) -> None:
        rm(self.root)

    def test_union_text(self) -> None:
        text = "a\n<<<<<<< HEAD\nx = 1\n=======\ny = 2\nx = 1\n>>>>>>> theirs\nb\n"
        merged, n = union_merge.union_text(text)
        self.assertEqual(n, 1)
        self.assertEqual(merged, "a\nx = 1\ny = 2\nb\n")
        self.assertEqual(union_merge.dedupe_toml_keys("[a]\nk = 1\nk = 2\n[b]\nk = 3\n"), "[a]\nk = 1\n[b]\nk = 3\n")

    def test_refuses_source_conflicts_and_unions_manifests(self) -> None:
        import subprocess

        proc = subprocess.run(["git", "merge", "--no-commit", "feat/one"], cwd=str(self.root), env=self.repo.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertNotEqual(proc.returncode, 0)
        code, msgs = union_merge.resolve(self.root, self.cfg, [])
        self.assertEqual(code, 3)
        self.assertTrue(any("REFUSE: src/lib.rs" in m for m in msgs), msgs)
        # Only the manifest may be union-merged.
        code, msgs = union_merge.resolve(self.root, self.cfg, ["Cargo.toml"])
        self.assertEqual(code, 1, msgs)  # src/lib.rs still unresolved
        text = (self.root / "Cargo.toml").read_text()
        self.assertIn('anyhow = "1"', text)
        self.assertIn('thiserror = "2"', text)
        self.assertNotIn("<<<<<<<", text)
        self.assertTrue(any("UNRESOLVED: src/lib.rs" in m for m in msgs), msgs)


ROADMAP = """# roadmap

### M0 — Foundations

#### F001 workspace
#### F002 runtime
#### F003 events

### M1 — Slice

#### F004 capture
#### F005 nas
#### F006 ngap

### M2 — Mobility

#### F007 paging
#### F008 handover
"""


def _tasks(fid: str, done: int, total: int) -> str:
    rows = "\n".join(f"- [{'x' if i <= done else ' '}] T{i:02d} [P] do {i} — files: a.rs — verifies: FC-DEV-R0{i} — depends: -" for i in range(1, total + 1))
    return f"# Tasks\n\nFeature: {fid}\n\n<!--\n- [ ] T01 [P] <imperative description> — files: <path> — verifies: <ID> — depends: -\n-->\n\n{rows}\n"


def _spec(fid: str, status: str) -> str:
    return f"# {fid}\n\nFeature: {fid}\nStatus: {status}\n"


class ProgressTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.base = make_tmp("progress-")
        src = cls.base / "src"
        r = GitRepo(src)
        r.init()
        write(src, "docs/ROADMAP.md", ROADMAP)
        r.commit("chore: roadmap")

        def fdir(fid: str) -> str:
            return f"specs/features/{fid[1:]}-{fid.lower()}"

        r.git("checkout", "-q", "-b", "feat/F002-runtime")
        d = fdir("F002")
        write(src, f"{d}/spec.md", _spec("F002", "Implemented"))
        write(src, f"{d}/design.md", "# d\n")
        write(src, f"{d}/tasks.md", _tasks("F002", 3, 3))
        r.commit("feat(F002): all")
        r.git("checkout", "-q", "main")
        r.git("merge", "-q", "--no-ff", "-m", "merge F002", "feat/F002-runtime")

        r.git("checkout", "-q", "-b", "feat/F003-events", "main")
        d = fdir("F003")
        write(src, f"{d}/spec.md", _spec("F003", "Approved"))
        r.commit("docs(F003): approve")
        write(src, f"{d}/design.md", "# d\n")
        write(src, f"{d}/tasks.md", _tasks("F003", 0, 4))
        r.commit("docs(F003): plan")
        write(src, "crates/x/tests/red.rs", "// red\n")
        r.commit("test(F003): failing tests")
        write(src, f"{d}/tasks.md", _tasks("F003", 1, 4))
        r.commit("feat(F003): T01")

        r.git("checkout", "-q", "-b", "feat/F004-capture", "main")
        write(src, f"{fdir('F004')}/spec.md", _spec("F004", "Draft"))
        r.commit("docs(F004): draft")

        r.git("checkout", "-q", "-b", "feat/F006-ngap", "main")
        d = fdir("F006")
        write(src, f"{d}/spec.md", _spec("F006", "Implemented"))
        write(src, f"{d}/design.md", "# d\n")
        write(src, f"{d}/tasks.md", _tasks("F006", 2, 2))
        r.commit("docs(F006): implemented")

        r.git("checkout", "-q", "-b", "feat/F007-paging", "main")
        d = fdir("F007")
        write(src, f"{d}/spec.md", _spec("F007", "Approved"))
        write(src, f"{d}/design.md", "# d\n")
        write(src, f"{d}/tasks.md", _tasks("F007", 0, 3))
        r.commit("docs(F007): plan")

        r.git("checkout", "-q", "-b", "feat/F008-handover-old", "main")
        write(src, f"{fdir('F008')}/spec.md", _spec("F008", "Draft"))
        r.commit("docs(F008): old draft")
        r.git("checkout", "-q", "-b", "feat/F008-handover", "main")
        d = fdir("F008")
        write(src, f"{d}/spec.md", _spec("F008", "Approved"))
        write(src, f"{d}/design.md", "# d\n")
        write(src, f"{d}/tasks.md", _tasks("F008", 0, 3))
        r.commit("docs(F008): plan")
        write(src, "crates/y/tests/red.rs", "// red\n")
        r.commit("test(F008): failing tests")
        r.git("checkout", "-q", "main")
        cls.work = cls.base / "work"
        r.git("clone", "-q", str(src), str(cls.work), cwd=cls.base)
        cls.cfg = test_config()
        cls.cfg["features"]["done_without_spec"] = ["F001"]
        write_config(cls.work, cls.cfg)

    @classmethod
    def tearDownClass(cls) -> None:
        rm(cls.base)

    def test_stages(self) -> None:
        import datetime as dt

        report = progress.build_report(self.work, self.cfg, now=dt.datetime(2026, 11, 11, 12, tzinfo=dt.timezone.utc))
        by_id = {f["id"]: (f["stage"], f["percent"]) for f in report["features"]}
        self.assertEqual(by_id, {
            "F001": ("merged", 100.0), "F002": ("merged", 100.0), "F003": ("implementing", 45.0), "F004": ("spec-draft", 5.0),
            "F005": ("not-started", 0.0), "F006": ("implemented", 95.0), "F007": ("planned", 20.0), "F008": ("tests", 30.0),
        })
        ms = {m["id"]: m["percent"] for m in report["milestones"]}
        self.assertEqual(ms, {"M0": 81.7, "M1": 33.3, "M2": 25.0})
        self.assertEqual(report["overall"], 49.4)
        self.assertEqual(report["generatedAt"], "2026-11-11T12:00:00Z")
        self.assertEqual(progress.render_json(report), progress.render_json(progress.build_report(self.work, self.cfg, now=dt.datetime(2026, 11, 11, 12, tzinfo=dt.timezone.utc))))

    def test_cli(self) -> None:
        proc = run_cli(self.work, "progress", "--json", "--out", str(self.base / "p.json"), env={"SOURCE_DATE_EPOCH": "0"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["overall"], 49.4)
        self.assertTrue((self.base / "p.json").is_file())
        proc = run_cli(self.work, "progress")
        self.assertIn("overall 49.4%", proc.stdout)


class SourceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_tmp("source-")
        self.cfg = test_config()
        self.cfg["sources"]["text_dir"] = "specs/source/text"
        write_config(self.root, self.cfg)
        write(self.root, "specs/source/text/ts24501-hi1.md", "# TS 24.501\n\n## 5 Procedures\n\n### 5.5.1 Registration\n\n#### 5.5.1.2 Initial\n\n##### 5.5.1.2.4 Accepted\n\nThe AMF shall send REGISTRATION ACCEPT.\nTimer T3550 starts.\n\n##### 5.5.1.2.5 Not accepted\n\nOther text.\n\n### 5.5.2 Deregistration\n")

    def tearDown(self) -> None:
        rm(self.root)

    def test_builtin_clause_and_find(self) -> None:
        proc = run_cli(self.root, "source", "clause", "24.501", "5.5.1.2.4")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("The AMF shall send REGISTRATION ACCEPT.", proc.stdout)
        self.assertNotIn("Other text", proc.stdout)
        proc = run_cli(self.root, "source", "clause", "24.501", "5.5.1")
        self.assertIn("Other text", proc.stdout)
        self.assertNotIn("Deregistration", proc.stdout)
        proc = run_cli(self.root, "source", "clause", "24.501", "9.9.9")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("NEEDS CLARIFICATION", proc.stdout)
        proc = run_cli(self.root, "source", "find", "TS24501", "T3550")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("[5.5.1.2.4 Accepted]", proc.stdout)

    def test_lookup_command_wins(self) -> None:
        self.cfg["sources"]["lookup_command"] = "echo LOOKUP {doc} {clause}"
        write_config(self.root, self.cfg)
        proc = run_cli(self.root, "source", "clause", "24.501", "5.5.1.2.4")
        self.assertEqual(proc.stdout.strip(), "LOOKUP 24.501 5.5.1.2.4")


class DoctorTest(unittest.TestCase):
    def test_doctor_runs(self) -> None:
        root = make_tmp("doctor-")
        try:
            write_config(root, test_config())
            write(root, "specs/templates/spec.md", "x")
            write(root, "docs/CONSTITUTION.md", "x")
            proc = run_cli(root, "doctor")
            self.assertIn("gate full/missing: definitely-not-a-program-xyz NOT on PATH", proc.stdout)
            self.assertEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("doctor: OK", proc.stdout)
        finally:
            rm(root)


if __name__ == "__main__":
    unittest.main(verbosity=2)
