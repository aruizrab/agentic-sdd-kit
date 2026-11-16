"""WI4 acceptance criteria: documentation and lessons, and the repository hygiene scan.

AC 1  LESSONS.md has entries 16-41 (what happened, rule, where).
AC 2  every key path of config.DEFAULTS appears in docs/CONFIG.md.
AC 3  PLAYBOOK has the adoption section with its three steps in order.
AC 4  every tracked text file: `--no-verify` only in prohibitions and guard code; e-mail
      addresses only noreply@ or reserved example domains; URL hosts on an allowlist and no
      forge owner/repo URL outside the allowlist; the reference project only in docs, by
      name, without a URL.
AC 5  every command of examples/configs is described in ADAPTING.md; README's tools table
      lists every `sdd` subcommand of cli.py.
"""

from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path

from helpers import KIT
from sddkit import config as cfgmod

DOCS = KIT / "docs"
# Spelled in two halves so that this file does not mention the reference project itself.
REFERENCE_PROJECT = "ferro" + "core"


def read(rel: str) -> str:
    return (KIT / rel).read_text(encoding="utf-8")


def section(text: str, heading_regex: str) -> str:
    """The body of the first heading matching heading_regex up to the next heading of the same level."""
    m = re.search(rf"^(#+)\s+{heading_regex}.*$", text, re.M)
    if not m:
        return ""
    level = len(m.group(1))
    rest = text[m.end():]
    nxt = re.search(rf"^#{{1,{level}}}\s", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


class LessonsTest(unittest.TestCase):
    """AC 1."""

    EXPECTED = {
        16: ("chat message", "scope"),
        17: ("gate", "HEAD"),
        18: ("empty result", "weekly"),
        19: ("trace", "tags"),
        20: ("review.md", "resume"),
        21: ("uncommitted", "discard"),
        22: ("dominated", "downgrad"),
        23: ("whole repository", "multi-id"),
        24: ("machine-readable", "reload"),
        25: ("out of memory", "idle host"),
        26: ("wake-up", "reset"),
        27: ("none applies", "explicit entry"),
        28: ("runaway", "memcap-runner"),
        29: ("not fair", "ticket queue"),
        30: ("address space", "ulimit -d"),
        31: ("relays", "notification"),
        32: ("all or nothing", "qualified successor"),
        33: ("backtick", "quote"),
        34: ("lowest free block", "above the highest"),
        35: ("setup", "no spec content"),
        36: ("ignored", "exact command"),
        37: ("projection", "85-90"),
        38: ("plan first", "task filter"),
        39: ("test_filter", "fails in CI"),
        40: ("reverse", "already applied"),
        41: ("comment", "gitleaks:allow"),
        42: ("misspelt", "legacy"),
        43: ("raw facts", "GATES_HEAD"),
        44: ("two valid refuting votes", "skeptics"),
        45: ("evidence", "allowPassing"),
        46: ("parent directory", "pending-owner"),
        47: ("SIGKILL", "not a catch"),
        48: ("heavy.sh", "main checkout"),
    }

    def test_entries_16_to_28(self) -> None:
        text = read("docs/LESSONS.md")
        rows = {}
        for line in text.splitlines():
            m = re.match(r"^\|\s*(\d+)\s*\|(.*)\|\s*$", line)
            if m:
                rows[int(m.group(1))] = [c.strip() for c in m.group(2).split("|")]
        for n, words in self.EXPECTED.items():
            with self.subTest(entry=n):
                self.assertTrue(n in rows, f"LESSONS.md has no row {n}")
                cells = rows[n]
                self.assertEqual(len(cells), 3, f"row {n}: what happened | rule | where")
                for cell in cells:
                    self.assertGreater(len(cell), 10, f"row {n} has an empty cell")
                row = " ".join(cells).lower()
                for word in words:
                    self.assertIn(word.lower(), row)
        self.assertEqual(sorted(rows), list(range(1, max(rows) + 1)), "entries are numbered without gaps")


def key_paths(d: dict, prefix: str = ""):
    for k, v in d.items():
        p = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict) and v:
            yield from key_paths(v, p)
        else:
            yield p


def documented(path: str, doc: str, lines: list) -> bool:
    """`a.b.c` literally, or a line naming the parent (`a.b` or `a.b.x`) and the leaf as a word."""
    if f"`{path}`" in doc or f"`{path}." in doc:
        return True
    parent, _, leaf = path.rpartition(".")
    if not parent:
        return False
    leaf_re = re.compile(rf"(?<![\w-]){re.escape(leaf)}(?![\w-])")
    for line in lines:
        if (f"`{parent}." in line or f"`{parent}`" in line) and leaf_re.search(line):
            return True
    return False


