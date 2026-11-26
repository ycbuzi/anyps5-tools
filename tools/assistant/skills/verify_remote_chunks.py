"""Checks that a big file copied to another machine matches the local one: compares size and the MD5 of 64 MB chunks
at 12 spread offsets (a full hash of a 13 GB file over ssh would take too long on the old Mac). A copy that broke
and left a full-size file with zero-filled holes fails here. Exit 0 = match.
usage: python -I verify_remote_chunks.py HOST LOCAL_FILE REMOTE_PATH"""
import hashlib
import os
import subprocess
import sys

CHUNK_MB = 64


def main():
    host, local, remote = sys.argv[1:4]
    size = os.path.getsize(local)
    rsize = subprocess.run(["ssh", "-o", "BatchMode=yes", host, f"stat -f %z '{remote}' 2>/dev/null || stat -c %s '{remote}'"],
                           capture_output=True, text=True).stdout.strip()
    if rsize != str(size):
        print(f"size differs: local {size}, remote {rsize}")
        return 1
    total_mb = size // (1 << 20)
    offsets = sorted({max(0, min(total_mb - CHUNK_MB, total_mb * i // 11)) for i in range(12)})
    bad = 0
    with open(local, "rb") as f:
        for off in offsets:
            f.seek(off << 20)
            mine = hashlib.md5(f.read(CHUNK_MB << 20)).hexdigest()
            theirs = subprocess.run(["ssh", "-o", "BatchMode=yes", host,
                                     f"dd if='{remote}' bs=1048576 skip={off} count={CHUNK_MB} 2>/dev/null | (md5 2>/dev/null || md5sum | cut -d' ' -f1)"],
                                    capture_output=True, text=True).stdout.strip()
            ok = mine == theirs
            bad += not ok
            print(f"{off} MB: {'ok' if ok else 'DIFFERS'}")
    print("match" if not bad else f"{bad} chunk(s) differ")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
