"""WI3 model routing: tiers with effort, models table, candidates, telemetry log and refit (AC 4.9)."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import unittest
from pathlib import Path

from helpers import KIT, make_tmp, rm, run_cli, test_config, write, write_config

from sddkit import config as cfgmod
from sddkit import routing, specs

FIX = KIT / "tests" / "fixtures" / "routing"


def project(table: str = "", **overrides) -> Path:
    root = make_tmp("routing-")
    cfg = test_config(**overrides)
    write_config(root, cfg)
    if table:
        shutil.copy(FIX / table, root / "sdd.models.json")
    return root


def problems_of(models=None, pipeline=None, root: Path = None) -> list:
    cfg = test_config()
    if models:
        cfg["models"].update(models)
    if pipeline:
        cfg["pipeline"].update(pipeline)
    if root:
        cfg["_root"] = str(root)
    return cfgmod.validate(cfg)


class TierConfigTest(unittest.TestCase):
    """AC 1: tiers as strings or {id, effort}; doctor warnings."""

    def test_tier_forms(self) -> None:
        ok = problems_of({"mid": "vendor-mid-2", "strong": {"id": "vendor-top-5", "effort": "medium"}, "gate": {"id": "vendor-small-1", "effort": ""}})
        self.assertFalse([p for p in ok if p.startswith("models")], ok)
        bad = problems_of({"mid": "opus", "top": {"id": "vendor-top-5", "effort": "ultra"}, "strong": {"id": "vendor-top-5", "colour": "red"}})
        text = "\n".join(bad)
        self.assertIn("models.mid", text)
        self.assertIn("alias", text)
        self.assertIn("models.top: unknown effort 'ultra'", text)
        self.assertIn("models.strong: unknown key 'colour'", text)
        unknown = problems_of({"huge": "vendor-x-1"})
        self.assertIn("unknown key 'models.huge'", "\n".join(unknown))
        self.assertIn("models.mid", "\n".join(problems_of({"mid": {"effort": "low"}})))

    def test_routing_mode_values(self) -> None:
        self.assertIn("pipeline.routing", "\n".join(problems_of(pipeline={"routing": "magic"})))

    def test_doctor_warnings(self) -> None:
        root = project("two-pool.json")
        try:
            cfg = json.loads((root / "sdd.config.json").read_text())
            cfg["models"] = {"mechanical": "", "gate": "", "mid": "vendor-mid-2", "strong": {"id": "std-s", "effort": "medium"}, "top": ""}
            write_config(root, cfg)
            out = run_cli(root, "doctor").stdout
            empty = [l for l in out.splitlines() if "inherit the session model" in l]
            self.assertEqual(len(empty), 1, out)
            self.assertIn("mechanical", empty[0])
            self.assertIn("top", empty[0])
            self.assertNotIn("gate", empty[0])  # gate falls back to mid, which is set
            fallback = [l for l in out.splitlines() if "no cap fallback" in l]
            self.assertEqual(len(fallback), 1, out)
            self.assertIn("models.mid", fallback[0])
            self.assertTrue(all(l.startswith("[warn]") for l in empty + fallback))
        finally:
            rm(root)


class TaskRatingTest(unittest.TestCase):
    """AC 3: complexity and risk per task."""

    TEXT = (
        "## Wave 1\n"
        "- [ ] T01 Plain — files: src/a.py — verifies: FC-A-R01 — depends: -\n"
        "- [ ] T02 Rated — files: src/b.py — verifies: FC-A-R02 — depends: - — complexity: high — risk: security\n"
        "- [ ] T03 Odd — files: src/c.py — verifies: FC-A-R03 — depends: - — complexity: extreme — risk: weather\n"
    )

    def test_parse_and_validate(self) -> None:
        plan = specs.parse_tasks(self.TEXT, test_config())
        t1, t2, t3 = plan["waves"][0]["tasks"]
        self.assertEqual((t1["complexity"], t1["risk"]), ("medium", "none"))
        self.assertEqual((t2["complexity"], t2["risk"]), ("high", "security"))
        self.assertEqual(t2["depends"], [])
        problems = specs.validate_tasks(plan, None)
        self.assertIn("T03 complexity 'extreme' must be low, medium or high", problems)
        self.assertIn("T03 risk 'weather' must be security, concurrency or none", problems)
        self.assertEqual([p for p in problems if p.startswith(("T01", "T02"))], [])

    def test_cli(self) -> None:
        root = project()
        try:
            write(root, "specs/features/011-x/tasks.md", self.TEXT.replace("\n- [ ] T03", "\n- [ ] T09").replace("extreme", "low").replace("weather", "concurrency"))
            res = run_cli(root, "tasks", "specs/features/011-x", "--validate", "--no-trace")
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            tasks = json.loads(res.stdout)["waves"][0]["tasks"]
            self.assertEqual([(t["complexity"], t["risk"]) for t in tasks], [("medium", "none"), ("high", "security"), ("low", "concurrency")])
            write(root, "specs/features/011-x/tasks.md", self.TEXT)
            res = run_cli(root, "tasks", "specs/features/011-x", "--validate", "--no-trace")
            self.assertEqual(res.returncode, 1)
            self.assertIn("complexity 'extreme'", res.stdout)
        finally:
            rm(root)


def e(cost: float, intel: float, floor: float, mult: float = 1.0) -> float:
    return cost * (1 + 0.3 * math.exp(-(intel - floor) / 4) * 2) * mult


class TableTest(unittest.TestCase):
    """AC 5: candidates, E values, floors, dominance, capped pools."""

    def table_json(self, name: str) -> dict:
        root = project(name)
        try:
            res = run_cli(root, "routing", "table", "--json")
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            return json.loads(res.stdout)
        finally:
            rm(root)

    def test_formula_needs_a_candidate_per_level(self) -> None:
        """S14: in formula mode a level whose floor no point reaches is a table problem."""
        raw = {"version": 1, "pools": {"standard": {"capped": False}},
               "points": [{"model": "low-s", "effort": "medium", "intelligence": 50, "cost_per_task": 1, "tokens_per_task": 1000, "pool": "standard"}]}
        problems = routing.table_problems(routing.normalize(raw), formula=True)
        self.assertTrue(any(p.startswith("level 6: no point reaches its floor") for p in problems), problems)
        self.assertFalse(any(p.startswith("level") for p in routing.table_problems(routing.normalize(raw), formula=False)))

    def test_two_pool_hand_computed(self) -> None:
        out = self.table_json("two-pool.json")
        l3 = out["levels"]["3"]
        self.assertEqual([c["model"] for c in l3], ["std-s", "cap-c"])
        self.assertAlmostEqual(l3[0]["E"], 2.01, places=2)
        self.assertAlmostEqual(l3[1]["E"], 3.12, places=2)
        l4 = out["levels"]["4"]
        self.assertEqual([c["model"] for c in l4], ["cap-c", "std-s"])
        self.assertAlmostEqual(l4[0]["E"], 1.17, places=2)
        self.assertAlmostEqual(l4[1]["E"], 2.06, places=2)
        self.assertEqual([c["model"] for c in out["levels"]["6"]], ["std-s"])  # 58 < floor 61
        self.assertAlmostEqual(l4[0]["E"], e(1, 58, 53), places=6)

    def test_frontier_properties(self) -> None:
        out = self.table_json("frontier.json")
        table = json.loads((FIX / "frontier.json").read_text())
        floors = {int(k): v for k, v in table["floors"].items()}
        points = table["points"]
        self.assertIn("slow-d/high", out["dominated"])
        picks = {}
        for level in range(1, 7):
            cands = out["levels"][str(level)]
            self.assertTrue(cands)
            self.assertTrue(all(c["intelligence"] >= floors[level] for c in cands))
            self.assertEqual([c["E"] for c in cands], sorted(c["E"] for c in cands))
            pick = cands[0]
            picks[level] = pick["model"]
            mult = lambda p: 3 if p["pool"] == "capped" and level <= 3 else 1  # noqa: E731
            for p in points:
                if p["intelligence"] < floors[level] or p["model"] == pick["model"]:
                    continue
                dominates = p["intelligence"] >= pick["intelligence"] and p["cost_per_task"] * mult(p) <= pick["cost_per_task"] * mult(pick)
                self.assertFalse(dominates, (level, p["model"], pick["model"]))
        self.assertEqual(picks, {1: "small-a", 2: "mid-b", 3: "mid-b", 4: "cap-c", 5: "cap-c", 6: "top-e"})
        l3 = {c["model"]: c["E"] for c in out["levels"]["3"]}
        self.assertAlmostEqual(l3["cap-c"], e(0.8, 60, 47, 3), places=6)

    def test_invalid_table(self) -> None:
        root = project()
        try:
            write(root, "sdd.models.json", json.dumps({"version": 1, "pools": {"standard": {"capped": False}},
                                                       "points": [{"model": "a-1", "effort": "turbo", "intelligence": 50, "cost_per_task": 1, "tokens_per_task": 1, "pool": "nope"}]}))
            res = run_cli(root, "routing", "table")
            self.assertEqual(res.returncode, 1)
            self.assertIn("effort", res.stdout + res.stderr)
            self.assertIn("pool", res.stdout + res.stderr)
        finally:
            rm(root)

    def test_pipeline_view_carries_candidates(self) -> None:
        root = project("two-pool.json", pipeline={"routing": "formula"})
        try:
            res = run_cli(root, "config", "--pipeline")
            view = json.loads(res.stdout)
            self.assertEqual(view["routing"]["mode"], "formula")
            self.assertEqual([c["model"] for c in view["routing"]["candidates"]["4"]], ["cap-c", "std-s"])
            self.assertTrue(view["routing"]["table"]["pools"]["capped"]["capped"])
            self.assertEqual(view["routing"]["table"]["roles"]["implementer"], {"base": 3, "min": 2})
        finally:
            rm(root)


class TemplateTest(unittest.TestCase):
    """AC 10: formula with the template table fails (templates: test_routing_templates, README: test_routing_docs)."""

    def test_formula_with_template_table_fails(self) -> None:
        root = project(pipeline={"routing": "formula"})
        try:
            res = run_cli(root, "config", "--validate")
            self.assertEqual(res.returncode, 1)
            self.assertIn("example-", res.stdout)
            table = run_cli(root, "routing", "table")
            self.assertEqual(table.returncode, 1)
            self.assertIn("placeholder", table.stdout)
        finally:
            rm(root)
        root = project()  # tiers mode: the template table is fine (levels and telemetry only)
        try:
            self.assertEqual(run_cli(root, "routing", "table").returncode, 0)
        finally:
            rm(root)
        root = project("two-pool.json", pipeline={"routing": "formula"})
        try:
            res = run_cli(root, "config", "--validate")
            self.assertNotIn("example-", res.stdout)
        finally:
            rm(root)


class LogTest(unittest.TestCase):
    """AC 8 (host side): sdd routing log joins times, deduplicates, validates."""

    def setUp(self) -> None:
        self.root = project()

    def tearDown(self) -> None:
        rm(self.root)

    def lines(self) -> list:
        path = self.root / ".sdd" / "logs" / "routing.jsonl"
        return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []

    def test_output_file_join_and_resume_dedup(self) -> None:
        res = run_cli(self.root, "routing", "log", "--output-file", str(FIX / "output-paused.json"))
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        rows = self.lines()
        self.assertEqual(len(rows), 4)
        first = rows[0]
        self.assertEqual((first["run"], first["feature"], first["status"]), ("F011-20260101T000000", "F011", "paused"))
        self.assertEqual(first["started_at"], "2026-01-01T00:00:00.000Z")
        self.assertEqual(first["duration_ms"], 30000)
        self.assertIs(first["replayed"], False)
        self.assertTrue(first["logged_at"])
        self.assertEqual(rows[2]["fallback_of"], "cap-c")
        self.assertEqual(rows[3]["duration_ms"], 3000)
        res = run_cli(self.root, "routing", "log", "--output-file", str(FIX / "output-resumed.json"))
        self.assertEqual(res.returncode, 0, res.stderr)
        rows = self.lines()
        self.assertEqual(len(rows), 6, rows)
        planner = [r for r in rows if r["call"] == "architect-planner F011#1" and r["attempt"] == 1]
        self.assertEqual(sorted(str(r["outcome"]) for r in planner), ["None", "ok"])
        self.assertEqual([r["status"] for r in planner if r["outcome"] == "ok"], ["stopped"])
        self.assertEqual(len([r for r in rows if r["call"] == "setup#1"]), 1)
        # Logging the same output again appends nothing.
        run_cli(self.root, "routing", "log", "--output-file", str(FIX / "output-resumed.json"))
        self.assertEqual(len(self.lines()), 6)

    def test_replayed_entries_have_null_times(self) -> None:
        run_cli(self.root, "routing", "log", "--output-file", str(FIX / "output-resumed.json"))
        rows = {r["call"]: r for r in self.lines()}
        self.assertIs(rows["setup#1"]["replayed"], True)
        self.assertIsNone(rows["setup#1"]["started_at"])
        self.assertIsNone(rows["setup#1"]["duration_ms"])
        self.assertEqual(rows["architect-planner F011#1"]["duration_ms"], 600000)

    def test_same_label_joins_in_call_order_and_unmatched(self) -> None:
        res = run_cli(self.root, "routing", "log", "--output-file", str(FIX / "output-same-label.json"))
        self.assertEqual(res.returncode, 0, res.stderr)
        rows = self.lines()
        self.assertEqual([r["duration_ms"] for r in rows], [3000, 7000, None])
        self.assertIn("1 ", res.stdout + res.stderr)
        self.assertIn("without times", res.stdout + res.stderr)

    def test_transcript_dir_only(self) -> None:
        summary = json.loads((FIX / "output-paused.json").read_text())["result"]
        write(self.root, "summary.json", json.dumps(summary))
        res = run_cli(self.root, "routing", "log", "--file", str(self.root / "summary.json"), "--transcript-dir", str(FIX / "transcripts"))
        self.assertEqual(res.returncode, 0, res.stderr)
        rows = self.lines()
        self.assertEqual(rows[0]["started_at"], "2026-01-01T00:00:00.000Z")
        self.assertEqual(rows[0]["duration_ms"], 30000)
        self.assertEqual(rows[2]["duration_ms"], 2500)
        self.assertIsNone(rows[3]["duration_ms"])  # no agent transcript for a4
        self.assertIn("without times", res.stdout + res.stderr)

    def test_unknown_host_format(self) -> None:
        data = json.loads((FIX / "output-paused.json").read_text())
        data["workflowProgress"] = {"something": "else"}
        write(self.root, "out.json", json.dumps(data))
        res = run_cli(self.root, "routing", "log", "--output-file", str(self.root / "out.json"))
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue(all(r["started_at"] is None for r in self.lines()))
        self.assertIn("4 ", res.stdout + res.stderr)

    def test_invalid_records(self) -> None:
        summary = json.loads((FIX / "output-paused.json").read_text())["result"]
        del summary["telemetry"][1]["level"]
        write(self.root, "bad.json", json.dumps(summary))
        res = run_cli(self.root, "routing", "log", "--file", str(self.root / "bad.json"))
        self.assertEqual(res.returncode, 2)
        self.assertIn("level", res.stderr)
        self.assertEqual(self.lines(), [])
        write(self.root, "none.json", json.dumps({"feature": "F011"}))
        self.assertEqual(run_cli(self.root, "routing", "log", "--file", str(self.root / "none.json")).returncode, 2)


class RefitTest(unittest.TestCase):
    """AC 9: per-level failure rates and floor suggestions, report only."""

    def test_refit(self) -> None:
        root = project("frontier.json")
        try:
            shutil.copy(FIX / "refit.jsonl", root / "routing.jsonl")
            digest = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (root / "sdd.models.json", root / "sdd.config.json", root / "routing.jsonl")}
            res = run_cli(root, "routing", "refit", "--json", "--file", str(root / "routing.jsonl"))
            self.assertEqual(res.returncode, 0, res.stderr)
            out = json.loads(res.stdout)
            l2 = out["levels"]["2"]
            self.assertEqual((l2["samples"], l2["fail"], l2["infra"]), (10, 4, 3))
            self.assertAlmostEqual(l2["failure_rate"], 0.4)
            self.assertAlmostEqual(l2["retry_rate"], 0.2)
            self.assertEqual(out["levels"]["2"]["models"]["mid-b"]["samples"], 10)
            sug = {s["level"]: s for s in out["suggestions"]}
            self.assertEqual((sug[2]["floor"], sug[2]["suggest"]), (40, 50))
            self.assertEqual((sug[4]["floor"], sug[4]["suggest"]), (53, 50))
            self.assertNotIn(3, sug)
            text = run_cli(root, "routing", "refit", "--file", str(root / "routing.jsonl"))
            self.assertEqual(text.returncode, 0)
            self.assertIn("raise", text.stdout)
            self.assertIn("lower", text.stdout)
            for p, h in digest.items():
                self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(), h, p)
            self.assertEqual(run_cli(root, "routing", "refit", "--file", str(root / "missing.jsonl")).returncode, 2)
        finally:
            rm(root)


class RefitJournalInputTest(unittest.TestCase):
    """Pluggable refit input: Workflow journals (level from a ' [L<n>]' label suffix, model from agent meta files)."""

    JOURNALS = FIX / "journals"

    def test_read_journal(self) -> None:
        rows = routing.read_journal(self.JOURNALS / "run-a" / "journal.jsonl")
        got = [(r["label"], r["level"], r["model"], r["outcome"], r["attempt"]) for r in rows]
        self.assertEqual(got, [
            ("setup", 1, "mech-a", "ok", 1),
            ("T01 impl", 3, "mid-a", "fail", 1),
            ("T01 impl retry", 4, "mid-a", "ok", 2),
            ("spec-critic r1", 5, "inherit", "ok", 1),  # no model in the meta file: the session model
            ("security-reviewer", 5, "strong-a", None, 1),  # null result: usage cap or outage
            ("verify R1 #1", 5, "strong-a", None, 1),  # failed entry
            ("perf-reviewer", 3, "mid-a", None, 1),  # started, never finished
        ])  # the unrouted "gates post-implement #1" is skipped
        self.assertEqual(routing.journal_paths([str(self.JOURNALS / "*")]), [self.JOURNALS / "run-a" / "journal.jsonl", self.JOURNALS / "run-b" / "journal.jsonl"])

    def test_cli_journal_input(self) -> None:
        root = project("frontier.json")
        try:
            res = run_cli(root, "routing", "refit", "--json", "--journal", str(self.JOURNALS / "run-a"), "--journal", str(self.JOURNALS / "run-b" / "journal.jsonl"))
            self.assertEqual(res.returncode, 0, res.stderr)
            out = json.loads(res.stdout)
            self.assertEqual(out["inputs"], ["journal"])
            l3 = out["levels"]["3"]
            self.assertEqual((l3["samples"], l3["fail"], l3["infra"]), (3, 1, 1))
            self.assertAlmostEqual(l3["retry_rate"], 1 / 3)  # the capped-fallback attempt
            self.assertEqual({m: (v["samples"], v["fail"]) for m, v in l3["models"].items()}, {"mid-a": (2, 1), "mid-b": (1, 0)})
            l5 = out["levels"]["5"]
            self.assertEqual((l5["samples"], l5["infra"]), (1, 2))
            glob_res = run_cli(root, "routing", "refit", "--json", "--input", "journal", "--journal", str(self.JOURNALS / "run-*"))
            self.assertEqual(json.loads(glob_res.stdout)["levels"], out["levels"])
            # Inputs pool: telemetry plus journals.
            shutil.copy(FIX / "refit.jsonl", root / "routing.jsonl")
            both = run_cli(root, "routing", "refit", "--json", "--input", "telemetry", "--input", "journal", "--file", str(root / "routing.jsonl"), "--journal", str(self.JOURNALS / "run-*"))
            self.assertEqual(both.returncode, 0, both.stderr)
            tel = json.loads(run_cli(root, "routing", "refit", "--json", "--file", str(root / "routing.jsonl")).stdout)
            pooled = json.loads(both.stdout)
            self.assertEqual(pooled["inputs"], ["telemetry", "journal"])
            self.assertEqual(pooled["levels"]["3"]["samples"], tel["levels"].get("3", {"samples": 0})["samples"] + 3)
            text = run_cli(root, "routing", "refit", "--journal", str(self.JOURNALS / "run-*"))
            self.assertIn("from 2 journal(s)", text.stdout)
            # No journals, or journals without routed labels: exit 2 with a reason.
            none = run_cli(root, "routing", "refit", "--journal", str(root / "nothing-*"))
            self.assertEqual(none.returncode, 2)
            self.assertIn("no workflow journals", none.stderr)
            write(root, "plain/journal.jsonl", json.dumps({"type": "started", "key": "k", "agentId": "x", "label": "setup"}) + "\n")
            plain = run_cli(root, "routing", "refit", "--journal", str(root / "plain"))
            self.assertEqual(plain.returncode, 2)
            self.assertIn("[L<n>]", plain.stderr)
        finally:
            rm(root)

    def test_failed_outcome_alias(self) -> None:
        root = project("frontier.json")
        try:
            write(root, "t.jsonl", "".join(json.dumps({"label": f"c{i}", "level": 2, "model": "m", "outcome": o}) + "\n" for i, o in enumerate(["failed", "ok", None])))
            out = json.loads(run_cli(root, "routing", "refit", "--json", "--file", str(root / "t.jsonl")).stdout)
            self.assertEqual((out["levels"]["2"]["samples"], out["levels"]["2"]["fail"], out["levels"]["2"]["infra"]), (2, 1, 1))
        finally:
            rm(root)


class CandidatesApiTest(unittest.TestCase):
    """The one implementation of the formula (used by the pipeline smoke test too)."""

    def test_candidates_function(self) -> None:
        table = routing.normalize(json.loads((FIX / "two-pool.json").read_text()))
        cands = routing.candidates(table)
        self.assertEqual(sorted(cands), [str(i) for i in range(1, 7)])
        self.assertEqual(cands["4"][0]["model"], "cap-c")


if __name__ == "__main__":
    unittest.main()
