# Discord multi-monitor stream

Discord Go Live shares one screen or one window. OBS puts the monitors you pick into one picture and shows it in a window (the projector), and you share that window in Discord.

## Use it

1. Start the scheduled task **AnyPS5 Discord stream** (Task Scheduler, or `schtasks /Run /TN "AnyPS5 Discord stream"`). OBS starts in the tray, and a window called **Projector - Program** opens on the right monitor.
2. In Discord: Screen Share -> Applications -> **Projector - Program**. Leave that window open and don't minimize it. You can move or resize it, or cover it with other windows.
3. Switch layouts at any time. Discord shows the change straight away:
   - Phone or PC browser: `http://192.0.2.1:8795`. The PIN is in `C:\AnyPS5-work\secrets\stream-pin.txt`; you enter it once per device.
   - Hotkeys (work in any app): Ctrl+Alt+1 Middle, 2 Left, 3 Right, 4 Middle + Right, 5 Left + Middle, 6 All three.
   - The page also has **Open projector** (brings the window back) and **Restart OBS**.

## Parts

- OBS profile **Discord Monitors** (2560x1440, 30 fps) and scene collection **Discord Monitors**. Your other profiles are not touched.
- `start.py`: the launcher run by the task. It starts OBS if needed, re-picks the three monitors by position, rebuilds the six layouts, adds the hotkeys and opens the projector. It refuses to switch profile while OBS is streaming or recording on another profile.
- `control.py`: the phone page. The task **AnyPS5 stream control page** starts it at logon, through python.exe (which the firewall already allows) under a headless conhost.
- `build.py screens`: switches through every layout and saves PNGs to `C:\AnyPS5-work\probes\stream\`.
- `build.py ws`: rewrites the OBS websocket config (`plugin_config\obs-websocket\config.json`, password in `C:\AnyPS5-work\secrets\obs-websocket.txt`). Close OBS first.
- `install.ps1`: registers both scheduled tasks again.
- Log: `C:\AnyPS5-work\probes\stream\stream.log`.

## Notes

- If a monitor is moved or swapped, edit `MONITORS` in `obsws.py` (position and size), then run the task again.
- When you open OBS normally, it opens the last profile you used. Pick Profile -> Untitled (or another) from the menu to go back.

## Stream audio
Discord only captures sound from the shared app (OBS). The stream carries game sound only:
- **Game sound** is an OBS per-app audio capture. The control page's watcher, every 10 s, points it at whichever AnyPS5 game window is running (an exe under D:\AnyPS5 Games\<game>\).
- OBS plays that sound ("monitor only") to the LS24D36xG HDMI output, which nobody listens to. Discord hears it from OBS, and you don't hear it twice.
- Spotify, Discord voice, browsers and everything else are never captured. The microphone stays with Discord voice.
- This takes effect from the next "AnyPS5 Discord stream" start, because OBS only picks its monitoring device at startup.
- If the left monitor ever plays sound out loud, change `SILENT_MONITOR` in obsws.py.
