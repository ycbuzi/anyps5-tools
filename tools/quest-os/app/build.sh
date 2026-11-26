#!/bin/bash
set -euo pipefail
# Quest OS app: the AnyPS5 console on an OpenXR quad layer. Run from Git Bash: bash build.sh   Output: C:/AnyPS5-work/quest-os/app/anyps5-os.apk
HERE="$(cd "$(dirname "$0")" && pwd)"
SDK=/c/AnyPS5-work/android-sdk
NDK=$SDK/ndk/29.0.14206865
BT=$SDK/build-tools/36.0.0
CMAKE=$(ls -d $SDK/cmake/*/bin | tail -1)
JAVA_BIN=$HOME/tools/jdk-21/bin
OUT=/c/AnyPS5-work/quest-os/app
OPENXR=/c/AnyPS5-work/refs/OpenXR-SDK
CONSOLE="/c/AnyPS5 Games/_tools/console-native"
mkdir -p "$OUT/build" "$OUT/apk/lib/arm64-v8a"

"$CMAKE/cmake" -S "$HERE" -B "$OUT/build" -G Ninja -DCMAKE_MAKE_PROGRAM="$CMAKE/ninja.exe" \
  -DCMAKE_TOOLCHAIN_FILE="$NDK/build/cmake/android.toolchain.cmake" -DANDROID_ABI=arm64-v8a -DANDROID_PLATFORM=android-29 \
  -DCMAKE_BUILD_TYPE=Release -DOPENXR_SDK="$OPENXR" -DCONSOLE_SRC="$CONSOLE/src" -DDEPS=/c/AnyPS5-work/quest-os/deps/arm64 > "$OUT/configure.log" 2>&1 || { tail -20 "$OUT/configure.log"; exit 1; }
nice -n 10 "$CMAKE/ninja.exe" -C "$OUT/build" -j 4 anyps5os > "$OUT/build.log" 2>&1 || { grep -iE "error" "$OUT/build.log" | head -20; exit 1; }

cp "$OUT/build/libanyps5os.so" "$OUT/apk/lib/arm64-v8a/"
LOADER=$(find "$OUT/build" -name libopenxr_loader.so | head -1)
cp "$LOADER" "$OUT/apk/lib/arm64-v8a/"
"$NDK/toolchains/llvm/prebuilt/windows-x86_64/bin/llvm-strip.exe" --strip-unneeded "$OUT/apk/lib/arm64-v8a/"*.so

A="$OUT/apk/assets"
rm -rf "$A"
mkdir -p "$A/fonts"
cp -r "$CONSOLE/assets" "$A/assets"
cp "$CONSOLE/tests/fixtures/basic.json" "$A/fixture.json"
cp -r "$CONSOLE/tests/fixtures/art" "$CONSOLE/tests/fixtures/trophies" "$A/"
for f in NotoSans-Light NotoSans-Regular NotoSans-Medium NotoSans-SemiBold NotoSans-Bold NotoSansSymbols-Regular NotoSansSymbols2-Regular; do
    MSYS_NO_PATHCONV=1 wsl -d archlinux -u root -- cat /usr/share/fonts/noto/$f.ttf > "$A/fonts/$f.ttf" 2>/dev/null || rm -f "$A/fonts/$f.ttf"
done
(cd "$A" && find . -type f ! -name files.txt | sed 's#^\./##' > files.txt)
"$BT/aapt2.exe" link -o "$OUT/unaligned.apk" --manifest "$(cygpath -w "$HERE/AndroidManifest.xml")" -I "$(cygpath -w "$SDK/platforms/android-34/android.jar")"
(cd "$OUT/apk" && "$JAVA_BIN/jar.exe" uf "$(cygpath -w "$OUT/unaligned.apk")" lib/arm64-v8a/libanyps5os.so lib/arm64-v8a/libopenxr_loader.so assets)
"$BT/zipalign.exe" -f -p 4 "$OUT/unaligned.apk" "$OUT/aligned.apk"
KS=/c/AnyPS5-work/quest-os/debug.keystore
[ -f "$KS" ] || "$JAVA_BIN/keytool.exe" -genkeypair -keystore "$(cygpath -w "$KS")" -storepass android -keypass android -alias debug \
  -keyalg RSA -keysize 2048 -validity 10000 -dname "CN=AnyPS5 OS debug" > /dev/null 2>&1
"$BT/apksigner.bat" sign --ks "$(cygpath -w "$KS")" --ks-pass pass:android --out "$(cygpath -w "$OUT/anyps5-os.apk")" "$(cygpath -w "$OUT/aligned.apk")"
ls -l "$OUT/anyps5-os.apk"
