#define _GNU_SOURCE
#include "runner.h"

#include <errno.h>
#include <fcntl.h>
#include <linux/capability.h>
#include <linux/sched.h>
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/epoll.h>
#include <sys/eventfd.h>
#include <sys/file.h>
#include <sys/mman.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

#ifndef SYS_io_uring_setup
#define SYS_io_uring_setup 425
#endif
#ifndef SYS_io_uring_enter
#define SYS_io_uring_enter 426
#endif

#ifndef PR_SET_VMA
#define PR_SET_VMA 0x53564d41
#endif
#ifndef PR_SET_VMA_ANON_NAME
#define PR_SET_VMA_ANON_NAME 0
#endif
#ifndef PR_SET_THP_DISABLE
#define PR_SET_THP_DISABLE 41
#endif

static int expect_sigsys(void (*fn)(void)) {
    pid_t pid = fork();
    if (pid < 0) {
        perror("fork");
        return 0;
    }
    if (pid == 0) {
        fn();
        _exit(0); /* If not killed by SIGSYS, exit 0 */
    }
    int status = 0;
    if (waitpid(pid, &status, 0) < 0) {
        perror("waitpid");
        return 0;
    }
    return WIFSIGNALED(status) && WTERMSIG(status) == SIGSYS;
}

static int expect_success(void (*fn)(void)) {
    pid_t pid = fork();
    if (pid < 0) {
        perror("fork");
        return 0;
    }
    if (pid == 0) {
        fn();
        _exit(0);
    }
    int status = 0;
    if (waitpid(pid, &status, 0) < 0) {
        perror("waitpid");
        return 0;
    }
    return WIFEXITED(status) && WEXITSTATUS(status) == 0;
}

/* Actions to run inside child */
static void action_strict_getrusage(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_STRICT) != 0) _exit(2);
    struct rusage ru;
    getrusage(RUSAGE_SELF, &ru);
}

static void action_compiler_getrusage(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_COMPILER) != 0) _exit(2);
    struct rusage ru;
    if (getrusage(RUSAGE_SELF, &ru) != 0) _exit(3);
}

static void action_strict_fork_permitted(void) {
    /*
     * Prevent zombie/orphan leakage:
     * 1. Setting SIGCHLD disposition to SIG_IGN with SA_NOCLDWAIT instructs
     *    the kernel to automatically reap terminated children without waiting.
     * 2. Synchronizing through a pipe ensures the child finishes before the
     *    parent exits, guaranteeing zero lingering processes.
     */
    struct sigaction sa;
    memset(&sa, 0, sizeof(sa));
    sa.sa_handler = SIG_IGN;
    sa.sa_flags = SA_NOCLDWAIT;
    if (sigaction(SIGCHLD, &sa, NULL) != 0) _exit(2);

    int pfd[2];
    if (pipe2(pfd, O_CLOEXEC) != 0) _exit(2);

    if (seccomp_apply_profile(SECCOMP_PROFILE_STRICT) != 0) _exit(2);

    /*
     * fork() is explicitly permitted under strict seccomp to allow standard
     * thread/process creation. Process quantity is bounded by cgroups v2 pids.max.
     */
    pid_t child = fork();
    if (child < 0) _exit(3);
    if (child == 0) {
        close(pfd[0]);
        char c = '1';
        if (write(pfd[1], &c, 1) != 1) _exit(4);
        close(pfd[1]);
        _exit(0);
    }
    close(pfd[1]);
    char buf = 0;
    if (read(pfd[0], &buf, 1) != 1 || buf != '1') _exit(5);
    close(pfd[0]);
    _exit(0);
}

static void action_strict_wait4(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_STRICT) != 0) _exit(2);
    int status = 0;
    /* wait4 is omitted from strict profile to restrict user binaries from reaping loops */
    wait4(-1, &status, WNOHANG, NULL);
}

static void action_compiler_wait4(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_COMPILER) != 0) _exit(2);
    int status = 0;
    /* Calling wait4 with -1 and WNOHANG returns -1 with ECHILD, but does not get killed by SIGSYS */
    wait4(-1, &status, WNOHANG, NULL);
}

static void action_compiler_network_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_COMPILER) != 0) _exit(2);
    socket(AF_INET, SOCK_STREAM, 0);
}

static void action_strict_network_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_STRICT) != 0) _exit(2);
    socket(AF_INET, SOCK_STREAM, 0);
}

static void action_strict_clone_user_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_STRICT) != 0) _exit(2);
    /* clone with CLONE_NEWUSER: must trigger SIGSYS */
    syscall(SYS_clone, (uintptr_t)CLONE_NEWUSER, 0, 0, 0, 0);
}

static void action_compiler_clone_user_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_COMPILER) != 0) _exit(2);
    /* clone with CLONE_NEWUSER: must trigger SIGSYS */
    syscall(SYS_clone, (uintptr_t)CLONE_NEWUSER, 0, 0, 0, 0);
}

static void action_compiler_unshare_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_COMPILER) != 0) _exit(2);
    unshare(CLONE_NEWUSER);
}

static void action_python_open(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_PYTHON) != 0) _exit(2);
    int fd = syscall(SYS_open, "/dev/null", O_RDONLY);
    if (fd >= 0) close(fd);
}

static void action_python_network_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_PYTHON) != 0) _exit(2);
    socket(AF_INET, SOCK_STREAM, 0);
}

