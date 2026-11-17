#include <stdlib.h>
#include <unistd.h>

int main(void) {
    const size_t bytes = 128U * 1024U * 1024U;
    volatile unsigned char *memory = malloc(bytes);
    if (!memory) return 2;
    for (size_t offset = 0; offset < bytes; offset += 4096U) memory[offset] = 1;
    return 0;
}
