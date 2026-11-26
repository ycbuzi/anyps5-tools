# Fix prediction (Blu 11-03): for every failed game, what most likely gets it past its stop, ranked, with the evidence
# for each candidate, and the candidates the pipeline can test by itself on a test copy. Plain code: log parsing, git
# in the PR worktree and gh queries (cached). Claude is asked only with --ask, only when no candidate has real
# evidence, at most ASK_PER_DAY times a day, and never twice for the same stop.
#
#   python -I predict_fix.py predict [--game "<game folder>"]... [--ask]   writes _reports\fix-predictions.md/.json
#   python -I predict_fix.py next --game "<game folder>" [--count 3]       JSON: testable candidates not tried yet
#   python -I predict_fix.py record --game G --copy C --id ID --version V --result "<Test-Game result>"
#                                                                          JSON: the verdict, from the copy's log
#   python -I predict_fix.py keep --game G --id ID                         a proven setting into game-env.txt
#   python -I predict_fix.py try --game G --id ID [--minutes 8]            one setting on the fix copy, then record
#                                                                          (for the local AI; the pipeline uses Test-Game)
#
# Candidates, strongest first:
#   setting   a launch setting the stop message names itself (set APS5_X=a or =b), or one a playbook gives for it
#   main      a newer AnyPS5 commit already merged: the code that threw changed after the game's build, the message
#             is gone on origin/main, or a PR the playbook names was merged after it
#   pr        an open PR the playbook names, or one gh finds that changes the file or library that threw
#   in-game   the game's own graphics or performance mode, for a game without a picture (a player has to try it)
#   ask       Claude's one-line answer, when nothing else has evidence
# A setting is tested on a test copy built on the game's own AnyPS5; a PR or main library is swapped into that copy.
# Nothing is deleted, nothing is pushed or posted, the game folder is only read (keep writes game-env.txt and the Play
# cmd once a setting is proven). Try results are kept in _tools\fix-tries.json; gh and git answers in
# _tools\fix-predict-cache.json.
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
GAMES = Path(r"D:\AnyPS5 Games")  # the game folders stay on D: (11-03 _tools moved to C: behind a link)
REPORTS = GAMES / "_reports"
STATE = TOOLS / "auto-state.json"
SETTINGS = TOOLS / "settings.json"
PLAYBOOKS = TOOLS / "assistant" / "playbooks.md"
CACHE = TOOLS / "fix-predict-cache.json"
TRIES = TOOLS / "fix-tries.json"
FIX_COPIES = GAMES / "_test" / "_fix"
REPO_DIR = Path(r"D:\AnyPS5\wt-pr")
REPO = "boykopovar/AnyPS5"
HIDDEN = 0x08000000
GH_HOURS = 12
ASK_PER_DAY = 3
TESTABLE = 40
MAIN_TESTABLE = 75

WHAT = re.compile(r"what\(\):\s+(\S.*)$")
THROWN = re.compile(r"\b(sce[A-Za-z0-9_]+)\b.*not implemented")
FRAME = re.compile(r"^\s+(rip|#\d+)\s+0x[0-9a-f]+\s+(\S+\+0x[0-9a-f]+)")
HINT = re.compile(r"(?i)\b(not implemented|not supported|unsupported|unhandled|unknown \w+ (id|format|opcode|type))\b")
NO_MODULE = re.compile(r"Failed to load module: (.+)")
VALUE = r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*"
SETTING = re.compile(r"\b(APS5_[A-Z0-9_]+)=(" + VALUE + r")((?:\s*(?:,|or)\s*=" + VALUE + r")*)")
PR_REF = re.compile(r"(?:\bPRs? |#)(\d{4,6})\b")
STOPS = re.compile(r"NotImplemented\w*\(|not implemented")
COMMON = {"implemented", "supported", "with", "from", "that", "this", "there", "their", "have", "into", "cannot", "does", "when",
          "which", "only", "more", "than", "would", "should", "set", "for", "the", "and", "are", "not", "missing", "sce"}


def run(command, timeout=120, codes=(0,)):
    try:
        done = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, creationflags=HIDDEN)
    except (subprocess.TimeoutExpired, OSError):
        return None
    return done.stdout if done.returncode in codes else None


def git(*args, timeout=180):
    return run(["git", "-C", str(REPO_DIR), *args], timeout)


def load(path, empty):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return empty


def save(path, data):
    tmp = path.with_suffix(path.suffix + ".new")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def scrub(text):
    sys.path.insert(0, str(TOOLS / "assistant"))
    try:
        from work_skills import scrub_text
        return scrub_text(text)
    except Exception:
        return text


class Cache:
    def __init__(self):
        self.data = load(CACHE, {})
        self.dirty = False

    def get(self, key, hours=None):
        row = self.data.get(key)
        if row is None or (hours is not None and time.time() - row.get("at", 0) > hours * 3600):
            return None
        return row

    def put(self, key, value):
        self.data[key] = {"at": time.time(), "value": value}
        self.dirty = True
        return value

    def flush(self):
        if self.dirty:
            save(CACHE, self.data)
            self.dirty = False


def git_cached(cache, key, *args, timeout=180):
    row = cache.get("git|" + key)
    if row:
        return row["value"]
    out = git(*args, timeout=timeout)
    return out if out is None else cache.put("git|" + key, out)