static void action_strict_getresuid(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_STRICT) != 0) _exit(2);
    uid_t r, e, s;
    getresuid(&r, &e, &s);
}

static void action_strict_getresgid(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_STRICT) != 0) _exit(2);
    gid_t r, e, s;
    getresgid(&r, &e, &s);
}

static void action_strict_getpgrp(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_STRICT) != 0) _exit(2);
    getpgrp();
}

static void action_bash_getresuid(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_BASH) != 0) _exit(2);
    uid_t r, e, s;
    if (getresuid(&r, &e, &s) != 0) _exit(3);
}

static void action_bash_getresgid(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_BASH) != 0) _exit(2);
    gid_t r, e, s;
    if (getresgid(&r, &e, &s) != 0) _exit(3);
}

static void action_bash_getpgrp(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_BASH) != 0) _exit(2);
    if (getpgrp() < 0) _exit(3);
}

static void action_bash_wait4(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_BASH) != 0) _exit(2);
    int status = 0;
    wait4(-1, &status, WNOHANG, NULL);
}

static void action_bash_network_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_BASH) != 0) _exit(2);
    socket(AF_INET, SOCK_STREAM, 0);
}

static void action_bash_clone_user_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_BASH) != 0) _exit(2);
    syscall(SYS_clone, (uintptr_t)CLONE_NEWUSER, 0, 0, 0, 0);
}

static void action_bash_unshare_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_BASH) != 0) _exit(2);
    unshare(CLONE_NEWUSER);
}

static void action_strict_fchdir(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_STRICT) != 0) _exit(2);
    fchdir(-1);
}

static void action_jvm_fchdir(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_JVM) != 0) _exit(2);
    fchdir(-1);
}

static void action_strict_ftruncate(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_STRICT) != 0) _exit(2);
    ftruncate(-1, 0);
}

static void action_jvm_ftruncate(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_JVM) != 0) _exit(2);
    ftruncate(-1, 0);
}

static void action_strict_flock(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_STRICT) != 0) _exit(2);
    flock(-1, LOCK_SH);
}

static void action_jvm_flock(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_JVM) != 0) _exit(2);
    flock(-1, LOCK_SH);
}

static void action_strict_prctl(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_STRICT) != 0) _exit(2);
    prctl(PR_SET_NAME, "test");
}

static void action_jvm_prctl(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_JVM) != 0) _exit(2);
    if (prctl(PR_SET_NAME, "test") != 0) _exit(3);
}

static void action_jvm_prctl_dumpable_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_JVM) != 0) _exit(2);
    prctl(PR_SET_DUMPABLE, 1);
}

static void action_jvm_getrusage(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_JVM) != 0) _exit(2);
    struct rusage ru;
    if (getrusage(RUSAGE_SELF, &ru) != 0) _exit(3);
}

static void action_jvm_socket_eperm(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_JVM) != 0) _exit(2);
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0 && errno == EPERM) _exit(0);
    _exit(3);
}

static void action_jvm_connect_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_JVM) != 0) _exit(2);
    connect(-1, NULL, 0);
}

static void action_jvm_clone_user_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_JVM) != 0) _exit(2);
    syscall(SYS_clone, (uintptr_t)CLONE_NEWUSER, 0, 0, 0, 0);
}

static void action_jvm_unshare_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_JVM) != 0) _exit(2);
    unshare(CLONE_NEWUSER);
}

static void action_ruby_sched_getaffinity(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_RUBY) != 0) _exit(2);
    cpu_set_t mask;
    CPU_ZERO(&mask);
    if (sched_getaffinity(0, sizeof(mask), &mask) != 0) _exit(3);
}

static void action_ruby_eventfd2(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_RUBY) != 0) _exit(2);
    int fd = (int)syscall(SYS_eventfd2, 0, 0);
    if (fd < 0) _exit(3);
    close(fd);
}

static void action_ruby_epoll_create1(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_RUBY) != 0) _exit(2);
    int fd = (int)syscall(SYS_epoll_create1, 0);
    if (fd < 0) _exit(3);
    close(fd);
}

static void action_ruby_epoll_ctl(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_RUBY) != 0) _exit(2);
    int epfd = (int)syscall(SYS_epoll_create1, 0);
    if (epfd < 0) _exit(3);
    int pfd[2];
    if (pipe2(pfd, O_CLOEXEC) != 0) _exit(4);
    struct epoll_event ev;
    memset(&ev, 0, sizeof(ev));
    ev.events = EPOLLIN;
    ev.data.fd = pfd[0];
    int rc = (int)syscall(SYS_epoll_ctl, epfd, EPOLL_CTL_ADD, pfd[0], &ev);
    close(pfd[0]);
    close(pfd[1]);
    close(epfd);
    if (rc != 0) _exit(5);
}

static void action_ruby_epoll_wait(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_RUBY) != 0) _exit(2);
    int epfd = (int)syscall(SYS_epoll_create1, 0);
    if (epfd < 0) _exit(3);
    struct epoll_event ev;
    int nfds = (int)syscall(SYS_epoll_wait, epfd, &ev, 1, 0);
    close(epfd);
    if (nfds < 0) _exit(4);
}

static void action_ruby_epoll_pwait(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_RUBY) != 0) _exit(2);
    int epfd = (int)syscall(SYS_epoll_create1, 0);
    if (epfd < 0) _exit(3);
    struct epoll_event ev;
    int nfds = (int)syscall(SYS_epoll_pwait, epfd, &ev, 1, 0, NULL);
    close(epfd);
    if (nfds < 0) _exit(4);
}

