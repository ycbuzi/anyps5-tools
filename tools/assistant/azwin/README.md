# azwin (Azure anyps5-win) Windows PR tests

anyps5-win: Spot B2as_v2 in Mexico Central, Windows Server 2025, AMD EPYC 7763 (2 vCPU, AVX2, no AVX-512), 8 GB RAM,
no GPU. `ssh azwin` gives PowerShell. Remote Desktop is in the Machines app.

- Toolchain in `C:\tools` (copied from the PC): mingw64 (WinLibs GCC 15.2 r7, ninja, cmake 4.3), cmake (4.4.4), Git,
  Python314. The scripts set PATH themselves, so it is not on the system PATH.
- Repo `C:\x64win\AnyPS5` (origin boykopovar, remote `fork` ycbuzi).
- `prtest.ps1 <PR>...`: fetches the PR to `refs/remotes/pr/N`, checks the head is not main and is in the merge, merges
  it into origin/main, builds relinker-only, runs ctest twice and compares with main. Reports go to
  `C:\x64win\prtests\<N>.md` (with a draft comment), one-line results to `C:\x64win\status.md`, logs to
  `C:\x64win\logs`. GPU/Vulkan tests are listed as not run.
- `prtest.ps1 -Auto`: the hourly scheduled task `x64win-prtest` (S4U, so it runs without a logon; also 10 min after
  boot; BelowNormal priority). It picks up to 3 open PRs whose head is under 2 days old and that only touch relinker,
  tools or cmake files.
- `live.ps1`: the live window, started from blu's Startup folder. Desktop shortcuts: Run PR tests now, Live PR test
  status, PR test reports.
- `jobrun.ps1`: job board wrapper (`python skills\jobboard.py send prtest N azwin`), run as a one-shot scheduled task.
- `setup.ps1`: re-runnable setup. Update: `scp *.ps1 azwin:C:/x64win/`, then
  `ssh azwin powershell -File C:\x64win\setup.ps1`.

Watchdog (`machines-watchdog.ps1`) checks that the task exists and is Ready or Running, and re-enables it if not.
After a Spot eviction, press Start in the portal.
