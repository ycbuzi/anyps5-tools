"""Jobs Claude did by hand for Blu, as tools: what is running, disk space and reservations, package unpacking, speed and window tests,
language checks, frame checks, building AnyPS5, Vulkan validation, rebuilding the app. Every number comes from the machine, never from memory."""
import ast
import contextlib
import datetime
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
GAMES = Path(r"D:\AnyPS5 Games")  # the game folders stay on D: (11-03 _tools moved to C: behind a link)
TOOLS_DIR = GAMES / "_tools"
SKILLS = HERE / "skills"
LEARNED = HERE / "learned"
REPORTS = GAMES / "_reports"
SPEED = REPORTS / "speed-test"
RESERVATIONS = TOOLS_DIR / "space-reservations.txt"
FORGE = TOOLS_DIR / "ps5-dump-forge" / "ps5-dump-forge.exe"
HELPERS = Path(r"D:\AnyPS5\scratchpad\helpers")
WORK = Path(r"C:\AnyPS5-work")
HIDDEN = 0x08000000
SITE_NAMES = re.compile(r"(https?://)?(www\.)?\[?\b(example-site(\.com)?|example-site)\b\]?(/\S*)?[\s_-]*", re.I)
PACKAGE_TYPES = (".pkg", ".ffpkg", ".ffpfs", ".ffpfsc")


def scrub_text(text):
    """The text without download site names, the same way the app's screens clean it."""
    return SITE_NAMES.sub("", str(text))


def _ps(command, seconds=60):
    r = subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True, text=True, errors="replace", timeout=seconds, creationflags=HIDDEN)
    return r.stdout


def _size(path):
    total = 0
    count = 0
    for base, _, names in os.walk(path):
        for n in names:
            try:
                total += os.path.getsize(os.path.join(base, n))
                count += 1
            except OSError:
                pass
    return total, count


def _process_list():
    raw = _ps("Get-CimInstance Win32_Process | Select-Object Name,ProcessId,CommandLine,@{n='Started';e={$_.CreationDate.ToString('s')}},ExecutablePath | ConvertTo-Json -Compress", 90)
    try:
        rows = json.loads(raw)
    except ValueError:
        return []
    return rows if isinstance(rows, list) else [rows]


def _work_of(row):
    name = (row.get("Name") or "").lower()
    cmd = row.get("CommandLine") or ""
    path = row.get("ExecutablePath") or ""
    if name == "ps5-dump-forge.exe":
        return "unpacking a game package"
    if name in ("unrar.exe", "winrar.exe", "7z.exe", "7zg.exe") or "exfat_extract" in cmd:
        return "extracting a game"
    if name == "robocopy.exe":
        return "copying a game's files"
    if "build-game.ps1" in cmd:
        return "building a game"
    if "auto-games.ps1" in cmd:
        return "pipeline"
    if name == "llama-server.exe":
        return "AI model"
    if name.endswith(".exe") and path.lower().startswith(str(GAMES).lower() + "\\") and "\\_tools\\" not in path and name != "anyps5 games.exe":
        return "game running"
    return ""


def _named_path(cmd):
    quoted = re.findall(r'"([A-Za-z]:\\[^"]+)"', cmd)
    usable = [q for q in quoted if not q.lower().endswith((".exe", ".ps1", ".py"))]
    return usable[-1] if usable else ""


def disk_load():
    raw = _ps("Get-Counter '\\PhysicalDisk(*)\\Avg. Disk Queue Length','\\PhysicalDisk(*)\\Disk Bytes/sec' | ForEach-Object { $_.CounterSamples } | ForEach-Object { $_.InstanceName + '|' + ($_.Path -split '\\\\')[-1] + '|' + $_.CookedValue }", 30)
    disks = {}
    for line in raw.splitlines():
        parts = line.strip().split("|")
        if len(parts) != 3 or parts[0] == "_total" or " " not in parts[0]:
            continue
        d = disks.setdefault(parts[0], {})
        try:
            d["queue" if "queue" in parts[1] else "speed"] = float(parts[2])
        except ValueError:
            pass
    return [f"{k.split()[-1].upper()} {v.get('speed', 0) / 1e6:.0f} MB/s, {v.get('queue', 0):.1f} waiting" for k, v in sorted(disks.items())]


def whats_running():
    """Real processes: what is working now, what closing the app would cut off, and how busy each drive is."""
    rows = _process_list()
    found = {}
    for row in rows:
        work = _work_of(row)
        if work:
            found.setdefault(work, []).append(row)
    cuts = [w for w in ("unpacking a game package", "extracting a game", "copying a game's files", "building a game") if w in found]
    out = []
    for work, items in found.items():
        for row in items[:3]:
            target = _named_path(row.get("CommandLine") or "")
            out.append(f"{work}: {scrub_text(target) or row.get('Name')} (since {str(row.get('Started'))[11:16]})")
    if not out:
        out.append("Nothing from the pipeline, a build or a game is running.")
    out.append("Drives: " + "; ".join(disk_load()))
    if cuts:
        out.append("Closing the app is safe only with Keep working in the background: the work carries on. Shut everything down would cut off: " + ", ".join(cuts) + ", and that step starts over later.")
    else:
        out.append("Closing the app: no extraction, unpack, copy or build is running, so nothing would be cut off.")
    out.append("A second copy of a build or extract on the same drive makes both slower: USB drive speed is the limit, not who starts it.")
    return "\n".join(out)


def build_progress(folder):
    """How far a game's build is: copied GB against its source, from the files themselves."""
    folder = Path(folder)
    cfg = folder / "game.json"
    if not cfg.exists():
        return f"{folder.name} has no game.json, so it has not started building."
    conf = json.loads(cfg.read_text(encoding="utf-8-sig"))
    exes = [p.name for p in folder.glob("*.exe")]
    running = [r for r in _process_list() if "build-game.ps1" in (r.get("CommandLine") or "") and folder.name in (r.get("CommandLine") or "")]
    stage = folder / ".build" / "app0"
    if not running:
        state = f"built: {exes[0]}" if exes else "not building now and has no program yet"
        return f"{folder.name}: {state}. Being built does not prove it runs; check game_history."
    source = conf.get("resources") or conf.get("dump") or ""
    want, wanted_files = _size(source) if source and os.path.isdir(source) else (0, 0)
    have, files = _size(stage) if stage.exists() else (0, 0)
    copying = [r for r in _process_list() if (r.get("Name") or "").lower() == "robocopy.exe" and folder.name in (r.get("CommandLine") or "")]
    out = [f"{folder.name}: building since {str(running[0].get('Started'))[11:16]}. The pipeline and a person run the same build script, so neither is faster; starting a second build would only slow this one."]
    if copying:
        out.append(f"Copying files: {have / 1e9:.1f} of {want / 1e9:.1f} GB, {files} of {wanted_files} files.")
        started = copying[0].get("Started")
        try:
            spent = time.time() - time.mktime(time.strptime(str(started)[:19], "%Y-%m-%dT%H:%M:%S"))
            if have and spent > 30 and want > have:
                out.append(f"At {have / spent / 1e6:.0f} MB/s so far, about {(want - have) / (have / spent) / 60:.0f} more minutes for the copy. That is only an estimate.")
        except ValueError:
            pass
    else:
        out.append(f"Not copying now; {have / 1e9:.1f} GB are in place, so it is linking or finishing.")
    return "\n".join(out)


