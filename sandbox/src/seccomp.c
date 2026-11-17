#include "runner.h"
#include <stdio.h>
#include <errno.h>
#include <seccomp.h>

/*
 * Seccomp-BPF Syscall Filter
 *
 * Concepts to learn:
 *   - seccomp(2): Secure Computing mode in the Linux kernel. BPF programs inspect syscall numbers and arguments.
 *   - SCMP_ACT_ALLOW vs SCMP_ACT_ERRNO: Default-deny policies kill or return errno (e.g. EPERM) for prohibited calls.
 *   - Prohibited syscall families:
 *       - Network: socket(2), connect(2), bind(2), listen(2), accept(2), sendto(2), recvfrom(2).
 *       - Namespaces: clone(2) without flags, unshare(2), setns(2).
 *       - System controls: reboot(2), mount(2), ptrace(2), syslog(2).
 */

int seccomp_apply_strict_filter(void) {
    /*
     * TODO(Piyush): Step 1 - Initialize seccomp context with default action.
     * Use seccomp_init(SCMP_ACT_ALLOW) for base allowlist, or seccomp_init(SCMP_ACT_KILL_PROCESS).
     * Man page: seccomp_init(3)
     */

    /*
     * TODO(Piyush): Step 2 - Add rule to block socket creation.
     * Use seccomp_rule_add(ctx, SCMP_ACT_ERRNO(EPERM), SCMP_SYS(socket), 0);
     * Man page: seccomp_rule_add(3)
     */

    /*
     * TODO(Piyush): Step 3 - Add rules to block ptrace and unauthorized process inspections.
     * seccomp_rule_add(ctx, SCMP_ACT_ERRNO(EPERM), SCMP_SYS(ptrace), 0);
     */

    /*
     * TODO(Piyush): Step 4 - Load the compiled BPF filter into the kernel.
     * Use seccomp_load(ctx); followed by seccomp_release(ctx);
     * Man page: seccomp_load(3)
     */

    return 0;
}
