import io
import json
import os
import re
import struct
import sys
import time

GAMES = r"D:\AnyPS5 Games"
CONSOLE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(CONSOLE, "cache", "trophies")
JS = os.path.join(CONSOLE, "cache", "trophies.js")
MANIFEST = os.path.join(OUT, "manifest.json")
LOG = os.path.join(OUT, "trophies.log")
LEGACY = os.path.join(CONSOLE, "..", "live", "trophy-legacy-owner.txt")
VERSION = 2
sys.path.insert(0, CONSOLE)
GRADES = {"P": "platinum", "G": "gold", "S": "silver", "B": "bronze"}
TICK_UNIX = 62135596800000000


def log(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")


def read_ucp(data):
    if len(data) < 0x40 or struct.unpack(">I", data[:4])[0] != 0xB228C60A:
        raise ValueError("not a trophy package")
    count, toc = struct.unpack(">II", data[0x10:0x18])
    files = {}
    for i in range(min(count, 4096)):
        e = toc + 0x20 + i * 0x40
        name = data[e:e + 0x20].split(b"\0")[0].decode("utf-8", "replace")
        off, ln = struct.unpack(">QQ", data[e + 0x20:e + 0x30])
        files[name] = data[off:off + ln]
    return files


def small_icon(raw, size, path, png=None):
    from PIL import Image
    im = Image.open(io.BytesIO(raw)).convert("RGBA")
    im.thumbnail((size, size))
    im.save(path, "WEBP", quality=82)
    if png:
        im.thumbnail((96, 96))
        im.save(png, "PNG")


def parse_package(ucp_path, key):
    files = read_ucp(open(ucp_path, "rb").read())
    conf = json.loads(files["tropconf.json"])
    langs = [l for l in ("en-US", conf.get("defaultLanguage")) if l and ("tropmeta_%s.json" % l) in files]
    if not langs:
        langs = [n[9:-5] for n in files if n.startswith("tropmeta_")]
    meta = json.loads(files["tropmeta_%s.json" % langs[0]])["metadata"] if langs else {}
    texts = {int(t["id"]): t for t in meta.get("trophyMetadata", []) if str(t.get("id", "")).isdigit()}
    groups = {int(g["id"]): g.get("name", "") for g in meta.get("groupMetadata", []) if str(g.get("id", "")).lstrip("-").isdigit()}
    folder = os.path.join(OUT, key)
    os.makedirs(folder, exist_ok=True)
    icon = ""
    for n in files:
        if n.startswith("icon0") and n.endswith(".png"):
            small_icon(files[n], 256, os.path.join(folder, "icon0.webp"))
            icon = "cache/trophies/%s/icon0.webp" % key
            break
    trophies = []
    for t in conf.get("trophies", []):
        tid = int(t["id"])
        png = "trop%04d.png" % tid
        ticon = ""
        if png in files:
            small_icon(files[png], 160, os.path.join(folder, "t%04d.webp" % tid), os.path.join(folder, "t%04d.png" % tid))
            ticon = "cache/trophies/%s/t%04d.webp" % (key, tid)
        tx = texts.get(tid, {})
        gid = t.get("groupId")
        trophies.append({"id": tid, "grade": GRADES.get(str(t.get("grade", "B"))[:1].upper(), "bronze"), "hidden": bool(t.get("hidden")),
                         "group": int(gid) if str(gid).lstrip("-").isdigit() else -1, "name": tx.get("name", ""), "detail": tx.get("detail", ""), "icon": ticon})
    return {"set": meta.get("titleMetadata", {}).get("name", ""), "icon": icon, "groups": {str(k): v for k, v in groups.items()}, "trophies": trophies}


def earned_by(game_dir, label, legacy):
    """Trophies earned per AnyPS5 user (trophy_users.py): {uid: {trophy id: unix ms}}; "_tests" is the pipeline's runs."""
    live = os.path.join(game_dir, "_trophy")
    users = os.path.join(live, "users")
    marker = os.path.join(users, "live-owner.txt")
    owner = open(marker, encoding="utf-8").read().strip() if os.path.exists(marker) else ""
    out = {(owner or "_tests"): earned(live, label)}
    if os.path.isdir(users):
        for uid in os.listdir(users):
            if os.path.isdir(os.path.join(users, uid)) and uid != owner and (uid != "_tests" or owner):
                out[uid] = earned(os.path.join(users, uid), label)
    if legacy and not os.path.exists(os.path.join(users, "migrated.txt")):
        out.setdefault(legacy, out.get("_tests", {}))
    return out


def earned(tdir, label):
    got = {}
    if not os.path.isdir(tdir):
        return got
    for n in os.listdir(tdir):
        m = re.fullmatch(r"\d+_(\d+)\.txt", n)
        if not m or int(m.group(1)) != label:
            continue
        for line in open(os.path.join(tdir, n), encoding="utf-8", errors="replace"):
            p = line.split()
            if len(p) >= 2 and p[0].isdigit() and p[1].isdigit():
                ms = (int(p[1]) - TICK_UNIX) // 1000
                tid = int(p[0])
                got[tid] = min(got.get(tid, ms), ms)
    return got


def main():
    os.makedirs(OUT, exist_ok=True)
    try:
        manifest = json.load(open(MANIFEST, encoding="utf-8"))
    except Exception:
        manifest = {}
    try:
        import trophy_users
        fixed = trophy_users.do_fix()
        if fixed not in ("nothing to fix", "games still running"):
            log("trophy records put back: " + fixed)
    except Exception as ex:
        log("trophy_users fix failed: %s" % ex)
    legacy = open(LEGACY, encoding="utf-8").read().strip() if os.path.exists(LEGACY) else ""
    games, seen = [], set()
    for name in sorted(os.listdir(GAMES)):
        gdir = os.path.join(GAMES, name)
        tdir = os.path.join(gdir, "app0", "sce_sys", "trophy2")
        m = re.search(r"\(([A-Z]{4}\d{5})\)$", name)
        if not m or not os.path.isdir(tdir):
            continue
        tid = m.group(1)
        for fn in sorted(os.listdir(tdir)):
            lm = re.fullmatch(r"trophy(\d\d)\.ucp", fn)
            if not lm:
                continue
            label = int(lm.group(1))
            path = os.path.join(tdir, fn)
            key = "%s_%02d" % (tid, label)
            seen.add(key)
            st = os.stat(path)
            stamp = [int(st.st_mtime), st.st_size]
            entry = manifest.get(key)
            if not entry or entry.get("stamp") != stamp or entry.get("v") != VERSION:
                try:
                    entry = {"stamp": stamp, "v": VERSION, "pkg": parse_package(path, key)}
                    manifest[key] = entry
                    log("read %s (%d trophies)" % (key, len(entry["pkg"]["trophies"])))
                except Exception as ex:
                    log("skipped %s: %s" % (path, ex))
                    continue
            by = earned_by(gdir, label, legacy)
            pkg = entry["pkg"]
            games.append({"key": key, "id": tid, "label": label, "title": re.sub(r"\s*\([A-Z]{4}\d{5}\)$", "", name), "set": pkg["set"], "icon": pkg["icon"],
                          "groups": pkg["groups"], "trophies": [dict(t, e={u: g[t["id"]] for u, g in by.items() if t["id"] in g}) for t in pkg["trophies"]]})
    for key in [k for k in manifest if k not in seen]:
        del manifest[key]
    json.dump(manifest, open(MANIFEST, "w", encoding="utf-8"))
    text = "window.TROPHIES = " + json.dumps({"built": int(time.time() * 1000), "games": games}, ensure_ascii=False, separators=(",", ":")) + ";\n"
    old = open(JS, encoding="utf-8").read() if os.path.exists(JS) else ""
    if re.sub(r'"built":\d+', "", old) != re.sub(r'"built":\d+', "", text):
        tmp = JS + ".tmp"
        open(tmp, "w", encoding="utf-8").write(text)
        os.replace(tmp, JS)
    return len(games)


if __name__ == "__main__":
    n = main()
    if "-q" not in sys.argv:
        print("%d trophy sets -> %s" % (n, JS))
