import base64
import ctypes
import ctypes.wintypes as wt
import hashlib
import json
import os
import socket
import subprocess
import time

OBS_DIR = r"C:\Program Files\obs-studio\bin\64bit"
OBS_EXE = os.path.join(OBS_DIR, "obs64.exe")
CFG = os.path.join(os.environ.get("APPDATA", r"%USERPROFILE%\AppData\Roaming"), "obs-studio")
SECRETS = r"C:\AnyPS5-work\secrets"
WS_SECRET = os.path.join(SECRETS, "obs-websocket.txt")
PROFILE = "Discord Monitors"
COLLECTION = "Discord Monitors"
PORT = 4455
CANVAS = (2560, 1440)
FPS = 30
TASK = "AnyPS5 Discord stream"
LOG = r"C:\AnyPS5-work\probes\stream\stream.log"

MONITORS = [
    ("Left", "Left monitor", (-1080, 0), 1080, 1920, ("LS24D36",)),
    ("Middle", "Middle monitor", (0, 0), 1440, 1080, ("H24S17P",)),
    ("Right", "Right monitor", (1440, 0), 1080, 1920, ("P2422H", "DELL")),
]
LAYOUTS = [
    ("Middle", ["Middle"]),
    ("Left", ["Left"]),
    ("Right", ["Right"]),
    ("Middle + Right", ["Middle", "Right"]),
    ("Left + Middle", ["Left", "Middle"]),
    ("All three", ["Left", "Middle", "Right"]),
]
NO_WINDOW = 0x08000000


def log(msg):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


def password():
    with open(WS_SECRET, encoding="utf-8") as f:
        return f.read().strip()


class Obs:
    def __init__(self, host="127.0.0.1", port=PORT, timeout=10):
        self.s = socket.create_connection((host, port), timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = f"GET / HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Protocol: obswebsocket.json\r\n\r\n"
        self.s.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.s.recv(4096)
            if not chunk:
                raise ConnectionError("handshake failed")
            buf += chunk
        self.extra = buf.split(b"\r\n\r\n", 1)[1]
        self.n = 0
        hello = self.recv()
        ident = {"rpcVersion": 1}
        auth = hello.get("d", {}).get("authentication")
        if auth:
            secret = base64.b64encode(hashlib.sha256((password() + auth["salt"]).encode()).digest())
            ident["authentication"] = base64.b64encode(hashlib.sha256(secret + auth["challenge"].encode()).digest()).decode()
        self.send({"op": 1, "d": ident})
        m = self.recv()
        if m.get("op") != 2:
            raise ConnectionError("identify failed")

    def close(self):
        try:
            self.s.close()
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def send(self, obj, opcode=1):
        data = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        mask = os.urandom(4)
        head = bytes([0x80 | opcode])
        n = len(data)
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + n.to_bytes(2, "big")
        else:
            head += bytes([0x80 | 127]) + n.to_bytes(8, "big")
        self.s.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _read(self, k):
        while len(self.extra) < k:
            chunk = self.s.recv(1 << 20)
            if not chunk:
                raise ConnectionError("obs closed the connection")
            self.extra += chunk
        out, self.extra = self.extra[:k], self.extra[k:]
        return out

    def recv(self):
        while True:
            b0, b1 = self._read(2)
            n = b1 & 0x7F
            if n == 126:
                n = int.from_bytes(self._read(2), "big")
            elif n == 127:
                n = int.from_bytes(self._read(8), "big")
            payload = self._read(n)
            op = b0 & 0x0F
            if op == 1:
                return json.loads(payload)
            if op == 9:
                self.send(payload, 10)
            if op == 8:
                raise ConnectionError("obs closed the connection")

    def call(self, rtype, data=None):
        self.n += 1
        rid = str(self.n)
        self.send({"op": 6, "d": {"requestType": rtype, "requestId": rid, "requestData": data or {}}})
        while True:
            m = self.recv()
            if m.get("op") == 7 and m["d"].get("requestId") == rid:
                return m["d"]

    def get(self, rtype, data=None):
        r = self.call(rtype, data)
        if not r["requestStatus"]["result"]:
            raise RuntimeError(f"{rtype}: {r['requestStatus'].get('comment', r['requestStatus'].get('code'))}")
        return r.get("responseData") or {}


def connect(wait=0):
    end = time.time() + wait
    while True:
        try:
            return Obs()
        except (OSError, ConnectionError):
            if time.time() >= end:
                raise
            if wait:
                dismiss_crash_dialog()
            time.sleep(1)


def obs_pids():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq obs64.exe", "/FO", "CSV", "/NH"], capture_output=True, text=True, creationflags=NO_WINDOW).stdout
    pids = []
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) > 1 and parts[0].lower().strip('"') == "obs64.exe":
            pids.append(int(parts[1]))
    return pids


