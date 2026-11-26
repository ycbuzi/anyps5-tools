#include <sys/types.h>
#include <sys/event.h>
#include <sys/time.h>
#include <stdint.h>
#include <time.h>
#include <unistd.h>
#include "probe.h"

static int kq;
static const struct timespec poll0 = {0, 0};

static long long now_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec * 1000LL + t.tv_nsec / 1000000;
}

static int timer(uintptr_t id, int flags, unsigned fflags, intptr_t data) {
    struct kevent ev;
    EV_SET(&ev, id, EVFILT_TIMER, flags, fflags, data, NULL);
    return kevent(kq, &ev, 1, NULL, 0, NULL);
}

static void collect(const char* label, const struct timespec* wait) {
    struct kevent out[4];
    errno = 0;
    int n = kevent(kq, NULL, 0, out, 4, wait);
    if (n <= 0) {
        NOTE(label, "n=%d%s%s", n, n < 0 ? " errno=" : "", n < 0 ? errname(errno) : "");
        return;
    }
    const char* d = out[0].data == 1 ? "1" : (out[0].data > 1 ? ">1" : "<1");
    NOTE(label, "n=%d flags=0x%x fflags=0x%x data=%s", n, out[0].flags, out[0].fflags, d);
}

static void fresh(void) {
    if (kq > 0) close(kq);
    kq = kqueue();
}

int main(void) {
    probe_name = "kqueue_timer";
    struct timespec w200 = {0, 200000000};
    fresh();
    CHECK("add_periodic_10ms", timer(1, EV_ADD, 0, 10));
    collect("periodic_immediate_poll", &poll0);
    usleep(55000);
    collect("periodic_after_55ms", &poll0);
    collect("periodic_poll_again", &poll0);
    collect("periodic_wait_next", &w200);
    fresh();
    CHECK("add_oneshot_10ms", timer(1, EV_ADD | EV_ONESHOT, 0, 10));
    collect("oneshot_wait", &w200);
    collect("oneshot_wait_again", &poll0);
    CHECK("oneshot_delete_after_fire", timer(1, EV_DELETE, 0, 0));
    fresh();
    CHECK("add_data_0_periodic", timer(1, EV_ADD, 0, 0));
    usleep(20000);
    collect("data_0_periodic_after_20ms", &poll0);
    fresh();
    CHECK("add_data_0_oneshot", timer(1, EV_ADD | EV_ONESHOT, 0, 0));
    collect("data_0_oneshot_wait", &w200);
    fresh();
    CHECK("add_data_neg", timer(1, EV_ADD, 0, -1));
    CHECK("add_seconds_unit", timer(2, EV_ADD, NOTE_SECONDS, 1));
    CHECK("add_useconds_unit", timer(3, EV_ADD, NOTE_USECONDS, 5000));
    CHECK("add_nseconds_unit", timer(4, EV_ADD, NOTE_NSECONDS, 5000000));
    CHECK("add_two_units", timer(5, EV_ADD, NOTE_SECONDS | NOTE_USECONDS, 1));
    CHECK("add_unknown_fflag_0x100", timer(6, EV_ADD, 0x100, 10));
    CHECK("add_abstime_fflag", timer(7, EV_ADD, NOTE_ABSTIME, 10));
    CHECK("add_huge_ms", timer(8, EV_ADD, 0, INTPTR_MAX));
    CHECK("add_huge_seconds", timer(9, EV_ADD, NOTE_SECONDS, INTPTR_MAX / 2));
    fresh();
    timer(1, EV_ADD, 0, 10);
    usleep(30000);
    CHECK("readd_new_period_1000ms", timer(1, EV_ADD, 0, 1000));
    collect("readd_resets_pending", &poll0);
    usleep(30000);
    collect("readd_new_period_after_30ms", &poll0);
    fresh();
    timer(1, EV_ADD | EV_DISABLE, 0, 10);
    usleep(30000);
    collect("disabled_after_30ms", &poll0);
    CHECK("enable", timer(1, EV_ENABLE, 0, 0));
    collect("enabled_poll", &poll0);
    collect("enabled_wait", &w200);
    fresh();
    timer(1, EV_ADD | EV_DISPATCH, 0, 10);
    collect("dispatch_wait1", &w200);
    usleep(30000);
    collect("dispatch_after_30ms", &poll0);
    timer(1, EV_ENABLE, 0, 0);
    collect("dispatch_after_enable", &w200);
    fresh();
    timer(1, EV_ADD, 0, 10);
    CHECK("modify_without_add", timer(1, 0, 0, 500));
    CHECK("delete", timer(1, EV_DELETE, 0, 0));
    CHECK("delete_again", timer(1, EV_DELETE, 0, 0));
    fresh();
    {
        struct timespec bad1 = {0, 1000000000}, bad2 = {-1, 0}, bad3 = {0, -1}, t20 = {0, 20000000};
        struct kevent out;
        timer(1, EV_ADD | EV_ONESHOT, NOTE_SECONDS, 100);
        CHECK("kevent_timeout_nsec_1e9", kevent(kq, NULL, 0, &out, 1, &bad1));
        CHECK("kevent_timeout_sec_neg", kevent(kq, NULL, 0, &out, 1, &bad2));
        CHECK("kevent_timeout_nsec_neg", kevent(kq, NULL, 0, &out, 1, &bad3));
        long long s = now_ms();
        CHECK("kevent_timeout_20ms_idle", kevent(kq, NULL, 0, &out, 1, &t20));
        NOTE("kevent_timeout_20ms_elapsed", "%s", now_ms() - s >= 19 ? ">=20ms" : "<20ms");
        CHECK("kevent_nevents_0_no_changes", kevent(kq, NULL, 0, &out, 0, &t20));
    }
    return 0;
}
