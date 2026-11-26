"""The machines' shared job board, with the PC as the hub (Blu 11-08: the machines hand work to each other offline,
and the local AI sends them tasks the way the cloud sessions do).

Jobs:
  prtest <PR>          PR tests run on GitHub now (11-17: forkci.py, Windows 2022/2025/11-arm plus Linux QEMU SandyBridge and
                       Westmere CPU lanes). The MacBook runs mac_prtest.sh only when named ("send prtest 17548 macbook");
                       by default the remaining PR runners test the PR, and the WSL builder
                       (PC WSL Arch, x86-64 no-AVX baseline) builds its head for the Quest path: the Quest tests that
                       build, and the PC hands it to the Azure ARM lane (relink in WSL, push to azarm).
  questbuild <branch>  the WSL builder builds main, a PR (17548 or pr17548) or a branch of ycbuzi/boykopovar under the
                       PC lock, sends it to the Quest, and the PC hands it to the ARM lane.
The Quest builder moved from the Chromebook (now AnyPS5 OS, retired 11-16) to WSL Arch on the PC: requests in
/root/qb/quest-requests.txt, sent list /root/qb/quest-sent.txt, log /root/qb/logs/quest-builds.log, driven by the
scheduled task "AnyPS5 WSL Quest builder" (_tools\\assistant\\wsl-qbuild\\qb-driver.ps1, which takes the PC lock).
  questsend [folder]   an existing Quest build (default: the newest the Quest has) goes to the ARM lane and the Quest.
  probes               every FreeBSD kernel probe on azbsd.
Each machine runs its own offline script at low priority, queued behind its own lock. Nothing here calls Claude,
pushes, posts, reboots or deletes anything but this board's own /tmp/jobboard files. Results land in
D:\\AnyPS5 Games\\_reports\\jobs\\<job>\\ (one report per machine, summary.md) and one line per job in jobs.txt.
The scheduled task "AnyPS5 job board" runs `collect` every 10 minutes; it does nothing while no job is open.

usage: python jobboard.py send prtest 17548 [machines]   (machines: comma list, default all but the macbook)
       python jobboard.py send questbuild main | send questsend [folder] | send probes
       python jobboard.py list | result <job> | cancel <job> | collect | wait <job> [minutes]
"""
import datetime
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("fleet", HERE / "fleet.py")
fleet = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fleet)

BOARD = Path(r"D:\AnyPS5 Games\_reports\jobs")
INDEX = BOARD / "jobs.txt"
LOCK = BOARD / ".collect.lock"
QR = fleet.QR
ORIGIN = "https://github.com/boykopovar/AnyPS5"
FORK = "https://github.com/ycbuzi/AnyPS5"
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DEADLINE_H = {"prtest": 10, "questbuild": 8, "questsend": 4, "probes": 2}
SAFE = re.compile(r"^[A-Za-z0-9._-]+$")
QB = "/root/qb"
KICK = Path(r"C:\AnyPS5 Games\_tools\live\wsl-qbuild.kick")

