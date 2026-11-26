"""Reverse-engineering skills for the AnyPS5 Assistant, built on REA (Ghidra) and the files AnyPS5 already makes.

REA's Ghidra on Windows reads Windows programs only, not PS5 ELF files. AnyPS5's relinker turns every game program
into one: the game's .exe (from eboot.bin) and app0\\...\\<module>.prx.guest.prx (from its .prx modules). Those hold
the game's own code, at the same layout as on the PS5. What they lack are names for the PS5 functions the game calls:
an import is a 6-byte stub "jmp [slot]", and the relinker's registry (<module>.registry.json, written by the import
audit) says which NID each slot is. The NID database (nid-names.csv, offline) names the NID. With those, the game
code Ghidra decompiles reads with real PS5 function names, the same way Claude reads it.

The skills answer the questions that matter when making a game work on AnyPS5:
  how_game_calls(game, function)       the game code around a call to a PS5 function: its arguments, and what the
                                       game does with the result (what AnyPS5 must return)
  game_crash_code(game)                the game function that was running when the last crash happened
  decompile_game_code(game, where)     one game function by address (0x...), <module>+0x... or name
  game_strings(game, text)             which game code uses a message or file name from the log
"""
import base64
import glob
import hashlib
import json
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
GAMES = Path(r"D:\AnyPS5 Games")  # the game folders stay on D: (11-03 _tools moved to C: behind a link)
AUDIT = HERE / "workspace" / "audit"
OBJDUMP = r"D:\AnyPS5-tools\mingw-extract\mingw64\bin\objdump.exe"
SUFFIX = bytes([0x51, 0x8D, 0x64, 0xA6, 0x35, 0xDE, 0xD8, 0xC1, 0xE6, 0xB0, 0x39, 0xB1, 0xC3, 0xE5, 0x52, 0x30])
CHARSET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+-"

STATE = {"rea": None, "open": None, "names": None, "stubs": {}, "bases": {}}


def nid_of(symbol):
    """A PS5 function's NID, as AnyPS5's tools/nid_names.py computes it."""
    digest = hashlib.sha1(symbol.encode() + SUFFIX).digest()
    rev = bytes(digest[7 - i] for i in range(8))
    out = []
    for i in range(0, 6, 3):
        triple = (rev[i] << 16) | (rev[i + 1] << 8) | rev[i + 2]
        out += [CHARSET[(triple >> 18) & 63], CHARSET[(triple >> 12) & 63], CHARSET[(triple >> 6) & 63], CHARSET[triple & 63]]
    tail = (rev[6] << 16) | (rev[7] << 8)
    out += [CHARSET[(tail >> 18) & 63], CHARSET[(tail >> 12) & 63], CHARSET[(tail >> 6) & 63]]
    return "".join(out)


def name_of(nid):
    if STATE["names"] is None:
        names = {}
        with (HERE / "nid-names.csv").open(encoding="utf-8", errors="replace") as f:
            for line in f:
                parts = line.rstrip("\n").split(" ", 1)
                if len(parts) == 2:
                    names[parts[0]] = parts[1]
        STATE["names"] = names
    return STATE["names"].get(nid.split("#")[0], nid)


def rea():
    if STATE["rea"] is None:
        import sys
        sys.path.insert(0, str(HERE))
        import rea_client
        STATE["rea"] = rea_client.Rea()
    return STATE["rea"]


def close():
    if STATE["rea"]:
        STATE["rea"].close()
        STATE["rea"] = None
        STATE["open"] = None


def ask(tool, args):
    """One REA call; returns its normalized result (REA wraps every answer in a large evidence record)."""
    out = rea().call(tool, args)
    try:
        d = json.loads(out[7:] if out.startswith("ERROR: ") else out)
    except ValueError:
        raise RuntimeError(out[:400])
    if "error" in d:
        raise RuntimeError(d["error"].get("message", str(d["error"]))[:400])
    return d.get("normalized_result")


def open_program(path):
    """Opens a converted game program in REA (Ghidra analyses it: seconds for a module, minutes for a large .exe)."""
    path = str(path)
    if STATE["open"] != path:
        ask_open = rea().call("open_binary", {"path": path})
        if '"error"' in ask_open[:200]:
            raise RuntimeError(ask_open[:400])
        STATE["open"] = path
        STATE["stubs"] = {}
    return path


def image_base(path):
    """(PE image base, address of the first section): ELF address 0 sits at the first section."""
    if path not in STATE["bases"]:
        head = subprocess.run([OBJDUMP, "-p", "-h", path], capture_output=True, text=True, errors="replace").stdout
        base = int(re.search(r"ImageBase\s+([0-9a-fA-F]+)", head).group(1), 16)
        first = re.search(r"^\s*0\s+\S+\s+[0-9a-fA-F]+\s+([0-9a-fA-F]+)", head, re.M)
        STATE["bases"][path] = (base, int(first.group(1), 16) if first else base)
    return STATE["bases"][path]


