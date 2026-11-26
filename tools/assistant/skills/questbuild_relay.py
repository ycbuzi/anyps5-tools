import base64
import datetime
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

FORK = "ycbuzi/AnyPS5"
UP = "boykopovar/AnyPS5"
BRANCH = "questbuild-runner"
REQ_URL = "http://192.0.2.1:9072/requests.txt"
LOG = Path(r"C:\AnyPS5 Games\_reports\questbuild\relay.log")
HIDDEN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def log(msg):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(datetime.datetime.now().strftime("%m-%d %H:%M:%S ") + msg + "\n")


def gh(args, inp=None):
    r = subprocess.run(["gh", *args], capture_output=True, text=True, input=inp, timeout=120, creationflags=HIDDEN)
    if r.returncode:
        raise RuntimeError(r.stderr.strip()[:300])
    return r.stdout


def quest_requests():
    with urllib.request.urlopen(REQ_URL, timeout=15) as r:
        text = r.read().decode("utf-8", "replace")
    out = []
    for line in text.splitlines():
        m = re.match(r"^\s*(\d{1,6})\s+([0-9a-f]{40})\s*$", line)
        if m:
            out.append((int(m.group(1)), m.group(2)))
    return out


def main():
    try:
        wanted = quest_requests()
    except OSError as e:
        log(f"Quest request list unreachable: {e}")
        return 0
    if not wanted:
        return 0
    meta = json.loads(gh(["api", f"repos/{FORK}/contents/queue.txt?ref={BRANCH}"]))
    queue = base64.b64decode(meta["content"]).decode("utf-8")
    have = set(queue.split())
    added = []
    for pr, sha in wanted:
        folder = f"pr{pr}-{sha[:11]}"
        if folder in have:
            continue
        queue = queue.rstrip("\n") + f"\n{sha} refs/pull/{pr}/head {folder} {UP}\n"
        added.append(folder)
    if not added:
        return 0
    body = json.dumps({"message": "queue: " + " ".join(added), "branch": BRANCH, "sha": meta["sha"],
                       "content": base64.b64encode(queue.encode("utf-8")).decode("ascii")})
    gh(["api", "-X", "PUT", f"repos/{FORK}/contents/queue.txt", "--input", "-"], inp=body)
    log("queued " + " ".join(added))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log(f"error: {e}")
        sys.exit(1)