static void action_ruby_epoll_pwait2(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_RUBY) != 0) _exit(2);
    int epfd = (int)syscall(SYS_epoll_create1, 0);
    if (epfd < 0) _exit(3);
    struct epoll_event ev;
    struct timespec ts = {0, 0};
#ifdef SYS_epoll_pwait2
    int nfds = (int)syscall(SYS_epoll_pwait2, epfd, &ev, 1, &ts, NULL);
    close(epfd);
    if (nfds < 0 && errno != ENOSYS) _exit(4);
#else
    close(epfd);
#endif
}

static void action_ruby_prctl_name(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_RUBY) != 0) _exit(2);
    if (prctl(PR_SET_NAME, "ruby-worker") != 0) _exit(3);
}

static void action_ruby_prctl_thp_disable(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_RUBY) != 0) _exit(2);
    /* arg2 == 1 is strictly permitted; kernel requires arg3=arg4=arg5=0 */
    if (prctl(PR_SET_THP_DISABLE, 1UL, 0UL, 0UL, 0UL) != 0) _exit(3);
}

static void action_ruby_prctl_vma_anon(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_RUBY) != 0) _exit(2);
    void *addr = mmap(NULL, 4096, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (addr == MAP_FAILED) _exit(2);
    /* arg2 == PR_SET_VMA_ANON_NAME (0) is strictly permitted */
    (void)prctl(PR_SET_VMA, PR_SET_VMA_ANON_NAME, (unsigned long)addr, 4096, (unsigned long)"ruby-gc");
    munmap(addr, 4096);
}

static void action_ruby_prctl_dumpable_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_RUBY) != 0) _exit(2);
    prctl(PR_SET_DUMPABLE, 1);
}

static void action_ruby_prctl_thp_arg2_invalid_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_RUBY) != 0) _exit(2);
    /* arg2 == 0 must be denied with SIGSYS */
    prctl(PR_SET_THP_DISABLE, 0);
}

static void action_ruby_socket_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_RUBY) != 0) _exit(2);
    socket(AF_INET, SOCK_STREAM, 0);
}

static void action_ruby_clone_user_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_RUBY) != 0) _exit(2);
    syscall(SYS_clone, (uintptr_t)CLONE_NEWUSER, 0, 0, 0, 0);
}

static void action_ruby_unshare_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_RUBY) != 0) _exit(2);
    unshare(CLONE_NEWUSER);
}

static void action_node_capget(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_NODE) != 0) _exit(2);
    struct __user_cap_header_struct hdr = {_LINUX_CAPABILITY_VERSION_3, 0};
    struct __user_cap_data_struct data[2];
    if (syscall(SYS_capget, &hdr, data) != 0) _exit(3);
}

static void action_node_capset_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_NODE) != 0) _exit(2);
    struct __user_cap_header_struct hdr = {_LINUX_CAPABILITY_VERSION_3, 0};
    struct __user_cap_data_struct data[2];
    memset(data, 0, sizeof(data));
    syscall(SYS_capset, &hdr, data);
}

static void action_node_sched_getaffinity(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_NODE) != 0) _exit(2);
    cpu_set_t set;
    if (sched_getaffinity(0, sizeof(set), &set) != 0) _exit(3);
}

static void action_node_sched_getparam(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_NODE) != 0) _exit(2);
    struct sched_param param;
    if (sched_getparam(0, &param) != 0) _exit(3);
}

static void action_node_sched_getscheduler(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_NODE) != 0) _exit(2);
    if (sched_getscheduler(0) < 0) _exit(3);
}

static void action_node_eventfd2(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_NODE) != 0) _exit(2);
    int fd = eventfd(0, EFD_CLOEXEC | EFD_NONBLOCK);
    if (fd < 0) _exit(3);
    close(fd);
}

static void action_node_epoll_create1(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_NODE) != 0) _exit(2);
    int fd = (int)syscall(SYS_epoll_create1, 0);
    if (fd < 0) _exit(3);
    close(fd);
}

static void action_node_epoll_ctl(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_NODE) != 0) _exit(2);
    int epfd = (int)syscall(SYS_epoll_create1, 0);
    if (epfd < 0) _exit(3);
    int pfd[2];
    if (pipe2(pfd, O_CLOEXEC) != 0) _exit(4);
    struct epoll_event ev;
    memset(&ev, 0, sizeof(ev));
    ev.events = EPOLLIN;
    ev.data.fd = pfd[0];
    int rc = (int)syscall(SYS_epoll_ctl, epfd, EPOLL_CTL_ADD, pfd[0], &ev);
    close(pfd[0]);
    close(pfd[1]);
    close(epfd);
    if (rc != 0) _exit(5);
}

static void action_node_epoll_wait(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_NODE) != 0) _exit(2);
    int epfd = (int)syscall(SYS_epoll_create1, 0);
    if (epfd < 0) _exit(3);
    struct epoll_event ev;
    int nfds = (int)syscall(SYS_epoll_wait, epfd, &ev, 1, 0);
    close(epfd);
    if (nfds < 0) _exit(4);
}

static void action_node_epoll_pwait(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_NODE) != 0) _exit(2);
    int epfd = (int)syscall(SYS_epoll_create1, 0);
    if (epfd < 0) _exit(3);
    struct epoll_event ev;
    int nfds = (int)syscall(SYS_epoll_pwait, epfd, &ev, 1, 0, NULL);
    close(epfd);
    if (nfds < 0) _exit(4);
}