def _built(name):
    try:
        state = json.loads((TOOLS_DIR / "auto-state.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return False
    entry = state.get(name) if isinstance(state, dict) else None
    return isinstance(entry, dict) and entry.get("status") in ("built", "done")


def _reservations():
    rows = []
    if RESERVATIONS.exists():
        for line in RESERVATIONS.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"^\s*([A-Za-z])\s+(\d+)\s+(.+?)\s*$", line)
            if m:
                rows.append((m.group(1).upper(), int(m.group(2)), m.group(3)))
    return rows


def _still_held(drive, gb, name):
    """A reservation counts down as that game's own download arrives."""
    have, _ = _size(f"{drive}:\\ps5 games dumps from ps5\\{name}")
    return max(0, gb - have / 2**30)


def disk_space():
    """Free space on each drive, what is held back for big downloads, and what the pipeline may really use."""
    rows = _reservations()
    out = []
    for drive in "CDG":
        root = f"{drive}:\\"
        if not os.path.exists(root):
            continue
        u = shutil.disk_usage(root)
        held = sum(_still_held(d, gb, name) for d, gb, name in rows if d == drive and not _built(name))
        if not held:
            out.append(f"{drive}: {u.free / 2**30:.0f} GB free of {u.total / 2**30:.0f} GB, all usable by the pipeline")
            continue
        left = u.free / 2**30 - held
        usable = f"{left:.0f} GB usable by the pipeline" if left > 0 else f"0 GB usable by the pipeline: it is {-left:.0f} GB short of the reservation, so it starts no new work on {drive}:"
        out.append(f"{drive}: {u.free / 2**30:.0f} GB free of {u.total / 2**30:.0f} GB, {held:.0f} GB still reserved, {usable}")
    for d, gb, name in rows:
        out.append(f"Reserved on {d}: {gb} GB for {name}" + (" (ignored now: it is built)" if _built(name) else f", {_still_held(d, gb, name):.0f} GB of it still to come"))
    out.append("Games stay on D: or G:, builds on C:. Never delete archives to make room unless Blu says so; they go to the Recycle Bin or _finished archives.")
    return "\n".join(out)


def reserve_space(drive, gb, game):
    drive = str(drive).strip().rstrip(":\\").upper()
    if len(drive) != 1 or not drive.isalpha():
        return "drive must be one letter, like D"
    try:
        gb = int(float(gb))
    except ValueError:
        return "gb must be a number"
    rows = [r for r in _reservations() if r[2].lower() != game.strip().lower()]
    rows.append((drive, gb, game.strip()))
    RESERVATIONS.write_text("".join(f"{d} {g} {n}\n" for d, g, n in rows), encoding="utf-8")
    return f"Reserved {gb} GB on {drive}: for {game}. The pipeline leaves it free until that game is built.\n" + disk_space()


def release_space(game):
    rows = _reservations()
    keep = [r for r in rows if r[2].lower() != game.strip().lower()]
    if len(keep) == len(rows):
        return f"nothing is reserved for {game}"
    RESERVATIONS.write_text("".join(f"{d} {g} {n}\n" for d, g, n in keep), encoding="utf-8")
    return f"Released the space held for {game}.\n" + disk_space()


def package_plan(path):
    """Checks a package is real and fits. Returns (command, output folder, problem)."""
    p = Path(path)
    if not p.is_file():
        return "", "", f"{path} is not a file"
    if p.suffix.lower() not in PACKAGE_TYPES:
        return "", "", f"{p.name} is not a package type ({', '.join(PACKAGE_TYPES)}); use extract_archive for it"
    if p.suffix.lower() == ".pkg":
        with p.open("rb") as f:
            head = f.read(4)
        if head[1:4] not in (b"FIH", b"CNT"):
            return "", "", f"{p.name} starts with {head.hex()}, not a PS5 FIH or CNT package, so ps5-dump-forge will not read it"
    if not FORGE.exists():
        return "", "", f"ps5-dump-forge.exe is missing from {FORGE.parent}"
    out = p.parent / "unpacked"
    if (out / "eboot.bin").exists():
        return "", str(out), f"already unpacked: {out} has eboot.bin. Use check_game_files, then the pipeline's rebuild."
    leftovers = [d.name for d in p.parent.glob("unpacked*.part") if d.is_dir()]
    if leftovers:
        return "", str(out), f"an earlier unpack was cut off: {leftovers[0]} is partial. Ask Blu to OK moving it to the Recycle Bin, or let the pipeline do it, then unpack again."
    need = p.stat().st_size * 2
    free = shutil.disk_usage(p.anchor).free
    if free < need:
        return "", str(out), f"not enough room: needs about {need / 2**30:.0f} GB, {free / 2**30:.0f} GB free on {p.anchor}"
    return f'& "{FORGE}" convert "{p}" --to folder --threads 6 -o "{out}"; "exit $LASTEXITCODE"; Test-Path -LiteralPath "{out}\\eboot.bin"; Test-Path -LiteralPath "{out}\\sce_sys\\param.json"', str(out), ""


def game_languages(folder):
    """Read-only: the languages a game declares and the language files it has. Changes nothing."""
    folder = Path(folder)
    app = folder / "app0" if (folder / "app0").exists() else folder
    out = [f"{folder.name}:"]
    param = app / "sce_sys" / "param.json"
    if param.exists():
        try:
            data = json.loads(param.read_text(encoding="utf-8-sig", errors="replace"))
            local = data.get("localizedParameters") or {}
            out.append("Declared in param.json: default " + str(local.get("defaultLanguage")) + "; " + ", ".join(k for k in local if k != "defaultLanguage")[:400])
        except ValueError:
            out.append("param.json could not be read as JSON.")
    else:
        out.append("No param.json found.")
    locale = re.compile(r"^(en|en-[a-z]+|ja|ja-jp|zh-hans|zh-hant|ko|fr|de|es|it|ru|pt-br)$", re.I)
    dirs = {}
    res = []
    paks = []
    for base, names, files in os.walk(app):
        rel = os.path.relpath(base, app)
        if rel.startswith("sce_sys"):
            continue
        for d in names:
            if locale.match(d):
                dirs[d.lower()] = dirs.get(d.lower(), 0) + 1
        for n in files:
            low = n.lower()
            if low.endswith((".locres", ".locmeta")):
                res.append(os.path.join(rel, n))
            elif low.endswith((".pak", ".utoc")):
                paks.append(os.path.join(base, n))
    out.append("Language folders: " + (", ".join(f"{k} ({v})" for k, v in sorted(dirs.items())) or "none outside packed files"))
    out.append(f"Loose text files (.locres): {len(res)}" + (": " + ", ".join(res[:6]) if res else ""))
    out.append(f"Packed files (.pak, .utoc): {len(paks)}. Their contents were not read, so a language inside them is unknown.")
    out.append("Do not edit a game's files or claim a language exists unless these lines show it. Ask Blu before changing anything; an English option needs the English text to be in the dump.")
    return "\n".join(out)


def frames_report(folder):
    """Brightness of the newest saved test frames, plus a contact sheet Blu can open. Says which look black."""
    from PIL import Image, ImageStat
    folder = Path(folder)
    dirs = sorted((folder / "crash-logs").glob("*-frames")) if (folder / "crash-logs").exists() else []
    if not dirs:
        return f"{folder.name} has no saved frames. A test_run saves them."
    files = sorted(dirs[-1].glob("*.bmp"))
    if not files:
        return f"{dirs[-1].name} is empty."
    rows = []
    images = []
    for path in files:
        with Image.open(path) as raw:
            im = raw.convert("RGB")
        s = ImageStat.Stat(im.convert("L"))
        kind = "black" if s.mean[0] < 8 and s.stddev[0] < 6 else "flat" if s.stddev[0] < 6 else "has a picture"
        rows.append(f"{path.name}: brightness {s.mean[0]:.0f}, contrast {s.stddev[0]:.0f}, {kind}")
        images.append(im)
    w, h = 400, 225
    sheet = Image.new("RGB", (w * 4, h * ((len(images) + 3) // 4)))
    for i, im in enumerate(images):
        sheet.paste(im.resize((w, h)), ((i % 4) * w, (i // 4) * h))
    out = REPORTS / "frames"
    out.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^\w]+", "_", folder.name) + ".png"
    sheet.save(out / name)
    age = (time.time() - dirs[-1].stat().st_mtime) / 60
    return f"{folder.name}, frames from {dirs[-1].name}, saved {age:.0f} minutes ago. If that is before the newest run, these frames are not from it.\n" + "\n".join(rows) + f"\nContact sheet: {out / name}\nBrightness cannot tell a right picture from a wrong one; only a look can."


def _fps_rows(path):
    rows = []
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("presents") and r.get("interval_end_s", 0) > r.get("interval_start_s", 0):
            rows.append(r)
    return rows


def speed_results(test=""):
    """Median FPS of each speed-test run. Base runs show the noise, and a change only counts if it beats that."""
    folder = SPEED
    if test:
        candidate = SPEED / test
        if not candidate.is_dir():
            return f"no test named {test}. Have: " + ", ".join(p.name for p in SPEED.iterdir() if p.is_dir())
        folder = candidate
    else:
        subs = sorted((p for p in SPEED.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime) if SPEED.exists() else []
        if subs:
            folder = subs[-1]
    files = sorted(folder.glob("*.jsonl"), key=lambda p: p.stat().st_mtime) if folder.exists() else []
    if not files:
        return "No speed test has been run. Use speed_test."
    out = [f"From {folder}. Median FPS after the first minute:"]
    base, other = [], []
    longest = max((r["interval_end_s"] for path in files for r in _fps_rows(path)), default=0)
    for path in files:
        rows = _fps_rows(path)
        settled = [r for r in rows if r["interval_end_s"] > 60]
        if not settled:
            out.append(f"  {path.stem}: too short to count, {len(rows)} rows")
            continue
        fps = statistics.median(r["presents"] / (r["interval_end_s"] - r["interval_start_s"]) for r in settled)
        seconds = settled[-1]["interval_end_s"]
        if seconds < longest / 2:
            out.append(f"  {path.stem}: ended early at {seconds:.0f} s, not counted (a crash or a closed game; its {fps:.1f} FPS is not comparable)")
            continue
        out.append(f"  {path.stem}: {fps:.1f} FPS over {seconds:.0f} s")
        if "warm" in path.stem or "first" in path.stem:
            continue
        (base if path.stem.lower().startswith("base") else other).append((path.stem, fps))
    if len(base) >= 2:
        spread = max(f for _, f in base) - min(f for _, f in base)
        out.append(f"Base runs differ by {spread:.1f} FPS between themselves: that is the noise.")
        low, high = min(f for _, f in base), max(f for _, f in base)
        for name, f in other:
            verdict = "above" if f > high else "below" if f < low else "inside"
            out.append(f"  {name} is {verdict} the base range.")
        out.append("A difference inside the noise is no result. One script on one scene is not a speed claim on GitHub: that also needs tools/trace_digest.py summaries of main and the change.")
    else:
        out.append("Fewer than two finished base runs, so the noise is unknown and no comparison can be claimed.")
    return "\n".join(out)


def window_mode_results(lines=40):
    log = SPEED / "window-modes.log"
    if not log.exists():
        return "Window modes were not watched yet. Use watch_window_modes, then Blu plays."
    text = log.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(text[-int(lines):]) + "\nOnly modes listed here were measured. Say which were not, rather than guessing them."


def validation_results(tag_a, tag_b="", folder=""):
    """VUID errors per side of a Vulkan validation run, and whether the layer really loaded. ctest can pass with errors logged."""
    base = Path(folder) if folder else HELPERS / "reports" / "validation"

    def side(tag):
        files = [p for p in base.glob(f"{tag}*") if p.suffix in (".log", ".txt")]
        if not files:
            return None, None, f"{tag}: no logs in {base}"
        ids = set()
        missing = 0
        for p in files:
            t = p.read_text(encoding="utf-8", errors="replace")
            ids.update(re.findall(r"VUID-[\w-]+", t))
            missing += t.count("was not found but was requested by env var VK_INSTANCE_LAYERS")
        control = base / f"{tag}-layer-control.txt"
        lines = len(control.read_text(encoding="utf-8", errors="replace").splitlines()) if control.exists() else 0
        status = base / f"{tag}.status"
        tail = " | ".join(status.read_text(encoding="utf-8", errors="replace").splitlines()[-3:]) if status.exists() else "no status file"
        ok = "layer loaded" if lines and not missing else "LAYER NOT PROVEN: this run shows nothing about validation"
        return ids, ok, f"{tag}: {len(ids)} distinct VUIDs, {ok} ({lines} control lines). {tail}"

    a, a_ok, a_text = side(tag_a)
    out = [a_text]
    if tag_b:
        b, b_ok, b_text = side(tag_b)
        out.append(b_text)
        if a is not None and b is not None:
            out.append("Only in " + tag_b + ": " + (", ".join(sorted(b - a)) or "none"))
            out.append("Only in " + tag_a + ": " + (", ".join(sorted(a - b)) or "none"))
    return "\n".join(out)


def rebuild_app(deploy=True):
    """Compiles Launcher.cs. Deploys only if it compiled, keeping the old exe. The running window keeps the old build until reopened."""
    source = TOOLS_DIR / "launcher" / "Launcher.cs"
    out = source.with_name("build-test.exe")
    web = TOOLS_DIR / "webview2"
    csc = r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
    r = subprocess.run([csc, "-nologo", "-target:winexe", "-optimize", "-r:System.Management.dll", "-r:System.Web.Extensions.dll", "-r:System.Windows.Forms.dll", "-r:System.Drawing.dll",
                        rf"-r:{web}\Microsoft.Web.WebView2.Core.dll", rf"-r:{web}\Microsoft.Web.WebView2.WinForms.dll", f"-out:{out}", str(source), *[str(source.with_name(n)) for n in ("TeamView.cs", "TownView.cs", "QuestView.cs") if source.with_name(n).exists()]],
                       capture_output=True, text=True, errors="replace", cwd=str(source.parent), creationflags=HIDDEN)
    if r.returncode:
        return "COMPILE FAILED, nothing changed:\n" + "\n".join(x for x in r.stdout.splitlines() if "error" in x.lower())[:2000]
    if not deploy:
        out.unlink(missing_ok=True)
        return "Compiled with no errors. Not deployed."
    exe = GAMES / "AnyPS5 Games.exe"
    stamp = time.strftime("%m%d-%H%M")
    # The open window keeps running from the old file, which Windows lets us rename but not delete; since 11-03 _tools
    # is on C: and the games folder on D:, so the old build is renamed next to the exe (same drive), then copied over.
    old = exe.with_name("AnyPS5 Games.exe.old-" + stamp)
    os.replace(exe, old)
    try:
        shutil.copy2(out, exe)
    except OSError:
        os.replace(old, exe)
        raise
    out.unlink(missing_ok=True)
    keep = source.with_name("AnyPS5 Games.exe.before-" + stamp)
    try:
        shutil.copy2(old, keep)
    except OSError:
        pass
    for stale in GAMES.glob("AnyPS5 Games.exe.old-*"):
        if stale != old:
            try:
                stale.unlink()
            except OSError:
                pass
    return f"Compiled and deployed ({exe.stat().st_size} bytes). Old build kept as {keep.name}. It takes effect when the app is reopened; a window not clicked through is not tested."


def kingdom_report():
    """The AI team's kingdom (Blu 11-04): each member's crowns, rank and freedoms from the real scoreboard, the treasury,
    the quests on the notice board and the last day's storms and festivals. Also refreshes repair-runs/world-state.json."""
    import crowns
    return crowns.report()


def district_status(district=""):
    """Each kingdom district's weather and building (Squire Qwen's ask, 11-04): the last day's crashes and stops over its libraries, the games its missing functions stop now, merges on AnyPS5 main in the last day and its quests, stormiest first. With district, only that one. Changes nothing."""
    import crowns
    return crowns.district_status(district)


KINGDOM = Path(r"C:\AnyPS5-work\kingdom\godot")
GODOT = Path(r"C:\AnyPS5-work\engine\godot-4.7.2\Godot_v4.7.2-stable_win64_console.exe")


@contextlib.contextmanager
def gpu_busy():
    """GPU work outside the games folder (a kingdom render) holds _tools\\live\\gpu-busy.flag, so the pipeline starts no
    game test or FPS run meanwhile (11-04: a render skewed the FPS optimizer's Octopath test). The pipeline ignores a
    flag older than 20 minutes."""
    flag = TOOLS_DIR / "live" / "gpu-busy.flag"
    flag.parent.mkdir(parents=True, exist_ok=True)
    if flag.exists() and time.time() - flag.stat().st_mtime >= 20 * 60:
        flag.unlink(missing_ok=True)
    mine = f"{os.getpid()} {time.strftime('%H:%M:%S')} kingdom render"
    # Created in one step only one job can win (skills\gpu_flag.ps1 does the same): on 11-04 two jobs that each saw the
    # flag free ran two games at once.
    try:
        fd = os.open(flag, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise RuntimeError("the GPU is busy: " + flag.read_text(encoding="utf-8-sig", errors="replace").strip()[:120])
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(mine)
    try:
        yield
    finally:
        try:
            if flag.read_text(encoding="utf-8") == mine:
                flag.unlink()
        except OSError:
            pass


def render_kingdom(cam="gate", refresh="no", mono="no"):
    """A picture of the 3D kingdom from one camera (gate, street, forge, over, realm), saved in the Godot project's shots
    folder. refresh=yes first re-exports the realm from teamshot\\export_godot.py (a few minutes). Opens a Godot window
    for about half a minute; refuses while a game runs."""
    if cam not in ("gate", "forge", "over", "realm", "library", "castle", "lineup", "embassy"):
        return "cam must be gate, forge, over, realm, library, castle, lineup or embassy."
    if str(refresh).lower() in ("yes", "true", "1"):
        r = subprocess.run([sys.executable, "-I", r"C:\AnyPS5-work\teamshot\export_godot.py"], capture_output=True, text=True, errors="replace", timeout=1800, creationflags=HIDDEN)
        if r.returncode:
            return "Export failed:\n" + (r.stderr or r.stdout)[-1500:]
    bw = str(mono).lower() in ("yes", "true", "1")
    name = cam + ("-mono" if bw else "")
    shot = KINGDOM / "shots" / f"{name}.png"
    extra = (["--mono"] if bw else []) + (["--lineup"] if cam == "lineup" else [])
    with gpu_busy():
        r = subprocess.run([str(GODOT), "--path", str(KINGDOM), "--resolution", "1920x1080", "--", f"--cam={cam}", f"--shot=shots/{name}.png", "--wait=200"] + extra,
                           capture_output=True, text=True, errors="replace", timeout=900, creationflags=HIDDEN)
    if "SHOT" not in r.stdout:
        return "Godot did not save the picture:\n" + "\n".join(x for x in (r.stdout + r.stderr).splitlines() if "ERROR" in x)[:1500]
    return f"Saved {shot} ({time.strftime('%H:%M')})."


def render_kingdom_side(wait_minutes=240):
    """The 2D kingdom's pictures (the AnyPS5 Games window, AI Team tab): the 3D kingdom seen from the side, the whole realm
    and each district, without the court (the window draws them live). Waits up to wait_minutes for no game to run, then
    holds the GPU flag for the minute or two it takes. Saved in _tools\\kingdom2d (s_*.jpg, side.json)."""
    out = TOOLS_DIR / "kingdom2d"
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(KINGDOM / "data" / "far_height.bin", out / "far_height.bin")
    end = time.time() + float(wait_minutes) * 60
    while any(_games(_processes())) or (TOOLS_DIR / "live" / "gpu-busy.flag").exists():
        if time.time() > end:
            return f"Still not free after {wait_minutes} minutes (a game ran or the GPU flag was held); nothing rendered."
        time.sleep(3)
    with gpu_busy():
        r = subprocess.run([str(GODOT), "--path", str(KINGDOM), "--resolution", "640x360", "--", f"--side={out}", "--hour=13"],
                           capture_output=True, text=True, errors="replace", timeout=1800, creationflags=HIDDEN)
    if "SIDE done" not in r.stdout:
        return "Godot did not finish the side pictures:\n" + "\n".join(x for x in (r.stdout + r.stderr).splitlines() if "ERROR" in x or "SIDE" in x)[:1500]
    return f"Saved {len(list(out.glob('s_*.jpg')))} side pictures in {out} ({time.strftime('%H:%M')})."


def render_kingdom_movie(mono="no", seconds=31):
    """A fly-over movie of the whole 3D kingdom (realm, embassy, library, castle, the forge district and its gate), 720p
    30 FPS, saved as shots\\kingdom-tour[-mono].mp4 in the Godot project. Takes a few minutes of GPU; refuses while a
    game runs, and holds the GPU flag so the pipeline starts no test meanwhile. Its frames are removed afterwards."""
    import imageio_ffmpeg
    bw = str(mono).lower() in ("yes", "true", "1")
    frames_dir = KINGDOM / "shots" / "tour-frames"
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir(parents=True)
    count = int(float(seconds) * 30)
    with gpu_busy():
        r = subprocess.run([str(GODOT), "--path", str(KINGDOM), "--resolution", "1280x720", "--write-movie", "shots/tour-frames/f.png", "--fixed-fps", "30",
                            "--quit-after", str(count), "--", "--movie"] + (["--mono"] if bw else []),
                           capture_output=True, text=True, errors="replace", timeout=3600, creationflags=HIDDEN)
    pngs = sorted(frames_dir.glob("f*.png"))
    if len(pngs) < count // 2:
        return f"Godot wrote {len(pngs)} of {count} frames:\n" + "\n".join(x for x in (r.stdout + r.stderr).splitlines() if "ERROR" in x)[:1500]
    out = KINGDOM / "shots" / f"kingdom-tour{'-mono' if bw else ''}.mp4"
    m = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-framerate", "30", "-i", str(frames_dir / "f%08d.png"),
                        "-c:v", "libx264", "-preset", "slow", "-pix_fmt", "yuv420p", "-crf", "30", "-maxrate", "6M", "-bufsize", "12M",
                        "-movflags", "+faststart", str(out)],
                       capture_output=True, text=True, errors="replace", timeout=1800, creationflags=HIDDEN)
    if m.returncode:
        return "ffmpeg failed: " + m.stderr[-800:]
    shutil.rmtree(frames_dir)
    return f"Saved {out} ({len(pngs)} frames, {out.stat().st_size / 1e6:.1f} MB, {time.strftime('%H:%M')})."


def brain_report(who=""):
    """The personality brains (C:\\AnyPS5-work\\kingdom\\brains): for each AI team member (or one, e.g. "build"), how many
    choices it has learned from, what it does most, what it says it cares about, and the nudges that shaped it. Villagers:
    how many, and the most common personalities."""
    import collections
    import brains
    rows = []
    for kind in ["boss", "king", "pr", "build", "fix", "exp", "repair", "coach", "supervise", "ask", "local"]:
        if who and who != kind:
            continue
        b = brains.ensure(kind)
        top = sorted(b.traits().items(), key=lambda kv: -kv[1])[:3]
        cares = sorted(b.cares.items(), key=lambda kv: -kv[1])[:3]
        rows.append(f"{kind}: {b.describe()} ({b.steps} lessons); does most: " + ", ".join(f"{a} {int(p * 100)}%" for a, p in top)
                    + "; cares most about: " + ", ".join(f"{c} {v:.2f}" for c, v in cares) + f"; shaped by: " + " | ".join(n["why"][:70] for n in b.nudges[-3:]))
    if not who:
        vill = brains.load_villagers()
        common = collections.Counter(b.describe() for b in vill.values()).most_common(5)
        rows.append(f"villagers: {len(vill)} brains; most common: " + ", ".join(f"{d} ({n})" for d, n in common))
    return "\n".join(rows)


def ask_team(question, members=""):
    """Asks AI team members (all Claude members, or a comma list of kinds such as "build,fix") what they think, each in
    its own model and role, one turn, no tools. Costs Claude usage (about $1 for the whole team); only when Blu asks."""
    import claude_bridge as cb
    kinds = [k.strip() for k in members.split(",") if k.strip()] or ["boss", "pr", "build", "fix", "exp", "repair", "coach", "supervise", "ask"]
    out = []
    for k in kinds:
        a = cb.voice(k, question)
        out.append(f"== {cb.MEMBERS.get(k, k)}\n{a.strip()[-1200:]}")
    return "\n\n".join(out)


def team_conversations(count=8):
    """The AI team's conversations (repair-runs/threads.json): the newest threads, who wrote to whom about which library,
    their real words and evidence, and whether each is a question, answered, used or closed. Changes nothing."""
    import talk
    return talk.report(int(count))


def reply_to_team(thread, text, evidence=""):
    """Squire Qwen (the local AI) answers a teammate's message in thread (e.g. T7): text is what you checked or will do,
    evidence what it rests on (a log line, a step number). Only real work, nothing invented."""
    import talk
    t = next((t for t in talk.load() if t["id"] == str(thread).strip()), None)
    if not t:
        return f"No conversation {thread}; team_conversations lists them."
    if t["messages"][-1]["to"] != "local":
        return f"{thread} is not waiting for the local AI."
    talk.post("local", t["messages"][-1]["from"], text, evidence, t["about"], t["id"])
    return f"Answered in {thread}."


def realm_report():
    """Where every AI team member is in the realm right now, what it is doing and why, its mood and energy, how it is
    travelling (on foot, horse, waystone), what it wants to try next, and the latest happenings (letters, journeys)."""
    import realm_sim
    return realm_sim.report()


def kingdom_decree(text, by="King"):
    """Posts a decree from the King or the Queen on the kingdom's notice board. It only announces."""
    import crowns
    return crowns.decree(text, by)


def deploy_app_when_idle(idle_minutes="20", what=""):
    """Put a changed AnyPS5 Games window in place only while Blu is away: waits for no keyboard or mouse input for idle_minutes, no game running, no Discord stream and the assistant idle, then compiles, swaps the exe (old one kept) and reopens the window. what says what the new window brings, for the log. Progress goes to app-updates.log next to this file, not the live console."""
    script = HERE / "deploy_when_idle.ps1"
    proc = subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-IdleMinutes", str(int(idle_minutes)), "-What", what or "the changes"], creationflags=HIDDEN)
    return f"Waiting in the background (process {proc.pid}) for {int(idle_minutes)} quiet minutes with no Discord stream; progress is in {HERE / 'app-updates.log'}."


_STARTERS = (("ai-player.py", "AI play session"), ("showcase-session.ps1", "showcase session"), ("test-launch.ps1", "the assistant's test"), ("auto-games.ps1", "the pipeline's test"))


def _windows():
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    found = {}

    def visit(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            buf = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, buf, 512)
            if buf.value and pid.value not in found:
                found[pid.value] = (hwnd, buf.value, user32.MonitorFromWindow(hwnd, 2))
        return True
    user32.EnumWindows(ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(visit), 0)
    return found


def _work_dir(cmd):
    found = ""
    for quoted, bare in re.findall(r'"([^"]*)"|(\S+)', cmd or ""):
        t = quoted or bare
        if t[:2] in ("-o", "-C"):
            t = t[2:]
        t = t.rstrip("\\")
        if len(t) > 3 and os.path.isabs(t) and os.path.isdir(t):
            found = t
    if not found:
        return ""
    leaf, up = Path(found).name, Path(found).parent.name
    return scrub_text(up + "\\" + leaf if leaf.lower().startswith("build") and up else leaf)


def _processes():
    r = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,ExecutablePath,CommandLine | ConvertTo-Json -Compress"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90, creationflags=HIDDEN)
    return {p["ProcessId"]: p for p in json.loads(r.stdout or "[]")}


def _games(procs):
    root = str(GAMES).lower() + "\\"
    for p in procs.values():
        path = p.get("ExecutablePath") or ""
        low = path.lower()
        if low.startswith(root) and "\\_tools\\" not in low and low.endswith(".exe") and not Path(path).name.lower().startswith("anyps5 "):
            yield p


def running_now():
    """Everything AnyPS5 is running right now: every game (FPS, who started it, its own window or inside the AnyPS5 Games window), every console job with its last line, every build and extraction. Works offline."""
    procs = _processes()
    wins = _windows()
    rows = []
    ai_game = False
    for p in _games(procs):
        by, at = "started by Blu", p
        for _ in range(12):
            at = procs.get(at.get("ParentProcessId"))
            if not at:
                break
            hit = next((label for key, label in _STARTERS if key in (at.get("CommandLine") or "")), None)
            if hit:
                by = hit
                break
        ai_game |= by == "AI play session"
        title, where = (wins[p["ProcessId"]][1], ", its own window") if p["ProcessId"] in wins else ("", "")
        f = TOOLS_DIR / "live" / f"title-{p['ProcessId']}.txt"
        if not title and f.exists():
            title, where = f.read_text(encoding="utf-8", errors="replace"), ", inside the AnyPS5 Games window"
        fps = re.search(r"FPS: ([0-9.]+)", title)
        rows.append(f"GAME {scrub_text(Path(p['ExecutablePath']).parent.name)} (process {p['ProcessId']}): {fps.group(1) + ' FPS' if fps else 'no FPS yet'}, {by}{where}")
    work = []
    for p in procs.values():
        name, cmd = (p.get("Name") or "").lower(), p.get("CommandLine") or ""
        label = None
        if name == "ninja.exe" or (name == "cmake.exe" and "--build" in cmd) or "build-game.ps1" in cmd:
            label = "BUILD " + _work_dir(cmd)
        elif ((name in ("winrar.exe", "rar.exe", "unrar.exe") or name.startswith("7z")) and re.search(r"\s[xe]\s", cmd)) or "exfat_extract.py" in cmd:
            label = "EXTRACTION into " + _work_dir(cmd)
        elif "pipeline-repair-agent.ps1" in cmd:
            label = "PIPELINE REPAIR (Claude)"
        elif "ai-player.py" in cmd and not ai_game:
            label = "AI PLAY SESSION starting its game"
        if label and label not in work:
            work.append(label)
    for f in sorted(JOBS_DIR.glob("*.json")) if JOBS_DIR.is_dir() else []:
        try:
            job = json.loads(f.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        if int(job.get("pid", 0)) not in procs:
            continue
        last = ""
        if job.get("log") and Path(job["log"]).exists():
            lines = [x.strip() for x in Path(job["log"]).read_text(encoding="utf-8-sig", errors="replace")[-4096:].splitlines() if x.strip()]
            last = lines[-1][:140] if lines else ""
        if not last.startswith("JOB EXIT"):
            work.append(f"CONSOLE JOB {scrub_text(job.get('what', ''))}: {scrub_text(job.get('game', ''))} since {job.get('started', '?')}" + (f", last line: {scrub_text(last)}" if last else ""))
    return "\n".join(rows + work or ["Nothing AnyPS5 is running: no game, console job, build or extraction."])


def _discord_streaming():
    import winreg
    for cap in ("graphicsCaptureProgrammatic", "graphicsCaptureWithoutBorder"):
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\{cap}\NonPackaged")
        except OSError:
            continue
        with key:
            for i in range(winreg.QueryInfoKey(key)[0]):
                name = winreg.EnumKey(key, i)
                if "discord" not in name.lower() or not os.path.exists(name.replace("#", "\\")):
                    continue
                with winreg.OpenKey(key, name) as app:
                    try:
                        start, stop = winreg.QueryValueEx(app, "LastUsedTimeStart")[0], winreg.QueryValueEx(app, "LastUsedTimeStop")[0]
                    except OSError:
                        continue
                    if start and not stop:
                        return True
    return False


def stream_check():
    """What Blu's Discord stream sends right now and whether the game windows are in it. Reads Discord's own log (read-only, never drives Discord): a window share sends only that one window, so test windows show only on a Screen share of the monitor they are on. Use it when Blu asks whether friends can see a game or test."""
    import ctypes
    if not _discord_streaming():
        return "Discord is not streaming right now."
    log = Path(os.environ.get("APPDATA", "")) / "discord" / "logs" / "renderer_js.log"
    source = None
    if log.exists():
        with open(log, "rb") as f:
            f.seek(max(0, log.stat().st_size - 8_000_000))
            text = f.read().decode("utf-8", "replace")
        hits = re.findall(r"Starting stream for source id (\S+) and name (.*?) and pid (\S+)", text)
        source = hits[-1] if hits else None
    procs = _processes()
    wins = _windows()
    user32 = ctypes.windll.user32
    out = []
    if not source:
        out.append("Discord is streaming, but its log does not say which source (log rotated?).")
    elif source[0].startswith("window:"):
        hwnd = int(source[0].split(":")[1])
        out.append(f"Discord shares ONE WINDOW: \"{scrub_text(source[1])}\" (window {hwnd}). Viewers see only that window; other windows, test windows included, are not sent, whichever process started them.")
    else:
        out.append(f"Discord shares a SCREEN ({source[0]}): viewers see everything on that monitor.")
    games = list(_games(procs))
    if not games:
        out.append("No AnyPS5 game is running.")
    app_win = next((w for pid, w in wins.items() if (procs.get(pid, {}).get("Name") or "").lower() == "anyps5 games.exe"), None)
    for p in games:
        name = scrub_text(Path(p["ExecutablePath"]).parent.name)
        w = wins.get(p["ProcessId"])
        if not w:
            out.append(f"{name}: inside the AnyPS5 Games window (or no window yet).")
            continue
        same = app_win is not None and w[2] == app_win[2]
        iconic = bool(user32.IsIconic(w[0]))
        out.append(f"{name}: its own window, {'minimized' if iconic else 'visible'}, {'on the same monitor as the AnyPS5 Games window' if same else 'on another monitor than the AnyPS5 Games window'}.")
    if source and source[0].startswith("window:") and games:
        out.append("To show the test windows: in Discord, change the stream to Screen and pick the monitor with the AnyPS5 Games window; the app puts each game window there, in front.")
    return "\n".join(out)


def pr_overlap(number):
    """Other open PRs touching the same files, from GitHub's API. tools/pr_overlap.py hangs here while D: is busy."""
    def gh(*args):
        r = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=240, creationflags=HIDDEN)
        return r.stdout if r.returncode == 0 else ""
    repo = "boykopovar/AnyPS5"
    raw = gh("pr", "view", str(int(number)), "-R", repo, "--json", "files,title")
    if not raw:
        return f"GitHub did not return PR {number}."
    mine = {f["path"] for f in json.loads(raw)["files"]}
    raw = gh("pr", "list", "-R", repo, "--state", "open", "--limit", "300", "--json", "number,title,files")
    if not raw:
        return "GitHub did not return the open PR list."
    prs = json.loads(raw)
    hits = []
    for p in prs:
        if p["number"] == int(number):
            continue
        same = mine & {f["path"] for f in p.get("files") or []}
        if same:
            hits.append((len(same), p["number"], p["title"], sorted(same)[:3]))
    hits.sort(reverse=True)
    out = [f"PR {number} changes {len(mine)} files. Checked the newest {len(prs)} open PRs; older ones were not checked."]
    out += [f"#{n}: {c} shared files ({', '.join(f)}) {t}" for c, n, t, f in hits[:15]]
    if not hits:
        out.append("None of them touch the same files.")
    return "\n".join(out)


def learned_index():
    path = LEARNED / "index.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return {}


def _save_index(index):
    LEARNED.mkdir(exist_ok=True)
    (LEARNED / "index.json").write_text(json.dumps(index, indent=1), encoding="utf-8")


def save_skill(name, description, code, usage=""):
    """Keeps a new tool. It stays marked UNTESTED until a run exits 0."""
    name = re.sub(r"[^a-z0-9_]+", "_", name.strip().lower()).strip("_")
    if not name:
        return "the tool needs a name"
    if not description.strip():
        return "the tool needs a one-line description, so it can be found later"
    try:
        ast.parse(code)
    except SyntaxError as e:
        return f"NOT SAVED: line {e.lineno}: {e.msg}. Fix the code and save again."
    titles = [p.name.split(" (")[0] for p in GAMES.iterdir() if p.is_dir() and not p.name.startswith("_") and len(p.name.split(" (")[0]) >= 4]
    named = [n for n in titles if re.search(r"\b" + re.escape(n) + r"\b", code, re.I)]
    if named:
        return f"NOT SAVED: it names a game ({named[0]}). A tool works for any game: take the game from sys.argv."
    LEARNED.mkdir(exist_ok=True)
    path = LEARNED / f"{name}.py"
    if path.exists():
        shutil.copy2(path, path.with_name(f"{name}.py.before-{time.strftime('%m%d-%H%M')}"))
    path.write_text(code, encoding="utf-8")
    index = learned_index()
    index[name] = {"description": description.strip(), "usage": usage.strip(), "saved": time.strftime("%Y-%m-%d %H:%M"), "runs": 0, "worked": 0, "last_exit": None}
    _save_index(index)
    return f"Saved {name}. It is UNTESTED: run it now with run_skill, on real input, before calling it done."


def list_skills(words=""):
    index = learned_index()
    if not index:
        return "No saved tools yet."
    want = [w for w in re.findall(r"\w+", words.lower())]
    out = []
    for name, s in sorted(index.items()):
        hay = (name + " " + s.get("description", "")).lower()
        if want and not any(w in hay for w in want):
            continue
        tested = f"worked {s.get('worked', 0)} of {s.get('runs', 0)} runs" if s.get("runs") else "UNTESTED"
        out.append(f"{name}: {s.get('description')} Arguments: {s.get('usage') or 'none'}. {tested}.")
    return "\n".join(out) or f"No saved tool matches {words}."


def run_skill(name, arguments="", seconds=600):
    name = re.sub(r"[^a-z0-9_]+", "_", name.strip().lower()).strip("_")
    path = LEARNED / f"{name}.py"
    index = learned_index()
    if not path.exists() or name not in index:
        return f"no saved tool {name}. list_skills shows them."
    args = re.findall(r'"([^"]*)"|(\S+)', arguments)
    argv = [a or b for a, b in args]
    try:
        r = subprocess.run([sys.executable, "-I", str(path), *argv], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=int(seconds), creationflags=HIDDEN, cwd=str(LEARNED))
        code, text = r.returncode, r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        code, text = -1, f"stopped after {seconds} s"
    s = index[name]
    s["runs"] = s.get("runs", 0) + 1
    s["worked"] = s.get("worked", 0) + (1 if code == 0 else 0)
    s["last_exit"] = code
    _save_index(index)
    return f"exit code {code}\n{scrub_text(text)[-5000:]}"


def real_space(folder):
    """What removing a folder would really free. Game folders and test copies share files by hard link, so their sizes overstate it."""
    alone = shared = 0
    per = {}
    root = Path(folder)
    if not root.is_dir():
        return f"{folder} is not a folder. Give its full path."
    for base, _, names in os.walk(root, onerror=lambda e: None):
        for n in names:
            try:
                st = os.stat(os.path.join(base, n), follow_symlinks=False)
            except OSError:
                continue
            if st.st_nlink <= 1:
                alone += st.st_size
                top = os.path.relpath(base, root).split(os.sep)[0]
                per[top] = per.get(top, 0) + st.st_size
            else:
                shared += st.st_size
    parts = ", ".join(f"{k} {v / 2**30:.1f} GB" for k, v in sorted(per.items(), key=lambda x: -x[1])[:6])
    return f"{root.name}: removing it would free about {alone / 2**30:.1f} GB. Another {shared / 2**30:.1f} GB is shared with another folder and stays. Biggest parts: {parts or 'none'}."


def download_file_state(path):
    """Is a .crdownload still being written? Its last change and whether Chrome holds it open, plus whether the keeper has reported in."""
    p = Path(path)
    if not p.is_file():
        name = p.name.lower()
        found = [Path(b) / x for root in (r"D:\ps5 games dumps from ps5", r"G:\ps5 games dumps from ps5", r"G:\ps5 downloads", os.path.expanduser("~\\Downloads")) for b, _, n in os.walk(root) for x in n if x.lower() == name or (name in x.lower() and x.lower().endswith(".crdownload"))]
        if not found:
            return f"No file named {path} in the game download folders."
        p = found[0]
    st = p.stat()
    minutes = (time.time() - st.st_mtime) / 60
    try:
        with open(p, "r+b"):
            pass
        held = "Nothing is writing it now, so this download has stopped." if minutes > 5 else "It changed recently."
    except OSError:
        held = "Chrome has it open."
    store = Path(os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data\Default\Local Extension Settings\eojnikflnaedepeikaphhmjnlngjicfa"))
    stamps = []
    for f in store.glob("*.log"):
        stamps += re.findall(rb"20\d\d-\d\d-\d\dT\d\d:\d\d:\d\d", f.read_bytes())
    keeper = f"The keeper's last check (UTC) is {stamps[-1].decode()}." if stamps else "The keeper reports no check time, so Chrome is running a version older than 1.2: Blu must press Reload on it in chrome://extensions."
    return f"{p.name}: {st.st_size / 1e9:.2f} GB, last changed {minutes:.0f} minutes ago. {held} {keeper} Only Blu can press Resume at chrome://downloads: Claude and this assistant cannot click in Chrome."


OBJDUMP = r"D:\AnyPS5-tools\mingw-extract\mingw64\bin\objdump.exe"


def disassemble_game(folder, address, before=120, after=60):
    """Plain disassembly around an address in a game's own program, from its converted .exe. No analysis step, so it works when the decompiler cannot."""
    folder = Path(folder)
    exes = sorted(folder.glob("*.exe"))
    if not exes:
        return f"{folder.name} has no program."
    where = int(str(address), 16)
    r = subprocess.run([OBJDUMP, "-d", "-M", "intel", "--no-show-raw-insn", f"--start-address={where - int(before):#x}", f"--stop-address={where + int(after):#x}", str(exes[0])],
                       capture_output=True, text=True, errors="replace", timeout=240, creationflags=HIDDEN)
    lines = [l for l in r.stdout.splitlines() if re.match(r"^\s*[0-9a-f]+:", l)]
    if not lines:
        return f"No code at {address} in {exes[0].name}. {r.stderr[:300]}"
    mark = f"{where:x}:"
    return "\n".join(("=> " if l.strip().startswith(mark) else "   ") + l.strip() for l in lines)


GHIDRA = Path(r"%USERPROFILE%\tools\ghidra_12.1.4_PUBLIC")
JDK = r"%USERPROFILE%\tools\jdk-21"
GHIDRA_PROJECTS = Path(r"C:\AnyPS5-work\ghidra-projects")


def read_program(path, what, seconds=3600):
    """Decompile addresses or find text in any program Ghidra can load: ELF, PRX, SPRX, EXE, DLL. The first read of a program analyses it and is slow; later reads reuse that."""
    path = Path(path)
    if not path.is_file():
        return f"{path} does not exist."
    items = [x.strip() for x in re.split(r"[;\n]", what) if x.strip()]
    if not items:
        return "say what to read: addresses like 0x4cbd6d, or text to find, separated by ;"
    key = re.sub(r"\W+", "_", str(path))[-70:].strip("_")
    work = GHIDRA_PROJECTS / key
    work.mkdir(parents=True, exist_ok=True)
    plain = work / re.sub(r"[^\w.-]+", "_", path.name)
    if not plain.exists() or plain.stat().st_size != path.stat().st_size or plain.stat().st_mtime < path.stat().st_mtime:
        shutil.copy2(path, plain)
    script = GHIDRA_PROJECTS / "AnyPS5Read.java"
    shutil.copy2(SKILLS / "AnyPS5Read.java", script)
    done = (work / "p.gpr").exists()
    head = [str(GHIDRA / "support" / "analyzeHeadless.bat"), str(work), "p"]
    source = ["-process", plain.name, "-noanalysis"] if done else ["-import", str(plain), "-analysisTimeoutPerFile", "2400"]
    env = dict(os.environ, JAVA_HOME=JDK, GHIDRA_INSTALL_DIR=str(GHIDRA))
    try:
        r = subprocess.run(head + source + ["-scriptPath", str(GHIDRA_PROJECTS), "-postScript", "AnyPS5Read.java", *items], capture_output=True, text=True, errors="replace", timeout=int(seconds), env=env, creationflags=HIDDEN)
    except subprocess.TimeoutExpired:
        return f"Stopped after {seconds} s while reading {path.name}. Use disassemble_game instead."
    keep = []
    showing = False
    for line in r.stdout.splitlines():
        if "AnyPS5Read.java>" in line:
            line = line.split("AnyPS5Read.java>", 1)[1].rstrip()
            line = re.sub(r"\s*\(GhidraScript\)\s*$", "", line)
            showing = True
            keep.append(line.strip())
        elif showing and "INFO" not in line and "REPORT" not in line:
            keep.append(line)
        elif "INFO" in line or "REPORT" in line:
            showing = False
    if not keep:
        tail = "\n".join(l for l in r.stdout.splitlines() if re.search(r"(?i)error|exception|fail|No load spec|conflict", l))[-1500:]
        return f"Ghidra could not read {path.name} (exit {r.returncode}): {tail or r.stderr[-800:]}"
    return scrub_text("\n".join(keep))[:30000]


NID_CACHE = {}


def nid_names(text):
    """PS5 imports show as scrambled names such as x8uvuFOPZhU_w_w. Puts the real name beside each one the project's list knows."""
    if not NID_CACHE:
        with (HERE / "nid-names.csv").open(encoding="utf-8", errors="replace") as f:
            for line in f:
                row = line.split()
                if len(row) >= 2:
                    NID_CACHE[row[0]] = row[1]

    def named(m):
        name = NID_CACHE.get(m.group(1))
        return f"{m.group(0)}/*{name}*/" if name else m.group(0)
    return re.sub(r"(?<![A-Za-z0-9_-])([A-Za-z0-9_-]{11})_[A-Za-z0-9]+_[A-Za-z0-9]+\b", named, text)


def elf_shift(folder):
    """How far a game's log addresses, taken from its converted .exe, are from the same code in its eboot.elf."""
    import struct
    folder = Path(folder)
    exe = sorted(folder.glob("*.exe"))[0]
    elf = (folder / "app0" / "eboot.elf").read_bytes()
    head = subprocess.run([OBJDUMP, "-h", str(exe)], capture_output=True, text=True, errors="replace", creationflags=HIDDEN).stdout
    m = re.search(r"\.elf0\s+[0-9a-f]+\s+([0-9a-f]+)\s+[0-9a-f]+\s+([0-9a-f]+)", head)
    base, offset = int(m.group(1), 16), int(m.group(2), 16)
    data = exe.read_bytes()
    probe = None
    for at in range(offset + 0x1000, offset + 0x400000, 0x1000):
        sample = data[at:at + 48]
        if len(set(sample)) > 20 and elf.count(sample) == 1:
            probe = (at, elf.find(sample))
            break
    if not probe:
        raise ValueError("no matching code found between the .exe and eboot.elf")
    phoff = struct.unpack_from("<Q", elf, 32)[0]
    for k in range(struct.unpack_from("<H", elf, 56)[0]):
        kind, _, off, va, _, size, _, _ = struct.unpack_from("<IIQQQQQQ", elf, phoff + k * 56)
        if kind == 1 and off <= probe[1] < off + size:
            return base + probe[0] - offset - (va + probe[1] - off)
    raise ValueError("the matching code is in no ELF segment")


def read_game_code(folder, what):
    """Decompile a game's own code by the addresses in its log, or find text in it. Shifts the addresses and names the PS5 functions."""
    folder = Path(folder)
    shift = elf_shift(folder)
    items = []
    for x in re.split(r"[;\n]", what):
        x = x.strip()
        if x:
            items.append(hex(int(x, 16) - shift) if x.startswith("0x") else x)
    return f"Log addresses are {shift:#x} above eboot.elf addresses.\n" + nid_names(read_program(folder / "app0" / "eboot.elf", ";".join(items)))


def set_game_setting(folder, name, value):
    """Keeps a setting a game needs. The app and the tests read game-env.txt; Play <game>.cmd gives it to a double-click, which a bare .exe cannot get."""
    folder = Path(folder)
    if not re.fullmatch(r"[A-Z][A-Z0-9_]+", name.strip()):
        return "the name must look like APS5_SOME_SETTING"
    exes = sorted(folder.glob("*.exe"))
    if not exes:
        return f"{folder.name} has no program yet."
    path = folder / "game-env.txt"
    rows = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                rows[k.strip()] = v.strip()
    if value.strip():
        rows[name.strip()] = value.strip()
    else:
        rows.pop(name.strip(), None)
    path.write_text("".join(f"{k}={v}\n" for k, v in rows.items()), encoding="utf-8")
    cmd = folder / f"Play {exes[0].stem}.cmd"
    cmd.write_text("@echo off\r\ncd /d \"%~dp0\"\r\n" + "".join(f'set "{k}={v}"\r\n' for k, v in rows.items()) + f'start "" "{exes[0].name}"\r\n', encoding="utf-8")
    return f"{folder.name} now starts with: {rows or 'no settings'}. The app uses it. For a double-click, use {cmd.name}, not the .exe. Test it with test_run."


def shader_register_writes(request_file, first, last):
    """Which memory loads in a dumped shader (a shader_*_failed.req) write scalar registers first to last, or read through them. Scans the words by their layout, so a data word can look like a load: check it."""
    import base64
    import struct
    raw = Path(request_file).read_bytes()
    text = re.match(rb"[A-Za-z0-9+/=]+", raw).group(0)
    data = base64.b64decode(text + b"=" * (-len(text) % 4))
    size = struct.unpack_from("<I", data, 17)[0]
    words = struct.unpack(f"<{size // 4}I", data[25:25 + size // 4 * 4])
    names = {0: "s_load_dword", 1: "s_load_dwordx2", 2: "s_load_dwordx4", 3: "s_load_dwordx8", 4: "s_load_dwordx16", 8: "s_buffer_load_dword", 9: "s_buffer_load_dwordx2", 10: "s_buffer_load_dwordx4", 11: "s_buffer_load_dwordx8", 12: "s_buffer_load_dwordx16"}
    count = {0: 1, 1: 2, 2: 4, 3: 8, 4: 16, 8: 1, 9: 2, 10: 4, 11: 8, 12: 16}
    first, last = int(first), int(last)
    out = [f"{Path(request_file).name}: program of {size:#x} bytes."]
    for i in range(len(words) - 1):
        x = words[i]
        op = (x >> 18) & 0xFF
        if x >> 26 != 0x3D or op not in names:
            continue
        data_reg, base = (x >> 6) & 0x7F, (x & 0x3F) * 2
        if data_reg <= last and data_reg + count[op] > first or base <= last and base + 1 >= first:
            out.append(f"{i * 4:#06x} {names[op]} s{data_reg} <- s{base} + {words[i + 1] & 0x1FFFFF:#x}")
    return "\n".join(out[:80])


SPIRV_NAMES = {43: "Constant", 54: "Function", 57: "FunctionCall", 61: "Load", 62: "Store", 65: "AccessChain", 71: "Decorate", 80: "CompositeConstruct", 81: "CompositeExtract", 124: "Bitcast", 128: "IAdd", 132: "IMul", 169: "Select", 170: "IEqual", 171: "INotEqual", 172: "UGreaterThan", 176: "ULessThan", 194: "ShiftRightLogical", 196: "ShiftLeftLogical", 199: "BitwiseAnd", 245: "Phi", 246: "LoopMerge", 247: "SelectionMerge", 248: "Label", 249: "Branch", 250: "BranchConditional", 251: "Switch"}


def spirv_makeup(path):
    """What a SPIR-V module is made of. path is a .spv file or a compute pipeline capture (capture.aps5cp from APS5_DUMP_COMPUTE_PIPELINES): words, blocks, loops, functions and the biggest instruction kinds."""
    import collections
    import struct
    data = Path(path).read_bytes()
    at = data.find(struct.pack("<I", 0x07230203))
    if at < 0:
        return f"{path} holds no SPIR-V."
    count = struct.unpack("<I", data[at - 4:at])[0] if at >= 4 else 0
    end = at + count * 4 if 5 <= count and at + count * 4 <= len(data) else len(data) - (len(data) - at) % 4
    words = struct.unpack(f"<{(end - at) // 4}I", data[at:end])
    total, times, functions = collections.Counter(), collections.Counter(), []
    i, current = 5, 0
    while i < len(words):
        size, op = words[i] >> 16, words[i] & 0xFFFF
        if size == 0:
            return f"{path}: malformed instruction at word {i}."
        total[op] += size
        times[op] += 1
        current = 0 if op == 54 else current + size
        if op == 56:
            functions.append(current)
        i += size
    rows = [f"{Path(path).name}: {len(words):,} words, {sum(times.values()):,} instructions, {times[248]:,} blocks, {times[246]} loops, {times[251]} switches, {times[245]:,} phis, {len(functions)} functions, largest {max(functions, default=0):,} words."]
    rows += [f"{n:>10,} words {times[op]:>8,} x {SPIRV_NAMES.get(op, op)}" for op, n in total.most_common(14)]
    return chr(10).join(rows)


def giant_shaders(folder, least=200000):
    """Read a game's anyps5.log for shaders the graphics driver is slow on: every pipeline of at least least SPIR-V words, how long the driver took, and any still compiling when the log ends."""
    log = Path(folder) / "anyps5.log"
    if not log.exists():
        return f"No anyps5.log in {folder}."
    done, started = [], {}
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.search(r"\[dispatch\] creating a pipeline for (\d+) SPIR-V words \(program (0x[0-9a-f]+)\)", line)
        if m:
            started[(m.group(2), m.group(1))] = int(m.group(1))
            continue
        m = re.search(r"\[dispatch\] created a pipeline for (\d+) SPIR-V words in (\d+) ms \(program (0x[0-9a-f]+)\)", line)
        if m:
            started.pop((m.group(3), m.group(1)), None)
            if int(m.group(1)) >= int(least):
                done.append((int(m.group(2)), int(m.group(1)), m.group(3)))
    rows = [f"{w:,} words took {ms / 1000:.1f} s, program {p}" for ms, w, p in sorted(done, reverse=True)[:12]]
    rows += [f"STILL COMPILING when the log ends: {w:,} words, program {p[0]}" for p, w in started.items() if w >= int(least)]
    return chr(10).join(rows) or f"No pipeline of at least {int(least):,} words in this log."


REPLAY_FOLDER = Path(r"C:\AnyPS5-work\spider-req\rp")


def precompile_pipelines(folder, minutes=60, unoptimized=False):
    """Compile every saved compute pipeline (capture.aps5cp under folder, saved by APS5_DUMP_COMPUTE_PIPELINES) with the graphics driver, to measure how long the driver takes. Slow the first time; a repeat takes under a second. Reports seconds for each. This did not fill the cache for the game itself in Spider-Man, so do not rely on it for that."""
    import json
    import os
    import subprocess
    tool = REPLAY_FOLDER / "agc_compute_pipeline_replay.exe"
    if not tool.exists():
        return f"{tool} is missing. Build agc_compute_pipeline_replay and copy it with the libraries from libs\\unpatched into that folder."
    rows = []
    for capture in sorted(Path(folder).rglob("capture.aps5cp")):
        report = capture.with_name("replay-report.json")
        report.unlink(missing_ok=True)
        try:
            run = subprocess.run([str(tool), str(capture), "--output", str(report)], capture_output=True, text=True, errors="replace", timeout=float(minutes) * 60, cwd=str(REPLAY_FOLDER), env={**os.environ, "APS5_COMPUTE_NO_DRIVER_OPT": "1"} if unoptimized else None)
        except subprocess.TimeoutExpired:
            rows.append(f"{capture.parent.name}: still compiling after {minutes} minutes, stopped.")
            continue
        if run.returncode or not report.exists():
            rows.append(f"{capture.parent.name}: failed: {(run.stderr or run.stdout)[-300:]}")
            continue
        data = json.loads(report.read_text(encoding="utf-8"))
        rows.append(f"{capture.parent.name}: {data['spirv_words']:,} words, driver took {data['milliseconds']['pipeline_create'] / 1000:.1f} s on {data['replay_device']['name']}.")
    return chr(10).join(rows) or f"No capture.aps5cp under {folder}."


def compare_pipelines(first, second):
    """Compare two saved pipelines (capture.aps5cp or .spv). Says which words differ between them, for finding why a shader misses the driver's cache from run to run, such as addresses baked into the shader."""
    import collections
    import struct

    def load(path):
        data = Path(path).read_bytes()
        at = data.find(struct.pack("<I", 0x07230203))
        if at < 0:
            raise ValueError(f"{path} holds no SPIR-V")
        count = struct.unpack("<I", data[at - 4:at])[0] if at >= 4 else 0
        if count < 5 or at + count * 4 > len(data):
            count = (len(data) - at) // 4
        return struct.unpack(f"<{count}I", data[at:at + count * 4])
    a, b = load(first), load(second)
    if a == b:
        return f"Identical: {len(a):,} words. The driver's cache can reuse a compile of either."
    if len(a) != len(b):
        def parts(words):
            found, i = [], 5
            while i < len(words):
                size = words[i] >> 16
                if size == 0:
                    break
                found.append(words[i:i + size])
                i += size
            return found
        first, second = parts(a), parts(b)
        count = lambda items: collections.Counter(item[0] & 0xFFFF for item in items)
        one, two = count(first), count(second)
        changed = {SPIRV_NAMES.get(op, op): two[op] - one[op] for op in sorted(set(one) | set(two)) if two[op] != one[op]}
        at = next((i for i, (x, y) in enumerate(zip(first, second)) if x != y), None)
        rows = [f"Different sizes: {len(a):,} and {len(b):,} words. Instructions {len(first):,} and {len(second):,}. Change in count by kind, second minus first: {changed}"]
        if at is not None:
            rows.append(f"First difference at instruction {at}:")
            rows += [f"  first:  {[hex(w) for w in first[i][:8]]}" for i in range(at, min(at + 4, len(first)))]
            rows += [f"  second: {[hex(w) for w in second[i][:8]]}" for i in range(at, min(at + 4, len(second)))]
        return chr(10).join(rows)
    kinds, rows, i = collections.Counter(), [], 5
    while i < len(a):
        size, op = a[i] >> 16, a[i] & 0xFFFF
        if size == 0:
            return f"Malformed instruction at word {i}."
        if a[i:i + size] != b[i:i + size]:
            kinds[SPIRV_NAMES.get(op, op)] += 1
            if len(rows) < 12:
                rows.append(f"  word {i}: {[hex(x) for x in a[i:i + min(size, 6)]]} and {[hex(x) for x in b[i:i + min(size, 6)]]}")
        i += size
    return chr(10).join([f"Same size, {sum(kinds.values())} instructions differ: {dict(kinds)}"] + rows)


def pipeline_variants(folder):
    """Compare every saved pipeline under folder (capture.aps5cp files). Pipelines with the same block count are one program built several ways, so whatever differs between them is what forces each new compile. Lists words, blocks, switches and image reads, grouped by block count."""
    import collections
    import struct
    groups = collections.defaultdict(list)
    for capture in sorted(Path(folder).rglob("capture.aps5cp")):
        data = capture.read_bytes()
        at = data.find(struct.pack("<I", 0x07230203))
        if at < 4:
            continue
        count = struct.unpack("<I", data[at - 4:at])[0]
        if count < 5 or at + count * 4 > len(data):
            continue
        words = struct.unpack(f"<{count}I", data[at:at + count * 4])
        times, i = collections.Counter(), 5
        while i < count:
            size, op = words[i] >> 16, words[i] & 0xFFFF
            if size == 0:
                break
            times[op] += 1
            i += size
        groups[times[248]].append((capture.parent.name, count, times[251], sum(times[op] for op in range(87, 98))))
    if not groups:
        return f"No capture.aps5cp under {folder}."
    rows = []
    for blocks, items in sorted(groups.items()):
        rows.append(f"{blocks:,} blocks: {len(items)} pipeline(s)" + (", one program built several ways" if len(items) > 1 else ""))
        rows += [f"  {name}: {count:,} words, {switches} switches, {reads} image reads" for name, count, switches, reads in items]
    return chr(10).join(rows)


def frame_time_split(folder):
    """Read a game's anyps5.log and say where frame time goes in the last 10-second reports: presents, GPU busy and idle per frame, driver capture time per dispatch, image-table time, and memory-map checks. Use it on a game that runs but is very slow. Run the game with APS5_TRACE_IMAGE_TABLE=1 to list each table."""
    log = Path(folder) / "anyps5.log"
    if not log.exists():
        return f"No anyps5.log in {folder}."
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    presents, busy, idle, table, capture, verify = [], [], [], [], [], ""
    for line in lines:
        m = re.match(r"\[present\] (\d+) presents over 10 s", line)
        if m:
            presents.append(int(m.group(1)))
            n = re.search(r"GPU busy ([0-9.]+) ms, idle gaps ([0-9.]+) ms", line)
            if n:
                busy.append(float(n.group(1)))
                idle.append(float(n.group(2)))
        m = re.match(r"\[image-table\] \(10 s\): (\d+) snapshots, (\d+) tables, (\d+) keys.* ([0-9.]+) ms$", line)
        if m:
            table.append((int(m.group(1)), int(m.group(2)), int(m.group(3)), float(m.group(4))))
        m = re.match(r"\[dispatch\] driver phases queue 0 direct \(10 s, (\d+) dispatches.*? capture ([0-9.]+) ", line)
        if m:
            capture.append((int(m.group(1)), float(m.group(2))))
        m = re.search(r"verify (\d+) calls", line) if line.startswith("[guestmem]") else None
        if m:
            verify = m.group(1)
    if not presents:
        return f"No [present] reports in {log}. Reports need APS5_PROFILE_DRAW or the test run's profile settings."
    rows = [f"Presents per 10 s, last 6: {presents[-6:]}"]
    if busy:
        rows.append(f"Per frame, latest: GPU busy {busy[-1]:.0f} ms, idle gaps {idle[-1]:.0f} ms. Large idle gaps mean the CPU side is the limit.")
    if table:
        s, n, k, ms = table[-1]
        rows.append(f"Image tables, latest 10 s: {s} snapshots, {n} tables, {k:,} keys, {ms:.0f} ms ({ms / 100:.0f} percent of the time).")
    if capture:
        count, each = capture[-1]
        rows.append(f"Driver capture, latest 10 s on queue 0: {count} dispatches, {each * count / 1000:.0f} ms in total.")
    if verify:
        rows.append(f"Memory-map checks so far in this run: {int(verify):,}.")
    return chr(10).join(rows)


def keeper_status():
    """Is the Chrome download keeper alive? Reads its saved state: last check, which downloads it counts as paused and since when, and the running file size. Read-only; it cannot click anything in Chrome."""
    import datetime
    import glob
    import json
    import os
    base = os.path.expandvars(r"%LOCALAPPDATA%\\Google\\Chrome\\User Data")
    rows = []
    for prefs in glob.glob(base + r"\\*\\Secure Preferences"):
        try:
            settings = json.load(open(prefs, encoding="utf-8")).get("extensions", {}).get("settings", {})
        except (OSError, ValueError):
            continue
        for key, entry in settings.items():
            if "chrome-auto-resume" not in str(entry.get("path", "")).lower():
                continue
            data = b""
            for file in sorted(glob.glob(os.path.join(os.path.dirname(prefs), "Local Extension Settings", key, "*.log")), key=os.path.getmtime):
                data += open(file, "rb").read()
            found = re.findall(rb'lastCheck.{1,3}"(\d{4}-[^"]+)"', data, re.S)
            if not found:
                rows.append("The keeper is installed but has saved no check yet.")
                continue
            last = datetime.datetime.fromisoformat(found[-1].decode().replace("Z", "+00:00"))
            age = (datetime.datetime.now(datetime.timezone.utc) - last).total_seconds()
            rows.append(f"Keeper last checked {age / 60:.1f} minutes ago." + (" It runs every minute, so it looks stopped: is Chrome open? If so, press Reload on it at chrome://extensions." if age > 300 else ""))
            paused = re.findall(rb'pausedSince.{1,3}?(\{[^}]*\})', data, re.S)
            items = re.findall(rb'"(\d+)":([0-9.e+]+)', paused[-1]) if paused else []
            rows += [f"Download {i.decode()} paused at {datetime.datetime.fromtimestamp(float(s) / 1000):%H:%M:%S}; the keeper resumes it 5 minutes after that." for i, s in items]
            if not items:
                rows.append("It counts no download as paused.")
            restarts = re.findall(rb'restarts.{1,3}?(\{[^}]*\})', data, re.S)
            counted = re.findall(rb'"(\d+)":(\d+)', restarts[-1]) if restarts else []
            rows += [f"Download {i.decode()} started over from the beginning {n.decode()} time(s). Its link does not continue, so a pause or network failure costs all its progress." for i, n in counted]
    return chr(10).join(rows) or "The keeper is not installed in Chrome (load _tools\\\\chrome-auto-resume at chrome://extensions)."


def check_7z_size(path):
    """Is a .7z file as long as its own header says? Needs no password and no unpacking. Use it when Chrome reports a content length mismatch. A match only means the length is right; it does not test the contents."""
    import struct
    file = Path(path)
    if not file.exists():
        return f"{path} does not exist."
    with open(file, "rb") as handle:
        head = handle.read(32)
    if head[:2] != b"7z" or len(head) < 32:
        return f"{file.name} does not start like a .7z file."
    offset, length = struct.unpack("<QQ", head[12:28])
    needed, size = offset + length + 32, file.stat().st_size
    if needed == size:
        return f"{file.name}: {size:,} bytes, exactly what its header says. The length is right; the contents are not tested."
    return f"{file.name}: {size:,} bytes but its header says {needed:,}. It is cut short or damaged, so it needs downloading again."


JOBS_DIR = HERE.parent / "live" / "jobs"


def _register_job(game, what, pid, log="", then="", watch="", total=""):
    import json
    import time
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    key = time.strftime("%Y%m%d-%H%M%S") + f"-{pid}"
    job = {"game": game, "what": what, "pid": int(pid), "log": str(log), "then": then, "watch": str(watch), "total": str(total), "started": time.strftime("%m-%d %H:%M")}
    (JOBS_DIR / f"{key}.json").write_text(json.dumps(job, indent=2), encoding="utf-8")
    return key


def console_job(game, what, command, then="", watch="", total=""):
    """Run a command for a game so the pipeline knows: its output shows in the live console, the pipeline leaves that game alone while it runs, its end is an EVENT, and then=rebuild or retest has the pipeline carry on after it. game is the pipeline's folder name for the game. Use it for every extraction, rebuild or copy done by hand."""
    import subprocess
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    import time
    log = JOBS_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}.log"
    wrapper = (f"$failed = $false; & {{ {command} }} *>&1 | ForEach-Object {{ if ($_ -is [System.Management.Automation.ErrorRecord] -and \"$($_.FullyQualifiedErrorId)\" -match 'CommandNotFound|ParserError|PathNotFound') {{ $failed = $true }}; \"$_\" }} | Out-File -LiteralPath '{log}' -Append -Encoding utf8; "
               f"'JOB EXIT ' + $(if ($failed) {{ 1 }} elseif ($null -ne $LASTEXITCODE) {{ $LASTEXITCODE }} else {{ 0 }}) | Out-File -LiteralPath '{log}' -Append -Encoding utf8")
    proc = subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", wrapper], creationflags=0x08000000)
    _register_job(game, what, proc.pid, log, then, watch, total)
    return f"Started {what} for {game} (process {proc.pid}). The live console shows its output within a minute; the pipeline waits on {game} until it ends."


def console_watch(game, what, pid, then="", watch="", total=""):
    """Tell the pipeline about a job that is already running (by its process ID), so the live console shows it and the pipeline waits on that game. watch is a folder whose size is shown as progress."""
    key = _register_job(game, what, pid, "", then, watch, total)
    return f"Registered {key}: the pipeline shows {what} for {game} and waits until process {pid} ends."


def console_progress(pid, folder, total_gb="0", label="copying"):
    """Show a long step the pipeline waits on without printing (a copy inside a build) in the live console: one top line (it becomes the window's Now: line, so label starts with the game name), then each file as it lands with its size, and a total every minute, until the process ends. folder is where the files appear."""
    proc = subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HERE / "trace_progress.ps1"), "-ProcessId", str(int(pid)), "-Folder", str(folder), "-TotalGB", str(float(total_gb)), "-Label", label], creationflags=HIDDEN)
    return f"The live console lists the files as they land (watcher {proc.pid}), until process {pid} ends."


def console_game_watch(game_dir, label=""):
    """Show a running game in the live console: a status line every minute (time up, frames, game memory, memory left) and the moments that matter from its log (a giant shader compile starting and how long it took, stop messages, crashes). test_run starts it by itself; use it for a game started another way. It waits up to 10 minutes for the game to start and ends when the game closes."""
    folder = Path(game_dir)
    if not folder.is_dir():
        folder = HERE.parent.parent / str(game_dir)
    proc = subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HERE / "trace_game.ps1"), "-GameDir", str(folder), "-Label", label or folder.name], creationflags=HIDDEN)
    return f"Watching {folder.name} in the live console (watcher {proc.pid})."


def pipeline_restart():
    """Restart the pipeline so it loads changed scripts, only between passes: never while it extracts, builds or tests. Returns what it did."""
    import subprocess
    trace = HERE.parent / "auto-games-trace.log"
    last = trace.read_text(encoding="utf-8", errors="replace").rstrip().splitlines()[-1] if trace.exists() else ""
    ps = ("$p=@(Get-CimInstance Win32_Process -Filter \"Name='powershell.exe'\" | Where-Object { $_.CommandLine -match 'auto-games\\.ps1' });"
          "$kids=@(Get-CimInstance Win32_Process | Where-Object { $p.ProcessId -contains $_.ParentProcessId -and $_.Name -ne 'conhost.exe' });"
          "'pipelines ' + $p.Count; 'children ' + (($kids | ForEach-Object { $_.Name }) -join ',')")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True).stdout
    children = next((line[len("children"):].strip() for line in out.splitlines() if line.startswith("children")), "?")
    if "pass done" not in last or children:
        return f"Not restarted: the pipeline is in the middle of something. Last line: {scrub_text(last[:150])}. {out.strip()}"
    stop = ("Get-CimInstance Win32_Process -Filter \"Name='powershell.exe'\" | Where-Object { $_.CommandLine -match 'auto-games\\.ps1' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force };"
            "schtasks /Run /TN 'AnyPS5 Games pipeline' | Out-Null")
    subprocess.run(["powershell", "-NoProfile", "-Command", stop], capture_output=True, text=True)
    return "Restarted between passes; it reads its requests and jobs at the start of its first pass."


def pipeline_checkup():
    """Why the pipeline is not building, testing or finding fixes: whether it and its watchdog run, how long its trace has been quiet, the nightly AnyPS5 build (failed runs, a state file without a commit, how old the AnyPS5 the games use is), flags that stop tests, games waiting for a test or a PR test, the last PR test results and build errors, and what the watchdog did. Each problem comes with the fix. Changes nothing."""
    import json
    import subprocess
    import time
    tools = HERE.parent
    problems, facts = [], []

    def ps(command):
        return subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True, text=True, creationflags=0x08000000).stdout.strip()

    procs = ps("@(Get-CimInstance Win32_Process -Filter \"Name='powershell.exe'\" | Where-Object { $_.CommandLine -match 'auto-games\\.ps1' }).Count")
    trace = tools / "auto-games-trace.log"
    lines = trace.read_text(encoding="utf-8", errors="replace").splitlines()[-4000:] if trace.exists() else []
    quiet = (time.time() - trace.stat().st_mtime) / 60 if trace.exists() else 0
    facts.append(f"pipeline processes: {procs}; trace quiet for {quiet:.0f} min; last line: {scrub_text(lines[-1][:140]) if lines else 'none'}")
    alive = tools / "auto-games.alive"
    alive_age = (time.time() - alive.stat().st_mtime) / 60 if alive.exists() else 999
    if procs in ("", "0") and min(quiet, alive_age) < 5:
        # Under load (or from a sandboxed session) the process list comes back without command lines; a trace or
        # heartbeat written in the last 5 minutes means it runs (the watchdog uses the same rule).
        facts.append("the process list shows no pipeline, but its trace or heartbeat is under 5 minutes old: it runs")
    elif procs in ("", "0"):
        problems.append("The pipeline is not running. Fix: pipeline_doctor (puts back a broken script or lost state, then starts it).")
    elif quiet > 60 and (tools / "pause.flag").exists():
        facts.append(f"the pipeline is quiet because Blu paused it {(time.time() - (tools / 'pause.flag').stat().st_mtime) / 3600:.1f} hours ago (pause.flag): not a fault, only Blu can resume it")
    elif quiet > 60:
        problems.append(f"The pipeline wrote nothing for {quiet:.0f} min. Fix: the watchdog stops a frozen program after 30 min and restarts a silent pipeline after 60; if it has not, check that the 'AnyPS5 pipeline watchdog' task runs.")
    tasks = ps("foreach ($n in 'AnyPS5 Games pipeline','AnyPS5 pipeline watchdog','AnyPS5 nightly health') { $t = Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue; $i = Get-ScheduledTaskInfo -TaskName $n -ErrorAction SilentlyContinue; if ($t) { '{0}|{1}|{2}|{3:yyyy-MM-dd HH:mm}' -f $n, $t.State, $i.LastTaskResult, $i.LastRunTime } else { '{0}|missing||' -f $n } }")
    for row in tasks.splitlines():
        name, state, result, last = (row.split("|") + ["", "", ""])[:4]
        facts.append(f"task {name}: {state}, last run {last}, result {result}")
        if state in ("missing", "Disabled"):
            problems.append(f"Scheduled task '{name}' is {state}. Fix: tell Blu; the pipeline cannot restart itself or take a newer AnyPS5 without it.")
        elif name == "AnyPS5 nightly health" and result not in ("0", "267009", ""):
            out = Path(r"D:\AnyPS5\scratchpad\helpers\health\last-run-output.txt")
            tail = [l for l in out.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()][-1:] if out.exists() else []
            problems.append(f"The nightly AnyPS5 build of {last} failed (result {result}): {scrub_text(tail[0][:200]) if tail else '?'}. While it fails the games stay on an old AnyPS5 and fixes merged since are never tried. Fix: read last-run-output.txt and repair D:\\AnyPS5\\scratchpad\\helpers\\health.py.")
    state = Path(r"D:\AnyPS5\scratchpad\helpers\health\state-latest.json")
    try:
        sha = json.loads(state.read_text(encoding="utf-8-sig")).get("sha", "") if state.exists() else ""
    except ValueError:
        sha = ""
    if state.exists() and not sha:
        problems.append("health\\state-latest.json has no commit (10-28 the pipeline wrote the games state over it). Fix: the watchdog restores it from the newest state-20*.json; or copy that file over it by hand.")
    settings = json.loads((tools / "settings.json").read_text(encoding="utf-8-sig"))
    commit = Path(settings.get("anyps5Build", "")) / "COMMIT.txt"
    if commit.exists():
        base = commit.read_text(encoding="utf-8-sig").split()
        age = (time.time() - commit.stat().st_mtime) / 86400
        facts.append(f"games use AnyPS5 {base[0] if base else '?'} ({age:.1f} days old)")
        if age > 2 and "AnyPS5 nightly health|Running" in tasks:
            facts.append(f"the games' AnyPS5 is {age:.0f} days old; the nightly build running now should replace it")
        elif age > 2:
            problems.append(f"The games' AnyPS5 is {age:.0f} days old: the pipeline takes a newer one only from a clean nightly run. Fix: see the nightly build line above.")
    for flag, why in (("pause.flag", "everything is paused"), ("tests-off.flag", "test launches are off")):
        if (tools / flag).exists():
            problems.append(f"_tools\\{flag} exists: {why}. Fix: only Blu turns this back on (Resume in the AnyPS5 Games window, or ask him).")
    games = json.loads((tools / "auto-state.json").read_text(encoding="utf-8-sig"))
    counts = {}
    for e in games.values():
        counts[e.get("status", "?")] = counts.get(e.get("status", "?"), 0) + 1
    facts.append("games: " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())))
    built = [n for n, e in games.items() if e.get("status") == "built"]
    if built:
        idle = ps("Add-Type 'using System;using System.Runtime.InteropServices;public class Idle{[StructLayout(LayoutKind.Sequential)]struct L{public uint s;public uint t;}[DllImport(\"user32.dll\")]static extern bool GetLastInputInfo(ref L l);public static uint Ms(){L l=new L();l.s=8;GetLastInputInfo(ref l);return (uint)Environment.TickCount-l.t;}}'; [Idle]::Ms()")
        minutes = int(idle or 0) / 60000
        problems.append(f"{len(built)} built game(s) wait for a test ({', '.join(built[:5])}). Tests start only after 10 minutes without keyboard or mouse (now {minutes:.0f} min) and with no game running.")
    for name, e in games.items():
        failure = e.get("failure") or {}
        if e.get("status") == "done" and str(e.get("note", "")).startswith("problem:") and not (isinstance(failure, dict) and failure.get("kind")):
            problems.append(f"{name} failed but has no failure recorded, so it gets no PR test. Fix: the pipeline reads its log on its next pass; if it does not, auto-games.ps1 is an old version (it reloads itself after a pass).")
        elif isinstance(failure, dict) and failure.get("kind"):
            chain = [c for c in (e.get("chain") or []) if isinstance(c, dict)]
            last = chain[-1] if chain else {}
            facts.append(f"failed: {name}: {failure.get('kind')} {failure.get('function') or str(failure.get('message', ''))[:70]}; PR test {e.get('triage') or 'never'} {e.get('triagedAt', '')}; last: {str(last.get('merged', ''))[:40]} {str(last.get('result', ''))[:90]}")
    errors = [l for l in lines if "CMake Error" in l or "FAILED:" in l or "error:" in l.lower()][-3:]
    for l in errors:
        facts.append("recent build error: " + scrub_text(l[:180]))
    since_start = lines[max((i for i, l in enumerate(lines) if "pipeline started" in l), default=0):]
    if any("submodule update --init" in l for l in since_start):
        problems.append("A PR build stopped because a submodule is not checked out. Fix: Build-PrLibraries runs 'git submodule update --init --recursive' after merging (added 11-03); if this is newer than that, run it by hand in D:\\AnyPS5\\wt-pr.")
    for l in [l for l in lines if "WATCHDOG" in l][-5:]:
        facts.append("watchdog: " + scrub_text(l[:180]))
    problems += parts_work_together(lines)
    return "\n".join(["Problems:"] + ([f"- {p}" for p in problems] or ["- none found"]) + ["", "Facts:"] + [f"- {f}" for f in facts])


