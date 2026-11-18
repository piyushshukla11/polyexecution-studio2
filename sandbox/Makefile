CC ?= gcc
CFLAGS ?= -Wall -Wextra -Werror -O2 -Iinclude
LDFLAGS ?= -lseccomp

SRCS = src/runner.c src/cgroup.c src/seccomp.c
OBJS = $(SRCS:.c=.o)
TARGET = pes2-runner

.PHONY: all clean test test-runtime

all: $(TARGET)

$(TARGET): $(OBJS)
	$(CC) $(CFLAGS) -o $@ $^ $(LDFLAGS)

src/%.o: src/%.c include/runner.h
	$(CC) $(CFLAGS) -c $< -o $@

test: $(TARGET)
	$(CC) $(CFLAGS) src/seccomp.c tests/test_seccomp.c -o tests/test_seccomp $(LDFLAGS)
	./tests/test_seccomp
	@rm -f tests/test_seccomp
	bash tests/test_cgroup_discovery.sh
	bash tests/test_runner.sh

test-runtime: $(TARGET)
	$(CC) $(CFLAGS) src/seccomp.c tests/test_seccomp.c -o tests/test_seccomp $(LDFLAGS)
	./tests/test_seccomp
	@rm -f tests/test_seccomp
	bash tests/test_cgroup_discovery.sh
	PES2_REQUIRE_RUNTIME=1 bash tests/test_runner.sh

clean:
	rm -f $(OBJS) $(TARGET) tests/test_seccomp
