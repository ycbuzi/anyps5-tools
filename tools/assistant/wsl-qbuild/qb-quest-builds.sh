#!/bin/bash
# WSL Quest builds (11-16, port of the Chromebook's ~/cb-work/bin/cb-quest-builds.sh; same recipe and output).
#   qb-quest-builds.sh pending   prints how many requests still need a build or a send (cheap, no fetch, no build)
#   qb-quest-builds.sh           builds and sends every pending request. Started only by qb-driver.ps1 on Windows,
#                                which holds the PC lock (gpu_flag.ps1 Use-Pc) for the whole run.
# Requests:
#   http://192.0.2.1:9072/requests.txt   lines: "<PR number> <head sha>" (PR in boykopovar/AnyPS5)
#   /root/qb/quest-requests.txt             lines: "<sha> [<git url> <ref>] [<folder>]"
# For each SHA not yet in /root/qb/quest-sent.txt: build relinker + PRX archive (Release, gcc, ccache, BUILD_TESTING=OFF,
# plain x86-64 baseline: no AVX, like the Chromebook, so box64 on the Quest runs it) INSIDE the Debian 13 chroot
# /root/qb-deb13 (qb-deb13.sh; Arch's gcc 16/glibc 2.44 output needs GLIBCXX_3.4.35 that the Quest lacks), then send ONE tar stream
# "<folder>/{relinker-v<sha11>,prx-linux-v<sha11>.tar.gz}" to 192.0.2.1:9071. The package stays in
# /root/qb/quest-dist/<folder> (newest 4 kept) for the ARM lane relink. Log: /root/qb/logs/quest-builds.log.
set -u
QB=/root/qb; REPO=$QB/AnyPS5; B=$QB/build-q13; DIST=$QB/quest-dist; IN="$QB/bin/qb-deb13.sh run"
LOG=$QB/logs/quest-builds.log; SENT=$QB/quest-sent.txt; LOCAL=$QB/quest-requests.txt; STATE=$QB/state.txt
QUEST=192.0.2.1; REQ_URL=http://$QUEST:9072/requests.txt; KEEP=4; JOBS=${QB_JOBS:-12}
ORIGIN=https://github.com/boykopovar/AnyPS5; FORK=https://github.com/ycbuzi/AnyPS5
export CCACHE_DIR=$QB/ccache13
unset CFLAGS CXXFLAGS CPPFLAGS LDFLAGS
mkdir -p "$QB/logs" "$DIST"; touch "$SENT" "$LOG"
log() { echo "$(date '+%m-%d %H:%M:%S') $*" >> "$LOG"; }
ok_sha() { [[ $1 =~ ^[0-9a-f]{7,40}$ ]]; }

