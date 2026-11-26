"""Counts AVX-512 (EVEX) instruction prefixes in each game's executable code, to pick games that a translator without
AVX-512 (box64 on the Quest 2) can run. A rough count: the 0x62 EVEX prefix followed by its fixed-bit pattern in the
executable PT_LOAD segments of each _sources\\<game>\\eboot.elf. Real PS5 games (Zen 2) show none or a few false hits.

usage: python -I evex_scan.py
"""
import struct
from pathlib import Path

SOURCES = Path(r"D:\AnyPS5 Games\_sources")


def evex_count(path):
    data = path.read_bytes()
    if data[:4] != b"\x7fELF":
        return None
    phoff, = struct.unpack_from("<Q", data, 0x20)
    phentsize, phnum = struct.unpack_from("<HH", data, 0x36)
    total = size = 0
    for i in range(phnum):
        p_type, p_flags, p_offset, _, _, p_filesz, _, _ = struct.unpack_from("<IIQQQQQQ", data, phoff + i * phentsize)
        if p_type != 1 or not p_flags & 1:
            continue
        seg = data[p_offset:p_offset + p_filesz]
        size += len(seg)
        for j in range(len(seg) - 3):
            if seg[j] == 0x62 and (seg[j + 1] & 0x0C) == 0 and (seg[j + 2] & 0x04) == 0x04 and seg[j + 1] & 0x03:
                total += 1
    return total, size


def main():
    rows = []
    for d in sorted(SOURCES.iterdir()):
        elf = d / "eboot.elf"
        if elf.exists():
            r = evex_count(elf)
            if r:
                rows.append((r[0] * 1_000_000 / max(1, r[1]), r[0], r[1], d.name))
    for density, count, size, name in sorted(rows):
        print(f"{density:8.1f} per MB  {count:7d} hits  {size / 1e6:7.1f} MB  {name}")


if __name__ == "__main__":
    main()
