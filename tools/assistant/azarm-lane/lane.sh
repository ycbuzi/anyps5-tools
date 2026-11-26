#!/bin/bash
# lane.sh — one cycle of the Azure ARM lane (anyps5-arm, aarch64, box64 + lavapipe), started by alane.timer.
# Mirrors the Quest's qpipe offline loop without a GPU or Claude:
#   1. newest build in ~/alane/incoming/<label>/ (relinker-v<sha> + prx-linux-v<sha>.tar.gz, pushed by the PC
#      dispatcher from the Chromebook's quest-dist): every game in ~/alane/games/*/game.conf is relinked and run once.
#      A build not tested before is "new"; otherwise the run is a rerun (shows flaky or changed stops over time).
#   2. every test bundle ~/alane/tests/tests-<sha>/run-tests.sh not run yet: run once under box64.
#   3. results: ~/alane/results.tsv (history), ~/alane/results.txt (short summary the PC fetches), logs/ per run.
set -u
L=${ALANE:-$HOME/alane}; cd "$L" || exit 1
mkdir -p logs run
exec 9>"$L/run/lane.lock"; flock -n 9 || { echo "busy"; exit 0; }
TIMEOUT=${LANE_TIMEOUT:-600}
utc() { date -u +%FT%TZ; }
state() { echo "$(utc) $*" > "$L/run/state"; }

find "$L/logs" -type f -mtime +3 ! -name '*-latest-*' -delete 2>/dev/null
[ "$(wc -l < "$L/results.tsv" 2>/dev/null || echo 0)" -gt 6000 ] && tail -n 4000 "$L/results.tsv" > "$L/results.tsv.part" && mv "$L/results.tsv.part" "$L/results.tsv"
ls -1dt "$L"/incoming/*/ 2>/dev/null | tail -n +5 | xargs -r rm -rf

build=""
for d in $(ls -1dt "$L"/incoming/*/ 2>/dev/null); do
  r=$(ls "$d"relinker-v* 2>/dev/null | head -1); p=$(ls "$d"prx-linux-v*.tar.gz 2>/dev/null | head -1)
  [ -n "$r" ] && [ -n "$p" ] && { build=${d%/}; break; }
done

if [ -n "$build" ]; then
  label=$(basename "$build"); kind=rerun; [ -e "$build/.tested" ] || kind=new
  for conf in "$L"/games/*/game.conf; do
    [ -f "$conf" ] || continue
    g=$(dirname "$conf"); game=$(basename "$g")
    state "running $game with $label ($kind)"
    t0=$(date +%s)
    rl=""; [ -f "$build/$game.relinked" ] && rl=$build/$game.relinked
    if [ -z "$rl" ]; then
      printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$(utc)" "$game" "$label" "$kind" "-" "0" "WAIT no $game.relinked (Chromebook relink via dispatcher; box64 cannot run the relinker here)" "-" >> "$L/results.tsv"
      continue
    fi
    RELINKED=$rl timeout -k 30 $(( TIMEOUT + 900 )) "$L/bin/run-game.sh" "$g" "$r" "$p" "$TIMEOUT" >/dev/null 2>&1 < /dev/null; rc=$?
    [ "$rc" = 124 ] && grep -q '^exit=' "$g/run.log" && rc=$(sed -n 's/^exit=//p' "$g/run.log" | tail -1)
    secs=$(( $(date +%s) - t0 ))
    log=$L/logs/$game-$label-$(date +%m%d-%H%M%S).log; cp "$g/run.log" "$log" 2>/dev/null || : > "$log"
    reason=$("$L/bin/classify.sh" "$log" "$rc")
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$(utc)" "$game" "$label" "$kind" "$rc" "$secs" "$reason" "$log" >> "$L/results.tsv"
    for f in frame.png frame-late.png; do [ -f "$g/$f" ] && cp "$g/$f" "$L/logs/$game-latest-$f"; done
    bin=$(DIR=$g; BINARY=game; . "$conf" >/dev/null 2>&1; echo "$BINARY")
    rm -rf "$g/libs"; [ -n "$bin" ] && [ -f "$g/$bin" ] && rm -f "$g/$bin"
  done
  utc > "$build/.tested"
fi

for d in "$L"/tests/tests-*/; do
  [ -f "$d/run-tests.sh" ] || continue
  kind=rerun; [ -e "$d/.tested" ] || kind=new
  label=$(basename "$d"); state "running test bundle $label"
  t0=$(date +%s)
  ( cd "$d" && BOX64=/opt/box64-git/bin/box64 BOX64_AVX=2 BOX64_LOG=1 BOX64_SHOWSEGV=1 BOX64_NOBANNER=1 \
      BOX64_LD_PRELOAD=$L/libaps5shim-fixed.so timeout 1800 sh ./run-tests.sh > .run.out 2>&1 ); rc=$?
  sum=$(grep -E 'passed, .* failed' "$d/.run.out" | tail -1)
  { echo "$(utc) rc=$rc"; grep -E '^(PASS|FAIL) ' "$d/.run.out"; } > "$d/.tested"
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$(utc)" "tests" "$label" "$kind" "$rc" "$(( $(date +%s) - t0 ))" "${sum:-no summary}" "$d.run.out" >> "$L/results.tsv"
done

state "idle"
{
  echo "# Azure ARM lane (anyps5-arm, Neoverse-N1 2 vCPU, $(free -m | awk '/Mem:/ {print $2}') MB): box64 $(/opt/box64-git/bin/box64 --version 2>&1 | awk '{print $3, $4}'), $(vulkaninfo --summary 2>/dev/null | grep -m1 deviceName | sed 's/.*= //')"
  echo "# updated $(utc); rounds back to back, 5 min pause (alane.timer); columns: time game build kind rc secs result"
  echo "## latest per item"
  awk -F'\t' '{ k=($2=="tests")?$3:$2; ch=""; if (k in last && last[k] != $7) ch=" CHANGED"; last[k]=$7; line[k]=$1"  "$2"  "$3"  "$4"  rc="$5"  "$6"s  "$7 ch; if (!(k in seen)) { seen[k]=1; order[++n]=k } } END { for (i=1;i<=n;i++) print line[order[i]] }' "$L/results.tsv" 2>/dev/null
  echo "## recent changes"
  awk -F'\t' '{ k=($2=="tests")?$3:$2; if (k in last && last[k] != $7) print $1"  "k"  "last[k]"  ->  "$7; last[k]=$7 }' "$L/results.tsv" 2>/dev/null | tail -n 10
} > "$L/results.txt.part" && mv "$L/results.txt.part" "$L/results.txt"
