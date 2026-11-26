import bisect
import collections
import os
import re
import struct
import subprocess
import sys

O = sys.argv[1]
HZ = os.sysconf("SC_CLK_TCK")


def elf_info(path):
    try:
        with open(path, "rb") as f:
            h = f.read(20)
        if h[:4] != b"\x7fELF":
            return None
        return struct.unpack_from("<H", h, 16)[0], struct.unpack_from("<H", h, 18)[0]
    except OSError:
        return None


def nm_symbols(path):
    out = []
    for args in (["nm", "-n", "--defined-only"], ["nm", "-n", "-D", "--defined-only"]):
        try:
            r = subprocess.run(args + [path], capture_output=True, text=True, timeout=300)
        except Exception:
            continue
        for line in r.stdout.splitlines():
            p = line.split(None, 2)
            if len(p) == 3 and p[1] in "TtWwiI":
                try:
                    out.append((int(p[0], 16), p[2]))
                except ValueError:
                    pass
        if out:
            break
    out.sort()
    return out


hdr = open(f"{O}/samples").readline().split()
pid = int(hdr[2])
samples = []
for line in open(f"{O}/samples"):
    if line.startswith("#"):
        continue
    f = line.split()
    samples.append((int(f[0]), int(f[1], 16), int(f[2], 16) if len(f) > 2 else 0))
threads = {}
if os.path.exists(f"{O}/threads"):
    for line in open(f"{O}/threads"):
        p = line.rstrip("\n").split(" ", 1)
        threads[int(p[0])] = p[1] if len(p) > 1 else ""

maps = []
for line in open(f"{O}/maps"):
    p = line.split(None, 5)
    a, b = (int(x, 16) for x in p[0].split("-"))
    path = p[5].strip() if len(p) > 5 else ""
    maps.append((a, b, p[1], int(p[2], 16), path))
maps.sort()
map_starts = [m[0] for m in maps]


def find_map(pc):
    i = bisect.bisect_right(map_starts, pc) - 1
    if i >= 0 and maps[i][0] <= pc < maps[i][1]:
        return maps[i]
    return None


pm_starts, pm_ends, pm_names = [], [], []
pmpath = f"/tmp/perf-{pid}.map"
blocks = []
if os.path.exists(pmpath):
    cur = None
    with open(pmpath, errors="replace") as f:
        for line in f:
            p = line.split(None, 2)
            if len(p) < 3:
                continue
            try:
                a = int(p[0], 16)
                s = int(p[1])
            except ValueError:
                continue
            name = p[2].split(":", 1)[0]
            if cur and cur[2] == name and a == cur[1]:
                cur[1] = a + s
            else:
                if cur:
                    blocks.append(tuple(cur))
                cur = [a, a + s, name]
    if cur:
        blocks.append(tuple(cur))
blocks.sort()
pm_starts = [b[0] for b in blocks]


def find_jit(pc):
    i = bisect.bisect_right(pm_starts, pc) - 1
    while i >= 0 and i > len(pm_starts) - 1:
        i -= 1
    j = i
    while j >= 0 and j > i - 8:
        if blocks[j][0] <= pc < blocks[j][1]:
            return blocks[j][2]
        j -= 1
    return None


x86_mods = {}
native_syms = {}
for a, b, perm, off, path in maps:
    if not path.startswith("/") or path in x86_mods or path in native_syms:
        continue
    info = elf_info(path)
    if not info:
        continue
    etype, mach = info
    if mach == 0x3E:
        x86_mods[path] = None
    elif mach == 0xB7 and "x" in perm:
        native_syms[path] = None

name_to_mod = {}
for path in x86_mods:
    for _, n in nm_symbols(path):
        name_to_mod.setdefault(n, os.path.basename(path))

x86_ranges = sorted((a, b, os.path.basename(p)) for a, b, perm, off, p in maps if p in x86_mods)


def x86_module_of_addr(addr):
    for a, b, n in x86_ranges:
        if a <= addr < b:
            return n
    return None


