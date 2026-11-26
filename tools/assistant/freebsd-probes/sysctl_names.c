#include <sys/types.h>
#include <sys/sysctl.h>
#include <sys/syscall.h>
#include <sys/user.h>
#include <stdint.h>
#include <stdlib.h>
#include <unistd.h>
#include "probe.h"

static const char* names[] = {
    "kern.ostype", "kern.osrelease", "kern.version", "kern.hostname", "hw.model", "hw.machine",
    "kern.osrevision", "kern.argmax", "kern.ngroups", "kern.osreldate",
    "hw.byteorder", "hw.cacheline_size", "hw.floatingpoint", "hw.machine_arch", "hw.usermem", "kern.bootfile",
    "kern.clockrate", "kern.cp_time", "kern.ident", "kern.maxfiles", "kern.maxfilesperproc", "kern.maxproc",
    "kern.ps_strings", "kern.sched.name", "kern.smp.maxcpus", "kern.stackprot", "kern.timecounter.hardware",
    "machdep.cacheline_size", "kern.proc.pathname", "kern.boottime", "vm.loadavg", "kern.arandom", "kern.rng_pseudo",
    "vm.overcommit", "kern.sdk_version", "machdep.tsc_freq", "machdep.upd_version", "machdep.curr_manumode",
    "machdep.rcmgr_intdev", "machdep.rcmgr_intdev_for_internal_libc", "hw.pagesize", "hw.ncpu", "hw.physmem", "hw.realmem",
    "kern.usrstack", "kern.smp.cpus", "hw.availpages", "kern.proc.pid", "kern.proc.vmmap", "kern.hz", "kern.pid_max",
    "kern.ipc.shmmax", "vm.stats.vm.v_page_count", "kern.osreldate.", "hw.pagesize.",
};

static const char* stable_values[] = {"kern.ostype", "kern.argmax", "kern.ngroups", "hw.byteorder", "hw.floatingpoint",
    "hw.machine", "hw.machine_arch", "hw.pagesize", "kern.stackprot", "kern.sched.name", "vm.overcommit", "kern.osrevision",
    "kern.smp.maxcpus", "kern.hz", "kern.pid_max", "hw.ncpu"};

static int is_stable(const char* n) {
    for (size_t i = 0; i < sizeof stable_values / sizeof stable_values[0]; ++i)
        if (strcmp(n, stable_values[i]) == 0) return 1;
    return 0;
}

static void describe(const char* n) {
    int mib[CTL_MAXNAME];
    size_t len = CTL_MAXNAME;
    char label[96];
    snprintf(label, sizeof label, "name.%s", n);
    if (sysctlnametomib(n, mib, &len) != 0) {
        NOTE(label, "nametomib errno=%s", errname(errno));
        return;
    }
    char mibs[128] = "";
    for (size_t i = 0; i < len; ++i) {
        char part[16];
        snprintf(part, sizeof part, "%s%d", i ? "." : "", mib[i] >= 256 ? -1 : mib[i]);
        strncat(mibs, part, sizeof mibs - strlen(mibs) - 1);
    }
    int qmib[CTL_MAXNAME + 2] = {0, 4};
    memcpy(qmib + 2, mib, len * sizeof(int));
    unsigned char fmt[256];
    size_t flen = sizeof fmt;
    char kind[64] = "?";
    unsigned ktype = 0;
    if (sysctl(qmib, len + 2, fmt, &flen, NULL, 0) == 0) {
        unsigned k;
        memcpy(&k, fmt, sizeof k);
        ktype = k & CTLTYPE;
        snprintf(kind, sizeof kind, "type=%u%s%s fmt=%s", k & CTLTYPE, (k & CTLFLAG_WR) ? " wr" : " ro", (k & CTLTYPE) == CTLTYPE_NODE ? " node" : "", (char*)fmt + sizeof k);
    }
    size_t size = 0;
    errno = 0;
    int r = sysctl(mib, len, NULL, &size, NULL, 0);
    if (r != 0) {
        NOTE(label, "mib=%s %s size_query errno=%s", mibs, kind, errname(errno));
        return;
    }
    if (is_stable(n) && size <= 64) {
        unsigned char buf[64];
        size_t blen = sizeof buf;
        sysctl(mib, len, buf, &blen, NULL, 0);
        char val[80];
        if (ktype == CTLTYPE_STRING) snprintf(val, sizeof val, "\"%.*s\"", (int)strnlen((char*)buf, blen), buf);
        else if (blen == 4) snprintf(val, sizeof val, "%d", *(int*)buf);
        else if (blen == 8) snprintf(val, sizeof val, "%lld", *(long long*)buf);
        else snprintf(val, sizeof val, "\"%.*s\"", (int)strnlen((char*)buf, blen), buf);
        NOTE(label, "mib=%s %s len=%zu value=%s", mibs, kind, size, val);
    } else {
        NOTE(label, "mib=%s %s len=%zu", mibs, kind, size);
    }
}

