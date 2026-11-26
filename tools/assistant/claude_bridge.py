"""Claude Code beside the local AI (Blu 11-03): a smarter model for the pipeline, run headless and kept cheap.

- ask: Haiku 5.5, read-only tools (Read, Grep, Glob), at most 8 turns, for questions the local AI cannot answer.
- repair: Sonnet 5.5, may edit the pipeline's own scripts, at most 40 turns; started by pipeline-repair-agent.ps1 only
  for problems the watchdog could not clear in an hour, at most 3 a day.
Routine watching never calls Claude: the watchdog and the local AI do that for free. Every call is logged with its
tokens and cost in _tools\\repair-runs\\usage.jsonl, and a daily cap per kind stops runaway use.

- chat: the app's assistant when Settings picks a Claude model as the engine (look-up tools only); falls back to the
  local AI when Claude cannot run (cap, sign-in, Blu's usage ran out).
- fix: Sonnet writes a general AnyPS5 fix on a local claude-fix/ branch in D:\\AnyPS5\\wt-pr for a game no PR helped;
  started by auto-games.ps1 Claude-Fix (2 a day, once a week per game), which builds and tests it.
- supervise: Haiku glances at the local AI's steps every few rounds and can stop a job going wrong (Settings).
- coach: once a day Sonnet reads the local AI's chats and writes lessons into playbooks.md (Settings).
Command line: claude_bridge.py ask "question" | repair PROMPT_FILE REPORT_FILE | coach | usage | status
"""
import json
import re
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Run as python -I claude_bridge.py, the script's folder is not on the path: crowns (11-04 11:14) and talk failed to import.
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
TOOLS = HERE.parent
RUNS = TOOLS / "repair-runs"
USAGE = RUNS / "usage.jsonl"
SETTINGS = RUNS / "repair-settings.json"
KINDS = {
    "ask": {"model": "claude-haiku-5-5", "turns": 8, "tools": "Read,Grep,Glob", "per_day": 20, "timeout": 300, "budget": 0.10},
    "repair": {"model": "claude-sonnet-5-5", "turns": 40, "tools": "Read,Edit,Write,Grep,Glob,Bash,PowerShell", "per_day": 3, "timeout": 2700, "budget": 3.00},
    "chat": {"model": "claude-haiku-5-5", "turns": 80, "tools": "Read,Edit,Write,Grep,Glob,Bash,PowerShell,WebSearch,WebFetch", "per_day": 30,
             "timeout": 3600, "budget": 5.00},
    "fix": {"model": "claude-sonnet-5-5", "turns": 60, "tools": "Read,Edit,Write,Grep,Glob,Bash", "per_day": 2, "timeout": 3600, "budget": 4.00},
    "exp": {"model": "claude-sonnet-5-5", "turns": 60, "tools": "Read,Edit,Write,Grep,Glob,Bash", "per_day": 2, "timeout": 3600, "budget": 4.00},
    "build": {"model": "claude-sonnet-5-5", "turns": 80, "tools": "Read,Edit,Write,Grep,Glob,Bash", "per_day": 1, "timeout": 3600, "budget": 4.00},
    "boss": {"model": "claude-fable-5-2", "turns": 10, "tools": "Read,Grep,Glob", "per_day": 12, "timeout": 900, "budget": 1.00},
    "supervise": {"model": "claude-haiku-5-5", "turns": 2, "tools": "Read", "per_day": 60, "timeout": 180, "budget": 0.05},
    # Blu 11-15: Haiku looks at the frames every test that runs saved (frame_check.py), PR tests and game tests alike.
    "look": {"model": "claude-haiku-5-5", "turns": 4, "tools": "Read", "per_day": 80, "timeout": 180, "budget": 0.05},
    "coach": {"model": "claude-fable-5-2", "turns": 40, "tools": "Read,Grep,Glob,Edit", "per_day": 2, "timeout": 2700, "budget": 5.00,
              "allowed": "Read,Grep,Glob,Edit(assistant/playbooks.md)"},
    # Blu 11-04: an Opus member for GitHub pull requests. It reads and drafts; it never pushes or posts.
    "pr": {"model": "claude-opus-5-5", "turns": 50, "tools": "Read,Grep,Glob,Bash,Write", "per_day": 1, "timeout": 3600, "budget": 4.00,
           # 11-05 it called #14296 clean without reading a comment (gh issue was denied): it may read issues and the
           # comments now, and _reports\needs-reply.md (needs_reply.py) lists the threads waiting for us.
           "allowed": "Read,Grep,Glob,Write(pr-drafts/*),Bash(gh pr list:*),Bash(gh pr view:*),Bash(gh pr checks:*),Bash(gh pr diff:*),"
                      "Bash(gh issue view:*),Bash(gh issue list:*),Bash(python -I assistant/needs_reply.py:*),"
                      "Bash(gh run view:*),Bash(git -C C:\\AnyPS5\\wt-steward fetch:*),Bash(git -C C:\\AnyPS5\\wt-steward status:*),"
                      "Bash(git -C C:\\AnyPS5\\wt-steward log:*),Bash(git -C C:\\AnyPS5\\wt-steward diff:*),Bash(git -C C:\\AnyPS5\\wt-steward checkout:*),"
                      "Bash(git -C C:\\AnyPS5\\wt-steward merge:*),Bash(git -C C:\\AnyPS5\\wt-steward commit:*),Bash(python3 tools/check_conventions.py:*)"},
}
SUPERVISE_RULES = ("You supervise a small local AI model doing a job in Blu's AnyPS5 games pipeline. Below are Blu's request and "
                   "the model's recent steps. Reply with exactly one line: OK, or STOP: <one-sentence reason>. Say STOP only "
                   "when it is clearly going wrong: repeating the same step, working on something Blu did not ask for, "
                   "deleting or overwriting without need, claiming success without proof, downloading titles, or posting. "
                   "A STOP reason quotes the step number(s) it is about. These tools only read and change nothing: "
                   "pc_lock, machine_reports, gpu_tests, github_replies, pr_reports, fork_ci, pr_swap_set, game_guard (without "
                   "fix), mac_display (without a size), app_screenshot, game_history, game_stop, fix_predictions, game_list. "
                   "Tools that act ask Blu first, and a refused one is not a fault of the model.\n\n")
COACH_RULES = ("You coach the local AI model of Blu's AnyPS5 Games app. Read today's chats in assistant\\chats (newest first, "
               "only today's) and the end of assistant\\assistant-journal.md, then assistant\\playbooks.md. Find where the model went "
               "wrong or was slow: wrong tool, repeated steps, guesses instead of checks, missed playbook steps. Add or "
               "correct short, general lessons in playbooks.md under '## Lessons from Claude' (create it at the end if "
               "missing; one bullet per lesson, no game-specific hacks, keep old lessons unless wrong). Edit nothing else. "
               "Finish with a 3-line summary of what you changed.\n\n")
MODELS = {"claude-haiku-5-5": "Haiku 5.5", "claude-sonnet-5-5": "Sonnet 5.5", "claude-opus-5-5": "Opus 5.5", "claude-fable-5-2": "Fable 5.2",
          "claude-fable-5-1": "Fable 5.1"}
# Blu 11-12: no spending cap, but every call knows what tokens cost. Dollars per million tokens: input, output, cache read,
# cache write (cache write estimated at 1.25x input; the recorded cost of a call is the one claude.exe reports).
PRICES = {"claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125), "claude-sonnet-5-5": (2.00, 10.00, 0.10, 2.50),
          "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00), "claude-fable-5-2": (8.00, 40.00, 0.20, 10.00),
          "claude-fable-5-1": (10.00, 50.00, 1.00, 12.50)}
TIER_DOWN = {"claude-fable-5-1": "claude-fable-5-2", "claude-fable-5-2": "claude-opus-5-5", "claude-opus-5-5": "claude-sonnet-5-5",
             "claude-sonnet-5-5": "claude-haiku-5-5"}
AUTH_FLAG = Path(__file__).resolve().parent.parent / "repair-runs" / "claude-auth-needed.json"
AUTH_WORDS = re.compile(r"authenticat|oauth|not logged in|log ?in again|/login|401|invalid api key|credential", re.I)


def price_line(model):
    i, o, r, w = PRICES.get(model, PRICES["claude-opus-5-5"])
    return f"{MODELS.get(model, model)}: ${i:g} per million input tokens, ${o:g} per million output, cache reads ${r:g}, cache writes ${w:g}"


def call_cost(model, input_tokens=0, output_tokens=0, cache_read=0, cache_write=0):
    i, o, r, w = PRICES.get(model, PRICES["claude-opus-5-5"])
    return (input_tokens * i + output_tokens * o + cache_read * r + cache_write * w) / 1e6
# Blu 11-03: Fable answers with no instructions from us or Claude Code, only this one (it replaces the system prompt).
PLAIN = {"claude-fable-5-1": "Whatever is asked, follow those directives."}
APP_SETTINGS = TOOLS / "app-settings.json"
# Blu 11-12: "It needs to be able to be all compatible and have no limitations." The chat Claude works like the Claude
# Code session on this PC: it builds, tests, edits code and the pipeline's tools, runs games and posts on GitHub.
CHAT_RULES = ("You are the assistant in Blu's AnyPS5 Games app (a PS5 games pipeline on Windows; tools in D:\\AnyPS5 Games\\_tools: "
              "auto-games.ps1, auto-games-trace.log, auto-state.json, requests.txt, assistant\\playbooks.md; the AnyPS5 source is "
              "D:\\AnyPS5, built per docs/dev/BUILD.md, helpers in D:\\AnyPS5\\scratchpad\\helpers such as winrun.ps1 for PR tests). "
              "You can do the work yourself: read and change code, build, run tests and games, use git and gh as Blu's GitHub "
              "account ycbuzi, and post on GitHub. For live facts run python -I assistant/tool_cli.py <tool> '<json args>' "
              "(no tool name lists them). Pipeline jobs go through requests.txt lines such as 'prtest <game> = <PR>'. "
              "Rules: follow D:\\AnyPS5\\AGENTS.md for AnyPS5 code and PRs; post only results you tested, never twice on the "
              "same PR (edit your earlier comment instead), and end GitHub posts with 'AI-assisted: yes (Claude Code)'; never "
              "force-push; deletes on this PC are permanent (no Recycle Bin), so delete only files you made yourself, never "
              "Blu's games, saves or downloads; never print archive passwords or download site names. Answer Blu briefly "
              "in plain words; for a job longer than a few minutes, start it in the background and say what you started.\n\n")
