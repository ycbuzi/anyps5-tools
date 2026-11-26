#!/bin/bash
# Live view of the Quest's AnyPS5 test pipeline on its desktop (11-08), so Blu sees what it is doing.
Q=/root/qtest
while true; do
  clear
  echo "=== AnyPS5 on Meta Quest 2: test pipeline === $(date '+%H:%M:%S')   load: $(cut -d' ' -f1-3 /proc/loadavg)"
  echo
  echo "--- pipeline ---"
  tail -n 1 $Q/run/pipe-state 2>/dev/null | cut -c1-140
  echo
  echo "--- running now ---"
  pgrep -fa "run-game|box64|ctest" | grep -v pgrep | cut -c1-140 | head -4
  echo
  echo "--- latest test runs ---"
  tail -n 6 $Q/app-runs.log 2>/dev/null | cut -c1-150
  echo
  L=$(ls -t $Q/logs/*.log 2>/dev/null | head -1)
  echo "--- newest log: $(basename "$L") ---"
  tail -n 10 "$L" 2>/dev/null | cut -c1-150
  echo
  echo "--- pipeline log ---"
  tail -n 4 $Q/run/qpipe.log 2>/dev/null | cut -c1-150
  sleep 5
done
