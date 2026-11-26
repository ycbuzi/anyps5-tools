#include <sys/types.h>
#include <sys/umtx.h>
#include <sys/time.h>
#include <limits.h>
#include <pthread.h>
#include <signal.h>
#include <stdint.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>
#include "probe.h"

static long long now_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec * 1000LL + t.tv_nsec / 1000000;
}

static volatile u_int word32;
static volatile u_int other32;
static volatile int woken;

static void* waiter(void* arg) {
    volatile u_int* w = arg;
    struct timespec ts = {2, 0};
    if (_umtx_op((void*)w, UMTX_OP_WAIT_UINT_PRIVATE, 0, (void*)sizeof ts, &ts) == 0) __sync_fetch_and_add(&woken, 1);
    return NULL;
}

static int wake_test(const char* label, int nthreads, u_long count) {
    pthread_t t[8];
    word32 = 0;
    woken = 0;
    for (int i = 0; i < nthreads; ++i) pthread_create(&t[i], NULL, waiter, (void*)&word32);
    usleep(100000);
    errno = 0;
    int r = _umtx_op((void*)&word32, UMTX_OP_WAKE_PRIVATE, count, NULL, NULL);
    usleep(100000);
    int seen = woken;
    word32 = 1;
    _umtx_op((void*)&word32, UMTX_OP_WAKE_PRIVATE, INT_MAX, NULL, NULL);
    for (int i = 0; i < nthreads; ++i) pthread_join(t[i], NULL);
    NOTE(label, "rc=%d woken=%d/%d", r, seen, nthreads);
    return seen;
}

static void* late_wake(void* arg) {
    (void)arg;
    usleep(300000);
    word32 = 1;
    _umtx_op((void*)&word32, UMTX_OP_WAKE_PRIVATE, INT_MAX, NULL, NULL);
    return NULL;
}

static void on_alarm(int sig) { (void)sig; }

static void signal_case(const char* label, int restart, int timed) {
    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = on_alarm;
    sa.sa_flags = restart ? SA_RESTART : 0;
    sigaction(SIGALRM, &sa, NULL);
    word32 = 0;
    pthread_t helper;
    pthread_create(&helper, NULL, late_wake, NULL);
    struct itimerval it = {{0, 0}, {0, 50000}};
    setitimer(ITIMER_REAL, &it, NULL);
    struct timespec ts = {1, 0};
    errno = 0;
    long long start = now_ms();
    int r = timed ? _umtx_op((void*)&word32, UMTX_OP_WAIT_UINT_PRIVATE, 0, (void*)sizeof ts, &ts)
                  : _umtx_op((void*)&word32, UMTX_OP_WAIT_UINT_PRIVATE, 0, NULL, NULL);
    int e = errno;
    long long took = now_ms() - start;
    pthread_join(helper, NULL);
    NOTE(label, "rc=%d errno=%s returned_%s", r, r ? errname(e) : "0", took < 200 ? "at_signal" : "after_wake");
}