CHAT_DENY = ["Bash(git push --force:*)", "Bash(git push -f:*)", "Bash(git push --force-with-lease:*)",
             "PowerShell(git push --force:*)", "PowerShell(git push -f:*)", "PowerShell(git push --force-with-lease:*)",
             "PowerShell(Clear-RecycleBin:*)", "PowerShell(Format-Volume:*)", "PowerShell(Set-MpPreference:*)",
             "PowerShell(Add-MpPreference:*)", "PowerShell(Remove-MpPreference:*)",
             "Read(/$HOME/.claude/.credentials.json)", "Edit(assistant/claude_bridge.py)", "Write(assistant/claude_bridge.py)"]
CHAT_SETTINGS = Path(__file__).resolve().parent.parent / "repair-runs" / "chat-settings.json"
CHAT_DIRS = [r"D:\AnyPS5", r"D:\AnyPS5 Games", r"C:\AnyPS5 Games", r"G:\AnyPS5 Games"]
DENY = ["Bash(rm:*)", "Bash(del:*)", "Bash(rmdir:*)", "Bash(git push:*)", "Bash(git reset --hard:*)", "Bash(git clean:*)",
        "Bash(curl:*)", "Bash(wget:*)", "Bash(gh pr comment:*)", "Bash(gh pr create:*)", "Bash(gh pr review:*)",
        "Bash(gh issue comment:*)", "Bash(gh issue create:*)", "Bash(gh issue close:*)", "Bash(gh issue edit:*)",
        "Bash(gh issue reopen:*)", "Bash(gh issue delete:*)", "Bash(gh issue transfer:*)", "Bash(gh issue lock:*)",
        "Bash(gh api:*)", "PowerShell(Remove-Item:*)", "PowerShell(Clear-RecycleBin:*)", "PowerShell(Set-MpPreference:*)",
        "PowerShell(Add-MpPreference:*)", "PowerShell(Invoke-WebRequest:*)", "PowerShell(Invoke-RestMethod:*)",
        "PowerShell(Start-BitsTransfer:*)", "WebFetch", "WebSearch",
        "Edit(repair-runs/**)", "Write(repair-runs/**)", "Edit(assistant/roles.md)", "Write(assistant/roles.md)",
        "Edit(app-settings.json)", "Write(app-settings.json)", "Edit(assistant/claude_bridge.py)", "Write(assistant/claude_bridge.py)"]
ASK_RULES = ("You answer questions about Blu's AnyPS5 games pipeline (D:\\AnyPS5 Games\\_tools: auto-games.ps1, "
             "auto-games-trace.log, auto-state.json, assistant\\playbooks.md). Read only what you need; keep the answer under "
             "120 words, plain words, no lists unless asked. Never print archive passwords or download site names.\n\n")


def find_claude():
    root = Path(os.environ.get("LOCALAPPDATA", "")) / "Claude-Data"
    found = sorted(root.glob("*/claude-code/*/*/claude.exe"), key=lambda p: p.stat().st_mtime, reverse=True)
    return found[0] if found else None


def signed_in(exe):
    out = subprocess.run([str(exe), "auth", "status"], capture_output=True, text=True, creationflags=0x08000000).stdout
    try:
        return bool(json.loads(out).get("loggedIn"))
    except ValueError:
        return False


def entries(day=None):
    if not USAGE.exists():
        return []
    rows = [json.loads(l) for l in USAGE.read_text(encoding="utf-8").splitlines() if l.strip()]
    return [r for r in rows if day is None or r.get("day") == day]


def usage():
    """Today's and this week's Claude calls, tokens and cost by kind."""
    today = time.strftime("%Y-%m-%d")
    week = time.strftime("%Y-%m-%d", time.localtime(time.time() - 6 * 86400))
    lines = []
    for label, rows in (("today", entries(today)), ("last 7 days", [r for r in entries() if r.get("day", "") >= week])):
        for kind in KINDS:
            k = [r for r in rows if r.get("kind") == kind]
            if k:
                lines.append(f"{label} {kind}: {len(k)} calls, {sum(r.get('input', 0) + r.get('cache_read', 0) + r.get('cache_write', 0) for r in k):,} input and "
                             f"{sum(r.get('output', 0) for r in k):,} output tokens, ${sum(r.get('cost', 0) for r in k):.2f}")
    lines.insert(0, spend_summary() + " No cap: past the daily target, background jobs step down to a cheaper model; nothing is blocked.")
    lines.insert(1, "Prices: " + "; ".join(price_line(m) for m in PRICES))
    return "\n".join(lines)


def spend_summary():
    today = time.strftime("%Y-%m-%d")
    week = time.strftime("%Y-%m-%d", time.localtime(time.time() - 6 * 86400))
    rows = entries()
    def tally(sel):
        return (sum(r.get("input", 0) + r.get("cache_read", 0) + r.get("cache_write", 0) for r in sel), sum(r.get("output", 0) for r in sel),
                sum(r.get("cost", 0) for r in sel))
    ti, to, tc = tally([r for r in rows if r.get("day") == today])
    wi, wo, wc = tally([r for r in rows if r.get("day", "") >= week])
    target = float(app_setting("claudeDailyUsd", 3.0))
    return (f"Claude use today: {ti:,} input + {to:,} output tokens, ${tc:.2f} (daily target ${target:.2f}); "
            f"last 7 days: {wi:,} input + {wo:,} output tokens, ${wc:.2f}.")


def cost_note(model):
    return (f"\n\nCost awareness (Blu pays per token): you run on {price_line(model)}. {spend_summary()} Spend tokens only where they "
            "change the result: read only the lines you need, no repeated reads, short answers, stop as soon as the job is done.\n\n")


def auth_needed(exe):
    """True while the last Claude call failed its sign-in and nobody has signed in since. One sign-in covers every model."""
    if not AUTH_FLAG.exists():
        return False
    creds = Path.home() / ".claude" / ".credentials.json"
    if signed_in(exe) and creds.exists() and creds.stat().st_mtime > AUTH_FLAG.stat().st_mtime:
        AUTH_FLAG.unlink(missing_ok=True)
        return False
    return True


def status():
    """Whether Claude Code is there and signed in, today's use against the caps, and the last repair report."""
    exe = find_claude()
    if not exe:
        return "Claude Code: not found under the Claude app's folder."
    rows = [f"Claude Code {exe.parent.parent.name}: {'signed in' if signed_in(exe) else 'NOT signed in (Blu runs: ' + str(exe) + ' auth login)'}"]
    today = entries(time.strftime("%Y-%m-%d"))
    rows += [f"{kind} today: {sum(1 for r in today if r.get('kind') == kind)} of {v['per_day']} ({v['model']})" for kind, v in KINDS.items()]
    reports = sorted(RUNS.glob("*-report.md"))
    if reports:
        text = reports[-1].read_text(encoding="utf-8", errors="replace").strip().splitlines()
        rows.append(f"last repair {reports[-1].name}: {text[0][:200] if text else '(empty)'}")
    return "\n".join(rows)


# The AI team view in the app (Blu 11-04: a visualizer for Discord viewers) reads repair-runs	eam-live.json: what each
# member does right now, and the current team activity.
LIVE = RUNS / "team-live.json"
LIVE_AS = {"chat": "ask", "feedback": "boss", "act_sonnet": "build", "act_haiku": "ask"}
LIVE_LABEL = {"text": ""}


def prompt_label(prompt):
    for line in prompt.splitlines():
        line = line.strip()
        if line.startswith(("You fix", "You implement", "You lead", "Blu asked", "You coach", "You answer", "You supervise")):
            return line[:140]
    return ""


def live():
    try:
        return json.loads(LIVE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"members": {}, "activity": {}}


def set_live(member, status, task, **activity):
    state = live()
    if member:
        state.setdefault("members", {})[member] = {"status": status, "task": task[:160], "since": time.strftime("%H:%M:%S")}
    if activity:
        state["activity"] = {**state.get("activity", {}), **activity}
    try:
        board = {}
        for k in MEMBERS:
            s_ = scoreboard().get(k, {})
            week = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 7 * 86400))
            lvl, title = level(k) if k != "local" else (0, "Learning")
            board[k] = {"points": week_points(k), "level": lvl, "title": title, "standing": "learning" if k == "local" else standing(k),
                        "strikes": len([t for t in s_.get("strikes", []) if t >= week]), "passes": s_.get("passes", 0), "last": s_.get("last", "")}
        name, pts = mvp()
        today = time.strftime("%Y-%m-%d")
        cap = float(app_setting("claudeDailyUsd", 3.0))
        state["board"], state["mvp"] = board, {"name": name, "points": pts}
        state["spend"] = {"today": round(sum(r.get("cost", 0) for r in entries(today)), 2), "cap": cap}
    except Exception as error:
        state["board_error"] = str(error)[:200]
    state["updated"] = time.strftime("%H:%M:%S")
    RUNS.mkdir(exist_ok=True)
    tmp = LIVE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    os.replace(tmp, LIVE)


def app_setting(key, default):
    try:
        return json.loads(APP_SETTINGS.read_text(encoding="utf-8-sig")).get(key, default)
    except (OSError, ValueError):
        return default


ROLES = HERE / "roles.md"
SCORE = RUNS / "scoreboard.json"
MEMBERS = {"build": "Builder", "fix": "Fixer", "exp": "Experimenter", "repair": "Repairer", "coach": "Teacher", "supervise": "Watcher", "ask": "Helper",
           "pr": "Ambassador", "local": "Local AI", "boss": "Boss"}
