"""Read-only extractor for a raw exFAT image (e.g. PPSA09232.exfat). The image is opened read-only.

usage: exfat_extract.py <image> <output dir> [--list]
"""
import os
import struct
import sys


def main():
    # Background I/O priority: a game loading or a build reading from the same USB drive goes first; the extraction
    # takes the rest (it once held D: so busy that Hoa sat for minutes at its first log line).
    if os.name == "nt":
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00100000)  # PROCESS_MODE_BACKGROUND_BEGIN
    image, out = sys.argv[1], sys.argv[2]
    listing = "--list" in sys.argv
    f = open(image, "rb")
    boot = f.read(512)
    if boot[3:11] != b"EXFAT   ":
        sys.exit("not an exFAT image")
    fat_offset, fat_length, heap_offset, cluster_count, root_cluster = struct.unpack_from("<IIIII", boot, 0x50)
    sector = 1 << boot[0x6C]
    cluster = sector << boot[0x6D]
    fat_start = fat_offset * sector
    heap = heap_offset * sector

    def fat_next(n):
        f.seek(fat_start + 4 * n)
        return struct.unpack("<I", f.read(4))[0]

    def clusters(first, length, contiguous):
        count = (length + cluster - 1) // cluster
        if contiguous:
            return list(range(first, first + count))
        chain = []
        n = first
        while 2 <= n < 0xFFFFFFF7 and (count == 0 or len(chain) < count):
            chain.append(n)
            n = fat_next(n)
        return chain

    def read_chain(chain, length):
        data = bytearray()
        for n in chain:
            f.seek(heap + (n - 2) * cluster)
            data += f.read(cluster)
        return bytes(data[:length])

    def entries(first, length, contiguous):
        if length == 0:  # root directory: follow the FAT to the end
            chain = clusters(first, 0, False)
            length = len(chain) * cluster
        else:
            chain = clusters(first, length, contiguous)
        raw = read_chain(chain, length)
        i = 0
        while i + 32 <= len(raw):
            kind = raw[i]
            if kind == 0x00:
                break
            if kind == 0x85:
                secondary = raw[i + 1]
                attrs = struct.unpack_from("<H", raw, i + 4)[0]
                stream = raw[i + 32:i + 64]
                flags, name_len = stream[1], stream[3]
                valid, = struct.unpack_from("<Q", stream, 8)
                first_cluster, size = struct.unpack_from("<IQ", stream, 20)
                name = b""
                for k in range(2, secondary + 1):
                    part = raw[i + 32 * k:i + 32 * k + 32]
                    if part[0] == 0xC1:
                        name += part[2:32]
                name = name.decode("utf-16-le")[:name_len]
                yield name, bool(attrs & 0x10), first_cluster, valid, size, bool(flags & 2)
                i += 32 * (secondary + 1)
            else:
                i += 32

    files = 0
    total = 0

    def walk(first, length, contiguous, path):
        nonlocal files, total
        for name, is_dir, first_cluster, valid, size, contig in entries(first, length, contiguous):
            if name in ("", ".", "..") or "/" in name or "\\" in name:
                continue
            target = os.path.join(path, name)
            if is_dir:
                if not listing:
                    os.makedirs(target, exist_ok=True)
                walk(first_cluster, size, contig, target)
                continue
            files += 1
            total += size
            if not listing:
                print(f"extracting {os.path.relpath(target, out)} ({size / 2**20:,.1f} MB)", flush=True)
            if listing:
                if "--check" in sys.argv:  # first bytes of the program files only: is the code decrypted?
                    if (name == "eboot.bin" or name.lower().endswith((".prx", ".sprx", ".elf"))) and first_cluster:
                        f.seek(heap + (first_cluster - 2) * cluster)
                        print(f"{f.read(4).hex()}  {os.path.relpath(target, out)}")
                else:
                    print(f"{size:14,}  {os.path.relpath(target, out)}")
                continue
            with open(target, "wb") as o:
                remaining = valid
                if valid and first_cluster:
                    for n in clusters(first_cluster, valid, contig):
                        f.seek(heap + (n - 2) * cluster)
                        chunk = f.read(min(cluster, remaining))
                        o.write(chunk)
                        remaining -= len(chunk)
                        if remaining <= 0:
                            break
                if size > valid:
                    o.truncate(size)

    if not listing:
        os.makedirs(out, exist_ok=True)
    walk(root_cluster, 0, False, out)
    print(f"{files} files, {total:,} bytes")


main()
