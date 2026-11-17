#!/usr/bin/env bash
# Discovers a writable cgroup v2 subtree with required controllers (cpu, memory, pids).
# Supports PES2_CGROUP_ROOT as explicit override.
set -euo pipefail

discover_cgroup_root() {
    # If caller explicitly set PES2_CGROUP_ROOT, validate and use it
    if [ -n "${PES2_CGROUP_ROOT:-}" ]; then
        if [ -d "$PES2_CGROUP_ROOT" ] && [ -r "$PES2_CGROUP_ROOT/cgroup.subtree_control" ] && [ -w "$PES2_CGROUP_ROOT/cgroup.subtree_control" ]; then
            local sc
            sc=$(cat "$PES2_CGROUP_ROOT/cgroup.subtree_control" 2>/dev/null || true)
            if echo "$sc" | grep -qw "memory" && echo "$sc" | grep -qw "pids" && echo "$sc" | grep -qw "cpu"; then
                echo "$PES2_CGROUP_ROOT"
                return 0
            fi
        fi
        return 1
    fi

    local uid
    uid=$(id -u)
    local candidates=()

    # 1. Process cgroup hierarchy and ancestors from /proc/self/cgroup
    if [ -f /proc/self/cgroup ]; then
        local self_cgroup
        self_cgroup=$(sed -n "s/^0:://p" /proc/self/cgroup | tr -d " \r\n")
        if [ -n "$self_cgroup" ]; then
            local curr="/sys/fs/cgroup$self_cgroup"
            while [ "$curr" != "/sys/fs/cgroup" ] && [ "$curr" != "/" ] && [ "$curr" != "." ]; do
                candidates+=("$curr")
                curr=$(dirname "$curr")
            done
        fi
    fi

    # 2. Standard systemd user delegation slice
    candidates+=("/sys/fs/cgroup/user.slice/user-${uid}.slice/user@${uid}.service")

    # 3. Root cgroup
    candidates+=("/sys/fs/cgroup")

    for cand in "${candidates[@]}"; do
        if [ -d "$cand" ] && [ -r "$cand/cgroup.subtree_control" ] && [ -w "$cand/cgroup.subtree_control" ]; then
            local sc
            sc=$(cat "$cand/cgroup.subtree_control" 2>/dev/null || true)
            if echo "$sc" | grep -qw "memory" && echo "$sc" | grep -qw "pids" && echo "$sc" | grep -qw "cpu"; then
                local probe="$cand/pes2-probe-$$"
                if mkdir "$probe" 2>/dev/null; then
                    rmdir "$probe" 2>/dev/null || true
                    echo "$cand"
                    return 0
                fi
            fi
        fi
    done
    return 1
}

if ! discover_cgroup_root; then
    exit 1
fi
