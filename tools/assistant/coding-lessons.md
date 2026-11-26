# Coding lessons (read before writing or reviewing code)

This file is how the coding gets better each time. Every agent prompt that writes or reviews code starts with: "Read C:\AnyPS5 Games\_tools\assistant\coding-lessons.md first."

**Adding a lesson:** whenever a review, a CI run or a device test catches a mistake, add one line here: the date, what went wrong, and the rule that prevents it. `lessons_from_reviews.py` mines review comments on our PRs every night and lists new candidates in `_reports\lessons-candidates.md`.

## Before writing
- Search for earlier work first: our local and fork branches (`git branch -a --list '*<topic>*'`), `_reports\quest-branches.md`, and open PRs touching the same files. On 11-16 we re-implemented a fragment one-lane fix that quest/terrifier-celeste-fixes already had.
- Read AGENTS.md rules again for every upstream change: no comments in code, no special case for one title or value, NotImplemented for unknown exports, a debt entry for known gaps.

- Before saying what we have or don't have (games, dumps, branches, devices), check the real source: scan the folders, query git, or ask the device. Never answer from a saved list or report. On 11-16 a design agent read an old `vr-titles.md` and said we had no real PS VR2 games, but VR Skater and OVRDARK had been downloaded since. `vr_titles.py` now rebuilds that list every night.

