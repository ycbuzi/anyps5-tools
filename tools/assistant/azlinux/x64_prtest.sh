#!/bin/bash
# Azure anyps5-x64 (Spot D2as_v4, AMD EPYC 7763: AVX2, no AVX-512, Ubuntu 24.04) offline PR test runner (11-08).
# Modelled on mac_prtest.sh / chromebook_prtest.sh. For each PR: merge it into current origin/main, build, run the
# ctest suite twice and compare with main's failures on this machine. MODE=relinker (default) builds the
# relinker-only tree; MODE=full builds everything with gcc + ccache like CI and runs ctest with lavapipe.
# Writes ~/x64-work/prtests/<N>.md with a draft comment. Nothing is pushed or posted from here.
# usage: [MODE=relinker|full] bash x64_prtest.sh <PR number>...
set -u
renice -n 15 $$ >/dev/null 2>&1
MODE=${MODE:-relinker}
W=$HOME/x64-work; repo=$HOME/AnyPS5; build=$repo/build-prtest-$MODE; logs=$W/logs; out=$W/prtests; status=$W/status.md
mkdir -p "$logs" "$out"
exec 8>"$W/.prtest.lock"
flock -n 8 || { echo "busy"; exit 3; }
export CCACHE_DIR=$HOME/.ccache CCACHE_MAXSIZE=15G
export ANYPS5_REQUIRE_VULKAN=1 VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/lvp_icd.x86_64.json
echo "$MODE $*" > "$W/current"
trap 'rm -f "$W/current"' EXIT
say() { echo "$(date '+%m-%d %H:%M:%S') [$MODE] $*" | tee -a "$status"; }
failures() { sed -n '/^The following tests FAILED:/,/^[^[:space:]]/p' "$1" | grep -E '^[[:space:]]+[0-9]+ - ' | sed -E 's/^[[:space:]]+[0-9]+ - //; s/ \(.*$//' | sort -u; }
summary() { grep -E 'tests passed' "$1" | tail -1; }
configure() {
    local extra="-DANYPS5_RELINKER_ONLY=ON"
    if [ "$MODE" = full ]; then
        extra="-DCMAKE_C_COMPILER=gcc -DCMAKE_CXX_COMPILER=g++"
        git -C "$repo" submodule update -q --init --recursive --jobs 4 > "$logs/submodules.log" 2>&1
    fi
    cmake -S "$repo" -B "$build" -G Ninja -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=ON \
        -DCMAKE_C_COMPILER_LAUNCHER=ccache -DCMAKE_CXX_COMPILER_LAUNCHER=ccache $extra > "$logs/configure-$MODE.log" 2>&1
}
build_all() {
    ninja -C "$build" -j2 -k 0 > "$1" 2>&1; local rc=$?
    if [ "$MODE" = full ]; then ninja -C "$build" -j2 -k 0 libs >> "$1" 2>&1 || rc=$?; fi
    return $rc
}
run_ctest() { ctest --test-dir "$build" -j2 --timeout 300 > "$1" 2>&1; }
cd "$repo" || exit 1
git fetch -q origin
main=$(git rev-parse --short=10 origin/main)
base=$W/main-$main-$MODE.failures
if [ ! -f "$base" ]; then
    say "main $main: baseline build and test"
    git checkout -q -f --detach origin/main && configure
    t0=$(date +%s)
    build_all "$logs/main-$main-$MODE-build.log"
    run_ctest "$logs/main-$main-$MODE-ctest.log"
    failures "$logs/main-$main-$MODE-ctest.log" > "$base"
    say "main $main: built+tested in $(( $(date +%s) - t0 ))s; $(summary "$logs/main-$main-$MODE-ctest.log"); failing: $(tr '\n' ' ' < "$base")"
fi
if [ "$MODE" = relinker ] && [ -f "$W/matrix/combos.txt" ]; then
    git checkout -q -f --detach origin/main
    bash "$W/bin/matrix.sh" baseline "$main" >> "$logs/matrix-run.log" 2>&1
