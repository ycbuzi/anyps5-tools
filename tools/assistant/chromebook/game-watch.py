#!/usr/bin/env python3
import fcntl
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time

DIR = "/root/idle-tests"
STATE = DIR + "/game-watch-state.json"
REPORT = DIR + "/game-watch.md"
LOG = DIR + "/game-watch.log"
SHOTS = DIR + "/watch-shots"
ACTIVE = "/run/anyps5-input-active"
GAMES = "/home/anyps5/games"
HAND_S = 300
PRESS_AFTER_S = 180
STUCK_S = 480
CLOSE_FROZEN_S = 1200
HOST_CFG = "/root/idle-tests/cb-idle-tests.py"
SAME_DIFF = 2.0
BROKEN = re.compile(r"invalid ELF header|wrong ELF class|undefined symbol|terminate called|what\(\):|unhandled exception|Needed module not converted")


def log(line):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(time.strftime("%m-%d %H:%M ") + line + "\n")


def load():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save(st):
    with open(STATE + ".tmp", "w", encoding="utf-8") as f:
        json.dump(st, f)
    os.replace(STATE + ".tmp", STATE)


def running_game():
    r = subprocess.run(["pgrep", "-af", "^" + GAMES + "/.*/game$"], capture_output=True, text=True)
    for line in r.stdout.splitlines():
        pid, _, cmd = line.partition(" ")
        return int(pid), os.path.dirname(cmd)
    return None, None


def started(pid):
    ticks = int(open("/proc/%d/stat" % pid).read().rsplit(")", 1)[1].split()[19])
    boot = time.time() - float(open("/proc/uptime").read().split()[0])
    return boot + ticks / os.sysconf("SC_CLK_TCK")


def input_idle():
    try:
        return time.time() - os.path.getmtime(ACTIVE)
    except OSError:
        return float("inf")


def broken_lines(folder):
    path = os.path.join(folder, "last-run.log")
    found = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f.read(8 << 20).splitlines():
                if BROKEN.search(line):
                    line = line.strip().replace(folder + "/", "")
                    if line[:160] not in found:
                        found.append(line[:160])
    except OSError:
        pass
    return found[:4]


def shot(folder, name):
    shots = os.path.join(folder, "anyps5-screenshots")
    before = set(os.listdir(shots)) if os.path.isdir(shots) else set()
    press("f12")
    path = None
    for _ in range(100):
        time.sleep(0.2)
        new = sorted(set(os.listdir(shots)) - before) if os.path.isdir(shots) else []
        if new:
            candidate = os.path.join(shots, new[-1])
            if os.path.getsize(candidate) > 12:
                with open(candidate, "rb") as f:
                    f.seek(-8, os.SEEK_END)
                    if f.read(4) == b"IEND":
                        path = candidate
                        break
    if not path:
        return None
    r = subprocess.run(["nice", "-n", "19", "ffmpeg", "-v", "error", "-i", path, "-vf", "scale=64:36,format=gray", "-f", "rawvideo", "-"],
                       capture_output=True, timeout=60)
    os.makedirs(SHOTS, exist_ok=True)
    shutil.move(path, os.path.join(SHOTS, name + ".png"))
    return list(r.stdout) if len(r.stdout) == 64 * 36 else None


def diff(a, b):
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def press(key):
    code = {"enter": 28, "f12": 88}[key]
    fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
    try:
        fcntl.ioctl(fd, 0x40045564, 1)
        fcntl.ioctl(fd, 0x40045565, code)
        fcntl.ioctl(fd, 0x405C5503, struct.pack("HHHH80sI", 0x06, 0x1209, 0x5007, 1, b"AnyPS5 watch keyboard", 0))
        fcntl.ioctl(fd, 0x5501)
        time.sleep(1.5)
        for value in (1, 0):
            os.write(fd, struct.pack("llHHi", 0, 0, 1, code, value))
            os.write(fd, struct.pack("llHHi", 0, 0, 0, 0, 0))
            time.sleep(0.12)
        time.sleep(0.5)
        fcntl.ioctl(fd, 0x5502)
    finally:
        os.close(fd)


