import os
import sys
import time

paths = sys.argv[1:]
pos = {p: (os.path.getsize(p) if os.path.exists(p) else 0) for p in paths}
while True:
    for p in paths:
        try:
            size = os.path.getsize(p)
        except OSError:
            continue
        if size < pos[p]:
            pos[p] = 0
        if size > pos[p]:
            with open(p, "r", encoding="utf-8-sig", errors="replace") as f:
                f.seek(pos[p])
                data = f.read()
                pos[p] = f.tell()
            for line in data.splitlines():
                if line.strip():
                    print(f"{os.path.basename(p)}: {line.strip()}", flush=True)
    time.sleep(2)
