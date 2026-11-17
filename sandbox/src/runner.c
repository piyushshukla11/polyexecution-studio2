#define _GNU_SOURCE
#include "runner.h"

#include <errno.h>
#include <dirent.h>
#include <fcntl.h>
#include <limits.h>
#include <linux/capability.h>
#include <linux/close_range.h>
#include <linux/limits.h>
#include <linux/sched.h>
#include <poll.h>
#include <sched.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mount.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/time.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define CHILD_STACK_BYTES (1024U * 1024U)
#define POLL_INTERVAL_MS 20
#define ROOTFS_PREFIX "/tmp/pes2-root-XXXXXX"

struct child_context {
    const runner_config_t *config;
    const char *workspace_real;
    const char *rootfs;
    int stdout_write;
    int stderr_write;
    int handshake_read;
    int exec_error_write;
};

static volatile sig_atomic_t cancel_requested;

static void request_cancel(int signal_number) {
    cancel_requested = signal_number;
}

static uint64_t monotonic_ms(void) {
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0) return 0;
    return (uint64_t)ts.tv_sec * 1000U + (uint64_t)ts.tv_nsec / 1000000U;
}

static int write_all(int fd, const char *data, size_t len) {
    size_t offset = 0;
    while (offset < len) {
        ssize_t n = write(fd, data + offset, len - offset);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) return -1;
        offset += (size_t)n;
    }
    return 0;
}

static int write_proc_file(pid_t pid, const char *leaf, const char *value) {
    char path[128];
    int n = snprintf(path, sizeof(path), "/proc/%ld/%s", (long)pid, leaf);
    if (n < 0 || (size_t)n >= sizeof(path)) { errno = ENAMETOOLONG; return -1; }
    int fd = open(path, O_WRONLY | O_CLOEXEC);
    if (fd < 0) return -1;
    int rc = write_all(fd, value, strlen(value));
    int saved = errno;
    if (close(fd) != 0 && rc == 0) return -1;
    errno = saved;
    return rc;
}

static int map_child_ids(pid_t pid, uid_t uid, gid_t gid) {
    char mapping[64];
    char setgroups_path[128];
    int n = snprintf(setgroups_path, sizeof(setgroups_path), "/proc/%ld/setgroups", (long)pid);
    if (n < 0 || (size_t)n >= sizeof(setgroups_path)) { errno = ENAMETOOLONG; return -1; }
    int fd = open(setgroups_path, O_WRONLY | O_CLOEXEC);
    if (fd >= 0) {
        int rc = write_all(fd, "deny", 4);
        int saved = errno;
        if (close(fd) != 0 && rc == 0) return -1;
        errno = saved;
        if (rc != 0) return -1;
    } else if (errno != ENOENT) {
        return -1;
    }

    n = snprintf(mapping, sizeof(mapping), "0 %lu 1\n", (unsigned long)uid);
    if (n < 0 || (size_t)n >= sizeof(mapping) || write_proc_file(pid, "uid_map", mapping) != 0) return -1;
    n = snprintf(mapping, sizeof(mapping), "0 %lu 1\n", (unsigned long)gid);
    if (n < 0 || (size_t)n >= sizeof(mapping) || write_proc_file(pid, "gid_map", mapping) != 0) return -1;
    return 0;
}

static int make_dir(const char *path, mode_t mode) {
    if (mkdir(path, mode) == 0 || errno == EEXIST) return 0;
    return -1;
}

static int create_rootfs(const char *rootfs) {
    static const char *const dirs[] = {
        "usr", "bin", "lib", "lib64", "etc", "workspace", "tmp", "dev"
    };
    char path[PATH_MAX];
    for (size_t i = 0; i < sizeof(dirs) / sizeof(dirs[0]); ++i) {
        int n = snprintf(path, sizeof(path), "%s/%s", rootfs, dirs[i]);
        if (n < 0 || (size_t)n >= sizeof(path) || make_dir(path, 0755) != 0) return -1;
    }
    const char *files[] = {"etc/ld.so.cache", "dev/null"};
    for (size_t i = 0; i < sizeof(files) / sizeof(files[0]); ++i) {
        int n = snprintf(path, sizeof(path), "%s/%s", rootfs, files[i]);
        if (n < 0 || (size_t)n >= sizeof(path)) { errno = ENAMETOOLONG; return -1; }
        int fd = open(path, O_WRONLY | O_CREAT | O_CLOEXEC, 0644);
        if (fd < 0 || close(fd) != 0) return -1;
    }
    return 0;
}

