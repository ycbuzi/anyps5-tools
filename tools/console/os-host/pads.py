#!/usr/bin/env python3
import json
import os
import re
import sys

import dualsense

BUS_NAMES = {0x0003: "usb", 0x0005: "bluetooth"}
VENDOR_FAMILY = {0x054C: "playstation", 0x045E: "xbox", 0x057E: "nintendo"}
NAME_FAMILY = (
    ("xbox", "xbox"), ("x-box", "xbox"), ("xinput", "xbox"),
    ("dualsense", "playstation"), ("dualshock", "playstation"), ("playstation", "playstation"),
    ("nintendo", "nintendo"), ("joy-con", "nintendo"), ("pro controller", "nintendo"),
)
FAMILY_LABEL = {"playstation": "PlayStation", "xbox": "Xbox", "nintendo": "Nintendo", "generic": "Controller"}
SKIP_SUFFIX = ("motion sensors", "touchpad", "imu")
ID_RE = re.compile(r"Bus=([0-9a-fA-F]+)\s+Vendor=([0-9a-fA-F]+)\s+Product=([0-9a-fA-F]+)\s+Version=([0-9a-fA-F]+)")


def family(vendor, name):
    if vendor in VENDOR_FAMILY:
        return VENDOR_FAMILY[vendor]
    low = (name or "").lower()
    for needle, fam in NAME_FAMILY:
        if needle in low:
            return fam
    return "generic"


def mapping_file():
    return os.environ.get("ANYPS5_PADMAPS_FILE") or os.path.join(os.path.expanduser("~"), ".local", "share", "anyps5", "padmaps.txt")


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return ""


def _battery(sys_root, sysfs):
    if not sysfs:
        return None
    folder = os.path.join(sys_root, sysfs.lstrip("/"))
    for _ in range(3):
        supply = os.path.join(folder, "power_supply")
        try:
            names = sorted(os.listdir(supply))
        except OSError:
            names = []
        for name in names:
            cap = _read(os.path.join(supply, name, "capacity"))
            if not cap:
                continue
            try:
                level = int(cap)
            except ValueError:
                continue
            return {"level": level, "status": _read(os.path.join(supply, name, "status"))}
        folder = os.path.dirname(folder)
    return None


def parse(text):
    out = []
    for block in text.split("\n\n"):
        ids, name, sysfs, handlers = None, "", "", ""
        for line in block.splitlines():
            if line.startswith("I:"):
                m = ID_RE.search(line)
                if m:
                    ids = tuple(int(g, 16) for g in m.groups())
            elif line.startswith("N: Name="):
                name = line[len("N: Name="):].strip().strip('"')
            elif line.startswith("S: Sysfs="):
                sysfs = line[len("S: Sysfs="):].strip()
            elif line.startswith("H: Handlers="):
                handlers = line[len("H: Handlers="):]
        if not ids or not name or not re.search(r"\bjs\d+\b", handlers):
            continue
        if name.lower().endswith(SKIP_SUFFIX):
            continue
        out.append({"bus": ids[0], "vendor": ids[1], "product": ids[2], "version": ids[3], "name": name, "sysfs": sysfs})
    return out


def find(proc_path="/proc/bus/input/devices", sys_root="/sys", include_dualsense=False):
    found = []
    for d in parse(_read(proc_path)):
        if not include_dualsense and d["vendor"] == dualsense.VENDOR and d["product"] in dualsense.PRODUCTS:
            continue
        fam = family(d["vendor"], d["name"])
        found.append({
            "name": d["name"],
            "family": fam,
            "type": FAMILY_LABEL[fam],
            "vendor": d["vendor"],
            "product": d["product"],
            "connection": BUS_NAMES.get(d["bus"], ""),
            "battery": _battery(sys_root, d["sysfs"]),
        })
    return found


def game_env(env):
    path = mapping_file()
    try:
        if os.path.getsize(path) > 0:
            env.setdefault("SDL_GAMECONTROLLERCONFIG_FILE", path)
    except OSError:
        pass
    return env


def main(argv):
    print(json.dumps(find(include_dualsense="--all" in argv), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
