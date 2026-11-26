import collections
import json
import subprocess
import time

LIMITS = {"vncviewer.exe": 4, "vncviewer64.exe": 2, "ssh.exe": 12, "powershell.exe": 14, "python.exe": 10, "pythonw.exe": 6,
          "cmd.exe": 14, "bash.exe": 16, "mstsc.exe": 1, "obs64.exe": 1, "conhost.exe": 30, "wsl.exe": 8, "git.exe": 10, "tail.exe": 0}
OURS = ("AnyPS5", "anyps5", "_tools", "AnyPS5-work", "AnyPS5-tools", "/root/", "mac-work", "machines")


def procs():
    ps = ("Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,CommandLine,ThreadCount,WorkingSetSize,"
          "@{n='Start';e={$_.CreationDate.ToString('o')}} | ConvertTo-Json -Compress")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, encoding="utf-8", errors="replace",
                         creationflags=0x08000000).stdout
    data = json.loads(out or "[]")
    return data if isinstance(data, list) else [data]


def old(p, hours):
    import datetime
    try:
        start = datetime.datetime.fromisoformat(p["Start"])
    except (KeyError, TypeError, ValueError):
        return False
    return (datetime.datetime.now(start.tzinfo) - start).total_seconds() > hours * 3600


def main():
    ps = procs()
    alive = {p["ProcessId"] for p in ps}
    by = collections.defaultdict(list)
    for p in ps:
        by[(p["Name"] or "").lower()].append(p)
    issues = []
    for name, limit in LIMITS.items():
        mine = [p for p in by.get(name, []) if any(o in (p.get("CommandLine") or "") for o in OURS) or name.startswith("vncviewer")]
        if name == "tail.exe":
            mine = [p for p in by.get(name, []) if "live" in (p.get("CommandLine") or "") or "AnyPS5" in (p.get("CommandLine") or "")]
        stuck = [p for p in mine if name == "python.exe" and (p.get("ThreadCount") or 0) <= 1 and (p.get("WorkingSetSize") or 0) < (20 << 20) and old(p, 12)]
        mine = [p for p in mine if p not in stuck]
        if stuck:
            issues.append(f"{len(stuck)} x {name} stuck (1 thread, cannot be ended; clears on reboot) - not counted as a leak")
        lasting = [p for p in mine if old(p, 0.25)]
        if len(lasting) > limit:
            issues.append(f"{len(lasting)} x {name} older than 15 min (limit {limit}) - likely a leak/respawn loop; oldest started {min(p['Start'] for p in lasting)[:16]}")
        orphans = [p for p in mine if p["ParentProcessId"] not in alive and name in ("vncviewer.exe", "vncviewer64.exe", "ssh.exe", "mstsc.exe", "wsl.exe", "cmd.exe", "tail.exe")]
        if len(orphans) >= 2:
            issues.append(f"{len(orphans)} orphaned {name} of ours (parent gone) - nothing owns them")
    print("\n".join(issues) if issues else "no anomalies")


if __name__ == "__main__":
    main()
