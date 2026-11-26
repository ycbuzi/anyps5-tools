#define _GNU_SOURCE
#include <fcntl.h>
#include <linux/futex.h>
#include <stdint.h>
#include <stdio.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

static double now(void)
{
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec / 1e9;
}

int main(void)
{
    const int n = 20000;
    double t;
    int word = 0;
    t = now();
    for (int i = 0; i < n; i++)
        syscall(SYS_getppid);
    printf("getppid %.2f us\n", (now() - t) / n * 1e6);
    t = now();
    for (int i = 0; i < n; i++)
        syscall(SYS_futex, &word, FUTEX_WAKE_PRIVATE, 1, 0, 0, 0);
    printf("futex_wake %.2f us\n", (now() - t) / n * 1e6);
    t = now();
    for (int i = 0; i < n; i++) {
        struct timespec ts;
        clock_gettime(CLOCK_MONOTONIC, &ts);
    }
    printf("clock_gettime %.3f us\n", (now() - t) / n * 1e6);
    t = now();
    for (int i = 0; i < n / 4; i++) {
        void* p = mmap(NULL, 65536, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        munmap(p, 65536);
    }
    printf("mmap+munmap %.2f us\n", (now() - t) / (n / 4) * 1e6);
    t = now();
    for (int i = 0; i < n / 4; i++) {
        int fd = open("/proc/self/stat", O_RDONLY);
        if (fd >= 0)
            close(fd);
    }
    printf("open+close %.2f us\n", (now() - t) / (n / 4) * 1e6);
    t = now();
    for (int i = 0; i < n / 4; i++)
        syscall(SYS_mprotect, (void*)((uintptr_t)&word & ~4095ul), 4096, PROT_READ | PROT_WRITE);
    printf("mprotect %.2f us\n", (now() - t) / (n / 4) * 1e6);
    return 0;
}
