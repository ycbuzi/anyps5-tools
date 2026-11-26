#include <sys/types.h>
#include <sys/thr.h>
#include <sys/umtx.h>
#include <pthread.h>
#include <stdint.h>
#include <time.h>
#include <unistd.h>
#include "probe.h"

struct old_usem { uint32_t has_waiters; uint32_t count; uint32_t flags; };

static long self_tid(void) { long t; thr_self(&t); return t; }

static struct umutex shared_m;
static struct ucond shared_cv;
static struct urwlock shared_rw;
static volatile int phase;

static void* holder(void* arg) {
    (void)arg;
    _umtx_op(&shared_m, UMTX_OP_MUTEX_LOCK, 0, NULL, NULL);
    phase = 1;
    while (phase != 2) usleep(1000);
    _umtx_op(&shared_m, UMTX_OP_MUTEX_UNLOCK, 0, NULL, NULL);
    phase = 3;
    return NULL;
}

static void* rw_reader(void* arg) {
    (void)arg;
    _umtx_op(&shared_rw, UMTX_OP_RW_RDLOCK, 0, NULL, NULL);
    phase = 1;
    while (phase != 2) usleep(1000);
    _umtx_op(&shared_rw, UMTX_OP_RW_UNLOCK, 0, NULL, NULL);
    phase = 3;
    return NULL;
}

static void* cv_signaller(void* arg) {
    (void)arg;
    usleep(100000);
    _umtx_op(&shared_cv, UMTX_OP_CV_SIGNAL, 0, NULL, NULL);
    return NULL;
}

static const char* owner_state(struct umutex* m) {
    long id = self_tid();
    uint32_t o = m->m_owner;
    if (o == UMUTEX_UNOWNED) return "unowned";
    if ((o & ~UMUTEX_CONTESTED) == (uint32_t)id) return (o & UMUTEX_CONTESTED) ? "self|contested" : "self";
    if (o == UMUTEX_CONTESTED) return "contested_only";
    return "other";
}

