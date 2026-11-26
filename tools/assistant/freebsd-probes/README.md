# FreeBSD kernel reference probes (Azure VM `azbsd`)

Small C programs that record how real FreeBSD (14.4, the PS5 OS family) answers the kernel calls AnyPS5's libkernel reimplements. Each output line is `<probe>.<case> <result>`, with no addresses or raw timings, so you can diff runs.

| File | Covers |
|---|---|
| mmap_map.c / mmap_unmap.c / mmap_protect.c / mmap_advise.c | mmap flags/errnos, munmap over holes, mprotect edges, madvise/msync/mlock |
| umtx_wait.c | `_umtx_op` WAIT/WAKE/NWAKE, timeouts (timespec and `_umtx_time`), signals, op range |
| umtx_mutex.c | `_umtx_op` mutex, CV, rwlock, SEM/SEM2, SHM, robust lists |
| kqueue_user.c / kqueue_timer.c / kqueue_misc.c | EVFILT_USER, EVFILT_TIMER, EV_CLEAR/ONESHOT/DISPATCH/RECEIPT, other filters, errors |
| sysctl_names.c | every name libkernel emulates: MIB, type, size, stable values; sysctl error semantics |
| clock_time.c | clock ids, getres, nanosleep/usleep/clock_nanosleep edges, EINTR, POSIX timers |
| probe.h | CHECK / NOTE / GUARDED (forks a case that may hang, reports HANG after 2 s) |

## Run
- Hourly: VM cron `0 * * * *` runs `run.sh` under `lockf .run.lock` at nice 19 and logs to `~/probes/cron.log`.
- Continuous: `repeat.sh` (cron `@reboot` plus a `*/30` watchdog, single instance, nice 20) loops between hourly runs. Each round reruns every probe and `stress/stress_kernel.c`, which checks threaded invariants: umtx handoff, wake-exactly-2, kernel mutex contention, cross-thread EVFILT_USER, timer count vs elapsed time, nanosleep oversleep, monotonic clock, mmap churn. It tallies every line in `~/probes/flaky/counts.txt`; `~/probes/flaky/flaky.txt` lists the cases that gave more than one result.
- By hand: `ssh azbsd 'sh ~/probes/run.sh'` (about 1 minute).
- Output on the VM: `~/probes/results.txt`, `results.prev.txt`, `results.diff`, `results.meta`, plus `history/<date>.txt` (30 kept).
- PC: `_tools\dispatcher.ps1` copies `results.*` and `flaky.txt` to `D:\AnyPS5 Games\_reports\freebsd-probes\` after each hourly run. Changed diffs and newly flaky cases are appended to `_reports\workers\freebsd-probes-<date>.md`.
- After you edit a probe here: `scp *.c probe.h run.sh repeat.sh azbsd:probes/` and `scp stress/stress_kernel.c azbsd:probes/stress/`.

## Do not probe
- `UMTX_OP_MUTEX_WAKE` (op 18) on an unowned umutex: the FreeBSD 14.4 kernel spins forever, and the process can't be killed (SIGKILL is ignored). Only a reboot clears it. The case is printed as SKIPPED.

- file_ops.c, socket_ops.c (11-13): file and socket call edge cases.
