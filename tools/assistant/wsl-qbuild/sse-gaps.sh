#!/bin/bash
# Which AVX instruction forms the --to-sse lowering still leaves in a game: sse-gaps.sh "<game folder under /mnt/...>" [relinker]
# Works on a scratch copy (the game folder is not written to). Prints the forms sorted by site count.
set -u
GAME="$1"; REL="${2:-/root/aos-toolchain/opt/anyps5/bin/relinker}"
W=/tmp/sse-gaps; rm -rf "$W"; mkdir -p "$W/app0"
cp "$GAME/app0/eboot.elf" "$W/app0/" || exit 1
[ -d "$GAME/app0/sce_module" ] && mkdir -p "$W/app0/sce_module" && find "$GAME/app0/sce_module" -maxdepth 1 -type f \( -name '*.prx' -o -name '*.sprx' \) ! -name '*.guest.prx' -exec cp {} "$W/app0/sce_module/" \;
"$REL" --to-sse --sce-module-path "$W/app0" "$W/app0/eboot.elf" "$W/out.elf" > "$W/relink.log" 2>&1
echo "relinker rc=$?"
grep -c "not lowered at" "$W/relink.log"
grep "not lowered at" "$W/relink.log" | sed -E 's/^Intel conversion: //' | awk '{n=$(NF-1); print n "\t" $0}' | sort -rn | cut -f2- | head -${TOP:-60}
grep -E "VEX instructions (not lowered|remain)" "$W/relink.log" | tail -3
rm -rf "$W/app0" "$W/out.elf"