int main(void) {
    probe_name = "umtx_mutex";
    struct timespec zero = {0, 0}, t20 = {0, 20000000};
    {
        struct umutex m = {0};
        CHECK("trylock_free", _umtx_op(&m, UMTX_OP_MUTEX_TRYLOCK, 0, NULL, NULL));
        NOTE("trylock_free_owner", "%s", owner_state(&m));
        CHECK("trylock_self_owned", _umtx_op(&m, UMTX_OP_MUTEX_TRYLOCK, 0, NULL, NULL));
        CHECK("lock_self_owned", _umtx_op(&m, UMTX_OP_MUTEX_LOCK, 0, (void*)sizeof zero, &zero));
        CHECK("unlock_owned", _umtx_op(&m, UMTX_OP_MUTEX_UNLOCK, 0, NULL, NULL));
        NOTE("unlock_owned_owner", "%s", owner_state(&m));
        CHECK("unlock_unowned", _umtx_op(&m, UMTX_OP_MUTEX_UNLOCK, 0, NULL, NULL));
        CHECK("lock_free", _umtx_op(&m, UMTX_OP_MUTEX_LOCK, 0, NULL, NULL));
        _umtx_op(&m, UMTX_OP_MUTEX_UNLOCK, 0, NULL, NULL);
        m.m_flags = UMUTEX_ROBUST;
        _umtx_op(&m, UMTX_OP_MUTEX_TRYLOCK, 0, NULL, NULL);
        CHECK("robust_trylock_self_owned", _umtx_op(&m, UMTX_OP_MUTEX_TRYLOCK, 0, NULL, NULL));
        _umtx_op(&m, UMTX_OP_MUTEX_UNLOCK, 0, NULL, NULL);
    }
    {
        struct umutex m = {0};
        m.m_owner = 12345;
        CHECK("unlock_other_owner", _umtx_op(&m, UMTX_OP_MUTEX_UNLOCK, 0, NULL, NULL));
        CHECK("trylock_other_owner", _umtx_op(&m, UMTX_OP_MUTEX_TRYLOCK, 0, NULL, NULL));
        CHECK("lock_other_owner_timeout_20ms", _umtx_op(&m, UMTX_OP_MUTEX_LOCK, 0, (void*)sizeof t20, &t20));
        NOTE("lock_other_owner_timeout_owner_bits", "%s", (m.m_owner & UMUTEX_CONTESTED) ? "contested_set" : "contested_clear");
        m.m_owner = 12345;
        CHECK("mutex_wait_held_timeout_20ms", _umtx_op(&m, UMTX_OP_MUTEX_WAIT, 0, (void*)sizeof t20, &t20));
        m.m_owner = UMUTEX_UNOWNED;
        CHECK("mutex_wait_free", _umtx_op(&m, UMTX_OP_MUTEX_WAIT, 0, (void*)sizeof t20, &t20));
        CHECK("mutex_wake2_free", _umtx_op(&m, UMTX_OP_MUTEX_WAKE2, 0, NULL, NULL));
        NOTE("mutex_wake_free", "SKIPPED: op 18 on this mutex livelocks the FreeBSD 14.4 kernel (unkillable spin)");
        m.m_owner = UMUTEX_CONTESTED;
        CHECK("trylock_contested_only", _umtx_op(&m, UMTX_OP_MUTEX_TRYLOCK, 0, NULL, NULL));
        NOTE("trylock_contested_only_owner", "%s", owner_state(&m));
    }
    {
        struct umutex m = {0};
        m.m_flags = UMUTEX_PRIO_INHERIT | UMUTEX_PRIO_PROTECT;
        CHECK("trylock_pi_and_pp", _umtx_op(&m, UMTX_OP_MUTEX_TRYLOCK, 0, NULL, NULL));
        m.m_flags = UMUTEX_PRIO_INHERIT;
        CHECK("trylock_pi", _umtx_op(&m, UMTX_OP_MUTEX_TRYLOCK, 0, NULL, NULL));
        NOTE("trylock_pi_owner", "%s", owner_state(&m));
        CHECK("unlock_pi", _umtx_op(&m, UMTX_OP_MUTEX_UNLOCK, 0, NULL, NULL));
        m.m_flags = UMUTEX_PRIO_PROTECT;
        m.m_ceilings[0] = 0;
        CHECK("trylock_pp", _umtx_op(&m, UMTX_OP_MUTEX_TRYLOCK, 0, NULL, NULL));
        CHECK("unlock_pp", _umtx_op(&m, UMTX_OP_MUTEX_UNLOCK, 0, NULL, NULL));
        uint32_t old = 77;
        GUARDED("set_ceiling_pp", _umtx_op(&m, UMTX_OP_SET_CEILING, 1, &old, NULL));
        m.m_flags = 0;
        GUARDED("set_ceiling_normal", _umtx_op(&m, UMTX_OP_SET_CEILING, 1, &old, NULL));
        CHECK("trylock_null", _umtx_op(NULL, UMTX_OP_MUTEX_TRYLOCK, 0, NULL, NULL));
        CHECK("unlock_null", _umtx_op(NULL, UMTX_OP_MUTEX_UNLOCK, 0, NULL, NULL));
    }
    {
        pthread_t t;
        memset(&shared_m, 0, sizeof shared_m);
        phase = 0;
        pthread_create(&t, NULL, holder, NULL);
        while (phase != 1) usleep(1000);
        CHECK("trylock_held_by_thread", _umtx_op(&shared_m, UMTX_OP_MUTEX_TRYLOCK, 0, NULL, NULL));
        CHECK("unlock_held_by_thread", _umtx_op(&shared_m, UMTX_OP_MUTEX_UNLOCK, 0, NULL, NULL));
        phase = 2;
        CHECK("lock_after_release", _umtx_op(&shared_m, UMTX_OP_MUTEX_LOCK, 0, NULL, NULL));
        NOTE("lock_after_release_owner", "%s", owner_state(&shared_m));
        pthread_join(t, NULL);
        _umtx_op(&shared_m, UMTX_OP_MUTEX_UNLOCK, 0, NULL, NULL);
    }
    {
        struct umutex m = {0};
        struct ucond cv = {0};
        CHECK("cv_signal_no_waiters", _umtx_op(&cv, UMTX_OP_CV_SIGNAL, 0, NULL, NULL));
        CHECK("cv_broadcast_no_waiters", _umtx_op(&cv, UMTX_OP_CV_BROADCAST, 0, NULL, NULL));
        CHECK("cv_wait_mutex_not_owned", _umtx_op(&cv, UMTX_OP_CV_WAIT, 0, &m, (void*)&t20));
        NOTE("cv_wait_mutex_not_owned_has_waiters", "%u", cv.c_has_waiters);
        _umtx_op(&m, UMTX_OP_MUTEX_LOCK, 0, NULL, NULL);
        CHECK("cv_wait_timeout_20ms", _umtx_op(&cv, UMTX_OP_CV_WAIT, 0, &m, (void*)&t20));
        NOTE("cv_wait_timeout_mutex_after", "%s", owner_state(&m));
        NOTE("cv_wait_timeout_has_waiters_after", "%u", cv.c_has_waiters);
        if (m.m_owner != UMUTEX_UNOWNED) _umtx_op(&m, UMTX_OP_MUTEX_UNLOCK, 0, NULL, NULL);
        _umtx_op(&m, UMTX_OP_MUTEX_LOCK, 0, NULL, NULL);
        struct timespec past;
        clock_gettime(CLOCK_MONOTONIC, &past);
        past.tv_sec -= 1;
        cv.c_clockid = CLOCK_MONOTONIC;
        CHECK("cv_wait_abstime_clockid_past", _umtx_op(&cv, UMTX_OP_CV_WAIT, CVWAIT_ABSTIME | CVWAIT_CLOCKID, &m, &past));
        if (m.m_owner != UMUTEX_UNOWNED) _umtx_op(&m, UMTX_OP_MUTEX_UNLOCK, 0, NULL, NULL);
        _umtx_op(&m, UMTX_OP_MUTEX_LOCK, 0, NULL, NULL);
        cv.c_clockid = 99;
        GUARDED("cv_wait_bad_clockid", _umtx_op(&cv, UMTX_OP_CV_WAIT, CVWAIT_ABSTIME | CVWAIT_CLOCKID, &m, &past));
        NOTE("cv_wait_bad_clockid_mutex_after", "%s", owner_state(&m));
        if (m.m_owner != UMUTEX_UNOWNED) _umtx_op(&m, UMTX_OP_MUTEX_UNLOCK, 0, NULL, NULL);
        struct timespec bad = {0, 1000000000};
        _umtx_op(&m, UMTX_OP_MUTEX_LOCK, 0, NULL, NULL);
        CHECK("cv_wait_bad_nsec", _umtx_op(&cv, UMTX_OP_CV_WAIT, 0, &m, &bad));
        NOTE("cv_wait_bad_nsec_mutex_after", "%s", owner_state(&m));
        if (m.m_owner != UMUTEX_UNOWNED) _umtx_op(&m, UMTX_OP_MUTEX_UNLOCK, 0, NULL, NULL);
    }
    {
        pthread_t t;
        memset(&shared_m, 0, sizeof shared_m);
        memset(&shared_cv, 0, sizeof shared_cv);
        _umtx_op(&shared_m, UMTX_OP_MUTEX_LOCK, 0, NULL, NULL);
        pthread_create(&t, NULL, cv_signaller, NULL);
        struct timespec two = {2, 0};
        CHECK("cv_wait_signalled", _umtx_op(&shared_cv, UMTX_OP_CV_WAIT, 0, &shared_m, &two));
        NOTE("cv_wait_signalled_mutex_after", "%s", owner_state(&shared_m));
        pthread_join(t, NULL);
        if (shared_m.m_owner != UMUTEX_UNOWNED) _umtx_op(&shared_m, UMTX_OP_MUTEX_UNLOCK, 0, NULL, NULL);
    }
    {
        struct urwlock rw = {0};
        CHECK("rw_unlock_unlocked", _umtx_op(&rw, UMTX_OP_RW_UNLOCK, 0, NULL, NULL));
        CHECK("rw_rdlock_free", _umtx_op(&rw, UMTX_OP_RW_RDLOCK, 0, NULL, NULL));
        NOTE("rw_rdlock_free_readers", "%u", URWLOCK_READER_COUNT(rw.rw_state));
        CHECK("rw_unlock_read", _umtx_op(&rw, UMTX_OP_RW_UNLOCK, 0, NULL, NULL));
        CHECK("rw_wrlock_free", _umtx_op(&rw, UMTX_OP_RW_WRLOCK, 0, NULL, NULL));
        NOTE("rw_wrlock_free_state", "%s", (rw.rw_state & URWLOCK_WRITE_OWNER) ? "write_owner" : "no_write_owner");
        CHECK("rw_unlock_write", _umtx_op(&rw, UMTX_OP_RW_UNLOCK, 0, NULL, NULL));
        pthread_t t;
        memset(&shared_rw, 0, sizeof shared_rw);
        phase = 0;
        pthread_create(&t, NULL, rw_reader, NULL);
        while (phase != 1) usleep(1000);
        struct _umtx_time ut = {{0, 20000000}, 0, CLOCK_MONOTONIC};
        CHECK("rw_wrlock_while_read_held_timeout", _umtx_op(&shared_rw, UMTX_OP_RW_WRLOCK, 0, (void*)sizeof ut, &ut));
        CHECK("rw_rdlock_while_read_held", _umtx_op(&shared_rw, UMTX_OP_RW_RDLOCK, 0, (void*)sizeof ut, &ut));
        _umtx_op(&shared_rw, UMTX_OP_RW_UNLOCK, 0, NULL, NULL);
        phase = 2;
        pthread_join(t, NULL);
    }
    {
        struct _usem2 s = {0, 0};
        CHECK("sem2_wait_count0_timeout_20ms", _umtx_op(&s, UMTX_OP_SEM2_WAIT, 0, (void*)sizeof t20, &t20));
        NOTE("sem2_wait_timeout_has_waiters_bit", "%s", (s._count & USEM_HAS_WAITERS) ? "set" : "clear");
        s._count = 1;
        CHECK("sem2_wait_count1", _umtx_op(&s, UMTX_OP_SEM2_WAIT, 0, (void*)sizeof t20, &t20));
        NOTE("sem2_wait_count1_count_after", "%u", USEM_COUNT(s._count));
        CHECK("sem2_wake_no_waiters", _umtx_op(&s, UMTX_OP_SEM2_WAKE, 0, NULL, NULL));
        CHECK("sem2_wait_null", _umtx_op(NULL, UMTX_OP_SEM2_WAIT, 0, NULL, NULL));
        struct old_usem os = {0, 0, 0};
        CHECK("sem_old_wait_count0_timeout_20ms", _umtx_op(&os, UMTX_OP_SEM_WAIT, 0, (void*)sizeof t20, &t20));
        NOTE("sem_old_wait_timeout_has_waiters", "%u", os.has_waiters);
        os.count = 1;
        CHECK("sem_old_wait_count1", _umtx_op(&os, UMTX_OP_SEM_WAIT, 0, (void*)sizeof t20, &t20));
        NOTE("sem_old_wait_count1_count_after", "%u", os.count);
        CHECK("sem_old_wake_no_waiters", _umtx_op(&os, UMTX_OP_SEM_WAKE, 0, NULL, NULL));
    }
    {
        static int key;
        int fd = _umtx_op(NULL, UMTX_OP_SHM, UMTX_SHM_CREAT, &key, NULL);
        NOTE("shm_creat", "%s", fd >= 0 ? "fd" : errname(errno));
        if (fd >= 0) close(fd);
        int fd2 = _umtx_op(NULL, UMTX_OP_SHM, UMTX_SHM_LOOKUP, &key, NULL);
        NOTE("shm_lookup_existing", "%s", fd2 >= 0 ? "fd" : errname(errno));
        if (fd2 >= 0) close(fd2);
        CHECK("shm_destroy", _umtx_op(NULL, UMTX_OP_SHM, UMTX_SHM_DESTROY, &key, NULL));
        CHECK("shm_lookup_destroyed", _umtx_op(NULL, UMTX_OP_SHM, UMTX_SHM_LOOKUP, &key, NULL));
        CHECK("shm_bad_flags", _umtx_op(NULL, UMTX_OP_SHM, 0x100, &key, NULL));
        CHECK("shm_two_flags", _umtx_op(NULL, UMTX_OP_SHM, UMTX_SHM_CREAT | UMTX_SHM_LOOKUP, &key, NULL));
    }
    {
        struct umtx_robust_lists_params rb = {0, 0, 0};
        CHECK("robust_lists_bad_size", _umtx_op(NULL, UMTX_OP_ROBUST_LISTS, 1, &rb, NULL));
        CHECK("robust_lists_ok", _umtx_op(NULL, UMTX_OP_ROBUST_LISTS, sizeof rb, &rb, NULL));
    }
    return 0;
}
