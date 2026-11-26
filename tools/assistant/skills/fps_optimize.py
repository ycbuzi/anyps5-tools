"""FPS optimizer for a game that already runs. Plain measurements, no AI calls.
Works only on a test copy, _test\\_fps\\<game>: app0, libs, mods and imports are hard links (never written), the saves and
other state folders are copied once and put back before every run, so both sides start from the same save and the same
in-game settings. Same machine, same scene (the same Cross presses on the same timer), several runs, base and change
taken in turns. A change is kept only when every one of its runs beats every base run, by 3 % and 0.5 FPS at least.
A game that waits for the display's refresh most of each frame is at its limit: two base runs show it, nothing more runs.
Results: _reports\\fps\\<game>\\ (each run's aps5-summary and its trace_digest), _reports\\fps-optimizations.md.
--apply puts a proven setting into the game's own game-env.txt (the old one is kept next to it); nothing else in the
original folder changes. Driver settings, in-game options, fullscreen and a lower output resolution are only reported.
Exit 0: worked, 3: stopped because Blu came back, a game started or the nightly build began, 4: nothing to do."""
import argparse
import ctypes
import hashlib
import json
import os
import re
import shutil
import stat
import statistics
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

GAMES = Path(r"D:\AnyPS5 Games")
STATE = GAMES / "_tools" / "auto-state.json"
REPORTS = GAMES / "_reports"
FPS_DIR = REPORTS / "fps"
REPORT = REPORTS / "fps-optimizations.md"
COPIES = GAMES / "_test" / "_fps"
SRC = Path(r"C:\AnyPS5-work\src")
PAD_TOOL = SRC / "tools" / "pad_input.py"
DIGEST = SRC / "tools" / "trace_digest.py"
APP = GAMES / "AnyPS5 Games.exe"
LINKED = {"app0", "libs", "mods", "imports"}
SKIPPED = {"ai-runs", "crash-logs", "anyps5-screenshots", "shader_cache", "_fps_state", "_fps_aside", "_fps_logs"}
CAPS = (29.97, 59.94, 119.88)
CANDIDATES = [
    ("present-mailbox", {"ANYPS5_PRESENT_MODE": "mailbox"}, "swapchain present mode mailbox instead of fifo"),
    ("thread-priority-high", {"APS5_THREAD_PRIORITY": "high"}, "queue workers and presenter above the title's threads"),
    ("draw-pipeline", {"APS5_DRAW_PIPELINE": "1", "APS5_PIPELINED_DRAWS": "32", "APS5_PIPELINE_DISPATCH": "1"}, "draws finished on a second thread (USAGE.md)"),
    ("mimalloc", {"APS5_MIMALLOC": "1"}, "mimalloc instead of the C runtime heap (needs libs\\mimalloc.dll)"),
]
TRADEOFFS = [("output-720p", {"APS5_OUTPUT_RESOLUTION": "1280x720"}, "tells the title the display is 1280x720: a smaller picture, never applied")]
REPORT_ONLY = [
    "In-game options: a performance or frame-rate mode (Blu found it raises FPS). Both sides here start from the same save, so they use the same option; change it in the game, then run this again.",
    "NVIDIA Control Panel program settings (Claude never changes them): Texture filtering - Quality (High performance caused #11433), Power management mode, Vertical sync = Use the 3D application setting.",
    "Fullscreen (ANYPS5_FULLSCREEN=1) and window mode: not tested here, the app shows games inside its own window.",
]
u32 = ctypes.windll.user32
k32 = ctypes.windll.kernel32


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


class MEMSTAT(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD), ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def last_input():
    i = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
    u32.GetLastInputInfo(ctypes.byref(i))
    return i.dwTime


def idle_minutes():
    return ((k32.GetTickCount() - last_input()) & 0xFFFFFFFF) / 60000


def free_gb():
    m = MEMSTAT()
    m.dwLength = ctypes.sizeof(MEMSTAT)
    k32.GlobalMemoryStatusEx(ctypes.byref(m))
    return min(m.ullAvailPhys, m.ullAvailPageFile) / 2**30


