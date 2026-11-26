#!/bin/sh
# FreeBSD kernel reference runner for AnyPS5's libkernel (nightly from cron, low priority).
# Builds every probes/*.c, runs each with a time limit, and writes:
#   results.txt       all probe lines, sorted, no timestamps (stable, diffable)
#   results.prev.txt  the previous run
#   results.diff      diff prev -> current ("no changes" when identical)
#   results.meta      date, kernel, compiler, per-probe exit codes
#   history/<date>.txt  last 30 runs
# usage: sh ~/probes/run.sh
cd "$(dirname "$0")" || exit 1
mkdir -p bin build history
tmp=$(mktemp -t probes)
meta=$(mktemp -t probesmeta)
{
  echo "run $(date -u +%FT%TZ)"
  echo "kernel $(uname -mrs) osreldate $(sysctl -n kern.osreldate)"
  echo "cc $(cc --version | head -1)"
} > "$meta"
for src in *.c; do
  name=${src%.c}
  if cc -O1 -Wall -Wno-unused-result -o "bin/$name" "$src" -lpthread -lrt 2> "build/$name.txt"; then
    timeout 120 "./bin/$name" > "build/$name.out" 2>&1
    rc=$?
    cat "build/$name.out" >> "$tmp"
    echo "$name.exit rc=$rc" >> "$tmp"
    echo "$name rc=$rc lines=$(wc -l < "build/$name.out" | tr -d ' ')" >> "$meta"
  else
    echo "$name.build FAILED" >> "$tmp"
    echo "$name BUILD FAILED (build/$name.txt)" >> "$meta"
  fi
done
[ -f results.txt ] && mv results.txt results.prev.txt
sort "$tmp" > results.txt
rm -f "$tmp"
if [ -f results.prev.txt ]; then
  if diff -u results.prev.txt results.txt > results.diff; then echo "no changes" > results.diff; fi
else
  echo "first run" > results.diff
fi
echo "lines $(wc -l < results.txt | tr -d ' ') diff $(grep -c '^[-+][^-+]' results.diff) changed" >> "$meta"
mv "$meta" results.meta
cp results.txt "history/$(date -u +%F).txt"
ls -1t history/*.txt 2>/dev/null | tail -n +31 | xargs rm -f
cat results.meta
