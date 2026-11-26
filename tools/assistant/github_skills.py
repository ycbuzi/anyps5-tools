"""GitHub skills for the AnyPS5 Assistant: the steps Claude takes on Blu's own pull requests, as tools a local model can
run one by one. Everything goes through Blu's GitHub login (gh / git on this PC): never ask Blu to run a command.

  sync_pr_branch(n)        merge current main into the PR's branch (never rebase); conflicts come back with the hunks
  finish_merge(n)          after the conflicts are edited: check no markers are left, commit the merge
  push_pr_branch(n)        push the branch to Blu's fork (no force) and confirm GitHub has the new head
  pr_test_report(n)        a PR comment drafted from the real Windows test report (never from memory)
  post_pr_comment(n, text) post it (Blu sees it first) and confirm it exists on GitHub; returns the real link

Report format (Blu's): address the reviewer by @name if they asked for something; say exactly what was tested
(head commit, main commit it was merged onto, which tests, how many times, which GPUs), the result in numbers, the
negative control (the same tests on main without the change), what was NOT tested; real line breaks, no "\\n";
end with "AI-assisted: yes (local model)".
"""
import json
import re
import subprocess
from pathlib import Path

REPO = "boykopovar/AnyPS5"
ME = "ycbuzi"
SRC = Path(r"D:\AnyPS5")
REPORTS = SRC / "scratchpad" / "helpers" / "reports"
WORK = Path(__file__).resolve().parent / "workspace"


def run(args, cwd=None, timeout=900):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).strip()


def git(args, cwd):
    return run(["git"] + args, cwd=str(cwd))


def pr_meta(number):
    code, out = run(["gh", "pr", "view", str(int(number)), "--repo", REPO, "--json",
                     "number,title,author,headRefName,headRefOid,state,mergeable,reviewDecision,reviews"])
    if code:
        raise RuntimeError("GitHub did not answer: " + out[:300])
    return json.loads(out)


def worktree_for(branch):
    """The worktree that has the branch checked out, or a new one D:\\AnyPS5\\wt-pr-<branch>."""
    code, out = git(["worktree", "list", "--porcelain"], SRC)
    path = None
    for block in out.split("\n\n"):
        lines = dict(l.split(" ", 1) for l in block.splitlines() if " " in l)
        if lines.get("branch") == f"refs/heads/{branch}":
            path = Path(lines["worktree"])
    if path:
        return path, False
    path = SRC / ("wt-pr-" + re.sub(r"[^A-Za-z0-9._-]+", "-", branch)[:50])
    git(["fetch", "-q", "fork", branch], SRC)
    code, out = git(["worktree", "add", "-q", "-B", branch, str(path), f"fork/{branch}"], SRC)
    if code:
        raise RuntimeError("could not make a worktree: " + out[:300])
    return path, True


def conflict_hunks(wt, files, limit=3500):
    text = []
    for f in files:
        try:
            body = (Path(wt) / f).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in re.finditer(r"<<<<<<< .*?\n(.*?)=======\n(.*?)>>>>>>> [^\n]*", body, re.S):
            text.append(f"--- {f}\nOURS (the PR):\n{m.group(1)[:900]}THEIRS (main):\n{m.group(2)[:900]}")
        code, why = git(["log", "--oneline", "-3", "origin/main", "--", f], wt)
        text.append(f"main's latest changes to {f}:\n{why}")
    out = "\n".join(text)
    return out[:limit] + ("\n...(more)" if len(out) > limit else "")


