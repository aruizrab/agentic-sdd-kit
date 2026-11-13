"""Configuration for the SDD kit: ``sdd.config.json`` at the project root.

Everything project-specific lives in that one file. The defaults below are
deliberately language-neutral; a project overrides what it needs. Unknown keys
are preserved (the pipeline script receives the whole object), but
``validate`` reports keys it does not understand (at every level of the known
sections) so that typos such as ``gates.fsat`` are caught.

The file is plain JSON (standard library on every platform, readable by the
Python tools, the hooks and the Node workflow script without dependencies).
"""

from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import routing

CONFIG_NAME = "sdd.config.json"

DEFAULTS: Dict[str, Any] = {
    "project": {
        "name": "my-project",
        "description": "",
        "languages": [],
        "threat_model": "",
    },
    "paths": {
        "features_dir": "specs/features",
        "templates_dir": "specs/templates",
        "roadmap": "docs/ROADMAP.md",
        "constitution": "docs/CONSTITUTION.md",
        "development": "docs/DEVELOPMENT.md",
        "logs_dir": ".sdd/logs",
        "worktree_root": ".wt",
        "build_dirs": [],
        "tools_dir": "tools/sdd",
    },
    "features": {
        "id_prefix": "F",
        "id_digits": 3,
        "branch_prefix": "feat/",
        "main_branch": "main",
        "roadmap_milestone_regex": r"^###\s+(M\d+)\s*[—–-]+\s*(.+?)\s*$",
        "roadmap_feature_regex": r"^####\s+(F\d{3})\s+(\S+)(.*)$",
        "done_without_spec": [],
    },
    "requirements": {
        "id_regex": r"[A-Z][A-Z0-9]*(?:-[A-Z0-9.]+)*-R\d{2,3}",
        "priorities": ["must", "should", "may"],
        "block_size": 10,
        "budget": {"min": 15, "max": 45},
        "clarification_marker": "[NEEDS CLARIFICATION:",
        "statuses": ["Draft", "Approved", "Implemented"],
        "enforce_for_statuses": ["Implemented"],
        "require_impl_for_statuses": ["Implemented"],
    },
    "trace": {
        "scan_globs": ["src/**/*", "tests/**/*"],
        "exclude_globs": ["**/node_modules/**", "**/target/**", "**/.git/**", "**/dist/**", "**/build/**"],
        "implements_keywords": ["Implements"],
        "verifies_keywords": ["Verifies"],
        "attribute_forms": True,
    },
    "components": {
        "path_regex": r"^src/([^/]+)/",
        "root": "workspace",
    },
    "gates": {
        "fast": [],
        "full": [],
        "nightly": [],
    },
    "quality": {
        "baseline_file": "sdd.baseline.json",
        "base_ref": "",
        "generated": [],
        "test_globs": [],
        "languages": {},
        "tool_dirs": [],
        "size": {
            "globs": [],
            "exclude": [],
            "function_warn": 0,
            "function_fail": 80,
            "file_warn": 600,
            "file_fail": 1000,
        },
        "arch": {"modules": [], "exclude": []},
        "coverage": {
            "command": "",
            "report": "",
            "format": "lcov",
            "globs": [],
            "unmeasured_ok": [],
            "ok_exits": [0],
            "requires": [],
            "install": "",
            "timeout": 3600,
        },
        "mutation": {
            "command": "",
            "report": "",
            "format": "regex",
            "survivor_regex": "",
            "ok_exits": [0],
            "globs": [],
            "requires": [],
            "install": "",
            "timeout": 3600,
        },
        "checks": [],
        "secrets": {"gitleaks": True, "patterns": [], "allow_globs": []},
        "hooks": {
            "types": ["build", "chore", "ci", "docs", "feat", "fix", "perf", "refactor", "revert", "style", "test"],
            "scope_regex": "",
            "required_trailers": [],
            "required_trailers_env": "CLAUDECODE",
            "trailer_authors": "",
            "pre_commit": [],
            "commit_tests": True,
            "skip_tests_for_types": ["test"],
            "test_timeout": 300,
            "budget_seconds": 60,
        },
    },
    "tests": {
        "run": "",
        "run_component": "",
        "red_check": "",
    },
    "sources": {
        "kind": "product-requirements",
        "description": "",
        "citation_format": "(Source: <document> §<section>)",
        "lookup_command": "",
        "text_dir": "",
        "heading_regex": r"^#+\s+{clause}\b",
    },
    "protected_paths": [
        {"glob": "docs/CONSTITUTION.md", "reason": "the constitution changes only via a human-approved ADR"},
        {"glob": ".claude/**", "reason": "agent, skill, hook and workflow configuration is human-owned"},
        {"glob": "sdd.config.json", "reason": "the SDD configuration is human-owned"},
    ],
    "append_only_paths": [],
    "role_write_scopes": {
        "spec-author": ["specs/features/**"],
        "architect-planner": ["specs/features/**"],
        "product-owner": ["specs/features/**"],
        "integrator": ["specs/features/**"],
    },
    "role_denied_paths": {
        "implementer": ["tests/**", "**/tests/**", "**/*.test.*", "**/*_test.*", "**/test_*.py",
                        "tools/sdd/githooks/**", "tools/sdd/sddkit/**", "tools/sdd/sdd.py"],
        "test-author": ["tools/sdd/githooks/**", "tools/sdd/sddkit/**", "tools/sdd/sdd.py"],
    },
    "formatters": [],
    "build_cache": {"env": {}},
    "stop_check": {"command": "", "changed_globs": [], "roles": ["implementer"], "timeout": 240},
    "models": {
        "mechanical": "",
        "gate": "",
        "mid": "",
        "strong": "",
        "top": "",
    },
    "pipeline": {
        "max_parallel_features": 2,
        "max_critic_fix_rounds": 2,
        "max_gate_fix_rounds": 3,
        "skeptics": 2,
        "max_build_jobs": 4,
        "gate_poll_seconds": 120,
        "routing": "tiers",
        "models_table": "sdd.models.json",
        "security_sensitive_hint": "authentication, authorization, cryptography, secrets, session or token handling, input parsing of untrusted data",
    },
    "merge": {
        "union_manifests": [],
        "after_union": "",
        "lockfiles": [],
    },
    "commit": {
        "trailers": [],
        "scope_format": "{feature}",
    },
    "override_env": "SDD_ALLOW_PROTECTED",
}


