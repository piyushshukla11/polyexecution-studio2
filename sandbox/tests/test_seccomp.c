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
#include <sys/fsuid.h>
#include <sys/mman.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <sys/syscall.h>
#include <sys/timerfd.h>
#include <time.h>
#include <poll.h>
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

static void action_dart_sched_getaffinity(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_DART) != 0) _exit(2);
    cpu_set_t set;
    if (sched_getaffinity(0, sizeof(set), &set) != 0) _exit(3);
}

static void action_dart_eventfd2(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_DART) != 0) _exit(2);
    int fd = (int)syscall(SYS_eventfd2, 0, 0);
    if (fd < 0) _exit(3);
    close(fd);
}

static void action_dart_epoll_create1(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_DART) != 0) _exit(2);
    int epfd = (int)syscall(SYS_epoll_create1, 0);
    if (epfd < 0) _exit(3);
    close(epfd);
}

static void action_dart_epoll_ctl(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_DART) != 0) _exit(2);
    int epfd = (int)syscall(SYS_epoll_create1, 0);
    if (epfd < 0) _exit(3);
    int p[2];
    if (pipe(p) != 0) { close(epfd); _exit(3); }
    struct epoll_event ev;
    ev.events = EPOLLIN;
    ev.data.fd = p[0];
    int rc = (int)syscall(SYS_epoll_ctl, epfd, EPOLL_CTL_ADD, p[0], &ev);
    close(p[0]); close(p[1]); close(epfd);
    if (rc != 0) _exit(4);
}

static void action_dart_epoll_wait(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_DART) != 0) _exit(2);
    int epfd = (int)syscall(SYS_epoll_create1, 0);
    if (epfd < 0) _exit(3);
    struct epoll_event ev;
    int nfds = (int)syscall(SYS_epoll_wait, epfd, &ev, 1, 0);
    close(epfd);
    if (nfds < 0) _exit(4);
}

static void action_dart_epoll_pwait(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_DART) != 0) _exit(2);
    int epfd = (int)syscall(SYS_epoll_create1, 0);
    if (epfd < 0) _exit(3);
    struct epoll_event ev;
    int nfds = (int)syscall(SYS_epoll_pwait, epfd, &ev, 1, 0, NULL);
    close(epfd);
    if (nfds < 0) _exit(4);
}

static void action_dart_epoll_pwait2(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_DART) != 0) _exit(2);
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

static void action_dart_timerfd_create(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_DART) != 0) _exit(2);
    int tfd = (int)syscall(SYS_timerfd_create, CLOCK_MONOTONIC, 0);
    if (tfd < 0) _exit(3);
    close(tfd);
}

static void action_dart_timerfd_settime(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_DART) != 0) _exit(2);
    int tfd = (int)syscall(SYS_timerfd_create, CLOCK_MONOTONIC, 0);
    if (tfd < 0) _exit(3);
    struct itimerspec its;
    memset(&its, 0, sizeof(its));
    its.it_value.tv_sec = 1;
    int rc = (int)syscall(SYS_timerfd_settime, tfd, 0, &its, NULL);
    close(tfd);
    if (rc != 0) _exit(4);
}

static void action_dart_timerfd_gettime(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_DART) != 0) _exit(2);
    int tfd = (int)syscall(SYS_timerfd_create, CLOCK_MONOTONIC, 0);
    if (tfd < 0) _exit(3);
    struct itimerspec its;
    int rc = (int)syscall(SYS_timerfd_gettime, tfd, &its);
    close(tfd);
    if (rc != 0) _exit(4);
}

static void action_dart_prctl_name(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_DART) != 0) _exit(2);
    if (prctl(PR_SET_NAME, "dart-isolate") != 0) _exit(3);
}

static void action_dart_prctl_get_name(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_DART) != 0) _exit(2);
    char name[16];
    if (prctl(PR_GET_NAME, name) != 0) _exit(3);
}

static void action_dart_prctl_vma_anon(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_DART) != 0) _exit(2);
    void *addr = mmap(NULL, 4096, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (addr == MAP_FAILED) _exit(2);
    (void)prctl(PR_SET_VMA, PR_SET_VMA_ANON_NAME, (unsigned long)addr, 4096, (unsigned long)"dart-isolate");
    munmap(addr, 4096);
}

static void action_dart_prctl_dumpable_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_DART) != 0) _exit(2);
    prctl(PR_SET_DUMPABLE, 1);
}

static void action_dart_socket_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_DART) != 0) _exit(2);
    socket(AF_INET, SOCK_STREAM, 0);
}

static void action_dart_clone_user_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_DART) != 0) _exit(2);
    syscall(SYS_clone, (uintptr_t)CLONE_NEWUSER, 0, 0, 0, 0);
}

static void action_dart_unshare_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_DART) != 0) _exit(2);
    unshare(CLONE_NEWUSER);
}

static void action_dart_capset_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_DART) != 0) _exit(2);
    struct __user_cap_header_struct hdr = {_LINUX_CAPABILITY_VERSION_3, 0};
    struct __user_cap_data_struct data[2];
    memset(data, 0, sizeof(data));
    syscall(SYS_capset, &hdr, data);
}

static void action_racket_sched_getaffinity(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_RACKET) != 0) _exit(2);
    cpu_set_t set;
    if (sched_getaffinity(0, sizeof(set), &set) != 0) _exit(3);
}

static void action_racket_epoll_create(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_RACKET) != 0) _exit(2);
    int epfd = (int)syscall(SYS_epoll_create, 1);
    if (epfd < 0) _exit(3);
    close(epfd);
}

static void action_racket_epoll_create1(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_RACKET) != 0) _exit(2);
    int epfd = (int)syscall(SYS_epoll_create1, 0);
    if (epfd < 0) _exit(3);
    close(epfd);
}

static void action_racket_epoll_ctl(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_RACKET) != 0) _exit(2);
    int epfd = (int)syscall(SYS_epoll_create1, 0);
    if (epfd < 0) _exit(3);
    int p[2];
    if (pipe(p) != 0) { close(epfd); _exit(3); }
    struct epoll_event ev;
    ev.events = EPOLLIN;
    ev.data.fd = p[0];
    int rc = (int)syscall(SYS_epoll_ctl, epfd, EPOLL_CTL_ADD, p[0], &ev);
    close(p[0]); close(p[1]); close(epfd);
    if (rc != 0) _exit(4);
}

static void action_racket_epoll_wait(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_RACKET) != 0) _exit(2);
    int epfd = (int)syscall(SYS_epoll_create1, 0);
    if (epfd < 0) _exit(3);
    struct epoll_event ev;
    int nfds = (int)syscall(SYS_epoll_wait, epfd, &ev, 1, 0);
    close(epfd);
    if (nfds < 0) _exit(4);
}

static void action_racket_epoll_pwait(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_RACKET) != 0) _exit(2);
    int epfd = (int)syscall(SYS_epoll_create1, 0);
    if (epfd < 0) _exit(3);
    struct epoll_event ev;
    int nfds = (int)syscall(SYS_epoll_pwait, epfd, &ev, 1, 0, NULL);
    close(epfd);
    if (nfds < 0) _exit(4);
}

static void action_racket_epoll_pwait2(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_RACKET) != 0) _exit(2);
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

static void action_racket_poll(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_RACKET) != 0) _exit(2);
    int p[2];
    if (pipe(p) != 0) _exit(3);
    struct pollfd pfd = {.fd = p[0], .events = POLLIN, .revents = 0};
    int rc = poll(&pfd, 1, 0);
    close(p[0]); close(p[1]);
    if (rc < 0) _exit(4);
}

static void action_racket_prctl_name(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_RACKET) != 0) _exit(2);
    if (prctl(PR_SET_NAME, "racket-worker") != 0) _exit(3);
}

static void action_racket_prctl_get_name(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_RACKET) != 0) _exit(2);
    char name[16];
    if (prctl(PR_GET_NAME, name) != 0) _exit(3);
}

