import os
import shlex
import socket

OS_RELEASE = ("/etc/os-release", "/usr/lib/os-release")


def read_os_release(paths=OS_RELEASE):
    for path in paths:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        values = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, raw = line.partition("=")
            try:
                parts = shlex.split(raw, posix=True)
            except ValueError:
                parts = [raw.strip("\"'")]
            values[key.strip()] = " ".join(parts)
        return values
    return {}


def os_name(values):
    if values.get("PRETTY_NAME"):
        return values["PRETTY_NAME"]
    name = values.get("NAME", "")
    version = values.get("VERSION") or values.get("VERSION_ID") or ""
    return (name + " " + version).strip() or "Linux"


def kernel():
    try:
        u = os.uname()
        return u.release
    except AttributeError:
        return ""


def handle(action, args):
    if action == "info":
        return {
            "platform": "linux",
            "os": os_name(read_os_release()),
            "machine": socket.gethostname(),
            "kernel": kernel(),
            "testMode": False,
        }
    raise NotImplementedError("unknown platform action " + str(action))
