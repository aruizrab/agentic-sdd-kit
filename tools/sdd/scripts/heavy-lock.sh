#!/bin/sh
# heavy-lock.sh: run a heavy build or test job under a host-wide lock, first come first served:
# one job at a time, or (with a memory budget) as many as fit in the budget, each capped.
#
#   heavy-lock.sh <command> [args...]
#
# Coverage, mutation testing, full builds and full test runs of concurrent pipelines (other
# worktrees, other projects) queue on one lock file instead of running at once and exhausting
# memory. SDD_HEAVY_LOCK names the lock file (default /tmp/sdd-heavy-job.lock: one per host,
# the same for every worktree). A relative SDD_HEAVY_LOCK (e.g. .wt/locks/heavy.lock) is resolved
# against the MAIN checkout of the current git repository (found via `git rev-parse
# --git-common-dir`), so the main checkout and every worktree of a project queue on one lock;
# its directory is created. tools/sdd/scripts/heavy.sh sets the lock, the budget and the job
# size from sdd.config.json (quality.heavy_lock, heavy_budget_mb, heavy_job_mb). A nested call for the same lock file runs the command directly
# (SDD_HEAVY_LOCK_HELD), so wrapping both a gate plan and its gates cannot deadlock; it runs
# inside its parent's slot (and memory cap).
#
# The queue is fair. flock(1) alone is not: a job that loops over long locked steps re-took the
# lock the moment it released it and kept a short job waiting for hours. So every caller first
# draws a ticket in <lock>.queue/ (a counter under a short mutex) and only the oldest waiting
# ticket may start; a job that comes back for another step queues behind the jobs already
# waiting. Tickets of callers that died are dropped (pid no longer alive), and a cancelled
# caller removes its own. SDD_HEAVY_LOCK_POLL (seconds, default 2) is how often a queued caller
# checks whether it is next.
#
# One at a time (SDD_HEAVY_BUDGET_MB unset or 0, the default): the oldest ticket takes an
# exclusive flock on the lock file and holds it until the command and every child that keeps
# its file descriptor exit.
#
# Memory budget (SDD_HEAVY_BUDGET_MB=<MB> > 0): every job declares SDD_HEAVY_JOB_MB (default
# half the budget). The oldest waiting ticket starts as soon as the MB of the running jobs plus
# its own fit in the budget; nobody overtakes it, so a big job waits for room and is never
# starved. A job bigger than the whole budget runs alone. A running job keeps its ticket (so
# an older heavy-lock.sh, which only runs at the head of the queue, waits behind it) and a
# record in <lock>.queue/.running/ (its MB; dropped when its pid is gone), and holds a shared
# flock on the lock file (so a one-at-a-time caller's exclusive flock waits for it). A job that
# holds the exclusive flock (a one-at-a-time caller or an older heavy-lock.sh) counts as
# SDD_HEAVY_LEGACY_MB (default half the budget). When `systemd-run --user` works, the job runs
# in its own systemd scope with MemoryMax=<job MB> and MemorySwapMax=0: a job that outgrows its
# declaration is OOM-killed inside its scope (exit 137 or a failed test), nothing else is touched.
# SDD_HEAVY_CAP=0 skips the scope; without systemd the job runs uncapped, with a warning (the
# budget accounting still applies). SDD_HEAVY_HIGH_PCT (default 0 = off) also sets MemoryHigh
# at that percentage of the cap (the kernel throttles and reclaims above it). Every finished job
# appends one line to <lock>.jobs.log: start, end, seconds, MB declared, peak MB used (memory.peak
# of its scope), exit code, OOM kills, cap, working directory and command; size SDD_HEAVY_JOB_MB
# from the peaks. All callers of one lock file must use the same budget setting.
#
# Needs flock(1) (util-linux); without it the command runs unlocked, with a warning.
lock="${SDD_HEAVY_LOCK:-/tmp/sdd-heavy-job.lock}"
case "$lock" in
  /*) ;;
  *)
    common="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null || git rev-parse --git-common-dir 2>/dev/null)" || {
      echo "heavy-lock: relative SDD_HEAVY_LOCK '$lock' needs a git repository (it is resolved against the main checkout)" >&2
      exit 2
    }
    main="$(cd "$common/.." && pwd)" || exit 2
    lock="$main/$lock"
    mkdir -p "$(dirname "$lock")" || { echo "heavy-lock: cannot create $(dirname "$lock")" >&2; exit 2; }
    ;;
esac
poll="${SDD_HEAVY_LOCK_POLL:-2}"
budget="${SDD_HEAVY_BUDGET_MB:-0}"
if [ "$#" -eq 0 ]; then
  echo "usage: heavy-lock.sh <command> [args...]" >&2
  exit 2
fi
if [ "${SDD_HEAVY_LOCK_HELD:-}" = "$lock" ]; then
  exec "$@"
fi
isnum() { case "$1" in '' | *[!0-9]*) return 1 ;; esac; }
if ! isnum "$budget"; then
  echo "heavy-lock: SDD_HEAVY_BUDGET_MB must be a whole number of MB (got '$budget')" >&2
  exit 2
fi
if [ "$budget" -gt 0 ]; then
  mb="${SDD_HEAVY_JOB_MB:-$((budget / 2))}"
  legacy_mb="${SDD_HEAVY_LEGACY_MB:-$((budget / 2))}"
  high="${SDD_HEAVY_HIGH_PCT:-0}"
  if ! isnum "$mb" || [ "$mb" -eq 0 ] || ! isnum "$legacy_mb" || ! isnum "$high" || [ "$high" -gt 100 ]; then
    echo "heavy-lock: SDD_HEAVY_JOB_MB (>= 1), SDD_HEAVY_LEGACY_MB and SDD_HEAVY_HIGH_PCT (0-100) must be whole numbers" >&2
    exit 2
  fi
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
runf=""
n=""
waited=0
trap 'rm -f "$me" ${runf:+"$runf"}; [ -n "$child" ] && kill "$child" 2>/dev/null' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

# Draws the next ticket under the mutex (fd 8, left open): sets n and me.
draw_ticket() {
  mkdir -p "$queue" 2>/dev/null && [ -w "$queue" ] && command exec 8>>"$queue/.mutex" || return 1
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
}

if [ "$budget" -gt 0 ]; then
  runs="$queue/.running"
  if draw_ticket && mkdir -p "$runs" 2>/dev/null && [ -w "$runs" ]; then
    chmod a+rwx "$runs" 2>/dev/null || true
  else
    echo "heavy-lock: cannot use the queue $queue; running one at a time instead of within the budget" >&2
    [ -n "$me" ] && rm -f "$me"
    me=""
    budget=0
  fi
fi

if [ "$budget" -gt 0 ]; then
  log="$lock.jobs.log"
  cap=""
  if [ "${SDD_HEAVY_CAP:-1}" != 0 ]; then
    if [ -z "${XDG_RUNTIME_DIR:-}" ] && [ -d "/run/user/$(id -u)" ]; then
      XDG_RUNTIME_DIR="/run/user/$(id -u)"
      export XDG_RUNTIME_DIR
    fi
    to=""
    command -v timeout >/dev/null 2>&1 && to="timeout 10"
    if ! command -v systemd-run >/dev/null 2>&1; then
      :
    # systemd 254+ expands $VAR in the command line unless told not to; older ones have no flag.
    # OOMPolicy=continue: an OOM kill takes the biggest process only, systemd does not stop the
    # whole scope (the job wrapper still logs and frees the slot).
    elif $to systemd-run --user --scope -q --collect --expand-environment=no -p OOMPolicy=continue -p MemoryMax=64M -- true >/dev/null 2>&1; then
      cap="--expand-environment=no -p OOMPolicy=continue"
    elif $to systemd-run --user --scope -q --collect -p MemoryMax=64M -- true >/dev/null 2>&1; then
      cap="-q"
    fi
    [ -n "$cap" ] || echo "heavy-lock: systemd-run --user is not usable; running WITHOUT a memory cap (the budget still applies)" >&2
  fi
  if [ "$mb" -gt "$budget" ]; then
    echo "heavy-lock: this job declares $mb MB, more than the budget of $budget MB: it runs alone" >&2
  fi
  while :; do
    [ -e "$me" ] || : > "$me"  # removed by a caller that could not see this pid: keep the place
    flock 8
    used=0
    jobs=0
    for f in "$runs"/[0-9]*; do
      [ -e "$f" ] || continue
      if alive "${f##*.}"; then
        m=""
        { read -r m _ < "$f"; } 2>/dev/null
        isnum "$m" || m=0
        used=$((used + m))
        jobs=$((jobs + 1))
        # Its ticket keeps one-at-a-time callers behind it: put it back if a caller that could
        # not see the pid dropped it.
        [ -e "$queue/${f##*/}" ] || : > "$queue/${f##*/}"
      else
        rm -f "$f" "$queue/${f##*/}"
      fi
    done
    head=""
    for f in "$queue"/[0-9]*; do
      [ -e "$f" ] || continue
      if ! alive "${f##*.}"; then
        rm -f "$f"
        continue
      fi
      [ -e "$runs/${f##*/}" ] && continue  # running, not waiting
      head="$f"
      break
    done
    held=0
    if [ "$head" = "$me" ]; then
      # An exclusive flock is a one-at-a-time job (or an older heavy-lock.sh): count it.
      if flock -n -s 9; then
        held=1
      else
        used=$((used + legacy_mb))
        jobs=$((jobs + 1))
      fi
      if [ "$jobs" -eq 0 ] || [ $((used + mb)) -le "$budget" ]; then
        runf="$runs/${me##*/}"
        printf '%s %s %s\n' "$mb" "$(date +%Y-%m-%dT%H:%M:%S%z)" "$*" | cut -c1-500 > "$runf"
        chmod a+rw "$runf" 2>/dev/null || true
        flock -u 8
        break
      fi
      [ "$held" = 1 ] && flock -u 9
    fi
    flock -u 8
    if [ "$waited" = 0 ]; then
      echo "heavy-lock: queued for $lock (ticket $n, $mb MB; $used of $budget MB in use by $jobs job(s), or older jobs waiting)" >&2
      waited=1
    fi
    interruptible sleep "$poll"
  done
  exec 8>&-
  trap - EXIT HUP INT TERM
  SDD_HEAVY_LOCK_HELD="$lock"
  export SDD_HEAVY_LOCK_HELD
  # The job wrapper keeps this pid (systemd-run --scope execs it), runs the command as its child
  # (the command does not inherit the lock descriptor), passes on signals, logs the job and
  # frees its slot. Background commands of a script get /dev/null as stdin, hence fd 3.
  # shellcheck disable=SC2016
  job='
me=$1 runf=$2 log=$3 mb=$4 scoped=$5
shift 5
t0=$(date +%s)
start=$(date +%Y-%m-%dT%H:%M:%S%z)
cg=$(sed -n "s/^0:://p" /proc/self/cgroup 2>/dev/null)
d=/sys/fs/cgroup$cg
command exec 3<&0 || exec 3</dev/null
"$@" 0<&3 3<&- 9>&- &
child=$!
exec 3<&-
# The command ignores SIGINT (a background command of a script): stop it, and in a scope every
# process of the job, with SIGTERM.
stop() {
  if [ "$scoped" = 1 ] && [ -r "$d/cgroup.procs" ]; then
    for p in $(cat "$d/cgroup.procs"); do [ "$p" = $$ ] || kill -TERM "$p" 2>/dev/null; done
  else
    kill -TERM "$child" 2>/dev/null
  fi
}
trap stop HUP INT TERM
while :; do
  wait "$child"
  rc=$?
  kill -0 "$child" 2>/dev/null || break
done
peak=- ooms=- cap=none
if [ "$scoped" = 1 ] && [ -r "$d/memory.peak" ] && max=$(cat "$d/memory.max" 2>/dev/null) && [ "$max" != max ] && [ -n "$max" ]; then
  case $max in *[!0-9]*) ;; *) cap=$((max / 1048576))M ;; esac
  peak=$(cat "$d/memory.peak" 2>/dev/null)
  case $peak in "" | *[!0-9]*) peak=- ;; *) peak=$((peak / 1048576)) ;; esac
  ooms=$(sed -n "s/^oom_kill //p" "$d/memory.events" 2>/dev/null)
