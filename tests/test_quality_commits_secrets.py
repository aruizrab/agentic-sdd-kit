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
        # C9/S13: combined short flags, abbreviated long options, global options with separate
        # values, --config-env, GIT_CONFIG_* variables, wrappers, removing the core section.
        for cmd in ['git commit -nm "x"', 'git commit -anm "x"', "git commit --no-verif -m x", "git push --no-verif",
                    "git --git-dir .git -c core.hooksPath=/dev/null commit -m x", "git --config-env=core.hooksPath=VAR commit -m x",
                    "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=/dev/null git commit -m x",
                    "env git push --no-verify", "git config --remove-section core", "git -C . commit --no-v -m x", "git merge --no-verify x"]:
            self.assertEqual(self.guard(cmd)[0], 2, cmd)
        for cmd in ['git commit -m "-n"', "git commit -mn", "git commit --amend --no-edit", 'git commit -m "fix --no-verify docs"',
                    "git commit -cHEAD", "git log -n 3", 'git commit --author "A <a@b>" -m x']:
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
        # S5: under the pipeline's role fallback every role runs as general-purpose.
        for path in ("tools/sdd/githooks/pre-commit", "tools/sdd/sddkit/quality/secrets.py", "tools/sdd/sdd.py"):
            self.assertEqual(edit(path, "general-purpose"), 2, path)
        for cmd in ("git rm -r tools/sdd/githooks", "rm -r tools/sdd/githooks", "git mv tools/sdd/githooks tools/sdd/old",
                    "sed -i s/x/y/ tools/sdd/sddkit/quality/secrets.py"):
            with redirect_stderr(io.StringIO()):
                code = hooks.bash_guard({"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": str(self.root), "agent_type": "general-purpose"})
            self.assertEqual(code, 2, cmd)
        # C13: the denial follows paths.tools_dir.
        cfg = test_config()
        cfg["paths"]["tools_dir"] = "scripts/sdd"
        write_config(self.root, cfg)
        self.assertEqual(edit("scripts/sdd/sddkit/quality/size.py", "implementer"), 2)
        self.assertEqual(edit("scripts/sdd/githooks/pre-commit", "general-purpose"), 2)

    def test_settings_deny_list(self) -> None:
        deny = json.loads((KIT / "claude" / "settings.json").read_text())["permissions"]["deny"]
        for entry in ("Bash(git push --no-verify *)", "Bash(git commit --no-verify *)", "Bash(git config core.hooksPath *)",
                      "Bash(git commit --no-v*)", "Bash(git commit -n*)", "Bash(git * -c core.hooksPath*)"):
            self.assertIn(entry, deny)


