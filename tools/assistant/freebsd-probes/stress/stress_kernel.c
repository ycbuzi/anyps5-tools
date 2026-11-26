#include <sys/types.h>
#include <sys/event.h>
#include <sys/mman.h>
#include <sys/thr.h>
#include <sys/umtx.h>
#include <pthread.h>
#include <stdint.h>
#include <time.h>
#include "../probe.h"

#define THREADS 4

static long long now_ns(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec * 1000000000LL + t.tv_nsec;
}

static const char* bucket(long v) { return v == 0 ? "0" : (v <= 10 ? "1-10" : (v <= 100 ? "11-100" : ">100")); }

static volatile u_int turn;
static void* handoff_worker(void* arg) {
    u_int me = (u_int)(uintptr_t)arg;
    for (int i = 0; i < 5000; ++i) {
        while (turn % THREADS != me) _umtx_op((void*)&turn, UMTX_OP_WAIT_UINT_PRIVATE, turn, NULL, NULL);
        __atomic_add_fetch(&turn, 1, __ATOMIC_SEQ_CST);
        _umtx_op((void*)&turn, UMTX_OP_WAKE_PRIVATE, INT32_MAX, NULL, NULL);
    }
    return NULL;
}

static void umtx_handoff(void) {
    pthread_t t[THREADS];
    turn = 0;
    for (int i = 0; i < THREADS; ++i) pthread_create(&t[i], NULL, handoff_worker, (void*)(uintptr_t)i);
    for (int i = 0; i < THREADS; ++i) pthread_join(t[i], NULL);
    NOTE("umtx_handoff_round_robin", "final_ok=%d", turn == THREADS * 5000);
}

static volatile u_int gate;
static volatile int woke;
static void* gate_waiter(void* arg) {
    (void)arg;
    struct timespec ts = {1, 0};
    while (gate == 0) {
        if (_umtx_op((void*)&gate, UMTX_OP_WAIT_UINT_PRIVATE, 0, (void*)sizeof ts, &ts) == 0) { __atomic_add_fetch(&woke, 1, __ATOMIC_SEQ_CST); return NULL; }
        if (errno == ETIMEDOUT) return NULL;
    }
    return NULL;
}

static void umtx_wake_exact(void) {
    int mismatches = 0;
    for (int round = 0; round < 40; ++round) {
        pthread_t t[THREADS];
        gate = 0;
        woke = 0;
        for (int i = 0; i < THREADS; ++i) pthread_create(&t[i], NULL, gate_waiter, NULL);
        usleep(20000);
        _umtx_op((void*)&gate, UMTX_OP_WAKE_PRIVATE, 2, NULL, NULL);
        usleep(20000);
        if (woke != 2) ++mismatches;
        gate = 1;
        _umtx_op((void*)&gate, UMTX_OP_WAKE_PRIVATE, INT32_MAX, NULL, NULL);
        for (int i = 0; i < THREADS; ++i) pthread_join(t[i], NULL);
    }
    NOTE("umtx_wake_2_of_4_mismatches", "%s", bucket(mismatches));
}

static struct umutex kmutex;
static long counter;
static void* mutex_worker(void* arg) {
    (void)arg;
    for (int i = 0; i < 20000; ++i) {
        long id;
        thr_self(&id);
        __lwpid_t expect = UMUTEX_UNOWNED;
        if (!__atomic_compare_exchange_n(&kmutex.m_owner, &expect, (__lwpid_t)id, 0, __ATOMIC_ACQUIRE, __ATOMIC_RELAXED))
            _umtx_op(&kmutex, UMTX_OP_MUTEX_LOCK, 0, NULL, NULL);
        ++counter;
        expect = (__lwpid_t)id;
        if (!__atomic_compare_exchange_n(&kmutex.m_owner, &expect, UMUTEX_UNOWNED, 0, __ATOMIC_RELEASE, __ATOMIC_RELAXED))
            _umtx_op(&kmutex, UMTX_OP_MUTEX_UNLOCK, 0, NULL, NULL);
    }
    return NULL;
}

static void umtx_mutex_contention(void) {
    pthread_t t[THREADS];
    memset(&kmutex, 0, sizeof kmutex);
    counter = 0;
    for (int i = 0; i < THREADS; ++i) pthread_create(&t[i], NULL, mutex_worker, NULL);
    for (int i = 0; i < THREADS; ++i) pthread_join(t[i], NULL);
    NOTE("umtx_mutex_contention_counter_exact", "%d", counter == THREADS * 20000L);
    NOTE("umtx_mutex_contention_owner_after", "%s", kmutex.m_owner == UMUTEX_UNOWNED ? "unowned" : "not_unowned");
}

static int user_kq;
static volatile int triggers_done;
static void* user_trigger(void* arg) {
    (void)arg;
    for (int i = 0; i < 20000; ++i) {
        struct kevent ev;
        EV_SET(&ev, 1, EVFILT_USER, 0, NOTE_TRIGGER, 0, NULL);
        kevent(user_kq, &ev, 1, NULL, 0, NULL);
    }
    triggers_done = 1;
    return NULL;
}

