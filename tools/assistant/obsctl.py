import base64
import json
import os
import socket
import sys
import time


class Obs:
    def __init__(self, host="127.0.0.1", port=4455):
        self.s = socket.create_connection((host, port), 10)
        key = base64.b64encode(os.urandom(16)).decode()
        req = f"GET / HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Protocol: obswebsocket.json\r\n\r\n"
        self.s.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.s.recv(4096)
        self.extra = buf.split(b"\r\n\r\n", 1)[1]
        self.n = 0
        hello = self.recv()
        self.send({"op": 1, "d": {"rpcVersion": 1}})
        self.recv()

    def send(self, obj):
        data = json.dumps(obj).encode()
        mask = os.urandom(4)
        head = bytes([0x81])
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
            chunk = self.s.recv(65536)
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
            if b0 & 0x0F == 1:
                return json.loads(payload)

    def call(self, rtype, data=None):
        self.n += 1
        rid = str(self.n)
        self.send({"op": 6, "d": {"requestType": rtype, "requestId": rid, "requestData": data or {}}})
        while True:
            m = self.recv()
            if m.get("op") == 7 and m["d"].get("requestId") == rid:
                return m["d"]


def setup(obs, scene_name="AnyPS5 Progress", monitor_index=0, width=1920, height=1080):
    scenes = [s["sceneName"] for s in obs.call("GetSceneList")["responseData"]["scenes"]]
    if scene_name not in scenes:
        obs.call("CreateScene", {"sceneName": scene_name})
    obs.call("SetCurrentProgramScene", {"sceneName": scene_name})
    inputs = [i["inputName"] for i in obs.call("GetInputList")["responseData"]["inputs"]]
    if "AnyPS5 main monitor" not in inputs:
        obs.call("CreateInput", {"sceneName": scene_name, "inputName": "AnyPS5 main monitor", "inputKind": "monitor_capture", "inputSettings": {"capture_cursor": True}})
    props = obs.call("GetInputPropertiesListPropertyItems", {"inputName": "AnyPS5 main monitor", "propertyName": "monitor_id"})
    items = (props.get("responseData") or {}).get("propertyItems") or []
    if items:
        pick = next((it for it in items if "Primary" in it.get("itemName", "") or "primary" in it.get("itemName", "")), items[min(monitor_index, len(items) - 1)])
        obs.call("SetInputSettings", {"inputName": "AnyPS5 main monitor", "inputSettings": {"monitor_id": pick["itemValue"], "capture_cursor": True}})
    sid = obs.call("GetSceneItemId", {"sceneName": scene_name, "sourceName": "AnyPS5 main monitor"})["responseData"]["sceneItemId"]
    obs.call("SetSceneItemTransform", {"sceneName": scene_name, "sceneItemId": sid, "sceneItemTransform": {"boundsType": "OBS_BOUNDS_SCALE_INNER", "boundsWidth": width, "boundsHeight": height, "positionX": 0, "positionY": 0, "alignment": 5, "boundsAlignment": 0}})
    return [it.get("itemName") for it in items]


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    obs = None
    for _ in range(30):
        try:
            obs = Obs()
            break
        except OSError:
            time.sleep(1)
    if obs is None:
        sys.exit("obs websocket not reachable on 127.0.0.1:4455")
    if cmd == "setup":
        print("monitors:", setup(obs))
    elif cmd == "start":
        print(obs.call("StartRecord").get("requestStatus"))
    elif cmd == "stop":
        print(obs.call("StopRecord").get("responseData"))
    else:
        print(obs.call("GetRecordStatus").get("responseData"), obs.call("GetCurrentProgramScene").get("responseData"))


if __name__ == "__main__":
    main()
