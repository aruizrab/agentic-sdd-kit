"""WI3 AC 10 (docs): the README "Model routing" section carries the default efficient mapping."""

from __future__ import annotations

import unittest

from helpers import KIT


class ReadmeRoutingTest(unittest.TestCase):
    def test_readme_section(self) -> None:
        readme = (KIT / "README.md").read_text()
        self.assertIn("## Model routing", readme)
        section = readme.split("## Model routing", 1)[1].split("\n## ", 1)[0]
        for text in ("mechanical", "gate", "mid", "strong", "top", "xhigh", '"routing": "formula"', "sdd routing refit"):
            self.assertIn(text, section)


if __name__ == "__main__":
    unittest.main()