def parts_work_together(lines):
    """Checks the pieces several sessions built still fit (Blu 11-03: "this app is not fluently all working together"):
    the assistant starts, every pipeline script parses, the app compiles, and no wait holds the pipeline for 30 minutes."""
    problems = []
    here = Path(__file__).resolve().parent
    r = subprocess.run([sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, r'{here}'); import assistant"],
                       capture_output=True, text=True, errors="replace", timeout=300, creationflags=HIDDEN)
    if r.returncode:
        last = (r.stderr.strip().splitlines() or ["?"])[-1]
        problems.append(f"The assistant cannot start: {last[:200]}. Fix: the newest change to assistant.py or a skill file (a tool added to TOOLS without its REQUIRED entry did this 11-03).")
    check = ("$bad = foreach ($f in Get-ChildItem -LiteralPath '" + str(TOOLS_DIR) + "' -Filter *.ps1) { $e = $null; "
             "[void][System.Management.Automation.Language.Parser]::ParseFile($f.FullName, [ref]$null, [ref]$e); if ($e.Count) { $f.Name } }; $bad -join ', '")
    bad = subprocess.run(["powershell", "-NoProfile", "-Command", check], capture_output=True, text=True, errors="replace", timeout=120, creationflags=HIDDEN).stdout.strip()
    if bad:
        problems.append(f"These pipeline scripts no longer parse: {bad}. Fix: restore the newest *.before-* copy or fix the last edit.")
    stamp = here / "self-test-ok.json"
    source = TOOLS_DIR / "launcher" / "Launcher.cs"
    known = json.loads(stamp.read_text(encoding="utf-8")) if stamp.exists() else {}
    if source.exists() and known.get("launcher") != source.stat().st_mtime:
        built = rebuild_app(False)
        if built.startswith("COMPILE FAILED"):
            problems.append("The AnyPS5 Games app (Launcher.cs) does not compile: " + built[:300])
        else:
            known["launcher"] = source.stat().st_mtime
            stamp.write_text(json.dumps(known), encoding="utf-8")
    stamped = [(l[:8], l[10:]) for l in lines if re.match(r"\d\d:\d\d:\d\d  ", l)]
    if stamped and "waiting" in stamped[-1][1]:
        same = re.sub(r"\d+", "#", stamped[-1][1])
        first = len(stamped) - 1
        while first > 0 and re.sub(r"\d+", "#", stamped[first - 1][1]) == same:
            first -= 1
        t0, t1 = (datetime.datetime.strptime(stamped[i][0], "%H:%M:%S") for i in (first, -1))
        minutes = ((t1 - t0).total_seconds() % 86400) / 60
        if minutes >= 30:
            problems.append(f"The pipeline has waited {minutes:.0f} min on one thing: {scrub_text(stamped[-1][1][:160])}. Fix: find that wait in auto-games.ps1 and check its condition still holds (11-03 two waits for the nightly build held every test for 8 hours). If the line says 'game X has run N min' with N over 120, read live/game-watch.json: frames 0 and one thread means a hung or orphaned game that blocks every test; tell Blu, do not kill it.")
    return problems


