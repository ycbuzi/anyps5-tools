"""Watches Blu's open AnyPS5 pull requests for what needs him: a check that failed (or went green again), a new
review, a new comment from someone else, a merge conflict, a merge. Prints {"events": [...]} with only what changed
since the last run (state in pr-watch.json). Read only (gh). Offline: prints no events.

usage: python -I pr_watch.py
"""
import json
import subprocess
from pathlib import Path

REPO, ME = "boykopovar/AnyPS5", "ycbuzi"
STATE = Path(__file__).resolve().parent / "pr-watch.json"


def checks(rollup):
    states = [(c.get("conclusion") or c.get("state") or c.get("status") or "").upper() for c in rollup or []]
    bad = [c.get("name") or c.get("context") or "?" for c in rollup or [] if (c.get("conclusion") or c.get("state") or "").upper() in ("FAILURE", "ERROR", "TIMED_OUT")]
    if bad:
        return "failing: " + ", ".join(sorted(set(bad))[:4])
    if any(s in ("", "PENDING", "QUEUED", "IN_PROGRESS", "EXPECTED", "WAITING") for s in states):
        return "running"
    return "passing" if states else "none"


def main():
    r = subprocess.run(["gh", "pr", "list", "--repo", REPO, "--author", ME, "--state", "all", "--limit", "20", "--json",
                        "number,title,state,statusCheckRollup,reviewDecision,mergeable,comments,reviews,url"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(json.dumps({"events": [], "offline": True}))
        return
    try:
        old = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = {}
    new, events = {}, []
    for p in json.loads(r.stdout):
        n = str(p["number"])
        others = [c for c in (p.get("comments") or []) if c["author"]["login"] != ME]
        reviews = [v for v in (p.get("reviews") or []) if v["author"]["login"] != ME]
        cur = {"state": p["state"], "checks": checks(p.get("statusCheckRollup")) if p["state"] == "OPEN" else "",
               "review": p.get("reviewDecision") or "", "conflict": p.get("mergeable") == "CONFLICTING",
               "comments": len(others), "reviews": len(reviews)}
        new[n] = cur
        was = old.get(n)
        if not was:
            continue  # first sight: nothing "new" to report
        title = f"#{n} {p['title'][:60]}"
        if cur["state"] == "MERGED" and was["state"] != "MERGED":
            events.append(f"{title} was MERGED.")
        if cur["checks"] != was["checks"] and cur["checks"].startswith(("failing", "passing")):
            events.append(f"{title}: checks {cur['checks']}.")
        if cur["conflict"] and not was["conflict"]:
            events.append(f"{title} now has a merge conflict with main (sync_pr_branch fixes it).")
        if cur["reviews"] > was["reviews"]:
            v = reviews[-1]
            events.append(f"{title}: new review by {v['author']['login']} ({v.get('state', '').replace('_', ' ').lower()}): {(v.get('body') or '')[:160]}")
        if cur["comments"] > was["comments"]:
            c = others[-1]
            events.append(f"{title}: new comment by {c['author']['login']}: {(c.get('body') or '')[:160]}")
    # 11-05 a one-time balloon for a new comment went unseen and #14168 (an issue) was never watched: every thread of ours
    # where someone else spoke last is named again every 6 hours until it is answered (assistant/needs_reply.py).
    reminded = old.get("_reminded", {})
    try:
        import sys, time
        sys.path.insert(0, str(Path(__file__).resolve().parent / "assistant"))
        import needs_reply
        now = time.time()
        waiting = needs_reply.waiting()
        keys = {f"{t['number']} {t['waiting_since']}" for t in waiting}
        reminded = {k: v for k, v in reminded.items() if k in keys}
        for t in waiting:
            key = f"{t['number']} {t['waiting_since']}"
            if now - reminded.get(key, 0) < 6 * 3600:
                continue
            reminded[key] = now
            who = ", ".join(t["from"])
            events.append(f"#{t['number']} {t['title'][:60]} is waiting for your reply since {t['waiting_since'][:16].replace('T', ' ')} ({who}): {t['url']}")
    except (SystemExit, Exception) as e:
        events.append(f"the waiting-for-reply check failed: {e}")
    new["_reminded"] = reminded
    STATE.write_text(json.dumps(new), encoding="utf-8")
    print(json.dumps({"events": [e.replace("\r", " ").replace("\n", " ") for e in events]}))


main()
