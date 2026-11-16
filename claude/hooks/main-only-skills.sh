#!/usr/bin/env bash
# Claude Code hook wrapper: main-only-skills. Logic lives in tools/sdd/sddkit/hooks.py (config-driven,
# unit-tested). Reads the hook JSON from stdin; exit 2 blocks (stderr goes back to Claude).
# Blocks the skills in orchestrator.main_only_skills for every subagent; the main session and all other skills pass.
set -uo pipefail
root="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
tool="$root/tools/sdd/sdd.py"
if [ ! -f "$tool" ]; then
  echo "sdd hook main-only-skills: $tool not found; refusing the Skill call without the guard." >&2
  exit 2
fi
exec python3 -I "$tool" hook main-only-skills
