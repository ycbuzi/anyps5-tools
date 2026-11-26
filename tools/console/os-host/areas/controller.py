import json
import os
import re
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dualsense
import pads as _padmaps

LEVELS = ("off", "weak", "medium", "strong")
RUMBLE_PERCENT = {"off": 0, "weak": 33, "medium": 66, "strong": 100}
TRIGGER_CUT = {"off": 7, "weak": 5, "medium": 2, "strong": 0}
TRIGGER_DEMO = {"off": 0, "weak": 3, "medium": 5, "strong": 8}
BRIGHTNESS = ("bright", "medium", "dim")
COLORS = {
    "blue": (0, 64, 255),
    "red": (255, 0, 0),
    "green": (0, 255, 0),
    "pink": (255, 0, 128),
    "purple": (128, 0, 255),
    "orange": (255, 96, 0),
    "white": (255, 255, 255),
    "off": (0, 0, 0),
}
PLAYER_LED = ("auto", "off")
MIC_LED = {"off": 0, "on": 1, "pulse": 2}
CYCLE = ((255, 0, 0), (255, 128, 0), (255, 255, 0), (0, 255, 0), (0, 255, 255), (0, 0, 255), (128, 0, 255), (255, 0, 255))
ANIMATIONS = {"rainbow": (24, 0.08), "strobe": (1, 0.25)}
PAD_RESCAN_SECONDS = 2.0
BUTTONS = ("cross", "circle", "square", "triangle", "l1", "r1", "l2", "r2", "l3", "r3", "options", "create",
           "up", "down", "left", "right")
BUTTON_NAMES = {
    "cross": "Cross", "circle": "Circle", "square": "Square", "triangle": "Triangle", "l1": "L1", "r1": "R1",
    "l2": "L2", "r2": "R2", "l3": "L3", "r3": "R3", "options": "Options", "create": "Create",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
}
SDL_TARGET = {
    "cross": "a", "circle": "b", "square": "x", "triangle": "y", "l1": "leftshoulder", "r1": "rightshoulder",
    "l2": "lefttrigger", "r2": "righttrigger", "l3": "leftstick", "r3": "rightstick", "options": "start",
    "create": "back", "up": "dpup", "down": "dpdown", "left": "dpleft", "right": "dpright",
}
SDL_SOURCE = {
    "cross": "b0", "circle": "b1", "square": "b2", "triangle": "b3", "create": "b4", "options": "b6",
    "l3": "b7", "r3": "b8", "l1": "b9", "r1": "b10", "up": "b11", "down": "b12", "left": "b13", "right": "b14",
    "l2": "a4", "r2": "a5",
}
PRESETS = {
    "default": {},
    "swapCrossCircle": {"cross": "circle", "circle": "cross"},
    "swapShoulders": {"l1": "l2", "l2": "l1", "r1": "r2", "r2": "r1"},
    "swapSides": {"l1": "r1", "r1": "l1", "l2": "r2", "r2": "l2", "l3": "r3", "r3": "l3"},
}
PRESET_NAMES = {"default": "Default", "swapCrossCircle": "Swap Cross and Circle", "swapShoulders": "Swap L1/R1 with L2/R2",
                "swapSides": "Swap Left and Right Buttons"}
DEFAULTS = {"vibration": "strong", "trigger": "strong", "brightness": "bright", "color": "blue", "playerLed": "auto", "micLed": "off",
            "swapSticks": False, "remap": {}}
CHOICES = {"vibration": LEVELS, "trigger": LEVELS, "brightness": BRIGHTNESS, "color": tuple(COLORS) + tuple(ANIMATIONS), "playerLed": PLAYER_LED,
           "micLed": tuple(MIC_LED)}
UID_RE = re.compile(r"^[A-Za-z0-9_.-]{0,64}$")
TRIGGER_DEMO_SECONDS = 20.0

_lock = threading.Lock()
_timers = {}
_send_lock = threading.Lock()
_animation = {"stop": None}


def settings_file():
    return os.environ.get("ANYPS5_CONTROLLER_FILE") or os.path.join(os.path.expanduser("~"), ".local", "share", "anyps5", "controller.json")


def _uid(args):
    uid = (args or {}).get("uid", "")
    if uid is None:
        uid = ""
    if not isinstance(uid, str) or not UID_RE.match(uid):
        raise ValueError("bad user id")
    return uid or "default"


