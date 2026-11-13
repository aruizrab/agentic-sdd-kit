#!/bin/sh
# memcap-runner.sh: run a command with its virtual address space capped (ulimit -v).
#
#   memcap-runner.sh <command> [args...]
#
# SDD_MEM_LIMIT_KB (KiB, default 2097152 = 2 GiB; 0 = no cap) is the limit. The limit applies
# to each process separately (every child inherits its own copy), not to the tree as a whole.
# Use it as a test runner hook (Rust: CARGO_TARGET_<TRIPLE>_RUNNER), so a mutant that turns a
# loop into a runaway allocator fails alone, counted as caught, instead of exhausting the host.
# `sdd check mutation` sets SDD_MEM_LIMIT_KB from quality.mutation.memory_limit_mib.
# If the cap cannot be set the command does not run (exit 125): an uncapped run is not
# silently treated as a capped one.
limit="${SDD_MEM_LIMIT_KB:-2097152}"
if [ "$#" -eq 0 ]; then
  echo "usage: memcap-runner.sh <command> [args...]" >&2
  exit 2
fi
if [ "$limit" != 0 ]; then
  ulimit -v "$limit" 2>/dev/null || {
    echo "memcap-runner: cannot set ulimit -v $limit on this platform; set quality.mutation.memory_limit_mib to 0 and cap memory another way (docs/PLAYBOOK.md §14)" >&2
    exit 125
  }
fi
exec "$@"
