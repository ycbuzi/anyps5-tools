"""Game bring-up reports for AnyPS5's GitHub discussions (Blu 11-04). The roadmap map was retired on 10-23 and points
there: one thread per title, written in the category's form (.github/DISCUSSION_TEMPLATE/game-bring-up.yml). Every
field comes from the pipeline's own records: title and content version from the game's sce_sys/param.json, the main
commit the pipeline's recheck relinked and ran with, this PC, the recheck's result and its end-of-test screenshot
(<game>\\test-shots). What the screenshot shows is written by whoever looked at it (Claude Code or the local vision
model); a report without it, without a screenshot from that commit, or on a commit that is not on main is refused.
Drafts and what was posted are kept in _reports\\bringup."""
import json
import re
import subprocess
from pathlib import Path

GAMES = Path(r"D:\AnyPS5 Games")
TOOLS = GAMES / "_tools"
OUT = GAMES / "_reports" / "bringup"
HEALTH = Path(r"D:\AnyPS5\wt-health")
REPO = ("boykopovar", "AnyPS5")
FURTHEST = ("Does not convert", "Stops before the first frame", "Logos or loading screen", "Menu", "In game")
HIDDEN = 0x08000000


def _run(args, cwd=None):
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cwd, creationflags=HIDDEN)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def _gql(query, **vars_):
    args = ["gh", "api", "graphql", "-f", "query=" + query]
    for k, v in vars_.items():
        args += ["-F" if isinstance(v, int) else "-f", f"{k}={v}"]
    code, out, err = _run(args)
    if code:
        raise RuntimeError("GitHub: " + (err or out)[:400])
    return json.loads(out)["data"]


def game_dir(game):
    p = Path(game)
    if p.is_dir():
        return p
    hits = [d for d in GAMES.iterdir() if d.is_dir() and not d.name.startswith("_") and game.lower() in d.name.lower()]
    exact = [d for d in hits if d.name.lower().startswith(game.lower() + " (")]
    if len(exact) == 1:
        hits = exact
    if len(hits) != 1:
        raise ValueError(f"'{game}' matches {len(hits)} game folders" + (": " + ", ".join(d.name for d in hits[:6]) if hits else ""))
    return hits[0]


def param(gdir):
    for p in [gdir / "app0" / "sce_sys" / "param.json", *gdir.glob("*/sce_sys/param.json"), *gdir.glob("sce_sys/param.json")]:
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8-sig", errors="replace"))
            loc = d.get("localizedParameters", {})
            lang = loc.get("defaultLanguage", "en-US")
            name = (loc.get(lang) or loc.get("en-US") or {}).get("titleName") or d.get("titleName", "")
            return {"name": name, "id": d.get("titleId", ""), "version": d.get("contentVersion", "")}
    raise ValueError(f"no sce_sys/param.json in {gdir}")


def last_result(gdir):
    """The newest RECHECKED/TESTED event for this folder: its result and the commit it ran on."""
    log = TOOLS / "auto-games.log"
    found = None
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.search(r"^(\d\d-\d\d \d\d:\d\d):\d\d\s+EVENT (RECHECKED|TESTED) (.+?): (.*)$", line)
        if m and m.group(3) == gdir.name:
            found = {"at": m.group(1), "kind": m.group(2), "text": m.group(4)}
    if not found:
        return None
    c = re.search(r"on ([0-9a-f]{10})\b", found["text"])
    found["short"] = c.group(1) if c else ""
    found["pinned"] = "REGRESSED" in found["text"]
    return found


def commit_facts(short):
    code, full, _ = _run(["git", "rev-parse", short + "^{commit}"], cwd=HEALTH)
    if code:
        raise ValueError(f"commit {short} is not in {HEALTH}")
    _run(["git", "fetch", "-q", "origin", "main"], cwd=HEALTH)
    on_main = _run(["git", "merge-base", "--is-ancestor", full, "origin/main"], cwd=HEALTH)[0] == 0
    date = _run(["git", "log", "-1", "--format=%cs", full], cwd=HEALTH)[1]
    return {"full": full, "date": date, "on_main": on_main}