def gh_cached(cache, key, args):
    row = cache.get("gh|" + key, GH_HOURS)
    if row:
        return row["value"]
    out = run(["gh", *args], 120)
    if out is None:
        old = cache.get("gh|" + key)
        return old["value"] if old else None
    try:
        return cache.put("gh|" + key, json.loads(out) if out.strip() else None)
    except ValueError:
        return None


# --- stops -------------------------------------------------------------------------------------------------------

def stop_key(s):
    where = re.sub(r"\+0x[0-9a-fA-F]+", "", s.get("where", ""))
    return re.sub(r"0x[0-9a-fA-F]+|\d+", "#", f"{s.get('kind', '')}|{s.get('function', '')}|{s.get('message', '')}|{where}")


def stop_label(s):
    if s.get("function"):
        return s["function"]
    if s.get("message"):
        return s["message"][:90]
    if s.get("where"):
        return f"{s['kind']} at {s['where']}"
    return s.get("kind", "")


def read_stop(log):
    """What stopped a run, from its log: the same reading as Read-Failure in auto-games.ps1."""
    s = {"kind": "no frames", "function": "", "message": "", "where": "", "line": "", "log": str(log), "when": ""}
    if not log.exists():
        s["kind"] = ""
        return s
    s["when"] = time.strftime("%m-%d %H:%M", time.localtime(log.stat().st_mtime))
    what = thrown = fatal = hint = frame = no_module = None
    after = 0
    with log.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            if what is None:
                m = WHAT.search(line)
                if m:
                    what = (m.group(1).strip(), line.strip())
            if thrown is None and THROWN.search(line):
                thrown = (THROWN.search(line).group(1), line.strip())
            if fatal is None and line.startswith("FATAL"):
                fatal, after = line.strip(), 30
            elif after:
                after -= 1
                m = FRAME.match(line)
                if m and frame is None:
                    frame = m.group(2)
            if hint is None and HINT.search(line):
                hint = line.strip()
            if no_module is None:
                m = NO_MODULE.search(line)
                if m:
                    no_module = m.group(1).strip()
    if what:
        text, s["line"] = what
        ident = re.match(r"^([A-Za-z_]\w*)\b", text)
        ident = ident.group(1) if ident else ""
        if ident and (re.match(r"^(sce|_)", ident) or re.match(r"^[a-z]+[A-Z]", ident)) and "not implemented" in text:
            s.update(kind="missing function", function=ident)
        else:
            s.update(kind="stopped", message=text)
    elif thrown:
        s.update(kind="missing function", function=thrown[0], line=thrown[1])
    elif fatal:
        s.update(kind="crash", line=fatal, where=frame or "")
        if "unhandled exception 0x" not in fatal:
            s["message"] = re.sub(r"^FATAL:?\s*", "", fatal)
    elif no_module:
        s.update(kind="stopped", message=f"Failed to load module: {no_module}", line=no_module)
    elif hint:
        s.update(line=hint, message=hint)
    return s


def newest_crash_log(folder):
    logs = sorted((folder / "crash-logs").glob("*.log"), key=lambda p: p.stat().st_mtime) if (folder / "crash-logs").is_dir() else []
    return logs[-1] if logs else None


def game_stops(entry, game_dir):
    """The game's own stop (its newest log, else its newest saved crash log, else what the pipeline recorded) and
    the stops its test copies reached."""
    own = read_stop(game_dir / "anyps5.log")
    if own["kind"] in ("", "no frames"):
        saved = newest_crash_log(game_dir)
        if saved:
            s = read_stop(saved)
            if s["kind"] not in ("", "no frames"):
                s["source"] = f"saved crash log {saved.name} (the newest log has no stop)"
                own = s
    if own["kind"] in ("", "no frames"):
        f = entry.get("failure") or {}
        note = str(entry.get("note", ""))
        m = re.search(r"AnyPS5 stopped it with this message: (.+?)(?:\. Test copy.*)?$", note)
        if f.get("kind") and f["kind"] != "no frames":
            own = dict(own, kind=f["kind"], function=f.get("function", ""), message=f.get("message", ""), where=f.get("where", ""), line=f.get("line", ""), source="the pipeline's record")
        elif m:
            own = dict(own, kind="stopped", message=m.group(1).strip(), source="the pipeline's note" + (" (a test copy's run)" if "Test copy" in note else ""))
    own.setdefault("source", "the game's newest log")
    if not own["kind"]:
        own["kind"] = "no frames"
    copies = []
    for folder in (GAMES / "_test" / game_dir.name, FIX_COPIES / game_dir.name):
        if not (folder / "anyps5.log").exists():
            continue
        s = read_stop(folder / "anyps5.log")
        cfg = load(folder / "game.json", {})
        s["source"] = f"test copy {folder.relative_to(GAMES)} on {Path(str(cfg.get('anyps5Build', '?'))).name}"
        copies.append(s)
    return own, copies


# --- sources -----------------------------------------------------------------------------------------------------

def build_commit(folder):
    info = folder / "info.txt"
    if info.exists():
        m = re.search(r"AnyPS5:\s+([0-9a-f]{7,40})", info.read_text(encoding="utf-8", errors="replace"))
        if m:
            return m.group(1)
    cfg = load(folder / "game.json", {})
    build = cfg.get("anyps5Build") or load(SETTINGS, {}).get("anyps5Build", "")
    commit = Path(str(build)) / "COMMIT.txt"
    if commit.exists():
        return commit.read_text(encoding="utf-8-sig").split()[0]
    return ""