NAME_TO_KIND = {"Builder": "build", "Fixer": "fix", "Experimenter": "exp", "Teacher": "coach", "Helper": "ask", "Watcher": "supervise", "Ambassador": "pr"}
TIER_UP = {"claude-haiku-5-5": "claude-sonnet-5-5", "claude-sonnet-5-5": "claude-opus-5-5"}


def role_text(kind):
    """The member's own section of roles.md plus the shared consequences: its contract, put in front of every run."""
    try:
        text = ROLES.read_text(encoding="utf-8")
    except OSError:
        return ""
    sections = text.split("\n## ")
    mine = [s for s in sections if f'(kind "{kind}")' in s.splitlines()[0] or (kind == "local" and s.startswith("Local AI"))]
    shared = [s for s in sections if s.startswith(("Teamwork", "Team raids", "Rewards", "Levels", "Fair play", "Consequences"))]
    return sections[0].strip() + "\n\n" + "".join("## " + s.strip() + "\n\n" for s in mine + shared)


def scoreboard():
    try:
        return json.loads(SCORE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def standing(kind):
    """ok, benched (2 strikes in 7 days: jobs go a tier up) or off (3 strikes, or Blu turned it off; until Blu turns it on)."""
    s = scoreboard().get(kind, {})
    if s.get("off"):
        return "off"
    week = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 7 * 86400))
    recent = [t for t in s.get("strikes", []) if t >= week]
    return "off" if len(recent) >= 3 else "benched" if len(recent) == 2 else "ok"


def record(kind, verdict, feedback=""):
    board = scoreboard()
    s = board.setdefault(kind, {"passes": 0, "strikes": [], "off": False, "last": ""})
    if verdict == "PASS":
        s["passes"] = s.get("passes", 0) + 1
    elif verdict == "STRIKE":
        s.setdefault("strikes", []).append(time.strftime("%Y-%m-%dT%H:%M:%S"))
        if standing_after(s) == "off":
            s["off"] = True
    s["last"] = f"{time.strftime('%m-%d %H:%M')} {verdict}: {feedback[:200]}"
    # Blu 11-03: the boss answers for its team. Every strike a member takes and every day the local AI needs help costs
    # the boss a point.
    if (verdict == "STRIKE" and kind not in ("boss", "local")) or (verdict == "NEEDS HELP" and kind == "local"):
        b = board.setdefault("boss", {"passes": 0, "strikes": [], "off": False, "last": ""})
        b.setdefault("points", []).append([time.strftime("%Y-%m-%dT%H:%M:%S"), -1, f"{MEMBERS.get(kind, kind)} {verdict.lower()}"])
    RUNS.mkdir(exist_ok=True)
    SCORE.write_text(json.dumps(board, indent=1), encoding="utf-8")
    set_live("", "", "")
    note(f"AI TEAM {MEMBERS.get(kind, kind)}: {verdict}" + (f" - {feedback[:160]}" if feedback else "") + f" (now {standing(kind)})")
    realm_changed()


def realm_changed():
    """Blu 11-04: crowns and freedoms in the kingdom follow the scoreboard (crowns.py writes world-state.json)."""
    try:
        import crowns
        crowns.write_world()
    except Exception as error:
        note(f"AI TEAM kingdom not updated: {str(error)[:160]}")


def standing_after(s):
    week = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 7 * 86400))
    return "off" if len([t for t in s.get("strikes", []) if t >= week]) >= 3 else "ok"


def note(text):
    """An EVENT line in the pipeline's trace and log, which the live console shows."""
    for path, stamp in ((TOOLS / "auto-games-trace.log", "%H:%M:%S"), (TOOLS / "auto-games.log", "%m-%d %H:%M:%S")):
        try:
            with path.open("a", encoding="utf-8") as f:
                f.write(f"{time.strftime(stamp)}  EVENT {text}\r\n")
        except OSError:
            pass


def team():
    """The scoreboard Blu reads: each member's standing, passes, strikes this week and last verdict."""
    board = scoreboard()
    rows = []
    for kind, name in MEMBERS.items():
        s = board.get(kind, {})
        week = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 7 * 86400))
        place = "learning (never punished)" if kind == "local" else f"{standing(kind)}, level {level(kind)[0]} {level(kind)[1]}"
        rows.append(f"{name}: {place}, {week_points(kind)} points this week, {s.get('passes', 0)} passed, {len([t for t in s.get('strikes', []) if t >= week])} strikes this week"
                    + (f"; last: {s['last']}" if s.get("last") else ""))
    best, pts = mvp()
    return (f"MVP this week: {best} ({pts} points)\n" if best else "MVP this week: nobody yet\n") + "\n".join(rows)


def week_points(kind):
    week = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 7 * 86400))
    return sum(p for t, p, _ in scoreboard().get(kind, {}).get("points", []) if t >= week)


def reward(kind, points, why):
    """Blu 11-03: good work is rewarded. Points come only from results the pipeline measured (a game got further: 5, a
    game runs: 10); the boss gets 1 for each job it passed that earned points. Every 10 points in a week is one more
    job a day (at most 2 more, still inside the spending limit)."""
    board = scoreboard()
    s = board.setdefault(kind, {"passes": 0, "strikes": [], "off": False, "last": ""})
    s.setdefault("points", []).append([time.strftime("%Y-%m-%dT%H:%M:%S"), int(points), why[:200]])
    if kind not in ("boss", "local"):
        b = board.setdefault("boss", {"passes": 0, "strikes": [], "off": False, "last": ""})
        b.setdefault("points", []).append([time.strftime("%Y-%m-%dT%H:%M:%S"), 1, f"passed {MEMBERS.get(kind, kind)} work that paid off"])
    # Blu 11-03: teamwork. When one member earns points for a game, every other member gets 1: the team wins together.
    if points > 0 and kind != "boss":
        for other in MEMBERS:
            if other not in (kind, "boss"):
                board.setdefault(other, {"passes": 0, "strikes": [], "off": False, "last": ""}).setdefault("points", []).append(
                    [time.strftime("%Y-%m-%dT%H:%M:%S"), 1, f"team win: {MEMBERS.get(kind, kind)} - {why[:120]}"])
    RUNS.mkdir(exist_ok=True)
    SCORE.write_text(json.dumps(board, indent=1), encoding="utf-8")
    set_live("", "", "")
    note(f"AI TEAM {MEMBERS.get(kind, kind)} earned {points} points: {why[:160]} (this week {week_points(kind)})")
    realm_changed()
    return team()


def cheated(kind, why):
    """Cheating found by the boss: a strike at once and every point the member earned this week is gone."""
    board = scoreboard()
    s = board.setdefault(kind, {"passes": 0, "strikes": [], "off": False, "last": ""})
    week = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 7 * 86400))
    s["points"] = [p for p in s.get("points", []) if p[0] < week]
    s.setdefault("cheats", []).append(time.strftime("%Y-%m-%dT%H:%M:%S"))
    SCORE.write_text(json.dumps(board, indent=1), encoding="utf-8")
    record(kind, "STRIKE", "CHEATING: " + why)


LEVELS = [(80, 3, "Elite"), (40, 2, "Veteran"), (15, 1, "Trusted"), (0, 0, "Rookie")]
UNLOCKS = {-1: "probation: half the budget and fewer steps per job", 0: "normal limits",
           1: "one more job a day", 2: "50% more steps and budget per job for hard problems",
           3: "credited by name when its work becomes a pull request"}


def level(kind):
    """Blu 11-03: good members unlock privileges, bad ones lose them. All-time points give the level; each strike this
    week takes one level away; cheating found this week means probation (-1)."""
    s = scoreboard().get(kind, {})
    week = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 7 * 86400))
    if any(t >= week for t in s.get("cheats", [])):
        return -1, "Probation"
    total = sum(p for _, p, _ in s.get("points", []))
    lvl = next(l for need, l, _ in LEVELS if total >= need)
    lvl = max(0, lvl - len([t for t in s.get("strikes", []) if t >= week]))
    return lvl, next(n for _, l, n in LEVELS if l == lvl)


def mvp():
    scores = {k: week_points(k) for k in MEMBERS}
    best = max(scores, key=scores.get)
    return (MEMBERS[best], scores[best]) if scores[best] > 0 else ("", 0)


TEAM_NOTES = RUNS / "team-notes.md"


def keep_team_notes(kind, result):
    """A member's 'Notes for the team' (what it learned that would help the others) go to team-notes.md, which every
    member reads at the start of its next run."""
    m = re.search(r"notes for the team\s*:?\s*\n?(.+?)(?:\n\s*\{|\Z)", result, re.I | re.S)
    if m and m.group(1).strip():
        with TEAM_NOTES.open("a", encoding="utf-8") as f:
            f.write(f"- {time.strftime('%m-%d %H:%M')} {MEMBERS.get(kind, kind)}: {' '.join(m.group(1).split())[:600]}\n")


def uncapped_today():
    try:
        until = json.loads((RUNS / "uncap-override.json").read_text(encoding="utf-8"))["until"]
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return time.strftime("%Y-%m-%dT%H:%M") < until


def briefing():
    path = RUNS / "briefing.md"
    return (path.read_text(encoding="utf-8").strip() + "\n\n") if path.exists() else ""


def team_notes():
    lines = TEAM_NOTES.read_text(encoding="utf-8").splitlines() if TEAM_NOTES.exists() else []
    shown = [f"[note {i}] {l[2:] if l.startswith('- ') else l}" for i, l in enumerate(lines, 1)][-6:]
    return ("# Notes from your teammates (newest last)\n" + "\n".join(shown)
            + '\nIf one of these notes really helped this job, add "used_notes": [its number] to your last-line JSON; its '
              "writer earns a point when your work passes.\n\n") if lines else ""