class ConfigDocTest(unittest.TestCase):
    """AC 2."""

    def test_every_default_key_documented(self) -> None:
        doc = read("docs/CONFIG.md")
        lines = doc.splitlines()
        missing = [p for p in key_paths(cfgmod.DEFAULTS) if not documented(p, doc, lines)]
        self.assertEqual(missing, [], "keys of config.DEFAULTS missing from docs/CONFIG.md")

    def test_environment_variables(self) -> None:
        doc = read("docs/CONFIG.md")
        for var in ("SDD_BASE_REF", "CI=true", "SDD_FEATURE"):
            self.assertIn(var, doc)


class PlaybookPipelineAndPausesTest(unittest.TestCase):
    """PLAYBOOK sections from the 2026-11-13 sync: changing the pipeline safely, unattended usage-limit pauses."""

    def test_sections(self) -> None:
        text = read("docs/PLAYBOOK.md")
        safe = section(text, r"(?:\d+\.\s+)?Changing the pipeline safely")
        for word in ("opt-in", "byte-identical", "tests/pipeline_cachekeys.mjs", "--baseline", "stub runtime"):
            self.assertIn(word, safe)
        pauses = section(text, r"(?:\d+\.\s+)?Usage-limit pauses while unattended")
        for word in ("one-shot wake-up", "reset time", "two heavy build or test jobs", "idle host", "without the compiler-cache wrapper", "registry dependencies",
                     "heavy-lock.sh", "memcap-runner.sh", "memguard.py", "CARGO_TARGET_<TRIPLE>_RUNNER", "never remove it", "slow end-to-end tests",
                     "SDD_HEAVY_BUDGET_MB", "SDD_HEAVY_JOB_MB", "jobs.log", "MemoryMax"):
            self.assertIn(word, pauses)
        self.assertTrue((KIT / "tests" / "pipeline_cachekeys.mjs").is_file())


class ProcedureStepsDocTest(unittest.TestCase):
    """Procedure step tables: PLAYBOOK section with the migration note, template section, agents."""

    def test_playbook(self) -> None:
        body = section(read("docs/PLAYBOOK.md"), r"(?:\d+\.\s+)?Procedure step tables")
        for word in ("trace.procedure_steps.enabled", "Step table: <doc> §<section>", "Responsibilities",
                     "No step table for <doc> §<section>: <reason>", "No step table: <reason>", "Coverage report",
                     "sdd callflow", "out of order", "golden-scenario rule", "Migration note", "feature-wide line"):
            self.assertIn(word, body)

    def test_template_and_agents(self) -> None:
        spec = read("templates/specs/templates/spec.md")
        for word in ("### Procedure steps", "| Step | Description | Mapping |", "| Owner | Responsibility | Source | Requirements |",
                     "No step table for <doc> §<section>: <reason>", "`No step table: <reason>`", "owned by F<NNN>: <reason>",
                     "out of scope: <reason>", "(Source: <doc> §<section> step <label>)", "Golden-scenario rule", "sdd callflow"):
            self.assertIn(word, spec)
        for agent in ("spec-author", "spec-critic", "test-adequacy-reviewer"):
            text = read(f"claude/agents/{agent}.md")
            self.assertTrue("trace.procedure_steps.enabled" in text or "step table" in text, agent)


class PlaybookAdoptionTest(unittest.TestCase):
    """AC 3."""

    def test_three_steps_in_order(self) -> None:
        body = section(read("docs/PLAYBOOK.md"), r"(?:\d+\.\s+)?Adopting the gates on an existing codebase")
        self.assertTrue(body, "PLAYBOOK has no 'Adopting the gates on an existing codebase' section")
        steps = [m.start() for m in re.finditer(r"^\d\.\s+\*\*", body, re.M)]
        self.assertGreaterEqual(len(steps), 3)
        markers = ("sdd hooks install", "sdd baseline --init", "remediation feature")
        positions = [body.find(m) for m in markers]
        for marker, pos in zip(markers, positions):
            self.assertGreaterEqual(pos, 0, marker)
        self.assertEqual(positions, sorted(positions), "build gates and hooks, then baseline, then remediation")
        # Each step's marker lives in its own numbered step.
        owners = [max(i for i, s in enumerate(steps) if s <= pos) for pos in positions]
        self.assertEqual(owners, [0, 1, 2])
        self.assertIn("before new feature work", body)