def message_windows(text):
    t = re.sub(r"\"[^\"]*\"|'[^']*'|`[^`]*`", " ", text)
    t = re.sub(r"0x[0-9a-fA-F]+|\b\d+\b", " ", t)
    t = re.sub(r"(?i)\b((is|are) )?not (implemented|supported)\b", " ", t)
    pieces = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"[:;,.()\[\]{}=<>/|!?]", t)]
    pieces = [p for p in pieces if len(p.split()) >= 2 and not p.startswith("APS5_")]
    windows = []
    for p in sorted(pieces, key=lambda p: -len(p.split())):
        w = p.split()
        windows += [p] if len(w) <= 3 else [" ".join(w[i:i + 3]) for i in range(len(w) - 2)]
    return list(dict.fromkeys(windows))[:40]


def library_dirs(cache, rev):
    out = git_cached(cache, f"dirs|{rev}", "ls-tree", "--name-only", f"{rev}:core/libs/prx")
    return [l.strip() for l in (out or "").splitlines() if l.strip()]


def where_it_threw(cache, stop, rev):
    """The libraries and source files of the stop at the game's commit: a missing function's exporting libraries;
    for a message, the library its prefix names and the files holding most of its 3-word windows."""
    dirs = library_dirs(cache, rev)
    libs, files = [], []
    if stop["kind"] == "missing function":
        out = git_cached(cache, f"export|{rev}|{stop['function']}", "grep", "-l", "-E", rf"APS5_VABI[^(]*\b{stop['function']}\(", rev, "--", "core/libs/prx")
        libs = sorted({m.group(1) for m in re.finditer(r"core/libs/prx/([^/]+)/", out or "")})
        return libs, files
    m = re.match(r"^\s*([A-Za-z][\w ]{0,30}?):", stop.get("message", ""))
    if m:
        token = re.sub(r"^(sce|lib)", "", m.group(1).split(" ")[0])
        if len(token) >= 3:
            libs += [d for d in dirs if re.sub(r"\.native$", "", re.sub(r"^libSce", "", d)).lower().startswith(token.lower())]
    windows = message_windows(stop.get("message", ""))
    if windows:
        args = ["grep", "-F", "-o", "-I"] + sum((["-e", w] for w in windows), []) + [rev, "--", "core"]
        out = git_cached(cache, f"grep|{rev}|{'|'.join(windows)}", *args)
        counts = {}
        for line in (out or "").splitlines():
            parts = line.split(":", 2)
            if len(parts) == 3:
                counts[parts[1]] = counts.get(parts[1], 0) + 1
        if counts:
            top = max(counts.values())
            files = [f for f, c in sorted(counts.items(), key=lambda x: -x[1]) if c == top][:5]
    if stop.get("where", "").startswith("lib"):
        module = re.match(r"^(lib[A-Za-z0-9_]+)", stop["where"]).group(1)
        libs += [d for d in dirs if d == module or d.startswith(module + ".")]
    libs += [m.group(1) for f in files for m in [re.match(r"core/libs/prx/([^/]+)/", f)] if m]
    return list(dict.fromkeys(libs)), files


def function_body(cache, rev, function, libs):
    """An exported function's code at rev, from its APS5_VABI line to the next export."""
    if not libs:
        return ""
    out = git_cached(cache, f"body|{rev}|{function}|{'|'.join(libs)}", "grep", "-h", "-A60", "-E", rf"APS5_VABI[^(]*\b{function}\(", rev, "--",
                     *[f"core/libs/prx/{l}" for l in libs]) or ""
    lines = []
    for i, line in enumerate(out.splitlines()):
        if i and ("APS5_VABI" in line or line == "--"):
            break
        lines.append(line.strip())
    return "\n".join(lines)


def playbook_paragraphs():
    if not PLAYBOOKS.exists():
        return []
    text = PLAYBOOKS.read_text(encoding="utf-8", errors="replace")
    return [p.strip() for p in re.split(r"\n(?=## )", text) if p.strip().startswith("## Playbook")]


def playbook_matches(stop, title_id, title):
    """Playbook entries about this stop: its function, a setting it names, or two of its 3-word windows; an entry
    that only names the game is listed but gives no candidate of its own."""
    found = []
    words = message_windows(stop.get("message", "") + " " + stop.get("line", ""))
    settings = set(re.findall(r"APS5_[A-Z0-9_]+", stop.get("message", "") + stop.get("line", "")))
    for p in playbook_paragraphs():
        low = p.lower()
        why = []
        if stop.get("function") and stop["function"] in p:
            why.append(f"names {stop['function']}")
        for s in settings:
            if s in p:
                why.append(f"names {s}")
        hits = [w for w in words if len(w.split()) >= 3 and w.lower() in low]
        if len(hits) >= 2 or (hits and len(words) == 1):
            why.append("has '" + "', '".join(hits[:2]) + "'")
        about_game = bool(title_id and title_id in p) or bool(title and len(title) > 6 and title.lower() in low)
        if why or about_game:
            found.append({"title": p.splitlines()[0].replace("## Playbook: ", ""), "text": p, "why": why, "aboutGame": about_game})
    return found


def pr_info(cache, number):
    return gh_cached(cache, f"pr|{number}", ["pr", "view", str(number), "--repo", REPO, "--json", "number,title,state,headRefOid,mergedAt,mergeCommit,files,url"])


def pr_libraries(info):
    return sorted({m.group(1) for f in (info or {}).get("files", []) for m in [re.match(r"core/libs/prx/([^/]+)/", f.get("path", ""))] if m})


