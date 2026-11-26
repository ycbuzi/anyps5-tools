#!/bin/bash
# Rebuild the Chromebook toolchain's relinker from the chromebook worktree: cb-relinker.sh [files changed since the toolchain copy...]
# Copies the named files (paths relative to the repo) from /mnt/c/AnyPS5-work/wt-cbint into /root/aos-toolchain/src,
# (check first, from Git Bash, that the toolchain copy matches the worktree's HEAD for those files)
# builds the relinker and its lowering tests, and leaves the binary at /root/aos-toolchain/build (path printed).
set -u
WT=/mnt/c/AnyPS5-work/wt-cbint
SRC=/root/aos-toolchain/src
B=/root/aos-toolchain/build
for f in "$@"; do
    cp "$WT/$f" "$SRC/$f"
done
cd "$B" || exit 1
T=$(ninja -t targets all 2>/dev/null | grep -oE '^[^:]*(relinker|upper_shadow_boundary_tests|vex_legacy_lowering_tests)[^:/]*:' | tr -d ':' | grep -vE '\.(a|o|so)$|/' | sort -u | tr '\n' ' ')
echo "targets: $T"
ninja relinker $(ninja -t targets all 2>/dev/null | grep -o "^upper_shadow_boundary_tests") 2>&1 | grep -E "error|warning: unused|FAILED|Linking" | tail -12
echo "ninja rc=${PIPESTATUS[0]}"
find "$B" -maxdepth 4 -type f -name relinker -newer "$SRC/core/relinker/relinker/src/pipeline/Conversion.cpp" | head -2
find "$B" -maxdepth 4 -type f -name upper_shadow_boundary_tests | head -1 | while read t; do "$t" > /tmp/usb-test.log 2>&1; echo "upper_shadow_boundary_tests rc=$?"; tail -2 /tmp/usb-test.log; done
