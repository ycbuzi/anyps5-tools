import json, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "console"))
sys.path.insert(0, HERE)
OUT = r"C:\AnyPS5-work\xbox-games"
READY = os.path.join(HERE, "console", "xbox-ready.json")
PACKAGE = "AnyPS5.Console"


def host():
    return open(os.path.join(HERE, "out", ".host")).read().strip()


def run(args):
    h = host()
    r = subprocess.run([sys.executable] + args, cwd=HERE, capture_output=True, text=True, encoding="utf-8", errors="replace")
    text = (r.stdout + r.stderr).replace(h, "<xbox>")
    if r.returncode:
        raise SystemExit("failed: %s\n%s" % (" ".join(args[:2]), text[-1500:]))
    return text


def refresh(local):
    import shutil
    import xbox_convert
    libs = xbox_convert.copy_tree(xbox_convert.LIBSET, os.path.join(local, "libs"))
    dll = os.path.join(local, "vulkan-1.dll")
    if not os.path.exists(dll) or open(dll, "rb").read() != open(xbox_convert.XVK, "rb").read():
        shutil.copy2(xbox_convert.XVK, dll)
        libs += 1
    return libs


def pull(local, name):
    server = subprocess.Popen([sys.executable, "-u", "xserve.py", name, "180"], cwd=HERE, stdout=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    print(server.stdout.readline().strip())
    status = os.path.join(HERE, "out", "pull-status.txt")
    if os.path.exists(status):
        os.remove(status)
    run(["launchapp.py", host(), PACKAGE, "App"])
    time.sleep(10)
    run(["putfile.py", host(), os.path.join(HERE, "out", "pull.json"), "--package=" + PACKAGE])
    last, state = "", {}
    while server.poll() is None:
        time.sleep(15)
        try:
            run(["fetchfile.py", host(), "pull-status.txt", "--package=" + PACKAGE])
            state = json.load(open(status, encoding="utf-8-sig"))
        except (SystemExit, ValueError, OSError):
            continue
        line = "%s: file %s of %s, %.0f of %.0f MB fetched, %s s" % (state.get("state"), state.get("index"), state.get("files"), state.get("mb", 0), state.get("totalMb", 0), state.get("seconds"))
        if line != last:
            print(line, flush=True)
            last = line
        if state.get("state") in ("done", "failed"):
            break
    server.terminate()
    if state.get("state") != "done":
        raise SystemExit("pull did not finish: %s" % (state or "no status from the Xbox"))


def add(folder, status, convert, lan=False):
    if convert:
        import xbox_convert
        xbox_convert.convert(folder)
    local = os.path.join(OUT, os.path.basename(folder.rstrip("\\/")))
    name = os.path.basename(local)
    cfg = json.load(open(os.path.join(local, "game.json"), encoding="utf-8-sig"))
    start = time.time()
    print("%d libraries or xvk refreshed" % refresh(local))
    if lan:
        for attempt in range(3):
            try:
                pull(local, name)
                break
            except SystemExit as e:
                print(e, flush=True)
                if attempt == 2:
                    raise
    else:
        out = run(["pushtree.py", host(), local, "games\\" + name, "--package=" + PACKAGE])
        print(out.strip().splitlines()[-1] if out.strip() else "pushed")
    if os.path.isdir(os.path.join(local, "shader_cache")):
        run(["pushtree.py", host(), os.path.join(local, "shader_cache"), "games\\%s\\shader_cache" % name, "--package=" + PACKAGE])
    ready = json.load(open(READY, encoding="utf-8")) if os.path.exists(READY) else {}
    ready[cfg["titleId"]] = {"status": status, "folder": name}
    with open(READY, "w", encoding="utf-8") as f:
        json.dump(ready, f, indent=1)
    print(run(["console/xlibrary.py", host(), "--no-art"]).strip().splitlines()[-1])
    print("%s is on the Xbox (%.0f s)" % (name, time.time() - start))


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        raise SystemExit("usage: xbox_add_game.py <game folder> [--convert] [--pull] [--status=Plays|Starts|Testing]")
    status = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--status=")), "Testing")
    add(args[0], status, "--convert" in sys.argv, "--pull" in sys.argv)
