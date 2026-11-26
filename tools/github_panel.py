"""GitHub for the AnyPS5 Games window and its assistant, read live from GitHub (gh): Blu's AnyPS5 pull requests
(checks, review, conflicts, latest comments), the newest open pull requests, issues, discussions, and Blu's GitHub
notifications (comments, mentions, review requests). Prints one JSON object. When GitHub cannot be reached it
prints the last saved copy with "offline": true. Read only.

usage: python -I github_panel.py [prs|issues|discussions|activity|all]   (default all)
"""
import datetime
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = "boykopovar/AnyPS5"
ME = "ycbuzi"
CACHE = Path(__file__).resolve().parent / "github-panel-cache.json"


def gh(args):
    r = subprocess.run(["gh"] + args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:300])
    return json.loads(r.stdout) if r.stdout.strip() else None


def when(s):
    return (s or "")[:16].replace("T", " ")


def checks(rollup):
    # GitHub also lists older runs of a check, like a run cancelled when a new push replaced it: only the newest of each name counts.
    newest = {}
    for c in sorted(rollup or [], key=lambda c: c.get("completedAt") or c.get("startedAt") or ""):
        newest[c.get("name") or c.get("context") or ""] = c
    states = [(c.get("conclusion") or c.get("state") or c.get("status") or "").upper() for c in newest.values()]
    if not states:
        return "no checks"
    bad = sum(s in ("FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED") for s in states)
    waiting = sum(s in ("", "PENDING", "QUEUED", "IN_PROGRESS", "EXPECTED", "WAITING") for s in states)
    return f"{bad} failing" if bad else f"{waiting} running" if waiting else "passing"


def review_of(p):
    """GitHub's own summary is empty on this repository, so it comes from each reviewer's latest verdict."""
    latest = {}
    for r in sorted(p.get("reviews") or [], key=lambda r: r.get("submittedAt") or ""):
        if r.get("state") in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            latest[r["author"]["login"]] = r["state"]
    verdicts = set(latest.values())
    return (p.get("reviewDecision") or "").replace("_", " ").lower() or ("changes requested" if "CHANGES_REQUESTED" in verdicts else "approved" if "APPROVED" in verdicts else "")


def prs():
    mine = gh(["pr", "list", "--repo", REPO, "--author", ME, "--state", "all", "--limit", "40",
               "--json", "number,title,state,isDraft,updatedAt,url,statusCheckRollup,reviewDecision,mergeable,comments,reviews"])
    newest = gh(["pr", "list", "--repo", REPO, "--state", "open", "--limit", "80", "--json", "number,title,author,isDraft,updatedAt,url,reviewDecision"])
    rows = []
    for p in mine:
        if p["state"] == "CLOSED":
            continue
        talk = [(c.get("createdAt", ""), c["author"]["login"], c.get("body", "")) for c in p.get("comments") or []]
        talk += [(r.get("submittedAt", ""), r["author"]["login"], (r.get("state", "") + ": " + (r.get("body") or "")).strip()) for r in p.get("reviews") or []]
        talk = [t for t in sorted(talk) if t[1] != ME][-3:]
        rows.append({"number": p["number"], "title": p["title"], "author": ME, "mine": True, "url": p["url"],
                     "state": "merged" if p["state"] == "MERGED" else ("draft" if p["isDraft"] else "open"),
                     "checks": checks(p.get("statusCheckRollup")) if p["state"] == "OPEN" else "",
                     "review": review_of(p),
                     "conflicts": p.get("mergeable") == "CONFLICTING", "updated": when(p["updatedAt"]),
                     "latest": " | ".join(f"{a}: {b[:160]}" for _, a, b in reversed(talk)).replace("\r", " ").replace("\n", " ")})
    for p in newest:
        if p["author"]["login"] != ME:
            rows.append({"number": p["number"], "title": p["title"], "author": p["author"]["login"], "mine": False, "url": p["url"],
                         "state": "draft" if p["isDraft"] else "open", "checks": "", "review": (p.get("reviewDecision") or "").replace("_", " ").lower(),
                         "conflicts": False, "updated": when(p["updatedAt"]), "latest": ""})
    return rows


def issues():
    rows = gh(["issue", "list", "--repo", REPO, "--state", "open", "--limit", "80", "--json", "number,title,author,labels,updatedAt,comments,url"])
    return [{"number": i["number"], "title": i["title"], "author": i["author"]["login"], "mine": i["author"]["login"] == ME,
             "labels": ", ".join(l["name"] for l in i.get("labels") or []), "comments": len(i.get("comments") or []),
             "updated": when(i["updatedAt"]), "url": i["url"]} for i in rows]


