"""What a test game really showed (Blu 11-15: PR #9508 drew Octopath Traveler II's title upside down at 4 FPS and the
pipeline called it "runs"). The window shots can come back black when the game sits inside another window, so the
test asks AnyPS5 itself to save frames (APS5_DUMP_FRAMES, frame_NNN.bmp in the game folder). This script turns them
into small PNGs in test-shots, deletes the BMPs, says whether every frame was one flat colour, and with --look asks
Claude (the bridge's Haiku "look", 80 a day) whether the newest real picture looks broken: upside down, mirrored, garbled.
usage: frame_check.py <gameDir> <shotDir> <prefix> [--look]   prints one JSON line
"""
import glob
import json
import os
import sys

from PIL import Image, ImageStat

FLAT_STDDEV = 6.0
SAME_DIFF = 2.0
HERE = os.path.dirname(os.path.abspath(__file__))


def colour_name(mean):
    return "black" if mean < 24 else "white" if mean > 230 else "one flat grey"


def look(paths, title):
    sys.path.insert(0, HERE)
    import claude_bridge
    files = "\n".join(paths)
    q = ("Use the Read tool to look at these frames from the PS5 game '%s' running in AnyPS5 (a compatibility layer):\n%s\n"
         "Judge only whether the picture is drawn correctly. Broken means: upside down or mirrored (look at any text and logos), "
         "garbled or torn geometry, or wildly wrong colours. Loading screens, logos, black or dark frames, fades and menus are fine. "
         "Answer with exactly one line: OK, or BROKEN: <what is wrong, under 15 words>." % (title, files))
    answer = claude_bridge.run("look", q).strip()
    if claude_bridge.unavailable(answer):
        return {"look": "unavailable", "answer": answer[:200]}
    line = next((l.strip() for l in answer.splitlines() if l.strip().upper().startswith(("OK", "BROKEN"))), answer.strip().splitlines()[0] if answer.strip() else "")
    if line.upper().startswith("BROKEN"):
        return {"look": "broken", "answer": line.split(":", 1)[1].strip() if ":" in line else line}
    if line.upper().startswith("OK"):
        return {"look": "ok", "answer": line}
    return {"look": "unclear", "answer": line[:200]}


def main():
    game_dir, shot_dir, prefix = sys.argv[1], sys.argv[2], sys.argv[3]
    os.makedirs(shot_dir, exist_ok=True)
    rows = []
    for bmp in sorted(glob.glob(os.path.join(game_dir, "frame_*.bmp"))):
        try:
            im = Image.open(bmp).convert("RGB")
            gray = im.convert("L")
            w, h = gray.size
            st = ImageStat.Stat(gray.crop((w // 12, h // 12, w - w // 12, h - h // 12)))
            png = os.path.join(shot_dir, "%s-f%s.png" % (prefix, os.path.basename(bmp)[6:9]))
            im.thumbnail((960, 960))
            im.save(png, "PNG")
            rows.append({"png": png, "mean": round(st.mean[0], 1), "flat": st.stddev[0] < FLAT_STDDEV, "tiny": list(gray.resize((64, 36)).tobytes())})
        except Exception as ex:
            rows.append({"png": "", "error": "%s: %s" % (os.path.basename(bmp), ex)})
        try:
            os.remove(bmp)
        except OSError:
            pass
    good = [r for r in rows if r.get("png")]
    flat = [r for r in good if r["flat"]]
    out = {"frames": len(good), "flat": len(flat), "all_flat": bool(good) and len(flat) == len(good)}
    if flat:
        out["colours"] = sorted({colour_name(r["mean"]) for r in flat})
    same = 1
    for r in reversed(good[:-1]):
        a, b = r["tiny"], good[-1]["tiny"]
        if sum(abs(x - y) for x, y in zip(a, b)) / len(a) > SAME_DIFF:
            break
        same += 1
    if good:
        out["same_last"] = same
    real = [r["png"] for r in good if not r["flat"]]
    out["newest"] = real[-1] if real else (good[-1]["png"] if good else "")
    if "--look" in sys.argv and real:
        title = os.path.basename(game_dir.rstrip("\\/"))
        picks = [real[len(real) // 2], real[-1]] if len(real) > 1 else real
        out.update(look(picks, title))
    print(json.dumps(out))


if __name__ == "__main__":
    main()
