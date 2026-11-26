"""Xbox and Exit 9 work for the local AI (11-19): the steps Claude ran by hand, as fixed commands.

Every command is fixed text here; a game folder must exist under the converted-games folder and a shader address
must be hex, so nothing the model writes reaches a shell. Nothing installs to the Xbox, uninstalls, deletes or
posts. The PC test refuses while a game runs (Blu plays) or the PC lock is held.

usage: python xbox_work.py games
       python xbox_work.py build                     rebuild xvk (the Vulkan to D3D12 layer)
       python xbox_work.py test <game> [seconds] [storage] [direct] [debug]
       python xbox_work.py convert <game>            relink a D:\\AnyPS5 Games title for the Xbox (SSE)
       python xbox_work.py diag <shader hex>         queue an Exit 9 IR dump run on the Chromebook
       python xbox_work.py ir                        summarize the last Exit 9 IR dump
       python xbox_work.py crosstest <game>          check the on-device converter (xcross) against dxbc.py output
       python xbox_work.py localtest [seconds]       run Celeste on the PC with on-device conversion (cold cache)
       python xbox_work.py xboxstate [shader dir]    Xbox idle/busy and how many shaders are converted
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROBE = Path(r"C:\AnyPS5-work\xbox-probe")
GAMES = Path(r"C:\AnyPS5-work\xbox-games")
LIVE = Path(__file__).resolve().parents[2] / "live"
CBSSH = r"C:\AnyPS5-work\cbssh.py"
EXIT9 = "/home/anyps5/games/The Exit 9 (PPSA20955)"
GAME_PATH = re.compile(r"^[A-Za-z]:\\AnyPS5 Games\\[^\\]+\\|\\steamapps\\common\\|\\Epic Games\\(?!.*\\Launcher\\)|\\Riot Games\\", re.I)
SKIP_PROC = re.compile(r"^(steam|steamwebhelper|UnityCrashHandler\d*|CrashReport.*|AnyPS5.*)$", re.I)


def run(cmd, timeout=600, cwd=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return r.returncode, r.stdout + r.stderr


def pc_busy():
    flag = LIVE / "gpu-busy.flag"
    if flag.exists() and flag.read_text(errors="replace").strip():
        return "the PC lock is held: " + flag.read_text(errors="replace").strip()[:80]
    rc, out = run(["powershell", "-NoProfile", "-Command", "Get-Process | Where-Object Path | ForEach-Object { $_.ProcessName + '|' + $_.Path }"], 60)
    for line in out.splitlines():
        name, _, path = line.partition("|")
        if GAME_PATH.search(path) and "\\_tools\\" not in path and not SKIP_PROC.match(name):
            return "a game is running (" + name + ")"
    return ""


def games():
    if not GAMES.exists():
        return "no converted games yet"
    return "\n".join(sorted(p.name for p in GAMES.iterdir() if p.is_dir() and (p / "game.json").exists()))


def find_game(name):
    name = (name or "").strip().lower()
    match = [p for p in GAMES.iterdir() if p.is_dir() and name and name in p.name.lower()] if GAMES.exists() else []
    return match[0] if len(match) == 1 else None


def build():
    rc, out = run(["cmd", "/c", str(PROBE / "xvk" / "build.cmd")], 900)
    errors = [l for l in out.splitlines() if "error C" in l or " error " in l.lower()]
    dll = PROBE / "xvk" / "bin" / "xvk.dll"
    return ("BUILT xvk.dll (%d KB)" % (dll.stat().st_size // 1024)) if rc == 0 and not errors else "BUILD FAILED:\n" + "\n".join(errors[:8] or out.splitlines()[-8:])


def test(game, seconds=100, storage=False, direct=False, debug=False):
    g = find_game(game)
    if not g:
        return "NOT RUN: name one converted game:\n" + games()
    busy = pc_busy()
    if busy:
        return "NOT RUN: " + busy
    seconds = max(30, min(int(seconds), 300))
    shutil.copy2(PROBE / "xvk" / "bin" / "xvk.dll", g / "vulkan-1.dll")
    work = Path(tempfile.mkdtemp(prefix="xvk-test-"))
    try:
        (work / "game.txt").write_text(str(g), encoding="utf-8")
        env = ["XVK_INT64=1", "ANYPS5_SINGLE_LANE_OWN_SELECTOR=1", "XVK_LOG=xvk.log", "XVK_SHADERS=xvk-shaders", "ANYPS5_SHADER_CACHE_DIR=%s" % (g / "shader_cache")]
        env += ["XVK_STORAGE_IMAGE=1"] if storage else []
        env += ["XVK_DIRECT_COPY=1"] if direct else []
        env += ["XVK_DEBUG=1"] if debug else []
        (work / "env.txt").write_text("\n".join(env) + "\n", encoding="utf-8")
        shutil.copy2(PROBE / "payload" / "out" / "gamehost.dll", work / "gamehost-test.dll")
        log = g / "xvk.log"
        if log.exists():
            log.unlink()
        try:
            run([sys.executable, str(PROBE / "payload" / "hostrun.py"), str(work / "gamehost-test.dll"), str(work), str(seconds)], seconds + 20, str(PROBE))
        except subprocess.TimeoutExpired:
            pass
        return summarize_log(log)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def summarize_log(log):
    if not log.exists():
        return "no xvk.log: the game did not reach Vulkan"
    lines = log.read_text(errors="replace").splitlines()
    presents = [l for l in lines if l.startswith("present ")]
    out = ["%d log lines, %d present marks" % (len(lines), len(presents))]
    ticks = [(int(l.split()[1]), int(l.split()[-1])) for l in presents if l.split()[-1].isdigit()]
    if len(ticks) >= 2 and ticks[-1][1] > ticks[0][1]:
        out.append("about %.2f fps (frames %d to %d)" % ((ticks[-1][0] - ticks[0][0]) * 1000.0 / (ticks[-1][1] - ticks[0][1]), ticks[0][0], ticks[-1][0]))
    out += [l[:240] for l in lines if l.startswith("perf:")][-2:]
    counts = {}
    for l in lines:
        for key in ("still running", "conversion failed", "pipeline state failed", "pipeline skipped", "d3d12:", "destroyed with GPU results pending"):
            if key in l:
                counts[key] = counts.get(key, 0) + 1
    out += ["%s: %d" % kv for kv in counts.items()]
    if counts.get("still running"):
        out.append("HUNG: a GPU batch never finished; rerun without storage/direct to bisect")
    return "\n".join(out)


def convert(game):
    src = Path(r"D:\AnyPS5 Games")
    match = [p.name for p in src.iterdir() if p.is_dir() and game and game.lower() in p.name.lower() and (p / "game.json").exists()]
    if len(match) != 1:
        return "NOT RUN: name one game folder; matches: " + ", ".join(match[:10])
    rc, out = run([sys.executable, str(PROBE / "xbox_convert.py"), match[0]], 1800, str(PROBE))
    return out.strip().splitlines()[-1] if rc == 0 else "CONVERT FAILED:\n" + "\n".join(out.strip().splitlines()[-8:])


def diag(shader):
    if not re.fullmatch(r"[0-9a-fA-F]{6,16}(,[0-9a-fA-F]{6,16}){0,7}", shader or ""):
        return "NOT QUEUED: the shader address must be hex, like 15c0f10000"
    body = '{"folder": "%s", "env": {"APS5_DUMP_IR": "%s", "APS5_TRACE_DISPATCH_IO": "2", "ANYPS5_NO_SHADER_CACHE": "1"}}' % (EXIT9, shader.lower())
    rc, out = run([sys.executable, CBSSH, "cat > /root/idle-tests/diagnose.json <<'EOF'\n%s\nEOF\necho queued" % body], 120)
    return "QUEUED: the Chromebook idle tester runs Exit 9 next with the IR dump; it may hang the GPU and reboot (expected). Read it with: ir" if "queued" in out else "NOT QUEUED: " + out[-300:]


def ir():
    rc, out = run([sys.executable, CBSSH, "cp \"%s/last-run.log\" /root/exit9-ir.log; grep -c '==== IR' /root/exit9-ir.log" % EXIT9], 120)
    local = Path(tempfile.gettempdir()) / "exit9-ir.log"
    env = dict(os.environ, MSYS_NO_PATHCONV="1")
    subprocess.run([sys.executable, CBSSH, "--get", "/root/exit9-ir.log", str(local)], capture_output=True, timeout=600, env=env)
    if not local.exists():
        return "no IR log fetched"
    text = local.read_text(errors="replace")
    local.unlink()
    dumps = re.split(r"^==== IR ", text, flags=re.M)[1:]
    if not dumps:
        return "no IR dump in the last run (was ANYPS5_NO_SHADER_CACHE=1 set? APS5_DUMP_IR is cache-neutral)"
    last = dumps[-1]
    head = last.splitlines()[0]
    blocks = re.findall(r"^Block \$(\d+)", last, re.M)
    loops = sum(1 for m in re.finditer(r"^Block \$(\d+)(.*?)(?=^Block |\Z)", last, re.M | re.S) for b in re.findall(r"Phi .*?\$(\d+)\]", m.group(2)) if int(b) >= int(m.group(1)))
    ops = {}
    for op in re.findall(r"= (\w+) ", last):
        ops[op] = ops.get(op, 0) + 1
    writes = len(re.findall(r"^\s+(ImageWrite|StoreBuffer\w*|Write\w*)", last, re.M))
    barriers = len(re.findall(r"Barrier", last))
    top = ", ".join("%s %d" % kv for kv in sorted(ops.items(), key=lambda kv: -kv[1])[:10])
    return "%d dumps; last: %s\n%d blocks, %d loop back-edges, %d writes, %d barriers\ntop ops: %s" % (len(dumps), head, len(blocks), loops, writes, barriers, top)


def scrub(text):
    host = (PROBE / "out" / ".host").read_text().strip()
    text = text.replace(host, "<xbox>") if host else text
    text = re.sub(r"AnyPS5\.Console_\S*", "AnyPS5.Console_<pkg>", text)
    return re.sub(r"[A-Z]:\\Users\\UserMgr\S*|\\Packages\\\S*", "<path>", text)


def crosstest(game):
    folder = find_game(game)
    if not folder:
        return "no such game folder"
    shaders = folder / "xvk-shaders"
    if not shaders.is_dir():
        return "no xvk-shaders in " + folder.name
    out = Path(tempfile.mkdtemp(prefix="xcross-"))
    try:
        rc, text = run([str(PROBE / "xvk" / "bin" / "xcrosstest.exe"), str(out), str(shaders)], timeout=3600)
        lines = text.splitlines()
        return "\n".join([l for l in lines if "DIFF" in l or "fail" in l.lower()][:20] + lines[-3:])
    finally:
        shutil.rmtree(out, ignore_errors=True)


def localtest(seconds=30):
    if pc_busy():
        return pc_busy()
    rc, text = run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(PROBE / "xvk" / "run-local-shaders.ps1"), "-Seconds", str(int(seconds))], timeout=int(seconds) + 900)
    return "\n".join(text.splitlines()[-15:])


def xboxstate(sub="xvk-shaders-dev4"):
    if not re.fullmatch(r"[A-Za-z0-9_\-/]+", sub):
        return "bad shader dir"
    host = (PROBE / "out" / ".host").read_text().strip()
    rc, a = run([sys.executable, "busy.py", host], timeout=120, cwd=str(PROBE))
    rc, b = run([sys.executable, "shaderstate.py", host, "game/" + sub], timeout=300, cwd=str(PROBE))
    return scrub((a.strip().splitlines() or [""])[-1] + "\n" + "\n".join(b.splitlines()[-10:]))


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a:
        print(__doc__)
    elif a[0] == "games":
        print(games())
    elif a[0] == "build":
        print(build())
    elif a[0] == "test" and len(a) > 1:
        print(test(a[1], next((int(x) for x in a[2:] if x.isdigit()), 100), "storage" in a, "direct" in a, "debug" in a))
    elif a[0] == "convert" and len(a) > 1:
        print(convert(" ".join(a[1:])))
    elif a[0] == "diag" and len(a) > 1:
        print(diag(a[1]))
    elif a[0] == "ir":
        print(ir())
    elif a[0] == "crosstest" and len(a) > 1:
        print(crosstest(" ".join(a[1:])))
    elif a[0] == "localtest":
        print(localtest(int(a[1]) if len(a) > 1 and a[1].isdigit() else 30))
    elif a[0] == "xboxstate":
        print(xboxstate(a[1] if len(a) > 1 else "xvk-shaders-dev4"))
    else:
        print(__doc__)
