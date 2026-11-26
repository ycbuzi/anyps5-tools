"""Waits for Blu to open a game, then logs every 5 s how its window is shown next to the FPS in its title. Read-only. Stops when the game closes, or after 2 hours."""
import ctypes
import json
import re
import subprocess
import time
from ctypes import wintypes
from pathlib import Path

OUT = Path(r"D:\AnyPS5 Games\_reports\speed-test\window-modes.log")
FRAME = Path(r"D:\AnyPS5 Games\_tools\live\frame.txt")
u = ctypes.windll.user32
FIND = "Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -like 'D:\\AnyPS5 Games\\*' -and $_.Name -ne 'AnyPS5 Games.exe' -and $_.ExecutablePath -notmatch '_tools' } | Select-Object -First 1 | ForEach-Object { '' + $_.ProcessId + '|' + $_.ExecutablePath }"


def say(text):
    with OUT.open("a", encoding="utf-8") as f:
        f.write(text + "\n")


def find():
    r = subprocess.run(["powershell", "-NoProfile", "-Command", FIND], capture_output=True, text=True, errors="replace", creationflags=0x08000000)
    line = r.stdout.strip()
    return (int(line.split("|")[0]), line.split("|", 1)[1]) if "|" in line else (0, "")


def windows(p):
    found = []
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(h, _):
        owner = wintypes.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(owner))
        if owner.value == p and u.IsWindowVisible(h):
            found.append(h)
        return True

    u.EnumWindows(proto(cb), 0)
    return found


def text(h):
    b = ctypes.create_unicode_buffer(140)
    u.GetWindowTextW(h, b, 140)
    return b.value


def alive(p):
    r = subprocess.run(["tasklist", "/FI", f"PID eq {p}", "/NH"], capture_output=True, text=True, errors="replace")
    return str(p) in r.stdout


def row(p):
    fg = u.GetForegroundWindow()
    tops = windows(p)
    if tops:
        h = tops[0]
        r = wintypes.RECT()
        u.GetWindowRect(h, ctypes.byref(r))
        title = text(h)
        mode = f"popped out {r.right - r.left}x{r.bottom - r.top}" + (" MINIMIZED" if u.IsIconic(h) else "") + (" in front" if h == fg else f" | in front: {text(fg)[:28]}")
    else:
        title = ""
        try:
            info = FRAME.read_text(encoding="utf-8", errors="replace")
            title = next((l[7:] for l in info.splitlines() if l.startswith("title: ")), "")
            at = next((l[6:] for l in info.splitlines() if l.startswith("time: ")), "")
            title += f" (live picture {at[-8:]})"
        except OSError:
            pass
        mode = f"inside the app | in front: {text(fg)[:28]}"
    return f"{time.strftime('%H:%M:%S')} {mode} | {title}"


say(f"--- tracker waiting for a game {time.strftime('%H:%M:%S')}")
start = time.time()
p, path = 0, ""
while not p and time.time() - start < 1800:
    p, path = find()
    if not p:
        time.sleep(5)
if not p:
    say("--- no game opened in 30 minutes")
    print("no game opened", flush=True)
    raise SystemExit
say(f"--- watching {path} {time.strftime('%H:%M:%S')}")
print("watching", path, flush=True)
while alive(p) and time.time() - start < 7200:
    say(row(p))
    time.sleep(5)
say(f"--- closed {time.strftime('%H:%M:%S')}")
print("closed", flush=True)
