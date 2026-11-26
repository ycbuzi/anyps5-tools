r"""Which more of a game's libraries must come from the PR build when some are swapped in (11-12).

A PR library built from newer sources can drop or rename exports the game's older libraries import, or import exports
those older libraries lack. Windows then refuses to load one (RuneScape Dragonwilds with #18176: libSceVideoOut.prx,
GetLastError 127, exit 0xC0000135), and the pipeline used to rebuild all ~400 of the game's libraries to get past it.
This compares import and export tables by name and prints the game libraries that also have to come from the PR build:
  - a game library that imports a name the swapped-in library no longer exports;
  - a game library that lacks a name a swapped-in library imports from it.

usage: python -I pr_swap_set.py <game folder> <PR libs folder> <lib> [<lib> ...]
prints a JSON list of library base names to add (empty when the swap set is consistent)
"""
import json
import os
import re
import subprocess
import sys

OBJDUMP = os.path.join(r"D:\AnyPS5-tools\mingw-extract\mingw64\bin", "objdump.exe")
_cache = {}


def table(path):
    """(imports {dll_lower: set(names)}, exports set(names)) of one PE file."""
    if path in _cache:
        return _cache[path]
    out = subprocess.run([OBJDUMP, "-p", path], capture_output=True, text=True, errors="replace").stdout
    imports, exports, dll, in_exports = {}, set(), None, False
    for line in out.splitlines():
        if "DLL Name:" in line:
            dll, in_exports = line.split(":", 1)[1].strip().lower(), False
            imports.setdefault(dll, set())
            continue
        if line.startswith("[Ordinal/Name Pointer] Table"):
            dll, in_exports = None, True
            continue
        if in_exports:
            m = re.match(r"\s*\[\s*\d+\]\s+(\S+)", line)
            if m:
                exports.add(m.group(1))
            elif line.strip() == "" and exports:
                in_exports = False
            continue
        if dll:
            m = re.match(r"\s*[0-9a-fA-F]+\s+(?:<none>|\d+)\s+[0-9a-fA-F]+\s+(\S+)\s*$", line)
            if m:
                imports[dll].add(m.group(1))
            elif line.strip() == "":
                dll = None
    _cache[path] = (imports, exports)
    return _cache[path]


def needed(game, pr_libs, swapped):
    libs_dir = os.path.join(game, "libs")
    names = {f[:-4].lower(): f[:-4] for f in os.listdir(libs_dir) if f.lower().endswith(".prx")}
    swap = {s.lower() for s in swapped}
    source = {n: os.path.join(pr_libs if n in swap else libs_dir, names[n] + ".prx") for n in names}
    add = set()
    for n in names:
        if n in swap:
            continue
        imports, exports = table(source[n])
        for s in swap:
            dll = s + ".prx"
            if dll in imports and s in names:
                _, new_exports = table(source[s])
                if imports[dll] - new_exports:
                    add.add(names[n])
        for s in swap:
            if s not in names:
                continue
            s_imports, _ = table(source[s])
            if s_imports.get(n + ".prx", set()) - exports:
                add.add(names[n])
    return sorted(add)


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(2)
    game, pr_libs, swapped = sys.argv[1], sys.argv[2], sys.argv[3:]
    print(json.dumps(needed(game, pr_libs, swapped)))


if __name__ == "__main__":
    main()
