import ctypes
import ctypes.wintypes as wt
import datetime
import os
import subprocess
import sys
import time

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
user32.SetProcessDPIAware()


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


class CURSORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("flags", wt.DWORD), ("hCursor", wt.HANDLE), ("ptScreenPos", wt.POINT)]


def record(x, y, w, h, fps, seconds, out_win):
    out_wsl = "/mnt/" + out_win[0].lower() + out_win[2:].replace("\\", "/")
    ff = subprocess.Popen(["wsl.exe", "-d", "archlinux", "-u", "root", "--", "ffmpeg", "-loglevel", "error", "-y",
                           "-f", "rawvideo", "-pix_fmt", "bgra", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-",
                           "-vf", "vflip,scale=1920:-2:flags=lanczos,format=yuv420p", "-c:v", "libx264", "-preset", "veryfast",
                           "-crf", "20", "-movflags", "+faststart", out_wsl],
                          stdin=subprocess.PIPE, creationflags=0x08000000)
    screen = user32.GetDC(0)
    mem = gdi32.CreateCompatibleDC(screen)
    bmp = gdi32.CreateCompatibleBitmap(screen, w, h)
    gdi32.SelectObject(mem, bmp)
    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(bi)
    bi.biWidth, bi.biHeight, bi.biPlanes, bi.biBitCount = w, h, 1, 32
    buf = ctypes.create_string_buffer(w * h * 4)
    ci = CURSORINFO()
    ci.cbSize = ctypes.sizeof(ci)
    frame_t = 1.0 / fps
    start = time.perf_counter()
    n = 0
    while time.perf_counter() - start < seconds:
        gdi32.BitBlt(mem, 0, 0, w, h, screen, x, y, 0x00CC0020 | 0x40000000)
        if user32.GetCursorInfo(ctypes.byref(ci)) and ci.flags & 1:
            user32.DrawIcon(mem, ci.ptScreenPos.x - x, ci.ptScreenPos.y - y, ci.hCursor)
        gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)
        try:
            ff.stdin.write(buf.raw)
        except OSError:
            break
        n += 1
        wait = start + n * frame_t - time.perf_counter()
        if wait > 0:
            time.sleep(wait)
    ff.stdin.close()
    ff.wait()
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(0, screen)
    return n


def main():
    seconds = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    x, y, w, h = (int(v) for v in (sys.argv[2] if len(sys.argv) > 2 else "0,0,1440,1080").split(","))
    out_dir = os.path.join(os.path.expanduser("~"), "Videos")
    out = os.path.join(out_dir, "AnyPS5-progress-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + ".mp4")
    frames = record(x, y, w, h, 20, seconds, out)
    with open(os.path.join(out_dir, "AnyPS5-progress-last.txt"), "w", encoding="utf-8") as f:
        f.write(f"{out}\n{frames} frames\n")


if __name__ == "__main__":
    main()
