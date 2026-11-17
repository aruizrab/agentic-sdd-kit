"""`sdd progress`: when a feature counts as planned (scaffold plans, stale plans) and the idle
flag of a feature whose branches are all merged."""

from __future__ import annotations

import datetime as dt
import unittest

from helpers import KIT, GitRepo, make_tmp, rm, test_config, write, write_config
from sddkit import new_feature, progress

TEMPLATES = KIT / "templates" / "specs" / "templates"


def template(name: str) -> str:
    return (TEMPLATES / name).read_text(encoding="utf-8")


def scaffold(name: str, number: str = "016", slug: str = "images") -> str:
    """The kit template as `sdd new-feature` writes it into a feature directory."""
    return new_feature.substitute(template(name), number, slug, test_config())


def tasks_md(done: int, total: int) -> str:
    lines = "\n".join(
        f"- [{'x' if i <= done else ' '}] T{i:02d} build part {i} — files: src/p{i}.py — verifies: FC-DEV-R0{i} — depends: -"
        for i in range(1, total + 1)
    )
    return f"# Tasks\n\n## Wave 1\n\n{lines}\n"


REAL_DESIGN = "# F016 — Container images — Design\n\n## 1. Overview\n\nThe image is built in two stages.\n"


class ScaffoldTest(unittest.TestCase):
    def setUp(self) -> None:
        self.kit = progress.Scaffold(template("design.md"), template("tasks.md"))

    def test_markers_come_from_the_templates(self) -> None:
        self.assertEqual(self.kit.title_tokens, ("<Feature title>",))
        self.assertTrue(self.kit.body_marker.startswith("<How the approved requirements are realised"))
        self.assertTrue(self.kit.task_lines)

    def test_scaffold_is_not_a_plan(self) -> None:
        for design, tasks in ((template("design.md"), template("tasks.md")), (scaffold("design.md"), scaffold("tasks.md"))):
            with self.subTest(design=design[:20]):
                self.assertTrue(self.kit.is_scaffold_design(design))
                self.assertFalse(self.kit.is_plan(design, tasks))
        self.assertEqual(progress.count_tasks(scaffold("tasks.md"), self.kit.task_lines), (0, 0))

    def test_written_plan(self) -> None:
        self.assertTrue(self.kit.is_plan(REAL_DESIGN, tasks_md(0, 2)))
        # A written design that mentions the placeholder in its text is still a design.
        self.assertTrue(self.kit.is_plan(REAL_DESIGN + "The scaffold title is `<Feature title>`.\n", tasks_md(0, 2)))
        # A design filled in except the title.
        self.assertFalse(self.kit.is_plan("# F016 — <Feature title> — Design\n\nWritten.\n", tasks_md(0, 2)))
        # Either half still the scaffold, or absent: not planned.
        self.assertFalse(self.kit.is_plan(REAL_DESIGN, scaffold("tasks.md")))
        self.assertFalse(self.kit.is_plan(scaffold("design.md"), tasks_md(0, 2)))
        self.assertFalse(self.kit.is_plan(None, tasks_md(0, 2)))
        self.assertFalse(self.kit.is_plan(REAL_DESIGN, None))
        self.assertFalse(self.kit.is_plan(REAL_DESIGN, "# Tasks\n\nnone yet\n"))

    def test_project_templates_not_kit_strings(self) -> None:
        """A project's own templates define its scaffold; nothing of the kit's wording is assumed."""
        design_t = "# {{FID}} — <Title here>\n\n## Summary\n\n```\n<not a marker: in a code block>\n```\n\n<Write the summary.>\n"
        tasks_t = "# Tasks\n\n- [ ] T01 Example task — files: src/a.py\n"
        own = progress.Scaffold(design_t, tasks_t)
        self.assertEqual((own.title_tokens, own.body_marker), (("<Title here>",), "<Write the summary.>"))
        self.assertTrue(own.is_scaffold_design(design_t.replace("{{FID}}", "F016")))
        self.assertTrue(own.is_scaffold_design("# F016 — Images\n\n## Summary\n\n<Write the summary.>\n"))
        self.assertFalse(own.is_scaffold_design("# F016 — Images\n\n## Summary\n\nTwo stages.\n"))
        self.assertEqual(progress.count_tasks(tasks_t, own.task_lines), (0, 0))
        self.assertFalse(own.is_plan("# F016 — Images\n\nTwo stages.\n", tasks_t))
        # The kit's wording means nothing to these templates.
        self.assertFalse(own.is_scaffold_design("# F016 — Images\n\n<How the approved requirements are realised\n"))

    def test_without_templates_only_a_missing_file_is_unplanned(self) -> None:
        none = progress.Scaffold()
        self.assertTrue(none.is_plan("# d\n", tasks_md(0, 1)))
        self.assertFalse(none.is_plan(None, tasks_md(0, 1)))
        self.assertFalse(none.is_plan("# d\n", "# Tasks\n"))


