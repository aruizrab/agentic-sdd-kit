#!/bin/sh
# memcap-runner.sh: run a command with its data memory capped (ulimit -d, RLIMIT_DATA).
#
#   memcap-runner.sh <command> [args...]
#
# SDD_MEM_LIMIT_KB (KiB, default 2097152 = 2 GiB; 0 = no cap) is the limit. The limit applies
# to each process separately (every child inherits its own copy), not to the tree as a whole.
# Use it as a test runner hook (Rust: CARGO_TARGET_<TRIPLE>_RUNNER), so a mutant that turns a
# loop into a runaway allocator fails alone, counted as caught, instead of exhausting the host.
# `sdd check mutation` sets SDD_MEM_LIMIT_KB from quality.mutation.memory_limit_mib.
#
# Why the data limit and not the address space (ulimit -v): runtimes such as Go reserve large
# PROT_NONE address ranges they never touch, so an address-space cap made a Go tool the tests
# run (gitleaks) panic at start-up and failed the unmutated baseline. Since Linux 4.7
# RLIMIT_DATA counts private writable mappings (heap, anonymous mmap, thread arenas) but not
# such reservations: those tools run normally, and a test that allocates and touches 3 GiB is
# still stopped at a 2 GiB cap. On kernels or systems that do not enforce RLIMIT_DATA for mmap
# (Linux before 4.7, macOS) the cap is weaker: add the memory guard or a cgroup limit there.
# If the cap cannot be set the command does not run (exit 125): an uncapped run is not
# silently treated as a capped one.
limit="${SDD_MEM_LIMIT_KB:-2097152}"
if [ "$#" -eq 0 ]; then
  echo "usage: memcap-runner.sh <command> [args...]" >&2
  exit 2
fi
if [ "$limit" != 0 ]; then
  ulimit -d "$limit" 2>/dev/null || {
    echo "memcap-runner: cannot set ulimit -d $limit on this platform; set quality.mutation.memory_limit_mib to 0 and cap memory another way (docs/PLAYBOOK.md §14)" >&2
    exit 125
  }
fi
exec "$@"
