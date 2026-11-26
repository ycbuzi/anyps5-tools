#!/bin/sh
# Continuous repeat/stress loop between the hourly run.sh runs (Blu 11-08: the VM is on 24/7, keep it busy).
# One instance only (lock .repeat.lock); each round takes .run.lock so it never overlaps run.sh.
# A round runs every probe in bin/ (built by run.sh) plus stress/stress_kernel.c, and tallies every result line in
# flaky/counts.txt. flaky/flaky.txt lists the cases that gave more than one result across rounds, with counts.
# usage: sh ~/probes/repeat.sh            (cron starts it @reboot and every 30 min; exits at once if already running)
#        sh ~/probes/repeat.sh round      (one round, what the loop runs)
cd "$(dirname "$0")" || exit 1
here=$(pwd)
if [ "$1" = round ]; then
  mkdir -p flaky stress/bin
  if [ ! -x stress/bin/stress_kernel ] || [ stress/stress_kernel.c -nt stress/bin/stress_kernel ] || [ probe.h -nt stress/bin/stress_kernel ]; then
    cc -O1 -Wall -Wno-unused-result -o stress/bin/stress_kernel stress/stress_kernel.c -lpthread 2> flaky/stress-build.txt || echo "stress.build FAILED" > flaky/stress-build-failed
  fi
  out=$(mktemp -t repeat)
  for b in bin/* stress/bin/*; do
    [ -x "$b" ] || continue
    name=$(basename "$b")
    limit=120; [ "$name" = stress_kernel ] && limit=300
    timeout $limit "$b" >> "$out" 2>&1
    echo "$name.exit rc=$?" >> "$out"
  done
  touch flaky/counts.txt
  awk -F '\t' 'FILENAME == ARGV[1] { c[$2] += $1; next } { c[$0]++ } END { for (l in c) printf "%d\t%s\n", c[l], l }' flaky/counts.txt "$out" | sort -t "$(printf '\t')" -k2 > flaky/counts.new && mv flaky/counts.new flaky/counts.txt
  rm -f "$out"
  rounds=$(( $(cat flaky/rounds 2>/dev/null || echo 0) + 1 )); echo "$rounds" > flaky/rounds
  awk -F '\t' -v rounds="$rounds" -v now="$(date -u +%FT%TZ)" '{ split($2, f, " "); key = f[1]; val = substr($2, length(key) + 2); n[key]++; s[key] = s[key] " | " val " x" $1 }
    END { printf "rounds %d, updated %s\n", rounds, now; for (k in n) if (n[k] > 1) print k s[k] | "sort" }' flaky/counts.txt > flaky/flaky.new && mv flaky/flaky.new flaky/flaky.txt
  exit 0
fi
[ "$1" = locked ] || exec /usr/bin/lockf -k -t 0 "$here/.repeat.lock" /bin/sh "$here/repeat.sh" locked
while :; do
  [ -d bin ] && /usr/bin/lockf -k "$here/.run.lock" /bin/sh "$here/repeat.sh" round
  sleep 10
done
