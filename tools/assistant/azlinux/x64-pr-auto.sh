#!/bin/bash
# Unattended PR runner for azlinux, run hourly by x64-pr-auto.timer (Persistent, so it also runs after a Spot restart).
# Modelled on the Chromebook's cb-pr-auto.sh. Lists open PRs of boykopovar/AnyPS5 whose head commit is < 2 days old,
# skips heads already tested, and runs x64_prtest.sh:
#   - relinker mode for PRs that only touch core/relinker/, tools/, cmake/ or the root CMakeLists.txt (up to 3 per run);
#   - full mode for other PRs that change code (not only docs/*.md), 1 per run (a full build takes long on 2 vCPUs).
# One line per PR goes to ~/x64-work/status.md. Nothing is posted or pushed.
set -u
W=$HOME/x64-work; REPO=$HOME/AnyPS5; BIN=$W/bin; A=$W/auto; LOG=$W/logs/pr-auto.log
mkdir -p "$A" "$W/logs" "$W/prtests"
exec 9>"$W/.auto.lock"; flock -n 9 || exit 0
log() { echo "$(date '+%m-%d %H:%M:%S') $*" >> "$LOG"; }
cd "$REPO" || exit 1
git fetch -q origin || { log "git fetch failed"; exit 1; }
: > "$A/heads.txt"
for p in $(seq 1 30); do
  curl -fsS --max-time 60 "https://api.github.com/repos/boykopovar/AnyPS5/pulls?state=open&per_page=100&page=$p" -o "$A/page.json" || { log "GitHub API page $p failed"; exit 1; }
  c=$(python3 -I - "$A/page.json" "$A/heads.txt" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
with open(sys.argv[2], "a") as h:
    for x in d:
        h.write(f'{x["number"]} {x["head"]["sha"]}\n')
print(len(d))
PY
)
  [ "${c:-0}" -lt 100 ] && break
done
while read -r n sha; do git cat-file -e "$sha" 2>/dev/null || echo "+refs/pull/$n/head:refs/cand/$n"; done < "$A/heads.txt" > "$A/refspecs.txt"
[ -s "$A/refspecs.txt" ] && xargs -n 200 git fetch -q origin < "$A/refspecs.txt" 2>/dev/null
cut=$(date -d '2 days ago' +%s)
relinker=(); full=()
while read -r n sha; do
  t=$(git log -1 --format=%ct "$sha" 2>/dev/null) && [ "$t" -ge "$cut" ] || continue
  short=${sha:0:10}
  grep -qs "^head $short " "$W/prtests/$n.md" && continue
  grep -qs "#$n ($short" "$W/status.md" && continue
  files=$(git log --format= --name-only --no-merges "origin/main..$sha" | sort -u)
  [ -z "$files" ] && continue
  if ! echo "$files" | grep -qvE '^(core/relinker/|tools/|cmake/)|^CMakeLists\.txt$'; then relinker+=("$n")
  elif echo "$files" | grep -qvE '\.md$|^docs/|^\.github/'; then full+=("$n"); fi
done < "$A/heads.txt"
log "scan: $(wc -l < "$A/heads.txt") open; relinker ${relinker[*]:-none}; full ${full[*]:-none}"
[ ${#relinker[@]} -gt 0 ] && MODE=relinker bash "$BIN/x64_prtest.sh" "${relinker[@]:0:3}" >> "$W/logs/prtest-run.log" 2>&1 < /dev/null
[ ${#full[@]} -gt 0 ] && MODE=full bash "$BIN/x64_prtest.sh" "${full[0]}" >> "$W/logs/prtest-run.log" 2>&1 < /dev/null
exit 0
