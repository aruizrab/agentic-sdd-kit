"""Procedure step tables (trace.procedure_steps) and the call-flow conformance helper."""

from __future__ import annotations

import json
import re
import unittest

from helpers import make_tmp, rm, run_cli, test_config, write, write_config

from sddkit import callflow, config as cfgmod, procedures, trace
from sddkit.callflow import Expect, Skip

SOURCE = """# PROTO

## 4 Procedures

## 4.2 Login

The login procedure runs these steps.

1\\. Client to Gateway: Login Request.
2. Gateway to IdP: Token Request.
3a) IdP to Store: Lookup.
3b IdP to Gateway: Token.
4. Gateway to Client: Login Accept.

### 4.2.1 Variant

1. Not part of the own text of 4.2.
7. Neither is this.

## 4.3 Logout

1. Client to Gateway: Logout.

## 4.9 Notes

No numbered steps here.

## 6.1 Gateway

The Gateway:
- Terminates the client interface\\.
- Issues sessions.
"""

ROADMAP = "# Roadmap\n\n#### F011 login\n#### F013 idp\n#### F099 fixture\n"

SPEC = """# F099 — Fixture

Feature: F099
Status: {status}

## 4. Actors and interfaces

### Procedure steps

#### Step table: PROTO §4.2 Login

| Step | Description | Mapping |
|---|---|---|
| 1 | Client to Gateway: Login Request | FC-LOG-R990 |
| 2 | Gateway to IdP: Token Request | FC-LOG-R991; owned by F013: IdP side |
| 3a | IdP to Store | owned by F013: IdP side |
| 3b | IdP to Gateway | out of scope: fixture |
| 4 | Gateway to Client | FC-LOG-R990 |

#### Responsibilities

| Owner | Responsibility | Source | Requirements |
|---|---|---|---|
| Gateway | terminates the client interface. | PROTO §6.1 | FC-LOG-R990 |

No step table for PROTO §4.3: fixture

## 5. Requirements

- **FC-LOG-R990** [must] THE Gateway SHALL accept a login. (Source: PROTO §4.2)
- **FC-LOG-R991** [must] THE Gateway SHALL request a token. (Source: PROTO §4.2)
"""

PLAIN = "# F098 — Other\n\nStatus: {status}\n\n- **FC-OTH-R980** [may] THE X SHALL y. (Source: PROTO §4.3)\n{extra}"


class ProcedureStepsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_tmp("proc-")
        self.cfg = test_config()
        self.cfg["sources"]["text_dir"] = "docs/requirements"
        self.cfg["trace"]["procedure_steps"]["enabled"] = True
        write_config(self.root, self.cfg)
        write(self.root, "docs/requirements/proto.md", SOURCE)
        write(self.root, "docs/ROADMAP.md", ROADMAP)
        write(self.root, "src/app/test_login.py", "# Verifies: FC-LOG-R990\ndef test_login():\n    pass\n")
        self.spec(SPEC.format(status="Approved"))

    def tearDown(self) -> None:
        rm(self.root)

    def spec(self, text: str, name: str = "099-fixture") -> None:
        write(self.root, f"specs/features/{name}/spec.md", text)

    def edit(self, old: str, new: str, status: str = "Approved") -> None:
        base = SPEC.format(status=status)
        self.assertIn(old, base)
        self.spec(base.replace(old, new))

    def evaluate(self, feature=None) -> procedures.Result:
        return procedures.evaluate(self.root, self.cfg, trace.load(self.root, self.cfg), feature)

    def assertFails(self, *fragments: str) -> None:
        errors = self.evaluate().errors
        for frag in fragments:
            self.assertTrue(any(frag in e for e in errors), f"{frag!r} not in {errors}")

    def test_disabled_by_default(self) -> None:
        self.assertFalse(cfgmod.DEFAULTS["trace"]["procedure_steps"]["enabled"])
        self.cfg["trace"]["procedure_steps"]["enabled"] = False
        write_config(self.root, self.cfg)
        self.spec(SPEC.format(status="Approved").replace("| 3b | IdP to Gateway | out of scope: fixture |\n", ""))
        self.spec(PLAIN.format(status="Approved", extra=""), "098-other")
        proc = run_cli(self.root, "trace", "--check")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotIn("Procedure step coverage", proc.stdout)

    def test_passing_fixture_and_coverage(self) -> None:
        res = self.evaluate()
        self.assertEqual(res.errors, [])
        self.assertEqual(res.warnings, [])
        self.assertEqual(res.coverage, [{
            "feature": "099-fixture", "status": "Approved",
            "stepTables": [{"procedure": "PROTO §4.2", "title": "Login", "steps": 5, "mapped": 3,
                            "ownedByOtherFeatures": 1, "outOfScope": 1, "verified": 2}],
            "exemptions": [{"procedure": "PROTO §4.3", "reason": "fixture"}],
            "featureWideReasons": [],
        }])
        md = procedures.to_markdown(res.coverage)
        self.assertIn("| 099-fixture (Approved) | PROTO §4.2 | 5 | 3 | 1 | 1 | 2 |", md)
        self.assertIn("- 099-fixture: No step table for PROTO §4.3: fixture", md)

    def test_unmapped_unknown_and_invented_steps(self) -> None:
        self.edit("| 3b | IdP to Gateway | out of scope: fixture |\n", "")
        self.assertFails("F099: step table PROTO §4.2: step 3b has no row")
        self.edit("out of scope: fixture |", "see step 2 |")
        self.assertFails("step table PROTO §4.2 step 3b is not mapped")
        self.edit("out of scope: fixture |", "out of scope: |")
        self.assertFails("step 3b: empty reason in `out of scope`")
        self.edit("out of scope: fixture |", "FC-LOG-R997 |")
        self.assertFails("step 3b: unknown requirement FC-LOG-R997")
        self.edit("| 4 | Gateway", "| 5 | extra | FC-LOG-R990 |\n| 4 | Gateway")
        self.assertFails("step 5 is not a step of the section")
        # Labels are canonicalised: a range written with "to" is the same row as "3a-3b".
        self.edit("| 3a | IdP to Store |", "| 3a to 3c | IdP to Store |")
        self.assertFails("step 3a-3c is not a step of the section", "step 3a has no row")

    def test_malformed_tables(self) -> None:
        table_start = "| Step | Description | Mapping |"
        self.edit(table_start, "Prose only.\n\n#### Next\n\n" + table_start)
        self.assertFails("step table PROTO §4.2: no table follows the step-table heading")
        self.edit(table_start + "\n|---|---|---|", "| Step | Mapping |\n|---|---|")
        self.assertFails("the table header is not | Step | Description | Mapping |")
        self.edit("| 4 | Gateway to Client | FC-LOG-R990 |", "| 4 | Gateway to Client | FC-LOG-R990 |\n| 3a | again | FC-LOG-R990 |")
        self.assertFails("step 3a has two rows")
        self.edit("#### Responsibilities", "#### Step table: PROTO §4.2\n\n| Step | Description | Mapping |\n|---|---|---|\n| 1 | x | FC-LOG-R990 |\n\n#### Responsibilities")
        self.assertFails("a second step table for PROTO §4.2")
        self.edit("Step table: PROTO §4.2 Login", "Step table: PROTO §4.99 Missing")
        self.assertFails("step table PROTO §4.99: section not found in the normative source")
        self.edit("Step table: PROTO §4.2 Login", "Step table: PROTO §4.9 Notes")
        self.assertFails("step table PROTO §4.9: the section's own text has no step label")
        self.edit("| 1 | Client to Gateway: Login Request | FC-LOG-R990 |", "| 1 | only two cells |")
        self.assertFails("a row needs 3 cells, has 2")

    def test_fenced_and_commented_structures_are_ignored(self) -> None:
        start = SPEC.index("#### Step table")
        end = SPEC.index("No step table for")
        fenced = SPEC[:start] + "```markdown\n" + SPEC[start:end] + "```\n<!-- No step table: hidden -->\n" + SPEC[end:]
        p = procedures.parse(fenced.format(status="Approved"))
        self.assertEqual(p.tables, [])
        self.assertEqual(p.resp_headings, 0)
        self.assertEqual(p.reasons, [])
        self.assertEqual([k for k, _, _ in p.exemptions], ["PROTO §4.3"])

    def test_owned_by(self) -> None:
        self.edit("owned by F013: IdP side |\n| 3b", "owned by F099: self |\n| 3b")
        self.assertFails("step 3a: `owned by F099` names this spec's own feature")
        self.edit("owned by F013: IdP side |\n| 3b", "owned by F777: x |\n| 3b")
        self.assertFails("step 3a: `owned by F777` names a feature without a roadmap entry")
        self.edit("owned by F013: IdP side |\n| 3b", "owned by F011: |\n| 3b")
        self.assertFails("step 3a: empty reason in `owned by F011`")

    def test_responsibilities(self) -> None:
        resp = "#### Responsibilities\n\n| Owner | Responsibility | Source | Requirements |\n|---|---|---|---|\n| Gateway | terminates the client interface. | PROTO §6.1 | FC-LOG-R990 |\n"
        self.edit(resp, "")
        self.assertFails("has a step table but no Responsibilities table")
        self.edit("| terminates the client interface. |", "| Terminates   the CLIENT interface |")
        self.assertEqual(self.evaluate().errors, [])  # case, white space, escapes and the full stop are normalised
        self.edit("| terminates the client interface. |", "| Stores tokens |")
        self.assertFails("Responsibilities Gateway: the Responsibility is not quoted from PROTO §6.1")
        self.edit("| PROTO §6.1 |", "| PROTO §6.9 |")
        self.assertFails("Responsibilities Gateway: PROTO §6.9 not found in the normative source")
        self.edit("| PROTO §6.1 | FC-LOG-R990 |", "| PROTO §6.1 | — |")
        self.assertFails("Responsibilities Gateway: the Requirements cell is not accounted for")
        self.edit("| Owner | Responsibility | Source | Requirements |", "| Owner | Responsibility | Requirements |")
        self.assertFails("the Responsibilities header is not")

    def test_entry_required_and_reasons(self) -> None:
        self.spec(PLAIN.format(status="Approved", extra=""), "098-other")
        self.assertFails("098-other/spec.md:1: F098: no procedure-step entry")
        self.spec(PLAIN.format(status="Approved", extra="\n- No step table: runs no procedure of PROTO\n"), "098-other")
        res = self.evaluate()
        self.assertEqual(res.errors, [])
        self.assertIn({"feature": "098-other", "status": "Approved", "stepTables": [], "exemptions": [],
                       "featureWideReasons": ["runs no procedure of PROTO"]}, res.coverage)
        # A reason keeps everything after the colon, `;` included (the reference project cut it at the first `;`).
        self.spec(PLAIN.format(status="Approved", extra="\nNo step table: parsers only; no procedure of PROTO applies; see D-3\n"), "098-other")
        self.edit("No step table for PROTO §4.3: fixture", "No step table for PROTO §4.3: fixture; owned by the next release; see D-4")
        res = self.evaluate()
        self.assertEqual(res.errors, [])
        cov = {c["feature"]: c for c in res.coverage}
        self.assertEqual(cov["098-other"]["featureWideReasons"], ["parsers only; no procedure of PROTO applies; see D-3"])
        self.assertEqual(cov["099-fixture"]["exemptions"], [{"procedure": "PROTO §4.3", "reason": "fixture; owned by the next release; see D-4"}])
        self.spec(PLAIN.format(status="Approved", extra="\nNo step table:\n"), "098-other")
        self.assertFails("F098: empty reason in `No step table:`")
        self.edit("No step table for PROTO §4.3: fixture", "No step table for PROTO §4.3:")
        self.assertFails("F099: empty reason in `No step table for PROTO §4.3:`")

    def test_draft_specs_warn(self) -> None:
        self.edit("| 3b | IdP to Gateway | out of scope: fixture |\n", "", status="Draft")
        self.spec(PLAIN.format(status="Draft", extra=""), "098-other")
        res = self.evaluate()
        self.assertEqual(res.errors, [])
        self.assertTrue(any("step 3b has no row" in w and "Draft spec; fails once Approved or Implemented" in w for w in res.warnings), res.warnings)
        self.assertTrue(any("F098: no procedure-step entry" in w for w in res.warnings), res.warnings)
        proc = run_cli(self.root, "trace", "--check")
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("warning: specs/features/099-fixture/spec.md", proc.stdout)
        self.assertIn("step 3b has no row", proc.stdout)

    def test_lookalikes_warn(self) -> None:
        self.edit("No step table for PROTO §4.3: fixture", "No step-table for PROTO §4.3 (fixture)\n\n### Step Table PROTO §4.3")
        res = self.evaluate()
        self.assertEqual(len([w for w in res.warnings if "looks like" in w]), 2, res.warnings)

    def test_source_check_off(self) -> None:
        self.cfg["trace"]["procedure_steps"]["source_check"] = False
        self.edit("| 3b | IdP to Gateway | out of scope: fixture |\n", "| 9z | invented | out of scope: fixture |\n")
        res = self.evaluate()
        self.assertEqual(res.errors, [])
        self.assertEqual(res.coverage[0]["stepTables"][0]["steps"], 5)
        self.edit("| FC-LOG-R990 |\n| 2 |", "| see below |\n| 2 |")
        self.assertFails("step 1 is not mapped")

    def test_custom_label_regex(self) -> None:
        write(self.root, "docs/requirements/proto.md", SOURCE.replace("1\\. Client", "S1: Client").replace("2. Gateway to IdP", "S2: Gateway to IdP"))
        self.cfg["trace"]["procedure_steps"]["label_regex"] = r"^S(?P<label>\d+):"
        self.assertFalse([p for p in cfgmod.validate(self.cfg) if "procedure_steps" in p])
        res = self.evaluate()
        self.assertTrue(any("step 3a is not a step of the section" in e for e in res.errors), res.errors)  # only S1, S2 are labels now
        self.assertFalse(any("step 1 " in e or "step 2 " in e for e in res.errors), res.errors)
        self.cfg["trace"]["procedure_steps"]["label_regex"] = r"^\d+\."
        self.assertIn("trace.procedure_steps.label_regex needs the named group (?P<label>...)", cfgmod.validate(self.cfg))

    def test_cli_json_and_feature_filter(self) -> None:
        self.spec(PLAIN.format(status="Approved", extra="\nNo step table: none applies\n"), "098-other")
        proc = run_cli(self.root, "trace", "--json", "--check")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual([f["feature"] for f in data["procedures"]], ["098-other", "099-fixture"])
        self.assertEqual(data["warnings"], [])
        proc = run_cli(self.root, "trace", "--feature", "098")
        self.assertIn("- 098-other: No step table: none applies", proc.stdout)
        self.assertNotIn("099-fixture", proc.stdout)
        self.edit("| 3b | IdP to Gateway | out of scope: fixture |\n", "")
        proc = run_cli(self.root, "trace", "--check")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("error: specs/features/099-fixture/spec.md:", proc.stdout)
        self.assertEqual(run_cli(self.root, "trace", "--check", "--feature", "098").returncode, 0)

    def test_label_grammar(self) -> None:
        rx = re.compile(procedures.DEFAULT_LABEL_REGEX)
        forms = {
            "1\\. Client to Gateway": "1", "9a. If": "9a", "9b If": "9b", "6a \\[Conditional\\] x": "6a",
            "2a1. When": "2a1", "7(A). If": "7(A)", "3(A) The": "3(A)", "3a) Gateway": "3a", "3c: The": "3c",
            "10.1. Gateway": "10.1", "Step 25: Data": "25", "14a-c. If": "14a-c", "13-20. Skipped.": "13-20",
            "2d-a. If": "2d-a", "4a - 4b. x": "4a-4b", "14.-15. If": "14-15", "2a. - 2c. x": "2a-2c",
            "2a to 2c. x": "2a-2c", "1a and 1b. If": "1a-1b", "20a. Void.": "20a",
        }
        for line, label in forms.items():
            with self.subTest(line=line):
                m = rx.match(line)
                self.assertIsNotNone(m)
                self.assertEqual(procedures.canonical(m.group("label")), label)
        for line in ("2026-01-01 release", "The step", "- 1. item", "  1. nested"):
            self.assertIsNone(rx.match(line), line)