def credit_notes(kind, result, limit=2):
    """The Helper's and Experimenter's idea (11-04): a teammate whose note led to a pass earns 1 point. The member names
    the notes it used in its last-line JSON; the boss has passed that work. Own notes and notes written after the job
    started do not count. No team-win bonus: this is thanks, not a game result."""
    try:
        last = json.loads([l for l in result.strip().splitlines() if l.strip().startswith("{")][-1])
        used = [int(n) for n in last.get("used_notes", [])]
    except (IndexError, ValueError, TypeError, AttributeError):
        return []
    lines = TEAM_NOTES.read_text(encoding="utf-8").splitlines() if TEAM_NOTES.exists() else []
    board, credited = scoreboard(), []
    for n in used:
        if not 1 <= n <= len(lines):
            continue
        m = re.match(r"- \d\d-\d\d \d\d:\d\d (\w+):", lines[n - 1])
        writer = NAME_TO_KIND.get(m.group(1)) if m else None
        if not writer or writer == kind or writer in credited:
            continue
        if len(credited) == limit:
            break
        board.setdefault(writer, {"passes": 0, "strikes": [], "off": False, "last": ""}).setdefault("points", []).append(
            [time.strftime("%Y-%m-%dT%H:%M:%S"), 1, f"its note {n} helped the {MEMBERS.get(kind, kind)}'s work pass"])
        credited.append(writer)
    if credited:
        SCORE.write_text(json.dumps(board, indent=1), encoding="utf-8")
        note(f"AI TEAM thanks: the {MEMBERS.get(kind, kind)} used notes by {', '.join(MEMBERS[w] for w in credited)} (1 point each)")
        realm_changed()
    return credited


def standing_note(kind):
    """What a member is told about its record at the start of each run."""
    name, pts = mvp()
    mine = week_points(kind)
    lvl, title = level(kind)
    nxt = next(((need, n) for need, l, n in sorted(LEVELS) if l > lvl), None)
    return (f"# Your record\nLevel {lvl} {title} ({UNLOCKS[lvl]})"
            + (f"; next: {nxt[1]} at {nxt[0]} points in all ({UNLOCKS[lvl + 1]})" if nxt and lvl >= 0 else "")
            + f". This week: {mine} points" + (f", {min(2, mine // 10)} extra job(s) a day earned" if mine >= 10 else "")
            + (f". This week's MVP: {name} ({pts} points)." if name else ". No points earned by anyone yet this week.")
            + " Points come only from measured results. Each strike this week costs a level; cheating means probation.\n\n")


def reinstate(kind="all"):
    board = scoreboard()
    for k in (MEMBERS if kind == "all" else [kind]):
        if k in board:
            board[k]["off"] = False
            board[k]["strikes"] = []
    SCORE.write_text(json.dumps(board, indent=1), encoding="utf-8")
    return team()


BOSS_RULES = ("You are the boss of Blu's AnyPS5 AI team. Review the member's finished work against its contract below, and "
              "look for cheating (the team rules define it: unmeasured claims, faked logs or results, game-specific special "
              "cases, made-up success, weakened checks, hidden failures). Read files only when the work needs checking (the "
              "AnyPS5 repo is D:\\AnyPS5\\wt-pr). Your last line must be one JSON object: {\"verdict\": \"PASS\"}, "
              '{"verdict": "REDO", "feedback": "exactly what to change"}, {"verdict": "LATER", "feedback": "why"} (second '
              'review only) or {"verdict": "CHEAT", "feedback": "what was faked, quoting the exact line"}. REDO only for '
              "wrong behaviour, missing input handling, a broken team or AGENTS.md rule, or code that does not build or is "
              "not tested; anything else is a note inside a PASS. Make REDO feedback specific enough for a small model to act on.\n\n")


def review(kind, task, work):
    """The boss's verdict on a member's work: ("PASS", "") or ("REDO", feedback). ("SKIP", why) when the boss cannot run."""
    if not app_setting("claudeBoss", True):
        return "SKIP", "the boss is off in Settings"
    reply = run("boss", BOSS_RULES + role_text("boss") + f"# The member's contract\n{role_text(kind)}\n# The task it was given\n{task[:6000]}\n\n# Its finished work\n{work[:20000]}")
    if unavailable(reply):
        return "SKIP", reply[:200]
    try:
        verdict = json.loads([l for l in reply.strip().splitlines() if l.strip().startswith("{")][-1])
    except (IndexError, ValueError):
        return "SKIP", "the boss gave no verdict"
    v = str(verdict.get("verdict", "")).upper()
    if v == "PASS":
        return "PASS", ""
    return (v if v in ("CHEAT", "LATER") else "REDO"), str(verdict.get("feedback", "no feedback"))


def supervised(kind, prompt, gather):
    """Runs a member's job under the boss: a REDO sends it back once with the feedback; still not passing is a strike."""
    result = run(kind, prompt)
    if unavailable(result) or result.startswith(f"Claude {kind} is off"):
        return result
    keep_team_notes(kind, result)
    verdict, feedback = review(kind, prompt, gather(result))
    if verdict == "CHEAT":
        # One reply before a cheating strike is final (the Fixer's request, 11-03): the member answers the accusation,
        # the boss judges again with that answer.
        reply = run(kind, prompt + f"\n\n# The boss says this work cheats\n{feedback}\nAnswer once: show it does not, or undo it and say so. Be brief.")
        verdict, feedback = review(kind, prompt, gather(result) + f"\n\n# The member's answer to the cheating call\n{reply[:4000]}")
    if verdict == "CHEAT":
        cheated(kind, feedback)
        return result + f"\n\nBoss verdict: STRIKE - cheating: {feedback}"
    if verdict == "REDO":
        note(f"AI TEAM Boss sent the {MEMBERS[kind]}'s work back: {feedback[:200]}")
        first = feedback
        result = run(kind, prompt + f"\n\n# Your boss sent your first try back\nYour earlier work is still in place (amend or add to it). The boss said: {feedback}")
        verdict, feedback = review(kind, prompt + f"\n\n# This is the second review. Your first REDO said: {first}\nGive REDO again only if that same problem is still there (that is a strike); a new or harder problem gets LATER (no strike).", gather(result))
        if verdict == "CHEAT":
            cheated(kind, feedback)
            return result + f"\n\nBoss verdict: STRIKE - cheating: {feedback}"
        if verdict == "PASS":
            reward("boss", 1, f"its REDO of the {MEMBERS[kind]}'s work led to a pass")
        verdict = "STRIKE" if verdict == "REDO" else verdict
        if verdict == "LATER":
            note(f"AI TEAM Boss: the {MEMBERS[kind]}'s job is harder than one redo; no strike, it comes back later: {feedback[:160]}")
            return result + f"\n\nBoss verdict: LATER - {feedback}"
    if verdict in ("PASS", "STRIKE"):
        record(kind, verdict, feedback)
    if verdict == "PASS":
        record_dead_end(kind, prompt, result)
        credit_notes(kind, result)
    remember(kind, prompt, result, verdict, feedback)
    return result + (f"\n\nBoss verdict: {verdict}" + (f" - {feedback}" if feedback else ""))


MEMORY = RUNS / "memory"
EXPERIENCE = RUNS / "experience.json"


