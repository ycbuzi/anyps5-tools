#!/bin/bash
# For the "set explicit ctest timeout" PRs (11-11, pushkarreddyy asked the community testers to confirm the TIMEOUT
# property and the run times of the tests each PR touches). For each "<PR>:<ctest regex>" argument: merge the PR into
# origin/main in ~/AnyPS5, build relinker-only with ccache, read each matching test's TIMEOUT from
# `ctest --show-only=json-v1`, run `ctest -R <regex> -V` three times and record each test's time. Main's TIMEOUT for the
# same tests is read first for comparison. Results: ~/cb-work/timeouts/<PR>.txt. Holds ~/cb-work/.heavy.lock (shared
# with the PR runner and the Quest builds). Nothing is posted from here.
# usage: bash chromebook_timeouts.sh 18445:'mingw_host_only_test_targets|build_changed_guest_tests' ...
set -u
repo=~/AnyPS5; build=$repo/build-relinker; out=~/cb-work/timeouts; mkdir -p "$out"
exec 9>~/cb-work/.heavy.lock; flock 9
cd "$repo" || exit 1
configure() {
    cmake -S "$repo" -B "$build" -G Ninja -DCMAKE_BUILD_TYPE=Release -DANYPS5_RELINKER_ONLY=ON -DBUILD_TESTING=ON \
        -DCMAKE_CXX_COMPILER_LAUNCHER=ccache -DCMAKE_C_COMPILER_LAUNCHER=ccache > "$out/configure.log" 2>&1
}
props() {
    ctest --test-dir "$build" --show-only=json-v1 -R "$1" 2>/dev/null | python3 -c '
import json, sys
for t in json.load(sys.stdin).get("tests", []):
    p = {x["name"]: x["value"] for x in t.get("properties", [])}
    print(f"  {t[\"name\"]}: TIMEOUT {p.get(\"TIMEOUT\", \"not set (ctest default)\")}")'
}
git fetch -q origin
main=$(git rev-parse --short=10 origin/main)
for arg in "$@"; do
    n=${arg%%:*}; rx=${arg#*:}; f="$out/$n.txt"
    git checkout -q -f --detach origin/main
    git fetch -q -f origin "pull/$n/head:refs/pr/$n" || { echo "fetch failed" > "$f"; continue; }
    head=$(git rev-parse --short=10 "refs/pr/$n")
    configure; ninja -C "$build" -j3 -k 0 > "$out/$n-main-build.log" 2>&1
    { echo "main $main: TIMEOUT of the matching tests"; props "$rx"; } > "$f"
    if ! git -c user.name=chromebook -c user.email=chromebook@localhost merge -q --no-edit "refs/pr/$n" > "$out/$n-merge.log" 2>&1; then
        git merge --abort 2>/dev/null; echo "head $head does not merge into main $main" >> "$f"; continue
    fi
    merged=$(git rev-parse --short=10 HEAD)
    configure; ninja -C "$build" -j3 -k 0 > "$out/$n-build.log" 2>&1; brc=$?
    { echo "PR head $head merged into main $main as $merged; build rc $brc"; echo "with the PR: TIMEOUT of the matching tests"; props "$rx"; } >> "$f"
    for run in 1 2 3; do
        ctest --test-dir "$build" -R "$rx" -V > "$out/$n-run$run.log" 2>&1
        echo "run $run: $(grep -E 'tests passed' "$out/$n-run$run.log" | tail -1)" >> "$f"
        grep -E '^[[:space:]]*[0-9]+/[0-9]+ Test +#' "$out/$n-run$run.log" | sed -E 's/^ */  /' >> "$f"
    done
    grep -E 'timeout computed to be' "$out/$n-run1.log" | sort | uniq -c | sed 's/^/  run 1 -V: /' >> "$f"
done
git checkout -q -f --detach origin/main
echo "$(date '+%m-%d %H:%M:%S') done: $*" >> "$out/status"
