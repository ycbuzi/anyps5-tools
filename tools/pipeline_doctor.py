"""Puts the pipeline back on its feet when it is not running: a script that no longer parses goes back to the last version
that completed a pass, a lost state file comes back from its hourly copy, then the pipeline starts. Prints JSON."""
import json
import shutil
import subprocess
import time
from pathlib import Path

T = Path(__file__).resolve().parent
SCRIPT, GOOD, STATE, COPY = T / "auto-games.ps1", T / "auto-games.ps1.good", T / "auto-state.json", T / "auto-state.json.backup"
fixed = []


def powershell(command, **env):
    import os
    return subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True, text=True, creationflags=0x08000000,
                          env=dict(os.environ, **env)).stdout.strip()


def running():
    return powershell("@(Get-CimInstance Win32_Process -Filter \"Name='powershell.exe'\" | Where-Object { $_.CommandLine -match 'auto-games\\.ps1' }).Count") not in ("", "0")


errors = powershell("$e = $null; [void][System.Management.Automation.Language.Parser]::ParseFile($env:S, [ref]$null, [ref]$e); $e | Select-Object -First 3 | ForEach-Object { 'line ' + $_.Extent.StartLineNumber + ': ' + $_.Message }", S=str(SCRIPT))
if errors:
    if GOOD.exists():
        keep = T / f"auto-games.ps1.broken-{time.strftime('%m%d-%H%M%S')}"
        shutil.copy2(SCRIPT, keep)
        shutil.copy2(GOOD, SCRIPT)
        fixed.append(f"auto-games.ps1 did not parse ({errors.splitlines()[0]}); put back the last working version, the broken one is {keep.name}")
    else:
        fixed.append(f"auto-games.ps1 does not parse ({errors.splitlines()[0]}) and there is no working copy to put back")
if not STATE.exists() and COPY.exists():
    shutil.copy2(COPY, STATE)
    fixed.append("auto-state.json was missing; put back its copy from " + time.strftime("%H:%M", time.localtime(COPY.stat().st_mtime)))
alive = running()
if not alive and not (T / "pause.flag").exists() and not (errors and not GOOD.exists()):
    subprocess.Popen(["wscript.exe", str(T / "start-auto-games.vbs")])
    fixed.append("the pipeline was not running; started it")
print(json.dumps({"running": alive, "fixed": fixed}))
