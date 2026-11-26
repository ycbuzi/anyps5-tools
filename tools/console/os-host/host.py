#!/usr/bin/env python3
"""AnyPS5 OS host: serves the console screen on 127.0.0.1 and answers the page the way AnyPS5.exe does on Windows.

GET  /         files of the console folder (read-only, os-host itself is never served)
POST /sys      {area, action, args} -> areas/<area>.py handle(action, args)
POST /msg      page messages ({settings}, {state}, {sys}, {play}, ...)
GET  /events   Server-Sent Events, host -> page messages
"""
import argparse
import email.utils
import json
import logging
import logging.handlers
import mimetypes
import os
import queue
import re
import shutil
import sys
import tempfile
import threading
import time
import traceback
import types
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import session as game_session
CONSOLE = HERE.parent
AREA_NAME = re.compile(r"^[a-z]+$")
MAX_SYS_BODY = 1 << 20
MAX_MSG_BODY = 16 << 20
SSE_KEEPALIVE = 15.0

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8", ".htm": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8", ".md": "text/markdown; charset=utf-8",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
    ".webp": "image/webp", ".svg": "image/svg+xml", ".ico": "image/x-icon", ".bmp": "image/bmp",
    ".mp4": "video/mp4", ".webm": "video/webm", ".mkv": "video/x-matroska",
    ".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".wav": "audio/wav", ".m4a": "audio/mp4",
    ".woff": "font/woff", ".woff2": "font/woff2", ".ttf": "font/ttf", ".otf": "font/otf",
    ".wasm": "application/wasm",
}

UNAVAILABLE = {
    "pause": "Pausing the pipeline",
    "machines": "The machines page",
    "tab": "The work app",
}
IGNORED = ("home", "dnd", "downloads", "trophyOwner", "wake", "fullscreen", "exit")


def content_type(path):
    ext = path.suffix.lower()
    if ext in CONTENT_TYPES:
        return CONTENT_TYPES[ext]
    guess = mimetypes.guess_type(path.name)[0]
    return guess or "application/octet-stream"


def inside(child, parent):
    c = os.path.normcase(str(child))
    p = os.path.normcase(str(parent)).rstrip("\\/")
    return c == p or c.startswith(p + os.sep)


def write_json_atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def mount_point(path):
    p = os.path.realpath(str(path))
    while not os.path.ismount(p):
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    return p


def list_pads():
    pads = []
    try:
        text = Path("/proc/bus/input/devices").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return pads
    for block in text.split("\n\n"):
        name, handlers = "", ""
        for line in block.splitlines():
            if line.startswith("N: Name="):
                name = line[len("N: Name="):].strip().strip('"')
            elif line.startswith("H: Handlers="):
                handlers = line[len("H: Handlers="):]
        if name and re.search(r"\bjs\d+\b", handlers):
            pads.append(name)
    return pads