fi
for n in "$@"; do
    n=${n#\#}
    git checkout -q -f --detach origin/main
    if ! git fetch -q -f origin "pull/$n/head:refs/pr/$n"; then say "#$n: fetch failed"; continue; fi
    head=$(git rev-parse --short=10 "refs/pr/$n")
    if ! git -c user.name=azlinux -c user.email=azlinux@localhost merge -q --no-edit "refs/pr/$n" > "$logs/pr-$n-merge.log" 2>&1; then
        git merge --abort 2>/dev/null
        say "#$n ($head): does not merge cleanly into main $main, skipped"
        printf '# #%s\nhead %s does not merge cleanly into main %s; not tested.\n' "$n" "$head" "$main" > "$out/$n.md"
        continue
    fi
    merged=$(git rev-parse --short=10 HEAD)
    configure
    t0=$(date +%s)
    build_all "$logs/pr-$n-$MODE-build.log"; brc=$?
    bsec=$(( $(date +%s) - t0 ))
    : > "$logs/pr-$n-$MODE-failures"
    runs=""
    for i in 1 2; do
        run_ctest "$logs/pr-$n-$MODE-ctest-$i.log"
        failures "$logs/pr-$n-$MODE-ctest-$i.log" >> "$logs/pr-$n-$MODE-failures"
        runs="$runs; run $i: $(summary "$logs/pr-$n-$MODE-ctest-$i.log")"
    done
    sort -u -o "$logs/pr-$n-$MODE-failures" "$logs/pr-$n-$MODE-failures"
    mx=""
    if [ "$MODE" = relinker ] && [ -f "$W/matrix/combos.txt" ]; then
        bash "$W/bin/matrix.sh" pr "$n" "$main" > "$logs/pr-$n-matrix.md" 2>> "$logs/matrix-run.log"
        mtot=$(grep -c '^|' "$logs/pr-$n-matrix.md"); mok=$(grep -c '| ok |' "$logs/pr-$n-matrix.md")
        mnew=$(grep '^|' "$logs/pr-$n-matrix.md" | awk -F'|' '$5 !~ /^ none/ {split($2,a," "); printf "%s/%s ", a[1], $3}' | sed -E 's/  +/ /g')
        mx="matrix: $mok of $mtot image/compiler builds ok; new failures in: ${mnew:-none}"
    fi
    new=$(comm -23 "$logs/pr-$n-$MODE-failures" "$base" | tr '\n' ' ')
    same=$(comm -12 "$logs/pr-$n-$MODE-failures" "$base" | tr '\n' ' ')
    linkerrors=$(grep -c "FAILED:" "$logs/pr-$n-$MODE-build.log")
    mainlink=$(grep -c "FAILED:" "$logs/main-$main-$MODE-build.log" 2>/dev/null); mainlink=${mainlink:-0}
    say "#$n ($head merged as $merged): build rc $brc in ${bsec}s, $linkerrors failed targets (main $mainlink)${runs}; new failures: ${new:-none}"
    {
        echo "# #$n"
        echo "head $head merged into main $main as $merged; $MODE build rc $brc in ${bsec}s; failed build targets $linkerrors (main $mainlink)${runs}"
        echo "failing on main too: ${same:-none}"
        echo "new failures: ${new:-none}"
        for t in $new; do echo "## $t"; grep -h -A12 -E "^ *[0-9]+/[0-9]+ Test +#[0-9]+: $t " "$logs/pr-$n-$MODE-ctest-1.log" | head -14; done
        if [ "$linkerrors" -gt "$mainlink" ]; then echo "## build"; grep -h -B2 -A8 "FAILED:" "$logs/pr-$n-$MODE-build.log" | head -40; fi
        if [ -n "$mx" ]; then
            echo "$mx"; echo; echo "## Linux matrix (containers, relinker-only, one ctest run each; new failures vs main on the same image)"
            echo "| image | compiler | build | new failures vs main | time |"; echo "|---|---|---|---|---|"; cat "$logs/pr-$n-matrix.md"
        fi
        echo
        echo "## Draft comment"
        what="relinker-only build"; [ "$MODE" = full ] && what="full build (GCC $(gcc -dumpfullversion), ccache, Vulkan tests on lavapipe)"
        echo "Tested on Ubuntu 24.04 x86-64 (AMD EPYC 7763, 2 vCPUs: AVX2 but no AVX-512), $what; PR head \`$head\` merged into main \`$main\` as \`$merged\`."
        if [ "$linkerrors" -gt "$mainlink" ]; then echo "The build has more failed targets than main here; first errors are below."
        elif [ -z "$new" ]; then echo "The ctest suite ran twice (${runs#; }) with no failure that main does not also have here${same:+ (\`$(echo $same | sed 's/ /\`, \`/g')\`)}."
        else echo "New failures compared with main on this machine: \`$(echo $new | sed 's/ /\`, \`/g')\` (details below)."; fi
        [ -n "$mx" ] && echo "Also built and tested in containers (Ubuntu 22.04/24.04, Debian 12/13, Fedora, Arch, openSUSE Tumbleweed, Alpine; GCC and Clang): ${mx#matrix: }."
        echo "AI-assisted: yes (Claude Code)"
    } > "$out/$n.md"
done
git checkout -q -f --detach origin/main
say "done: $*"
