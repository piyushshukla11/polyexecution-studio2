#ifndef PES2_RUNNER_H
#define PES2_RUNNER_H

#include <stdint.h>
#include <sys/types.h>

/* Truthful execution result status codes */
typedef enum {
    STATUS_OK = 0,
    STATUS_RUNTIME_ERROR = 1,
    STATUS_TIMEOUT = 2,
    STATUS_OUTPUT_LIMIT = 3,
    STATUS_MEMORY_LIMIT = 4,
    STATUS_BLOCKED_BY_POLICY = 5,
    STATUS_SANDBOX_ERROR = 6
} runner_status_t;

/* Execution configuration and bounded limits */
typedef struct {
    const char *target_bin;       /* Absolute path to target executable */
    char *const *target_argv;     /* Null-terminated argv vector */
    const char *workspace_dir;    /* Isolated ephemeral workspace */
    uint32_t cpu_timeout_ms;      /* CPU time limit ceiling */
    uint32_t wall_timeout_ms;     /* Wall-clock timeout ceiling */
    uint32_t memory_limit_kb;     /* RSS/Virtual memory ceiling */
    uint32_t max_output_bytes;    /* Stdout/stderr byte cap (e.g. 65536) */
    uint32_t max_pids;            /* Cgroup pids.max ceiling (e.g. 32) */
    const char *cgroup_name;      /* Name of dedicated cgroup */
} runner_config_t;

/* Execution result metrics and captured output */
typedef struct {
    runner_status_t status;       /* High-level status */
    int exit_code;                /* Process exit status if exited */
    int term_signal;              /* Fatal signal if terminated by signal */
    uint64_t cpu_time_ms;         /* CPU user + system time */
    uint64_t wall_time_ms;        /* High-resolution wall-clock duration */
    uint64_t peak_memory_kb;      /* Peak RSS reported by wait4 / cgroup */
    char *stdout_buf;             /* Bounded stdout stream */
    uint32_t stdout_len;          /* Number of bytes in stdout */
    char *stderr_buf;             /* Bounded stderr stream */
    uint32_t stderr_len;          /* Number of bytes in stderr */
} runner_result_t;

/* Cgroup v2 lifecycle functions */
int cgroup_init(const char *cgroup_name, uint32_t max_pids, uint32_t memory_limit_kb);
int cgroup_attach(const char *cgroup_name, pid_t pid);
int cgroup_cleanup(const char *cgroup_name);

/* Seccomp-BPF filter functions */
int seccomp_apply_strict_filter(void);

#endif /* PES2_RUNNER_H */
