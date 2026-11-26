"""Keeps the WSL builder and the Quest busy (Blu 11-17: every device always working, wasted time is wasted usage).

Run every 15 minutes by the scheduled task "AnyPS5 idle filler". When the Quest's test pipeline is idle and upstream
main has a commit the WSL builder has not built yet, it queues that commit in /root/qb/quest-requests.txt
(replacing an older main it queued, so only the newest main waits); the
"AnyPS5 WSL Quest builder" task builds it under the PC lock and sends it to the Quest, whose qpipe tests every game.
The Chromebook fills its own idle time (anyps5-idle-tests.timer on it). Offline only: no Claude, no pushes.
"""
import datetime
import subprocess
import sys

LOG = r"C:\AnyPS5 Games\_reports\idle-filler.log"
REPO = "https://github.com/boykopovar/AnyPS5"
QUEST_STATUS = "cat $PREFIX/var/lib/proot-distro/containers/debian/rootfs/root/qtest/status.txt | head -1"


def run(cmd, timeout=60):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace", creationflags=0x08000000)
        return r.returncode, r.stdout.strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, str(e)


def log(line):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(datetime.datetime.now().strftime("%Y-%m-%d %H:%M ") + line + "\n")


def wsl(script):
    return run(["wsl", "-e", "bash", "-c", script])


def main():
    rc, status = run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "quest", QUEST_STATUS])
    if rc != 0:
        log("quest not reachable: " + status[:120])
        return 0
    if "idle" not in status:
        return 0
    rc, out = run(["git", "ls-remote", REPO, "refs/heads/main"])
    if rc != 0 or not out:
        log("main not readable: " + out[:120])
        return 0
    sha = out.split()[0]
    rc, have = wsl("grep -c '%s' /root/qb/quest-sent.txt /root/qb/quest-requests.txt 2>/dev/null | awk -F: '{s+=$2} END {print s+0}'" % sha[:10])
    if rc != 0:
        log("wsl not answering: " + have[:120])
        return 0
    if have.strip() not in ("", "0"):
        return 0
    rc, out = wsl("sed -i -E '/^[0-9a-f]{40}( \\S+ refs\\/heads\\/main main-[0-9a-f]{11})?$/d' /root/qb/quest-requests.txt && echo '%s %s refs/heads/main main-%s' >> /root/qb/quest-requests.txt" % (sha, REPO, sha[:11]))
    log("quest idle, queued main %s for the WSL builder" % sha[:10] if rc == 0 else "queue failed: " + out[:120])
    return 0


if __name__ == "__main__":
    sys.exit(main())
