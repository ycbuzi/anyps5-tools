# AnyPS5 tools (retired)

Everything I built around [AnyPS5](https://github.com/boykopovar/AnyPS5) while working on it, given away as is. I'm
retiring from PS5 work for personal reasons, so none of this is maintained anymore. Take whatever is useful, fork it,
rip it apart. Public domain (see `LICENSE`).

These were written for one setup: a Windows PC with three GPUs (RTX 4070 SUPER, GTX 1650 SUPER, Ryzen iGPU), a
MacBook, a Chromebook on Arch Linux, a Quest 2 and an Xbox in Developer Mode. Paths, drive letters and host names
are mine; IPs, e-mails and my user name are replaced with placeholders (`192.0.2.1`, `user@example.com`, `user`).
Expect to edit paths before anything runs. No game files, keys, logs or saved credentials are in here.

## What's in here

### Games pipeline (`tools/`)
- `auto-games.ps1`: the unattended loop. Finds dumped games, builds each one into a per-game folder
  (`<Title>.exe` + `Rebuild.cmd` + mods), boots it, takes screenshots, sorts it into works / broken / stuck and
  retries with newer AnyPS5 builds. `build-game.ps1`, `start-game.ps1` and `test-launch.ps1` are its pieces.
- `game-guard.ps1`: keeps every game on the AnyPS5 build it was tested with, so PR test builds can't break games.
- `pipeline-watchdog.ps1`, `pipeline_doctor.py`, `pipeline-repair-agent.ps1`: restart a stuck pipeline and
  diagnose why it stopped.
- `predict_missing.py`, `predict_fix.py`: read a game's import list and predict which missing functions it will
  hit, and which open PRs implement them.
- `shader_tier.py`: puts recently played games' shader caches on the SSD (junctions, with a restore-all undo).
- `gpu-lanes.ps1`, `dispatcher.ps1`, `pc-monitor.ps1`: one GPU per job, a job board, and one lock for heavy work.
- `exfat_extract.py`: pulls files out of exFAT dump images without mounting them.
- `shot.ps1`, `window-shot.ps1`, `watch-log.ps1`, `remote-view.ps1`: screenshots and log tails of running games.

### Launcher (`tools/launcher`)
The "AnyPS5 Games" Windows app: game library, play button, per-game status, machines tab, live pipeline status.

### Local assistant (`tools/assistant`)
A local helper that knows the machines and can run the routine jobs itself: GitHub PR tests (`skills/forkci.py`
runs them free on GitHub runners), PR report posting, mentions and replies, job board fan-out to the Mac /
Chromebook / Quest (`skills/jobboard.py`, `skills/fleet.py`), nightly health, and a lot of small skills.
`claude_bridge.py` lets the pipeline ask Claude for a repair with a daily dollar cap.

### AnyPS5 OS (`tools/console`, `tools/console-native`)
A console-style shell for running AnyPS5 as the whole OS on a small Linux box (the Chromebook): home screen,
library, settings pages (network, Bluetooth, controllers, DualSense), host service.

### Quest 2 (`tools/quest*`)
Native AnyPS5 on the Quest 2 (box64 + Turnip): build relay from WSL, profiler (`quest-qprof`), screen capture,
a Quest OS shell.

### Chromebook (`tools/chromebook-thermal`, `tools/device-map`)
Thermal limits for long test runs on a fanless N6000, and a device map for every machine.

### Streaming (`tools/stream`)
OBS WebSocket control, scene builder and a stream control page for showing test runs live.

### Xbox (`xbox-probe/`)
Tools for running AnyPS5 games on an Xbox in Developer Mode: a UWP probe app that surveys what the console
allows, a Vulkan-on-D3D12 runtime experiment (`xvk`), the Xbox console shell, and Device Portal helpers to deploy,
launch, push files and pull logs (`deploy.py`, `push.py`, `launch.py`, `xbox_watch.py`). `scrub.py` strips
console names, IPs and account info from anything printed, for streaming.

## Thanks
To everyone in the AnyPS5 Discord who tested, reviewed and put up with my PRs. It was a blast.
