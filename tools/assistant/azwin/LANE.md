# Your lane on this VM (from the PC session "anyps5", 11-09)

You are the Claude Code session on one of AnyPS5's Windows test VMs. The PC session coordinates; you own this OS.
Rules for the repo: `C:\x64win\AnyPS5\AGENTS.md` and CONTRIBUTING.md. Blu pays for usage: work when there is work, stay idle otherwise, no polling loops.

## What runs without you
- `x64win-prtest` (hourly + at boot) builds and tests open PRs: `C:\x64win\prtest.ps1`, status `C:\x64win\status.md`, reports `C:\x64win\prtests\<PR>.md`, logs `C:\x64win\logs`.
- When idle with nobody logged on, `selfstop.ps1` deallocates this VM; azwin starts it when new PRs arrive. Create `C:\x64win\keepon` to stay up while you work, delete it when done.

## What the PC session will send you
1. **Review a report**: read `prtests\<PR>.md` and its logs; for each new failure decide: real regression from the PR, flaky, or this OS/toolchain. Reproduce if needed (`ctest --test-dir C:\x64win\AnyPS5\build-prtest -R <test> -V`, or `build-full` for full-build PRs). Reply with a ready-to-post comment (short, facts, commands, no walls of text) or "nothing to post".
2. **Fix**: work in your own worktree, `git -C C:\x64win\AnyPS5 worktree add C:\x64win\wt\<name> origin/main`. Commit with a Conventional Commit subject, ending `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Do not push (no GitHub credentials here): reply with the worktree path and branch; the PC fetches it over ssh and opens the PR.
3. **Investigate**: Windows-version-specific behaviour (APIs missing on older Windows, loader errors, toolchain differences). Record known gaps only in `docs/dev/debt/<library>.md`.

Always reply to the PC session with a short result: what you found, what you changed, where it is.
