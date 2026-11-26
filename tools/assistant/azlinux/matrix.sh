#!/bin/bash
# Linux distro/compiler matrix for azlinux (11-08). Builds the relinker-only tree and runs ctest once inside one
# container per image/compiler from ~/x64-work/matrix/combos.txt (Dockerfiles next to it, official base images only).
# One container at a time, nice 19; build dirs + ccache per combo on the temp SSD (/mnt/matrix, recreated after an
# eviction). Main baselines are cached per main commit and combo, so a PR only rebuilds what it changes.
# usage: matrix.sh baseline <main>        (run with main checked out in ~/AnyPS5)
#        matrix.sh pr <N> <main>          (run with the merged PR checked out; prints one report line per combo)
set -u
W=$HOME/x64-work; M=$W/matrix; L=$W/logs/matrix; T=/mnt/matrix; SRC=$HOME/AnyPS5
mkdir -p "$M" "$L"
[ -d "$T" ] || { sudo mkdir -p "$T" && sudo chown "$(id -u):$(id -g)" "$T"; }
mode=$1; shift
img_ok() {
    local img=$1 tag=anyps5-matrix:$1 made
    made=$(docker image inspect -f '{{.Created}}' "$tag" 2>/dev/null)
    if [ -z "$made" ] || [ $(( $(date +%s) - $(date -d "$made" +%s) )) -gt 604800 ]; then
        echo "image $img: building" > "$M/now.txt"
        nice -n 19 docker build -q -t "$tag" -f "$M/$img.Dockerfile" "$M" > "$L/image-$img.log" 2>&1 < /dev/null || return 1
    fi
}
run() {
    local img=$1 label=$2 cc=$3 cxx=$4 tag=$5 d=$T/$1-$2
    mkdir -p "$d/ccache"
    rm -f "$d/build.rc" "$d/ctest.log" "$d/build.log"
    echo "$tag $img $label: building + ctest" > "$M/now.txt"
    docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -e CCACHE_DIR=/w/ccache -e CCACHE_MAXSIZE=500M \
        -e CC="$cc" -e CXX="$cxx" -v "$SRC:/src" -v "$d:/w" --memory 6g --memory-swap 12g \
        "anyps5-matrix:$img" bash -c 'cd /w; $CXX --version | head -1 > cc.txt
          G=Ninja; command -v ninja > /dev/null || G="Unix Makefiles"
          CL=""; command -v ccache > /dev/null && CL="-DCMAKE_C_COMPILER_LAUNCHER=ccache -DCMAKE_CXX_COMPILER_LAUNCHER=ccache"
          if nice -n 19 cmake -S /src -B /w/build -G "$G" -DCMAKE_BUILD_TYPE=Release -DANYPS5_RELINKER_ONLY=ON -DBUILD_TESTING=ON $CL > configure.log 2>&1; then
            if [ "$G" = Ninja ]; then nice -n 19 ninja -C /w/build -j2 -k 0 > build.log 2>&1; else nice -n 19 make -C /w/build -j2 -k > build.log 2>&1; fi; echo $? > build.rc
            nice -n 19 ctest --test-dir /w/build -j2 --timeout 300 > ctest.log 2>&1
          else echo configure > build.rc; cp configure.log build.log; fi' > /dev/null 2>&1 < /dev/null
    cp "$d/build.log" "$L/$tag-$img-$label-build.log" 2>/dev/null
    cp "$d/ctest.log" "$L/$tag-$img-$label-ctest.log" 2>/dev/null
}
failures() { sed -n '/^The following tests FAILED:/,/^[^[:space:]]/p' "$1" 2>/dev/null | grep -E '^[[:space:]]+[0-9]+ - ' | sed -E 's/^[[:space:]]+[0-9]+ - //; s/ \(.*$//' | sort -u; }
summary() { grep -E 'tests passed' "$1" 2>/dev/null | tail -1 | sed -E 's/^[0-9]+% tests passed, //'; }
grep -vE '^#|^$' "$M/combos.txt" | while read -r img label cc cxx; do
    d=$T/$img-$label
    case $mode in
    baseline)
        main=$1; b=$M/main-$main-$img-$label
        [ -f "$b.rc" ] && continue
        img_ok "$img" || { echo "image-failed" > "$b.rc"; : > "$b.failures"; continue; }
        t0=$(date +%s); run "$img" "$label" "$cc" "$cxx" "main-$main"
        cp "$d/build.rc" "$b.rc" 2>/dev/null || echo none > "$b.rc"
        failures "$d/ctest.log" > "$b.failures"; cp "$d/cc.txt" "$M/$img-$label.cc" 2>/dev/null
        echo "$(date '+%m-%d %H:%M') main $main $img $label: build rc $(cat "$b.rc") in $(( $(date +%s) - t0 ))s; $(summary "$d/ctest.log")" >> "$M/status.txt"
        ;;
    pr)
        n=$1; main=$2; b=$M/main-$main-$img-$label
        brc_main=$(cat "$b.rc" 2>/dev/null || echo "?")
        if [ "$brc_main" = image-failed ]; then echo "| $img | $label | image did not build | - | - |"; continue; fi
        t0=$(date +%s); run "$img" "$label" "$cc" "$cxx" "pr-$n"; s=$(( $(date +%s) - t0 ))
        brc=$(cat "$d/build.rc" 2>/dev/null || echo none)
        new=$(comm -23 <(failures "$d/ctest.log") <(sort -u "$b.failures" 2>/dev/null) | tr '\n' ' ')
        ver=$(sed -E 's/ \(.*\)//; s/^.*(version|\)) //' "$d/cc.txt" 2>/dev/null | head -c 40)
        if [ "$brc" = 0 ]; then bt="ok"; elif [ "$brc_main" != 0 ]; then bt="FAILED (rc $brc; main also rc $brc_main)"; else bt="FAILED (rc $brc; main ok)"; fi
        echo "| $img | $label $ver | $bt | ${new:-none} ($(summary "$d/ctest.log")) | ${s}s |"
        echo "$(date '+%m-%d %H:%M') #$n $img $label: build $bt, new failures ${new:-none}, ${s}s" >> "$M/status.txt"
        ;;
    esac
done
rm -f "$M/now.txt"
