import datetime
import json
import os
import re
import tempfile
import threading

UID_RX = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
TIME_RX = re.compile(r"^([01][0-9]|2[0-3]):([0-5][0-9])$")
TITLE_RX = re.compile(r"\(([A-Z]{4}[0-9]{5})\)\s*$")
UNLIMITED = 2 ** 31 - 1
KEEP_DAYS = 31

_lock = threading.Lock()


def usage_file():
    return os.path.join(os.path.expanduser("~"), ".local", "share", "anyps5", "family-usage.json")


def _now():
    return datetime.datetime.now()


def _today():
    return _now().strftime("%Y-%m-%d")


def _valid_uid(uid):
    return isinstance(uid, str) and bool(UID_RX.match(uid))


def _minutes(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return 0
    if v != v or v <= 0:
        return 0
    return int(round(min(v, UNLIMITED)))


def _load():
    path = usage_file()
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return {}
    out = {}
    if not isinstance(raw, dict):
        return out
    for day, users in raw.items():
        try:
            datetime.datetime.strptime(day, "%Y-%m-%d")
        except (TypeError, ValueError):
            continue
        if not isinstance(users, dict):
            continue
        clean = {}
        for uid, mins in users.items():
            m = _minutes(mins)
            if _valid_uid(uid) and m > 0:
                clean[uid] = m
        out[day] = clean
    return out


def _save(data):
    oldest = (_now().date() - datetime.timedelta(days=KEEP_DAYS - 1)).strftime("%Y-%m-%d")
    keep = {d: u for d, u in data.items() if d >= oldest}
    path = usage_file()
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".family-usage-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(keep, f, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def add_minute(uid):
    if not _valid_uid(uid):
        raise ValueError("invalid user id")
    with _lock:
        data = _load()
        day = data.setdefault(_today(), {})
        day[uid] = day.get(uid, 0) + 1
        _save(data)
        return day[uid]


def minutes_today(uid):
    if not _valid_uid(uid):
        return 0
    with _lock:
        return _load().get(_today(), {}).get(uid, 0)


def _clock(t):
    if not isinstance(t, str):
        return None
    m = TIME_RX.match(t)
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def window_state(start, end, n):
    a, b = _clock(start), _clock(end)
    if a is None and b is None:
        return True, None
    if a is not None and b is not None:
        if a == b:
            return True, None
        if a < b:
            return (True, b - n) if a <= n < b else (False, 0)
        if n >= a:
            return True, 1440 - n + b
        if n < b:
            return True, b - n
        return False, 0
    if a is not None:
        return (True, 1440 - n) if n >= a else (False, 0)
    return (True, b - n) if n < b else (False, 0)


def _family(uid, settings_file):
    if not _valid_uid(uid) or not settings_file:
        return None
    try:
        with open(settings_file, "r", encoding="utf-8") as f:
            s = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(s, dict) or s.get("testUid") == uid:
        return None
    profiles = s.get("profiles")
    if not isinstance(profiles, list):
        return None
    for p in profiles:
        if not isinstance(p, dict) or p.get("uid") != uid:
            continue
        if not s.get("testUid") and re.match(r"^anyps5$", str(p.get("name", "")), re.I):
            return None
        fam = p.get("family")
        return fam if isinstance(fam, dict) else None
    return None


def minutes_left(uid, settings_file):
    f = _family(uid, settings_file)
    if f is None or f.get("limited") is not True:
        return UNLIMITED
    now = _now()
    inside, to_end = window_state(f.get("from"), f.get("to"), now.hour * 60 + now.minute)
    if not inside:
        return 0
    left = UNLIMITED
    daily = _minutes(f.get("dailyMin"))
    if daily > 0:
        left = max(0, daily - minutes_today(uid))
    if to_end is not None:
        left = min(left, to_end)
    return left


def game_allowed(uid, title_id, settings_file):
    f = _family(uid, settings_file)
    if f is None or f.get("allowAll") is not False:
        return True
    games = f.get("games")
    if not title_id or not isinstance(games, list):
        return False
    return any(isinstance(g, str) and g.upper() == str(title_id).upper() for g in games)


def when_playtime_ends(uid, settings_file):
    f = _family(uid, settings_file)
    return "close" if f is not None and f.get("whenUp") == "close" else "notify"


def title_id_from_folder(folder):
    if not isinstance(folder, str) or not folder:
        return ""
    m = TITLE_RX.search(os.path.basename(folder.rstrip("/\\")))
    return m.group(1) if m else ""


def handle(action, args):
    if action == "usage":
        with _lock:
            day = dict(_load().get(_today(), {}))
        return {"date": _today(), "today": day}
    raise NotImplementedError("unknown family action " + str(action))
