#!/data/data/com.termux/files/usr/bin/bash
R=$PREFIX/var/lib/proot-distro/containers/debian/rootfs
N=$HOME/native; L=$N/box64-termux.log
log() { echo "$(date +%T) $*" >> "$L"; }
: > "$L"
if [ -n "$1" ]; then
  end=$(( $(date +%s) + 3600 ))
  until [ -f "$1/finished" ] || [ "$(date +%s)" -gt "$end" ]; do sleep 30; done
fi
log "copy source"
rm -rf "$N/box64"; mkdir -p "$N/box64"
tar -C "$R/root/box64-src/box64" --exclude=./build --exclude=./.git -cf - . | tar -C "$N/box64" -xf -
CL=$PREFIX/bin/clang
log "configure"
proot-distro login debian -- bash -c "cmake -S $N/box64 -B $N/box64/build-termux -DTERMUX=1 -DANDROID=1 -DARM_DYNAREC=ON -DCMAKE_BUILD_TYPE=RelWithDebInfo -DCMAKE_C_COMPILER=$CL -DCMAKE_ASM_COMPILER=$CL -DCMAKE_MAKE_PROGRAM=/usr/bin/make" >> "$L" 2>&1 || { log configure-failed; echo configure-failed > "$N/box64-termux.done"; exit 1; }
log "build"
t0=$(date +%s)
proot-distro login debian -- bash -c "nice -n 10 make -C $N/box64/build-termux -j4 box64" > "$N/box64-build.log" 2>&1
rc=$?
log "build rc=$rc secs=$(( $(date +%s) - t0 ))"
tail -20 "$N/box64-build.log" >> "$L"
B=$N/box64/build-termux/box64
if [ -x "$B" ]; then
  readelf -l "$B" | grep -o 'interpreter: [^]]*' >> "$L"
  "$B" --version >> "$L" 2>&1
  cp "$R/root/qtest/qprof/cpu-x86" "$N/cpu-x86"
  proot-distro login debian -- bash -c "x86_64-linux-gnu-gcc -O2 -o $N/bench-x86 /root/qtest/qprof/bench.c" >> "$L" 2>&1
  for i in 1 2 3; do
    a=$(date +%s%N); BOX64_NOBANNER=1 BOX64_LOG=0 "$B" "$N/cpu-x86" >/dev/null 2>&1; r=$?; b=$(date +%s%N)
    log "cpu-x86 native-box64 rc=$r ms $(( (b - a) / 1000000 ))"
  done
  for i in 1 2; do
    log "bench-x86 native-box64"; BOX64_NOBANNER=1 BOX64_LOG=0 "$B" "$N/bench-x86" >> "$L" 2>&1; log "rc=$?"
    log "bench-x86 proot-box64"; proot-distro login debian -- bash -c "BOX64_NOBANNER=1 BOX64_LOG=0 /opt/box64-once/bin/box64 $N/bench-x86" >> "$L" 2>&1
    log "bench-x86 nokr-proot-box64"; PD_PROOT_BIN=$N/proot-nokr proot-distro login debian -- bash -c "BOX64_NOBANNER=1 BOX64_LOG=0 /opt/box64-once/bin/box64 $N/bench-x86" >> "$L" 2>&1
  done
fi
echo "rc=$rc" > "$N/box64-termux.done"
