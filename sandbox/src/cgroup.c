#include "runner.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/stat.h>

/*
 * Cgroup v2 Isolation Subsystem
 *
 * Concepts to learn:
 *   - cgroups(7): Linux Control Groups v2 unified hierarchy located at /sys/fs/cgroup.
 *   - pids.max: Limits the total number of tasks (processes + threads) inside the group to prevent fork bombs.
 *   - memory.max: Hard memory ceiling. Breaching this triggers the kernel OOM killer (sending SIGKILL).
 *   - cgroup.procs: Writing a PID to this file moves the task into the control group.
 */

int cgroup_init(const char *cgroup_name, uint32_t max_pids, uint32_t memory_limit_kb) {
    if (!cgroup_name) return -1;

    char cgroup_path[256];
    snprintf(cgroup_path, sizeof(cgroup_path), "/sys/fs/cgroup/%s", cgroup_name);

    /*
     * TODO(Piyush): Step 1 - Create the cgroup directory using mkdir(2) with 0755 permissions.
     * Man page: mkdir(2)
     */

    /*
     * TODO(Piyush): Step 2 - Set pids.max limit.
     * Open "/sys/fs/cgroup/<cgroup_name>/pids.max", write max_pids as string, and close file.
     */

    /*
     * TODO(Piyush): Step 3 - Set memory.max limit.
     * Calculate bytes (memory_limit_kb * 1024), write to "/sys/fs/cgroup/<cgroup_name>/memory.max".
     */

    (void)max_pids;
    (void)memory_limit_kb;
    return 0;
}

int cgroup_attach(const char *cgroup_name, pid_t pid) {
    if (!cgroup_name) return -1;

    /*
     * TODO(Piyush): Step 4 - Attach target process to cgroup.
     * Write pid string to "/sys/fs/cgroup/<cgroup_name>/cgroup.procs".
     * Man page: cgroups(7)
     */

    (void)pid;
    return 0;
}

int cgroup_cleanup(const char *cgroup_name) {
    if (!cgroup_name) return -1;

    char cgroup_path[256];
    snprintf(cgroup_path, sizeof(cgroup_path), "/sys/fs/cgroup/%s", cgroup_name);

    /*
     * TODO(Piyush): Step 5 - Remove cgroup using rmdir(2).
     * Verify all processes inside cgroup.procs have terminated before calling rmdir.
     * Man page: rmdir(2)
     */

    return 0;
}
