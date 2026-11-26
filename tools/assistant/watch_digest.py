import hashlib
import json
import os
import subprocess
import sys
import time

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
LIVE = os.path.join(TOOLS, "live")
OUT = os.path.join(LIVE, "watch-digest.txt")
STATE = os.path.join(LIVE, "watch-digest.json")
CB = ["ssh", "-o", "ConnectTimeout=6", "-o", "BatchMode=yes", "-o", "UserKnownHostsFile=" + os.path.expanduser("~/.ssh/known_hosts_anyps5os"),
      "-i", os.path.expanduser("~/.ssh/id_ed25519_chromebook"), "user@example.com"]
CB_PROBE = ("tail -4 /root/idle-tests/runs.log | cut -c1-200; "
            "w=$(cat /sys/kernel/debug/dri/*/i915_wedged 2>/dev/null | head -1); [ \"$w\" = 0 ] || echo \"GPU WEDGED ($w)\"; "
            "for f in /home/anyps5/.local/share/anyps5/collection/*.json; do python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); "
            "print(\"install %s: %s %s %s\" % (d.get(\"title\"), d.get(\"state\"), d.get(\"phase\") or \"\", (d.get(\"error\") or \"\")[:120])) "
            "if d.get(\"state\") not in (\"installed\", None) else None' \"$f\"; done 2>/dev/null")
FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run(cmd, timeout):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, creationflags=FLAGS)
        return (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return "TIMEOUT after %d s" % timeout
    except OSError as e:
        return "FAILED: %s" % e


def tail(path, n):
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            return f.read().splitlines()[-n:]
    except OSError:
        return []


def sections():
    py = sys.executable
    yield "fleet", run([py, os.path.join(HERE, "skills", "fleet.py"), "status"], 120).strip().splitlines()
    yield "pc", run([py, os.path.join(HERE, "pc_anomalies.py")], 60).strip().splitlines()
    yield "chromebook", run(CB + [CB_PROBE], 30).strip().splitlines()
    yield "device-events", tail(os.path.join(LIVE, "device-events.log"), 5)
    yield "alerts", tail(os.path.join(LIVE, "pc-monitor-alerts.log"), 3)


def main():
    try:
        with open(STATE, encoding="utf-8") as f:
            seen = set(json.load(f).get("seen", []))
    except (OSError, ValueError):
        seen = set()
    lines, new, keys = [], [], []
    for name, body in sections():
        lines.append("== " + name)
        for line in body:
            line = line.rstrip()
            if not line:
                continue
            lines.append(line)
            key = hashlib.sha1((name + "|" + line).encode("utf-8")).hexdigest()[:16]
            keys.append(key)
            if key not in seen:
                new.append("[%s] %s" % (name, line))
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    head = ["watch digest %s: %d new line(s) since the last run" % (stamp, len(new))] + ["NEW " + n for n in new[:40]]
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(head + [""] + lines) + "\n")
    os.replace(tmp, OUT)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump({"seen": keys, "at": stamp}, f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
