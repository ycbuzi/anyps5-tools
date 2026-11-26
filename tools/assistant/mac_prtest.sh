#!/bin/bash
# macOS (Ventura 13.7.8 via OCLP, MacBook Pro 8,1 i5-2415M: AVX, no AVX2/BMI/F16C) offline test runner for open AnyPS5
# PRs (11-08, replaces the Claude-run job 7 so no usage is spent). For each PR: merge it into current origin/main,
# build the relinker-only tree with LLVM 19, run the whole ctest suite twice, and compare with main's failures.
# Writes ~/mac-work/prtests/<N>.md with a draft comment; the PC Claude reviews it before anything is posted.
# Nothing is pushed or posted from here.
# usage: bash mac_prtest.sh <PR number>...
set -u
# 11-08: Blu uses the Mac through Screen Sharing; lowest priority and two compile jobs keep the desktop responsive.
renice -n 19 $$ >/dev/null 2>&1
# 11-11: unplugged, the Mac idle-slept at 15:47 halfway through a batch and stayed asleep overnight (pmset log). The
# batch keeps it awake (no admin rights needed); the assertion ends with this script.
caffeinate -i -w $$ >/dev/null 2>&1 &
source ~/tools/llvm-env.sh
repo=~/AnyPS5; build=$repo/build-prtest; logs=~/mac-work/logs; out=~/mac-work/prtests; status=~/mac-work/status.md
mkdir -p "$logs" "$out"
lock=~/mac-work/prtest.lock
if ! mkdir "$lock" 2>/dev/null; then echo "busy"; exit 3; fi
trap 'rmdir "$lock"' EXIT
say() { echo "$(date '+%m-%d %H:%M:%S') $*" | tee -a "$status"; }
failures() { grep -E '^[[:space:]]+[0-9]+ - ' "$1" | sed -E 's/^[[:space:]]+[0-9]+ - //' | sort -u; }
summary() { grep -E 'tests passed' "$1" | tail -1; }
configure() { cmake -S "$repo" -B "$build" -G Ninja -DCMAKE_BUILD_TYPE=Release -DANYPS5_RELINKER_ONLY=ON -DBUILD_TESTING=ON > "$logs/configure.log" 2>&1; }
cd "$repo" || exit 1
git fetch -q origin
main=$(git rev-parse --short=10 origin/main)
base=~/mac-work/main-$main.failures
if [ ! -f "$base" ]; then
    say "main $main: baseline build and test"
    git checkout -q -f --detach origin/main && configure
    ninja -C "$build" -j2 -k 0 > "$logs/main-$main-build.log" 2>&1
    ctest --test-dir "$build" -j2 --timeout 300 > "$logs/main-$main-ctest.log" 2>&1
    failures "$logs/main-$main-ctest.log" > "$base"
    say "main $main: $(summary "$logs/main-$main-ctest.log"); failing: $(tr '\n' ' ' < "$base")"
fi
for n in "$@"; do
    n=${n#\#}
    git checkout -q -f --detach origin/main
    if ! git fetch -q -f origin "pull/$n/head:refs/pr/$n"; then say "#$n: fetch failed"; continue; fi
    head=$(git rev-parse --short=10 "refs/pr/$n")
    if ! git -c user.name=macbook -c user.email=macbook@localhost merge -q --no-edit "refs/pr/$n" > "$logs/pr-$n-merge.log" 2>&1; then
        git merge --abort 2>/dev/null
        say "#$n ($head): does not merge cleanly into main $main, skipped"
        printf '# #%s\nhead %s does not merge cleanly into main %s; not tested.\n' "$n" "$head" "$main" > "$out/$n.md"
        continue
    fi
    merged=$(git rev-parse --short=10 HEAD)
    configure
    t0=$(date +%s)
    ninja -C "$build" -j2 -k 0 > "$logs/pr-$n-build.log" 2>&1; brc=$?
    bsec=$(( $(date +%s) - t0 ))
    : > "$logs/pr-$n-failures"
    runs=""
    for i in 1 2; do
        ctest --test-dir "$build" -j2 --timeout 300 > "$logs/pr-$n-ctest-$i.log" 2>&1
        failures "$logs/pr-$n-ctest-$i.log" >> "$logs/pr-$n-failures"
        runs="$runs; run $i: $(summary "$logs/pr-$n-ctest-$i.log")"
    done
    sort -u -o "$logs/pr-$n-failures" "$logs/pr-$n-failures"
    new=$(comm -23 "$logs/pr-$n-failures" "$base" | tr '\n' ' ')
    same=$(comm -12 "$logs/pr-$n-failures" "$base" | tr '\n' ' ')
    linkerrors=$(grep -c "FAILED:" "$logs/pr-$n-build.log")
    mainlink=$(grep -c "FAILED:" "$logs/main-$main-build.log" 2>/dev/null || echo 0)
    say "#$n ($head merged as $merged): build rc $brc in ${bsec}s, $linkerrors failed targets (main $mainlink)${runs}; new failures: ${new:-none}"
    {
        echo "# #$n"
        echo "head $head merged into main $main as $merged; build rc $brc in ${bsec}s; failed build targets $linkerrors (main $mainlink: std::pmr tests need macOS 14)${runs}"
        echo "failing on main too: ${same:-none}"
        echo "new failures: ${new:-none}"
        for t in $new; do echo "## $t"; grep -h -A12 -E "^[0-9]+/[0-9]+ Test +#[0-9]+: $t " "$logs/pr-$n-ctest-1.log" | head -14; done
        if [ "$linkerrors" -gt "$mainlink" ]; then echo "## build"; grep -h -B2 -A8 "FAILED:" "$logs/pr-$n-build.log" | head -40; fi
        echo
        echo "## Draft comment"
        echo "Tested on macOS 13.7.8 (Intel MacBook Pro 8,1, i5-2415M: AVX but no AVX2, BMI, LZCNT, MOVBE or F16C), LLVM $(clang --version | head -1 | sed -E 's/.*version ([0-9.]+).*/\1/'), relinker-only build; PR head \`$head\` merged into main \`$main\` as \`$merged\`."
        if [ "$linkerrors" -gt "$mainlink" ]; then echo "The build has more failed targets than main here; first errors are below."
        elif [ -z "$new" ]; then echo "The full ctest suite ran twice${runs#;} with no failure that main does not also have here${same:+ (\`$(echo $same | sed 's/ /\`, \`/g')\`, which run host AVX2 code or need macOS 14)}."
        else echo "New failures compared with main on this machine: \`$(echo $new | sed 's/ /\`, \`/g')\` (details below)."; fi
        echo "AI-assisted: yes (Claude Code)"
    } > "$out/$n.md"
done
git checkout -q -f --detach origin/main
say "done: $*"
