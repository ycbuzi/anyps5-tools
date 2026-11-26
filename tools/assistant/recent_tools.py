"""Local-AI tools for what Claude built for Blu 11-07 .. 11-12 (Blu: "everything you do, the local AI model should be
able to do offline"). Each wraps the real script; nothing here calls Claude. Anything that posts in public or changes a
machine is approved by Blu first through assistant.approve (the caller passes it in).

    pc_lock()                        who holds the PC lock (gpu_flag.ps1) and who waits
    machine_say(machine, text)       type a message to the MacBook's or the Quest's always-on Claude
    machine_reports(machine, n)      the newest job/test lines the Mac, Quest or Chromebook wrote
    mac_display(size)                the MacBook's screen mode (800x500 keeps its viewer pane readable)
    app_screenshot()                 screenshot every window of the AnyPS5 Games app (no screen takeover)
    github_replies()                 our GitHub threads where someone else spoke last
    gpu_tests()                      the GPU PR test queue: done, left, running
    pr_reports(limit)                what prpost.py would post (it posts nothing)
    fork_ci(action)                  fork CI runs of PRs on GitHub runners: status / plan / collect
    game_guard(game, fix)            check (or put back) a game's own libraries after PR tests
    pr_swap_set(game, pr_libs, libs) which more libraries must come from a PR build (import/export check)
    windows_pr_test(pr, args)        start the winrun Windows ctest of a PR in the background
    main_baseline(game, seconds)     start a timed run of a game on its own (main) build in the background
"""
import json
import os
import re
import subprocess
from pathlib import Path

TOOLS = Path(r"C:\AnyPS5 Games\_tools")
GAMES = Path(r"D:\AnyPS5 Games")
HELPERS = Path(r"D:\AnyPS5\scratchpad\helpers")
SKILLS = TOOLS / "assistant" / "skills"
QUEST_ROOT = "/data/data/com.termux/files/usr/var/lib/proot-distro/containers/debian/rootfs/root"
HIDDEN = 0x08000000


def _run(cmd, timeout=120, cwd=None, inp=None):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                           cwd=cwd, input=inp, creationflags=HIDDEN)
        return r.returncode, (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    except OSError as e:
        return 1, str(e)


def _ssh(host, command, timeout=60, inp=None):
    return _run(["ssh", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes", host, command], timeout, inp=inp)


def _detached(command):
    code, out = _run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(TOOLS / "detach.ps1"), "-Command", command], 60)
    return out if code == 0 else f"could not start ({code}): {out[-300:]}"


def _ps(script_path, *args, timeout=300):
    return _run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script_path), *args], timeout)


def pc_lock():
    flag = TOOLS / "live" / "gpu-busy.flag"
    waits = TOOLS / "live" / "pc-wait"
    lines = []
    if flag.exists():
        text = flag.read_text(encoding="utf-8", errors="replace").strip()
        pid = text.split(" ", 1)[0]
        code, out = _run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], 20)
        alive = pid in out
        lines.append(f"PC held by: {text.split(' ', 2)[-1] if text.count(' ') >= 2 else text} (pid {pid}, {'running' if alive else 'GONE - the next job takes it over'})")
    else:
        lines.append("PC: free")
    for f in sorted(waits.glob("*.txt")) if waits.exists() else []:
        parts = f.read_text(encoding="utf-8", errors="replace").split(" ", 2)
        lines.append(f"waiting: {parts[2] if len(parts) > 2 else f.stem} (priority {parts[0]})")
    if (TOOLS / "pause.flag").exists():
        lines.append("Everything is PAUSED by Blu himself (pause.flag, usually because he is using the PC). It is not stuck and not a fault: no job takes the PC until he resumes.")
    return "\n".join(lines)


def machine_say(machine, text):
    m = (machine or "").strip().lower()
    text = (text or "").strip().replace("\n", " ")
    if not text:
        return "Nothing to send."
    quoted = "'" + text.replace("'", "'\\''") + "'"
    if m == "quest":
        code, out = _ssh("quest", f"tmux send-keys -t claude -l {quoted}; sleep 1; tmux send-keys -t claude C-m; sleep 4; tmux capture-pane -p -t claude | grep -v '^\\s*$' | tail -4")
    elif m in ("mac", "macbook"):
        code, out = _ssh("macbook", f"screen -S claude -p 0 -X stuff {quoted}; sleep 1; screen -S claude -p 0 -X stuff $'\\r'; sleep 4; screen -S claude -p 0 -X hardcopy /tmp/mc.txt; grep -v '^\\s*$' /tmp/mc.txt | tail -4")
    else:
        return "machine must be quest or macbook (the Chromebook's Claude is reached through the job tool or remote_job)."
    return ("SENT to the " + m + "'s Claude. Its screen now:\n" + out) if code == 0 else f"NOT SENT ({code}): {out[-300:]}"