RUNNERS = {
    "macbook": {"what": "MacBook (AVX)", "reports": "~/mac-work/prtests", "unlock": "pgrep -f '[m]ac_prtest.sh' >/dev/null || rmdir ~/mac-work/prtest.lock 2>/dev/null",
                "body": "while :; do echo running > {st}.state; nice -n 15 bash ~/mac-work/mac_prtest.sh {pr}; rc=$?; [ $rc -ne 3 ] && break; echo waiting > {st}.state; sleep 120; done"},
    "azlinux": {"what": "Azure x64 (AVX2)", "reports": "~/x64-work/prtests",
                "body": "while :; do echo running > {st}.state; nice -n 15 bash ~/x64-work/bin/x64_prtest.sh {pr}; rc=$?; [ $rc -ne 3 ] && break; echo waiting > {st}.state; sleep 120; done"},
    "azwin": {"what": "Azure Windows (AVX2, MinGW)", "reports": "C:/x64win/prtests", "windows": True, "body": "{pr}"},
}
AZARM_GONE = "the Azure ARM VM was deleted 11-09; no ARM lane"
QUEST_PATH = {"qbuild": "Quest build (PC WSL Arch)", "quest": "Quest 2 (box64, real headset)", "azarm": "Azure ARM lane (box64 + lavapipe)"}
WIN_LAUNCH = """$ErrorActionPreference = 'Stop'
$ps = "$env:SystemRoot\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"
$a = New-ScheduledTaskAction -Execute $ps -Argument '-NoProfile -ExecutionPolicy Bypass -File C:\\x64win\\jobrun.ps1 -Id {id} -Pr {pr}'
$p = New-ScheduledTaskPrincipal -UserId "$env:COMPUTERNAME\\blu" -LogonType S4U -RunLevel Highest
$s = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 12) -Priority 7
Register-ScheduledTask -TaskName 'jobboard-{id}' -Action $a -Principal $p -Settings $s -Force | Out-Null
New-Item -ItemType Directory -Force C:\\x64win\\jobboard | Out-Null
Set-Content 'C:\\x64win\\jobboard\\{id}.state' 'waiting'
Start-ScheduledTask -TaskName 'jobboard-{id}'
'launched'"""
WIN_COLLECT = """$st = 'C:\\x64win\\jobboard\\{id}'; $f = '{rep}'
if (Test-Path "$st.rc") {{ "rc=$(Get-Content "$st.rc")"
  if ((Test-Path $f) -and (Get-Item $f).LastWriteTime -gt (Get-Item "$st.state").CreationTime) {{ '---REPORT---'; Get-Content $f }} else {{ '---LOG---'; Get-Content "$st.log" -Tail 5 }} }}
elseif (Get-ScheduledTask -TaskName 'jobboard-{id}' -ErrorAction SilentlyContinue) {{ "state=$(Get-Content "$st.state")" }}
else {{ 'lost' }}"""
WIN_CANCEL = """Stop-ScheduledTask -TaskName 'jobboard-{id}' -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName 'jobboard-{id}' -Confirm:$false -ErrorAction SilentlyContinue
Get-Process ninja, ctest -ErrorAction SilentlyContinue | Stop-Process -Force
'stopped'"""

LABELS = dict({m: r["what"] for m, r in RUNNERS.items()}, **QUEST_PATH, azbsd="Azure FreeBSD probes")


def now():
    return datetime.datetime.now().strftime("%m-%d %H:%M")


def ssh(machine, script, seconds=40):
    if RUNNERS.get(machine, {}).get("windows"):
        import base64
        enc = base64.b64encode(script.replace("\r", "").encode("utf-16-le")).decode()
        try:
            r = subprocess.run(["ssh", "-o", "ConnectTimeout=6", "-o", "BatchMode=yes", machine,
                                f"powershell -NoProfile -NonInteractive -EncodedCommand {enc}"],
                               capture_output=True, timeout=seconds, creationflags=NOWIN)
        except subprocess.TimeoutExpired:
            return None, f"no answer within {seconds} s"
        if r.returncode == 255:
            return None, "DOWN (not reachable over ssh)"
        out = r.stdout.decode("utf-8", "replace").replace("\r", "").strip()
        return r.returncode, out
    return fleet._ssh(machine, script, seconds)


def save(job):
    d = BOARD / job["id"]
    d.mkdir(parents=True, exist_ok=True)
    (d / "job.json").write_text(json.dumps(job, indent=1), encoding="utf-8")


def load(job_id):
    p = BOARD / job_id / "job.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def all_jobs():
    if not BOARD.exists():
        return []
    jobs = [load(p.name) for p in BOARD.iterdir() if (p / "job.json").exists()]
    return sorted((j for j in jobs if j), key=lambda j: j["created"])


def find(job_id):
    job_id = (job_id or "").strip()
    jobs = all_jobs()
    if not job_id or job_id in ("last", "latest", "newest"):
        return jobs[-1] if jobs else None
    exact = load(job_id) if SAFE.match(job_id) else None
    if exact:
        return exact
    hits = [j for j in jobs if job_id in j["id"]]
    return hits[-1] if hits else None


