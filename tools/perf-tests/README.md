# Perf tests on every device (Blu 11-17)

Every game test also measures speed and looks for what slows the game or the device down.

- `devsample.py <out.jsonl> [--seconds N] [--every S] [--match TEXT]`: Linux/Android telemetry (CPU load and clocks, RAM, game RSS/threads, heat, GPU clock/busy).
- `perf_report.py --device D --game G --dir RUNDIR [--log L] [--history H] [--report R] [--ran S] [--ended E]`: reads `RUNDIR/aps5-summary.jsonl` (`APS5_TRACE_SUMMARY_EVERY=10`, `APS5_TRACE_DIR=RUNDIR`) and `RUNDIR/samples.jsonl`, and writes FPS, bottleneck, device limits, log problems and changes against the last run.
- `perf_collect.py`: the scheduled task "AnyPS5 perf board" runs it every 30 min and writes `_reports\perf-board.md` (all devices, regressions first).

Where it runs:
- Chromebook: `/root/idle-tests/cb-idle-tests.py` (timer `anyps5-idle-tests`), with runs in `/home/anyps5/perf-runs` and output in `/root/idle-tests/perf-report.md`.
- Quest: `/root/qtest/test-game.sh` in proot Debian, with runs in `/root/qtest/perf` and output in `perf-report.md` and `perf.tsv`. Android hides CPU load and temperatures; GPU and RAM are measured.
- PC: `auto-games.ps1` Test-GameUnlocked, with runs in `D:\AnyPS5 Games\_reports\perf-runs` and output in `perf-history.jsonl` and `perf-report.md`. PC device telemetry is not sampled yet.

Run by hand: `python -I perf_collect.py`, then open `_reports\perf-board.md`.
