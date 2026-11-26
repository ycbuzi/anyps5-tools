"""Training data from Claude's sessions for the local AI (11-19, Blu: "make tools that train the local AI model to help me out a lot more like you").

Reads the Claude Code transcripts (one .jsonl per session) and writes, offline, two files in training\\:
  chat.jsonl   {"messages": [system, user, assistant]}: Blu's ask and Claude's final answer (fine-tune format)
  steps.jsonl  {"ask", "steps": [{"tool", "input", "result"}], "answer"}: how Claude worked an ask, step by step
Everything secret-shaped is masked first: IPs, e-mails, tokens/keys, package and user paths. Files that hold
secrets are never read. Re-running only adds sessions not exported yet (training\\done.txt).

usage: python export_training.py [--all] [--limit N]
"""
import json
import time
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "training"
PROJECTS = Path.home() / ".claude" / "projects"
SYSTEM = "You are Blu's AnyPS5 assistant. Work like Claude did: act with tools, verify before claiming, keep replies short, never show secrets."
MASKS = [
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "<ip>"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "<email>"),
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,}|xox[abp]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16})\b"), "<token>"),
    (re.compile(r"(?i)\b(password|passwd|pin|secret|token|apikey|api_key)\s*[:=]\s*\S+"), r"\1=<hidden>"),
    (re.compile(r"(?i)AnyPS5\.Console_[A-Za-z0-9_.]+"), "AnyPS5.Console_<pkg>"),
    (re.compile(r"(?i)U:\\Users\\[^\\\s]+\\[^\s\"']*"), "<xbox-user-path>"),
    (re.compile(r"(?i)\\Packages\\[^\\\s]+"), r"\\Packages\\<pkg>"),
    (re.compile(r"(?i)[^\s\"']*AnyPS5-work\\secrets[^\s\"']*"), "<secrets>"),
    (re.compile(r"(?i)https://discord(?:app)?\.com/api/webhooks/\S+"), "<webhook>"),
]
SKIP_RESULT = re.compile(r"(?i)secrets\\|developerfolder|BEGIN (?:RSA|OPENSSH) PRIVATE KEY")


def mask(text):
    text = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False)
    for rx, rep in MASKS:
        text = rx.sub(rep, text)
    return text


def content_text(content):
    if isinstance(content, str):
        return content
    return "\n".join(c.get("text", "") for c in content or [] if isinstance(c, dict) and c.get("type") == "text")


def is_real_ask(text):
    t = text.strip()
    return len(t) > 15 and not t.startswith(("<", "[SYSTEM", "WATCH TICK", "IMPROVE TICK", "This session is being continued", "Caveat:", "Another Claude session sent"))


def sessions(limit):
    files = sorted(PROJECTS.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[:limit] if limit else files


def export(path):
    asks, cur, pending = [], None, {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            msg = rec.get("message") or {}
            role, content = msg.get("role"), msg.get("content")
            if rec.get("type") == "user" and role == "user":
                if isinstance(content, list) and any(isinstance(c, dict) and c.get("type") == "tool_result" for c in content):
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "tool_result" and cur and c.get("tool_use_id") in pending:
                            res = content_text(c.get("content")) if not isinstance(c.get("content"), str) else c["content"]
                            step = pending.pop(c["tool_use_id"])
                            step["result"] = "<skipped>" if SKIP_RESULT.search(res or "") else mask((res or "")[:1500])
                    continue
                text = content_text(content)
                if is_real_ask(text):
                    cur = {"ask": mask(text[:4000]), "steps": [], "answer": ""}
                    asks.append(cur)
            elif rec.get("type") == "assistant" and role == "assistant" and cur:
                for c in content if isinstance(content, list) else []:
                    if c.get("type") == "tool_use":
                        inp = mask(c.get("input"))
                        if SKIP_RESULT.search(inp):
                            continue
                        step = {"tool": c.get("name"), "input": inp[:1500], "result": ""}
                        cur["steps"].append(step)
                        pending[c.get("id")] = step
                    elif c.get("type") == "text" and c.get("text", "").strip():
                        cur["answer"] = mask(c["text"][:4000])
    return [a for a in asks if a["answer"]]


def main():
    OUT.mkdir(exist_ok=True)
    done_file = OUT / "done.txt"
    done = set() if "--all" in sys.argv else set(done_file.read_text().split()) if done_file.exists() else set()
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 0
    chats = steps = new = 0
    with open(OUT / "chat.jsonl", "a", encoding="utf-8") as cf, open(OUT / "steps.jsonl", "a", encoding="utf-8") as sf, open(done_file, "a") as df:
        for path in sessions(limit):
            key = path.stem
            if key in done or time.time() - path.stat().st_mtime < 3600:
                continue
            for a in export(path):
                cf.write(json.dumps({"messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": a["ask"]}, {"role": "assistant", "content": a["answer"]}]}, ensure_ascii=False) + "\n")
                chats += 1
                if a["steps"]:
                    sf.write(json.dumps(a, ensure_ascii=False) + "\n")
                    steps += 1
            df.write(key + "\n")
            new += 1
    print("exported %d sessions: %d chat examples, %d step-by-step examples -> %s" % (new, chats, steps, OUT))


if __name__ == "__main__":
    main()
