#!/bin/bash
cd /c/AnyPS5-work/xbox-probe/console || exit 1
host=${1:-192.0.2.1}
version=${2:-1.0.4.0}
keys=${3:-ok}
ok=0
for i in $(seq 1 180); do
    c=$(curl -sk -m 5 -o /dev/null -w "%{http_code}" "https://$host:11443/")
    if [ "$c" != "000" ]; then ok=$((ok + 1)); else ok=0; fi
    [ $ok -ge 3 ] && break
    sleep 10
done
echo "portal $c steady $ok at $(date +%T)"
[ $ok -ge 3 ] || exit 1
timeout 420 python -u xtest.py "$PWD/out/AnyPS5Console.appx" AnyPS5.Console "$version" 20 2>&1 | grep -v "^ \|Traceback" | tail -6
python -u xkeys.py "$host" $keys 2>&1 | grep -v "^ \|Traceback" | tail -12
