"""Unattended test of one game folder. Pauses the pipeline, runs the game up to --runs times (a first run mostly builds shaders),
presses Cross on a timer to pass menus, then pushes the stick forward, and keeps a few real frames plus each log. Writes the game's story."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, r"D:\AnyPS5 Games\_tools\assistant")
import assistant as a
import debug_skills as d

ap = argparse.ArgumentParser()
ap.add_argument("folder")
ap.add_argument("--minutes", type=float, default=12)
ap.add_argument("--runs", type=int, default=3)
ap.add_argument("--enough", type=int, default=1500, help="frames that end the repeats")
ap.add_argument("--label", default="")
ap.add_argument("--env", action="append", default=[])
ap.add_argument("--no-walk", action="store_true", help="only press Cross, so the view stays comparable")
ap.add_argument("--swap-lib", default="", help="a library to use for this test; the game's own goes back after")
ap.add_argument("--hands-off", action="store_true", help="press nothing: Blu is playing")
ap.add_argument("--every-button", action="store_true", help="cycle the face buttons, options, touchpad and both sticks, for screens Cross alone does not pass")
args = ap.parse_args()

G = Path(args.folder)
APP = a.GAMES / "AnyPS5 Games.exe"
PAD_TOOL = r"C:\AnyPS5-work\src\tools\pad_input.py"
pad = G / "pad.txt"
os.environ.update({"APS5_PAD_INPUT_FILE": str(pad), "APS5_STALL_WATCHDOG_SECS": "20", "APS5_DUMP_FRAMES": "8", "APS5_DUMP_FRAMES_EVERY": "400", "APS5_DUMP_SCALE": "4"})
for pair in args.env:
    k, v = pair.split("=", 1)
    os.environ[k] = v
stop = threading.Event()


def press(*words):
    subprocess.run([sys.executable, "-I", PAD_TOOL, str(pad), *words], capture_output=True)


def fingers():
    """Cross every 30 s from 60 s (menus, Play), then also walk forward."""
    start = time.time()
    if args.hands_off or stop.wait(60):
        return
    while args.every_button and not stop.is_set():
        for word in ("cross", "options", "touchpad", "square", "triangle"):
            press(word)
            if stop.wait(6):
                return
        press("release", "--right-stick", "128", "255", "--hold-ms", "4000")
        if stop.wait(6):
            return
        press("release", "--left-stick", "128", "0", "--hold-ms", "5000")
        if stop.wait(6):
            return
    while not stop.is_set():
        press("cross")
        if stop.wait(10):
            return
        if time.time() - start > 240 and not args.no_walk:
            press("release", "--left-stick", "128", "0", "--hold-ms", "5000")
        if stop.wait(20):
            return


subprocess.run([str(APP), "--pause"])
results = []
swapped = G / "libs" / Path(args.swap_lib).name if args.swap_lib else None
keep_lib = swapped.with_name(swapped.name + ".before-test") if swapped else None
if swapped:
    shutil.copy2(swapped, keep_lib)
    shutil.copy2(args.swap_lib, swapped)
try:
    for n in range(1, args.runs + 1):
        stop.clear()
        finger = threading.Thread(target=fingers, daemon=True)
        finger.start()
        began = time.strftime("%H:%M")
        result = d.timeline(G, args.minutes, a.GAMES / "_tools" / "live", lambda text, *_: None, source=r"C:\AnyPS5-work\src")
        stop.set()
        finger.join(15)
        log = G / "anyps5.log"
        keep = G / "crash-logs"
        keep.mkdir(exist_ok=True)
        stamp = time.strftime("%Y-%m-%d-%H%M")
        if log.exists():
            shutil.copy2(log, keep / f"{stamp}-night{n}.log")
        shots = keep / f"{stamp}-night{n}-frames"
        frames = sorted(G.glob("frame_*.bmp"))
        if frames:
            shots.mkdir(exist_ok=True)
            for f in frames:
                shutil.move(str(f), str(shots / f.name))
        tail = result.splitlines()
        drawn = next((int(l.split()[2].rstrip(".")) for l in tail if l.startswith("Frames drawn:")), 0)
        summary = {"run": n, "began": began, "frames": drawn, "end": next((l for l in tail if l.startswith(("It exited", "It was still", "It was closed"))), ""),
                   "first": next((l for l in tail if l.startswith(("First frame", "No frame"))), ""), "shots": len(frames)}
        diag = d.diagnose(G)
        summary["kind"] = next((l for l in diag.splitlines() if l.startswith("KIND")), "")[:200]
        summary["said"] = next((l for l in diag.splitlines() if l.startswith("AnyPS5 stopped")), "")[:260]
        rows = [l for l in tail if "FPS" in l and "frames" in l]
        summary["last_rows"] = rows[-3:]
        try:
            lines = (G / "aps5-summary.jsonl").read_text(encoding="utf-8", errors="replace").splitlines()
            last = [json.loads(l) for l in lines[-4:] if l.strip()]
            if last:
                s = last[-1]
                summary["perf"] = {"resolution": s.get("resolution"), "frame_ms_p50": s.get("frame_ms", {}).get("p50"), "gpu_ms": s.get("gpu_busy_ms_per_present"),
                                   "workers": s.get("worker_busy_pct"), "top": dict(list(s.get("top_phases_ms_per_frame", {}).items())[:6])}
        except (OSError, ValueError):
            pass
        results.append(summary)
        print(json.dumps(summary), flush=True)
        a.log_progress(G.name, f"Claude, overnight: {args.label or 'test'}, run {n} of up to {args.runs}", result[-1500:], "")
        if drawn >= args.enough:
            break
finally:
    stop.set()
    if swapped:
        for _ in range(60):
            try:
                shutil.copy2(keep_lib, swapped)
                keep_lib.unlink()
                break
            except PermissionError:
                time.sleep(5)
        else:
            print(f"COULD NOT RESTORE {swapped}; the game's own library is {keep_lib}", flush=True)
    subprocess.run([str(APP), "--resume"])
    pad.unlink(missing_ok=True)
print("=== DONE", json.dumps({"game": G.name, "label": args.label, "runs": len(results), "best_frames": max((r["frames"] for r in results), default=0)}), flush=True)
