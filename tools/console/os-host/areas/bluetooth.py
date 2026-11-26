import re
import shutil
import subprocess
import time

MAC_RE = re.compile(r"^[0-9A-F]{2}(?::[0-9A-F]{2}){5}$")
DEVICE_LINE_RE = re.compile(r"^Device ([0-9A-F]{2}(?::[0-9A-F]{2}){5}) ?(.*)$", re.IGNORECASE)
REDISCOVER_SECONDS = 15
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x01|\x02")
ICON_TYPES = {
    "input-gaming": "gamepad",
    "audio-headset": "audio",
    "audio-headphones": "audio",
    "audio-card": "audio",
    "audio-speakers": "audio",
    "input-keyboard": "keyboard",
    "input-mouse": "mouse",
    "input-tablet": "mouse",
    "phone": "phone",
    "computer": "computer",
}


def _tool():
    path = shutil.which("bluetoothctl")
    if not path:
        raise NotImplementedError("bluetoothctl is not installed (package bluez-utils)")
    return path


def _clean(text):
    return ANSI_RE.sub("", (text or "").replace("\r", ""))


def _run(args, timeout=10):
    try:
        proc = subprocess.run([_tool()] + list(args), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError("Bluetooth did not answer in time. Is the bluetooth service running?")
    return proc.returncode, _clean(proc.stdout) + "\n" + _clean(proc.stderr)


def _mac(args):
    value = (args or {}).get("address")
    if not isinstance(value, str):
        raise ValueError("address is required")
    value = value.strip().upper().replace("-", ":")
    if not MAC_RE.match(value):
        raise ValueError("not a Bluetooth address: " + value[:32])
    return value


def _fields(text):
    out = {}
    for line in text.split("\n"):
        line = line.strip()
        if ":" not in line or line.startswith("["):
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        if key and key not in out:
            out[key] = val.strip()
    return out


def _yes(value):
    return value is not None and value.strip().lower() == "yes"


def _device_list(text):
    found = []
    seen = set()
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("[") and "] " in line:
            line = line.split("] ", 1)[1].strip()
        m = DEVICE_LINE_RE.match(line)
        if not m:
            continue
        mac = m.group(1).upper()
        if mac in seen:
            continue
        seen.add(mac)
        name = m.group(2).strip()
        if name.upper().replace("-", ":") == mac:
            name = ""
        found.append({"address": mac, "name": name})
    return found


def _type_from_class(value):
    try:
        token = value.split()[0]
        cod = int(token, 16) if token.lower().startswith("0x") else int(token)
    except (ValueError, AttributeError, IndexError):
        return None
    major = (cod >> 8) & 0x1F
    minor = (cod >> 2) & 0x3F
    if major == 1:
        return "computer"
    if major == 2:
        return "phone"
    if major == 4:
        return "audio"
    if major == 5:
        if (minor & 0x0F) in (1, 2):
            return "gamepad"
        kind = (minor >> 4) & 3
        if kind == 2:
            return "mouse"
        if kind in (1, 3):
            return "keyboard"
    return None


def _type(info):
    icon = info.get("Icon", "")
    if icon in ICON_TYPES:
        return ICON_TYPES[icon]
    if icon.startswith("audio"):
        return "audio"
    if "Class" in info:
        t = _type_from_class(info["Class"])
        if t:
            return t
    return "other"


def _info(mac):
    code, out = _run(["info", mac])
    if "not available" in out and "Device " + mac + " (" not in out:
        return None
    return _fields(out)


def _describe(mac, name=""):
    info = _info(mac) or {}
    label = info.get("Alias") or info.get("Name") or name
    if label.upper().replace("-", ":") == mac:
        label = ""
    return {
        "address": mac,
        "name": label,
        "type": _type(info),
        "paired": _yes(info.get("Paired")),
        "trusted": _yes(info.get("Trusted")),
        "connected": _yes(info.get("Connected")),
        "blocked": _yes(info.get("Blocked")),
    }


def _devices(filter_name=None):
    args = ["devices"] + ([filter_name] if filter_name else [])
    code, out = _run(args)
    if filter_name == "Paired" and code != 0 and not _device_list(out):
        code, out = _run(["paired-devices"])
    return _device_list(out)


def _controller():
    code, out = _run(["show"])
    if "No default controller available" in out:
        return None
    m = re.search(r"Controller ([0-9A-F]{2}(?::[0-9A-F]{2}){5})", out, re.IGNORECASE)
    if not m:
        return None
    f = _fields(out)
    return {
        "address": m.group(1).upper(),
        "name": f.get("Alias") or f.get("Name") or "",
        "powered": _yes(f.get("Powered")),
    }


def _status():
    ctl = _controller()
    result = {
        "platform": "linux",
        "available": ctl is not None,
        "adapter": ctl["name"] if ctl else "",
        "adapterAddress": ctl["address"] if ctl else "",
        "powered": ctl["powered"] if ctl else False,
        "canPower": ctl is not None,
        "canPair": ctl is not None,
        "devices": [],
    }
    if not ctl:
        return result
    known = {}
    for flt in ("Paired", "Trusted", "Connected"):
        for d in _devices(flt):
            known.setdefault(d["address"], d["name"])
    devices = [_describe(mac, name) for mac, name in known.items()]
    devices.sort(key=lambda d: (not d["connected"], d["name"].lower()))
    result["devices"] = devices
    return result


def _need_power():
    ctl = _controller()
    if not ctl:
        raise RuntimeError("No Bluetooth adapter found")
    if not ctl["powered"]:
        raise RuntimeError("Bluetooth is off. Turn it on first.")


def _scan(args):
    seconds = (args or {}).get("seconds", 10)
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        raise ValueError("seconds must be a number")
    seconds = max(3, min(30, int(seconds)))
    _need_power()
    _run(["--timeout", str(seconds), "scan", "on"], timeout=seconds + 15)
    paired = {d["address"] for d in _devices("Paired")}
    found = []
    for d in _devices()[:40]:
        if d["address"] in paired:
            continue
        item = _describe(d["address"], d["name"])
        if item["paired"]:
            continue
        found.append(item)
    found.sort(key=lambda d: (d["name"] == "", d["type"] != "gamepad", d["name"].lower()))
    return {"devices": found}


def _check(code, out, success, failure_prefix, accept=()):
    if any(s in out for s in success) or any(a in out for a in accept):
        return
    for line in out.split("\n"):
        line = line.strip()
        if line.startswith("Failed") or "org.bluez.Error" in line or "not available" in line:
            raise RuntimeError(line)
    if code == 0:
        return
    raise RuntimeError(failure_prefix)


def _stop(proc):
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def _rediscover(mac):
    if _info(mac) is not None:
        return None
    seconds = REDISCOVER_SECONDS
    proc = subprocess.Popen([_tool(), "--timeout", str(seconds + 40), "scan", "on"],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        time.sleep(1)
        if _info(mac) is not None:
            return proc
    _stop(proc)
    raise RuntimeError("The accessory is no longer in pairing mode. Put it in pairing mode again and search again.")


def _pair(mac):
    _need_power()
    _run(["pairable", "on"])
    proc = _rediscover(mac)
    try:
        code, out = _run(["pair", mac], timeout=35)
    finally:
        _stop(proc)
    _check(code, out, ("Pairing successful",), "Pairing failed", accept=("AlreadyExists",))
    _trust(mac)
    return {"address": mac, "paired": True, "trusted": True}


def _trust(mac):
    code, out = _run(["trust", mac])
    _check(code, out, ("trust succeeded",), "Could not trust the accessory")
    return {"address": mac, "trusted": True}


def _connect(mac):
    code, out = _run(["connect", mac], timeout=30)
    _check(code, out, ("Connection successful",), "Connection failed", accept=("AlreadyConnected",))
    return {"address": mac, "connected": True}


def _disconnect(mac):
    code, out = _run(["disconnect", mac], timeout=20)
    _check(code, out, ("Successful disconnected",), "Disconnect failed", accept=("NotConnected",))
    return {"address": mac, "connected": False}


def _remove(mac):
    code, out = _run(["remove", mac], timeout=20)
    _check(code, out, ("Device has been removed",), "Could not remove the accessory")
    return {"address": mac, "removed": True}


def _power(args):
    on = (args or {}).get("on")
    if not isinstance(on, bool):
        raise ValueError("on must be true or false")
    state = "on" if on else "off"
    code, out = _run(["power", state])
    _check(code, out, ("power " + state + " succeeded",), "Could not turn Bluetooth " + state)
    return {"powered": on}


def handle(action, args):
    args = args or {}
    if action == "status":
        return _status()
    if action == "scan":
        return _scan(args)
    if action == "power":
        return _power(args)
    if action == "pair":
        return _pair(_mac(args))
    if action == "trust":
        return _trust(_mac(args))
    if action == "connect":
        return _connect(_mac(args))
    if action == "disconnect":
        return _disconnect(_mac(args))
    if action == "remove":
        return _remove(_mac(args))
    raise NotImplementedError("unknown bluetooth action " + str(action))
