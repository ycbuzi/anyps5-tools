"""Blu's machines for the local AI (11-08): one short status of each, and the start of their offline jobs.

Blu: talk to the assistant and have it know about, and work with, all his machines (the AnyPS5 Machines app shows
them; this is the same set). Every remote command is fixed text here: a machine and a job are looked up in the
tables below and PR numbers are checked, so nothing the model writes reaches a shell. Nothing reboots, deletes,
pushes or posts. Each ssh has a short connect timeout and a hard time limit, so the chat never hangs.

usage: python fleet.py status [machine]
       python fleet.py jobs
       python fleet.py run <machine> <job> [PR numbers]
"""
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

LIVE = Path(__file__).resolve().parents[2] / "live"
QR = "$PREFIX/var/lib/proot-distro/containers/debian/rootfs/root/qtest"
LOAD = "echo \"load: $(uptime | sed 's/.*load average[s]*: //')\"\n"

MACHINES = {
    "azbsd": "Azure FreeBSD VM: kernel reference probes, hourly, and a stress repeat",
    "macbook": "MacBook (macOS Ventura): PR tests run on GitHub now (fork CI, QEMU CPU lanes); mac_prtest.sh only when named",
    "quest": "Meta Quest 2 (Termux + Debian): game test pipeline",
    "wsl": "PC WSL Arch (archlinux): Quest builds for the Quest and the ARM lane (took over from the Chromebook 11-16)",
}
RETIRED = {"chromebook": "retired 11-16: the Chromebook runs AnyPS5 OS now; its Quest builds moved to wsl (PC WSL Arch), "
                         "its no-AVX PR coverage moved to GitHub (fork CI QEMU -cpu Westmere lane, 11-17)"}
ALIASES = {"arm": "azarm", "azure arm": "azarm", "ubuntu": "azarm", "lane": "azarm", "bsd": "azbsd", "freebsd": "azbsd",
           "probes": "azbsd", "x64": "azlinux", "azure x64": "azlinux", "azlinux": "azlinux", "cb": "chromebook", "chromebook": "chromebook",
           "wsl": "wsl", "arch": "wsl", "wsl arch": "wsl", "builder": "wsl", "quest builder": "wsl", "qbuild": "wsl", "mac": "macbook", "macbook": "macbook",
           "quest": "quest", "quest2": "quest", "quest 2": "quest", "headset": "quest", "azarm": "azarm", "azbsd": "azbsd"}

STATUS = {
    "azarm": LOAD + """s=$(systemctl is-active alane.service)
case $s in active|activating) echo "now: test round running";; *) echo "now: idle";; esac
echo "last: $(grep -v '^#' ~/alane/results.txt | tail -n 1)"
""",
    "azbsd": LOAD + """now=""
pgrep -f 'probes/run.sh' >/dev/null && now="probes running"
pgrep -f 'repeat.sh' >/dev/null && now="${now:+$now, }stress repeat running"
echo "now: ${now:-idle}"
echo "last: $(stat -f '%Sm' -t '%m-%d %H:%M' ~/probes/results.txt) $(grep -c . ~/probes/results.txt) probe lines; changes: $(head -n 1 ~/probes/results.diff)"
""",
    "azlinux": LOAD + """now=""
pgrep -f '[x]64_prtest.sh' >/dev/null && now="PR tests running"
echo "now: ${now:-idle}"
echo "last: $(grep . ~/x64-work/status.md | tail -n 1)"
""",
    "wsl": LOAD + """st=$(cat /root/qb/state.txt 2>/dev/null)
dr=$(cat "/mnt/c/AnyPS5 Games/_tools/live/wsl-qbuild.state" 2>/dev/null)
pgrep -f '[q]b-quest-builds.sh$' >/dev/null && now="building: $st" || now="idle; driver: ${dr:-no state (is the task 'AnyPS5 WSL Quest builder' running?)}"
echo "now: $now"
echo "last: $(grep -E ' (SENT|FAIL|SKIP|BUILT-NOT-SENT|STOP) ' /root/qb/logs/quest-builds.log 2>/dev/null | tail -n 1)"
""",
    "macbook": LOAD + """now=""
[ -d ~/mac-work/prtest.lock ] && now="PR tests running"
pgrep -f '[c]laude -p' >/dev/null && now="${now:+$now, }Claude job running"
echo "now: ${now:-idle}"
echo "last: $(grep . ~/mac-work/status.md | tail -n 1)"
""",
    "quest": LOAD + f"""R={QR}
pgrep -f 'qtest/qpipe.sh' >/dev/null && p="pipeline up" || p="pipeline DOWN"
t=""
if [ -s $R/run/current-test ] && ! grep -q '^RESULT' $R/run/current-test; then t="; testing $(cut -f1 $R/run/current-test | head -n 1)"; fi
pgrep -f 'qpipe.sh nightly-now' >/dev/null && t="$t; game tests running"
echo "now: $p, $(head -n 1 $R/status.txt)$t"
echo "last: $(tail -n 3 $R/results.tsv | cut -f2,3,6 | tr '\\t' ' ' | sed 's/;.*frames:/ frames:/; s/, captures.*//' | paste -sd '|' -)"
""",
}

