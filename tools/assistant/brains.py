"""Personality brains (Blu 11-04): a small neural network of its own for every AI team member and every villager, for
their free time only (never for the coding work). Written from scratch in numpy.

Each brain sees its situation (time of day, its needs, the weather over its district, whether it is on a real job,
its crowns, its last reward) and picks one of ACTIONS. The deciding layer starts at exactly zero, so every brain begins
with no preferences at all; the hidden layer starts with tiny random numbers (an all-zero network can never learn:
every hidden unit would get the same update forever). It learns by reinforcement (REINFORCE with a running baseline):
the reward is its own needs being met, weighted by what it says it cares about, plus, for the AI team, its real
measured points and the boss's verdicts. "Nudges" (from the character sheets and from what each member said about
itself when Blu asked) shape what it cares about; they never touch the deciding weights directly.

Weights live in C:\\AnyPS5-work\\kingdom\\brains (members: <kind>.json; villagers: villagers.json)."""
import json
import math
import os
import random
from pathlib import Path

import numpy as np

DIR = Path(r"C:\AnyPS5-work\kingdom\brains")
ACTIONS = ["rest", "company", "craft", "learn", "explore", "shelter", "celebrate", "help"]
NEEDS = ["rest", "company", "craft", "learning", "exploring", "safety", "celebrating", "helping"]
FEATURES = ["day_sin", "day_cos", "energy", "social", "purpose", "curiosity", "safety", "storm_here", "festival", "on_job",
            "crowns", "last_reward", "teammate_in_storm", "night"]
HIDDEN = 16
LR = 0.05
EFFECTS = {
    "rest": {"energy": 0.35, "social": -0.03, "purpose": -0.02},
    "company": {"social": 0.35, "energy": -0.05, "celebration": 0.05},
    "craft": {"purpose": 0.35, "energy": -0.12},
    "learn": {"curiosity": 0.3, "purpose": 0.08, "energy": -0.06},
    "explore": {"curiosity": 0.25, "adventure": 0.35, "energy": -0.15},
    "shelter": {"safety": 0.4, "social": -0.02},
    "celebrate": {"celebration": 0.4, "social": 0.15, "energy": -0.08},
    "help": {"helped": 0.4, "purpose": 0.12, "energy": -0.1},
}
CARE_OF = {"energy": "rest", "social": "company", "purpose": "craft", "curiosity": "learning", "adventure": "exploring",
           "safety": "safety", "celebration": "celebrating", "helped": "helping"}


