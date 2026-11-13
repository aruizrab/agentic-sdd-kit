"""WI1 AC 9: the changed-lines coverage gate (lcov and Cobertura)."""

from __future__ import annotations

import json
import os
import time
import unittest

from helpers import GitRepo, make_tmp, rm, run_cli, test_config, write, write_config

BASE_PY = "def a():\n    return 1\n"
EDIT_PY = (
    "def a():\n"                                        # 1
    "    return 1\n"                                    # 2
    "\n"                                                # 3
    "\n"                                                # 4
    "def b(x):\n"                                       # 5
    "    if x:\n"                                       # 6
    "        return 2\n"                                # 7  uncovered
    "    raise SystemExit  # cov:unreachable: guarded by caller\n"  # 8 uncovered, marked
    "    # cov:unreachable: platform branch\n"           # 9
    "    return 3\n"                                    # 10 uncovered, marked above
    "    return 4  # cov:unreachable:\n"                # 11 marker without reason
)
LCOV = """TN:
SF:{root}/src/app/mod.py
DA:1,1
DA:2,1
DA:5,1
DA:6,1
DA:7,0
DA:8,0
DA:10,0
DA:11,0
end_of_record
SF:src/lib/core.rs
DA:2,0
DA:7,0
DA:9,0
end_of_record
SF:src/gen/out.py
DA:1,0
end_of_record
"""


class CoverageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_tmp("cov-")
        self.repo = GitRepo(self.root)
        self.repo.init()
        self.cfg = test_config()
        self.cfg["quality"]["generated"] = ["src/gen/**"]
        self.cfg["quality"]["coverage"].update({"command": "cp cov/lcov.fixture lcov.info", "report": "lcov.info", "format": "lcov"})
        write_config(self.root, self.cfg)
        write(self.root, ".gitignore", "lcov.info\ncoverage.xml\n.sdd/\n")
        write(self.root, "src/app/mod.py", BASE_PY)
        write(self.root, "src/lib/core.rs", "pub fn f() -> u8 {\n    1\n}\n")
        write(self.root, "src/gen/out.py", "x = 1\n")
        write(self.root, "cov/lcov.fixture", "")
        self.repo.commit("base")
        self.repo.git("switch", "-q", "-c", "feat/F011-cov")
        # Uncommitted edits count (working tree vs merge-base).
        write(self.root, "src/app/mod.py", EDIT_PY)
        write(self.root, "src/lib/core.rs",
              "pub fn f() -> u8 {\n    2\n}\n\n#[cfg(test)]\nmod tests {\n    #[test]\n    fn t() {\n        assert_eq!(super::f(), 2);\n    }\n}\n")
        write(self.root, "src/gen/out.py", "x = 2\n")
        write(self.root, "tests/test_mod.py", "def test_x():\n    assert True\n")

    def tearDown(self) -> None:
        rm(self.root)

    def check(self, *args, env=None):
        proc = run_cli(self.root, "check", "coverage", "--json", *args, env=env)
        try:
            return proc.returncode, json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(proc.stdout + proc.stderr)

    def test_lcov_changed_lines(self) -> None:
        write(self.root, "cov/lcov.fixture", LCOV.format(root=self.root))
        self.repo.git("add", "tests/test_mod.py")
        code, out = self.check()
        self.assertEqual(code, 1, out)
        got = sorted((v["kind"], v["file"], v["line"]) for v in out["violations"])
        self.assertEqual(got, [
            ("coverage", "src/app/mod.py", 7),
            ("coverage", "src/lib/core.rs", 2),
            ("coverage-marker", "src/app/mod.py", 11),
        ])  # unchanged lines, uninstrumented lines, #[cfg(test)] region, tests/ and generated files do not fail
        self.assertEqual(sorted((m["line"], m["reason"]) for m in out["markers"]),
                         [(8, "guarded by caller"), (10, "platform branch"), (11, "")])
        human = run_cli(self.root, "check", "coverage")
        self.assertIn("3 cov:unreachable marker(s)", human.stdout)
        self.assertIn("src/app/mod.py:8: guarded by caller", human.stdout)

    def test_cobertura(self) -> None:
        xml = (f'<?xml version="1.0"?>\n<coverage><sources><source>{self.root}/src</source></sources><packages><package><classes>'
               '<class filename="app/mod.py"><lines><line number="5" hits="1"/><line number="7" hits="0"/><line number="8" hits="0"/>'
               '<line number="10" hits="0"/><line number="11" hits="0"/></lines></class>'
               '<class filename="lib/core.rs"><lines><line number="2" hits="3"/></lines></class>'
               '</classes></package></packages></coverage>\n')
        write(self.root, "cov/cobertura.fixture", xml)
        self.cfg["quality"]["coverage"].update({"command": "cp cov/cobertura.fixture coverage.xml", "report": "coverage.xml", "format": "cobertura"})
        write_config(self.root, self.cfg)
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertEqual(sorted((v["kind"], v["file"], v["line"]) for v in out["violations"]),
                         [("coverage", "src/app/mod.py", 7), ("coverage-marker", "src/app/mod.py", 11)])

    def test_unmeasured_files(self) -> None:
        write(self.root, "cov/lcov.fixture", "SF:src/app/mod.py\nDA:1,1\nend_of_record\n")
        write(self.root, "src/app/new.py", "VALUE = 1\n")
        write(self.root, "src/web/new.ts", "export const x = 1;\n")
        write(self.root, "src/web/types.d.ts", "export type T = number;\n")
        write(self.root, "src/web/ok.ts", "export const y = 2;\n")
        write(self.root, "src/lib/decl.rs", "pub mod a;\npub use a::B;\n")
        self.repo.git("add", "-A")
        self.cfg["quality"]["coverage"]["unmeasured_ok"] = ["src/web/ok.ts"]
        write_config(self.root, self.cfg)
        code, out = self.check()
        unmeasured = sorted(v["file"] for v in out["violations"] if v["kind"] == "coverage-unmeasured")
        self.assertEqual(unmeasured, ["src/app/new.py", "src/lib/core.rs", "src/web/new.ts"])
        self.assertEqual(code, 1)

    def test_kit_upgrade_is_not_project_code(self) -> None:
        """C2: a changed file of the installed kit (<paths.tools_dir>/**) is neither measured nor unmeasured."""
        write(self.root, "cov/lcov.fixture", LCOV.format(root=self.root))
        self.repo.git("add", "-A")
        self.repo.commit("feature edits")
        self.repo.git("switch", "-q", "main")
        self.repo.git("switch", "-q", "-c", "chore/kit-upgrade")
        write(self.root, "tools/sdd/sddkit/util.py", "def added():\n    return 1\n")
        write(self.root, "tools/sdd/pipeline/feature-pipeline.js", "export function f() {\n  return 1\n}\n")
        self.repo.git("add", "-A")
        code, out = self.check()
        self.assertEqual(code, 0, out)
        self.assertEqual(out["violations"], [])
        self.assertIn("no changed hand-written lines", out["notes"])

    def test_tool_errors_and_stale_reports(self) -> None:
        self.cfg["quality"]["coverage"].update({"command": "echo boom; exit 4", "ok_exits": [0]})
        write_config(self.root, self.cfg)
        code, out = self.check()
        self.assertEqual(code, 2)
        self.assertIn("exited 4", out["notes"][0])
        self.assertIn("boom", out["notes"][0])
        # A report left over from an earlier run is deleted before the command runs.
        write(self.root, "lcov.info", "SF:src/app/mod.py\nDA:1,1\nend_of_record\n")
        self.cfg["quality"]["coverage"].update({"command": "true", "ok_exits": [0]})
        write_config(self.root, self.cfg)
        code, out = self.check()
        self.assertEqual(code, 2)
        self.assertIn("wrote no report", out["notes"][0])
        self.assertFalse((self.root / "lcov.info").exists())
        # --no-run: a report older than a considered file is stale.
        write(self.root, "lcov.info", "SF:src/app/mod.py\nDA:1,1\nend_of_record\n")
        old = time.time() - 3600
        os.utime(self.root / "lcov.info", (old, old))
        code, out = self.check("--no-run")
        self.assertEqual(code, 2)
        self.assertIn("stale", out["notes"][0])
        os.utime(self.root / "lcov.info", None)
        code, out = self.check("--no-run")
        self.assertNotEqual(code, 2, out)

    def test_no_merge_base_and_not_configured(self) -> None:
        code, out = self.check(env={"SDD_BASE_REF": "does-not-exist"})
        self.assertEqual(code, 2)
        self.assertIn("does-not-exist", out["notes"][0])
        self.cfg["quality"]["coverage"].update({"command": "", "report": ""})
        write_config(self.root, self.cfg)
        code, out = self.check()
        self.assertEqual(code, 2)
        self.assertIn("not configured", out["notes"][0])

    def test_missing_tool_skips_and_fails_in_ci(self) -> None:
        self.cfg["quality"]["coverage"].update({"requires": ["definitely-not-a-tool-xyz"], "install": "pip install xyz"})
        write_config(self.root, self.cfg)
        code, out = self.check(env={"CI": ""})
        self.assertEqual(code, 3)
        self.assertIn("definitely-not-a-tool-xyz not installed (install: pip install xyz)", out["notes"][0])
        code, out = self.check(env={"CI": "true"})
        self.assertEqual(code, 1)


MUT_SRC = "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"


class MutationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_tmp("mut-")
        self.repo = GitRepo(self.root)
        self.repo.init()
        self.cfg = test_config()
        self.cfg["quality"]["mutation"].update({
            "command": "cat mut/out.txt",
            "survivor_regex": r"(?m)^SURVIVED (?P<file>[^:\s]+)(?::(?P<line>\d+))?: (?P<mutation>.+)$",
            "ok_exits": [0, 2, 3],
        })
        write_config(self.root, self.cfg)
        write(self.root, ".gitignore", ".sdd/\nmutants.out/\n")
        write(self.root, "src/calc.py", "def add(a, b):\n    return a + b\n")
        write(self.root, "mut/out.txt", "")
        write(self.root, "specs/features/011-calc/spec.md", "# F011\n")
        self.repo.commit("base")
        self.repo.git("switch", "-q", "-c", "feat/F011-calc")

    def tearDown(self) -> None:
        rm(self.root)

    def check(self, env=None):
        proc = run_cli(self.root, "check", "mutation", "--json", env=env)
        return proc.returncode, json.loads(proc.stdout)

    def test_no_changes_does_not_run(self) -> None:
        self.cfg["quality"]["mutation"]["command"] = "touch ran.marker"
        write_config(self.root, self.cfg)
        code, out = self.check()
        self.assertEqual(code, 0, out)
        self.assertFalse((self.root / "ran.marker").exists())

    def test_regex_survivors_and_justification(self) -> None:
        write(self.root, "src/calc.py", MUT_SRC)
        write(self.root, "mut/out.txt",
              "SURVIVED src/calc.py:6: replace - with +\n"     # changed line: fails
              "SURVIVED src/calc.py:2: replace + with -\n"     # unchanged line: ignored
              "SURVIVED src/calc.py: delete function body\n")  # no line: fails unless justified
        self.cfg["quality"]["mutation"]["command"] = "cat mut/out.txt; echo 'diff '{diff_file}; echo 'files '{changed_files} > mut/args.txt"
        write_config(self.root, self.cfg)
        code, out = self.check()
        self.assertEqual(code, 1, out)
        self.assertEqual(sorted((v["file"], v["line"] or 0, v["key"]["message"]) for v in out["violations"]),
                         [("src/calc.py", 0, "delete function body"), ("src/calc.py", 6, "replace - with +")])
        args = (self.root / "mut" / "args.txt").read_text()
        self.assertIn("files src/calc.py", args)
        self.assertTrue((self.root / ".sdd" / "logs" / "quality" / "diff.patch").is_file())
        self.assertIn("+def sub(a, b):", (self.root / ".sdd" / "logs" / "quality" / "diff.patch").read_text())
        write(self.root, "specs/features/011-calc/review.md",
              "## 6. Justifications\n\n- mutant: src/calc.py:6: replace - with + — \n"
              "- mutant: src/calc.py: delete function body — equivalent: body is re-exported\n")
        code, out = self.check()
        self.assertEqual([(v["line"]) for v in out["violations"]], [6])  # empty reason does not justify
        write(self.root, "specs/features/011-calc/review.md",
              "- mutant: src/calc.py:6: replace - with + — equivalent for unsigned inputs\n"
              "- mutant: src/calc.py: delete function body — equivalent: body is re-exported\n")
        code, out = self.check()
        self.assertEqual(code, 0, out)
        # The current feature comes from SDD_FEATURE before the branch name.
        code, out = self.check(env={"SDD_FEATURE": "F012"})
        self.assertEqual(code, 1)

    def test_mutation_testing_report(self) -> None:
        write(self.root, "src/calc.py", MUT_SRC)
        report = {"schemaVersion": "1", "files": {"src/calc.py": {"language": "python", "source": "", "mutants": [
            {"id": "1", "mutatorName": "ArithmeticOperator", "replacement": "a + b", "status": "Survived", "location": {"start": {"line": 6, "column": 12}}},
            {"id": "2", "mutatorName": "ArithmeticOperator", "replacement": "a - b", "status": "Survived", "location": {"start": {"line": 2, "column": 12}}},
            {"id": "3", "mutatorName": "BlockStatement", "status": "Killed", "location": {"start": {"line": 5, "column": 1}}},
            {"id": "4", "mutatorName": "BlockStatement", "status": "Timeout", "location": {"start": {"line": 5, "column": 1}}},
        ]}}}
        write(self.root, "mut/report.fixture", json.dumps(report))
        self.cfg["quality"]["mutation"].update({"command": "mkdir -p reports && cp mut/report.fixture reports/mutation.json",
                                                "report": "reports/mutation.json", "format": "mutation-testing-report"})
        write_config(self.root, self.cfg)
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertEqual([(v["line"], v["key"]["message"]) for v in out["violations"]], [(6, "ArithmeticOperator: a + b")])

    def test_tool_failure_is_an_error_not_a_pass(self) -> None:
        write(self.root, "src/calc.py", MUT_SRC)
        self.cfg["quality"]["mutation"]["command"] = "echo 'baseline test failed'; exit 4"
        write_config(self.root, self.cfg)
        code, out = self.check()
        self.assertEqual(code, 2)
        self.assertIn("baseline test failed", out["notes"][0])
        # A stale report from an earlier run is deleted before the command runs.
        write(self.root, "reports/mutation.json", json.dumps({"files": {}}))
        self.cfg["quality"]["mutation"].update({"command": "true", "report": "reports/mutation.json", "format": "mutation-testing-report"})
        write_config(self.root, self.cfg)
        code, out = self.check()
        self.assertEqual(code, 2)
        self.assertFalse((self.root / "reports" / "mutation.json").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
