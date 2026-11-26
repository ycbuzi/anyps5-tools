"""Our GitHub threads where someone else has the last word: every open issue and PR by ycbuzi, or that ycbuzi took part
in, whose newest human comment, review or unresolved review thread is not ours.

Blu 11-05: "There's no reason none of our PRs should" go unanswered. zaryabzip asked for a shader dump on #14168 on 11-01
and two testers confirmed #14296 on 11-02; nobody answered for days, because the AI team's PR role may not run
`gh issue`/`gh api` and called the threads clean without reading a comment. This runs gh itself, no model involved.

usage: python -I needs_reply.py [--json]     writes _reports/needs-reply.md (and .json); prints one line per thread
"""
import json
import pathlib
import subprocess
import sys

ME = "ycbuzi"
REPO = "boykopovar/AnyPS5"
REPORTS = pathlib.Path(r"D:\AnyPS5 Games\_reports")
# CI and bot posts report results; they need no answer. TechLevelZero's GPU runs carry these markers.
CI_MARKERS = ("<!-- anyps5-nvidia-ci", "<!-- anyps5-amd-ci", "<!-- pr-overlap", "<!-- anyps5-")

QUERY = """query($q: String!) { search(query: $q, type: ISSUE, first: 60) { nodes {
  ... on Issue { __typename number title url author { login }
    comments(last: 15) { nodes { author { login } createdAt body url } } }
  ... on PullRequest { __typename number title url author { login }
    comments(last: 15) { nodes { author { login } createdAt body url } }
    reviews(last: 15) { nodes { author { login } submittedAt body state url } }
    reviewThreads(last: 30) { nodes { isResolved comments(last: 1) { nodes { author { login } createdAt body url } } } } } } } }"""


def gh_json(*args):
    r = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise SystemExit(f"gh failed: {r.stderr.strip()[:300]}")
    return json.loads(r.stdout)


def human(login, body):
    if not login or login.endswith("[bot]") or login == "github-actions":
        return False
    return not any(m in (body or "") for m in CI_MARKERS)


def asks_us(body):
    """Names us and asks something ("thanks @ycbuzi" alone does not)."""
    b = (body or "").lower()
    at = f"@{ME}"
    # The ask must follow the mention in the same few sentences ("@x thanks ... @y please ..." asks y, not x).
    for i in [k for k in range(len(b)) if b.startswith(at, k)]:
        near = b[i:i + 250].split("@", 2)
        near = "@" + near[1] if len(near) > 1 else b[i:i + 250]
        if "?" in near or any(w in near for w in ("please", "could you", "can you", "would you")):
            return True
    return False


def threads():
    seen = {}
    for q in (f"repo:{REPO} is:open author:{ME}", f"repo:{REPO} is:open commenter:{ME}"):
        for n in gh_json("api", "graphql", "-f", f"query={QUERY}", "-f", f"q={q}")["data"]["search"]["nodes"]:
            if n.get("number"):
                seen[n["number"]] = n
    return seen.values()


def last_words(n):
    events = []
    for c in n["comments"]["nodes"]:
        events.append((c["createdAt"], (c["author"] or {}).get("login"), c["body"], c["url"], "comment"))
    for r in (n.get("reviews") or {}).get("nodes", []):
        if r["state"] in ("CHANGES_REQUESTED", "COMMENTED") and (r["body"] or r["state"] == "CHANGES_REQUESTED"):
            events.append((r["submittedAt"], (r["author"] or {}).get("login"), r["body"] or r["state"], r["url"], "review " + r["state"].lower()))
    for t in (n.get("reviewThreads") or {}).get("nodes", []):
        if not t["isResolved"] and t["comments"]["nodes"]:
            c = t["comments"]["nodes"][-1]
            events.append((c["createdAt"], (c["author"] or {}).get("login"), c["body"], c["url"], "unresolved review thread"))
    events = [e for e in events if e[1] == ME or human(e[1], e[2])]
    events.sort()
    return events


def waiting():
    """The threads waiting for our answer (also used by the app's PR watcher, _tools/pr_watch.py)."""
    out = []
    for n in sorted(threads(), key=lambda n: n["number"]):
        ev = last_words(n)
        if not ev or ev[-1][1] == ME:
            continue
        ours = [e for e in ev if e[1] == ME]
        theirs = [e for e in ev if e[1] != ME and (not ours or e[0] > ours[-1][0])]
        # On someone else's thread our test report or comment needs no follow-up unless they ask us by name.
        if (n["author"] or {}).get("login") != ME and not any(asks_us(e[2]) for e in theirs):
            continue
        out.append({"number": n["number"], "kind": n["__typename"], "title": n["title"], "url": n["url"],
                    "waiting_since": theirs[0][0], "from": sorted({e[1] for e in theirs}),
                    "items": [{"at": e[0], "who": e[1], "what": e[4], "url": e[3], "text": (e[2] or "")[:600]} for e in theirs]})
    return out


def main():
    out = waiting()
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "needs-reply.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    lines = ["# Our GitHub threads waiting for our answer", "",
             "Written by `_tools/assistant/needs_reply.py`. Each one: someone else spoke last; answer it with facts from a run.", ""]
    for t in out:
        lines.append(f"## #{t['number']} {t['title']}")
        lines.append(f"{t['url']} - waiting since {t['waiting_since'][:16]} - from {', '.join(t['from'])}")
        for i in t["items"]:
            lines.append(f"- {i['at'][:16]} {i['who']} ({i['what']}): {i['text'][:300].replace(chr(10), ' ')}")
        lines.append("")
    if not out:
        lines.append("Nothing waiting.")
    (REPORTS / "needs-reply.md").write_text("\n".join(lines), encoding="utf-8")
    if "--json" in sys.argv:
        print(json.dumps(out))
    else:
        for t in out:
            print(f"#{t['number']} waiting since {t['waiting_since'][:16]} from {', '.join(t['from'])}: {t['title'][:80]}")


if __name__ == "__main__":
    main()
