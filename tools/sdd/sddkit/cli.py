"""Command-line entry point: ``python3 -I tools/sdd/sdd.py <command> ...``."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import sys
from pathlib import Path
from typing import List, Optional

from . import config as cfgmod
from . import callflow, gates, hooks, new_feature, progress, routing, source, specs, trace, union_merge
from . import quality
from .quality import arch, baseline, checks, commits, coverage, githooks, mutation, protected, secrets, size, vcs

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
        view = cfgmod.pipeline_view(cfg)
        print(json.dumps(view, separators=(",", ":")) if args.compact else json.dumps(view, indent=2))
        return 0
    if args.heavy_env:
        # Shell assignments for tools/sdd/scripts/heavy.sh: the lock from the config (always), budget and job size unless set.
        env = coverage.heavy_env(cfg, Path(cfg["_root"]))
        print(f"SDD_HEAVY_LOCK={shlex.quote(env.get('SDD_HEAVY_LOCK', ''))}; export SDD_HEAVY_LOCK")
        for var in ("SDD_HEAVY_BUDGET_MB", "SDD_HEAVY_JOB_MB"):
            if var in env:
                print(f"{var}={shlex.quote(env[var])}; export {var}")
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
    return trace.run(root, cfg, args.feature, args.check, args.json, args.allow_missing_impl, args.include)


def cmd_gates(args: argparse.Namespace) -> int:
    root, cfg = _cfg(args)
    logs_dir = root / cfg["paths"]["logs_dir"]
    if args.wait:
        return gates.wait(Path(args.wait), float(args.poll), float(args.timeout))
    plan_name = args.plan or ("fast" if args.fast else "full")
    tag = args.tag or plan_name
    if args.detached:
        argv = ["--plan", plan_name]
        for o in args.only or []:
            argv += ["--only", o]
        if args.feature:
            argv += ["--feature", args.feature]
        if args.log:
            return gates.detached(root, argv, Path(args.log), keyed=False, fresh=True)
        if args.feature:
            try:
                log_path = gates.keyed_log(root, logs_dir, args.feature, tag)
            except gates.GateRunError as exc:
                print(f"gates: {exc}", file=sys.stderr)
                return 2
            return gates.detached(root, argv, log_path, keyed=True, fresh=args.fresh)
        return gates.detached(root, argv, logs_dir / f"gates-{tag}.log", keyed=False, fresh=True)
    log_path = Path(args.log) if args.log else logs_dir / f"gates-{tag}.log"
    extra = {"SDD_FEATURE": args.feature} if args.feature else None
    return gates.run(cfg, root, plan_name == "fast", args.only, log_path, extra, quiet=args.no_stdout, plan_name=plan_name)


def cmd_check(args: argparse.Namespace) -> int:
    root, cfg = _cfg(args)
    name = args.name
    try:
        if name == "size":
            res = size.run(root, cfg, staged=args.staged)
        elif name == "arch":
            res = arch.run(root, cfg)
        elif name == "coverage":
            res = coverage.run(root, cfg, no_run=args.no_run)
        elif name == "mutation":
            res = mutation.run(root, cfg, no_run=args.no_run)
        elif name == "commits":
            res = commits.check(root, cfg)
        elif name == "secrets":
            res = secrets.check(root, cfg)
        elif name == "baseline":
            res = baseline.ratchet(root, cfg)
        elif name == "protected":
            res = protected.check(root, cfg)
        else:
            res = checks.run(root, cfg, name)
    except quality.QualityError as exc:
        res = quality.error(name, str(exc))
    return quality.emit(res, args.json)


def cmd_merge_base(args: argparse.Namespace) -> int:
    """The merge-base every diffing check uses, for project gate commands that select work by changed
    files (fuzz targets, benchmarks): one rule for all of them, including CI's SDD_BASE_REF requirement."""
    root, cfg = _cfg(args)
    try:
        print(vcs.merge_base(root, cfg))
    except quality.QualityError as exc:
        print(f"sdd merge-base: {exc}", file=sys.stderr)
        return 2
    return 0


