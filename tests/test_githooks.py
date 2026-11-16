"""WI1 AC 1-6: git hooks installed with `sdd hooks install`, run in throwaway repos under .scratch/."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from helpers import GIT_ENV, KIT, make_tmp, rm, test_config, write, write_config

PY = sys.executable
AWS = "AKIA" + "ABCDEFGHIJKLMNOP"  # built at runtime: no secret-shaped literal in the kit


def no_gitleaks_path() -> str:
    dirs = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d and not os.access(os.path.join(d, "gitleaks"), os.X_OK)]
    return os.pathsep.join(dirs)


class HookRepo:
    """A throwaway repository with the kit's tools copied in (so the relative hooksPath resolves)."""

    def __init__(self, cfg: dict, prefix: str = "hooks-") -> None:
        self.root = make_tmp(prefix)
        self.env = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "CI", "SDD_ROOT", "SDD_FEATURE", "SDD_BASE_REF")}
        self.env.update(GIT_ENV)
        self.git("init", "-q", "-b", "main")
        shutil.copytree(KIT / "tools" / "sdd", self.root / "tools" / "sdd", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        write(self.root, ".gitignore", ".sdd/\n")
        self.cfg = cfg
        write_config(self.root, cfg)
        write(self.root, "README.md", "# t\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "chore: init")

    def run(self, *args, env=None, cwd=None, check=False, inp=None) -> subprocess.CompletedProcess:
        e = dict(self.env)
        e.update(env or {})
        proc = subprocess.run(list(args), cwd=str(cwd or self.root), env=e, input=inp, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if check and proc.returncode != 0:
            raise AssertionError(f"{args} failed: {proc.stdout}{proc.stderr}")
        return proc

    def git(self, *args, **kw) -> subprocess.CompletedProcess:
        return self.run("git", "-c", "commit.gpgsign=false", *args, check=kw.pop("check", True), **kw)

    def sdd(self, *args, **kw) -> subprocess.CompletedProcess:
        return self.run(PY, "-I", "tools/sdd/sdd.py", *args, **kw)

    def install(self) -> None:
        proc = self.sdd("hooks", "install")
        assert proc.returncode == 0, proc.stdout + proc.stderr

    def set_cfg(self) -> None:
        write_config(self.root, self.cfg)

    def commit(self, msg: str, *files: str, env=None, cwd=None, extra=()) -> subprocess.CompletedProcess:
        if files:
            self.git("add", *files, cwd=cwd)
        return self.git("commit", "-m", msg, *extra, env=env, cwd=cwd, check=False)

    def state(self) -> tuple:
        return (self.git("status", "--porcelain=v2").stdout, self.git("stash", "list").stdout,
                self.git("ls-files", "-s").stdout, self.git("config", "--list", "--local").stdout)

    def close(self) -> None:
        rm(self.root)


def base_cfg(**quality) -> dict:
    cfg = test_config()
    cfg["tests"]["run"] = "echo RUN-ROOT"
    cfg["quality"]["secrets"]["gitleaks"] = False
    for k, v in quality.items():
        if isinstance(v, dict):
            cfg["quality"][k].update(v)
        else:
            cfg["quality"][k] = v
    return cfg


class InstallTest(unittest.TestCase):
    def setUp(self) -> None:
        self.r = HookRepo(base_cfg())

    def tearDown(self) -> None:
        self.r.close()

    def local_config(self) -> str:
        return self.r.git("config", "--list", "--local").stdout

    def test_install_sets_only_hooks_path(self) -> None:
        before = self.local_config()
        self.r.install()
        after = self.local_config()
        self.assertEqual(self.r.git("config", "--get", "core.hooksPath").stdout.strip(), "tools/sdd/githooks")
        self.assertEqual(sorted(set(after.splitlines()) - set(before.splitlines())), ["core.hookspath=tools/sdd/githooks"])
        self.assertTrue(set(before.splitlines()) <= set(after.splitlines()))
        proc = self.r.sdd("hooks", "install")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("already installed", proc.stdout)
        self.assertEqual(self.local_config(), after)

    def test_refuses_over_existing_hooks(self) -> None:
        hook = self.r.root / ".git" / "hooks" / "pre-push"
        hook.write_text("#!/bin/sh\ngit lfs pre-push \"$@\"\n")
        hook.chmod(0o755)
        before = self.local_config()
        proc = self.r.sdd("hooks", "install")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("pre-push", proc.stderr)
        self.assertEqual(self.local_config(), before)
        proc = self.r.sdd("hooks", "install", "--force")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(self.r.git("config", "--get", "core.hooksPath").stdout.strip(), "tools/sdd/githooks")

    def test_refuses_over_other_hooks_path(self) -> None:
        self.r.git("config", "core.hooksPath", ".husky")
        before = self.local_config()
        proc = self.r.sdd("hooks", "install")
        self.assertEqual(proc.returncode, 1)
        self.assertIn(".husky", proc.stderr)
        self.assertEqual(self.local_config(), before)
        self.assertEqual(self.r.sdd("hooks", "install", "--force").returncode, 0)

    def test_refuses_over_global_hooks_path(self) -> None:
        """S3/C8: a global core.hooksPath (organisation hooks) is never shadowed without --force."""
        org = self.r.root.parent / (self.r.root.name + "-orghooks")
        org.mkdir()
        (org / "pre-commit").write_text("#!/bin/sh\necho ORG-HOOK\n")
        (org / "pre-commit").chmod(0o755)
        gcfg = self.r.root.parent / (self.r.root.name + "-gitconfig")
        gcfg.write_text(f"[core]\n\thooksPath = {org}\n")
        try:
            env = {"GIT_CONFIG_GLOBAL": str(gcfg)}
            before = self.local_config()
            proc = self.r.sdd("hooks", "install", env=env)
            self.assertEqual(proc.returncode, 1, proc.stdout)
            self.assertIn(str(gcfg), proc.stderr)
            self.assertEqual(self.local_config(), before)
            self.assertEqual(self.r.sdd("hooks", "install", "--force", env=env).returncode, 0)
        finally:
            rm(org)
            gcfg.unlink()

    def test_root_is_the_target_repository(self) -> None:
        """S2: --root never configures the repository of the cwd; missing hook scripts refuse."""
        other = HookRepo(base_cfg(), prefix="other-")
        try:
            other_before = other.git("config", "--list", "--local").stdout
            proc = other.run(PY, "-I", str(self.r.root / "tools/sdd/sdd.py"), "--root", str(self.r.root), "hooks", "install")
            self.assertEqual(proc.returncode, 1, proc.stdout)
            self.assertIn("not the repository of the current directory", proc.stderr)
            self.assertEqual(other.git("config", "--list", "--local").stdout, other_before)
            self.assertEqual(self.r.git("config", "--get", "core.hooksPath", check=False).stdout, "")
            # From outside any repository, --root picks the target.
            outside = self.r.root.parent
            if subprocess.run(["git", "-C", str(outside), "rev-parse"], capture_output=True).returncode != 0:
                proc = self.r.run(PY, "-I", str(self.r.root / "tools/sdd/sdd.py"), "--root", str(self.r.root), "hooks", "install", cwd=outside)
                self.assertEqual(proc.returncode, 0, proc.stderr)
            # A checkout without the hook scripts: no config write.
            shutil.rmtree(other.root / "tools" / "sdd" / "githooks")
            proc = other.sdd("hooks", "install")
            self.assertEqual(proc.returncode, 1)
            self.assertIn("lacks", proc.stderr)
            self.assertEqual(other.git("config", "--list", "--local").stdout, other_before)
        finally:
            other.close()

    def test_doctor(self) -> None:
        proc = self.r.sdd("doctor")
        self.assertIn("[warn] git hooks installed", proc.stdout)
        for name in ("pre-commit", "commit-msg", "pre-push"):
            p = self.r.root / ".git" / "hooks" / name
            p.write_text(f"#!/bin/sh\ngit lfs {name} \"$@\" || exit 1\npython3 -I tools/sdd/sdd.py hooks run {name} \"$@\"\n")
            p.chmod(0o755)
        proc = self.r.sdd("doctor")
        self.assertIn("[ok  ] git hooks installed", proc.stdout)
        for p in (self.r.root / ".git" / "hooks").iterdir():
            if not p.name.endswith(".sample"):
                p.unlink()
        self.r.install()
        self.assertIn("[ok  ] git hooks installed", self.r.sdd("doctor").stdout)


class CommitMsgTest(unittest.TestCase):
    def setUp(self) -> None:
        self.r = HookRepo(base_cfg())
        self.r.install()

    def tearDown(self) -> None:
        self.r.close()

    def attempt(self, msg: str, cwd=None, env=None) -> subprocess.CompletedProcess:
        root = Path(cwd or self.r.root)
        n = len(list(root.glob("notes/*")))
        write(root, f"notes/n{n}.md", "x\n")
        return self.r.commit(msg, f"notes/n{n}.md", cwd=cwd, env=env)

    def test_format(self) -> None:
        for bad in ["update stuff", "feature: x", "feat:  ", "feat(F011):"]:
            proc = self.attempt(bad)
            self.assertNotEqual(proc.returncode, 0, bad)
            self.assertIn("expected `<type>", proc.stderr, bad)
        for good in ["feat(F011): x", "fix(core)!: y", 'Revert "feat(F011): x"', "docs: plain"]:
            proc = self.attempt(good)
            self.assertEqual(proc.returncode, 0, good + proc.stderr)

    def test_scope_regex_and_worktree(self) -> None:
        self.r.cfg["quality"]["hooks"]["scope_regex"] = r"F\d{3}|core"
        self.r.set_cfg()
        self.r.commit("chore(core): scope rule", "sdd.config.json")
        for bad in ["feat: x", "feat(other): x"]:
            proc = self.attempt(bad)
            self.assertNotEqual(proc.returncode, 0, bad)
            self.assertIn("scope", proc.stderr)
        self.assertEqual(self.attempt("feat(F011): x").returncode, 0)
        wt = self.r.root / ".wt" / "F011"
        self.r.git("worktree", "add", "-q", "-b", "feat/F011-x", str(wt))
        self.assertNotEqual(self.attempt("update stuff", cwd=wt).returncode, 0)
        self.assertNotEqual(self.attempt("feat(other): x", cwd=wt).returncode, 0)
        self.assertEqual(self.attempt("fix(F011)!: y", cwd=wt).returncode, 0)

    def _merges(self, cwd: Path) -> None:
        g = lambda *a, **k: self.r.git(*a, cwd=cwd, **k)  # noqa: E731
        start = g("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        g("switch", "-q", "-c", f"side-{cwd.name}")
        write(cwd, f"side-{cwd.name}.md", "side\n")
        proc = self.r.commit("docs: side", f"side-{cwd.name}.md", cwd=cwd)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        g("switch", "-q", start)
        write(cwd, f"main-{cwd.name}.md", "main\n")
        self.assertEqual(self.r.commit("docs: main", f"main-{cwd.name}.md", cwd=cwd).returncode, 0)
        proc = g("merge", "--no-ff", "-m", "merge(F011): lane 1", f"side-{cwd.name}", check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("merge commit", proc.stderr)
        # A conflicted merge concluded with `git commit --no-edit`.
        g("switch", "-q", "-c", f"c1-{cwd.name}")
        write(cwd, f"c-{cwd.name}.md", "one\n")
        self.r.commit("docs: one", f"c-{cwd.name}.md", cwd=cwd)
        g("switch", "-q", start)
        write(cwd, f"c-{cwd.name}.md", "two\n")
        self.r.commit("docs: two", f"c-{cwd.name}.md", cwd=cwd)
        self.assertNotEqual(g("merge", f"c1-{cwd.name}", check=False).returncode, 0)
        write(cwd, f"c-{cwd.name}.md", "one and two\n")
        g("add", f"c-{cwd.name}.md")
        proc = self.r.git("commit", "--no-edit", cwd=cwd, check=False, env={"CLAUDECODE": "1"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("merge commit", proc.stderr)

    def test_merge_commits_skip_format_trailers_tests(self) -> None:
        self.r.cfg["quality"]["hooks"]["required_trailers"] = ["^Co-Authored-By: .+ <.+>$"]
        self.r.cfg["tests"]["run"] = "exit 1"
        self.r.set_cfg()
        self.r.git("commit", "-q", "-am", "chore: config\n\nCo-Authored-By: T <t@localhost>", env={"CLAUDECODE": "1"})
        self._merges(self.r.root)
        wt = self.r.root / ".wt" / "M"
        self.r.git("worktree", "add", "-q", "-b", "wt-main", str(wt))
        self._merges(wt)

    def test_trailers(self) -> None:
        self.r.cfg["quality"]["hooks"]["required_trailers"] = ["^Co-Authored-By: .+ <.+>$"]
        self.r.set_cfg()
        self.r.commit("chore: trailers", "sdd.config.json")
        proc = self.attempt("feat(F011): x", env={"CLAUDECODE": "1"})
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("^Co-Authored-By: .+ <.+>$", proc.stderr)
        self.assertEqual(self.attempt("feat(F011): x\n\nCo-Authored-By: Agent <noreply@example.com>", env={"CLAUDECODE": "1"}).returncode, 0)
        self.assertEqual(self.attempt("feat(F011): x").returncode, 0)  # a human shell: not required
        self.r.cfg["quality"]["hooks"]["required_trailers_env"] = ""
        self.r.set_cfg()
        self.assertNotEqual(self.attempt("feat(F011): x").returncode, 0)


class SecretsHookTest(unittest.TestCase):
    def setUp(self) -> None:
        self.r = HookRepo(base_cfg(secrets={"allow_globs": ["vectors/**"], "gitleaks": False}))
        self.r.install()

    def tearDown(self) -> None:
        self.r.close()

    def test_staged_and_message_secrets(self) -> None:
        write(self.r.root, "src/conf.py", f"A = 1\nKEY = '{AWS}'\n")
        proc = self.r.commit("feat: conf", "src/conf.py")
        self.assertNotEqual(proc.returncode, 0)
        out = proc.stdout + proc.stderr
        self.assertIn("aws-access-key-id", out)
        self.assertIn("src/conf.py:2", out)
        self.assertNotIn(AWS, out)
        self.r.git("rm", "-q", "--cached", "src/conf.py")
        write(self.r.root, "vectors/v1.txt", AWS + "\n")
        self.assertEqual(self.r.commit("test: vector", "vectors/v1.txt").returncode, 0)
        write(self.r.root, "notes.md", "x\n")
        proc = self.r.commit(f"docs: note {AWS}", "notes.md")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("commit message", proc.stderr)
        self.assertNotIn(AWS, proc.stdout + proc.stderr)
        # `git commit -m` keeps comment-character lines and a bare scissors line (cleanup `whitespace`):
        # a secret there would be stored, so the scan reads them too.
        for msg in (f"docs: note\n\n# deploy key {AWS}\n", f"docs: note\n# ------------------------ >8 ------------------------\n# {AWS}\n"):
            with self.subTest(msg=msg.splitlines()[1][:12]):
                proc = self.r.commit(msg, "notes.md")
                self.assertNotEqual(proc.returncode, 0, proc.stderr)
                self.assertIn("commit message", proc.stderr)
                self.assertNotIn(AWS, proc.stdout + proc.stderr)

    def test_binary_to_git_is_still_scanned(self) -> None:
        """S1: `-diff`/`binary` attributes or a NUL byte must not hide added lines from the scan;
        S12: neither may a type change (symlink -> file)."""
        write(self.r.root, ".gitattributes", "creds.txt -diff\n*.cfg binary\n")
        self.assertEqual(self.r.commit("chore: attributes", ".gitattributes").returncode, 0)
        cases = {"creds.txt": f"key = {AWS}\n", "app.cfg": f"key = {AWS}\n", "nul.txt": f"x\0y\nkey = {AWS}\n"}
        for name, text in cases.items():
            with self.subTest(name=name):
                (self.r.root / name).write_bytes(text.encode())
                proc = self.r.commit("chore: data", name)
                self.assertNotEqual(proc.returncode, 0, proc.stderr)
                self.assertIn(f"aws-access-key-id at {name}:", proc.stderr)
                self.r.git("rm", "-q", "--cached", name)
                (self.r.root / name).unlink()
        os.symlink("README.md", self.r.root / "link.txt")
        self.assertEqual(self.r.commit("chore: link", "link.txt").returncode, 0)
        (self.r.root / "link.txt").unlink()
        write(self.r.root, "link.txt", f"key = {AWS}\n")
        proc = self.r.commit("chore: link to file", "link.txt")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("aws-access-key-id at link.txt:1", proc.stderr)
        # The CI check sees the committed content the same way.
        self.r.git("commit", "-q", "--no-verify", "-m", "chore: bypass for the CI check")
        write(self.r.root, "nul2.txt", "")
        (self.r.root / "nul2.txt").write_bytes(f"a\0b\n{AWS}\n".encode())
        self.r.git("add", "nul2.txt")
        self.r.git("commit", "-q", "--no-verify", "-m", "chore: bypass 2")
        proc = self.r.sdd("check", "secrets", env={"SDD_BASE_REF": "HEAD~2"})
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("link.txt:1", proc.stdout)
        self.assertIn("nul2.txt:2", proc.stdout)

    def test_branch_cannot_switch_off_gitleaks(self) -> None:
        """S7: a .gitleaks.toml allowlist in the tree is ignored; a changed .gitleaksignore fails."""
        if not shutil.which("gitleaks"):
            self.skipTest("gitleaks not installed")
        self.r.cfg["quality"]["secrets"]["gitleaks"] = True
        self.r.set_cfg()
        self.assertEqual(self.r.commit("chore: gitleaks on", "sdd.config.json").returncode, 0)
        self.r.git("switch", "-q", "-c", "feat/F001-x")
        generic = 'api_key = "' + 'Zx8Qw3Lm9Rt2' + 'Vb7Nc4Kp6Hj1Gf5Ds0Ay"\n'  # only gitleaks' generic rule finds it
        write(self.r.root, "cfg.ini", generic)
        proc = self.r.commit("chore: cfg", "cfg.ini")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("gitleaks:generic-api-key", proc.stderr)
        # An inline gitleaks:allow comment does not exempt the line (F058 review: a change could exempt its own secret).
        write(self.r.root, "cfg.ini", generic.rstrip("\n") + "  # gitleaks:allow\n")
        proc = self.r.commit("chore: cfg", "cfg.ini")
        self.assertNotEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("gitleaks:generic-api-key", proc.stderr)
        write(self.r.root, "cfg.ini", generic)
        write(self.r.root, ".gitleaks.toml", "[extend]\nuseDefault = true\n[allowlist]\npaths = ['''.*''']\n")
        proc = self.r.commit("chore: cfg", "cfg.ini", ".gitleaks.toml")
        self.assertNotEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("gitleaks:generic-api-key", proc.stderr)
        write(self.r.root, ".gitleaksignore", "cfg.ini:generic-api-key:1\n")
        proc = self.r.commit("chore: cfg", "cfg.ini", ".gitleaks.toml", ".gitleaksignore")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn(".gitleaksignore differs", proc.stderr)
        # The CI check: both files committed on the branch without the hooks.
        self.r.git("commit", "-q", "--no-verify", "-m", "chore: cfg")
        proc = self.r.sdd("check", "secrets", env={"CI": "true", "SDD_BASE_REF": "main"})
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("secret-config", proc.stdout)
        (self.r.root / ".gitleaksignore").unlink()
        self.r.git("commit", "-q", "--no-verify", "-am", "chore: drop ignore")
        proc = self.r.sdd("check", "secrets", env={"CI": "true", "SDD_BASE_REF": "main"})
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("cfg.ini:1: gitleaks rule generic-api-key", proc.stdout)

    def test_gitleaks_scans_binary_to_git_paths(self) -> None:
        """S1: gitleaks git skips paths git treats as binary; their blobs are scanned with gitleaks dir."""
        if not shutil.which("gitleaks"):
            self.skipTest("gitleaks not installed")
        self.r.cfg["quality"]["secrets"]["gitleaks"] = True
        self.r.set_cfg()
        self.assertEqual(self.r.commit("chore: gitleaks on", "sdd.config.json").returncode, 0)
        self.r.git("switch", "-q", "-c", "feat/F001-x")
        generic = 'api_key = "' + 'u8jzPde0IgxL' + 'd6GncfBAepfJBd0Kh8oO"\n'  # only gitleaks' generic rule finds it
        write(self.r.root, ".gitattributes", "*.cfg binary\nvectors/** binary\n")
        write(self.r.root, "app.cfg", "x = 1\n" + generic)
        proc = self.r.commit("chore: app config", ".gitattributes", "app.cfg")
        self.assertNotEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("gitleaks:generic-api-key at app.cfg:2", proc.stderr)
        self.assertNotIn("u8jzPde0", proc.stdout + proc.stderr)
        write(self.r.root, "vectors/v.cfg", generic)  # allow_globs
        self.r.git("rm", "-q", "--cached", "app.cfg")
        self.assertEqual(self.r.commit("test: vector", ".gitattributes", "vectors/v.cfg").returncode, 0)
        # The CI check: added on the branch without the hooks, then removed again.
        self.r.git("add", "app.cfg")
        self.r.git("commit", "-q", "--no-verify", "-m", "chore: app config")
        self.r.git("rm", "-q", "app.cfg")
        self.r.git("commit", "-q", "--no-verify", "-m", "chore: drop app config")
        proc = self.r.sdd("check", "secrets", "--json", env={"CI": "true", "SDD_BASE_REF": "main"})
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertEqual([(v["kind"], v["file"], v["line"], v["message"]) for v in json.loads(proc.stdout)["violations"]],
                         [("secret", "app.cfg", 2, "gitleaks rule generic-api-key")])
        # A binary path gitleaks cannot scan fails closed.
        from sddkit.quality import secrets
        found = secrets.gitleaks_blobs(self.r.root, self.r.cfg, [("gone.cfg", "0" * 40), ("vectors/gone.cfg", "0" * 40)], "HEAD")
        self.assertEqual([(v.kind, v.file) for v in found], [("secret-unscanned", "gone.cfg")])

    GENERIC = 'api_key = "' + 'u8jzPde0IgxL' + 'd6GncfBAepfJBd0Kh8oO"\n'  # only gitleaks' generic rule finds it

    def _gitleaks_branch(self, attributes: str = "*.dat binary\n") -> None:
        if not shutil.which("gitleaks"):
            self.skipTest("gitleaks not installed")
        self.r.cfg["quality"]["secrets"]["gitleaks"] = True
        self.r.set_cfg()
        write(self.r.root, ".gitattributes", attributes)
        self.assertEqual(self.r.commit("chore: gitleaks on", "sdd.config.json", ".gitattributes").returncode, 0)
        self.r.git("switch", "-q", "-c", "feat/F001-x")

    def _ci_secrets(self) -> list:
        proc = self.r.sdd("check", "secrets", "--json", env={"CI": "true", "SDD_BASE_REF": "main"})
        return [(v["kind"], v["file"], v["line"]) for v in json.loads(proc.stdout)["violations"]]

    def test_allowed_copy_does_not_hide_a_blob(self) -> None:
        """A blob first added at an allow_globs path is still scanned where it is copied to."""
        self._gitleaks_branch()
        write(self.r.root, "vectors/k.dat", self.GENERIC)
        self.r.git("add", "vectors/k.dat")
        self.r.git("commit", "-q", "--no-verify", "-m", "test: vector")
        write(self.r.root, "src/k.dat", self.GENERIC)
        self.r.git("add", "src/k.dat")
        self.r.git("commit", "-q", "--no-verify", "-m", "chore: copy")
        self.assertEqual(self._ci_secrets(), [("secret", "src/k.dat", 1)])

    def test_staged_blob_of_a_path_that_looks_like_a_stage_spec(self) -> None:
        """`:0:x.dat` would name stage 0 of x.dat: the staged blob of `0:x.dat` is read by its id."""
        self._gitleaks_branch()
        write(self.r.root, "x.dat", "benign\n")
        self.assertEqual(self.r.commit("chore: x", "x.dat").returncode, 0)
        write(self.r.root, "0:x.dat", self.GENERIC)
        proc = self.r.commit("chore: y", "0:x.dat")
        self.assertNotEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("gitleaks:generic-api-key at 0:x.dat:1", proc.stderr)
        self.r.git("commit", "-q", "--no-verify", "-m", "chore: y")
        self.assertEqual(self._ci_secrets(), [("secret", "0:x.dat", 1)])

    def test_blobs_that_look_like_application_files(self) -> None:
        """gitleaks skips files whose first bytes look like an application type (ELF, PDF, zip,
        sqlite); the scratch copies start with a newline, so these are scanned too."""
        import io
        import sqlite3
        import zipfile
        self._gitleaks_branch()
        secret = self.GENERIC.encode()
        (self.r.root / "elf.dat").write_bytes(b"\x7fELF\x02\x01\x01\0\0\0\0\0\0\0\0\0\n" + secret)
        (self.r.root / "doc.dat").write_bytes(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\0\n" + secret)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as z:
            z.writestr("conf.ini", secret)
        (self.r.root / "pack.dat").write_bytes(buf.getvalue())
        db = sqlite3.connect(str(self.r.root / "db.dat"))
        db.execute("create table t (v text)")
        db.execute("insert into t values (?)", (self.GENERIC,))
        db.commit()
        db.close()
        names = ["db.dat", "doc.dat", "elf.dat", "pack.dat"]
        self.r.git("add", *names)
        proc = self.r.commit("chore: binaries")
        self.assertNotEqual(proc.returncode, 0, proc.stderr)
        for name in names:
            self.assertIn(f"gitleaks:generic-api-key at {name}", proc.stderr)
        self.assertIn("at elf.dat:2 ", proc.stderr)
        self.r.git("commit", "-q", "--no-verify", "-m", "chore: binaries")
        self.assertEqual(sorted(f for _, f, _ in self._ci_secrets()), names)

    def test_path_allowlist_cannot_match_the_scratch_dir(self) -> None:
        """A trusted .gitleaks.toml allowlisting `logs/` must not hide blobs copied under <logs_dir>."""
        write(self.r.root, ".gitleaks.toml", "[extend]\nuseDefault = true\n[allowlist]\npaths = ['''(^|/)logs/''']\n")
        self.r.git("add", ".gitleaks.toml")
        self._gitleaks_branch()
        (self.r.root / "blob.dat").write_bytes(b"\0\n" + self.GENERIC.encode())
        proc = self.r.commit("chore: blob", "blob.dat")
        self.assertNotEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("gitleaks:generic-api-key at blob.dat:2", proc.stderr)
        self.r.git("commit", "-q", "--no-verify", "-m", "chore: blob")
        self.assertEqual(self._ci_secrets(), [("secret", "blob.dat", 2)])

    def test_secret_added_only_in_a_merge_commit(self) -> None:
        """`gitleaks git` gets no diff for a merge unless git log is told to diff against its first parent."""
        self._gitleaks_branch()
        write(self.r.root, "b.md", "b\n")
        self.r.git("add", "b.md")
        self.r.git("commit", "-q", "--no-verify", "-m", "docs: b")
        self.r.git("switch", "-q", "-c", "side", "main")
        write(self.r.root, "a.md", "a\n")
        self.r.git("add", "a.md")
        self.r.git("commit", "-q", "--no-verify", "-m", "docs: a")
        self.r.git("switch", "-q", "feat/F001-x")
        self.r.git("merge", "-q", "--no-ff", "--no-commit", "side")
        write(self.r.root, "cfg.txt", self.GENERIC)
        self.r.git("add", "cfg.txt")
        self.r.git("commit", "-q", "--no-verify", "-m", "Merge branch side")
        self.assertEqual(self._ci_secrets(), [("secret", "cfg.txt", 1)])

    def test_many_binary_blobs_take_one_gitleaks_run(self) -> None:
        """Perf: 50 binary blobs are scanned by one `gitleaks dir`, in the hook and in the check;
        a failing gitleaks leaves every one of them `secret-unscanned`."""
        self._gitleaks_branch()
        wrap = self.r.root / ".sdd" / "fakebin"
        wrap.mkdir(parents=True)
        log = wrap / "calls.log"
        (wrap / "gitleaks").write_text(f'#!/bin/sh\necho "$1" >> "{log}"\n'
                                       'if [ "$1" = dir ] && [ -n "$FAIL_DIR" ]; then exit 2; fi\n'
                                       f'exec "{shutil.which("gitleaks")}" "$@"\n')
        (wrap / "gitleaks").chmod(0o755)
        env = {"PATH": f"{wrap}{os.pathsep}{self.r.env['PATH']}"}
        for i in range(50):
            (self.r.root / "data").mkdir(exist_ok=True)
            (self.r.root / "data" / f"f{i:02d}.dat").write_bytes(b"\0\1" + os.urandom(64) + (self.GENERIC.encode() if i == 37 else b""))
        self.r.git("add", "data")
        proc = self.r.commit("chore: data", env=env)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("gitleaks:generic-api-key at data/f37.dat:", proc.stderr)
        self.assertEqual(log.read_text().split().count("dir"), 1)
        proc = self.r.commit("chore: data", env=dict(env, FAIL_DIR="1"))
        self.assertEqual(proc.stderr.count("gitleaks could not scan it"), 50, proc.stderr)
        self.r.git("commit", "-q", "--no-verify", "-m", "chore: data")
        log.unlink()
        proc = self.r.sdd("check", "secrets", "--json", env=dict(env, CI="true", SDD_BASE_REF="main"))
        self.assertEqual([(v["kind"], v["file"]) for v in json.loads(proc.stdout)["violations"]], [("secret", "data/f37.dat")])
        self.assertEqual(log.read_text().split(), ["git", "dir"])

    def test_gitleaks_missing_skip_and_ci(self) -> None:
        path = no_gitleaks_path()
        if not shutil.which("git", path=path) or not shutil.which("python3", path=path):
            self.skipTest("gitleaks shares a directory with git/python3")
        self.r.cfg["quality"]["secrets"]["gitleaks"] = True
        self.r.set_cfg()
        self.r.git("add", "sdd.config.json")
        proc = self.r.git("commit", "-m", "chore: gitleaks on", env={"PATH": path}, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("SKIP gitleaks not installed", proc.stderr)
        write(self.r.root, "a.md", "a\n")
        self.r.git("add", "a.md")
        proc = self.r.git("commit", "-m", "docs: a", env={"PATH": path, "CI": "true"}, check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("gitleaks not installed", proc.stderr)


class PreCommitStepsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.r = HookRepo(base_cfg(hooks={"pre_commit": [
            {"name": "py-step", "command": "echo PY-STEP {files}", "glob": "*.py"},
            {"name": "ts-step", "command": "echo TS-STEP {files}", "glob": "*.ts"},
            {"name": "fail-step", "command": "echo FAIL-STEP; exit 1", "glob": "*.fail"},
            {"name": "slow-step", "command": "sleep 5", "glob": "*.slow", "timeout": 1},
            {"name": "budget-step", "command": "sleep 1.2", "glob": "*.budget"},
            {"name": "txt-step", "command": "echo TXT-STEP {files}", "glob": "*.txt", "exclude": ["vendor/**"]},
        ], "budget_seconds": 2}))
        self.r.cfg["tests"]["run"] = "sleep 1.2"
        self.r.set_cfg()
        self.r.git("commit", "-q", "-am", "chore: steps")
        self.r.install()

    def tearDown(self) -> None:
        self.r.close()

    def test_steps(self) -> None:
        write(self.r.root, "docs/a b.py", "x = 1\n")
        write(self.r.root, "docs/c.md", "x\n")
        proc = self.r.commit("docs: a", "docs/a b.py", "docs/c.md")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("PY-STEP 'docs/a b.py'", proc.stderr)
        # S8: a staged name that looks like an option reaches the tool as a path.
        write(self.r.root, "-e.py", "x = 1\n")
        proc = self.r.commit("docs: dash", "--", "-e.py")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("PY-STEP ./-e.py", proc.stderr)
        self.assertNotIn("TS-STEP", proc.stderr)
        # C2: the installed kit and a step's `exclude` globs never reach {files}.
        write(self.r.root, "tools/sdd/sddkit/extra.py", "x = 1\n")
        write(self.r.root, "vendor/v.txt", "v\n")
        proc = self.r.commit("chore: kit and vendor", "tools/sdd/sddkit/extra.py", "vendor/v.txt")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("PY-STEP", proc.stderr)
        self.assertNotIn("TXT-STEP", proc.stderr)
        self.assertIn("test step skipped: no source or test files staged", proc.stderr)
        write(self.r.root, "notes/a.txt", "a\n")
        write(self.r.root, "vendor/w.txt", "w\n")
        proc = self.r.commit("docs: notes", "notes/a.txt", "vendor/w.txt")
        self.assertIn("TXT-STEP notes/a.txt\n", proc.stderr + "\n")
        write(self.r.root, "x.fail", "x\n")
        proc = self.r.commit("docs: f", "x.fail")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("fail-step: failed", proc.stderr)
        self.r.git("rm", "-q", "--cached", "x.fail")
        write(self.r.root, "x.slow", "x\n")
        proc = self.r.commit("docs: s", "x.slow")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("slow-step: timed out", proc.stderr)
        self.r.git("rm", "-q", "--cached", "x.slow")

    def test_go_example_step_with_option_like_name(self) -> None:
        """S8: the Go example's gofmt step fails on gofmt errors and on unformatted files even
        next to a staged `-e.go`."""
        if not shutil.which("gofmt"):
            self.skipTest("gofmt not installed")
        go = json.loads((KIT / "examples" / "configs" / "go.json").read_text())
        r = HookRepo(base_cfg(hooks={"pre_commit": go["quality"]["hooks"]["pre_commit"]}), prefix="gofmt-")
        try:
            r.install()
            write(r.root, "bad.go", "package x\nfunc  F( ) {}\n")
            write(r.root, "-e.go", "")
            proc = r.commit("feat: go", "--", "bad.go", "-e.go")
            self.assertNotEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("gofmt: failed", proc.stderr)
        finally:
            r.close()

    def test_budget_warns_once_and_passes(self) -> None:
        write(self.r.root, "src/x.budget", "x\n")
        write(self.r.root, "src/y.py", "y = 1\n")
        proc = self.r.commit("feat: budget", "src/x.budget", "src/y.py")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr.count("WARNING: hooks took"), 1)
        self.assertRegex(proc.stderr, r"slowest step: (pre-commit: budget-step|commit-msg: tests)")

    def test_hooks_leave_no_trace(self) -> None:
        write(self.r.root, "src/p.py", "a = 1\nb = 2\n")
        self.r.commit("feat: p", "src/p.py")
        write(self.r.root, "src/p.py", "a = 1\nb = 2\nc = 3\n")
        self.r.git("add", "src/p.py")
        write(self.r.root, "src/p.py", "a = 1\nb = 2\nc = 3\nd = 4\n")  # partially staged
        write(self.r.root, "x.fail", "x\n")
        self.r.git("add", "x.fail")
        before = self.r.state()
        hooks = self.r.root / "tools" / "sdd" / "githooks"
        self.r.run(str(hooks / "pre-commit"))
        msg = self.r.root / ".git" / "COMMIT_EDITMSG"
        msg.write_text("feat: p\n")
        self.r.run(str(hooks / "commit-msg"), str(msg))
        self.assertEqual(self.r.state(), before)


class TestStepAndPrePushTest(unittest.TestCase):
    def setUp(self) -> None:
        cfg = base_cfg()
        cfg["components"]["path_regex"] = r"^crates/([^/]+)/"
        cfg["tests"]["run_component"] = "echo RUN-COMP {component}"
        cfg["gates"]["fast"] = [{"name": "ok", "command": "echo FAST-GATES-RAN"}]
        self.r = HookRepo(cfg)
        self.r.install()

    def tearDown(self) -> None:
        self.r.close()

    def test_components_and_root(self) -> None:
        write(self.r.root, "crates/a/src/lib.rs", "pub fn f() {}\n")
        write(self.r.root, "crates/b/tests/t.rs", "#[test]\nfn t() {}\n")
        proc = self.r.commit("feat(F011): a", "crates/a/src/lib.rs", "crates/b/tests/t.rs")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("RUN-COMP a", proc.stderr + proc.stdout)
        self.assertIn("$ echo RUN-COMP b", proc.stderr)
        self.assertNotIn("RUN-ROOT", proc.stderr)
        write(self.r.root, "src/main.py", "x = 1\n")
        proc = self.r.commit("feat(F011): root", "src/main.py")
        self.assertIn("$ echo RUN-ROOT", proc.stderr)

    def test_failure_timeout_and_skips(self) -> None:
        self.r.cfg["tests"]["run_component"] = "exit 1"
        self.r.set_cfg()
        self.r.commit("chore: cfg", "sdd.config.json")
        write(self.r.root, "crates/a/src/lib.rs", "pub fn f() {}\n")
        proc = self.r.commit("feat(F011): a", "crates/a/src/lib.rs")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("tests: failed", proc.stderr)
        # test(...) commits are red by design: no test command runs.
        write(self.r.root, "crates/a/tests/red.rs", "#[test]\nfn red() { panic!() }\n")
        proc = self.r.commit("test(F011): red", "crates/a/tests/red.rs")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("test step skipped: commit type 'test'", proc.stderr)
        self.assertNotIn("$ exit 1", proc.stderr)
        # Docs-only commits run no tests.
        write(self.r.root, "docs/x.md", "x\n")
        proc = self.r.commit("docs(F011): spec", "docs/x.md")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("test step skipped: no source or test files staged", proc.stderr)
        # Over test_timeout.
        write(self.r.root, "crates/a/src/lib.rs", "pub fn g() {}\n")
        self.r.cfg["tests"]["run_component"] = "sleep 5"
        self.r.cfg["quality"]["hooks"]["test_timeout"] = 1
        self.r.set_cfg()
        self.r.git("add", "sdd.config.json", "crates/a/src/lib.rs")
        proc = self.r.git("commit", "-m", "feat(F011): slow", check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("timed out", proc.stderr)

    def test_red_tests_then_task_by_task(self) -> None:
        """C1: after a red-by-design test(...) commit, a task commit is not rejected for the tests
        of tasks not done yet; once the command passed on the branch, a failure is a regression."""
        self.r.cfg["tests"]["run"] = "grep -q T01 src/ops.py && grep -q T02 src/ops.py"
        self.r.set_cfg()
        self.r.commit("chore: tests", "sdd.config.json")
        # Without a red test commit on the branch every failure rejects (unchanged behaviour).
        self.r.git("switch", "-q", "-c", "feat/F002-other")
        write(self.r.root, "src/ops.py", "# T01\n")
        proc = self.r.commit("feat(F002): T01 only", "src/ops.py")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("tests: failed", proc.stderr)
        self.assertNotIn("red-by-design", proc.stderr)
        self.r.git("rm", "-q", "--cached", "src/ops.py")
        self.r.git("switch", "-q", "main")
        self.r.git("switch", "-q", "-c", "feat/F001-ops")
        write(self.r.root, "tests/test_ops.py", "def test_t01(): ...\ndef test_t02(): ...\n")
        proc = self.r.commit("test(F001): failing tests for T01 and T02", "tests/test_ops.py")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        write(self.r.root, "src/ops.py", "# T01\n")
        proc = self.r.commit("feat(F001): T01 mul", "src/ops.py")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("red-by-design tests", proc.stderr)
        write(self.r.root, "src/ops.py", "# T01\n# T02\n")
        proc = self.r.commit("feat(F001): T02 div", "src/ops.py")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("red-by-design", proc.stderr)
        write(self.r.root, "src/ops.py", "# T01\n")
        proc = self.r.commit("refactor(F001): drop div", "src/ops.py")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("a regression", proc.stderr)

    def test_commands_do_not_inherit_git_hook_env(self) -> None:
        """S6: a test that runs git in a nested repository never writes the committing repo's index
        (`commit -a` exports GIT_INDEX_FILE=<repo>/.git/index.lock to the hooks)."""
        nested = ("rm -rf .sdd/nested && mkdir -p .sdd/nested && cd .sdd/nested && git init -q && echo n > NESTED.txt && "
                  "git add NESTED.txt && git -c user.name=t -c user.email=t@localhost commit -qm n && "
                  "test -n \"$SDD_GIT_INDEX_FILE\"")
        self.r.cfg["tests"]["run"] = nested
        self.r.cfg["quality"]["hooks"]["pre_commit"] = [{"name": "nested-step", "command": nested, "glob": "*.py"}]
        self.r.set_cfg()
        self.r.commit("chore: nested tests", "sdd.config.json")
        write(self.r.root, "src/m.py", "m = 1\n")
        self.r.commit("feat: m", "src/m.py")
        for extra in (["-a"], ["src/m.py"]):
            with self.subTest(extra=extra):
                write(self.r.root, "src/m.py", f"m = {len(extra[0])}\n")
                proc = self.r.git("commit", "-m", "feat: change m", *extra, check=False)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(self.r.git("show", "--name-only", "--format=", "HEAD").stdout.split(), ["src/m.py"])
                self.assertNotIn("NESTED.txt", self.r.git("ls-files").stdout)
                self.assertEqual(self.r.git("status", "--porcelain", "--untracked-files=no").stdout, "")

    def test_pre_push(self) -> None:
        remote = make_tmp("remote-")
        try:
            self.r.run("git", "init", "-q", "--bare", str(remote), check=True)
            self.r.git("remote", "add", "origin", str(remote))
            proc = self.r.git("push", "-q", "origin", "main", check=False)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("FAST-GATES-RAN", proc.stdout + proc.stderr)
            self.r.cfg["gates"]["fast"] = [{"name": "bad", "command": "echo BROKEN; exit 1"}]
            self.r.set_cfg()
            self.r.commit("chore: break gates", "sdd.config.json")
            proc = self.r.git("push", "-q", "origin", "main", check=False)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("push blocked", proc.stderr)
        finally:
            rm(remote)


if __name__ == "__main__":
    unittest.main(verbosity=2)
