"""Builds the AnyPS5 console launcher's game library from the built games in D:\\AnyPS5 Games.
For each game folder with a game.json it copies the game's own sce_sys art (icon0, pic0/pic1) as small JPEGs into
art\\<titleId>\\, reads the pipeline's result for it from _tools\\auto-state.json, and writes library.js
(window.LIBRARY = [...]) next to index.html. Art is only re-made when the source is newer.
usage: python -I build_library.py"""
import json
import os
import re
from datetime import datetime
from pathlib import Path

from PIL import Image

GAMES = Path(r"D:\AnyPS5 Games")
HERE = Path(__file__).resolve().parent
ART = HERE / "art"
STATE = Path(r"C:\AnyPS5 Games\_tools\auto-state.json")


def shrink(src, dst, width, quality):
    if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
        return True
    try:
        with Image.open(src) as im:
            im = im.convert("RGB")
            if im.width > width:
                im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
            dst.parent.mkdir(parents=True, exist_ok=True)
            im.save(dst, "JPEG", quality=quality, optimize=True)
        return True
    except OSError:
        return False


def pipeline_results():
    try:
        state = json.loads(STATE.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    out = {}
    for entry in state.values():
        if isinstance(entry, dict) and entry.get("gameDir"):
            out[str(Path(entry["gameDir"])).lower()] = entry
    return out


def status_of(entry):
    if not entry:
        return "Not tested yet", None
    note = str(entry.get("note", ""))
    fps = re.search(r"FPS: ([0-9.]+)", note)
    fps = round(float(fps.group(1))) if fps else None
    if note.startswith("runs"):
        return "Plays", fps
    if str(entry.get("plays", "")).strip():
        return "Plays", fps
    failure = str(entry.get("failure", ""))
    if failure and failure not in ("", "None", "{}"):
        return "Stops early", fps
    return "In testing", fps


def main():
    results = pipeline_results()
    games = []
    for folder in sorted(GAMES.iterdir(), key=lambda p: p.name.lower()):
        meta = folder / "game.json"
        if not folder.is_dir() or folder.name.startswith("_") or not meta.exists():
            continue
        try:
            info = json.loads(meta.read_text(encoding="utf-8-sig"))
        except ValueError:
            continue
        title_id = info.get("titleId") or folder.name
        title = info.get("title") or folder.name
        sce = folder / "app0" / "sce_sys"
        icon = bg = ""
        if (sce / "icon0.png").exists() and shrink(sce / "icon0.png", ART / title_id / "icon.jpg", 384, 85):
            icon = f"art/{title_id}/icon.jpg"
        for name in ("pic0.png", "pic1.png"):
            if (sce / name).exists() and shrink(sce / name, ART / title_id / "bg.jpg", 1600, 80):
                bg = f"art/{title_id}/bg.jpg"
                break
        shot = ""
        shots = []
        for sub in ("test-shots", "screenshots"):
            if (folder / sub).is_dir():
                shots += list((folder / sub).glob("*.png"))
        shots.sort(key=lambda p: p.stat().st_mtime)
        if shots and shrink(shots[-1], ART / title_id / "shot.jpg", 960, 80):
            shot = f"art/{title_id}/shot.jpg"
        status, fps = status_of(results.get(str(folder).lower()))
        if isinstance(info.get("status"), str) and info["status"] and not results.get(str(folder).lower()):
            status = info["status"]
        log = folder / "anyps5.log"
        last = datetime.fromtimestamp(log.stat().st_mtime).strftime("%Y-%m-%d %H:%M") if log.exists() else ""
        exe = next((str(p) for p in folder.glob("*.exe") if not p.name.lower().startswith("anyps5")), "")
        saves = 0
        if (folder / "_sd").is_dir():
            for _, _, files in os.walk(folder / "_sd"):
                saves += len(files)
                if saves > 9999:
                    break
        games.append({"id": title_id, "title": title, "icon": icon, "bg": bg, "shot": shot, "status": status, "fps": fps,
                      "lastRun": last, "folder": str(folder), "exe": exe, "saves": saves})
    (HERE / "library.js").write_text("window.LIBRARY = " + json.dumps(games, indent=1) + ";\n", encoding="utf-8")
    print(f"{len(games)} games, {sum(1 for g in games if g['icon'])} with art -> {HERE / 'library.js'}")


if __name__ == "__main__":
    main()