static int bind_mount(const char *source, const char *target, int read_only) {
    /* Do not recursively expose nested host mounts below any bind source. */
    if (mount(source, target, NULL, MS_BIND, NULL) != 0) return -1;
    if (read_only && mount(NULL, target, NULL, MS_BIND | MS_REMOUNT | MS_RDONLY |
                           MS_NOSUID | MS_NODEV, NULL) != 0) return -1;
    return 0;
}

static int setup_mounts(const struct child_context *ctx) {
    char target[PATH_MAX];
    if (mount(NULL, "/", NULL, MS_REC | MS_PRIVATE, NULL) != 0) return -1;

    const char *const system_dirs[] = {"/usr", "/bin", "/lib", "/lib64"};
    for (size_t i = 0; i < sizeof(system_dirs) / sizeof(system_dirs[0]); ++i) {
        if (access(system_dirs[i], F_OK) != 0) continue;
        int n = snprintf(target, sizeof(target), "%s%s", ctx->rootfs, system_dirs[i]);
        if (n < 0 || (size_t)n >= sizeof(target) || bind_mount(system_dirs[i], target, 1) != 0) return -1;
    }

    int n = snprintf(target, sizeof(target), "%s/workspace", ctx->rootfs);
    if (n < 0 || (size_t)n >= sizeof(target) || bind_mount(ctx->workspace_real, target, 0) != 0) return -1;
    if (mount(NULL, target, NULL, MS_BIND | MS_REMOUNT | MS_NOSUID | MS_NODEV, NULL) != 0) return -1;

    n = snprintf(target, sizeof(target), "%s/etc/ld.so.cache", ctx->rootfs);
    if (n < 0 || (size_t)n >= sizeof(target)) { errno = ENAMETOOLONG; return -1; }
    if (access("/etc/ld.so.cache", R_OK) == 0 && bind_mount("/etc/ld.so.cache", target, 1) != 0) return -1;

    n = snprintf(target, sizeof(target), "%s/dev/null", ctx->rootfs);
    if (n < 0 || (size_t)n >= sizeof(target) || bind_mount("/dev/null", target, 0) != 0) return -1;

    n = snprintf(target, sizeof(target), "%s/tmp", ctx->rootfs);
    if (n < 0 || (size_t)n >= sizeof(target) ||
        mount("tmpfs", target, "tmpfs", MS_NOSUID | MS_NODEV | MS_NOEXEC,
              "size=16m,mode=1777") != 0) return -1;
    return 0;
}

static int target_is_allowed(const char *target) {
    if (!target || target[0] != '/') return 0;
    for (const char *part = target; *part;) {
        while (*part == '/') ++part;
        const char *end = strchr(part, '/');
        size_t length = end ? (size_t)(end - part) : strlen(part);
        if (length == 2 && part[0] == '.' && part[1] == '.') return 0;
        if (!end) break;
        part = end + 1;
    }
    return strncmp(target, "/workspace/", 11) == 0 ||
           strncmp(target, "/usr/", 5) == 0 ||
           strncmp(target, "/bin/", 5) == 0 ||
           strncmp(target, "/lib/", 5) == 0 ||
           strncmp(target, "/lib64/", 7) == 0;
}

static int apply_child_limits(const runner_config_t *config) {
    struct rlimit limit;
    limit.rlim_cur = limit.rlim_max = (rlim_t)((config->cpu_timeout_ms + 999U) / 1000U + 1U);
    if (setrlimit(RLIMIT_CPU, &limit) != 0) return -1;
    limit.rlim_cur = limit.rlim_max = 1024U * 1024U;
    if (setrlimit(RLIMIT_FSIZE, &limit) != 0) return -1;
    limit.rlim_cur = limit.rlim_max = 64;
    if (setrlimit(RLIMIT_NOFILE, &limit) != 0) return -1;
    limit.rlim_cur = limit.rlim_max = 0;
    if (setrlimit(RLIMIT_CORE, &limit) != 0) return -1;
    return 0;
}

static int close_child_fds_except(int keep_fd) {
#ifdef SYS_close_range
    int failed = 0;
    if (keep_fd > 3 && syscall(SYS_close_range, 3U, (unsigned int)keep_fd - 1U, 0U) != 0) failed = 1;
    if (syscall(SYS_close_range, (unsigned int)keep_fd + 1U, UINT_MAX, 0U) != 0) failed = 1;
    if (!failed) return 0;
#endif
    long max_fd = sysconf(_SC_OPEN_MAX);
    if (max_fd < 0) max_fd = 65536;
    for (int fd = 3; fd < max_fd; ++fd) {
        if (fd != keep_fd && close(fd) != 0 && errno != EBADF) return -1;
    }
    return 0;
}

