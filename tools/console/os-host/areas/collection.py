import http.client
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import types
import urllib.error
import urllib.parse
import urllib.request

ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
FOLDER_ID_RE = re.compile(r"\(([A-Za-z0-9_-]{1,32})\)\s*$")
BAD_NAME = re.compile(r'[\x00-\x1f/\\:*?"<>|]+')
STEP_RE = re.compile(r"\[(\d+)\s*/\s*(\d+)\]")
PCT_RE = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d+)?)\s*%")
DEFAULT_PORT = 8791
RESERVE = 256 << 20
CHUNK = 1 << 20
TIMEOUT = 30
ATTEMPTS = 4
PART = ".anyps5-part"
MARKER = ".anyps5-collection.json"
STAGE = ".anyps5-build"
BUILD_RESULT = "anyps5-build.json"
WRAPPER = "anyps5-build-game"
DEFAULT_TOOLCHAIN = "/opt/anyps5"
CPUINFO = "/proc/cpuinfo"
MOUNTS = "/proc/mounts"
ELF = b"\x7fELF"
GAME_FLAGS = ("avx", "avx2", "fma", "bmi2")
V2 = ("cx16", "lahf_lm", "popcnt", "sse4_1", "sse4_2", "ssse3")
V3 = V2 + ("avx", "avx2", "bmi1", "bmi2", "f16c", "fma", "abm", "movbe")
V4 = V3 + ("avx512f", "avx512bw", "avx512cd", "avx512dq", "avx512vl")
LEVELS = (("x86-64-v4", V4), ("x86-64-v3", V3), ("x86-64-v2", V2), ("x86-64", ()))
WIN_FEATURES = {"sse3": 13, "ssse3": 36, "sse4_1": 37, "sse4_2": 38, "avx": 39, "avx2": 40, "avx512f": 41}
VERSION_KEYS = ("commit", "date", "cpu", "baseline", "cpu_baseline", "avx_lowering", "avx", "lowering", "os", "arch", "revision")
PROTECTED = ("app0", "download0", "temp0", "mods", "data")
PRIORITY = {"device": 2, "media": 1, "server": 0}
MEDIA_PREFIXES = ("/run/media/", "/media/", "/mnt/")
MEDIA_FS = ("vfat", "msdos", "exfat", "ntfs", "ntfs3", "fuseblk", "ext2", "ext3", "ext4", "btrfs", "xfs", "f2fs", "iso9660", "udf", "hfsplus")
MEDIA_DEPTH = 3
MEDIA_BUDGET = 1500
MEDIA_SECONDS = 3.0
MEDIA_TTL = 15.0
MEDIA_SKIP = ("windows", "program files", "program files (x86)", "programdata", "users", "recovery", "perflogs", "msocache",
              "system volume information", "$recycle.bin", "lost+found", "proc", "sys", "dev", "boot", "efi")
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

_shared = sys.modules.setdefault("anyps5_collection_jobs", types.ModuleType("anyps5_collection_jobs"))
if not hasattr(_shared, "jobs"):
    _shared.jobs = {}
    _shared.lock = threading.Lock()
for _name, _value in (("build_lock", threading.Lock()), ("media", None), ("sizes", {}), ("size_queue", []), ("sizer", None)):
    if not hasattr(_shared, _name):
        setattr(_shared, _name, _value)


class Cancelled(Exception):
    pass


def games_root():
    return os.path.abspath(os.path.expanduser(os.environ.get("ANYPS5_COLLECTION_DIR") or os.environ.get("ANYPS5_GAMES") or os.path.join("~", "games")))


def state_dir():
    return os.environ.get("ANYPS5_COLLECTION_STATE") or os.path.join(os.path.expanduser("~"), ".local", "share", "anyps5", "collection")


def toolchain_dir():
    return os.environ.get("ANYPS5_TOOLCHAIN") or DEFAULT_TOOLCHAIN


def _state_path(tid):
    return os.path.join(state_dir(), tid + ".json")


def _log_path(tid):
    return os.path.join(state_dir(), tid + ".log")


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            value = json.load(f)
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _write_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.%d.%d.tmp" % (path, os.getpid(), threading.get_ident())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(value, f, indent=1)
    for attempt in range(50):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 49:
                os.remove(tmp)
                raise
            time.sleep(0.02)


def _text(value):
    return value.strip() if isinstance(value, str) and value.strip() else None


def _title_id(args):
    tid = args.get("titleId", args.get("id"))
    if not isinstance(tid, str) or not ID_RE.match(tid):
        raise ValueError("A game title ID is needed")
    return tid


def version_key(text):
    return tuple(int(p) for p in re.findall(r"\d+", text)) if isinstance(text, str) else ()


def _is_link(path):
    return os.path.islink(path) or getattr(os.path, "isjunction", lambda p: False)(path)


def normalize_server(value):
    v = _text(value)
    if not v:
        raise ValueError("Enter the library server address, for example 192.0.2.1:%d" % DEFAULT_PORT)
    if "://" not in v:
        v = "http://" + v
    p = urllib.parse.urlsplit(v)
    try:
        port = p.port
    except ValueError:
        port = -1
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password or port == -1:
        raise ValueError("The library server address is not valid")
    return "%s://%s" % (p.scheme, p.netloc if port else "%s:%d" % (p.netloc, DEFAULT_PORT))


def _config():
    return _read_json(os.path.join(state_dir(), "config.json")) or {}


def server(args):
    if _text(args.get("server")):
        url = normalize_server(args.get("server"))
        cfg = _config()
        if cfg.get("server") != url:
            cfg["server"] = url
            _write_json(os.path.join(state_dir(), "config.json"), cfg)
        return url
    saved = _text(_config().get("server")) or _text(os.environ.get("ANYPS5_LIBRARY_URL"))
    return normalize_server(saved) if saved else None


def _norm_arch(value):
    m = (value or "").strip().lower()
    if m in ("x86_64", "amd64", "x64", "x86-64"):
        return "x86_64"
    if m in ("aarch64", "arm64", "armv8"):
        return "arm64"
    return m


def _norm_os(value):
    v = (value or "").strip().lower()
    if v.startswith("win") or v == "nt":
        return "windows"
    if v.startswith("linux"):
        return "linux"
    return v


def _machine():
    if os.name == "nt":
        return os.environ.get("PROCESSOR_ARCHITEW6432") or os.environ.get("PROCESSOR_ARCHITECTURE") or ""
    return os.uname().machine


def _platform():
    return _norm_os("windows" if os.name == "nt" else sys.platform), _norm_arch(_machine())


def parse_cpuinfo(text):
    model, flags = "", None
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key = key.strip().lower()
        if key == "model name" and not model:
            model = value.strip()
        elif key in ("flags", "features") and flags is None:
            flags = set(value.lower().split())
    return model, flags or set()


def _win_cpu():
    flags, known = set(), set()
    try:
        import ctypes
        present = ctypes.windll.kernel32.IsProcessorFeaturePresent
        for name, pf in WIN_FEATURES.items():
            known.add(name)
            if present(pf):
                flags.add(name)
    except (ImportError, AttributeError, OSError):
        known = set()
    return {"model": os.environ.get("PROCESSOR_IDENTIFIER", ""), "flags": flags, "known": known}


