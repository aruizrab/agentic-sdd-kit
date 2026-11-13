"""Tests for sdd trace, spec-info/markers, tasks parsing and new-feature."""

from __future__ import annotations

import json
import unittest

from helpers import make_tmp, rm, run_cli, test_config, write, write_config

from sddkit import new_feature, specs, trace

SPEC = """# F011 — Registration

Feature: F011
Status: {status}

<!-- - **FC-OBS-R99** [must] this is a commented example that must not count -->
<!--
Approval is blocked while any "[NEEDS CLARIFICATION" marker remains.
-->

## 5. Requirements

- **TS24501-5.5.1.2.4-R110** [must] WHEN a REGISTRATION REQUEST arrives THE AMF SHALL start authentication. (Source: TS 24.501 §5.5.1.2.4)
- **TS24501-5.5.1.2.4-R111** [must] IF the message is malformed THEN THE AMF SHALL reject it. (Source: TS 24.501 §5.5.1.2.7)
- **FC-OBS-R110** [must] WHEN a state changes THE AMF SHALL emit an event. [behaviour-only] (Source: docs)
- **FC-OBS-R111** [should] THE AMF SHALL log at debug level. (Source: docs)
- **FC-OBS-R112** [must] old rule (superseded by FC-OBS-R110) (Source: docs)
- **FC-OBS-R113** [must] a `[NEEDS CLARIFICATION: x]` inside a code span is not open.

## 11. Open questions
{markers}
"""

RUST = """
/// Implements: TS24501-5.5.1.2.4-R110,
///   TS24501-5.5.1.2.4-R111
pub fn on_registration_request() {}

#[fc_trace::verifies("TS24501-5.5.1.2.4-R110", "FC-OBS-R110")]
fn triggers_auth() {}

#[verifies(
    "TS24501-5.5.1.2.4-R111",
)]
fn rejects_malformed() {}

fn implements(x: u32) -> u32 { x }   // a plain function named implements is not a tag
fn caller() { let _ = implements(3); }
"""

PY = """
@verifies("FC-OBS-R111")
def test_debug_log():
    pass

# Implements: FC-OBS-R111
def log_debug():
    pass
"""

GO = """
// Verifies: FC-OBS-R111
func TestFoo(t *testing.T) {}
"""


class TraceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_tmp("trace-")
        self.cfg = test_config()
        write_config(self.root, self.cfg)
        write(self.root, "specs/features/011-registration/spec.md", SPEC.format(status="Implemented", markers=""))
        write(self.root, "crates/fc-amf/src/lib.rs", RUST)
        write(self.root, "src/app/log.py", PY)
        write(self.root, "pkg/foo/foo_test.go", GO)
        write(self.root, "crates/target/debug/junk.rs", '#[verifies("FC-OBS-R999")] fn t() {}')

    def tearDown(self) -> None:
        rm(self.root)

    def test_matrix_and_check_pass(self) -> None:
        m = trace.load(self.root, self.cfg)
        self.assertEqual(set(m.requirements), {"TS24501-5.5.1.2.4-R110", "TS24501-5.5.1.2.4-R111", "FC-OBS-R110", "FC-OBS-R111", "FC-OBS-R112", "FC-OBS-R113"})
        self.assertEqual(m.features, {"011-registration": "Implemented"})
        impl = m.sites("TS24501-5.5.1.2.4-R111", "implements")
        self.assertEqual(len(impl), 1)
        self.assertEqual(impl[0].item, "on_registration_request")
        self.assertEqual(impl[0].line, 2)
        self.assertEqual(len(m.sites("TS24501-5.5.1.2.4-R111", "verifies")), 1)
        self.assertEqual(len(m.sites("FC-OBS-R111", "verifies")), 2)  # python decorator + go comment
        self.assertEqual(len(m.sites("FC-OBS-R111", "implements")), 1)
        self.assertFalse(any(t.id == "FC-OBS-R999" for t in m.tags), "excluded target dir was scanned")
        problems = trace.check(m, self.cfg)
        # FC-OBS-R113 is must, Implemented, no test/impl -> 2 problems; everything else is covered.
        self.assertEqual(sorted(problems), sorted([
            "FC-OBS-R113 (011-registration, Implemented): must requirement has no verifying test",
            "FC-OBS-R113 (011-registration, Implemented): must requirement has no implementation site",
        ]))
        self.assertEqual(trace.status_of(m, m.requirements["FC-OBS-R110"]), "implemented+tested")  # behaviour-only
        self.assertEqual(trace.status_of(m, m.requirements["FC-OBS-R112"]), "superseded by FC-OBS-R110")
        md = trace.to_markdown(m, "011")
        self.assertIn("| TS24501-5.5.1.2.4-R110 | must |", md)

    def test_python_docstring_tags(self) -> None:
        src = (
            "def add(a, b):\n"
            "    \"\"\"Implements: FC-OBS-R110\"\"\"\n"
            "    return a + b\n\n\n"
            "class TestAdd:\n"
            "    def test_add(self):\n"
            "        \'\'\'Verifies: FC-OBS-R110\n"
            "        A longer docstring.\n"
            "        \'\'\'\n"
            "        assert add(1, 1) == 2\n\n"
            "def other():\n"
            "    \"\"\"Return nothing. Implements nothing.\"\"\"\n"
        )
        tags = trace.scan_source(src, "src/app/doc.py", self.cfg)
        self.assertEqual([(t.id, t.kind, t.line, t.item) for t in tags],
                         [("FC-OBS-R110", "implements", 2, "add"), ("FC-OBS-R110", "verifies", 8, "test_add")])

    def test_draft_specs_are_not_enforced(self) -> None:
        write(self.root, "specs/features/011-registration/spec.md", SPEC.format(status="Draft", markers=""))
        m = trace.load(self.root, self.cfg)
        self.assertEqual(trace.check(m, self.cfg), [])

    def test_orphans_duplicates_and_unknown_superseded(self) -> None:
        write(self.root, "specs/features/012-other/spec.md", "Status: Draft\n- **FC-OBS-R110** [must] duplicate id. (Source: x)\n- **FC-OBS-R120** [may] y (superseded by FC-OBS-R777)\n")
        write(self.root, "crates/fc-x/src/lib.rs", "/// Implements: FC-OBS-R555\nfn f() {}\n")
        m = trace.load(self.root, self.cfg)
        problems = trace.check(m, self.cfg)
        self.assertTrue(any(p.startswith("duplicate requirement id FC-OBS-R110") for p in problems), problems)
        self.assertTrue(any("unknown requirement FC-OBS-R555" in p for p in problems), problems)
        self.assertTrue(any("superseded by unknown id FC-OBS-R777" in p for p in problems), problems)

    def test_cli_json_and_feature_filter(self) -> None:
        proc = run_cli(self.root, "trace", "--feature", "011", "--json", "--check")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(len(data["requirements"]), 6)
        self.assertEqual(len(data["problems"]), 2)
        proc = run_cli(self.root, "trace", "--feature", "999", "--check")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_include_scans_files_outside_scan_globs(self) -> None:
        # AC 3.6-7: a Verifies tag in a file outside trace.scan_globs is found only via --include.
        write(self.root, "tools/x/test_r113.py", "# Verifies: FC-OBS-R113\ndef test_r113():\n    pass\n")
        proc = run_cli(self.root, "trace", "--feature", "011", "--json")
        r113 = [r for r in json.loads(proc.stdout)["requirements"] if r["id"] == "FC-OBS-R113"][0]
        self.assertEqual(r113["verifies"], [])
        self.assertEqual(json.loads(proc.stdout)["outsideScan"], [])
        proc = run_cli(self.root, "trace", "--feature", "011", "--json", "--include", "tools/x/test_r113.py",
                       "--include", "src/app/log.py", "--include", str(self.root / "tools" / "x" / "test_r113.py"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        r113 = [r for r in data["requirements"] if r["id"] == "FC-OBS-R113"][0]
        self.assertEqual(r113["verifies"], ["tools/x/test_r113.py:1 (test_r113)"])
        # Only files that scan_globs does not already cover are outside the scan; no tag is counted twice.
        self.assertEqual(data["outsideScan"], ["tools/x/test_r113.py"])
        r111 = [r for r in data["requirements"] if r["id"] == "FC-OBS-R111"][0]
        self.assertEqual(len(r111["verifies"]), 2)


class SpecInfoTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = test_config()

    def test_marker_counting_ignores_comments_code_and_prose(self) -> None:
        text = SPEC.format(status="Draft", markers="- [NEEDS CLARIFICATION: which timer?]\n- [NEEDS CLARIFICATION: cause value?] -- affects R110\n")
        self.assertEqual(specs.count_markers(text), 2)
        self.assertEqual(specs.count_markers(SPEC.format(status="Draft", markers="")), 0)
        prose = 'Write "[NEEDS CLARIFICATION: ...]" for genuine ambiguity.\nUse the marker [NEEDS CLARIFICATION: only when needed.\n```\n[NEEDS CLARIFICATION: in a fence]\n```\n'
        self.assertEqual(specs.count_markers(prose), 0)
        self.assertEqual(specs.count_markers("Open: [NEEDS CLARIFICATION: real one]"), 1)

    def test_spec_info(self) -> None:
        root = make_tmp("info-")
        try:
            p = write(root, "spec.md", SPEC.format(status="Approved", markers="- [NEEDS CLARIFICATION: q]"))
            info = specs.spec_info(p, self.cfg)
            self.assertEqual(info["status"], "Approved")
            self.assertEqual(info["needsClarification"], 1)
            self.assertEqual(info["requirements"], 6)
            self.assertEqual(info["must"], 5)
            self.assertFalse(info["withinBudget"])
            self.assertEqual(specs.spec_info(root / "none.md", self.cfg)["status"], "Missing")
        finally:
            rm(root)


TASKS = """# Tasks

Feature: F011
Security-sensitive: yes
Tests commit (red check): 0123abcd

<!--
- [ ] T01 [P] <imperative description> — files: <path> — verifies: <ID> — depends: -
-->

## Wave 1

- [x] T01 Add types — files: crates/fc-types/src/id.rs — verifies: FC-OBS-R110 — depends: -
  - context: something
- [ ] T02 Parse message — files: crates/fc-nas/src/reg.rs, crates/fc-nas/src/lib.rs — verifies: TS24501-5.5.1.2.4-R110, TS24501-5.5.1.2.4-R111 — depends: -

## Wave 2

- [ ] T03 [P] AMF handler — files: crates/fc-amf/src/gmm.rs — verifies: TS24501-5.5.1.2.4-R110 — depends: T02
- [ ] T04 [P] Events — files: crates/fc-events/src/kinds.rs — verifies: FC-OBS-R110 — depends: T01
- [ ] T05 [P] Workspace manifest — files: Cargo.toml — verifies: FC-OBS-R111 — depends: -
- [ ] T06 [P] AMF tests helper — files: crates/fc-amf/src/test_util.rs — verifies: FC-OBS-R111 — depends: T03
"""


class TasksTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = test_config()

    def test_parse_and_lanes(self) -> None:
        plan = specs.parse_tasks(TASKS, self.cfg)
        self.assertTrue(plan["securitySensitive"])
        self.assertEqual(plan["testsSha"], "0123abcd")
        self.assertEqual([w["wave"] for w in plan["waves"]], [1, 2])
        w1 = plan["waves"][0]["tasks"]
        self.assertEqual([t["id"] for t in w1], ["T02"])  # T01 is done and skipped
        self.assertEqual(w1[0]["components"], ["fc-nas"])
        self.assertEqual(w1[0]["verifies"], ["TS24501-5.5.1.2.4-R110", "TS24501-5.5.1.2.4-R111"])
        self.assertEqual(w1[0]["depends"], [])
        w2 = plan["waves"][1]["tasks"]
        self.assertEqual([t["id"] for t in w2], ["T03", "T04", "T05", "T06"])
        self.assertEqual(w2[2]["components"], ["workspace"])
        self.assertEqual(w2[0]["depends"], ["T02"])
        lanes = specs.build_lanes(w2)
        self.assertEqual(len(lanes), 3)
        by_comp = {tuple(l["components"]): [t["id"] for t in l["tasks"]] for l in lanes}
        self.assertEqual(by_comp[("fc-amf",)], ["T03", "T06"])
        self.assertEqual(specs.build_lanes(w1)[0]["components"], ["*"])
        full = specs.parse_tasks(TASKS, self.cfg, include_done=True)
        self.assertEqual([t["id"] for t in full["waves"][0]["tasks"]], ["T01", "T02"])

    def test_template_placeholders_are_not_tasks(self) -> None:
        text = (
            "## Wave 1\n"
            "- [ ] T01 [P] <imperative description> — files: <path>, <path> — verifies: <ID> — depends: -\n"
            "- [ ] T02 [P] <description> — files: <path in component a> — verifies: <ID>, <ID> — depends: T01\n"
            "- [ ] T03 Parse Vec<u8> payloads — files: crates/fc-nas/src/lib.rs — verifies: FC-OBS-R110 — depends: -\n"
        )
        plan = specs.parse_tasks(text, self.cfg)
        self.assertEqual([t["id"] for t in plan["waves"][0]["tasks"]], ["T03"])
        self.assertEqual(specs.validate_tasks(plan, ["FC-OBS-R110"]), [])

    def test_validate(self) -> None:
        plan = specs.parse_tasks(TASKS, self.cfg, include_done=True)
        problems = specs.validate_tasks(plan, ["FC-OBS-R110", "FC-OBS-R111", "TS24501-5.5.1.2.4-R110", "TS24501-5.5.1.2.4-R111"])
        self.assertEqual(problems, ["T06 depends on T03 which is not in an earlier wave"])
        bad = specs.parse_tasks(TASKS.replace("crates/fc-events/src/kinds.rs", "crates/fc-amf/src/gmm.rs"), self.cfg)
        problems = specs.validate_tasks(bad, None)
        self.assertIn("[P] tasks T03 and T04 in wave 2 share file crates/fc-amf/src/gmm.rs", problems)


class NewFeatureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_tmp("newf-")
        self.cfg = test_config()
        write_config(self.root, self.cfg)
        write(self.root, "specs/templates/spec.md", "# {{FID}} — <slug>\n\nFeature: F<NNN>\nSlug: {{SLUG}}\nBranch: {{BRANCH}}\nIDs: {{ID_BLOCK}}\nStatus: Draft\n")
        write(self.root, "specs/templates/tasks.md", "Feature: {{FEATURE}} in {{PROJECT}}\n")
        write(self.root, "docs/ROADMAP.md", "### M1 — First\n\n#### F013 user-login\n")

    def tearDown(self) -> None:
        rm(self.root)

    def test_scaffold(self) -> None:
        dest, written = new_feature.create(self.root, self.cfg, "013", "user-login")
        self.assertEqual(sorted(written), ["specs/features/013-user-login/spec.md", "specs/features/013-user-login/tasks.md"])
        spec = (dest / "spec.md").read_text()
        self.assertIn("# F013 — user-login", spec)
        self.assertIn("Feature: F013", spec)
        self.assertIn("Branch: feat/F013-user-login", spec)
        self.assertIn("IDs: R130-R139", spec)
        self.assertIn("Feature: 013-user-login in my-project", (dest / "tasks.md").read_text())
        # Non-destructive on re-run; number collision refused.
        _, again = new_feature.create(self.root, self.cfg, "013", "user-login")
        self.assertEqual(again, [])
        with self.assertRaises(FileExistsError):
            new_feature.create(self.root, self.cfg, "013", "other-slug")
        with self.assertRaises(ValueError):
            new_feature.create(self.root, self.cfg, "13", "x")
        with self.assertRaises(ValueError):
            new_feature.create(self.root, self.cfg, "014", "Bad_Slug")
        self.assertTrue(new_feature.roadmap_has(self.root, self.cfg, "013", "user-login"))
        self.assertFalse(new_feature.roadmap_has(self.root, self.cfg, "014", "x"))

    def test_cli(self) -> None:
        proc = run_cli(self.root, "new-feature", "014", "payments")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("R140-R149", proc.stdout)
        self.assertIn("warning", proc.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
