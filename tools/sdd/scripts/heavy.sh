#!/bin/sh
# heavy.sh: run a heavy build or test job under the project's heavy-job lock.
#
#   tools/sdd/scripts/heavy.sh <command> [args...]
#   SDD_HEAVY_JOB_MB=2048 tools/sdd/scripts/heavy.sh <one component's test command>
#
# Every command that builds the project or runs its tests (full and fast gates, test runs,
# coverage, mutation, benchmarks, cold builds) goes through this wrapper, by agents and by hand,
# from the main checkout or any worktree. It takes the lock file, the memory budget and the
# default job size from sdd.config.json (`sdd config --heavy-env`: quality.heavy_lock, resolved
# against the main checkout when relative, so all worktrees share one queue; quality.
# heavy_budget_mb; quality.heavy_job_mb); variables already set in the environment win. With
# quality.heavy_lock "" the command runs directly. The queue itself is heavy-lock.sh (fair,
# first come first served, optional memory budget with a capped systemd scope per job).
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
if [ "$#" -eq 0 ]; then
  echo "usage: heavy.sh <command> [args...]" >&2
  exit 2
fi
envs="$(python3 -I "$here/../sdd.py" config --heavy-env)" || {
  echo "heavy.sh: cannot read the heavy-job settings (sdd config --heavy-env); refusing to run unlocked" >&2
  exit 2
}
eval "$envs"
if [ -z "${SDD_HEAVY_LOCK:-}" ]; then
  exec "$@"
fi
# systemd-run --user needs the user's runtime directory (lost in some non-login shells).
if [ -z "${XDG_RUNTIME_DIR:-}" ] && [ -d "/run/user/$(id -u)" ]; then
  XDG_RUNTIME_DIR="/run/user/$(id -u)"
  export XDG_RUNTIME_DIR
fi
exec sh "$here/heavy-lock.sh" "$@"