def ps(command):
    return subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True, text=True, errors="replace", creationflags=0x08000000).stdout


def busy(own=0):
    """Why a run must not start or go on: another game running, or the nightly build."""
    out = ps("Get-CimInstance Win32_Process | ForEach-Object { '' + $_.ProcessId + '|' + $_.ExecutablePath + '|' + $_.CommandLine }")
    for line in out.splitlines():
        pid, path, cmd = (line.split("|", 2) + ["", ""])[:3]
        if path.lower().startswith(str(GAMES).lower() + "\\") and path.lower().endswith(".exe") and "\\_tools\\" not in path.lower() \
                and not Path(path).name.lower().startswith("anyps5 ") and pid != str(own):
            return f"a game is running: {Path(path).name}"
        if "python" in path.lower() and re.search(r"health\.py", cmd):
            return "the nightly AnyPS5 build (health.py) is running"
    return ""


def free_gpu():
    """As the pipeline does before a game: the assistant's 14B model on the RTX 4070 SUPER would take GPU time from both sides."""
    ps("Get-CimInstance Win32_Process -Filter \"Name='llama-server.exe'\" | Where-Object { $_.CommandLine -match '--port 8093\\b' -and $_.CommandLine -match 'Vulkan0' } | "
       "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")


def entries():
    state = json.loads(STATE.read_text(encoding="utf-8-sig"))
    for name, e in state.items():
        if isinstance(e, dict) and str(e.get("note", "")).startswith("runs:") and e.get("gameDir") and Path(e["gameDir"]).is_dir():
            yield name, e


def note_fps(note):
    m = re.search(r"FPS: ([0-9.]+)|([0-9.]+) FPS", note)
    return float(m.group(1) or m.group(2)) if m else None


def find_game(text):
    p = Path(text)
    if p.is_dir() and (p / "game.json").exists():
        return p
    rows = list(entries())
    for name, e in rows:
        if text.lower() in (name.lower(), Path(e["gameDir"]).name.lower()):
            return Path(e["gameDir"])
    hits = [Path(e["gameDir"]) for name, e in rows if text.lower() in Path(e["gameDir"]).name.lower()]
    return hits[0] if len(hits) == 1 else None


def exe_of(folder):
    cfg = json.loads((folder / "game.json").read_text(encoding="utf-8-sig"))
    return folder / f"{cfg.get('exeName') or cfg['title']}.exe"


def read_env(path):
    rows = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                rows[k.strip()] = v.strip()
    return rows


def fingerprint(game):
    exe = exe_of(game)
    st = exe.stat()
    env = (game / "game-env.txt").read_bytes() if (game / "game-env.txt").exists() else b""
    return hashlib.sha1(f"{st.st_size}|{int(st.st_mtime)}|".encode() + env).hexdigest()[:12]


class NotRunnable(Exception):
    pass


def link_tree(src, dst):
    for root, _, files in os.walk(src):
        rel = Path(root).relative_to(src)
        (dst / rel).mkdir(parents=True, exist_ok=True)
        for f in files:
            target = dst / rel / f
            if not target.exists():
                os.link(Path(root) / f, target)