BG = "> /dev/null 2>&1 < /dev/null &"
JOBS = {
    "azarm": {
        "round": ("a Quest-style test round now (alane.service; results in ~/alane/results.txt)", """case $(systemctl is-active alane.service) in
active|activating) echo "busy: a test round is already running";;
*) sudo -n systemctl start --no-block alane.service && echo "started a test round; results land in ~/alane/results.txt";;
esac"""),
    },
    "azbsd": {
        "probes": ("every FreeBSD kernel probe now (run.sh; results.txt and results.diff)", f"""if pgrep -f 'probes/run.sh' >/dev/null; then echo "busy: the probes are already running"
else nohup lockf -k ~/probes/.run.lock nice -n 10 sh ~/probes/run.sh > ~/probes/manual.log 2>&1 < /dev/null &
sleep 3
if pgrep -f 'probes/run.sh' >/dev/null; then echo "started the probes (they wait for a stress round that holds the lock); results in ~/probes/results.txt, changes in results.diff"
else echo "it ended at once: $(tail -n 2 ~/probes/manual.log)"; fi; fi"""),
        "stress": ("one stress repeat round (repeat.sh)", f"""if pgrep -f 'repeat.sh' >/dev/null; then echo "busy: a stress repeat is already running"
else nohup nice -n 10 sh ~/probes/repeat.sh {BG} echo "started a stress repeat; log ~/probes/repeat.log"; fi"""),
    },
    "azlinux": {
        "prtests": ("its offline PR tests on the given PRs (x64_prtest.sh; reports in ~/x64-work/prtests)", """if pgrep -f '[x]64_prtest.sh' >/dev/null; then echo "busy: PR tests are already running"
else nohup bash ~/x64-work/bin/x64_prtest.sh {prs} > ~/x64-work/logs/prtest-assistant.log 2>&1 < /dev/null &
sleep 3
if pgrep -f '[x]64_prtest.sh' >/dev/null; then echo "started PR tests on {prs}; reports in ~/x64-work/prtests/<N>.md"
else echo "it ended at once: $(tail -n 2 ~/x64-work/logs/prtest-assistant.log)"; fi; fi"""),
    },
    "wsl": {
        "questbuild": ("a Quest build check now: the WSL builder builds open requests under the PC lock and sends them to the Quest", """touch "/mnt/c/AnyPS5 Games/_tools/live/wsl-qbuild.kick" && echo "asked the WSL builder to check its requests now (open: $(bash /root/qb/bin/qb-quest-builds.sh pending)); it builds them when it gets the PC lock\""""),
    },
    "macbook": {
        "prtests": ("its offline PR tests on the given PRs (mac_prtest.sh; reports in ~/mac-work/prtests)", """if [ -d ~/mac-work/prtest.lock ]; then echo "busy: PR tests are already running"
else nohup bash ~/mac-work/mac_prtest.sh {prs} > ~/mac-work/logs/prtest-assistant.log 2>&1 < /dev/null &
sleep 3
if [ -d ~/mac-work/prtest.lock ]; then echo "started PR tests on {prs}; reports in ~/mac-work/prtests/<N>.md"
else echo "it ended at once: $(tail -n 2 ~/mac-work/logs/prtest-assistant.log)"; fi; fi"""),
    },
    "quest": {
        "tests": ("the Quest game tests now (qpipe.sh nightly-now)", f"""R={QR}
if pgrep -f 'qpipe.sh nightly-now' >/dev/null; then echo "busy: the game tests are already running"
elif [ -s $R/run/current-test ] && ! grep -q '^RESULT' $R/run/current-test; then echo "busy: the Quest pipeline is testing $(cut -f1,2 $R/run/current-test | head -n 1 | tr '\t' ' ') right now; ask again when it is done"
else nohup proot-distro login debian --shared-tmp -- env NICE_REL=0 nice -n 15 /root/qtest/qpipe.sh nightly-now > $R/run/assistant-nightly.log 2>&1 < /dev/null &
sleep 4
if pgrep -f 'qpipe.sh nightly-now' >/dev/null; then echo "started the Quest game tests; results in app-runs.log"
else echo "busy: the Quest pipeline skipped it (headset worn, or a test holds the lock); its state: $(head -n 1 $R/status.txt) $(tail -n 1 $R/run/assistant-nightly.log)"; fi; fi"""),
        "check": ("a check for new builds now (touches run/trigger)", f"""touch {QR}/run/trigger && echo "asked the Quest pipeline to check for new builds now\""""),
    },
}
JOB_ALIASES = {
    "azarm": {"round": "round", "test": "round", "tests": "round", "lane": "round", "test_round": "round"},
    "azbsd": {"probes": "probes", "probe": "probes", "test": "probes", "tests": "probes", "kernel": "probes", "stress": "stress", "repeat": "stress"},
    "azlinux": {"prtests": "prtests", "pr": "prtests", "prs": "prtests", "pr_tests": "prtests", "test": "prtests", "tests": "prtests"},
    "wsl": {"questbuild": "questbuild", "quest_build": "questbuild", "build": "questbuild", "quest": "questbuild", "builds": "questbuild"},
    "macbook": {"prtests": "prtests", "pr": "prtests", "prs": "prtests", "pr_tests": "prtests", "test": "prtests", "tests": "prtests"},
    "quest": {"tests": "tests", "test": "tests", "games": "tests", "game_tests": "tests", "nightly": "tests", "round": "tests",
              "check": "check", "builds": "check", "trigger": "check", "new_builds": "check"},
}