def merged_after(cache, oid, base, main):
    """True when commit oid is on origin/main but not in the game's build."""
    if not oid or not base:
        return False
    if git_cached(cache, f"anc|{oid}|{base}", "merge-base", "--is-ancestor", oid, base, timeout=60) is not None:
        return False
    return git_cached(cache, f"anc|{oid}|{main}", "merge-base", "--is-ancestor", oid, main, timeout=60) is not None


def chain_tests(entry):
    """PR heads the daily PR test (Triage-Game) already launched the game with, and what came of it."""
    done = {}
    for row in entry.get("chain") or []:
        for m in re.finditer(r"#(\d+) ([0-9a-f]{7,})", str(row.get("merged", ""))):
            done[int(m.group(1))] = {"head": m.group(2), "result": str(row.get("result", ""))[:160], "for": row.get("function", "")}
    return done


# --- candidates --------------------------------------------------------------------------------------------------

def candidates_for(cache, entry, game_dir, stop, ask=False):
    cfg = load(game_dir / "game.json", {})
    title_id, title = cfg.get("titleId", ""), cfg.get("title", "")
    base = build_commit(game_dir)
    main = (git("rev-parse", "--short=10", "origin/main", timeout=60) or "").strip()
    rev = base if base and git_cached(cache, f"has|{base}", "cat-file", "-e", base + "^{commit}", timeout=60) is not None else "origin/main"
    text = " ".join([stop.get("message", ""), stop.get("line", "")])
    out = []

    def add(c):
        if not any(o["id"] == c["id"] for o in out):
            out.append(c)
        else:
            o = next(o for o in out if o["id"] == c["id"])
            o["score"] = max(o["score"], c["score"])
            o["evidence"] += [e for e in c["evidence"] if e not in o["evidence"]]
            o["searchOnly"] = o.get("searchOnly", False) and c.get("searchOnly", False)
            o["libraries"] = list(dict.fromkeys((o.get("libraries") or []) + (c.get("libraries") or [])))

    for m in SETTING.finditer(text):
        values = [m.group(2)] + re.findall(r"=(" + VALUE + ")", m.group(3) or "")
        for i, v in enumerate(values):
            add({"id": f"setting:{m.group(1)}={v}", "kind": "setting", "title": f"launch setting {m.group(1)}={v}", "score": 95 - i,
                 "evidence": [f"the stop message itself says to set {m.group(1)}=" + " or =".join(values)], "env": {m.group(1): v}, "version": base})

    playbooks = playbook_matches(stop, title_id, title)
    for p in playbooks:
        if not p["why"]:
            continue
        for name, value in re.findall(r"\b(APS5_[A-Z0-9_]+)=(" + VALUE + ")", p["text"]):
            add({"id": f"setting:{name}={value}", "kind": "setting", "title": f"launch setting {name}={value}", "score": 85,
                 "evidence": [f"playbook \"{p['title']}\" ({', '.join(p['why'])}) gives {name}={value}"], "env": {name: value}, "version": base})
        for number in sorted({int(n) for n in PR_REF.findall(p["text"])}):
            info = pr_info(cache, number)
            if not info:
                continue
            why = f"playbook \"{p['title']}\" ({', '.join(p['why'])}) names PR #{number}"
            if info.get("state") == "MERGED":
                oid = (info.get("mergeCommit") or {}).get("oid", "")
                if merged_after(cache, oid, rev, "origin/main"):
                    add({"id": f"main:{main}", "kind": "main", "title": f"newer AnyPS5 (origin/main {main})", "score": 80,
                         "evidence": [why + f", merged {str(info.get('mergedAt', ''))[:10]} after the game's AnyPS5 {base}"], "libraries": pr_libraries(info), "version": main})
                elif oid:
                    add({"id": f"pr:{number}", "kind": "note", "title": f"PR #{number} ({info.get('title', '')[:60]})", "score": 5,
                         "evidence": [why + ", already in the game's AnyPS5: it does not cover this stop"], "version": ""})
            elif info.get("state") == "OPEN":
                add({"id": f"pr:{number}", "kind": "pr", "title": f"open PR #{number}: {info.get('title', '')[:70]}", "score": 70,
                     "evidence": [why], "prs": [number], "libraries": pr_libraries(info), "version": info.get("headRefOid", "")[:10]})

    libs, files = where_it_threw(cache, stop, rev) if stop["kind"] != "no frames" or stop.get("message") else ([], [])
    if (libs or files) and main and rev != "origin/main":
        paths = files or [f"core/libs/prx/{l}" for l in libs]
        log = git_cached(cache, f"log|{rev}|{main}|{'|'.join(paths)}", "log", "-n", "15", "--format=%h|%cs|%s", f"{rev}..origin/main", "--", *paths, timeout=300) or ""
        commits = [l.split("|", 2) for l in log.splitlines() if l.count("|") >= 2]
        gone = False
        if files and stop.get("message"):
            windows = message_windows(stop["message"])
            args = ["grep", "-c", "-F"] + sum((["-e", w] for w in windows), []) + ["origin/main", "--", *files]
            # git grep exits 1 when nothing matches; a timeout gives None and says nothing.
            still = run(["git", "-C", str(REPO_DIR), *args], 180, codes=(0, 1))
            gone = still is not None and not still.strip()
        named = [c for c in commits if stop.get("function") and stop["function"] in c[2]]
        body = ""
        if stop["kind"] == "missing function":
            before, after = function_body(cache, rev, stop["function"], libs), function_body(cache, "origin/main", stop["function"], libs)
            stops_before, stops_after = len(STOPS.findall(before)), len(STOPS.findall(after))
            if after and stops_before and not stops_after:
                gone, body = True, f"{stop['function']} no longer stops on origin/main ({stops_before} stop(s) in it at {rev}, none now)"
            elif after and after != before:
                body = f"{stop['function']} changed on origin/main and still has {stops_after} stop(s) in it"
            elif after:
                body = f"{stop['function']} is the same on origin/main: it still stops there"
        if commits:
            score = 80 if gone else 75 if named else 60 if "changed on origin/main" in body else 45
            if body.endswith("still stops there"):
                score = 20
            ev = [f"{len(commits)}{'+' if len(commits) == 15 else ''} commit(s) on origin/main after the game's AnyPS5 {rev} change {', '.join(paths[:3])}: "
                  + "; ".join(f"{h} {d} {s[:70]}" for h, d, s in (named or commits)[:3])]
            if gone and not body:
                ev.append("the message's words are no longer in that code on origin/main")
            if body:
                ev.append(body)
            add({"id": f"main:{main}", "kind": "main", "title": f"newer AnyPS5 (origin/main {main})", "score": score, "evidence": ev,
                 "libraries": libs, "version": main})

    if stop["kind"] != "no frames" or stop.get("message"):
        queries = [stop["function"]] if stop.get("function") else []
        words = message_windows(stop.get("message", ""))
        if words:
            queries.append(f"\"{words[0]}\"")
        queries += [re.sub(r"\.native$", "", re.sub(r"^libSce", "", l)) for l in libs[:2]]
        tested = chain_tests(entry)
        lib_words = {w.lower() for l in libs for w in re.findall(r"[A-Za-z][a-z0-9]+", re.sub(r"^libSce|\.native$", "", l))}
        stop_words = {w.lower() for w in re.findall(r"[A-Z]?[a-z]{3,}|[A-Z]{3,}", stop.get("function", "") + " " + stop.get("message", ""))} - COMMON - lib_words
        library_only = 0
        for q in dict.fromkeys(q for q in queries if q):
            found = gh_cached(cache, f"search|{q}", ["search", "prs", "--repo", REPO, "--state", "open", q, "--json", "number,title,updatedAt", "--limit", "15"]) or []
            for p in found:
                info = pr_info(cache, p["number"])
                if not info or info.get("state") != "OPEN":
                    continue
                changed = [f.get("path", "") for f in info.get("files", [])]
                plibs = pr_libraries(info)
                exact = [f for f in changed if f in files]
                same_lib = [l for l in plibs if l in libs]
                if not exact and not same_lib:
                    continue
                named = bool(stop.get("function") and stop["function"] in (p.get("title") or ""))
                shared = sorted(stop_words & {w.lower() for w in re.findall(r"[A-Za-z][a-z0-9]+|[A-Z]{3,}", p.get("title") or "")})
                score = 65 if named or (exact and len(shared) >= 2) else 55 if exact and shared else 50 if len(shared) >= 2 else 40 if exact else 35 if shared else 25
                if score == 25:
                    library_only += 1
                    if library_only > 3:
                        continue
                ev = [f"gh search '{q}': changes {', '.join(exact[:2]) if exact else ', '.join(same_lib)}" + (", title names it" if named else "")
                      + (f", title shares '{', '.join(shared)}' with the stop" if shared and not named else "")]
                head = info.get("headRefOid", "")[:10]
                t = tested.get(int(p["number"]))
                if t and head.startswith(t["head"][:10]):
                    score = min(score, 15)
                    ev.append(f"the PR test already ran this head: {t['result']}")
                elif t:
                    ev.append(f"the PR test ran an older head ({t['head']}): {t['result']}")
                add({"id": f"pr:{p['number']}", "kind": "pr", "title": f"open PR #{p['number']}: {(p.get('title') or '')[:70]}", "score": score,
                     "evidence": ev, "prs": [int(p["number"])], "libraries": plibs, "version": head, "searchOnly": True})

    if stop["kind"] == "no frames" or "stalls" in str(entry.get("note", "")):
        add({"id": "in-game:graphics-mode", "kind": "in-game", "title": "the game's own graphics / performance mode", "score": 15,
             "evidence": ["no picture or a stall without an AnyPS5 stop: Blu's find (11-02) is that a game's performance or frame rate mode can change this; a player has to set it in the game's menu"], "version": ""})

    for p in playbooks:
        if p["aboutGame"] and not p["why"]:
            add({"id": f"playbook:{p['title']}", "kind": "note", "title": f"playbook \"{p['title']}\" is about this game", "score": 10,
                 "evidence": ["read it before testing by hand: " + re.sub(r"\s+", " ", p["text"].split("\n", 1)[-1])[:200]], "version": ""})

    if ask and not any(c["score"] >= 50 for c in out):
        answer = ask_claude(cache, game_dir, stop, base, out)
        if answer:
            ev = [f"Claude (Haiku, cached for this stop): {answer[:300]}"]
            m = re.search(r"\b(APS5_[A-Z0-9_]+)=(" + VALUE + ")", answer)
            pr = re.search(r"#(\d{4,6})", answer)
            if m:
                add({"id": f"setting:{m.group(1)}={m.group(2)}", "kind": "setting", "title": f"launch setting {m.group(1)}={m.group(2)}", "score": 45, "evidence": ev, "env": {m.group(1): m.group(2)}, "version": base})
            elif pr:
                info = pr_info(cache, int(pr.group(1)))
                if info and info.get("state") == "OPEN":
                    add({"id": f"pr:{pr.group(1)}", "kind": "pr", "title": f"open PR #{pr.group(1)}: {info.get('title', '')[:70]}", "score": 45, "evidence": ev,
                         "prs": [int(pr.group(1))], "libraries": pr_libraries(info), "version": info.get("headRefOid", "")[:10]})
            else:
                add({"id": "ask", "kind": "note", "title": "Claude's reading", "score": 20, "evidence": ev, "version": ""})

    for c in out:
        c["testable"] = (c["kind"] == "setting" and c["score"] >= TESTABLE) or (c["kind"] == "pr" and c["score"] >= TESTABLE and bool(c.get("libraries"))) \
            or (c["kind"] == "main" and c["score"] >= MAIN_TESTABLE and bool(c.get("libraries")))
    out.sort(key=lambda c: -c["score"])
    return out, {"commit": base, "main": main, "libraries": libs, "files": files, "playbooks": [p["title"] for p in playbooks]}


