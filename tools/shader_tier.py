"""Shader cache SSD tier (11-16, Blu: "Compiled shaders loading up ... if it takes just that long, I can only imagine on
bigger games"). AnyPS5 reads one shader_cache\\<build>\\<key>.bin per shader the first time the game asks for it
(core/shader/recompiler/ShaderDiskCache.cpp, path = <exe folder>\\shader_cache unless ANYPS5_SHADER_CACHE_DIR). On the
D: USB disk those thousands of small random reads stall start-up (Luna Abyss: 11.5k files, 5 GB). This keeps the caches
of the most recently played games on the C: SSD in C:\\AnyPS5-work\\shader-hot\\<game> and leaves a directory junction
at <game>\\shader_cache, so AnyPS5 itself is unchanged. Least recently played games go back to D: when room is needed.

  shader_tier.py status                      what is hot, sizes, budget, C: free
  shader_tier.py play --game <dir>           Play button hook (game-guard.ps1 -Force): records the play, promotes the
                                             game now if it fits and the copy takes under --max-seconds (default 60)
  shader_tier.py prewarm                     scheduled task: when idle 10 min, no game running, no pause.flag and the
                                             PC lock free, promotes recently played games; keeps C: above the floor
  shader_tier.py promote|demote --game <dir> by hand
  shader_tier.py restore-all                 undo: every cache back to D:, junctions removed
  shader_tier.py check                       repairs dangling junctions and finishes half-done moves
  shader_tier.py purge-standby               drops the Windows file cache (elevated; benchmarks only, no game running)
  shader_tier_bench.ps1 -GameDir <dir>       time to first present with the cache on D: and on C: (PC lock)

Budget 12 GB of hot caches, never leaving C: under 8 GB free (--budget-gb, --min-free-gb). A cache is never moved
while any process runs from that game's folder. Copies go to a staging folder first; the junction swap happens only
after the copy and a delta copy finished, so an interrupted move leaves the original in place.
While a game is hot its old copy stays in <drive>:\\AnyPS5 Games\\_shader-tier\\old\\<game> on the drive that really
holds the game (games moved to G: keep a junction at their D: path), so moving it back is a delta copy and a lost C:
folder falls back to it (check). When that drive drops under 40 GB free only the parts C: also holds are removed.
Leftover copies are merged (files the live cache lacks) before they are removed, never just deleted. Games already on
C: are skipped. "A game is running" means any process under <drive>:\\AnyPS5 Games\\<game>\\ (real or junction path),
and prewarm also waits for Steam, Epic and Riot games, like the PC lock (gpu_flag.ps1).
Log: D:\\AnyPS5 Games\\_reports\\shader-tier.log. State: C:\\AnyPS5-work\\shader-hot\\tier-state.json.
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

GAMES = Path(r"D:\AnyPS5 Games")
TOOLS = Path(r"C:\AnyPS5 Games\_tools")
HOT = Path(r"C:\AnyPS5-work\shader-hot")
D_STAGE = GAMES / "_shader-tier"
STATE = HOT / "tier-state.json"
LOCK = HOT / "tier.lock"
ABORT = HOT / "tier.abort"
LOG = GAMES / "_reports" / "shader-tier.log"
PLAYS_LOG = TOOLS / "live" / "anyps5-app-plays.log"
APP_GAME = TOOLS / "live" / "anyps5-app-game.txt"
GPU_FLAG = TOOLS / "live" / "gpu-busy.flag"
PAUSE_FLAG = TOOLS / "pause.flag"
LANES = TOOLS / "live" / "lanes"
GB = 1 << 30
DEFAULT_RATE = 5.0
MIRROR_FLOOR = 40 * GB

k32 = ctypes.WinDLL("kernel32", use_last_error=True)


def log(text):
    line = time.strftime("%m-%d %H:%M:%S ") + text
    print(line, flush=True)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def load_state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"games": {}, "rateMBps": DEFAULT_RATE}


def save_state(state):
    HOT.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    os.replace(tmp, STATE)


def norm(p):
    s = str(p)
    for prefix in ("\\\\?\\", "\\??\\"):
        if s.startswith(prefix):
            s = s[len(prefix):]
    return os.path.normcase(os.path.normpath(s))


def is_junction(p):
    try:
        return os.path.isjunction(p)
    except OSError:
        return False


def junction_target(p):
    try:
        return Path(norm(os.readlink(p)))
    except OSError:
        return None


def make_junction(link, target):
    try:
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    except (ImportError, AttributeError, OSError):
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
        if r.returncode != 0:
            raise OSError(f"mklink failed: {r.stdout} {r.stderr}")


def tree_size(p):
    total = 0
    stack = [str(p)]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if not is_junction(e.path):
                                stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        pass
        except OSError:
            pass
    return total


def newest_version(sc):
    best, when = None, -1.0
    try:
        for e in os.scandir(sc):
            if e.is_dir(follow_symlinks=False) and len(e.name) == 16 and all(c in "0123456789abcdef" for c in e.name):
                t = e.stat().st_mtime
                try:
                    t = max(t, os.stat(os.path.join(e.path, "last-used")).st_mtime)
                except OSError:
                    pass
                if t > when:
                    best, when = e.name, t
    except OSError:
        pass
    return best


def live_size(sc):
    v = newest_version(sc)
    return tree_size(Path(sc) / v) if v else tree_size(sc)


def copy_live(src, dst, abort_check):
    dst.mkdir(parents=True, exist_ok=True)
    for e in os.scandir(src):
        if e.is_file(follow_symlinks=False) and ".tmp." not in e.name:
            shutil.copy2(e.path, dst / e.name)
    v = newest_version(src)
    if not v:
        return True, None
    return robocopy(Path(src) / v, dst / v, abort_check)


def free_c():
    return shutil.disk_usage("C:\\").free


def game_dirs():
    out = []
    try:
        for e in os.scandir(GAMES):
            if e.is_dir() and not e.name.startswith("_") and os.path.exists(os.path.join(e.path, "game.json")):
                out.append(Path(e.path))
    except OSError:
        pass
    return out


def game_of(arg):
    p = Path(arg)
    if not p.is_absolute():
        p = GAMES / arg
    p = Path(str(p).rstrip("\\/"))
    if norm(p.parent) != norm(GAMES):
        raise SystemExit(f"not a folder in {GAMES}: {p}")
    return p


def running_images():
    arr = (wt.DWORD * 8192)()
    needed = wt.DWORD()
    ctypes.WinDLL("psapi").EnumProcesses(ctypes.byref(arr), ctypes.sizeof(arr), ctypes.byref(needed))
    out = []
    k32.OpenProcess.restype = wt.HANDLE
    for pid in arr[: needed.value // ctypes.sizeof(wt.DWORD)]:
        h = k32.OpenProcess(0x1000, False, pid)
        if not h:
            continue
        try:
            code = wt.DWORD()
            if k32.GetExitCodeProcess(h, ctypes.byref(code)) and code.value != 259:
                continue
            buf = ctypes.create_unicode_buffer(1024)
            size = wt.DWORD(1024)
            if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                out.append((int(pid), buf.value))
        finally:
            k32.CloseHandle(h)
    return out


def lane_pids():
    pids = set()
    for f in LANES.glob("*.json") if LANES.exists() else []:
        try:
            pids.add(int(json.loads(f.read_text(encoding="utf-8"))["pid"]))
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return pids


def game_processes(images=None):
    out = []
    for pid, image in images if images is not None else running_images():
        n = norm(image)
        parts = n.split(os.sep)
        if len(parts) < 4 or parts[1] != "anyps5 games" or "_tools" in parts[2:-1]:
            continue
        if parts[-1].startswith("anyps5"):
            continue
        out.append((pid, n))
    return out


OTHER_GAMES = ((os.sep + "steamapps" + os.sep + "common" + os.sep, ("steam.exe", "steamwebhelper.exe")),
               (os.sep + "epic games" + os.sep, ()),
               (os.sep + "riot games" + os.sep, ()))


def any_game_running():
    images = running_images()
    if game_processes(images):
        return True
    for _, image in images:
        n = norm(image)
        for marker, skip in OTHER_GAMES:
            if marker in n and os.path.basename(n) not in skip and (os.sep + "launcher" + os.sep) not in n and "crash" not in os.path.basename(n):
                return True
    return False


def real_dir(game):
    return Path(os.path.realpath(game))


def mirror_of(game):
    return real_dir(game).parent / "_shader-tier" / "old" / game.name


def on_ssd(game):
    return norm(real_dir(game)).startswith(norm(HOT.anchor))


def users_of(game):
    bases = {norm(game) + os.sep, norm(real_dir(game)) + os.sep}
    return [pid for pid, n in game_processes() if any(n.startswith(b) for b in bases)]


def idle_ms():
    class LII(ctypes.Structure):
        _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]
    lii = LII(ctypes.sizeof(LII), 0)
    if not ctypes.WinDLL("user32").GetLastInputInfo(ctypes.byref(lii)):
        return 0
    return (k32.GetTickCount() - lii.dwTime) & 0xFFFFFFFF


def pc_lock_held():
    try:
        pid = int(GPU_FLAG.read_text(encoding="utf-8").split()[0])
    except (OSError, ValueError, IndexError):
        return False
    if time.time() - GPU_FLAG.stat().st_mtime > 20 * 60:
        return False
    return any(p == pid for p, _ in running_images())


def take_lock(wait_s):
    HOT.mkdir(parents=True, exist_ok=True)
    end = time.time() + wait_s
    while True:
        try:
            fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            try:
                owner = int(LOCK.read_text().strip() or 0)
            except (OSError, ValueError):
                owner = 0
            if owner and not any(p == owner for p, _ in running_images()):
                LOCK.unlink(missing_ok=True)
                continue
            if time.time() >= end:
                return False
            time.sleep(1)


def drop_lock():
    try:
        if LOCK.read_text().strip() == str(os.getpid()):
            LOCK.unlink()
    except OSError:
        pass


def robocopy(src, dst, abort_check, only_missing=False):
    cmd = ["robocopy", str(src), str(dst), "/E", "/COPY:DAT", "/DCOPY:DAT", "/R:1", "/W:1", "/MT:8",
           "/NFL", "/NDL", "/NJH", "/NJS", "/NP", "/XF", "*.tmp.*"] + (["/XC", "/XN", "/XO"] if only_missing else [])
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)
    while True:
        try:
            code = p.wait(timeout=3)
            return code < 8, None
        except subprocess.TimeoutExpired:
            why = abort_check() if abort_check else None
            if why:
                p.kill()
                p.wait()
                return False, why


def rmtree_real(p):
    p = Path(p)
    if not p.exists() and not is_junction(p):
        return
    if is_junction(p) or p.is_symlink():
        raise OSError(f"refusing to delete through a link: {p}")
    shutil.rmtree(p, ignore_errors=True)


def fold_into(src, dst, what):
    ok, _ = robocopy(src, dst, None, only_missing=True)
    if not ok:
        log(f"{dst.parent.name}: could not merge {what} {src} into {dst}; both kept")
        return False
    rmtree_real(src)
    if src.exists():
        log(f"{dst.parent.name}: {what} {src} merged but not fully removed")
        return False
    log(f"{dst.parent.name}: {what} {src} merged into {dst} (only files it lacked) and removed")
    return True


def prune_mirror(old, hot):
    removed = 0
    for e in list(os.scandir(old)):
        if not (hot / e.name).exists():
            continue
        if e.is_dir(follow_symlinks=False):
            rmtree_real(e.path)
        else:
            os.remove(e.path)
        removed += 1
    if not any(os.scandir(old)):
        os.rmdir(old)
    return removed


def hot_dir(game):
    return HOT / game.name


def is_hot(game):
    sc = game / "shader_cache"
    if not is_junction(sc):
        return False
    t = junction_target(sc)
    return t is not None and norm(t) == norm(hot_dir(game)) and t.is_dir()


def promote(game, state, abort_check=None):
    sc = game / "shader_cache"
    if is_hot(game):
        return True
    if is_junction(sc):
        log(f"{game.name}: shader_cache is a junction somewhere else ({junction_target(sc)}); left alone")
        return False
    if on_ssd(game):
        log(f"{game.name}: already on the SSD ({real_dir(game)}); not promoted")
        return False
    if not sc.is_dir():
        sc.mkdir(parents=True, exist_ok=True)
    if users_of(game):
        log(f"{game.name}: not promoted, the game is running")
        return False
    hot = hot_dir(game)
    old = mirror_of(game)
    for leftover, what in ((old, "earlier D: copy"), (hot, "unlinked C: copy")):
        if leftover.is_dir() and not fold_into(leftover, sc, what):
            return False
    stage = HOT / "_incoming" / game.name
    rmtree_real(stage)
    stage.parent.mkdir(parents=True, exist_ok=True)
    size = live_size(sc)
    start = time.time()
    ok, why = copy_live(sc, stage, abort_check)
    if not ok:
        rmtree_real(stage)
        log(f"{game.name}: promotion stopped ({why or 'robocopy failed'}); cache stays on D:")
        return False
    secs = max(time.time() - start, 0.5)
    if size > 64 << 20:
        rate = size / (1 << 20) / secs
        state["rateMBps"] = round(0.5 * state.get("rateMBps", DEFAULT_RATE) + 0.5 * rate, 1)
    if users_of(game):
        rmtree_real(stage)
        log(f"{game.name}: promotion stopped, the game started during the copy")
        return False
    copy_live(sc, stage, None)
    why = abort_check() if abort_check else None
    if why or users_of(game):
        rmtree_real(stage)
        log(f"{game.name}: promotion stopped before the swap ({why or 'the game started'}); cache stays where it was")
        return False
    old.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(sc, old)
    except OSError as e:
        rmtree_real(stage)
        log(f"{game.name}: promotion stopped, shader_cache is in use ({e})")
        return False
    try:
        os.rename(stage, hot)
        make_junction(sc, hot)
    except OSError as e:
        if is_junction(sc):
            os.rmdir(sc)
        if not sc.exists():
            os.rename(old, sc)
        log(f"{game.name}: promotion failed ({e}); original put back")
        return False
    entry = state["games"].setdefault(game.name, {})
    entry.update({"hot": True, "bytes": size, "promoted": time.time()})
    log(f"{game.name}: promoted {size / GB:.2f} GB to C: in {secs:.0f} s ({size / (1 << 20) / secs:.0f} MB/s); the D: copy stays in {old} so moving it back is quick")
    return True


def demote(game, state, abort_check=None):
    sc = game / "shader_cache"
    entry = state["games"].setdefault(game.name, {})
    hot = hot_dir(game)
    if not is_junction(sc):
        entry["hot"] = False
        return True
    target = junction_target(sc)
    if target is None or norm(target) != norm(hot):
        log(f"{game.name}: shader_cache points at {target}, not the tier; left alone")
        return False
    if users_of(game):
        log(f"{game.name}: not moved back, the game is running")
        return False
    mirror = mirror_of(game)
    mirror.parent.mkdir(parents=True, exist_ok=True)
    start = time.time()
    ok, why = robocopy(hot, mirror, abort_check) if hot.is_dir() else (True, None)
    if not ok or users_of(game):
        log(f"{game.name}: move back stopped ({why or 'game started or robocopy failed'}); stays on C:")
        return False
    if hot.is_dir():
        robocopy(hot, mirror, None)
    try:
        os.rmdir(sc)
        os.rename(mirror, sc)
    except OSError as e:
        if not sc.exists() and not is_junction(sc):
            make_junction(sc, hot)
        log(f"{game.name}: move back failed ({e}); junction kept")
        return False
    rmtree_real(hot)
    entry["hot"] = False
    log(f"{game.name}: moved back to D: in {time.time() - start:.0f} s")
    return True


def hot_games():
    return [g for g in game_dirs() if is_hot(g)]


def hot_total():
    total = 0
    for g in hot_games():
        total += tree_size(hot_dir(g))
    return total


def sync_plays(state):
    games = state["games"]
    try:
        for line in PLAYS_LOG.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split("\t")
            if len(parts) >= 5:
                try:
                    t = time.mktime(time.strptime(parts[0], "%Y-%m-%d %H:%M:%S"))
                except ValueError:
                    continue
                name = Path(parts[4].strip()).name
                e = games.setdefault(name, {})
                e["lastPlayed"] = max(e.get("lastPlayed", 0), t)
    except OSError:
        pass
    try:
        pid = int(APP_GAME.read_text(encoding="utf-8").split()[0])
    except (OSError, ValueError, IndexError):
        pid = 0
    lanes = lane_pids()
    active = idle_ms() < 120000
    for p, image in game_processes():
        if p in lanes:
            continue
        if p == pid or active:
            name = Path(image).parent.name
            if (GAMES / name / "game.json").exists():
                games.setdefault(name, {})["lastPlayed"] = time.time()


def room(state, budget, floor, exclude_bytes=0):
    used = hot_total() - exclude_bytes
    return min(budget - used, free_c() - floor)


def make_room(state, need, budget, floor, newer_than, abort_check):
    games = state["games"]
    hot = sorted(hot_games(), key=lambda g: games.get(g.name, {}).get("lastPlayed", 0))
    for g in hot:
        if room(state, budget, floor) >= need:
            break
        if games.get(g.name, {}).get("lastPlayed", 0) >= newer_than or users_of(g):
            continue
        demote(g, state, abort_check)
    return room(state, budget, floor) >= need


def cmd_play(args, state):
    game = game_of(args.game)
    e = state["games"].setdefault(game.name, {})
    e["lastPlayed"] = time.time()
    if is_hot(game) or on_ssd(game):
        log(f"{game.name}: play, cache already on C:")
        return
    deadline = time.time() + args.max_seconds + 20

    def play_abort():
        return "the game is about to start (Play button deadline)" if time.time() > deadline else None
    if not take_lock(0):
        ABORT.write_text("play")
        if not take_lock(20):
            log(f"{game.name}: play, a tier move is busy; starting from D:")
            return
    ABORT.unlink(missing_ok=True)
    try:
        sc = game / "shader_cache"
        if not sc.is_dir():
            return
        size = live_size(sc)
        e["bytes"] = size
        rate = state.get("rateMBps", DEFAULT_RATE)
        estimate = size / (1 << 20) / max(rate, 1)
        if estimate > args.max_seconds:
            log(f"{game.name}: play, {size / GB:.2f} GB would take ~{estimate:.0f} s to move at {rate:.0f} MB/s; the idle prewarm moves it after this session")
            return
        if room(state, args.budget, args.floor) < size:
            if not make_room(state, size, args.budget, args.floor, e["lastPlayed"], play_abort):
                log(f"{game.name}: play, no room on C: for {size / GB:.2f} GB")
                return
        promote(game, state, play_abort)
    finally:
        drop_lock()


def prewarm_abort():
    if ABORT.exists():
        return "the Play button needs the tier"
    if any_game_running():
        return "a game started"
    if PAUSE_FLAG.exists():
        return "pause.flag"
    return None


def cmd_prewarm(args, state):
    sync_plays(state)
    save_state(state)
    if PAUSE_FLAG.exists():
        return
    if any_game_running():
        return
    if not args.now and (idle_ms() < 600000 or pc_lock_held()):
        return
    if not take_lock(0):
        return
    ABORT.unlink(missing_ok=True)
    try:
        cmd_check(args, state, quiet=True)
        games = state["games"]
        for g in sorted(hot_games(), key=lambda g: games.get(g.name, {}).get("lastPlayed", 0)):
            if free_c() >= args.floor:
                break
            if not users_of(g):
                log(f"C: is under {args.floor / GB:.0f} GB free; moving {g.name} back")
                demote(g, state, prewarm_abort)
        while hot_total() > args.budget:
            hot = sorted(hot_games(), key=lambda g: games.get(g.name, {}).get("lastPlayed", 0))
            if not hot or not demote(hot[0], state, prewarm_abort):
                break
        cutoff = time.time() - args.days * 86400
        wanted = sorted((n for n, e in games.items() if e.get("lastPlayed", 0) >= cutoff), key=lambda n: -games[n]["lastPlayed"])
        for name in wanted:
            game = GAMES / name
            if not (game / "game.json").exists() or is_hot(game) or on_ssd(game) or not (game / "shader_cache").is_dir():
                continue
            if prewarm_abort():
                break
            size = live_size(game / "shader_cache")
            games[name]["bytes"] = size
            if size < args.min_mb << 20:
                continue
            if size > args.budget:
                log(f"{name}: {size / GB:.1f} GB is larger than the whole budget; stays on D:")
                continue
            if room(state, args.budget, args.floor) < size and not make_room(state, size, args.budget, args.floor, games[name]["lastPlayed"], prewarm_abort):
                continue
            promote(game, state, prewarm_abort)
            save_state(state)
    finally:
        save_state(state)
        drop_lock()


def cmd_check(args, state, quiet=False):
    for g in game_dirs():
        sc = g / "shader_cache"
        old = mirror_of(g)
        if is_junction(sc):
            t = junction_target(sc)
            if t is None or not t.is_dir():
                if users_of(g):
                    continue
                os.rmdir(sc)
                if old.is_dir():
                    os.rename(old, sc)
                    log(f"{g.name}: junction pointed at a missing folder; original D: cache put back")
                else:
                    sc.mkdir()
                    log(f"{g.name}: junction pointed at a missing folder; empty cache folder made")
                state["games"].setdefault(g.name, {})["hot"] = False
            elif old.is_dir() and shutil.disk_usage(str(old)).free < MIRROR_FLOOR and not users_of(g):
                n = prune_mirror(old, t)
                if n:
                    log(f"{g.name}: {old.anchor} under {MIRROR_FLOOR / GB:.0f} GB free; removed {n} items of its mirror that C: also holds (older builds kept)")
        elif old.is_dir() and not users_of(g):
            if not sc.exists():
                os.rename(old, sc)
                log(f"{g.name}: half-done move found; original cache put back")
            else:
                fold_into(old, sc, "leftover mirror")
    for d in (HOT / "_incoming", D_STAGE / "incoming"):
        if d.is_dir() and not game_processes():
            for e in d.iterdir():
                rmtree_real(e)
    if HOT.is_dir():
        names = {g.name for g in game_dirs() if is_hot(g)}
        for e in HOT.iterdir():
            if e.is_dir() and not e.name.startswith("_") and e.name not in names:
                game = GAMES / e.name
                if (game / "shader_cache").exists() or not (game / "game.json").exists():
                    log(f"{e.name}: hot folder no game links to; left in place for a look ({e})")
    if not quiet:
        print("check done")


def purge_standby():
    if game_processes():
        raise SystemExit("a game is running; not dropping the file cache under it")
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)

    class LUID(ctypes.Structure):
        _fields_ = [("Low", wt.DWORD), ("High", wt.LONG)]

    class TP(ctypes.Structure):
        _fields_ = [("Count", wt.DWORD), ("Luid", LUID), ("Attr", wt.DWORD)]
    k32.GetCurrentProcess.restype = wt.HANDLE
    k32.CloseHandle.argtypes = [wt.HANDLE]
    advapi.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]
    advapi.AdjustTokenPrivileges.argtypes = [wt.HANDLE, wt.BOOL, ctypes.c_void_p, wt.DWORD, ctypes.c_void_p, ctypes.c_void_p]
    token = wt.HANDLE()
    if not advapi.OpenProcessToken(k32.GetCurrentProcess(), 0x28, ctypes.byref(token)):
        raise SystemExit("cannot open the process token")
    luid = LUID()
    advapi.LookupPrivilegeValueW(None, "SeProfileSingleProcessPrivilege", ctypes.byref(luid))
    tp = TP(1, luid, 2)
    advapi.AdjustTokenPrivileges(token, False, ctypes.byref(tp), 0, None, None)
    k32.CloseHandle(token)
    command = ctypes.c_int(4)
    status = ctypes.WinDLL("ntdll").NtSetSystemInformation(80, ctypes.byref(command), ctypes.sizeof(command)) & 0xFFFFFFFF
    if status != 0:
        raise SystemExit(f"purging the standby list failed (NTSTATUS 0x{status:08X}; needs an elevated shell)")
    print("standby list purged")


def stamp(path):
    start = time.perf_counter()
    with open(path, "w", encoding="utf-8", errors="replace") as out:
        for raw in sys.stdin.buffer:
            out.write(f"{time.perf_counter() - start:9.3f} {raw.decode('utf-8', 'replace').rstrip()}\n")
            out.flush()


def cmd_status(args, state):
    games = state["games"]
    print(f"C: free {free_c() / GB:.1f} GB (floor {args.floor / GB:.0f}), hot {hot_total() / GB:.2f} of {args.budget / GB:.0f} GB, copy rate {state.get('rateMBps', DEFAULT_RATE):.0f} MB/s")
    rows = []
    for g in game_dirs():
        e = games.get(g.name, {})
        hot = is_hot(g)
        if hot or e.get("lastPlayed"):
            rows.append((e.get("lastPlayed", 0), g.name, "C:" if hot else real_dir(g).drive, tree_size(hot_dir(g)) if hot else e.get("bytes", 0)))
    for t, name, where, size in sorted(rows, reverse=True):
        when = time.strftime("%m-%d %H:%M", time.localtime(t)) if t else "-"
        print(f"  {where} {size / GB:6.2f} GB  played {when}  {name}")


def main():
    ap = argparse.ArgumentParser(description="Shader cache SSD tier")
    ap.add_argument("command", choices=["status", "play", "prewarm", "promote", "demote", "restore-all", "check", "played", "purge-standby", "stamp"])
    ap.add_argument("--game")
    ap.add_argument("--budget-gb", type=float, default=12)
    ap.add_argument("--min-free-gb", type=float, default=8)
    ap.add_argument("--max-seconds", type=float, default=60)
    ap.add_argument("--days", type=float, default=14)
    ap.add_argument("--min-mb", type=int, default=64)
    ap.add_argument("--now", action="store_true", help="prewarm without waiting for 10 idle minutes")
    args = ap.parse_args()
    args.budget = int(args.budget_gb * GB)
    args.floor = int(args.min_free_gb * GB)
    state = load_state()
    state.setdefault("games", {})
    try:
        if args.command == "status":
            cmd_status(args, state)
        elif args.command == "play":
            cmd_play(args, state)
        elif args.command == "prewarm":
            cmd_prewarm(args, state)
            return
        elif args.command == "check":
            cmd_check(args, state)
        elif args.command == "purge-standby":
            purge_standby()
        elif args.command == "stamp":
            stamp(args.game)
            return
        elif args.command == "played":
            state["games"].setdefault(game_of(args.game).name, {})["lastPlayed"] = time.time()
        elif args.command in ("promote", "demote"):
            game = game_of(args.game)
            if not take_lock(30):
                raise SystemExit("another tier move is running")
            try:
                if args.command == "promote":
                    size = live_size(game / "shader_cache")
                    if room(state, args.budget, args.floor) < size and not make_room(state, size, args.budget, args.floor, time.time(), None):
                        raise SystemExit(f"no room on C: for {size / GB:.2f} GB")
                    ok = promote(game, state)
                else:
                    ok = demote(game, state)
            finally:
                drop_lock()
            save_state(state)
            sys.exit(0 if ok else 1)
        elif args.command == "restore-all":
            if not take_lock(30):
                raise SystemExit("another tier move is running")
            try:
                for g in hot_games():
                    demote(g, state)
                cmd_check(args, state, quiet=True)
            finally:
                drop_lock()
    finally:
        if args.command not in ("prewarm", "stamp", "purge-standby", "status"):
            save_state(state)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        log(f"error: {type(e).__name__}: {e}")
        sys.exit(0 if "play" in sys.argv[1:2] else 1)
