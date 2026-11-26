import http.server
import json
import os
import socket
import socketserver
import sys
import threading
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = r"C:\AnyPS5-work\xbox-games"
SKIP_DIRS = {"_sd", "_trophy", ".xbuild", "shader_cache"}
PORT = 8765


def xbox_host():
    return open(os.path.join(HERE, "out", ".host")).read().strip()


def lan_address(target):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((target, 9))
        return s.getsockname()[0]
    finally:
        s.close()


def manifest(folder):
    base = os.path.join(ROOT, folder)
    files = []
    for root, dirs, names in os.walk(base):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in names:
            if name.endswith((".bmp", ".log")) and root == base:
                continue
            path = os.path.join(root, name)
            files.append({"p": os.path.relpath(path, base).replace("\\", "/"), "s": os.path.getsize(path), "m": int(os.path.getmtime(path))})
    return files


class Handler(http.server.BaseHTTPRequestHandler):
    allowed = ""
    served = {"files": 0, "bytes": 0, "last": time.time()}

    def log_message(self, *args):
        pass

    def deny(self, code):
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if self.client_address[0] != self.allowed:
            return self.deny(403)
        Handler.served["last"] = time.time()
        url = urllib.parse.urlparse(self.path)
        parts = [urllib.parse.unquote(p) for p in url.path.split("/") if p]
        if len(parts) == 2 and parts[0] == "manifest":
            folder = parts[1]
            if os.path.dirname(folder) or folder in ("", ".", "..") or not os.path.isdir(os.path.join(ROOT, folder)):
                return self.deny(404)
            body = json.dumps(manifest(folder)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if len(parts) >= 3 and parts[0] == "file":
            base = os.path.realpath(os.path.join(ROOT, parts[1]))
            path = os.path.realpath(os.path.join(base, *parts[2:]))
            if not path.startswith(base + os.sep) or not os.path.isfile(path) or os.path.dirname(base) != os.path.realpath(ROOT):
                return self.deny(404)
            size = os.path.getsize(path)
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(size))
            self.end_headers()
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(1 << 20)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    Handler.served["last"] = time.time()
            Handler.served["files"] += 1
            Handler.served["bytes"] += size
            return
        self.deny(404)


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


def main():
    if len(sys.argv) < 2:
        raise SystemExit("usage: xserve.py <xbox-games folder> [idle seconds]")
    folder = sys.argv[1]
    idle = int(sys.argv[2]) if len(sys.argv) > 2 else 120
    host = xbox_host()
    Handler.allowed = host
    me = lan_address(host)
    server = Server((me, PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with open(os.path.join(HERE, "out", "pull.json"), "w", encoding="utf-8") as f:
        json.dump({"base": "http://%s:%d" % (me, PORT), "game": folder}, f)
    files = manifest(folder)
    print("serving %s: %d files, %.0f MB; pull.json written" % (folder, len(files), sum(x["s"] for x in files) / 1048576), flush=True)
    while time.time() - Handler.served["last"] < idle:
        time.sleep(2)
    server.shutdown()
    print("stopped after %d s idle: %d files, %.0f MB sent" % (idle, Handler.served["files"], Handler.served["bytes"] / 1048576), flush=True)


if __name__ == "__main__":
    main()
