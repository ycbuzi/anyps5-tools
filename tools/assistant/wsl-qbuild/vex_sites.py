"""List candidate sites of given VEX opcodes in an ELF's executable segments (raw byte scan, so a few false hits).

python vex_sites.py <elf> <map>:<opcode hex> [...]     e.g. vex_sites.py eboot.elf 2:13 3:1D 1:53
Prints per form: L, pp, W, vvvv, reg or mem, and the immediate for map 3.
"""
import collections
import struct
import sys


def segments(data):
    phoff, = struct.unpack_from("<Q", data, 0x20)
    phentsize, phnum = struct.unpack_from("<HH", data, 0x36)
    for i in range(phnum):
        ptype, flags, off, vaddr, paddr, filesz = struct.unpack_from("<IIQQQQ", data, phoff + i * phentsize)
        if ptype == 1 and flags & 1:
            yield off, filesz, vaddr


def modrm_len(data, i):
    modrm = data[i]
    mod, rm = modrm >> 6, modrm & 7
    if mod == 3:
        return 1
    n = 1
    if rm == 4:
        sib = data[i + 1]
        n += 1
        if mod == 0 and (sib & 7) == 5:
            n += 4
    if mod == 0 and rm == 5:
        n += 4
    elif mod == 1:
        n += 1
    elif mod == 2:
        n += 4
    return n


def main():
    data = open(sys.argv[1], "rb").read()
    wanted = {(int(a.split(":")[0]), int(a.split(":")[1], 16)) for a in sys.argv[2:]}
    seen = collections.Counter()
    first = {}
    for off, size, vaddr in segments(data):
        end = off + size - 12
        i = off
        while True:
            c4 = data.find(b"\xc4", i, end)
            c5 = data.find(b"\xc5", i, end)
            hits = [x for x in (c4, c5) if x >= 0]
            if not hits:
                break
            i = min(hits)
            if data[i] == 0xC4:
                b1, b2, op = data[i + 1], data[i + 2], data[i + 3]
                vmap, w, m = b1 & 0x1F, b2 >> 7, i + 4
            else:
                b2, op = data[i + 1], data[i + 2]
                vmap, w, m = 1, 0, i + 3
            if (vmap, op) in wanted:
                length, pp, vvvv = (b2 >> 2) & 1, b2 & 3, (~b2 >> 3) & 15
                n = modrm_len(data, m)
                mem = (data[m] >> 6) != 3
                imm = data[m + n] if vmap == 3 else None
                key = "map%d op%02X L%d pp%d W%d vvvv%d %s%s" % (vmap, op, length, pp, w, vvvv, "mem" if mem else "reg", "" if imm is None else " imm%02X" % imm)
                seen[key] += 1
                first.setdefault(key, vaddr + i - off)
            i += 1
    for key, count in sorted(seen.items(), key=lambda kv: -kv[1]):
        print("%6d  %s  first at 0x%x" % (count, key, first[key]))


main()
