"""Read-only: state of downloads saved into the game folders, from a temporary copy of Chrome's History. Prints no URLs and reads nothing else."""
import datetime
import glob
import os
import shutil
import sqlite3
import tempfile

STATE = {0: "in progress", 1: "complete", 2: "cancelled", 3: "bug", 4: "interrupted"}
REASON = {0: "", 1: "file failed", 2: "no access", 3: "no space", 5: "name too long", 6: "file too large", 7: "virus", 10: "transient file error", 11: "blocked", 12: "file security", 13: "file too short", 14: "hash mismatch", 15: "same as source",
          20: "network failed", 21: "network timeout", 22: "network disconnected", 23: "server down", 24: "invalid request",
          30: "server failed", 31: "no range support", 33: "server bad content", 34: "unauthorized", 35: "cert problem", 36: "forbidden", 37: "server unreachable", 38: "content length mismatch", 39: "cross origin redirect",
          40: "cancelled by user", 41: "browser shutdown", 50: "crash"}


def when(us):
    return datetime.datetime.fromtimestamp(us / 1e6 - 11644473600).strftime("%m-%d %H:%M") if us else "-"


tmp = tempfile.mkdtemp()
for profile in glob.glob(os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data\*\History")):
    copy = os.path.join(tmp, "h")
    shutil.copy2(profile, copy)
    db = sqlite3.connect(copy)
    rows = db.execute("select target_path, current_path, state, interrupt_reason, received_bytes, total_bytes, start_time, end_time, last_access_time, opened, etag, last_modified from downloads order by start_time desc limit 400").fetchall()
    for target, current, state, reason, got, total, start, end, access, opened, etag, modified in rows:
        path = target or current or ""
        if "ps5 " not in path.lower():
            continue
        print(when(start), "|", STATE.get(state, state), "|", REASON.get(reason, reason), "|", f"{got / 1e9:.2f}/{total / 1e9:.2f} GB", "| ended", when(end), "| validator:", "yes" if etag or modified else "no", "|", os.path.basename(path or current)[:70], "|", path[:3])
    db.close()
    os.remove(copy)
shutil.rmtree(tmp)
