#include <fcntl.h>
#include <unistd.h>

int main(void) {
    int fd = open("/etc/passwd", O_RDONLY);
    if (fd >= 0) {
        close(fd);
        return 1;
    }
    return 0;
}