def cpu():
    path = os.environ.get("ANYPS5_CPUINFO") or ("" if os.name == "nt" else CPUINFO)
    if path:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                model, flags = parse_cpuinfo(f.read())
            return {"model": model, "flags": flags, "known": None}
        except OSError:
            pass
    if os.name == "nt":
        return _win_cpu()
    return {"model": os.environ.get("PROCESSOR_IDENTIFIER", ""), "flags": set(), "known": set()}


def has_flag(c, flag):
    if c["known"] is not None and flag not in c["known"]:
        return None
    return flag in c["flags"]


def _flag_names(flags):
    return ", ".join(f.upper().replace("_", ".") for f in flags)


def _level(c, arch):
    if arch != "x86_64":
        return ""
    return next((name for name, need in LEVELS if all(has_flag(c, f) for f in need)), "")


def _baseline_need(name):
    v = re.sub(r"[^a-z0-9]", "", (name or "").lower())
    m = re.search(r"v(\d)$", v)
    level = int(m.group(1)) if m else (1 if v in ("x8664", "amd64") else 0)
    return {2: V2, 3: V3, 4: V4}.get(level, ())


def _key(k):
    return re.sub(r"[^a-z0-9]+", "_", str(k).lower()).strip("_")


def _yes(v):
    if isinstance(v, bool):
        return v
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in ("yes", "y", "true", "1", "on", "enabled"):
        return True
    if s in ("no", "n", "false", "0", "off", "disabled", "none"):
        return False
    return None


def parse_version(text):
    try:
        value = json.loads(text)
    except ValueError:
        value = None
    if isinstance(value, dict):
        return {_key(k): v for k, v in value.items()}
    out = {}
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        m = re.match(r"^\s*([A-Za-z][\w.-]*(?:[ \t]+[A-Za-z][\w.-]*){0,2})\s*[:=]\s*(.*?)\s*$", line)
        if not m:
            m = re.match(r"^\s*([A-Za-z][\w-]*)\s+(\S.*?)\s*$", line)
            if not m or _key(m.group(1)) not in VERSION_KEYS:
                continue
        out[_key(m.group(1))] = m.group(2).strip("\"'")
    if out:
        return out
    for tok in text.split():
        low = tok.lower()
        if re.match(r"^\d{4}-\d{2}-\d{2}", tok):
            out.setdefault("date", tok)
        elif re.match(r"^(x86-64|x86_64)(-v\d)?$", low):
            out.setdefault("cpu_baseline", tok)
        elif "avx" in low and _yes(low.rsplit("=", 1)[-1]) is not None:
            out.setdefault("avx_lowering", low.rsplit("=", 1)[-1])
        elif re.match(r"^[0-9a-f]{7,40}$", low):
            out.setdefault("commit", tok)
    return out


def _first(v, *names):
    for n in names:
        if v.get(n) not in (None, ""):
            return v[n]
    return None


def toolchain():
    tc = toolchain_dir()
    names = [WRAPPER] + ([WRAPPER + e for e in (".exe", ".cmd", ".bat", ".py")] if os.name == "nt" else [])
    wrapper = next((p for p in (os.path.join(d, n) for d in (os.path.join(tc, "bin"), tc) for n in names)
                    if os.path.isfile(p) and (os.name == "nt" or os.access(p, os.X_OK))), None)
    try:
        with open(os.path.join(tc, "VERSION"), encoding="utf-8-sig", errors="replace") as f:
            raw = f.read()
    except OSError:
        raw = None
    v = parse_version(raw) if raw else {}
    lowering = _first(v, "avx_lowering", "avx_lower", "lowers_avx", "avx2_lowering", "lowering", "avx")
    if lowering is None:
        lowering = next((v[k] for k in v if "avx" in k and "lower" in k), None)
    return {"path": tc, "present": wrapper is not None, "wrapper": wrapper, "hasVersion": raw is not None,
            "commit": _text(str(_first(v, "commit", "git_commit", "revision", "rev", "sha") or "")),
            "date": _text(str(_first(v, "date", "build_date", "built", "commit_date") or "")),
            "cpuBaseline": _text(str(_first(v, "cpu_baseline", "baseline", "cpu", "march", "cpu_level") or "")),
            "avxLowering": True if _yes(v.get("to_sse")) else _yes(lowering),
            "maxSystemVersion": _text(str(_first(v, "max_system_version", "max_system_software", "max_firmware", "firmware") or "")),
            "os": _text(str(_first(v, "os", "target_os") or "")), "arch": _text(str(_first(v, "arch", "target_arch") or ""))}


def device(args=None):
    osn, arch = _platform()
    c = cpu()
    return {"os": osn, "arch": arch,
            "cpu": {"model": c["model"], "flags": {f: has_flag(c, f) for f in GAME_FLAGS}, "level": _level(c, arch)},
            "toolchain": toolchain(), "_cpu": c}


def _public_device(dev):
    return {k: v for k, v in dev.items() if not k.startswith("_")}


def _sysver(value):
    if isinstance(value, int):
        value = "0x%016x" % value
    s = (value or "").strip().lower() if isinstance(value, str) else ""
    if s.startswith("0x"):
        h = s[2:].rjust(16, "0")[:16]
        parts = (h[0:2], h[2:4], h[4:8])
        try:
            return tuple(int(p, 10) if p.isdigit() else int(p, 16) for p in parts)
        except ValueError:
            return ()
    return version_key(s)


def _sysver_text(value):
    v = _sysver(value)
    return "%d.%02d" % (v[0], v[1]) if len(v) >= 2 else str(value)


def _query(dev):
    return "?" + urllib.parse.urlencode({"os": dev["os"], "arch": dev["arch"]})


def _open(url, headers=None):
    h = {"User-Agent": "AnyPS5-OS"}
    h.update(headers or {})
    return OPENER.open(urllib.request.Request(url, headers=h), timeout=TIMEOUT)


def fetch_json(url, path):
    try:
        with _open(url + path) as r:
            value = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        e.close()
        raise RuntimeError("The library server answered HTTP %d for %s" % (e.code, path.split("?")[0]))
    except (OSError, ValueError) as e:
        raise RuntimeError("The library server at %s did not answer: %s" % (url, getattr(e, "reason", None) or e))
    if not isinstance(value, dict):
        raise RuntimeError("The library server sent an unexpected reply for " + path.split("?")[0])
    return value


def _file_url(url, tid, rel, query=""):
    return "%s/game/%s/%s%s" % (url, urllib.parse.quote(tid), urllib.parse.quote(rel, safe="/"), query)


def _param_title(param):
    loc = param.get("localizedParameters")
    if not isinstance(loc, dict):
        return None
    lang = loc.get("defaultLanguage")
    entry = loc.get(lang) if isinstance(lang, str) else None
    if not isinstance(entry, dict) or not _text(entry.get("titleName")):
        entry = next((v for v in loc.values() if isinstance(v, dict) and _text(v.get("titleName"))), None)
    return _text(entry.get("titleName")) if isinstance(entry, dict) else None


