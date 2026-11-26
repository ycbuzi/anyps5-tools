#include <sys/mman.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdlib.h>
#include <unistd.h>
#include "probe.h"

static long page;

static void map_case(const char* label, void* addr, size_t len, int prot, int flags, int fd, off_t off) {
    errno = 0;
    void* p = mmap(addr, len, prot, flags, fd, off);
    if (p == MAP_FAILED) {
        NOTE(label, "MAP_FAILED errno=%s", errname(errno));
        return;
    }
    const char* where = addr == NULL ? "kernel-chosen" : (p == addr ? "at-hint" : "elsewhere");
    NOTE(label, "mapped %s", where);
    munmap(p, len ? len : 1);
}

static char* fresh(int pages) {
    return mmap(NULL, pages * page, PROT_READ | PROT_WRITE, MAP_ANON | MAP_PRIVATE, -1, 0);
}

int main(void) {
    probe_name = "mmap_map";
    page = sysconf(_SC_PAGESIZE);
    NOTE("page_size", "%ld", page);
    map_case("anon_private", NULL, page, PROT_READ, MAP_ANON | MAP_PRIVATE, -1, 0);
    map_case("anon_no_share_type", NULL, page, PROT_READ, MAP_ANON, -1, 0);
    map_case("anon_shared", NULL, page, PROT_READ, MAP_ANON | MAP_SHARED, -1, 0);
    map_case("anon_shared_and_private", NULL, page, PROT_READ, MAP_ANON | MAP_SHARED | MAP_PRIVATE, -1, 0);
    map_case("anon_length_0", NULL, 0, PROT_READ, MAP_ANON | MAP_PRIVATE, -1, 0);
    map_case("anon_unaligned_length", NULL, page + 1, PROT_READ, MAP_ANON | MAP_PRIVATE, -1, 0);
    map_case("anon_fd_not_minus1", NULL, page, PROT_READ, MAP_ANON | MAP_PRIVATE, 0, 0);
    map_case("anon_offset_nonzero", NULL, page, PROT_READ, MAP_ANON | MAP_PRIVATE, -1, page);
    map_case("anon_prot_bit8", NULL, page, 8, MAP_ANON | MAP_PRIVATE, -1, 0);
    map_case("anon_prot_none", NULL, page, PROT_NONE, MAP_ANON | MAP_PRIVATE, -1, 0);
    map_case("anon_prot_exec", NULL, page, PROT_READ | PROT_EXEC, MAP_ANON | MAP_PRIVATE, -1, 0);
    map_case("anon_prot_max_rw", NULL, page, PROT_READ | PROT_MAX(PROT_READ | PROT_WRITE), MAP_ANON | MAP_PRIVATE, -1, 0);
    map_case("excl_without_fixed", NULL, page, PROT_READ, MAP_ANON | MAP_PRIVATE | MAP_EXCL, -1, 0);
    map_case("unknown_flag_0x20000000", NULL, page, PROT_READ, MAP_ANON | MAP_PRIVATE | 0x20000000, -1, 0);
    map_case("aligned_super", NULL, 2 * 1024 * 1024, PROT_READ, MAP_ANON | MAP_PRIVATE | MAP_ALIGNED_SUPER, -1, 0);
    map_case("no_fd_not_anon", NULL, page, PROT_READ, MAP_PRIVATE, -1, 0);
    {
        char* base = fresh(2);
        map_case("fixed_unaligned_addr", base + 1, page, PROT_READ, MAP_ANON | MAP_PRIVATE | MAP_FIXED, -1, 0);
        munmap(base, 2 * page);
    }
    {
        char* base = fresh(2);
        map_case("fixed_over_existing", base, page, PROT_READ, MAP_ANON | MAP_PRIVATE | MAP_FIXED, -1, 0);
        munmap(base, 2 * page);
    }
    {
        char* base = fresh(2);
        map_case("fixed_excl_over_existing", base, page, PROT_READ, MAP_ANON | MAP_PRIVATE | MAP_FIXED | MAP_EXCL, -1, 0);
        munmap(base, 2 * page);
    }
    {
        char* base = fresh(3);
        munmap(base + page, page);
        map_case("fixed_into_hole", base + page, page, PROT_READ, MAP_ANON | MAP_PRIVATE | MAP_FIXED, -1, 0);
        munmap(base, 3 * page);
    }
    {
        char* base = fresh(2);
        map_case("hint_over_existing", base, page, PROT_READ, MAP_ANON | MAP_PRIVATE, -1, 0);
        munmap(base, 2 * page);
    }
    {
        char* base = fresh(2);
        munmap(base, 2 * page);
        map_case("hint_into_free_range", base, page, PROT_READ, MAP_ANON | MAP_PRIVATE, -1, 0);
    }
    map_case("hint_unaligned_free", (void*)(uintptr_t)0x200000001ULL, page, PROT_READ, MAP_ANON | MAP_PRIVATE, -1, 0);
    {
        int fd = open("/etc/passwd", O_RDONLY);
        map_case("file_private_readonly_fd_write", NULL, page, PROT_READ | PROT_WRITE, MAP_PRIVATE, fd, 0);
        map_case("file_shared_readonly_fd_write", NULL, page, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
        map_case("file_offset_unaligned", NULL, page, PROT_READ, MAP_PRIVATE, fd, 1);
        map_case("file_offset_negative", NULL, page, PROT_READ, MAP_PRIVATE, fd, -(off_t)page);
        map_case("file_beyond_eof", NULL, page, PROT_READ, MAP_PRIVATE, fd, 1024 * page);
        close(fd);
        map_case("file_closed_fd", NULL, page, PROT_READ, MAP_PRIVATE, fd, 0);
        int wfd = open("/dev/null", O_WRONLY);
        map_case("file_writeonly_fd_read", NULL, page, PROT_READ, MAP_SHARED, wfd, 0);
        close(wfd);
        int dfd = open("/", O_RDONLY | O_DIRECTORY);
        map_case("file_directory_fd", NULL, page, PROT_READ, MAP_PRIVATE, dfd, 0);
        close(dfd);
    }
    return 0;
}
