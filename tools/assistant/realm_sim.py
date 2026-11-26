"""The living realm (Blu 11-04): where every AI team member is, what it is doing and why, how it feels, and how it gets
around. One tick reads what the team really does (team-live.json, the scoreboard, the pipeline's EVENT lines) and
moves the members through the realm at real pace: world time is real time, walking is 1.4 m/s, a horse 4.5 m/s, and
the royal waystones (an earned freedom) are instant. The Queen summons a member to court the moment she reviews its
work. Team notes become letters Herald Pip carries on foot; urgent news goes by raven. State goes to
repair-runs/realm-sim.json for the kingdom view and the town chat. Nothing here starts or changes real work."""
import json
import math
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import brains  # noqa: E402
import claude_bridge as cb  # noqa: E402
import crowns  # noqa: E402
import random  # noqa: E402

RND = random.Random()

STATE = cb.RUNS / "realm-sim.json"
REGION = Path(r"C:\AnyPS5-work\kingdom\godot\data\region.json")
UNIT_M = 1.5
WALK, HORSE = 1.4, 4.5
# Blu 11-04: "will it take forever for them to even talk to each other?" The whole team asked for a fast realm clock:
# walks are seconds of animation (crossing the realm about a minute) and never delay a message or a job.
TIME_SCALE = 20
TALK_MINUTES = 15
WAYSTONE_CROWNS, HORSE_CROWNS = 15, 30
NIGHT = (22, 7)
CASTLE = "Castle"
EMBASSY = "Embassy"


def places():
    """Realm-unit coordinates of every district square, the castle and the embassy at the west pass."""
    out = {CASTLE: (512.0, 512.0), EMBASSY: (60.0, 512.0)}
    try:
        data = json.loads(REGION.read_text(encoding="utf-8"))
        for t in data.get("towns", []) + data.get("far_towns", []):
            out[t["name"]] = (float(t["x"]), float(t["y"]))
        if data.get("castle"):
            out[CASTLE] = tuple(map(float, data["castle"]))
    except (OSError, ValueError, KeyError):
        pass
    return out


HOME = dict(crowns.HOMES, pr=EMBASSY, boss=CASTLE)
KING = "king"


def load():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"members": {}, "letters": [], "said": []}


def where_for(task, home):
    """The district a job is about: the first library its task names, else the member's home."""
    for lib, district in crowns.LIB_DISTRICT.items():
        if re.search(re.escape(lib) + r"(\b|\.)", task or ""):
            return district
    return home


def mood_of(kind):
    s = cb.scoreboard().get(kind, {})
    last = (s.get("last") or "")
    week = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 7 * 86400))
    strikes = len([t for t in s.get("strikes", []) if t >= week])
    if strikes >= 2:
        return "downcast"
    if "PASS" in last[:20]:
        return "proud"
    if strikes == 1 or "REDO" in last[:20]:
        return "determined"
    return "calm"


def energy_of(kind):
    spec = cb.KINDS.get(kind)
    if not spec:
        return 1.0
    used = sum(1 for r in cb.entries(time.strftime("%Y-%m-%d")) if r.get("kind") == kind)
    return round(max(0.0, 1.0 - used / max(1, spec["per_day"])), 2)


def travel_mode(kind, summoned):
    crowns_kept = crowns.ledger(kind)[2]
    if summoned or crowns_kept >= WAYSTONE_CROWNS:
        return "waystone"
    if crowns_kept >= HORSE_CROWNS:
        return "horse"
    return "walk"


def position(m, now):
    if not m.get("eta") or now >= m["eta"]:
        return tuple(m["at"])
    f = (now - m["depart"]) / max(1.0, m["eta"] - m["depart"])
    a, b = m["from"], m["at"]
    return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)


