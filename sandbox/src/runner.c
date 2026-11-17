#include "runner.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <signal.h>
#include <time.h>
#include <errno.h>
#include <sys/wait.h>
#include <sys/time.h>
#include <sys/resource.h>
#include <poll.h>

/*
 * PolyExecution Studio 2 — Core Sandbox Runner
 *
 * Concepts to learn:
 *   - fork(2) & execve(2): Process duplication and binary execution.
 *   - pipe2(2): Unidirectional communication channels created with O_CLOEXEC.
 *   - poll(2): I/O event multiplexing with high-resolution millisecond timeout.
 *   - clock_gettime(2): Monotonic wall-clock timing immune to system clock jumps (CLOCK_MONOTONIC).
 *   - wait4(2): Reaping child processes and extracting kernel rusage telemetry (ru_utime, ru_stime, ru_maxrss).
 */

static uint64_t get_time_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000 + (uint64_t)ts.tv_nsec / 1000000;
}

int main(int argc, char *argv[]) {
    if (argc < 2) {
        fprintf(stderr, "Usage: %s <target_binary> [args...]\n", argv[0]);
        return STATUS_SANDBOX_ERROR;
    }

    /*
     * TODO(Piyush): Step 1 - Initialize configuration.
     * Populate runner_config_t with command line arguments, default timeouts, and memory limits.
     */

    /*
     * TODO(Piyush): Step 2 - Create stdout and stderr pipes using pipe2(fds, O_CLOEXEC).
     * Man page: pipe(2), pipe2(2)
     */

    /*
     * TODO(Piyush): Step 3 - Fork child process.
     * In child:
     *   - Attach to cgroup: cgroup_attach(config.cgroup_name, getpid());
     *   - Redirect stdout and stderr using dup2(2);
     *   - Set rlimits using setrlimit(2) (RLIMIT_CPU, RLIMIT_FSIZE);
     *   - Apply seccomp filter: seccomp_apply_strict_filter();
     *   - Execute binary: execve(target_bin, target_argv, envp);
     * In parent:
     *   - Record start timestamp via get_time_ms();
     *   - Run non-blocking poll(2) loop reading stdout/stderr up to max_output_bytes;
     *   - Enforce wall_timeout_ms; if exceeded, kill child with SIGKILL;
     *   - Collect status and rusage metrics using wait4(pid, &wstatus, 0, &usage);
     */

    printf("PES 2 Sandbox Runner Skeleton\n");
    printf("Target binary: %s\n", argv[1]);
    printf("System clock (monotonic): %lu ms\n", (unsigned long)get_time_ms());
    printf("Status: Ready for Tier A implementation by Piyush.\n");

    return STATUS_OK;
}
