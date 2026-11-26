cd /c/AnyPS5-work/xbox-probe
game="${1:-Stray (PPSA02100)}"
limit="${2:-180}"
H=$(cat out/.host)
python -X utf8 xbox_add_game.py "C:\\AnyPS5-work\\xbox-games\\$game" --pull 2>&1 | tail -1
python -X utf8 fetchfile.py "$H" anyps5.log --package=AnyPS5.Console > /dev/null 2>&1
before=$(grep -ac "ANYPS5_LOG_FILE: appending" out/anyps5.log 2>/dev/null)
python -X utf8 launchapp.py "$H" AnyPS5.Console Game > /dev/null 2>&1
start=$(date +%s)
state="not started"
while [ $(( $(date +%s) - start )) -lt "$limit" ]; do
    python -X utf8 fetchfile.py "$H" anyps5.log --package=AnyPS5.Console > /dev/null 2>&1
    [ "$(grep -ac "ANYPS5_LOG_FILE: appending" out/anyps5.log)" -gt "${before:-0}" ] || continue
    state=running
    awk '/ANYPS5_LOG_FILE: appending/{n=NR} {l[NR]=$0} END{for(i=n;i<=NR;i++) print l[i]}' out/anyps5.log > out/stray-run.txt
    if grep -qaE "^FATAL|what\(\)|driver failure" out/stray-run.txt; then state=crashed; break; fi
done
echo "$state after $(( $(date +%s) - start )) s, $(wc -l < out/stray-run.txt) log lines, $(grep -ac "videoout. flip\|present" out/stray-run.txt) present lines"
grep -naE "^FATAL|what\(\)|\[apr\]|  memory:|PreInit|driver failure|presentation failed" out/stray-run.txt | head -12 | cut -c1-220
python -X utf8 fetchfile.py "$H" xvk.log --package=AnyPS5.Console > /dev/null 2>&1
n=$(grep -an "debug layer on\|xvk start" out/xvk.log | tail -1 | cut -d: -f1)
tail -n +"${n:-1}" out/xvk.log | grep -avE "host import|chain:|did not pass any clear|OpenExistingHeap" | grep -aiE "fail|remov|D3D12 ERROR|CORRUPTION" | awk '!s[substr($0,1,70)]++' | head -6 | cut -c1-220
