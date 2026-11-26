#!/usr/bin/env bash
# Starts the AnyPS5 OS host (host.py) and shows the console screen in a kiosk browser. Installs nothing.
# Environment: ANYPS5_OS_PORT (default 8790), ANYPS5_GAMES (default ~/AnyPS5 Games), ANYPS5_OS_DATA (default os-host/data).
set -u

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
port="${ANYPS5_OS_PORT:-8790}"
games="${ANYPS5_GAMES:-$HOME/AnyPS5 Games}"
data="${ANYPS5_OS_DATA:-$here/data}"
url="http://127.0.0.1:${port}/"

case "$port" in ''|*[!0-9]*) echo "start-anyps5-os: bad port '$port'" >&2; exit 2 ;; esac

python=""
for p in python3 python; do
    if command -v "$p" >/dev/null 2>&1; then python="$p"; break; fi
done
if [ -z "$python" ]; then
    echo "start-anyps5-os: python3 is not installed" >&2
    exit 1
fi

mkdir -p "$data"
"$python" "$here/host.py" --port "$port" --games "$games" --data "$data" &
host_pid=$!
browser_pid=""

cleanup() {
    if [ -n "$browser_pid" ]; then kill "$browser_pid" 2>/dev/null; fi
    kill "$host_pid" 2>/dev/null
    wait "$host_pid" 2>/dev/null
}
trap cleanup EXIT
trap 'exit 143' INT TERM

ready=0
for _ in $(seq 1 100); do
    if ! kill -0 "$host_pid" 2>/dev/null; then
        echo "start-anyps5-os: host.py exited; see $data/host.log" >&2
        exit 1
    fi
    if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then ready=1; break; fi
    sleep 0.1
done
if [ "$ready" != 1 ]; then
    echo "start-anyps5-os: host.py did not start listening on port $port" >&2
    exit 1
fi

browser=""
for b in chromium chromium-browser google-chrome-stable google-chrome; do
    if command -v "$b" >/dev/null 2>&1; then browser="$b"; break; fi
done
if [ -z "$browser" ]; then
    echo "start-anyps5-os: no Chromium found; the console is at $url (host keeps running)" >&2
    wait "$host_pid"
    exit $?
fi

profile="$data/browser"
mkdir -p "$profile"
flags=(--kiosk "--app=$url" --noerrdialogs --no-first-run --disable-session-crashed-bubble --disable-infobars
       --disable-translate --password-store=basic --autoplay-policy=no-user-gesture-required
       --ozone-platform-hint=auto "--user-data-dir=$profile")

if [ -z "${WAYLAND_DISPLAY:-}" ] && [ -z "${DISPLAY:-}" ] && command -v cage >/dev/null 2>&1; then
    cage -- "$browser" "${flags[@]}" &
else
    "$browser" "${flags[@]}" &
fi
browser_pid=$!
wait "$browser_pid"
status=$?
browser_pid=""
exit "$status"
