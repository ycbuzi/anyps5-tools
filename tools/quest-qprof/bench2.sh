#!/data/data/com.termux/files/usr/bin/bash
R=$PREFIX/var/lib/proot-distro/containers/debian/rootfs
P=/root/qtest/qprof; RP=$R$P; O=$RP/bench2.txt; T=$PREFIX/tmp/qprof-bench; mkdir -p $T; : > $O
PL() { proot-distro login debian -- bash -lc "$1"; }
PL "cd $P && gcc -O2 -o bench-arm bench.c && x86_64-linux-gnu-gcc -O2 -nostdlib -static -no-pie -fno-builtin -fno-stack-protector -mstackrealign -o cpu-x86 cpubench.c" >> $O 2>&1
clang -O2 -o $T/bench-bionic $RP/bench.c && clang -O2 -DHOSTED -o $T/cpu-bionic $RP/cpubench.c || echo "termux clang build failed" >> $O
for i in 1 2; do
  echo "== glibc under proot" >> $O; PL "$P/bench-arm" >> $O 2>&1
  echo "== bionic, no proot" >> $O; $T/bench-bionic >> $O 2>&1
done
ms() { local a=$(date +%s%N); "$@" >/dev/null 2>&1; local r=$?; echo "rc=$r $(( ($(date +%s%N) - a) / 1000000 )) ms"; }
for i in 1 2 3; do
  echo "cpu aarch64 native: $(ms $T/cpu-bionic)" >> $O
  PL "a=\$(date +%s%N); BOX64_NOBANNER=1 BOX64_LOG=0 BOX64_DYNACACHE=0 /opt/box64-once/bin/box64 $P/cpu-x86; r=\$?; echo cpu x86-64 under box64: rc=\$r \$(( (\$(date +%s%N)-a)/1000000 )) ms" >> $O 2>&1
done
rm -rf $T
echo done >> $O
