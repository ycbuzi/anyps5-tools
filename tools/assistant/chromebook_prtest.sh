#!/bin/bash
# Linux (Debian 13, Pentium Silver N6000: SSE4.2, no AVX) test runner for open AnyPS5 PRs on Blu's Chromebook
# (11-06). For each PR: merge it into current origin/main, build the relinker-only tree with ccache, run the whole
# ctest suite 3 times, and compare with main's failures. Writes ~/cb-work/prtests/<N>.md with a draft comment; the PC
# Claude reviews it before anything is posted. Nothing is pushed or posted from here.
# usage: bash chromebook_prtest.sh <PR number>...
set -u
repo=~/AnyPS5; build=$repo/build-relinker; logs=~/cb-work/logs; out=~/cb-work/prtests; status=~/cb-work/prtest.status
mkdir -p "$logs" "$out"
say() { echo "$(date '+%m-%d %H:%M:%S') $*" | tee -a "$status"; }
failures() { grep -E '^[[:space:]]+[0-9]+ - ' "$1" | sed -E 's/^[[:space:]]+[0-9]+ - //' | sort -u; }
summary() { grep -E 'tests passed' "$1" | tail -1; }
configure() {

    cmake -S "$repo" -B "$build" -G Ninja -DCMAKE_BUILD_TYPE=Release -DANYPS5_RELINKER_ONLY=ON -DBUILD_TESTING=ON \
        -DCMAKE_CXX_COMPILER_LAUNCHER=ccache -DCMAKE_C_COMPILER_LAUNCHER=ccache > "$logs/configure.log" 2>&1
}
cd "$repo" || exit 1
git fetch -q origin
main=$(git rev-parse --short=10 origin/main)
base=~/cb-work/main-$main.failures
if [ ! -f "$base" ]; then
    say "main $main: baseline build and test"
    git checkout -q -f --detach origin/main && configure
    ninja -C "$build" -j3 -k 0 > "$logs/main-$main-build.log" 2>&1
    ctest --test-dir "$build" -j3 --timeout 300 > "$logs/main-$main-ctest.log" 2>&1
    failures "$logs/main-$main-ctest.log" > "$base"
    say "main $main: $(summary "$logs/main-$main-ctest.log"); failing: $(tr '\n' ' ' < "$base")"
fi
for n in "$@"; do
    git checkout -q -f --detach origin/main
    if ! git fetch -q origin "pull/$n/head:refs/pr/$n" 2>/dev/null && ! git fetch -q -f origin "pull/$n/head:refs/pr/$n"; then say "#$n: fetch failed"; continue; fi
    head=$(git rev-parse --short=10 "refs/pr/$n")
    if ! git -c user.name=chromebook -c user.email=chromebook@localhost merge -q --no-edit "refs/pr/$n" > "$logs/pr-$n-merge.log" 2>&1; then
        git merge --abort 2>/dev/null
        say "#$n ($head): does not merge cleanly into main $main, skipped"
        printf '# #%s\nhead %s does not merge cleanly into main %s; not tested.\n' "$n" "$head" "$main" > "$out/$n.md"
        continue
    fi
    merged=$(git rev-parse --short=10 HEAD)
    configure
    t0=$(date +%s)
    ninja -C "$build" -j3 -k 0 > "$logs/pr-$n-build.log" 2>&1; brc=$?
    bsec=$(( $(date +%s) - t0 ))
    : > "$logs/pr-$n-failures"
    runs=""
    for i in 1 2 3; do
        ctest --test-dir "$build" -j3 --timeout 300 > "$logs/pr-$n-ctest-$i.log" 2>&1
        failures "$logs/pr-$n-ctest-$i.log" >> "$logs/pr-$n-failures"
        runs="$runs; run $i: $(summary "$logs/pr-$n-ctest-$i.log")"
    done
    sort -u -o "$logs/pr-$n-failures" "$logs/pr-$n-failures"
    new=$(comm -23 "$logs/pr-$n-failures" "$base" | tr '\n' ' ')
    same=$(comm -12 "$logs/pr-$n-failures" "$base" | tr '\n' ' ')
    say "#$n ($head merged as $merged): build rc $brc in ${bsec}s${runs}; new failures: ${new:-none}"
    {
        echo "# #$n"
        echo "head $head merged into main $main as $merged; build rc $brc in ${bsec}s (ccache)${runs}"
        echo "failing on main too: ${same:-none}"
        echo "new failures: ${new:-none}"
        for t in $new; do echo "## $t"; grep -h -A12 -E "^[0-9]+/[0-9]+ Test +#[0-9]+: $t " "$logs/pr-$n-ctest-1.log" | head -14; done
        echo
        echo "## Draft comment"
        echo "Tested on Linux: Debian 13 in ChromeOS's Linux environment on a Pentium Silver N6000 (4 cores, SSE4.2, no AVX), GCC $(g++ -dumpfullversion), relinker-only build (\`-DANYPS5_RELINKER_ONLY=ON\`); PR head \`$head\` merged into main \`$main\` as \`$merged\`."
        if [ "$brc" != 0 ]; then echo "The build failed; first errors are in the attached log lines below."
        elif [ -z "$new" ]; then echo "The full ctest suite ran 3 times${runs#;} with no failure that main does not also have here${same:+ (\`$(echo $same | sed 's/ /\`, \`/g')\`, which execute AVX instructions this CPU lacks)}."
        else echo "New failures compared with main on this machine: \`$(echo $new | sed 's/ /\`, \`/g')\` (details below)."; fi
        echo "AI-assisted: yes (Claude Code)"
    } > "$out/$n.md"
done
git checkout -q -f --detach origin/main
say "done: $*"
