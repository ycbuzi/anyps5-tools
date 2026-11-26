"""ai-player -- plays an AnyPS5 game for a while with two local models, to get past menus into the game, take
screenshots of it (AnyPS5's F12 screenshots) and catch crashes that only happen in play.

  eyes:  Qwen2-VL-2B on the GTX 1650 SUPER describes the screen   (llama-server, port 8091)
  brain: Qwen2.5-7B on the Radeon iGPU picks the next button       (llama-server, port 8092)
  The game itself keeps the RTX 4070 SUPER.

Safety: the game's save folder (_sd) is backed up first and put back afterwards, so a session never overwrites
Blu's progress. The session stops as soon as the mouse moves or another window comes to the front (someone is at
the PC), and every key it holds is released. Optional per-game guidance: <game folder>\\ai-notes.txt (the goal and
walkthrough steps), read every turn.

Brain "claude" (Blu 11-06): Claude Code looks at each screenshot itself and answers with up to 6 buttons, keeping a
short memory of where it is and what it is trying; no local models are started. It is the default while the
pipeline's Claude limits are lifted (repair-runs\\uncap-override.json) or Settings aiPlayerBrain is "claude".

usage: ai-player.py "<game folder>" [--minutes 15] [--brain local|claude]
Results: <game folder>\\ai-runs\\<stamp>\\ (steps.jsonl, summary.md, a few small frames) and the F12 screenshots in
<game folder>\\anyps5-screenshots\\.
"""
import argparse
import base64
import ctypes
import ctypes.wintypes as wt
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
import uuid
import urllib.request
import zlib
from pathlib import Path

MODELS = Path(r"C:\fragnetic_llm")
SERVER = MODELS / "vk" / "llama-server.exe"
EYES = {"port": 8091, "device": "Vulkan1", "args": ["-m", str(MODELS / "Qwen2-VL-2B-Instruct-Q4_K_M.gguf"), "--mmproj", str(MODELS / "mmproj-Qwen2-VL-2B-Instruct-f16.gguf")]}
BRAIN = {"port": 8092, "device": "Vulkan2", "args": ["-m", str(MODELS / "Qwen2.5-7B-Instruct-Q4_K_M.gguf")]}
TOOLS = Path(__file__).resolve().parent
PLAY_DIR = TOOLS / "live" / "play"
sys.path.insert(0, str(TOOLS / "assistant"))

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

# --- keyboard (scan codes; AnyPS5 reads SDL scancodes) -------------------------------------------------------------
# Square and R2 are mouse buttons by default; ai-input.ini (passed with ANYPS5_INPUT_CONFIG to this session's game
# only) puts them on J and K so no mouse input is ever sent.
KEYS = {"CROSS": (0x1C, False), "CIRCLE": (0x2E, False), "TRIANGLE": (0x17, False), "SQUARE": (0x24, False),
        "OPTIONS": (0x01, False), "L1": (0x10, False), "R1": (0x12, False), "R2": (0x25, False),
        "UP": (0x48, True), "DOWN": (0x50, True), "LEFT": (0x4B, True), "RIGHT": (0x4D, True),
        "MOVE_UP": (0x11, False), "MOVE_LEFT": (0x1E, False), "MOVE_DOWN": (0x1F, False), "MOVE_RIGHT": (0x20, False),
        "F12": (0x58, False)}
HOLD = {"MOVE_UP": 0.8, "MOVE_DOWN": 0.8, "MOVE_LEFT": 0.8, "MOVE_RIGHT": 0.8}
ACTIONS = [a for a in KEYS if a != "F12"] + ["WAIT"]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]


def key(name, down):
    scan, extended = KEYS[name]
    flags = 0x0008 | (0x0001 if extended else 0) | (0 if down else 0x0002)  # SCANCODE, EXTENDEDKEY, KEYUP
    i = INPUT(type=1, u=_INPUTUNION(ki=KEYBDINPUT(0, scan, flags, 0, 0)))
    user32.SendInput(1, ctypes.byref(i), ctypes.sizeof(INPUT))


# Blu 11-07: Claude keeps playing while Blu uses the PC. When the test launched the game with APS5_PAD_INPUT_FILE
# (AnyPS5's controller command file, polled at least every 100 ms), buttons go into that file instead of SendInput:
# no keyboard, no mouse, no foreground needed, so moving the mouse or using another window never stops the session.
PAD_FILE = None
PAD_BUTTONS = {"CROSS": 0x4000, "CIRCLE": 0x2000, "TRIANGLE": 0x1000, "SQUARE": 0x8000, "OPTIONS": 0x8, "L1": 0x400,
               "R1": 0x800, "L2": 0x100, "R2": 0x200, "L3": 0x2, "R3": 0x4, "TOUCHPAD": 0x100000,
               "UP": 0x10, "DOWN": 0x40, "LEFT": 0x80, "RIGHT": 0x20}
# Blu 11-07 ("it's literally going to get stuck"): a move is one controller state held for its time, so buttons and
# both sticks combine ("MOVE_UP+R2" runs forward shooting, "MOVE_UP+LOOK_RIGHT" walks while turning). MOVE_* is the
# left stick, LOOK_* the right stick (camera); the half-tilt *_SLOW forms walk carefully or aim finely.
LEFT_STICK = {"MOVE_UP": (128, 0), "MOVE_DOWN": (128, 255), "MOVE_LEFT": (0, 128), "MOVE_RIGHT": (255, 128),
              "MOVE_UP_LEFT": (38, 38), "MOVE_UP_RIGHT": (218, 38), "MOVE_DOWN_LEFT": (38, 218), "MOVE_DOWN_RIGHT": (218, 218),
              "MOVE_UP_SLOW": (128, 64)}
