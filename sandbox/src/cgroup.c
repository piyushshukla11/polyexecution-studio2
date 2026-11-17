#define _GNU_SOURCE
#include "runner.h"

#include <errno.h>
#include <fcntl.h>
#include <linux/magic.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/statfs.h>
#include <time.h>
#include <unistd.h>

static int valid_name(const char *name) {
    if (!name || !*name || strlen(name) >= 64) return 0;
    for (const unsigned char *p = (const unsigned char *)name; *p; ++p) {
        if (!( (*p >= 'a' && *p <= 'z') || (*p >= 'A' && *p <= 'Z') ||
               (*p >= '0' && *p <= '9') || *p == '-' || *p == '_' )) return 0;
    }
    return 1;
}

static int make_path(char *out, size_t cap, const char *root,
                     const char *name, const char *leaf) {
    if (!root || root[0] != '/' || !valid_name(name)) {
        errno = EINVAL;
        return -1;
    }
    int n = snprintf(out, cap, "%s/%s/%s", root, name, leaf);
    if (n < 0 || (size_t)n >= cap) {
        errno = ENAMETOOLONG;
        return -1;
    }
    return 0;
}

static int write_value(const char *path, const char *value) {
    int fd = open(path, O_WRONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) return -1;
    size_t len = strlen(value);
    ssize_t written;
    do {
        written = write(fd, value, len);
    } while (written < 0 && errno == EINTR);
    int saved = errno;
    if (close(fd) < 0 && written == (ssize_t)len) return -1;
    errno = saved;
    return written == (ssize_t)len ? 0 : -1;
}

static int contains_token(const char *path, const char *needle) {
    FILE *f = fopen(path, "re");
    if (!f) return -1;
    char token[64];
    int found = 0;
    while (fscanf(f, "%63s", token) == 1) {
        if (strcmp(token, needle) == 0) found = 1;
    }
    int saved = errno;
    if (fclose(f) != 0) return -1;
    errno = saved;
    return found;
}

static int read_key_u64(const char *path, const char *key, uint64_t *out) {
    FILE *f = fopen(path, "re");
    if (!f) return -1;
    char label[64];
    unsigned long long value;
    int result = -1;
    while (fscanf(f, "%63s %llu", label, &value) == 2) {
        if (strcmp(label, key) == 0) {
            *out = (uint64_t)value;
            result = 0;
            break;
        }
    }
    int saved = errno;
    if (fclose(f) != 0 && result == 0) return -1;
    errno = saved;
    if (result != 0) errno = ENOENT;
    return result;
}

static int cgroup_dir(char *out, size_t cap, const char *root, const char *name) {
    if (!root || root[0] != '/' || !valid_name(name)) {
        errno = EINVAL;
        return -1;
    }
    int n = snprintf(out, cap, "%s/%s", root, name);
    if (n < 0 || (size_t)n >= cap) {
        errno = ENAMETOOLONG;
        return -1;
    }
    return 0;
}

