#!/bin/bash
# Compare the toolchain's shipped libraries with the build tree: cb-libs-diff.sh [symbol]
# Lists shipped libraries whose build-tree copy differs, and (with a symbol) which build libraries define it.
B=/root/aos-toolchain/build
OPT=/root/aos-toolchain/opt/anyps5/libs
n=0
for shipped in "$OPT"/*; do
    name=$(basename "$shipped")
    built=$(find "$B" -type f -name "$name" -not -path "*/opt/*" | head -1)
    [ -n "$built" ] || continue
    if ! cmp -s "$built" "$shipped"; then
        n=$((n + 1))
        echo "differs: $name (built $(date -r "$built" +%m-%d\ %H:%M), shipped $(date -r "$shipped" +%m-%d\ %H:%M))"
    fi
done | head -30
echo "shipped: $(ls "$OPT" | wc -l)"
if [ -n "${1:-}" ]; then
    for f in $(find "$B" -type f \( -name "*.prx" -o -name "*.so" \) -not -path "*/opt/*"); do
        nm -D --defined-only "$f" 2>/dev/null | grep -q " $1$" && echo "defines $1: $f ($(date -r "$f" +%m-%d\ %H:%M))"
    done
    for f in "$OPT"/*; do
        nm -D --defined-only "$f" 2>/dev/null | grep -q " $1$" && echo "shipped copy defines $1: $(basename "$f")"
    done
fi
