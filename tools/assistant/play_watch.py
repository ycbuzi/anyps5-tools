import ctypes
import json
import os
import re
import sys
import time

GAMES = r"D:\AnyPS5 Games"
INTEREST = re.compile(r"(?i)(abort|fatal|crash|sigsegv|exception|not implemented|notimplemented|unsupported|missing|device lost|device_lost|stopped|stop:|assert|failed to|error:|\[error\]|hang|timeout|out of memory)")
NOISE = re.compile(r"(?i)(warning: cannot dlopen|validation layer|shader cache miss)")
k32 = ctypes.windll.kernel32
psapi = ctypes.windll.psapi


def processes():
    arr = (ctypes.c_ulong * 4096)()
    needed = ctypes.c_ulong()
    psapi.EnumProcesses(ctypes.byref(arr), ctypes.sizeof(arr), ctypes.byref(needed))
    out = {}
    for pid in arr[: needed.value // 4]:
        h = k32.OpenProcess(0x1000, False, pid)
        if not h:
            continue
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.c_ulong(1024)
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            out[pid] = buf.value
        k32.CloseHandle(h)
    return out


GAME_DIR = re.compile(r"(?i)^[a-z]:\\AnyPS5 Games\\[^\\]+\\[^\\]+\.exe$")


def running_game():
    for pid, exe in processes().items():
        if GAME_DIR.match(exe) and "\\_" not in exe:
            return pid, os.path.dirname(exe), os.path.basename(exe)
    return None


def say(msg):
    print(time.strftime("%H:%M:%S"), msg[:400], flush=True)


def report_summary(folder):
    try:
        d = json.load(open(os.path.join(folder, "anyps5-report.json"), encoding="utf-8-sig"))
    except (OSError, ValueError):
        return "no report"
    run = d.get("run", {})
    stop = (d.get("stop") or {}).get("message", "")
    miss = [m.get("name") for m in (d.get("missing") or {}).get("functions", [])][:6]
    ms = run.get("milestones", {})
    return f"exit={run.get('exit')} stage={run.get('stage')} secs={run.get('seconds')} firstPresent={ms.get('firstPresent')} stop={stop[:200]} missing={miss}"


def main():
    current = None
    log_path = None
    pos = 0
    seen = {}
    last_beat = time.time()
    while True:
        g = running_game()
        if g and (not current or g[0] != current[0]):
            current = g
            log_path = os.path.join(g[1], "anyps5.log")
            pos = os.path.getsize(log_path) if os.path.exists(log_path) else 0
            seen.clear()
            say(f"START {os.path.basename(g[1])} ({g[2]}, pid {g[0]})")
        elif current and not g:
            time.sleep(4)
            say(f"EXIT {os.path.basename(current[1])}: {report_summary(current[1])}")
            current = None
            log_path = None
        if log_path and os.path.exists(log_path):
            size = os.path.getsize(log_path)
            if size < pos:
                pos = 0
            if size > pos:
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(pos)
                    chunk = f.read(min(size - pos, 4_000_000))
                    pos = f.tell()
                for line in chunk.splitlines():
                    if INTEREST.search(line) and not NOISE.search(line):
                        key = re.sub(r"0x[0-9a-fA-F]+|\d+", "#", line)[:160]
                        seen[key] = seen.get(key, 0) + 1
                        if seen[key] in (1, 50, 1000):
                            say(("x%d " % seen[key] if seen[key] > 1 else "") + "LOG " + line.strip())
        if time.time() - last_beat > 1800:
            last_beat = time.time()
            say("still watching" + (f" {os.path.basename(current[1])}" if current else " (no game running)"))
        time.sleep(3)


if __name__ == "__main__":
    main()