def launch(machine, job_id, body):
    """A detached wrapper on the machine: it waits for the machine's own lock, runs, and leaves /tmp/jobboard/<id>.rc."""
    if RUNNERS.get(machine, {}).get("windows"):
        rc, text = ssh(machine, WIN_LAUNCH.format(id=job_id, pr=body), 40)
        if rc is None:
            return "failed", f"not started: {text}"
        return ("launched", "queued on its own lock") if "launched" in text else ("failed", f"not started: {fleet._short(text, 200)}")
    st = f"/tmp/jobboard/{job_id}"
    script = (f"mkdir -p /tmp/jobboard\ncat > {st}.sh <<'JOBBOARD_EOF'\nst={st}\necho waiting > $st.state\n"
              f"{body.format(st=st)}\necho $rc > $st.rc\necho done > $st.state\nJOBBOARD_EOF\n"
              f"nohup sh {st}.sh > {st}.log 2>&1 < /dev/null &\necho launched\n")
    rc, text = ssh(machine, script, 30)
    if rc is None:
        return "failed", f"not started: {text}"
    return ("launched", "queued on its own lock") if "launched" in text else ("failed", f"not started: {fleet._short(text, 200)}")


def target(state, detail=""):
    return {"state": state, "detail": detail, "since": now()}


# --- send ------------------------------------------------------------------------------------------------------------
def pr_head(pr):
    r = subprocess.run(["gh", "pr", "view", pr, "--repo", "boykopovar/AnyPS5", "--json", "headRefOid,state,title"],
                       capture_output=True, text=True, timeout=60, creationflags=NOWIN)
    if r.returncode:
        return None
    return json.loads(r.stdout)


def ls_remote(url, ref):
    try:
        r = subprocess.run(["git", "ls-remote", url, ref], capture_output=True, text=True, timeout=60, creationflags=NOWIN)
    except (OSError, subprocess.TimeoutExpired):
        return None
    line = r.stdout.split("\n")[0].split()
    return line[0] if line else None


def resolve_branch(branch):
    """(sha, url, ref, folder name) for main, a PR number, or a branch on Blu's fork or upstream."""
    b = branch.strip()
    m = re.fullmatch(r"(?:pr|#)?\s*(\d{3,6})", b, re.I)
    if m:
        info = pr_head(m.group(1))
        if not info:
            return None
        return info["headRefOid"], ORIGIN, f"refs/pull/{m.group(1)}/head", f"pr{m.group(1)}-{info['headRefOid'][:11]}"
    if not SAFE.match(b.replace("/", "-")):
        return None
    for url in ((ORIGIN, FORK) if b == "main" else (FORK, ORIGIN)):
        sha = ls_remote(url, f"refs/heads/{b}")
        if sha:
            return sha, url, f"refs/heads/{b}", f"{b.replace('/', '-')[:40]}-{sha[:11]}"
    return None


def request_quest_build(sha, url, ref, folder):
    line = f"{sha} {url} {ref} {folder}"
    rc, text = ssh("wsl", f"""f={QB}/quest-requests.txt; touch $f {QB}/quest-sent.txt
grep -q '^{sha} ' $f || echo '{line}' >> $f
if grep -q '^{sha[:11]} ' {QB}/quest-sent.txt; then echo already-sent; else echo requested; fi""", 60)
    if rc is None:
        return target("failed", f"WSL: {text}")
    if "already-sent" in text:
        return target("done", f"already built and sent as {folder} earlier")
    try:
        KICK.touch()
    except OSError:
        pass
    return target("waiting", "requested; the WSL builder (qb-driver.ps1) builds it when it gets the PC lock")


