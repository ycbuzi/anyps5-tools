"""Posts the PR test reports that need no judgement (11-11, Blu: "337 PR test reports are waiting on your review ...
get to it"). Offline: gh only, no Claude.

For every PR in the review queue (prreports.py) it reads _reports/pr-review/<PR>/*.md and:
  * PR closed or merged: nothing to post, taken off the queue.
  * reports for an older head than the PR's current one, and reports that could not test (merge conflict with main,
    stale head): ignored. No report left: taken off the queue.
  * a comment of ours already names this head: taken off the queue.
  * every report that tested shows no new failure and no failed build target beyond main's: one comment listing each
    machine's result is posted, then the PR leaves the queue.
  * any new failure, a failed build, or a report it cannot read: left in the queue and listed in
    _reports/pr-review/needs-review.md for a person (or Claude) to look at before anything is posted.

    python prpost.py plan [--limit N]     show what would be posted, post nothing
    python prpost.py post [--limit N]     post up to N comments (default 20), 25 s apart
Scheduled task "AnyPS5 PR posts" runs `post --limit 20` every 30 minutes.
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = "boykopovar/AnyPS5"
ME = "ycbuzi"
OUT = Path(r"C:\AnyPS5 Games\_reports\pr-review")
REVIEW = OUT / "needs-review.md"
LOG = OUT / "prpost.log"
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
sys.path.insert(0, str(Path(__file__).parent))
import prreports  # noqa: E402

GHA = re.compile(r"^gha-(windows-[0-9]+(?:-arm)?)-(full|relinker)\.md$")
OS_NAMES = {"windows-2025": "Windows Server 2025", "windows-2022": "Windows Server 2022", "windows-11-arm": "Windows 11 on ARM64"}


def log(text):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(time.strftime("%m-%d %H:%M:%S ") + text + "\n")


def gh(args, seconds=120):
    r = subprocess.run(["gh"] + args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=seconds, creationflags=NOWIN)
    if r.returncode:
        raise RuntimeError(r.stderr.strip()[:300])
    return r.stdout


def field(text, pattern):
    m = re.search(pattern, text, re.M)
    return m.group(1).strip() if m else ""


def read_report(path):
    text = path.read_text(encoding="utf-8", errors="replace")
    r = {"file": path.name, "head": field(text, r"head ([0-9a-f]{7,40})"), "text": text}
    if text.lstrip("\ufeff").startswith("# #") and "\nnot tested:" in text:
        r["state"] = "untested"
        r["why"] = field(text, r"^not tested: (\w+)")
        return r
    new = field(text, r"^new failures: (.*)$")
    r["new"] = new
    gha = GHA.match(path.name)
    if gha:
        os_, kind = gha.groups()
        build = field(text, r"^build rc (\d+)")
        targets = field(text, r"failed targets (\d+)")
        r["build_ok"] = targets in ("", "0") or build == "0"
        r["runs"] = "; ".join(x for x in (field(text, r"run 1: ([^;]*)"), field(text, r"run 2: (.*)$")) if x)
        r["where"] = f"{OS_NAMES.get(os_, os_)} (GitHub-hosted runner), {'full' if kind == 'full' else 'relinker-only'} build, WinLibs GCC {field(text, r'WinLibs GCC ([0-9.]+)')}"
        r["link"] = field(text, r"^run: (\S+)")
        r["main_too"] = field(text, r"^failing on main too: (.*)$")
    else:
        line = field(text, r"^head .*$") if False else text.splitlines()[1] if len(text.splitlines()) > 1 else ""
        mine = re.search(r"failed build targets (\d+) \(main (\d+)", line)
        r["build_ok"] = not mine or int(mine.group(1)) <= int(mine.group(2))
        if re.search(r"build rc [1-9]", line) and not mine:
            r["build_ok"] = False
        draft = text.split("## Draft comment", 1)[1].strip().splitlines() if "## Draft comment" in text else []
        r["where"] = re.sub(r"^Tested on ", "", draft[0]).split("; PR head")[0].rstrip(".") if draft else path.stem
        r["runs"] = "; ".join(re.findall(r"run \d: [^;]*?out of \d+", line))
        r["link"] = ""
        r["main_too"] = field(text, r"^failing on main too: (.*)$")
    r["state"] = "clean" if new == "none" and r["build_ok"] else ("unreadable" if not new else "attention")
    # 11-11 audit: "no new failures" only means something if the suite ran: a report without a test-run line is held.
    if r["state"] == "clean" and "tests passed" not in text:
        r["state"] = "unreadable"
    return r


GPU_PATHS = re.compile(r"libSceAgc|ShaderRecompiler|libSceVideoOut|libSceGnm|Vulkan|shader", re.I)
GPU_NOTE = ("**Not covered: GPU.** This pull request changes GPU code, and the GitHub-hosted runners above have no GPU, so "
            "the Vulkan and GPU tests fail there on main and on this pull request alike. This result says nothing about the "
            "changed GPU paths; they still need a run on a real GPU.")


def touches_gpu(pr):
    files = gh(["api", "--paginate", f"repos/{REPO}/pulls/{pr}/files?per_page=100", "--jq", ".[].filename"])
    return any(GPU_PATHS.search(f) for f in files.splitlines())


def our_comments(pr):
    out = gh(["api", "--paginate", f"repos/{REPO}/issues/{pr}/comments", "--jq", f'.[] | select(.user.login=="{ME}") | .body'])
    return out


def compose(pr, head, reports, gpu=False):
    lines = [f"<!-- anyps5-test-report {head[:10]} -->",
             f"Tested `{head[:10]}` merged into main, with the whole ctest suite on each machine below. Nothing fails that does not also fail on main on the same machine.", ""]
    for r in reports:
        names = [w for w in (r["main_too"] or "").split() if w != "none" and not w.startswith("(")]
        extra = f" {len(names)} of the failing tests fail on main too." if names else ""
        link = f" [run]({r['link']})" if r["link"] else ""
        lines.append(f"- {r['where']}: {r['runs'] or 'tests ran'}.{extra}{link}")
    if gpu and any(r["file"].startswith("gha-") for r in reports):
        lines += ["", GPU_NOTE]
    lines += ["", "AI-assisted: yes (Claude Code); the runs are automated, this summary was written from their reports."]
    return "\n".join(lines) + "\n"


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "plan"
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 20
    if cmd != "post":
        prreports.save = lambda state: None
    s = prreports.load()
    pending = sorted(s["pending"], key=int, reverse=True)
    review, posted, dropped, looked = [], 0, 0, 0
    for pr in pending:
        if posted >= limit:
            break
        folder = OUT / pr
        files = sorted(folder.glob("*.md")) if folder.is_dir() else []
        try:
            state, head = gh(["api", f"repos/{REPO}/pulls/{pr}", "--jq", '.state + " " + (.merged|tostring) + " " + .head.sha']).split()[0::2]
        except Exception as e:
            log(f"#{pr}: lookup failed: {e}")
            continue
        looked += 1
        if state != "open":
            s["pending"].pop(pr, None); dropped += 1; log(f"#{pr}: {state}, nothing posted")
            continue
        reports = [read_report(f) for f in files]
        current = [r for r in reports if r["head"] and head.startswith(r["head"])]
        tested = [r for r in current if r["state"] != "untested"]
        if not tested:
            s["pending"].pop(pr, None); dropped += 1
            log(f"#{pr}: no report for head {head[:10]} that tested ({', '.join(sorted({r.get('why', 'older head') for r in reports if r not in tested})) or 'none'})")
            continue
        try:
            mine = our_comments(pr)
        except Exception as e:
            log(f"#{pr}: comment lookup failed: {e}")
            continue
        # Blu 11-11: "make sure that we don't post any PRs that already been posted": one test report of ours per PR,
        # whatever head it named (the machine runners' and the earlier hand-posted ones included).
        if head[:10] in mine or re.search(r"anyps5-test-report|^Tested (on |`)", mine, re.M):
            s["pending"].pop(pr, None); dropped += 1; log(f"#{pr}: we already posted a test report on it")
            continue
        bad = [r for r in tested if r["state"] != "clean"]
        if bad:
            review.append((pr, head, bad))
            continue
        try:
            gpu = touches_gpu(pr)
        except Exception as e:
            log(f"#{pr}: file list failed: {e}")
            continue
        # Blu 11-11: "we don't have to post until all the test is completely green". A PR that changes GPU code waits
        # for a clean GPU run on the PC (gpu-<os>.md from gpu_prtest.ps1, queued in gpu-queue.json) before anything is posted.
        gpu_reports = [r for r in current if r["file"].startswith("gpu-")]
        if gpu and not gpu_reports:
            queue_file = OUT / "gpu-queue.json"
            try:
                waiting = json.loads(queue_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                waiting = {}
            if waiting.get(pr) != head:
                waiting[pr] = head
                if cmd == "post":
                    queue_file.write_text(json.dumps(waiting, indent=1), encoding="utf-8")
                log(f"#{pr}: changes GPU code; waiting for a GPU run on the PC before posting")
            continue
        if gpu and any(r["state"] != "clean" for r in gpu_reports):
            review.append((pr, head, [r for r in gpu_reports if r["state"] != "clean"]))
            continue
        body = compose(pr, head, tested, gpu and not gpu_reports)
        if cmd == "post":
            tmp = OUT / f".post-{pr}.md"
            tmp.write_text(body, encoding="utf-8")
            try:
                url = gh(["pr", "comment", pr, "-R", REPO, "-F", str(tmp)]).strip()
                log(f"#{pr}: posted {url} ({len(tested)} machine reports)")
                s["pending"].pop(pr, None); posted += 1
            except Exception as e:
                log(f"#{pr}: post failed: {e}")
            finally:
                tmp.unlink(missing_ok=True)
            prreports.save(s)
            time.sleep(25)
        else:
            posted += 1
            print(f"=== would post on #{pr}\n{body}")
    prreports.save(s)
    old = REVIEW.read_text(encoding="utf-8") if REVIEW.exists() else ""
    rows = [f"- #{pr} head {head[:10]}: " + "; ".join(f"{r['file']}: new {r.get('new') or '?'}{'' if r.get('build_ok', True) else ', build failed'}" for r in bad) for pr, head, bad in review]
    keep = [l for l in old.splitlines() if l.startswith("- #") and not any(l.startswith(f"- #{pr} ") for pr, _, _ in review)]
    REVIEW.write_text("# PR reports that need a look before posting\n\n" + "\n".join(sorted(set(keep + rows), key=lambda l: -int(re.match(r"- #(\d+)", l).group(1)))) + "\n", encoding="utf-8")
    summary = f"{cmd}: looked at {looked}, {'posted' if cmd == 'post' else 'would post'} {posted}, off the queue without a post {dropped}, need a look {len(review)}; {len(s['pending'])} still queued"
    log(summary)
    print(summary)


if __name__ == "__main__":
    main()
