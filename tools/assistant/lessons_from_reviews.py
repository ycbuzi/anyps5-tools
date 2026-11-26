import datetime
import re
import json
import pathlib
import subprocess

REPO = "boykopovar/AnyPS5"
AUTHOR = "ycbuzi"
OUT = pathlib.Path(r"C:\AnyPS5 Games\_reports\lessons-candidates.md")
SEEN = pathlib.Path(r"C:\AnyPS5 Games\_tools\live\lessons-seen.json")


def gh(*args):
    r = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8", timeout=120)
    return json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else []


def main():
    seen = set(json.loads(SEEN.read_text(encoding="utf-8"))) if SEEN.exists() else set()
    prs = gh("pr", "list", "-R", REPO, "--author", AUTHOR, "--state", "all", "--limit", "40", "--json", "number,title")
    rows = []
    for pr in prs:
        n = pr["number"]
        comments = gh("api", f"repos/{REPO}/pulls/{n}/comments", "--paginate")
        reviews = gh("api", f"repos/{REPO}/pulls/{n}/reviews", "--paginate")
        issue = gh("api", f"repos/{REPO}/issues/{n}/comments", "--paginate")
        for c in [*comments, *reviews, *issue]:
            cid = str(c.get("id"))
            who = (c.get("user") or {}).get("login", "")
            body = (c.get("body") or "").strip()
            if not body or cid in seen or who in (AUTHOR, "github-actions[bot]"):
                continue
            if not c.get("path") and c.get("state") not in ("CHANGES_REQUESTED", "COMMENTED") and re.search(r"(?i)\bsetup:|check of head|run of head|tested on|ctest", body):
                seen.add(cid)
                continue
            seen.add(cid)
            where = c.get("path", "")
            rows.append(f"- #{n} {who}{' ' + where if where else ''}: {body[:300].replace(chr(10), ' ')}")
    if rows:
        with OUT.open("a", encoding="utf-8") as f:
            f.write(f"\n## {datetime.datetime.now():%Y-%m-%d %H:%M} new review feedback (turn real mistakes into a line in coding-lessons.md)\n")
            f.write("\n".join(rows) + "\n")
    SEEN.parent.mkdir(parents=True, exist_ok=True)
    SEEN.write_text(json.dumps(sorted(seen)), encoding="utf-8")
    print(f"{len(rows)} new review comments")


if __name__ == "__main__":
    main()
