import hashlib
import hmac
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import obsws

PORT = 8795
PIN_FILE = os.path.join(obsws.SECRETS, "stream-pin.txt")
fails = {}
busy = {"restart": False}


def pin():
    if not os.path.exists(PIN_FILE):
        os.makedirs(obsws.SECRETS, exist_ok=True)
        with open(PIN_FILE, "w", encoding="utf-8") as f:
            f.write(f"{secrets.randbelow(10**6):06d}")
    with open(PIN_FILE, encoding="utf-8") as f:
        return f.read().strip()


def token():
    return hashlib.sha256(("anyps5-stream:" + pin()).encode()).hexdigest()


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Stream layout</title><style>
body{margin:0;background:#111;color:#eee;font:18px system-ui,sans-serif;padding:16px}
h1{font-size:20px;margin:0 0 12px}#st{color:#9a9;font-size:14px;margin-bottom:12px;min-height:18px}
.g{display:grid;grid-template-columns:1fr 1fr;gap:12px}
button{font:inherit;font-size:22px;padding:26px 8px;border-radius:14px;border:2px solid #333;background:#222;color:#eee}
button.on{background:#2a6;border-color:#4c8;color:#fff;font-weight:700}
.k{display:block;font-size:12px;color:#999;margin-top:4px}.on .k{color:#dfd}
.x{grid-template-columns:1fr 1fr;margin-top:20px}.x button{font-size:17px;padding:16px 8px;background:#1a1a2a}
</style></head><body><h1>Discord stream layout</h1><div id="st">loading</div><div class="g" id="g"></div>
<div class="g x"><button onclick="act('gamemode-on')">Game mode ON (pause AnyPS5 work)</button><button onclick="act('gamemode-off')">Game mode OFF</button></div>
<div class="g x"><button onclick="act('projector')">Open projector</button><button onclick="if(confirm('Restart OBS?'))act('restart')">Restart OBS</button></div>
<script>
const L=__LAYOUTS__;const g=document.getElementById('g'),st=document.getElementById('st');
L.forEach((n,i)=>{const b=document.createElement('button');b.innerHTML=n+'<span class="k">Ctrl+Alt+'+(i+1)+'</span>';b.dataset.n=n;b.onclick=()=>pick(n);g.appendChild(b)});
function mark(c){for(const b of g.children)b.classList.toggle('on',b.dataset.n===c)}
async function api(p,body){const r=await fetch(p,{method:body?'POST':'GET',headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});if(r.status==401){location.reload();return{}}return r.json()}
async function poll(){try{const s=await api('/api/state');if(s.current){mark(s.current);st.textContent='Live: '+s.current}else st.textContent=s.error||'OBS not reachable'}catch(e){st.textContent='PC not reachable'}}
async function pick(n){mark(n);const s=await api('/api/scene',{name:n});st.textContent=s.ok?'Live: '+n:(s.error||'failed');poll()}
async function act(a){st.textContent=a+'...';const s=await api('/api/'+a,{});st.textContent=s.ok?(s.msg||'done'):(s.error||'failed')}
poll();setInterval(poll,3000);
</script></body></html>"""

LOGIN = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Stream layout</title>
<style>body{margin:0;background:#111;color:#eee;font:20px system-ui,sans-serif;padding:16px}input,button{font:inherit;font-size:28px;padding:14px;border-radius:12px;border:2px solid #333;width:100%;box-sizing:border-box;margin-top:12px;background:#222;color:#eee}button{background:#2a6}</style></head>
<body><h1 style="font-size:22px">Stream layout PIN</h1><form method="post" action="/login"><input name="pin" inputmode="numeric" autocomplete="off" autofocus><button>Unlock</button></form>__MSG__</body></html>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def authed(self):
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "sl" and hmac.compare_digest(v, token()):
                return True
        return False

    def send(self, code, body, ctype="application/json", extra=None):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def j(self, obj, code=200):
        self.send(code, json.dumps(obj))

    def body(self):
        n = min(int(self.headers.get("Content-Length") or 0), 4096)
        return self.rfile.read(n) if n else b""

    def do_GET(self):
        if self.path.split("?")[0] == "/":
            if self.authed():
                return self.send(200, PAGE.replace("__LAYOUTS__", json.dumps([l[0] for l in obsws.LAYOUTS])), "text/html")
            return self.send(200, LOGIN.replace("__MSG__", ""), "text/html")
        if self.path == "/api/state":
            if not self.authed():
                return self.j({"error": "pin"}, 401)
            try:
                with obsws.connect() as obs:
                    return self.j({"current": obs.get("GetCurrentProgramScene")["currentProgramSceneName"], "restarting": busy["restart"]})
            except Exception as e:
                return self.j({"error": "OBS restarting" if busy["restart"] else "OBS not reachable: " + str(e)[:80]})
        self.send(404, "{}")

    def do_POST(self):
        ip = self.client_address[0]
        if self.path == "/login":
            f = fails.get(ip, [0, 0])
            if f[0] >= 8 and time.time() - f[1] < 300:
                return self.send(429, LOGIN.replace("__MSG__", "<p>Too many tries, wait 5 minutes.</p>"), "text/html")
            got = parse_qs(self.body().decode(errors="ignore")).get("pin", [""])[0].strip()
            if hmac.compare_digest(got, pin()):
                fails.pop(ip, None)
                self.send_response(303)
                self.send_header("Location", "/")
                self.send_header("Set-Cookie", f"sl={token()}; Max-Age=31536000; Path=/; HttpOnly; SameSite=Strict")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            fails[ip] = [f[0] + 1, time.time()]
            return self.send(401, LOGIN.replace("__MSG__", "<p>Wrong PIN.</p>"), "text/html")
        if not self.authed():
            return self.j({"error": "pin"}, 401)
        try:
            req = json.loads(self.body() or b"{}")
        except ValueError:
            req = {}
        if self.path == "/api/scene":
            name = req.get("name")
            if name not in [l[0] for l in obsws.LAYOUTS]:
                return self.j({"error": "unknown layout"}, 400)
            try:
                with obsws.connect() as obs:
                    obs.get("SetCurrentProgramScene", {"sceneName": name})
                return self.j({"ok": True})
            except Exception as e:
                return self.j({"error": str(e)[:120]})
        if self.path in ("/api/gamemode-on", "/api/gamemode-off"):
            mode = "on" if self.path.endswith("on") else "off"
            out = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", r"C:\AnyPS5 Games\_tools\game-mode.ps1", mode], capture_output=True, text=True, creationflags=obsws.NO_WINDOW).stdout.strip()
            return self.j({"ok": True, "msg": out[-160:] or ("game mode " + mode)})
        if self.path == "/api/projector":
            try:
                with obsws.connect() as obs:
                    wins = obsws.ensure_projector(obs)
                for hwnd, _, _ in obsws.projector_windows():
                    obsws.user32.ShowWindow(hwnd, 9)
                return self.j({"ok": True, "msg": "projector open" if wins else "projector requested"})
            except Exception as e:
                return self.j({"error": str(e)[:120]})
        if self.path == "/api/restart":
            if busy["restart"]:
                return self.j({"ok": True, "msg": "already restarting"})
            threading.Thread(target=restart, daemon=True).start()
            return self.j({"ok": True, "msg": "restarting OBS, about 30 s"})
        self.j({"error": "not found"}, 404)


def restart():
    busy["restart"] = True
    try:
        obsws.log("control: restart requested")
        obsws.close_obs()
        subprocess.run(["schtasks", "/Run", "/TN", obsws.TASK], capture_output=True, creationflags=obsws.NO_WINDOW)
        time.sleep(30)
    finally:
        busy["restart"] = False


def game_sound_loop():
    last = None
    while True:
        try:
            if obsws.obs_pids():
                with obsws.connect(2) as obs:
                    last = obsws.follow_game(obs, last)
        except Exception as e:
            obsws.log(f"control: game sound follow failed: {e}")
        time.sleep(10)


def main():
    pin()
    threading.Thread(target=game_sound_loop, daemon=True).start()
    while True:
        try:
            ip = lan_ip()
            srv = ThreadingHTTPServer((ip, PORT), H)
            break
        except OSError as e:
            obsws.log(f"control: bind failed {e}; retry in 15 s")
            time.sleep(15)
    obsws.log(f"control: serving http://{ip}:{PORT}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