static void action_racket_prctl_dumpable_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_RACKET) != 0) _exit(2);
    prctl(PR_SET_DUMPABLE, 1);
}

static void action_racket_socket_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_RACKET) != 0) _exit(2);
    socket(AF_INET, SOCK_STREAM, 0);
}

static void action_racket_clone_user_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_RACKET) != 0) _exit(2);
    syscall(SYS_clone, (uintptr_t)CLONE_NEWUSER, 0, 0, 0, 0);
}

static void action_racket_unshare_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_RACKET) != 0) _exit(2);
    unshare(CLONE_NEWUSER);
}

static void action_racket_capset_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_RACKET) != 0) _exit(2);
    struct __user_cap_header_struct hdr = {_LINUX_CAPABILITY_VERSION_3, 0};
    struct __user_cap_data_struct data[2];
    memset(data, 0, sizeof(data));
    syscall(SYS_capset, &hdr, data);
}

static void action_racket_sched_setscheduler_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_RACKET) != 0) _exit(2);
    struct sched_param param = {0};
    sched_setscheduler(0, SCHED_FIFO, &param);
}

static void action_erlang_getpgrp(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ERLANG) != 0) _exit(2);
    (void)getpgrp();
}

static void action_erlang_getresuid(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ERLANG) != 0) _exit(2);
    uid_t ruid, euid, suid;
    if (getresuid(&ruid, &euid, &suid) != 0) _exit(3);
}

static void action_erlang_getresgid(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ERLANG) != 0) _exit(2);
    gid_t rgid, egid, sgid;
    if (getresgid(&rgid, &egid, &sgid) != 0) _exit(3);
}

static void action_erlang_wait4(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ERLANG) != 0) _exit(2);
    int status;
    (void)wait4(-1, &status, WNOHANG, NULL);
}

static void action_erlang_sched_getaffinity(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ERLANG) != 0) _exit(2);
    cpu_set_t set;
    if (sched_getaffinity(0, sizeof(set), &set) != 0) _exit(3);
}

static void action_erlang_epoll_create(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ERLANG) != 0) _exit(2);
    int epfd = (int)syscall(SYS_epoll_create, 1);
    if (epfd < 0) _exit(3);
    close(epfd);
}

static void action_erlang_epoll_create1(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ERLANG) != 0) _exit(2);
    int epfd = (int)syscall(SYS_epoll_create1, 0);
    if (epfd < 0) _exit(3);
    close(epfd);
}

static void action_erlang_epoll_ctl(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ERLANG) != 0) _exit(2);
    int epfd = (int)syscall(SYS_epoll_create1, 0);
    if (epfd < 0) _exit(3);
    int p[2];
    if (pipe(p) != 0) { close(epfd); _exit(3); }
    struct epoll_event ev;
    ev.events = EPOLLIN;
    ev.data.fd = p[0];
    int rc = (int)syscall(SYS_epoll_ctl, epfd, EPOLL_CTL_ADD, p[0], &ev);
    close(p[0]); close(p[1]); close(epfd);
    if (rc != 0) _exit(4);
}

static void action_erlang_epoll_wait(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ERLANG) != 0) _exit(2);
    int epfd = (int)syscall(SYS_epoll_create1, 0);
    if (epfd < 0) _exit(3);
    struct epoll_event ev;
    int nfds = (int)syscall(SYS_epoll_wait, epfd, &ev, 1, 0);
    close(epfd);
    if (nfds < 0) _exit(4);
}

static void action_erlang_epoll_pwait(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ERLANG) != 0) _exit(2);
    int epfd = (int)syscall(SYS_epoll_create1, 0);
    if (epfd < 0) _exit(3);
    struct epoll_event ev;
    int nfds = (int)syscall(SYS_epoll_pwait, epfd, &ev, 1, 0, NULL);
    close(epfd);
    if (nfds < 0) _exit(4);
}

static void action_erlang_epoll_pwait2(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ERLANG) != 0) _exit(2);
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

static void action_erlang_poll(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ERLANG) != 0) _exit(2);
    int p[2];
    if (pipe(p) != 0) _exit(3);
    struct pollfd pfd = {.fd = p[0], .events = POLLIN, .revents = 0};
    int rc = poll(&pfd, 1, 0);
    close(p[0]); close(p[1]);
    if (rc < 0) _exit(4);
}

static void action_erlang_ppoll(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ERLANG) != 0) _exit(2);
    int p[2];
    if (pipe(p) != 0) _exit(3);
    struct pollfd pfd = {.fd = p[0], .events = POLLIN, .revents = 0};
    struct timespec ts = {0, 0};
    int rc = ppoll(&pfd, 1, &ts, NULL);
    close(p[0]); close(p[1]);
    if (rc < 0) _exit(4);
}

static void action_erlang_timerfd_create(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ERLANG) != 0) _exit(2);
    int tfd = (int)syscall(SYS_timerfd_create, CLOCK_MONOTONIC, 0);
    if (tfd < 0) _exit(3);
    close(tfd);
}

static void action_erlang_timerfd_settime(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ERLANG) != 0) _exit(2);
    int tfd = (int)syscall(SYS_timerfd_create, CLOCK_MONOTONIC, 0);
    if (tfd < 0) _exit(3);
    struct itimerspec its;
    memset(&its, 0, sizeof(its));
    its.it_value.tv_sec = 1;
    int rc = (int)syscall(SYS_timerfd_settime, tfd, 0, &its, NULL);
    close(tfd);
    if (rc != 0) _exit(4);
}

static void action_erlang_timerfd_gettime(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ERLANG) != 0) _exit(2);
    int tfd = (int)syscall(SYS_timerfd_create, CLOCK_MONOTONIC, 0);
    if (tfd < 0) _exit(3);
    struct itimerspec its;
    int rc = (int)syscall(SYS_timerfd_gettime, tfd, &its);
    close(tfd);
    if (rc != 0) _exit(4);
}

static void action_erlang_prctl_name(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ERLANG) != 0) _exit(2);
    if (prctl(PR_SET_NAME, "beam-worker") != 0) _exit(3);
}

static void action_erlang_prctl_get_name(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ERLANG) != 0) _exit(2);
    char name[16];
    if (prctl(PR_GET_NAME, name) != 0) _exit(3);
}

static void action_erlang_prctl_dumpable_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ERLANG) != 0) _exit(2);
    prctl(PR_SET_DUMPABLE, 1);
}

static void action_erlang_chdir(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ERLANG) != 0) _exit(2);
    if (chdir("/tmp") != 0) _exit(3);
}

static void action_erlang_memfd_create(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ERLANG) != 0) _exit(2);
    int fd = (int)syscall(SYS_memfd_create, "beam_jit", 0);
    if (fd < 0) _exit(3);
    close(fd);
}

static void action_erlang_ftruncate(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ERLANG) != 0) _exit(2);
    int fd = (int)syscall(SYS_memfd_create, "beam_jit_trunc", 0);
    if (fd < 0) _exit(3);
    int rc = ftruncate(fd, 4096);
    close(fd);
    if (rc != 0) _exit(4);
}

static void action_erlang_setsid(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ERLANG) != 0) _exit(2);
    (void)setsid();
}

static void action_erlang_pselect6(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ERLANG) != 0) _exit(2);
    struct timespec ts = {0, 0};
    (void)pselect(0, NULL, NULL, NULL, &ts, NULL);
}

static void action_erlang_close_range(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ERLANG) != 0) _exit(2);
    (void)syscall(436, 100, 105, 0);
}

static void action_erlang_socketpair_unix(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ERLANG) != 0) _exit(2);
    int sv[2];
    if (socketpair(AF_UNIX, SOCK_STREAM, 0, sv) != 0) _exit(3);
    close(sv[0]);
    close(sv[1]);
}

static void action_erlang_socketpair_inet_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ERLANG) != 0) _exit(2);
    int sv[2];
    socketpair(AF_INET, SOCK_STREAM, 0, sv);
}

static void action_erlang_socket_returns_eperm(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ERLANG) != 0) _exit(2);
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd == -1 && errno == EPERM) _exit(0);
    _exit(3);
}