def predicted_missing(game="", refresh="no"):
    """Functions a game will need that AnyPS5 does not have yet, exact from its import table: predict_missing.py lists every function the game's executable and modules import (the relinker's own lists, in <game>/imports) and checks it against the AnyPS5 build the game uses (absent: no library has it; stub: always stops; partial: stops for some inputs), most likely next first. With a game: its share implemented, what stops it now and whether that is on the list, and the top of its list. Without one: the functions most games need. refresh=yes runs the check again first (about a minute; a game without import lists gets one relinker run into C:/AnyPS5-work/predict). Changes no game."""
    import json
    import subprocess
    tools = HERE.parent
    predictor = tools / "predict_missing.py"
    folder = None
    if game:
        folder = Path(game) if Path(game).is_dir() else GAMES / game
        if not (folder / "game.json").exists():
            return f"{folder} has no game.json."
    report = REPORTS / "predicted" / f"{folder.name}.json" if folder else REPORTS / "predicted-missing.json"
    if str(refresh).lower() in ("yes", "true", "1") or not report.exists():
        command = ["python", "-I", str(predictor)] + (["--game", str(folder)] if folder else ["--all", "--max-relinks", "0"])
        run = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=0x08000000, timeout=3600)
        if run.returncode != 0 and not report.exists():
            return scrub_text(f"predict_missing.py failed ({run.returncode}): {(run.stdout + run.stderr).strip()[-400:]}")
    data = json.loads(report.read_text(encoding="utf-8-sig"))
    if folder:
        if data.get("error"):
            return scrub_text(f"{folder.name}: not checked: {data['error']}")
        c = data["imports"]["byClass"]
        rows = [f"{folder.name} against AnyPS5 {data['commit']} ({data['updated']}): {data['imports']['unique']} imported functions, {data['implementedShare']}% implemented; absent {c.get('absent', 0)}, stub {c.get('stub', 0)}, partial {c.get('partial', 0)}, the game's own modules {c.get('module', 0)}."]
        stop = data.get("currentStop") or {}
        if stop.get("function") or stop.get("message"):
            on = data.get("stopPredicted")
            rows.append(f"Stops now at: {stop.get('function') or stop.get('message')}" + ("" if on is None else (" (on the list)" if on else " (not on the list: AnyPS5 exports it and does not mark it a stub)")))
        if data.get("missingLibraries"):
            rows.append("Needed libraries nobody provides: " + ", ".join(data["missingLibraries"]))
        rows.append("Most likely next:")
        rows += [f"  {p['rank']}. {p['function']} [{p['library']}] {p['class']}, {p['references']} call site(s)" + (" - stops it now" if p["stopsNow"] else " - same library as the stop" if p["sameLibraryAsStop"] else "") + (" - weak" if p["weak"] else "")
                 for p in data["predicted"][:15]]
        if len(data["predicted"]) > 15:
            rows.append(f"  ... {len(data['predicted']) - 15} more in _reports/predicted/{folder.name}.md")
        rows.append("auto-games.ps1 takes open PRs for the first of these along in its PR tests (Upcoming-Prs).")
        return scrub_text(chr(10).join(rows))
    rows = [f"Predicted missing functions over {data['games']} built games ({data['updated']}), most games first:"]
    rows += [f"  {f['function']} [{f['library']}] {f['class']}: {len(f['games'])} game(s)" + (f", stops {len(f['stopsNow'])} now" if f["stopsNow"] else "") for f in data["functions"][:20]]
    rows.append("Full table: _reports/predicted-missing.md; per game: _reports/predicted/<game>.md.")
    return scrub_text(chr(10).join(rows))


