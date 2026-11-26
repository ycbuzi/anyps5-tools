"""Date and time for AnyPS5 OS (sysCall "datetime") through systemd's timedatectl.

This file is named datetime.py, so it must not import the standard library module of the same name; the host should
load areas by file path (importlib.util.spec_from_file_location) so it never shadows the real datetime module.
"""
import os
import re
import shutil
import subprocess
import time

ZONE_RE = re.compile(r"^[A-Za-z0-9_+\-]+(/[A-Za-z0-9_+\-]+){0,3}$")
TIMEOUT = 15


def _timedatectl():
    exe = shutil.which("timedatectl")
    if not exe:
        raise NotImplementedError("timedatectl is not installed (systemd is needed for date and time settings)")
    return exe


def _run(argv, timeout=TIMEOUT):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError("timedatectl did not answer in time")
    return p


def _fail(p, what):
    msg = (p.stderr or p.stdout or "").strip()
    low = msg.lower()
    if "access denied" in low or "interactive authentication required" in low or "not authorized" in low:
        raise PermissionError(what + ": AnyPS5 is not allowed to change this (polkit denied it)")
    raise RuntimeError(what + ": " + (msg or "timedatectl exit code %d" % p.returncode))


def _bool(v):
    if v is None:
        return None
    return v.strip().lower() in ("yes", "true", "1", "on")


def parse_show(text):
    out = {}
    for line in (text or "").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _offset_text(seconds):
    sign = "-" if seconds < 0 else "+"
    seconds = abs(int(seconds))
    return "%s%02d:%02d" % (sign, seconds // 3600, (seconds % 3600) // 60)


def _local_zone_facts():
    if "TZ" in os.environ or not hasattr(time, "tzset"):
        return {}
    time.tzset()
    lt = time.localtime()
    facts = {"dst": bool(lt.tm_isdst > 0)}
    if getattr(lt, "tm_gmtoff", None) is not None:
        facts["utcOffset"] = _offset_text(lt.tm_gmtoff)
    if getattr(lt, "tm_zone", None):
        facts["abbrev"] = lt.tm_zone
    facts["supportsDst"] = time.daylight != 0
    return facts


def zone_label(zone):
    parts = zone.split("/")
    rest = parts[1:] if len(parts) > 1 else parts
    return " - ".join(p.replace("_", " ") for p in rest)


def zone_group(zone):
    return zone.split("/", 1)[0] if "/" in zone else "Other"


def status():
    exe = _timedatectl()
    p = _run([exe, "show"])
    if p.returncode != 0:
        _fail(p, "Could not read the date and time settings")
    s = parse_show(p.stdout)
    tz = s.get("Timezone") or None
    can_ntp = _bool(s.get("CanNTP"))
    d = {
        "platform": "linux",
        "timezone": tz,
        "timezoneLabel": zone_label(tz) if tz else None,
        "ntp": _bool(s.get("NTP")),
        "ntpSynchronized": _bool(s.get("NTPSynchronized")),
        "localRtc": _bool(s.get("LocalRTC")),
        "localTime": s.get("TimeUSec") or None,
        "epochMs": int(time.time() * 1000),
        "canSetTimezone": True,
        "canSetNtp": bool(can_ntp),
        "autoTimezone": None,
        "canSetAutoTimezone": False,
        "utcOffset": None,
        "dst": None,
        "abbrev": None,
        "supportsDst": None,
    }
    if can_ntp is False:
        d["syncNote"] = "No time sync service (such as systemd-timesyncd) is installed."
    d.update(_local_zone_facts())
    return d


def list_zones():
    exe = _timedatectl()
    p = _run([exe, "list-timezones", "--no-pager"])
    if p.returncode != 0:
        _fail(p, "Could not list time zones")
    return [z.strip() for z in p.stdout.splitlines() if z.strip() and ZONE_RE.match(z.strip())]


def zones():
    exe = _timedatectl()
    try:
        cur = parse_show(_run([exe, "show", "-p", "Timezone"]).stdout).get("Timezone")
    except RuntimeError:
        cur = None
    return {
        "grouping": "region",
        "current": cur,
        "zones": [{"id": z, "label": zone_label(z), "group": zone_group(z)} for z in list_zones()],
    }


def set_timezone(args):
    zone = args.get("zone") if isinstance(args, dict) else None
    if not isinstance(zone, str) or len(zone) > 64 or not ZONE_RE.match(zone):
        raise ValueError("Not a valid time zone name")
    if zone not in list_zones():
        raise ValueError("Unknown time zone: " + zone)
    exe = _timedatectl()
    p = _run([exe, "set-timezone", zone])
    if p.returncode != 0:
        _fail(p, "Could not set the time zone")
    return status()


def set_ntp(args):
    on = args.get("on") if isinstance(args, dict) else None
    if not isinstance(on, bool):
        raise ValueError("on must be true or false")
    exe = _timedatectl()
    p = _run([exe, "set-ntp", "true" if on else "false"])
    if p.returncode != 0:
        _fail(p, "Could not change automatic date and time")
    return status()


def handle(action, args):
    args = args or {}
    if action == "status":
        return status()
    if action == "zones":
        return zones()
    if action == "setTimezone":
        return set_timezone(args)
    if action == "setNtp":
        return set_ntp(args)
    if action == "setAutoTimezone":
        raise NotImplementedError("Setting the time zone automatically is not available on this system")
    raise NotImplementedError("unknown datetime action " + str(action))
