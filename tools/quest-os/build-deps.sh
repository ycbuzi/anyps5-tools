#!/bin/bash
# Quest OS, phase 1: cross-build the console's libraries for the Quest (arm64 Android) as static libraries.
# Run from Git Bash on the PC: bash build-deps.sh        Output prefix: C:\AnyPS5-work\quest-os\deps\arm64
# Sources: SDL3 (already in refs), SDL_ttf and SDL_image from libsdl-org's GitHub (cloned once into refs).
# Local SDL patches for NativeActivity apps: see sdl-patches.py.
set -euo pipefail
SDK=/c/AnyPS5-work/android-sdk
NDK=$SDK/ndk/29.0.14206865
CMAKE=$(ls -d $SDK/cmake/*/bin | tail -1)
REFS=/c/AnyPS5-work/refs
OUT=/c/AnyPS5-work/quest-os/deps
PREFIX=$OUT/arm64
mkdir -p "$OUT" "$PREFIX"

clone() {
    [ -d "$REFS/$1/.git" ] && return 0
    git clone --quiet --depth 1 --branch "$3" "$2" "$REFS/$1"
}
clone SDL3_ttf https://github.com/libsdl-org/SDL_ttf.git release-3.2.2
clone SDL3_image https://github.com/libsdl-org/SDL_image.git release-3.2.4
if [ ! -f "$REFS/SDL3_ttf/external/freetype/CMakeLists.txt" ]; then
    (cd "$REFS/SDL3_ttf" && git submodule --quiet update --init --depth 1 external/freetype)
fi

python -I "$(dirname "$0")/sdl-patches.py" "$REFS/SDL3" || exit 1

build() {
    name=$1; src=$2; shift 2
    "$CMAKE/cmake" -S "$src" -B "$OUT/build-$name" -G Ninja -DCMAKE_MAKE_PROGRAM="$CMAKE/ninja.exe" \
        -DCMAKE_TOOLCHAIN_FILE="$NDK/build/cmake/android.toolchain.cmake" -DANDROID_ABI=arm64-v8a -DANDROID_PLATFORM=android-29 \
        -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF -DCMAKE_INSTALL_PREFIX="$PREFIX" -DCMAKE_PREFIX_PATH="$PREFIX" \
        -DCMAKE_FIND_ROOT_PATH="$PREFIX" -DCMAKE_POSITION_INDEPENDENT_CODE=ON "$@" > "$OUT/$name-configure.log" 2>&1 \
        || { echo "$name: configure failed"; tail -15 "$OUT/$name-configure.log"; exit 1; }
    nice -n 10 "$CMAKE/ninja.exe" -C "$OUT/build-$name" -j 4 install > "$OUT/$name-build.log" 2>&1 \
        || { echo "$name: build failed"; grep -iE "error|FAILED" "$OUT/$name-build.log" | head -15; exit 1; }
    echo "$name: built"
}

build sdl3 "$REFS/SDL3" -DSDL_SHARED=OFF -DSDL_STATIC=ON -DSDL_TEST_LIBRARY=OFF -DSDL_TESTS=OFF -DSDL_EXAMPLES=OFF
build sdl3_image "$REFS/SDL3_image" -DSDLIMAGE_VENDORED=OFF -DSDLIMAGE_SAMPLES=OFF -DSDLIMAGE_TESTS=OFF -DSDLIMAGE_AVIF=OFF \
    -DSDLIMAGE_JXL=OFF -DSDLIMAGE_TIF=OFF -DSDLIMAGE_WEBP=OFF -DSDLIMAGE_BACKEND_STB=ON
build sdl3_ttf "$REFS/SDL3_ttf" -DSDLTTF_VENDORED=ON -DSDLTTF_HARFBUZZ=OFF -DSDLTTF_PLUTOSVG=OFF -DSDLTTF_SAMPLES=OFF
ls -la "$PREFIX/lib" | awk '{print $5, $9}' | grep -E "\.a$"
