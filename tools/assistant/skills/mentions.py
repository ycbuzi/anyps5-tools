"""Watches GitHub for new comments that come Blu's way (11-11, Blu: "watch for any new PR comments that come my way and
put a background task"). Offline: gh only, no Claude.

Every run reads ycbuzi's GitHub notifications (mentions, replies on threads we are in, review requests), fetches each
new comment and keeps it in _reports/github-mentions.md, newest first: who, where, what kind (asks us / thanks / reply)
and the start of the text. Comments by ycbuzi are skipped. New ones that ask something pop a Windows notification.
Notifications are never marked read on GitHub; what was seen is kept in _reports/github-mentions.json.

    python -I mentions.py            check now
Scheduled task "AnyPS5 GitHub mentions" runs it every 15 minutes.
"""
import json
import re
import subprocess
import time
from pathlib import Path

ME = "ycbuzi"
REPORTS = Path(r"D:\AnyPS5 Games\_reports")
STATE = REPORTS / "github-mentions.json"
OUT = REPORTS / "github-mentions.md"
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
REASONS = {"mention", "team_mention", "comment", "author", "review_requested", "assign"}


def gh(path):
    r = subprocess.run(["gh", "api", path], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90, creationflags=NOWIN)
    if r.returncode:
        raise RuntimeError(r.stderr.strip()[:200])
    return json.loads(r.stdout or "null")


def kind(body):
    low = body.lower()
    request = re.search(r"\?|could you|can you|please|would you|whenever|when you get a chance|calling on", low)
    if re.search(r"\bthank", low[:200]) and not request:
        return "thanks"
    if f"@{ME}" in low and request:
        return "asks us"
    if f"@{ME}" in low:
        return "mentions us"
    return "reply"


def toast(title, text):
    esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("'", "''")
    ps = ("[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
          "$x = New-Object Windows.Data.Xml.Dom.XmlDocument;"
          f"$x.LoadXml('<toast><visual><binding template=\"ToastGeneric\"><text>{esc(title)}</text><text>{esc(text)}</text></binding></visual></toast>');"
          "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe').Show([Windows.UI.Notifications.ToastNotification]::new($x))")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=30, creationflags=NOWIN)


def main():
    first = not STATE.exists()
    try:
        state = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {"seen": [], "since": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 86400)), "items": []}
    seen = set(state["seen"])
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    notes = gh(f"notifications?all=true&participating=true&per_page=50&since={state['since']}") or []
    new = []
    for n in notes:
        if n.get("reason") not in REASONS:
            continue
        url = (n.get("subject") or {}).get("latest_comment_url") or ""
        if "/comments/" not in url:
            continue
        cid = url.rsplit("/", 1)[1]
        if cid in seen:
            continue
        seen.add(cid)
        try:
            c = gh(url.replace("https://api.github.com/", ""))
        except Exception:
            continue
        who = (c.get("user") or {}).get("login", "?")
        if who == ME or who.endswith("[bot]"):
            continue
        body = (c.get("body") or "").strip()
        new.append({"at": c.get("created_at", ""), "who": who, "kind": kind(body), "title": n["subject"]["title"],
                    "repo": n["repository"]["full_name"], "url": c.get("html_url", ""), "text": re.sub(r"\s+", " ", body)[:300]})
    new.sort(key=lambda x: x["at"], reverse=True)
    state["items"] = (new + state.get("items", []))[:200]
    state["seen"] = list(seen)[-3000:]
    state["since"] = started
    STATE.write_text(json.dumps(state, indent=1), encoding="utf-8")
    lines = ["# GitHub comments that came our way", "", f"Checked {time.strftime('%m-%d %H:%M')}; newest first. Kinds: asks us, mentions us, thanks, reply.", ""]
    for i in state["items"]:
        num = re.search(r"/(?:pull|issues)/(\d+)", i["url"])
        lines.append(f"- {i['at'][5:16].replace('T', ' ')} **{i['kind']}** @{i['who']} on [{i['repo'].split('/')[-1]}#{num.group(1) if num else '?'}]({i['url']}) {i['title'][:80]}: {i['text'][:200]}")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    asks = [i for i in new if i["kind"] in ("asks us", "mentions us")]
    if asks and not first:
        where = lambda u: (re.search(r"/(?:pull|issues)/(\d+)", u) or re.match(r"()", "")).group(1)
        toast(f"GitHub: {len(asks)} new comment(s) for you", "; ".join(f"@{i['who']} on #{where(i['url'])}" for i in asks[:4]))
    print(f"{len(new)} new ({len(asks)} ask or mention us); {OUT}")


if __name__ == "__main__":
    main()
