#!/bin/bash
# Build and run the relinker's lowering tests from the chromebook worktree: cb-relinker-tests.sh [test targets...]
# Copies core/relinker from /mnt/c/AnyPS5-work/wt-cbint into /root/cbint/src (the tree with tests enabled) first.
set -u
WT=/mnt/c/AnyPS5-work/wt-cbint/core/relinker
SRC=/root/cbint/src/core/relinker
B=/root/cbint/build-relinker
TESTS="${*:-vex_legacy_lowering_tests upper_shadow_boundary_tests relinker_conversion_tests}"
cp -a "$SRC" /tmp/relinker-src-before-sync 2>/dev/null
rm -rf "$SRC" && cp -r "$WT" "$SRC" && rm -rf /tmp/relinker-src-before-sync || exit 1
cd "$B" || exit 1
ninja $TESTS 2>&1 | grep -E "error|FAILED|Linking" | cut -c1-260 | tail -12
echo "ninja rc=${PIPESTATUS[0]}"
for t in $TESTS; do
    f=$(find . -type f -name "$t" | head -1)
    [ -n "$f" ] || { echo "$t: not built"; continue; }
    timeout 1200 "$f" > "/tmp/$t.log" 2>&1
    echo "$t rc=$? $(tail -1 "/tmp/$t.log" | cut -c1-260)"
done
