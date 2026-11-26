"""Debugging tools for the assistant, all without installing anything: where a running game's threads are, a timeline of a
long test run, what changed between two logs, and searches across every game's logs. Plus the mods folder."""
import collections
import ctypes
import json
import os
import re
import shutil
import subprocess
import time
from ctypes import wintypes
from pathlib import Path

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)
u32 = ctypes.WinDLL("user32", use_last_error=True)
H = wintypes.HANDLE
for f, res, args in [
    ("CreateToolhelp32Snapshot", H, [wintypes.DWORD, wintypes.DWORD]),
    ("OpenThread", H, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]),
    ("OpenProcess", H, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]),
    ("CloseHandle", wintypes.BOOL, [H]),
    ("SuspendThread", wintypes.DWORD, [H]),
    ("ResumeThread", wintypes.DWORD, [H]),
    ("GetThreadContext", wintypes.BOOL, [H, ctypes.c_void_p]),
    ("GetProcessTimes", wintypes.BOOL, [H] + [ctypes.POINTER(wintypes.FILETIME)] * 4),
]:
    getattr(k32, f).restype = res
    getattr(k32, f).argtypes = args


class THREADENTRY32(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ThreadID", wintypes.DWORD), ("th32OwnerProcessID", wintypes.DWORD),
                ("tpBasePri", wintypes.LONG), ("tpDeltaPri", wintypes.LONG), ("dwFlags", wintypes.DWORD)]


class MODULEINFO(ctypes.Structure):
    _fields_ = [("base", ctypes.c_void_p), ("size", wintypes.DWORD), ("entry", ctypes.c_void_p)]


