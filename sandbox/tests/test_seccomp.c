#define _GNU_SOURCE
#include "runner.h"

#include <errno.h>
#include <fcntl.h>
#include <linux/sched.h>
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

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

    printf("========================================================================\n");
    printf("SECCOMP PROFILE UNIT TEST SUMMARY:\n");
    printf("  All 22 seccomp profile unit verification checks PASSED.\n");
    printf("========================================================================\n");
    return 0;
}