def ask_claude(cache, game_dir, stop, base, found):
    key = "ask|" + stop_key(stop)
    row = cache.get(key)
    if row:
        return row["value"]
    today = time.strftime("%Y-%m-%d")
    asked = cache.get("askcount|" + today)
    if asked and asked["value"] >= ASK_PER_DAY:
        return ""
    facts = (f"Game {game_dir.name} on AnyPS5 {base}. Stop kind: {stop['kind']}. "
             + (f"Function: {stop['function']}. " if stop.get("function") else "")
             + (f"Message: {stop['message'][:200]}. " if stop.get("message") else "")
             + (f"Crash at {stop['where']}. " if stop.get("where") else "")
             + ("Weak leads so far: " + "; ".join(c["title"] for c in found[:3]) + ". " if found else "No lead found in playbooks, open PRs or newer commits. "))
    question = (facts + "Which one thing most likely gets this game past the stop: a launch setting (APS5_NAME=value), "
                "an open AnyPS5 PR (#number), newer AnyPS5 main (MAIN), or NONE? First line: exactly one of those. "
                "Then one sentence why. Check assistant\\playbooks.md first; do not read game logs.")
    cache.put("askcount|" + today, (asked["value"] if asked else 0) + 1)
    cache.flush()
    out = run(["python", "-I", str(TOOLS / "assistant" / "claude_bridge.py"), "ask", question], 400) or ""
    out = out.strip()
    if not out or out.startswith("Claude ") and ("not signed in" in out or "cap reached" in out or "gave no answer" in out or "not installed" in out):
        return ""
    return cache.put(key, out)


