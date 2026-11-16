"""Orchestrator-only skills: the main-only-skills hook, its wiring and config, the two skills, the docs.

The hook (PreToolUse Skill) blocks the skills in ``orchestrator.main_only_skills`` for every
subagent (hook input with ``agent_id`` or ``agent_type``) and nothing else: the main session is
never blocked, other skills stay available to subagents, and no agent template disallows the
Skill tool (owner decision 2026-11-17).
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import unittest
from contextlib import redirect_stderr

from helpers import KIT, GitRepo, make_tmp, rm, test_config, write_config

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
        ok = cfgmod._merge(cfgmod.DEFAULTS, test_config())
        self.assertFalse([p for p in cfgmod.validate(ok) if "main_only_skills" in p or "paths." in p])
        cases = [
            (("orchestrator", "main_only_skills"), "owner-rule", "main_only_skills must be a list"),
            (("orchestrator", "main_only_skills"), ["plugin:owner-rule"], "main_only_skills must be a list"),
            (("paths", "handoff"), "../STATE.md", "paths.handoff must be"),
            (("paths", "orchestrator"), "/etc/rules.md", "paths.orchestrator must be"),
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

    def test_main_only_skills_name_their_restriction_and_config(self) -> None:
        for skill, key in (("owner-rule", "paths.orchestrator"), ("compact-handoff", "paths.handoff")):
            text = (KIT / f"claude/skills/{skill}/SKILL.md").read_text(encoding="utf-8")
            self.assertIn("Main session (orchestrator) only", frontmatter(text)["description"])
            self.assertIn("main-only-skills", text)
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
        self.assertRegex(dev, r"\| `main-only-skills\.sh` \| PreToolUse Skill \|")
        orch = read("templates/docs/ORCHESTRATOR.md")
        for word in ("`owner-rule`", "`compact-handoff`", "main-only-skills", "every other skill stays available to subagents"):
            self.assertIn(word, orch)
        playbook = read("docs/PLAYBOOK.md")
        for word in ("`owner-rule`", "`compact-handoff`", "orchestrator.main_only_skills", "only restriction on skills"):
            self.assertIn(word, playbook)
        self.assertIn("main-only-skills", read("claude/README.md"))


if __name__ == "__main__":
    unittest.main()