class PlanStageTest(unittest.TestCase):
    """build_report on a repository that holds the kit templates (as an installed project does)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.base = make_tmp("progress-plan-")
        src = cls.base / "src"
        r = GitRepo(src)
        r.init()
        write(src, "docs/ROADMAP.md", "### M0 — A\n\n" + "".join(f"#### F00{i} f{i}\n" for i in range(1, 7)))
        for name in ("design.md", "tasks.md"):
            write(src, f"specs/templates/{name}", template(name))
        r.commit("chore: roadmap and templates")

        def spec(fid: str, status: str, extra: str = "", bold: bool = False) -> str:
            line = f"**Status:** {status}" if bold else f"Status: {status}"
            return f"# {fid} — Feature {fid}\n\n{line}\n{extra}"

        def plan(d: str, fid: str) -> None:
            write(src, f"{d}/design.md", f"# {fid} — Feature {fid} — Design\n\n## 1. Overview\n\nWritten.\n")
            write(src, f"{d}/tasks.md", tasks_md(0, 2))

        # F001: approved, design/tasks still the new-feature scaffold -> spec-approved.
        r.git("checkout", "-q", "-b", "feat/F001-f1", "main")
        d = "specs/features/001-f1"
        write(src, f"{d}/spec.md", spec("F001", "Draft"))
        write(src, f"{d}/design.md", scaffold("design.md", "001", "f1"))
        write(src, f"{d}/tasks.md", scaffold("tasks.md", "001", "f1"))
        r.commit("docs(F001): scaffold and draft spec")
        write(src, f"{d}/spec.md", spec("F001", "Approved"))
        r.commit("docs(F001): approve spec")

        # F002: planned, then a spec edit that keeps the Status line -> planned.
        r.git("checkout", "-q", "-b", "feat/F002-f2", "main")
        d = "specs/features/002-f2"
        write(src, f"{d}/spec.md", spec("F002", "Approved"))
        r.commit("docs(F002): approve spec")
        plan(d, "F002")
        r.commit("docs(F002): plan")
        write(src, f"{d}/spec.md", spec("F002", "Approved", "typo fixed\n"))
        r.commit("docs(F002): fix a sentence")

        # F003: planned, then re-specified (Draft) and re-approved -> the plan is stale.
        r.git("checkout", "-q", "-b", "feat/F003-f3", "main")
        d = "specs/features/003-f3"
        write(src, f"{d}/spec.md", spec("F003", "Approved"))
        r.commit("docs(F003): approve spec")
        plan(d, "F003")
        r.commit("docs(F003): plan")
        write(src, f"{d}/spec.md", spec("F003", "Draft", "re-specified\n"))
        r.commit("docs(F003): re-specify")
        write(src, f"{d}/spec.md", spec("F003", "Approved", "re-specified\n"))
        r.commit("docs(F003): re-approve")

        # F004: planned, amendment re-approved, then red tests on the kept plan -> tests.
        r.git("checkout", "-q", "-b", "feat/F004-f4", "main")
        d = "specs/features/004-f4"
        write(src, f"{d}/spec.md", spec("F004", "Approved"))
        plan(d, "F004")
        r.commit("docs(F004): spec and plan")
        write(src, f"{d}/spec.md", spec("F004", "Draft", "amendment 1\n"))
        r.commit("docs(F004): amend spec")
        write(src, f"{d}/spec.md", spec("F004", "Approved", "amendment 1\n"))
        r.commit("docs(F004): approve amendment 1")
        write(src, "tests/test_f4.py", "# red\n")
        r.commit("test(F004): failing tests")

        # F005: a decorated Status line ("**Status:** Approved") re-approved after the plan -> stale.
        r.git("checkout", "-q", "-b", "feat/F005-f5", "main")
        d = "specs/features/005-f5"
        write(src, f"{d}/spec.md", spec("F005", "Approved", bold=True))
        plan(d, "F005")
        r.commit("docs(F005): spec and plan")
        write(src, f"{d}/spec.md", spec("F005", "Draft", bold=True))
        r.commit("docs(F005): re-specify")
        write(src, f"{d}/spec.md", spec("F005", "Approved", bold=True))
        r.commit("docs(F005): re-approve")

        # F006: merges in parts; every branch merged, spec on main still Approved -> implementing,
        # with lastCommit (the newest commit on its merged branches) and idle.
        r.git("checkout", "-q", "-b", "feat/F006-f6", "main")
        d = "specs/features/006-f6"
        write(src, f"{d}/spec.md", spec("F006", "Approved"))
        plan(d, "F006")
        r.commit("docs(F006): plan")
        r.git("checkout", "-q", "main")
        r.git("merge", "-q", "--no-ff", "-m", "merge F006 plan", "feat/F006-f6")
        r.git("checkout", "-q", "-b", "feat/F006-wi01", "main")
        write(src, f"{d}/tasks.md", tasks_md(1, 2))
        r.commit("feat(F006): T01")
        cls.f6_last = int(r.git("log", "-1", "--format=%ct").strip())
        r.git("checkout", "-q", "main")
        r.git("merge", "-q", "--no-ff", "-m", "merge F006 WI-01", "feat/F006-wi01")

        cls.src = src
        cls.cfg = test_config()
        write_config(src, cls.cfg)

    @classmethod
    def tearDownClass(cls) -> None:
        rm(cls.base)

    def report(self, now: dt.datetime) -> dict:
        return progress.build_report(self.src, self.cfg, remote="", now=now)

    def test_stages(self) -> None:
        rows = {f["id"]: f for f in self.report(dt.datetime(2026, 11, 17, tzinfo=dt.timezone.utc))["features"]}
        self.assertEqual({fid: (f["stage"], f["percent"]) for fid, f in rows.items()}, {
            "F001": ("spec-approved", 15.0),  # scaffold design/tasks are not a plan
            "F002": ("planned", 20.0),        # spec edit without a Status change keeps the plan
            "F003": ("spec-approved", 15.0),  # plan predates the re-approved spec
            "F004": ("tests", 30.0),          # red tests after the re-approval: the plan was kept
            "F005": ("spec-approved", 15.0),  # decorated Status line, same rule
            "F006": ("implementing", 60.0),   # 30 + 60 * 1/2, every branch merged
        })

    def test_idle_when_every_branch_is_merged(self) -> None:
        last = dt.datetime.fromtimestamp(self.f6_last, tz=dt.timezone.utc)
        f6 = {f["id"]: f for f in self.report(last + dt.timedelta(hours=25))["features"]}["F006"]
        self.assertEqual((f6["lastCommit"], f6["idle"]), (last.strftime("%Y-%m-%dT%H:%M:%SZ"), True))
        f6 = {f["id"]: f for f in self.report(last + dt.timedelta(hours=1))["features"]}["F006"]
        self.assertFalse(f6["idle"])

    def test_templates_from_the_working_tree_when_main_lacks_them(self) -> None:
        repo = progress.Repo(self.src, self.cfg, "")
        repo.load_templates("refs/heads/does-not-exist", self.src)
        self.assertEqual(repo.scaffold.title_tokens, ("<Feature title>",))


class LastActivityTest(unittest.TestCase):
    def test_unmerged_first_else_all(self) -> None:
        times = {"feat/F059-a": 100, "feat/F059-b": 300, "feat/F059-c": 200}

        class FakeGit:
            merged = set(times)

            def is_ancestor(self, ref: str, of: str) -> bool:
                return ref in self.merged

            def commit_time(self, ref: str) -> int:
                return times[ref]

        git = FakeGit()
        self.assertEqual(progress.last_activity(git, "main", list(times)), 300)  # all merged: all branches
        git.merged = {"feat/F059-b"}
        self.assertEqual(progress.last_activity(git, "main", list(times)), 200)  # unmerged ones only
        self.assertIsNone(progress.last_activity(git, "main", []))


if __name__ == "__main__":
    unittest.main()