def tick(now=None):
    now = now or time.time()
    st = load()
    spots = places()
    live = cb.live().get("members", {})
    hour = time.localtime(now).tm_hour
    night = hour >= NIGHT[0] or hour < NIGHT[1]
    reviewing = live.get("boss", {}).get("status") == "reviewing"
    under_review = next((k for k, v in live.items() if k not in ("boss",) and v.get("status") == "working"), "") if reviewing else ""
    kinds = [k for k in cb.MEMBERS] + [KING]
    talking = conversations(live, now)
    for kind in kinds:
        m = st["members"].setdefault(kind, {})
        home = HOME.get(kind, CASTLE) if kind != KING else CASTLE
        info = live.get(kind, {})
        standing = "ok" if kind in ("local", "boss", KING) else cb.standing(kind)
        task = info.get("task", "")
        working = info.get("status") in ("working", "reviewing", "planning")
        confined = kind != KING and (standing in ("off", "benched") or crowns.member(kind)["confined"])
        if confined:
            goal, doing, why = home, "confined", f"{standing}: stays in the {home}"
        elif kind == "boss" or kind == KING:
            goal, doing, why = CASTLE, ("judging" if working else "holding court"), task or "the court is quiet"
        elif working:
            goal, doing, why = where_for(task, home), "working", task
        elif kind == under_review:
            goal, doing, why = CASTLE, "summoned", "the Queen is reviewing its work"
        elif talking.get(kind):
            goal, doing, why = talking[kind]
        else:
            goal, doing, why = free_time(st, kind, home, spots, now, night)
        target = spots.get(goal, spots[CASTLE])
        if "at" not in m:
            m.update(at=list(spots.get(home, spots[CASTLE])), place=home, eta=0, depart=0, mode="walk")
        cur = position(m, now)
        if goal != m.get("place"):
            # The Queen (11-04): her summons also covers a member going to help a teammate.
            mode = travel_mode(kind, doing in ("summoned", "helping")) if kind not in ("boss", KING) else "waystone"
            dist_m = math.dist(cur, target) * UNIT_M * 1.25
            secs = 3.0 if mode == "waystone" else dist_m / (HORSE if mode == "horse" else WALK) / TIME_SCALE
            m.update(**{"from": list(cur), "at": list(target), "place": goal, "depart": now, "eta": now + secs, "mode": mode})
            say(st, kind, f"{'steps through the waystone to' if mode == 'waystone' else 'rides to' if mode == 'horse' else 'sets off on foot to'} "
                          f"the {goal} ({int(secs)} s){': ' + why[:90] if why else ''}")
        m.update(doing=doing, why=why[:200], mood=mood_of(kind) if kind not in (KING,) else "calm",
                 energy=energy_of(kind) if kind not in (KING,) else 1.0, x=position(m, now)[0], y=position(m, now)[1],
                 travelling=bool(m.get("eta") and now < m["eta"]), goals=goals_of(kind))
    deliver_letters(st, now, spots)
    learn_all(st, now)
    st["updated"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
    st["night"] = night
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1), encoding="utf-8")
    tmp.replace(STATE)
    return st


def conversations(live, now):
    """Members in a conversation from the last TALK_MINUTES (talk.py threads). Both free: they meet in the district the
    thread is about and show their own last words. Busy on a job: it answers by speaking stone where it stands (its job
    goes on; only the words show). Nothing is made up: the words are the thread's."""
    import talk
    out = {}
    for t in sorted(talk.load(), key=lambda t: t.get("updated", "")):
        if not t["messages"] or now - time.mktime(time.strptime(t["updated"], "%Y-%m-%dT%H:%M:%S")) > TALK_MINUTES * 60:
            continue
        last = t["messages"][-1]
        pair = [last["from"], last["to"]]
        place = where_for(t.get("about", "") + " " + last["text"], HOME.get(t["messages"][0]["from"], CASTLE))
        for kind in pair:
            other = pair[1] if kind == pair[0] else pair[0]
            if kind in (KING, "boss") or live.get(kind, {}).get("status") in ("working", "reviewing", "planning"):
                continue
            words = last["text"] if kind == last["from"] else f"listening to the {cb.MEMBERS.get(other, other)}: {last['text']}"
            out[kind] = (place, f"talking with the {cb.MEMBERS.get(other, other)} ({t['id']})", words)
    return out


def storms():
    return {e["where"] for e in crowns.events(6) if e["kind"] == "storm"}


def festival_now():
    return any(e["kind"] == "festival" for e in crowns.events(6))


def free_time(st, kind, home, spots, now, night):
    """The member's own brain picks what it does between jobs. It keeps a choice until it gets there and has spent at
    least 20 minutes on it."""
    m = st["members"][kind]
    if m.get("eta") and now < m["eta"] and m.get("choice_place"):
        return m["choice_place"], m.get("doing", "idle"), m.get("why", "")
    if m.get("chosen_at") and now - m["chosen_at"] < 1200 and m.get("choice_place"):
        return m["choice_place"], m.get("doing", "idle"), m.get("why", "")
    brain = brains.ensure(kind)
    hour = time.localtime(now).tm_hour + time.localtime(now).tm_min / 60
    stormy = storms()
    x = brains.features(hour, brain, home in stormy, festival_now(), False, crowns.ledger(kind)[2] if kind != KING else 0, bool(stormy - {home}))
    action, _ = brain.act(x, RND)
    away = [p for p in spots if p not in (home, CASTLE)]
    place, doing, why = {
        "rest": (home, "resting", "sleeping it off" if night else "taking a rest"),
        "company": (CASTLE, "keeping company", "looking for company at the castle"),
        "craft": (home, "practising its craft", "at its own workbench"),
        "learn": ("Royal Library", "studying", "reading in the Royal Library"),
        "explore": (RND.choice(away) if away else home, "exploring", "wandering out to see the realm"),
        "shelter": (home, "sheltering", "keeping out of the storm"),
        "celebrate": (CASTLE, "celebrating", "off to celebrate at the castle"),
        "help": (next(iter(stormy), home), "helping", "going to help where the storm is"),
    }[action]
    if place not in spots:
        place = home
    m.update(choice_place=place, chosen_at=now, action=action)
    brains.save(brain)
    return place, doing, why


