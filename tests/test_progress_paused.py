"""`sdd progress`: the paused marker (paths.paused), read from main only and validated strictly."""

from __future__ import annotations

import datetime as dt
import json
import unittest

from helpers import GitRepo, make_tmp, rm, test_config, write, write_config
from sddkit import config as cfgmod, progress

REASON = "Implementation waits for F002 to merge (shared components)."
IDS = {"F001", "F002"}


def paused_cfg() -> dict:
    return test_config()


class ParsePausedTest(unittest.TestCase):
    def test_absent_and_empty(self) -> None:
        self.assertEqual(progress.parse_paused(None, IDS), {})
        self.assertEqual(progress.parse_paused("{}", IDS), {})

    def test_valid(self) -> None:
        text = json.dumps({"F002": "b (shared).", "F001": "x" * progress.PAUSED_REASON_MAX})
        self.assertEqual(progress.parse_paused(text, IDS), {"F001": "x" * 160, "F002": "b (shared)."})

    def test_rejects(self) -> None:
        bad = [
            ("not valid JSON", "{"),
            ("must be a JSON object", '["F001"]'),
            ("duplicate key", '{"F001": "a", "F001": "b"}'),
            ("not a feature in the roadmap", '{"f001": "a"}'),
            ("not a feature in the roadmap", '{"F009": "a"}'),
            ("must be a string", '{"F001": 1}'),
            ("must not be empty", '{"F001": "   "}'),
            ("at most 160", json.dumps({"F001": "x" * 161})),
            ("control characters", '{"F001": "a\\nb"}'),
            ("control characters", '{"F001": "a\\u2028b"}'),
            ("e-mail", '{"F001": "ask someone@example.com"}'),
            ("URL", '{"F001": "see ' + "https:" + '//host.test/x"}'),
            ("URL", '{"F001": "see www.' + 'host.test"}'),
            ("filesystem path", '{"F001": "see /home/someone/notes"}'),
            ("filesystem path", '{"F001": "see C:\\\\Users\\\\x"}'),
        ]
        for needle, text in bad:
            with self.subTest(text=text):
                with self.assertRaises(ValueError) as ctx:
                    progress.parse_paused(text, IDS, "p/paused.json")
                self.assertIn("p/paused.json", str(ctx.exception))
                self.assertIn(needle, str(ctx.exception))

    def test_a_relative_mention_is_allowed(self) -> None:
        self.assertEqual(progress.parse_paused('{"F001": "waits for docs/x and a/b"}', IDS), {"F001": "waits for docs/x and a/b"})


class PausedReportTest(unittest.TestCase):
    NOW = dt.datetime(2026, 11, 17, tzinfo=dt.timezone.utc)

    def setUp(self) -> None:
        self.base = make_tmp("progress-paused-")

    def tearDown(self) -> None:
        rm(self.base)

    def build(self, on_main, on_branch=None):
        src = self.base / f"src{len(list(self.base.iterdir()))}"
        r = GitRepo(src)
        r.init()
        write(src, "docs/ROADMAP.md", "### M0 — A\n\n#### F001 one\n#### F002 two\n")
        if on_main is not None:
            write(src, "tools/progress/paused.json", on_main)
        r.commit("chore: roadmap")
        r.git("checkout", "-q", "-b", "feat/F001-one", "main")
        write(src, "specs/features/001-one/spec.md", "# F001 — One\n\nStatus: Approved\n")
        if on_branch is not None:
            write(src, "tools/progress/paused.json", on_branch)
        r.commit("docs(F001): approve spec")
        r.git("checkout", "-q", "main")
        cfg = paused_cfg()
        write_config(src, cfg)
        return src, cfg

    def rows(self, src, cfg):
        return {f["id"]: f for f in progress.build_report(src, cfg, remote="", now=self.NOW)["features"]}

    def test_paused_feature_carries_reason_and_nothing_else_changes(self) -> None:
        src, cfg = self.build(json.dumps({"F001": REASON}))
        rows = self.rows(src, cfg)
        self.assertIs(rows["F001"]["paused"], True)
        self.assertEqual(rows["F001"]["pausedReason"], REASON)
        self.assertNotIn("paused", rows["F002"])
        src2, cfg2 = self.build(None)
        plain = self.rows(src2, cfg2)["F001"]
        self.assertEqual({k: v for k, v in rows["F001"].items() if k not in ("paused", "pausedReason")}, plain)
        self.assertEqual((plain["stage"], plain["percent"]), ("spec-approved", 15.0))

    def test_paused_feature_without_a_branch(self) -> None:
        src, cfg = self.build(json.dumps({"F002": "Waits for review."}))
        self.assertEqual(self.rows(src, cfg)["F002"]["pausedReason"], "Waits for review.")

    def test_file_only_on_a_branch_or_in_the_working_tree_has_no_effect(self) -> None:
        src, cfg = self.build(None, on_branch=json.dumps({"F001": REASON}))
        write(src, "tools/progress/paused.json", json.dumps({"F001": REASON}))  # working tree of main
        self.assertFalse(any("paused" in f for f in self.rows(src, cfg).values()))

    def test_invalid_file_on_a_branch_is_never_read(self) -> None:
        src, cfg = self.build(json.dumps({"F001": REASON}), on_branch='{"F999": ')
        self.assertTrue(self.rows(src, cfg)["F001"]["paused"])

    def test_invalid_file_on_main_fails_the_run(self) -> None:
        src, cfg = self.build('{"F009": "x"}')
        with self.assertRaises(ValueError) as ctx:
            progress.build_report(src, cfg, remote="", now=self.NOW)
        self.assertIn("tools/progress/paused.json", str(ctx.exception))

    def test_configurable_path_and_disabled(self) -> None:
        src, cfg = self.build(json.dumps({"F001": REASON}))
        cfg["paths"]["paused"] = ""
        self.assertFalse(any("paused" in f for f in self.rows(src, cfg).values()))
        cfg["paths"]["paused"] = "elsewhere/p.json"
        self.assertFalse(any("paused" in f for f in self.rows(src, cfg).values()))

    def test_table_marks_paused(self) -> None:
        src, cfg = self.build(json.dumps({"F001": REASON}))
        self.assertIn(f"(paused: {REASON})", progress.render_table(progress.build_report(src, cfg, remote="", now=self.NOW)))

    def test_config_validation(self) -> None:
        cfg = paused_cfg()
        self.assertEqual(cfgmod.DEFAULTS["paths"]["paused"], "tools/progress/paused.json")
        for value, ok in (("", True), ("a/p.json", True), ("../p.json", False), ("/abs/p.json", False), (3, False)):
            cfg["paths"]["paused"] = value
            problems = [p for p in cfgmod.validate(cfg) if "paths.paused" in p]
            self.assertEqual(problems == [], ok, (value, problems))


if __name__ == "__main__":
    unittest.main()
