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

static void action_strict_wait4(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_STRICT) != 0) _exit(2);
    int status = 0;
    wait4(-1, &status, WNOHANG, NULL);
}

static void action_compiler_wait4(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_COMPILER) != 0) _exit(2);
    int status = 0;
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

static void action_compiler_clone_user_blocked(void) {
    if (seccomp_apply_profile(SECCOMP_PROFILE_COMPILER) != 0) _exit(2);
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

    /* Test 3: wait4 blocked under strict profile */
    if (expect_sigsys(action_strict_wait4)) {
        printf("  [PASS] SECCOMP_PROFILE_STRICT correctly kills wait4 with SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_STRICT permitted wait4!\n");
        return 1;
    }

    /* Test 4: wait4 permitted under compiler profile */
    if (expect_success(action_compiler_wait4)) {
        printf("  [PASS] SECCOMP_PROFILE_COMPILER permits wait4 without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_COMPILER failed on wait4!\n");
        return 1;
    }

    /* Test 5: network socket blocked under compiler profile */
    if (expect_sigsys(action_compiler_network_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_COMPILER kills socket() with SIGSYS (network denied)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_COMPILER permitted socket creation!\n");
        return 1;
    }

    /* Test 6: network socket blocked under strict profile */
    if (expect_sigsys(action_strict_network_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_STRICT kills socket() with SIGSYS (network denied)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_STRICT permitted socket creation!\n");
        return 1;
    }

    /* Test 7: CLONE_NEWUSER blocked under compiler profile */
    if (expect_sigsys(action_compiler_clone_user_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_COMPILER kills clone(CLONE_NEWUSER) with SIGSYS (namespaces denied)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_COMPILER permitted CLONE_NEWUSER!\n");
        return 1;
    }

    /* Test 8: unshare blocked under compiler profile */
    if (expect_sigsys(action_compiler_unshare_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_COMPILER kills unshare() with SIGSYS (namespaces denied)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_COMPILER permitted unshare()!\n");
        return 1;
    }

    /* Test 9: direct open permitted under python profile */
    if (expect_success(action_python_open)) {
        printf("  [PASS] SECCOMP_PROFILE_PYTHON permits direct open() without SIGSYS\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_PYTHON failed on open()!\n");
        return 1;
    }

    /* Test 10: network socket blocked under python profile */
    if (expect_sigsys(action_python_network_blocked)) {
        printf("  [PASS] SECCOMP_PROFILE_PYTHON kills socket() with SIGSYS (network denied)\n");
    } else {
        fprintf(stderr, "  [FAIL] SECCOMP_PROFILE_PYTHON permitted socket creation!\n");
        return 1;
    }

    printf("========================================================================\n");
    printf("SECCOMP PROFILE UNIT TEST SUMMARY:\n");
    printf("  All 10 seccomp profile unit verification checks PASSED.\n");
    printf("========================================================================\n");
    return 0;
}
