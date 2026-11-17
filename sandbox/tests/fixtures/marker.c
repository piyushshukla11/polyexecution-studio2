#include <fcntl.h>
#include <unistd.h>

int main(void) {
    int fd = open("/workspace/should-not-run", O_WRONLY | O_CREAT, 0600);
    if (fd < 0) return 2;
    close(fd);
    return 0;
}
