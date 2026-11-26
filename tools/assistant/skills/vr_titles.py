"""Lists which dumped games use the PS VR2 libraries (libSceHmd2, libSceVrTracker2, ...), by looking for the library
names in each dump's executables (eboot.bin and *.prx/*.sprx; decrypted ELF files carry their import library names
as plain strings). Writes _reports\\vr-titles.md. usage: python -I vr_titles.py [dump root ...]"""
import sys
from pathlib import Path

ROOTS = [Path(a) for a in sys.argv[1:]] or [Path(r"D:\AnyPS5 Games\_sources"), Path(r"G:\AnyPS5 Games\_sources")]
LIBS = [b"libSceHmd2", b"libSceVrTracker2", b"libSceVrSetupDialog", b"libSceHmdSetupDialog", b"libSceVrHand",
        b"libSceHeadTracker", b"libSceHandTracker", b"libSceFaceTracker", b"libSceHmd\x00", b"libSceVrTracker\x00"]
OUT = Path(r"C:\AnyPS5 Games\_reports\vr-titles.md")


def scan(path):
    found = set()
    try:
        with path.open("rb") as f:
            head = f.read(4)
            if head != b"\x7fELF":
                return found
            f.seek(0)
            data = f.read()
    except OSError:
        return found
    for lib in LIBS:
        if lib in data:
            found.add(lib.rstrip(b"\x00").decode())
    return found


def main():
    rows = []
    for root in ROOTS:
        if not root.is_dir():
            continue
        for game in sorted(p for p in root.iterdir() if p.is_dir()):
            libs = set()
            for exe in game.rglob("*"):
                if exe.is_file() and (exe.name == "eboot.bin" or exe.suffix.lower() in (".prx", ".sprx", ".elf")):
                    libs |= scan(exe)
            if libs:
                rows.append((game.name, sorted(libs)))
    lines = ["# Dumped games that use PS VR2 libraries", ""]
    lines += [f"- {name}: {', '.join(libs)}" for name, libs in rows] or ["- none found"]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