def module_copies(module, game=""):
    """Every game's copy of one sce_module file (for example libSceFontGsm.prx) checked side by side: build, string table size and whether each module, library and needed-file name its dynamic table points at lies inside the string table. A copy with names outside it is damaged when other copies of the same module are fine (11-04: Hollow Knight's libSceFontGsm.prx had every SCE value 0x220 too high), which no AnyPS5 change can fix: it needs a clean copy. With game, that game's copy first. Changes no file."""
    import hashlib
    import struct
    sources = GAMES / "_sources"
    copies = sorted(p for p in sources.glob(f"*/sce_module/{module}") if p.is_file())
    if game:
        copies.sort(key=lambda p: game.lower() not in p.parent.parent.name.lower())
    if not copies:
        return f"No game in {sources} has sce_module/{module}."
    names = {1: "needed file", 14: "own name", 0x61000045: "module import", 0x6100000f: "module import", 0x61000043: "module export",
             0x6100000d: "module export", 0x61000047: "library export", 0x61000049: "library import", 0x61000013: "library export", 0x61000015: "library import"}
    rows = [f"{len(copies)} copies of {module}:"]
    for path in copies:
        b = path.read_bytes()
        label = f"  {path.parent.parent.name}: {len(b)} bytes, sha1 {hashlib.sha1(b).hexdigest()[:10]}"
        try:
            phoff, = struct.unpack_from("<Q", b, 32)
            count, = struct.unpack_from("<H", b, 56)
            heads = [struct.unpack_from("<IIQQQQQQ", b, phoff + i * 56) for i in range(count)]
            dyn = next(h for h in heads if h[0] == 2)
            tags = []
            for o in range(dyn[2], dyn[2] + dyn[5], 16):
                t, v = struct.unpack_from("<QQ", b, o)
                if t == 0:
                    break
                tags.append((t, v))
            d = dict(tags)
            size = d.get(10, d.get(0x61000037))
            where = d.get(5, d.get(0x61000035))
            next(h for h in heads if h[0] == 1 and h[3] <= where < h[3] + h[5])
        except (struct.error, StopIteration, TypeError):
            rows.append(label + ": not a readable ELF dynamic table (encrypted or damaged)")
            continue
        bad =[(t, v & 0xffffffff) for t, v in tags if t in names and (v & 0xffffffff) >= size]
        if not bad:
            rows.append(label + f": string table 0x{size:x}, all {sum(1 for t, _ in tags if t in names)} names inside: fine")
            continue
        rows.append(label + f": string table 0x{size:x}, {len(bad)} name(s) outside it ({', '.join(f'{names[t]} 0x{off:x}' for t, off in bad[:4])}"
                    + (" ..." if len(bad) > 4 else "") + "): damaged copy unless every copy shows it; it needs a clean copy, not an AnyPS5 change")
    fine = sum(1 for r in rows if r.endswith(": fine"))
    rows.append(f"{fine} of {len(copies)} copies fine." + (" Compare a damaged copy's dynamic tags with a fine one of the same size to see what changed." if fine and fine < len(copies) else ""))
    return scrub_text("\n".join(rows))


