#!/usr/bin/env bash
# Claude Code hook wrapper: orchestrator-context (SessionStart). Logic lives in tools/sdd/sddkit/hooks.py
# (config-driven, unit-tested). Prints additionalContext JSON that reminds the MAIN session (the
# orchestrator) of its two habits: record owner rules in paths.orchestrator, write paths.handoff before a
# compaction. SessionStart fires for the main session only (subagents get SubagentStart); the hook also
# exits silently for input carrying agent_id. Never blocks: on any problem it prints nothing.
set -uo pipefail
root="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
tool="$root/tools/sdd/sdd.py"
[ -f "$tool" ] || exit 0
python3 -I "$tool" hook orchestrator-context || exit 0
