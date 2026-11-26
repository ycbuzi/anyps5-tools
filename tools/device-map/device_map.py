import glob, json, os, re, subprocess, time

ROOT = r"D:\AnyPS5"
OUT = r"C:\AnyPS5 Games\_reports\device-map.md"
REC = os.path.join(ROOT, r"core\shader\recompiler")
AGC = os.path.join(ROOT, r"core\libs\prx\libSceAgcDriver")


def files(base, exts=(".cpp", ".h")):
    for d, _, names in os.walk(base):
        if os.sep + "tests" in d:
            continue
        for n in names:
            if n.endswith(exts):
                yield os.path.join(d, n)


def scan(base, pattern):
    rx = re.compile(pattern)
    hits = []
    for f in files(base):
        try:
            lines = open(f, encoding="utf-8", errors="replace").read().splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines, 1):
            if rx.search(line):
                hits.append((os.path.relpath(f, ROOT).replace("\\", "/"), i, line.strip()[:150]))
    return hits


def by_file(hits, limit=40):
    count = {}
    for f, _, _ in hits:
        count[f] = count.get(f, 0) + 1
    return sorted(count.items(), key=lambda kv: -kv[1])[:limit]


def section(out, title, hits, note, detail=False):
    out.append("### %s (%d sites)" % (title, len(hits)))
    out.append(note)
    if detail:
        for f, i, text in hits[:30]:
            out.append("- `%s:%d` %s" % (f, i, text.replace("`", "'")))
    else:
        for f, n in by_file(hits):
            out.append("- `%s` x%d" % (f, n))
    out.append("")


def last_lines(path, n):
    try:
        return open(path, encoding="utf-8", errors="replace").read().splitlines()[-n:]
    except OSError:
        return []


def main():
    head = subprocess.run(["git", "-C", ROOT, "log", "-1", "--format=%h %cd", "--date=short"], capture_output=True, text=True).stdout.strip()
    out = ["# Device map", "", "Made by device_map.py %s from %s (main checkout %s). Read this before working on a device; it lists the code that blocks each one, so nothing is found by accident." % (time.strftime("%Y-%m-%d %H:%M"), ROOT, head), ""]

    out += ["## Xbox One (D3D12 via fxc SM5.1: no Int64, no subgroup ops, no BDA, no 8/16-bit, no Float16/64)", ""]
    out += ["Chain: req -> agc_shader_replay --spv --xbox (WSL /root/cbint) -> pushfix.py -> dxbc.py (spirv-cross SM51, SM67+bary/intsample lowering) -> fxc -> psocheck (3 PC GPUs) -> probe pso.json on the console.",
            "Tools: C:/AnyPS5-work/xbox-probe/hlsl51 (xbox-chain.sh, retest-failed.sh, cache_spv.py, patch_*.py). Recompiler patches are local in WSL /root/cbint/src (backups /root/*.orig, *.pre*).", ""]
    section(out, "Hard capability checks that fail without Int64/BDA", scan(REC, r'supportedCapabilities.*(Int64|PhysicalStorage)|require[s]? shaderInt64|BDA requires'), "Each one is a replay failure under --xbox until it gets a 32-bit path.", True)
    section(out, "64-bit integer emitters (TypeU64 / TypeScalarU64)", scan(REC, r'TypeU64\(|TypeScalarU64\(|ConstantU64\('), "Every file here emits OpTypeInt 64 somewhere; fxc 5.1 rejects it. InterpolateInteger (SpirvAluEmitterFloat64.cpp) is the 8 Spider-Man shaders left on 11-18.")
    section(out, "Subgroup / wave emitters", scan(REC, r'GroupNonUniform|OpSubgroup|CapabilityGroupNonUniform'), "Removed by single-lane mode (subgroupSize=1); each needs a single-lane form.")
    section(out, "Float16 / 8-bit / 16-bit storage emitters", scan(REC, r'CapabilityFloat16|StorageBuffer8Bit|StorageBuffer16Bit|CapabilityInt16|CapabilityInt8'), "Must stay off under --xbox.")
    out += ["### Shader request dumps (needed to replay a game for Xbox)",
            "- `APS5_DUMP_SHADERS=1`: compute requests always (Dispatch.cpp); graphics requests ONLY on failure (DrawCapture.cpp `_failed`, ShaderRegistry.cpp `.prepare`).",
            "- Graphics success dumps need `APS5_DUMP_DRAW_SHADERS=<addr>` (one program) -> a local patch to dump every draw request is required before any game's VS/PS can be replayed for Xbox.",
            "- Shader caches (`<game>/shader_cache/<hash>/*.bin`, magic APSC8) hold PC-target SPIR-V (word count u64 before the magic); not usable for Xbox (mesh/BDA/subgroups).", ""]

    out += ["## Quest 2 (Turnip Adreno 650, Mesa 25.0.7)", ""]
    section(out, "8-bit storage requirement sites", scan(AGC, r'storageBuffer8BitAccess|StorageBuffer8BitAccess|VK_KHR_8bit_storage|uniformAndStorageBuffer8BitAccess'), "Every Quest game aborts at startup on these (VK_KHR_8bit_storage missing on Turnip). Fix = a fallback that avoids 8-bit storage when the device lacks it.", True)
    q = [l for l in last_lines(r"C:\AnyPS5 Games\_tools\live\watch-digest.txt", 60) if "quest" in l.lower()][:3]
    out += ["Last digest lines:"] + ["- " + l[:200] for l in q] + [""]

    out += ["## Chromebook (Galtic360, N6000 Jasper Lake, fanless, PL1 6 W)", ""]
    out += ["- Thermal: no fan (cros_ec fans read 0), thermald not installed, TCPU critical shutdown 105 C. anyps5-thermal.service (C:/AnyPS5 Games/_tools/chromebook-thermal) steers RAPL PL1/PL2 to hold 90 C; log /var/log/anyps5-thermal.log.",
            "- AVX: games relink with --to-sse (no AVX on N6000).", ""]
    section(out, "AVX-only code in core (needs SSE paths for Chromebook/Xbox Jaguar AVX1)", scan(os.path.join(ROOT, "core"), r'_mm256_|__AVX2__|immintrin'), "Jaguar has AVX1 but no AVX2; N6000 has neither.")

    out += ["## Build trees", ""]
    for d in sorted(glob.glob(os.path.join(ROOT, "build", "*"))):
        prx = glob.glob(os.path.join(d, "core", "libs", "libs", "**", "libSceAgcDriver.prx"), recursive=True)
        cache = os.path.join(d, "CMakeCache.txt")
        src = ""
        if os.path.exists(cache):
            m = re.search(r"CMAKE_HOME_DIRECTORY:INTERNAL=(.*)", open(cache, encoding="utf-8", errors="replace").read())
            src = m.group(1) if m else ""
        out.append("- `%s` src `%s` AgcDriver.prx: %s" % (d, src, prx[0] if prx else "none"))
    try:
        game = json.load(open(r"C:\AnyPS5 Games\_tools\settings.json", encoding="utf-8-sig")).get("anyps5Build")
        out.append("- Games build from `%s` (settings.json anyps5Build); the runtime libs are core/libs/libs/*.prx, swapped per game in <game>/libs." % game)
    except (OSError, ValueError):
        pass
    out.append("")
    open(OUT, "w", encoding="utf-8").write("\n".join(out) + "\n")
    print("wrote", OUT, len(out), "lines")


if __name__ == "__main__":
    main()
