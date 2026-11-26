#include <sys/types.h>
#include <sys/event.h>
#include <sys/time.h>
#include <signal.h>
#include <fcntl.h>
#include <unistd.h>
#include "probe.h"

static const struct timespec poll0 = {0, 0};

static int reg(int kq, uintptr_t id, short filter, int flags, unsigned fflags, intptr_t data) {
    struct kevent ev;
    EV_SET(&ev, id, filter, flags, fflags, data, NULL);
    return kevent(kq, &ev, 1, NULL, 0, NULL);
}

static void collect(int kq, const char* label) {
    struct kevent out[4];
    errno = 0;
    int n = kevent(kq, NULL, 0, out, 4, &poll0);
    if (n <= 0) {
        NOTE(label, "n=%d%s%s", n, n < 0 ? " errno=" : "", n < 0 ? errname(errno) : "");
        return;
    }
    NOTE(label, "n=%d filter=%d flags=0x%x fflags=0x%x data=%ld", n, out[0].filter, out[0].flags, out[0].fflags, (long)out[0].data);
}

int main(void) {
    probe_name = "kqueue_misc";
    int kq = kqueue();
    struct kevent out;
    CHECK("kevent_bad_fd", kevent(-1, NULL, 0, &out, 1, &poll0));
    CHECK("kevent_closed_fd", kevent(1000, NULL, 0, &out, 1, &poll0));
    int p[2];
    pipe(p);
    CHECK("kevent_on_pipe_fd", kevent(p[0], NULL, 0, &out, 1, &poll0));
    CHECK("kevent_nevents_neg", kevent(kq, NULL, 0, &out, -1, &poll0));
    CHECK("kevent_nchanges_neg", kevent(kq, NULL, -1, &out, 1, &poll0));
    CHECK("kevent_changes_null_nchanges_1", kevent(kq, NULL, 1, &out, 1, &poll0));
    CHECK("filter_0_skipped", reg(kq, 1, 0, EV_ADD, 0, 0));
    CHECK("filter_positive", reg(kq, 1, 1, EV_ADD, 0, 0));
    CHECK("filter_minus_100", reg(kq, 1, -100, EV_ADD, 0, 0));
    CHECK("read_closed_fd", reg(kq, 1000, EVFILT_READ, EV_ADD, 0, 0));
    CHECK("read_on_self", reg(kq, kq, EVFILT_READ, EV_ADD, 0, 0));
    CHECK("read_pipe", reg(kq, p[0], EVFILT_READ, EV_ADD, 0, 0));
    collect(kq, "read_pipe_empty");
    write(p[1], "abc", 3);
    collect(kq, "read_pipe_3_bytes");
    CHECK("write_pipe", reg(kq, p[1], EVFILT_WRITE, EV_ADD | EV_ONESHOT, 0, 0));
    collect(kq, "write_pipe_ready");
    CHECK("read_pipe_lowat_4", reg(kq, p[0], EVFILT_READ, EV_ADD, NOTE_LOWAT, 4));
    collect(kq, "read_pipe_lowat_4_with_3");
    reg(kq, p[0], EVFILT_READ, EV_DELETE, 0, 0);
    close(p[1]);
    CHECK("read_pipe_writer_closed", reg(kq, p[0], EVFILT_READ, EV_ADD | EV_ONESHOT, 0, 0));
    collect(kq, "read_pipe_writer_closed_poll");
    close(p[0]);
    pipe(p);
    close(p[0]);
    CHECK("write_pipe_reader_closed", reg(kq, p[1], EVFILT_WRITE, EV_ADD, 0, 0));
    close(p[1]);
    pipe(p);
    reg(kq, p[0], EVFILT_READ, EV_ADD, 0, 0);
    close(p[0]);
    CHECK("delete_after_fd_closed", reg(kq, p[0], EVFILT_READ, EV_DELETE, 0, 0));
    close(p[1]);
    {
        int f = open("/etc/passwd", O_RDONLY);
        CHECK("read_regular_file", reg(kq, f, EVFILT_READ, EV_ADD | EV_ONESHOT, 0, 0));
        collect(kq, "read_regular_file_poll");
        CHECK("write_regular_file", reg(kq, f, EVFILT_WRITE, EV_ADD | EV_ONESHOT, 0, 0));
        collect(kq, "write_regular_file_poll");
        CHECK("vnode_regular_file", reg(kq, f, EVFILT_VNODE, EV_ADD, NOTE_DELETE | NOTE_WRITE, 0));
        close(f);
    }
    {
        int kq2 = kqueue();
        CHECK("read_other_kqueue", reg(kq, kq2, EVFILT_READ, EV_ADD, 0, 0));
        collect(kq, "read_other_kqueue_idle");
        reg(kq2, 5, EVFILT_USER, EV_ADD, NOTE_TRIGGER, 0);
        reg(kq2, 5, EVFILT_USER, 0, NOTE_TRIGGER, 0);
        collect(kq, "read_other_kqueue_pending");
        reg(kq, kq2, EVFILT_READ, EV_DELETE, 0, 0);
        close(kq2);
    }
    signal(SIGUSR1, SIG_IGN);
    CHECK("signal_add", reg(kq, SIGUSR1, EVFILT_SIGNAL, EV_ADD | EV_CLEAR, 0, 0));
    kill(getpid(), SIGUSR1);
    kill(getpid(), SIGUSR1);
    collect(kq, "signal_two_kills");
    CHECK("signal_bad_number", reg(kq, 999, EVFILT_SIGNAL, EV_ADD, 0, 0));
    CHECK("proc_self", reg(kq, getpid(), EVFILT_PROC, EV_ADD, NOTE_EXIT, 0));
    CHECK("proc_missing_pid", reg(kq, 999999, EVFILT_PROC, EV_ADD, NOTE_EXIT, 0));
    CHECK("empty_filter_pipe", (pipe(p), reg(kq, p[1], EVFILT_EMPTY, EV_ADD | EV_ONESHOT, 0, 0)));
    collect(kq, "empty_filter_pipe_poll");
    CHECK("aio_add", reg(kq, 1, EVFILT_AIO, EV_ADD, 0, 0));
    CHECK("aio_delete_missing", reg(kq, 1, EVFILT_AIO, EV_DELETE, 0, 0));
    CHECK("lio_add", reg(kq, 1, EVFILT_LIO, EV_ADD, 0, 0));
    CHECK("fs_add", reg(kq, 0, EVFILT_FS, EV_ADD, 0, 0));
    CHECK("procdesc_on_pipe", reg(kq, p[0], EVFILT_PROCDESC, EV_ADD, NOTE_EXIT, 0));
    {
        int e = kqueue();
        reg(e, 1, EVFILT_USER, EV_ADD, 0, 0);
        reg(e, 1, EVFILT_USER, 0, NOTE_TRIGGER, 0);
        CHECK("eventlist_null_with_ready_event", kevent(e, NULL, 0, NULL, 1, &poll0));
        close(e);
    }
    {
        int e = kqueue();
        int d = dup(e);
        reg(e, 1, EVFILT_USER, EV_ADD, 0, 0);
        reg(e, 1, EVFILT_USER, 0, NOTE_TRIGGER, 0);
        collect(d, "dup_shares_queue");
        close(e);
        collect(d, "dup_after_original_closed");
        close(d);
    }
    {
        int e = kqueue();
        int fl = fcntl(e, F_GETFD);
        NOTE("kqueue_cloexec_default", "%s", (fl & FD_CLOEXEC) ? "set" : "clear");
        int e2 = kqueuex(KQUEUE_CLOEXEC);
        NOTE("kqueuex_cloexec", "%s", (fcntl(e2, F_GETFD) & FD_CLOEXEC) ? "set" : "clear");
        close(e);
        close(e2);
    }
    return 0;
}