def cmd_baseline(args: argparse.Namespace) -> int:
    root, cfg = _cfg(args)
    try:
        if args.init:
            return baseline.init(root, cfg)
        if args.update:
            return baseline.update(root, cfg)
        if args.resolve:
            return baseline.resolve(root, cfg)
        if args.merge_driver:
            return baseline.merge_driver(*args.merge_driver)
        return baseline.report(root, cfg, args.json)
    except quality.QualityError as exc:
        print(f"baseline: {exc}", file=sys.stderr)
        return 2


def cmd_hooks(args: argparse.Namespace) -> int:
    if args.action == "run":
        return githooks.run(args.hook, args.hook_args)
    root, cfg = _cfg(args)
    top = vcs.toplevel(root)
    if top is None:
        print(f"hooks: NOT installed: {root} is not inside a git repository", file=sys.stderr)
        return 1
    here = vcs.toplevel(Path.cwd())
    if here is not None and here.resolve() != top.resolve():
        print(f"hooks: NOT installed: the project root {top} is not the repository of the current directory ({here}); "
              "run the command from inside the target repository", file=sys.stderr)
        return 1
    return githooks.install(top, cfgmod.load(top) if (top / cfgmod.CONFIG_NAME).is_file() else cfg, args.force)


def cmd_routing(args: argparse.Namespace) -> int:
    root, cfg = _cfg(args)
    if args.action == "table":
        return routing.cmd_table(root, cfg, args.json)
    if args.action == "log":
        if not (args.output_file or args.file):
            print("routing log: give --output-file <Workflow output file> or --file <summary JSON | ->", file=sys.stderr)
            return 2
        return routing.cmd_log(root, cfg, args.output_file, args.file, args.transcript_dir)
    return routing.cmd_refit(root, cfg, args.file, args.json, args.min_samples, args.max_fail, args.input, args.journal)


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


def cmd_callflow(args: argparse.Namespace) -> int:
    return callflow.run(Path(args.spec), args.procedure, Path(args.expect), Path(args.events), args.json)


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
    for mode in ("fast", "full", "nightly"):
        for g in cfg["gates"][mode]:
            if "not configured" in g["command"]:
                check(False, f"gate {mode}/{g['name']}: still the template placeholder (set a real command; see docs/ADAPTING.md)", warn=bool(g.get("optional")))
                continue
            prog = g["command"].split()[0] if g["command"].split() else ""
            path = quality.tool_env(cfg, root).get("PATH")
            found = shutil.which(prog, path=path) is not None or prog in ("python3", "sdd", "sh") or "=" in prog
            # A gate that SKIPs when its tool is missing (skip_if_missing), an optional one and the
            # nightly plan (CI only) warn; only a gate that would FAIL is a doctor problem.
            soft = bool(g.get("optional") or g.get("skip_if_missing")) or mode == "nightly"
            check(found, f"gate {mode}/{g['name']}: {prog} {'on' if found else 'NOT on'} PATH", warn=soft)
    q = cfg["quality"]
    for tool in ("coverage", "mutation"):
        if not q[tool]["command"] and not q[tool]["report"]:
            check(False, f"quality.{tool}: not configured (sdd check {tool} fails until it is; see docs/ADAPTING.md)", warn=True)
    if vcs.toplevel(root):
        check(githooks.installed(root, cfg), "git hooks installed (run `sdd hooks install`, or chain them: docs/ADAPTING.md)", warn=True)
    if "not configured" in (cfg.get("tests", {}).get("run") or ""):
        check(False, "tests.run: still the template placeholder", warn=True)
    for warning in routing.doctor_warnings(root, cfg):
        check(False, warning, warn=True)
    mounts = str(root)
    check(not mounts.startswith("/tmp"), "project not under /tmp (RAM tmpfs would lose it on a crash)")
    for key, value in (cfg["build_cache"].get("env") or {}).items():
        if isinstance(value, str) and value.startswith(("/", "~")) and _on_tmpfs(Path(value).expanduser()):
            check(False, f"build_cache.env {key}={value}: under /tmp or on a tmpfs mount (RAM); put the shared build cache on disk", warn=True)
    print("doctor: " + ("OK" if ok else "problems found"))
    return 0 if ok else 1


