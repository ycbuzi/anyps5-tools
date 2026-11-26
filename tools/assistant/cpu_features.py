"""Counts the instructions in a game's code that need CPU extensions older hosts lack (AVX, AVX2, FMA, F16C, BMI1/2,
LZCNT, MOVBE), so a relinker pass that lowers them for older CPUs can be sized from real titles (Blu 11-06: get the
MacBook (AVX, no AVX2) and the Chromebook (no AVX) past AnyPS5's CPU check).

usage: python -I cpu_features.py <game folder or ELF file> [...] [--json out.json]

It reads the executable segments of eboot.bin and the game's own ELF modules (encrypted SELF files are skipped and
listed), disassembles them with MinGW objdump (linear sweep, so data inside code can add a little noise) and sorts
each instruction by the newest extension it needs. Counts are static: how many places the code uses an extension,
not how often they run.
"""
import hashlib
import json
import re
import struct
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

OBJDUMP = r"D:\AnyPS5-tools\mingw-extract\mingw64\bin\objdump.exe"
SKIP_DIRS = {"fakelib", "decrypted_old", "_sd", "_sd_mem", "shader_cache", "crash-logs", "test-shots", "mods", "libs", ".build"}

FMA = re.compile(r"^vf(n)?m(add|sub|addsub|subadd)\d")
F16C = {"vcvtph2ps", "vcvtps2ph"}
BMI1 = {"andn", "bextr", "blsi", "blsmsk", "blsr", "tzcnt"}
BMI2 = {"bzhi", "mulx", "pdep", "pext", "rorx", "sarx", "shlx", "shrx"}
AVX2_ALWAYS = {"vperm2i128", "vpermd", "vpermq", "vpermps", "vpermpd", "vinserti128", "vextracti128", "vpbroadcastb",
               "vpbroadcastw", "vpbroadcastd", "vpbroadcastq", "vbroadcasti128", "vpmaskmovd", "vpmaskmovq",
               "vpsllvd", "vpsllvq", "vpsrlvd", "vpsrlvq", "vpsravd", "vpblendd"}
FLOAT_ONLY_YMM = re.compile(r"^v(add|sub|mul|div|min|max|sqrt|rsqrt|rcp|and|andn|or|xor|cmp|blend|blendv|shuf|unpck|movap|movup|movnt|movdq|lddqu|movmskp|round|dp|hadd|hsub|addsub|cvt|broadcastss|broadcastsd|broadcastf128|insertf128|extractf128|perm2f128|permilp|maskmov|testp|ptest|zeroupper|zeroall|movsh|movsl|movddup)")


def classify(mnemonic, operands):
    m = mnemonic.split(".")[0]
    if m in ("movbe",):
        return "MOVBE"
    if m == "lzcnt":
        return "LZCNT"
    if m in BMI2:
        return "BMI2"
    if m in BMI1:
        return "BMI1"
    if FMA.match(m):
        return "FMA"
    if m in F16C:
        return "F16C"
    if m.startswith("vgather") or m.startswith("vpgather") or m in AVX2_ALWAYS:
        return "AVX2"
    if m.startswith("v"):
        if "%ymm" in operands and m.startswith("vp") and not FLOAT_ONLY_YMM.match(m):
            return "AVX2"
        return "AVX-256" if "%ymm" in operands else "AVX-128"
    return None


def code_segments(data):
    if data[:4] != b"\x7fELF" or data[4] != 2:
        return None
    phoff, = struct.unpack_from("<Q", data, 0x20)
    phentsize, phnum = struct.unpack_from("<HH", data, 0x36)
    out = []
    for i in range(phnum):
        p_type, p_flags, p_offset, p_vaddr, _, p_filesz, _, _ = struct.unpack_from("<IIQQQQQQ", data, phoff + i * phentsize)
        if p_type == 1 and p_flags & 1 and p_filesz:
            out.append(data[p_offset:p_offset + p_filesz])
    return out


def count_file(path):
    data = path.read_bytes()
    segments = code_segments(data)
    if segments is None:
        return None
    counts, total = Counter(), 0
    for seg in segments:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as tmp:
            tmp.write(seg)
        try:
            r = subprocess.run([OBJDUMP, "-D", "-b", "binary", "-m", "i386:x86-64", "--no-show-raw-insn", tmp.name],
                               capture_output=True, text=True, errors="replace")
        finally:
            Path(tmp.name).unlink(missing_ok=True)
        for line in r.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) < 2 or not parts[0].strip().endswith(":"):
                continue
            ins = parts[1].strip()
            if not ins or ins.startswith("(bad)"):
                continue
            mnemonic, _, operands = ins.partition(" ")
            total += 1
            feature = classify(mnemonic, operands)
            if feature:
                counts[feature] += 1
    return total, counts


def game_files(root):
    if root.is_file():
        return [root]
    files = []
    for p in root.rglob("*"):
        if not p.is_file() or any(part in SKIP_DIRS for part in p.relative_to(root).parts[:-1]):
            continue
        if p.name == "eboot.bin" or p.suffix.lower() in (".prx", ".sprx", ".elf"):
            files.append(p)
    return files


def main():
    args = sys.argv[1:]
    out_json = None
    if "--json" in args:
        i = args.index("--json")
        out_json = args[i + 1]
        del args[i:i + 2]
    report = {}
    for arg in args:
        root = Path(arg)
        source = root / "app0" if (root / "app0").is_dir() else root
        total, counts, files, skipped, seen = 0, Counter(), 0, [], set()
        for f in game_files(source):
            digest = hashlib.sha256(f.read_bytes()).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            r = count_file(f)
            if r is None:
                skipped.append(str(f.relative_to(source)) if f != source else f.name)
                continue
            files += 1
            total += r[0]
            counts.update(r[1])
        no_avx2 = sum(counts[k] for k in ("AVX2", "FMA", "F16C", "BMI1", "BMI2", "LZCNT", "MOVBE"))
        no_avx = no_avx2 + counts["AVX-128"] + counts["AVX-256"]
        report[root.name] = {"files": files, "skipped": len(skipped), "instructions": total, "counts": dict(counts),
                             "mac_avx_only_sites": no_avx2, "chromebook_no_avx_sites": no_avx}
        share = (100.0 * no_avx / total) if total else 0.0
        print(f"{root.name}: {files} ELF files ({len(skipped)} encrypted skipped), {total:,} instructions; "
              + ", ".join(f"{k} {v:,}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))
              + f" | to lower for the Mac: {no_avx2:,}; for the Chromebook: {no_avx:,} ({share:.2f}% of all), of which 256-bit: {counts['AVX-256'] + counts['AVX2']:,}")
    if out_json:
        Path(out_json).write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
