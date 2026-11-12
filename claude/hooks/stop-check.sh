#!/usr/bin/env bash
# Claude Code hook wrapper: stop-check. Logic lives in tools/sdd/sddkit/hooks.py (config-driven,
# unit-tested). Reads the hook JSON from stdin; exit 2 blocks (stderr goes back to Claude).
# Override for a human maintenance session: <override_env>=1 claude (default SDD_ALLOW_PROTECTED).
set -uo pipefail
root="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
tool="$root/tools/sdd/sdd.py"
if [ ! -f "$tool" ]; then
  echo "sdd hook stop-check: $tool not found; skipping." >&2
  exit 0
fi
exec python3 -I "$tool" hook stop-check