def make_copy(game, copy, build_from=None):
    """The test copy. build_from: another built copy whose program and libraries are used instead (the build candidate)."""
    stamp = copy / "_fps_copy.json"
    program = build_from or game
    want = {"fingerprint": fingerprint(game), "program": str(program), "exe_mtime": int(exe_of(program).stat().st_mtime)}
    if stamp.exists() and json.loads(stamp.read_text(encoding="utf-8")) == want:
        return copy
    if copy.exists():
        aside = COPIES / "_aside" / f"{copy.name}-{time.strftime('%m%d-%H%M%S')}"
        aside.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(copy), str(aside))
        print(f"older test copy moved aside to {aside}", flush=True)
    copy.mkdir(parents=True)
    for item in game.iterdir():
        name = item.name
        if item.is_dir():
            if name in SKIPPED and name != "shader_cache":
                continue
            if name in LINKED:
                link_tree((program / name) if build_from and name in ("libs", "imports") and (program / name).is_dir() else item, copy / name)
            elif name == "shader_cache":
                if not build_from:
                    shutil.copytree(item, copy / name)
            else:
                size = sum(f.stat().st_size for f in item.rglob("*") if f.is_file())
                if size > 2 * 2**30:
                    raise NotRunnable(f"not tried: its {name} folder holds {size / 2**30:.1f} GB, too big to put back before every run")
                shutil.copytree(item, copy / "_fps_state" / name)
        elif name in ("game.json", "game-env.txt"):
            shutil.copy2(item, copy / name)
    exe = exe_of(program)
    os.link(exe, copy / exe_of(game).name)
    stamp.write_text(json.dumps(want), encoding="utf-8")
    return copy


def restore_state(copy):
    """Puts the saved state back: files the last run made go to _fps_aside, the saved ones are copied over."""
    saved = copy / "_fps_state"
    if not saved.exists():
        return
    aside = copy / "_fps_aside" / time.strftime("%m%d-%H%M%S")
    for d in saved.iterdir():
        live = copy / d.name
        if live.exists():
            for f in [f for f in live.rglob("*") if f.is_file()]:
                if not (d / f.relative_to(live)).exists():
                    (aside / d.name / f.relative_to(live)).parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(f), str(aside / d.name / f.relative_to(live)))
        if live.exists():
            for f in live.rglob("*"):
                if f.is_file() and not os.access(f, os.W_OK):
                    os.chmod(f, stat.S_IWRITE | stat.S_IREAD)
        shutil.copytree(d, live, dirs_exist_ok=True)


def measure(summary, skip_s):
    rows = []
    for line in summary.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            s = json.loads(line)
        except ValueError:
            continue
        if s.get("interval_end_s") is not None:
            rows.append(s)
    first = next((r for r in rows if r.get("presents")), None)
    late = [r for r in rows if r.get("interval_start_s", 0) >= skip_s and r.get("window", {}).get("ms")]
    shown = next((r["window"]["first_present"] for r in reversed(rows) if (r.get("window") or {}).get("first_present")), None)
    out = {"intervals": len(late), "first_present_s": round(shown / 1000, 1) if shown else None,
           "worst_frame_ms": max((r.get("frame_ms", {}).get("max") or 0 for r in rows), default=0)}
    if first:
        out.update({k: first.get(k) for k in ("commit", "gpu", "driver", "resolution")})
    if not late:
        return out
    presents = sum(r.get("presents") or 0 for r in late)
    ms = sum(r["window"]["ms"] for r in late)
    fps = presents / ms * 1000 if ms else 0
    p50 = statistics.median(r.get("frame_ms", {}).get("p50") or 0 for r in late)
    vbl = statistics.mean(r.get("top_phases_ms_per_frame", {}).get("VideoOut.Flip.vblank_wait", 0) for r in late)
    out.update({"fps": round(fps, 2), "p50_ms": p50, "p99_ms": statistics.median(r.get("frame_ms", {}).get("p99") or 0 for r in late),
                "vblank_share": round(vbl / (1000 / fps), 3) if fps else 0, "resolution": late[-1].get("resolution")})
    return out