FLOW_SPEC = """#### Step table: PROTO §4.2 Login

| Step | Description | Mapping |
|---|---|---|
| 1 | Client to Gateway | FC-LOG-R990 |
| 2 | Gateway to IdP | FC-LOG-R991 |
| 3a | IdP to Store | out of scope: x |
| 3b | IdP to Gateway | out of scope: x |
| 4 | Gateway to Client | FC-LOG-R990 |
| 5 | Audit | out of scope: x |
"""


def msg(src: str, dst: str):
    return {"kind": "msg", "src": src, "dst": dst}


class CallFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.expect = [
            Expect("1", msg("client", "gw")),
            Expect("2", msg("gw", "idp")),
            Expect("3a", msg("idp", "store")),
            Expect("3b", lambda e: e.get("src") == "idp" and e.get("dst") == "gw"),
            Expect("4", msg("gw", "client")),
        ]
        self.skips = [Skip("5", "audit is not recorded")]
        self.events = [
            {"seq": i, "kind": "msg", "src": s, "dst": d, "wall_time": 1000 - i}
            for i, (s, d) in enumerate([("client", "gw"), ("gw", "idp"), ("idp", "store"), ("idp", "gw"), ("gw", "client")], start=1)
        ]

    def check(self, **kw):
        return callflow.check(kw.get("spec", FLOW_SPEC), kw.get("procedure", "PROTO §4.2"), kw.get("expect", self.expect),
                              kw.get("skips", self.skips), kw.get("events", self.events))

    def test_in_order_passes(self) -> None:
        self.assertEqual(self.check(), [])
        callflow.assert_call_flow(FLOW_SPEC, "PROTO  §4.2", self.expect, self.skips, self.events)

    def test_swapped_steps_and_missing_step(self) -> None:
        swapped = [dict(e) for e in self.events]
        swapped[1]["seq"], swapped[2]["seq"] = 3, 2
        self.assertEqual(self.check(events=swapped), ["PROTO §4.2: step 3a out of order: it occurs before step 2"])
        self.assertEqual(self.check(events=self.events[:4]), ["PROTO §4.2: step 4 missing"])
        with self.assertRaises(AssertionError) as ctx:
            callflow.assert_call_flow(FLOW_SPEC, "PROTO §4.2", self.expect, self.skips, self.events[:4])
        self.assertIn("step 4 missing", str(ctx.exception))
        self.assertEqual(self.check(events=[]), [f"PROTO §4.2: step {s} missing" for s in ("1", "2", "3a", "3b", "4")])

    def test_unordered_expectation(self) -> None:
        expect = self.expect[:4] + [Expect("4", msg("gw", "client"), ordered=False)]
        moved = [dict(self.events[4], seq=0)] + self.events[:4]
        self.assertEqual(self.check(expect=expect, events=moved), [])
        self.assertEqual(self.check(expect=expect, events=self.events[:4]), ["PROTO §4.2: step 4 missing"])

    def test_accounting_and_table_errors(self) -> None:
        self.assertEqual(self.check(procedure="PROTO §4.3"), ["PROTO §4.3: the spec has no step table for this procedure"])
        bad = FLOW_SPEC.replace("| Step | Description | Mapping |\n|---|---|---|", "| Step | Mapping |\n|---|---|")
        self.assertTrue(self.check(spec=bad)[0].startswith("PROTO §4.2: step table defect: the table header"))
        self.assertEqual(self.check(skips=[]), ["PROTO §4.2: step 5 has neither an expectation nor a skip"])
        self.assertEqual(self.check(expect=self.expect + [Expect("9", {})]), ["PROTO §4.2: step 9 is not in the step table"])
        self.assertEqual(self.check(skips=[Skip("5", " ")]), ["PROTO §4.2: the skip of step 5 has no reason"])
        self.assertEqual(self.check(expect=self.expect + [Expect("1", {})]), ["PROTO §4.2: step 1 has more than one expectation or skip"])

    def test_deterministic(self) -> None:
        swapped = [dict(e) for e in self.events]
        swapped[1]["seq"], swapped[2]["seq"] = 3, 2
        first = self.check(events=swapped)
        self.assertEqual(self.check(events=swapped), first)
        self.assertEqual(self.check(events=[dict(e, wall_time=0, id="x") for e in swapped]), first)
        self.assertEqual(self.check(events=list(reversed(swapped))), first)

    def test_cli(self) -> None:
        root = make_tmp("callflow-")
        try:
            write(root, "spec.md", FLOW_SPEC)
            write(root, "expect.json", json.dumps({
                "expect": [{"step": e.step, "match": e.match} for e in self.expect if isinstance(e.match, dict)]
                + [{"step": "3b", "match": msg("idp", "gw"), "ordered": True}],
                "skip": [{"step": "5", "reason": "audit is not recorded"}],
            }))
            write(root, "events.jsonl", "\n".join(json.dumps(e) for e in self.events) + "\n")
            proc = run_cli(root, "callflow", str(root / "spec.md"), "--procedure", "PROTO §4.2",
                           "--expect", str(root / "expect.json"), "--events", str(root / "events.jsonl"))
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            write(root, "events.json", json.dumps(self.events[:4]))
            proc = run_cli(root, "callflow", str(root / "spec.md"), "--procedure", "PROTO §4.2",
                           "--expect", str(root / "expect.json"), "--events", str(root / "events.json"), "--json")
            self.assertEqual(proc.returncode, 1)
            self.assertEqual(json.loads(proc.stdout), {"ok": False, "violations": ["PROTO §4.2: step 4 missing"]})
            write(root, "events.json", json.dumps([{"kind": "msg"}]))
            proc = run_cli(root, "callflow", str(root / "spec.md"), "--procedure", "PROTO §4.2",
                           "--expect", str(root / "expect.json"), "--events", str(root / "events.json"))
            self.assertEqual(proc.returncode, 2)
        finally:
            rm(root)


if __name__ == "__main__":
    unittest.main()
