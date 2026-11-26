"""Tests every open upstream PR on free GitHub-hosted runners in the public fork (11-09). Offline: gh + git, no Claude.

The fork branch `prtest-runner` (local clone C:/AnyPS5-work/forkci) holds .github/workflows/fork-prtest.yml and queue.json.
Each `tick` (scheduled task "AnyPS5 fork CI", every 30 minutes):
  1. collect: downloads finished runs' artifacts, compares each PR's failures with main's from the same run and writes
     _reports/pr-review/<PR>/gha-<os>-<kind>.md, adding the PR to the review queue (prreports.py queue).
  2. plan: when no run is queued or running, picks up to BATCH untested PR heads (newest first), classifies them by
     changed files and pushes a new queue.json, which starts a run.
Jobs per PR: full builds on windows-2025 and windows-2022; relinker-only PRs on windows-2022 and windows-11-arm
(upstream CI already runs relinker builds on windows-latest, Linux and macOS). Docs-only PRs are skipped.
PRs touching core/relinker/, cmake/ or the root CMakeLists.txt also get the linux/cpu job (11-17, replaces the MacBook and
Chromebook runners): one relinker-only build on ubuntu-latest whose ctest runs twice through qemu-x86_64, once with
-cpu SandyBridge (AVX, no AVX2/BMI/FMA/F16C/MOVBE) and once with -cpu Westmere (no AVX), reported as
gha-linux-sandybridge.md and gha-linux-westmere.md against main from the same run.

    python forkci.py tick | collect | plan | status
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

UP = "boykopovar/AnyPS5"
FORK = "ycbuzi/AnyPS5"
BRANCH = "prtest-runner"
CLONE = Path(r"C:\AnyPS5-work\forkci")
OUT = Path(r"C:\AnyPS5 Games\_reports\pr-review")
STATE = Path(r"C:\AnyPS5 Games\_reports\forkci\state.json")
LOG = STATE.with_name("forkci.log")
BATCH = 16
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
RELINKER = re.compile(r"^(core/relinker/|tools/|cmake/)|^CMakeLists\.txt$")
DOCS = re.compile(r"^docs/|\.md$")
CPU = re.compile(r"^(core/relinker/|cmake/)|^CMakeLists\.txt$")
CPU_JOB = ("linux", "cpu")
JOBS = {"full": [("windows-2025", "full"), ("windows-2022", "full")],
        "relinker": [("windows-2022", "relinker"), ("windows-11-arm", "relinker")]}


def log(t):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(time.strftime("%m-%d %H:%M:%S ") + t + "\n")


def run(cmd, seconds=300, cwd=None):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=seconds, cwd=cwd, creationflags=NOWIN)
    return r.returncode, r.stdout, r.stderr


def gh_json(args, seconds=300):
    rc, out, err = run(["gh"] + args, seconds)
    if rc:
        raise RuntimeError(f"gh {' '.join(args[:3])}: {err.strip()[:300]}")
    return json.loads(out) if out.strip() else None


def load():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"tested": {}, "collected": [], "kinds": {}}


def save(s):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, indent=1, sort_keys=True), encoding="utf-8")


def runs():
    return gh_json(["run", "list", "-R", FORK, "--branch", BRANCH, "-L", "30",
                    "--json", "databaseId,status,conclusion,createdAt"]) or []


def read_kv(p):
    d = {}
    if p.exists():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            k, _, v = line.partition("=")
            d[k.strip()] = v.strip()
    return d


def lines(p):
    return [x.strip() for x in p.read_text(encoding="utf-8", errors="replace").splitlines() if x.strip()] if p.exists() else []


def mark_pending(pr, tag):
    sys.path.insert(0, str(Path(__file__).parent))
    import prreports
    s = prreports.load()
    s["pending"].setdefault(pr, [])
    if tag not in s["pending"][pr]:
        s["pending"][pr].append(tag)
    prreports.save(s)


def collect(s):
    for r in runs():
        rid = str(r["databaseId"])
        if r["status"] != "completed" or rid in s["collected"]:
            continue
        tmp = Path(tempfile.mkdtemp(prefix="forkci-"))
        try:
            rc, _, err = run(["gh", "run", "download", rid, "-R", FORK, "-D", str(tmp)], 900)
            if rc and not any(tmp.iterdir()):
                log(f"run {rid}: download failed: {err.strip()[:200]}")
                s["collected"].append(rid)
                continue
            arts = [read_kv(d / "report.txt") | {"_dir": d} for d in tmp.iterdir() if d.is_dir()]
            mains = {(a.get("os"), a.get("kind")): set(lines(a["_dir"] / "failures.txt")) for a in arts if a.get("pr") == "0"}
            n = 0
            for a in arts:
                pr = a.get("pr", "")
                if not pr or pr == "0":
                    continue
                if a.get("status", "").startswith("emulator-"):
                    log(f"run {rid}: #{pr} {a.get('os')}/{a.get('kind')} not counted: {a.get('status')}")
                    continue
                write_report(a, mains.get((a.get("os"), a.get("kind"))))
                s["tested"].setdefault(pr, {})[a.get("job") or f"{a.get('os')}/{a.get('kind')}"] = a.get("head", "")
                mark_pending(pr, f"gha-{a.get('os')}-{a.get('kind')}")
                n += 1
            log(f"run {rid} ({r['conclusion']}): {n} PR reports, mains {sorted(f'{k[0]}/{k[1]}' for k in mains)}")
            s["collected"].append(rid)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    s["collected"] = s["collected"][-200:]


def write_report(a, main_fails):
    d = a["_dir"]
    pr, os_, kind = a["pr"], a.get("os", ""), a.get("kind", "")
    fails = lines(d / "failures.txt")
    status = a.get("status", "")
    if a.get("cpu"):
        r = [f"# #{pr} on Linux under qemu-x86_64 -cpu {a['cpu']} (relinker-only build), GitHub-hosted runner in the fork", f"run: {a.get('url', '')}",
             f"head {a.get('head', '')[:10]} merged into main {a.get('main', '')[:10]} as {a.get('merged', '')}; runner {a.get('runner', '')}; GCC {a.get('gcc', '')}; {a.get('qemu', '')}",
             f"{a.get('emulated', '')}; instruction trap check: {a.get('trap_check', 'missing')}; tests took {a.get('test_sec', '')}s"]
        if a["cpu"] == "Westmere":
            r.append("Westmere has no AVX: a test that executes --to-avx output fails here by design (main: avx2_twin, lane_split, permute, widen_broadcast lowering), so a new AVX lowering's test is expected in the new list")
    else:
        r = [f"# #{pr} on {os_} ({kind} build), GitHub-hosted runner in the fork", f"run: {a.get('url', '')}",
             f"head {a.get('head', '')[:10]} merged into main {a.get('main', '')[:10]} as {a.get('merged', '')}; runner {a.get('runner', '')}; WinLibs GCC {a.get('gcc', '')}"]
    if status != "tested":
        r.append(f"not tested: {status} (stale = PR has newer commits; conflict = does not merge into main; configure-failed = see run; emulator-not-trapping / emulator-not-applied = the QEMU lane could not prove the CPU model)")
    else:
        r.append(f"build rc {a.get('build_rc', '')} in {a.get('build_sec', '')}s, failed targets {a.get('failed_targets', '')}; run 1: {a.get('run1', '')}; run 2: {a.get('run2', '')}")
        if main_fails is None:
            r.append("main baseline from this run missing: failures listed without comparison")
            new, same = fails, []
        else:
            new = [f for f in fails if f not in main_fails]
            same = [f for f in fails if f in main_fails]
        once = []
        if (d / "failures-1.txt").exists() and (d / "failures-2.txt").exists():
            both = set(lines(d / "failures-1.txt")) & set(lines(d / "failures-2.txt"))
            once = [f for f in new if f not in both]
            new = [f for f in new if f in both]
        r.append(f"failing on main too: {' '.join(same) or 'none'}")
        r.append(f"new failures: {' '.join(new) or 'none'}")
        if once:
            r.append(f"failed in one of the two runs only (not counted): {' '.join(once)}")
        for name, label in (("build-errors.txt", "## build errors"), ("failure-output.txt", "## failure output")):
            body = (d / name).read_text(encoding="utf-8", errors="replace").strip() if (d / name).exists() else ""
            if body and (label != "## failure output" or new):
                r += ["", label, "```", body[:6000], "```"]
    dest = OUT / pr
    dest.mkdir(parents=True, exist_ok=True)
    (dest / f"gha-{os_}-{kind}.md").write_text("\n".join(r) + "\n", encoding="utf-8")


def kind_of(s, pr, head):
    k = s["kinds"].get(pr)
    if k and k[0] == head and len(k) > 2:
        return k[1], k[2]
    rc, out, err = run(["gh", "api", "--paginate", f"repos/{UP}/pulls/{pr}/files?per_page=100", "--jq", ".[].filename"], 120)
    if rc:
        raise RuntimeError(f"files of #{pr}: {err.strip()[:200]}")
    paths = [x for x in out.splitlines() if x]
    if not paths or all(DOCS.search(p) for p in paths):
        k = "docs"
    elif all(RELINKER.search(p) for p in paths):
        k = "relinker"
    else:
        k = "full"
    cpu = k != "docs" and any(CPU.search(p) for p in paths)
    s["kinds"][pr] = [head, k, cpu]
    return k, cpu


def stuck(r, hours=12):
    import datetime
    created = datetime.datetime.fromisoformat(r["createdAt"].replace("Z", "+00:00"))
    return (datetime.datetime.now(datetime.timezone.utc) - created).total_seconds() > hours * 3600


def plan(s):
    active = [r for r in runs() if r["status"] != "completed"]
    if sum(1 for r in active if not stuck(r)) >= 2:
        return
    for r in active:
        log(f"ignoring run {r['databaseId']}: still {r['status']} after 12 h (GitHub never started it)")
    rc, out, err = run(["gh", "api", "--paginate", f"repos/{UP}/pulls?state=open&sort=updated&direction=desc&per_page=100",
                        "--jq", ".[] | [.number, .head.sha, .draft] | @tsv"], 600)
    if rc:
        log(f"listing PRs failed: {err.strip()[:200]}")
        return
    main = gh_json(["api", f"repos/{UP}/commits/main", "--jq", "{sha: .sha}"])["sha"]
    queue, used = [], set()
    for line in out.splitlines():
        if len(queue) >= BATCH * 2:
            break
        num, head, draft = line.split("\t")
        if draft == "true":
            continue
        k, cpu = kind_of(s, num, head)
        if k == "docs":
            continue
        todo = [(o, kk) for o, kk in JOBS[k] + ([CPU_JOB] if cpu else []) if s["tested"].get(num, {}).get(f"{o}/{kk}") != head]
        if not todo:
            continue
        if len({e["pr"] for e in queue} | {num}) > BATCH:
            break
        for o, kk in todo:
            queue.append({"pr": num, "head": head, "main": main, "os": o, "kind": kk})
            used.add((o, kk))
    if not queue:
        return
    queue += [{"pr": "0", "head": main, "main": main, "os": o, "kind": kk} for o, kk in sorted(used)]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for e in queue:
        e["batch"] = stamp
    (CLONE / "queue.json").write_text(json.dumps(queue, indent=1) + "\n", encoding="utf-8")
    prs = sorted({e["pr"] for e in queue if e["pr"] != "0"}, key=int)
    run(["git", "add", "queue.json"], cwd=CLONE)
    rc, _, err = run(["git", "-c", "user.name=ycbuzi", "-c", "user.email=user@example.com", "commit", "-q", "-m",
                      f"test: queue {' '.join('#' + p for p in prs)}"], cwd=CLONE)
    rc2, _, err2 = run(["git", "push", "-q", "origin", BRANCH], 300, cwd=CLONE)
    log(f"queued {len(queue)} jobs for {' '.join(prs)} on main {main[:10]}" + (f"; push failed: {err2.strip()[:200]}" if rc2 else ""))


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    s = load()
    try:
        if cmd in ("tick", "collect"):
            collect(s)
        if cmd in ("tick", "plan"):
            plan(s)
    except Exception as e:
        log(f"{cmd} error: {e}")
    finally:
        save(s)
    if cmd == "status":
        print(f"PR heads tested: {sum(len(v) for v in s['tested'].values())} jobs over {len(s['tested'])} PRs; runs collected {len(s['collected'])}")
        for r in runs()[:5]:
            print(r["databaseId"], r["status"], r["conclusion"], r["createdAt"])
        if LOG.exists():
            print("".join(LOG.read_text(encoding="utf-8").splitlines(True)[-5:]))


if __name__ == "__main__":
    main()
