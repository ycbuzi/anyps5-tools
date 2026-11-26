#!/bin/bash
# qb-relink.sh <build folder> - WSL port (11-16) of the Chromebook's ~/cb-work/azarm/cb-relink.sh, for the Azure ARM lane:
# the x86-64 relinker crashes under box64 on the VM's 48-bit address space, so each lane game is relinked here with
# that build's relinker. Called by jobboard.py (to_azarm).
# In:  /root/qb/quest-dist/<folder>/relinker-v*, /root/qb/azarm/games/<game>/{game.conf,eboot.elf}
# Out: /root/qb/azarm-relinked/<folder>/<game>.relinked (jobboard streams it to the VM with the build)
set -u
label=${1:?label}; Q=/root/qb/quest-dist/$label; O=/root/qb/azarm-relinked/$label
r=$(ls "$Q"/relinker-v* 2>/dev/null | head -1); [ -n "$r" ] || { echo "no relinker in $Q"; exit 1; }
mkdir -p "$O"; chmod +x "$r"
for conf in /root/qb/azarm/games/*/game.conf; do
  g=$(dirname "$conf"); game=$(basename "$g")
  [ -s "$O/$game.relinked" ] && continue
  args=$(DIR=$g; . "$conf" >/dev/null 2>&1; echo "$RELINK_ARGS")
  if nice -n 15 "$r" $args "$g/eboot.elf" "$O/$game.relinked.part" > "$O/$game.relink.log" 2>&1; then
    mv "$O/$game.relinked.part" "$O/$game.relinked"; echo "$game ok $(sha256sum "$O/$game.relinked" | cut -c1-16)"
  else
    echo "$game relink failed rc=$? (see $O/$game.relink.log)"; rm -f "$O/$game.relinked.part"
  fi
done
ls -1dt /root/qb/azarm-relinked/*/ 2>/dev/null | tail -n +5 | xargs -r rm -rf