def crash_ab(game, env, runs=4, seconds=60):
    """Does a setting stop a crash? Launches the game's FPS test copy (or the game folder when it has none) runs times as it is and runs times with env (NAME=value;NAME=value), alternating, each up to seconds, and counts the crashes. Waits until no game runs and no other GPU job holds the busy flag, then holds it so the pipeline waits. Runs as a console job; its RESULT line and logs land in _reports\\crash-ab. Changes no game file; a crash that never happens as it is proves nothing."""
    folder = Path(game) if Path(game).is_dir() else GAMES / game
    if not (folder / "game.json").exists():
        return f"{folder} has no game.json."
    copy = GAMES / "_test" / "_fps" / folder.name
    target = copy if (copy / "game.json").exists() else folder
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=[^;\"']*(;[A-Za-z_][A-Za-z0-9_]*=[^;\"']*)*", str(env)):
        return "env must be NAME=value pairs separated by ; (no quotes)."
    script = SKILLS / "crash_ab.ps1"
    command = f"& '{script}' -Copy '{target}' -Env '{env}' -Runs {int(runs)} -Seconds {int(seconds)}"
    return console_job(folder.name, f"crash A/B of {target.name}{' (FPS test copy)' if target == copy else ''}: {int(runs)} runs as it is, {int(runs)} with {env}", command)


