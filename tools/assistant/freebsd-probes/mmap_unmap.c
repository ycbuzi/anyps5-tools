#include <sys/mman.h>
#include <stdint.h>
#include <unistd.h>
#include "probe.h"

static long page;

static char* fresh(int pages) {
    return mmap(NULL, pages * page, PROT_READ | PROT_WRITE, MAP_ANON | MAP_PRIVATE, -1, 0);
}

static int still_mapped(char* p) {
    char v;
    return mincore(p, page, &v) == 0;
}

int main(void) {
    probe_name = "mmap_unmap";
    page = sysconf(_SC_PAGESIZE);
    { char* b = fresh(4); CHECK("middle_page", munmap(b + page, page)); NOTE("middle_page_neighbours_mapped", "%d%d", still_mapped(b), still_mapped(b + 2 * page)); munmap(b, 4 * page); }
    { char* b = fresh(4); munmap(b + page, page); CHECK("already_unmapped_page", munmap(b + page, page)); munmap(b, 4 * page); }
    { char* b = fresh(4); munmap(b + page, page); CHECK("range_spanning_hole", munmap(b, 3 * page)); NOTE("range_spanning_hole_tail_mapped", "%d", still_mapped(b + 3 * page)); munmap(b, 4 * page); }
    { char* b = fresh(2); munmap(b, 2 * page); CHECK("never_mapped_again", munmap(b, 2 * page)); }
    { char* b = fresh(2); CHECK("unaligned_addr", munmap(b + 1, page)); NOTE("unaligned_addr_first_page_mapped", "%d", still_mapped(b)); munmap(b, 2 * page); }
    { char* b = fresh(2); CHECK("unaligned_length", munmap(b, page + 1)); NOTE("unaligned_length_second_page_mapped", "%d", still_mapped(b + page)); munmap(b, 2 * page); }
    { char* b = fresh(2); CHECK("length_0", munmap(b, 0)); munmap(b, 2 * page); }
    CHECK("null_addr", munmap(NULL, page));
    CHECK("addr_wraps", munmap((void*)(uintptr_t)(UINTPTR_MAX & ~(uintptr_t)(page - 1)), 2 * page));
    CHECK("kernel_half_addr", munmap((void*)(uintptr_t)0xffff800000000000ULL, page));
    { char* b = fresh(2); CHECK("huge_length", munmap(b, (size_t)1 << 62)); munmap(b, 2 * page); }
    { char* b = fresh(1); char v; munmap(b, page); errno = 0; int r = mincore(b, page, &v); NOTE("mincore_unmapped", "rc=%d errno=%s", r, r ? errname(errno) : "0"); }
    { char* b = fresh(2); munmap(b + page, page); unsigned char v[2]; errno = 0; int r = mincore(b, 2 * page, (char*)v); NOTE("mincore_over_hole", "rc=%d errno=%s", r, r ? errname(errno) : "0"); munmap(b, 2 * page); }
    return 0;
}