## While writing
- When main moves under a branch, re-check constants that depend on generated code layout. On 11-16 the `--to-sse` test helper assumed an old 14-byte-shorter prologue and crashed after the rebase.
- When a tool output may vary by host, check it on every target before reporting. On 11-16 WSL Arch builds needed glibc 2.44 / GLIBCXX_3.4.35, which the Quest's Debian 13 lacks. The builder now refuses packages that need newer symbols.
- Never edit a script in place while bash is running it. Bash reads it as it goes. Kill the job, patch it, then resend.
- Path quoting: Git Bash needs `MSYS_NO_PATHCONV=1` for `/mnt/...` paths, `tar --force-local` for `D:\` archives, and a script file instead of inline `$vars` sent to `wsl.exe`.

## Before reporting or pushing
- Before opening or updating any upstream PR, run `python "C:\AnyPS5 Games\_tools\assistant\pr_conflicts.py" <branch> [PR#]`. It dry-merges our branch with every open PR that touches the same files (`git merge-tree`) and lists real text conflicts and possible duplicates. Note every conflicting PR in **Depends on** with how they compose, and never open a PR that duplicates an open one. On 11-17, EagleBearerYS had to point out that #22062 and #15941 change the same `else` branch of PublishToGuest; the bot's file-level list of 60 PRs hid it.
- A pushed head must contain every local commit. On 11-16 PR #22062's follow-up fix sat unpushed until a reviewer found it. Check `git status -sb` for "ahead".
- Test the thing that actually ships (the deployed program on the real device), not only unit tests. An offscreen or harness pass is not "works".
- On the Chromebook, never reload audio modules (rt5682s clock bug, which wedges the kernel). Restart the session only via `systemd-run --on-active=2`.
- Flaky versus real: rerun a failing test alone with a longer timeout before calling it a regression, as mac_prtest.sh now does.

## My own tools
- Assume my tools misbehave until checked. Each check-in runs `pc_anomalies.py` (leaks, respawn loops, orphans). On 11-17 the AnyPS5 Machines app had been retrying 10 deleted machines forever and had leaked 24 VNC viewers, and the PC lock missed games on G:. All of that went unnoticed for 6 check-ins.
- When a machine, drive or path changes (VM deleted, games moved to G:, device retired), grep every tool and config for the old value the same day and fix them all.
- 2026-11-17: the shader tier assumed every game folder lives on D:, but 12 are junctions to G: or C: (Luna Abyss, the game it was built for, among them). Its cross-drive rename always failed and its "game running" check missed G: processes. Resolve the real path for renames and process checks, and grep `Get-ChildItem "D:\AnyPS5 Games" | ? LinkType` before assuming a drive.
- Prefer the simplest code that works. Remove dead config and branches instead of working around them. Retry loops need backoff and a reason to keep retrying.

- Never watch a log with Git Bash `tail -F` from a Monitor. When the watch expires, `tail.exe` stays behind and keeps the file locked, so the writer silently fails. On 11-17 the PC monitor's alerts, including the VPN-dropped alert, were lost this way. Use `_toolsssistantollow.py`, which reopens the file on each read and never locks it.

## Agent prompts
- Forbid ending a run with "I'll wait for events": use bounded blocking waits, or report "not done". Two reviewers quit early on 11-16.
- Give each agent its own build dir and the PC lock rule. Say which other jobs' dirs it must never touch.
- A reviewer gets the code and the facts, not the author's conclusion.
- Processes a tool or app starts belong under it: launch children from the owning process (CreateProcess, in a Job Object when they should close together), not via a detached script or scheduled task. Blu 11-16: games launched from AnyPS5.exe were not children of it.
- 2026-11-17: on Windows `OnMainThread` runs inline, so SDL subsystems started in a guest-thread constructor live on that thread. Joystick hot-plug (HIDAPI device notifications) only works when the subsystem is initialised on the thread that pumps SDL events; a controller plugged in after launch was never seen (#22189). Start event-driven SDL subsystems where the events are pumped.
- 2026-11-17: BlueZ forgets a found device about 30 s after a scan ends, so `bluetoothctl pair` from a list the user took a while to pick from failed with "not available". Pair while a scan runs again, or check that the device is still known first (os-host `bluetooth._rediscover`).
- 2026-11-17: an exec gate read `<&3` but `pass_fds` keeps the pipe's own fd number, so the shell failed (`3: Bad file descriptor`) and the game started early; a unit test passed only because the pipe happened to be fd 3. Gate on stdin (or dup to a fixed fd), and test fd plumbing with extra fds open first.
- 11-17: after deploying a console build, check the console log shows 'pad:' for a connected controller before saying it works; the 04:16 build shipped without registering pads and Blu found it hours later. When reverting an env change, re-test the original symptom on the CURRENT build (HIDAPI_PS5=0 was blamed, the build was the cause).
- 11-17: an A/B is only valid with a warm cache on both sides; my 'pad costs 2.7x FPS' came from cold-shader-cache runs. Repeat the baseline right after the test before blaming a variable.
- 11-17 (again): on the device, never start the host with setsid by hand for a test; it outlives the session and keeps port 8790, so later deploys silently run old host code. Restart through the session (systemctl restart getty@tty1), and identify processes by PID/parent, never pgrep -f with a pattern from my own command line.
- 11-17: a heal action must never fire on an untested check. My new Chromebook check read the host as DOWN (Windows stripped quotes, then braces) and restarted the session on its first run. Run a new check in report-only mode first, dump its raw output, and enable the heal only once it reads a healthy device as healthy.
- 11-17 (third time): pgrep -f with a pattern inside an ssh/bash -c command always matches the command itself. Match processes by exact name (pgrep -x game) or by PID.
- 11-22: a tool printing 'launched'/'done' is not proof. After any launch, check what is really running (which game, its log) before telling Blu. Xbox: launchapp.py Game opens the app's current game (game.txt), not the one just pulled.
- 11-22: /proc/pid/maps paths can hold spaces (game folders do); split with maxsplit=5, never take field 5 of split(). A wrong module label (cbsample called every game-folder lib 'Hades') sent the Hades profile analysis the wrong way.
- 11-22: scrub.py missed the UWP '-ServerName:...AppX<hash>' argument inside a game log; now scrubbed. Before printing any new kind of device log, grep it for package/AppX/ServerName first.
- Before saying a fix moved a game further, list every session in the appended log (grep the session marker plus the key lines); the arena crash was a separate run, PreInit had never passed.
- Compare files across worktrees with diff -q --strip-trailing-cr; a CRLF-only difference printed 700 lines.
- UWP/GUI processes: C stdout/stderr FILE streams can start with no descriptor, so dup2 on fd 1/2 does not reach fwrite/fprintf; check that library log lines actually land in the file before trusting a quiet log.
- C string escapes (
) written through a bash heredoc into Python get turned into real newlines here; add C log lines with the Edit tool, then grep the line before building.
- 2026-11-22 Xbox dir-open fix attempt 1 (GetFileInformationByHandleEx directory check after NativeOpen) did not change Stray's fd 5; trace the open call site before guessing the branch.
- 2026-11-22 On UWP/Xbox, GetFinalPathNameByHandleW cannot name directory handles; never make a descriptor lookup depend on it alone, keep the opened path as fallback. Trace the call site before patching a guessed branch.
- 2026-11-22 procs.py printed nothing while I called Stray 'exited'; confirm a process check sees a known-running game before using its silence as proof.
- 2026-11-22 Don't gate a D3D12 workaround on the Vulkan flag the app should set (MUTABLE_FORMAT): AnyPS5 storage images skip it, so the TYPELESS fix only landed once the gate was the real D3D12 need (STORAGE usage).