def copy_base(folder):
    """The AnyPS5 commit a game folder or test copy was built from: the _tools\\anyps5-<commit> build whose libc.prx it
    has (copies keep no record of it, and a copy can lag the pipeline's current build). "" when none matches."""
    import hashlib
    mine = Path(folder) / "libs" / "libc.prx"
    if not mine.exists():
        return ""
    digest = hashlib.sha1(mine.read_bytes()).hexdigest()
    for build in sorted(TOOLS_DIR.glob("anyps5-*"), key=lambda p: p.stat().st_mtime, reverse=True):
        lib = build / "core" / "libs" / "libs" / "libc.prx"
        if lib.exists() and hashlib.sha1(lib.read_bytes()).hexdigest() == digest:
            return (build / "COMMIT.txt").read_text(encoding="utf-8-sig").split()[0] if (build / "COMMIT.txt").exists() else build.name[len("anyps5-"):]
    return ""


def pr_crash_ab(game, number, libs="", runs=5, seconds=60):
    """Does a PR stop a crash that comes and goes? Builds the PR's libraries merged onto the AnyPS5 commit the game's copies were built from (its own worktree wt-prtest and build folder, never the pipeline's wt-pr), then runs crash_ab with those libraries swapped into the game's FPS test copy. libs defaults to the libraries the PR changes. One console job; the RESULT line and logs land in _reports\\crash-ab. Changes no game file for good: each run puts the copy's own libraries back."""
    import subprocess
    folder = Path(game) if Path(game).is_dir() else GAMES / game
    if not (folder / "game.json").exists():
        return f"{folder} has no game.json."
    n = int(number)
    if not libs:
        files = subprocess.run(["gh", "pr", "view", str(n), "--repo", "boykopovar/AnyPS5", "--json", "files", "--jq", ".files[].path"], capture_output=True, text=True, creationflags=HIDDEN).stdout
        libs = ",".join(sorted({m.group(1) for m in re.finditer(r"(?m)^core/libs/prx/([^/]+)/(?!tests/)", files)}))
    if not libs or not re.fullmatch(r"[A-Za-z0-9_.]+(,[A-Za-z0-9_.]+)*", libs):
        return "Could not tell which libraries the PR changes; give libs=libA,libB."
    copy = GAMES / "_test" / "_fps" / folder.name
    target = copy if (copy / "game.json").exists() else folder
    base = copy_base(target)
    if not base:
        return f"Not tested: {target.name}'s libc.prx matches none of the AnyPS5 builds in _tools (anyps5-<commit>), so its commit is unknown."
    # 11-04 #6926 had merged main of 11-02 into itself: built onto the copy's 10-28 commit, its side would have carried a
    # week of main as well. Swapping newer libraries into both sides instead hung Hoa at start on both (libraries of two
    # commits do not mix), so such a PR waits until the game's copies are built from a commit that has its main.
    git = lambda *a: subprocess.run(["git", "-C", r"D:\AnyPS5", *a], capture_output=True, text=True, creationflags=HIDDEN)
    git("fetch", "-q", "origin", "main", f"+refs/pull/{n}/head:refs/remotes/pr-claude/{n}")
    merge_base = git("merge-base", f"refs/remotes/pr-claude/{n}", "origin/main").stdout.strip()[:10]
    if not merge_base:
        return f"Could not find PR #{n}'s merge base with main."
    ab = REPORTS / "crash-ab"
    if git("merge-base", "--is-ancestor", merge_base, base).returncode == 0:
        command = (f"& '{SKILLS / 'pr_libs.ps1'}' -Number {n} -Base {base} -Libs '{libs}'; if ($LASTEXITCODE -eq 0) {{ & '{SKILLS / 'crash_ab.ps1'}' "
                   f"-Copy '{target}' -LibDir '{ab / f'pr{n}-{base}'}' -Runs {int(runs)} -Seconds {int(seconds)} }}")
        what = f"PR #{n}: build {libs} onto {base}, then crash A/B of {target.name} with them"
    else:
        return (f"Not tested: PR #{n} carries AnyPS5 main up to {merge_base}, newer than {base}, which {target.name} was built from. "
                f"Its libraries would bring that newer main along, and libraries of two commits swapped into one copy hang the game. "
                f"Test it once the pipeline has taken an AnyPS5 at or after {merge_base} and rebuilt the game.")
    return console_job(folder.name, what, command)


def predicted_ranking(refresh="no"):
    """The functions most built games import that AnyPS5 lacks (absent), has only as a stub, or stops on for some inputs (partial), ranked by games it stops now, then games that import it, from every game's import table. refresh=yes checks all games again first (no relinker runs). Changes no game."""
    return predicted_missing("", refresh)


def fix_predictions(game="", refresh="no"):
    """What most likely gets a failed game past its stop, ranked with the evidence (_tools/predict_fix.py): a launch setting the stop message names, a playbook's workaround, a newer AnyPS5 commit already merged (the code that threw changed, the message is gone, or a PR the playbook names was merged after the game's build), an open PR that changes the code that threw, the game's own graphics mode. Shows what the pipeline already tried on the fix test copy (_test/_fix/<game>) and the verdict: fixed, further (past the stop), same stop, unclear, not tested. The pipeline keeps a setting in game-env.txt only after a run proved it. refresh=yes predicts again first (git and cached gh, no Claude, under a minute per game). Changes no game."""
    import json
    import subprocess
    tools = HERE.parent
    report = REPORTS / "fix-predictions.json"
    folder = None
    if game:
        folder = Path(game) if Path(game).is_dir() else GAMES / game
        if not (folder / "game.json").exists():
            return f"{folder} has no game.json."
    if str(refresh).lower() in ("yes", "true", "1") or not report.exists():
        command = ["python", "-I", str(tools / "predict_fix.py"), "predict"] + (["--game", str(folder)] if folder else [])
        run = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=0x08000000, timeout=1800)
        if run.returncode != 0 and not report.exists():
            return scrub_text(f"predict_fix.py failed ({run.returncode}): {(run.stdout + run.stderr).strip()[-400:]}")
    games = json.loads(report.read_text(encoding="utf-8-sig")).get("games", {})
    if folder:
        g = games.get(folder.name)
        if not g:
            return f"{folder.name} has no fix prediction: it is not a failed game, or refresh=yes was not run since it failed."
        s = g["stop"]
        rows = [f"{folder.name} stops at {s['kind']}: {s.get('function') or s.get('message') or s.get('where') or '-'} ({s.get('source', '')}, {s.get('when', '')}); AnyPS5 {g['where'].get('commit')}, origin/main {g['where'].get('main')}."]
        rows += [f"Seen on {c['source']}: {c['kind']}: {c.get('function') or c.get('message', '')[:120]}" for c in g.get("copies", []) if c.get("kind")]
        for i, c in enumerate(g["candidates"][:8], 1):
            t = c.get("tried")
            rows.append(f"  {i}. {c['title']} (score {c['score']}): {' / '.join(c['evidence'])[:300]}"
                        + (f" -> tried {t['when']}: {t['verdict']}, {t['result'][:100]}" if t else " -> to test" if c.get("testable") else ""))
        if not g["candidates"]:
            rows.append("Nothing found: no setting, playbook entry, newer commit or open PR points at this stop.")
        return scrub_text(chr(10).join(rows))
    rows = [f"Fix predictions for {len(games)} failed game(s):"]
    for name, g in sorted(games.items()):
        top = g["candidates"][0] if g["candidates"] else None
        tried = [c for c in g["candidates"] if c.get("tried")]
        rows.append(f"  {name}: {g['stop']['kind']} -> " + (f"{top['title']} ({top['score']})" if top else "nothing found")
                    + (f"; tried: " + ", ".join(f"{c['id']} {c['tried']['verdict']}" for c in tried[:3]) if tried else ""))
    rows.append("Full table: _reports/fix-predictions.md.")
    return scrub_text(chr(10).join(rows))


def _fix_tool():
    import importlib.util
    spec = importlib.util.spec_from_file_location("predict_fix", HERE.parent / "predict_fix.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fix_run(*args, seconds=600):
    import subprocess
    run = subprocess.run(["python", "-I", str(HERE.parent / "predict_fix.py"), *args], capture_output=True, text=True, encoding="utf-8",
                         errors="replace", creationflags=HIDDEN, timeout=seconds)
    return (run.stdout + run.stderr).strip()


def _game_folder(game):
    folder = Path(game) if Path(game).is_dir() else GAMES / game
    return folder if (folder / "game.json").exists() else None


def game_stop(game):
    """What stopped a game, read the way the pipeline reads it: its newest log, else its newest saved crash log, else what the pipeline recorded; and what its test copies (_test/<game>, _test/_fix/<game>) stopped at. Kind: missing function, stopped (an AnyPS5 message), crash (where), no frames. Changes nothing."""
    folder = _game_folder(game)
    if not folder:
        return f"{game} has no game.json."
    pf = _fix_tool()
    state = pf.load(pf.STATE, {})
    entry = next((e for e in state.values() if Path(e.get("gameDir") or "-").name == folder.name), {})
    own, copies = pf.game_stops(entry, folder)
    rows = [f"{folder.name}: {own['kind']}: {pf.stop_label(own)} ({own.get('source', '')}, {own.get('when', '')})."]
    if own.get("line"):
        rows.append(f"Line: {own['line'][:240]}")
    rows += [f"{c['source']} ({c.get('when', '')}): {c['kind']}: {pf.stop_label(c)}" for c in copies if c.get("kind")]
    rows.append(f"Pipeline record: {(entry.get('failure') or {}).get('kind', 'none')}; note: {str(entry.get('note', ''))[:160]}")
    return scrub_text(chr(10).join(rows))


def make_fix_copy(game):
    """Builds the game's fix copy, _test/_fix/<game>, on the AnyPS5 build the game itself uses (its game.json pin, else settings.json), in the background with make_test_copy.ps1 -Into. The game folder is only read. A copy already on that build is kept; one on another build gets its libs folder moved to _test/_set aside first (never recycled). Then try_fix tests a setting on it."""
    import json
    import subprocess
    folder = _game_folder(game)
    if not folder:
        return f"{game} has no game.json."
    cfg = json.loads((folder / "game.json").read_text(encoding="utf-8-sig"))
    build = cfg.get("anyps5Build") or json.loads((HERE.parent / "settings.json").read_text(encoding="utf-8-sig"))["anyps5Build"]
    copy = GAMES / "_test" / "_fix" / folder.name
    have = json.loads((copy / "game.json").read_text(encoding="utf-8-sig")) if (copy / "game.json").exists() else None
    if have and have.get("anyps5Build") == build and (copy / f"{have.get('exeName') or have.get('title')}.exe").exists():
        return f"{copy} is already built on {Path(build).name}; use try_fix."
    if have and have.get("anyps5Build") != build and (copy / "libs").exists():
        aside = GAMES / "_test" / "_set aside" / f"{folder.name} libs {time.strftime('%m%d-%H%M')}"
        aside.parent.mkdir(parents=True, exist_ok=True)
        (copy / "libs").rename(aside)
    copy.parent.mkdir(parents=True, exist_ok=True)
    log = copy.parent / f"{folder.name}.build.log"
    with log.open("w", encoding="utf-8") as out:
        subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SKILLS / "make_test_copy.ps1"), "-Name", folder.name,
                          "-Build", str(build), "-Into", str(copy)], stdout=out, stderr=subprocess.STDOUT, creationflags=HIDDEN)
    return f"Building {copy} on {Path(build).name} in the background (several minutes on D:); progress in {log}. Then try_fix."


def try_fix(game, candidate, minutes="8"):
    """Tests one launch-setting candidate from fix_predictions on the game's fix copy, in the background: the game's own game-env.txt plus the setting, until it exits, runs 3 minutes past its first frame, or the minutes are up; then records the verdict (fixed, further, same stop, unclear) in _tools/fix-tries.json and the report. Refuses while any game runs. A PR or newer AnyPS5 candidate is tried by the pipeline (queue_fix_try). Changes only the fix copy."""
    import subprocess
    folder = _game_folder(game)
    if not folder:
        return f"{game} has no game.json."
    if not str(candidate).startswith("setting:"):
        candidate = "setting:" + str(candidate)
    log = GAMES / "_test" / "_fix" / f"{folder.name}.try.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w", encoding="utf-8") as out:
        subprocess.Popen(["python", "-I", str(HERE.parent / "predict_fix.py"), "try", "--game", str(folder), "--id", candidate, "--minutes", str(float(minutes))],
                         stdout=out, stderr=subprocess.STDOUT, creationflags=HIDDEN)
    return f"Trying {candidate} on {folder.name}'s fix copy for up to {float(minutes):g} min in the background; the verdict lands in {log} and fix_predictions."


def record_fix_try(game, candidate, result, copy=""):
    """Records the outcome of a fix try run by hand (test_run, timeline_test) so the pipeline does not repeat it: result is the run's one-line outcome, starting with runs:, exited, stalls: or no frames; copy is the folder that ran (default the fix copy), whose log gives the next stop. Prints the verdict."""
    folder = _game_folder(game)
    if not folder:
        return f"{game} has no game.json."
    copy = copy or str(GAMES / "_test" / "_fix" / folder.name)
    pf = _fix_tool()
    version = next((c.get("version", "") for c in pf.load(pf.REPORTS / "fix-predictions.json", {"games": {}}).get("games", {}).get(folder.name, {}).get("candidates", []) if c["id"] == candidate), "")
    return scrub_text(_fix_run("record", "--game", str(folder), "--copy", copy, "--id", candidate, "--version", version, "--result", result))


def keep_fix_setting(game, candidate):
    """Keeps a launch setting a fix try proved (verdict fixed or further) in the game's game-env.txt and Play <game>.cmd, through set_game_setting. Refuses a setting no try has proven. Tell Blu, and queue a retest of the game itself (pipeline_control retest <game>)."""
    folder = _game_folder(game)
    if not folder:
        return f"{game} has no game.json."
    if not str(candidate).startswith("setting:"):
        candidate = "setting:" + str(candidate)
    return scrub_text(_fix_run("keep", "--game", str(folder), "--id", candidate, seconds=60))


def queue_fix_try(game):
    """Asks the pipeline to predict and try fixes for a failed game in its next quiet pass (fixtry in _tools/requests.txt): settings and newer AnyPS5 or playbook PRs on the fix copy, then its PR test. Use it for PR and newer-AnyPS5 candidates, which need the PR worktree build."""
    folder = _game_folder(game)
    if not folder:
        return f"{game} has no game.json."
    with (HERE.parent / "requests.txt").open("a", encoding="utf-8") as f:
        f.write(f"fixtry {folder.name}\n")
    return f"Queued: the pipeline tries {folder.name}'s fix prediction in its next quiet pass (after rechecks, never while a game runs or the nightly build works). Watch for FIX TRY in its log."