def game_dir(game):
    p = Path(game)
    if p.is_dir():
        return p
    hits = [d for d in GAMES.iterdir() if d.is_dir() and not d.name.startswith("_") and game.lower() in d.name.lower()]
    return hits[0] if len(hits) == 1 else None


def programs(d):
    """The game's converted programs: {"main": <game>.exe, "<module>.prx": <...>.prx.guest.prx}."""
    out = {}
    cfg = json.loads((d / "game.json").read_text(encoding="utf-8-sig"))
    exe = d / f"{cfg.get('exeName') or cfg.get('title')}.exe"
    if exe.exists():
        out["main"] = exe
    for f in (d / "app0").rglob("*.guest.prx"):
        out[f.name[:-len(".guest.prx")]] = f
    return out


def registries(d):
    """{program key: [{nid, library, targetOffset}]} from the import audit (missing_functions makes it)."""
    out = {}
    for f in glob.glob(str(AUDIT / d.name / "work" / "output" / "*.registry.json")):
        name = Path(f).name
        key = "main" if name == "app.registry.json" else name[len("app."):-len(".guest.prx.registry.json")]
        data = json.loads(Path(f).read_text(encoding="utf-8"))
        entries = data if isinstance(data, list) else next((v for v in data.values() if isinstance(v, list)), [])
        out[key] = [e for e in entries if isinstance(e, dict) and "nid" in e]
    return out


def stub_name(path, address, reg):
    """The PS5 function an import stub (jmp [slot]) jumps to, or None for ordinary game code."""
    if address in STATE["stubs"]:
        return STATE["stubs"][address]
    name = None
    try:
        raw = ask("read_bytes", {"address": hex(address), "length": 6})
        hexs = raw.get("bytes_hex") if isinstance(raw, dict) else None
        b = bytes.fromhex(hexs) if isinstance(hexs, str) else None
        if b and b[:2] == b"\xff\x25":
            slot = address + 6 + int.from_bytes(b[2:6], "little", signed=True)
            first = image_base(path)[1]
            for e in reg:
                if first + int(e["targetOffset"], 16) == slot:
                    name = name_of(e["nid"])
                    break
    except Exception:
        pass
    STATE["stubs"][address] = name
    return name


def readable(path, code, reg, limit=40):
    """Decompiled code with FUN_<address> import stubs renamed to their PS5 function names."""
    seen = []
    for m in re.finditer(r"FUN_([0-9a-fA-F]{6,16})", code):
        if m.group(0) not in seen:
            seen.append(m.group(0))
    for fun in seen[:limit]:
        name = stub_name(path, int(fun[4:], 16), reg)
        if name:
            code = code.replace(fun, name)
    return code.replace("\r\n", "\n")


def decompile(path, where, reg, max_chars=3500):
    code = ask("procedure_pseudo_code", {"procedure": where})
    if isinstance(code, dict):
        code = code.get("pseudo_code") or code.get("code") or json.dumps(code)
    code = readable(path, str(code), reg)
    return code if len(code) <= max_chars else code[:max_chars] + f"\n... ({len(code) - max_chars} more characters)"


# --- the skills ------------------------------------------------------------------------------------------------------
def how_game_calls(game, function, missing_functions=None):
    """Where and how the game calls one PS5 function, decompiled with PS5 names."""
    d = game_dir(game)
    if not d:
        return f"no game folder matches '{game}'"
    regs = registries(d)
    if not regs:
        if missing_functions is None:
            return "Run missing_functions for this game first (it writes the import tables these skills read)."
        missing_functions(str(d))
        regs = registries(d)
    nid = function if re.fullmatch(r"[A-Za-z0-9+-]{11}", function) else nid_of(function)
    progs = programs(d)
    users = [(key, e) for key, entries in regs.items() for e in entries if e["nid"].split("#")[0] == nid]
    if not users:
        return f"The game's programs do not import {function} (NID {nid}) directly. Check the spelling, or it is called only through another library."
    out = [f"{function} (NID {nid}) is imported by: " + ", ".join(sorted({k for k, _ in users}))]
    # Smallest program first: a module analyses in seconds; a large game .exe can exceed REA's analysis budget.
    users.sort(key=lambda u: progs[u[0]].stat().st_size if u[0] in progs else 1 << 62)
    shown = 0
    for key, e in users:
        if shown >= 2:
            break
        path = progs.get(key)
        if not path:
            out.append(f"[{key}] its converted program was not found in the game folder (rebuild the game)")
            continue
        try:
            open_program(path)
            slot = image_base(str(path))[1] + int(e["targetOffset"], 16)
            stubs = ask("xrefs", {"address": hex(slot)}) or []
            callers = []
            for s in stubs[:3]:
                got = ask("procedure_callers", {"procedure": s}) or []
                callers += [c for c in got if c not in callers]
            out.append(f"\n[{key}] {path.name}: import slot {hex(slot)}, stub {', '.join(stubs) or 'none'}, called from {len(callers)} place(s): {', '.join(callers[:8])}")
            for c in callers[:2]:
                out.append(f"\n--- game function {c} (calls {function}) ---\n" + decompile(str(path), c, regs.get(key, [])))
            shown += 1
        except RuntimeError as err:
            out.append(f"\n[{key}] {path.name} ({path.stat().st_size // 2**20} MB) could not be analysed: {str(err)[:160]}")
            STATE["open"] = None
    out.append("\nRead it for: the arguments passed, and what the game does with the result (if (x < 0) = treated as an error; "
               "a value stored for later = must be valid). That is what AnyPS5's implementation must return.")
    return "\n".join(out)