int main(void) {
    probe_name = "umtx_wait";
    u_long lword = 5;
    u_int uword = 5;
    struct timespec zero = {0, 0}, ten = {0, 10000000}, badnsec = {0, 1000000000}, negnsec = {0, -1}, negsec = {-1, 0};
    CHECK("wait_mismatch", _umtx_op(&lword, UMTX_OP_WAIT, 4, NULL, NULL));
    CHECK("wait_uint_mismatch", _umtx_op(&uword, UMTX_OP_WAIT_UINT, 4, NULL, NULL));
    CHECK("wait_uint_private_mismatch", _umtx_op(&uword, UMTX_OP_WAIT_UINT_PRIVATE, 4, NULL, NULL));
    CHECK("wait_uint_expected_above_32bit", _umtx_op(&uword, UMTX_OP_WAIT_UINT_PRIVATE, 0x100000005ULL, (void*)sizeof zero, &zero));
    CHECK("wait_long_compares_64bit", _umtx_op(&lword, UMTX_OP_WAIT, 0x100000005ULL, (void*)sizeof zero, &zero));
    CHECK("wait_match_timeout_0", _umtx_op(&uword, UMTX_OP_WAIT_UINT_PRIVATE, 5, (void*)sizeof zero, &zero));
    {
        long long s = now_ms();
        CHECK("wait_match_timeout_10ms", _umtx_op(&uword, UMTX_OP_WAIT_UINT_PRIVATE, 5, (void*)sizeof ten, &ten));
        NOTE("wait_match_timeout_10ms_elapsed", "%s", now_ms() - s >= 10 ? ">=10ms" : "<10ms");
    }
    CHECK("wait_timeout_size_null_reads_timespec", _umtx_op(&uword, UMTX_OP_WAIT_UINT_PRIVATE, 5, NULL, &zero));
    {
        struct { u_int lo; u_int hi; long nsec; } one = {1, 0, 0};
        long long s = now_ms();
        int r = _umtx_op(&uword, UMTX_OP_WAIT_UINT_PRIVATE, 5, NULL, &one);
        int e = errno;
        NOTE("wait_timeout_size_null_value_1", "rc=%d errno=%s waited_%s", r, r ? errname(e) : "0", now_ms() - s >= 900 ? "1s_as_timespec" : "under_1s_not_timespec");
    }
    CHECK("wait_timeout_nsec_1e9", _umtx_op(&uword, UMTX_OP_WAIT_UINT_PRIVATE, 5, (void*)sizeof badnsec, &badnsec));
    CHECK("wait_timeout_nsec_neg", _umtx_op(&uword, UMTX_OP_WAIT_UINT_PRIVATE, 5, (void*)sizeof negnsec, &negnsec));
    CHECK("wait_timeout_sec_neg", _umtx_op(&uword, UMTX_OP_WAIT_UINT_PRIVATE, 5, (void*)sizeof negsec, &negsec));
    CHECK("wait_mismatch_bad_timeout", _umtx_op(&uword, UMTX_OP_WAIT_UINT_PRIVATE, 4, (void*)sizeof badnsec, &badnsec));
    {
        struct _umtx_time ut;
        memset(&ut, 0, sizeof ut);
        ut._flags = UMTX_ABSTIME;
        ut._clockid = CLOCK_MONOTONIC;
        clock_gettime(CLOCK_MONOTONIC, &ut._timeout);
        ut._timeout.tv_sec -= 1;
        CHECK("wait_abstime_monotonic_past", _umtx_op(&uword, UMTX_OP_WAIT_UINT_PRIVATE, 5, (void*)sizeof ut, &ut));
        ut._clockid = CLOCK_REALTIME;
        clock_gettime(CLOCK_REALTIME, &ut._timeout);
        ut._timeout.tv_nsec = (ut._timeout.tv_nsec + 20000000) % 1000000000;
        ut._timeout.tv_sec += ut._timeout.tv_nsec < 20000000;
        long long s = now_ms();
        CHECK("wait_abstime_realtime_plus20ms", _umtx_op(&uword, UMTX_OP_WAIT_UINT_PRIVATE, 5, (void*)sizeof ut, &ut));
        NOTE("wait_abstime_realtime_plus20ms_elapsed", "%s", now_ms() - s >= 15 ? ">=15ms" : "<15ms");
        ut._clockid = 99;
        GUARDED("wait_abstime_bad_clock", _umtx_op(&uword, UMTX_OP_WAIT_UINT_PRIVATE, 5, (void*)sizeof ut, &ut));
        ut._clockid = CLOCK_MONOTONIC;
        ut._flags = 0;
        ut._timeout = zero;
        CHECK("wait_umtx_time_relative_0", _umtx_op(&uword, UMTX_OP_WAIT_UINT_PRIVATE, 5, (void*)sizeof ut, &ut));
        ut._flags = 0x80;
        CHECK("wait_umtx_time_unknown_flag", _umtx_op(&uword, UMTX_OP_WAIT_UINT_PRIVATE, 5, (void*)sizeof ut, &ut));
    }
    CHECK("wait_null", _umtx_op(NULL, UMTX_OP_WAIT, 0, NULL, NULL));
    CHECK("wait_uint_null", _umtx_op(NULL, UMTX_OP_WAIT_UINT, 0, NULL, NULL));
    CHECK("wait_uint_private_null", _umtx_op(NULL, UMTX_OP_WAIT_UINT_PRIVATE, 0, NULL, NULL));
    {
        static unsigned char raw[16] __attribute__((aligned(8)));
        memset(raw, 0, sizeof raw);
        CHECK("wait_uint_unaligned_mismatch", _umtx_op(raw + 1, UMTX_OP_WAIT_UINT_PRIVATE, 7, NULL, NULL));
        CHECK("wait_uint_unaligned_match_timeout_0", _umtx_op(raw + 1, UMTX_OP_WAIT_UINT_PRIVATE, 0, (void*)sizeof zero, &zero));
        CHECK("wait_long_unaligned_match_timeout_0", _umtx_op(raw + 3, UMTX_OP_WAIT, 0, (void*)sizeof zero, &zero));
        CHECK("wake_unaligned", _umtx_op(raw + 1, UMTX_OP_WAKE_PRIVATE, 1, NULL, NULL));
    }
    CHECK("wake_null_shared", _umtx_op(NULL, UMTX_OP_WAKE, 1, NULL, NULL));
    CHECK("wake_null_private", _umtx_op(NULL, UMTX_OP_WAKE_PRIVATE, 1, NULL, NULL));
    CHECK("wake_no_waiters", _umtx_op(&uword, UMTX_OP_WAKE_PRIVATE, 1, NULL, NULL));
    CHECK("wake_count_0_no_waiters", _umtx_op(&uword, UMTX_OP_WAKE_PRIVATE, 0, NULL, NULL));
    wake_test("wake_1_of_3", 3, 1);
    wake_test("wake_0_of_2", 2, 0);
    wake_test("wake_all_of_3", 3, INT_MAX);
    wake_test("wake_ulong_max_of_2", 2, ULONG_MAX);
    {
        void* addrs[2] = {(void*)&word32, (void*)&other32};
        CHECK("nwake_private_count_0", _umtx_op(addrs, UMTX_OP_NWAKE_PRIVATE, 0, NULL, NULL));
        CHECK("nwake_private_count_neg", _umtx_op(addrs, UMTX_OP_NWAKE_PRIVATE, (u_long)-1, NULL, NULL));
        CHECK("nwake_private_2", _umtx_op(addrs, UMTX_OP_NWAKE_PRIVATE, 2, NULL, NULL));
        CHECK("nwake_private_null_array", _umtx_op(NULL, UMTX_OP_NWAKE_PRIVATE, 1, NULL, NULL));
        void* nulls[1] = {NULL};
        CHECK("nwake_private_null_entry", _umtx_op(nulls, UMTX_OP_NWAKE_PRIVATE, 1, NULL, NULL));
    }
    signal_case("signal_untimed_no_restart", 0, 0);
    signal_case("signal_untimed_restart", 1, 0);
    signal_case("signal_timed_no_restart", 0, 1);
    signal_case("signal_timed_restart", 1, 1);
    signal(SIGALRM, SIG_IGN);
    int ops[] = {-1, 0, 1, 27, 28, 29, 30, 1000};
    char label[64];
    for (size_t i = 0; i < sizeof ops / sizeof ops[0]; ++i) {
        snprintf(label, sizeof label, "op_%d", ops[i]);
        u_int scratch = 0;
        struct timespec t0 = {0, 0};
        CHECK(label, _umtx_op(&scratch, ops[i], 0, (void*)sizeof t0, &t0));
    }
    return 0;
}
