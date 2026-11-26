import argparse
import email.utils
import fnmatch
import ipaddress
import json
import os
import re
import socket
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")
GAME_EXCLUDES = ("*.log", "*.old", "*.old-*", "*.part", "*.tmp", "crash-logs", "shader_cache*", "test-shots", "ai-runs")
ICONS = ("app0/sce_sys/icon0.png", "sce_sys/icon0.png")
FLAVORS = ("game", "dump")
WINDOWS_OS = ("windows", "win", "win32", "win64", "nt")
WINDOWS_ARCH = ("", "x86_64", "amd64", "x64", "x86-64")
FLAVOR_QUERY = {"game": "?os=windows&arch=x86_64", "dump": ""}
DUMP_DEPTH = 4
CHUNK = 1 << 20


def read_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            value = json.load(f)
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def param_title(param):
    loc = param.get("localizedParameters") if isinstance(param, dict) else None
    if not isinstance(loc, dict):
        return None
    lang = loc.get("defaultLanguage")
    entry = loc.get(lang) if isinstance(lang, str) else None
    if not isinstance(entry, dict) or not entry.get("titleName"):
        entry = next((v for v in loc.values() if isinstance(v, dict) and v.get("titleName")), None)
    name = entry.get("titleName") if isinstance(entry, dict) else None
    return name.strip() if isinstance(name, str) and name.strip() else None


def system_version(param):
    v = param.get("requiredSystemSoftwareVersion") if isinstance(param, dict) else None
    return v if isinstance(v, str) else ""


def version_key(text):
    if not isinstance(text, str):
        return ()
    return tuple(int(p) for p in re.findall(r"\d+", text))


def is_link(path):
    return os.path.islink(path) or getattr(os.path, "isjunction", lambda p: False)(path)


def allowed(kind, top, name):
    if kind != "game" or not top:
        return True
    if name.startswith(("_", ".")):
        return False
    low = name.lower()
    return not any(fnmatch.fnmatchcase(low, p) for p in GAME_EXCLUDES)


def walk_files(root, kind):
    out = []
    for cur, dirs, files in os.walk(root):
        rel_dir = os.path.relpath(cur, root)
        top = rel_dir == "."
        dirs[:] = sorted(d for d in dirs if allowed(kind, top, d) and not is_link(os.path.join(cur, d)))
        for name in sorted(files):
            full = os.path.join(cur, name)
            if not allowed(kind, top, name) or is_link(full):
                continue
            try:
                st = os.stat(full)
            except OSError:
                continue
            rel = name if top else rel_dir.replace(os.sep, "/") + "/" + name
            out.append({"path": rel, "size": st.st_size, "mtime": int(st.st_mtime)})
    return out


def split_rel(rel):
    parts = rel.split("/")
    if any(p in ("", ".", "..") or "\\" in p or ":" in p or "\x00" in p for p in parts):
        return None
    return parts


def rel_allowed(kind, parts):
    return all(allowed(kind, i == 0, p) for i, p in enumerate(parts))


ENV_PREFIXES = ("ANYPS5_", "APS5_")


def read_env(path):
    out = {}
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            lines = f.read().splitlines()
    except (OSError, ValueError):
        return out
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith(ENV_PREFIXES) and key.replace("_", "").isalnum() and len(value) < 512:
            out[key] = value.strip()
    return out


def game_entry(folder):
    meta = read_json(os.path.join(folder, "game.json"))
    if meta is None:
        return None
    param = read_json(os.path.join(folder, "app0", "sce_sys", "param.json")) or {}
    tid = meta.get("titleId") or param.get("titleId")
    if not isinstance(tid, str) or not ID_RE.match(tid):
        return None
    title = meta.get("title") if isinstance(meta.get("title"), str) and meta.get("title").strip() else None
    app0 = os.path.join(folder, "app0")
    dump_root = app0 if param.get("titleId") == tid and not is_link(app0) else ""
    return {"titleId": tid, "title": title or param_title(param) or os.path.basename(folder), "kind": "game",
            "version": param.get("contentVersion") if isinstance(param.get("contentVersion"), str) else "", "root": folder,
            "dumpRoot": dump_root, "requiredSystemVersion": system_version(param)}