# Sections whose keys are free-form (role names), and known keys that have no default.
_FREE_FORM_SECTIONS = {"role_write_scopes", "role_denied_paths", "quality.languages", "build_cache.env"}
_BUILTIN_CHECKS = ("size", "arch", "coverage", "mutation", "baseline", "commits", "secrets")
_GATE_KEYS = {"name", "command", "timeout", "optional", "skip_if_missing", "env", "install", "skip_exit"}
_EXTRA_KEYS = {"components": {"word"}, "pipeline": {"role_tiers"}}


def _unknown_keys(node: Dict[str, Any], defaults: Dict[str, Any], path: str = "") -> List[str]:
    """Keys in ``node`` (recursively, dict sections only) that the defaults do not know."""
    out: List[str] = []
    allowed = set(defaults) | _EXTRA_KEYS.get(path, set())
    for key, value in node.items():
        if not path and key.startswith("_"):
            continue
        full = f"{path}.{key}" if path else key
        if key not in allowed:
            out.append(f"unknown {'top-level ' if not path else ''}key {full!r}")
            continue
        if full in _FREE_FORM_SECTIONS:
            continue
        if isinstance(value, dict) and isinstance(defaults.get(key), dict):
            out.extend(_unknown_keys(value, defaults[key], full))
    return out


class ConfigError(Exception):
    """Raised for a missing or invalid configuration."""


def _merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def find_root(start: Optional[str] = None) -> Path:
    """Walk up from ``start`` (or the cwd) until a directory holds ``sdd.config.json``.

    ``SDD_ROOT`` in the environment wins. Falls back to the git top level when no
    config is found, so that commands still work in a freshly installed repo.
    """
    env = os.environ.get("SDD_ROOT")
    if env:
        return Path(env).resolve()
    here = Path(start or os.getcwd()).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / CONFIG_NAME).is_file():
            return candidate
    for candidate in [here, *here.parents]:
        if (candidate / ".git").exists():
            return candidate
    return here


def load(root: Optional[Path] = None, path: Optional[Path] = None) -> Dict[str, Any]:
    """Load the configuration merged over the defaults.

    A missing file yields the defaults (so `sdd init`, `sdd config` and the
    tests work before the file exists).
    """
    root = root or find_root()
    path = path or (root / CONFIG_NAME)
    data: Dict[str, Any] = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"{path}: invalid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigError(f"{path}: top level must be a JSON object")
    cfg = _merge(DEFAULTS, data)
    cfg["_root"] = str(root)
    cfg["_path"] = str(path)
    cfg["_present"] = path.is_file()
    return cfg


