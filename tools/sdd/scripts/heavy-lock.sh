#!/bin/sh
# heavy-lock.sh: run a heavy build or test job under a host-wide lock, one at a time, first come
# first served.
#
#   heavy-lock.sh <command> [args...]
#
# Coverage, mutation testing, full builds and full test runs of concurrent pipelines (other
# worktrees, other projects) queue on one lock file instead of running at once and exhausting
# memory. SDD_HEAVY_LOCK names the lock file (default /tmp/sdd-heavy-job.lock: one per host,
# the same for every worktree). The lock is held until the command and every child that keeps
# its file descriptor exit. A nested call for the same lock file runs the command directly
# (SDD_HEAVY_LOCK_HELD), so wrapping both a gate plan and its gates cannot deadlock.
#
# The queue is fair. flock(1) alone is not: a job that loops over long locked steps re-took the
# lock the moment it released it and kept a short job waiting for hours. So every caller first
# draws a ticket in <lock>.queue/ (a counter under a short mutex) and only the oldest live
# ticket may wait on the lock; a job that comes back for another step queues behind the jobs
# already waiting. Tickets of callers that died are dropped (pid no longer alive), and a
# cancelled caller removes its own. SDD_HEAVY_LOCK_POLL (seconds, default 2) is how often a
# queued caller checks whether it is next.
# Needs flock(1) (util-linux); without it the command runs unlocked, with a warning.
lock="${SDD_HEAVY_LOCK:-/tmp/sdd-heavy-job.lock}"
poll="${SDD_HEAVY_LOCK_POLL:-2}"
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
command exec 9>>"$lock" || { echo "heavy-lock: cannot open lock file $lock" >&2; exit 2; }
chmod a+rw "$lock" 2>/dev/null || true  # other users' jobs share the same lock

alive() {
  if [ -d /proc/self ]; then [ -d "/proc/$1" ]; else kill -0 "$1" 2>/dev/null || ps -p "$1" >/dev/null 2>&1; fi
}

# Waits run in the background so a signal ends a queued or waiting caller at once (a trap
# would otherwise wait for the foreground flock or sleep to return).
child=""
interruptible() {
  "$@" &
  child=$!
  wait "$child"
  rc=$?
  child=""
  return "$rc"
}

queue="$lock.queue"
me=""
waited=0
trap 'rm -f "$me"; [ -n "$child" ] && kill "$child" 2>/dev/null' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
if mkdir -p "$queue" 2>/dev/null && [ -w "$queue" ] && command exec 8>>"$queue/.mutex"; then
  chmod a+rwx "$queue" 2>/dev/null || true
  chmod a+rw "$queue/.mutex" 2>/dev/null || true
  flock 8
  n=$(cat "$queue/.next" 2>/dev/null)
  case "$n" in '' | *[!0-9]*) n=0 ;; esac
  n=$((n + 1))
  echo "$n" > "$queue/.next"
  chmod a+rw "$queue/.next" 2>/dev/null || true
  me="$queue/$(printf '%012d' "$n").$$"
  : > "$me"
  flock -u 8
  exec 8>&-
  while :; do
    [ -e "$me" ] || : > "$me"  # removed by a caller that could not see this pid: keep the place
    head=""
    for f in "$queue"/[0-9]*; do
      [ -e "$f" ] || continue
      if alive "${f##*.}"; then
        head="$f"
        break
      fi
      rm -f "$f"
    done
    [ "$head" = "$me" ] && break
    if [ "$waited" = 0 ]; then
      echo "heavy-lock: queued for $lock (ticket $n; other heavy jobs are running or waiting)" >&2
      waited=1
    fi
    interruptible sleep "$poll"
  done
else
  echo "heavy-lock: cannot use the queue $queue; waiting on $lock without it (not first come, first served)" >&2
fi
if ! flock -n 9; then
  [ "$waited" = 0 ] && echo "heavy-lock: waiting for $lock (another heavy job is running)" >&2
  # The lock belongs to the open file (fd 9, shared with the child), so it stays held after flock exits.
  interruptible flock 9 || { echo "heavy-lock: flock failed on $lock" >&2; exit 2; }
fi
[ -n "$me" ] && rm -f "$me"
trap - EXIT HUP INT TERM
SDD_HEAVY_LOCK_HELD="$lock"
export SDD_HEAVY_LOCK_HELD
exec "$@"
