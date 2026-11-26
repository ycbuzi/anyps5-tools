"""FPS over the last 60 % of a run from aps5-summary.jsonl, plus where frame time goes (the FPS tool's measure())."""
import json, statistics, sys

rows = []
for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
    try:
        s = json.loads(line)
    except ValueError:
        continue
    if s.get("interval_end_s") is not None:
        rows.append(s)
ran = float(sys.argv[2]) if len(sys.argv) > 2 else (rows[-1]["interval_end_s"] if rows else 0)
skip = ran * 0.4
late = [r for r in rows if r.get("interval_start_s", 0) >= skip and (r.get("window") or {}).get("ms")]
first = next((r for r in rows if r.get("presents")), None)
shown = next((r["window"]["first_present"] for r in reversed(rows) if (r.get("window") or {}).get("first_present")), None)
print(f"{len(rows)} intervals; first frame after {shown / 1000:.1f} s" if shown else f"{len(rows)} intervals; no frame shown")
if first:
    print("build", first.get("commit"), "| gpu", first.get("gpu"), "| resolution", first.get("resolution"))
if not late:
    print("no measurable frames in the last 60 %")
    sys.exit(0)
presents = sum(r.get("presents") or 0 for r in late)
ms = sum(r["window"]["ms"] for r in late)
p50 = statistics.median(r.get("frame_ms", {}).get("p50") or 0 for r in late)
worst = max((r.get("frame_ms", {}).get("max") or 0 for r in late), default=0)
print(f"FPS {presents / ms * 1000:.2f} over the last {ms / 1000:.0f} s ({presents} frames); median frame {p50:.1f} ms, worst {worst:.0f} ms")
phases = {}
for r in late:
    for k, v in (r.get("top_phases_ms_per_frame") or {}).items():
        phases[k] = phases.get(k, 0) + v / len(late)
for k, v in sorted(phases.items(), key=lambda x: -x[1])[:8]:
    print(f"   {v:7.2f} ms/frame  {k}")