int cgroup_init(const char *root, const char *name, uint32_t max_pids,
                uint32_t memory_limit_kb) {
    if (!root || root[0] != '/' || !valid_name(name) || max_pids == 0 ||
        memory_limit_kb == 0) {
        errno = EINVAL;
        return -1;
    }

    struct statfs fs;
    if (statfs(root, &fs) != 0) return -1;
    if ((unsigned long)fs.f_type != CGROUP2_SUPER_MAGIC) {
        errno = ENODEV;
        return -1;
    }

    char file[512];
    const char *required[] = {"cpu", "memory", "pids"};
    for (size_t i = 0; i < sizeof(required) / sizeof(required[0]); ++i) {
        if (snprintf(file, sizeof(file), "%s/cgroup.subtree_control", root) >= (int)sizeof(file)) {
            errno = ENAMETOOLONG;
            return -1;
        }
        int enabled = contains_token(file, required[i]);
        if (enabled != 1) {
            if (enabled == 0) errno = ENOENT;
            return -1;
        }
    }

    char dir[512];
    if (cgroup_dir(dir, sizeof(dir), root, name) != 0) return -1;
    if (mkdir(dir, 0700) != 0) return -1;

    char value[64];
    int failed = 0;
    if (make_path(file, sizeof(file), root, name, "pids.max") != 0) failed = 1;
    else {
        snprintf(value, sizeof(value), "%u", max_pids);
        if (write_value(file, value) != 0) failed = 1;
    }
    if (!failed && make_path(file, sizeof(file), root, name, "memory.max") != 0) failed = 1;
    else if (!failed) {
        snprintf(value, sizeof(value), "%llu", (unsigned long long)memory_limit_kb * 1024ULL);
        if (write_value(file, value) != 0) failed = 1;
    }
    if (!failed && make_path(file, sizeof(file), root, name, "memory.oom.group") != 0) failed = 1;
    else if (!failed && write_value(file, "1") != 0) failed = 1;
    if (!failed && make_path(file, sizeof(file), root, name, "cpu.max") != 0) failed = 1;
    else if (!failed) {
        /* Cap concurrent CPU rate at one core; cumulative CPU time is checked separately. */
        snprintf(value, sizeof(value), "100000 100000");
        if (write_value(file, value) != 0) failed = 1;
    }
    if (!failed && make_path(file, sizeof(file), root, name, "cgroup.kill") != 0) failed = 1;
    else if (!failed && access(file, W_OK) != 0) failed = 1;

    if (failed) {
        int saved = errno;
        (void)rmdir(dir);
        errno = saved;
        return -1;
    }
    return 0;
}

int cgroup_attach(const char *root, const char *name, pid_t pid) {
    char path[512];
    char value[32];
    if (pid <= 0 || make_path(path, sizeof(path), root, name, "cgroup.procs") != 0) {
        errno = EINVAL;
        return -1;
    }
    snprintf(value, sizeof(value), "%ld", (long)pid);
    return write_value(path, value);
}

int cgroup_kill(const char *root, const char *name) {
    char path[512];
    if (make_path(path, sizeof(path), root, name, "cgroup.kill") != 0) return -1;
    return write_value(path, "1");
}

int cgroup_cleanup(const char *root, const char *name) {
    char dir[512], path[512];
    if (cgroup_dir(dir, sizeof(dir), root, name) != 0) return -1;
    if (cgroup_kill(root, name) != 0) return -1;
    if (make_path(path, sizeof(path), root, name, "cgroup.events") != 0) return -1;

    for (unsigned int attempt = 0; attempt < 100; ++attempt) {
        FILE *f = fopen(path, "re");
        if (!f) return -1;
        char key[32];
        int populated = 1;
        while (fscanf(f, "%31s %d", key, &populated) == 2) {
            if (strcmp(key, "populated") == 0) break;
        }
        int saved = errno;
        if (fclose(f) != 0) return -1;
        errno = saved;
        if (populated == 0) return rmdir(dir);
        struct timespec delay = {.tv_sec = 0, .tv_nsec = 10000000L};
        while (nanosleep(&delay, &delay) != 0 && errno == EINTR) {}
    }
    errno = EBUSY;
    return -1;
}

int cgroup_peak_memory_kb(const char *root, const char *name, uint64_t *peak_kb) {
    char path[512];
    uint64_t bytes;
    if (!peak_kb || make_path(path, sizeof(path), root, name, "memory.peak") != 0) return -1;
    FILE *f = fopen(path, "re");
    if (!f) return -1;
    unsigned long long value;
    int ok = fscanf(f, "%llu", &value) == 1;
    int saved = errno;
    if (fclose(f) != 0 || !ok) { errno = saved ? saved : EIO; return -1; }
    bytes = (uint64_t)value;
    *peak_kb = bytes / 1024U;
    return 0;
}

int cgroup_oom_kill_count(const char *root, const char *name, uint64_t *count) {
    char path[512];
    if (!count || make_path(path, sizeof(path), root, name, "memory.events") != 0) return -1;
    return read_key_u64(path, "oom_kill", count);
}

int cgroup_cpu_usage_ms(const char *root, const char *name, uint64_t *usage_ms) {
    char path[512];
    uint64_t usec;
    if (!usage_ms || make_path(path, sizeof(path), root, name, "cpu.stat") != 0 ||
        read_key_u64(path, "usage_usec", &usec) != 0) return -1;
    *usage_ms = usec / 1000U;
    return 0;
}
