"""A fair speed test of one library on one game: base warm-up, base 1, change warm-up, change, base 2, the same script each time.
A changed library rebuilds shaders, so warm-ups do not count. Base 1 and base 2 show the noise. Results: _reports\\speed-test\\<name>\\."""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
RUN = HERE / "night_run.py"
SPEED = HERE.parent.parent.parent / "_reports" / "speed-test"

ap = argparse.ArgumentParser()
ap.add_argument("folder")
ap.add_argument("--lib", required=True, help="the changed library, e.g. C:\\AnyPS5-work\\speed-libs\\libSceAgcDriver.prx")
ap.add_argument("--name", default="")
ap.add_argument("--minutes", type=float, default=8)
ap.add_argument("--env", action="append", default=[])
ap.add_argument("--plan", action="store_true", help="print the runs and stop")
args = ap.parse_args()

G = Path(args.folder)
LIB = Path(args.lib)
OUT = SPEED / (args.name or f"{G.name.split(' (')[0].replace(' ', '-')}-{LIB.stem}-{time.strftime('%m%d-%H%M')}")
STEPS = [("base warm-up", False), ("base 1", False), ("change warm-up", True), ("change", True), ("base 2", False)]
problems = []
if not G.is_dir():
    problems.append(f"no folder {G}")
if not LIB.is_file():
    problems.append(f"no library {LIB}")
elif not (G / "libs" / LIB.name).is_file():
    problems.append(f"{G.name} has no libs\\{LIB.name} to swap")
if problems:
    print("NOT RUN: " + "; ".join(problems))
    sys.exit(2)
print(f"Results go to {OUT}")
for label, change in STEPS:
    print(f"  {label}: {args.minutes:g} minutes" + (f" with {LIB.name}" if change else ""))
if args.plan:
    sys.exit(0)
OUT.mkdir(parents=True, exist_ok=True)
for label, change in STEPS:
    cmd = [sys.executable, "-I", str(RUN), str(G), "--label", f"speed test {label}", "--minutes", str(args.minutes), "--runs", "1", "--no-walk"]
    for pair in args.env:
        cmd += ["--env", pair]
    if change:
        cmd += ["--swap-lib", str(LIB)]
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    print(label, "exit", r.returncode, flush=True)
    for line in r.stdout.splitlines():
        if line.startswith('{"run"'):
            s = json.loads(line)
            print("  frames", s.get("frames"), s.get("end"), s.get("kind"), flush=True)
    summary = G / "aps5-summary.jsonl"
    if summary.exists():
        lines = summary.read_text(encoding="utf-8", errors="replace").splitlines()
        cut, last = 0, None
        for i, line in enumerate(lines):
            try:
                end = json.loads(line).get("interval_end_s")
            except ValueError:
                continue
            if end is not None and last is not None and end < last:
                cut = i
            if end is not None:
                last = end
        (OUT / f"{label.replace(' ', '-')}.jsonl").write_text("\n".join(lines[cut:]) + "\n", encoding="utf-8")
print("DONE. Read it with speed_results.")
