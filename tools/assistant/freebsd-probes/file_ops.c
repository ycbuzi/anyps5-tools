#include <sys/types.h>
#include <sys/stat.h>
#include <sys/uio.h>
#include <dirent.h>
#include <fcntl.h>
#include <limits.h>
#include <stdlib.h>
#include <unistd.h>
#include "probe.h"

static char dir[] = "/tmp/fileops.XXXXXX";
static char path[PATH_MAX];

static const char* at(const char* name) {
    snprintf(path, sizeof path, "%s/%s", dir, name);
    return path;
}

static void sizeof_fd(const char* label, int fd) {
    struct stat st;
    if (fstat(fd, &st) == 0) NOTE(label, "size=%lld", (long long)st.st_size);
    else NOTE(label, "fstat errno=%s", errname(errno));
}

int main(void) {
    probe_name = "file_ops";
    if (!mkdtemp(dir)) return 1;
    char buf[64];

    CHECK("open_missing_rdonly", open(at("missing"), O_RDONLY));
    CHECK("open_creat_excl_new", open(at("a"), O_RDWR | O_CREAT | O_EXCL, 0644));
    CHECK("open_creat_excl_existing", open(at("a"), O_RDWR | O_CREAT | O_EXCL, 0644));
    CHECK("open_dir_rdwr", open(dir, O_RDWR));
    CHECK("open_dir_rdonly_directory", open(dir, O_RDONLY | O_DIRECTORY) >= 0);
    CHECK("open_file_with_directory_flag", open(at("a"), O_RDONLY | O_DIRECTORY));
    CHECK("open_trailing_slash_on_file", open(strcat(strcpy(buf, at("a")), "/"), O_RDONLY));

    int fd = open(at("a"), O_RDWR);
    CHECK("write_hello", write(fd, "hello", 5));
    CHECK("lseek_end", lseek(fd, 0, SEEK_END));
    CHECK("lseek_negative", lseek(fd, -10, SEEK_SET));
    CHECK("lseek_past_end", lseek(fd, 100, SEEK_SET));
    CHECK("read_past_end", read(fd, buf, sizeof buf));
    CHECK("write_after_gap", write(fd, "x", 1));
    sizeof_fd("size_after_gap_write", fd);
    CHECK("lseek_whence_bad", lseek(fd, 0, 7));
    CHECK("pread_offset0", pread(fd, buf, 5, 0));
    CHECK("pread_negative_offset", pread(fd, buf, 5, -1));
    CHECK("pwrite_keeps_position", (pwrite(fd, "HE", 2, 0), lseek(fd, 0, SEEK_CUR)));
    CHECK("read_zero_len", read(fd, buf, 0));
    CHECK("write_zero_len", write(fd, buf, 0));
    CHECK("ftruncate_shrink", ftruncate(fd, 3));
    sizeof_fd("size_after_shrink", fd);
    CHECK("ftruncate_negative", ftruncate(fd, -1));
    CHECK("position_after_shrink", lseek(fd, 0, SEEK_CUR));
    struct iovec iov[2] = {{"ab", 2}, {"cd", 2}};
    CHECK("writev_two", (lseek(fd, 0, SEEK_SET), writev(fd, iov, 2)));
    CHECK("readv_iovcnt_zero", readv(fd, iov, 0));
    close(fd);

    fd = open(at("ro"), O_RDONLY | O_CREAT, 0644);
    CHECK("write_on_rdonly", write(fd, "x", 1));
    CHECK("ftruncate_on_rdonly", ftruncate(fd, 0));
    close(fd);
    fd = open(at("wo"), O_WRONLY | O_CREAT, 0644);
    CHECK("read_on_wronly", read(fd, buf, 1));
    close(fd);

    fd = open(at("ap"), O_WRONLY | O_CREAT | O_APPEND, 0644);
    write(fd, "12345", 5);
    lseek(fd, 0, SEEK_SET);
    CHECK("append_write_after_seek0", write(fd, "Z", 1));
    CHECK("append_position_after", lseek(fd, 0, SEEK_CUR));
    CHECK("append_pwrite_offset0", pwrite(fd, "Q", 1, 0));
    sizeof_fd("append_size_after_pwrite", fd);
    close(fd);

    CHECK("close_twice_first", close(fd));
    CHECK("read_closed_fd", read(fd, buf, 1));
    CHECK("fstat_bad_fd", fstat(-1, NULL));
    fd = open(at("a"), O_RDONLY);
    int d = dup(fd);
    CHECK("dup_shares_offset", (lseek(fd, 2, SEEK_SET), lseek(d, 0, SEEK_CUR)));
    CHECK("dup2_same_fd", dup2(fd, fd) == fd);
    CHECK("fcntl_getfl_rdonly", fcntl(fd, F_GETFL) & O_ACCMODE);
    CHECK("fcntl_cloexec_default", fcntl(fd, F_GETFD));
    CHECK("fcntl_bad_cmd", fcntl(fd, 9999));
    close(d);
    close(fd);

    CHECK("rename_over_existing", rename(at("ro"), at("wo")));
    CHECK("rename_missing", rename(at("ro"), at("x")));
    CHECK("rename_file_over_dir", (mkdir(at("sub"), 0755), rename(at("wo"), at("sub"))));
    CHECK("unlink_dir", unlink(at("sub")));
    CHECK("rmdir_nonempty", (open(at("sub/f"), O_CREAT | O_WRONLY, 0644), rmdir(at("sub"))));
    CHECK("mkdir_existing", mkdir(at("sub"), 0755));
    CHECK("unlink_missing", unlink(at("missing")));
    CHECK("stat_through_file", access(at("a/b"), F_OK));

    fd = open(at("a"), O_RDWR);
    unlink(at("a"));
    CHECK("write_after_unlink", write(fd, "y", 1));
    sizeof_fd("fstat_after_unlink", fd);
    close(fd);

    DIR* dp = opendir(dir);
    int entries = 0;
    struct dirent* de;
    while ((de = readdir(dp))) ++entries;
    NOTE("readdir_count_with_dots", "n=%d", entries);
    closedir(dp);

    char cmd[PATH_MAX + 16];
    snprintf(cmd, sizeof cmd, "rm -rf %s", dir);
    system(cmd);
    return 0;
}
