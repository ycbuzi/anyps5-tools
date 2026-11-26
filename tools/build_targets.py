"""What the team's Builder implements next, most useful first, as JSON for auto-games.ps1 (Build-Next).

python -I build_targets.py   prints [{"function", "games": [game folders], "why"}]
Functions that already stopped a game come first (by how many games they stop), then the ones most games import that
AnyPS5 lacks or only stubs (from _reports\\predicted-missing.md). Functions tried in the last 7 days are left out.
"""
import collections
import json
import re
import sys
import time
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
GAMES = Path(r"D:\AnyPS5 Games")  # the game folders stay on D: (11-03 _tools moved to C: behind a link)
sys.path.insert(0, str(TOOLS / "assistant"))
import debug_skills  # noqa: E402

TRIES = TOOLS / "builder-tries.json"


def recent():
    try:
        tries = json.loads(TRIES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    week = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 7 * 86400))
    return {f.strip() for t in tries if t.get("at", "") >= week for f in str(t.get("function", "")).split(",") if f.strip()}


def stopped():
    wanted = collections.defaultdict(set)
    rx = re.compile(r"\b(sce\w+|_?\w+_nid_\w+|\w+)\s+not implemented|not implemented[:\s]+(\w+)", re.I)
    for d in (p for p in GAMES.iterdir() if p.is_dir() and not p.name.startswith("_")):
        for f in debug_skills.log_files(d):
            with open(f, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    m = rx.search(line)
                    if m:
                        name = m.group(1) or m.group(2)
                        if name.lower().startswith("sce") or "_nid_" in name:
                            wanted[name].add(str(d))
    return sorted(wanted.items(), key=lambda kv: -len(kv[1]))


def predicted():
    table = GAMES / "_reports" / "predicted-missing.md"
    rows = []
    if table.exists():
        for line in table.read_text(encoding="utf-8", errors="replace").splitlines():
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) >= 5 and cells[0].isdigit() and cells[4].isdigit():
                rows.append((cells[1], cells[2], cells[3], int(cells[4])))
    return rows


if __name__ == "__main__":
    skip = recent()
    out = [{"function": f, "games": sorted(g), "why": f"stops {len(g)} game(s)"} for f, g in stopped() if f not in skip]
    seen = {o["function"] for o in out}
    out += [{"function": f, "games": [], "why": f"{status} in AnyPS5, imported by {n} game(s)"} for f, lib, status, n in predicted()
            if f not in skip and f not in seen]
    print(json.dumps(out[:10]))
