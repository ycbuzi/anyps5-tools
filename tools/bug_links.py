"""For each game whose last log names a problem: where on GitHub the fix or the work on it may be. Prints JSON for the app and
keeps bug-links.json, so it also works offline. Only logs that changed are read, and each problem is searched once in 6 hours."""
import json
import re
import subprocess
import time
import urllib.parse
from pathlib import Path

T = Path(__file__).resolve().parent
GAMES = Path(r"D:\AnyPS5 Games")  # the game folders stay on D: (11-03 _tools moved to C: behind a link)
REPO = "boykopovar/AnyPS5"
CACHE = T / "bug-links.json"
SEARCH_AGE = 6 * 3600


def problems_in(log):
    """What to search for: missing functions by name, GPU gaps by their sentence."""
    text = log.read_text(encoding="utf-8", errors="replace")
    found = []
    for m in re.finditer(r"\b((?:sce|_?[A-Z])\w{3,}) not implemented", text):
        if ("missing function", m.group(1)) not in found:
            found.append(("missing function", m.group(1)))
    for m in re.finditer(r"shader 0x[0-9a-f]+: ([^\r\n]+?(?:not implemented|unsupported)[^\r\n]*)", text):
        if ("GPU gap", m.group(1).strip()) not in found:
            found.append(("GPU gap", m.group(1).strip()))
    return found[:4]


def words(text):
    """Comparable words: sceHttp2SetRedirectCallback -> http2, redirect, callback; plurals count as the same word."""
    parts = re.findall(r"[A-Z]?[a-z0-9-]+|[A-Z]+(?![a-z])", re.sub(r"^sce", "", text))
    return {w.lower().rstrip("s") for w in parts if len(w) > 2 and w.lower() not in ("set", "get", "the", "with", "and", "for", "from", "which", "not", "implemented", "reads", "writes", "read", "write")}


def search(kind, query):
    """Open PRs first, then merged ones (a newer AnyPS5 already has it), then open issues."""
    q = f'"{query}"' if " " in query else query
    rows = []
    for what, state, label in (("prs", "open", "open PR"), ("prs", "merged", "merged PR: a newer AnyPS5 has it"), ("issues", "open", "open issue")):
        args = ["gh", "search", what, "--repo", REPO, q, "--limit", "3", "--json", "number,title,url"]
        args += ["--merged"] if state == "merged" else ["--state", state]
        r = subprocess.run(args, capture_output=True, text=True, errors="replace", timeout=60, creationflags=0x08000000)
        if r.returncode:
            raise OSError(r.stderr[:200])
        wanted = words(query)
        rows += [{"state": label, "number": x["number"], "title": x["title"], "url": x["url"]} for x in json.loads(r.stdout or "[]")
                 if query.lower() in x["title"].lower() or len(wanted & words(x["title"])) >= 2]
        time.sleep(2)
    return rows


def main():
    try:
        cache = json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    logs, leads = cache.setdefault("logs", {}), cache.setdefault("leads", {})
    offline = False
    for d in sorted(p for p in GAMES.iterdir() if p.is_dir() and not p.name.startswith("_") and p.name != "Drop"):
        log = d / "anyps5.log"
        if not log.exists():
            continue
        stamp = log.stat().st_mtime
        if logs.get(d.name, {}).get("stamp") != stamp:
            logs[d.name] = {"stamp": stamp, "problems": problems_in(log)}
        for kind, query in logs[d.name]["problems"]:
            known = leads.get(query)
            if offline or (known and time.time() - known["searched"] < SEARCH_AGE):
                continue
            try:
                leads[query] = {"searched": time.time(), "rows": search(kind, query)}
            except (OSError, subprocess.SubprocessError):
                offline = True
    CACHE.write_text(json.dumps(cache), encoding="utf-8")
    rows = []
    for game, info in sorted(logs.items()):
        for kind, query in info["problems"]:
            found = leads.get(query, {}).get("rows", [])
            for lead in found[:4]:
                rows.append({"game": game, "kind": kind, "problem": query, "state": lead["state"], "lead": f"#{lead['number']} {lead['title']}", "url": lead["url"]})
            if not found:
                rows.append({"game": game, "kind": kind, "problem": query, "state": "nothing found yet: new work" if query in leads else "not searched yet (offline)", "lead": "Search GitHub for it",
                             "url": f"https://github.com/{REPO}/issues?q=" + urllib.parse.quote(query)})
    print(json.dumps({"rows": rows, "offline": offline}))


if __name__ == "__main__":
    main()
