r"""Lists the DLLs a game folder's executable and libraries import that Windows will not find (exit 0xC0000135).

usage: python -I missing_dll.py <game folder or libs folder> [--extra-libs <folder>] [--json]

Looks in the folder of each importer, the game's libs\ folder, --extra-libs (for example a PR build's libs that
will be swapped in) and System32, the places the converted games load from. Each missing name is printed with the
files that import it. Exit 1 when something is missing, 0 when everything resolves.
"""
import json
import os
import subprocess
import sys

OBJDUMP = os.path.join(r"D:\AnyPS5-tools\mingw-extract\mingw64\bin", "objdump.exe")
SYSTEM = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
PE_EXT = (".exe", ".dll", ".prx", ".sprx")


def imports(path):
    out = subprocess.run([OBJDUMP, "-p", path], capture_output=True, text=True, errors="replace").stdout
    return [line.split(":", 1)[1].strip() for line in out.splitlines() if "DLL Name:" in line]


def check(root, extra=None):
    root = os.path.abspath(root)
    libs = os.path.join(root, "libs") if os.path.isdir(os.path.join(root, "libs")) else root
    files = [os.path.join(root, f) for f in os.listdir(root) if f.lower().endswith(".exe")]
    files += [os.path.join(libs, f) for f in os.listdir(libs) if f.lower().endswith(PE_EXT)]
    if extra:
        files += [os.path.join(extra, f) for f in os.listdir(extra) if f.lower().endswith(PE_EXT)]
    places = [libs, root, SYSTEM] + ([extra] if extra else [])
    have = {f.lower() for p in places if os.path.isdir(p) for f in os.listdir(p)}
    missing = {}
    for f in files:
        for name in imports(f):
            low = name.lower()
            if low in have or low.startswith(("api-ms-win-", "ext-ms-")):
                continue
            missing.setdefault(name, []).append(os.path.relpath(f, root))
    return missing


def main():
    args = sys.argv[1:]
    extra = args[args.index("--extra-libs") + 1] if "--extra-libs" in args else None
    root = next(a for a in args if not a.startswith("--") and a != extra)
    missing = check(root, extra)
    if "--json" in args:
        print(json.dumps(missing))
    elif missing:
        for name, users in sorted(missing.items()):
            print(f"missing {name}: imported by {', '.join(sorted(users)[:5])}")
    else:
        print("every import resolves")
    sys.exit(1 if missing else 0)


if __name__ == "__main__":
    main()