def run_once(copy, extra_env, minutes, out_dir, label, hands_off):
    exe = copy / exe_of(copy).name
    restore_state(copy)
    for name in ("aps5-summary.jsonl", "anyps5.log"):
        if (copy / name).exists():
            logs = copy / "_fps_logs"
            logs.mkdir(exist_ok=True)
            shutil.move(str(copy / name), str(logs / f"{time.strftime('%m%d-%H%M%S')}-{name}"))
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("APS5_", "ANYPS5_"))}
    env.update(read_env(copy / "game-env.txt"))
    pad = copy / "pad.txt"
    env.update({"APS5_TRACE_SUMMARY_EVERY": "10", "APS5_PAD_INPUT_FILE": str(pad)})
    env.update(extra_env)
    stop = threading.Event()

    def fingers():
        if hands_off or stop.wait(60):
            return
        while not stop.is_set():
            subprocess.run([sys.executable, "-I", str(PAD_TOOL), str(pad), "cross"], capture_output=True)
            if stop.wait(30):
                return

    began, touched = time.time(), last_input()
    p = subprocess.Popen([str(exe)], cwd=str(copy), env=env, creationflags=0x00000020)
    t = threading.Thread(target=fingers, daemon=True)
    t.start()
    why = ""
    try:
        while time.time() - began < minutes * 60 and p.poll() is None:
            time.sleep(5)
            if last_input() != touched and not ARGS.now:
                why = "Blu used the keyboard or mouse"
            elif free_gb() < 2:
                why = "memory ran low"
            elif int(time.time() - began) % 60 < 5:
                why = busy(p.pid)
            if why:
                break
    finally:
        stop.set()
        code = p.poll()
        if code is None:
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True)
        pad.unlink(missing_ok=True)
    seconds = int(time.time() - began)
    row = {"label": label, "env": extra_env, "seconds": seconds, "exit": None if code is None else code & 0xFFFFFFFF, "stopped": why}
    summary = copy / "aps5-summary.jsonl"
    if summary.exists():
        out_dir.mkdir(parents=True, exist_ok=True)
        keep = out_dir / f"{time.strftime('%m%d-%H%M%S')}-{label}.jsonl"
        shutil.copy2(summary, keep)
        row["summary"] = keep.name
        row.update(measure(summary, max(60.0, minutes * 60 * 0.4)))
        if DIGEST.exists():
            d = subprocess.run([sys.executable, "-I", str(DIGEST), str(keep)], capture_output=True, text=True, errors="replace")
            keep.with_suffix(".digest.txt").write_text(d.stdout or d.stderr, encoding="utf-8")
            row["digest"] = keep.with_suffix(".digest.txt").name
    return row


def config_files(game):
    """Settings files the game ships that name frame rate, vsync or quality: read-only, for the report."""
    found = []
    app0 = game / "app0"
    looked = seen = 0
    for f in app0.rglob("*"):
        seen += 1
        if looked > 400 or len(found) >= 8 or seen > 20000:
            break
        if not f.is_file() or f.suffix.lower() not in (".ini", ".cfg", ".config", ".json", ".xml", ".txt", ".lua", ".toml", ".yaml") or f.stat().st_size > 256 * 1024:
            continue
        looked += 1
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        hits = [l.strip()[:90] for l in text.splitlines() if re.search(r"(?i)vsync|frame.?rate|target.?fps|maxfps|fps.?limit|performance.?mode|framerate", l)]
        if hits:
            found.append(f"{f.relative_to(game)}: " + " / ".join(hits[:3]))
    return found


def load_runs(path, fp):
    if not path.exists():
        return []
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    return [r for r in rows if r.get("fingerprint") == fp]


def counted(runs, label):
    return [r for r in runs if r["label"] == label and not r.get("warm") and not r.get("stopped") and r.get("fps") and r.get("exit") is None]


def cap_of(base):
    fps = statistics.median(r["fps"] for r in base)
    share = statistics.median(r.get("vblank_share", 0) for r in base)
    cap = next((c for c in CAPS if abs(fps - c) / c < 0.03), None)
    return (cap, share) if cap and share >= 0.25 else (None, share)


def failed(runs, label):
    return [r for r in runs if r["label"] == label and not r.get("warm") and not r.get("stopped") and (not r.get("fps") or r.get("exit") is not None)]


