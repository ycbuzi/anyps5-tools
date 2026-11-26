"""The realm's economy (Blu 11-04): the AI team earns freedom in the kingdom. Crowns are the points the pipeline measured
(claude_bridge.reward), less the King's tithe of 1 in 10 that pays for public works. Crowns kept unlock freedoms in
the realm; ranks follow the real levels; probation or a bench keeps a member inside its own district. The King and
Queen post quests (the functions most games need next), call festivals and storms from what the pipeline really did,
and their decrees go on the notice board. Everything is written to repair-runs/world-state.json for the kingdom view.
Nothing here changes a member's real limits: those stay with claude_bridge.level."""
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import claude_bridge as cb  # noqa: E402

TOOLS = HERE.parent
GAMES = Path(r"D:\AnyPS5 Games")  # the game folders and _reports stay on D: (_tools is on C: behind a link)
WORLD = cb.RUNS / "world-state.json"
DECREES = cb.RUNS / "decrees.json"
PROGRESS = Path(r"C:\AnyPS5-work\progress\progress.json")
PREDICTED = GAMES / "_reports" / "predicted-missing.json"
TITHE = 0.1
WORKS_COST = 25

FREEDOMS = [
    (0, "a cot in its district's hall and the roads of its own district"),
    (5, "the whole valley: every district and the market"),
    (10, "its own house on a lot in its district"),
    (15, "the royal waystones: instant travel between the district squares and the castle"),
    (20, "a market stall where its tools and notes are on show"),
    (30, "a horse to ride every road of the realm"),
    (45, "the ridges and the four mountain passes"),
    (60, "an apprentice: the Squire helps with its chores"),
    (80, "a feast at the castle in its honour"),
    (100, "a seat at court to propose a law (a roles.md change Blu approves)"),
]
RANKS = {-1: "Under probation", 0: "Commoner", 1: "Knight (Sir or Lady)", 2: "Lord", 3: "Duke"}
HOMES = {"build": "Forge", "fix": "Workshop", "exp": "Alchemy Tower", "repair": "Gearworks", "coach": "Royal Library",
         "supervise": "Watchtower", "ask": "Herald's Post", "local": "Apprentice Cottage", "boss": "Castle", "pr": "Embassy"}
COURT = {"build": "Lord Mason", "fix": "Sir Patch", "exp": "Duchess Alchemy", "repair": "Baron Cog", "coach": "Lady Scroll",
         "supervise": "Captain Vigil", "ask": "Herald Pip", "local": "Squire Qwen", "boss": "Queen Fable 5.1", "pr": "Ambassador Merge"}
LIB_DISTRICT = {"libkernel": "Forge", "libSceAgcDriver": "Workshop", "libSceNgs2": "Alchemy Tower", "libSceVideoOut": "Gearworks",
                "libc": "Royal Library", "libSceSystemService": "Watchtower", "libSceHttp2": "Herald's Post", "libScePad": "Apprentice Cottage",
                "libSceAgc": "Agc Foundry"}
WEATHER_WORDS = [(r"Ngs2|audio|Reverb", "Alchemy Tower"), (r"AgcDriver|GPU|shader|Vulkan", "Workshop"), (r"\bAgc\b|Foundry", "Agc Foundry"),
                 (r"VideoOut|no frames?|black", "Gearworks"), (r"Http|network|NP\b", "Herald's Post"), (r"\bPad\b|controller", "Apprentice Cottage"),
                 (r"SystemService", "Watchtower"), (r"libc\b", "Royal Library")]


def ledger(kind):
    """Points earned in all, the tithe taken from them, and the crowns the member keeps."""
    points = sum(p for _, p, _ in cb.scoreboard().get(kind, {}).get("points", []))
    tithe = int(max(0, points) * TITHE)
    return points, tithe, points - tithe


def freedoms(crowns):
    have = [f for need, f in FREEDOMS if crowns >= need]
    nxt = next(((need, f) for need, f in FREEDOMS if crowns < need), None)
    return have, nxt


def member(kind):
    points, tithe, crowns = ledger(kind)
    have, nxt = freedoms(crowns)
    if kind == "local":
        lvl, standing = 0, "learning"
    else:
        lvl, standing = cb.level(kind)[0], cb.standing(kind)
    confined = lvl < 0 or standing in ("benched", "off")
    return {"name": COURT.get(kind, kind), "kind": kind, "role": cb.MEMBERS.get(kind, kind), "home": HOMES.get(kind, "Castle"),
            "crowns": crowns, "points": points, "tithe": tithe, "rank": RANKS[lvl], "standing": standing,
            "freedoms": have[:1] if confined else have, "next": {"crowns": nxt[0], "freedom": nxt[1]} if nxt else None,
            "confined": confined, "why_confined": ("probation" if lvl < 0 else standing) if confined else ""}