def close_game():
    import urllib.request
    host = re.search(r'HOST\s*=\s*"([^"]+)"', open(HOST_CFG, encoding="utf-8").read()).group(1)
    req = urllib.request.Request(host + "/msg", data=json.dumps({"closegame": True}).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        r.read()


def write_report(st, now):
    lines = ["# Chromebook game watch " + time.strftime("%m-%d %H:%M", time.localtime(now)), ""]
    g = st.get("game")
    if g:
        title = os.path.basename(g["folder"])
        mins = int((now - g["since"]) / 60)
        if g.get("hand"):
            lines.append("- %s: running %d min, played by hand" % (title, mins))
        else:
            same = int((now - g["changed"]) / 60)
            dark = int((now - g.get("shot_at", g["since"])) / 60)
            if g.get("frozen"):
                state = "FROZEN: no new frame for %d min" % dark
            elif g.get("frame") is None:
                state = "no screenshot yet"
            elif dark >= 3:
                state = "no new frame for %d min" % dark
            elif g.get("stuck"):
                state = "STUCK on one screen for %d min" % same
            else:
                state = "screen moving" if same < 3 else "same screen for %d min" % same
            lines.append("- %s: running %d min, %s%s" % (title, mins, state, ", pressed X %d times" % g["presses"] if g.get("presses") else ""))
    else:
        lines.append("- no game running")
    problems = st.get("problems", {})
    if problems:
        lines += ["", "Problems found (newest first):"]
        for title, p in sorted(problems.items(), key=lambda kv: -kv[1]["when"])[:12]:
            lines.append("- %s %s: %s" % (time.strftime("%m-%d %H:%M", time.localtime(p["when"])), title, p["what"]))
    with open(REPORT + ".tmp", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(REPORT + ".tmp", REPORT)


def problem(st, title, what, now):
    old = st.setdefault("problems", {}).get(title)
    if old and old["what"] == what:
        return
    st["problems"][title] = {"what": what, "when": now}
    log("PROBLEM %s: %s" % (title, what))


def main():
    st = load()
    now = time.time()
    pid, folder = running_game()
    if not pid:
        st.pop("game", None)
        write_report(st, now)
        save(st)
        return
    title = os.path.basename(folder)
    g = st.get("game")
    if not g or g.get("pid") != pid:
        g = {"pid": pid, "folder": folder, "since": started(pid), "changed": now, "frame": None, "presses": 0}
        st["game"] = g
        log("watching %s" % title)
    broken = broken_lines(folder)
    if broken:
        problem(st, title, "broken build: " + " | ".join(broken), now)
    elif st.get("problems", {}).get(title, {}).get("what", "").startswith("broken build") and now - g["since"] > 120:
        del st["problems"][title]
        log("%s: loads cleanly now" % title)
    if input_idle() < HAND_S:
        if not g.get("hand"):
            log("%s: played by hand, screen checks paused" % title)
        g["hand"] = True
        g["changed"] = now
        g["frame"] = None
    elif now - g["since"] > 60:
        g["hand"] = False
        frame = shot(folder, re.sub(r"[^A-Za-z0-9]+", "-", title)[:60])
        if frame is None:
            dark = now - g.get("shot_at", g["since"] + 60)
            if dark >= STUCK_S and not g.get("frozen"):
                g["frozen"] = True
                problem(st, title, "frozen: no new frame for %d min (the game stopped drawing; screenshot key saves nothing)" % (dark // 60), now)
            if dark >= CLOSE_FROZEN_S and input_idle() >= CLOSE_FROZEN_S:
                close_game()
                log("%s: frozen for %d min with nobody playing, closed it" % (title, dark // 60))
                problem(st, title, "frozen for %d min after the last picture, nobody playing: closed it" % (dark // 60), now)
        else:
            if g.get("frozen"):
                log("%s: drawing again" % title)
            g["frozen"] = False
            g["shot_at"] = now
            if g["frame"] is None or diff(frame, g["frame"]) > SAME_DIFF:
                if g.get("stuck"):
                    log("%s: screen moving again" % title)
                if g.get("presses") and g["frame"] is not None and now - g["changed"] >= PRESS_AFTER_S:
                    log("%s: got past a still screen after pressing X" % title)
                g["changed"] = now
                g["stuck"] = False
                g["frame"] = frame
                g["presses"] = 0
            else:
                same = now - g["changed"]
                if same >= PRESS_AFTER_S and g["presses"] < 6:
                    press("enter")
                    g["presses"] += 1
                if same >= STUCK_S and not g.get("stuck"):
                    g["stuck"] = True
                    problem(st, title, "stuck on one screen for %d min (pressed X %d times, nobody playing)" % (same // 60, g["presses"]), now)
    write_report(st, now)
    save(st)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("watch error: %r" % e)
        sys.exit(1)
