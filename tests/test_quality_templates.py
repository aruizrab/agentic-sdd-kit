"""WI1 AC 12 and 14: template and example configs (plans, validation, captured tool output)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from helpers import KIT

from sddkit import config as cfgmod
from sddkit.quality import checks, mutation

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