def machine_reports(machine="", n=8):
    m = (machine or "").strip().lower()
    n = max(1, min(int(n or 8), 40))
    out = []
    if m in ("", "mac", "macbook"):
        code, text = _ssh("macbook", f"grep -E 'JOB|start job|end job|COPIED|queue empty' ~/mac-work/status.md | tail -{n}; tail -3 ~/mac-work/gametest/results.tsv 2>/dev/null; true")
        out.append("== MacBook (~/mac-work/status.md)\n" + (text if code == 0 else f"unreachable: {text[-200:]}"))
    if m in ("", "quest"):
        code, text = _ssh("quest", f"tail -{n} {QUEST_ROOT}/qtest/status.md 2>/dev/null; tail -3 {QUEST_ROOT}/qtest/results.tsv 2>/dev/null | cut -c1-220; true")
        out.append("== Quest (qtest/status.md, results.tsv)\n" + (text if code == 0 else f"unreachable: {text[-200:]}"))
    if m in ("", "chromebook"):
        code, text = _ssh("chromebook", f"tail -{n} ~/cb-work/quest-requests.txt 2>/dev/null; ls -t ~/cb-work/quest-dist 2>/dev/null | head -3; true")
        out.append("== Chromebook (Quest build requests, newest builds)\n" + (text if code == 0 else f"unreachable: {text[-200:]}"))
    return "\n".join(out)


def mac_display(size=""):
    size = (size or "").strip().lower().replace(" ", "")
    arg = ""
    if size:
        mm = re.match(r"^(\d{3,4})x(\d{3,4})$", size)
        if not mm:
            return "size must look like 800x500 or 1280x800 (empty = just read the current mode)."
        arg = f" {mm.group(1)} {mm.group(2)}"
    code, out = _ssh("macbook", "osascript -l JavaScript ~/mac-work/display-mode.js" + arg)
    return out if code == 0 else f"failed ({code}): {out[-300:]}"


def app_screenshot():
    code, out = _ps(HELPERS / "capture-app.ps1", timeout=60)
    files = [l for l in out.splitlines() if l.lower().endswith(".png")]
    return ("Saved:\n" + "\n".join(files)) if files else f"No window captured ({code}): {out[-300:]}"


def github_replies():
    code, out = _run(["python", "-I", str(TOOLS / "assistant" / "needs_reply.py")], 180)
    report = GAMES / "_reports" / "needs-reply.md"
    heads = [l for l in report.read_text(encoding="utf-8", errors="replace").splitlines() if l.startswith("## ")] if report.exists() else []
    return ("Threads waiting for our answer:\n" + "\n".join(heads)) if heads else ("No thread waits for us." if code == 0 else f"check failed: {out[-300:]}")