def send(kind, arg="", machines=""):
    kind = (kind or "").strip().lower().replace("-", "").replace("_", "")
    stamp = datetime.datetime.now().strftime("%m%d-%H%M%S")
    want = [m.strip().lower() for m in re.split(r"[,\s]+", machines or "") if m.strip() and m.strip().lower() not in ("all", "every")]
    if len(want) >= 3:
        want = []
    job = {"kind": kind, "arg": arg, "created": datetime.datetime.now().isoformat(timespec="seconds"), "targets": {}, "state": "open"}
    if kind == "prtest":
        nums = re.findall(r"\d{3,6}", str(arg))
        if not nums:
            return "NOT SENT: prtest needs a PR number"
        pr = nums[0]
        info = pr_head(pr)
        if not info:
            return f"NOT SENT: GitHub does not know PR {pr} (or gh failed)"
        if info["state"] != "OPEN":
            return f"NOT SENT: PR {pr} is {info['state'].lower()}"
        job.update(id=f"prtest-{pr}-{stamp}", arg=pr, title=info["title"], sha=info["headRefOid"])
        for m in RUNNERS:
            if want and m not in want and not (m == "macbook" and "mac" in want):
                continue
            if not want and m == "macbook":
                job["targets"][m] = target("skipped", "PR tests run on GitHub (forkci.py QEMU CPU lanes); name the macbook to use it")
                continue
            if m == "macbook" and (fleet.LIVE / "mac-paused.flag").exists():
                job["targets"][m] = target("skipped", "the Mac's jobs are paused (live\\mac-paused.flag)")
                continue
            job["targets"][m] = target(*launch(m, job["id"], RUNNERS[m]["body"].replace("{pr}", pr)))
        if not want or {"quest", "azarm", "arm"} & set(want):
            folder = f"pr{pr}-{info['headRefOid'][:11]}"
            job.update(folder=folder, sha11=info["headRefOid"][:11])
            job["targets"]["qbuild"] = request_quest_build(info["headRefOid"], ORIGIN, f"refs/pull/{pr}/head", folder)
            job["targets"]["quest"] = target("waiting", "tests the build when the Quest receives it")
            job["targets"]["azarm"] = target("skipped", AZARM_GONE)
    elif kind == "questbuild":
        r = resolve_branch(arg or "main")
        if not r:
            return f"NOT SENT: cannot find {arg!r} as main, a PR number, or a branch of ycbuzi/AnyPS5 or boykopovar/AnyPS5"
        sha, url, ref, folder = r
        job.update(id=f"questbuild-{folder[:30]}-{stamp}", arg=arg or "main", sha=sha, sha11=sha[:11], folder=folder)
        job["targets"]["qbuild"] = request_quest_build(sha, url, ref, folder)
        job["targets"]["quest"] = target("waiting", "tests the build when the Quest receives it")
        job["targets"]["azarm"] = target("skipped", AZARM_GONE)
    elif kind == "questsend":
        folder = arg.strip() if arg and arg.strip() not in ("newest", "latest", "last") else ""
        if folder and not SAFE.match(folder):
            return "NOT SENT: that is not a build folder name"
        if not folder:
            rc, text = ssh("quest", f"for d in $(ls -1t {QR}/incoming); do ls {QR}/incoming/$d/relinker-v* >/dev/null 2>&1 && ls {QR}/incoming/$d/prx-linux-v*.tar.gz >/dev/null 2>&1 && {{ echo $d; break; }}; done")
            folder = (text or "").strip().splitlines()[-1] if rc is not None and text.strip() else ""
            if not SAFE.match(folder or "-/"):
                return f"NOT SENT: could not find the newest build on the Quest ({text})"
        job.update(id=f"questsend-{folder[:30]}-{stamp}", arg=folder, folder=folder)
        job["targets"]["quest"] = target("waiting", "")
        job["targets"]["azarm"] = target("skipped", AZARM_GONE)
    elif kind == "probes":
        job.update(id=f"probes-{stamp}")
        job["targets"]["azbsd"] = target(*launch("azbsd", job["id"], "lockf -k ~/probes/.run.lock sh -c 'echo running > {st}.state; nice -n 10 sh ~/probes/run.sh'; rc=$?"))
    else:
        return "NOT SENT: jobs are prtest <PR>, questbuild <branch>, questsend [folder], probes"
    if not job["targets"]:
        return "NOT SENT: none of those machines runs this job (prtest: macbook, azlinux, azwin, quest, azarm)"
    save(job)
    write_summary(job)
    lines = [f"SENT {job['id']}:"] + [f"  {m}: {t['state']}, {t['detail']}" for m, t in job["targets"].items()]
    lines.append("Results come together in " + str(BOARD / job["id"] / "summary.md") + " (collected every 10 minutes).")
    return "\n".join(lines)