collect() {
  if remote=$(curl -fsS --max-time 10 "$REQ_URL" 2>/dev/null); then
    while read -r line; do
      if [[ $line =~ PR\ \#([0-9]{1,6})\ ([0-9a-f]{7,40}) ]] || [[ $line =~ ^([0-9]{1,6})[[:space:]]+([0-9a-f]{7,40}) ]]; then
        pr=${BASH_REMATCH[1]}; sha=${BASH_REMATCH[2]}
        printf '%s\t%s\t%s\t%s\n' "$sha" "$ORIGIN" "refs/pull/$pr/head" "$pr-${sha:0:11}"
      fi
    done <<< "$remote"
  fi
  if [ -f "$LOCAL" ]; then
    while read -r sha url ref folder; do
      [[ ${sha:-} == \#* ]] && continue; ok_sha "${sha:-}" || continue
      printf '%s\t%s\t%s\t%s\n' "$sha" "${url:-}" "${ref:-}" "${folder:-${sha:0:11}}"
    done < "$LOCAL"
  fi
}
open_req() { ! grep -q "^${1:0:11}" "$SENT" && [ "$(grep -cE "(FAIL|SKIP) ${1:0:11}" "$LOG")" -lt 3 ]; }

if [ "${1:-}" = pending ]; then
  n=0; while IFS=$'\t' read -r want _; do open_req "$want" && n=$((n + 1)); done < <(collect | sort -u)
  echo "$n"; exit 0
fi

exec 9>"$QB/.build.lock"; flock -n 9 || { echo "another qb-quest-builds run holds $QB/.build.lock"; exit 0; }
trap 'echo "idle (last run ended $(date "+%m-%d %H:%M"))" > "$STATE"' EXIT
$IN ccache -M 10G > /dev/null 2>&1; $IN ccache -c > /dev/null 2>&1
find "$QB/logs" -name 'quest-*.log' ! -name quest-builds.log -mtime +14 -delete 2>/dev/null

checkout_clean() {
  git -C "$REPO" checkout -q --detach -f "$1" || return 1
  if ! git -C "$REPO" submodule update --init --recursive --filter=blob:none --jobs 4 >> "$QB/logs/quest-submodules.log" 2>&1; then
    git -C "$REPO" submodule status --recursive | awk '/^-/{print $2}' | grep -v 'test262' | while read -r p; do
      git -C "$REPO" submodule update --init --depth 1 -- "$p" >> "$QB/logs/quest-submodules.log" 2>&1; done
    git -C "$REPO" submodule update --init --recursive --jobs 4 >> "$QB/logs/quest-submodules.log" 2>&1 || return 1
  fi
  git -C "$REPO" submodule foreach -q --recursive 'git status --porcelain --untracked-files=no | grep -q . && git reset -q --hard HEAD; true'
  [ -z "$(git -C "$REPO" status --porcelain --untracked-files=no)" ]
}

compat_check() {
  local t need; t=$(mktemp -d); tar -C "$t" -xzf "$DIST/$1"/prx-linux-*.tar.gz || { rm -rf "$t"; echo "archive unreadable"; return 1; }
  need=$(find "$t" -type f \( -name '*.prx' -o -name '*.so*' \) -exec objdump -T {} + 2>/dev/null | grep -oE 'GLIBCXX_3\.4\.[0-9]+|GLIBC_2\.[0-9]+' \
    | sort -uV | awk -F'[_.]' '($1=="GLIBCXX" && $4>33) || ($1=="GLIBC" && $3>41)' | tr '\n' ' ')
  rm -rf "$t"; [ -z "$need" ] || { echo "needs symbols newer than the Quest's Debian 13 (glibc 2.41, GLIBCXX_3.4.33): $need"; return 1; }
}

build_one() {
  local sha=$1 folder=$2 v="v${1:0:11}" t0=$(date +%s)
  checkout_clean "$sha" || { echo "checkout/submodules failed"; return 1; }
  $IN true > /dev/null || { echo "Debian 13 chroot missing (run qb-deb13.sh setup)"; return 1; }
  [ -f "$B/build.ninja" ] || $IN cmake -S "$REPO" -B "$B" -G Ninja -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF \
      -DCMAKE_C_COMPILER=gcc -DCMAKE_CXX_COMPILER=g++ -DCMAKE_C_COMPILER_LAUNCHER=ccache -DCMAKE_CXX_COMPILER_LAUNCHER=ccache \
      "-DCMAKE_C_FLAGS=-march=x86-64 -mtune=generic" "-DCMAKE_CXX_FLAGS=-march=x86-64 -mtune=generic" \
      > "$QB/logs/quest-$folder-configure.log" 2>&1 || { echo "configure failed"; return 1; }
  nice -n 15 $IN ninja -C "$B" -j"$JOBS" relinker > "$QB/logs/quest-$folder-relinker.log" 2>&1 || { echo "relinker build failed"; return 1; }
  nice -n 15 $IN ninja -C "$B" -j"$JOBS" libs > "$QB/logs/quest-$folder-libs.log" 2>&1 || { echo "libs build failed ($(grep -m1 FAILED "$QB/logs/quest-$folder-libs.log" | cut -c1-120))"; return 1; }
  rm -rf "$DIST/.pkg" "$DIST/$folder"; mkdir -p "$DIST/$folder"
  $IN python3 "$REPO/tools/package_release.py" --platform linux --build "$B" --output "$DIST/.pkg" --version "$v" \
      > "$QB/logs/quest-$folder-package.log" 2>&1 || { echo "package failed"; return 1; }
  cp "$B/core/relinker/relinker" "$DIST/$folder/relinker-$v" && mv "$DIST/.pkg/prx-linux-$v.tar.gz" "$DIST/$folder/" && rm -rf "$DIST/.pkg"
  compat_check "$folder" || return 1
  echo "built in $(( $(date +%s) - t0 ))s (Debian 13 chroot, Quest-compatible symbols)"
}

send_tar() {
  python3 -I - "$DIST" "$QUEST" "$@" <<'PY'
import socket, subprocess, sys
dist, host, folders = sys.argv[1], sys.argv[2], sys.argv[3:]
tar = subprocess.Popen(["tar", "-C", dist, "--exclude=*.log", "-cf", "-", *folders], stdout=subprocess.PIPE)
sent = 0
with socket.create_connection((host, 9071), timeout=30) as s:
    while chunk := tar.stdout.read(1 << 20):
        s.sendall(chunk); sent += len(chunk)
    s.shutdown(socket.SHUT_WR)
sys.exit(f"tar rc {tar.wait()}") if tar.wait() else print(sent)
PY
}

reqs=$(mktemp); trap 'rm -f "$reqs"; echo "idle (last run ended $(date "+%m-%d %H:%M"))" > "$STATE"' EXIT
collect | awk -F'\t' '!seen[$1]++' > "$reqs"
while IFS=$'\t' read -r want url ref folder; do
  open_req "$want" || continue
  echo "building $folder since $(date '+%m-%d %H:%M')" > "$STATE"
  full=""
  if [ -f "$DIST/$folder/relinker-v${want:0:11}" ] && [ -f "$DIST/$folder/prx-linux-v${want:0:11}.tar.gz" ]; then
    full=$want
  elif [ -n "$url" ] && [ -n "$ref" ]; then
    git -C "$REPO" fetch -q "$url" "+$ref:refs/qreq/${want:0:11}" 2>/dev/null && full=$(git -C "$REPO" rev-parse "refs/qreq/${want:0:11}")
    if [[ $full != $want* ]] && [ ${#want} -eq 40 ]; then
      git -C "$REPO" fetch -q "$url" "+$want:refs/qreq/${want:0:11}" 2>/dev/null && full=$(git -C "$REPO" rev-parse "refs/qreq/${want:0:11}")
    fi
    if [[ $full != $want* ]]; then
      g=$(git -C "$REPO" rev-parse -q --verify "$want^{commit}" 2>/dev/null) && full=$g
    fi
  else
    for u in "$ORIGIN" "$FORK"; do git -C "$REPO" fetch -q "$u" "$want" 2>/dev/null && { full=$(git -C "$REPO" rev-parse FETCH_HEAD); break; }; done
    [ -z "$full" ] && full=$(git -C "$REPO" rev-parse -q --verify "$want^{commit}" 2>/dev/null)
  fi
  if [ -z "$full" ] || [[ $full != $want* ]]; then log "SKIP ${want:0:11} ($folder): cannot fetch, or the ref no longer points at it (got ${full:0:11})"; continue; fi
  df_gb=$(df -BG --output=avail /root | tail -1 | tr -dc 0-9); [ "$df_gb" -lt 15 ] && { log "STOP: only ${df_gb}G free (need 15G)"; break; }
  if [ -f "$DIST/$folder/relinker-v${full:0:11}" ] && [ -f "$DIST/$folder/prx-linux-v${full:0:11}.tar.gz" ]; then
    res="already built"
  elif ! res=$(build_one "$full" "$folder"); then log "FAIL ${full:0:11} ($folder): $res"; continue; fi
  sizes=$(cd "$DIST/$folder" && stat -c '%n=%s' relinker-v* prx-linux-v* | tr '\n' ' ')
  echo "sending $folder to the Quest since $(date '+%m-%d %H:%M')" > "$STATE"
  if bytes=$(send_tar "$folder" 2>&1); then
    echo "${full:0:11} $folder $(date +%F_%T) $bytes" >> "$SENT"; log "SENT ${full:0:11} -> $QUEST:9071 $folder/ ($bytes bytes tar) $res; $sizes"
  else
    log "BUILT-NOT-SENT ${full:0:11} ($folder): $res; send failed: $(echo "$bytes" | tail -1); will retry next run"
  fi
  ls -1dt "$DIST"/*/ 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -rf
done < "$reqs"