def dump_info(root):
    param = _read_json(os.path.join(root, "sce_sys", "param.json"))
    tid = param.get("titleId") if param else None
    if not isinstance(tid, str) or not ID_RE.match(tid):
        return None
    icon = os.path.join(root, "sce_sys", "icon0.png")
    return {"titleId": tid, "title": _param_title(param), "version": _text(param.get("contentVersion")) or "",
            "requiredSystemVersion": _text(param.get("requiredSystemSoftwareVersion")), "path": root,
            "iconFile": icon if os.path.isfile(icon) else ""}


def folder_info(folder):
    name = os.path.basename(folder)
    marker = _read_json(os.path.join(folder, MARKER)) or {}
    meta = _read_json(os.path.join(folder, "game.json")) or {}
    param = _read_json(os.path.join(folder, "app0", "sce_sys", "param.json")) or {}
    m = FOLDER_ID_RE.search(name)
    tid = next((t for t in (marker.get("titleId"), meta.get("titleId"), param.get("titleId"), m and m.group(1))
                if isinstance(t, str) and ID_RE.match(t)), None)
    title = _text(marker.get("title")) or _text(meta.get("title")) or _param_title(param) or FOLDER_ID_RE.sub("", name).strip() or name
    return tid, title


def _children():
    root = games_root()
    try:
        names = sorted(os.listdir(root), key=str.lower)
    except OSError:
        return []
    out = []
    for name in names:
        folder = os.path.join(root, name)
        if name.startswith(("_", ".")) or _is_link(folder) or not os.path.isdir(folder):
            continue
        out.append(folder)
    return out


def installed():
    out = {}
    for folder in _children():
        tid, title = folder_info(folder)
        if tid and tid not in out:
            out[tid] = {"titleId": tid, "title": title, "folder": folder}
    return out


def _folders_for(tid):
    return [f for f in _children() if folder_info(f)[0] == tid]


def _safe_rel(name):
    return isinstance(name, str) and name not in ("", ".", "..") and not any(c in name for c in "/\\:\x00")


def built(folder, marker, osn):
    if isinstance(marker, dict) and osn == "windows" and (marker.get("prebuilt") or ("toolchain" not in marker and "outputs" not in marker)):
        return os.path.isdir(folder)
    outputs = marker.get("outputs") if isinstance(marker, dict) and not marker.get("prebuilt") else None
    if isinstance(outputs, list) and outputs:
        return any(_safe_rel(n) and os.path.lexists(os.path.join(folder, n)) for n in outputs)
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return False
    for n in names:
        p = os.path.join(folder, n)
        if n.startswith((".", "anyps5")) or not os.path.isfile(p):
            continue
        if osn == "windows":
            if n.lower().endswith(".exe"):
                return True
            continue
        try:
            with open(p, "rb") as f:
                if f.read(4) == ELF and os.access(p, os.X_OK):
                    return True
        except OSError:
            pass
    return False


def parse_mounts(text):
    out = []
    for line in text.splitlines():
        p = line.split()
        if len(p) < 3 or p[2] not in MEDIA_FS:
            continue
        mp = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), p[1])
        if any(mp.startswith(pre) for pre in MEDIA_PREFIXES) and mp.rstrip("/") not in ("/run/media", "/media", "/mnt") and mp not in out:
            out.append(mp)
    return out


def _win_drives(fixed=False):
    out = []
    try:
        import ctypes
        k = ctypes.windll.kernel32
        mask = k.GetLogicalDrives()
        for i in range(26):
            if mask >> i & 1:
                root = "%s:\\" % chr(65 + i)
                kind = k.GetDriveTypeW(ctypes.c_wchar_p(root))
                system = os.environ.get("SystemDrive", "C:").upper()
                if kind == 2 or (kind == 3 and fixed and root[:2].upper() != system):
                    out.append({"path": root, "label": root[:2], "removable": kind == 2})
    except (ImportError, AttributeError, OSError):
        pass
    return out


def media_roots():
    env = os.environ.get("ANYPS5_MEDIA_ROOTS")
    if env is not None:
        return [{"path": p, "label": os.path.basename(p.rstrip("/\\")) or p, "removable": True} for p in env.split(os.pathsep) if p]
    scan = os.environ.get("ANYPS5_MEDIA_SCAN", "1").strip().lower()
    if scan == "0":
        return []
    if os.name == "nt":
        return _win_drives(scan == "all")
    try:
        with open(MOUNTS, encoding="utf-8", errors="replace") as f:
            points = parse_mounts(f.read())
    except OSError:
        points = []
    system = set()
    for p in ("/", games_root()):
        try:
            system.add(os.stat(p).st_dev)
        except OSError:
            pass
    out = []
    for p in points:
        try:
            if os.stat(p).st_dev in system:
                continue
        except OSError:
            continue
        out.append({"path": p, "label": os.path.basename(p.rstrip("/")) or p, "removable": True})
    return out


def _scan_root(media, skip_real):
    out, seen, start = [], 0, time.monotonic()
    queue = [(media["path"], 0)]
    while queue:
        path, depth = queue.pop(0)
        for root in (path, os.path.join(path, "app0")):
            if os.path.isfile(os.path.join(root, "sce_sys", "param.json")):
                real = os.path.realpath(root)
                if not any(real == s or real.startswith(s + os.sep) for s in skip_real):
                    info = dump_info(root)
                    if info:
                        out.append(dict(info, type="media", label=media["label"], removable=media["removable"]))
                break
        else:
            if depth >= MEDIA_DEPTH:
                continue
            try:
                names = sorted(os.listdir(path), key=str.lower)
            except OSError:
                continue
            for name in names:
                child = os.path.join(path, name)
                if name.startswith((".", "_", "$")) or name.lower() in MEDIA_SKIP or _is_link(child):
                    continue
                seen += 1
                if seen > MEDIA_BUDGET or time.monotonic() - start > MEDIA_SECONDS:
                    return out
                if os.path.isdir(child):
                    queue.append((child, depth + 1))
    return out


def media(refresh=False):
    roots = media_roots()
    key = tuple(r["path"] for r in roots) + (games_root(),)
    now = time.monotonic()
    with _shared.lock:
        hit = _shared.media
    if not refresh and hit and hit[0] == key and now - hit[1] < MEDIA_TTL:
        return hit[2], roots
    skip = [os.path.realpath(games_root())]
    found = []
    for r in roots:
        found.extend(_scan_root(r, skip))
    with _shared.lock:
        _shared.media = (key, now, found)
    return found, roots


