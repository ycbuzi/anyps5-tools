"""Tries open PRs one at a time on a test copy: merges each onto a base branch in the C: worktree, builds one library, runs the game, and reports which stop it reached. Pushes nothing.
usage: try_prs.py <game> <base ref> <library> <minutes> <env or -> <pr> [<pr> ...]"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import assistant as a

a.AUTO["on"] = True
a.say = lambda *x, **k: None
WORK = Path(r"C:\AnyPS5-work")
SRC = WORK / "prs"
OUT = WORK / "build-prs"
NINJA = WORK / "mingw64" / "bin" / "ninja.exe"
ENV = dict(os.environ, GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="core.fsmonitor", GIT_CONFIG_VALUE_0="false", PATH=str(WORK / "mingw64" / "bin") + os.pathsep + os.environ["PATH"])
game, base, library, minutes, env = sys.argv[1:6]
env = "" if env == "-" else env
prs = sys.argv[6:]
FOLDER = a._test_folder(game)
REPORT = a.GAMES / "_reports" / f"try-prs-{time.strftime('%m%d-%H%M')}.md"


def note(text):
    with REPORT.open("a", encoding="utf-8") as f:
        f.write(text + "\n")
    print(text[:500], flush=True)


def git(*args, timeout=600):
    return subprocess.run(["git", "-C", str(SRC), *args], capture_output=True, text=True, errors="replace", env=ENV, timeout=timeout)


def build():
    r = subprocess.run([str(NINJA), "-C", str(OUT), "-j", "8", f"core/libs/libs/{library}.prx"], capture_output=True, text=True, errors="replace", env=ENV)
    if r.returncode == 0:
        return ""
    return " | ".join(re.findall(r"(?m)^.*(?:error:|FAILED:|CMake Error).*$", r.stdout)[:3])[:400] or r.stdout[-300:]


def run(label, lib):
    while a.game_running():
        time.sleep(60)
    n = len(a.JOBS) + 1
    a.test_run(str(FOLDER), minutes, "1", "every_button", label, lib, env)
    a.JOBS[n]["proc"].wait()
    for line in a.JOBS[n]["log"].read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith('{"run"'):
            r = json.loads(line)
            return f"{r.get('end')} Frames {r.get('frames')}. {r.get('said') or r.get('kind')}"[:360]
    return "no result"


note(f"# PRs on {FOLDER.name}, base {base}, library {library}, {minutes} min each\n")
git("checkout", "-q", "-f", "--detach", base)
broken = build()
note(f"| PR | Result |\n| --- | --- |\n| base | {'did not build: ' + broken if broken else run('base for PR tries', str(OUT / 'core/libs/libs' / (library + '.prx')))} |")
for number in prs:
    git("merge", "--abort")
    git("checkout", "-q", "-f", "--detach", base)
    if git("fetch", "-q", "origin", f"+refs/pull/{number}/head:refs/remotes/origin/pr/{number}").returncode:
        note(f"| #{number} | could not fetch |")
        continue
    merged = git("-c", "user.name=test", "-c", "user.email=user@example.com", "merge", "-q", "--no-edit", f"origin/pr/{number}")
    if merged.returncode:
        files = git("diff", "--name-only", "--diff-filter=U").stdout.split()
        note(f"| #{number} | conflicts with the base in {', '.join(files[:3])} |")
        continue
    broken = build()
    note(f"| #{number} | {'did not build: ' + broken if broken else run(f'PR {number} try', str(OUT / 'core/libs/libs' / (library + '.prx')))} |")
git("merge", "--abort")
git("checkout", "-q", "-f", "--detach", base)
note("\nOne run each. If this game's stop varies between runs, this table proves nothing by itself: repeat the base and any PR that looks better at least three times.")
note("\nDONE")
