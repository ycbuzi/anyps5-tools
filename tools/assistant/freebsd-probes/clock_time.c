#include <sys/syscall.h>
#include <sys/time.h>
#include <signal.h>
#include <stdint.h>
#include <time.h>
#include <unistd.h>
#include "probe.h"

static long long now_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec * 1000LL + t.tv_nsec / 1000000;
}

static void on_alarm(int sig) { (void)sig; }

static void arm_alarm(int restart, long usec) {
    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = on_alarm;
    sa.sa_flags = restart ? SA_RESTART : 0;
    sigaction(SIGALRM, &sa, NULL);
    struct itimerval it = {{0, 0}, {0, usec}};
    setitimer(ITIMER_REAL, &it, NULL);
}

static void res_label(char* out, size_t n, const struct timespec* r) {
    if (r->tv_sec) snprintf(out, n, "%llds+%ldns", (long long)r->tv_sec, r->tv_nsec);
    else snprintf(out, n, "%ldns", r->tv_nsec);
}

int main(void) {
    probe_name = "clock";
    int ids[] = {-1, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 256};
    char label[64], res[48];
    for (size_t i = 0; i < sizeof ids / sizeof ids[0]; ++i) {
        struct timespec t = {0, 0}, r = {0, 0};
        errno = 0;
        int g = clock_gettime(ids[i], &t);
        int ge = errno;
        errno = 0;
        int rr = clock_getres(ids[i], &r);
        int re = errno;
        res_label(res, sizeof res, &r);
        snprintf(label, sizeof label, "id_%d", ids[i]);
        NOTE(label, "gettime=%s getres=%s res=%s", g ? errname(ge) : "ok", rr ? errname(re) : "ok", rr ? "-" : res);
    }
    GUARDED("libc_gettime_null_tp", clock_gettime(CLOCK_REALTIME, NULL));
    CHECK("gettime_null_tp", syscall(SYS_clock_gettime, CLOCK_REALTIME, NULL));
    CHECK("gettime_bad_id_null_tp", syscall(SYS_clock_gettime, 99, NULL));
    CHECK("getres_null_tp", syscall(SYS_clock_getres, CLOCK_REALTIME, NULL));
    CHECK("getres_bad_id_null_tp", syscall(SYS_clock_getres, 99, NULL));
    {
        struct timespec s;
        clock_gettime(CLOCK_SECOND, &s);
        NOTE("second_nsec_zero", "%s", s.tv_nsec == 0 ? "yes" : "no");
        struct timespec a, b;
        clock_gettime(CLOCK_MONOTONIC, &a);
        clock_gettime(CLOCK_UPTIME, &b);
        NOTE("uptime_vs_monotonic_same_second", "%s", (b.tv_sec - a.tv_sec) <= 1 && (a.tv_sec - b.tv_sec) <= 1 ? "yes" : "no");
    }
    GUARDED("libc_gettimeofday_null_null", gettimeofday(NULL, NULL));
    CHECK("gettimeofday_null_null", syscall(SYS_gettimeofday, NULL, NULL));
    {
        struct timeval tv;
        struct timezone tz = {77, 77};
        CHECK("gettimeofday_tz", gettimeofday(&tv, &tz));
        NOTE("gettimeofday_tz_values", "minuteswest=%d dsttime=%d", tz.tz_minuteswest, tz.tz_dsttime);
    }
    {
        struct timespec bad1 = {0, 1000000000}, bad2 = {0, -1}, neg = {-1, 0}, negbad = {-1, 1000000000}, zero = {0, 0}, ten = {0, 10000000};
        CHECK("nanosleep_null", nanosleep(NULL, NULL));
        CHECK("nanosleep_nsec_1e9", nanosleep(&bad1, NULL));
        CHECK("nanosleep_nsec_neg", nanosleep(&bad2, NULL));
        long long s = now_ms();
        CHECK("nanosleep_sec_neg", nanosleep(&neg, NULL));
        NOTE("nanosleep_sec_neg_elapsed", "%s", now_ms() - s < 5 ? "immediate" : "slept");
        CHECK("nanosleep_sec_neg_nsec_1e9", nanosleep(&negbad, NULL));
        CHECK("nanosleep_zero", nanosleep(&zero, NULL));
        struct timespec rm = {77, 77};
        s = now_ms();
        CHECK("nanosleep_10ms", nanosleep(&ten, &rm));
        NOTE("nanosleep_10ms_elapsed", "%s", now_ms() - s >= 10 ? ">=10ms" : "<10ms");
        NOTE("nanosleep_10ms_rmtp", "%s", rm.tv_sec == 77 && rm.tv_nsec == 77 ? "untouched" : "written");
        struct timespec half = {0, 500000000};
        for (int restart = 0; restart < 2; ++restart) {
            rm.tv_sec = 77;
            rm.tv_nsec = 77;
            arm_alarm(restart, 50000);
            s = now_ms();
            errno = 0;
            int r = nanosleep(&half, &rm);
            int e = errno;
            long long took = now_ms() - s;
            snprintf(label, sizeof label, "nanosleep_signal_%s", restart ? "sa_restart" : "no_restart");
            const char* rmstate = rm.tv_sec == 77 ? "untouched" : (rm.tv_sec == 0 && rm.tv_nsec > 300000000 && rm.tv_nsec < 500000000 ? "remaining_in_range" : "other");
            NOTE(label, "rc=%d errno=%s returned_%s rmtp=%s", r, r ? errname(e) : "0", took < 300 ? "early" : "full", rmstate);
        }
        arm_alarm(0, 50000);
        errno = 0;
        int r = usleep(500000);
        NOTE("usleep_signal", "rc=%d errno=%s", r, r ? errname(errno) : "0");
        CHECK("usleep_1000000", usleep(1000000));
        CHECK("usleep_0", usleep(0));
        arm_alarm(0, 50000);
        unsigned left = sleep(1);
        NOTE("sleep_signal_left", "%u", left);
        signal(SIGALRM, SIG_IGN);
    }
    {
        struct timespec zero = {0, 0}, bad = {0, 1000000000}, past;
        CHECK_ERR("clock_nanosleep_monotonic_rel0", clock_nanosleep(CLOCK_MONOTONIC, 0, &zero, NULL));
        CHECK_ERR("clock_nanosleep_bad_clock", clock_nanosleep(99, 0, &zero, NULL));
        CHECK_ERR("clock_nanosleep_bad_nsec", clock_nanosleep(CLOCK_MONOTONIC, 0, &bad, NULL));
        CHECK_ERR("clock_nanosleep_thread_cputime", clock_nanosleep(CLOCK_THREAD_CPUTIME_ID, 0, &zero, NULL));
        CHECK_ERR("clock_nanosleep_process_cputime", clock_nanosleep(CLOCK_PROCESS_CPUTIME_ID, 0, &zero, NULL));
        CHECK_ERR("clock_nanosleep_bad_flags", clock_nanosleep(CLOCK_MONOTONIC, 0x10, &zero, NULL));
        clock_gettime(CLOCK_REALTIME, &past);
        past.tv_sec -= 5;
        long long s = now_ms();
        CHECK_ERR("clock_nanosleep_abstime_past", clock_nanosleep(CLOCK_REALTIME, TIMER_ABSTIME, &past, NULL));
        NOTE("clock_nanosleep_abstime_past_elapsed", "%s", now_ms() - s < 5 ? "immediate" : "slept");
        CHECK_ERR("clock_nanosleep_uptime_fast", clock_nanosleep(CLOCK_UPTIME_FAST, 0, &zero, NULL));
        CHECK_ERR("clock_nanosleep_second", clock_nanosleep(CLOCK_SECOND, 0, &zero, NULL));
    }
    {
        struct sigevent none;
        memset(&none, 0, sizeof none);
        none.sigev_notify = SIGEV_NONE;
        timer_t t;
        CHECK("timer_create_monotonic", timer_create(CLOCK_MONOTONIC, &none, &t));
        struct itimerspec bad = {{0, 0}, {0, 1000000000}}, one = {{0, 0}, {1, 0}}, cur, old;
        CHECK("timer_settime_bad_nsec", timer_settime(t, 0, &bad, NULL));
        CHECK("timer_settime_1s", timer_settime(t, 0, &one, NULL));
        CHECK("timer_gettime", timer_gettime(t, &cur));
        NOTE("timer_gettime_remaining", "%s", cur.it_value.tv_sec == 0 && cur.it_value.tv_nsec > 0 ? "under_1s" : (cur.it_value.tv_sec == 1 ? "1s" : "other"));
        struct itimerspec off = {{0, 0}, {0, 0}};
        CHECK("timer_settime_disarm_with_old", timer_settime(t, 0, &off, &old));
        NOTE("timer_settime_old_value", "%s", old.it_value.tv_sec == 0 && old.it_value.tv_nsec > 0 ? "under_1s" : "other");
        CHECK("timer_getoverrun", timer_getoverrun(t));
        struct timespec past;
        clock_gettime(CLOCK_MONOTONIC, &past);
        struct itimerspec abs = {{0, 0}, {past.tv_sec - 1, 0}};
        CHECK("timer_settime_abstime_past", timer_settime(t, TIMER_ABSTIME, &abs, NULL));
        timer_gettime(t, &cur);
        NOTE("timer_abstime_past_after", "%s", cur.it_value.tv_sec == 0 && cur.it_value.tv_nsec == 0 ? "expired_disarmed" : "armed");
        struct itimerspec per = {{0, 1000000}, {0, 1000000}};
        timer_settime(t, 0, &per, NULL);
        usleep(30000);
        int ov = timer_getoverrun(t);
        NOTE("timer_sigev_none_overrun_after_30ms", "%s", ov > 1 ? ">1" : (ov == 0 ? "0" : "1"));
        CHECK("timer_delete", timer_delete(t));
        CHECK("ktimer_delete_unknown_id", syscall(SYS_ktimer_delete, 999));
        CHECK("ktimer_settime_unknown_id", syscall(SYS_ktimer_settime, 999, 0, &one, NULL));
        CHECK("ktimer_getoverrun_unknown_id", syscall(SYS_ktimer_getoverrun, 999));
        CHECK("timer_create_bad_clock", timer_create(99, &none, &t));
        CHECK("timer_create_thread_cputime", timer_create(CLOCK_THREAD_CPUTIME_ID, &none, &t));
        if (errno == 0) timer_delete(t);
        CHECK("timer_create_process_cputime", timer_create(CLOCK_PROCESS_CPUTIME_ID, &none, &t));
        if (errno == 0) timer_delete(t);
        CHECK("timer_create_uptime", timer_create(CLOCK_UPTIME, &none, &t));
        if (errno == 0) timer_delete(t);
        struct sigevent badsig = none;
        badsig.sigev_notify = SIGEV_SIGNAL;
        badsig.sigev_signo = 999;
        CHECK("timer_create_bad_signal", timer_create(CLOCK_MONOTONIC, &badsig, &t));
        badsig.sigev_notify = 77;
        CHECK("timer_create_bad_notify", timer_create(CLOCK_MONOTONIC, &badsig, &t));
    }
    return 0;
}