def sync_pr_branch(number):
    meta = pr_meta(number)
    if meta["author"]["login"] != ME:
        return f"#{number} is {meta['author']['login']}'s PR, not Blu's: only its author can update it. To test it, use run_pr_on_windows."
    branch = meta["headRefName"]
    wt, new = worktree_for(branch)
    code, dirty = git(["status", "--porcelain", "--untracked-files=no", "--ignore-submodules"], wt)
    if dirty and "<<<<<<<" not in dirty:
        unmerged = [l[3:] for l in dirty.splitlines() if l[:2] in ("UU", "AA", "DU", "UD")]
        if unmerged:
            return f"A merge is already in progress in {wt}. Conflicts left: {', '.join(unmerged)}.\n" + conflict_hunks(wt, unmerged)
    git(["fetch", "-q", "origin", "main"], wt)
    git(["fetch", "-q", "fork", branch], wt)
    code, local = git(["rev-parse", "HEAD"], wt)
    code, remote = git(["rev-parse", f"fork/{branch}"], wt)
    if local != remote:
        code, _ = git(["merge-base", "--is-ancestor", "HEAD", f"fork/{branch}"], wt)
        if code == 0:
            git(["merge", "--ff-only", f"fork/{branch}"], wt)
    code, behind = git(["rev-list", "--count", "HEAD..origin/main"], wt)
    if behind.strip() == "0":
        return f"#{number} ({branch}) in {wt} is already up to date with main (head {local[:10]}). Nothing to merge."
    code, out = git(["merge", "--no-edit", "origin/main"], wt)
    git(["submodule", "update", "--init", "--recursive", "-q"], wt)
    code2, head = git(["log", "--format=%h", "-1"], wt)
    code3, main = git(["log", "--format=%h", "-1", "origin/main"], wt)
    if code == 0:
        return (f"Merged main {main} into #{number} ({branch}) in {wt}: clean, new head {head} (was {behind.strip()} behind). "
                f"Next: run the PR's focused tests on this head (a retest script in D:\\AnyPS5\\scratchpad\\helpers\\retest_{number}.ps1 if there is one, "
                f"else run_pr_on_windows {number}), then push_pr_branch {number}, then pr_test_report / post_pr_comment.")
    code, st = git(["diff", "--name-only", "--diff-filter=U"], wt)
    files = [f for f in st.splitlines() if f.strip()]
    return (f"Merging main {main} into #{number} ({branch}) in {wt} stopped at conflicts in: {', '.join(files)}.\n"
            "How to resolve (what Claude does): read each hunk. Lists in docs/dev/debt/*.md: keep the PR's new lines and main's lines; "
            "drop a line only when main deleted it on purpose (main's log below tells why, e.g. a PR that implemented it). "
            "Code: keep both changes and make them fit; if the two sides change the same lines in different ways, ask Blu. "
            "Edit the file with replace_in_file (remove the <<<<<<< ======= >>>>>>> lines), then finish_merge.\n\n" + conflict_hunks(wt, files))


def finish_merge(number):
    meta = pr_meta(number)
    wt, _ = worktree_for(meta["headRefName"])
    code, st = git(["diff", "--name-only", "--diff-filter=U"], wt)
    left = []
    for f in [x for x in st.splitlines() if x.strip()]:
        body = (Path(wt) / f).read_text(encoding="utf-8", errors="replace")
        if re.search(r"^(<<<<<<<|=======|>>>>>>>)", body, re.M):
            left.append(f)
    if left:
        return "Conflict markers are still in: " + ", ".join(left) + ". Edit them out first."
    git(["add", "-A"], wt)
    code, out = git(["-c", "core.editor=true", "commit", "--no-edit", "-q"], wt)
    if code and "nothing to commit" not in out:
        return "commit failed: " + out[:400]
    code, head = git(["log", "--format=%h %s", "-1"], wt)
    return f"Merge committed in {wt}: {head}. Next: build and run the PR's focused tests on this head, then push_pr_branch {number}."


def push_pr_branch(number):
    meta = pr_meta(number)
    if meta["author"]["login"] != ME:
        return "not Blu's PR"
    branch = meta["headRefName"]
    wt, _ = worktree_for(branch)
    code, head = git(["rev-parse", "HEAD"], wt)
    code, out = git(["push", "fork", f"HEAD:{branch}"], wt)  # never --force
    if code:
        return "push failed (nothing was forced): " + out[-600:]
    after = pr_meta(number)["headRefOid"]
    ok = after == head.strip()
    return (f"Pushed {head[:10]} to ycbuzi/{branch}; GitHub's PR head is now {after[:10]} " + ("(confirmed)." if ok else "(NOT the same yet: check again in a minute)."))