# --- collect ---------------------------------------------------------------------------------------------------------
def collect_runner(job, m, t):
    pr = job["arg"]
    st = f"/tmp/jobboard/{job['id']}"
    rep = f"{RUNNERS[m]['reports']}/{pr}.md" if m in RUNNERS else ""
    if RUNNERS.get(m, {}).get("windows"):
        rc, text = ssh(m, WIN_COLLECT.format(id=job["id"], rep=rep), 60)
    else:
        rc, text = ssh(m, f"""st={st}
if [ -f $st.rc ]; then echo "rc=$(cat $st.rc)"; f={rep}
  if [ -n "$f" ] && [ -f $f ] && [ $f -nt $st.sh ]; then echo ---REPORT---; cat $f; else echo ---LOG---; tail -n 5 $st.log; fi
elif pgrep -f "jobboard/{job['id']}.sh" >/dev/null; then echo "state=$(cat $st.state 2>/dev/null)"
else echo lost; fi""", 60)
    if rc is None:
        t["detail"] = f"{text} at {now()}"
        return
    if text.startswith("state="):
        t.update(state=text[6:].strip() or "waiting", detail="running its tests" if "running" in text else "queued behind its own lock")
        return
    if text.startswith("lost"):
        t.update(state="failed", detail="the job's wrapper is gone without a result (machine restarted?)", since=now())
        return
    code = text.split("\n", 1)[0][3:].strip()
    if "---REPORT---" in text:
        report = text.split("---REPORT---", 1)[1].lstrip("\n")
        (BOARD / job["id"] / f"{m}.md").write_text(report, encoding="utf-8")
        head = report.splitlines()[1] if len(report.splitlines()) > 1 else ""
        new = next((l for l in report.splitlines() if l.startswith("new failures:")), "new failures: (no line)")
        build = re.search(r"build rc \d+", head)
        runs = re.findall(r"run \d+: ([^;]+)", head)
        mx = next((l for l in report.splitlines() if l.startswith("matrix:")), "")
        t.update(state="done", since=now(), detail=fleet._short(f"{new}; {build.group(0) if build else 'build ?'}; {len(runs)} runs ({runs[-1] if runs else '?'})" + (f"; {mx}" if mx else ""), 360))
    else:
        t.update(state="failed", since=now(), detail=fleet._short(f"exit {code}, no new report: {text.split('---LOG---', 1)[-1]}", 260))


def collect_qbuild(job, t):
    s = job["sha11"]
    rc, text = ssh("wsl", f"""grep '^{s} ' {QB}/quest-sent.txt | tail -n 1
grep -E '(FAIL|SKIP) {s}' {QB}/logs/quest-builds.log | tail -n 1
pgrep -f '[q]b-quest-builds.sh$' >/dev/null && echo BUILDING
cat "/mnt/c/AnyPS5 Games/_tools/live/wsl-qbuild.state" 2>/dev/null | sed 's/^/DRIVER /'""", 60)
    if rc is None:
        t["detail"] = f"WSL: {text} at {now()}"
        return
    lines = text.splitlines()
    if lines and lines[0].startswith(s):
        t.update(state="done", since=now(), detail=f"built and sent to the Quest as {job['folder']} ({lines[0].split()[2].replace('_', ' ')})")
    elif any(re.search(r"(FAIL|SKIP) ", l) for l in lines):
        t.update(state="failed", since=now(), detail=fleet._short(next(l for l in lines if re.search(r"(FAIL|SKIP) ", l)), 220))
    elif "BUILDING" in text:
        t.update(state="running", detail="the WSL builder is building (requests are built in order)")
    else:
        d = next((l[7:] for l in lines if l.startswith("DRIVER ")), "")
        if d:
            t["detail"] = fleet._short(f"WSL builder: {d}", 200)


def collect_quest(job, t):
    f = job["folder"]
    rc, text = ssh("quest", f"""R={QR}
[ -d $R/incoming/{f} ] && echo HAS
[ -e $R/incoming/{f}/.tested ] && echo TESTED
awk -F'\\t' '$3=="pr-{f}" {{print $2 ": rc=" $4 " " $6}}' $R/results.tsv | tail -n 6""", 40)
    if rc is None:
        t["detail"] = f"{text} at {now()}"
        return
    rows = [l for l in text.splitlines() if l not in ("HAS", "TESTED")]
    if "TESTED" in text.splitlines():
        (BOARD / job["id"] / "quest.txt").write_text("\n".join(rows) + "\n", encoding="utf-8")
        t.update(state="done", since=now(), detail=fleet._short(" | ".join(rows) or "tested, no result rows", 400))
    elif "HAS" in text.splitlines():
        t.update(state="running", detail="the Quest has the build; its pipeline tests it" + (f" ({len(rows)} game(s) so far)" if rows else ""))


