"""Run commands on the MacBook when it has no SSH (Recovery, the macOS installer).

On the PC:   python mac_link.py serve            (leave running)
On the Mac:  curl 192.0.2.1:8765/s | sh       (in Recovery's Terminal, after joining Wi-Fi)
Then:        python mac_link.py run "diskutil list"   prints the Mac's output.

The Mac polls the PC; nothing listens on the Mac. Only the home network can reach the port.
"""
import http.server
import pathlib
import sys
import time

PORT = 8765
HOST_IP = "192.0.2.1"
HERE = pathlib.Path(__file__).resolve().parent / "mac-link"
CMDS = HERE / "cmds"
OUTS = HERE / "outs"

BOOT = f"""H=http://{HOST_IP}:{PORT}
echo "linked to the PC; leave this window open"
curl -s -m 10 $H/hello >/dev/null
while :; do
  i=$(curl -s -m 20 $H/next)
  case "$i" in
    ''|*[!0-9]*) sleep 2 ;;
    *) curl -s -m 20 $H/cmd/$i -o /tmp/link.sh
       /bin/bash /tmp/link.sh > /tmp/link.out 2>&1
       curl -s -m 120 --data-binary @/tmp/link.out $H/out/$i >/dev/null ;;
  esac
done
"""


class Handler(http.server.BaseHTTPRequestHandler):
    def send(self, body, code=200):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        HERE.mkdir(exist_ok=True)
        (HERE / "last-seen.txt").write_text(f"{time.strftime('%H:%M:%S')} {self.client_address[0]} {self.path}")
        if self.path == "/s":
            return self.send(BOOT)
        if self.path == "/hello":
            return self.send("ok")
        if self.path == "/next":
            pending = sorted(CMDS.glob("*.sh"), key=lambda p: int(p.stem))
            return self.send(pending[0].stem if pending else "")
        if self.path.startswith("/cmd/") and self.path[5:].isdigit():
            f = CMDS / f"{self.path[5:]}.sh"
            if f.exists():
                body = f.read_bytes()
                f.rename(f.with_suffix(".sent"))
                return self.send(body)
        self.send("", 404)

    def do_POST(self):
        if self.path.startswith("/out/") and self.path[5:].isdigit():
            n = int(self.headers.get("Content-Length") or 0)
            (OUTS / f"{self.path[5:]}.txt").write_bytes(self.rfile.read(n))
            return self.send("ok")
        self.send("", 404)

    def log_message(self, *args):
        pass


def serve():
    CMDS.mkdir(parents=True, exist_ok=True)
    OUTS.mkdir(parents=True, exist_ok=True)
    http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


def run(command, timeout=600):
    CMDS.mkdir(parents=True, exist_ok=True)
    OUTS.mkdir(parents=True, exist_ok=True)
    i = str(int(time.time() * 1000))
    (CMDS / f"{i}.sh").write_text(command.replace("\r\n", "\n") + "\n", newline="\n")
    out = OUTS / f"{i}.txt"
    end = time.time() + timeout
    while time.time() < end:
        if out.exists():
            return out.read_text(errors="replace")
        time.sleep(1)
    return f"no answer in {timeout} s (is the Mac's Terminal still running the link?)"


if __name__ == "__main__":
    if sys.argv[1:2] == ["serve"]:
        serve()
    elif sys.argv[1:2] == ["run"]:
        print(run(" ".join(sys.argv[2:])))
    else:
        print(__doc__)
