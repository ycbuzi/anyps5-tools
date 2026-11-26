#!/bin/bash
# Rebuild the Chromebook toolchain's runtime libraries after source changes: cb-libs.sh <files relative to the repo...>
# Copies the files from /mnt/c/AnyPS5-work/wt-cbint into /root/aos-toolchain/src, builds everything, and copies each
# rebuilt library, after its NID patch step (build/core/libs/libs, never libs/unpatched), into the toolchain. Prints the list,
# which is also saved in /tmp/cb-libs-changed.txt for the deploy step.
set -u
WT=/mnt/c/AnyPS5-work/wt-cbint
SRC=/root/aos-toolchain/src
B=/root/aos-toolchain/build
OPT=/root/aos-toolchain/opt/anyps5/libs
for f in "$@"; do cp "$WT/$f" "$SRC/$f" || exit 1; done
touch /tmp/cb-libs-marker
sleep 1
cd "$B" || exit 1
nice -n 10 ninja -j6 2>&1 | grep -E "error|FAILED|ninja: " | cut -c1-240 | tail -8
echo "ninja rc=${PIPESTATUS[0]}"
: > /tmp/cb-libs-changed.txt
P="$B/core/libs/libs"
for raw in $(find "$P/unpatched" -maxdepth 1 -type f -newer /tmp/cb-libs-marker -name "*.prx"); do
    name=$(basename "$raw")
    nice -n 10 ninja -j6 "core/libs/libs/$name" 2>&1 | grep -E "error|FAILED" | cut -c1-200
    if [ -f "$OPT/$name" ] && [ -f "$P/$name" ] && ! cmp -s "$P/$name" "$OPT/$name"; then
        cp "$P/$name" "$OPT/$name" && echo "$name" >> /tmp/cb-libs-changed.txt
    fi
done
sort -u -o /tmp/cb-libs-changed.txt /tmp/cb-libs-changed.txt
echo "libraries updated in the toolchain: $(wc -l < /tmp/cb-libs-changed.txt)"
head -20 /tmp/cb-libs-changed.txt