def to_azarm(job, t):
    """Copy the build back from the Quest when WSL no longer has it, relink in WSL, push it to the lane."""
    f = job["folder"]
    rc, have = ssh("azarm", f"ls ~/alane/incoming/{f}/relinker-v* >/dev/null 2>&1 && echo yes", 30)
    if rc is None:
        t["detail"] = f"azarm: {have} at {now()}"
        return
    if "yes" not in have:
        rc, cb = ssh("wsl", f"ls {QB}/quest-dist/{f}/relinker-v* {QB}/quest-dist/{f}/prx-linux-v*.tar.gz >/dev/null 2>&1 && echo yes", 60)
        if rc is None:
            t["detail"] = f"WSL: {cb} at {now()}"
            return
        if "yes" not in cb:
            rc, files = ssh("quest", f"cd {QR}/incoming/{f} 2>/dev/null && ls relinker-v* prx-linux-v*.tar.gz", 30)
            names = [n for n in (files or "").split() if SAFE.match(n)]
            if rc is None or len(names) < 2:
                t["detail"] = f"waiting: the Quest does not have {f} yet"
                return
            if not pipe(["ssh", "-o", "BatchMode=yes", "quest", f"tar -C {QR}/incoming -cf - " + " ".join(f"{f}/{n}" for n in names)],
                        fleet.WSL + ["sh", "-c", f"mkdir -p {QB}/quest-dist && tar -C {QB}/quest-dist -xf -"]):
                t["detail"] = f"copying {f} from the Quest to WSL failed at {now()}; next collect tries again"
                return
        rc, relink = ssh("wsl", f"bash {QB}/bin/qb-relink.sh {f} 2>&1 | tr '\\n' ' '", 900)
        if not pipe(fleet.WSL + ["sh", "-c", f"mkdir -p {QB}/azarm-relinked/{f} && tar -cf - --exclude=*.log --exclude=*.part --exclude=.tested -C {QB}/quest-dist {f} -C {QB}/azarm-relinked {f}"],
                    ["ssh", "-o", "BatchMode=yes", "azarm", "mkdir -p ~/alane/incoming && tar -C ~/alane/incoming -xf -"]):
            t["detail"] = f"pushing {f} to azarm failed at {now()}; next collect tries again"
            return
        ssh("azarm", f"touch ~/alane/incoming/{f}", 20)
        t.update(state="running", since=now(), detail=fleet._short(f"pushed {f} at {now()} (relink: {relink.strip() or 'nothing new'}); the lane tests it in its next round", 300))
        return
    rc, text = ssh("azarm", f"""[ -e ~/alane/incoming/{f}/.tested ] && echo TESTED
awk -F'\\t' '$3=="{f}" {{print $2 ": rc=" $5 " " $7}}' ~/alane/results.tsv | tail -n 4
systemctl is-active -q alane.service && echo LANE-RUNNING""", 30)
    if rc is None:
        return
    rows = [l for l in text.splitlines() if l not in ("TESTED", "LANE-RUNNING")]
    if "TESTED" in text.splitlines():
        (BOARD / job["id"] / "azarm.txt").write_text("\n".join(rows) + "\n", encoding="utf-8")
        t.update(state="done", since=now(), detail=fleet._short(" | ".join(rows) or "tested, no result rows", 400))
    elif t["state"] != "running":
        t.update(state="running", detail="the lane has the build; it tests it in its next round")


def pipe(a, b, seconds=1800):
    try:
        p1 = subprocess.Popen(a, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, creationflags=NOWIN)
        p2 = subprocess.Popen(b, stdin=p1.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NOWIN)
        p1.stdout.close()
        p2.wait(seconds)
        p1.wait(60)
        return p1.returncode == 0 and p2.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        for p in (locals().get("p1"), locals().get("p2")):
            if p and p.poll() is None:
                p.kill()
        return False


