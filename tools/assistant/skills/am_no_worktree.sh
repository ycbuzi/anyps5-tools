#!/bin/bash
# Applies a patch series onto a base commit without any working tree (temporary index + commit-tree),
# keeping each patch's author, date and message. Prints the final commit and tree.
# usage: am_no_worktree.sh GIT_DIR BASE PATCH...
set -e
gd="$1"; base="$2"; shift 2
tmp=$(mktemp -d)
export GIT_DIR="$gd" GIT_INDEX_FILE="$tmp/index"
git read-tree "$base"
head=$(git rev-parse "$base")
for p in "$@"; do
  git mailinfo "$tmp/msg" "$tmp/diff" < "$p" > "$tmp/info"
  git apply --cached ${AM3WAY:+--3way} "$tmp/diff"
  tree=$(git write-tree)
  an=$(sed -n 's/^Author: //p' "$tmp/info"); ae=$(sed -n 's/^Email: //p' "$tmp/info")
  ad=$(sed -n 's/^Date: //p' "$tmp/info"); su=$(sed -n 's/^Subject: //p' "$tmp/info")
  { echo "$su"; if [ -s "$tmp/msg" ]; then echo; cat "$tmp/msg"; fi; } > "$tmp/full"
  head=$(GIT_AUTHOR_NAME="$an" GIT_AUTHOR_EMAIL="$ae" GIT_AUTHOR_DATE="$ad" git commit-tree "$tree" -p "$head" -F "$tmp/full")
done
echo "commit $head"
echo "tree $(git rev-parse "$head^{tree}")"
rm -rf "$tmp"
