"""WI3 AC 3 and 10 (templates): task ratings documented, the config template's model example."""

from __future__ import annotations

import json
import unittest

from helpers import KIT

from sddkit import config as cfgmod


class RoutingTemplatesTest(unittest.TestCase):
    def test_tasks_template_and_planner_document_ratings(self) -> None:
        tpl = (KIT / "templates" / "specs" / "templates" / "tasks.md").read_text()
        self.assertIn("complexity: low|medium|high", tpl)
        self.assertIn("risk: security|concurrency|none", tpl)
        planner = (KIT / "claude" / "agents" / "architect-planner.md").read_text()
        for word in ("complexity:", "risk:", "low", "high", "concurrency"):
            self.assertIn(word, planner)

    def test_config_template_models_example(self) -> None:
        tpl = json.loads((KIT / "templates" / "sdd.config.json").read_text())
        example = tpl["_models_example"]
        self.assertEqual(set(k for k in example if not k.startswith("_")), {"mechanical", "gate", "mid", "strong", "top"})
        self.assertEqual(example["gate"]["effort"], "medium")
        self.assertEqual(example["top"]["effort"], "high")
        self.assertNotIn(example["strong"]["effort"], ("xhigh", "max"))
        self.assertEqual(tpl["pipeline"]["routing"], "tiers")
        self.assertIn("gate", tpl["models"])
        cfg = cfgmod._merge(cfgmod.DEFAULTS, tpl)
        self.assertFalse([p for p in cfgmod.validate(cfg) if "models" in p or "routing" in p])


if __name__ == "__main__":
    unittest.main()