def quests(count=6):
    """The King's notice board: the missing functions most games need, each posted in its library's district."""
    try:
        funcs = json.loads(PREDICTED.read_text(encoding="utf-8-sig")).get("functions", [])
    except (OSError, ValueError):
        funcs = []
    board = []
    for f in sorted(funcs, key=lambda f: (-len(f.get("stopsNow", [])), -len(f.get("games", []))))[:count]:
        lib = f.get("library", "")
        board.append({"function": f.get("function", ""), "library": lib, "district": district_of(lib), "games": len(f.get("games", [])),
                      "stops_now": f.get("stopsNow", []), "class": f.get("class", "")})
    if board:
        return board
    try:
        groups = json.loads(PROGRESS.read_text(encoding="utf-8"))["libraries"]["groups"]
    except (OSError, ValueError, KeyError):
        return []
    for g in sorted(groups, key=lambda g: g["done"] / max(1, g["done"] + g["todo"]))[:count]:
        if g.get("todo_names"):
            board.append({"function": g["todo_names"][0], "library": g["name"], "district": district_of(g["name"]), "games": 0, "stops_now": [], "class": "missing"})
    return board


def district_of(lib):
    base = re.sub(r"\.native$", "", lib or "")
    return LIB_DISTRICT.get(base, "the frontier")


def events(hours=24):
    """What the pipeline really did, as weather and happenings over the realm."""
    since = time.strftime("%m-%d %H:%M:%S", time.localtime(time.time() - hours * 3600))
    out = []
    try:
        lines = (TOOLS / "auto-games.log").read_text(encoding="utf-8", errors="replace").splitlines()[-4000:]
    except OSError:
        return out
    for line in lines:
        m = re.match(r"(\d\d-\d\d \d\d:\d\d:\d\d)  EVENT (.+)", line)
        if not m or m.group(1) < since:
            continue
        stamp, text = m.groups()
        where = next((d for pat, d in WEATHER_WORDS if re.search(pat, text, re.I)), "Forge")
        if re.search(r"\bruns\b|now runs|boots and runs", text) and not re.search(r"stops|no frames|crash", text, re.I):
            out.append({"at": stamp, "kind": "festival", "where": "Castle", "why": text[:160]})
        elif text.startswith("TRIAGED") and re.search(r"-> #\d+.*\bruns:", text):
            out.append({"at": stamp, "kind": "cure found", "where": where, "why": text[:160]})
        elif text.startswith("AI TEAM") and " -> " in text:
            out.append({"at": stamp, "kind": "conversation", "where": where, "why": text[:160]})
        elif text.startswith(("FIX TRY", "ASSISTANT JOB")):
            out.append({"at": stamp, "kind": "knight rides out", "where": where, "why": text[:160]})
        elif re.search(r"crash|no frames|stopped after|STOPS LATER|exited \(code", text, re.I):
            out.append({"at": stamp, "kind": "storm", "where": where, "why": text[:160]})
        elif text.startswith("EXTRACTED"):
            out.append({"at": stamp, "kind": "caravan", "where": "the gate", "why": text[:160]})
        elif text.startswith("AI TEAM") and "earned" in text:
            out.append({"at": stamp, "kind": "crowns paid", "where": "Castle", "why": text[:160]})
        elif "RAID" in text.upper():
            out.append({"at": stamp, "kind": "tournament", "where": "the lists", "why": text[:160]})
    return out[-40:]