def pr_test_report(number):
    """A comment draft from the Windows test helper's real report (reports\\pr-<n>.md)."""
    rep = REPORTS / f"pr-{int(number)}.md"
    if not rep.exists():
        return f"No test report for #{number}: run run_pr_on_windows {number} first. Do not post results that do not exist."
    text = rep.read_text(encoding="utf-8", errors="replace")
    ctest = re.findall(r"^ctest \((.+?)\) exit (\d+): (.+)$", text, re.M)
    gpu_runs = [c for c in ctest if c[0] != "control"]
    if not gpu_runs:
        reason = next((l for l in text.splitlines() if re.search(r"conflict|failed|error", l, re.I)), "the test did not run")
        return (f"NOT TESTED: the report for #{number} shows no test results ({reason.strip()}). Do not post a result. "
                + ("If it is Blu's PR, sync_pr_branch it first and test again." if "conflict" in text.lower() else ""))
    head = re.search(r"head (\w+) merged onto main (\w+)", text)
    tests_line = next((l for l in text.splitlines() if l.startswith("tests (")), "")
    control = [c for c in ctest if c[0] == "control"]
    fails = re.findall(r"^  (\S+): (Exception|Failed|Timeout|SEGFAULT|Subprocess aborted)", text, re.M)
    codes = sorted(set(re.findall(r"0x[cC]0000\w{3}", text)))
    meta = pr_meta(number)
    asked = [r["author"]["login"] for r in meta.get("reviews") or [] if r.get("state") == "CHANGES_REQUESTED"]
    names = tests_line.rsplit("): ", 1)[1].split() if "): " in tests_line else []
    lines = [(f"@{asked[-1]} " if asked else "")
             + (f"Tested on Windows 11: head `{head.group(1)}` merged onto `main` `{head.group(2)}`." if head else "Tested on Windows 11.")]
    lines.append(f"The {len(names)} tests ({', '.join(f'`{n}`' for n in names[:8])}{' ...' if len(names) > 8 else ''}):")
    for gpu, code, result in gpu_runs:
        lines.append(f"- {gpu}: {result}")
    if control:
        lines.append(f"Negative control (the same tests with the change taken out, on `main`): {control[0][2]}"
                     + (f", {', '.join('`' + c + '`' for c in codes)}" if codes and fails else "") + ".")
    lines.append("Not tested: Linux, macOS.")
    lines.append("")
    lines.append("AI-assisted: yes (local model)")
    draft = "\n".join(lines)
    return ("DRAFT (from " + str(rep) + "; check it, then post it with post_pr_comment exactly as it is, or improve wording "
            "without changing any fact):\n\n" + draft)


def already_posted(number):
    """The link of Blu's newest comment on the PR made after its latest commit ("" if none): a report is already there."""
    code, out = run(["gh", "api", f"repos/{REPO}/pulls/{int(number)}/commits", "--paginate", "--jq", ".[-1].commit.committer.date"])
    last = out.strip().splitlines()[-1] if code == 0 and out.strip() else ""
    code, out = run(["gh", "api", f"repos/{REPO}/issues/{int(number)}/comments", "--paginate", "--jq",
                     f'.[] | select(.user.login == "{ME}") | .created_at + " " + .html_url'])
    if code or not last:
        return ""
    newer = [l.split(" ", 1)[1] for l in out.strip().splitlines() if " " in l and l.split(" ", 1)[0] > last]
    return newer[-1] if newer else ""


def post_pr_comment(number, text, approve=None):
    body = text.replace("\\n", "\n").strip()
    if "AI-assisted:" not in body:
        body += "\n\nAI-assisted: yes (local model)"
    if approve and not approve(f"post this comment on #{number}:\n\n{body}"):
        return "Blu declined: nothing was posted."
    WORK.mkdir(parents=True, exist_ok=True)
    f = WORK / f"comment-{int(number)}.md"
    f.write_text(body, encoding="utf-8")
    code, out = run(["gh", "api", f"repos/{REPO}/issues/{int(number)}/comments", "-F", f"body=@{f}", "--jq", ".html_url"])
    if code or "github.com" not in out:
        return "NOT POSTED (GitHub said: " + out[:300] + "). Tell Blu it failed; do not say it was posted."
    url = out.strip().splitlines()[-1]
    cid = url.rsplit("-", 1)[-1]
    code, check = run(["gh", "api", f"repos/{REPO}/issues/comments/{cid}", "--jq", ".html_url"])
    return f"Posted and confirmed: {url}" if code == 0 else f"Posted ({url}) but could not confirm it yet."