user32 = ctypes.WinDLL("user32", use_last_error=True)
try:
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except (AttributeError, OSError):
    pass
EnumWindowsProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
MonitorEnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HMONITOR, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM)


def windows(pids):
    found = []

    def cb(hwnd, _):
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pids is None or pid.value in pids:
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            found.append((hwnd, buf.value, bool(user32.IsWindowVisible(hwnd))))
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return found


def projector_windows():
    return [w for w in windows(obs_pids()) if "Projector" in w[1] and w[2]]


def main_windows():
    return [w for w in windows(obs_pids()) if w[1].startswith("OBS ")]


def monitor_rects():
    rects = []

    def cb(hmon, hdc, lprc, _):
        r = lprc.contents
        rects.append((r.left, r.top, r.right, r.bottom))
        return True

    user32.EnumDisplayMonitors(None, None, MonitorEnumProc(cb), 0)
    return rects


def place_projector(hwnd):
    rects = monitor_rects()
    x = max(r[2] for r in rects) + 200
    y = min(r[1] for r in rects)
    user32.ShowWindow(hwnd, 9)
    user32.SetWindowPos(hwnd, None, x, y, 1920, 1080, 0x0004 | 0x0010)


def clear_sentinels():
    d = os.path.join(CFG, ".sentinel")
    if obs_pids() or not os.path.isdir(d):
        return
    for fn in os.listdir(d):
        if fn.startswith("run_"):
            try:
                os.remove(os.path.join(d, fn))
            except OSError:
                pass


def dismiss_crash_dialog():
    for _, title, vis in windows(obs_pids()):
        if vis and ("Crash" in title or "Safe Mode" in title):
            out = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", os.path.join(os.path.dirname(os.path.abspath(__file__)), "dismiss.ps1")], capture_output=True, text=True, creationflags=NO_WINDOW).stdout
            log("crash dialog: " + out.strip())
            return True
    return False


def launch_obs():
    clear_sentinels()
    args = [OBS_EXE, "--profile", PROFILE, "--collection", COLLECTION, "--minimize-to-tray", "--disable-shutdown-check", "--disable-updater", "--disable-missing-files-check"]
    subprocess.Popen(args, cwd=OBS_DIR, creationflags=0x00000008 | 0x00000200, close_fds=True)


def close_obs(wait=25):
    for hwnd, _, _ in main_windows():
        user32.PostMessageW(hwnd, 0x0010, 0, 0)
    end = time.time() + wait
    while obs_pids() and time.time() < end:
        time.sleep(1)
    if obs_pids():
        subprocess.run(["taskkill", "/F", "/IM", "obs64.exe"], capture_output=True, creationflags=NO_WINDOW)
        time.sleep(2)
    return not obs_pids()


def outputs_active(obs):
    st = obs.get("GetStreamStatus").get("outputActive") or obs.get("GetRecordStatus").get("outputActive")
    vc = obs.call("GetVirtualCamStatus").get("responseData") or {}
    return bool(st or vc.get("outputActive"))