def decrees():
    try:
        return json.loads(DECREES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def decree(text, by="King"):
    """A decree from the King (Blu's word, or Fable 5 for the gravest troubles) or the Queen (the boss): it goes on the
    notice board for every member and viewer. It only announces; real changes still go through Blu's settings."""
    if by not in ("King", "Queen"):
        return "Only the King or the Queen can decree."
    if not text.strip():
        return "A decree needs words."
    items = decrees()
    items.append({"at": time.strftime("%Y-%m-%d %H:%M"), "by": by, "text": text.strip()[:300]})
    DECREES.write_text(json.dumps(items[-50:], indent=1), encoding="utf-8")
    write_world()
    return f"Decreed by the {by}: {text.strip()[:300]}"


def world():
    members = [member(k) for k in cb.MEMBERS]
    treasury = sum(m["tithe"] for m in members)
    try:
        groups = json.loads(PROGRESS.read_text(encoding="utf-8"))["libraries"]["groups"]
        done, total = sum(g["done"] for g in groups), sum(g["done"] + g["todo"] for g in groups)
    except (OSError, ValueError, KeyError):
        done = total = 0
    return {"updated": time.strftime("%Y-%m-%d %H:%M:%S"), "members": members,
            "treasury": {"crowns": treasury, "public_works": treasury // WORKS_COST, "next_work_at": (treasury // WORKS_COST + 1) * WORKS_COST},
            "realm": {"functions_done": done, "functions_total": total}, "quests": quests(), "events": events(), "decrees": decrees()[-10:],
            "rules": {"tithe": TITHE, "freedoms": [{"crowns": n, "freedom": f} for n, f in FREEDOMS], "ranks": RANKS}}


def write_world():
    state = world()
    tmp = WORLD.with_suffix(".tmp")
    cb.RUNS.mkdir(exist_ok=True)
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    tmp.replace(WORLD)
    return state


def district_status(district=""):
    """Squire Qwen's ask (11-04): each district's weather and building, so a member can go where help is needed. Storms
    are the last day's crashes and stops over it, and the games its missing functions stop now; building is the merges
    on AnyPS5 main in the last day that touched its libraries. With a district, only that one."""
    import subprocess
    libs = {}
    for lib, d in LIB_DISTRICT.items():
        libs.setdefault(d, []).append(lib)
    rows = {d: {"storms": [], "stopping": set(), "quests": [], "merges": 0} for d in sorted(set(LIB_DISTRICT.values()) | {"the frontier"})}
    for e in events():
        named = next((d for pat, d in WEATHER_WORDS if re.search(pat, e["why"], re.I)), None)
        if e["kind"] == "storm" and named in rows:
            rows[named]["storms"].append(e["why"])
    for q in quests(40):
        r = rows.setdefault(q["district"], {"storms": [], "stopping": set(), "quests": [], "merges": 0})
        r["quests"].append(q["function"])
        r["stopping"].update(q["stops_now"])
    for d, members in libs.items():
        paths = [f"core/libs/prx/{lib}" for lib in members] + [f"core/libs/prx/{lib}.native" for lib in members]
        log = subprocess.run(["git", "-C", r"D:\AnyPS5", "log", "origin/main", "--first-parent", "--since=24 hours ago", "--format=%h", "--"] + paths,
                             capture_output=True, text=True, errors="replace", creationflags=0x08000000).stdout
        rows[d]["merges"] = len(log.split())
    import talk
    talk_threads = talk.load()
    out = []
    for d, r in sorted(rows.items(), key=lambda kv: (-len(kv[1]["stopping"]), -len(kv[1]["storms"]), kv[0])):
        if district and district.lower() not in d.lower():
            continue
        severity = "a great storm" if len(r["stopping"]) >= 3 or len(r["storms"]) >= 3 else "a storm" if r["stopping"] or r["storms"] else "clear skies"
        line = (f"{d} ({', '.join(libs.get(d, ['libraries no district names']))}): {severity}; {len(r['storms'])} crash or stop(s) in the last day, "
                f"its missing functions stop {len(r['stopping'])} game(s) now; {r['merges']} merge(s) on main in the last day")
        if r["quests"]:
            line += "; quests: " + ", ".join(r["quests"][:4]) + (" ..." if len(r["quests"]) > 4 else "")
        if r["storms"]:
            line += "; latest: " + r["storms"][-1][:140]
        talks = [t for t in talk_threads if district_of(t.get("about", "")) == d and t["status"] not in ("used", "closed")]
        if talks:
            line += f"; {len(talks)} open conversation(s): " + ", ".join(f"{t['id']} {t['status']}" for t in talks[:4])
        out.append(line)
    return "\n".join(out) or f"No district matches {district}."


def report():
    """The kingdom in plain words: each member's crowns, rank and freedoms, the treasury, quests and today's happenings."""
    s = write_world()
    rows = [f"Realm: {s['realm']['functions_done']} of {s['realm']['functions_total']} functions built. Treasury: {s['treasury']['crowns']} crowns, "
            f"{s['treasury']['public_works']} public works (next at {s['treasury']['next_work_at']})."]
    for m in s["members"]:
        nxt = f"; next: {m['next']['freedom']} at {m['next']['crowns']}" if m["next"] else ""
        held = f" CONFINED to the {m['home']} ({m['why_confined']})" if m["confined"] else ""
        rows.append(f"{m['name']} ({m['role']}): {m['rank']}, {m['crowns']} crowns ({m['points']} earned, {m['tithe']} tithe), "
                    f"{len(m['freedoms'])} freedom(s){held}{nxt}")
    rows.append("Quests on the notice board: " + ("; ".join(f"{q['function']} ({q['library']}, {q['district']}, {q['games']} games"
                                                          + (f", stops {len(q['stops_now'])} now" if q['stops_now'] else "") + ")" for q in s["quests"]) or "none"))
    kinds = {}
    for e in s["events"]:
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    rows.append("Last 24 hours: " + (", ".join(f"{n} {k}" for k, n in kinds.items()) or "quiet"))
    if s["decrees"]:
        rows.append("Decrees: " + " | ".join(f"{d['by']} ({d['at']}): {d['text']}" for d in s["decrees"][-3:]))
    return "\n".join(rows)


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] in ("King", "Queen"):
        print(decree(" ".join(sys.argv[2:]), sys.argv[1]))
    else:
        print(report())
