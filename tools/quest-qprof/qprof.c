#define _GNU_SOURCE
#include <pthread.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/syscall.h>
#include <sys/time.h>
#include <time.h>
#include <ucontext.h>
#include <unistd.h>

#define MAXS (1u << 21)

static uint64_t pcs[MAXS];
static uint32_t tids[MAXS];
static uint64_t x64s[MAXS];
typedef void* (*find_fn)(void*);
typedef uintptr_t (*getx_fn)(void*, uintptr_t);
static find_fn qfind;
static getx_fn qgetx;
static atomic_uint count;
static atomic_int active;

static void onprof(int sig, siginfo_t* si, void* ctx)
{
    (void)sig;
    (void)si;
    if (!atomic_load_explicit(&active, memory_order_relaxed))
        return;
    unsigned i = atomic_fetch_add_explicit(&count, 1, memory_order_relaxed);
    if (i >= MAXS)
        return;
    ucontext_t* u = ctx;
    pcs[i] = u->uc_mcontext.pc;
    tids[i] = (uint32_t)syscall(SYS_gettid);
    x64s[i] = 0;
    if (qfind && qgetx) {
        void* db = qfind((void*)pcs[i]);
        if (db)
            x64s[i] = qgetx(db, pcs[i]);
    }
}

static void sleep_s(double s)
{
    struct timespec t0, t;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    for (;;) {
        clock_gettime(CLOCK_MONOTONIC, &t);
        double left = s - ((t.tv_sec - t0.tv_sec) + (t.tv_nsec - t0.tv_nsec) / 1e9);
        if (left <= 0)
            return;
        struct timespec r = {(time_t)left, (long)((left - (time_t)left) * 1e9)};
        nanosleep(&r, NULL);
    }
}

static void copyfile(const char* from, const char* to)
{
    FILE* a = fopen(from, "r");
    FILE* b = fopen(to, "w");
    if (a && b) {
        char buf[65536];
        size_t n;
        while ((n = fread(buf, 1, sizeof buf, a)) > 0)
            fwrite(buf, 1, n, b);
    }
    if (a)
        fclose(a);
    if (b)
        fclose(b);
}

static void* worker(void* arg)
{
    (void)arg;
    sigset_t all;
    sigfillset(&all);
    pthread_sigmask(SIG_BLOCK, &all, NULL);
    const char* out = getenv("QPROF_OUT");
    double start = getenv("QPROF_START") ? atof(getenv("QPROF_START")) : 90;
    double secs = getenv("QPROF_SECS") ? atof(getenv("QPROF_SECS")) : 60;
    long us = getenv("QPROF_US") ? atol(getenv("QPROF_US")) : 2000;
    if (getenv("QPROF_FIND") && getenv("QPROF_GETX64")) {
        qfind = (find_fn)strtoul(getenv("QPROF_FIND"), NULL, 16);
        qgetx = (getx_fn)strtoul(getenv("QPROF_GETX64"), NULL, 16);
    }
    sleep_s(start);
    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_sigaction = onprof;
    sa.sa_flags = SA_SIGINFO | SA_RESTART;
    sigemptyset(&sa.sa_mask);
    sigaction(SIGPROF, &sa, NULL);
    atomic_store(&active, 1);
    struct itimerval it = {{0, us}, {0, us}};
    setitimer(ITIMER_PROF, &it, NULL);
    sleep_s(secs);
    struct itimerval off = {{0, 0}, {0, 0}};
    setitimer(ITIMER_PROF, &off, NULL);
    atomic_store(&active, 0);
    sleep_s(0.2);
    char path[512];
    snprintf(path, sizeof path, "%s/maps", out);
    copyfile("/proc/self/maps", path);
    snprintf(path, sizeof path, "%s/samples", out);
    FILE* f = fopen(path, "w");
    unsigned n = atomic_load(&count);
    if (n > MAXS)
        n = MAXS;
    if (f) {
        fprintf(f, "# pid %d samples %u interval_us %ld secs %.1f\n", getpid(), n, us, secs);
        for (unsigned i = 0; i < n; i++)
            fprintf(f, "%u %lx %lx\n", tids[i], (unsigned long)pcs[i], (unsigned long)x64s[i]);
        fclose(f);
    }
    snprintf(path, sizeof path, "%s/threads", out);
    f = fopen(path, "w");
    if (f) {
        char tp[128], comm[64];
        for (unsigned i = 0; i < n; i++) {
            int seen = 0;
            for (unsigned j = 0; j < i && !seen; j++)
                seen = tids[j] == tids[i];
            if (seen)
                continue;
            snprintf(tp, sizeof tp, "/proc/self/task/%u/comm", tids[i]);
            FILE* c = fopen(tp, "r");
            comm[0] = 0;
            if (c) {
                if (!fgets(comm, sizeof comm, c))
                    comm[0] = 0;
                fclose(c);
            }
            comm[strcspn(comm, "\n")] = 0;
            fprintf(f, "%u %s\n", tids[i], comm);
        }
        fclose(f);
    }
    snprintf(path, sizeof path, "%s/done", out);
    f = fopen(path, "w");
    if (f)
        fclose(f);
    return NULL;
}

__attribute__((constructor)) static void qprof_init(void)
{
    const char* out = getenv("QPROF_OUT");
    const char* match = getenv("QPROF_MATCH");
    if (!out || !match)
        return;
    char cmd[4096];
    FILE* c = fopen("/proc/self/cmdline", "r");
    if (!c)
        return;
    size_t n = fread(cmd, 1, sizeof cmd - 1, c);
    fclose(c);
    cmd[n] = 0;
    size_t a0 = strlen(cmd);
    if (a0 < 5 || strcmp(cmd + a0 - 5, "box64") != 0)
        return;
    unsetenv("LD_PRELOAD");
    for (size_t i = 0; i < n; i++)
        if (!cmd[i])
            cmd[i] = ' ';
    if (!strstr(cmd + a0, match))
        return;
    pthread_t t;
    if (pthread_create(&t, NULL, worker, NULL) == 0)
        pthread_detach(t);
}
