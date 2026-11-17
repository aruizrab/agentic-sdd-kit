"""Orchestrator-only habits: the hooks, their wiring and config, the two hidden skills, the docs.

Option B (lesson 54): the ``orchestrator-context`` hook (SessionStart) reminds the main session of
its two habits; the skills set ``disable-model-invocation`` and pull their procedure from the
orchestrator guide; the ``main-only-skills`` hook (PreToolUse Skill) stays as the backstop that
blocks the skills in ``orchestrator.main_only_skills`` for every subagent (hook input with
``agent_id`` or ``agent_type``) and nothing else: the main session is never blocked, other skills
stay available to subagents, and no agent template disallows the Skill tool (owner decision
2026-11-17).
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import unittest
from contextlib import redirect_stderr, redirect_stdout

from helpers import GIT_ENV, KIT, GitRepo, make_tmp, rm, test_config, write_config

from sddkit import config as cfgmod
from sddkit import hooks

MAIN_ONLY = ("owner-rule", "compact-handoff")


def frontmatter(text: str) -> dict:
    """The ``key: value`` lines between the leading ``---`` fences (the kit's skills use one line per key)."""
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep and re.fullmatch(r"[a-z][a-z-]*", key):
            out[key] = value.strip()
    return out


class MainOnlySkillsHookTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_tmp("skills-")
        GitRepo(self.root).init()
        self.cfg = test_config()
        write_config(self.root, self.cfg)
        self.env_backup = dict(os.environ)
        os.environ["CLAUDE_PROJECT_DIR"] = str(self.root)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.env_backup)
        rm(self.root)

    def call(self, skill: str, **who) -> tuple:
        data = {"hook_event_name": "PreToolUse", "tool_name": "Skill", "tool_input": {"skill": skill}, "cwd": str(self.root), **who}
        err = io.StringIO()
        with redirect_stderr(err):
            code = hooks.main_only_skills(data)
        return code, err.getvalue()

    def test_main_session_is_allowed(self) -> None:
        for skill in MAIN_ONLY + ("gates", "feature"):
            with self.subTest(skill=skill):
                self.assertEqual(self.call(skill)[0], 0)
                self.assertEqual(self.call(skill, agent_id="", agent_type="")[0], 0)

    def test_every_kind_of_subagent_is_blocked(self) -> None:
        callers = {
            "custom agent": {"agent_id": "a1b2", "agent_type": "implementer"},
            "built-in agent": {"agent_id": "c3d4", "agent_type": "general-purpose"},
            "fork": {"agent_id": "e5f6", "agent_type": "fork"},
            "id only": {"agent_id": "0a9b"},
            "type only": {"agent_type": "workflow-agent"},
        }
        for skill in MAIN_ONLY:
            for kind, who in callers.items():
                with self.subTest(skill=skill, caller=kind):
                    code, err = self.call(skill, **who)
                    self.assertEqual(code, 2)
                    self.assertIn("BLOCKED by SDD hook", err)
                    self.assertIn(skill, err)
                    self.assertIn("final message", err)

    def test_namespaced_and_slash_forms_are_blocked(self) -> None:
        for raw in ("project:owner-rule", "/owner-rule", "/compact-handoff now", "plugin:sub:compact-handoff", "  owner-rule  "):
            with self.subTest(raw=raw):
                self.assertEqual(self.call(raw, agent_id="x", agent_type="verifier")[0], 2)
        data = {"tool_name": "Skill", "tool_input": {"name": "owner-rule"}, "cwd": str(self.root), "agent_type": "verifier"}
        with redirect_stderr(io.StringIO()):
            self.assertEqual(hooks.main_only_skills(data), 2, "the name field is read too")

    def test_other_skills_stay_available_to_subagents(self) -> None:
        for skill in ("gates", "trace", "source", "feature", "review-feature", "new-feature", "owner-rules", "my-plugin:gates", ""):
            with self.subTest(skill=skill):
                self.assertEqual(self.call(skill, agent_id="x", agent_type="implementer")[0], 0)

    def test_the_list_is_configuration(self) -> None:
        self.cfg["orchestrator"] = {"main_only_skills": ["release"]}
        write_config(self.root, self.cfg)
        self.assertEqual(self.call("release", agent_type="integrator")[0], 2)
        self.assertEqual(self.call("owner-rule", agent_type="integrator")[0], 0)
        self.cfg["orchestrator"] = {"main_only_skills": []}
        write_config(self.root, self.cfg)
        self.assertEqual(self.call("compact-handoff", agent_type="integrator")[0], 0)

    def test_bad_input_fails_closed(self) -> None:
        with redirect_stderr(io.StringIO()):
            self.assertEqual(hooks.main_only_skills({"_invalid": True, "cwd": str(self.root)}), 2)

    def test_shell_wrapper(self) -> None:
        """The installed wrapper runs `sdd hook main-only-skills` with the hook JSON on stdin."""
        def run(payload: dict) -> subprocess.CompletedProcess:
            return subprocess.run(["bash", str(KIT / "claude/hooks/main-only-skills.sh")], input=json.dumps(payload), cwd=str(self.root),
                                  env={**os.environ, "CLAUDE_PROJECT_DIR": str(KIT), "SDD_ROOT": str(self.root)},
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        base = {"tool_name": "Skill", "tool_input": {"skill": "owner-rule"}, "cwd": str(self.root)}
        self.assertEqual(run(base).returncode, 0)
        blocked = run({**base, "agent_id": "f1", "agent_type": "fork"})
        self.assertEqual(blocked.returncode, 2)
        self.assertIn("main session", blocked.stderr)
        self.assertEqual(run({**base, "tool_input": {"skill": "gates"}, "agent_type": "implementer"}).returncode, 0)
        self.assertTrue(os.access(KIT / "claude/hooks/main-only-skills.sh", os.X_OK))


class ConfigTest(unittest.TestCase):
    def test_defaults_and_validation(self) -> None:
        self.assertEqual(cfgmod.DEFAULTS["orchestrator"]["main_only_skills"], list(MAIN_ONLY))
        self.assertEqual(cfgmod.DEFAULTS["paths"]["orchestrator"], "docs/ORCHESTRATOR.md")
        self.assertEqual(cfgmod.DEFAULTS["paths"]["handoff"], ".wt/handoff/STATE.md")
        self.assertEqual(cfgmod.DEFAULTS["orchestrator"]["heartbeat"], {"enabled": True, "minute": 17})
        self.assertEqual(cfgmod.DEFAULTS["orchestrator"]["usage_file"], "")
        self.assertEqual(cfgmod.DEFAULTS["orchestrator"]["progress_site"], "")
        ok = cfgmod._merge(cfgmod.DEFAULTS, test_config())
        self.assertFalse([p for p in cfgmod.validate(ok) if "orchestrator." in p or "paths." in p])
        cases = [
            (("orchestrator", "main_only_skills"), "owner-rule", "main_only_skills must be a list"),
            (("orchestrator", "main_only_skills"), ["plugin:owner-rule"], "main_only_skills must be a list"),
            (("paths", "handoff"), "../STATE.md", "paths.handoff must be"),
            (("paths", "orchestrator"), "/etc/rules.md", "paths.orchestrator must be"),
            (("orchestrator", "heartbeat"), {"enabled": "yes", "minute": 17}, "heartbeat.enabled must be"),
            (("orchestrator", "heartbeat"), {"enabled": True, "minute": 60}, "heartbeat.minute must be"),
            (("orchestrator", "heartbeat"), {"enabled": True, "minute": True}, "heartbeat.minute must be"),
            (("orchestrator", "usage_file"), "a b.json", "usage_file must be"),
            (("orchestrator", "usage_file"), 3, "usage_file must be"),
            (("orchestrator", "progress_site"), 3, "progress_site must be"),
            (("orchestrator", "progress_site"), "site\nrm -rf", "progress_site must be"),
            (("orchestrator", "progress_site"), "x" * 301, "progress_site must be"),
        ]
        for (section, key), value, text in cases:
            with self.subTest(key=key, value=value):
                cfg = cfgmod._merge(cfgmod.DEFAULTS, test_config())
                cfg[section][key] = value
                self.assertTrue(any(text in p for p in cfgmod.validate(cfg)), cfgmod.validate(cfg))


class WiringAndTemplatesTest(unittest.TestCase):
    def test_settings_wire_the_hook_on_skill(self) -> None:
        settings = json.loads((KIT / "claude/settings.json").read_text(encoding="utf-8"))
        entries = [e for e in settings["hooks"]["PreToolUse"] if e.get("matcher") == "Skill"]
        self.assertEqual(len(entries), 1)
        self.assertEqual([h["command"] for h in entries[0]["hooks"]], ['"$CLAUDE_PROJECT_DIR"/.claude/hooks/main-only-skills.sh'])
        self.assertIn("main-only-skills", hooks.HOOKS)

    def test_settings_wire_the_reminder_on_session_start_only(self) -> None:
        settings = json.loads((KIT / "claude/settings.json").read_text(encoding="utf-8"))
        cmd = '"$CLAUDE_PROJECT_DIR"/.claude/hooks/orchestrator-context.sh'
        entries = settings["hooks"]["SessionStart"]
        self.assertEqual(len(entries), 1)
        self.assertNotIn("matcher", entries[0], "every source: startup, resume, clear, compact")
        self.assertEqual([h["command"] for h in entries[0]["hooks"]], [cmd])
        for event, lst in settings["hooks"].items():
            if event != "SessionStart":
                self.assertNotIn(cmd, json.dumps(lst), f"{event} must not carry the main-session reminder")
        self.assertIn("orchestrator-context", hooks.HOOKS)

    def test_every_skill_has_valid_frontmatter(self) -> None:
        skills = sorted((KIT / "claude/skills").glob("*/SKILL.md"))
        self.assertTrue({p.parent.name for p in skills} >= set(MAIN_ONLY))
        for path in skills:
            with self.subTest(skill=path.parent.name):
                text = path.read_text(encoding="utf-8")
                fm = frontmatter(text)
                self.assertEqual(fm.get("name"), path.parent.name)
                self.assertGreater(len(fm.get("description", "")), 40)
                self.assertTrue(fm.get("allowed-tools"), "allowed-tools")
                for key in fm:
                    self.assertIn(key, {"name", "description", "argument-hint", "allowed-tools", "disable-model-invocation", "model", "context", "agent"}, key)

    def test_main_only_skills_are_hidden_and_name_their_config(self) -> None:
        for skill, key in (("owner-rule", "paths.orchestrator"), ("compact-handoff", "paths.handoff")):
            text = (KIT / f"claude/skills/{skill}/SKILL.md").read_text(encoding="utf-8")
            fm = frontmatter(text)
            self.assertEqual(fm.get("disable-model-invocation"), "true", "no model context lists the skill")
            for word in ("main-only-skills", "orchestrator-context", "disable-model-invocation"):
                self.assertIn(word, text)
            self.assertIn(f"config --get {key}", text, "paths come from the configuration")
            self.assertNotIn("ferro" + "core", text.lower())

    def test_no_agent_template_disallows_the_skill_tool(self) -> None:
        """Only the hook restricts skills: subagents keep the Skill tool for every other skill."""
        agents = sorted((KIT / "claude/agents").glob("*.md"))
        self.assertEqual(len(agents), 12)
        for path in agents:
            with self.subTest(agent=path.stem):
                fm = frontmatter(path.read_text(encoding="utf-8"))
                self.assertNotIn("Skill", re.split(r"[\s,]+", fm.get("disallowedTools", "")))

    def test_docs(self) -> None:
        read = lambda rel: (KIT / rel).read_text(encoding="utf-8")  # noqa: E731
        dev = read("templates/docs/DEVELOPMENT.md")
        self.assertRegex(dev, r"\| `main-only-skills\.sh` \| PreToolUse Skill \| backstop")
        self.assertRegex(dev, r"\| `orchestrator-context\.sh` \| SessionStart \(main session only")
        orch = read("templates/docs/ORCHESTRATOR.md")
        for word in ("`/owner-rule <rule>`", "`/compact-handoff`", "main-only-skills", "orchestrator-context", "disable-model-invocation",
                     "### Recording an owner rule\n", "### Handoff before compaction\n", "every other skill stays available to subagents",
                     "### Heartbeat and wake-ups\n", '"{{PROJECT}} heartbeat"', "CronList", "orchestrator.heartbeat.minute", "orchestrator.usage_file",
                     "Heartbeat steps", "6 or more days", "**Progress site** (only when `orchestrator.progress_site`",
                     "**Keep the progress site current**", "7. **Renew**"):
            self.assertIn(word, orch)
        playbook = read("docs/PLAYBOOK.md")
        for word in ("`owner-rule`", "`compact-handoff`", "orchestrator.main_only_skills", "only restriction on skills", "orchestrator-context",
                     "disable-model-invocation", "SubagentStart", "agent_id"):
            self.assertIn(word, playbook)
        for word in ("main-only-skills", "orchestrator-context", "disable-model-invocation"):
            self.assertIn(word, read("claude/README.md"))
        self.assertIn("orchestrator-context", read("README.md"))


class OrchestratorContextHookTest(unittest.TestCase):
    """SessionStart: the reminder reaches the main session only, with the configured paths."""

    def setUp(self) -> None:
        self.root = make_tmp("orch-ctx-")
        GitRepo(self.root).init()
        self.cfg = test_config()
        write_config(self.root, self.cfg)
        (self.root / "docs").mkdir(exist_ok=True)
        shutil.copy(KIT / "templates/docs/ORCHESTRATOR.md", self.root / "docs/ORCHESTRATOR.md")
        self.env_backup = dict(os.environ)
        os.environ["CLAUDE_PROJECT_DIR"] = str(self.root)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.env_backup)
        rm(self.root)

    def call(self, **data) -> tuple:
        data = {"hook_event_name": "SessionStart", "cwd": str(self.root), **data}
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = hooks.orchestrator_context(data)
        return code, out.getvalue(), err.getvalue()

    def context(self, **data) -> str:
        code, out, err = self.call(**data)
        self.assertEqual((code, err), (0, ""))
        payload = json.loads(out)
        self.assertEqual(payload["hookSpecificOutput"]["hookEventName"], "SessionStart")
        return payload["hookSpecificOutput"]["additionalContext"]

    def test_startup_resume_clear(self) -> None:
        for source in ("startup", "resume", "clear", ""):
            with self.subTest(source=source):
                msg = self.context(source=source)
                for word in ("docs/ORCHESTRATOR.md", ".wt/handoff/STATE.md", f'"{hooks.OWNER_RULE_SECTION}"',
                             f'"{hooks.HANDOFF_SECTION}"', "same turn", "safe to compact"):
                    self.assertIn(word, msg)
                self.assertNotIn("compaction just happened", msg)

    def test_heartbeat_habit(self) -> None:
        msg = self.context(source="startup")
        name = f"{self.cfg['project']['name']} heartbeat"
        for word in ("Three habits", f'hourly "{name}" cron exists', "CronList", 'cron "17 * * * *"', f'"{hooks.HEARTBEAT_SECTION}"',
                     "expire after 7 days", "renew it at 6 days", "one-shot wake-up at each usage reset", "plus 2 minutes"):
            self.assertIn(word, msg)
        self.assertNotIn("rate_limits", msg, "no usage file configured")
        self.assertNotIn("progress site", msg, "no progress site configured")

    def test_heartbeat_is_configuration(self) -> None:
        self.cfg["project"]["name"] = "acme"
        self.cfg["orchestrator"] = {"heartbeat": {"enabled": True, "minute": 43}, "usage_file": "~/.claude/state/statusline-input.json"}
        write_config(self.root, self.cfg)
        msg = self.context(source="resume")
        for word in ('"acme heartbeat"', 'cron "43 * * * *"', "rate_limits.*.resets_at in ~/.claude/state/statusline-input.json"):
            self.assertIn(word, msg)
        self.assertNotIn("progress site", msg)
        self.cfg["orchestrator"]["progress_site"] = "../acme-site"
        write_config(self.root, self.cfg)
        msg = self.context(source="startup")
        for word in ("progress site (../acme-site)", "real stage", "same day", "heartbeat step 5"):
            self.assertIn(word, msg)
        self.cfg["orchestrator"]["heartbeat"]["enabled"] = False
        write_config(self.root, self.cfg)
        msg = self.context(source="startup")
        self.assertIn("Two habits", msg)
        for word in ("heartbeat", "CronList", "wake-up"):
            self.assertNotIn(word, msg)
        self.assertIn("safe to compact", msg)

    def test_compact_adds_read_the_handoff_first(self) -> None:
        msg = self.context(source="compact")
        self.assertIn("A compaction just happened: read .wt/handoff/STATE.md and docs/ORCHESTRATOR.md", msg)

    def test_subagent_like_input_is_silent(self) -> None:
        for who in ({"agent_id": "a1"}, {"agent_id": "a1", "agent_type": "general-purpose"}, {"agent_id": "f2", "agent_type": "fork"}):
            with self.subTest(who=who):
                self.assertEqual(self.call(source="startup", **who), (0, "", ""))

    def test_main_session_started_with_an_agent_still_gets_it(self) -> None:
        """agent_type without agent_id is the main thread of `claude --agent <name>`."""
        self.assertIn("docs/ORCHESTRATOR.md", self.context(source="startup", agent_type="product-owner"))

    def test_paths_are_configuration(self) -> None:
        self.cfg["paths"].update({"orchestrator": "ops/RULES.md", "handoff": ".wt/h/NOW.md"})
        write_config(self.root, self.cfg)
        (self.root / "ops").mkdir()
        shutil.copy(KIT / "templates/docs/ORCHESTRATOR.md", self.root / "ops/RULES.md")
        msg = self.context(source="compact")
        self.assertIn("record it in ops/RULES.md", msg)
        self.assertIn("read .wt/h/NOW.md and ops/RULES.md", msg)
        self.assertNotIn("docs/ORCHESTRATOR.md", msg)

    def test_silent_without_a_rules_file_or_on_bad_input(self) -> None:
        self.assertEqual(self.call(_invalid=True), (0, "", ""))
        (self.root / "docs/ORCHESTRATOR.md").unlink()
        self.assertEqual(self.call(source="startup"), (0, "", ""))
        (self.root / "sdd.config.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(self.call(source="startup"), (0, "", ""))

    def test_shell_wrapper(self) -> None:
        def run(payload: dict, project: str) -> subprocess.CompletedProcess:
            return subprocess.run(["bash", str(KIT / "claude/hooks/orchestrator-context.sh")], input=json.dumps(payload), cwd=str(self.root),
                                  env={**os.environ, "CLAUDE_PROJECT_DIR": project}, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        base = {"hook_event_name": "SessionStart", "source": "compact", "cwd": str(self.root)}
        main = run(base, str(KIT))
        self.assertEqual(main.returncode, 0, main.stderr)
        self.assertIn("compaction just happened", json.loads(main.stdout)["hookSpecificOutput"]["additionalContext"])
        sub = run({**base, "agent_id": "x1", "agent_type": "general-purpose"}, str(KIT))
        self.assertEqual((sub.returncode, sub.stdout), (0, ""))
        missing = run(base, str(self.root))  # no tools/sdd in the project: silent, never blocks
        self.assertEqual((missing.returncode, missing.stdout), (0, ""))
        self.assertTrue(os.access(KIT / "claude/hooks/orchestrator-context.sh", os.X_OK))


class SkillBodiesTest(unittest.TestCase):
    """The skills' `!` commands pull exactly their procedure section out of the installed rules file."""

    def setUp(self) -> None:
        self.root = make_tmp("skill-body-")
        GitRepo(self.root).init()
        self.cfg = test_config()
        write_config(self.root, self.cfg)
        os.symlink(KIT / "tools", self.root / "tools")
        self.template = (KIT / "templates/docs/ORCHESTRATOR.md").read_text(encoding="utf-8")

    def tearDown(self) -> None:
        rm(self.root)

    def procedure(self, skill: str) -> str:
        text = (KIT / f"claude/skills/{skill}/SKILL.md").read_text(encoding="utf-8")
        cmds = re.findall(r"^!`(.*)`$", text, re.M)
        self.assertTrue(cmds)
        proc = subprocess.run(["bash", "-c", cmds[-1]], cwd=str(self.root), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                              env={**os.environ, **GIT_ENV})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout

    def install(self, rel: str) -> None:
        (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
        (self.root / rel).write_text(self.template, encoding="utf-8")

    def expected(self, title: str) -> str:
        start = self.template.index(f"### {title}\n")
        end = self.template.index("\n#", start + 4)
        return self.template[start:end].rstrip("\n") + "\n"

    def test_each_skill_extracts_its_section(self) -> None:
        self.install("docs/ORCHESTRATOR.md")
        for skill, title in (("owner-rule", hooks.OWNER_RULE_SECTION), ("compact-handoff", hooks.HANDOFF_SECTION)):
            with self.subTest(skill=skill):
                out = self.procedure(skill)
                self.assertEqual(out, self.expected(title))
                self.assertGreater(out.count("\n"), 10)
                self.assertNotIn("## 8.", out)
        self.assertNotIn(hooks.HANDOFF_SECTION, self.procedure("owner-rule").splitlines()[-1])
        self.assertIn("safe to compact", self.procedure("compact-handoff"))

    def test_configured_rules_file(self) -> None:
        self.cfg["paths"]["orchestrator"] = "ops/RULES.md"
        write_config(self.root, self.cfg)
        self.install("ops/RULES.md")
        self.assertEqual(self.procedure("owner-rule"), self.expected(hooks.OWNER_RULE_SECTION))

    def test_missing_file_or_section_says_so(self) -> None:
        for skill, title in (("owner-rule", hooks.OWNER_RULE_SECTION), ("compact-handoff", hooks.HANDOFF_SECTION)):
            with self.subTest(skill=skill):
                self.assertIn(f'no section "{title}" in docs/ORCHESTRATOR.md', self.procedure(skill))
        (self.root / "docs").mkdir()
        (self.root / "docs/ORCHESTRATOR.md").write_text("# rules\n\n## 1. Something\n\ntext\n", encoding="utf-8")
        self.assertIn("no section", self.procedure("owner-rule"))


if __name__ == "__main__":
    unittest.main()