RIGHT_STICK = {"LOOK_UP": (128, 0), "LOOK_DOWN": (128, 255), "LOOK_LEFT": (0, 128), "LOOK_RIGHT": (255, 128),
               "LOOK_LEFT_SLOW": (64, 128), "LOOK_RIGHT_SLOW": (192, 128), "LOOK_UP_SLOW": (128, 64), "LOOK_DOWN_SLOW": (128, 192)}
PAD_STICKS = dict(LEFT_STICK, **RIGHT_STICK)
PAD_NAMES = set(PAD_BUTTONS) | set(PAD_STICKS)
pad_sequence = 0


def pad(name, seconds):
    global pad_sequence
    hold = max(1, min(5000, int(seconds * 1000)))
    parts = [p for p in name.split("+") if p in PAD_NAMES]
    mask = 0
    left, right = (128, 128), (128, 128)
    for p in parts:
        mask |= PAD_BUTTONS.get(p, 0)
        left = LEFT_STICK.get(p, left)
        right = RIGHT_STICK.get(p, right)
    sticks = "" if (left, right) == ((128, 128), (128, 128)) else " {} {} {} {}".format(*left, *right)
    pad_sequence = max(time.monotonic_ns(), pad_sequence + 1)
    temp = PAD_FILE.with_suffix(".part")
    temp.write_text(f"{pad_sequence} {mask} {hold}{sticks}\n", encoding="utf-8", newline="\n")
    os.replace(temp, PAD_FILE)


def valid_move(name):
    parts = name.split("+")
    names = PAD_NAMES if PAD_FILE else set(KEYS) - {"F12"}
    return bool(parts) and all(p in names for p in parts) and len(set(parts)) == len(parts)


def press(name, seconds=0.12):
    if PAD_FILE:
        pad(name, seconds)
        time.sleep(seconds)
        return
    parts = [p for p in name.split("+") if p in KEYS]
    for p in parts:
        key(p, True)
    time.sleep(seconds)
    for p in reversed(parts):
        key(p, False)


def release_all():
    if PAD_FILE:
        pad("RELEASE", 0.05)
        return
    for name in KEYS:
        key(name, False)


# --- windows and capture ------------------------------------------------------------------------------------------
class RECT(ctypes.Structure):
    _fields_ = [("left", wt.LONG), ("top", wt.LONG), ("right", wt.LONG), ("bottom", wt.LONG)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


def window_of(pid):
    """The game's window: a top-level window, or, when the game plays inside the AnyPS5 Games window, a child of it."""
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def visit(hwnd, _):
        owner = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True
    user32.EnumWindows(visit, 0)
    if not found:
        @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        def top(hwnd, _):
            user32.EnumChildWindows(hwnd, visit, 0)
            return not found
        user32.EnumWindows(top, 0)
    return found[0] if found else None


user32.GetAncestor.restype = wt.HWND
user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]


def root_of(hwnd):
    """The top-level window: the game's own, or the AnyPS5 Games window when the game is inside it."""
    return user32.GetAncestor(hwnd, 2) or hwnd


def embedded(hwnd):
    return root_of(hwnd) != hwnd


def title_of(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buf, 256)
    return buf.value


def bring_forward(hwnd):
    root = root_of(hwnd)
    user32.ShowWindow(root, 6)  # minimize then restore: Windows lets that take the foreground
    user32.ShowWindow(root, 9)
    user32.SetForegroundWindow(root)
    if root != hwnd:
        # Inside the AnyPS5 Games window: the keys must reach the game's window, which belongs to another thread.
        me = ctypes.windll.kernel32.GetCurrentThreadId()
        game = user32.GetWindowThreadProcessId(hwnd, None)
        user32.AttachThreadInput(me, game, True)
        user32.SetFocus(hwnd)
        user32.AttachThreadInput(me, game, False)
    time.sleep(0.5)


def maximize(hwnd):
    """Bigger, sharper showcase shots; inside the AnyPS5 Games window, that window is maximized instead."""
    user32.ShowWindow(root_of(hwnd), 3)


def capture(hwnd):
    """The window's client area as (width, height, BGRA bytes). In pad mode Blu may have other windows on top, so the
    window draws itself (PrintWindow, client only, full content) first; the screen copy is the fallback."""
    if PAD_FILE:
        r = RECT()
        user32.GetClientRect(hwnd, ctypes.byref(r))
        w, h = r.right - r.left, r.bottom - r.top
        if w > 0 and h > 0:
            screen = user32.GetDC(None)
            mem = gdi32.CreateCompatibleDC(screen)
            bmp = gdi32.CreateCompatibleBitmap(screen, w, h)
            gdi32.SelectObject(mem, bmp)
            ok = user32.PrintWindow(hwnd, mem, 0x1 | 0x2)
            info = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
            data = ctypes.create_string_buffer(w * h * 4)
            gdi32.GetDIBits(mem, bmp, 0, h, data, ctypes.byref(info), 0)
            gdi32.DeleteObject(bmp)
            gdi32.DeleteDC(mem)
            user32.ReleaseDC(None, screen)
            if ok and any(data.raw[i] for i in range(0, len(data.raw), 4093)):
                return w, h, data.raw
    r = RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    pt = wt.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    w, h = r.right - r.left, r.bottom - r.top
    screen = user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(screen)
    bmp = gdi32.CreateCompatibleBitmap(screen, w, h)
    gdi32.SelectObject(mem, bmp)
    gdi32.BitBlt(mem, 0, 0, w, h, screen, pt.x, pt.y, 0x00CC0020 | 0x40000000)
    info = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    data = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, data, ctypes.byref(info), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(None, screen)
    return w, h, data.raw


