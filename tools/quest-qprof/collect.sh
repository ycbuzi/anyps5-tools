#!/bin/bash
O=$1; START=${2:-90}; WIN=${3:-60}; export DISPLAY=:0
end=$(( $(date +%s) + 1200 ))
until P=$(pgrep -x celeste | head -1) && [ -n "$P" ]; do [ "$(date +%s)" -gt "$end" ] && exit 1; sleep 1; done
echo "$P" > "$O/game.pid"
t0=$(date +%s)
while [ $(( $(date +%s) - t0 )) -lt "$START" ]; do kill -0 "$P" 2>/dev/null || exit 1; sleep 1; done
snap() {
  T=$(awk '/TracerPid/ {print $2}' /proc/$P/status)
  X=$(pgrep -x termux-x11 | head -1)
  {
    echo "time $(date +%s.%N)"
    echo "tracer $T $(awk '{print $14, $15}' /proc/$T/stat 2>/dev/null)"
    echo "xserver $X $(awk '{print $14, $15}' /proc/$X/stat 2>/dev/null)"
    echo "game $P $(awk '{print $10, $12, $14, $15}' /proc/$P/stat)"
    grep -E 'ctxt_switches' /proc/$P/status | tr '\n' ' '; echo
    for t in /proc/$P/task/*; do echo "task ${t##*/} $(awk '{print $14, $15}' $t/stat 2>/dev/null) $(cat $t/comm 2>/dev/null)"; done
    for p in /proc/[0-9]*; do s=$(awk '{print $14+$15}' $p/stat 2>/dev/null) && echo "proc ${p##*/} $s $(cat $p/comm 2>/dev/null)"; done
  } > "$O/$1"
}
snap snap0
: > "$O/fps.samples"
for i in $(seq 1 $(( WIN / 5 ))); do
  title=$(wmctrl -l 2>/dev/null | grep -o 'FPS: [0-9.]*' | head -1 | cut -d' ' -f2)
  echo "$(date +%s) fps=${title:-?} gpu=$(cut -d' ' -f1 /sys/class/kgsl/kgsl-3d0/gpu_busy_percentage 2>/dev/null) freq=$(cat /sys/class/kgsl/kgsl-3d0/devfreq/cur_freq 2>/dev/null)" >> "$O/fps.samples"
  sleep 5
done
snap snap1