def walk_files(root):
    out = []
    for cur, dirs, files in os.walk(root):
        rel_dir = os.path.relpath(cur, root)
        dirs[:] = sorted(d for d in dirs if not _is_link(os.path.join(cur, d)))
        for name in sorted(files):
            full = os.path.join(cur, name)
            if _is_link(full) or name.endswith(PART):
                continue
            try:
                st = os.stat(full)
            except OSError:
                continue
            rel = name if rel_dir == "." else rel_dir.replace(os.sep, "/") + "/" + name
            out.append({"path": rel, "size": st.st_size, "mtime": st.st_mtime, "src": full})
    return out


def _size_worker():
    while True:
        with _shared.lock:
            if not _shared.size_queue:
                _shared.sizer = None
                return
            key = _shared.size_queue.pop(0)
        total = sum(f["size"] for f in walk_files(key[0]))
        with _shared.lock:
            _shared.sizes[key] = total


def _media_size(src):
    if not src.get("removable"):
        return None
    try:
        key = (src["path"], os.path.getmtime(os.path.join(src["path"], "sce_sys", "param.json")))
    except OSError:
        return None
    with _shared.lock:
        if key in _shared.sizes:
            return _shared.sizes[key]
        if key not in _shared.size_queue:
            _shared.size_queue.append(key)
        if _shared.sizer is None:
            _shared.sizer = threading.Thread(target=_size_worker, daemon=True, name="collection-sizes")
            _shared.sizer.start()
    return None


def _gather(args, dev, refresh=False):
    url = server(args)
    error, remote = None, []
    if url:
        try:
            remote = fetch_json(url, "/catalog.json" + _query(dev)).get("games")
        except RuntimeError as e:
            error = str(e)
    games = {}

    def slot(tid):
        return games.setdefault(tid, {"titleId": tid, "title": None, "sources": [], "folder": "", "marker": None, "iconFile": "", "icon": ""})

    for g in remote if isinstance(remote, list) else []:
        tid = g.get("titleId") if isinstance(g, dict) else None
        if not isinstance(tid, str) or not ID_RE.match(tid):
            continue
        icon = g.get("icon") if isinstance(g.get("icon"), str) and g.get("icon").startswith("/") else ""
        e = slot(tid)
        e["icon"] = url + icon if icon else ""
        e["remoteTitle"] = _text(g.get("title"))
        e["sources"].append({"type": "server", "kind": g.get("kind") if isinstance(g.get("kind"), str) else "", "url": url,
                             "version": g.get("version") if isinstance(g.get("version"), str) else "",
                             "size": g.get("size") if isinstance(g.get("size"), int) else None,
                             "requiredSystemVersion": _text(g.get("requiredSystemVersion")), "title": _text(g.get("title"))})
    for folder in _children():
        tid, title = folder_info(folder)
        if not tid:
            continue
        e = slot(tid)
        if e["folder"]:
            continue
        e["folder"], e["localTitle"] = folder, title
        e["marker"] = _read_json(os.path.join(folder, MARKER))
        info = dump_info(os.path.join(folder, "app0"))
        if info and info["titleId"] == tid:
            e["sources"].append(dict(info, type="device", size=None))
            e["iconFile"] = info["iconFile"]
    found, roots = media(refresh)
    for info in found:
        e = slot(info["titleId"])
        e["sources"].append(dict(info, size=_media_size(info)))
        e["mediaTitle"] = e.get("mediaTitle") or info["title"]
        e["iconFile"] = e["iconFile"] or info["iconFile"]
    return url, error, games, roots


def _prebuilt(src, dev):
    return src["type"] == "server" and src.get("kind") == "game" and dev["os"] == "windows" and dev["arch"] == "x86_64"


def _usable(src, dev):
    return _prebuilt(src, dev) or dev["toolchain"]["present"]


def _rank(src):
    return version_key(src.get("version")), PRIORITY.get(src["type"], 0)


def choose_source(g, dev, want=None):
    cands = [s for s in g["sources"] if not want or s["type"] == want]
    pre = [s for s in cands if _prebuilt(s, dev)]
    usable = pre or [s for s in cands if _usable(s, dev)]
    pool = usable or cands
    return max(pool, key=_rank) if pool else None


def _recorded(g, job, dev):
    commit = dev["toolchain"]["commit"]
    if job and job.get("toolchainCommit") == commit and (job.get("requires") is not None or job.get("needsNewer")):
        return job.get("requires"), job.get("needsNewer")
    m = g.get("marker") or {}
    if (m.get("toolchain") or {}).get("commit") == commit and isinstance(m.get("requires"), list):
        return m["requires"], None
    return None, None


def compat(g, dev, best, job=None):
    def no(reason, status="unsupported"):
        return {"status": status, "reason": reason, "prebuilt": False}

    if best is not None and _prebuilt(best, dev):
        return {"status": "installable", "reason": None, "prebuilt": True}
    tc, c = dev["toolchain"], dev["_cpu"]
    if not tc["present"]:
        return no("The AnyPS5 tools are not installed on this system (%s)" % tc["path"])
    if (tc["os"] and _norm_os(tc["os"]) != dev["os"]) or (tc["arch"] and _norm_arch(tc["arch"]) != dev["arch"]):
        return no("The bundled AnyPS5 is built for %s %s, not %s %s" % (tc["os"] or dev["os"], tc["arch"] or dev["arch"], dev["os"], dev["arch"]))
    missing = [f for f in _baseline_need(tc["cpuBaseline"]) if has_flag(c, f) is False]
    if missing:
        return no("The bundled AnyPS5 is built for %s and this CPU has no %s" % (tc["cpuBaseline"], _flag_names(missing)))
    requires, needs = _recorded(g, job, dev)
    if needs:
        return no(needs, "needs-newer")
    if requires is not None:
        miss = [f for f in requires if has_flag(c, str(f).lower()) is False]
        if miss:
            return no("This game needs %s and this CPU has none" % _flag_names(miss))
    elif not tc["avxLowering"]:
        miss = [f for f in GAME_FLAGS if has_flag(c, f) is False]
        if miss:
            return no("PS5 games use %s and this CPU has none; the bundled AnyPS5 does not lower them" % _flag_names(miss))
    need = best.get("requiredSystemVersion") if best else None
    if need and tc["maxSystemVersion"] and _sysver(need) and _sysver(tc["maxSystemVersion"]) and _sysver(need) > _sysver(tc["maxSystemVersion"]):
        return no("This game needs PS5 system software %s; the bundled AnyPS5 supports up to %s"
                  % (_sysver_text(need), _sysver_text(tc["maxSystemVersion"])), "needs-newer")
    return {"status": "installable", "reason": None, "prebuilt": False}


def _short(commit):
    return (commit or "unknown")[:10]


def update_reason(marker, best, dev):
    if not marker:
        return None
    mv = _text(marker.get("version"))
    if best and mv and best.get("version") and version_key(best["version"]) > version_key(mv):
        return "Version %s is available (installed %s)" % (best["version"], mv)
    if marker.get("prebuilt"):
        return None
    mt, tc = marker.get("toolchain") or {}, dev["toolchain"]
    if tc["present"] and tc["commit"] and mt.get("commit") and tc["commit"] != mt["commit"]:
        if tc["date"] and mt.get("date") and tc["date"] < mt["date"]:
            return None
        return "AnyPS5 was updated (%s, the game was built with %s)" % (_short(tc["commit"]), _short(mt["commit"]))
    bf = marker.get("builtFor") or {}
    if bf.get("os") and (bf.get("os"), bf.get("arch")) != (dev["os"], dev["arch"]):
        return "It was built for %s %s" % (bf.get("os"), bf.get("arch"))
    return None