def system():
    ps = ("$o=Get-CimInstance Win32_OperatingSystem; $c=(Get-CimInstance Win32_Processor).Name;"
          "$g=Get-CimInstance Win32_VideoController | Where-Object { $_.Name -match 'RTX|GTX|Radeon RX|Arc' } | Select-Object -First 1;"
          "'{0}|{1}|{2}|{3}|{4}' -f $o.Caption, $o.Version, $c.Trim(), $g.Name, $g.DriverVersion")
    caption, ver, cpu, gpu, drv = _run(["powershell", "-NoProfile", "-Command", ps])[1].split("|")
    nv = drv.replace(".", "")[-5:]
    driver = f"NVIDIA driver {nv[:3]}.{nv[3:]}" if "NVIDIA" in gpu else f"driver {drv}"
    cache = Path(r"D:\AnyPS5\build\health\CMakeCache.txt").read_text(encoding="utf-8", errors="replace")
    cxx = re.search(r"^CMAKE_CXX_COMPILER:FILEPATH=(.+)$", cache, re.M).group(1)
    first = _run([cxx, "--version"])[1].splitlines()[0]
    gcc = re.search(r"(\d+\.\d+\.\d+)\s*$", first).group(1)
    kind = "MinGW-w64 GCC " + gcc + (" UCRT (WinLibs)" if "ucrt" in first.lower() else "")
    return f"{caption.replace('Microsoft ', '')} {ver}, {cpu}, {gpu} ({driver}), Vulkan. Built myself: {kind}, Release."