def judge(base, cand, need, broke=()):
    if broke:
        return "unstable", f"{len(broke)} run(s) stopped by themselves or drew nothing measurable"
    if len(cand) >= 2 and base and max(r["fps"] for r in cand) <= statistics.median(r["fps"] for r in base):
        return "no gain", f"dropped after {len(cand)} runs: none above the base median ({statistics.median(r['fps'] for r in cand):.2f} vs {statistics.median(r['fps'] for r in base):.2f})"
    if len(cand) < need or len(base) < need:
        return "pending", ""
    b = [r["fps"] for r in base]
    c = [r["fps"] for r in cand]
    gain = statistics.median(c) - statistics.median(b)
    pct = gain / statistics.median(b) * 100
    if {r.get("resolution") for r in cand} != {r.get("resolution") for r in base}:
        return "different picture", f"resolution {sorted({str(r.get('resolution')) for r in cand})}"
    if min(c) > max(b) and pct >= 3 and gain >= 0.5:
        return "faster", f"+{gain:.2f} FPS ({pct:+.1f} %), every run above every base run"
    if max(c) < min(b) and pct <= -3:
        return "slower", f"{gain:.2f} FPS ({pct:+.1f} %)"
    if min(c) > max(b):
        return "no gain", f"{gain:+.2f} FPS ({pct:+.1f} %), every run above the base runs but under the 3 % and 0.5 FPS bar"
    return "no gain", f"{gain:+.2f} FPS ({pct:+.1f} %), inside the base runs' spread {min(b):.2f}-{max(b):.2f}"


def optimize(game, minutes, need, budget, apply, tradeoffs, hands_off):
    fp = fingerprint(game)
    out = FPS_DIR / game.name
    out.mkdir(parents=True, exist_ok=True)
    runs_file = out / "runs.jsonl"
    verdict_file = out / "verdict.json"
    if verdict_file.exists() and json.loads(verdict_file.read_text(encoding="utf-8")).get("fingerprint") == fp:
        print(f"{game.name}: already done for this build and game-env.txt", flush=True)
        return 4
    cands = [c for c in CANDIDATES if c[0] != "mimalloc" or (game / "libs" / "mimalloc.dll").exists()]
    cands += TRADEOFFS if tradeoffs else []
    test = GAMES / "_test" / game.name
    build_copy = None
    try:
        copy = make_copy(game, COPIES / game.name)
        if test.is_dir() and (test / "game.json").exists() and exe_of(test).exists():
            build = json.loads((test / "game.json").read_text(encoding="utf-8-sig")).get("anyps5Build", "")
            build_copy = make_copy(game, COPIES / (game.name + "@test-build"), build_from=test)
            cands.append(("build:test-copy", {}, f"the program and libraries of _test\\{game.name} ({build or 'its own build'})"))
    except (NotRunnable, OSError) as e:
        return finish(game, fp, [], {"base": {}}, need, False, str(e) if isinstance(e, NotRunnable) else f"not tried: the test copy could not be made ({e})")
    copies = {c[0]: (build_copy if c[0] == "build:test-copy" else copy) for c in cands}
    copies["base"] = copy
    envs = {c[0]: c[1] for c in cands}
    envs["base"] = {}
    deadline = time.time() + budget * 60

    def record(row):
        row["fingerprint"] = fp
        row["at"] = time.strftime("%Y-%m-%d %H:%M")
        with runs_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")

    while True:
        runs = load_runs(runs_file, fp)
        base = counted(runs, "base")
        warmed = {r["label"] for r in runs if r.get("warm") and not r.get("stopped")}
        if "base" not in warmed:
            label, warm = "base", True
        elif len(base) < 2:
            label, warm = "base", False
        else:
            cap, share = cap_of(base)
            if cap:
                return finish(game, fp, runs, envs, need, apply, f"at its limit: {cap} FPS, it waits for the display's refresh {share * 100:.0f} % of each frame. No setting can raise that.")
            active = []
            for name in [n for n in envs if n != "base"]:
                mine = counted(runs, name)
                if failed(runs, name) or len(mine) >= need:
                    continue
                if len(mine) >= 2 and max(r["fps"] for r in mine) <= statistics.median(r["fps"] for r in base):
                    continue
                active.append((len(mine), name))
            winners = [n for n, _, _ in CANDIDATES if n in envs and judge(base, counted(runs, n), need, failed(runs, n))[0] == "faster"]
            if not active and len(winners) >= 2 and "combined" not in envs:
                envs["combined"] = {k: v for n in winners for k, v in envs[n].items()}
                copies["combined"] = copy
                continue
            if not active:
                return finish(game, fp, runs, envs, need, apply, "")
            fewest = min(n for n, _ in active)
            if len(base) <= fewest:
                label, warm = "base", False
            else:
                label = next(name for n, name in active if n == fewest)
                warm = label not in warmed and copies[label] is not copy
        if time.time() + minutes * 60 > deadline:
            print(f"{game.name}: time budget used; it goes on next time", flush=True)
            return 0
        why = busy()
        if why or idle_minutes() < 10 and not ARGS.now:
            print(f"{game.name}: stopped before a run: {why or 'Blu is using the PC'}", flush=True)
            return 3
        print(f"{game.name}: {label}{' warm-up' if warm else ''} run, {minutes:g} min", flush=True)
        free_gpu()
        row = run_once(copies[label], envs[label], minutes, out, label, hands_off)
        row["warm"] = warm
        record(row)
        print("  " + json.dumps({k: row.get(k) for k in ("fps", "p50_ms", "vblank_share", "first_present_s", "exit", "stopped")}), flush=True)
        if row.get("stopped"):
            return 3
        if row.get("exit") is not None and row["seconds"] < minutes * 60 - 30 and label == "base":
            return finish(game, fp, load_runs(runs_file, fp), envs, need, apply, f"the test copy stopped by itself after {row['seconds']} s (exit 0x{row['exit']:08X}) on a base run: not a settings problem")
        if not row.get("intervals") and not warm and label == "base":
            return finish(game, fp, load_runs(runs_file, fp), envs, need, apply, f"no measurable frames in {row['seconds']} s on a base run: not a settings problem")