def _predictor():
    import importlib.util
    spec = importlib.util.spec_from_file_location("predict_missing", HERE.parent / "predict_missing.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def why_function_missing(function, game=""):
    """Why AnyPS5 counts a function as missing, a stub or partial: where it is defined in the AnyPS5 source of the build a game uses (or the build the games use), the lines that stop with NotImplemented_nid_no_patch and the conditions before them, what docs/dev/debt says about it, and which games import it. All offline: the source is the build's commit unpacked from the local git repository. Changes nothing."""
    import json
    import re
    import subprocess
    pm = _predictor()
    settings = json.loads((HERE.parent / "settings.json").read_text(encoding="utf-8-sig"))
    cfg = {}
    if game:
        folder = Path(game) if Path(game).is_dir() else GAMES / game
        if (folder / "game.json").exists():
            cfg = json.loads((folder / "game.json").read_text(encoding="utf-8-sig"))
    root, commit = pm.build_of(cfg, settings)
    source = pm.source_tree(commit)
    audit = pm.load_audit(source)
    progress = audit.progress
    name = audit.nid_names.strip_nid_postfix(function.strip())
    facts = pm.build_facts(audit, root, commit, source)
    kinds = {}
    partial = {(p, n) for p, n in facts["partial"]}
    for provider, nid, stub_name in facts["stubs"]:
        if audit.nid_names.strip_nid_postfix(stub_name) == name:
            kinds[provider] = "partial" if (provider, nid) in partial else "stub"
    exported = sorted(set(facts["exports"].get(audit.nid_names.compute_nid(name), [])) | {f for n, files in facts["exports"].items() if audit.nid_names.strip_nid_postfix(n.split("#", 1)[0]) == name for f in files})
    rows = [f"{name} in AnyPS5 {commit} ({root.name}):"]
    if not exported and not kinds:
        rows.append("absent: no AnyPS5 library exports it. A new export is needed (or a PR that adds one).")
    else:
        rows.append("exported by " + (", ".join(exported) or "?") + "; " + (", ".join(f"{p}: {k}" for p, k in sorted(kinds.items())) if kinds else "implemented (no NotImplemented path found by import_audit)"))
    shown = 0
    for cpp in sorted((source / "core" / "libs" / "prx").rglob("*.cpp")):
        if "tests" in cpp.parts:
            continue
        raw = cpp.read_text(encoding="utf-8", errors="ignore")
        if name not in raw:
            continue
        text = progress.code(raw)
        if len(text) != len(raw):
            text = raw
        stops = audit.always_stub_calls(text)
        for match in progress.DEFINITION.finditer(text):
            if audit.nid_names.strip_nid_postfix(match.group(1)) != name:
                continue
            start = match.start()
            end = progress.body_end(text, match.end() - 1)
            first = raw.count("\n", 0, start) + 1
            rel = cpp.relative_to(source).as_posix()
            rows.append(f"defined at {rel}:{first}")
            body = raw[start:end + 1].splitlines()
            hits = [i for i, line in enumerate(body) if any(re.search(rf"\b{re.escape(s)}\s*\(", line) for s in stops)]
            if not hits:
                rows.append("  no NotImplemented call in its own body (a helper it calls may stop)")
            for i in hits[:8]:
                context = [l.strip() for l in body[max(0, i - 2):i + 1] if l.strip()]
                rows.append(f"  line {first + i}: " + " | ".join(c[:160] for c in context))
                shown += 1
            if len(hits) > 8:
                rows.append(f"  ... {len(hits) - 8} more stop lines")
    if shown == 0 and (exported or kinds) and not any(r.startswith("defined at") for r in rows):
        rows.append("no APS5_VABI definition found by that name (it may be generated or aliased by NID)")
    libs = {p.removesuffix(".prx") for p in list(kinds) + exported}
    for lib in sorted(libs | {l.removesuffix(".native") for l in libs}):
        debt = subprocess.run(["git", "-C", str(pm.REPO), "show", f"{commit}:docs/dev/debt/{lib}.md"], capture_output=True, text=True, encoding="utf-8", errors="replace")
        if debt.returncode == 0:
            notes = [l.strip() for l in debt.stdout.splitlines() if name in l]
            rows.append(f"docs/dev/debt/{lib}.md: " + (" / ".join(n[:200] for n in notes[:4]) if notes else "does not name it"))
    ranking = REPORTS / "predicted-missing.json"
    if ranking.exists():
        entry = next((f for f in json.loads(ranking.read_text(encoding="utf-8-sig"))["functions"] if f["function"] == name), None)
        if entry:
            rows.append(f"imported by {len(entry['games'])} built game(s)" + (f", stops {', '.join(entry['stopsNow'])} now" if entry["stopsNow"] else "") + ": " + ", ".join(g.rsplit(' (', 1)[0] for g in entry["games"][:8]) + (" ..." if len(entry["games"]) > 8 else ""))
    return scrub_text(chr(10).join(rows))


def missing_exe(game):
    """Why a built game has no .exe, or what a rebuild will use: Defender detections of files in its folder, whether the folder is excluded from scanning, and whether the dump and resources folders in its game.json still exist. When they are gone but the game's own app0 is whole, build-game.ps1 rebuilds from app0 (hard links, no second copy). Never changes Defender."""
    import json
    import os
    import subprocess
    games = HERE.parent.parent
    folder = games / game if not Path(game).is_dir() else Path(game)
    if not (folder / "game.json").exists():
        return f"{folder} has no game.json."
    rows = []
    exes = list(folder.glob("*.exe"))
    rows.append(f"exe: {exes[0].name}" if exes else "exe: none")
    ps = ("$f=$env:MISSING_EXE_FOLDER; Get-MpThreatDetection | Where-Object { ($_.Resources -join ';') -like ('*'+$f+'*') } | ForEach-Object { '{0:yyyy-MM-dd HH:mm} {1} ran:{2}' -f $_.InitialDetectionTime, (Get-MpThreat -ThreatID $_.ThreatID).ThreatName, (Get-MpThreat -ThreatID $_.ThreatID).DidThreatExecute };"
          "$x=@((Get-MpPreference).ExclusionPath); if ($x -contains 'N/A: Must be an administrator to view exclusions') { 'excluded: cannot read without administrator' } elseif (@($x | Where-Object { $_ -and $f.StartsWith($_.TrimEnd([char]92), 'OrdinalIgnoreCase') }).Count) { 'excluded: yes' } else { 'excluded: no' }")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], env={**os.environ, "MISSING_EXE_FOLDER": str(folder)}, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip().splitlines()
    detections = [line for line in out if not line.startswith("excluded:")]
    rows += [f"Defender: {line}" for line in detections] or ["Defender: no detections in this folder"]
    rows += [line for line in out if line.startswith("excluded:")]
    cfg = json.loads((folder / "game.json").read_text(encoding="utf-8-sig"))
    eboot = cfg.get("eboot") or "eboot.bin"
    for key in ("dump", "resources", "sceSys"):
        if cfg.get(key):
            rows.append(f"{key}: {'there' if Path(cfg[key]).exists() else 'gone'}")
    gone = any(cfg.get(key) and not Path(cfg[key]).exists() for key in ("dump", "resources"))
    whole = (folder / "app0" / "sce_sys").is_dir() and (folder / "app0" / eboot).exists()
    if gone:
        rows.append("A rebuild uses the game's own app0 in place of the deleted folders." if whole else "A deleted folder and no whole app0: it cannot be rebuilt until the game's files are back.")
    if not exes and detections:
        rows.append("Defender removed the exe. Rebuild.cmd makes a new one; Blu decides about exclusions, never change them.")
    return scrub_text(chr(10).join(rows))


def check_rar(path):
    """Which of the saved archive passwords opens a .rar, and is every file in it whole? Never prints a password, only its line number. Use it when the pipeline says Incorrect password, or a .rar will not unpack."""
    import re
    import subprocess
    file = Path(path)
    if not file.exists():
        return f"{path} does not exist."
    unrar = r"C:\Program Files\WinRAR\UnRAR.exe"
    lines = [line.strip() for line in (HERE.parent / "archive-passwords.txt").read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()] if (HERE.parent / "archive-passwords.txt").exists() else []
    tries = [("no password", "-p-")] + [(f"password line {i}", "-p" + p) for i, p in enumerate(lines, 1)]
    name = scrub_text(file.name)
    for label, flag in tries:
        test = subprocess.run([unrar, "t", flag, str(file)], capture_output=True, text=True, encoding="utf-8", errors="replace")
        out = test.stdout + test.stderr
        if "Incorrect password" in out or "password is incorrect" in out:
            continue
        good = len(re.findall(r" OK\s*$", out, re.M))
        bad = [scrub_text(m) for m in re.findall(r"^(.*?) - checksum error", out, re.M)]
        if test.returncode == 0:
            return f"{name}: {label} opens it and all {good} files test OK."
        cut = "Unexpected end of archive" in out
        reason = "the file is cut short, so the download stopped early and it needs downloading again" if cut else "it is damaged"
        return f"{name}: {label} opens it. {good} files test OK, " + (f"{', '.join(bad)} fails its checksum, and " if bad else "") + f"{reason}."
    return f"{name}: none of the {len(tries)} tries opens it (no password, then each line of archive-passwords.txt)."


def shader_throw_sites(words=""):
    """The project's published list of places in the shader recompiler that throw unsupported or not implemented, counted by area. words narrows it to sites whose message holds any of the words. These are counted from source code, not from games, so a site only matters if a game's log shows its message."""
    import collections
    import json
    import urllib.request
    with urllib.request.urlopen("https://boykopovar.github.io/AnyPS5/progress.json", timeout=60) as reply:
        data = json.load(reply)
    sites = data["shader_source"]["throw_sites"]
    areas = collections.Counter(site["file"].split("/")[0] for site in sites)
    rows = [f"{len(sites)} unsupported throw sites: " + ", ".join(f"{name} {count}" for name, count in areas.most_common())]
    wanted = [word.lower() for word in str(words).split()]
    for site in sites:
        if wanted and any(word in site["message"].lower() for word in wanted):
            rows.append(f"{site['file']}:{site['line']}: {site['message'][:160]}")
    return chr(10).join(rows)


def _fps():
    """The FPS optimizer's own functions (skills/fps_optimize.py), loaded once, so these tools and the pipeline measure the same way."""
    global _FPS
    if "_FPS" not in globals():
        import importlib.util
        spec = importlib.util.spec_from_file_location("fps_optimize", Path(__file__).parent / "skills" / "fps_optimize.py")
        _FPS = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_FPS)
    return _FPS


def fps_game_settings(folder):
    """What decides a game's FPS on the Windows side, read only: game-env.txt, the game's settings files that name fps or vsync, and its last measured run: FPS, how much of each frame waits for the display (at its limit or not), resolution."""
    fo, folder = _fps(), Path(folder)
    rows = [f"{folder.name}", "game-env.txt: " + (", ".join(f"{k}={v}" for k, v in fo.read_env(folder / "game-env.txt").items()) or "none")]
    found = fo.config_files(folder)
    rows += ["Settings files in app0 that name frame rate or vsync (change them only in a test copy):"] + [f"  {c}" for c in found] if found else ["No settings file in app0 names frame rate or vsync."]
    summary = folder / "aps5-summary.jsonl"
    if summary.exists():
        m = fo.measure(summary, 96)
        if m.get("fps"):
            cap, share = fo.cap_of([m])
            rows.append(f"Last run: {m['fps']} FPS, frame p50 {m['p50_ms']} ms, {m.get('resolution')}, waits for the display {share * 100:.0f} % of each frame. "
                        + (f"At its limit ({cap} FPS): no setting can raise it." if cap else "Not at a display limit: settings can help, fps_optimize measures them."))
    else:
        rows.append("No measured run yet (no aps5-summary.jsonl).")
    rows.append("Also check in the game itself: a performance or frame-rate mode (Blu found it helps). NVIDIA Control Panel is Blu's to change, never ours.")
    return chr(10).join(rows)


def fps_candidates(folder):
    """The changes fps_optimize would test on this game, with their settings, and what is only reported."""
    fo, folder = _fps(), Path(folder)
    rows = [f"Candidates for {folder.name}:"]
    for name, env, why in fo.CANDIDATES + fo.TRADEOFFS:
        if name == "mimalloc" and not (folder / "libs" / "mimalloc.dll").exists():
            rows.append(f"  {name}: skipped, libs\\mimalloc.dll is missing")
            continue
        rows.append(f"  {name}: {' '.join(f'{k}={v}' for k, v in env.items())} - {why}" + (" (picture changes: never kept)" if (name, env, why) in fo.TRADEOFFS else ""))
    test = fo.GAMES / "_test" / folder.name
    rows.append(f"  build:test-copy: the build of {test}" if (test / "game.json").exists() else "  build:test-copy: none, the game has no _test copy")
    rows += ["Only reported:"] + [f"  {t}" for t in fo.REPORT_ONLY]
    return chr(10).join(rows)


def fps_test_copy(folder):
    """Makes (or reuses) the FPS test copy in _test\\_fps: hard links for app0, libs, mods, imports and the program; the state folders are kept aside and put back before every run. Never write a library into it."""
    fo, folder = _fps(), Path(folder)
    try:
        copy = fo.make_copy(folder, fo.COPIES / folder.name)
    except (fo.NotRunnable, OSError) as e:
        return f"No test copy: {e}"
    state = sorted(p.name for p in (copy / "_fps_state").iterdir()) if (copy / "_fps_state").exists() else []
    return f"Test copy: {copy}\nLinked: {', '.join(sorted(p.name for p in copy.iterdir() if p.name in fo.LINKED))}\nState put back before each run: {', '.join(state) or 'none'}"


def fps_compare(folder):
    """The numbers fps_optimize kept for a game: base and each setting (median, runs, range, frame p50) and the verdict, from _reports\\fps\\<game>\\runs.jsonl for the game's current build."""
    fo, folder = _fps(), Path(folder)
    runs = fo.load_runs(fo.FPS_DIR / folder.name / "runs.jsonl", fo.fingerprint(folder))
    if not runs:
        return f"No FPS runs for {folder.name} on its current build. Start fps_optimize."
    base = fo.counted(runs, "base")
    rows = [f"{folder.name}: base {fo.fmt(base)}"]
    if len(base) >= 2:
        cap, share = fo.cap_of(base)
        if cap:
            rows.append(f"At its limit: {cap} FPS, waits for the display {share * 100:.0f} % of each frame.")
    for name in dict.fromkeys(r["label"] for r in runs if r["label"] != "base"):
        verdict, why = fo.judge(base, fo.counted(runs, name), fo.ARGS.runs, fo.failed(runs, name))
        rows.append(f"{name}: {fo.fmt(fo.counted(runs, name))} -> {verdict} {why}")
    rows.append(f"Runs: {len(runs)} ({sum(1 for r in runs if r.get('warm'))} warm-up, {sum(1 for r in runs if r.get('stopped'))} stopped early). A gain counts only as 'faster'.")
    return chr(10).join(rows)


def fps_keep_setting(folder, candidate):
    """Puts a setting into the game's game-env.txt, only when fps_compare says 'faster' for it on the current build (every run beat every base run). The old game-env.txt is kept next to it."""
    fo, folder = _fps(), Path(folder)
    runs = fo.load_runs(fo.FPS_DIR / folder.name / "runs.jsonl", fo.fingerprint(folder))
    base, mine = fo.counted(runs, "base"), fo.counted(runs, candidate)
    verdict, why = fo.judge(base, mine, fo.ARGS.runs, fo.failed(runs, candidate))
    if verdict != "faster":
        return f"Not kept: {candidate} is '{verdict}' {why}. Only a measured, repeatable gain goes into game-env.txt."
    env = mine[0]["env"]
    if not env:
        return f"{candidate} is not a setting (a build); rebuild the game on that build instead."
    if (folder / "game-env.txt").exists():
        shutil.copy2(folder / "game-env.txt", folder / f"game-env.txt.before-fps-{time.strftime('%m%d-%H%M')}")
    said = ""
    for k, v in env.items():
        said = set_game_setting(folder, k, v)
    return f"Kept {candidate} ({why}). {said}"
