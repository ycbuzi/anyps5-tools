#include <sys/mman.h>
#include <setjmp.h>
#include <signal.h>
#include <stdint.h>
#include <unistd.h>
#include "probe.h"

static long page;
static sigjmp_buf jump;
static void on_fault(int sig) { siglongjmp(jump, sig); }

static char* fresh(int pages) {
    return mmap(NULL, pages * page, PROT_READ | PROT_WRITE, MAP_ANON | MAP_PRIVATE, -1, 0);
}

static const char* writable(volatile char* p) {
    int sig = sigsetjmp(jump, 1);
    if (sig == SIGSEGV) return "SIGSEGV";
    if (sig == SIGBUS) return "SIGBUS";
    *p = 1;
    return "ok";
}

int main(void) {
    probe_name = "mmap_protect";
    page = sysconf(_SC_PAGESIZE);
    signal(SIGSEGV, on_fault);
    signal(SIGBUS, on_fault);
    { char* b = fresh(3); munmap(b + page, page); CHECK("over_hole", mprotect(b, 3 * page, PROT_READ)); NOTE("over_hole_first_page_write", "%s", writable(b)); NOTE("over_hole_last_page_write", "%s", writable(b + 2 * page)); munmap(b, 3 * page); }
    { char* b = fresh(1); munmap(b, page); CHECK("fully_unmapped", mprotect(b, page, PROT_READ)); }
    { char* b = fresh(2); CHECK("unaligned_addr", mprotect(b + 1, page, PROT_READ)); NOTE("unaligned_addr_page0_write", "%s", writable(b)); NOTE("unaligned_addr_page1_write", "%s", writable(b + page)); munmap(b, 2 * page); }
    { char* b = fresh(2); CHECK("unaligned_length", mprotect(b, page + 1, PROT_READ)); NOTE("unaligned_length_page1_write", "%s", writable(b + page)); munmap(b, 2 * page); }
    { char* b = fresh(1); CHECK("length_0", mprotect(b, 0, PROT_READ)); NOTE("length_0_write", "%s", writable(b)); munmap(b, page); }
    CHECK("null_addr", mprotect(NULL, page, PROT_READ));
    CHECK("null_addr_length_0", mprotect(NULL, 0, PROT_READ));
    { char* b = fresh(1); CHECK("prot_bit8", mprotect(b, page, PROT_READ | 8)); munmap(b, page); }
    { char* b = fresh(1); CHECK("prot_none", mprotect(b, page, PROT_NONE)); NOTE("prot_none_write", "%s", writable(b)); munmap(b, page); }
    { char* b = fresh(1); CHECK("prot_write_only", mprotect(b, page, PROT_WRITE)); NOTE("prot_write_only_write", "%s", writable(b)); munmap(b, page); }
    { char* b = mmap(NULL, page, PROT_READ | PROT_MAX(PROT_READ), MAP_ANON | MAP_PRIVATE, -1, 0); CHECK("above_prot_max", mprotect(b, page, PROT_READ | PROT_WRITE)); munmap(b, page); }
    { char* b = fresh(1); CHECK("wraps", mprotect(b, (size_t)0 - (uintptr_t)b + page, PROT_READ)); munmap(b, page); }
    return 0;
}
