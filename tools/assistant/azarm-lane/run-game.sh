#!/bin/bash
# run-game.sh <game dir> <relinker> <prx archive> [timeout s] — Azure ARM lane game runner (mirror of the Quest's
# qtest/run-game-lvp.sh): relink the eboot with the x86-64 Linux relinker under box64, unpack the matching prx-linux
# libs, run the game under box64 + lavapipe (no GPU) on the lane's own Xvfb display, capture frames. Log: <game dir>/run.log
set -u
DIR=$(cd "${1:?usage: run-game.sh <game dir> <relinker> <prx archive> [timeout]}" && pwd)
RELINKER=${2:?relinker}; PRX_ARCHIVE=${3:?prx archive}
L=${ALANE:-$HOME/alane}
BINARY=game; TIMEOUT=600
. "$DIR/game.conf"
TIMEOUT=${4:-$TIMEOUT}
LOG=$DIR/run.log
BOX64=/opt/box64-git/bin/box64
SHIM=$L/libaps5shim-fixed.so
ICD=/usr/share/vulkan/icd.d/lvp_icd.aarch64.json
[ -f "$ICD" ] || ICD=/usr/share/vulkan/icd.d/lvp_icd.json
DISP=${LANE_DISPLAY:-:7}
RAM_MIN_MB=${RAM_MIN_MB:-120}
FRAME_RE='swapchain|present|VideoOut|flip'
die() { echo "ERROR: $*" | tee -a "$LOG" >&2; exit 1; }

cd "$DIR" || exit 1
: > "$LOG"
for f in "$RELINKER" "$PRX_ARCHIVE" "$EBOOT" "$BOX64" "$SHIM" "$ICD"; do [ -e "$f" ] || die "missing $f"; done
if ! DISPLAY=$DISP xdpyinfo >/dev/null 2>&1; then
  setsid nohup Xvfb "$DISP" -screen 0 1920x1080x24 -nolisten tcp >/dev/null 2>&1 < /dev/null &
  disown
  for i in $(seq 1 20); do DISPLAY=$DISP xdpyinfo >/dev/null 2>&1 && break; sleep 0.5; done
  DISPLAY=$DISP xdpyinfo >/dev/null 2>&1 || die "Xvfb $DISP did not start"
fi
echo "display $DISP (Xvfb), relinker sha256 $(sha256sum "$RELINKER" | cut -c1-16), archive sha256 $(sha256sum "$PRX_ARCHIVE" | cut -c1-16)" >> "$LOG"

if [ -n "${RELINKED:-}" ]; then
  echo "== relinked elsewhere: $RELINKED sha256 $(sha256sum "$RELINKED" | cut -c1-16)" >> "$LOG"
  cp "$RELINKED" "$DIR/$BINARY" || die "copy $RELINKED failed"
else
  echo "== relink $(date '+%H:%M:%S')" >> "$LOG"
  chmod +x "$RELINKER"
  BOX64_LOG=0 BOX64_NOBANNER=1 "$BOX64" "$RELINKER" $RELINK_ARGS "$EBOOT" "$DIR/$BINARY" >> "$LOG" 2>&1 || die "relinker failed (rc=$?)"
fi
chmod +x "$DIR/$BINARY"

echo "== unpack $(date '+%H:%M:%S')" >> "$LOG"
rm -rf "$DIR/libs" "$DIR/.unpack"; mkdir -p "$DIR/.unpack"
case "$PRX_ARCHIVE" in
  *.zip) unzip -q "$PRX_ARCHIVE" -d "$DIR/.unpack" ;;
  *.tar.gz|*.tgz) tar xzf "$PRX_ARCHIVE" -C "$DIR/.unpack" ;;
  *) die "unknown archive type: $PRX_ARCHIVE" ;;
esac || die "unpack failed"
PRXDIR=$(dirname "$(find "$DIR/.unpack" -name 'libkernel.prx' | head -1)")
[ -f "$PRXDIR/libkernel.prx" ] || die "no libkernel.prx in archive"
mv "$PRXDIR" "$DIR/libs"; rm -rf "$DIR/.unpack"
echo "libs: $(ls "$DIR/libs" | wc -l) files" >> "$LOG"

export XDG_RUNTIME_DIR=/tmp/xdg-alane; mkdir -p -m700 "$XDG_RUNTIME_DIR"
export DISPLAY=$DISP VK_ICD_FILENAMES=$ICD MESA_VK_WSI_DEBUG=sw
export BOX64_AVX=2 BOX64_LOG=1 BOX64_SHOWSEGV=1 BOX64_NOBANNER=1 BOX64_LD_PRELOAD=$SHIM
ln -sfn . "$DIR/\$ORIGIN"
while IFS= read -r module; do
  while IFS= read -r need; do
    rel=${need#\$ORIGIN/}; target="$(dirname "$module")/$rel"
    [ -e "$target" ] && [ ! -e "$DIR/$rel" ] && mkdir -p "$(dirname "$DIR/$rel")" && ln -s "$target" "$DIR/$rel"
  done < <(readelf -d "$module" 2>/dev/null | sed -n 's/.*Shared library: \[\(\$ORIGIN\/[^]]*\)\]/\1/p')
done < <(find "$DIR/app0" -name '*.guest.prx' 2>/dev/null)
export APS5_PAD_INPUT_FILE=$DIR/pad.txt ANYPS5_HOST_REPORT=1 APS5_ADDRESS_COPY_MAX_MIB=${APS5_ADDRESS_COPY_MAX_MIB:-160}
export SDL_VIDEO_X11_WMCLASS=anyps5-game
rm -f "$DIR"/frame*.png
echo "== run $(date '+%H:%M:%S') timeout ${TIMEOUT}s" >> "$LOG"
timeout -k 20 "$TIMEOUT" "$BOX64" "./$BINARY" >> "$LOG" 2>&1 &
GAME=$!

(
  while kill -0 $GAME 2>/dev/null; do
    avail=$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo)
    swapfree=$(awk '/SwapFree/ {print int($2/1024)}' /proc/meminfo)
    if [ $((avail + swapfree)) -lt "$RAM_MIN_MB" ]; then
      echo "== ram guard: MemAvailable ${avail} MB + SwapFree ${swapfree} MB < ${RAM_MIN_MB} MB, killing the game" >> "$LOG"
      pkill -KILL -P $GAME; kill -KILL $GAME; exit 0
    fi
    sleep 5
  done
) &
RAMW=$!

game_lines() { sed -n '/^== run /,$p' "$LOG" | grep -avE '^(== |\[BOX64\])'; }
snap() {
  local m
  import -display "$DISP" -window root "$DIR/$1" 2>>"$LOG" || return
  m=$(convert "$DIR/$1" -format '%[fx:mean]' info:)
  echo "== capture $1 $(date '+%H:%M:%S') mean=$m" >> "$LOG"
}
(
  t0=$(date +%s); hit=0; late=0
  while kill -0 $GAME 2>/dev/null && { [ $hit = 0 ] || [ $late = 0 ]; }; do
    if [ $hit = 0 ] && game_lines | grep -qiE "$FRAME_RE"; then sleep 2; snap frame.png; hit=1; fi
    if [ $late = 0 ] && [ $(( $(date +%s) - t0 )) -ge $(( TIMEOUT - 30 )) ]; then snap frame-late.png; late=1; fi
    sleep 2
  done
) &
CAPW=$!

wait $GAME; rc=$?
echo "exit=$rc" >> "$LOG"
kill $RAMW $CAPW 2>/dev/null
wait $RAMW $CAPW 2>/dev/null
exit $rc