fi
cmd=$(printf "%s " "$@" | tr "\n\t" "  " | cut -c1-300)
printf "start=%s\tend=%s\tsecs=%s\tmb=%s\tpeak_mb=%s\trc=%s\toom_kills=%s\tcap=%s\tcwd=%s\tcmd=%s\n" \
  "$start" "$(date +%Y-%m-%dT%H:%M:%S%z)" "$(($(date +%s) - t0))" "$mb" "$peak" "$rc" "${ooms:--}" "$cap" "$PWD" "${cmd% }" >> "$log" 2>/dev/null
chmod a+rw "$log" 2>/dev/null
rm -f "$runf" "$me"
exit "$rc"'
  if [ -n "$cap" ]; then
    hp=""
    [ "$high" -gt 0 ] && hp="-p MemoryHigh=$((mb * high / 100))M"
    # shellcheck disable=SC2086
    exec systemd-run --user --scope -q --collect $cap --unit "sdd-heavy-$n-$$" \
      -p "MemoryMax=${mb}M" -p MemorySwapMax=0 $hp -- sh -c "$job" sdd-heavy-job "$me" "$runf" "$log" "$mb" 1 "$@"
  fi
  exec sh -c "$job" sdd-heavy-job "$me" "$runf" "$log" "$mb" 0 "$@"
fi

if draw_ticket; then
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
