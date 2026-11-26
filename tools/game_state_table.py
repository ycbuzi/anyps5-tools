import json, re, sys
from pathlib import Path

G = Path(r"D:\AnyPS5 Games")
state = json.loads((G / "_tools" / "auto-state.json").read_text(encoding="utf-8-sig"))
by_dir = {str(v.get("gameDir", "")).lower(): (k, v) for k, v in state.items() if v.get("gameDir")}
matrix = {}
for l in (G / "_reports" / "gpu-matrix.md").read_text(encoding="utf-8").splitlines():
    if l.startswith("| ") and "---" not in l and not l.startswith("| Game"):
        c = [x.strip() for x in l.strip("|").split("|")]
        matrix[re.sub(r"[^a-z0-9]", "", c[0].lower())] = c[1:]

def mrow(title, name):
    for key in (title, name):
        k = re.sub(r"[^a-z0-9]", "", key.lower())
        for m, v in matrix.items():
            if m and (m == k or m in k or k in m):
                return v
    return []

rows = []
for d in sorted(x for x in G.iterdir() if x.is_dir() and not x.name.startswith("_")):
    gj = d / "game.json"
    title = d.name
    if gj.exists():
        try:
            title = json.loads(gj.read_text(encoding="utf-8-sig")).get("title", d.name)
        except Exception:
            pass
    build = built = ""
    info = d / "info.txt"
    if info.exists():
        for l in info.read_text(encoding="utf-8", errors="replace").splitlines():
            if l.startswith("AnyPS5:"):
                build = l.split(":", 1)[1].strip()[:22]
            if l.startswith("Built:"):
                built = l.split(":", 1)[1].strip()
    exe = [e.name for e in d.glob("*.exe")]
    k, e = by_dir.get(str(d).lower(), ("", {}))
    rep = G / "_reports" / f"{d.name}.md"
    stop = ""
    if rep.exists():
        for l in rep.read_text(encoding="utf-8", errors="replace").splitlines():
            if l.startswith("Stopped by:"):
                stop = l.split(":", 1)[1].strip()[:110]
    m = mrow(title, d.name)
    rows.append({"dir": d.name, "title": title, "exe": bool(exe), "build": build, "built": built,
                 "status": e.get("status", ""), "note": str(e.get("note", ""))[:110], "recheck": str(e.get("recheck", ""))[:140],
                 "played": str(e.get("played", ""))[:90], "gpu4070": m[0] if m else "", "gpu1650": m[1] if len(m) > 1 else "",
                 "igpu": m[2][:70] if len(m) > 2 else "", "stop": stop})

out = Path(sys.argv[1]) if len(sys.argv) > 1 else G / "_reports" / "game-state.md"
lines = ["# Game state (from the pipeline's records)", "", "Built by _tools\\game_state_table.py. 9fb2d9bb83 results that exit 0xC0000135 are the pipeline build failing to load a library, not the game.", "",
         "| Game | Build | Latest result (RTX 4070 SUPER) | Stop |", "|---|---|---|---|"]
for r in rows:
    res = r["gpu4070"] or r["note"]
    lines.append(f"| {r['dir']} | {r['build'][:10]} | {res[:60]} | {r['stop'][:80]} |")
out.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"wrote {out} ({len(rows)} games)")
