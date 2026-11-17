CC ?= gcc
CFLAGS ?= -Wall -Wextra -Werror -O2 -Iinclude
LDFLAGS ?= -lseccomp

SRCS = src/runner.c src/cgroup.c src/seccomp.c
OBJS = $(SRCS:.c=.o)
TARGET = pes2-runner

.PHONY: all clean test

all: $(TARGET)

$(TARGET): $(OBJS)
	$(CC) $(CFLAGS) -o $@ $^ $(LDFLAGS)

src/%.o: src/%.c include/runner.h
	$(CC) $(CFLAGS) -c $< -o $@

clean:
	rm -f $(OBJS) $(TARGET)

test: $(TARGET)
	@echo "Running skeleton check..."
	./$(TARGET) /bin/true
