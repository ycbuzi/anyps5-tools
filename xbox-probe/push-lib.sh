#!/bin/sh
# usage: push-lib.sh <name.prx> "<xbox game folder>"   copies a build-gpucap lib through xboxpatch into libset and the game, keeping the first backup in out/
set -e
cd /c/AnyPS5-work/xbox-probe
name=$1
game=$2
built=/c/AnyPS5-work/build-gpucap/core/libs/libs/$name
tmp=out/push-lib
rm -rf "$tmp"
python -I -X utf8 xboxpatch.py "$tmp" "$built" > /dev/null
src=$built
[ -f "$tmp/$name" ] && src=$tmp/$name
[ -f "out/$name.before-push" ] || cp -p "libset/$name" "out/$name.before-push"
cp "$src" "libset/$name"
cp "$src" "/c/AnyPS5-work/xbox-games/$game/libs/$name"
rm -rf "$tmp"
echo "$name copied to libset and $game"
