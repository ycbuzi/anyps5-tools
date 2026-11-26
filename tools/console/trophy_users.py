"""Per-user trophies (Blu 11-15). A relinked game always plays as one user (0x10000000) and writes its trophy record to
<game>\\_trophy\\<user>_<label>.txt. AnyPS5 keeps one record per AnyPS5 user and puts the right one in place:
  in  <gameDir> <uid> [legacyUid]  before AnyPS5 starts the game for that user
  out <gameDir>                    after the game closed: the record goes back to its user
  fix                              finishes an "in" whose "out" never ran (AnyPS5 closed or crashed), for games not running
Records: <game>\\_trophy\\users\\<uid>\\*.txt per AnyPS5 user, users\\_tests\\ for the pipeline's own runs while a user
plays. users\\live-owner.txt names whose record is in place. The first time, the trophies already earned are copied to
legacyUid (the console's first user) and stay with the tests too.
"""
import os
import shutil
import subprocess
import sys
import time

GAMES = r"D:\AnyPS5 Games"


def records(folder):
    if not os.path.isdir(folder):
        return []
    return [n for n in os.listdir(folder) if n.endswith(".txt") and "_" in n and n.split("_", 1)[0].isdigit()]


def retry(fn):
    for i in range(10):
        try:
            return fn()
        except OSError:
            if i == 9:
                raise
            time.sleep(0.3)


def move_records(src, dst):
    os.makedirs(dst, exist_ok=True)
    for n in records(dst):
        retry(lambda: os.remove(os.path.join(dst, n)))
    for n in records(src):
        retry(lambda: shutil.move(os.path.join(src, n), os.path.join(dst, n)))


def copy_records(src, dst):
    os.makedirs(dst, exist_ok=True)
    for n in records(dst):
        retry(lambda: os.remove(os.path.join(dst, n)))
    for n in records(src):
        retry(lambda: shutil.copy2(os.path.join(src, n), os.path.join(dst, n)))


def safe_uid(uid):
    uid = "".join(c for c in uid if c.isalnum() or c in "-_")[:40]
    if not uid or uid.startswith("_"):
        raise ValueError("bad user id")
    return uid


def do_out(game_dir):
    live = os.path.join(game_dir, "_trophy")
    users = os.path.join(live, "users")
    marker = os.path.join(users, "live-owner.txt")
    if not os.path.exists(marker):
        return "nothing in place"
    owner = safe_uid(open(marker, encoding="utf-8").read().strip())
    copy_records(live, os.path.join(users, owner))
    move_records(os.path.join(users, "_tests"), live)
    os.remove(marker)
    return "saved for " + owner


def do_in(game_dir, uid, legacy):
    uid = safe_uid(uid)
    live = os.path.join(game_dir, "_trophy")
    users = os.path.join(live, "users")
    if os.path.exists(os.path.join(users, "live-owner.txt")):
        do_out(game_dir)
    os.makedirs(live, exist_ok=True)
    if not os.path.exists(os.path.join(users, "migrated.txt")):
        os.makedirs(users, exist_ok=True)
        if legacy:
            legacy = safe_uid(legacy)
            if not records(os.path.join(users, legacy)):
                copy_records(live, os.path.join(users, legacy))
        open(os.path.join(users, "migrated.txt"), "w", encoding="utf-8").write("trophies earned before %s copied to %s\n" % (time.strftime("%Y-%m-%d %H:%M"), legacy or "nobody"))
    move_records(live, os.path.join(users, "_tests"))
    mine = os.path.join(users, uid)
    for n in records(mine):
        shutil.copy2(os.path.join(mine, n), os.path.join(live, n))
    open(os.path.join(users, "live-owner.txt"), "w", encoding="utf-8").write(uid)
    return "in place for " + uid


def running_dirs():
    out = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Process | Where-Object { $_.Path } | ForEach-Object { $_.Path }"],
                         capture_output=True, text=True, creationflags=0x08000000).stdout
    return [os.path.dirname(p).lower() for p in out.splitlines() if p.strip()]


def do_fix():
    pending = [os.path.join(GAMES, n) for n in os.listdir(GAMES) if os.path.exists(os.path.join(GAMES, n, "_trophy", "users", "live-owner.txt"))]
    if not pending:
        return "nothing to fix"
    busy = running_dirs()
    done = []
    for d in pending:
        if any(b == d.lower() or b.startswith(d.lower() + "\\") for b in busy):
            continue
        done.append("%s: %s" % (os.path.basename(d), do_out(d)))
    return "; ".join(done) or "games still running"


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "in":
        print(do_in(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else ""))
    elif cmd == "out":
        print(do_out(sys.argv[2]))
    elif cmd == "fix":
        print(do_fix())
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