def ensure_profile(obs):
    cur = obs.get("GetProfileList")
    if cur["currentProfileName"] != PROFILE:
        if PROFILE not in cur["profiles"]:
            raise RuntimeError("profile missing; run build.py profile with OBS closed")
        obs.get("SetCurrentProfile", {"profileName": PROFILE})
        time.sleep(2)
    v = obs.get("GetVideoSettings")
    want = {"baseWidth": CANVAS[0], "baseHeight": CANVAS[1], "outputWidth": CANVAS[0], "outputHeight": CANVAS[1], "fpsNumerator": FPS, "fpsDenominator": 1}
    if any(v.get(k) != val for k, val in want.items()):
        obs.get("SetVideoSettings", want)


def ensure_collection(obs):
    cur = obs.get("GetSceneCollectionList")
    if cur["currentSceneCollectionName"] == COLLECTION:
        return
    if COLLECTION in cur["sceneCollections"]:
        obs.get("SetCurrentSceneCollection", {"sceneCollectionName": COLLECTION})
    else:
        obs.get("CreateSceneCollection", {"sceneCollectionName": COLLECTION})
    time.sleep(2)


def pick_monitor(items, pos, names):
    tag = f"@ {pos[0]},{pos[1]}"
    for it in items:
        if tag in it.get("itemName", ""):
            return it
    for it in items:
        if any(n.lower() in it.get("itemName", "").lower() for n in names):
            return it
    return None


def ensure_inputs(obs):
    have = {i["inputName"] for i in obs.get("GetInputList")["inputs"]}
    scenes = {s["sceneName"] for s in obs.get("GetSceneList")["scenes"]}
    holder = LAYOUTS[0][0]
    if holder not in scenes:
        obs.get("CreateScene", {"sceneName": holder})
    picked = {}
    for key, name, pos, w, h, names in MONITORS:
        if name not in have:
            obs.get("CreateInput", {"sceneName": holder, "inputName": name, "inputKind": "monitor_capture", "inputSettings": {"capture_cursor": True, "method": 2}})
        items = obs.get("GetInputPropertiesListPropertyItems", {"inputName": name, "propertyName": "monitor_id"}).get("propertyItems") or []
        it = pick_monitor(items, pos, names)
        if it is None:
            raise RuntimeError(f"no monitor matches {name}: {[i.get('itemName') for i in items]}")
        obs.get("SetInputSettings", {"inputName": name, "inputSettings": {"monitor_id": it["itemValue"], "capture_cursor": True, "method": 2}})
        picked[name] = it.get("itemName")
    return picked


def layout_boxes(keys):
    mons = {m[0]: m for m in MONITORS}
    tw = sum(mons[k][3] for k in keys)
    th = max(mons[k][4] for k in keys)
    s = min(CANVAS[0] / tw, CANVAS[1] / th)
    x = (CANVAS[0] - tw * s) / 2
    boxes = []
    for k in keys:
        w, h = mons[k][3] * s, mons[k][4] * s
        boxes.append((mons[k][1], x, (CANVAS[1] - h) / 2, w, h))
        x += w
    return boxes


