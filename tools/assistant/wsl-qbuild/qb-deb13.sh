#!/bin/bash
# qb-deb13.sh - Debian 13 (trixie) build chroot for the WSL Quest builder (11-17).
# Why: WSL Arch builds with gcc 16 / glibc 2.44 needed GLIBCXX_3.4.35 and GLIBC_2.44; the Quest's Debian 13 rootfs
# (box64 loads its x86-64 libstdc++ 6.0.33 = gcc 14) cannot load them, so every game exited rc=255 at startup.
# The Chromebook builds worked because they were made on Debian 13. This chroot reproduces that toolchain.
#   qb-deb13.sh setup        create/refresh /root/qb-deb13 (debootstrap from deb.debian.org + build packages)
#   qb-deb13.sh run CMD...   run CMD inside the chroot (mounts /proc, /dev, /dev/pts and /root/qb at the same path)
#   qb-deb13.sh check        prints "ok <gcc> <glibc>" when the chroot is ready, exits 1 otherwise
set -eu
C=/root/qb-deb13; SUITE=trixie; MIRROR=http://deb.debian.org/debian
PKGS="build-essential git cmake ninja-build python3 ccache ca-certificates pkg-config libx11-dev libxext-dev
 libxrandr-dev libxcursor-dev libxi-dev libxfixes-dev libxss-dev libxtst-dev libwayland-dev wayland-protocols
 libxkbcommon-dev libegl-dev libgl-dev libdecor-0-dev libpipewire-0.3-dev libpulse-dev libasound2-dev"

mount_all() {
  mkdir -p "$C/root/qb"
  mountpoint -q "$C/proc" || mount -t proc proc "$C/proc"
  mountpoint -q "$C/dev" || mount --bind /dev "$C/dev"
  mountpoint -q "$C/dev/pts" || mount --bind /dev/pts "$C/dev/pts"
  mountpoint -q "$C/root/qb" || mount --bind /root/qb "$C/root/qb"
}
in_chroot() {
  chroot "$C" /usr/bin/env -i HOME=/root LANG=C.UTF-8 TERM=dumb CCACHE_DIR=/root/qb/ccache13 \
    PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin "$@"
}

case "${1:-}" in
  setup)
    for p in debootstrap debian-archive-keyring; do pacman -Q "$p" > /dev/null 2>&1 || pacman -S --needed --noconfirm "$p"; done
    if [ ! -x "$C/usr/bin/apt-get" ]; then
      rm -rf "$C"
      debootstrap --variant=minbase --keyring=/usr/share/keyrings/debian-archive-keyring.gpg "$SUITE" "$C" "$MIRROR"
    fi
    cp /etc/resolv.conf "$C/etc/resolv.conf" 2>/dev/null || true
    mount_all
    in_chroot apt-get update -q
    in_chroot env DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends $PKGS
    in_chroot apt-get clean
    in_chroot git config --global --add safe.directory '*'
    mkdir -p /root/qb/ccache13
    "$0" check ;;
  run)
    shift; [ -x "$C/usr/bin/cmake" ] || { echo "Debian chroot $C not set up (qb-deb13.sh setup)"; exit 1; }
    mount_all; in_chroot "$@" ;;
  check)
    [ -x "$C/usr/bin/cmake" ] && [ -x "$C/usr/bin/g++" ] || { echo "not ready"; exit 1; }
    mount_all
    echo "ok gcc $(in_chroot gcc -dumpfullversion) glibc $(in_chroot ldd --version | head -1 | awk '{print $NF}')" ;;
  *) echo "usage: qb-deb13.sh setup|run CMD...|check"; exit 2 ;;
esac
