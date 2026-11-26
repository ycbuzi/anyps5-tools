"""One-shot: restart the games pipeline at its next pass break so it loads changed scripts, then remove its own task.

Run every 10 minutes by the scheduled task "AnyPS5 restart pipeline when free" (made when a script changed mid-pass).
Offline: no Claude. Log: _reports\\idle-filler.log.
"""
import datetime
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import work_skills

TASK = "AnyPS5 restart pipeline when free"
LOG = r"C:\AnyPS5 Games\_reports\idle-filler.log"


def main():
    out = work_skills.pipeline_restart()
    if out.startswith("Restarted"):
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(datetime.datetime.now().strftime("%Y-%m-%d %H:%M ") + "pipeline restarted to load changed scripts\n")
        subprocess.run(["schtasks", "/Delete", "/TN", TASK, "/F"], capture_output=True, creationflags=0x08000000)
    return 0


if __name__ == "__main__":
    sys.exit(main())
