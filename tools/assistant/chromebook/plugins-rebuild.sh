#!/bin/bash
LOG=/root/idle-tests/plugins-rebuild.log
WORK=/home/anyps5/games/.rebuild
for g in "Coffee Talk Episode 2 (PPSA07053)" "Coffee Talk (PPSA07057)" "TERRIFIER The ArtCade Game (PPSA26362)"; do
  d="/home/anyps5/games/$g"
  out="$WORK/$g"
  rm -rf "$out"; mkdir -p "$out"
  echo "$(date +%H:%M) start $g" >> "$LOG"
  anyps5-build-game "$d/app0" "$out" > "$d/rebuild-plugins.txt" 2>&1
  code=$?
  if [ $code -ne 0 ] || [ ! -x "$out/game" ] || [ ! -d "$out/app0" ]; then
    echo "$(date +%H:%M) FAILED $g exit $code (see rebuild-plugins.txt); game left as it was" >> "$LOG"
    rm -rf "$out"
    continue
  fi
  while pgrep -f "^$d/game\$" >/dev/null; do sleep 30; done
  for item in game app0 libs relinker-output.txt; do
    [ -e "$out/$item" ] || continue
    rm -rf "$d/$item.old"
    [ -e "$d/$item" ] && mv "$d/$item" "$d/$item.old"
    mv "$out/$item" "$d/$item"
  done
  [ -e "$out/anyps5-build.json" ] && mv "$out/anyps5-build.json" "$d/anyps5-build.json"
  rm -rf "$d/game.old" "$d/app0.old" "$d/libs.old" "$d/relinker-output.txt.old" "$out"
  chown -R anyps5:anyps5 "$d/game" "$d/app0" "$d/libs" "$d/relinker-output.txt" "$d/anyps5-build.json" "$d/rebuild-plugins.txt" 2>/dev/null
  pe=0
  for f in "$d"/app0/Media/Plugins/*.guest.prx; do [ "$(head -c 2 "$f")" = MZ ] && pe=$((pe+1)); done
  n=$(ls "$d"/app0/Media/Plugins/*.guest.prx 2>/dev/null | wc -l)
  echo "$(date +%H:%M) done $g, plugin guest modules $n, still Windows $pe" >> "$LOG"
done
rmdir "$WORK" 2>/dev/null