static void action_erlang_connect_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ERLANG) != 0) _exit(2);
    connect(-1, NULL, 0);
}

static void action_erlang_bind_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ERLANG) != 0) _exit(2);
    bind(-1, NULL, 0);
}

static void action_erlang_setfsuid_zero(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ERLANG) != 0) _exit(2);
    setfsuid(0);
}

static void action_erlang_setfsuid_nonzero_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ERLANG) != 0) _exit(2);
    setfsuid(1000);
}

static void action_erlang_setfsgid_zero(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ERLANG) != 0) _exit(2);
    setfsgid(0);
}

static void action_erlang_setfsgid_nonzero_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ERLANG) != 0) _exit(2);
    setfsgid(1000);
}

static void action_erlang_sendmsg_recvmsg(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ERLANG) != 0) _exit(2);
    int sv[2];
    if (socketpair(AF_UNIX, SOCK_STREAM, 0, sv) != 0) _exit(2);
    char buf = 'E';
    struct iovec iov = { .iov_base = &buf, .iov_len = 1 };
    struct msghdr msg;
    memset(&msg, 0, sizeof(msg));
    msg.msg_iov = &iov;
    msg.msg_iovlen = 1;
    if (sendmsg(sv[0], &msg, 0) != 1) _exit(3);
    char in_buf;
    struct iovec in_iov = { .iov_base = &in_buf, .iov_len = 1 };
    struct msghdr in_msg;
    memset(&in_msg, 0, sizeof(in_msg));
    in_msg.msg_iov = &in_iov;
    in_msg.msg_iovlen = 1;
    if (recvmsg(sv[1], &in_msg, 0) != 1 || in_buf != 'E') _exit(4);
    close(sv[0]);
    close(sv[1]);
}

static void action_erlang_getsockopt_setsockopt(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ERLANG) != 0) _exit(2);
    int sv[2];
    if (socketpair(AF_UNIX, SOCK_STREAM, 0, sv) != 0) _exit(2);
    int val = 1;
    if (setsockopt(sv[0], SOL_SOCKET, SO_REUSEADDR, &val, sizeof(val)) != 0) _exit(3);
    int out_val = 0;
    socklen_t len = sizeof(out_val);
    if (getsockopt(sv[0], SOL_SOCKET, SO_REUSEADDR, &out_val, &len) != 0) _exit(4);
    close(sv[0]);
    close(sv[1]);
}

static void action_erlang_clone_user_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ERLANG) != 0) _exit(2);
    syscall(SYS_clone, (uintptr_t)CLONE_NEWUSER, 0, 0, 0, 0);
}

static void action_erlang_unshare_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ERLANG) != 0) _exit(2);
    unshare(CLONE_NEWUSER);
}

static void action_erlang_capset_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ERLANG) != 0) _exit(2);
    struct __user_cap_header_struct hdr = {_LINUX_CAPABILITY_VERSION_3, 0};
    struct __user_cap_data_struct data[2];
    memset(data, 0, sizeof(data));
    syscall(SYS_capset, &hdr, data);
}

static void action_erlang_sched_setscheduler_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ERLANG) != 0) _exit(2);
    struct sched_param param = {0};
    sched_setscheduler(0, SCHED_FIFO, &param);
}

static void action_elixir_getpgrp(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ELIXIR) != 0) _exit(2);
    (void)getpgrp();
}

static void action_elixir_getresuid(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ELIXIR) != 0) _exit(2);
    uid_t ruid, euid, suid;
    if (getresuid(&ruid, &euid, &suid) != 0) _exit(3);
}

static void action_elixir_getresgid(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ELIXIR) != 0) _exit(2);
    gid_t rgid, egid, sgid;
    if (getresgid(&rgid, &egid, &sgid) != 0) _exit(3);
}

static void action_elixir_wait4(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ELIXIR) != 0) _exit(2);
    int status;
    (void)wait4(-1, &status, WNOHANG, NULL);
}

static void action_elixir_sched_getaffinity(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ELIXIR) != 0) _exit(2);
    cpu_set_t set;
    if (sched_getaffinity(0, sizeof(set), &set) != 0) _exit(3);
}

static void action_elixir_epoll_create(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ELIXIR) != 0) _exit(2);
    int epfd = (int)syscall(SYS_epoll_create, 1);
    if (epfd < 0) _exit(3);
    close(epfd);
}

static void action_elixir_epoll_create1(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ELIXIR) != 0) _exit(2);
    int epfd = (int)syscall(SYS_epoll_create1, 0);
    if (epfd < 0) _exit(3);
    close(epfd);
}

static void action_elixir_epoll_ctl(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ELIXIR) != 0) _exit(2);
    int epfd = (int)syscall(SYS_epoll_create1, 0);
    if (epfd < 0) _exit(3);
    int p[2];
    if (pipe(p) != 0) { close(epfd); _exit(3); }
    struct epoll_event ev;
    ev.events = EPOLLIN;
    ev.data.fd = p[0];
    int rc = (int)syscall(SYS_epoll_ctl, epfd, EPOLL_CTL_ADD, p[0], &ev);
    close(p[0]); close(p[1]); close(epfd);
    if (rc != 0) _exit(4);
}

static void action_elixir_epoll_wait(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ELIXIR) != 0) _exit(2);
    int epfd = (int)syscall(SYS_epoll_create1, 0);
    if (epfd < 0) _exit(3);
    struct epoll_event ev;
    int nfds = (int)syscall(SYS_epoll_wait, epfd, &ev, 1, 0);
    close(epfd);
    if (nfds < 0) _exit(4);
}

static void action_elixir_epoll_pwait(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ELIXIR) != 0) _exit(2);
    int epfd = (int)syscall(SYS_epoll_create1, 0);
    if (epfd < 0) _exit(3);
    struct epoll_event ev;
    int nfds = (int)syscall(SYS_epoll_pwait, epfd, &ev, 1, 0, NULL);
    close(epfd);
    if (nfds < 0) _exit(4);
}

static void action_elixir_epoll_pwait2(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ELIXIR) != 0) _exit(2);
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

static void action_elixir_poll(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ELIXIR) != 0) _exit(2);
    int p[2];
    if (pipe(p) != 0) _exit(3);
    struct pollfd pfd = {.fd = p[0], .events = POLLIN, .revents = 0};
    int rc = poll(&pfd, 1, 0);
    close(p[0]); close(p[1]);
    if (rc < 0) _exit(4);
}

static void action_elixir_ppoll(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ELIXIR) != 0) _exit(2);
    int p[2];
    if (pipe(p) != 0) _exit(3);
    struct pollfd pfd = {.fd = p[0], .events = POLLIN, .revents = 0};
    struct timespec ts = {0, 0};
    int rc = ppoll(&pfd, 1, &ts, NULL);
    close(p[0]); close(p[1]);
    if (rc < 0) _exit(4);
}

static void action_elixir_timerfd_create(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ELIXIR) != 0) _exit(2);
    int tfd = (int)syscall(SYS_timerfd_create, CLOCK_MONOTONIC, 0);
    if (tfd < 0) _exit(3);
    close(tfd);
}

static void action_elixir_timerfd_settime(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ELIXIR) != 0) _exit(2);
    int tfd = (int)syscall(SYS_timerfd_create, CLOCK_MONOTONIC, 0);
    if (tfd < 0) _exit(3);
    struct itimerspec its;
    memset(&its, 0, sizeof(its));
    its.it_value.tv_sec = 1;
    int rc = (int)syscall(SYS_timerfd_settime, tfd, 0, &its, NULL);
    close(tfd);
    if (rc != 0) _exit(4);
}

static void action_elixir_timerfd_gettime(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ELIXIR) != 0) _exit(2);
    int tfd = (int)syscall(SYS_timerfd_create, CLOCK_MONOTONIC, 0);
    if (tfd < 0) _exit(3);
    struct itimerspec its;
    int rc = (int)syscall(SYS_timerfd_gettime, tfd, &its);
    close(tfd);
    if (rc != 0) _exit(4);
}

