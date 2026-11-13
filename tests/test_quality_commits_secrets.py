"""WI1 AC 13 and 14: bypass protection (bash-guard, role denials), `sdd check commits` and `sdd check secrets`."""

from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stderr

from helpers import KIT, GitRepo, make_tmp, rm, run_cli, test_config, write, write_config

from sddkit import config as cfgmod
from sddkit import hooks

AWS = "AKIA" + "ABCDEFGHIJKLMNOP"
PRIVATE = "-----BEGIN " + "RSA PRIVATE KEY-----"


class BypassGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_tmp("guard-")
        GitRepo(self.root).init()
        write_config(self.root, test_config(role_denied_paths=cfgmod.DEFAULTS["role_denied_paths"]))
        self.env = dict(os.environ)
        os.environ.pop("SDD_ALLOW_PROTECTED", None)
        os.environ.pop("SDD_ALLOW_DESTRUCTIVE_GIT", None)
        os.environ["CLAUDE_PROJECT_DIR"] = str(self.root)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.env)
        rm(self.root)

    def guard(self, cmd: str) -> tuple:
        err = io.StringIO()
        with redirect_stderr(err):
            code = hooks.bash_guard({"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": str(self.root)})
        return code, err.getvalue()

    def test_bash_guard(self) -> None:
        for cmd in ["git push --no-verify", "git push origin main --no-verify", "git commit -n -m x", "git commit --no-verify -m x",
                    "git config core.hooksPath x", "git config --local core.hooksPath x", "git config --unset core.hooksPath",
                    "git config --unset-all core.hooksPath", "git -c core.hooksPath=/dev/null commit -m x"]:
            code, err = self.guard(cmd)
            self.assertEqual(code, 2, cmd)
            self.assertIn("BLOCKED", err, cmd)
        for cmd in ["python3 -I tools/sdd/sdd.py hooks install", "git config --get core.hooksPath", "git push -n", "git push origin main"]:
            self.assertEqual(self.guard(cmd)[0], 0, cmd)

    def test_code_writing_roles_cannot_edit_hooks_or_checkers(self) -> None:
        def edit(path: str, agent: str) -> int:
            with redirect_stderr(io.StringIO()):
                return hooks.protect_paths({"tool_name": "Edit", "tool_input": {"file_path": path}, "cwd": str(self.root), "agent_type": agent})

        for agent in ("implementer", "test-author"):
            for path in ("tools/sdd/githooks/pre-commit", "tools/sdd/sddkit/quality/size.py", "tools/sdd/sdd.py"):
                self.assertEqual(edit(path, agent), 2, (agent, path))
        self.assertEqual(edit("tools/sdd/sddkit/quality/size.py", ""), 0)  # the orchestrator may (lesson 14)
        self.assertEqual(edit("src/app.py", "implementer"), 0)

    def test_settings_deny_list(self) -> None:
        deny = json.loads((KIT / "claude" / "settings.json").read_text())["permissions"]["deny"]
        for entry in ("Bash(git push --no-verify *)", "Bash(git commit --no-verify *)", "Bash(git config core.hooksPath *)"):
            self.assertIn(entry, deny)


class CommitsAndSecretsCheckTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_tmp("cs-")
        self.repo = GitRepo(self.root)
        self.repo.init()
        cfg = test_config()
        cfg["quality"]["secrets"].update({"gitleaks": False, "allow_globs": ["vectors/**"]})
        write_config(self.root, cfg)
        write(self.root, "README.md", "x\n")
        self.repo.commit("chore: init")
        self.repo.git("switch", "-q", "-c", "feat/F001-x")

    def tearDown(self) -> None:
        rm(self.root)

    def test_commits(self) -> None:
        proc = run_cli(self.root, "check", "commits")
        self.assertEqual(proc.returncode, 0, proc.stdout)  # empty range
        write(self.root, "a.md", "a\n")
        self.repo.commit("docs(F001): fine")
        write(self.root, "b.md", "b\n")
        bad = self.repo.commit("updated things")
        self.repo.git("switch", "-q", "-c", "side")
        write(self.root, "c.md", "c\n")
        self.repo.commit("docs: side")
        self.repo.git("switch", "-q", "feat/F001-x")
        self.repo.git("merge", "-q", "--no-ff", "-m", "Merge branch side", "side")
        proc = run_cli(self.root, "check", "commits", "--json")
        out = json.loads(proc.stdout)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual([(v["file"], v["kind"]) for v in out["violations"]], [(bad[:12], "commit-format")])

    def test_secrets(self) -> None:
        write(self.root, "src/a.py", f"x = 1\nk = '{AWS}'\n")
        self.repo.commit("feat: committed secret")
        write(self.root, "src/b.py", f"y = 2\n\n{PRIVATE}\n")  # uncommitted
        write(self.root, "vectors/key.pem", PRIVATE + "\n")
        self.repo.git("add", "vectors/key.pem", "src/b.py")
        proc = run_cli(self.root, "check", "secrets")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("src/a.py:2: secret rule aws-access-key-id", proc.stdout)
        self.assertIn("src/b.py:3: secret rule private-key", proc.stdout)
        self.assertNotIn("vectors/key.pem", proc.stdout)
        self.assertNotIn(AWS, proc.stdout + proc.stderr)
        self.assertNotIn("BEGIN RSA", proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