def validate(cfg: Dict[str, Any]) -> List[str]:
    """Return a list of problems (empty when the configuration is usable)."""
    problems: List[str] = _unknown_keys(cfg, DEFAULTS)
    try:
        re.compile(cfg["requirements"]["id_regex"])
    except re.error as exc:
        problems.append(f"requirements.id_regex does not compile: {exc}")
    for name in ("roadmap_milestone_regex", "roadmap_feature_regex"):
        try:
            re.compile(cfg["features"][name])
        except re.error as exc:
            problems.append(f"features.{name} does not compile: {exc}")
    if cfg["components"].get("path_regex"):
        try:
            re.compile(cfg["components"]["path_regex"])
        except re.error as exc:
            problems.append(f"components.path_regex does not compile: {exc}")
    for mode in ("fast", "full", "nightly"):
        gates = cfg["gates"].get(mode)
        if not isinstance(gates, list):
            problems.append(f"gates.{mode} must be a list")
            continue
        seen = set()
        for i, gate in enumerate(gates):
            if not isinstance(gate, dict) or not gate.get("name") or not gate.get("command"):
                problems.append(f"gates.{mode}[{i}] needs 'name' and 'command'")
                continue
            if gate["name"] in seen:
                problems.append(f"gates.{mode}: duplicate gate name {gate['name']!r}")
            seen.add(gate["name"])
            if "timeout" in gate and not isinstance(gate["timeout"], (int, float)):
                problems.append(f"gates.{mode}[{i}] timeout must be a number of seconds")
            if "skip_exit" in gate and not _exit_code(gate["skip_exit"]):
                problems.append(f"gates.{mode}[{i}] skip_exit must be an integer 0-255")
            for key in sorted(set(gate) - _GATE_KEYS):
                problems.append(f"gates.{mode}[{i}]: unknown key {key!r}")
    if not cfg["gates"]["fast"] and not cfg["gates"]["full"]:
        problems.append("no gates configured (gates.fast / gates.full); the kit cannot verify anything")
    for i, entry in enumerate(cfg["protected_paths"]):
        if not isinstance(entry, dict) or "glob" not in entry:
            problems.append(f"protected_paths[{i}] must be an object with 'glob' (and 'reason')")
    for i, entry in enumerate(cfg["append_only_paths"]):
        if not isinstance(entry, dict) or "glob" not in entry:
            problems.append(f"append_only_paths[{i}] must be an object with 'glob' (and 'allowed_roles')")
    for i, entry in enumerate(cfg["formatters"]):
        if not isinstance(entry, dict) or "glob" not in entry or "command" not in entry:
            problems.append(f"formatters[{i}] must be an object with 'glob' and 'command'")
    problems.extend(_validate_quality(cfg["quality"]))
    cache_env = cfg["build_cache"].get("env")
    if not isinstance(cache_env, dict):
        problems.append("build_cache.env must be an object of environment variables")
    else:
        for key, value in cache_env.items():
            if not isinstance(value, str):
                problems.append(f"build_cache.env.{key} must be a string")
    problems.extend(routing.config_problems(cfg))
    if not cfg["commit"]["trailers"]:
        problems.append("commit.trailers is empty: add at least the attribution trailer line the project uses")
    for i, trailer in enumerate(cfg["commit"]["trailers"]):
        if not isinstance(trailer, str) or not trailer.strip():
            problems.append(f"commit.trailers[{i}] must be a non-empty string")
        elif re.search(r"<[^<>@]*>", trailer.split("<noreply@", 1)[0]) or re.search(r"<[^<>]*\s[^<>]*>", trailer):
            problems.append(f"commit.trailers[{i}]: still the template placeholder ({trailer!r}); set the real attribution line")
    return problems


