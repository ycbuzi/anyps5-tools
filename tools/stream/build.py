import base64
import json
import os
import secrets
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import obsws

PROBES = r"C:\AnyPS5-work\probes\stream"


def ini_set(path, section, values):
    text = ""
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig") as f:
            text = f.read()
    lines = text.splitlines()
    out, i, found = [], 0, False
    while i < len(lines):
        line = lines[i]
        if line.strip() == f"[{section}]":
            found = True
            out.append(line)
            i += 1
            seen = set()
            while i < len(lines) and not lines[i].startswith("["):
                k = lines[i].split("=", 1)[0]
                if k in values:
                    out.append(f"{k}={values[k]}")
                    seen.add(k)
                elif lines[i].strip():
                    out.append(lines[i])
                i += 1
            for k, v in values.items():
                if k not in seen:
                    out.append(f"{k}={v}")
            out.append("")
            continue
        out.append(line)
        i += 1
    if not found:
        if out and out[-1].strip():
            out.append("")
        out.append(f"[{section}]")
        out += [f"{k}={v}" for k, v in values.items()]
        out.append("")
    with open(path + ".tmp", "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out).rstrip("\n") + "\n")
    os.replace(path + ".tmp", path)


def step_ws():
    if obsws.obs_pids():
        sys.exit("close OBS first")
    os.makedirs(obsws.SECRETS, exist_ok=True)
    if not os.path.exists(obsws.WS_SECRET):
        with open(obsws.WS_SECRET, "w", encoding="utf-8") as f:
            f.write(secrets.token_urlsafe(18))
    d =os.path.join(obsws.CFG, "plugin_config", "obs-websocket")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "config.json.tmp"), "w", encoding="utf-8") as f:
        json.dump({"alerts_enabled": False, "auth_required": True, "first_load": False, "server_enabled": True,
                   "server_password": obsws.password(), "server_port": obsws.PORT}, f, indent=2)
    os.replace(os.path.join(d, "config.json.tmp"), os.path.join(d, "config.json"))
    print("websocket configured on port", obsws.PORT)


def step_profile():
    if obsws.obs_pids():
        sys.exit("close OBS first")
    d = os.path.join(obsws.CFG, "basic", "profiles", obsws.PROFILE.replace(" ", "_"))
    ini = os.path.join(d, "basic.ini")
    os.makedirs(d, exist_ok=True)
    w, h = obsws.CANVAS
    ini_set(ini, "General", {"Name": obsws.PROFILE})
    ini_set(ini, "Video", {"BaseCX": w, "BaseCY": h, "OutputCX": w, "OutputCY": h, "FPSType": 0, "FPSCommon": obsws.FPS, "ScaleType": "lanczos"})
    print("profile at", d)


def step_screens():
    os.makedirs(PROBES, exist_ok=True)
    with obsws.connect(5) as obs:
        res = {}
        for scene, _ in obsws.LAYOUTS:
            obs.get("SetCurrentProgramScene", {"sceneName": scene})
            time.sleep(1.5)
            cur = obs.get("GetCurrentProgramScene")["currentProgramSceneName"]
            img = obs.get("GetSourceScreenshot", {"sourceName": scene, "imageFormat": "png", "imageWidth": 1280})["imageData"]
            fn = os.path.join(PROBES, scene.replace(" + ", "-").replace(" ", "-").lower() + ".png")
            with open(fn, "wb") as f:
                f.write(base64.b64decode(img.split(",", 1)[1]))
            items = obs.get("GetSceneItemList", {"sceneName": scene})["sceneItems"]
            res[scene] = {"switched": cur == scene, "file": fn, "items": [(i["sourceName"], round(i["sceneItemTransform"]["positionX"]), round(i["sceneItemTransform"]["width"]), round(i["sceneItemTransform"]["height"])) for i in items]}
        obs.get("SetCurrentProgramScene", {"sceneName": "All three"})
    print(json.dumps(res, indent=1))


def step_ensure():
    with obsws.connect(60) as obs:
        print(json.dumps(obsws.ensure_all(obs), indent=1))


if __name__ == "__main__":
    steps = {"ws": step_ws, "profile": step_profile, "ensure": step_ensure, "screens": step_screens}
    for s in sys.argv[1:] or ["ensure", "screens"]:
        steps[s]()