int main(void) {
    probe_name = "sysctl";
    for (size_t i = 0; i < sizeof names / sizeof names[0]; ++i) describe(names[i]);
    int pg[2] = {CTL_HW, HW_PAGESIZE};
    int value = 0;
    size_t len;
    CHECK("namelen_0", sysctl(pg, 0, &value, (len = 4, &len), NULL, 0));
    CHECK("namelen_1", sysctl(pg, 1, &value, (len = 4, &len), NULL, 0));
    {
        int big[CTL_MAXNAME + 1] = {CTL_HW, HW_PAGESIZE};
        CHECK("namelen_25", sysctl(big, CTL_MAXNAME + 1, &value, (len = 4, &len), NULL, 0));
        CHECK("namelen_24_leaf_extra", sysctl(big, CTL_MAXNAME, &value, (len = 4, &len), NULL, 0));
    }
    {
        int leaf3[3] = {CTL_HW, HW_PAGESIZE, 0};
        CHECK("leaf_with_extra_component", sysctl(leaf3, 3, &value, (len = 4, &len), NULL, 0));
        int str3[3] = {CTL_KERN, KERN_OSTYPE, 0};
        char sbuf[32];
        CHECK("string_leaf_with_extra_component", sysctl(str3, 3, sbuf, (len = sizeof sbuf, &len), NULL, 0));
        int absent[2] = {50, 1};
        CHECK("absent_top_level_50", sysctl(absent, 2, &value, (len = 4, &len), NULL, 0));
        int absent2[2] = {CTL_HW, 9999};
        CHECK("absent_hw_9999", sysctl(absent2, 2, &value, (len = 4, &len), NULL, 0));
        int negative[2] = {-1, 1};
        CHECK("negative_top_level", sysctl(negative, 2, &value, (len = 4, &len), NULL, 0));
        int node[2] = {CTL_KERN, KERN_PROC};
        CHECK("node_kern_proc", sysctl(node, 2, NULL, (len = 0, &len), NULL, 0));
        int topnode[2] = {CTL_HW, 0};
        CHECK("hw_0", sysctl(topnode, 2, &value, (len = 4, &len), NULL, 0));
    }
    {
        unsigned short small = 0;
        len = sizeof small;
        errno = 0;
        int r = sysctl(pg, 2, &small, &len, NULL, 0);
        NOTE("int_short_buffer_2", "rc=%d errno=%s len_after=%zu", r, r ? errname(errno) : "0", len);
        char tiny[3];
        int os[2] = {CTL_KERN, KERN_OSTYPE};
        len = sizeof tiny;
        r = sysctl(os, 2, tiny, &len, NULL, 0);
        NOTE("string_short_buffer_3", "rc=%d errno=%s len_after=%zu bytes=%.3s", r, r ? errname(errno) : "0", len, tiny);
        CHECK("oldp_set_oldlenp_null", sysctl(pg, 2, &value, NULL, NULL, 0));
        CHECK("oldp_null_oldlenp_null", sysctl(pg, 2, NULL, NULL, NULL, 0));
        len = 4;
        r = sysctl(pg, 2, NULL, &len, NULL, 0);
        NOTE("size_query_int", "rc=%d len=%zu", r, len);
        len = 100;
        r = sysctl(pg, 2, &value, &len, NULL, 0);
        NOTE("oversize_buffer_int", "rc=%d len_after=%zu", r, len);
        len = 0;
        r = sysctl(pg, 2, &value, &len, NULL, 0);
        NOTE("zero_capacity_buffer", "rc=%d errno=%s len_after=%zu", r, r ? errname(errno) : "0", len);
        int nv = 8192;
        CHECK("write_readonly_int", sysctl(pg, 2, NULL, NULL, &nv, sizeof nv));
        int hn[2] = {CTL_KERN, KERN_HOSTNAME};
        CHECK("write_hostname_unprivileged", sysctl(hn, 2, NULL, NULL, "x", 1));
        CHECK("write_readonly_with_newlen_0", sysctl(pg, 2, NULL, NULL, &nv, 0));
    }
    {
        int ar[2] = {CTL_KERN, KERN_ARND};
        unsigned char buf[512];
        len = 512;
        int r = sysctl(ar, 2, buf, &len, NULL, 0);
        NOTE("arandom_512", "rc=%d len_after=%zu", r, len);
        len = 512;
        r = sysctl(ar, 2, NULL, &len, NULL, 0);
        NOTE("arandom_size_query_512", "rc=%d len_after=%zu", r, len);
        len = 0;
        r = sysctl(ar, 2, NULL, &len, NULL, 0);
        NOTE("arandom_size_query_0", "rc=%d len_after=%zu", r, len);
        len = 7;
        r = sysctl(ar, 2, buf, &len, NULL, 0);
        NOTE("arandom_7", "rc=%d len_after=%zu", r, len);
    }
    {
        char path[1024];
        int pp[4] = {CTL_KERN, KERN_PROC, KERN_PROC_PATHNAME, -1};
        len = sizeof path;
        int r = sysctl(pp, 4, path, &len, NULL, 0);
        NOTE("proc_pathname_minus1", "rc=%d %s", r, r ? errname(errno) : "ok");
        pp[3] = getpid();
        len = sizeof path;
        r = sysctl(pp, 4, path, &len, NULL, 0);
        NOTE("proc_pathname_self", "rc=%d %s", r, r ? errname(errno) : "ok");
        pp[3] = 1;
        len = sizeof path;
        r = sysctl(pp, 4, path, &len, NULL, 0);
        NOTE("proc_pathname_pid1", "rc=%d %s", r, r ? errname(errno) : "ok");
        pp[3] = 999999;
        len = sizeof path;
        r = sysctl(pp, 4, path, &len, NULL, 0);
        NOTE("proc_pathname_missing_pid", "rc=%d %s", r, r ? errname(errno) : "ok");
        len = sizeof path;
        CHECK("proc_pathname_no_pid", sysctl(pp, 3, path, &len, NULL, 0));
        int kp[4] = {CTL_KERN, KERN_PROC, KERN_PROC_PID, getpid()};
        struct kinfo_proc ki;
        len = sizeof ki;
        r = sysctl(kp, 4, &ki, &len, NULL, 0);
        NOTE("proc_pid_self", "rc=%d len=%zu ki_structsize=%d", r, len, ki.ki_structsize);
        kp[3] = 999999;
        len = sizeof ki;
        r = sysctl(kp, 4, &ki, &len, NULL, 0);
        NOTE("proc_pid_missing", "rc=%d errno=%s len=%zu", r, r ? errname(errno) : "0", len);
        kp[3] = 1;
        len = sizeof ki;
        r = sysctl(kp, 4, &ki, &len, NULL, 0);
        NOTE("proc_pid_1", "rc=%d errno=%s", r, r ? errname(errno) : "0");
        int vm[4] = {CTL_KERN, KERN_PROC, KERN_PROC_VMMAP, getpid()};
        len = 0;
        r = sysctl(vm, 4, NULL, &len, NULL, 0);
        NOTE("proc_vmmap_self_size_query", "rc=%d len_%s", r, len > 0 ? "positive" : "zero");
        NOTE("kinfo_vmentry_size", "%zu", sizeof(struct kinfo_vmentry));
        NOTE("kinfo_proc_size", "%zu", sizeof(struct kinfo_proc));
    }
    {
        size_t l = 0;
        CHECK("byname_trailing_dot", sysctlbyname("hw.pagesize.", NULL, &l, NULL, 0));
        CHECK("byname_top_level_hw", sysctlbyname("hw", NULL, (l = 0, &l), NULL, 0));
        CHECK("byname_top_level_kern_with_buf", sysctlbyname("kern", &value, (l = 4, &l), NULL, 0));
        CHECK("byname_empty", sysctlbyname("", NULL, (l = 0, &l), NULL, 0));
        CHECK("byname_missing", sysctlbyname("no.such.name", NULL, (l = 0, &l), NULL, 0));
        CHECK("byname_double_dot", sysctlbyname("hw..pagesize", NULL, (l = 0, &l), NULL, 0));
        CHECK("byname_leading_dot", sysctlbyname(".hw.pagesize", NULL, (l = 0, &l), NULL, 0));
        CHECK("byname_numeric_component", sysctlbyname("hw.7", NULL, (l = 0, &l), NULL, 0));
        CHECK("byname_null_syscall", syscall(SYS___sysctlbyname, NULL, (size_t)0, NULL, (l = 0, &l), NULL, (size_t)0));
        int m[CTL_MAXNAME];
        size_t ml = 1;
        CHECK("nametomib_short_mib_buffer", sysctlnametomib("kern.proc.pathname", m, &ml));
        NOTE("nametomib_short_mib_buffer_len", "%zu", ml);
    }
    {
        long ncpu = sysconf(_SC_NPROCESSORS_ONLN);
        int hn = 0;
        size_t l = sizeof hn;
        sysctlbyname("hw.ncpu", &hn, &l, NULL, 0);
        NOTE("hw_ncpu_vs_sysconf", "%s", hn == ncpu ? "equal" : "differ");
        unsigned long pm = 0;
        l = sizeof pm;
        sysctlbyname("hw.physmem", &pm, &l, NULL, 0);
        long pages = sysconf(_SC_PHYS_PAGES);
        NOTE("hw_physmem_vs_sysconf", "%s", pm == (unsigned long)pages * (unsigned long)sysconf(_SC_PAGESIZE) ? "equal" : "differ");
        int i4 = 0;
        l = sizeof i4;
        int r = sysctlbyname("hw.physmem", &i4, &l, NULL, 0);
        NOTE("hw_physmem_into_int", "rc=%d errno=%s len_after=%zu", r, r ? errname(errno) : "0", l);
    }
    return 0;
}
