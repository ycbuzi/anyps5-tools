"""The other two machines and Discord, for the local AI (Blu 11-06: "anything you're able to do, the AI model should be
able to do", offline, when Claude's usage runs out). The MacBook and the Chromebook are reached with ssh (hosts
"macbook" and "chromebook" in the PC's ssh config; the Chromebook through a tunnel on the Mac). Every command here is
fixed: a machine name and PR numbers are checked before they reach a shell, and nothing is deleted or posted.
"""
import datetime
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
LIVE = TOOLS / "live"
SHOTS = TOOLS.parent / "_reports" / "shots"
MACHINES = ("macbook", "quest")
ADB = Path(r"C:\AnyPS5-tools\platform-tools\adb.exe")
QUEST_WIFI = "192.0.2.1:5555"


def _bash():
    found = shutil.which("bash")
    return found or r"C:\Program Files\Git\bin\bash.exe"


def _ssh(machine, script, timeout=60):
    if machine not in MACHINES:
        return f"unknown machine {machine!r}: use {', '.join(MACHINES)}"
    try:
        r = subprocess.run(["ssh", "-o", "ConnectTimeout=12", "-o", "BatchMode=yes", machine, "bash -s"], input=script.replace("\r", ""),
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, creationflags=0x08000000)
    except subprocess.TimeoutExpired:
        return f"{machine}: no answer within {timeout} s"
    if r.returncode == 255:
        return f"{machine}: not reachable (asleep, off, or the Chromebook's tunnel through the Mac is down)"
    return (r.stdout + (r.stderr if r.returncode else "")).strip()


def _prs(prs):
    nums = re.findall(r"\d{3,6}", str(prs))
    return nums


def machines_status():
    """One status pass over the PC, the MacBook and the Chromebook (three_check.sh): pipeline and GPU lanes, drives, RAM, the Mac's job, the Chromebook's PR tests and its Claude. PROBLEM lines say what needs fixing."""
    r = subprocess.run([_bash(), str(TOOLS / "assistant" / "three_check.sh")], capture_output=True, text=True, timeout=240, creationflags=0x08000000)
    return (r.stdout or r.stderr).strip()


def machines_live():
    """What every machine is doing right now, from live\\machines.json (remote-view.ps1 rewrites it every 15 s): the Chromebook, the MacBook and the Quest, online or not, busy or not, and the last online/offline changes. The Quest part says which game it is playing."""
    path = LIVE / "machines.json"
    if not path.exists():
        return "machines.json is missing: remote-view.ps1 is not running (the AnyPS5 Games window starts it)."
    age = int(time.time() - path.stat().st_mtime)
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    lines = [f"(updated {age} s ago{'; STALE, remote-view.ps1 may have stopped' if age > 120 else ''})"]
    for name in ("macbook", "quest"):
        m = data.get(name, {})
        state = "offline" if not m.get("online") else "busy" if m.get("busy") else "idle"
        extra = " (Mac jobs paused by live\\mac-paused.flag)" if m.get("paused") else ""
        lines.append(f"{name}: {state}{extra}  {m.get('detail', '')}".rstrip())
    events = LIVE / "machine-events.log"
    if events.exists():
        lines.append("recent changes:")
        lines += ["  " + x for x in events.read_text(encoding="utf-8-sig").splitlines()[-5:]]
    quest_view = LIVE / "quest-view.json"
    if quest_view.exists():
        q = json.loads(quest_view.read_text(encoding="utf-8-sig"))
        lines.append(f"quest live view (game display, no headset): {q.get('url', '')}")
    return "\n".join(lines)


def remote_view():
    """What the Chromebook and the MacBook are running right now, as shown beside the Live console (refreshed every 15 s)."""
    path = LIVE / "remote-view.txt"
    if not path.exists():
        return "remote-view.txt is missing: remote-view.ps1 is not running (the AnyPS5 Games window starts it)."
    age = int(time.time() - path.stat().st_mtime)
    return f"(updated {age} s ago)\n" + path.read_text(encoding="utf-8-sig")