# --- games -------------------------------------------------------------------------------------------------------

def failed_games(state, only):
    rows = []
    for name, e in state.items():
        folder = Path(e.get("gameDir") or "")
        if not e.get("gameDir") or not (folder / "game.json").exists():
            continue
        if only and not any(Path(o).resolve() == folder.resolve() or Path(o).name == folder.name for o in only):
            continue
        note = str(e.get("note", ""))
        if not only and (e.get("status") not in ("done", "built") or note.startswith("runs:") or not (note.startswith("problem:") or (e.get("failure") or {}).get("kind"))):
            continue
        rows.append((name, e, folder))
    return rows


def predict(games, ask=False):
    state = load(STATE, {})
    cache = Cache()
    tries = load(TRIES, {})
    result = load(REPORTS / "fix-predictions.json", {"games": {}})
    result.setdefault("games", {})
    for name, e, folder in failed_games(state, games):
        stop, copies = game_stops(e, folder)
        cands, where = candidates_for(cache, e, folder, stop, ask)
        key = stop_key(stop)
        for c in cands:
            t = tries.get(f"{folder.name}|{key}|{c['id']}|{c.get('version', '')}")
            if t:
                c["tried"] = t
        recorded = e.get("failure") or {}
        result["games"][folder.name] = {"entry": name, "updated": time.strftime("%Y-%m-%d %H:%M"), "stop": stop, "stopKey": key,
                                        "triageKey": stop_key(recorded) if isinstance(recorded, dict) and recorded.get("kind") else "",
                                        "copies": copies, "where": where, "candidates": cands}
        cache.flush()
    if not games:
        keep = {f.name for _, _, f in failed_games(state, [])}
        result["games"] = {k: v for k, v in result["games"].items() if k in keep}
    result["updated"] = time.strftime("%Y-%m-%d %H:%M")
    save(REPORTS / "fix-predictions.json", result)
    write_md(result, tries)
    return result