def ensure_scenes(obs):
    scenes = {s["sceneName"] for s in obs.get("GetSceneList")["scenes"]}
    for scene, keys in LAYOUTS:
        if scene not in scenes:
            obs.get("CreateScene", {"sceneName": scene})
        boxes = layout_boxes(keys)
        wanted = {b[0] for b in boxes}
        items = obs.get("GetSceneItemList", {"sceneName": scene})["sceneItems"]
        present = {it["sourceName"]: it["sceneItemId"] for it in items if it["sourceName"] in wanted}
        for name, x, y, w, h in boxes:
            sid = present.get(name)
            if sid is None:
                sid = obs.get("CreateSceneItem", {"sceneName": scene, "sourceName": name, "sceneItemEnabled": True})["sceneItemId"]
            obs.get("SetSceneItemTransform", {"sceneName": scene, "sceneItemId": sid, "sceneItemTransform": {
                "positionX": round(x), "positionY": round(y), "alignment": 5, "rotation": 0,
                "boundsType": "OBS_BOUNDS_SCALE_INNER", "boundsWidth": round(w), "boundsHeight": round(h), "boundsAlignment": 0,
                "cropLeft": 0, "cropRight": 0, "cropTop": 0, "cropBottom": 0}})
            obs.get("SetSceneItemEnabled", {"sceneName": scene, "sceneItemId": sid, "sceneItemEnabled": True})
    for scene, keys in LAYOUTS:
        wanted = {b[0] for b in layout_boxes(keys)}
        for it in obs.get("GetSceneItemList", {"sceneName": scene})["sceneItems"]:
            if it["sourceName"] not in wanted:
                obs.get("RemoveSceneItem", {"sceneName": scene, "sceneItemId": it["sceneItemId"]})
    for s in scenes - {l[0] for l in LAYOUTS}:
        if not obs.get("GetSceneItemList", {"sceneName": s})["sceneItems"]:
            obs.call("RemoveScene", {"sceneName": s})


def collection_file():
    d = os.path.join(CFG, "basic", "scenes")
    for fn in os.listdir(d):
        if fn.endswith(".json"):
            p = os.path.join(d, fn)
            try:
                with open(p, encoding="utf-8-sig") as f:
                    if json.load(f).get("name") == COLLECTION:
                        return p
            except (OSError, ValueError):
                continue
    return None


def hotkey_for(i):
    return [{"key": f"OBS_KEY_{i}", "control": True, "alt": True}]


def hotkeys_ok(path):
    if not path:
        return False
    with open(path, encoding="utf-8-sig") as f:
        data = json.load(f)
    srcs = {s.get("name"): s for s in data.get("sources", [])}
    for i, (scene, _) in enumerate(LAYOUTS, 1):
        hk = (srcs.get(scene) or {}).get("hotkeys", {}).get("OBSBasic.SelectScene")
        if hk != hotkey_for(i):
            return False
    return True


def write_hotkeys(path):
    with open(path, encoding="utf-8-sig") as f:
        data = json.load(f)
    srcs = {s.get("name"): s for s in data.get("sources", [])}
    for i, (scene, _) in enumerate(LAYOUTS, 1):
        s = srcs.get(scene)
        if s is not None:
            s.setdefault("hotkeys", {})["OBSBasic.SelectScene"] = hotkey_for(i)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)
    os.replace(tmp, path)


def ensure_hotkeys(obs):
    path = collection_file()
    if hotkeys_ok(path):
        return "present"
    others = [c for c in obs.get("GetSceneCollectionList")["sceneCollections"] if c != COLLECTION]
    if not others:
        return "no other collection to swap through; hotkeys not set"
    obs.get("SetCurrentSceneCollection", {"sceneCollectionName": others[0]})
    time.sleep(3)
    path = collection_file()
    if path:
        write_hotkeys(path)
    obs.get("SetCurrentSceneCollection", {"sceneCollectionName": COLLECTION})
    time.sleep(3)
    return "written" if path else "collection file not found"


def ensure_projector(obs, wait=10):
    wins = projector_windows()
    if not wins:
        obs.get("OpenVideoMixProjector", {"videoMixType": "OBS_WEBSOCKET_VIDEO_MIX_TYPE_PROGRAM", "monitorIndex": -1})
        end = time.time() + wait
        while not wins and time.time() < end:
            time.sleep(0.5)
            wins = projector_windows()
        for hwnd, _, _ in wins:
            place_projector(hwnd)
    return [w[1] for w in wins]


def wait_ready(obs, wait=60):
    end = time.time() + wait
    while time.time() < end:
        if obs.call("GetStreamStatus")["requestStatus"]["code"] != 207:
            time.sleep(2)
            return True
        time.sleep(1)
    return False


