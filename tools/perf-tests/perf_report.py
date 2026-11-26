#!/usr/bin/env python3
"""Turns one AnyPS5 game test run into a perf + problems record and flags regressions (stdlib only, any OS).

usage: perf_report.py --device NAME --game NAME --dir RUNDIR [--log GAMELOG] [--history H.jsonl] [--report R.md]
RUNDIR holds aps5-summary.jsonl (APS5_TRACE_SUMMARY_EVERY lines, APS5_TRACE_DIR=RUNDIR) and samples.jsonl
(devsample.py). Prints the record as JSON, appends it to the history and a short block to the report.
What it finds: FPS (median, 10th percentile, worst interval), frame p99 spikes, GPU- vs CPU-bound intervals and the
busiest queue workers and phases, device limits (CPU core pegged, RAM pressure, heat, clocks held below their limit),
unique problem lines in the game log, and changes against the previous run of the same game on the same device.
"""
import json
import re
import sys
import time
from pathlib import Path

PROBLEM = re.compile(r"not ?implemented|unsupported|unimplemented|error|fail|assert|abort|sigsegv|sigbus|segmentation|crash|exception|refused|timed? ?out|lost|stall|hang", re.I)
SKIP = re.compile(r"\[shader-disk-cache\]|0 (load |write )?failures.*0 (load |write )?failures")
NOISE = re.compile(r"0x[0-9a-f]+|\b\d+(\.\d+)?\b|\"[^\"]{40,}\"", re.I)


def args(argv):
    a, i = {}, 1
    while i < len(argv):
        if argv[i].startswith("--") and i + 1 < len(argv):
            a[argv[i][2:]] = argv[i + 1]
            i += 2
        else:
            i += 1
    return a


def jsonl(path):
    out = []
    try:
        for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    except OSError:
        pass
    return out


def pct(vals, p):
    if not vals:
        return None
    v = sorted(vals)
    return v[min(len(v) - 1, int(round(p / 100.0 * (len(v) - 1))))]


def perf(lines):
    iv = [l for l in lines if "interval_start_s" in l and l.get("presents")]
    if not iv:
        return {}
    fps, p99, gpu_bound, workers, phases = [], [], 0, {}, {}
    spikes = 0
    for l in iv:
        dur = l["interval_end_s"] - l["interval_start_s"]
        if dur <= 0:
            continue
        f = l["presents"] / dur
        fps.append(f)
        fm = l.get("frame_ms") or {}
        if fm.get("p99"):
            p99.append(fm["p99"])
            if fm.get("p50") and fm["p99"] > 3 * fm["p50"]:
                spikes += 1
        g = l.get("gpu_busy_ms_per_present")
        if g and fm.get("p50") and g >= 0.85 * fm["p50"]:
            gpu_bound += 1
        for k, v in (l.get("worker_busy_pct") or {}).items():
            workers.setdefault(k, []).append(v)
        for k, v in (l.get("top_phases_ms_per_frame") or {}).items():
            phases.setdefault(k, []).append(v)
    if not fps:
        return {}
    first = iv[0]
    r = {
        "intervals": len(fps),
        "fps_median": round(pct(fps, 50), 1),
        "fps_p10": round(pct(fps, 10), 1),
        "fps_worst": round(min(fps), 1),
        "frame_p99_ms_median": round(pct(p99, 50), 1) if p99 else None,
        "spiky_intervals": spikes,
        "gpu_bound_share": round(gpu_bound / len(fps), 2),
        "gpu_busy_ms_median": round(pct([l.get("gpu_busy_ms_per_present") or 0 for l in iv], 50), 2),
        "top_workers_pct": {k: round(sum(v) / len(v), 1) for k, v in sorted(workers.items(), key=lambda kv: -sum(kv[1]) / len(kv[1]))[:4]},
        "top_phases_ms": {k: round(sum(v) / len(fps), 2) for k, v in sorted(phases.items(), key=lambda kv: -sum(kv[1]))[:6]},
        "resolution": first.get("resolution"),
        "commit": first.get("commit"),
        "gpu": first.get("gpu"),
        "title_id": first.get("title_id"),
        "trace_dropped": any(l.get("trace_dropped") for l in iv),
    }
    r["bound"] = "GPU" if r["gpu_bound_share"] >= 0.5 else "CPU/driver"
    return r


def device(samples):
    if not samples:
        return {}
    def col(k):
        return [s[k] for s in samples if s.get(k) is not None]
    d = {"samples": len(samples)}
    for k, fn in (("cpu_pct", "avg"), ("cpu_core_max_pct", "avg"), ("game_cpu_pct", "avg"), ("ram_used_mib", "max"), ("game_rss_mib", "max"),
                  ("temp_c", "max"), ("cpu_mhz_avg", "avg"), ("gpu_mhz", "avg"), ("gpu_busy_pct", "avg"), ("game_threads", "max")):
        v = col(k)
        if v:
            d[k + "_" + fn] = round(max(v) if fn == "max" else sum(v) / len(v), 1)
    lim = col("cpu_mhz_limit")
    glim = col("gpu_mhz_limit")
    total = col("ram_total_mib")
    flags = []
    if d.get("cpu_core_max_pct_avg", 0) >= 90:
        flags.append("a CPU core is pegged (%.0f%% average busiest core): single-thread bound" % d["cpu_core_max_pct_avg"])
    if total and d.get("ram_used_mib_max", 0) >= 0.9 * total[0]:
        flags.append("RAM nearly full (%d of %d MiB)" % (d["ram_used_mib_max"], total[0]))
    if d.get("temp_c_max", 0) >= 90:
        flags.append("hot: %.0f C" % d["temp_c_max"])
    if lim and d.get("cpu_mhz_avg_avg") and d.get("cpu_pct_avg", 0) >= 60 and d["cpu_mhz_avg_avg"] < 0.7 * lim[0]:
        flags.append("CPU clocks held at %.0f of %.0f MHz under load (throttling or power limit)" % (d["cpu_mhz_avg_avg"], lim[0]))
    if glim and glim[0] and d.get("gpu_mhz_avg") and d["gpu_mhz_avg"] < 0.6 * glim[0] and d.get("gpu_busy_pct_avg", 100) >= 80:
        flags.append("GPU clock at %.0f of %.0f MHz while busy" % (d["gpu_mhz_avg"], glim[0]))
    d["flags"] = flags
    return d