def write_md(result, tries):
    lines = [f"# Fix predictions ({result['updated']})", "",
             "What most likely gets each failed game past its stop, strongest first, with the evidence. The pipeline tests "
             "the testable ones by itself on a test copy (_test\\_fix\\<game>) and keeps a launch setting only once a run "
             "proves it. Made by _tools\\predict_fix.py from logs, playbooks.md, git and gh; no Claude unless nothing else "
             "had evidence.", ""]
    for game, g in sorted(result["games"].items()):
        s = g["stop"]
        lines += [f"## {game}", "",
                  f"Stop: **{s['kind']}**: {stop_label(s)} ({s.get('source', '')}, {s.get('when', '')}). AnyPS5 {g['where'].get('commit') or '?'}; origin/main {g['where'].get('main') or '?'}."]
        if g["where"].get("libraries") or g["where"].get("files"):
            lines.append(f"Where it threw: {', '.join(g['where'].get('libraries') or [])} {', '.join(g['where'].get('files') or [])}".rstrip())
        for c in g.get("copies") or []:
            if c.get("kind"):
                lines.append(f"Seen on {c['source']} ({c.get('when', '')}): {c['kind']}: {stop_label(c)}")
        lines += ["", "| # | candidate | score | evidence | test |", "|---|---|---|---|---|"]
        for i, c in enumerate(g["candidates"], 1):
            t = c.get("tried")
            test = (f"{t['verdict']}: {t['result'][:90]} ({t['when']})" if t else "the daily PR test" if c.get("searchOnly") and c.get("testable") and g.get("triageKey") == g["stopKey"]
                    else "to test on the fix copy" if c.get("testable") else "not tested unattended")
            lines.append(f"| {i} | {c['title']} | {c['score']} | {' / '.join(c['evidence'])[:400].replace('|', '/')} | {test.replace('|', '/')} |")
        if not g["candidates"]:
            lines.append("| - | nothing found | 0 | no setting, playbook, newer commit or open PR points at this stop | - |")
        lines.append("")
    recent = sorted(tries.values(), key=lambda t: t.get("when", ""), reverse=True)[:15]
    if recent:
        lines += ["## Latest tries", ""] + [f"- {t['when']} {t['game']}: {t['id']} -> {t['verdict']} ({t['result'][:100]})" for t in recent] + [""]
    (REPORTS / "fix-predictions.md").write_text(scrub("\n".join(lines)), encoding="utf-8")


def find_game(result, game):
    name = Path(game).name
    return name, result["games"].get(name)


def cmd_next(game, count):
    result = load(REPORTS / "fix-predictions.json", {"games": {}})
    name, g = find_game(result, game)
    if not g:
        print("[]")
        return
    tries = load(TRIES, {})
    # PRs a gh search found are the daily PR test's (Triage-Game), unless it searches for another stop than this one
    # (Fatal Frame II: its record says no frames, its note an NGS2 stop).
    own_search = g.get("triageKey") != g["stopKey"]
    picked = [c for c in g["candidates"] if c.get("testable") and (own_search or not c.get("searchOnly"))
              and f"{name}|{g['stopKey']}|{c['id']}|{c.get('version', '')}" not in tries]
    print(json.dumps([{k: c.get(k) for k in ("id", "kind", "title", "score", "env", "prs", "libraries", "version")} for c in picked[:count]]))


def known_stops(game_dir):
    """Stop keys the game itself already reached without any change: its own log and its saved crash logs."""
    logs = [game_dir / "anyps5.log"]
    if (game_dir / "crash-logs").is_dir():
        logs += list((game_dir / "crash-logs").glob("*.log"))
    keys = set()
    for log in logs:
        s = read_stop(log) if log.exists() else {"kind": ""}
        if s["kind"] in ("missing function", "stopped", "crash"):
            keys.add(stop_key(s))
        elif s["kind"] == "no frames" and not s.get("line"):
            keys.add("no frames")
    return keys


def verdict_of(result_text, before, after, known=()):
    if result_text.startswith("runs:"):
        return "fixed"
    if result_text.startswith(("could not", "build failed", "not tested")):
        return "not tested"
    # Fatal Frame II: three PRs "got past" an NGS2 stop seen on another build, to a stop its own build already had.
    if after["kind"] in ("missing function", "stopped", "crash") and stop_key(after) in known:
        return "same stop"
    if after["kind"] == "no frames" and not after.get("line") and "no frames" in known:
        return "same stop"
    if after["kind"] in ("missing function", "stopped", "crash") and stop_key(after) != stop_key(before):
        if after.get("message", "").startswith("Failed to load module"):
            return "unclear"
        return "further"
    if stop_key(after) == stop_key(before):
        return "same stop"
    # The stop was AnyPS5's own and this run stayed up without any stop in its log: it got past it (Scorn with the
    # PlayGo setting drew nothing visible for 20 minutes, writing back storage textures, and never stopped).
    if before["kind"] in ("missing function", "stopped") and after["kind"] == "no frames" and not after.get("line") \
            and "no frames" not in known and result_text.startswith(("no frames after", "stalls:")):
        return "further"
    return "unclear"


def cmd_record(game, copy, cid, version, result_text):
    result = load(REPORTS / "fix-predictions.json", {"games": {}})
    name, g = find_game(result, game)
    if not g:
        print(json.dumps({"verdict": "unknown game"}))
        return
    after = read_stop(Path(copy) / "anyps5.log") if copy else {"kind": ""}
    verdict = verdict_of(result_text, g["stop"], after, known_stops(game_dir_of(g)))
    tries = load(TRIES, {})
    row = {"game": name, "id": cid, "version": version, "stop": stop_label(g["stop"]), "verdict": verdict, "result": result_text[:300],
           "next": stop_label(after) if after.get("kind") else "", "when": time.strftime("%Y-%m-%d %H:%M")}
    tries[f"{name}|{g['stopKey']}|{cid}|{version}"] = row
    save(TRIES, tries)
    for c in g["candidates"]:
        if c["id"] == cid:
            c["tried"] = row
    save(REPORTS / "fix-predictions.json", result)
    write_md(result, tries)
    print(json.dumps(row))


def game_dir_of(g):
    e = load(STATE, {}).get(g.get("entry", ""), {})
    return Path(e.get("gameDir") or GAMES / "-")