static void kqueue_user_cross_thread(void) {
    user_kq = kqueue();
    struct kevent ev;
    EV_SET(&ev, 1, EVFILT_USER, EV_ADD | EV_CLEAR, 0, 0, NULL);
    kevent(user_kq, &ev, 1, NULL, 0, NULL);
    triggers_done = 0;
    pthread_t t;
    pthread_create(&t, NULL, user_trigger, NULL);
    long events = 0;
    struct timespec w = {0, 50000000};
    while (!triggers_done) events += kevent(user_kq, NULL, 0, &ev, 1, &w) > 0;
    pthread_join(t, NULL);
    int last = kevent(user_kq, NULL, 0, &ev, 1, &w);
    int after = kevent(user_kq, NULL, 0, &ev, 1, &(struct timespec){0, 0});
    NOTE("kqueue_user_20000_triggers_events", "%s", events >= 1 && events <= 20000 ? "1..20000" : "out_of_range");
    NOTE("kqueue_user_final_state", "pending_or_seen=%d cleared_after=%d", last >= 0, after == 0);
    close(user_kq);
}

static void kqueue_timer_count(void) {
    int kq = kqueue();
    struct kevent ev;
    EV_SET(&ev, 1, EVFILT_TIMER, EV_ADD, 0, 1, NULL);
    kevent(kq, &ev, 1, NULL, 0, NULL);
    long long start = now_ns();
    long total = 0;
    struct timespec w = {1, 0};
    while (now_ns() - start < 2000000000LL)
        if (kevent(kq, NULL, 0, &ev, 1, &w) > 0) total += ev.data;
    long ms = (long)((now_ns() - start) / 1000000);
    long drift = total > ms ? total - ms : ms - total;
    NOTE("kqueue_timer_1ms_for_2s_count_vs_elapsed", "%s", drift * 20 <= ms ? "within_5pct" : (drift * 5 <= ms ? "within_20pct" : "off_by_more"));
    close(kq);
}

static void nanosleep_oversleep(void) {
    int over = 0;
    struct timespec one = {0, 1000000};
    for (int i = 0; i < 500; ++i) {
        long long s = now_ns();
        nanosleep(&one, NULL);
        long long took = now_ns() - s;
        if (took < 1000000) {
            NOTE("nanosleep_1ms_undersleep", "seen");
            break;
        }
        if (took > 6000000) ++over;
    }
    NOTE("nanosleep_1ms_oversleep_over_5ms", "%s", bucket(over));
}

static volatile int clock_back;
static void* clock_reader(void* arg) {
    (void)arg;
    struct timespec prev, cur;
    clock_gettime(CLOCK_MONOTONIC, &prev);
    for (int i = 0; i < 500000; ++i) {
        clock_gettime(CLOCK_MONOTONIC, &cur);
        if (cur.tv_sec < prev.tv_sec || (cur.tv_sec == prev.tv_sec && cur.tv_nsec < prev.tv_nsec)) __atomic_add_fetch(&clock_back, 1, __ATOMIC_RELAXED);
        prev = cur;
    }
    return NULL;
}

static void clock_monotonic_threads(void) {
    pthread_t t[THREADS];
    clock_back = 0;
    for (int i = 0; i < THREADS; ++i) pthread_create(&t[i], NULL, clock_reader, NULL);
    for (int i = 0; i < THREADS; ++i) pthread_join(t[i], NULL);
    NOTE("clock_monotonic_backwards_steps", "%s", bucket(clock_back));
}

static volatile int map_failures;
static void* map_churn(void* arg) {
    (void)arg;
    long page = sysconf(_SC_PAGESIZE);
    for (int i = 0; i < 3000; ++i) {
        char* p = mmap(NULL, 4 * page, PROT_READ | PROT_WRITE, MAP_ANON | MAP_PRIVATE, -1, 0);
        if (p == MAP_FAILED) { __atomic_add_fetch(&map_failures, 1, __ATOMIC_RELAXED); continue; }
        p[0] = 1;
        p[3 * page] = 1;
        if (mprotect(p + page, page, PROT_READ) != 0) __atomic_add_fetch(&map_failures, 1, __ATOMIC_RELAXED);
        if (munmap(p + page, 2 * page) != 0) __atomic_add_fetch(&map_failures, 1, __ATOMIC_RELAXED);
        if (munmap(p, 4 * page) != 0) __atomic_add_fetch(&map_failures, 1, __ATOMIC_RELAXED);
    }
    return NULL;
}

static void mmap_churn_threads(void) {
    pthread_t t[THREADS];
    map_failures = 0;
    for (int i = 0; i < THREADS; ++i) pthread_create(&t[i], NULL, map_churn, NULL);
    for (int i = 0; i < THREADS; ++i) pthread_join(t[i], NULL);
    NOTE("mmap_churn_failures", "%s", bucket(map_failures));
}

static void on_watchdog(int sig) {
    (void)sig;
    static const char m[] = "stress.watchdog fired after 240s\n";
    write(1, m, sizeof m - 1);
    _exit(3);
}

int main(void) {
    probe_name = "stress";
    signal(SIGALRM, on_watchdog);
    alarm(240);
    umtx_handoff();
    umtx_wake_exact();
    umtx_mutex_contention();
    kqueue_user_cross_thread();
    kqueue_timer_count();
    nanosleep_oversleep();
    clock_monotonic_threads();
    mmap_churn_threads();
    return 0;
}