static void child_error(int fd, int error_number) {
    (void)write(fd, &error_number, sizeof(error_number));
    _exit(125);
}

static int child_main(void *opaque) {
    const struct child_context *ctx = opaque;
    char token;
    ssize_t got;
    do { got = read(ctx->handshake_read, &token, 1); } while (got < 0 && errno == EINTR);
    if (got != 1) child_error(ctx->exec_error_write, EPIPE);
    close(ctx->handshake_read);

    if (setresgid(0, 0, 0) != 0 || setresuid(0, 0, 0) != 0) child_error(ctx->exec_error_write, errno);
    if (dup2(ctx->stdout_write, STDOUT_FILENO) < 0 ||
        dup2(ctx->stderr_write, STDERR_FILENO) < 0) child_error(ctx->exec_error_write, errno);
    int null_fd = open("/dev/null", O_RDONLY | O_CLOEXEC);
    if (null_fd < 0 || dup2(null_fd, STDIN_FILENO) < 0) child_error(ctx->exec_error_write, errno);
    close(null_fd);
    close(ctx->stdout_write);
    close(ctx->stderr_write);
    if (close_child_fds_except(ctx->exec_error_write) != 0) child_error(ctx->exec_error_write, errno);

    if (apply_child_limits(ctx->config) != 0) child_error(ctx->exec_error_write, errno);
    if (setup_mounts(ctx) != 0) child_error(ctx->exec_error_write, errno);
    if (chroot(ctx->rootfs) != 0 || chdir("/") != 0) child_error(ctx->exec_error_write, errno);
    if (chdir("/workspace") != 0) child_error(ctx->exec_error_write, errno);

    /* Remove namespace capabilities after mount/root setup and before running user code. */
    struct __user_cap_header_struct cap_header = {
        .version = _LINUX_CAPABILITY_VERSION_3,
        .pid = 0
    };
    struct __user_cap_data_struct cap_data[2] = {{0}};
    if (syscall(SYS_capset, &cap_header, cap_data) != 0) child_error(ctx->exec_error_write, errno);
    if (prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) != 0) child_error(ctx->exec_error_write, errno);
    if (seccomp_apply_strict_filter() != 0) child_error(ctx->exec_error_write, errno);

    char *const safe_env[] = {
        "PATH=/usr/bin:/bin",
        "HOME=/workspace",
        "TMPDIR=/tmp",
        "LANG=C",
        NULL
    };
    execve(ctx->config->target_bin, ctx->config->target_argv, safe_env);
    child_error(ctx->exec_error_write, errno);
    return 125;
}

static int set_nonblocking(int fd) {
    int flags = fcntl(fd, F_GETFL);
    return flags < 0 ? -1 : fcntl(fd, F_SETFL, flags | O_NONBLOCK);
}

static int make_pipe(int fds[2]) {
    return pipe2(fds, O_CLOEXEC);
}

static int drain_output(int fd, char *buffer, uint32_t cap, uint32_t *length,
                        int *eof, int *overflow) {
    char chunk[4096];
    for (;;) {
        ssize_t n = read(fd, chunk, sizeof(chunk));
        if (n > 0) {
            uint32_t available = cap - *length;
            size_t keep = (size_t)n < available ? (size_t)n : available;
            if (keep > 0) {
                memcpy(buffer + *length, chunk, keep);
                *length += (uint32_t)keep;
            }
            if ((size_t)n > keep) *overflow = 1;
            continue;
        }
        if (n == 0) { *eof = 1; return 0; }
        if (errno == EINTR) continue;
        if (errno == EAGAIN || errno == EWOULDBLOCK) return 0;
        return -1;
    }
}

static int remove_tree(const char *path) {
    struct stat st;
    if (lstat(path, &st) != 0) return errno == ENOENT ? 0 : -1;
    if (!S_ISDIR(st.st_mode)) return unlink(path);
    DIR *dir = opendir(path);
    if (!dir) return -1;
    struct dirent *entry;
    int result = 0;
    while ((entry = readdir(dir)) != NULL) {
        if (strcmp(entry->d_name, ".") == 0 || strcmp(entry->d_name, "..") == 0) continue;
        char child[PATH_MAX];
        int n = snprintf(child, sizeof(child), "%s/%s", path, entry->d_name);
        if (n < 0 || (size_t)n >= sizeof(child) || remove_tree(child) != 0) { result = -1; break; }
    }
    int saved = errno;
    if (closedir(dir) != 0 && result == 0) result = -1;
    errno = saved;
    if (result != 0) return -1;
    return rmdir(path);
}

