#!/usr/bin/env bash
# Claude Code hook wrapper: protect-paths. Logic lives in tools/sdd/sddkit/hooks.py (config-driven,
# unit-tested). Reads the hook JSON from stdin; exit 2 blocks (stderr goes back to Claude).
# Override for a human maintenance session: <override_env>=1 claude (default SDD_ALLOW_PROTECTED).
set -uo pipefail
root="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
tool="$root/tools/sdd/sdd.py"
if [ ! -f "$tool" ]; then
  echo "sdd hook protect-paths: $tool not found; refusing to proceed without the guard." >&2
  exit 2
fi
exec python3 -I "$tool" hook protect-paths
