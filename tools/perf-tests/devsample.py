#!/usr/bin/env python3
"""Device telemetry sampler for AnyPS5 game test runs (Linux, Android/proot, ChromeOS-like hosts).

usage: devsample.py <out.jsonl> [--seconds N] [--every S] [--match TEXT]
Writes one JSON line per sample: CPU busy (total and busiest core), CPU clocks, RAM in use, the game's RSS and CPU
(process whose command line contains TEXT), the hottest thermal zone, and GPU clock/busy (Intel i915, Adreno kgsl,
AMD/other DRM devfreq). Anything the device does not expose is left out. Stdlib only.
"""
import glob
import json
import os
import sys
import time


def read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return None


def num(path):
    v = read(path)
    try:
        return float(v.split()[0]) if v else None
    except ValueError:
        return None


def cpu_times():
    text = read("/proc/stat")
    out = {}
    if not text:
        return out
    for line in text.splitlines():
        if not line.startswith("cpu"):
            break
        p = line.split()
        vals = [int(x) for x in p[1:]]
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        out[p[0]] = (sum(vals), idle)
    return out


def busy(a, b):
    res = {}
    for k in b:
        if k in a:
            dt = b[k][0] - a[k][0]
            di = b[k][1] - a[k][1]
            if dt > 0:
                res[k] = round(100.0 * (dt - di) / dt, 1)
    return res


def find_pid(match):
    if not match:
        return None
    best = None
    for d in glob.glob("/proc/[0-9]*"):
        cmd = read(d + "/cmdline")
        if cmd and match in cmd.replace("\x00", " ") and "devsample" not in cmd:
            rss = proc_rss(d)
            if rss and (best is None or rss > best[1]):
                best = (d, rss)
    return best[0] if best else None


def proc_rss(d):
    text = read(d + "/status")
    if not text:
        return None
    for line in text.splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) // 1024
    return None


def proc_ticks(d):
    text = read(d + "/stat")
    if not text:
        return None
    p = text[text.rindex(")") + 2:].split()
    return int(p[11]) + int(p[12])


def mem():
    text = read("/proc/meminfo")
    if not text:
        return None, None
    m = {}
    for line in text.splitlines():
        k, _, v = line.partition(":")
        m[k] = int(v.split()[0]) if v.split() else 0
    total = m.get("MemTotal", 0) // 1024
    avail = m.get("MemAvailable", 0) // 1024
    return total - avail, total


def temps():
    best = None
    for z in glob.glob("/sys/class/thermal/thermal_zone*/temp") + glob.glob("/sys/class/hwmon/hwmon*/temp*_input"):
        v = num(z)
        if v is not None and 1000 <= v <= 150000:
            best = v if best is None else max(best, v)
    return round(best / 1000.0, 1) if best else None


def cpufreq():
    cur, top = [], []
    for c in glob.glob("/sys/devices/system/cpu/cpu[0-9]*/cpufreq"):
        f = num(c + "/scaling_cur_freq")
        m = num(c + "/cpuinfo_max_freq")
        if f:
            cur.append(f / 1000.0)
        if m:
            top.append(m / 1000.0)
    if not cur:
        return {}
    return {"cpu_mhz_avg": round(sum(cur) / len(cur)), "cpu_mhz_max": round(max(cur)), "cpu_mhz_limit": round(max(top)) if top else None}


def gpu():
    out = {}
    for card in glob.glob("/sys/class/drm/card[0-9]"):
        act = num(card + "/gt_act_freq_mhz")
        if act is not None:
            out["gpu_mhz"] = act
            out["gpu_mhz_limit"] = num(card + "/gt_max_freq_mhz") or num(card + "/gt_RP0_freq_mhz")
            return out
        b = num(card + "/device/gpu_busy_percent")
        if b is not None:
            out["gpu_busy_pct"] = b
            return out
    k = "/sys/class/kgsl/kgsl-3d0"
    if os.path.isdir(k):
        b = read(k + "/gpu_busy_percentage")
        if b:
            out["gpu_busy_pct"] = float(b.split()[0].rstrip("%"))
        clk = num(k + "/gpuclk")
        if clk:
            out["gpu_mhz"] = round(clk / 1e6)
        mx = num(k + "/max_gpuclk")
        if mx:
            out["gpu_mhz_limit"] = round(mx / 1e6)
    for d in glob.glob("/sys/class/devfreq/*gpu*") + glob.glob("/sys/class/devfreq/*.gpu"):
        if "gpu_mhz" not in out:
            f = num(d + "/cur_freq")
            if f:
                out["gpu_mhz"] = round(f / 1e6)
                m = num(d + "/max_freq")
                out["gpu_mhz_limit"] = round(m / 1e6) if m else None
    return out


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    out = argv[1]
    seconds, every, match = 660.0, 2.0, ""
    i = 2
    while i < len(argv):
        if argv[i] == "--seconds":
            seconds = float(argv[i + 1]); i += 2
        elif argv[i] == "--every":
            every = float(argv[i + 1]); i += 2
        elif argv[i] == "--match":
            match = argv[i + 1]; i += 2
        else:
            i += 1
    hz = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
    start = time.time()
    prev = cpu_times()
    pid = None
    prev_ticks = None
    prev_t = time.time()
    while time.time() - start < seconds:
        time.sleep(every)
        now = time.time()
        cur = cpu_times()
        b = busy(prev, cur)
        prev = cur
        s = {"t": round(now - start, 1)}
        if "cpu" in b:
            s["cpu_pct"] = b["cpu"]
            cores = [v for k, v in b.items() if k != "cpu"]
            if cores:
                s["cpu_core_max_pct"] = max(cores)
        used, total = mem()
        if used is not None:
            s["ram_used_mib"], s["ram_total_mib"] = used, total
        if pid is None or not os.path.isdir(pid):
            pid = find_pid(match)
            prev_ticks = None
        if pid:
            r = proc_rss(pid)
            if r is not None:
                s["game_rss_mib"] = r
            t = proc_ticks(pid)
            if t is not None and prev_ticks is not None:
                s["game_cpu_pct"] = round(100.0 * (t - prev_ticks) / hz / max(now - prev_t, 0.001), 1)
            prev_ticks = t
            th = read(pid + "/status")
            if th:
                for line in th.splitlines():
                    if line.startswith("Threads:"):
                        s["game_threads"] = int(line.split()[1])
        prev_t = now
        tc = temps()
        if tc is not None:
            s["temp_c"] = tc
        s.update(cpufreq())
        s.update(gpu())
        with open(out, "a", encoding="utf-8") as f:
            f.write(json.dumps(s) + "\n")
        if match and pid is None and now - start > 120:
            break
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
