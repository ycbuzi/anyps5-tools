#!/bin/bash
# Runs every game on the PC through the Chromebook's --to-sse relink (Blu 11-17: "fix it for all the games already, so
# when I go to download it, none of them fail"). Scratch copies only; the game folders are not written to.
# usage (WSL): sse-survey.sh [relinker] [games root ...]
# Output: /mnt/c/AnyPS5 Games/_reports/sse-survey.tsv  (game, rc, seconds, error, VEX sites left, top forms)
#         /mnt/c/AnyPS5 Games/_reports/sse-survey-forms.tsv  (form, sites, games) summed over all games
set -u
REL="${1:-/root/sse/sse-build/core/relinker/relinker}"; shift || true
ROOTS=("$@"); [ ${#ROOTS[@]} -eq 0 ] && ROOTS=("/mnt/d/AnyPS5 Games" "/mnt/g/AnyPS5 Games")
OUT="/mnt/c/AnyPS5 Games/_reports/sse-survey.tsv"; FORMS="/mnt/c/AnyPS5 Games/_reports/sse-survey-forms.tsv"
W=/tmp/sse-survey; ALL=$W/all-forms.txt
rm -rf "$W"; mkdir -p "$W"; : > "$ALL"
printf 'game\trc\tseconds\terror\tvex_left\ttop_forms\n' > "$OUT.tmp"
for root in "${ROOTS[@]}"; do
  for g in "$root"/*/; do
    a="$g/app0"; e="$a/eboot.elf"; [ -f "$e" ] || e="$a/eboot.bin"; [ -f "$e" ] || continue
    name=$(basename "$g")
    case "$name" in _*) continue;; esac
    rm -rf "$W/app0" "$W/out.elf"; mkdir -p "$W/app0/sce_module"
    cp "$e" "$W/app0/" || continue
    [ -d "$a/sce_module" ] && find "$a/sce_module" -maxdepth 1 -type f \( -name '*.prx' -o -name '*.sprx' \) ! -name '*.guest.prx' -exec cp {} "$W/app0/sce_module/" \;
    t0=$(date +%s)
    timeout 1800 nice -n 15 ionice -c3 "$REL" --to-sse --sce-module-path "$W/app0" "$W/app0/$(basename "$e")" "$W/out.elf" > "$W/relink.log" 2>&1
    rc=$?; secs=$(( $(date +%s) - t0 ))
    err=$(grep -m1 -E "^(ERROR|FAIL)|FAIL:|Exception|error:" "$W/relink.log" | tr '\t' ' ' | cut -c1-200)
    left=$(grep -E "not lowered at [0-9]+ sites" "$W/relink.log" | sed -E 's/.* at ([0-9]+) sites.*/\1/' | awk '{s+=$1} END {print s+0}')
    grep -E "not lowered at [0-9]+ sites" "$W/relink.log" | sed -E 's/^Intel conversion: //; s/ not lowered at ([0-9]+) sites.*/\t\1/' | awk -F'\t' -v g="$name" '{print $1 "\t" $2 "\t" g}' >> "$ALL"
    top=$(grep -E "not lowered at [0-9]+ sites" "$W/relink.log" | sed -E 's/^Intel conversion: //; s/ not lowered at ([0-9]+) sites.*/ \1/' | sort -k2 -t' ' -rn | awk '{print $NF ":" $0}' | sort -t: -k1 -rn | head -4 | cut -d: -f2- | tr '\n' ';')
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$name" "$rc" "$secs" "$err" "$left" "$top" >> "$OUT.tmp"
    cp "$OUT.tmp" "$OUT"
  done
done
awk -F'\t' '{s[$1]+=$2; if (!(($1 SUBSEP $3) in seen)) {seen[$1 SUBSEP $3]=1; n[$1]++}} END {for (f in s) print f "\t" s[f] "\t" n[f]}' "$ALL" | sort -t$'\t' -k3,3rn -k2,2rn > "$FORMS.tmp"
{ printf 'form\tsites\tgames\n'; cat "$FORMS.tmp"; } > "$FORMS"; rm -f "$FORMS.tmp"
mv "$OUT.tmp" "$OUT"; rm -rf "$W"
echo "done: $(($(wc -l < "$OUT") - 1)) games"