SILENT_MONITOR = ("LS24D36xG", "{0.0.0.00000000}.{0a557598-2082-4308-8cc9-dee1c7723318}")


GAME_SOUND = "Game sound"
GAMES_ROOT = r"D:\AnyPS5 Games"


def ensure_audio(obs):
    active = obs.get("GetProfileParameter", {"parameterCategory": "Audio", "parameterName": "MonitoringDeviceId"}).get("parameterValue") == SILENT_MONITOR[1]
    if not active:
        obs.get("SetProfileParameter", {"parameterCategory": "Audio", "parameterName": "MonitoringDeviceName", "parameterValue": SILENT_MONITOR[0]})
        obs.get("SetProfileParameter", {"parameterCategory": "Audio", "parameterName": "MonitoringDeviceId", "parameterValue": SILENT_MONITOR[1]})
    inputs = {i["inputName"]: i["inputKind"] for i in obs.get("GetInputList")["inputs"]}
    if "Desktop sound" in inputs:
        obs.get("RemoveInput", {"inputName": "Desktop sound"})
    scenes = [l[0] for l in LAYOUTS]
    if GAME_SOUND not in inputs:
        obs.get("CreateInput", {"sceneName": scenes[0], "inputName": GAME_SOUND, "inputKind": "wasapi_process_output_capture", "inputSettings": {"priority": 2}})
    for s in scenes:
        names = [i["sourceName"] for i in obs.get("GetSceneItemList", {"sceneName": s})["sceneItems"]]
        if GAME_SOUND not in names:
            obs.get("CreateSceneItem", {"sceneName": s, "sourceName": GAME_SOUND})
    obs.get("SetInputAudioMonitorType", {"inputName": GAME_SOUND, "monitorType": "OBS_MONITORING_TYPE_MONITOR_ONLY" if active else "OBS_MONITORING_TYPE_NONE"})
    return {"game sound": "replayed to " + SILENT_MONITOR[0] if active else "takes effect after the next OBS start"}


def running_game_window():
    games = os.path.normcase(GAMES_ROOT)
    best = None
    for hwnd, title, vis in windows(None):
        if not vis or not title:
            continue
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid.value)
        if not h:
            continue
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.c_ulong(1024)
        ok = ctypes.windll.kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
        ctypes.windll.kernel32.CloseHandle(h)
        exe = buf.value if ok else ""
        name = os.path.basename(exe)
        if os.path.normcase(exe).startswith(games) and os.path.dirname(exe) != GAMES_ROOT and name.lower().endswith(".exe"):
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            best = f"{title.replace(':', '#3A')}:{cls.value.replace(':', '#3A')}:{name}"
            break
    return best


def follow_game(obs, last=None):
    win = running_game_window()
    if win and win != last:
        obs.get("SetInputSettings", {"inputName": GAME_SOUND, "inputSettings": {"window": win, "priority": 2}})
        log("game sound now follows " + win.split(":")[-1])
    return win or last


def ensure_all(obs, scene=None):
    report = {}
    wait_ready(obs)
    if outputs_active(obs) and obs.get("GetProfileList")["currentProfileName"] != PROFILE:
        raise RuntimeError("OBS is streaming/recording on another profile; not switching")
    ensure_profile(obs)
    ensure_collection(obs)
    report["audio"] = ensure_audio(obs)
    report["monitors"] = ensure_inputs(obs)
    ensure_scenes(obs)
    report["hotkeys"] = ensure_hotkeys(obs)
    cur = obs.get("GetCurrentProgramScene")["currentProgramSceneName"]
    if scene or cur not in {l[0] for l in LAYOUTS}:
        obs.get("SetCurrentProgramScene", {"sceneName": scene or "All three"})
    report["projector"] = ensure_projector(obs)
    return report