def _job_message(v):
    s, phase = v.get("state"), v.get("phase")
    if s == "installing":
        if phase == "building":
            return "Waiting to build" if v.get("waiting") else "Building %d%%" % int(v.get("percent") or 0)
        return "Copying %d%%" % int(v.get("percent") or 0)
    if s == "failed":
        return "Failed (%s)" % (v.get("error") or "unknown error")
    return {"installed": "Installed", "cancelled": "Cancelled"}.get(s, "")


def _alive(tid):
    with _shared.lock:
        job = _shared.jobs.get(tid)
    return job is not None and (job[0] is None or job[0].is_alive())


def _view(tid):
    st = _read_json(_state_path(tid))
    if st is None or st.get("titleId") != tid:
        return None
    live = _alive(tid)
    v = dict(st)
    if v.get("state") == "downloading":
        v["state"], v["phase"] = "installing", "copying"
    elif v.get("state") == "error":
        v["state"] = "failed"
    if v.get("state") == "installing" and not live:
        v.update({"state": "failed", "error": v.get("error") or "Stopped before finishing", "interrupted": True})
    v["running"] = live
    total = v.get("total") or 0
    if v.get("state") == "installed":
        v["percent"] = 100.0
    elif v.get("phase") == "building":
        v["percent"] = round(float(v.get("buildPercent") or 0.0), 1)
    else:
        v["percent"] = round(100.0 * (v.get("done") or 0) / total, 1) if total else 0.0
    v["id"] = tid
    v["message"] = _job_message(v)
    return v


def job_view(folder, tid):
    if not isinstance(tid, str) or not ID_RE.match(tid):
        return None
    v = _view(tid)
    if v is None or not v.get("folder"):
        return None
    try:
        same = os.path.samefile(v["folder"], folder)
    except OSError:
        same = os.path.normcase(os.path.abspath(v["folder"])) == os.path.normcase(os.path.abspath(folder))
    return v if same else None


ART_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "art")


def _cached_icon(tid):
    if not re.fullmatch(r"[A-Z0-9]{4,16}", tid or ""):
        return ""
    path = os.path.join(ART_DIR, tid, "icon.png")
    return path if os.path.isfile(path) else ""


def _game_entry(tid, g, dev):
    best = choose_source(g, dev)
    job = _view(tid)
    marker = g["marker"]
    folder = g["folder"]
    is_built = bool(folder) and built(folder, marker, dev["os"])
    if best is None:
        comp = {"status": "nosource", "reason": "No drive, folder or PC library has this game's data", "prebuilt": False}
    else:
        comp = compat(g, dev, best, job)
    ok = comp["status"] == "installable"
    blocked = comp["status"] in ("unsupported", "needs-newer")
    upd = update_reason(marker, best, dev) if is_built else None
    usable = best is not None and (comp["prebuilt"] or dev["toolchain"]["present"])
    if job and job["state"] == "installing":
        state = "installing"
    elif job and job["state"] == "failed" and ok:
        state = "failed"
    elif is_built:
        state = "installed"
    elif blocked:
        state = "unsupported"
    else:
        state = "notinstalled"
    if state == "installing":
        action = {"id": "cancel", "label": job["message"], "enabled": True, "cancel": True}
    elif state == "failed":
        action = {"id": "retry", "label": job["message"], "enabled": usable, "retry": True}
    elif blocked:
        label = ("Needs a newer AnyPS5 (%s)" if comp["status"] == "needs-newer" else "Not supported (%s)") % comp["reason"]
        action = {"id": "none", "label": label, "enabled": False}
    elif not usable:
        action = {"id": "none", "label": "Installed" if is_built else "No game data", "enabled": False}
    elif is_built:
        action = {"id": "update", "label": "Update", "enabled": True} if upd else {"id": "reinstall", "label": "Reinstall", "enabled": True}
    else:
        action = {"id": "install", "label": "Install", "enabled": True}
    action.setdefault("retry", False)
    action.setdefault("cancel", False)
    action["reason"] = comp["reason"] if not ok else (job.get("error") if state == "failed" else upd)
    mt = (marker or {}).get("toolchain") or {}
    build = None
    if is_built and marker:
        build = {"toolchain": mt.get("commit"), "toolchainDate": mt.get("date"), "gameVersion": _text(marker.get("version")),
                 "builtFor": marker.get("builtFor"), "prebuilt": bool(marker.get("prebuilt")) or "toolchain" not in marker,
                 "installed": marker.get("installed"), "source": (marker.get("source") or {}).get("type")}
    sources = [{k: s.get(k) for k in ("type", "kind", "version", "size", "path", "url", "label") if s.get(k) not in (None, "")}
               for s in sorted(g["sources"], key=_rank, reverse=True)]
    title = g.get("localTitle") or g.get("remoteTitle") or g.get("mediaTitle") or tid
    return {"titleId": tid, "id": tid, "title": title, "version": (best or {}).get("version") or "",
            "size": (best or {}).get("size"), "icon": g["icon"], "iconFile": g["iconFile"] or _cached_icon(tid), "sources": sources,
            "source": (best or {}).get("type"), "kind": (best or {}).get("kind") or ("dump" if best else ""),
            "remote": any(s["type"] == "server" for s in g["sources"]), "folder": folder, "installed": is_built,
            "compat": comp, "build": build, "update": bool(upd), "updateReason": upd, "state": state,
            "phase": job.get("phase") if state == "installing" else None,
            "percent": job["percent"] if state == "installing" else None, "message": action["label"], "action": action, "job": job}


def list_games(args):
    dev = device()
    url, error, games, roots = _gather(args, dev, bool(args.get("refresh")))
    out = [_game_entry(tid, g, dev) for tid, g in games.items()]
    out.sort(key=lambda e: (e["title"].lower(), e["titleId"]))
    return {"server": url or "", "error": error, "root": games_root(), "device": _public_device(dev),
            "media": [r["path"] for r in roots], "games": out}


def _safe_name(title, tid):
    name = re.sub(r"\s+", " ", BAD_NAME.sub(" ", "%s (%s)" % (title, tid))).strip(" .")[:120].strip(" .")
    return name or tid


def _dest(folder, prefix, rel):
    if not isinstance(rel, str):
        return None
    parts = rel.split("/")
    if any(p in ("", ".", "..") or "\\" in p or ":" in p or "\x00" in p for p in parts):
        return None
    full = os.path.abspath(os.path.join(folder, *(prefix + parts)))
    return full if os.path.commonpath([folder, full]) == folder and full != folder else None