def machine_name(text):
    t = re.sub(r"\s+", " ", str(text or "").strip().lower())
    return ALIASES.get(t) or ALIASES.get(t.replace(" ", ""))


def job_name(machine, text):
    t = re.sub(r"[\s-]+", "_", str(text or "").strip().lower())
    return JOB_ALIASES.get(machine, {}).get(t)


AZBSD_ALLOWED = {"192.0.2.1", "192.0.2.1", "192.0.2.1"}


def _azure_hint():
    try:
        r = subprocess.run(["ssh", "-o", "ConnectTimeout=6", "-o", "BatchMode=yes", "macbook", "curl -s -4 --max-time 8 https://api.ipify.org"],
                           capture_output=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        home = r.stdout.decode().strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if home and home not in AZBSD_ALLOWED:
        return (f". Likely cause: the home IP is now {home}, which the Azure firewall (anyps5-bsd-nsg, SSH rule) does not allow. "
                "Blu adds it in the portal (VM > Network settings > SSH > Source IP addresses), then update AZBSD_ALLOWED in fleet.py")
    return ". Home IP is allowed; if the PC's ProtonVPN is on, check that 192.0.2.1 is excluded (split tunneling, Standard mode)"


WSL = [os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "wsl.exe"), "-d", "archlinux", "-u", "root", "--"]


