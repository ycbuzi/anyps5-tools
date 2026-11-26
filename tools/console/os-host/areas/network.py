import os
import re
import shutil
import socket
import subprocess
import time
import urllib.request

DOWN_URL = "https://speed.cloudflare.com/__down?bytes="
UP_URL = "https://speed.cloudflare.com/__up"
TEST_CAP = 20.0
MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
DEV_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,15}$")


def _nmcli():
    p = shutil.which("nmcli")
    if not p:
        raise NotImplementedError("NetworkManager (nmcli) is not installed")
    return p


def _env():
    e = dict(os.environ)
    e["LC_ALL"] = "C"
    e["LANG"] = "C"
    return e


def _nm_message(r):
    text = (r.stderr or "").strip() or (r.stdout or "").strip() or ("nmcli exited with code %d" % r.returncode)
    line = text.splitlines()[-1].strip()
    if line.startswith("Error: "):
        line = line[7:]
    low = line.lower()
    if "secrets were required" in low or "802-1x supplicant" in low or "no secrets" in low:
        return "The password is not correct."
    if "no network with ssid" in low:
        return "The network was not found. Search again or check the name."
    return line


def _run(args, timeout=20):
    r = subprocess.run([_nmcli()] + args, capture_output=True, text=True, timeout=timeout, env=_env())
    if r.returncode != 0:
        raise RuntimeError(_nm_message(r))
    return r.stdout


def split_terse(line):
    out, cur, i = [], [], 0
    while i < len(line):
        c = line[i]
        if c == "\\" and i + 1 < len(line):
            cur.append(line[i + 1])
            i += 2
            continue
        if c == ":":
            out.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    out.append("".join(cur))
    return out


def unescape(value):
    out, i = [], 0
    while i < len(value):
        if value[i] == "\\" and i + 1 < len(value):
            out.append(value[i + 1])
            i += 2
            continue
        out.append(value[i])
        i += 1
    return "".join(out)


def parse_multiline(text):
    fields = {}
    for line in text.splitlines():
        k, sep, v = line.partition(":")
        if not sep:
            continue
        v = unescape(v).strip()
        fields.setdefault(k.strip(), v)
    return fields


def _blank(v):
    return "" if v in (None, "--") else v


def prefix_to_mask(prefix):
    try:
        p = int(prefix)
    except (TypeError, ValueError):
        return ""
    if p < 0 or p > 32:
        return ""
    n = (0xFFFFFFFF << (32 - p)) & 0xFFFFFFFF if p else 0
    return ".".join(str((n >> s) & 255) for s in (24, 16, 8, 0))


def band_of(freq):
    m = re.match(r"\s*(\d+)", freq or "")
    if not m:
        return ""
    f = int(m.group(1))
    if f >= 5925:
        return "6 GHz"
    if f >= 4900:
        return "5 GHz"
    if 2400 <= f < 2500:
        return "2.4 GHz"
    return ""


def general():
    out = _run(["-t", "-f", "STATE,CONNECTIVITY,NETWORKING,WIFI-HW,WIFI", "general", "status"])
    line = next((l for l in out.splitlines() if l.strip()), "")
    f = split_terse(line) + [""] * 5
    return {"state": f[0], "connectivity": f[1], "networking": f[2] == "enabled",
            "wifiHw": f[3] not in ("missing", "disabled", ""), "wifiRadio": f[4] == "enabled"}


def devices():
    out = _run(["-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"])
    res = []
    for line in out.splitlines():
        if not line.strip():
            continue
        f = split_terse(line) + [""] * 4
        res.append({"device": f[0], "type": f[1], "state": f[2], "connection": _blank(f[3])})
    return res


def _connected(d):
    return d["state"].startswith("connected")


def device_details(dev):
    f = parse_multiline(_run(["-t", "-f", "GENERAL,IP4", "device", "show", dev]))
    addr = _blank(f.get("IP4.ADDRESS[1]", ""))
    ip, _, prefix = addr.partition("/")
    dns = []
    i = 1
    while ("IP4.DNS[%d]" % i) in f:
        v = _blank(f["IP4.DNS[%d]" % i])
        if v:
            dns.append(v)
        i += 1
    return {"device": dev, "type": f.get("GENERAL.TYPE", ""), "mac": _blank(f.get("GENERAL.HWADDR", "")).upper(),
            "connection": _blank(f.get("GENERAL.CONNECTION", "")), "ip": ip, "mask": prefix_to_mask(prefix) if prefix else "",
            "prefix": int(prefix) if prefix.isdigit() else None, "gateway": _blank(f.get("IP4.GATEWAY", "")), "dns": dns}


