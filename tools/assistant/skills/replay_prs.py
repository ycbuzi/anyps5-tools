"""Replays a saved shader request (shader_*.prepare.req) against open PRs without the game: the same input every time, so the answer does not vary between runs. Pushes nothing.
usage: replay_prs.py <request file> <base ref> <pr or a+b> [...]"""
import os
import re
import subprocess
import sys
import time
from pathlib import Path

WORK = Path(r"C:\AnyPS5-work")
SRC = WORK / "prs"
OUT = WORK / "build-prs"
TOOL = OUT / "core" / "libs" / "prx" / "libSceAgcDriver" / "agc_shader_replay.exe"
ENV = dict(os.environ, GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="core.fsmonitor", GIT_CONFIG_VALUE_0="false", PATH=str(OUT / "core" / "libs" / "libs") + os.pathsep + str(WORK / "mingw64" / "bin") + os.pathsep + os.environ["PATH"])
request, base = sys.argv[1], sys.argv[2]
sets = sys.argv[3:]
REPORT = Path(r"D:\AnyPS5 Games\_reports") / f"replay-prs-{time.strftime('%m%d-%H%M')}.md"


def note(text):
    with REPORT.open("a", encoding="utf-8") as f:
        f.write(text + "\n")
    print(text[:700], flush=True)


def git(*args):
    return subprocess.run(["git", "-C", str(SRC), "-c", "user.name=test", "-c", "user.email=user@example.com", *args], capture_output=True, text=True, errors="replace", env=ENV)


def replay():
    b = subprocess.run([str(WORK / "mingw64" / "bin" / "ninja.exe"), "-C", str(OUT), "-j", "8", "agc_shader_replay"], capture_output=True, text=True, errors="replace", env=ENV)
    if b.returncode:
        return "did not build: " + " | ".join(re.findall(r"(?m)^.*(?:error:|FAILED:|CMake Error).*$", b.stdout)[:3])[:400]
    r = subprocess.run([str(TOOL), request], capture_output=True, text=True, errors="replace", env=ENV, timeout=600, cwd=str(WORK))
    text = (r.stdout + r.stderr).strip().splitlines()
    tail = [l.strip() for l in text[2:]] or ["(no output)"]
    return f"exit {r.returncode}: " + " / ".join(tail)[:560]


note(f"# Replay of {Path(request).name}, base {base}\n")
git("checkout", "-q", "-f", "--detach", base)
note(f"| base | {replay()} |")
for group in sets:
    git("merge", "--abort")
    git("checkout", "-q", "-f", "--detach", base)
    numbers = group.split("+")
    failed = False
    for number in numbers:
        git("fetch", "-q", "origin", f"+refs/pull/{number}/head:refs/remotes/origin/pr/{number}")
        m = git("merge", "-q", "--no-edit", f"origin/pr/{number}")
        if m.returncode:
            note(f"| {group} | #{number} conflicts in {git('diff', '--name-only', '--diff-filter=U').stdout.split()[:3]} |")
            failed = True
            break
    if not failed:
        note(f"| {group} | {replay()} |")
git("merge", "--abort")
git("checkout", "-q", "-f", "--detach", base)
note("\nDONE")