def discord_notifications(minutes=60):
    """Discord notifications (DMs, mentions, friend requests) the AnyPS5 Games window's Discord received in the last <minutes>. Read only: the assistant never sends anything on Discord."""
    path = LIVE / "discord-notifications.jsonl"
    if not path.exists():
        return "No Discord notifications recorded yet (the AnyPS5 Games window records them since its 11-06 build)."
    since = datetime.datetime.now() - datetime.timedelta(minutes=int(minutes))
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
            if datetime.datetime.fromisoformat(row["time"]) >= since:
                n = row.get("n", {})
                rows.append(f"{row['time'][11:16]}  {n.get('title', '')}: {n.get('body', '')}".strip())
        except (ValueError, KeyError, TypeError):
            continue
    return "\n".join(rows) if rows else f"No Discord notifications in the last {minutes} minutes."


def remote_screenshot(machine):
    """A screenshot of the MacBook's screen, or of the Chromebook's Claude Code terminal, saved under _reports\\shots. Returns the file path."""
    if machine not in MACHINES:
        return f"unknown machine {machine!r}: use {', '.join(MACHINES)}"
    SHOTS.mkdir(parents=True, exist_ok=True)
    dest = SHOTS / f"{machine}-{time.strftime('%Y-%m-%d-%H%M%S')}.png"
    if machine == "quest":
        _adb("connect", QUEST_WIFI)
        folder = "/sdcard/Oculus/Screenshots"
        before = _adb("-s", QUEST_WIFI, "shell", f"ls -t {folder} 2>/dev/null | head -1")
        _adb("-s", QUEST_WIFI, "shell", "am startservice -n com.oculus.metacam/.capture.CaptureService -a TAKE_SCREENSHOT")
        time.sleep(4)
        newest = _adb("-s", QUEST_WIFI, "shell", f"ls -t {folder} 2>/dev/null | head -1").strip()
        if not newest or newest == before.strip():
            return "quest: no screenshot was saved (the headset may be asleep)"
        dest = dest.with_suffix(".jpg")
        _adb("-s", QUEST_WIFI, "pull", f"{folder}/{newest}", str(dest), timeout=60)
        if not dest.exists():
            return "quest: copying the screenshot failed"
        _adb("-s", QUEST_WIFI, "shell", f"rm -f '{folder}/{newest}'")
        return f"{dest} (Meta's screenshot of the composited headset view, the room and its windows as Blu sees them; black or grey while nobody wears the headset. Blu's rule: ask him before taking one.)"
    if machine == "macbook":
        out = _ssh(machine, "screencapture -x /tmp/remote-shot.png && echo ok")
    else:
        out = _ssh(machine, 'export DISPLAY=:0; id=$(xwininfo -root -tree | grep \'"Claude Code"\' | awk \'{print $1}\' | head -1); '
                            '[ -z "$id" ] && id=root; import -window "$id" /tmp/remote-shot.png && echo ok')
    if not out.endswith("ok"):
        return f"{machine}: screenshot failed: {out[-300:]}"
    r = subprocess.run(["scp", "-q", f"{machine}:/tmp/remote-shot.png", str(dest)], capture_output=True, text=True, timeout=120, creationflags=0x08000000)
    return str(dest) if r.returncode == 0 and dest.exists() else f"{machine}: copying the screenshot failed: {r.stderr[-200:]}"