def _item(folder, prefix, remote, rel, size, mtime, src=None):
    dest = _dest(folder, prefix, rel) if isinstance(size, int) and size >= 0 else None
    if dest is None:
        raise RuntimeError("The game source has an unsafe file entry: %r" % (remote,))
    return {"path": remote, "rel": rel, "dest": dest, "size": size, "mtime": mtime if isinstance(mtime, (int, float)) else None, "src": src}


def _write_game_env(folder, env):
    path = os.path.join(folder, "game-env.txt")
    if not isinstance(env, dict) or os.path.exists(path):
        return False
    lines = ["%s=%s" % (k, v) for k, v in sorted(env.items())
             if isinstance(k, str) and isinstance(v, str) and k.startswith(("ANYPS5_", "APS5_")) and k.replace("_", "").isalnum()
             and "\n" not in v and "\r" not in v and "\x00" not in v and len(v) < 512]
    if not lines:
        return False
    try:
        os.makedirs(folder, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        return False
    return True


def _plan_server(url, tid, folder, dev):
    manifest = fetch_json(url, "/manifest/%s.json%s" % (urllib.parse.quote(tid), _query(dev)))
    files = manifest.get("files")
    if not isinstance(files, list):
        raise RuntimeError("The library server sent no file list")
    kind = manifest.get("kind")
    prebuilt = kind == "game" and dev["os"] == "windows" and dev["arch"] == "x86_64"
    plan = []
    for f in files:
        path = f.get("path") if isinstance(f, dict) else None
        if kind == "game" and not prebuilt:
            if not isinstance(path, str) or not path.startswith("app0/") or _tool_made(path):
                continue
            rel = path[len("app0/"):]
        else:
            rel = path
        plan.append(_item(folder, [] if prebuilt else ["app0"], path, rel, f.get("size") if isinstance(f, dict) else None,
                          f.get("mtime") if isinstance(f, dict) else None))
    if not prebuilt and not any(i["rel"] == "sce_sys/param.json" for i in plan):
        raise RuntimeError("The library server has no PS5 game data (sce_sys/param.json) for " + tid)
    return plan, prebuilt, manifest


def _same(item):
    try:
        st = os.stat(item["dest"])
    except OSError:
        return False
    if st.st_size != item["size"]:
        return False
    m, d = item["mtime"], st.st_mtime
    if m is None or abs(d - m) < 0.01:
        return True
    return d == int(d) and m != int(m) and abs(d - m) <= 2


def _have(item):
    if _same(item):
        return item["size"]
    if item["src"] is not None:
        return 0
    try:
        return min(os.path.getsize(item["dest"] + PART), item["size"])
    except OSError:
        return 0


def _free(path):
    while not os.path.exists(path):
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return shutil.disk_usage(path).free


def _gib(n):
    return "%.1f GB" % (n / 1e9)


def install(args):
    tid = _title_id(args)
    ev = threading.Event()
    with _shared.lock:
        job = _shared.jobs.get(tid)
        busy = job is not None and (job[0] is None or job[0].is_alive())
        if not busy:
            _shared.jobs[tid] = (None, ev)
    if busy:
        return _view(tid) or {"titleId": tid, "id": tid, "state": "installing", "running": True}
    try:
        dev = device()
        url, error, games, _ = _gather(args, dev, True)
        g = games.get(tid)
        want = _text(args.get("source"))
        src = choose_source(g, dev, want) if g else None
        if src is None:
            if error:
                raise RuntimeError(error)
            if not url and not g:
                raise RuntimeError("No library server is set and no drive or folder has this game. Start server.py on the PC and enter its address, or plug in a drive with the game.")
            raise RuntimeError("No source has the game %s" % tid)
        comp = compat(g, dev, src)
        if comp["status"] != "installable":
            raise RuntimeError(comp["reason"])
        title = g.get("localTitle") or src.get("title") or g.get("remoteTitle") or g.get("mediaTitle") or tid
        folder = g["folder"] or os.path.join(games_root(), _safe_name(title, tid))
        prebuilt, prune, game_env = False, False, None
        if src["type"] == "server":
            plan, prebuilt, manifest = _plan_server(src["url"], tid, folder, dev)
            game_env = manifest.get("env")
            title = g.get("localTitle") or _text(manifest.get("title")) or title
            version = _text(manifest.get("version")) or src.get("version") or ""
            prune = not prebuilt
        elif src["type"] == "media":
            plan = [_item(folder, ["app0"], f["path"], f["path"], f["size"], f["mtime"], f["src"]) for f in walk_files(src["path"])]
            version, prune = src.get("version") or "", True
        else:
            plan, version = [], src.get("version") or ""
        source = {"type": src["type"], "version": version, "kind": "game" if prebuilt else "dump"}
        source.update({"url": src["url"]} if src["type"] == "server" else {"path": src["path"]})
        source_key = "%s:%s" % (src["type"], source.get("url") or source.get("path"))
        prev = _read_json(_state_path(tid))
        if prev and prev.get("titleId") == tid and (prev.get("version"), prev.get("sourceKey")) != (version, source_key):
            for i in plan:
                try:
                    os.remove(i["dest"] + PART)
                except OSError:
                    pass
        total = sum(i["size"] for i in plan)
        have = sum(_have(i) for i in plan)
        free = _free(folder)
        if total - have + RESERVE > free:
            raise RuntimeError("Not enough free space: %s needed, %s free" % (_gib(total - have + RESERVE), _gib(free)))
        tc = dev["toolchain"]
        state = {"titleId": tid, "title": title, "kind": source["kind"], "server": src.get("url") or "", "folder": folder,
                 "source": source, "sourceKey": source_key, "version": version, "prebuilt": prebuilt, "build": not prebuilt,
                 "state": "installing", "phase": "copying", "total": total, "done": have, "files": len(plan), "file": 0,
                 "current": "", "copied": 0, "buildPercent": 0.0, "waiting": False, "error": None, "log": _log_path(tid),
                 "toolchainCommit": tc["commit"], "requires": None, "needsNewer": None,
                 "started": time.time(), "updated": time.time()}
        _write_game_env(folder, game_env)
        _write_json(_state_path(tid), state)
        th = threading.Thread(target=_worker, args=(tid, plan, prune, state, ev, dev), daemon=True, name="collection-" + tid)
        with _shared.lock:
            _shared.jobs[tid] = (th, ev)
        th.start()
    except BaseException:
        with _shared.lock:
            if _shared.jobs.get(tid, (None, None))[1] is ev:
                del _shared.jobs[tid]
        raise
    return _view(tid)


def _fetch(url, tid, item, ev, add, query):
    dest, size, part = item["dest"], item["size"], item["dest"] + PART
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if _same(item):
        return
    if size == 0:
        open(part, "wb").close()
    for attempt in range(ATTEMPTS):
        have = os.path.getsize(part) if os.path.exists(part) else 0
        if have > size:
            os.remove(part)
            add(-have)
            have = 0
        if have == size and os.path.exists(part):
            break
        try:
            with _open(_file_url(url, tid, item["path"], query), {"Range": "bytes=%d-" % have} if have else None) as r:
                if have and r.status != 206:
                    add(-have)
                    have = 0
                with open(part, "ab" if have else "wb") as f:
                    while True:
                        if ev.is_set():
                            raise Cancelled()
                        chunk = r.read1(CHUNK)
                        if not chunk:
                            break
                        if have + len(chunk) > size:
                            raise RuntimeError("The library server sent too much data for " + item["path"])
                        f.write(chunk)
                        have += len(chunk)
                        add(len(chunk))
            if have == size:
                break
        except urllib.error.HTTPError as e:
            e.close()
            if e.code == 416 and have:
                os.remove(part)
                add(-have)
                continue
            raise RuntimeError("The library server refused %s (HTTP %d)" % (item["path"], e.code))
        except (OSError, http.client.HTTPException) as e:
            if attempt == ATTEMPTS - 1:
                raise RuntimeError("Downloading %s failed: %s" % (item["path"], getattr(e, "reason", None) or e))
        if ev.wait(min(5, attempt + 1)):
            raise Cancelled()
    if not os.path.exists(part) or os.path.getsize(part) != size:
        raise RuntimeError("Downloading %s did not finish" % item["path"])
    os.replace(part, dest)
    if item["mtime"]:
        os.utime(dest, (item["mtime"], item["mtime"]))


def _copy(item, ev, add):
    dest, part = item["dest"], item["dest"] + PART
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    done = 0
    try:
        with open(item["src"], "rb") as s, open(part, "wb") as d:
            while True:
                if ev.is_set():
                    raise Cancelled()
                chunk = s.read(CHUNK)
                if not chunk:
                    break
                d.write(chunk)
                done += len(chunk)
                add(len(chunk))
        os.replace(part, dest)
        if item["mtime"]:
            os.utime(dest, (item["mtime"], item["mtime"]))
    except Cancelled:
        add(-done)
        raise
    except OSError as e:
        add(-done)
        raise RuntimeError("Copying %s failed: %s" % (item["path"], e.strerror or e))
    if done != item["size"]:
        add(item["size"] - done)


def _prune(app0, plan):
    keep = {os.path.normcase(i["dest"]) for i in plan}
    for cur, dirs, files in os.walk(app0, topdown=False):
        for n in files:
            p = os.path.join(cur, n)
            if os.path.normcase(p) not in keep:
                os.remove(p)
        for n in dirs:
            p = os.path.join(cur, n)
            try:
                if _is_link(p):
                    continue
                os.rmdir(p)
            except OSError:
                pass


def _progress(line):
    m = STEP_RE.search(line)
    if m and int(m.group(2)) > 0:
        return min(100.0, 100.0 * int(m.group(1)) / int(m.group(2)))
    m = PCT_RE.search(line)
    if m and float(m.group(1)) <= 100:
        return float(m.group(1))
    return None


def _kill(p):
    try:
        if os.name == "nt":
            p.terminate()
        else:
            os.killpg(p.pid, signal.SIGTERM)
    except OSError:
        pass
    try:
        p.wait(5)
    except subprocess.TimeoutExpired:
        try:
            if os.name == "nt":
                p.kill()
            else:
                os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass


def _watch(p, ev):
    while p.poll() is None:
        if ev.wait(0.2):
            _kill(p)
            return


def _run(cmd, cwd, env, ev, log, on_progress):
    kw = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if os.name == "nt" else {"start_new_session": True}
    try:
        p = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kw)
    except OSError as e:
        raise RuntimeError("Could not start %s: %s" % (os.path.basename(cmd[-3]), e.strerror or e))
    watcher = threading.Thread(target=_watch, args=(p, ev), daemon=True)
    watcher.start()
    last = ""
    for raw in iter(p.stdout.readline, b""):
        log.write(raw)
        log.flush()
        line = raw.decode("utf-8", "replace").strip()
        if not line:
            continue
        pct = _progress(line)
        if pct is not None:
            on_progress(pct)
        else:
            last = line
    p.stdout.close()
    rc = p.wait()
    watcher.join(6)
    return rc, last