static int set_fd_nonblock(int fd) {
    return set_nonblocking(fd);
}

static int valid_arguments(char *const *argv) {
    size_t total = 0;
    for (size_t i = 0; i < 256; ++i) {
        if (!argv[i]) return i > 0;
        size_t length = strnlen(argv[i], 4097);
        if (length > 4096 || total > 65536U - length - 1U) return 0;
        total += length + 1U;
    }
    return 0;
}

static int kill_sandbox(const char *root, const char *name, pid_t child_pid) {
    if (cgroup_kill(root, name) == 0) return 0;
    int saved = errno;
    if (child_pid > 0 && kill(child_pid, SIGKILL) != 0 && errno != ESRCH) return -1;
    errno = saved;
    return -1;
}

static int set_status_from_wait(runner_result_t *result, int wait_status,
                                int exec_error, int oom_killed) {
    if (WIFSIGNALED(wait_status)) {
        result->term_signal = WTERMSIG(wait_status);
    }
    if (exec_error != 0) {
        result->status = STATUS_SANDBOX_ERROR;
        return 0;
    }
    if (oom_killed) {
        result->status = STATUS_MEMORY_LIMIT;
        return 0;
    }
    if (WIFSIGNALED(wait_status)) {
        if (result->term_signal == SIGSYS) result->status = STATUS_BLOCKED_BY_POLICY;
        else if (result->term_signal == SIGXCPU) result->status = STATUS_TIMEOUT;
        else result->status = STATUS_RUNTIME_ERROR;
        return 0;
    }
    if (WIFEXITED(wait_status)) {
        result->exit_code = WEXITSTATUS(wait_status);
        result->status = result->exit_code == 0 ? STATUS_OK : STATUS_RUNTIME_ERROR;
        return 0;
    }
    result->status = STATUS_SANDBOX_ERROR;
    return 0;
}

void runner_result_destroy(runner_result_t *result) {
    if (!result) return;
    free(result->stdout_buf);
    free(result->stderr_buf);
    memset(result, 0, sizeof(*result));
}

