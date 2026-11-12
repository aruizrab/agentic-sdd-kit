"""Configuration for the SDD kit: ``sdd.config.json`` at the project root.

Everything project-specific lives in that one file. The defaults below are
deliberately language-neutral; a project overrides what it needs. Unknown keys
are preserved (the pipeline script receives the whole object), but
``validate`` reports keys it does not understand so that typos are caught.

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
        "implementer": ["tests/**", "**/tests/**", "**/*.test.*", "**/*_test.*", "**/test_*.py"],
    },
    "formatters": [],
    "stop_check": {"command": "", "changed_globs": [], "roles": ["implementer"], "timeout": 240},
    "models": {
        "mechanical": "",
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
    problems: List[str] = []
    known = set(DEFAULTS)
    for key in cfg:
        if key.startswith("_"):
            continue
        if key not in known:
            problems.append(f"unknown top-level key {key!r}")
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
    for mode in ("fast", "full"):
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
    models = cfg["models"]
    for tier in ("mechanical", "mid", "strong", "top"):
        value = models.get(tier, "")
        if value and not re.match(r"^[a-z0-9][a-z0-9.-]*$", value):
            problems.append(f"models.{tier}: use an explicit model id (lower-case, digits, dots, dashes), got {value!r}")
    if not cfg["commit"]["trailers"]:
        problems.append("commit.trailers is empty: add at least the attribution trailer line the project uses")
    return problems


def public(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """The configuration without the private ``_root``/``_path`` keys."""
    return {k: v for k, v in cfg.items() if not k.startswith("_")}


def pipeline_view(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """The subset the Workflow script needs (passed as ``args.config``)."""
    keys = [
        "project", "paths", "features", "requirements", "components", "tests", "sources",
        "models", "pipeline", "commit", "merge", "override_env",
    ]
    view = {k: copy.deepcopy(cfg[k]) for k in keys}
    view["gates"] = {
        "fast": [g["name"] for g in cfg["gates"]["fast"]],
        "full": [g["name"] for g in cfg["gates"]["full"]],
    }
    view["root"] = cfg["_root"]
    return view