class Broker:
    def __init__(self):
        self.lock = threading.Lock()
        self.clients = set()
        self.closed = False

    def subscribe(self):
        q = queue.Queue(maxsize=256)
        with self.lock:
            if self.closed:
                q.put_nowait(None)
            else:
                self.clients.add(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            self.clients.discard(q)

    def publish(self, message):
        with self.lock:
            clients = list(self.clients)
        for q in clients:
            try:
                q.put_nowait(message)
            except queue.Full:
                self.unsubscribe(q)
                try:
                    q.get_nowait()
                    q.put_nowait(None)
                except (queue.Empty, queue.Full):
                    pass

    def close(self):
        with self.lock:
            self.closed = True
            clients = list(self.clients)
            self.clients.clear()
        for q in clients:
            try:
                q.put_nowait(None)
            except queue.Full:
                try:
                    q.get_nowait()
                    q.put_nowait(None)
                except (queue.Empty, queue.Full):
                    pass


class Areas:
    def __init__(self, folder, log):
        self.folder = Path(folder)
        self.log = log
        self.lock = threading.Lock()
        self.cache = {}

    def get(self, area):
        path = self.folder / (area + ".py")
        try:
            st = path.stat()
        except OSError:
            return None
        key = (st.st_mtime_ns, st.st_size)
        with self.lock:
            hit = self.cache.get(area)
            if hit and hit[0] == key:
                return hit[1]
            source = path.read_bytes()
            module = types.ModuleType("anyps5_area_" + area)
            module.__file__ = str(path)
            exec(compile(source, str(path), "exec"), module.__dict__)
            if not callable(getattr(module, "handle", None)):
                raise RuntimeError("areas/" + area + ".py has no handle(action, args)")
            self.cache[area] = (key, module)
            if hit:
                self.log.info("reloaded area %s", area)
            return module


class AnyPS5Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port=8790, games=None, data_dir=None, areas_dir=None, console_dir=None):
        self.console = Path(console_dir or CONSOLE).resolve()
        self.os_host = (self.console / "os-host").resolve()
        self.games = Path(os.path.expanduser(games or "~/AnyPS5 Games"))
        self.data = Path(data_dir or os.environ.get("ANYPS5_OS_DATA") or (HERE / "data"))
        self.data.mkdir(parents=True, exist_ok=True)
        self.settings_file = self.data / "anyps5-settings.json"
        self.settings_lock = threading.Lock()
        self.log = logging.getLogger("anyps5os.%x" % id(self))
        self.log.setLevel(logging.INFO)
        self.log.propagate = False
        self.log_handler = logging.handlers.RotatingFileHandler(str(self.data / "host.log"), maxBytes=1 << 20, backupCount=3, encoding="utf-8")
        self.log_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        self.log.addHandler(self.log_handler)
        self.areas = Areas(areas_dir or (HERE / "areas"), self.log)
        self.broker = Broker()
        self.session = game_session.GameSession(self.games, self.broker.publish, self.log, family=self.areas.get("family"),
                                                settings_file=str(self.settings_file), prepare=self.install_job)
        if os.name == "posix":
            threading.Thread(target=self.rescan, args=(False,), daemon=True).start()
        super().__init__(("127.0.0.1", int(port)), Handler)
        self.port = self.server_address[1]
        self.allowed_hosts = {"127.0.0.1:%d" % self.port, "localhost:%d" % self.port}
        self.allowed_origins = {"http://" + h for h in self.allowed_hosts}
        self.log.info("AnyPS5 OS host on http://127.0.0.1:%d (games %s)", self.port, self.games)

    def load_settings(self):
        with self.settings_lock:
            try:
                return json.loads(self.settings_file.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return None
            except (OSError, ValueError) as e:
                self.log.warning("settings file unreadable: %s", e)
                return None

    def save_settings(self, value):
        with self.settings_lock:
            write_json_atomic(self.settings_file, value)

    def drives(self):
        out, seen = [], set()
        candidates = [("/", "System")] if os.name == "posix" else []
        if self.games.is_dir():
            mp = mount_point(self.games)
            candidates.append((mp, "System" if mp == "/" else (os.path.basename(mp.rstrip("\\/")) or mp)))
        for path, label in candidates:
            try:
                dev = os.stat(path).st_dev
                if dev in seen:
                    continue
                usage = shutil.disk_usage(path)
            except OSError:
                continue
            seen.add(dev)
            out.append({"name": path, "label": label, "free": float(usage.free), "total": float(usage.total)})
        return out

    def state(self):
        try:
            build = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(__file__)))
        except OSError:
            build = ""
        return {
            "host": True, "platform": "linux", "settings": self.load_settings(), "paused": False,
            "pads": list_pads(), "build": build, "program": "AnyPS5 OS", "root": str(self.games),
            "playing": self.session.playing(), "drives": self.drives(),
            "handoff": True, "suspended": self.session.is_suspended(),
        }

    def install_job(self, folder, title_id):
        module = self.areas.get("collection")
        view = getattr(module, "job_view", None) if module is not None else None
        return view(str(folder), title_id) if view is not None else None

    def rescan(self, announce=True):
        try:
            games = game_session.scan_library(self.games, self.console, on_art=lambda: self.rescan(announce=False))
        except OSError as e:
            self.log.error("games scan failed: %s", e)
            if announce:
                self.broker.publish({"toast": "Could not scan the games folder: " + str(e)})
            return
        self.log.info("games scan: %d games", len(games))
        self.broker.publish({"library": True})
        if announce:
            self.broker.publish({"toast": "%d game%s found" % (len(games), "" if len(games) == 1 else "s")})

    def controller_env(self, user):
        try:
            module = self.areas.get("controller")
            if module is not None:
                return module.game_env(os.environ, user if isinstance(user, str) else "")
        except Exception:
            self.log.error("controller settings not applied to the game\n%s", traceback.format_exc())
        return os.environ

    def sys_call(self, area, action, args):
        if not isinstance(area, str) or not AREA_NAME.match(area):
            return {"ok": False, "error": "bad area name"}
        if not isinstance(action, str) or not action:
            return {"ok": False, "error": "bad action"}
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return {"ok": False, "error": "args must be an object"}
        try:
            module = self.areas.get(area)
            if module is None:
                return {"ok": False, "unsupported": True, "error": "This AnyPS5 OS host has no " + area + " module yet"}
            data = module.handle(action, args)
            json.dumps(data)
            return {"ok": True, "data": data}
        except NotImplementedError as e:
            return {"ok": False, "unsupported": True, "error": str(e) or "Not available in AnyPS5 OS"}
        except Exception as e:
            self.log.error("system %s/%s failed\n%s", area, action, traceback.format_exc())
            return {"ok": False, "error": str(e) or e.__class__.__name__}

    def on_message(self, msg):
        if not isinstance(msg, dict):
            self.log.info("ignored non-object message")
            return None
        if "sys" in msg:
            reply = self.sys_call(msg.get("sys"), msg.get("action"), msg.get("args"))
            idv = msg.get("id")
            reply["id"] = idv if isinstance(idv, (int, float, str)) else 0
            self.broker.publish({"sysReply": reply})
            return {"sysReply": reply}
        if "state" in msg:
            st = self.state()
            self.broker.publish(st)
            return st
        if "settings" in msg:
            value = msg["settings"]
            if not isinstance(value, dict):
                self.log.info("ignored settings that are not an object")
                return None
            try:
                self.save_settings(value)
            except OSError as e:
                self.log.error("settings not saved: %s", e)
                note = {"toast": "Settings not saved: " + str(e)}
                self.broker.publish(note)
                return note
            return {"saved": True}
        if "fullscreenStart" in msg:
            st = self.state()
            self.broker.publish(st)
            return st
        if "play" in msg:
            self.session.base_env = self.controller_env(msg.get("user") or "")
            note = self.session.play(msg.get("play"), msg.get("user") or "")
            if note and "toast" in note:
                note = dict(note, launch={"phase": "failed"})
                self.broker.publish(note)
            return note
        if "shot" in msg:
            folder = Path(self.console) / "captures"
            try:
                folder.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                return {"error": "captures folder: " + str(e)}
            note = {"screenshot": str(folder / time.strftime("shot-%Y%m%d-%H%M%S.png"))}
            self.broker.publish(note)
            return note
        if "open" in msg:
            name = msg.get("open")
            if not isinstance(name, str) or not re.fullmatch(r"[a-z_]{1,24}", name):
                return {"error": "open needs a screen name"}
            note = {"open": name}
            self.broker.publish(note)
            return note
        if "osd" in msg:
            o = msg.get("osd")
            if not isinstance(o, dict) or o.get("kind") not in ("volume", "brightness"):
                return {"error": "osd needs kind volume or brightness"}
            level = o.get("level")
            if isinstance(level, bool) or not isinstance(level, (int, float)):
                return {"error": "osd level must be a number"}
            note = {"osd": {"kind": o["kind"], "level": max(0, min(100, int(round(level)))), "muted": o.get("muted") is True}}
            self.broker.publish(note)
            return None
        if "closegame" in msg:
            threading.Thread(target=self.session.close, daemon=True).start()
            return None
        if "display" in msg:
            if msg.get("display") == "released":
                self.session.display_released()
            return None
        if "suspend" in msg:
            return self.session.suspend()
        if "resume" in msg:
            threading.Thread(target=self.session.resume, daemon=True).start()
            return None
        if "rescan" in msg:
            threading.Thread(target=self.rescan, daemon=True).start()
            return None
        if "power" in msg:
            note = self.session.power(msg.get("power"))
            if note:
                self.broker.publish(note)
            return note
        if "rest" in msg:
            note = self.session.rest()
            if note:
                self.broker.publish(note)
            return note
        for key, what in UNAVAILABLE.items():
            if key in msg:
                self.log.info("page asked for %s (not available)", key)
                note = {"toast": what + " is not available in AnyPS5 OS yet"}
                self.broker.publish(note)
                return note
        for key in IGNORED:
            if key in msg:
                self.log.info("page message %s ignored", key)
                return None
        self.log.info("unknown page message ignored: %s", ",".join(sorted(str(k) for k in msg))[:200])
        return None

    def server_close(self):
        self.broker.close()
        super().server_close()
        self.log.removeHandler(self.log_handler)
        self.log_handler.close()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "AnyPS5OS/1"
    sys_version = ""

    def log_message(self, fmt, *args):
        self.server.log.debug("%s %s", self.address_string(), fmt % args)

    def log_error(self, fmt, *args):
        self.server.log.warning("%s %s", self.address_string(), fmt % args)

    def guard(self, post):
        host = (self.headers.get("Host") or "").lower()
        if host not in self.server.allowed_hosts:
            self.send_error(403, "Host not allowed")
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin.lower() not in self.server.allowed_origins:
            self.send_error(403, "Origin not allowed")
            return False
        if post:
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype != "application/json":
                self.send_error(415, "Send application/json")
                return False
        return True

    def reply_json(self, value, status=200):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self, limit):
        if self.headers.get("Transfer-Encoding"):
            self.send_error(411, "Content-Length required")
            return None, False
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self.send_error(411, "Content-Length required")
            return None, False
        if length < 0 or length > limit:
            self.send_error(413, "Body too large")
            return None, False
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8")), True
        except (UnicodeDecodeError, ValueError):
            self.send_error(400, "Body is not JSON")
            return None, False

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        if path not in ("/sys", "/msg"):
            self.send_error(404)
            return
        if not self.guard(True):
            return
        body, good = self.read_json(MAX_SYS_BODY if path == "/sys" else MAX_MSG_BODY)
        if not good:
            return
        if path == "/sys":
            if not isinstance(body, dict):
                self.reply_json({"ok": False, "error": "send {area, action, args}"})
                return
            self.reply_json(self.server.sys_call(body.get("area"), body.get("action"), body.get("args")))
            return
        try:
            out = self.server.on_message(body)
        except Exception:
            self.server.log.error("page message failed\n%s", traceback.format_exc())
            self.reply_json({"ok": False, "error": "message failed"}, 500)
            return
        self.reply_json({"ok": True, "reply": out})

    def do_GET(self):
        self.get(True)

    def do_HEAD(self):
        self.get(False)

    def get(self, with_body):
        if not self.guard(False):
            return
        path = urllib.parse.urlsplit(self.path).path
        if path == "/events":
            if with_body:
                self.events()
            else:
                self.send_error(405)
            return
        if path in ("/sys", "/msg"):
            self.send_error(405)
            return
        target, status = self.resolve(path)
        if target is None:
            self.send_error(status)
            return
        self.send_file(target, with_body)

    def resolve(self, raw):
        try:
            path = urllib.parse.unquote(raw, errors="strict")
        except UnicodeDecodeError:
            return None, 400
        if path in ("", "/"):
            path = "/index.html"
        if "\x00" in path or "\\" in path or ":" in path:
            return None, 403
        parts = [p for p in path.split("/") if p]
        if not parts or any(p.startswith(".") for p in parts):
            return None, 403
        if parts[0].casefold() == "os-host" or any(p.casefold() == "__pycache__" for p in parts):
            return None, 403
        try:
            target = self.server.console.joinpath(*parts).resolve()
        except (OSError, RuntimeError, ValueError):
            return None, 404
        if not inside(target, self.server.console) or inside(target, self.server.os_host):
            return None, 403
        if not target.is_file():
            return None, 404
        return target, 200

    def send_file(self, target, with_body):
        try:
            f = open(str(target), "rb")
        except OSError:
            self.send_error(404)
            return
        with f:
            st = os.fstat(f.fileno())
            size = st.st_size
            last = email.utils.formatdate(st.st_mtime, usegmt=True)
            ims = self.headers.get("If-Modified-Since")
            if ims and not self.headers.get("Range"):
                try:
                    since = email.utils.parsedate_to_datetime(ims).timestamp()
                    if int(st.st_mtime) <= int(since):
                        self.send_response(304)
                        self.send_header("Last-Modified", last)
                        self.send_header("Cache-Control", "no-cache")
                        self.end_headers()
                        return
                except (TypeError, ValueError, IndexError, OverflowError):
                    pass
            start, end, status = 0, size - 1, 200
            rng = self.headers.get("Range")
            if rng:
                m = re.fullmatch(r"\s*bytes=(\d*)-(\d*)\s*", rng)
                if m and (m.group(1) or m.group(2)):
                    if m.group(1):
                        start = int(m.group(1))
                        end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
                    else:
                        start = max(0, size - int(m.group(2)))
                    if start >= size or start > end:
                        self.send_response(416)
                        self.send_header("Content-Range", "bytes */%d" % size)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    status = 206
            length = max(0, end - start + 1)
            self.send_response(status)
            self.send_header("Content-Type", content_type(target))
            self.send_header("Content-Length", str(length))
            self.send_header("Last-Modified", last)
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("X-Content-Type-Options", "nosniff")
            if status == 206:
                self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
            self.end_headers()
            if not with_body:
                return
            f.seek(start)
            left = length
            while left > 0:
                chunk = f.read(min(1 << 16, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    def events(self):
        q = self.server.broker.subscribe()
        self.close_connection = True
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.wfile.write(b"retry: 2000\n\n")
            self.send_event(self.server.state())
            while True:
                try:
                    message = q.get(timeout=SSE_KEEPALIVE)
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                if message is None:
                    break
                self.send_event(message)
        except OSError:
            pass
        finally:
            self.server.broker.unsubscribe(q)

    def send_event(self, message):
        data = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        self.wfile.write(("data: " + data + "\n\n").encode("utf-8"))
        self.wfile.flush()


def main(argv=None):
    ap = argparse.ArgumentParser(description="AnyPS5 OS host (console screen + system calls) on 127.0.0.1")
    ap.add_argument("--port", type=int, default=int(os.environ.get("ANYPS5_OS_PORT", "8790")))
    ap.add_argument("--games", default=os.environ.get("ANYPS5_GAMES") or "~/AnyPS5 Games")
    ap.add_argument("--data", default=None, help="data folder (default os-host/data or $ANYPS5_OS_DATA)")
    a = ap.parse_args(argv)
    if not 0 <= a.port <= 65535:
        ap.error("port out of range")
    try:
        server = AnyPS5Server(port=a.port, games=a.games, data_dir=a.data)
    except OSError as e:
        print("AnyPS5 OS host could not listen on 127.0.0.1:%d: %s" % (a.port, e), file=sys.stderr)
        return 1
    print("AnyPS5 OS host on http://127.0.0.1:%d" % server.port, flush=True)
    try:
        import signal
        signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
    except (ImportError, ValueError, AttributeError):
        pass
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
