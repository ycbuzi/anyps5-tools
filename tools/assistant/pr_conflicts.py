import json
import re
import subprocess
import sys

REPO = "boykopovar/AnyPS5"
GIT = ["git", "-C", r"D:\AnyPS5"]


def run(cmd, timeout=300):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return r.returncode, r.stdout


def files_of(ref, base):
    _, out = run(GIT + ["diff", "--name-only", f"{base}...{ref}"])
    return {l.strip() for l in out.splitlines() if l.strip()}


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: pr_conflicts.py <branch or commit> [own PR number]")
    ref = sys.argv[1]
    own = sys.argv[2] if len(sys.argv) > 2 else ""
    run(GIT + ["fetch", "-q", "origin", "main"])
    base = "origin/main"
    mine = files_of(ref, base)
    if not mine:
        sys.exit(f"{ref} has no changes against {base}")
    code, out = run(["gh", "pr", "list", "-R", REPO, "--state", "open", "--limit", "5000", "--json", "number,title,author,headRefOid,files"])
    prs = json.loads(out or "[]")
    hits = []
    for pr in prs:
        if str(pr["number"]) == own:
            continue
        theirs = {f["path"] for f in pr.get("files") or []}
        shared = sorted(mine & theirs)
        if not shared:
            continue
        sha = pr["headRefOid"]
        run(GIT + ["fetch", "-q", "origin", f"pull/{pr['number']}/head"], timeout=600)
        code, mt = run(GIT + ["merge-tree", "--write-tree", "--name-only", "--no-messages", ref, sha], timeout=600)
        conflicted = sorted({l.strip() for l in mt.splitlines()[1:] if l.strip()} & set(shared)) if code == 1 else []
        hits.append((pr["number"], pr["author"]["login"], pr["title"][:80], shared, conflicted))
    title_words = None
    print(f"# Overlap check for {ref} against {len(prs)} open PRs ({len(mine)} files changed)\n")
    real = [h for h in hits if h[4]]
    print(f"## Text conflicts ({len(real)}): resolve or note in Depends on before pushing/opening")
    for n, who, t, shared, conf in real:
        print(f"- #{n} {who}: {t} | conflicting: {', '.join(conf)}")
    print(f"\n## Same files, merges cleanly ({len(hits) - len(real)}): check none does the same thing")
    for n, who, t, shared, conf in hits:
        if not conf:
            print(f"- #{n} {who}: {t} | shared: {', '.join(shared[:4])}{' ...' if len(shared) > 4 else ''}")
    sys.exit(2 if real else 0)


if __name__ == "__main__":
    main()
