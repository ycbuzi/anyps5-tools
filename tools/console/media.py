"""Media Gallery data for the AnyPS5 screen (Blu 11-15: "do the media gallery the same way").
Reads every game's captures: <game>\\screenshots (the control center's Screenshot, and "Trophy NN-ID ..." captures
AnyPS5.exe takes when a trophy is earned) and <game>\\anyps5-screenshots (AnyPS5's own in-game screenshot key). The
screen only reads files inside _tools\\console, so each capture gets a 512 px preview and a full picture (at most
1920 px) in cache\\media\\, and cache\\media.js lists them. Only new or changed captures are converted; removed ones
are dropped. Scheduled task "AnyPS5 media" runs it every minute; AnyPS5.exe also runs it after a capture.
usage: media.py [-q]
"""
import hashlib
import json
import os
import re
import sys
import time

GAMES = r"D:\AnyPS5 Games"
CONSOLE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(CONSOLE, "cache", "media")
JS = os.path.join(CONSOLE, "cache", "media.js")
MANIFEST = os.path.join(OUT, "manifest.json")
TROPHIES = os.path.join(CONSOLE, "cache", "trophies", "manifest.json")
FOLDERS = {"screenshots": "AnyPS5", "anyps5-screenshots": "In game"}
PICS = (".png", ".jpg", ".jpeg", ".bmp", ".webp")


def convert(src, mid):
    from PIL import Image
    im = Image.open(src).convert("RGB")
    full = im.copy()
    full.thumbnail((1920, 1920))
    full.save(os.path.join(OUT, mid + ".jpg"), "JPEG", quality=90)
    im.thumbnail((512, 512))
    im.save(os.path.join(OUT, mid + "_t.webp"), "WEBP", quality=80)
    return im.size


def trophy_names():
    try:
        m = json.load(open(TROPHIES, encoding="utf-8"))
    except Exception:
        return {}
    return {k: {t["id"]: (t.get("name", ""), t.get("grade", "bronze")) for t in v["pkg"]["trophies"]} for k, v in m.items()}


def main():
    os.makedirs(OUT, exist_ok=True)
    try:
        manifest = json.load(open(MANIFEST, encoding="utf-8"))
    except Exception:
        manifest = {}
    names = trophy_names()
    items, seen = [], set()
    for game in sorted(os.listdir(GAMES)):
        gdir = os.path.join(GAMES, game)
        m = re.search(r"\(([A-Z]{4}\d{5})\)$", game)
        if not m or not os.path.isdir(gdir):
            continue
        gid, title = m.group(1), re.sub(r"\s*\([A-Z]{4}\d{5}\)$", "", game)
        for sub, source in FOLDERS.items():
            folder = os.path.join(gdir, sub)
            if not os.path.isdir(folder):
                continue
            for fn in os.listdir(folder):
                if not fn.lower().endswith(PICS):
                    continue
                path = os.path.join(folder, fn)
                st = os.stat(path)
                mid = hashlib.sha1(path.lower().encode("utf-8")).hexdigest()[:14]
                seen.add(mid)
                stamp = [int(st.st_mtime), st.st_size]
                e = manifest.get(mid)
                if not e or e.get("stamp") != stamp or not os.path.exists(os.path.join(OUT, mid + ".jpg")):
                    try:
                        w, h = convert(path, mid)
                        e = {"stamp": stamp, "size": [w, h]}
                        manifest[mid] = e
                    except Exception as ex:
                        print("skipped %s: %s" % (path, ex), file=sys.stderr)
                        continue
                item = {"id": mid, "game": title, "gameId": gid, "src": "cache/media/%s.jpg" % mid, "thumb": "cache/media/%s_t.webp" % mid,
                        "at": int(st.st_mtime * 1000), "source": source, "file": fn}
                t = re.match(r"Trophy (\d+)-(\d+) ", fn)
                if t:
                    name, grade = names.get("%s_%02d" % (gid, int(t.group(1))), {}).get(int(t.group(2)), ("", "bronze"))
                    item["source"] = "Trophy"
                    item["trophy"] = {"name": name, "grade": grade}
                items.append(item)
    for mid in [k for k in manifest if k not in seen]:
        for suffix in (".jpg", "_t.webp"):
            try:
                os.remove(os.path.join(OUT, mid + suffix))
            except OSError:
                pass
        del manifest[mid]
    json.dump(manifest, open(MANIFEST, "w", encoding="utf-8"))
    items.sort(key=lambda x: -x["at"])
    text = "window.MEDIA = " + json.dumps({"items": items}, ensure_ascii=False, separators=(",", ":")) + ";\n"
    old = open(JS, encoding="utf-8").read() if os.path.exists(JS) else ""
    if old != text:
        tmp = JS + ".tmp"
        open(tmp, "w", encoding="utf-8").write(text)
        os.replace(tmp, JS)
    return len(items)


if __name__ == "__main__":
    n = main()
    if "-q" not in sys.argv:
        print("%d captures -> %s" % (n, JS))