def _exit_code(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 255


def _regex(problems: List[str], where: str, pattern: Any, groups: tuple = ()) -> None:
    if not isinstance(pattern, str):
        problems.append(f"{where} must be a regex string")
        return
    try:
        rx = re.compile(pattern)
    except re.error as exc:
        problems.append(f"{where} does not compile: {exc}")
        return
    for g in groups:
        if g not in rx.groupindex:
            problems.append(f"{where} needs the named group (?P<{g}>...)")


def _validate_quality(q: Dict[str, Any]) -> List[str]:
    problems: List[str] = []
    for key in ("generated", "test_globs", "tool_dirs"):
        if not isinstance(q.get(key), list):
            problems.append(f"quality.{key} must be a list of globs")
    for ext, lang in (q.get("languages") or {}).items():
        if lang not in ("rust", "python", "typescript", "go", "none"):
            problems.append(f"quality.languages[{ext!r}]: unknown language {lang!r} (rust, python, typescript, go or none)")
    for key in ("function_warn", "function_fail", "file_warn", "file_fail"):
        v = q["size"].get(key)
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            problems.append(f"quality.size.{key} must be a non-negative integer (0 disables)")
    modules = q["arch"].get("modules") or []
    names = [m.get("name") for m in modules if isinstance(m, dict)]
    for i, m in enumerate(modules):
        where = f"quality.arch.modules[{i}]"
        if not isinstance(m, dict) or not m.get("name") or not isinstance(m.get("paths"), list):
            problems.append(f"{where} needs 'name' and a 'paths' list")
            continue
        for key in sorted(set(m) - {"name", "paths", "imports", "may_depend_on", "forbid"}):
            problems.append(f"{where}: unknown key {key!r}")
        for j, rx in enumerate(m.get("imports") or []):
            _regex(problems, f"{where}.imports[{j}]", rx)
        for dep in m.get("may_depend_on") or []:
            if dep not in names:
                problems.append(f"{where}.may_depend_on: no module named {dep!r}")
        for j, f in enumerate(m.get("forbid") or []):
            if not isinstance(f, dict) or "pattern" not in f:
                problems.append(f"{where}.forbid[{j}] needs 'pattern' (and 'reason')")
            else:
                _regex(problems, f"{where}.forbid[{j}].pattern", f["pattern"])
    if len(set(names)) != len(names):
        problems.append("quality.arch.modules: duplicate module names")
    for tool in ("coverage", "mutation"):
        t = q[tool]
        if not all(_exit_code(c) for c in t.get("ok_exits") or []):
            problems.append(f"quality.{tool}.ok_exits must be integers 0-255")
    if q["coverage"]["format"] not in ("lcov", "cobertura"):
        problems.append("quality.coverage.format must be 'lcov' or 'cobertura'")
    if q["mutation"]["format"] not in ("regex", "mutation-testing-report"):
        problems.append("quality.mutation.format must be 'regex' or 'mutation-testing-report'")
    if q["mutation"]["format"] == "regex" and q["mutation"]["survivor_regex"]:
        _regex(problems, "quality.mutation.survivor_regex", q["mutation"]["survivor_regex"], ("file", "mutation"))
    seen = set()
    for i, c in enumerate(q.get("checks") or []):
        where = f"quality.checks[{i}]"
        if not isinstance(c, dict) or not c.get("name") or not c.get("command"):
            problems.append(f"{where} needs 'name' and 'command'")
            continue
        for key in sorted(set(c) - {"name", "command", "violation_regex", "baseline", "requires", "install", "timeout", "ok_exits"}):
            problems.append(f"{where}: unknown key {key!r}")
        if c["name"] in _BUILTIN_CHECKS:
            problems.append(f"{where}: name {c['name']!r} shadows a built-in check")
        if c["name"] in seen:
            problems.append(f"quality.checks: duplicate check name {c['name']!r}")
        seen.add(c["name"])
        if c.get("violation_regex"):
            _regex(problems, f"{where}.violation_regex", c["violation_regex"], ("file", "message"))
        if not all(_exit_code(x) for x in c.get("ok_exits") or []):
            problems.append(f"{where}.ok_exits must be integers 0-255")
    for i, p in enumerate(q["secrets"].get("patterns") or []):
        if not isinstance(p, dict) or not p.get("id"):
            problems.append(f"quality.secrets.patterns[{i}] needs 'id' and 'regex'")
        else:
            _regex(problems, f"quality.secrets.patterns[{i}].regex", p.get("regex"))
    h = q["hooks"]
    if h.get("scope_regex"):
        _regex(problems, "quality.hooks.scope_regex", h["scope_regex"])
    for i, rx in enumerate(h.get("required_trailers") or []):
        _regex(problems, f"quality.hooks.required_trailers[{i}]", rx)
    if h.get("trailer_authors"):
        _regex(problems, "quality.hooks.trailer_authors", h["trailer_authors"])
    for i, step in enumerate(h.get("pre_commit") or []):
        if not isinstance(step, dict) or not step.get("name") or not step.get("command"):
            problems.append(f"quality.hooks.pre_commit[{i}] needs 'name' and 'command'")
    for key in ("test_timeout", "budget_seconds"):
        if not isinstance(h.get(key), (int, float)) or h.get(key) <= 0:
            problems.append(f"quality.hooks.{key} must be a positive number of seconds")
    return problems


def public(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """The configuration without the private ``_root``/``_path`` keys."""
    return {k: v for k, v in cfg.items() if not k.startswith("_")}


def pipeline_view(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """The subset the Workflow script needs (passed as ``args.config``)."""
    keys = [
        "project", "paths", "features", "requirements", "components", "tests", "sources",
        "models", "pipeline", "commit", "merge", "override_env", "build_cache",
    ]
    view = {k: copy.deepcopy(cfg[k]) for k in keys}
    view["gates"] = {
        "fast": [g["name"] for g in cfg["gates"]["fast"]],
        "full": [g["name"] for g in cfg["gates"]["full"]],
        "nightly": [g["name"] for g in cfg["gates"].get("nightly") or []],
    }
    view["root"] = cfg["_root"]
    view["routing"] = routing.pipeline_routing(Path(cfg["_root"]), cfg)
    return view
