#!/bin/sh
# heavy-lock.sh: run a heavy build or test job under a host-wide lock, one at a time.
#
#   heavy-lock.sh <command> [args...]
#
# Coverage, mutation testing, full builds and full test runs of concurrent pipelines (other
# worktrees, other projects) queue on one lock file instead of running at once and exhausting
# memory. SDD_HEAVY_LOCK names the lock file (default /tmp/sdd-heavy-job.lock: one per host,
# the same for every worktree). The lock is held until the command and every child that keeps
# its file descriptor exit. A nested call for the same lock file runs the command directly
# (SDD_HEAVY_LOCK_HELD), so wrapping both a gate plan and its gates cannot deadlock.
# Needs flock(1) (util-linux); without it the command runs unlocked, with a warning.
lock="${SDD_HEAVY_LOCK:-/tmp/sdd-heavy-job.lock}"
if [ "$#" -eq 0 ]; then
  echo "usage: heavy-lock.sh <command> [args...]" >&2
  exit 2
fi
if [ "${SDD_HEAVY_LOCK_HELD:-}" = "$lock" ]; then
  exec "$@"
fi
if ! command -v flock >/dev/null 2>&1; then
  echo "heavy-lock: flock not found (util-linux); running WITHOUT the host-wide heavy-job lock" >&2
  exec "$@"
fi
exec 9>>"$lock" || { echo "heavy-lock: cannot open lock file $lock" >&2; exit 2; }
chmod a+rw "$lock" 2>/dev/null || true  # other users' jobs share the same lock
if ! flock -n 9; then
  echo "heavy-lock: waiting for $lock (another heavy job is running)" >&2
  flock 9 || { echo "heavy-lock: flock failed on $lock" >&2; exit 2; }
fi
SDD_HEAVY_LOCK_HELD="$lock"
export SDD_HEAVY_LOCK_HELD
exec "$@"