sym_cache = {}


def native_sym(m, pc):
    a, b, perm, off, path = m
    if path not in sym_cache:
        sym_cache[path] = nm_symbols(path)
        info = elf_info(path)
        sym_cache[path + "#type"] = info[0] if info else 3
    syms = sym_cache[path]
    base = a - off
    vaddr = pc if sym_cache[path + "#type"] == 2 else pc - base
    if not syms:
        return "?"
    i = bisect.bisect_right(syms, (vaddr, "￿")) - 1
    return syms[i][1] if i >= 0 else "?"


def module_group(mod):
    if mod is None:
        return "JIT ?"
    if mod.endswith(".prx"):
        return "JIT AnyPS5 runtime libs (x86 under box64)"
    if mod.startswith("libstdc++") or mod.startswith("libgcc"):
        return "JIT x86 libstdc++/libgcc (emulated)"
    if "shim" in mod:
        return "JIT aps5 shim (x86)"
    return "JIT game code (relinked eboot)"


cats = collections.Counter()
detail = collections.defaultdict(collections.Counter)
per_thread = collections.defaultdict(collections.Counter)
for tid, pc, x64 in samples:
    m = find_map(pc)
    xm = find_map(x64) if x64 else None
    if xm and xm[4] in x86_mods:
        mod = os.path.basename(xm[4])
        top = module_group(mod)
        key = f"{mod}:{native_sym(xm, x64)}"
    elif m and m[4].startswith("/") and m[4] in native_syms:
        base = os.path.basename(m[4])
        sym = native_sym(m, pc)
        if "box64" in base:
            top = "box64 itself (dynarec compiler, wrappers, helpers)"
            key = sym
        else:
            top = "native libs (aarch64)"
            key = f"{base}:{sym}"
    else:
        name = find_jit(pc)
        if name is None:
            top = "JIT/anon code not in perf map"
            key = (m[4] or "[anon]") if m else "[unmapped]"
        else:
            pref = next((p for p in x86_mods if name.startswith(p)), None)
            if pref:
                mod = os.path.basename(pref)
                name = name[len(pref):].lstrip("/").split(" + ")[0] or "?"
            elif name.startswith("0x"):
                try:
                    mod = x86_module_of_addr(int(name, 16))
                except ValueError:
                    mod = None
            else:
                mod = name_to_mod.get(name.split("+")[0].strip())
            top = module_group(mod)
            key = f"{mod}:{name}" if mod else name
    cats[top] += 1
    detail[top][key] += 1
    per_thread[tid][top] += 1

N = max(len(samples), 1)
print(f"samples={len(samples)} pid={pid} perfmap_blocks={len(blocks)} x86_modules={len(x86_mods)}")
print("\n== CPU samples by layer (share of all game-process CPU samples)")
for c, n in cats.most_common():
    print(f"{100 * n / N:6.1f}%  {c}")
for c, _ in cats.most_common():
    print(f"\n-- top in: {c}")
    for k, n in detail[c].most_common(15):
        print(f"{100 * n / N:6.2f}%  {k[:150]}")
print("\n== per thread (top 12 by samples)")
for tid, cnt in sorted(per_thread.items(), key=lambda x: -sum(x[1].values()))[:12]:
    tot = sum(cnt.values())
    parts = ", ".join(f"{c.split(' (')[0]} {100 * n / tot:.0f}%" for c, n in cnt.most_common(4))
    print(f"{tid:>7} {threads.get(tid, ''):<16} {100 * tot / N:5.1f}% of samples | {parts}")


