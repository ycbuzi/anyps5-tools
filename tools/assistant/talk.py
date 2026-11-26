"""The AI team's conversations (Blu 11-04: "they have actual conversations, we see that they're actually working on stuff
too when they're talking"). Built the way the team asked for it when Blu asked them first:

- Work talk is instant and addressed: a message goes to a named teammate wherever it is in the realm; walking is only
  what viewers see and never delays a message or a job.
- Every message is about real work and carries its evidence (a file:line, PR, log line, step number or library).
- A message reaches its teammate at the start of the teammate's next job, or sooner: step() lets the oldest waiting
  teammate answer at once in a short call of its own (read-only look-ups so it can check before it answers).
- A thread is a question, answered or used; the asker marks a reply that helped its job as used and the replier earns
  1 point. After three exchanges an open thread goes to the Round Table.
- Nothing here is invented: viewers see the members' own words, beside the job each thread is about.
Threads live in repair-runs/threads.json."""
import json
import re
import time
from pathlib import Path

import claude_bridge as cb

THREADS = cb.RUNS / "threads.json"
JOB_KINDS = ("build", "fix", "exp", "repair", "coach", "pr")
TALKERS = ("boss", "pr", "build", "fix", "exp", "repair", "coach", "supervise", "ask")
COURT = {"build": "Lord Mason", "fix": "Sir Patch", "exp": "Duchess Alchemy", "repair": "Baron Cog", "coach": "Lady Scroll",
         "supervise": "Captain Vigil", "ask": "Herald Pip", "local": "Squire Qwen", "boss": "Queen Fable", "pr": "Ambassador Merge"}
NAMES = {"fable": "boss", "queen": "boss", "boss": "boss", "opus": "pr", "qwen": "local", "squire": "local", "local ai": "local"}
for _k, _n in list(cb.MEMBERS.items()) + list(COURT.items()):
    NAMES[_n.lower()] = _k
    NAMES[_n.split()[-1].lower()] = _k
MAX_EXCHANGES = 3


