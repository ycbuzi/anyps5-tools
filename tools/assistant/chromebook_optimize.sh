#!/bin/bash
# Speeds up Blu's Chromebook Linux (penguin) as an AnyPS5 build and test runner, like mac_optimize.sh does for the
# MacBook (Blu 11-06: "add the performance settings, just like ... the MacBook"). Run with: bash chromebook_optimize.sh
# Undo: bash ~/chromebook-optimize-undo.sh (written by this script).
set -u
undo=~/chromebook-optimize-undo.sh
echo "#!/bin/bash" > "$undo"

# Background jobs that can start a package download or a whole-disk scan in the middle of a build.
for t in apt-daily.timer apt-daily-upgrade.timer e2scrub_all.timer dpkg-db-backup.timer; do
    if systemctl is-enabled "$t" >/dev/null 2>&1; then
        sudo systemctl disable --now "$t" >/dev/null 2>&1 && echo "sudo systemctl enable --now $t" >> "$undo" && echo "off: $t"
    fi
done

# System log capped at 100 MB so long test runs do not grow it on the small disk.
if [ ! -f /etc/systemd/journald.conf.d/size.conf ]; then
    sudo mkdir -p /etc/systemd/journald.conf.d
    printf "[Journal]\nSystemMaxUse=100M\n" | sudo tee /etc/systemd/journald.conf.d/size.conf >/dev/null
    sudo systemctl restart systemd-journald
    echo "sudo rm -f /etc/systemd/journald.conf.d/size.conf; sudo systemctl restart systemd-journald" >> "$undo"
    echo "on: journal capped at 100 MB"
fi

# ccache: a PR test recompiles only what the PR changes instead of the whole relinker (the CPU is a 1.1 GHz N6000).
if ! command -v ccache >/dev/null; then
    sudo apt-get install -y ccache >/dev/null 2>&1 && echo "sudo apt-get remove -y ccache" >> "$undo"
fi
if command -v ccache >/dev/null; then
    ccache --max-size=10G >/dev/null
    echo "on: ccache ($(ccache --version | head -1)), 10 GB; configure builds with -DCMAKE_CXX_COMPILER_LAUNCHER=ccache"
fi

chmod +x "$undo"
echo "undo script: $undo"
