import json
import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

ELF = b"\x7fELF"
SKIP_EXES = ("run.sh",)
CATALOG_WAIT = 4.0


def _is_elf_exe(p):
    try:
        if not p.is_file() or not os.access(str(p), os.X_OK):
            return False
        with open(p, "rb") as f:
            return f.read(4) == ELF
    except OSError:
        return False


def _read_json(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def find_exe(folder):
    folder = Path(folder)
    meta = _read_json(folder / "game.json") or {}
    named = meta.get("exe") if isinstance(meta, dict) else None
    if isinstance(named, str) and named:
        p = folder / os.path.basename(named)
        if _is_elf_exe(p):
            return p
    cands = [p for p in sorted(folder.iterdir()) if p.name not in SKIP_EXES and not p.name.startswith(("anyps5", ".")) and _is_elf_exe(p)]
    if not cands:
        return None
    base = folder.name.lower().split("-")[0]
    cands.sort(key=lambda p: (p.name.lower() != folder.name.lower(), p.name.lower() != base, p.name.endswith(".part"), -p.stat().st_size))
    return cands[0]


def title_info(folder):
    folder = Path(folder)
    meta = _read_json(folder / "game.json")
    meta = meta if isinstance(meta, dict) else {}
    title, title_id = meta.get("title"), meta.get("titleId")
    param = _read_json(folder / "app0" / "sce_sys" / "param.json")
    if isinstance(param, dict):
        title_id = title_id or param.get("titleId")
        loc = param.get("localizedParameters")
        if not title and isinstance(loc, dict):
            lang = loc.get("defaultLanguage")
            entry = loc.get(lang) if isinstance(lang, str) else None
            if not isinstance(entry, dict):
                entry = next((v for v in loc.values() if isinstance(v, dict) and v.get("titleName")), None)
            if isinstance(entry, dict):
                title = entry.get("titleName")
    return (title if isinstance(title, str) and title else folder.name), (title_id if isinstance(title_id, str) and title_id else folder.name)


def collection_games(console_dir, fetch=None):
    cache = Path(console_dir) / "collection-cache.json"
    games = None
    if fetch is None:
        def fetch():
            from areas import collection
            return collection.list_games({}).get("games")
    box = {}

    def run():
        try:
            box["games"] = fetch()
        except Exception:
            box["games"] = None

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(CATALOG_WAIT)
    games = box.get("games")
    if isinstance(games, list) and games:
        try:
            cache.write_text(json.dumps(games), encoding="utf-8")
        except OSError:
            pass
    else:
        games = _read_json(cache)
    out = []
    for g in games if isinstance(games, list) else []:
        if not isinstance(g, dict) or not isinstance(g.get("titleId"), str):
            continue
        url = g.get("icon") if isinstance(g.get("icon"), str) and g.get("icon").startswith("http") else ""
        local = {}
        for key in ("icon", "bg"):
            rel = "art/%s/%s.png" % (g["titleId"], key)
            local[key] = rel if (Path(console_dir) / rel).is_file() else ""
        out.append({"id": g["titleId"], "title": g.get("title") or g["titleId"], "icon": local["icon"], "bg": local["bg"], "shot": "",
                    "status": "Not installed", "fps": None, "lastRun": "", "folder": "", "exe": "", "saves": 0, "installed": False,
                    "iconUrl": url})
    out.sort(key=lambda e: e["title"].lower())
    return out


ART_LOCK = threading.Lock()
ART_TIMEOUT = 20
ART_MAX = 8 * 1024 * 1024


def _http_get(url):
    import urllib.request
    with urllib.request.urlopen(url, timeout=ART_TIMEOUT) as r:
        return r.read(ART_MAX + 1)


def fetch_art(console_dir, entries, done=None, get=None):
    get = get or _http_get
    if not ART_LOCK.acquire(False):
        return 0
    got = 0
    try:
        for e in entries:
            url = e.get("iconUrl") or ""
            if not url.endswith("/icon0.png"):
                continue
            base = url[:-len("icon0.png")]
            folder = Path(console_dir) / "art" / e["id"]
            for key, names in (("icon", ("icon0.png",)), ("bg", ("pic1.png", "pic0.png"))):
                target = folder / (key + ".png")
                if target.is_file():
                    continue
                for name in names:
                    try:
                        data = get(base + name)
                    except (OSError, ValueError):
                        continue
                    if not data or len(data) > ART_MAX or data[:8] != b"\x89PNG\r\n\x1a\n":
                        continue
                    try:
                        folder.mkdir(parents=True, exist_ok=True)
                        tmp = folder / (key + ".png.tmp")
                        tmp.write_bytes(data)
                        os.replace(str(tmp), str(target))
                    except OSError:
                        break
                    got += 1
                    break
    finally:
        ART_LOCK.release()
    if got and done is not None:
        done()
    return got


PLAYS_AFTER = 60.0


def record_result(folder, seconds, failed):
    if seconds >= PLAYS_AFTER:
        status = "Plays"
    elif failed:
        status = "Stops early"
    else:
        return None
    try:
        (Path(folder) / "play-result.json").write_text(json.dumps({"status": status, "seconds": int(seconds), "when": time.strftime("%Y-%m-%d %H:%M")}), encoding="utf-8")
    except OSError:
        return None
    return status


def scan_library(games_dir, console_dir, on_art=None):
    games_dir, console_dir = Path(games_dir), Path(console_dir)
    out = []
    if games_dir.is_dir():
        for folder in sorted(games_dir.iterdir(), key=lambda p: p.name.lower()):
            if not folder.is_dir() or folder.name.startswith(("_", ".")):
                continue
            exe = find_exe(folder)
            if exe is None:
                continue
            title, title_id = title_info(folder)
            art = {}
            sce = folder / "app0" / "sce_sys"
            for key, names in (("icon", ("icon0.png",)), ("bg", ("pic0.png", "pic1.png"))):
                src = next((sce / n for n in names if (sce / n).is_file()), None)
                if src is None:
                    continue
                dst = console_dir / "art" / title_id / (key + ".png")
                try:
                    if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(src, dst)
                    art[key] = "art/%s/%s.png" % (title_id, key)
                except OSError:
                    pass
            log = folder / "last-run.log"
            last = time.strftime("%Y-%m-%d %H:%M", time.localtime(log.stat().st_mtime)) if log.exists() else ""
            result = _read_json(folder / "play-result.json")
            result = result if isinstance(result, dict) else {}
            saves = 0
            if (folder / "_sd").is_dir():
                for _, _, files in os.walk(folder / "_sd"):
                    saves += len(files)
            out.append({"id": title_id, "title": title, "icon": art.get("icon", ""), "bg": art.get("bg", ""), "shot": "",
                        "status": result.get("status") if result.get("status") in ("Plays", "Stops early") else "", "fps": None, "lastRun": last, "folder": str(folder), "exe": str(exe), "saves": saves})
    have = {g["id"] for g in out}
    remote = [g for g in collection_games(console_dir) if g["id"] not in have]
    out.extend(remote)
    if on_art is not None and any(g["iconUrl"] and not (g["icon"] and g["bg"]) for g in remote):
        threading.Thread(target=fetch_art, args=(console_dir, remote, on_art), daemon=True).start()
    tmp = console_dir / "library.js.tmp"
    tmp.write_text("window.LIBRARY = " + json.dumps(out, indent=1) + ";\n", encoding="utf-8")
    os.replace(str(tmp), str(console_dir / "library.js"))
    return out


def suspend_seconds():
    try:
        return time.clock_gettime(time.CLOCK_BOOTTIME) - time.clock_gettime(time.CLOCK_MONOTONIC)
    except (AttributeError, OSError):
        return 0.0


SIGSTOP = getattr(signal, "SIGSTOP", 19)
SIGCONT = getattr(signal, "SIGCONT", 18)
SIGKILL = getattr(signal, "SIGKILL", signal.SIGTERM)
GAME_ENV = {"SDL_VIDEODRIVER": "kmsdrm", "ANYPS5_FULLSCREEN": "borderless"}
GATE = 'read -r _; exec "$0" </dev/null'
GAME_ENV_FILE = "game-env.txt"
GAME_ENV_PREFIXES = ("ANYPS5_", "APS5_", "VK_", "MESA_", "INTEL_", "SDL_")


def display_size(root="/sys/class/drm"):
    try:
        outputs = sorted(os.listdir(root))
    except OSError:
        return ""
    for name in outputs:
        try:
            with open(os.path.join(root, name, "status"), encoding="ascii") as f:
                if f.read().strip() != "connected":
                    continue
            with open(os.path.join(root, name, "modes"), encoding="ascii") as f:
                mode = f.readline().strip()
        except (OSError, ValueError):
            continue
        width, _, height = mode.partition("x")
        height = height.rstrip("i")
        if width.isdigit() and height.isdigit() and int(width) > 0 and int(height) > 0:
            return "%dx%d" % (int(width), int(height))
    return ""


def read_game_env(path):
    out = {}
    try:
        lines = Path(path).read_text(encoding="utf-8-sig").splitlines()
    except (OSError, ValueError):
        return out
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith(GAME_ENV_PREFIXES) and key.replace("_", "").isalnum() and "\x00" not in value:
            out[key] = value.strip()
    return out


def exit_reason(rc):
    if rc in (0, None):
        return ""
    if rc < 0:
        try:
            name = signal.Signals(-rc).name
        except ValueError:
            name = "signal %d" % -rc
        return "crashed (%s)" % name
    return "stopped (exit %d)" % rc


def last_log_line(path, limit=120):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 4096))
            tail = f.read().decode("utf-8", "replace")
    except OSError:
        return ""
    for line in reversed(tail.splitlines()):
        line = line.strip()
        if line and not line.startswith("== "):
            return line if len(line) <= limit else line[:limit - 3] + "..."
    return ""


