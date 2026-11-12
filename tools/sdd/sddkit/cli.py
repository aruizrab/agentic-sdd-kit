"""Command-line entry point: ``python3 -I tools/sdd/sdd.py <command> ...``."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import List, Optional

from . import config as cfgmod
from . import gates, hooks, new_feature, progress, source, specs, trace, union_merge

VERSION = "0.1.0"


def _root(args: argparse.Namespace) -> Path:
    return Path(args.root).resolve() if getattr(args, "root", None) else cfgmod.find_root()


def _cfg(args: argparse.Namespace):
    root = _root(args)
    return root, cfgmod.load(root)


def cmd_config(args: argparse.Namespace) -> int:
    root, cfg = _cfg(args)
    if args.init:
        target = root / cfgmod.CONFIG_NAME
        if target.exists() and not args.force:
            print(f"{target} exists (use --force to overwrite)", file=sys.stderr)
            return 1
        example = Path(__file__).resolve().parent.parent.parent.parent / "templates" / "sdd.config.json"
        src = example if example.is_file() else None
        text = src.read_text(encoding="utf-8") if src else json.dumps(cfgmod.public(cfgmod.load(root)), indent=2) + "\n"
        target.write_text(text, encoding="utf-8")
        print(f"wrote {target}")
        return 0
    problems = cfgmod.validate(cfg)
    if args.validate:
        if not cfg["_present"]:
            print(f"no {cfgmod.CONFIG_NAME} at {root} (defaults in effect)")
        for p in problems:
            print(f"error: {p}")
        print("config: " + ("OK" if not problems else f"{len(problems)} problem(s)"))
        return 1 if problems else 0
    if args.pipeline:
        print(json.dumps(cfgmod.pipeline_view(cfg), indent=2))
        return 0
    if args.get:
        node = cfg
        for part in args.get.split("."):
            if not isinstance(node, dict) or part not in node:
                print(f"no such key: {args.get}", file=sys.stderr)
                return 1
            node = node[part]
        print(json.dumps(node, indent=2) if isinstance(node, (dict, list)) else node)
        return 0
    print(json.dumps(cfgmod.public(cfg), indent=2))
    return 0


def cmd_trace(args: argparse.Namespace) -> int:
    root, cfg = _cfg(args)
    return trace.run(root, cfg, args.feature, args.check, args.json, args.allow_missing_impl)


def cmd_gates(args: argparse.Namespace) -> int:
    root, cfg = _cfg(args)
    logs_dir = root / cfg["paths"]["logs_dir"]
    if args.wait:
        return gates.wait(Path(args.wait), float(args.poll), float(args.timeout))
    tag = args.tag or ("fast" if args.fast else "full")
    log_path = Path(args.log) if args.log else logs_dir / f"gates-{tag}.log"
    if args.detached:
        argv = []
        if args.fast:
            argv.append("--fast")
        for o in args.only or []:
            argv += ["--only", o]
        if args.tag:
            argv += ["--tag", args.tag]
        return gates.start_detached(root, argv, log_path)
    extra = {"SDD_FEATURE": args.feature} if args.feature else None
    return gates.run(cfg, root, args.fast, args.only, log_path, extra, quiet=args.no_stdout)


def cmd_new_feature(args: argparse.Namespace) -> int:
    root, cfg = _cfg(args)
    try:
        dest, written = new_feature.create(root, cfg, args.number, args.slug, args.force)
    except (ValueError, FileExistsError, FileNotFoundError) as exc:
        print(f"new-feature: {exc}", file=sys.stderr)
        return 1
    rel = dest.relative_to(root)
    if written:
        print(f"created {rel}/ ({len(written)} file(s))")
    else:
        print(f"{rel}/ already exists; nothing written (use --force to overwrite)")
    lo, hi = new_feature.id_block(args.number, int(cfg["requirements"]["block_size"]))
    print(f"requirement id block for this feature: R{lo}-R{hi} (per clause/area key)")
    if not new_feature.roadmap_has(root, cfg, args.number, args.slug):
        print(f"warning: {cfg['paths']['roadmap']} has no entry '{cfg['features']['id_prefix']}{args.number} {args.slug}'; the roadmap owns feature numbers", file=sys.stderr)
    fid = f"{cfg['features']['id_prefix']}{args.number}"
    print(f"next: git switch -c {cfg['features']['branch_prefix']}{fid}-{args.slug}; then hand {rel}/spec.md to spec-author or run /feature {fid} {args.slug}")
    return 0


def cmd_progress(args: argparse.Namespace) -> int:
    root, cfg = _cfg(args)
    try:
        report = progress.build_report(root, cfg, args.main_ref, args.remote)
    except (RuntimeError, ValueError) as exc:
        print(f"progress: {exc}", file=sys.stderr)
        return 1
    if args.out:
        Path(args.out).write_text(progress.render_json(report), encoding="utf-8")
    if args.json:
        print(progress.render_json(report), end="")
    else:
        print(progress.render_table(report), end="")
    return 0


def cmd_union_merge(args: argparse.Namespace) -> int:
    root, cfg = _cfg(args)
    code, messages = union_merge.resolve(root, cfg, args.files)
    for m in messages:
        print(m, file=sys.stderr if code else sys.stdout)
    return code


def cmd_spec_info(args: argparse.Namespace) -> int:
    root, cfg = _cfg(args)
    path = Path(args.spec)
    if not path.is_absolute():
        path = root / path
    if path.is_dir():
        path = path / "spec.md"
    info = specs.spec_info(path, cfg)
    print(json.dumps(info, indent=2))
    return 0


def cmd_markers(args: argparse.Namespace) -> int:
    root, cfg = _cfg(args)
    path = Path(args.file)
    if not path.is_absolute():
        path = root / path
    n = specs.count_markers(path.read_text(encoding="utf-8", errors="replace"), cfg["requirements"]["clarification_marker"])
    print(n)
    return 0


def cmd_tasks(args: argparse.Namespace) -> int:
    root, cfg = _cfg(args)
    path = Path(args.tasks)
    if not path.is_absolute():
        path = root / path
    if path.is_dir():
        path = path / "tasks.md"
    if not path.is_file():
        print(json.dumps({"ok": False, "error": f"{path} not found", "waves": []}))
        return 1
    plan = specs.parse_tasks(path.read_text(encoding="utf-8", errors="replace"), cfg, include_done=args.all)
    if args.lanes:
        for w in plan["waves"]:
            w["lanes"] = specs.build_lanes(w["tasks"])
    if args.validate:
        known = None
        if not args.no_trace:
            known = list(trace.load(root, cfg).requirements)
        plan["problems"] = specs.validate_tasks(plan, known)
        plan["ok"] = not plan["problems"]
    print(json.dumps(plan, indent=2))
    return 0 if plan["ok"] else 1


def cmd_source(args: argparse.Namespace) -> int:
    root, cfg = _cfg(args)
    if args.what == "clause":
        return source.clause(cfg, root, args.doc, args.clause)
    return source.find(cfg, root, args.doc, args.clause)


def cmd_hook(args: argparse.Namespace) -> int:
    fn = hooks.HOOKS.get(args.name)
    if not fn:
        print(f"unknown hook {args.name}; known: {', '.join(hooks.HOOKS)}", file=sys.stderr)
        return 1
    return fn()


def cmd_doctor(args: argparse.Namespace) -> int:
    root, cfg = _cfg(args)
    ok = True

    def check(cond: bool, msg: str, warn: bool = False) -> None:
        nonlocal ok
        tag = "ok  " if cond else ("warn" if warn else "FAIL")
        if not cond and not warn:
            ok = False
        print(f"[{tag}] {msg}")

    check(sys.version_info >= (3, 8), f"python {sys.version.split()[0]}")
    check(shutil.which("git") is not None, "git on PATH")
    check(shutil.which("jq") is not None, "jq on PATH (hooks use python; jq optional)", warn=True)
    check(cfg["_present"], f"{cfgmod.CONFIG_NAME} present at {root}")
    problems = cfgmod.validate(cfg)
    for p in problems:
        check(False, p)
    check((root / cfg["paths"]["templates_dir"]).is_dir(), f"templates dir {cfg['paths']['templates_dir']}")
    check((root / cfg["paths"]["constitution"]).is_file(), f"constitution {cfg['paths']['constitution']}")
    check((root / cfg["paths"]["roadmap"]).is_file(), f"roadmap {cfg['paths']['roadmap']}", warn=True)
    check((root / ".claude" / "settings.json").is_file(), ".claude/settings.json installed", warn=True)
    check((root / cfg["paths"]["tools_dir"] / "pipeline" / "feature-pipeline.js").is_file(), "feature pipeline script installed", warn=True)
    for mode in ("fast", "full"):
        for g in cfg["gates"][mode]:
            if "not configured" in g["command"]:
                check(False, f"gate {mode}/{g['name']}: still the template placeholder (set a real command; see docs/ADAPTING.md)", warn=bool(g.get("optional")))
                continue
            prog = g["command"].split()[0] if g["command"].split() else ""
            check(shutil.which(prog) is not None or prog in ("python3", "python", "sdd"), f"gate {mode}/{g['name']}: {prog} on PATH", warn=bool(g.get("optional")))
    if "not configured" in (cfg.get("tests", {}).get("run") or ""):
        check(False, "tests.run: still the template placeholder", warn=True)
    for tier in ("mechanical", "mid", "strong", "top"):
        check(bool(cfg["models"].get(tier)), f"models.{tier} set (explicit model id)", warn=True)
    mounts = str(root)
    check(not mounts.startswith("/tmp"), "project not under /tmp (RAM tmpfs would lose it on a crash)")
    print("doctor: " + ("OK" if ok else "problems found"))
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sdd", description="Spec-driven agentic development kit tools (stdlib only).")
    p.add_argument("--root", help="project root (default: nearest sdd.config.json or git top level)")
    p.add_argument("--version", action="version", version=f"sdd {VERSION}")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("config", help="show, validate or initialise sdd.config.json")
    c.add_argument("--validate", action="store_true")
    c.add_argument("--pipeline", action="store_true", help="print the subset the feature pipeline needs (args.config)")
    c.add_argument("--get", help="print one key, e.g. paths.features_dir")
    c.add_argument("--init", action="store_true", help="write the template config")
    c.add_argument("--force", action="store_true")
    c.set_defaults(fn=cmd_config)

    t = sub.add_parser("trace", help="requirement -> implementation -> test matrix")
    t.add_argument("--feature", help="feature number (e.g. 011) or directory name")
    t.add_argument("--check", action="store_true", help="fail on orphans, duplicates and uncovered must requirements")
    t.add_argument("--json", action="store_true")
    t.add_argument("--allow-missing-impl", action="store_true")
    t.set_defaults(fn=cmd_trace)

    g = sub.add_parser("gates", help="run the configured gates")
    g.add_argument("--fast", action="store_true", help="run gates.fast instead of gates.full")
    g.add_argument("--only", action="append", help="run only this gate (repeatable)")
    g.add_argument("--feature", help="exported as SDD_FEATURE to gate commands")
    g.add_argument("--log", help="log file (default: <logs_dir>/gates-<tag>.log)")
    g.add_argument("--tag", help="log name tag")
    g.add_argument("--detached", action="store_true", help="start in the background; poll with --wait <log>")
    g.add_argument("--wait", metavar="LOG", help="wait for GATES_EXIT=<n> in LOG and exit with n")
    g.add_argument("--poll", default="15", help="seconds between polls for --wait")
    g.add_argument("--timeout", default="7200", help="max seconds for --wait")
    g.add_argument("--no-stdout", action="store_true", help=argparse.SUPPRESS)
    g.set_defaults(fn=cmd_gates)

    n = sub.add_parser("new-feature", help="scaffold <features_dir>/<NNN>-<slug>/ from the templates")
    n.add_argument("number")
    n.add_argument("slug")
    n.add_argument("--force", action="store_true")
    n.set_defaults(fn=cmd_new_feature)

    pr = sub.add_parser("progress", help="lifecycle-stage progress per feature, milestone and overall")
    pr.add_argument("--json", action="store_true")
    pr.add_argument("--out", help="also write the JSON report here")
    pr.add_argument("--remote", default="origin")
    pr.add_argument("--main-ref")
    pr.set_defaults(fn=cmd_progress)

    u = sub.add_parser("union-merge", help="resolve additive manifest conflicts only (refuses anything else)")
    u.add_argument("files", nargs="*")
    u.set_defaults(fn=cmd_union_merge)

    si = sub.add_parser("spec-info", help="Status, open clarification markers and requirement counts of a spec")
    si.add_argument("spec", help="spec.md path or feature directory")
    si.set_defaults(fn=cmd_spec_info)

    mk = sub.add_parser("markers", help="count open clarification markers (ignores comments, code and prose mentions)")
    mk.add_argument("file")
    mk.set_defaults(fn=cmd_markers)

    tk = sub.add_parser("tasks", help="parse tasks.md into waves/tasks (JSON)")
    tk.add_argument("tasks", help="tasks.md path or feature directory")
    tk.add_argument("--all", action="store_true", help="include done tasks")
    tk.add_argument("--lanes", action="store_true", help="group each wave into disjoint-component lanes")
    tk.add_argument("--validate", action="store_true", help="check plan exit criteria")
    tk.add_argument("--no-trace", action="store_true", help="with --validate: do not check requirement ids against specs")
    tk.set_defaults(fn=cmd_tasks)

    so = sub.add_parser("source", help="look up the normative source (clause or search)")
    so.add_argument("what", choices=["clause", "find"])
    so.add_argument("doc")
    so.add_argument("clause", help="clause id, or a regex for 'find'")
    so.set_defaults(fn=cmd_source)

    h = sub.add_parser("hook", help="Claude Code hook entry points (JSON on stdin)")
    h.add_argument("name", choices=list(hooks.HOOKS))
    h.set_defaults(fn=cmd_hook)

    d = sub.add_parser("doctor", help="check the installation and configuration")
    d.set_defaults(fn=cmd_doctor)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.fn(args)
    except cfgmod.ConfigError as exc:
        print(f"sdd: {exc}", file=sys.stderr)
        return 2
