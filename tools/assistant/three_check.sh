#!/usr/bin/env bash
# One status pass over the three machines (Blu 11-06: "check the Mac, check the Chromebook, and check the PC").
# Prints a short block per machine and PROBLEM lines for anything that needs action. Changes nothing.
# usage: bash three_check.sh
T="/c/AnyPS5 Games/_tools"
problem() { echo "PROBLEM: $*"; }

echo "== PC $(date +%H:%M)"
pipes=$(powershell -NoProfile -Command "(Get-CimInstance Win32_Process -Filter \"Name='powershell.exe'\" | ? { \$_.CommandLine -match 'auto-games\.ps1' } | Measure-Object).Count" | tr -d '\r')
lanes=$(powershell -NoProfile -Command "(Get-CimInstance Win32_Process -Filter \"Name='powershell.exe'\" | ? { \$_.CommandLine -match 'gpu-lanes\.ps1' } | Measure-Object).Count" | tr -d '\r')
[ "$pipes" = "1" ] || problem "pipeline processes: $pipes (want 1)"
[ "$lanes" = "1" ] || problem "GPU lane runners: $lanes (want 1)"
trace_age=$(( ($(date +%s) - $(stat -c %Y "$T/auto-games-trace.log")) / 60 ))
echo "pipeline trace quiet ${trace_age} min; last events:"
grep -v '^\S* \S*    ' "$T/auto-games.log" | grep -v '^\s*$' | tail -4 | cut -c1-200
[ "$trace_age" -gt 45 ] && problem "pipeline trace quiet for $trace_age min"
echo "lanes:"; tail -3 "$T/gpu-lanes.log" | cut -c1-160
lane_age=$(( ($(date +%s) - $(stat -c %Y "$T/gpu-lanes.log")) / 60 ))
[ "$lane_age" -gt 30 ] && problem "GPU lanes log quiet for $lane_age min"
free=$(powershell -NoProfile -Command "Get-PSDrive C,D,G -EA SilentlyContinue | % { \$_.Name + '=' + [int](\$_.Free/1GB) + 'GB' }" | tr -d '\r' | tr '\n' ' ')
mem=$(powershell -NoProfile -Command "[int](Get-Counter '\Memory\Available MBytes').CounterSamples[0].CookedValue" | tr -d '\r')
echo "free: $free RAM ${mem} MB"
for d in $free; do n=${d#*=}; n=${n%GB}; [ "$n" -lt 20 ] && problem "drive low: $d"; done
[ "$mem" -lt 2500 ] && problem "PC RAM available ${mem} MB"
grep -h "$(date +%Y-%m-%d)" "$T/repair-runs/usage.jsonl" 2>/dev/null | python -I -c "import sys,json; r=[json.loads(l) for l in sys.stdin if l.strip()]; print('pipeline Claude today: %d calls, \$%.2f, %d failed' % (len(r), sum(x.get('cost',0) for x in r), sum(1 for x in r if not x.get('ok'))))"

echo "== Mac"
if out=$(ssh -o ConnectTimeout=12 -o BatchMode=yes macbook 'uptime | sed "s/.*load/load/"; pgrep -f "[c]laude -p" >/dev/null && echo "claude job: running" || echo "claude job: idle"; tail -1 ~/mac-work/status.md | cut -c1-220; df -h / | tail -1 | awk "{print \"disk free \" \$4}"' 2>/dev/null); then echo "$out"; else problem "Mac not reachable"; fi

echo "== Chromebook (AnyPS5 OS)"
if out=$(ssh -o ConnectTimeout=15 -o BatchMode=yes -o UserKnownHostsFile=~/.ssh/known_hosts_anyps5os -i ~/.ssh/id_ed25519_chromebook user@example.com 'uptime | sed "s/.*load/load/"; pgrep -x anyps5-console >/dev/null && echo "console: running" || echo "console: not running"; sed -n "s/^commit=/toolchain /p" /opt/anyps5/VERSION 2>/dev/null | cut -c1-22; df -h / | tail -1 | awk "{print \"disk free \" \$4}"' 2>/dev/null); then
    echo "$out"
    echo "$out" | grep -q "console: not running" && problem "AnyPS5 OS console is not running on the Chromebook"
else problem "Chromebook (AnyPS5 OS, 192.0.2.1) not reachable"; fi