def remote_job(machine, action, prs=""):
    """Work on the MacBook or Chromebook. action: status (what runs there), report <prs> (its test report for each PR), start_prtests <prs> (Chromebook: run its CPU PR test runner on up to 3 PR numbers when it is idle), next_job (MacBook: start its next Claude job through the dispatcher)."""
    nums = _prs(prs)
    if action == "status":
        if machine == "chromebook":
            return _ssh(machine, "pgrep -f '[c]hromebook_prtest.sh' >/dev/null && echo 'PR tests: running' || echo 'PR tests: idle'; tail -3 ~/cb-work/prtest.status; tail -5 ~/cb-work/status.md")
        return _ssh(machine, "pgrep -f '[c]laude -p' >/dev/null && echo 'Claude job: running' || echo 'Claude job: idle'; tail -4 ~/mac-work/status.md | cut -c1-300")
    if action == "report":
        if not nums:
            return "report needs PR numbers"
        folder = "~/cb-work/prtests" if machine == "chromebook" else "~/mac-work/prtests"
        review = "; echo '--- review:'; cat ~/cb-work/reviews/$n.md 2>/dev/null | head -40" if machine == "chromebook" else ""
        return _ssh(machine, f"for n in {' '.join(nums)}; do echo \"=== #$n\"; head -40 {folder}/$n.md 2>/dev/null || echo 'no report'{review}; done")
    if action == "start_prtests" and machine == "chromebook":
        if not nums:
            return "start_prtests needs PR numbers"
        return _ssh(machine, "if pgrep -f '[c]hromebook_prtest.sh' >/dev/null; then echo 'busy: the runner is already testing'; else "
                             f"nohup bash ~/cb-work/chromebook_prtest.sh {' '.join(nums[:3])} > ~/cb-work/logs/prtest-run.log 2>&1 < /dev/null & echo 'started on {' '.join(nums[:3])}'; fi")
    if action == "next_job" and machine == "macbook":
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(TOOLS / "dispatcher.ps1"), "-MacJobsPerDay", "12"],
                           capture_output=True, text=True, timeout=300, creationflags=0x08000000)
        log = (TOOLS / "dispatcher.log").read_text(encoding="utf-8-sig", errors="replace").splitlines()[-3:]
        return "dispatcher ran; last lines:\n" + "\n".join(log)
    return f"unknown action {action!r} for {machine}: status, report, start_prtests (chromebook), next_job (macbook)"


def _adb(*args, timeout=30):
    try:
        r = subprocess.run([str(ADB), *args], capture_output=True, text=True, timeout=timeout, creationflags=0x08000000)
        return (r.stdout + r.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"adb failed: {e}"


def quest(action="status"):
    """The Meta Quest 2 (Termux + Debian proot, reached with adb over Wi-Fi at 192.0.2.1 and ssh host "quest" on port 8022). action: status (adb, ssh, the setup/build log and what its Claude Code shows), reconnect (adb over Wi-Fi again and restart its ssh server), claude_screen (the Claude Code screen in its tmux session), claude_enter / claude_escape (press Enter or Esc in that Claude Code; never types text: sign-in codes are Blu's to paste)."""
    if action == "reconnect":
        out = [_adb("connect", QUEST_WIFI)]
        out.append(_adb("-s", QUEST_WIFI, "shell", "run-as com.termux /data/data/com.termux/files/usr/bin/bash -c 'export PREFIX=/data/data/com.termux/files/usr PATH=/data/data/com.termux/files/usr/bin; pgrep -x sshd >/dev/null || sshd; echo sshd ok'"))
        return "\n".join(out)
    if action in ("claude_enter", "claude_escape"):
        return _ssh("quest", f"tmux send-keys -t claude {'C-m' if action == 'claude_enter' else 'Escape'}; sleep 4; tmux capture-pane -p -t claude | grep -v '^[[:space:]]*$' | tail -12")
    if action == "claude_screen":
        return _ssh("quest", "tmux capture-pane -p -t claude | grep -v '^[[:space:]]*$' | tail -30")
    if action == "status":
        dev = _adb("devices")
        ssh = _ssh("quest", "echo ssh ok; tail -4 ~/quest-setup.log; echo '--- Claude Code:'; tmux capture-pane -p -t claude 2>/dev/null | grep -v '^[[:space:]]*$' | tail -4")
        return f"adb: {dev}\n{ssh}"
    return f"unknown action {action!r}: status, reconnect, claude_screen, claude_enter, claude_escape"


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    fn = {"status": machines_status, "view": remote_view, "discord": discord_notifications, "shot": remote_screenshot, "job": remote_job, "quest": quest}[sys.argv[1]]
    print(fn(*sys.argv[2:]))