def gpu_tests():
    review = Path(r"C:\AnyPS5 Games\_reports\pr-review")
    try:
        queue = json.loads((review / "gpu-queue.json").read_text(encoding="utf-8"))
        done = json.loads((review / "gpu-done.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return f"cannot read the GPU queue: {e}"
    left = [pr for pr, head in queue.items() if done.get(pr, {}).get("head") != head]
    recent = sorted(done.items(), key=lambda kv: kv[1].get("at", ""))[-5:]
    lines = [f"GPU PR tests: {len(queue) - len(left)} done, {len(left)} left."]
    lines += [f"  #{pr} {d.get('at', '')}: {d.get('runs', 0)} GPU runs, new failures: {', '.join(d.get('new') or []) or 'none'}" for pr, d in recent]
    lines.append(pc_lock())
    return "\n".join(lines)


def pr_reports(limit=20):
    code, out = _run(["python", "-I", str(SKILLS / "prpost.py"), "plan", "--limit", str(int(limit or 20))], 300)
    return out[-3000:] if out else f"prpost plan gave nothing ({code})"


def fork_ci(action="status"):
    act = (action or "status").strip().lower()
    if act not in ("status", "plan", "collect"):
        return "action must be status, plan or collect (tick starts runs and is left to its scheduled task)."
    code, out = _run(["python", "-I", str(SKILLS / "forkci.py"), act], 300)
    return out[-3000:] if out else f"forkci {act} gave nothing ({code})"



def fork_linux(pr="", targets="", tests="", branch=""):
    if not str(pr).strip().isdigit() or not targets or not tests:
        return "give pr (number), targets (cmake test targets, space separated) and tests (ctest regex)."
    args = ["python", "-I", str(SKILLS / "forklinux.py"), str(pr).strip(), "--targets", targets, "--tests", tests]
    if branch:
        args += ["--branch", branch]
    code, out = _run(args, 3600)
    return out[-3000:] if out else f"forklinux gave nothing ({code})"


def gpu_mixed_samples(gpu=""):
    exe = Path(__file__).resolve().parent / "probes" / "mixprobe.exe"
    env = dict(os.environ, ONLY=gpu or "NVIDIA")
    try:
        r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=60, env=env)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"mixprobe failed: {e}"
    keep = [l for l in r.stdout.splitlines() if "driver" in l or ("truncate" in l and l.endswith("color=0x1"))]
    return "\n".join(keep)[-3000:] or "no output"

def game_guard(game="", fix=False, approve=None):
    args = ["-All"] if not game else ["-GameDir", str(GAMES / game) if not Path(game).is_absolute() else game]
    if not fix:
        args.append("-Check")
    elif approve and not approve(f"put back the game's own libraries ({game or 'every game'})"):
        return "NOT CHANGED: Blu declined."
    code, out = _ps(TOOLS / "game-guard.ps1", *args, timeout=600)
    if out:
        return out[-3000:]
    return ("Nothing to change: every game checked runs on its own build's libraries." if code == 0 else f"game-guard failed ({code})")


def pr_swap_set(game, pr_libs, libs):
    names = [l.strip() for l in re.split(r"[,\s]+", libs or "") if l.strip()]
    game_dir = str(GAMES / game) if not Path(game).is_absolute() else game
    if not (Path(game_dir) / "libs").is_dir():
        return f"no libs folder in {game_dir}: give the game's folder name as it is in {GAMES}"
    if not Path(pr_libs).is_dir():
        return f"no PR library folder {pr_libs}"
    code, out = _run(["python", "-I", str(TOOLS / "assistant" / "pr_swap_set.py"), game_dir, pr_libs, *names], 300)
    return out if code == 0 else f"failed ({code}): {out[-300:]}"


def windows_pr_test(pr, args="", approve=None):
    pr = re.sub(r"\D", "", str(pr or ""))
    if not pr:
        return "pr must be a PR number."
    if approve and not approve(f"build and test PR #{pr} on Windows (winrun, takes the PC for 20-60 min)"):
        return "NOT STARTED: Blu declined."
    log = HELPERS / "reports" / f"winrun-{pr}-console.log"
    started = _detached(f"& '{HELPERS / 'winrun.ps1'}' {pr} {args} *> '{log}'")
    if not started.startswith("started"):
        return started
    return f"STARTED winrun of #{pr} (waits for the PC lock). Report: {HELPERS / 'reports' / f'pr-{pr}.md'}; console: {log}"


UNWRAPPED = TOOLS / "unwrapped"


def unwrap_self(path):
    src = Path((path or "").strip().strip('"'))
    if not src.is_file():
        return f"no file {src}"
    with src.open("rb") as f:
        magic = f.read(4)
    if magic == b"\x7fELF":
        return f"{src.name} is already a plain ELF: readable as it is (objdump, the relinker, REA). Nothing to unwrap."
    UNWRAPPED.mkdir(exist_ok=True)
    out = UNWRAPPED / (src.stem + ".elf")
    code, text = _run(["python", "-I", r"D:\AnyPS5\tools\unwrap_self.py", str(src), str(out)], 300)
    if code != 0:
        return f"NOT unwrapped ({code}): {text[-400:]}"
    code2, head = _run(["objdump", "-f", str(out)], 60)
    return f"Unwrapped {src.name} -> {out}\n{text[-400:]}\n{head[-300:] if code2 == 0 else ''}".strip()


def main_baseline(game, seconds=420, approve=None):
    game_dir = str(GAMES / game) if not Path(game).is_absolute() else game
    if not Path(game_dir).exists():
        return f"no game folder {game_dir}"
    if approve and not approve(f"run {Path(game_dir).name} on its own build for {int(seconds)} s (takes the RTX 4070)"):
        return "NOT STARTED: Blu declined."
    started = _detached(f"& '{HELPERS / 'main-baseline-run.ps1'}' -GameDir '{game_dir}' -Seconds {int(seconds)}")
    if not started.startswith("started"):
        return started
    return f"STARTED a {int(seconds)} s run of {Path(game_dir).name} (waits for the PC lock); its log goes to {game_dir}\\crash-logs\\<time>-main-baseline.log"