static void action_elixir_chdir(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ELIXIR) != 0) _exit(2);
    if (chdir(".") != 0) _exit(3);
}

static void action_elixir_memfd_create(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ELIXIR) != 0) _exit(2);
    int mfd = (int)syscall(SYS_memfd_create, "elixir_jit", 0);
    if (mfd < 0) _exit(3);
    close(mfd);
}

static void action_elixir_ftruncate(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ELIXIR) != 0) _exit(2);
    int mfd = (int)syscall(SYS_memfd_create, "elixir_jit", 0);
    if (mfd < 0) _exit(3);
    int rc = ftruncate(mfd, 4096);
    close(mfd);
    if (rc != 0) _exit(4);
}

static void action_elixir_setsid(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ELIXIR) != 0) _exit(2);
    (void)setsid();
}

static void action_elixir_pselect6(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ELIXIR) != 0) _exit(2);
    struct timespec ts = {0, 0};
    int rc = (int)syscall(SYS_pselect6, 0, NULL, NULL, NULL, &ts, NULL);
    if (rc < 0 && errno != EINTR) _exit(3);
}

static void action_elixir_close_range(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ELIXIR) != 0) _exit(2);
    (void)syscall(SYS_close_range, 100, 101, 0);
}

static void action_elixir_socketpair_unix(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ELIXIR) != 0) _exit(2);
    int sv[2];
    if (socketpair(AF_UNIX, SOCK_STREAM, 0, sv) != 0) _exit(3);
    close(sv[0]);
    close(sv[1]);
}

static void action_elixir_socketpair_non_unix_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ELIXIR) != 0) _exit(2);
    int sv[2];
    socketpair(AF_INET, SOCK_STREAM, 0, sv);
}

static void action_elixir_socket_returns_eperm(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ELIXIR) != 0) _exit(2);
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd == -1 && errno == EPERM) _exit(0);
    _exit(3);
}

static void action_elixir_connect_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ELIXIR) != 0) _exit(2);
    connect(-1, NULL, 0);
}

static void action_elixir_bind_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ELIXIR) != 0) _exit(2);
    bind(-1, NULL, 0);
}

static void action_elixir_setfsuid_zero(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ELIXIR) != 0) _exit(2);
    setfsuid(0);
}

static void action_elixir_setfsuid_nonzero_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ELIXIR) != 0) _exit(2);
    setfsuid(1000);
}

static void action_elixir_setfsgid_zero(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ELIXIR) != 0) _exit(2);
    setfsgid(0);
}

static void action_elixir_setfsgid_nonzero_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ELIXIR) != 0) _exit(2);
    setfsgid(1000);
}

static void action_elixir_sendmsg_recvmsg(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ELIXIR) != 0) _exit(2);
    int sv[2];
    if (socketpair(AF_UNIX, SOCK_STREAM, 0, sv) != 0) _exit(3);
    char buf[] = "x";
    struct iovec iov = {.iov_base = buf, .iov_len = 1};
    struct msghdr msg;
    memset(&msg, 0, sizeof(msg));
    msg.msg_iov = &iov;
    msg.msg_iovlen = 1;
    if (sendmsg(sv[0], &msg, 0) < 0) { close(sv[0]); close(sv[1]); _exit(4); }
    char rbuf[1];
    struct iovec riov = {.iov_base = rbuf, .iov_len = 1};
    struct msghdr rmsg;
    memset(&rmsg, 0, sizeof(rmsg));
    rmsg.msg_iov = &riov;
    rmsg.msg_iovlen = 1;
    if (recvmsg(sv[1], &rmsg, 0) < 0) { close(sv[0]); close(sv[1]); _exit(5); }
    close(sv[0]);
    close(sv[1]);
}

static void action_elixir_getsockopt_setsockopt(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ELIXIR) != 0) _exit(2);
    int sv[2];
    if (socketpair(AF_UNIX, SOCK_STREAM, 0, sv) != 0) _exit(3);
    int val = 1;
    if (setsockopt(sv[0], SOL_SOCKET, SO_KEEPALIVE, &val, sizeof(val)) != 0) {
        close(sv[0]); close(sv[1]); _exit(4);
    }
    int gval = 0;
    socklen_t glen = sizeof(gval);
    if (getsockopt(sv[0], SOL_SOCKET, SO_KEEPALIVE, &gval, &glen) != 0) {
        close(sv[0]); close(sv[1]); _exit(5);
    }
    close(sv[0]);
    close(sv[1]);
}

static void action_elixir_prctl_name(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ELIXIR) != 0) _exit(2);
    if (prctl(PR_SET_NAME, "beam-worker") != 0) _exit(3);
}

static void action_elixir_prctl_get_name(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ELIXIR) != 0) _exit(2);
    char name[16];
    if (prctl(PR_GET_NAME, name) != 0) _exit(3);
}

static void action_elixir_prctl_dumpable_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ELIXIR) != 0) _exit(2);
    prctl(PR_SET_DUMPABLE, 1);
}

static void action_elixir_clone_user_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ELIXIR) != 0) _exit(2);
    syscall(SYS_clone, (uintptr_t)CLONE_NEWUSER, 0, 0, 0, 0);
}

static void action_elixir_unshare_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ELIXIR) != 0) _exit(2);
    unshare(CLONE_NEWUSER);
}

static void action_elixir_capset_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ELIXIR) != 0) _exit(2);
    struct __user_cap_header_struct hdr = {_LINUX_CAPABILITY_VERSION_3, 0};
    struct __user_cap_data_struct data[2];
    memset(data, 0, sizeof(data));
    syscall(SYS_capset, &hdr, data);
}

