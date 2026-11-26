#include <sys/types.h>
#include <sys/event.h>
#include <sys/time.h>
#include <unistd.h>
#include "probe.h"

static int kq;
static const struct timespec poll0 = {0, 0};

static int change(uintptr_t id, int flags, unsigned fflags, intptr_t data) {
    struct kevent ev;
    EV_SET(&ev, id, EVFILT_USER, flags, fflags, data, NULL);
    return kevent(kq, &ev, 1, NULL, 0, NULL);
}

static void collect(const char* label) {
    struct kevent out[4];
    errno = 0;
    int n = kevent(kq, NULL, 0, out, 4, &poll0);
    if (n <= 0) {
        NOTE(label, "n=%d%s%s", n, n < 0 ? " errno=" : "", n < 0 ? errname(errno) : "");
        return;
    }
    NOTE(label, "n=%d ident=%lu flags=0x%x fflags=0x%x data=%ld", n, (unsigned long)out[0].ident, out[0].flags, out[0].fflags, (long)out[0].data);
}

static void fresh(void) {
    if (kq > 0) close(kq);
    kq = kqueue();
}

int main(void) {
    probe_name = "kqueue_user";
    fresh();
    CHECK("add", change(1, EV_ADD, 0, 0));
    collect("add_untriggered_poll");
    CHECK("trigger", change(1, 0, NOTE_TRIGGER, 0));
    collect("trigger_poll1");
    collect("trigger_poll2_no_clear");
    fresh();
    change(1, EV_ADD | EV_CLEAR, 0, 0);
    change(1, 0, NOTE_TRIGGER, 0);
    collect("clear_poll1");
    collect("clear_poll2");
    change(1, 0, NOTE_TRIGGER, 0);
    collect("clear_retrigger");
    fresh();
    CHECK("add_with_trigger", change(1, EV_ADD, NOTE_TRIGGER, 0));
    collect("add_with_trigger_poll");
    fresh();
    CHECK("add_with_fflags_data", change(1, EV_ADD | EV_CLEAR, 0x5, 9));
    change(1, 0, NOTE_TRIGGER, 0);
    collect("add_with_fflags_data_poll");
    fresh();
    change(1, EV_ADD | EV_CLEAR, 0, 0);
    change(1, 0, NOTE_FFCOPY | 0x0f, 0);
    change(1, 0, NOTE_FFOR | 0x30, 0);
    change(1, 0, NOTE_FFAND | 0x3c, 0);
    change(1, 0, NOTE_TRIGGER, 42);
    collect("ffcopy_ffor_ffand_data42");
    change(1, 0, NOTE_TRIGGER, 0);
    collect("fflags_kept_after_clear");
    change(1, 0, NOTE_TRIGGER | NOTE_FFCOPY | 0xffffff, 0);
    collect("ffcopy_with_trigger_all_bits");
    fresh();
    change(1, EV_ADD | EV_ONESHOT, 0, 0);
    change(1, 0, NOTE_TRIGGER, 0);
    collect("oneshot_poll1");
    collect("oneshot_poll2");
    CHECK("oneshot_delete_after_fire", change(1, EV_DELETE, 0, 0));
    fresh();
    change(1, EV_ADD | EV_DISPATCH, 0, 0);
    change(1, 0, NOTE_TRIGGER, 0);
    collect("dispatch_poll1");
    collect("dispatch_poll2");
    CHECK("dispatch_enable", change(1, EV_ENABLE, 0, 0));
    collect("dispatch_after_enable");
    fresh();
    change(1, EV_ADD | EV_DISABLE, 0, 0);
    change(1, 0, NOTE_TRIGGER, 0);
    collect("disabled_triggered_poll");
    change(1, EV_ENABLE, 0, 0);
    collect("enabled_after_trigger_poll");
    fresh();
    change(1, EV_ADD, 0, 0);
    change(1, 0, NOTE_TRIGGER, 0);
    CHECK("readd_existing", change(1, EV_ADD | EV_CLEAR, 0, 0));
    collect("readd_existing_poll1");
    collect("readd_existing_poll2");
    fresh();
    CHECK("delete_missing", change(7, EV_DELETE, 0, 0));
    CHECK("trigger_missing", change(7, 0, NOTE_TRIGGER, 0));
    CHECK("enable_and_disable", change(7, EV_ADD | EV_ENABLE | EV_DISABLE, 0, 0));
    {
        struct kevent ev, out;
        EV_SET(&ev, 3, EVFILT_USER, EV_ADD | EV_RECEIPT, 0, 0, NULL);
        int n = kevent(kq, &ev, 1, &out, 1, &poll0);
        NOTE("receipt_add", "n=%d flags=0x%x data=%ld", n, n > 0 ? out.flags : 0, n > 0 ? (long)out.data : 0L);
        EV_SET(&ev, 99, EVFILT_USER, EV_DELETE, 0, 0, NULL);
        n = kevent(kq, &ev, 1, &out, 1, &poll0);
        NOTE("delete_missing_with_eventlist", "n=%d flags=0x%x data=%s", n, n > 0 ? out.flags : 0, n > 0 ? errname((int)out.data) : "-");
        EV_SET(&ev, 3, EVFILT_USER, EV_ADD | EV_RECEIPT, 0, 0, NULL);
        CHECK("receipt_no_eventlist", kevent(kq, &ev, 1, NULL, 0, &poll0));
    }
    {
        struct kevent evs[3], out[3];
        EV_SET(&evs[0], 10, EVFILT_USER, EV_ADD, NOTE_TRIGGER, 0, NULL);
        EV_SET(&evs[1], 11, EVFILT_USER, EV_DELETE, 0, 0, NULL);
        EV_SET(&evs[2], 12, EVFILT_USER, EV_ADD, 0, 0, NULL);
        int n = kevent(kq, evs, 3, out, 3, &poll0);
        NOTE("batch_with_one_error", "n=%d first_flags=0x%x first_data=%s", n, n > 0 ? out[0].flags : 0, n > 0 ? errname((int)out[0].data) : "-");
        EV_SET(&evs[0], 12, EVFILT_USER, 0, NOTE_TRIGGER, 0, NULL);
        kevent(kq, evs, 1, NULL, 0, NULL);
        collect("batch_later_change_applied");
    }
    {
        struct kevent ev;
        EV_SET(&ev, 20, EVFILT_USER, EV_ADD, 0, 0, (void*)0x1234);
        kevent(kq, &ev, 1, NULL, 0, NULL);
        EV_SET(&ev, 20, EVFILT_USER, 0, NOTE_TRIGGER, 0, (void*)0x5678);
        kevent(kq, &ev, 1, NULL, 0, NULL);
        struct kevent out[8];
        int n = kevent(kq, NULL, 0, out, 8, &poll0);
        const char* ud = "none";
        for (int i = 0; i < n; ++i)
            if (out[i].ident == 20) ud = out[i].udata == (void*)0x1234 ? "from_add" : (out[i].udata == (void*)0x5678 ? "from_trigger" : "other");
        NOTE("udata_after_trigger_change", "%s", ud);
    }
    return 0;
}