def dump_entry(folder):
    param = read_json(os.path.join(folder, "sce_sys", "param.json"))
    if param is None:
        return None
    tid = param.get("titleId")
    if not isinstance(tid, str) or not ID_RE.match(tid):
        return None
    return {"titleId": tid, "title": param_title(param) or os.path.basename(folder), "kind": "dump",
            "version": param.get("contentVersion") if isinstance(param.get("contentVersion"), str) else "", "root": folder,
            "requiredSystemVersion": system_version(param)}


def find_dumps(root, depth=0):
    out = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return out
    for name in names:
        path = os.path.join(root, name)
        if name.startswith(("_", ".")) or not os.path.isdir(path) or is_link(path):
            continue
        if os.path.isfile(os.path.join(path, "sce_sys", "param.json")):
            entry = dump_entry(path)
            if entry:
                out.append(entry)
        elif depth + 1 < DUMP_DEPTH:
            out.extend(find_dumps(path, depth + 1))
    return out


def find_games(root):
    out = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return out
    for name in names:
        path = os.path.join(root, name)
        if name.startswith(("_", ".")) or not os.path.isdir(path) or is_link(path):
            continue
        entry = game_entry(path)
        if entry:
            out.append(entry)
    return out


def rank(entry):
    return (0 if entry["kind"] == "game" else 1, tuple(-v for v in version_key(entry["version"])), entry["root"].lower())


def dump_rank(entry):
    return (tuple(-v for v in version_key(entry["version"])), 0 if entry.get("via") != "game" else 1, entry["root"].lower())


def client_flavor(query):
    q = urllib.parse.parse_qs(query or "")
    name = (q.get("os") or [""])[0].strip().lower()
    arch = (q.get("arch") or [""])[0].strip().lower()
    if name in WINDOWS_OS and arch in WINDOWS_ARCH:
        return "game"
    return "dump"


def candidates(group, flavor):
    if flavor == "game":
        return sorted(group, key=rank)
    out = []
    for e in group:
        if e["kind"] == "dump":
            out.append(dict(e, via="dump"))
        elif e.get("dumpRoot"):
            out.append(dict(e, kind="dump", root=e["dumpRoot"], via="game"))
    return sorted(out, key=dump_rank)


def derived_files(walked, root):
    parent, name = os.path.split(root)
    files = walked.get((parent, "game"))
    if files is None:
        return None
    prefix = name + "/"
    return [dict(f, path=f["path"][len(prefix):]) for f in files if f["path"].startswith(prefix)]


