#ifndef PES2_RUNNER_H
#define PES2_RUNNER_H

#include <stdint.h>
#include <sys/types.h>

/*
 * PES2 User Namespace Identity & Capability Model:
 *
 * 1. Single Unprivileged Mapping:
 *    The runner uses unprivileged CLONE_NEWUSER. Without root privileges or setuid helpers
 *    (newuidmap/newgidmap), Linux allows writing exactly one mapping to uid_map / gid_map,
 *    mapping namespace UID/GID 0 to the invoking host UID/GID (getuid() / getgid()).
 *
 * 2. Setup Privilege:
 *    Inside the new user namespace, UID 0 initially possesses capabilities (CAP_SYS_ADMIN, etc.)
 *    within that namespace. This is required to perform unprivileged mount operations (bind-mounting
 *    read-only system directories, creating an ephemeral tmpfs /tmp, and bind-mounting /workspace)
 *    and chrooting into the private rootfs without host root access.
 *
 * 3. Complete Capability Stripping Before Target Execution:
 *    Once the isolated mount tree and chroot are in place, the child process permanently clears all
 *    capabilities using SYS_capset (cap_data = {{0}}). PR_SET_DUMPABLE is set to 0, and
 *    PR_SET_NO_NEW_PRIVS is locked by the seccomp filter.
 *
 * 4. Runtime Identity:
 *    The target process executes as namespace UID/GID 0 with zero capabilities inside the sandbox.
 *    On the host system, all actions map strictly to the invoking unprivileged host user. The target
 *    does not switch to an unmapped UID (such as 65534 nobody) because in a single-mapping namespace
 *    any other UID is unmapped (overflowuid), which would prevent access to workspace files and
 *    require multi-user subuid delegations.
 */

#define RUNNER_OUTPUT_CAP_BYTES 65536U
#define RUNNER_MAX_TIMEOUT_MS 60000U
#define RUNNER_MIN_MEMORY_KB 16384U
#define RUNNER_MAX_MEMORY_KB 1048576U
#define RUNNER_MAX_PIDS 128U

typedef enum {
    STATUS_OK = 0,
    STATUS_RUNTIME_ERROR = 1,
    STATUS_TIMEOUT = 2,
    STATUS_OUTPUT_LIMIT = 3,
    STATUS_MEMORY_LIMIT = 4,
    STATUS_BLOCKED_BY_POLICY = 5,
    STATUS_SANDBOX_ERROR = 6,
    STATUS_CANCELLED = 7
} runner_status_t;

typedef enum {
    SECCOMP_PROFILE_STRICT = 0,
    SECCOMP_PROFILE_COMPILER = 1,
    SECCOMP_PROFILE_PYTHON = 2,
    SECCOMP_PROFILE_BASH = 3,
    SECCOMP_PROFILE_JVM = 4,
    SECCOMP_PROFILE_RUBY = 5,
    SECCOMP_PROFILE_NODE = 6,
    SECCOMP_PROFILE_PHP = 7,
    SECCOMP_PROFILE_DART = 8,
    SECCOMP_PROFILE_RACKET = 9
} seccomp_profile_t;

typedef struct {
    const char *target_bin;
    char *const *target_argv;
    const char *workspace_dir;
    const char *cgroup_root;
    uint32_t cpu_timeout_ms;
    uint32_t wall_timeout_ms;
    uint32_t memory_limit_kb;    /* cgroup-charged memory, not virtual address space */
    uint32_t max_output_bytes;
    uint32_t max_pids;
    seccomp_profile_t seccomp_profile;
    char cgroup_name[64];
} runner_config_t;

typedef struct {
    runner_status_t status;
    int exit_code;
    int term_signal;
    uint64_t cpu_time_ms;
    uint64_t wall_time_ms;
    uint64_t peak_memory_kb;
    char *stdout_buf;
    uint32_t stdout_len;
    char *stderr_buf;
    uint32_t stderr_len;
} runner_result_t;

int runner_execute(const runner_config_t *config, runner_result_t *result);
void runner_result_destroy(runner_result_t *result);

int cgroup_init(const char *root, const char *name, uint32_t max_pids,
                uint32_t memory_limit_kb);
int cgroup_attach(const char *root, const char *name, pid_t pid);
int cgroup_kill(const char *root, const char *name);
int cgroup_cleanup(const char *root, const char *name);
int cgroup_peak_memory_kb(const char *root, const char *name, uint64_t *peak_kb);
int cgroup_oom_kill_count(const char *root, const char *name, uint64_t *count);
int cgroup_cpu_usage_ms(const char *root, const char *name, uint64_t *usage_ms);

int seccomp_apply_profile(seccomp_profile_t profile);
int seccomp_apply_strict_filter(void);

#endif