def _wifi_rows(dev, rescan):
    args = ["-t", "-f", "IN-USE,SSID,BSSID,SIGNAL,SECURITY,CHAN,FREQ", "device", "wifi", "list", "--rescan", rescan]
    if dev:
        args += ["ifname", dev]
    out = _run(args, timeout=30)
    rows = []
    for line in out.splitlines():
        if not line.strip():
            continue
        f = split_terse(line) + [""] * 7
        sec = _blank(f[4]).strip()
        try:
            sig = int(f[3])
        except ValueError:
            sig = 0
        rows.append({"inUse": f[0].strip() == "*", "ssid": f[1], "bssid": f[2].upper(), "signal": sig,
                     "security": sec, "secured": bool(sec), "channel": f[5], "band": band_of(f[6])})
    return rows


def _wifi_device(devs):
    w = [d for d in devs if d["type"] == "wifi"]
    c = [d for d in w if _connected(d)]
    return (c or w or [None])[0]


def status(args):
    g = general()
    devs = devices()
    eth = [d for d in devs if d["type"] == "ethernet"]
    wdev = _wifi_device(devs)
    prim = next((d for d in devs if _connected(d) and d["type"] in ("ethernet", "wifi")), None)
    if prim is None:
        prim = next((d for d in devs if _connected(d) and d["type"] not in ("loopback", "wifi-p2p")), None)
    res = {"platform": "linux", "control": True, "connected": prim is not None, "internet": g["connectivity"] == "full",
           "connectivity": g["connectivity"], "networking": g["networking"], "wifiHw": g["wifiHw"] and wdev is not None,
           "wifiRadio": g["wifiRadio"], "method": "", "device": "", "ssid": "", "signal": None, "ip": "", "mask": "",
           "gateway": "", "dns": [], "mac": "",
           "lan": {"present": bool(eth), "connected": any(_connected(d) for d in eth),
                   "device": eth[0]["device"] if eth else ""}}
    if prim is not None:
        det = device_details(prim["device"])
        res.update({"device": prim["device"], "ip": det["ip"], "mask": det["mask"], "gateway": det["gateway"],
                    "dns": det["dns"], "mac": det["mac"]})
        res["method"] = {"ethernet": "LAN Cable", "wifi": "Wi-Fi"}.get(prim["type"], prim["type"])
        if prim["type"] == "wifi":
            try:
                cur = next((r for r in _wifi_rows(prim["device"], "no") if r["inUse"]), None)
            except RuntimeError:
                cur = None
            res["ssid"] = cur["ssid"] if cur else (prim["connection"] or "")
            res["signal"] = cur["signal"] if cur else None
            res["security"] = cur["security"] if cur else ""
            res["band"] = cur["band"] if cur else ""
    return res


def wifi_list(args):
    g = general()
    devs = devices()
    wdev = _wifi_device(devs)
    if wdev is None or not g["wifiHw"]:
        return {"present": False, "radio": False, "networks": []}
    if not g["wifiRadio"]:
        return {"present": True, "radio": False, "device": wdev["device"], "networks": []}
    rescan = "yes" if args.get("rescan") is True else "auto"
    try:
        rows = _wifi_rows(wdev["device"], rescan)
    except RuntimeError:
        if rescan != "yes":
            raise
        rows = _wifi_rows(wdev["device"], "auto")
    best = {}
    for r in rows:
        if not r["ssid"]:
            continue
        k = r["ssid"]
        if k not in best or r["inUse"] or (r["signal"] > best[k]["signal"] and not best[k]["inUse"]):
            best[k] = r
    nets = sorted(best.values(), key=lambda r: (not r["inUse"], -r["signal"], r["ssid"].lower()))
    return {"present": True, "radio": True, "device": wdev["device"], "networks": nets}


def _text(args, key):
    v = args.get(key)
    if v is None:
        return ""
    if not isinstance(v, str):
        raise ValueError(key + " must be text")
    return v


def _ssid(args):
    s = _text(args, "ssid")
    if not s:
        raise ValueError("Enter the network name (SSID).")
    if len(s.encode("utf-8")) > 32:
        raise ValueError("A network name can be at most 32 bytes.")
    if any(ord(c) < 32 or ord(c) == 127 for c in s):
        raise ValueError("The network name has characters that cannot be used.")
    if s.startswith("-"):
        raise ValueError("Network names that start with '-' are not supported.")
    return s


def _password(args):
    p = _text(args, "password")
    if not p:
        return ""
    if len(p) < 5 or len(p) > 64:
        raise ValueError("The password must be 8 to 63 characters (or a 64-digit key).")
    if any(ord(c) < 32 or ord(c) == 127 for c in p):
        raise ValueError("The password has characters that cannot be used.")
    return p