class Library:
    def __init__(self, games_roots, dump_roots, ttl=600):
        self.games_roots = [os.path.abspath(p) for p in games_roots]
        self.dump_roots = [os.path.abspath(p) for p in dump_roots]
        self.ttl = ttl
        self.lock = threading.Lock()
        self.scan_lock = threading.Lock()
        self.entries = None
        self.stamp = 0.0
        self.refreshing = False
        self.manifests = {}

    def scan(self):
        found = []
        for root in self.games_roots:
            found.extend(find_games(root))
        for root in self.dump_roots:
            found.extend(find_dumps(root))
        groups = {}
        for e in found:
            groups.setdefault(e["titleId"], []).append(e)
        entries = {f: {} for f in FLAVORS}
        for tid, group in groups.items():
            walked = {}
            for flavor in FLAVORS:
                cands = candidates(group, flavor)
                if not cands:
                    continue
                best = dict(cands[0])
                files = derived_files(walked, best["root"]) if best.get("via") == "game" else None
                key = (best["root"], best["kind"])
                if files is None:
                    files = walked[key] if key in walked else walk_files(best["root"], best["kind"])
                walked[key] = files
                best["size"] = sum(f["size"] for f in files)
                best["files"] = len(files)
                paths = {f["path"] for f in files}
                best["icon"] = next(("/game/%s/%s%s" % (tid, p, FLAVOR_QUERY[flavor]) for p in ICONS if p in paths), "")
                best["kinds"] = sorted({e["kind"] for e in group})
                best["copies"] = len(group)
                best["flavor"] = flavor
                entries[flavor][tid] = best
                with self.lock:
                    self.manifests[(flavor, tid)] = (time.monotonic(), best["root"], files)
        return entries

    def refresh(self):
        with self.scan_lock:
            try:
                entries = self.scan()
            finally:
                with self.lock:
                    self.refreshing = False
            with self.lock:
                self.entries = entries
                self.stamp = time.monotonic()
        return entries

    def current(self):
        with self.lock:
            entries, stale = self.entries, time.monotonic() - self.stamp > self.ttl
            start = entries is not None and stale and not self.refreshing
            if start:
                self.refreshing = True
        if entries is None:
            with self.scan_lock:
                with self.lock:
                    entries = self.entries
            return entries if entries is not None else self.refresh()
        if start:
            threading.Thread(target=self.refresh, daemon=True).start()
        return entries

    def catalog(self, flavor="dump"):
        games = []
        for e in self.current()[flavor].values():
            games.append({k: e[k] for k in ("titleId", "title", "kind", "kinds", "copies", "version", "size", "files", "icon", "flavor", "requiredSystemVersion")})
        games.sort(key=lambda g: (g["title"].lower(), g["titleId"]))
        return {"generated": int(time.time()), "flavor": flavor, "games": games}

    def entry(self, tid, flavor="dump"):
        return self.current()[flavor].get(tid) if ID_RE.match(tid) else None

    def manifest(self, tid, flavor="dump"):
        e = self.entry(tid, flavor)
        if e is None:
            return None
        with self.lock:
            hit = self.manifests.get((flavor, tid))
        if hit is None or hit[1] != e["root"] or time.monotonic() - hit[0] > 60:
            files = walk_files(e["root"], e["kind"])
            with self.lock:
                self.manifests[(flavor, tid)] = (time.monotonic(), e["root"], files)
        else:
            files = hit[2]
        root = e["root"]
        home = root if e["kind"] == "game" else os.path.dirname(root) if os.path.basename(root).lower() == "app0" else ""
        return {"titleId": tid, "title": e["title"], "kind": e["kind"], "version": e["version"], "flavor": flavor,
                "size": sum(f["size"] for f in files), "files": files,
                "env": read_env(os.path.join(home, "game-env.txt")) if home else {}}

    def resolve(self, tid, rel, flavor="dump"):
        e = self.entry(tid, flavor)
        parts = split_rel(rel) if e else None
        if parts is None or not rel_allowed(e["kind"], parts):
            return None
        root = os.path.realpath(e["root"])
        full = os.path.realpath(os.path.join(root, *parts))
        try:
            if os.path.commonpath([root, full]) != root or full == root:
                return None
        except ValueError:
            return None
        real_parts = os.path.relpath(full, root).split(os.sep)
        if not rel_allowed(e["kind"], real_parts) or not os.path.isfile(full):
            return None
        return full


def parse_range(header, size):
    m = RANGE_RE.match(header.strip())
    if not m or (m.group(1) == "" and m.group(2) == ""):
        return "ignore"
    a, b = m.group(1), m.group(2)
    if a == "":
        n = int(b)
        if n == 0 or size == 0:
            return None
        return max(0, size - n), size - 1
    start = int(a)
    end = min(int(b), size - 1) if b else size - 1
    if start >= size or start > end:
        return None
    return start, end