def decompile_game_code(game, where):
    """One game function: where = 0x<address>, <module>+0x<offset> (as in crash logs) or a function name."""
    d = game_dir(game)
    if not d:
        return f"no game folder matches '{game}'"
    progs, regs = programs(d), registries(d)
    m = re.match(r"(.+?)\+0x([0-9a-fA-F]+)$", where.strip())
    key = "main"
    if m:
        mod = m.group(1).strip()
        key = "main" if mod.lower().endswith(".exe") else mod.replace(".guest.prx", "")
        if key not in progs:
            return f"{mod} is not one of the game's programs (it is an AnyPS5 or Windows library: look at AnyPS5's source instead). Programs: {', '.join(progs)}"
        path = str(progs[key])
        address = hex(image_base(path)[0] + int(m.group(2), 16))
    else:
        path = str(progs["main"])
        address = where.strip()
    open_program(path)
    proc = ask("resolve_containing_procedure", {"address": address}) if address.startswith("0x") else None
    target = proc["procedure"]["address"] if isinstance(proc, dict) and proc.get("found") else address
    return f"[{Path(path).name}] {where} is in game function {target}:\n" + decompile(path, target, regs.get(key, []))


def game_crash_code(game):
    """The game code that was running when the game last crashed (from the newest crash log's stack)."""
    d = game_dir(game)
    if not d:
        return f"no game folder matches '{game}'"
    logs = sorted((d / "crash-logs").glob("*.log"), key=lambda p: p.stat().st_mtime) if (d / "crash-logs").exists() else []
    log = logs[-1] if logs else d / "anyps5.log"
    if not log.exists():
        return "no crash log"
    text = log.read_text(encoding="utf-8", errors="replace")
    fatal = text.find("FATAL")
    frames = re.findall(r"([^\s][^+\n]*?\.(?:exe|guest\.prx))\+0x([0-9a-fA-F]+)", text[fatal if fatal >= 0 else 0:])
    if not frames:
        return f"{log.name}: the crash happened outside the game's own code (in AnyPS5 or Windows libraries); use crash_summary and AnyPS5's source."
    mod, off = frames[0]
    try:
        return f"Crash log {log.name}: the first game-code frame is {mod.strip()}+0x{off}.\n" + decompile_game_code(str(d), f"{mod.strip()}+0x{off}")
    except (RuntimeError, AttributeError, OSError, ValueError) as error:
        return f"Crash log {log.name}: the first game-code frame is {mod.strip()}+0x{off}. The decompiler could not read this game: {error}"


def game_strings(game, text):
    """Which game code uses a string (a log message, a file name): the strings and the functions that use them."""
    d = game_dir(game)
    if not d:
        return f"no game folder matches '{game}'"
    progs, regs = programs(d), registries(d)
    out = []
    for key in ["main"] + [k for k in progs if k != "main"]:
        path = str(progs[key])
        open_program(path)
        hits = ask("search_strings", {"pattern": text}) or []
        for h in hits[:5]:
            addr = h.get("address") if isinstance(h, dict) else h
            value = h.get("value") if isinstance(h, dict) else ""
            users = ask("xrefs", {"address": addr}) or []
            funcs = []
            for u in users[:5]:
                p = ask("resolve_containing_procedure", {"address": u})
                if isinstance(p, dict) and p.get("found"):
                    funcs.append(p["procedure"]["address"])
            out.append(f"[{Path(path).name}] {addr} {str(value)[:120]!r} used by {', '.join(funcs) or 'no code found'}")
        if len(out) >= 8:
            break
    return "\n".join(out) or f"no string containing {text!r} in the game's programs"