def finish(game, fp, runs, envs, need, apply, note):
    base = counted(runs, "base")
    results = {}
    for name in [n for n in envs if n != "base" and any(r["label"] == n for r in runs)]:
        results[name] = judge(base, counted(runs, name), need, failed(runs, name)) if base else ("pending", "")
    applied = ""
    env_names = {c[0] for c in CANDIDATES} | {"combined"}
    best = sorted(((statistics.median(r["fps"] for r in counted(runs, n)), n) for n, (v, _) in results.items() if v == "faster" and n in env_names), reverse=True)
    if best and apply:
        name = best[0][1]
        path = game / "game-env.txt"
        if path.exists():
            shutil.copy2(path, game / f"game-env.txt.before-fps-{time.strftime('%m%d-%H%M')}")
        sys.path.insert(0, str(Path(__file__).parent.parent))
        import work_skills
        for k, v in envs[name].items():
            said = work_skills.set_game_setting(game, k, v)
        applied = f"{name}: {said}"
    warm = [r for r in runs if r.get("warm") and r["label"] == "base"]
    later = [r["first_present_s"] for r in base if r.get("first_present_s")]
    verdict = {"fingerprint": fingerprint(game), "at": time.strftime("%Y-%m-%d %H:%M"), "note": note,
               "results": {n: list(v) for n, v in results.items()}, "applied": applied, "config_files": config_files(game),
               "first_compile": {"warm_up_first_frame_s": warm[-1].get("first_present_s") if warm else None, "warm_up_worst_frame_ms": warm[-1].get("worst_frame_ms") if warm else None,
                                 "later_first_frame_s": statistics.median(later) if later else None}}
    (FPS_DIR / game.name / "verdict.json").write_text(json.dumps(verdict, indent=1), encoding="utf-8")
    write_report()
    print(f"{game.name}: done. {note or ''} {'; '.join(f'{n}: {v}' for n, (v, _) in results.items())}{' APPLIED ' + applied if applied else ''}", flush=True)
    return 0