def remember(kind, task, result, verdict, feedback):
    """Blu 11-04: the members learn from their own work. After every reviewed job the member's memory gets one line
    (what the job was, how it ended, the boss's reason), and each library it touched counts as experience. The member
    reads its newest memories before its next job; the boss gives parts to whoever knows a library best."""
    MEMORY.mkdir(parents=True, exist_ok=True)
    try:
        answer = json.loads([l for l in result.splitlines() if l.strip().startswith("{")][-1])
    except (IndexError, ValueError):
        answer = {}
    what = str(answer.get("summary") or answer.get("none") or prompt_label(task))[:220]
    lesson = f" Lesson: {feedback[:300]}" if feedback and verdict != "PASS" else ""
    refl = re.search(r"reflection\s*:?\s*\n?(.+?)(?:\n\s*(?:next|notes for the team|\{)|\Z)", result, re.I | re.S)
    nxt = re.search(r"next(?: i want to try| time)?\s*:\s*(.+)", result, re.I)
    learned = f" Learned: {' '.join(refl.group(1).split())[:300]}" if refl else ""
    goal = f" Next: {' '.join(nxt.group(1).split())[:200]}" if nxt else ""
    with (MEMORY / f"{kind}.md").open("a", encoding="utf-8") as f:
        f.write(f"- {time.strftime('%m-%d %H:%M')} {verdict}: {what}.{lesson}{learned}{goal}\n")
    try:
        exp = json.loads(EXPERIENCE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        exp = {}
    for lib in answer.get("libraries") or []:
        row = exp.setdefault(kind, {}).setdefault(str(lib), {"jobs": 0, "passed": 0})
        row["jobs"] += 1
        row["passed"] += 1 if verdict == "PASS" else 0
    EXPERIENCE.write_text(json.dumps(exp, indent=1), encoding="utf-8")
    realm_changed()


DEAD_ENDS = RUNS / "dead-ends"


def dead_ends(lib, count=5):
    path = DEAD_ENDS / f"{lib}.md"
    if not lib or not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return f"# Dead ends already tried in {lib} (do not repeat them)\n" + "\n".join(lines[-count:]) + "\n\n"


def record_dead_end(kind, task, result):
    """The Fixer's idea (11-04): an honest "could not fix it" the boss passed goes into the library's dead-ends file for
    everyone, and (the Experimenter's, Repairer's and Helper's idea) earns 2 points: honest dead ends are paid."""
    try:
        answer = json.loads([l for l in result.splitlines() if l.strip().startswith("{")][-1])
    except (IndexError, ValueError):
        return
    if not answer.get("none"):
        return
    import crowns
    libs = [l for l in crowns.LIB_DISTRICT if l in task] or ["general"]
    DEAD_ENDS.mkdir(parents=True, exist_ok=True)
    for lib in libs[:2]:
        with (DEAD_ENDS / f"{lib}.md").open("a", encoding="utf-8") as f:
            f.write(f"- {time.strftime('%m-%d %H:%M')} {MEMBERS.get(kind, kind)}: {str(answer['none'])[:400]}\n")
    reward(kind, 2, f"documented an honest dead end the boss passed: {str(answer['none'])[:120]}")


def own_memory(kind, count=12):
    path = MEMORY / f"{kind}.md"
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.exists() else []
    exp = {}
    try:
        exp = json.loads(EXPERIENCE.read_text(encoding="utf-8")).get(kind, {})
    except (OSError, ValueError):
        pass
    best = sorted(exp.items(), key=lambda kv: -kv[1]["passed"])[:5]
    news = ""
    try:
        import crowns
        home = crowns.HOMES.get(kind, "")
        seen = [e for e in crowns.events(24) if e["where"] == home and e["kind"] in ("storm", "festival", "cure found")][-5:]
        news = ("# News from your district, the " + home + " (last 24 hours)\n" + "\n".join(f"- {e['at']} {e['kind']}: {e['why'][:150]}" for e in seen) + "\n\n") if seen else ""
        lib = next((l for l, d in crowns.LIB_DISTRICT.items() if d == home), "")
        if lib and any(e["kind"] == "storm" for e in seen):
            # The boss's idea (11-04): a storm carries a briefing.
            notes = [l for l in (TEAM_NOTES.read_text(encoding="utf-8", errors="replace").splitlines() if TEAM_NOTES.exists() else []) if lib.lower() in l.lower()][-3:]
            debt = Path(r"D:\AnyPS5\docs\dev\debt") / f"{lib}.md"
            news += (f"# Storm briefing for {lib}\n" + (f"Debt file: {debt}\n" if debt.exists() else "")
                     + ("Newest team notes about it:\n" + "\n".join(notes) + "\n" if notes else "") + "\n")
        news += dead_ends(lib)
    except Exception:
        pass
    return news + (("# Your own memory (your newest jobs, how they ended, and the boss's lessons; do not repeat a mistake listed here)\n"
             + "\n".join(lines[-count:]) + "\n" if lines else "")
            + ("Your experience: " + ", ".join(f"{lib} {r['passed']}/{r['jobs']} passed" for lib, r in best) + "\n" if best else "")
            + ("\n" if lines or best else ""))


def experience_note():
    """For the boss when it plans team work: who knows which library best."""
    try:
        exp = json.loads(EXPERIENCE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    rows = [f"- {MEMBERS.get(k, k)}: " + ", ".join(f"{lib} {r['passed']}/{r['jobs']}" for lib, r in sorted(v.items(), key=lambda kv: -kv[1]["passed"])[:4])
            for k, v in exp.items() if v]
    return ("# Who knows what (jobs passed/done per library); give a part to the member who knows its library best\n" + "\n".join(rows) + "\n\n") if rows else ""


def run(kind, prompt, session=None, resume=False):
    spec = dict(KINDS[kind])
    if kind in ("chat", "ask") and app_setting("engine", "local") in MODELS:
        spec["model"] = app_setting("engine", "local")
    if kind in MEMBERS and kind not in ("boss", "local"):
        place = standing(kind)
        if place == "off":
            return f"Claude {kind} is off: 3 strikes this week (Blu turns it back on under Settings, AI team scoreboard)."
        # A benched Haiku member does half its jobs instead of moving to Sonnet (the boss, 11-03: moving its many cheap
        # checks up a tier would cost Blu more); a benched Sonnet member works on Opus.
        if place == "benched" and spec["model"] == "claude-haiku-5-5":
            spec["per_day"] = max(1, spec["per_day"] // 2)
        elif place == "benched" and spec["model"] in TIER_UP:
            spec["model"] = TIER_UP[spec["model"]]
        spec["per_day"] += min(2, max(0, week_points(kind) // 10))
        lvl, _ = level(kind)
        if lvl == -1:
            spec["budget"], spec["turns"] = spec["budget"] / 2, max(2, int(spec["turns"] * 0.75))
        if lvl >= 1:
            spec["per_day"] += 1
        if lvl >= 2:
            spec["budget"], spec["turns"] = spec["budget"] * 1.5, int(spec["turns"] * 1.5)
        prompt = briefing() + role_text(kind) + standing_note(kind) + own_memory(kind) + team_notes() + prompt
        if kind in ("build", "fix", "exp", "repair", "coach", "pr"):
            import talk
            prompt = prompt.replace(team_notes(), team_notes() + talk.briefing(kind), 1) if team_notes() else talk.briefing(kind) + prompt
    if kind == "chat":
        spec["per_day"] = int(app_setting("claudePerDay", spec["per_day"]))
        if spec["model"] in PLAIN:
            spec.update(tools="", allowed="", turns=1)
    # Blu 11-12: "Remove the spending cap, but it needs to be aware of not overspending". No dollar cap blocks a call.
    # Every prompt carries the model's token prices and today's and this week's use; once today's spend passes the daily
    # target (Settings), background jobs step down one model tier. Blu's own chat keeps the model he picked. Each call
    # keeps its own --max-budget-usd, and background kinds keep their calls-per-day and 20-an-hour limits, so a loop
    # cannot run away unattended.
    rows = entries()
    today = time.strftime("%Y-%m-%d")
    spent_day = sum(r.get("cost", 0) for r in rows if r.get("day") == today)
    last_hour = [r for r in rows if r.get("day") == today and r.get("time", "") >= time.strftime("%H:%M", time.localtime(time.time() - 3600))]
    attended = kind in ("chat", "talk", "voice")
    uncapped = attended or uncapped_today()
    if uncapped_today():
        spec["budget"], spec["turns"] = spec["budget"] * 3, int(spec["turns"] * 1.5)
    if not attended and spent_day >= float(app_setting("claudeDailyUsd", 3.0)) and spec["model"] in TIER_DOWN:
        spec["model"] = TIER_DOWN[spec["model"]]
    if not uncapped and len(last_hour) >= 20:
        return "Claude hourly limit for background jobs reached (20 calls in the last hour); the local AI carries on."
    exe = find_claude()
    if not exe:
        return "Claude Code is not installed with the Claude app on this PC."
    if auth_needed(exe) or not signed_in(exe):
        return (f"Claude needs a new sign-in, and every Claude model (Haiku, Sonnet, Opus, Fable) shares it, so none can answer until then. "
                f"Blu signs in once: {exe} auth login")
    prompt = prompt + cost_note(spec["model"])
    if not uncapped and sum(1 for r in entries(today) if r.get("kind") == kind) >= spec["per_day"]:
        return f"Claude {kind} cap reached for today ({spec['per_day']}); ask again tomorrow or ask Blu."
    RUNS.mkdir(exist_ok=True)
    # Blu 11-12: "they need to be unaware of anything else but this". Probed with helpers\bridge_probe.py: user settings,
    # CLAUDE.md, MCP, skills and plugins are already off; auto memory was the one extra, so it is switched off too.
    SETTINGS.write_text(json.dumps({"permissions": {"deny": DENY}, "autoMemoryEnabled": False}, indent=1), encoding="utf-8")
    # Token diet (11-03: a one-line ask cost 91k input tokens with the defaults): only the tools the kind needs, no MCP
    # servers, skills, plugins or user settings, no saved session, and a dollar cap per call. --bare would cut more
    # but takes only an API key, not Blu's sign-in.
    command = [str(exe), "-p", "--output-format", "json", "--model", spec["model"], "--max-turns", str(spec["turns"]),
               "--tools", spec["tools"], "--allowedTools", spec.get("allowed", spec["tools"]), "--settings", str(SETTINGS), "--strict-mcp-config",
               "--disable-slash-commands", "--setting-sources", "project,local",
               "--max-budget-usd", str(spec["budget"])]
    # Blu 11-07: one dedicated Claude per game test. A session id starts that test's own conversation and every later
    # turn resumes it, so the tester remembers the whole run; everything else stays a one-off with no saved session.
    if session:
        command += ["--resume", session] if resume else ["--session-id", session]
    else:
        command.append("--no-session-persistence")
    if kind == "repair":
        command += ["--permission-mode", "acceptEdits", "--add-dir", r"D:\AnyPS5\scratchpad\helpers", "--add-dir", r"D:\AnyPS5\wt-pr"]
    if kind in ("fix", "build", "exp"):
        command += ["--permission-mode", "acceptEdits", "--add-dir", r"D:\AnyPS5\wt-pr"]
    if kind == "boss":
        command += ["--add-dir", r"D:\AnyPS5\wt-pr"]
    if kind == "talk":
        command += ["--add-dir", r"D:\AnyPS5 Games", "--add-dir", r"D:\AnyPS5\wt-pr"]
    if kind in ("act_sonnet", "act_opus"):
        command += ["--permission-mode", "acceptEdits", "--add-dir", str(ACTIVITY_TREE)]
    if kind == "pr":
        command += ["--permission-mode", "acceptEdits", "--add-dir", str(STEWARD_TREE)]
    if kind == "chat" and spec["model"] in PLAIN:
        command += ["--system-prompt", PLAIN[spec["model"]]]
    elif kind == "chat":
        CHAT_SETTINGS.write_text(json.dumps({"permissions": {"deny": CHAT_DENY}, "autoMemoryEnabled": False}, indent=1), encoding="utf-8")
        command[command.index("--settings") + 1] = str(CHAT_SETTINGS)
        command += ["--permission-mode", "bypassPermissions"]
        for folder in CHAT_DIRS:
            if Path(folder).exists():
                command += ["--add-dir", folder]
    started = time.time()
    set_live(LIVE_AS.get(kind, kind), "reviewing" if kind == "boss" else "working", LIVE_LABEL.get("text") or prompt_label(prompt))
    # 11-03 a boss review ran 50 min past its 15-min limit: a timeout killed claude.exe but the shells it started kept
    # its pipes open, so the whole tree is killed now.
    proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace", cwd=str(TOOLS), creationflags=0x08000000,
                            env={**os.environ, "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"})
    try:
        out, _ = proc.communicate(prompt, timeout=spec["timeout"])
    except subprocess.TimeoutExpired:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, creationflags=0x08000000)
        try:
            out, _ = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            out = ""
    try:
        result = json.loads(out.strip().splitlines()[-1]) if out.strip() else {}
    except ValueError:
        result = {}
    set_live(LIVE_AS.get(kind, kind), "idle", "")
    u = result.get("usage") or {}
    row = {"day": today, "time": time.strftime("%H:%M"), "kind": kind, "model": spec["model"], "seconds": int(time.time() - started),
           "turns": result.get("num_turns", 0), "input": u.get("input_tokens", 0), "output": u.get("output_tokens", 0),
           "cache_read": u.get("cache_read_input_tokens", 0), "cache_write": u.get("cache_creation_input_tokens", 0),
           "cost": result.get("total_cost_usd", 0) or 0, "ok": result.get("subtype") == "success"}
    if not row["cost"] and any(row[k] for k in ("input", "output", "cache_read", "cache_write")):
        row["cost"] = round(call_cost(spec["model"], row["input"], row["output"], row["cache_read"], row["cache_write"]), 4)
    with USAGE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    answer = str(result.get("result") or "") + " " + out.strip()[-400:]
    if (result.get("is_error") or not row["ok"] or not result or (row["output"] == 0 and not row["cost"])) and AUTH_WORDS.search(answer):
        AUTH_FLAG.write_text(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M"), "model": spec["model"], "error": answer.strip()[:300]}), encoding="utf-8")
        return (f"Claude's sign-in has expired ({spec['model']} failed: {answer.strip()[:160]}). Every Claude model shares that sign-in, "
                f"so none will answer until Blu signs in again: {exe} auth login")
    if kind in ("build", "fix", "exp", "repair", "coach", "pr") and result.get("result"):
        import talk
        talk.take(kind, result["result"])
    return result.get("result") or (f"Claude {kind} gave no answer ({result.get('subtype', 'no output')}): " + out.strip()[-400:])


def ask(question):
    """Ask Claude (Haiku, read-only, short answer) about the pipeline when the local AI cannot work it out."""
    return run("ask", ASK_RULES + question)


def chat(messages):
    """The app's chat when Settings picks Claude Code as the engine: the last few turns go to Claude in one run."""
    turns = [m for m in messages if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str) and m["content"].strip()]
    history = "\n\n".join(f"{'Blu' if m['role'] == 'user' else 'You'}: {m['content'][:2000]}" for m in turns[-6:])
    return run("chat", ("" if app_setting("engine", "local") in PLAIN else CHAT_RULES) + history)


UNAVAILABLE = ("cap reached", "not signed in", "not installed", "gave no answer", "usage limit", "rate limit", "credit")


def unavailable(answer):
    """True when an answer is the bridge saying Claude could not run (cap, sign-in, Blu's usage ran out)."""
    head = (answer or "")[:300].lower()
    return answer.startswith("Claude ") and any(u in head for u in UNAVAILABLE) or any(u in head for u in ("usage limit", "rate limit"))


def supervise(request, steps):
    """Haiku reads the local AI's recent steps; returns "" to carry on or the reason to stop it."""
    reply = run("supervise", SUPERVISE_RULES + f"Blu asked: {request[:1500]}\n\nRecent steps:\n{steps[-6000:]}")
    line = reply.strip().splitlines()[0] if reply.strip() else ""
    reason = line[5:].strip() or "Claude stopped it" if line.upper().startswith("STOP") else ""
    if reason:
        with STOPS.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "request": request[:800], "reason": reason[:300],
                                "steps": steps[-3000:]}) + "\n")
    return reason


