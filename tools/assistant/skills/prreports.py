"""Collects PR test reports from every runner into one review queue (11-09). Offline: ssh/scp only, no Claude.

Every 30 minutes (scheduled task "AnyPS5 PR reports") it copies new or changed reports into
_reports/pr-review/<PR>/<machine>.md and rewrites _reports/pr-review/queue.md, the list of PRs whose reports
changed since they were last reviewed. Stopped VMs are skipped quietly.

    python prreports.py collect          pull reports
    python prreports.py queue            print the review queue
    python prreports.py done <PR>...     mark PRs reviewed (after their comment is posted or deliberately skipped)
"""
import json
import subprocess
import sys
import time
from pathlib import Path

OUT = Path(r"C:\AnyPS5 Games\_reports\pr-review")
STATE = OUT / "state.json"
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
LINUX = {"chromebook": "~/cb-work/prtests", "macbook": "~/mac-work/prtests"}
WINDOWS = []
SSH = ["ssh", "-n", "-o", "ConnectTimeout=12", "-o", "BatchMode=yes", "-o", "LogLevel=ERROR"]


def run(cmd, seconds=60):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=seconds, creationflags=NOWIN)
        return r.returncode, r.stdout
    except (subprocess.TimeoutExpired, OSError):
        return 1, ""


def listing(machine):
    if machine in LINUX:
        d = LINUX[machine]
        rc, out = run(SSH + [machine, f'cd {d} 2>/dev/null && for f in *.md; do [ -f "$f" ] && echo "$f $(date -r "$f" +%s)"; done'])
        return d, rc, out
    ps = "Get-ChildItem C:\\x64win\\prtests\\*.md -EA 0 | ForEach-Object { \"$($_.Name) $([int64](($_.LastWriteTimeUtc - [datetime]'1970-01-01').TotalSeconds))\" }"
    rc, out = run(SSH + [machine, ps])
    return "C:/x64win/prtests", rc, out


def load():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {"seen": {}, "pending": {}}


def save(s):
    OUT.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, indent=1, sort_keys=True))
    lines = ["# PR reports waiting for review", "", f"Updated {time.strftime('%Y-%m-%d %H:%M')}", ""]
    for pr in sorted(s["pending"], key=int):
        lines.append(f"- #{pr}: {', '.join(sorted(s['pending'][pr]))} -> {OUT / pr}")
    if not s["pending"]:
        lines.append("Nothing waiting.")
    (OUT / "queue.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def collect():
    s = load()
    for m in list(LINUX) + WINDOWS:
        d, rc, out = listing(m)
        if rc != 0:
            continue
        for line in out.splitlines():
            parts = line.strip().rsplit(" ", 1)
            if len(parts) != 2 or not parts[0][:-3].isdigit():
                continue
            name, mtime = parts
            key = f"{m}/{name}"
            if s["seen"].get(key) == mtime:
                continue
            pr = name[:-3]
            dest = OUT / pr
            dest.mkdir(parents=True, exist_ok=True)
            rc2, _ = run(["scp", "-q", "-o", "ConnectTimeout=12", "-o", "BatchMode=yes", "-o", "LogLevel=ERROR",
                          f"{m}:{d}/{name}", str(dest / f"{m}.md")], 120)
            if rc2 == 0:
                s["seen"][key] = mtime
                s["pending"].setdefault(pr, [])
                if m not in s["pending"][pr]:
                    s["pending"][pr].append(m)
    save(s)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "queue"
    if cmd == "collect":
        collect()
    elif cmd == "done":
        s = load()
        for pr in sys.argv[2:]:
            s["pending"].pop(pr.lstrip("#"), None)
        save(s)
    queue = OUT / "queue.md"
    if cmd != "collect" and queue.exists():
        print(queue.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