def collect_probes(job, t):
    st = f"/tmp/jobboard/{job['id']}"
    rc, text = ssh("azbsd", f"""st={st}
if [ -f $st.rc ]; then echo "rc=$(cat $st.rc)"; echo "$(grep -c . ~/probes/results.txt) probe lines; changes: $(head -n 3 ~/probes/results.diff | tr '\\n' ' ')"
elif pgrep -f "jobboard/{job['id']}.sh" >/dev/null; then echo "state=$(cat $st.state 2>/dev/null)"; else echo lost; fi""", 30)
    if rc is None:
        return
    if text.startswith("rc="):
        lines = text.splitlines()
        subprocess.run(["scp", "-q", "-o", "BatchMode=yes", "azbsd:probes/results.txt", "azbsd:probes/results.diff", str(BOARD / job["id"])],
                       capture_output=True, timeout=120, creationflags=NOWIN)
        t.update(state="done" if lines[0] == "rc=0" else "failed", since=now(), detail=fleet._short(f"exit {lines[0][3:]}; {lines[1] if len(lines) > 1 else ''}", 260))
    elif text.startswith("state="):
        t.update(state=text[6:].strip() or "waiting", detail="running" if "running" in text else "waiting for a stress round that holds the probe lock")
    else:
        t.update(state="failed", since=now(), detail="the job's wrapper is gone without a result")


OPEN = ("launched", "waiting", "running")


def collect_job(job):
    for m, t in job["targets"].items():
        if t["state"] not in OPEN:
            continue
        try:
            if m in RUNNERS:
                collect_runner(job, m, t)
            elif m == "qbuild":
                collect_qbuild(job, t)
            elif m == "quest":
                if job["targets"].get("qbuild", {}).get("state") in (None, "done"):
                    collect_quest(job, t)
            elif m == "azarm":
                if job["targets"].get("qbuild", {}).get("state") in (None, "done"):
                    to_azarm(job, t)
            elif m == "azbsd":
                collect_probes(job, t)
        except Exception as e:
            t["detail"] = f"collect error at {now()}: {e}"
    if job["targets"].get("qbuild", {}).get("state") == "failed":
        for m in ("quest", "azarm"):
            if job["targets"].get(m, {}).get("state") in OPEN:
                job["targets"][m].update(state="skipped", detail="no build: the WSL Quest build failed")
    age_h = (datetime.datetime.now() - datetime.datetime.fromisoformat(job["created"])).total_seconds() / 3600
    if age_h > DEADLINE_H.get(job["kind"], 8):
        for t in job["targets"].values():
            if t["state"] in OPEN:
                t.update(state="timed out", detail=t["detail"] + f" (no result after {DEADLINE_H.get(job['kind'], 8)} h)")
    if all(t["state"] not in OPEN for t in job["targets"].values()):
        job["state"] = "done"
        job["finished"] = now()
    save(job)
    write_summary(job)


def summary_line(job):
    created = job["created"][5:16].replace("T", " ")
    parts = []
    for m, t in job["targets"].items():
        d = t["detail"] if t["state"] in ("done", "failed") else t["state"]
        parts.append(f"{m}: {fleet._short(d, 90)}")
    return f"{created} {job['id']} [{job['state']}] " + " | ".join(parts)