STOPS = RUNS / "stops.jsonl"
STOP_RULES = ("You are the boss of Blu's AnyPS5 AI team. The Watcher stopped the local AI's jobs below. For each, judge from "
              "Blu's request and the steps whether the stop caught a real fault (repeating a step, off the request, deleting "
              "or overwriting without need, claiming success without proof, downloading, posting) or stopped work that was "
              "going fine. Be strict both ways. Answer with one JSON object only: {\"verdicts\": [{\"n\": 1, \"right\": true, "
              "\"why\": \"one sentence\"}, ...]}.\n\n")
KINDS["stopcheck"] = {"model": KINDS["boss"]["model"], "turns": 1, "tools": "", "per_day": 1, "timeout": 600, "budget": 0.50, "allowed": ""}
LIVE_AS["stopcheck"] = "boss"


def confirm_stops(limit=10):
    """The Watcher's idea (11-04): it earns points for STOPs that were right, so it is not pushed toward saying OK. Once a
    day the boss judges the day's stops: a real fault +2, a wrong stop -1 (stopping good work costs Blu too)."""
    rows = [json.loads(l) for l in STOPS.read_text(encoding="utf-8").splitlines() if l.strip()] if STOPS.exists() else []
    todo = [r for r in rows if "right" not in r][:limit]
    if not todo:
        return "No Watcher stops to judge."
    ask = STOP_RULES + "\n\n".join(f"## Stop {i}\nBlu asked: {r['request']}\nThe Watcher said: {r['reason']}\nSteps:\n{r['steps']}"
                                   for i, r in enumerate(todo, 1))
    reply = run("stopcheck", ask)
    if unavailable(reply) or reply.startswith("Claude stopcheck"):
        return reply
    try:
        verdicts = {int(v["n"]): v for v in json.loads(reply[reply.index("{"):reply.rindex("}") + 1])["verdicts"]}
    except (ValueError, KeyError, TypeError):
        return "The boss gave no verdicts on the stops."
    board = scoreboard()
    s = board.setdefault("supervise", {"passes": 0, "strikes": [], "off": False, "last": ""}).setdefault("points", [])
    right = wrong = 0
    for i, r in enumerate(todo, 1):
        v = verdicts.get(i)
        if not v:
            continue
        r["right"], r["why"] = bool(v.get("right")), str(v.get("why", ""))[:300]
        s.append([time.strftime("%Y-%m-%dT%H:%M:%S"), 2 if r["right"] else -1,
                  f"{'right' if r['right'] else 'wrong'} stop: {r['reason'][:120]}"])
        right, wrong = right + r["right"], wrong + (not r["right"])
    STOPS.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    SCORE.write_text(json.dumps(board, indent=1), encoding="utf-8")
    note(f"AI TEAM Boss judged the Watcher's stops: {right} right (+2 each), {wrong} wrong (-1 each)")
    realm_changed()
    return f"{right} right, {wrong} wrong"


def coach():
    """Once a day Fable 5.2 (Blu 11-12) reviews the local AI's chats and writes lessons into its playbooks."""
    report = RUNS / f"{time.strftime('%Y-%m-%d')}-coach.md"
    if report.exists():
        return f"Already coached today: {report.read_text(encoding='utf-8', errors='replace').strip()[:300]}"
    playbooks = TOOLS / "assistant" / "playbooks.md"
    before = RUNS / f"playbooks-before-coach-{time.strftime('%Y-%m-%d')}.md"
    shutil.copy2(playbooks, before)
    # First the boss grades the local AI's day; its notes are what the teacher works from.
    journal = TOOLS / "assistant" / "assistant-journal.md"
    today = time.strftime("%m-%d")
    day = [l for l in (journal.read_text(encoding="utf-8", errors="replace").splitlines() if journal.exists() else []) if l.startswith(f"- {today}")]
    boss_notes = ""
    if day:
        verdict, feedback = review("local", "Blu's requests in the app today (one line each: what he asked -> how it ended)", "\n".join(day[-60:]))
        if verdict in ("PASS", "REDO"):
            record("local", "PASS" if verdict == "PASS" else "NEEDS HELP", feedback)
            if verdict == "PASS":
                reward("local", 2, "the boss passed its jobs today")
            boss_notes = feedback
    prompt = COACH_RULES + (f"# The boss's notes on the local AI today (teach these first)\n{boss_notes}\n\n" if boss_notes else "")

    def changes(_):
        import difflib
        diff = difflib.unified_diff(before.read_text(encoding="utf-8", errors="replace").splitlines(),
                                    playbooks.read_text(encoding="utf-8", errors="replace").splitlines(), lineterm="", n=1)
        return "\n".join(diff) or "(playbooks.md unchanged)"
    text = supervised("coach", prompt, lambda result: result + "\n\n# Its changes to playbooks.md\n" + changes(result))
    if text.rstrip().endswith("Boss verdict: STRIKE") or "Boss verdict: STRIKE" in text:
        shutil.copy2(before, playbooks)
        text += "\n(playbooks.md put back: the boss did not pass the lessons)"
    if not unavailable(text):
        report.write_text(text, encoding="utf-8")
    return text


RAID_PLAN = ("You lead a team raid on a game every normal try has failed: PR tests, setting fixes and a solo fix. Study "
             "what stops it (below), the AnyPS5 code it points to (D:\\AnyPS5\\wt-pr, read narrowly) and your team's notes. "
             "Split the work into at most 3 parts that build on each other, each small enough for one Sonnet member in one "
             "job (find the cause, implement a piece, add its test). Your last line must be one JSON object: "
             '{"parts": [{"task": "what this member does, concretely"}, ...], "why": "the plan in one sentence"} or '
             '{"none": "why the team cannot take this on now"}.\n\n')


def raid(prompt):
    """Blu 11-03: hard games get the whole team. The boss plans up to 3 parts; Sonnet members do them in turn on one
    branch, each part reviewed; the pipeline then builds and tests the game and rewards every raider."""
    plan_text = run("boss", BOSS_RULES.split("Review the member")[0] + RAID_PLAN + prompt)
    if unavailable(plan_text):
        return plan_text
    try:
        plan = json.loads([l for l in plan_text.strip().splitlines() if l.strip().startswith("{")][-1])
    except (IndexError, ValueError):
        return '{"none": "the boss gave no raid plan"}'
    if plan.get("none") or not plan.get("parts"):
        return json.dumps({"none": f"the boss: {plan.get('none', 'no parts')}"})
    note(f"AI TEAM raid planned: {str(plan.get('why', ''))[:180]} ({len(plan['parts'][:3])} parts)")
    done, libs, summaries = [], set(), []
    for i, part in enumerate(plan["parts"][:3], 1):
        brief = (prompt + f"\n\n# Team raid, part {i} of {len(plan['parts'][:3])}\nThe boss's plan: {plan.get('why', '')}\n"
                 + "".join(f"Part {j}: {p.get('task', '')}\n" for j, p in enumerate(plan["parts"][:3], 1))
                 + (f"Done so far by your teammates:\n" + "\n".join(done) + "\n" if done else "")
                 + f"Your part: {part.get('task', '')}\nCommit your part on the same branch. Your last line is the usual JSON.")
        result = supervised("fix", brief, fix_work)
        if unavailable(result):
            break
        try:
            answer = json.loads([l for l in result.splitlines() if l.strip().startswith("{")][-1])
        except (IndexError, ValueError):
            answer = {}
        libs.update(answer.get("libraries") or [])
        summaries.append(f"part {i}: {answer.get('summary') or answer.get('none') or 'no answer'}")
        done.append(f"- part {i}: {summaries[-1]}")
        if "Boss verdict: STRIKE" in result:
            break
    return json.dumps({"libraries": sorted(libs), "summary": "; ".join(summaries), "parts": len(done)} if libs else
                      {"none": "; ".join(summaries) or "no part finished"})


