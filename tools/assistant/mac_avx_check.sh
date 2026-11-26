#!/bin/bash
# Offline check for the --to-avx relinker work (11-08), run on the MacBook (i5-2415M: AVX, no AVX2), no Claude needed.
# Builds a branch of ycbuzi/AnyPS5 in ~/AnyPS5-avx (never ~/AnyPS5, which mac_prtest.sh uses), runs every lowering
# test (their execution parts really run the stubs on this CPU), converts Celeste Classic's eboot with
# --macos --to-avx and counts the AVX2 instructions left with Capstone. Report: ~/mac-work/avx-check.md.
# usage: bash mac_avx_check.sh [branch]   (default feat/relinker-avx2-widen-broadcast)
set -u
source ~/tools/llvm-env.sh
branch=${1:-feat/relinker-avx2-widen-broadcast}
wt=~/AnyPS5-avx; out=~/mac-work/avx-check.md; logs=~/mac-work/logs; mkdir -p "$logs"
cd ~/AnyPS5 || exit 1
[ -d "$wt" ] || git worktree add -q --detach "$wt" origin/main
cd "$wt" || exit 1
git fetch -q https://github.com/ycbuzi/AnyPS5.git "$branch" && git checkout -q -f --detach FETCH_HEAD || { echo "fetch of $branch failed" > "$out"; exit 1; }
head=$(git rev-parse --short=11 HEAD)
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release -DANYPS5_RELINKER_ONLY=ON -DBUILD_TESTING=ON > "$logs/avx-configure.log" 2>&1
tests=$(ls core/relinker/codegen/tests/*LoweringTests.cpp | sed -E 's#.*/([A-Za-z0-9]+)LoweringTests.cpp#\1#' | sed -E 's/([a-z0-9])([A-Z])/\1_\2/g' | tr 'A-Z' 'a-z' | sed 's/$/_lowering_tests/')
nice -n 5 cmake --build build -j3 --target relinker $tests amd64_only_converter_tests cli_args_tests > "$logs/avx-build.log" 2>&1
{
    echo "# --to-avx check, $branch at $head ($(date '+%m-%d %H:%M'))"
    echo
    echo "## Lowering tests (execution parts run on this AVX-only CPU)"
    for t in $tests cli_args_tests; do
        if [ -x "build/tests/$t" ]; then r=$("build/tests/$t" 2>&1 | tail -1); echo "- $t: $r"; else echo "- $t: NOT BUILT"; fi
    done
    echo "- amd64_only_converter_tests: skipped here (runs host AVX2 code, #16236)"
    echo
    echo "## Celeste Classic conversion"
    rm -rf ~/scan/out-check && mkdir -p ~/scan/out-check
    if build/core/relinker/relinker --macos --to-avx --skip-sce-module ~/scan/celeste-eboot.elf ~/scan/out-check/celeste > "$logs/avx-convert.log" 2>&1; then
        echo "- converted: $(stat -f %z ~/scan/out-check/celeste) bytes"
        python3 ~/scan/ymm_left.py ~/scan/out-check/celeste | sed 's/^/- /'
    else
        echo "- conversion FAILED: $(tr '\r' '\n' < "$logs/avx-convert.log" | grep -E 'FAIL|rror' | head -2)"
    fi
} > "$out" 2>&1
cat "$out"
