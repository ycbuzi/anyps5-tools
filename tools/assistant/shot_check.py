import glob
import json
import os
import sys

from PIL import Image, ImageStat

TITLE_BAR = 32
FLAT_STDDEV = 6.0


def measure(path):
    im = Image.open(path).convert("L")
    w, h = im.size
    if h > TITLE_BAR * 3:
        im = im.crop((0, TITLE_BAR, w, h))
    w, h = im.size
    box = im.crop((w // 12, h // 12, w - w // 12, h - h // 12)) if w > 48 and h > 48 else im
    st = ImageStat.Stat(box)
    small = box.resize((64, 36))
    top = ImageStat.Stat(small.crop((0, 0, 64, 18))).mean[0]
    bottom = ImageStat.Stat(small.crop((0, 18, 64, 36))).mean[0]
    return {"shot": os.path.basename(path), "mean": round(st.mean[0], 1), "stddev": round(st.stddev[0], 1), "flat": st.stddev[0] < FLAT_STDDEV, "top": round(top, 1), "bottom": round(bottom, 1)}


def main():
    folder, prefix = sys.argv[1], sys.argv[2]
    shots = sorted(glob.glob(os.path.join(folder, prefix + "-t*.png"))) + sorted(glob.glob(os.path.join(folder, prefix + "-zend.png")))
    rows = []
    for s in shots:
        try:
            rows.append(measure(s))
        except Exception as ex:
            rows.append({"shot": os.path.basename(s), "error": str(ex)})
    good = [r for r in rows if "error" not in r]
    flat = [r for r in good if r["flat"]]
    out = {"shots": len(good), "flat": len(flat), "all_flat": bool(good) and len(flat) == len(good), "last_flat": bool(good) and good[-1]["flat"], "rows": rows}
    if flat:
        m = sum(r["mean"] for r in flat) / len(flat)
        out["flat_colour"] = "black" if m < 24 else "white" if m > 230 else "one grey level (%d)" % m
    print(json.dumps(out))


if __name__ == "__main__":
    main()