static void action_node_epoll_pwait2(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_NODE) != 0) _exit(2);
    int epfd = (int)syscall(SYS_epoll_create1, 0);
    if (epfd < 0) _exit(3);
    struct epoll_event ev;
    struct timespec ts = {0, 0};
#ifdef SYS_epoll_pwait2
    int nfds = (int)syscall(SYS_epoll_pwait2, epfd, &ev, 1, &ts, NULL);
    close(epfd);
    if (nfds < 0 && errno != ENOSYS) _exit(4);
#else
    close(epfd);
#endif
}

static void action_node_io_uring_setup_returns_enosys(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_NODE) != 0) _exit(2);
    char p[128];
    memset(p, 0, sizeof(p));
    long rc = syscall(SYS_io_uring_setup, 256, p);
    if (rc == -1 && errno == ENOSYS) _exit(0);
    _exit(3);
}

static void action_node_io_uring_enter_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_NODE) != 0) _exit(2);
    syscall(SYS_io_uring_enter, 0, 0, 0, 0, NULL, 0);
}

static void action_node_prctl_name(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_NODE) != 0) _exit(2);
    if (prctl(PR_SET_NAME, "node-worker") != 0) _exit(3);
}

static void action_node_prctl_vma_anon(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_NODE) != 0) _exit(2);
    void *addr = mmap(NULL, 4096, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (addr == MAP_FAILED) _exit(2);
    (void)prctl(PR_SET_VMA, PR_SET_VMA_ANON_NAME, (unsigned long)addr, 4096, (unsigned long)"v8:isolate");
    munmap(addr, 4096);
}

static void action_node_prctl_dumpable_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_NODE) != 0) _exit(2);
    prctl(PR_SET_DUMPABLE, 1);
}

static void action_node_socket_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_NODE) != 0) _exit(2);
    socket(AF_INET, SOCK_STREAM, 0);
}

static void action_node_clone_user_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_NODE) != 0) _exit(2);
    syscall(SYS_clone, (uintptr_t)CLONE_NEWUSER, 0, 0, 0, 0);
}

static void action_node_unshare_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_NODE) != 0) _exit(2);
    unshare(CLONE_NEWUSER);
}

static void action_php_sched_getaffinity(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_PHP) != 0) _exit(2);
    cpu_set_t set;
    if (sched_getaffinity(0, sizeof(set), &set) != 0) _exit(3);
}

static void action_php_prctl_name(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_PHP) != 0) _exit(2);
    if (prctl(PR_SET_NAME, "php-cli") != 0) _exit(3);
}

static void action_php_prctl_vma_anon(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_PHP) != 0) _exit(2);
    void *addr = mmap(NULL, 4096, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (addr == MAP_FAILED) _exit(2);
    (void)prctl(PR_SET_VMA, PR_SET_VMA_ANON_NAME, (unsigned long)addr, 4096, (unsigned long)"zend_alloc");
    munmap(addr, 4096);
}

static void action_php_prctl_dumpable_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_PHP) != 0) _exit(2);
    prctl(PR_SET_DUMPABLE, 1);
}

static void action_php_socket_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_PHP) != 0) _exit(2);
    socket(AF_INET, SOCK_STREAM, 0);
}

static void action_php_clone_user_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_PHP) != 0) _exit(2);
    syscall(SYS_clone, (uintptr_t)CLONE_NEWUSER, 0, 0, 0, 0);
}

static void action_php_unshare_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_PHP) != 0) _exit(2);
    unshare(CLONE_NEWUSER);
}

static void action_php_capset_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_PHP) != 0) _exit(2);
    struct __user_cap_header_struct hdr = {_LINUX_CAPABILITY_VERSION_3, 0};
    struct __user_cap_data_struct data[2];
    memset(data, 0, sizeof(data));
    syscall(SYS_capset, &hdr, data);
}