def problems(log_path):
    seen = {}
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if PROBLEM.search(line) and not SKIP.search(line):
                    key = NOISE.sub("#", line.strip())[:160]
                    seen[key] = seen.get(key, 0) + 1
    except (OSError, TypeError):
        return {}
    return dict(sorted(seen.items(), key=lambda kv: -kv[1])[:25])


def crash(log_path):
    fatal = re.compile(r"terminate called|what\(\)|FATAL|SIGSEGV|SIGBUS|SIGABRT|Segmentation fault|Aborted|unhandled exception", re.I)
    found = []
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if fatal.search(line):
                    found.append(line.strip()[:300])
    except (OSError, TypeError):
        return None
    return " / ".join(found[-2:]) if found else None


def kind(build):
    build = build or ""
    return build if build.startswith("pr-") else "main"


def previous(history, dev, game, build=""):
    for rec in reversed(jsonl(history)):
        if rec.get("device") == dev and rec.get("game") == game and kind(rec.get("build")) == kind(build):
            return rec
    return None


def compare(rec, prev):
    if not prev:
        return ["first measured run on this device"]
    notes = []
    a, b = (prev.get("perf") or {}).get("fps_median"), (rec.get("perf") or {}).get("fps_median")
    if a and b:
        ch = (b - a) / a
        if ch <= -0.10:
            notes.append("REGRESSION: FPS %.1f -> %.1f (%+.0f%%)" % (a, b, ch * 100))
        elif ch >= 0.10:
            notes.append("faster: FPS %.1f -> %.1f (%+.0f%%)" % (a, b, ch * 100))
    elif a and not b:
        notes.append("REGRESSION: no frames this run (previous %.1f FPS)" % a)
    new = [k for k in rec.get("problems", {}) if k not in (prev.get("problems") or {})]
    if new:
        notes.append("%d new problem lines" % len(new))
    gone = [k for k in (prev.get("problems") or {}) if k not in rec.get("problems", {})]
    if gone:
        notes.append("%d problem lines gone" % len(gone))
    if rec.get("crash") and not prev.get("crash"):
        notes.append("REGRESSION: crashed (previous run did not)")
    if prev.get("ran_s") and rec.get("ran_s") and rec["ran_s"] < 0.5 * prev["ran_s"] and rec.get("ended") == "exited":
        notes.append("REGRESSION: exited after %d s (previous run lasted %d s)" % (rec["ran_s"], prev["ran_s"]))
    if not notes:
        notes.append("no change against the run of %s" % prev.get("time", "?"))
    return notes


def main(argv):
    a = args(argv)
    if "device" not in a or "game" not in a or "dir" not in a:
        print(__doc__)
        return 2
    run = Path(a["dir"])
    rec = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "device": a["device"], "game": a["game"], "build": a.get("build", ""),
           "ran_s": int(float(a.get("ran", "0"))), "ended": a.get("ended", "")}
    rec["perf"] = perf(jsonl(run / "aps5-summary.jsonl"))
    rec["device_stats"] = device(jsonl(run / "samples.jsonl"))
    rec["problems"] = problems(a.get("log"))
    rec["crash"] = crash(a.get("log"))
    hist = a.get("history")
    rec["changes"] = compare(rec, previous(hist, rec["device"], rec["game"], rec["build"]) if hist else None)
    if hist:
        with open(hist, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    if a.get("report"):
        p, d = rec["perf"], rec["device_stats"]
        lines = ["## %s  %s on %s (%s s, %s)" % (rec["time"], rec["game"], rec["device"], rec["ran_s"], rec["ended"] or "?")]
        if p:
            lines.append("- FPS median %s, low %s, worst %s; frame p99 %s ms; %s-bound (%d%% of intervals); spiky intervals %d" % (
                p["fps_median"], p["fps_p10"], p["fps_worst"], p["frame_p99_ms_median"], p["bound"], p["gpu_bound_share"] * 100, p["spiky_intervals"]))
            lines.append("- busiest workers %s; top phases ms/frame %s" % (p["top_workers_pct"], p["top_phases_ms"]))
        else:
            lines.append("- no presented frames recorded")
        if d:
            lines.append("- device: " + ", ".join("%s %s" % (k, v) for k, v in d.items() if k not in ("flags", "samples")))
            for fl in d.get("flags", []):
                lines.append("- LIMIT: " + fl)
        if rec["crash"]:
            lines.append("- CRASH: " + rec["crash"])
        for k, v in list(rec["problems"].items())[:6]:
            lines.append("- problem x%d: %s" % (v, k))
        for c in rec["changes"]:
            lines.append("- " + c)
        with open(a["report"], "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n\n")
    print(json.dumps(rec))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
