#!/bin/bash
# Double-click launchers and a live status window for the MacBook's offline AnyPS5 jobs (11-08).
# Run on the Mac: bash mac-launchers.sh   (.command files open in Terminal when double-clicked)
set -u
W=$HOME/mac-work
D=$HOME/Desktop
mkdir -p "$D"

cat > "$W/live.sh" <<'EOF'
#!/bin/bash
W=$HOME/mac-work
while true; do
  clear
  echo "=== AnyPS5 MacBook (offline PR tests, AVX-only CPU) === $(date '+%H:%M:%S')   load: $(sysctl -n vm.loadavg)"
  echo
  echo "--- running now ---"
  pgrep -fl "mac_prtest|mac_avx_check|ninja|ctest" | grep -v pgrep | cut -c1-140 | head -4
  [ -e "$W/prtest.lock" ] && echo "(PR test lock held)"
  echo
  echo "--- latest PR test reports ---"
  ls -t "$W/prtests" 2>/dev/null | head -5 | while read -r f; do echo "$f  $(grep -m1 -iE 'verdict|result|no new|new fail' "$W/prtests/$f" | cut -c1-110)"; done
  echo
  L=$(ls -t "$W"/logs/*.log 2>/dev/null | head -1)
  echo "--- newest log: $(basename "$L") ---"
  tail -n 12 "$L" 2>/dev/null | cut -c1-150
  sleep 5
done
EOF
chmod +x "$W/live.sh"

launcher() {
  printf '#!/bin/bash\n%s\necho\nread -r -p "Done. Press Enter to close this window. " _\n' "$2" > "$D/$1.command"
  chmod +x "$D/$1.command"
  echo "launcher: $1"
}
launcher "Run PR tests now" 'echo "Running the offline PR tests (lowest priority, 2 jobs)..."; bash "$HOME/mac-work/mac_prtest.sh"'
launcher "Run AVX check" 'echo "Building the --to-avx branch and counting AVX2 left..."; nice -n 19 bash "$HOME/mac-work/mac_avx_check.sh"'
launcher "Live Mac status" 'exec "$HOME/mac-work/live.sh"'
launcher "PR test results" 'f=$(ls -t "$HOME/mac-work/prtests"/*.md | head -1); echo "$f"; less "$f"'
open -a Terminal "$D/Live Mac status.command"