class Brain:
    def __init__(self, name, cares=None, seed=None):
        rng = np.random.default_rng(seed if seed is not None else abs(hash(name)) % (2 ** 32))
        self.name = name
        self.W1 = rng.normal(0, 0.1, (HIDDEN, len(FEATURES)))
        self.b1 = np.zeros(HIDDEN)
        self.W2 = np.zeros((len(ACTIONS), HIDDEN))
        self.b2 = np.zeros(len(ACTIONS))
        self.cares = {n: 1.0 for n in NEEDS} | (cares or {})
        self.baseline = 0.0
        self.steps = 0
        self.counts = {a: 0 for a in ACTIONS}
        self.nudges = []
        self.needs = {"energy": 0.7, "social": 0.5, "purpose": 0.5, "curiosity": 0.5, "adventure": 0.4, "safety": 0.8,
                      "celebration": 0.4, "helped": 0.4}
        self.last = None

    def forward(self, x):
        h = np.tanh(self.W1 @ x + self.b1)
        z = self.W2 @ h + self.b2
        p = np.exp(z - z.max())
        return h, p / p.sum()

    def act(self, x, rnd):
        _, p = self.forward(x)
        a = rnd.choices(range(len(ACTIONS)), weights=p.tolist())[0]
        self.last = (x.tolist(), a)
        self.counts[ACTIONS[a]] += 1
        return ACTIONS[a], p

    def learn(self, reward):
        if not self.last:
            return
        x, a = np.array(self.last[0]), self.last[1]
        h, p = self.forward(x)
        adv = reward - self.baseline
        self.baseline += 0.05 * (reward - self.baseline)
        g = -p
        g[a] += 1.0
        g *= adv
        gh = (self.W2.T @ g) * (1 - h * h)
        self.W2 += LR * np.outer(g, h)
        self.b2 += LR * g
        self.W1 += LR * np.outer(gh, x)
        self.b1 += LR * gh
        self.steps += 1

    def live(self, action, dt_hours, night, storm_here, festival):
        """Needs drift over time; the chosen action meets some of them. Returns the reward: how much better the needs
        it cares about got."""
        before = {k: v for k, v in self.needs.items()}
        decay = {"energy": 0.05 if not night else 0.09, "social": 0.04, "purpose": 0.03, "curiosity": 0.03, "adventure": 0.02,
                 "safety": 0.0, "celebration": 0.03, "helped": 0.02}
        for k, d in decay.items():
            self.needs[k] = max(0.0, self.needs[k] - d * dt_hours)
        if storm_here:
            self.needs["safety"] = max(0.0, self.needs["safety"] - 0.3 * dt_hours)
        else:
            self.needs["safety"] = min(1.0, self.needs["safety"] + 0.1 * dt_hours)
        gain = dict(EFFECTS.get(action, {}))
        if action == "celebrate" and not festival:
            gain["celebration"] = gain.get("celebration", 0) * 0.3
        if action == "rest" and night:
            gain["energy"] = gain.get("energy", 0) * 1.5
        for k, v in gain.items():
            self.needs[k] = min(1.0, max(0.0, self.needs[k] + v * min(1.0, dt_hours * 2)))
        reward = 0.0
        for k in self.needs:
            w = self.cares.get(CARE_OF[k], 1.0)
            reward += w * (self.needs[k] - before[k]) + 0.02 * w * (self.needs[k] - 0.5)
        return reward

    def nudge(self, cares, why):
        """What the member cares about (0-2 per need), from its sheet or its own words. Shapes rewards, not weights."""
        for k, v in cares.items():
            if k in self.cares:
                self.cares[k] = round(0.5 * self.cares[k] + 0.5 * float(v), 3)
        self.nudges.append({"why": why[:200], "cares": {k: float(v) for k, v in cares.items()}})

    def traits(self):
        total = max(1, sum(self.counts.values()))
        return {a: round(c / total, 3) for a, c in self.counts.items()}

    def describe(self):
        if self.steps < 5:
            return "still finding out who it is"
        top = sorted(self.traits().items(), key=lambda kv: -kv[1])[:2]
        words = {"rest": "restful", "company": "sociable", "craft": "devoted to its craft", "learn": "studious", "explore": "adventurous",
                 "shelter": "cautious", "celebrate": "festive", "help": "helpful"}
        return " and ".join(words[a] for a, _ in top)

    def to_json(self):
        return {"name": self.name, "W1": self.W1.round(5).tolist(), "b1": self.b1.round(5).tolist(), "W2": self.W2.round(5).tolist(),
                "b2": self.b2.round(5).tolist(), "cares": self.cares, "baseline": round(self.baseline, 5), "steps": self.steps,
                "counts": self.counts, "nudges": self.nudges[-12:], "needs": {k: round(v, 3) for k, v in self.needs.items()}, "last": self.last}

    @classmethod
    def from_json(cls, d):
        b = cls(d["name"])
        b.W1, b.b1, b.W2, b.b2 = (np.array(d[k]) for k in ("W1", "b1", "W2", "b2"))
        b.cares, b.baseline, b.steps = d["cares"], d["baseline"], d["steps"]
        b.counts, b.nudges, b.needs, b.last = d["counts"], d.get("nudges", []), d["needs"], d.get("last")
        return b


def childhood(brain, days=5, seed=0):
    """A few simulated days of growing up: hourly choices against its own needs only (no real events), so habits form
    (sleeping at night, seeking company when lonely) in the way its own cares make it. Kept as a nudge in its history."""
    rnd = random.Random(seed or brain.name)
    for step in range(days * 24):
        hour = step % 24
        night = hour >= 21 or hour < 6
        storm = rnd.random() < 0.08
        fest = rnd.random() < 0.05
        x = features(hour, brain, storm, fest, False, 0, rnd.random() < 0.1)
        action, _ = brain.act(x, rnd)
        brain.learn(brain.live(action, 1.0, night, storm, fest))
    brain.nudges.append({"why": f"{days} simulated days growing up on its own needs", "cares": dict(brain.cares)})
    return brain


