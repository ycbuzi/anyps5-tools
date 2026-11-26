#!/bin/bash
# classify.sh LOG [rc] — one-line stop reason from a run-game.sh log:
#   NOTIMPL <function> (a "<function> not implemented" stub; other "... not implemented" texts are ABORT)  |  ABORT <what() text>  |  CRASH <signal> @<addr> in <lib/func>  |  FATAL/ERROR <text>
#   |  TIMEOUT (ran to the limit)  |  OK — always followed by "; frames: <flip lines> flips, captures <means>"
LOG=${1:?usage: classify.sh LOG [rc]}; rc=${2:-$(sed -n 's/^exit=//p' "$LOG" | tail -1)}
g() { sed -n '/^== run /,$p' "$LOG" | grep -av '^\[memory\] write watch unavailable'; }   # the run only; userfaultfd note is not a stop
c() { cut -c1-"${1:-150}" | tr -d '\033' | sed 's/\[31m//g; s/\[0m//g; s/^ *//'; }
r=""
if l=$(grep -am1 -E '^ERROR: ' "$LOG"); then r="SETUP $(echo "${l#ERROR: }" | c)"
elif l=$(g | grep -aim1 -E '(^|[^A-Za-z0-9_])[A-Za-z_][A-Za-z0-9_]{3,} not implemented *$'); then   # NotImplemented_nid_no_patch: "<function> not implemented"
  f=$(echo "$l" | grep -aoE "[A-Za-z_][A-Za-z0-9_]* not implemented" | tail -1 | sed -E "s/ not implemented//; s/(_nid_[a-z_0-9]+)+$//"); r="NOTIMPL $f: $(echo "$l" | c 120)"
elif l=$(g | grep -am1 'what():'); then r="ABORT $(echo "${l#*what():}" | c)"
elif l=$(g | grep -am1 -E '\|SIG(SEGV|BUS|ILL|FPE) @'); then
  sig=$(echo "$l" | grep -aoE 'SIG[A-Z]+' | head -1); addr=$(echo "$l" | grep -aoE '@0x[0-9a-f]+' | head -1)
  fn=$(echo "$l" | sed -n 's/.*x64pc=\(0x[0-9a-f]*\)\/"\([^"]*\)".*/\1 \2/p' | sed 's#/root/[^ ]*/libs/##; s#/root/[^ ]*/##')
  acc=$(echo "$l" | grep -aoE 'accessing 0x[0-9a-f]+' | head -1)
  r="CRASH $sig $addr x64pc=${fn:-?}${acc:+ ($acc)}"
elif l=$(g | grep -aim1 -E 'FATAL|panic'); then r="FATAL $(echo "$l" | c)"
elif [ "$rc" = 124 ]; then r="TIMEOUT ran to the limit"
elif [ "$rc" = 0 ]; then r="OK exit 0"
else r="EXIT rc=${rc:-none}$( [ "$rc" = 137 ] && grep -aq 'ram' "$LOG" && echo ' (killed: RAM guard or timeout -s KILL)')"; fi
flips=$(( $(g | grep -aciE 'submitflip|videoout.*flip|present(ed)? frame') + $(g | grep -aoE 'submissions: window count=[0-9]+' | sed 's/.*=//' | awk '{s+=$1} END {print s+0}') ))
caps=$(sed -n 's/^== capture \([^ ]*\) .* mean=\(.*\)/\1=\2/p' "$LOG" | tr '\n' ' ' | sed 's/ $//')
echo "$r; frames: ${flips} flips, captures ${caps:-none}"