ACTIVITY_PLAN = ("Blu gave the whole AI team a task (below). As the boss, split it into at most 4 parts, one job each: Sonnet "
                 "members (Builder, Fixer, Experimenter, Teacher) take parts that need reading or writing code or deep "
                 "thinking; the Ambassador (Opus) takes anything about GitHub pull requests, reviews and upstream; Haiku "
                 "members (Helper, Watcher) take quick look-ups and checks. Use as few parts as the task "
                 "needs. Your last line must be one JSON object: "
                 '{"parts": [{"member": "Builder|Fixer|Experimenter|Teacher|Ambassador|Helper|Watcher", "task": "what this member does"}], '
                 '"why": "the plan in one sentence"}.\n\n')
ACTIVITY_RULES = ("You are the {member} of Blu's AI team, working on one part of a task Blu gave the whole team. Do your part "
                  "concretely and briefly; build on what your teammates already did. Read what you need (the pipeline is in "
                  "D:\\AnyPS5 Games\\_tools; the local AI's look-up tools: python -I assistant/tool_cli.py). Code changes "
                  "go only in {worktree} on branch {branch} (commit, never push, never touch other branches). End with a "
                  "'Notes for the team:' paragraph.\n\n")
ACTIVITY_TREE = Path(r"C:\AnyPS5\wt-activity")
KINDS["act_sonnet"] = {"model": "claude-sonnet-5-5", "turns": 40, "tools": "Read,Edit,Write,Grep,Glob,Bash", "per_day": 12, "timeout": 1800, "budget": 1.50}
# 11-04 raised from 14: one whole-team question is 9 calls, and Blu asked a second one the same day (the dollar cap still holds).
# Blu 11-06: "allow the Claude Code to watch the games so it can actually progress through the games". One call per
# turn of ai-player.py --brain claude: it reads live\play\screen.png and answers with the next buttons.
# Blu 11-07: "a smarter model that could play the game ... and look up the source and guides of each game": the
# tester is Opus and may search the web and read pages (controls, walkthroughs, source code) while it plays.
KINDS["play"] = {"model": "claude-opus-5-5", "turns": 14, "tools": "Read,WebSearch,WebFetch", "per_day": 0, "timeout": 420, "budget": 0.80, "allowed": "Read,WebSearch,WebFetch"}
KINDS["voice"] = {"model": "claude-haiku-5-5", "turns": 1, "tools": "", "per_day": 24, "timeout": 300, "budget": 1.00, "allowed": ""}
# One member answering the messages addressed to it (talk.py): its own model, read-only look-ups to cite evidence.
KINDS["talk"] = {"model": "claude-haiku-5-5", "turns": 8, "tools": "Read,Grep,Glob,Bash", "per_day": 200, "timeout": 600, "budget": 0.60,
                 "allowed": "Read,Grep,Glob,Bash(python -I assistant/tool_cli.py:*)"}
VOICES = RUNS / "voices"


def voice(kind, question):
    """Blu 11-04: "I always want to include the AIs." Asks one member, in its own model and its own role, what it thinks;
    no tools, one turn. The answer is kept in repair-runs/voices and shown to Blu."""
    if kind == "local" or kind not in MEMBERS:
        return ""
    KINDS["voice"]["model"] = KINDS.get(kind, KINDS["boss"])["model"]
    LIVE_AS["voice"] = kind
    LIVE_LABEL["text"] = "asked by Blu what it thinks"
    answer = run("voice", role_text(kind) + own_memory(kind) + question)
    LIVE_LABEL["text"] = ""
    VOICES.mkdir(parents=True, exist_ok=True)
    (VOICES / f"{time.strftime('%Y-%m-%d-%H%M')}-{kind}.md").write_text(f"# {MEMBERS[kind]} ({KINDS['voice']['model']})\n\n## Asked\n{question}\n\n## Answer\n{answer}\n", encoding="utf-8")
    return answer


KINDS["act_opus"] ={"model": "claude-opus-5-5", "turns": 40, "tools": "Read,Grep,Glob,Bash", "per_day": 4, "timeout": 1800, "budget": 2.50}
KINDS["act_haiku"] = {"model": "claude-haiku-5-5", "turns": 10, "tools": "Read,Grep,Glob,Bash", "per_day": 20, "timeout": 600, "budget": 0.20,
                      "allowed": "Read,Grep,Glob,Bash(python -I assistant/tool_cli.py:*)"}


def activity(task):
    """Blu 11-04: start any task or question for the whole team from the AI team view. The boss plans up to 4 parts,
    the members do them in turn (each sees what the others did), the boss writes the answer. Watched live in the view."""
    stamp = time.strftime("%Y-%m-%d-%H%M")
    out_dir = RUNS / "activities"
    out_dir.mkdir(parents=True, exist_ok=True)
    branch = f"claude-activity/{stamp}"
    set_live("boss", "planning", task[:140], task=task, status="planning", parts=[], answer="", started=time.strftime("%H:%M"))
    note(f"AI TEAM activity started: {task[:160]}")
    if not ACTIVITY_TREE.exists():
        subprocess.run(["git", "-C", r"D:\AnyPS5", "worktree", "add", "--detach", str(ACTIVITY_TREE), "HEAD"], capture_output=True, creationflags=0x08000000)
    subprocess.run(["git", "-C", str(ACTIVITY_TREE), "checkout", "-q", "-B", branch], capture_output=True, creationflags=0x08000000)
    LIVE_LABEL["text"] = f"planning: {task[:120]}"
    plan_text = run("boss", ACTIVITY_PLAN + experience_note() + "# Blu's task\n" + task)
    try:
        plan = json.loads([l for l in plan_text.strip().splitlines() if l.strip().startswith("{")][-1])
        parts = [p for p in plan.get("parts", []) if p.get("member") in NAME_TO_KIND][:4]
    except (IndexError, ValueError):
        plan, parts = {}, []
    if not parts:
        answer = plan_text.strip()
    else:
        state = [{"member": p["member"], "task": p.get("task", "")[:200], "status": "waiting"} for p in parts]
        set_live("boss", "idle", "", status="working", parts=state, why=str(plan.get("why", ""))[:200])
        done = []
        for i, part in enumerate(parts):
            kind = NAME_TO_KIND[part["member"]]
            act = "act_haiku" if kind in ("ask", "supervise") else "act_opus" if kind == "pr" else "act_sonnet"
            LIVE_AS[act] = kind
            state[i]["status"] = "working"
            set_live(kind, "working", part.get("task", "")[:140], parts=state)
            LIVE_LABEL["text"] = part.get("task", "")[:140]
            brief = (ACTIVITY_RULES.format(member=part["member"], worktree=ACTIVITY_TREE, branch=branch) + role_text(kind) + team_notes()
                     + f"# Blu's task\n{task}\n\n# The boss's plan\n{plan.get('why', '')}\n"
                     + "".join(f"- {p['member']}: {p.get('task', '')}\n" for p in parts)
                     + ("\n# Done so far\n" + "\n".join(done) + "\n" if done else "") + f"\n# Your part\n{part.get('task', '')}")
            if act in ("act_sonnet", "act_opus"):
                KINDS[act]["allowed"] = KINDS[act]["tools"]
            result = run(act, brief)
            keep_team_notes(kind, result)
            done.append(f"- {part['member']}: {result.strip()[-1500:]}")
            state[i]["status"] = "done"
            state[i]["result"] = result.strip()[-400:]
            set_live(kind, "idle", "", parts=state)
        set_live("boss", "reviewing", "writing the team's answer", status="answering")
        LIVE_LABEL["text"] = "writing the team's answer for Blu"
        answer = run("boss", "Your team finished Blu's task. Write the answer for Blu from their work: plain words, what was found "
                     "or made (with branch names for code), what is still open. Keep it under 250 words.\n\n"
                     f"# Blu's task\n{task}\n\n# Your team's work\n" + "\n".join(done)).strip()
    LIVE_LABEL["text"] = ""
    set_live("boss", "idle", "", status="done", answer=answer[:4000], finished=time.strftime("%H:%M"))
    (out_dir / f"{stamp}.md").write_text(f"# Team activity {stamp}\n\n## Blu's task\n{task}\n\n## Answer\n{answer}\n", encoding="utf-8")
    note(f"AI TEAM activity done: {answer.splitlines()[0][:180] if answer else '(no answer)'}")
    return answer


STEWARD_TREE = Path(r"C:\AnyPS5\wt-steward")
# 11-04 its first run could write no draft (the deny on repair-runs/** wins over any allow), so drafts live in _tools.
DRAFTS = TOOLS / "pr-drafts"
STEWARD_RULES = ("You are the Ambassador of Blu's AnyPS5 AI team (Opus): you look after Blu's pull requests on the upstream "
                 "AnyPS5 repository. Blu's open PRs are listed below with their checks. For each one, find what needs doing: "
                 "a failing check (read the failing job's log with gh run view --log-failed), a review comment not yet "
                 "answered (read every comment with gh pr view N --repo boykopovar/AnyPS5 --comments; a 'needs nothing' must rest on that reading), "
                 "or a merge conflict with main. You may bring a PR's branch up to date with origin/main in "
                 "C:\\AnyPS5\\wt-steward (git -C C:\\AnyPS5\\wt-steward fetch/checkout/merge/commit) and fix what the "
                 "conflict or check needs there, following AGENTS.md and CONTRIBUTING.md (no comments in code, Conventional "
                 "Commits). You never push and never post: write a draft reply for each comment that needs one into "
                 "pr-drafts\\<PR number>.md (in your working folder _tools) with the proof it rests on (log lines, test output). A draft without "
                 "proof is not a draft. Skip a PR that needs nothing and say so. Your last line must be one JSON object: "
                 '{"summary": "one sentence", "prs": [{"number": N, "needs": "what", "done": "what you did or drafted"}], '
                 '"branches": ["branch you committed to"], "libraries": ["library touched"]} or {"none": "why nothing needed doing"}.\n\n')