class SecretScanTextTest(unittest.TestCase):
    """The commit-msg secret scan reads every line git may store, not the cleaned message."""

    CUT = "# ------------------------ >8 ------------------------"

    def test_keeps_comment_lines_and_a_bare_scissors_line(self) -> None:
        from sddkit.quality import commits

        raw = "fix(x): y\n\n# deploy token X\n"
        self.assertEqual(commits.secret_scan_text(raw), raw)
        raw = f"fix(x): y\n{self.CUT}\n# token X\n"
        self.assertEqual(commits.secret_scan_text(raw), raw)
        self.assertNotIn("token", commits.clean(raw))  # the format check still reads the cleaned text

    def test_cuts_only_the_verbose_diff(self) -> None:
        from sddkit.quality import commits

        raw = f"fix(x): y\n# kept\n{self.CUT}\n# Do not modify or remove the line above.\n# Everything below it will be ignored.\ndiff --git a/x b/x\n"
        self.assertEqual(commits.secret_scan_text(raw), "fix(x): y\n# kept\n")
        semi = raw.replace("# ", "; ")
        self.assertEqual(commits.secret_scan_text(semi), "fix(x): y\n; kept\n")  # any core.commentChar
        mixed = semi.replace("; Do not", "# Do not")
        self.assertEqual(commits.secret_scan_text(mixed), mixed)  # the marker must use the scissors' character


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

    def test_commit_trailers_follow_the_commit_not_the_checker(self) -> None:
        """C3: an agent-run check passes a human commit; CI fails an agent commit without trailers."""
        cfg = json.loads((self.root / "sdd.config.json").read_text())
        cfg["quality"]["hooks"].update({"required_trailers": ["^Co-Authored-By: .+ <.+>$"], "required_trailers_env": "CLAUDECODE"})
        write_config(self.root, cfg)
        write(self.root, "a.md", "a\n")
        self.repo.commit("docs(F001): owner commit, no trailer")
        agent = {"CLAUDECODE": "1", "CI": ""}
        ci = {"CLAUDECODE": "", "CI": "true", "SDD_BASE_REF": "main"}
        for env in (agent, ci):
            proc = run_cli(self.root, "check", "commits", env=env)
            self.assertEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("trailers not checked", proc.stdout)
        cfg["quality"]["hooks"]["trailer_authors"] = r"<agent@example\.com>$"
        write_config(self.root, cfg)
        for env in (agent, ci):
            self.assertEqual(run_cli(self.root, "check", "commits", env=env).returncode, 0)
        self.repo.env.update({"GIT_AUTHOR_NAME": "Agent", "GIT_AUTHOR_EMAIL": "agent@example.com"})
        write(self.root, "b.md", "b\n")
        sha = self.repo.commit("docs(F001): agent commit from a hookless clone")
        for env in (agent, ci):
            proc = run_cli(self.root, "check", "commits", "--json", env=env)
            self.assertEqual(proc.returncode, 1, proc.stdout)
            self.assertEqual([(v["file"], v["kind"]) for v in json.loads(proc.stdout)["violations"]], [(sha[:12], "commit-trailer")])
        write(self.root, "c.md", "c\n")
        self.repo.commit("docs(F001): agent commit\n\nCo-Authored-By: Agent <agent@example.com>")
        proc = run_cli(self.root, "check", "commits", "--json", env=ci)
        self.assertEqual(len(json.loads(proc.stdout)["violations"]), 1)

    def test_protected_changes_need_owner_approval(self) -> None:
        """S5/S4/S7: CI flags a branch that changes the kit, the configuration or gitleaks' files."""
        self.assertEqual(run_cli(self.root, "check", "protected").returncode, 0)
        write(self.root, "src/app.py", "x = 1\n")
        self.repo.commit("feat: ordinary change")
        self.assertEqual(run_cli(self.root, "check", "protected").returncode, 0)
        write(self.root, "tools/sdd/sddkit/quality/secrets.py", "def check(*a):\n    pass\n")
        write(self.root, ".gitleaks.toml", "[allowlist]\npaths = ['.*']\n")
        self.repo.commit("chore: weaken the checker")
        cfg = json.loads((self.root / "sdd.config.json").read_text())
        cfg["quality"]["secrets"]["allow_globs"] = ["**"]
        write_config(self.root, cfg)  # uncommitted counts too
        proc = run_cli(self.root, "check", "protected", "--json", env={"SDD_ALLOW_PROTECTED": ""})
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertEqual(sorted(v["file"] for v in json.loads(proc.stdout)["violations"]),
                         [".gitleaks.toml", "sdd.config.json", "tools/sdd/sddkit/quality/secrets.py"])
        proc = run_cli(self.root, "check", "protected", env={"SDD_ALLOW_PROTECTED": "1"})
        self.assertEqual(proc.returncode, 0)
        self.assertIn("owner-approved", proc.stdout)

    def _protected(self, **env) -> tuple:
        e = {"SDD_ALLOW_PROTECTED": "", "SHLVL": "1", "CI": "true", "SDD_BASE_REF": "main"}
        e.update(env)
        proc = run_cli(self.root, "check", "protected", "--json", env=e)
        return proc.returncode, sorted((v["kind"], v["file"]) for v in json.loads(proc.stdout)["violations"])

    def test_protected_rules_come_from_the_merge_base(self) -> None:
        """S5: a branch cannot unprotect the config, move the kit or rename the approval variable."""
        cfg = json.loads((self.root / "sdd.config.json").read_text())
        cfg["protected_paths"] = [e for e in cfg["protected_paths"] if e["glob"] != "sdd.config.json"]
        cfg["paths"]["tools_dir"] = "elsewhere"
        write_config(self.root, cfg)
        write(self.root, "tools/sdd/sddkit/quality/secrets.py", "def check(*a):\n    pass\n")
        write(self.root, "elsewhere/x.py", "x = 1\n")
        self.repo.commit("chore: move the kit and unprotect the config")
        self.assertEqual(self._protected(), (1, [("protected-changed", "elsewhere/x.py"), ("protected-changed", "sdd.config.json"),
                                                 ("protected-changed", "tools/sdd/sddkit/quality/secrets.py")]))
        self.repo.git("switch", "-q", "main")
        self.repo.git("switch", "-q", "-c", "feat/F002-y")
        cfg = json.loads((self.root / "sdd.config.json").read_text())
        cfg["override_env"] = "SHLVL"
        write_config(self.root, cfg)
        self.repo.commit("chore: rename the approval variable")
        self.assertEqual(self._protected(), (1, [("protected-changed", "sdd.config.json")]))
        self.assertEqual(self._protected(SDD_ALLOW_PROTECTED="1"), (0, []))

    def test_ci_needs_the_base_ref_from_the_job(self) -> None:
        """Under CI=true the base comes only from SDD_BASE_REF: a branch could name itself main_branch."""
        cfg = json.loads((self.root / "sdd.config.json").read_text())
        cfg["features"]["main_branch"] = "feat/F001-x"
        write_config(self.root, cfg)
        write(self.root, "src/k.py", "k = '" + AWS + "'\n")
        self.repo.commit("chore: grade myself")
        for check in ("protected", "secrets", "commits", "baseline"):
            proc = run_cli(self.root, "check", check, env={"CI": "true", "SDD_BASE_REF": ""})
            self.assertEqual(proc.returncode, 2, check + proc.stdout)
            self.assertIn("CI=true needs SDD_BASE_REF", proc.stdout)
        self.assertEqual(self._protected(), (1, [("protected-changed", "sdd.config.json")]))
        # Project gate commands get the same base from `sdd merge-base` (never a hard-coded main).
        proc = run_cli(self.root, "merge-base", env={"CI": "true", "SDD_BASE_REF": ""})
        self.assertEqual((proc.returncode, proc.stdout), (2, ""))
        self.assertIn("CI=true needs SDD_BASE_REF", proc.stderr)
        main = self.repo.git("rev-parse", "main").strip()
        proc = run_cli(self.root, "merge-base", env={"CI": "true", "SDD_BASE_REF": "main"})
        self.assertEqual((proc.returncode, proc.stdout.strip()), (0, main), proc.stderr)
        proc = run_cli(self.root, "merge-base", env={"CI": "", "SDD_BASE_REF": "no-such-ref"})
        self.assertEqual(proc.returncode, 2)

    def test_gitattributes_hiding_content_is_protected(self) -> None:
        """S1: a branch that marks files `binary` or `-diff` needs the owner's approval."""
        write(self.root, ".gitattributes", "*.png filter=lfs diff=lfs merge=lfs -text\n")
        self.repo.commit("chore: lfs")
        self.assertEqual(self._protected(), (0, []))
        write(self.root, "sub/.gitattributes", "# -diff in a comment\n*.cfg binary\n")
        write(self.root, ".gitattributes", "*.png filter=lfs diff=lfs merge=lfs -text\ncreds.txt -diff\n")
        self.repo.commit("chore: hide")
        self.assertEqual(self._protected(), (1, [("protected-gitattributes", ".gitattributes"),
                                                 ("protected-gitattributes", "sub/.gitattributes")]))
        self.assertEqual(self._protected(SDD_ALLOW_PROTECTED="1"), (0, []))

    def test_diff_paths_with_spaces_and_quotes(self) -> None:
        """S11: names git prints with a trailing TAB or C-quoted are normalised."""
        from sddkit.quality import vcs
        for name in ("my file.txt", 'q"uote.txt', "back\\slash.txt", "caf\u00e9.txt"):
            write(self.root, name, "a\n")
        self.repo.git("add", "-A")
        added = vcs.staged_added_lines(self.root)
        self.assertEqual(sorted(added), sorted(["back\\slash.txt", "caf\u00e9.txt", "my file.txt", 'q"uote.txt']))
        self.assertEqual(added["my file.txt"], [(1, "a")])

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
