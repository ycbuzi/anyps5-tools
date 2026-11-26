r"""Looks up how KytyPS5 implements a PS5 system function or handles a GPU message, for AnyPS5 fixes to learn from.

usage: python -I kyty_ref.py <sceFunction or words> [...] [--update] [--json]

KytyPS5 (GPL-2.0, like AnyPS5) is listed in AnyPS5's docs/dev/REVERSE_ENGINEERING.md as a reference implementation
("Other implementations and SDK bindings"). This reads a local shallow clone in C:\AnyPS5-work\refs\KytyPS5; --update
refreshes it (at most once a day). KytyPS5 names functions without the "sce" prefix and registers them by NID with
LIB_FUNC("<nid>", Lib::Name). For each term it prints the file and line of the definition, the NID registration and
the start of the body. A fix written from it says so in the PR (source 3 in REVERSE_ENGINEERING.md) and is written for
AnyPS5, not copied.
"""
import json
import os
import re
import subprocess
import sys
import time

CLONE = r"C:\AnyPS5-work\refs\KytyPS5"
URL = "https://github.com/KytyPS5/KytyPS5.git"


def git(*args):
    return subprocess.run(["git", "-C", CLONE, *args], capture_output=True, text=True, encoding="utf-8", errors="replace")


def update():
    stamp = os.path.join(CLONE, ".git", "FETCH_HEAD")
    if os.path.exists(stamp) and time.time() - os.path.getmtime(stamp) < 86400:
        return
    if not os.path.isdir(CLONE):
        os.makedirs(os.path.dirname(CLONE), exist_ok=True)
        subprocess.run(["git", "clone", "-q", "--depth", "1", URL, CLONE], check=False)
        return
    if git("fetch", "-q", "--depth", "1", "origin", "main").returncode == 0:
        git("reset", "-q", "--hard", "FETCH_HEAD")


def body(path, line, lines=40):
    with open(os.path.join(CLONE, path), encoding="utf-8", errors="replace") as f:
        text = f.read().splitlines()
    out, depth, opened = [], 0, False
    for l in text[line - 1:line - 1 + 200]:
        out.append(l)
        depth += l.count("{") - l.count("}")
        opened = opened or "{" in l
        if (opened and depth <= 0) or len(out) >= lines:
            break
    return "\n".join(out)


def lookup(term):
    name = re.sub(r"^sce", "", term) if re.match(r"^sce[A-Z]", term) else term
    hits = {"term": term, "definitions": [], "registrations": [], "mentions": []}
    if re.fullmatch(r"\w+", name) and not re.fullmatch(r"[A-Z0-9_]+", name):
        r = git("grep", "-n", "-I", "-P", rf"\b(int|void|uint\w*|int\w*|bool|auto|\w+\*?)\s+(KYTY_SYSV_ABI\s+)?{name}\s*\(", "--", "src")
        for l in r.stdout.splitlines()[:3]:
            path, line, _ = l.split(":", 2)
            hits["definitions"].append({"where": f"{path}:{line}", "code": body(path, int(line))})
        r = git("grep", "-n", "-I", "-P", rf"LIB_FUNC\(\"[^\"]+\",\s*[\w:]*\b{name}\)", "--", "src")
        hits["registrations"] = [l.strip()[:200] for l in r.stdout.splitlines()[:3]]
    else:
        r = git("grep", "-n", "-I", "-F", name, "--", "src")
        hits["mentions"] = [l.strip()[:200] for l in r.stdout.splitlines()[:8]]
    return hits


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--update" in sys.argv or not os.path.isdir(CLONE):
        update()
    head = git("log", "-1", "--format=%h %cs").stdout.strip()
    results = [lookup(t) for t in args]
    if "--json" in sys.argv:
        print(json.dumps({"kyty": head, "results": results}))
        return
    for h in results:
        found = h["definitions"] or h["registrations"] or h["mentions"]
        print(f"== {h['term']}: " + ("found in KytyPS5 " + head if found else "not in KytyPS5 " + head))
        for d in h["definitions"]:
            print(f"-- {d['where']}\n{d['code']}")
        for r in h["registrations"] + h["mentions"]:
            print("   " + r)


if __name__ == "__main__":
    main()