static void action_elixir_sched_setscheduler_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_ELIXIR) != 0) _exit(2);
    struct sched_param param = {0};
    sched_setscheduler(0, SCHED_FIFO, &param);
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

    /* Test 79: sched_getaffinity permitted under Dart profile */
    if (expect_success(action_dart_sched_getaffinity)) {
        printf("  [PASS] SECCOMP_PROFILE_DART permits sched_getaffinity without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_DART failed on sched_getaffinity!\n");
        return 1;
    }

    /* Test 80: eventfd2 permitted under Dart profile */
    if (expect_success(action_dart_eventfd2)) {
        printf("  [PASS] SECCOMP_PROFILE_DART permits eventfd2 without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_DART failed on eventfd2!\n");
        return 1;
    }

    /* Test 81: epoll_create1 permitted under Dart profile */
    if (expect_success(action_dart_epoll_create1)) {
        printf("  [PASS] SECCOMP_PROFILE_DART permits epoll_create1 without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_DART failed on epoll_create1!\n");
        return 1;
    }

    /* Test 82: epoll_ctl permitted under Dart profile */
    if (expect_success(action_dart_epoll_ctl)) {
        printf("  [PASS] SECCOMP_PROFILE_DART permits epoll_ctl without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_DART failed on epoll_ctl!\n");
        return 1;
    }

    /* Test 83: epoll_wait permitted under Dart profile */
    if (expect_success(action_dart_epoll_wait)) {
        printf("  [PASS] SECCOMP_PROFILE_DART permits epoll_wait without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_DART failed on epoll_wait!\n");
        return 1;
    }

    /* Test 84: epoll_pwait permitted under Dart profile */
    if (expect_success(action_dart_epoll_pwait)) {
        printf("  [PASS] SECCOMP_PROFILE_DART permits epoll_pwait without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_DART failed on epoll_pwait!\n");
        return 1;
    }

    /* Test 85: epoll_pwait2 permitted under Dart profile */
    if (expect_success(action_dart_epoll_pwait2)) {
        printf("  [PASS] SECCOMP_PROFILE_DART permits epoll_pwait2 without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_DART failed on epoll_pwait2!\n");
        return 1;
    }

    /* Test 86: timerfd_create permitted under Dart profile */
    if (expect_success(action_dart_timerfd_create)) {
        printf("  [PASS] SECCOMP_PROFILE_DART permits timerfd_create without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_DART failed on timerfd_create!\n");
        return 1;
    }

    /* Test 87: timerfd_settime permitted under Dart profile */
    if (expect_success(action_dart_timerfd_settime)) {
        printf("  [PASS] SECCOMP_PROFILE_DART permits timerfd_settime without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_DART failed on timerfd_settime!\n");
        return 1;
    }

    /* Test 88: timerfd_gettime permitted under Dart profile */
    if (expect_success(action_dart_timerfd_gettime)) {
        printf("  [PASS] SECCOMP_PROFILE_DART permits timerfd_gettime without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_DART failed on timerfd_gettime!\n");
        return 1;
    }

    /* Test 89: prctl(PR_SET_NAME) permitted under Dart profile */
    if (expect_success(action_dart_prctl_name)) {
        printf("  [PASS] SECCOMP_PROFILE_DART permits prctl(PR_SET_NAME) without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_DART failed on prctl(PR_SET_NAME)!\n");
        return 1;
    }

    /* Test 90: prctl(PR_GET_NAME) permitted under Dart profile */
    if (expect_success(action_dart_prctl_get_name)) {
        printf("  [PASS] SECCOMP_PROFILE_DART permits prctl(PR_GET_NAME) without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_DART failed on prctl(PR_GET_NAME)!\n");
        return 1;
    }

    /* Test 91: prctl(PR_SET_VMA, PR_SET_VMA_ANON_NAME) permitted under Dart profile */
    if (expect_success(action_dart_prctl_vma_anon)) {
        printf("  [PASS] SECCOMP_PROFILE_DART permits prctl(PR_SET_VMA, PR_SET_VMA_ANON_NAME) without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_DART failed on prctl(PR_SET_VMA, PR_SET_VMA_ANON_NAME)!\n");
        return 1;
    }

    /* Test 92: prctl(PR_SET_DUMPABLE) blocked with SIGSYS under Dart profile */
    if (expect_sigsys(action_dart_prctl_dumpable_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_DART kills prctl(PR_SET_DUMPABLE) with SIGSYS (dumpability locked)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_DART permitted prctl(PR_SET_DUMPABLE)!\n");
        return 1;
    }

    /* Test 93: socket() blocked with SIGSYS under Dart profile */
    if (expect_sigsys(action_dart_socket_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_DART kills socket() with SIGSYS (network denied)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_DART permitted socket()!\n");
        return 1;
    }

    /* Test 94: CLONE_NEWUSER blocked with SIGSYS under Dart profile */
    if (expect_sigsys(action_dart_clone_user_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_DART kills clone(CLONE_NEWUSER) with SIGSYS (namespaces denied)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_DART permitted CLONE_NEWUSER!\n");
        return 1;
    }

    /* Test 95: unshare blocked with SIGSYS under Dart profile */
    if (expect_sigsys(action_dart_unshare_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_DART kills unshare() with SIGSYS (namespaces denied)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_DART permitted unshare()!\n");
        return 1;
    }

    /* Test 96: capset blocked with SIGSYS under Dart profile */
    if (expect_sigsys(action_dart_capset_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_DART kills capset() with SIGSYS (privilege modification locked)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_DART permitted capset()!\n");
        return 1;
    }

    /* Test 97: sched_getaffinity permitted under Racket profile */
    if (expect_success(action_racket_sched_getaffinity)) {
        printf("  [PASS] SECCOMP_PROFILE_RACKET permits sched_getaffinity without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_RACKET failed on sched_getaffinity!\n");
        return 1;
    }

    /* Test 98: epoll_create permitted under Racket profile */
    if (expect_success(action_racket_epoll_create)) {
        printf("  [PASS] SECCOMP_PROFILE_RACKET permits epoll_create without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_RACKET failed on epoll_create!\n");
        return 1;
    }

    /* Test 99: epoll_create1 permitted under Racket profile */
    if (expect_success(action_racket_epoll_create1)) {
        printf("  [PASS] SECCOMP_PROFILE_RACKET permits epoll_create1 without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_RACKET failed on epoll_create1!\n");
        return 1;
    }

    /* Test 100: epoll_ctl permitted under Racket profile */
    if (expect_success(action_racket_epoll_ctl)) {
        printf("  [PASS] SECCOMP_PROFILE_RACKET permits epoll_ctl without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_RACKET failed on epoll_ctl!\n");
        return 1;
    }

    /* Test 101: epoll_wait permitted under Racket profile */
    if (expect_success(action_racket_epoll_wait)) {
        printf("  [PASS] SECCOMP_PROFILE_RACKET permits epoll_wait without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_RACKET failed on epoll_wait!\n");
        return 1;
    }

    /* Test 102: epoll_pwait permitted under Racket profile */
    if (expect_success(action_racket_epoll_pwait)) {
        printf("  [PASS] SECCOMP_PROFILE_RACKET permits epoll_pwait without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_RACKET failed on epoll_pwait!\n");
        return 1;
    }

    /* Test 103: epoll_pwait2 permitted under Racket profile */
    if (expect_success(action_racket_epoll_pwait2)) {
        printf("  [PASS] SECCOMP_PROFILE_RACKET permits epoll_pwait2 without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_RACKET failed on epoll_pwait2!\n");
        return 1;
    }

    /* Test 104: poll permitted under Racket profile */
    if (expect_success(action_racket_poll)) {
        printf("  [PASS] SECCOMP_PROFILE_RACKET permits poll without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_RACKET failed on poll!\n");
        return 1;
    }

    /* Test 105: prctl(PR_SET_NAME) permitted under Racket profile */
    if (expect_success(action_racket_prctl_name)) {
        printf("  [PASS] SECCOMP_PROFILE_RACKET permits prctl(PR_SET_NAME) without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_RACKET failed on prctl(PR_SET_NAME)!\n");
        return 1;
    }

    /* Test 106: prctl(PR_GET_NAME) permitted under Racket profile */
    if (expect_success(action_racket_prctl_get_name)) {
        printf("  [PASS] SECCOMP_PROFILE_RACKET permits prctl(PR_GET_NAME) without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_RACKET failed on prctl(PR_GET_NAME)!\n");
        return 1;
    }

    /* Test 107: prctl(PR_SET_DUMPABLE) blocked with SIGSYS under Racket profile */
    if (expect_sigsys(action_racket_prctl_dumpable_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_RACKET kills prctl(PR_SET_DUMPABLE) with SIGSYS (dumpability locked)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_RACKET permitted prctl(PR_SET_DUMPABLE)!\n");
        return 1;
    }

    /* Test 108: socket() blocked with SIGSYS under Racket profile */
    if (expect_sigsys(action_racket_socket_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_RACKET kills socket() with SIGSYS (network denied)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_RACKET permitted socket()!\n");
        return 1;
    }

    /* Test 109: CLONE_NEWUSER blocked with SIGSYS under Racket profile */
    if (expect_sigsys(action_racket_clone_user_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_RACKET kills clone(CLONE_NEWUSER) with SIGSYS (namespaces denied)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_RACKET permitted CLONE_NEWUSER!\n");
        return 1;
    }

    /* Test 110: unshare blocked with SIGSYS under Racket profile */
    if (expect_sigsys(action_racket_unshare_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_RACKET kills unshare() with SIGSYS (namespaces denied)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_RACKET permitted unshare()!\n");
        return 1;
    }

    /* Test 111: capset blocked with SIGSYS under Racket profile */
    if (expect_sigsys(action_racket_capset_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_RACKET kills capset() with SIGSYS (privilege modification locked)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_RACKET permitted capset()!\n");
        return 1;
    }

    /* Test 112: sched_setscheduler blocked with SIGSYS under Racket profile */
    if (expect_sigsys(action_racket_sched_setscheduler_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_RACKET kills sched_setscheduler() with SIGSYS (scheduler changes denied)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_RACKET permitted sched_setscheduler()!\n");
        return 1;
    }

    /* Test 113: getpgrp permitted under Erlang profile */
    if (expect_success(action_erlang_getpgrp)) {
        printf("  [PASS] SECCOMP_PROFILE_ERLANG permits getpgrp without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ERLANG failed on getpgrp!\n");
        return 1;
    }

    /* Test 114: getresuid permitted under Erlang profile */
    if (expect_success(action_erlang_getresuid)) {
        printf("  [PASS] SECCOMP_PROFILE_ERLANG permits getresuid without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ERLANG failed on getresuid!\n");
        return 1;
    }

    /* Test 115: getresgid permitted under Erlang profile */
    if (expect_success(action_erlang_getresgid)) {
        printf("  [PASS] SECCOMP_PROFILE_ERLANG permits getresgid without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ERLANG failed on getresgid!\n");
        return 1;
    }

    /* Test 116: wait4 permitted under Erlang profile */
    if (expect_success(action_erlang_wait4)) {
        printf("  [PASS] SECCOMP_PROFILE_ERLANG permits wait4 without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ERLANG failed on wait4!\n");
        return 1;
    }

    /* Test 117: sched_getaffinity permitted under Erlang profile */
    if (expect_success(action_erlang_sched_getaffinity)) {
        printf("  [PASS] SECCOMP_PROFILE_ERLANG permits sched_getaffinity without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ERLANG failed on sched_getaffinity!\n");
        return 1;
    }

    /* Test 118: epoll_create permitted under Erlang profile */
    if (expect_success(action_erlang_epoll_create)) {
        printf("  [PASS] SECCOMP_PROFILE_ERLANG permits epoll_create without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ERLANG failed on epoll_create!\n");
        return 1;
    }

    /* Test 119: epoll_create1 permitted under Erlang profile */
    if (expect_success(action_erlang_epoll_create1)) {
        printf("  [PASS] SECCOMP_PROFILE_ERLANG permits epoll_create1 without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ERLANG failed on epoll_create1!\n");
        return 1;
    }

    /* Test 120: epoll_ctl permitted under Erlang profile */
    if (expect_success(action_erlang_epoll_ctl)) {
        printf("  [PASS] SECCOMP_PROFILE_ERLANG permits epoll_ctl without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ERLANG failed on epoll_ctl!\n");
        return 1;
    }

    /* Test 121: epoll_wait permitted under Erlang profile */
    if (expect_success(action_erlang_epoll_wait)) {
        printf("  [PASS] SECCOMP_PROFILE_ERLANG permits epoll_wait without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ERLANG failed on epoll_wait!\n");
        return 1;
    }

    /* Test 122: epoll_pwait permitted under Erlang profile */
    if (expect_success(action_erlang_epoll_pwait)) {
        printf("  [PASS] SECCOMP_PROFILE_ERLANG permits epoll_pwait without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ERLANG failed on epoll_pwait!\n");
        return 1;
    }

    /* Test 123: epoll_pwait2 permitted under Erlang profile */
    if (expect_success(action_erlang_epoll_pwait2)) {
        printf("  [PASS] SECCOMP_PROFILE_ERLANG permits epoll_pwait2 without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ERLANG failed on epoll_pwait2!\n");
        return 1;
    }

    /* Test 124: poll permitted under Erlang profile */
    if (expect_success(action_erlang_poll)) {
        printf("  [PASS] SECCOMP_PROFILE_ERLANG permits poll without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ERLANG failed on poll!\n");
        return 1;
    }

    /* Test 125: ppoll permitted under Erlang profile */
    if (expect_success(action_erlang_ppoll)) {
        printf("  [PASS] SECCOMP_PROFILE_ERLANG permits ppoll without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ERLANG failed on ppoll!\n");
        return 1;
    }

    /* Test 126: timerfd_create permitted under Erlang profile */
    if (expect_success(action_erlang_timerfd_create)) {
        printf("  [PASS] SECCOMP_PROFILE_ERLANG permits timerfd_create without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ERLANG failed on timerfd_create!\n");
        return 1;
    }

    /* Test 127: timerfd_settime permitted under Erlang profile */
    if (expect_success(action_erlang_timerfd_settime)) {
        printf("  [PASS] SECCOMP_PROFILE_ERLANG permits timerfd_settime without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ERLANG failed on timerfd_settime!\n");
        return 1;
    }

    /* Test 128: timerfd_gettime permitted under Erlang profile */
    if (expect_success(action_erlang_timerfd_gettime)) {
        printf("  [PASS] SECCOMP_PROFILE_ERLANG permits timerfd_gettime without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ERLANG failed on timerfd_gettime!\n");
        return 1;
    }

    /* Test 129: prctl(PR_SET_NAME) permitted under Erlang profile */
    if (expect_success(action_erlang_prctl_name)) {
        printf("  [PASS] SECCOMP_PROFILE_ERLANG permits prctl(PR_SET_NAME) without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ERLANG failed on prctl(PR_SET_NAME)!\n");
        return 1;
    }

    /* Test 130: prctl(PR_GET_NAME) permitted under Erlang profile */
    if (expect_success(action_erlang_prctl_get_name)) {
        printf("  [PASS] SECCOMP_PROFILE_ERLANG permits prctl(PR_GET_NAME) without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ERLANG failed on prctl(PR_GET_NAME)!\n");
        return 1;
    }

    /* Test 131: prctl(PR_SET_DUMPABLE) blocked with SIGSYS under Erlang profile */
    if (expect_sigsys(action_erlang_prctl_dumpable_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_ERLANG kills prctl(PR_SET_DUMPABLE) with SIGSYS (dumpability locked)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ERLANG permitted prctl(PR_SET_DUMPABLE)!\n");
        return 1;
    }

    /* Test 132: chdir permitted under Erlang profile */
    if (expect_success(action_erlang_chdir)) {
        printf("  [PASS] SECCOMP_PROFILE_ERLANG permits chdir without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ERLANG failed on chdir!\n");
        return 1;
    }

    /* Test 133: memfd_create permitted under Erlang profile */
    if (expect_success(action_erlang_memfd_create)) {
        printf("  [PASS] SECCOMP_PROFILE_ERLANG permits memfd_create without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ERLANG failed on memfd_create!\n");
        return 1;
    }

    /* Test 134: ftruncate permitted under Erlang profile */
    if (expect_success(action_erlang_ftruncate)) {
        printf("  [PASS] SECCOMP_PROFILE_ERLANG permits ftruncate without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ERLANG failed on ftruncate!\n");
        return 1;
    }

    /* Test 135: setsid permitted under Erlang profile */
    if (expect_success(action_erlang_setsid)) {
        printf("  [PASS] SECCOMP_PROFILE_ERLANG permits setsid without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ERLANG failed on setsid!\n");
        return 1;
    }

    /* Test 136: pselect6 permitted under Erlang profile */
    if (expect_success(action_erlang_pselect6)) {
        printf("  [PASS] SECCOMP_PROFILE_ERLANG permits pselect6 without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ERLANG failed on pselect6!\n");
        return 1;
    }

    /* Test 137: close_range permitted under Erlang profile */
    if (expect_success(action_erlang_close_range)) {
        printf("  [PASS] SECCOMP_PROFILE_ERLANG permits close_range without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ERLANG failed on close_range!\n");
        return 1;
    }

    /* Test 138: socketpair(AF_UNIX) permitted under Erlang profile */
    if (expect_success(action_erlang_socketpair_unix)) {
        printf("  [PASS] SECCOMP_PROFILE_ERLANG permits socketpair(AF_UNIX) without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ERLANG failed on socketpair(AF_UNIX)!\n");
        return 1;
    }

    /* Test 139: socketpair(AF_INET) killed with SIGSYS under Erlang profile */
    if (expect_sigsys(action_erlang_socketpair_inet_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_ERLANG kills socketpair(AF_INET) with SIGSYS (AF_UNIX only)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ERLANG permitted socketpair(AF_INET)!\n");
        return 1;
    }

    /* Test 140: socket() returns EPERM without crashing under Erlang profile */
    if (expect_success(action_erlang_socket_returns_eperm)) {
        printf("  [PASS] SECCOMP_PROFILE_ERLANG returns EPERM on socket() without crashing\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ERLANG socket() did not return EPERM!\n");
        return 1;
    }

    /* Test 141: connect() killed with SIGSYS under Erlang profile */
    if (expect_sigsys(action_erlang_connect_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_ERLANG kills connect() with SIGSYS (network denied)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ERLANG permitted connect()!\n");
        return 1;
    }

    /* Test 142: bind() killed with SIGSYS under Erlang profile */
    if (expect_sigsys(action_erlang_bind_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_ERLANG kills bind() with SIGSYS (network denied)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ERLANG permitted bind()!\n");
        return 1;
    }

    /* Test 143: setfsuid(0) permitted under Erlang profile */
    if (expect_success(action_erlang_setfsuid_zero)) {
        printf("  [PASS] SECCOMP_PROFILE_ERLANG permits setfsuid(0) without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ERLANG failed on setfsuid(0)!\n");
        return 1;
    }

    /* Test 144: setfsuid(1000) killed with SIGSYS under Erlang profile */
    if (expect_sigsys(action_erlang_setfsuid_nonzero_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_ERLANG kills setfsuid(non-zero) with SIGSYS (ID 0 only)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ERLANG permitted setfsuid(1000)!\n");
        return 1;
    }

    /* Test 145: setfsgid(0) permitted under Erlang profile */
    if (expect_success(action_erlang_setfsgid_zero)) {
        printf("  [PASS] SECCOMP_PROFILE_ERLANG permits setfsgid(0) without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ERLANG failed on setfsgid(0)!\n");
        return 1;
    }

    /* Test 146: setfsgid(1000) killed with SIGSYS under Erlang profile */
    if (expect_sigsys(action_erlang_setfsgid_nonzero_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_ERLANG kills setfsgid(non-zero) with SIGSYS (ID 0 only)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ERLANG permitted setfsgid(1000)!\n");
        return 1;
    }

    /* Test 147: sendmsg and recvmsg permitted under Erlang profile */
    if (expect_success(action_erlang_sendmsg_recvmsg)) {
        printf("  [PASS] SECCOMP_PROFILE_ERLANG permits sendmsg and recvmsg on domain socketpair\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ERLANG failed on sendmsg / recvmsg!\n");
        return 1;
    }

    /* Test 148: getsockopt and setsockopt permitted under Erlang profile */
    if (expect_success(action_erlang_getsockopt_setsockopt)) {
        printf("  [PASS] SECCOMP_PROFILE_ERLANG permits getsockopt and setsockopt on domain socketpair\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ERLANG failed on getsockopt / setsockopt!\n");
        return 1;
    }

    /* Test 133: CLONE_NEWUSER blocked with SIGSYS under Erlang profile */
    if (expect_sigsys(action_erlang_clone_user_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_ERLANG kills clone(CLONE_NEWUSER) with SIGSYS (namespaces denied)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ERLANG permitted CLONE_NEWUSER!\n");
        return 1;
    }

    /* Test 134: unshare blocked with SIGSYS under Erlang profile */
    if (expect_sigsys(action_erlang_unshare_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_ERLANG kills unshare() with SIGSYS (namespaces denied)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ERLANG permitted unshare()!\n");
        return 1;
    }

    /* Test 135: capset blocked with SIGSYS under Erlang profile */
    if (expect_sigsys(action_erlang_capset_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_ERLANG kills capset() with SIGSYS (privilege modification locked)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ERLANG permitted capset()!\n");
        return 1;
    }

    /* Test 136: sched_setscheduler blocked with SIGSYS under Erlang profile */
    if (expect_sigsys(action_erlang_sched_setscheduler_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_ERLANG kills sched_setscheduler() with SIGSYS (scheduler changes denied)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ERLANG permitted sched_setscheduler()!\n");
        return 1;
    }

    /* Test 137: getpgrp permitted under Elixir profile */
    if (expect_success(action_elixir_getpgrp)) {
        printf("  [PASS] SECCOMP_PROFILE_ELIXIR permits getpgrp without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ELIXIR failed on getpgrp!\n");
        return 1;
    }

    /* Test 138: getresuid permitted under Elixir profile */
    if (expect_success(action_elixir_getresuid)) {
        printf("  [PASS] SECCOMP_PROFILE_ELIXIR permits getresuid without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ELIXIR failed on getresuid!\n");
        return 1;
    }

    /* Test 139: getresgid permitted under Elixir profile */
    if (expect_success(action_elixir_getresgid)) {
        printf("  [PASS] SECCOMP_PROFILE_ELIXIR permits getresgid without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ELIXIR failed on getresgid!\n");
        return 1;
    }

    /* Test 140: wait4 permitted under Elixir profile */
    if (expect_success(action_elixir_wait4)) {
        printf("  [PASS] SECCOMP_PROFILE_ELIXIR permits wait4 without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ELIXIR failed on wait4!\n");
        return 1;
    }

    /* Test 141: sched_getaffinity permitted under Elixir profile */
    if (expect_success(action_elixir_sched_getaffinity)) {
        printf("  [PASS] SECCOMP_PROFILE_ELIXIR permits sched_getaffinity without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ELIXIR failed on sched_getaffinity!\n");
        return 1;
    }

    /* Test 142: epoll_create permitted under Elixir profile */
    if (expect_success(action_elixir_epoll_create)) {
        printf("  [PASS] SECCOMP_PROFILE_ELIXIR permits epoll_create without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ELIXIR failed on epoll_create!\n");
        return 1;
    }

    /* Test 143: epoll_create1 permitted under Elixir profile */
    if (expect_success(action_elixir_epoll_create1)) {
        printf("  [PASS] SECCOMP_PROFILE_ELIXIR permits epoll_create1 without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ELIXIR failed on epoll_create1!\n");
        return 1;
    }

    /* Test 144: epoll_ctl permitted under Elixir profile */
    if (expect_success(action_elixir_epoll_ctl)) {
        printf("  [PASS] SECCOMP_PROFILE_ELIXIR permits epoll_ctl without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ELIXIR failed on epoll_ctl!\n");
        return 1;
    }

    /* Test 145: epoll_wait permitted under Elixir profile */
    if (expect_success(action_elixir_epoll_wait)) {
        printf("  [PASS] SECCOMP_PROFILE_ELIXIR permits epoll_wait without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ELIXIR failed on epoll_wait!\n");
        return 1;
    }

    /* Test 146: epoll_pwait permitted under Elixir profile */
    if (expect_success(action_elixir_epoll_pwait)) {
        printf("  [PASS] SECCOMP_PROFILE_ELIXIR permits epoll_pwait without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ELIXIR failed on epoll_pwait!\n");
        return 1;
    }

    /* Test 147: epoll_pwait2 permitted under Elixir profile */
    if (expect_success(action_elixir_epoll_pwait2)) {
        printf("  [PASS] SECCOMP_PROFILE_ELIXIR permits epoll_pwait2 without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ELIXIR failed on epoll_pwait2!\n");
        return 1;
    }

    /* Test 148: poll permitted under Elixir profile */
    if (expect_success(action_elixir_poll)) {
        printf("  [PASS] SECCOMP_PROFILE_ELIXIR permits poll without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ELIXIR failed on poll!\n");
        return 1;
    }

    /* Test 149: ppoll permitted under Elixir profile */
    if (expect_success(action_elixir_ppoll)) {
        printf("  [PASS] SECCOMP_PROFILE_ELIXIR permits ppoll without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ELIXIR failed on ppoll!\n");
        return 1;
    }

    /* Test 150: timerfd_create permitted under Elixir profile */
    if (expect_success(action_elixir_timerfd_create)) {
        printf("  [PASS] SECCOMP_PROFILE_ELIXIR permits timerfd_create without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ELIXIR failed on timerfd_create!\n");
        return 1;
    }

    /* Test 151: timerfd_settime permitted under Elixir profile */
    if (expect_success(action_elixir_timerfd_settime)) {
        printf("  [PASS] SECCOMP_PROFILE_ELIXIR permits timerfd_settime without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ELIXIR failed on timerfd_settime!\n");
        return 1;
    }

    /* Test 152: timerfd_gettime permitted under Elixir profile */
    if (expect_success(action_elixir_timerfd_gettime)) {
        printf("  [PASS] SECCOMP_PROFILE_ELIXIR permits timerfd_gettime without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ELIXIR failed on timerfd_gettime!\n");
        return 1;
    }

    /* Test 153: chdir permitted under Elixir profile */
    if (expect_success(action_elixir_chdir)) {
        printf("  [PASS] SECCOMP_PROFILE_ELIXIR permits chdir without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ELIXIR failed on chdir!\n");
        return 1;
    }

    /* Test 154: memfd_create permitted under Elixir profile */
    if (expect_success(action_elixir_memfd_create)) {
        printf("  [PASS] SECCOMP_PROFILE_ELIXIR permits memfd_create without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ELIXIR failed on memfd_create!\n");
        return 1;
    }

    /* Test 155: ftruncate permitted under Elixir profile */
    if (expect_success(action_elixir_ftruncate)) {
        printf("  [PASS] SECCOMP_PROFILE_ELIXIR permits ftruncate without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ELIXIR failed on ftruncate!\n");
        return 1;
    }

    /* Test 156: setsid permitted under Elixir profile */
    if (expect_success(action_elixir_setsid)) {
        printf("  [PASS] SECCOMP_PROFILE_ELIXIR permits setsid without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ELIXIR failed on setsid!\n");
        return 1;
    }

    /* Test 157: pselect6 permitted under Elixir profile */
    if (expect_success(action_elixir_pselect6)) {
        printf("  [PASS] SECCOMP_PROFILE_ELIXIR permits pselect6 without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ELIXIR failed on pselect6!\n");
        return 1;
    }

    /* Test 158: close_range permitted under Elixir profile */
    if (expect_success(action_elixir_close_range)) {
        printf("  [PASS] SECCOMP_PROFILE_ELIXIR permits close_range without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ELIXIR failed on close_range!\n");
        return 1;
    }

    /* Test 159: socketpair(AF_UNIX) permitted under Elixir profile */
    if (expect_success(action_elixir_socketpair_unix)) {
        printf("  [PASS] SECCOMP_PROFILE_ELIXIR permits socketpair(AF_UNIX) without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ELIXIR failed on socketpair(AF_UNIX)!\n");
        return 1;
    }

    /* Test 160: socketpair(AF_INET) killed with SIGSYS under Elixir profile */
    if (expect_sigsys(action_elixir_socketpair_non_unix_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_ELIXIR kills socketpair(AF_INET) with SIGSYS (AF_UNIX only)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ELIXIR permitted socketpair(AF_INET)!\n");
        return 1;
    }

    /* Test 161: socket() returns EPERM under Elixir profile */
    if (expect_success(action_elixir_socket_returns_eperm)) {
        printf("  [PASS] SECCOMP_PROFILE_ELIXIR returns EPERM on socket() without crashing\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ELIXIR did not return EPERM on socket()!\n");
        return 1;
    }

    /* Test 162: connect() blocked with SIGSYS under Elixir profile */
    if (expect_sigsys(action_elixir_connect_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_ELIXIR kills connect() with SIGSYS (network denied)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ELIXIR permitted connect()!\n");
        return 1;
    }

    /* Test 163: bind() blocked with SIGSYS under Elixir profile */
    if (expect_sigsys(action_elixir_bind_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_ELIXIR kills bind() with SIGSYS (network denied)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ELIXIR permitted bind()!\n");
        return 1;
    }

    /* Test 164: setfsuid(0) permitted under Elixir profile */
    if (expect_success(action_elixir_setfsuid_zero)) {
        printf("  [PASS] SECCOMP_PROFILE_ELIXIR permits setfsuid(0) without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ELIXIR failed on setfsuid(0)!\n");
        return 1;
    }

    /* Test 165: setfsuid(1000) killed with SIGSYS under Elixir profile */
    if (expect_sigsys(action_elixir_setfsuid_nonzero_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_ELIXIR kills setfsuid(non-zero) with SIGSYS (ID 0 only)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ELIXIR permitted setfsuid(1000)!\n");
        return 1;
    }

    /* Test 166: setfsgid(0) permitted under Elixir profile */
    if (expect_success(action_elixir_setfsgid_zero)) {
        printf("  [PASS] SECCOMP_PROFILE_ELIXIR permits setfsgid(0) without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ELIXIR failed on setfsgid(0)!\n");
        return 1;
    }

    /* Test 167: setfsgid(1000) killed with SIGSYS under Elixir profile */
    if (expect_sigsys(action_elixir_setfsgid_nonzero_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_ELIXIR kills setfsgid(non-zero) with SIGSYS (ID 0 only)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ELIXIR permitted setfsgid(1000)!\n");
        return 1;
    }

    /* Test 168: sendmsg and recvmsg permitted under Elixir profile */
    if (expect_success(action_elixir_sendmsg_recvmsg)) {
        printf("  [PASS] SECCOMP_PROFILE_ELIXIR permits sendmsg and recvmsg on domain socketpair\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ELIXIR failed on sendmsg / recvmsg!\n");
        return 1;
    }

    /* Test 169: getsockopt and setsockopt permitted under Elixir profile */
    if (expect_success(action_elixir_getsockopt_setsockopt)) {
        printf("  [PASS] SECCOMP_PROFILE_ELIXIR permits getsockopt and setsockopt on domain socketpair\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ELIXIR failed on getsockopt / setsockopt!\n");
        return 1;
    }

    /* Test 170: prctl(PR_SET_NAME) permitted under Elixir profile */
    if (expect_success(action_elixir_prctl_name)) {
        printf("  [PASS] SECCOMP_PROFILE_ELIXIR permits prctl(PR_SET_NAME) without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ELIXIR failed on prctl(PR_SET_NAME)!\n");
        return 1;
    }

    /* Test 171: prctl(PR_GET_NAME) permitted under Elixir profile */
    if (expect_success(action_elixir_prctl_get_name)) {
        printf("  [PASS] SECCOMP_PROFILE_ELIXIR permits prctl(PR_GET_NAME) without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ELIXIR failed on prctl(PR_GET_NAME)!\n");
        return 1;
    }

    /* Test 172: prctl(PR_SET_DUMPABLE) blocked with SIGSYS under Elixir profile */
    if (expect_sigsys(action_elixir_prctl_dumpable_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_ELIXIR kills prctl(PR_SET_DUMPABLE) with SIGSYS (dumpability locked)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ELIXIR permitted prctl(PR_SET_DUMPABLE)!\n");
        return 1;
    }

    /* Test 173: CLONE_NEWUSER blocked with SIGSYS under Elixir profile */
    if (expect_sigsys(action_elixir_clone_user_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_ELIXIR kills clone(CLONE_NEWUSER) with SIGSYS (namespaces denied)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ELIXIR permitted CLONE_NEWUSER!\n");
        return 1;
    }

    /* Test 174: unshare blocked with SIGSYS under Elixir profile */
    if (expect_sigsys(action_elixir_unshare_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_ELIXIR kills unshare() with SIGSYS (namespaces denied)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ELIXIR permitted unshare()!\n");
        return 1;
    }

    /* Test 175: capset blocked with SIGSYS under Elixir profile */
    if (expect_sigsys(action_elixir_capset_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_ELIXIR kills capset() with SIGSYS (privilege modification locked)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ELIXIR permitted capset()!\n");
        return 1;
    }

    /* Test 176: sched_setscheduler blocked with SIGSYS under Elixir profile */
    if (expect_sigsys(action_elixir_sched_setscheduler_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_ELIXIR kills sched_setscheduler() with SIGSYS (scheduler changes denied)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_ELIXIR permitted sched_setscheduler()!\n");
        return 1;
    }

    printf("========================================================================\n");
    printf("SECCOMP PROFILE UNIT TEST SUMMARY:\n");
    printf("  All 192 seccomp profile unit verification checks PASSED.\n");
    printf("========================================================================\n");
    return 0;
}
