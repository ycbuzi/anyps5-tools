#!/bin/bash
# Live view of azlinux's AnyPS5 PR tests for Blu's AnyPS5 Machines pane (VNC desktop :1).
W=$HOME/x64-work
while true; do
  clear
  echo "=== AnyPS5 Azure x64 (AVX2 PR tests, Spot) === $(date '+%H:%M:%S')   load: $(cut -d' ' -f1-3 /proc/loadavg)   mem free: $(free -m | awk '/Mem/{print $7}') MB"
  echo
  echo "--- testing now ---"
  if [ -f "$W/current" ]; then echo "PR $(cat "$W/current")"; else echo "idle"; fi
  pgrep -fa "ninja|ctest|cc1plus" | grep -v pgrep | sed -E 's#/home/blu/##g' | cut -c1-150 | head -4
  echo
  echo "--- next automatic run ---"
  systemctl list-timers x64-pr-auto.timer --no-pager 2>/dev/null | sed -n 2p | cut -c1-120
  echo
  echo "--- results (status.md) ---"
  tail -n 8 "$W/status.md" 2>/dev/null | cut -c1-170
  echo
  echo "--- distro/compiler matrix: $(cat "$W/matrix/now.txt" 2>/dev/null || echo idle) ---"
  tail -n 6 "$W/matrix/status.txt" 2>/dev/null | cut -c1-170
  echo
  L=$(ls -t "$W"/logs/*build.log "$W"/logs/*ctest*.log 2>/dev/null | head -1)
  echo "--- live log: $(basename "$L" 2>/dev/null) ---"
  tail -n 12 "$L" 2>/dev/null | cut -c1-160
  sleep 5
done