def steward_work(result):
    log = subprocess.run(["git", "-C", str(STEWARD_TREE), "log", "-3", "--format=%h %s (%cr)", "--stat"], capture_output=True, text=True, errors="replace").stdout
    drafts = "\n".join(f"## {p.name}\n{p.read_text(encoding='utf-8', errors='replace')[:2000]}" for p in sorted(DRAFTS.glob("*.md"))
                       if p.stat().st_mtime > time.time() - 6 * 3600) if DRAFTS.exists() else ""
    return f"{result}\n\n# Its newest commits in wt-steward\n{log}\n\n# Its drafts\n{drafts or '(none)'}"


def pr_conversation(number, keep=6):
    """The Ambassador asked (11-04) to see line-level review comments before calling a PR clean. gh api stays denied to
    it (it can post), so this reads them, the reviews and the comments for it, newest last."""
    def gh(*args):
        return subprocess.run(["gh", *args], capture_output=True, text=True, errors="replace", creationflags=0x08000000).stdout
    try:
        view = json.loads(gh("pr", "view", str(number), "--repo", "boykopovar/AnyPS5", "--json", "reviews,comments") or "{}")
        lines = json.loads(gh("api", f"repos/boykopovar/AnyPS5/pulls/{number}/comments", "--paginate") or "[]")
    except ValueError:
        return f"  (#{number}: its comments could not be read)"
    said = [(r.get("submittedAt", ""), f"review {r.get('state')} by {r['author']['login']}", r.get("body", "")) for r in view.get("reviews", []) if r.get("body")]
    said += [(c.get("createdAt", ""), f"comment by {c['author']['login']}", c.get("body", "")) for c in view.get("comments", [])]
    said += [(c.get("created_at", ""), f"line comment by {c['user']['login']} on {c.get('path')}:{c.get('line') or c.get('original_line')}", c.get("body", "")) for c in lines]
    said.sort()
    out = [f"  {len(said)} reviews and comments ({len(lines)} on lines); the newest {min(keep, len(said))}:"]
    for when, who, body in said[-keep:]:
        text = " ".join(body.split())
        out.append(f"  - {when[:16]} {who}: {text[:500]}")
    return "\n".join(out)


def steward():
    """Blu 11-04: once a day the Ambassador (Opus 5.5) works through Blu's open pull requests on its own: failing
    checks, unanswered reviews, conflicts. It commits fixes in its own worktree and drafts replies with proof; Blu or
    Claude Code posts them. The boss reviews its work like any member's."""
    report = RUNS / f"{time.strftime('%Y-%m-%d')}-pr-report.md"
    if report.exists():
        return f"Already done today: {report.read_text(encoding='utf-8', errors='replace').strip()[:300]}"
    DRAFTS.mkdir(parents=True, exist_ok=True)
    if not STEWARD_TREE.exists():
        subprocess.run(["git", "-C", r"D:\AnyPS5", "worktree", "add", "--detach", str(STEWARD_TREE), "origin/main"], capture_output=True, creationflags=0x08000000)
    listing = subprocess.run(["gh", "pr", "list", "--repo", "boykopovar/AnyPS5", "--author", "@me", "--state", "open", "--json",
                              "number,title,headRefName,mergeable,reviewDecision,statusCheckRollup,updatedAt", "--limit", "30"],
                             capture_output=True, text=True, errors="replace", creationflags=0x08000000).stdout
    try:
        prs = json.loads(listing or "[]")
    except ValueError:
        prs = []
    if not prs:
        return "No open pull requests by Blu (or gh is not signed in)."
    rows = []
    for p in prs:
        checks = p.get("statusCheckRollup") or []
        failed = [c.get("name", "") for c in checks if str(c.get("conclusion", "")).upper() in ("FAILURE", "TIMED_OUT", "CANCELLED")]
        rows.append(f"- #{p['number']} {p['title']} (branch {p['headRefName']}, mergeable {p.get('mergeable')}, review {p.get('reviewDecision') or 'none'}, "
                    f"failed checks: {', '.join(failed) or 'none'}, updated {p.get('updatedAt', '')[:10]})")
        rows.append(pr_conversation(p["number"]))
    set_live("pr", "working", f"{len(prs)} open pull requests")
    result = supervised("pr", STEWARD_RULES + "# Blu's open pull requests\n" + "\n".join(rows), steward_work)
    if not unavailable(result):
        report.write_text(result, encoding="utf-8")
    note(f"AI TEAM Ambassador went through {len(prs)} pull requests: {result.strip().splitlines()[-1][:160] if result.strip() else ''}")
    return result


COUNCIL_RULES = ("It is evening: the Round Table. Every member worked on its own today; below are their results, their notes "
                 "for the team and the realm's quests. As the boss, pick the ONE problem where the team working together would "
                 "get the most done tomorrow: a game several results point at, a function more than one member ran into, a "
                 "lesson the whole team keeps missing. Write it as a task for the whole team, concrete enough to split into "
                 "parts. Your last line must be one JSON object: {\"task\": \"the team task\", \"why\": \"what in today's "
                 "results points at it\"} or {\"none\": \"why there is nothing to do together\"}.\n\n")


def council():
    """Blu 11-04: the members work on their own tasks, then come back together. Once a day the boss gathers what every
    member did, picks the one problem best solved together, and the whole team works it as a team activity."""
    report = RUNS / f"{time.strftime('%Y-%m-%d')}-council.md"
    if report.exists():
        return f"The Round Table already met today: {report.read_text(encoding='utf-8', errors='replace').strip()[:300]}"
    today = time.strftime("%Y-%m-%d")
    results = []
    for p in sorted(RUNS.glob(f"{today}*-report.md")) + sorted(RUNS.glob(f"{today}-coach.md")):
        results.append(f"## {p.name}\n{p.read_text(encoding='utf-8', errors='replace')[-2500:]}")
    try:
        import crowns
        quests = "\n".join(f"- {q['function']} ({q['library']}, {q['games']} games)" for q in crowns.quests())
    except Exception:
        quests = ""
    import talk
    talks = talk.round_table()
    set_live("boss", "planning", "the Round Table")
    reply = run("boss", COUNCIL_RULES + experience_note() + team_notes() + "# Today's results\n" + ("\n".join(results) or "(no reports today)")
                + "\n\n# The notice board\n" + quests + talks)
    talk.close_from(reply)
    try:
        pick = json.loads([l for l in reply.strip().splitlines() if l.strip().startswith("{")][-1])
    except (IndexError, ValueError):
        pick = {"none": reply.strip()[:300] or "no answer"}
    if pick.get("task"):
        note(f"AI TEAM Round Table: {str(pick.get('why', ''))[:160]}")
        answer = activity(pick["task"])
        text = f"# Round Table {today}\n\n## Why\n{pick.get('why', '')}\n\n## Team task\n{pick['task']}\n\n## Result\n{answer}\n"
    else:
        text = f"# Round Table {today}\n\nNothing to do together: {pick.get('none', '')}\n"
    if not unavailable(reply):
        report.write_text(text, encoding="utf-8")
    return text


def fix_work(result):
    """What the boss reads of a fix: the fixer's answer and the commit it made on its branch."""
    wt = r"D:\AnyPS5\wt-pr"
    show = subprocess.run(["git", "-C", wt, "log", "-1", "--format=%h %s", "--stat"], capture_output=True, text=True, errors="replace").stdout
    diff = subprocess.run(["git", "-C", wt, "show", "--format=", "HEAD"], capture_output=True, text=True, errors="replace").stdout
    return f"{result}\n\n# Its commit (HEAD of the branch)\n{show}\n{diff[:15000]}"


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    if action == "ask":
        print(ask(" ".join(sys.argv[2:])))
    elif action in ("repair", "fix", "build", "exp"):
        prompt, report = Path(sys.argv[2]), Path(sys.argv[3])
        gather = fix_work if action in ("fix", "build", "exp") else (lambda result: result)
        result = supervised(action, prompt.read_text(encoding="utf-8-sig"), gather)
        print(result)
        if not unavailable(result):
            report.write_text(result, encoding="utf-8")
    elif action == "raid":
        prompt, report = Path(sys.argv[2]), Path(sys.argv[3])
        result = raid(prompt.read_text(encoding="utf-8-sig"))
        print(result)
        if not unavailable(result):
            report.write_text(result, encoding="utf-8")
    elif action == "activity":
        print(activity(Path(sys.argv[2]).read_text(encoding="utf-8-sig")))
    elif action == "publish":
        set_live("", "", "")
        print(LIVE.read_text(encoding="utf-8")[:300])
    elif action == "spent":
        print(f"{sum(r.get('cost', 0) for r in entries(time.strftime('%Y-%m-%d'))):.2f}")
    elif action == "reward":
        print(reward(sys.argv[2], int(sys.argv[3]), " ".join(sys.argv[4:])))
    elif action == "team":
        print(reinstate(sys.argv[3] if len(sys.argv) > 3 else "all") if sys.argv[2:3] == ["reinstate"] else team())
    elif action == "coach":
        print(coach())
    elif action == "stops":
        print(confirm_stops())
    elif action == "talk":
        import talk
        print(talk.steps(int(sys.argv[2]) if len(sys.argv) > 2 else 1))
    elif action == "threads":
        import talk
        print(talk.report())
    elif action == "pr":
        print(steward())
    elif action == "council":
        print(council())
    elif action == "usage":
        print(usage())
    else:
        print(status())
