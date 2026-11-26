#!/bin/bash
# Installs the WSL Quest builder (11-16; replaces the Chromebook's cb-quest-builds) into WSL Arch "archlinux" as root.
# Run from Windows:  wsl.exe -d archlinux -u root -- bash "/mnt/c/AnyPS5 Games/_tools/assistant/wsl-qbuild/install.sh"
# Safe to rerun: it updates /root/qb/bin from this folder and never touches builds, requests or the sent list.
# Layout (same as the Chromebook's ~/cb-work): /root/qb/{quest-requests.txt,quest-sent.txt,quest-dist/<folder>/,
# azarm/games/<game>/,azarm-relinked/<folder>/,logs/quest-builds.log,state.txt}, repo /root/qb/AnyPS5, build /root/qb/build-q.
set -eu
SRC="$(cd "$(dirname "$0")" && pwd)"
QB=/root/qb
PKGS="base-devel git cmake ninja python ccache curl tar gzip libx11 libxext libxrandr libxcursor libxi libxfixes libxss
 libxtst wayland wayland-protocols libxkbcommon libglvnd mesa libdecor pipewire libpulse alsa-lib"
missing=$(pacman -T $PKGS 2>/dev/null || true)
[ -n "$missing" ] && pacman -S --needed --noconfirm $PKGS
mkdir -p "$QB/bin" "$QB/logs" "$QB/quest-dist" "$QB/azarm/games" "$QB/azarm-relinked" "$QB/ccache"
install -m 755 "$SRC/qb-quest-builds.sh" "$QB/bin/qb-quest-builds.sh"
install -m 755 "$SRC/qb-relink.sh" "$QB/bin/qb-relink.sh"
install -m 755 "$SRC/qb-deb13.sh" "$QB/bin/qb-deb13.sh"
[ "${QB_SKIP_DEB13:-0}" = 1 ] || "$QB/bin/qb-deb13.sh" check > /dev/null 2>&1 || "$QB/bin/qb-deb13.sh" setup
touch "$QB/quest-requests.txt"
[ -s "$QB/quest-sent.txt" ] || cp "$SRC/seed/quest-sent.from-chromebook.txt" "$QB/quest-sent.txt"
for g in "$SRC"/seed/azarm/games/*/; do
  n=$(basename "$g"); [ -d "$QB/azarm/games/$n" ] || cp -r "$g" "$QB/azarm/games/$n"
done
if [ ! -d "$QB/AnyPS5/.git" ]; then
  git clone -q --filter=blob:none https://github.com/boykopovar/AnyPS5 "$QB/AnyPS5"
  git -C "$QB/AnyPS5" remote add fork https://github.com/ycbuzi/AnyPS5
fi
[ -f "$QB/state.txt" ] || echo "idle" > "$QB/state.txt"
echo "installed: $(ls "$QB/bin" | tr '\n' ' ')repo $(git -C "$QB/AnyPS5" rev-parse --short HEAD)"
