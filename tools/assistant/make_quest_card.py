"""Makes the progress card for the Discord showcase of the Meta Quest 2 experiment (Blu 11-06: "open a showcase in
Discord and start updating the progress on a quest"). Edit STEPS and rerun for each update.

usage: python -I make_quest_card.py <out.png>
"""
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

W, H = 1280, 720
BG, CARD, FG, DIM, OK, WIP, TODO = (16, 18, 24), (30, 34, 46), (236, 238, 245), (160, 166, 184), (110, 210, 140), (255, 190, 90), (120, 126, 140)
FONTS = Path(r"C:\Windows\Fonts")


def font(name, size):
    for n in (name, "segoeui.ttf"):
        try:
            return ImageFont.truetype(str(FONTS / n), size)
        except OSError:
            continue
    return ImageFont.load_default()


TITLE, SUB, BODY, SMALL = font("seguisb.ttf", 44), font("segoeui.ttf", 26), font("segoeui.ttf", 27), font("segoeui.ttf", 21)
STEPS = [
    ("done", "Termux + Debian on the Quest 2 (Snapdragon XR2, 8 cores, 6 GB)"),
    ("done", "AnyPS5 builds on ARM64 and passes 750 of 786 tests"),
    ("done", "Turnip Vulkan 1.3 on the Adreno 650 has every Vulkan feature AnyPS5 needs"),
    ("done", "Celeste Classic converted for Linux; box64 loads it and all of AnyPS5's libraries"),
    ("now", "AnyPS5's guest memory setup vs box64's /proc/self/maps"),
    ("todo", "First frame on the headset"),
]


def main():
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 8], fill=OK)
    d.text((64, 50), "AnyPS5 on a Meta Quest 2", font=TITLE, fill=FG)
    d.text((64, 110), "PS5 game code -> AnyPS5 Linux build -> box64 (x86-64 on ARM) -> Turnip (Vulkan on Adreno)", font=SUB, fill=DIM)
    d.rounded_rectangle([64, 170, W - 64, H - 90], radius=22, fill=CARD)
    y = 205
    for state, text in STEPS:
        colour = {"done": OK, "now": WIP, "todo": TODO}[state]
        mark = {"done": "done", "now": "now", "todo": "next"}[state]
        d.rounded_rectangle([100, y + 2, 190, y + 38], radius=10, fill=colour)
        d.text((145, y + 20), mark, font=SMALL, fill=BG, anchor="mm")
        d.text((215, y), text, font=BODY, fill=FG if state != "todo" else DIM)
        y += 70
    d.text((64, H - 60), "experiment, homebrew only, updated as it goes", font=SMALL, fill=DIM)
    out = Path(sys.argv[1])
    img.save(out)
    print(out)


if __name__ == "__main__":
    main()
