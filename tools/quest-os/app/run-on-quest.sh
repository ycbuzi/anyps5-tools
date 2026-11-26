#!/bin/bash
# Build, install and start AnyPS5 OS on the Quest, then print its log: bash run-on-quest.sh [--no-build] [seconds]
export MSYS_NO_PATHCONV=1
HERE="$(cd "$(dirname "$0")" && pwd)"
A=/c/AnyPS5-tools/platform-tools/adb.exe
S=192.0.2.1:5555
LOG=/sdcard/Android/data/com.anyps5.os/files/anyps5-os.log
if [ "$1" = "--no-build" ]; then shift; else env -u MSYS_NO_PATHCONV bash "$HERE/build.sh" 2>&1 | grep -E "error:|undefined|apk" | cut -c1-220; fi
WAIT=${1:-30}
"$A" connect $S >/dev/null 2>&1
"$A" -s $S install -r "C:/AnyPS5-work/quest-os/app/anyps5-os.apk" | tail -1
"$A" -s $S shell am force-stop com.anyps5.os
"$A" -s $S logcat -c
"$A" -s $S shell am broadcast -a com.oculus.vrpowermanager.prox_close >/dev/null
"$A" -s $S shell am start -n com.anyps5.os/android.app.NativeActivity | tail -1
"$A" -s $S shell "for i in \$(seq 1 $WAIT); do grep -q 'frames 90 \|FAIL\|stopped' $LOG 2>/dev/null && break; sleep 1; done"
"$A" -s $S shell cat $LOG | head -20
echo "pid: $("$A" -s $S shell pidof com.anyps5.os)"
"$A" -s $S logcat -d | grep -E "FATAL|  #[0-9][0-9] pc|SDL" | head -12 | cut -c1-200
