#include <time.h>
#include <unistd.h>
#include <sys/types.h>

int main(void) {
    static const char marker[] = "PIDS_LIMIT_REACHED\n";
    for (int i = 0; i < 1024; ++i) {
        pid_t child = fork();
        if (child == 0) {
            for (;;) {
                struct timespec delay = {.tv_sec = 1, .tv_nsec = 0};
                nanosleep(&delay, 0);
            }
        }
        if (child < 0) {
            (void)write(STDOUT_FILENO, marker, sizeof(marker) - 1);
            return 0;
        }
    }
    return 3;
}
