#include <unistd.h>

int main(void) {
    static const char message[] = "sandbox-ok\n";
    return write(STDOUT_FILENO, message, sizeof(message) - 1) == (ssize_t)(sizeof(message) - 1) ? 0 : 1;
}