def _on_tmpfs(path: Path) -> bool:
    """True when ``path`` is under /tmp or its mount point (from /proc/mounts) is a tmpfs."""
    path = Path(os.path.abspath(str(path)))
    if path == Path("/tmp") or Path("/tmp") in path.parents:
        return True
    best, fstype = "", ""
    try:
        lines = Path("/proc/mounts").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    for line in lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        mount = parts[1].replace("\\040", " ")
        if (str(path) == mount or str(path).startswith(mount.rstrip("/") + "/")) and len(mount) > len(best):
            best, fstype = mount, parts[2]
    return fstype in ("tmpfs", "ramfs")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sdd", description="Spec-driven agentic development kit tools (stdlib only).")
    p.add_argument("--root", help="project root (default: nearest sdd.config.json or git top level)")
    p.add_argument("--version", action="version", version=f"sdd {VERSION}")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("config", help="show, validate or initialise sdd.config.json")
    c.add_argument("--validate", action="store_true")
    c.add_argument("--pipeline", action="store_true", help="print the subset the feature pipeline needs (args.config)")
    c.add_argument("--compact", action="store_true", help="with --pipeline: one-line JSON (safe to embed in a prompt)")
    c.add_argument("--get", help="print one key, e.g. paths.features_dir")
    c.add_argument("--heavy-env", action="store_true", help="shell assignments of the heavy-job lock settings (tools/sdd/scripts/heavy.sh)")
    c.add_argument("--init", action="store_true", help="write the template config")
    c.add_argument("--force", action="store_true")
    c.set_defaults(fn=cmd_config)

    t = sub.add_parser("trace", help="requirement -> implementation -> test matrix")
    t.add_argument("--feature", help="feature number (e.g. 011) or directory name")
    t.add_argument("--check", action="store_true", help="fail on orphans, duplicates and uncovered must requirements")
    t.add_argument("--json", action="store_true")
    t.add_argument("--allow-missing-impl", action="store_true")
    t.add_argument("--include", action="append", metavar="PATH",
                   help="also scan this file whatever trace.scan_globs says (repeatable); files outside the globs are listed as outsideScan")
    t.set_defaults(fn=cmd_trace)

    g = sub.add_parser("gates", help="run the configured gates")
    g.add_argument("--fast", action="store_true", help="run gates.fast instead of gates.full (alias of --plan fast)")
    g.add_argument("--plan", choices=["fast", "full", "nightly"], help="gate plan to run (default: full)")
    g.add_argument("--only", action="append", help="run only this gate (repeatable)")
    g.add_argument("--feature", help="exported as SDD_FEATURE to gate commands")
    g.add_argument("--log", help="log file (default: <logs_dir>/gates-<tag>.log)")
    g.add_argument("--tag", help="log name tag; with --detached --feature the log is <logs_dir>/gates/<feature>-<tag>-<sha7>[-dirty].log")
    g.add_argument("--detached", action="store_true",
                   help="start in the background (or attach to the live run of the same key); prints {log, state, pid, exit?}; wait with --wait <log>")
    g.add_argument("--fresh", action="store_true", help="with --detached --feature: never reuse a completed log of this HEAD and tag (it is renamed to <log>.<n>)")
    g.add_argument("--wait", metavar="LOG", help="wait for GATES_EXIT=<n> in LOG and exit with n; 124 timed out (still running), 125 incomplete (runner dead, no marker)")
    g.add_argument("--poll", default="15", help="seconds between polls for --wait")
    g.add_argument("--timeout", default="7200", help="max seconds for --wait")
    g.add_argument("--no-stdout", action="store_true", help=argparse.SUPPRESS)
    g.set_defaults(fn=cmd_gates)

    ck = sub.add_parser("check", help="run one quality check: size, arch, coverage, mutation, commits, secrets, baseline, protected or a quality.checks entry")
    ck.add_argument("name")
    ck.add_argument("--json", action="store_true")
    ck.add_argument("--staged", action="store_true", help="size: check the staged content of staged files only")
    ck.add_argument("--no-run", action="store_true", help="coverage/mutation: read the existing report instead of running the tool")
    ck.set_defaults(fn=cmd_check)

    mb = sub.add_parser("merge-base", help="print the merge-base of HEAD and the base ref that every diffing check uses (SDD_BASE_REF; required with CI=true)")
    mb.set_defaults(fn=cmd_merge_base)

    bl = sub.add_parser("baseline", help="the shrink-only violation baseline: report (default), init, update, resolve")
    mode = bl.add_mutually_exclusive_group()
    mode.add_argument("--report", action="store_true", help="every current violation by kind and file (default)")
    mode.add_argument("--init", action="store_true", help="record all current violations (refuses when the file exists)")
    mode.add_argument("--update", action="store_true", help="remove stale entries and lower values; never adds")
    mode.add_argument("--resolve", action="store_true", help="resolve a conflicted baseline file in a merge (intersection, then --update)")
    mode.add_argument("--merge-driver", nargs=3, metavar=("O", "A", "B"), help="git merge driver: intersection of A and B written to A")
    bl.add_argument("--json", action="store_true")
    bl.set_defaults(fn=cmd_baseline)

    hk = sub.add_parser("hooks", help="git hooks: install (sets core.hooksPath) or run one (used by the hook scripts)")
    hk_sub = hk.add_subparsers(dest="action", required=True)
    hi = hk_sub.add_parser("install", help="set core.hooksPath to <tools_dir>/githooks in this repository")
    hi.add_argument("--force", action="store_true", help="install even over hooks already in use")
    hr = hk_sub.add_parser("run", help="run one hook's logic")
    hr.add_argument("hook", choices=list(githooks.HOOK_NAMES))
    hr.add_argument("hook_args", nargs=argparse.REMAINDER)
    hk.set_defaults(fn=cmd_hooks)

    ro = sub.add_parser("routing", help="model routing: models table candidates, telemetry log, refit report")
    ro_sub = ro.add_subparsers(dest="action", required=True)
    rt = ro_sub.add_parser("table", help="validate the effective models table and print the candidates per level with E")
    rt.add_argument("--json", action="store_true")
    rl = ro_sub.add_parser("log", help="append a run's telemetry (one JSONL line per agent attempt) to <logs_dir>/routing.jsonl")
    src = rl.add_mutually_exclusive_group()
    src.add_argument("--output-file", help="the Workflow output file named in the completion notice (summary in 'result', times in 'workflowProgress')")
    src.add_argument("--file", help="a pipeline summary or telemetry array as JSON ('-' = stdin)")
    rl.add_argument("--transcript-dir", help="the run's transcript dir (journal.jsonl + agent-*.jsonl) for times when there is no output file")
    rf = ro_sub.add_parser("refit", help="per-level failure rates and floor suggestions (report only, never writes)")
    rf.add_argument("--json", action="store_true")
    rf.add_argument("--file", help="routing JSONL for the telemetry input (default: <logs_dir>/routing.jsonl)")
    rf.add_argument("--input", action="append", choices=sorted(routing.REFIT_INPUTS),
                    help="where the attempts come from (repeatable; rows are pooled): telemetry (default) or journal "
                         "(Workflow journals: level from a ' [L<n>]' label suffix, model from agent-<id>.meta.json)")
    rf.add_argument("--journal", action="append", metavar="PATH",
                    help="journal file, run directory or glob for the journal input (repeatable; implies --input journal; "
                         "default: the Workflow journals under ~/.claude/projects)")
    rf.add_argument("--min-samples", type=int, default=10)
    rf.add_argument("--max-fail", type=float, default=0.25)
    ro.set_defaults(fn=cmd_routing)

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

    cf = sub.add_parser("callflow", help="check a recorded event trace against a spec's step table")
    cf.add_argument("spec", help="the spec.md holding the step table")
    cf.add_argument("--procedure", required=True, help="the step table's procedure, e.g. 'PROTO §4.2'")
    cf.add_argument("--expect", required=True, metavar="FILE", help='JSON: {"expect": [{"step", "match", "ordered"?}], "skip": [{"step", "reason"}]}')
    cf.add_argument("--events", required=True, metavar="FILE", help="JSON array or JSON Lines of events, each with an integer seq")
    cf.add_argument("--json", action="store_true")
    cf.set_defaults(fn=cmd_callflow)

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