def games_running():
    out = run(["powershell", "-NoProfile", "-Command", "Get-Process | Where-Object { $_.Path } | ForEach-Object { $_.Path }"], 60) or ""
    root = str(GAMES).lower() + "\\"
    return [p for p in out.splitlines() if p.lower().startswith(root) and "\\_tools\\" not in p.lower() and not Path(p).name.lower().startswith("anyps5 ")]


def cmd_try(game, cid, minutes):
    """One setting candidate on the fix copy, the way Test-Game launches a game: the game's own game-env.txt plus the
    candidate's setting, until it exits, runs 3 minutes past its first frame with 600 frames, or the time is up."""
    result = load(REPORTS / "fix-predictions.json", {"games": {}})
    name, g = find_game(result, game)
    if not g:
        print(f"{Path(game).name} has no fix prediction; run predict first.")
        return 1
    c = next((c for c in g["candidates"] if c["id"] == cid), None)
    if not c or c["kind"] != "setting":
        print(f"{cid} is not a setting candidate of {name}. Settings: " + ", ".join(x["id"] for x in g["candidates"] if x["kind"] == "setting")
              + ". A PR or newer AnyPS5 is tried by the pipeline (fixtry), or by hand with build_pr_library and test_run on the fix copy.")
        return 1
    copy = FIX_COPIES / name
    cfg = load(copy / "game.json", {})
    exe = copy / f"{cfg.get('exeName') or cfg.get('title', '')}.exe"
    if not cfg or not exe.exists():
        print(f"{name} has no fix copy yet: make_fix_copy first.")
        return 1
    busy = games_running()
    if busy:
        print(f"A game is running ({Path(busy[0]).name}); Blu may be playing. Not started.")
        return 1
    own = game_dir_of(g) / "game-env.txt"
    rows = {}
    for f in ([own] if own.exists() else []):
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                rows[k.strip()] = v.strip()
    (copy / "game-env.txt").write_text("".join(f"{k}={v}\n" for k, v in rows.items()), encoding="utf-8")
    env = dict(__import__("os").environ, **rows, **c["env"])
    sys.path.insert(0, str(TOOLS / "assistant"))
    from debug_skills import window_title
    started = time.time()
    p = subprocess.Popen([str(exe)], cwd=str(copy), env=env)
    frames, first, title = 0, None, ""
    while p.poll() is None and time.time() - started < minutes * 60:
        time.sleep(3)
        title = window_title(p.pid, TOOLS / "live")
        m = re.search(r"FPS: ([0-9.]+) \((\d+)\)", title)
        if m:
            frames = int(m.group(2))
            first = first or time.time()
            if frames > 600 and time.time() - first >= 180:
                break
    if p.poll() is not None:
        text = f"exited 0x{p.returncode & 0xFFFFFFFF:08X} after {time.time() - started:.0f} s"
    elif frames >= 300:
        text = f"runs: {title} | first frame after {int(first - started)} s"
    elif frames:
        text = f"stalls: only {frames} frames in {int((time.time() - started) / 60)} min ({title})"
    else:
        text = f"no frames after {(time.time() - started) / 60:.0f} min"
    if p.poll() is None:
        p.kill()
        time.sleep(2)
    stop = read_stop(copy / "anyps5.log")
    if stop.get("line") and not text.startswith("runs:"):
        text += " | " + stop["line"][:200]
        (copy / "crash-logs").mkdir(exist_ok=True)
        (copy / "crash-logs" / time.strftime("%Y-%m-%d-%H%M-fix-try.log")).write_bytes((copy / "anyps5.log").read_bytes())
    cmd_record(name, str(copy), cid, c.get("version", ""), text)
    return 0


def cmd_keep(game, cid):
    m = re.fullmatch(r"setting:(APS5_[A-Z0-9_]+)=(" + VALUE + ")", cid)
    if not m:
        print("only a setting can be kept")
        return 1
    name = Path(game).name
    proven = [t for t in load(TRIES, {}).values() if t.get("game") == name and t.get("id") == cid and t.get("verdict") in ("fixed", "further")]
    if not proven:
        print(f"{cid} has no try on {name}'s fix copy that ran or got past the stop; try_fix it first.")
        return 1
    sys.path.insert(0, str(TOOLS / "assistant"))
    from work_skills import set_game_setting
    print(set_game_setting(game, m.group(1), m.group(2)))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["predict", "next", "record", "keep", "try"])
    ap.add_argument("--minutes", type=float, default=8)
    ap.add_argument("--game", action="append", default=[])
    ap.add_argument("--ask", action="store_true")
    ap.add_argument("--count", type=int, default=3)
    ap.add_argument("--copy", default="")
    ap.add_argument("--id", default="")
    ap.add_argument("--version", default="")
    ap.add_argument("--result", default="")
    a = ap.parse_args()
    if a.action == "predict":
        r = predict(a.game, a.ask)
        for game, g in sorted(r["games"].items()):
            if a.game and not any(Path(x).name == game for x in a.game):
                continue
            top = g["candidates"][0] if g["candidates"] else None
            print(scrub(f"{game}: {g['stop']['kind']} {stop_label(g['stop'])[:60]} -> " + (f"{top['title']} ({top['score']})" if top else "nothing found")
                        + f"; {sum(1 for c in g['candidates'] if c.get('testable'))} testable"))
        return 0
    if a.action == "next":
        cmd_next(a.game[0], a.count)
    elif a.action == "try":
        return cmd_try(a.game[0], a.id, a.minutes)
    elif a.action == "record":
        cmd_record(a.game[0], a.copy, a.id, a.version, a.result)
    else:
        return cmd_keep(a.game[0], a.id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