def features(hour, brain, storm_here, festival, on_job, crowns, teammate_in_storm):
    n = brain.needs
    return np.array([math.sin(hour / 24 * 2 * math.pi), math.cos(hour / 24 * 2 * math.pi), n["energy"], n["social"], n["purpose"],
                     n["curiosity"], n["safety"], float(storm_here), float(festival), float(on_job), min(1.0, crowns / 100.0),
                     max(-1.0, min(1.0, brain.baseline)), float(teammate_in_storm), float(hour >= 21 or hour < 6)])



def write_json(path, data):
    """Whole-file write: a temp file then a rename, so two writers at once never leave a half-overwritten file (11-08
    build.json and supervise.json ended with a stray brace and stopped the realm and the watchdog for a day)."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def load(name):
    path = DIR / f"{name}.json"
    if path.exists():
        return Brain.from_json(json.loads(path.read_text(encoding="utf-8")))
    return Brain(name)


def save(brain):
    DIR.mkdir(parents=True, exist_ok=True)
    write_json(DIR / f"{brain.name}.json", brain.to_json())


def load_villagers():
    path = DIR / "villagers.json"
    if path.exists():
        return {k: Brain.from_json(v) for k, v in json.loads(path.read_text(encoding="utf-8")).items()}
    return {}


def save_villagers(brains):
    DIR.mkdir(parents=True, exist_ok=True)
    write_json(DIR / "villagers.json", {k: b.to_json() for k, b in brains.items()})


def villager_temperament(vid):
    """Every villager is born different: a random temperament (what it cares about), never the same twice."""
    r = random.Random(vid)
    return {n: round(r.uniform(0.4, 1.6), 2) for n in NEEDS}


SHEET_NUDGES = {
    "build": ({"craft": 1.8, "company": 0.6, "rest": 0.8, "learning": 0.9, "helping": 1.1}, "sheet: gruff, proud of square corners; tests every wall he raises"),
    "fix": ({"helping": 1.7, "safety": 0.7, "craft": 1.3, "exploring": 1.1}, "sheet: brave, steady, a little stubborn"),
    "exp": ({"learning": 1.8, "exploring": 1.5, "celebrating": 1.2, "safety": 0.5}, "sheet: curious, bold, laughs at failed brews"),
    "repair": ({"craft": 1.6, "rest": 0.9, "company": 0.8, "safety": 1.2}, "sheet: precise, fussy, polite; hums while he works"),
    "coach": ({"learning": 1.7, "helping": 1.5, "company": 1.1, "exploring": 0.6}, "sheet: patient scholar; says 'not yet'"),
    "supervise": ({"safety": 1.8, "craft": 1.1, "company": 0.5, "celebrating": 0.4}, "sheet: alert, terse, cautious"),
    "ask": ({"company": 1.9, "exploring": 1.3, "celebrating": 1.4, "rest": 0.6}, "sheet: cheerful, fast-talking, knows every villager"),
    "pr": ({"craft": 1.4, "company": 1.2, "learning": 1.2, "rest": 1.0}, "sheet: courteous, exact, unhurried"),
    "local": ({"learning": 1.8, "helping": 1.4, "rest": 0.9, "craft": 1.1}, "sheet: humble, earnest; asks good questions"),
    "boss": ({"craft": 1.5, "safety": 1.2, "company": 1.0, "celebrating": 0.7}, "sheet: sharp, fair, notices everything"),
    "king": ({"rest": 1.3, "safety": 1.4, "company": 0.9, "learning": 1.0}, "sheet: old, calm, slow to speak"),
}


def ensure(kind):
    """A member's brain; a new one starts at zero and gets its character-sheet nudge."""
    b = load(kind)
    if not b.nudges and kind in SHEET_NUDGES:
        b.nudge(*SHEET_NUDGES[kind])
        save(b)
    return b