int runner_execute(const runner_config_t *config, runner_result_t *result) {
    if (!config || !result || !config->target_bin || !config->target_argv ||
        !config->target_argv[0] || !valid_arguments(config->target_argv) ||
        !config->workspace_dir || !config->cgroup_root ||
        strnlen(config->target_bin, PATH_MAX) >= PATH_MAX ||
        strnlen(config->workspace_dir, PATH_MAX) >= PATH_MAX ||
        strnlen(config->cgroup_root, PATH_MAX) >= PATH_MAX ||
        config->wall_timeout_ms == 0 || config->wall_timeout_ms > RUNNER_MAX_TIMEOUT_MS ||
        config->cpu_timeout_ms == 0 || config->cpu_timeout_ms > RUNNER_MAX_TIMEOUT_MS ||
        config->memory_limit_kb < RUNNER_MIN_MEMORY_KB ||
        config->memory_limit_kb > RUNNER_MAX_MEMORY_KB ||
        config->max_output_bytes == 0 || config->max_output_bytes > RUNNER_OUTPUT_CAP_BYTES ||
        config->max_pids == 0 || config->max_pids > RUNNER_MAX_PIDS ||
        !target_is_allowed(config->target_bin)) {
        errno = EINVAL;
        return -1;
    }
    memset(result, 0, sizeof(*result));
    result->status = STATUS_SANDBOX_ERROR;
    char cgroup_name[sizeof(config->cgroup_name)];
    if (config->cgroup_name[0] != '\0') {
        memcpy(cgroup_name, config->cgroup_name, sizeof(cgroup_name));
        cgroup_name[sizeof(cgroup_name) - 1] = '\0';
    } else {
        int name_len = snprintf(cgroup_name, sizeof(cgroup_name), "pes2-%ld-%llu",
                                (long)getpid(), (unsigned long long)monotonic_ms());
        if (name_len < 0 || (size_t)name_len >= sizeof(cgroup_name)) {
            runner_result_destroy(result);
            errno = ENAMETOOLONG;
            return -1;
        }
    }
    result->stdout_buf = calloc(config->max_output_bytes, 1);
    result->stderr_buf = calloc(config->max_output_bytes, 1);
    if (!result->stdout_buf || !result->stderr_buf) { runner_result_destroy(result); return -1; }

    char workspace_real[PATH_MAX];
    if (!realpath(config->workspace_dir, workspace_real)) { runner_result_destroy(result); return -1; }
    struct stat workspace_stat;
    if (stat(workspace_real, &workspace_stat) != 0 || !S_ISDIR(workspace_stat.st_mode) ||
        workspace_stat.st_uid != geteuid() || (workspace_stat.st_mode & 0077) != 0) {
        errno = EPERM;
        runner_result_destroy(result);
        return -1;
    }

    if (strncmp(config->target_bin, "/workspace/", 11) == 0) {
        char host_target[PATH_MAX];
        int n = snprintf(host_target, sizeof(host_target), "%s/%s", workspace_real,
                         config->target_bin + 11);
        if (n < 0 || (size_t)n >= sizeof(host_target) || access(host_target, X_OK) != 0) {
            runner_result_destroy(result);
            return -1;
        }
    } else if (access(config->target_bin, X_OK) != 0) {
        runner_result_destroy(result);
        return -1;
    }

    char rootfs[] = ROOTFS_PREFIX;
    if (!mkdtemp(rootfs) || create_rootfs(rootfs) != 0) {
        int saved = errno;
        (void)remove_tree(rootfs);
        runner_result_destroy(result);
        errno = saved;
        return -1;
    }

    if (cgroup_init(config->cgroup_root, cgroup_name, config->max_pids,
                    config->memory_limit_kb) != 0) {
        fprintf(stderr, "runner: cgroup setup failed at %s: %s\n", config->cgroup_root, strerror(errno));
        (void)remove_tree(rootfs);
        runner_result_destroy(result);
        result->status = STATUS_SANDBOX_ERROR;
        return 0;
    }

    if (cancel_requested) {
        (void)cgroup_cleanup(config->cgroup_root, cgroup_name);
        (void)remove_tree(rootfs);
        runner_result_destroy(result);
        result->status = STATUS_CANCELLED;
        return 0;
    }

    uint64_t oom_before = 0;
    if (cgroup_oom_kill_count(config->cgroup_root, cgroup_name, &oom_before) != 0) oom_before = 0;

    int out_pipe[2] = {-1, -1}, err_pipe[2] = {-1, -1};
    int handshake[2] = {-1, -1}, exec_error[2] = {-1, -1};
    void *stack = malloc(CHILD_STACK_BYTES);
    if (!stack || make_pipe(out_pipe) != 0 || make_pipe(err_pipe) != 0 ||
        make_pipe(handshake) != 0 || make_pipe(exec_error) != 0) {
        int saved = errno;
        for (size_t i = 0; i < 2; ++i) {
            if (out_pipe[i] >= 0) close(out_pipe[i]);
            if (err_pipe[i] >= 0) close(err_pipe[i]);
            if (handshake[i] >= 0) close(handshake[i]);
            if (exec_error[i] >= 0) close(exec_error[i]);
        }
        free(stack); (void)cgroup_cleanup(config->cgroup_root, cgroup_name);
        (void)remove_tree(rootfs); runner_result_destroy(result); errno = saved; return -1;
    }

    struct child_context child_ctx = {
        .config = config, .workspace_real = workspace_real, .rootfs = rootfs,
        .stdout_write = out_pipe[1], .stderr_write = err_pipe[1],
        .handshake_read = handshake[0], .exec_error_write = exec_error[1]
    };
    int ns_flags = CLONE_NEWUSER | CLONE_NEWNS | CLONE_NEWPID | CLONE_NEWNET | CLONE_NEWIPC | SIGCHLD;
    pid_t child_pid = clone(child_main, (char *)stack + CHILD_STACK_BYTES, ns_flags, &child_ctx);
    if (child_pid < 0) {
        int saved = errno;
        fprintf(stderr, "runner: namespace clone failed: %s\n", strerror(saved));
        close(out_pipe[0]); close(out_pipe[1]); close(err_pipe[0]); close(err_pipe[1]);
        close(handshake[0]); close(handshake[1]); close(exec_error[0]); close(exec_error[1]);
        free(stack); (void)cgroup_cleanup(config->cgroup_root, cgroup_name);
        (void)remove_tree(rootfs); runner_result_destroy(result); errno = saved;
        result->status = STATUS_SANDBOX_ERROR;
        return 0;
    }

    close(out_pipe[1]); close(err_pipe[1]); close(handshake[0]); close(exec_error[1]);
    if (set_fd_nonblock(out_pipe[0]) != 0 || set_fd_nonblock(err_pipe[0]) != 0 ||
        set_fd_nonblock(exec_error[0]) != 0 ||
        map_child_ids(child_pid, getuid(), getgid()) != 0 ||
        cgroup_attach(config->cgroup_root, cgroup_name, child_pid) != 0) {
        int saved = errno;
        (void)kill(child_pid, SIGKILL);
        (void)waitpid(child_pid, NULL, 0);
        close(out_pipe[0]); close(err_pipe[0]); close(handshake[1]); close(exec_error[0]);
        free(stack); (void)cgroup_cleanup(config->cgroup_root, cgroup_name);
        (void)remove_tree(rootfs); runner_result_destroy(result); errno = saved;
        result->status = STATUS_SANDBOX_ERROR;
        fprintf(stderr, "runner: child namespace mapping or cgroup attachment failed: %s\n", strerror(saved));
        return 0;
    }

    char release = 'G';
    if (write_all(handshake[1], &release, 1) != 0) {
        int saved = errno;
        (void)kill_sandbox(config->cgroup_root, cgroup_name, child_pid);
        (void)waitpid(child_pid, NULL, 0);
        close(out_pipe[0]); close(err_pipe[0]); close(handshake[1]); close(exec_error[0]);
        free(stack); (void)cgroup_cleanup(config->cgroup_root, cgroup_name);
        (void)remove_tree(rootfs); runner_result_destroy(result); errno = saved;
        result->status = STATUS_SANDBOX_ERROR;
        return 0;
    }
    close(handshake[1]);

    int out_eof = 0, err_eof = 0, child_reaped = 0, forced = 0;
    int wait_status = 0, exec_errno = 0, exec_errno_read = 0, overflow = 0;
    struct rusage usage;
    memset(&usage, 0, sizeof(usage));
    uint64_t start_ms = monotonic_ms();
    int descendants_killed = 0;

    while (!child_reaped || !out_eof || !err_eof) {
        struct pollfd fds[2] = {
            {.fd = out_eof ? -1 : out_pipe[0], .events = POLLIN | POLLHUP},
            {.fd = err_eof ? -1 : err_pipe[0], .events = POLLIN | POLLHUP}
        };
        uint64_t now_ms = monotonic_ms();
        uint64_t elapsed = now_ms >= start_ms ? now_ms - start_ms : 0;
        int wait_ms = POLL_INTERVAL_MS;
        if (cancel_requested && !forced) {
            forced = STATUS_CANCELLED;
            if (kill_sandbox(config->cgroup_root, cgroup_name, child_pid) != 0) forced = STATUS_SANDBOX_ERROR;
        } else if (elapsed >= config->wall_timeout_ms && !forced) {
            forced = STATUS_TIMEOUT;
            if (kill_sandbox(config->cgroup_root, cgroup_name, child_pid) != 0) forced = STATUS_SANDBOX_ERROR;
        } else if (!forced && config->wall_timeout_ms - elapsed < (uint64_t)wait_ms) {
            wait_ms = (int)(config->wall_timeout_ms - elapsed);
        }

        uint64_t cpu_ms = 0;
        if (!forced && cgroup_cpu_usage_ms(config->cgroup_root, cgroup_name, &cpu_ms) != 0) {
            forced = STATUS_SANDBOX_ERROR;
            (void)kill_sandbox(config->cgroup_root, cgroup_name, child_pid);
        } else if (!forced && cpu_ms > config->cpu_timeout_ms) {
            forced = STATUS_TIMEOUT;
            if (kill_sandbox(config->cgroup_root, cgroup_name, child_pid) != 0) forced = STATUS_SANDBOX_ERROR;
        }

        int poll_rc;
        do { poll_rc = poll(fds, 2, wait_ms); } while (poll_rc < 0 && errno == EINTR);
        if (poll_rc < 0) {
            forced = STATUS_SANDBOX_ERROR;
            (void)kill_sandbox(config->cgroup_root, cgroup_name, child_pid);
        }
        if (!out_eof && fds[0].revents != 0 &&
            drain_output(out_pipe[0], result->stdout_buf, config->max_output_bytes,
                         &result->stdout_len, &out_eof, &overflow) != 0) {
            forced = STATUS_SANDBOX_ERROR;
            (void)kill_sandbox(config->cgroup_root, cgroup_name, child_pid);
        }
        if (!err_eof && fds[1].revents != 0 &&
            drain_output(err_pipe[0], result->stderr_buf, config->max_output_bytes,
                         &result->stderr_len, &err_eof, &overflow) != 0) {
            forced = STATUS_SANDBOX_ERROR;
            (void)kill_sandbox(config->cgroup_root, cgroup_name, child_pid);
        }
        if (overflow && !forced) {
            forced = STATUS_OUTPUT_LIMIT;
            if (kill_sandbox(config->cgroup_root, cgroup_name, child_pid) != 0) forced = STATUS_SANDBOX_ERROR;
        }

        if (!exec_errno_read) {
            int child_errno;
            ssize_t n = read(exec_error[0], &child_errno, sizeof(child_errno));
            if (n == (ssize_t)sizeof(child_errno)) { exec_errno = child_errno; exec_errno_read = 1; }
            else if (n == 0) exec_errno_read = 1;
            else if (n < 0 && errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR) {
                exec_errno = errno; exec_errno_read = 1;
            }
        }

        if (!child_reaped) {
            pid_t waited = wait4(child_pid, &wait_status, WNOHANG, &usage);
            if (waited == child_pid) {
                child_reaped = 1;
                if (!descendants_killed) {
                    if (cgroup_kill(config->cgroup_root, cgroup_name) != 0 && !forced)
                        forced = STATUS_SANDBOX_ERROR;
                    descendants_killed = 1;
                }
            } else if (waited < 0 && errno != EINTR) {
                forced = STATUS_SANDBOX_ERROR;
                (void)kill_sandbox(config->cgroup_root, cgroup_name, child_pid);
            }
        }
        if (child_reaped && out_eof && err_eof) break;
    }

    result->wall_time_ms = monotonic_ms() - start_ms;
    result->cpu_time_ms = (uint64_t)usage.ru_utime.tv_sec * 1000U +
                          (uint64_t)usage.ru_utime.tv_usec / 1000U +
                          (uint64_t)usage.ru_stime.tv_sec * 1000U +
                          (uint64_t)usage.ru_stime.tv_usec / 1000U;
    uint64_t cgroup_cpu_ms = 0;
    if (cgroup_cpu_usage_ms(config->cgroup_root, cgroup_name, &cgroup_cpu_ms) == 0)
        result->cpu_time_ms = cgroup_cpu_ms;
    (void)cgroup_peak_memory_kb(config->cgroup_root, cgroup_name, &result->peak_memory_kb);
    if (result->peak_memory_kb == 0 && usage.ru_maxrss > 0)
        result->peak_memory_kb = (uint64_t)usage.ru_maxrss;
    uint64_t oom_after = oom_before;
    int oom_killed = cgroup_oom_kill_count(config->cgroup_root, cgroup_name, &oom_after) == 0 && oom_after > oom_before;
    if (forced == 0) set_status_from_wait(result, wait_status, exec_errno, oom_killed);
    else result->status = (runner_status_t)forced;

    if (close(out_pipe[0]) != 0 && result->status == STATUS_OK) result->status = STATUS_SANDBOX_ERROR;
    if (close(err_pipe[0]) != 0 && result->status == STATUS_OK) result->status = STATUS_SANDBOX_ERROR;
    close(exec_error[0]);
    free(stack);
    if (cgroup_cleanup(config->cgroup_root, cgroup_name) != 0) {
        fprintf(stderr, "runner: cgroup cleanup failed for %s: %s\n", cgroup_name, strerror(errno));
        result->status = STATUS_SANDBOX_ERROR;
    }
    if (remove_tree(rootfs) != 0) {
        fprintf(stderr, "runner: rootfs cleanup failed at %s: %s\n", rootfs, strerror(errno));
        result->status = STATUS_SANDBOX_ERROR;
    }
    return 0;
}

