#!/data/data/com.termux/files/usr/bin/bash
R=$PREFIX/var/lib/proot-distro/containers/debian/rootfs
P=/root/qtest/qprof; RP=$R$P; Q=$R/root/qtest
B=${QPROF_BUILD:-/root/qtest/incoming/quest-integration-1116-2105f14a5cb}
O=out-$(date +%m%d-%H%M%S); mkdir -p "$RP/$O"; echo "$P/$O" > "$RP/current-out"
DQ=$Q/run/diag-queue.active; DQOLD=$(stat -c %Y "$DQ" 2>/dev/null)
log() { echo "$(date +%T) $*" >> "$RP/$O/driver.log"; }
PL() { proot-distro login debian -- bash -lc "$1"; }
finish() { if [ -n "$DQOLD" ]; then touch -d "@$DQOLD" "$DQ"; else rm -f "$DQ"; fi; log "end $1"; echo "$1" > "$RP/$O/finished"; exit 0; }
log compile
PL "cd $P && gcc -O2 -shared -fPIC -o libqprof.so qprof.c -lpthread && gcc -O2 -o bench-arm bench.c && gcc -O2 -nostdlib -static -fno-builtin -fno-stack-protector -o cpu-arm cpubench.c && x86_64-linux-gnu-gcc -O2 -nostdlib -static -fno-builtin -fno-stack-protector -mstackrealign -o cpu-x86 cpubench.c" >> "$RP/$O/driver.log" 2>&1 || finish compile-failed
chmod +x "$RP"/collect.sh "$RP"/run-game-qprof.sh
log "wait for idle"
deadline=$(( $(date +%s) + 2400 ))
until flock -n "$Q/run/qtest.lock" true && ! pgrep -f "box64 ./" >/dev/null; do
  touch "$DQ"; [ "$(date +%s)" -gt "$deadline" ] && finish idle-timeout; sleep 15
done
touch "$DQ"
log benches
for i in 1 2; do
  PL "$P/bench-arm" >> "$RP/$O/bench-proot.txt" 2>&1
  "$R/lib/ld-linux-aarch64.so.1" --library-path "$R/lib/aarch64-linux-gnu:$R/usr/lib/aarch64-linux-gnu" "$RP/bench-arm" >> "$RP/$O/bench-direct.txt" 2>&1
done
tm() { local a b; a=$(date +%s%N); "$@" >/dev/null 2>&1; b=$(date +%s%N); echo $(( (b - a) / 1000000 )); }
for i in 1 2 3; do
  echo "cpu-arm direct ms $(tm "$RP/cpu-arm")" >> "$RP/$O/cpu.txt"
  PL "a=\$(date +%s%N); $P/cpu-arm; b=\$(date +%s%N); echo cpu-arm proot ms \$(( (b-a)/1000000 )); a=\$(date +%s%N); BOX64_NOBANNER=1 BOX64_LOG=0 BOX64_DYNACACHE=0 /opt/box64-once/bin/box64 $P/cpu-x86; b=\$(date +%s%N); echo cpu-x86 box64 ms \$(( (b-a)/1000000 ))" >> "$RP/$O/cpu.txt" 2>&1
done
log game
for try in 1 2 3 4 5 6; do
  touch "$DQ"
  PL "$P/collect.sh $P/$O 90 60 & GAME_FULLSCREEN=0 LABEL=qprof RUN_GAME=$P/run-game-qprof.sh RELINKER=\$(ls $B/relinker-*) PRX_ARCHIVE=\$(ls $B/prx-linux-*) /root/qtest/test-game.sh /root/qtest/pr/celeste-linux-9387 200 > $P/$O/test.txt 2>&1; rc=\$?; [ \$rc = 3 ] && kill %1 2>/dev/null; wait; exit \$rc"
  rc=$?; log "test rc=$rc"; [ $rc != 3 ] && break; sleep 30
done
log analyze
PL "python3 -I $P/analyze.py $P/$O > $P/$O/summary.txt 2>$P/$O/analyze.err; pid=\$(awk 'NR==1{print \$3}' $P/$O/samples 2>/dev/null); ls -la /tmp/perf-\$pid.map >> $P/$O/driver.log 2>&1; rm -f /tmp/perf-*.map"
finish ok