int main(void) {
    printf("========================================================================\n");
    printf("Running Seccomp Profile Isolation & Permitted Syscall Unit Tests\n");
    printf("========================================================================\n");

    /* Test 1: getrusage blocked under strict profile */
    if (expect_sigsys(action_strict_getrusage)) {
        printf("  [PASS] SECCOMP_PROFILE_STRICT correctly kills getrusage with SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_STRICT permitted getrusage!\n");
        return 1;
    }

    /* Test 2: getrusage permitted under compiler profile */
    if (expect_success(action_compiler_getrusage)) {
        printf("  [PASS] SECCOMP_PROFILE_COMPILER permits getrusage without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_COMPILER failed on getrusage!\n");
        return 1;
    }

    /* Test 3: fork() permitted under strict profile */
    if (expect_success(action_strict_fork_permitted)) {
        printf("  [PASS] SECCOMP_PROFILE_STRICT permits fork() without SIGSYS (bounded by pids.max)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_STRICT failed on fork()!\n");
        return 1;
    }

    /* Test 4: wait4 blocked under strict profile */
    if (expect_sigsys(action_strict_wait4)) {
        printf("  [PASS] SECCOMP_PROFILE_STRICT correctly kills wait4 with SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_STRICT permitted wait4!\n");
        return 1;
    }

    /* Test 5: wait4 permitted under compiler profile */
    if (expect_success(action_compiler_wait4)) {
        printf("  [PASS] SECCOMP_PROFILE_COMPILER permits wait4 without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_COMPILER failed on wait4!\n");
        return 1;
    }

    /* Test 6: network socket blocked under compiler profile */
    if (expect_sigsys(action_compiler_network_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_COMPILER kills socket() with SIGSYS (network denied)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_COMPILER permitted socket creation!\n");
        return 1;
    }

    /* Test 7: network socket blocked under strict profile */
    if (expect_sigsys(action_strict_network_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_STRICT kills socket() with SIGSYS (network denied)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_STRICT permitted socket creation!\n");
        return 1;
    }

    /* Test 8: CLONE_NEWUSER blocked under strict profile */
    if (expect_sigsys(action_strict_clone_user_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_STRICT kills clone(CLONE_NEWUSER) with SIGSYS (namespaces denied)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_STRICT permitted CLONE_NEWUSER!\n");
        return 1;
    }

    /* Test 9: CLONE_NEWUSER blocked under compiler profile */
    if (expect_sigsys(action_compiler_clone_user_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_COMPILER kills clone(CLONE_NEWUSER) with SIGSYS (namespaces denied)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_COMPILER permitted CLONE_NEWUSER!\n");
        return 1;
    }

    /* Test 10: unshare blocked under compiler profile */
    if (expect_sigsys(action_compiler_unshare_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_COMPILER kills unshare() with SIGSYS (namespaces denied)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_COMPILER permitted unshare()!\n");
        return 1;
    }

    /* Test 11: direct open permitted under python profile */
    if (expect_success(action_python_open)) {
        printf("  [PASS] SECCOMP_PROFILE_PYTHON permits direct open() without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_PYTHON failed on open()!\n");
        return 1;
    }

    /* Test 12: network socket blocked under python profile */
    if (expect_sigsys(action_python_network_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_PYTHON kills socket() with SIGSYS (network denied)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_PYTHON permitted socket creation!\n");
        return 1;
    }

    /* Test 13: getresuid blocked under strict profile */
    if (expect_sigsys(action_strict_getresuid)) {
        printf("  [PASS] SECCOMP_PROFILE_STRICT correctly kills getresuid with SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_STRICT permitted getresuid!\n");
        return 1;
    }

    /* Test 14: getresgid blocked under strict profile */
    if (expect_sigsys(action_strict_getresgid)) {
        printf("  [PASS] SECCOMP_PROFILE_STRICT correctly kills getresgid with SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_STRICT permitted getresgid!\n");
        return 1;
    }

    /* Test 15: getpgrp blocked under strict profile */
    if (expect_sigsys(action_strict_getpgrp)) {
        printf("  [PASS] SECCOMP_PROFILE_STRICT correctly kills getpgrp with SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_STRICT permitted getpgrp!\n");
        return 1;
    }

    /* Test 16: getresuid permitted under bash profile */
    if (expect_success(action_bash_getresuid)) {
        printf("  [PASS] SECCOMP_PROFILE_BASH permits getresuid without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_BASH failed on getresuid!\n");
        return 1;
    }

    /* Test 17: getresgid permitted under bash profile */
    if (expect_success(action_bash_getresgid)) {
        printf("  [PASS] SECCOMP_PROFILE_BASH permits getresgid without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_BASH failed on getresgid!\n");
        return 1;
    }

    /* Test 18: getpgrp permitted under bash profile */
    if (expect_success(action_bash_getpgrp)) {
        printf("  [PASS] SECCOMP_PROFILE_BASH permits getpgrp without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_BASH failed on getpgrp!\n");
        return 1;
    }

    /* Test 19: wait4 permitted under bash profile */
    if (expect_success(action_bash_wait4)) {
        printf("  [PASS] SECCOMP_PROFILE_BASH permits wait4 without SIGSYS (for subshell pipelines)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_BASH failed on wait4!\n");
        return 1;
    }

    /* Test 20: network socket blocked under bash profile */
    if (expect_sigsys(action_bash_network_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_BASH kills socket() with SIGSYS (network denied)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_BASH permitted socket creation!\n");
        return 1;
    }

    /* Test 21: CLONE_NEWUSER blocked under bash profile */
    if (expect_sigsys(action_bash_clone_user_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_BASH kills clone(CLONE_NEWUSER) with SIGSYS (namespaces denied)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_BASH permitted CLONE_NEWUSER!\n");
        return 1;
    }

    /* Test 22: unshare blocked under bash profile */
    if (expect_sigsys(action_bash_unshare_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_BASH kills unshare() with SIGSYS (namespaces denied)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_BASH permitted unshare()!\n");
        return 1;
    }

    /* Test 23: fchdir blocked under strict profile */
    if (expect_sigsys(action_strict_fchdir)) {
        printf("  [PASS] SECCOMP_PROFILE_STRICT correctly kills fchdir with SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_STRICT permitted fchdir!\n");
        return 1;
    }

    /* Test 24: fchdir permitted under JVM profile */
    if (expect_success(action_jvm_fchdir)) {
        printf("  [PASS] SECCOMP_PROFILE_JVM permits fchdir without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_JVM failed on fchdir!\n");
        return 1;
    }

    /* Test 25: ftruncate blocked under strict profile */
    if (expect_sigsys(action_strict_ftruncate)) {
        printf("  [PASS] SECCOMP_PROFILE_STRICT correctly kills ftruncate with SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_STRICT permitted ftruncate!\n");
        return 1;
    }

    /* Test 26: ftruncate permitted under JVM profile */
    if (expect_success(action_jvm_ftruncate)) {
        printf("  [PASS] SECCOMP_PROFILE_JVM permits ftruncate without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_JVM failed on ftruncate!\n");
        return 1;
    }

    /* Test 27: flock blocked under strict profile */
    if (expect_sigsys(action_strict_flock)) {
        printf("  [PASS] SECCOMP_PROFILE_STRICT correctly kills flock with SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_STRICT permitted flock!\n");
        return 1;
    }

    /* Test 28: flock permitted under JVM profile */
    if (expect_success(action_jvm_flock)) {
        printf("  [PASS] SECCOMP_PROFILE_JVM permits flock without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_JVM failed on flock!\n");
        return 1;
    }

    /* Test 29: prctl blocked under strict profile */
    if (expect_sigsys(action_strict_prctl)) {
        printf("  [PASS] SECCOMP_PROFILE_STRICT correctly kills prctl with SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_STRICT permitted prctl!\n");
        return 1;
    }

    /* Test 30: prctl(PR_SET_NAME) permitted under JVM profile */
    if (expect_success(action_jvm_prctl)) {
        printf("  [PASS] SECCOMP_PROFILE_JVM permits prctl(PR_SET_NAME) without SIGSYS (for thread naming)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_JVM failed on prctl(PR_SET_NAME)!\n");
        return 1;
    }

    /* Test 31: prctl with PR_SET_DUMPABLE blocked under JVM profile */
    if (expect_sigsys(action_jvm_prctl_dumpable_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_JVM kills prctl(PR_SET_DUMPABLE) with SIGSYS (dumpability locked)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_JVM permitted prctl(PR_SET_DUMPABLE)!\n");
        return 1;
    }

    /* Test 32: getrusage permitted under JVM profile */
    if (expect_success(action_jvm_getrusage)) {
        printf("  [PASS] SECCOMP_PROFILE_JVM permits getrusage without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_JVM failed on getrusage!\n");
        return 1;
    }

    /* Test 33: socket() fails with EPERM under JVM profile (network denied without SIGSYS) */
    if (expect_success(action_jvm_socket_eperm)) {
        printf("  [PASS] SECCOMP_PROFILE_JVM returns EPERM on socket() (network denied)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_JVM did not return EPERM on socket()!\n");
        return 1;
    }

    /* Test 34: connect() blocked with SIGSYS under JVM profile */
    if (expect_sigsys(action_jvm_connect_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_JVM kills connect() with SIGSYS (network denied)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_JVM permitted connect()!\n");
        return 1;
    }

    /* Test 35: CLONE_NEWUSER blocked under JVM profile */
    if (expect_sigsys(action_jvm_clone_user_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_JVM kills clone(CLONE_NEWUSER) with SIGSYS (namespaces denied)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_JVM permitted CLONE_NEWUSER!\n");
        return 1;
    }

    /* Test 36: unshare blocked under JVM profile */
    if (expect_sigsys(action_jvm_unshare_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_JVM kills unshare() with SIGSYS (namespaces denied)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_JVM permitted unshare()!\n");
        return 1;
    }

    /* Test 37: sched_getaffinity permitted under Ruby profile */
    if (expect_success(action_ruby_sched_getaffinity)) {
        printf("  [PASS] SECCOMP_PROFILE_RUBY permits sched_getaffinity without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_RUBY failed on sched_getaffinity!\n");
        return 1;
    }

    /* Test 38: eventfd2 permitted under Ruby profile */
    if (expect_success(action_ruby_eventfd2)) {
        printf("  [PASS] SECCOMP_PROFILE_RUBY permits eventfd2 without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_RUBY failed on eventfd2!\n");
        return 1;
    }

    /* Test 39: epoll_create1 permitted under Ruby profile */
    if (expect_success(action_ruby_epoll_create1)) {
        printf("  [PASS] SECCOMP_PROFILE_RUBY permits epoll_create1 without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_RUBY failed on epoll_create1!\n");
        return 1;
    }

    /* Test 40: epoll_ctl permitted under Ruby profile */
    if (expect_success(action_ruby_epoll_ctl)) {
        printf("  [PASS] SECCOMP_PROFILE_RUBY permits epoll_ctl without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_RUBY failed on epoll_ctl!\n");
        return 1;
    }

    /* Test 41: epoll_wait permitted under Ruby profile */
    if (expect_success(action_ruby_epoll_wait)) {
        printf("  [PASS] SECCOMP_PROFILE_RUBY permits epoll_wait without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_RUBY failed on epoll_wait!\n");
        return 1;
    }

    /* Test 42: epoll_pwait permitted under Ruby profile */
    if (expect_success(action_ruby_epoll_pwait)) {
        printf("  [PASS] SECCOMP_PROFILE_RUBY permits epoll_pwait without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_RUBY failed on epoll_pwait!\n");
        return 1;
    }

    /* Test 43: epoll_pwait2 permitted under Ruby profile */
    if (expect_success(action_ruby_epoll_pwait2)) {
        printf("  [PASS] SECCOMP_PROFILE_RUBY permits epoll_pwait2 without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_RUBY failed on epoll_pwait2!\n");
        return 1;
    }

    /* Test 44: prctl(PR_SET_NAME) permitted under Ruby profile */
    if (expect_success(action_ruby_prctl_name)) {
        printf("  [PASS] SECCOMP_PROFILE_RUBY permits prctl(PR_SET_NAME) without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_RUBY failed on prctl(PR_SET_NAME)!\n");
        return 1;
    }

    /* Test 45: prctl(PR_SET_THP_DISABLE, 1) permitted under Ruby profile */
    if (expect_success(action_ruby_prctl_thp_disable)) {
        printf("  [PASS] SECCOMP_PROFILE_RUBY permits prctl(PR_SET_THP_DISABLE, 1) without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_RUBY failed on prctl(PR_SET_THP_DISABLE, 1)!\n");
        return 1;
    }

    /* Test 46: prctl(PR_SET_VMA, PR_SET_VMA_ANON_NAME) permitted under Ruby profile */
    if (expect_success(action_ruby_prctl_vma_anon)) {
        printf("  [PASS] SECCOMP_PROFILE_RUBY permits prctl(PR_SET_VMA, PR_SET_VMA_ANON_NAME) without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_RUBY failed on prctl(PR_SET_VMA, PR_SET_VMA_ANON_NAME)!\n");
        return 1;
    }

    /* Test 47: prctl(PR_SET_DUMPABLE) blocked under Ruby profile */
    if (expect_sigsys(action_ruby_prctl_dumpable_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_RUBY kills prctl(PR_SET_DUMPABLE) with SIGSYS (dumpability locked)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_RUBY permitted prctl(PR_SET_DUMPABLE)!\n");
        return 1;
    }

    /* Test 48: prctl(PR_SET_THP_DISABLE, 0) blocked under Ruby profile */
    if (expect_sigsys(action_ruby_prctl_thp_arg2_invalid_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_RUBY kills prctl(PR_SET_THP_DISABLE, 0) with SIGSYS (restricted to 1)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_RUBY permitted prctl(PR_SET_THP_DISABLE, 0)!\n");
        return 1;
    }

    /* Test 49: socket() blocked under Ruby profile */
    if (expect_sigsys(action_ruby_socket_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_RUBY kills socket() with SIGSYS (network denied)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_RUBY permitted socket()!\n");
        return 1;
    }

    /* Test 50: CLONE_NEWUSER blocked under Ruby profile */
    if (expect_sigsys(action_ruby_clone_user_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_RUBY kills clone(CLONE_NEWUSER) with SIGSYS (namespaces denied)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_RUBY permitted CLONE_NEWUSER!\n");
        return 1;
    }

    /* Test 51: unshare blocked under Ruby profile */
    if (expect_sigsys(action_ruby_unshare_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_RUBY kills unshare() with SIGSYS (namespaces denied)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_RUBY permitted unshare()!\n");
        return 1;
    }

    /* Test 52: capget permitted under Node profile */
    if (expect_success(action_node_capget)) {
        printf("  [PASS] SECCOMP_PROFILE_NODE permits capget without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_NODE failed on capget!\n");
        return 1;
    }

    /* Test 53: capset blocked under Node profile */
    if (expect_sigsys(action_node_capset_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_NODE kills capset() with SIGSYS (privilege modification locked)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_NODE permitted capset()!\n");
        return 1;
    }

    /* Test 54: sched_getaffinity permitted under Node profile */
    if (expect_success(action_node_sched_getaffinity)) {
        printf("  [PASS] SECCOMP_PROFILE_NODE permits sched_getaffinity without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_NODE failed on sched_getaffinity!\n");
        return 1;
    }

    /* Test 55: sched_getparam permitted under Node profile */
    if (expect_success(action_node_sched_getparam)) {
        printf("  [PASS] SECCOMP_PROFILE_NODE permits sched_getparam without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_NODE failed on sched_getparam!\n");
        return 1;
    }

    /* Test 56: sched_getscheduler permitted under Node profile */
    if (expect_success(action_node_sched_getscheduler)) {
        printf("  [PASS] SECCOMP_PROFILE_NODE permits sched_getscheduler without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_NODE failed on sched_getscheduler!\n");
        return 1;
    }

    /* Test 57: eventfd2 permitted under Node profile */
    if (expect_success(action_node_eventfd2)) {
        printf("  [PASS] SECCOMP_PROFILE_NODE permits eventfd2 without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_NODE failed on eventfd2!\n");
        return 1;
    }

    /* Test 58: epoll_create1 permitted under Node profile */
    if (expect_success(action_node_epoll_create1)) {
        printf("  [PASS] SECCOMP_PROFILE_NODE permits epoll_create1 without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_NODE failed on epoll_create1!\n");
        return 1;
    }

    /* Test 59: epoll_ctl permitted under Node profile */
    if (expect_success(action_node_epoll_ctl)) {
        printf("  [PASS] SECCOMP_PROFILE_NODE permits epoll_ctl without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_NODE failed on epoll_ctl!\n");
        return 1;
    }

    /* Test 60: epoll_wait permitted under Node profile */
    if (expect_success(action_node_epoll_wait)) {
        printf("  [PASS] SECCOMP_PROFILE_NODE permits epoll_wait without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_NODE failed on epoll_wait!\n");
        return 1;
    }

    /* Test 61: epoll_pwait permitted under Node profile */
    if (expect_success(action_node_epoll_pwait)) {
        printf("  [PASS] SECCOMP_PROFILE_NODE permits epoll_pwait without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_NODE failed on epoll_pwait!\n");
        return 1;
    }

    /* Test 62: epoll_pwait2 permitted under Node profile */
    if (expect_success(action_node_epoll_pwait2)) {
        printf("  [PASS] SECCOMP_PROFILE_NODE permits epoll_pwait2 without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_NODE failed on epoll_pwait2!\n");
        return 1;
    }

    /* Test 63: io_uring_setup returns ENOSYS under Node profile */
    if (expect_success(action_node_io_uring_setup_returns_enosys)) {
        printf("  [PASS] SECCOMP_PROFILE_NODE safely returns ENOSYS on io_uring_setup without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_NODE failed to return ENOSYS on io_uring_setup!\n");
        return 1;
    }

    /* Test 64: io_uring_enter blocked with SIGSYS under Node profile */
    if (expect_sigsys(action_node_io_uring_enter_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_NODE kills io_uring_enter with SIGSYS (io_uring blocked)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_NODE permitted io_uring_enter!\n");
        return 1;
    }

    /* Test 65: prctl(PR_SET_NAME) permitted under Node profile */
    if (expect_success(action_node_prctl_name)) {
        printf("  [PASS] SECCOMP_PROFILE_NODE permits prctl(PR_SET_NAME) without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_NODE failed on prctl(PR_SET_NAME)!\n");
        return 1;
    }

    /* Test 66: prctl(PR_SET_VMA, PR_SET_VMA_ANON_NAME) permitted under Node profile */
    if (expect_success(action_node_prctl_vma_anon)) {
        printf("  [PASS] SECCOMP_PROFILE_NODE permits prctl(PR_SET_VMA, PR_SET_VMA_ANON_NAME) without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_NODE failed on prctl(PR_SET_VMA, PR_SET_VMA_ANON_NAME)!\n");
        return 1;
    }

    /* Test 67: prctl(PR_SET_DUMPABLE) blocked under Node profile */
    if (expect_sigsys(action_node_prctl_dumpable_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_NODE kills prctl(PR_SET_DUMPABLE) with SIGSYS (dumpability locked)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_NODE permitted prctl(PR_SET_DUMPABLE)!\n");
        return 1;
    }

    /* Test 68: socket() blocked under Node profile */
    if (expect_sigsys(action_node_socket_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_NODE kills socket() with SIGSYS (network denied)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_NODE permitted socket()!\n");
        return 1;
    }

    /* Test 69: CLONE_NEWUSER blocked under Node profile */
    if (expect_sigsys(action_node_clone_user_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_NODE kills clone(CLONE_NEWUSER) with SIGSYS (namespaces denied)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_NODE permitted CLONE_NEWUSER!\n");
        return 1;
    }

    /* Test 70: unshare blocked under Node profile */
    if (expect_sigsys(action_node_unshare_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_NODE kills unshare() with SIGSYS (namespaces denied)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_NODE permitted unshare()!\n");
        return 1;
    }

    /* Test 71: sched_getaffinity permitted under PHP profile */
    if (expect_success(action_php_sched_getaffinity)) {
        printf("  [PASS] SECCOMP_PROFILE_PHP permits sched_getaffinity without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_PHP failed on sched_getaffinity!\n");
        return 1;
    }

    /* Test 72: prctl(PR_SET_NAME) permitted under PHP profile */
    if (expect_success(action_php_prctl_name)) {
        printf("  [PASS] SECCOMP_PROFILE_PHP permits prctl(PR_SET_NAME) without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_PHP failed on prctl(PR_SET_NAME)!\n");
        return 1;
    }

    /* Test 73: prctl(PR_SET_VMA, PR_SET_VMA_ANON_NAME) permitted under PHP profile */
    if (expect_success(action_php_prctl_vma_anon)) {
        printf("  [PASS] SECCOMP_PROFILE_PHP permits prctl(PR_SET_VMA, PR_SET_VMA_ANON_NAME) without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_PHP failed on prctl(PR_SET_VMA, PR_SET_VMA_ANON_NAME)!\n");
        return 1;
    }

    /* Test 74: prctl(PR_SET_DUMPABLE) blocked with SIGSYS under PHP profile */
    if (expect_sigsys(action_php_prctl_dumpable_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_PHP kills prctl(PR_SET_DUMPABLE) with SIGSYS (dumpability locked)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_PHP permitted prctl(PR_SET_DUMPABLE)!\n");
        return 1;
    }

    /* Test 75: socket() blocked with SIGSYS under PHP profile */
    if (expect_sigsys(action_php_socket_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_PHP kills socket() with SIGSYS (network denied)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_PHP permitted socket()!\n");
        return 1;
    }

    /* Test 76: CLONE_NEWUSER blocked with SIGSYS under PHP profile */
    if (expect_sigsys(action_php_clone_user_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_PHP kills clone(CLONE_NEWUSER) with SIGSYS (namespaces denied)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_PHP permitted CLONE_NEWUSER!\n");
        return 1;
    }

    /* Test 77: unshare blocked with SIGSYS under PHP profile */
    if (expect_sigsys(action_php_unshare_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_PHP kills unshare() with SIGSYS (namespaces denied)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_PHP permitted unshare()!\n");
        return 1;
    }

    /* Test 78: capset blocked with SIGSYS under PHP profile */
    if (expect_sigsys(action_php_capset_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_PHP kills capset() with SIGSYS (privilege modification locked)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_PHP permitted capset()!\n");
        return 1;
    }

    printf("========================================================================\n");
    printf("SECCOMP PROFILE UNIT TEST SUMMARY:\n");
    printf("  All 78 seccomp profile unit verification checks PASSED.\n");
    printf("========================================================================\n");
    return 0;
}