# --- AC 4: repository hygiene scan -------------------------------------------------------

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})")
URL_RE = re.compile(r"https?://([A-Za-z0-9.-]+)(/[^\s)>\]\"'`]*)?")
RESERVED_DOMAINS = ("example.com", "example.org", "example.net")
# Public documentation and tool hosts the kit may link to.
URL_HOSTS = {
    "json.schemastore.org",
    "deptry.com",
    "www.conventionalcommits.org",
    "conventionalcommits.org",
    "docs.anthropic.com",
    "docs.claude.com",
    "claude.com",
    "keepachangelog.com",
    "semver.org",
    "github.com",
    "gitlab.com",
    "bitbucket.org",
    "codeberg.org",
}
FORGES = {"github.com", "gitlab.com", "bitbucket.org", "codeberg.org"}
# Public projects whose forge URLs may appear: "owner/repo" on a forge host.
FORGE_PROJECTS = {
    "github.com": {"gitleaks/gitleaks", "mozilla/sccache", "sourcefrog/cargo-mutants", "stryker-mutator/stryker-js"},
}
PROHIBITION_RE = re.compile(
    r"\b(never|no|not|block(s|ed)?|den(y|ies|ied)|refus\w*|forbid\w*|without|reject\w*|around)\b", re.I
)
# Guard code and its tests: the places that must name the flag to block it.
GUARD_FILES = {"tools/sdd/sddkit/hooks.py", "claude/settings.json"}


