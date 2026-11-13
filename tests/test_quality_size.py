"""WI1 AC 7: language registry, function detection and the size check."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from helpers import KIT, GitRepo, make_tmp, rm, run_cli, test_config, write, write_config

from sddkit.quality import langs

FIX = KIT / "tests" / "fixtures" / "quality" / "size"


def spans(rel: str, cfg=None) -> dict:
    cfg = cfg or test_config()
    text = (FIX / rel).read_text()
    src = langs.lex(text, langs.language_for(rel, cfg))
    return {(f.name, f.ordinal): (f.start + 1, f.end + 1, f.lines) for f in langs.find_functions(src)}


class FunctionSpanTest(unittest.TestCase):
    def test_rust(self) -> None:
        got = spans("sample.rs")
        self.assertEqual(got, {
            ("alpha", 0): (4, 9, 5),  # braces in strings, chars and comments ignored; doc comments not counted
            ("generic", 0): (11, 15, 3),  # -> Result<Vec<u8>, E> where T: Clone {
            ("method", 0): (20, 23, 4),
            ("nested", 0): (21, 21, 1),
            ("t", 0): (33, 35, 3),
        })  # `fn decl(&self);` has no body

    def test_rust_test_region(self) -> None:
        src = langs.lex((FIX / "sample.rs").read_text(), langs.LANGS["rust"])
        self.assertEqual(langs.test_regions(src), [(29, 35)])

    def test_python(self) -> None:
        got = spans("sample.py")
        self.assertEqual(got, {
            ("outer", 0): (7, 21, 8),  # docstring not counted; dedented triple-quoted lines do not end the body
            ("inner", 0): (15, 18, 4),
            ("method", 0): (27, 28, 2),
            ("amethod", 0): (30, 31, 2),
            ("black_formatted", 0): (34, 41, 8),  # `) -> dict:` at the def's indent belongs to the signature
            ("one_liner", 0): (44, 44, 1),
        })
        src = langs.lex((FIX / "sample.py").read_text(), langs.LANGS["python"])
        self.assertEqual(langs.code_line_count(src), 23)  # module, class and function docstrings excluded

    def test_typescript(self) -> None:
        got = spans("sample.ts")
        self.assertEqual(got, {
            ("Comp", 0): (2, 5, 4),  # function Comp({ title, children }) {
            ("f", 0): (7, 9, 3),  # const f = ({ a }) => {
            ("g", 0): (11, 13, 3),
            ("typed", 0): (17, 19, 3),  # function f(o: { a: number }): void {
            ("retLiteral", 0): (21, 23, 3),  # function f(): { a: number } {
            ("method", 0): (28, 47, 20),  # if/for/while/switch/catch are not methods
            ("load", 0): (49, 51, 3),
        })  # expression-bodied arrow and interface members have no body

    def test_go(self) -> None:
        got = spans("sample.go")
        self.assertEqual(got, {
            ("F", 0): (5, 9, 5),  # func f(m map[string]interface{}) error {
            ("G", 0): (11, 13, 3),  # func f() interface{ M() } {
            ("Method", 0): (17, 22, 6),  # receiver
            ("Map", 0): (24, 28, 5),  # type parameters
        })

    def test_ordinals_and_blank_comment_lines(self) -> None:
        text = "def f():\n    # c\n\n    return 1\n\n\ndef f():\n    return 2\n"
        fns = langs.find_functions(langs.lex(text, langs.LANGS["python"]))
        self.assertEqual([(f.name, f.ordinal, f.lines) for f in fns], [("f", 0, 2), ("f", 1, 2)])


def py_function(name: str, body_lines: int) -> str:
    # def line + body lines = code lines of the function; interleaved blanks and comments do not count.
    body = "".join(f"    x{i} = {i}\n    # comment {i}\n\n" for i in range(body_lines - 1))
    return f"def {name}():\n{body}"


class SizeCheckTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_tmp("size-")
        self.repo = GitRepo(self.root)
        self.repo.init()
        self.cfg = test_config()
        self.cfg["quality"]["generated"] = ["gen/**"]
        self.cfg["quality"]["size"]["exclude"] = ["vendor/**"]
        write_config(self.root, self.cfg)

    def tearDown(self) -> None:
        rm(self.root)

    def check(self, *extra: str):
        proc = run_cli(self.root, "check", "size", "--json", *extra)
        return proc.returncode, json.loads(proc.stdout) if proc.stdout.strip().startswith("{") else proc

    def test_function_threshold(self) -> None:
        write(self.root, "src/ok.py", py_function("fine", 80))
        self.repo.commit("ok")
        code, out = self.check()
        self.assertEqual(code, 0, out)
        write(self.root, "src/big.py", py_function("too_big", 81))
        self.repo.commit("big")
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertEqual([(v["kind"], v["file"], v["key"]["function"], v["value"]) for v in out["violations"]],
                         [("size-function", "src/big.py", "too_big", 81)])
        proc = run_cli(self.root, "check", "size")
        self.assertIn("src/big.py:1: function too_big: 81 code lines", proc.stdout)

    def test_file_thresholds_and_skips(self) -> None:
        many = lambda n: "".join(f"x{i} = {i}\n\n# c\n" for i in range(n))  # noqa: E731
        write(self.root, "src/warn.py", many(601))
        write(self.root, "src/edge.py", many(600))
        self.repo.commit("files")
        code, out = self.check()
        self.assertEqual(code, 0, out)
        self.assertEqual(len(out["warnings"]), 1)
        self.assertIn("src/warn.py: 601 code lines", out["warnings"][0])
        write(self.root, "src/fail.py", many(1001))
        write(self.root, "src/limit.py", many(1000))
        write(self.root, "gen/big.py", many(1001))
        write(self.root, "vendor/big.py", many(1001))
        self.repo.commit("more")
        write(self.root, "src/untracked.py", many(1001))
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertEqual([v["file"] for v in out["violations"]], ["src/fail.py"])
        self.assertEqual(out["violations"][0]["value"], 1001)

    def test_registry_extension_from_config(self) -> None:
        self.cfg["quality"]["languages"] = {".pyx": "python"}
        self.cfg["quality"]["size"]["file_fail"] = 3
        self.cfg["quality"]["size"]["file_warn"] = 0
        write_config(self.root, self.cfg)
        write(self.root, "src/fast.pyx", "a = 1\nb = 2\nc = 3\nd = 4\n")
        write(self.root, "notes/readme.txt", "a\nb\nc\nd\ne\n")  # unknown extension: not measured
        self.repo.commit("pyx")
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertEqual([v["file"] for v in out["violations"]], ["src/fast.pyx"])

    def test_staged_content(self) -> None:
        self.cfg["quality"]["size"]["file_fail"] = 3
        self.cfg["quality"]["size"]["file_warn"] = 0
        write_config(self.root, self.cfg)
        write(self.root, "src/a.py", "a = 1\n")
        self.repo.commit("a")
        write(self.root, "src/a.py", "a = 1\nb = 2\nc = 3\nd = 4\n")
        self.repo.git("add", "src/a.py")
        write(self.root, "src/a.py", "a = 1\n")  # working tree shrinks again; the staged blob is judged
        code, out = self.check("--staged")
        self.assertEqual(code, 1)
        self.assertEqual(out["violations"][0]["file"], "src/a.py")
        code, out = self.check()
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
