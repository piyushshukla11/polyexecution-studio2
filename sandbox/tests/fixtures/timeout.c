#include <time.h>

int main(void) {
    for (;;) {
        struct timespec delay = {.tv_sec = 0, .tv_nsec = 10000000L};
        nanosleep(&delay, 0);
    }
}
