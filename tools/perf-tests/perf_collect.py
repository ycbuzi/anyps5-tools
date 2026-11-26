"""Collects the perf test history of every device into one board (Blu 11-17: find what slows each game and device down).

Run every 30 minutes by the scheduled task "AnyPS5 perf board". Pulls perf-history.jsonl from the Chromebook
(/root/idle-tests) and the Quest (/root/qtest in proot Debian), reads the PC's own (_reports\\perf-history.jsonl,
written by auto-games.ps1), and writes _reports\\perf-board.md: per device and game the latest FPS, bottleneck, top
phase, device limits, new problems and regressions, worst first. Offline only: no Claude.
"""
import json
import subprocess
from pathlib import Path

REPORTS = Path(r"C:\AnyPS5 Games\_reports")
PERF = REPORTS / "perf"
CB = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "UserKnownHostsFile=" + str(Path.home() / ".ssh/known_hosts_anyps5os"),
      "-i", str(Path.home() / ".ssh/id_ed25519_chromebook"), "user@example.com", "cat /root/idle-tests/perf-history.jsonl"]
QUEST = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "quest",
         "cat $PREFIX/var/lib/proot-distro/containers/debian/rootfs/root/qtest/perf-history.jsonl"]
SOURCES = {"chromebook": CB, "quest": QUEST}


def pull(name, cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=60, creationflags=0x08000000)
    except (OSError, subprocess.TimeoutExpired):
        return
    if r.returncode == 0 and r.stdout:
        (PERF / (name + "-perf-history.jsonl")).write_bytes(r.stdout)


def records():
    files = [REPORTS / "perf-history.jsonl", Path(r"D:\AnyPS5 Games\_reports\perf-history.jsonl")] + list(PERF.glob("*-perf-history.jsonl"))
    out = []
    for f in files:
        try:
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
        except OSError:
            pass
    return out


def main():
    PERF.mkdir(parents=True, exist_ok=True)
    for name, cmd in SOURCES.items():
        pull(name, cmd)
    latest, runs = {}, {}
    for r in records():
        k = (r.get("device", "?"), r.get("game", "?"))
        runs[k] = runs.get(k, 0) + 1
        if k not in latest or r.get("time", "") >= latest[k].get("time", ""):
            latest[k] = r
    rows = []
    for k, r in latest.items():
        p, d = r.get("perf") or {}, r.get("device_stats") or {}
        bad = any("REGRESSION" in c for c in r.get("changes", []))
        fps = p.get("fps_median") or 0
        top = next(iter((p.get("top_phases_ms") or {}).items()), ("-", 0))
        rows.append((not bad, fps, k[0], k[1], r, p, d, top))
    rows.sort(key=lambda x: (x[0], x[1]))
    lines = ["# Perf board", "", "Latest test of each game on each device, regressions first, then slowest. Source: perf_report.py on every device.", "",
             "| device | game | when | FPS (low) | p99 ms | bound | top phase ms/frame | device limits | changes |", "|---|---|---|---|---|---|---|---|---|"]
    for _, fps, dev, game, r, p, d, top in rows:
        lines.append("| %s | %s | %s | %s | %s | %s | %s %s | %s | %s |" % (
            dev, game, r.get("time", "")[5:16], "%s (%s)" % (p.get("fps_median"), p.get("fps_p10")) if p else "no frames",
            p.get("frame_p99_ms_median", "-"), p.get("bound", "-"), top[0], top[1], "; ".join(d.get("flags", [])) or "-",
            "; ".join(r.get("changes", [])).replace("|", "/")))
    (REPORTS / "perf-board.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
