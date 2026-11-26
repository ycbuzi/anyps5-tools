#!/data/data/com.termux/files/usr/bin/bash
R=$PREFIX/var/lib/proot-distro/containers/debian/rootfs
P=/root/qtest/qprof; RP=$R$P; Q=$R/root/qtest
B=${QPROF_BUILD:-/root/qtest/incoming/quest-integration-1116-2105f14a5cb}
O=ab-$(date +%m%d-%H%M%S); mkdir -p "$RP/$O"
DQ=$Q/run/diag-queue.active; DQOLD=$(stat -c %Y "$DQ" 2>/dev/null)
log() { echo "$(date +%T) $*" >> "$RP/$O/driver.log"; }
finish() { if [ -n "$DQOLD" ]; then touch -d "@$DQOLD" "$DQ"; else rm -f "$DQ"; fi; log "end $1"; echo "$1" > "$RP/$O/finished"; exit 0; }
log "wait for idle"
deadline=$(( $(date +%s) + 2400 ))
until flock -n "$Q/run/qtest.lock" true && ! pgrep -f "box64 ./" >/dev/null; do
  touch "$DQ"; [ "$(date +%s)" -gt "$deadline" ] && finish idle-timeout; sleep 15
done
for mode in ${AB_MODES:-default nokr default nokr}; do
  touch "$DQ"
  if [ "$mode" = nokr ]; then export PD_PROOT_BIN=$HOME/native/proot-nokr; else unset PD_PROOT_BIN; fi
  D=$P/$O/$mode-$(date +%H%M%S); mkdir -p "$R$D"
  log "game $mode -> $D"
  proot-distro login debian -- bash -lc "exec 9>/root/qtest/run/qtest.lock; flock -w 600 9 || exit 3; export DISPLAY=:0; $P/collect.sh $D 90 60 & (sleep 120; g=\$(pgrep -x celeste | head -1); t=\$(awk '/TracerPid/ {print \$2}' /proc/\$g/status); echo tracer \$t; tr '\0' ' ' < /proc/\$t/cmdline | grep -o 'kernel-release=[^ ]*' || echo no-kernel-release) > $D/tracer.txt 2>&1 & GAME_FULLSCREEN=0 RELINKER=\$(ls $B/relinker-*) PRX_ARCHIVE=\$(ls $B/prx-linux-*) /root/run-game.sh /root/qtest/pr/celeste-linux-9387 200 > $D/test.txt 2>&1; rc=\$?; wait; exit \$rc"
  log "rc=$? fps $(awk '{print $2}' "$R$D/fps.samples" 2>/dev/null | tr '\n' ' ')"
  sleep 20
done
unset PD_PROOT_BIN
finish ok