def _shows_something(png):
    """Not a black or one-colour frame (11-04: Astro's Playroom's end shot was black while it ran)."""
    from PIL import Image, ImageStat
    # A dark menu (Octopath Traveler II's Initial Settings: white text on black) is not a black frame: count lit pixels.
    with Image.open(png) as im:
        g = im.convert("L")
        g = g.crop((0, min(40, g.height // 10), g.width, g.height))
        hist = g.histogram()
        lit = sum(hist[60:]) / max(1, g.width * g.height)
        return lit > 0.005 or ImageStat.Stat(g).stddev[0] > 12


def shot_for(gdir, short):
    """The newest screenshot from the latest run on this commit that shows something (a shot every 45 s and one at the end)."""
    shots = sorted((gdir / "test-shots").glob(f"*recheck-{short}*.png")) if short else []
    if not shots:
        return None
    latest = shots[-1].name[:15]
    run = [s for s in shots if s.name.startswith(latest)]
    good = [s for s in run if _shows_something(s)]
    return good[-1] if good else run[-1]


def existing_thread(title_id):
    q = '{search(query:"repo:%s/%s %s", type:DISCUSSION, first:10){nodes{... on Discussion{number title url category{slug}}}}}' % (*REPO, title_id)
    for n in _gql(q)["search"]["nodes"]:
        if n and n.get("category", {}).get("slug") == "game-bring-up" and title_id in n["title"]:
            return n
    return None


def facts(game):
    """Everything known for a report on this game, and what is still missing before it can be posted."""
    gdir = game_dir(game)
    p = param(gdir)
    res = last_result(gdir)
    f = {"dir": str(gdir), "title": f"{p['name']} ({p['id']})", "id": p["id"], "version": p["version"], "result": res, "missing": []}
    if not res or not res["short"]:
        f["missing"].append("no recheck on a known commit in auto-games.log")
        return f
    c = commit_facts(res["short"])
    f["commit"] = c
    if not c["on_main"]:
        f["missing"].append(f"{res['short']} is not on main")
    if res["pinned"]:
        f["missing"].append("its last recheck regressed and it was pinned back to an older build; report the regression, not a bring-up")
    shot = shot_for(gdir, res["short"])
    f["shot"] = str(shot) if shot else ""
    if not shot and "runs:" in res["text"]:
        f["missing"].append("no end-of-test screenshot from that commit yet (the next recheck saves one)")
    # 11-05 Scorn's recheck said FIXED at 42 FPS while all five shots were black.
    elif shot and "runs:" in res["text"] and not _shows_something(shot):
        f["missing"].append("every screenshot of that run is black; it drew nothing to show")
    # 11-04 MOUSE P.I., The Exit 8, Platform 8 and Hades II come from "BP 4.xx" backports (the executable patched for older
    # system software); a report says so, since a backport can behave differently from the original.
    cfg = json.loads((gdir / "game.json").read_text(encoding="utf-8-sig"))
    # Only real backport markers: "2.xx+" on a download is the minimum firmware of an original (Hades), not a backport.
    bp = r"(?i)backport|\bBP ?\d\.?xx"
    f["backport"] = any(re.search(bp, str(cfg.get(k) or "")) for k in ("dump", "resources", "sceSys"))
    if not f["backport"]:
        # The log names a game by its title or by its download folder ("hades 2" for Hades II).
        names = {str(cfg.get("title") or gdir.name.split(" (")[0]).lower()}
        m = re.search(r"(?i)dumps from ps5\\([^\\]+)", str(cfg.get("resources") or ""))
        if m:
            names.add(m.group(1).lower())
        log = (TOOLS / "auto-games.log").read_text(encoding="utf-8", errors="replace").lower()
        events = [line for line in log.splitlines() if "event" in line and any(n + ":" in line for n in names)]
        # 11-05 Blu replaced the Hades II backport with an original: only events since the pipeline's last NEW COPY count
        # (one copy can be several archives, such as backport files next to the game).
        fresh = [i for i, line in enumerate(events) if "event new copy" in line]
        f["backport"] = any(re.search(bp, line) for line in events[fresh[-1] if fresh else 0:])
    # Blu 11-04: "Don't add those copies or backport yet ... I can get the actual copies": no report from a backport.
    if f["backport"]:
        f["missing"].append("this copy is a backport; wait for an original copy (Blu 11-04)")
    # 11-04 Ghost Song still ran a 10-28 PR build of libSceHttp2 from mods\libs: such a run is not plain main.
    over = [p.name for p in (gdir / "mods" / "libs").glob("*.prx")] if (gdir / "mods" / "libs").is_dir() else []
    if over:
        f["missing"].append("its mods replace AnyPS5 libraries (" + ", ".join(over) + "), so the run is not plain main")
    env = gdir / "game-env.txt"
    f["env"] = [l.strip() for l in env.read_text(encoding="utf-8", errors="replace").splitlines() if "=" in l] if env.exists() else []
    f["system"] = system()
    # Blu 11-04: a title with a thread gets our result as a comment in it; one without gets a new thread.
    t = existing_thread(p["id"])
    f["thread"] = t["url"] if t else ""
    f["thread_number"] = t["number"] if t else 0
    done = _posted().get(gdir.name, {})
    if done.get("commit") == c["full"][:10]:
        f["missing"].append(f"already reported on this commit: {done.get('url')}")
    return f


def _posted():
    log = OUT / "posted.json"
    return json.loads(log.read_text(encoding="utf-8")) if log.exists() else {}


def draft(game, shown, furthest, blocker="", notes="", image="", hand=False):
    """The report in the form's layout, saved to _reports\\bringup\\<game>.md. shown = what the end-of-test screenshot and
    the run showed (frame rate, picture, audio, input); furthest = one of the form's choices; image = the screenshot's
    GitHub attachment markdown (Blu 11-04: a test report carries its picture; the API cannot upload one, so it is uploaded
    through GitHub's own attachment upload in Chrome and its <img ...> tag passed here)."""
    if furthest not in FURTHEST:
        raise ValueError("furthest must be one of: " + ", ".join(FURTHEST))
    if not shown.strip():
        raise ValueError("say what the run showed: look at the screenshot first")
    if image.strip():
        shown = shown.strip() + "\n\n" + image.strip()
    f = facts(game)
    res, c = f["result"], f.get("commit", {})
    fps = re.search(r"FPS: ([0-9.]+) \((\d+)\)", res["text"] if res else "")
    first = re.search(r"first frame after (\d+) s", res["text"] if res else "")
    # Blu 11-04: the pipeline cannot see the screen, so it sends no input and cannot get past a game's own prompts; while
    # Blu is at the PC he plays the test runs by hand, and the furthest point is where he got. hand=True says so.
    run = ["Launched by my test pipeline right after relinking; the run ends three minutes after the first frame once 600 frames are drawn (20 minutes at most)."]
    if hand is not None:
        run.append("The pipeline sends no input; during this run I played it by hand to get past its screens, so the furthest point is where I got." if hand else
                   "No input was sent during this run.")
    run.append("Environment: " + (", ".join(f"`{e}`" for e in f["env"]) + " (the title needs it to start)." if f["env"] else "none; no trace or profiling switches."))
    if fps:
        run.append(f"AnyPS5's window title read {fps.group(1)} FPS with {fps.group(2)} frames drawn at the end" + (f"; the first frame came after {first.group(1)} s." if first else "."))
    run.append("The run, the screenshot check and this write-up were done with AI assistance (Claude Code); the facts are from the run's own log and window.")
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / (Path(f["dir"]).name + ".md")
    if f.get("thread"):
        lines = [f"Update on plain `main` `{c.get('full', '?')[:10]}` ({c.get('date', '?')}), no pull requests or local changes; the executable was converted by that commit's relinker on the day of the run. Content version {f['version']}. {f.get('system', '')}",
                 "", f"- **Furthest point:** {furthest}", f"- **What is shown:** {shown.strip()}"]
        if blocker.strip():
            lines.append(f"- **Blocker:** {blocker.strip()}")
        lines += ["", "**How it was run:** " + " ".join(run[:-1]) + (("\n\n" + notes.strip()) if notes.strip() else ""), "", "AI-assisted: yes."]
        path.write_text(f"<!-- comment: {f['thread']} -->\n" + "\n".join(lines) + "\n", encoding="utf-8")
        return {"path": str(path), "missing": f["missing"], "shot": f.get("shot", ""), "body": "\n".join(lines), "thread": f["thread"]}
    body = [
        "### Existing thread", "", "- [X] I searched this category for the title ID and no thread exists for this title on any platform.", "",
        "### Title", "", f["title"], "",
        "### Content version tested", "", f["version"], "",
        "### AnyPS5 commit", "", f"`main` {c.get('full', '?')[:10]} ({c.get('date', '?')}), with no pull requests or local changes on top. The executable was converted by that commit's relinker on the day of the run.", "",
        "### System tested", "", f.get("system", ""), "",
        "### Furthest point", "", furthest, "",
        "### What is shown", "", shown.strip(), "",
        "### Current blocker", "", blocker.strip() or "_No response_", "",
        "### How it was run", "", " ".join(run) + (("\n\n" + notes.strip()) if notes.strip() else ""),
    ]
    path.write_text(f"<!-- title: {f['title']} -->\n" + "\n".join(body) + "\n", encoding="utf-8")
    return {"path": str(path), "missing": f["missing"], "shot": f.get("shot", ""), "body": "\n".join(body)}


def post(game):
    """Posts the saved draft as a new Game bring-up thread. Refuses while anything is missing; records the URL."""
    gdir = game_dir(game)
    path = OUT / (gdir.name + ".md")
    if not path.exists():
        return "No draft yet: draft() it first."
    f = facts(game)
    if f["missing"]:
        return "Not posted: " + "; ".join(f["missing"])
    text = path.read_text(encoding="utf-8")
    head, body = text.split("\n", 1)
    if f["thread"]:
        if not head.startswith("<!-- comment:"):
            return f"Not posted: a thread exists now ({f['thread']}); draft() it again so it becomes a comment there."
        did = _gql('{repository(owner:"%s",name:"%s"){discussion(number:%d){id}}}' % (*REPO, f["thread_number"]))["repository"]["discussion"]["id"]
        d = _gql("mutation($d:ID!,$b:String!){addDiscussionComment(input:{discussionId:$d,body:$b}){comment{url}}}", d=did, b=body)["addDiscussionComment"]["comment"]
        kind = "comment"
    else:
        if not head.startswith("<!-- title:"):
            return "Not posted: the draft is a comment but no thread exists; draft() it again."
        title = re.match(r"<!-- title: (.+?) -->", head).group(1)
        ids = _gql('{repository(owner:"%s",name:"%s"){id discussionCategories(first:30){nodes{id slug}}}}' % REPO)["repository"]
        cat = next(n["id"] for n in ids["discussionCategories"]["nodes"] if n["slug"] == "game-bring-up")
        d = _gql("mutation($r:ID!,$c:ID!,$t:String!,$b:String!){createDiscussion(input:{repositoryId:$r,categoryId:$c,title:$t,body:$b}){discussion{number url}}}",
                 r=ids["id"], c=cat, t=title, b=body)["createDiscussion"]["discussion"]
        kind = "thread"
    done = _posted()
    done[gdir.name] = {"url": d["url"], "kind": kind, "commit": f["commit"]["full"][:10], "posted": __import__("time").strftime("%Y-%m-%d %H:%M")}
    (OUT / "posted.json").write_text(json.dumps(done, indent=1), encoding="utf-8")
    return f"Posted ({kind}) {d['url']}"


def update(game):
    """Pushes the saved draft's current text to the report already posted (a correction), keeping it a thread or comment."""
    gdir = game_dir(game)
    done = _posted().get(gdir.name)
    path = OUT / (gdir.name + ".md")
    if not done or not path.exists():
        return "Nothing posted for this game yet."
    body = path.read_text(encoding="utf-8").split("\n", 1)[1]
    num = int(re.search(r"/discussions/(\d+)", done["url"]).group(1))
    if done["kind"] == "thread":
        did = _gql('{repository(owner:"%s",name:"%s"){discussion(number:%d){id}}}' % (*REPO, num))["repository"]["discussion"]["id"]
        _gql("mutation($d:ID!,$b:String!){updateDiscussion(input:{discussionId:$d,body:$b}){discussion{url}}}", d=did, b=body)
    else:
        cid = int(re.search(r"discussioncomment-(\d+)", done["url"]).group(1))
        nodes = _gql('{repository(owner:"%s",name:"%s"){discussion(number:%d){comments(last:100){nodes{id databaseId}}}}}' % (*REPO, num))["repository"]["discussion"]["comments"]["nodes"]
        node = next((n["id"] for n in nodes if n["databaseId"] == cid), None)
        if not node:
            return "Could not find our comment in the thread."
        _gql("mutation($c:ID!,$b:String!){updateDiscussionComment(input:{commentId:$c,body:$b}){comment{url}}}", c=node, b=body)
    return f"Updated: {done['url']}"


def add_image(game, image):
    """Adds the screenshot (GitHub attachment markdown) to a report already posted, right after what it shows."""
    gdir = game_dir(game)
    done = _posted().get(gdir.name)
    path = OUT / (gdir.name + ".md")
    if not done or not path.exists():
        return "Nothing posted for this game yet."
    text = path.read_text(encoding="utf-8")
    if image.strip() in text:
        return "The image is already in the report."
    head, body = text.split("\n", 1)
    if done["kind"] == "thread":
        body = re.sub(r"(### What is shown\n\n.*?)(\n\n### )", lambda m: m.group(1) + "\n\n" + image.strip() + m.group(2), body, count=1, flags=re.S)
    else:
        body = re.sub(r"(- \*\*What is shown:\*\* [^\n]*)", lambda m: m.group(1) + "\n\n" + image.strip(), body, count=1)
    if image.strip() not in body:
        return "Could not find where the report says what is shown."
    num = int(re.search(r"/discussions/(\d+)", done["url"]).group(1))
    if done["kind"] == "thread":
        did = _gql('{repository(owner:"%s",name:"%s"){discussion(number:%d){id}}}' % (*REPO, num))["repository"]["discussion"]["id"]
        _gql("mutation($d:ID!,$b:String!){updateDiscussion(input:{discussionId:$d,body:$b}){discussion{url}}}", d=did, b=body)
    else:
        cid = int(re.search(r"discussioncomment-(\d+)", done["url"]).group(1))
        nodes = _gql('{repository(owner:"%s",name:"%s"){discussion(number:%d){comments(last:100){nodes{id databaseId}}}}}' % (*REPO, num))["repository"]["discussion"]["comments"]["nodes"]
        node = next((n["id"] for n in nodes if n["databaseId"] == cid), None)
        if not node:
            return "Could not find our comment in the thread."
        _gql("mutation($c:ID!,$b:String!){updateDiscussionComment(input:{commentId:$c,body:$b}){comment{url}}}", c=node, b=body)
    path.write_text(head + "\n" + body, encoding="utf-8")
    return f"Image added: {done['url']}"


def pending():
    """Every game whose latest run could become a report: ready (all facts there), or what each one still lacks."""
    rows = []
    posted = json.loads((OUT / "posted.json").read_text(encoding="utf-8")) if (OUT / "posted.json").exists() else {}
    for gdir in sorted(d for d in GAMES.iterdir() if d.is_dir() and not d.name.startswith("_") and (d / "game.json").exists()):
        if gdir.name in posted:
            rows.append(f"posted  {gdir.name}: {posted[gdir.name]['url']}")
            continue
        try:
            f = facts(str(gdir))
        except Exception as e:
            rows.append(f"skip    {gdir.name}: {e}")
            continue
        rows.append(("ready   " if not f["missing"] else "waiting ") + gdir.name + ("" if not f["missing"] else ": " + "; ".join(f["missing"])))
    return "\n".join(rows)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "facts":
        print(json.dumps(facts(sys.argv[2]), indent=1))
    else:
        print(pending())
