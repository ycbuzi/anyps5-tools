import datetime
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

QROOT = "/data/data/com.termux/files/usr/var/lib/proot-distro/containers/debian/rootfs/root/qtest/outbox"
REPO = Path(r"D:\AnyPS5")
LANE = Path(r"C:\AnyPS5 Games\_tools\questbuild")
FORK_URL = "https://github.com/ycbuzi/AnyPS5.git"
LOG = Path(r"C:\AnyPS5 Games\_reports\questbuild\patch-relay.log")
HIDDEN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SAFE = re.compile(r"^[A-Za-z0-9._/-]+$")


def log(msg):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(datetime.datetime.now().strftime("%m-%d %H:%M:%S ") + msg + "\n")


def run(cmd, cwd=None, timeout=600):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, creationflags=HIDDEN)
    return r.returncode, (r.stdout + r.stderr).strip()


def ssh(command):
    return run(["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", "quest", command], timeout=120)


def identity():
    _, email = run(["git", "-C", str(REPO), "config", "user.email"])
    return ["-c", "user.name=blu", "-c", f"user.email={email}"]


def handle(name, spec):
    fields = dict(line.split("=", 1) for line in spec.splitlines() if "=" in line)
    base, branch, folder = fields.get("base", ""), fields.get("branch", ""), fields.get("folder", "")
    patches = [p for p in fields.get("patches", "").split() if p]
    tests = fields.get("tests", "")
    if not (re.fullmatch(r"[0-9a-f]{7,40}", base) and branch.startswith("quest/") and SAFE.match(branch) and SAFE.match(folder) and patches):
        return f"rejected: needs base=<sha>, branch=quest/<name>, folder=<name>, patches=<files in outbox>"
    if tests and not SAFE.match(tests.replace(",", "")):
        return "rejected: bad tests field"
    work = Path(tempfile.mkdtemp(prefix="qpatch-"))
    wt = work / "wt"
    try:
        local = []
        for p in patches:
            if not SAFE.match(p) or "/" in p:
                return f"rejected: bad patch name {p}"
            dest = work / p
            code, out = run(["scp", "-q", f"quest:{QROOT}/{p}", str(dest)], timeout=120)
            if code:
                return f"cannot fetch {p}: {out[-200:]}"
            local.append(str(dest))
        run(["git", "-C", str(REPO), "fetch", "-q", "fork"], timeout=300)
        code, out = run(["git", "-C", str(REPO), "worktree", "add", "-q", "--detach", str(wt), base])
        if code:
            return f"base {base} not found: {out[-200:]}"
        code, out = run(["git", *identity(), "am", "-3", "-q", *local], cwd=wt)
        if code:
            run(["git", "am", "--abort"], cwd=wt)
            return f"git am failed: {out[-300:]}"
        _, head = run(["git", "rev-parse", "HEAD"], cwd=wt)
        code, out = run(["git", "push", "-q", "fork", f"HEAD:refs/heads/{branch}"], cwd=wt, timeout=300)
        if code:
            return f"push failed: {out[-200:]}"
        run(["git", "pull", "-q", "--rebase", FORK_URL, "questbuild-runner"], cwd=LANE, timeout=300)
        line = f"{head} refs/heads/{branch} {folder}" + (f" tests={tests}" if tests else "")
        with (LANE / "queue.txt").open("a", encoding="utf-8", newline="\n") as f:
            f.write(line + "\n")
        run(["git", *identity(), "commit", "-qam", f"queue: {folder}"], cwd=LANE)
        code, out = run(["git", "push", "-q", FORK_URL, "HEAD:questbuild-runner"], cwd=LANE, timeout=300)
        if code:
            return f"queue push failed: {out[-200:]}"
        return f"queued {folder} at {head[:10]} on {branch}"
    finally:
        run(["git", "-C", str(REPO), "worktree", "remove", "--force", str(wt)])
        shutil.rmtree(work, ignore_errors=True)


def main():
    code, listing = ssh(f"ls {QROOT}/*.req 2>/dev/null; true")
    if code:
        log(f"Quest unreachable: {listing[-200:]}")
        return 0
    for path in [l for l in listing.splitlines() if l.endswith(".req")]:
        name = path.rsplit("/", 1)[-1]
        _, spec = ssh(f"cat '{path}'")
        result = handle(name, spec).replace("'", "").replace("\n", " ")
        log(f"{name}: {result}")
        status = "done" if result.startswith("queued") else "failed"
        ssh(f"mv '{path}' '{path[:-4]}.{status}' && printf '%s\\n' '{result}' >> '{path[:-4]}.{status}'")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log(f"error: {e}")
        sys.exit(1)
