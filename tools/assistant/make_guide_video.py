"""Makes a short slideshow video (animated GIF, plays inside Discord) that walks a new tester through running a PS5
game they own with AnyPS5 on Windows, using the wording of the official converter page
(https://boykopovar.github.io/AnyPS5/). Made 11-06 for Reflex-shrimp93, a new tester Blu is helping.

usage: python -I make_guide_video.py <out.gif> [--pc "line 1|line 2|..."]
"""
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

W, H = 1280, 720
BG, CARD, FG, DIM, ACCENT, WARN = (17, 19, 26), (30, 34, 46), (236, 238, 245), (160, 166, 184), (95, 170, 255), (255, 190, 90)
FONTS = Path(r"C:\Windows\Fonts")


def font(name, size):
    for n in (name, "segoeui.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(str(FONTS / n), size)
        except OSError:
            continue
    return ImageFont.load_default()


TITLE, BODY, SMALL, BIG = font("seguisb.ttf", 46), font("segoeui.ttf", 30), font("segoeui.ttf", 22), font("seguibl.ttf", 64)


def wrap(draw, text, f, width):
    out = []
    for para in text.split("\n"):
        line = ""
        for word in para.split(" "):
            test = (line + " " + word).strip()
            if draw.textlength(test, font=f) > width and line:
                out.append(line)
                line = word
            else:
                line = test
        out.append(line)
    return out


def slide(step, title, lines, note="", accent=ACCENT):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 8], fill=accent)
    if step:
        d.rounded_rectangle([64, 60, 64 + 120, 60 + 56], radius=14, fill=accent)
        d.text((64 + 60, 88), step, font=SMALL, fill=BG, anchor="mm")
    d.text((64 if not step else 210, 64), title, font=TITLE, fill=FG)
    d.rounded_rectangle([64, 150, W - 64, H - 110], radius=22, fill=CARD)
    y = 182
    for item in lines:
        bullet = not item.startswith("  ")
        text = item.strip()
        for i, row in enumerate(wrap(d, text, BODY, W - 64 * 2 - 110)):
            if i == 0 and bullet:
                d.ellipse([104, y + 14, 116, y + 26], fill=accent)
            d.text((136, y), row, font=BODY, fill=FG if bullet else DIM)
            y += 42
        y += 12
    if note:
        d.text((64, H - 86), note, font=SMALL, fill=WARN)
    d.text((W - 64, H - 40), "AnyPS5 beginner guide · Windows", font=SMALL, fill=DIM, anchor="rs")
    return img


def cover():
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 8], fill=ACCENT)
    d.text((W // 2, 250), "Your PS5 game, on your PC", font=BIG, fill=FG, anchor="mm")
    d.text((W // 2, 335), "A beginner's guide to AnyPS5 on Windows", font=TITLE, fill=ACCENT, anchor="mm")
    d.text((W // 2, 430), "AnyPS5 is in early development: most games don't play yet.", font=BODY, fill=DIM, anchor="mm")
    d.text((W // 2, 475), "Testing still helps: report how far your game gets.", font=BODY, fill=DIM, anchor="mm")
    return img


def main():
    out = Path(sys.argv[1])
    pc = None
    if "--pc" in sys.argv:
        pc = sys.argv[sys.argv.index("--pc") + 1].split("|")
    slides = [(cover(), 5000)]
    slides.append((slide("", "What you need", [
        "A CPU with AVX2 (and FMA, BMI1/2, F16C, MOVBE): the PS5's game code runs directly on it",
        "A graphics card with Vulkan and up-to-date drivers",
        "16 GB of RAM or more, plus a big Windows page file (about 64 GB) for large games",
        "Free space on an SSD: a game plus its converted copy",
        "A PS5 you own and a game you bought on it",
    ]), 9000))
    if pc:
        slides.append((slide("", "Your PC", pc, accent=WARN), 9000))
    slides.append((slide("STEP 1", "Dump your game", [
        "Copy (dump) the game from your own PS5",
        "  AnyPS5 never ships game files, firmware or keys, and doesn't provide dumping tools",
        "The converter needs the decrypted game folder: one executable ELF, sce_sys/ and sce_module/",
        "  Everything else in the game folder can stay as it is",
    ]), 9000))
    slides.append((slide("STEP 2", "Open the converter", [
        "Go to  boykopovar.github.io/AnyPS5",
        "  (or demo.anyps5.gg  →  Open the converter)",
        "Nothing to install: it converts in your browser and your files stay on your PC",
        "Use desktop Chrome or Edge for big games",
    ]), 8000))
    slides.append((slide("STEP 3", "Pick your game", [
        "Click  Choose folder  and pick the game folder",
        "  or zip the folder and use  Choose ZIP file(s)",
        "Under  Convert for, pick  Windows  .exe",
        "Under  Save the result, pick  Save ZIP to disk  (the in-memory download only fits small apps)",
        "Intel CPU? Tick  Convert AMD-specific instructions for Intel CPUs.  AMD CPU: leave it off",
    ]), 11000))
    slides.append((slide("STEP 4", "Convert and run", [
        "Click  Convert & save  and wait for the ZIP",
        "Unzip it to a folder on your SSD",
        "Double-click the game's .exe",
        "  The first start compiles the game's shaders and can take a long time. Later starts are faster",
    ], note="Converting doesn't guarantee a game runs. Check the Compatibility page for your game first."), 10000))
    slides.append((slide("", "If it stops or crashes", [
        "Look up your game in the Game bring-up discussions on the AnyPS5 GitHub",
        "Ask in the AnyPS5 Discord with your PC specs and the last lines of the game's log",
        "Big games: make sure the page file is large and the game is on the SSD",
        "Reports of how far your game gets help the developers fix it",
    ], accent=(120, 210, 140)), 10000))
    frames, durations = [s for s, _ in slides], [t for _, t in slides]
    frames = [f.convert("P", palette=Image.ADAPTIVE, colors=64) for f in frames]
    frames[0].save(out, save_all=True, append_images=frames[1:], duration=durations, loop=0, optimize=True)
    for i, (img, _) in enumerate(slides):
        img.save(out.with_name(f"{out.stem}-{i + 1:02d}.png"))
    print(f"{out} ({out.stat().st_size // 1024} KB, {len(frames)} slides, {sum(durations) // 1000} s)")


if __name__ == "__main__":
    main()
