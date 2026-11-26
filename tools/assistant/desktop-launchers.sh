#!/bin/bash
# Double-click launchers for Blu's offline AnyPS5 jobs on each machine's VNC desktop (11-08).
# usage: bash desktop-launchers.sh arm|bsd|chromebook|quest
# Each launcher runs a small script in ~/anyps5-launchers in a terminal window, so Blu sees the job and its result.
set -u
machine=$1
dir=$HOME/anyps5-launchers
desk=$HOME/Desktop
mkdir -p "$dir" "$desk"
case $machine in
  chromebook) term="xterm -fa Monospace -fs 11 -bg black -fg white -T" ;;
  *) term="xfce4-terminal --disable-server --title" ;;
esac
shell=$(command -v bash)

job() {
  local file=$1 title=$2 icon=$3 body=$4
  printf '#!%s\n%s\necho\nread -r -p "Done. Press Enter to close this window. " _\n' "$shell" "$body" > "$dir/$file.sh"
  chmod +x "$dir/$file.sh"
  if [ "$machine" = chromebook ]; then exec_line="$term \"$title\" -e $dir/$file.sh"; else exec_line="$term \"$title\" -x $dir/$file.sh"; fi
  printf '[Desktop Entry]\nVersion=1.0\nType=Application\nName=%s\nExec=%s\nIcon=%s\nTerminal=false\n' "$title" "$exec_line" "$icon" > "$desk/$file.desktop"
  chmod +x "$desk/$file.desktop"
  if command -v gio >/dev/null 2>&1; then
    gio set -t string "$desk/$file.desktop" metadata::xfce-exe-checksum "$(sha256sum "$desk/$file.desktop" 2>/dev/null | cut -d' ' -f1)" 2>/dev/null || true
  fi
  echo "launcher: $title"
}

case $machine in
  arm)
    job lane-run "Run Quest-style test round" system-run 'echo "Starting a test round now (box64 + lavapipe, same as the Quest)..."; sudo systemctl start alane.service && journalctl -u alane.service -n 30 --no-pager -f & sleep 600; kill %1 2>/dev/null'
    job lane-live "Live lane status" utilities-system-monitor 'exec /home/blu/alane/bin/live.sh'
    job lane-results "Test results" text-x-generic 'less +G /home/blu/alane/results.txt'
    ;;
  bsd)
    job probes-run "Run kernel probes now" system-run 'echo "Running every FreeBSD kernel probe..."; lockf -k /home/blu/probes/.run.lock nice -n 10 sh /home/blu/probes/run.sh; echo; tail -n 25 /home/blu/probes/results.diff'
    job probes-stress "Run stress repeat" system-run 'nice -n 10 sh /home/blu/probes/repeat.sh; tail -n 20 /home/blu/probes/repeat.log 2>/dev/null'
    job probes-live "Live probe status" utilities-system-monitor 'exec sh /home/blu/probes/live.sh'
    job probes-results "Probe results" text-x-generic 'less /home/blu/probes/results.txt'
    ;;
  x64)
    job x64-prtests "Run PR tests now" system-run 'echo "Checking open PRs and testing new ones now..."; sudo systemctl start --no-block x64-pr-auto.service; journalctl -u x64-pr-auto.service -n 30 --no-pager -f & sleep 900; kill %1 2>/dev/null'
    job x64-live "Live PR test status" utilities-system-monitor 'exec /home/blu/x64-work/bin/live.sh'
    job x64-status "Status notes" text-x-generic 'less +G /home/blu/x64-work/status.md'
    job x64-reports "PR test reports" folder 'ls -t /home/blu/x64-work/prtests | head -20; echo; f=$(ls -t /home/blu/x64-work/prtests/*.md 2>/dev/null | head -1); [ -n "$f" ] && less "$f"'
    ;;
  chromebook)
    job cb-prtests "Run PR tests now" system-run 'echo "Starting the PR test run..."; systemctl --user start cb-pr-auto.service; journalctl --user -u cb-pr-auto.service -n 30 --no-pager -f & sleep 900; kill %1 2>/dev/null'
    job cb-questbuild "Make a Quest build now" system-run 'echo "Building and sending a Quest build..."; systemctl --user start cb-quest-builds.service; journalctl --user -u cb-quest-builds.service -n 30 --no-pager -f & sleep 1800; kill %1 2>/dev/null'
    job cb-live "Live Chromebook status" utilities-system-monitor 'exec /home/user/cb-work/live.sh'
    job cb-status "Status notes" text-x-generic 'less +G /home/user/cb-work/status.md'
    ;;
  quest)
    job q-run "Run Quest game tests now" system-run 'echo "Running the Quest game tests now (Celeste, TERRIFIER)..."; nice -n 15 /root/qtest/qpipe.sh nightly-now; ls -t /root/qtest/logs | head -3'
    job q-cycle "Check for new builds now" system-run 'touch /root/qtest/run/trigger; echo "Asked the pipeline to check for new builds now."; sleep 2; tail -n 5 /root/qtest/run/qpipe.log'
    job q-live "Live Quest status" utilities-system-monitor 'exec /root/qtest/live.sh'
    ;;
esac
