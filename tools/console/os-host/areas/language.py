import re
import shutil
import subprocess

LOCALE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]{0,63}$")
LAYOUT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
XKB_LISTS = ("/usr/share/X11/xkb/rules/base.lst", "/usr/share/X11/xkb/rules/evdev.lst")
TIMEOUT = 20


def _localectl():
    exe = shutil.which("localectl")
    if not exe:
        raise NotImplementedError("localectl is not installed")
    return exe


def _run(args):
    r = subprocess.run([_localectl(), "--no-pager"] + args, capture_output=True, text=True, timeout=TIMEOUT)
    if r.returncode != 0:
        msg = (r.stderr or r.stdout or "").strip().splitlines()
        raise RuntimeError(msg[-1] if msg else "localectl failed with code %d" % r.returncode)
    return r.stdout


def _tag(locale):
    base = locale.split(".", 1)[0].split("@", 1)[0]
    if base in ("", "C", "POSIX"):
        return ""
    return base.replace("_", "-")


def parse_status(text):
    fields = {}
    key = None
    for line in text.splitlines():
        if not line.strip():
            continue
        m = re.match(r"^\s*([A-Za-z0-9 ]+):\s?(.*)$", line)
        if m and "=" not in m.group(1):
            key = m.group(1).strip()
            fields[key] = [m.group(2).strip()] if m.group(2).strip() else []
        elif key is not None:
            fields[key].append(line.strip())
    def one(name):
        v = fields.get(name) or []
        v = v[0] if v else ""
        return "" if v in ("(unset)", "n/a") else v
    env = {}
    for item in fields.get("System Locale") or []:
        if "=" in item:
            k, v = item.split("=", 1)
            env[k.strip()] = v.strip()
    return {
        "env": env,
        "lang": env.get("LANG", ""),
        "vcKeymap": one("VC Keymap"),
        "x11Layout": one("X11 Layout"),
        "x11Model": one("X11 Model"),
        "x11Variant": one("X11 Variant"),
        "x11Options": one("X11 Options"),
    }


def layout_names():
    for path in XKB_LISTS:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        names = {}
        section = None
        for line in text.splitlines():
            if line.startswith("!"):
                section = line[1:].strip()
                continue
            if section == "layout":
                m = re.match(r"^\s+(\S+)\s+(.+?)\s*$", line)
                if m:
                    names[m.group(1)] = m.group(2)
        if names:
            return names
    return {}


def _lines(text):
    return [l.strip() for l in text.splitlines() if l.strip()]


def status():
    st = parse_status(_run(["status"]))
    names = layout_names()
    layouts = [l for l in st["x11Layout"].split(",") if l]
    return {
        "platform": "linux",
        "canChange": True,
        "language": {"tag": _tag(st["lang"]), "locale": st["lang"]},
        "region": {"tag": _tag(st["env"].get("LC_TIME") or st["lang"]), "locale": st["env"].get("LC_TIME") or st["lang"]},
        "env": st["env"],
        "vcKeymap": st["vcKeymap"],
        "x11Layout": st["x11Layout"],
        "x11Model": st["x11Model"],
        "x11Variant": st["x11Variant"],
        "keyboards": [{"id": l, "lang": "", "name": names.get(l, l)} for l in layouts],
    }


def list_locales():
    return [l for l in _lines(_run(["list-locales"])) if LOCALE_RE.match(l)]


def list_layouts():
    return [l for l in _lines(_run(["list-x11-keymap-layouts"])) if LAYOUT_RE.match(l)]


def _arg(args, name):
    v = (args or {}).get(name)
    if not isinstance(v, str) or not v:
        raise ValueError("missing %s" % name)
    return v


def set_locale(args):
    loc = _arg(args, "locale")
    if not LOCALE_RE.match(loc):
        raise ValueError("invalid locale")
    if loc not in list_locales():
        raise ValueError("%s is not an installed locale" % loc)
    _run(["set-locale", "LANG=" + loc])
    return {"locale": loc, "tag": _tag(loc), "restart": True}


def set_layout(args):
    layout = _arg(args, "layout")
    if not LAYOUT_RE.match(layout):
        raise ValueError("invalid keyboard layout")
    if layout not in list_layouts():
        raise ValueError("%s is not a known keyboard layout" % layout)
    model = parse_status(_run(["status"]))["x11Model"]
    cmd = ["set-x11-keymap", layout]
    if model and LAYOUT_RE.match(model):
        cmd.append(model)
    _run(cmd)
    return {"layout": layout, "name": layout_names().get(layout, layout), "restart": True}


def handle(action, args):
    if action == "status":
        return status()
    if action == "locales":
        locs = list_locales()
        return {"locales": [{"id": l, "tag": _tag(l)} for l in locs]}
    if action == "setLocale":
        return set_locale(args)
    if action == "layouts":
        names = layout_names()
        return {"layouts": [{"id": l, "name": names.get(l, l)} for l in list_layouts()]}
    if action == "setLayout":
        return set_layout(args)
    raise NotImplementedError("unknown language action %s" % action)
