#include <unistd.h>

int main(void) {
    char bytes[4096];
    for (int i = 0; i < (int)sizeof(bytes); ++i) bytes[i] = 'x';
    for (;;) {
        ssize_t n = write(STDOUT_FILENO, bytes, sizeof(bytes));
        if (n < 0) return 1;
    }
}