class GameSession:
    def __init__(self, games_dir, publish, log, popen=subprocess.Popen, run=subprocess.run, family=None, settings_file=None,
                 handoff_wait=15.0, env=None, prepare=None, prepare_poll=0.5):
        self.games = Path(games_dir)
        self.publish = publish
        self.log = log
        self.popen = popen
        self.run = run
        self.family = family
        self.settings_file = settings_file
        self.handoff_wait = handoff_wait
        self.base_env = os.environ if env is None else env
        self.prepare = prepare
        self.prepare_poll = prepare_poll
        self.lock = threading.Lock()
        self.released = threading.Event()
        self.proc = None
        self.title = None
        self.uid = ""
        self.closing = False
        self.starting = False
        self.suspended = False

    def playing(self):
        with self.lock:
            return self.title if self.proc is not None else None

    def is_suspended(self):
        with self.lock:
            return self.proc is not None and self.suspended

    def _inside(self, folder):
        try:
            g = self.games.resolve()
            f = Path(folder).resolve()
        except OSError:
            return None
        return f if f != g and g in f.parents and f.is_dir() else None

    def game_env(self, folder=None):
        env = dict(self.base_env)
        for k, v in GAME_ENV.items():
            env.setdefault(k, v)
        size = display_size()
        if size:
            env.setdefault("APS5_OUTPUT_RESOLUTION", size)
        if folder is not None:
            env.update(read_game_env(Path(folder) / GAME_ENV_FILE))
        return env

    def display_released(self):
        self.released.set()

    def _handoff(self):
        self.released.clear()
        start = time.monotonic()
        self.publish({"display": "release"})
        if self.released.wait(self.handoff_wait):
            self.log.info("console released the display in %.2f s", time.monotonic() - start)
            return True
        self.log.warning("console did not release the display within %.1f s", self.handoff_wait)
        return False

    def _busy_toast(self):
        return {"toast": "%s is still running. PS button > Close game first." % (self.title or "A game")}

    def play(self, folder, user=""):
        f = self._inside(folder) if isinstance(folder, str) else None
        if f is None:
            return {"toast": "That game is not in the games folder"}
        with self.lock:
            if self.proc is not None or self.starting:
                return self._busy_toast()
        exe = find_exe(f)
        title, title_id = title_info(f)
        if exe is None and not self._preparing(self._job(f, title_id)):
            return {"toast": "This game has no Linux program yet"}
        uid = user if isinstance(user, str) else ""
        if uid and self.family is not None:
            if not self.family.game_allowed(uid, title_id, self.settings_file):
                return {"toast": "This user is not allowed to play this game (Settings > Family and Parental Controls)"}
            if self.family.minutes_left(uid, self.settings_file) <= 0:
                return {"toast": "Playtime for today is over for this user"}
        with self.lock:
            if self.proc is not None or self.starting:
                return self._busy_toast()
            self.starting = True
        threading.Thread(target=self._launch, args=(f, exe, title, title_id, uid), daemon=True).start()
        return {"starting": title}

    def _job(self, f, title_id):
        if self.prepare is None:
            return None
        try:
            return self.prepare(f, title_id)
        except Exception as e:
            self.log.warning("install state for %s unreadable: %s", title_id, e)
            return None

    @staticmethod
    def _preparing(job):
        return isinstance(job, dict) and job.get("state") == "installing" and bool(job.get("running"))

    def _launch_failed(self, title, e, card=None):
        with self.lock:
            self.starting = False
        self.publish({"launch": dict(card or {"title": title}, phase="failed")})
        self.publish({"display": "acquire"})
        self.publish({"toast": "Could not start %s: %s" % (title, e)})

    def _wait_prepared(self, f, title_id, card):
        job = self._job(f, title_id)
        last = None
        while self._preparing(job):
            last = job
            pct = job.get("percent")
            self.publish({"launch": dict(card, phase="preparing", percent=pct if isinstance(pct, (int, float)) else None,
                                         detail=str(job.get("message") or ""))})
            time.sleep(self.prepare_poll)
            job = self._job(f, title_id)
        if last is not None:
            self.log.info("%s finished preparing: %s", card["title"], (job or {}).get("state", "gone"))
        exe = find_exe(f)
        if exe is not None:
            return exe, None
        why = (job or {}).get("error") or (last or {}).get("error")
        return None, why or "This game has no Linux program yet"

    def _launch(self, f, exe, title, title_id, uid):
        card = {"title": title, "titleId": title_id}
        self.publish({"launch": dict(card, phase="starting")})
        if exe is None:
            exe, why = self._wait_prepared(f, title_id, card)
            if exe is None:
                self._launch_failed(title, why, card)
                return
        try:
            logf = open(f / "last-run.log", "w", encoding="utf-8")
        except OSError as e:
            self._launch_failed(title, e, card)
            return
        logf.write("== %s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), exe))
        logf.flush()
        gate_r, gate_w = os.pipe() if os.name == "posix" else (None, None)
        argv = [str(exe)] if gate_r is None else ["/bin/sh", "-c", GATE, str(exe)]
        try:
            proc = self.popen(argv, cwd=str(f), env=self.game_env(f), stdin=subprocess.DEVNULL if gate_r is None else gate_r,
                              stdout=logf, stderr=subprocess.STDOUT, start_new_session=True)
        except OSError as e:
            logf.close()
            if gate_w is not None:
                os.close(gate_w)
            self._launch_failed(title, e, card)
            return
        finally:
            if gate_r is not None:
                os.close(gate_r)
        with self.lock:
            self.proc, self.title, self.uid = proc, title, uid
            self.closing = self.suspended = self.starting = False
        pid = getattr(proc, "pid", None)
        self.log.info("started %s (%s) pid %s, held until the console releases the display", title, exe, pid)
        self.publish({"launch": dict(card, phase="started", pid=pid)})
        self._handoff()
        if gate_w is not None:
            os.close(gate_w)
        threading.Thread(target=self._wait, args=(proc, title, logf, f), daemon=True).start()
        if uid and self.family is not None:
            threading.Thread(target=self._family_clock, args=(proc, uid), daemon=True).start()
        self.publish({"ingame": True, "title": title, "bg": "", "handoff": True, "pid": pid})

    def _wait(self, proc, title, logf, folder):
        started = time.monotonic()
        rc = proc.wait()
        logf.close()
        seconds = time.monotonic() - started
        with self.lock:
            closing = self.closing
            if self.proc is proc:
                self.proc, self.title, self.uid, self.suspended = None, None, "", False
        self.log.info("%s exited with %s", title, rc)
        self.publish({"ingame": False})
        reason = exit_reason(rc)
        record_result(folder, seconds, bool(reason) and not closing)
        if reason and not closing:
            line = last_log_line(folder / "last-run.log")
            detail = (" " + line.rstrip(".") + ".") if line else ""
            self.publish({"toast": "%s %s.%s Log: %s" % (title, reason, detail, folder / "last-run.log")})
        self.publish({"gameClosed": title})

    def _family_clock(self, proc, uid):
        warned = set()
        while proc.poll() is None:
            for _ in range(60):
                if proc.poll() is not None:
                    return
                time.sleep(1)
            if self.is_suspended():
                continue
            self.family.add_minute(uid)
            left = self.family.minutes_left(uid, self.settings_file)
            if left in (15, 5, 1) and left not in warned:
                warned.add(left)
                self.publish({"toast": "Playtime ends in %d minute%s" % (left, "" if left == 1 else "s")})
            if left <= 0:
                if self.family.when_playtime_ends(uid, self.settings_file) == "close":
                    self.publish({"toast": "Playtime is over for today. Closing the game."})
                    self.close()
                    return
                if 0 not in warned:
                    warned.add(0)
                    self.publish({"toast": "Playtime is over for today"})

    def _signal(self, proc, sig):
        try:
            os.killpg(proc.pid, sig)
            return
        except (ProcessLookupError, PermissionError, AttributeError, OSError):
            pass
        try:
            proc.send_signal(sig)
        except (OSError, ValueError):
            pass

    def suspend(self):
        with self.lock:
            proc, title = self.proc, self.title
            if proc is None or self.suspended or self.closing:
                return None
            self.suspended = True
        self._signal(proc, SIGSTOP)
        self.log.info("suspended %s", title)
        msg = {"suspended": True, "title": title}
        self.publish(msg)
        return msg

    def resume(self):
        with self.lock:
            proc = self.proc
            if proc is None or not self.suspended:
                return None
        self._handoff()
        with self.lock:
            if self.proc is not proc or not self.suspended:
                return None
            self.suspended = False
        self._signal(proc, SIGCONT)
        self.log.info("resumed %s", self.title)
        msg = {"suspended": False}
        self.publish(msg)
        return msg

    def close(self, wait=5.0):
        with self.lock:
            proc = self.proc
            if proc is None:
                return False
            self.closing = True
            stopped = self.suspended
        for sig in (signal.SIGTERM, SIGKILL):
            self._signal(proc, sig)
            if stopped:
                self._signal(proc, SIGCONT)
            try:
                proc.wait(timeout=wait)
                return True
            except subprocess.TimeoutExpired:
                continue
        return True

    def power(self, what):
        verb = {"off": "poweroff", "restart": "reboot"}.get(what)
        if verb is None:
            return {"toast": "Unknown power option"}
        self.close()
        r = self.run(["systemctl", verb], capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return {"toast": "Could not %s: %s" % ("turn off" if what == "off" else "restart", (r.stderr or "").strip()[:160])}
        return None

    def rest(self):
        before = suspend_seconds()
        r = self.run(["systemctl", "suspend"], capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return {"toast": "Could not enter rest mode: " + (r.stderr or "").strip()[:160]}
        threading.Thread(target=self._watch_wake, args=(before,), daemon=True).start()
        return None

    def _watch_wake(self, before, limit=86400 * 7):
        end = time.monotonic() + limit
        while time.monotonic() < end:
            time.sleep(1)
            if suspend_seconds() - before > 1.5:
                self.publish({"woke": True})
                return