def _load_all():
    try:
        with open(settings_file(), encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return {}
    users = data.get("users") if isinstance(data, dict) else None
    return users if isinstance(users, dict) else {}


def _clean_remap(remap):
    out = {}
    if not isinstance(remap, dict):
        return out
    for k, v in remap.items():
        if k in BUTTONS and v in BUTTONS and k != v:
            out[k] = v
    if sorted(out) != sorted(out.values()):
        return {}
    return out


def _settings(raw):
    s = dict(DEFAULTS)
    if isinstance(raw, dict):
        for key, choices in CHOICES.items():
            if raw.get(key) in choices:
                s[key] = raw[key]
        if isinstance(raw.get("swapSticks"), bool):
            s["swapSticks"] = raw["swapSticks"]
        s["remap"] = _clean_remap(raw.get("remap"))
    else:
        s["remap"] = {}
    return s


def load(uid):
    with _lock:
        return _settings(_load_all().get(uid))


def _save(uid, s):
    with _lock:
        users = _load_all()
        users[uid] = s
        path = settings_file()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"users": users}, f, sort_keys=True, indent=1)
        os.replace(tmp, path)
    return s


def assign(remap, button, function):
    if button not in BUTTONS or function not in BUTTONS:
        raise ValueError("unknown button")
    full = {b: remap.get(b, b) for b in BUTTONS}
    holder = next(b for b in BUTTONS if full[b] == function)
    full[holder], full[button] = full[button], function
    return {b: f for b, f in full.items() if b != f}


def preset_of(s):
    for name, remap in PRESETS.items():
        if remap == s["remap"]:
            return name
    return "custom"


def sdl_mapping(s):
    if not s["remap"] and not s["swapSticks"]:
        return ""
    pairs = [SDL_TARGET[s["remap"].get(b, b)] + ":" + SDL_SOURCE[b] for b in BUTTONS]
    sticks = "leftx:a2,lefty:a3,rightx:a0,righty:a1" if s["swapSticks"] else "leftx:a0,lefty:a1,rightx:a2,righty:a3"
    body = ",".join(sorted(pairs)) + "," + sticks + ",guide:b5,touchpad:b15,misc1:b16,platform:Linux,"
    lines = []
    for product, name in sorted(dualsense.PRODUCTS.items()):
        for bus in (dualsense.BUS_USB, dualsense.BUS_BT):
            guid = "%04x0000%04x0000%04x0000" % (_swap16(bus), _swap16(dualsense.VENDOR), _swap16(product)) + "00006800"
            lines.append(guid + "," + name + " (AnyPS5 OS)," + body)
    return "\n".join(lines)


def _swap16(v):
    return ((v & 0xFF) << 8) | (v >> 8)


def player_pattern(s, index):
    if s["playerLed"] == "off":
        return 0
    return dualsense.PLAYER_PATTERNS[min(index, 3) + 1]


def cycle_color(step, steps_per_color):
    i, frac = divmod(step, steps_per_color)
    a = CYCLE[i % len(CYCLE)]
    b = CYCLE[(i + 1) % len(CYCLE)]
    t = frac / steps_per_color
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def apply_payload(s, index):
    color = COLORS.get(s["color"]) or cycle_color(0, 1)
    return dualsense.common(lightbar=color, mic_led=MIC_LED[s["micLed"]], brightness=BRIGHTNESS.index(s["brightness"]),
                            player_leds=player_pattern(s, index), motor_power=(0, TRIGGER_CUT[s["trigger"]]))


def _pads():
    return dualsense.find()


def _others():
    return _padmaps.find()


def _animate(name, stop, send):
    steps, delay = ANIMATIONS[name]
    pads, rescan, step = [], 0.0, 0
    while not stop.is_set():
        now = time.monotonic()
        if now >= rescan:
            pads, rescan = _pads(), now + PAD_RESCAN_SECONDS
        payload = dualsense.common(lightbar=cycle_color(step, steps))
        for pad in pads:
            try:
                with _send_lock:
                    send(pad, payload)
            except OSError:
                rescan = 0.0
        step += 1
        stop.wait(delay)


def stop_animation():
    stop = _animation["stop"]
    _animation["stop"] = None
    if stop is not None:
        stop.set()


def start_animation(name, send=None):
    stop_animation()
    if name not in ANIMATIONS:
        return False
    stop = threading.Event()
    _animation["stop"] = stop
    threading.Thread(target=_animate, args=(name, stop, send or dualsense.send), daemon=True).start()
    return True


def apply(s, pads=None, send=None, animate=True):
    pads = _pads() if pads is None else pads
    send = send or dualsense.send
    if animate:
        stop_animation()
    done, errors = 0, []
    for i, pad in enumerate(pads):
        try:
            with _send_lock:
                send(pad, dualsense.lightbar_on())
            time.sleep(0.3)
            with _send_lock:
                send(pad, apply_payload(s, i))
            done += 1
        except OSError as e:
            errors.append(str(e))
    if animate and s["color"] in ANIMATIONS:
        start_animation(s["color"], send)
    return {"applied": done, "errors": errors}


def _public_pad(p):
    return {k: p[k] for k in ("id", "name", "model", "connection", "address", "battery", "leds", "writable")}


def kernel_info():
    return {
        "driver": os.path.isdir("/sys/module/hid_playstation"),
        "rumble": "evdev force feedback (FF_RUMBLE)",
        "leds": "rgb:indicator, white:player-1..5",
        "battery": "ps-controller-battery-<address>",
        "rawOnly": ["adaptive trigger effects", "motor power reduction", "LED brightness", "voice-coil haptics"],
    }


