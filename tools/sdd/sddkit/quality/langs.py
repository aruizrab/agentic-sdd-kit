"""Language registry: the only place in the kit with per-language knowledge.

For each language: extensions, comment syntax, string/char literal syntax, a function
finder, an import extractor, test-file patterns, optional test regions inside source
files, no-code file patterns and whether the language has top-level executable
statements. Built-in: ``rust``, ``python``, ``typescript`` (also JavaScript), ``go``.
``quality.languages`` in ``sdd.config.json`` maps further extensions to one of these, or to
``none`` (plain: file-size check only, every non-blank line counts).

Everything here is a documented heuristic (docs/ADAPTING.md lists the limits): no parser,
no type information. A lexer blanks comments and literal contents so that brackets,
keywords and imports are only ever seen in code.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..util import glob_match

GENERIC_TEST_GLOBS = ["tests/**", "**/tests/**", "test/**", "**/test/**"]


@dataclass(frozen=True)
class Lang:
    name: str
    exts: Tuple[str, ...]
    test_patterns: Tuple[str, ...] = ()
    no_code_patterns: Tuple[str, ...] = ()
    top_level_statements: bool = False
    comment_marker: str = "//"


LANGS: Dict[str, Lang] = {
    "rust": Lang("rust", (".rs",), ("**/benches/**", "**/examples/**"), (), False, "//"),
    "python": Lang("python", (".py",), ("**/test_*.py", "**/*_test.py", "**/conftest.py"), (), True, "#"),
    "typescript": Lang("typescript", (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"),
                       ("**/__tests__/**", "**/*.test.*", "**/*.spec.*"), ("**/*.d.ts",), True, "//"),
    "go": Lang("go", (".go",), ("**/*_test.go",), (), False, "//"),
}
PLAIN = Lang("none", ())


def _ext_map(cfg: Dict[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for lang in LANGS.values():
        for ext in lang.exts:
            out[ext] = lang.name
    for ext, name in ((cfg.get("quality") or {}).get("languages") or {}).items():
        key = ext if ext.startswith(".") else "." + ext
        out[key] = name
    return out


def language_for(path: str, cfg: Dict[str, Any]) -> Optional[Lang]:
    """The language of a path, ``PLAIN`` for an extension mapped to ``none``, or None (unknown)."""
    name = path.rsplit("/", 1)[-1]
    ext_map = _ext_map(cfg)
    # Longest matching suffix first (".d.ts" style keys may be configured).
    for ext in sorted(ext_map, key=len, reverse=True):
        if name.endswith(ext):
            lang = ext_map[ext]
            return LANGS.get(lang, PLAIN)
    return None


def known_globs(cfg: Dict[str, Any]) -> List[str]:
    """Every extension the registry knows (built-in plus ``quality.languages``) as ``*.<ext>``."""
    return sorted("*" + ext for ext in _ext_map(cfg))


def test_globs(cfg: Dict[str, Any]) -> List[str]:
    globs = list(GENERIC_TEST_GLOBS)
    for lang in LANGS.values():
        globs.extend(lang.test_patterns)
    globs.extend((cfg.get("quality") or {}).get("test_globs") or [])
    return globs


def is_test_file(path: str, cfg: Dict[str, Any]) -> bool:
    return any(glob_match(path, g) for g in test_globs(cfg))


def is_no_code_file(path: str, cfg: Dict[str, Any]) -> bool:
    return any(glob_match(path, g) for lang in LANGS.values() for g in lang.no_code_patterns)


# ----------------------------------------------------------------------------
# Lexer
# ----------------------------------------------------------------------------


@dataclass
class Source:
    """A lexed file. All lists are indexed by 0-based line number."""

    lang: Lang
    lines: List[str]
    struct: List[str]  # comments and literal contents blanked (quotes kept)
    code: List[str]  # comments blanked, literals kept
    is_code: List[bool]  # line has code (not blank, not comment-only, not a docstring)
    comments: List[str]  # comment text on the line
    starts_in_literal: List[bool]  # line begins inside a multi-line literal
    depth0: List[int] = field(default_factory=list)  # bracket depth at line start
    strings: List[Tuple[int, int, int, int]] = field(default_factory=list)  # (sl, sc, el, ec)


_IDENT = re.compile(r"[A-Za-z0-9_$]")


class _Lexer:
    def __init__(self, text: str, lang: Lang) -> None:
        self.text = text
        self.lang = lang
        self.n = len(text)
        self.struct = list(text)
        self.code = list(text)
        nlines = text.count("\n") + 1
        self.is_code = [False] * nlines
        self.comments: List[List[str]] = [[] for _ in range(nlines)]
        self.starts_in = [False] * nlines
        self.strings: List[Tuple[int, int, int, int]] = []
        self.line_starts = [0]
        for i, c in enumerate(text):
            if c == "\n":
                self.line_starts.append(i + 1)

    def line_of(self, i: int) -> int:
        lo, hi = 0, len(self.line_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.line_starts[mid] <= i:
                lo = mid
            else:
                hi = mid - 1
        return lo

    def comment(self, a: int, b: int) -> None:
        line = self.line_of(a)
        buf = []
        for k in range(a, b):
            c = self.text[k]
            if c == "\n":
                self.comments[line].append("".join(buf))
                buf = []
                line += 1
                continue
            buf.append(c)
            self.struct[k] = " "
            self.code[k] = " "
        self.comments[line].append("".join(buf))

    def literal(self, a: int, b: int, open_len: int, close_len: int) -> None:
        """Literal text[a:b] whose delimiters have the given lengths: blank the contents in struct."""
        sl = self.line_of(a)
        line = sl
        self.is_code[line] = True
        for k in range(a + open_len, max(a + open_len, b - close_len)):
            c = self.text[k]
            if c == "\n":
                line += 1
                self.starts_in[line] = True
                continue
            self.struct[k] = " "
            if not c.isspace():
                self.is_code[line] = True
        el = self.line_of(max(a, b - 1))
        self.is_code[el] = True
        self.strings.append((sl, a - self.line_starts[sl], el, b - self.line_starts[el]))

    def mark(self, i: int) -> None:
        if not self.text[i].isspace():
            self.is_code[self.line_of(i)] = True

    def find_close(self, i: int, quote: str, escapes: bool, multiline: bool) -> int:
        """Index just past the closing ``quote`` searching from i (exclusive of the opener)."""
        t, n = self.text, self.n
        while i < n:
            if escapes and t[i] == "\\":
                i += 2
                continue
            if t.startswith(quote, i):
                return i + len(quote)
            if t[i] == "\n" and not multiline:
                return i
            i += 1
        return n

    def run(self) -> Source:
        name = self.lang.name
        if name == "python":
            self._run_python()
        elif name in ("rust", "go", "typescript"):
            self._run_clike()
        else:
            for i in range(self.n):
                self.mark(i)
        return self._source()

    def _run_python(self) -> None:
        t, n, i = self.text, self.n, 0
        prefix = re.compile(r"(?i)(?:rb|br|fr|rf|b|r|u|f)?(\"\"\"|'''|\"|')")
        while i < n:
            c = t[i]
            if c == "#":
                j = t.find("\n", i)
                j = n if j < 0 else j
                self.comment(i, j)
                i = j
                continue
            if c in "\"'rRbBuUfF" and (i == 0 or not _IDENT.match(t[i - 1])):
                m = prefix.match(t, i)
                if m:
                    q = m.group(1)
                    start = m.end()
                    j = self.find_close(start, q, True, len(q) == 3)
                    self.literal(i, j, m.end() - i, len(q) if t.startswith(q, j - len(q)) and j - len(q) >= start else 0)
                    i = j
                    continue
            self.mark(i)
            i += 1

    def _run_clike(self) -> None:
        t, n, i = self.text, self.n, 0
        name = self.lang.name
        raw = re.compile(r"b?r(#*)\"")
        while i < n:
            c = t[i]
            if t.startswith("//", i):
                j = t.find("\n", i)
                j = n if j < 0 else j
                self.comment(i, j)
                i = j
                continue
            if t.startswith("/*", i):
                depth, j = 1, i + 2
                while j < n and depth:
                    if name == "rust" and t.startswith("/*", j):
                        depth, j = depth + 1, j + 2
                    elif t.startswith("*/", j):
                        depth, j = depth - 1, j + 2
                    else:
                        j += 1
                self.comment(i, j)
                i = j
                continue
            if name == "rust" and c in "br" and (i == 0 or not _IDENT.match(t[i - 1])):
                m = raw.match(t, i)
                if m:
                    close = '"' + m.group(1)
                    j = t.find(close, m.end())
                    j = n if j < 0 else j + len(close)
                    self.literal(i, j, m.end() - i, len(close))
                    i = j
                    continue
            if c == '"':
                j = self.find_close(i + 1, '"', True, name == "rust")
                self.literal(i, j, 1, 1)
                i = j
                continue
            if c == "`" and name in ("go", "typescript"):
                j = self.find_close(i + 1, "`", name == "typescript", True)
                self.literal(i, j, 1, 1)
                i = j
                continue
            if c == "'":
                if name == "typescript":
                    j = self.find_close(i + 1, "'", True, False)
                    self.literal(i, j, 1, 1)
                    i = j
                    continue
                # Rust/Go char literal ('a', '\n', '\u{1F600}'); a Rust lifetime ('a) is code.
                m = re.match(r"'(?:\\(?:u\{[0-9a-fA-F]{1,6}\}|x[0-9a-fA-F]{2}|.)|[^\\'\n])'", t[i:i + 12])
                if m:
                    self.literal(i, i + m.end(), 1, 1)
                    i += m.end()
                    continue
            self.mark(i)
            i += 1

    def _source(self) -> Source:
        struct = "".join(self.struct).split("\n")
        code = "".join(self.code).split("\n")
        lines = self.text.split("\n")
        depth0 = []
        depth = 0
        for s in struct:
            depth0.append(depth)
            for ch in s:
                if ch in "([{":
                    depth += 1
                elif ch in ")]}":
                    depth = max(0, depth - 1)
        return Source(self.lang, lines, struct, code, list(self.is_code),
                      [" ".join(c).strip() for c in self.comments], list(self.starts_in), depth0, self.strings)


def lex(text: str, lang: Lang) -> Source:
    src = _Lexer(text, lang).run()
    if lang.name == "python":
        _python_docstrings(src)
    return src


def code_line_count(src: Source, start: int = 0, end: Optional[int] = None) -> int:
    end = len(src.lines) - 1 if end is None else end
    return sum(1 for k in range(start, end + 1) if src.is_code[k])


# ----------------------------------------------------------------------------
# Functions
# ----------------------------------------------------------------------------


@dataclass
class Function:
    name: str
    start: int  # 0-based first line (the def/fn/func line)
    end: int  # 0-based last line
    lines: int = 0  # code lines
    ordinal: int = 0  # among same-named functions in the file


def find_functions(src: Source) -> List[Function]:
    name = src.lang.name
    if name == "python":
        funcs = [Function(n, s, e) for kind, n, s, _sig, e in _python_blocks(src) if kind == "def"]
    elif name in ("rust", "go", "typescript"):
        funcs = _brace_functions(src)
    else:
        funcs = []
    seen: Dict[str, int] = {}
    for f in sorted(funcs, key=lambda f: (f.start, f.end)):
        f.lines = code_line_count(src, f.start, f.end)
        f.ordinal = seen.get(f.name, 0)
        seen[f.name] = f.ordinal + 1
    return sorted(funcs, key=lambda f: (f.start, f.end))


def _indent(s: str) -> int:
    return len(s) - len(s.lstrip(" \t"))


_PY_HEADER = re.compile(r"^(\s*)(?:async\s+)?(def|class)\s+([A-Za-z_]\w*)")


def _python_blocks(src: Source) -> List[Tuple[str, str, int, int, int]]:
    """(kind, name, start line, signature end line, end line) for every def/class."""
    out = []
    n = len(src.struct)
    for idx, s in enumerate(src.struct):
        if src.depth0[idx] or src.starts_in_literal[idx]:
            continue
        m = _PY_HEADER.match(s)
        if not m:
            continue
        indent = len(m.group(1).expandtabs())
        # The signature ends at the first ':' at bracket depth 0 after the name.
        depth, line, col, found = 0, idx, m.end(), None
        while line < n and found is None:
            text = src.struct[line]
            while col < len(text):
                ch = text[col]
                if ch in "([{":
                    depth += 1
                elif ch in ")]}":
                    depth -= 1
                elif ch == ":" and depth == 0:
                    found = (line, col)
                    break
                col += 1
            if found is None:
                line, col = line + 1, 0
        if found is None:
            continue
        sig_line, sig_col = found
        if src.struct[sig_line][sig_col + 1:].strip():
            out.append((m.group(2), m.group(3), idx, sig_line, sig_line))
            continue
        last = sig_line
        k = sig_line + 1
        while k < n:
            s2 = src.struct[k]
            if s2.strip() and not src.starts_in_literal[k] and src.depth0[k] == 0 and len(s2[:_indent(s2)].expandtabs()) <= indent:
                break
            if src.is_code[k] or s2.strip():
                last = k
            k += 1
        out.append((m.group(2), m.group(3), idx, sig_line, last))
    return out


def _string_at(src: Source, line: int, col: int) -> Optional[Tuple[int, int, int, int]]:
    for s in src.strings:
        if s[0] == line and s[1] == col:
            return s
    return None


def _python_docstrings(src: Source) -> None:
    """Mark docstrings (first statement of a module, class or function body) as not code."""
    starts: List[Tuple[int, int]] = [(-1, -1)]  # (first line to search, minimum indent exclusive)
    for kind, _name, start, sig_end, end in _python_blocks(src):
        if end > sig_end:
            starts.append((sig_end + 1, len(src.struct[start][:_indent(src.struct[start])].expandtabs())))
    for first, min_indent in starts:
        k = max(first, 0)
        while k < len(src.struct) and not src.struct[k].strip():
            k += 1
        if k >= len(src.struct):
            continue
        s = src.struct[k]
        col = _indent(s)
        if min_indent >= 0 and len(s[:col].expandtabs()) <= min_indent:
            continue
        tok = _string_at(src, k, col)
        if tok is None:
            # A prefixed string (r"""...""") starts at the prefix.
            continue
        sl, sc, el, ec = tok
        if src.struct[el][ec:].strip():
            continue
        for line in range(sl, el + 1):
            src.is_code[line] = False


_RUST_FN = re.compile(r"^\s*(?:pub(?:\s*\([^)]*\))?\s+)?(?:(?:const|async|unsafe|default)\s+)*(?:extern\s+\"[^\"]*\"\s+)?fn\s+([A-Za-z_]\w*)")
_GO_FN = re.compile(r"^func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)")
_TS_FN = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:declare\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)")
_TS_ARROW = re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*(?:async\s+)?(?=[(<])")
_TS_METHOD = re.compile(r"^\s*(?:(?:public|private|protected|static|async|readonly|override|abstract|get|set|accessor)\s+)*\*?\s*(#?[A-Za-z_$][\w$]*)\s*(?=[(<])")
_TS_NOT_METHOD = {"if", "for", "while", "switch", "catch", "with", "return", "else", "do", "function", "new",
                  "typeof", "await", "yield", "throw", "delete", "void", "super", "import", "case", "in", "of"}


class _Text:
    """The struct text of a file as one string, with line lookups."""

    def __init__(self, src: Source) -> None:
        self.s = "\n".join(src.struct)
        self.starts = [0]
        for line in src.struct[:-1]:
            self.starts.append(self.starts[-1] + len(line) + 1)

    def line_of(self, i: int) -> int:
        lo, hi = 0, len(self.starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.starts[mid] <= i:
                lo = mid
            else:
                hi = mid - 1
        return lo

    def ws(self, i: int) -> int:
        while i < len(self.s) and self.s[i] in " \t\r\n":
            i += 1
        return i

    def balanced(self, i: int, pairs: str = "()[]{}") -> Optional[int]:
        """Index just past the bracket group opening at i."""
        opens, closes = pairs[0::2], pairs[1::2]
        depth = 0
        for j in range(i, len(self.s)):
            c = self.s[j]
            if c in opens:
                depth += 1
            elif c in closes:
                depth -= 1
                if depth == 0:
                    return j + 1
        return None

    def angle(self, i: int) -> Optional[int]:
        depth = 0
        j = i
        while j < len(self.s):
            if self.s.startswith("=>", j) or self.s.startswith("->", j):
                j += 2
                continue
            c = self.s[j]
            if c == "<":
                depth += 1
            elif c == ">":
                depth -= 1
                if depth == 0:
                    return j + 1
            elif c in ";{}":
                return None
            j += 1
        return None

    def prev_word(self, i: int) -> str:
        j = i - 1
        while j >= 0 and self.s[j] in " \t\r\n":
            j -= 1
        k = j
        while k >= 0 and (self.s[k].isalnum() or self.s[k] == "_"):
            k -= 1
        return self.s[k + 1:j + 1]

    def prev_char(self, i: int) -> str:
        j = i - 1
        while j >= 0 and self.s[j] in " \t\r\n":
            j -= 1
        return self.s[j] if j >= 0 else ""


def _body_start(tx: _Text, pos: int, lang: str, arrow_kind: bool) -> Optional[int]:
    """Index of the body's '{' for a function whose name ends at pos, or None (no body)."""
    s = tx.s
    i = tx.ws(pos)
    if lang == "go" and i < len(s) and s[i] == "[":
        i = tx.balanced(i)
        if i is None:
            return None
        i = tx.ws(i)
    elif lang in ("rust", "typescript") and i < len(s) and s[i] == "<":
        i = tx.angle(i)
        if i is None:
            return None
        i = tx.ws(i)
    if i >= len(s) or s[i] != "(":
        return None
    i = tx.balanced(i)
    if i is None:
        return None
    depth = 0
    saw_colon = False
    j = i
    while j < len(s):
        c = s[j]
        if s.startswith("->", j):
            j += 2
            continue
        if s.startswith("=>", j):
            if depth == 0 and arrow_kind:
                k = tx.ws(j + 2)
                return k if k < len(s) and s[k] == "{" else None
            j += 2
            continue
        if c == "\n" and depth == 0 and lang == "typescript" and not saw_colon:
            k = tx.ws(j)
            if k < len(s) and s[k] == "{" and not arrow_kind:
                return k
            if not arrow_kind:
                return None
        if depth > 0:
            if c in "([<{":
                depth += 1
            elif c in ")]>}":
                depth -= 1
            j += 1
            continue
        if c in "([<":
            depth += 1
        elif c in ")]>":
            return None
        elif c == ":" and lang == "typescript":
            saw_colon = True
        elif c == ";" or c == "}":
            return None
        elif c == "=" and lang != "rust" and not arrow_kind:
            return None
        elif c == "{":
            if lang == "go" and tx.prev_word(j) in ("interface", "struct"):
                j = tx.balanced(j, "{}") or len(s)
                continue
            if lang == "typescript" and (tx.prev_char(j) in (":", "|", "&", ",") or arrow_kind):
                j = tx.balanced(j, "{}") or len(s)
                continue
            return j
        j += 1
    return None


def _brace_functions(src: Source) -> List[Function]:
    tx = _Text(src)
    lang = src.lang.name
    out: List[Function] = []
    for idx, line in enumerate(src.struct):
        if src.starts_in_literal[idx] and not line.strip():
            continue
        arrow = False
        if lang == "rust":
            m = _RUST_FN.match(line)
        elif lang == "go":
            m = _GO_FN.match(line)
        else:
            m = _TS_FN.match(line)
            if not m:
                m = _TS_ARROW.match(line)
                arrow = bool(m)
                if m:
                    # Start scanning at the parameter list (or generic list) after '='.
                    body = _body_start(tx, tx.starts[idx] + m.end(), lang, True)
                    if body is not None:
                        end = tx.balanced(body, "{}")
                        if end is not None:
                            out.append(Function(m.group(1), idx, tx.line_of(end - 1)))
                    continue
            if not m:
                m = _TS_METHOD.match(line)
                if m and m.group(1) in _TS_NOT_METHOD:
                    m = None
        if not m:
            continue
        body = _body_start(tx, tx.starts[idx] + m.end(), lang, arrow)
        if body is None:
            continue
        end = tx.balanced(body, "{}")
        if end is None:
            continue
        out.append(Function(m.group(1), idx, tx.line_of(end - 1)))
    return out


# ----------------------------------------------------------------------------
# Test regions
# ----------------------------------------------------------------------------


def test_regions(src: Source) -> List[Tuple[int, int]]:
    """0-based inclusive line ranges of test code inside a source file (Rust ``#[cfg(test)]``)."""
    if src.lang.name != "rust":
        return []
    tx = _Text(src)
    out = []
    for m in re.finditer(r"#\[cfg\(test\)\]", tx.s):
        j = m.end()
        while j < len(tx.s) and tx.s[j] not in "{;":
            j += 1
        if j >= len(tx.s):
            continue
        if tx.s[j] == ";":
            out.append((tx.line_of(m.start()), tx.line_of(j)))
            continue
        end = tx.balanced(j, "{}")
        if end is not None:
            out.append((tx.line_of(m.start()), tx.line_of(end - 1)))
    return out


# ----------------------------------------------------------------------------
# Imports / dependencies
# ----------------------------------------------------------------------------


@dataclass
class Dep:
    spec: str  # the import specifier or qualified path
    line: int  # 1-based
    relative: bool = False  # a path relative to the importing file (already resolved)


def _rust_module_path(path: str, files: Iterable[str]) -> List[str]:
    """``crate`` module path segments of a Rust file (relative to the nearest lib.rs/main.rs dir)."""
    fileset = set(files)
    parts = path.split("/")
    for cut in range(len(parts) - 1, -1, -1):
        d = "/".join(parts[:cut])
        prefix = d + "/" if d else ""
        if prefix + "lib.rs" in fileset or prefix + "main.rs" in fileset:
            rest = parts[cut:]
            break
    else:
        rest = parts[1:] if parts[0] == "src" else parts
    if rest and rest[-1] in ("lib.rs", "main.rs", "mod.rs"):
        rest = rest[:-1]
    elif rest:
        rest = rest[:-1] + [rest[-1][:-3] if rest[-1].endswith(".rs") else rest[-1]]
    return rest


def _expand_use(tree: str) -> List[str]:
    """``a::{b, c::{d, e}}`` -> ``a::b``, ``a::c::d``, ``a::c::e`` (aliases dropped)."""
    tree = re.sub(r"\s+as\s+\w+", "", tree)
    tree = re.sub(r"\s+", "", tree)
    if "{" not in tree:
        return [tree]
    i = tree.index("{")
    head = tree[:i]
    depth, parts, cur = 0, [], ""
    for ch in tree[i + 1:]:
        if ch == "{":
            depth += 1
        elif ch == "}":
            if depth == 0:
                break
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
            continue
        cur += ch
    if cur:
        parts.append(cur)
    out = []
    for p in parts:
        if p in ("self", ""):
            out.append(head.rstrip(":"))
            continue
        out.extend(_expand_use(head + p))
    return out


def _rust_deps(src: Source, path: str, files: Iterable[str], name_ok) -> List[Dep]:
    mod = _rust_module_path(path, files)
    deps: List[Dep] = []
    use_lines = set()
    tx = _Text(src)
    text = tx.s
    line_of = tx.line_of

    def absolute(p: str) -> str:
        segs = p.split("::")
        if segs[0] == "crate":
            return p
        if segs[0] in ("self", "super"):
            base = list(mod)
            k = 0
            if segs[0] == "self":
                k = 1
            while k < len(segs) and segs[k] == "super":
                base = base[:-1]
                k += 1
            return "::".join(["crate", *base, *segs[k:]])
        return p

    for m in re.finditer(r"(?m)^\s*(?:pub(?:\s*\([^)]*\))?\s+)?use\s+([^;]+);", text):
        ln = line_of(m.start())
        for k in range(ln, line_of(m.end()) + 1):
            use_lines.add(k)
        for p in _expand_use(m.group(1)):
            p = re.sub(r"\s+as\s+\w+$", "", p).lstrip(":")
            deps.append(Dep(absolute(p), ln + 1))
    for m in re.finditer(r"(?m)^\s*extern\s+crate\s+(\w+)", text):
        deps.append(Dep(m.group(1), line_of(m.start()) + 1))
    qual = re.compile(r"(?<![\w:])((?:crate|super|self|[A-Za-z_]\w*)(?:::(?:[A-Za-z_]\w*))+)")
    for idx, line in enumerate(src.struct):
        if idx in use_lines or not src.is_code[idx]:
            continue
        for m in qual.finditer(line):
            p = m.group(1)
            head = p.split("::")[0]
            if head in ("crate", "super", "self"):
                deps.append(Dep(absolute(p), idx + 1))
            elif name_ok(p):
                deps.append(Dep(p, idx + 1))
    return deps


def _python_deps(src: Source, path: str) -> List[Dep]:
    deps: List[Dep] = []
    bound: Dict[str, str] = {}
    import_lines = set()
    text = "\n".join(src.code)
    tx = _Text(src)
    pkg = path.split("/")[:-1]
    for m in re.finditer(r"(?m)^[ \t]*import[ \t]+([^\n]+)", text):
        ln = tx.line_of(m.start())
        import_lines.add(ln)
        for part in m.group(1).split(","):
            part = part.strip()
            mm = re.match(r"([\w.]+)(?:\s+as\s+(\w+))?", part)
            if not mm:
                continue
            name, alias = mm.group(1), mm.group(2)
            deps.append(Dep(name, ln + 1))
            if alias:
                bound[alias] = name
            else:
                bound[name.split(".")[0]] = name.split(".")[0]
    for m in re.finditer(r"(?m)^[ \t]*from[ \t]+(\.*)([\w.]*)[ \t]+import[ \t]+(\([^)]*\)|[^\n]+)", text):
        ln = tx.line_of(m.start())
        for k in range(ln, tx.line_of(m.end()) + 1):
            import_lines.add(k)
        dots, mod = m.group(1), m.group(2)
        names = [re.split(r"\s+as\s+", n.strip())[0] for n in m.group(3).strip("()").replace("\\", "").split(",") if n.strip()]
        if dots:
            base = pkg[: len(pkg) - (len(dots) - 1)] if len(dots) > 1 else list(pkg)
            target = "/".join(base + ([*mod.split(".")] if mod else []))
            for n in names:
                spec = target if mod or n == "*" else posixpath.join(target, n)
                deps.append(Dep(spec, ln + 1, relative=True))
                if mod:
                    break
        else:
            for n in names:
                deps.append(Dep(mod if n == "*" else f"{mod}.{n}", ln + 1))
    if bound:
        chain = re.compile(r"(?<![\w.])(" + "|".join(re.escape(b) for b in sorted(bound, key=len, reverse=True)) + r")((?:\.\w+)+)")
        for idx, line in enumerate(src.struct):
            if idx in import_lines or not src.is_code[idx]:
                continue
            for m in chain.finditer(line):
                deps.append(Dep(bound[m.group(1)] + m.group(2), idx + 1))
    return deps


def _ts_deps(src: Source, path: str) -> List[Dep]:
    text = "\n".join(src.code)
    tx = _Text(src)
    deps: List[Dep] = []
    patterns = [
        r"\bimport\s+(?:type\s+)?(?:[\w*{}\s,$]+?\s+from\s+)?([\"'])([^\"'\n]+)\1",
        r"\bexport\s+(?:type\s+)?(?:\*(?:\s+as\s+[\w$]+)?|\{[^}]*\})\s+from\s+([\"'])([^\"'\n]+)\1",
        r"\brequire\(\s*([\"'])([^\"'\n]+)\1\s*\)",
        r"\bimport\(\s*([\"'])([^\"'\n]+)\1\s*\)",
    ]
    seen = set()
    for p in patterns:
        for m in re.finditer(p, text):
            # Only matches that start in code (struct text has the keyword, not inside a literal).
            if tx.s[m.start():m.start() + 3] != text[m.start():m.start() + 3]:
                continue
            key = (m.start(), m.group(2))
            if key in seen:
                continue
            seen.add(key)
            spec = m.group(2)
            ln = tx.line_of(m.start()) + 1
            if spec.startswith("./") or spec.startswith("../"):
                deps.append(Dep(posixpath.normpath(posixpath.join(posixpath.dirname(path), spec)), ln, relative=True))
            else:
                deps.append(Dep(spec, ln))
    return sorted(deps, key=lambda d: d.line)


def _go_deps(src: Source) -> List[Dep]:
    text = "\n".join(src.code)
    tx = _Text(src)
    deps: List[Dep] = []
    for m in re.finditer(r"(?m)^import[ \t]+(?:[\w.]+[ \t]+)?\"([^\"]+)\"", text):
        deps.append(Dep(m.group(1), tx.line_of(m.start()) + 1))
    for m in re.finditer(r"(?m)^import[ \t]*\(([^)]*)\)", text):
        for mm in re.finditer(r"\"([^\"]+)\"", m.group(1)):
            deps.append(Dep(mm.group(1), tx.line_of(m.start(1) + mm.start()) + 1))
    return sorted(deps, key=lambda d: d.line)


def extract_deps(src: Source, path: str, files: Iterable[str] = (), name_ok=lambda p: False) -> List[Dep]:
    """Dependencies of a file with their 1-based lines. ``name_ok(path)`` says whether a
    non-relative Rust qualified path names a mapped module (so usages without ``use`` count)."""
    name = src.lang.name
    if name == "rust":
        return _rust_deps(src, path, files, name_ok)
    if name == "python":
        return _python_deps(src, path)
    if name == "typescript":
        return _ts_deps(src, path)
    if name == "go":
        return _go_deps(src)
    return []


def relative_candidates(spec: str, lang: Lang) -> List[str]:
    """Paths a resolved relative specifier may refer to (module ``paths`` globs are matched)."""
    out = [spec]
    if lang.name == "python":
        out += [spec + ".py", spec + "/__init__.py"]
    elif lang.name == "typescript":
        out += [spec + e for e in lang.exts] + [spec + "/index" + e for e in lang.exts]
    return out