def _wifi_dev_or_fail():
    wdev = _wifi_device(devices())
    if wdev is None:
        raise RuntimeError("No Wi-Fi device was found.")
    return wdev["device"]


def connect(args):
    hidden = args.get("hidden") is True
    bssid = _text(args, "bssid").upper()
    if bssid and not MAC_RE.match(bssid):
        raise ValueError("Bad BSSID")
    use_bssid = bool(bssid) and not hidden
    ssid = _text(args, "ssid") if use_bssid else _ssid(args)
    pw = _password(args)
    dev = _wifi_dev_or_fail()
    target = bssid if use_bssid else ssid
    cmd = ["--wait", "40", "device", "wifi", "connect", target]
    if pw:
        cmd += ["password", pw]
    cmd += ["ifname", dev]
    if hidden:
        cmd += ["hidden", "yes"]
    _run(cmd, timeout=50)
    return {"connected": True, "ssid": ssid or target, "device": dev}


def disconnect(args):
    devs = devices()
    want = _text(args, "device")
    if want:
        if not DEV_RE.match(want):
            raise ValueError("Bad device name")
        d = next((x for x in devs if x["device"] == want), None)
        if d is None:
            raise ValueError("No such network device: " + want)
    else:
        d = next((x for x in devs if x["type"] == "wifi" and _connected(x)), None) or \
            next((x for x in devs if x["type"] in ("ethernet", "wifi") and _connected(x)), None)
        if d is None:
            return {"disconnected": False, "device": ""}
    _run(["device", "disconnect", d["device"]], timeout=30)
    return {"disconnected": True, "device": d["device"]}


def _onoff(args):
    v = args.get("on")
    if not isinstance(v, bool):
        raise ValueError("on must be true or false")
    return v


def radio(args):
    on = _onoff(args)
    _run(["radio", "wifi", "on" if on else "off"])
    return {"wifiRadio": on}


def networking(args):
    on = _onoff(args)
    _run(["networking", "on" if on else "off"])
    return {"networking": on}


def _local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("1.1.1.1", 80))
        return s.getsockname()[0]
    except OSError:
        return ""
    finally:
        s.close()


def test_ip(args):
    if shutil.which("nmcli"):
        st = status(args)
        return {"ok": bool(st["ip"]), "ip": st["ip"], "method": st["method"]}
    ip = _local_ip()
    return {"ok": bool(ip) and not ip.startswith("127."), "ip": ip, "method": ""}


def _request(url, data=None):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "AnyPS5"})
    if data is not None:
        req.add_header("Content-Type", "application/octet-stream")
    return urllib.request.urlopen(req, timeout=TEST_CAP)


def test_internet(args):
    t0 = time.monotonic()
    try:
        with _request(DOWN_URL + "0") as r:
            r.read()
            code = r.status
    except OSError as e:
        return {"ok": False, "host": "speed.cloudflare.com", "error": str(getattr(e, "reason", e))}
    return {"ok": 200 <= code < 400, "host": "speed.cloudflare.com", "ms": int((time.monotonic() - t0) * 1000)}


def _size(args, key, default, lo, hi):
    v = args.get(key, default)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(key + " must be a number")
    return int(min(hi, max(lo, v)))


def test_download(args):
    n = _size(args, "bytes", 10000000, 100000, 25000000)
    got = 0
    t0 = time.monotonic()
    with _request(DOWN_URL + str(n)) as r:
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            got += len(chunk)
            if time.monotonic() - t0 > TEST_CAP:
                break
    secs = max(time.monotonic() - t0, 0.001)
    return {"bytes": got, "seconds": round(secs, 3), "mbps": round(got * 8 / secs / 1e6, 1)}


def test_upload(args):
    n = _size(args, "bytes", 2000000, 100000, 10000000)
    body = bytes(n)
    t0 = time.monotonic()
    with _request(UP_URL, body) as r:
        r.read()
    secs = max(time.monotonic() - t0, 0.001)
    return {"bytes": n, "seconds": round(secs, 3), "mbps": round(n * 8 / secs / 1e6, 1)}


ACTIONS = {"status": status, "wifi_list": wifi_list, "connect": connect, "disconnect": disconnect, "radio": radio,
           "networking": networking, "test_ip": test_ip, "test_internet": test_internet,
           "test_download": test_download, "test_upload": test_upload}


def handle(action, args):
    fn = ACTIONS.get(action)
    if fn is None:
        raise NotImplementedError("unknown network action " + str(action))
    return fn(args if isinstance(args, dict) else {})