def load():
    try:
        return json.loads(THREADS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def save(threads):
    cb.RUNS.mkdir(exist_ok=True)
    tmp = THREADS.with_suffix(".tmp")
    tmp.write_text(json.dumps(threads, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(THREADS)


def who(name, text=""):
    """A member kind from how a teammate named it: its role (Fixer), court name (Sir Patch, Patch) or model (Fable). Haiku
    and Sonnet name several members; the role a message talks about decides, else the Helper (Herald Pip carries it)."""
    key = re.sub(r"[^a-z ]", "", str(name).lower()).strip()
    if key in NAMES:
        return NAMES[key]
    if key in ("haiku", "sonnet"):
        model = "claude-haiku-5-5" if key == "haiku" else "claude-sonnet-5-5"
        hints = {"supervise": r"\bwatch|\bstop\b", "ask": r"\bcheck\b|\bask\b", "build": r"\bexport|\bbuild", "fix": r"\bfix\b|\bcause",
                 "exp": r"\bexperiment|\bguess", "repair": r"\brepair|\bpipeline", "coach": r"\blesson|\bteach"}
        same = [k for k in TALKERS if cb.KINDS.get(k, {}).get("model") == model]
        return next((k for k in same if re.search(hints.get(k, "$^"), text, re.I)), "ask" if key == "haiku" else same[0])
    return ""


def post(frm, to, text, evidence="", about="", thread=None):
    """A new message, or a reply in thread (its id). Returns the thread id."""
    threads = load()
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    msg = {"from": frm, "to": to, "text": str(text).strip()[:1200], "evidence": str(evidence).strip()[:400], "at": now}
    t = next((t for t in threads if t["id"] == thread), None) if thread else None
    if t is None:
        t = {"id": f"T{len(threads) + 1}", "about": str(about)[:200], "status": "question", "messages": [], "started": now}
        threads.append(t)
    t["messages"].append(msg)
    t["updated"] = now
    if thread and t["status"] == "question" and frm == t["messages"][0]["to"]:
        t["status"] = "answered"
    if len(t["messages"]) >= 2 * MAX_EXCHANGES and t["status"] not in ("used", "closed"):
        t["status"] = "to the Round Table"
    save(threads)
    cb.note(f"AI TEAM {cb.MEMBERS.get(frm, frm)} -> {cb.MEMBERS.get(to, to)} ({t['id']}): {msg['text'][:150]}")
    return t["id"]


def waiting_for(kind, threads=None):
    """Threads whose newest message is addressed to kind and still wants an answer."""
    return [t for t in (threads if threads is not None else load())
            if t["messages"] and t["messages"][-1]["to"] == kind and t["status"] not in ("used", "closed", "to the Round Table")]


def briefing(kind):
    """What a member is told at the start of a job: its messages, and how to answer or write to a teammate."""
    mine = waiting_for(kind)[-6:]
    rows = []
    for t in mine:
        rows.append(f"[{t['id']}] about {t['about'] or 'the work'} ({t['status']}):")
        rows += [f"  {cb.MEMBERS.get(m['from'], m['from'])}: {m['text']}" + (f" (evidence: {m['evidence']})" if m["evidence"] else "") for m in t["messages"][-4:]]
    names = ", ".join(cb.MEMBERS[k] for k in TALKERS if k != kind)
    return (("# Messages for you from your teammates\n" + "\n".join(rows) + "\n" if rows else "")
            + "# Talking to your team\nYour teammates: " + names + ". Messages are instant, wherever anyone is. To answer one, add "
              '"replies": [{"thread": "T3", "text": "your answer", "evidence": "file:line, log line, PR or step"}] to your last-line JSON; '
              'to ask or tell a teammate something about your real work, add "messages": [{"to": "Fixer", "about": "game or library", '
              '"text": "one question or offer", "evidence": "what it rests on"}]; if a teammate\'s reply helped this job, add '
              '"used_replies": ["T3"]. Only what you really checked or did; no small talk.\n\n')


def take(kind, result):
    """The replies, messages and thanks in a member's last-line JSON."""
    try:
        last = json.loads([l for l in str(result).strip().splitlines() if l.strip().startswith("{")][-1])
    except (IndexError, ValueError):
        return 0
    if not isinstance(last, dict):
        return 0
    count = 0
    for r in last.get("replies") or []:
        if isinstance(r, dict) and r.get("thread") and r.get("text"):
            t = next((t for t in load() if t["id"] == r["thread"]), None)
            if t:
                post(kind, t["messages"][-1]["from"], r["text"], r.get("evidence", ""), t["about"], t["id"])
                count += 1
    for m in (last.get("messages") or [])[:3]:
        to = who(m.get("to", ""), m.get("text", "")) if isinstance(m, dict) else ""
        if to and to != kind and m.get("text"):
            post(kind, to, m["text"], m.get("evidence", ""), m.get("about", ""))
            count += 1
    threads = load()
    for tid in last.get("used_replies") or []:
        t = next((t for t in threads if t["id"] == tid and t["status"] != "used"), None)
        repliers = [m["from"] for m in (t or {}).get("messages", []) if m["from"] != kind]
        if t and repliers and t["messages"][0]["from"] == kind:
            t["status"] = "used"
            board = cb.scoreboard()
            board.setdefault(repliers[-1], {"passes": 0, "strikes": [], "off": False, "last": ""}).setdefault("points", []).append(
                [time.strftime("%Y-%m-%dT%H:%M:%S"), 1, f"its reply in {tid} helped the {cb.MEMBERS.get(kind, kind)}'s job"])
            cb.SCORE.write_text(json.dumps(board, indent=1), encoding="utf-8")
            cb.note(f"AI TEAM thanks: the {cb.MEMBERS.get(kind, kind)} used the {cb.MEMBERS.get(repliers[-1], repliers[-1])}'s reply in {tid} (1 point)")
    save(threads)
    return count


TALK_RULES = ("Your teammates wrote to you. Answer each message below about your real work: check what you need with the "
              "look-up tools (python -I assistant/tool_cli.py <tool> '<json>', or read the files), then answer briefly with what "
              "you found and the evidence. Game logs: crash_summary, search_game_logs, game_history and diagnose_game look them up; "
              "the files are D:\\AnyPS5 Games\\<game>\\anyps5.log and crash-logs, test copies D:\\AnyPS5 Games\\_test\\_fps\\<game>, "
              "AnyPS5 code D:\\AnyPS5\\wt-pr. Say plainly when you do not know or did not check. You may ask one teammate one "
              "question back. You change no files here. End with one JSON line: "
              '{"replies": [{"thread": "T1", "text": "...", "evidence": "..."}], "messages": []}\n\n')


def converse(kind):
    """kind answers what is waiting for it now, in a short call of its own on its own model."""
    if kind not in TALKERS or not waiting_for(kind):
        return ""
    cb.KINDS["talk"]["model"] = cb.KINDS.get(kind, cb.KINDS["boss"])["model"]
    cb.LIVE_AS["talk"] = kind
    cb.LIVE_LABEL["text"] = "answering its teammates"
    try:
        answer = cb.run("talk", cb.role_text(kind) + cb.own_memory(kind) + TALK_RULES + briefing(kind))
    finally:
        cb.LIVE_LABEL["text"] = ""
    if cb.unavailable(answer) or answer.startswith("Claude talk"):
        return answer
    return f"{cb.MEMBERS.get(kind, kind)} answered {take(kind, answer)} message(s)"


def step(min_wait=120):
    """One conversation per pass (the watchdog calls it): the teammate whose message has waited longest, at least
    min_wait seconds (a job of its own may answer it first), answers now. Squire Qwen's messages wait for the app."""
    threads = load()
    due = []
    for t in threads:
        if t["status"] in ("used", "closed", "to the Round Table") or not t["messages"]:
            continue
        last = t["messages"][-1]
        age = time.time() - time.mktime(time.strptime(last["at"], "%Y-%m-%dT%H:%M:%S"))
        if last["to"] in TALKERS and age >= min_wait and last["from"] != last["to"]:
            due.append((last["at"], last["to"]))
    if not due:
        return "Nobody is waiting for an answer."
    return converse(sorted(due)[0][1])


def round_table():
    """The conversations the boss settles at the Round Table: threads sent there after three exchanges, and answered ones
    nobody has closed. The boss's question, the team's ask: close each as fixed, dead end or raid."""
    rows = []
    for t in load():
        if t["status"] in ("to the Round Table", "answered") and t["messages"]:
            rows.append(f"[{t['id']}] about {t['about'] or 'the work'} ({t['status']}): "
                        + " | ".join(f"{cb.MEMBERS.get(m['from'], m['from'])}: {m['text'][:240]}" for m in t["messages"][-4:]))
    if not rows:
        return ""
    return ("\n\n# The team's conversations to settle\n" + "\n".join(rows[-12:])
            + '\nClose the ones that are settled: add "close_threads": [{"thread": "T3", "outcome": "fixed | dead end | raid | answered", '
              '"why": "one line"}] to your last-line JSON.\n')


def close_from(reply):
    """The boss's closed threads from a Round Table answer."""
    try:
        last = json.loads([l for l in str(reply).strip().splitlines() if l.strip().startswith("{")][-1])
    except (IndexError, ValueError):
        return 0
    threads = load()
    closed = 0
    for c in (last.get("close_threads") or []) if isinstance(last, dict) else []:
        t = next((t for t in threads if isinstance(c, dict) and t["id"] == c.get("thread")), None)
        if t and t["status"] not in ("closed", "used"):
            t["status"], t["outcome"] = "closed", f"{c.get('outcome', 'answered')}: {str(c.get('why', ''))[:200]}"
            closed += 1
    if closed:
        save(threads)
        cb.note(f"AI TEAM Round Table closed {closed} conversation(s)")
    return closed


def steps(count=3):
    """Up to count conversations, one pass at a time (repair-runs/talk.lock, taken in one step; a lock older than 20
    minutes is from a pass that died), so two passes never answer the same message."""
    import os
    lock = cb.RUNS / "talk.lock"
    if lock.exists() and time.time() - lock.stat().st_mtime > 20 * 60:
        lock.unlink(missing_ok=True)
    try:
        os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError:
        return "Another talk pass is running."
    try:
        done = []
        for _ in range(int(count)):
            r = step()
            done.append(r)
            if r.startswith(("Nobody", "Claude")):
                break
        return "\n".join(done)
    finally:
        lock.unlink(missing_ok=True)


def report(count=8):
    """The newest threads in plain words, for the app, the realm and Blu."""
    rows = []
    for t in sorted(load(), key=lambda t: t.get("updated", ""), reverse=True)[:count]:
        rows.append(f"{t['id']} about {t['about'] or 'the work'} [{t['status']}]")
        for m in t["messages"][-4:]:
            rows.append(f"  {m['at'][11:16]} {COURT.get(m['from'], m['from'])} to {COURT.get(m['to'], m['to'])}: {m['text'][:300]}"
                        + (f" [{m['evidence'][:120]}]" if m["evidence"] else ""))
    return "\n".join(rows) or "No conversations yet."
