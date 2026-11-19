#define _GNU_SOURCE
#include "runner.h"

#include <errno.h>
#include <linux/sched.h>
#include <seccomp.h>
#include <sys/prctl.h>

/*
 * Validated, explicit seccomp-bpf syscall filtering profiles.
 * Preserves default-deny architecture, namespace blocking, network isolation,
 * and PR_SET_NO_NEW_PRIVS privilege containment across all profiles.
 */
int seccomp_apply_profile(seccomp_profile_t profile) {
    if (profile != SECCOMP_PROFILE_STRICT &&
        profile != SECCOMP_PROFILE_COMPILER &&
        profile != SECCOMP_PROFILE_PYTHON &&
        profile != SECCOMP_PROFILE_BASH &&
        profile != SECCOMP_PROFILE_JVM) {
        errno = EINVAL;
        return -1;
    }

    scmp_filter_ctx ctx = seccomp_init(SCMP_ACT_KILL_PROCESS);
    if (ctx == NULL) return -1;

    const int base_allowed[] = {
        SCMP_SYS(read), SCMP_SYS(write), SCMP_SYS(readv), SCMP_SYS(writev),
        SCMP_SYS(close), SCMP_SYS(fstat), SCMP_SYS(newfstatat), SCMP_SYS(statx),
        SCMP_SYS(lseek), SCMP_SYS(pread64), SCMP_SYS(pwrite64),
        SCMP_SYS(mmap), SCMP_SYS(mprotect), SCMP_SYS(munmap), SCMP_SYS(mremap),
        SCMP_SYS(brk), SCMP_SYS(madvise), SCMP_SYS(futex), SCMP_SYS(futex_waitv),
        SCMP_SYS(rt_sigaction), SCMP_SYS(rt_sigprocmask), SCMP_SYS(rt_sigreturn),
        SCMP_SYS(sigaltstack), SCMP_SYS(exit), SCMP_SYS(exit_group),
        SCMP_SYS(execve), SCMP_SYS(arch_prctl), SCMP_SYS(set_tid_address),
        SCMP_SYS(set_robust_list), SCMP_SYS(rseq), SCMP_SYS(getpid),
        SCMP_SYS(getppid), SCMP_SYS(gettid), SCMP_SYS(getuid), SCMP_SYS(geteuid),
        SCMP_SYS(getgid), SCMP_SYS(getegid), SCMP_SYS(getcwd), SCMP_SYS(readlink),
        SCMP_SYS(readlinkat), SCMP_SYS(openat), SCMP_SYS(access),
        SCMP_SYS(faccessat), SCMP_SYS(faccessat2), SCMP_SYS(fcntl),
        SCMP_SYS(ioctl), SCMP_SYS(dup), SCMP_SYS(dup2), SCMP_SYS(dup3),
        SCMP_SYS(pipe2), SCMP_SYS(clock_gettime), SCMP_SYS(clock_getres),
        SCMP_SYS(gettimeofday), SCMP_SYS(nanosleep), SCMP_SYS(clock_nanosleep),
        SCMP_SYS(sched_yield), SCMP_SYS(prlimit64), SCMP_SYS(getrlimit),
        SCMP_SYS(getrandom), SCMP_SYS(uname), SCMP_SYS(sysinfo),
        SCMP_SYS(getdents64), SCMP_SYS(fstatfs), SCMP_SYS(statfs),
        SCMP_SYS(membarrier), SCMP_SYS(fsync), SCMP_SYS(fdatasync),
        SCMP_SYS(umask)
    };

    for (size_t i = 0; i < sizeof(base_allowed) / sizeof(base_allowed[0]); ++i) {
        if (seccomp_rule_add(ctx, SCMP_ACT_ALLOW, base_allowed[i], 0) < 0) {
            seccomp_release(ctx);
            errno = EINVAL;
            return -1;
        }
    }

    if (profile == SECCOMP_PROFILE_COMPILER) {
        /*
         * Narrowly scoped permissions strictly required by build tools (gcc, clang++):
         * - wait4 / waitid: for reaping child compiler processes (cc1, as, ld, collect2)
         * - pipe / unlink / rename / chmod / mkdir: intermediate file management in /tmp and /workspace
         * - sched_getaffinity: CPU topology awareness
         * - getrusage: process resource accounting used by assembler (GNU as)
         */
        const int compiler_allowed[] = {
            SCMP_SYS(wait4), SCMP_SYS(waitid),
            SCMP_SYS(pipe), SCMP_SYS(unlink), SCMP_SYS(unlinkat),
            SCMP_SYS(rename), SCMP_SYS(renameat), SCMP_SYS(renameat2),
            SCMP_SYS(chmod), SCMP_SYS(fchmod), SCMP_SYS(fchmodat),
            SCMP_SYS(mkdir), SCMP_SYS(mkdirat),
            SCMP_SYS(sched_getaffinity),
            SCMP_SYS(getrusage)
        };
        for (size_t i = 0; i < sizeof(compiler_allowed) / sizeof(compiler_allowed[0]); ++i) {
            if (seccomp_rule_add(ctx, SCMP_ACT_ALLOW, compiler_allowed[i], 0) < 0) {
                seccomp_release(ctx);
                errno = EINVAL;
                return -1;
            }
        }
    } else if (profile == SECCOMP_PROFILE_PYTHON) {
        /*
         * Interpreted scripting permissions:
         * - sched_getaffinity: os.cpu_count() multi-core affinity queries
         * - open: direct open syscall invoked during Python 3 runtime initialization
         */
        const int python_allowed[] = {
            SCMP_SYS(sched_getaffinity),
            SCMP_SYS(open)
        };
        for (size_t i = 0; i < sizeof(python_allowed) / sizeof(python_allowed[0]); ++i) {
            if (seccomp_rule_add(ctx, SCMP_ACT_ALLOW, python_allowed[i], 0) < 0) {
                seccomp_release(ctx);
                errno = EINVAL;
                return -1;
            }
        }
    } else if (profile == SECCOMP_PROFILE_BASH) {
        /*
         * Shell runtime & syntax check permissions:
         * - getresuid / getresgid: user/group credential inspection on startup
         * - getpgrp: process group inspection during shell initialization
         * - wait4: reaping subshell pipelines and child processes
         */
        const int bash_allowed[] = {
            SCMP_SYS(getresuid),
            SCMP_SYS(getresgid),
            SCMP_SYS(getpgrp),
            SCMP_SYS(wait4)
        };
        for (size_t i = 0; i < sizeof(bash_allowed) / sizeof(bash_allowed[0]); ++i) {
            if (seccomp_rule_add(ctx, SCMP_ACT_ALLOW, bash_allowed[i], 0) < 0) {
                seccomp_release(ctx);
                errno = EINVAL;
                return -1;
            }
        }
    } else if (profile == SECCOMP_PROFILE_JVM) {
        /*
         * JVM execution & javac compilation permissions:
         * - fchdir: directory/jar traversal using directory file descriptors
         * - ftruncate: classfile generation and buffer sizing
         * - flock: file-locking coordination on jar/class caches
         * - prctl(PR_SET_NAME): thread naming for HotSpot internal threads
         * - sched_getaffinity: CPU topology awareness for GC and JIT compiler thread sizing
         * - getrusage: process resource accounting
         * - restart_syscall: signal interruption restarts
         * - wait4 / waitid: child process and launcher process reaping
         * - unlink / unlinkat: classfile / cache management
         * - mkdir / mkdirat: directory creation for packaged classes
         * - rename / renameat / renameat2: atomic classfile writes
         */
        const int jvm_allowed[] = {
            SCMP_SYS(fchdir),
            SCMP_SYS(ftruncate),
            SCMP_SYS(flock),
            SCMP_SYS(sched_getaffinity),
            SCMP_SYS(getrusage),
            SCMP_SYS(restart_syscall),
            SCMP_SYS(wait4),
            SCMP_SYS(waitid),
            SCMP_SYS(unlink),
            SCMP_SYS(unlinkat),
            SCMP_SYS(mkdir),
            SCMP_SYS(mkdirat),
            SCMP_SYS(rename),
            SCMP_SYS(renameat),
            SCMP_SYS(renameat2)
        };
        for (size_t i = 0; i < sizeof(jvm_allowed) / sizeof(jvm_allowed[0]); ++i) {
            if (seccomp_rule_add(ctx, SCMP_ACT_ALLOW, jvm_allowed[i], 0) < 0) {
                seccomp_release(ctx);
                errno = EINVAL;
                return -1;
            }
        }

        /*
         * Restrict prctl strictly to PR_SET_NAME (used by HotSpot VM for naming
         * GC and compiler threads). Any other option (including PR_SET_DUMPABLE)
         * is denied by default and terminates the process with SIGSYS.
         */
        if (seccomp_rule_add(ctx, SCMP_ACT_ALLOW, SCMP_SYS(prctl), 1,
                             SCMP_A0(SCMP_CMP_EQ, PR_SET_NAME)) < 0) {
            seccomp_release(ctx);
            errno = EINVAL;
            return -1;
        }

        /*
         * JVM network denial: return EPERM for socket and setsockopt so that
         * JVM internal dual-stack IPv6 probes during JNI_OnLoad (libnet.so)
         * fail cleanly with permission denied without crashing the runtime.
         * All network operations (connect, bind, listen, accept) remain
         * default-denied and kill the process with SIGSYS.
         */
        if (seccomp_rule_add(ctx, SCMP_ACT_ERRNO(EPERM), SCMP_SYS(socket), 0) < 0 ||
            seccomp_rule_add(ctx, SCMP_ACT_ERRNO(EPERM), SCMP_SYS(setsockopt), 0) < 0) {
            seccomp_release(ctx);
            errno = EINVAL;
            return -1;
        }
    }

    /* Permit fork/thread creation only without creating or joining namespaces. */
    const uint64_t namespace_flags = (uint64_t)(CLONE_NEWCGROUP | CLONE_NEWIPC |
        CLONE_NEWNET | CLONE_NEWNS | CLONE_NEWPID | CLONE_NEWUSER | CLONE_NEWUTS);
#ifdef CLONE_NEWTIME
    const uint64_t all_namespace_flags = namespace_flags | (uint64_t)CLONE_NEWTIME;
#else
    const uint64_t all_namespace_flags = namespace_flags;
#endif
    if (seccomp_rule_add(ctx, SCMP_ACT_ALLOW, SCMP_SYS(clone), 1,
                         SCMP_A0(SCMP_CMP_MASKED_EQ, all_namespace_flags, 0)) < 0 ||
        seccomp_rule_add(ctx, SCMP_ACT_ALLOW, SCMP_SYS(fork), 0) < 0 ||
        seccomp_rule_add(ctx, SCMP_ACT_ALLOW, SCMP_SYS(vfork), 0) < 0 ||
        seccomp_rule_add(ctx, SCMP_ACT_ERRNO(ENOSYS), SCMP_SYS(clone3), 0) < 0) {
        seccomp_release(ctx);
        errno = EINVAL;
        return -1;
    }

    if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0) {
        seccomp_release(ctx);
        return -1;
    }

    int rc = seccomp_load(ctx);
    int saved = errno;
    seccomp_release(ctx);
    errno = saved;
    return rc;
}

int seccomp_apply_strict_filter(void) {
    return seccomp_apply_profile(SECCOMP_PROFILE_STRICT);
}
