#!/bin/sh
# heavy-stop.sh: stop the heavy jobs (systemd scopes) that run in a given worktree.
#
#   tools/sdd/scripts/heavy-stop.sh [--dry-run] <worktree-dir>
#
# Stopping a pipeline run (cancelling its agent, killing its session) does NOT stop the heavy job
# it started: the job runs in its own systemd scope (heavy-lock.sh, `sdd-heavy-<ticket>-<pid>.scope`)
# and keeps running, holding its place and its memory in the heavy-job queue until it ends by
# itself. Run this after stopping a run, with the run's worktree: it stops every `sdd-heavy-*`
# scope with a process whose working directory is that worktree or below it, which ends the job's
# wrapper and every process of the job and frees its slot (the wrapper's EXIT trap removes the
# ticket and the running record; a dead pid is dropped by the queue in any case).
#
# Only scopes are touched, never other processes, and only those of the given worktree: jobs of
# other worktrees keep running. A job that waits in the queue (no scope yet) belongs to the
# caller's shell and ends with it. --dry-run lists the scopes without stopping them.
# Needs `systemctl --user` (the same systemd user session heavy-lock.sh uses for its scopes).
# SDD_HEAVY_STOP_SYSTEMCTL and SDD_CGROUP_ROOT (default /sys/fs/cgroup) exist for the tests.
set -eu
dry=0
if [ "${1:-}" = "--dry-run" ]; then
  dry=1
  shift
fi
if [ "$#" -ne 1 ] || [ ! -d "$1" ]; then
  echo "usage: heavy-stop.sh [--dry-run] <worktree-dir>" >&2
  exit 2
fi
dir="$(cd "$1" && pwd -P)"
ctl="${SDD_HEAVY_STOP_SYSTEMCTL:-systemctl}"
root="${SDD_CGROUP_ROOT:-/sys/fs/cgroup}"
if [ -z "${XDG_RUNTIME_DIR:-}" ] && [ -d "/run/user/$(id -u)" ]; then
  XDG_RUNTIME_DIR="/run/user/$(id -u)"
  export XDG_RUNTIME_DIR
fi
units="$("$ctl" --user list-units --all --plain --no-legend --no-pager 'sdd-heavy-*.scope' 2>/dev/null | awk '{print $1}')" || units=""
found=0
for unit in $units; do
  case "$unit" in sdd-heavy-*.scope) ;; *) continue ;; esac
  cg="$("$ctl" --user show -p ControlGroup --value "$unit" 2>/dev/null)" || cg=""
  [ -n "$cg" ] && [ -r "$root$cg/cgroup.procs" ] || continue
  hit=0
  for pid in $(cat "$root$cg/cgroup.procs" 2>/dev/null); do
    cwd="$(readlink "/proc/$pid/cwd" 2>/dev/null)" || continue
    case "$cwd/" in "$dir"/*) hit=1; break ;; esac
  done
  [ "$hit" = 1 ] || continue
  found=$((found + 1))
  if [ "$dry" = 1 ]; then
    echo "would stop $unit"
  else
    "$ctl" --user stop "$unit" && echo "stopped $unit"
  fi
done
[ "$found" -gt 0 ] || echo "heavy-stop: no heavy scope runs in $dir"
