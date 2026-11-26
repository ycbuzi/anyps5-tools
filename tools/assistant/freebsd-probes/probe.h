#ifndef PROBE_H
#define PROBE_H
/* Shared helpers: every result line is "<probe>.<case> <result>", stable across runs (no addresses, no raw timings). */
#include <errno.h>
#include <stdio.h>
#include <string.h>

static const char* probe_name = "probe";

__attribute__((constructor)) static void probe_line_buffered(void) { setvbuf(stdout, NULL, _IOLBF, 0); }

static const char* errname(int e) {
    switch (e) {
    case 0: return "0";
    case EPERM: return "EPERM";
    case ENOENT: return "ENOENT";
    case ESRCH: return "ESRCH";
    case EINTR: return "EINTR";
    case EIO: return "EIO";
    case E2BIG: return "E2BIG";
    case EBADF: return "EBADF";
    case EDEADLK: return "EDEADLK";
    case ENOMEM: return "ENOMEM";
    case EACCES: return "EACCES";
    case EFAULT: return "EFAULT";
    case EBUSY: return "EBUSY";
    case EEXIST: return "EEXIST";
    case ENODEV: return "ENODEV";
    case ENOTDIR: return "ENOTDIR";
    case EISDIR: return "EISDIR";
    case EINVAL: return "EINVAL";
    case ENFILE: return "ENFILE";
    case EMFILE: return "EMFILE";
    case EPIPE: return "EPIPE";
    case EAGAIN: return "EAGAIN";
    case EOPNOTSUPP: return "EOPNOTSUPP";
    case ETIMEDOUT: return "ETIMEDOUT";
    case ENOSYS: return "ENOSYS";
    case EOVERFLOW: return "EOVERFLOW";
    case ENOTTY: return "ENOTTY";
    case ERANGE: return "ERANGE";
    case ENOTCONN: return "ENOTCONN";
    case ENOTEMPTY: return "ENOTEMPTY";
    case EINPROGRESS: return "EINPROGRESS";
    case EDESTADDRREQ: return "EDESTADDRREQ";
    case EMSGSIZE: return "EMSGSIZE";
    case EPROTOTYPE: return "EPROTOTYPE";
    case ENOPROTOOPT: return "ENOPROTOOPT";
    case EPROTONOSUPPORT: return "EPROTONOSUPPORT";
    case EAFNOSUPPORT: return "EAFNOSUPPORT";
    case EADDRINUSE: return "EADDRINUSE";
    case EISCONN: return "EISCONN";
    case ECONNREFUSED: return "ECONNREFUSED";
    case ECONNRESET: return "ECONNRESET";
    default: {
        static char buf[32];
        snprintf(buf, sizeof buf, "errno%d", e);
        return buf;
    }
    }
}

/* rc-style call: prints rc and errno name (errno only when rc == -1). */
#define CHECK(label, expr)                                                         \
    do {                                                                           \
        errno = 0;                                                                 \
        long rc_ = (long)(expr);                                                   \
        int e_ = errno;                                                            \
        if (rc_ == -1) printf("%s.%s rc=-1 errno=%s\n", probe_name, label, errname(e_)); \
        else printf("%s.%s rc=%ld\n", probe_name, label, rc_);                     \
    } while (0)

/* For calls returning an error number directly (posix_* style). */
#define CHECK_ERR(label, expr)                                                     \
    do {                                                                           \
        int r_ = (expr);                                                           \
        printf("%s.%s ret=%s\n", probe_name, label, errname(r_));                  \
    } while (0)

/* Runs a case that may block forever in a child process; reports HANG if it is still running after 2 s. */
#include <signal.h>
#include <stdlib.h>
#include <sys/wait.h>
#include <unistd.h>
#define GUARDED(label, expr)                                                       \
    do {                                                                           \
        fflush(stdout);                                                            \
        pid_t child_ = fork();                                                     \
        if (child_ == 0) {                                                         \
            CHECK(label, expr);                                                    \
            fflush(stdout);                                                        \
            _exit(0);                                                              \
        }                                                                          \
        int st_ = 0, waited_ = 0;                                                  \
        while (waitpid(child_, &st_, WNOHANG) == 0 && waited_ < 200) {             \
            usleep(10000);                                                         \
            ++waited_;                                                             \
        }                                                                          \
        if (waited_ >= 200) {                                                      \
            kill(child_, SIGKILL);                                                 \
            waitpid(child_, &st_, 0);                                              \
            printf("%s.%s HANG (still blocked after 2s)\n", probe_name, label);    \
        } else if (WIFSIGNALED(st_)) {                                             \
            printf("%s.%s CRASH signal=%d\n", probe_name, label, WTERMSIG(st_));   \
        }                                                                          \
    } while (0)

#define NOTE(label, ...)                                                         \
    do {                                                                           \
        printf("%s.%s ", probe_name, label);                                       \
        printf(__VA_ARGS__);                                                       \
        printf("\n");                                                              \
    } while (0)

#endif
