"""Flat pictures of every screen on the Quest: each 2D panel Blu lays out in VR is its own Android display.

usage: python -I quest_screens.py [--min 200] [--lens] [--out DIR]
Lists the displays scrcpy can mirror, records about two seconds of each without a window and saves its last frame
as display-<id>.png. Display 0 is the headset's lens view (both eyes, distorted, passthrough camera included), so it
is skipped unless --lens is given. Panels smaller than --min pixels on both sides (status bars, toasts) are skipped.
Writes to _reports\\quest-screens\\<time>\\ and prints one line per display.
"""
import argparse
import datetime
import pathlib
import re
import subprocess
import tempfile

import imageio_ffmpeg

SCRCPY = r"C:\AnyPS5-tools\scrcpy\v5.0.1\scrcpy-win64-v5.0.1\scrcpy.exe"
DEVICE = "192.0.2.1:5555"
HERE = pathlib.Path(__file__).resolve().parent
HIDDEN = 0x08000000


def displays():
    out = subprocess.run([SCRCPY, "-s", DEVICE, "--list-displays"], capture_output=True, text=True, errors="replace", timeout=60, creationflags=HIDDEN)
    return [(int(i), int(w), int(h)) for i, w, h in re.findall(r"--display-id=(\d+)\s+\((\d+)x(\d+)\)", out.stdout + out.stderr)]


def grab(display, png):
    with tempfile.TemporaryDirectory() as tmp:
        clip = pathlib.Path(tmp) / "clip.mkv"
        subprocess.run([SCRCPY, "-s", DEVICE, f"--display-id={display}", "--no-window", "--no-audio", "--no-control",
                        f"--record={clip}", "--time-limit=2", "--max-fps=15"], capture_output=True, timeout=60, creationflags=HIDDEN)
        if not clip.exists() or clip.stat().st_size < 1000:
            return "no video (secure or empty display)"
        r = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-sseof", "-0.5", "-i", str(clip), "-frames:v", "1", str(png)],
                           capture_output=True, text=True, timeout=60, creationflags=HIDDEN)
        if r.returncode or not png.exists():
            r = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(clip), "-frames:v", "1", str(png)],
                               capture_output=True, text=True, timeout=60, creationflags=HIDDEN)
        return "ok" if png.exists() else "frame extraction failed: " + r.stderr.strip()[:120]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=int, default=200)
    ap.add_argument("--lens", action="store_true")
    ap.add_argument("--out")
    args = ap.parse_args()
    out = pathlib.Path(args.out) if args.out else HERE.parent / "_reports" / "quest-screens" / datetime.datetime.now().strftime("%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    found = displays()
    if not found:
        print("no displays: is the Quest connected over adb?")
        return
    for display, w, h in found:
        if display == 0 and not args.lens:
            print(f"display 0 {w}x{h}: lens view, skipped (use --lens)")
            continue
        if max(w, h) < args.min:
            print(f"display {display} {w}x{h}: smaller than {args.min} px, skipped")
            continue
        png = out / f"display-{display}.png"
        print(f"display {display} {w}x{h}: {grab(display, png)}")
    print(f"saved in {out}")


if __name__ == "__main__":
    main()
