#include <sys/mman.h>
#include <stdint.h>
#include <unistd.h>
#include "probe.h"

static long page;

static char* fresh(int pages) {
    return mmap(NULL, pages * page, PROT_READ | PROT_WRITE, MAP_ANON | MAP_PRIVATE, -1, 0);
}

static const struct { const char* name; int advice; } advices[] = {
    {"NORMAL", MADV_NORMAL}, {"RANDOM", MADV_RANDOM}, {"SEQUENTIAL", MADV_SEQUENTIAL}, {"WILLNEED", MADV_WILLNEED},
    {"DONTNEED", MADV_DONTNEED}, {"FREE", MADV_FREE}, {"NOSYNC", MADV_NOSYNC}, {"AUTOSYNC", MADV_AUTOSYNC},
    {"NOCORE", MADV_NOCORE}, {"CORE", MADV_CORE},
};

int main(void) {
    probe_name = "mmap_advise";
    page = sysconf(_SC_PAGESIZE);
    char label[96];
    for (size_t i = 0; i < sizeof advices / sizeof advices[0]; ++i) {
        char* b = fresh(3);
        snprintf(label, sizeof label, "madvise_%s_mapped", advices[i].name);
        CHECK(label, madvise(b, 3 * page, advices[i].advice));
        munmap(b + page, page);
        snprintf(label, sizeof label, "madvise_%s_over_hole", advices[i].name);
        CHECK(label, madvise(b, 3 * page, advices[i].advice));
        munmap(b, 3 * page);
        snprintf(label, sizeof label, "madvise_%s_unmapped", advices[i].name);
        CHECK(label, madvise(b, page, advices[i].advice));
    }
    { char* b = fresh(1); CHECK("madvise_advice_minus1", madvise(b, page, -1)); CHECK("madvise_advice_99", madvise(b, page, 99)); CHECK("madvise_unaligned", madvise(b + 1, 16, MADV_NORMAL)); CHECK("madvise_length_0", madvise(b, 0, MADV_NORMAL)); munmap(b, page); }
    CHECK("madvise_kernel_addr", madvise((void*)(uintptr_t)0xffff800000000000ULL, page, MADV_NORMAL));
    CHECK("madvise_protect_null", madvise(NULL, 0, MADV_PROTECT));
    { char* b = fresh(1); CHECK_ERR("posix_madvise_99", posix_madvise(b, page, 99)); CHECK_ERR("posix_madvise_normal", posix_madvise(b, page, POSIX_MADV_NORMAL)); munmap(b, page); }
    { char* b = fresh(1); b[0] = 1; CHECK("msync_async", msync(b, page, MS_ASYNC)); CHECK("msync_sync", msync(b, page, MS_SYNC)); CHECK("msync_invalidate", msync(b, page, MS_INVALIDATE)); CHECK("msync_async_invalidate", msync(b, page, MS_ASYNC | MS_INVALIDATE)); CHECK("msync_unaligned", msync(b + 1, 16, MS_SYNC)); CHECK("msync_length_0", msync(b, 0, MS_SYNC)); munmap(b, page); }
    { char* b = fresh(3); munmap(b + page, page); CHECK("msync_over_hole", msync(b, 3 * page, MS_SYNC)); munmap(b, 3 * page); CHECK("msync_unmapped", msync(b, page, MS_SYNC)); CHECK("msync_unmapped_length_0", msync(b, 0, MS_SYNC)); }
    { char* b = fresh(1); CHECK("mlock_page", mlock(b, page)); CHECK("munlock_page", munlock(b, page)); CHECK("munlock_not_locked", munlock(b, page)); munmap(b, page); CHECK("mlock_unmapped", mlock(b, page)); }
    CHECK("mlockall_0", mlockall(0));
    CHECK("mlockall_bad_flag", mlockall(8));
    CHECK("mlockall_current", mlockall(MCL_CURRENT));
    CHECK("munlockall", munlockall());
    return 0;
}