def _remove(path):
    if _is_link(path):
        try:
            os.remove(path)
        except OSError:
            os.rmdir(path)
    elif os.path.isdir(path):
        shutil.rmtree(path)
    elif os.path.lexists(path):
        os.remove(path)


def _protected(name):
    return name.startswith(("_", ".")) or name.lower() in PROTECTED


def _merge_game_json(src, dst):
    new = _read_json(src) or {}
    cur = _read_json(dst) or {}
    merged = dict(cur)
    merged.update(new)
    for k in ("title", "titleId"):
        if cur.get(k) and not new.get(k):
            merged[k] = cur[k]
    _write_json(dst, merged)


def _adopt_built(built, kept):
    moved = 0
    for cur, dirs, files in os.walk(built):
        rel = os.path.relpath(cur, built)
        for name in files:
            src = os.path.join(cur, name)
            dst = os.path.normpath(os.path.join(kept, rel, name))
            try:
                if os.path.islink(src):
                    continue
                a = os.stat(src)
                try:
                    b = os.stat(dst)
                    if (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino):
                        continue
                except OSError:
                    pass
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                os.replace(src, dst)
                moved += 1
            except OSError:
                continue
    return moved


def _tool_made(rel):
    return isinstance(rel, str) and rel.lower().endswith((".guest.prx", ".esbak"))


def _move_outputs(folder, stage, old, note):
    out = []
    for n in sorted(os.listdir(stage)):
        src, dst = os.path.join(stage, n), os.path.join(folder, n)
        if n == "game.json":
            _merge_game_json(src, dst)
            continue
        if n == BUILD_RESULT:
            continue
        if _protected(n):
            if n == "app0" and os.path.isdir(src) and os.path.isdir(dst):
                note("kept the game folder's own app0 and took %d built files from the build output" % _adopt_built(src, dst))
            else:
                note("kept the game folder's own %s; the build output %s was not used" % (n, n))
            continue
        _remove(dst)
        os.replace(src, dst)
        out.append(n)
    for n in old if isinstance(old, list) else []:
        if _safe_rel(n) and n not in out and not _protected(n) and n != "game.json":
            _remove(os.path.join(folder, n))
    return out


def _command(wrapper, app0, stage):
    if wrapper.lower().endswith(".py"):
        return [sys.executable, wrapper, app0, stage]
    return [wrapper, app0, stage]