def learn_all(st, now):
    """After each tick every brain learns from what its last choice did for it: needs met (weighted by what it cares
    about), and for members the real points they earned and the boss's verdicts since the last tick."""
    dt = min(1.0, max(0.02, (now - st.get("last_tick", now - 300)) / 3600))
    hour = time.localtime(now).tm_hour
    night = hour >= NIGHT[0] or hour < NIGHT[1]
    stormy, fest = storms(), festival_now()
    board = cb.scoreboard()
    for kind, m in st["members"].items():
        brain = brains.ensure(kind)
        reward = brain.live(m.get("action", "craft" if m.get("doing") == "working" else "rest"), dt, night, HOME.get(kind) in stormy, fest)
        s = board.get(kind, {})
        pts = sum(p[1] for p in s.get("points", []))
        last = s.get("last", "")
        # The Watcher and the Helper (11-04): the personality learns mostly from stated needs; points are a small input.
        if "points" in m and pts != m["points"]:
            reward += 0.01 * (pts - m["points"])
        if m.get("verdict_seen") != last and last:
            reward += 0.1 if " PASS" in last[:20] else (-0.15 if "STRIKE" in last[:20] else -0.05 if "REDO" in last[:20] else 0.0)
            m["verdict_seen"] = last
        m["points"] = pts
        brain.learn(reward)
        m["personality"] = brain.describe()
        m["cares"] = brain.cares
        brains.save(brain)
    vill = brains.load_villagers()
    intents = {}
    for t in towns():
        count = min(26, max(4, len(t.get("houses", [])) // 3 + 3))
        for i in range(count):
            vid = f"{t['name']}#{i}"
            b = vill.get(vid)
            if b is None:
                b = brains.Brain(vid, brains.villager_temperament(vid))
                b.nudges.append({"why": "born with its own temperament", "cares": dict(b.cares)})
            if b.last:
                b.learn(b.live(brains.ACTIONS[b.last[1]], dt, night, t["name"] in stormy, fest))
            x = brains.features(hour, b, t["name"] in stormy, fest, False, 0, False)
            action, _ = b.act(x, RND)
            vill[vid] = b
            intents[vid] = {"action": action, "personality": b.describe()}
    brains.save_villagers(vill)
    st["villagers"] = intents
    st["last_tick"] = now


def towns():
    try:
        return json.loads(REGION.read_text(encoding="utf-8")).get("towns", [])
    except (OSError, ValueError):
        return []


def say(st, kind, text):
    name = crowns.COURT.get(kind, "King Fable 5" if kind == KING else kind)
    st.setdefault("said", []).append({"at": time.strftime("%H:%M"), "who": name, "text": text[:220]})
    st["said"] = st["said"][-80:]


def goals_of(kind):
    path = cb.MEMORY / f"{kind}.md"
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.exists() else []
    return [l.split("Next:", 1)[1].strip()[:160] for l in lines if "Next:" in l][-3:]


def deliver_letters(st, now, spots):
    """New 'Notes for the team' become letters: Herald Pip walks each one from the writer's district to the castle
    notice board, where every member reads it; a note that warns of a crash goes by raven (instant)."""
    notes = cb.TEAM_NOTES.read_text(encoding="utf-8", errors="replace").splitlines() if cb.TEAM_NOTES.exists() else []
    seen = set(l.get("text") for l in st.get("letters", []))
    for line in notes[-10:]:
        m = re.match(r"- (\d\d-\d\d \d\d:\d\d) (\w[\w ]*?): (.+)", line)
        if not m or m.group(3)[:200] in seen:
            continue
        author, text = m.group(2), m.group(3)[:200]
        kind = cb.NAME_TO_KIND.get(author, "")
        frm = spots.get(HOME.get(kind, CASTLE), spots[CASTLE])
        urgent = bool(re.search(r"crash|stops|broke|urgent", text, re.I))
        secs = 0 if urgent else math.dist(frm, spots[CASTLE]) * UNIT_M * 1.25 / (WALK * 2.2)
        st.setdefault("letters", []).append({"from": author, "text": text, "by": "raven" if urgent else "Herald Pip",
                                             "sent": now, "arrives": now + secs, "start": list(frm), "to": list(spots[CASTLE])})
        say(st, "ask" if not urgent else "boss", f"{'receives by raven' if urgent else 'runs off with'} the {author}'s letter: {text[:120]}")
    st["letters"] = st.get("letters", [])[-30:]


def report():
    st = tick()
    rows = []
    for kind, m in st["members"].items():
        name = crowns.COURT.get(kind, "King Fable 5" if kind == KING else kind)
        move = f", {m['mode']} to the {m['place']}" if m.get("travelling") else f" at the {m.get('place')}"
        rows.append(f"{name}: {m.get('doing')}{move} ({m.get('mood')}, energy {int(100 * m.get('energy', 1))}%)"
                    + (f" - {m['why'][:100]}" if m.get("why") else "") + (f"; wants to: {m['goals'][-1]}" if m.get("goals") else ""))
    rows.append("Recently: " + " | ".join(f"{s['at']} {s['who']} {s['text'][:90]}" for s in st.get("said", [])[-4:]))
    return ("Night in the realm.\n" if st.get("night") else "") + "\n".join(rows)


if __name__ == "__main__":
    print(report())
