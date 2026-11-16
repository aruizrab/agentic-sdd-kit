"""`sdd progress`: cross-cutting group, display names (spec title, roadmap title, slug), placeholder notes."""

from __future__ import annotations

import datetime as dt
import unittest

from helpers import GitRepo, make_tmp, rm, run_cli, test_config, write, write_config
from sddkit import config as cfgmod
from sddkit import progress

ROADMAP = """# roadmap

#### F009 early-bird

### M0 — Foundations

#### F001 workspace — Workspace and tooling
#### F002 runtime — Runtime scheduler (placeholder, specs later)

### M1 — Release 2 profile (placeholder, specs later)

#### F003 events
#### F004 later — (TBD)

### Cross-cutting — observability and packaging (placeholder)

#### F016 container-images — Container images and charts
#### F017 metrics-feed
"""


class ParseRoadmapTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = test_config()

    def test_groups_and_names(self) -> None:
        groups, features = progress.parse_roadmap(ROADMAP, self.cfg)
        self.assertEqual([(g["id"], g["name"], g["kind"]) for g in groups], [
            ("M0", "Foundations", "milestone"),
            ("M1", "Release 2 profile", "milestone"),
            ("CC", "Observability and packaging", "cross-cutting"),
            ("unassigned", "No milestone", "unassigned"),
        ])
        by = {f["id"]: (f["slug"], f["name"], f["milestone"]) for f in features}
        self.assertEqual(by, {
            "F009": ("early-bird", "early-bird", "unassigned"),
            "F001": ("workspace", "Workspace and tooling", "M0"),
            "F002": ("runtime", "Runtime scheduler", "M0"),
            "F003": ("events", "events", "M1"),
            "F004": ("later", "(TBD)", "M1"),  # a title that is only the note is kept
            "F016": ("container-images", "Container images and charts", "CC"),
            "F017": ("metrics-feed", "metrics-feed", "CC"),
        })

    def test_cross_cutting_heading_forms(self) -> None:
        for heading, name in (("### Cross-cutting", "Cross-cutting"), ("### cross-cutting: ops", "Ops"), ("### Cross-cutting – tooling (TBD)", "Tooling")):
            with self.subTest(heading=heading):
                groups, features = progress.parse_roadmap(f"### M0 — A\n#### F001 a\n{heading}\n#### F002 b\n### M1 — B\n#### F003 c\n", self.cfg)
                self.assertEqual([(g["id"], g["name"]) for g in groups], [("M0", "A"), ("M1", "B"), ("CC", name)])
                self.assertEqual([f["milestone"] for f in features], ["M0", "CC", "M1"])
        # Several cross-cutting sections form one group named after the first.
        groups, features = progress.parse_roadmap("### Cross-cutting — one\n#### F001 a\n### M0 — A\n#### F002 b\n### Cross-cutting — two\n#### F003 c\n", self.cfg)
        self.assertEqual([g["id"] for g in groups], ["M0", "CC"])
        self.assertEqual([f["milestone"] for f in features], ["CC", "M0", "CC"])

    def test_configurable_heading_and_older_configs(self) -> None:
        cfg = test_config()
        cfg["features"]["roadmap_cross_cutting_regex"] = r"^## Platform(?: — (.*))?$"
        groups, _ = progress.parse_roadmap("### M0 — A\n#### F001 a\n## Platform — infra\n#### F002 b\n", cfg)
        self.assertEqual(groups[-1], {"id": "CC", "name": "Infra", "kind": "cross-cutting"})
        del cfg["features"]["roadmap_cross_cutting_regex"]
        groups, _ = progress.parse_roadmap("### M0 — A\n#### F001 a\n### Cross-cutting\n#### F002 b\n", cfg)
        self.assertEqual(groups[-1]["kind"], "cross-cutting")
        bad = test_config()
        bad["features"]["roadmap_cross_cutting_regex"] = "(("
        self.assertTrue(any("roadmap_cross_cutting_regex" in p for p in cfgmod.validate(bad)))

    def test_spec_title(self) -> None:
        self.assertEqual(progress.spec_title("# F002 — Runtime scheduler\n\nStatus: Draft\n", "F"), "Runtime scheduler")
        self.assertEqual(progress.spec_title("# F002 - Runtime (placeholder)\n", "F"), "Runtime")
        self.assertIsNone(progress.spec_title("# F002 — <Feature title>\n", "F"))
        self.assertIsNone(progress.spec_title("# F002\n", "F"))
        self.assertIsNone(progress.spec_title(None, "F"))

    def test_display_name(self) -> None:
        self.assertEqual(progress.display_name("Release 18 profile (placeholder, specs later)"), "Release 18 profile")
        self.assertEqual(progress.display_name("Paging (to be defined)"), "Paging")
        self.assertEqual(progress.display_name("Paging (phase 2)"), "Paging (phase 2)")


class ProgressGroupsReportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.base = make_tmp("progress-groups-")
        src = cls.base / "src"
        r = GitRepo(src)
        r.init()
        write(src, "docs/ROADMAP.md", ROADMAP)
        r.commit("chore: roadmap")
        # F001: merged, spec H1 title wins over the roadmap title.
        r.git("checkout", "-q", "-b", "feat/F001-workspace")
        write(src, "specs/features/001-workspace/spec.md", "# F001 — Workspace, CI and tooling\n\nStatus: Implemented\n")
        r.commit("docs(F001): implemented")
        r.git("checkout", "-q", "main")
        r.git("merge", "-q", "--no-ff", "-m", "merge F001", "feat/F001-workspace")
        # F016 (cross-cutting): approved spec whose H1 still has the template placeholder: roadmap title.
        r.git("checkout", "-q", "-b", "feat/F016-container-images", "main")
        write(src, "specs/features/016-container-images/spec.md", "# F016 — <Feature title>\n\nStatus: Approved\n")
        r.commit("docs(F016): approve")
        # F017 (cross-cutting): draft spec with a title on its branch.
        r.git("checkout", "-q", "-b", "feat/F017-metrics-feed", "main")
        write(src, "specs/features/017-metrics-feed/spec.md", "# F017 — Metrics feed (TBD)\n\nStatus: Draft\n")
        r.commit("docs(F017): draft")
        r.git("checkout", "-q", "main")
        cls.work = cls.base / "work"
        r.git("clone", "-q", str(src), str(cls.work), cwd=cls.base)
        cls.cfg = test_config()
        write_config(cls.work, cls.cfg)

    @classmethod
    def tearDownClass(cls) -> None:
        rm(cls.base)

    def test_report(self) -> None:
        report = progress.build_report(self.work, self.cfg, now=dt.datetime(2026, 11, 13, tzinfo=dt.timezone.utc))
        names = {f["id"]: f["name"] for f in report["features"]}
        self.assertEqual(names["F001"], "Workspace, CI and tooling")
        self.assertEqual(names["F002"], "Runtime scheduler")
        self.assertEqual(names["F003"], "events")
        self.assertEqual(names["F016"], "Container images and charts")
        self.assertEqual(names["F017"], "Metrics feed")
        self.assertEqual({f["id"]: f["slug"] for f in report["features"]}["F016"], "container-images")
        groups = {m["id"]: m for m in report["milestones"]}
        self.assertEqual(groups["CC"]["kind"], "cross-cutting")
        self.assertEqual(groups["CC"]["features"], ["F016", "F017"])
        self.assertEqual(groups["CC"]["percent"], 10.0)  # (15 + 5) / 2
        self.assertEqual(groups["M0"]["percent"], 50.0)  # (100 + 0) / 2: no cross-cutting feature in a milestone
        self.assertEqual(groups["M1"]["percent"], 0.0)
        # Overall covers every feature, cross-cutting included: (100 + 15 + 5) / 7.
        self.assertEqual(report["overall"], round(120 / 7, 1))
        table = progress.render_table(report)
        self.assertIn("  CC    10.0%  Observability and packaging [cross-cutting] (2 features)", table)
        self.assertIn("F016    15.0%  spec-approved  Container images and charts", table)

    def test_cli_json(self) -> None:
        proc = run_cli(self.work, "progress", "--json", env={"SOURCE_DATE_EPOCH": "0"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('"kind": "cross-cutting"', proc.stdout)


class PartlyMergedFeatureTest(unittest.TestCase):
    """A feature that merges in parts (plan first, then one branch per work item) is not finished
    while its spec on main is not Implemented; its stage comes from the newest unmerged branch,
    else from main's tasks."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.base = make_tmp("progress-parts-")
        src = cls.base / "src"
        r = GitRepo(src)
        r.init()
        write(src, "docs/ROADMAP.md", "### M0 — A\n\n#### F005 remediation\n#### F006 tooling\n")
        r.commit("chore: roadmap")
        d = "specs/features/005-remediation"

        def tasks(done: int) -> str:
            return "# Tasks\n\n" + "\n".join(f"- [{'x' if i <= done else ' '}] T{i:02d} do {i} — files: a.py — verifies: FC-DEV-R05{i} — depends: -" for i in range(1, 5)) + "\n"

        r.git("checkout", "-q", "-b", "feat/F005-remediation")
        write(src, f"{d}/spec.md", "# F005 — Remediation\n\nStatus: Approved\n")
        write(src, f"{d}/design.md", "# d\n")
        write(src, f"{d}/tasks.md", tasks(0))
        r.commit("docs(F005): plan")
        r.git("checkout", "-q", "main")
        r.git("merge", "-q", "--no-ff", "-m", "merge F005 plan", "feat/F005-remediation")
        r.git("checkout", "-q", "-b", "feat/F005-wi01", "main")
        write(src, f"{d}/tasks.md", tasks(1))
        r.commit("feat(F005): T01")
        r.git("checkout", "-q", "main")
        r.git("merge", "-q", "--no-ff", "-m", "merge F005 WI-01", "feat/F005-wi01")
        # F006: its branch merged code only; main holds no spec of it (the unchanged rule).
        r.git("checkout", "-q", "-b", "feat/F006-tooling", "main")
        write(src, "tools/x.py", "x = 1\n")
        r.commit("chore(F006): tooling")
        r.git("checkout", "-q", "main")
        r.git("merge", "-q", "--no-ff", "-m", "merge F006", "feat/F006-tooling")
        cls.src, cls.repo, cls.dir, cls.tasks = src, r, d, staticmethod(tasks)
        cls.cfg = test_config()

    @classmethod
    def tearDownClass(cls) -> None:
        rm(cls.base)

    def stages(self) -> dict:
        report = progress.build_report(self.src, self.cfg, now=dt.datetime(2026, 11, 16, tzinfo=dt.timezone.utc))
        return {f["id"]: (f["stage"], f["percent"]) for f in report["features"]}

    def test_stages(self) -> None:
        r = self.repo
        write_config(self.src, self.cfg)
        # Every F005 branch so far is merged, but the spec on main is Approved: implementing 1/4.
        self.assertEqual(self.stages(), {"F005": ("implementing", 45.0), "F006": ("merged", 100.0)})
        # An unmerged work-item branch is the evidence while it exists.
        r.git("checkout", "-q", "-b", "feat/F005-wi02", "main")
        write(self.src, f"{self.dir}/tasks.md", self.tasks(2))
        r.commit("feat(F005): T02")
        self.assertEqual(self.stages()["F005"], ("implementing", 60.0))
        # A started feature with an unmerged branch carries its last commit and is idle after 24 h without one.
        last = dt.datetime.fromtimestamp(int(r.git("log", "-1", "--format=%ct", "feat/F005-wi02").strip()), tz=dt.timezone.utc)
        soon = progress.build_report(self.src, self.cfg, now=last + dt.timedelta(hours=1))
        f5 = {f["id"]: f for f in soon["features"]}["F005"]
        self.assertEqual((f5["lastCommit"], f5["idle"]), (last.strftime("%Y-%m-%dT%H:%M:%SZ"), False))
        late = progress.build_report(self.src, self.cfg, now=last + dt.timedelta(hours=25))
        self.assertTrue({f["id"]: f for f in late["features"]}["F005"]["idle"])
        self.assertIn(f"(idle since {last.strftime('%Y-%m-%d')})", progress.render_table(late))
        self.assertNotIn("idle", {f["id"]: f for f in late["features"]}["F006"], "a merged feature has no idle flag")
        # Finished only when the spec on main says Implemented.
        r.git("checkout", "-q", "main")
        r.git("merge", "-q", "--no-ff", "-m", "merge F005 WI-02", "feat/F005-wi02")
        write(self.src, f"{self.dir}/spec.md", "# F005 — Remediation\n\nStatus: Implemented\n")
        r.commit("docs(F005): implemented")
        self.assertEqual(self.stages()["F005"], ("merged", 100.0))


class RoadmapTemplateTest(unittest.TestCase):
    def test_template_headings_have_titles_and_parse(self) -> None:
        from helpers import KIT

        text = (KIT / "templates" / "docs" / "ROADMAP.md").read_text(encoding="utf-8")
        groups, features = progress.parse_roadmap(text, test_config())
        self.assertTrue(features)
        for f in features:
            self.assertNotEqual(f["name"], f["slug"], f"{f['id']} heading has a display title")
        self.assertIn("cross-cutting", [g["kind"] for g in groups])


if __name__ == "__main__":
    unittest.main()
