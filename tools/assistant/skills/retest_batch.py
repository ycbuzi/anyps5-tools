"""Clean test copies of several games on the newest C: build of AnyPS5, each tested the same way, with one report of what is true now.
Waits while a game is running. Stops if D: gets short. Blu's own game folders are only read."""
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import assistant as a

a.AUTO["on"] = True
a.say = lambda *x, **k: None
MINUTES = sys.argv[1]
ENV = "" if sys.argv[2] == "-" else sys.argv[2]
LIBRARY = "" if sys.argv[3] == "-" else sys.argv[3]
GAMES = sys.argv[4:]
MIN_FREE_GB = 100
OUT = a.GAMES / "_reports" / f"retest-{time.strftime('%m%d-%H%M')}.md"


def note(text):
    with OUT.open("a", encoding="utf-8") as f:
        f.write(text + "\n")
    print(text[:600], flush=True)


def job(fn, *args):
    n = len(a.JOBS) + 1
    r = str(fn(*args))
    if n not in a.JOBS:
        return r
    j = a.JOBS[n]
    j["proc"].wait()
    return j["log"].read_text(encoding="utf-8", errors="replace")


note(f"# Retest on the newest C: build, {time.strftime('%Y-%m-%d %H:%M')}. Settings: {ENV or 'none'}. Library: {LIBRARY or 'none'}\n")
for game in GAMES:
    while a.game_running():
        time.sleep(120)
    free = shutil.disk_usage("D:\\").free / 2**30
    if free < MIN_FREE_GB:
        note(f"STOPPED before {game}: only {free:.0f} GB free on D:.")
        break
    note(f"## {game}")
    try:
        copy = job(a.make_test_copy, game)
        if "build exit 0" not in copy:
            note("The copy did not build: " + copy[-400:].replace("\n", " "))
            continue
        result = job(a.test_run, game, MINUTES, "1", "every_button", "retest on newest main", LIBRARY, ENV)
        runs = [json.loads(l) for l in result.splitlines() if l.startswith('{"run"')]
        for r in runs:
            note(f"- {r.get('end')} First: {r.get('first')} Frames: {r.get('frames')}. {r.get('kind')} {r.get('said')}")
            note(f"- Last rows: {' / '.join(x.strip() for x in r.get('last_rows', [])[-2:])}")
        if not runs:
            note("- No result: " + result[-400:].replace("\n", " "))
        if any(r.get("shots") for r in runs):
            note("- " + a.frames_report(game).replace("\n", " | ")[:700])
        else:
            note("- This run saved no frames.")
    except Exception as e:
        note(f"- Tool failed: {e!r}")
note("\nDONE")
