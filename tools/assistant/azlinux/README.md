# azlinux (Azure anyps5-x64) PR tests

Spot VM `anyps5-x64` (rg anyps5-cus, West US 3, D2as_v4: AMD EPYC 7763, 2 vCPU, AVX2, no AVX-512, 8 GB + 8 GB swap,
Ubuntu 24.04, 64 GB Standard SSD). `ssh azlinux` (192.0.2.1), NSG SSH only from home + VPN IPs.

- `x64_prtest.sh [PR...]`: merge PR into origin/main, build, ctest twice, compare with main's failures. `MODE=relinker`
  (default) or `MODE=full` (gcc + ccache like CI, lavapipe Vulkan). Reports: `~/x64-work/prtests/<N>.md`,
  one-line results: `~/x64-work/status.md`, logs: `~/x64-work/logs/`.
- `x64-pr-auto.sh`: hourly (`x64-pr-auto.timer`, :20 and 10 min after boot): new PR heads < 2 days old; up to 3
  relinker-only PRs + 1 full-build PR per run. Nothing is posted; the PC Claude reviews reports first.
- `live.sh`: live window on the VNC desktop (:1, localhost only; Machines app pane "Azure x64", local port 15905).
- `setup.sh`: re-runnable setup (swap, ccache, clone with `fork` remote, systemd units, autostart, launchers).

Update: `scp *.sh ../desktop-launchers.sh azlinux:x64-work/bin/ && ssh azlinux bash x64-work/bin/setup.sh`.
Manual run: `ssh azlinux 'MODE=relinker bash x64-work/bin/x64_prtest.sh 12345'` (or the "Run PR tests now" desktop icon).

Spot eviction: the VM is deallocated (disk kept). Start it in the portal (VM > Start); VNC and the timer are enabled
units, the runner's flock locks clear on reboot, and the timer catches up 10 min after boot.