def _build(tid, state, ev, save, dev, log):
    folder = state["folder"]
    app0 = os.path.join(folder, "app0")
    stage = os.path.join(folder, STAGE)
    tc = dev["toolchain"]
    if not os.path.isfile(os.path.join(app0, "sce_sys", "param.json")):
        raise RuntimeError("The game data has no sce_sys/param.json")
    if not tc["wrapper"]:
        raise RuntimeError("The AnyPS5 tools are not installed on this system (%s)" % tc["path"])
    state.update({"phase": "building", "buildPercent": 0.0, "waiting": True, "current": ""})
    save(True)
    while not _shared.build_lock.acquire(timeout=0.2):
        if ev.is_set():
            raise Cancelled()
    try:
        state["waiting"] = False
        save(True)
        if os.path.lexists(stage):
            _remove(stage)
        os.makedirs(stage)

        def progress(pct):
            state["buildPercent"] = max(state["buildPercent"], min(100.0, pct))
            save()

        env = dict(os.environ, ANYPS5_TOOLCHAIN=tc["path"], ANYPS5_TITLE_ID=tid)
        log.write(("== build %s with %s (%s)\n" % (tid, tc["wrapper"], tc["commit"] or "unknown commit")).encode("utf-8"))
        rc, last = _run(_command(tc["wrapper"], app0, stage), folder, env, ev, log, progress)
        if ev.is_set():
            raise Cancelled()
        result = _read_json(os.path.join(stage, BUILD_RESULT)) or {}
        req = result.get("requires")
        state["requires"] = [str(f).lower() for f in req if isinstance(f, str)] if isinstance(req, list) else None
        if result.get("needsNewer"):
            state["needsNewer"] = _text(result.get("needsNewer")) or _text(result.get("error")) or "This game needs a newer AnyPS5"
            raise RuntimeError(state["needsNewer"])
        if rc != 0 or result.get("ok") is False:
            last = re.sub(r"^ERROR:\s*", "", last)
            raise RuntimeError(_text(result.get("error")) or ("Building failed: " + last if last else "Building failed (exit code %d)" % rc))
        miss = [f for f in state["requires"] or [] if has_flag(dev["_cpu"], f) is False]
        if miss:
            raise RuntimeError("This game needs %s and this CPU has none" % _flag_names(miss))
        marker = _read_json(os.path.join(folder, MARKER)) or {}

        def note(msg):
            log.write(("== " + msg + "\n").encode("utf-8"))

        outputs = _move_outputs(folder, stage, marker.get("outputs"), note)
        if not outputs:
            raise RuntimeError("Building finished but produced no program")
        state["buildPercent"] = 100.0
        return outputs
    finally:
        _shared.build_lock.release()
        if os.path.lexists(stage):
            shutil.rmtree(stage, ignore_errors=True)


def _worker(tid, plan, prune, state, ev, dev):
    last = [0.0]

    def save(force=False):
        now = time.monotonic()
        if force or now - last[0] >= 0.5:
            state["updated"] = time.time()
            _write_json(_state_path(tid), state)
            last[0] = now

    def add(n):
        state["done"] = max(0, state["done"] + n)
        save()

    log = None
    try:
        os.makedirs(state_dir(), exist_ok=True)
        log = open(_log_path(tid), "wb")
        log.write(("== install %s from %s %s\n" % (tid, state["source"]["type"], state["source"].get("url") or state["source"].get("path"))).encode("utf-8"))
        folder = state["folder"]
        os.makedirs(folder, exist_ok=True)
        query = _query(dev)
        for i, item in enumerate(plan):
            state["file"], state["current"] = i + 1, item["path"]
            if _same(item):
                continue
            save()
            if item["src"] is not None:
                _copy(item, ev, add)
            else:
                _fetch(state["source"]["url"], tid, item, ev, add, query)
            state["copied"] += 1
        state["done"] = state["total"]
        if prune and plan:
            _prune(os.path.join(folder, "app0"), plan)
        log.write(("== copied %d of %d files\n" % (state["copied"], len(plan))).encode("utf-8"))
        if not os.path.exists(os.path.join(folder, "game.json")) and not state["prebuilt"]:
            _write_json(os.path.join(folder, "game.json"), {"title": state["title"], "titleId": tid})
        tc = dev["toolchain"]
        marker = {"titleId": tid, "title": state["title"], "version": state["version"], "source": state["source"],
                  "builtFor": {"os": dev["os"], "arch": dev["arch"]}, "installed": time.time()}
        if state["prebuilt"]:
            marker.update({"prebuilt": True, "server": state["server"]})
        else:
            marker.update({"prebuilt": False, "outputs": _build(tid, state, ev, save, dev, log), "requires": state["requires"],
                           "toolchain": {"commit": tc["commit"], "date": tc["date"], "cpuBaseline": tc["cpuBaseline"], "avxLowering": tc["avxLowering"]}})
        _write_json(os.path.join(folder, MARKER), marker)
        state.update({"state": "installed", "current": "", "finished": time.time()})
    except Cancelled:
        state.update({"state": "cancelled", "current": ""})
    except Exception as e:
        state.update({"state": "failed", "error": str(e) or e.__class__.__name__})
    finally:
        if state["state"] == "installing":
            state["state"] = "failed"
        try:
            if log is not None:
                log.write(("== %s %s\n" % (state["state"], state.get("error") or "")).encode("utf-8"))
                log.close()
            save(True)
        finally:
            with _shared.lock:
                if _shared.jobs.get(tid, (None, None))[1] is ev:
                    del _shared.jobs[tid]


def status(args):
    if args.get("titleId", args.get("id")) is None:
        out = []
        try:
            names = sorted(os.listdir(state_dir()))
        except OSError:
            names = []
        for n in names:
            if n.endswith(".json") and ID_RE.match(n[:-5]):
                v = _view(n[:-5])
                if v:
                    out.append(v)
        return {"jobs": out}
    tid = _title_id(args)
    return _view(tid) or {"titleId": tid, "id": tid, "state": "none", "running": False}


def _stop(tid, wait=15.0):
    with _shared.lock:
        job = _shared.jobs.get(tid)
    if job is None:
        return True
    job[1].set()
    if job[0] is not None:
        job[0].join(wait)
    return not _alive(tid)


def cancel(args):
    tid = _title_id(args)
    if not _stop(tid):
        raise RuntimeError("The install did not stop in time")
    st = _read_json(_state_path(tid))
    if st and st.get("state") in ("installing", "downloading"):
        st["state"] = "cancelled"
        _write_json(_state_path(tid), st)
    return status({"titleId": tid})


def delete(args):
    tid = _title_id(args)
    if not _stop(tid):
        raise RuntimeError("The install did not stop in time")
    root = os.path.realpath(games_root())
    folders = set(_folders_for(tid))
    st = _read_json(_state_path(tid)) or {}
    if isinstance(st.get("folder"), str):
        folders.add(st["folder"])
    deleted = []
    for folder in sorted(folders):
        real = os.path.realpath(folder)
        if os.path.dirname(real) != root or _is_link(folder) or not os.path.isdir(real):
            continue
        shutil.rmtree(real)
        deleted.append(folder)
    for path in (_state_path(tid), _log_path(tid)):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
    return {"titleId": tid, "deleted": deleted}


def server_action(args):
    return {"server": server(args) or ""}


def device_action(args):
    return _public_device(device())


ACTIONS = {"list": list_games, "install": install, "reinstall": install, "update": install, "retry": install,
           "status": status, "cancel": cancel, "delete": delete, "server": server_action, "device": device_action}


def handle(action, args):
    fn = ACTIONS.get(action)
    if fn is None:
        raise NotImplementedError("unknown collection action " + str(action))
    return fn(args if isinstance(args, dict) else {})
