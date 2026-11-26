#!/bin/bash
# Drive the running AnyPS5 OS app on the Quest with console keys, then save its console frame (not a headset capture).
# usage: bash qshot.sh "<keys, e.g. ok wait:2 down ok>" <seconds to wait> <out.png>
export MSYS_NO_PATHCONV=1
A=/c/AnyPS5-tools/platform-tools/adb.exe
S=192.0.2.1:5555
D=/sdcard/Android/data/com.anyps5.os/files
KEYS="$1"; WAIT="${2:-5}"; OUT="${3:-C:/AnyPS5-work/quest-console-shot.png}"
"$A" -s $S shell "rm -f $D/console-shot.png; echo '$KEYS' > $D/keys; sleep $WAIT; touch $D/shot-request; for i in 1 2 3 4 5 6 7 8 9 10; do [ -s $D/console-shot.png ] && break; sleep 0.5; done; sleep 1"
"$A" -s $S pull $D/console-shot.png "$OUT" >/dev/null && echo "saved $OUT"
