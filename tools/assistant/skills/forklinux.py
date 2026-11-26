"""Focused Linux test run of a PR on a free GitHub runner (fork workflow fork-linux.yml, 11-16). Offline: gh + git, no Claude.

    python forklinux.py <pr> --targets "t1_tests t2_tests" --tests "^(t1|t2)" [--branch fork-branch] [--repeat 3] [--no-main] [--no-wait]

Queues up to three jobs, each merged onto the current upstream main and run --repeat times:
  the PR head (or --branch, a branch pushed to the fork, for a fix not yet on the PR), the PR's own head as the
  negative control when --branch is given, and main itself (unless --no-main).
Waits for the run and prints one summary line per job plus each test's result from the first run.
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

UP = "boykopovar/AnyPS5"
FORK = "ycbuzi/AnyPS5"
CLONE = Path(r"C:\AnyPS5-work\forkci")
WORKFLOW = "fork-linux.yml"


def sh(args, cwd=None, check=True):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if check and r.returncode:
        sys.exit(f"{' '.join(args[:3])}... failed: {r.stderr.strip()[:400]}")
    return r.stdout.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pr")
    ap.add_argument("--targets", required=True)
    ap.add_argument("--tests", required=True)
    ap.add_argument("--branch", default="")
    ap.add_argument("--repeat", default="3")
    ap.add_argument("--no-main", action="store_true")
    ap.add_argument("--no-wait", action="store_true")
    a = ap.parse_args()

    main_sha = sh(["gh", "api", f"repos/{UP}/commits/main", "--jq", ".sha"])
    pr_head = sh(["gh", "pr", "view", a.pr, "-R", UP, "--json", "headRefOid", "--jq", ".headRefOid"])
    base = {"main": main_sha, "targets": a.targets, "tests": a.tests, "repeat": a.repeat}
    q = []
    if a.branch:
        fix = sh(["gh", "api", f"repos/{FORK}/commits/{a.branch}", "--jq", ".sha"])
        q.append(dict(base, pr=a.pr, head=fix, branch=a.branch))
    q.append(dict(base, pr=a.pr, head=pr_head))
    if not a.no_main:
        q.append(dict(base, pr="0", head=""))
    for e in q:
        e["batch"] = main_sha[:10] + "-" + (e.get("head") or "main")[:10]

    sh(["git", "pull", "-q", "--rebase", "origin", "prtest-runner"], cwd=CLONE)
    (CLONE / "linux-queue.json").write_text(json.dumps(q, indent=1) + "\n", encoding="utf-8")
    sh(["git", "add", "linux-queue.json"], cwd=CLONE)
    sh(["git", "commit", "-q", "-m", f"ci: focused Linux run for #{a.pr}"], cwd=CLONE)
    before = sh(["gh", "run", "list", "-R", FORK, "--workflow", WORKFLOW, "--limit", "1", "--json", "databaseId", "--jq", ".[0].databaseId // 0"])
    sh(["git", "push", "-q", "origin", "prtest-runner"], cwd=CLONE)
    run = before
    for _ in range(30):
        run = sh(["gh", "run", "list", "-R", FORK, "--workflow", WORKFLOW, "--limit", "1", "--json", "databaseId", "--jq", ".[0].databaseId // 0"])
        if run != before:
            break
        subprocess.run(["powershell", "-NoProfile", "-Command", "Start-Sleep 5"])
    print(f"run https://github.com/{FORK}/actions/runs/{run}")
    if a.no_wait:
        return
    subprocess.run(["gh", "run", "watch", run, "-R", FORK], capture_output=True)
    out = Path(tempfile.mkdtemp(prefix=f"forklinux-{a.pr}-", dir=r"C:\AnyPS5-work"))
    sh(["gh", "run", "download", run, "-R", FORK, "-D", str(out)], check=False)
    for d in sorted(out.iterdir()):
        rep = dict(l.split("=", 1) for l in (d / "report.txt").read_text(encoding="utf-8").splitlines() if "=" in l) if (d / "report.txt").exists() else {}
        who = "main" if rep.get("pr") == "0" else f"#{rep.get('pr')} {rep.get('head', '')[:10]}"
        runs = [v for k, v in sorted(rep.items()) if k.startswith("ctest-")]
        print(f"{who}: status={rep.get('status')} build_rc={rep.get('build_rc')} | " + " ; ".join(runs))
        log = d / "ctest-1.log"
        if log.exists():
            for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
                if " Test " in line and ("Passed" in line or "***" in line or "Failed" in line):
                    print("   " + " ".join(line.split())[:140])
    print(f"artifacts: {out}")


if __name__ == "__main__":
    main()
