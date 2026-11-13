"""WI1 AC 8: dependency extraction per language and the arch check."""

from __future__ import annotations

import json
import unittest

from helpers import GitRepo, make_tmp, rm, run_cli, test_config, write, write_config

from sddkit.quality import langs


def deps(lang: str, path: str, text: str, files=(), name_ok=lambda p: False):
    src = langs.lex(text, langs.LANGS[lang])
    return [(d.spec, d.line, d.relative) for d in langs.extract_deps(src, path, files, name_ok)]


class ExtractTest(unittest.TestCase):
    def test_rust_forms(self) -> None:
        text = (
            "use crate::domain::model;\n"                       # 1
            "use crate::infra::{db, cache::Store};\n"          # 2
            "pub(crate) use super::sibling as s;\n"            # 3
            "extern crate serde;\n"                            # 4
            "// use crate::infra::commented;\n"                # 5
            "fn f() {\n"                                       # 6
            "    crate::infra::db::connect();\n"               # 7
            "    let s = \"crate::infra::in_string\";\n"       # 8
            "    my_infra::run();\n"                           # 9
            "    String::new();\n"                             # 10
            "}\n"
        )
        got = deps("rust", "src/domain/svc.rs", text, ["src/lib.rs", "src/domain/svc.rs"], lambda p: p.startswith("my_infra::"))
        self.assertEqual(got, [
            ("crate::domain::model", 1, False), ("crate::infra::db", 2, False), ("crate::infra::cache::Store", 2, False),
            ("crate::domain::sibling", 3, False), ("serde", 4, False),
            ("crate::infra::db::connect", 7, False), ("my_infra::run", 9, False),
        ])

    def test_rust_super_from_module_path(self) -> None:
        text = "fn f() {\n    super::super::infra::x();\n}\n"
        files = ["src/lib.rs", "src/domain/model/mod.rs"]
        self.assertEqual(deps("rust", "src/domain/model/mod.rs", text, files), [("crate::infra::x", 2, False)])
        self.assertEqual(deps("rust", "src/domain/model.rs", text, ["src/lib.rs"]), [("crate::infra::x", 2, False)])

    def test_python_forms(self) -> None:
        text = (
            "import os, app.infra.db as dbm\n"     # 1
            "from app.domain import Thing\n"       # 2
            "from . import sibling\n"              # 3
            "from ..infra.db import (\n"           # 4
            "    connect,\n"
            "    close,\n"
            ")\n"
            "import app\n"                         # 8
            "# import app.commented\n"             # 9
            "def f():\n"                           # 10
            "    return app.infra.x(dbm.q)\n"      # 11
        )
        got = deps("python", "src/app/domain/svc.py", text)
        self.assertEqual(got, [
            ("os", 1, False), ("app.infra.db", 1, False), ("app", 8, False),
            ("app.domain.Thing", 2, False), ("src/app/domain/sibling", 3, True), ("src/app/infra/db", 4, True),
            ("app.infra.x", 11, False), ("app.infra.db.q", 11, False),
        ])

    def test_typescript_forms(self) -> None:
        text = (
            "import { a,\n  b } from '../infra/db';\n"   # 1
            "import type { T } from \"@app/domain\";\n"  # 3
            "import './side-effect';\n"                  # 4
            "export * from './x';\n"                     # 5
            "export { y } from 'lib-y';\n"              # 6
            "const z = require('lib-z');\n"              # 7
            "const w = await import('lib-w');\n"         # 8
            "// import q from 'commented';\n"            # 9
            "const s = \"import q from 'in-string'\";\n"  # 10
        )
        got = deps("typescript", "src/domain/a.ts", text)
        self.assertEqual(got, [
            ("src/infra/db", 1, True), ("@app/domain", 3, False), ("src/domain/side-effect", 4, True),
            ("src/domain/x", 5, True), ("lib-y", 6, False), ("lib-z", 7, False), ("lib-w", 8, False),
        ])

    def test_go_forms(self) -> None:
        text = 'package a\n\nimport "fmt"\nimport x "example.com/app/infra"\n\nimport (\n\t"os"\n\tdb "example.com/app/infra/db"\n)\n'
        self.assertEqual(deps("go", "domain/a.go", text), [
            ("fmt", 3, False), ("example.com/app/infra", 4, False), ("os", 7, False), ("example.com/app/infra/db", 8, False),
        ])


class ArchCheckTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_tmp("arch-")
        self.repo = GitRepo(self.root)
        self.repo.init()
        self.cfg = test_config()
        self.cfg["quality"]["arch"]["modules"] = [
            {"name": "domain", "paths": ["src/domain/**", "app/domain/**", "pkg/domain/**"],
             "imports": ["^crate::domain\\b", "^app\\.domain\\b", "^example\\.com/app/domain\\b"],
             "may_depend_on": [],
             "forbid": [{"pattern": "\\bSystemTime::now\\b", "reason": "domain logic takes time as input"},
                        {"pattern": "\\btokio::", "reason": "domain is runtime-free"}]},
            {"name": "infra", "paths": ["src/infra/**", "app/infra/**", "pkg/infra/**"],
             "imports": ["^crate::infra\\b", "^app\\.infra\\b", "^example\\.com/app/infra\\b"], "may_depend_on": ["domain"]},
        ]
        self.cfg["quality"]["arch"]["exclude"] = ["**/tests/**"]
        write_config(self.root, self.cfg)
        write(self.root, "src/lib.rs", "pub mod domain;\npub mod infra;\n")
        write(self.root, "src/infra/mod.rs", "use crate::domain::model;\n")  # allowed direction

    def tearDown(self) -> None:
        rm(self.root)

    def check(self):
        proc = run_cli(self.root, "check", "arch", "--json")
        return proc.returncode, json.loads(proc.stdout)

    def test_rust_rules(self) -> None:
        write(self.root, "src/domain/model.rs",
              "use std::time::SystemTime;\n"
              "// SystemTime::now in a comment is fine\n"
              "pub fn now() -> u64 {\n"
              "    let _ = crate::infra::db::connect();\n"
              "    let _ = super::super::infra::x();\n"
              "    tokio::spawn(async {});\n"
              "    SystemTime::now();\n"
              "    0\n"
              "}\n")
        write(self.root, "src/domain/tests/t.rs", "use crate::infra::db;\n")  # excluded
        self.repo.commit("rust")
        code, out = self.check()
        self.assertEqual(code, 1)
        got = sorted((v["kind"], v["file"], v["line"], v["key"].get("dep") or v["key"].get("pattern")) for v in out["violations"])
        self.assertEqual(got, [
            ("arch-dep", "src/domain/model.rs", 4, "domain->infra"),
            ("arch-dep", "src/domain/model.rs", 5, "domain->infra"),
            ("arch-forbid", "src/domain/model.rs", 6, "\\btokio::"),
            ("arch-forbid", "src/domain/model.rs", 7, "\\bSystemTime::now\\b"),
        ])
        self.assertTrue(any("domain logic takes time as input" in v["message"] for v in out["violations"]))

    def test_python_and_relative(self) -> None:
        write(self.root, "app/domain/svc.py", "import app\nimport json\n\n\ndef f():\n    return app.infra.x()\n")
        write(self.root, "app/domain/rel.py", "from ..infra import db\n")
        write(self.root, "app/infra/db.py", "from app.domain import svc\n")
        self.repo.commit("py")
        code, out = self.check()
        self.assertEqual(code, 1)
        got = sorted((v["file"], v["line"], v["key"]["dep"]) for v in out["violations"])
        self.assertEqual(got, [("app/domain/rel.py", 1, "domain->infra"), ("app/domain/svc.py", 6, "domain->infra")])

    def test_typescript_and_go(self) -> None:
        write(self.root, "src/domain/a.ts", "import { db } from '../infra/db';\nimport x from 'lodash';\n")
        write(self.root, "pkg/domain/a.go", 'package domain\n\nimport (\n\t"fmt"\n\t"example.com/app/infra"\n)\n')
        self.repo.commit("ts go")
        code, out = self.check()
        got = sorted((v["file"], v["line"], v["key"]["dep"]) for v in out["violations"])
        self.assertEqual(got, [("pkg/domain/a.go", 5, "domain->infra"), ("src/domain/a.ts", 1, "domain->infra")])
        self.assertEqual(code, 1)

    def test_clean_passes(self) -> None:
        self.repo.commit("clean")
        code, out = self.check()
        self.assertEqual(code, 0, out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