def load_snap(p):
    d = {"task": {}, "proc": {}}
    for line in open(p):
        f = line.split()
        if not f:
            continue
        if f[0] == "time":
            d["time"] = float(f[1])
        elif f[0] in ("tracer", "xserver") and len(f) >= 4:
            d[f[0]] = (int(f[2]), int(f[3]))
        elif f[0] == "game":
            d["game"] = tuple(int(x) for x in f[2:6])
        elif f[0] == "task" and len(f) >= 4:
            d["task"][f[1]] = (int(f[2]), int(f[3]), " ".join(f[4:]))
        elif f[0] == "proc" and len(f) >= 3:
            d["proc"][f[1]] = (int(f[2]), " ".join(f[3:]))
        elif f[0].startswith("voluntary"):
            nums = re.findall(r"\d+", line)
            d["ctxt"] = (int(nums[0]), int(nums[1])) if len(nums) >= 2 else (0, 0)
    return d


if os.path.exists(f"{O}/snap1"):
    s0, s1 = load_snap(f"{O}/snap0"), load_snap(f"{O}/snap1")
    dt = s1["time"] - s0["time"]
    pct = lambda ticks: 100 * ticks / HZ / dt
    gu = sum(v[0] for v in s1["task"].values()) - sum(v[0] for k, v in s0["task"].items() if k in s1["task"])
    gs = sum(v[1] for v in s1["task"].values()) - sum(v[1] for k, v in s0["task"].items() if k in s1["task"])
    print(f"\n== /proc accounting over {dt:.0f} s (100% = one core)")
    print(f"game threads: user {pct(gu):.0f}%  kernel {pct(gs):.0f}%  (kernel share {100 * gs / max(gu + gs, 1):.0f}%)")
    if "game" in s0 and "game" in s1:
        print(f"game minor faults/s {(s1['game'][0] - s0['game'][0]) / dt:.0f}  major faults/s {(s1['game'][1] - s0['game'][1]) / dt:.1f}")
    if "ctxt" in s0 and "ctxt" in s1:
        print(f"main thread ctxt switches/s vol {(s1['ctxt'][0] - s0['ctxt'][0]) / dt:.0f} nonvol {(s1['ctxt'][1] - s0['ctxt'][1]) / dt:.0f}")
    for k in ("tracer", "xserver"):
        if k in s0 and k in s1:
            u = s1[k][0] - s0[k][0]
            s = s1[k][1] - s0[k][1]
            label = "proot tracer (ptrace syscall handling)" if k == "tracer" else "termux-x11 X server"
            print(f"{label}: user {pct(u):.1f}%  kernel {pct(s):.1f}%")
    rows = []
    for k, v in s1["task"].items():
        if k in s0["task"]:
            rows.append((v[0] - s0["task"][k][0], v[1] - s0["task"][k][1], k, v[2]))
    rows.sort(key=lambda r: -(r[0] + r[1]))
    print("top game threads by CPU:")
    for u, s, k, n in rows[:12]:
        print(f"  {k:>7} {n:<16} user {pct(u):5.1f}%  kernel {pct(s):5.1f}%")
    procs = []
    for k, v in s1["proc"].items():
        if k in s0["proc"]:
            procs.append((v[0] - s0["proc"][k][0], k, v[1]))
    procs.sort(reverse=True)
    tot = sum(p[0] for p in procs)
    print(f"all visible processes: {pct(tot):.0f}% ; top:")
    for t, k, n in procs[:8]:
        print(f"  {k:>7} {n:<16} {pct(t):5.1f}%")
if os.path.exists(f"{O}/fps.samples"):
    fps, gpu, fr = [], [], []
    for line in open(f"{O}/fps.samples"):
        f = dict(x.split("=") for x in line.split()[1:])
        try:
            fps.append(float(f["fps"]))
        except (ValueError, KeyError):
            pass
        try:
            gpu.append(int(f["gpu"]))
            fr.append(int(f["freq"]))
        except (ValueError, KeyError):
            pass
    if fps:
        print(f"\nFPS mean {sum(fps) / len(fps):.1f} (min {min(fps):.1f}, max {max(fps):.1f})", end="")
    if gpu:
        print(f" | KGSL busy {sum(gpu) / len(gpu):.0f}% at {sum(fr) / len(fr) / 1e6:.0f} MHz", end="")
    print()