def status(uid):
    s = load(uid)
    return {
        "controllers": [_public_pad(p) for p in _pads()],
        "others": _others(),
        "dualsenseOnly": ["trigger", "brightness", "color", "playerLed", "micLed", "remap", "swapSticks"],
        "settings": s,
        "preset": preset_of(s),
        "presets": [{"id": k, "name": PRESET_NAMES[k]} for k in PRESETS],
        "buttons": [{"id": b, "name": BUTTON_NAMES[b]} for b in BUTTONS],
        "colors": list(COLORS) + list(ANIMATIONS),
        "micLeds": list(MIC_LED),
        "kernel": kernel_info(),
    }


def _set(uid, args):
    key = args.get("key")
    value = args.get("value")
    s = load(uid)
    if key in CHOICES:
        if value not in CHOICES[key]:
            raise ValueError("%s must be one of %s" % (key, ", ".join(CHOICES[key])))
        s[key] = value
    elif key == "swapSticks":
        if not isinstance(value, bool):
            raise ValueError("swapSticks must be true or false")
        s[key] = value
    else:
        raise ValueError("unknown controller setting " + str(key)[:40])
    return _changed(uid, s)


def _changed(uid, s):
    _save(uid, s)
    out = apply(s)
    out["settings"] = s
    out["preset"] = preset_of(s)
    return out


def _need_pads():
    pads = _pads()
    if not pads:
        raise RuntimeError("Connect a DualSense wireless controller with a USB cable or Bluetooth first.")
    return pads


def _cancel_timer(node):
    t = _timers.pop(node, None)
    if t:
        t.cancel()


def _release_triggers(pad):
    try:
        dualsense.send(pad, dualsense.common(right_trigger=dualsense.trigger_off(), left_trigger=dualsense.trigger_off()))
    except OSError:
        pass


def test(uid, what):
    s = load(uid)
    pads = _need_pads()
    if what == "rumble":
        if s["vibration"] == "off":
            raise ValueError("Vibration is off. Choose an intensity first.")
        level = 255 * RUMBLE_PERCENT[s["vibration"]] // 100
        for p in pads:
            dualsense.pulse(p, level, level, 0.6)
        return {"tested": "rumble", "level": level, "controllers": len(pads)}
    if what == "trigger":
        strength = TRIGGER_DEMO[s["trigger"]]
        if strength == 0:
            raise ValueError("Trigger effects are off. Choose an intensity first.")
        effect = dualsense.trigger_feedback(2, strength)
        for p in pads:
            _cancel_timer(p["node"])
            dualsense.send(p, dualsense.common(right_trigger=effect, left_trigger=effect))
            t = threading.Timer(TRIGGER_DEMO_SECONDS, _release_triggers, args=(p,))
            t.daemon = True
            _timers[p["node"]] = t
            t.start()
        return {"tested": "trigger", "strength": strength, "seconds": TRIGGER_DEMO_SECONDS, "controllers": len(pads)}
    if what == "light":
        out = apply(s, pads)
        if out["errors"]:
            raise OSError(out["errors"][0])
        return {"tested": "light", "color": s["color"], "controllers": len(pads)}
    raise ValueError("unknown test " + str(what)[:40])


def game_env(base, uid, pads=None, send=None):
    try:
        s = load(_uid({"uid": uid}))
    except ValueError:
        s = _settings(None)
    env = dict(base)
    if s["vibration"] != "strong":
        env["ANYPS5_RUMBLE"] = str(RUMBLE_PERCENT[s["vibration"]])
    mapping = sdl_mapping(s)
    if mapping:
        env["SDL_GAMECONTROLLERCONFIG"] = mapping
    _padmaps.game_env(env)
    apply(s, pads, send)
    return env


def handle(action, args):
    args = args or {}
    uid = _uid(args)
    if action == "status":
        return status(uid)
    if action == "set":
        return _set(uid, args)
    if action == "assign":
        s = load(uid)
        s["remap"] = assign(s["remap"], args.get("button"), args.get("function"))
        return _changed(uid, s)
    if action == "preset":
        name = args.get("preset")
        if name not in PRESETS:
            raise ValueError("unknown preset " + str(name)[:40])
        s = load(uid)
        s["remap"] = dict(PRESETS[name])
        return _changed(uid, s)
    if action == "resetButtons":
        s = load(uid)
        s["remap"] = {}
        s["swapSticks"] = False
        return _changed(uid, s)
    if action == "reset":
        return _changed(uid, _settings(None))
    if action == "apply":
        return apply(load(uid))
    if action == "test":
        return test(uid, args.get("what"))
    if action == "env":
        env = game_env({}, uid, pads=[])
        return {"env": env}
    raise NotImplementedError("unknown controller action " + str(action))
