"""End-to-end: install the kit into a fresh repo, then exercise the tools and hooks there."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

from helpers import KIT, GitRepo, make_tmp, rm, write


def sh(*args, cwd, env=None, inp=None):
    e = dict(os.environ)
    e.pop("SDD_ROOT", None)
    e.update(env or {})
    return subprocess.run(list(args), cwd=str(cwd), env=e, input=inp, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


class InstallTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_tmp("install-")
        self.repo = GitRepo(self.root)
        self.repo.init()
        write(self.root, "README.md", "# demo\n")
        write(self.root, "docs/CONSTITUTION.md", "# my own constitution\n")
        self.repo.commit("init")

    def tearDown(self) -> None:
        rm(self.root)

    def test_install_is_non_destructive_and_tools_work(self) -> None:
        proc = sh("bash", str(KIT / "install.sh"), str(self.root), "--name", "demo", "--config", "python", cwd=KIT)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("skipped:", proc.stdout)
        self.assertIn("docs/CONSTITUTION.md", proc.stdout)
        self.assertEqual((self.root / "docs" / "CONSTITUTION.md").read_text(), "# my own constitution\n")
        self.assertIn("# demo: agent guide", (self.root / "CLAUDE.md").read_text())
        cfg = json.loads((self.root / "sdd.config.json").read_text())
        self.assertEqual(cfg["project"]["name"], "demo")
        self.assertEqual(cfg["project"]["languages"], ["python"])
        for rel in [".claude/settings.json", ".claude/agents/product-owner.md", ".claude/skills/feature/SKILL.md", ".claude/hooks/protect-paths.sh",
                    "tools/sdd/sdd.py", "tools/sdd/pipeline/feature-pipeline.js", "specs/templates/spec.md", "specs/features/README.md", "docs/DEVELOPMENT.md", "docs/ROADMAP.md"]:
            self.assertTrue((self.root / rel).is_file(), rel)
        self.assertTrue(os.access(self.root / ".claude/hooks/protect-paths.sh", os.X_OK))
        for hook in ("pre-commit", "commit-msg", "pre-push"):
            self.assertTrue(os.access(self.root / "tools/sdd/githooks" / hook, os.X_OK), hook)
        # install.sh never sets core.hooksPath; it prints the `sdd hooks install` hint.
        self.assertEqual(sh("git", "config", "--local", "--get", "core.hooksPath", cwd=self.root).stdout, "")
        self.assertIn("hooks install", proc.stdout)
        self.assertIn(".wt/\n", (self.root / ".gitignore").read_text())
        self.assertFalse(list(self.root.rglob("__pycache__")))

        # Re-running changes nothing without --force; --force overwrites the template files.
        (self.root / "CLAUDE.md").write_text("mine\n")
        proc = sh("bash", str(KIT / "install.sh"), str(self.root), "--config", "python", cwd=KIT)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual((self.root / "CLAUDE.md").read_text(), "mine\n")
        proc = sh("bash", str(KIT / "install.sh"), str(self.root), "--name", "demo", "--config", "python", "--force", cwd=KIT)
        self.assertIn("# demo: agent guide", (self.root / "CLAUDE.md").read_text())
        self.assertNotEqual((self.root / "docs" / "CONSTITUTION.md").read_text(), "# my own constitution\n")

        # Tools run from inside the installed repo.
        tool = [sys.executable, "-I", "tools/sdd/sdd.py"]
        # The example config ships a placeholder trailer: validate must say so until it is set.
        proc = sh(*tool, "config", "--validate", cwd=self.root)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("commit.trailers[0]: still the template placeholder", proc.stdout)
        cfg["commit"]["trailers"] = ["Co-Authored-By: Demo Bot <demo@localhost>"]
        (self.root / "sdd.config.json").write_text(json.dumps(cfg, indent=2) + "\n")
        self.assertEqual(sh(*tool, "config", "--validate", cwd=self.root).returncode, 0)
        proc = sh(*tool, "config", "--pipeline", "--compact", cwd=self.root)
        self.assertEqual(len(proc.stdout.strip().splitlines()), 1)
        self.assertEqual(json.loads(proc.stdout)["features"]["main_branch"], "main")
        proc = sh(*tool, "new-feature", "001", "hello", cwd=self.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue((self.root / "specs/features/001-hello/spec.md").is_file())
        spec = (self.root / "specs/features/001-hello/spec.md").read_text()
        self.assertIn("Feature: F001", spec)
        self.assertIn("R10-R19", spec)
        self.assertEqual(sh(*tool, "trace", "--check", cwd=self.root).returncode, 0)
        proc = sh(*tool, "spec-info", "specs/features/001-hello", cwd=self.root)
        info = json.loads(proc.stdout)
        self.assertEqual(info["status"], "Draft")
        self.assertEqual(info["needsClarification"], 0)  # the template's example question lives in a comment
        proc = sh(*tool, "tasks", "specs/features/001-hello", "--validate", "--no-trace", cwd=self.root)
        plan = json.loads(proc.stdout)
        self.assertEqual(plan["problems"], [])  # placeholder task lines are not tasks
        self.assertEqual(sum(len(w["tasks"]) for w in plan["waves"]), 0)
        proc = sh(*tool, "config", "--pipeline", cwd=self.root)
        view = json.loads(proc.stdout)
        self.assertEqual(view["root"], str(self.root.resolve()))
        self.assertIn("trace", view["gates"]["fast"])

        # The installed hooks protect the installed config and constitution.
        hook_in = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": "sdd.config.json"}, "cwd": str(self.root)})
        proc = sh("bash", ".claude/hooks/protect-paths.sh", cwd=self.root, env={"CLAUDE_PROJECT_DIR": str(self.root)}, inp=hook_in)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        proc = sh("bash", ".claude/hooks/bash-guard.sh", cwd=self.root, env={"CLAUDE_PROJECT_DIR": str(self.root)}, inp=json.dumps({"tool_input": {"command": "git stash"}, "cwd": str(self.root)}))
        self.assertEqual(proc.returncode, 2)
        proc = sh("bash", ".claude/hooks/bash-guard.sh", cwd=self.root, env={"CLAUDE_PROJECT_DIR": str(self.root)}, inp=json.dumps({"tool_input": {"command": "ls"}, "cwd": str(self.root)}))
        self.assertEqual(proc.returncode, 0)
        # Gates run (fast list from the python example: fmt/lint will be missing tools -> FAIL exit 127 unless optional) -> just check the marker.
        proc = sh(*tool, "gates", "--only", "trace", cwd=self.root)
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("GATES_EXIT=0", proc.stdout)

    def test_adoption_commit_passes_the_hooks(self) -> None:
        """C2: the kit's own files are not project code: the adoption commit passes the installed
        hooks with no baseline, and the kit never lands in the baseline or the commit test step."""
        for config in ("python", "go", "template"):
            with self.subTest(config=config):
                root = make_tmp(f"adopt-{config}-")
                try:
                    repo = GitRepo(root)
                    repo.init()
                    write(root, "README.md", "# demo\n")
                    repo.commit("init")
                    args = ["--config", config] if config != "template" else []
                    proc = sh("bash", str(KIT / "install.sh"), str(root), "--name", "demo", *args, cwd=KIT)
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    self.assertIn("__pycache__/\n", (root / ".gitignore").read_text())
                    env = {k: v for k, v in repo.env.items() if k not in ("CLAUDECODE", "CI", "SDD_BASE_REF", "SDD_FEATURE")}
                    tool = [sys.executable, "-I", "tools/sdd/sdd.py"]
                    proc = subprocess.run([*tool, "hooks", "install"], cwd=str(root), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                    self.assertEqual(proc.returncode, 0, proc.stdout)
                    proc = subprocess.run([*tool, "check", "size"], cwd=str(root), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                    self.assertNotIn("tools/sdd/", proc.stdout)
                    subprocess.run(["git", "add", "-A"], cwd=str(root), env=env, check=True)
                    proc = subprocess.run(["git", "-c", "commit.gpgsign=false", "commit", "-m", "chore: adopt the SDD kit"], cwd=str(root), env=env,
                                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                    self.assertEqual(proc.returncode, 0, proc.stdout)
                    self.assertNotIn("size:", proc.stdout)
                    self.assertIn("test step skipped", proc.stdout)
                    self.assertFalse((root / "sdd.baseline.json").exists())
                    files = subprocess.run(["git", "ls-files"], cwd=str(root), env=env, stdout=subprocess.PIPE, text=True).stdout
                    self.assertNotIn(".pyc", files)
                finally:
                    rm(root)

    def test_dry_run_writes_nothing(self) -> None:
        proc = sh("bash", str(KIT / "install.sh"), str(self.root), "--dry-run", cwd=KIT)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse((self.root / "sdd.config.json").exists())
        self.assertFalse((self.root / ".claude").exists())
        self.assertIn("+ tools/sdd/sdd.py", proc.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
