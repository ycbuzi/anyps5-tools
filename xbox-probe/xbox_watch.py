import json, os, re, shutil, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from scrub import scrub

WORK = os.path.join(HERE, "out", "watch")
REPORT = r"C:\AnyPS5 Games\_reports\xbox-watch.md"
HISTORY = r"C:\AnyPS5 Games\_reports\xbox-watch.jsonl"
STATE = os.path.join(WORK, "state.json")
PACKAGE = "AnyPS5.Console"
FILES = ("gamepart.txt", "anyps5.log", "xvk.log")
ISSUE = re.compile(r"NotImplemented|not implemented|unhandled|exception|Failed|failed|fatal|error|device removed|hung|stall", re.I)
NOISE = re.compile(r"sceKernelOpen\]|open failed path=|stack return address|^\s+0x")
PRESENT = re.compile(r"^present (\d+) hr \S+ tick (\d+)")
GAME = re.compile(r"^(\S+) game: games\\(.+)$")


def fetch(host, name):
    run = subprocess.run([sys.executable, "-X", "utf8", "fetchfile.py", host, name, "--package=" + PACKAGE], cwd=HERE, capture_output=True, timeout=180)
    src = os.path.join(HERE, "out", name)
    if run.returncode or not os.path.exists(src):
        return None
    dst = os.path.join(WORK, name)
    shutil.copy2(src, dst)
    return dst


def reachable(host):
    import socket
    try:
        socket.create_connection((host, 11443), 3).close()
        return True
    except OSError:
        return False


def session(path):
    lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
    start = max((i for i, l in enumerate(lines) if "ANYPS5_LOG_FILE: appending" in l), default=0)
    return lines[start:]


def fps(path):
    tail = []
    with open(path, "rb") as f:
        f.seek(max(0, os.path.getsize(path) - (256 << 10)))
        for raw in f.read().decode("utf-8", "replace").splitlines():
            m = PRESENT.match(raw)
            if m:
                tail.append((int(m.group(1)), int(m.group(2))))
    if len(tail) < 2 or tail[-1][1] <= tail[-2][1]:
        return None, tail[-1][0] if tail else 0
    return round((tail[-1][0] - tail[-2][0]) * 1000.0 / (tail[-1][1] - tail[-2][1]), 1), tail[-1][0]


def main():
    os.makedirs(WORK, exist_ok=True)
    host = open(os.path.join(HERE, "out", ".host")).read().strip()
    try:
        state = json.load(open(STATE))
    except (OSError, ValueError):
        state = {}
    now = time.strftime("%Y-%m-%d %H:%M")
    entry = {"time": now}
    if not reachable(host):
        entry["status"] = "Xbox not reachable"
    else:
        got = {name: fetch(host, name) for name in FILES}
        game, started = "", ""
        if got["gamepart.txt"]:
            for line in open(got["gamepart.txt"], encoding="utf-8", errors="replace"):
                m = GAME.match(line.strip())
                if m:
                    started, game = m.group(1), m.group(2)
        entry["game"], entry["started"] = game, started
        log = session(got["anyps5.log"]) if got["anyps5.log"] else []
        size = os.path.getsize(got["anyps5.log"]) if got["anyps5.log"] else 0
        rate, presents = fps(got["xvk.log"]) if got["xvk.log"] else (None, 0)
        same_game = state.get("game") == game and state.get("started") == started
        entry["log_growing"] = size != state.get("size") if same_game else True
        entry["presents"], entry["fps"] = presents, rate
        entry["presenting"] = presents != state.get("presents") if same_game else presents > 0
        seen = set(state.get("seen", {}).get(game, []))
        fresh = []
        for line in log:
            if ISSUE.search(line) and not NOISE.search(line):
                sig = re.sub(r"0x[0-9a-fA-F]+|\d+", "#", line)[:160]
                if sig not in seen:
                    seen.add(sig)
                    fresh.append(scrub(line)[:220])
        entry["new_issues"] = fresh[:25]
        entry["last_line"] = scrub(log[-1])[:200] if log else ""
        if not entry["log_growing"] and not entry["presenting"]:
            entry["status"] = "stopped or stuck (log and presents unchanged since last check)"
        elif entry["presenting"]:
            entry["status"] = "running, presenting frames"
        else:
            entry["status"] = "running, log moving, no new frames"
        state.update({"game": game, "started": started, "size": size, "presents": presents})
        state.setdefault("seen", {})[game] = sorted(seen)[-800:]
    json.dump(state, open(STATE, "w"))
    with open(HISTORY, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(entry) + "\n")
    lines = ["## %s  %s" % (now, entry.get("game") or "-"), "- " + entry["status"]]
    if entry.get("game"):
        lines.append("- started %s (Xbox clock), presents %s, fps %s" % (entry["started"], entry["presents"], entry["fps"]))
        lines.append("- last log line: " + entry["last_line"])
        lines += ["- new: " + x for x in entry["new_issues"]]
    old = open(REPORT, encoding="utf-8").read().split("\n## ")[1:40] if os.path.exists(REPORT) else []
    with open(REPORT, "w", encoding="utf-8", newline="\n") as f:
        f.write("# Xbox watch (xbox_watch.py every 10 min)\n\n" + "\n".join(lines) + "\n" + "".join("\n## " + s.rstrip("\n") + "\n" for s in old))


if __name__ == "__main__":
    main()