static int parse_u32(const char *text, uint32_t min, uint32_t max, uint32_t *out) {
    if (!text || !*text) return -1;
    char *end = NULL;
    errno = 0;
    unsigned long value = strtoul(text, &end, 10);
    if (errno != 0 || !end || *end != '\0' || value < min || value > max) return -1;
    *out = (uint32_t)value;
    return 0;
}

static const char *status_name(runner_status_t status) {
    switch (status) {
        case STATUS_OK: return "OK";
        case STATUS_RUNTIME_ERROR: return "RUNTIME_ERROR";
        case STATUS_TIMEOUT: return "TIMEOUT";
        case STATUS_OUTPUT_LIMIT: return "OUTPUT_LIMIT";
        case STATUS_MEMORY_LIMIT: return "MEMORY_LIMIT";
        case STATUS_BLOCKED_BY_POLICY: return "BLOCKED_BY_POLICY";
        case STATUS_SANDBOX_ERROR: return "SANDBOX_ERROR";
        case STATUS_CANCELLED: return "CANCELLED";
    }
    return "UNKNOWN";
}

int main(int argc, char **argv) {
    struct sigaction action;
    memset(&action, 0, sizeof(action));
    action.sa_handler = request_cancel;
    sigemptyset(&action.sa_mask);
    if (sigaction(SIGINT, &action, NULL) != 0 || sigaction(SIGTERM, &action, NULL) != 0) {
        fprintf(stderr, "runner: cannot install cancellation handlers: %s\n", strerror(errno));
        return STATUS_SANDBOX_ERROR;
    }
    runner_config_t config = {
        .cpu_timeout_ms = 1000,
        .wall_timeout_ms = 2000,
        .memory_limit_kb = 65536,
        .max_output_bytes = RUNNER_OUTPUT_CAP_BYTES,
        .max_pids = 32,
        .cgroup_root = getenv("PES2_CGROUP_ROOT") ? getenv("PES2_CGROUP_ROOT") : "/sys/fs/cgroup"
    };
    int i = 1;
    while (i < argc && strcmp(argv[i], "--") != 0) {
        if (strcmp(argv[i], "--workspace") == 0 && i + 1 < argc) config.workspace_dir = argv[++i];
        else if (strcmp(argv[i], "--cgroup-root") == 0 && i + 1 < argc) config.cgroup_root = argv[++i];
        else if (strcmp(argv[i], "--wall-ms") == 0 && i + 1 < argc) {
            if (parse_u32(argv[++i], 1, RUNNER_MAX_TIMEOUT_MS, &config.wall_timeout_ms) != 0) goto usage;
        } else if (strcmp(argv[i], "--cpu-ms") == 0 && i + 1 < argc) {
            if (parse_u32(argv[++i], 1, RUNNER_MAX_TIMEOUT_MS, &config.cpu_timeout_ms) != 0) goto usage;
        } else if (strcmp(argv[i], "--memory-kb") == 0 && i + 1 < argc) {
            if (parse_u32(argv[++i], RUNNER_MIN_MEMORY_KB, RUNNER_MAX_MEMORY_KB, &config.memory_limit_kb) != 0) goto usage;
        } else if (strcmp(argv[i], "--pids") == 0 && i + 1 < argc) {
            if (parse_u32(argv[++i], 1, RUNNER_MAX_PIDS, &config.max_pids) != 0) goto usage;
        } else {
            goto usage;
        }
        ++i;
    }
    if (i >= argc || strcmp(argv[i], "--") != 0 || i + 1 >= argc || !config.workspace_dir) goto usage;
    config.target_bin = argv[i + 1];
    config.target_argv = &argv[i + 1];

    runner_result_t result;
    if (runner_execute(&config, &result) != 0) {
        fprintf(stderr, "runner: invalid request or supervisor failure: %s\n", strerror(errno));
        return STATUS_SANDBOX_ERROR;
    }
    if (result.stdout_len > 0) (void)fwrite(result.stdout_buf, 1, result.stdout_len, stdout);
    if (result.stderr_len > 0) (void)fwrite(result.stderr_buf, 1, result.stderr_len, stderr);
    fprintf(stderr, "PES2_RESULT status=%s exit=%d signal=%d wall_ms=%llu cpu_ms=%llu peak_memory_kb=%llu stdout_bytes=%u stderr_bytes=%u\n",
            status_name(result.status), result.exit_code, result.term_signal,
            (unsigned long long)result.wall_time_ms, (unsigned long long)result.cpu_time_ms,
            (unsigned long long)result.peak_memory_kb, result.stdout_len, result.stderr_len);
    runner_status_t status = result.status;
    runner_result_destroy(&result);
    return status;

usage:
    fprintf(stderr, "Usage: %s [--wall-ms N] [--cpu-ms N] [--memory-kb N] [--pids N] [--cgroup-root PATH] --workspace PRIVATE_DIR -- ABSOLUTE_TARGET [ARGS...]\n", argv[0]);
    return STATUS_SANDBOX_ERROR;
}
