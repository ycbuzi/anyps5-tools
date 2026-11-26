#!/bin/bash
# Adds a PC game to the Quest's qtest pipeline: copies app0 (plus the decrypted eboot from _sources when app0 has none)
# into /root/<dir> in the Quest's Debian rootfs and writes its game.conf, so qpipe tests it on every new build.
# usage (Git Bash on the PC): bash quest-add-game.sh "<PC game folder name>" <quest dir> <binary name> "<relink args>" [timeout s]
# e.g. bash quest-add-game.sh "Animal Well (PPSA22520)" animalwell-linux animalwell "--to-rosetta"
set -euo pipefail
GAME="$1"; DIR="$2"; BIN="$3"; ARGS="$4"; TIMEOUT="${5:-1800}"
SRC="D:/AnyPS5 Games/$GAME"
EBOOT_SRC="D:/AnyPS5 Games/_sources/$GAME/eboot.elf"
PORT=$((9100 + RANDOM % 800))
ROOT='$PREFIX/var/lib/proot-distro/containers/debian/rootfs/root'
SEND="C:/AnyPS5 Games/_tools/assistant/skills/send_tar.py"
[ -d "$SRC/app0" ] || { echo "no app0 in $SRC"; exit 1; }
ssh -o BatchMode=yes -o ConnectTimeout=8 quest "R=$ROOT; [ -f \$R/$DIR/game.conf ] && { echo 'already on the Quest: $DIR'; exit 3; }; tmux kill-session -t qadd-$DIR 2>/dev/null; tmux new -d -s qadd-$DIR \"proot-distro login debian -- python3 /root/qtest/qrecv.py $PORT /root/$DIR 3 1\"" || exit $?
for i in 1 2 3 4 5 6 7 8 9 10; do
    ssh -o BatchMode=yes quest "grep -q 'listening on $PORT' $ROOT/$DIR/receive.log 2>/dev/null" && break
    ssh -o BatchMode=yes quest "sleep 2"
done
PAIRS=("$SRC/app0" app0)
if [ ! -f "$SRC/app0/eboot.elf" ]; then
    [ -f "$EBOOT_SRC" ] || { echo "no decrypted eboot for $GAME"; exit 1; }
    PAIRS+=("$EBOOT_SRC" app0/eboot.elf)
fi
python -I "$SEND" 192.0.2.1 $PORT "${PAIRS[@]}"
CONF="# $GAME: app0 from the PC (quest-add-game.sh $(date +%m-%d))
GAME_NAME=\"${GAME% (*}\"
EBOOT=\$DIR/app0/eboot.elf
BINARY=$BIN
RELINK_ARGS=\"$ARGS\"
TIMEOUT=$TIMEOUT"
printf '%s\n' "$CONF" | ssh -o BatchMode=yes quest "cat > $ROOT/$DIR/game.conf; tail -2 $ROOT/$DIR/receive.log; du -sh $ROOT/$DIR | cut -f1"
echo "added $GAME as /root/$DIR on the Quest"
