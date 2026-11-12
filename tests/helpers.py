"""Shared test helpers: a temp project under .scratch/ (never /tmp) and a config."""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT / "tools" / "sdd"))

from sddkit import config as cfgmod  # noqa: E402

SCRATCH = KIT / ".scratch" / "tests"
SCRATCH.mkdir(parents=True, exist_ok=True)

GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "sdd-test",
    "GIT_AUTHOR_EMAIL": "sdd-test@localhost",
    "GIT_COMMITTER_NAME": "sdd-test",
    "GIT_COMMITTER_EMAIL": "sdd-test@localhost",
}


def make_tmp(prefix: str = "t-") -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix, dir=str(SCRATCH)))


def rm(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def test_config(**overrides) -> dict:
    cfg = copy.deepcopy(cfgmod.DEFAULTS)
    cfg["requirements"]["id_regex"] = r"(?:TS\d{5}-[0-9A-Z.]+|FC-[A-Z0-9]+)-R\d{2,3}"
    cfg["trace"]["scan_globs"] = ["crates/**/*.rs", "src/**/*.py", "src/**/*.ts", "pkg/**/*.go"]
    cfg["components"]["path_regex"] = r"^crates/([^/]+)/"
    cfg["gates"]["fast"] = [{"name": "echo", "command": "echo fast-ok", "timeout": 10}]
    cfg["gates"]["full"] = [
        {"name": "echo", "command": "echo full-ok", "timeout": 10},
        {"name": "sleepy", "command": "sleep 5", "timeout": 1},
        {"name": "missing", "command": "definitely-not-a-program-xyz", "timeout": 10, "optional": True},
    ]
    cfg["commit"]["trailers"] = ["Co-Authored-By: Test <test@localhost>"]
    cfg["merge"]["union_manifests"] = ["Cargo.toml", "crates/*/Cargo.toml", "package.json"]
    for k, v in overrides.items():
        if isinstance(v, dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    return cfg


def write_config(root: Path, cfg: dict) -> None:
    (root / "sdd.config.json").write_text(json.dumps({k: v for k, v in cfg.items() if not k.startswith("_")}, indent=2), encoding="utf-8")


def write(root: Path, rel: str, text: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


class GitRepo:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.tick = 0
        self.env = dict(os.environ)
        self.env.update(GIT_ENV)

    def git(self, *args: str, cwd: Path = None) -> str:
        self.tick += 1
        env = dict(self.env)
        stamp = f"2026-01-01T00:{self.tick // 60:02d}:{self.tick % 60:02d}Z"
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = stamp
        proc = subprocess.run(
            ["git", "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main", *args],
            cwd=str(cwd or self.root), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        if proc.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} failed: {proc.stderr}")
        return proc.stdout

    def init(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.git("init", "-q", "-b", "main")

    def commit(self, message: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD").strip()


def run_cli(root: Path, *args: str, env: dict = None) -> subprocess.CompletedProcess:
    e = dict(os.environ)
    e.update(env or {})
    e["SDD_ROOT"] = str(root)
    return subprocess.run(
        [sys.executable, "-I", str(KIT / "tools" / "sdd" / "sdd.py"), *args],
        cwd=str(root), env=e, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