def tracked_text_files():
    out = subprocess.run(
        ["git", "-C", str(KIT), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True, check=True,
    ).stdout.decode()
    for rel in sorted(set(filter(None, out.split("\0")))):
        path = KIT / rel
        if not path.is_file():
            continue
        data = path.read_bytes()
        if b"\0" in data[:4096]:
            continue
        try:
            yield rel, data.decode("utf-8")
        except UnicodeDecodeError:
            continue


class HygieneScanTest(unittest.TestCase):
    """AC 4."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.files = list(tracked_text_files())

    def test_no_verify_only_prohibited(self) -> None:
        bad = []
        for rel, text in self.files:
            if rel in GUARD_FILES or rel.startswith("tests/"):
                continue
            lines = text.splitlines()
            for i, line in enumerate(lines):
                if "--no-verify" not in line:
                    continue
                window = " ".join(lines[max(0, i - 2): i + 1])
                if not PROHIBITION_RE.search(window):
                    bad.append(f"{rel}:{i + 1}: {line.strip()[:120]}")
        self.assertEqual(bad, [], "--no-verify outside a prohibition or guard code")

    def test_email_addresses(self) -> None:
        bad = []
        for rel, text in self.files:
            for m in EMAIL_RE.finditer(text):
                local = m.group(0).split("@", 1)[0]
                domain = m.group(1).lower()
                reserved = domain in RESERVED_DOMAINS or domain.endswith(tuple("." + d for d in RESERVED_DOMAINS))
                if not (reserved or local == "noreply" and "anthropic" in domain):
                    bad.append(f"{rel}: {m.group(0)}")
        self.assertEqual(bad, [], "e-mail addresses must be noreply@ or a reserved example domain")

    def test_urls(self) -> None:
        bad = []
        for rel, text in self.files:
            for m in URL_RE.finditer(text):
                host = m.group(1).lower().rstrip(".")
                path = m.group(2) or ""
                if host not in URL_HOSTS:
                    bad.append(f"{rel}: host {host}")
                    continue
                if REFERENCE_PROJECT in (host + path).lower():
                    bad.append(f"{rel}: {m.group(0)} links the reference project")
                    continue
                if host in FORGES:
                    parts = [p for p in path.split("/") if p]
                    pair = "/".join(parts[:2])
                    if parts and pair not in FORGE_PROJECTS.get(host, set()):
                        bad.append(f"{rel}: {m.group(0)} (forge owner/repo not on the allowlist)")
        self.assertEqual(bad, [], "URLs must be public documentation or tool hosts")

    def test_reference_project_only_in_docs(self) -> None:
        bad = []
        for rel, text in self.files:
            if REFERENCE_PROJECT not in text.lower():
                continue
            if not (rel == "README.md" or rel.startswith("docs/") and rel.endswith(".md")):
                bad.append(rel)
        self.assertEqual(bad, [], f"{REFERENCE_PROJECT} may appear only in README.md and docs/*.md")


# --- AC 5: examples and tools table ----------------------------------------------------

WRAPPERS = {"npx", "cargo", "go", "npm", "pnpm", "yarn", "uv", "poetry", "coverage", "mutmut"}
NOOP = {"test", "true", "false", "!"}
COMMAND_KEYS = {"command", "run", "run_component", "red_check", "lookup_command", "after_union"}
BUILTIN_CHECKS = {"size", "arch", "coverage", "mutation", "baseline", "commits", "secrets", "protected"}


def commands(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.startswith("_"):
                continue
            if k in COMMAND_KEYS and isinstance(v, str) and v:
                yield v
            else:
                yield from commands(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from commands(v)


def tools_of(cmd: str):
    """The programs a command runs: `cargo llvm-cov`, `pytest`, `sdd check docs`, `scripts/x.sh`."""
    for seg in re.split(r"&&|\|\||;|\||\$\(|[<>]", cmd):
        toks = [t.strip("\"'()") for t in seg.split()]
        toks = [t for t in toks if t and not re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t)]
        while toks and toks[0] in NOOP:
            toks = toks[1:]
        toks = [t for t in toks if t not in ("-z", "-I", "-q")]
        if not toks or toks[0][0] in "/-{.&" or toks[0].isdigit():
            continue
        if any(t.endswith("sdd.py") for t in toks[:2]):
            rest = [t for t in toks if not t.endswith("sdd.py") and t not in ("python3", "python")]
            yield "sdd " + " ".join(rest[:2] if rest[0] == "check" else rest[:1])
        elif toks[0] in ("python3", "python") and len(toks) > 2 and toks[1] == "-m":
            yield f"{toks[0]} -m {toks[2]}"
        elif toks[0] in WRAPPERS and len(toks) > 1 and not toks[1].startswith("-"):
            yield f"{toks[0]} {toks[1]}"
        else:
            yield toks[0]


class ExamplesDocumentedTest(unittest.TestCase):
    """AC 5, first half."""

    def test_every_example_command_in_adapting(self) -> None:
        doc = read("docs/ADAPTING.md")
        missing = set()
        for path in sorted((KIT / "examples" / "configs").glob("*.json")):
            for cmd in commands(json.loads(path.read_text())):
                for tool in tools_of(cmd):
                    if tool.startswith("sdd check "):
                        name = tool.split()[2]
                        ok = "sdd check" in doc and (name in BUILTIN_CHECKS or f"`{name}`" in doc)
                    else:
                        ok = tool in doc
                    if not ok:
                        missing.add(f"{path.name}: {tool} ({cmd})")
        self.assertEqual(sorted(missing), [], "commands of examples/configs not described in docs/ADAPTING.md")

    def test_tool_extraction(self) -> None:
        self.assertEqual(list(tools_of('test -z "$(gofmt -l .)"')), ["gofmt"])
        self.assertEqual(list(tools_of("FUZZ_BUDGET_SECS=300 scripts/fuzz-changed.sh")), ["scripts/fuzz-changed.sh"])
        self.assertEqual(list(tools_of("python3 -I tools/sdd/sdd.py check docs")), ["sdd check docs"])
        self.assertEqual(list(tools_of("python3 -I tools/sdd/sdd.py trace --check")), ["sdd trace"])
        self.assertEqual(list(tools_of("uv lock >/dev/null 2>&1 || poetry lock")), ["uv lock", "poetry lock"])


def cli_subcommands():
    src = read("tools/sdd/sddkit/cli.py")
    top = {}
    for m in re.finditer(r'^\s*(\w+)\s*=\s*sub\.add_parser\("([\w-]+)"', src, re.M):
        top[m.group(1)] = m.group(2)
    names = set(top.values())
    for m in re.finditer(r"^\s*\w+\s*=\s*(\w+)_sub\.add_parser\(\"([\w-]+)\"", src, re.M):
        parent = top.get(m.group(1))
        if parent:
            names.add(f"{parent} {m.group(2)}")
    return names


class ReadmeToolsTableTest(unittest.TestCase):
    """AC 5, second half."""

    def test_every_subcommand_listed(self) -> None:
        table = section(read("README.md"), "The tools")
        first_cells = " ".join(line.split("|")[1] for line in table.splitlines() if line.startswith("| `sdd"))
        names = cli_subcommands()
        self.assertIn("hooks install", names)
        self.assertIn("routing refit", names)
        missing = sorted(n for n in names if f"`sdd {n}" not in first_cells and f"sdd {n}" not in first_cells)
        self.assertEqual(missing, [], "sdd subcommands missing from README's tools table")


class ScopeRuleTest(unittest.TestCase):
    """S10: the scope rule is in every agent definition and in every skill that starts agents."""

    def test_agents_and_skills(self) -> None:
        agents = sorted((KIT / "claude" / "agents").glob("*.md"))
        self.assertEqual(len(agents), 12)
        for p in agents:
            self.assertIn("SCOPE RULE (hard rule)", p.read_text(encoding="utf-8"), p.name)
        for skill in ("review-feature", "feature", "gates"):
            self.assertIn("SCOPE RULE (hard rule)", read(f"claude/skills/{skill}/SKILL.md"), skill)


if __name__ == "__main__":
    unittest.main()