def _ssh(machine, script, seconds=20):
    """Runs a fixed script on a machine over ssh; 'wsl' runs it in the PC's WSL Arch instead."""
    if machine == "wsl":
        try:
            r = subprocess.run(WSL + ["sh", "-s"], input=script.replace("\r", "").encode("utf-8"), capture_output=True,
                               timeout=seconds, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            return None, f"no answer within {seconds} s"
        except OSError as e:
            return None, f"wsl.exe failed: {e}"
        out, err = (x.decode("utf-8", "replace").replace("\r", "").replace("\x00", "").strip() for x in (r.stdout, r.stderr))
        if r.returncode and not out and ("archlinux" in err or "WSL" in err):
            return None, "DOWN (WSL archlinux did not start: " + _short(err, 120) + ")"
        return r.returncode, (out + (f"\n{err}" if r.returncode and err else "")).strip()
    try:
        r = subprocess.run(["ssh", "-o", "ConnectTimeout=6", "-o", "BatchMode=yes", "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=2",
                            machine, "sh -s"], input=script.replace("\r", "").encode("utf-8"), capture_output=True,
                           timeout=seconds, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return None, f"no answer within {seconds} s"
    except OSError as e:
        return None, f"ssh failed: {e}"
    if r.returncode == 255:
        return None, "DOWN (not reachable over ssh)" + (_azure_hint() if machine == "azbsd" else "")
    out, err = (x.decode("utf-8", "replace").replace("\r", "").strip() for x in (r.stdout, r.stderr))
    return r.returncode, (out + (f"\n{err}" if r.returncode and err else "")).strip()


def _chromebook_os():
    key = os.path.expanduser("~/.ssh/id_ed25519_chromebook")
    probe = ("echo \"up $(uptime -p | sed 's/^up //'), root free $(df -h / | awk 'NR==2{print $4}')\"; "
             "pgrep -x anyps5-console >/dev/null && echo 'console: running' || echo 'console: NOT running'; "
             "g=$(pgrep -a -f '/games/.*/game$' | grep -v '/.anyps5-build' | head -1 | sed 's|.*/games/||;s|/game$||'); echo \"game: ${g:-none}\"; "
             "c=$(bluetoothctl devices Connected 2>/dev/null | cut -d' ' -f3- | paste -sd, -); echo \"bluetooth: ${c:-nothing connected}\"; "
             "u=$(ls /dev/input/by-id 2>/dev/null | grep -ci joystick); echo \"usb pads: $u\"; "
             "f=$(python3 -c 'import glob,json; print(\", \".join(\"%s (%s)\" % (d.get(\"title\"), (d.get(\"error\") or \"\")[:90]) for d in (json.load(open(p)) for p in glob.glob(\"/home/anyps5/.local/share/anyps5/collection/*.json\")) if d.get(\"state\") == \"failed\"))' 2>/dev/null); "
             "[ -z \"$f\" ] || echo \"INSTALL FAILED: $f\"")
    try:
        r = subprocess.run(["ssh", "-o", "ConnectTimeout=6", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
                            "-o", "UserKnownHostsFile=" + os.path.expanduser("~/.ssh/known_hosts_anyps5os"), "-i", key,
                            "user@example.com", probe], capture_output=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (subprocess.TimeoutExpired, OSError):
        return "chromebook (AnyPS5 OS): no answer within 20 s"
    text = r.stdout.decode("utf-8", "replace").strip()
    if r.returncode != 0 or not text:
        return "chromebook (AnyPS5 OS): DOWN (not reachable over ssh)"
    return "chromebook (AnyPS5 OS): " + "; ".join(text.splitlines())


def _short(line, n=170):
    line = re.sub(r"\s+", " ", line).strip()
    return line if len(line) <= n else line[:n - 3] + "..."


def status(machine=""):
    if machine and machine.lower() not in ("all", "every", "everything"):
        name = machine_name(machine)
        if name == "chromebook":
            return _chromebook_os()
        if name in RETIRED:
            return f"{name}: {RETIRED[name]}"
        if not name:
            return f"unknown machine {machine!r}: use {', '.join(MACHINES)} (or all)"
        names = [name]
    else:
        names = list(MACHINES)
    out = {}

    def one(n):
        rc, text = _ssh(n, STATUS[n])
        if rc is None:
            out[n] = f"{n}: {text}"
            return
        rows = {k: v for k, _, v in (l.partition(": ") for l in text.splitlines()) if v}
        out[n] = f"{n}: up, load {rows.get('load', '?')}; {_short(rows.get('now', '?'), 120)}\n  last: {_short(rows.get('last', '?'))}"
    threads = [threading.Thread(target=one, args=(n,), daemon=True) for n in names]
    for t in threads:
        t.start()
    for t in threads:
        t.join(25)
    lines = [out.get(n, f"{n}: no answer within 25 s") for n in names]
    if len(names) > 1:
        lines.append(_chromebook_os())
    return "\n".join(lines)


def jobs():
    lines = ["Jobs the machines tool can start (each says busy instead of starting twice):"]
    for m, table in JOBS.items():
        for j, (what, _) in table.items():
            lines.append(f"  {m} {j}: {what}" + (" (needs PR numbers)" if "{prs}" in table[j][1] else ""))
    return "\n".join(lines)


def check(machine, job, prs=""):
    """(ok, what or error, machine, job, PR numbers) without running anything."""
    m = machine_name(machine)
    if m in RETIRED:
        return False, f"{m}: {RETIRED[m]}", None, None, []
    if not m:
        return False, f"unknown machine {machine!r}: use {', '.join(MACHINES)}", None, None, []
    j = job_name(m, job)
    if not j:
        return False, f"{m} has no job {job!r}. Its jobs: {', '.join(JOBS[m])}", m, None, []
    nums = re.findall(r"\b\d{3,6}\b", str(prs or ""))[:3]
    what, script = JOBS[m][j]
    if "{prs}" in script and not nums:
        return False, f"{m} {j} needs PR numbers (up to 3)", m, j, []
    if m == "macbook" and (LIVE / "mac-paused.flag").exists():
        return False, "the Mac's jobs are paused (live\\mac-paused.flag): Blu is using it", m, j, nums
    return True, f"{m}: {what}" + (f" for {' '.join(nums)}" if nums else ""), m, j, nums


def run(machine, job, prs=""):
    ok, what, m, j, nums = check(machine, job, prs)
    if not ok:
        return "NOT STARTED: " + what
    script = JOBS[m][j][1].replace("{prs}", " ".join(nums))
    rc, text = _ssh(m, script, 30)
    if rc is None:
        return f"NOT STARTED: {m} {text}"
    text = _short(text, 300)
    done = re.match(r"(started|asked)\b", text)
    return f"{'STARTED' if done else 'NOT STARTED'}: {m} {j}: {text}"


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = sys.argv[1:] or ["status"]
    if args[0] == "status":
        print(status(" ".join(args[1:])))
    elif args[0] == "jobs":
        print(jobs())
    elif args[0] == "run" and len(args) >= 3:
        result = run(args[1], args[2], " ".join(args[3:]))
        print(result)
        sys.exit(0 if result.startswith("STARTED") else 1)
    else:
        print(__doc__)
        sys.exit(2)