class PMC(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [(n, ctypes.c_size_t) for n in (
        "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
        "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]


def pid_of(folder):
    """The process running from a game folder (0 if none)."""
    script = "Get-Process | Where-Object { $_.Path -and $_.Path.StartsWith($env:GAME_DIR + '\\', 'OrdinalIgnoreCase') } | Select-Object -First 1 -ExpandProperty Id"
    out = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True, env=dict(os.environ, GAME_DIR=str(folder))).stdout.strip()
    return int(out) if out.isdigit() else 0


def modules_of(pid):
    h = k32.OpenProcess(0x0410, False, pid)
    if not h:
        return []
    try:
        arr = (ctypes.c_void_p * 2048)()
        needed = wintypes.DWORD()
        psapi.EnumProcessModulesEx.argtypes = [H, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.DWORD]
        if not psapi.EnumProcessModulesEx(h, arr, ctypes.sizeof(arr), ctypes.byref(needed), 3):
            return []
        psapi.GetModuleInformation.argtypes = [H, ctypes.c_void_p, ctypes.POINTER(MODULEINFO), wintypes.DWORD]
        psapi.GetModuleBaseNameW.argtypes = [H, ctypes.c_void_p, wintypes.LPWSTR, wintypes.DWORD]
        mods = []
        for m in arr[:needed.value // ctypes.sizeof(ctypes.c_void_p)]:
            info, name = MODULEINFO(), ctypes.create_unicode_buffer(260)
            if psapi.GetModuleInformation(h, m, ctypes.byref(info), ctypes.sizeof(info)) and psapi.GetModuleBaseNameW(h, m, name, 260):
                mods.append((info.base or 0, info.size, name.value))
        return mods
    finally:
        k32.CloseHandle(h)


def threads_of(pid):
    snap = k32.CreateToolhelp32Snapshot(4, 0)
    e = THREADENTRY32(); e.dwSize = ctypes.sizeof(e)
    ids = []
    k32.Thread32First.argtypes = k32.Thread32Next.argtypes = [H, ctypes.POINTER(THREADENTRY32)]
    ok = k32.Thread32First(snap, ctypes.byref(e))
    while ok:
        if e.th32OwnerProcessID == pid:
            ids.append(e.th32ThreadID)
        ok = k32.Thread32Next(snap, ctypes.byref(e))
    k32.CloseHandle(snap)
    return ids


def thread_rip(tid):
    """The thread's instruction address; it is held only for the moment of reading."""
    h = k32.OpenThread(0x004A, False, tid)
    if not h:
        return None
    buf = ctypes.create_string_buffer(1232 + 16)
    addr = (ctypes.addressof(buf) + 15) & ~15
    ctypes.c_uint32.from_address(addr + 0x30).value = 0x100001  # CONTEXT_AMD64 | CONTEXT_CONTROL
    try:
        if k32.SuspendThread(h) == 0xFFFFFFFF:
            return None
        try:
            return ctypes.c_uint64.from_address(addr + 0xF8).value if k32.GetThreadContext(h, addr) else None
        finally:
            k32.ResumeThread(h)
    finally:
        k32.CloseHandle(h)


def where_stuck(folder, samples=8):
    pid = pid_of(folder)
    if not pid:
        return f"{Path(folder).name} is not running: start it first."
    mods = sorted(modules_of(pid))

    def place(rip):
        for base, size, name in mods:
            if base <= rip < base + size:
                return name, rip - base
        return "(not in a module: code the game generated or relinked data)", rip

    seen = collections.defaultdict(collections.Counter)
    for _ in range(max(2, min(int(samples), 40))):
        for tid in threads_of(pid):
            rip = thread_rip(tid)
            if rip:
                seen[tid][place(rip)] += 1
        time.sleep(0.25)
    if not seen:
        return f"could not read the threads of pid {pid} (a process started as administrator can't be read from here)."
    rows, waiting = [], collections.Counter()
    for tid, c in seen.items():
        (mod, off), n = c.most_common(1)[0]
        if mod.lower() in ("ntdll.dll", "win32u.dll", "kernelbase.dll") and n >= sum(c.values()) * 0.8:
            waiting[mod] += 1
            continue
        rows.append((n / sum(c.values()), f"thread {tid}: {mod}+0x{off:x}, {n}/{sum(c.values())} samples, {len(c)} different place(s)"))
    rows.sort(reverse=True)
    text = [f"pid {pid}, {len(seen)} threads, {sum(seen[t].total() for t in seen) // len(seen)} samples each."]
    text += ["Threads running game or library code (the same address in every sample = stuck there or spinning):"] + [r[1] for r in rows[:30]]
    text.append("Threads asleep in Windows: " + (", ".join(f"{n} in {m}" for m, n in waiting.items()) or "none"))
    text.append("A guest module (*.guest.prx) offset can go to decompile_game_code.")
    return "\n".join(text)


SYSTEM_MODULES = {"ntdll.dll", "kernelbase.dll", "kernel32.dll", "win32u.dll", "user32.dll", "ucrtbase.dll", "msvcrt.dll", "combase.dll", "rpcrt4.dll",
                  "gdi32.dll", "gdi32full.dll", "sechost.dll", "advapi32.dll", "ws2_32.dll", "mswsock.dll", "libwinpthread-1.dll", "libgcc_s_seh-1.dll", "libstdc++-6.dll"}


def stack_callers(folder, deepest="4", bytes_per_thread="16384"):
    """Which AnyPS5 or game code each thread came from, read from its stack: for a game whose threads all sit in Windows
    (where_stuck shows only ntdll), the return addresses on each stack that point into game or AnyPS5 modules. Threads with
    the same callers are grouped. A stack can hold old addresses too, so read the first few as likely, not certain.
    Works where gdb cannot attach."""
    pid = pid_of(folder)
    if not pid:
        return f"{Path(folder).name} is not running: start it first."
    mods = sorted(modules_of(pid))
    k32.ReadProcessMemory.argtypes = [H, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    k32.ReadProcessMemory.restype = wintypes.BOOL
    proc = k32.OpenProcess(0x0410, False, pid)
    if not proc:
        return f"could not open pid {pid} to read memory (a process started as administrator can't be read from here)."

    def place(value):
        for base, size, name in mods:
            if base <= value < base + size:
                return name, value - base
        return None

    size = max(4096, min(int(bytes_per_thread), 1 << 20))
    groups = collections.Counter()
    seen_at = collections.defaultdict(set)
    try:
        for tid in threads_of(pid):
            h = k32.OpenThread(0x004A, False, tid)
            if not h:
                continue
            buf = ctypes.create_string_buffer(1232 + 16)
            addr = (ctypes.addressof(buf) + 15) & ~15
            ctypes.c_uint32.from_address(addr + 0x30).value = 0x100001
            stack = ctypes.create_string_buffer(size)
            got = ctypes.c_size_t()
            try:
                if k32.SuspendThread(h) == 0xFFFFFFFF:
                    continue
                try:
                    if not k32.GetThreadContext(h, addr):
                        continue
                    rsp = ctypes.c_uint64.from_address(addr + 0x98).value
                    k32.ReadProcessMemory(proc, ctypes.c_void_p(rsp), stack, size, ctypes.byref(got))
                finally:
                    k32.ResumeThread(h)
            finally:
                k32.CloseHandle(h)
            callers = []
            for i in range(0, got.value - 7, 8):
                hit = place(int.from_bytes(stack.raw[i:i + 8], "little"))
                if hit and hit[0].lower() not in SYSTEM_MODULES and hit not in callers:
                    callers.append(hit)
                    if len(callers) >= int(deepest):
                        break
            key = tuple(callers)
            groups[key] += 1
            seen_at[key].add(tid)
    finally:
        k32.CloseHandle(proc)
    bases = {name: base for base, size, name in mods}
    paths = {}
    for p in Path(folder).rglob("*"):
        if p.suffix.lower() in (".prx", ".exe", ".dll") and p.name in bases:
            paths.setdefault(p.name, p)
    settings = Path(folder).parents[1] / "_tools" / "settings.json" if (Path(folder).parents[1] / "_tools").exists() else Path(folder).parent / "_tools" / "settings.json"
    addr2line = None
    try:
        addr2line = Path(json.loads(settings.read_text(encoding="utf-8-sig"))["mingwBin"]) / "addr2line.exe"
    except (OSError, ValueError, KeyError):
        pass

    symbols = {}

    def from_nm(exe, image_base, off):
        if exe not in symbols:
            rows = []
            try:
                r = subprocess.run([str(addr2line.with_name("nm.exe")), "-C", "--defined-only", str(exe)], capture_output=True, text=True, errors="replace", timeout=60)
                for line in r.stdout.splitlines():
                    parts = line.split(" ", 2)
                    if len(parts) == 3 and parts[1] in ("T", "t") and not parts[2].startswith("."):
                        rows.append((int(parts[0], 16), parts[2]))
            except (OSError, ValueError, subprocess.TimeoutExpired):
                pass
            symbols[exe] = sorted(rows)
        import bisect
        rows = symbols[exe]
        i = bisect.bisect_right(rows, (image_base + off - 1, chr(0x10FFFF))) - 1
        return rows[i][1] if i >= 0 else ""

    def name_of(mod, off):
        exe = paths.get(mod)
        if addr2line and addr2line.exists() and exe:
            try:
                import struct
                data = exe.read_bytes()[:4096]
                pe = struct.unpack_from("<I", data, 0x3C)[0]
                image_base = struct.unpack_from("<Q", data, pe + 24 + 24)[0]
                r = subprocess.run([str(addr2line), "-f", "-C", "-e", str(exe), hex(image_base + off - 1)], capture_output=True, text=True, timeout=30)
                fn = r.stdout.splitlines()[0].strip() if r.stdout else "??"
                if not fn or fn == "??":
                    fn = from_nm(exe, image_base, off)
                if fn and fn != "??":
                    return f"{mod}:{fn[:90]}"
            except (OSError, subprocess.TimeoutExpired, struct.error, IndexError):
                pass
        return f"{mod}+0x{off:x}"

    rows = [f"pid {pid}: {sum(groups.values())} threads, grouped by the game and AnyPS5 code on their stacks (nearest first):"]
    for key, n in groups.most_common(20):
        rows.append(f"{n:3} thread(s): " + (" <- ".join(name_of(m, o) for m, o in key) if key else "only Windows code on the stack"))
    rows.append("Read the first one or two names as where the thread waits; deeper ones may be old values left on the stack.")
    return "\n".join(rows)


def window_title(pid, live_dir):
    f = Path(live_dir) / f"title-{pid}.txt"
    try:
        if f.exists():
            return f.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        pass
    found = []
    PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def each(hwnd, _):
        owner = wintypes.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and u32.IsWindowVisible(hwnd):
            b = ctypes.create_unicode_buffer(256)
            u32.GetWindowTextW(hwnd, b, 256)
            if b.value:
                found.append(b.value)
        return True
    u32.EnumWindows(PROC(each), 0)
    return found[0] if found else ""


ERRORS = re.compile(r"not implemented|unimplemented|^FATAL|unhandled exception|open failed|\babort", re.I)


def errors_in(log, limit=8):
    if not Path(log).exists():
        return []
    seen = []
    with open(log, encoding="utf-8", errors="replace") as f:
        for line in f:
            if ERRORS.search(line) and line.strip()[:160] not in seen:
                seen.append(line.strip()[:160])
                if len(seen) >= limit:
                    break
    return seen


def report_tested(folder, code, seconds, frames, rows):
    """Tells the pipeline what a test run found, so the app's list and the assistant's game_list stay true. It applies this at its next pass."""
    fps = next((m.group(1) for m in (re.search(r"FPS\s+([0-9.]+)", r) for r in reversed(rows)) if m), "")
    said = ""
    if code not in (None, 0):
        try:
            said = next((l for l in diagnose(folder).splitlines() if l.startswith(("AnyPS5 stopped", "KIND"))), "")
        except OSError:
            pass
    how = f"ran {seconds // 60} min, no stop" if code is None else f"closed normally after {seconds} s" if code == 0 else f"stopped after {seconds} s"
    text = ("runs: " if code in (None, 0) else "problem: ") + f"{time.strftime('%m-%d %H:%M')} {how}, {frames} frames" + (f", {fps} FPS at the end" if fps else "") + (f". {said}" if said else "") + (". Test copy" if "_test" in Path(folder).parts else "")
    try:
        with (Path(__file__).resolve().parent.parent / "requests.txt").open("a", encoding="utf-8") as f:
            f.write(f"tested {Path(folder).name} = {text[:300]}\n")
    except OSError:
        pass


MEMORY_STOP_GB = float(os.environ.get("APS5_TEST_MEMORY_STOP_GB", "10"))


def commit_free_gb():
    """Memory Windows can still promise, in GB (RAM plus page file). When it reaches 0, programs start failing, Chrome included."""
    class Status(ctypes.Structure):
        _fields_ = [("length", wintypes.DWORD), ("load", wintypes.DWORD)] + [(n, ctypes.c_ulonglong) for n in ("physTotal", "physAvail", "pageTotal", "pageAvail", "virtTotal", "virtAvail", "extended")]
    status = Status()
    status.length = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return float("inf")
    return status.pageAvail / 2**30


def timeline(folder, minutes, live_dir, say=lambda *_: None, cold=False, calls="", source=""):
    """Runs the game and writes down what it does every 15 s. What it proves is only what the table shows: nobody played it."""
    folder = Path(folder)
    cfg = json.loads(folder.joinpath("game.json").read_text(encoding="utf-8-sig"))
    exe = folder / f"{cfg.get('exeName') or cfg['title']}.exe"
    if not exe.exists():
        return f"{exe.name} does not exist: build the game first."
    minutes = max(1.0, min(float(minutes), 240.0))
    # one line per 10 s without slowing the game; [present] lines; more in a crash report
    env = dict(os.environ, APS5_TRACE_SUMMARY_EVERY="10", APS5_PROFILE_DRAW="1", APS5_LOG_TIMESTAMPS="1", APS5_CRASH_INDIRECT_MEMORY="1")
    saved = folder / "game-env.txt"
    if saved.exists():
        for line in saved.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in line:
                name, value = line.split("=", 1)
                env.setdefault(name.strip(), value.strip())
    if cold:
        env["ANYPS5_NO_SHADER_CACHE"] = "1"
    if calls:
        env["APS5_TRACE_CALLS"] = calls
    p = subprocess.Popen([str(exe)], cwd=str(folder), env=env)
    first = None
    h = k32.OpenProcess(0x1000, False, p.pid)
    rows, start, frames, last_cpu = [], time.time(), 0, 0.0
    memory_stop = 0.0
    try:
        while time.time() - start < minutes * 60 and p.poll() is None:
            for _ in range(15):
                time.sleep(1)
                if commit_free_gb() < MEMORY_STOP_GB:
                    memory_stop = max(commit_free_gb(), 0.01)
                    break
            if memory_stop:
                break
            title = window_title(p.pid, live_dir)
            m = re.search(r"FPS: ([0-9.]+) \((\d+)\)", title)
            if m:
                frames = int(m.group(2))
                first = first or time.time() - start
            times = [wintypes.FILETIME() for _ in range(4)]
            k32.GetProcessTimes(h, *[ctypes.byref(t) for t in times])
            cpu = sum((t.dwHighDateTime << 32 | t.dwLowDateTime) / 1e7 for t in times[2:])
            pmc = PMC(); pmc.cb = ctypes.sizeof(pmc)
            psapi.GetProcessMemoryInfo.argtypes = [H, ctypes.POINTER(PMC), wintypes.DWORD]
            psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb)
            rows.append(f"{int(time.time() - start):>5}s  FPS {m.group(1) if m else '-':>7}  frames {frames:>7}  CPU {(cpu - last_cpu) / 15 * 100:>5.0f}%  memory {pmc.WorkingSetSize // 2**20:>6} MB")
            last_cpu = cpu
            say(f"  {rows[-1]}", "dim")
    finally:
        code = p.poll()
        if code is None:
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True)
        k32.CloseHandle(h)
    out = rows[:] if len(rows) <= 24 else rows[:12] + ["..."] + rows[-12:]
    if memory_stop:
        out.append(f"STOPPED TO PROTECT THE PC: only {memory_stop:.1f} GB of memory was left for all programs, so the game was closed after {int(time.time() - start)} s. Do not rerun this without changing what used the memory; a giant shader compile can use over 60 GB.")
    end = f"It exited by itself after {int(time.time() - start)} s with code 0x{code & 0xFFFFFFFF:08X}." if code is not None else f"It was closed after {int(time.time() - start)} s to protect the PC: only {memory_stop:.1f} GB of memory was left for all programs." if memory_stop else f"It was still running after {minutes:g} minutes and was closed."
    errs = errors_in(folder / "anyps5.log")
    digest = []
    summary = folder / "aps5-summary.jsonl"
    tool = Path(source) / "tools" / "trace_digest.py"
    if summary.exists() and summary.stat().st_mtime >= start and tool.exists():
        r = subprocess.run(["python", "-I", str(tool), str(summary)], capture_output=True, text=True, errors="replace")
        digest = ["Frame times per 10 s:", r.stdout[-2500:]]
    out.append(f"First frame after {int(first)} s." if first else "No frame was drawn.")
    report_tested(folder, code, int(time.time() - start), frames, rows)
    if cold:
        out.append("Run with the shader cache off, so this is the load time of a first run.")
    return "\n".join(out + digest + [end, f"Frames drawn: {frames}."] + (["Errors in its log:"] + errs if errs else ["No errors in its log."]) +
                     ["Nobody played it during this run, so this says nothing about later parts of the game."])


def log_files(folder):
    folder = Path(folder)
    files = sorted(folder.glob("crash-logs/*.log"), key=lambda f: f.stat().st_mtime)
    if (folder / "anyps5.log").exists():
        files.append(folder / "anyps5.log")
    return files


def compare_logs(folder):
    files = log_files(folder)
    if len(files) < 2:
        return f"{Path(folder).name} has {len(files)} log(s); two are needed."
    norm = lambda s: re.sub(r"0x[0-9a-fA-F]+|\b\d+\b", "#", s)
    new, old = errors_in(files[-1], 60), errors_in(files[-2], 60)
    was = {norm(l) for l in old}
    now = {norm(l) for l in new}
    return (f"Newer: {files[-1].name} ({files[-1].stat().st_size // 1024} KB). Older: {files[-2].name} ({files[-2].stat().st_size // 1024} KB).\n"
            "New problems:\n" + ("\n".join(l for l in new if norm(l) not in was) or "(none)") +
            "\nGone since the older log:\n" + ("\n".join(l for l in old if norm(l) not in now) or "(none)"))


def search_logs(games_root, pattern):
    rx, out = re.compile(pattern, re.I), []
    for d in sorted(p for p in Path(games_root).iterdir() if p.is_dir() and not p.name.startswith("_")):
        hits, first = 0, ""
        for f in log_files(d):
            with open(f, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if rx.search(line):
                        hits += 1
                        first = first or line.strip()[:150]
        if hits:
            out.append(f"{d.name}: {hits} line(s); first: {first}")
    return "\n".join(out[:40]) or f"no game's log matches {pattern}"


def most_wanted(games_root):
    wanted = collections.defaultdict(set)
    rx = re.compile(r"\b(sce\w+|_?\w+_nid_\w+|\w+)\s+not implemented|not implemented[:\s]+(\w+)", re.I)
    for d in (p for p in Path(games_root).iterdir() if p.is_dir() and not p.name.startswith("_")):
        for f in log_files(d):
            with open(f, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    m = rx.search(line)
                    if m:
                        name = m.group(1) or m.group(2)
                        if name.lower().startswith("sce") or "_nid_" in name:
                            wanted[name].add(d.name)
    rows = sorted(wanted.items(), key=lambda kv: -len(kv[1]))[:30]
    return "\n".join(f"{n}: {len(g)} game(s): {', '.join(sorted(g)[:4])}" for n, g in rows) or "no game's log names a missing function"


def list_mods(folder):
    mods = Path(folder) / "mods"
    if not mods.exists():
        return f"{Path(folder).name} has no mods folder yet. Replacement files go to mods\\files\\<path inside the game>, patches to mods\\patches\\<name>.toml."
    files = [str(f.relative_to(mods)) for f in mods.rglob("*") if f.is_file()]
    return "\n".join(files[:200]) or "the mods folder is empty"


def add_mod_file(folder, source, target):
    target = Path(target)
    if target.is_absolute() or ".." in target.parts:
        return "target must be a path inside the game, like sce_sys/icon0.png"
    dest = Path(folder) / "mods" / "files" / target
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    return f"added {dest}. It takes effect at the next build_game; then test it."


def add_mod_patch(folder, name, text):
    dest = Path(folder) / "mods" / "patches" / (re.sub(r"[^\w.-]", "_", name).removesuffix(".toml") + ".toml")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    return f"wrote {dest}. It takes effect at the next build_game; then test it. Read mods\\patches of another game for the format."


def disable_mod(folder, path):
    mods = Path(folder) / "mods"
    src = mods / path
    if not src.is_file():
        return f"{src} does not exist; list_mods shows the paths."
    dest = mods / "_disabled" / path
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    return f"moved to {dest}; rebuild to take it out of the game."


def screen_facts(image_path, previous_path=""):
    """What can be measured about a picture without a model: how dark it is, whether it changed, and its exact text (Windows' own OCR).
    These are facts, unlike a vision model's description. Returns (text, is_black)."""
    from PIL import Image, ImageChops, ImageStat
    import tempfile
    picture = Image.open(image_path).convert("RGB")
    gray = picture.convert("L")
    stat = ImageStat.Stat(gray)
    mean, spread = stat.mean[0], stat.stddev[0]
    black = mean < 3 and spread < 4
    out = [f"Measured brightness {mean:.0f} of 255, contrast {spread:.0f}" + (": the picture is black." if black else ".")]
    if previous_path:
        try:
            before = Image.open(previous_path).convert("L").resize(gray.size)
            change = ImageStat.Stat(ImageChops.difference(gray, before)).mean[0]
            out.append(f"Change from the previous picture: {change:.1f} of 255" + (" (no visible change)." if change < 0.5 else "."))
        except OSError:
            pass
    if not black:
        handle, big = tempfile.mkstemp(suffix=".png")
        os.close(handle)
        try:
            scale = max(1, min(3, 2880 // max(1, picture.width)))
            picture.resize((picture.width * scale, picture.height * scale)).save(big)
            r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(Path(__file__).with_name("screen_ocr.ps1")), "-Path", big],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, creationflags=0x08000000)
            text = " | ".join(l.strip() for l in r.stdout.splitlines() if l.strip())
            out.append(f"Text read by OCR (may miss stylized or small text): {text[:600]}" if r.returncode == 0 and text else "OCR found no text." if r.returncode == 0 else "OCR did not run.")
        except (OSError, subprocess.SubprocessError):
            out.append("OCR did not run.")
        finally:
            os.unlink(big)
    return " ".join(out), black


def why_slow(folder):
    """Where a game's frame time goes, from its last run's own numbers. Prints what was measured and labels the limit; it never guesses a cause."""
    folder = Path(folder)
    path = folder / "aps5-summary.jsonl"
    if not path.exists():
        return f"{folder.name} has no aps5-summary.jsonl, so nothing was measured. Run timeline_test first."
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    rows = [r for r in rows if r.get("presents") and r.get("interval_end_s", 0) > r.get("interval_start_s", 0)]
    for i in range(len(rows) - 1, 0, -1):
        if rows[i]["interval_end_s"] < rows[i - 1]["interval_end_s"]:
            rows = rows[i:]
            break
    if not rows:
        return f"{folder.name}: the summary file has no finished 10-second rows. Run it longer."
    fps = lambda r: r["presents"] / (r["interval_end_s"] - r["interval_start_s"])
    last = rows[-1]
    gpu = last.get("gpu_busy_ms_per_present") or 0
    frame = 1000 / fps(last)
    out = ["Answer with these measured numbers and the LIMIT line.", f"{folder.name}, from {path.name} ({len(rows)} rows; the last run only, which may hold more than one scene).",
           f"Last row, at {last['interval_end_s']:.0f} s: {fps(last):.1f} FPS, {frame:.0f} ms per frame, rendering at {last.get('resolution')}. Typical (median) {sorted(map(fps, rows))[len(rows) // 2]:.1f} FPS over the run, slowest row {min(map(fps, rows)):.1f}, fastest {max(map(fps, rows)):.1f}. Report the typical number first; the last row may be a brief dip.",
           f"60 FPS needs under 16.7 ms. Graphics card busy {gpu:.0f} ms per frame; that alone caps it near {1000 / gpu if gpu else 0:.0f} FPS.",
           "Workers busy: " + ", ".join(f"{k} {v:.0f}%" for k, v in (last.get("worker_busy_pct") or {}).items()),
           "Top phases, ms per frame (they overlap and add across threads, do not sum them): " + ", ".join(f"{k} {v:.0f}" for k, v in list((last.get("top_phases_ms_per_frame") or {}).items())[:8])]
    if gpu >= frame * 0.8:
        out.append("LIMIT: the graphics card takes most of the frame. Lowering the render resolution should help most; try the game's own settings first.")
    elif gpu > 16.7:
        out.append("LIMIT: both. The CPU side takes longer than the card, and the card alone is also too slow for 60 FPS, so fixing one will not reach 60.")
    else:
        out.append("LIMIT: the CPU side (AnyPS5 handling the game's graphics commands). The card has room, so lowering resolution may not help much.")
    log = folder / "anyps5.log"
    if log.exists():
        text = log.read_text(encoding="utf-8", errors="replace").splitlines()
        for tag in ("[packets] queue 0x0 ", "[packets] queue 0x20 ", "[draws] ", "[hooksync] "):
            found = next((l for l in reversed(text) if l.startswith(tag)), "")
            if found:
                out.append(found[:520])
        out.append("Reading those: WAIT_REG_MEM on a second queue is waiting, not work, so a 100% busy worker there can mean idle. In [draws], the phases (vertex, readTarget, lookup, record) show the slowest part of a draw.")
    out.append("Next: try the game's own graphics settings; if a setting is changed, check the resolution line above actually changed. Then search_github with the slowest phase's name for open perf PRs, and compare only runs of the same scene with the same setting.")
    return "\n".join(out)


def source_of_message(message):
    """Where AnyPS5's source raises a message: the part after the last colon, which has no addresses in it."""
    needle = message.strip().rsplit(": ", 1)[-1][:90]
    function = re.fullmatch(r"(\w+) not implemented", needle)
    needle = f"{function.group(1)}(" if function else needle
    for root in (r"C:\AnyPS5-work\src", r"D:\AnyPS5\wt-assistant"):
        if len(needle) < 12 or not Path(root, ".git").exists():
            continue
        try:
            r = subprocess.run(["git", "-c", "core.fsmonitor=false", "-C", root, "grep", "-n", "-F", needle, "--", "core", ":!*/tests/*"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, creationflags=0x08000000)
        except (OSError, subprocess.SubprocessError):
            continue
        hits = [" ".join(l.split(":", 2)[:2]).replace(" ", ":", 1) for l in r.stdout.splitlines()]
        if hits:
            return [f"Raised in the source at: {', '.join(hits[:4])} (in {root})"]
    return []


PLAYBOOK_HINTS = (("GetImageResource dword 0 is not a valid runtime value", "GetImageResource dword 0 is not a valid runtime value"), ("Ran out of memory allocating 5565054976", "a game runs out of memory at startup"), ("GEngineLoop.PreInit Failed", "an Unreal game exits at once with GEngineLoop.PreInit Failed"), ("scePlayGo: .*cannot be checked", "scePlayGo says chunks cannot be checked"), ("not statically resolvable|ShaderRecompiler::Recompile", "a shader cannot be translated"), ("timed out waiting for RenderThread", "the game stops itself on Unreal's watchdog"),
                  ("param.json or param.sfo not found", "a download will not build"), ("not implemented|refused", "a game stops with a not implemented or refusal message"), ("unhandled exception", "a crash, not a refusal"))


def playbook_hint(text):
    tail = text[-200000:]
    for pattern, title in PLAYBOOK_HINTS:
        if re.search(pattern, tail):
            return f"Playbook to follow: knowledge \"{title}\"."
    return ""


def diagnose(folder):
    """Reads a game's last log and says what kind of problem it is and what to do next, in the order that usually pays off."""
    folder = Path(folder)
    log = folder / "anyps5.log"
    if not log.exists():
        return f"{folder.name} has no log: it has not been run. Run timeline_test first."
    text = log.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    out = [f"{folder.name}: log of {len(lines)} lines, written {time.strftime('%m-%d %H:%M', time.localtime(log.stat().st_mtime))}."]
    if playbook_hint(text):
        out.append(playbook_hint(text))
    saved = sorted(folder.glob("crash-logs/*.log"), key=lambda f: f.stat().st_mtime)
    if saved:
        out.append(f"Newest saved crash log: {saved[-1].name}. Compare the dates: a crash older than the newest log may already be fixed, so do not report it as current.")
    if not re.search(r"^FATAL", text, re.M):
        out.append("The newest log has no FATAL: that run did not crash, or it was closed first.")
    hits = sum(int(n) for n in re.findall(r"\[shader-disk-cache\] \(\d+ s\): (\d+) hits", text))
    misses = sum(int(n) for n in re.findall(r"\[shader-disk-cache\] \(\d+ s\): \d+ hits, (\d+) misses", text))
    if hits or misses:
        out.append(f"Shader cache: {hits} hits, {misses} misses." + (" Many misses: this run was building shaders, so it was slower than the next one will be." if misses > hits else ""))
    big = sorted((f for f in folder.glob("app0/**/*") if f.is_file()), key=lambda f: f.stat().st_size, reverse=True)[:1]
    if big and big[0].stat().st_size > 2**30:
        out.append(f"Largest file: {big[0].name}, {big[0].stat().st_size / 2**30:.1f} GB on {str(big[0].resolve())[:2]}. On a USB hard drive this alone can take minutes to load.")
    if len(lines) <= 40:
        out.append("The whole log is short, so here it is:\n" + "\n".join(l[:200] for l in lines))
    no_module = re.search(r"Failed to load module: (.+)", text)
    if no_module:
        info = folder / "info.txt"
        told = re.findall(r"Needed module not converted[^\r\n]*", info.read_text(encoding="utf-8", errors="replace")) if info.exists() else []
        return "\n".join(out + [f"KIND: the game cannot start: a module it needs is missing. {no_module.group(1)}"] + [f"The build said: {m}" for m in told[:3]] +
                          ["NEXT: 1) Look for that file under app0 and in the build's output. 2) If the build said to add --module-dir, add it to moduleDirs in game.json and build_game. 3) If the file exists, its dependent DLLs may be missing: read the hint in the log."])
    last_words = re.search(r"what\(\):\s+(.+)", text)
    if last_words:
        out.append(f"AnyPS5 stopped it with this message: {last_words.group(1)[:260]}")
        out.extend(source_of_message(last_words.group(1)))
    watchdog = re.search(r"GameThread timed out waiting for RenderThread after [\d.]+ secs", text)
    if watchdog:
        sizes = [int(n) for n in re.findall(r"creating a pipeline for (\d+) SPIR-V words", text)]
        misses_note = f" The largest pipeline it built has {max(sizes):,} SPIR-V words." if sizes else ""
        return "\n".join(out + [f"KIND: Unreal's watchdog, not memory corruption: {watchdog.group(0)}. The game stopped itself because rendering stalled for 120 s.{misses_note}",
                                 "NEXT: 1) Close other disk work and run it again: what it compiled is saved, so the next run often gets further. Twice, if it gets further each time. 2) If it stops at the same place with cache hits, take game_stacks during the stall and search_github for the sentence. Issue #12089 is about this."])
    playgo = "scePlayGo" in (last_words.group(1) if last_words else "")
    if playgo:
        return "\n".join(out + ["KIND: AnyPS5 needs a setting or a file; the message above says which. NEXT: try what it names, in one run, and compare."])
    shader = re.search(r"(compute|vertex|pixel|geometry|hull|domain)? ?shader 0x[0-9a-f]+: (.+?not implemented|.+?unsupported.*|.+?requires .*)", text)
    missing = re.search(r"what\(\):\s+(\w+) not implemented", text) or re.search(r"\b(sce\w+) not implemented", text)
    fatal = re.search(r"^FATAL.*", text, re.M)
    rip = re.search(r"rip[ =:]+\S+.*", text)
    if shader:
        out += [f"KIND: GPU shader gap. {shader.group(0)[:200]}",
                "NEXT: 1) search_github for that exact sentence: an open PR or issue often exists. 2) try_pr_in_game with the PR. 3) If there is none, find the sentence with search under core/shader and read why it refuses; implement it for every input, not for this one game."]
    elif missing:
        out += [f"KIND: missing function {missing.group(1)}.",
                f"NEXT: 1) find_prs {missing.group(1)}. 2) try_pr_in_game. 3) If there is none: find_function_code, how_game_calls, implement it, build_library, try_library_in_game."]
    elif last_words:
        out += [f"KIND: AnyPS5 refused or failed. FATAL only marks the stop; the message above is the cause.",
                "NEXT: 1) search_github for the words of that message. 2) try_pr_in_game with an open PR that is about it. 3) If there is none, find the sentence with search under core and read why it stops."]
    elif fatal:
        out += [f"KIND: crash. {fatal.group(0)[:200]}", (f"Where: {rip.group(0)[:160]}" if rip else "Where: read the lines after FATAL in the log."),
                "NEXT: 1) game_crash_code. 2) compare_logs, to see what is new. 3) timeline_test with calls=<library>, to record the calls just before it. 4) search_game_logs for the same address or message in other games."]
    elif "FPS" not in text and len(lines) < 400:
        out += ["KIND: no error in the log, and little output. It is still loading, waiting, or hung.",
                "NEXT: while it runs: system_load (is the disk saturated?), where_is_game_stuck, game_stacks. Same address in every sample = stuck; high disk and low CPU = loading from a slow drive."]
    else:
        out += ["KIND: no fatal error found.", "NEXT: timeline_test for FPS and memory over time. Low FPS: look at the frame table it prints. Then have Blu play it: only playing finds what comes later."]
    failed = collections.Counter(re.sub(r".*open failed path=(\S+).*", r"\1", l) for l in lines if "open failed" in l)
    if failed:
        out.append(f"Files the game asked for and did not find: {len(failed)}, for example {', '.join(list(failed)[:3])}. Most are normal checks for optional files: only follow one if the failure comes right before the problem.")
    return "\n".join(out)


def try_pr(folder, number, library, minutes, settings, live_dir, say=lambda *_: None):
    """Builds one library from a PR merged onto the commit the game was built from, runs the game with it for a while, and
    always puts the game's own library back."""
    folder, source, build = Path(folder), Path(r"D:\AnyPS5\wt-pr"), Path(r"D:\AnyPS5\build\pr-rint")
    base = Path(settings["anyps5Build"]).joinpath("COMMIT.txt").read_text(encoding="utf-8-sig").split()[0]
    env = dict(os.environ, PATH=settings["mingwBin"] + os.pathsep + os.environ["PATH"], GIT_OPTIONAL_LOCKS="0")

    def run(args, limit=300):
        say("  " + " ".join(str(a) for a in args)[:120], "dim")
        return subprocess.run([str(a) for a in args], capture_output=True, text=True, errors="replace", timeout=limit, env=env, creationflags=0x08004000)

    n = int(number)
    steps = [["git", "-C", source, "checkout", "-q", "--detach", base],
             ["git", "-C", source, "fetch", "-q", "origin", f"+refs/pull/{n}/head:refs/remotes/pr/{n}"],
             ["git", "-C", source, "-c", "user.name=blu", "-c", "user.email=user@example.com", "merge", "--no-edit", "-q", f"refs/remotes/pr/{n}"]]
    for s in steps:
        r = run(s)
        if r.returncode:
            run(["git", "-C", source, "merge", "--abort"])
            return f"NOT TESTED: {' '.join(s[3:6])} failed: {(r.stdout + r.stderr)[-400:]}"
    r = run([Path(settings["mingwBin"]) / "ninja.exe", "-C", build, "-j", "4", f"core/libs/libs/{library}.prx"], 3600)
    if r.returncode:
        return f"NOT TESTED: building {library}.prx failed:\n{r.stdout[-1500:]}"
    built, dst = build / "core" / "libs" / "libs" / f"{library}.prx", folder / "libs" / f"{library}.prx"
    keep = dst.with_name(dst.name + ".before-pr")
    shutil.copy2(dst, keep)
    try:
        shutil.copy2(built, dst)
        return f"#{n} merged onto {base}, {library}.prx built.\n" + timeline(folder, minutes, live_dir, say, source=str(source))
    finally:
        shutil.copy2(keep, dst)
        keep.unlink()