def window_png(hwnd):
    """The window as seen: title bar (game and AnyPS5's FPS) plus the picture, cut at the DWM visible bounds so
    nothing behind the window shows (the invisible resize border once leaked a browser bar into a showcase shot)."""
    r = RECT()
    if ctypes.WinDLL("dwmapi").DwmGetWindowAttribute(hwnd, 9, ctypes.byref(r), ctypes.sizeof(r)) != 0 or embedded(hwnd):
        user32.GetWindowRect(hwnd, ctypes.byref(r))  # a child window has no DWM frame of its own
    w, h = r.right - r.left, r.bottom - r.top
    screen = user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(screen)
    bmp = gdi32.CreateCompatibleBitmap(screen, w, h)
    gdi32.SelectObject(mem, bmp)
    gdi32.BitBlt(mem, 0, 0, w, h, screen, r.left, r.top, 0x00CC0020 | 0x40000000)
    info = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    data = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, data, ctypes.byref(info), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(None, screen)
    bgra = bytearray(data.raw)
    rgb = bytearray(w * h * 3)
    rgb[0::3], rgb[1::3], rgb[2::3] = bgra[2::4], bgra[1::4], bgra[0::4]
    return png(w, h, bytes(rgb))


def shrink(w, h, bgra, width):
    """Nearest-neighbour resize to `width`, as RGB bytes."""
    tw, th = width, max(1, h * width // w)
    out = bytearray()
    for y in range(th):
        row = bgra[(y * h // th) * w * 4:((y * h // th) + 1) * w * 4]
        px = bytearray()
        for x in range(tw):
            i = (x * w // tw) * 4
            px += bytes((row[i + 2], row[i + 1], row[i]))
        out += px
    return tw, th, bytes(out)


def png(w, h, rgb):
    def chunk(tag, body):
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + rgb[y * w * 3:(y + 1) * w * 3] for y in range(h))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")


def difference(a, b):
    if not a or not b or len(a) != len(b):
        return 255.0
    return sum(abs(x - y) for x, y in zip(a[::3], b[::3])) / (len(a) / 3)


# --- models -------------------------------------------------------------------------------------------------------
def healthy(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
            return json.load(r).get("status") == "ok"
    except Exception:
        return False


def start_server(spec):
    if healthy(spec["port"]):
        return None
    proc = subprocess.Popen([str(SERVER)] + spec["args"] + ["--device", spec["device"], "-ngl", "99", "-c", "4096", "--port", str(spec["port"]), "--host", "127.0.0.1"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)
    for _ in range(120):
        if healthy(spec["port"]):
            return proc
        time.sleep(1)
    proc.kill()
    raise RuntimeError(f"model server on port {spec['port']} did not start")


def chat(port, messages, max_tokens):
    body = json.dumps({"messages": messages, "max_tokens": max_tokens, "temperature": 0.2}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions", body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.load(r)["choices"][0]["message"]["content"].strip()


def describe(image_png):
    b64 = base64.b64encode(image_png).decode()
    return chat(EYES["port"], [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
        {"type": "text", "text": "Describe this video game screen for someone who must press the next button. Mention: any menu and its items with the highlighted one, any dialogue or prompt text, the player character and where it can go, anything blocking. Be brief and factual."}]}], 160)


SYSTEM = ("You play a PlayStation game with a controller, one button per turn. You get the game's notes, a description of the "
          "current screen and your last moves. Goal unless the notes say otherwise: get from the title screen into the game "
          "(choose New Game / Start, skip or advance intros and dialogue), then explore and make progress. Never quit the "
          "game, delete saves, or change settings. If a move did not change the screen, try something different. In menus "
          "move the highlight with UP/DOWN first, then confirm with CROSS. Walk with MOVE_LEFT/MOVE_RIGHT/MOVE_UP/MOVE_DOWN. "
          "Reply with exactly one of: " + ", ".join(ACTIONS) + " - then a short reason.")


def highlighted(s):
    """Which item the description says is highlighted: 'the highlighted button is labeled "Next"', '"Options" is
    highlighted', 'NEW GAME is highlighted'."""
    for pattern in (r'highlighted[^."]{0,40}"([^"]{1,24})"', r'"([^"]{1,24})"[^.]{0,20}is highlighted', r'([a-z][a-z ]{1,20}?) (?:option |button |item )?is (?:currently )?highlighted',
                    r'highlighted (?:option|button|item|choice) (?:is |reads |says )?"?([a-z][a-z ]{1,20})'):
        m = re.search(pattern, s)
        if m:
            return m.group(1).strip()
    return None


def rule(screen, history):
    """Moves that need no judgement, ahead of the brain (it kept pressing DOWN on a highlighted "Next" in Coffee Talk)."""
    s = screen.lower()
    if any(w in s for w in ("press any button", "press any key", "press x", "press cross", "press the x")):
        return "CROSS"
    # Name entry (Coffee Talk): Enter takes the typed name, Options confirms, CROSS answers "is ... correct?".
    if "correct?" in s or ("is " in s and "correct" in s and "yes" in s):
        return "CROSS"
    if "confirm" in s and "name" in s:
        return "OPTIONS"
    if any(w in s for w in ("what's your name", "what is your name", "enter your name", "on-screen keyboard", "virtual keyboard")):
        return "CROSS"
    label = highlighted(s)
    if label:
        if any(w in label for w in ("option", "setting", "quit", "exit", "credit", "delete", "language", "extra", "bonus")):
            return "UP"  # New Game / Start is almost always above these
        if any(w in label for w in ("next", "new game", "start", "continue", "begin", "confirm", "yes", "ok", "play", "story")):
            return "CROSS"
    if "blank" in s or "black screen" in s or "black background" in s:
        return "CROSS"  # intros and fades: CROSS advances or skips them and is harmless otherwise
    if len(history) >= 2 and not history[-1]["changed"] and not history[-2]["changed"] and history[-1]["action"] != "CROSS":
        return "CROSS"  # two moves without any change: try confirming
    return None


def decide(notes, screen, history):
    fixed = rule(screen, history)
    if fixed:
        return fixed, f"{fixed} - rule"
    recent = "\n".join(f"- {h['action']}" + ("" if h["changed"] else " (screen did not change)") for h in history[-6:]) or "- none yet"
    reply = chat(BRAIN["port"], [{"role": "system", "content": SYSTEM},
                                 {"role": "user", "content": f"Game notes:\n{notes}\n\nScreen now:\n{screen}\n\nYour last moves:\n{recent}"}], 40)
    word = reply.split()[0].strip(".,:;-*").upper() if reply.split() else "WAIT"
    return (word if word in ACTIONS else "WAIT"), reply


CLAUDE_PLAY = """You are playing a PlayStation 5 game on Blu's PC through AnyPS5, the way a person with a controller would. The
point is to get as far into the game as possible so its testing reaches real gameplay: past logos, title screens, intros,
dialogue and menus, then really playing: moving through levels, fighting, solving, making progress.

Believe in yourself (Blu's words to every tester): every game has a way forward and gives instructions for it, on
screen, in its menus, tutorials, hints and guides. You can work it out, so keep reading the game and keep trying.

Read the screenshot at {screen} (the game window right now) with your Read tool before you answer. From the second turn
on you also get the frame from before your last moves: compare the two to see what your moves really did.

THE CONTROLLER. A move is one controller state held for "hold" seconds (up to 5). Join names with + to hold them
together: "MOVE_UP+R2" runs forward while firing, "MOVE_UP+LOOK_RIGHT" walks while turning, "MOVE_UP+CROSS" jumps
while running. Names: {buttons}.
- Buttons: CROSS (confirm, often jump/interact), CIRCLE (back, often dodge/crouch), SQUARE, TRIANGLE, L1, R1, L2 (often
  aim), R2 (often shoot/accelerate), L3/R3 (stick clicks: sprint, melee, zoom), OPTIONS (pause menu), TOUCHPAD (map).
- D-pad UP/DOWN/LEFT/RIGHT: menus and quick items.
- Left stick (walk): MOVE_UP, MOVE_DOWN, MOVE_LEFT, MOVE_RIGHT, the diagonals MOVE_UP_LEFT, MOVE_UP_RIGHT,
  MOVE_DOWN_LEFT, MOVE_DOWN_RIGHT, and MOVE_UP_SLOW (careful walking).
- Right stick (camera/aim): LOOK_LEFT, LOOK_RIGHT, LOOK_UP, LOOK_DOWN and the half-speed *_SLOW forms for fine aim.
- WAIT does nothing for "hold" seconds (loading, cutscenes, waiting for an enemy).

HOW TO MOVE IN 3D (first or third person). You see one frame every few seconds, so plan moves that are safe while you
are not looking: walk in bursts of 1.5 to 3 s, not 0.2 s taps. To turn, hold LOOK_LEFT or LOOK_RIGHT about 0.5 s for a
quarter turn (check the next frame and adjust). Head for openings, doors, corridors, lit areas, objective markers and
anything the HUD points at. In a first-person shooter, face enemies with LOOK_*, fire with R2 (or what the game shows),
and keep moving between shots. In 2D games use MOVE_LEFT/MOVE_RIGHT with CROSS (jump) held together for running jumps.

PLAY THE KIND OF GAME IT IS. First decide what kind of game this is and play it that way:
- Story, adventure, exploration, puzzle (Blu: "you got to go locate things"): progress comes from objectives, not
  movement. Read every objective, quest log, dialogue and on-screen hint; talk to characters (walk up and press the
  interact button); follow the objective marker, compass or map (TOUCHPAD or OPTIONS often opens it); pick up and use
  items; examine highlighted objects. Keep the current objective and the places you have already searched in "memory",
  and go somewhere new each time instead of circling. If you have walked around for a few turns without finding the
  next step, stop running: open the map or quest log, re-read the last hint, or look up the walkthrough.
- Action, shooter, platformer: move with purpose toward the level exit or the next objective, deal with threats,
  avoid hazards you have learned about.
- Racing, sports, rhythm, card or turn-based: learn the rules from the tutorial, then play by them (accelerate and
  steer; pick the move the menu offers; follow the beat).
Never just run around: every burst of moves should have a reason you can state in "memory".

PLAY BOLDLY. You get one look every several seconds, so make every turn count: plan a whole sequence of 4 to 8 moves
(turn, walk, turn again, interact), chaining turning and walking ("MOVE_UP+LOOK_LEFT"), and walk in 2 to 4 second
bursts. End a turn with WAIT only while the game is loading or a cutscene plays, never after a move just to look.
When exploring a level, open the game's map (often TOUCHPAD, OPTIONS, or a button the guide names) and plan the route
to the next objective before moving.

KNOW THE GAME. On your first turn, before choosing moves, use WebSearch and WebFetch once to look up this game's
controls and a guide or walkthrough for its opening (for open-source games, its source code or manual), and put
the facts that matter in "memory". Search again whenever you are stuck: the next objective, a puzzle solution, how
to get past an area. Think like a player who has read the guide: know where you are going and why.

WHEN STUCK. If the view stops changing or you keep failing, think about why from the two frames and what you read,
then do something genuinely different (another route, another button, the map or pause menu, the guide). Never
repeat a move that changed nothing three times.

RULES. Never quit the game, delete or overwrite saves, change system settings or buy anything. Choose New Game / Start
when offered, the easiest difficulty if asked. Read every tutorial or controls screen and put the controls in "memory".

Game notes (from Blu and from this game's earlier testers):
{notes}

Your memory so far:
{memory}

Your last moves (oldest first):
{history}

Answer with exactly one JSON object on the last line, nothing after it:
{{"screen": "what is on screen, one sentence", "scene": "menu|gameplay|dialogue|loading|other", "moves": [{{"button": "MOVE_UP+R2", "hold": 2.0}}], "memory": "where you are, the controls you know, and what you try next, under 400 characters", "done": false}}
"moves" has 1 to 8 entries, done in order. This whole test is yours: you are this game's own tester and this
conversation continues every turn, so you remember everything you tried. Set "done": true only when the test has what
it needs (you have been playing real gameplay for several minutes, or you are stuck for good after the whole escape
routine and every idea, or the game froze), and say which in "screen"."""

LESSONS_PROMPT = """The test is over. Write what the next tester of this same game must know to get further faster: the
controls you worked out, the menu path to gameplay, where you got stuck and what got you out, and the next goal. Then,
on a line starting with GENERAL:, one lesson about playing games through this controller that helps with any game (or
GENERAL: none). Plain text, under 1200 characters, no JSON."""


REFLECT_PROMPT = """Stop and learn before the next move (Blu: it must get better while it plays, not repeat mistakes).
Here are your recent turns, oldest first: what you saw, what you pressed, and whether the view changed afterwards:
{log}

Rules you already follow:
{rules}

Were you making progress toward an objective, or running around without one? What went wrong or wasted time, and
what worked? Write up to 5 short, concrete rules for the rest of this game (include the current objective), for
example "- The green floor on the right drains health: stay on the grey path" or "- CROSS opens doors here, SQUARE
does nothing". Keep the old rules that still hold, drop wrong ones, add new ones. Answer only with lines starting
with "- ", nothing else."""

DEATH = re.compile(r"\b(died|dies|dying|dead|death|game over|respawn\w*|killed|restart(ing|s)? (the )?level|level restart\w*|health (0|critical|very low)|lost a life|try again)", re.I)


def claude_reflect(session, history, rules):
    import claude_bridge
    lines = "\n".join(f"- saw: {h.get('screen', '')[:140]} | pressed: {h['action']} | view changed after: {'yes' if h['changed'] else 'NO'}" for h in history[-10:])
    answer = claude_bridge.run("play", REFLECT_PROMPT.format(log=lines or "- none", rules="\n".join(rules) or "- none yet"), session=session, resume=True)
    if claude_bridge.unavailable(answer):
        return rules
    new = [l.strip()[:220] for l in answer.splitlines() if l.strip().startswith("- ")][:5]
    return new or rules


def claude_turn(notes, memory, history, image, session=None, resume=False, before=None, rules=()):
    import claude_bridge
    PLAY_DIR.mkdir(parents=True, exist_ok=True)
    tag = "" if not session else "-" + session[:8]
    shot = PLAY_DIR / f"screen{tag}.png"
    prev = PLAY_DIR / f"before{tag}.png"
    shot.write_bytes(image)
    if before:
        prev.write_bytes(before)
    recent = "\n".join(f"- {h['action']}" + ("" if h["changed"] else " (screen did not change)") for h in history[-10:]) or "- none yet"
    stuck = sum(1 for h in history[-3:] if not h["changed"])
    if resume:
        prompt = (f"Next turn. Read the frame now at {shot}" + (f" and the frame from before your last moves at {prev}" if before else "")
                  + f" with your Read tool. Your last moves:\n{recent}\n"
                  + (("Rules you learned while playing (follow them):\n" + "\n".join(rules) + "\n") if rules else "")
                  + ("The view has not changed for several turns: work out why and try something genuinely different, or look it up.\n" if stuck >= 2 else "")
                  + "Answer with the same one-line JSON object as before (screen, scene, moves, memory, done).")
    else:
        prompt = CLAUDE_PLAY.format(screen=str(shot), buttons=", ".join(sorted(PAD_NAMES if PAD_FILE else set(KEYS) - {"F12"})) + ", WAIT", notes=notes, memory=memory or "(nothing yet)", history=recent)
    answer = claude_bridge.run("play", prompt, session=session, resume=resume)
    if claude_bridge.unavailable(answer):
        return None, answer
    try:
        reply = json.loads([l for l in answer.strip().splitlines() if l.strip().startswith("{")][-1])
    except (IndexError, ValueError):
        return {"screen": answer.strip()[:200], "scene": "other", "moves": [("WAIT", 1.5)], "memory": memory}, answer
    moves = []
    for m in reply.get("moves") or []:
        button = str(m.get("button", "")).upper().replace(" ", "") if isinstance(m, dict) else str(m).upper().replace(" ", "")
        if button == "WAIT" or valid_move(button):
            hold = m.get("hold", HOLD.get(button, 0.3)) if isinstance(m, dict) else HOLD.get(button, 0.3)
            try:
                hold = float(hold)
            except (TypeError, ValueError):
                hold = 0.3
            moves.append((button, max(0.05, min(hold, 5.0 if button != "WAIT" else 8.0))))
    reply["moves"] = moves[:8] or [("WAIT", 1.5)]
    return reply, answer


def claude_lessons(session):
    import claude_bridge
    answer = claude_bridge.run("play", LESSONS_PROMPT, session=session, resume=True)
    return "" if claude_bridge.unavailable(answer) else answer.strip()[:1500]


class Attached:
    """A game process started by someone else (the pipeline's test), with the poll() and returncode of a Popen."""
    def __init__(self, pid):
        self.pid, self.returncode = pid, None
        self.handle = ctypes.windll.kernel32.OpenProcess(0x1000 | 0x00100000, False, pid)

    def poll(self):
        if self.returncode is not None:
            return self.returncode
        if not self.handle:
            self.returncode = 0
            return 0
        code = wt.DWORD()
        ctypes.windll.kernel32.GetExitCodeProcess(self.handle, ctypes.byref(code))
        if code.value != 259:
            self.returncode = code.value
        return self.returncode


def default_brain():
    import claude_bridge
    if claude_bridge.uncapped_today():
        return "claude"
    return "claude" if claude_bridge.app_setting("aiPlayerBrain", "local") == "claude" else "local"


# --- the session ----------------------------------------------------------------------------------------------------
def main():
    # Blu 11-08: "remove the AIs playing games, they're not smart enough". AI play is off everywhere (pipeline,
    # GPU lanes, assistant, showcase); ANYPS5_ALLOW_AI_PLAYER=1 is the only way to run it by hand.
    if os.environ.get("ANYPS5_ALLOW_AI_PLAYER") != "1":
        print("AI play is switched off (Blu 11-08). Set ANYPS5_ALLOW_AI_PLAYER=1 to run it by hand.")
        return 0
    ap = argparse.ArgumentParser()
    ap.add_argument("game")
    ap.add_argument("--minutes", type=float, default=15)
    ap.add_argument("--brain", choices=("local", "claude"), default=None)
    ap.add_argument("--attach", type=int, default=0, help="play a game a test already started (its process id) instead of starting one")
    ap.add_argument("--pad-file", default="", help="the game's APS5_PAD_INPUT_FILE: buttons go there, so the session never needs the keyboard, the mouse or the foreground")
    o = ap.parse_args()
    global PAD_FILE
    PAD_FILE = Path(o.pad_file) if o.pad_file else None
    brain = o.brain or default_brain()
    # Blu 11-07: "for each game, a Claude Code dedicated for the test so it can get through, and whatever the time
    # limits for them is it": one Claude conversation per session, resumed every turn; --minutes 0 plays until that
    # Claude says done (or its limits stop it), with a two-hour ceiling so a session can never run forever.
    session = str(uuid.uuid4()) if brain == "claude" else None
    minutes = o.minutes if o.minutes > 0 else 120
    memory = ""
    game = Path(o.game)
    cfg = json.loads((game / "game.json").read_text(encoding="utf-8-sig"))
    exe = game / f"{cfg.get('exeName') or cfg['title']}.exe"
    stamp = time.strftime("%Y-%m-%d-%H%M")
    run = game / "ai-runs" / stamp
    run.mkdir(parents=True, exist_ok=True)
    log = (run / "steps.jsonl").open("w", encoding="utf-8")
    notes_file = game / "ai-notes.txt"
    notes = notes_file.read_text(encoding="utf-8") if notes_file.exists() else "(no notes for this game)"
    # What this game's earlier Claude testers learned, and the lessons every tester shares (Blu 11-07: train the AI).
    lessons_file = game / "ai-lessons.md"
    skills_file = TOOLS / "play-skills.md"
    if lessons_file.exists():
        notes += "\n\nWhat earlier testers of this game learned (newest last):\n" + lessons_file.read_text(encoding="utf-8")[-4000:]
    if skills_file.exists():
        notes += "\n\nLessons for playing any game through this controller:\n" + skills_file.read_text(encoding="utf-8")[-3000:]
    if (game / "ai-rules-live.md").exists():
        notes += "\n\nRules another tester of this game learned while playing:\n" + (game / "ai-rules-live.md").read_text(encoding="utf-8")[-2000:]

    saves = game / "_sd"
    backup = run / "_sd-backup"
    had_saves = saves.exists()
    if had_saves:
        shutil.copytree(saves, backup)
    showcase_dir = run / "showcase"

    servers = [start_server(EYES), start_server(BRAIN)] if brain == "local" else []
    env = dict(os.environ, ANYPS5_INPUT_CONFIG=str(TOOLS / "ai-input.ini"))
    proc = Attached(o.attach) if o.attach else subprocess.Popen([str(exe)], cwd=str(game), env=env)
    result, steps, shots, history = "time limit reached", 0, 0, []
    try:
        hwnd = None
        for _ in range(300):  # wait for the window and its first frames
            time.sleep(1)
            if proc.poll() is not None:
                break
            hwnd = hwnd or window_of(proc.pid)
            if hwnd and "FPS:" in title_of(hwnd):
                break
        if proc.poll() is not None or not hwnd:
            result = f"game ended before play (exit {proc.poll()})"
            return
        time.sleep(8)
        if not PAD_FILE:
            bring_forward(hwnd)
            if brain == "local":
                maximize(hwnd)
        time.sleep(2)
        cursor = wt.POINT()
        user32.GetCursorPos(ctypes.byref(cursor))
        home = (cursor.x, cursor.y)
        end = time.time() + minutes * 60
        last_small, last_shot, scene, refocused = None, 0.0, None, 0
        last_big = None
        rules, last_reflect = [], 0
        live_rules = game / "ai-rules-live.md"
        learned, control_tries = False, 0
        showcase_dir.mkdir(exist_ok=True)
        while time.time() < end:
            if proc.poll() is not None:
                result = f"game exited (code 0x{proc.returncode & 0xFFFFFFFF:08X}) after {steps} moves"
                break
            user32.GetCursorPos(ctypes.byref(cursor))
            if not PAD_FILE and (abs(cursor.x - home[0]) > 4 or abs(cursor.y - home[1]) > 4):
                result = f"stopped: the mouse moved, someone is using the PC (after {steps} moves)"
                break
            # Another window in front (a notification, the Claude app showing that a session runs) is not a person
            # at the PC: bring the game back. Only the mouse stops a session. In pad mode the foreground is Blu's.
            if not PAD_FILE and user32.GetForegroundWindow() != root_of(hwnd):
                refocused += 1
                if refocused > 30:
                    result = f"stopped: the game kept losing the foreground (after {steps} moves)"
                    break
                bring_forward(hwnd)
                user32.GetCursorPos(ctypes.byref(cursor))
                home = (cursor.x, cursor.y)
            w, h, bgra = capture(hwnd)
            if w <= 0 or h <= 0:  # minimized or not shown yet (Ghost Song's first session died dividing by a zero width)
                if not PAD_FILE:
                    bring_forward(hwnd)
                    maximize(hwnd)
                time.sleep(2)
                continue
            sw, sh, small = shrink(w, h, bgra, 448)
            image = png(sw, sh, small)
            if brain == "claude":
                changed = difference(small, last_small) > 2.0
                if history:
                    history[-1]["changed"] = changed
                if steps % 5 == 0:
                    (run / f"frame-{steps:04d}.png").write_bytes(image)
                reply, raw = None, ""
                for attempt in range(3):
                    reply, raw = claude_turn(notes, memory, history, png(*shrink(w, h, bgra, 896)), session, resume=steps > 0 or attempt > 0, before=last_big, rules=rules)
                    if reply is not None or "cap reached" in raw or "not signed in" in raw:
                        break
                    time.sleep(3)
                if reply is None:
                    result = f"stopped: Claude could not play ({raw[:150]}) after {steps} moves"
                    break
                last_big = png(*shrink(w, h, bgra, 896))
                memory = str(reply.get("memory") or memory)[:500]
                screen = str(reply.get("screen", ""))
                bright = sum(small[::7]) / max(1, len(small[::7])) > 30
                big = difference(small, scene) > 25.0
                if bright and shots < 8 and time.time() - last_shot > 25 and (big or time.time() - last_shot > 90):
                    kind = "g" if reply.get("scene") == "gameplay" else "m"
                    (showcase_dir / f"{kind}-{shots:02d}.png").write_bytes(window_png(hwnd))
                    shots += 1
                    last_shot = time.time()
                    scene = small
                if not PAD_FILE and user32.GetForegroundWindow() != root_of(hwnd):
                    bring_forward(hwnd)
                    user32.GetCursorPos(ctypes.byref(cursor))
                    home = (cursor.x, cursor.y)
                if reply.get("done") is True:
                    result = f"Claude finished the test after {steps} moves: {screen[:160]}"
                    log.write(json.dumps({"step": steps, "time": time.strftime("%H:%M:%S"), "brain": "claude", "screen": screen, "done": True}) + "\n")
                    log.flush()
                    break
                done = []
                for button, hold in reply["moves"]:
                    if proc.poll() is not None:
                        break
                    if button == "WAIT":
                        time.sleep(hold)
                    else:
                        press(button, hold)
                        time.sleep(0.15)
                    done.append(f"{button} {hold:.1f}s")
                action = ", ".join(done) or "WAIT"
                steps += 1
                history.append({"action": action, "changed": True, "screen": screen})
                # Learn while playing: every 8 moves, after a death, and when the view stopped changing, the tester
                # reviews its recent turns and rewrites its rules; they go into every later turn and, live, into the
                # game's ai-rules-live.md, which the game's other testers (a lane and the main test) read at start.
                not_moving = len(history) >= 3 and sum(1 for h in history[-3:-1] if not h["changed"]) >= 2
                if steps - last_reflect >= 8 or DEATH.search(screen) or (not_moving and steps - last_reflect >= 3):
                    rules = claude_reflect(session, history, rules)
                    last_reflect = steps
                    live_rules.write_text(f"# Rules the Claude tester learned while playing ({stamp}, after {steps} moves)\n" + "\n".join(rules) + "\n", encoding="utf-8")
                    log.write(json.dumps({"step": steps, "time": time.strftime("%H:%M:%S"), "reflect": rules}) + "\n")
                    print(f"move {steps}: learned " + " | ".join(rules)[:300], flush=True)
                log.write(json.dumps({"step": steps, "time": time.strftime("%H:%M:%S"), "brain": "claude", "screen": screen, "scene": reply.get("scene"),
                                      "action": action, "memory": memory, "changed_before": changed}) + "\n")
                log.flush()
                print(f"move {steps}: Claude sees \"{screen[:110]}\" -> presses {action}", flush=True)
                last_small = small
                time.sleep(0.5)
                continue
            changed = difference(small, last_small) > 2.0
            if history:
                history[-1]["changed"] = changed
            big = difference(small, scene) > 25.0
            bright = sum(small[::7]) / max(1, len(small[::7])) > 30  # no screenshots of black or fading screens
            if steps % 10 == 0:
                (run / f"frame-{steps:04d}.png").write_bytes(image)
            screen = describe(image)
            s = screen.lower()
            # Showcase shots: the maximized window (title bar with the game and its FPS + the picture), at most 8, on a
            # new scene or every 90 s. Gameplay ("g") and menus/logos ("m") are kept apart so gameplay comes first.
            if bright and shots < 8 and time.time() - last_shot > 25 and (big or time.time() - last_shot > 90):
                menuish = any(k in s for k in ("menu", "logo", "loading", "title screen", "press any", "highlighted", "option", "setting", "keyboard", "warning"))
                kind = "m" if menuish else "g"
                (showcase_dir / f"{kind}-{shots:02d}.png").write_bytes(window_png(hwnd))
                shots += 1
                last_shot = time.time()
                scene = small
            # Learn the controls first, the way Blu's showcase sessions went: open the game's own Controls / How to
            # play screen from the menu, have the eyes read it, add that to the notes, back out with CIRCLE.
            if not learned and control_tries < 10 and "menu" in s and re.search(r"\b(controls?|how to play|controller)\b", s):
                label = highlighted(s) or ""
                if any(k in label for k in ("control", "how to play")):
                    press("CROSS")
                    time.sleep(3)
                    cw, ch, cb = capture(hwnd)
                    controls = chat(EYES["port"], [{"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png(*shrink(cw, ch, cb, 640))).decode()}},
                        {"type": "text", "text": "This screen shows the game's controls. List every action and the PlayStation button for it, one per line, like 'jump: CROSS'."}]}], 200)
                    (run / "controls.txt").write_text(controls, encoding="utf-8")
                    notes += "\n\nControls, read from the game's own controls screen:\n" + controls
                    press("CIRCLE")
                    time.sleep(2)
                    learned = True
                    action, reply = "CROSS+CIRCLE", "read the controls screen"
                else:
                    press("DOWN")
                    control_tries += 1
                    action, reply = "DOWN", "moving to the controls entry"
                steps += 1
                history.append({"action": action, "changed": True})
                log.write(json.dumps({"step": steps, "time": time.strftime("%H:%M:%S"), "screen": screen, "action": action, "reply": reply, "changed_before": changed}) + "\n")
                log.flush()
                last_small = small
                continue
            action, reply = decide(notes, screen, history)
            if action != "WAIT":
                press(action, HOLD.get(action, 0.12))
            else:
                time.sleep(1.5)
            steps += 1
            history.append({"action": action, "changed": True})
            log.write(json.dumps({"step": steps, "time": time.strftime("%H:%M:%S"), "screen": screen, "action": action, "reply": reply, "changed_before": changed}) + "\n")
            log.flush()
            print(f"move {steps}: sees \"{screen[:110]}\" -> presses {action}", flush=True)
            last_small = small
            time.sleep(0.8)
    finally:
        release_all()
        if brain == "claude" and session and steps >= 3:
            try:
                lessons = claude_lessons(session)
                if lessons:
                    game_part, _, general = lessons.partition("GENERAL:")
                    with lessons_file.open("a", encoding="utf-8") as f:
                        f.write(f"\n## {stamp} ({steps} moves, {result})\n{game_part.strip()}\n")
                    general = general.strip()
                    if general and general.lower().rstrip('.') != "none":
                        with skills_file.open("a", encoding="utf-8") as f:
                            f.write(f"- {general.splitlines()[0][:300]} ({cfg['title']}, {stamp})\n")
            except Exception as e:
                print(f"lessons not saved: {e}", flush=True)
        if proc.poll() is None and not o.attach:
            proc.kill()
            proc.wait(10)
        if o.attach:
            for _ in range(120):
                if proc.poll() is not None:
                    break
                time.sleep(1)
        for s in servers:
            if s:
                s.kill()
        crashed = (game / "anyps5.log").exists() and "FATAL" in (game / "anyps5.log").read_text(encoding="utf-8", errors="replace")
        if crashed:
            shutil.copy2(game / "anyps5.log", run / "anyps5-crash.log")
        if backup.exists():  # put Blu's saves back exactly as they were
            shutil.rmtree(saves, ignore_errors=True)
            shutil.copytree(backup, saves)
        elif not had_saves:  # no saves before: drop the session's own
            shutil.rmtree(saves, ignore_errors=True)
        log.close()
        # Number the showcase shots for posting: gameplay first (the first one becomes a forum post's cover).
        final = run / "showcase-final"
        picked = sorted(showcase_dir.glob("g-*.png")) + sorted(showcase_dir.glob("m-*.png")) if showcase_dir.exists() else []
        if picked:
            final.mkdir(exist_ok=True)
            for i, p in enumerate(picked[:6], 1):
                shutil.copy2(p, final / f"{i}-{'gameplay' if p.name.startswith('g') else 'menu'}.png")
            # Also into the reports, ready to post: D:\AnyPS5 Games\_reports\showcase\<game folder>\ai-<stamp>\
            ready = TOOLS.parent / "_reports" / "showcase" / game.name / f"ai-{stamp}"
            shutil.copytree(final, ready, dirs_exist_ok=True)
        moves = [json.loads(l) for l in (run / "steps.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        (run / "summary.md").write_text("\n".join([
            f"# AI play session {stamp}: {cfg['title']} ({'Claude' if brain == 'claude' else 'local models'})", "",
            f"- result: {result}" + ("; the game crashed (anyps5-crash.log)" if crashed else ""),
            f"- moves: {steps}, showcase shots: {shots} (numbered for posting in showcase-final\\, gameplay first)",
            f"- controls learned from the game's menu: {'yes (controls.txt)' if (run / 'controls.txt').exists() else 'no'}",
            f"- saves: {'restored from the backup' if backup.exists() else 'none existed'}", "",
            "Last screens:", ""] + [f"- {m['time']} {m['action']}: {m['screen'][:200]}" for m in moves[-8:]]), encoding="utf-8")
        if memory:
            (run / "claude-memory.txt").write_text(memory, encoding="utf-8")
        print(f"{cfg['title']}: {result}; {steps} moves{' by Claude' if brain == 'claude' else ''}, {shots} screenshots; {run}")


if __name__ == "__main__":
    main()