def discussions():
    q = ('{repository(owner:"boykopovar",name:"AnyPS5"){discussions(first:40,orderBy:{field:UPDATED_AT,direction:DESC})'
         '{nodes{number title author{login} updatedAt comments{totalCount} category{name} url answerChosenAt}}}}')
    nodes = gh(["api", "graphql", "-f", "query=" + q])["data"]["repository"]["discussions"]["nodes"]
    return [{"number": d["number"], "title": d["title"], "author": (d.get("author") or {}).get("login", "?"), "mine": (d.get("author") or {}).get("login") == ME,
             "category": d["category"]["name"], "comments": d["comments"]["totalCount"], "answered": bool(d.get("answerChosenAt")),
             "updated": when(d["updatedAt"]), "url": d["url"]} for d in nodes]


def activity():
    items = gh(["api", "notifications?all=true&per_page=50"]) or []
    rows = []
    for n in items:
        s = n["subject"]
        url = (s.get("url") or "").replace("api.github.com/repos", "github.com").replace("/pulls/", "/pull/")
        rows.append({"reason": n["reason"].replace("_", " "), "type": s["type"], "title": s["title"], "repo": n["repository"]["full_name"],
                     "unread": n.get("unread", False), "updated": when(n["updated_at"]), "url": url or n["repository"]["html_url"]})
    return rows


def reports():
    """The PR test reports (prpost.py, 11-11): posted comments, reports that need a look first, and the queue. Read
    from local files, so it shows even when GitHub is offline."""
    import re
    root = Path(r"C:\AnyPS5 Games\_reports\pr-review")
    rows = []
    try:
        queued = len(json.loads((root / "state.json").read_text(encoding="utf-8")).get("pending", {}))
    except (OSError, ValueError):
        queued = 0
    try:
        for line in (root / "needs-review.md").read_text(encoding="utf-8").splitlines():
            m = re.match(r"- #(\d+) head (\w+): (.*)", line)
            if m:
                rows.append({"number": m.group(1), "status": "needs a look", "detail": f"head {m.group(2)}: {m.group(3)}", "when": "", "url": f"https://github.com/{REPO}/pull/{m.group(1)}"})
    except OSError:
        pass
    posted = []
    try:
        for line in (root / "prpost.log").read_text(encoding="utf-8").splitlines():
            m = re.match(r"(\d\d-\d\d \d\d:\d\d):\d\d #(\d+): posted (\S+) \((\d+) machine", line)
            if m:
                posted.append({"number": m.group(2), "status": "posted", "detail": f"{m.group(4)} machine report(s), no new failures", "when": m.group(1), "url": m.group(3)})
    except OSError:
        pass
    rows += list(reversed(posted))
    looks = sum(1 for r in rows if r["status"] == "needs a look")
    rows.insert(0, {"number": "", "status": "summary", "detail": f"{len(posted)} posted, {looks} need a look, {queued} still in the queue (posting runs every 30 min)", "when": datetime.datetime.now().strftime("%m-%d %H:%M"), "url": f"https://github.com/{REPO}/pulls?q=is%3Apr+commenter%3A{ME}"})
    return rows


PARTS = {"prs": prs, "issues": issues, "discussions": discussions, "activity": activity, "reports": reports}


def main():
    wanted = [a for a in sys.argv[1:] if a in PARTS] or list(PARTS)
    try:
        cache = json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    out = {"offline": False, "errors": {}}
    with ThreadPoolExecutor(4) as pool:
        futures = {k: pool.submit(PARTS[k]) for k in wanted}
        for k, f in futures.items():
            try:
                out[k] = f.result()
                cache[k] = out[k]
                cache[k + "Saved"] = datetime.datetime.now().strftime("%m-%d %H:%M")
            except Exception as e:  # offline or a GitHub error: the saved copy
                out[k] = cache.get(k, [])
                out["errors"][k] = str(e)[:200]
                out["offline"] = True
            out[k + "Saved"] = cache.get(k + "Saved", "never")
    try:
        CACHE.write_text(json.dumps(cache), encoding="utf-8")
    except OSError:
        pass
    out["saved"] = out.get("prsSaved", "")
    print(json.dumps(out))


main()