class Handler(BaseHTTPRequestHandler):
    server_version = "AnyPS5Library/1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if self.server.verbose:
            sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def do_GET(self):
        self.route(True)

    def do_HEAD(self):
        self.route(False)

    def refuse(self):
        self.reply(405, {"error": "this server is read-only"}, True, {"Allow": "GET, HEAD"})

    do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = refuse

    def reply(self, code, value, body=True, extra=None):
        data = json.dumps(value, indent=1).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(data)

    def host_ok(self):
        host = (self.headers.get("Host") or "").strip()
        if host.startswith("["):
            name = host[1:host.find("]")] if "]" in host else ""
        else:
            name = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
        try:
            ipaddress.ip_address(name)
            return True
        except ValueError:
            return name.lower().rstrip(".") in self.server.names

    def route(self, body):
        if not self.host_ok():
            return self.reply(403, {"error": "unknown Host header"}, body)
        split = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(split.path)
        flavor = client_flavor(split.query)
        lib = self.server.library
        if path in ("/", ""):
            return self.reply(200, {"server": "AnyPS5 library", "catalog": "/catalog.json", "flavor": flavor}, body)
        if path == "/catalog.json":
            return self.reply(200, lib.catalog(flavor), body)
        if path.startswith("/manifest/") and path.endswith(".json"):
            m = lib.manifest(path[len("/manifest/"):-len(".json")], flavor)
            return self.reply(200, m, body) if m else self.reply(404, {"error": "unknown game"}, body)
        if path.startswith("/game/"):
            tid, _, rel = path[len("/game/"):].partition("/")
            full = lib.resolve(tid, rel, flavor) if rel else None
            if full is None:
                return self.reply(404, {"error": "not found"}, body)
            return self.send_file(full, body)
        return self.reply(404, {"error": "not found"}, body)

    def send_file(self, full, body):
        try:
            f = open(full, "rb")
        except OSError:
            return self.reply(404, {"error": "not found"}, body)
        with f:
            st = os.fstat(f.fileno())
            size = st.st_size
            span = (0, size - 1)
            code = 200
            header = self.headers.get("Range")
            if header:
                r = parse_range(header, size)
                if r is None:
                    return self.reply(416, {"error": "range not satisfiable"}, body, {"Content-Range": "bytes */%d" % size})
                if r != "ignore":
                    span, code = r, 206
            length = max(0, span[1] - span[0] + 1)
            self.send_response(code)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Last-Modified", email.utils.formatdate(st.st_mtime, usegmt=True))
            if code == 206:
                self.send_header("Content-Range", "bytes %d-%d/%d" % (span[0], span[1], size))
            self.end_headers()
            if not body or length == 0:
                return
            f.seek(span[0])
            left = length
            try:
                while left > 0:
                    chunk = f.read(min(CHUNK, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                self.close_connection = True
            if left:
                self.close_connection = True


class LibraryServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, library, bind="0.0.0.0", port=8791, allow_hosts=(), verbose=False):
        self.library = library
        self.verbose = verbose
        host = socket.gethostname().lower()
        self.names = {"localhost", host, host + ".local", socket.getfqdn().lower()} | {h.lower() for h in allow_hosts}
        super().__init__((bind, int(port)), Handler)


def lan_address():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def env_list(name, default):
    value = os.environ.get(name)
    return [p for p in value.split(os.pathsep) if p] if value else default


def main(argv=None):
    ap = argparse.ArgumentParser(description="Read-only AnyPS5 game library server for the LAN: GET /catalog.json, "
                                             "GET /manifest/<titleId>.json, GET /game/<titleId>/<path> (Range supported). "
                                             "Clients add ?os=<os>&arch=<arch>; only os=windows on x86_64 gets Windows builds, "
                                             "every other client gets the PS5 dump data (raw dump or a game's app0) to relink itself.")
    ap.add_argument("--games", action="append", help="AnyPS5 games folder with <Title>/game.json (repeatable)")
    ap.add_argument("--dumps", action="append", help="folder of PS5 dumps with sce_sys/param.json (repeatable)")
    ap.add_argument("--bind", default=os.environ.get("ANYPS5_LIBRARY_BIND") or "0.0.0.0")
    ap.add_argument("--port", type=int, default=int(os.environ.get("ANYPS5_LIBRARY_PORT") or 8791))
    ap.add_argument("--ttl", type=int, default=600, help="seconds before the catalog is rescanned in the background")
    ap.add_argument("--allow-host", action="append", default=[], help="extra Host header name to accept")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args(argv)
    games = a.games or env_list("ANYPS5_LIBRARY_GAMES", [r"D:\AnyPS5 Games"])
    dumps = a.dumps or env_list("ANYPS5_LIBRARY_DUMPS", [r"D:\ps5 games dumps from ps5"])
    library = Library(games, dumps, a.ttl)
    server = LibraryServer(library, a.bind, a.port, a.allow_host, a.verbose)
    print("AnyPS5 library on http://%s:%d (games %s; dumps %s)" % (lan_address() if a.bind == "0.0.0.0" else a.bind,
                                                                  server.server_address[1], "; ".join(games), "; ".join(dumps)), flush=True)
    threading.Thread(target=library.current, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
