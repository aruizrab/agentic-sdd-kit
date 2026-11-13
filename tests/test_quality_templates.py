"""WI1 AC 12 and 14: template and example configs (plans, validation, captured tool output)."""

from __future__ import annotations

import json
import os
import shutil
import unittest
from pathlib import Path

from helpers import KIT, make_tmp, rm

from sddkit import config as cfgmod
from sddkit import quality
from sddkit.quality import checks, coverage, mutation

FIX = KIT / "tests" / "fixtures" / "quality" / "tools"
EXAMPLES = ["rust", "python", "typescript", "go"]


def load(path: Path) -> dict:
    return cfgmod._merge(cfgmod.DEFAULTS, json.loads(path.read_text(encoding="utf-8")))


class TemplatePlansTest(unittest.TestCase):
    def test_check_gates_in_plans(self) -> None:
        for path in [KIT / "templates" / "sdd.config.json"] + [KIT / "examples" / "configs" / f"{e}.json" for e in EXAMPLES]:
            cfg = json.loads(path.read_text())
            for plan in ("fast", "full"):
                names = {g["name"]: g for g in cfg["gates"][plan]}
                for check in ("size", "arch", "baseline", "commits", "secrets"):
                    self.assertIn(check, names, (path.name, plan, check))
                    self.assertEqual(names[check].get("skip_exit"), 3)
                    self.assertIn(f"sdd.py check {check}", names[check]["command"])
            full = {g["name"]: g for g in cfg["gates"]["full"]}
            self.assertTrue({"coverage", "mutation"} <= set(full), path.name)
            for c in cfg["quality"]["checks"]:
                self.assertIn(c["name"], full, (path.name, c["name"]))
            # skip_exit only on the kit's own check entries.
            for plan in ("fast", "full", "nightly"):
                for g in cfg["gates"][plan]:
                    if "skip_exit" in g:
                        self.assertIn("sdd.py check ", g["command"], (path.name, g["name"]))
        tmpl = json.loads((KIT / "templates" / "sdd.config.json").read_text())
        self.assertEqual(tmpl["gates"]["nightly"], [])
        self.assertEqual(tmpl["quality"]["coverage"]["command"], "")
        for e in EXAMPLES:
            self.assertTrue(json.loads((KIT / "examples" / "configs" / f"{e}.json").read_text())["gates"]["nightly"], e)

    def test_configs_validate(self) -> None:
        for path in [KIT / "templates" / "sdd.config.json"] + [KIT / "examples" / "configs" / f"{e}.json" for e in EXAMPLES]:
            problems = cfgmod.validate(load(path))
            # The attribution trailer is a deliberate loud placeholder until the project sets it.
            self.assertEqual([p for p in problems if "commit.trailers" not in p], [], path.name)


class CommitTestStepTest(unittest.TestCase):
    def test_python_src_layout_runs_the_whole_suite(self) -> None:
        """C11: a src-layout package with a flat tests/ dir must not map to a missing tests/<package>."""
        from types import SimpleNamespace

        from sddkit.quality import githooks

        cfg = load(KIT / "examples" / "configs" / "python.json")
        cmds = githooks._test_commands(SimpleNamespace(cfg=cfg), ["src/app/domain/ops.py"])
        self.assertEqual(cmds, [cfg["tests"]["run"]])


class CapturedToolOutputTest(unittest.TestCase):
    def test_every_example_check_parses_its_fixture(self) -> None:
        expected = json.loads((FIX / "expected.json").read_text())
        root = Path("/work/app")
        seen = set()
        for lang in EXAMPLES:
            cfg = load(KIT / "examples" / "configs" / f"{lang}.json")
            for chk in cfg["quality"]["checks"]:
                key = f"{lang}/{chk['name']}"
                seen.add(key)
                got = [[v.file, v.line, v.message] for v in checks.parse(chk, (FIX / lang / f"{chk['name']}.txt").read_text(), root)]
                self.assertEqual(got, expected[key], key)
            if cfg["quality"]["mutation"]["format"] == "regex":
                key = f"{lang}/mutation"
                seen.add(key)
                got = [[f, ln, m] for f, ln, m in mutation.parse_survivors((FIX / lang / "mutation.txt").read_text(), cfg, root)]
                self.assertEqual(got, expected[key], key)
        self.assertEqual(seen, set(expected))


# Programs a bare machine has; the example's project tools (linters, test runners, mutation tools) are left out.
BASE_PROGRAMS = ["sh", "bash", "env", "cat", "grep", "sed", "test", "[", "ls", "mkdir", "rm", "cp", "mv", "tr", "head", "tail",
                 "dirname", "basename", "uname", "python3", "git", "node", "npx", "npm"]


class MissingToolTest(unittest.TestCase):
    """C4: with its tool missing, every example quality command SKIPs (3) or errors (2), never passes."""

    def setUp(self) -> None:
        self.root = make_tmp("missing-tool-")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for prog in BASE_PROGRAMS:
            found = shutil.which(prog)
            if found:
                os.symlink(found, self.bin / prog)
        # pytest without the rerunfailures plugin: the plugin guard must turn that into a SKIP.
        pytest = shutil.which("pytest")
        if pytest and os.system(f"{shutil.which('python3')} -c 'import pytest_rerunfailures' 2>/dev/null") != 0:
            os.symlink(pytest, self.bin / "pytest")
        (self.root / "src").mkdir()
        self.saved = dict(os.environ)
        for k in ("CI", "SDD_ROOT"):
            os.environ.pop(k, None)
        os.environ.update({"PATH": str(self.bin), "npm_config_offline": "true", "npm_config_yes": "false", "HOME": str(self.root)})

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.saved)
        rm(self.root)

    def outcome(self, fn) -> str:
        try:
            return fn().status
        except quality.QualityError:
            return "error"

    def test_every_example_tool_command(self) -> None:
        for lang in EXAMPLES:
            cfg = load(KIT / "examples" / "configs" / f"{lang}.json")
            for chk in cfg["quality"]["checks"]:
                with self.subTest(lang=lang, check=chk["name"]):
                    self.assertIn(self.outcome(lambda: checks.execute(self.root, cfg, chk)), ("skip", "error"))
            for section in ("coverage", "mutation"):
                with self.subTest(lang=lang, check=section):
                    def run():
                        failed, _ = coverage.run_tool(self.root, cfg, section, cfg["quality"][section]["command"].replace("{diff_file}", "x.patch")
                                                      .replace("{changed_files}", "src/a"))
                        return failed or quality.Result(section, status="pass")
                    self.assertIn(self.outcome(run), ("skip", "error"))


    def test_tool_dirs_find_project_local_tools(self) -> None:
        cfg = load(KIT / "examples" / "configs" / "typescript.json")
        tool = self.root / "node_modules" / ".bin" / "eslint"
        tool.parent.mkdir(parents=True)
        tool.write_text("#!/bin/sh\necho 'src/a.ts:3:1: Missing JSDoc comment. [Warning/jsdoc/require-jsdoc]'\nexit 1\n")
        tool.chmod(0o755)
        chk = checks.find(cfg, "docs")
        res = checks.execute(self.root, cfg, chk)
        self.assertEqual([(v.file, v.line) for v in res.violations], [("src/a.ts", 3)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