def write_summary(job):
    title = f" ({job['title']})" if job.get("title") else ""
    lines = [f"# {job['kind']} {job['arg']}{title}", f"job {job['id']}, sent {job['created'].replace('T', ' ')}, {job['state']}"
             + (f" {job['finished']}" if job.get("finished") else ""), ""]
    for m, t in job["targets"].items():
        lines.append(f"- **{LABELS.get(m, m)}** ({m}): {t['state']} since {t['since']}: {t['detail']}")
    reports = sorted(p.name for p in (BOARD / job["id"]).iterdir() if p.name not in ("job.json", "summary.md"))
    if reports:
        lines += ["", "Full reports here: " + ", ".join(reports)]
    (BOARD / job["id"] / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    jobs = all_jobs()
    INDEX.write_text("\n".join(summary_line(j) for j in jobs[-200:]) + "\n", encoding="utf-8")


def collect(only=None):
    BOARD.mkdir(parents=True, exist_ok=True)
    try:
        if LOCK.exists() and time.time() - LOCK.stat().st_mtime > 3600:
            LOCK.unlink()
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return "another collect is running; try again in a few minutes"
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        n = 0
        for job in all_jobs():
            if job["state"] == "open" and (only is None or job["id"] == only):
                collect_job(job)
                n += 1
        return f"collected {n} open job(s)"
    finally:
        LOCK.unlink(missing_ok=True)


# --- read and cancel -------------------------------------------------------------------------------------------------
def list_jobs(n=10):
    jobs = all_jobs()
    if not jobs:
        return "No jobs on the board yet."
    return "\n".join(summary_line(j) for j in jobs[-int(n):])


def result(job_id=""):
    job = find(job_id)
    if not job:
        return f"no job {job_id!r} on the board. list shows them."
    if job["state"] == "open":
        collect(job["id"])
        job = load(job["id"])
    return (BOARD / job["id"] / "summary.md").read_text(encoding="utf-8")


def cancel(job_id):
    job = find(job_id)
    if not job:
        return f"no job {job_id!r} on the board"
    if job["state"] != "open":
        return f"{job['id']} is already {job['state']}"
    out = []
    for m, t in job["targets"].items():
        if t["state"] not in OPEN:
            continue
        if RUNNERS.get(m, {}).get("windows"):
            rc, text = ssh(m, WIN_CANCEL.format(id=job["id"]), 30)
            out.append(f"{m}: {'stopped' if rc is not None else text}")
        elif m in RUNNERS or m == "azbsd":
            rc, text = ssh(m, f"""kt() {{ for c in $(pgrep -P $1); do kt $c; done; kill -TERM $1 2>/dev/null; }}
for p in $(pgrep -f "jobboard/{job['id']}.sh"); do kt $p; done
{RUNNERS.get(m, {}).get('unlock', ':')}
echo stopped""", 30)
            out.append(f"{m}: {'stopped' if rc is not None else text}")
        elif m == "qbuild":
            rc, text = ssh("wsl", f"f={QB}/quest-requests.txt; [ -f $f ] && {{ grep -v '^{job['sha']} ' $f; true; }} > $f.jb && mv $f.jb $f; echo removed", 60)
            out.append(f"qbuild: request {'removed' if rc is not None else text} (a build already started finishes)")
        t.update(state="cancelled", since=now(), detail="cancelled")
    job["state"] = "cancelled"
    job["finished"] = now()
    save(job)
    write_summary(job)
    return f"CANCELLED {job['id']}: " + "; ".join(out or ["nothing was running"])


def wait(job_id, minutes=360):
    """Offline: collect every 10 minutes until the job is done (for a background check, never a Claude loop)."""
    end = time.time() + float(minutes) * 60
    job = find(job_id)
    while job and job["state"] == "open" and time.time() < end:
        collect(job["id"])
        job = load(job["id"])
        if job["state"] != "open":
            break
        time.sleep(600)
    return result(job["id"]) if job else f"no job {job_id!r}"


if __name__ == "__main__":
    if sys.stdout:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    a = sys.argv[1:] or ["list"]
    if a[0] == "collect" and not sys.stdout:
        try:
            msg = collect()
        except Exception as e:
            msg = f"collect failed: {e!r}"
        with (BOARD / "collect.log").open("a", encoding="utf-8") as f:
            f.write(f"{now()} {msg}\n")
        sys.exit(0)
    if a[0] == "send" and len(a) >= 2:
        print(send(a[1], a[2] if len(a) > 2 else "", ",".join(a[3:])))
    elif a[0] == "list":
        print(list_jobs())
    elif a[0] == "result":
        print(result(a[1] if len(a) > 1 else ""))
    elif a[0] == "cancel" and len(a) > 1:
        print(cancel(a[1]))
    elif a[0] == "collect":
        print(collect())
    elif a[0] == "wait" and len(a) > 1:
        print(wait(a[1], a[2] if len(a) > 2 else 360))
    else:
        print(__doc__)
        sys.exit(2)