def fmt(rows):
    if not rows:
        return "-"
    f = [r["fps"] for r in rows]
    return f"{statistics.median(f):.2f} ({len(f)} runs, {min(f):.2f}-{max(f):.2f})"


def write_report():
    lines = ["# FPS optimizations", "", f"Written by `_tools\\assistant\\skills\\fps_optimize.py`, {time.strftime('%Y-%m-%d %H:%M')}. Test copies only (`_test\\_fps`).",
             "Same PC, same scene (same save restored before every run, Cross every 30 s from 60 s), base and change in turns; FPS = presents per second",
             "over the last 60 % of each run, from `aps5-summary.jsonl`. A change counts only when every one of its runs beats every base run by 3 % and 0.5 FPS.",
             "Each run's summary and its `trace_digest.py` digest are in `_reports\\fps\\<game>\\`.", ""]
    for d in sorted(FPS_DIR.iterdir()) if FPS_DIR.exists() else []:
        vf = d / "verdict.json"
        runs_all = [json.loads(l) for l in (d / "runs.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()] if (d / "runs.jsonl").exists() else []
        v = json.loads(vf.read_text(encoding="utf-8")) if vf.exists() else {}
        if not runs_all:
            if v.get("note"):
                lines += [f"## {d.name}", "", f"**{v['note']}** ({v['at']})", ""]
            continue
        fp = runs_all[-1]["fingerprint"]
        runs = [r for r in runs_all if r["fingerprint"] == fp]
        base = counted(runs, "base")
        first = next((r for r in runs if r.get("gpu")), {})
        lines += [f"## {d.name}", "", f"{'Finished ' + v['at'] if v else 'In progress'}. AnyPS5 {first.get('commit', '?')}, {first.get('gpu', '?')}, {first.get('driver', '?')}, {first.get('resolution', '?')}.", ""]
        if v.get("note"):
            lines += [f"**{v['note']}**", ""]
        lines += ["| Setting | FPS median (runs, range) | Frame p50 ms | Verdict |", "|---|---|---|---|",
                  f"| base (game-env.txt as it was) | {fmt(base)} | {statistics.median(r['p50_ms'] for r in base) if base else '-'} | - |"]
        for name in dict.fromkeys(r["label"] for r in runs if r["label"] != "base"):
            mine = counted(runs, name)
            verdict = v.get("results", {}).get(name) or list(judge(base, mine, ARGS.runs))
            lines.append(f"| {name} `{' '.join(f'{k}={x}' for k, x in (mine[0]['env'] if mine else {}).items())}` | {fmt(mine)} | {statistics.median(r['p50_ms'] for r in mine) if mine else '-'} | {verdict[0]}: {verdict[1]} |")
        lines.append("")
        if v.get("applied"):
            lines += [f"Applied to the game's own game-env.txt: {v['applied']}", ""]
        fc = v.get("first_compile", {})
        if fc.get("warm_up_first_frame_s") is not None:
            lines += [f"First-compile cost: first frame after {fc['warm_up_first_frame_s']} s on the warm-up run (worst frame {fc['warm_up_worst_frame_ms']} ms), after {fc['later_first_frame_s']} s once the shader cache was filled.", ""]
        if v.get("config_files"):
            lines += ["Settings files in the game that name frame rate or vsync (read only, not changed):", ""] + [f"- `{c}`" for c in v["config_files"]] + [""]
        if base and DIGEST.exists():
            pair = [base[-1]] + [r for r in runs if r["label"] != "base" and r.get("digest") and not r.get("warm")][-1:]
            for r in pair:
                if r.get("digest") and (d / r["digest"]).exists():
                    text = (d / r["digest"]).read_text(encoding="utf-8", errors="replace").splitlines()
                    lines += [f"trace_digest, {r['label']} run {r['at']}:", "```"] + text[:6] + ["```", ""]
    slow = sorted(Path(e["gameDir"]).name for _, e in entries() if note_fps(e["note"]) is not None and note_fps(e["note"]) < 5)
    if slow:
        lines += ["## Not tried: under 5 FPS in the pipeline's test", "", "A shader-compile or stall problem, not one a setting fixes: " + ", ".join(slow) + ".", ""]
    lines += ["## Checked by hand only", ""] + [f"- {t}" for t in REPORT_ONLY] + [""]
    REPORT.write_text("\n".join(lines), encoding="utf-8")


def pick_next():
    rows = []
    for name, e in entries():
        g = Path(e["gameDir"])
        try:
            if not exe_of(g).exists():
                continue
            fp = fingerprint(g)
        except (OSError, ValueError, KeyError):
            continue
        vf = FPS_DIR / g.name / "verdict.json"
        if vf.exists() and json.loads(vf.read_text(encoding="utf-8")).get("fingerprint") == fp:
            continue
        fps = note_fps(e["note"])
        if fps is not None and fps < 5:
            continue
        capped = fps is not None and any(abs(fps - c) / c < 0.03 for c in CAPS)
        started = (FPS_DIR / g.name / "runs.jsonl").exists()
        rows.append((not started, capped, fps if fps is not None else 99, g))
    rows.sort(key=lambda r: r[:3])
    return rows[0][3] if rows else None


ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument("game", nargs="?", help="game name or folder; --next picks one")
ap.add_argument("--next", action="store_true", help="the next game marked runs: that has no result for its current build")
ap.add_argument("--minutes", type=float, default=4, help="per run; the last 60 %% is measured")
ap.add_argument("--runs", type=int, default=3, help="counted runs per side")
ap.add_argument("--budget-minutes", type=float, default=75)
ap.add_argument("--apply", action="store_true", help="put a proven setting into the game's game-env.txt")
ap.add_argument("--tradeoffs", action="store_true", help="also measure a lower output resolution (never applied)")
ap.add_argument("--hands-off", action="store_true", help="press nothing")
ap.add_argument("--now", action="store_true", help="do not wait for 10 idle minutes (Blu asked for it)")
ap.add_argument("--from-pipeline", action="store_true")
ap.add_argument("--plan", action="store_true", help="print the game and the candidates, run nothing")
ap.add_argument("--report", action="store_true", help="only rewrite fps-optimizations.md")
ARGS = ap.parse_args() if __name__ == "__main__" else ap.parse_args([])

if __name__ == "__main__":
    if ARGS.report:
        write_report()
        print(f"written {REPORT}")
        sys.exit(0)
    G = pick_next() if ARGS.next else find_game(ARGS.game or "")
    if not G:
        print("nothing to do" if ARGS.next else f"NOT RUN: no built game matches {ARGS.game!r}")
        sys.exit(4)
    if ARGS.plan:
        print(f"{G.name}: copy {COPIES / G.name}; candidates: " + ", ".join(c[0] for c in CANDIDATES if c[0] != "mimalloc" or (G / "libs" / "mimalloc.dll").exists())
              + (", build:test-copy" if (GAMES / "_test" / G.name / "game.json").exists() else "") + f"; {ARGS.minutes:g} min a run, {ARGS.runs} counted runs a side")
        sys.exit(0)
    why = busy()
    if why or (idle_minutes() < 10 and not ARGS.now):
        print(f"NOT RUN: {why or 'Blu used the PC in the last 10 minutes'}")
        sys.exit(3)
    import msvcrt
    FPS_DIR.mkdir(parents=True, exist_ok=True)
    LOCK = open(FPS_DIR / ".running", "a+")
    try:
        msvcrt.locking(LOCK.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        print("NOT RUN: another fps_optimize is running")
        sys.exit(3)
    if not ARGS.from_pipeline:
        subprocess.run([str(APP), "--pause"])
    try:
        code = optimize(G, ARGS.minutes, ARGS.runs, ARGS.budget_minutes, ARGS.apply, ARGS.tradeoffs, ARGS.hands_off)
    finally:
        if not ARGS.from_pipeline:
            subprocess.run([str(APP), "--resume"])
    sys.exit(code)
